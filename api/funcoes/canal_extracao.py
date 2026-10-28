# -*- coding: utf-8 -*-
"""Canal de extração — separa ONDE a pipeline grava o que ela produz.

Modo normal (central): as funções de escrita de `investigacao_utils` gravam direto no
banco (cursor real), exatamente como sempre funcionaram — este módulo fica inerte.

Modo entrega (nó, docs/no_descentralizado.md): o nó processa o .ufdr na SUA CPU e não tem
banco nenhum. Cada escritor de domínio, em vez do INSERT, EMITE um registro JSON com o
mesmo dict natural que receberia (conversas, mensagens, contatos... — nenhum id gerado
no banco atravessa o canal, ver o mapa de RETURNING: ids de conversa/mensagem/anexo só
servam dentro da própria função). Os registros viram LOTES JSONL no disco do nó, que
sobem ao central em sequência; o central REPRÓDUZ cada registro chamando a MESMA função
de gravação local (REPLAY abaixo) — mesma SQL, mesmo ON CONFLICT, mesmo dedup. O
`id_extracao` é criado no central e usado igual nos dois lados, então não há remapeamento.

A pipeline ativa o emissor via contextvar no thread do worker (`emissor_ativo`). Emissor
ausente = comportamento IDÊNTICO ao de sempre no central.
"""
import base64
import contextlib
import contextvars
import datetime
import decimal
import json
import logging
import os
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------------- emissor

_EMISSOR: contextvars.ContextVar["CanalEntrega | None"] = contextvars.ContextVar("canal_emissor", default=None)


def emissor_ativo():
    return _EMISSOR.get()


@contextlib.contextmanager
def emissor(canal: "CanalEntrega"):
    token = _EMISSOR.set(canal)
    try:
        yield canal
    finally:
        _EMISSOR.reset(token)


def ativar(canal: "CanalEntrega"):
    """Para threads de worker dedicadas (a pipeline roda em thread própria): ativa o canal
    e devolve o token para `desativar` no finally."""
    return _EMISSOR.set(canal)


def desativar(token) -> None:
    _EMISSOR.reset(token)


def emitir(tipo: str, args: tuple) -> bool:
    """Chamado no topo de cada escritor de domínio: devolve True se o canal absorveu a
    escrita (modo entrega — o chamador deve voltar sem tocar no banco)."""
    canal = _EMISSOR.get()
    if canal is None:
        return False
    canal.emitir(tipo, args)
    return True


def em_modo_entrega() -> bool:
    return _EMISSOR.get() is not None


def estado_agora(payload: dict) -> bool:
    """Estado (fase/contagens/hashes/...) do worker para o central, agora. Devolve True se
    o canal absorveu — as funções de estado da pipeline usam isso para pular o UPDATE."""
    canal = _EMISSOR.get()
    if canal is None:
        return False
    canal.estado(payload)
    return True


# ----------------------------------------------------------------------------- JSON seguro

def _enc(o):
    if isinstance(o, (datetime.datetime, datetime.date, datetime.time)):
        return {"__dt__": o.isoformat()}
    if isinstance(o, decimal.Decimal):
        return {"__dec__": str(o)}
    if isinstance(o, (bytes, bytearray)):
        return {"__b64__": base64.b64encode(bytes(o)).decode()}
    if isinstance(o, (set, frozenset)):
        return {"__set__": list(o)}
    raise TypeError(f"tipo não serializável no canal: {type(o)}")


def _dec(o):
    if isinstance(o, dict) and len(o) == 1:
        if "__dt__" in o:
            for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
                try:
                    return datetime.datetime.fromisoformat(o["__dt__"])
                except ValueError:
                    continue
            return o["__dt__"]
        if "__dec__" in o:
            return decimal.Decimal(o["__dec__"])
        if "__b64__" in o:
            return base64.b64decode(o["__b64__"])
        if "__set__" in o:
            return set(o["__set__"])
    if isinstance(o, dict):
        return {k: _dec(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_dec(v) for v in o]
    return o


# ----------------------------------------------------------------------------- canal

_LOTE_REGISTROS = 2000
# 2 MB (e não 8): o POST da entrega sai da estação por HTTPS e o router do OpenShift
# derruba upload lento/grande na WAN — um lote de 10 MB (visto num iPhone) trava a fila
# inteira, que é estritamente em ordem. Lote pequeno = retry barato e conexões curtas.
_LOTE_BYTES = 2 * 1024 * 1024
_TIPOS_AUDIO = ("audio",)
_TIPOS_VIDEO = ("video",)


class CanalEntrega:
    """Acumula os registros da pipeline em lotes JSONL no disco do nó e entrega ao central.

    A entrega é SEMPRE persistente antes do envio: o lote N+1 só pode ser enviado com o N
    confirmado (a fila de reenvio fica no disco — `lote_*.jsonl` sem ack). Estados (fase,
    progresso, batida, hashes, contagens, finalizar) não esperam lote: vão imediatamente
    por `enviar_estado`, que é o que mantém a tela do central viva e o vigia quieto.
    """

    # (modelo, transcrever_video) escolhido na tela — chega pelo POST /no/extrair, é o que
    # _obter_config_transcricao devolve no modo entrega (não há banco no nó)
    config_transcricao: tuple | None = None
    # número do inquérito (também vem pelo /no/extrair): é o destino do organize do nó
    numero_inquerito: str | None = None

    def __init__(self, id_extracao: int, pasta: Path,
                 enviar_lote, enviar_estado, lote_max: int = _LOTE_REGISTROS):
        self.id_extracao = id_extracao
        self.pasta = Path(pasta)
        self.pasta.mkdir(parents=True, exist_ok=True)
        self._enviar_lote = enviar_lote
        self._enviar_estado = enviar_estado
        self._lote_max = lote_max
        self._buf: list[dict] = []
        self._bytes = 0
        self._seq = 0                      # lote em geração
        self._ack = 0                      # próximo lote a enviar/aguardar
        self._trava = threading.Lock()
        self.audios_pendentes: list[dict] = []   # p/ transcrição local (Fase 3)
        self._cancelar = False
        # Event externo de cancelamento (troca de geração no redespacho — no_extracao.aceitar)
        self._cancelar_extra: threading.Event | None = None
        self._ult_cancel_chk = 0.0
        self._recuperar_seq()

    # -- retomada: lotes no disco sem ack voltam à fila na mesma ordem ------------------
    def _arquivo_lote(self, seq: int) -> Path:
        return self.pasta / f"lote_{seq:06d}.jsonl"

    def _recuperar_seq(self) -> None:
        existe = sorted(p.name for p in self.pasta.glob("lote_*.jsonl"))
        if existe:
            self._seq = int(existe[-1].split("_")[1].split(".")[0]) + 1

    # -- geração --------------------------------------------------------------------------
    def emitir(self, tipo: str, args: tuple) -> None:
        registro = {"t": tipo, "a": list(args)}
        if tipo == "conversa":
            self._registrar_audios(args, prefixo=args[0])
        elif tipo == "conversa_msg":
            self._registrar_audios(args, prefixo="msg")
        linha = json.dumps(registro, ensure_ascii=False, default=_enc)
        with self._trava:
            self._buf.append(linha)
            self._bytes += len(linha) + 1
            if len(self._buf) >= self._lote_max or self._bytes >= _LOTE_BYTES:
                self._fechar_lote_travado()

    def _registrar_audios(self, args: tuple, prefixo: str) -> None:
        """Guarda os anexos de áudio (e vídeo) dos chats emitidos — a transcrição LOCAL do
        nó (Fase 3) percorre esta lista no lugar do SELECT de pendentes do central. Os
        guards passam o dict `chat` na POSIÇÃO 2 dos args (conversa: prefixo, ide, chat;
        conversa_msg: ide, app, chat) e o prefixo de tabela chega por parâmetro."""
        chat = args[2] if len(args) > 2 and isinstance(args[2], dict) else None
        if chat is None:
            return
        for msg in chat.get("mensagens") or ():
            for anexo in msg.get("anexos") or ():
                tipo_c = (anexo.get("tipo_conteudo") or "").lower()
                caminho = anexo.get("caminho_zip")
                if (caminho and not caminho.startswith("IPED::")
                        and not anexo.get("transcricao")
                        and (tipo_c.startswith("audio") or tipo_c.startswith("video"))):
                    self.audios_pendentes.append({"prefixo": prefixo, "caminho_zip": caminho,
                                                  "tipo": tipo_c})

    def _fechar_lote_travado(self) -> None:
        if not self._buf:
            return
        seq = self._seq
        destino = self._arquivo_lote(seq)
        # Escrita ATÔMICA (tmp + fsync + replace): um write_text interrompido no meio
        # (queda de energia, disco cheio) deixava uma linha JSON truncada definitiva — o
        # central rejeitava o lote para sempre e a extração travava em 'processando'.
        tmp = destino.with_name(destino.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(self._buf))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, destino)
        self._seq = seq + 1
        self._buf = []
        self._bytes = 0

    def commit(self) -> None:
        """Equivalente ao conn.commit() do laço: no modo entrega, fecha o lote corrente e
        aciona o envio dos pendentes (o envio é assíncrono/backoff por conta do chamador)."""
        with self._trava:
            self._fechar_lote_travado()
        self.enviar_pendentes()

    # -- entrega --------------------------------------------------------------------------
    def enviar_pendentes(self) -> None:
        """Envia, EM ORDEM, os lotes escritos no disco ainda sem ack. O central responde o
        maior seq já processado (retomada: o que ele já tem é pulado sem reenviar). Erro de
        rede interrompe a tentativa — os lotes seguem no disco para a próxima chamada."""
        while self._ack < self._seq:
            arquivo = self._arquivo_lote(self._ack)
            if not arquivo.exists():
                return
            corpo = arquivo.read_bytes()
            try:
                novo_ack = self._enviar_lote(self._ack, corpo)
            except Exception as exc:
                from funcoes import metricas_no
                metricas_no.incr("lote_reenvios")
                logger.warning("entrega lote %s falhou (tentará de novo): %s", self._ack, exc)
                return
            if novo_ack is not None and novo_ack >= self._ack:
                # confirmado até novo_ack: apaga os entregues e avança
                for seq in range(self._ack, novo_ack + 1):
                    self._arquivo_lote(seq).unlink(missing_ok=True)
                self._ack = novo_ack + 1
            else:
                return

    # -- estado (fase/progresso/batida/hashes/contagens/finalizar) ------------------------
    def estado(self, payload: dict) -> None:
        try:
            resposta = self._enviar_estado(payload)
        except Exception as exc:
            # fire-and-forget de verdade, mas NUNCA mudo: um 'finalizar' que não chega deixa
            # o central em 'processando' até o vigia de 60 min — sem este log, invisível
            from funcoes import metricas_no
            metricas_no.incr("estado_falhos")
            logger.warning("Callback de estado %r da extração %s não chegou ao central: %s",
                           payload.get("t"), self.id_extracao, exc)
            return
        if isinstance(resposta, dict):
            self._cancelar = bool(resposta.get("cancelar"))
            self._ult_cancel_chk = time.time()

    @property
    def cancelar(self) -> bool:
        # _cancelar_extra: ordem de troca de geração (o central redespachou esta mesma
        # extração enquanto esta passada ainda vivia — ver no_extracao.aceitar)
        return self._cancelar or (self._cancelar_extra is not None
                                  and self._cancelar_extra.is_set())

    # -- encerramento -----------------------------------------------------------------------
    def finalizar(self, relatorio: dict) -> None:
        """Fecha o último lote, entrega tudo (com retry de até 10 min) e manda o relatório
        final. Se o central não confirmar tudo nesse prazo a extração é dada como erro —
        nunca 'concluída' com dados parcialmente entregues."""
        with self._trava:
            self._fechar_lote_travado()
        limite = time.time() + 600
        while self._ack < self._seq and time.time() < limite:
            antes = self._ack
            self.enviar_pendentes()
            if self._ack == antes:
                time.sleep(5)
        if self._ack < self._seq:
            raise RuntimeError(
                "O central não confirmou a entrega de todos os lotes desta extração "
                "(rede instável ou central fora do ar). Os lotes ficaram gravados na pasta "
                ".entrega do nó; rode de novo quando a conexão voltar.")
        self._enviar_estado({"t": "finalizar", **relatorio})


class ConnFalsa:
    """Substitui a conexão longa do laço no modo entrega: commit fecha lote, nada de banco."""

    def __init__(self, canal: CanalEntrega):
        self._canal = canal

    def commit(self) -> None:
        self._canal.commit()

    def rollback(self) -> None:
        pass

    def cursor(self):
        return _CursorFalso()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _CursorFalso:
    def execute(self, *a, **k):
        raise RuntimeError("cursor falso: o modo entrega não pode consultar o banco "
                           "(falta um guard de canal em alguma função da pipeline)")

    executemany = copy = executemany_block = fetchone = fetchall = nextset = execute

    def close(self):
        pass


class BatimentoPonte:
    """_Batimento do nó: fase/progresso/batida viram callbacks ao central; cancelamento
    vem da resposta do próprio callback (cache de 2 s, igual ao SELECT do central)."""

    def __init__(self, canal: CanalEntrega):
        self.canal = canal
        self._thread: threading.Thread | None = None
        self._parar = threading.Event()
        self._ultimo_pct = -1

    def iniciar_vigia(self) -> None:
        def vigia():
            while not self._parar.wait(120):
                self.canal.estado({"t": "batida"})
        self._thread = threading.Thread(target=vigia, daemon=True, name="batimento-ponte")
        self._thread.start()

    def parar_vigia(self) -> None:
        self._parar.set()

    def fase(self, nome: str) -> None:
        self._ultimo_pct = -1
        self.canal.estado({"t": "fase", "cd_fase": nome, "qt_progresso_fase": None})

    def progresso(self, feitos: float, total: float) -> None:
        if not total:
            return
        pct = min(99, max(0, int(feitos * 100 / total)))
        if pct == self._ultimo_pct:
            return
        self._ultimo_pct = pct
        self.canal.estado({"t": "progresso", "qt_progresso_fase": pct})

    def cancelar_pedido(self) -> bool:
        if time.time() - self.canal._ult_cancel_chk > 2:
            self.canal.estado({"t": "batida"})
        return self.canal.cancelar


# ----------------------------------------------------------------------------- replay (central)

def _fu():
    from funcoes import investigacao_utils
    return investigacao_utils


def _replay_args(args: list):
    return tuple(_dec(a) for a in args)


def reproduzir_lote(cur, linhas_texto: str, id_extracao: int) -> int:
    """Reproduz um lote JSONL no banco do central chamando as MESMAS funções de gravação
    da pipeline. Cada registro carrega só dados naturais + o id_extracao global."""
    IU = _fu()
    gravados = 0
    fonte_ctx, fonte_lido = None, False  # linha-pai das importações remotas (lazy, 1×/lote)
    # splitlines() é guloso: também corta em \x85 (NEL), \u2028 e \u2029 — que o json.dumps
    # (ensure_ascii=False, no emissor) deixa CRUS dentro das strings. Um só U+2028 numa
    # mensagem fendia um registro ao meio e o central rejeitava o lote inteiro
    # (422/quarentena, extração em 'erro' — visto no nó SMT, lote 94, linha 167:
    # 'Unterminated string'). O separador de recorde é só \n (o emissor junta por \n e o
    # json.dumps escapa todo \n interno), então fender por \n é o correto.
    for n_linha, linha in enumerate(linhas_texto.split("\n"), start=1):
        if not linha.strip():
            continue
        # ValueError (e não JSONDecodeError cru): o roteador devolve 422 e o nó QUARENTENA
        # o lote em vez de reenviar o mesmo veneno para sempre (deadlock de entrega)
        try:
            registro = json.loads(linha)
        except ValueError as exc:
            raise ValueError(f"linha {n_linha} do lote não é JSON válido: {exc}") from exc
        if not isinstance(registro, dict) or "t" not in registro:
            raise ValueError(f"linha {n_linha} do lote sem tipo de registro")
        tipo, args = registro["t"], _replay_args(registro["a"])
        if tipo == "conversa":
            IU._gravar_conversa(cur, *args)
        elif tipo == "conversa_msg":
            IU._gravar_conversa_msg(cur, *args)
        elif tipo == "contato_agenda":
            IU._gravar_contato_agenda(cur, *args)
        elif tipo == "ligacao":
            IU._gravar_ligacao(cur, *args)
        elif tipo == "localizacao":
            IU._gravar_localizacao(cur, *args)
        elif tipo == "notificacao":
            IU._gravar_notificacao(cur, *args)
        elif tipo == "nota":
            IU._gravar_nota(cur, *args)
        elif tipo == "pagina_visitada":
            IU._gravar_pagina_visitada(cur, *args)
        elif tipo == "aplicativo":
            IU._gravar_aplicativo(cur, *args)
        elif tipo == "dono":
            IU._gravar_dono(cur, *args)
        elif tipo == "conta_usuario":
            IU._gravar_conta_usuario(cur, *args)
        elif tipo == "email":
            IU._gravar_email(cur, *args)
        elif tipo == "conta_financeira":
            IU._gravar_conta_financeira(cur, *args)
        elif tipo == "senha":
            IU._gravar_senha(cur, *args)
        elif tipo == "autofill":
            IU._gravar_autofill(cur, *args)
        elif tipo == "rede_wifi":
            IU._gravar_rede_wifi(cur, *args)
        elif tipo == "dominio_novo":
            IU._gravar_dominio_novo(cur, *args)
        elif tipo == "arquivos":
            IU._drenar_arquivos(cur, [tuple(l) for l in args[0]])
        elif tipo == "acesso_conta":
            from funcoes.contas_nuvem_utils import _gravar_acesso_conta
            _gravar_acesso_conta(cur, *args)
        elif tipo == "atividade_conta":
            from funcoes.contas_nuvem_utils import _gravar_atividade_conta
            _gravar_atividade_conta(cur, *args)
        elif tipo == "evento_conta":
            from funcoes.contas_nuvem_utils import _gravar_evento_conta
            _gravar_evento_conta(cur, *args)
        elif tipo == "perfil_conta":
            from funcoes.contas_nuvem_utils import _gravar_perfil_conta
            _gravar_perfil_conta(cur, *args)
        elif tipo == "dispositivo_conta":
            from funcoes.contas_nuvem_utils import _gravar_dispositivo_conta
            _gravar_dispositivo_conta(cur, *args)
        elif tipo == "metadado_nuvem":
            from funcoes.nuvem_metadados import gravar_metadado
            gravar_metadado(cur, *args)
        elif tipo == "dono_ios":
            IU._derivar_dono_ios(cur, *args)
        elif tipo == "resolver_anexos":
            IU._resolver_anexos_por_file_id(cur, *args)
        elif tipo == "resolver_localizacoes":
            IU._resolver_localizacoes_compartilhadas(cur, *args)
        elif tipo == "resolver_contatos_chatstorage":
            IU._resolver_contatos_chatstorage(cur, *args)
        elif tipo == "conta_apps_ios":
            IU._gravar_conta_apps_ios(cur, *args)
        elif tipo == "corrigir_direcao":
            # adiada: precisa rodar com TUDO commitado (abre conexão própria) — o handler
            # da entrega a executa uma vez no finalize, junto com PIX/identificação.
            pass
        elif tipo == "transcricao":
            IU._aplicar_transcricao(cur, *args)
        elif tipo == "agregado_conta":
            from funcoes.contas_nuvem_utils import _gravar_agregado_conta
            _gravar_agregado_conta(cur, *args)
        elif tipo == "conexao_conta":
            from funcoes.contas_nuvem_utils import _gravar_conexao_conta
            _gravar_conexao_conta(cur, *args)
        elif tipo in _TIPOS_FONTE:
            # importação remota de RIF/SIMBA (no_importacao.py): a linha-pai é a da ponte
            # (no_fonte + id_importacao em INQ_TB02_EXTRACOES) — os registros carregam só
            # dados naturais, o replay usa as MESMAS funções do importador local
            if not fonte_lido:
                fonte_ctx, fonte_lido = _fonte_ctx(cur, id_extracao), True
            fonte = fonte_ctx
            if fonte is None:
                raise ValueError(f"ponte de importação ausente para o registro {tipo!r}")
            if tipo.startswith("rif_"):
                from funcoes.rif_utils import reproduzir_rif
                reproduzir_rif(cur, tipo, args, fonte)
            else:
                from funcoes.simba_utils import reproduzir_simba
                reproduzir_simba(cur, tipo, args, fonte)
        else:
            raise ValueError(f"registro de canal desconhecido: {tipo!r}")
        gravados += 1
    return gravados


# tipos das importações despachadas ao nó (funcoes/no_importacao.py) — a linha-pai não vem no
# registro: é a ponte (no_fonte/id_importacao da INQ_TB02_EXTRACOES do lote)
_TIPOS_FONTE = ("rif_meta", "rif_arquivos", "rif_comunicacoes", "rif_envolvidos",
                "rif_ocorrencias", "smb_meta", "smb_contas", "smb_lancamentos")


def _fonte_ctx(cur, id_extracao: int) -> dict | None:
    """{no_fonte, id_importacao, id_inquerito, id_usuario} da ponte — lido UMA vez por lote
    (o laço chama com o mesmo cur). None quando a extração não é uma importação despachada."""
    cur.execute("SELECT no_fonte, id_importacao, id_inquerito, id_usuario "
                "FROM investigacao.inq_tb02_extracoes WHERE id_extracao = %s", (id_extracao,))
    row = cur.fetchone()
    if row is None or not row[0]:
        return None
    return {"no_fonte": row[0], "id_importacao": row[1], "id_inquerito": row[2],
            "id_usuario": row[3]}


# ----------------------------------------------------------------------------- estado (central)

# colunas de estado que o nó pode atualizar na linha da extração (whitelist estrita)
_CAMPOS_DIRETOS = ("cd_fase", "qt_progresso_fase", "no_dispositivo", "in_nuvem",
                   "co_hash_md5", "co_hash_sha1", "co_hash_sha256")


def aplicar_estado(id_extracao: int, payload: dict) -> dict:
    """Aplica um callback de estado do nó na INQ_TB02_EXTRACOES. Responde {cancelar}."""
    from funcoes.db_utils import get_connection

    t = payload.get("t")
    # TODOS os UPDATEs filtram cd_status = 'processando': um callback atrasado/reenviado
    # (rede instável) nunca pode puxar uma extração já 'concluido' de volta para fase/erro
    # algum. O guard do 'erro' já existia; agora são todos.
    with get_connection() as conn, conn.cursor() as cur:
        if t in ("fase", "progresso", "batida"):
            campos = {"dt_ultima_atividade": "now()"}
            params: list = []
            for campo in ("cd_fase", "qt_progresso_fase"):
                if campo in payload:
                    campos[campo] = "%s"
                    params.append(payload[campo])
            cur.execute(f"UPDATE investigacao.inq_tb02_extracoes SET {', '.join(f'{c} = {v}' for c, v in campos.items())} "
                        f"WHERE id_extracao = %s AND cd_status = 'processando'",
                        (*params, id_extracao))
        elif t == "hashes":
            cur.execute("UPDATE investigacao.inq_tb02_extracoes SET co_hash_md5 = %s, co_hash_sha1 = %s, "
                        "co_hash_sha256 = %s, dt_ultima_atividade = now() "
                        "WHERE id_extracao = %s AND cd_status = 'processando'",
                        (payload.get("md5"), payload.get("sha1"), payload.get("sha256"), id_extracao))
        elif t == "info_dispositivo":
            cur.execute("UPDATE investigacao.inq_tb02_extracoes SET no_dispositivo = %s, no_fabricante = %s, "
                        "dt_extracao = %s, in_nuvem = %s, dt_ultima_atividade = now() "
                        "WHERE id_extracao = %s AND cd_status = 'processando'",
                        (payload.get("no_dispositivo"), payload.get("no_fabricante"),
                         payload.get("dt_extracao"), bool(payload.get("in_nuvem")), id_extracao))
        elif t == "arquivo_organizado":
            cur.execute("UPDATE investigacao.inq_tb02_extracoes SET no_arquivo = %s, "
                        "dt_ultima_atividade = now() WHERE id_extracao = %s AND cd_status = 'processando'",
                        (str(payload.get("no_arquivo"))[:1000], id_extracao))
        elif t == "contas_total":
            cur.execute("UPDATE investigacao.inq_tb02_extracoes SET qt_contas_financeiras = %s, "
                        "dt_ultima_atividade = now() WHERE id_extracao = %s AND cd_status = 'processando'",
                        (int(payload.get("total") or 0), id_extracao))
        elif t == "contagens":
            IU = _fu()
            IU._gravar_contagens_extracao(id_extracao, payload.get("contagens") or {})
        elif t == "erro":
            cur.execute("UPDATE investigacao.inq_tb02_extracoes SET cd_status = 'erro', cd_fase = NULL, "
                        "qt_progresso_fase = NULL, tx_erro = %s, dt_fim_processamento = now() "
                        "WHERE id_extracao = %s AND cd_status = 'processando'",
                        (str(payload.get("mensagem"))[:2000], id_extracao))
        conn.commit()
        cur.execute("SELECT fl_cancelar FROM investigacao.inq_tb02_extracoes WHERE id_extracao = %s",
                    (id_extracao,))
        linha = cur.fetchone()
    return {"cancelar": bool(linha and linha[0])}

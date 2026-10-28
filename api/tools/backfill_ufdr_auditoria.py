"""Backfill das extrações UFDR importadas antes da auditoria de aproveitamento.

Preenche, SEM reimportar e SEM trocar nenhum ID, o que o parser antigo descartava:
domínios inteiros (uso de app, eventos do aparelho, buscas, calendário, antenas,
transferências…), o `deleted_state` de todos os registros, os rótulos e recibos das
mensagens, os hashes dos arquivos, remetente e destinatário dos e-mails, e o `file_id`
que localiza o binário dos anexos.

Reimportar seria mais simples, mas apagaria as anotações do analista — entidades,
evidências de canônicas e detecções de padrões apontam para os IDs existentes
(tp_origem/id_origem, sem FK). Por isso aqui tudo é UPDATE em cima da linha que já
existe, ou INSERT em tabela nova.

## Como cada linha é reencontrada no XML

Duas estratégias, nessa ordem de preferência:

- **por chave**, quando existe uma: conversa de WhatsApp por `nu_whatsapp_chat_id`,
  de mensageiro por `(tp_app, nu_chat_id)`, mensagem por `nu_identificador_original`,
  arquivo do aparelho por `tx_caminho`. É imune a qualquer diferença de ordem.
- **por posição**, para os domínios sem chave natural (e-mail, contato, ligação,
  localização…): as linhas foram inseridas exatamente na ordem em que o parser as
  produziu, e a leitura do mesmo XML produz a mesma ordem. Para não confiar nisso às
  cegas, a contagem lida é conferida contra a contagem no banco ANTES de aplicar; se
  divergir, o domínio inteiro é pulado e reportado (é o que acontece numa extração que
  foi cancelada no meio, por exemplo).

Nada é sobrescrito quando já tem valor: só preenche lacuna. Idempotente — rodar de novo
não muda nada, e as tabelas dos domínios novos são refeitas do zero a cada execução.

Uso, de dentro do contêiner da API:
    python /app/tools/backfill_ufdr_auditoria.py --listar
    python /app/tools/backfill_ufdr_auditoria.py --extracao 11 --dry-run
    python /app/tools/backfill_ufdr_auditoria.py --extracao 11
    python /app/tools/backfill_ufdr_auditoria.py --todas
"""

import argparse
import sys
import time
from collections import defaultdict

sys.path.insert(0, "/app")

from funcoes import ufdr_zip                                       # noqa: E402
from funcoes.db_utils import get_connection                       # noqa: E402
from funcoes.investigacao_utils import (                          # noqa: E402
    _CONTADOR_DOMINIO,
    _DOMINIOS_NOVOS,
    _chat_sinteticas,
    _coletar_alvos_localizacao,
    _gravar_conversa,
    _gravar_conversa_msg,
    _gravar_anexo,
    _gravar_dominio_novo,
    _localizar_ufdr,
    _resolver_anexos_por_file_id,
    _resolver_localizacoes_compartilhadas,
)
from funcoes.ufdr_parser import (                                 # noqa: E402
    APP_DESCONHECIDO,
    FONTE_SMS,
    FONTE_WHATSAPP,
    indexar_indicadores,
    iterar_registros,
    ler_info_dispositivo,
)

LOTE = 2000

# Domínios sem chave natural, casados por posição (ver docstring). Cada item é
# (tabela, coluna de id, [(coluna no banco, chave no dict do parser)]).
ORDENADOS = {
    "contato": ("ctt_tb01_contatos", "id_contato", [
        ("dt_criacao", "criacao"), ("dt_modificacao", "modificacao"), ("tx_notas", "notas"),
        ("tx_endereco", "endereco"), ("no_organizacao", "organizacao"), ("no_conta", "conta"),
        ("tx_nascimento", "nascimento"), ("tx_info_adicional", "info_adicional"),
        ("co_caminho_foto", "caminho_foto"), ("cd_status_ufdr", "estado"),
    ]),
    "email": ("eml_tb01_emails", "id_email", [
        ("no_remetente", "remetente"), ("tx_destinatarios", "destinatarios"),
        ("tx_cc", "cc"), ("tx_cco", "cco"), ("no_pasta", "pasta"),
        ("cd_status_email", "status"), ("no_conta", "conta"), ("tx_rotulos", "rotulos"),
        ("tx_anexos", "anexos"), ("cd_status_ufdr", "estado"),
    ]),
    "ligacao": ("lig_tb01_ligacoes", "id_ligacao", [
        ("tp_desconexao", "desconexao"), ("no_conta", "conta"), ("cd_status_ufdr", "estado"),
    ]),
    "localizacao": ("loc_tb01_localizacoes", "id_localizacao", [
        ("tx_endereco", "endereco"), ("vl_precisao", "precisao"), ("vl_elevacao", "elevacao"),
        ("tp_localizacao", "tipo"), ("no_origem", "origem"), ("dt_fim", "fim"),
        ("no_conta", "conta"), ("cd_status_ufdr", "estado"),
    ]),
    "notificacao": ("ntf_tb01_notificacoes", "id_notificacao", [
        ("no_pacote_app", "pacote_app"), ("tx_conteudo", "conteudo"), ("no_assunto", "assunto"),
        ("tp_notificacao", "tipo"), ("cd_status", "status"), ("cd_status_ufdr", "estado"),
    ]),
    "nota": ("not_tb01_notas", "id_nota", [
        ("tx_resumo", "resumo"), ("no_pasta", "pasta"), ("no_conta", "conta"),
    ]),
    "navegacao": ("nav_tb01_paginas_visitadas", "id_pagina", [
        ("cd_status_ufdr", "estado"),
    ]),
    "aplicativo": ("app_tb01_aplicativos", "id_aplicativo", [
        ("tx_categorias", "categorias"), ("tx_permissoes", "permissoes"),
        ("tx_diretorios", "diretorios"), ("cd_status_ufdr", "estado"),
    ]),
    "wifi": ("wifi_tb01_redes", "id_rede_wifi", [
        ("vl_latitude", "latitude"), ("vl_longitude", "longitude"),
        ("tp_conexao", "tipo_conexao"), ("cd_status_ufdr", "estado"),
    ]),
    "senha": ("snh_tb01_senhas", "id_senha", [
        ("tp_senha", "tipo_senha"), ("no_grupo_acesso", "grupo_acesso"),
        ("cd_status_ufdr", "estado"),
    ]),
    "conta_usuario": ("cta_tb01_contas_usuario", "id_conta_usuario", [
        ("cd_status_ufdr", "estado"),
    ]),
    # o autofill costuma ser pulado pela checagem de contagem: o parser novo também emite as
    # entradas de <model type="FormData">, que antes não existiam, e aí a posição não bate mais.
    # Fica listado de propósito — quando o aparelho não tem FormData, o backfill vale.
    "autofill": ("afl_tb01_autofill", "id_autofill", [
        ("dt_registro", "registro"), ("dt_ultimo_uso", "ultimo_uso"),
        ("cd_status_ufdr", "estado"),
    ]),
}

# Campos das mensagens, casados pelo identificador original.
COLUNAS_MENSAGEM = [
    ("cd_status_ufdr", "estado"), ("in_encaminhada", "encaminhada"),
    ("in_apagada_remetente", "apagada_remetente"), ("in_temporaria", "temporaria"),
    ("in_editada", "editada"), ("tx_rotulos", "rotulos"),
    ("nu_ident_citado", "identificador_citado"), ("tx_destinatarios", "destinatarios"),
    ("tx_assunto", "assunto"), ("tx_motivo_exclusao", "motivo_exclusao"),
    ("nu_smsc", "smsc"), ("dt_entregue", "entregue"), ("dt_lida", "lida"),
    ("dt_reproduzida", "reproduzida"),
]

TABELAS = {
    "wpp": ("wpp_tb02_conversas", "wpp_tb04_mensagens", "wpp_tb05_anexos"),
    "sms": ("sms_tb02_conversas", "sms_tb04_mensagens", "sms_tb05_anexos"),
    "msg": ("msg_tb02_conversas", "msg_tb04_mensagens", "msg_tb05_anexos"),
}


def familia_da_fonte(fonte):
    """Roteia o Source do Chat para a família de tabelas, igual à ingestão."""
    if fonte == FONTE_WHATSAPP:
        return "wpp"
    if fonte == FONTE_SMS:
        return "sms"
    return "msg"


def listar_extracoes(cur, apenas=None):
    """Extrações UFDR concluídas (id, arquivo). A Nuvem IPED usa a mesma tabela e não entra."""
    sql = ("SELECT id_extracao, no_arquivo, no_dispositivo FROM investigacao.inq_tb02_extracoes "
           "WHERE cd_status = 'concluido' AND no_arquivo IS NOT NULL "
           "AND lower(no_arquivo) LIKE '%%.ufdr'")
    params = ()
    if apenas:
        sql += " AND id_extracao = ANY(%s)"
        params = (list(apenas),)
    cur.execute(sql + " ORDER BY id_extracao", params)
    return cur.fetchall()


def _ids_ordenados(cur, tabela, coluna_id, id_extracao):
    """IDs de um domínio, na ordem em que foram inseridos (= ordem do parser)."""
    cur.execute(
        f"SELECT {coluna_id} FROM investigacao.{tabela} "
        f"WHERE id_extracao = %s ORDER BY {coluna_id}",
        (id_extracao,),
    )
    return [linha[0] for linha in cur.fetchall()]


def _aplicar_lote(cur, tabela, coluna_id, colunas, linhas):
    """UPDATE em lote que só preenche lacuna: cada coluna usa COALESCE com o valor atual.

    O parâmetro entra direto no COALESCE, ao lado da própria coluna — é o que faz o Postgres
    inferir o tipo certo. Passar por um SELECT intermediário deixaria o parâmetro como texto e
    quebraria em coluna de data ou booleana.

    Cada linha é (valor1, valor2, …, id) — o id vai por último porque é o do WHERE."""
    if not linhas:
        return 0
    sets = ", ".join(f"{c} = COALESCE({c}, %s)" for c, _ in colunas)
    cur.executemany(
        f"UPDATE investigacao.{tabela} SET {sets} WHERE {coluna_id} = %s",
        linhas,
    )
    return len(linhas)


class Backfill:
    """Um passe pelo report.xml de uma extração, aplicando tudo que faltava."""

    def __init__(self, cur, id_extracao, dry_run=False):
        self.cur = cur
        self.id_extracao = id_extracao
        self.dry_run = dry_run
        self.resumo = defaultdict(int)
        self.pulados = []

        # posição -> id, por domínio sem chave natural
        self.ids = {}
        self.buffer = defaultdict(list)
        self.vistos = defaultdict(int)

        # chaves das conversas e das mensagens já existentes
        self.conversas = {}       # (familia, chave) -> id_conversa
        self.mensagens = {}       # (familia, id_conversa, identificador) -> id_mensagem
        self.anexos = {}          # id_mensagem -> [id_anexo...] na ordem de inserção
        self.arquivos = {}        # tx_caminho -> id_arquivo
        self.mensagens_soltas = defaultdict(list)
        # localização compartilhada ENCAMINHADA: a coordenada não está na mensagem, e sim num
        # <model type="Location"> de topo que aponta de volta para ela — os dois lados são
        # juntados em finalizar() (ver _resolver_localizacoes_compartilhadas)
        self.alvos_localizacao = {}   # id do <model> -> (família, identificador da mensagem)
        self.coords_de_mensagem = {}  # id do <model> -> (latitude, longitude)

    # ---------- preparação ----------

    def carregar_indices(self):
        cur, ext = self.cur, self.id_extracao
        for dominio, (tabela, coluna_id, _cols) in ORDENADOS.items():
            self.ids[dominio] = _ids_ordenados(cur, tabela, coluna_id, ext)

        for familia, (t_conv, t_msg, t_anexo) in TABELAS.items():
            if familia == "wpp":
                cur.execute(
                    f"SELECT nu_whatsapp_chat_id, id_conversa FROM investigacao.{t_conv} "
                    f"WHERE id_extracao = %s AND nu_whatsapp_chat_id IS NOT NULL", (ext,))
                for chave, id_conv in cur.fetchall():
                    self.conversas[(familia, chave)] = id_conv
            elif familia == "msg":
                cur.execute(
                    f"SELECT tp_app, nu_chat_id, id_conversa FROM investigacao.{t_conv} "
                    f"WHERE id_extracao = %s AND nu_chat_id IS NOT NULL", (ext,))
                for app, chave, id_conv in cur.fetchall():
                    self.conversas[(familia, (app, chave))] = id_conv
            # SMS não guarda id de chat; suas mensagens casam por identificador dentro da extração

            cur.execute(
                f"SELECT m.id_mensagem, m.id_conversa, m.nu_identificador_original "
                f"FROM investigacao.{t_msg} m "
                f"JOIN investigacao.{t_conv} c ON c.id_conversa = m.id_conversa "
                f"WHERE c.id_extracao = %s AND m.nu_identificador_original IS NOT NULL", (ext,))
            for id_msg, id_conv, ident in cur.fetchall():
                chave_conv = id_conv if familia != "sms" else None
                self.mensagens[(familia, chave_conv, ident)] = id_msg

            cur.execute(
                f"SELECT a.id_mensagem, a.id_anexo FROM investigacao.{t_anexo} a "
                f"JOIN investigacao.{t_msg} m ON m.id_mensagem = a.id_mensagem "
                f"JOIN investigacao.{t_conv} c ON c.id_conversa = m.id_conversa "
                f"WHERE c.id_extracao = %s ORDER BY a.id_anexo", (ext,))
            for id_msg, id_anexo in cur.fetchall():
                self.anexos.setdefault(id_msg, []).append(id_anexo)

        cur.execute(
            "SELECT tx_caminho, id_arquivo FROM investigacao.arq_tb01_arquivos "
            "WHERE id_extracao = %s AND tx_caminho IS NOT NULL", (ext,))
        for caminho, id_arq in cur.fetchall():
            # caminho repetido no mesmo aparelho existe (pastas diferentes com o mesmo path
            # normalizado); o primeiro vence e o resto fica de fora, sem risco de trocar linha
            self.arquivos.setdefault(caminho, id_arq)

    def limpar_dominios_novos(self):
        """Refaz as tabelas dos domínios novos do zero — é o que torna o script repetível."""
        for tabela, _cols in _DOMINIOS_NOVOS.values():
            self.cur.execute(
                f"DELETE FROM investigacao.{tabela} WHERE id_extracao = %s", (self.id_extracao,))

    # ---------- consumo do passe ----------

    def registro(self, tipo, dados):
        if tipo in _DOMINIOS_NOVOS:
            if not self.dry_run:
                _gravar_dominio_novo(self.cur, tipo, self.id_extracao, dados)
            self.resumo[tipo] += 1
            return
        if tipo == "arquivo":
            self._arquivo(dados)
            return
        if tipo == "chat":
            self._chat(*dados)
            return
        if tipo == "mensagem_solta":
            fonte, msg = dados
            self.mensagens_soltas[fonte or APP_DESCONHECIDO].append(msg)
            return
        if tipo == "localizacao" and dados.get("id_mensagem_ufdr"):
            self.coords_de_mensagem[dados["id_mensagem_ufdr"]] = (
                dados["latitude"], dados["longitude"])
        if tipo in ORDENADOS:
            pos = self.vistos[tipo]
            self.vistos[tipo] += 1
            lista = self.ids.get(tipo) or []
            if pos < len(lista):
                _tabela, _coluna, colunas = ORDENADOS[tipo]
                self.buffer[tipo].append(
                    tuple([dados.get(chave) for _c, chave in colunas] + [lista[pos]]))

    def _arquivo(self, dados):
        id_arq = self.arquivos.get(dados["caminho"])
        if id_arq is None:
            return
        self.buffer["arquivo"].append(
            (dados["hash_md5"], dados["hash_sha256"], dados["id_ufdr"], id_arq))
        if len(self.buffer["arquivo"]) >= LOTE:
            self._drenar_arquivos()

    def _drenar_arquivos(self):
        linhas = self.buffer.pop("arquivo", [])
        if not linhas or self.dry_run:
            self.resumo["arquivo"] += len(linhas)
            return
        self.cur.executemany(
            "UPDATE investigacao.arq_tb01_arquivos SET "
            "  co_hash_md5 = COALESCE(co_hash_md5, %s), "
            "  co_hash_sha256 = COALESCE(co_hash_sha256, %s), "
            "  nu_id_ufdr = COALESCE(nu_id_ufdr, %s) "
            "WHERE id_arquivo = %s",
            linhas,
        )
        self.resumo["arquivo"] += len(linhas)

    def _chat(self, fonte, chat):
        familia = familia_da_fonte(fonte)
        t_conv, t_msg, t_anexo = TABELAS[familia]
        _coletar_alvos_localizacao(chat, familia, self.alvos_localizacao)

        id_conversa = None
        if familia == "wpp" and chat.get("chat_id"):
            id_conversa = self.conversas.get((familia, chat["chat_id"]))
        elif familia == "msg":
            app = fonte or APP_DESCONHECIDO
            id_conversa = self.conversas.get((familia, (app, chat.get("chat_id"))))

        if id_conversa is not None:
            if not self.dry_run:
                self.cur.execute(
                    f"UPDATE investigacao.{t_conv} SET "
                    f"  tp_chat_ufdr = COALESCE(tp_chat_ufdr, %s), "
                    f"  tx_descricao = COALESCE(tx_descricao, %s), "
                    f"  no_conta = COALESCE(no_conta, %s), "
                    f"  tx_status_chat = COALESCE(tx_status_chat, %s), "
                    f"  cd_status_ufdr = COALESCE(cd_status_ufdr, %s) "
                    f"WHERE id_conversa = %s",
                    (chat.get("tipo_chat"), chat.get("descricao"), chat.get("conta"),
                     chat.get("status_chat"), chat.get("estado"), id_conversa))
            self.resumo["conversa"] += 1

        chave_conv = id_conversa if familia != "sms" else None
        for msg in chat["mensagens"]:
            ident = msg.get("identificador_original")
            id_msg = self.mensagens.get((familia, chave_conv, ident)) if ident else None
            if id_msg is None:
                self.resumo["mensagem_sem_par"] += 1
                continue
            self.buffer[("msg", familia)].append(
                tuple([msg.get(chave) for _c, chave in COLUNAS_MENSAGEM] + [id_msg]))
            self._anexos(familia, id_msg, msg["anexos"])
            if len(self.buffer[("msg", familia)]) >= LOTE:
                self._drenar_mensagens(familia)

    def _anexos(self, familia, id_mensagem, anexos):
        """Casa por posição os anexos já gravados e INSERE os que faltam.

        O que falta é o anexo que o parser antigo não enxergava: no Status repostado (e no
        encaminhamento de mídia em geral) o Cellebrite deixa a mensagem externa com
        `<multiModelField name="Attachments" />` vazio e põe o arquivo no InstantMessage
        EMBUTIDO. Essas mensagens ficaram no banco sem anexo nenhum e apareciam na tela como
        "(sem conteúdo)" — inclusive algumas cujo vídeo de 1 MB está inteiro na extração.
        Atualizar linha existente, que era tudo o que esta função fazia, nunca as alcançaria.

        Inserir é seguro para repetir: na segunda passada o anexo já existe e cai no ramo de
        UPDATE. Só entra anexo com algum conteúdo identificável, para não criar linha vazia."""
        ids = self.anexos.get(id_mensagem) or []
        for pos, anexo in enumerate(anexos):
            if pos < len(ids):
                self.buffer[("anexo", familia)].append(
                    (anexo["file_id"], anexo["titulo"], anexo["url"], anexo["transcricao"], ids[pos]))
            elif anexo.get("nome_arquivo") or anexo.get("caminho_zip") or anexo.get("file_id"):
                self.buffer[("anexo_novo", familia)].append((id_mensagem, anexo))

    def _drenar_mensagens(self, familia):
        _t_conv, t_msg, t_anexo = TABELAS[familia]
        linhas = self.buffer.pop(("msg", familia), [])
        if linhas and not self.dry_run:
            _aplicar_lote(self.cur, t_msg, "id_mensagem", COLUNAS_MENSAGEM, linhas)
        self.resumo["mensagem"] += len(linhas)

        novos = self.buffer.pop(("anexo_novo", familia), [])
        if novos and not self.dry_run:
            for id_mensagem, anexo in novos:
                _gravar_anexo(self.cur, t_anexo, id_mensagem, anexo)
                # o anexo recém-criado passa a contar na posição, para uma segunda passada
                # reencontrá-lo por UPDATE em vez de inserir de novo
                self.anexos.setdefault(id_mensagem, []).append(None)
        self.resumo["anexo_inserido"] += len(novos)

        anexos = self.buffer.pop(("anexo", familia), [])
        if anexos and not self.dry_run:
            self.cur.executemany(
                f"UPDATE investigacao.{t_anexo} SET "
                f"  co_file_id = COALESCE(co_file_id, %s), "
                f"  tx_titulo = COALESCE(tx_titulo, %s), "
                f"  co_url_original = COALESCE(co_url_original, %s), "
                f"  tx_transcricao = COALESCE(tx_transcricao, %s) "
                f"WHERE id_anexo = %s",
                anexos)
        self.resumo["anexo"] += len(anexos)

    # ---------- fechamento ----------

    def finalizar(self, mapa_fotos, jid_wpp, jid_sms):
        self._drenar_arquivos()
        for familia in TABELAS:
            self._drenar_mensagens(familia)

        # domínios por posição: a contagem lida tem que bater com a do banco, senão o
        # casamento posicional não vale e o domínio inteiro fica de fora
        for dominio, (tabela, coluna_id, colunas) in ORDENADOS.items():
            lidos = self.vistos.get(dominio, 0)
            no_banco = len(self.ids.get(dominio) or [])
            if no_banco == 0:
                continue
            if lidos != no_banco:
                self.pulados.append(f"{dominio}: {lidos} no arquivo x {no_banco} no banco")
                self.buffer.pop(dominio, None)
                continue
            linhas = self.buffer.pop(dominio, [])
            if linhas and not self.dry_run:
                _aplicar_lote(self.cur, tabela, coluna_id, colunas, linhas)
            self.resumo[dominio] += len(linhas)

        # mensagens sem Chat pai: são registros novos, entram em conversa sintética
        for fonte, lista in self.mensagens_soltas.items():
            if self.dry_run:
                self.resumo["mensagem_solta"] += len(lista)
                continue
            chat = _chat_sinteticas(lista)
            _coletar_alvos_localizacao(chat, familia_da_fonte(fonte), self.alvos_localizacao)
            if fonte == FONTE_WHATSAPP:
                _gravar_conversa(self.cur, "wpp", self.id_extracao, chat, mapa_fotos,
                                 jid_dono=jid_wpp)
            elif fonte == FONTE_SMS:
                _gravar_conversa(self.cur, "sms", self.id_extracao, chat, jid_dono=jid_sms)
            else:
                _gravar_conversa_msg(self.cur, self.id_extracao, fonte, chat, jid_dono=jid_wpp)
            self.resumo["mensagem_solta"] += len(lista)

        if not self.dry_run:
            self.resumo["anexo_resolvido"] = _resolver_anexos_por_file_id(
                self.cur, self.id_extracao)
            self.resumo["localizacao_de_mensagem"] = _resolver_localizacoes_compartilhadas(
                self.cur, self.id_extracao, self.alvos_localizacao, self.coords_de_mensagem)
        else:
            self.resumo["localizacao_de_mensagem"] = sum(
                1 for id_modelo in self.alvos_localizacao if id_modelo in self.coords_de_mensagem)

    def gravar_contagens(self):
        """Atualiza os contadores de INQ_TB02 dos domínios novos."""
        if self.dry_run:
            return
        sets, valores = [], []
        for tipo, chave in _CONTADOR_DOMINIO.items():
            sets.append(f"qt_{chave} = %s")
            valores.append(self.resumo.get(tipo, 0))
        valores.append(self.id_extracao)
        self.cur.execute(
            "UPDATE investigacao.inq_tb02_extracoes SET " + ", ".join(sets) +
            " WHERE id_extracao = %s", valores)


def processar(conn, id_extracao, no_arquivo, dry_run=False):
    caminho = _localizar_ufdr(no_arquivo)
    if caminho is None:
        print(f"  !! arquivo não encontrado na pasta de dados: {no_arquivo}")
        return False

    inicio = time.time()
    with conn.cursor() as cur:
        bf = Backfill(cur, id_extracao, dry_run)
        bf.carregar_indices()
        if not dry_run:
            bf.limpar_dominios_novos()

        with ufdr_zip.abrir(caminho) as zf:
            info = ler_info_dispositivo(zf=zf)
            fone = info.get("telefone")
            jid_wpp = f"{fone}@s.whatsapp.net" if fone else None
            indice, mapa_fotos, caminhos, contagem_tamanhos = indexar_indicadores(zf=zf)
            for tipo, dados in iterar_registros(indice_binarios=indice, caminhos_zip=caminhos,
                                                contagem_tamanhos=contagem_tamanhos, zf=zf):
                bf.registro(tipo, dados)
            bf.finalizar(mapa_fotos, jid_wpp, fone)
        bf.gravar_contagens()

    if dry_run:
        conn.rollback()
    else:
        conn.commit()

    itens = sorted(bf.resumo.items(), key=lambda kv: -kv[1])
    resumo = " · ".join(f"{k}={v:,}" for k, v in itens if v)
    print(f"  {'(simulado) ' if dry_run else ''}{resumo}")
    if bf.pulados:
        print("  !! domínios pulados por divergência de contagem: " + "; ".join(bf.pulados))
    print(f"  tempo: {time.time() - inicio:.0f}s")
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--listar", action="store_true", help="só lista as extrações UFDR concluídas")
    ap.add_argument("--extracao", type=int, action="append", help="id da extração (pode repetir)")
    ap.add_argument("--todas", action="store_true", help="processa todas as extrações UFDR")
    ap.add_argument("--dry-run", action="store_true", help="lê e conta, sem gravar nada")
    args = ap.parse_args()

    with get_connection() as conn:
        with conn.cursor() as cur:
            extracoes = listar_extracoes(cur, args.extracao)

        if args.listar or (not args.extracao and not args.todas):
            for id_ext, arquivo, dispositivo in extracoes:
                print(f"{id_ext:>5}  {dispositivo or '?':<24} {arquivo}")
            if not args.listar:
                print("\nescolha com --extracao ID (repetível) ou --todas")
            return

        for id_ext, arquivo, dispositivo in extracoes:
            print(f"[{id_ext}] {dispositivo or '?'} — {arquivo}")
            try:
                processar(conn, id_ext, arquivo, args.dry_run)
            except Exception as exc:  # uma extração com problema não pode parar as outras
                conn.rollback()
                print(f"  !! FALHOU: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()

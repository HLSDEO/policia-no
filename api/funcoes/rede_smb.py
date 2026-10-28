"""Cópia de arquivos a partir de pasta de rede Windows (SMB) para data/<DELEGACIA>/.

Cobre o caso em que o material (.ufdr de GBs, pasta RIF/IPED, documentos) está numa pasta
compartilhada de outra máquina/unidade e o investigador não tem como copiá-lo à mão para o
servidor: a API — que roda no servidor — conecta no compartilhamento com as credenciais
informadas na tela e puxa os arquivos para a pasta de entrega da delegacia
(`data/<DELEGACIA>/`, ver docs/organizacao_arquivos.md). Daí para frente o fluxo é idêntico
ao de quem copiou à mão: o arquivo aparece nos seletores de importacao.html.

Delegacia com nó ativo (ver docs/no_descentralizado.md): a cópia é DESVIADA para a estação —
cada arquivo sobe da origem SMB direto pelo `no_ponte.enviar_arquivo` (PUT /no/enviar em
streaming) e NADA fica no data/ do central. É o mesmo desvio do upload pelo navegador: o
usuário escolhe a delegacia na topbar, e a delegacia decide o destino.

Desenho:

- Transporte SMB via `smbprotocol` (módulo de alto nível `smbclient`, que já vem na
  própria distribuição — não há pacote separado): SMB2/3 com NTLM, em userspace. O
  container Linux não tem como montar CIFS sem privilégio, então o acesso é por
  biblioteca — ver api/requirements.txt. Sem a dependência instalada, as funções levantam
  ErroRedeSmb explicando que é preciso rebuildar a API.
- Credencial de rede NUNCA é persistida: chega no corpo do POST, vive só na memória da
  requisição/thread e nunca vai para log, banco ou mensagem de erro.
- Cópias grandes rodam em thread de segundo plano com progresso em LINHA de JOB_TB01_JOBS
  (`tp_job='copia_rede'`, ver funcoes/jobs_utils.py) — o mesmo padrão de UX dos jobs
  pesados, e pelo mesmo motivo: fechar a página, trocar de tela ou reiniciar a API não
  perde o acompanhamento (o painel "Cópias" de importacao.html lista pelo dono). É job
  global (sem inquérito — a cópia acontece ANTES de existir inquérito, com destino na raiz
  da delegacia): o escopo é `"<delegacia>:<token>"`, único por cópia, então cópias
  paralelas não se bloqueiam no guard UX100_JOB_TB01_ATIVO. Sem mudança de schema: TP_JOB
  não tem CHECK e CK14 aceita NO_ESCOPO sem ID_INQUERITO.
"""

import logging
import os
import re
import socket
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import PureWindowsPath

from funcoes import jobs_utils
from funcoes.organizacao_arquivos import PASTA_DADOS

logger = logging.getLogger(__name__)

# Bloco de streaming rede -> disco. Mesmo motivo de documentos_utils._BLOCO_UPLOAD: nunca
# carregar o arquivo inteiro (um .ufdr tem GBs) na memória do processo da API.
_BLOCO_REDE = 4 * 1024 * 1024

# Timeout de conexão SMB (segundos) — share fora do ar/VPN caída não pode pendurar a
# requisição de listagem para sempre.
_TIMEOUT_CONEXAO_S = 15

# Teto de itens por pedido de cópia: evita que uma seleção acidental de pasta com milhares
# de arquivos vire uma cópia de horas sem o usuário perceber.
_MAX_ITENS_COPIA = 200

# Caracteres que o Windows recusa em nome de arquivo — reaproveita a regra do upload pelo
# navegador (funcoes/documentos_utils._RE_CARACTERE_PROIBIDO).
_RE_PROIBIDO = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class ErroRedeSmb(RuntimeError):
    """Falha ao falar com o compartilhamento — mensagem já pronta para a tela."""


class _CancelamentoCopia(RuntimeError):
    """Sinal interno: o usuário pediu cancelamento no meio do streaming (ver _copiar_arquivo)."""


def _exigir_smbclient():
    """Import tardio do cliente SMB, com erro amigável quando a imagem ainda não foi rebuildada."""
    try:
        import smbclient  # type: ignore
    except ImportError as exc:
        raise ErroRedeSmb(
            "Acesso a pasta de rede indisponível nesta instalação: "
            "o servidor precisa do pacote SMB (rebuild da API com --build)."
        ) from exc
    return smbclient


def dividir_unc(caminho_unc: str) -> tuple[str, str, str]:
    """Quebra `\\\\servidor\\share\\resto\\...` em (servidor, share, resto).

    Aceita barra invertida ou normal. Levanta ValueError com mensagem para a tela quando o
    formato é inválido. O `resto` volta com separador `/` (formato que o smbclient espera
    depois do share) e sem `.`/`..`.
    """
    bruto = (caminho_unc or "").strip().strip("'\"")
    if not bruto:
        raise ValueError("Informe o caminho da pasta de rede (ex.: \\\\SERVIDOR\\pasta).")
    normalizado = bruto.replace("/", "\\")
    # Remove prefixo estendido \\?\UNC\ se o usuário colar do Explorer.
    if normalizado.upper().startswith("\\\\?\\UNC\\"):
        normalizado = "\\\\" + normalizado[8:]
    partes = [p for p in normalizado.split("\\") if p not in ("", ".")]
    if len(partes) < 2 or not bruto.startswith(("\\\\", "//")):
        raise ValueError(
            "Caminho inválido. Use o formato \\\\SERVIDOR\\pasta (compartilhamento Windows)."
        )
    servidor, share = partes[0], partes[1]
    resto = [p for p in partes[2:] if p != ".."]
    if ".." in partes[2:]:
        raise ValueError("Caminho com '..' não é permitido.")
    if len(bruto) > 500:
        raise ValueError("Caminho muito longo (máx. 500 caracteres).")
    return servidor, share, "/".join(resto)


def _separar_dominio(usuario: str | None, dominio: str | None) -> tuple[str | None, str | None]:
    """Normaliza as três formas de informar conta de AD: `DOMINIO\\user`, `user@dominio` ou
    campos separados. Devolve (username_final, dominio_final)."""
    usuario = (usuario or "").strip()
    dominio = (dominio or "").strip()
    if not usuario:
        return None, dominio or None
    if "\\" in usuario:
        dom, _, user = usuario.partition("\\")
        return user.strip() or None, (dom.strip() or dominio or None)
    if "@" in usuario and not dominio:
        user, _, dom = usuario.partition("@")
        return user.strip() or None, (dom.strip() or None)
    return usuario, dominio or None


def _unc_base(servidor: str, share: str, resto: str = "") -> str:
    base = f"\\\\{servidor}\\{share}"
    if resto:
        base += "\\" + resto.replace("/", "\\")
    return base


def _mascarar_unc_para_log(servidor: str, share: str) -> str:
    return f"\\\\{servidor}\\{share}"


def _aplicar_dominio(servidor: str, dominio: str | None) -> str:
    """Completa nome curto com o sufixo DNS do domínio (`ses0006` + `pf.gov.br`).

    Espelha a regra do modal (ver aplicarDominioNoCaminho em importacao.js): só quando o
    domínio tem cara de sufixo DNS (contém ponto — `PF` NetBIOS não entra aqui) e o servidor
    é nome curto sem ponto e não-IP. Domínio vazio, IP ou FQDN: devolve como está.
    """
    dominio = (dominio or "").strip().strip(".")
    if not dominio or "." not in dominio or "." in servidor:
        return servidor
    if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", servidor):
        return servidor
    if servidor.lower().endswith("." + dominio.lower()):
        return servidor
    return f"{servidor}.{dominio}"


def _checar_nome(servidor: str) -> None:
    """Confere se o nome do servidor resolve na rede do container, ANTES de tentar o SMB.

    O erro mais comum aqui é nome curto de máquina (`SES0006`) que o Windows resolve por
    NetBIOS/sufixo de DNS corporativo, mas o container Linux não: o `smbclient` falha com um
    genérico "Failed to connect to 'host:445'" que não diz se o problema é o nome ou a
    conexão. Separando a resolução, cada caso ganha a sua mensagem — e a saída para nome
    que não resolve é imediata: use o IP ou o nome completo (FQDN).
    """
    if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", servidor):
        return  # já é IP, nada a resolver
    try:
        socket.getaddrinfo(servidor, 445, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ErroRedeSmb(
            f"O servidor '{servidor}' não foi encontrado na rede do servidor do PolícIA. "
            "Nomes curtos de máquina costumam falhar aqui: tente o IP "
            "(ex.: \\\\192.168.1.10\\pasta) ou o nome completo "
            f"(ex.: \\\\{servidor}.dominio\\pasta)."
        ) from exc
    except Exception as exc:
        raise ErroRedeSmb(_traduzir_erro_conexao(exc)) from exc


def _conectar(smbclient, servidor: str, usuario: str | None, senha: str | None,
              dominio: str | None) -> None:
    """Registra a sessão SMB. Sem usuário = tenta acesso anônimo/convidado (share aberto).

    A sessão é registrada por chamada e derrubada em `finally` (ver `listar_pasta_rede` e
    `_executar_copia`): sem isso, a credencial de um usuário ficaria no cache do processo e
    valeria para o pedido seguinte de outra pessoa ao mesmo servidor. Duas cópias
    simultâneas ao MESMO servidor com credenciais DIFERENTES disputam esse registro — caso
    raro; a segunda reconecta por cima e a primeira pode ter que reautenticar.
    """
    username, dom = _separar_dominio(usuario, dominio)
    kwargs: dict = {}
    if username:
        kwargs["username"] = f"{dom}\\{username}" if dom else username
        # smbclient aceita senha vazia como string; None significa "sem senha".
        kwargs["password"] = senha if senha else ""
    _checar_nome(servidor)
    try:
        smbclient.register_session(servidor, connection_timeout=_TIMEOUT_CONEXAO_S, **kwargs)
    except Exception as exc:
        raise ErroRedeSmb(_traduzir_erro_conexao(exc)) from exc


def _desconectar(smbclient, servidor: str) -> None:
    try:
        smbclient.delete_session(servidor)
    except Exception:
        pass


def _traduzir_erro_conexao(exc: Exception) -> str:
    texto = str(exc) or type(exc).__name__
    baixo = texto.lower()
    if "logon failure" in baixo or "access denied" in baixo or "0xc000006d" in baixo:
        return "Usuário ou senha rejeitados pelo servidor de arquivos. Confira domínio, usuário e senha."
    if "bad network name" in baixo or "0xc00000cc" in baixo:
        return "Compartilhamento não encontrado. Confira o nome após o servidor (\\\\SERVIDOR\\PASTA)."
    if "failed to connect" in baixo or (
            "host" in baixo and ("unreach" in baixo or "refus" in baixo)):
        # O nome resolveu (ver _checar_nome), mas o TCP na porta 445 não abriu: firewall entre
        # o servidor do analyser e a máquina de origem, SMB desabilitado nela, ou IP errado.
        # Quem confirma em 10 segundos: na SUA máquina, `Test-NetConnection SERVIDOR -Port 445`.
        return ("Não foi possível abrir conexão SMB (porta 445) com o servidor — o nome foi "
                "localizado, mas a conexão foi recusada ou não houve resposta. Confira se o "
                "servidor do PolícIA alcança essa máquina (firewall/VPN/rede) e tente pelo IP. "
                f"Detalhe: {texto[:120]}")
    if "timeout" in baixo or "timed out" in baixo:
        return "Tempo esgotado ao contatar o servidor de arquivos. Tente de novo."
    return f"Não foi possível acessar a pasta de rede ({texto[:160]})."


def listar_pasta_rede(caminho_unc: str, usuario: str | None = None,
                      senha: str | None = None, dominio: str | None = None,
                      subpasta: str = "") -> dict:
    """Lista um nível do compartilhamento para o modal escolher o que copiar.

    `caminho_unc` é a base (`\\\\SERVIDOR\\share\\...`); `subpasta` navega a partir dela sem
    o usuário precisar redigitar o caminho. Devolve {"servidor", "share", "caminho",
    "subpasta", "itens": [{nome, caminho_rel, is_dir, tamanho_bytes, modificado}]}.
    Pastas primeiro, depois arquivos, ambos em ordem alfabética.
    """
    smbclient = _exigir_smbclient()
    servidor, share, resto = dividir_unc(caminho_unc)
    servidor = _aplicar_dominio(servidor, dominio)
    sub = (subpasta or "").replace("\\", "/").strip("/")
    if ".." in sub.split("/") or len(sub) > 400:
        raise ValueError("Subpasta inválida.")
    alvo = "/".join(p for p in (resto, sub) if p)
    _conectar(smbclient, servidor, usuario, senha, dominio)
    try:
        unc = _unc_base(servidor, share, alvo)
        try:
            nomes = smbclient.listdir(unc)
        except Exception as exc:
            raise ErroRedeSmb(_traduzir_erro_conexao(exc)) from exc
        itens = []
        for nome in sorted(nomes, key=str.casefold):
            if nome in (".", ".."):
                continue
            rel = f"{alvo}/{nome}" if alvo else nome
            unc_item = _unc_base(servidor, share, rel)
            try:
                stat = smbclient.stat(unc_item)
                is_dir = smbclient.path.isdir(unc_item)
            except Exception:
                continue
            modificado = None
            try:
                modificado = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
            except Exception:
                pass
            itens.append({
                "nome": nome,
                "caminho_rel": rel,
                "is_dir": bool(is_dir),
                "tamanho_bytes": 0 if is_dir else int(getattr(stat, "st_size", 0) or 0),
                "modificado": modificado,
            })
        itens.sort(key=lambda i: (not i["is_dir"], i["nome"].casefold()))
        logger.info("Listagem SMB %s: %d item(ns)",
                    _mascarar_unc_para_log(servidor, share), len(itens))
        return {
            "servidor": servidor,
            "share": share,
            "caminho": _unc_base(servidor, share, resto),
            "subpasta": alvo,
            "itens": itens,
        }
    finally:
        _desconectar(smbclient, servidor)


def _nome_destino_seguro(nome: str) -> str:
    """Nome de arquivo/pasta vindo da rede, sanitizado para gravar em data/.

    Mesma filosofia de documentos_utils._nome_arquivo_seguro: tira componente de caminho e
    caractere proibido no Windows. Aqui preserva o nome mesmo sem extensão conhecida, porque
    a cópia aceita qualquer tipo (o .ufdr, por exemplo, não passa no upload do navegador).
    """
    base = (nome or "").replace("\\", "/").split("/")[-1].strip().strip(".")
    base = _RE_PROIBIDO.sub("-", base).strip().strip(".")
    if not base:
        raise ValueError("Nome de arquivo inválido na origem.")
    if len(base) > 150:
        sufixo = "".join(PureWindowsPath(base).suffixes[-1:])
        raiz = base[: 150 - len(sufixo)]
        base = raiz + sufixo
    return base


def _destino_livre(pasta: "object", nome: str):
    """Caminho livre em `pasta` (sufixo ` (2)` em colisão) — espelha documentos_utils e
    organizacao_arquivos.destino para não sobrescrever prova de outra cópia."""
    from pathlib import Path as _Path
    pasta = _Path(str(pasta))  # type: ignore[arg-type]
    alvo = pasta / nome
    if not alvo.exists():
        return alvo
    sufixo_path = _Path(nome).suffix
    raiz = nome[: len(nome) - len(sufixo_path)] if sufixo_path else nome
    for i in range(2, 1000):
        candidato = pasta / f"{raiz} ({i}){sufixo_path}"
        if not candidato.exists():
            return candidato
    raise ValueError(f"{nome}: já existem arquivos demais com este nome em data/.")


# ------------------------------------------------------------------ jobs (JOB_TB01_JOBS)


@dataclass
class _EstadoCopia:
    """Acumuladores da thread de cópia — só a thread escreve; o polling lê do banco."""

    atual: int = 0
    bytes_copiados: int = 0
    concluidos: list = None
    falhas: list = None

    def __post_init__(self):
        self.concluidos = []
        self.falhas = []


@dataclass
class _CtxCopia:
    """O que as funções de cópia precisam além do smbclient: origem + linha do job."""

    servidor: str
    share: str
    id_job: int
    estado: _EstadoCopia
    # Delegacia com nó ativo -> a cópia sobe direto para a estação (no_ponte), nada no central.
    no: "dict | None" = None


def _batimento(ctx: _CtxCopia) -> bool:
    """Grava progresso/bytes e devolve True se o usuário pediu cancelamento.

    Uma ida ao banco que substitui o par _PROGRESS/_CANCELAR do desenho anterior — ver
    jobs_utils.progresso. Chamado a cada N blocos (não a cada bloco: a 100 MB/s seriam
    dezenas de UPDATEs por segundo) e ao fim de cada arquivo.
    """
    return jobs_utils.progresso(
        ctx.id_job,
        processados=ctx.estado.atual,
        metricas={"bytes_copiados": ctx.estado.bytes_copiados},
    )


def resumo_copia(job: dict) -> dict:
    """Molda a linha de JOB_TB01_JOBS no formato que o front da importação já consome.

    Mantém as chaves do desenho anterior em memória (id_job, rodando, atual, total,
    bytes_copiados, concluidos, falhas, tx_erro) — o polling do modal não muda.
    """
    parametros = job.get("tx_parametros") or {}
    resultado = job.get("tx_resultado") or {}
    metricas = job.get("tx_metricas") or {}
    return {
        "id_job": job["id_job"],
        "cd_status": job["cd_status"],
        "rodando": job["cd_status"] in ("pendente", "aguardando_vaga", "processando"),
        "delegacia": parametros.get("delegacia"),
        "origem": (
            f"\\\\{parametros.get('servidor')}\\{parametros.get('share')}"
            if parametros.get("servidor") else None
        ),
        "atual": job.get("qt_processados", 0),
        "total": job.get("qt_total", 0),
        "bytes_copiados": int(metricas.get("bytes_copiados", 0) or 0),
        "concluidos": resultado.get("concluidos", []),
        "falhas": resultado.get("falhas", []),
        "tx_erro": job.get("tx_erro"),
        "dt_inicio": str(job.get("dt_inicio")) if job.get("dt_inicio") else None,
    }


def _iterar_arquivos(smbclient, servidor: str, share: str, rel: str):
    """Gera (caminho_rel_do_arquivo, tamanho) para um item: arquivo direto ou pasta recursiva."""
    unc = _unc_base(servidor, share, rel)
    if not smbclient.path.isdir(unc):
        try:
            tamanho = int(smbclient.stat(unc).st_size or 0)
        except Exception:
            tamanho = 0
        yield rel, tamanho
        return
    pilha = [rel]
    while pilha:
        atual = pilha.pop()
        try:
            nomes = smbclient.listdir(_unc_base(servidor, share, atual))
        except Exception as exc:
            raise ErroRedeSmb(f"Não foi possível ler a pasta '{atual}': {exc}") from exc
        for nome in sorted(nomes):
            if nome in (".", ".."):
                continue
            filho = f"{atual}/{nome}"
            if smbclient.path.isdir(_unc_base(servidor, share, filho)):
                pilha.append(filho)
            else:
                try:
                    tamanho = int(smbclient.stat(_unc_base(servidor, share, filho)).st_size or 0)
                except Exception:
                    tamanho = 0
                yield filho, tamanho


def _copiar_arquivo_marcado(smbclient, ctx: _CtxCopia, pasta_destino, rel: str) -> bool:
    """Arquivo avulso cai SOLTO na raiz da delegacia (só o nome-base).

    Os seletores de todas as abas listam SÓ a raiz (`listar_arquivos_diversos` usa
    `iterdir()`, o `.ufdr` usa `glob("*.ufdr")` — ambos não-recursivos): preservar o caminho
    desde o share enterraria o arquivo onde a importação nunca olha. Devolve False se o
    cancelamento foi pedido no meio.
    """
    try:
        nome = _nome_destino_seguro(rel.split("/")[-1])
    except ValueError as exc:
        ctx.estado.falhas.append(f"{rel}: {exc}")
        return True
    if ctx.no is not None:
        # No nó quem decide o nome final (sufixo "(2)") é a própria estação no PUT —
        # checar colisão no disco do central seria só um falso "já existe".
        destino = pasta_destino / nome
    else:
        destino = _destino_livre(pasta_destino, nome)
    nome_final = _copiar_arquivo(smbclient, ctx, rel, destino)
    if nome_final is None:
        return False
    if nome_final not in ctx.estado.concluidos:
        ctx.estado.concluidos.append(nome_final)
    return True


def _copiar_pasta_marcada(smbclient, ctx: _CtxCopia, pasta_destino, rel: str) -> bool:
    """Pasta marcada vira UMA pasta na RAIZ da delegacia, com a estrutura INTERNA preservada.

    O topo é a pasta marcada (não o caminho desde o share): é o que as abas de RIF/IPED
    listam — pasta-caso direto em `data/<DELEGACIA>/`. Devolve False se cancelado.
    """
    try:
        topo_nome = _nome_destino_seguro(rel.rstrip("/").split("/")[-1])
    except ValueError as exc:
        ctx.estado.falhas.append(f"{rel}: {exc}")
        return True
    try:
        topo_nome = _nome_destino_seguro(rel.rstrip("/").split("/")[-1])
    except ValueError as exc:
        ctx.estado.falhas.append(f"{rel}: {exc}")
        return True
    topo = pasta_destino / topo_nome
    if ctx.no is None:
        if topo.exists() and not topo.is_dir():
            topo = _destino_livre(pasta_destino, topo_nome)
        topo.mkdir(parents=True, exist_ok=True)
    if f"{topo.name}/" not in ctx.estado.concluidos:
        ctx.estado.concluidos.append(f"{topo.name}/")
    prefixo = rel.rstrip("/")
    for caminho_rel, _tamanho in _iterar_arquivos(smbclient, ctx.servidor, ctx.share, rel):
        resto = caminho_rel[len(prefixo):].strip("/")
        if not resto:
            continue
        try:
            partes = [_nome_destino_seguro(p) for p in resto.split("/")]
        except ValueError as exc:
            ctx.estado.falhas.append(f"{caminho_rel}: {exc}")
            continue
        destino = topo.joinpath(*partes)
        if ctx.no is None:
            destino.parent.mkdir(parents=True, exist_ok=True)
        if _copiar_arquivo(smbclient, ctx, caminho_rel, destino) is None:
            return False
    return True


def _executar_copia(id_job: int, usuario_rede: str | None, senha_rede: str | None,
                    dominio_rede: str | None) -> None:
    from pathlib import Path

    smbclient = _exigir_smbclient()
    linha = jobs_utils.obter(id_job)
    if linha is None:
        logger.error("Cópia de rede %s sem linha de job — abortando", id_job)
        return
    parametros = linha.get("tx_parametros") or {}
    ctx = _CtxCopia(servidor=parametros.get("servidor", ""), share=parametros.get("share", ""),
                    id_job=id_job, estado=_EstadoCopia())
    itens = parametros.get("itens", [])
    delegacia = parametros.get("delegacia")
    jobs_utils.marcar_processando(id_job)
    try:
        _conectar(smbclient, ctx.servidor, usuario_rede, senha_rede, dominio_rede)
    except ErroRedeSmb as exc:
        jobs_utils.falhar(id_job, str(exc))
        _desconectar(smbclient, ctx.servidor)
        return
    cancelado = False
    try:
        # Delegacia com nó ativo: o destino é a estação, não o data/ do central (mesmo
        # desvio do upload pelo navegador — docs/no_descentralizado.md).
        from funcoes import no_utils
        no = no_utils.servidor_ativo_para(delegacia)
        pasta_destino = Path(str(PASTA_DADOS)) / delegacia
        if no is None:
            pasta_destino.mkdir(parents=True, exist_ok=True)
        else:
            logger.info("Cópia SMB %s desviada para o nó %s (delegacia %s)",
                        id_job, no["no_servidor"], delegacia)
        ctx.no = no
        for rel in itens:
            ctx.estado.atual += 1
            # Arquivo dentro de uma pasta também marcada vem pela pasta — pula o avulso
            # para não copiar (e contar) duas vezes.
            if any(outro != rel and rel.startswith(outro.rstrip("/") + "/") for outro in itens):
                continue
            try:
                item_eh_pasta = smbclient.path.isdir(_unc_base(ctx.servidor, ctx.share, rel))
            except Exception:
                item_eh_pasta = False
            try:
                if item_eh_pasta:
                    ok = _copiar_pasta_marcada(smbclient, ctx, pasta_destino, rel)
                else:
                    ok = _copiar_arquivo_marcado(smbclient, ctx, pasta_destino, rel)
                if not ok:
                    cancelado = True
                    break
            except ErroRedeSmb as exc:
                ctx.estado.falhas.append(f"{rel}: {exc}")
            except Exception as exc:  # noqa: BLE001 — cópia não pode matar a thread sem resumo
                logger.exception("Falha ao copiar %s", rel)
                ctx.estado.falhas.append(f"{rel}: {exc}")
            # Batimento por item: o reaper (liberar_jobs_travados) usa DT_ULTIMA_ATIVIDADE.
            if _batimento(ctx):
                cancelado = True
                break
        jobs_utils.progresso(id_job, processados=ctx.estado.atual,
                             erros=len(ctx.estado.falhas),
                             metricas={"bytes_copiados": ctx.estado.bytes_copiados})
        if cancelado:
            jobs_utils.marcar_cancelado(id_job)
        elif ctx.estado.concluidos:
            jobs_utils.concluir(id_job, resultado={"concluidos": ctx.estado.concluidos,
                                                   "falhas": ctx.estado.falhas})
        else:
            jobs_utils.falhar(id_job, "; ".join(ctx.estado.falhas)[:2000] or "Nada foi copiado.")
    except Exception as exc:  # noqa: BLE001
        logger.exception("Falha geral na cópia SMB %s", id_job)
        try:
            jobs_utils.falhar(id_job, str(exc)[:500])
        except Exception:
            pass
    finally:
        _desconectar(smbclient, ctx.servidor)
        # Credenciais vivem só nos argumentos da thread — apaga as referências assim que acaba.
        try:
            del usuario_rede, senha_rede, dominio_rede
        except Exception:
            pass


# A cada quantos blocos o batimento vai ao banco (ver _batimento): 8 blocos de 4 MB =
# ~32 MB. A cada bloco seria um UPDATE por 4 MB — dezenas por segundo num link rápido.
_BLOCOS_POR_BATIMENTO = 8


def _copiar_arquivo(smbclient, ctx: _CtxCopia, caminho_rel: str, destino) -> "str | None":
    """Copia UM arquivo da rede para `destino` em blocos, via `.part` + rename atômico.

    Com nó ativo o `destino` local vira só o CAMINHO RELATIVO espelhado na estação: o corpo
    sai do SMB e entra no `no_ponte.enviar_arquivo` (PUT /no/enviar) em streaming, sem nunca
    tocar o disco do central. O nó pode renomear (colisão -> " (2)"); o nome final é o que
    volta na resposta.

    Devolve o NOME FINAL gravado, ou None se o cancelamento foi pedido no meio (o `.part`
    local / o `.parcial` do nó é apagado).
    """
    from pathlib import Path
    destino = Path(str(destino))
    unc = _unc_base(ctx.servidor, ctx.share, caminho_rel)

    if ctx.no is not None:
        from funcoes import no_ponte
        relativo = os.path.relpath(str(destino), str(PASTA_DADOS)).replace(os.sep, "/")
        try:
            tamanho = int(smbclient.stat(unc).st_size or 0)
        except Exception:
            tamanho = 0
        if tamanho == 0:
            raise ErroRedeSmb("o arquivo na origem está vazio — o servidor de origem "
                              "descentralizado não aceita arquivo vazio")
        cancelado = {"sim": False}

        def _gerar():
            blocos = 0
            with smbclient.open_file(unc, mode="rb") as remoto:
                while True:
                    bloco = remoto.read(_BLOCO_REDE)
                    if not bloco:
                        break
                    ctx.estado.bytes_copiados += len(bloco)
                    yield bloco
                    blocos += 1
                    if blocos % _BLOCOS_POR_BATIMENTO == 0 and _batimento(ctx):
                        cancelado["sim"] = True
                        raise _CancelamentoCopia()

        try:
            salvo = no_ponte.enviar_arquivo(ctx.no, _gerar(), relativo)
        except Exception as exc:  # noqa: BLE001 — cancel vira ProtocolError/RequestException no meio
            if cancelado["sim"]:
                return None
            if isinstance(exc, no_ponte.NoIndisponivel):
                raise ErroRedeSmb(str(exc)) from exc
            raise
        if _batimento(ctx):
            return None
        return str(salvo.get("nome") or destino.name)

    if destino.exists() and destino.is_file():
        return destino.name  # já copiado (repetição dentro da mesma pasta)
    tmp = destino.parent / (destino.name + ".part")
    blocos = 0
    cancelado_local = False
    try:
        with smbclient.open_file(unc, mode="rb") as remoto, open(tmp, "wb") as local:
            while True:
                bloco = remoto.read(_BLOCO_REDE)
                if not bloco:
                    break
                local.write(bloco)
                ctx.estado.bytes_copiados += len(bloco)
                blocos += 1
                if blocos % _BLOCOS_POR_BATIMENTO == 0 and _batimento(ctx):
                    cancelado_local = True
                    break
        if not cancelado_local and _batimento(ctx):
            cancelado_local = True
        if cancelado_local:
            tmp.unlink(missing_ok=True)
            return None
        # Arquivo vazio na origem: mantém o arquivo vazio (é o que a origem tem).
        os.replace(tmp, destino)
        return destino.name
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def iniciar_copia(*, caminho_unc: str, itens: list[str], delegacia: str, id_usuario: int,
                  usuario_rede: str | None = None, senha_rede: str | None = None,
                  dominio_rede: str | None = None) -> int:
    """Valida o pedido, abre a linha do job e dispara a thread de cópia. Devolve o id_job."""
    _exigir_smbclient()
    servidor, share, resto = dividir_unc(caminho_unc)
    servidor = _aplicar_dominio(servidor, dominio_rede)
    if not itens or len(itens) > _MAX_ITENS_COPIA:
        raise ValueError(f"Selecione de 1 a {_MAX_ITENS_COPIA} itens para copiar.")
    # Normaliza cada item para `resto/item` (relativo ao share) e barra colagem maliciosa.
    normais: list[str] = []
    for item in itens:
        item = (item or "").replace("\\", "/").strip().strip("/")
        if not item or ".." in item.split("/") or len(item) > 500:
            raise ValueError(f"Item inválido para copiar: {item!r}.")
        # O modal lista a partir da base; se ele mandou caminho já relativo à base, prefixa.
        if resto and not (item == resto or item.startswith(resto + "/")):
            item = f"{resto}/{item}"
        normais.append(item)
    # Remove duplicados preservando ordem (selecionar pasta + arquivo dentro dela copia 1x).
    vistos: set[str] = set()
    unicos = [i for i in normais if not (i in vistos or vistos.add(i))]
    # Escopo único por cópia (delegacia + token): cópias paralelas não se bloqueiam no
    # guard UX100_JOB_TB01_ATIVO, que é por (tp_job, escopo) ativo. SEM senha, nunca.
    try:
        id_job = jobs_utils.criar(
            jobs_utils.TP_COPIA_REDE, id_usuario,
            escopo=f"{delegacia}:{uuid.uuid4().hex[:8]}",
            parametros={"servidor": servidor, "share": share,
                        "delegacia": delegacia, "itens": unicos},
            pesado=False,  # I/O de rede/disco, não CPU: não ocupa vaga de jobs_pesados
        )
    except jobs_utils.JobOcupado as exc:
        raise ValueError(str(exc)) from exc
    jobs_utils.progresso(id_job, total=len(unicos), processados=0)
    # Log SEM credencial: só origem e destino.
    logger.info("Cópia SMB %s iniciada por usuario=%s: %s (%d item(ns)) -> data/%s",
                id_job, id_usuario, _mascarar_unc_para_log(servidor, share),
                len(unicos), delegacia)
    t = threading.Thread(target=_executar_copia,
                         args=(id_job, usuario_rede, senha_rede, dominio_rede),
                         name=f"copia-rede-{id_job}", daemon=True)
    t.start()
    # Apaga referências locais assim que despacha (a thread tem as suas).
    try:
        del usuario_rede, senha_rede, dominio_rede
    except Exception:
        pass
    return id_job

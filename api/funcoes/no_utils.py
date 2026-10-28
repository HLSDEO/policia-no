"""Nós descentralizados: registro, tokens, batimento e o papel do processo.

O projeto roda em dois PAPÉIS com o mesmo código (ver docs/no_descentralizado.md):

- `PAPEL=central` (o default): web + API + Postgres, como sempre. É aqui que vive o registro
  dos nós (`INVESTIGACAO.NO_TB01_SERVIDORES`);
- `PAPEL=no`: container enxuto de uma delegacia — sem Postgres, sem web. Sobe só as rotas
  internas `/no/*` (rotas/no_servico.py), que o CENTRAL chama para hashear arquivo, parsear
  documento, listar e ler bytes da sua pasta data/, e mantém um heartbeat ligado batendo em
  POST /api/no/ping.

Quem autentica o QUÊ:

- central → nó: header `Authorization: Bearer <token>`; o nó compara com NO_TOKEN do seu .env
  (comparação constante-tempo). O nó não tem banco, então o segredo dele é o próprio .env;
- nó → central: o MESMO token, conferido contra TX_TOKEN da tabela em comparação
  constante-tempo. O token vive em claro nos dois lados de propósito: o central precisa
  APRESENTAR o token ao chamar o nó, e revogar é "trocar token" na tela — o nó para de
  autenticar nos dois sentidos até ser reconfigurado.

O roteamento é sempre delegacia → nó (UX110 no vínculo NO_TB02 garante um por unidade; um nó
pode atender várias delegacias): `servidor_ativo_para()` é a única pergunta que o resto do código
faz — "esta delegacia tem nó ativo?". Sem nó (ou com o nó inativo, ou com o "Usa o Nó" da
delegacia desligado), TODO o fluxo é exatamente o de antes: arquivo no data/ do central, execução
no central. É isso que deixa os nós entrarem um a um, sem tocar nas delegacias que ficaram no
central.
"""

from __future__ import annotations

import hmac
import logging
import os
import secrets
import time

from fastapi import Header, HTTPException, Request

from funcoes.db_utils import get_connection

logger = logging.getLogger(__name__)

# Um nó sem batimento há mais de este tempo é tratado como offline pela tela. O heartbeat do
# nó vem a cada NO_HEARTBEAT_SEGUNDOS (default 60 s), então 3 minutos = 2 batimentos perdidos.
MINUTOS_NO_ONLINE = 3

# Cache da pergunta "este NO_ARQUIVO é de algum nó?" (servidor_por_arquivo): dispara a cada anexo
# lido de uma extração remota, e uma galeria de 100 miniaturas não pode custar 100 SELECTs.
# 60 s é invisível para o admin (trocar o nó da delegacia propaga no próximo minuto).
_CACHE_NO_ARQUIVO: dict = {}
_CACHE_NO_ARQUIVO_TTL = 60.0

# tx_token acompanha as consultas: é o que a ponte (funcoes/no_ponte.py) apresenta quando o
# CENTRAL chama o nó — sem ele o central teria o registro mas não a credencial. Quem devolve
# essas linhas para o navegador (rotas/servidores.py) SEMPRE remove tx_token antes.
# As delegacias atendidas NÃO estão aqui: vivem em NO_TB02_SERVIDOR_DELEGACIAS (um nó atende N)
# e são anexadas por _anexar_delegacias em quem precisa exibi-las.
# dt_tunel_ativo: a última batida do CANO WebSocket do túnel NAT (serviço tunel: connect, ping de
# 30 s, NULL no disconnect). É TIMESTAMP e não booleano de propósito — se o serviço túnel morrer
# sem se despedir, a coluna congela e deixa de valer sozinha; um booleano mentiria para sempre.
_COLUNAS = """s.id_servidor, s.no_servidor, s.tx_url_base, s.tx_token, s.tx_versao,
              s.dt_ultimo_heartbeat, s.dt_tunel_ativo, s.dt_criacao, s.in_ativa,
              s.tx_midia_url, s.in_midia_direta"""

# Um cano sem PING há mais que isto não é mais um cano (o nó bate a cada 30 s; 3 batimentos de
# folga cobrem um relay ocupado sem segurar um cano morto).
SEGUNDOS_TUNEL_FRESCO = 120


def papel() -> str:
    """Papel deste processo: 'central' (default) ou 'no'. Lido do .env via PAPEL."""
    return (os.getenv("PAPEL") or "central").strip().lower()


def eh_no() -> bool:
    return papel() == "no"


def gerar_token() -> str:
    """Token novo de 256 bits, em claro — quem chama mostra UMA vez e descarta."""
    return secrets.token_urlsafe(32)


def _limite_online_ts() -> float:
    import time
    return time.time() - MINUTOS_NO_ONLINE * 60


def _com_online(d: dict) -> dict:
    hb = d.get("dt_ultimo_heartbeat")
    d["online"] = bool(hb) and hb.timestamp() > _limite_online_ts()
    tunel = d.get("dt_tunel_ativo")
    d["tunel_ativo"] = bool(tunel) and tunel.timestamp() > time.time() - SEGUNDOS_TUNEL_FRESCO
    return d


def _linha(cur) -> dict | None:
    row = cur.fetchone()
    if row is None:
        return None
    return _com_online(dict(zip([c.name for c in cur.description], row)))


# ------------------------------------------------------------------ consulta

def _anexar_delegacias(nos: list[dict]) -> list[dict]:
    """Anexa em cada nó a lista de delegacias que ele atende (um nó atende N — ver
    NO_TB02_SERVIDOR_DELEGACIAS e a tela Administração → Servidores). O roteamento não usa
    isto: servidor_ativo_para resolve a delegacia direto no SQL."""
    if not nos:
        return nos
    ids = [d["id_servidor"] for d in nos]
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT v.id_servidor, d.id_delegacia, d.no_delegacia, d.uf "
                "FROM investigacao.no_tb02_servidor_delegacias v "
                "JOIN seguranca.delegacias d ON d.id_delegacia = v.id_delegacia "
                "WHERE v.id_servidor = ANY(%s) ORDER BY d.no_delegacia",
                (ids,))
            por_no: dict[int, list] = {}
            for id_no, id_delegacia, nome, uf in cur.fetchall():
                por_no.setdefault(id_no, []).append(
                    {"id_delegacia": id_delegacia, "no_delegacia": nome, "uf": uf})
    for d in nos:
        d["delegacias"] = por_no.get(d["id_servidor"], [])
        d["no_delegacias"] = ", ".join(x["no_delegacia"] for x in d["delegacias"])
    return nos


def listar() -> list[dict]:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {_COLUNAS} FROM investigacao.no_tb01_servidores s "
                "ORDER BY s.no_servidor")
            colunas = [c.name for c in cur.description]
            nos = [_com_online(dict(zip(colunas, row))) for row in cur.fetchall()]
    return _anexar_delegacias(nos)


def obter(id_servidor: int) -> dict | None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {_COLUNAS} FROM investigacao.no_tb01_servidores s "
                "WHERE s.id_servidor = %s",
                (id_servidor,))
            d = _linha(cur)
    return _anexar_delegacias([d])[0] if d else None


def servidor_ativo_para(nome_delegacia: str | None) -> dict | None:
    """O nó que serve a delegacia, ou None. Aceita o nome de seguranca.delegacias ("FICCO/ES")
    OU a pasta sanitizada de data/ ("FICCO-ES") — quem chama ora tem um, ora outro, e os dois
    caminhos têm que encontrar o mesmo nó.

    None desliga o caminho remoto de ponta a ponta — a chamada é barata (delegacias ativas são
    dezenas) e é a pergunta que extrair/upload/listagem fazem para decidir central × nó.

    A delegacia também manda: IN_NO_ATIVA=false na tela Administração → Delegacias faz o central
    fingir que o nó não existe (extração segue 100 % no principal) sem descadastrar o nó. O que
    já nasceu no nó continua sendo lido por ele — servidor_por_arquivo não passa por aqui."""
    if not nome_delegacia:
        return None
    # import tardio: o sanitizador mora em investigacao_utils — laço só dentro de função
    from funcoes.investigacao_utils import _sanitizar_delegacia

    pasta = _sanitizar_delegacia(nome_delegacia)
    if not pasta:
        return None
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {_COLUNAS}, d.no_delegacia FROM investigacao.no_tb01_servidores s "
                "JOIN investigacao.no_tb02_servidor_delegacias v ON v.id_servidor = s.id_servidor "
                "JOIN seguranca.delegacias d ON d.id_delegacia = v.id_delegacia "
                "WHERE s.in_ativa AND d.in_no_ativa")
            colunas = [c.name for c in cur.description]
            for row in cur.fetchall():
                d = dict(zip(colunas, row))
                if _sanitizar_delegacia(d.get("no_delegacia") or "") == pasta:
                    return _com_online(d)
    return None


def listagem_remota(nome_delegacia: str | None, subpasta: str | None, tipo: str) -> list | None:
    """Arquivos da pasta de UMA delegacia quando ela tem nó: a pergunta que o seletor da tela
    faz. None = sem nó (a lista local do central vale); lista vazia = o nó respondeu e está
    vazia mesmo. Erro de rede devolve None para a rota cair na listagem local em vez de um 500."""
    no = servidor_ativo_para(nome_delegacia)
    if no is None:
        return None
    from funcoes import no_ponte

    try:
        return no_ponte.listar(no, subpasta, tipo)
    except Exception:
        logger.warning("Nó %s inacessível para listar %s (%s); usando o que o central tem",
                       no.get("no_servidor"), subpasta or "/", tipo)
        return None


def servidor_da_extracao(id_extracao: int) -> dict | None:
    """O nó dono do arquivo de uma extração (snapshot em INQ_TB02_EXTRACOES.ID_SERVIDOR)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT {_COLUNAS} FROM investigacao.no_tb01_servidores s
                JOIN investigacao.inq_tb02_extracoes e ON e.id_servidor = s.id_servidor
                WHERE e.id_extracao = %s
                """,
                (id_extracao,))
            return _linha(cur)


# ------------------------------------------------------------------ escrita (admin)

def _avisar_http(url_base: str) -> None:
    """O MESMO token autentica os dois sentidos (central→nó e nó→central). Em http:// ele
    viaja em claro na WAN: quem escuta decide o que as duas pontas veem. Não bloqueamos
    (rede interna dedicada é uso legítimo), mas o registro fica no log e o admin precisa
    saber que viu — o conserto é TLS no proxy na frente das duas pontas."""
    if url_base.startswith("http://"):
        logger.warning("Nó registrado com URL http:// (%s): o token de nó viaja em claro "
                       "entre central e estação. Em rede não confiável, termine TLS num "
                       "proxy (TX_URL_BASE https://) — ver docs/no_descentralizado.md.",
                       url_base)


def _trocar_vinculos(cur, id_servidor: int, ids_delegacia: list[int]) -> None:
    """Regrava as delegacias que o nó atende (UX110: nenhuma pode já estar em outro nó).
    Roda na MESMA transação de quem chama — ou muda tudo, ou nada."""
    import psycopg

    cur.execute("DELETE FROM investigacao.no_tb02_servidor_delegacias WHERE id_servidor = %s",
                (id_servidor,))
    try:
        for id_delegacia in ids_delegacia:
            cur.execute(
                "INSERT INTO investigacao.no_tb02_servidor_delegacias (id_servidor, id_delegacia) "
                "VALUES (%s, %s)",
                (id_servidor, id_delegacia))
    except psycopg.errors.UniqueViolation as exc:
        raise ValueError("Uma das delegacias já é atendida por outro nó — desfaça o vínculo "
                         "dele antes.") from exc
    except psycopg.errors.ForeignKeyViolation as exc:
        raise ValueError("Delegacia não encontrada.") from exc


def criar(no_servidor: str, ids_delegacia: list[int], url_base: str) -> tuple[int, str]:
    """Cadastra o nó e as delegacias que ele vai atender; devolve (id_servidor, token_em_claro)
    — o token só existe neste retorno; o instalar-host do nó cola no .env dele."""
    import psycopg

    nomes = (no_servidor or "").strip().upper()
    url_base = (url_base or "").strip().rstrip("/")
    ids = list(dict.fromkeys(int(i) for i in (ids_delegacia or [])))
    if not nomes:
        raise ValueError("Nome do servidor é obrigatório.")
    if not ids:
        raise ValueError("Escolha pelo menos uma delegacia para o nó atender.")
    if not url_base.startswith(("http://", "https://")):
        raise ValueError("URL base deve começar com http:// ou https://.")
    _avisar_http(url_base)
    token = gerar_token()
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    """
                    INSERT INTO investigacao.no_tb01_servidores
                        (no_servidor, tx_url_base, tx_token)
                    VALUES (%s, %s, %s)
                    RETURNING id_servidor
                    """,
                    (nomes, url_base, token))
                id_servidor = cur.fetchone()[0]
            except psycopg.errors.UniqueViolation as exc:
                raise ValueError("Já existe servidor com esse nome.") from exc
            _trocar_vinculos(cur, id_servidor, ids)
        conn.commit()
    return id_servidor, token


def atualizar(id_servidor: int, *, url_base: str | None = None,
              in_ativa: bool | None = None,
              ids_delegacia: list[int] | None = None,
              midia_url: str | None = None,
              midia_ativa: bool | None = None) -> None:
    sets, params = [], []
    if url_base is not None:
        url_base = url_base.strip().rstrip("/")
        if not url_base.startswith(("http://", "https://")):
            raise ValueError("URL base deve começar com http:// ou https://.")
        _avisar_http(url_base)
        sets.append("tx_url_base = %s")
        params.append(url_base)
    if in_ativa is not None:
        sets.append("in_ativa = %s")
        params.append(in_ativa)
    if midia_url is not None:
        midia_url = midia_url.strip().rstrip("/") or None
        if midia_url:
            if not midia_url.startswith("https://"):
                # http aqui é o convite ao erro: a página do central é https e o navegador
                # recusa mídia http de uma página https (mixed content) — o admin descobriria
                # pela tela quebrada, melhor pela mensagem
                raise ValueError("URL de mídia deve começar com https:// (o navegador não "
                                 "baixa http de uma página https).")
            sets.append("tx_midia_url = %s")
            params.append(midia_url)
        else:
            sets.append("tx_midia_url = NULL")
    if midia_ativa is not None:
        sets.append("in_midia_direta = %s")
        params.append(midia_ativa)
    if ids_delegacia is not None:
        ids = list(dict.fromkeys(int(i) for i in ids_delegacia))
        if not ids:
            raise ValueError("O nó precisa continuar atendendo pelo menos uma delegacia — "
                             "para tirá-lo de todas, inative-o ou exclua-o.")
    if midia_ativa:
        # ligar sem URL é deixar o navegador sondando o nada: exija o endereço do no-web
        url_efetiva = midia_url if midia_url is not None else None
        if url_efetiva is None:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT tx_midia_url FROM investigacao.no_tb01_servidores "
                                "WHERE id_servidor = %s", (id_servidor,))
                    linha = cur.fetchone()
                    url_efetiva = linha[0] if linha else None
        if not url_efetiva:
            raise ValueError("Informe a URL de mídia (ex.: "
                             "https://no-smt.policia-es.pf.gov.br:8443) antes de ativar — "
                             "o certificado e o DNS do nó são o checklist do docs/"
                             "no_midia_direta.md.")
    if not sets and ids_delegacia is None:
        return
    with get_connection() as conn:
        with conn.cursor() as cur:
            if sets:
                params.append(id_servidor)
                cur.execute(
                    f"UPDATE investigacao.no_tb01_servidores SET {', '.join(sets)} "
                    "WHERE id_servidor = %s",
                    tuple(params))
                if cur.rowcount == 0:
                    raise ValueError("Servidor não encontrado.")
            if ids_delegacia is not None:
                cur.execute(
                    "SELECT 1 FROM investigacao.no_tb01_servidores WHERE id_servidor = %s",
                    (id_servidor,))
                if cur.fetchone() is None:
                    raise ValueError("Servidor não encontrado.")
                _trocar_vinculos(cur, id_servidor, ids)
        conn.commit()


def trocar_token(id_servidor: int) -> str:
    """Gera token novo e devolve em claro (mostra 1×). O nó precisa ser reconfigurado com ele —
    é assim que se revoga um nó comprometido: troca o token aqui e o nó para de autenticar."""
    token = gerar_token()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE investigacao.no_tb01_servidores SET tx_token = %s "
                "WHERE id_servidor = %s",
                (token, id_servidor))
            if cur.rowcount == 0:
                raise ValueError("Servidor não encontrado.")
        conn.commit()
    return token


def excluir(id_servidor: int) -> None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            # o histórico (extrações/jobs) guarda o id sem FK justamente para o DELETE passar —
            # as linhas antigas continuam apontando para um servidor que não existe mais, e os
            # fluxos remotos tratam isso como "arquivo indisponível"
            cur.execute("DELETE FROM investigacao.no_tb01_servidores WHERE id_servidor = %s",
                        (id_servidor,))
            if cur.rowcount == 0:
                raise ValueError("Servidor não encontrado.")
        conn.commit()


# ------------------------------------------------------------------ batimento

def bater(id_servidor: int, versao: str | None) -> None:
    """DT_ULTIMO_HEARTBEAT + versão reportada. Chamado pelo ping e por toda chamada do nó; a
    tela mostra online/offline e a versão a partir daqui."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE investigacao.no_tb01_servidores "
                "SET dt_ultimo_heartbeat = now(), tx_versao = coalesce(%s, tx_versao) "
                "WHERE id_servidor = %s",
                ((versao or "")[:50] or None, id_servidor))
        conn.commit()


# ------------------------------------------------------------------ autenticação nó→central

# Toda chamada autenticada "vale como batimento" — mas uma galeria de 100 miniaturas são
# 100 UPDATEs no heartbeat. Amortiza por processo: no máximo um UPDATE por nó a cada
# 30 s (o limiar de online é 3 min — invisível). _BATEU_CACHE nunca é limpo: um registro
# por nó cadastrado, tamanho natural pequeno.
_BATEU_CACHE: dict[int, float] = {}
_BATEU_MINIMO_S = 30.0


def no_autenticado(authorization: str | None, ip: str | None = None) -> dict:
    """Resolve o nó dono do Bearer token (comparação constante-tempo contra TX_TOKEN) e bate o
    heartbeat. Sem correspondência: 401. Nó inativado: 403 — a diferença importa para quem
    depura o nó ("token errado" × "o central me desligou").

    `ip` (quando o chamador tem): alimenta o freio de tentativas — o token é 256 bits, mas
    replay de credencial vazada e varredura de token curto querem resposta 429, não 401 ao
    infinito. Quem ACERTA o token nunca é contabilizado: o freio não alcança nó legítimo."""
    if not authorization or not authorization.lower().startswith("bearer "):
        _freio_falha(ip)
        raise HTTPException(status_code=401, detail="Token do servidor ausente.")
    bruto = authorization[7:].strip()
    if not bruto:
        _freio_falha(ip)
        raise HTTPException(status_code=401, detail="Token do servidor ausente.")
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {_COLUNAS} FROM investigacao.no_tb01_servidores s")
            colunas = [c.name for c in cur.description]
            alvo = None
            for row in cur.fetchall():
                d = dict(zip(colunas, row))
                if hmac.compare_digest(d.get("tx_token") or "", bruto):
                    alvo = d
                    break
            if alvo is None:
                _freio_falha(ip)
                raise HTTPException(status_code=401, detail="Token de servidor inválido.")
            if not alvo["in_ativa"]:
                raise HTTPException(status_code=403, detail="Servidor inativado no central.")
            agora = time.monotonic()
            if agora - _BATEU_CACHE.get(alvo["id_servidor"], 0.0) >= _BATEU_MINIMO_S:
                _BATEU_CACHE[alvo["id_servidor"]] = agora
                cur.execute(
                    "UPDATE investigacao.no_tb01_servidores SET dt_ultimo_heartbeat = now() "
                    "WHERE id_servidor = %s",
                    (alvo["id_servidor"],))
        conn.commit()
    _freio_ok(ip)
    alvo["online"] = True
    return alvo


# ------------------------------------------------------------------ freio de tentativas
# Janela deslizante por IP só de TENTATIVAS FALHAS de token (401). O central atende nós
# numa rede que pode não ser confiável; nenhum outro limite se aplica ao tráfego autenticado.

_FALHAS_TOKEN: dict[str, list] = {}
_FALHAS_JANELA_S = 300.0
_FALHAS_LIMITE = 40


def _freio_falha(ip: str | None) -> None:
    if not ip:
        return
    agora = time.time()
    tentativas = [t for t in _FALHAS_TOKEN.get(ip, []) if agora - t < _FALHAS_JANELA_S]
    if len(tentativas) >= _FALHAS_LIMITE:
        raise HTTPException(status_code=429,
                            detail="Demasiadas tentativas de token a partir deste endereço.")
    tentativas.append(agora)
    _FALHAS_TOKEN[ip] = tentativas
    if len(_FALHAS_TOKEN) > 2000:  # o freio não pode virar um segundo banco
        _FALHAS_TOKEN.clear()


def _freio_ok(ip: str | None) -> None:
    if ip:
        _FALHAS_TOKEN.pop(ip, None)


def exigir_no(request: Request,
              authorization: str | None = Header(default=None)) -> dict:
    return no_autenticado(authorization, _ip_no(request))


def _ip_no(request) -> str | None:
    """X-Real-IP (nginx; a API não tem porta publicada — o proxy é o único caminho) ou o
    soquete direto (instalação sem proxy na rede interna)."""
    if request is None:
        return None
    return (request.headers.get("x-real-ip") or "").strip()[:45] or (
        request.client.host if getattr(request, "client", None) else None)


def servidor_por_arquivo(no_arquivo: str) -> dict | None:
    """Resolve o nó dono do `.ufdr` gravado em INQ_TB02_EXTRACOES.NO_ARQUIVO — a pergunta que o
    leitor de mídia faz quando o arquivo NÃO está no disco do central (docs/no_descentralizado.md):
    a extração nasceu com ID_SERVIDOR, logo a prova mora na pasta daquele nó.

    Vale apenas nó ATIVO: nó inativado = arquivo indisponível (404 honesto), nunca chamada HTTP
    para um endereço que o admin acabou de desligar. Duas extrações com o mesmo NO_ARQUIVO
    (reimport): a mais recente manda — é ela que tem o arquivo hoje.

    O resultado passa a ser chamado a CADA imagem de uma galeria, então mora aqui um cache de 60 s
    (inclusive para o None: delegacia sem nó é o caso comum, e um SELECT por miniatura não)."""
    if not no_arquivo:
        return None
    import time

    agora = time.monotonic()
    guardado = _CACHE_NO_ARQUIVO.get(no_arquivo)
    if guardado is not None and agora - guardado[0] < _CACHE_NO_ARQUIVO_TTL:
        return guardado[1]
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {_COLUNAS} FROM investigacao.no_tb01_servidores s "
                "JOIN investigacao.inq_tb02_extracoes e ON e.id_servidor = s.id_servidor "
                "WHERE e.no_arquivo = %s AND s.in_ativa "
                "ORDER BY e.id_extracao DESC LIMIT 1",
                (no_arquivo,))
            alvo = _linha(cur)
    if len(_CACHE_NO_ARQUIVO) > 5000:  # o cache é a galeria de ontem, não um segundo banco
        _CACHE_NO_ARQUIVO.clear()
    _CACHE_NO_ARQUIVO[no_arquivo] = (agora, alvo)
    return alvo


# ------------------------------------------------------------------ papel "no": heartbeat de saída


def limpar_parciais_orfaos_no(idade_minima_segundos: int = 24 * 60 * 60) -> int:
    """Varre `.parcial` deixados por uploads repassados que morreram no meio (PUT /no/enviar).
    Espelho do limpar_ufdr_parciais_orfas do central, mas para os nomes que o NOSSO receptor
    grava (`<token><ext>.parcial`, de qualquer extensão) e sem depender do startup do central —
    o nó tem o seu. Só os antigos: um upload em andamento não pode ser apagado. Devolve quantos
    removeu.

    Varredura RECURSIVA de propósito: o organize move os uploads para
    data/<delegacia>/<inquérito>/<categoria>/ (3+ níveis) — o antigo varrer de 2 níveis nunca
    enxergava um único .parcial organizado."""
    import time

    from funcoes.organizacao_arquivos import PASTA_DADOS

    if not PASTA_DADOS.is_dir():
        return 0
    agora = time.time()
    removidos = 0
    try:
        candidatas = list(PASTA_DADOS.rglob("*.parcial"))
    except OSError:
        return 0
    for caminho in candidatas:
        try:
            if agora - caminho.stat().st_mtime < idade_minima_segundos:
                continue
            caminho.unlink()
            removidos += 1
            logger.warning("Removido .parcial órfão de upload: %s", caminho)
        except OSError:
            logger.exception("Falha ao remover .parcial órfão: %s", caminho)
    if removidos:
        logger.info("Uploads .parcial órfãos removidos no nó: %s", removidos)
    return removidos


def limpar_spools_entrega_orfaos(idade_minima_segundos: int = 3 * 24 * 60 * 60) -> int:
    """Remove pastas .entrega/<id> (data/ ou a reserva em /tmp) de extrações que morreram sem
    limpar: o `limpar()` do operário só age na passada que termina, e a cauda de uma entrega
    abortada (erro, queda, quarentena) apodrecia no disco da estação para sempre — lote a
    lote, dezenas de GB de spool JSONL.

    Pasta com flock VIVO nunca é tocada: o lock é do kernel (morreu o processo, liberado),
    então um .lock tomado = extração saudável ainda escrevendo ali. A idade mínima é folgada
    (3 dias): uma extração legítima bate no central a cada 2 min — se nada toca a pasta há
    3 dias, quem a criou já não existe."""
    import shutil
    import tempfile
    import time
    from pathlib import Path

    from funcoes.organizacao_arquivos import PASTA_DADOS

    removidos = 0
    agora = time.time()
    raizes = [PASTA_DADOS / ".entrega",
              Path(tempfile.gettempdir()) / "analyser-entrega"]
    for raiz in raizes:
        if not raiz.is_dir():
            continue
        try:
            pastas = [p for p in raiz.iterdir() if p.is_dir()]
        except OSError:
            continue
        for pasta in pastas:
            try:
                mtimes = [p.stat().st_mtime for p in pasta.rglob("*")] or [pasta.stat().st_mtime]
                if agora - max(mtimes) < idade_minima_segundos:
                    continue
                if _lock_entrega_vivo(pasta):
                    continue
                shutil.rmtree(pasta, ignore_errors=True)
                if not pasta.exists():
                    removidos += 1
                    logger.warning("Removido spool de entrega órfão: %s", pasta)
            except OSError:
                logger.exception("Falha ao remover spool órfão: %s", pasta)
    if removidos:
        logger.info("Spools de entrega órfãos removidos no nó: %s", removidos)
    return removidos


def _lock_entrega_vivo(pasta) -> bool:
    """True se o .lock da pasta está flockado AGORA (extração viva). O teste adquire e
    devolve o lock — quem o segurasse de verdade estaria bloqueando aqui, e só há uma
    extração por pasta."""
    lock = pasta / ".lock"
    if not lock.exists():
        return False
    try:
        import fcntl
    except ImportError:
        return False  # sem flock (dev Windows): confia só na idade
    try:
        fd = os.open(lock, os.O_RDWR)
    except OSError:
        return True  # existe e não abre: tratar como vivo é o lado seguro
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    except OSError:
        return True
    finally:
        os.close(fd)


def limpar_quarentena_rejeitados(idade_minima_dias: int | None = None) -> int:
    """A quarentena do lote 422 renomeia o arquivo para `.rejeitado` (prova do veneno) — mas
    dentro de pasta VIVA nada os tocava: com um central mal-versionado acumulavam para
    sempre no disco da estação, invisíveis ao spool_estado (que só conta `lote_*.jsonl`).
    Mantidos `NO_QUARENTENA_DIAS` dias (padrão 14) como evidência consultável — o contador
    `lote_quarentena` em /no/estado diz o total histórico — e removidos depois com log."""
    import tempfile
    import time
    from pathlib import Path

    from funcoes.organizacao_arquivos import PASTA_DADOS

    try:
        dias = max(1, int((os.getenv("NO_QUARENTENA_DIAS") or "").strip()
                          or idade_minima_dias or 14))
    except ValueError:
        dias = 14
    removidos = 0
    agora = time.time()
    for raiz in (PASTA_DADOS / ".entrega",
                 Path(tempfile.gettempdir()) / "analyser-entrega"):
        if not raiz.is_dir():
            continue
        try:
            candidatos = list(raiz.glob("*/*.rejeitado"))
        except OSError:
            continue
        for arq in candidatos:
            try:
                if agora - arq.stat().st_mtime < dias * 86400:
                    continue
                if _lock_entrega_vivo(arq.parent):
                    continue  # a passada que o rejeitou ainda escreve ali
                arq.unlink()
                removidos += 1
                logger.warning("Quarentena expirada (%d dias): removido %s", dias, arq)
            except OSError:
                logger.exception("Falha ao remover quarentena: %s", arq)
    return removidos


def iniciar_varredura_no(intervalo_segundos: int = 6 * 60 * 60) -> None:
    """Só no papel `no`: as varreduras de limpeza também são PERIÓDICAS, não só de startup —
    um nó que fica semanas de pé sem reiniciar acumulava .parcial de pastas organizadas e
    spool .entrega de extrações mortas porque nada mais as procurava. Ache barato (um rglob
    a cada 6 h no disco da estação); o prejuízo de não rodar é disco cheio com a prova em
    cima."""
    import threading
    import time as _t

    def _laco() -> None:
        while True:
            for tarefa in (limpar_parciais_orfaos_no, limpar_spools_entrega_orfaos,
                           limpar_quarentena_rejeitados):
                try:
                    tarefa()
                except Exception:
                    logger.exception("Varredura periódica do nó falhou (%s)", tarefa.__name__)
            _t.sleep(intervalo_segundos)

    threading.Thread(target=_laco, daemon=True, name="no-varredura").start()

def iniciar_heartbeat_central() -> None:
    """Só no papel `no`: thread daemon que bate em POST {CENTRAL_URL}/api/no/ping a cada
    NO_HEARTBEAT_SEGUNDOS. É o que mantém o ponto verde na tela Administração → Servidores
    mesmo com o nó ocioso — e o TX_VERSAO que denuncia nó desatualizado."""
    import threading
    import time

    import requests

    central_url = (os.getenv("CENTRAL_URL") or "").strip().rstrip("/")
    token = (os.getenv("NO_TOKEN") or "").strip()
    if not central_url or not token:
        logger.warning("Nó sem CENTRAL_URL/NO_TOKEN no .env — heartbeat desligado. "
                       "Configure-os no .env do nó (ver docs/no_descentralizado.md).")
        return
    _avisar_http(central_url)  # o token do heartbeat é o mesmo segredo dos dois sentidos
    intervalo = max(15, int(os.getenv("NO_HEARTBEAT_SEGUNDOS") or 60))

    def _laco() -> None:
        atraso = float(intervalo)
        while True:
            try:
                # versão RELIDA a cada batida: o nó se auto-atualiza pelo agente do host e o
                # container reinicia, mas quando o restart não vem (update travou no meio) o
                # heartbeat ficava reportando a versão do startup PARA SEMPRE, acendendo a
                # tela de servidores com a mentira "nó em dia"
                from funcoes import versao_utils
                versao = versao_utils.versao_instalada()
            except Exception:
                versao = None
            try:
                r = requests.post(f"{central_url}/api/no/ping",
                                  headers={"Authorization": f"Bearer {token}"},
                                  json={"versao": versao}, timeout=10)
                if r.status_code in (401, 403):
                    from funcoes import metricas_no
                    metricas_no.incr("heartbeat_recusado")
                    # token revogado / nó inativado NÃO se conserta batendo mais: sobe o
                    # atraso até 15 min (o log fala uma vez por fase de queda, como o túnel)
                    if atraso == float(intervalo):
                        logger.error("Central recusou o heartbeat (%s) — token revogado ou nó "
                                     "inativado. Refaça o cadastro na tela Servidores.",
                                     r.status_code)
                    atraso = min(900.0, max(300.0, atraso * 2))
                else:
                    atraso = float(intervalo)
            except Exception:
                from funcoes import metricas_no
                metricas_no.incr("heartbeat_falhas")
                logger.warning("Heartbeat do nó falhou (central fora do ar?) — tentando de novo "
                               "em %s s", intervalo)
                atraso = float(intervalo)
            time.sleep(atraso)

    threading.Thread(target=_laco, daemon=True, name="no-heartbeat").start()
    logger.info("Heartbeat do nó ligado: central %s, a cada %s s", central_url, intervalo)

"""Detecção de chaves PIX mencionadas nas conversas, ligando-as ao contato.

Estratégia (decidida com o investigador): só olha mensagens que citam "pix" ou "chave"
(pré-filtro no SQL) e, nelas, extrai a chave por tipo — CPF, CNPJ, e-mail, telefone ou
aleatória (EVP, formato UUID). CPF/CNPJ passam por validação de dígito verificador para
não confundir qualquer número solto com chave. Cada ocorrência é gravada associada TANTO
ao remetente da mensagem QUANTO à conversa em que apareceu (ver 18_credenciais_financeiro.sql).
"""

import re

from funcoes.cpf_cnpj_utils import valida_cnpj as _valida_cnpj, valida_cpf as _valida_cpf
from funcoes.db_utils import get_connection
from funcoes.investigacao_utils import _TABELAS_CONVERSA, obter_id_inquerito_de_extracao

# formatos das chaves. Ordem de precedência: uuid > e-mail > cnpj > cpf > telefone (aplicada por
# um mapa de posições "ocupadas", pra um CNPJ não ser recortado como CPF, etc.)
_RE_UUID = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
_RE_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_RE_CNPJ = re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b")
_RE_CPF = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")
# telefone BR: +55 opcional, DDD (com ou sem parênteses), 9 opcional, 4+4 dígitos
_RE_TEL = re.compile(r"(?:\+?55[\s.-]?)?\(?\d{2}\)?[\s.-]?9?\d{4}[\s.-]?\d{4}")


def _so_digitos(texto: str) -> str:
    return re.sub(r"\D", "", texto)


def _telefone_normalizado(bruto: str) -> str | None:
    """Normaliza um telefone brasileiro para só dígitos (sem o 55 de país); None se não parecer válido."""
    dig = _so_digitos(bruto)
    if len(dig) in (12, 13) and dig.startswith("55"):   # tira código do país
        dig = dig[2:]
    if len(dig) not in (10, 11):
        return None
    ddd = int(dig[:2])
    if ddd < 11 or ddd > 99:                             # DDD brasileiro válido
        return None
    return dig


def extrair_chaves_pix(texto: str) -> list[dict]:
    """Extrai as chaves PIX de um texto (que já se sabe citar 'pix'/'chave'). Devolve uma lista de
    {tipo, chave, original}, sem repetição de (tipo, chave). Posições já casadas por um tipo de maior
    precedência não são reaproveitadas por outro (ex.: os 11 dígitos de dentro de um CNPJ)."""
    if not texto:
        return []
    ocupado = bytearray(len(texto))

    def livre(ini: int, fim: int) -> bool:
        return not any(ocupado[ini:fim])

    def marcar(ini: int, fim: int) -> None:
        for i in range(ini, fim):
            ocupado[i] = 1

    achados: list[dict] = []

    for m in _RE_UUID.finditer(texto):
        achados.append({"tipo": "aleatoria", "chave": m.group().lower(), "original": m.group()})
        marcar(*m.span())
    for m in _RE_EMAIL.finditer(texto):
        if livre(*m.span()):
            achados.append({"tipo": "email", "chave": m.group().lower(), "original": m.group()})
            marcar(*m.span())
    for m in _RE_CNPJ.finditer(texto):
        if livre(*m.span()):
            dig = _so_digitos(m.group())
            if _valida_cnpj(dig):
                achados.append({"tipo": "cnpj", "chave": dig, "original": m.group()})
                marcar(*m.span())
    for m in _RE_CPF.finditer(texto):
        if livre(*m.span()):
            dig = _so_digitos(m.group())
            if _valida_cpf(dig):     # se não passa no dígito verificador, deixa livre p/ virar telefone
                achados.append({"tipo": "cpf", "chave": dig, "original": m.group()})
                marcar(*m.span())
    for m in _RE_TEL.finditer(texto):
        if livre(*m.span()):
            dig = _telefone_normalizado(m.group())
            if dig:
                achados.append({"tipo": "telefone", "chave": dig, "original": m.group().strip()})
                marcar(*m.span())

    # remove repetições de (tipo, chave) dentro da mesma mensagem
    vistos = set()
    unicos = []
    for a in achados:
        ch = (a["tipo"], a["chave"])
        if ch not in vistos:
            vistos.add(ch)
            unicos.append(a)
    return unicos


def _trecho(texto: str, limite: int = 300) -> str:
    """Trecho curto da mensagem como evidência (colapsa espaços/quebras, corta no limite)."""
    t = re.sub(r"\s+", " ", texto or "").strip()
    return t if len(t) <= limite else t[:limite] + "…"


def _cap(texto: str | None, limite: int = 3000) -> str | None:
    """Apara para a largura das colunas varchar(3000) de PIX_TB01_CHAVES. `outro_lado` é um
    string_agg dos participantes da conversa — num grupo grande estoura a largura e o INSERT
    derrubava a varredura inteira (que é best-effort no fim da entrega)."""
    return texto[: limite - 1] + "…" if texto and len(texto) > limite else texto


def _query_mensagens(fonte: str) -> str:
    """SQL que traz as mensagens de uma fonte que citam pix/chave, com remetente e o outro lado da
    conversa já resolvidos. O pré-filtro ILIKE reduz muito o que o Python precisa varrer com regex."""
    t = _TABELAS_CONVERSA[fonte]
    app_sel = "c.tp_app AS app," if t.get("tem_app") else "NULL AS app,"
    return f"""
        SELECT m.id_mensagem, m.id_conversa, m.tx_mensagem, m.dt_mensagem,
               m.id_contato_remetente, rc.no_contato AS no_remetente,
               coalesce(rc.in_dono_dispositivo, false) AS remetente_dono,
               c.no_grupo, c.tp_conversa, {app_sel}
               (SELECT string_agg(DISTINCT ct.no_contato, ', ')
                  FROM investigacao.{t['participantes']} p
                  JOIN investigacao.{t['contatos']} ct ON ct.id_contato = p.id_contato
                 WHERE p.id_conversa = c.id_conversa AND NOT coalesce(ct.in_dono_dispositivo, false)
               ) AS outro_lado
        FROM investigacao.{t['mensagens']} m
        JOIN investigacao.{t['conversas']} c ON c.id_conversa = m.id_conversa
        LEFT JOIN investigacao.{t['contatos']} rc ON rc.id_contato = m.id_contato_remetente
        WHERE c.id_extracao = %s
          AND (m.tx_mensagem ILIKE '%%pix%%' OR m.tx_mensagem ILIKE '%%chave%%')
    """


# Quantas mensagens por FETCH na varredura de chaves. O pico de memória da detecção é este bloco,
# não mais a extração inteira.
_LOTE_LEITURA = 2000


def detectar_chaves_pix(id_extracao: int) -> int:
    """Varre as mensagens da extração, extrai as chaves PIX e grava em PIX_TB01_CHAVES (uma linha por
    ocorrência chave×mensagem), associando ao remetente e à conversa. Idempotente: limpa o que já
    havia da extração antes. Atualiza QT_CHAVES_PIX e devolve o total encontrado."""
    from funcoes import canal_extracao
    if canal_extracao.em_modo_entrega():
        return 0  # nó: a varredura lê mensagens do BANCO — quem roda é o central, após a entrega
    total = 0
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM investigacao.pix_tb01_chaves WHERE id_extracao = %s", (id_extracao,))
        conn.commit()

        for fonte in ("wpp", "sms", "msg"):
            # fetchall() trazia TODAS as mensagens da extração — com o corpo inteiro — para a
            # memória de uma vez. Num aparelho de algumas centenas de milhares de mensagens isso
            # é a extração inteira em RAM logo depois do passe do report.xml, com as outras
            # extrações simultâneas fazendo o mesmo. A varredura é linha a linha e não precisa
            # de nada disso: fetchmany em blocos mantém o pico limitado ao bloco.
            # cursor NOMEADO (server-side): o cursor comum do psycopg já traz o resultado inteiro
            # para o cliente no execute(), então fetchmany sozinho não pouparia nada — ele só
            # fatiaria o que já está na memória. Com o cursor no servidor, cada FETCH traz um
            # bloco de verdade.
            with conn.cursor(name=f"pix_{fonte}_{id_extracao}") as cur:
                cur.execute(_query_mensagens(fonte), (id_extracao,))
                colunas = [d.name for d in cur.description]
                while True:
                    linhas = cur.fetchmany(_LOTE_LEITURA)
                    if not linhas:
                        break
                    registros = []
                    for linha in linhas:
                        r = dict(zip(colunas, linha))
                        chaves = extrair_chaves_pix(r["tx_mensagem"])
                        if not chaves:
                            continue
                        no_conversa = r["no_grupo"] if r["tp_conversa"] == "grupo" else r["outro_lado"]
                        trecho = _trecho(r["tx_mensagem"])
                        for c in chaves:
                            registros.append((
                                id_extracao, fonte, _cap(r["app"]), c["chave"], c["tipo"], c["original"][:160],
                                r["id_mensagem"], r["id_conversa"], _cap(no_conversa or None),
                                r["id_contato_remetente"], _cap(r["no_remetente"]), r["remetente_dono"],
                                trecho, r["dt_mensagem"],
                            ))
                    if not registros:
                        continue
                    # cursor próprio: o de leitura ainda tem resultado pendente
                    with conn.cursor() as cur_ins:
                        cur_ins.executemany(
                            """
                            INSERT INTO investigacao.pix_tb01_chaves
                                (id_extracao, no_fonte, no_app, co_chave, tp_chave, tx_chave_original,
                                 id_mensagem, id_conversa, no_conversa, id_contato_remetente, no_remetente,
                                 in_remetente_dono, tx_trecho, dt_mensagem)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            """,
                            registros,
                        )
                    total += len(registros)
            conn.commit()

        with conn.cursor() as cur:
            cur.execute(
                "UPDATE investigacao.inq_tb02_extracoes SET qt_chaves_pix = %s WHERE id_extracao = %s",
                (total, id_extracao),
            )
        conn.commit()
    return total


def listar_chaves_pix(id_inquerito: int, id_extracao: int | None = None) -> list[dict]:
    """Lista as chaves PIX detectadas de um inquérito (ou de uma extração), agrupáveis na tela por
    chave ou por contato. Ordena por chave e data."""
    filtros = ["ex.id_inquerito = %s"]
    params: list = [id_inquerito]
    if id_extracao is not None:
        filtros.append("k.id_extracao = %s")
        params.append(id_extracao)
    where = " AND ".join(filtros)
    query = f"""
        SELECT k.id_chave_pix, k.no_fonte, k.no_app, k.co_chave, k.tp_chave, k.tx_chave_original,
               k.id_mensagem, k.id_conversa, k.no_conversa, k.id_contato_remetente, k.no_remetente,
               k.in_remetente_dono, k.tx_trecho, k.dt_mensagem, k.id_extracao
        FROM investigacao.pix_tb01_chaves k
        JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = k.id_extracao
        WHERE {where}
        ORDER BY k.tp_chave, k.co_chave, k.dt_mensagem NULLS LAST
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            colunas = [d.name for d in cur.description]
            return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


def status_deteccao_extracao(id_extracao: int) -> dict | None:
    """Devolve {id_inquerito, escaneado, qt_chaves} da extração (para a página saber se já rodou a
    detecção). None se a extração não existir."""
    id_inquerito = obter_id_inquerito_de_extracao(id_extracao)
    if id_inquerito is None:
        return None
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT qt_chaves_pix FROM investigacao.inq_tb02_extracoes WHERE id_extracao = %s",
                (id_extracao,),
            )
            row = cur.fetchone()
    qt = row[0] if row else None
    return {"id_inquerito": id_inquerito, "escaneado": qt is not None, "qt_chaves": qt or 0}

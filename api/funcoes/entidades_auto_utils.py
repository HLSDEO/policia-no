"""Entidades automatizadas: extração determinística de entidades de documentos genéricos.

Hoje a análise de um documento anexado (INQ_TB04_DOCUMENTOS) é 100% IA
(funcoes/documentos_utils.py) — e a produção mostra o custo disso: 36% dos achados caem no
tipo genérico "documento", com CPF/CNPJ/CNJ válidos perdidos ali. Esta é a outra metade:
regex com validação (dígito verificador de CPF/CNPJ, telefone, CNJ...) e normalização
(só-dígitos, minúsculo, placa sem traço), auditável e sem custo de IA.

Duas formas de usar, mesma função pura no meio (`extrair_automaticas_texto`):

- pré-passe da análise por IA: documentos_utils roda isto antes de chamar o modelo e diz
  à IA para não repetir o que já saiu daqui (a IA fica no resíduo: pessoas, vínculos,
  tipificação, endereços);
- job por inquérito ("Extração automática", abaixo): varre o tx_conteudo de todos os
  documentos e grava DOC_TB07_AUTO_DETECCOES — ver db/init/47_entidades_automaticas.sql.

Prioridade entre padrões é a ordem de id (os seeds entram do específico para o genérico):
trecho já ocupado por um padrão não é recontado por outro — é assim que "Conta: 1643" não
vira telefone e "(27) 99847-7194" não vira conta.
"""

import json
import logging
import re
import threading

import psycopg
from funcoes.db_utils import get_connection

logger = logging.getLogger(__name__)

# tipos canônicos de achado que o determinístico produz (e que a IA deve usar — ver o
# complemento no SISTEMA_PROMPT_ANALISE_DOCUMENTO). Vem da produção: são os que o regex
# alcança; endereço/tipificação continuam só com a IA.
TIPOS_ACHADO_VALIDOS = {
    "cpf", "cnpj", "telefone", "email", "valor", "conta", "agencia",
    "processo", "procedimento", "oab", "placa_veiculo", "documento",
}

# o que a IA já produziu fora do enum (levantado na produção, inquérito 187) e para onde
# cada variante dobra na leitura — sem isto "endereço" e "endereco" viram nós distintos.
_MAPA_TIPO_ACHADO = {
    "cpf_cnpj": "cpf_cnpj",
    "cpf": "cpf",
    "cnpj": "cnpj",
    "endereço": "endereco",
}

VALIDADORES_VALIDOS = {"nenhum", "cpf", "cnpj", "telefone", "email", "placa", "valor", "cnj", "conta"}
NORMALIZACOES_VALIDAS = {"digitos", "minusculo", "placa", "valor", "cnj", "oab", "ipl"}

# tetos por segurança: um documento patológico não pode inundar a execução
_MAX_MATCHES_POR_PADRAO = 500
_MAX_DISTINTOS_POR_DOC_PADRAO = 100
_MAX_TEXTO_AUTO = 200_000
_JANELA_TRECHO = 120


def normalizar_tp_achado(tp_achado: str | None) -> str:
    """Dobra variantes que a IA inventa para o tipo canônico ('endereço'→'endereco').
    Usado na leitura do grafo (cobre o legado) e na fusão com a IA."""
    tp = (tp_achado or "documento").strip().lower()[:40] or "documento"
    return _MAPA_TIPO_ACHADO.get(tp, tp)


# ---------- validadores e normalizadores (puros, testáveis sem banco) ----------


def _so_digitos(texto: str | None) -> str:
    return re.sub(r"\D", "", str(texto or ""))


def validar_cpf(valor: str) -> bool:
    """CPF com os dois dígitos verificadores (módulo 11); rejeita sequências triviais."""
    d = _so_digitos(valor)
    if len(d) != 11 or len(set(d)) == 1:
        return False
    for pos in (9, 10):
        soma = sum(int(d[i]) * (pos + 1 - i) for i in range(pos))
        if int(d[pos]) != (soma * 10) % 11 % 10:
            return False
    return True


def validar_cnpj(valor: str) -> bool:
    """CNPJ com os dois dígitos verificadores; rejeita sequências triviais."""
    d = _so_digitos(valor)
    if len(d) != 14 or len(set(d)) == 1:
        return False
    for pos, pesos in (
        (12, (5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)),
        (13, (6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)),
    ):
        soma = sum(int(d[i]) * pesos[i] for i in range(pos))
        resto = soma % 11
        if int(d[pos]) != (0 if resto < 2 else 11 - resto):
            return False
    return True


def normalizar_valor(valor: str) -> str:
    """'R$ 552.879,99' → '552879.99' (convenção BR: ponto é milhar, vírgula é decimal)."""
    s = re.sub(r"^[Rr]\$\s*", "", valor.strip()).rstrip(".,;:")
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(".", "").replace(",", "")
    return s


def validar_valor(valor: str) -> bool:
    try:
        return float(normalizar_valor(valor)) > 0
    except ValueError:
        return False


def normalizar_por_tipo(tp_achado: str, valor: str) -> str:
    """Chave de deduplicação: duas grafias do mesmo dado viram a mesma chave."""
    v = valor.strip()
    if tp_achado in ("cpf", "cnpj", "telefone", "conta", "agencia", "cnj", "processo"):
        return _so_digitos(v)
    if tp_achado == "email":
        return v.lower()
    if tp_achado == "placa_veiculo":
        return re.sub(r"[^A-Z0-9]", "", v.upper())
    if tp_achado == "valor":
        try:
            return f"{float(normalizar_valor(v)):.2f}"
        except ValueError:
            return v.lower()
    if tp_achado == "oab":
        return re.sub(r"[^A-Z0-9/]", "", v.upper())
    if tp_achado == "procedimento":
        return re.sub(r"[^0-9A-Z./-]", "", v.upper())
    return re.sub(r"[^a-z0-9]", "", v.lower())


def validar_por_tipo(validador: str, tp_achado: str, valor: str) -> bool:
    """Validação do valor conforme o validador do padrão."""
    if validador == "nenhum":
        return bool(valor.strip())
    if validador == "cpf":
        return validar_cpf(valor)
    if validador == "cnpj":
        return validar_cnpj(valor)
    if validador == "telefone":
        return len(_so_digitos(valor)) in (10, 11)
    if validador == "email":
        return bool(re.fullmatch(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", valor.strip())) and len(valor) <= 254
    if validador == "placa":
        return len(normalizar_por_tipo("placa_veiculo", valor)) == 7
    if validador == "valor":
        return validar_valor(valor)
    if validador == "cnj":
        return len(_so_digitos(valor)) == 20
    if validador == "conta":
        return 4 <= len(_so_digitos(valor)) <= 15
    return False


def chave_achado(tp_achado: str, valor: str) -> str:
    """Chave que casa um achado da IA com um automático: tipo normalizado + valor normalizado."""
    tp = normalizar_tp_achado(tp_achado)
    return tp + ":" + (normalizar_por_tipo(tp, valor) or valor.strip().lower())


def validar_definicao(definicao: dict) -> dict:
    """Valida e normaliza a definição JSON de um padrão automático; devolve a limpa.
    Levanta ValueError com mensagem legível quando algo não fecha."""
    if not isinstance(definicao, dict):
        raise ValueError("Definição precisa ser um objeto JSON.")
    padrao = (definicao.get("padrao") or "").strip()
    if not padrao:
        raise ValueError("Um padrão automático precisa da expressão regular.")
    try:
        compilado = re.compile(padrao, re.IGNORECASE)
    except re.error as exc:
        raise ValueError(f"Expressão regular inválida: {exc}")
    try:
        grupo = int(definicao.get("grupo", 0))
    except (TypeError, ValueError):
        raise ValueError("O grupo precisa ser um número inteiro (0 = o match inteiro).")
    if grupo < 0 or grupo > compilado.groups:
        raise ValueError(f"O grupo {grupo} não existe nesta expressão (ela tem {compilado.groups}).")
    validador = str(definicao.get("validador") or "nenhum").strip()
    if validador not in VALIDADORES_VALIDOS:
        raise ValueError(f"Validador inválido: {validador!r}.")
    normalizacao = str(definicao.get("normalizacao") or "digitos").strip()
    if normalizacao not in NORMALIZACOES_VALIDAS:
        raise ValueError(f"Normalização inválida: {normalizacao!r}.")
    return {"padrao": padrao, "grupo": grupo, "validador": validador, "normalizacao": normalizacao}


def validar_tp_achado(tp_achado: str) -> str:
    tp = normalizar_tp_achado(tp_achado)
    if tp not in TIPOS_ACHADO_VALIDOS and tp != "cpf_cnpj":
        raise ValueError(f"Tipo de achado inválido: {tp_achado!r}.")
    return tp


# ---------- extração pura (sem banco: roda no pré-passe da IA e no job) ----------


def _trecho(texto: str, inicio: int, fim: int) -> str:
    janela = texto[max(0, inicio - _JANELA_TRECHO):fim + _JANELA_TRECHO]
    return re.sub(r"\s+", " ", janela).strip()[:500]


def extrair_automaticas_texto(texto: str, padroes: list[dict]) -> list[dict]:
    """Roda os padrões (ativos, em ordem de id) sobre um texto e devolve os achados.

    Cada item: {id_padrao, tp_achado, tx_valor, co_chave, tx_trecho, qt_ocorrencias}.
    Trecho já ocupado por um padrão anterior não é recontado (é a prioridade que evita que
    telefone vire conta e CPF vire valor). Nunca levanta por padrão quebrado: quem não
    compila é pulado — o job registra o aviso.
    """
    texto = str(texto or "")
    if len(texto) > _MAX_TEXTO_AUTO:
        texto = texto[:_MAX_TEXTO_AUTO]
    ocupados: list[tuple[int, int]] = []
    agregados: dict[tuple[int, str], dict] = {}

    def _livre(inicio: int, fim: int) -> bool:
        return all(fim <= a or inicio >= b for a, b in ocupados)

    for p in padroes:
        try:
            definicao = p["tx_definicao"] if isinstance(p.get("tx_definicao"), dict) else json.loads(p.get("tx_definicao") or "{}")
            compilado = re.compile(definicao["padrao"], re.IGNORECASE)
            grupo = int(definicao.get("grupo", 0))
            validador = str(definicao.get("validador") or "nenhum")
        except Exception:
            continue
        n = 0
        try:
            for m in compilado.finditer(texto):
                n += 1
                if n > _MAX_MATCHES_POR_PADRAO:
                    break
                inicio, fim = m.span()
                if fim <= inicio or not _livre(inicio, fim):
                    continue
                try:
                    bruto = m.group(grupo) if grupo <= (compilado.groups or 0) else None
                except IndexError:
                    bruto = None
                if bruto is None:  # grupo da alternativa que não casou — vale o match inteiro
                    bruto = m.group(0)
                bruto = str(bruto or "").strip().rstrip(".,;:")
                if not bruto:
                    continue
                tp = normalizar_tp_achado(p.get("tp_achado"))
                if not validar_por_tipo(validador, tp, bruto):
                    continue
                chave = tp + ":" + (normalizar_por_tipo(tp, bruto) or bruto.lower())
                if not chave or len(chave) > 340:
                    continue
                ocupados.append((inicio, fim))
                agg = agregados.get((p["id_padrao"], chave))
                if agg is None:
                    if sum(1 for (pp, _c) in agregados if pp == p["id_padrao"]) >= _MAX_DISTINTOS_POR_DOC_PADRAO:
                        continue
                    agregados[(p["id_padrao"], chave)] = {
                        "id_padrao": p["id_padrao"],
                        "tp_achado": tp,
                        "tx_valor": bruto[:200],
                        "co_chave": chave[len(tp) + 1:][:300],
                        "tx_trecho": _trecho(texto, inicio, fim),
                        "qt_ocorrencias": 1,
                    }
                else:
                    agg["qt_ocorrencias"] += 1
        except Exception:
            logger.exception("Padrão automático %s quebrou no meio da extração", p.get("id_padrao"))
    return list(agregados.values())


# ---------- CRUD da biblioteca ----------


def listar_padroes() -> list[dict]:
    """Todos os padrões automáticos (ativos e inativos), mais recentes primeiro."""
    query = """
        SELECT p.id_auto_padrao, p.no_padrao, p.tp_padrao, p.tp_achado, p.no_categoria,
               p.ds_padrao, p.tx_definicao, p.in_ativo, p.dt_cadastro, u.no_usuario AS criado_por
        FROM investigacao.doc_tb05_auto_padroes p
        LEFT JOIN seguranca.usr_tb01_usuarios u ON u.id_usuario = p.id_usuario_criador
        ORDER BY p.dt_cadastro DESC
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query)
            colunas = [d.name for d in cur.description]
            return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


def listar_padroes_ativos() -> list[dict]:
    """Só os ativos, em ordem de id (a ordem é a prioridade na extração)."""
    query = """
        SELECT id_auto_padrao AS id_padrao, tp_achado, tx_definicao
        FROM investigacao.doc_tb05_auto_padroes
        WHERE in_ativo
        ORDER BY id_auto_padrao
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query)
            colunas = [d.name for d in cur.description]
            return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


def criar_padrao(
    nome: str, tp_achado: str, definicao: dict, categoria: str | None = None,
    descricao: str | None = None, id_usuario: int | None = None,
) -> int:
    """Cria um padrão automático novo; devolve o ID. Levanta ValueError se não fechar."""
    nome = (nome or "").strip()
    if not nome:
        raise ValueError("Nome do padrão não pode ser vazio.")
    tp_achado = validar_tp_achado(tp_achado)
    definicao = validar_definicao(definicao)
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    """
                    INSERT INTO investigacao.doc_tb05_auto_padroes
                        (no_padrao, tp_achado, no_categoria, ds_padrao, tx_definicao, id_usuario_criador)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id_auto_padrao
                    """,
                    (nome, tp_achado, categoria, descricao, json.dumps(definicao), id_usuario),
                )
            except Exception as exc:
                if "UQ11" in str(exc) or "uq11" in str(exc).lower() or "duplicate" in str(exc).lower():
                    raise ValueError(f"Já existe um padrão automático chamado {nome!r}.")
                raise
            id_padrao = cur.fetchone()[0]
        conn.commit()
    return id_padrao


def atualizar_padrao(
    id_padrao: int, nome: str, tp_achado: str, definicao: dict,
    categoria: str | None = None, descricao: str | None = None,
) -> bool:
    """Atualiza um padrão existente; devolve False se o id não existir."""
    nome = (nome or "").strip()
    if not nome:
        raise ValueError("Nome do padrão não pode ser vazio.")
    tp_achado = validar_tp_achado(tp_achado)
    definicao = validar_definicao(definicao)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE investigacao.doc_tb05_auto_padroes
                SET no_padrao = %s, tp_achado = %s, no_categoria = %s, ds_padrao = %s, tx_definicao = %s
                WHERE id_auto_padrao = %s
                """,
                (nome, tp_achado, categoria, descricao, json.dumps(definicao), id_padrao),
            )
            atualizado = cur.rowcount > 0
        conn.commit()
    return atualizado


def definir_ativo(id_padrao: int, ativo: bool) -> bool:
    """Ativa/desativa um padrão (só os ativos entram na próxima extração)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE investigacao.doc_tb05_auto_padroes SET in_ativo = %s WHERE id_auto_padrao = %s",
                (ativo, id_padrao),
            )
            atualizado = cur.rowcount > 0
        conn.commit()
    return atualizado


def remover_padrao(id_padrao: int) -> bool:
    """Remove um padrão (as detecções dele caem junto via ON DELETE CASCADE)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM investigacao.doc_tb05_auto_padroes WHERE id_auto_padrao = %s", (id_padrao,))
            removido = cur.rowcount > 0
        conn.commit()
    return removido


# ---------- execução por inquérito (job em background com polling) ----------


class ExecucaoOcupada(RuntimeError):
    """Já há uma extração automática ativa neste inquérito. As rotas traduzem em 409."""


def iniciar_extracao(id_inquerito: int, id_usuario: int | None) -> int:
    """Cria a execução (status 'pendente') e dispara o processamento em background;
    devolve o ID da execução. Mesmo padrão de job/polling das demais.

    Uma ativa por inquérito (UX115_DOC_TB06_ATIVA): o segundo POST viola o índice e vira
    ExecucaoOcupada, em vez de subir N threads sobre os mesmos documentos. Ver C8."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    "INSERT INTO investigacao.doc_tb06_auto_execucoes (id_inquerito, id_usuario) VALUES (%s, %s) RETURNING id_execucao",
                    (id_inquerito, id_usuario),
                )
            except psycopg.errors.UniqueViolation:
                conn.rollback()
                raise ExecucaoOcupada("Já há uma extração automática em andamento neste inquérito.")
            id_execucao = cur.fetchone()[0]
        conn.commit()
    thread = threading.Thread(target=_processar_extracao, args=(id_execucao, id_inquerito), daemon=True)
    thread.start()
    return id_execucao


def listar_execucoes(id_inquerito: int) -> list[dict]:
    """Histórico de execuções da extração automática, mais recente primeiro (polling)."""
    query = """
        SELECT x.id_execucao, x.cd_status, x.tx_erro, x.qt_deteccoes, x.dt_inicio, x.dt_fim,
               u.no_usuario AS iniciado_por
        FROM investigacao.doc_tb06_auto_execucoes x
        LEFT JOIN seguranca.usr_tb01_usuarios u ON u.id_usuario = x.id_usuario
        WHERE x.id_inquerito = %s
        ORDER BY x.dt_inicio DESC
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (id_inquerito,))
            colunas = [d.name for d in cur.description]
            return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


def listar_deteccoes(id_inquerito: int, id_execucao: int | None = None) -> list[dict]:
    """Detecções de uma execução (a mais recente concluída, se não informada), com os
    dados do padrão — o front agrupa por padrão."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            if id_execucao is None:
                cur.execute(
                    """
                    SELECT id_execucao FROM investigacao.doc_tb06_auto_execucoes
                    WHERE id_inquerito = %s AND cd_status = 'concluido'
                    ORDER BY dt_inicio DESC LIMIT 1
                    """,
                    (id_inquerito,),
                )
                row = cur.fetchone()
                if row is None:
                    return []
                id_execucao = row[0]
            cur.execute(
                """
                SELECT d.id_deteccao, d.id_execucao, d.id_auto_padrao, d.id_documento,
                       d.tp_achado, d.tx_valor, d.co_chave, d.tx_trecho, d.qt_ocorrencias,
                       doc.no_arquivo, p.no_padrao, p.no_categoria
                FROM investigacao.doc_tb07_auto_deteccoes d
                JOIN investigacao.doc_tb05_auto_padroes p ON p.id_auto_padrao = d.id_auto_padrao
                JOIN investigacao.inq_tb04_documentos doc ON doc.id_documento = d.id_documento
                WHERE d.id_inquerito = %s AND d.id_execucao = %s
                ORDER BY p.no_padrao, d.tx_valor
                """,
                (id_inquerito, id_execucao),
            )
            colunas = [d.name for d in cur.description]
            return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


def _processar_extracao(id_execucao: int, id_inquerito: int) -> None:
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE investigacao.doc_tb06_auto_execucoes SET cd_status = 'processando' WHERE id_execucao = %s",
                    (id_execucao,),
                )
            conn.commit()

        padroes = listar_padroes_ativos()
        if not padroes:
            raise ValueError("Nenhum padrão automático ativo na aba Entidades automatizadas.")

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id_documento, tx_conteudo
                    FROM investigacao.inq_tb04_documentos
                    WHERE id_inquerito = %s AND tx_conteudo IS NOT NULL AND tx_erro IS NULL
                    ORDER BY id_documento
                    """,
                    (id_inquerito,),
                )
                documentos = cur.fetchall()

        total = 0
        with get_connection() as conn:
            with conn.cursor() as cur:
                for id_documento, conteudo in documentos:
                    try:
                        achados = extrair_automaticas_texto(conteudo or "", padroes)
                    except Exception:
                        logger.exception("Extração automática quebrou no documento %s", id_documento)
                        continue
                    for a in achados:
                        cur.execute(
                            """
                            INSERT INTO investigacao.doc_tb07_auto_deteccoes
                                (id_execucao, id_inquerito, id_auto_padrao, id_documento,
                                 tp_achado, tx_valor, co_chave, tx_trecho, qt_ocorrencias)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (id_execucao, id_documento, id_auto_padrao, co_chave) DO NOTHING
                            """,
                            (
                                id_execucao, id_inquerito, a["id_padrao"], id_documento,
                                a["tp_achado"], a["tx_valor"], a["co_chave"],
                                a["tx_trecho"], a["qt_ocorrencias"],
                            ),
                        )
                        total += cur.rowcount
            conn.commit()

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE investigacao.doc_tb06_auto_execucoes
                    SET cd_status = 'concluido', dt_fim = now(), qt_deteccoes = %s
                    WHERE id_execucao = %s
                    """,
                    (total, id_execucao),
                )
            conn.commit()
    except Exception as exc:
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE investigacao.doc_tb06_auto_execucoes
                        SET cd_status = 'erro', tx_erro = %s, dt_fim = now()
                        WHERE id_execucao = %s
                        """,
                        (str(exc)[:2000], id_execucao),
                    )
                conn.commit()
        except Exception:
            logger.exception("Falha ao gravar status de erro da extração automática %s", id_execucao)

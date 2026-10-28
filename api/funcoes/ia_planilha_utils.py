"""Consolidação pesada pedida no chat de IA — o "agente" que varre muitos documentos e devolve um .xlsx.

O caso que motivou isto: "faça uma planilha com todas as armas apreendidas em 2026 a partir dos 200 PDFs".
Nenhuma resposta de chat cabe numa consolidação assim — o teto de tokens da resposta corta a tabela no
meio e o "continue" da vez seguinte não recupera o que ficou para trás. O caminho certo é um job em
background (JOB_TB01_JOBS, tp_job='consolidacao_ia'):

  - **avaliar_consolidacao()** conta os documentos do escopo e estima o tempo ANTES de disparar — é o
    que a ferramenta mostra à IA para ela explicar o funcionamento ao investigador e pedir confirmação;
  - o worker (:func:`consolidar_em_planilha`) faz map-reduce: uma chamada à IA por lote de texto de cada
    documento, pedindo as ocorrências em JSON com as colunas pedidas; no fim junta tudo, deduplica de
    forma determinística (sem IA — não queremos o modelo reescrevendo nome de ninguém na hora de fundir)
    e monta o .xlsx com openpyxl;
  - o arquivo fica em data/planilhas_ia e a linha em IA_TB07_PLANILHAS; o cartão de progresso já na
    bolha (co_jobs, migração 0045) faz polling do job e mostra o link de download quando conclui —
    mesmo desenho do .docx de ia_relatorio_docx.py.

O job NÃO é da fila de CPU (fl_pesado=False): quem disputa aqui é cota de IA, não Tesseract.
"""

from __future__ import annotations

import json
import logging
import math
import re
import threading
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path

from funcoes.db_utils import get_connection
from funcoes.investigacao_utils import PASTA_DADOS

logger = logging.getLogger(__name__)

PASTA_PLANILHAS_IA = Path(PASTA_DADOS) / "planilhas_ia"
ROTA_DOWNLOAD = "/api/investigacao/ia/planilhas"

_DIAS_MANTER_ARQUIVO = 30   # mesma janela dos .docx do chat; a planilha é regerável

_LOTE_CHARS = 60_000              # texto por chamada à IA no mapa (~15k tokens)
_MAX_LOTES_POR_DOCUMENTO = 40     # documento colossal é amostrado no início, com aviso na planilha
_MAX_TOKENS_SAIDA_MAPA = 4_096
_SEGUNDOS_POR_LOTE = 45           # régua da estimativa: ida à IA com 60k chars de ida e JSON de volta
_MINUTOS_CUSTO_FIXO = 2           # abrir job, juntar, montar o xlsx
_MAX_CELULA = 32_000              # teto prático de célula do xlsx (o formato corta em 32767)
_MAX_VALOR_LINHA = 2_000          # valor de célula extraído não pode carregar página inteira

# Coluna sempre acrescida: sem fonte não há como o investigador conferir a linha no papel.
COLUNA_FONTE = "Fonte"


class SemDocumentos(RuntimeError):
    """Nenhum documento com texto extraído no escopo pedido — nada que consolidar."""


class _CanceladoInterno(Exception):
    """Cancelamento pedido no meio da leitura de um documento — desfaz os laços até o marca_cancelado."""


# =====================================================================
# Escopo: quais documentos entrarão na varredura
# =====================================================================

def _listar_documentos(id_inquerito: int, escopos: list[dict] | None) -> tuple[list[dict], list[str]]:
    """(documentos com texto, tipos de escopo ignorados) do recorte pedido.

    Sem escopo selecionado no chat, varre TODOS os documentos do inquérito com texto extraído. Com
    escopo, entram 'documento' (o id escolhido) e 'ipl' (o PDF de cada IPL escolhido); os demais
    tipos (aparelho, RIF, SIMBA...) não são documentos de texto livre — saem em `ignorados` para a
    ferramenta explicar à IA o que ficou de fora, em vez de calar.
    """
    query = """
        SELECT d.id_documento, d.no_arquivo, length(d.tx_conteudo) AS qt_caracteres
        FROM investigacao.inq_tb04_documentos d
        WHERE d.id_inquerito = %(id_inquerito)s
          AND d.tx_erro IS NULL
          AND coalesce(d.tx_conteudo, '') <> ''
    """
    parametros: dict = {"id_inquerito": id_inquerito}
    documentos_ids: list[int] = []
    ipl_ids: list[int] = []
    ignorados: list[str] = []
    for escopo in escopos or []:
        tp = escopo.get("tp_escopo")
        id_escopo = escopo.get("id_escopo")
        if tp == "documento" and id_escopo is not None:
            documentos_ids.append(int(id_escopo))
        elif tp == "ipl" and id_escopo is not None:
            ipl_ids.append(int(id_escopo))
        elif tp == "ipl":
            ipl_ids.append(None)  # IPL sem id = todos os IPLs do inquérito
        else:
            ignorados.append(tp or "?")

    if documentos_ids or ipl_ids:
        condicoes = []
        if documentos_ids:
            condicoes.append("d.id_documento = ANY(%(ids_documento)s::bigint[])")
            parametros["ids_documento"] = documentos_ids
        if ipl_ids:
            condicoes.append(
                """EXISTS (SELECT 1 FROM investigacao.ipl_tb01_inqueritos_pdf i
                           WHERE i.id_documento = d.id_documento
                             AND (%(todos_ipl)s OR i.id_ipl = ANY(%(ids_ipl)s::bigint[])))"""
            )
            parametros["todos_ipl"] = None in ipl_ids
            parametros["ids_ipl"] = [i for i in ipl_ids if i is not None]
        query += " AND (" + " OR ".join(condicoes) + ")"

    query += " ORDER BY d.id_documento"
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, parametros)
            linhas = cur.fetchall()
            colunas = [d.name for d in cur.description]
    documentos = [dict(zip(colunas, linha)) for linha in linhas]
    return documentos, sorted(set(ignorados))


def limpar_colunas(colunas: list) -> list[str]:
    """Colunas pedidas pela IA, saneadas: sem vazias, sem duplicatas (casefold), sem a 'Fonte'
    (que o job acrescenta sozinho — deixá-la nas mãos do modelo quebraria o dedup)."""
    limpas: list[str] = []
    vistas: set[str] = set()
    for c in colunas or []:
        c = str(c).strip()
        if not c or c.casefold() == COLUNA_FONTE.casefold():
            continue
        chave = c.casefold()
        if chave in vistas:
            continue
        vistas.add(chave)
        limpas.append(c)
    return limpas[:15]


def avaliar_consolidacao(id_inquerito: int, escopos: list[dict] | None) -> dict:
    """Pré-disparo: quantos documentos/lotes a varredura vai rodar e quanto tempo deve levar.

    É a resposta da ferramenta com avaliar=true — a IA usa os números para explicar o funcionamento
    e a expectativa de tempo ao investigador e pedir o 'sim' antes de disparar. Não
    roda IA nenhuma: é só contagem de texto no banco."""
    documentos, ignorados = _listar_documentos(id_inquerito, escopos)
    qt_lotes = sum(
        min(max(1, math.ceil(d["qt_caracteres"] / _LOTE_CHARS)), _MAX_LOTES_POR_DOCUMENTO)
        for d in documentos
    )
    minutos = int(math.ceil(qt_lotes * _SEGUNDOS_POR_LOTE / 60)) + _MINUTOS_CUSTO_FIXO
    resultado = {
        "qt_documentos": len(documentos),
        "qt_caracteres": sum(d["qt_caracteres"] for d in documentos),
        "qt_lotes_estimados": qt_lotes,
        "minutos_estimados": minutos,
        "ids_documentos": [d["id_documento"] for d in documentos],
        "documentos": [
            {"id_documento": d["id_documento"], "no_arquivo": d["no_arquivo"],
             "qt_caracteres": d["qt_caracteres"]}
            for d in documentos[:20]
        ],
    }
    if len(documentos) > 20:
        resultado["aviso_lista"] = (
            f"Listados os 20 primeiros de {len(documentos)} documentos."
        )
    if ignorados:
        resultado["aviso_escopos"] = (
            f"Os escopos selecionados {ignorados} não são documentos de texto e ficam fora da "
            "planilha; ela varre os documentos anexados e os PDFs de IPL do recorte."
        )
    return resultado


# =====================================================================
# Mapa: uma chamada à IA por lote de texto de documento
# =====================================================================

def _instrucao_mapa(pedido: str, colunas: list[str]) -> str:
    lista_colunas = ", ".join(colunas)
    exemplo = json.dumps([{c: "..." for c in colunas}], ensure_ascii=False)
    return (
        "Você está extraindo dados de um documento anexado a um inquérito policial, para alimentar "
        "uma planilha consolidada.\n"
        f"PROCURAR NO TEXTO: {pedido}\n\n"
        "Responda APENAS com um array JSON válido — sem prosa, sem markdown, sem bloco de código. "
        "Um objeto por ocorrência encontrada, com exatamente estas chaves: " + lista_colunas + ". "
        "Valores sempre em texto, transcritos como aparecem no documento (datas, números, nomes "
        "exatos). Se o mesmo fato aparecer repetido no documento, pode listar mais de uma vez. "
        "Não invente ocorrência: se nada no texto responde à procura, responda [].\n"
        f"Formato: {exemplo}"
    )


def _parse_json_linhas(texto: str) -> list[dict]:
    """Do texto do modelo ao array de objetos — tolerante a cercas de código e prosa em volta
    (modelo pequeno insiste em "claro!": fatia do primeiro '[' ao último ']')."""
    if not texto:
        return []
    inicio, fim = texto.find("["), texto.rfind("]")
    if inicio == -1 or fim <= inicio:
        return []
    try:
        dados = json.loads(texto[inicio:fim + 1])
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(dados, list):
        return []
    return [d for d in dados if isinstance(d, dict)]


def _normalizar_valor(valor) -> str:
    if valor is None:
        return ""
    if isinstance(valor, (list, dict)):
        valor = json.dumps(valor, ensure_ascii=False)
    return re.sub(r"\s+", " ", str(valor)).strip()[:_MAX_VALOR_LINHA]


def extrair_linhas_do_texto(pedido: str, colunas: list[str], conteudo: str,
                            id_usuario: int | None = None, anonimizador=None) -> list[dict]:
    """Uma ida à IA com um lote de texto do documento: devolve as ocorrências como dicts
    {coluna: valor} já normalizados (chaves fora das pedidas são descartadas)."""
    from funcoes.ia_provedores import enviar_mensagem

    resposta, _modelo = enviar_mensagem(
        [{"role": "user", "content": "DOCUMENTO:\n\n" + conteudo}],
        sistema=_instrucao_mapa(pedido, colunas),
        id_usuario=id_usuario,
        anonimizador=anonimizador,
        max_tokens_saida=_MAX_TOKENS_SAIDA_MAPA,
    )
    linhas = []
    for bruta in _parse_json_linhas(resposta):
        linha = {c: _normalizar_valor(bruta.get(c)) for c in colunas}
        if any(linha.values()):  # ocorrência toda vazia é ruído do modelo
            linhas.append(linha)
    return linhas


def _mesclar_linhas(linhas: list[dict], colunas: list[str]) -> list[dict]:
    """Dedup determinístico: mesma combinação de valores (ignorada a fonte) é a mesma linha, e as
    fontes se acumulam na coluna Fonte. Sem IA no reduce — fundir é contabilidade, e o modelo
    reescreveria nome/valor a caminho."""
    ordem: list[tuple] = []
    vistas: dict[tuple, dict] = {}
    for linha in linhas:
        chave = tuple((linha.get(c) or "").casefold() for c in colunas)
        if chave in vistas:
            fontes = vistas[chave]["fontes"]
            if linha.get(COLUNA_FONTE) and linha[COLUNA_FONTE] not in fontes:
                fontes.append(linha[COLUNA_FONTE])
            continue
        entrada = {c: linha.get(c) or "" for c in colunas}
        entrada["fontes"] = [linha[COLUNA_FONTE]] if linha.get(COLUNA_FONTE) else []
        vistas[chave] = entrada
        ordem.append(chave)
    saida = []
    for chave in ordem:
        entrada = vistas[chave]
        entrada[COLUNA_FONTE] = "; ".join(entrada.pop("fontes"))
        saida.append(entrada)
    return saida


# =====================================================================
# .xlsx
# =====================================================================

_RE_NOME_INVALIDO = re.compile(r"[^0-9A-Za-z._-]+")


def _sem_acento(texto: str) -> str:
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")


def _nome_arquivo(pedido: str) -> str:
    base = _RE_NOME_INVALIDO.sub("_", _sem_acento(pedido or "").strip()).strip("_")[:50]
    return f"planilha_ia_{base or 'consolidado'}_{datetime.now():%Y%m%d_%H%M%S}.xlsx"


def montar_xlsx(caminho: Path, colunas: list[str], linhas: list[dict]) -> Path:
    """Cabeçalho + linhas, com a coluna Fonte sempre por último."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter

    cabecalhos = [*colunas, COLUNA_FONTE]
    wb = Workbook()
    aba = wb.active
    aba.title = "Consolidado"
    preenchimento = PatternFill("solid", fgColor="EFEFEF")
    for i, titulo in enumerate(cabecalhos, start=1):
        celula = aba.cell(row=1, column=i, value=titulo[:100])
        celula.font = Font(bold=True)
        celula.fill = preenchimento
    for n, linha in enumerate(linhas, start=2):
        for i, titulo in enumerate(cabecalhos, start=1):
            valor = str(linha.get(titulo) or "")[:_MAX_CELULA]
            aba.cell(row=n, column=i, value=valor)
    for i, titulo in enumerate(cabecalhos, start=1):
        maior = max([len(titulo)] + [len(str(linha.get(titulo) or "")) for linha in linhas[:500]])
        aba.column_dimensions[get_column_letter(i)].width = min(60, max(12, maior + 2))
    aba.freeze_panes = "A2"
    aba.auto_filter.ref = f"A1:{get_column_letter(len(cabecalhos))}{max(1, len(linhas) + 1)}"
    caminho.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(caminho))
    return caminho


# =====================================================================
# Persistência (IA_TB07_PLANILHAS) — espelho de ia_relatorio_docx
# =====================================================================

def _registrar(id_conversa_ia: int | None, id_inquerito: int | None, id_usuario: int | None,
               nome: str, caminho: Path, qt_linhas: int) -> int:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO investigacao.ia_tb07_planilhas
                    (id_conversa_ia, id_inquerito, id_usuario, no_arquivo, caminho_arquivo, qt_linhas)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id_planilha_ia
                """,
                (id_conversa_ia, id_inquerito, id_usuario, nome, str(caminho), qt_linhas),
            )
            id_planilha = cur.fetchone()[0]
        conn.commit()
    return id_planilha


def obter_planilha_ia(id_planilha_ia: int) -> dict | None:
    """Registro de um .xlsx gerado (None se não existir) — usado pela rota de download."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id_planilha_ia, id_conversa_ia, id_inquerito, id_usuario,
                       no_arquivo, caminho_arquivo, qt_linhas, dt_criacao
                FROM investigacao.ia_tb07_planilhas
                WHERE id_planilha_ia = %s
                """,
                (id_planilha_ia,),
            )
            linha = cur.fetchone()
            if not linha:
                return None
            return dict(zip([d.name for d in cur.description], linha))


def _limpar_planilhas_antigas() -> None:
    marco = datetime.now() - timedelta(days=_DIAS_MANTER_ARQUIVO)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id_planilha_ia, caminho_arquivo "
                "FROM investigacao.ia_tb07_planilhas WHERE dt_criacao < %s",
                (marco,),
            )
            antigos = cur.fetchall()
            if not antigos:
                return
            for _id, caminho in antigos:
                try:
                    Path(caminho).unlink(missing_ok=True)
                except OSError:
                    pass
            cur.execute(
                "DELETE FROM investigacao.ia_tb07_planilhas WHERE dt_criacao < %s",
                (marco,),
            )
        conn.commit()


def _apagar_planilhas_onde(cur, coluna: str, valor: int) -> None:
    cur.execute(
        f"SELECT caminho_arquivo FROM investigacao.ia_tb07_planilhas WHERE {coluna} = %s",
        (valor,),
    )
    for (caminho,) in cur.fetchall():
        try:
            Path(caminho).unlink(missing_ok=True)
        except OSError:
            pass
    cur.execute(f"DELETE FROM investigacao.ia_tb07_planilhas WHERE {coluna} = %s", (valor,))


def apagar_planilhas_da_conversa(cur, id_conversa_ia: int) -> None:
    _apagar_planilhas_onde(cur, "id_conversa_ia", id_conversa_ia)


def apagar_planilhas_de_inquerito(cur, id_inquerito: int) -> None:
    _apagar_planilhas_onde(cur, "id_inquerito", id_inquerito)


# =====================================================================
# Worker do job (roda em thread daemon, disparado pela ferramenta)
# =====================================================================

def consolidar_em_planilha(id_job: int) -> None:
    """Ponto de entrada da thread: nunca levanta — a falha morre em TX_ERRO do job, e é o cartão
    na conversa que mostra o fim ao investigador."""
    try:
        _rodar(id_job)
    except Exception as exc:
        logger.exception("Job de consolidação %s falhou", id_job)
        try:
            from funcoes import jobs_utils
            jobs_utils.falhar(id_job, str(exc)[:2000])
        except Exception:
            logger.exception("Falha ao gravar erro do job de consolidação %s", id_job)


def _rodar(id_job: int) -> None:
    from funcoes import jobs_utils
    from funcoes.ia_provedores import ErroProvedorIA

    job = jobs_utils.obter(id_job)
    if job is None or not job.get("rodando"):
        return
    parametros = job.get("tx_parametros") or {}
    id_inquerito = job.get("id_inquerito")
    pedido = parametros.get("pedido") or ""
    colunas = limpar_colunas(parametros.get("colunas") or [])
    ids_documentos = [int(i) for i in (parametros.get("ids_documentos") or [])]
    id_usuario = job.get("id_usuario")
    if not colunas:
        jobs_utils.falhar(id_job, "O job foi criado sem colunas — refaça o pedido no chat.")
        return

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT d.id_documento, d.no_arquivo, d.tx_conteudo
                FROM investigacao.inq_tb04_documentos d
                WHERE d.id_documento = ANY(%s::bigint[]) AND d.id_inquerito = %s
                  AND d.tx_erro IS NULL AND coalesce(d.tx_conteudo, '') <> ''
                ORDER BY d.id_documento
                """,
                (ids_documentos, id_inquerito),
            )
            documentos = [dict(zip([c.name for c in cur.description], linha))
                          for linha in cur.fetchall()]
    if not documentos:
        jobs_utils.falhar(id_job, "Os documentos deste pedido sumiram do inquérito.")
        return

    jobs_utils.marcar_processando(id_job)
    if jobs_utils.progresso(id_job, total=len(documentos), processados=0):
        jobs_utils.marcar_cancelado(id_job)
        return

    anonimizador = None
    try:
        from funcoes.anonimizacao import anonimizador_para_inquerito, anonimizacao_ativa
        if anonimizacao_ativa("AI"):
            anonimizador = anonimizador_para_inquerito(id_inquerito)
    except Exception:
        logger.exception("Consolidação %s: anonimizador indisponível, segue sem anonimizar", id_job)

    linhas: list[dict] = []
    erros = 0
    qt_lotes = 0
    truncados: list[str] = []
    cancelado = False
    for indice, doc in enumerate(documentos):
        conteudo = doc["tx_conteudo"] or ""
        lotes = [conteudo[i:i + _LOTE_CHARS] for i in range(0, len(conteudo), _LOTE_CHARS)]
        if len(lotes) > _MAX_LOTES_POR_DOCUMENTO:
            truncados.append(doc["no_arquivo"])
            lotes = lotes[:_MAX_LOTES_POR_DOCUMENTO]
        try:
            for lote in lotes:
                qt_lotes += 1
                for linha in extrair_linhas_do_texto(
                    pedido, colunas, lote, id_usuario=id_usuario, anonimizador=anonimizador
                ):
                    linha[COLUNA_FONTE] = doc["no_arquivo"]
                    linhas.append(linha)
                # batimento por lote: um documento longo leva dezenas de chamadas à IA e o watchdog
                # (liberar_jobs_travados, 5 min sem bater) não pode confundi-lo com processo morto
                if jobs_utils.bater(id_job):
                    raise _CanceladoInterno()
        except _CanceladoInterno:
            cancelado = True
        except ErroProvedorIA:
            raise  # IA mal configurada derruba o job inteiro — continuar só acumularia falha
        except Exception:
            erros += 1
            logger.exception("Consolidação %s: documento %s falhou, seguindo", id_job,
                             doc["id_documento"])
        if jobs_utils.progresso(id_job, processados=indice + 1, erros=erros,
                                metricas={"qt_lotes_ia": qt_lotes}) or cancelado:
            jobs_utils.marcar_cancelado(id_job)
            return

    consolidadas = _mesclar_linhas(linhas, colunas)
    nome = _nome_arquivo(pedido)
    caminho = montar_xlsx(PASTA_PLANILHAS_IA / nome, colunas, consolidadas)
    _limpar_planilhas_antigas()
    id_planilha = _registrar(parametros.get("id_conversa_ia"), id_inquerito, id_usuario,
                             nome, caminho, len(consolidadas))
    resultado = {
        "id_planilha": id_planilha,
        "no_arquivo": nome,
        "qt_linhas": len(consolidadas),
        "qt_documentos": len(documentos),
        "link": f"{ROTA_DOWNLOAD}/{id_planilha}/download",
    }
    if truncados:
        resultado["aviso"] = (
            f"Documentos maiores que o limite de varredura tiveram apenas o início lido: "
            f"{', '.join(truncados[:5])}{'...' if len(truncados) > 5 else ''}."
        )
    jobs_utils.concluir(id_job, resultado=resultado,
                        metricas={"qt_lotes_ia": qt_lotes, "qt_linhas_brutas": len(linhas)})


def disparar(id_job: int) -> None:
    """Thread daemon nos moldes dos outros jobs: a rota/ferramenta cria o job e responde na hora."""
    threading.Thread(target=consolidar_em_planilha, args=(id_job,), daemon=True).start()

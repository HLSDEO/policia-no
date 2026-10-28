"""Estado dos jobs de segundo plano em tabela, escopado por inquérito e por dono.

Antes disto, OCR / classificação de imagem / comparação facial guardavam progresso e cancelamento
em dicionário de módulo. Estado de processo tem três defeitos que só aparecem com mais de um
analista usando o sistema ao mesmo tempo:

1. o guard "já existe um em andamento" valia para o servidor inteiro, então o segundo analista —
   de outra delegacia, em outro inquérito — levava 409;
2. `solicitar_cancelamento()` não recebia parâmetro nenhum e parava o job de quem estivesse
   rodando, fosse quem fosse;
3. o progresso da comparação facial carregava a lista de rostos parecidos, que o GET de progresso
   devolvia a qualquer usuário aprovado.

E nada disso sobrevive a `--workers > 1`: o POST cai num processo e o GET de progresso em outro,
que não tem a entrada no dicionário.

O padrão aqui é o que o projeto já usa em INQ_TB02_EXTRACOES, EMB_TB02_INDEXACOES e
IA_TB03_PROCESSAMENTOS: linha de controle no banco, guard por índice único parcial, cancelamento
por FL_CANCELAR e detecção de órfão por DT_ULTIMA_ATIVIDADE. Ver db/init/41_jobs.sql.
"""

import logging
import os
import socket

import psycopg
from psycopg.types.json import Jsonb

from funcoes.db_utils import get_connection

logger = logging.getLogger(__name__)

TP_OCR = "ocr_imagem"
# Seleção parcial de imagens pedida pela IA no chat (ferramenta processar_texto_em_imagens).
# Tipo separado de TP_OCR de propósito: o guard único por (tipo, inquérito) não deixa uma seleção
# da IA colidir com o OCR completo que um analista está rodando na tela Arquivos.
TP_OCR_SELECAO = "ocr_imagem_selecao"
TP_CLASSIFICACAO = "classificacao_imagem"
TP_COMPARACAO_FACIAL = "comparacao_facial"
TP_IMPORT_REFERENCIA = "import_referencia"
TP_OLLAMA_PULL = "ollama_pull"
TP_COPIA_REDE = "copia_rede"
TP_IMPORT_ZIP = "import_zip"
# Documento avulso PESADO (PDF de perícia com centenas de páginas): a extração de texto sai da
# requisição e entra na fila, no mesmo espírito do ZIP — acima do teto síncrono
# (DOC_IMPORTE_SINCRONO_MB) ela estouraria o proxy_read_timeout de 300 s do nginx e o usuário
# veria só "falha ao extrair". Tipo separado do ZIP para o guard deixar os dois rodando juntos.
TP_DOC_IMPORT = "import_documento"
# Consolidação pesada pedida no chat de IA (ferramenta gerar_planilha_consolidada): varre muitos
# documentos com chamadas em lote à IA (map-reduce) e monta um .xlsx — minutos, não segundos, por
# isso vira job em background em vez de turno de chat. Não é 'pesado' de CPU: disputa cota de IA,
# não vaga da fila de OCR/classificação.
TP_CONSOLIDACAO = "consolidacao_ia"
# "Extrair temporariamente" da galeria (funcoes/midia_temp.py): cópia dos vídeos/imagens de uma
# extração para data/_midia_temp + thumbs por ffmpeg. Não é 'pesado': é I/O de cópia e um
# -frames:v 1 por vídeo, não disputa a vaga de CLIP/Tesseract.
TP_MIDIA_TEMP = "midia_temporaria"
# "Atualizar todos os nós" da tela Administração → Servidores: o rollout em levas (ver
# docs/no_descentralizado.md, "rollout de update") — job global (sem inquérito, escopo "nos")
# para herdar guard único, progresso, histórico e cancelamento da mesma tabela. Não é 'pesado':
# disputa nada de CPU, só empurra o gatilho /no/atualizar e acompanha os nós voltarem.
TP_ROLLOUT_NOS = "rollout_nos"

# Rótulo legível de cada tipo, para as mensagens de erro e para a tela de administração.
ROTULOS = {
    TP_OCR: "OCR",
    TP_OCR_SELECAO: "OCR de imagens selecionadas",
    TP_CLASSIFICACAO: "classificação de imagem",
    TP_COMPARACAO_FACIAL: "comparação facial",
    TP_IMPORT_REFERENCIA: "importação de base de referência",
    TP_OLLAMA_PULL: "download de modelo",
    TP_COPIA_REDE: "cópia de pasta de rede",
    TP_IMPORT_ZIP: "importação de ZIP",
    TP_DOC_IMPORT: "importação de documento",
    TP_CONSOLIDACAO: "consolidação de planilha pela IA",
    TP_MIDIA_TEMP: "extração temporária de mídia",
    TP_ROLLOUT_NOS: "atualização dos nós em levas",
}

ATIVOS = ("pendente", "aguardando_vaga", "processando")

# Job saudável bate DT_ULTIMA_ATIVIDADE a cada lote (segundos). Sem batimento por
# MINUTOS_JOB_STALE o reaper considera travado — 5 min era curto para lote de OCR remoto lento
# ou cold-load do CLIP, e ceifava job vivo (ver C2): o worker ceifado continuava escrevendo
# progresso e ressuscitava a linha para 'concluido', além de liberar o guard único e admitir
# job duplicado. Piso de 5 para ninguém configurar um valor que ceife lote normal.
def _minutos_job_stale() -> int:
    try:
        return max(5, int((os.environ.get("MINUTOS_JOB_STALE") or "").strip() or 30))
    except ValueError:
        return 30


MINUTOS_JOB_TRAVADO = 30  # compat: preferir _minutos_job_stale(), que lê o .env

# Identificação do processo que está tocando o job. Só diagnóstico: quem decide se um job está
# órfão é o batimento, não isto — ver liberar_jobs_travados.
_NO_WORKER = f"{socket.gethostname()}:{os.getpid()}"[:100]


class JobOcupado(RuntimeError):
    """Já existe um job deste tipo ativo neste inquérito. As rotas traduzem em 409."""


class JobCancelado(RuntimeError):
    """O usuário pediu para parar enquanto o job esperava vaga."""


_COLUNAS = """id_job, tp_job, id_inquerito, id_extracao, no_escopo, id_usuario, cd_status,
              qt_total, qt_processados, qt_erros, fl_cancelar, fl_pesado, tx_erro,
              tx_parametros, tx_metricas, tx_resultado, no_worker,
              dt_inicio, dt_ultima_atividade, dt_fim"""


def _linha(cur) -> dict | None:
    row = cur.fetchone()
    if row is None:
        return None
    return _formatar(dict(zip([d.name for d in cur.description], row)))


def _formatar(job: dict) -> dict:
    """Acrescenta os campos derivados que o front já lê hoje (`rodando`, `cancelado`).

    Manter esses dois nomes é de propósito: o JS de arquivos.js e comparar_rosto.js os consulta
    desde antes desta tabela existir, e assim o diff do front fica em "de onde vem o objeto", não
    em "como interpretá-lo"."""
    job["rodando"] = job["cd_status"] in ATIVOS
    job["cancelado"] = job["cd_status"] == "cancelado" or bool(job.get("fl_cancelar"))
    job["rotulo"] = ROTULOS.get(job["tp_job"], job["tp_job"])
    return job


# ------------------------------------------------------------------ ciclo de vida

def criar(tp_job: str, id_usuario: int, *, id_inquerito: int | None = None,
          id_extracao: int | None = None, escopo: str | None = None,
          parametros: dict | None = None, pesado: bool = True,
          id_servidor: int | None = None) -> int:
    """Abre um job e devolve o id. Levanta JobOcupado se já houver um ativo do mesmo tipo neste
    inquérito (ou no mesmo escopo, para job global).

    `id_servidor` (jobs no nó — docs/no_descentralizado.md): o job NASCE marcado para o nó X e o
    chamador NÃO sobe thread local — ele fica 'pendente' até o próprio nó reivindicar
    (jobs_no.claim). Um job de nó nunca disputa a vaga de CPU do central, então quem roteia passa
    `pesado=False` junto (a contagem de pesados é só para o que roda aqui).

    Libera os travados ANTES de tentar: um job cujo processo morreu segura o índice único, e sem
    isto o analista levaria 409 para sempre, sem ter como sair disso pela tela. Mesma ordem de
    apagar_extracao, que chama liberar_extracoes_travadas antes de conferir o status.

    O guard é o banco (UX100_JOB_TB01_ATIVO), não um SELECT antes do INSERT: dois cliques
    simultâneos passariam os dois pelo SELECT."""
    liberar_jobs_travados()
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO investigacao.job_tb01_jobs
                        (tp_job, id_inquerito, id_extracao, no_escopo, id_usuario,
                         cd_status, fl_pesado, tx_parametros, no_worker, id_servidor)
                    VALUES (%s, %s, %s, %s, %s, 'pendente', %s, %s, %s, %s)
                    RETURNING id_job
                    """,
                    (tp_job, id_inquerito, id_extracao, escopo, id_usuario, pesado,
                     Jsonb(parametros) if parametros is not None else None, _NO_WORKER,
                     id_servidor),
                )
                id_job = cur.fetchone()[0]
            conn.commit()
        return id_job
    except psycopg.errors.UniqueViolation:
        raise JobOcupado(_mensagem_ocupado(tp_job, id_inquerito, escopo))


def _mensagem_ocupado(tp_job: str, id_inquerito: int | None, escopo: str | None) -> str:
    """Diz QUEM está com o job e desde quando — "já existe um em andamento" sozinho não ajuda
    ninguém a decidir se espera ou se procura o colega."""
    rotulo = ROTULOS.get(tp_job, tp_job)
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT u.no_usuario, j.dt_inicio
                    FROM investigacao.job_tb01_jobs j
                    LEFT JOIN seguranca.usr_tb01_usuarios u ON u.id_usuario = j.id_usuario
                    WHERE j.tp_job = %s
                      AND coalesce(j.id_inquerito, 0) = coalesce(%s::bigint, 0)
                      AND coalesce(j.no_escopo, '') = coalesce(%s, '')
                      AND j.cd_status IN ('pendente', 'aguardando_vaga', 'processando')
                    """,
                    (tp_job, id_inquerito, escopo),
                )
                row = cur.fetchone()
        if row:
            quem = row[0] or "outro usuário"
            quando = row[1].strftime("%d/%m às %H:%M") if row[1] else "há pouco"
            return f"Já existe {rotulo} em andamento aqui, iniciado por {quem} em {quando}."
    except Exception:
        logger.exception("Falha ao montar a mensagem de job ocupado (best-effort)")
    return f"Já existe {rotulo} em andamento aqui."


def concluir(id_job: int, *, resultado=None, metricas: dict | None = None) -> None:
    _fechar(id_job, "concluido", resultado=resultado, metricas=metricas)


def falhar(id_job: int, erro: str) -> None:
    _fechar(id_job, "erro", erro=erro)


def marcar_cancelado(id_job: int) -> None:
    _fechar(id_job, "cancelado")


# Teto do que cabe em TX_RESULTADO. A comparação facial com limite=100 dá poucos KB; o teto existe
# para uma mudança futura não transformar um resultado grande num INSERT que estoura.
_MAX_RESULTADO_BYTES = 256 * 1024


def _limpar_referencia(id_job: int) -> None:
    """A foto de referência de uma comparação facial ROTEADA PARA O NÓ fica em
    data/.jobs_no/<id_job>.ref até o claim lê-la (jobs_no.guardar_referencia). Esteja o job no
    central (sem arquivo, o bytes nunca saiu da memória) ou no nó, fechar o job apaga o resto —
    a prova de referência não se acumina na estante."""
    try:
        from funcoes.organizacao_arquivos import PASTA_DADOS

        (PASTA_DADOS / ".jobs_no" / f"{id_job}.ref").unlink(missing_ok=True)
    except OSError:
        pass


def _fechar(id_job: int, status: str, *, resultado=None, metricas: dict | None = None,
            erro: str | None = None) -> None:
    aviso = None
    if resultado is not None:
        import json
        bruto = json.dumps(resultado, ensure_ascii=False, default=str)
        if len(bruto.encode("utf-8")) > _MAX_RESULTADO_BYTES:
            aviso = (f"Resultado grande demais para guardar ({len(bruto) // 1024} KB); "
                     "o job terminou, mas o detalhamento não foi salvo.")
            resultado = None
    with get_connection() as conn:
        with conn.cursor() as cur:
            # Guarda de status: um worker que foi ceifado pelo reaper (ou teve o job liberado/
            # cancelado por um admin) não ressuscita a linha para 'concluido' — o UPDATE só
            # acontece se o job ainda estiver ativo. Ver C2/M5.
            cur.execute(
                """
                UPDATE investigacao.job_tb01_jobs
                SET cd_status = %s,
                    dt_fim = now(),
                    dt_ultima_atividade = now(),
                    tx_erro = coalesce(%s, tx_erro),
                    tx_resultado = coalesce(%s, tx_resultado),
                    tx_metricas = tx_metricas || coalesce(%s, '{}'::jsonb)
                WHERE id_job = %s
                  AND cd_status IN ('pendente', 'aguardando_vaga', 'processando')
                """,
                 (status, erro or aviso,
                  Jsonb(resultado) if resultado is not None else None,
                  Jsonb(metricas) if metricas is not None else None,
                  id_job),
            )
        conn.commit()
    _limpar_referencia(id_job)


# ------------------------------------------------------------------ durante a execução

def progresso(id_job: int, *, processados: int | None = None, total: int | None = None,
              erros: int | None = None, metricas: dict | None = None) -> bool:
    """Grava progresso, bate DT_ULTIMA_ATIVIDADE e devolve FL_CANCELAR — tudo numa ida ao banco.

    É o que substitui, de uma vez, o par _PROGRESS/_CANCELAR dos três módulos: o laço grava o
    quanto andou e recebe de volta a resposta de "devo parar?", em vez de ler um global.

    Guarda de status como em _fechar: se o reaper ceifou o job no meio do lote, o UPDATE não
    acha linha ativa e devolvemos cancelado=True — é o sinal para a thread encerrar em vez de
    continuar queimando CPU/IA num job que já virou 'erro' na tela. Ver C2/M5."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE investigacao.job_tb01_jobs
                SET qt_processados = coalesce(%s, qt_processados),
                    qt_total = coalesce(%s, qt_total),
                    qt_erros = coalesce(%s, qt_erros),
                    tx_metricas = tx_metricas || coalesce(%s, '{}'::jsonb),
                    dt_ultima_atividade = now()
                WHERE id_job = %s
                  AND cd_status IN ('pendente', 'aguardando_vaga', 'processando')
                RETURNING fl_cancelar
                """,
                (processados, total, erros,
                 Jsonb(metricas) if metricas is not None else None, id_job),
            )
            row = cur.fetchone()
        conn.commit()
    if row is None:
        return True
    return bool(row[0])


def bater(id_job: int) -> bool:
    """Só o batimento, sem mexer em contador. Devolve FL_CANCELAR."""
    return progresso(id_job)


def marcar_processando(id_job: int) -> None:
    _mudar_status(id_job, "processando")


def marcar_aguardando(id_job: int) -> None:
    _mudar_status(id_job, "aguardando_vaga")


def _mudar_status(id_job: int, status: str) -> None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE investigacao.job_tb01_jobs "
                "SET cd_status = %s, no_worker = %s, dt_ultima_atividade = now() "
                "WHERE id_job = %s AND cd_status IN ('pendente', 'aguardando_vaga', 'processando')",
                (status, _NO_WORKER, id_job),
            )
        conn.commit()


# ------------------------------------------------------------------ leitura

def obter(id_job: int) -> dict | None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {_COLUNAS} FROM investigacao.job_tb01_jobs WHERE id_job = %s",
                (id_job,),
            )
            return _linha(cur)


def ultimo(tp_job: str, *, id_inquerito: int | None = None, escopo: str | None = None) -> dict | None:
    """O job mais recente deste tipo neste inquérito/escopo — em andamento ou já terminado.

    É o que a tela consulta ao abrir: serve tanto para retomar o acompanhamento de um job que
    ficou rodando quanto para mostrar o resultado do que acabou de terminar."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT {_COLUNAS} FROM investigacao.job_tb01_jobs
                WHERE tp_job = %s
                  AND coalesce(id_inquerito, 0) = coalesce(%s::bigint, 0)
                  AND coalesce(no_escopo, '') = coalesce(%s, '')
                ORDER BY id_job DESC LIMIT 1
                """,
                (tp_job, id_inquerito, escopo),
            )
            return _linha(cur)


def listar(id_inquerito: int | None = None, *, so_ativos: bool = True,
           limite: int = 50) -> list[dict]:
    condicoes = []
    parametros: list = []
    if id_inquerito is not None:
        condicoes.append("id_inquerito = %s")
        parametros.append(id_inquerito)
    if so_ativos:
        condicoes.append("cd_status IN ('pendente', 'aguardando_vaga', 'processando')")
    onde = ("WHERE " + " AND ".join(condicoes)) if condicoes else ""
    parametros.append(max(1, min(500, limite)))
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {_COLUNAS} FROM investigacao.job_tb01_jobs {onde} "
                f"ORDER BY id_job DESC LIMIT %s",
                tuple(parametros),
            )
            colunas = [d.name for d in cur.description]
            return [_formatar(dict(zip(colunas, row))) for row in cur.fetchall()]


def listar_por_usuario(tp_job: str, id_usuario: int, *, so_ativos: bool = True,
                       limite: int = 20) -> list[dict]:
    """Jobs deste tipo pertencentes ao usuário, do mais recente ao mais antigo.

    É o que alimenta o painel "Cópias" da importação: a cópia acontece antes de existir
    inquérito (destino é a raiz da delegacia), então o escopo aqui é o dono, não o
    inquérito — mesma visibilidade de `_job_visivel` para job global (dono ou admin).
    Usa IX254_JOB_TB01_USUARIO.
    """
    condicao = "AND cd_status IN ('pendente', 'aguardando_vaga', 'processando')" if so_ativos else ""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {_COLUNAS} FROM investigacao.job_tb01_jobs "
                f"WHERE tp_job = %s AND id_usuario = %s {condicao} "
                f"ORDER BY id_job DESC LIMIT %s",
                (tp_job, id_usuario, max(1, min(100, limite))),
            )
            colunas = [d.name for d in cur.description]
            return [_formatar(dict(zip(colunas, row))) for row in cur.fetchall()]


def posicao_na_fila(id_job: int) -> int:
    """Quantos jobs pesados estão à frente deste na fila. É o que deixa a tela dizer
    "na fila, 2 à frente" em vez de mostrar 0% congelado."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT count(*) FROM investigacao.job_tb01_jobs
                WHERE fl_pesado
                  AND cd_status IN ('pendente', 'aguardando_vaga')
                  AND id_job < %s
                """,
                (id_job,),
            )
            return int(cur.fetchone()[0])


# Colunas da listagem de administração: as mesmas de _COLUNAS menos tx_resultado — na tela de
# filas ninguém precisa da lista inteira de rostos parecidos de cada job, e ela é o maior campo.
_COLUNAS_ADMIN = """j.id_job, j.tp_job, j.id_inquerito, j.id_extracao, j.no_escopo, j.id_usuario,
                    j.cd_status, j.qt_total, j.qt_processados, j.qt_erros, j.fl_cancelar,
                    j.fl_pesado, j.tx_erro, j.tx_parametros, j.tx_metricas, j.no_worker,
                    j.dt_inicio, j.dt_ultima_atividade, j.dt_fim"""


def listar_admin(*, tp_job: str | None = None, status: list[str] | None = None,
                 busca: str | None = None, limite: int = 200) -> list[dict]:
    """Jobs para a tela de administração: cada um já com o número do inquérito, o nome do dono e
    a posição na fila — o /api/admin/jobs antigo devolvia a linha crua, e a tela teria que
    mostrar ID numérico onde o admin quer ler "Inquérito 2025.0079407 · João".

    `status` filtra por cd_status (vazio = todos, inclusive o histórico). `busca` casa com número
    do inquérito, usuário, escopo ou tipo — é o campo "achar o job do analista X"."""
    condicoes: list[str] = []
    parametros: list = []
    if tp_job:
        condicoes.append("j.tp_job = %s")
        parametros.append(tp_job)
    if status:
        condicoes.append("j.cd_status IN (" + ", ".join(["%s"] * len(status)) + ")")
        parametros.extend(status)
    if busca:
        condicoes.append(
            "(i.nu_inquerito::text ILIKE %s OR u.no_usuario ILIKE %s "
            "OR j.no_escopo ILIKE %s OR j.tp_job ILIKE %s)"
        )
        like = f"%{busca}%"
        parametros.extend([like, like, like, like])
    onde = ("WHERE " + " AND ".join(condicoes)) if condicoes else ""
    parametros.append(max(1, min(500, limite)))
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT {_COLUNAS_ADMIN},
                       i.nu_inquerito, u.no_usuario,
                       -- posição na fila de pesados, a mesma conta de posicao_na_fila, embutida
                       -- para a tabela inteira não custar N+1 consultas
                       CASE WHEN j.fl_pesado
                                 AND j.cd_status IN ('pendente', 'aguardando_vaga')
                            THEN (SELECT count(*) FROM investigacao.job_tb01_jobs f
                                   WHERE f.fl_pesado
                                     AND f.cd_status IN ('pendente', 'aguardando_vaga')
                                     AND f.id_job < j.id_job)
                            ELSE 0 END AS qt_na_frente
                FROM investigacao.job_tb01_jobs j
                LEFT JOIN investigacao.inq_tb01_inqueritos i ON i.id_inquerito = j.id_inquerito
                LEFT JOIN seguranca.usr_tb01_usuarios u ON u.id_usuario = j.id_usuario
                {onde}
                ORDER BY j.id_job DESC
                LIMIT %s
                """,
                tuple(parametros),
            )
            colunas = [d.name for d in cur.description]
            return [_formatar(dict(zip(colunas, row))) for row in cur.fetchall()]


def contagem_status() -> dict:
    """Quantos jobs há em cada status, no servidor inteiro — os KPIs da tela de administração
    precisam do total global, não do pedaço que o filtro atual mostrou."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT cd_status, count(*) FROM investigacao.job_tb01_jobs GROUP BY cd_status"
            )
            return {status: int(n) for status, n in cur.fetchall()}


# ------------------------------------------------------------------ controle

def pode_controlar(job: dict, usuario: dict) -> bool:
    """Quem pode cancelar/liberar: o dono do job, um admin global, ou um gestor do inquérito.

    Import tardio de investigacao_utils para não criar ciclo (aquele módulo é o que vai chamar
    este)."""
    if usuario.get("in_admin"):
        return True
    if job.get("id_usuario") == usuario.get("id_usuario"):
        return True
    if job.get("id_inquerito") is None:
        return False
    from funcoes.investigacao_utils import eh_gestor_inquerito

    return eh_gestor_inquerito(job["id_inquerito"], usuario)


def solicitar_cancelamento(id_job: int) -> bool:
    """Liga FL_CANCELAR. False se o job já terminou. Quem confere a permissão é a rota, por
    pode_controlar — este módulo não conhece sessão."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE investigacao.job_tb01_jobs
                SET fl_cancelar = true, dt_ultima_atividade = now()
                WHERE id_job = %s AND cd_status IN ('pendente', 'aguardando_vaga', 'processando')
                RETURNING id_job
                """,
                (id_job,),
            )
            ok = cur.fetchone() is not None
        conn.commit()
    return ok


def liberar(id_job: int) -> bool:
    """O antigo forcar_reset_progresso, agora com alvo: marca o job como erro para destravar a
    tela quando o processo que o tocava morreu de um jeito que o batimento não pega."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE investigacao.job_tb01_jobs
                SET cd_status = 'erro', dt_fim = now(), dt_ultima_atividade = now(),
                    tx_erro = coalesce(tx_erro, 'Job liberado manualmente.')
                WHERE id_job = %s AND cd_status IN ('pendente', 'aguardando_vaga', 'processando')
                RETURNING id_job
                """,
                (id_job,),
            )
            ok = cur.fetchone() is not None
        conn.commit()
    if ok:
        _limpar_referencia(id_job)
    return ok


def liberar_jobs_travados() -> int:
    """Fecha os jobs ativos que pararam de bater há mais de MINUTOS_JOB_STALE.

    Irmão de liberar_extracoes_travadas, e com o mesmo cuidado: o predicado é PURAMENTE TEMPORAL.
    Com mais de um worker do uvicorn não há como saber quais jobs são de um processo morto e
    quais são de um processo vivo, e um "libera tudo no startup" mataria o trabalho de um colega
    que está rodando neste instante. Por isso o limiar é folgado (30 min) e os laços batem a cada
    lote — e, se mesmo assim um vivo for ceifado, as guardas de progresso/_fechar impedem a
    ressurreição da linha (ver C2/M5)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE investigacao.job_tb01_jobs
                SET cd_status = CASE WHEN fl_cancelar THEN 'cancelado' ELSE 'erro' END,
                    dt_fim = now(),
                    tx_erro = coalesce(tx_erro,
                        'O processamento parou de responder e foi encerrado automaticamente.')
                WHERE cd_status IN ('pendente', 'aguardando_vaga', 'processando')
                  AND dt_ultima_atividade < now() - (%s || ' minutes')::interval
                RETURNING id_job
                """,
                (_minutos_job_stale(),),
            )
            liberados = cur.fetchall()
        conn.commit()
    if liberados:
        logger.info("Jobs sem batimento liberados: %s", ", ".join(str(r[0]) for r in liberados))
        for (id_job,) in liberados:
            _limpar_referencia(id_job)
    return len(liberados)

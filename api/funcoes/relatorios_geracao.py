"""Geração do relatório em .docx como job em segundo plano.

A montagem em si continua em relatorios_docx.gerar_relatorio_docx; aqui está só o invólucro que
tira o trabalho de dentro da requisição HTTP. O motivo é o mesmo do SIMBA e do chat de IA: o nginx
corta a requisição em 60 s, e um caso grande — sobretudo com as opções de IA ("Resumo das conversas
por IA", uma chamada ao provedor por marcador de cada conversa, e "Reescrever comentários do
analista por IA", uma por comentário) — passa disso com folga. O usuário via erro num relatório que
ia terminar bem.

Desenho copiado de funcoes/embed_utils.py (indexação semântica), que é o job mais próximo deste:
linha de status em REL_TB04_GERACOES, trava por índice único parcial, watchdog de job órfão,
progresso escrito em conexão própria e falha que nunca sobe — vira cd_status='erro' + tx_erro.

O .docx fica em PASTA_INVESTIGACAO/relatorios e é servido depois por FileResponse (o precedente é
IA_TB04_ARQUIVOS_PROMPT, no chat de IA). Não vai para o banco: são megabytes de binário que o
usuário baixa uma vez.
"""

import threading
from datetime import datetime
from pathlib import Path

import psycopg

from funcoes.db_utils import get_connection
from funcoes.investigacao_utils import PASTA_DADOS
from funcoes.relatorios_docx import gerar_relatorio_docx, nome_arquivo
from funcoes.relatorios_utils import obter_numero_inquerito, obter_padrao

# Onde os .docx gerados ficam até o usuário baixar. Sai de PASTA_INVESTIGACAO (o mesmo bind mount
# ./data do docker-compose), nunca de um caminho relativo: o CWD do processo não é contrato.
PASTA_RELATORIOS = Path(PASTA_DADOS) / "relatorios"

_MINUTOS_GERACAO_STALE = 30   # libera geração presa (servidor reiniciou no meio) depois disso
_DIAS_MANTER_ARQUIVO = 7      # relatório é regerável; não vale guardar o binário indefinidamente


class GeracaoOcupada(Exception):
    """Já existe uma geração em andamento deste usuário neste inquérito."""


class RecursoDeOutroInquerito(Exception):
    """Um id_marcacao/ids_rif/ids_extratos/ids_ipl do payload não pertence a este inquérito.

    A tela só oferece registros do próprio caso, mas o payload é editável: sem esta peita, um id de
    outro inquérito entraria na lista e o relatório traria dado (inclusive PII) de um caso que o
    usuário não pode ver. Devolve 404, como se o registro não existisse — não confirmamos terceiros."""


# =====================================================================
# Linha de status (REL_TB04_GERACOES)
# =====================================================================


def _liberar_geracoes_travadas(id_inquerito: int, id_usuario: int | None) -> None:
    """Marca como erro as gerações penduradas há tempo demais.

    Sem isto, um restart da API no meio da montagem deixa a linha em 'processando' para sempre e o
    índice único tranca o usuário fora do recurso — ele nunca mais conseguiria gerar."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE investigacao.rel_tb04_geracoes
                SET cd_status = 'erro',
                    tx_erro = 'Geração interrompida (liberada por inatividade).',
                    dt_fim = now()
                WHERE id_inquerito = %s AND id_usuario IS NOT DISTINCT FROM %s
                  AND cd_status IN ('pendente', 'processando')
                  AND dt_inicio < now() - make_interval(mins => %s)
                """,
                (id_inquerito, id_usuario, _MINUTOS_GERACAO_STALE),
            )
        conn.commit()


def _criar_linha_geracao(id_inquerito: int, id_padrao: int, id_usuario: int | None, qt_blocos: int) -> int:
    """Cria a linha da geração (trava por índice único parcial); devolve o id.
    Levanta GeracaoOcupada se o usuário já tiver uma geração ativa neste inquérito."""
    _liberar_geracoes_travadas(id_inquerito, id_usuario)
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO investigacao.rel_tb04_geracoes
                        (id_inquerito, id_padrao, id_usuario, qt_total)
                    VALUES (%s, %s, %s, %s)
                    RETURNING id_geracao
                    """,
                    (id_inquerito, id_padrao, id_usuario, qt_blocos),
                )
                id_geracao = cur.fetchone()[0]
            conn.commit()
    except psycopg.errors.UniqueViolation:
        raise GeracaoOcupada("Já há um relatório sendo gerado por você neste inquérito. Aguarde a conclusão.")
    return id_geracao


def _atualizar_geracao(id_geracao: int, set_sql: str, params: tuple) -> None:
    """Conexão própria por update: se o worker segurasse uma transação até o fim, o polling não
    enxergaria progresso nenhum (mesma razão das duas conexões da importação do SIMBA)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE investigacao.rel_tb04_geracoes SET {set_sql} WHERE id_geracao = %s",
                (*params, id_geracao),
            )
        conn.commit()


def listar_geracoes_recentes(id_inquerito: int, id_usuario: int | None, limite: int = 5) -> list[dict]:
    """Últimas gerações do usuário neste inquérito, para o histórico do modal.

    Sem caminho em disco: é detalhe do servidor, e o download passa pela rota própria
    (que confere o acesso de novo). Inclui se o arquivo ainda existe, para a tela não
    oferecer um "baixar de novo" que cai em 404 de limpeza por antiguidade."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id_geracao, id_padrao, cd_status, qt_total, qt_feitos,
                       tx_etapa_atual, tx_erro, no_arquivo, caminho_arquivo,
                       dt_inicio, dt_fim
                FROM investigacao.rel_tb04_geracoes
                WHERE id_inquerito = %s AND id_usuario IS NOT DISTINCT FROM %s
                ORDER BY dt_inicio DESC
                LIMIT %s
                """,
                (id_inquerito, id_usuario, limite),
            )
            colunas = [d.name for d in cur.description]
            recentes = []
            for linha in cur.fetchall():
                g = dict(zip(colunas, linha))
                caminho = g.pop("caminho_arquivo", None)
                g["tem_arquivo"] = bool(caminho and Path(caminho).exists())
                recentes.append(g)
            return recentes


def obter_geracao(id_geracao: int) -> dict | None:
    """Status de uma geração, para o polling da tela e para o download."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id_geracao, id_inquerito, id_padrao, id_usuario, cd_status, qt_total,
                       qt_feitos, tx_etapa_atual, tx_erro, no_arquivo, caminho_arquivo,
                       dt_inicio, dt_fim
                FROM investigacao.rel_tb04_geracoes
                WHERE id_geracao = %s
                """,
                (id_geracao,),
            )
            linha = cur.fetchone()
            if linha is None:
                return None
            return dict(zip([d.name for d in cur.description], linha))


# =====================================================================
# Arquivo em disco
# =====================================================================


def _limpar_relatorios_antigos() -> None:
    """Apaga .docx com mais de _DIAS_MANTER_ARQUIVO, na hora de gravar um novo.

    Limpeza preguiçosa em vez de tarefa periódica porque o projeto não tem agendador — mesma
    solução do .md de prompt do chat de IA. Falha ao apagar é ignorada de propósito: não pode
    derrubar a geração que o usuário está esperando."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id_geracao, caminho_arquivo
                    FROM investigacao.rel_tb04_geracoes
                    WHERE caminho_arquivo IS NOT NULL
                      AND dt_inicio < now() - make_interval(days => %s)
                    """,
                    (_DIAS_MANTER_ARQUIVO,),
                )
                antigos = cur.fetchall()
                for id_geracao, caminho in antigos:
                    try:
                        Path(caminho).unlink(missing_ok=True)
                    except OSError:
                        pass
                    cur.execute(
                        "UPDATE investigacao.rel_tb04_geracoes SET caminho_arquivo = NULL WHERE id_geracao = %s",
                        (id_geracao,),
                    )
            conn.commit()
    except Exception:
        pass


def _gravar_arquivo(id_geracao: int, conteudo: bytes) -> Path:
    """Grava o .docx em PASTA_RELATORIOS com o id no nome (dois usuários podem gerar o relatório
    do mesmo inquérito ao mesmo tempo, e o nome sugerido no download é igual para os dois)."""
    _limpar_relatorios_antigos()
    PASTA_RELATORIOS.mkdir(parents=True, exist_ok=True)
    caminho = PASTA_RELATORIOS / f"{id_geracao}_{datetime.now():%Y%m%d_%H%M%S}.docx"
    caminho.write_bytes(conteudo)
    return caminho


def apagar_arquivos_de_inquerito(cur, id_inquerito: int) -> None:
    """Remove do disco os .docx gerados de um inquérito — chamado por apagar_inquerito, para não
    deixar relatório de caso excluído em data/relatorios.

    Recebe o cursor da transação de exclusão em vez de abrir conexão própria: a leitura precisa
    enxergar as linhas que ainda não foram apagadas, e pegar uma segunda conexão do pool no meio
    de uma transação longa é pedir para travar."""
    cur.execute(
        "SELECT caminho_arquivo FROM investigacao.rel_tb04_geracoes "
        "WHERE id_inquerito = %s AND caminho_arquivo IS NOT NULL",
        (id_inquerito,),
    )
    for (caminho,) in cur.fetchall():
        try:
            Path(caminho).unlink(missing_ok=True)
        except OSError:
            pass


# =====================================================================
# Job
# =====================================================================


# Cada família de registro aceito no payload → (tabela, coluna id). A peita A5 confere que todo id
# de cada lista pertence ao inquérito da rota, um SELECT por família.
_TABELAS_RECURSO_RELATORIO = {
    "ids_marcacoes": ("investigacao.rel_tb03_marcacoes", "id_marcacao"),
    "ids_rif": ("investigacao.rif_tb01_rifs", "id_rif"),
    "ids_extratos": ("investigacao.smb_tb01_extratos", "id_extrato"),
    "ids_ipl": ("investigacao.ipl_tb01_inqueritos_pdf", "id_ipl"),
}


def _validar_ids_do_caso(id_inquerito: int, opcoes: dict) -> None:
    """Levanta RecursoDeOutroInquerito se algum id das listas do payload não for deste inquérito.

    Um SELECT por família: conta os ids pedidos e os que de fato pertencem ao caso; se diferirem,
    algum é estranho. Não devolve quais — não revelamos se um id existe em outro caso."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            for campo, (tabela, coluna) in _TABELAS_RECURSO_RELATORIO.items():
                ids = opcoes.get(campo)
                if not ids:
                    continue
                try:
                    ids_int = [int(i) for i in ids]
                except (TypeError, ValueError):
                    raise RecursoDeOutroInquerito()
                cur.execute(
                    f"SELECT count(*) FROM {tabela} WHERE {coluna} = ANY(%s) AND id_inquerito = %s",
                    (ids_int, id_inquerito),
                )
                pertencentes = cur.fetchone()[0]
                if pertencentes != len(set(ids_int)):
                    raise RecursoDeOutroInquerito()


def iniciar_geracao(id_inquerito: int, id_padrao: int, usuario: dict | None, opcoes: dict) -> int:
    """Cria a linha da geração e dispara o job em background; devolve o id da geração.

    opcoes é o que a tela escolheu (valores, ids_marcacoes, ids_rif, ids_extratos, ids_ipl,
    ids_blocos, comentario_ia, reescrever_comentarios) e vai direto para gerar_relatorio_docx.
    Levanta ValueError se o padrão não existir, RecursoDeOutroInquerito se algum id das listas for
    de outro inquérito e GeracaoOcupada se já houver uma ativa."""
    padrao = obter_padrao(id_padrao)
    if padrao is None:
        raise ValueError("Padrão não encontrado.")

    # peita A5: nenhum id das listas pode ser de outro inquérito (ver RecursoDeOutroInquerito)
    _validar_ids_do_caso(id_inquerito, opcoes)

    # o total é contado sobre os blocos que realmente vão ser montados, senão a barra pararia
    # antes do fim num padrão com blocos desmarcados
    blocos = padrao["blocos"]
    ids_blocos = opcoes.get("ids_blocos")
    if ids_blocos is not None:
        escolhidos = {int(i) for i in ids_blocos}
        blocos = [b for b in blocos if b["id_bloco"] in escolhidos]

    id_usuario = (usuario or {}).get("id_usuario")
    id_geracao = _criar_linha_geracao(id_inquerito, id_padrao, id_usuario, len(blocos))
    threading.Thread(
        target=_processar_geracao,
        args=(id_geracao, id_inquerito, id_padrao, usuario, opcoes, padrao),
        daemon=True,
    ).start()
    return id_geracao


def _processar_geracao(id_geracao: int, id_inquerito: int, id_padrao: int, usuario: dict | None,
                       opcoes: dict, padrao: dict) -> None:
    try:
        _atualizar_geracao(id_geracao, "cd_status = 'processando', tx_etapa_atual = %s", ("Consultando os dados do caso…",))

        def progresso(indice: int, rotulo: str) -> None:
            _atualizar_geracao(id_geracao, "qt_feitos = %s, tx_etapa_atual = %s", (indice, rotulo[:200]))

        conteudo = gerar_relatorio_docx(
            id_inquerito, id_padrao,
            opcoes.get("valores"), opcoes.get("ids_marcacoes"), usuario,
            ids_rif=opcoes.get("ids_rif"), ids_extratos=opcoes.get("ids_extratos"),
            comentario_ia=bool(opcoes.get("comentario_ia")),
            reescrever_comentarios=bool(opcoes.get("reescrever_comentarios")),
            ids_ipl=opcoes.get("ids_ipl"), ids_blocos=opcoes.get("ids_blocos"),
            progresso=progresso,
            expandir_conversas=bool(opcoes.get("expandir_conversas")),
        )

        _atualizar_geracao(id_geracao, "tx_etapa_atual = %s", ("Gravando o documento…",))
        # o número do inquérito vem do banco, nunca do payload: 'numero_inquerito' é variável
        # automática e a tela não a envia
        nome = nome_arquivo(id_padrao, obter_numero_inquerito(id_inquerito), padrao)
        caminho = _gravar_arquivo(id_geracao, conteudo)

        _atualizar_geracao(
            id_geracao,
            "cd_status = 'concluido', qt_feitos = qt_total, tx_etapa_atual = NULL, "
            "no_arquivo = %s, caminho_arquivo = %s, dt_fim = now()",
            (nome, str(caminho)),
        )
    except Exception as exc:
        # nunca relança: quem chamou é uma thread solta, e o erro tem que chegar ao usuário pelo
        # polling, não morrer no log
        _atualizar_geracao(
            id_geracao,
            "cd_status = 'erro', tx_erro = %s, tx_etapa_atual = NULL, dt_fim = now()",
            (str(exc)[:2000],),
        )

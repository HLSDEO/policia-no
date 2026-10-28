"""Busca automática de entidades: roda a IA em background com as mesmas ferramentas de
leitura do chat normal mais as de escrita (criar entidade/relação) — ver
funcoes.ia_investigacao.ferramentas_para_busca_automatica.

Dois tipos de busca (TP_BUSCA):
- 'prompts': varre os dados guiada pelos prompts ativos (ver prompts_entidade_utils);
- 'relacionar_manuais': foca nas entidades que o investigador criou manualmente e ainda
  estão soltas, investigando os dados atrás de vínculos entre elas.

Tudo que a IA cria nasce como SUGESTÃO ('sugerida'), com a justificativa/evidência e o
ID da busca gravados — o investigador confirma ou descarta na Mesa de Inquérito.

Mesmo padrão de job em background do investigacao_utils.iniciar_extracao: cria a linha
com status 'pendente', dispara uma thread e devolve na hora — o front-end faz polling.
"""

import logging
import threading
import time

from funcoes.anonimizacao import anonimizacao_ativa, anonimizador_para_inquerito
from funcoes.db_utils import get_connection
from funcoes.ia_auditoria import registrar_auditoria
from funcoes.ia_investigacao import ferramentas_para_busca_automatica
from funcoes.ia_provedores import AuditoriaIA, enviar_mensagem
from funcoes.prompts_entidade_utils import listar_prompts_ativos

logger = logging.getLogger(__name__)

TIPOS_BUSCA_VALIDOS = {"prompts", "relacionar_manuais"}

SISTEMA_PROMPT_BUSCA = (
    "Você é um assistente de investigação rodando uma busca automática de entidades sobre os "
    "dados extraídos de um inquérito. Use as ferramentas de leitura disponíveis para investigar "
    "cada instrução abaixo e, para tudo que encontrar de relevante e concreto (baseado em dado "
    "real retornado pelas ferramentas — nunca invente conteúdo), crie uma entidade com "
    "criar_entidade, sempre preenchendo a justificativa com a evidência real (trecho, nome, data) "
    "que embasou a criação. Antes de criar, chame listar_entidades_existentes para não duplicar o "
    "que já existe — se encontrar algo relacionado a uma entidade já existente, use criar_relacao "
    "para ligar as duas em vez de criar uma entidade repetida. Tudo que você criar entra como "
    "sugestão a ser revisada pelo investigador, então prefira precisão a volume. Não é "
    "obrigatório criar algo para cada instrução — se não achar nada relevante e concreto, siga em "
    "frente. Termine com um resumo curto do que foi encontrado, em português."
)

SISTEMA_PROMPT_RELACIONAR = (
    "Você é um assistente de investigação. O investigador criou manualmente as entidades listadas "
    "abaixo no grafo do inquérito e quer saber COMO ELAS SE CONECTAM. Para cada par plausível, "
    "investigue os dados com as ferramentas de leitura (mensagens, ligações, contatos, "
    "localizações...) atrás de vínculo concreto: mesma conversa, mesmo telefone, menção de um ao "
    "outro, mesma data/local. Quando encontrar evidência real retornada pelas ferramentas, chame "
    "criar_relacao ligando as duas entidades, com a justificativa citando essa evidência. Se um "
    "vínculo passar por um registro relevante que ainda não é entidade (ex.: a mensagem em que um "
    "cita o outro), você pode criar essa entidade-ponte com criar_entidade e ligá-la às duas. "
    "Nunca crie relação sem evidência concreta — tudo vira sugestão a ser revisada pelo "
    "investigador. Termine com um resumo curto, em português."
)


def iniciar_busca_automatica(id_inquerito: int, id_usuario: int | None, tp_busca: str = "prompts") -> int:
    """Cria a linha da busca (status 'pendente') e dispara o processamento em background;
    devolve o ID da busca."""
    if tp_busca not in TIPOS_BUSCA_VALIDOS:
        raise ValueError(f"Tipo de busca inválido: {tp_busca!r}")
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO investigacao.ent_tb04_buscas_automaticas (id_inquerito, id_usuario, tp_busca)
                VALUES (%s, %s, %s)
                RETURNING id_busca
                """,
                (id_inquerito, id_usuario, tp_busca),
            )
            id_busca = cur.fetchone()[0]
        conn.commit()

    thread = threading.Thread(
        target=_processar_busca, args=(id_busca, id_inquerito, id_usuario, tp_busca), daemon=True
    )
    thread.start()
    return id_busca


def _montar_mensagem_prompts() -> str:
    """Mensagem do modo 'prompts': as instruções ativas da biblioteca, numeradas."""
    prompts = listar_prompts_ativos("investigacao")
    if not prompts:
        raise ValueError("Nenhum prompt ativo de investigação cadastrado em Prompts de entidade.")
    instrucoes = "\n".join(f"{i + 1}. {p['tx_prompt']}" for i, p in enumerate(prompts))
    return (
        "Investigue os dados deste inquérito segundo as instruções abaixo e crie entidades/"
        f"relações para o que encontrar de concreto:\n\n{instrucoes}"
    )


def _montar_mensagem_relacionar(id_inquerito: int) -> str:
    """Mensagem do modo 'relacionar_manuais': as entidades manuais do inquérito, com destaque
    para as que ainda não têm nenhuma relação."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT e.id_entidade, e.tp_origem, e.tx_comentario,
                       NOT EXISTS (
                           SELECT 1 FROM investigacao.ent_tb03_relacoes r
                           WHERE (r.id_entidade_origem = e.id_entidade OR r.id_entidade_destino = e.id_entidade)
                             AND r.cd_revisao <> 'descartada'
                       ) AS solta
                FROM investigacao.ent_tb01_entidades e
                WHERE e.id_inquerito = %s AND e.in_automatico = false AND e.cd_revisao <> 'descartada'
                ORDER BY e.dt_criacao
                """,
                (id_inquerito,),
            )
            entidades = cur.fetchall()

    if len(entidades) < 2:
        raise ValueError(
            "É preciso ter pelo menos duas entidades manuais (botão 📌 nas telas de dados) "
            "para buscar relações entre elas."
        )

    linhas = "\n".join(
        f"- id_entidade={id_entidade} ({tp_origem}{', ainda sem nenhuma relação' if solta else ''}): {comentario}"
        for id_entidade, tp_origem, comentario, solta in entidades
    )
    return (
        "Estas são as entidades criadas manualmente pelo investigador neste inquérito. "
        "Investigue os dados e proponha relações entre elas (priorize as que ainda estão "
        f"sem nenhuma relação):\n\n{linhas}"
    )


def _contar_criadas_pela_busca(id_busca: int) -> tuple[int, int]:
    """Quantas entidades/relações esta execução criou — contagem exata pela proveniência
    (id_busca), imune a criações manuais concorrentes."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM investigacao.ent_tb01_entidades WHERE id_busca = %s", (id_busca,))
            qt_entidades = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM investigacao.ent_tb03_relacoes WHERE id_busca = %s", (id_busca,))
            qt_relacoes = cur.fetchone()[0]
    return qt_entidades, qt_relacoes


def _processar_busca(id_busca: int, id_inquerito: int, id_usuario: int | None, tp_busca: str) -> None:
    auditoria = AuditoriaIA()
    inicio_turno = time.monotonic()
    sistema = ""
    mensagem_usuario = ""
    auditoria_registrada = False
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE investigacao.ent_tb04_buscas_automaticas SET cd_status = 'processando' WHERE id_busca = %s",
                    (id_busca,),
                )
            conn.commit()

        if tp_busca == "relacionar_manuais":
            sistema = SISTEMA_PROMPT_RELACIONAR
            mensagem_usuario = _montar_mensagem_relacionar(id_inquerito)
        else:
            sistema = SISTEMA_PROMPT_BUSCA
            mensagem_usuario = _montar_mensagem_prompts()

        ferramentas, funcoes = ferramentas_para_busca_automatica(id_inquerito, id_usuario, id_busca)
        # "Anonimizar dados" (Configurações → Prompt IA). Aqui a proteção é parcial por natureza:
        # a busca automática existe para achar gente que o sistema AINDA não conhece, e nome que
        # não está no dicionário só é codificado se a heurística pegar. O que já é conhecido — e o
        # CPF/telefone/e-mail que vêm nos resultados das ferramentas — sai como código.
        anonimizador = anonimizador_para_inquerito(id_inquerito) if anonimizacao_ativa("AI") else None
        resposta, modelo = enviar_mensagem(
            [{"role": "user", "content": mensagem_usuario}],
            sistema=sistema,
            ferramentas=ferramentas,
            funcoes=funcoes,
            auditoria=auditoria,
            anonimizador=anonimizador,
            id_usuario=id_usuario,
        )
        registrar_auditoria(
            "busca_automatica", mensagem_usuario, sistema, auditoria, modelo,
            int((time.monotonic() - inicio_turno) * 1000), resposta=resposta,
            id_inquerito=id_inquerito, id_usuario=id_usuario,
        )
        auditoria_registrada = True

        qt_entidades, qt_relacoes = _contar_criadas_pela_busca(id_busca)

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE investigacao.ent_tb04_buscas_automaticas
                    SET cd_status = 'concluido', dt_fim = now(),
                        qt_entidades_criadas = %s, qt_relacoes_criadas = %s
                    WHERE id_busca = %s
                    """,
                    (qt_entidades, qt_relacoes, id_busca),
                )
            conn.commit()
    except Exception as exc:  # guarda a falha pra tela, em vez de deixar a busca presa em "processando"
        if not auditoria_registrada:
            registrar_auditoria(
                "busca_automatica", mensagem_usuario, sistema, auditoria, None,
                int((time.monotonic() - inicio_turno) * 1000), status="erro", erro=str(exc),
                id_inquerito=id_inquerito, id_usuario=id_usuario,
            )
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE investigacao.ent_tb04_buscas_automaticas
                        SET cd_status = 'erro', tx_erro = %s, dt_fim = now()
                        WHERE id_busca = %s
                        """,
                        (str(exc)[:2000], id_busca),
                    )
                conn.commit()
        except Exception:
            logger.exception("Falha ao gravar status de erro da busca automática %s", id_busca)


def listar_buscas(id_inquerito: int) -> list[dict]:
    """Histórico de execuções da busca automática de um inquérito, mais recente primeiro."""
    query = """
        SELECT b.id_busca, b.tp_busca, b.cd_status, b.tx_erro, b.qt_entidades_criadas, b.qt_relacoes_criadas,
               b.dt_inicio, b.dt_fim, u.no_usuario AS iniciado_por
        FROM investigacao.ent_tb04_buscas_automaticas b
        LEFT JOIN seguranca.usr_tb01_usuarios u ON u.id_usuario = b.id_usuario
        WHERE b.id_inquerito = %s
        ORDER BY b.dt_inicio DESC
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (id_inquerito,))
            colunas = [d.name for d in cur.description]
            return [dict(zip(colunas, linha)) for linha in cur.fetchall()]

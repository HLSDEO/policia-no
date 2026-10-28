"""Compactação do histórico das conversas de IA (chat por inquérito).

O histórico de uma conversa era enviado inteiro em todo turno: cada pergunta reenviava todas as
perguntas e respostas anteriores. Numa conversa longa isso (a) paga de novo, a cada turno, os
tokens de tudo o que já foi dito e (b) acaba estourando a janela de contexto do modelo — o
provedor devolve erro e o turno morre, sem nenhum tratamento.

Aqui o histórico vira "resumo + últimas mensagens": quando o que está por enviar passa de uma
fatia da janela do modelo, as mensagens antigas são condensadas num resumo (uma chamada ao
próprio provedor, sem ferramentas), gravado na conversa junto com o id da última mensagem que ele
cobre. Os turnos seguintes reaproveitam esse resumo — a compactação só volta a rodar quando o
histórico crescer de novo.

O resumo entra no PROMPT DE SISTEMA (bloco_resumo), não na lista de mensagens: assim não há risco
de quebrar a alternância user/assistant que a API da Anthropic exige, e o bloco viaja junto com o
resto do prompt de sistema (inclusive no "não enviar+exibir prompt").

Módulo separado de ia_utils porque a mecânica é usada com tabelas diferentes — os nomes
das tabelas entram por parâmetro.
"""

from funcoes.db_utils import get_connection
from funcoes.ia_provedores import (
    CHARS_POR_TOKEN, AuditoriaIA, ErroProvedorIA, enviar_mensagem, janela_contexto_configurada,
)

# CHARS_POR_TOKEN (~chars por token em português, baixo de propósito: mais folga) vem de
# ia_provedores, onde os cortes de tamanho do turno também a usam; é reexportada aqui porque
# ia_utils a importa deste módulo desde antes — uma só verdade, um só valor.
__all__ = ["CHARS_POR_TOKEN", "bloco_resumo", "estimar_tokens", "montar_historico", "tokens_historico"]

# Teto do histórico: fração da janela do modelo que as mensagens anteriores podem ocupar antes de
# serem compactadas. O resto da janela é do prompt de sistema, dos schemas das ferramentas, dos
# resultados que as ferramentas trouxerem neste turno e da resposta.
_FRACAO_JANELA_HISTORICO = 0.15
# Piso, para janelas pequenas (modelo local) não compactarem a conversa já no segundo turno.
_MIN_TOKENS_HISTORICO = 3000
# Mensagens recentes preservadas na íntegra (a última é sempre a pergunta deste turno).
_MENSAGENS_RECENTES = 4
# Quanto do histórico antigo vai numa única chamada de resumo; o que passar disso é resumido em
# rodadas sucessivas (conversa que já estava gigante quando esta compactação entrou no ar).
_MAX_CHUNKS_RESUMO = 6

_SISTEMA_RESUMO = (
    "Você compacta o histórico de uma conversa entre um investigador e um assistente de análise "
    "criminal. O objetivo é caber na janela de contexto do modelo sem perder o fio da "
    "investigação: o resumo vai substituir as mensagens antigas nos próximos turnos.\n\n"
    "Escreva em português, em tópicos, preservando:\n"
    "1. O que o investigador pediu, as hipóteses levantadas e as decisões já tomadas.\n"
    "2. Os fatos apurados COM os identificadores citados — marcadores [msg:wpp:ID]/[msg:sms:ID], "
    "nomes, apelidos, CPF/CNPJ, telefones, contas, valores e datas. Copie-os literalmente; nunca "
    "aproxime, arredonde nem invente um identificador.\n"
    "3. As buscas já feitas e o que não deu em nada (para o assistente não repetir trabalho).\n"
    "4. Pendências e próximas diligências combinadas.\n\n"
    "Não responda à investigação e não acrescente conclusão nova: apenas condense o que está no "
    "histórico. Se o texto abaixo já trouxer um RESUMO ANTERIOR, incorpore-o — nada do que estava "
    "nele pode se perder. Não use mais que ~600 palavras."
)

_CABECALHO_BLOCO = (
    "\n\n## RESUMO DA CONVERSA ATÉ AQUI (mensagens antigas, compactadas para poupar contexto)\n"
)
_RODAPE_BLOCO = (
    "\n(Fim do resumo. As mensagens mais recentes seguem na íntegra no histórico abaixo. Se "
    "precisar de um detalhe que o resumo não traz — o id de uma mensagem, um valor exato — busque "
    "de novo com as ferramentas em vez de deduzir.)"
)


def estimar_tokens(texto: str) -> int:
    """Estimativa grosseira e conservadora de tokens de um texto."""
    return int(len(texto or "") / CHARS_POR_TOKEN)


def _limite_tokens_historico(id_usuario: int | None = None) -> int:
    """Teto de tokens do histórico antes de compactar — pela janela do modelo deste usuário."""
    return max(int(janela_contexto_configurada(id_usuario) * _FRACAO_JANELA_HISTORICO), _MIN_TOKENS_HISTORICO)


def _carregar_conversa(id_conversa_ia: int, tabela_conversas: str) -> tuple[str, int]:
    """(resumo já gravado, id da última mensagem que ele cobre) — ('', 0) se ainda não houve resumo."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT tx_resumo_historico, id_mensagem_resumida_ate FROM {tabela_conversas} "
                "WHERE id_conversa_ia = %s",
                (id_conversa_ia,),
            )
            row = cur.fetchone()
    if not row:
        return "", 0
    return (row[0] or ""), (row[1] or 0)


def _gravar_resumo(id_conversa_ia: int, tabela_conversas: str, resumo: str, id_ate: int) -> None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE {tabela_conversas} SET tx_resumo_historico = %s, "
                "id_mensagem_resumida_ate = %s, dt_resumo_historico = now() WHERE id_conversa_ia = %s",
                (resumo, id_ate, id_conversa_ia),
            )
        conn.commit()


def _mensagens_pendentes(id_conversa_ia: int, tabela_mensagens: str, id_ate: int) -> list[dict]:
    """Mensagens ainda não cobertas pelo resumo, em ordem cronológica."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT id_mensagem_ia, tp_papel, tx_conteudo
                FROM {tabela_mensagens}
                WHERE id_conversa_ia = %s AND id_mensagem_ia > %s
                ORDER BY dt_mensagem, id_mensagem_ia
                """,
                (id_conversa_ia, id_ate),
            )
            return [
                {"id_mensagem_ia": r[0], "role": r[1], "content": r[2] or ""}
                for r in cur.fetchall()
            ]


def bloco_resumo(resumo: str) -> str:
    """Bloco para o prompt de sistema; vazio se a conversa ainda não foi compactada."""
    resumo = (resumo or "").strip()
    return _CABECALHO_BLOCO + resumo + _RODAPE_BLOCO if resumo else ""


def tokens_historico(id_conversa_ia: int, tabela_conversas: str, tabela_mensagens: str) -> int:
    """Tokens que o histórico ocupa HOJE (resumo gravado + mensagens ainda não resumidas).

    Usado pelo orçamento do modo "conteúdo completo": é o estado antes de uma eventual compactação
    deste turno, ou seja, uma superestimativa — que é o lado seguro (anexa menos conteúdo)."""
    resumo, id_ate = _carregar_conversa(id_conversa_ia, tabela_conversas)
    pendentes = _mensagens_pendentes(id_conversa_ia, tabela_mensagens, id_ate)
    return estimar_tokens(bloco_resumo(resumo)) + sum(estimar_tokens(m["content"]) for m in pendentes)


def _transcrever(mensagens: list[dict]) -> str:
    rotulo = {"user": "INVESTIGADOR", "assistant": "ASSISTENTE"}
    return "\n\n".join(
        f"--- {rotulo.get(m['role'], m['role'])} ---\n{m['content']}" for m in mensagens
    )


def _fatiar(mensagens: list[dict], max_chars: int) -> list[list[dict]]:
    """Divide as mensagens em blocos que caibam numa chamada de resumo, sem quebrar mensagem.

    Uma mensagem sozinha maior que o teto vira um bloco só dela (será truncada no envio) — melhor
    do que descartá-la."""
    blocos: list[list[dict]] = []
    atual: list[dict] = []
    tamanho = 0
    for m in mensagens:
        n = len(m["content"])
        if atual and tamanho + n > max_chars:
            blocos.append(atual)
            atual, tamanho = [], 0
        atual.append(m)
        tamanho += n
    if atual:
        blocos.append(atual)
    return blocos


def _resumir(resumo_anterior: str, mensagens: list[dict], max_chars: int, auditoria,
             anonimizador=None, id_usuario: int | None = None) -> str:
    """Uma chamada ao provedor, sem ferramentas, condensando resumo anterior + mensagens.

    O resumo é a conversa inteira em miniatura — nome, CPF e telefone inclusive —, então ele
    passa pelo anonimizador como qualquer outra chamada. O resumo GRAVADO na conversa volta em
    claro (enviar_mensagem descodifica a resposta): quem reabrir a conversa amanhã, com o
    checkbox desligado ou com outra tabela de-para, lê nome de gente, não FULANO."""
    partes = []
    if resumo_anterior.strip():
        partes.append("=== RESUMO ANTERIOR ===\n" + resumo_anterior.strip())
    partes.append("=== MENSAGENS A CONDENSAR ===\n" + _transcrever(mensagens))
    texto = "\n\n".join(partes)[:max_chars]
    resumo, _modelo = enviar_mensagem(
        [{"role": "user", "content": texto}], sistema=_SISTEMA_RESUMO, auditoria=auditoria,
        anonimizador=anonimizador, id_usuario=id_usuario,
    )
    return (resumo or "").strip()


def montar_historico(
    id_conversa_ia: int,
    tabela_conversas: str,
    tabela_mensagens: str,
    auditoria: AuditoriaIA | None = None,
    on_etapa=None,
    checkpoint=None,
    compactar: bool = True,
    anonimizador=None,
    id_usuario: int | None = None,
) -> tuple[str, list[dict]]:
    """Devolve (bloco de resumo para o prompt de sistema, mensagens a enviar ao provedor).

    Compacta as mensagens antigas se o histórico passou do teto da janela; caso contrário devolve
    o resumo que já existia (ou '') e as mensagens ainda não resumidas — sem chamar a IA.

    compactar=False (modo "não enviar+exibir prompt") monta o prompt com o histórico como está: o
    investigador pediu justamente para NÃO chamar a IA neste turno.

    Falha ao resumir não derruba o turno: cai no comportamento antigo (histórico inteiro).
    """
    resumo, id_ate = _carregar_conversa(id_conversa_ia, tabela_conversas)
    pendentes = _mensagens_pendentes(id_conversa_ia, tabela_mensagens, id_ate)
    mensagens = [{"role": m["role"], "content": m["content"]} for m in pendentes]

    limite = _limite_tokens_historico(id_usuario)
    ocupado = estimar_tokens(bloco_resumo(resumo)) + sum(estimar_tokens(m["content"]) for m in pendentes)
    antigas = pendentes[:-_MENSAGENS_RECENTES]
    if not compactar or ocupado <= limite or not antigas:
        return bloco_resumo(resumo), mensagens

    if on_etapa:
        on_etapa("resumir_conversa")
    max_chars = int(limite * CHARS_POR_TOKEN)
    try:
        blocos = _fatiar(antigas, max_chars)
        novo = resumo
        if len(blocos) > _MAX_CHUNKS_RESUMO:
            # conversa que já estava gigante quando esta compactação entrou no ar: resumir tudo
            # custaria uma dezena de chamadas num único turno. Condensa o trecho mais recente e
            # diz, no próprio resumo, o que ficou de fora — as mensagens seguem gravadas e
            # visíveis na tela; o que se perde é só a memória delas no prompt.
            omitidas = sum(len(b) for b in blocos[:-_MAX_CHUNKS_RESUMO])
            blocos = blocos[-_MAX_CHUNKS_RESUMO:]
            novo = (novo + "\n\n" if novo.strip() else "") + (
                f"(As {omitidas} mensagens mais antigas desta conversa não entraram neste resumo — "
                "a compactação começou pelo trecho mais recente. Elas continuam gravadas na conversa "
                "e visíveis para o investigador; se precisar delas, peça a ele.)"
            )
        for bloco in blocos:
            if checkpoint:
                checkpoint()  # cancelamento cooperativo: o investigador pode abortar no meio
            novo = _resumir(novo, bloco, max_chars, auditoria, anonimizador, id_usuario)
        if not novo:
            return bloco_resumo(resumo), mensagens
        _gravar_resumo(id_conversa_ia, tabela_conversas, novo, antigas[-1]["id_mensagem_ia"])
    except ErroProvedorIA:
        # provedor fora do ar ou resposta inesperada: manda o histórico inteiro, como antes desta
        # compactação existir — pior gastar tokens do que perder o turno
        return bloco_resumo(resumo), mensagens

    recentes = pendentes[-_MENSAGENS_RECENTES:]
    return bloco_resumo(novo), [{"role": m["role"], "content": m["content"]} for m in recentes]

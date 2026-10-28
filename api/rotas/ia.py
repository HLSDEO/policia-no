"""Rotas do chat de IA por inquérito (usa o provedor configurado em Configurações)."""

from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from funcoes.auth_utils import exigir_aprovado
from funcoes.ia_provedores import ErroProvedorIA, janela_contexto_configurada
from fastapi.responses import FileResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from funcoes.env_utils import parse_env, read_env_text
from funcoes.ia_auditoria import listar_auditoria
from funcoes.ia_eventos import CABECALHOS_SSE, fluxo_processamento
from funcoes.ia_planilha_utils import obter_planilha_ia
from funcoes.ia_relatorio_docx import obter_relatorio_ia
from funcoes.ia_utils import (
    ConversaOcupada,
    apagar_conversa_ia,
    criar_conversa,
    duplicar_conversa_ia,
    enviar_mensagem_usuario,
    listar_conversas_ia,
    listar_mensagens_ia,
    obter_feedbacks_conversa,
    obter_id_conversa_de_mensagem_ia,
    obter_id_inquerito_de_conversa_ia,
    obter_processamento,
    obter_arquivo_prompt,
    registrar_feedback_mensagem,
    renomear_conversa_ia,
    solicitar_cancelamento,
)
from funcoes.ia_delegacia import exigir_acesso_ia_inquerito, exigir_ia_ativa, exigir_ia_inquerito
from funcoes.investigacao_utils import usuario_tem_acesso_inquerito


# Lado "quem pede" do flag IN_IA_ATIVA (delegacia do usuário logado), para todas as rotas do
# router. O lado "sobre o quê" (delegacia do inquérito) depende do id e entra rota a rota, por
# exigir_acesso_ia_inquerito / exigir_ia_inquerito — ver funcoes/ia_delegacia.py.
router = APIRouter(dependencies=[Depends(exigir_ia_ativa)])


class NovaConversaIn(BaseModel):
    """Corpo aceito pelo POST que cria uma nova conversa de IA."""

    titulo: str = Field("", max_length=200)
    tp_escopo: str | None = Field(
        None,
        description=(
            "Restringe a conversa a um único documento anexado ('rif', 'simba' ou 'ipl') em vez do "
            "inquérito inteiro — usado pelo botão 'Prompt de IA específico'. Exige id_escopo."
        ),
    )
    id_escopo: int | None = Field(
        None, description="ID do RIF/extrato do SIMBA/IPL, conforme tp_escopo."
    )


class EscopoMensagemIn(BaseModel):
    """Item da multisseleção "considerar apenas" de uma mensagem do chat geral do inquérito."""

    tp_escopo: str = Field(..., max_length=20, description="'extracao', 'rif', 'simba' ou 'ipl'.")
    id_escopo: int = Field(..., description="ID do item selecionado (extração/RIF/extrato SIMBA/IPL).")


class NovaMensagemIn(BaseModel):
    """Corpo aceito pelo POST que envia uma mensagem do usuário na conversa."""

    texto: str = Field(..., min_length=1, max_length=8000)
    incluir_conteudo: bool = Field(
        False, description="Anexa o conteúdo extraído do inquérito ao contexto enviado à IA nesta mensagem."
    )
    exibir_prompt: bool = Field(
        False,
        description=(
            "Não chama a IA em nenhum momento: monta o prompt completo — com o conteúdo extraído "
            "do inquérito já anexado, como no modo conteúdo completo — e devolve para copiar em "
            "outra IA, junto do .md para download. Liga incluir_conteudo sozinho."
        ),
    )
    escopos: list[EscopoMensagemIn] | None = Field(
        None,
        description=(
            "Restringe esta mensagem aos itens selecionados (extrações, RIFs, extratos SIMBA ou "
            "IPLs) em vez de todo o inquérito. Nenhum item = considera tudo."
        ),
    )
    contexto_tela: dict | None = Field(
        None,
        description=(
            "Retrato do painel de filtros da tela de grafo aberta (grafo do RIF/do SIMBA ou mesa "
            "de inquérito): campos, opções válidas e o que já está filtrado. Com ele a IA pode "
            "propor um recorte do grafo no fim da resposta, que vira um botão para o investigador "
            "aplicar. Montado por web/js/utils/grafo_filtros.js; validado em "
            "funcoes/ia_contexto_tela.py."
        ),
    )


class FeedbackMensagemIn(BaseModel):
    """Voto 👍/👎 numa resposta da IA. `None` remove o voto do usuário."""

    tp_feedback: str | None = Field(
        None, description="'positivo', 'negativo' ou nulo (remover o voto)."
    )


class RenomearConversaIn(BaseModel):
    """Novo título da conversa de IA."""

    titulo: str = Field(..., min_length=1, max_length=200)


def _exigir_acesso_via_mensagem_ia(id_mensagem_ia: int, usuario: dict) -> int:
    """Resolve a conversa dona da mensagem e confere acesso; devolve o id_conversa_ia."""
    id_conversa = obter_id_conversa_de_mensagem_ia(id_mensagem_ia)
    if id_conversa is None:
        raise HTTPException(status_code=404, detail="Mensagem de IA não encontrada.")
    _exigir_acesso_via_conversa_ia(id_conversa, usuario)
    return id_conversa


def _exigir_acesso_via_conversa_ia(id_conversa_ia: int, usuario: dict) -> None:
    """Resolve o inquérito dono da conversa de IA e confere acesso — para rotas que só recebem
    id_conversa_ia. Confere também o flag IN_IA_ATIVA do inquérito resolvido: o do usuário já foi
    conferido no router, mas o do inquérito só dá para saber depois de resolvê-lo aqui."""
    id_inquerito = obter_id_inquerito_de_conversa_ia(id_conversa_ia)
    if id_inquerito is None:
        raise HTTPException(status_code=404, detail="Conversa de IA não encontrada.")
    if not usuario_tem_acesso_inquerito(id_inquerito, usuario):
        raise HTTPException(status_code=403, detail="Sem permissão para este inquérito.")
    exigir_ia_inquerito(id_inquerito, usuario)


@router.get("/api/investigacao/inqueritos/{id_inquerito}/ia/conversas")
def conversas_ia(
    id_inquerito: int,
    conversa_ativa: int | None = None,
    usuario: dict = Depends(exigir_acesso_ia_inquerito),
):
    """Lista as conversas de IA já criadas para o inquérito.

    conversa_ativa: a conversa aberta agora na tela de quem chama — poupada da limpeza automática
    de conversas vazias (ver listar_conversas_ia), para não sumir enquanto se digita a 1ª mensagem.
    """
    return listar_conversas_ia(id_inquerito, preservar=conversa_ativa)


@router.post("/api/investigacao/inqueritos/{id_inquerito}/ia/conversas")
def criar_conversa_ia(id_inquerito: int, payload: NovaConversaIn, usuario: dict = Depends(exigir_acesso_ia_inquerito)):
    """Cria uma nova conversa de IA para o inquérito (opcionalmente restrita a um RIF/extrato/IPL)."""
    try:
        id_conversa = criar_conversa(
            id_inquerito, payload.titulo.strip() or None, usuario["id_usuario"],
            payload.tp_escopo, payload.id_escopo,
        )
    except ErroProvedorIA as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "id_conversa_ia": id_conversa}


@router.delete("/api/investigacao/ia/conversas/{id_conversa_ia}")
def apagar_conversa(id_conversa_ia: int, usuario: dict = Depends(exigir_aprovado)):
    """Apaga uma conversa de IA e suas mensagens (qualquer usuário com acesso ao inquérito)."""
    _exigir_acesso_via_conversa_ia(id_conversa_ia, usuario)
    if not apagar_conversa_ia(id_conversa_ia):
        raise HTTPException(status_code=404, detail="Conversa de IA não encontrada.")
    return {"ok": True}


@router.post("/api/investigacao/ia/conversas/{id_conversa_ia}/duplicar")
def duplicar_conversa(id_conversa_ia: int, usuario: dict = Depends(exigir_aprovado)):
    """Duplica uma conversa de IA (exportar 1-clique): nova conversa com o mesmo escopo e
    cópia das mensagens, sem levar processamentos/auditoria/feedbacks."""
    _exigir_acesso_via_conversa_ia(id_conversa_ia, usuario)
    try:
        id_nova = duplicar_conversa_ia(id_conversa_ia, usuario["id_usuario"])
    except ErroProvedorIA as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"ok": True, "id_conversa_ia": id_nova}


@router.patch("/api/investigacao/ia/conversas/{id_conversa_ia}")
def renomear_conversa(id_conversa_ia: int, payload: RenomearConversaIn, usuario: dict = Depends(exigir_aprovado)):
    """Troca o título de uma conversa de IA (qualquer usuário com acesso ao inquérito)."""
    _exigir_acesso_via_conversa_ia(id_conversa_ia, usuario)
    try:
        if not renomear_conversa_ia(id_conversa_ia, payload.titulo):
            raise HTTPException(status_code=404, detail="Conversa de IA não encontrada.")
    except ErroProvedorIA as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


@router.get("/api/investigacao/ia/conversas/{id_conversa_ia}/feedbacks")
def feedbacks_conversa(id_conversa_ia: int, usuario: dict = Depends(exigir_aprovado)):
    """Agregado de 👍/👎 por mensagem da conversa: {id_mensagem: {positivo, negativo, meu}}."""
    _exigir_acesso_via_conversa_ia(id_conversa_ia, usuario)
    return obter_feedbacks_conversa(id_conversa_ia, usuario["id_usuario"])


@router.post("/api/investigacao/ia/mensagens/{id_mensagem_ia}/feedback")
def feedback_mensagem(
    id_mensagem_ia: int, payload: FeedbackMensagemIn, usuario: dict = Depends(exigir_aprovado)
):
    """Registra (ou remove, com nulo) o voto 👍/👎 do usuário numa resposta da IA."""
    _exigir_acesso_via_mensagem_ia(id_mensagem_ia, usuario)
    try:
        resultado = registrar_feedback_mensagem(
            id_mensagem_ia, usuario["id_usuario"], payload.tp_feedback
        )
    except ErroProvedorIA as exc:
        detalhe = str(exc)
        codigo = 404 if "não encontrada" in detalhe else 400
        raise HTTPException(status_code=codigo, detail=detalhe)
    return {"ok": True, **resultado}


@router.get("/api/investigacao/ia/conversas/{id_conversa_ia}/mensagens")
def mensagens_ia(id_conversa_ia: int, usuario: dict = Depends(exigir_aprovado)):
    """Lista as mensagens de uma conversa de IA."""
    _exigir_acesso_via_conversa_ia(id_conversa_ia, usuario)
    return listar_mensagens_ia(id_conversa_ia)


@router.post("/api/investigacao/ia/conversas/{id_conversa_ia}/mensagens")
def enviar_mensagem_ia(id_conversa_ia: int, payload: NovaMensagemIn, usuario: dict = Depends(exigir_aprovado)):
    """Envia uma mensagem do usuário, chama a IA configurada e devolve a resposta (ambas já gravadas)."""
    _exigir_acesso_via_conversa_ia(id_conversa_ia, usuario)
    try:
        return enviar_mensagem_usuario(
            id_conversa_ia, payload.texto.strip(), usuario["id_usuario"],
            payload.incluir_conteudo, payload.exibir_prompt,
            escopos=(
                [{"tp_escopo": e.tp_escopo, "id_escopo": e.id_escopo} for e in payload.escopos]
                if payload.escopos else None
            ),
            contexto_tela=payload.contexto_tela,
        )
    except ConversaOcupada as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ErroProvedorIA as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/api/investigacao/ia/processamentos/{id_processamento}")
def status_processamento(id_processamento: int, usuario: dict = Depends(exigir_aprovado)):
    """Status de um processamento em lotes do modo "conteúdo completo" — o front faz polling daqui
    até concluir, e então recarrega as mensagens da conversa para mostrar a resposta final."""
    proc = obter_processamento(id_processamento)
    if proc is None:
        raise HTTPException(status_code=404, detail="Processamento não encontrado.")
    _exigir_acesso_via_conversa_ia(proc["id_conversa_ia"], usuario)
    return proc


@router.get("/api/investigacao/ia/processamentos/{id_processamento}/eventos")
async def eventos_processamento(id_processamento: int, usuario: dict = Depends(exigir_aprovado)):
    """Mesma informação da rota acima, empurrada por SSE enquanto o turno roda — é o caminho
    normal do front; o polling de 2,5 s ficou como reserva para quando o EventSource falha
    (proxy que não deixa passar text/event-stream, navegador antigo).

    A permissão é conferida aqui, antes de abrir o fluxo: depois de a resposta começar a sair não
    há mais como devolver 403/404."""
    proc = await run_in_threadpool(obter_processamento, id_processamento)
    if proc is None:
        raise HTTPException(status_code=404, detail="Processamento não encontrado.")
    await run_in_threadpool(_exigir_acesso_via_conversa_ia, proc["id_conversa_ia"], usuario)
    return StreamingResponse(
        fluxo_processamento(obter_processamento, id_processamento),
        media_type="text/event-stream",
        headers=CABECALHOS_SSE,
    )


@router.get("/api/investigacao/ia/parametros")
def parametros_chat_ia(usuario: dict = Depends(exigir_aprovado)):
    """Réguas que a tela do chat precisa para exibir o gasto de cada turno:

    - qt_janela_contexto: janela do modelo que ESTE usuário usa (a IA própria dele, se tiver) —
      é contra ela que a bolha avisa que o contexto está enchendo;
    - vl_cotacao_brl: cotação do dólar, quando houver AI_COTACAO_BRL no .env. A auditoria grava o
      custo em US$ (é o preço tabelado dos provedores); com a cotação configurada a tela mostra
      também o valor em reais. Sem ela, só o US$ — melhor do que inventar uma taxa de câmbio.
    """
    env = parse_env(read_env_text())
    try:
        cotacao = float(str(env.get("AI_COTACAO_BRL", "")).strip().replace(",", ".") or 0)
    except ValueError:
        cotacao = 0.0
    return {
        "qt_janela_contexto": janela_contexto_configurada(usuario["id_usuario"]),
        "vl_cotacao_brl": cotacao if cotacao > 0 else None,
    }


@router.post("/api/investigacao/ia/processamentos/{id_processamento}/cancelar")
def cancelar_processamento(id_processamento: int, usuario: dict = Depends(exigir_aprovado)):
    """Pede o cancelamento do turno em andamento. O worker detecta nos checkpoints (antes de cada
    parte e durante o streaming) e encerra sem gravar resposta; o front vê o status 'cancelado'."""
    proc = obter_processamento(id_processamento)
    if proc is None:
        raise HTTPException(status_code=404, detail="Processamento não encontrado.")
    _exigir_acesso_via_conversa_ia(proc["id_conversa_ia"], usuario)
    return {"ok": True, "cancelando": solicitar_cancelamento(id_processamento)}


@router.get("/api/investigacao/ia/conversas/{id_conversa_ia}/auditoria")
def auditoria_conversa(id_conversa_ia: int, usuario: dict = Depends(exigir_aprovado)):
    """Lista auditoria (tokens, custo, tempo) dos turnos da conversa de IA."""
    _exigir_acesso_via_conversa_ia(id_conversa_ia, usuario)
    resultado = listar_auditoria(id_conversa_ia=id_conversa_ia, limite=100)
    # Retorna apenas os itens, não o formato completo, para compatibilidade com o frontend
    return resultado.get("itens", [])


@router.get("/api/investigacao/ia/prompts/{id_arquivo_prompt}/download")
def download_arquivo_prompt(id_arquivo_prompt: int, usuario: dict = Depends(exigir_aprovado)):
    """Serve o arquivo .md gerado pelo prompt para download."""
    arquivo = obter_arquivo_prompt(id_arquivo_prompt)
    if arquivo is None:
        raise HTTPException(status_code=404, detail="Arquivo de prompt não encontrado.")
    _exigir_acesso_via_conversa_ia(arquivo["id_conversa_ia"], usuario)
    caminho = arquivo["caminho_arquivo"]
    # sem o header customizado: o FileResponse já monta o Content-Disposition em
    # filename*=UTF-8'' (RFC 5987) a partir de `filename=` — o header cru de antes não só
    # quebrava com qualquer acento no nome (header latin-1) como sobrescrevia o correto
    return FileResponse(
        path=caminho,
        filename=arquivo["no_arquivo"],
        media_type="text/markdown",
    )


@router.get("/api/investigacao/ia/relatorios/{id_relatorio_ia}/download")
def download_relatorio_ia(id_relatorio_ia: int, usuario: dict = Depends(exigir_aprovado)):
    """Serve o .docx gerado quando o investigador pede um relatório no chat (link no fim da própria
    resposta — ver funcoes/ia_relatorio_docx.py).

    O acesso é conferido de novo aqui, pelo inquérito gravado na linha: entre gerar e baixar pode
    ter passado tempo, e a permissão sobre o inquérito pode ter mudado. Confere pelo ID_INQUERITO,
    não pela conversa."""
    relatorio = obter_relatorio_ia(id_relatorio_ia)
    if relatorio is None or relatorio["id_inquerito"] is None:
        raise HTTPException(status_code=404, detail="Relatório não encontrado.")
    if not usuario_tem_acesso_inquerito(relatorio["id_inquerito"], usuario):
        raise HTTPException(status_code=403, detail="Sem permissão para este inquérito.")
    exigir_ia_inquerito(relatorio["id_inquerito"], usuario)
    caminho = Path(relatorio["caminho_arquivo"])
    if not caminho.exists():
        # arquivo já limpo por antiguidade — dizer isso é melhor que um 500 de arquivo sumido
        raise HTTPException(
            status_code=404,
            detail="O arquivo deste relatório não está mais disponível. Peça o relatório de novo no chat.",
        )
    nome = relatorio["no_arquivo"]
    return FileResponse(
        path=str(caminho),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(nome)}"},
    )


@router.get("/api/investigacao/ia/planilhas/{id_planilha_ia}/download")
def download_planilha_ia(id_planilha_ia: int, usuario: dict = Depends(exigir_aprovado)):
    """Serve o .xlsx do job de consolidação pedido no chat (ferramenta gerar_planilha_consolidada —
    ver funcoes/ia_planilha_utils.py). Link no cartão de progresso da conversa.

    Acesso conferido de novo pelo INQUERITO gravado na linha (não pela conversa): entre o job
    terminar e o clique no link pode ter passado tempo — e mudado — o acesso de quem clica."""
    planilha = obter_planilha_ia(id_planilha_ia)
    if planilha is None or planilha["id_inquerito"] is None:
        raise HTTPException(status_code=404, detail="Planilha não encontrada.")
    if not usuario_tem_acesso_inquerito(planilha["id_inquerito"], usuario):
        raise HTTPException(status_code=403, detail="Sem permissão para este inquérito.")
    exigir_ia_inquerito(planilha["id_inquerito"], usuario)
    caminho = Path(planilha["caminho_arquivo"])
    if not caminho.exists():
        raise HTTPException(
            status_code=404,
            detail="O arquivo desta planilha não está mais disponível. Peça a consolidação de novo no chat.",
        )
    nome = planilha["no_arquivo"]
    return FileResponse(
        path=str(caminho),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(nome)}"},
    )

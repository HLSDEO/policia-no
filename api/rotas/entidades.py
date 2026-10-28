"""Rotas de entidades: anotação livre do investigador em qualquer dado extraído
(mensagem, contato, ligação, localização, notificação...), relações manuais entre
entidades, revisão das sugestões da IA e a camada de entidades canônicas — tudo
que a Mesa de Inquérito desenha."""

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field

from funcoes.auth_utils import exigir_aprovado
from funcoes.canonicas_utils import (
    adicionar_evidencia,
    atualizar_posicao_canonica,
    criar_canonica,
    criar_relacao_canonica,
    fundir_canonicas,
    listar_canonicas,
    listar_evidencias,
    listar_relacoes_canonicas,
    listar_vinculos_evidencia,
    obter_id_inquerito_de_canonica,
    obter_id_inquerito_de_relacao_canonica,
    remover_canonica,
    remover_relacao_canonica,
)
from funcoes.entidades_utils import (
    TIPOS_ORIGEM_VALIDOS,
    atualizar_entidade,
    atualizar_posicao,
    criar_entidade,
    criar_relacao,
    listar_entidades,
    listar_entidades_inquerito,
    listar_relacoes,
    obter_id_inquerito_de_entidade,
    obter_id_inquerito_de_relacao,
    remover_entidade,
    remover_relacao,
    revisar_entidade,
    revisar_relacao,
)
from funcoes.identificacao_pessoas import IdentificacaoEmAndamento, identificar_pessoas_inquerito
from funcoes.imagens_mesa import (
    TAMANHO_MAX_ENVIO,
    TP_NO_VALIDOS,
    listar_imagens_inquerito,
    obter_imagem_no,
    remover_imagem_no,
    salvar_imagem_no,
)
from funcoes.investigacao_utils import exigir_acesso_inquerito, exigir_escrita_inquerito, usuario_tem_acesso_inquerito
from funcoes.mesa_visoes_utils import apagar_visao, listar_visoes, obter_visao, salvar_visao
from funcoes.upload_utils import ler_limitado

router = APIRouter()


def _exigir_acesso(id_inquerito: int | None, usuario: dict, detalhe: str = "Sem permissão para este inquérito.", *, escrita: bool = False):
    """Guard comum das rotas que recebem só o id do recurso: 404 se não existe, 403 se sem acesso."""
    if id_inquerito is None:
        raise HTTPException(status_code=404, detail="Não encontrado.")
    if not usuario_tem_acesso_inquerito(id_inquerito, usuario, escrita=escrita):
        raise HTTPException(status_code=403, detail=detalhe)


def _inquerito_do_no(tp_no: str, id_no: int) -> int | None:
    """Resolve o inquérito dono de um nó da mesa, seja ele de qual camada for — é o que
    permite às rotas de imagem terem uma URL só para os dois tipos de nó."""
    if tp_no not in TP_NO_VALIDOS:
        raise HTTPException(status_code=400, detail="Tipo de nó inválido.")
    if tp_no == "canonica":
        return obter_id_inquerito_de_canonica(id_no)
    return obter_id_inquerito_de_entidade(id_no)


class EntidadeIn(BaseModel):
    """Corpo aceito pelo POST que cria uma entidade."""

    id_inquerito: int
    tp_origem: str = Field(..., max_length=30)
    id_origem: int
    comentario: str = Field(..., min_length=1, max_length=4000)


class ComentarioIn(BaseModel):
    comentario: str = Field(..., min_length=1, max_length=4000)


class PosicaoIn(BaseModel):
    """Posição fixada de um nó arrastado na mesa (None limpa e volta pro layout automático)."""

    pos_x: float | None = None
    pos_y: float | None = None


class RevisaoIn(BaseModel):
    revisao: str = Field(..., pattern="^(sugerida|confirmada|descartada)$")


class RelacaoIn(BaseModel):
    """Corpo do POST que cria uma relação manual entre duas entidades da mesa."""

    id_inquerito: int
    id_entidade_origem: int
    id_entidade_destino: int
    tipo_relacao: str = Field(..., min_length=1, max_length=200)


class CanonicaIn(BaseModel):
    """Corpo do POST que cria uma entidade canônica manualmente."""

    id_inquerito: int
    tp_entidade: str = Field(..., max_length=30)
    nome: str = Field(..., min_length=1, max_length=200)
    observacao: str | None = Field(None, max_length=4000)


class RelacaoCanonicaIn(BaseModel):
    id_inquerito: int
    id_canonica_origem: int
    id_canonica_destino: int
    tipo_relacao: str = Field(..., min_length=1, max_length=200)


class EvidenciaIn(BaseModel):
    """Pendura um registro extraído como evidência de uma canônica (liga os dois no grafo)."""

    tp_origem: str = Field(..., max_length=30)
    id_origem: int
    detalhe: str | None = Field(None, max_length=300)


class FusaoCanonicaIn(BaseModel):
    """Corpo do POST que funde uma canônica (origem, na URL) em outra (destino)."""

    id_canonica_destino: int


@router.get("/api/investigacao/inqueritos/{id_inquerito}/entidades")
def entidades(id_inquerito: int, tp_origem: str, usuario: dict = Depends(exigir_acesso_inquerito)):
    """Lista as entidades de um tipo de origem, para marcar registros já comentados."""
    if tp_origem not in TIPOS_ORIGEM_VALIDOS:
        raise HTTPException(status_code=400, detail="Tipo de origem inválido.")
    return listar_entidades(id_inquerito, tp_origem)


@router.post("/api/investigacao/entidades")
def criar(payload: EntidadeIn, usuario: dict = Depends(exigir_aprovado)):
    """Cria uma entidade (comentário) presa a um registro de origem."""
    if not usuario_tem_acesso_inquerito(payload.id_inquerito, usuario, escrita=True):
        raise HTTPException(status_code=403, detail="Sem permissão para este inquérito.")
    try:
        id_entidade = criar_entidade(
            payload.id_inquerito, payload.tp_origem, payload.id_origem, payload.comentario, usuario["id_usuario"]
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "id_entidade": id_entidade}


@router.delete("/api/investigacao/entidades/{id_entidade}")
def remover(id_entidade: int, usuario: dict = Depends(exigir_aprovado)):
    """Remove uma entidade (e as relações que a tocam)."""
    _exigir_acesso(obter_id_inquerito_de_entidade(id_entidade), usuario, escrita=True)
    remover_entidade(id_entidade)
    return {"ok": True}


@router.post("/api/investigacao/entidades/{id_entidade}/comentario")
def editar_comentario(id_entidade: int, payload: ComentarioIn, usuario: dict = Depends(exigir_aprovado)):
    """Troca o comentário de uma entidade."""
    _exigir_acesso(obter_id_inquerito_de_entidade(id_entidade), usuario, escrita=True)
    try:
        atualizar_entidade(id_entidade, payload.comentario)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


@router.post("/api/investigacao/entidades/{id_entidade}/posicao")
def fixar_posicao(id_entidade: int, payload: PosicaoIn, usuario: dict = Depends(exigir_aprovado)):
    """Fixa a posição de um nó arrastado na mesa (persiste entre aberturas da página)."""
    _exigir_acesso(obter_id_inquerito_de_entidade(id_entidade), usuario, escrita=True)
    atualizar_posicao(id_entidade, payload.pos_x, payload.pos_y)
    return {"ok": True}


@router.post("/api/investigacao/entidades/{id_entidade}/revisao")
def revisar(id_entidade: int, payload: RevisaoIn, usuario: dict = Depends(exigir_aprovado)):
    """Confirma ou descarta uma entidade sugerida pela busca automática."""
    _exigir_acesso(obter_id_inquerito_de_entidade(id_entidade), usuario, escrita=True)
    revisar_entidade(id_entidade, payload.revisao)
    return {"ok": True}


@router.post("/api/investigacao/relacoes")
def criar_relacao_manual(payload: RelacaoIn, usuario: dict = Depends(exigir_aprovado)):
    """Cria uma relação manual (aresta) entre duas entidades da mesa."""
    if not usuario_tem_acesso_inquerito(payload.id_inquerito, usuario, escrita=True):
        raise HTTPException(status_code=403, detail="Sem permissão para este inquérito.")
    try:
        id_relacao = criar_relacao(
            payload.id_inquerito, payload.id_entidade_origem, payload.id_entidade_destino,
            payload.tipo_relacao, usuario["id_usuario"],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "id_relacao": id_relacao}


@router.delete("/api/investigacao/relacoes/{id_relacao}")
def remover_relacao_rota(id_relacao: int, usuario: dict = Depends(exigir_aprovado)):
    """Remove uma relação entre entidades."""
    _exigir_acesso(obter_id_inquerito_de_relacao(id_relacao), usuario, escrita=True)
    remover_relacao(id_relacao)
    return {"ok": True}


@router.post("/api/investigacao/relacoes/{id_relacao}/revisao")
def revisar_relacao_rota(id_relacao: int, payload: RevisaoIn, usuario: dict = Depends(exigir_aprovado)):
    """Confirma ou descarta uma relação sugerida pela busca automática."""
    _exigir_acesso(obter_id_inquerito_de_relacao(id_relacao), usuario, escrita=True)
    revisar_relacao(id_relacao, payload.revisao)
    return {"ok": True}


@router.get("/api/investigacao/inqueritos/{id_inquerito}/grafo/resumo")
def grafo_resumo(id_inquerito: int, usuario: dict = Depends(exigir_acesso_inquerito)):
    """Resumo leve do grafo (contagens por tipo, histograma de grau, totais).

    A mesa carrega isto primeiro para decidir o recorte (ex.: inquérito de 6k nós
    abre nos hubs em vez de tentar baixar/desenhar tudo)."""
    from funcoes.canonicas_utils import resumo_grafo
    return resumo_grafo(id_inquerito)


@router.get("/api/investigacao/inqueritos/{id_inquerito}/grafo")
def grafo(
    id_inquerito: int,
    usuario: dict = Depends(exigir_acesso_inquerito),
    tipos_canonica: str | None = None,
    tipos_entidade: str | None = None,
    busca: str | None = None,
    min_evidencias: int = 0,
    limite: int | None = None,
    docs: str | None = None,
    tipos_doc: str | None = None,
    min_docs: int = 0,
    busca_doc: str | None = None,
    fontes: str | None = None,
):
    """Tudo que a Mesa de Inquérito desenha: entidades-comentário + relações, entidades
    canônicas + relações entre elas, e os vínculos de evidência que ligam as duas camadas.
    Vem junto o grafo derivado dos documentos genéricos anexados (pessoas/achados que a IA
    extraiu de cada um) — é o que dá rede a um caso de inteligência feito só de documentos.

    Query params opcionais (recorte server-side para grafos grandes): ``tipos_canonica``
    e ``tipos_entidade`` (csv), ``busca`` (ILIKE em nome/comentário), ``min_evidencias``
    (corta canônicas da cauda longa), ``limite`` (teto de nós por camada). Para a camada
    de documentos: ``docs`` (csv de id_documento — só esses), ``tipos_doc`` (csv de
    tp_achado), ``min_docs`` (pessoa/achado entra se citado em N+ documentos),
    ``busca_doc`` (substring em nome/valor). ``fontes`` (csv de ``tp:id`` —
    extracao/rif/simba/ipl/irpf/ipei/telefonia/documento, painel "🗂 Fontes" da mesa, ver
    funcoes/fontes_mesa_utils) recorta as camadas do banco às entidades/evidências nascidas
    naquelas fontes e a camada de documentos aos documentos marcados (presente o param, a
    camada de documentos ignora ``docs`` e usa só os ``documento:`` dele). Sem params,
    devolve tudo como antes."""
    from funcoes.documentos_utils import grafo_documentos_inquerito
    from funcoes.fontes_mesa_utils import parse_fontes

    def _csv(v: str | None) -> list[str] | None:
        if not v:
            return None
        vals = [s.strip() for s in v.split(",") if s.strip()]
        return vals or None

    def _csv_int(v: str | None) -> list[int] | None:
        if not v:
            return None
        vals = [int(s) for s in v.split(",") if s.strip().lstrip("-").isdigit()]
        return vals or None

    try:
        fontes_map = parse_fontes(fontes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    fontes_ativo = bool(fontes_map)
    # 'documento' resolve na camada de documentos (docs=), não por tp_origem.
    docs_fonte = fontes_map.pop("documento", None) if fontes_map else None
    fontes_sql = fontes_map or None

    if fontes_ativo and docs_fonte is None:
        # fontes presente sem nenhum documento marcado: a camada de documentos não entra.
        documentos_camada: dict = {"nos": [], "arestas": []}
    else:
        docs_sel = list(docs_fonte) if docs_fonte else _csv_int(docs)
        documentos_camada = grafo_documentos_inquerito(
            id_inquerito, docs=docs_sel, tipos_achado=_csv(tipos_doc),
            min_docs=min_docs or 0, busca=busca_doc,
        )

    return {
        "entidades": listar_entidades_inquerito(
            id_inquerito, tipos=_csv(tipos_entidade), busca=busca, limite=limite,
            fontes=fontes_sql,
        ),
        "relacoes": listar_relacoes(id_inquerito),
        "canonicas": listar_canonicas(
            id_inquerito, tipos=_csv(tipos_canonica), busca=busca,
            min_evidencias=min_evidencias or 0, limite=limite,
            fontes=fontes_sql,
        ),
        "relacoes_canonicas": listar_relacoes_canonicas(id_inquerito),
        "vinculos_evidencia": listar_vinculos_evidencia(id_inquerito),
        "imagens": listar_imagens_inquerito(id_inquerito),
        "documentos": documentos_camada,
    }


# ---------- entidades canônicas ----------


@router.post("/api/investigacao/canonicas")
def criar_canonica_rota(payload: CanonicaIn, usuario: dict = Depends(exigir_aprovado)):
    """Cria uma entidade canônica manualmente (pessoa, telefone, local, veículo...)."""
    if not usuario_tem_acesso_inquerito(payload.id_inquerito, usuario, escrita=True):
        raise HTTPException(status_code=403, detail="Sem permissão para este inquérito.")
    try:
        id_canonica = criar_canonica(
            payload.id_inquerito, payload.tp_entidade, payload.nome,
            observacao=payload.observacao, id_usuario=usuario["id_usuario"],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "id_canonica": id_canonica}


@router.delete("/api/investigacao/canonicas/{id_canonica}")
def remover_canonica_rota(id_canonica: int, usuario: dict = Depends(exigir_aprovado)):
    """Remove uma canônica (evidências e relações dela caem junto)."""
    _exigir_acesso(obter_id_inquerito_de_canonica(id_canonica), usuario, escrita=True)
    remover_canonica(id_canonica)
    return {"ok": True}


@router.post("/api/investigacao/canonicas/{id_canonica}/posicao")
def fixar_posicao_canonica(id_canonica: int, payload: PosicaoIn, usuario: dict = Depends(exigir_aprovado)):
    """Fixa a posição de uma canônica arrastada na mesa (persiste entre aberturas da página)."""
    _exigir_acesso(obter_id_inquerito_de_canonica(id_canonica), usuario, escrita=True)
    atualizar_posicao_canonica(id_canonica, payload.pos_x, payload.pos_y)
    return {"ok": True}


@router.post("/api/investigacao/canonicas/{id_canonica}/fundir")
def fundir_canonica_rota(id_canonica: int, payload: FusaoCanonicaIn, usuario: dict = Depends(exigir_aprovado)):
    """Funde a canônica ID_CANONICA (origem) na canônica do payload (destino); a origem some,
    o destino herda evidências, identificadores e relações dela."""
    id_inquerito = obter_id_inquerito_de_canonica(id_canonica)
    _exigir_acesso(id_inquerito, usuario, escrita=True)
    if obter_id_inquerito_de_canonica(payload.id_canonica_destino) != id_inquerito:
        raise HTTPException(status_code=400, detail="A entidade de destino não é deste inquérito.")
    try:
        resumo = fundir_canonicas(id_canonica, payload.id_canonica_destino, id_inquerito)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, **resumo}


@router.get("/api/investigacao/canonicas/{id_canonica}/evidencias")
def evidencias(id_canonica: int, usuario: dict = Depends(exigir_aprovado)):
    """Evidências (registros de origem) de uma canônica, para o modal de detalhes."""
    _exigir_acesso(obter_id_inquerito_de_canonica(id_canonica), usuario)
    return listar_evidencias(id_canonica)


@router.post("/api/investigacao/canonicas/{id_canonica}/evidencias")
def adicionar_evidencia_rota(id_canonica: int, payload: EvidenciaIn, usuario: dict = Depends(exigir_aprovado)):
    """Pendura um registro extraído como evidência da canônica (a mesa usa para ligar
    uma entidade-comentário a uma canônica)."""
    id_inquerito = obter_id_inquerito_de_canonica(id_canonica)
    _exigir_acesso(id_inquerito, usuario, escrita=True)
    try:
        adicionar_evidencia(id_canonica, id_inquerito, payload.tp_origem, payload.id_origem, payload.detalhe)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


@router.post("/api/investigacao/canonicas/relacoes")
def criar_relacao_canonica_rota(payload: RelacaoCanonicaIn, usuario: dict = Depends(exigir_aprovado)):
    """Cria uma relação manual entre duas canônicas."""
    if not usuario_tem_acesso_inquerito(payload.id_inquerito, usuario, escrita=True):
        raise HTTPException(status_code=403, detail="Sem permissão para este inquérito.")
    try:
        id_relacao = criar_relacao_canonica(
            payload.id_inquerito, payload.id_canonica_origem, payload.id_canonica_destino,
            payload.tipo_relacao, usuario["id_usuario"],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "id_relacao": id_relacao}


@router.delete("/api/investigacao/canonicas/relacoes/{id_relacao}")
def remover_relacao_canonica_rota(id_relacao: int, usuario: dict = Depends(exigir_aprovado)):
    """Remove uma relação entre canônicas."""
    _exigir_acesso(obter_id_inquerito_de_relacao_canonica(id_relacao), usuario, escrita=True)
    remover_relacao_canonica(id_relacao)
    return {"ok": True}


# ---------- imagem do nó (a foto que substitui a bolinha no grafo) ----------


@router.post("/api/investigacao/nos/{tp_no}/{id_no}/imagem")
def enviar_imagem_no(
    tp_no: str, id_no: int, arquivo: UploadFile = File(...), usuario: dict = Depends(exigir_aprovado),
):
    """Põe (ou troca) a foto de um nó da mesa. TP_NO é a camada do nó: 'canonica' (ENT_TB05)
    ou 'entidade' (ENT_TB01). O arquivo é reduzido e reencodado em JPEG antes de ser gravado
    (ver funcoes/imagens_mesa.py) — o original não fica no banco."""
    id_inquerito = _inquerito_do_no(tp_no, id_no)
    _exigir_acesso(id_inquerito, usuario, escrita=True)
    # em blocos, com o mesmo teto que imagens_mesa.salvar_imagem_no já aplicava — só que agora
    # ANTES de alocar o corpo inteiro, e não depois. Ver funcoes/upload_utils.py.
    try:
        conteudo = ler_limitado(arquivo.file, TAMANHO_MAX_ENVIO, "Imagem")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        arquivo.file.close()
    try:
        return {"ok": True, **salvar_imagem_no(
            id_inquerito, tp_no, id_no, conteudo, arquivo.filename, usuario["id_usuario"]
        )}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/api/investigacao/nos/{tp_no}/{id_no}/imagem")
def imagem_no(tp_no: str, id_no: int, usuario: dict = Depends(exigir_aprovado)):
    """Devolve a foto do nó. O front busca uma vez por nó e guarda em data URL — é o formato
    que o PNG exportado precisa, porque o SVG rasterizado não vai à rede buscar imagem."""
    _exigir_acesso(_inquerito_do_no(tp_no, id_no), usuario)
    info = obter_imagem_no(tp_no, id_no)
    if info is None:
        raise HTTPException(status_code=404, detail="Este nó não tem imagem.")
    return Response(
        content=info["conteudo"],
        media_type=info["tp_mime"],
        # privada (é dado de inquérito) e revalidada pela query ?v=dt_envio que a mesa manda
        headers={"Cache-Control": "private, max-age=3600"},
    )


@router.delete("/api/investigacao/nos/{tp_no}/{id_no}/imagem")
def remover_imagem_no_rota(tp_no: str, id_no: int, usuario: dict = Depends(exigir_aprovado)):
    """Tira a foto do nó — ele volta a ser desenhado com a cor e o ícone do tipo."""
    _exigir_acesso(_inquerito_do_no(tp_no, id_no), usuario, escrita=True)
    remover_imagem_no(tp_no, id_no)
    return {"ok": True}


@router.post("/api/investigacao/inqueritos/{id_inquerito}/identificar-pessoas")
def identificar_pessoas(id_inquerito: int, usuario: dict = Depends(exigir_escrita_inquerito)):
    """Reprocessa a identificação de pessoas de todas as extrações do inquérito — a mesma
    etapa que roda sozinha ao final de cada extração (ver funcoes/identificacao_pessoas.py).
    Existe para os inquéritos cujas extrações são anteriores a ela. Idempotente.

    Roda inline, e num inquérito grande leva minutos. O 409 abaixo é o que impede o segundo
    clique (ou o clique de um colega) de abrir uma varredura concorrente sobre as mesmas
    tabelas: as duas disputariam os locks de escrita das ENT_TB* e a primeira morreria por
    lock_timeout, sem que ninguém entendesse por quê."""
    try:
        return {"ok": True, **identificar_pessoas_inquerito(id_inquerito)}
    except IdentificacaoEmAndamento as exc:
        raise HTTPException(status_code=409, detail=str(exc))


# ---------- visões salvas da mesa (privadas por usuário) ----------

class NovaVisaoIn(BaseModel):
    """Corpo do POST que salva uma visão: um nome e o estado opaco que a tela produziu
    (filtros, fontes, limite, câmera). O backend não entende o estado — só o guarda como JSONB."""

    nome: str = Field(..., min_length=1, max_length=120)
    estado: dict


@router.get("/api/investigacao/inqueritos/{id_inquerito}/mesa-visoes")
def listar_mesa_visoes(id_inquerito: int, usuario: dict = Depends(exigir_acesso_inquerito)):
    """As visões salvas do PRÓPRIO usuário naquele inquérito (o dropdown "Visões" da mesa).
    Privadas: listar_visoes filtra por ID_USUARIO, então nunca traz as de outro investigador."""
    return listar_visoes(id_inquerito, usuario["id_usuario"])


@router.post("/api/investigacao/inqueritos/{id_inquerito}/mesa-visoes")
def criar_mesa_visao(
    id_inquerito: int, payload: NovaVisaoIn, usuario: dict = Depends(exigir_acesso_inquerito),
):
    """Salva (ou substitui, pelo nome) uma visão do usuário. O estado é JSONB opaco. Basta acesso
    de leitura ao inquérito: a visão é dado PESSOAL do investigador (ENT_TB10 filtrada por ele),
    não mexe em nenhum conteúdo do caso — por isso não exige escrita."""
    try:
        id_visao = salvar_visao(id_inquerito, usuario["id_usuario"], payload.nome, payload.estado)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "id_visao": id_visao}


@router.get("/api/investigacao/inqueritos/{id_inquerito}/mesa-visoes/{id_visao}")
def detalhe_mesa_visao(
    id_inquerito: int, id_visao: int, usuario: dict = Depends(exigir_acesso_inquerito),
):
    """Uma visão com o estado serializado, para aplicar na tela. Privada: se não for do usuário
    (ou não existir), cai em 404 — sem revelar que a visão existe nas mãos de outra pessoa."""
    visao = obter_visao(id_visao, usuario["id_usuario"])
    if visao is None:
        raise HTTPException(status_code=404, detail="Visão não encontrada.")
    return visao


@router.delete("/api/investigacao/inqueritos/{id_inquerito}/mesa-visoes/{id_visao}")
def apagar_mesa_visao(
    id_inquerito: int, id_visao: int, usuario: dict = Depends(exigir_acesso_inquerito),
):
    """Apaga a visão — só se for do próprio usuário (ID_USUARIO na WHERE). Como o salvar, é dado
    pessoal do investigador, não conteúdo do caso: basta acesso de leitura."""
    apagar_visao(id_visao, usuario["id_usuario"])
    return {"ok": True}

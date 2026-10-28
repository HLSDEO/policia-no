"""Rotas dos marcadores nomeados (as "tags" do IPED) — ver funcoes/marcadores_utils.py.

Duas checagens em toda rota, não uma: acesso ao INQUÉRITO (como no resto do app) e, quando a
rota recebe só o id do marcador, que ele é do USUÁRIO LOGADO. Marcador é pessoal: sem a segunda
checagem, um id chutado no payload renomearia ou apagaria a etiqueta de um colega no mesmo caso.

O router é registrado com exigir_aprovado no main.py.
"""

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from funcoes.auth_utils import exigir_aprovado
from funcoes.investigacao_utils import exigir_acesso_inquerito, exigir_escrita_inquerito, usuario_tem_acesso_inquerito
from funcoes.marcadores_utils import (
    aplicar_marcadores_lote,
    atualizar_marcador,
    criar_marcador,
    definir_marcadores_da_marcacao,
    ids_marcacoes_dos_marcadores,
    listar_marcadores,
    mesclar_marcadores,
    obter_marcador,
    remover_marcador,
    sumario_mensageiro,
)
from funcoes.relatorios_docx import gerar_marcadores_docx
from funcoes.relatorios_utils import (
    listar_chat_marcado,
    listar_conversas_marcadas,
    obter_id_inquerito_de_marcacao,
    obter_numero_inquerito,
)

router = APIRouter()


class MarcadorIn(BaseModel):
    nome: str = Field(..., max_length=120)
    cor: str = Field(..., max_length=7)
    descricao: str | None = Field(None, max_length=2000)


class MesclarIn(BaseModel):
    id_destino: int


class MarcadoresDaMarcacaoIn(BaseModel):
    ids_marcadores: list[int] = Field(default_factory=list)


class ItemLote(BaseModel):
    tp_origem: str = Field(..., max_length=30)
    id_origem: int


class LoteIn(BaseModel):
    itens: list[ItemLote] = Field(default_factory=list)
    ids_marcadores: list[int] = Field(default_factory=list)
    acao: str = Field("adicionar", pattern="^(adicionar|remover)$")
    comentario: str | None = Field(None, max_length=4000)
    cor: str | None = Field(None, max_length=7)


def _meu_marcador(id_marcador: int, usuario: dict, *, escrita: bool = False) -> dict:
    """404 se não existe, 403 se é de outro usuário ou de um inquérito sem acesso."""
    marcador = obter_marcador(id_marcador)
    if marcador is None:
        raise HTTPException(status_code=404, detail="Marcador não encontrado.")
    if not usuario_tem_acesso_inquerito(marcador["id_inquerito"], usuario, escrita=escrita):
        raise HTTPException(status_code=403, detail="Sem permissão para este inquérito.")
    if marcador["id_usuario"] != usuario["id_usuario"]:
        raise HTTPException(status_code=403, detail="Este marcador é de outro usuário.")
    return marcador


# =====================================================================
# CRUD dos marcadores
# =====================================================================


@router.get("/api/investigacao/inqueritos/{id_inquerito}/marcadores")
def marcadores(id_inquerito: int, usuario: dict = Depends(exigir_acesso_inquerito)):
    """Meus marcadores neste inquérito, com a contagem de registros de cada um."""
    return listar_marcadores(id_inquerito, usuario["id_usuario"])


@router.post("/api/investigacao/inqueritos/{id_inquerito}/marcadores")
def criar_marcador_rota(id_inquerito: int, payload: MarcadorIn,
                        usuario: dict = Depends(exigir_escrita_inquerito)):
    try:
        marcador = criar_marcador(
            id_inquerito, usuario["id_usuario"], payload.nome, payload.cor, payload.descricao
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "marcador": marcador}


@router.post("/api/investigacao/marcadores/{id_marcador}")
def atualizar_marcador_rota(id_marcador: int, payload: MarcadorIn,
                            usuario: dict = Depends(exigir_aprovado)):
    _meu_marcador(id_marcador, usuario, escrita=True)
    try:
        atualizar_marcador(id_marcador, payload.nome, payload.cor, payload.descricao)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


@router.delete("/api/investigacao/marcadores/{id_marcador}")
def remover_marcador_rota(id_marcador: int, usuario: dict = Depends(exigir_aprovado)):
    """Apaga o marcador. As marcações continuam de pé — só perdem esta etiqueta."""
    _meu_marcador(id_marcador, usuario, escrita=True)
    remover_marcador(id_marcador)
    return {"ok": True}


@router.post("/api/investigacao/marcadores/{id_marcador}/mesclar")
def mesclar_rota(id_marcador: int, payload: MesclarIn, usuario: dict = Depends(exigir_aprovado)):
    """Move as aplicações deste marcador para outro e apaga este."""
    origem = _meu_marcador(id_marcador, usuario, escrita=True)
    destino = _meu_marcador(payload.id_destino, usuario, escrita=True)
    if origem["id_inquerito"] != destino["id_inquerito"]:
        raise HTTPException(status_code=400, detail="Os dois marcadores precisam ser do mesmo inquérito.")
    try:
        movidas = mesclar_marcadores(id_marcador, payload.id_destino)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "movidas": movidas}


# =====================================================================
# Aplicação nas marcações
# =====================================================================


@router.post("/api/investigacao/marcacoes/{id_marcacao}/marcadores")
def definir_marcadores_rota(id_marcacao: int, payload: MarcadoresDaMarcacaoIn,
                            usuario: dict = Depends(exigir_aprovado)):
    """Substitui o conjunto de marcadores DESTE usuário nesta marcação."""
    id_inquerito = obter_id_inquerito_de_marcacao(id_marcacao)
    if id_inquerito is None:
        raise HTTPException(status_code=404, detail="Marcação não encontrada.")
    if not usuario_tem_acesso_inquerito(id_inquerito, usuario, escrita=True):
        raise HTTPException(status_code=403, detail="Sem permissão para este inquérito.")
    aplicados = definir_marcadores_da_marcacao(
        id_marcacao, id_inquerito, usuario["id_usuario"], payload.ids_marcadores
    )
    return {"ok": True, "ids_marcadores": aplicados}


@router.post("/api/investigacao/inqueritos/{id_inquerito}/marcadores/lote")
def lote_rota(id_inquerito: int, payload: LoteIn, usuario: dict = Depends(exigir_escrita_inquerito)):
    """Aplica ou remove marcadores em N registros numa requisição só."""
    try:
        resultado = aplicar_marcadores_lote(
            id_inquerito, usuario["id_usuario"],
            [item.model_dump() for item in payload.itens],
            payload.ids_marcadores, payload.acao, payload.comentario, payload.cor,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, **resultado}


# =====================================================================
# Sumário e exportação do mensageiro
# =====================================================================


@router.get("/api/investigacao/inqueritos/{id_inquerito}/marcadores/sumario")
def sumario_rota(id_inquerito: int, fonte: str = "wpp", extracao: int | None = None,
                 q: str | None = None, limit: int | None = None, offset: int | None = None,
                 usuario: dict = Depends(exigir_acesso_inquerito)):
    """A árvore marcador → conversa → mensagens (painel flutuante e página Marcações).

    fonte aceita wpp/sms/msg (painel flutuante) ou todas (página Marcações). q filtra por
    substring e limit/offset paginam as conversas de cada marcador."""
    if limit is not None and (limit < 1 or limit > 2000):
        raise HTTPException(status_code=400, detail="limit deve estar entre 1 e 2000.")
    if offset is not None and offset < 0:
        raise HTTPException(status_code=400, detail="offset deve ser >= 0.")
    try:
        return sumario_mensageiro(
            id_inquerito, usuario["id_usuario"], fonte, extracao,
            q=q, limit=limit, offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/api/investigacao/inqueritos/{id_inquerito}/marcadores/exportacao")
def exportar_rota(id_inquerito: int, marcadores: str | None = None,
                  conteudo_conversas: bool = False,
                  usuario: dict = Depends(exigir_acesso_inquerito)):
    """Exporta em .docx o que está marcado: as CONVERSAS marcadas como tabela de identificação
    (triagem do contato) e as MENSAGENS marcadas como bolhas de chat.

    `marcadores` é uma lista de ids separada por vírgula (vazio = todos os meus).

    `conteudo_conversas=true` acrescenta o fio completo das conversas marcadas. Fica desligado
    por padrão de propósito: marcar uma conversa como "lida" não é pedir 2.000 bolhas no .docx —
    trazer o conteúdo é uma segunda decisão, tomada na hora de exportar."""
    ids = _ids_da_query(marcadores)
    ids_marcacoes = ids_marcacoes_dos_marcadores(id_inquerito, usuario["id_usuario"], ids)
    conversas = listar_chat_marcado(
        id_inquerito, ids_marcacoes, usuario["id_usuario"], expandir_conversas=conteudo_conversas
    )
    conversas_marcadas = listar_conversas_marcadas(id_inquerito, ids_marcacoes, usuario["id_usuario"])
    numero = obter_numero_inquerito(id_inquerito)
    docx_bytes = gerar_marcadores_docx(
        numero, "Mensagens marcadas", conversas, conversas_marcadas, conteudo_conversas
    )
    nome = f"marcados_{(numero or str(id_inquerito)).replace('/', '-')}.docx"
    return Response(
        content=docx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(nome)}"},
    )


def _ids_da_query(valor: str | None) -> list[int] | None:
    """'3,7,12' -> [3, 7, 12]; vazio/ausente -> None (todos)."""
    if not valor:
        return None
    ids = [parte.strip() for parte in valor.split(",") if parte.strip().isdigit()]
    return [int(parte) for parte in ids] or None

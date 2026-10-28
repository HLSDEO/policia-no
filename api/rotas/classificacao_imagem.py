"""Rotas de classificação de imagem (CLIP) e das categorias usadas por ela.

Como em rotas/ocr_imagem.py, nenhuma destas rotas conferia nada além do `Depends(exigir_aprovado)`
global do main.py: qualquer analista aprovado disparava, lia e cancelava a classificação de
qualquer inquérito, e apagava categorias que são configuração global de todo mundo. Agora o job
confere acesso ao inquérito e a escrita de categoria exige admin.
"""

import threading

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from funcoes.auth_utils import exigir_admin, exigir_aprovado
from funcoes import jobs_utils
from funcoes.classificacao_imagem_utils import (
    alternar_ativo_categoria,
    classificar_imagens_pendentes,
    criar_categoria,
    excluir_categoria,
    listar_categorias,
)
from funcoes.investigacao_utils import (
    exigir_escrita_inquerito,
    obter_id_inquerito_de_extracao,
    usuario_tem_acesso_inquerito,
)

router = APIRouter()

# As categorias são configuração GLOBAL: valem para todos os inquéritos de todas as delegacias, e
# a classificação já feita fica presa a elas. Por isso a leitura é aberta a qualquer analista e a
# escrita é de admin — antes disso, um analista apagava uma categoria e o efeito aparecia na tela
# de todas as outras unidades.
router_admin = APIRouter(dependencies=[Depends(exigir_admin)])


class CategoriaIn(BaseModel):
    termo: str = Field(..., min_length=1, max_length=500)
    rotulo_exibicao: str = Field(..., min_length=1, max_length=200)


class ClassificarIn(BaseModel):
    id_inquerito: int
    id_extracao: int | None = Field(default=None)
    batch_size: int = Field(default=16, ge=1, le=128)
    max_imagens: int | None = Field(default=None, ge=1)


@router.get("/api/categorias-imagem")
def listar():
    """Lista todas as categorias de classificação de imagem."""
    return listar_categorias()


@router_admin.post("/api/categorias-imagem")
def criar(payload: CategoriaIn):
    """Cria uma nova categoria de classificação de imagem."""
    return criar_categoria(payload.termo, payload.rotulo_exibicao)


@router_admin.delete("/api/categorias-imagem/{id_categoria}")
def excluir(id_categoria: int):
    """Exclui uma categoria de classificação de imagem."""
    if not excluir_categoria(id_categoria):
        raise HTTPException(status_code=404, detail="Categoria não encontrada.")
    return {"ok": True}


@router_admin.post("/api/categorias-imagem/{id_categoria}/alternar-ativo")
def alternar_ativo(id_categoria: int):
    """Alterna o estado ativo/inativo de uma categoria."""
    resultado = alternar_ativo_categoria(id_categoria)
    if resultado is None:
        raise HTTPException(status_code=404, detail="Categoria não encontrada.")
    return resultado


@router.post("/api/arquivos/classificar-imagens")
def iniciar_classificacao(payload: ClassificarIn, usuario: dict = Depends(exigir_aprovado)):
    """Dispara a classificação de imagens pendentes em background."""
    if not usuario_tem_acesso_inquerito(payload.id_inquerito, usuario, escrita=True):
        raise HTTPException(status_code=403, detail="Sem permissão para alterar este inquérito.")
    if payload.id_extracao is not None:
        if obter_id_inquerito_de_extracao(payload.id_extracao) != payload.id_inquerito:
            raise HTTPException(status_code=404, detail="Extração não encontrada neste inquérito.")

    # nó online + JOBS_NO_ATIVO=tudo → a classificação CLIP roda na estação (o modelo precisa
    # estar no cache HF do nó — ver docker-compose.no.yml; docs/no_descentralizado.md)
    from funcoes import jobs_no

    no = jobs_no.no_para_job(jobs_utils.TP_CLASSIFICACAO, payload.id_inquerito)
    try:
        id_job = jobs_utils.criar(
            jobs_utils.TP_CLASSIFICACAO, usuario["id_usuario"],
            id_inquerito=payload.id_inquerito, id_extracao=payload.id_extracao,
            parametros={"batch_size": payload.batch_size, "max_imagens": payload.max_imagens},
            pesado=not no, id_servidor=no["id_servidor"] if no else None,
        )
    except jobs_utils.JobOcupado as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    if no is not None:
        return {"ok": True, "id_job": id_job,
                "mensagem": f"Classificação iniciada no servidor {no['no_servidor']}."}

    threading.Thread(
        target=classificar_imagens_pendentes,
        args=(id_job, payload.id_inquerito, payload.id_extracao, payload.batch_size,
              payload.max_imagens),
        daemon=True,
    ).start()

    return {"ok": True, "id_job": id_job, "mensagem": "Classificação iniciada em background."}

"""Rotas de OCR das imagens de um inquérito.

Toda rota daqui confere acesso ao inquérito. Antes disso o router só carregava o
`Depends(exigir_aprovado)` global do main.py, o que quer dizer que qualquer analista aprovado
disparava, lia e cancelava OCR de qualquer inquérito do sistema informando o id no corpo — e
`POST /ocr-imagens/resetar` chegava a APAGAR o texto reconhecido de um inquérito inteiro de outra
delegacia. O padrão seguido aqui é o de rotas/comparar_rosto.py: resolver o inquérito dono do id
recebido e chamar usuario_tem_acesso_inquerito.

Progresso, cancelamento e "já existe um em andamento" saíram daqui: vivem na tabela de jobs
(funcoes/jobs_utils.py) e são lidos pelas rotas genéricas de rotas/jobs.py. O POST devolve o
`id_job` que o front usa no polling. Como o escopo do guard passou a ser (tipo, inquérito), dois
analistas de inquéritos diferentes rodam OCR ao mesmo tempo — antes o segundo levava 409.
"""

import threading

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from funcoes import jobs_utils
from funcoes.auth_utils import exigir_aprovado
from funcoes.investigacao_utils import (
    exigir_escrita_inquerito,
    obter_id_inquerito_de_extracao,
    usuario_tem_acesso_inquerito,
)
from funcoes.ocr_utils import (
    ocr_disponivel,
    ocr_imagem_especifica,
    ocr_imagens_pendentes,
    resetar_ocr_imagens,
    status_ocr_extracao,
)

router = APIRouter()


def _exigir_acesso(id_inquerito: int | None, usuario: dict, *, escrita: bool = False) -> None:
    if id_inquerito is None:
        raise HTTPException(status_code=404, detail="Não encontrado.")
    if not usuario_tem_acesso_inquerito(id_inquerito, usuario, escrita=escrita):
        raise HTTPException(
            status_code=403,
            detail="Sem permissão para alterar este inquérito." if escrita else "Sem permissão para este inquérito.",
        )


class OcrIn(BaseModel):
    id_inquerito: int
    id_extracao: int | None = Field(default=None)
    lote: int = Field(default=20, ge=1, le=200)
    max_imagens: int | None = Field(default=None, ge=1)


@router.post("/api/arquivos/ocr-imagens")
def iniciar_ocr(payload: OcrIn, usuario: dict = Depends(exigir_aprovado)):
    """Dispara em background o OCR das imagens ainda não processadas do inquérito.

    Devolve `id_job`: é por ele que a tela acompanha o progresso, em
    GET /api/investigacao/jobs/{id_job}."""
    _exigir_acesso(payload.id_inquerito, usuario, escrita=True)
    if payload.id_extracao is not None:
        # a extração informada tem que ser DESTE inquérito: sem isto o filtro do job passaria a
        # varrer os arquivos de uma extração alheia
        if obter_id_inquerito_de_extracao(payload.id_extracao) != payload.id_inquerito:
            raise HTTPException(status_code=404, detail="Extração não encontrada neste inquérito.")

    if not ocr_disponivel():
        raise HTTPException(
            status_code=503,
            detail="OCR indisponível: sem Tesseract instalado (modo local) e sem URL/modelo de API de visão configurados (modo api).",
        )

    # delegacia com nó online + JOBS_NO_ATIVO=ocr|tudo → o job NASCE para o nó e nenhuma thread
    # sobe aqui: a CPU da estação é quem reconhece as imagens que já estão no disco dela
    # (funcoes/jobs_no.py + funcoes/no_jobs.py; docs/no_descentralizado.md "jobs no nó")
    from funcoes import jobs_no

    no = jobs_no.no_para_job(jobs_utils.TP_OCR, payload.id_inquerito)
    try:
        id_job = jobs_utils.criar(
            jobs_utils.TP_OCR, usuario["id_usuario"],
            id_inquerito=payload.id_inquerito, id_extracao=payload.id_extracao,
            parametros={"lote": payload.lote, "max_imagens": payload.max_imagens},
            pesado=not no, id_servidor=no["id_servidor"] if no else None,
        )
    except jobs_utils.JobOcupado as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    if no is not None:
        return {"ok": True, "id_job": id_job,
                "mensagem": f"OCR iniciado no servidor {no['no_servidor']} (extração na "
                            "delegacia processa na própria estação)."}

    threading.Thread(
        target=ocr_imagens_pendentes,
        args=(id_job, payload.id_inquerito, payload.id_extracao, payload.lote, payload.max_imagens),
        daemon=True,
    ).start()

    return {"ok": True, "id_job": id_job, "mensagem": "OCR iniciado em background."}


class OcrEspecificoIn(BaseModel):
    id_arquivo: int
    id_inquerito: int


@router.post("/api/arquivos/ocr-imagem-especifica")
def ocr_especifico(payload: OcrEspecificoIn, usuario: dict = Depends(exigir_aprovado)):
    """Faz OCR sob demanda em uma imagem específica (sem fila). Útil para reprocessar
    imagens com problemas de orientação ou para testar configurações de OCR."""
    _exigir_acesso(payload.id_inquerito, usuario, escrita=True)
    if not ocr_disponivel():
        raise HTTPException(
            status_code=503,
            detail="OCR indisponível: sem Tesseract instalado (modo local) e sem URL/modelo de API de visão configurados (modo api).",
        )

    try:
        # ocr_imagem_especifica já confere que o arquivo é deste inquérito
        return ocr_imagem_especifica(payload.id_arquivo, payload.id_inquerito)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


class ResetarOcrIn(BaseModel):
    id_inquerito: int
    id_extracao: int | None = Field(default=None)
    mime_types: list[str] | None = Field(default=None, description="Ex: ['image/jpeg', 'image/png']")


@router.post("/api/arquivos/ocr-imagens/resetar")
def resetar_ocr(payload: ResetarOcrIn, usuario: dict = Depends(exigir_aprovado)):
    """Reseta o OCR de imagens (marca como não processadas) para re-rodar.
    Útil em produção quando OCR foi processado com problemas ou após melhorias no código.

    Exemplos:
    - Resetar TODO o inquérito: {"id_inquerito": 123}
    - Resetar uma extração específica: {"id_inquerito": 123, "id_extracao": 456}
    - Resetar apenas JPEGs: {"id_inquerito": 123, "mime_types": ["image/jpeg"]}
    """
    _exigir_acesso(payload.id_inquerito, usuario, escrita=True)
    if payload.id_extracao is not None:
        if obter_id_inquerito_de_extracao(payload.id_extracao) != payload.id_inquerito:
            raise HTTPException(status_code=404, detail="Extração não encontrada neste inquérito.")
    return resetar_ocr_imagens(
        payload.id_inquerito,
        payload.id_extracao,
        payload.mime_types
    )


@router.get("/api/arquivos/ocr-extracao/{id_extracao}/status")
def status_ocr(id_extracao: int, usuario: dict = Depends(exigir_aprovado)):
    """Retorna o status de OCR de uma extração: 'completo' | 'parcial' | 'pendente' | 'sem_imagens'.
    Útil para decidir se mostra "Extrair texto (OCR)" ou "Refazer OCR" no frontend."""
    _exigir_acesso(obter_id_inquerito_de_extracao(id_extracao), usuario)
    return status_ocr_extracao(id_extracao)

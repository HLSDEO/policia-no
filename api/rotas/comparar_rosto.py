import logging

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import Response

from funcoes.auth_utils import exigir_aprovado
from funcoes import jobs_utils
from funcoes.comparar_rosto_utils import (
    extracao_pertence_inquerito,
    iniciar_comparacao,
    obter_origem_arquivo,
)
from funcoes.investigacao_utils import (
    exigir_acesso_inquerito,
    exigir_escrita_inquerito,
    fonte_ufdr,
    obter_binario_arquivo,
    redimensionar_imagem,
    usuario_tem_acesso_inquerito,
)
from funcoes import ufdr_cache
from funcoes.upload_utils import ler_limitado

router = APIRouter()
logger = logging.getLogger(__name__)

MAX_TAMANHO_UPLOAD = 10 * 1024 * 1024


@router.post("/api/investigacao/inqueritos/{id_inquerito}/comparar-rosto")
def comparar_rosto_route(
    id_inquerito: int,
    arquivo: UploadFile = File(...),
    limite: int = Form(50),
    extracao: int | None = Form(None),
    usuario: dict = Depends(exigir_escrita_inquerito),
):
    if not arquivo.content_type or not arquivo.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="O arquivo enviado não é uma imagem.")

    # `extracao` é o deep-link das telas de extração (?extracao=ID): restringe a varredura a um
    # aparelho. Confere antes de qualquer coisa — um id de outro inquérito não pode virar filtro.
    if extracao and not extracao_pertence_inquerito(id_inquerito, extracao):
        raise HTTPException(status_code=400, detail="Esta extração não pertence a este inquérito.")

    # em blocos, parando no limite: `read()` inteiro seguido de `len(dados) > MAX` só descobria o
    # excesso depois de já ter alocado o corpo todo, e o teto do corpo é o do nginx (520 MB), não o
    # desta rota. Ver funcoes/upload_utils.py.
    try:
        dados = ler_limitado(arquivo.file, MAX_TAMANHO_UPLOAD, "Imagem")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        arquivo.file.close()

    # comparação facial com nó online + JOBS_NO_ATIVO=tudo: os milhares de forward de CLIP
    # descem para a CPU da estação (a prova já está lá); o central guarda a foto de referência
    # num arquivo escondido até o nó reivindicar o job (jobs_no.guardar_referencia) e fecha a
    # comparação com a multiplicação de matriz quando os vetores voltam
    from funcoes import jobs_no

    no = jobs_no.no_para_job(jobs_utils.TP_COMPARACAO_FACIAL, id_inquerito)
    try:
        id_job = jobs_utils.criar(
            jobs_utils.TP_COMPARACAO_FACIAL, usuario["id_usuario"],
            id_inquerito=id_inquerito, id_extracao=extracao,
            parametros={"limite": min(limite, 100), "extracao": extracao},
            pesado=not no, id_servidor=no["id_servidor"] if no else None,
        )
    except jobs_utils.JobOcupado as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    if no is not None:
        try:
            jobs_no.guardar_referencia(id_job, dados)
        except OSError as exc:
            jobs_utils.falhar(id_job, f"não consegui guardar a referência para o nó: {exc}")
            raise HTTPException(status_code=500,
                                detail="Erro interno ao processar a comparação.") from exc
        return {"ok": True, "id_job": id_job,
                "mensagem": f"Comparação iniciada no servidor {no['no_servidor']}."}

    try:
        iniciar_comparacao(
            id_job=id_job,
            id_inquerito=id_inquerito,
            imagem_upload_bytes=dados,
            limite=min(limite, 100),
            id_extracao=extracao,
        )
    except Exception:
        logger.exception("Erro ao iniciar comparação de rostos")
        jobs_utils.falhar(id_job, "Falha ao iniciar a comparação.")
        raise HTTPException(status_code=500, detail="Erro interno ao processar a comparação.")

    # o front acompanha por GET /api/investigacao/jobs/{id_job} e lê os rostos parecidos em
    # tx_resultado.matches quando o job conclui
    return {"ok": True, "id_job": id_job}


@router.get("/api/investigacao/comparar-rosto/arquivo/{id_arquivo}/preview")
def preview_arquivo(
    id_arquivo: int,
    usuario: dict = Depends(exigir_aprovado),
):
    info = obter_binario_arquivo(id_arquivo)
    if info is None:
        raise HTTPException(status_code=404, detail="Arquivo não encontrado.")
    if not info["caminho_zip"] or not (info["tp_mime"] or "").startswith("image/"):
        raise HTTPException(status_code=404, detail="Imagem não disponível.")

    if not usuario_tem_acesso_inquerito(info["id_inquerito"], usuario):
        raise HTTPException(status_code=403, detail="Sem permissão para este inquérito.")

    caminho_ufdr = fonte_ufdr(info["no_arquivo_ufdr"])
    if caminho_ufdr is None:
        raise HTTPException(status_code=404,
                            detail="Arquivo .ufdr original não encontrado (nem no servidor "
                                   "central, nem no servidor da delegacia).")
    chave_processada = (str(caminho_ufdr), info["caminho_zip"], "cmp-preview")
    dados = ufdr_cache.obter_conteudo(chave_processada)
    if dados is None:
        try:
            bruto = ufdr_cache.ler_membro(caminho_ufdr, info["caminho_zip"])
        except (FileNotFoundError, KeyError):
            raise HTTPException(status_code=404, detail="Imagem não encontrada no arquivo original.")
        try:
            dados = redimensionar_imagem(bruto, tamanho_max=400, qualidade=70)
        except Exception:
            raise HTTPException(status_code=422, detail="Não foi possível processar esta imagem.")
        ufdr_cache.guardar_conteudo(chave_processada, dados)

    return Response(content=dados, media_type="image/jpeg")


@router.get("/api/investigacao/comparar-rosto/arquivo/{id_arquivo}/origem")
def origem_arquivo(
    id_arquivo: int,
    usuario: dict = Depends(exigir_aprovado),
):
    info = obter_origem_arquivo(id_arquivo)
    if info is None:
        raise HTTPException(status_code=404, detail="Arquivo não encontrado.")

    if not usuario_tem_acesso_inquerito(info["id_inquerito"], usuario):
        raise HTTPException(status_code=403, detail="Sem permissão para este inquérito.")

    return info

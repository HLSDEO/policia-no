"""Rotas de documentos avulsos em data/ (fora do .ufdr): listagem e envio pelo navegador.

A importação de documentos acontece pelo inquérito (POST /api/investigacao/extrair-documento),
que anexa o texto extraído ao INQ_TB04_DOCUMENTOS — mesmo caminho para inquérito e caso de
inteligência (o caso é um inquérito com IN_CASO_INTELIGENCIA)."""

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from pathlib import Path

from funcoes.auth_utils import exigir_aprovado
from funcoes.documentos_utils import (
    listar_arquivos_diversos,
    salvar_documento_enviado,
)
router = APIRouter()


@router.get("/api/documentos/prompts-documento")
def prompts_documento(usuario: dict = Depends(exigir_aprovado)):
    """Lista prompts ativos de escopo 'documento' — para qualquer usuário aprovado montar a reextração."""
    from funcoes.prompts_entidade_utils import listar_prompts_ativos
    return listar_prompts_ativos("documento")


@router.get("/api/documentos/arquivos")
def arquivos(usuario: dict = Depends(exigir_aprovado), delegacia: str | None = None):
    """Lista os arquivos de data/ que não são .ufdr, para o seletor da tela. Se a delegacia tem
    nó ativo, a lista vem da pasta DO NÓ (é lá que os arquivos dela estão); senão, do central."""
    from funcoes import no_utils
    from funcoes.investigacao_utils import delegacia_permitida
    subpasta = delegacia_permitida(delegacia, usuario)
    remotos = no_utils.listagem_remota(subpasta, subpasta, "diversos")
    if remotos is not None:
        return remotos
    return listar_arquivos_diversos(subpasta)


@router.get("/api/documentos/upload/limite")
def limite_upload(usuario: dict = Depends(exigir_aprovado)):
    """Limite vigente de upload por arquivo — o que a tela usa para validar antes de enviar.

    Ajustável em Administração → Desempenho (UPLOAD_DOCUMENTO_MB no .env); vale na hora, sem
    reiniciar. O front busca aqui ao abrir as telas de importação e atualiza os textos de ajuda
    ("Até X MB por arquivo") e a checagem local — quem decide de verdade continua sendo
    salvar_documento_enviado, que relê o mesmo valor a cada arquivo. O .zip é a exceção:
    não tem teto (vai por POST /api/documentos/upload-zip, como o .ufdr)."""
    from funcoes.documentos_utils import limite_upload_mb

    mb = limite_upload_mb()
    return {"limite_mb": mb, "limite_bytes": mb * 1024 * 1024}


@router.post("/api/documentos/upload")
def upload_documentos(
    arquivos: list[UploadFile] = File(...),
    delegacia: str | None = Form(default=None),
    usuario: dict = Depends(exigir_aprovado),
):
    """Recebe documentos pelo navegador e grava em data/[<delegacia>/] — exatamente a pasta em que
    o investigador largaria o arquivo à mão, e a mesma que GET /api/documentos/arquivos lista.

    Não importa nada por conta própria: quem importa continua sendo
    POST /api/investigacao/extrair-documento (aba "Arquivo genérico"), com o nome que vem na
    resposta daqui — que pode diferir do enviado quando já existe arquivo com aquele nome.

    A delegacia passa por `delegacia_permitida`: sem isso, mandar outra no formulário gravaria na
    pasta de uma unidade que o usuário nem pode listar.

    Responde 400 só quando NADA foi salvo; com envio múltiplo, o que deu certo volta em "arquivos"
    e o que falhou (tipo não suportado, vazio, grande demais) em "falhas", para a tela mostrar os
    dois — um PDF recusado no meio da leva não deve derrubar os outros quatro."""
    from funcoes.investigacao_utils import delegacia_permitida

    subpasta = delegacia_permitida(delegacia, usuario)
    salvos: list[dict] = []
    falhas: list[str] = []
    for arquivo in arquivos:
        if not arquivo.filename:
            continue
        try:
            salvos.extend(salvar_documento_enviado(arquivo.file, arquivo.filename, subpasta))
        except ValueError as exc:
            falhas.append(str(exc))
        except OSError as exc:
            falhas.append(f"{arquivo.filename}: não foi possível gravar em data/ ({exc.strerror or exc}).")
        finally:
            arquivo.file.close()

    if not salvos and not falhas:
        raise HTTPException(status_code=400, detail="Nenhum arquivo enviado.")
    if not salvos:
        raise HTTPException(status_code=400, detail="; ".join(falhas))
    return {"arquivos": salvos, "falhas": falhas}


@router.post("/api/documentos/upload-zip")
def upload_zip(
    arquivos: list[UploadFile] = File(...),
    delegacia: str | None = Form(default=None),
    usuario: dict = Depends(exigir_aprovado),
):
    """Recebe um .zip pelo navegador e expande em data/[<delegacia>/] — a mesma pasta em que
    o investigador largaria os documentos à mão, e a mesma que GET /api/documentos/arquivos
    lista. O ZIP em si não fica em data/: só o conteúdo útil de dentro (ver
    _salvar_documento_zip em funcoes/documentos_utils.py).

    É a segunda exceção de tamanho entre os uploads (a primeira é o .ufdr): um ZIP de prova
    com centenas de documentos passa de GB, então não vale o limite de UPLOAD_DOCUMENTO_MB —
    o teto desta rota é o `location` próprio no nginx (client_max_body_size 0 +
    proxy_request_buffering off, ver nginx/default.conf), e a gravação/expansão é em blocos,
    sem passar o corpo pela memória do processo. Arquivo que não é .zip cai em "falhas"
    (esse vai pelo /api/documentos/upload comum, com o limite vigente).

    Não importa nada por conta própria: quem importa continua sendo
    POST /api/investigacao/extrair-documento, com os nomes que vêm na resposta daqui.
    Mesmo contrato de resposta do upload comum (`arquivos`/`falhas`), para o front tratar
    os dois do mesmo jeito."""
    from funcoes.investigacao_utils import delegacia_permitida

    subpasta = delegacia_permitida(delegacia, usuario)
    salvos: list[dict] = []
    falhas: list[str] = []
    for arquivo in arquivos:
        if not arquivo.filename:
            continue
        try:
            if Path(arquivo.filename).suffix.lower() != ".zip":
                raise ValueError(
                    f"{arquivo.filename}: esta rota só recebe .zip — "
                    "demais tipos vão pelo upload comum."
                )
            salvos.extend(salvar_documento_enviado(arquivo.file, arquivo.filename, subpasta))
        except ValueError as exc:
            falhas.append(str(exc))
        except OSError as exc:
            falhas.append(f"{arquivo.filename}: não foi possível gravar em data/ ({exc.strerror or exc}).")
        finally:
            arquivo.file.close()

    if not salvos and not falhas:
        raise HTTPException(status_code=400, detail="Nenhum arquivo enviado.")
    if not salvos:
        raise HTTPException(status_code=400, detail="; ".join(falhas))
    return {"arquivos": salvos, "falhas": falhas}

"""Rotas da página de extração de dados de investigação (UFDR -> WhatsApp -> banco)."""

import logging
import re
from io import BytesIO
import psycopg
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from funcoes import jobs_utils, midia_temp, ufdr_cache, ufdr_zip
from funcoes.resposta_utils import cabecalho_disposicao

logger = logging.getLogger(__name__)
from funcoes.classificacao_imagem_utils import listar_categorias
from funcoes.auth_utils import exigir_admin, exigir_aprovado, listar_usuarios_basico
from funcoes.documentos_utils import (
    anexar_documento_inquerito,
    apagar_documento_inquerito,
    obter_documento_visualizacao,
    obter_id_inquerito_de_documento,
    renderizar_documento,
    reextrair_texto_documento,
    resposta_documento_original,
)
from funcoes.busca_entidades_utils import (
    LIMITE_POR_FONTE,
    buscar_entidades,
    fontes_disponiveis,
)
from funcoes.embed_utils import IndexacaoOcupada, iniciar_indexacao, obter_indexacao
from funcoes.geocodificacao_utils import geocodificar_coordenadas
from funcoes.geoespacial_utils import (
    apagar_camada,
    contagem_por_feicao,
    geojson_camada,
    importar_shapefile,
    listar_camadas,
    obter_id_inquerito_de_camada,
    renomear_camada,
)
from funcoes.ia_investigacao import listar_documentos_inquerito, ler_documento_inquerito_completo
from funcoes.ia_provedores import ErroProvedorIA
from funcoes.pix_utils import (
    detectar_chaves_pix,
    listar_chaves_pix,
    status_deteccao_extracao,
)
from funcoes.ufdr_dominios_utils import contagens_dominios, facetas_dominio, listar_dominio
from funcoes.wifi_utils import listar_redes_wifi, status_wifi_extracao
from funcoes.contas_nuvem_utils import (
    listar_atividades_conta,
    facetas_atividades_conta,
    resumo_atividades_conta,
    listar_acessos_conta,
    facetas_acessos_conta,
    resumo_acessos_conta,
    listar_agregados_conta,
    facetas_agregados_conta,
    resumo_agregados_conta,
    listar_conexoes_conta,
    resumo_conexoes_conta,
    listar_dispositivos_conta,
    resumo_dispositivos_conta,
    listar_perfil_conta,
    listar_eventos_conta,
    resumo_eventos_conta,
)
from funcoes.ipei_utils import (
    apagar_ipei,
    listar_alvos_ipei,
    listar_anos_ipei,
    listar_bens_ipei,
    listar_contas_ipei,
    listar_contatos_ipei,
    listar_fontes_ipei,
    listar_ipeis_inquerito,
    listar_notas_ipei,
    listar_relacionados_ipei,
    obter_id_inquerito_de_ipei,
    obter_ipei,
)
from funcoes.ipl_utils import (
    apagar_ipl,
    listar_achados_ipl,
    listar_ipls_inquerito,
    listar_pecas_ipl,
    listar_pessoas_ipl,
    obter_id_inquerito_de_ipl,
    obter_ipl,
)
from funcoes.irpf_utils import (
    apagar_irpf,
    listar_bens_irpf,
    listar_dependentes_irpf,
    listar_dividas_irpf,
    listar_doacoes_irpf,
    listar_irpfs_inquerito,
    listar_rendimentos_irpf,
    obter_id_inquerito_de_irpf,
    obter_irpf,
)
from funcoes.rif_utils import (
    analisar_contrapartes_comunicacao_ia,
    apagar_identificacao_rif,
    apagar_rif,
    consolidar_contrapartes_rif,
    criar_identificacao_rif,
    editar_identificacao_rif,
    importar_rif,
    listar_comunicacoes_rif,
    listar_contrapartes_rif,
    listar_envolvidos_rif,
    listar_identificacoes_rif,
    listar_ocorrencias_rif,
    listar_rifs_disponiveis,
    listar_rifs_inquerito,
    obter_id_inquerito_de_rif,
    obter_id_rif_de_identificacao,
    reprocessar_contrapartes_rif,
)
from funcoes.simba_utils import (
    ImportacaoEmAndamento,
    apagar_simba,
    calcular_alertas,
    consolidar_contrapartes_simba,
    grafo_simba,
    iniciar_importacao_simba,
    listar_arquivos_simba,
    listar_contas_simba,
    listar_contrapartes_simba,
    listar_extratos_inquerito,
    listar_lancamentos_simba,
    obter_extrato,
    obter_id_inquerito_de_extrato,
    reprocessar_titular_interno_simba,
    reprocessar_titular_interno_todos_simba,
    serie_mensal_simba,
    solicitar_cancelamento_simba,
    timeline_financeira_inquerito,
)
from funcoes.telefonia_utils import (
    apagar_telefonia,
    listar_ancoras_inquerito,
    listar_arquivos_telefonia,
    listar_chamadas_telefonia,
    listar_dados_moveis_telefonia,
    listar_encontros_inquerito,
    listar_erbs_inquerito,
    listar_erbs_telefonia,
    listar_filtros_telefonia,
    listar_linhas_telefonia,
    listar_numeros_telefonia,
    listar_sessoes_telefonia,
    listar_telefonia_inquerito,
    obter_id_inquerito_de_telefonia,
    obter_telefonia,
)
from funcoes.canonicas_utils import (
    disparar_refresh_convergencia,
    exportar_convergencia,
    listar_convergencia,
    listar_convergencia_arestas,
    listar_convergencia_inquerito,
    listar_convergencia_par,
    resumo_convergencia_inqueritos,
    status_cache_convergencia,
)
from funcoes.compartilhar_prova import compartilhar_extracao, listar_destinos
from funcoes.iped_cache import abrir_blob_stream as _stream_blob_iped
from funcoes.iped_cache import ler_blob as _ler_blob_iped
from funcoes.iped_cache import ler_thumb as _ler_thumb_iped
from funcoes.iped_utils import (
    apagar_iped,
    detectar_conta_iped,
    iniciar_importacao_iped,
    java_disponivel,
    listar_casos_iped_disponiveis,
    listar_extracoes_iped_inquerito,
    obter_id_inquerito_de_iped,
    solicitar_cancelamento_iped,
)
from funcoes.investigacao_utils import (
    _resolver_arquivo_iped,
    _resolver_arquivo_zip,
    apagar_extracao,
    apagar_inquerito,
    associar_contato,
    categoria_binario_anexo,
    conceder_permissao,
    eh_gestor_inquerito,
    exigir_acesso_inquerito,
    exigir_escrita_inquerito,
    exigir_gestor_inquerito,
    fonte_ufdr,
    iniciar_extracao,
    iniciar_transcricao_extracao,
    mesma_delegacia_inqueritos,
    restaurar_arquivo_ufdr,
    solicitar_cancelamento_extracao,
    listar_aplicativos,
    facetas_emails,
    listar_emails,
    obter_corpo_email,
    listar_contas_financeiras,
    listar_arquivos,
    listar_arquivos_ufdr,
    listar_pastas_arquivos,
    periodo_arquivos,
    listar_contatos,
    facetas_contato,
    listar_apps_mensageiros,
    listar_conversas,
    listar_donos,
    listar_contas_usuario,
    listar_senhas,
    listar_autofill,
    listar_extracoes,
    listar_extracoes_do_inquerito,
    listar_inqueritos,
    estatisticas_delegacia,
    listar_ligacoes,
    facetas_ligacao,
    listar_localizacoes,
    grafo_localizacoes,
    buscar_mensagens_conversa,
    listar_mensagens_conversa,
    listar_notas,
    listar_notificacoes,
    facetas_notificacao,
    listar_paginas_visitadas,
    listar_timeline,
    resumo_navegacao,
    atualizar_transcricao_anexo,
    atualizar_classificacao_arquivo,
    atualizar_texto_ocr,
    facetas_localizacao,
    facetas_navegacao,
    listar_participantes_conversa,
    listar_permissoes_inquerito,
    extrair_metadados_imagem,
    extrair_metadados_video,
    obter_binario_anexo,
    obter_binario_arquivo,
    obter_foto_conversa,
    obter_id_inquerito_de_contato,
    obter_contexto_investigacao,
    obter_conversa_de_mensagem,
    obter_evidencia,
    obter_id_inquerito_de_conversa,
    obter_id_inquerito_de_extracao,
    redimensionar_imagem,
    renomear_inquerito,
    atualizar_apelido_extracao,
    atualizar_apelido_prova,
    atualizar_delegacia_inquerito,
    atualizar_situacao_inquerito,
    salvar_contexto_investigacao,
    revogar_permissao,
    usuario_tem_acesso_inquerito,
)
from funcoes.ufdr_parser import mime_de_extensao
from funcoes.ocr_utils import obter_texto_ocr

router = APIRouter()

_RE_INQUERITO = re.compile(r"^[\w./-]{1,50}$")
_FONTES_CONVERSA = {"wpp", "sms", "msg"}


def _validar_numero_ou_caso(numero_informado: str | None, caso_inteligencia: bool, usuario: dict) -> str:
    """Resolve o número a usar nas importações.

    Caso de inteligência: exige um dos perfis de inteligência (chefia ou agente). Com número
    vazio, devolve string vazia e o backend gera um novo CASO-AAAA.NNNN
    (_obter_ou_criar_inquerito); com número preenchido (ex.: o do card, ou o que a primeira
    resposta de um lote devolveu em nu_inquerito), devolve o número — a importação entra no
    caso JÁ EXISTENTE em vez de criar outro. Sem isto, um lote de N arquivos (um POST por
    arquivo) criava N casos diferentes.
    Inquérito normal: valida o número informado contra _RE_INQUERITO."""
    if caso_inteligencia:
        if not usuario.get("pode_caso_inteligencia"):
            raise HTTPException(status_code=403, detail="Requer perfil de inteligência.")
        numero = (numero_informado or "").strip()
        if numero and not _RE_INQUERITO.match(numero):
            raise HTTPException(status_code=400, detail="Número de caso de inteligência inválido.")
        return numero
    numero = (numero_informado or "").strip()
    if not numero or not _RE_INQUERITO.match(numero):
        raise HTTPException(status_code=400, detail="Número de inquérito inválido.")
    return numero


class ExtracaoIn(BaseModel):
    """Corpo aceito pelo POST que dispara a extração de um .ufdr."""

    arquivo: str = Field(..., max_length=255)
    numero_inquerito: str = Field(..., max_length=50)
    modelo_transcricao: str | None = Field(default=None, max_length=50)
    transcrever_video: bool = Field(default=False)
    delegacia: str | None = Field(default=None, max_length=100)
    caso_inteligencia: bool = Field(default=False)


class RifIn(BaseModel):
    """Corpo aceito pelo POST que importa um RIF (pasta em data/) para um inquérito."""

    pasta: str = Field(..., max_length=255)
    numero_inquerito: str = Field(..., max_length=50)
    delegacia: str | None = Field(default=None, max_length=100)
    caso_inteligencia: bool = Field(default=False)


class IdentificacaoRifIn(BaseModel):
    """Fatia de uma contraparte DESCONHECIDA do RIF que o investigador conseguiu identificar.

    id_comunicacao_rif/cd_direcao só entram na criação — na edição a linha já sabe a que
    desconhecido pertence, e mudá-la seria apagar e criar outra."""

    id_comunicacao_rif: int | None = None
    cd_direcao: str | None = Field(default=None, max_length=20)
    no_contraparte: str | None = Field(default=None, max_length=3000)
    nu_cpf_cnpj: str | None = Field(default=None, max_length=3000)
    vl_total: float
    qt_lancamentos: int | None = None
    tx_obs: str | None = Field(default=None, max_length=3000)


class SimbaIn(BaseModel):
    """Corpo aceito pelo POST que importa um extrato do SIMBA (*R304*.csv em data/)."""

    arquivo: str = Field(..., max_length=255)
    numero_inquerito: str = Field(..., max_length=50)
    delegacia: str | None = Field(default=None, max_length=100)
    caso_inteligencia: bool = Field(default=False)


class IpedIn(BaseModel):
    """Corpo aceito pelo POST que importa um caso do IPED (pasta em data/). `conta`, quando
    informado, pula a autodetecção (ex.: o investigador confirmou/editou a sugestão na tela)."""

    arquivo: str = Field(..., max_length=3000)
    numero_inquerito: str = Field(..., max_length=50)
    delegacia: str | None = Field(default=None, max_length=100)
    conta: str | None = Field(default=None, max_length=3000)
    caso_inteligencia: bool = Field(default=False)


class IpedDetectarContaIn(BaseModel):
    """Corpo aceito pelo POST que pré-visualiza a conta detectada de um caso do IPED, antes
    de confirmar a importação (ver iped_utils.detectar_conta_iped)."""

    arquivo: str = Field(..., max_length=3000)
    delegacia: str | None = Field(default=None, max_length=100)


class AssociacaoIn(BaseModel):
    """Corpo aceito pelo POST que associa um contato a uma pessoa e/ou local de referência.

    Todos os campos são opcionais e nulos limpam a associação — é julgamento do investigador,
    não um dado extraído automaticamente."""

    id_pessoa: int | None = None
    id_municipio: int | None = None
    id_uf: int | None = None


class PermissaoIn(BaseModel):
    """Corpo aceito pelo POST que concede acesso a um inquérito a outro usuário."""

    id_usuario: int
    in_admin: bool = False


@router.get("/api/investigacao/arquivos")
def arquivos(usuario: dict = Depends(exigir_aprovado), delegacia: str | None = None):
    """Lista os .ufdr disponíveis na pasta de dados, para o seletor da página. Se a delegacia tem
    nó ativo, a lista vem da pasta DO NÓ; senão, da pasta local do central."""
    from funcoes import no_utils
    from funcoes.investigacao_utils import delegacia_permitida
    subpasta = delegacia_permitida(delegacia, usuario)
    remotos = no_utils.listagem_remota(subpasta, subpasta, "ufdr")
    if remotos is not None:
        return remotos
    return listar_arquivos_ufdr(subpasta)


@router.post("/api/investigacao/ufdr-upload")
def upload_ufdr(
    arquivo: UploadFile = File(...),
    delegacia: str | None = Form(default=None),
    token: str | None = Form(default=None),
    usuario: dict = Depends(exigir_aprovado),
):
    """Recebe um .ufdr pelo navegador e grava em data/[<delegacia>/] — exatamente a pasta que
    GET /api/investigacao/arquivos lista. Não importa nada por conta própria: quem importa continua
    sendo POST /api/investigacao/extrair, com o nome devolvido aqui (que pode diferir do enviado
    quando já existe arquivo com aquele nome).

    É a exceção de tamanho entre os uploads do navegador: um .ufdr passa de dezenas de GB, então
    não vale o limite de UPLOAD_DOCUMENTO_MB — o teto desta rota é o `location` próprio no nginx
    (client_max_body_size 0 + proxy_request_buffering off, ver nginx/default.conf), e a gravação é
    em blocos por salvar_ufdr_enviado, sem passar o corpo pela memória do processo. A delegacia
    passa por `delegacia_permitida`, como nas listagens.

    O `token` é sorteado pelo front e dá nome ao `.parcial` em data/ — é por ele que
    GET /api/investigacao/ufdr-upload/progresso acompanha a gravação no servidor, que continua
    DEPOIS de o navegador terminar de mandar os bytes."""
    from funcoes.investigacao_utils import delegacia_permitida, salvar_ufdr_enviado

    subpasta = delegacia_permitida(delegacia, usuario)
    if not arquivo.filename:
        raise HTTPException(status_code=400, detail="Nenhum arquivo enviado.")
    try:
        salvo = salvar_ufdr_enviado(arquivo.file, arquivo.filename, subpasta, token=token)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except OSError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"{arquivo.filename}: não foi possível gravar em data/ ({exc.strerror or exc}).",
        )
    finally:
        arquivo.file.close()
    return {"arquivos": [salvo], "falhas": []}


@router.get("/api/investigacao/ufdr-upload/progresso")
def ufdr_upload_progresso(
    token: str,
    delegacia: str | None = None,
    usuario: dict = Depends(exigir_aprovado),
):
    """Progresso da gravação no SERVIDOR de um upload de .ufdr em andamento: o tamanho atual do
    `.parcial` em disco. A barra do navegador só cobre o envio dos bytes; num arquivo de dezenas
    de GB, gravá-los em data/ demora o mesmo tanto e acontece depois — sem esta rota a tela
    ficaria parada em "Enviando…" já com 100% transferido. 404 quando o upload não existe (mais).
    O progresso vem do disco (estat do `.parcial` nomeado pelo token), então não depende de
    estado em memória e funciona com vários workers."""
    from funcoes.investigacao_utils import delegacia_permitida, progresso_ufdr_upload

    subpasta = delegacia_permitida(delegacia, usuario)
    progresso = progresso_ufdr_upload(token, subpasta)
    if progresso is None:
        raise HTTPException(status_code=404, detail="Upload não encontrado.")
    return progresso


@router.get("/api/investigacao/extracoes")
def extracoes(inquerito: str | None = None, usuario: dict = Depends(exigir_aprovado)):
    """Lista as extrações já disparadas visíveis ao usuário, com status atual — usado no modal
    "todas as extrações". Quando ?inquerito=ID é informado, retorna só as extrações daquele
    inquérito (deep-link investigacao.html?inquerito=ID) para não trazer todas as extrações."""
    id_filtro: int | None = None
    if inquerito not in (None, ""):
        try:
            id_filtro = int(inquerito)
        except ValueError:
            return []
    return listar_extracoes(usuario, id_inquerito=id_filtro)


@router.get("/api/investigacao/poll")
def poll_investigacao(
    inquerito: str | None = None, usuario: dict = Depends(exigir_aprovado)
):
    """Agregado para polling da página de investigações — devolve inquéritos e extrações num
    único round-trip, respeitando o mesmo filtro ?inquerito do deep-link."""

    id_inquerito: int | None = None
    if inquerito not in (None, ""):
        try:
            id_inquerito = int(inquerito)
        except ValueError:
            return {"inqueritos": [], "extracoes": []}

    if id_inquerito is not None:
        inqs = listar_inqueritos(usuario, id_inquerito=id_inquerito)
        exts = listar_extracoes(usuario, id_inquerito=id_inquerito)
    else:
        inqs = listar_inqueritos(usuario)
        exts = listar_extracoes(usuario)

    return {"inqueritos": inqs, "extracoes": exts}


@router.delete("/api/investigacao/extracoes/{id_extracao}")
def apagar_extracao_rota(id_extracao: int, usuario: dict = Depends(exigir_aprovado)):
    """Apaga uma extração e todos os dados que vieram dela (conversas, mensagens, anexos,
    contatos, ligações, localizações, notificações, entidades ligadas a esses registros).
    Só o criador do inquérito ou um administrador pode apagar."""
    id_inquerito = obter_id_inquerito_de_extracao(id_extracao)
    if id_inquerito is None:
        raise HTTPException(status_code=404, detail="Extração não encontrada.")
    if not eh_gestor_inquerito(id_inquerito, usuario):
        raise HTTPException(status_code=403, detail="Apenas o criador do inquérito ou um administrador pode apagar extrações.")
    try:
        apagar_extracao(id_extracao)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"ok": True}


@router.post("/api/investigacao/extracoes/{id_extracao}/transcrever")
def transcrever_extracao_rota(id_extracao: int, usuario: dict = Depends(exigir_aprovado)):
    """Transcreve, em segundo plano, os áudios ainda não transcritos desta extração (botão
    "Transcrever" do modal "todas as extrações"). Usa a qualidade média (modelo 'small') e
    respeita a opção de transcrever vídeos escolhida na extração original. Só o criador do
    inquérito ou um administrador pode disparar."""
    id_inquerito = obter_id_inquerito_de_extracao(id_extracao)
    if id_inquerito is None:
        raise HTTPException(status_code=404, detail="Extração não encontrada.")
    if not eh_gestor_inquerito(id_inquerito, usuario):
        raise HTTPException(status_code=403, detail="Apenas o criador do inquérito ou um administrador pode transcrever os áudios desta extração.")
    try:
        return iniciar_transcricao_extracao(id_extracao)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/api/investigacao/extracoes/{id_extracao}/cancelar")
def cancelar_extracao_rota(id_extracao: int, usuario: dict = Depends(exigir_aprovado)):
    """Solicita a interrupção cooperativa sem apagar os dados já gravados."""
    id_inquerito = obter_id_inquerito_de_extracao(id_extracao)
    if id_inquerito is None:
        raise HTTPException(status_code=404, detail="Extração não encontrada.")
    if not eh_gestor_inquerito(id_inquerito, usuario):
        raise HTTPException(status_code=403, detail="Apenas o criador do inquérito ou um administrador pode parar a extração.")
    if not solicitar_cancelamento_extracao(id_extracao):
        raise HTTPException(status_code=409, detail="A extração não está em processamento.")
    return {"ok": True, "cancelando": True}


@router.get("/api/investigacao/extracoes/{id_extracao}/destinos-compartilhamento")
def destinos_compartilhamento_rota(id_extracao: int, usuario: dict = Depends(exigir_aprovado)):
    """Lista os inquéritos para onde esta prova pode ser copiada (menu "Compartilhar prova").

    Mesma permissão de apagar/transcrever: quem pode mandar uma prova para outro inquérito é o
    gestor do inquérito de origem. A lista já vem limitada aos inquéritos visíveis ao usuário —
    o isolamento por delegacia sai do mesmo filtro de acesso das outras telas."""
    id_inquerito = obter_id_inquerito_de_extracao(id_extracao)
    if id_inquerito is None:
        raise HTTPException(status_code=404, detail="Extração não encontrada.")
    if not eh_gestor_inquerito(id_inquerito, usuario):
        raise HTTPException(status_code=403, detail="Apenas o criador do inquérito ou um administrador pode compartilhar esta prova.")
    return listar_destinos(id_extracao, usuario)


class CompartilharProvaIn(BaseModel):
    """Corpo aceito pelo POST que copia a extração para outro inquérito."""

    id_inquerito_destino: int


@router.post("/api/investigacao/extracoes/{id_extracao}/compartilhar")
def compartilhar_prova_rota(
    id_extracao: int,
    payload: CompartilharProvaIn,
    usuario: dict = Depends(exigir_aprovado),
):
    """Copia esta extração para outro inquérito, sem reimportar o .ufdr (ver
    docs/compartilhar_prova.md). O arquivo não é duplicado no disco: a cópia aponta para o mesmo
    .ufdr da origem.

    Duas permissões, porque são dois inquéritos: gestor no de ORIGEM (mesma régua de apagar e
    transcrever — é de lá que a prova sai) e acesso ao de DESTINO (mesma régua de importar um
    .ufdr novo para ele). Entre unidades diferentes, só com edita_todas_delegacias
    (inteligência/administrador): sem isso um usuário com acesso em duas unidades clonaria a
    prova de uma para a outra. Ver A3."""
    id_inquerito = obter_id_inquerito_de_extracao(id_extracao)
    if id_inquerito is None:
        raise HTTPException(status_code=404, detail="Extração não encontrada.")
    if not eh_gestor_inquerito(id_inquerito, usuario):
        raise HTTPException(status_code=403, detail="Apenas o criador do inquérito ou um administrador pode compartilhar esta prova.")
    if not usuario_tem_acesso_inquerito(payload.id_inquerito_destino, usuario, escrita=True):
        raise HTTPException(status_code=403, detail="Você não tem acesso ao inquérito de destino.")
    if (not mesma_delegacia_inqueritos(id_inquerito, payload.id_inquerito_destino)
            and not usuario.get("edita_todas_delegacias")):
        raise HTTPException(
            status_code=403,
            detail="Compartilhar prova entre delegacias diferentes exige perfil de inteligência ou administrador.",
        )
    try:
        return compartilhar_extracao(id_extracao, payload.id_inquerito_destino, usuario["id_usuario"])
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


class RestaurarArquivoIn(BaseModel):
    """Corpo aceito pelo POST que restaura um .ufdr ausente a partir de um caminho no servidor."""

    caminho: str = Field(..., max_length=2000)


@router.post("/api/investigacao/extracoes/{id_extracao}/restaurar-arquivo")
def restaurar_arquivo_ufdr_rota(
    id_extracao: int,
    payload: RestaurarArquivoIn,
    usuario: dict = Depends(exigir_aprovado),
):
    """Copia o .ufdr de um caminho externo de volta para a pasta de dados, tornando-o
    novamente disponível para leitura de anexos. Só o gestor do inquérito pode fazer isto."""
    id_inquerito = obter_id_inquerito_de_extracao(id_extracao)
    if id_inquerito is None:
        raise HTTPException(status_code=404, detail="Extração não encontrada.")
    if not eh_gestor_inquerito(id_inquerito, usuario):
        raise HTTPException(status_code=403, detail="Apenas o criador do inquérito ou um administrador pode restaurar o arquivo.")
    try:
        return restaurar_arquivo_ufdr(id_extracao, payload.caminho, usuario)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/api/investigacao/extracoes/{id_extracao}/restaurar-arquivo/upload")
def restaurar_arquivo_upload(
    id_extracao: int,
    arquivo: UploadFile = File(...),
    usuario: dict = Depends(exigir_aprovado),
):
    """Restaura o .ufdr de uma extração fazendo upload do arquivo pelo navegador.
    Só o gestor do inquérito pode fazer isto. Útil quando o arquivo está na máquina
    do usuário (não num caminho acessível ao servidor)."""
    from funcoes.investigacao_utils import restaurar_arquivo_ufdr_upload

    id_inquerito = obter_id_inquerito_de_extracao(id_extracao)
    if id_inquerito is None:
        raise HTTPException(status_code=404, detail="Extração não encontrada.")
    if not eh_gestor_inquerito(id_inquerito, usuario):
        raise HTTPException(status_code=403, detail="Apenas o criador do inquérito ou um administrador pode restaurar o arquivo.")
    if not arquivo.filename:
        raise HTTPException(status_code=400, detail="Nenhum arquivo enviado.")
    try:
        # passa o fluxo, não os bytes: o util grava em blocos (ver restaurar_arquivo_ufdr_upload)
        return restaurar_arquivo_ufdr_upload(id_extracao, arquivo.file)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        arquivo.file.close()


class ApelidoExtracaoIn(BaseModel):
    """Corpo aceito pelo PUT que grava o apelido livre da extração."""

    apelido: str = Field("", max_length=200)


@router.put("/api/investigacao/extracoes/{id_extracao}/apelido")
def atualizar_apelido_rota(
    id_extracao: int,
    payload: ApelidoExtracaoIn,
    usuario: dict = Depends(exigir_aprovado),
):
    """Grava (ou limpa, com string vazia) o apelido de uma extração — nome curto do menu
    "Mais ações" do bloco em investigacao.html (ex.: "Celular do João").

    Qualquer usuário com acesso ao inquérito pode apelidar (mesma política do contexto da
    investigação): é só identificação no card, não muda arquivo, permissão nem dados."""
    id_inquerito = obter_id_inquerito_de_extracao(id_extracao)
    if id_inquerito is None:
        raise HTTPException(status_code=404, detail="Extração não encontrada.")
    if not usuario_tem_acesso_inquerito(id_inquerito, usuario, escrita=True):
        raise HTTPException(status_code=403, detail="Sem permissão para este inquérito.")
    atualizado = atualizar_apelido_extracao(id_extracao, payload.apelido)
    if not atualizado:
        raise HTTPException(status_code=404, detail="Extração não encontrada.")
    return {"ok": True}


# Prova do card que NÃO é extração .ufdr: o apelido mora em INQ_TB06_APELIDOS, endereçado pelo
# par (tipo, id) — o mesmo par que o front usa para nomear cada bloco. Cada tipo traz sua própria
# função de "qual inquérito é este", a mesma usada pelas rotas de apagar/gerenciar abaixo.
_APELIDO_PROVAS = {
    "rif": obter_id_inquerito_de_rif,
    "simba": obter_id_inquerito_de_extrato,
    "documento": obter_id_inquerito_de_documento,
    "iped": obter_id_inquerito_de_iped,
    "ipl": obter_id_inquerito_de_ipl,
    "irpf": obter_id_inquerito_de_irpf,
    "ipei": obter_id_inquerito_de_ipei,
    "telefonia": obter_id_inquerito_de_telefonia,
}


@router.put("/api/investigacao/provas/{tp_prova}/{id_prova}/apelido")
def atualizar_apelido_prova_rota(
    tp_prova: str,
    id_prova: int,
    payload: ApelidoExtracaoIn,
    usuario: dict = Depends(exigir_aprovado),
):
    """Grava (ou limpa, com string vazia) o apelido de uma prova que não é extração .ufdr —
    rif/simba/documento/iped/ipl/irpf/ipei/telefonia. Mesma política do apelido da extração:
    qualquer usuário com acesso ao inquérito pode apelidar; é só identificação no card."""
    buscar_inquerito = _APELIDO_PROVAS.get(tp_prova)
    if buscar_inquerito is None:
        raise HTTPException(status_code=404, detail="Tipo de prova desconhecido.")
    id_inquerito = buscar_inquerito(id_prova)
    if id_inquerito is None:
        raise HTTPException(status_code=404, detail="Prova não encontrada.")
    if not usuario_tem_acesso_inquerito(id_inquerito, usuario, escrita=True):
        raise HTTPException(status_code=403, detail="Sem permissão para este inquérito.")
    atualizar_apelido_prova(tp_prova, id_prova, payload.apelido, usuario.get("id_usuario"))
    return {"ok": True}


@router.get("/api/investigacao/inqueritos")
def inqueritos(inquerito: str | None = None, usuario: dict = Depends(exigir_aprovado)):
    """Lista os inquéritos visíveis ao usuário (admin vê todos; os demais só os que criaram ou
    receberam permissão), com contagens agregadas, para a seção "Investigações".

    Quando ?inquerito=ID é informado, retorna só aquele inquérito — deep-link
    investigacao.html?inquerito=ID para não trazer todos os inquéritos do sistema."""
    id_filtro: int | None = None
    if inquerito not in (None, ""):
        try:
            id_filtro = int(inquerito)
        except ValueError:
            return []
    return listar_inqueritos(usuario, id_inquerito=id_filtro)


@router.get("/api/investigacao/estatisticas-delegacia")
def estatisticas_delegacia_rota(
    delegacia: str | None = None, usuario: dict = Depends(exigir_aprovado)
):
    """Retorna estatísticas agregadas da delegacia selecionada (ou da do usuário se não informada).

    Como em convergencia_global, `delegacia` é filtro de apresentação: o isolamento vem do
    _filtro_acesso_sql, que já limita aos inquéritos visíveis ao usuário antes de agregar."""
    return estatisticas_delegacia(usuario, delegacia)


@router.delete("/api/investigacao/inqueritos/{id_inquerito}")
def apagar_inquerito_rota(id_inquerito: int, usuario: dict = Depends(exigir_gestor_inquerito)):
    """Apaga um inquérito inteiro: todas as extrações (e os dados de cada uma), entidades,
    relações, histórico de busca automática, conversas de IA e permissões concedidas.
    Só o criador do inquérito ou um administrador pode apagar."""
    try:
        apagado = apagar_inquerito(id_inquerito)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if not apagado:
        raise HTTPException(status_code=404, detail="Inquérito não encontrado.")
    return {"ok": True}


class RenomearInqueritoIn(BaseModel):
    """Corpo aceito pelo PUT que altera o número do inquérito."""

    numero_inquerito: str = Field(..., max_length=50)


@router.put("/api/investigacao/inqueritos/{id_inquerito}/numero")
def renomear_inquerito_rota(
    id_inquerito: int, payload: RenomearInqueritoIn, usuario: dict = Depends(exigir_gestor_inquerito)
):
    """Altera o número (NU_INQUERITO) de um inquérito. Só o criador do inquérito ou um administrador."""
    numero = payload.numero_inquerito.strip()
    if not numero or not _RE_INQUERITO.match(numero):
        raise HTTPException(status_code=400, detail="Número de inquérito inválido.")
    try:
        renomeado = renomear_inquerito(id_inquerito, numero)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if not renomeado:
        raise HTTPException(status_code=404, detail="Inquérito não encontrado.")
    return {"ok": True}


class DelegaciaInqueritoIn(BaseModel):
    """Corpo aceito pelo PUT que altera a delegacia de um inquérito."""

    delegacia: str = Field("", max_length=300)


@router.put("/api/investigacao/inqueritos/{id_inquerito}/delegacia")
def atualizar_delegacia_rota(
    id_inquerito: int, payload: DelegaciaInqueritoIn, usuario: dict = Depends(exigir_gestor_inquerito)
):
    """Altera a delegacia de um inquérito. Só o gestor do inquérito ou um administrador."""
    atualizado = atualizar_delegacia_inquerito(id_inquerito, payload.delegacia)
    if not atualizado:
        raise HTTPException(status_code=404, detail="Inquérito não encontrado.")
    return {"ok": True}


class SituacaoInqueritoIn(BaseModel):
    """Corpo aceito pelo PUT que muda a situação do inquérito."""

    situacao: str = Field(..., max_length=10)


@router.put("/api/investigacao/inqueritos/{id_inquerito}/situacao")
def atualizar_situacao_rota(
    id_inquerito: int, payload: SituacaoInqueritoIn, usuario: dict = Depends(exigir_gestor_inquerito)
):
    """Ativo | Arquivado — a situação do inquérito em si (não o andamento do processamento).

    Só o gestor (criador ou permissão in_admin): arquivar encerra o caso para a equipe, e essa
    decisão é de quem responde pelo inquérito, não de qualquer um que o edita."""
    situacao = (payload.situacao or "").strip().lower()
    if situacao not in ("ativo", "arquivado"):
        raise HTTPException(status_code=400, detail="Situação inválida (use ativo ou arquivado).")
    if not atualizar_situacao_inquerito(id_inquerito, situacao):
        raise HTTPException(status_code=404, detail="Inquérito não encontrado.")
    return {"ok": True, "situacao": situacao}


class ContextoInvestigacaoIn(BaseModel):
    """Corpo aceito pelo PUT que grava o contexto livre da investigação."""

    texto: str = Field("", max_length=8000)


@router.get("/api/investigacao/inqueritos/{id_inquerito}/contexto")
def obter_contexto(id_inquerito: int, usuario: dict = Depends(exigir_acesso_inquerito)):
    """Texto de contexto da investigação do inquérito (usado no prompt de IA)."""
    return {"texto": obter_contexto_investigacao(id_inquerito) or ""}


@router.put("/api/investigacao/inqueritos/{id_inquerito}/contexto")
def salvar_contexto(id_inquerito: int, payload: ContextoInvestigacaoIn, usuario: dict = Depends(exigir_escrita_inquerito)):
    """Grava (ou limpa) o contexto da investigação — qualquer usuário com acesso ao inquérito."""
    salvar_contexto_investigacao(id_inquerito, payload.texto)
    return {"ok": True}


@router.get("/api/investigacao/inqueritos/{id_inquerito}/resumo-noturno")
def resumo_noturno(id_inquerito: int, usuario: dict = Depends(exigir_acesso_inquerito)):
    """Resumo da madrugada do inquérito (consolidado + por fonte), para o bloco no topo de
    investigacao.html?inquerito=ID.

    Só lê INQ_TB05_RESUMO_NOTURNO — nunca chama a IA no request, então vale mesmo com a IA
    desativada para a delegacia. {} com tem=False quando o passe ainda não rodou."""
    from funcoes.resumo_noturno import obter_resumo_inquerito
    return obter_resumo_inquerito(id_inquerito)


@router.post("/api/investigacao/inqueritos/{id_inquerito}/indexacao-semantica")
def iniciar_indexacao_semantica(id_inquerito: int, usuario: dict = Depends(exigir_escrita_inquerito)):
    """Dispara em background a indexação semântica (embeddings) das mensagens do inquérito."""
    try:
        id_indexacao = iniciar_indexacao(id_inquerito)
    except IndexacaoOcupada as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ErroProvedorIA as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "id_indexacao": id_indexacao}


@router.get("/api/investigacao/inqueritos/{id_inquerito}/indexacao-semantica")
def status_indexacao_semantica(id_inquerito: int, usuario: dict = Depends(exigir_acesso_inquerito)):
    """Status da indexação semântica do inquérito (para polling); null se nunca indexado."""
    return obter_indexacao(id_inquerito)


@router.get("/api/investigacao/mensagens/{id_mensagem}/conversa")
def conversa_de_mensagem(id_mensagem: int, fonte: str = "wpp", usuario: dict = Depends(exigir_aprovado)):
    """Resolve a conversa (e o inquérito) de uma mensagem, para o deep-link "abrir mensagem original"
    que a IA cita como [msg:wpp:ID]/[msg:sms:ID]."""
    if fonte not in _FONTES_CONVERSA:
        raise HTTPException(status_code=400, detail="Fonte inválida.")
    res = obter_conversa_de_mensagem(id_mensagem, fonte)
    if res is None:
        raise HTTPException(status_code=404, detail="Mensagem não encontrada.")
    id_conversa, id_inquerito, id_extracao = res
    if not usuario_tem_acesso_inquerito(id_inquerito, usuario):
        raise HTTPException(status_code=403, detail="Sem permissão para este inquérito.")
    return {"id_conversa": id_conversa, "id_inquerito": id_inquerito, "id_extracao": id_extracao}


@router.get("/api/investigacao/evidencias/{fonte}/{id_prova}")
def evidencia_info(fonte: str, id_prova: int, usuario: dict = Depends(exigir_aprovado)):
    """Metadados de cadeia de custódia de uma evidência citada pela IA — o modal ℹ️ ao lado da
    citação [msg:wpp|sms|msg:ID] (apelido/arquivo/dispositivo/hashes do .ufdr + data/hora da
    mensagem) ou [doc:ID] (apelido/arquivo/hashes do documento). fonte aceita wpp|sms|msg|doc."""
    if fonte not in _FONTES_CONVERSA | {"doc"}:
        raise HTTPException(status_code=400, detail="Fonte inválida.")
    dados = obter_evidencia(fonte, id_prova)
    if dados is None:
        raise HTTPException(status_code=404, detail="Evidência não encontrada.")
    if not usuario_tem_acesso_inquerito(dados["id_inquerito"], usuario):
        raise HTTPException(status_code=403, detail="Sem permissão para este inquérito.")
    return dados


@router.get("/api/investigacao/usuarios-basico")
def usuarios_basico(usuario: dict = Depends(exigir_aprovado)):
    """Lista nome/login dos usuários ativos, para o seletor de "adicionar usuário" na gestão de permissões."""
    return listar_usuarios_basico()


@router.get("/api/investigacao/inqueritos/{id_inquerito}/conversas")
def conversas(
    id_inquerito: int, fonte: str = "wpp", extracao: int | None = None, app: str | None = None,
    busca: str | None = None, tipo: str = "todos", offset: int = 0, limite: int = 1000,
    conversa: int | None = None,
    usuario: dict = Depends(exigir_acesso_inquerito),
):
    """Uma PÁGINA das conversas de um inquérito (via ?fonte=wpp|sms|msg), para o visualizador.

    Para fonte=msg (mensageiros diversos), ?app=Instagram filtra o mensageiro específico.
    ?extracao=ID restringe a uma única extração (um aparelho). ?busca= filtra por nome/número,
    ?tipo= por aba (todos|conversa|grupo|difusao), ?offset/?limite paginam (limite teto 2000).
    Devolve {conversas, total, contagens, offset, limite, tem_mais} — as contagens por aba vêm
    sempre cheias para a busca atual, então as abas continuam corretos com uma página só."""
    if fonte not in _FONTES_CONVERSA:
        raise HTTPException(status_code=400, detail="Fonte inválida.")
    return listar_conversas(
        id_inquerito, fonte, extracao, app,
        busca=busca, tipo=tipo, offset=offset, limite=limite, conversa_id=conversa,
    )


@router.get("/api/investigacao/inqueritos/{id_inquerito}/apps-mensageiros")
def apps_mensageiros(id_inquerito: int, extracao: int | None = None, usuario: dict = Depends(exigir_acesso_inquerito)):
    """Lista os mensageiros diversos (Instagram, Telegram, …) presentes, com contagens — um botão por app."""
    return listar_apps_mensageiros(id_inquerito, extracao)


@router.get("/api/investigacao/inqueritos/{id_inquerito}/whatsapp-parcial")
def whatsapp_parcial(
    id_inquerito: int, extracao: int | None = None,
    usuario: dict = Depends(exigir_acesso_inquerito),
):
    """As "conversas" reconstruídas a partir da mídia do WhatsApp de uma nuvem (iCloud): uma por
    JID/contato, com contagem e período. Só vale para extração de nuvem (a mídia do aparelho vem
    no próprio WhatsApp)."""
    from funcoes.whatsapp_parcial_utils import listar_conversas_whatsapp_parcial, resolver_extracao_nuvem

    try:
        id_extracao = resolver_extracao_nuvem(id_inquerito, extracao)
    except ValueError:
        raise HTTPException(status_code=404, detail="Nenhuma extração de nuvem neste inquérito.")
    return listar_conversas_whatsapp_parcial(id_extracao)


@router.get("/api/investigacao/inqueritos/{id_inquerito}/whatsapp-parcial/midias")
def whatsapp_parcial_midias(
    id_inquerito: int, jid: str, extracao: int | None = None,
    offset: int = 0, limite: int = 20, tipo: str | None = None,
    usuario: dict = Depends(exigir_acesso_inquerito),
):
    """Uma janela da sequência cronológica de mídias de UM contato/grupo (a "conversa"
    reconstruída), com ?offset/?limite (paginação no servidor) e ?tipo=audio|imagem|video|
    documento (filtro de categoria). Devolve {midias, total, por_tipo, offset}."""
    from funcoes.whatsapp_parcial_utils import listar_midias_whatsapp_parcial, resolver_extracao_nuvem

    try:
        id_extracao = resolver_extracao_nuvem(id_inquerito, extracao)
    except ValueError:
        raise HTTPException(status_code=404, detail="Nenhuma extração de nuvem neste inquérito.")
    return listar_midias_whatsapp_parcial(id_extracao, jid, offset, limite, tipo)


@router.get("/api/investigacao/conversas/{id_conversa}/mensagens")
def mensagens(
    id_conversa: int, fonte: str = "wpp", limite: int = 200,
    antes_id: int | None = None, depois_id: int | None = None, centro_id: int | None = None,
    tipo: str | None = None,
    usuario: dict = Depends(exigir_aprovado),
):
    """Janela paginada das mensagens de uma conversa (ordem cronológica). Sem cursor, as `limite`
    mais recentes; ?antes_id / ?depois_id / ?centro_id navegam para mais antigas/novas/em torno de
    uma mensagem. ?tipo=texto|midia filtra o conteúdo da bolha (só texto / só com anexo).
    Devolve {mensagens, tem_anteriores, tem_posteriores}."""
    if fonte not in _FONTES_CONVERSA:
        raise HTTPException(status_code=400, detail="Fonte inválida.")
    if tipo not in (None, "texto", "midia"):
        raise HTTPException(status_code=400, detail="Filtro de conteúdo inválido.")
    _exigir_acesso_via_conversa(id_conversa, fonte, usuario)
    return listar_mensagens_conversa(id_conversa, fonte, limite, antes_id, depois_id, centro_id, tipo)


@router.get("/api/investigacao/conversas/{id_conversa}/buscar-mensagens")
def buscar_mensagens(
    id_conversa: int, termo: str, fonte: str = "wpp", limite: int = 30,
    usuario: dict = Depends(exigir_aprovado),
):
    """Busca texto e transcrições em todo o histórico de uma conversa."""
    if fonte not in _FONTES_CONVERSA:
        raise HTTPException(status_code=400, detail="Fonte inválida.")
    _exigir_acesso_via_conversa(id_conversa, fonte, usuario)
    return buscar_mensagens_conversa(id_conversa, fonte, termo, limite)


@router.get("/api/investigacao/anexos/{id_anexo}/imagem")
def imagem_anexo(id_anexo: int, fonte: str = "wpp", usuario: dict = Depends(exigir_aprovado)):
    """Serve a prévia (baixa resolução) da imagem de um anexo, lida sob demanda do .ufdr
    original — o binário não é copiado nem persistido no banco. Cacheada em memória
    (funcoes/ufdr_cache.py): renova por até 1h enquanto a conversa continuar sendo aberta,
    expira sozinha se não."""
    if fonte not in _FONTES_CONVERSA:
        raise HTTPException(status_code=400, detail="Fonte inválida.")

    info = obter_binario_anexo(id_anexo, fonte)
    if info is None:
        raise HTTPException(status_code=404, detail="Anexo não encontrado.")
    if not usuario_tem_acesso_inquerito(info["id_inquerito"], usuario):
        raise HTTPException(status_code=403, detail="Sem permissão para este inquérito.")
    if (info["caminho_zip"] or "").startswith("IPED::"):
        # Anexo da Nuvem IPED: o binário vive no storage do caso (ponteiro gravado na
        # importação a partir do sha-256 do linkedItems), não no .ufdr.
        if not (info["tp_conteudo"] or "").startswith("image/"):
            raise HTTPException(status_code=404, detail="Imagem não disponível.")
        resolvido_iped = _resolver_arquivo_iped(info["caminho_zip"])
        if not resolvido_iped:
            raise HTTPException(status_code=404, detail="Pasta do caso do IPED não encontrada em data/.")
        pasta_modulo, origem, hash_md5, id_fonte = resolvido_iped
        try:
            bruto = _ler_blob_iped(pasta_modulo, origem, hash_md5, id_fonte)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Imagem não encontrada no caso do IPED.")
        try:
            dados = redimensionar_imagem(bruto)
        except Exception:
            raise HTTPException(status_code=422, detail="Não foi possível processar esta imagem.")
        return Response(content=dados, media_type="image/jpeg")

    # a porta é o binário que existe de fato, não o tipo declarado: a miniatura de um vídeo
    # não recuperado chega marcada como video/mp4 e era recusada aqui, deixando o
    # investigador sem ver sequer o quadro que o Cellebrite extraiu
    if categoria_binario_anexo(info["caminho_zip"]) != "imagem":
        raise HTTPException(status_code=404, detail="Imagem não disponível.")

    caminho_ufdr = fonte_ufdr(info["no_arquivo_ufdr"])
    if caminho_ufdr is None:
        raise HTTPException(status_code=404,
                            detail="Arquivo .ufdr original não encontrado (nem no servidor "
                                   "central, nem no servidor da delegacia).")
    chave_processada = (str(caminho_ufdr), info["caminho_zip"], "img")
    dados = ufdr_cache.obter_conteudo(chave_processada)
    if dados is None:
        try:
            bruto = ufdr_cache.ler_membro(caminho_ufdr, info["caminho_zip"])
        except (FileNotFoundError, KeyError):
            raise HTTPException(status_code=404, detail="Imagem não encontrada no arquivo original.")
        try:
            dados = redimensionar_imagem(bruto)
        except Exception:
            # fonte no nó: a 1ª leitura depois de abrir a ponte às vezes chega truncada
            # (soquete pooling velho após reinício do nó) e o investigador ficava com
            # "não foi possível carregar" até dar F5 — descarta o handle e tenta de novo
            # com conexão nova antes de desistir, agora com o erro no log.
            if not str(caminho_ufdr).startswith("no://"):
                logger.exception("não foi possível processar a imagem do anexo %s", id_anexo)
                raise HTTPException(status_code=422,
                                    detail="Não foi possível processar esta imagem.")
            logger.warning("prévia do anexo %s falhou pela ponte; descartando o handle do "
                           ".ufdr e tentando de novo", id_anexo)
            ufdr_cache.descartar(caminho_ufdr)
            try:
                bruto = ufdr_cache.ler_membro(caminho_ufdr, info["caminho_zip"])
                dados = redimensionar_imagem(bruto)
            except Exception:
                logger.exception("prévia do anexo %s falhou até na retomada pela ponte", id_anexo)
                raise HTTPException(status_code=422,
                                    detail="Não foi possível processar esta imagem.")
        ufdr_cache.guardar_conteudo(chave_processada, dados)

    return Response(content=dados, media_type="image/jpeg")


@router.get("/api/investigacao/anexos/{id_anexo}/audio")
def audio_anexo(id_anexo: int, fonte: str = "wpp", usuario: dict = Depends(exigir_aprovado)):
    """Serve o áudio de um anexo, lido sob demanda do .ufdr original — o binário não é
    copiado nem persistido no banco (mesmo padrão de imagem_anexo, sem redimensionar).
    Cacheada em memória (funcoes/ufdr_cache.py), mesma política de 1h por inatividade."""
    if fonte not in _FONTES_CONVERSA:
        raise HTTPException(status_code=400, detail="Fonte inválida.")

    info = obter_binario_anexo(id_anexo, fonte)
    if info is None:
        raise HTTPException(status_code=404, detail="Anexo não encontrado.")
    if not usuario_tem_acesso_inquerito(info["id_inquerito"], usuario):
        raise HTTPException(status_code=403, detail="Sem permissão para este inquérito.")
    if (info["caminho_zip"] or "").startswith("IPED::"):
        # Anexo da Nuvem IPED: binário no storage do caso (mesmo desvio da imagem_anexo)
        if not (info["tp_conteudo"] or "").startswith("audio/"):
            raise HTTPException(status_code=404, detail="Áudio não disponível.")
        resolvido_iped = _resolver_arquivo_iped(info["caminho_zip"])
        if not resolvido_iped:
            raise HTTPException(status_code=404, detail="Pasta do caso do IPED não encontrada em data/.")
        pasta_modulo, origem, hash_md5, id_fonte = resolvido_iped
        try:
            dados = _ler_blob_iped(pasta_modulo, origem, hash_md5, id_fonte)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Áudio não encontrado no caso do IPED.")
        return Response(content=dados, media_type=info["tp_conteudo"])
    if categoria_binario_anexo(info["caminho_zip"]) != "audio":
        raise HTTPException(status_code=404, detail="Áudio não disponível.")

    caminho_ufdr = fonte_ufdr(info["no_arquivo_ufdr"])
    if caminho_ufdr is None:
        raise HTTPException(status_code=404,
                            detail="Arquivo .ufdr original não encontrado (nem no servidor "
                                   "central, nem no servidor da delegacia).")
    try:
        dados = ufdr_cache.ler_membro(caminho_ufdr, info["caminho_zip"])
    except (FileNotFoundError, KeyError):
        raise HTTPException(status_code=404, detail="Áudio não encontrado no arquivo original.")

    return Response(content=dados, media_type=info["tp_conteudo"])


@router.get("/api/investigacao/anexos/{id_anexo}/arquivo")
def arquivo_anexo(id_anexo: int, fonte: str = "wpp", usuario: dict = Depends(exigir_aprovado)):
    """Serve o binário de QUALQUER anexo (vídeo, documento, etc.), lido sob demanda do .ufdr
    original — sem copiar/persistir no banco. Vídeo/PDF abrem no navegador (inline); os demais
    o navegador baixa. Áudio/imagem têm endpoints próprios (com redimensionamento da imagem).
    Cacheada em memória (funcoes/ufdr_cache.py) só quando o anexo é pequeno (documentos/vídeos
    grandes não entram no cache, ver CONTEUDO_ITEM_TAMANHO_MAX)."""
    if fonte not in _FONTES_CONVERSA:
        raise HTTPException(status_code=400, detail="Fonte inválida.")

    info = obter_binario_anexo(id_anexo, fonte)
    if info is None:
        raise HTTPException(status_code=404, detail="Anexo não encontrado.")
    if not usuario_tem_acesso_inquerito(info["id_inquerito"], usuario):
        raise HTTPException(status_code=403, detail="Sem permissão para este inquérito.")
    if not info["caminho_zip"]:
        raise HTTPException(status_code=404, detail="Anexo não disponível no arquivo original.")

    # nome sugerido para download; vai percent-codificado no filename* UTF-8 (ver
    # resposta_utils.cabecalho_disposicao — um acento no header latin-1 derruba a rota)
    nome = info["no_anexo"] or "anexo"

    if info["caminho_zip"].startswith("IPED::"):
        # Anexo da Nuvem IPED: binário no storage do caso. MIME pelo TIPO DECLARADO no item
        # (o ponteiro não tem extensão) e conteúdo em streaming — o blob descomprime em
        # pedaços em vez de carregar o vídeo inteiro na memória do processo.
        resolvido_iped = _resolver_arquivo_iped(info["caminho_zip"])
        if not resolvido_iped:
            raise HTTPException(status_code=404, detail="Pasta do caso do IPED não encontrada em data/.")
        pasta_modulo, origem, hash_md5, id_fonte = resolvido_iped
        media_type = info["tp_conteudo"] or "application/octet-stream"
        return StreamingResponse(
            _stream_blob_iped(pasta_modulo, origem, hash_md5, id_fonte),
            media_type=media_type,
            headers={"Content-Disposition": cabecalho_disposicao("inline", nome)},
        )

    caminho_ufdr = fonte_ufdr(info["no_arquivo_ufdr"])
    if caminho_ufdr is None:
        raise HTTPException(status_code=404,
                            detail="Arquivo .ufdr original não encontrado (nem no servidor "
                                   "central, nem no servidor da delegacia).")
    try:
        dados = ufdr_cache.ler_membro(caminho_ufdr, info["caminho_zip"])
    except (FileNotFoundError, KeyError):
        raise HTTPException(status_code=404, detail="Anexo não encontrado no arquivo original.")

    # MIME pelo binário REAL: servir uma miniatura JPEG como "video/mp4" (o tipo da mídia
    # original, que não veio na extração) fazia o navegador abrir um player vazio.
    # Inclui o caso da miniatura salva como files/Image/<GUID>.mp4: a extensão diz vídeo,
    # mas categoria_binario_anexo já corrige para imagem (conteúdo JPEG) — o MIME acompanha.
    media_type = mime_de_extensao(info["caminho_zip"] or "")
    if media_type == "application/octet-stream":
        media_type = info["tp_conteudo"] or media_type
    if media_type.startswith("video/") and categoria_binario_anexo(info["caminho_zip"]) == "imagem":
        media_type = "image/jpeg"
    return Response(
        content=dados,
        media_type=media_type,
        headers={"Content-Disposition": cabecalho_disposicao("inline", nome)},
    )


class TranscricaoAnexoIn(BaseModel):
    """Corpo aceito pelo PUT que corrige a transcrição automática de um anexo de áudio/vídeo."""

    texto: str = Field("", max_length=20000)


@router.put("/api/investigacao/anexos/{id_anexo}/transcricao")
def editar_transcricao_anexo_rota(
    id_anexo: int, payload: TranscricaoAnexoIn, fonte: str = "wpp", usuario: dict = Depends(exigir_aprovado)
):
    """Corrige manualmente a transcrição automática (faster-whisper) de um áudio/vídeo, quando o
    investigador percebe que o texto não bate com o que foi dito ao ouvir. Qualquer usuário com
    acesso ao inquérito pode corrigir (mesma política de salvar_contexto_investigacao)."""
    if fonte not in _FONTES_CONVERSA:
        raise HTTPException(status_code=400, detail="Fonte inválida.")
    info = obter_binario_anexo(id_anexo, fonte)
    if info is None:
        raise HTTPException(status_code=404, detail="Anexo não encontrado.")
    if not usuario_tem_acesso_inquerito(info["id_inquerito"], usuario, escrita=True):
        raise HTTPException(status_code=403, detail="Sem permissão para este inquérito.")
    texto = payload.texto.strip() or None
    atualizar_transcricao_anexo(id_anexo, fonte, texto)
    return {"ok": True, "transcricao": texto or ""}


class OcrArquivoIn(BaseModel):
    """Corpo aceito pelo PUT que corrige o texto OCR de um arquivo (revisão humana do que a
    imagem contém). Vazio = "esta imagem não tem texto" — não volta para a fila de pendentes."""

    texto: str = Field("", max_length=20000)


class ClassificacaoArquivoIn(BaseModel):
    """Corpo aceito pelo PUT que define a classificação de um arquivo à mão. Vazio = limpar."""

    classificacao: str = Field("", max_length=200)


@router.put("/api/investigacao/arquivos/{id_arquivo}/ocr")
def editar_ocr_arquivo_rota(id_arquivo: int, payload: OcrArquivoIn,
                            usuario: dict = Depends(exigir_aprovado)):
    """Corrige (ou escreve do zero) o texto OCR de um arquivo no modal da tela de Arquivos —
    o mesmo gesto da revisão de transcrição nos anexos: o OCR erra em print torto e o
    investigador, com a imagem na frente, é quem sabe o que está escrito."""
    info = obter_binario_arquivo(id_arquivo)
    if info is None:
        raise HTTPException(status_code=404, detail="Arquivo não encontrado.")
    if not usuario_tem_acesso_inquerito(info["id_inquerito"], usuario, escrita=True):
        raise HTTPException(status_code=403, detail="Sem permissão para este inquérito.")
    texto = payload.texto.strip() or None
    atualizar_texto_ocr(id_arquivo, texto)
    return {"ok": True, "texto": texto or ""}


@router.put("/api/investigacao/arquivos/{id_arquivo}/classificacao")
def editar_classificacao_arquivo_rota(id_arquivo: int, payload: ClassificacaoArquivoIn,
                                      usuario: dict = Depends(exigir_aprovado)):
    """Define à mão a classificação de um arquivo, corrigindo a classe que a IA (CLIP) atribuiu.
    O valor precisa ser um termo do vocabulário (CATEGORIAS_IMAGEM, incluindo as desativadas —
    senão um termo renomeado ontem ficava impossível de manter). O job em lote só pega de
    classificacao_ia IS NULL: o que o humano escolheu a IA não por cima."""
    info = obter_binario_arquivo(id_arquivo)
    if info is None:
        raise HTTPException(status_code=404, detail="Arquivo não encontrado.")
    if not usuario_tem_acesso_inquerito(info["id_inquerito"], usuario, escrita=True):
        raise HTTPException(status_code=403, detail="Sem permissão para este inquérito.")
    classificacao = payload.classificacao.strip() or None
    if classificacao is not None:
        vocab = {c["termo"] for c in listar_categorias()}
        if classificacao not in vocab:
            raise HTTPException(status_code=400,
                                detail="Classificação fora do vocabulário de imagens.")
    atualizar_classificacao_arquivo(id_arquivo, classificacao)
    return {"ok": True, "classificacao": classificacao or "",
            "confianca": 1.0 if classificacao else None}


@router.get("/api/investigacao/conversas/{id_conversa}/participantes")
def participantes(id_conversa: int, fonte: str = "wpp", usuario: dict = Depends(exigir_aprovado)):
    """Lista os participantes de uma conversa (exceto o dono do aparelho), com associação atual."""
    if fonte not in _FONTES_CONVERSA:
        raise HTTPException(status_code=400, detail="Fonte inválida.")
    _exigir_acesso_via_conversa(id_conversa, fonte, usuario)
    return listar_participantes_conversa(id_conversa, fonte)


@router.get("/api/investigacao/conversas/{id_conversa}/foto")
def foto_conversa(id_conversa: int, fonte: str = "wpp", usuario: dict = Depends(exigir_aprovado)):
    """Foto de perfil do outro lado da conversa (só WhatsApp), lida sob demanda direto do .ufdr —
    o binário não é copiado nem persistido no banco (só o caminho fica gravado no contato).
    Cacheada em memória (funcoes/ufdr_cache.py), mesma política de 1h por inatividade."""
    if fonte != "wpp":
        raise HTTPException(status_code=404, detail="Sem foto para esta fonte.")
    _exigir_acesso_via_conversa(id_conversa, fonte, usuario)

    info = obter_foto_conversa(id_conversa)
    if info is None or not info["caminho_zip"]:
        raise HTTPException(status_code=404, detail="Sem foto para esta conversa.")

    caminho_ufdr = fonte_ufdr(info["no_arquivo_ufdr"])
    if caminho_ufdr is None:
        raise HTTPException(status_code=404,
                            detail="Arquivo .ufdr original não encontrado (nem no servidor "
                                   "central, nem no servidor da delegacia).")
    chave_processada = (str(caminho_ufdr), info["caminho_zip"], "img")
    dados = ufdr_cache.obter_conteudo(chave_processada)
    if dados is None:
        try:
            bruto = ufdr_cache.ler_membro(caminho_ufdr, info["caminho_zip"])
        except (FileNotFoundError, KeyError):
            raise HTTPException(status_code=404, detail="Foto não encontrada no arquivo original.")
        try:
            dados = redimensionar_imagem(bruto)
        except Exception:
            raise HTTPException(status_code=422, detail="Não foi possível processar a foto.")
        ufdr_cache.guardar_conteudo(chave_processada, dados)

    return Response(content=dados, media_type="image/jpeg")


@router.post("/api/investigacao/contatos/{id_contato}/associacao")
def associar(id_contato: int, payload: AssociacaoIn, fonte: str = "wpp", usuario: dict = Depends(exigir_aprovado)):
    """Associa (ou limpa, se todos os campos vierem nulos) um contato a pessoa e/ou município/UF."""
    if fonte not in _FONTES_CONVERSA:
        raise HTTPException(status_code=400, detail="Fonte inválida.")
    _exigir_acesso_via_contato(id_contato, fonte, usuario)
    atualizado = associar_contato(
        id_contato, fonte, payload.id_pessoa, payload.id_municipio, payload.id_uf, usuario["id_usuario"]
    )
    if not atualizado:
        raise HTTPException(status_code=404, detail="Contato não encontrado.")
    return {"ok": True}


@router.get("/api/investigacao/inqueritos/{id_inquerito}/contatos")
def contatos(
    id_inquerito: int,
    extracao: int | None = None,
    busca: str | None = None,
    tipo: str | None = None,
    ordenar: str | None = None,
    pagina: int = 1,
    limite: int = 100,
    usuario: dict = Depends(exigir_acesso_inquerito),
):
    """Lista, paginado, os contatos da agenda de um inquérito.
    Filtros: ?extracao=ID (um aparelho), ?busca=texto, ?tipo=tp_contato.
    Ordenação: ?ordenar= (ver _ORDENACOES_CONTATO; valor fora da lista cai no padrão).
    Paginação: ?pagina= e ?limite=."""
    return listar_contatos(id_inquerito, extracao, busca, tipo, ordenar, pagina, limite)


@router.get("/api/investigacao/inqueritos/{id_inquerito}/contatos/filtros")
def contatos_filtros(
    id_inquerito: int,
    extracao: int | None = None,
    usuario: dict = Depends(exigir_acesso_inquerito),
):
    """Valores distintos para popular os selects de filtro da página de contatos."""
    return facetas_contato(id_inquerito, extracao)


@router.get("/api/investigacao/inqueritos/{id_inquerito}/ligacoes")
def ligacoes(
    id_inquerito: int,
    extracao: int | None = None,
    busca: str | None = None,
    direcao: str | None = None,
    data_de: str | None = None,
    data_ate: str | None = None,
    periodo: str | None = None,
    ordenar: str | None = None,
    pagina: int = 1,
    limite: int = 100,
    usuario: dict = Depends(exigir_acesso_inquerito),
):
    """Lista, paginado, as ligações de um inquérito.
    Filtros: ?extracao=ID, ?busca=texto, ?direcao=tp_direcao,
    ?data_de=AAAA-MM-DD&data_ate=AAAA-MM-DD, ?periodo=madrugada|manha|tarde|noite.
    Ordenação: ?ordenar= (ver _ORDENACOES_LIGACAO; valor fora da lista cai no padrão).
    Paginação: ?pagina= e ?limite=."""
    return listar_ligacoes(id_inquerito, extracao, busca, direcao, data_de, data_ate, periodo,
                           ordenar, pagina, limite)


@router.get("/api/investigacao/inqueritos/{id_inquerito}/ligacoes/filtros")
def ligacoes_filtros(
    id_inquerito: int,
    extracao: int | None = None,
    usuario: dict = Depends(exigir_acesso_inquerito),
):
    """Valores distintos para popular os selects de filtro da página de ligações."""
    return facetas_ligacao(id_inquerito, extracao)


def _ids_extracao(extracoes: str | None) -> list[int] | None:
    """Lê ?extracoes=1,2,3 (a seleção de fontes da tela de localizações) como lista de ids.

    Ausente = sem recorte por aparelho, que é diferente de presente e vazio: quem desmarca todos
    os celulares e deixa só as antenas está pedindo nenhum registro de aparelho, e recebe isso."""
    if extracoes is None:
        return None
    return [int(p) for p in extracoes.split(",") if p.strip().lstrip("-").isdigit()]


@router.get("/api/investigacao/inqueritos/{id_inquerito}/localizacoes")
def localizacoes(
    id_inquerito: int,
    extracao: int | None = None,
    extracoes: str | None = None,
    busca: str | None = None,
    app: str | None = None,
    categoria: str | None = None,
    so_coordenadas: bool = False,
    data_de: str | None = None,
    data_ate: str | None = None,
    periodo: str | None = None,
    ordenar: str | None = None,
    pagina: int = 1,
    limite: int = 100,
    camada: int | None = None,
    feicao: int | None = None,
    usuario: dict = Depends(exigir_acesso_inquerito),
):
    """Lista, paginado, as localizações de um inquérito. Filtros: ?extracao=ID (um aparelho),
    ?busca=texto (endereço/descrição/app), ?app=, ?categoria=, ?so_coordenadas=true (só com lat/lon),
    ?data_de=AAAA-MM-DD e ?data_ate=AAAA-MM-DD, ?periodo=madrugada|manha|tarde|noite,
    ?camada=ID (só o que cai dentro de uma camada de shapefile importada) ou ?feicao=ID (só uma
    área dela), ?ordenar=recentes|antigos, ?extracoes=1,2,3 (seleção de fontes: vários aparelhos
    de uma vez; presente e vazia = nenhum). Paginação: ?pagina= e ?limite=."""
    return listar_localizacoes(
        id_inquerito, extracao, busca, app, categoria, so_coordenadas,
        data_de, data_ate, periodo, ordenar, pagina, limite,
        id_camada=camada, id_feicao=feicao, ids_extracao=_ids_extracao(extracoes),
    )


class GeocodificarIn(BaseModel):
    """Corpo do POST que geocodifica (reversa) coordenadas sob demanda: lista de pares [lat, lon]."""
    coordenadas: list[list[float]] = Field(default_factory=list)


@router.post("/api/investigacao/inqueritos/{id_inquerito}/localizacoes/geocodificar")
def localizacoes_geocodificar(
    id_inquerito: int,
    corpo: GeocodificarIn,
    usuario: dict = Depends(exigir_escrita_inquerito),
):
    """Busca o endereço das coordenadas informadas via OpenStreetMap/Nominatim (sob demanda, acionado
    pelo botão da página). Usa/atualiza o cache global e devolve {enderecos, cacheadas, geocodificadas,
    restantes}. Processa no máximo LIMITE_POR_CHAMADA coordenadas novas por chamada."""
    return geocodificar_coordenadas(corpo.coordenadas)


@router.get("/api/investigacao/inqueritos/{id_inquerito}/localizacoes/grafo")
def localizacoes_grafo(
    id_inquerito: int,
    extracao: int | None = None,
    extracoes: str | None = None,
    busca: str | None = None,
    app: str | None = None,
    categoria: str | None = None,
    data_de: str | None = None,
    data_ate: str | None = None,
    periodo: str | None = None,
    nivel: int = 0,
    lat_min: float | None = None,
    lat_max: float | None = None,
    lon_min: float | None = None,
    lon_max: float | None = None,
    camada: int | None = None,
    feicao: int | None = None,
    usuario: dict = Depends(exigir_acesso_inquerito),
):
    """Grafo de lugares das localizações, agregado por grade. ?nivel=0..6 escolhe o tamanho da célula
    (do país ao endereço) — a aba Grafo o deriva do zoom do mapa. ?lat_min/lat_max/lon_min/lon_max
    recortam à área visível; ?camada=ID/?feicao=ID recortam ao que cai dentro de uma área importada
    de shapefile. ?extracoes=1,2,3 é a seleção de fontes da tela. Demais filtros iguais aos da tabela. Devolve nós (lugares) e arestas
    (deslocamentos entre lugares consecutivos no tempo)."""
    return grafo_localizacoes(
        id_inquerito, extracao, busca, app, categoria, data_de, data_ate, periodo,
        nivel, lat_min, lat_max, lon_min, lon_max,
        id_camada=camada, id_feicao=feicao, ids_extracao=_ids_extracao(extracoes),
    )


@router.get("/api/investigacao/inqueritos/{id_inquerito}/localizacoes/filtros")
def localizacoes_filtros(id_inquerito: int, extracao: int | None = None, usuario: dict = Depends(exigir_acesso_inquerito)):
    """Valores distintos (apps e categorias) para os selects de filtro das localizações."""
    return facetas_localizacao(id_inquerito, extracao)


# ---------- Camadas geoespaciais (shapefile) sobre o mapa das localizações ----------


def _exigir_camada_do_inquerito(id_inquerito: int, id_camada: int) -> None:
    """A camada vem no path abaixo do inquérito, mas o id é do cliente: sem esta conferência,
    trocar o número na URL leria a camada de outro inquérito."""
    dono = obter_id_inquerito_de_camada(id_camada)
    if dono is None or dono != id_inquerito:
        raise HTTPException(status_code=404, detail="Camada não encontrada neste inquérito.")


@router.get("/api/investigacao/inqueritos/{id_inquerito}/localizacoes/camadas")
def localizacoes_camadas(id_inquerito: int, usuario: dict = Depends(exigir_acesso_inquerito)):
    """Camadas de polígonos importadas de shapefile neste inquérito (metadados, sem a geometria)."""
    return listar_camadas(id_inquerito)


@router.post("/api/investigacao/inqueritos/{id_inquerito}/localizacoes/camadas")
def importar_camada(
    id_inquerito: int,
    arquivos: list[UploadFile] = File(...),
    nome: str | None = Form(default=None),
    cor: str | None = Form(default=None),
    usuario: dict = Depends(exigir_escrita_inquerito),
):
    """Importa um shapefile de polígonos como camada do mapa das localizações.

    Aceita o `.zip` do conjunto ou os arquivos soltos (.shp + .dbf + .prj + .shx) enviados de uma
    vez. A geometria é reprojetada para WGS 84 na importação — ver funcoes/geoespacial_utils.py."""
    enviados: list[tuple[str, bytes]] = []
    try:
        for arquivo in arquivos:
            if arquivo.filename:
                enviados.append((arquivo.filename, arquivo.file.read()))
    finally:
        for arquivo in arquivos:
            arquivo.file.close()

    if not enviados:
        raise HTTPException(status_code=400, detail="Nenhum arquivo enviado.")

    try:
        return importar_shapefile(
            id_inquerito, enviados, nome, cor,
            importada_por=usuario.get("no_usuario") or usuario.get("no_login"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/api/investigacao/inqueritos/{id_inquerito}/localizacoes/camadas/{id_camada}")
def camada_geojson(id_inquerito: int, id_camada: int, usuario: dict = Depends(exigir_acesso_inquerito)):
    """A camada como FeatureCollection GeoJSON, do jeito que o Leaflet desenha."""
    _exigir_camada_do_inquerito(id_inquerito, id_camada)
    try:
        return geojson_camada(id_camada)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/api/investigacao/inqueritos/{id_inquerito}/localizacoes/camadas/{id_camada}/contagem")
def camada_contagem(
    id_inquerito: int,
    id_camada: int,
    extracao: int | None = None,
    extracoes: str | None = None,
    busca: str | None = None,
    app: str | None = None,
    categoria: str | None = None,
    data_de: str | None = None,
    data_ate: str | None = None,
    periodo: str | None = None,
    usuario: dict = Depends(exigir_acesso_inquerito),
):
    """Quantas localizações do recorte atual caíram dentro de cada área da camada. Os filtros são
    os mesmos da tabela e do grafo — o que muda é só o agrupamento."""
    _exigir_camada_do_inquerito(id_inquerito, id_camada)
    return contagem_por_feicao(
        id_inquerito, id_camada, extracao, busca, app, categoria, data_de, data_ate, periodo,
        ids_extracao=_ids_extracao(extracoes),
    )


class CamadaIn(BaseModel):
    """Corpo do PUT que renomeia (e opcionalmente recolore) uma camada geoespacial."""

    nome: str = Field(..., max_length=200)
    cor: str | None = Field(default=None, max_length=9)


@router.put("/api/investigacao/inqueritos/{id_inquerito}/localizacoes/camadas/{id_camada}")
def atualizar_camada(
    id_inquerito: int,
    id_camada: int,
    corpo: CamadaIn,
    usuario: dict = Depends(exigir_escrita_inquerito),
):
    """Renomeia a camada e, se `cor` vier, troca a cor com que ela é desenhada."""
    _exigir_camada_do_inquerito(id_inquerito, id_camada)
    try:
        renomear_camada(id_camada, corpo.nome, corpo.cor)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


@router.delete("/api/investigacao/inqueritos/{id_inquerito}/localizacoes/camadas/{id_camada}")
def remover_camada(id_inquerito: int, id_camada: int, usuario: dict = Depends(exigir_escrita_inquerito)):
    """Apaga a camada e todas as suas áreas (qualquer usuário com acesso ao inquérito)."""
    _exigir_camada_do_inquerito(id_inquerito, id_camada)
    apagar_camada(id_camada)
    return {"ok": True}


@router.get("/api/investigacao/inqueritos/{id_inquerito}/notificacoes")
def notificacoes(
    id_inquerito: int,
    extracao: int | None = None,
    busca: str | None = None,
    app: str | None = None,
    data_de: str | None = None,
    data_ate: str | None = None,
    periodo: str | None = None,
    ordenar: str | None = None,
    pagina: int = 1,
    limite: int = 100,
    usuario: dict = Depends(exigir_acesso_inquerito),
):
    """Lista, paginado, as notificações de um inquérito.
    Filtros: ?extracao=ID, ?busca=texto, ?app=app,
    ?data_de=AAAA-MM-DD&data_ate=AAAA-MM-DD, ?periodo=madrugada|manha|tarde|noite.
    Ordenação: ?ordenar= (ver _ORDENACOES_NOTIFICACAO; valor fora da lista cai no padrão).
    Paginação: ?pagina= e ?limite=."""
    return listar_notificacoes(id_inquerito, extracao, busca, app, data_de, data_ate, periodo,
                               ordenar, pagina, limite)


@router.get("/api/investigacao/inqueritos/{id_inquerito}/notificacoes/filtros")
def notificacoes_filtros(
    id_inquerito: int,
    extracao: int | None = None,
    usuario: dict = Depends(exigir_acesso_inquerito),
):
    """Valores distintos para popular os selects de filtro da página de notificações."""
    return facetas_notificacao(id_inquerito, extracao)


@router.get("/api/investigacao/inqueritos/{id_inquerito}/notas")
def notas(id_inquerito: int, extracao: int | None = None, usuario: dict = Depends(exigir_acesso_inquerito)):
    """Lista as notas de blocos de notas (Samsung Notes, Apple Notes/iCloud Backup, Google Keep…)
    de um inquérito (?extracao=ID restringe a um aparelho)."""
    return listar_notas(id_inquerito, extracao)


@router.get("/api/investigacao/inqueritos/{id_inquerito}/navegacao")
def navegacao(
    id_inquerito: int,
    extracao: int | None = None,
    busca: str | None = None,
    navegador: str | None = None,
    data_de: str | None = None,
    data_ate: str | None = None,
    periodo: str | None = None,
    ordenar: str | None = None,
    pagina: int = 1,
    limite: int = 100,
    usuario: dict = Depends(exigir_acesso_inquerito),
):
    """Lista, paginado, o histórico de navegação (páginas visitadas) de um inquérito. Filtros:
    ?extracao=ID (um aparelho), ?busca=texto (título/URL/conta), ?navegador=, ?data_de=AAAA-MM-DD e
    ?data_ate=AAAA-MM-DD (data da visita), ?periodo=madrugada|manha|tarde|noite,
    ?ordenar=recentes|antigos|visitas|titulo. Paginação: ?pagina= e ?limite=."""
    return listar_paginas_visitadas(
        id_inquerito, extracao, busca, navegador, data_de, data_ate, periodo, ordenar, pagina, limite,
    )


@router.get("/api/investigacao/inqueritos/{id_inquerito}/navegacao/filtros")
def navegacao_filtros(id_inquerito: int, extracao: int | None = None, usuario: dict = Depends(exigir_acesso_inquerito)):
    """Navegadores distintos para o select de filtro do histórico de navegação."""
    return facetas_navegacao(id_inquerito, extracao)


@router.get("/api/investigacao/inqueritos/{id_inquerito}/navegacao/resumo")
def navegacao_resumo(
    id_inquerito: int,
    extracao: int | None = None,
    busca: str | None = None,
    navegador: str | None = None,
    data_de: str | None = None,
    data_ate: str | None = None,
    periodo: str | None = None,
    usuario: dict = Depends(exigir_acesso_inquerito),
):
    """KPIs do histórico de navegação (total de visitas, sites mais acessados, navegadores, período
    do dia etc.), respeitando os mesmos filtros da listagem paginada."""
    return resumo_navegacao(id_inquerito, extracao, busca, navegador, data_de, data_ate, periodo)


@router.get("/api/investigacao/inqueritos/{id_inquerito}/extracoes-lista")
def extracoes_lista(id_inquerito: int, usuario: dict = Depends(exigir_acesso_inquerito)):
    """Lista as extrações de um inquérito para popular o filtro da timeline."""
    return listar_extracoes_do_inquerito(id_inquerito)


@router.get("/api/investigacao/inqueritos/{id_inquerito}/aplicativos")
def aplicativos(
    id_inquerito: int,
    extracao: int | None = None,
    busca: str | None = None,
    pagina: int = 1,
    limite: int = 100,
    usuario: dict = Depends(exigir_acesso_inquerito),
):
    """Lista, paginado, os aplicativos instalados de um inquérito.
    Filtros: ?extracao=ID (um aparelho), ?busca=texto.
    Paginação: ?pagina= e ?limite=."""
    return listar_aplicativos(id_inquerito, extracao, busca, pagina, limite)


@router.get("/api/investigacao/inqueritos/{id_inquerito}/timeline")
def timeline(
    id_inquerito: int,
    extracao: str | None = None,
    busca: str | None = None,
    categoria: str | None = None,
    data_de: str | None = None,
    data_ate: str | None = None,
    conjunto: str | None = None,
    periodo: str | None = None,
    somente_geo: bool = False,
    pagina: int = 1,
    limite: int = 200,
    analitico: bool = False,
    sem_total: bool = False,
    apenas_total: bool = False,
    usuario: dict = Depends(exigir_acesso_inquerito),
):
    """Linha do tempo unificada de todas as atividades do inquérito. ?sem_total=1 responde a
    página sem contar o recorte (total=null — a tela pede ?apenas_total=1 em paralelo; o total
    fica em cache por recorte). Filtros: ?extracao=ID ou
    ?extracao=ID1,ID2 (mais de um aparelho de uma vez), ?busca=texto, ?categoria=ligacao|
    mensagem_wpp|mensagem_sms|localizacao|notificacao|navegacao|email (ou múltiplas separadas por
    vírgula), ?conjunto=investigativo|comunicacao|localizacao|internet|financeiro|aparelho
    (recorte temático de categorias — no investigativo os feriados importados automaticamente do
    calendário ficam de fora), ?periodo=madrugada|manha|tarde|noite (faixa de horário local),
    ?data_de=AAAA-MM-DD e ?data_ate=AAAA-MM-DD, ?somente_geo=1 (só eventos com coordenada).
    Paginação: ?pagina= e ?limite=. Com ?analitico=true retorna o resumo diário completo usado
    pela visualização analítica do tema Orbit (incluindo a distribuição por período do dia)."""
    ids_extracao = None
    if extracao:
        ids_extracao = [int(v) for v in extracao.split(",") if v.strip().isdigit()] or None
    return listar_timeline(
        id_inquerito, ids_extracao, busca, categoria, data_de, data_ate, conjunto, periodo,
        somente_geo, pagina, limite, analitico, apenas_total, sem_total
    )


@router.get("/api/investigacao/inqueritos/{id_inquerito}/emails")
def emails(
    id_inquerito: int,
    extracao: int | None = None,
    busca: str | None = None,
    fonte: str | None = None,
    remetente: str | None = None,
    data_de: str | None = None,
    data_ate: str | None = None,
    ordenar: str | None = None,
    pagina: int = 1,
    limite: int = 100,
    usuario: dict = Depends(exigir_acesso_inquerito),
):
    """Lista, paginado, os e-mails de um inquérito. Filtros: ?extracao=ID (um aparelho),
    ?busca=texto (assunto/remetente/destinatários/corpo), ?fonte=, ?remetente=,
    ?data_de=AAAA-MM-DD e ?data_ate=AAAA-MM-DD. Ordenação: ?ordenar= (ver _ORDENACOES_EMAIL;
    valor fora da lista cai no padrão). Paginação: ?pagina= e ?limite=."""
    return listar_emails(id_inquerito, extracao, busca, fonte, remetente, data_de, data_ate,
                         ordenar, pagina, limite)


@router.get("/api/investigacao/inqueritos/{id_inquerito}/emails/filtros")
def emails_filtros(id_inquerito: int, extracao: int | None = None, usuario: dict = Depends(exigir_acesso_inquerito)):
    """Fontes e remetentes distintos para os selects de filtro da página de e-mails."""
    return facetas_emails(id_inquerito, extracao)


@router.get("/api/investigacao/inqueritos/{id_inquerito}/emails/{id_email}/corpo")
def email_corpo(id_inquerito: int, id_email: int, usuario: dict = Depends(exigir_acesso_inquerito)):
    """Corpo INTEGRAL de um e-mail para o modal "Ver HTML". A listagem transporta só uma prévia
    (a coluna "Conteúdo" não precisa de mais, e um corpo integral chega a 200 mil caracteres —
    100 por página travavam a tela); o resto vem sob demanda, quando o modal abre."""
    corpo = obter_corpo_email(id_inquerito, id_email)
    if corpo is None:
        raise HTTPException(status_code=404, detail="E-mail não encontrado neste inquérito.")
    return {"tx_corpo": corpo}


@router.get("/api/investigacao/inqueritos/{id_inquerito}/contas-financeiras")
def contas_financeiras(id_inquerito: int, extracao: int | None = None, usuario: dict = Depends(exigir_acesso_inquerito)):
    """Lista contas bancárias/financeiras modeladas pelo UFDR."""
    return listar_contas_financeiras(id_inquerito, extracao)


@router.get("/api/investigacao/inqueritos/{id_inquerito}/donos")
def donos(id_inquerito: int, extracao: int | None = None, usuario: dict = Depends(exigir_acesso_inquerito)):
    """Lista o(s) dono(s) do aparelho de um inquérito (?extracao=ID restringe a um aparelho)."""
    return listar_donos(id_inquerito, extracao)


@router.get("/api/investigacao/inqueritos/{id_inquerito}/contas-usuario")
def contas_usuario(id_inquerito: int, extracao: int | None = None, usuario: dict = Depends(exigir_acesso_inquerito)):
    """Lista todas as contas de usuário encontradas no aparelho pelo UFDR."""
    return listar_contas_usuario(id_inquerito, extracao)


@router.get("/api/investigacao/inqueritos/{id_inquerito}/senhas")
def senhas(id_inquerito: int, extracao: int | None = None, usuario: dict = Depends(exigir_acesso_inquerito)):
    """Lista as senhas/segredos do chaveiro (UFDR Password e Nuvem IPED) de um inquérito."""
    return listar_senhas(id_inquerito, extracao)


@router.get("/api/investigacao/inqueritos/{id_inquerito}/autofill")
def autofill(id_inquerito: int, extracao: int | None = None, usuario: dict = Depends(exigir_acesso_inquerito)):
    """Lista os campos de preenchimento automático (UFDR Autofill e Nuvem IPED)."""
    return listar_autofill(id_inquerito, extracao)


@router.get("/api/investigacao/inqueritos/{id_inquerito}/arquivos")
def arquivos_dispositivo(
    id_inquerito: int,
    extracao: int | None = None,
    categoria: str | None = None,
    busca: str | None = None,
    disponivel: bool = False,
    status: str | None = None,
    tamanho_min: int | None = None,
    data_de: str | None = None,
    data_ate: str | None = None,
    periodo: str | None = None,
    ordenar: str | None = None,
    pagina: int = 1,
    limite: int = 100,
    pasta: str | None = None,
    ocr: str | None = None,
    classificacao_ia: str | None = None,
    marcados: bool = False,
    usuario: dict = Depends(exigir_acesso_inquerito),
):
    """Lista, paginado, os arquivos do aparelho (filesystem) de um inquérito. Filtros: ?extracao=ID
    (um aparelho), ?categoria=Imagem|Vídeo|Áudio|Documento|Compactado|Outro, ?busca=texto (nome,
    caminho ou texto extraído por OCR), ?disponivel=true (só os que têm binário no .ufdr),
    ?status=intacto|apagado, ?tamanho_min=bytes, ?data_de=AAAA-MM-DD e ?data_ate=AAAA-MM-DD (data de
    modificação), ?periodo=madrugada|manha|tarde|noite (faixa de horário local),
    ?ocr=com|sem|pendente (situação do OCR da imagem),
    ?classificacao_ia=com|sem (classificação por IA: com/sem ou um valor exato),
    ?marcados=true (itens que o perito marcou/selecionou dentro do IPED — bookmarks.iped),
    ?ordenar=categoria|nome|nome_desc|maiores|menores|recentes|antigos,
    ?pasta=DCIM/Camera (só os arquivos soltos nessa pasta; '' = raiz — usado na navegação por pastas).
    Paginação: ?pagina= e ?limite=."""
    return listar_arquivos(
        id_inquerito, extracao, categoria, busca, disponivel,
        status, tamanho_min, data_de, data_ate, periodo, ordenar, pagina, limite, pasta, ocr,
        classificacao_ia, marcados,
    )


@router.get("/api/investigacao/inqueritos/{id_inquerito}/arquivos/pastas")
def pastas_arquivos(
    id_inquerito: int,
    extracao: int | None = None,
    pasta: str = "",
    categoria: str | None = None,
    busca: str | None = None,
    disponivel: bool = False,
    status: str | None = None,
    tamanho_min: int | None = None,
    data_de: str | None = None,
    data_ate: str | None = None,
    periodo: str | None = None,
    ocr: str | None = None,
    usuario: dict = Depends(exigir_acesso_inquerito),
):
    """Subpastas imediatas de ?pasta= (vazio = raiz do aparelho), com contagem e volume recursivos —
    alimenta a visualização por navegação em pastas da tela de arquivos. Aceita os mesmos filtros da
    listagem, para a árvore acompanhar o recorte em tela."""
    return listar_pastas_arquivos(
        id_inquerito, extracao, pasta, categoria, busca, disponivel,
        status, tamanho_min, data_de, data_ate, periodo, ocr,
    )


@router.get("/api/investigacao/inqueritos/{id_inquerito}/arquivos/periodo")
def periodo_arquivos_route(
    id_inquerito: int,
    extracao: int | None = None,
    usuario: dict = Depends(exigir_acesso_inquerito),
):
    """Data de modificação mínima e máxima dos arquivos do inquérito (?extracao=ID restringe a um
    aparelho) — alimenta os atalhos de período ("7 dias", "30 dias", "12 meses") da tela de
    arquivos. Devolve {min, max} em 'AAAA-MM-DD' ou {} quando não há arquivo com data."""
    return periodo_arquivos(id_inquerito, extracao)


@router.get("/api/investigacao/arquivos/{id_arquivo}/metadados")
def metadados_arquivo(id_arquivo: int, usuario: dict = Depends(exigir_aprovado)):
    """Metadados de mídia do aparelho, lidos sob demanda do binário no .ufdr:
    imagem → EXIF (dimensões, câmera, data da captura, GPS…) mais o texto já reconhecido por OCR
    (campo `ocr`, do banco); vídeo → localização (GPS) e data de criação do metadado QuickTime.
    Devolve {} quando não é mídia, não tem binário ou não há metadado."""
    info = obter_binario_arquivo(id_arquivo)
    if info is None:
        raise HTTPException(status_code=404, detail="Arquivo não encontrado.")
    if not usuario_tem_acesso_inquerito(info["id_inquerito"], usuario):
        raise HTTPException(status_code=403, detail="Sem permissão para este inquérito.")
    mime = info["tp_mime"] or ""
    if not info["caminho_zip"]:
        return {}
    if info["caminho_zip"].startswith("IPED::"):
        # Itens da Nuvem IPED: o binário vive no storage do caso, não num ZIP. Lê de lá e
        # passa pelo MESMO extrator do UFDR — devolver {} aqui era uma lacuna registrada como
        # backlog, e o custo real é uma leitura de blob (que o cache do iped_cache já cobre).
        resolvido_iped = _resolver_arquivo_iped(info["caminho_zip"])
        if not resolvido_iped:
            return obter_texto_ocr(id_arquivo) if mime.startswith("image/") else {}
        pasta_modulo, origem, hash_md5, id_fonte = resolvido_iped
        try:
            dados = _ler_blob_iped(pasta_modulo, origem, hash_md5, id_fonte)
        except FileNotFoundError:
            # pasta de evidências ausente é normal em entrega portátil: devolve o que houver
            return obter_texto_ocr(id_arquivo) if mime.startswith("image/") else {}
        if mime.startswith("image/"):
            return {**extrair_metadados_imagem(dados), **obter_texto_ocr(id_arquivo)}
        if mime.startswith("video/"):
            return extrair_metadados_video(BytesIO(dados))
        return {}
    # Nuvem Google: ponteiro de dois níveis tratado antes da resolução "flat".
    aninhado = ufdr_cache.desmembrar_aninhado(info["caminho_zip"])
    if aninhado:
        membro_externo, caminho_interno = aninhado
        caminho_ufdr = fonte_ufdr(info["no_arquivo_ufdr"])
        if caminho_ufdr is None:
            return {}
        try:
            if mime.startswith("image/"):
                dados = ufdr_cache.ler_membro_aninhado(caminho_ufdr, membro_externo, caminho_interno)
                return {**extrair_metadados_imagem(dados), **obter_texto_ocr(id_arquivo)}
            if mime.startswith("video/"):
                with ufdr_cache.abrir_membro_aninhado(caminho_ufdr, membro_externo, caminho_interno) as fp:
                    return extrair_metadados_video(fp)
        except (FileNotFoundError, KeyError):
            raise HTTPException(status_code=404, detail="Arquivo não encontrado no ZIP original.")
        return {}

    resolvido = _resolver_arquivo_zip(info["caminho_zip"], info["no_arquivo_ufdr"])
    if not resolvido:
        return {}
    caminho_zip_real, caminho_interno = resolvido

    try:
        if mime.startswith("image/"):
            with ufdr_zip.abrir(caminho_zip_real) as zf:
                dados = zf.read(caminho_interno)
            return {**extrair_metadados_imagem(dados), **obter_texto_ocr(id_arquivo)}
        if mime.startswith("video/"):
            with ufdr_zip.abrir(caminho_zip_real) as zf:
                with zf.open(caminho_interno) as fp:
                    return extrair_metadados_video(fp)
    except (FileNotFoundError, KeyError):
        raise HTTPException(status_code=404, detail="Arquivo não encontrado no ZIP original.")
    return {}


@router.get("/api/investigacao/inqueritos/{id_inquerito}/chaves-pix")
def chaves_pix(id_inquerito: int, extracao: int | None = None, usuario: dict = Depends(exigir_acesso_inquerito)):
    """Lista as chaves PIX detectadas nas conversas do inquérito (?extracao=ID restringe a um aparelho),
    cada uma ligada ao remetente e à conversa em que apareceu."""
    return listar_chaves_pix(id_inquerito, extracao)


@router.get("/api/investigacao/extracoes/{id_extracao}/chaves-pix/status")
def chaves_pix_status(id_extracao: int, usuario: dict = Depends(exigir_aprovado)):
    """Diz se a extração já foi escaneada por chaves PIX e quantas foram achadas (para a página decidir
    entre mostrar o botão 'detectar' ou a lista)."""
    status = status_deteccao_extracao(id_extracao)
    if status is None:
        raise HTTPException(status_code=404, detail="Extração não encontrada.")
    if not usuario_tem_acesso_inquerito(status["id_inquerito"], usuario):
        raise HTTPException(status_code=403, detail="Sem permissão para este inquérito.")
    return {"escaneado": status["escaneado"], "qt_chaves": status["qt_chaves"]}


@router.post("/api/investigacao/extracoes/{id_extracao}/chaves-pix/detectar")
def chaves_pix_detectar(id_extracao: int, usuario: dict = Depends(exigir_aprovado)):
    """Varre as mensagens da extração e (re)popula as chaves PIX ligadas aos contatos. Idempotente."""
    status = status_deteccao_extracao(id_extracao)
    if status is None:
        raise HTTPException(status_code=404, detail="Extração não encontrada.")
    if not usuario_tem_acesso_inquerito(status["id_inquerito"], usuario, escrita=True):
        raise HTTPException(status_code=403, detail="Sem permissão para este inquérito.")
    total = detectar_chaves_pix(id_extracao)
    return {"ok": True, "total": total}


# ---------- Redes Wi-Fi ----------


@router.get("/api/investigacao/inqueritos/{id_inquerito}/redes-wifi")
def redes_wifi(id_inquerito: int, extracao: int | None = None, usuario: dict = Depends(exigir_acesso_inquerito)):
    """Lista as redes Wi-Fi detectadas no UFDR do inquérito (?extracao=ID restringe a um aparelho),
    com SSID, BSSID, tipo de segurança, senha (se disponível), quantidade de conexões e metadados."""
    return listar_redes_wifi(id_inquerito, extracao)


@router.get("/api/investigacao/extracoes/{id_extracao}/redes-wifi/status")
def redes_wifi_status(id_extracao: int, usuario: dict = Depends(exigir_aprovado)):
    """Diz se a extração tem redes Wi-Fi e quantas foram achadas."""
    status = status_wifi_extracao(id_extracao)
    if status is None:
        raise HTTPException(status_code=404, detail="Extração não encontrada.")
    if not usuario_tem_acesso_inquerito(status["id_inquerito"], usuario):
        raise HTTPException(status_code=403, detail="Sem permissão para este inquérito.")
    return {"escaneado": status["escaneado"], "qt_redes": status["qt_redes"]}


# ---------- Domínios do UFDR importados na auditoria de aproveitamento ----------
#
# Uso de app, eventos do aparelho, sensores, dispositivos reconhecidos, conexões, cookies,
# favoritos, downloads, uso de rede, atividade em rede social, buscas, envios para a nuvem,
# gravações, calendário, uso de aplicativo, trajetos, identidade do aparelho, antenas de
# telefonia, caixa postal, transferências e cartões. Todos têm a mesma forma de consulta, então
# compartilham uma rota só (ver funcoes/ufdr_dominios_utils.py).


@router.get("/api/investigacao/inqueritos/{id_inquerito}/ufdr/{dominio}")
def ufdr_dominio(
    id_inquerito: int,
    dominio: str,
    request: Request,
    extracao: int | None = None,
    busca: str | None = None,
    apagados: bool = False,
    pagina: int = 1,
    limite: int = 100,
    pacote: str | None = None,
    de: str | None = None,
    ate: str | None = None,
    valor_min: str | None = None,
    valor_max: str | None = None,
    faixa_dia: str | None = None,
    ordenar: str | None = None,
    usuario: dict = Depends(exigir_acesso_inquerito),
):
    """Lista, paginado, um domínio do UFDR do inquérito (?extracao=ID restringe a um aparelho).

    ?busca= procura nos campos de texto do domínio; ?apagados=true traz só o que veio marcado
    como apagado/lixeira no UFDR; ?pacote= restringe a um aplicativo, nos domínios de uso.
    ?de=/?ate= recortam o período (AAAA-MM-DD), ?faixa_dia= a faixa do dia (madrugada/manha/
    tarde/noite, hora de Brasília) e ?valor_min=/?valor_max= a faixa de valor, nos domínios que
    declaram coluna de data/valor. Cada faceta do domínio vira ?f_<coluna>=<valor> (ex.:
    ?f_no_moeda=BRL) e cada marcador vira ?m_<chave>=true (ex.: ?m_com_anexo=true) — nome fora
    da lista declarada pelo domínio é ignorado. ?ordenar= escolhe a ordenação do cabeçalho
    clicável; chave fora da lista declarada pelo domínio cai na ordem padrão."""
    facetas = {chave[2:]: valor for chave, valor in request.query_params.items()
               if chave.startswith("f_") and valor}
    marcadores = [chave[2:] for chave, valor in request.query_params.items()
                  if chave.startswith("m_") and valor not in ("", "false", "0")]
    try:
        return listar_dominio(dominio, id_inquerito, extracao, busca, apagados, pagina, limite,
                              pacote, de=de, ate=ate, facetas=facetas,
                              valor_min=valor_min, valor_max=valor_max,
                              faixa_dia=faixa_dia, marcadores=marcadores, ordenar=ordenar)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Domínio desconhecido: {dominio}")


@router.get("/api/investigacao/inqueritos/{id_inquerito}/ufdr/{dominio}/facetas")
def ufdr_dominio_facetas(
    id_inquerito: int,
    dominio: str,
    extracao: int | None = None,
    usuario: dict = Depends(exigir_acesso_inquerito),
):
    """Valores existentes nas colunas de filtro do domínio (com a contagem de cada um) e os
    extremos de data/valor — é o que popula os selects e limita os campos de período da tela."""
    try:
        return facetas_dominio(dominio, id_inquerito, extracao)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Domínio desconhecido: {dominio}")


@router.get("/api/investigacao/extracoes/{id_extracao}/ufdr-contagens")
def ufdr_contagens(id_extracao: int, usuario: dict = Depends(exigir_aprovado)):
    """Quantos registros a extração tem em cada domínio novo — as telas usam para decidir
    quais abas mostrar sem carregar as listas."""
    dados = contagens_dominios(id_extracao)
    if dados is None:
        raise HTTPException(status_code=404, detail="Extração não encontrada.")
    if not usuario_tem_acesso_inquerito(dados["id_inquerito"], usuario):
        raise HTTPException(status_code=403, detail="Sem permissão para este inquérito.")
    return {"contagens": dados["contagens"]}


def _stream_membro_zip(caminho_ufdr, membro: str, chunk: int = 262144):
    """Gera o conteúdo de um membro do .ufdr em pedaços, mantendo o zip aberto durante o envio —
    evita carregar arquivos grandes (vídeo de câmera etc.) inteiros na memória."""
    with ufdr_zip.abrir(caminho_ufdr) as zf:
        with zf.open(membro) as fp:
            while True:
                dados = fp.read(chunk)
                if not dados:
                    break
                yield dados


def _stream_membro_aninhado(caminho_ufdr, membro_externo: str, caminho_interno: str,
                            chunk: int = 262144):
    """Como _stream_membro_zip, mas para um membro de um ZIP aninhado dentro do .ufdr (nuvem
    Google): abre o archive STORED por janela de bytes (ufdr_cache) e lê o membro em pedaços."""
    with ufdr_cache.abrir_membro_aninhado(caminho_ufdr, membro_externo, caminho_interno) as fp:
        while True:
            dados = fp.read(chunk)
            if not dados:
                break
            yield dados


# ---------- Imagens derivadas (prévia do modal e miniatura da galeria) ----------
#
# A tela Arquivos pede a MESMA imagem em dois contextos muito diferentes: a miniatura da grade
# (célula de 160px, 120px no mobile — ver .arq-grade em css/arquivos.css) e a prévia em tamanho
# cheio do modal. Servir as duas a 1600px fazia a grade de 100 itens baixar ~100x mais pixel do
# que cabe na tela, e reencodar 100 JPEGs grandes no processo da API a cada carga.

_TAMANHO_PREVIA = 1600      # modal: imagem aberta ocupando a tela
_TAMANHO_MINIATURA = 320    # grade: 160px de célula, com folga para telas 2x
_QUALIDADE_MINIATURA = 70

# Entra no ETag e na chave do cache em memória: mudar tamanho/qualidade acima sem mexer aqui
# deixaria o navegador (e o ufdr_cache) servindo a versão antiga.
_VERSAO_IMAGEM_DERIVADA = "v1"

# 7 dias. O binário de um arquivo do aparelho não muda: o .ufdr é prova, aberto só para leitura,
# e um reimport gera ID_ARQUIVO novo (logo, URL e ETag novos).
_MAX_AGE_IMAGEM = 7 * 24 * 60 * 60


def _cabecalhos_imagem(etag: str) -> dict:
    # "private" porque é imagem de prova: pode ficar no navegador do investigador, nunca num
    # proxy compartilhado no caminho.
    return {"ETag": etag, "Cache-Control": f"private, max-age={_MAX_AGE_IMAGEM}"}


def _cliente_ja_tem(request: Request, etag: str) -> bool:
    """True se o If-None-Match da requisição já cobre este ETag (responder 304 basta)."""
    cabecalho = request.headers.get("if-none-match")
    if not cabecalho:
        return False
    for candidato in cabecalho.split(","):
        candidato = candidato.strip()
        if candidato == "*" or candidato.removeprefix("W/") == etag:
            return True
    return False


def _imagem_derivada(chave_origem: tuple, variante: str, ler_bruto) -> bytes:
    """Bytes JPEG da variante pedida, reaproveitando o cache em memória do ufdr_cache.

    Só a imagem JÁ DERIVADA entra em cache, não o binário cru: numa galeria de 100 fotos, guardar
    os originais encheria o cache (200 MB no total, ver ufdr_cache) com bytes que ninguém mais
    pede, enquanto as miniaturas ocupam alguns KB cada e são exatamente o que a tela repete.
    `ler_bruto` só é chamado quando dá miss."""
    tamanho = _TAMANHO_MINIATURA if variante == "thumb" else _TAMANHO_PREVIA
    qualidade = _QUALIDADE_MINIATURA if variante == "thumb" else 75
    chave = chave_origem + (f"{_VERSAO_IMAGEM_DERIVADA}:{variante}",)

    dados = ufdr_cache.obter_conteudo(chave)
    if dados is not None:
        return dados

    dados = redimensionar_imagem(ler_bruto(), tamanho_max=tamanho, qualidade=qualidade)
    ufdr_cache.guardar_conteudo(chave, dados)
    return dados


@router.get("/api/investigacao/arquivos/{id_arquivo}/conteudo")
def conteudo_arquivo(
    request: Request,
    id_arquivo: int,
    download: bool = False,
    preview: bool = False,
    thumb: bool = False,
    usuario: dict = Depends(exigir_aprovado),
):
    """Serve o binário de um arquivo do aparelho, lido sob demanda direto do .ufdr (não copiado nem
    persistido no banco).

    ?preview=true → só para imagens: reencoda em JPEG (converte HEIC do iPhone, que o navegador não
      exibe, e reduz fotos grandes) para abrir no modal. ?thumb=true (junto com preview) → miniatura
      da grade, bem menor. ?download=true → força o download do binário ORIGINAL (attachment).
      Padrão: inline em streaming (vídeo/áudio no modal, PDF no navegador).
    404 quando o arquivo é só metadado (sem binário extraído no .ufdr)."""
    info = obter_binario_arquivo(id_arquivo)
    if info is None:
        raise HTTPException(status_code=404, detail="Arquivo não encontrado.")
    if not usuario_tem_acesso_inquerito(info["id_inquerito"], usuario):
        raise HTTPException(status_code=403, detail="Sem permissão para este inquérito.")
    if not info["caminho_zip"]:
        raise HTTPException(status_code=404, detail="Este arquivo não tem binário no .ufdr (só metadado).")

    # prévia derivada de imagem; para VÍDEO, só a miniatura (o quadro que o IPED já extraiu) —
    # gerar um frame de vídeo aqui custaria a decodificação inteira e não é o papel da grade
    e_imagem_derivada = preview and (
        (info["tp_mime"] or "").startswith("image/")
        or (thumb and (info["tp_mime"] or "").startswith("video/"))
    )
    variante = ("thumb" if thumb else "previa") if e_imagem_derivada else None
    etag = f'"{_VERSAO_IMAGEM_DERIVADA}-{variante}-{id_arquivo}"' if variante else None

    # Revalidação antes de qualquer I/O: como o conteúdo é imutável, o ETag pode sair da identidade
    # do registro em vez do conteúdo — e um 304 custa só a consulta de metadado já feita acima, sem
    # abrir o ZIP nem redecodificar a imagem. É o que evita a galeria inteira ser rebaixada toda vez
    # que o investigador volta pra ela.
    if etag and _cliente_ja_tem(request, etag):
        return Response(status_code=304, headers=_cabecalhos_imagem(etag))

    # Cópia materializada pelo botão "Extrair temporariamente" (funcoes/midia_temp.py): quando
    # existe e está fresca, o binário sai do disco com Range de verdade (206 por faixa — o
    # FileResponse deste Starlette ignora Range, e é isso que fazia o .mov do iPhone baixar
    # inteiro antes de começar). O caminho é feito só de ids numéricos e o acesso já passou.
    if variante is None:
        copia = midia_temp.midia_temporaria(info["id_inquerito"], info["id_extracao"], id_arquivo)
        if copia is not None:
            try:
                resposta = midia_temp.resposta_arquivo(
                    copia, info["tp_mime"], request.headers.get("range"),
                    disposicao="attachment" if download else "inline",
                    nome=info["no_arquivo"] or "arquivo")
            except OSError:
                resposta = None      # a cópia expirou entre o lookup e o stat: serve do .ufdr
            if resposta is not None:
                resposta.headers["Cache-Control"] = f"private, max-age={_MAX_AGE_IMAGEM}"
                return resposta

    def _bytes_da_copia():
        """Bytes da cópia temporária, se houver — as prévias leem dali em vez de descomprimir
        o blob de novo (mesmo conteúdo, leitura de disco direto)."""
        copia = midia_temp.midia_temporaria(info["id_inquerito"], info["id_extracao"], id_arquivo)
        try:
            return copia.read_bytes() if copia is not None else None
        except OSError:
            return None

    if info["caminho_zip"].startswith("IPED::"):
        resolvido_iped = _resolver_arquivo_iped(info["caminho_zip"])
        if not resolvido_iped:
            raise HTTPException(status_code=404, detail="Pasta do caso do IPED não encontrada em data/.")
        pasta_modulo, origem, hash_md5, id_fonte = resolvido_iped

        if variante:
            # Miniatura que o IPED JÁ GEROU durante o processamento, guardada na tabela
            # `thumbs` do mesmo storage-N.db e chaveada pelo MD5 (ver iped_cache.ler_thumb).
            # Numa grade de 100 fotos isso troca 100 descompressões de JPEG cheio (mais o
            # HEIC do iPhone, que ainda passa pelo pillow-heif) por 100 leituras de alguns KB
            # já prontos. Só vale para a grade: a prévia do modal precisa da resolução cheia,
            # e a thumb do IPED tem ~160px. Vídeo: o quadro em view/H0/H1/HASH.jpg (também em
            # ler_thumb) — sem quadro pronto não há miniatura (a grade mostra o ícone).
            if variante == "thumb":
                pronta = _ler_thumb_iped(pasta_modulo, hash_md5)
                if pronta:
                    return Response(content=pronta, media_type="image/jpeg",
                                    headers=_cabecalhos_imagem(etag))
                if (info["tp_mime"] or "").startswith("video/"):
                    # sem quadro do IPED, sobra o que a extração temporária gerou por ffmpeg;
                    # sem nenhum dos dois a grade mostra o ícone (404 é o sinal disso)
                    quadro = midia_temp.thumb_temporaria(info["id_inquerito"], info["id_extracao"],
                                                         id_arquivo)
                    if quadro:
                        return Response(content=quadro, media_type="image/jpeg",
                                        headers=_cabecalhos_imagem(etag))
                    raise HTTPException(status_code=404, detail="Sem miniatura para este vídeo.")

            def _ler_iped():
                guardado = _bytes_da_copia()
                if guardado is not None:
                    return guardado
                try:
                    return _ler_blob_iped(pasta_modulo, origem, hash_md5, id_fonte)
                except FileNotFoundError:
                    raise HTTPException(status_code=404, detail="Binário não encontrado no caso do IPED.")
            try:
                dados = _imagem_derivada((str(pasta_modulo), hash_md5, id_fonte), variante, _ler_iped)
            except HTTPException:
                raise
            except Exception:
                raise HTTPException(status_code=422, detail="Não foi possível gerar a prévia desta imagem.")
            return Response(content=dados, media_type="image/jpeg", headers=_cabecalhos_imagem(etag))

        # binário completo: em streaming, descomprimido em pedaços — um vídeo de centenas de MB
        # não passa mais inteiro pela memória do processo (ver iped_cache.abrir_blob_stream)
        disposicao = "attachment" if download else "inline"
        return StreamingResponse(
            _stream_blob_iped(pasta_modulo, origem, hash_md5, id_fonte),
            media_type=info["tp_mime"] or "application/octet-stream",
            headers={"Content-Disposition": cabecalho_disposicao(disposicao, info["no_arquivo"])},
        )

    # Nuvem Google: o binário vive num ZIP aninhado dentro do .ufdr
    # (ponteiro "files/Archives/<zip>::<caminho_interno>") — tratado antes da resolução "flat"
    # porque o membro externo não é um arquivo solto em data/.
    aninhado = ufdr_cache.desmembrar_aninhado(info["caminho_zip"])
    if aninhado:
        membro_externo, caminho_interno = aninhado
        caminho_ufdr = fonte_ufdr(info["no_arquivo_ufdr"])
        if caminho_ufdr is None:
            raise HTTPException(status_code=404,
                                detail="Arquivo .ufdr original não encontrado (nem no servidor "
                                       "central, nem no servidor da delegacia).")

        # vídeo na grade: o pipeline de imagem não tem o que fazer com bytes de vídeo (e leria o
        # arquivo inteiro para falhar) — ou há quadro da extração temporária, ou ícone (404)
        if variante == "thumb" and (info["tp_mime"] or "").startswith("video/"):
            quadro = midia_temp.thumb_temporaria(info["id_inquerito"], info["id_extracao"],
                                                 id_arquivo)
            if quadro:
                return Response(content=quadro, media_type="image/jpeg",
                                headers=_cabecalhos_imagem(etag))
            raise HTTPException(status_code=404, detail="Sem miniatura para este vídeo.")

        if variante:
            def _ler_aninhado():
                guardado = _bytes_da_copia()
                if guardado is not None:
                    return guardado
                try:
                    return ufdr_cache.ler_membro_aninhado(caminho_ufdr, membro_externo, caminho_interno)
                except (FileNotFoundError, KeyError):
                    raise HTTPException(status_code=404, detail="Arquivo não encontrado no ZIP original.")
            try:
                dados = _imagem_derivada((str(caminho_ufdr), membro_externo, caminho_interno),
                                         variante, _ler_aninhado)
            except HTTPException:
                raise
            except Exception:
                raise HTTPException(status_code=422, detail="Não foi possível gerar a prévia desta imagem.")
            return Response(content=dados, media_type="image/jpeg", headers=_cabecalhos_imagem(etag))

        # confere que o membro existe antes do streaming (o cabeçalho 200 já teria ido). O membro
        # externo pode ser ZIP (Google) ou TAR (mídia do iCloud) — o TAR não abre com zipfile.
        if not ufdr_cache.membro_aninhado_existe(caminho_ufdr, membro_externo, caminho_interno):
            raise HTTPException(status_code=404, detail="Arquivo não encontrado no ZIP original.")

        disposicao = "attachment" if download else "inline"
        return StreamingResponse(
            _stream_membro_aninhado(caminho_ufdr, membro_externo, caminho_interno),
            media_type=info["tp_mime"] or "application/octet-stream",
            headers={"Content-Disposition": cabecalho_disposicao(disposicao, info["no_arquivo"])},
        )

    resolvido = _resolver_arquivo_zip(info["caminho_zip"], info["no_arquivo_ufdr"])
    if not resolvido:
        raise HTTPException(status_code=404, detail="Arquivo de origem (ZIP) não encontrado na pasta de dados.")
    caminho_zip_real, caminho_interno = resolvido

    # prévia de imagem: lê o binário inteiro e reencoda em JPEG (Pillow, com HEIC via pillow-heif).
    # É o único jeito de mostrar foto de iPhone no navegador; imagens são pequenas o bastante para
    # caber na memória (ao contrário de vídeo, que segue por streaming abaixo). O handle do zip vem
    # do ufdr_cache: numa grade de 100 miniaturas, reabrir o .ufdr a cada uma significaria reler o
    # diretório central (dezenas de milhares de entradas) 100 vezes.
    # vídeo na grade: idem ao ramo aninhado — nada de passar bytes de vídeo pelo Pillow
    if variante == "thumb" and (info["tp_mime"] or "").startswith("video/"):
        quadro = midia_temp.thumb_temporaria(info["id_inquerito"], info["id_extracao"], id_arquivo)
        if quadro:
            return Response(content=quadro, media_type="image/jpeg",
                            headers=_cabecalhos_imagem(etag))
        raise HTTPException(status_code=404, detail="Sem miniatura para este vídeo.")

    if variante:
        def _ler_do_zip():
            guardado = _bytes_da_copia()
            if guardado is not None:
                return guardado
            try:
                return ufdr_cache.obter_zip(caminho_zip_real).read(caminho_interno)
            except (FileNotFoundError, KeyError):
                raise HTTPException(status_code=404, detail="Arquivo não encontrado no ZIP original.")
        try:
            dados = _imagem_derivada((str(caminho_zip_real), caminho_interno), variante, _ler_do_zip)
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(status_code=422, detail="Não foi possível gerar a prévia desta imagem.")
        return Response(content=dados, media_type="image/jpeg", headers=_cabecalhos_imagem(etag))

    # confere que o membro existe ANTES de iniciar o streaming — dentro do gerador não dá mais para
    # devolver um status HTTP de erro (o cabeçalho 200 já teria ido)
    try:
        with ufdr_zip.abrir(caminho_zip_real) as zf:
            zf.getinfo(caminho_interno)
    except (FileNotFoundError, KeyError):
        raise HTTPException(status_code=404, detail="Arquivo não encontrado no ZIP original.")

    disposicao = "attachment" if download else "inline"
    return StreamingResponse(
        _stream_membro_zip(caminho_zip_real, caminho_interno),
        media_type=info["tp_mime"] or "application/octet-stream",
        headers={"Content-Disposition": cabecalho_disposicao(disposicao, info["no_arquivo"])},
    )


# ---------- Mídia direta do nó (docs/no_midia_direta.md) ----------
#
# O navegador pergunta antes de apontar a <img>/<video> para o anexo: "me dá a URL do nó, se
# eu consigo". Estas rotas só EMITEM a autorização (ticket de ~10 min assinado para aquele
# arquivo, funcoes/no_ticket.py); o transporte é o nó, direto na LAN de quem assiste. None é o
# "siga pelo central" — local, nó sem estrutura, usuário em outra cidade: o proxy de sempre.


class RotasDiretasIn(BaseModel):
    ids: list[int] = Field(..., min_length=1, max_length=400)
    download: bool = False


@router.get("/api/investigacao/arquivos/{id_arquivo}/rota-direta")
def rota_direta_arquivo(id_arquivo: int, download: bool = False,
                        usuario: dict = Depends(exigir_aprovado)):
    """URL direta do nó para este arquivo, ou null (= sirva pelo proxy do central, como sempre)."""
    info = obter_binario_arquivo(id_arquivo)
    if info is None:
        raise HTTPException(status_code=404, detail="Arquivo não encontrado.")
    if not usuario_tem_acesso_inquerito(info["id_inquerito"], usuario):
        raise HTTPException(status_code=403, detail="Sem permissão para este inquérito.")
    from funcoes import midia_direta_utils

    return {"url": midia_direta_utils.emitir(info, download)}


@router.post("/api/investigacao/arquivos/rotas-diretas")
def rotas_diretas_lote(payload: RotasDiretasIn, usuario: dict = Depends(exigir_aprovado)):
    """As mesmas URLs para uma leva (a grade pede a página inteira de uma vez — um round-trip,
    não duzentos). Sem acesso a QUALQUER inquérito da lista, a resposta é 400 inteira: a tela
    só pede o que já mostrou, chegar pedindo o resto é anomalia, não caso de uso."""
    from funcoes import midia_direta_utils

    infos = midia_direta_utils.info_arquivos(payload.ids)
    for id_inquerito in {i["id_inquerito"] for i in infos.values()}:
        if not usuario_tem_acesso_inquerito(id_inquerito, usuario):
            raise HTTPException(status_code=403, detail="Sem permissão para este inquérito.")
    return {"urls": {str(id_): midia_direta_utils.emitir(info, payload.download)
                     for id_, info in infos.items()}}


@router.post("/api/investigacao/extracoes/{id_extracao}/midia-temporaria")
def iniciar_midia_temporaria(id_extracao: int, usuario: dict = Depends(exigir_aprovado)):
    """Dispara em background a "extração temporária" da galeria: copia os vídeos e imagens da
    extração para data/_midia_temp (leitura de disco no modal, seek no player) e gera por ffmpeg
    a miniatura dos vídeos que o IPED não quadrou. A cópia expira sozinha em
    MIDIA_TEMP_TTL_HORAS (4 h por padrão) — o original no .ufdr/caso IPED nunca é tocado.

    Devolve `id_job` para acompanhamento em GET /api/investigacao/jobs/{id_job}; um segundo
    clique enquanto há job ativo neste inquérito leva 409 (guard da tabela de jobs)."""
    import threading

    id_inquerito = obter_id_inquerito_de_extracao(id_extracao)
    if id_inquerito is None:
        raise HTTPException(status_code=404, detail="Extração não encontrada.")
    if not usuario_tem_acesso_inquerito(id_inquerito, usuario, escrita=True):
        raise HTTPException(status_code=403, detail="Sem permissão para este inquérito.")

    try:
        id_job = jobs_utils.criar(jobs_utils.TP_MIDIA_TEMP, usuario["id_usuario"],
                                  id_inquerito=id_inquerito, id_extracao=id_extracao,
                                  pesado=False)
    except jobs_utils.JobOcupado as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    threading.Thread(target=midia_temp.extrair_midia_temporaria,
                     args=(id_job, id_inquerito, id_extracao),
                     daemon=True, name=f"midia-temp-{id_extracao}").start()
    return {"ok": True, "id_job": id_job}


@router.get("/api/investigacao/inqueritos/{id_inquerito}/permissoes")
def permissoes(id_inquerito: int, usuario: dict = Depends(exigir_gestor_inquerito)):
    """Lista quem tem permissão adicional de acesso a este inquérito (só o criador/admin vê)."""
    return listar_permissoes_inquerito(id_inquerito)


@router.post("/api/investigacao/inqueritos/{id_inquerito}/permissoes")
def conceder(id_inquerito: int, payload: PermissaoIn, usuario: dict = Depends(exigir_gestor_inquerito)):
    """Concede a outro usuário acesso a este inquérito. Se in_admin=true, concede também
    a gestão de permissões (o usuário vira administrador do inquérito)."""
    conceder_permissao(id_inquerito, payload.id_usuario, payload.in_admin)
    return {"ok": True}


@router.delete("/api/investigacao/inqueritos/{id_inquerito}/permissoes/{id_usuario}")
def revogar(id_inquerito: int, id_usuario: int, usuario: dict = Depends(exigir_gestor_inquerito)):
    """Revoga o acesso concedido a um usuário a este inquérito."""
    if not revogar_permissao(id_inquerito, id_usuario):
        raise HTTPException(status_code=404, detail="Este usuário não tem permissão concedida neste inquérito.")
    return {"ok": True}


def _exigir_acesso_via_conversa(id_conversa: int, fonte: str, usuario: dict) -> None:
    """Resolve o inquérito dono da conversa e confere acesso — para rotas que só recebem id_conversa."""
    id_inquerito = obter_id_inquerito_de_conversa(id_conversa, fonte)
    if id_inquerito is None:
        raise HTTPException(status_code=404, detail="Conversa não encontrada.")
    if not usuario_tem_acesso_inquerito(id_inquerito, usuario):
        raise HTTPException(status_code=403, detail="Sem permissão para este inquérito.")


def _exigir_acesso_via_contato(id_contato: int, fonte: str, usuario: dict) -> None:
    """Resolve o inquérito dono do contato e confere acesso — para rotas que só recebem id_contato."""
    id_inquerito = obter_id_inquerito_de_contato(id_contato, fonte)
    if id_inquerito is None:
        raise HTTPException(status_code=404, detail="Contato não encontrado.")
    if not usuario_tem_acesso_inquerito(id_inquerito, usuario, escrita=True):
        raise HTTPException(status_code=403, detail="Sem permissão para este inquérito.")


@router.post("/api/investigacao/extrair")
def extrair(payload: ExtracaoIn, usuario: dict = Depends(exigir_aprovado)):
    """Dispara em background a extração das conversas de WhatsApp de um .ufdr para um inquérito."""
    from funcoes.investigacao_utils import delegacia_permitida

    numero = _validar_numero_ou_caso(payload.numero_inquerito, payload.caso_inteligencia, usuario)

    arquivo = payload.arquivo.strip()
    # nada de path traversal — só o nome do arquivo, sem separadores de diretório
    if not arquivo or "/" in arquivo or "\\" in arquivo or not arquivo.lower().endswith(".ufdr"):
        raise HTTPException(status_code=400, detail="Arquivo inválido.")

    subpasta = delegacia_permitida(payload.delegacia, usuario)

    try:
        resultado = iniciar_extracao(arquivo, numero, usuario["id_usuario"], payload.modelo_transcricao,
                                     subpasta, payload.transcrever_video, delegacia=payload.delegacia,
                                     in_caso_inteligencia=payload.caso_inteligencia)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Arquivo não encontrado na pasta de dados.")

    return {"ok": True, **resultado}


def _pos_processar_documento(resultado: dict, usuario: dict) -> dict:
    """Roteamento pós-anexo de UM documento (via funcoes/zip_import.py, para a fila do ZIP usar
    o mesmo caminho sem duplicar)."""
    from funcoes.zip_import import pos_processar_documento

    return pos_processar_documento(resultado, usuario["id_usuario"],
                                   usuario.get("no_delegacia"))


@router.post("/api/investigacao/extrair-documento")
def extrair_documento(payload: ExtracaoIn, usuario: dict = Depends(exigir_aprovado)):
    """Anexa um documento (PDF/CSV/XLS/XLSX/DOCX/TXT/HTML de data/ — ex.: o PDF do próprio
    inquérito) a um inquérito, extraindo o texto na hora. O conteúdo fica disponível para a IA
    da investigação.

    Um .zip vai para a FILA de importação (job import_zip, ver funcoes/zip_import.py) em vez
    de processar na requisição: centenas de documentos estourariam o proxy_read_timeout de
    300 s do nginx. O POST valida, cria o inquérito e devolve o id_job para acompanhamento
    em GET /api/investigacao/jobs/{id_job}."""
    from funcoes.documentos_utils import eh_zip
    from funcoes.investigacao_utils import delegacia_permitida

    numero = _validar_numero_ou_caso(payload.numero_inquerito, payload.caso_inteligencia, usuario)

    subpasta = delegacia_permitida(payload.delegacia, usuario)

    if eh_zip(payload.arquivo):
        from funcoes.db_utils import get_connection
        from funcoes.investigacao_utils import _obter_ou_criar_inquerito
        from funcoes.zip_import import iniciar_importacao_zip

        nome_zip = (payload.arquivo or "").strip()
        if not nome_zip or "/" in nome_zip or "\\" in nome_zip:
            raise HTTPException(status_code=400, detail="Arquivo inválido.")
        from funcoes.documentos_utils import _no_com_arquivo, _subpasta_docs
        # falha rápido com 404 quando o ZIP não está nem no central nem no nó (sem isso o
        # erro só apareceria no job, depois do polling começar)
        _caminho_zip = _subpasta_docs(subpasta) / nome_zip
        if not _caminho_zip.is_file() and _no_com_arquivo(_caminho_zip, payload.delegacia) is None:
            raise HTTPException(status_code=404, detail="Arquivo não encontrado na pasta de dados.")
        # inquérito criado AGORA (barato e síncrono): escopa o guard do job e o link de
        # acompanhamento — o worker só anexa. Gera o CASO-AAAA.NNNN uma vez, aqui.
        with get_connection() as conn:
            with conn.cursor() as cur:
                id_inquerito = _obter_ou_criar_inquerito(cur, numero, usuario["id_usuario"],
                                                        payload.delegacia,
                                                        payload.caso_inteligencia)
                if payload.caso_inteligencia and not numero:
                    cur.execute(
                        "SELECT nu_inquerito FROM investigacao.inq_tb01_inqueritos "
                        "WHERE id_inquerito = %s",
                        (id_inquerito,),
                    )
                    numero = cur.fetchone()[0]
            conn.commit()
        try:
            id_job = iniciar_importacao_zip(
                arquivo=nome_zip, id_inquerito=id_inquerito, nu_inquerito=numero,
                subpasta=subpasta, delegacia=payload.delegacia,
                id_usuario=usuario["id_usuario"],
                no_delegacia_usuario=usuario.get("no_delegacia"),
                caso_inteligencia=payload.caso_inteligencia)
        except jobs_utils.JobOcupado as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return {"ok": True, "job_queued": True, "id_job": id_job,
                "id_inquerito": id_inquerito, "nu_inquerito": numero,
                "no_arquivo": nome_zip,
                "mensagem": "Importação do ZIP iniciada em background — acompanhe o progresso."}

    # Documento avulso PESADO (PDF de perícia, planilha gigante): a extração de texto sai da
    # requisição e entra na fila — mesmo motivo do ZIP, o proxy_read_timeout de 300 s. Só vale
    # para arquivo LOCAL: documento de nó extrai na estação (barato de lado lá, e o central não
    # sabe o tamanho sem consultar — segue o caminho síncrono de sempre).
    from funcoes.documentos_utils import _subpasta_docs
    from funcoes.zip_import import iniciar_importacao_documento, limite_doc_sincrono_bytes

    teto = limite_doc_sincrono_bytes()
    nome_doc = (payload.arquivo or "").strip()
    # o mesmo guard do anexar, antes do stat: sem isto um "../x" faria este código bisbilhotar
    # o tamanho de um arquivo fora de data/ (o anexar recusaria depois, mas o stat já tinha ido)
    if teto and nome_doc and "/" not in nome_doc and "\\" not in nome_doc:
        _caminho_doc = _subpasta_docs(subpasta) / nome_doc
        try:
            pesado = _caminho_doc.is_file() and _caminho_doc.stat().st_size > teto
        except OSError:
            pesado = False
        if pesado:
            from funcoes.db_utils import get_connection
            from funcoes.investigacao_utils import _obter_ou_criar_inquerito

            with get_connection() as conn:
                with conn.cursor() as cur:
                    id_inquerito = _obter_ou_criar_inquerito(cur, numero, usuario["id_usuario"],
                                                            payload.delegacia,
                                                            payload.caso_inteligencia)
                    if payload.caso_inteligencia and not numero:
                        cur.execute(
                            "SELECT nu_inquerito FROM investigacao.inq_tb01_inqueritos "
                            "WHERE id_inquerito = %s",
                            (id_inquerito,),
                        )
                        numero = cur.fetchone()[0]
                conn.commit()
            try:
                id_job = iniciar_importacao_documento(
                    arquivo=payload.arquivo.strip(), id_inquerito=id_inquerito,
                    nu_inquerito=numero, subpasta=subpasta, delegacia=payload.delegacia,
                    id_usuario=usuario["id_usuario"],
                    no_delegacia_usuario=usuario.get("no_delegacia"),
                    caso_inteligencia=payload.caso_inteligencia)
            except jobs_utils.JobOcupado as exc:
                raise HTTPException(status_code=409, detail=str(exc))
            return {"ok": True, "job_queued": True, "id_job": id_job,
                    "id_inquerito": id_inquerito, "nu_inquerito": numero,
                    "no_arquivo": payload.arquivo.strip(),
                    "mensagem": "Documento grande: importação em background — acompanhe o progresso."}

    try:
        resultado = anexar_documento_inquerito(payload.arquivo, numero, usuario["id_usuario"], subpasta,
                                               delegacia=payload.delegacia,
                                               in_caso_inteligencia=payload.caso_inteligencia)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Arquivo não encontrado na pasta de dados.")
    except (psycopg.DataError, UnicodeError):
        # rede de segurança: se ainda escapar um NUL/caractere inválido do texto extraído, isso é
        # problema do arquivo, não do servidor — 400 com recado claro em vez de um 500 opaco
        # (a limpeza fica em documentos_utils._limpar_texto_para_banco).
        raise HTTPException(
            status_code=400,
            detail="O texto extraído deste documento tem caracteres que o banco não aceita. "
                   "O arquivo provavelmente é um PDF digitalizado ou com fonte corrompida — "
                   "gere um PDF novo (ex.: 'Imprimir para PDF') e importe de novo.",
        )

    pos = _pos_processar_documento(resultado, usuario)
    return {"ok": True, **resultado, **pos}


@router.get("/api/investigacao/inqueritos/{id_inquerito}/documentos")
def documentos_inquerito(id_inquerito: int, usuario: dict = Depends(exigir_acesso_inquerito)):
    """Lista os documentos anexados ao inquérito (nome, tipo, tamanho do texto, erro se houver)."""
    return listar_documentos_inquerito(id_inquerito)


@router.get("/api/investigacao/inqueritos/{id_inquerito}/documentos/{id_documento}")
def documento_inquerito(id_inquerito: int, id_documento: int, usuario: dict = Depends(exigir_acesso_inquerito)):
    """Texto extraído de um documento anexado ao inquérito, para o investigador conferir."""
    return ler_documento_inquerito_completo(id_inquerito, id_documento)


@router.get("/api/investigacao/inqueritos/{id_inquerito}/documentos/{id_documento}/visualizacao")
def visualizacao_documento_inquerito(id_inquerito: int, id_documento: int,
                                     usuario: dict = Depends(exigir_acesso_inquerito)):
    """O documento fatiado em páginas para a tela documento.html — a que a citação [doc:ID] abre."""
    documento = obter_documento_visualizacao("inquerito", id_inquerito, id_documento)
    if documento is None:
        raise HTTPException(status_code=404, detail="Documento não encontrado neste inquérito.")
    return documento


@router.get("/api/investigacao/inqueritos/{id_inquerito}/documentos/{id_documento}/original")
def original_documento_inquerito(id_inquerito: int, id_documento: int,
                                 usuario: dict = Depends(exigir_acesso_inquerito)):
    """O arquivo original (o PDF como ele é), para a aba "Original" do visualizador."""
    return resposta_documento_original("inquerito", id_inquerito, id_documento)


@router.get("/api/investigacao/inqueritos/{id_inquerito}/documentos/{id_documento}/render")
def render_documento_inquerito(id_inquerito: int, id_documento: int,
                               usuario: dict = Depends(exigir_acesso_inquerito)):
    """Os blocos estruturados do .docx (títulos, listas, tabelas) para o modo "Formatado" da
    documento.html. O texto em si continua o mesmo extraído — o render só devolve a forma."""
    try:
        return renderizar_documento("inquerito", id_inquerito, id_documento)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/api/investigacao/inqueritos/{id_inquerito}/documentos/{id_documento}/reextrair")
def reextrair_documento_inquerito(id_inquerito: int, id_documento: int,
                                  usuario: dict = Depends(exigir_escrita_inquerito)):
    """Relê o arquivo original e regrava o texto — é assim que um documento anexado antes dos
    marcadores de página ganha paginação (e volta para a fila da indexação semântica)."""
    try:
        return {"ok": True, **reextrair_texto_documento("inquerito", id_inquerito, id_documento)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/api/investigacao/inqueritos/{id_inquerito}/documentos/{id_documento}/analisar")
def analisar_documento_inquerito(id_inquerito: int, id_documento: int, usuario: dict = Depends(exigir_escrita_inquerito)):
    """Dispara a análise de entidades do documento (resumo + pessoas + achados) em background;
    o card mostra o resultado quando concluir. Com a IA bloqueada para a delegacia a extração
    automática (determinística) ainda roda sozinha — a IA é o complemento, não o requisito."""
    from funcoes.documentos_utils import iniciar_analise_documento
    from funcoes.ia_delegacia import bloqueio_ia, erro_ia_desativada
    bloqueada = bloqueio_ia(usuario, id_inquerito=id_inquerito)
    try:
        iniciar_analise_documento(id_inquerito, id_documento, usuario["id_usuario"], usar_ia=not bloqueada)
    except ValueError as exc:
        if bloqueada:
            raise erro_ia_desativada(bloqueada)
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


@router.delete("/api/investigacao/documentos/{id_documento}")
def apagar_documento(id_documento: int, usuario: dict = Depends(exigir_aprovado)):
    """Remove um documento anexado (qualquer usuário com acesso ao inquérito)."""
    id_inquerito = obter_id_inquerito_de_documento(id_documento)
    if id_inquerito is None:
        raise HTTPException(status_code=404, detail="Documento não encontrado.")
    if not usuario_tem_acesso_inquerito(id_inquerito, usuario, escrita=True):
        raise HTTPException(status_code=403, detail="Sem permissão para este inquérito.")
    apagar_documento_inquerito(id_documento)
    return {"ok": True}


# ---------- Inquérito em PDF (peças, pessoas e achados extraídos do IPL) ----------

@router.get("/api/investigacao/inqueritos/{id_inquerito}/pdfs")
def ipls_inquerito(id_inquerito: int, usuario: dict = Depends(exigir_acesso_inquerito)):
    """Lista os PDFs de inquérito extraídos (com contagens), para o bloco no card."""
    return listar_ipls_inquerito(id_inquerito)


def _exigir_acesso_via_ipl(id_ipl: int, usuario: dict, escrita: bool = False) -> None:
    """Resolve o inquérito dono do IPL e confere acesso — para rotas que só recebem id_ipl."""
    id_inquerito = obter_id_inquerito_de_ipl(id_ipl)
    if id_inquerito is None:
        raise HTTPException(status_code=404, detail="Inquérito em PDF não encontrado.")
    if not usuario_tem_acesso_inquerito(id_inquerito, usuario, escrita=escrita):
        raise HTTPException(status_code=403, detail="Sem permissão para este inquérito.")


@router.get("/api/investigacao/inqueritos-pdf/{id_ipl}")
def ipl_cabecalho(id_ipl: int, usuario: dict = Depends(exigir_aprovado)):
    """Cabeçalho do procedimento (nº do IPL, autoridade, tipificação, resumo dos fatos)."""
    _exigir_acesso_via_ipl(id_ipl, usuario)
    return obter_ipl(id_ipl)


@router.get("/api/investigacao/inqueritos-pdf/{id_ipl}/pecas")
def ipl_pecas(id_ipl: int, usuario: dict = Depends(exigir_aprovado)):
    """Índice das peças do inquérito (que documento está em qual folha)."""
    _exigir_acesso_via_ipl(id_ipl, usuario)
    return listar_pecas_ipl(id_ipl)


@router.get("/api/investigacao/inqueritos-pdf/{id_ipl}/pessoas")
def ipl_pessoas(id_ipl: int, usuario: dict = Depends(exigir_aprovado)):
    """Pessoas citadas no inquérito (CPF/CNPJ, vulgo, papel, folha)."""
    _exigir_acesso_via_ipl(id_ipl, usuario)
    return listar_pessoas_ipl(id_ipl)


@router.get("/api/investigacao/inqueritos-pdf/{id_ipl}/achados")
def ipl_achados(id_ipl: int, usuario: dict = Depends(exigir_aprovado)):
    """Dados de interesse (telefones, e-mails, valores, contas, processos, tipificações...)."""
    _exigir_acesso_via_ipl(id_ipl, usuario)
    return listar_achados_ipl(id_ipl)


@router.delete("/api/investigacao/inqueritos-pdf/{id_ipl}")
def apagar_ipl_rota(id_ipl: int, usuario: dict = Depends(exigir_aprovado)):
    """Remove o inquérito extraído, suas peças/pessoas/achados e o documento de texto do PDF."""
    _exigir_acesso_via_ipl(id_ipl, usuario, escrita=True)
    apagar_ipl(id_ipl)
    return {"ok": True}


# ---------- Declaração de IRPF (Receita Federal) em PDF ----------

@router.get("/api/investigacao/inqueritos/{id_inquerito}/declaracoes-irpf")
def irpfs_inquerito(id_inquerito: int, usuario: dict = Depends(exigir_acesso_inquerito)):
    """Lista as declarações de IRPF extraídas (com contagens), para o bloco no card."""
    return listar_irpfs_inquerito(id_inquerito)


def _exigir_acesso_via_irpf(id_irpf: int, usuario: dict, escrita: bool = False) -> None:
    """Resolve o inquérito dono da declaração e confere acesso — para rotas que só recebem id_irpf."""
    id_inquerito = obter_id_inquerito_de_irpf(id_irpf)
    if id_inquerito is None:
        raise HTTPException(status_code=404, detail="Declaração de IRPF não encontrada.")
    if not usuario_tem_acesso_inquerito(id_inquerito, usuario, escrita=escrita):
        raise HTTPException(status_code=403, detail="Sem permissão para este inquérito.")


@router.get("/api/investigacao/declaracoes-irpf/{id_irpf}")
def irpf_cabecalho(id_irpf: int, usuario: dict = Depends(exigir_aprovado)):
    """Cabeçalho da declaração (identificação do contribuinte, resumo, evolução patrimonial)."""
    _exigir_acesso_via_irpf(id_irpf, usuario)
    return obter_irpf(id_irpf)


@router.get("/api/investigacao/declaracoes-irpf/{id_irpf}/bens")
def irpf_bens(id_irpf: int, usuario: dict = Depends(exigir_aprovado)):
    """Declaração de bens e direitos (código, discriminação, valores em 31/12)."""
    _exigir_acesso_via_irpf(id_irpf, usuario)
    return listar_bens_irpf(id_irpf)


@router.get("/api/investigacao/declaracoes-irpf/{id_irpf}/dependentes")
def irpf_dependentes(id_irpf: int, usuario: dict = Depends(exigir_aprovado)):
    """Dependentes declarados (nome, CPF, relação, nascimento)."""
    _exigir_acesso_via_irpf(id_irpf, usuario)
    return listar_dependentes_irpf(id_irpf)


@router.get("/api/investigacao/declaracoes-irpf/{id_irpf}/rendimentos")
def irpf_rendimentos(id_irpf: int, usuario: dict = Depends(exigir_aprovado)):
    """Rendimentos recebidos de pessoa jurídica (fonte pagadora, CNPJ/CPF, valor)."""
    _exigir_acesso_via_irpf(id_irpf, usuario)
    return listar_rendimentos_irpf(id_irpf)


@router.get("/api/investigacao/declaracoes-irpf/{id_irpf}/dividas")
def irpf_dividas(id_irpf: int, usuario: dict = Depends(exigir_aprovado)):
    """Dívidas e ônus reais (código, discriminação, valores em 31/12)."""
    _exigir_acesso_via_irpf(id_irpf, usuario)
    return listar_dividas_irpf(id_irpf)


@router.get("/api/investigacao/declaracoes-irpf/{id_irpf}/doacoes")
def irpf_doacoes(id_irpf: int, usuario: dict = Depends(exigir_aprovado)):
    """Doações efetuadas e a partidos políticos/candidatos (beneficiário, CNPJ/CPF, valor)."""
    _exigir_acesso_via_irpf(id_irpf, usuario)
    return listar_doacoes_irpf(id_irpf)


@router.delete("/api/investigacao/declaracoes-irpf/{id_irpf}")
def apagar_irpf_rota(id_irpf: int, usuario: dict = Depends(exigir_aprovado)):
    """Remove a declaração extraída, seus bens/dependentes/rendimentos/dívidas/doações e o
    documento de texto do PDF."""
    _exigir_acesso_via_irpf(id_irpf, usuario, escrita=True)
    apagar_irpf(id_irpf)
    return {"ok": True}


# ---------- IPEI (quebra de sigilo fiscal — Receita Federal) ----------

@router.get("/api/investigacao/inqueritos/{id_inquerito}/ipeis")
def ipeis_inquerito(id_inquerito: int, usuario: dict = Depends(exigir_acesso_inquerito)):
    """Lista as IPEIs extraídas (com contagens), para o bloco no card."""
    return listar_ipeis_inquerito(id_inquerito)


def _exigir_acesso_via_ipei(id_ipei: int, usuario: dict, escrita: bool = False) -> None:
    """Resolve o inquérito dono da IPEI e confere acesso — para rotas que só recebem id_ipei."""
    id_inquerito = obter_id_inquerito_de_ipei(id_ipei)
    if id_inquerito is None:
        raise HTTPException(status_code=404, detail="IPEI não encontrada.")
    if not usuario_tem_acesso_inquerito(id_inquerito, usuario, escrita=escrita):
        raise HTTPException(status_code=403, detail="Sem permissão para este inquérito.")


@router.get("/api/investigacao/ipeis/{id_ipei}")
def ipei_cabecalho(id_ipei: int, usuario: dict = Depends(exigir_aprovado)):
    """Cabeçalho da IPEI (número, data, unidade, processo judicial, conclusão)."""
    _exigir_acesso_via_ipei(id_ipei, usuario)
    return obter_ipei(id_ipei)


@router.get("/api/investigacao/ipeis/{id_ipei}/alvos")
def ipei_alvos(id_ipei: int, usuario: dict = Depends(exigir_aprovado)):
    """Contribuintes analisados e seus dados cadastrais (um por PF, vários por PDF de PJ)."""
    _exigir_acesso_via_ipei(id_ipei, usuario)
    return listar_alvos_ipei(id_ipei)


@router.get("/api/investigacao/ipeis/{id_ipei}/relacionados")
def ipei_relacionados(id_ipei: int, usuario: dict = Depends(exigir_aprovado)):
    """Vínculos do alvo: sócios, empregadores, procurações e MAC coincidente."""
    _exigir_acesso_via_ipei(id_ipei, usuario)
    return listar_relacionados_ipei(id_ipei)


@router.get("/api/investigacao/ipeis/{id_ipei}/anos")
def ipei_anos(id_ipei: int, usuario: dict = Depends(exigir_aprovado)):
    """Consolidado ano a ano: rendimentos, movimentação financeira, NFe e variação patrimonial."""
    _exigir_acesso_via_ipei(id_ipei, usuario)
    return listar_anos_ipei(id_ipei)


@router.get("/api/investigacao/ipeis/{id_ipei}/fontes")
def ipei_fontes(id_ipei: int, usuario: dict = Depends(exigir_aprovado)):
    """Fontes pagadoras e pagamentos declarados (nome, CNPJ/CPF, natureza, valor)."""
    _exigir_acesso_via_ipei(id_ipei, usuario)
    return listar_fontes_ipei(id_ipei)


@router.get("/api/investigacao/ipeis/{id_ipei}/contas")
def ipei_contas(id_ipei: int, usuario: dict = Depends(exigir_aprovado)):
    """Contas da e-Financeira com crédito, débito e saldo mês a mês."""
    _exigir_acesso_via_ipei(id_ipei, usuario)
    return listar_contas_ipei(id_ipei)


@router.get("/api/investigacao/ipeis/{id_ipei}/bens")
def ipei_bens(id_ipei: int, usuario: dict = Depends(exigir_aprovado)):
    """Bens declarados, veículos do RENAVAM, imóveis, embarcações e aeronaves."""
    _exigir_acesso_via_ipei(id_ipei, usuario)
    return listar_bens_ipei(id_ipei)


@router.get("/api/investigacao/ipeis/{id_ipei}/notas")
def ipei_notas(id_ipei: int, usuario: dict = Depends(exigir_aprovado)):
    """Notas fiscais eletrônicas — as do corpo do PDF e as da planilha de anexo."""
    _exigir_acesso_via_ipei(id_ipei, usuario)
    return listar_notas_ipei(id_ipei)


@router.get("/api/investigacao/ipeis/{id_ipei}/contatos")
def ipei_contatos(id_ipei: int, usuario: dict = Depends(exigir_aprovado)):
    """Endereços, telefones, e-mails e MAC/IP levantados em outras bases da RFB."""
    _exigir_acesso_via_ipei(id_ipei, usuario)
    return listar_contatos_ipei(id_ipei)


@router.delete("/api/investigacao/ipeis/{id_ipei}")
def apagar_ipei_rota(id_ipei: int, usuario: dict = Depends(exigir_aprovado)):
    """Remove a IPEI extraída, suas linhas filhas e os documentos de texto do PDF e do anexo."""
    _exigir_acesso_via_ipei(id_ipei, usuario, escrita=True)
    apagar_ipei(id_ipei)
    return {"ok": True}


# ---------- Telefonia (retorno da operadora na quebra de sigilo telefônico) ----------
# Um card por operadora dentro do inquérito, e não um por arquivo: a operadora responde em vários
# arquivos (cadastro, planilha do ofício, o PDF dela, as ERBs em levas) e é tudo o mesmo material.
# Quem costura é funcoes/telefonia_utils.py — ver docs/telefonia.md.

@router.get("/api/investigacao/inqueritos/{id_inquerito}/telefonia")
def telefonia_inquerito(id_inquerito: int, usuario: dict = Depends(exigir_acesso_inquerito)):
    """Os cards de telefonia do inquérito (um por operadora), com as contagens, para o bloco."""
    return listar_telefonia_inquerito(id_inquerito)


@router.get("/api/investigacao/inqueritos/{id_inquerito}/telefonia/erbs")
def telefonia_erbs_inquerito(
    id_inquerito: int,
    data_de: str | None = None,
    data_ate: str | None = None,
    periodo: str | None = None,
    usuario: dict = Depends(exigir_acesso_inquerito),
):
    """As antenas com coordenada de TODAS as operadoras do inquérito — a camada de ERBs do mapa
    de localizações, que é do inquérito e não de um aparelho.

    ?data_de=AAAA-MM-DD, ?data_ate=AAAA-MM-DD e ?periodo=madrugada|manha|tarde|noite recortam pelas
    CHAMADAS que passaram por cada antena (a torre não tem data; a chamada tem), e nesse caso só
    voltam as antenas usadas no período. Sem eles, volta o cadastro inteiro, como antes."""
    return listar_erbs_inquerito(id_inquerito, data_de, data_ate, periodo)


@router.get("/api/investigacao/inqueritos/{id_inquerito}/telefonia/encontros")
def telefonia_encontros_inquerito(
    id_inquerito: int,
    janela_min: int = 30,
    min_encontros: int = 2,
    data_de: str | None = None,
    data_ate: str | None = None,
    limite: int = 200,
    usuario: dict = Depends(exigir_acesso_inquerito),
):
    """Pares de linhas na mesma ERB dentro da janela — quem estava com quem, onde e quando.

    Só entram as linhas do cadastro (os alvos); sem cadastro, as de maior volume no CDR.
    Antena é região, não posição: o encontro é co-localização, não prova de contato."""
    return listar_encontros_inquerito(id_inquerito, janela_min, min_encontros, data_de, data_ate, limite)


@router.get("/api/investigacao/inqueritos/{id_inquerito}/telefonia/ancoras")
def telefonia_ancoras_inquerito(
    id_inquerito: int,
    usuario: dict = Depends(exigir_acesso_inquerito),
):
    """Residência/trabalho provável por linha, pela rotina na ERB (madrugada vs. horário comercial)."""
    return listar_ancoras_inquerito(id_inquerito)


def _exigir_acesso_via_telefonia(id_telefonia: int, usuario: dict, escrita: bool = False) -> None:
    """Resolve o inquérito dono do card — para as rotas que só recebem id_telefonia."""
    id_inquerito = obter_id_inquerito_de_telefonia(id_telefonia)
    if id_inquerito is None:
        raise HTTPException(status_code=404, detail="Telefonia não encontrada.")
    if not usuario_tem_acesso_inquerito(id_inquerito, usuario, escrita=escrita):
        raise HTTPException(status_code=403, detail="Sem permissão para este inquérito.")


@router.get("/api/investigacao/telefonia/{id_telefonia}")
def telefonia_cabecalho(id_telefonia: int, usuario: dict = Depends(exigir_aprovado)):
    """Cabeçalho do card: operadora, contagens e quem importou — para o topo da tela."""
    _exigir_acesso_via_telefonia(id_telefonia, usuario)
    return obter_telefonia(id_telefonia)


@router.get("/api/investigacao/telefonia/{id_telefonia}/arquivos")
def telefonia_arquivos(id_telefonia: int, usuario: dict = Depends(exigir_aprovado)):
    """A procedência: cada arquivo que entrou no card, o que trouxe e que período cobre."""
    _exigir_acesso_via_telefonia(id_telefonia, usuario)
    return listar_arquivos_telefonia(id_telefonia)


@router.get("/api/investigacao/telefonia/{id_telefonia}/filtros")
def telefonia_filtros(id_telefonia: int, usuario: dict = Depends(exigir_aprovado)):
    """Os valores reais dos selects da tela (UFs e situações deste card), sem carregar as listas."""
    _exigir_acesso_via_telefonia(id_telefonia, usuario)
    return listar_filtros_telefonia(id_telefonia)


@router.get("/api/investigacao/telefonia/{id_telefonia}/linhas")
def telefonia_linhas(id_telefonia: int, usuario: dict = Depends(exigir_aprovado),
                     pagina: int | None = None, limite: int = 100, deslocamento: int = 0,
                     busca: str | None = None, situacao: str | None = None):
    """Fichas cadastrais das linhas, paginadas no servidor: {total, pagina, limite, itens}."""
    _exigir_acesso_via_telefonia(id_telefonia, usuario)
    return listar_linhas_telefonia(id_telefonia, limite, deslocamento,
                                   pagina=pagina, busca=busca, situacao=situacao)


@router.get("/api/investigacao/telefonia/{id_telefonia}/chamadas")
def telefonia_chamadas(id_telefonia: int, usuario: dict = Depends(exigir_aprovado),
                       pagina: int | None = None, limite: int = 100, deslocamento: int = 0,
                       busca: str | None = None, data_de: str | None = None,
                       data_ate: str | None = None, tipo: str | None = None,
                       so_completadas: bool = False, so_com_antena: bool = False):
    """O CDR em ordem cronológica, paginado no servidor, já com a antena resolvida pelo CGI."""
    _exigir_acesso_via_telefonia(id_telefonia, usuario)
    return listar_chamadas_telefonia(id_telefonia, limite, deslocamento, pagina=pagina,
                                     busca=busca, data_de=data_de, data_ate=data_ate,
                                     tipo=tipo, so_completadas=so_completadas,
                                     so_com_antena=so_com_antena)


@router.get("/api/investigacao/telefonia/{id_telefonia}/erbs")
def telefonia_erbs(id_telefonia: int, usuario: dict = Depends(exigir_aprovado),
                   pagina: int | None = None, limite: int = 100, deslocamento: int = 0,
                   busca: str | None = None, uf: str | None = None,
                   so_com_coord: bool = False):
    """As antenas do card, paginadas, com endereço, coordenada e quantas chamadas passaram."""
    _exigir_acesso_via_telefonia(id_telefonia, usuario)
    return listar_erbs_telefonia(id_telefonia, limite, deslocamento, pagina=pagina,
                                 busca=busca, uf=uf, so_com_coord=so_com_coord)


@router.get("/api/investigacao/telefonia/{id_telefonia}/numeros")
def telefonia_numeros(id_telefonia: int, usuario: dict = Depends(exigir_aprovado),
                      pagina: int | None = None, limite: int = 100, deslocamento: int = 0,
                      busca: str | None = None, so_com_imei: bool = False,
                      so_compartilhado: bool = False, ordenar: str | None = None):
    """Números e IMEIs do resumo, paginados. `ordenar=chamadas` traz o ranking de interlocutores."""
    _exigir_acesso_via_telefonia(id_telefonia, usuario)
    return listar_numeros_telefonia(id_telefonia, limite, deslocamento, pagina=pagina,
                                    busca=busca, so_com_imei=so_com_imei,
                                    so_compartilhado=so_compartilhado, ordenar=ordenar)


@router.get("/api/investigacao/telefonia/{id_telefonia}/sessoes")
def telefonia_sessoes(id_telefonia: int, usuario: dict = Depends(exigir_aprovado),
                      pagina: int | None = None, limite: int = 100, deslocamento: int = 0,
                      busca: str | None = None):
    """Sessões de chamada por WiFi (a linha falando por IP, fora da rede móvel), paginadas."""
    _exigir_acesso_via_telefonia(id_telefonia, usuario)
    return listar_sessoes_telefonia(id_telefonia, limite, deslocamento,
                                    pagina=pagina, busca=busca)


@router.get("/api/investigacao/telefonia/{id_telefonia}/dados")
def telefonia_dados_moveis(id_telefonia: int, usuario: dict = Depends(exigir_aprovado),
                           pagina: int | None = None, limite: int = 100,
                           deslocamento: int = 0, busca: str | None = None,
                           data_de: str | None = None, data_ate: str | None = None):
    """Sessões de dados móveis (CGI + IP + volumes por intervalo), paginadas no servidor."""
    _exigir_acesso_via_telefonia(id_telefonia, usuario)
    return listar_dados_moveis_telefonia(id_telefonia, limite, deslocamento,
                                         pagina=pagina, busca=busca,
                                         data_de=data_de, data_ate=data_ate)


@router.delete("/api/investigacao/telefonia/{id_telefonia}")
def apagar_telefonia_rota(id_telefonia: int, usuario: dict = Depends(exigir_aprovado)):
    """Remove o card daquela operadora, tudo o que ele levantou e os documentos de texto dos
    arquivos que entraram nele."""
    _exigir_acesso_via_telefonia(id_telefonia, usuario, escrita=True)
    apagar_telefonia(id_telefonia)
    return {"ok": True}


# ---------- RIF (Relatório de Inteligência Financeira) ----------

@router.get("/api/investigacao/rifs-disponiveis")
def rifs_disponiveis(usuario: dict = Depends(exigir_aprovado), delegacia: str | None = None):
    """Lista as subpastas de data/ com 'RIF' no nome, para o seletor. Se a delegacia tem nó
    ativo, a lista vem da pasta DO NÓ; senão, da pasta local do central."""
    from funcoes import no_utils
    from funcoes.investigacao_utils import delegacia_permitida
    subpasta = delegacia_permitida(delegacia, usuario)
    remotos = no_utils.listagem_remota(subpasta, subpasta, "rif")
    if remotos is not None:
        return remotos
    return listar_rifs_disponiveis(subpasta)


@router.post("/api/investigacao/rif-upload")
def upload_rif(
    arquivos: list[UploadFile] = File(...),
    delegacia: str | None = Form(default=None),
    usuario: dict = Depends(exigir_aprovado),
):
    """Recebe a PASTA de um RIF pelo navegador (webkitdirectory) e grava em data/[<delegacia>/] —
    a mesma raiz que GET /api/investigacao/rifs-disponiveis lista. Não importa nada por conta
    própria: quem importa continua sendo POST /api/investigacao/extrair-rif, com o nome de pasta
    devolvido aqui.

    Uma requisição por pasta: os arquivos vêm com o caminho relativo do webkitRelativePath, e o
    primeiro componente dele é o nome da pasta. Só CSV e PDF entram; o que for recusado volta em
    "falhas" e a pasta é removida se nada puder ser salvo."""
    from funcoes.investigacao_utils import delegacia_permitida
    from funcoes.rif_utils import salvar_rif_enviado

    subpasta = delegacia_permitida(delegacia, usuario)
    itens = [(arquivo.file, arquivo.filename or "") for arquivo in arquivos]
    try:
        resultado = salvar_rif_enviado(itens, subpasta)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        for arquivo in arquivos:
            arquivo.file.close()
    return resultado


@router.post("/api/investigacao/extrair-rif")
def extrair_rif(payload: RifIn, usuario: dict = Depends(exigir_aprovado)):
    """Importa um RIF (pasta em data/) para um inquérito: parseia os CSVs em tabelas próprias e anexa
    o PDF como documento. Cria o inquérito se o número ainda não existir."""
    from funcoes.investigacao_utils import delegacia_permitida

    numero = _validar_numero_ou_caso(payload.numero_inquerito, payload.caso_inteligencia, usuario)

    subpasta = delegacia_permitida(payload.delegacia, usuario)

    try:
        resultado = importar_rif(payload.pasta, numero, usuario["id_usuario"], subpasta,
                                 delegacia=payload.delegacia,
                                 in_caso_inteligencia=payload.caso_inteligencia)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Pasta do RIF não encontrada em data/.")

    return {"ok": True, **resultado}


@router.get("/api/investigacao/inqueritos/{id_inquerito}/rifs")
def rifs_inquerito(id_inquerito: int, usuario: dict = Depends(exigir_acesso_inquerito)):
    """Lista os RIFs importados de um inquérito (com contagens), para o bloco no card."""
    return listar_rifs_inquerito(id_inquerito)


def _exigir_acesso_via_rif(id_rif: int, usuario: dict, escrita: bool = False) -> None:
    """Resolve o inquérito dono do RIF e confere acesso — para rotas que só recebem id_rif."""
    id_inquerito = obter_id_inquerito_de_rif(id_rif)
    if id_inquerito is None:
        raise HTTPException(status_code=404, detail="RIF não encontrado.")
    if not usuario_tem_acesso_inquerito(id_inquerito, usuario, escrita=escrita):
        raise HTTPException(status_code=403, detail="Sem permissão para este inquérito.")


@router.get("/api/investigacao/rifs/{id_rif}/comunicacoes")
def rif_comunicacoes(id_rif: int, usuario: dict = Depends(exigir_aprovado)):
    """Movimentações bancárias (comunicações) de um RIF."""
    _exigir_acesso_via_rif(id_rif, usuario)
    return listar_comunicacoes_rif(id_rif)


@router.get("/api/investigacao/rifs/{id_rif}/envolvidos")
def rif_envolvidos(id_rif: int, usuario: dict = Depends(exigir_aprovado)):
    """Pessoas/empresas citadas em um RIF."""
    _exigir_acesso_via_rif(id_rif, usuario)
    return listar_envolvidos_rif(id_rif)


@router.get("/api/investigacao/rifs/{id_rif}/contrapartes")
def rif_contrapartes(id_rif: int, usuario: dict = Depends(exigir_aprovado)):
    """Contrapartes estruturadas (remetentes/favorecidos, com valor e nº de lançamentos) por comunicação."""
    _exigir_acesso_via_rif(id_rif, usuario)
    return listar_contrapartes_rif(id_rif)


@router.get("/api/investigacao/rifs/{id_rif}/contrapartes-consolidadas")
def rif_contrapartes_consolidadas(id_rif: int, usuario: dict = Depends(exigir_aprovado)):
    """Contrapartes do RIF somadas por pessoa (mesma chave da timeline financeira), com o
    DESCONHECIDO, a cobertura do movimentado e o cruzamento com o SIMBA do inquérito."""
    _exigir_acesso_via_rif(id_rif, usuario)
    return consolidar_contrapartes_rif(id_rif)


@router.post("/api/investigacao/rifs/{id_rif}/reprocessar-contrapartes")
def rif_reprocessar_contrapartes(id_rif: int, usuario: dict = Depends(exigir_aprovado)):
    """Reextrai as contrapartes do texto já guardado, para aplicar melhorias no parser sem reimportar."""
    _exigir_acesso_via_rif(id_rif, usuario, escrita=True)
    return reprocessar_contrapartes_rif(id_rif)


@router.post("/api/investigacao/rifs/{id_rif}/comunicacoes/{id_comunicacao_rif}/reanalisar-ia")
def rif_comunicacao_reanalisar_ia(id_rif: int, id_comunicacao_rif: int,
                                  usuario: dict = Depends(exigir_aprovado)):
    """Reanalisa com IA o texto de UMA comunicação e regrava as contrapartes identificadas —
    a IA lê o texto em vez do parser REGEX. As demais comunicações não são tocadas."""
    _exigir_acesso_via_rif(id_rif, usuario, escrita=True)
    from funcoes.ia_delegacia import exigir_ia_inquerito
    exigir_ia_inquerito(obter_id_inquerito_de_rif(id_rif), usuario)
    try:
        return analisar_contrapartes_comunicacao_ia(id_comunicacao_rif, id_rif, usuario["id_usuario"])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except ErroProvedorIA as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/api/investigacao/rifs/{id_rif}/identificacoes")
def rif_identificacoes(id_rif: int, usuario: dict = Depends(exigir_aprovado)):
    """Identificações manuais de fatias das contrapartes desconhecidas do RIF."""
    _exigir_acesso_via_rif(id_rif, usuario)
    return listar_identificacoes_rif(id_rif)


@router.post("/api/investigacao/rifs/{id_rif}/identificacoes")
def rif_criar_identificacao(id_rif: int, payload: IdentificacaoRifIn,
                            usuario: dict = Depends(exigir_aprovado)):
    """Registra que parte do valor desconhecido de uma comunicação é de determinada pessoa."""
    _exigir_acesso_via_rif(id_rif, usuario, escrita=True)
    if payload.id_comunicacao_rif is None or not payload.cd_direcao:
        raise HTTPException(status_code=400, detail="Comunicação e direção são obrigatórias.")
    try:
        criado = criar_identificacao_rif(
            id_rif, payload.id_comunicacao_rif, payload.cd_direcao, payload.no_contraparte,
            payload.nu_cpf_cnpj, payload.vl_total, payload.qt_lancamentos, payload.tx_obs,
            usuario["id_usuario"],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, **criado}


def _exigir_acesso_via_identificacao(id_identificacao: int, usuario: dict) -> None:
    id_rif = obter_id_rif_de_identificacao(id_identificacao)
    if id_rif is None:
        raise HTTPException(status_code=404, detail="Identificação não encontrada.")
    _exigir_acesso_via_rif(id_rif, usuario, escrita=True)


@router.put("/api/investigacao/rifs/identificacoes/{id_identificacao}")
def rif_editar_identificacao(id_identificacao: int, payload: IdentificacaoRifIn,
                             usuario: dict = Depends(exigir_aprovado)):
    """Corrige nome, documento, valor, lançamentos ou observação de uma identificação."""
    _exigir_acesso_via_identificacao(id_identificacao, usuario)
    try:
        alterou = editar_identificacao_rif(
            id_identificacao, payload.no_contraparte, payload.nu_cpf_cnpj,
            payload.vl_total, payload.qt_lancamentos, payload.tx_obs,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not alterou:
        raise HTTPException(status_code=404, detail="Identificação não encontrada.")
    return {"ok": True}


@router.delete("/api/investigacao/rifs/identificacoes/{id_identificacao}")
def rif_apagar_identificacao(id_identificacao: int, usuario: dict = Depends(exigir_aprovado)):
    """Desfaz a identificação — o valor volta a compor o desconhecido da comunicação."""
    _exigir_acesso_via_identificacao(id_identificacao, usuario)
    if not apagar_identificacao_rif(id_identificacao):
        raise HTTPException(status_code=404, detail="Identificação não encontrada.")
    return {"ok": True}


@router.get("/api/investigacao/rifs/{id_rif}/ocorrencias")
def rif_ocorrencias(id_rif: int, usuario: dict = Depends(exigir_aprovado)):
    """Enquadramentos/alertas de um RIF."""
    _exigir_acesso_via_rif(id_rif, usuario)
    return listar_ocorrencias_rif(id_rif)


@router.delete("/api/investigacao/rifs/{id_rif}")
def apagar_rif_rota(id_rif: int, usuario: dict = Depends(exigir_aprovado)):
    """Remove um RIF e todas as suas linhas + o PDF vinculado (qualquer usuário com acesso ao inquérito)."""
    _exigir_acesso_via_rif(id_rif, usuario, escrita=True)
    apagar_rif(id_rif)
    return {"ok": True}


# ---------- SIMBA (extrato bancário R304) ----------

@router.get("/api/investigacao/simba-disponiveis")
def simba_disponiveis(usuario: dict = Depends(exigir_aprovado), delegacia: str | None = None):
    """Lista os *R304*.csv de data/, para o seletor da página. Se a delegacia tem nó ativo,
    a lista vem da pasta DO NÓ; senão, da pasta local do central."""
    from funcoes import no_utils
    from funcoes.investigacao_utils import delegacia_permitida
    subpasta = delegacia_permitida(delegacia, usuario)
    remotos = no_utils.listagem_remota(subpasta, subpasta, "simba")
    if remotos is not None:
        return remotos
    return listar_arquivos_simba(subpasta)


@router.post("/api/investigacao/simba-upload")
def upload_simba(
    arquivos: list[UploadFile] = File(...),
    delegacia: str | None = Form(default=None),
    usuario: dict = Depends(exigir_aprovado),
):
    """Recebe o relatório R304 do SIMBA pelo navegador e grava em data/[<delegacia>/] — a mesma
    pasta que GET /api/investigacao/simba-disponiveis lista. Não importa nada por conta própria:
    quem importa continua sendo POST /api/investigacao/extrair-simba, com o nome devolvido aqui.

    Mesmo contrato do upload de documento genérico (POST /api/documentos/upload): a delegacia
    passa por `delegacia_permitida`, e a resposta traz o que salvou em "arquivos" e o que foi
    recusado (não-CSV, vazio, grande demais, cabeçalho que não é R304) em "falhas"."""
    from funcoes.investigacao_utils import delegacia_permitida
    from funcoes.simba_utils import salvar_simba_enviado

    subpasta = delegacia_permitida(delegacia, usuario)
    salvos: list[dict] = []
    falhas: list[str] = []
    for arquivo in arquivos:
        if not arquivo.filename:
            continue
        try:
            salvos.append(salvar_simba_enviado(arquivo.file, arquivo.filename, subpasta))
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


@router.post("/api/investigacao/extrair-simba")
def extrair_simba(payload: SimbaIn, usuario: dict = Depends(exigir_aprovado)):
    """Importa um extrato bancário do SIMBA (relatório R304) para um inquérito: carrega contas,
    lançamentos e contrapartes consolidadas, e anexa uma síntese textual para a IA. Cria o
    inquérito se o número ainda não existir."""
    from funcoes.investigacao_utils import delegacia_permitida

    numero = _validar_numero_ou_caso(payload.numero_inquerito, payload.caso_inteligencia, usuario)

    subpasta = delegacia_permitida(payload.delegacia, usuario)

    try:
        resultado = iniciar_importacao_simba(payload.arquivo, numero, usuario["id_usuario"], subpasta,
                                             delegacia=payload.delegacia,
                                             in_caso_inteligencia=payload.caso_inteligencia)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Arquivo não encontrado em data/.")

    return {"ok": True, **resultado}


@router.get("/api/investigacao/inqueritos/{id_inquerito}/extratos-simba")
def extratos_simba_inquerito(id_inquerito: int, usuario: dict = Depends(exigir_acesso_inquerito)):
    """Extratos SIMBA importados de um inquérito (com contagens), para o bloco no card."""
    return listar_extratos_inquerito(id_inquerito)


def _exigir_acesso_via_simba(id_extrato: int, usuario: dict, escrita: bool = False) -> None:
    """Resolve o inquérito dono do extrato e confere acesso — para rotas que só recebem id_extrato."""
    id_inquerito = obter_id_inquerito_de_extrato(id_extrato)
    if id_inquerito is None:
        raise HTTPException(status_code=404, detail="Extrato não encontrado.")
    if not usuario_tem_acesso_inquerito(id_inquerito, usuario, escrita=escrita):
        raise HTTPException(status_code=403, detail="Sem permissão para este inquérito.")


@router.get("/api/investigacao/simba/{id_extrato}")
def simba_extrato(id_extrato: int, usuario: dict = Depends(exigir_aprovado)):
    """Cabeçalho do extrato (arquivo, período, totais) para o resumo da página."""
    _exigir_acesso_via_simba(id_extrato, usuario)
    return obter_extrato(id_extrato)


@router.get("/api/investigacao/simba/{id_extrato}/contas")
def simba_contas(id_extrato: int, usuario: dict = Depends(exigir_aprovado)):
    """Contas bancárias afastadas no extrato, com totais de crédito/débito e período."""
    _exigir_acesso_via_simba(id_extrato, usuario)
    return listar_contas_simba(id_extrato)


@router.get("/api/investigacao/simba/{id_extrato}/lancamentos")
def simba_lancamentos(
    id_extrato: int,
    conta: int | None = None,
    termo: str | None = None,
    natureza: str | None = None,
    dt_inicio: str | None = None,
    dt_fim: str | None = None,
    valor_minimo: float | None = None,
    pagina: int = 1,
    tamanho: int = 200,
    usuario: dict = Depends(exigir_aprovado),
):
    """Lançamentos filtrados e paginados no servidor (um R304 passa fácil de 100 mil linhas)."""
    _exigir_acesso_via_simba(id_extrato, usuario)
    return listar_lancamentos_simba(
        id_extrato, id_conta=conta, termo=termo, natureza=natureza, dt_inicio=dt_inicio,
        dt_fim=dt_fim, valor_minimo=valor_minimo, pagina=pagina, tamanho=tamanho,
    )


@router.get("/api/investigacao/simba/{id_extrato}/contrapartes")
def simba_contrapartes(
    id_extrato: int,
    conta: int | None = None,
    direcao: str | None = None,
    termo: str | None = None,
    so_internas: bool = False,
    limite: int = 500,
    usuario: dict = Depends(exigir_aprovado),
):
    """Contrapartes por conta e direção (quem creditou / quem foi debitado em cada conta)."""
    _exigir_acesso_via_simba(id_extrato, usuario)
    return listar_contrapartes_simba(
        id_extrato, id_conta=conta, direcao=direcao, termo=termo,
        so_internas=so_internas, limite=limite,
    )


@router.get("/api/investigacao/simba/{id_extrato}/contrapartes-consolidadas")
def simba_contrapartes_consolidadas(
    id_extrato: int,
    termo: str | None = None,
    so_internas: bool = False,
    limite: int = 500,
    usuario: dict = Depends(exigir_aprovado),
):
    """Contrapartes somadas por pessoa em todas as contas do extrato."""
    _exigir_acesso_via_simba(id_extrato, usuario)
    return consolidar_contrapartes_simba(id_extrato, termo=termo, so_internas=so_internas, limite=limite)


@router.get("/api/investigacao/simba/{id_extrato}/serie-mensal")
def simba_serie_mensal(id_extrato: int, conta: int | None = None, usuario: dict = Depends(exigir_aprovado)):
    """Crédito x débito por mês, do extrato inteiro ou de uma conta."""
    _exigir_acesso_via_simba(id_extrato, usuario)
    return serie_mensal_simba(id_extrato, id_conta=conta)


@router.get("/api/investigacao/inqueritos/{id_inquerito}/financeiro-timeline")
def financeiro_timeline(id_inquerito: int, pessoa: str | None = None,
                        usuario: dict = Depends(exigir_acesso_inquerito)):
    """Linha do tempo financeira do inquérito: SIMBA + RIF por mês, num JSON único.

    É a tela de Extras que junta o que hoje vive separado (série mensal do extrato e
    comunicações do RIF) sobre o mesmo eixo temporal — quando a movimentação apareceu, parou
    ou mudou de patamar, e se as duas fontes contam a mesma história no mesmo mês.

    `pessoa` (chave devolvida em `pessoas` no próprio JSON) restringe tudo a uma pessoa só —
    titular da conta no SIMBA, envolvido da comunicação no RIF; sem ela vem a soma de todas."""
    return timeline_financeira_inquerito(id_inquerito, pessoa=pessoa)


@router.get("/api/investigacao/inqueritos/{id_inquerito}/financeiro-timeline/detalhes")
def financeiro_timeline_detalhes(
    id_inquerito: int,
    mes: str,
    limite: int = 200,
    simba_offset: int = 0,
    rif_offset: int = 0,
    pessoa: str | None = None,
    usuario: dict = Depends(exigir_acesso_inquerito)
):
    """Detalhes das transações de um mês específico na linha do tempo financeira.

    Retorna os lançamentos SIMBA e comunicações RIF do mês informado (formato YYYY-MM),
    paginados por fonte (limite/simba_offset/rif_offset), com simba_total/rif_total.
    `pessoa` aplica o mesmo filtro da timeline (titular no SIMBA, envolvido no RIF)."""
    from funcoes.simba_utils import timeline_financeira_detalhes_mes
    return timeline_financeira_detalhes_mes(
        id_inquerito, mes, limite=limite, simba_offset=simba_offset, rif_offset=rif_offset,
        pessoa=pessoa,
    )


@router.get("/api/investigacao/simba/{id_extrato}/grafo")
def simba_grafo(
    id_extrato: int,
    banco: str | None = None,
    direcao: str | None = None,
    valor_minimo: float | None = None,
    so_internas: bool = False,
    tp_pessoa: str | None = None,
    termo: str | None = None,
    limite: int | None = None,
    contexto: bool = False,
    usuario: dict = Depends(exigir_aprovado),
):
    """Fluxo financeiro agregado em três colunas (banco → titular → contraparte), com as
    transações somadas por par de pessoas e a cauda longa reunida em "Demais contrapartes".
    ``contexto`` amplia o ``termo`` para o entorno dos titulares que ele toca (clique em achado)."""
    _exigir_acesso_via_simba(id_extrato, usuario)
    return grafo_simba(
        id_extrato, banco=banco, direcao=direcao, valor_minimo=valor_minimo,
        so_internas=so_internas, tp_pessoa=tp_pessoa, termo=termo, limite=limite,
        contexto=contexto,
    )


@router.get("/api/investigacao/simba/{id_extrato}/alertas")
def simba_alertas(id_extrato: int, usuario: dict = Depends(exigir_aprovado)):
    """Alertas determinísticos do extrato: espécie, fracionamento, valores redondos, repasses
    entre os próprios titulares afastados e triangulação (ciclos A → B → C → A entre eles)."""
    _exigir_acesso_via_simba(id_extrato, usuario)
    return calcular_alertas(id_extrato)


@router.post("/api/investigacao/simba/{id_extrato}/reprocessar-titular-interno")
def simba_reprocessar_titular_interno(id_extrato: int, usuario: dict = Depends(exigir_aprovado)):
    """Normaliza os CPF/CNPJ gravados (o CPF que o banco entregou preenchido até 14 dígitos, lido
    como CNPJ), reconsolida as contrapartes com IN_TITULAR_NO_EXTRATO e regenera a síntese — para
    extratos importados antes dessas correções. Não reimporta o CSV."""
    _exigir_acesso_via_simba(id_extrato, usuario, escrita=True)
    return reprocessar_titular_interno_simba(id_extrato)


@router.post("/api/investigacao/simba/reprocessar-titular-interno-todos")
def simba_reprocessar_titular_interno_todos(usuario: dict = Depends(exigir_admin)):
    """Roda o reprocessamento de IN_TITULAR_NO_EXTRATO em todos os extratos concluídos, de todos
    os inquéritos — admin-only por cruzar inquéritos. Devolve só os que tiveram correção."""
    return reprocessar_titular_interno_todos_simba()


@router.post("/api/investigacao/simba/{id_extrato}/cancelar")
def cancelar_simba_rota(id_extrato: int, usuario: dict = Depends(exigir_aprovado)):
    """Pede que a importação em andamento pare. O que já entrou é descartado pela própria thread —
    extrato pela metade tem total de conta maior que os lançamentos carregados."""
    _exigir_acesso_via_simba(id_extrato, usuario, escrita=True)
    if not solicitar_cancelamento_simba(id_extrato):
        raise HTTPException(status_code=409, detail="Esta importação não está em andamento.")
    return {"ok": True}


@router.delete("/api/investigacao/simba/{id_extrato}")
def apagar_simba_rota(id_extrato: int, usuario: dict = Depends(exigir_aprovado)):
    """Remove o extrato e tudo que veio dele (contas, lançamentos, contrapartes e a síntese)."""
    _exigir_acesso_via_simba(id_extrato, usuario, escrita=True)
    try:
        apagar_simba(id_extrato)
    except ImportacaoEmAndamento:
        raise HTTPException(
            status_code=409,
            detail="A importação deste extrato ainda está em andamento. Cancele-a antes de apagar.",
        )
    return {"ok": True}


# ---------- Nuvem IPED (importa um caso inteiro processado pelo IPED) ----------

@router.get("/api/investigacao/iped-disponivel")
def iped_disponivel():
    """True se há um `java` executável na imagem — o front desabilita a aba sem isso."""
    return {"disponivel": java_disponivel()}


@router.get("/api/investigacao/iped-disponiveis")
def iped_disponiveis(usuario: dict = Depends(exigir_aprovado), delegacia: str | None = None):
    """Lista as pastas de data/ que parecem casos do IPED (IPEDConfig.txt/index), para o seletor."""
    from funcoes.investigacao_utils import delegacia_permitida
    return listar_casos_iped_disponiveis(delegacia_permitida(delegacia, usuario))


@router.post("/api/investigacao/iped-detectar-conta")
def iped_detectar_conta(payload: IpedDetectarContaIn, usuario: dict = Depends(exigir_aprovado)):
    """Pré-visualiza a conta (e-mail) que a importação vai detectar e usar para renomear a
    pasta — o investigador confirma ou edita antes de disparar a extração de verdade."""
    from funcoes.investigacao_utils import delegacia_permitida
    from funcoes.iped_utils import _resolver_pasta_caso

    subpasta = delegacia_permitida(payload.delegacia, usuario)
    try:
        caminho = _resolver_pasta_caso(payload.arquivo, subpasta)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Pasta não encontrada em data/.")

    return {"conta": detectar_conta_iped(caminho)}


@router.post("/api/investigacao/extrair-iped")
def extrair_iped(payload: IpedIn, usuario: dict = Depends(exigir_aprovado)):
    """Importa uma pasta-caso do IPED para um inquérito: lê o índice Lucene em background
    (subprocesso Java, ver api/tools/iped_dumper/IpedDumper.java) e grava cada item em
    ARQ_TB01_ARQUIVOS (mesma tabela do UFDR). Antes de iniciar, identifica a conta
    do caso e renomeia a pasta em data/ para o e-mail (ou usa `payload.conta`, se o
    investigador já confirmou/editou a sugestão). Cria o inquérito se o número ainda não existir."""
    from funcoes.investigacao_utils import delegacia_permitida

    numero = _validar_numero_ou_caso(payload.numero_inquerito, payload.caso_inteligencia, usuario)

    subpasta = delegacia_permitida(payload.delegacia, usuario)

    try:
        resultado = iniciar_importacao_iped(payload.arquivo, numero, usuario["id_usuario"], subpasta,
                                            conta=payload.conta, delegacia=payload.delegacia,
                                            in_caso_inteligencia=payload.caso_inteligencia)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Pasta não encontrada em data/.")

    return {"ok": True, **resultado}


@router.get("/api/investigacao/inqueritos/{id_inquerito}/extracoes-iped")
def extracoes_iped_inquerito(id_inquerito: int, usuario: dict = Depends(exigir_acesso_inquerito)):
    """Casos do IPED importados de um inquérito (com contagens), para o bloco no card."""
    return listar_extracoes_iped_inquerito(id_inquerito)


def _exigir_acesso_via_iped(id_extracao: int, usuario: dict) -> None:
    """Resolve o inquérito dono da extração e confere acesso — para rotas que só recebem id_extracao."""
    id_inquerito = obter_id_inquerito_de_iped(id_extracao)
    if id_inquerito is None:
        raise HTTPException(status_code=404, detail="Extração não encontrada.")
    if not usuario_tem_acesso_inquerito(id_inquerito, usuario, escrita=True):
        raise HTTPException(status_code=403, detail="Sem permissão para este inquérito.")


@router.post("/api/investigacao/iped/{id_extracao}/cancelar")
def cancelar_iped_rota(id_extracao: int, usuario: dict = Depends(exigir_aprovado)):
    """Pede que a importação em andamento pare. O que já entrou é preservado (mesma
    filosofia de contagens parciais do UFDR)."""
    _exigir_acesso_via_iped(id_extracao, usuario)
    if not solicitar_cancelamento_iped(id_extracao):
        raise HTTPException(status_code=409, detail="Esta importação não está em andamento.")
    return {"ok": True}


@router.delete("/api/investigacao/iped/{id_extracao}")
def apagar_iped_rota(id_extracao: int, usuario: dict = Depends(exigir_aprovado)):
    """Remove a extração e tudo que veio dela (a pasta do caso em data/ não é apagada)."""
    _exigir_acesso_via_iped(id_extracao, usuario)
    if not apagar_iped(id_extracao):
        raise HTTPException(
            status_code=409,
            detail="A importação desta extração ainda está em andamento. Cancele-a antes de apagar.",
        )
    return {"ok": True}


# ---------- Contas na nuvem: Minha Atividade, acessos, dispositivos, perfil, agenda ----------
# Genéricas (funcoes/contas_nuvem_utils.py) — hoje quem grava aqui é a Nuvem IPED, que
# decompõe os produtos do Google a partir da pasta-caso; as tabelas/rotas/páginas não são
# específicas de nenhuma fonte: uma futura nuvem Microsoft/Apple reaproveita as mesmas
# telas, bastando gravar nas mesmas tabelas com outro NO_FONTE.

@router.get("/api/investigacao/inqueritos/{id_inquerito}/atividades")
def atividades_conta(
    id_inquerito: int, extracao: int | None = None, busca: str | None = None,
    produto: str | None = None, data_de: str | None = None, data_ate: str | None = None,
    periodo: str | None = None, ordenar: str | None = None, pagina: int = 1, limite: int = 100,
    usuario: dict = Depends(exigir_acesso_inquerito),
):
    """Lista, paginado, os registros de Minha Atividade de uma conta na nuvem. Filtros:
    ?extracao=ID, ?busca=texto (descrição/URL), ?produto=, ?data_de=AAAA-MM-DD e
    ?data_ate=AAAA-MM-DD, ?periodo=madrugada|manha|tarde|noite, ?ordenar=recentes|antigos."""
    return listar_atividades_conta(id_inquerito, extracao, busca, produto, data_de, data_ate,
                                    periodo, ordenar, pagina, limite)


@router.get("/api/investigacao/inqueritos/{id_inquerito}/atividades/filtros")
def atividades_conta_filtros(id_inquerito: int, extracao: int | None = None,
                             usuario: dict = Depends(exigir_acesso_inquerito)):
    """Produtos distintos para o select de filtro da página de Minha Atividade."""
    return facetas_atividades_conta(id_inquerito, extracao)


@router.get("/api/investigacao/inqueritos/{id_inquerito}/atividades/resumo")
def atividades_conta_resumo(
    id_inquerito: int, extracao: int | None = None, busca: str | None = None,
    produto: str | None = None, data_de: str | None = None, data_ate: str | None = None,
    periodo: str | None = None, usuario: dict = Depends(exigir_acesso_inquerito),
):
    """KPIs de Minha Atividade, respeitando os mesmos filtros da listagem."""
    return resumo_atividades_conta(id_inquerito, extracao, busca, produto, data_de, data_ate, periodo)


@router.get("/api/investigacao/inqueritos/{id_inquerito}/acessos")
def acessos_conta(
    id_inquerito: int, extracao: int | None = None, busca: str | None = None,
    produto: str | None = None, data_de: str | None = None, data_ate: str | None = None,
    periodo: str | None = None, ordenar: str | None = None, pagina: int = 1, limite: int = 100,
    usuario: dict = Depends(exigir_acesso_inquerito),
):
    """Lista, paginado, os eventos de acesso à conta (IP, agente, identificadores de sessão).
    Filtros: ?extracao=ID, ?busca=texto (IP/user-agent/produto/identificadores), ?produto=,
    ?data_de=AAAA-MM-DD e ?data_ate=AAAA-MM-DD, ?periodo=madrugada|manha|tarde|noite,
    ?ordenar=recentes|antigos."""
    return listar_acessos_conta(id_inquerito, extracao, busca, produto, data_de, data_ate,
                                 periodo, ordenar, pagina, limite)


@router.get("/api/investigacao/inqueritos/{id_inquerito}/acessos/filtros")
def acessos_conta_filtros(id_inquerito: int, extracao: int | None = None,
                          usuario: dict = Depends(exigir_acesso_inquerito)):
    """Produtos distintos para o select de filtro da página de acessos."""
    return facetas_acessos_conta(id_inquerito, extracao)


@router.get("/api/investigacao/inqueritos/{id_inquerito}/acessos/resumo")
def acessos_conta_resumo(
    id_inquerito: int, extracao: int | None = None, busca: str | None = None,
    produto: str | None = None, data_de: str | None = None, data_ate: str | None = None,
    periodo: str | None = None, usuario: dict = Depends(exigir_acesso_inquerito),
):
    """KPIs de acessos à conta, respeitando os mesmos filtros da listagem."""
    return resumo_acessos_conta(id_inquerito, extracao, busca, produto, data_de, data_ate, periodo)


@router.get("/api/investigacao/inqueritos/{id_inquerito}/acessos-agregados")
def acessos_agregados_conta(
    id_inquerito: int, extracao: int | None = None, busca: str | None = None,
    tipo: str | None = None, data_de: str | None = None, data_ate: str | None = None,
    ordenar: str | None = None, pagina: int = 1, limite: int = 100,
    usuario: dict = Depends(exigir_acesso_inquerito),
):
    """Lista, paginado, os identificadores agregados (dias ativos por identificador). Filtros:
    ?extracao=ID, ?busca=texto, ?tipo=, ?data_de=AAAA-MM-DD e ?data_ate=AAAA-MM-DD (sobre a
    última atividade), ?ordenar=recentes|dias_ativos."""
    return listar_agregados_conta(id_inquerito, extracao, busca, tipo, data_de, data_ate,
                                   ordenar, pagina, limite)


@router.get("/api/investigacao/inqueritos/{id_inquerito}/acessos-agregados/filtros")
def acessos_agregados_conta_filtros(id_inquerito: int, extracao: int | None = None,
                                    usuario: dict = Depends(exigir_acesso_inquerito)):
    """Tipos de identificador distintos para o select de filtro."""
    return facetas_agregados_conta(id_inquerito, extracao)


@router.get("/api/investigacao/inqueritos/{id_inquerito}/acessos-agregados/resumo")
def acessos_agregados_conta_resumo(
    id_inquerito: int, extracao: int | None = None, busca: str | None = None,
    tipo: str | None = None, data_de: str | None = None, data_ate: str | None = None,
    usuario: dict = Depends(exigir_acesso_inquerito),
):
    """KPIs de acessos agregados, respeitando os mesmos filtros da listagem."""
    return resumo_agregados_conta(id_inquerito, extracao, busca, tipo, data_de, data_ate)


@router.get("/api/investigacao/inqueritos/{id_inquerito}/conexoes")
def conexoes_conta(
    id_inquerito: int, extracao: int | None = None, busca: str | None = None,
    data_de: str | None = None, data_ate: str | None = None, pagina: int = 1, limite: int = 100,
    usuario: dict = Depends(exigir_acesso_inquerito),
):
    """Lista, paginado, as conexões entre identificadores vistos juntos numa sessão. Filtros:
    ?extracao=ID, ?busca=texto, ?data_de=AAAA-MM-DD e ?data_ate=AAAA-MM-DD (sobre a última vez)."""
    return listar_conexoes_conta(id_inquerito, extracao, busca, data_de, data_ate, pagina, limite)


@router.get("/api/investigacao/inqueritos/{id_inquerito}/conexoes/resumo")
def conexoes_conta_resumo(
    id_inquerito: int, extracao: int | None = None, busca: str | None = None,
    data_de: str | None = None, data_ate: str | None = None,
    usuario: dict = Depends(exigir_acesso_inquerito),
):
    """KPIs de conexões entre identificadores, respeitando os mesmos filtros da listagem."""
    return resumo_conexoes_conta(id_inquerito, extracao, busca, data_de, data_ate)


@router.get("/api/investigacao/inqueritos/{id_inquerito}/dispositivos-conta")
def dispositivos_conta_rota(
    id_inquerito: int, extracao: int | None = None, busca: str | None = None,
    pagina: int = 1, limite: int = 100, usuario: dict = Depends(exigir_acesso_inquerito),
):
    """Lista, paginado, os dispositivos conhecidos pela conta na nuvem. Filtros: ?extracao=ID,
    ?busca=texto (nome/fabricante/Android ID/IMEI)."""
    return listar_dispositivos_conta(id_inquerito, extracao, busca, pagina, limite)


@router.get("/api/investigacao/inqueritos/{id_inquerito}/dispositivos-conta/resumo")
def dispositivos_conta_resumo(id_inquerito: int, extracao: int | None = None, busca: str | None = None,
                              usuario: dict = Depends(exigir_acesso_inquerito)):
    """KPIs dos dispositivos conhecidos pela conta, respeitando os mesmos filtros da listagem."""
    return resumo_dispositivos_conta(id_inquerito, extracao, busca)


@router.get("/api/investigacao/inqueritos/{id_inquerito}/perfil-conta")
def perfil_conta_rota(id_inquerito: int, extracao: int | None = None,
                      usuario: dict = Depends(exigir_acesso_inquerito)):
    """Lista os perfis de conta do inquérito (?extracao=ID restringe a uma conta —
    normalmente 0-1 registro por conta)."""
    return listar_perfil_conta(id_inquerito, extracao)


@router.get("/api/investigacao/inqueritos/{id_inquerito}/agenda")
def agenda_conta(
    id_inquerito: int, extracao: int | None = None, busca: str | None = None,
    data_de: str | None = None, data_ate: str | None = None, ordenar: str | None = None,
    pagina: int = 1, limite: int = 100, usuario: dict = Depends(exigir_acesso_inquerito),
):
    """Lista, paginado, os eventos de agenda. Filtros: ?extracao=ID, ?busca=texto
    (título/local/descrição), ?data_de=AAAA-MM-DD e ?data_ate=AAAA-MM-DD (sobre o início),
    ?ordenar=recentes|antigos (default antigos)."""
    return listar_eventos_conta(id_inquerito, extracao, busca, data_de, data_ate, ordenar, pagina, limite)


@router.get("/api/investigacao/inqueritos/{id_inquerito}/agenda/resumo")
def agenda_conta_resumo(
    id_inquerito: int, extracao: int | None = None, busca: str | None = None,
    data_de: str | None = None, data_ate: str | None = None,
    usuario: dict = Depends(exigir_acesso_inquerito),
):
    """KPIs da agenda, respeitando os mesmos filtros da listagem."""
    return resumo_eventos_conta(id_inquerito, extracao, busca, data_de, data_ate)


# ---------- Busca de entidades no material do inquérito ----------


class BuscaEntidadesItem(BaseModel):
    """Uma entidade (pessoa física/jurídica) a buscar no material do inquérito."""
    nome: str = ""
    cpf_cnpj: str | None = None
    telefone: str | None = None
    # Hash de custódia (MD5/SHA-1/SHA-256) do arquivo da extração, do documento anexado ou de um
    # arquivo do aparelho. Opcional, como os demais — a tela detecta o tipo por regex e preenche
    # só o campo que o termo digitado é.
    hash: str | None = None


class BuscaEntidadesIn(BaseModel):
    """Corpo aceito pelo POST da busca de entidades.

    `aproximada` liga o casamento por semelhança de nome e a varredura tolerante do texto livre
    (mais achados, mais lento); `grupos` restringe as fontes (vazio = tudo que o inquérito tem)."""
    entidades: list[BuscaEntidadesItem]
    aproximada: bool = True
    grupos: list[str] | None = None
    limite: int = LIMITE_POR_FONTE


@router.get("/api/investigacao/inqueritos/{id_inquerito}/buscar-entidades/fontes")
def buscar_entidades_fontes(id_inquerito: int, usuario: dict = Depends(exigir_acesso_inquerito)):
    """Que grupos de material o inquérito tem, e quais fontes cada um cobre — é o que a tela
    mostra como caixas de seleção antes da busca."""
    return fontes_disponiveis(id_inquerito)


@router.post("/api/investigacao/inqueritos/{id_inquerito}/buscar-entidades")
def buscar_entidades_em_documentos(
    id_inquerito: int,
    payload: BuscaEntidadesIn,
    usuario: dict = Depends(exigir_acesso_inquerito),
):
    """Procura cada entidade informada (nome, CPF/CNPJ, telefone e hash de custódia, todos
    opcionais desde que reste um) em tudo que foi importado para o inquérito — RIF, extrato do
    SIMBA, inquérito em PDF, IRPF, IPEI, telefonia, documentos anexados, celular, nuvem e a própria
    mesa de análise.

    A lógica está em `funcoes/busca_entidades_utils.py` (registro de fontes + motor)."""
    return buscar_entidades(
        id_inquerito,
        [item.model_dump() for item in payload.entidades],
        aproximada=payload.aproximada,
        grupos=payload.grupos,
        limite=payload.limite,
    )


# ---------- Convergência entre inquéritos ----------


def _min_score_valido(min_score: float | None) -> float | None:
    """O campo "Score ≥ %" da tela chega em porcentagem (0–100) e mora em 0–1 no banco.
    Fora da faixa, valueNone — filtra como se estivesse vazio, em vez de erro: é filtro de
    tela, não API pública."""
    if min_score is None:
        return None
    if min_score < 0 or min_score > 100:
        return None
    return min_score / 100.0


@router.get("/api/investigacao/convergencia")
def convergencia_global(
    usuario: dict = Depends(exigir_aprovado),
    delegacia: str | None = None,
    tipo: str | None = None,
    confianca: str | None = None,
    busca: str | None = None,
    min_sinais: int = 0,
    min_score: float | None = None,
    ordem: str = "sinais",
    pagina: int = 1,
    tamanho: int = 10,
):
    """Página de PARES de inquéritos que convergem (com prévia dos sinais), restrito aos
    inquéritos que o usuário pode ver.

    A consulta pesada não roda mais aqui — ela vive em INVESTIGACAO.CONV_TB01_SINAIS,
    recalculada por um laço de fundo a cada hora (funcoes.canonicas_utils). Esta rota é um
    SELECT paginado sobre o cache: filtros, busca, "pelo menos X sinais" e ordenação entram no
    SQL, e a tela baixa UMA página em vez da base inteira (o payload antigo passava de dezenas
    de MB e a tela nunca abria).

    Cache vazio (primeira subida pós-migração) dispara o recálculo em segundo plano e devolve a
    lista vazia com `recalculando: true` — a tela mostra o aviso e tenta de novo sozinha.

    `delegacia` é conveniência de apresentação, não controle de acesso: qualquer usuário pode
    informar qualquer unidade, e o filtro só consegue ESTREITAR o resultado — quem restringe é o
    _filtro_acesso_sql de canonicas_utils, aplicado na leitura nos dois lados do par."""
    cache = status_cache_convergencia()
    if cache["idade_s"] is None:
        disparar_refresh_convergencia()
        cache = status_cache_convergencia()
    resultado = listar_convergencia(
        usuario, delegacia=delegacia, tipo=tipo, confianca=confianca, busca=busca,
        min_sinais=min_sinais, min_score=_min_score_valido(min_score), ordem=ordem,
        pagina=pagina, tamanho=tamanho,
    )
    resultado["gerado_em"] = cache["gerado_em"]
    resultado["recalculando"] = cache["recalculando"] or cache["idade_s"] is None
    return resultado


@router.get("/api/investigacao/convergencia/status")
def convergencia_status(usuario: dict = Depends(exigir_aprovado)):
    """Estado do cache de sinais (quando foi calculado, se há recálculo em curso) — a tela usa
    depois do "Atualizar" para saber quando recarregar."""
    return status_cache_convergencia()


@router.post("/api/investigacao/convergencia/recalcular")
def convergencia_recalcular(usuario: dict = Depends(exigir_aprovado)):
    """Força o recálculo do cache em segundo plano (botão "Atualizar"). Responde na hora: a
    conta leva ~1 minuto e o navegador não espera por ela. Se outro recálculo estiver em curso
    (laço de fundo, outra aba, outra réplica), `recalculando: true` sem duplicar o trabalho."""
    disparar_refresh_convergencia(forcar=True)
    return status_cache_convergencia()


@router.get("/api/investigacao/convergencia/par")
def convergencia_par(
    id_a: int,
    id_b: int,
    busca: str | None = None,
    pagina: int = 1,
    tamanho: int = 50,
    usuario: dict = Depends(exigir_aprovado),
):
    """Sinais de UM par de inquéritos, paginados (o modal "Ver todos os sinais"). Os dois
    lados passam pelo filtro de acesso: par com inquérito invisível devolve lista vazia."""
    return listar_convergencia_par(usuario, id_a, id_b, busca=busca,
                                   pagina=pagina, tamanho=tamanho)


@router.get("/api/investigacao/convergencia/arestas")
def convergencia_arestas(
    usuario: dict = Depends(exigir_aprovado),
    delegacia: str | None = None,
    tipo: str | None = None,
    confianca: str | None = None,
    busca: str | None = None,
    min_sinais: int = 0,
    min_score: float | None = None,
):
    """O grafo inteiro do recorte filtrado, mas SÓ com as arestas (par, contagem, se tem sinal
    exato) — sem texto de sinal. É a forma do rede que o desenho precisa; os sinais de um par
    chegam sob demanda pelo /par quando alguém clica na aresta."""
    return listar_convergencia_arestas(
        usuario, delegacia=delegacia, tipo=tipo, confianca=confianca, busca=busca,
        min_sinais=min_sinais, min_score=_min_score_valido(min_score),
    )


@router.get("/api/investigacao/convergencia/export")
def convergencia_export(
    usuario: dict = Depends(exigir_aprovado),
    delegacia: str | None = None,
    tipo: str | None = None,
    confianca: str | None = None,
    busca: str | None = None,
    min_sinais: int = 0,
    min_score: float | None = None,
):
    """O conjunto filtrado INTEIRO, uma linha por sinal, para o Excel e para o i2. Com a lista
    paginada no servidor, o que se exporta é o que o FILTRO selecionou (nunca a página
    desenhada) — os mesmos filtros da listagem, sem paginação."""
    return exportar_convergencia(
        usuario, delegacia=delegacia, tipo=tipo, confianca=confianca, busca=busca,
        min_sinais=min_sinais, min_score=_min_score_valido(min_score),
    )


@router.get("/api/investigacao/convergencia-resumo")
def convergencia_resumo(
    inqueritos: str | None = None,
    usuario: dict = Depends(exigir_aprovado),
):
    """Alerta de convergência dos cards da lista de investigações, para a PÁGINA inteira de uma
    vez: `?inqueritos=1,2,5` → `{"1": [{id_inquerito_relacionado, nu_inquerito_relacionado,
    qt_identificadores}], ...}`. Inquérito sem convergência simplesmente não aparece na resposta.

    Substitui a chamada da rota unitária card a card (dez consultas de cruzamento por página, e um
    toast de erro por card quando ela falhava).

    Sem `exigir_acesso_inquerito`: não há id no path para ele conferir. Quem restringe é o
    _filtro_acesso_sql aplicado aos dois lados dentro de resumo_convergencia_inqueritos — id de
    inquérito que o usuário não pode ver não produz linha, então a lista solta do cliente não vira
    oráculo."""
    ids = _ids_de_inqueritos(inqueritos)
    if not ids:
        return {}
    return resumo_convergencia_inqueritos(ids, usuario)


def _ids_de_inqueritos(valor: str | None) -> list[int]:
    """'3,7,12' -> [3, 7, 12]. Descarta o que não for número e limita o lote: a lista vem do
    cliente e o único uso legítimo é a página corrente, que tem dezenas de cards no máximo."""
    if not valor:
        return []
    ids = [p.strip() for p in valor.split(",") if p.strip().isdigit()]
    return [int(p) for p in ids][:200]


@router.get("/api/investigacao/inqueritos/{id_inquerito}/convergencia")
def convergencia_inquerito(
    id_inquerito: int,
    usuario: dict = Depends(exigir_acesso_inquerito),
):
    """Identificadores canônicos deste inquérito que também aparecem em OUTROS
    inquéritos visíveis ao usuário. Devolve lista de {tp_identificador, co_valor,
    id_inquerito_relacionado, nu_inquerito_relacionado, no_entidade_origem,
    no_entidade_destino}."""
    return listar_convergencia_inquerito(id_inquerito, usuario)

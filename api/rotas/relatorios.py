"""Rotas do módulo de relatórios:

  - Padrões de relatório e seus blocos — biblioteca global do administrador (exigir_admin),
    montada na página "Padrão de relatórios".
  - Marcações "marcar para o relatório" — presas a registros de um inquérito, com a mesma
    checagem de acesso por caso das entidades (ver rotas/entidades.py).
  - Geração do relatório final em .docx para um inquérito + padrão: o POST dispara o job em
    segundo plano (ver funcoes/relatorios_geracao.py) e o arquivo é baixado por rota própria.

O router é registrado com exigir_aprovado no main.py; cada rota de escrita de padrão exige
admin por conta própria, e cada rota de caso confere o acesso ao inquérito.
"""

from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from funcoes.auth_utils import exigir_admin, exigir_aprovado
from funcoes.investigacao_utils import exigir_acesso_inquerito, exigir_escrita_inquerito, usuario_tem_acesso_inquerito
from funcoes.ipl_utils import listar_ipls_inquerito
from funcoes.relatorios_geracao import (
    GeracaoOcupada,
    RecursoDeOutroInquerito,
    iniciar_geracao,
    listar_geracoes_recentes,
    obter_geracao,
)
from funcoes.rif_utils import listar_rifs_inquerito
from funcoes.simba_utils import listar_extratos_inquerito
from funcoes.relatorios_utils import (
    CATALOGO_VARIAVEIS_AUTO,
    ROTULO_TP_BLOCO,
    TIPOS_BLOCO_BANCARIO,
    TIPOS_BLOCO_RIF,
    atualizar_bloco,
    atualizar_marcacao,
    atualizar_padrao,
    criar_bloco,
    criar_marcacao,
    criar_padrao,
    definir_ativo,
    listar_marcacoes,
    listar_marcacoes_inquerito,
    listar_padroes,
    listar_padroes_ativos,
    mover_bloco,
    obter_id_inquerito_de_marcacao,
    obter_padrao,
    remover_bloco,
    remover_marcacao,
    remover_padrao,
    tokens_manuais,
    variaveis_auto,
)

router = APIRouter()


def _exigir_acesso(id_inquerito: int | None, usuario: dict, detalhe: str = "Sem permissão para este inquérito.", *, escrita: bool = False):
    """Guard das rotas que recebem só o id do recurso: 404 se não existe, 403 se sem acesso."""
    if id_inquerito is None:
        raise HTTPException(status_code=404, detail="Não encontrado.")
    if not usuario_tem_acesso_inquerito(id_inquerito, usuario, escrita=escrita):
        raise HTTPException(status_code=403, detail=detalhe)


# =====================================================================
# Padrões de relatório (admin)
# =====================================================================


class PadraoIn(BaseModel):
    nome: str = Field(..., max_length=150)
    descricao: str | None = Field(None, max_length=4000)


class AtivoIn(BaseModel):
    ativo: bool


class BlocoIn(BaseModel):
    tp_bloco: str = Field(..., max_length=20)
    titulo: str | None = Field(None, max_length=200)
    conteudo: str | None = Field(None, max_length=20000)


class MoverIn(BaseModel):
    direcao: str = Field(..., pattern="^(cima|baixo)$")


@router.get("/api/relatorios/variaveis")
def variaveis_catalogo(usuario: dict = Depends(exigir_admin)):
    """Catálogo das variáveis automáticas, para o painel de ajuda do construtor de padrões."""
    return CATALOGO_VARIAVEIS_AUTO


@router.get("/api/relatorios/padroes")
def padroes(usuario: dict = Depends(exigir_admin)):
    """Lista todos os padrões (ativos e inativos)."""
    return listar_padroes()


@router.post("/api/relatorios/padroes")
def criar_padrao_rota(payload: PadraoIn, usuario: dict = Depends(exigir_admin)):
    try:
        id_padrao = criar_padrao(payload.nome, payload.descricao, usuario["id_usuario"])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "id_padrao": id_padrao}


@router.get("/api/relatorios/padroes/{id_padrao}")
def obter_padrao_rota(id_padrao: int, usuario: dict = Depends(exigir_admin)):
    """Padrão com seus blocos ordenados (para editar no construtor)."""
    padrao = obter_padrao(id_padrao)
    if padrao is None:
        raise HTTPException(status_code=404, detail="Padrão não encontrado.")
    return padrao


@router.post("/api/relatorios/padroes/{id_padrao}")
def atualizar_padrao_rota(id_padrao: int, payload: PadraoIn, usuario: dict = Depends(exigir_admin)):
    try:
        atualizado = atualizar_padrao(id_padrao, payload.nome, payload.descricao)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not atualizado:
        raise HTTPException(status_code=404, detail="Padrão não encontrado.")
    return {"ok": True}


@router.post("/api/relatorios/padroes/{id_padrao}/ativo")
def alterar_ativo_rota(id_padrao: int, payload: AtivoIn, usuario: dict = Depends(exigir_admin)):
    if not definir_ativo(id_padrao, payload.ativo):
        raise HTTPException(status_code=404, detail="Padrão não encontrado.")
    return {"ok": True}


@router.delete("/api/relatorios/padroes/{id_padrao}")
def remover_padrao_rota(id_padrao: int, usuario: dict = Depends(exigir_admin)):
    if not remover_padrao(id_padrao):
        raise HTTPException(status_code=404, detail="Padrão não encontrado.")
    return {"ok": True}


# ---------- blocos (admin) ----------


@router.post("/api/relatorios/padroes/{id_padrao}/blocos")
def criar_bloco_rota(id_padrao: int, payload: BlocoIn, usuario: dict = Depends(exigir_admin)):
    try:
        id_bloco = criar_bloco(id_padrao, payload.tp_bloco, payload.titulo, payload.conteudo)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "id_bloco": id_bloco}


@router.post("/api/relatorios/blocos/{id_bloco}")
def atualizar_bloco_rota(id_bloco: int, payload: BlocoIn, usuario: dict = Depends(exigir_admin)):
    try:
        atualizado = atualizar_bloco(id_bloco, payload.tp_bloco, payload.titulo, payload.conteudo)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not atualizado:
        raise HTTPException(status_code=404, detail="Bloco não encontrado.")
    return {"ok": True}


@router.post("/api/relatorios/blocos/{id_bloco}/mover")
def mover_bloco_rota(id_bloco: int, payload: MoverIn, usuario: dict = Depends(exigir_admin)):
    try:
        mover_bloco(id_bloco, payload.direcao)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


@router.delete("/api/relatorios/blocos/{id_bloco}")
def remover_bloco_rota(id_bloco: int, usuario: dict = Depends(exigir_admin)):
    if not remover_bloco(id_bloco):
        raise HTTPException(status_code=404, detail="Bloco não encontrado.")
    return {"ok": True}


# =====================================================================
# Marcações "marcar para o relatório" (acesso ao inquérito)
# =====================================================================


class MarcacaoIn(BaseModel):
    id_inquerito: int
    tp_origem: str = Field(..., max_length=30)
    id_origem: int
    comentario: str | None = Field(None, max_length=4000)
    cor: str | None = Field(None, max_length=7)


class ComentarioIn(BaseModel):
    comentario: str | None = Field(None, max_length=4000)


@router.get("/api/investigacao/inqueritos/{id_inquerito}/marcacoes")
def marcacoes(id_inquerito: int, tp_origem: str, usuario: dict = Depends(exigir_acesso_inquerito)):
    """Marcações de um tipo de origem, para o widget destacar registros já incluídos.

    Os marcadores que vêm junto são só os DESTE usuário: a marcação é do caso, a etiqueta é
    pessoal (ver funcoes/marcadores_utils.py)."""
    return listar_marcacoes(id_inquerito, tp_origem, usuario["id_usuario"])


@router.post("/api/investigacao/marcacoes")
def criar_marcacao_rota(payload: MarcacaoIn, usuario: dict = Depends(exigir_aprovado)):
    """Marca um registro para o relatório (comentário opcional).

    Marcar o que já está marcado não cria uma segunda marcação — atualiza a existente e
    devolve ja_existia=True, para a tela avisar em vez de deixar o trecho sair duplicado no
    relatório sem ninguém perceber."""
    if not usuario_tem_acesso_inquerito(payload.id_inquerito, usuario, escrita=True):
        raise HTTPException(status_code=403, detail="Sem permissão para este inquérito.")
    try:
        id_marcacao, ja_existia = criar_marcacao(
            payload.id_inquerito, payload.tp_origem, payload.id_origem, payload.comentario, usuario["id_usuario"],
            cor=payload.cor
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "id_marcacao": id_marcacao, "ja_existia": ja_existia}


@router.post("/api/investigacao/marcacoes/{id_marcacao}/comentario")
def editar_comentario_rota(id_marcacao: int, payload: ComentarioIn, usuario: dict = Depends(exigir_aprovado)):
    """Troca o comentário (opcional) de uma marcação."""
    _exigir_acesso(obter_id_inquerito_de_marcacao(id_marcacao), usuario, escrita=True)
    if not atualizar_marcacao(id_marcacao, payload.comentario):
        raise HTTPException(status_code=404, detail="Marcação não encontrada.")
    return {"ok": True}


@router.delete("/api/investigacao/marcacoes/{id_marcacao}")
def remover_marcacao_rota(id_marcacao: int, usuario: dict = Depends(exigir_aprovado)):
    """Desfaz uma marcação."""
    _exigir_acesso(obter_id_inquerito_de_marcacao(id_marcacao), usuario, escrita=True)
    remover_marcacao(id_marcacao)
    return {"ok": True}


# =====================================================================
# Geração do relatório (acesso ao inquérito)
# =====================================================================


class GerarIn(BaseModel):
    valores: dict[str, str] = Field(default_factory=dict)
    ids_marcacoes: list[int] | None = None
    ids_rif: list[int] | None = None
    ids_extratos: list[int] | None = None
    ids_ipl: list[int] | None = None
    ids_blocos: list[int] | None = None
    in_comentario_ia: bool = False
    in_reescrever_comentarios: bool = False
    # True = imprime o fio completo das CONVERSAS marcadas nos blocos 'chat'/'whatsapp'
    # (a página do WhatsApp manda True ao gerar o relatório da conversa aberta; nas demais
    # telas o padrão segue False: marcar conversa é triagem, não pedir o conteúdo inteiro)
    in_expandir_conversas: bool = False


# tipos de bloco que consomem cada seção "escolha os arquivos" da tela de geração — um padrão de
# RIF/dados bancários/inquérito em PDF não tem registros "marcados" nas telas de dados, tem
# RIFs/extratos/IPLs importados no inquérito inteiro; por isso o formulário mostra um seletor de
# arquivos, não a lista de marcações (que fica vazia e confundia o usuário, ver relato original do bug)
TIPOS_BLOCO_MARCACOES = {"marcacoes", "chat", "whatsapp"}
TIPOS_BLOCO_IPL = {"ipl_resumo", "ipl_pecas", "ipl_pessoas", "ipl_achados"}


@router.get("/api/relatorios/padroes-ativos")
def padroes_ativos(usuario: dict = Depends(exigir_aprovado)):
    """Padrões ativos (id + nome + descrição) — o que a tela de geração oferece."""
    return listar_padroes_ativos()


@router.get("/api/investigacao/inqueritos/{id_inquerito}/relatorios/{id_padrao}/preparar")
def preparar_geracao(id_inquerito: int, id_padrao: int, usuario: dict = Depends(exigir_acesso_inquerito)):
    """Dados para montar o formulário de geração: os blocos do padrão (para o autor escolher quais
    entram nesta geração), variáveis manuais a preencher, prévia das automáticas e, conforme os
    tipos de bloco do padrão, o que o usuário escolhe incluir — marcações "para o relatório"
    (padrão genérico/investigação) e/ou os arquivos de RIF, de extrato bancário e/ou de inquérito
    em PDF importados no inquérito."""
    padrao = obter_padrao(id_padrao)
    if padrao is None:
        raise HTTPException(status_code=404, detail="Padrão não encontrado.")

    tipos = {b["tp_bloco"] for b in padrao["blocos"]}
    # padrão de RIF/dados bancários/inquérito em PDF pode ter um bloco de marcações genéricas junto
    # (ex.: "Registros marcados pela equipe"), mas a tela não precisa oferecer esse seletor nesses
    # casos — o foco ali é escolher os arquivos, e o bloco de marcações sai com tudo que estiver marcado
    tem_documento_proprio = bool(tipos & (TIPOS_BLOCO_RIF | TIPOS_BLOCO_BANCARIO | TIPOS_BLOCO_IPL))
    mostrar_marcacoes = bool(tipos & TIPOS_BLOCO_MARCACOES) and not tem_documento_proprio
    resultado = {
        "padrao": {"id_padrao": padrao["id_padrao"], "no_padrao": padrao["no_padrao"]},
        # o rótulo do tipo vem pronto do servidor: a tela de geração não precisa manter uma
        # segunda cópia do mapa de tipos de bloco (a do construtor de padrões já existe, e duas
        # cópias divergem no próximo tipo novo)
        "blocos": [
            {
                "id_bloco": b["id_bloco"],
                "tp_bloco": b["tp_bloco"],
                "no_titulo": b.get("no_titulo"),
                "rotulo_tipo": ROTULO_TP_BLOCO.get(b["tp_bloco"], b["tp_bloco"]),
            }
            for b in padrao["blocos"]
        ],
        "tokens_manuais": tokens_manuais(padrao),
        "variaveis_auto": variaveis_auto(id_inquerito, usuario),
        "mostrar_marcacoes": mostrar_marcacoes,
        "mostrar_comentario_ia": bool(tipos & {"chat", "whatsapp"}),
        # a reescrita mexe no comentário do analista, e ele só é impresso no bloco 'chat' (depois
        # de cada trecho) e na coluna "Observação" do bloco 'marcacoes' — a tabela do bloco
        # 'whatsapp' não tem comentário, então ali a opção não faria nada
        "mostrar_reescrever_comentarios": bool(tipos & {"chat", "marcacoes"}),
    }
    if mostrar_marcacoes:
        # com os marcadores de quem está gerando: é por eles que a tela agrupa a seleção
        resultado["marcacoes"] = listar_marcacoes_inquerito(id_inquerito, usuario["id_usuario"])
    if tipos & TIPOS_BLOCO_RIF:
        resultado["arquivos_rif"] = [
            {
                "id_rif": r["id_rif"], "nu_rif": r["nu_rif"], "no_pasta": r["no_pasta"],
                "qt_comunicacoes": r["qt_comunicacoes"],
            }
            for r in listar_rifs_inquerito(id_inquerito)
        ]
    if tipos & TIPOS_BLOCO_BANCARIO:
        resultado["arquivos_bancario"] = [
            {
                "id_extrato": s["id_extrato"], "no_arquivo": s["no_arquivo"],
                "qt_lancamentos": s["qt_lancamentos"],
            }
            for s in listar_extratos_inquerito(id_inquerito)
            if s["cd_status"] == "concluido"
        ]
    if tipos & TIPOS_BLOCO_IPL:
        resultado["arquivos_ipl"] = [
            {
                "id_ipl": p["id_ipl"], "nu_ipl": p["nu_ipl"], "no_arquivo": p["no_arquivo"],
                "qt_folhas": p["qt_folhas"],
            }
            for p in listar_ipls_inquerito(id_inquerito)
        ]
    return resultado


@router.post("/api/investigacao/inqueritos/{id_inquerito}/relatorios/{id_padrao}/gerar")
def gerar_rota(id_inquerito: int, id_padrao: int, payload: GerarIn, usuario: dict = Depends(exigir_acesso_inquerito)):
    """Dispara a geração do relatório em segundo plano e devolve o id para acompanhamento.

    A montagem não cabe na requisição: um caso grande com as opções de IA ligadas (resumo das
    conversas, reescrita dos comentários) passa dos 60 s em que o nginx corta, e o usuário via erro
    num relatório que ia terminar bem. O .docx é baixado depois pela rota de download."""
    try:
        id_geracao = iniciar_geracao(
            id_inquerito, id_padrao, usuario,
            {
                "valores": payload.valores,
                "ids_marcacoes": payload.ids_marcacoes,
                "ids_rif": payload.ids_rif,
                "ids_extratos": payload.ids_extratos,
                "ids_ipl": payload.ids_ipl,
                "ids_blocos": payload.ids_blocos,
                "comentario_ia": payload.in_comentario_ia,
                "reescrever_comentarios": payload.in_reescrever_comentarios,
                "expandir_conversas": payload.in_expandir_conversas,
            },
        )
    except GeracaoOcupada as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except RecursoDeOutroInquerito:
        # id de outro inquérito no payload: respondemos como se não existisse (não confirmamos terceiros)
        raise HTTPException(status_code=404, detail="Registro não encontrado neste inquérito.")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "id_geracao": id_geracao}


def _geracao_com_acesso(id_geracao: int, usuario: dict) -> dict:
    """Carrega a geração conferindo o acesso ao inquérito dela (404/403)."""
    geracao = obter_geracao(id_geracao)
    if geracao is None:
        raise HTTPException(status_code=404, detail="Geração não encontrada.")
    _exigir_acesso(geracao["id_inquerito"], usuario)
    return geracao


@router.get("/api/investigacao/inqueritos/{id_inquerito}/geracoes-relatorio/recentes")
def geracoes_recentes_rota(id_inquerito: int, usuario: dict = Depends(exigir_acesso_inquerito)):
    """Últimas gerações do usuário logado neste inquérito, para o histórico do modal."""
    return listar_geracoes_recentes(id_inquerito, usuario["id_usuario"])


@router.get("/api/investigacao/geracoes-relatorio/{id_geracao}")
def status_geracao_rota(id_geracao: int, usuario: dict = Depends(exigir_aprovado)):
    """Andamento da geração, para o polling da tela."""
    geracao = _geracao_com_acesso(id_geracao, usuario)
    # o caminho em disco é detalhe do servidor e não tem por que trafegar
    geracao.pop("caminho_arquivo", None)
    return geracao


@router.get("/api/investigacao/geracoes-relatorio/{id_geracao}/download")
def baixar_geracao_rota(id_geracao: int, usuario: dict = Depends(exigir_aprovado)):
    """Baixa o .docx de uma geração concluída.

    O acesso é conferido de novo aqui: entre disparar e baixar pode ter passado tempo, e a permissão
    sobre o inquérito pode ter mudado."""
    geracao = _geracao_com_acesso(id_geracao, usuario)
    if geracao["cd_status"] != "concluido" or not geracao.get("caminho_arquivo"):
        raise HTTPException(status_code=409, detail="O relatório ainda não está pronto.")
    caminho = Path(geracao["caminho_arquivo"])
    if not caminho.exists():
        # arquivo já limpo por antiguidade — dizer isso é melhor que um 500 de arquivo sumido
        raise HTTPException(status_code=404, detail="O arquivo desta geração não está mais disponível. Gere o relatório novamente.")
    nome = geracao.get("no_arquivo") or "relatorio.docx"
    return FileResponse(
        path=str(caminho),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(nome)}"},
    )

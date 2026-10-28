"""Rotas de administração de usuários — todas exigem administrador (ver dependencies do router)."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from funcoes import cert_utils, config_ia_usuario
from funcoes.auditoria_usuarios import registrar_auditoria_usuario
from funcoes.auth_utils import (
    PERFIS_VALIDOS,
    criar_token_redefinicao,
    criar_usuario,
    definir_aprovado,
    definir_ativo,
    exigir_admin,
    horas_validade_token,
    listar_usuarios,
    obter_usuario,
)
from funcoes.db_utils import get_connection
from funcoes.email_utils import enviar_redefinicao_senha, link_redefinicao, smtp_configurado
from funcoes.env_utils import mask_key, sanitize

router = APIRouter(dependencies=[Depends(exigir_admin)])


class UsuarioIn(BaseModel):
    login: str = Field(..., max_length=60)
    nome: str = Field(..., max_length=150)
    senha: str = Field(..., max_length=200)
    perfil: str = "analista"
    email: str | None = Field(default=None, max_length=200)
    id_delegacia: int | None = Field(default=None)


class AtivoIn(BaseModel):
    ativo: bool


class AprovadoIn(BaseModel):
    aprovado: bool


class IaAtivaIn(BaseModel):
    ativa: bool


class RedefinicaoIn(BaseModel):
    # Só tenta enviar se o admin pedir E o usuário tiver e-mail; o link é devolvido de qualquer jeito.
    enviar_email: bool = False


def _eh_ultimo_admin_ativo(id_usuario: int) -> bool:
    """Diz se este usuário é o único administrador ativo — desativá-lo/rebaixá-lo trancaria o sistema."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT in_admin, in_ativo FROM seguranca.usr_tb01_usuarios WHERE id_usuario = %s",
                (id_usuario,),
            )
            linha = cur.fetchone()
            if not linha or not linha[0] or not linha[1]:
                return False
            cur.execute(
                "SELECT count(*) FROM seguranca.usr_tb01_usuarios WHERE in_admin AND in_ativo AND id_usuario <> %s",
                (id_usuario,),
            )
            return (cur.fetchone() or [0])[0] == 0


@router.get("/api/usuarios")
def usuarios():
    """Lista todos os usuários cadastrados."""
    return listar_usuarios()


@router.post("/api/usuarios")
def criar(payload: UsuarioIn):
    """Cria um novo usuário."""
    perfil = (payload.perfil or "analista").strip().lower()
    if perfil not in PERFIS_VALIDOS:
        raise HTTPException(status_code=400, detail="Perfil inválido.")
    try:
        id_usuario = criar_usuario(payload.login, payload.nome, payload.senha, perfil, email=payload.email, id_delegacia=payload.id_delegacia)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    registrar_auditoria_usuario(
        id_usuario_alvo=id_usuario,
        tp_acao="criar",
        detalhes={"login": payload.login, "nome": payload.nome, "perfil": perfil, "email": payload.email, "id_delegacia": payload.id_delegacia},
    )
    return {"ok": True, "id_usuario": id_usuario}


@router.post("/api/usuarios/{id_usuario}/ativo")
def alterar_ativo(id_usuario: int, payload: AtivoIn, admin: dict = Depends(exigir_admin)):
    """Ativa ou desativa um usuário (bloqueia login sem apagar o histórico)."""
    if id_usuario == admin.get("id_usuario") and not payload.ativo:
        raise HTTPException(status_code=400, detail="Você não pode desativar a sua própria conta.")
    if not payload.ativo and _eh_ultimo_admin_ativo(id_usuario):
        raise HTTPException(status_code=400, detail="Este é o último administrador ativo — promova outra pessoa antes.")
    if not definir_ativo(id_usuario, payload.ativo):
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")
    registrar_auditoria_usuario(
        id_usuario_alvo=id_usuario,
        tp_acao="ativar" if payload.ativo else "desativar",
        detalhes={"ativo": payload.ativo},
    )
    return {"ok": True}


@router.post("/api/usuarios/{id_usuario}/aprovado")
def alterar_aprovado(id_usuario: int, payload: AprovadoIn, admin: dict = Depends(exigir_admin)):
    """Aprova ou revoga o acesso de um usuário autocadastrado às funcionalidades do sistema."""
    if id_usuario == admin.get("id_usuario") and not payload.aprovado:
        raise HTTPException(status_code=400, detail="Você não pode revogar a sua própria aprovação.")
    if not definir_aprovado(id_usuario, payload.aprovado):
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")
    registrar_auditoria_usuario(
        id_usuario_alvo=id_usuario,
        tp_acao="aprovar" if payload.aprovado else "revogar_aprovacao",
        detalhes={"aprovado": payload.aprovado},
    )
    return {"ok": True}


@router.post("/api/usuarios/{id_usuario}/ia")
def alterar_ia_usuario(id_usuario: int, payload: IaAtivaIn):
    """Liga ou desliga a IA de um usuário.

    Desligada, ele perde TODA IA — nem a própria, nem a das Configurações (o portão é o mesmo do
    flag por delegacia, funcoes/ia_delegacia.py). Diferente de desativar a conta: o analista segue
    trabalhando no resto do sistema. A configuração própria, se houver, fica guardada — religar a
    IA devolve exatamente o que era antes."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE seguranca.usr_tb01_usuarios SET in_ia_ativa = %s WHERE id_usuario = %s",
                (payload.ativa, id_usuario),
            )
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="Usuário não encontrado.")
        conn.commit()
    registrar_auditoria_usuario(
        id_usuario_alvo=id_usuario,
        tp_acao="ativar_ia" if payload.ativa else "desativar_ia",
        detalhes={"ativa": payload.ativa},
    )
    return {"ok": True}


@router.post("/api/usuarios/{id_usuario}/redefinicao")
def gerar_redefinicao(id_usuario: int, payload: RedefinicaoIn):
    """Gera um link de redefinição de senha para um usuário e devolve ao administrador.

    Este é o caminho que não depende de nada: o admin gera, entrega o link ao usuário (pessoalmente,
    por telefone, pelo canal interno) e é o próprio usuário quem escolhe a senha nova — o admin
    nunca fica sabendo qual é. Opcionalmente também envia por e-mail, se houver SMTP e e-mail
    cadastrado.

    Devolve o token separado da url porque APP_URL pode não estar configurado; nesse caso a tela
    mostra o código para o usuário colar à mão em redefinir_senha.html.
    """
    usuario = obter_usuario(id_usuario)
    if usuario is None:
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")

    token, expiracao = criar_token_redefinicao(id_usuario, "admin")

    enviado_para = None
    if payload.enviar_email and usuario["no_email"] and smtp_configurado():
        if enviar_redefinicao_senha(
            usuario["no_email"], usuario["no_usuario"], token, horas_validade_token("admin")
        ):
            enviado_para = usuario["no_email"]

    registrar_auditoria_usuario(
        id_usuario_alvo=id_usuario,
        tp_acao="redefinir_senha",
        detalhes={"enviar_email": payload.enviar_email, "enviado_para": enviado_para},
    )

    return {
        "ok": True,
        "token": token,
        "url": link_redefinicao(token),
        "dt_expiracao": expiracao.isoformat(),
        "enviado_para": enviado_para,
        "email_disponivel": bool(usuario["no_email"]) and smtp_configurado(),
    }


class UsuarioUpdateIn(BaseModel):
    nome: str = Field(..., max_length=150)
    email: str | None = Field(default=None, max_length=200)
    perfil: str = "analista"
    id_delegacia: int | None = Field(default=None)


class DelegaciaUsuarioIn(BaseModel):
    id_delegacia: int | None = None


@router.put("/api/usuarios/{id_usuario}")
def atualizar_usuario(id_usuario: int, payload: UsuarioUpdateIn, admin: dict = Depends(exigir_admin)):
    """Atualiza nome, email, perfil e delegacia de um usuário."""
    perfil = (payload.perfil or "analista").strip().lower()
    if perfil not in PERFIS_VALIDOS:
        raise HTTPException(status_code=400, detail="Perfil inválido.")
    if id_usuario == admin.get("id_usuario") and perfil != "administrador":
        raise HTTPException(status_code=400, detail="Você não pode remover seu próprio perfil de administrador.")
    if perfil != "administrador" and _eh_ultimo_admin_ativo(id_usuario):
        raise HTTPException(status_code=400, detail="Este é o último administrador ativo — promova outra pessoa antes.")
    admin = perfil == "administrador"
    email = (payload.email or "").strip().lower() or None
    with get_connection() as conn:
        with conn.cursor() as cur:
            if email:
                cur.execute(
                    "SELECT 1 FROM seguranca.usr_tb01_usuarios WHERE lower(no_email) = %s AND id_usuario <> %s",
                    (email, id_usuario),
                )
                if cur.fetchone():
                    raise HTTPException(status_code=400, detail="E-mail já pertence a outro usuário.")
            cur.execute(
                """
                UPDATE seguranca.usr_tb01_usuarios
                SET no_usuario = %s, no_email = %s, in_admin = %s, tp_perfil = %s, id_delegacia = %s
                WHERE id_usuario = %s
                """,
                (payload.nome.strip(), email, admin, perfil, payload.id_delegacia, id_usuario),
            )
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="Usuário não encontrado.")
        conn.commit()
    registrar_auditoria_usuario(
        id_usuario_alvo=id_usuario,
        tp_acao="editar",
        detalhes={"nome": payload.nome.strip(), "email": email, "perfil": perfil, "id_delegacia": payload.id_delegacia},
    )
    return {"ok": True}


@router.put("/api/usuarios/{id_usuario}/delegacia")
def alterar_delegacia(id_usuario: int, payload: DelegaciaUsuarioIn):
    """Altera a delegacia de um usuário."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE seguranca.usr_tb01_usuarios SET id_delegacia = %s WHERE id_usuario = %s",
                (payload.id_delegacia, id_usuario),
            )
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="Usuário não encontrado.")
        conn.commit()
    registrar_auditoria_usuario(
        id_usuario_alvo=id_usuario,
        tp_acao="alterar_delegacia",
        detalhes={"id_delegacia": payload.id_delegacia},
    )
    return {"ok": True}


# =====================================================================
# IA própria do usuário — a exceção à configuração global das Configurações
#
# O .env é o padrão de todo mundo; aqui o administrador define, para UM usuário, outro
# provedor/modelo/chave. Quem não tem linha continua no padrão. Ver funcoes/config_ia_usuario.py
# e db/init/39_config_ia_usuario.sql.
# =====================================================================

class ConfigIaIn(BaseModel):
    ativa: bool = True
    provider: str = Field("", max_length=30)
    model: str = Field("", max_length=200)
    apiUrl: str = Field("", max_length=500)
    # Vazio mantém a chave gravada, como nas Configurações — a tela nunca recebe a chave de volta
    # e portanto não teria como reenviá-la.
    apiKey: str = Field("", max_length=500)
    contextLength: int = Field(0, ge=0, le=2_000_000)
    reasoningEffort: str = Field("", max_length=10)
    # Mesma regra da chave: em branco mantém o certificado gravado.
    caCert: str = Field("", max_length=32_000)
    removerCaCert: bool = False


class ConfigIaOut(BaseModel):
    definida: bool
    ativa: bool
    provider: str
    model: str
    apiUrl: str
    apiKeyMasked: str
    contextLength: int
    reasoningEffort: str
    caCertResumo: str
    dt_atualizacao: str | None


@router.get("/api/usuarios/config-ia")
def usuarios_com_ia_propria():
    """Ids dos usuários com IA própria ativa — a listagem usa só para o selo na linha.

    Endpoint separado, e não uma coluna em GET /api/usuarios, de propósito: numa instalação que
    ainda não aplicou a migração 39 a tela de usuários continua abrindo inteira, só sem o selo.
    """
    return {"ids": config_ia_usuario.ids_configurados()}


@router.get("/api/usuarios/{id_usuario}/config-ia", response_model=ConfigIaOut)
def obter_config_ia(id_usuario: int):
    """Configuração de IA do usuário. A chave nunca sai daqui: vai só a máscara, e o certificado
    vai como descrição (nome, validade, impressão digital), igual às Configurações."""
    if obter_usuario(id_usuario) is None:
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")
    linha = config_ia_usuario.obter(id_usuario)
    if linha is None:
        return ConfigIaOut(
            definida=False, ativa=False, provider="", model="", apiUrl="", apiKeyMasked="",
            contextLength=0, reasoningEffort="", caCertResumo="", dt_atualizacao=None,
        )
    return ConfigIaOut(
        definida=True,
        ativa=linha["in_ativa"],
        provider=linha["co_provedor"],
        model=linha["no_modelo"],
        apiUrl=linha["tx_api_url"],
        apiKeyMasked=mask_key(linha["tx_api_key"]),
        contextLength=linha["qt_contexto"] or 0,
        reasoningEffort=linha["co_esforco_raciocinio"],
        caCertResumo=cert_utils.resumo(linha["tx_ca_cert"]),
        dt_atualizacao=linha["dt_atualizacao"].isoformat() if linha["dt_atualizacao"] else None,
    )


@router.put("/api/usuarios/{id_usuario}/config-ia")
def salvar_config_ia(id_usuario: int, payload: ConfigIaIn, admin: dict = Depends(exigir_admin)):
    """Define (ou substitui) a IA própria do usuário."""
    if obter_usuario(id_usuario) is None:
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")

    atual = config_ia_usuario.obter(id_usuario) or {}
    provider = sanitize(payload.provider).lower()
    modelo = sanitize(payload.model)
    api_url = sanitize(payload.apiUrl)
    esforco = sanitize(payload.reasoningEffort).lower()
    # Chave e certificado seguem a regra das Configurações: campo em branco mantém o que está
    # gravado (a tela nunca recebe nenhum dos dois de volta para poder reenviar).
    api_key = sanitize(payload.apiKey) or atual.get("tx_api_key", "")
    if payload.removerCaCert:
        ca_cert = ""
    elif (payload.caCert or "").strip():
        try:
            ca_cert = cert_utils.pem_para_env(cert_utils.normalizar_pem(payload.caCert))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Certificado inválido: {exc}.")
    else:
        ca_cert = atual.get("tx_ca_cert", "")

    try:
        config_ia_usuario.validar(provider, modelo, api_url, esforco, api_key, ca_cert)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    config_ia_usuario.salvar(
        id_usuario,
        ativa=payload.ativa,
        provedor=provider,
        modelo=modelo,
        api_url=api_url,
        api_key=api_key,
        ca_cert=ca_cert,
        esforco=esforco,
        contexto=payload.contextLength or None,
        id_autor=admin.get("id_usuario"),
    )
    registrar_auditoria_usuario(
        id_usuario_alvo=id_usuario,
        tp_acao="configurar_ia",
        detalhes={
            "ativa": payload.ativa,
            "provider": provider,
            "modelo": modelo,
            "api_url": api_url,
            "context_length": payload.contextLength,
            "reasoning_effort": esforco,
            "remover_ca_cert": payload.removerCaCert,
        },
    )
    return {"ok": True}


@router.delete("/api/usuarios/{id_usuario}/config-ia")
def apagar_config_ia(id_usuario: int):
    """Apaga a exceção: o usuário volta a usar a IA das Configurações."""
    if not config_ia_usuario.remover(id_usuario):
        raise HTTPException(status_code=404, detail="Este usuário não tem IA própria configurada.")
    registrar_auditoria_usuario(
        id_usuario_alvo=id_usuario,
        tp_acao="apagar_ia",
        detalhes={},
    )
    return {"ok": True}

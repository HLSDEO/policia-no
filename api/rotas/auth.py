"""Rotas de autenticação: login, logout, usuário logado, troca da própria senha e recuperação
de senha esquecida (ver o desenho em db/init/07_seguranca_usuarios.sql).

São duas formas de entrar, com um cadastro só (ver funcoes/api_pf_utils.py):

  POLICIA — a senha é conferida aqui (bcrypt em TX_SENHA_HASH). É o caminho principal: funciona
            sem rede da PF e é o único que existe para quem foi cadastrado neste sistema.
  API_PF  — a senha vai para o OpenID Connect da PF. No primeiro login o cadastro é criado aqui
            a partir das claims do token; a pessoa então cria a senha do PolícIA e escolhe a
            delegacia (POST /api/auth/primeiro-acesso), e passa a entrar pelos dois caminhos.

A escolha do provedor é do cliente, mas o que ele pode escolher vem de GET /api/auth/provedores.
"""

import logging

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from funcoes import api_pf_utils
from funcoes.auth_utils import (
    COOKIE_SESSAO,
    DURACAO_SESSAO,
    autenticar,
    chegou_https,
    criar_sessao,
    criar_token_redefinicao,
    criar_usuario,
    definir_email,
    definir_senha,
    definir_senha_inicial,
    definir_tema,
    encerrar_sessao,
    exigir_login,
    horas_validade_token,
    pedido_redefinicao_recente,
    redefinir_senha_com_token,
    registrar_tentativa_cadastro,
    registrar_tentativa_login,
    segundos_bloqueio_cadastro,
    segundos_bloqueio_login,
    sincronizar_usuario_api_pf,
    usuario_para_recuperacao,
    validar_token_redefinicao,
)
from funcoes.email_utils import enviar_redefinicao_senha, smtp_configurado

logger = logging.getLogger(__name__)

router = APIRouter()

# Provedores oferecidos na tela de login, nesta ordem. O PolícIA vem primeiro e é o padrão: é o
# caminho que sempre funciona (não depende de rede externa) e o único que existe para quem foi
# cadastrado aqui. Rótulo e descrição saem daqui — o front não tem essa lista fixa, para uma aba
# nunca aparecer oferecendo algo que a API não aceita.
PROVEDOR_LOCAL = "POLICIA"
PROVEDORES = [
    {
        "co_provedor": PROVEDOR_LOCAL,
        "no_provedor": "PolícIA",
        "ds_provedor": "Login e senha guardados neste servidor. Funciona sem acesso à rede da PF.",
    },
    {
        "co_provedor": api_pf_utils.CO_PROVEDOR,
        "no_provedor": "API-PF",
        "ds_provedor": "Credencial validada pela API da Polícia Federal; o cadastro no PolícIA é "
                       "criado a partir da resposta.",
    },
]


class LoginIn(BaseModel):
    login: str = Field(..., max_length=60)
    senha: str = Field(..., max_length=200)
    # Ausente = PolícIA: uma aba antiga em cache (ou um cliente que não conheça a escolha)
    # continua caindo no caminho principal em vez de quebrar.
    provedor: str = Field(default=PROVEDOR_LOCAL, max_length=20)


class PrimeiroAcessoIn(BaseModel):
    senha_nova: str = Field(..., max_length=200)
    # Sem id_delegacia de propósito: a delegacia é definida por um administrador na tela
    # Usuários (ver A2) — aceitar a autodeclaração aqui permitia escolher a unidade da vítima
    # e, pelo auto-join da extração, sequestrar o acesso aos inquéritos dela.


class CadastroIn(BaseModel):
    login: str = Field(..., max_length=60)
    nome: str = Field(..., max_length=150)
    senha: str = Field(..., max_length=200)
    email: str | None = Field(default=None, max_length=200)
    # Sem id_delegacia pelo mesmo motivo do PrimeiroAcessoIn acima.


class SenhaIn(BaseModel):
    senha_atual: str = Field(..., max_length=200)
    senha_nova: str = Field(..., max_length=200)


class EmailIn(BaseModel):
    senha_atual: str = Field(..., max_length=200)
    email: str | None = Field(default=None, max_length=200)


class TemaIn(BaseModel):
    tema: str = Field(..., max_length=10)


class WebhookIaIn(BaseModel):
    # URL vazia = remover o webhook (para de notificar). http(s) obrigatório — ver ia_webhook.validar_url.
    url: str = Field(default="", max_length=2000)
    ativo: bool = True


class EsqueciIn(BaseModel):
    login_ou_email: str = Field(..., max_length=200)


class RedefinirIn(BaseModel):
    token: str = Field(..., max_length=200)
    senha_nova: str = Field(..., max_length=200)


class ConferirTokenIn(BaseModel):
    token: str = Field(..., max_length=200)


def _abrir_sessao(response: Response, id_usuario: int, request: Request) -> None:
    token = criar_sessao(id_usuario)
    response.set_cookie(
        COOKIE_SESSAO, token,
        max_age=int(DURACAO_SESSAO.total_seconds()),
        httponly=True, samesite="lax", path="/",
        secure=chegou_https(request),
    )


def _ip_cliente(request: Request) -> str | None:
    """IP de origem da requisição, para a contagem de tentativas por IP.

    Vem do X-Real-IP posto pelo nginx (nginx/default.conf): sem isso, request.client.host seria
    sempre o IP do container web e todo mundo contaria como uma origem só. Confiar no cabeçalho é
    seguro AQUI porque a API não tem porta publicada no host — o único caminho até ela é o proxy,
    que sobrescreve o cabeçalho. Se um dia a API for exposta direto, isto vira spoofável.
    """
    return (request.headers.get("x-real-ip") or "").strip()[:45] or (
        request.client.host if request.client else None
    )


@router.get("/api/auth/provedores")
def provedores():
    """Opções da tela de login. Pública: é lida antes de existir qualquer sessão.

    Devolve só rótulo, descrição e o motivo de indisponibilidade — nenhuma URL, client_id ou
    segredo da integração. Um provedor sem condição de funcionar (API-PF sem URL configurada)
    vem com `ds_indisponivel` e a tela mostra a aba desabilitada com o motivo, em vez de sumir e
    deixar o usuário achando que a opção não existe.
    """
    lista = []
    for provedor in PROVEDORES:
        item = dict(provedor)
        item["ds_indisponivel"] = (
            api_pf_utils.indisponivel() if item["co_provedor"] == api_pf_utils.CO_PROVEDOR else None
        )
        lista.append(item)
    return {"provedores": lista}


def _autenticar_api_pf(login: str, senha: str) -> dict | None:
    """Valida na API-PF e devolve o usuário do PolícIA (criando/atualizando o cadastro).

    None é credencial inválida, igual ao caminho local. Falha da própria integração vira 502 com
    o motivo: não é palpite errado de senha, e tratá-la como tal deixaria uma indisponibilidade
    externa bloqueando contas legítimas pelo limite de tentativas.
    """
    try:
        identidade = api_pf_utils.autenticar(login, senha)
    except api_pf_utils.ErroApiPf as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if identidade is None:
        return None

    try:
        return sincronizar_usuario_api_pf(identidade)
    except (ValueError, RuntimeError) as exc:
        logger.warning("API-PF: falha ao sincronizar o cadastro de %s (%s)", login, exc)
        raise HTTPException(
            status_code=502,
            detail="Não foi possível criar seu cadastro no PolícIA a partir da resposta da API-PF.",
        ) from exc


@router.post("/api/auth/login")
def login(payload: LoginIn, request: Request, response: Response):
    """Confere as credenciais no provedor escolhido e abre uma sessão (cookie httpOnly).

    O bloqueio por tentativas é checado antes de conferir a senha (não gasta bcrypt — nem uma
    chamada à API-PF — com quem já estourou) e a mensagem pode ser específica sem vazar nada:
    ela só aparece para quem provocou as falhas, e diz apenas o que essa pessoa já sabe — que
    errou várias vezes. Continua sem distinguir login inexistente de senha errada, que é o que
    denunciaria quem tem conta.

    A contagem de tentativas é por login, comum aos dois provedores: quem erra cinco vezes na
    aba do PolícIA não ganha cinco palpites novos trocando de aba.
    """
    provedor = (payload.provedor or PROVEDOR_LOCAL).strip().upper()
    if provedor not in (PROVEDOR_LOCAL, api_pf_utils.CO_PROVEDOR):
        raise HTTPException(status_code=400, detail="Forma de autenticação indisponível.")

    ip = _ip_cliente(request)
    espera = segundos_bloqueio_login(payload.login, ip)
    if espera:
        minutos = max(1, round(espera / 60))
        raise HTTPException(
            status_code=429,
            detail=f"Muitas tentativas de login. Tente de novo em {minutos} min.",
            headers={"Retry-After": str(espera)},
        )

    if provedor == api_pf_utils.CO_PROVEDOR:
        usuario = _autenticar_api_pf(payload.login.strip(), payload.senha)
    else:
        usuario = autenticar(payload.login, payload.senha)

    registrar_tentativa_login(payload.login, ip, usuario is not None)
    if usuario is None:
        raise HTTPException(status_code=401, detail="Login ou senha inválidos.")

    # A sincronização não mexe em IN_ATIVO: quem foi desativado aqui não entra, mesmo que a PF
    # continue autenticando. (No caminho local, autenticar() já filtrou por in_ativo.)
    if not usuario.get("in_ativo", True):
        raise HTTPException(status_code=403, detail="Este usuário está desativado no sistema.")

    _abrir_sessao(response, usuario["id_usuario"], request)
    return {
        "ok": True,
        "no_usuario": usuario["no_usuario"],
        "in_admin": usuario["in_admin"],
        "in_aprovado": usuario["in_aprovado"],
        # Primeiro acesso de quem veio da API-PF: autenticado, mas ainda sem senha do PolícIA.
        # O front abre a caixa bloqueante de criação de senha + delegacia antes de entrar.
        "primeiro_acesso": bool(usuario.get("senha_local_pendente")),
    }


@router.post("/api/auth/primeiro-acesso")
def primeiro_acesso(payload: PrimeiroAcessoIn, usuario: dict = Depends(exigir_login)):
    """Cria a senha do PolícIA de quem acabou de entrar pela API-PF.

    Sem senha atual porque não existe uma — e por isso a rota só funciona enquanto o hash for
    NULL (a condição está no próprio UPDATE, ver definir_senha_inicial): senão ela seria um
    caminho para trocar a senha de qualquer conta sem conhecer a anterior.

    A delegacia NÃO é definida aqui: é um administrador quem vincula, na tela Usuários
    (ver A2). A sessão continua aberta: a pessoa está no meio do primeiro acesso, e derrubá-la
    aqui a mandaria de volta ao login no instante em que terminasse.
    """
    try:
        definir_senha_inicial(usuario["id_usuario"], payload.senha_nova, None)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except LookupError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"ok": True}


@router.post("/api/auth/cadastro")
def cadastro(payload: CadastroIn, request: Request, response: Response):
    """Autocadastro: cria o usuário (sem admin, sem aprovação, sem delegacia) e já abre
    sessão — o usuário consegue entrar, mas fica sem acesso às funcionalidades até um
    administrador aprovar E vincular a delegacia (ver exigir_aprovado e A2).

    Throttle por IP ANTES de tocar o banco de usuários: a mensagem "já existe esse login/
    e-mail" é verdadeira e útil para quem se cadastra de verdade, mas para um anônimo é um
    oracle de enumeração — sem limite, a rota entregava a lista de contas do sistema de
    graça (o login tinha bloqueio por tentativas; o cadastro não tinha nada)."""
    ip = _ip_cliente(request)
    espera = segundos_bloqueio_cadastro(ip)
    if espera:
        minutos = max(1, round(espera / 60))
        raise HTTPException(
            status_code=429,
            detail=f"Muitas tentativas de cadastro a partir desta rede. Tente de novo em {minutos} min.",
            headers={"Retry-After": str(espera)},
        )
    try:
        id_usuario = criar_usuario(
            payload.login, payload.nome, payload.senha, perfil="analista", aprovado=False, email=payload.email,
            id_delegacia=None,
        )
    except ValueError as exc:
        registrar_tentativa_cadastro(ip, False)  # foi palpite de login/e-mail duplicado (ou senha curta)
        raise HTTPException(status_code=400, detail=str(exc))
    registrar_tentativa_cadastro(ip, True)
    _abrir_sessao(response, id_usuario, request)
    return {"ok": True, "no_usuario": payload.nome, "in_admin": False, "in_aprovado": False}


@router.post("/api/auth/logout")
def logout(response: Response, analyser_sessao: str | None = Cookie(default=None)):
    """Encerra a sessão atual (revoga o token no banco e limpa o cookie)."""
    encerrar_sessao(analyser_sessao)
    response.delete_cookie(COOKIE_SESSAO, path="/")
    return {"ok": True}


@router.get("/api/auth/eu")
def eu(usuario: dict = Depends(exigir_login)):
    """Devolve o usuário da sessão atual — o frontend usa isso pra checar se está logado."""
    return usuario


@router.post("/api/auth/tema")
def tema(payload: TemaIn, usuario: dict = Depends(exigir_login)):
    """Salva a preferência de tema (claro/escuro) do usuário logado, pra seguir ele entre páginas
    e dispositivos em vez de ficar só em localStorage."""
    try:
        definir_tema(usuario["id_usuario"], payload.tema)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


def _confirmar_senha_atual(usuario: dict, senha_atual: str, request: Request) -> None:
    """Confirma a senha atual aplicando o MESMO bloqueio por tentativas do login.

    Sem o throttle, uma sessão sequestrada mas ociosa poderia martelar a senha atual sem limite
    algum — e acertá-la em /api/auth/email equivale a tomar a conta para sempre (o e-mail é a
    chave da recuperação)."""
    ip = _ip_cliente(request)
    espera = segundos_bloqueio_login(usuario["no_login"], ip)
    if espera:
        minutos = max(1, round(espera / 60))
        raise HTTPException(
            status_code=429,
            detail=f"Muitas tentativas. Tente de novo em {minutos} min.",
            headers={"Retry-After": str(espera)},
        )
    ok = autenticar(usuario["no_login"], senha_atual) is not None
    registrar_tentativa_login(usuario["no_login"], ip, ok)
    if not ok:
        raise HTTPException(status_code=401, detail="Senha atual incorreta.")


@router.post("/api/auth/senha")
def trocar_senha(payload: SenhaIn, request: Request, response: Response, usuario: dict = Depends(exigir_login)):
    """Troca a senha do próprio usuário logado, exigindo a senha atual.

    A troca encerra todas as sessões do usuário (inclusive esta) e emite um identificador de sessão
    novo no mesmo response: o cookie entregue antes da troca deixa de existir no servidor e não
    abre mais nada, nem nas mãos de quem o tenha interceptado.
    """
    _confirmar_senha_atual(usuario, payload.senha_atual, request)
    try:
        definir_senha(usuario["id_usuario"], payload.senha_nova)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    _abrir_sessao(response, usuario["id_usuario"], request)
    return {"ok": True}


@router.post("/api/auth/email")
def trocar_email(payload: EmailIn, request: Request, usuario: dict = Depends(exigir_login)):
    """Grava o e-mail de recuperação do próprio usuário logado.

    Exige a senha atual pelo mesmo motivo da troca de senha: o e-mail é o que permite recuperar a
    conta, então trocá-lo é equivalente a trocar a chave — sem essa exigência, uma sessão
    sequestrada viraria um roubo permanente da conta.
    """
    _confirmar_senha_atual(usuario, payload.senha_atual, request)
    try:
        definir_email(usuario["id_usuario"], payload.email)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


@router.get("/api/auth/recuperacao")
def recuperacao_disponivel():
    """Diz se o envio por e-mail está disponível nesta instalação, para a tela de login não
    oferecer "esqueci minha senha" quando não há SMTP e o único caminho é falar com o administrador."""
    return {"email": smtp_configurado()}


@router.post("/api/auth/esqueci")
def esqueci_senha(payload: EsqueciIn):
    """Pede um link de redefinição por e-mail (rota pública).

    Responde SEMPRE a mesma coisa, com ou sem sucesso: login inexistente, usuário sem e-mail
    cadastrado, SMTP fora do ar, pedido repetido. Um retorno que distinguisse esses casos
    transformaria a rota em um verificador de quem tem conta (e de qual é o e-mail dela).
    """
    generico = {
        "ok": True,
        "mensagem": "Se houver uma conta com esse login ou e-mail e um e-mail cadastrado, "
                    "o link de redefinição foi enviado.",
    }
    usuario = usuario_para_recuperacao(payload.login_ou_email)
    if usuario is None or not usuario["no_email"]:
        return generico
    if pedido_redefinicao_recente(usuario["id_usuario"]):
        return generico
    if not smtp_configurado():
        return generico

    token, _ = criar_token_redefinicao(usuario["id_usuario"], "email")
    enviar_redefinicao_senha(
        usuario["no_email"], usuario["no_usuario"], token, horas_validade_token("email")
    )
    return generico


@router.post("/api/auth/redefinir/conferir")
def conferir_token(payload: ConferirTokenIn):
    """Confere se um link/código de redefinição ainda vale, sem consumi-lo — a tela chama isso ao
    abrir para já avisar que o link expirou em vez de deixar o usuário digitar a senha em vão.

    POST com o token no CORPO (era GET /api/auth/redefinir/{token}): o token é a credencial
    única para tomar a conta, e em GET ele ficava registrado no access_log do uvicorn — o
    mesmo argumento que motivou o link em fragmento (ver email_utils.link_redefinicao)."""
    usuario = validar_token_redefinicao(payload.token)
    if usuario is None:
        raise HTTPException(status_code=404, detail="Link de redefinição inválido ou expirado.")
    return {"ok": True, "no_usuario": usuario["no_usuario"], "no_login": usuario["no_login"]}


@router.post("/api/auth/redefinir")
def redefinir(payload: RedefinirIn):
    """Define a senha nova a partir de um token de redefinição (rota pública — é justamente para
    quem não consegue logar). Encerra as outras sessões do usuário; ver redefinir_senha_com_token."""
    try:
        trocou = redefinir_senha_com_token(payload.token, payload.senha_nova)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not trocou:
        raise HTTPException(status_code=404, detail="Link de redefinição inválido ou expirado.")
    return {"ok": True}


# =====================================================================
# Webhook de conclusão da IA — self-service ("Meu webhook IA" no menu)
#
# Cada usuário cadastra a SUA url (Discord/Slack/Teams/n8n/...) e passa a receber um POST JSON
# de aviso simples sempre que um turno da IA termina (concluído/erro/cancelado) nos chats que
# lhe dizem respeito: pergunta que ele enviou ou conversa que ele criou. Vale para todos os
# chats (ia.html, painel flutuante e IA do caso — ver funcoes/ia_webhook.py).
# =====================================================================

@router.get("/api/auth/webhook-ia")
def obter_webhook_ia(usuario: dict = Depends(exigir_login)):
    """Webhook de IA do próprio usuário logado. URL cheia (é dado dele, via sessão autenticada)."""
    from funcoes import ia_webhook

    cfg = ia_webhook.obter(usuario["id_usuario"])
    if not cfg:
        return {"definido": False, "ativo": True, "url": ""}
    return {"definido": True, "ativo": cfg["ativo"], "url": cfg["url"]}


@router.put("/api/auth/webhook-ia")
def salvar_webhook_ia(payload: WebhookIaIn, usuario: dict = Depends(exigir_login)):
    """Cria/substitui o webhook de IA do próprio usuário. URL vazia = remover (para de notificar)."""
    from funcoes import ia_webhook

    try:
        ia_webhook.salvar(usuario["id_usuario"], payload.url, payload.ativo)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


@router.post("/api/auth/webhook-ia/teste")
def testar_webhook_ia(usuario: dict = Depends(exigir_login)):
    """Envia um evento de teste ao webhook cadastrado — resposta na hora (ok/http_status/erro)."""
    from funcoes import ia_webhook

    try:
        return ia_webhook.enviar_teste(usuario["id_usuario"])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# =====================================================================
# Avisos in-app de conclusão da IA — o sino que aparece em qualquer tela
#
# O webhook acima só entrega FORA do sistema (Discord/Slack/n8n) e exige cadastro. Este endpoint
# é o aviso dentro do próprio sistema: toda página faz polling curto aqui e mostra o toast (e a
# notificação do navegador, se autorizada) quando termina um turno da IA em que o usuário logado
# é o solicitante ou o criador da conversa — mesmo que ele já tenha saído do chat.
# Ver funcoes/ia_avisos.py e web/js/utils/avisos_ia.js.
# =====================================================================

@router.get("/api/auth/avisos-ia")
def avisos_ia(desde: str | None = None, usuario: dict = Depends(exigir_login)):
    """Turnos de IA terminados desde `desde` (o `agora` da resposta anterior) para o usuário logado."""
    from funcoes import ia_avisos

    return ia_avisos.listar(usuario["id_usuario"], desde)

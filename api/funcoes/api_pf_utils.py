"""Provedor de autenticação API-PF: a senha vai para o OpenID Connect da Polícia Federal.

A senha digitada é mandada para o endpoint de token da PF (Keycloak), que responde com um
access token JWT. Nome, e-mail e login saem das claims desse token — **nada disso é senha, e
nenhuma senha da PF fica neste servidor** (`TX_SENHA_HASH` continua NULL para quem entrou por
aqui até a pessoa criar a senha do PolícIA no primeiro acesso).

O login do PolícIA é que manda: a API-PF só responde *"esta credencial é válida e de quem é?"*.
Quem abre sessão, decide aprovação, delegacia e privilégio de administrador é
`funcoes/auth_utils.py`, igual para os dois caminhos de entrada.

CONTRATO
--------

    POST $API_PF_URL
    Content-Type: application/x-www-form-urlencoded

    client_id      = $API_PF_CLIENT_ID
    client_secret  = $API_PF_CLIENT_SECRET
    grant_type     = $API_PF_GRANT_TYPE       (password)
    $API_PF_CAMPO_USUARIO = login digitado    (username)
    $API_PF_CAMPO_SENHA   = senha digitada    (password)

    200 → {"access_token": "<JWT>", "expires_in": ..., "token_type": "Bearer", ...}
    400/401/403 → credencial inválida (a tela mostra "login ou senha inválidos")
    demais / timeout / erro de rede → ErroApiPf → 502 com o motivo

Toda essa configuração é editável em **Administração → API-PF** e mora no `.env` deste servidor
(`CAMPOS`, abaixo, é o que monta aquele formulário). Os valores são lidos do arquivo a cada
tentativa — não de `os.environ` —, para uma mudança na tela valer no login seguinte sem
reiniciar o container.

SOBRE NÃO CONFERIR A ASSINATURA DO JWT
--------------------------------------
O token é lido só para extrair login, nome e e-mail, sem validar a assinatura. Isso é seguro
**neste fluxo específico** porque nós somos o cliente: o token acabou de chegar por TLS, numa
resposta à nossa própria requisição ao servidor de autorização, e a prova de que a senha estava
certa é o próprio 200. Não há terceiro no meio para forjá-lo.

Mudaria se o token passasse a chegar de outro lugar (do navegador, de um header `Authorization`
que a API recebe): aí a assinatura vira a única garantia, e o caminho é buscar o JWKS do realm
(`/protocol/openid-connect/certs`) e conferir assinatura, `iss`, `aud` e `exp` antes de confiar
em qualquer claim.
"""

import base64
import binascii
import json
import logging
from dataclasses import dataclass

import requests
import urllib3

from funcoes.env_utils import parse_env, read_env_text

logger = logging.getLogger(__name__)

# Código do provedor em USR_TB01_USUARIOS.CO_PROVEDOR (ver db/init/35_autenticacao_api_pf.sql).
CO_PROVEDOR = "API_PF"

# Respostas que significam "essa credencial não serve". O padrão OAuth devolve 401; 400
# (invalid_grant) e 403 entram junto porque significam a mesma coisa para quem está na tela e
# não valem um erro de infraestrutura.
STATUS_CREDENCIAL_INVALIDA = (400, 401, 403)

TIPO_TEXTO = "texto"
TIPO_SENHA = "senha"
TIPO_URL = "url"
TIPO_NUMERO = "numero"
TIPO_BOOLEANO = "booleano"  # select Sim/Não, gravado como "sim"/"nao"

# Catálogo dos campos: é a descrição da integração, e a tela Administração → API-PF é montada a
# partir daqui (um campo de formulário por entrada). Os valores moram no `.env`.
#
# Campo com `secreto: True` nunca sai daqui em texto claro: a leitura devolve máscara, e a
# gravação entende "veio vazio" como "mantenha o que já está lá" — senão salvar o formulário sem
# redigitar o segredo o apagaria.
CAMPOS = [
    {
        "chave": "API_PF_URL",
        "rotulo": "URL do endpoint (POST)",
        "tipo": TIPO_URL,
        "padrao": "",
        "obrigatorio": True,
        "ajuda": "Ex.: https://sistemas.dpf.gov.br/auth/realms/sistemasDPF/protocol/openid-connect/token",
    },
    {
        "chave": "API_PF_CLIENT_ID",
        "rotulo": "client_id",
        "tipo": TIPO_TEXTO,
        "padrao": "",
        "ajuda": "Enviado no corpo da requisição. Vazio não é enviado (cliente público, sem segredo).",
    },
    {
        "chave": "API_PF_CLIENT_SECRET",
        "rotulo": "client_secret",
        "tipo": TIPO_SENHA,
        "padrao": "",
        "secreto": True,
        "ajuda": "Fica só no .env deste servidor. Deixe vazio ao salvar para manter o valor atual.",
    },
    {
        "chave": "API_PF_GRANT_TYPE",
        "rotulo": "grant_type",
        "tipo": TIPO_TEXTO,
        "padrao": "password",
        "ajuda": "O fluxo de senha do OAuth 2.0 é o único que aceita as credenciais digitadas na tela.",
    },
    {
        "chave": "API_PF_CAMPO_USUARIO",
        "rotulo": "Campo do login no corpo",
        "tipo": TIPO_TEXTO,
        "padrao": "username",
        "ajuda": "Nome do campo que recebe o login digitado pelo usuário.",
    },
    {
        "chave": "API_PF_CAMPO_SENHA",
        "rotulo": "Campo da senha no corpo",
        "tipo": TIPO_TEXTO,
        "padrao": "password",
        "ajuda": "Nome do campo que recebe a senha digitada pelo usuário.",
    },
    {
        "chave": "API_PF_TIMEOUT",
        "rotulo": "Timeout (segundos)",
        "tipo": TIPO_NUMERO,
        "padrao": "10",
        "ajuda": "A tela de login fica esperando por esta chamada; um valor alto trava o usuário.",
    },
    {
        "chave": "API_PF_VERIFICAR_TLS",
        "rotulo": "Conferir o certificado TLS",
        "tipo": TIPO_BOOLEANO,
        "padrao": "sim",
        "ajuda": (
            "Desligar destrava a integração quando a cadeia da AC da PF não é reconhecida pelo "
            "container, mas a conexão deixa de provar que do outro lado está mesmo o servidor da PF."
        ),
    },
    {
        "chave": "API_PF_CLAIM_LOGIN",
        "rotulo": "Claim do login",
        "tipo": TIPO_TEXTO,
        "padrao": "preferred_username",
        "ajuda": "Claim do access token usada como login do cadastro no PolícIA.",
    },
    {
        "chave": "API_PF_CLAIM_NOME",
        "rotulo": "Claim do nome",
        "tipo": TIPO_TEXTO,
        "padrao": "name",
        "ajuda": "Sem nome não dá para montar um cadastro utilizável, então o login falha se ela vier vazia.",
    },
    {
        "chave": "API_PF_CLAIM_EMAIL",
        "rotulo": "Claim do e-mail",
        "tipo": TIPO_TEXTO,
        "padrao": "email",
        "ajuda": "Opcional. Serve para a recuperação de senha do PolícIA; e-mail de outro usuário é ignorado.",
    },
]

_PADROES = {campo["chave"]: campo.get("padrao", "") for campo in CAMPOS}


class ErroApiPf(Exception):
    """Falha que o usuário precisa ver na tela.

    Não é "senha errada" — isso é `None` no retorno de `autenticar()`, e a mensagem genérica de
    credencial inválida sai de quem chamou. É a API sem condição de responder: fora do ar, URL
    não configurada, resposta em formato inesperado. A mensagem chega ao usuário, então descreve
    o problema sem citar host, token ou corpo da resposta.
    """


@dataclass(frozen=True)
class IdentidadeApiPf:
    """Quem é a pessoa, segundo a API-PF que acabou de validá-la."""

    no_login: str
    no_usuario: str
    no_email: str | None = None


# ---------------------------------------------------------------------------
# Configuração (.env)
# ---------------------------------------------------------------------------

def ler(chave: str, padrao: str | None = None) -> str:
    """Valor de uma chave da integração, relido do `.env` a cada chamada.

    Não usa `os.environ`: o compose copia o `.env` para o ambiente do processo **uma vez**, na
    subida do container, então um valor salvo pela tela só valeria depois de um `docker compose
    up`. Lendo o arquivo, a mudança vale no login seguinte.
    """
    if padrao is None:
        padrao = _PADROES.get(chave, "")
    valor = (parse_env(read_env_text()).get(chave) or "").strip()
    return valor or padrao


def ler_int(chave: str, padrao: int) -> int:
    try:
        return int(float(ler(chave, str(padrao))))
    except (TypeError, ValueError):
        return padrao


def ler_bool(chave: str, padrao: bool = True) -> bool:
    valor = ler(chave, "sim" if padrao else "nao").strip().lower()
    return valor not in ("nao", "não", "n", "0", "false")


def configurado() -> bool:
    """A integração tem o mínimo para ser tentada (só a URL é obrigatória)."""
    return bool(ler("API_PF_URL"))


def indisponivel() -> str | None:
    """Motivo pelo qual a API-PF não pode ser usada agora, ou `None` se está pronta.

    A tela de login usa isto para mostrar a aba desabilitada com a explicação, em vez de deixar
    o usuário digitar a senha e só então receber erro.
    """
    if not configurado():
        return "A entrada pela API-PF ainda não foi configurada neste servidor (Administração → API-PF)."
    return None


# ---------------------------------------------------------------------------
# Leitura do token
# ---------------------------------------------------------------------------

def payload_jwt(token: str) -> dict:
    """Claims do JWT, sem conferir a assinatura (ver o cabeçalho do módulo)."""
    partes = token.split(".")
    if len(partes) != 3:
        raise ErroApiPf("A API-PF devolveu um token em formato inesperado.")

    corpo = partes[1]
    # base64url do JWT vem sem o padding "=" — repô-lo é obrigatório para o decode não estourar.
    corpo += "=" * (-len(corpo) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(corpo))
    except (binascii.Error, ValueError, UnicodeDecodeError) as erro:
        raise ErroApiPf("A API-PF devolveu um token que não pôde ser lido.") from erro

    if not isinstance(claims, dict):
        raise ErroApiPf("A API-PF devolveu um token em formato inesperado.")
    return claims


def claim_texto(claims: dict, caminho: str | None) -> str | None:
    """Valor de uma claim. Aceita caminho com ponto (`resource_access.epol-server.roles`).

    Lista vira o primeiro item: claims desse tipo costumam ser listas e aqui se quer um valor só.
    """
    if not caminho:
        return None

    valor = claims
    for parte in caminho.split("."):
        if not isinstance(valor, dict) or parte not in valor:
            return None
        valor = valor[parte]

    if isinstance(valor, list):
        valor = valor[0] if valor else None
    if valor is None or isinstance(valor, (dict, list)):
        return None

    texto = str(valor).strip()
    return texto or None


# ---------------------------------------------------------------------------
# Autenticação
# ---------------------------------------------------------------------------

def _corpo(login: str, senha: str) -> dict[str, str]:
    corpo: dict[str, str] = {}

    # client_id/client_secret vazios não são enviados: há instalações em que o cliente é público
    # (sem segredo), e mandar o campo em branco faz o Keycloak recusar.
    client_id = ler("API_PF_CLIENT_ID")
    client_secret = ler("API_PF_CLIENT_SECRET")
    if client_id:
        corpo["client_id"] = client_id
    if client_secret:
        corpo["client_secret"] = client_secret

    corpo["grant_type"] = ler("API_PF_GRANT_TYPE")
    corpo[ler("API_PF_CAMPO_USUARIO")] = login
    corpo[ler("API_PF_CAMPO_SENHA")] = senha
    return corpo


def autenticar(login: str, senha: str) -> IdentidadeApiPf | None:
    """`None` para credencial inválida; `ErroApiPf` para falha da própria integração."""
    url = ler("API_PF_URL")
    if not url:
        raise ErroApiPf("A entrada pela API-PF ainda não foi configurada neste servidor.")

    verificar_tls = ler_bool("API_PF_VERIFICAR_TLS", True)
    if not verificar_tls:
        # Sem isto o urllib3 imprime um InsecureRequestWarning por requisição e o log de produção
        # vira um muro de avisos iguais. O aviso que interessa é o do logger abaixo: uma linha por
        # chamada, dizendo qual URL está sem conferência.
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        logger.warning("API-PF: certificado NAO conferido (API_PF_VERIFICAR_TLS=nao) em %s", url)

    try:
        resposta = requests.post(
            url,
            data=_corpo(login, senha),  # requests codifica como x-www-form-urlencoded
            timeout=ler_int("API_PF_TIMEOUT", 10),
            headers={"Accept": "application/json"},
            verify=verificar_tls,
        )
    except requests.Timeout as erro:
        logger.warning("API-PF: timeout em %s", url)
        raise ErroApiPf("A API-PF não respondeu a tempo. Tente novamente.") from erro
    except requests.exceptions.SSLError as erro:
        # Separado do erro de rede genérico porque a saída é outra: não é "a rede caiu", é "o
        # certificado não foi aceito" — e a mensagem precisa apontar para o ajuste certo, senão o
        # administrador vai procurar problema de conectividade que não existe.
        logger.warning("API-PF: falha de TLS em %s (%s)", url, erro)
        raise ErroApiPf(
            "O certificado da API-PF não foi aceito. Em Administração → API-PF, desligue a "
            "conferência de TLS ou instale a cadeia da autoridade certificadora no servidor."
        ) from erro
    except requests.RequestException as erro:
        logger.warning("API-PF: falha de rede em %s (%s)", url, erro)
        raise ErroApiPf("Não foi possível falar com a API-PF. Verifique a conexão.") from erro

    if resposta.status_code in STATUS_CREDENCIAL_INVALIDA:
        return None

    if resposta.status_code != 200:
        logger.warning("API-PF: HTTP %s em %s", resposta.status_code, url)
        raise ErroApiPf(f"A API-PF respondeu HTTP {resposta.status_code}. Procure o administrador.")

    try:
        corpo = resposta.json()
    except ValueError as erro:
        raise ErroApiPf("A API-PF respondeu em formato inesperado.") from erro

    token = corpo.get("access_token") if isinstance(corpo, dict) else None
    if not token:
        # 200 sem token não é credencial inválida: é a integração apontando para o lugar errado.
        # Falhar visível é melhor que virar "login ou senha inválidos" para o usuário.
        logger.warning("API-PF: HTTP 200 sem access_token")
        raise ErroApiPf("A API-PF não devolveu o token de acesso.")

    return _identidade(payload_jwt(token), login)


def _identidade(claims: dict, login_digitado: str) -> IdentidadeApiPf:
    no_login = claim_texto(claims, ler("API_PF_CLAIM_LOGIN"))
    no_usuario = claim_texto(claims, ler("API_PF_CLAIM_NOME"))
    no_email = claim_texto(claims, ler("API_PF_CLAIM_EMAIL"))

    if not no_usuario:
        # Sem nome não dá para montar um cadastro utilizável, e usar o login no lugar encheria a
        # tela Usuários de linhas indistinguíveis. Melhor falhar visível.
        logger.warning("API-PF: token sem a claim de nome (claims: %s)", sorted(claims))
        raise ErroApiPf(
            "A API-PF não devolveu o nome do usuário. Confira a claim do nome em "
            "Administração → API-PF."
        )

    return IdentidadeApiPf(
        no_login=no_login or login_digitado,
        no_usuario=no_usuario,
        no_email=no_email,
    )

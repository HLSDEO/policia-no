"""Envio de e-mail por SMTP — usado só pela recuperação de senha.

A configuração vem do .env (mesmo padrão de db_utils: lê o arquivo a cada uso, para uma mudança
no .env valer sem reiniciar o container). SMTP é OPCIONAL: numa instalação fechada, sem relay
alcançável, `smtp_configurado()` devolve False e a recuperação de senha funciona só pelo caminho
do administrador — nada aqui deve derrubar uma rota por estar sem configuração.
"""

import smtplib
import ssl
from email.message import EmailMessage

from funcoes.env_utils import parse_env, read_env_text

TIMEOUT_SMTP = 15


def _config() -> dict:
    """Lê a configuração de SMTP do .env, já com os defaults aplicados."""
    env = parse_env(read_env_text())
    host = env.get("SMTP_HOST", "").strip()
    seguranca = (env.get("SMTP_SEGURANCA", "") or "starttls").strip().lower()
    if seguranca not in ("starttls", "ssl", "nenhuma"):
        seguranca = "starttls"
    # porta padrão conforme o modo: 465 é o SMTP sobre SSL, 587 o de submissão com STARTTLS
    porta_padrao = "465" if seguranca == "ssl" else "587"
    try:
        porta = int(env.get("SMTP_PORT", "").strip() or porta_padrao)
    except ValueError:
        porta = int(porta_padrao)
    usuario = env.get("SMTP_USUARIO", "").strip()
    return {
        "host": host,
        "porta": porta,
        "usuario": usuario,
        "senha": env.get("SMTP_SENHA", ""),
        "seguranca": seguranca,
        # sem remetente explícito, cai no usuário da autenticação (o mais comum em relay autenticado)
        "remetente": env.get("SMTP_REMETENTE", "").strip() or usuario,
        "app_url": env.get("APP_URL", "").strip().rstrip("/"),
    }


def smtp_configurado() -> bool:
    """Diz se há SMTP utilizável — host e remetente são o mínimo para conseguir enviar."""
    cfg = _config()
    return bool(cfg["host"] and cfg["remetente"])


def url_base() -> str:
    """Endereço público do sistema (APP_URL), usado para montar o link de redefinição.

    Devolve string vazia se não estiver configurado; nesse caso quem chama mostra/envia o código
    para o usuário colar à mão na tela de redefinição, em vez de um link clicável.
    """
    return _config()["app_url"]


def link_redefinicao(token: str) -> str:
    """Monta a URL completa da tela de redefinição; devolve "" se APP_URL não estiver configurado.

    O token vai em FRAGMENTO (#token=), não em query string (?token=): fragmento nunca é
    enviado ao servidor, então o link para tomar conta da conta não fica registrado no
    access_log do nginx nem no log de acesso do uvicorn — que é o que acontecia com a query
    string, esvaziando o cuidado de guardar só o HASH do token no banco. A tela lê o
    fragmento e confere o token por POST (ver web/js/redefinir_senha.js)."""
    base = url_base()
    return f"{base}/redefinir_senha.html#token={token}" if base else ""


def enviar_email(destino: str, assunto: str, corpo: str) -> bool:
    """Envia um e-mail de texto simples. Devolve True se o servidor aceitou, False em qualquer
    falha (sem configuração, host inalcançável, autenticação recusada).

    Engole a exceção de propósito: quem chama é a rota pública de "esqueci minha senha", que
    responde a mesma coisa com ou sem sucesso — deixar o erro subir transformaria o formulário
    em um jeito de descobrir quais logins existem.
    """
    cfg = _config()
    if not cfg["host"] or not cfg["remetente"]:
        return False

    msg = EmailMessage()
    msg["From"] = cfg["remetente"]
    msg["To"] = destino
    msg["Subject"] = assunto
    msg.set_content(corpo)

    try:
        if cfg["seguranca"] == "ssl":
            servidor = smtplib.SMTP_SSL(
                cfg["host"], cfg["porta"], timeout=TIMEOUT_SMTP, context=ssl.create_default_context()
            )
        else:
            servidor = smtplib.SMTP(cfg["host"], cfg["porta"], timeout=TIMEOUT_SMTP)
        with servidor:
            if cfg["seguranca"] == "starttls":
                servidor.starttls(context=ssl.create_default_context())
            # relay interno costuma aceitar sem autenticação; só tenta login se houver usuário
            if cfg["usuario"]:
                servidor.login(cfg["usuario"], cfg["senha"])
            servidor.send_message(msg)
        return True
    except (OSError, smtplib.SMTPException):
        return False


def enviar_redefinicao_senha(destino: str, nome: str, token: str, validade_horas: int) -> bool:
    """Manda o e-mail de recuperação de senha, com link se APP_URL estiver configurado ou com o
    código para colar à mão se não estiver.

    validade_horas vem de quem criou o token (a validade difere entre origem 'email' e 'admin'),
    para o texto não prometer um prazo diferente do que o banco vai aceitar.
    """
    link = link_redefinicao(token)
    if link:
        instrucao = f"Para definir uma senha nova, acesse:\n\n{link}\n"
    else:
        instrucao = (
            "Para definir uma senha nova, abra a tela de redefinição de senha do sistema "
            f"e informe o código abaixo:\n\n{token}\n"
        )
    prazo = "1 hora" if validade_horas == 1 else f"{validade_horas} horas"

    corpo = (
        f"Olá, {nome}.\n\n"
        "Recebemos um pedido de redefinição de senha para a sua conta no PolícIA.\n\n"
        f"{instrucao}\n"
        f"O link vale por {prazo} e só pode ser usado uma vez.\n\n"
        "Se não foi você que pediu, ignore esta mensagem — sua senha atual continua valendo.\n"
    )
    return enviar_email(destino, "Redefinição de senha · PolícIA", corpo)

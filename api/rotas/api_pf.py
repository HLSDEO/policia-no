"""Administração → API-PF: configuração da entrada pela API da Polícia Federal.

Todas as rotas exigem administrador — a proteção está no próprio router, para não haver rota
nova nascendo desprotegida por esquecimento. É o cuidado mínimo aqui: estas rotas leem e
escrevem credenciais de integração no `.env` do servidor.

O catálogo dos campos (rótulo, tipo, ajuda, padrão) vive em `funcoes/api_pf_utils.py`; a tela
`web/api_pf.html` é montada a partir dele, então um campo novo aparece no formulário sem tocar
no front nem aqui.
"""

import logging
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from funcoes import api_pf_utils
from funcoes.auth_utils import exigir_admin
from funcoes.env_utils import parse_env, read_env_text, read_modify_write_env, sanitize, upsert_env_values, write_env_text

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(exigir_admin)])

VALORES_BOOLEANOS = ("sim", "nao")


class SalvarIn(BaseModel):
    """Valores a gravar, por chave do `.env`.

    Chave de campo secreto **ausente** (ou vazia) significa "mantenha o que já está lá" — é o que
    permite salvar o formulário sem redigitar o client_secret.
    """

    valores: dict[str, str] = Field(default_factory=dict)


class TestarIn(BaseModel):
    login: str = Field(..., min_length=1, max_length=120)
    senha: str = Field(..., min_length=1, max_length=200)


def _mascarar(valor: str) -> str:
    """Pista suficiente para reconhecer o segredo, curta demais para reconstruí-lo."""
    if not valor:
        return ""
    if len(valor) <= 6:
        return "•" * len(valor)
    return "•" * 8 + valor[-4:]


def _campo_publico(campo: dict, env: dict) -> dict:
    """Um campo do catálogo com o valor atual — o segredo em texto claro nunca sai daqui."""
    bruto = (env.get(campo["chave"]) or "").strip() or campo.get("padrao", "")
    publico = {
        "chave": campo["chave"],
        "rotulo": campo["rotulo"],
        "tipo": campo["tipo"],
        "ajuda": campo.get("ajuda", ""),
        "obrigatorio": campo.get("obrigatorio", False),
        "secreto": campo.get("secreto", False),
        "padrao": campo.get("padrao", ""),
    }
    if publico["secreto"]:
        publico["valor"] = ""
        publico["preenchido"] = bool(bruto)
        publico["mascara"] = _mascarar(bruto)
    else:
        publico["valor"] = bruto
    return publico


def _resposta() -> dict:
    env = parse_env(read_env_text())
    return {
        "configurado": api_pf_utils.configurado(),
        "campos": [_campo_publico(campo, env) for campo in api_pf_utils.CAMPOS],
    }


def _validar(campo: dict, valor: str) -> str:
    """Confere o valor de um campo antes de gravar; devolve o valor já normalizado."""
    valor = sanitize(valor)

    if campo.get("obrigatorio") and not valor:
        raise HTTPException(status_code=400, detail=f'O campo "{campo["rotulo"]}" é obrigatório.')

    if not valor:
        return valor

    if campo["tipo"] == api_pf_utils.TIPO_URL and not re.match(r"^https?://", valor, re.IGNORECASE):
        raise HTTPException(
            status_code=400,
            detail=f'O campo "{campo["rotulo"]}" deve começar com http:// ou https://.',
        )

    if campo["tipo"] == api_pf_utils.TIPO_NUMERO:
        try:
            numero = int(float(valor))
        except ValueError:
            raise HTTPException(status_code=400, detail=f'O campo "{campo["rotulo"]}" deve ser um número.')
        if numero < 1 or numero > 300:
            raise HTTPException(
                status_code=400,
                detail=f'O campo "{campo["rotulo"]}" deve ficar entre 1 e 300 segundos.',
            )
        return str(numero)

    if campo["tipo"] == api_pf_utils.TIPO_BOOLEANO and valor.lower() not in VALORES_BOOLEANOS:
        raise HTTPException(status_code=400, detail=f'O campo "{campo["rotulo"]}" só aceita sim ou não.')

    return valor.lower() if campo["tipo"] == api_pf_utils.TIPO_BOOLEANO else valor


@router.get("/api/config/api-pf")
def obter_configuracao():
    """Catálogo dos campos com os valores atuais — é o que monta o formulário da tela."""
    return _resposta()


@router.put("/api/config/api-pf")
def salvar_configuracao(payload: SalvarIn):
    alteracoes: dict[str, str] = {}

    for campo in api_pf_utils.CAMPOS:
        chave = campo["chave"]
        if chave not in payload.valores:
            continue

        valor = _validar(campo, payload.valores[chave])

        # Segredo em branco mantém o que já está gravado: a tela nunca recebe o valor em claro,
        # então "veio vazio" quer dizer "não mexi neste campo", não "apague". Para apagar de
        # verdade, o caminho é editar o .env do servidor.
        if campo.get("secreto") and not valor:
            continue

        alteracoes[chave] = valor

    if not alteracoes:
        return _resposta()

    try:
        read_modify_write_env(alteracoes)  # atômico: não perde write concorrente (ver C9)
    except OSError as exc:
        logger.error("falha ao gravar o .env: %s", exc)
        raise HTTPException(status_code=500, detail="Falha ao gravar o arquivo .env.")

    return _resposta()


@router.post("/api/config/api-pf/testar")
def testar_configuracao(payload: TestarIn):
    """Experimenta a integração com um login e uma senha, sem abrir sessão nem criar usuário.

    Serve para o administrador conferir a configuração na hora, em vez de descobrir que ela está
    errada quando alguém não conseguir entrar. A tentativa **não** entra em
    `USR_TB04_TENTATIVAS_LOGIN`: quem chamou já está autenticado, e contá-la deixaria o
    administrador capaz de bloquear a conta de outra pessoa a partir desta tela.
    """
    if not api_pf_utils.configurado():
        raise HTTPException(status_code=400, detail="Informe e salve a URL do endpoint antes de testar.")

    try:
        identidade = api_pf_utils.autenticar(payload.login.strip(), payload.senha)
    except api_pf_utils.ErroApiPf as exc:
        return {"ok": False, "motivo": "falha", "detalhe": str(exc)}

    if identidade is None:
        return {
            "ok": False,
            "motivo": "credencial",
            "detalhe": "A API-PF respondeu que o login ou a senha estão inválidos.",
        }

    return {
        "ok": True,
        "identidade": {
            "no_login": identidade.no_login,
            "no_usuario": identidade.no_usuario,
            "no_email": identidade.no_email,
        },
    }

"""Certificado de CA próprio para APIs de IA servidas com TLS autoassinado.

Gateway interno (o LiteLLM da SR/RR, por exemplo) publica HTTPS com certificado assinado por
ele mesmo: o Python valida a cadeia contra as autoridades públicas do sistema e recusa a
conexão antes de mandar qualquer requisição ("certificate verify failed"). Este módulo ensina o
Python a confiar naquele certificado — e só nele. É de propósito que não existe aqui nenhuma
opção de "ignorar a verificação" (o `-k` do curl): sem verificação nenhuma, qualquer máquina no
caminho poderia se passar pelo gateway e ficar com o conteúdo do inquérito.

O certificado é a parte pública do par (é o que o servidor mostra a quem conecta), mas fica
guardado no `.env` junto do resto da configuração de IA, porque é lá que a tela de Configurações
grava tudo. Como o `.env` é uma linha por chave e um PEM tem várias, o valor é gravado em
**base64** (`AI_CA_CERT`). Um **caminho absoluto** para um `.crt` também é aceito, para quem
prefere montar o arquivo no container por volume.
"""

import base64
import binascii
import hashlib
import re
import ssl
import time
from functools import lru_cache
from pathlib import Path

# Bloco PEM de certificado. Só certificado: chave privada ("BEGIN PRIVATE KEY") colada aqui por
# engano não passa — e não deveria mesmo estar no .env de ninguém.
BLOCO_PEM = re.compile(
    r"-----BEGIN CERTIFICATE-----\s*(?P<corpo>[A-Za-z0-9+/=\s]+?)\s*-----END CERTIFICATE-----"
)


def normalizar_pem(texto: str) -> str:
    """Valida o PEM colado no formulário e devolve só os blocos de certificado, um depois do outro.

    Aceita mais de um bloco (cadeia intermediária + raiz) e descarta qualquer coisa em volta
    (comentários que alguns exportadores põem antes do BEGIN). Levanta ValueError se não houver
    bloco válido ou se o base64 de dentro estiver corrompido — o caso comum de ter copiado o
    certificado do terminal e perdido um pedaço no meio."""
    blocos = []
    for m in BLOCO_PEM.finditer(texto or ""):
        corpo = "".join(m.group("corpo").split())
        try:
            base64.b64decode(corpo, validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("o conteúdo do certificado está corrompido (base64 inválido)")
        linhas = [corpo[i:i + 64] for i in range(0, len(corpo), 64)]
        blocos.append(
            "-----BEGIN CERTIFICATE-----\n" + "\n".join(linhas) + "\n-----END CERTIFICATE-----"
        )
    if not blocos:
        raise ValueError("não parece um certificado PEM (falta o bloco BEGIN/END CERTIFICATE)")
    return "\n".join(blocos) + "\n"


def pem_para_env(pem: str) -> str:
    """Codifica o PEM já normalizado para caber em uma linha do .env."""
    return base64.b64encode(pem.encode("ascii")).decode("ascii")


def pem_do_env(valor: str) -> str | None:
    """Lê o valor gravado no .env e devolve o PEM em texto; None se não houver certificado.

    O valor é o base64 gravado pela tela de Configurações ou o caminho absoluto de um .crt
    montado no container (começa com "/")."""
    valor = (valor or "").strip()
    if not valor:
        return None
    if valor.startswith("/"):
        try:
            return Path(valor).read_text(encoding="utf-8")
        except OSError as e:
            raise ValueError(f"não foi possível ler o certificado em {valor}: {e}")
    try:
        return base64.b64decode(valor, validate=True).decode("ascii")
    except (binascii.Error, ValueError, UnicodeDecodeError):
        raise ValueError("o certificado gravado no .env está ilegível — salve-o de novo em Configurações")


@lru_cache(maxsize=8)
def contexto_ssl(valor_env: str) -> ssl.SSLContext | None:
    """Contexto TLS que confia no certificado do .env, para passar ao urlopen; None se não houver.

    Continua verificando hostname e validade — o certificado do gateway só entra como âncora de
    confiança adicional. Como o CN/SAN do gateway costuma ser o IP, a URL configurada precisa ser
    a mesma que o certificado cobre (o guia do LiteLLM pede o IP, não o nome).

    O cache é pelo próprio valor do .env: montar o contexto lê e faz o parse do PEM a cada
    chamada, e o .env é lido a cada mensagem enviada. Trocar o certificado troca o valor e
    invalida a entrada sozinho."""
    pem = pem_do_env(valor_env)
    if not pem:
        return None
    ctx = ssl.create_default_context(cadata=normalizar_pem(pem))
    return ctx


def impressao_digital(valor_env: str) -> str:
    """SHA-256 do primeiro certificado, no formato "AB:CD:…" — como openssl x509 -fingerprint."""
    pem = pem_do_env(valor_env)
    if not pem:
        return ""
    m = BLOCO_PEM.search(pem)
    if not m:
        return ""
    der = base64.b64decode("".join(m.group("corpo").split()))
    digest = hashlib.sha256(der).hexdigest().upper()
    return ":".join(digest[i:i + 2] for i in range(0, len(digest), 2))


def resumo(valor_env: str) -> str:
    """Descrição de uma linha do certificado salvo, para a tela de Configurações: a quem ele foi
    emitido, até quando vale e a impressão digital — dá para conferir contra o que o administrador
    do gateway informou sem devolver o certificado inteiro ao navegador. Vazio se não houver
    certificado; se o que está gravado não puder ser lido, a mensagem do erro entra no lugar (é
    informação de tela, não pode derrubar o GET da configuração)."""
    try:
        ctx = contexto_ssl(valor_env)
    except ValueError as e:
        return f"Certificado ilegível: {e}"
    if ctx is None:
        return ""
    certs = ctx.get_ca_certs()
    if not certs:
        return "Certificado salvo"
    campos = {c[0][0]: c[0][1] for c in certs[0].get("subject", ()) if c and c[0]}
    nome = campos.get("commonName") or campos.get("organizationName") or "certificado"
    try:
        validade = time.strftime("%d/%m/%Y", time.gmtime(ssl.cert_time_to_seconds(certs[0]["notAfter"])))
    except (KeyError, ValueError):
        validade = "?"
    return f"{nome} · válido até {validade} · SHA-256 {impressao_digital(valor_env)}"

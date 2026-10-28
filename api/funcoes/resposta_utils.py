"""Peças compartilhadas de montagem de resposta HTTP."""

from urllib.parse import quote


def cabecalho_disposicao(modo: str, nome: str | None) -> str:
    """Valor de Content-Disposition com o nome em UTF-8 (RFC 5987/6266).

    O motivo de existir: nomes de arquivo reais (Cellebrite/IPED, anexo do WhatsApp) levam
    acento, aspas curvas e emoji, e o header HTTP é codificado latin-1 pelo Starlette — um
    `filename="São Paulo.pdf"` cru não é apenas feio: levanta UnicodeEncodeError na montagem
    da resposta e derruba a rota com 500 em cima de um download legítimo. O
    `filename*=UTF-8''...` percent-codificado (quote) resolve os dois: sobrevive a qualquer
    caractere (CR/LF e aspas viram %0D/%0A/%22, sem espaço para injeção no header) e é o
    formato que todo navegador moderno entende. É o desenho que a casa já usa em
    relatorios.py/marcadores.py/documentos_utils.py — as rotas de mídia é que tinham ficado
    para trás com o sanitize antigo (que só descascava aspas e CRLF, nunca o acento)."""
    nome = (nome or "arquivo").strip() or "arquivo"
    return f"{modo}; filename*=UTF-8''{quote(nome)}"

"""Rota da documentação de código: lista e entrega os arquivos .md da pasta `docs/` do projeto.

A pasta `docs/` é a documentação de feature do próprio sistema (um .md por feature, indexados por
docs/README.md). Ela já existia no repositório, mas só era legível por quem tem o código aberto no
editor; esta rota a publica para a tela Administração → Documentação de código, que renderiza o
markdown no navegador.

Nada aqui toca o banco: é leitura de arquivo, e só leitura — não há rota de escrita, a
documentação continua sendo editada pelo repositório e versionada no git.

A pasta entra no container por bind mount (`./docs:/app/docs:ro` no docker-compose.yml), não pelo
Dockerfile: assim editar um .md aparece na hora na tela, sem rebuild da imagem da API. Rodando
fora do Docker, cai no `docs/` da raiz do repositório.
"""

import os
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query

from funcoes.auth_utils import exigir_admin

router = APIRouter(dependencies=[Depends(exigir_admin)])

# Nome do arquivo, sem caminho: só o que a pasta docs/ usa hoje (letras com acento, dígitos,
# `_`, `-`, `.` e espaço). Barra e `..` nem chegam a virar caminho — a checagem de que o arquivo
# resolvido está DENTRO de docs/ ainda é feita depois, mas esta primeira barreira evita traversal
# antes de tocar o filesystem.
_RE_NOME_MD = re.compile(r"^[\w .\-]+\.md$", re.UNICODE)

# Link markdown para outro documento da pasta: [rótulo](arquivo.md)
_RE_LINK_MD = re.compile(r"\[[^\]]+\]\(([^)#]+\.md)(?:#[^)]*)?\)")

_MAX_TRECHOS = 3          # trechos de contexto por arquivo na busca
_TAMANHO_TRECHO = 200     # caracteres de cada trecho


def diretorio_docs() -> Path:
    """Onde está a pasta `docs/`. Ordem: DOCS_DIR no .env, o bind mount /app/docs do container,
    e por fim `docs/` na raiz do repositório (execução local, sem Docker)."""
    candidatos = []
    do_env = os.getenv("DOCS_DIR", "").strip()
    if do_env:
        candidatos.append(Path(do_env))
    aqui = Path(__file__).resolve()
    candidatos.append(aqui.parents[1] / "docs")   # /app/docs (container)
    candidatos.append(aqui.parents[2] / "docs")   # <repo>/docs (fora do Docker)
    for candidato in candidatos:
        if candidato.is_dir():
            return candidato
    return candidatos[-1]


def _normalizar(texto: str) -> str:
    """Minúsculas e sem acento — a busca não pode depender de o usuário digitar "extração" com
    cedilha e til para achar extração_ufdr.md."""
    sem_acento = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in sem_acento if not unicodedata.combining(c)).lower()


def _titulo(conteudo: str, nome: str) -> str:
    """Título do documento = primeiro `# ` do arquivo; sem ele, o próprio nome do arquivo.

    Os arquivos são lidos como `utf-8-sig`: mesa_inquerito.md está salvo com BOM, e o BOM cola
    no `#` da primeira linha — o título viraria parágrafo aqui e na renderização da tela."""
    for linha in conteudo.split("\n")[:60]:
        if linha.startswith("# "):
            return linha[2:].strip()
    return nome[:-3] if nome.endswith(".md") else nome


def _resolver(nome: str) -> Path:
    """Caminho do .md pedido, garantindo que ele é mesmo um arquivo DENTRO de docs/."""
    if not _RE_NOME_MD.match(nome or "") or ".." in nome:
        raise HTTPException(status_code=400, detail="Nome de documento inválido.")
    base = diretorio_docs().resolve()
    caminho = (base / nome).resolve()
    if caminho.parent != base or not caminho.is_file():
        raise HTTPException(status_code=404, detail="Documento não encontrado.")
    return caminho


def _atualizado_em(caminho: Path) -> str:
    return datetime.fromtimestamp(caminho.stat().st_mtime, tz=timezone.utc).isoformat()


def _grupos_do_indice(base: Path):
    """Lê docs/README.md e devolve (arquivo -> seção, ordem dos arquivos).

    O README é o índice curado da documentação: agrupa os .md em seções ("Plataforma",
    "Extração forence"…) numa ordem que faz sentido para quem lê. A tela reaproveita esse
    agrupamento em vez de inventar um próprio ou jogar 30 arquivos numa lista alfabética. Se o
    README sumir ou mudar de formato, o resultado é vazio e a listagem cai na ordem alfabética —
    sem quebrar a tela.
    """
    readme = base / "README.md"
    if not readme.is_file():
        return {}, []
    try:
        linhas = readme.read_text(encoding="utf-8-sig").split("\n")
    except (OSError, UnicodeDecodeError):
        return {}, []

    grupos = {}
    ordem = []
    secao = ""
    for linha in linhas:
        titulo = re.match(r"^#{2,3}\s+(.*)$", linha)
        if titulo:
            # "## 4.1. Arquivos de celular e nuvem" -> "Arquivos de celular e nuvem"
            secao = re.sub(r"^[\d.\s]+", "", titulo.group(1)).strip()
            continue
        for alvo in _RE_LINK_MD.findall(linha):
            nome = alvo.split("/")[-1]
            if nome not in grupos:
                grupos[nome] = secao
                ordem.append(nome)
    return grupos, ordem


def _trechos(conteudo: str, termo_normalizado: str):
    """Até 3 linhas do documento que contêm o termo buscado, para a lista mostrar por que o
    arquivo casou (nome e título sozinhos não explicam um acerto no meio do texto)."""
    achados = []
    for linha in conteudo.split("\n"):
        limpa = linha.strip()
        if not limpa or limpa.startswith("|---") or limpa.startswith("```"):
            continue
        if termo_normalizado in _normalizar(limpa):
            achados.append(limpa[:_TAMANHO_TRECHO])
            if len(achados) >= _MAX_TRECHOS:
                break
    return achados


@router.get("/api/documentacao")
def listar_documentacao(busca: str = Query("", max_length=120)):
    """Lista os .md de docs/ — nome, título, seção do índice, tamanho e data da última alteração.

    Com `busca`, filtra por nome, título ou conteúdo (sem acento, sem caixa) e devolve os trechos
    que casaram. A pasta tem algumas dezenas de arquivos pequenos, então a busca lê todos eles a
    cada chamada em vez de manter índice — mais simples, e imperceptível nesse volume.
    """
    base = diretorio_docs()
    if not base.is_dir():
        return {"arquivos": [], "diretorio": str(base), "disponivel": False}

    grupos, ordem_indice = _grupos_do_indice(base)
    termo = _normalizar(busca.strip())

    arquivos = []
    for caminho in base.glob("*.md"):
        if not caminho.is_file():
            continue
        try:
            conteudo = caminho.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError):
            continue

        nome = caminho.name
        titulo = _titulo(conteudo, nome)
        trechos = []
        if termo:
            no_nome = termo in _normalizar(nome) or termo in _normalizar(titulo)
            trechos = _trechos(conteudo, termo)
            if not no_nome and not trechos:
                continue

        arquivos.append({
            "nome": nome,
            "titulo": titulo,
            "grupo": grupos.get(nome) or ("Índice" if nome == "README.md" else "Outros documentos"),
            "bytes": caminho.stat().st_size,
            "linhas": conteudo.count("\n") + 1,
            "atualizado_em": _atualizado_em(caminho),
            "trechos": trechos,
        })

    # ordem do índice primeiro (README na frente); o que não está nele vai para o fim, em ordem
    # alfabética sem acento
    def chave(item):
        try:
            posicao = ordem_indice.index(item["nome"])
        except ValueError:
            posicao = len(ordem_indice)
        return (0 if item["nome"] == "README.md" else 1, posicao, _normalizar(item["titulo"]))

    arquivos.sort(key=chave)
    return {"arquivos": arquivos, "diretorio": str(base), "disponivel": True}


@router.get("/api/documentacao/{nome}")
def obter_documentacao(nome: str):
    """Devolve o markdown cru de um documento — quem renderiza é a tela (js/utils/markdown.js)."""
    caminho = _resolver(nome)
    try:
        conteudo = caminho.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        raise HTTPException(status_code=500, detail="Não foi possível ler o documento.")
    return {
        "nome": caminho.name,
        "titulo": _titulo(conteudo, caminho.name),
        "conteudo": conteudo,
        "bytes": caminho.stat().st_size,
        "atualizado_em": _atualizado_em(caminho),
    }

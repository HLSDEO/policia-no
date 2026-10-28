"""Higiene da pasta data/: uploads abandonados e lixeira têm prazo de validade.

**Por que existe.** O fluxo da aba "Arquivo genérico" larga o arquivo em data/ (ou
data/<delegacia>/) e ele só muda de lugar quando alguém o IMPORTA — até lá é um arquivo solto, e
depois de um "esqueci de importar" vira um para sempre: a pasta de entrega vira cemitério de
PDFs de 2023. Do outro lado, o "Remover documento" do card manda a prova para data/_lixeira/
(documentos_utils.mover_para_lixeira) — um cofre com prazo, não uma lixeira eterna.

**O que esta varredura considera órfão** (movido para a lixeira, NUNCA apagado em seco):
arquivo SOLTO na raiz de data/ ou na raiz de uma delegacia (profundidade ≤ 2), com extensão
dos tipos que o upload aceita (.ufdr fica de fora — é insumo de extração, não prova avulsa),
mais velho que UPLOAD_ORFAO_DIAS (padrão 7; 0 desliga), e cujo nome não aparece em nenhum
registro (documento anexado ou extração). As três condições juntas poupam o arquivo copiado à
mão esperando importação de verdade: 7 dias parado E sem registro E de tipo de upload é
abandono com quase toda certeza — e ainda assim a volta-atrás são 30 dias na lixeira.

**O expurgo da lixeira** (LIXEIRA_DIAS, padrão 30) é o único rm() daqui: arquivo que já era
documento removido ou upload órfão e passou o mês inteiro sem ninguém reclamar.

Roda pelo agendador interno (funcoes/agendador.py, tarefa "higiene_data", mesmo ledger do resumo
noturno) e é chamável à mão: python tools/higienizar_data.py.
"""

import logging
import os
import time
from pathlib import Path

from funcoes.db_utils import get_connection
from funcoes.env_utils import parse_env, read_env_text
from funcoes.organizacao_arquivos import (
    CATEGORIAS_EXTRACAO,
    PASTA_DADOS,
    PASTA_LIXEIRA,
    PASTAS_PULADAS,
)

logger = logging.getLogger(__name__)

_PADRAO_ORFAO_DIAS = 7
_PADRAO_LIXEIRA_DIAS = 30


def _dias(chave: str, padrao: int) -> int:
    """Dias do .env (relido a cada uso, como o resto da casa). 0 = tarefa desligada."""
    try:
        return max(0, int((parse_env(read_env_text()).get(chave) or "").strip() or padrao))
    except ValueError:
        return padrao


def _extensoes_de_upload() -> frozenset:
    """As extensões que o próprio upload/gravação de prova avulsa escreve em data/ — importadas
    de documentos_utils para a varredura nunca divergir do que o upload aceita."""
    from funcoes.documentos_utils import _EXTRATORES_TEXTO, _IMAGENS_PROVA

    return frozenset(_EXTRATORES_TEXTO) | frozenset(_IMAGENS_PROVA) | {".zip"}


def _nomes_referenciados() -> set:
    """Nomes (sem caminho, minúsculos) que pertencem a algo: documento anexado (NO_ARQUIVO guarda
    o caminho relativo organizado, mas quando a organização falhou guarda só o nome — o nome é a
    interseção que importa dos dois casos) e .ufdr de extração. Um arquivo solto com um destes
    nomes NÃO é órfão, é registro com caminho quebrado — mexer nele seria piorar."""
    nomes = set()
    query = """
        SELECT no_arquivo FROM investigacao.inq_tb04_documentos WHERE no_arquivo IS NOT NULL
        UNION
        SELECT no_arquivo FROM investigacao.inq_tb02_extracoes WHERE no_arquivo IS NOT NULL
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query)
            for (no_arquivo,) in cur.fetchall():
                nomes.add(Path(str(no_arquivo)).name.lower())
    return nomes


def _varredura_solta():
    """Os arquivos SOLTOS das pastas de entrega: raiz de data/ e raiz de cada delegacia
    (profundidade ≤ 2). Pastas de categoria, internas do sistema, ocultas e a própria lixeira
    ficam fora — aqui só o que espera importação."""
    def _pasta_de_entrega(p: Path) -> bool:
        return p.is_dir() and p.name not in CATEGORIAS_EXTRACAO \
            and p.name not in PASTAS_PULADAS and not p.name.startswith((".", "_"))

    if not PASTA_DADOS.is_dir():
        return
    pastas = [PASTA_DADOS]
    try:
        pastas += [p for p in PASTA_DADOS.iterdir() if _pasta_de_entrega(p)]
    except OSError:
        return
    for pasta in pastas:
        try:
            soltos = [p for p in pasta.iterdir() if p.is_file()]
        except OSError:
            continue
        if soltos:
            yield pasta, soltos


def limpar_uploads_orfaos() -> tuple[int, int]:
    """Move uploads abandonados para data/_lixeira/uploads/. Devolve (movidos, erros)."""
    dias = _dias("UPLOAD_ORFAO_DIAS", _PADRAO_ORFAO_DIAS)
    if dias == 0:
        return 0, 0
    limite = time.time() - dias * 86400
    referenciados = _nomes_referenciados()
    extensoes = _extensoes_de_upload()

    from funcoes.documentos_utils import mover_para_lixeira

    movidos = erros = 0
    for _pasta, soltos in _varredura_solta():
        for caminho in soltos:
            if caminho.suffix.lower() not in extensoes:
                continue
            if caminho.name.lower() in referenciados:
                continue
            try:
                if caminho.stat().st_mtime > limite:
                    continue
            except OSError:
                continue
            if mover_para_lixeira(caminho, "uploads") is not None:
                movidos += 1
            else:
                erros += 1
    return movidos, erros


def expurgar_lixeira() -> int:
    """Apaga da lixeira o que passou de LIXEIRA_DIAS sem reclamação, e remove as pastas que
    ficarem vazias. Devolve quantos arquivos saíram."""
    dias = _dias("LIXEIRA_DIAS", _PADRAO_LIXEIRA_DIAS)
    raiz = PASTA_DADOS / PASTA_LIXEIRA
    if dias == 0 or not raiz.is_dir():
        return 0
    limite = time.time() - dias * 86400
    apagados = 0
    for caminho in sorted(raiz.rglob("*"), reverse=True):
        try:
            if caminho.is_file():
                if caminho.stat().st_mtime < limite:
                    caminho.unlink(missing_ok=True)
                    apagados += 1
            elif caminho.is_dir() and not any(caminho.iterdir()):
                caminho.rmdir()
        except OSError:
            continue
    return apagados


def higienizar() -> dict:
    """Os dois passes de uma vez — o que o agendador chama na madrugada."""
    movidos, erros = limpar_uploads_orfaos()
    expurgados = expurgar_lixeira()
    resultado = {"orfaos_movidos": movidos, "erros": erros, "lixeira_expurgada": expurgados}
    if movidos or expurgados:
        logger.info("higiene data/: %s upload(s) órfão(s) para a lixeira, %s arquivo(s) da "
                    "lixeira expurgados (%s erro(s))", movidos, expurgados, erros)
    return resultado

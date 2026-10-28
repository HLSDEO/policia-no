"""Exercita a cadeia "parser de produto do Google -> gravador de domínio" da Nuvem IPED, com
uma amostra sintética de CADA produto, contra o banco REAL.

Por que existe: os parsers de `takeout_parsers` montam o dict só com o que o produto entrega,
enquanto os `_gravar_*` de `investigacao_utils` nasceram do UFDR, onde o dict vem completo, e
por isso leem cada campo por chave obrigatória (`loc["precisao"]`). A diferença não aparece em
teste que lê um caso do IPED de verdade: só quebra quando o caso tem AQUELE produto. Foi assim
que um local do Maps derrubou uma importação inteira com `KeyError: 'precisao'` depois de 293
itens — o caso usado para testar não tinha Maps.

Aqui cada produto tem amostra própria, então o buraco aparece sem depender do caso de teste.
O passo 1 pega chave faltando (sem tocar o banco); o passo 2 grava de verdade, o que também
cobre aridade do INSERT, tipo de coluna e NOT NULL.

O banco é tocado dentro de uma transação revertida no fim — nada sobra.

    docker compose run --rm -v ./api:/app api python /app/tools/teste_nuvem_produtos.py
"""

import sys
import traceback

sys.path.insert(0, "/app")

from funcoes import iped_utils as U  # noqa: E402
from funcoes.db_utils import get_connection  # noqa: E402
from funcoes.investigacao_utils import _gravar_contato_agenda  # noqa: E402
from funcoes.takeout_parsers import destino  # noqa: E402

falhas: list[str] = []
passos = 0


def checar(rotulo, fn):
    global passos
    passos += 1
    try:
        resultado = fn()
        print(f"  ok   {rotulo}" + (f" -> {resultado}" if resultado is not None else ""))
        return resultado
    except Exception:
        falhas.append(rotulo)
        print(f"  ERRO {rotulo}")
        print("       " + "\n       ".join(traceback.format_exc().strip().splitlines()[-3:]))
        return None


# ---------------------------------------------------------------------------
# Amostras: (rótulo, caminho dentro do ZIP do Takeout, tabela esperada, conteúdo, mínimo de linhas)
#
# O caminho é o que o item carrega depois do último ">>" do `path` do IPED, e é ele que
# takeout_parsers.destino() classifica — por isso a amostra também confere o roteamento, não
# só o parser: um produto renomeado pelo Google deixa de casar no _ROTEIRO e some sem erro.
# ---------------------------------------------------------------------------

AMOSTRAS = [
    (
        "Maps (locais rotulados)",
        "Maps/My labeled places/Labeled places.json",
        "tb18",
        b'{"type": "FeatureCollection", "features": [{"type": "Feature",'
        b' "geometry": {"type": "Point", "coordinates": [-43.1729, -22.9068]},'
        b' "properties": {"address": "Rua Um, 100", "name": "Casa",'
        b' "date": "2024-03-01T12:00:00Z",'
        b' "google_maps_url": "https://maps.google.com/?q=1"}}]}',
        1,
    ),
    (
        "Saved (locais salvos)",
        "Saved/Want to go.json",
        "tb18",
        b'{"list": {"listItems": [{"createTime": "2024-05-02T10:00:00Z",'
        b' "title": "Padaria", "viewer": {"url": "https://maps.google.com/?cid=1"},'
        b' "place": {"latLng": {"latE7": -229068000, "lngE7": -431729000}}}]}}',
        1,
    ),
    (
        "Timeline (histórico de localização)",
        "Timeline/Records.json",
        "tb18",
        b'{"locations": [{"latitudeE7": -229068000, "longitudeE7": -431729000,'
        b' "timestamp": "2024-01-05T08:00:00Z", "activity": "still"}],'
        b' "semanticSegments": [{"startTime": "2024-01-06T08:00:00Z",'
        b' "visit": {"topCandidate": {"placeLocation": "geo:-22.9068,-43.1729",'
        b' "semanticType": "Home", "placeId": "abc"}}}],'
        b' "rawSignals": [{"position": {"LatLng": "geo:-22.9100,-43.1700",'
        b' "timestamp": "2024-01-07T08:00:00Z"}}]}',
        3,
    ),
    (
        "Duo (histórico de chamadas)",
        "Duo/Call History.csv",
        "tb16",
        b"StateKey,IsOutgoingCall,IsVideoCall,CallDurationSeconds ,CallResult,UpdatedAt\r\n"
        b"tel:+5521999999999,true,true,42,ANSWERED,2024-02-01T10:00:00Z\r\n",
        1,
    ),
    (
        "Keep (nota)",
        "Keep/Lista de compras.json",
        "tb17",
        b'{"title": "Lista", "textContent": "comprar pao",'
        b' "createdTimestampUsec": 1700000000000000,'
        b' "userEditedTimestampUsec": 1700000100000000, "isTrashed": false,'
        b' "attachments": [{"filePath": "foto.jpg"}]}',
        1,
    ),
    (
        "Chrome (autofill)",
        "Chrome/Addresses and more.json",
        "autofill",
        b'{"Autofill": [{"name": "NAME_FULL", "value": "Fulano de Tal",'
        b' "timestamp": 13340000000000000, "usage_timestamp": [13350000000000000]},'
        b' {"name": "CPF", "value": "123.456.789-00"}]}',
        2,
    ),
]

VCARD = (
    b"BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Fulano de Tal\r\n"
    b"TEL;TYPE=CELL:+55 21 99999-9999\r\nEMAIL;TYPE=HOME:fulano@exemplo.com\r\n"
    b"END:VCARD\r\n"
)


# ---------------------------------------------------------------------------
# Passo 1 — chaves: nenhum gravador pode exigir campo que o produto não tem
# ---------------------------------------------------------------------------

class _CursorFalso:
    """Só registra o que seria executado. Serve para separar "chave faltando no dict" de
    "problema no SQL": aqui o banco nem entra na conta."""

    def __init__(self):
        self.execucoes = []

    def execute(self, sql, params=None):
        self.execucoes.append((sql, params))

    def fetchone(self):
        return (1,)


def _chaves_obrigatorias():
    """Todo gravador alcançado pelo roteador tem que aceitar a linha mais pobre possível — a
    que só tem o que o parser conseguiu extrair. `_LinhaParcial` é quem garante isso; sem ela
    a chave ausente vira KeyError no meio da importação."""
    for tabela, (fn, _chave) in sorted(U._GRAVAR_POR_TABELA.items()):
        fn(_CursorFalso(), 1, U._LinhaParcial({}))
    # o vCard entra por fora do roteador (item category=='Contacts'), mesmo problema — e a
    # entrada de telefone/e-mail é um dict à parte, com chaves próprias
    _gravar_contato_agenda(_CursorFalso(), 1,
                           U._LinhaParcial({"entradas": [U._LinhaParcial({})]}))
    return f"{len(U._GRAVAR_POR_TABELA)} gravadores + contato"


# ---------------------------------------------------------------------------
# Passo 2 — roteamento, parser e gravação de verdade, em transação revertida
# ---------------------------------------------------------------------------

def _preparar(cur):
    cur.execute("INSERT INTO investigacao.inq_tb01_inqueritos (nu_inquerito) "
                "VALUES ('TESTE-NUVEM-PRODUTOS') RETURNING id_inquerito")
    id_inq = cur.fetchone()[0]
    cur.execute(
        """INSERT INTO investigacao.inq_tb02_extracoes
               (id_inquerito, no_arquivo, no_fabricante, cd_status)
           VALUES (%s, 'caso-iped', 'nuvem', 'concluido') RETURNING id_extracao""",
        (id_inq,))
    return cur.fetchone()[0]


def _rodar_amostra(cur, id_extracao, rotulo, interno, tabela_esperada, blob, minimo):
    alvo = destino(interno)
    if alvo is None:
        raise AssertionError(f"{interno} não casa com nenhuma regra de _ROTEIRO")
    if alvo[0] != tabela_esperada:
        raise AssertionError(f"{interno} roteado para {alvo[0]}, esperado {tabela_esperada}")

    linhas = U._linhas_de_produto(alvo[0], interno, blob)
    if len(linhas) < minimo:
        raise AssertionError(f"{len(linhas)} linha(s), esperado ao menos {minimo}")

    for tabela, linha in linhas:
        fn, _chave = U._GRAVAR_POR_TABELA[tabela]
        fn(cur, id_extracao, U._LinhaParcial(linha))
    return f"{len(linhas)} linha(s) em {tabela_esperada}"


def _rodar_contatos(cur, id_extracao, pasta_modulo):
    """Caminho do item category=='Contacts': o blob é lido do storage do caso, então aqui o
    leitor é trocado por um que devolve o vCard da amostra — o resto do caminho é o real."""
    original = U._ler_blob_iped
    U._ler_blob_iped = lambda *_a, **_k: VCARD
    try:
        campos = {"dataSourcePath": "caso.zip", "md5": "e" * 32,
                  "idInDataSource": "1", "name": "contacts.vcf"}
        contatos = U._contatos_de_item(pasta_modulo, campos)
    finally:
        U._ler_blob_iped = original
    if not contatos:
        raise AssertionError("vCard não gerou contato")
    for contato in contatos:
        _gravar_contato_agenda(cur, id_extracao, contato)
    return f"{len(contatos)} contato(s)"


def main():
    from pathlib import Path

    print("Nuvem IPED: parsers de produto -> gravadores de domínio\n")

    print("chaves (sem banco):")
    checar("todo gravador aceita linha parcial", _chaves_obrigatorias)

    print("\nroteamento, parser e gravação (transação revertida):")
    with get_connection() as conn:
        try:
            with conn.cursor() as cur:
                id_extracao = _preparar(cur)
                for rotulo, interno, tabela, blob, minimo in AMOSTRAS:
                    checar(rotulo, lambda r=rotulo, i=interno, t=tabela, b=blob, m=minimo:
                           _rodar_amostra(cur, id_extracao, r, i, t, b, m))
                checar("Contacts (vCard)",
                       lambda: _rodar_contatos(cur, id_extracao, Path("/app")))
        finally:
            conn.rollback()

    print(f"\n{passos - len(falhas)}/{passos} passos ok")
    if falhas:
        print("falhou: " + ", ".join(falhas))
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())

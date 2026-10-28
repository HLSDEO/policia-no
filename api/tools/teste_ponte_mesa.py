"""Verifica a ponte da camada de documentos com as canônicas da mesa — sem banco.

Rodar da pasta api:  python tools/teste_ponte_mesa.py

Cobre o bug real (inquérito 272): achado etiquetado "documento" com valor "149.408.157-14"
não encostava na canônica 'cpf:14940815714' por causa da pontuação. O get_connection é
trocado por um dublê que devolve duas consultas pré-fabricadas (documentos e canônicas) —
o resto do grafo roda de verdade, com a IA simulada no JQ_PESSOAS/JQ_ACHADOS.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from funcoes import documentos_utils  # noqa: E402

FALHAS = []


def checa(cond, rotulo):
    print(("  ok  " if cond else "  FALHA  ") + rotulo)
    if not cond:
        FALHAS.append(rotulo)


PESSOAS = [
    {"no_pessoa": "FLAVIO DOS SANTOS MEDINA", "nu_cpf_cnpj": "149.408.157-14",
     "tp_papel": "investigado", "qt_mencoes": 500, "tx_contexto": "", "no_variantes": "", "no_vulgo": ""},
    {"no_pessoa": "ENDINGER THAYNA", "nu_cpf_cnpj": "149.408.157-14",
     "tp_papel": "envolvido", "qt_mencoes": 50, "tx_contexto": "", "no_variantes": "", "no_vulgo": ""},
]
ACHADOS = [
    # o caso do usuário: tipo "documento" (a IA etiqueta assim o cabeçalho do dossiê)
    {"tp_achado": "documento", "tx_valor": "149.408.157-14", "qt_ocorrencias": 4, "tx_contexto": "CABECALHO"},
    # CPF com um dígito errado: checksum não bate, não pode ligar
    {"tp_achado": "cpf", "tx_valor": "149.408.157-15", "qt_ocorrencias": 4, "tx_contexto": ""},
    # telefone que por acaso é 11 dígitos de CPF válido: tipo telefone não liga
    {"tp_achado": "telefone", "tx_valor": "14940815714", "qt_ocorrencias": 4, "tx_contexto": ""},
    # CNPJ puro de outra canônica
    {"tp_achado": "cnpj", "tx_valor": "60.821.467/0001-24", "qt_ocorrencias": 4, "tx_contexto": ""},
]
LINHAS = [(209, "DRE/2025.0098775/OUTROS/Dossie.xls", "xls", "resumo", PESSOAS, ACHADOS)]
CANONICAS = [
    (7810, "FLAVIO DOS SANTOS MEDINA", "cpf:14940815714"),
    (7900, "EMPRESA QUALQUER", "cnpj:60821467000124"),
]


class _Cur:
    def __init__(self, resultados):
        self._res = list(resultados)
        self._atual = []

    def execute(self, query, params=None):
        self._atual = self._res.pop(0)

    def fetchall(self):
        return self._atual


class _Conn:
    def __init__(self, resultados):
        self._resultados = resultados

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        cur = _Cur(self._resultados)

        class _Ctx:
            def __enter__(_s):
                return cur

            def __exit__(_s, *exc):
                return False

        return _Ctx()


def main() -> int:
    documentos_utils.get_connection = lambda: _Conn([LINHAS, CANONICAS])
    g = documentos_utils.grafo_documentos_inquerito(272)
    por_id = {n["id"]: n for n in g["nos"]}
    achado_por_valor = {n["dado"].get("tx_valor"): n["id"]
                        for n in g["nos"] if n["tipo"] == "doc_achado"}
    pessoa_por_rotulo = {n["rotulo"]: n["id"] for n in g["nos"] if n["tipo"] == "doc_pessoa"}
    pontes = {a["a"]: a["b"] for a in g["arestas"] if a["b"].startswith("c")}

    id_doc_ment = achado_por_valor.get("149.408.157-14")
    checa(pontes.get(id_doc_ment) == "c7810",
          "achado 'documento' 149.408.157-14 liga na canônica cpf:14940815714")
    checa(pontes.get(pessoa_por_rotulo.get("FLAVIO DOS SANTOS MEDINA")) == "c7810",
          "pessoa FLAVIO (mesmo nome + CPF) liga na canônica")
    checa(pessoa_por_rotulo.get("ENDINGER THAYNA") not in pontes,
          "ENDINGER (mesmo CPF, outro nome) NÃO liga")
    checa(achado_por_valor.get("149.408.157-15") not in pontes, "CPF com checksum inválido não liga")
    checa(achado_por_valor.get("14940815714") not in pontes,
          "telefone com dígitos de CPF válido não liga (tipo telefone)")
    checa(pontes.get(achado_por_valor.get("60.821.467/0001-24")) == "c7900",
          "achado cnpj 60.821.467/0001-24 liga na canônica do CNPJ")
    checa(all(a["a"] in por_id and a["b"] in por_id or a["b"].startswith("c")
              for a in g["arestas"] if a["b"].startswith("c")),
          "as arestas da ponte apontam para nós 'c<id>' (a mesa resolve as canônicas)")

    print()
    if FALHAS:
        print(f"{len(FALHAS)} FALHA(S)")
        return 1
    print("tudo certo.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

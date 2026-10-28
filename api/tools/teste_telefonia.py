"""Prova de ponta a ponta da importação de telefonia, contra o Postgres de verdade.

    docker compose exec api-1 python /app/tools/teste_telefonia.py <id_inquerito> <pasta em data/>

Importa os arquivos da pasta na ordem dada, depois de novo na ordem inversa, e confere o que
importa nesta funcionalidade: que tudo cai num card só por operadora, que reimportar não duplica
e que a planilha completa o que o PDF não tinha. No fim apaga o card que criou — é uma prova, não
uma carga (passe --manter para inspecionar o resultado no banco).

Mesmo espírito de tools/teste_parser_ipei.py e tools/teste_parser_ufdr.py.
"""

import sys
from pathlib import Path

sys.path.insert(0, "/app")

from funcoes import telefonia_utils as T  # noqa: E402
from funcoes.db_utils import get_connection  # noqa: E402
from funcoes.organizacao_arquivos import PASTA_DADOS  # noqa: E402

FALHAS: list[str] = []


def conferir(rotulo: str, obtido, esperado) -> None:
    ok = obtido == esperado
    print(f"  [{'ok ' if ok else 'FALHA'}] {rotulo}: {obtido}" + ("" if ok else f" (esperado {esperado})"))
    if not ok:
        FALHAS.append(rotulo)


def contar(id_telefonia: int) -> dict:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT qt_arquivos, qt_linhas, qt_chamadas, qt_erbs, qt_numeros, qt_sessoes,
                       qt_dados
                FROM investigacao.tel_tb01_telefonia WHERE id_telefonia = %s
                """,
                (id_telefonia,))
            arquivos, linhas, chamadas, erbs, numeros, sessoes, dados = cur.fetchone()
    return {"arquivos": arquivos, "linhas": linhas, "chamadas": chamadas,
            "erbs": erbs, "numeros": numeros, "sessoes": sessoes, "dados": dados}


def importar(pasta: Path, nomes: list[str], id_inquerito: int) -> int | None:
    id_telefonia = None
    for nome in nomes:
        relativo = (pasta / nome).relative_to(PASTA_DADOS).as_posix()
        resumo = T.extrair_telefonia(relativo, id_inquerito, None, None)
        if resumo is None:
            print(f"  - {nome}: NÃO reconhecido como telefonia")
            continue
        id_telefonia = resumo["id_telefonia"]
        secoes = ", ".join(f"{s['secao']}={s['qt_linhas']}" for s in resumo["secoes"])
        print(f"  - {nome}: {resumo['tp_conteudo']} · operadora {resumo['no_operadora']} · {secoes}")
    return id_telefonia


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    id_inquerito = int(argv[0])
    pasta = PASTA_DADOS / argv[1]
    if not pasta.is_dir():
        print(f"pasta não encontrada em data/: {argv[1]}")
        return 2
    manter = "--manter" in argv

    nomes = sorted(a.name for a in pasta.iterdir() if a.is_file())
    print(f"Arquivos: {len(nomes)}")

    print("\n1) importação na ordem alfabética")
    id_telefonia = importar(pasta, nomes, id_inquerito)
    if id_telefonia is None:
        print("nenhum arquivo reconhecido — nada a conferir")
        return 1
    primeira = contar(id_telefonia)
    print(f"  card {id_telefonia}: {primeira}")

    print("\n2) mesmos arquivos, ordem inversa (nada pode duplicar, e o card tem de ser o mesmo)")
    id_repetida = importar(pasta, list(reversed(nomes)), id_inquerito)
    segunda = contar(id_telefonia)
    print(f"  card {id_repetida}: {segunda}")

    print("\n3) conferências")
    conferir("o card é o mesmo nas duas importações", id_repetida, id_telefonia)
    conferir("nada duplicou", segunda, primeira)
    conferir("um arquivo registrado por arquivo da pasta", segunda["arquivos"], len(nomes))

    erbs = T.listar_erbs_telefonia(id_telefonia, limite=20000)["itens"]
    com_endereco = [e for e in erbs if e["tx_endereco"]]
    com_coordenada = [e for e in erbs if e["vl_latitude"] is not None]
    if primeira["chamadas"]:
        conferir("toda ERB ficou com endereço (a planilha completa o que o PDF não traz)",
                 len(com_endereco), len(erbs))
        conferir("toda ERB ficou com coordenada para o mapa", len(com_coordenada), len(erbs))
    else:
        # pasta só de dados: a ERB da sessão pode vir sem endereço (o extrato por CGI não traz
        # colunas ERB_*), e isso é esperado — o ofício de voz completa depois, pelo CGI
        print(f"  ERBs: {len(erbs)} ({len(com_endereco)} com endereço, "
              f"{len(com_coordenada)} com coordenada)")

    chamadas = T.listar_chamadas_telefonia(id_telefonia, limite=10)["itens"]
    if chamadas:
        primeira_chamada = chamadas[0]
        print(f"  1ª chamada: {primeira_chamada['dt_chamada']} "
              f"{primeira_chamada['nu_chamador']} -> {primeira_chamada['nu_chamado']} "
              f"({primeira_chamada['qt_duracao_segundos']}s, {primeira_chamada['cd_status']})")
    primeira_pagina = T.listar_chamadas_telefonia(id_telefonia, limite=100)
    com_antena = [c for c in primeira_pagina["itens"]
                  if c["tx_endereco_origem"] or c["tx_endereco_destino"]]
    print(f"  chamadas com antena resolvida pelo CGI (1ª página de "
          f"{primeira_pagina['total']}): {len(com_antena)}")
    conferir("o CGI da chamada casa com alguma ERB", bool(com_antena), True)

    numeros = T.listar_numeros_telefonia(id_telefonia, limite=5,
                                         ordenar="chamadas")["itens"]
    if numeros:
        print(f"  interlocutor mais frequente: {numeros[0]['nu_telefone']} "
              f"({numeros[0]['qt_chamadas']} chamadas)")
    conferir("ERBs do inquérito visíveis para o mapa",
             len(T.listar_erbs_inquerito(id_inquerito)) >= len(com_coordenada), True)

    primeira_pagina_dados = T.listar_dados_moveis_telefonia(id_telefonia, limite=100)
    if primeira_pagina_dados["total"]:
        d0 = primeira_pagina_dados["itens"][0]
        print(f"  1ª sessão de dados: {d0['dt_inicio']} linha {d0['nu_linha']} "
              f"IP {d0['tx_ip']} CGI {d0['nu_cgi']} "
              f"({d0['qt_vol_entrada']}/{d0['qt_vol_saida']} {d0['tp_unidade'] or ''})")
        com_antena_dados = [d for d in primeira_pagina_dados["itens"] if d["tx_endereco_erb"]]
        print(f"  sessões com antena resolvida pelo CGI (1ª página de "
              f"{primeira_pagina_dados['total']}): {len(com_antena_dados)}")
        conferir("o CGI da sessão de dados casa com alguma ERB", bool(com_antena_dados), True)

    if manter:
        print(f"\ncard {id_telefonia} mantido no banco (--manter)")
    else:
        T.apagar_telefonia(id_telefonia)
        print(f"\ncard {id_telefonia} apagado")

    print("\nFALHAS: " + (", ".join(FALHAS) if FALHAS else "nenhuma"))
    return 1 if FALHAS else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

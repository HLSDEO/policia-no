"""Chama as funções de rota de "Nuvem × aparelho" direto, com um usuário de mentira, para cobrir
a camada que fica ACIMA de correlacao_nuvem_utils: a conversão de `?extracoes=1,2` em lista e o
repasse dos parâmetros na ordem certa.

Não vai por HTTP de propósito: pela rede seria preciso uma sessão de verdade, e o objetivo aqui é
a assinatura das funções, não a autenticação (que o FastAPI já garante — `dependencies=protegido`
no main.py, e a rota sem sessão responde 401).

    docker compose run --rm -v ./api:/app api python /app/tools/teste_rotas_correlacao.py
"""

import sys
import traceback

sys.path.insert(0, "/app")

from rotas import correlacao_nuvem as R  # noqa: E402

USUARIO = {"id_usuario": 1, "no_usuario": "teste"}

falhas: list[str] = []
passos = 0


def checar(rotulo, fn):
    global passos
    passos += 1
    try:
        print(f"  ok   {rotulo} -> {fn()}")
    except Exception:
        falhas.append(rotulo)
        print(f"  ERRO {rotulo}")
        print("       " + "\n       ".join(traceback.format_exc().strip().splitlines()[-3:]))


print("\n[conversão de ?extracoes=]")
for entrada, esperado in [(None, None), ("", None), ("1,2,5", [1, 2, 5]),
                          ("3, 4 ,x", [3, 4]), ("abc", None), ("-1", [-1])]:
    obtido = R._extracoes(entrada)
    marca = "ok  " if obtido == esperado else "ERRO"
    if obtido != esperado:
        falhas.append(f"_extracoes({entrada!r})")
    passos += 1
    print(f"  {marca} {entrada!r:10} -> {obtido!r}")

print("\n[funções de rota]")
# `limite` vai explícito em toda chamada porque o default declarado é `Query(25, ge=1, le=200)`:
# chamada por HTTP o FastAPI resolve isso num int, mas chamada direta como aqui o default chega
# como o próprio objeto Query. É limitação de chamar a função por fora, não da rota.
checar("fontes_route", lambda: R.fontes_route(-1, usuario=USUARIO))
checar("resumo_route (com ?extracoes)",
       lambda: R.resumo_route(-1, extracoes="1,2", usuario=USUARIO))
checar("arquivos_route (padrões)",
       lambda: R.arquivos_route(-1, limite=25, usuario=USUARIO)["total"])
checar("arquivos_route (todos os parâmetros)",
       lambda: R.arquivos_route(-1, extracoes="1,2", criterio="hash", estado="apagado",
                                categoria="Imagem", so_whatsapp=False, so_do_aparelho=True,
                                busca="boleto", tamanho_min=1024, ordenar="data",
                                pagina=2, limite=50)["criterio"])
checar("arquivos_route (critério inválido cai no padrão, não estoura)",
       lambda: R.arquivos_route(-1, criterio="'; DROP TABLE--", ordenar="xpto",
                                estado="inexistente", limite=25, usuario=USUARIO)["criterio"])

print(f"\n{passos - len(falhas)}/{passos} passos ok")
if falhas:
    print("FALHARAM: " + ", ".join(falhas))
sys.exit(1 if falhas else 0)

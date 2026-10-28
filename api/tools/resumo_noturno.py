"""Passe noturno do resumo por inquérito (ver funcoes/resumo_noturno.py e docs/resumo_noturno.md).

Roda de dentro do contêiner da API (o host chama via docker compose):

    docker compose exec -T api-1 python /app/tools/resumo_noturno.py --dry-run
    docker compose exec -T api-1 python /app/tools/resumo_noturno.py --inquerito 101 --forcar
    docker compose exec -T api-1 python /app/tools/resumo_noturno.py

Regras:
- Idempotente por hash: sem --forcar, só regenera o inquérito cujo material mudou.
- Um inquérito nunca derruba o passe inteiro (o erro fica na linha consolidada).
- Sai com código 0 mesmo com erros por inquérito; 1 só se nada pôde rodar (ex.: sem IA).
"""

import argparse
import json
import sys

sys.path.insert(0, "/app")

from funcoes.resumo_noturno import processar_todos, resumo_noturno_configurado  # noqa: E402


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Resumo noturno por inquérito")
    ap.add_argument("--inquerito", type=int, default=None, help="só este ID_INQUERITO")
    ap.add_argument("--forcar", action="store_true", help="regenera mesmo sem mudança")
    ap.add_argument("--dry-run", action="store_true",
                    help="lista o que seria processado, sem chamar a IA")
    ap.add_argument("--usuario", type=int, default=None,
                    help="ID_USUARIO para IA própria do usuário (opcional)")
    args = ap.parse_args(argv)

    if args.dry_run:
        from funcoes.resumo_noturno import calcular_hash_entrada, listar_inqueritos_para_passe, obter_resumo_inquerito
        alvos = listar_inqueritos_para_passe(args.inquerito)
        saida = []
        for iid in alvos:
            try:
                atual = obter_resumo_inquerito(iid)
                cons = (atual.get("consolidado") or {}).get("tx_hash_entrada")
                try:
                    novo = calcular_hash_entrada(iid)
                except Exception as exc:
                    novo = f"erro: {exc}"
                saida.append({"id_inquerito": iid, "tem_resumo": atual.get("tem"),
                              "mudou": (cons != novo), "hash_atual": novo})
            except Exception as exc:
                saida.append({"id_inquerito": iid, "erro": str(exc)[:300]})
        print(json.dumps(saida, ensure_ascii=False, indent=2, default=str))
        return 0

    if not resumo_noturno_configurado(args.usuario):
        print("ERRO: nenhum provedor de IA configurado (RESUMO_NOTURNO_*/AI_*).", file=sys.stderr)
        return 1

    resultados = processar_todos(args.inquerito, args.usuario, args.forcar)

    # Passe completo (não é teste de um inquérito só) conta como a execução do dia: o agendador
    # interno da API vê o dia como feito e não repete. É o que deixa quem já tem o systemd /
    # Agendador de Tarefas do host manter o que tem, sem os dois passes na mesma noite.
    if args.inquerito is None:
        from funcoes.agendador import registrar_passe_manual

        registrar_passe_manual(
            processados=sum(1 for r in resultados.values() if r.get("status") == "gerado"),
            erros=sum(1 for r in resultados.values() if r.get("status") == "erro"),
        )

    print(json.dumps(resultados, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

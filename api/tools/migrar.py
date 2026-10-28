"""CLI do runner de migrações de schema.

    docker compose exec api-1 python /app/tools/migrar.py                    # aplica os pendentes
    docker compose exec api-1 python /app/tools/migrar.py --estado           # só mostra, não aplica
    docker compose exec api-1 python /app/tools/migrar.py --marcar-baseline  # registra sem executar

`--marcar-baseline` é o cutover de um servidor que já existia antes deste sistema: o schema dele
foi mantido à mão e já contém o efeito dos deltas, então rodá-los quebraria. Registra todos como
aplicados sem executar nenhum. Numa instalação NOVA o bootstrap já faz isso (Fase 2 de
db/bootstrap/00_aplicar_migracoes.sh) — não rode à mão lá.

Sai com código 0 em sucesso (inclusive "nada pendente") e 1 se alguma migração falhou.
A mesma função roda sozinha no startup da API — ver funcoes/migrar_utils.py e main.py.
"""

import json
import sys

sys.path.insert(0, "/app")

from funcoes import migrar_utils  # noqa: E402


def main(argv: list[str]) -> int:
    if "--estado" in argv:
        print(json.dumps(migrar_utils.estado(), ensure_ascii=False, indent=2, default=str))
        return 0

    if "--marcar-baseline" in argv:
        r = migrar_utils.marcar_baseline()
        if r.get("erro"):
            print(f"ERRO: {r['erro']}", file=sys.stderr)
            return 1
        for nome in r["marcadas"]:
            print(f"baseline: {nome}")
        print(f"{len(r['marcadas'])} de {r['total_arquivos']} marcada(s) como já aplicada(s), sem executar")
        return 0

    resultado = migrar_utils.executar(aplicada_por="cli")
    for nome in resultado["aplicadas"]:
        print(f"aplicada: {nome}")

    if resultado["erro"]:
        print(f"ERRO: {resultado['erro']}", file=sys.stderr)
        return 1
    if not resultado["aplicadas"]:
        print("nada pendente")
    else:
        print(f"{len(resultado['aplicadas'])} migração(ões) aplicada(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

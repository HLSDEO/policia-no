# -*- coding: utf-8 -*-
"""Amostra as tripas do sistema durante o teste: CPU/RAM dos containers, conexões do
Postgres e profundidade da fila de extração — a cada N segundos, em CSV.

É o que diz O QUE quebrou quando o Locust mostrar o QUANDO quebrou:
  - conexoes_postgres perto de max_connections (100 default) → pool/queries saturando;
  - extracoes_pendentes/processando subindo sem nunca cair → fila engarrafada no
    JOBS_PESADOS_LIMITE=2 (ou no worker único);
  - cpu_api colada em 100% com poucos usuários → worker único da API é o teto.

Rodar do host, na raiz do repo, enquanto o teste roda:
    python tests/carga/monitor.py            # analiser central (docker compose padrão)
    python tests/carga/monitor.py --no       # também amostra o nó (C:\\Git\\analyser-no)
Saída: logs/monitor_carga.csv (junte com o CSV do Locust pelo timestamp).
"""
import argparse
import csv
import datetime
import os
import subprocess
import sys
import time

CAMPOS = ["timestamp", "container", "cpu_pct", "mem_mb", "conexoes_postgres",
          "extracoes_pendentes", "extracoes_processando", "extracoes_concluidas_5min"]


def _sh(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=30,
                              shell=(os.name == "nt")).stdout
    except Exception:
        return ""


def _stats(containers: dict[str, str]) -> list[dict]:
    """docker stats --no-stream → uma linha por container (cpu e memória em MB)."""
    saida = _sh(["docker", "stats", "--no-stream", "--format",
                 "{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}"])
    linhas = []
    for linha in saida.splitlines():
        partes = linha.split("\t")
        if len(partes) < 3:
            continue
        nome = partes[0]
        rotulo = next((r for c, r in containers.items() if nome == c or nome.endswith(c)), None)
        if rotulo is None:
            continue
        try:
            cpu = float(partes[1].rstrip("%"))
            mem = partes[2].split("/")[0].strip()
            mem_mb = round(float(mem.rstrip("MiB ").strip()), 1) if mem.endswith("MiB") else round(
                float(mem.rstrip("GiB ").strip()) * 1024, 1)
        except ValueError:
            continue
        linhas.append({"container": rotulo, "cpu_pct": cpu, "mem_mb": mem_mb})
    return linhas


def _postgres() -> dict:
    sql = ("SELECT (SELECT count(*) FROM pg_stat_activity), "
           "(SELECT count(*) FROM investigacao.inq_tb02_extracoes WHERE cd_status='pendente'), "
           "(SELECT count(*) FROM investigacao.inq_tb02_extracoes WHERE cd_status IN ('processando','parando')), "
           "(SELECT count(*) FROM investigacao.inq_tb02_extracoes WHERE cd_status='concluido' "
           " AND dt_fim_processamento > now() - interval '5 minutes')")
    saida = _sh(["docker", "compose", "exec", "-T", "postgres",
                 "psql", "-U", "analyser", "-d", "analyser", "-tAc", sql])
    partes = (saida.strip().split("|") + [""] * 4)[:4]
    return {"conexoes_postgres": partes[0], "extracoes_pendentes": partes[1],
            "extracoes_processando": partes[2], "extracoes_concluidas_5min": partes[3]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no", action="store_true", help="também amostra o container do nó (C:\\Git\\analyser-no)")
    ap.add_argument("--intervalo", type=int, default=10)
    ap.add_argument("--saida", default=os.path.join("logs", "monitor_carga.csv"))
    args = ap.parse_args()

    containers = {"analyser-api-1": "api-1", "analyser-api-2": "api-2",
                  "analyser-postgres": "postgres", "analyser-web": "web"}
    if args.no:
        containers["analyser-no-no"] = "no"
    os.makedirs(os.path.dirname(args.saida) or ".", exist_ok=True)
    novo = not os.path.exists(args.saida)
    with open(args.saida, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CAMPOS)
        if novo:
            w.writeheader()
        print(f"[monitor] amostrando a cada {args.intervalo}s em {args.saida} (Ctrl+C para parar)")
        while True:
            agora = datetime.datetime.now().isoformat(timespec="seconds")
            banco = _postgres()
            linhas = _stats(containers) or [{"container": c, "cpu_pct": "", "mem_mb": ""} for c in containers.values()]
            for linha in linhas:
                linha.update({"timestamp": agora, **banco})
                w.writerow({c: linha.get(c, "") for c in CAMPOS})
            f.flush()
            time.sleep(args.intervalo)


if __name__ == "__main__":
    sys.exit(main())

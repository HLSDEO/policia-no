# -*- coding: utf-8 -*-
"""Seed do cenário "50 unidades com nó": 50 usuários CARGA01..50, UM POR DELEGACIA
(CARGA-D01..D50, todas vinculadas ao nó de teste). Dispara os 50 uploads+extrações
(caminho 100% descentralizado: upload repassado ao nó + extração delegada) e espera
TODAS concluírem no fim — o nó drena a fila na CPU dele enquanto isso.

Requer: ADMIN_LOGIN/ADMIN_SENHA do central e as delegacias CARGA-D01..D50 criadas e
vinculadas ao nó. Escreve credenciais.csv no formato do locustfile.
"""
import csv
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import seed_carga  # noqa: E402
from gerar_ufdr import montar_ufdr_bytes  # noqa: E402

ESPERA_MAX_S = int(os.getenv("ESPERA_EXTRACAO_S", "900"))


def main() -> None:
    if not seed_carga.ADMIN_LOGIN or not seed_carga.ADMIN_SENHA:
        sys.exit("defina ADMIN_LOGIN e ADMIN_SENHA antes de rodar o seed")
    admin = seed_carga.sessao_admin()
    mapa = seed_carga.ids_delegacia(admin)
    faltando = [f"CARGA-D{nn:02d}" for nn in range(1, 51) if f"CARGA-D{nn:02d}" not in mapa]
    if faltando:
        sys.exit(f"faltam delegacias: {faltando[:5]}...")

    alvos = []
    for nn in range(1, 51):
        delegacia = f"CARGA-D{nn:02d}"
        seed_carga.garantir_usuario(admin, f"CARGA{nn:02d}", mapa[delegacia])
        alvos.append((nn, delegacia))
    seed_carga._log("50 usuários criados/aprovados")

    disparadas = []
    falhas = 0
    for nn, delegacia in alvos:
        login = f"CARGA{nn:02d}"
        numero = f"CARGA-{nn:02d}"
        arquivo = f"{login}.ufdr"
        s = seed_carga.login_usuario(login)
        existente = seed_carga.extracao_do_arquivo(s, arquivo)
        if existente and existente.get("cd_status") == "concluido":
            disparadas.append({"s": s, "login": login, "delegacia": delegacia,
                               "id_inq": existente["id_inquerito"], "id_ext": existente["id_extracao"]})
            continue
        r = s.post(f"{seed_carga.BASE_URL}/api/investigacao/ufdr-upload",
                   files={"arquivo": (arquivo, montar_ufdr_bytes(), "application/zip")},
                   data={"delegacia": delegacia, "token": f"carga{nn:02d}{int(time.time())}"},
                   timeout=300)
        if r.status_code != 200:
            falhas += 1
            seed_carga._log(f"FALHA upload {login}: {r.status_code} {r.text[:150]}")
            continue
        nome = r.json()["arquivos"][0]["nome"]
        r = s.post(f"{seed_carga.BASE_URL}/api/investigacao/extrair",
                   json={"arquivo": nome, "numero_inquerito": numero, "delegacia": delegacia},
                   timeout=60)
        if r.status_code != 200:
            falhas += 1
            seed_carga._log(f"FALHA extrair {login}: {r.status_code} {r.text[:150]}")
            continue
        corpo = r.json()
        disparadas.append({"s": s, "login": login, "delegacia": delegacia,
                           "id_inq": corpo["id_inquerito"], "id_ext": corpo["id_extracao"]})
    seed_carga._log(f"{len(disparadas)} extrações enfileiradas no nó ({falhas} falhas de envio)")

    fim = time.time() + ESPERA_MAX_S
    pendentes = {d["id_ext"] for d in disparadas}
    erros = {}
    while pendentes and time.time() < fim:
        for d in list(disparadas):
            if d["id_ext"] not in pendentes:
                continue
            e = next((x for x in d["s"].get(f"{seed_carga.BASE_URL}/api/investigacao/extracoes",
                                            params={"inquerito": d["id_inq"]}, timeout=60).json()
                      if x["id_extracao"] == d["id_ext"]), None)
            status = (e or {}).get("cd_status")
            if status == "concluido":
                pendentes.discard(d["id_ext"])
            elif status == "erro":
                pendentes.discard(d["id_ext"])
                erros[d["login"]] = (e or {}).get("tx_erro")
        if pendentes:
            seed_carga._log(f"restam {len(pendentes)} no nó...")
            time.sleep(5)

    linhas = []
    for d in disparadas:
        if d["id_ext"] in pendentes:
            erros[d["login"]] = "timeout"
            continue
        conversas = d["s"].get(f"{seed_carga.BASE_URL}/api/investigacao/inqueritos/"
                               f"{d['id_inq']}/conversas", timeout=60).json()
        linhas.append({"login": d["login"], "senha": seed_carga.SENHA_CARGA,
                       "delegacia": d["delegacia"], "id_inquerito": d["id_inq"],
                       "id_conversa": conversas[0].get("id_conversa", "") if conversas else ""})

    with open(seed_carga.SAIDA, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["login", "senha", "delegacia", "id_inquerito", "id_conversa"],
                           delimiter=";")
        w.writeheader()
        w.writerows(linhas)
    seed_carga._log(f"{len(linhas)}/50 concluídas pelo NÓ | erros: {erros or 'nenhum'}")
    if erros:
        sys.exit(1)


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""Prepara o ambiente para o teste de carga usando SÓ rotas HTTP (sem tocar no banco).

Cria N usuários analistas CARGA01..NN (aprovados), e para cada um: faz login, sobe um
.ufdr sintético via POST /api/investigacao/ufdr-upload (metade para a delegacia com NÓ,
se DELEGACIA_NO estiver definida — o upload é encaminhado ao nó, exercitando a ponte)
e dispara a extração para o inquérito CARGA-<nn>, esperando virar 'concluido'. No fim
grava credenciais.csv (login;senha;delegacia;id_inquerito) que o locustfile consome.

Rodar do host (precisa de `pip install requests`):
    $env:ADMIN_LOGIN='admin'; $env:ADMIN_SENHA='...'
    python tests/carga/seed_carga.py

Idempotente: usuário que já existe é reaproveitado; extração concluída não é re-disparada.
"""
import csv
import os
import sys
import time

import requests

from gerar_ufdr import montar_ufdr_bytes

BASE_URL = os.getenv("BASE_URL", "http://localhost:8080").rstrip("/")
ADMIN_LOGIN = os.getenv("ADMIN_LOGIN", "")
ADMIN_SENHA = os.getenv("ADMIN_SENHA", "")
QTD = int(os.getenv("QTD", "20"))
SENHA_CARGA = os.getenv("SENHA_CARGA", "Carga@2026!")
DELEGACIA_NO = os.getenv("DELEGACIA_NO", "") or None      # ex.: DELECIBER (tem nó ativo)
DELEGACIA_LOCAL = os.getenv("DELEGACIA_LOCAL", "") or None  # ex.: SIP (pasta do central)
SAIDA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "credenciais.csv")
ESPERA_MAX_S = int(os.getenv("ESPERA_EXTRACAO_S", "300"))


def _log(msg: str) -> None:
    print(f"[seed] {msg}", flush=True)


def sessao_admin() -> requests.Session:
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"login": ADMIN_LOGIN, "senha": ADMIN_SENHA}, timeout=30)
    r.raise_for_status()
    return s


def ids_delegacia(admin: requests.Session) -> dict:
    return {d["no_delegacia"]: d["id_delegacia"] for d in admin.get(f"{BASE_URL}/api/delegacias", timeout=30).json()}


def id_usuario_existente(admin: requests.Session, login: str) -> int | None:
    for u in admin.get(f"{BASE_URL}/api/usuarios", timeout=30).json():
        if (u.get("no_login") or "").lower() == login.lower():
            return u["id_usuario"]
    return None


def garantir_usuario(admin: requests.Session, login: str, id_del: int | None) -> None:
    if id_usuario_existente(admin, login) is not None:
        return
    r = admin.post(f"{BASE_URL}/api/usuarios",
                   json={"login": login, "nome": f"Usuário {login}", "senha": SENHA_CARGA,
                         "perfil": "analista", "id_delegacia": id_del}, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"criar {login}: {r.status_code} {r.text[:200]}")
    novo = r.json()["id_usuario"]
    admin.post(f"{BASE_URL}/api/usuarios/{novo}/aprovado", json={"aprovado": True}, timeout=30)


def login_usuario(login: str) -> requests.Session:
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"login": login, "senha": SENHA_CARGA}, timeout=30)
    r.raise_for_status()
    return s


def extracao_do_arquivo(s: requests.Session, arquivo: str) -> dict | None:
    for e in s.get(f"{BASE_URL}/api/investigacao/extracoes", timeout=60).json():
        if e.get("no_arquivo") == arquivo:
            return e
    return None


def semear_usuario(nn: int, delegacia: str | None) -> dict:
    login = f"CARGA{nn:02d}"
    numero = f"CARGA-{nn:02d}"
    arquivo = f"{login}.ufdr"
    s = login_usuario(login)

    existente = extracao_do_arquivo(s, arquivo)
    if existente and existente.get("cd_status") == "concluido":
        _log(f"{login}: extração {existente['id_extracao']} já concluída — pulando")
        return {"login": login, "senha": SENHA_CARGA, "delegacia": delegacia or "",
                "id_inquerito": existente["id_inquerito"], "id_conversa": ""}

    token = f"carga{nn:02d}{int(time.time())}"
    r = s.post(f"{BASE_URL}/api/investigacao/ufdr-upload",
               files={"arquivo": (arquivo, montar_ufdr_bytes(), "application/zip")},
               data={"delegacia": delegacia or "", "token": token}, timeout=300)
    if r.status_code != 200:
        raise RuntimeError(f"{login} upload: {r.status_code} {r.text[:200]}")
    nome = r.json()["arquivos"][0]["nome"]

    r = s.post(f"{BASE_URL}/api/investigacao/extrair",
               json={"arquivo": nome, "numero_inquerito": numero, "delegacia": delegacia}, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"{login} extrair: {r.status_code} {r.text[:200]}")
    corpo = r.json()
    id_inq, id_ext = corpo["id_inquerito"], corpo["id_extracao"]

    fim = time.time() + ESPERA_MAX_S
    while time.time() < fim:
        e = next((x for x in s.get(f"{BASE_URL}/api/investigacao/extracoes",
                                   params={"inquerito": id_inq}, timeout=60).json()
                  if x["id_extracao"] == id_ext), None)
        status = (e or {}).get("cd_status")
        if status == "concluido":
            break
        if status == "erro":
            raise RuntimeError(f"{login}: extração {id_ext} falhou: {(e or {}).get('tx_erro')}")
        time.sleep(2)
    else:
        raise RuntimeError(f"{login}: extração {id_ext} não concluiu em {ESPERA_MAX_S}s")

    corpo = s.get(f"{BASE_URL}/api/investigacao/inqueritos/{id_inq}/conversas", timeout=60).json()
    conversas = corpo.get("conversas") if isinstance(corpo, dict) else corpo
    id_conversa = conversas[0].get("id_conversa", "") if conversas else ""
    _log(f"{login}: inquérito {id_inq}, extração {id_ext} concluída ({delegacia or 'central'})")
    return {"login": login, "senha": SENHA_CARGA, "delegacia": delegacia or "",
            "id_inquerito": id_inq, "id_conversa": id_conversa}


def main() -> None:
    if not ADMIN_LOGIN or not ADMIN_SENHA:
        sys.exit("defina ADMIN_LOGIN e ADMIN_SENHA antes de rodar o seed")
    admin = sessao_admin()
    mapa = ids_delegacia(admin)
    alvos = []
    for nn in range(1, QTD + 1):
        del_no = DELEGACIA_NO and nn % 2 == 0
        delegacia = (DELEGACIA_NO if del_no else DELEGACIA_LOCAL) or None
        if delegacia is not None and delegacia not in mapa:
            sys.exit(f"delegacia {delegacia!r} não existe (use uma de {sorted(mapa)})")
        garantir_usuario(admin, f"CARGA{nn:02d}", mapa.get(delegacia) if delegacia else None)
        alvos.append((nn, delegacia))

    linhas = []
    for nn, delegacia in alvos:
        try:
            linhas.append(semear_usuario(nn, delegacia))
        except Exception as exc:  # segue semeando os demais; o resumo do fim conta os falhos
            _log(f"FALHA CARGA{nn:02d}: {exc}")

    with open(SAIDA, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["login", "senha", "delegacia", "id_inquerito", "id_conversa"],
                           delimiter=";")
        w.writeheader()
        w.writerows(linhas)
    _log(f"{len(linhas)}/{QTD} usuários prontos — credenciais em {SAIDA}")


if __name__ == "__main__":
    main()

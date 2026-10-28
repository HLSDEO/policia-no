# -*- coding: utf-8 -*-
"""Harness de carga do PolícIA (Locust) — cenários A (respiração), B (ingestão) e C (rampa).

Prerequisito: `python tests/carga/seed_carga.py` para gerar credenciais.csv (usuários
CARGA01..NN com inquérito e extração concluída; metade numa delegacia com NÓ).

Rodar (host, contra http://localhost:8080):
    pip install locust
    $env:CENARIO='A'; locust -f tests/carga/locustfile.py --host http://localhost:8080
ou pelo compose (master+worker na rede do analys­er, ver docker-compose.carga.yml).

Cenários (env CENARIO):
  A — só navegação/consulta (respiração de 100 abas abertas): acha o teto de leitores.
  B — respiração + ingestão (.ufdr novo por iteração, upload→extrair→acompanhar):
      acha a vazão real e onde a fila engarrafa.
  C — igual A, para a rampa de ruptura (step load 10→50→100→200… no web UI ou
      --spawn-rate/--step); use também o monitor.py para ver pool/fila/CPU no colapso.

O que cada métrica interna significa está no README (pool DB=20, anyio=40, jobs=2…).
"""
import csv
import os
import random
import time
import uuid

from locust import HttpUser, between, events, task

from gerar_ufdr import montar_ufdr_bytes

CREDENCIAIS = os.getenv("CREDENCIAIS", os.path.join(os.path.dirname(os.path.abspath(__file__)), "credenciais.csv"))
CENARIO = os.getenv("CENARIO", "A").strip().upper()
ESPERA_INGESTAO_S = int(os.getenv("ESPERA_INGESTAO_S", "600"))

with open(CREDENCIAIS, encoding="utf-8") as _f:
    CREDS = [linha for linha in csv.DictReader(_f, delimiter=";") if linha.get("login")]
if not CREDS:
    raise SystemExit(f"crenenciais.csv vazio ou ausente em {CREDENCIAIS} — rode seed_carga.py antes")


def _cred_aleatoria() -> dict:
    return random.choice(CREDS)


class Base(HttpUser):
    abstract = True

    def on_start(self) -> None:
        self.cred = _cred_aleatoria()
        self.id_inquerito = int(self.cred["id_inquerito"] or 0)
        self.id_conversa = self.cred.get("id_conversa") or ""
        with self.client.post("/api/auth/login",
                              json={"login": self.cred["login"], "senha": self.cred["senha"]},
                              name="/api/auth/login", catch_response=True) as r:
            if r.status_code != 200:
                r.failure(f"login {self.cred['login']}: {r.status_code}")
                self.client.stop()
            else:
                r.success()

    def get(self, url: str, **kw) -> None:
        kw.setdefault("name", url)
        self.client.get(url, **kw)


class Respiracao(Base):
    """A aba aberta: respiração de avisos + navegação leve pelo inquérito próprio."""

    abstract = os.getenv("SO_INGESTAO") == "1"
    weight = 5 if CENARIO == "B" else 1
    wait_time = between(2, 5)

    @task(2)
    def poll_investigacao(self) -> None:
        self.get("/api/investigacao/poll")

    @task(2)
    def avisos_ia(self) -> None:
        self.get("/api/auth/avisos-ia")

    @task(1)
    def quem_sou(self) -> None:
        self.get("/api/auth/eu")

    @task(2)
    def listar_inqueritos(self) -> None:
        self.get("/api/investigacao/inqueritos")

    @task(2)
    def abrir_inquerito(self) -> None:
        if self.id_inquerito:
            self.get(f"/api/investigacao/inqueritos/{self.id_inquerito}/contexto")
            self.get(f"/api/investigacao/inqueritos/{self.id_inquerito}/conversas")

    @task(1)
    def buscar_mensagens(self) -> None:
        if self.id_conversa:
            self.get(f"/api/investigacao/conversas/{self.id_conversa}/buscar-mensagens",
                     params={"termo": "mensagem", "limite": 30})

    @task(1)
    def listar_extracoes(self) -> None:
        if self.id_inquerito:
            self.get("/api/investigacao/extracoes", params={"inquerito": self.id_inquerito},
                     name="/api/investigacao/extracoes?inquerito")


class Ingestao(Base):
    """A estação que importa prova: upload .ufdr → extrair → acompanhar até o fim."""

    abstract = CENARIO != "B"
    weight = 4
    wait_time = between(5, 15)

    @task
    def ciclo_completo(self) -> None:
        nome = f"CARGAB-{self.cred['login']}-{uuid.uuid4().hex[:8]}.ufdr"
        # sufixo único => conteúdo (e hash MD5 do dedup) único por ciclo
        corpo = montar_ufdr_bytes(sufixo=uuid.uuid4().hex[:10])
        delegacia = self.cred.get("delegacia") or ""
        token = uuid.uuid4().hex

        with self.client.post("/api/investigacao/ufdr-upload",
                              files={"arquivo": (nome, corpo, "application/zip")},
                              data={"delegacia": delegacia, "token": token},
                              name="/api/investigacao/ufdr-upload",
                              catch_response=True, timeout=300) as r:
            if r.status_code != 200:
                r.failure(f"upload {r.status_code}: {r.text[:120]}")
                return
            salvo = r.json()["arquivos"][0]["nome"]
            r.success()

        with self.client.get("/api/investigacao/ufdr-upload/progresso", params={"token": token},
                             name="/api/investigacao/ufdr-upload/progresso",
                             catch_response=True, timeout=60) as r:
            # 404 aqui é o melhor resultado possível: o .parcial já virou arquivo final
            r.success() if r.status_code in (200, 404) else r.failure(f"progresso {r.status_code}")

        with self.client.post("/api/investigacao/extrair",
                              json={"arquivo": salvo, "numero_inquerito": f"CARGA-{self.cred['login']}",
                                    "delegacia": delegacia},
                              name="/api/investigacao/extrair",
                              catch_response=True, timeout=60) as r:
            if r.status_code != 200:
                r.failure(f"extrair {r.status_code}: {r.text[:120]}")
                return
            id_ext = r.json().get("id_extracao")
            r.success()

        self._acompanhar(id_ext)

    def _acompanhar(self, id_extracao) -> None:
        """Aguarda a extração terminar; o tempo TOTAL do ciclo vira a métrica `ciclo_ingestao`."""
        inicio = time.time()
        fim = inicio + ESPERA_INGESTAO_S
        while time.time() < fim:
            with self.client.get("/api/investigacao/extracoes", name="/api/investigacao/extracoes[poll]",
                                 catch_response=True, timeout=60) as r:
                if r.status_code != 200:
                    r.failure(f"poll {r.status_code}")
                    return
                e = next((x for x in r.json() if x["id_extracao"] == id_extracao), None)
                r.success()
            status = (e or {}).get("cd_status")
            if status == "concluido":
                self.environment.events.request.fire(
                    request_type="CYCLE", name="ciclo_ingestao", response_time=int((time.time() - inicio) * 1000),
                    response_length=0, exception=None, context="")
                return
            if status == "erro":
                self.environment.events.request.fire(
                    request_type="CYCLE", name="ciclo_ingestao", response_time=int((time.time() - inicio) * 1000),
                    response_length=0, exception=RuntimeError((e or {}).get("tx_erro") or "erro"), context="")
                return
            time.sleep(2)
        self.environment.events.request.fire(
            request_type="CYCLE", name="ciclo_ingestao", response_time=int((time.time() - inicio) * 1000),
            response_length=0, exception=TimeoutError("extração não concluiu"), context="")


@events.init_command_line_parser.add_listener
def _aviso(**_kwargs) -> None:
    print(f"[carga] cenário={CENARIO} | {len(CREDS)} credenciais de {CREDENCIAIS}")

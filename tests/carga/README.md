# Teste de carga (Locust)

Acha o **ponto de ruptura** do PolícIA: quantos usuários simultâneos o central aguenta e
onde ele quebra primeiro (pool do banco, worker da API, fila de jobs ou a ponte com o nó).

## Arquivos

| Arquivo | O que é |
| --- | --- |
| `seed_carga.py` | Cria os usuários CARGA01..NN e um inquérito com extração concluída para cada (metade numa delegacia com NÓ), via rotas HTTP reais. Gera `credenciais.csv`. |
| `locustfile.py` | Os usuários virtuais: navegam, consultam, buscam e (cenário B) importam `.ufdr`. |
| `gerar_ufdr.py` | Monta `.ufdr` sintético em memória (o mesmo formato do E2E do nó). |
| `monitor.py` | Amostra CPU/RAM dos containers + conexões do Postgres + profundidade da fila de extração em `logs/monitor_carga.csv`. |
| `docker-compose.carga.yml` | Locust mestre+worker dentro da rede do analyser (UI em http://localhost:8089). |

## Como rodar

```powershell
# 1) ambiente de pé (central e, se for testar o nó, o nó também)
docker compose up -d

# 2) seed (no host; pede ADMIN_LOGIN/ADMIN_SENHA do ambiente)
pip install requests locust
$env:ADMIN_LOGIN='admin'; $env:ADMIN_SENHA='...'
$env:QTD='20'; $env:DELEGACIA_NO='DELECIBER'; $env:DELEGACIA_LOCAL='SIP'
python tests\carga\seed_carga.py

# 3) monitor ligado numa janela (a cada 10 s, CSV em logs\monitor_carga.csv)
python tests\carga\monitor.py --no

# 4) Locust — opção leve (host) ou compose (mais VUs que o docker permite)
$env:CENARIO='A'
locust -f tests\carga\locustfile.py --host http://localhost:8080
# ou:
$env:CENARIO='A'; docker compose -f tests\carga\docker-compose.carga.yml --project-directory tests\carga up -d --build
# UI: http://localhost:8089
```

## Cenários (env `CENARIO`)

- **A — Respiração (leitores puros):** 100 VUs só navegando/consultando. Responde
  "quantas abas abertas o sistema aguenta?" sem nada pesado na mesa.
- **B — Tempestade de ingestão:** VUs importando `.ufdr` (upload→extrair→acompanhar)
  enquanto as demais consultam. Responde "quantas extrações/h cabem por hora de trabalho
  real" — e se a fila some da vista. Métrica `ciclo_ingestao` = tempo total por prova.
- **C — Rampa de ruptura:** igual A, mas suba degraus no web UI (10→50→100→200→400,
  5 min cada) até **erro > 5% ou p95 > 5 s**. O último degrau bom é o teto; o próximo é
  a ruptura — olhe o CSV do monitor nesse minuto para ver O QUE quebrou.

## O que olhar quando quebrar

| Sintoma no Locust | Causa provável (confirme no monitor/CSV) |
| --- | --- |
| p95 sobe, CPU baixa, conexões do Postgres ≈ `max_connections` | pool DB (`DB_POOL_MAX=20` × workers) estourado |
| Erros 5xx com CPU da API em ~100% (1 worker) | worker único da API é o teto → `API_WORKERS` |
| `ciclo_ingestao` estoura mas consulta leve vai bem | fila de jobs: `JOBS_PESADOS_LIMITE=2` / worker único de parsing |
| Só as extrações da delegacia com nó atolam | ponte central→nó (leitura por Range sem pooling) |
| Latência acompanha CPU do postgres | índices/paginção das listagens quentes |

## Limpeza

```powershell
docker compose -f tests\carga\docker-compose.carga.yml --project-directory tests\carga down
# usuários/inquéritos CARGA ficam no banco para reuso (o seed é idempotente);
# para apagar, remova os inquéritos CARGA-* pela UI e os usuários na Administração.
Remove-Item tests\carga\credenciais.csv   # tem senha — nunca commitar
```

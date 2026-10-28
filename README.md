# Analyser — NÓ de delegacia (PAPEL=no)

Este repositório é o **nó** do esquema descentralizado (`docs/no_descentralizado.md`): o
container que roda na máquina da delegacia, junto da prova (`data/`). Não tem Postgres, web,
sessão nem IA de relatório — só a CPU da estação, a pasta `data/` local e as rotas internas
`/no/*` que o central chama.

O produto completo (banco, web, rotas de usuário, túnel, despachante) é o **central**, que roda
em outro lugar e alcança este nó por HTTP (porta `NO_PORT`) ou pelo túnel NAT.

## O que sobe

Um único serviço (`no`, imagem `policia-api-no`, build a partir de `./api`) e, opcionalmente,
o `no-web` (HTTPS 8443 para a mídia direta na LAN — `docs/no_midia_direta.md`). A imagem é
`python:3.12-slim` com Tesseract(+pt-BR), JRE (IpedDumper) e ffmpeg; os jobs pesados de OCR/CLIP
e a transcrição rodam aqui, na CPU local (`funcoes/no_jobs.py`, `funcoes/transcricao.py`).

Rotas (`api/rotas/no_servico.py`, todas com `Authorization: Bearer <NO_TOKEN>` salvo `/no/saude`):
`/no/saude`, `/no/listar`, `/no/hash`, `/no/parse`, `/no/arquivo`, `/no/midia`, `/no/organizar`,
`/no/extrair` (extração `.ufdr` na CPU do nó), `/no/transcrever`, `/no/embed`, `/no/importar`
(RIF/SIMBA/IPED parseados na estação, dados voltam pelo canal), `/no/iped-blob`, `/no/iped-thumb`.

## Instalação numa delegacia

1. clone deste repositório na máquina da delegacia;
2. `cp .env.no.example .env` e preencha: `PAPEL=no`, `NO_TOKEN` (gerado em Administração →
   Servidores no central), `CENTRAL_URL`, `NO_PORT` (ver `.env.no.example`);
3. coloque os `.ufdr`/documentos da delegacia em `./data` (subpasta com o nome da delegacia);
4. `docker compose -f docker-compose.no.yml up -d --build`;
5. no central, clique **Testar** no registro deste nó — online é o ponto verde.

## Layout

| Caminho | O que é |
|---|---|
| `api/Dockerfile` | o build do nó (único Dockerfile) |
| `docker-compose.no.yml` | o container do nó (+ `no-web` de mídia) |
| `.env.no.example` | modelo do `.env` do nó |
| `nginx/no-midia.*` | HTTPS da mídia direta ao navegador na LAN |
| `api/main.py` | app FastAPI do nó: só `/no/*` + health, e os laços de fundo (heartbeat, túnel, jobs) |
| `docs/no_descentralizado.md` | a arquitetura inteira (central ↔ nó) |

Os utilitários em `api/funcoes/` são compartilhados com o central (os MESMOS parsers/gravadores,
para o que o nó produz byte a byte igual ao que o central produziria). O que é papel do central
(banco, web, rotas de usuário, OpenShift) foi removido deste repositório.

# Mídia direta do nó para o navegador

**Resumo em uma frase:** quem está na LAN da delegacia baixa fotos/vídeos/áudios **direto do
nó** (porta 8443, HTTPS), sem o rinque pela WAN; quem está em qualquer outro lugar **não nota
nada** — o caminho de sempre (proxy do central) continua sendo a regra, e o atalho é só um
atalho.

## Por que isto existe

A evidência nasce no disco do nó e o analista que a examina está fisicamente ao lado dele.
Servir essa mídia pelo central faz cada byte atravessar o enlace fino duas vezes
(nó→central para indexar/abrir o zip, central→navegador para assistir) — um vídeo de 200 MB
que a LAN entregaria em segundos vira minutos. A mídia direta corta o laço: o central mantém
o **controle** (autentica o usuário, confere o inquérito, autoriza por arquivo) e o nó entrega
o **peso**.

```
navegador (estação na LAN de SMT)
   │ 1. "posso baixar do nó?"  ──────────► CENTRAL  (sessão + acesso ao inquérito)
   │ ◄── https://no-smt...:8443/no/midia?tk=<ticket de 10 min>
   │ 2. GET direto (Range p/ seek de vídeo)
   └──────────────────────────► NÓ :8443 (no-web, nginx TLS) ──► uvicorn /no/midia
                                                                  valida o HMAC com NO_TOKEN
                                                                  lê o membro do .ufdr local
```

Falhou qualquer elo (central sem rota, sonda da LAN falhou, ticket vencido, certificado
ausente): o navegador usa a URL do central, exatamente como era antes desta feature.

## Peças

| Arquivo | Papel |
|---|---|
| `api/funcoes/no_ticket.py` | o passe: `emitir`/`validar` HMAC-SHA256 sobre um payload JSON com expiração; segredo = `TX_TOKEN`/`NO_TOKEN` do nó (um ticket do nó A não vale no nó B) |
| `api/funcoes/midia_direta_utils.py` | decisão no central: quando um arquivo tem rota direta (.ufdr remoto + nó apto + versão mínima) e a URL com ticket |
| `api/rotas/investigacao.py` (`/rota-direta`, `/rotas-diretas`) | o navegador pergunta por um arquivo ou por uma página inteira |
| `api/rotas/no_servico.py` (`GET /no/midia`) | serve o membro do `.ufdr` local; `Range` em membro STORED (seek de vídeo sem descomprimir); fluxo puro no resto |
| `nginx/no-midia.conf.template` + serviço `no-web` em `docker-compose.no.yml` | TLS 8443 na LAN; a porta publica **só** `/no/saude` e `/no/midia` — o resto do `/no/*` continua exclusivo do central |
| `web/js/utils/midia_direta.js` | a tela: `preparar()` em lote, `url()` síncrona, `fallback()` no `onerror`; sonda por base 1× a cada 10 min por aba |
| `db/init/46_nos.sql` + `db/migracoes/0061_midia_direta.sql` | `NO_TB01_SERVIDORES.TX_MIDIA_URL` + `IN_MIDIA_DIRETA` (por nó, desligado por default) |

## O ticket (o que o navegador recebe)

`tk = b64url(json) + "." + hex(hmac-sha256(NO_TOKEN, b64url(json)))`, payload:

```json
{"r": "SMT-2026/UFDR_...ufdr", "i": "archive/Pictures/x.jpg", "t": "image/jpeg", "d": 0, "n": "x.jpg", "x": 1761234567}
```

`r` = .ufdr relativo à pasta de dados do nó · `i` = membro no zip (com `::` dos aninhados da
nuvem) · `t` mime · `d=1` vira `Content-Disposition: attachment` · `x` expira em ~10 min.
Adulterar QUALQUER byte (inclusive trocar o destino por outro arquivo) quebra o HMAC — 403, e o
navegador cai no central sozinho. Não há estado nem revogação fina: o TTL é a revogação, e
"trocar token" na tela derruba também os tickets em aberto.

Segurança da porta 8443: sem ticket não existe resposta útil; sem HTTPS não existe página do
central (que é `https://`) que consiga chamar http — por isso o checklist da TI é pré-requisito,
e o `no-web` fica parado sem certificado.

## O que vai direto e o que fica no central

Vai direto do nó (com ticket): o **binário original** — vídeo/áudio no modal e no "Baixar", e a
**imagem em formato que o navegador decodifica sozinho** (jpg/png/webp/gif/bmp/avif: o modal abre
o original do nó, que é maior e mais nítido que a prévia). Fica no central: thumbnail da galeria
(milhares por página, o central tem cache), prévia de **heic/tiff** (quem converte é o Pillow do
central) e tudo que não é mídia. Estação fora da LAN do nó: a sonda falha e tudo volta ao central
sozinho — nada quebra, só fica lento como sempre foi.

Por isso o **índice remoto do .ufdr** (`no_ponte.py`) importa mesmo fora da LAN: é como o central
abre o zip do nó pelo link fino sem varrer 90 GB. Ele mora em `./.cache/ufdr-indice` (bind mount
compartilhado entre api-1/api-2, travado entre processos): desce **uma vez** por arquivo, sobrevive
a deploy. Em `/tmp` ele morria a cada upgrade e todo mundo comédia 503 ("índice em preparação") ao
mesmo tempo enquanto as duas réplicas rebaixavam o link fino de novo.

## Sem CGTI: CA própria do Analiser (modo instalado em SMT)

Sem acesso à emissão da PF, a confiança vem de uma **CA própria do Analiser** criada no próprio
nó — o URL usa o **IP** do servidor (dispensa DNS): `https://10.27.16.146:8443`.

```
nginx/ssl/ca/ca-analyser.key   CA de 10 anos — SÓ ROOT, nunca sai da infraestrutura
nginx/ssl/ca/ca-analyser.crt   o público — este sim vai para as estações
nginx/ssl/midia.crt            leaf (IP SAN 10.27.16.146 + DNS futuro), assinado pela CA, 390 dias
nginx/ssl/midia.key            a chave do leaf
```

Backup da CA no central: `/mnt/dados/analyser/nginx/ssl/nos-ca/` (chave 600). Perder a CA =
recriar tudo e re-importar nas estações; guardar como se guarda token.

**Por estação, uma única vez** (o arquivo circula por onde a delegacia quiser — há uma cópia
no share `\\<IP-do-no>\Dados\ca-analyser.crt`):

```bat
certutil -addstore -f Root ca-analyser.crt
```

ou, sem admin local na estação (vale por usuário):

```bat
certutil -addstore -user -f Root ca-analyser.crt
```

ou duplo clique no `ca-analyser.crt` → Instalar Certificado → **Máquina Local** →
**Autoridades de Certificação Raiz Confiáveis**. Chrome/Edge/Firefox usam o repositório do
Windows — vale para todos. Sem importar nada a estação **não quebra**: a sonda TLS falha, e o
navegador usa o proxy do central como se mídia direta não existisse.

Emitir o leaf de um NÓ NOVO com a mesma CA (rodar no nó, porta `nginx/ssl`):

```bash
cd /mnt/dados/analyser/nginx/ssl
openssl req -new -newkey rsa:2048 -nodes -keyout midia.key \
  -subj "/O=Policia Federal/CN=<IP-do-no>" \
  -addext "subjectAltName=IP:<IP-do-no>" -out midia.csr
openssl x509 -req -in midia.csr -CA ca/ca-analyser.crt -CAkey ca/ca-analyser.key \
  -CAcreateserial -days 390 \
  -extfile <(printf 'subjectAltName=IP:<IP-do-no>\nextendedKeyUsage=serverAuth\nbasicConstraints=critical,CA:FALSE\n') \
  -out midia.crt
cat midia.crt ca/ca-analyser.crt > midia-chain.crt && mv midia-chain.crt midia.crt
docker compose -f docker-compose.no.yml restart no-web
```

(a CA e o CSR do primeiro nó ficaram também no central em `nginx/ssl/nos-ca/`; o "Testar mídia"
do central já valida contra essa CA montada em `/app/certs-nos`.)

**Renovar o leaf** (o modal Estado amarela aos 30 dias): repetir os comandos acima e
`restart no-web`; as estações não precisam fazer nada — só confiam na CA, não no leaf.

## Checklist para a TI (por nó novo)

> No modo "sem CGTI" acima, os passos 1 e 2 (DNS + certificado PF) não se aplicam — sobra só
> o firewall. Em SMT-ES o caminho ativo hoje é o da CA própria com URL por IP.

1. **DNS interno** — registrar A `no-<sigla>.policia-es.pf.gov.br` → IP do servidor da delegacia
   (resposta visível das estações da LAN; fora dela pode nem resolver — tanto faz).
2. **Certificado TLS** — emitir pela **AC interna da PF** para `no-<sigla>.policia-es.pf.gov.br`
   (a mesma infra usada pelo site já em produção). Entregar ao administrador do nó:
   - `nginx/ssl/midia.crt` — certificado **com a cadeia completa** (leaf + intermediárias);
   - `nginx/ssl/midia.key`  — chave privada (permissões 600, não sai da máquina).
3. **Firewall** — liberar TCP **8443** das estações da delegacia para o servidor do nó. A porta
   é **8443 em todos os nós** (não precisa liberar nada para o central nem para a internet: nada
   entra pela 8443 de fora da LAN; o nó continua ligando para fora no que já tinha).
4. **Sem DNAT/reverse proxy** — o navegador fala com o nó pelo nome dele; o DNS split-horizon
   resolve localmente.

No servidor do nó, depois de receber os arquivos (uma vez, pelo `instalar-host` à mão):

```bash
cd /mnt/dados/analyser
sudo install -d -m 700 nginx/ssl
# copiar midia.crt / midia.key para nginx/ssl/ (chmod 600 na key)
sudo docker compose -f docker-compose.no.yml up -d --force-recreate no-web
```

O container `no-web` fica em `sleep` inofensivo enquanto não há certificado — ao colocá-lo e
recriar o serviço, a mídia entra no ar sem tocar no `no`.

**Nó sem acesso ao Docker Hub** (o caso das delegacias, que só falam com o central): a imagem
`nginx:1.27-alpine` é re-publicada no registry do central — na primeira vez, no central:

```bash
docker tag nginx:1.27-alpine 10.27.7.19:5000/nginx:1.27-alpine
docker push 10.27.7.19:5000/nginx:1.27-alpine
```

e no nó, antes do primeiro `up` do no-web:

```bash
docker pull 10.27.7.19:5000/nginx:1.27-alpine
docker tag 10.27.7.19:5000/nginx:1.27-alpine nginx:1.27-alpine   # o compose pede por este nome
```

## Ligar no central (Administração → Servidores → Editar)

1. **URL de mídia direta**: `https://no-<sigla>.policia-es.pf.gov.br:8443`;
2. **Testar mídia** — o central bate no `/no/saude` por HTTPS: confere DNS, certificado e
   no-web de pé (o que a estação vai conferir depois, na rota dela);
3. marcar **Oferecer mídia direta** e salvar. A partir daí o central emite tickets para o nó
   (que já precisa estar na versão com `/no/midia` — o `Verificar`/`Atualizar` da tela resolve).

O modal **Estado** do nó mostra a validade do certificado ("válido por mais N dias", amarelo
< 30, vermelho vencido). Renovar o certificado = trocar os dois arquivos em `nginx/ssl/` e
`docker compose -f docker-compose.no.yml restart no-web`.

## Comportamento observado (o que cada falha parece)

| Sintoma | O que houve | Ação |
|---|---|---|
| Mídia abre igual sempre | nó sem mídia direta, ou estação fora da LAN | nada — é o default |
| Na delegacia continua lento, no "Testar mídia" tudo verde | estação não alcança a 8443 (firewall/DNS local) | passo 3/1 do checklist na estação |
| Funcionou e parou de repente | certificado venceu (modal Estado denuncia) | renovar + restart no-web |
| Linha do tempo do vídeo não seek | membro DEFLATE (raro) — fluxo sem Range é o contrato | nada |
| Miniatura da galeria lenta | prévia JPEG é gerada no central (não tem equivalente no nó) | fora do escopo disto |

Contadores do nó (`/no/estado` → `metricas`): `midia_direta` (atendidas),
`midia_direta_recusada` (HMAC/prazo furando — um espiko alto é varredura ou client quebrado).

## Teste rápido de ponta a ponta

```bash
# no nó — tudo local, sem navegador:
docker exec -e PASTA_INVESTIGACAO=/tmp/md-teste analyser-no python tools/teste_midia_direta.py
# da estação da LAN, com o URL do Editar:
curl -k https://no-smt.policia-es.pf.gov.br:8443/no/saude     # {"ok":true,"papel":"no",...}
```

O `tools/teste_midia_direta.py` cobre ticket (emissão/adulteração/origem/prazo) e a rota
(STORED inteiro, Range do meio, 416, DEFLATE, download anexado, ticket de outro nó, caminho
fora da pasta, membro inexistente).

# Extração temporária de mídia (galeria)

Botão **"Extrair temporariamente"** na aba **Galeria** da tela Arquivos do aparelho
(`arquivos.html?visao=galeria`). Materializa os vídeos e imagens da extração aberta numa pasta
temporária do servidor para resolver duas dores da galeria:

1. **Miniatura de vídeo.** A grade mostra como thumb o quadro que o **IPED** gerou ao processar
   o caso (tabela `thumbs` do `storage-N.db` ou `view/H0/H1/HASH.jpg` do `VideoThumbTask` — ver
   `funcoes/iped_cache.py`, `ler_thumb`). Vídeo sem quadro do IPED ficava com o ícone ▶ e nada.
   A extração temporária gera o quadro que falta por **ffmpeg** (`-frames:v 1`, 320 px) e a
   galeria passa a mostrá-lo.
2. **Carregamento do modal.** Sem a cópia, cada reprodução descomprime o binário sob demanda
   (gzip do storage IPED ou ZIP do `.ufdr`) e o player não tem seek. Com a cópia em disco, a
   rota serve a cópia com **206 por faixa** (`midia_temp.resposta_arquivo`) — o `FileResponse`
   deste Starlette ignora `Range`, e sem 206 o `.mov` do iPhone (átomo `moov` no fim do arquivo)
   baixa inteiro antes de começar a tocar.

## Como funciona

- `funcoes/midia_temp.py` é o dono de tudo: pasta, TTL, cópia e thumb. A rota de conteúdo
  (`GET /api/investigacao/arquivos/{id}/conteudo`, em `rotas/investigacao.py`) só consulta
  `midia_temporaria()` / `thumb_temporaria()` — dois `stat()` best-effort por requisição.
- O botão dispara `POST /api/investigacao/extracoes/{id_extracao}/midia-temporaria`, que abre um
  job **`midia_temporaria`** (`funcoes/jobs_utils.py`; não é pesado: é I/O de cópia e um frame
  por vídeo, não disputa a vaga de CLIP/Tesseract). Progresso em
  `GET /api/investigacao/jobs/{id_job}`, cancelável e com guard de job único por inquérito
  (segundo clique = 409). O front (`js/arquivos.js`) retoma o acompanhamento ao abrir a tela.
- O job varre `ARQ_TB01_ARQUIVOS` da extração (vídeos primeiro, maiores primeiro), copia cada
  binário pelos MESMOS resolutores da rota de conteúdo (ponteiro `IPED::`, ZIP aninhado da
  nuvem, membro do `.ufdr`) e grava `data/_midia_temp/<id_inquerito>/<id_extracao>/<id_arquivo>`
  — SEM extensão (o mime vem do banco; a busca vira um `stat` direto, não um glob de pasta)
  — cópia atômica (`.tmp` + `os.replace`). Para cada vídeo sem quadro do IPED nem thumb
  temporária, roda `ffmpeg -frames:v 1 -vf scale=320:-2` → `<id_arquivo>.thumb.jpg`.
- Escala: o `.ufdr` é aberto **uma única vez** por job (reler o diretório central a cada arquivo
  travou o primeiro job em produção, num aparelho com 212 mil mídias) e o progresso vai ao banco
  em lotes de 25. Images minúsculas ficam de fora: só entram imagens com ≥
  `MIDIA_TEMP_MIN_IMAGEM_KB` (padrão 100 KB) — ícone, figurinha e `.thumb` de WhatsApp copiam
  por dia e não melhoram a tela; vídeos não têm piso.

## Prova e expiração

- O original nunca é tocado: o `.ufdr`/caso IPED é aberto só em leitura; a cópia nasce depois,
  numa pasta fora da árvore da evidência e inalcançável pelo nginx.
- TTL de **4 h** (`MIDIA_TEMP_TTL_HORAS` no `.env`, piso 1): arquivo vencido é removido no
  primeiro acesso e uma varredura apaga vencidos e pastas vazias no início de cada job. A janela
  é **deslizante por uso**: a rota "toca" a cópia a cada requisição atendida, o que está em uso
  não vence no meio de uma reprodução.
- Sem `ffmpeg` na imagem (instalação antiga), a cópia acontece e os vídeos ganham velocidade,
  só não ganham miniatura nova.

## Limites

- O escopo é a **extração aberta** na tela (a pasta de cópia é por extração); áudio não entra —
  a dor é da galeria de vídeo/imagem.
- Vídeo cujo binário não existe no `.ufdr` (só catálogo/metadado) não tem o que copiar nem
  quadrar: continua com o ícone da grade.

- Documentos relacionados: `investigacao_celular.md` (tela Arquivos), `extracao_iped.md` e
  `extracao_java_iped.md` (de onde vêm as miniaturas nativas), `extração_ufdr.md` (o `.ufdr`
  como fonte dos binários).

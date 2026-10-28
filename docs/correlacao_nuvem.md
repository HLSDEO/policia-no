# Nuvem × aparelho (correlação)

## Visão geral

A tela **Nuvem × aparelho** procura o arquivo que **está na conta na nuvem e não está mais no
celular**.

O caso que ela resolve: o investigado apagou a foto, o vídeo ou o áudio do WhatsApp no aparelho
antes da apreensão. O Cellebrite recupera parte disso do espaço não alocado, mas o que o banco do
WhatsApp já tinha compactado se perde. A conta do Google, porém, guarda uma cópia — o backup
automático da galeria sobe as pastas `WhatsApp Images`, `WhatsApp Video` e `WhatsApp Voice Notes`
para o Google Fotos, e essa cópia **sobrevive ao apagamento no telefone**.

Ou seja: a mídia que interessa costuma estar viva na extração de nuvem
([extracao_iped.md](extracao_iped.md)) e ausente na do celular
([extração_ufdr.md](extração_ufdr.md)). Enquanto as duas extrações eram só duas listas de
arquivos, achar isso à mão significava abrir duas galerias de centenas de milhares de itens e
comparar de olho.

E o ganho que fecha o caso: o item da nuvem **tem binário** no `storage-N.db` do caso do IPED. A
tela não mostra só que o arquivo sumiu — mostra a foto que o investigado apagou, dentro da
conversa em que ela trafegou.

> **Por que não é a tela "Comparar celulares".** `comparar_celulares_utils.comparar_arquivos` já
> cruza `ARQ_TB01_ARQUIVOS` por MD5 entre extrações do mesmo inquérito, e a extração-ponte da
> Nuvem IPED (`NO_FABRICANTE = 'IPED'`) já entra lá como se fosse mais um aparelho. Mas aquela
> tela responde à pergunta oposta: acha o que está em **dois ou mais** lugares. Aqui a pergunta é
> o **anti-join** — o que está na nuvem e **não** está mais no celular. Ver
> [comparar_celulares.md](comparar_celulares.md).

## Fluxo de uso

1. Na tela Investigação, menu do inquérito → **☁️ Nuvem × aparelho** (ou pelo menu Navegar, em
   "Telas do inquérito").
2. O card **O que cruzar** lista as contas na nuvem de um lado e os aparelhos do outro. Por
   padrão entram todos; desmarcar restringe.
3. O card **Panorama da mídia do WhatsApp** mostra a classificação de toda mídia com nome
   canônico do WhatsApp existente na nuvem, em três quadros. Clicar num quadro filtra por ele;
   clicar de novo limpa.
4. Os filtros escolhem o **eixo do cruzamento** (nome/hash/tamanho), o estado, a categoria e a
   ordenação, e ligam/desligam "Só mídia do WhatsApp" e "Só o que subiu de um aparelho".
5. Cada resultado traz o selo de estado, o metadado que a nuvem guarda sobre o arquivo, o
   contexto da conversa em que ele trafegou (com deep-link para a mensagem) e o botão **Abrir da
   nuvem**, que serve o binário preservado.

## Como funciona

### Os três eixos, do mais forte ao mais circunstancial

**`nome`** (padrão) — o WhatsApp nomeia mídia de forma determinística, e esse nome **sobrevive ao
backup** para o Google Fotos:

```
IMG-AAAAMMDD-WA####.jpg   VID-AAAAMMDD-WA####.mp4   AUD-AAAAMMDD-WA####.opus
PTT-AAAAMMDD-WA####.opus  DOC-AAAAMMDD-WA####.pdf   STK-AAAAMMDD-WA####.webp
```

Um `IMG-20230715-WA0042.jpg` na nuvem sem par no celular **é** mídia de WhatsApp que saiu do
aparelho. É o eixo mais confiável para mídia de conversa, e o único que funciona quando o binário
foi reencodado no upload. O índice `IX203_ARQ_TB01_EXTRACAO_NOME_LOWER` (`(ID_EXTRACAO,
LOWER(NO_ARQUIVO))`) é o outro lado dessa junção — já existia, criado para outra coisa.

**`hash`** — anti-join por MD5. Prova forte, com uma limitação que a tela diz em voz alta: o
Google **reencoda** mídia no upload (Google Fotos em "economia de espaço"), então o MD5 da nuvem
difere do MD5 do celular para foto e vídeo. Funciona bem para PDF, documento e áudio (o WhatsApp
guarda `.opus` intacto); mal para foto reencodada. **Nesse eixo, "sem par no aparelho" não prova
ausência de foto.** Mesma ressalva de [comparar_celulares.md](comparar_celulares.md).

**`tamanho`** — casa `QT_BYTES` exato dentro da mesma categoria, acima de um piso de 8 KiB. É o
eixo circunstancial: serve para o anexo que o Cellebrite viu na conversa mas cujo binário não
recuperou (`WPP_TB05_ANEXOS` com `CO_CAMINHO_ZIP IS NULL`), quando nome e hash não têm o que
comparar. Espere falso positivo.

### Os três estados — o miolo do valor investigativo

Não é um booleano "está / não está":

| estado | como se detecta | o que significa |
|---|---|---|
| `apagado` | referência em `*_TB05_ANEXOS`, nenhuma linha em `ARQ_TB01` do aparelho | a mensagem prova que o arquivo existiu na conversa; **a nuvem é a única cópia** |
| `recuperado` | linha em `ARQ_TB01` do aparelho com `CD_STATUS_ARQUIVO = 'Deleted'` | o Cellebrite carveou; a nuvem **confirma** e entrega o binário íntegro |
| `so_nuvem` | nem referência | apagado antes do backup do banco do WhatsApp, ou nunca esteve neste aparelho |

O estado `presente` (o arquivo está íntegro nos dois lados) existe na classificação mas **nunca
aparece na tela**: ela é sobre o que não está mais no aparelho.

A separação entre `apagado` e `so_nuvem` é o que a mensagem órfã fornece — o registro do WhatsApp
continua no banco depois de o binário sumir, então o anexo sem arquivo é a prova de que ele
**esteve** no aparelho.

### Buraco na sequência: apagamento seletivo

`WA####` é sequencial por dia. Se o celular tem `WA0041` e `WA0043` do mesmo dia e tipo e o
`WA0042` só existe na nuvem, houve apagamento **seletivo** — frase muito mais forte num relatório
do que "arquivo ausente", porque descarta a hipótese de sincronização incompleta. É o selo
`in_lacuna`.

A lacuna é calculada em `_marcar_lacunas`, numa consulta separada e **só para as chaves
(tipo, data) da página em tela**: fazer isso no SQL principal exigiria um `EXISTS` por linha sobre
`substring(no_arquivo, …)`, que nenhum índice cobre. Por isso `in_lacuna` é **selo, não critério
de ordenação** — se ordenasse por ela, a ordem mudaria conforme a página.

### Guarda contra falso positivo

O arquivo da nuvem pode nunca ter estado *naquele* aparelho: outro celular da mesma conta, álbum
compartilhado por terceiro, anexo de e-mail. Dois amarradores, os dois vindos do sidecar que
[`nuvem_metadados.py`](../api/funcoes/nuvem_metadados.py) passou a gravar:

- `TP_ORIGEM_UPLOAD = 'Upload de aparelho'` (do `googlePhotosOrigin.mobileUpload`);
- `NO_PASTA_DISPOSITIVO` — a pasta de origem no telefone, e é ela que diz "veio de
  `WhatsApp Images`".

O filtro **"Só o que subiu de um aparelho"** exige os dois. Caso do IPED importado antes de a
leitura de sidecar existir não tem nenhuma linha em `NUV_TB01_METADADOS_ARQUIVO`; nesse caso o
filtro é **desabilitado** e a tela explica o motivo, em vez de devolver zero resultados sem dizer
por quê.

### Desempenho

O cruzamento é `LEFT JOIN LATERAL … LIMIT 1` por arquivo da nuvem, e não `NOT EXISTS`: o mesmo
passe que decide se há par também traz o caminho e o status da cópia no aparelho, que a tela
exibe. A ordenação do `LATERAL` (`ORDER BY (cd_status_arquivo = 'Deleted')`) faz o par **íntegro**
ganhar do carveado — se o celular ainda tem o arquivo inteiro não é caso de "sumiu"; a cópia
carveada é que interessa, como confirmação.

O universo é restrito a `NO_CATEGORIA IN ('Imagem','Vídeo','Áudio','Documento')` pelo mesmo motivo
de `comparar_celulares_utils`: sem isso o resultado vem soterrado em ícone e cache de app.

## Modelo de dados

Nenhuma tabela nova é criada por esta feature — ela consulta o que a extração já grava:

| Tabela | Papel |
|---|---|
| `ARQ_TB01_ARQUIVOS` | os dois lados do cruzamento; `CD_STATUS_ARQUIVO` distingue o carveado |
| `NUV_TB01_METADADOS_ARQUIVO` | o sidecar da nuvem — data de servidor, lixeira, compartilhamento e origem do upload (`db/init/29_nuvem.sql`, ver [extracao_iped.md](extracao_iped.md)) |
| `WPP_TB05_ANEXOS` / `SMS_TB05_ANEXOS` / `MSG_TB05_ANEXOS` | a referência órfã que separa `apagado` de `so_nuvem`, e o contexto da conversa |
| `INQ_TB02_EXTRACOES` | `NO_FABRICANTE = 'IPED'` é o que separa nuvem de aparelho |

A junção com o sidecar é por `(ID_EXTRACAO, LOWER(NO_ARQUIVO))` e não por chave estrangeira: o
sidecar é lido na mesma passada em que os arquivos entram por `COPY` em lote, e o `COPY` não
devolve `ID_ARQUIVO`.

## Configuração

Nenhuma variável de ambiente própria. A feature depende de o inquérito ter, ao mesmo tempo, uma
extração de nuvem concluída e uma extração de aparelho concluída — sem os dois lados a tela diz
que não há o que cruzar.

## Referências

- Funções: [`api/funcoes/correlacao_nuvem_utils.py`](../api/funcoes/correlacao_nuvem_utils.py) —
  `listar_fontes`, `resumo`, `correlacionar_arquivos`, `_sql_presenca_no_aparelho`,
  `_sql_referencia_em_conversa`, `_sql_chat_do_arquivo`, `_marcar_lacunas`.
- Rotas: [`api/rotas/correlacao_nuvem.py`](../api/rotas/correlacao_nuvem.py) —
  `GET …/correlacao-nuvem/fontes`, `/resumo`, `/arquivos`.
- Telas: `web/correlacao_nuvem.html`, `web/js/correlacao_nuvem.js`,
  `web/css/correlacao_nuvem.css` (o desenho dos cartões e chips vem de
  `web/css/comparar_celulares.css`).
- Relacionados: [extracao_iped.md](extracao_iped.md) (de onde vêm o sidecar e o binário da
  nuvem), [comparar_celulares.md](comparar_celulares.md) (a pergunta oposta),
  [extração_ufdr.md](extração_ufdr.md), [investigacao_celular.md](investigacao_celular.md)
  (inclusive o **WhatsApp parcial** — a mesma mídia da nuvem reconstruída como conversa).

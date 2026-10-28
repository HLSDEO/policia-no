# Compartilhar prova entre inquéritos

## Visão geral

O mesmo celular apreendido costuma ser prova em **mais de um inquérito** — a apreensão de um
flagrante que também interessa à investigação da organização criminosa, o aparelho do mesmo alvo
sendo trabalhado por duas equipes. Antes, a única saída era **reimportar o `.ufdr`** no segundo
inquérito: horas de passe do `report.xml`, hash de dezenas de GB, transcrição de áudio e indexação
semântica para produzir exatamente o que já estava no banco — e mais uma cópia do arquivo ocupando
o disco.

**Compartilhar prova** copia a extração já processada para outro inquérito. Leva minutos (a maior
parte é escrita de linha em banco), e o **arquivo não é duplicado**: a cópia aponta para o mesmo
`.ufdr` da origem.

Vale também para a **Nuvem IPED** (a pasta-caso processada pelo IPED é a "origem" no lugar do
`.ufdr`).

## Fluxo de uso

1. No card do aparelho, em **Investigações**, abrir o menu `⋮` → **Compartilhar prova** (entre
   "Hash" e "Transcrever áudios"). O item só aparece para quem administra o inquérito de origem —
   mesma régua de apagar e de transcrever.
2. Escolher o inquérito de destino. A lista traz os inquéritos que o usuário enxerga (o mesmo
   filtro de acesso das outras telas, então o isolamento por delegacia vale aqui também). O
   inquérito que **já tem esta prova** aparece desabilitado, com o motivo escrito ao lado — em vez
   de sumir da lista e deixar o investigador procurando.
3. **Copiar prova.** O aparelho aparece na hora no inquérito de destino com o badge
   *"Copiando prova: N%"*, e o andamento pode ser acompanhado de lá (ou fechando a página).
4. Terminada a cópia, o card do destino ganha o badge **"Prova de \<inquérito de origem\>"** —
   quem olha o inquérito sabe que aquele aparelho não foi apreendido ali, e de onde ele veio.

Parar a cópia no meio (menu `⋮` → **Parar cópia**) **desfaz tudo**: o aparelho não fica no
inquérito de destino. É diferente de parar uma extração, em que o que já foi lido do `.ufdr` é
preservado — meia prova passaria por prova inteira, e é o tipo de coisa que ninguém percebe
olhando a tela.

## O que vai junto e o que não vai

| Vai | Não vai |
|---|---|
| A extração, com contagens, hashes, datas e identidade do aparelho | Entidades e nós da **Mesa** |
| Conversas (WhatsApp/SMS/mensageiros), participantes, mensagens e anexos — com as transcrições | Pessoas canônicas identificadas **e conferidas** no inquérito de origem |
| Agenda, ligações, localizações, notificações, notas, navegação, apps, e-mails, arquivos, Wi-Fi, senhas, PIX e os demais domínios | **Achados do analista** e marcações de relatório |
| OCR, classificação de imagem e hashes por arquivo já calculados | Conversas de IA e detecções de padrões de crime |
| Os vetores da **busca semântica** das mensagens copiadas | Permissões (o destino tem as suas) |

A divisão é uma linha só: **o que é prova vai, o que é trabalho do analista fica**. Anotação é do
inquérito, não do aparelho — o que a equipe do inquérito A concluiu não entra no inquérito B como
se fosse dado extraído.

Terminada a cópia, a **identificação de pessoas** roda no inquérito de destino (a mesma chamada
que a ingestão faz no fim de uma extração de verdade) e materializa a Mesa de lá a partir do que
acabou de chegar, cruzando com o que o destino já tinha — o dono do aparelho que chegou pode ser
contato salvo no celular que já estava no inquérito.

## O arquivo `.ufdr` (e o que acontece se a origem for apagada)

`NO_ARQUIVO` da cópia é o **mesmo caminho** da origem, e é de lá que os dois inquéritos leem
anexos, fotos e áudios sob demanda. Consequências, todas verificadas:

- **Apagar a extração de origem, ou o inquérito inteiro de origem, não apaga o arquivo do disco**
  — nenhuma das duas operações mexe em `data/`. A cópia continua inteira e funcionando.
- Os dados da cópia são **linhas próprias**: apagar a origem não leva nada da cópia junto.
- Quem apagar o `.ufdr` **à mão** da pasta de dados tira os anexos dos dois inquéritos; os dois
  passam a mostrar o aviso *"Arquivo ausente"*, com o botão **Re-localizar arquivo** para apontar
  onde ele está agora (ver [organizacao_arquivos.md](organizacao_arquivos.md)).
- A **cadeia de custódia** é a mesma nos dois: hash idêntico, porque o arquivo é literalmente o
  mesmo (ver [hash_arquivos.md](hash_arquivos.md)). A cópia guarda ainda de qual extração e de
  qual inquérito ela veio, e quando — `ID_EXTRACAO_ORIGEM`, `NU_INQUERITO_ORIGEM` e
  `DT_COMPARTILHAMENTO` em `INQ_TB02_EXTRACOES`. O número do inquérito de origem é guardado **em
  texto** de propósito: continua aparecendo no card mesmo depois que a origem for apagada.

## Como funciona

`api/funcoes/compartilhar_prova.py`, disparado por
`POST /api/investigacao/extracoes/{id}/compartilhar` (a lista do seletor vem de
`GET /api/investigacao/extracoes/{id}/destinos-compartilhamento`).

1. **Conferências** e criação da linha nova em `INQ_TB02_EXTRACOES`, já no inquérito de destino,
   em `processando`/`copiando` — é ela que faz o card aparecer imediatamente. Recusa (409) a
   extração ainda em andamento, o destino que já tem a mesma prova (mesmo caminho ou mesmo
   SHA-256) e o próprio inquérito de origem.
2. **A cópia dos dados, numa transação só**, em segundo plano. Tudo ou nada: qualquer falha no
   meio desfaz a cópia inteira e a linha fica visível como `erro`, com a mensagem — nunca um
   aparelho pela metade parecendo completo.
3. **Fecha** como `concluido` e roda a identificação de pessoas do destino.

O plano de cópia se monta sozinho, e é aí que está o cuidado do módulo:

- as tabelas de domínio penduradas em `ID_EXTRACAO` são descobertas por **introspecção**
  (`information_schema`), então um domínio novo do parser entra na cópia sem ninguém lembrar de
  mexer aqui;
- as "netas" (participantes, mensagens, anexos, entradas de agenda, embedding de imagem) têm uma
  lista explícita, `_NETAS`, porque chegam pela linha do pai;
- os **ids são traduzidos**: cada tabela copiada com filhos ganha uma tabela temporária de
  de-para (`id_antigo` → `id_novo`, com os ids reservados por `nextval`), e o filho é inserido
  já apontando para o pai novo. Sem isso a mensagem copiada continuaria pendurada na conversa do
  **outro** inquérito;
- referência sem foreign key também é traduzida: as chaves **PIX** apontam para mensagem, conversa
  e contato por número solto, com a fonte (`wpp`/`sms`/`msg`) dizendo em qual conjunto de tabelas
  procurar (`_REFS_PIX`), e os vetores da busca semântica apontam para a mensagem por
  `TP_FONTE` + `ID_MENSAGEM`;
- **rede de segurança**: antes de copiar, `_conferir_cobertura` pergunta ao banco quem aponta
  para as tabelas que estão sendo copiadas. Tabela nova fora do plano **para a cópia** com
  mensagem legível, em vez de produzir uma prova incompleta em silêncio — é o mesmo espírito do
  `ForeignKeyViolation` de `apagar_extracao`.

> **Ao acrescentar um domínio ao parser:** coluna que aponta para outra linha do banco (id de
> conversa, de mensagem, de contato, de arquivo) precisa ser remapeada em
> `compartilhar_prova.py`. Copiada como está, ela aponta para a linha do inquérito de origem.

## Teste

`api/tools/teste_compartilhar_prova.py` roda contra o Postgres de verdade, copia uma extração já
existente para um inquérito de teste e **desfaz tudo no final**:

```bash
docker compose exec api-1 python /app/tools/teste_compartilhar_prova.py [id_extracao]
```

Ele cobra as três coisas que a feature promete: contagem igual tabela a tabela (inclusive netas e
vetores), nenhum ponteiro da cópia para linhas do inquérito de origem, e o mesmo `.ufdr` nos dois.

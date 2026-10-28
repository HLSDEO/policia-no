# IPEI — quebra de sigilo fiscal (Receita Federal)

## Visão geral

Deferida a quebra de sigilo fiscal, o que chega à autoridade policial não é um documento: é uma
**pasta**. Dentro dela, um **PDF por alvo** — a *Informação de Pesquisa e Investigação* (IPEI),
emitida pelo Nupei/Copei da Receita Federal — e, ao lado de cada PDF, uma **planilha de anexo**
com as tabelas que o relatório resumiu ("a planilha completa de todas as notas fiscais segue em
anexo", "a relação de contas da empresa encontra-se no anexo desse relatório").

A IPEI é a peça que responde à pergunta patrimonial: **quanto a pessoa declarou receber, quanto
movimentou de verdade, o que comprou e com quem se relaciona**. É a fonte que sustenta (ou
derruba) a hipótese de incompatibilidade entre renda e patrimônio — e, ao contrário do extrato
bancário, ela já vem cruzada com DIRPF, Dirf, e-Financeira, NFe, RENAVAM, CENSEC e DECRED.

O sistema reconhece esse par de arquivos no mesmo botão **Arquivo genérico** da tela de
importação, que já trata o inquérito do ePol e a declaração de IRPF, e levanta por **regex — sem
IA**, como no RIF, no IPL e no IRPF:

| Bloco | Conteúdo |
|---|---|
| cabeçalho | número da IPEI, data, unidade emissora, processo judicial, data da decisão que afastou o sigilo, conclusão e encaminhamentos, auditores signatários |
| alvos | o contribuinte analisado e seus dados cadastrais — pessoa física (CPF, mãe, nascimento, endereço, ocupação) ou jurídica (CNPJ, CNAE, capital social, abertura/baixa, contador) |
| vínculos | sócios e responsáveis legais das empresas do alvo, empregadores do CNIS, outorgantes/outorgados de procuração (CENSEC e RFB) e **quem transmitiu declaração do mesmo endereço MAC** |
| ano a ano | o consolidado anual: rendimentos declarados ou receita bruta, movimentação financeira, DECRED, notas fiscais, variação patrimonial e a **razão movimentação/rendimento** |
| fontes pagadoras | de onde veio e para onde foi o dinheiro declarado, por ano — nome, CNPJ/CPF, natureza e valor |
| contas | contas da e-Financeira com crédito, débito e saldo **mês a mês** |
| bens | bens declarados no IRPF (valor em 31/12), veículos do RENAVAM (placa e restrição), imóveis da DOI/Dimob, embarcações, aeronaves e criptoativos |
| notas fiscais | as NFe destinadas ao alvo — as do corpo do PDF e as da planilha de anexo |
| contatos | endereços, telefones, e-mails e MAC/IP levantados em outras bases da RFB |

**Dois sinais merecem atenção especial**, e por isso têm coluna própria e destaque na tela:

- **razão movimentação/rendimento** (`VL_RAZAO_MOV_REND`) — quanto a movimentação financeira supera
  o rendimento declarado no ano. É o número que o próprio relatório usa para dizer que algo não
  fecha; um salto de 1,47 para 94,23 entre dois anos é o tipo de coisa que ele existe para mostrar.
  A tela pinta de laranja a partir de 3× e de vermelho a partir de 10×.
- **MAC coincidente** — duas pessoas investigadas que transmitiram declaração de imposto da mesma
  máquina. É o achado mais forte da IPEI para ligar investigados entre si, e por isso entra como
  **vínculo** (`TP_RELACAO = 'mac_coincidente'`), não como texto solto.

O PDF e a planilha continuam valendo **também como documento de texto** anexado ao inquérito: mesmo
que o levantamento estruturado falhe, o texto extraído fica disponível para a IA e para a busca.

## O par PDF + planilha vira um card só

A planilha não é um documento à parte — é a continuação das tabelas do PDF. Se ela virasse um
segundo bloco no card do inquérito, o investigador teria dois cartões para a mesma quebra de
sigilo, um deles sem contexto nenhum.

A costura é pelo **número da IPEI**, que aparece no nome dos dois arquivos:

```
VT20230001 - LEONARDO MARTINS ZEKEL 14455873783 -assinado Todos.pdf
VT20230001 Anexo LEONARDO MARTINS ZEKEL 14455873783 - NOTA FISCAL 2018 a 2023.xlsx
```

`IPE_TB01_IPEIS` guarda os dois (`ID_DOCUMENTO` e `ID_DOCUMENTO_ANEXO`), e a **ordem de importação
não importa**:

- **PDF primeiro** — a IPEI é criada e, logo depois, `_absorver_anexo_pendente()` procura no
  inquérito um `.xlsx`/`.csv` com o mesmo número que ainda não esteja preso a nenhuma IPEI;
- **planilha primeiro** — ela entra como documento comum (não há a que se ligar) e é recolhida
  quando o PDF chega.

Uma planilha sem número de IPEI no nome, ou cujo número não bate com nenhuma IPEI do inquérito,
simplesmente segue sendo um documento comum — que é o tratamento que ela teria de qualquer forma.

Dá para jogar a pasta inteira de uma vez no campo de upload: cada IPEI se junta ao seu anexo.

## Fluxo de uso

1. Copiar os arquivos para `data/<DELEGACIA>/`, ou enviá-los pelo campo **Enviar do seu
   computador** da tela de importação (até 50 MB por arquivo).
2. Em **Nova importação → Nova importação de inquérito → Arquivo genérico**, informar o número do
   inquérito e escolher os arquivos. Não há um botão separado para IPEI: o sistema **reconhece o
   formato** e decide o que fazer.
3. A IPEI vira um **bloco no card do inquérito** (aba *Outros documentos*), ao lado das extrações
   de UFDR, dos RIFs, dos extratos do SIMBA, dos inquéritos em PDF e das declarações de IRPF. O
   bloco diz se veio só o PDF ou o par PDF + planilha.
4. O bloco leva à tela **Quebra de sigilo fiscal** (`ipei.html`), com o cabeçalho, a conclusão do
   relatório, indicadores (movimentação × rendimento, DECRED, bens), indícios automáticos
   clicáveis (razão mov./rend. alta, MAC coincidente, compras em NFe acima da renda) e nove abas —
   *Alvos*, *Vínculos*, *Rede de vínculos* (grafo dos vínculos, com clique que filtra ou leva à
   aba certa), *Ano a ano* (com gráfico movimentação × rendimento por ano), *Fontes pagadoras*,
   *Contas*, *Bens*, *Notas fiscais*, *Contatos*. As tabelas ordenam pelo cabeçalho, paginam
   (cliente), somam os totais do filtro no rodapé e cada linha tem 📄 para abrir o PDF/anexo
   original no ponto do registro; um filtro global por alvo vale para todas as abas, e aba +
   filtros ficam na URL (`js/utils/estado_url.js`) para voltar à mesma visão ou compartilhá-la.
5. No bloco também estão, como no RIF/SIMBA/IPL/IRPF:
   - **Prompt de IA específico** — abre uma conversa restrita **àquela IPEI**, sem acesso ao resto
     do inquérito (ver [chat_ia.md](chat_ia.md));
   - **Achados do analista** — o que já foi apurado sobre aquela quebra de sigilo, para a IA usar
     como contexto;
   - **Apagar** a IPEI (leva junto o PDF e a planilha).
6. Alvo, vínculo, conta, bem, nota fiscal e contato aceitam **📌**, virando nó da mesa
   (`tp_origem` `ipei_alvo`, `ipei_relacionado`, `ipei_conta`, `ipei_bem`, `ipei_nota`,
   `ipei_contato`) — ver [mesa_inquerito.md](mesa_inquerito.md).
7. O **painel flutuante de IA** (a bolha no canto, `js/utils/ia_flutuante.js`) acompanha a tela,
   já restrito a esta IPEI — o mesmo que rif.html e simba.html oferecem.

## O que a IPEI desenha na mesa de inquérito

Ler a tabela de vínculos linha a linha não é o que a investigação quer: o que ela quer é o
**desenho**. Por isso a identificação de pessoas (`funcoes/identificacao_pessoas.py`, a mesma que
já lê UFDR, RIF, SIMBA e IPL) condensa a IPEI em `fulano —vínculo→ entidade`:

- o **alvo** vira nó de pessoa (CPF) ou de empresa (CNPJ);
- cada **relacionado** vira nó — pessoa pelo CPF, empresa pelo CNPJ;
- o **tipo do vínculo** vira a aresta: `sociedade (IPEI)`, `vínculo empregatício (IPEI)`,
  `procuração/escritura (IPEI)`, `procuração perante a RFB (IPEI)` e
  `declaração do mesmo computador (IPEI)` — esta última **simétrica**, porque os dois lados estão
  no mesmo pé.

O **anexo** (a planilha) rende mais quatro desenhos, todos com documento dos dois lados:

- quem mais **figura na conta bancária** do alvo (`IPE_TB06_CONTAS`, colunas `NO_TITULAR`/
  `NU_TITULAR`/`TX_RELACAO`) vira nó e ganha a aresta `cotitular de conta (IPEI)`, simétrica —
  a mesma conta chega mês a mês, e o vínculo é um só;
- de quem o alvo **compra** e para quem **vende** por NF-e (`IPE_TB08_NOTAS_FISCAIS`): as notas
  são agregadas por contraparte, viram `comprou de (IPEI)` / `vendeu para (IPEI)` com o total
  movimentado e o período, e entram as **15 maiores por valor** de cada alvo — sem esse corte,
  posto de gasolina e supermercado afogariam o que interessa. O anexo identifica o emitente pela
  **raiz do CNPJ** (8 dígitos), que não identifica um estabelecimento: ela só vira aresta quando
  casa, sem ambiguidade, com um CNPJ completo que a própria IPEI trouxe nos vínculos — senão não
  dá para dizer com qual empresa o vínculo é, e um nó "empresa raiz" ficaria em paralelo ao nó
  que os vínculos societários já criaram;
- o **veículo** do RENAVAM (`IPE_TB07_BENS`, `TP_BEM` começando em `veiculo`) vira nó
  `TP_ENTIDADE = 'veiculo'` deduplicado pela **placa**, com a aresta `proprietário de (IPEI)`.
  Só entra com placa: a discriminação do bem é texto livre;
- o **telefone e o e-mail** do alvo em outras bases da RFB (`IPE_TB09_CONTATOS`) não viram nó
  nem aresta — viram **identificador do próprio alvo**. É o que faz o alvo da quebra de sigilo
  fiscal e o contato salvo no celular apreendido virarem uma pessoa só, já que até então a IPEI
  só trazia CPF para a fusão.

A IPEI foi a **primeira fonte a criar nó de empresa** (`TP_ENTIDADE = 'organizacao'`, deduplicado
pelo CNPJ; hoje o IRPF e a telefonia fazem o mesmo, pelo mesmo motivo): metade dos vínculos dela tem CNPJ de um lado, e sem o nó da empresa a sociedade entre dois
investigados na mesma empresa — o desenho que a investigação procura — ficaria invisível. Num
teste com duas IPEIs do mesmo caso, isso rendeu 20 pessoas, 11 empresas e 33 arestas, entre elas
quatro investigados ligados ao mesmo endereço MAC e três ligados pela mesma construtora.

## Os dois formatos que convivem na mesma pasta

O mesmo lote traz dois modelos de relatório, e os dois passam pelo mesmo código:

| | pessoa física | pessoa jurídica |
|---|---|---|
| alvos por PDF | um | vários (um bloco por empresa) |
| numeração das seções | `2.x` | `3.N.x` |
| tabelas pesadas | no corpo do PDF | empurradas para o anexo |
| texto do PDF | nome de glifo (`/uni0049/uni004E…`) | texto normal, justificado e silabado |

O que os une é o **esqueleto numerado das seções**. `_secoes()` reconstrói esse índice a partir do
texto e todo extrator trabalha dentro de uma seção, nunca sobre o PDF inteiro. E **um alvo é toda
seção que tem uma subseção "Dados Cadastrais"** — é isso que faz o mesmo código servir para um PDF
de uma pessoa e para um PDF de seis empresas.

## Como o PDF é lido

O extrator de PDF não devolve tabela: devolve as células empilhadas em ordem de leitura, com a
linha quebrada onde a coluna era estreita. Nada aqui é lido "por coluna". Cada armadilha abaixo já
custou uma linha errada durante o desenvolvimento, e todas estão cobertas por
`api/tools/teste_parser_ipei.py`:

| Armadilha | O que o parser faz |
|---|---|
| o gerador não escreve ToUnicode válido e a página inteira sai como `/uni0049/uni004E…` | `funcoes/pdf_texto.py` desfaz o nome de glifo antes de qualquer regex — e isso vale também para o texto que vai para a IA, em **todo** documento importado |
| uma linha de tabela começa por número igual a um título (`2018 38.848,70…`, `2 PARES LED CODE…`) | um título só é aceito se **desce um nível** do título corrente ou abre o **próximo** capítulo |
| célula vazia marcada com `-` ou `x` | conta como coluna na leitura posicional — sem isso todos os valores seguintes escorregam uma casa |
| a tabela emenda no parágrafo que vem logo abaixo dela | o registro é cortado na primeira sequência de **quatro palavras minúsculas seguidas** (isso é frase, não célula) |
| CNPJ partido na quebra de linha (`23.750.632/0001` + `-78`) | remendado antes de qualquer coisa |
| a palavra silabada na justificação (`CAR-` / `NEIRO`) | o hífen colado a letras dos dois lados some |
| a qualificação colada no nome (`…GRAOS LTDA Socio`, `LTDAAtiva`) | o nome em CAIXA ALTA termina onde aparece a primeira maiúscula seguida de minúscula |
| o mês colado na última letra do banco (`…ESPIRITO SANTO01/2018`) | o `MM/AAAA` é reconhecido sem fronteira de palavra à esquerda |
| a ordem das colunas da tabela de contas muda de relatório para relatório | o registro é cortado no primeiro valor monetário e cada campo é reconhecido **pela forma** (mês é `MM/AAAA`, conta é o bloco com mais dígitos, titulares é o número solto no fim) |
| duas células numéricas coladas (`AUTORIZACAO: 202300382824725` + `1,0000`) | a quantidade só é lida quando começa depois de um espaço; e nenhum valor acima de dez trilhões entra no banco |
| a linha que atravessa a virada de página | é lida do jeito que o extrator entregou — pode sair com uma coluna a menos; a linha crua fica em `TX_CONTEXTO` para conferência |

As datas da linha de vínculo (ingresso, retirada, situação cadastral) vêm **em ordem que varia de
tabela para tabela**: `DT_INICIO` é a primeira, e `DT_FIM` só recebe a última quando ela é
posterior à primeira — data de situação cadastral antiga não pode virar data de saída. A linha
crua fica em `TX_CONTEXTO` e aparece no *title* da célula de qualificação, na tela.

## Como a planilha é lida

Os cabeçalhos vêm do gerador de relatórios da Receita, são enormes ("Razao Social ou Nome do
emitente C01 - Emitente da NFe"), variam entre a planilha de pessoa física e a de pessoa jurídica e
mudam de aba para aba. Em vez de exigir um layout, **cada coluna é reconhecida por um pedaço
característico do cabeçalho**, e é o *conjunto* de colunas reconhecidas que diz o que a aba é —
notas fiscais, contas, movimentação ou RENAVAM. Aba que não for reconhecida como nenhuma das
quatro é ignorada aqui: ela continua indo inteira para a IA como texto do documento.

As linhas do anexo são distribuídas entre os alvos da IPEI pelo CPF/CNPJ que citam; o que não cita
ninguém reconhecível fica com o primeiro alvo — que, na IPEI de pessoa física, é o único que existe.

**Armadilha do reconhecimento por trecho, e o custo dela.** O prefixo `Emitente NFe : A01 (8 dig)`
abre também as colunas de ENDEREÇO do emitente (`... - End. Logradouro Atual`,
`... - Munic. Atual`), e como o casamento é por trecho contido, ele vencia a coluna certa
(`Emitente/Remetente C01`): o CNPJ do emitente era gravado como um logradouro — em produção, a
string `IBIRACU`. Hoje o trecho específico vem primeiro na lista **e** `_documento_emitente`
recusa o que não tem 8 ou 14 dígitos. As duas correções valem só para importações NOVAS: linha já
gravada não muda com correção de parser — para consertar o que já entrou, reimportar a IPEI.

Note ainda que a planilha dá a **raiz do CNPJ** (8 dígitos, `19111420`), não o CNPJ do
estabelecimento. Por isso a mesa de inquérito só usa o emitente quando essa raiz casa, sem
ambiguidade, com um CNPJ completo que a própria IPEI trouxe nos vínculos (ver abaixo).

## Banco de dados

`db/init/42_ipei.sql` (delta em `db/migracoes/0015_ipei.sql`). O nível do meio é o **alvo**: toda
tabela filha pendura em `ID_ALVO`, e não direto na IPEI.

| Tabela | Conteúdo |
|---|---|
| `IPE_TB01_IPEIS` | a IPEI: número, data, unidade, processo, conclusão, os dois documentos (PDF e planilha) e as contagens |
| `IPE_TB02_ALVOS` | o contribuinte analisado e seus dados cadastrais |
| `IPE_TB03_RELACIONADOS` | vínculos (`societario`, `empregaticio`, `procuracao`, `procuracao_rfb`, `mac_coincidente`) |
| `IPE_TB04_ANOS` | uma linha por ano-calendário de cada alvo |
| `IPE_TB05_FONTES` | fontes pagadoras e pagamentos declarados |
| `IPE_TB06_CONTAS` | conta bancária por mês (do PDF) e a relação de contas (do anexo) |
| `IPE_TB07_BENS` | bens declarados, veículos, imóveis, embarcações, aeronaves |
| `IPE_TB08_NOTAS_FISCAIS` | NFe, com `TP_ORIGEM` dizendo se veio do PDF ou do anexo |
| `IPE_TB09_CONTATOS` | endereços, telefones, e-mails e MAC/IP |

## Ferramentas da IA

No escopo `ipei` (conversa presa a uma IPEI), a IA tem `obter_ipei`, `buscar_relacionados_ipei`,
`buscar_anos_ipei`, `buscar_fontes_ipei`, `buscar_contas_ipei`, `buscar_bens_ipei`,
`buscar_notas_ipei` e `buscar_contatos_ipei` — e **só elas**: não enxerga outras quebras de sigilo,
RIFs, IPLs, extratos do SIMBA nem o conteúdo do aparelho (ver
[chat_ia.md](chat_ia.md)). Os nomes e documentos levantados aqui entram no dicionário de
anonimização do inquérito (`funcoes/anonimizacao.py`), que é o que impede o nome de um sócio ou de
um outorgante de sair em claro para o provedor de IA.

## Limites conhecidos

- **A IPEI não traz lançamento, traz montante mensal.** A e-Financeira informa quanto entrou e
  saiu de cada conta em cada mês, não quem pagou a quem. Para o extrato lançamento a lançamento é
  preciso a quebra de sigilo **bancário** — ver [simba.md](simba.md).
- **Seção que o relatório traz como "Nada consta" não vira linha nenhuma.** Um PDF curto, em que
  quase tudo é "Nada consta", entra com as tabelas vazias — isso é o conteúdo do documento, não
  falha de leitura.
- **Não há bloco de relatório de IPEI.** Como no IRPF, a quebra de sigilo fiscal ainda não entra no
  `.docx` gerado; o caminho para levar um achado ao relatório é o 📌 e o texto do analista.
- **O ofício de encaminhamento e a planilha de alvos que vêm na mesma pasta não são IPEIs** e
  seguem como documentos comuns, de propósito — a heurística de reconhecimento é conservadora.

## Teste

```bash
docker compose exec api-1 python /app/tools/teste_parser_ipei.py
```

Roda sem banco: monta uma IPEI sintética com as armadilhas da tabela acima e confere campo a campo.
Passando uma pasta como argumento, imprime também o que levantou de cada IPEI real que encontrar
ali — serve para conferir um lote novo antes de importar de verdade.

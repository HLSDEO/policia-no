# Declaração de IRPF (Receita Federal)

## Visão geral

O afastamento de sigilo fiscal devolve a **Declaração de Ajuste Anual** do investigado em PDF — o
Recibo/Declaração Completa exportado pelo programa oficial da Receita. É a peça que responde à
pergunta patrimonial da investigação: **o que a pessoa declarou ter, quanto declarou receber e de
quem**. Cruzada com o extrato do SIMBA e com o RIF, é o que sustenta (ou derruba) a hipótese de
incompatibilidade entre patrimônio e renda declarada.

O sistema reconhece esse PDF no mesmo botão **Extrair de PDF** que já trata o inquérito do ePol, e
levanta por **regex — sem IA**, como no RIF e no IPL — seis blocos:

| Bloco | Conteúdo |
|---|---|
| cabeçalho | identificação do contribuinte (CPF, nascimento, título eleitoral, endereço, contato, ocupação) e o resumo do exercício (rendimentos tributáveis, imposto devido, a restituir, saldo a pagar) |
| bens | declaração de bens e direitos — imóveis, veículos, contas, aplicações — com a situação em 31/12 do ano anterior e do ano atual |
| dependentes | quem consta como dependente (nome, CPF, relação, nascimento) |
| rendimentos | fontes pagadoras (pessoa jurídica) do titular e dos dependentes |
| dívidas | dívidas e ônus reais |
| doações | doações efetuadas e doações a partidos políticos/candidatos |

**Fora do escopo, de propósito**: atividade rural, renda variável/*day-trade* mês a mês, ganhos de
capital e fundos imobiliários — raros no perfil típico investigado, e cada um com um formato de
tabela próprio a manter.

O PDF continua valendo **também como documento de texto** anexado ao inquérito: mesmo que o
levantamento estruturado falhe, o texto extraído fica disponível para a IA e para a busca.

## Fluxo de uso

1. Copiar o PDF da declaração para `data/` (ou para a subpasta da delegacia).
2. Na tela de **Investigação**, no card do inquérito, clicar em **Extrair de PDF** e escolher o
   arquivo — o mesmo botão do IPL e dos documentos diversos. Não há um botão separado: o sistema
   **reconhece o formato** e decide o que fazer.
3. A declaração vira um **bloco no card do inquérito**, ao lado das extrações de UFDR, dos RIFs, dos
   extratos do SIMBA e dos inquéritos em PDF, com titular, CPF e exercício/ano-calendário.
4. O bloco leva à tela **Declaração de IRPF** (`declaracao_irpf.html`): cabeçalho com identificação
   e resumo, e cinco abas — **Bens e Direitos**, **Dependentes**, **Rendimentos**, **Dívidas**,
   **Doações**.
5. No bloco também estão, como no RIF/SIMBA/IPL:
   - **Prompt de IA específico** — abre uma conversa restrita **àquela declaração**, sem acesso ao
     resto do inquérito (ver [chat_ia.md](chat_ia.md));
   - **Achados do analista** — ver a ressalva em "Limites conhecidos", abaixo;
   - **Apagar** a declaração.
6. Bem, dependente, rendimento, dívida e doação aceitam **📌**, virando nó da mesa
   (`tp_origem` `irpf_bem`, `irpf_dependente`, `irpf_rendimento`, `irpf_divida`, `irpf_doacao`) —
   ver [mesa_inquerito.md](mesa_inquerito.md).

### Limites conhecidos

- **Não há bloco de relatório de IRPF.** Diferente do RIF (`rif_*`), do SIMBA (`bancario_*`) e do
  IPL (`ipl_*`), a declaração não entra no `.docx` gerado, e a tela não oferece "marcar para o
  relatório". O caminho para levar um achado ao relatório é o 📌 e o texto do analista.
- **"Achados do analista" no bloco de IRPF** ficou muito tempo sem gravar: o `CHECK`
  `CK06_ACH_TB01_TP_ESCOPO` de `ACH_TB01_ACHADOS_ANALISTA` não listava `'irpf'`, então salvar um
  achado ali estourava a *constraint*. Corrigido em `db/migracoes/0015_ipei.sql`, junto com a
  entrada do escopo `'ipei'` (ver [ipei.md](ipei.md)).

## Como funciona

Tudo em `api/funcoes/irpf_utils.py`.

### Reconhecimento

`parece_irpf(paginas)` — a 1ª página tem que trazer o timbre fixo do formulário
(`IMPOSTO SOBRE A RENDA - PESSOA FISICA`, `DECLARACAO DE AJUSTE ANUAL`, `EXERCICIO`,
`ANO-CALENDARIO`, comparados sem acento e em caixa alta) **mais um CPF**. Não sendo isso,
`analisar_irpf` devolve `None` e o arquivo segue como documento de texto comum.

Ao contrário do IPL, aqui **não há heurística de OCR**: o PDF da Receita nasce digital.

### A armadilha do formato

Esse é o ponto que explica quase todo o código do módulo: **o `pypdf` cola rótulo e valor sem
espaço nenhum entre eles**, e cola também uma célula de tabela na próxima. O texto extraído sai
como `CPF:000.000.000-00Nome:FULANO DE TAL...` e uma linha de tabela vira
`30.000,000,000,000,000,00CNPJ/CPF:00.000.000/0001-00`.

Consequências, todas deliberadas:

- **Toda extração ancora no texto literal do rótulo**, nunca em espaço ou `\b` — rótulo colado ao
  texto anterior não tem fronteira de palavra para o regex enxergar.
- **`_valores_colados`** separa uma sequência de valores monetários grudados porque cada valor
  termina em `,DD`: a vírgula final é o único ponto sem ambiguidade onde o próximo pode começar.
  Por isso o regex de valor proíbe zero de preenchimento antes de um grupo de milhar — sem essa
  restrição, o corte ficaria ambíguo entre dois pontos válidos.
- **`_campos_por_rotulo`** delimita cada campo pela **posição real** do próximo rótulo no texto, não
  pela ordem em que os rótulos foram declarados. Campo em branco (dois rótulos colados, sem valor
  entre eles) vira valor vazio em vez de "roubar" o texto do campo seguinte.
- **`_extrair_contato`** classifica telefone/celular/e-mail **pelo formato do valor**, não pelo
  rótulo que veio antes: quando um dos três está em branco, o stream do PDF não segue a ordem
  visual e o valor do celular aparece depois do rótulo "E-mail:".
- **`_limpar_nome`** corta o rabicho de 10+ dígitos que às vezes gruda no fim de um nome (um CPF sem
  pontuação) e o rótulo do campo seguinte colado sem separador.

### Recorte das seções e das linhas

- **`_secao(texto, inicio, fins)`** recorta entre o rótulo de início e o primeiro rótulo de fim
  encontrado; devolve vazio quando a seção não aparece ou diz "Sem Informações". Detalhe registrado
  no código: `DEPENDENTES` também aparece dentro de `…PELOS DEPENDENTES` (rendimentos), sempre com
  esse prefixo — excluído por *lookbehind*.
- **`_linhas_por_documento`** (rendimentos, doações) ancora em `CNPJ/CPF:` e lê os N valores
  imediatamente **antes** do rótulo — achado empírico: no texto do `pypdf` a leva de valores da
  linha sai antes do documento, não depois. Efeito colateral desejado: a linha **TOTAL**, que não
  tem `CNPJ/CPF:` na sequência, fica de fora sozinha, sem precisar filtrar por nome.
- **`_linhas_por_par_valores`** (bens, dívidas) usa o mesmo princípio: cada item termina nos **dois**
  valores colados — situação em 31/12 do ano anterior e do atual.

### Evolução patrimonial

`VL_BENS_ANO_ANTERIOR`/`ATUAL` e `VL_DIVIDAS_ANO_ANTERIOR`/`ATUAL` no cabeçalho são **calculados
somando as linhas** de `IRP_TB02`/`IRP_TB05`, não lidos do resumo em texto do PDF — o resumo dessa
seção saiu embaralhado nos testes. É a diferença entre os dois anos que dá a variação patrimonial
para comparar com a renda declarada.

### Gravação

`extrair_irpf(no_arquivo, id_inquerito, id_documento, id_usuario)` roda **logo depois** de
`anexar_documento_inquerito` (que já validou o nome do arquivo, mesmo caminho do IPL) e grava os
seis blocos. Devolve `None` se não for uma declaração. Procura o arquivo na raiz de `data/` e nas
subpastas de delegacia.

A rota `POST .../extrair-de-pdf` chama, na ordem: `extrair_inquerito` (IPL) → `extrair_irpf` →
e, **não sendo nenhum dos dois**, a análise do documento por IA. Um PDF é uma coisa só.

### Ferramentas de IA

Seis, em `api/funcoes/ia_investigacao.py`, todas aceitando `id_irpf` opcional para restringir a uma
declaração:

| Ferramenta | O que faz |
|---|---|
| `obter_declaracao_irpf` | cabeçalho: identificação, resumo do exercício e evolução patrimonial |
| `buscar_bens_direitos_irpf` | bens e direitos declarados |
| `buscar_dependentes_irpf` | dependentes (nome, CPF, relação) |
| `buscar_rendimentos_irpf` | fontes pagadoras do titular e dos dependentes |
| `buscar_dividas_irpf` | dívidas e ônus reais |
| `buscar_doacoes_irpf` | doações efetuadas e a partidos/candidatos |

Numa conversa com **escopo `irpf`**, `ferramentas_para_inquerito` deixa só essas seis e injeta o
`id_irpf` — a IA não consegue sair daquela declaração.

### Na mesa de inquérito

A declaração é a fonte mais barata de vínculo **com documento** que existe no inquérito: o
declarante diz o CPF dos dependentes, o CNPJ de quem lhe pagou e o de quem ele doou. A
identificação de pessoas (`funcoes/identificacao_pessoas.py`, `_coletar_irpf`/
`_materializar_irpf`) transforma isso em grafo:

- o **declarante** vira nó de pessoa com CPF, **telefone, celular e e-mail declarados** — e são
  esses três que valem aqui: até então o telefone do contribuinte só existia na mesa se ele
  aparecesse em algum aparelho, e é o telefone que funde o contribuinte com o contato salvo no
  celular apreendido;
- cada **dependente** vira nó com a aresta `dependente (IRPF)`. Dependente menor costuma vir sem
  CPF: aí a ponta vale pelo nome completo, como no IPL;
- cada **fonte pagadora** vira nó (empresa pelo CNPJ, pessoa pelo CPF) com a aresta
  `recebeu de (IRPF)`, e cada **beneficiário de doação** com `doou a (IRPF)` — as duas agregadas
  por documento, com o valor total, e limitadas às `_MAX_CONTRAPARTES_IRPF` (20) maiores por
  valor de cada declaração.

**Bens e dívidas ficam de fora de propósito**: ali a contraparte é texto livre ("apartamento na
rua X"), e nó tirado de discriminação de bem encheria a mesa de coisa que não se consegue
afirmar. O que a declaração tem de identificado vira aresta; o resto continua sendo lido nesta
tela. Ver `mesa_inquerito.md`.

## Modelo de dados

Schema `INVESTIGACAO`, migração `db/init/28_irpf.sql`:

| Tabela | Conteúdo |
|---|---|
| `IRP_TB01_DECLARACOES` | uma linha por declaração: `ID_INQUERITO`, `ID_DOCUMENTO` (o PDF em `INQ_TB04`), `NO_ARQUIVO`, identificação (`NO_TITULAR`, `NU_CPF`, `DT_NASCIMENTO`, `NU_TITULO_ELEITORAL`, endereço, contato, ocupação), `NU_EXERCICIO`/`NU_ANO_CALENDARIO`, resumo (`VL_RENDIMENTOS_TRIBUTAVEIS`, `VL_IMPOSTO_DEVIDO`, `VL_IMPOSTO_RESTITUIR`, `VL_SALDO_PAGAR`), evolução patrimonial (`VL_BENS_*`, `VL_DIVIDAS_*`), contadores `QT_*` e quem extraiu |
| `IRP_TB02_BENS_DIREITOS` | `NO_CODIGO` (grupo como consta no PDF), `TX_DISCRIMINACAO`, `VL_ANO_ANTERIOR`, `VL_ANO_ATUAL` |
| `IRP_TB03_DEPENDENTES` | `NO_DEPENDENTE`, `NU_CPF`, `TX_RELACAO`, `DT_NASCIMENTO` |
| `IRP_TB04_RENDIMENTOS` | `TP_ORIGEM` (`titular`\|`dependente`), `NO_FONTE_PAGADORA`, `NU_CNPJ_CPF`, `VL_RENDIMENTO` |
| `IRP_TB05_DIVIDAS` | `NO_CODIGO`, `TX_DISCRIMINACAO`, `VL_ANO_ANTERIOR`, `VL_ANO_ATUAL` |
| `IRP_TB06_DOACOES` | `TP_DOACAO` (`efetuada`\|`partido`), `NO_BENEFICIARIO`, `NU_CNPJ_CPF`, `VL_DOACAO` |

FKs `FK230`–`FK237`, índices `IX192`–`IX197` (todos por `ID_IRPF`, mais `ID_INQUERITO` em `TB01`).

`db/init/24_ia.sql` acrescenta `'irpf'` ao `CK05_IA_TB01_TP_ESCOPO`, permitindo prender
uma conversa de IA a uma declaração (`TP_ESCOPO = 'irpf'`, `ID_ESCOPO = ID_IRPF`).

## Configuração

Nenhuma variável de `.env` própria: usa a mesma pasta de dados (`PASTA_INVESTIGACAO`) e, para a
conversa restrita, o provedor de IA configurado em Configurações. Sem provedor, a extração
estruturada e as telas continuam funcionando — só o chat não.

## Referências

- `api/funcoes/irpf_utils.py` — `parece_irpf`, `analisar_irpf`, `extrair_irpf`, os auxiliares de
  formato (`_valores_colados`, `_campos_por_rotulo`, `_extrair_contato`, `_limpar_nome`,
  `_sem_identificador_colado`), o recorte (`_secao`, `_linhas_por_documento`,
  `_linhas_por_par_valores`, `_extrair_dependentes_secao`) e as consultas
  (`listar_irpfs_inquerito`, `obter_irpf`, `listar_*_irpf`, `apagar_irpf`,
  `obter_id_inquerito_de_irpf`).
- `api/rotas/investigacao.py` — `POST .../extrair-de-pdf`,
  `GET .../inqueritos/{id}/declaracoes-irpf`, `GET/DELETE .../declaracoes-irpf/{id_irpf}` e as
  cinco rotas de aba; `_exigir_acesso_via_irpf` (resolve o inquérito a partir do `id_irpf`).
- `api/funcoes/ia_investigacao.py` — as seis ferramentas; `api/funcoes/ia_utils.py` —
  `_TABELA_ESCOPO` (`'irpf'` → `IRP_TB01_DECLARACOES`).
- `api/funcoes/entidades_utils.py` — `_VERIFICACAO_ORIGEM` para os cinco `tp_origem` de IRPF.
- Telas: `web/declaracao_irpf.html` (JS: `js/declaracao_irpf.js`); o bloco no card em
  `web/js/investigacao.js` (`renderIrpfBloco`, `apagarIrpf`).
- Esquema: `db/init/28_irpf.sql`, `db/init/24_ia.sql`.
- Documentos relacionados: [documentos_diversos.md](documentos_diversos.md) (o "Extrair de PDF" e o
  IPL do ePol), [simba.md](simba.md) e [rif.md](rif.md) (o outro lado da apuração patrimonial),
  [chat_ia.md](chat_ia.md) (conversa restrita a um documento),
  [relatorio_word.md](relatorio_word.md).

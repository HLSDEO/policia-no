# Buscar entidades no material do inquérito

## Visão geral

Chegou uma lista de nomes — do inquérito do ePol, de uma interceptação, de uma reunião com a
promotoria — e a pergunta é sempre a mesma: **algum deles aparece no material do caso?** Fazer isso
à mão é abrir a tela do RIF e buscar nome por nome, abrir a do SIMBA, repetir, e repetir de novo na
agenda do celular, nas mensagens, no texto do documento anexado, no cadastro da linha da operadora.

A tela **Buscar entidades no material do inquérito** (`busca_entidades.html`) faz a varredura em
lote: recebe uma lista de termos — cada linha é **um único termo**: nome, CPF/CNPJ, telefone ou
**hash de custódia** (MD5/SHA-1/SHA-256), reconhecido por regex enquanto se digita — e devolve, por
termo, **onde ele é citado em tudo o que foi importado** — RIF, extrato do SIMBA, inquérito em PDF,
IRPF, IPEI, telefonia, documentos anexados, celular, contas na nuvem e a própria mesa de análise.
A busca também pode ser aberta direto do card do inquérito na página Investigações (barra de busca
rápida abaixo dos botões de ação), que chega aqui com `?inquerito=ID&q=<termo>` e dispara a busca
sozinha.

É consulta determinística — **sem IA** —, para que cada achado possa ser reconferido no documento
original.

> Até a versão anterior a tela se chamava "Buscar entidades em RIF e extrato bancário" e olhava
> seis lugares. O motor de agora é o mesmo para todas as fontes, e acrescentar uma é acrescentar
> uma entrada no registro `FONTES`.

## Fluxo de uso

1. Abrir a tela (aceita `?inquerito=ID` para já vir com o caso selecionado e `?q=<termo>` para
   buscar um termo direto — é o que a busca rápida do card da página Investigações usa) e escolher
   o inquérito.
2. Montar a lista: cada linha é **um termo único** — nome, CPF/CNPJ, telefone ou hash — e a tela
   mostra ao lado o tipo detectado por regex (hash hex de 32/40/64 dígitos → CPF/CNPJ de 11/14
   dígitos, com ou sem máscara → telefone de 8 a 13 dígitos → resto é nome). Linhas se acrescentam
   e se removem à vontade; **Colar lista…** aceita um termo por linha (ou `nome;CPF;telefone` na
   mesma linha, que vira uma entidade só — o que sai de uma coluna de planilha).
3. Escolher **como procurar** — com ou sem correspondências aproximadas — e **onde procurar**: um
   grupo de fontes por pílula. O grupo sem material no inquérito aparece desabilitado em vez de
   sumir, porque saber que o caso *não* tem extrato do SIMBA é parte da resposta.
4. **Buscar**. O resultado vem por entidade: um resumo com a contagem por grupo e, abaixo, um bloco
   por fonte (os três primeiros já abertos) com registro, de onde veio, **como casou** e o trecho
   do texto com a menção destacada.
5. O filtro **"só correspondências fortes"** esconde as pistas (nome parecido e menção no texto) e
   deixa só o que casou por documento, telefone ou nome exato.

## Como o casamento é feito

Cinco critérios independentes, sempre em `OR` dentro de cada fonte — quem sabe o CPF acha mesmo
com o nome grafado de outro jeito, e quem só tem o nome ainda acha:

| Critério | Como compara | Força |
|---|---|---|
| **hash** | MD5/SHA-1/SHA-256 por igualdade em hex minúsculo com o hash de custódia gravado na importação (arquivo da extração, documento anexado, arquivo do aparelho) — e por menção em texto livre | identificador |
| **documento** | CPF/CNPJ por igualdade, dos dois lados reduzidos a dígitos | identificador |
| **telefone** | pelos **últimos 8 dígitos**, que é o pedaço que não muda entre DDI 55, DDD e nono dígito | identificador |
| **nome exato** | igualdade depois de normalizar (minúscula, sem acento, pontuação virando espaço) | identificador |
| **nome aproximado** | `similarity()` do pg_trgm ≥ 0,42, nome contido no campo, ou todos os tokens presentes em qualquer ordem | pista |
| **citado no texto** | menção dentro de uma coluna de texto livre | pista |

Em coluna de **texto livre** (a análise que o banco escreveu no RIF, o corpo da mensagem, o OCR de
uma foto de documento) não há o que comparar por igualdade — procura-se a *menção*:

- o **modo exato** usa `ILIKE` com as variantes literais do termo (nome como digitado, CPF com e
  sem pontuação, telefone nas grafias usuais), o que aproveita os índices GIN trigram
  (`db/init/37_indices_busca_trgm.sql`);
- o **modo aproximado** acrescenta uma **expressão regular tolerante** a acento e a qualquer
  pontuação entre as palavras e entre os algarismos. É ela que encontra `JOÃO PEDRO\nDA SILVA` e
  `123.456.\n789-09` partidos pela largura fixa da linha do PDF — a mesma armadilha registrada em
  [rif.md](rif.md) e em `ipl_utils`. Ela **não usa índice**: é a parte cara da busca, e por isso
  não é aplicada às fontes marcadas como `pesada` (mensagens, arquivos, navegação, chamadas).

O **RIF é caso especial e deliberado**: além dos campos estruturados (envolvidos, contrapartes), a
busca varre `RIF_TB02_COMUNICACOES.TX_INFORMACOES_ADICIONAIS`, que é o **texto original** da
comunicação — é ali que o nome de quem depositou costuma estar quando o parser não conseguiu montar
uma tabela de contrapartes. O **PDF do RIF**, quando anexado, é varrido pelo grupo "Documentos
anexados" — é o mesmo `INQ_TB04_DOCUMENTOS` apontado por `RIF_TB01_RIFS.ID_DOCUMENTO_PDF`.

## Fontes

Cada fonte é uma entrada de `FONTES` em `api/funcoes/busca_entidades_utils.py`, com a tabela, o
caminho até o inquérito, as colunas de nome/documento/telefone/texto e as expressões que descrevem
a linha achada. Os grupos:

| Grupo | O que cobre |
|---|---|
| RIF (COAF) | envolvidos, comunicações (**texto original**), contrapartes, identificações do investigador, ocorrências |
| Extrato bancário (SIMBA) | contas, lançamentos, contrapartes consolidadas |
| Inquérito em PDF | pessoas citadas, achados no texto, peças, resumo dos fatos |
| Declaração de IRPF | declarante, dependentes, fontes pagadoras, doações, bens e dívidas |
| IPEI (patrimonial) | alvos, relacionados, fontes de renda, contas, notas fiscais, bens, contatos |
| Telefonia (operadora) | cadastro da linha, chamadas, números e IMEI |
| Documentos anexados | o texto inteiro do PDF/DOCX anexado ao inquérito + hash de custódia (MD5/SHA-1/SHA-256) |
| Celular / extração | agenda e suas entradas, WhatsApp/SMS/mensageiros (contatos, mensagens, transcrição de anexo), ligações, caixa postal, e-mails, notas, notificações, compromissos, chaves PIX, contas financeiras e de aplicativo, cartões, credenciais, autofill, donos, buscas, navegação, redes sociais, transferências, arquivos (nome, caminho, **OCR**, transcrição e **hash**), informações do aparelho, dispositivos reconhecidos, arquivo da extração (.ufdr, pelo hash de custódia) |
| Contas na nuvem | perfil, agenda, atividades, dispositivos, arquivos, identificadores de acesso |
| Análise do caso | entidades canônicas da mesa e seus identificadores, hipóteses do inquérito e suas evidências, comentários das entidades |

## Como funciona

Duas rotas em `api/rotas/investigacao.py`, ambas atrás de `exigir_acesso_inquerito`:

- `GET /api/investigacao/inqueritos/{id}/buscar-entidades/fontes` — quais grupos o inquérito tem
  (um `EXISTS` por grupo) e que fontes cada um cobre. É o que a tela desenha como pílulas.
- `POST /api/investigacao/inqueritos/{id}/buscar-entidades` — corpo
  `{entidades: [{nome, cpf_cnpj, telefone, hash}], aproximada, grupos, limite}` — cada item preenche
  só o campo do tipo que a regex da tela detectou.

A lógica está em `api/funcoes/busca_entidades_utils.py`. Pontos que valem registrar:

- **Uma consulta por fonte, não por fonte × entidade.** As entidades entram como um `VALUES` no
  CTE `alvos`, e o resultado diz qual delas casou. Uma lista de trinta nomes custa o mesmo que uma.
- **Grupo sem material não é consultado.** É o que impede a busca de varrer quarenta tabelas
  vazias — e o que faz uma busca num inquérito só de celular ser rápida.
- **Quatro fontes em paralelo** (`ThreadPoolExecutor`), cada uma com sua conexão do pool.
- **Teto de 50 linhas por fonte e por entidade** (`LIMITE_POR_FONTE`, máximo 200). O total real vem
  junto — `count()` sobre a janela —, então "1 de 214" continua sendo informação honesta.
- **Fonte que falha não derruba a busca.** Timeout de uma varredura pesada ou tabela de uma
  migração ainda não aplicada viram um aviso ("não foi possível consultar…") com o resto do
  resultado na tela.
- **O trecho vem recortado do servidor**, com a posição da menção já localizada; a tela só escapa o
  HTML e destaca. Sem escapar, um `<script>` dentro de uma mensagem de WhatsApp entraria na página.
- **`translate()` no lugar de `unaccent()`**: a extensão não está instalada (ver
  `db/init/01_schemas.sql`) e exigiria superusuário em toda instalação já existente.
- O acesso passa por `exigir_acesso_inquerito`, como toda rota de dado de inquérito — a busca nunca
  cruza inquéritos, nem os que o usuário não pode ver (ver
  [permissoes_delegacia.md](permissoes_delegacia.md)).

## Modelo de dados

Nenhuma tabela própria: só leitura das tabelas de cada fonte, no schema `INVESTIGACAO`. A lista de
entidades pesquisadas **não é persistida** — é um formulário, não um cadastro. Para fixar uma
pessoa no caso, o caminho é a entidade canônica da mesa ([mesa_inquerito.md](mesa_inquerito.md)).

## Configuração

Nenhuma variável de `.env`. As constantes de ajuste estão no topo de
`api/funcoes/busca_entidades_utils.py`: `LIMITE_POR_FONTE`, `LIMIAR_SIMILARIDADE` (0,42) e
`FONTES_EM_PARALELO`.

## Como acrescentar uma fonte

Uma entrada em `FONTES` — nada mais. O motor cuida do resto (predicados, trecho, contagem, limite):

```python
Fonte(
    chave="minha_fonte", rotulo="Grupo · o que é", grupo="celular",
    origem="investigacao.xyz_tb01_coisas d "
           "JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = d.id_extracao",
    onde="e.id_inquerito = %(inq)s",
    id_col="d.id_coisa", titulo="d.no_coisa", detalhe="d.tx_detalhe",
    local="coalesce(e.no_dispositivo, e.no_arquivo)", data="d.dt_coisa",
    nomes=("d.no_titular",), docs=("d.nu_cpf",), fones=("d.nu_telefone",),
    textos=("d.tx_observacao",),
)
```

Se o grupo for novo, acrescente-o também em `GRUPOS` e na consulta de disponibilidade de
`fontes_disponiveis()` (e no mapa de rótulos de reserva em `web/js/busca_entidades.js`).

## Referências

- `api/funcoes/busca_entidades_utils.py` — `FONTES`, `Fonte`, `preparar_alvo`, `buscar_entidades`,
  `fontes_disponiveis`.
- `api/rotas/investigacao.py` — `BuscaEntidadesIn`, `buscar_entidades_fontes`,
  `buscar_entidades_em_documentos`.
- Tela: `web/busca_entidades.html` (JS: `js/busca_entidades.js`, CSS: `css/busca_entidades.css`).
- Documentos relacionados: [rif.md](rif.md), [simba.md](simba.md),
  [convergencia.md](convergencia.md) (o cruzamento equivalente **entre** inquéritos, por
  identificador de entidade canônica), [mesa_inquerito.md](mesa_inquerito.md).

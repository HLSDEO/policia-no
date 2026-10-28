"""Contexto da tela de grafo no prompt — e o protocolo que deixa a IA propor um filtro.

Nas telas de grafo (grafo_rif.html, grafo_simba.html, mesa_inquerito.html) o painel flutuante de
IA manda, junto da pergunta, um retrato do painel de filtros daquela tela: quais campos existem,
de que tipo, com que opções válidas (a lista de bancos, por exemplo, só existe depois que os dados
carregam) e o que já está filtrado agora. É o `contexto_tela` do POST de mensagem, montado por
web/js/utils/grafo_filtros.js.

Aqui esse retrato vira um bloco do prompt de sistema, junto com a instrução de como responder um
pedido do tipo "me mostra as principais relações do fulano": achar os valores concretos com as
ferramentas de sempre e terminar a resposta com UM marcador

    [filtro:{"busca": "FULANO DE TAL", "credito_min": 10000}]

que o front transforma no botão "Aplicar no grafo". O marcador é só uma PROPOSTA: quem muda a tela
é o investigador, clicando — a IA não tem, e não deve ter, o poder de mexer no que ele está vendo.

Tudo o que chega aqui vem do navegador e entra num prompt, então nada é usado sem passar por
_saneado: página fora da lista conhecida, campo de tipo estranho ou texto gigante são descartados
em vez de irem para o modelo.
"""

from __future__ import annotations

# Páginas que têm grafo com filtros — o resto é descartado. Serve de trava contra um cliente
# adulterado tentando injetar texto arbitrário no prompt de sistema por este caminho.
PAGINAS_CONHECIDAS = {"grafo_rif", "grafo_simba", "mesa_inquerito", "caso_grafo"}

TIPOS_CAMPO = {"texto", "select", "data", "moeda", "numero", "bool", "lista"}

MAX_CAMPOS = 40
MAX_OPCOES_PROMPT = 40   # bancos de um extrato grande passam disso; o resto vira "e mais N"
MAX_TEXTO = 300
MAX_ROTULO = 120


def _txt(valor, limite: int = MAX_TEXTO) -> str:
    """Texto de uma linha, sem quebra e sem tamanho de sobra — o bloco é prompt, não payload."""
    if valor is None:
        return ""
    return " ".join(str(valor).split())[:limite]


def _valor_simples(valor):
    """Escalar ou lista curta de escalares; qualquer outra coisa (dict aninhado, etc.) é descartada."""
    if isinstance(valor, bool) or isinstance(valor, (int, float)):
        return valor
    if isinstance(valor, str):
        return _txt(valor, MAX_ROTULO)
    if isinstance(valor, list):
        return [_txt(v, MAX_ROTULO) for v in valor[:MAX_OPCOES_PROMPT] if isinstance(v, (str, int, float))]
    return None


def _saneado(ctx) -> dict | None:
    """Valida e recorta o que veio do navegador. Devolve None quando não dá para aproveitar nada."""
    if not isinstance(ctx, dict):
        return None
    pagina = _txt(ctx.get("pagina"), 40)
    if pagina not in PAGINAS_CONHECIDAS:
        return None

    campos = []
    for bruto in (ctx.get("campos") or [])[:MAX_CAMPOS]:
        if not isinstance(bruto, dict):
            continue
        nome = _txt(bruto.get("nome"), 40)
        tipo = _txt(bruto.get("tipo"), 20)
        if not nome or tipo not in TIPOS_CAMPO:
            continue
        campo = {
            "nome": nome,
            "tipo": tipo,
            "rotulo": _txt(bruto.get("rotulo"), MAX_ROTULO) or nome,
            "descricao": _txt(bruto.get("descricao")),
            "opcoes": [],
            "opcoes_truncadas": 0,
        }
        opcoes = bruto.get("opcoes") or []
        if isinstance(opcoes, list):
            for opcao in opcoes[:MAX_OPCOES_PROMPT]:
                if isinstance(opcao, dict):
                    valor = _txt(opcao.get("valor"), MAX_ROTULO)
                    rotulo = _txt(opcao.get("rotulo"), MAX_ROTULO)
                    if valor:
                        campo["opcoes"].append((valor, rotulo or valor))
                elif isinstance(opcao, (str, int, float)):
                    valor = _txt(opcao, MAX_ROTULO)
                    if valor:
                        campo["opcoes"].append((valor, valor))
            sobra = len(opcoes) - len(campo["opcoes"])
            if sobra > 0:
                campo["opcoes_truncadas"] = sobra
        campos.append(campo)

    if not campos:
        return None

    valores = {}
    brutos = ctx.get("valores")
    if isinstance(brutos, dict):
        nomes = {c["nome"] for c in campos}
        for chave, valor in list(brutos.items())[:MAX_CAMPOS]:
            chave = _txt(chave, 40)
            if chave not in nomes:
                continue
            simples = _valor_simples(valor)
            if simples is not None:
                valores[chave] = simples

    return {
        "pagina": pagina,
        "titulo": _txt(ctx.get("titulo"), MAX_ROTULO) or pagina,
        "descricao": _txt(ctx.get("descricao"), 400),
        "campos": campos,
        "valores": valores,
    }


_PROTOCOLO = (
    "\nCOMO FILTRAR A TELA: quando o pedido do investigador for para VER, ISOLAR, DESTACAR ou "
    "REDUZIR algo no grafo — 'me mostra as principais relações do fulano', 'só as entradas acima "
    "de 50 mil', 'esconde os não identificados', 'período de março' —, faça nesta ordem:\n"
    "1. Descubra os valores concretos com as ferramentas antes de propor qualquer coisa: o nome "
    "como está grafado nos dados, o banco, a faixa de valor que de fato separa o relevante do "
    "ruído. Não chute nome nem valor — filtro com nome errado devolve um grafo vazio.\n"
    "2. Diga em uma a três frases o que o recorte mostra e por que esses valores (é a análise que "
    "justifica o filtro — sem ela o investigador não tem como conferir seu raciocínio).\n"
    "3. Termine a resposta com UM marcador, sozinho na última linha:\n"
    '   [filtro:{"nome_do_campo": valor, ...}]\n'
    "Regras do marcador: JSON válido numa linha só; use apenas os campos listados acima, pelo "
    "'nome'; campo de tipo select aceita o valor OU o rótulo da lista de opções; data em "
    "AAAA-MM-DD; moeda como número puro (50000 ou 50000.75), sem 'R$' nem ponto de milhar; bool "
    "como true/false; lista como array JSON. Todo campo que você NÃO citar volta ao padrão — o "
    "marcador descreve a visão inteira que você está propondo, não um ajuste sobre o que já está "
    "filtrado. Para tirar todos os filtros use [filtro:{}]. No máximo um marcador por resposta.\n"
    "Se a pergunta não for sobre o que aparece na tela (um resumo, uma dúvida sobre um dado), "
    "responda normalmente e NÃO emita marcador.\n"
    "O marcador não muda a tela sozinho: ele vira um botão 'Aplicar no grafo' que o investigador "
    "clica se concordar. Então não escreva 'apliquei o filtro' nem 'a tela agora mostra' — "
    "escreva o que o filtro VAI mostrar quando ele clicar."
)


def bloco_contexto_tela(ctx) -> str:
    """Bloco de prompt com os filtros da tela de grafo aberta + o protocolo do marcador.

    Devolve "" quando não veio contexto de tela (o normal fora das telas de grafo) ou quando o
    que veio não passou na validação — nesses casos a conversa segue como sempre foi.
    """
    dados = _saneado(ctx)
    if not dados:
        return ""

    linhas = [
        "\n\nTELA ABERTA AO LADO DESTE CHAT: " + dados["titulo"] + ".",
    ]
    if dados["descricao"]:
        linhas.append(dados["descricao"])
    linhas.append(
        "O investigador está olhando esse grafo agora, e você pode propor um recorte dele. "
        "Filtros disponíveis nesta tela:"
    )

    for campo in dados["campos"]:
        linha = f"- {campo['nome']} ({campo['tipo']}) — {campo['rotulo']}"
        if campo["descricao"]:
            linha += ": " + campo["descricao"]
        linhas.append(linha)
        if campo["opcoes"]:
            opcoes = "; ".join(
                rotulo if rotulo == valor else f"{valor} = {rotulo}"
                for valor, rotulo in campo["opcoes"]
            )
            sufixo = f" (e mais {campo['opcoes_truncadas']})" if campo["opcoes_truncadas"] else ""
            linhas.append(f"    opções: {opcoes}{sufixo}")

    if dados["valores"]:
        ativos = "; ".join(f"{k} = {v}" for k, v in dados["valores"].items())
        linhas.append("Filtros ativos agora na tela: " + ativos + ".")
    else:
        linhas.append("Nenhum filtro ativo agora: a tela mostra o grafo inteiro.")

    return "\n".join(linhas) + "\n" + _PROTOCOLO

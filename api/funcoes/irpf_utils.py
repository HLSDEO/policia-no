"""Declaração de IRPF (Receita Federal) em PDF ("Extrair de PDF" na tela de Investigação, quando
o arquivo escolhido é o Recibo/Declaração Completa exportado pelo programa oficial da Receita).

Ao contrário do IPL (PDF escaneado, sujo de OCR), esse PDF nasce digital e segue um formulário
nacional fixo — mas o pypdf cola rótulo e valor SEM espaço nenhum entre eles
("CPF:101.383.374-03Nome:TATIANE ARAUJO DE SOUZA..."), então toda extração ancora no texto
literal do rótulo, não em espaço/word-boundary. Levantado por regex — sem IA, como no RIF e no
IPL:

    cabeçalho   · identificação do contribuinte + resumo (rendimentos, imposto)
    bens        · declaração de bens e direitos
    dependentes · quem consta como dependente
    rendimentos · fontes pagadoras (pessoa jurídica) do titular e dependentes
    dívidas     · dívidas e ônus reais
    doações     · doações efetuadas e a partidos políticos/candidatos

Fora do escopo, de propósito: atividade rural, renda variável/day-trade mês a mês, ganhos de
capital, fundos imobiliários — raras no perfil típico investigado.

Ver db/init/28_irpf.sql, rotas/investigacao.py e a página web declaracao_irpf.html.
"""

import re
import unicodedata
from pathlib import Path

from funcoes.db_utils import get_connection
from funcoes.entidades_utils import limpar_anotacoes_orfas
from funcoes.organizacao_arquivos import resolver as resolver_em_dados
from funcoes.pdf_texto import paginas_pdf

# ---------------------------------------------------------------------------
# Normalização de texto e valores
# ---------------------------------------------------------------------------

def _sem_acento(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto or "") if unicodedata.category(c) != "Mn")


def _chave(texto: str) -> str:
    return re.sub(r"\s+", " ", _sem_acento(texto)).upper().strip()


def _limpo(texto: str | None) -> str | None:
    texto = re.sub(r"\s+", " ", (texto or "")).strip(" :-")
    return texto or None


def _limpar_nome(texto: str | None) -> str | None:
    """Nome de pessoa/empresa antes de um valor ou CNPJ/CPF colado. Corta um rabicho de 10+
    dígitos colado no fim — o pypdf às vezes gruda um CPF sem pontuação bem depois do nome
    (ex.: 'TATIANE ARAUJO DE SOUZA010138337403') — e um rótulo colado sem valor depois, tipo
    'CPF:' (o rótulo do PRÓXIMO campo, sem separador nenhum antes dele)."""
    texto = re.sub(r"\d{10,}\s*$", "", texto or "")
    texto = re.sub(r"[A-Za-zÀ-ÿ]{2,25}:\s*$", "", texto)
    return _limpo(texto)


# Sem zero de preenchimento colado antes de um grupo de milhar (nunca '0.000,00' — um valor
# nesse formato só existe assim de verdade sem o separador, '0,00'). Essa restrição importa
# pra _sem_identificador_colado(): sem ela, o corte do código colado antes de um valor (ver
# abaixo) fica ambíguo entre dois pontos de corte válidos.
_RE_VALOR = re.compile(r"(?:[1-9]\d{0,2}(?:\.\d{3})+|\d{1,3}),\d{2}")
_RE_CPF = re.compile(r"\d{3}\.\d{3}\.\d{3}-\d{2}")
_RE_TELEFONE = re.compile(r"\(\d{2}\)\s*\d{4,5}-?\d{4}")
_RE_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_RE_DATA_BR = re.compile(r"(\d{2})/(\d{2})/(\d{4})")


def _para_float(valor_br: str) -> float:
    return float(valor_br.replace(".", "").replace(",", "."))


def _valores_colados(texto: str) -> list[float]:
    """Uma sequência de valores monetários brasileiros colados sem separador (o extrator do
    pypdf não deixa espaço entre uma célula e a próxima de uma linha de tabela) vira lista de
    floats — ex.: '30.000,000,000,000,000,00' -> [30000.0, 0.0, 0.0, 0.0, 0.0]. Funciona porque
    cada valor termina em ',DD': a vírgula final é o único ponto sem ambiguidade onde o próximo
    valor pode começar (o separador de milhar só aparece antes de um grupo de 3 dígitos logo
    após um '.', o que nunca se confunde com o início do próximo valor)."""
    return [_para_float(m.group(0)) for m in _RE_VALOR.finditer(texto)]


_RE_IDENTIFICADOR_COLADO = re.compile(r"(?<=\D)\d{6,17}(?=" + _RE_VALOR.pattern + r")")


def _sem_identificador_colado(texto: str) -> str:
    """Remove um código (CPF/CNPJ sem pontuação, 6-17 dígitos) colado bem antes do início de uma
    leva de valores monetários. Achado empírico contra o PDF de teste: um CPF sem pontuação
    termina em dígito, e se o valor logo depois também começa em dígito, o dígito final do CPF
    pode ser lido como parte do 1º valor — ex.: CPF '...740403' + '30.000,00' vira '...4404' +
    '030.000,00' em vez de '...740403' + '30.000,00'. Como \\d{6,17} é guloso com backtracking, a
    busca naturalmente encontra o comprimento certo: só "sobra" pro valor o que de fato bate com
    o formato de moeda logo depois do corte (por isso _RE_VALOR não aceita '0.000,00' — senão
    sobrariam dois pontos de corte válidos, e o guloso escolheria o errado)."""
    return _RE_IDENTIFICADOR_COLADO.sub("", texto)


def _data_iso(dia: int, mes: int, ano: int) -> str | None:
    from datetime import date

    try:
        return date(ano, mes, dia).isoformat()
    except ValueError:
        return None


def _data_br_para_iso(texto: str | None) -> str | None:
    m = _RE_DATA_BR.search(texto or "")
    return _data_iso(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


# ---------------------------------------------------------------------------
# Páginas e detecção
# ---------------------------------------------------------------------------

def _paginas_pdf(caminho: Path) -> list[str]:
    """Texto por página, já sem nome de glifo e sem caractere que o banco recusa
    (funcoes/pdf_texto.py)."""
    return paginas_pdf(caminho)


_MARCAS_IRPF = (
    "IMPOSTO SOBRE A RENDA - PESSOA FISICA", "DECLARACAO DE AJUSTE ANUAL", "EXERCICIO", "ANO-CALENDARIO",
)


def parece_irpf(paginas: list[str]) -> bool:
    """Heurística: a 1ª página traz o timbre fixo do Recibo/Declaração Completa da Receita mais
    um CPF. PDF nascido digital (não escaneado) — sem heurística de OCR, ao contrário do IPL."""
    if not paginas:
        return False
    cabecalho = _chave(paginas[0])
    return all(marca in cabecalho for marca in _MARCAS_IRPF) and bool(_RE_CPF.search(paginas[0]))


# ---------------------------------------------------------------------------
# Cabeçalho — identificação do contribuinte + resumo
# ---------------------------------------------------------------------------

# Rótulos da seção "IDENTIFICAÇÃO DO CONTRIBUINTE", na ordem em que aparecem no PDF. Cada um vira
# um delimitador: o valor de um campo é o texto entre o FIM do seu rótulo e o INÍCIO do próximo
# rótulo reconhecido (por posição real no texto, não pela ordem declarada aqui) — cobre o campo
# em branco no PDF (dois rótulos ficam colados, sem separador) sem precisar de um caractere que
# não existe. "CPF:" fica de fora de propósito: o mesmo rótulo aparece de novo no timbre repetido
# no topo de toda página, então o CPF do titular é lido direto por padrão (ver
# _extrair_identificacao) — os dois lugares sempre têm o mesmo valor, então não importa qual bate.
_ROTULOS_IDENTIFICACAO = [
    ("nome", r"Nome:"),
    ("nascimento", r"Data de Nascimento:"),
    ("titulo_eleitoral", r"T[ií]tulo Eleitoral:"),
    ("conjuge", r"Possui c[oô]njuge ou companheiro\(a\)\?"),
    ("alteracao_cadastral", r"Houve altera[cç][aã]o de dados cadastrais\?"),
    ("endereco", r"Endere[cç]o:"),
    ("numero", r"N[uú]mero:"),
    ("complemento", r"Complemento:"),
    ("bairro", r"Bairro/Distrito:"),
    ("municipio", r"Munic[íi]pio:"),
    ("uf", r"UF:"),
    ("cep", r"CEP:"),
    ("telefone", r"DDD/Telefone:"),
    ("natureza_ocupacao", r"Natureza da Ocupa[cç][aã]o:"),
    ("ocupacao_principal", r"Ocupa[cç][aã]o Principal:"),
    ("tipo_declaracao", r"Tipo de declara[cç][aã]o:"),
    ("recibo_anterior", r"N[ºo]\.?\s*do recibo da [uú]ltima declara[cç][aã]o entregue do exerc[íi]cio de\s*\d{4}:"),
    ("celular", r"DDD/Celular:"),
    ("email", r"E-mail:"),
    ("doenca_grave", r"Um dos declarantes [ée] pessoa com doen[cç]a grave[^?]{0,80}\?"),
    ("residente_exterior", r"Era residente no exterior[^?]{0,80}\?"),
]

# Prefixo confiável da seção "RESUMO" (rendimentos e imposto). A partir de "Informações
# Bancárias" o texto saiu embaralhado nos testes contra o PDF real (valores fora de ordem em
# relação aos rótulos) — ver comentário em _extrair_resumo.
_ROTULOS_RESUMO = [
    ("recebido_pj_titular", r"Recebidos de Pessoa Jur[íi]dica pelo Titular"),
    ("recebido_pj_dependentes", r"Recebidos de Pessoa Jur[íi]dica pelos Dependentes"),
    ("recebido_pf_titular", r"Recebidos de Pessoa F[íi]sica/Exterior pelo Titular"),
    ("recebido_pf_dependentes", r"Recebidos de Pessoa F[íi]sica/Exterior pelos Dependentes"),
    ("atividade_rural", r"Resultado tribut[áa]vel da Atividade Rural"),
    ("total_tributaveis", r"TOTAL DE RENDIMENTOS TRIBUT[ÁA]VEIS"),
    ("desconto_simplificado", r"Desconto Simplificado"),
    ("base_calculo", r"Base de c[áa]lculo do Imposto"),
    ("imposto_devido", r"Imposto devido"),
    ("imposto_retido_titular", r"Imposto retido na fonte do titular"),
    ("imposto_restituir", r"IMPOSTO A RESTITUIR"),
    ("saldo_pagar", r"SALDO IMPOSTO A PAGAR"),
    ("parcelamento", r"PARCELAMENTO"),
]


def _campos_por_rotulo(texto: str, rotulos: list[tuple[str, str]]) -> dict[str, str]:
    """Quebra texto em pares rótulo->valor a partir de uma lista de (chave, padrão do rótulo). O
    valor de cada campo é o texto entre o fim do seu rótulo e o início do próximo rótulo
    encontrado no texto (por posição real, não pela ordem declarada em `rotulos`) — assim um
    campo em branco (dois rótulos colados, sem valor entre eles) vira valor vazio em vez de
    "roubar" o texto do campo seguinte."""
    achados = []
    for chave, padrao in rotulos:
        m = re.search(padrao, texto)
        if m:
            achados.append((m.start(), m.end(), chave))
    achados.sort()
    campos: dict[str, str] = {}
    for i, (_, fim, chave) in enumerate(achados):
        proximo_ini = achados[i + 1][0] if i + 1 < len(achados) else len(texto)
        campos[chave] = texto[fim:proximo_ini].strip()
    return campos


def _extrair_contato(campos: dict) -> tuple[str | None, str | None, str | None]:
    """Telefone/celular/e-mail: quando um dos três vem em branco, o pypdf cola o valor no rótulo
    seguinte em vez do próprio (a ordem do stream não segue a ordem visual nessa trinca — visto
    no PDF de teste, onde o valor do celular saiu depois do rótulo "E-mail:", não do seu
    próprio). Por isso classifica pelo FORMATO do valor (e-mail tem '@', telefone bate o padrão
    (DD)XXXXX-XXXX) em vez de confiar em qual rótulo veio logo antes."""
    bruto = " ".join(filter(None, [campos.get("telefone"), campos.get("celular"), campos.get("email")]))
    m_email = _RE_EMAIL.search(bruto)
    telefones = _RE_TELEFONE.findall(bruto)
    email = m_email.group(0) if m_email else None
    celular = telefones[0] if telefones else None
    telefone = telefones[1] if len(telefones) > 1 else None
    return telefone, celular, email


def _extrair_identificacao(texto: str) -> dict:
    campos = _campos_por_rotulo(texto, _ROTULOS_IDENTIFICACAO)
    telefone, celular, email = _extrair_contato(campos)
    endereco = ", ".join(filter(None, [
        _limpo(campos.get("endereco")),
        (f"nº {n}" if (n := _limpo(campos.get("numero"))) else None),
        _limpo(campos.get("complemento")),
        _limpo(campos.get("bairro")),
    ]))
    m_cpf = _RE_CPF.search(texto)
    m_exercicio = re.search(r"EXERC[ÍI]CIO\s*(\d{4})", _chave(texto))
    m_ano_cal = re.search(r"ANO-CALEND[ÁA]RIO\s*(\d{4})", _chave(texto))
    return {
        "no_titular": _limpo(campos.get("nome")),
        "nu_cpf": m_cpf.group(0) if m_cpf else None,
        "dt_nascimento": _data_br_para_iso(campos.get("nascimento")),
        "nu_titulo_eleitoral": _limpo(campos.get("titulo_eleitoral")),
        "tx_endereco": endereco or None,
        "no_municipio": _limpo(campos.get("municipio")),
        "sg_uf": _limpo(campos.get("uf")),
        "nu_cep": _limpo(campos.get("cep")),
        "nu_telefone": telefone,
        "nu_celular": celular,
        "no_email": email,
        "tx_natureza_ocupacao": _limpo(campos.get("natureza_ocupacao")),
        "tx_ocupacao_principal": _limpo(campos.get("ocupacao_principal")),
        "tx_tipo_declaracao": _limpo(campos.get("tipo_declaracao")),
        "nu_exercicio": int(m_exercicio.group(1)) if m_exercicio else None,
        "nu_ano_calendario": int(m_ano_cal.group(1)) if m_ano_cal else None,
    }


def _extrair_resumo(texto: str) -> dict:
    """Só o prefixo confiável da seção RESUMO (rendimentos tributáveis e imposto). A partir de
    "Informações Bancárias" o texto saiu embaralhado nos testes contra o PDF real (valores fora
    de ordem em relação aos rótulos), então não tentamos ler banco/agência/conta nem o
    detalhamento de imposto pago dali. Os valores de bens/dívidas (evolução patrimonial) TAMBÉM
    não vêm daqui — são a soma das linhas de bens/dívidas (ver analisar_irpf), que é o dado que
    já estamos lendo estruturado, em vez de uma segunda leitura da mesma informação a partir de
    um texto que se mostrou pouco confiável nesse trecho."""
    campos = _campos_por_rotulo(texto, _ROTULOS_RESUMO)

    def valor(chave: str) -> float | None:
        m = _RE_VALOR.search(campos.get(chave, ""))
        return _para_float(m.group(0)) if m else None

    return {
        "vl_rendimentos_tributaveis": valor("total_tributaveis"),
        "vl_imposto_devido": valor("imposto_devido"),
        "vl_imposto_restituir": valor("imposto_restituir"),
        "vl_saldo_pagar": valor("saldo_pagar"),
    }


# ---------------------------------------------------------------------------
# Seções repetidas — dependentes, rendimentos, bens, dívidas, doações
# ---------------------------------------------------------------------------

def _secao(texto: str, rotulo_inicio: str, rotulos_fim: list[str]) -> str:
    """Recorta o texto entre o rótulo de início de uma seção do formulário e o primeiro entre os
    rótulos de fim (a próxima seção) — vazio se a seção não aparecer ou vier "Sem Informações"."""
    m_ini = re.search(rotulo_inicio, texto)
    if not m_ini:
        return ""
    fim = len(texto)
    restante = texto[m_ini.end():]
    for rotulo in rotulos_fim:
        m_fim = re.search(rotulo, restante)
        if m_fim:
            fim = min(fim, m_ini.end() + m_fim.start())
    trecho = texto[m_ini.end():fim].strip()
    return "" if _chave(trecho).startswith("SEM INFORMA") else trecho


_RE_CNPJ_CPF_ROTULO = re.compile(r"CNPJ/CPF:\s*([\d./-]{11,20})")


def _linhas_por_documento(texto: str, n_valores: int) -> list[dict]:
    """Linhas ancoradas em 'CNPJ/CPF:' (fonte pagadora de rendimento, beneficiário de doação):
    achado empírico contra o PDF de teste — a leva de N valores monetários colados de uma linha
    sai IMEDIATAMENTE ANTES do rótulo do documento no texto do pypdf, não depois (raw:
    '...30.000,000,000,000,000,00CNPJ/CPF:22.890.017/0001-02...'). A linha TOTAL (sem
    'CNPJ/CPF:' na sequência) fica de fora sozinha, sem precisar filtrar por nome."""
    linhas = []
    cursor = 0
    for m_doc in _RE_CNPJ_CPF_ROTULO.finditer(texto):
        antes = _sem_identificador_colado(texto[cursor:m_doc.start()])
        valores_match = list(_RE_VALOR.finditer(antes))
        cursor = m_doc.end()
        if len(valores_match) < n_valores:
            continue
        usados = valores_match[-n_valores:]
        linhas.append({
            "nome": _limpar_nome(antes[:usados[0].start()]),
            "documento": m_doc.group(1),
            "valores": [_para_float(m.group(0)) for m in usados],
        })
    return linhas


_RE_2_VALORES = re.compile(r"(?:" + _RE_VALOR.pattern + r"){2}")
_RE_CODIGO_ITEM = re.compile(r"^(\d{1,3})\s*-\s*")


def _linhas_por_par_valores(texto: str) -> list[dict]:
    """Bens e direitos / dívidas e ônus: cada item termina nos dois valores colados (situação em
    31/12 do ano anterior e do atual), mesmo princípio de _linhas_por_documento. NÃO verificado
    contra um PDF populado de verdade — a única amostra disponível não tinha nada nessas seções.
    Sem um rótulo como 'CNPJ/CPF:' para confirmar a fronteira, o corte por par de valores é a
    melhor aposta; se a ordem real divergir (valores antes vs. depois do texto do item), ajustar
    aqui depois de testar contra uma declaração real com bens/dívidas preenchidos."""
    texto = _sem_identificador_colado(texto)
    linhas = []
    cursor = 0
    for m_par in _RE_2_VALORES.finditer(texto):
        bloco = texto[cursor:m_par.start()].strip()
        cursor = m_par.end()
        if not bloco:
            continue
        valores = _valores_colados(m_par.group(0))
        m_cod = _RE_CODIGO_ITEM.match(bloco)
        linhas.append({
            "codigo": m_cod.group(1) if m_cod else None,
            "discriminacao": _limpo(bloco[m_cod.end():] if m_cod else bloco),
            "vl_ano_anterior": valores[0] if len(valores) > 0 else None,
            "vl_ano_atual": valores[1] if len(valores) > 1 else None,
        })
    return linhas


_RELACOES_DEPENDENTE = (
    "CONJUGE", "COMPANHEIRO", "COMPANHEIRA", "FILHO", "FILHA", "ENTEADO", "ENTEADA",
    "PAI", "MAE", "AVO", "NETO", "NETA", "IRMAO", "IRMA", "MENOR POBRE", "TUTELADO", "CURATELADO",
)


_RE_NOME_ROTULO = re.compile(r"Nome:\s*", re.IGNORECASE)


def _extrair_dependentes_secao(texto: str) -> list[dict]:
    """Cada dependente é ancorado pelo CPF — único dado garantidamente presente e sem ambiguidade
    de formato nessa seção. Nome = texto depois do rótulo "Nome:" mais próximo antes do CPF, se
    houver (sem isso, a data/relação do dependente ANTERIOR vaza pro nome do próximo, já que os
    dois ficam na mesma janela "entre um CPF e o outro"); relação e nascimento = melhor achado na
    janela até o próximo CPF. NÃO verificado contra uma declaração real com dependentes
    preenchidos."""
    cpfs = list(_RE_CPF.finditer(texto))
    linhas = []
    for i, m_cpf in enumerate(cpfs):
        inicio_busca = cpfs[i - 1].end() if i > 0 else 0
        bruto = texto[inicio_busca:m_cpf.start()]
        rotulos_nome = list(_RE_NOME_ROTULO.finditer(bruto))
        nome_bruto = bruto[rotulos_nome[-1].end():] if rotulos_nome else bruto
        fim_janela = cpfs[i + 1].start() if i + 1 < len(cpfs) else len(texto)
        janela = texto[m_cpf.end():fim_janela]
        chave_janela = _chave(janela)
        linhas.append({
            "nome": _limpar_nome(nome_bruto),
            "cpf": m_cpf.group(0),
            "relacao": next((r for r in _RELACOES_DEPENDENTE if r in chave_janela), None),
            "nascimento": _data_br_para_iso(janela),
        })
    return linhas


def _soma(linhas: list[dict], chave: str) -> float | None:
    valores = [linha[chave] for linha in linhas if linha.get(chave) is not None]
    return sum(valores) if valores else None


# ---------------------------------------------------------------------------
# Análise completa (sem banco) — usada na importação e testável isoladamente
# ---------------------------------------------------------------------------

def analisar_irpf(caminho: Path) -> dict | None:
    """Lê o PDF e devolve {cabecalho, bens, dependentes, rendimentos, dividas, doacoes}; None se
    não parecer uma declaração de IRPF (aí ele segue sendo só documento de texto para a IA)."""
    paginas = _paginas_pdf(caminho)
    if not parece_irpf(paginas):
        return None
    texto = "\n".join(paginas)

    bens = _linhas_por_par_valores(_secao(
        texto, r"DECLARA[ÇC][ÃA]O DE BENS E DIREITOS", [r"D[ÍI]VIDAS E [ÔO]NUS REAIS"]))
    dividas = _linhas_por_par_valores(_secao(
        texto, r"D[ÍI]VIDAS E [ÔO]NUS REAIS", [r"DOA[ÇC][ÕO]ES A PARTIDOS POL[ÍI]TICOS"]))
    # sem \b: rótulo colado ao texto anterior sem espaço não tem fronteira de palavra pro regex
    # enxergar (ver módulo de testes). "DEPENDENTES" sozinho também aparece dentro de "…PELOS
    # DEPENDENTES" (rendimentos), sempre com esse prefixo — excluído via lookbehind.
    dependentes = _extrair_dependentes_secao(_secao(texto, r"(?<!PELOS )DEPENDENTES", [r"ALIMENTANDOS"]))

    rendimentos = []
    for tp_origem, rotulo_ini, rotulos_fim in (
        ("titular", r"RENDIMENTOS TRIBUT[ÁA]VEIS RECEBIDOS DE PESSOA JUR[ÍI]DICA PELO TITULAR",
         [r"RENDIMENTOS TRIBUT[ÁA]VEIS RECEBIDOS DE PESSOA JUR[ÍI]DICA PELOS DEPENDENTES"]),
        ("dependente", r"RENDIMENTOS TRIBUT[ÁA]VEIS RECEBIDOS DE PESSOA JUR[ÍI]DICA PELOS DEPENDENTES",
         [r"RENDIMENTOS TRIBUT[ÁA]VEIS RECEBIDOS DE PESSOA F[ÍI]SICA"]),
    ):
        for linha in _linhas_por_documento(_secao(texto, rotulo_ini, rotulos_fim), 5):
            rendimentos.append({
                "tp_origem": tp_origem, "nome": linha["nome"], "documento": linha["documento"],
                "valor": linha["valores"][0] if linha["valores"] else None,
            })

    doacoes = []
    for tp_doacao, rotulo_ini, rotulos_fim in (
        ("efetuada", r"DOA[ÇC][ÕO]ES EFETUADAS", [r"DECLARA[ÇC][ÃA]O DE BENS E DIREITOS"]),
        ("partido", r"DOA[ÇC][ÕO]ES A PARTIDOS POL[ÍI]TICOS E CANDIDATOS A CARGOS ELETIVOS",
         [r"DEMONSTRATIVO DE ATIVIDADE RURAL"]),
    ):
        for linha in _linhas_por_documento(_secao(texto, rotulo_ini, rotulos_fim), 1):
            doacoes.append({
                "tp_doacao": tp_doacao, "nome": linha["nome"], "documento": linha["documento"],
                "valor": linha["valores"][0] if linha["valores"] else None,
            })

    cabecalho = {
        **_extrair_identificacao(texto),
        **_extrair_resumo(texto),
        "vl_bens_ano_anterior": _soma(bens, "vl_ano_anterior"),
        "vl_bens_ano_atual": _soma(bens, "vl_ano_atual"),
        "vl_dividas_ano_anterior": _soma(dividas, "vl_ano_anterior"),
        "vl_dividas_ano_atual": _soma(dividas, "vl_ano_atual"),
    }
    return {
        "cabecalho": cabecalho, "bens": bens, "dependentes": dependentes,
        "rendimentos": rendimentos, "dividas": dividas, "doacoes": doacoes,
    }


# ---------------------------------------------------------------------------
# Gravação — chamada logo depois de anexar o PDF como documento do inquérito
# ---------------------------------------------------------------------------

def extrair_irpf(no_arquivo: str, id_inquerito: int, id_documento: int | None,
                 id_usuario: int | None) -> dict | None:
    """Analisa o PDF de data/ e grava cabeçalho/bens/dependentes/rendimentos/dívidas/doações.
    Devolve o resumo da extração, ou None se o arquivo não for uma declaração de IRPF — nesse
    caso ele segue sendo só texto para a IA. Chamada logo depois de anexar_documento_inquerito,
    que já validou o nome do arquivo (mesmo padrão de extrair_inquerito, para o IPL)."""
    if not no_arquivo.lower().endswith(".pdf"):
        return None
    # no_arquivo é o caminho relativo dentro de data/ (…/<inquérito>/OUTROS/x.pdf) desde a
    # organização por inquérito; documento anexado antes disso guardou só o nome do arquivo
    caminho = resolver_em_dados(no_arquivo)
    if caminho is None:
        return None

    analise = analisar_irpf(caminho)
    if analise is None:
        return None

    cab = analise["cabecalho"]
    bens, dependentes = analise["bens"], analise["dependentes"]
    rendimentos, dividas, doacoes = analise["rendimentos"], analise["dividas"], analise["doacoes"]

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO investigacao.irp_tb01_declaracoes
                    (id_inquerito, id_documento, no_arquivo, no_titular, nu_cpf, dt_nascimento,
                     nu_titulo_eleitoral, tx_endereco, no_municipio, sg_uf, nu_cep, nu_telefone,
                     nu_celular, no_email, tx_natureza_ocupacao, tx_ocupacao_principal,
                     tx_tipo_declaracao, nu_exercicio, nu_ano_calendario,
                     vl_rendimentos_tributaveis, vl_imposto_devido, vl_imposto_restituir,
                     vl_saldo_pagar, vl_bens_ano_anterior, vl_bens_ano_atual,
                     vl_dividas_ano_anterior, vl_dividas_ano_atual,
                     qt_bens, qt_dependentes, qt_rendimentos, qt_dividas, qt_doacoes, id_usuario)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id_irpf
                """,
                (
                    id_inquerito, id_documento, caminho.name, cab["no_titular"], cab["nu_cpf"],
                    cab["dt_nascimento"], cab["nu_titulo_eleitoral"], cab["tx_endereco"],
                    cab["no_municipio"], cab["sg_uf"], cab["nu_cep"], cab["nu_telefone"],
                    cab["nu_celular"], cab["no_email"], cab["tx_natureza_ocupacao"],
                    cab["tx_ocupacao_principal"], cab["tx_tipo_declaracao"], cab["nu_exercicio"],
                    cab["nu_ano_calendario"], cab["vl_rendimentos_tributaveis"],
                    cab["vl_imposto_devido"], cab["vl_imposto_restituir"], cab["vl_saldo_pagar"],
                    cab["vl_bens_ano_anterior"], cab["vl_bens_ano_atual"],
                    cab["vl_dividas_ano_anterior"], cab["vl_dividas_ano_atual"],
                    len(bens), len(dependentes), len(rendimentos), len(dividas), len(doacoes),
                    id_usuario,
                ),
            )
            id_irpf = cur.fetchone()[0]

            for b in bens:
                cur.execute(
                    """
                    INSERT INTO investigacao.irp_tb02_bens_direitos
                        (id_irpf, no_codigo, tx_discriminacao, vl_ano_anterior, vl_ano_atual)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (id_irpf, b["codigo"], b["discriminacao"], b["vl_ano_anterior"], b["vl_ano_atual"]),
                )

            for d in dependentes:
                cur.execute(
                    """
                    INSERT INTO investigacao.irp_tb03_dependentes
                        (id_irpf, no_dependente, nu_cpf, tx_relacao, dt_nascimento)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (id_irpf, d["nome"], d["cpf"], d["relacao"], d["nascimento"]),
                )

            for r in rendimentos:
                cur.execute(
                    """
                    INSERT INTO investigacao.irp_tb04_rendimentos
                        (id_irpf, tp_origem, no_fonte_pagadora, nu_cnpj_cpf, vl_rendimento)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (id_irpf, r["tp_origem"], r["nome"], r["documento"], r["valor"]),
                )

            for dv in dividas:
                cur.execute(
                    """
                    INSERT INTO investigacao.irp_tb05_dividas
                        (id_irpf, no_codigo, tx_discriminacao, vl_ano_anterior, vl_ano_atual)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (id_irpf, dv["codigo"], dv["discriminacao"], dv["vl_ano_anterior"], dv["vl_ano_atual"]),
                )

            for doa in doacoes:
                cur.execute(
                    """
                    INSERT INTO investigacao.irp_tb06_doacoes
                        (id_irpf, tp_doacao, no_beneficiario, nu_cnpj_cpf, vl_doacao)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (id_irpf, doa["tp_doacao"], doa["nome"], doa["documento"], doa["valor"]),
                )
        conn.commit()

    return {
        "id_irpf": id_irpf, "no_titular": cab["no_titular"], "nu_cpf": cab["nu_cpf"],
        "qt_bens": len(bens), "qt_dependentes": len(dependentes),
        "qt_rendimentos": len(rendimentos), "qt_dividas": len(dividas), "qt_doacoes": len(doacoes),
    }


# ---------------------------------------------------------------------------
# Consultas (bloco no card + página declaracao_irpf.html) e remoção
# ---------------------------------------------------------------------------

def _linhas(cur) -> list[dict]:
    colunas = [d.name for d in cur.description]
    return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


def _consultar(query: str, params: tuple) -> list[dict]:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            return _linhas(cur)


def listar_irpfs_inquerito(id_inquerito: int) -> list[dict]:
    """Declarações de IRPF extraídas, para o bloco no card da investigação."""
    return _consultar(
        """
        SELECT p.id_irpf, p.id_documento, p.no_arquivo, p.no_titular, p.nu_cpf,
               p.nu_exercicio, p.nu_ano_calendario, p.vl_rendimentos_tributaveis,
               p.vl_imposto_devido, p.vl_imposto_restituir, p.vl_saldo_pagar,
               p.vl_bens_ano_anterior, p.vl_bens_ano_atual,
               p.vl_dividas_ano_anterior, p.vl_dividas_ano_atual,
               p.qt_bens, p.qt_dependentes, p.qt_rendimentos, p.qt_dividas, p.qt_doacoes,
               p.dt_extracao, u.no_usuario AS extraido_por,
               ap.no_apelido,
               -- hash do PDF de origem (cadeia de custódia), quando o documento o guarda
               d.co_hash_md5, d.co_hash_sha1, d.co_hash_sha256
        FROM investigacao.irp_tb01_declaracoes p
        LEFT JOIN seguranca.usr_tb01_usuarios u ON u.id_usuario = p.id_usuario
        LEFT JOIN investigacao.inq_tb04_documentos d ON d.id_documento = p.id_documento
        -- apelido livre do card (INQ_TB06_APELIDOS — ver menu "Mais ações" em investigacao.js)
        LEFT JOIN investigacao.inq_tb06_apelidos ap
               ON ap.tp_prova = 'irpf' AND ap.id_prova = p.id_irpf
        WHERE p.id_inquerito = %s
        ORDER BY p.dt_extracao DESC
        """,
        (id_inquerito,),
    )


def obter_irpf(id_irpf: int) -> dict | None:
    """Cabeçalho completo de uma declaração extraída — topo da página declaracao_irpf.html."""
    linhas = _consultar(
        """
        SELECT p.*, u.no_usuario AS extraido_por, i.nu_inquerito
        FROM investigacao.irp_tb01_declaracoes p
        LEFT JOIN seguranca.usr_tb01_usuarios u ON u.id_usuario = p.id_usuario
        LEFT JOIN investigacao.inq_tb01_inqueritos i ON i.id_inquerito = p.id_inquerito
        WHERE p.id_irpf = %s
        """,
        (id_irpf,),
    )
    return linhas[0] if linhas else None


def obter_id_inquerito_de_irpf(id_irpf: int) -> int | None:
    """Resolve o inquérito dono de uma declaração extraída, para checar permissão nas rotas."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id_inquerito FROM investigacao.irp_tb01_declaracoes WHERE id_irpf = %s", (id_irpf,))
            row = cur.fetchone()
            return row[0] if row else None


def listar_bens_irpf(id_irpf: int) -> list[dict]:
    return _consultar(
        """
        SELECT id_bem, no_codigo, tx_discriminacao, vl_ano_anterior, vl_ano_atual
        FROM investigacao.irp_tb02_bens_direitos WHERE id_irpf = %s ORDER BY id_bem
        """,
        (id_irpf,),
    )


def listar_dependentes_irpf(id_irpf: int) -> list[dict]:
    return _consultar(
        """
        SELECT id_dependente, no_dependente, nu_cpf, tx_relacao, dt_nascimento
        FROM investigacao.irp_tb03_dependentes WHERE id_irpf = %s ORDER BY no_dependente
        """,
        (id_irpf,),
    )


def listar_rendimentos_irpf(id_irpf: int) -> list[dict]:
    return _consultar(
        """
        SELECT id_rendimento, tp_origem, no_fonte_pagadora, nu_cnpj_cpf, vl_rendimento
        FROM investigacao.irp_tb04_rendimentos WHERE id_irpf = %s
        ORDER BY vl_rendimento DESC NULLS LAST
        """,
        (id_irpf,),
    )


def listar_dividas_irpf(id_irpf: int) -> list[dict]:
    return _consultar(
        """
        SELECT id_divida, no_codigo, tx_discriminacao, vl_ano_anterior, vl_ano_atual
        FROM investigacao.irp_tb05_dividas WHERE id_irpf = %s ORDER BY id_divida
        """,
        (id_irpf,),
    )


def listar_doacoes_irpf(id_irpf: int) -> list[dict]:
    return _consultar(
        """
        SELECT id_doacao, tp_doacao, no_beneficiario, nu_cnpj_cpf, vl_doacao
        FROM investigacao.irp_tb06_doacoes WHERE id_irpf = %s ORDER BY vl_doacao DESC NULLS LAST
        """,
        (id_irpf,),
    )


def apagar_irpf(id_irpf: int, apagar_documento: bool = True) -> bool:
    """Remove uma declaração extraída e suas linhas filhas. Por padrão leva junto o documento de
    texto que alimenta a IA — é o mesmo PDF, veio no mesmo clique. False se não existir."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id_documento FROM investigacao.irp_tb01_declaracoes WHERE id_irpf = %s", (id_irpf,))
            row = cur.fetchone()
            if row is None:
                return False
            id_documento = row[0]
            cur.execute("DELETE FROM investigacao.irp_tb02_bens_direitos WHERE id_irpf = %s", (id_irpf,))
            cur.execute("DELETE FROM investigacao.irp_tb03_dependentes WHERE id_irpf = %s", (id_irpf,))
            cur.execute("DELETE FROM investigacao.irp_tb04_rendimentos WHERE id_irpf = %s", (id_irpf,))
            cur.execute("DELETE FROM investigacao.irp_tb05_dividas WHERE id_irpf = %s", (id_irpf,))
            cur.execute("DELETE FROM investigacao.irp_tb06_doacoes WHERE id_irpf = %s", (id_irpf,))
            cur.execute("DELETE FROM investigacao.irp_tb01_declaracoes WHERE id_irpf = %s", (id_irpf,))
            # As anotações da mesa apontam para a linha por TP_ORIGEM/ID_ORIGEM, sem FK
            # (vínculo polimórfico): sem esta limpeza, a evidência do nó fica pendurada
            # num ID morto — e o ID é IDENTITY, então a próxima importação o reaproveita
            # e a evidência passa a apontar para OUTRO registro (ver limpar_anotacoes_orfas).
            limpar_anotacoes_orfas(cur, "irpf_declaracao", "irpf_bem", "irpf_dependente",
                                   "irpf_rendimento", "irpf_divida", "irpf_doacao")
            if apagar_documento and id_documento is not None:
                cur.execute("DELETE FROM investigacao.inq_tb04_documentos WHERE id_documento = %s", (id_documento,))
        conn.commit()
    return True

"""Inquérito Policial em PDF ("Extrair de PDF" na tela de Investigação).

Um IPL exportado do ePol é um PDF único com TODAS as peças do procedimento empilhadas
(portaria, ofícios, despachos, informações policiais, termos, petições…), cada folha
carimbada com "Fl. N" no rodapé. Fora o texto solto que já vai para a IA (ver
documentos_utils.anexar_documento_inquerito), dá para levantar deterministicamente o que o
investigador procura primeiro:

    peças    · o índice do inquérito — que documento está em qual folha
    pessoas  · quem é citado (CPF/CNPJ, vulgo, papel) e em que folha aparece
    achados  · telefones, e-mails, valores, contas, processos, tipificações, endereços

Tudo por regex sobre o texto do PDF — sem IA, como no RIF. As páginas digitalizadas (OCR)
perdem acento e ganham espaço no meio das palavras, então toda comparação de rótulo passa
por _chave() (sem acento, maiúscula) e, nos títulos de peça, também sem espaço nenhum.

Ver db/init/27_inquerito_pdf.sql, rotas/investigacao.py e a página web inquerito_pdf.html.
"""

import re
import unicodedata
from pathlib import Path

from funcoes.db_utils import get_connection
from funcoes.organizacao_arquivos import resolver as resolver_em_dados
from funcoes.pdf_texto import paginas_pdf

# ---------------------------------------------------------------------------
# Normalização de texto
# ---------------------------------------------------------------------------

def _sem_acento(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto or "") if unicodedata.category(c) != "Mn")


def _chave(texto: str) -> str:
    """Maiúscula, sem acento e com espaços colapsados — base de toda comparação de rótulo."""
    return re.sub(r"\s+", " ", _sem_acento(texto)).upper().strip()


def _colado(texto: str) -> str:
    """_chave() sem espaço nenhum. O PDF do ePol quebra palavras de título no meio
    ('DESP ACHO', 'P O R T A R I A'); colar tudo faz o título voltar a ser uma palavra só."""
    return re.sub(r"[\s.]+", "", _chave(texto))


def _limpo(texto: str) -> str:
    return re.sub(r"[ \t]+", " ", (texto or "")).strip()


def _linhas_uteis(texto: str) -> list[str]:
    return [l.strip() for l in (texto or "").split("\n") if l.strip()]


_MAX_ROTULO = 60  # "Valor a apurar:", "Assunto:" — linha curta terminada em ':' é campo, não frase


def _reagrupar(texto: str) -> str:
    """Remonta os parágrafos do texto do PDF.

    O extrator quebra a linha a cada mudança de fonte, e o inquérito é cheio de negrito no meio
    da frase — o resultado cru sai picotado ("...visa apurar o / potencial comprometimento de
    unidade policial especializada / , decorrente de suposto"). Quase toda quebra é artefato de
    layout: só sobrevivem a linha em branco (parágrafo de verdade) e a linha de rótulo."""
    saida: list[str] = []
    paragrafo = ""

    def _fechar():
        nonlocal paragrafo
        if paragrafo.strip():
            saida.append(paragrafo.strip())
        paragrafo = ""

    for bruta in (texto or "").split("\n"):
        linha = _limpo(bruta)
        if not linha:
            _fechar()
            if saida and saida[-1] != "":
                saida.append("")  # marca de parágrafo, preservada no join
            continue
        if len(linha) <= _MAX_ROTULO and linha.endswith(":"):
            _fechar()
            saida.append(linha)
            continue
        if not paragrafo:
            paragrafo = linha
        elif linha[0] in ",.;:)%":
            paragrafo += linha            # pontuação órfã cola sem espaço
        else:
            paragrafo += " " + linha
    _fechar()
    return "\n".join(saida).strip()


# ---------------------------------------------------------------------------
# Páginas e folhas
# ---------------------------------------------------------------------------

_RE_FOLHA = re.compile(r"\bFl\.?\s*(\d{1,4})\b", re.IGNORECASE)


def _paginas_pdf(caminho: Path) -> list[str]:
    """Texto por página, já sem nome de glifo e sem caractere que o banco recusa
    (funcoes/pdf_texto.py)."""
    return paginas_pdf(caminho)


def _folhas(paginas: list[str]) -> list[int]:
    """Nº da folha de cada página, lido do carimbo 'Fl. N' do rodapé; cai para a ordem da página.
    O último carimbo da página é o do rodapé — os do meio costumam ser citações a outras folhas."""
    numeros = []
    for i, texto in enumerate(paginas):
        achados = _RE_FOLHA.findall(texto)
        numeros.append(int(achados[-1]) if achados else i + 1)
    return numeros


# ---------------------------------------------------------------------------
# Detecção: este PDF é um inquérito?
# ---------------------------------------------------------------------------

_MARCAS_INQUERITO = (
    "INQUERITO POLICIAL", "IPL N", "AUTORIDADE POLICIAL", "DELEGADO DE POLICIA",
    "POLICIA FEDERAL", "POLICIA CIVIL", "DELEGACIA", "CODIGO DE PROCESSO PENAL",
)


def parece_inquerito(paginas: list[str]) -> bool:
    """Heurística: um inquérito tem folhas carimbadas ('Fl. N') e vocabulário de polícia judiciária.
    Conservadora de propósito — um PDF que não passe aqui vira só documento de texto, como antes."""
    todo = "\n".join(paginas)
    if len(_RE_FOLHA.findall(todo)) < 3:
        return False
    chave = _chave(todo)
    return sum(1 for marca in _MARCAS_INQUERITO if marca in chave) >= 2


# ---------------------------------------------------------------------------
# Datas
# ---------------------------------------------------------------------------

_MESES = {m: i + 1 for i, m in enumerate(
    ["JANEIRO", "FEVEREIRO", "MARCO", "ABRIL", "MAIO", "JUNHO", "JULHO",
     "AGOSTO", "SETEMBRO", "OUTUBRO", "NOVEMBRO", "DEZEMBRO"])}

_RE_DATA_EXTENSO = re.compile(r"(\d{1,2})\s+DE\s+([A-Z]+)\s+DE\s+(\d{4})")
_RE_DATA_BR = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
_RE_ASSINADO_EM = re.compile(r"ASSINAD[OA][^.\n]{0,40}?\bEM\s*(\d{1,2})/(\d{1,2})/(\d{4})")

# datas dentro de citação legal ("Lei 11.419, de 19 de dezembro de 2006") não são data da peça
_CITACAO_LEGAL = ("LEI", "DECRETO", "MEDIDA PROVISORIA", "MP ", "CODIGO", "CONSTITUICAO", "ADI", "SUMULA")


def _em_citacao_legal(chave: str, inicio: int) -> bool:
    return any(termo in chave[max(0, inicio - 90):inicio] for termo in _CITACAO_LEGAL)


def _data_iso(dia: int, mes: int, ano: int) -> str | None:
    from datetime import date

    try:
        return date(ano, mes, dia).isoformat()
    except ValueError:
        return None


def _achar_data(texto: str) -> str | None:
    """Data da peça: primeiro por extenso ('24 de fevereiro de 2026'), depois a da assinatura
    eletrônica, depois dd/mm/aaaa — sempre ignorando datas de citação legal."""
    chave = _chave(texto)
    for m in _RE_DATA_EXTENSO.finditer(chave):
        if _em_citacao_legal(chave, m.start()) or m.group(2) not in _MESES:
            continue
        iso = _data_iso(int(m.group(1)), _MESES[m.group(2)], int(m.group(3)))
        if iso:
            return iso
    m = _RE_ASSINADO_EM.search(chave)
    if m:
        iso = _data_iso(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if iso:
            return iso
    for m in _RE_DATA_BR.finditer(chave):
        if _em_citacao_legal(chave, m.start()):
            continue
        iso = _data_iso(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if iso:
            return iso
    return None


# ---------------------------------------------------------------------------
# Peças — o índice do inquérito
# ---------------------------------------------------------------------------

# (rótulo exibido, regex casada contra a LINHA de título colada por _colado()).
# Ordem importa: o primeiro que casar ganha, então o específico vem antes do genérico.
_TIPOS_PECA = (
    ("Portaria",             r"^PORTARIA"),
    ("Termo de apreensão",   r"^TERMODEAPREENSAO"),
    ("Termo de depoimento",  r"^TERMODE(DEPOIMENTO|DECLARACOES|INTERROGATORIO|QUALIFICACAO)"),
    ("Termo",                r"^TERMODE[A-Z]"),
    ("Auto",                 r"^AUTODE[A-Z]"),
    ("Despacho",             r"^DESPACHO"),
    ("Ofício",               r"^OFICIO(N|\d)"),
    ("Informação policial",  r"^INFORMACAO(POLICIAL|DEPOLICIAJUDICIARIA)"),
    ("Relatório",            r"^RELATORIO(FINAL|POLICIAL|DE)"),
    ("Representação",        r"^REPRESENTACAO"),
    ("Manifestação",         r"^MANIFESTACAO"),
    ("Registro de denúncia", r"^REGISTRODADENUNCIA"),
    ("Certidão",             r"^CERTIDAO"),
    ("Decisão",              r"^DECISAO"),
    ("Ofício",               r"^OF/"),
    ("Petição",              r"^(AOI?L(MO|USTRISSIMO)|AOEXCELENTISSIMO|AOEXMO)"),
    ("Requerimento",         r"^REQUERIMENTO"),
    ("Guia de depósito",     r"^GUIAPARADEPOSITO"),
    ("Comprovante",          r"^COMPROVANTEDE"),
    ("Mandado",              r"^MANDADODE"),
)

_LINHAS_CABECALHO = 18   # zona de título: só o topo da página conta como início de peça
_MAX_TITULO = 90         # título é linha curta; parágrafo que cita "portaria" não entra

_RE_NUM_PECA = re.compile(r"N?[º°O.]{0,3}\s*(\d{2,}\s*/\s*\d{4}|\d{2,}\s*[._]\s*\d{4})")
_RE_NUM_IPL = re.compile(r"\b(\d{4}\.\d{6,8})\b")


def _titulo_peca(linha: str) -> str | None:
    """Tipo da peça se a linha for um título; None se for texto corrido.
    'Petição' só vale em caixa alta — o mesmo vocativo em caixa mista é fecho de ofício."""
    if len(linha) > _MAX_TITULO:
        return None
    colada = _colado(linha)
    if len(colada) < 5:
        return None
    for tipo, padrao in _TIPOS_PECA:
        if not re.match(padrao, colada):
            continue
        if tipo == "Petição" and linha != linha.upper():
            continue
        return tipo
    return None


def _numero_peca(linhas: list[str], indice: int) -> str | None:
    """Número da peça a partir do título. O ePol quebra 'DESPACHO N°' e o número em linhas
    diferentes, então olha o título e as duas linhas seguintes — descartando o nº do próprio IPL."""
    for linha in linhas[indice:indice + 3]:
        chave = _chave(linha)
        if _RE_NUM_IPL.search(chave):
            continue
        m = _RE_NUM_PECA.search(chave)
        if m:
            return re.sub(r"[\s_]+", "", m.group(1)).replace(".", "/")
    return None


def _orgao_pagina(linhas: list[str]) -> str | None:
    """Timbre da página — a 1ª linha que pareça nome de órgão (e não um número de página solto)."""
    for linha in linhas[:4]:
        if len(linha) >= 12 and sum(c.isalpha() for c in linha) >= len(linha) * 0.6:
            return _limpo(linha)[:150]
    return None


def _extrair_pecas(paginas: list[str], nums_folha: list[int]) -> list[dict]:
    """Varre o topo de cada página atrás de um título de peça. Página sem título é continuação
    da peça anterior — é assim que a faixa de folhas de cada peça é fechada."""
    pecas: list[dict] = []
    for i, texto in enumerate(paginas):
        linhas = _linhas_uteis(texto)
        for j, linha in enumerate(linhas[:_LINHAS_CABECALHO]):
            tipo = _titulo_peca(linha)
            if tipo is None:
                continue
            numero = _numero_peca(linhas, j)
            anterior = pecas[-1] if pecas else None
            # mesma peça continuando na página seguinte (ex.: "REGISTRO DA DENÚNCIA" pág. 1 de 2):
            # o título repetido tem que ser o MESMO texto, senão duas peças do mesmo tipo em
            # folhas seguidas (Informação Policial 008 e 011) virariam uma só
            if (anterior and anterior["_titulo"] == _colado(linha) and anterior["nu_peca"] == numero
                    and anterior["_pagina_fim"] == i - 1):
                anterior["_pagina_fim"] = i
                break
            pecas.append({
                "tp_peca": tipo,
                "nu_peca": numero,
                "nu_folha_inicial": nums_folha[i],
                "no_orgao": _orgao_pagina(linhas),
                "_titulo": _colado(linha),
                "_pagina_ini": i,
                "_pagina_fim": i,
            })
            break
        else:
            if pecas:
                pecas[-1]["_pagina_fim"] = i

    for j, peca in enumerate(pecas):
        fim_pagina = pecas[j + 1]["_pagina_ini"] - 1 if j + 1 < len(pecas) else len(paginas) - 1
        inicio_pagina = peca["_pagina_ini"]
        peca["nu_folha_final"] = max(nums_folha[max(fim_pagina, inicio_pagina)], peca["nu_folha_inicial"])
        primeira = paginas[inicio_pagina]
        corpo = "\n".join(paginas[inicio_pagina:fim_pagina + 1])
        # a data e a assinatura da peça estão na folha de abertura; o resto do corpo é só reserva
        peca["dt_peca"] = _achar_data(primeira) or _achar_data(corpo)
        peca["no_assinante"] = _achar_assinante(primeira) or _achar_assinante(corpo)
        peca["tx_resumo"] = _resumo_peca(primeira, peca["no_orgao"])
        peca["qt_folhas"] = peca["nu_folha_final"] - peca["nu_folha_inicial"] + 1
        del peca["_pagina_ini"], peca["_pagina_fim"], peca["_titulo"]
    return pecas


_CARGOS = (r"Delegad[oa]|Escriv[ãa]o?|Agente\s+de\s+Pol[íi]cia|Perit[oa]|Corregedor|Promotor[a]?|"
           r"Procurador[a]?|Investigador[a]?|Papiloscopista|Subsecret[áa]ri[oa]|Superintendente|"
           r"Oficial\s+Investigador|Coordenador[a]?")
_RE_ASSINANTE = re.compile(
    r"assinad[oa]\b[^\n]{0,90}?\bpor\s+([A-ZÀ-Ü][A-ZÀ-Üa-zà-ÿ .'-]{6,70}?)\s*(?:,|\n|$)", re.IGNORECASE)
_RE_ASSINANTE_CARGO = re.compile(
    r"assinad[oa]\b[^\n]{0,90}?\bpor\s+([A-ZÀ-Ü][A-ZÀ-Üa-zà-ÿ .'-]{6,70}?)\s*,\s*(" + _CARGOS + r")",
    re.IGNORECASE)


def _achar_assinante(texto: str) -> str | None:
    plano = re.sub(r"[ \t]+", " ", texto)
    m = _RE_ASSINANTE_CARGO.search(plano) or _RE_ASSINANTE.search(plano)
    return _limpo(m.group(1)).strip(" ,.")[:150] if m else None


_RUIDO_RESUMO = ("ENDERECO:", "CEP:", "PAGINA", "DOCUMENTO ELETRONICO", "ASSINADO ELETRONICAMENTE",
                 "ACESSO RESTRITO", "CODIGO VERIFICADOR")


def _resumo_peca(pagina: str, no_orgao: str | None) -> str | None:
    """Primeiro parágrafo de corpo da peça (pulando timbre/cabeçalho), para dar contexto na lista.
    Roda sobre o texto já reagrupado — senão o "trecho de abertura" sai cortado no meio da frase,
    onde por acaso o negrito terminou."""
    for paragrafo in _reagrupar(pagina).split("\n"):
        if len(paragrafo) < 60 or _titulo_peca(paragrafo):
            continue
        if no_orgao and paragrafo.startswith(no_orgao):
            continue
        if any(t in _chave(paragrafo) for t in _RUIDO_RESUMO):
            continue
        # timbre e título são caixa alta; corpo de peça é prosa — exige minúscula de verdade
        letras = [c for c in paragrafo if c.isalpha()]
        if letras and sum(c.islower() for c in letras) / len(letras) < 0.5:
            continue
        return paragrafo[:400]
    return None


# ---------------------------------------------------------------------------
# Pessoas citadas
# ---------------------------------------------------------------------------

# CPF/CNPJ tolerando o espaço que o OCR enfia antes do hífen ('055.194.947 -38')
_RE_CPF = re.compile(r"\b(\d{3}\.\s?\d{3}\.\s?\d{3}\s?-\s?\d{2})\b")
_RE_CNPJ = re.compile(r"\b(\d{2}\.\s?\d{3}\.\s?\d{3}\s?/\s?\d{4}\s?-\s?\d{2})\b")
_RE_VULGO = re.compile(r"\bvulgo\s+([A-ZÀ-Ü][A-Za-zÀ-ÿ]{2,}(?:\s+[A-ZÀ-Ü][A-Za-zÀ-ÿ]{2,}){0,2})")

_CONECTORES = {"DE", "DA", "DO", "DAS", "DOS", "E", "DI", "DU", "VAN", "VON", "DELLA", "DEL"}
_RUIDO_NOME = {"CPF", "CNPJ", "NOME", "TITULAR", "SACADORA", "SACADOR", "REQUERENTE", "DECLARANTE",
               "SENHOR", "SENHORA", "SR", "SRA", "DR", "DRA", "EXMO", "ILMO", "FL", "RG", "ID"}

# papel/qualificação procurada na vizinhança da citação, da mais específica para a mais genérica
_PAPEIS = (
    ("Delegado de Polícia Federal", ("DELEGADO DE POLICIA FEDERAL", "DELEGADA DE POLICIA FEDERAL", "DPF ")),
    ("Escrivão de Polícia Federal", ("ESCRIVAO DE POLICIA", "ESCRIVA DE POLICIA")),
    ("Agente/Policial Federal",     ("AGENTE DE POLICIA FEDERAL", "APF ", "POLICIAL FEDERAL")),
    ("Policial civil",              ("POLICIAL CIVIL", "INVESTIGADOR DA POLICIA", "AGENTE DE POLICIA CIVIL",
                                     "OFICIAL INVESTIGADOR", "DENARC")),
    ("Membro do Ministério Público", ("PROMOTOR DE JUSTICA", "PROCURADOR", "GAECO")),
    ("Corregedor",                  ("CORREGEDOR",)),
    ("Advogado",                    ("ADVOGAD", "OAB", "PATRONO", "DEFESA CONSTITUIDA")),
    ("Investigado",                 ("INVESTIGADO", "INDICIADO", "SUSPEITO", "ACUSADO", "ALVO")),
    ("Preso/conduzido",             ("PRESO", "CONDUZIDA", "CONDUZIDO", "ABORDADA", "ABORDADO", "DETIDO")),
    ("Declarante",                  ("DECLARANTE", "REQUERENTE", "PETICIONANTE")),
    ("Titular de conta",            ("TITULAR DA CONTA", "TITULARIZADA POR", "TITULARIZADAS POR")),
    ("Sacador(a)",                  ("SACADORA", "SACADOR")),
    ("Testemunha",                  ("TESTEMUNHA",)),
    ("Vítima",                      ("VITIMA", "OFENDIDO")),
    ("Familiar",                    ("GENITOR", "GENITORA", "ESPOSA", "CONJUGE", "MARIDO", "FILHA", "FILHO",
                                     "COMPANHEIR")),
    ("Liderança criminosa",         ("LIDERANCA", "LIDER DO", "CHEFE DA FACCAO", "FACCAO")),
)


def _nome_antes(trecho: str) -> str | None:
    """Lê o nome imediatamente anterior a um CPF/CNPJ, andando de trás para frente pelos tokens
    enquanto eles parecerem parte de nome próprio ('...por Eduardo Tadeu Ribeiro da Cunha, CPF')."""
    # corta o rótulo do documento que fica entre o nome e o número
    trecho = re.sub(
        r"[\s(,;:.]*(?:portador(?:a)?\s+d[oe]\s+|inscrit[oa]\s+n[oa]\s+)?(?:CPF|CNPJ)?[\s.:/nºn°-]*$",
        "", trecho, flags=re.IGNORECASE)
    tokens = _limpo(trecho).split(" ")
    nome: list[str] = []
    for token in reversed(tokens[-9:]):
        limpo = token.strip(".,;:()[]\"'")
        if not limpo:
            break
        chave = _chave(limpo)
        eh_nome = (re.fullmatch(r"[A-ZÀ-Ü][A-Za-zÀ-ÿ'´-]{1,}", limpo) or re.fullmatch(r"[A-ZÀ-Ü]{2,}", limpo))
        if chave in _CONECTORES and nome:
            nome.append(limpo)
            continue
        if not eh_nome or chave in _RUIDO_NOME or chave in _CONECTORES:
            break
        nome.append(limpo)
    while nome and _chave(nome[-1]) in _CONECTORES:
        nome.pop()
    nome.reverse()
    proprios = [t for t in nome if _chave(t) not in _CONECTORES]
    return _limpo(" ".join(nome))[:150] if len(proprios) >= 2 else None


def _papel_por_perto(texto: str, inicio: int, fim: int) -> str | None:
    janela = _chave(texto[max(0, inicio - 250):fim + 250])
    for papel, termos in _PAPEIS:
        if any(termo in janela for termo in termos):
            return papel
    return None


def _contexto(texto: str, inicio: int, fim: int, margem: int = 130) -> str:
    return _limpo(texto[max(0, inicio - margem):fim + margem].replace("\n", " "))[:400]


# nome precedido do posto/tratamento que o identifica ("APF ANDRÉ SIMONASSI NANTES", "Sr. CLERIO …").
# O separador é espaço literal, nunca \s: quebra de linha aqui já é outro campo do documento.
_RE_NOME_COM_POSTO = re.compile(
    r"\b(DPF|APF|PCF|EPF|PM|GCM|Sr\.|Sra\.|Dr\.|Dra\.|" + _CARGOS + r") +"
    r"((?:[A-ZÀ-Ü][A-Za-zÀ-ÿ']{1,}|d[aeo]s?)(?: +(?:[A-ZÀ-Ü][A-Za-zÀ-ÿ']{1,}|d[aeo]s?)){1,5})")
# nome seguido do cargo, na linha de baixo ou após vírgula ("DIEGO MARQUES YAMASHITA\nCorregedor…")
_RE_NOME_COM_CARGO = re.compile(
    r"\b([A-ZÀ-Ü][A-ZÀ-Ü']{1,}(?: +(?:[A-ZÀ-Ü][A-ZÀ-Ü']{1,}|D[AEO]S?)){1,5}) *[,\n] *(" + _CARGOS + r")")

# palavras que nunca compõem nome de pessoa aqui — barram órgão, logradouro e rótulo de campo
# capturados por engano ("Avenida Delegado Federal Geraldo Guimarães", "Corregedor Geral da
# Polícia Civil do Estado")
_PALAVRAS_NAO_PESSOA = {
    "POLICIA", "POLICIAL", "FEDERAL", "CIVIL", "ESTADO", "ESTADUAL", "MINISTERIO", "JUSTICA",
    "GERAL", "DELEGACIA", "SUPERINTENDENCIA", "REGIONAL", "VARA", "SECAO", "NUCLEO", "SECRETARIA",
    "DEPARTAMENTO", "MUNICIPIO", "PREFEITURA", "GOVERNO", "TRIBUNAL", "COMARCA", "AVENIDA", "RUA",
    "VILA", "SENHOR", "SENHORA", "DOCUMENTO", "REMETENTE", "DESTINATARIO", "RESPONSAVEIS",
    "ASSUNTO", "REFERENCIA", "GAMPES", "IPL", "RIF", "COAF", "BANESTES", "PCC", "GAECO", "MP",
}

# posto/cargo casado junto com o nome -> papel da pessoa. Mais confiável que varrer a vizinhança:
# o posto está colado no nome, enquanto a janela de contexto pega o cargo de quem estiver ao lado.
_PAPEL_DO_POSTO = (
    (("DPF", "DELEGAD"),                        "Delegado de Polícia Federal"),
    (("APF", "AGENTE DE POLICIA"),              "Agente/Policial Federal"),
    (("EPF", "ESCRIV"),                         "Escrivão de Polícia Federal"),
    (("PCF", "PERIT"),                          "Perito"),
    (("PM", "GCM"),                             "Policial militar / guarda municipal"),
    (("PROMOTOR", "PROCURADOR"),                "Membro do Ministério Público"),
    (("CORREGEDOR",),                           "Corregedor"),
    (("INVESTIGADOR", "OFICIAL INVESTIGADOR"),  "Investigador"),
    (("SUPERINTENDENTE", "SUBSECRET", "COORDENADOR"), "Autoridade administrativa"),
)


def _papel_do_posto(posto: str) -> str | None:
    chave = _chave(posto)
    for prefixos, papel in _PAPEL_DO_POSTO:
        if any(chave.startswith(p) for p in prefixos):
            return papel
    return None


def _nome_de_pessoa(nome: str) -> bool:
    """Filtro do nome capturado por posto/cargo: dois nomes próprios e nada de palavra de órgão."""
    tokens = [_chave(t) for t in nome.split(" ") if t]
    if len([t for t in tokens if t not in _CONECTORES]) < 2:
        return False
    return not any(t in _PALAVRAS_NAO_PESSOA for t in tokens)


# ---------------------------------------------------------------------------
# Agrupamento de grafias da mesma pessoa
#
# O mesmo nome sai escrito de três jeitos no PDF, porque o extrator quebra a palavra onde muda a
# fonte: "ALEXANDRE DE OLIVEIRA BOTELHO", "ALEXANDRE DE OL IVEIRA BOTELHO" e "ALEX ANDRE DE
# OLIVEIR" (esta truncada, porque o resto virou letra solta). Some-se a isso a citação abreviada
# ("ALEXANDRE BOTELHO"). São três testes, do mais seguro para o mais frouxo — e as grafias
# fundidas ficam gravadas em no_variantes, para o investigador auditar o agrupamento.
# ---------------------------------------------------------------------------

_MIN_CHAVE_CONTIDA = 12  # abaixo disso, conter o nome do outro é coincidência (ANA em ANABEL)


def _chave_nome(nome: str | None) -> str:
    """Nome reduzido a letras puras — mata acento, caixa e o espaço de kerning no meio da palavra."""
    return re.sub(r"[^A-Z]", "", _chave(nome or ""))


def _tokens_nome(nome: str | None) -> list[str]:
    return [_chave(t) for t in (nome or "").split(" ") if _chave(t) and _chave(t) not in _CONECTORES]


def _subsequencia(menor: list[str], maior: list[str]) -> bool:
    """menor cabe em maior na mesma ordem? ('ALEXANDRE BOTELHO' em 'ALEXANDRE DE OLIVEIRA BOTELHO')"""
    it = iter(maior)
    return all(t in it for t in menor)


def _mesma_pessoa(a: dict, b: dict) -> bool:
    """b (grafia menor) é a mesma pessoa de a (grafia maior)? Documento diferente nunca funde —
    dois CPFs são duas pessoas, por mais parecido que o nome esteja escrito."""
    if a["nu_cpf_cnpj"] and b["nu_cpf_cnpj"] and a["nu_cpf_cnpj"] != b["nu_cpf_cnpj"]:
        return False
    chave_a, chave_b = _chave_nome(a["no_pessoa"]), _chave_nome(b["no_pessoa"])
    if not chave_a or not chave_b:
        return False
    if chave_a == chave_b:
        return True
    if len(chave_b) >= _MIN_CHAVE_CONTIDA and chave_b in chave_a:
        return True
    # citação abreviada: mesmo primeiro e último nome, com o resto sendo corte do nome completo
    tokens_a, tokens_b = _tokens_nome(a["no_pessoa"]), _tokens_nome(b["no_pessoa"])
    return (len(tokens_b) >= 2 and len(tokens_a) > len(tokens_b)
            and tokens_a[0] == tokens_b[0] and tokens_a[-1] == tokens_b[-1]
            and _subsequencia(tokens_b, tokens_a))


def _agrupar_pessoas(pessoas: list[dict]) -> list[dict]:
    """Funde as grafias da mesma pessoa numa linha só. Processa do nome mais completo para o mais
    curto, então o primeiro de cada grupo é o canônico: mais letras ganha e, no empate (as duas
    grafias têm as mesmas letras), ganha a que tem menos tokens — ou seja, a menos picotada."""
    ordenadas = sorted(
        pessoas,
        key=lambda p: (-len(_chave_nome(p["no_pessoa"])), len(_tokens_nome(p["no_pessoa"])),
                       p["nu_folha"] or 0),
    )
    grupos: list[dict] = []
    for pessoa in ordenadas:
        alvo = next((g for g in grupos if _mesma_pessoa(g, pessoa)), None)
        if alvo is None:
            pessoa["_variantes"] = []
            grupos.append(pessoa)
            continue
        alvo["qt_mencoes"] += pessoa["qt_mencoes"]
        for campo in ("nu_cpf_cnpj", "no_vulgo", "tp_papel"):
            if not alvo[campo] and pessoa[campo]:
                alvo[campo] = pessoa[campo]
        # a folha de referência é sempre a primeira citação, venha ela de qual grafia vier
        if pessoa["nu_folha"] is not None and (alvo["nu_folha"] is None or pessoa["nu_folha"] < alvo["nu_folha"]):
            alvo["nu_folha"], alvo["tx_contexto"] = pessoa["nu_folha"], pessoa["tx_contexto"]
        if pessoa["no_pessoa"] and pessoa["no_pessoa"] != alvo["no_pessoa"]:
            alvo["_variantes"].append(pessoa["no_pessoa"])

    for g in grupos:
        variantes = sorted(set(g.pop("_variantes")))
        g["no_variantes"] = ", ".join(variantes)[:400] or None
    return grupos


def _extrair_pessoas(paginas: list[str], nums_folha: list[int]) -> list[dict]:
    """Pessoas citadas: uma linha por CPF/CNPJ distinto, mais os vulgos e os nomes qualificados por
    posto/cargo (delegado, escrivã, APF, Sr.) — que é como as autoridades e os terceiros aparecem
    quando o inquérito não repete o documento deles."""
    pessoas: dict[str, dict] = {}
    por_nome: dict[str, str] = {}   # nome normalizado -> chave em `pessoas` (funde nome com CPF)

    def _registrar(chave_id, nome, documento, vulgo, papel, folha, contexto):
        # se o nome já foi visto com CPF, tudo que vier depois entra naquela mesma pessoa
        nome_chave = _chave_nome(nome) or None
        if nome_chave and nome_chave in por_nome:
            chave_id = por_nome[nome_chave]
        atual = pessoas.get(chave_id)
        if atual is None:
            pessoas[chave_id] = {
                "no_pessoa": nome, "nu_cpf_cnpj": documento, "no_vulgo": vulgo,
                "tp_papel": papel, "nu_folha": folha, "qt_mencoes": 1, "tx_contexto": contexto,
            }
        else:
            atual["qt_mencoes"] += 1
            # a primeira citação costuma trazer a qualificação completa; só preenche o que faltava
            for campo, valor in (("no_pessoa", nome), ("nu_cpf_cnpj", documento),
                                 ("no_vulgo", vulgo), ("tp_papel", papel)):
                if not atual[campo] and valor:
                    atual[campo] = valor
        if nome_chave:
            por_nome.setdefault(nome_chave, chave_id)

    for i, texto in enumerate(paginas):
        folha = nums_folha[i]
        plano = re.sub(r"[ \t]+", " ", texto)

        for regex in (_RE_CPF, _RE_CNPJ):
            for m in regex.finditer(plano):
                documento = re.sub(r"\s+", "", m.group(1))
                _registrar(
                    documento, _nome_antes(plano[max(0, m.start() - 180):m.start()]), documento, None,
                    _papel_por_perto(plano, m.start(), m.end()), folha, _contexto(plano, m.start(), m.end()),
                )

        for m in _RE_VULGO.finditer(plano):
            vulgo = _limpo(m.group(1))
            _registrar(
                "vulgo:" + _chave(vulgo), _nome_antes(plano[max(0, m.start() - 180):m.start()]), None, vulgo,
                _papel_por_perto(plano, m.start(), m.end()), folha, _contexto(plano, m.start(), m.end()),
            )

        for regex, grupo_nome, grupo_posto in ((_RE_NOME_COM_POSTO, 2, 1), (_RE_NOME_COM_CARGO, 1, 2)):
            for m in regex.finditer(plano):
                nome = _limpo(m.group(grupo_nome)).strip(" ,.")
                if not _nome_de_pessoa(nome):
                    continue
                papel = _papel_do_posto(m.group(grupo_posto)) or _papel_por_perto(plano, m.start(), m.end())
                _registrar("nome:" + _chave_nome(nome), nome, None, None, papel, folha,
                           _contexto(plano, m.start(), m.end()))

    agrupadas = _agrupar_pessoas(list(pessoas.values()))
    return sorted(agrupadas, key=lambda p: (p["nu_folha"], p["no_pessoa"] or ""))


# ---------------------------------------------------------------------------
# Achados — dados de interesse espalhados pelas peças
# ---------------------------------------------------------------------------

_RE_TELEFONE = re.compile(r"(?<![\d./-])(?:\(?(\d{2})\)?[\s.-]?)?(9?\d{4})[\s.-]?(\d{4})(?![\d./-])")
_RE_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_RE_VALOR = re.compile(r"R\$\s*([\d]{1,3}(?:\.\s?\d{3})*(?:\s?,\s?\d{2})?)")
_RE_PROCESSO = re.compile(r"\b\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}\b")
_RE_CONTA = re.compile(r"\bcont[ao]\s*(?:judicial\s*)?(?:n[º°.]?\s*)?:?\s*(\d[\d.\s-]{4,20}\d)", re.IGNORECASE)
_RE_AGENCIA = re.compile(r"\bag[êe]ncia\s*(?:n[º°.]?\s*)?:?\s*(?:[A-ZÀ-Ü][A-Za-zÀ-ÿ]+\s*)?\(?(?:c[óo]d\.?\s*)?(\d{3,5})\b",
                         re.IGNORECASE)
_RE_OAB = re.compile(r"\bOAB[/\s-]*([A-Z]{2})?[\s.nº°-]*(\d{1,3}\.\d{3}|\d{3,6})", re.IGNORECASE)
_RE_ENDERECO = re.compile(
    r"\b((?:Rua|Avenida|Av\.|Travessa|Rodovia|Alameda|Praça)\s+[A-ZÀ-Ü][^\n;]{8,110}?"
    r"(?:\d{5}-?\d{3}|/\s?[A-Z]{2}\b|,\s*[A-ZÀ-Ü][A-Za-zÀ-ÿ\s]{3,25}\s*[-/]\s*[A-Z]{2}\b))")
# procedimentos correlatos citados no corpo (o que puxa outra base para dentro da investigação)
_RE_PROCEDIMENTO = re.compile(
    r"\b(RIF|IPL|IPJ|SEI-?C?|GAMPES|BO|RDO|PIC|NF|denúncia|ID)\b[\s\w]{0,18}?"
    r"n?[º°.:]{0,3}\s*(\d{3,}(?:[./-]\d{2,})*)", re.IGNORECASE)
_RE_TIPIFICACAO = re.compile(
    r"\b(?:art(?:igo)?s?\.?\s*[\dº°.,\s§eIVXº-]{1,40}?)?\b"
    r"(?:Lei\s*(?:n[º°.]?\s*)?([\d.]{4,10})\s*/\s*(\d{4})|C[óo]digo\s+Penal)", re.IGNORECASE)

_MIN_VALOR = 100.0        # abaixo disso é troco/taxa, não movimentação de interesse
_MAX_ACHADOS_POR_TIPO = 400


def _valor_br(texto: str) -> float | None:
    limpo = re.sub(r"\s+", "", texto)
    limpo = limpo.replace(".", "").replace(",", ".")
    try:
        return float(limpo)
    except ValueError:
        return None


def _telefone_valido(ddd: str | None, meio: str, fim: str) -> str | None:
    """Só aceita telefone com DDD plausível — sem isso, protocolo e conta viram 'telefone'."""
    if not ddd or not (11 <= int(ddd) <= 99):
        return None
    numero = meio + fim
    if len(numero) == 9 and not numero.startswith("9"):
        return None
    if len(set(numero)) <= 2:  # 0000-0000 e afins
        return None
    return f"({ddd}) {meio}-{fim}"


def _extrair_achados(paginas: list[str], nums_folha: list[int]) -> list[dict]:
    """Dados de interesse por tipo, deduplicados pelo valor normalizado (guarda a 1ª folha e conta
    quantas vezes aparece) — é o que o investigador transcreve à mão hoje."""
    achados: dict[tuple[str, str], dict] = {}

    def _registrar(tipo, valor, numerico, folha, texto, inicio, fim):
        valor = _limpo(valor)
        if not valor:
            return
        chave = (tipo, _chave(valor))
        atual = achados.get(chave)
        if atual is not None:
            atual["qt_ocorrencias"] += 1
            return
        if sum(1 for t, _ in achados if t == tipo) >= _MAX_ACHADOS_POR_TIPO:
            return
        achados[chave] = {
            "tp_achado": tipo, "tx_valor": valor[:300], "vl_numerico": numerico,
            "nu_folha": folha, "qt_ocorrencias": 1, "tx_contexto": _contexto(texto, inicio, fim),
        }

    for i, texto in enumerate(paginas):
        folha = nums_folha[i]
        plano = re.sub(r"[ \t]+", " ", texto)

        for m in _RE_EMAIL.finditer(plano):
            _registrar("email", m.group(0).lower(), None, folha, plano, m.start(), m.end())

        for m in _RE_TELEFONE.finditer(plano):
            formatado = _telefone_valido(m.group(1), m.group(2), m.group(3))
            if formatado:
                _registrar("telefone", formatado, None, folha, plano, m.start(), m.end())

        for m in _RE_VALOR.finditer(plano):
            numerico = _valor_br(m.group(1))
            if numerico is not None and numerico >= _MIN_VALOR:
                _registrar("valor", "R$ " + _limpo(m.group(1)), numerico, folha, plano, m.start(), m.end())

        for m in _RE_PROCESSO.finditer(plano):
            _registrar("processo", m.group(0), None, folha, plano, m.start(), m.end())

        for m in _RE_ENDERECO.finditer(plano):
            _registrar("endereco", m.group(1), None, folha, plano, m.start(), m.end())

        for m in _RE_CONTA.finditer(plano):
            digitos = re.sub(r"\D", "", m.group(1))
            if 4 <= len(digitos) <= 15:
                _registrar("conta", digitos, None, folha, plano, m.start(), m.end())

        for m in _RE_AGENCIA.finditer(plano):
            _registrar("agencia", m.group(1), None, folha, plano, m.start(), m.end())

        for m in _RE_OAB.finditer(plano):
            _registrar("oab", _limpo(("OAB/" + m.group(1) if m.group(1) else "OAB") + " " + m.group(2)),
                       None, folha, plano, m.start(), m.end())

        for m in _RE_PROCEDIMENTO.finditer(plano):
            sigla = _chave(m.group(1)).replace("SEIC", "SEI-C")
            _registrar("procedimento", f"{sigla} {m.group(2)}", None, folha, plano, m.start(), m.end())

        for m in _RE_TIPIFICACAO.finditer(plano):
            _registrar("tipificacao", m.group(0), None, folha, plano, m.start(), m.end())

    ordem = {t: i for i, t in enumerate(
        ("tipificacao", "procedimento", "valor", "conta", "agencia", "telefone", "email", "endereco",
         "processo", "oab"))}
    return sorted(achados.values(), key=lambda a: (ordem.get(a["tp_achado"], 99), a["nu_folha"], a["tx_valor"]))


# ---------------------------------------------------------------------------
# Cabeçalho do procedimento
# ---------------------------------------------------------------------------

_RE_IPL_CABECALHO = re.compile(r"IPL\s*n[º°.\s]*([\d.]{8,}[-\w/]*)", re.IGNORECASE)
_RE_AUTORIDADE = re.compile(
    r"([A-ZÀ-Ü][A-ZÀ-Ü ]{8,70}?)\s*,\s*Delegad[oa]\s+de\s*\n?\s*Pol[ií]cia\s+Federal", re.IGNORECASE)
_RE_TIPIFICACAO_PORTARIA = re.compile(
    r"ocorr[êe]ncia\(?s?\)?\s+prevista\(?s?\)?\s+no\(?s?\)?\s+(.{5,200}?)(?:,\s*al[ée]m|\.\s|\n\s*\n)",
    re.IGNORECASE | re.DOTALL)
_RE_RESUMO = re.compile(
    r"RESUMO\s+DO\(?s?\)?\s+FATO\(?s?\)?\s+INVESTIGADO\(?s?\)?\s*:?(.{50,4000}?)"
    r"(?:Diante\s+disso|CUMPRA-SE|determino\s+que\s+sejam)", re.IGNORECASE | re.DOTALL)


def _extrair_cabecalho(paginas: list[str], pecas: list[dict]) -> dict:
    """Identificação do procedimento, lida da portaria inaugural (1ª peça) e do rodapé das folhas."""
    inicio = "\n".join(paginas[:4])
    todo = "\n".join(paginas)

    m = _RE_IPL_CABECALHO.search(inicio) or _RE_IPL_CABECALHO.search(todo)
    nu_ipl = _limpo(m.group(1)).rstrip(".,;") if m else None
    if not nu_ipl:
        m = _RE_NUM_IPL.search(inicio)
        nu_ipl = m.group(1) if m else None

    m = _RE_AUTORIDADE.search(inicio) or _RE_AUTORIDADE.search(todo)
    autoridade = _limpo(m.group(1)) if m else None

    # unidade: a sigla de lotação que se repete no rodapé de todas as folhas
    unidades = re.findall(r"\b([A-Z]{2,6}(?:/[A-Z]{2,6}){1,5})\b", _sem_acento(todo))
    unidade = max(set(unidades), key=unidades.count) if unidades else None

    m = _RE_TIPIFICACAO_PORTARIA.search(inicio)
    tipificacao = _limpo(re.sub(r"\s+", " ", m.group(1))) if m else None

    m = _RE_RESUMO.search(inicio)
    resumo = _reagrupar(m.group(1)).strip(" :") if m else None

    m = _RE_PROCESSO.search(todo)
    return {
        "nu_ipl": (nu_ipl or "")[:80] or None,
        "no_unidade": (unidade or "")[:120] or None,
        "no_autoridade": (autoridade or "")[:150] or None,
        "tx_tipificacao": (tipificacao or "")[:400] or None,
        "tx_resumo_fatos": (resumo or "")[:4000] or None,
        "nu_processo_judicial": m.group(0) if m else None,
        "dt_instauracao": pecas[0]["dt_peca"] if pecas else None,
    }


# ---------------------------------------------------------------------------
# Análise completa (sem banco) — usada na importação e testável isoladamente
# ---------------------------------------------------------------------------

def analisar_inquerito(caminho: Path) -> dict | None:
    """Lê o PDF e devolve {cabecalho, pecas, pessoas, achados, qt_folhas}; None se o PDF não
    parecer um inquérito (aí ele segue sendo só um documento de texto para a IA)."""
    paginas = _paginas_pdf(caminho)
    if not parece_inquerito(paginas):
        return None
    nums_folha = _folhas(paginas)
    pecas = _extrair_pecas(paginas, nums_folha)
    return {
        "cabecalho": _extrair_cabecalho(paginas, pecas),
        "pecas": pecas,
        "pessoas": _extrair_pessoas(paginas, nums_folha),
        "achados": _extrair_achados(paginas, nums_folha),
        "qt_folhas": max(nums_folha) if nums_folha else 0,
        "qt_paginas": len(paginas),
    }


# ---------------------------------------------------------------------------
# Gravação — chamada logo depois de anexar o PDF como documento do inquérito
# ---------------------------------------------------------------------------

def extrair_inquerito(no_arquivo: str, id_inquerito: int, id_documento: int | None,
                      id_usuario: int | None) -> dict | None:
    """Analisa o PDF de data/ e grava peças/pessoas/achados. Devolve o resumo da extração, ou None
    se o arquivo não for um PDF de inquérito — nesse caso ele segue sendo só texto para a IA.
    Chamada logo depois de anexar_documento_inquerito, que já validou o nome do arquivo."""
    if not no_arquivo.lower().endswith(".pdf"):
        return None
    # no_arquivo é o caminho relativo dentro de data/ (…/<inquérito>/OUTROS/x.pdf) desde a
    # organização por inquérito; documento anexado antes disso guardou só o nome do arquivo
    caminho = resolver_em_dados(no_arquivo)
    if caminho is None:
        return None
    analise = analisar_inquerito(caminho)
    if analise is None:
        return None

    cabecalho, pecas, pessoas, achados = (
        analise["cabecalho"], analise["pecas"], analise["pessoas"], analise["achados"])

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO investigacao.ipl_tb01_inqueritos_pdf
                    (id_inquerito, id_documento, no_arquivo, nu_ipl, no_unidade, no_autoridade,
                     dt_instauracao, tx_tipificacao, tx_resumo_fatos, nu_processo_judicial,
                     qt_folhas, qt_paginas, qt_pecas, qt_pessoas, qt_achados, id_usuario)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id_ipl
                """,
                (
                    id_inquerito, id_documento, caminho.name, cabecalho["nu_ipl"],
                    cabecalho["no_unidade"], cabecalho["no_autoridade"], cabecalho["dt_instauracao"],
                    cabecalho["tx_tipificacao"], cabecalho["tx_resumo_fatos"],
                    cabecalho["nu_processo_judicial"], analise["qt_folhas"], analise["qt_paginas"],
                    len(pecas), len(pessoas), len(achados), id_usuario,
                ),
            )
            id_ipl = cur.fetchone()[0]

            for p in pecas:
                cur.execute(
                    """
                    INSERT INTO investigacao.ipl_tb02_pecas
                        (id_ipl, tp_peca, nu_peca, dt_peca, nu_folha_inicial, nu_folha_final,
                         qt_folhas, no_orgao, no_assinante, tx_resumo)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (id_ipl, p["tp_peca"], p["nu_peca"], p["dt_peca"], p["nu_folha_inicial"],
                     p["nu_folha_final"], p["qt_folhas"], p["no_orgao"], p["no_assinante"], p["tx_resumo"]),
                )

            for p in pessoas:
                cur.execute(
                    """
                    INSERT INTO investigacao.ipl_tb03_pessoas
                        (id_ipl, no_pessoa, no_variantes, nu_cpf_cnpj, no_vulgo, tp_papel,
                         nu_folha, qt_mencoes, tx_contexto)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (id_ipl, p["no_pessoa"], p["no_variantes"], p["nu_cpf_cnpj"], p["no_vulgo"],
                     p["tp_papel"], p["nu_folha"], p["qt_mencoes"], p["tx_contexto"]),
                )

            for a in achados:
                cur.execute(
                    """
                    INSERT INTO investigacao.ipl_tb04_achados
                        (id_ipl, tp_achado, tx_valor, vl_numerico, nu_folha, qt_ocorrencias, tx_contexto)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (id_ipl, a["tp_achado"], a["tx_valor"], a["vl_numerico"], a["nu_folha"],
                     a["qt_ocorrencias"], a["tx_contexto"]),
                )
        conn.commit()

    return {
        "id_ipl": id_ipl, "nu_ipl": cabecalho["nu_ipl"], "qt_folhas": analise["qt_folhas"],
        "qt_pecas": len(pecas), "qt_pessoas": len(pessoas), "qt_achados": len(achados),
    }


# ---------------------------------------------------------------------------
# Consultas (bloco no card + página inquerito_pdf.html) e remoção
# ---------------------------------------------------------------------------

def _linhas(cur) -> list[dict]:
    colunas = [d.name for d in cur.description]
    return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


def _consultar(query: str, params: tuple) -> list[dict]:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            return _linhas(cur)


def listar_ipls_inquerito(id_inquerito: int) -> list[dict]:
    """Inquéritos em PDF extraídos, para o bloco no card da investigação."""
    return _consultar(
        """
        SELECT p.id_ipl, p.id_documento, p.no_arquivo, p.nu_ipl, p.no_unidade, p.no_autoridade,
               p.dt_instauracao, p.tx_tipificacao, p.nu_processo_judicial,
               p.qt_folhas, p.qt_pecas, p.qt_pessoas, p.qt_achados, p.dt_extracao,
               u.no_usuario AS extraido_por,
               ap.no_apelido,
               -- hash do PDF de origem (cadeia de custódia), quando o documento o guarda
               d.co_hash_md5, d.co_hash_sha1, d.co_hash_sha256
        FROM investigacao.ipl_tb01_inqueritos_pdf p
        LEFT JOIN seguranca.usr_tb01_usuarios u ON u.id_usuario = p.id_usuario
        LEFT JOIN investigacao.inq_tb04_documentos d ON d.id_documento = p.id_documento
        -- apelido livre do card (INQ_TB06_APELIDOS — ver menu "Mais ações" em investigacao.js)
        LEFT JOIN investigacao.inq_tb06_apelidos ap
               ON ap.tp_prova = 'ipl' AND ap.id_prova = p.id_ipl
        WHERE p.id_inquerito = %s
        ORDER BY p.dt_extracao DESC
        """,
        (id_inquerito,),
    )


def obter_ipl(id_ipl: int) -> dict | None:
    """Cabeçalho completo de um IPL extraído — topo da página inquerito_pdf.html."""
    linhas = _consultar(
        """
        SELECT p.*, u.no_usuario AS extraido_por, i.nu_inquerito
        FROM investigacao.ipl_tb01_inqueritos_pdf p
        LEFT JOIN seguranca.usr_tb01_usuarios u ON u.id_usuario = p.id_usuario
        LEFT JOIN investigacao.inq_tb01_inqueritos i ON i.id_inquerito = p.id_inquerito
        WHERE p.id_ipl = %s
        """,
        (id_ipl,),
    )
    return linhas[0] if linhas else None


def obter_id_inquerito_de_ipl(id_ipl: int) -> int | None:
    """Resolve o inquérito dono de um IPL extraído, para checar permissão nas rotas."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id_inquerito FROM investigacao.ipl_tb01_inqueritos_pdf WHERE id_ipl = %s", (id_ipl,))
            row = cur.fetchone()
            return row[0] if row else None


def listar_pecas_ipl(id_ipl: int) -> list[dict]:
    return _consultar(
        """
        SELECT id_peca, tp_peca, nu_peca, dt_peca, nu_folha_inicial, nu_folha_final, qt_folhas,
               no_orgao, no_assinante, tx_resumo
        FROM investigacao.ipl_tb02_pecas WHERE id_ipl = %s ORDER BY nu_folha_inicial, id_peca
        """,
        (id_ipl,),
    )


def listar_pessoas_ipl(id_ipl: int) -> list[dict]:
    return _consultar(
        """
        SELECT id_pessoa_ipl, no_pessoa, no_variantes, nu_cpf_cnpj, no_vulgo, tp_papel,
               nu_folha, qt_mencoes, tx_contexto
        FROM investigacao.ipl_tb03_pessoas WHERE id_ipl = %s ORDER BY nu_folha, no_pessoa
        """,
        (id_ipl,),
    )


def listar_achados_ipl(id_ipl: int) -> list[dict]:
    return _consultar(
        """
        SELECT id_achado_ipl, tp_achado, tx_valor, vl_numerico, nu_folha, qt_ocorrencias, tx_contexto
        FROM investigacao.ipl_tb04_achados WHERE id_ipl = %s ORDER BY tp_achado, nu_folha, tx_valor
        """,
        (id_ipl,),
    )


def apagar_ipl(id_ipl: int, apagar_documento: bool = True) -> bool:
    """Remove um IPL extraído e suas peças/pessoas/achados. Por padrão leva junto o documento
    de texto que alimenta a IA — é o mesmo PDF, veio no mesmo clique. False se não existir."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id_documento FROM investigacao.ipl_tb01_inqueritos_pdf WHERE id_ipl = %s", (id_ipl,))
            row = cur.fetchone()
            if row is None:
                return False
            id_documento = row[0]
            cur.execute("DELETE FROM investigacao.ipl_tb02_pecas WHERE id_ipl = %s", (id_ipl,))
            cur.execute("DELETE FROM investigacao.ipl_tb03_pessoas WHERE id_ipl = %s", (id_ipl,))
            cur.execute("DELETE FROM investigacao.ipl_tb04_achados WHERE id_ipl = %s", (id_ipl,))
            cur.execute("DELETE FROM investigacao.ipl_tb01_inqueritos_pdf WHERE id_ipl = %s", (id_ipl,))
            if apagar_documento and id_documento is not None:
                cur.execute("DELETE FROM investigacao.inq_tb04_documentos WHERE id_documento = %s", (id_documento,))
        conn.commit()
    return True

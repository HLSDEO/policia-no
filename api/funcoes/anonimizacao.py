"""Anonimização dos dados que saem para o provedor de IA (checkbox "Anonimizar dados" em
Configurações — abas "Prompt IA" e "Comentário IA no relatório").

O problema: hoje o chat manda o dump das conversas do aparelho no prompt de sistema e os resultados
das ferramentas de busca (nome, CPF, telefone, e-mail) como mensagens `tool`; a IA do relatório
manda o texto das mensagens marcadas. Com provedor externo, é dado pessoal de investigação
trafegando para terceiro.

A ideia é simples: antes de enviar, nome de pessoa vira FULANO A e os identificadores dela viram
CPF A / TELEFONE A / EMAIL A — a MESMA letra, para o modelo não perder o vínculo "este telefone é
desta pessoa". Na volta, os códigos são trocados de novo pelos valores reais. O investigador
continua vendo nome de gente na tela e no .docx; quem recebeu o prompt viu rótulo.

Três fontes de acerto, nesta ordem de prioridade:

1. DICIONÁRIO do inquérito — pessoas já identificadas (ENT_TB05/ENT_TB08) mais os nomes de
   contato, agenda, dono do aparelho, IPL, RIF, SIMBA, IRPF e IPEI. É o caminho confiável: só codifica
   nome que o sistema já conhece, e todas as grafias da mesma pessoa caem no mesmo código.
2. IDENTIFICADOR por formato — CPF, telefone e e-mail achados por regex, mesmo que não estejam no
   dicionário. Falso positivo aqui é quase nulo (o formato é estrutural).
3. HEURÍSTICA de nome próprio — sequência de 2 a 5 palavras capitalizadas que passe pelo filtro
   anti-órgão/anti-logradouro de ipl_utils. Pega o nome que aparece só dentro de uma mensagem e
   nunca foi cadastrado. É a parte que erra: "Boa Vista" pode virar FULANO X. O erro é reversível
   (o código volta ao original na resposta), mas o modelo enxerga um rótulo onde havia um nome
   próprio real — por isso a heurística NÃO roda no prompt de sistema fixo, só onde há dado
   (ver `codificar(..., heuristica=)`).

Casar nome não é `str.replace`: o texto vem com acento, caixa e conectores variando ("João Pedro
da Silva" / "JOAO PEDRO SILVA"). Em vez de um regex gigante com milhares de alternativas (lento
num prompt de 500 KB), o texto é tokenizado UMA vez e as palavras são procuradas num índice por
primeiro token — rápido e, de quebra, casa nome quebrado por linha, que é o normal em texto de PDF.

Este módulo não importa ia_provedores: quem chama monta o Anonimizador e passa por parâmetro
(`enviar_mensagem(..., anonimizador=)`). Nada de contextvars — os resultados de ferramenta e as
reescritas do relatório rodam em ThreadPoolExecutor, que não propaga contexto.
"""

import bisect
import re
import threading
import unicodedata

from funcoes.db_utils import get_connection
from funcoes.env_utils import parse_env, read_env_text

# ---------------------------------------------------------------------------
# Dobra de acentos preservando o comprimento
# ---------------------------------------------------------------------------


def _montar_tabela_dobra() -> dict[int, str]:
    """Tabela para `str.translate` que tira o acento MANTENDO um caractere por caractere.

    Só entra o caractere cuja decomposição dá exatamente uma letra base ('á' → 'a'). 'ß' e 'Æ'
    ficam de fora de propósito: virariam duas letras e deslocariam todos os índices do texto — e
    todo o casamento aqui depende de o índice no texto dobrado valer no texto original."""
    tabela: dict[int, str] = {}
    for cp in range(0xC0, 0x250):
        c = chr(cp)
        base = "".join(x for x in unicodedata.normalize("NFD", c) if unicodedata.category(x) != "Mn")
        if len(base) == 1 and base != c:
            tabela[cp] = base
    return tabela


_TABELA_DOBRA = _montar_tabela_dobra()


def _dobrar(texto: str) -> str:
    """Texto sem acento, com o mesmo comprimento do original (ver _montar_tabela_dobra)."""
    dobrado = texto.translate(_TABELA_DOBRA)
    # cinto de segurança: se algum caractere exótico mudou o tamanho, casar por índice passaria a
    # cortar o texto no lugar errado. Melhor perder o acerto de acento do que corromper o texto.
    return dobrado if len(dobrado) == len(texto) else texto


# ---------------------------------------------------------------------------
# Códigos
# ---------------------------------------------------------------------------

# Prefixos dos rótulos. CAIXA-ALTA de propósito: é o formato que o prompt de reescrita do
# relatório já manda preservar ("quem aparece em CAIXA-ALTA continua em CAIXA-ALTA") e o que
# melhor sobrevive à redação do modelo.
PREFIXO_NOME = "FULANO"
PREFIXO_CPF = "CPF"
PREFIXO_TELEFONE = "TELEFONE"
PREFIXO_EMAIL = "EMAIL"

_PREFIXOS = (PREFIXO_NOME, PREFIXO_CPF, PREFIXO_TELEFONE, PREFIXO_EMAIL)

# Reconhece um código já inserido no texto — serve para (a) descodificar a resposta e (b) impedir
# que uma segunda passada de codificação transforme "FULANO A" em outro FULANO.
#
# Estrito (sem IGNORECASE) de propósito: "EMAIL" e "CPF" são palavras correntes em português, e
# uma versão frouxa lia "email jp@..." como o código "EMAIL JP" — protegendo (na codificação) e
# revelando (na descodificação) trecho que não era código nenhum.
# O `(?!prefixo)` depois do separador é o que faz "CPF CPF A" (a palavra CPF na frase seguida do
# código) ser lido como o código "CPF A", e não como um "CPF CPF" que não existe.
_RE_CODIGO = re.compile(
    r"\b(" + "|".join(_PREFIXOS) + r")[ _-](?!(?:" + "|".join(_PREFIXOS) + r")\b)([A-Z]{1,3})\b"
)
# Segunda passada da descodificação, só para o prefixo de nome: o modelo às vezes devolve
# "Fulano A" no meio da frase. "Cpf a"/"Email a" ficam de fora — ali a colisão com o português
# corrente é real e trocar por um valor verdadeiro seria vazamento, não conserto.
_RE_CODIGO_NOME_FROUXO = re.compile(r"\b" + PREFIXO_NOME + r"[ _-]([A-Za-z]{1,3})\b", re.IGNORECASE)


def _letra(indice: int) -> str:
    """0 → A, 25 → Z, 26 → AA, 27 → AB…"""
    letras = ""
    n = indice
    while True:
        letras = chr(ord("A") + n % 26) + letras
        n = n // 26 - 1
        if n < 0:
            return letras


# ---------------------------------------------------------------------------
# Normalização de nome e de identificador
# ---------------------------------------------------------------------------

# Conectores descartados do nome ao indexar e pulados ao varrer o texto: "João da Silva" e "João
# Silva" precisam cair no mesmo código.
#
# "E" só vale no caminho do DICIONÁRIO, onde a sequência de palavras já é conhecida: ali ele casa o
# sobrenome corrente ("Ana Paula e Silva", "CARLOS E FERREIRA" no IPL). Na heurística ele fica de
# fora, e a razão apareceu no primeiro teste contra um provedor de verdade: "é" perde o acento na
# dobra e vira "e", então "Quem é João Pedro da Silva?" era lido como UM nome de quatro palavras —
# a heurística engolia o "Quem é" e ainda passava por cima do código que o dicionário daria.
_CONECTORES_NOME = {"DE", "DA", "DO", "DAS", "DOS", "E", "D"}
_CONECTORES_TEXTO = _CONECTORES_NOME
_CONECTORES_HEURISTICA = _CONECTORES_NOME - {"E"}

_RE_TOKEN = re.compile(r"[A-Za-z][A-Za-z']*")
_SO_DIGITOS = re.compile(r"\D")

_RE_EMAIL_TEXTO = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_RE_CPF_MASCARADO = re.compile(r"(?<!\d)\d{3}\.\d{3}\.\d{3}-\d{2}(?!\d)")
_RE_TELEFONE_FORMATADO = re.compile(
    r"(?<![\w])\+?55[\s.\-]?\(?\d{2}\)?[\s.\-]?9?\d{4}[\s.\-]?\d{4}(?!\d)"
    r"|(?<![\w])\(\d{2}\)[\s.\-]?9?\d{4}[\s.\-]?\d{4}(?!\d)"
    r"|(?<![\w])\d{2}[\s.\-]9\d{4}[\s.\-]?\d{4}(?!\d)"
)
# Número cru de 10 a 13 dígitos. O que vem antes/depois só desqualifica quando é OUTRO dígito ou
# um separador SEGUIDO de dígito — assim "o CPF é 12345678901." (ponto final da frase) casa, mas o
# "456" do meio de "123.456.789-01" não.
_RE_NUMERO_LONGO = re.compile(r"(?<!\d)(?<![.\-/]\d)\d{10,13}(?!\d)(?![.\-/]\d)")

# Domínios que parecem e-mail e são JID de mensageiro: só o número antes do @ é identificador
# (mesma armadilha tratada em identificacao_pessoas._email).
_DOMINIOS_MENSAGEIRO = ("s.whatsapp.net", "g.us", "c.us", "lid", "broadcast")


# Só isto pode separar duas palavras do MESMO nome: espaço, quebra de linha, hífen ou apóstrofo,
# e no máximo três caracteres. Sem esse limite a varredura junta palavras que só estão perto no
# texto — "CPF 111.444.777-35) e ALEXANDRE" viraria um nome só, engolindo o CPF e o parêntese.
_RE_SEPARADOR_NOME = re.compile(r"[ \t\r\n'\-]{1,3}$")


def _tokenizar(dobrado: str) -> list[tuple[str, int, int, bool]]:
    """(palavra, início, fim, emenda) de cada palavra do texto.

    `emenda` diz se esta palavra pode continuar o nome iniciado na anterior — isto é, se o que há
    entre as duas é separador de nome e não pontuação, número ou outra frase."""
    tokens = []
    fim_anterior = None
    for m in _RE_TOKEN.finditer(dobrado):
        emenda = fim_anterior is not None and bool(_RE_SEPARADOR_NOME.match(dobrado[fim_anterior:m.start()]))
        tokens.append((m.group(0), m.start(), m.end(), emenda))
        fim_anterior = m.end()
    return tokens


def _tokens_nome(nome: str) -> list[str]:
    """Nome em tokens maiúsculos sem acento, sem conector e sem pedaço com dígito.

    Mesma regra de identificacao_pessoas._tokens_nome — repetida aqui, e não importada, porque
    aquela é privada do módulo de identificação e trabalha sobre o texto já dobrado."""
    return [
        t.upper()
        for bruto in re.split(r"[\s.]+", _dobrar(nome or ""))
        for t in [re.sub(r"[^A-Za-z]", "", bruto)]
        if t and t.upper() not in _CONECTORES_NOME and not any(c.isdigit() for c in bruto)
    ]


def _chave_telefone(valor: str) -> str | None:
    """Telefone reduzido aos 11 últimos dígitos: '+55 51 99677-2307', '5551996772307' e
    '51996772307' viram a mesma chave (ver identificacao_pessoas._telefone)."""
    digitos = _SO_DIGITOS.sub("", valor or "")
    if len(digitos) in (12, 13) and digitos.startswith("55"):
        digitos = digitos[2:]
    if not 10 <= len(digitos) <= 13:
        return None
    return digitos[-11:]


def _chave_cpf(valor: str) -> str | None:
    """CPF em 11 dígitos. CNPJ não entra: empresa não é pessoa, e o pedido é anonimizar gente.
    Documento de dígito repetido é sentinela de "não informado", não identifica ninguém."""
    digitos = _SO_DIGITOS.sub("", valor or "")
    if len(digitos) == 11 and len(set(digitos)) > 1:
        return digitos
    return None


def _chave_email(valor: str) -> str | None:
    v = (valor or "").strip().lower()
    if "@" not in v or v.partition("@")[2] in _DOMINIOS_MENSAGEIRO:
        return None
    return v


# ---------------------------------------------------------------------------
# Heurística de nome próprio
# ---------------------------------------------------------------------------

# Palavras que nunca compõem nome de pessoa AQUI, além das de ipl_utils (órgão, logradouro,
# rótulo de campo). Estas cobrem o que aparece em conversa de aparelho e em resultado de
# ferramenta: saudação, tempo, nome de app e rótulo de tela — tudo capitalizado e tudo capaz de
# virar um FULANO se ninguém barrar.
_RUIDO_EXTRA = {
    "BOM", "BOA", "DIA", "DIAS", "NOITE", "TARDE", "OBRIGADO", "OBRIGADA", "DEUS", "AMEM",
    "SEGUNDA", "TERCA", "QUARTA", "QUINTA", "SEXTA", "SABADO", "DOMINGO", "HOJE", "ONTEM",
    "AMANHA", "JANEIRO", "FEVEREIRO", "MARCO", "ABRIL", "MAIO", "JUNHO", "JULHO", "AGOSTO",
    "SETEMBRO", "OUTUBRO", "NOVEMBRO", "DEZEMBRO",
    "WHATSAPP", "TELEGRAM", "INSTAGRAM", "FACEBOOK", "MESSENGER", "SIGNAL", "TIKTOK", "TWITTER",
    "GOOGLE", "APPLE", "SAMSUNG", "ANDROID", "IPHONE", "GMAIL", "OUTLOOK", "BUSINESS", "STATUS",
    "GRUPO", "CONTATO", "CONVERSA", "MENSAGEM", "MENSAGENS", "LIGACAO", "CHAMADA", "AUDIO",
    "VIDEO", "FOTO", "IMAGEM", "ARQUIVO", "ANEXO", "EXTRACAO", "APARELHO", "DONO", "DESCONHECIDO",
    "INQUERITO", "RELATORIO", "PIX", "BANCO", "CONTA", "AGENCIA", "CREDITO", "DEBITO",
    "TRANSFERENCIA", "PAGAMENTO", "VALOR", "TOTAL", "DATA", "HORA", "NAO", "SIM", "OK",
    "FULANO", "BELTRANO", "SICRANO",
    # rótulos de documento: aparecem colados ao valor ("CPF 111.444.777-35") e, sem isto,
    # o rótulo em caixa-alta era lido como a primeira palavra de um nome
    "CPF", "CNPJ", "RG", "TELEFONE", "CELULAR", "EMAIL", "ENDERECO", "NOME", "APELIDO", "VULGO",
}

_palavras_nao_pessoa: set[str] | None = None


def _nao_pessoa() -> set[str]:
    """Conjunto de palavras que barram um candidato a nome. Importa ipl_utils tarde de propósito:
    ele puxa investigacao_utils, que não tem por que ser carregado só para anonimizar."""
    global _palavras_nao_pessoa
    if _palavras_nao_pessoa is None:
        try:
            from funcoes.ipl_utils import _PALAVRAS_NAO_PESSOA
            _palavras_nao_pessoa = set(_PALAVRAS_NAO_PESSOA) | _RUIDO_EXTRA
        except Exception:
            _palavras_nao_pessoa = set(_RUIDO_EXTRA)
    return _palavras_nao_pessoa


def _tem_palavra_proibida(tokens) -> bool:
    """Alguma palavra é de órgão, logradouro, app ou saudação? Barra "Boa Noite", "Policia
    Federal", "Banco do Brasil", "Whatsapp Business" — tanto no dicionário quanto na heurística."""
    proibidas = _nao_pessoa()
    return any(t.upper() in proibidas for t in tokens)


def _parece_nome(tokens: list[str]) -> bool:
    """Filtro do candidato da HEURÍSTICA: 2 a 5 palavras, nenhuma proibida, todas com corpo de
    palavra e pelo menos uma com corpo de nome.

    O mínimo de duas letras por palavra é só daqui: no texto livre, uma letra solta entre nomes é
    quase sempre outra coisa. O dicionário não usa essa regra — lá a inicial abreviada ("JOAO P
    SILVA", como o RIF e o SIMBA costumam gravar) é uma grafia legítima da pessoa."""
    if not 2 <= len(tokens) <= 5:
        return False
    if _tem_palavra_proibida(tokens):
        return False
    if any(len(t) < 2 for t in tokens):
        return False
    return any(len(t) >= 3 for t in tokens)


# ---------------------------------------------------------------------------
# Anonimizador
# ---------------------------------------------------------------------------

class Anonimizador:
    """Tabela de-para de um inquérito/caso, montada uma vez por turno de IA.

    Uma pessoa é uma LETRA: o nome dela vira "FULANO A" e os identificadores dela viram "CPF A",
    "TELEFONE A", "EMAIL A". Identificador sem dono conhecido ganha uma letra própria.

    A tabela cresce durante o turno (achados da heurística e identificadores novos vindos de
    resultado de ferramenta), e os resultados de ferramenta são codificados em paralelo — daí o
    lock em toda alocação de código."""

    def __init__(self):
        self._lock = threading.RLock()
        self._contador = 0
        # nome do dicionário → código; a chave é a sequência de tokens, para casar grafia variada
        self._indice: dict[str, list[tuple[tuple[str, ...], str]]] = {}
        # ("cpf"|"telefone"|"email", chave normalizada) → código
        self._por_identificador: dict[tuple[str, str], str] = {}
        # código → texto que volta na descodificação
        self._original: dict[str, str] = {}

    # -- montagem -----------------------------------------------------------

    def _novo_slot(self) -> str:
        with self._lock:
            letra = _letra(self._contador)
            self._contador += 1
            return letra

    def registrar_pessoa(self, nomes, cpfs=(), telefones=(), emails=()) -> str | None:
        """Registra uma pessoa e devolve a letra dela. `nomes` pode trazer várias grafias — todas
        apontam para o mesmo código, e a PRIMEIRA é a que volta na descodificação (é o nome de
        exibição do sistema, então é o que o investigador espera ler)."""
        # e-mail no campo de nome é rotina em contato sincronizado do Google. Ele é
        # identificador, não nome: entra como e-mail (abaixo) ou não entra (ver _nome_util em
        # identificacao_pessoas, mesma regra).
        grafias = [n for n in (nomes or []) if (n or "").strip() and "@" not in n]
        chaves = []
        for nome in grafias:
            tokens = tuple(_tokens_nome(nome))
            # Duas travas, e as duas evitam que o dicionário reescreva texto que não é nome:
            # uma palavra só ("Mãe", "Chefe", "Zé") casaria com qualquer ocorrência dela no texto;
            # e a agenda de um aparelho real tem contato salvo como "Boa Noite" ou "Banco do
            # Brasil" — que, indexados, trocariam a saudação de uma conversa (ou uma linha do
            # prompt de sistema) por um FULANO.
            if len(tokens) >= 2 and sum(len(t) for t in tokens) >= 8 and not _tem_palavra_proibida(tokens):
                chaves.append(tokens)
        # (tipo, chave normalizada, valor como veio) — o valor bruto é o que volta na
        # descodificação: ler "+55 51 99677-2307" na resposta é melhor que ler "51996772307"
        ids = (
            [("cpf", _chave_cpf(v), v) for v in cpfs or []]
            + [("telefone", _chave_telefone(v), v) for v in telefones or []]
            + [("email", _chave_email(v), v) for v in emails or []]
        )
        ids = [t for t in ids if t[1]]
        if not chaves and not ids:
            return None

        with self._lock:
            # a mesma pessoa chega por vários caminhos (entidade canônica, contato de WhatsApp,
            # envolvido de RIF): reaproveita a letra já dada a esta grafia em vez de gastar outra
            letra = None
            for tokens in chaves:
                for existente, codigo in self._indice.get(tokens[0], []):
                    if existente == tokens:
                        letra = codigo.rpartition(" ")[2]
                        break
                if letra:
                    break
            if letra is None:
                letra = self._novo_slot()

            exibicao = grafias[0].strip() if grafias else ""
            codigo_nome = f"{PREFIXO_NOME} {letra}"
            if chaves:
                self._original.setdefault(codigo_nome, exibicao)
                for tokens in chaves:
                    if not any(seq == tokens for seq, _cod in self._indice.get(tokens[0], [])):
                        self._indice.setdefault(tokens[0], []).append((tokens, codigo_nome))
            for tipo, chave, bruto in ids:
                if (tipo, chave) in self._por_identificador:
                    continue
                prefixo = {"cpf": PREFIXO_CPF, "telefone": PREFIXO_TELEFONE, "email": PREFIXO_EMAIL}[tipo]
                codigo = f"{prefixo} {letra}"
                self._por_identificador[(tipo, chave)] = codigo
                self._original.setdefault(codigo, (bruto or chave).strip())
        return letra

    def registrar_nome(self, nome: str) -> None:
        """Atalho para nome solto, sem identificador junto."""
        self.registrar_pessoa([nome])

    def _ordenar_indice(self) -> None:
        """Dentro de cada primeiro token, o nome mais longo é testado primeiro — "João Pedro
        Silva" tem de ganhar de "João Pedro"."""
        for entradas in self._indice.values():
            entradas.sort(key=lambda e: len(e[0]), reverse=True)

    @property
    def qt_pessoas(self) -> int:
        return self._contador

    # -- codificação --------------------------------------------------------

    def _codigo_identificador(self, tipo: str, chave: str, bruto: str) -> str:
        """Código de um identificador; cria um slot novo se ele não pertence a ninguém conhecido."""
        with self._lock:
            codigo = self._por_identificador.get((tipo, chave))
            if codigo is None:
                prefixo = {"cpf": PREFIXO_CPF, "telefone": PREFIXO_TELEFONE, "email": PREFIXO_EMAIL}[tipo]
                codigo = f"{prefixo} {_letra(self._contador)}"
                self._contador += 1
                self._por_identificador[(tipo, chave)] = codigo
                self._original.setdefault(codigo, bruto)
            return codigo

    def _codigo_nome_novo(self, texto: str) -> str:
        """Código para um nome achado pela heurística (não estava no dicionário)."""
        tokens = tuple(_tokens_nome(texto))
        with self._lock:
            for existente, codigo in self._indice.get(tokens[0] if tokens else "", []):
                if existente == tokens:
                    return codigo
            codigo = f"{PREFIXO_NOME} {_letra(self._contador)}"
            self._contador += 1
            if tokens:
                self._indice.setdefault(tokens[0], []).append((tokens, codigo))
                self._indice[tokens[0]].sort(key=lambda e: len(e[0]), reverse=True)
            self._original.setdefault(codigo, texto.strip())
            return codigo

    def _achados_identificadores(self, dobrado: str) -> list[tuple[int, int, str, int]]:
        """Spans de CPF/telefone/e-mail. A tupla é (início, fim, código, prioridade)."""
        achados = []
        for m in _RE_EMAIL_TEXTO.finditer(dobrado):
            bruto = m.group(0)
            chave = _chave_email(bruto)
            if chave:
                achados.append((m.start(), m.end(), self._codigo_identificador("email", chave, bruto), 0))
                continue
            # JID de mensageiro ('5551996772307@s.whatsapp.net'): codifica só o número, o domínio
            # fica visível — é ele que diz ao modelo de qual app veio a conversa
            local = bruto.partition("@")[0]
            chave_tel = _chave_telefone(local)
            if chave_tel:
                achados.append(
                    (m.start(), m.start() + len(local),
                     self._codigo_identificador("telefone", chave_tel, local), 0)
                )
        for m in _RE_CPF_MASCARADO.finditer(dobrado):
            chave = _chave_cpf(m.group(0))
            if chave:
                achados.append((m.start(), m.end(), self._codigo_identificador("cpf", chave, m.group(0)), 0))
        for m in _RE_TELEFONE_FORMATADO.finditer(dobrado):
            chave = _chave_telefone(m.group(0))
            if chave:
                achados.append((m.start(), m.end(), self._codigo_identificador("telefone", chave, m.group(0)), 0))
        for m in _RE_NUMERO_LONGO.finditer(dobrado):
            bruto = m.group(0)
            tipo, chave = self._classificar_numero(bruto)
            if chave:
                achados.append((m.start(), m.end(), self._codigo_identificador(tipo, chave, bruto), 0))
        return achados

    def _classificar_numero(self, digitos: str) -> tuple[str, str | None]:
        """11 dígitos crus são ambíguos: '27999887766' é celular e '12345678901' é CPF.

        Decide pelo que o dicionário já conhece; sem isso, cai na forma: DDD válido com o 9 na
        frente é telefone, o resto é CPF."""
        chave_tel = _chave_telefone(digitos)
        chave_cpf = _chave_cpf(digitos)
        if chave_tel and ("telefone", chave_tel) in self._por_identificador:
            return "telefone", chave_tel
        if chave_cpf and ("cpf", chave_cpf) in self._por_identificador:
            return "cpf", chave_cpf
        if len(digitos) != 11:
            return "telefone", chave_tel
        ddd_valido = 11 <= int(digitos[:2]) <= 99
        if ddd_valido and digitos[2] == "9":
            return "telefone", chave_tel
        return "cpf", chave_cpf

    def _achados_nomes(self, dobrado: str, heuristica: bool) -> list[tuple[int, int, str | None, int]]:
        """Spans de nome, do dicionário (prioridade 0) e da heurística (prioridade 1).

        Duas varreduras independentes sobre a MESMA tokenização, de propósito. Numa varredura só, um
        palpite da heurística que começasse antes impediria o acerto do dicionário de sequer ser
        gerado ("Ana Maria João Pedro Silva" engoliria o "João Pedro Silva" cadastrado) — e quem
        escolhe entre spans que se sobrepõem é `codificar`, não esta função.

        O span da heurística vem com código None: quem for escolhido é que ganha uma letra, em
        `codificar`. Alocar aqui gastaria letra com candidato descartado e, pior, cadastraria no
        índice um "nome" que a seleção rejeitou."""
        tokens = _tokenizar(dobrado)
        achados: list[tuple[int, int, str | None, int]] = []

        i = 0
        while i < len(tokens):
            texto_tok, inicio, _fim, _emenda = tokens[i]
            for sequencia, codigo in self._indice.get(texto_tok.upper(), ()):
                fim_idx = self._casar_sequencia(tokens, i, sequencia)
                if fim_idx is not None:
                    achados.append((inicio, tokens[fim_idx][2], codigo, 0))
                    i = fim_idx
                    break
            i += 1

        if heuristica:
            i = 0
            while i < len(tokens):
                fim_idx, palavras = self._sequencia_capitalizada(tokens, i)
                if fim_idx is not None and _parece_nome(palavras):
                    achados.append((tokens[i][1], tokens[fim_idx][2], None, 1))
                    i = fim_idx + 1
                else:
                    i += 1
        return achados

    @staticmethod
    def _casar_sequencia(tokens, i: int, sequencia: tuple[str, ...]) -> int | None:
        """Índice do último token se `sequencia` casa a partir de `i`, pulando conectores no meio
        ("Joao da Silva" casa com ('JOAO', 'SILVA')). None se não casa."""
        pos = i
        for n, esperado in enumerate(sequencia):
            while pos < len(tokens) and tokens[pos][0].upper() in _CONECTORES_TEXTO:
                if not tokens[pos][3]:
                    return None
                pos += 1
            if pos >= len(tokens) or tokens[pos][0].upper() != esperado:
                return None
            if n and not tokens[pos][3]:
                return None
            pos += 1
        return pos - 1

    @staticmethod
    def _sequencia_capitalizada(tokens, i: int) -> tuple[int | None, list[str]]:
        """Maior sequência de palavras capitalizadas (Título ou CAIXA-ALTA, sem misturar as duas)
        a partir de `i`, pulando conectores. Devolve (índice do último token, palavras)."""
        primeiro = tokens[i][0]
        # conector nunca abre um nome: "…entregue DE SOUZA" não é a pessoa "De Souza"
        if not primeiro[:1].isupper() or len(primeiro) < 2 or primeiro.upper() in _CONECTORES_HEURISTICA:
            return None, []
        caixa_alta = primeiro.isupper()
        palavras = [primeiro]
        pos = i + 1
        ultimo = i
        while pos < len(tokens) and len(palavras) < 5:
            atual = tokens[pos][0]
            if not tokens[pos][3]:  # há pontuação ou número entre as duas: acabou o nome
                break
            # conector no meio do nome é pulado, minúsculo ("Ana de Souza") ou maiúsculo
            # ("ANA DE SOUZA"): sem isso o "DE" quebraria a sequência por não ser um nome próprio
            if atual.upper() in _CONECTORES_HEURISTICA:
                pos += 1
                continue
            if not atual[:1].isupper() or len(atual) < 2 or atual.isupper() != caixa_alta:
                break
            palavras.append(atual)
            ultimo = pos
            pos += 1
        return (ultimo, palavras) if len(palavras) >= 2 else (None, [])

    def codificar(self, texto: str, heuristica: bool = True) -> str:
        """Troca nomes e identificadores pelos códigos. Re-executar sobre um texto já codificado é
        seguro: os trechos que já são código ficam de fora."""
        if not texto:
            return texto
        dobrado = _dobrar(texto)
        protegidos = [(m.start(), m.end()) for m in _RE_CODIGO.finditer(dobrado)]
        achados = self._achados_identificadores(dobrado) + self._achados_nomes(dobrado, heuristica)
        if not achados:
            return texto

        # Dicionário e identificador são colocados PRIMEIRO, na varredura inteira; a heurística só
        # preenche o que sobrou. A ordem importa: um palpite da heurística que comece antes de um
        # acerto do dicionário ("Quem é João Pedro da Silva") tomaria o lugar dele numa passada
        # única por posição — e o mesmo nome ganharia códigos diferentes conforme a palavra que
        # viesse antes. Dentro de cada prioridade vale a posição e, no empate, o trecho mais longo.
        # Os escolhidos ficam ordenados por início (bisect), e não numa lista qualquer varrida do
        # começo a cada teste: num dump de 600 KB são dezenas de milhares de spans, e a varredura
        # linear levava o quadrático a 40 s — contra menos de 2 s assim.
        escolhidos: list[tuple[int, int, str]] = []
        inicios: list[int] = []

        def livre(inicio: int, fim: int) -> bool:
            k = bisect.bisect_right(inicios, inicio) - 1
            if k >= 0 and escolhidos[k][1] > inicio:
                return False
            if k + 1 < len(escolhidos) and escolhidos[k + 1][0] < fim:
                return False
            return not any(inicio < pf and pi < fim for pi, pf in protegidos)

        for prioridade in (0, 1):
            for inicio, fim, codigo, _p in sorted(
                (a for a in achados if a[3] == prioridade), key=lambda a: (a[0], -(a[1] - a[0]))
            ):
                if livre(inicio, fim):
                    # só agora a heurística gasta uma letra — no span que sobreviveu à seleção
                    k = bisect.bisect_right(inicios, inicio)
                    escolhidos.insert(k, (inicio, fim, codigo or self._codigo_nome_novo(texto[inicio:fim])))
                    inicios.insert(k, inicio)

        partes = []
        cursor = 0
        for inicio, fim, codigo in escolhidos:
            partes.append(texto[cursor:inicio])
            partes.append(codigo)
            cursor = fim
        partes.append(texto[cursor:])
        return "".join(partes)

    def codificar_mensagens(self, mensagens: list[dict], heuristica: bool = True) -> list[dict]:
        """Codifica o `content` de cada mensagem — string ou lista de blocos (formato Anthropic)."""
        saida = []
        for m in mensagens or []:
            conteudo = m.get("content")
            if isinstance(conteudo, str):
                saida.append({**m, "content": self.codificar(conteudo, heuristica)})
            elif isinstance(conteudo, list):
                saida.append({**m, "content": self.codificar_dados(conteudo, heuristica)})
            else:
                saida.append(m)
        return saida

    def codificar_dados(self, valor, heuristica: bool = True):
        """Codifica só os VALORES de texto de uma estrutura (resultado de ferramenta). As chaves
        ficam intocadas: são nome de coluna, não dado — e mexer nelas quebraria o JSON para o
        modelo."""
        if isinstance(valor, str):
            return self.codificar(valor, heuristica)
        if isinstance(valor, dict):
            return {k: self.codificar_dados(v, heuristica) for k, v in valor.items()}
        if isinstance(valor, (list, tuple)):
            return [self.codificar_dados(v, heuristica) for v in valor]
        return valor

    # -- descodificação -----------------------------------------------------

    def decodificar(self, texto: str) -> str:
        """Devolve os valores reais no lugar dos códigos. Código que nunca foi emitido (o modelo
        inventou um "FULANO Z") fica como está — inventar um nome no lugar seria pior.

        Duas passadas: a estrita, que casa o formato exatamente como foi emitido, e uma tolerante
        só para FULANO, porque o modelo às vezes normaliza a caixa no meio da frase."""
        if not texto:
            return texto

        def troca(m):
            codigo = f"{m.group(1).upper()} {m.group(2).upper()}"
            return self._original.get(codigo, m.group(0))

        def troca_nome(m):
            codigo = f"{PREFIXO_NOME} {m.group(1).upper()}"
            return self._original.get(codigo, m.group(0))

        return _RE_CODIGO_NOME_FROUXO.sub(troca_nome, _RE_CODIGO.sub(troca, texto))

    def decodificar_dados(self, valor):
        """Descodifica recursivamente — usado nos ARGUMENTOS que o modelo manda numa chamada de
        ferramenta: ele pede `buscar_contato_extracao(nome="FULANO A")` e a busca precisa do nome
        real para achar alguma coisa."""
        if isinstance(valor, str):
            return self.decodificar(valor)
        if isinstance(valor, dict):
            return {k: self.decodificar_dados(v) for k, v in valor.items()}
        if isinstance(valor, (list, tuple)):
            return [self.decodificar_dados(v) for v in valor]
        return valor


# ---------------------------------------------------------------------------
# Configuração (.env)
# ---------------------------------------------------------------------------

def anonimizacao_ativa(prefixo: str) -> bool:
    """`prefixo` é "AI" (aba Prompt IA) ou "REPORT_AI" (aba Comentário IA no relatório).

    Lê o .env a cada chamada, como o resto das configurações de IA — ligar/desligar o checkbox
    vale já no próximo turno, sem reiniciar o container."""
    env = parse_env(read_env_text())
    return (env.get(f"{prefixo}_ANONIMIZAR") or "").strip().lower() in ("1", "true", "sim")


# ---------------------------------------------------------------------------
# Montagem do dicionário
# ---------------------------------------------------------------------------

# Teto por consulta: um inquérito grande tem dezenas de milhares de contatos, e a partir de certo
# ponto o que entra é ruído de agenda ("Pizzaria", "Uber") que não ajuda ninguém.
_LIMITE_POR_FONTE = 5000

# Fontes de pessoa do inquérito: (SQL, assinatura das colunas do SELECT, na ordem).
#
# A assinatura é o que faz o nome e os identificadores da MESMA linha caírem no MESMO código —
# "FULANO A" e "TELEFONE A" são a mesma pessoa. Cada letra é um tipo de coluna:
#   n = nome (várias grafias podem vir separadas por vírgula na mesma coluna)
#   c = CPF   t = telefone   e = e-mail
#   x = identificador de tipo incerto (o campo do UFDR é telefone num app e id numérico noutro):
#       tenta as três normalizações e fica com a que aceitar
#
# As tabelas do aparelho são por extração, não por inquérito — daí o JOIN com inq_tb02_extracoes.
_FONTES_PESSOA_INQUERITO = (
    ("""SELECT DISTINCT c.no_contato, c.nu_whatsapp_id
          FROM investigacao.wpp_tb01_contatos c
          JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = c.id_extracao
         WHERE e.id_inquerito = %(inq)s AND c.no_contato IS NOT NULL LIMIT %(lim)s""", "nt"),
    ("""SELECT DISTINCT c.no_contato, c.nu_telefone
          FROM investigacao.sms_tb01_contatos c
          JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = c.id_extracao
         WHERE e.id_inquerito = %(inq)s AND c.no_contato IS NOT NULL LIMIT %(lim)s""", "nt"),
    # a agenda guarda os identificadores numa tabela filha (telefone e e-mail na mesma coluna)
    ("""SELECT DISTINCT c.no_contato, v.tx_valor
          FROM investigacao.ctt_tb01_contatos c
          JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = c.id_extracao
          LEFT JOIN investigacao.ctt_tb02_contato_entradas v ON v.id_contato = c.id_contato
         WHERE e.id_inquerito = %(inq)s AND c.no_contato IS NOT NULL LIMIT %(lim)s""", "nx"),
    ("""SELECT DISTINCT c.no_contato, c.nu_identificador
          FROM investigacao.msg_tb01_contatos c
          JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = c.id_extracao
         WHERE e.id_inquerito = %(inq)s AND c.no_contato IS NOT NULL LIMIT %(lim)s""", "nx"),
    ("""SELECT DISTINCT l.no_contato, l.nu_telefone_contato
          FROM investigacao.lig_tb01_ligacoes l
          JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = l.id_extracao
         WHERE e.id_inquerito = %(inq)s AND l.no_contato IS NOT NULL LIMIT %(lim)s""", "nt"),
    ("""SELECT DISTINCT d.no_dono, d.nu_identificador
          FROM investigacao.don_tb01_donos d
          JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = d.id_extracao
         WHERE e.id_inquerito = %(inq)s AND d.no_dono IS NOT NULL LIMIT %(lim)s""", "nx"),
    ("""SELECT DISTINCT c.no_usuario, c.no_titular
          FROM investigacao.cta_tb01_contas_usuario c
          JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = c.id_extracao
         WHERE e.id_inquerito = %(inq)s LIMIT %(lim)s""", "nn"),
    # o IPL já fundiu as grafias da mesma pessoa em no_variantes — manter esse agrupamento é o
    # que impede "ALEXANDRE BOTELHO" e "ALEXANDRE DE OLIVEIRA BOTELHO" de virarem dois FULANOs
    ("""SELECT DISTINCT p.no_pessoa, p.no_variantes, p.no_vulgo, p.nu_cpf_cnpj
          FROM investigacao.ipl_tb03_pessoas p
          JOIN investigacao.ipl_tb01_inqueritos_pdf i ON i.id_ipl = p.id_ipl
         WHERE i.id_inquerito = %(inq)s LIMIT %(lim)s""", "nnnc"),
    ("""SELECT DISTINCT v.no_envolvido, v.nu_cpf_cnpj
          FROM investigacao.rif_tb03_envolvidos v
          JOIN investigacao.rif_tb01_rifs r ON r.id_rif = v.id_rif
         WHERE r.id_inquerito = %(inq)s AND v.no_envolvido IS NOT NULL LIMIT %(lim)s""", "nc"),
    ("""SELECT DISTINCT c.no_contraparte, c.nu_cpf_cnpj
          FROM investigacao.rif_tb05_contrapartes c
          JOIN investigacao.rif_tb01_rifs r ON r.id_rif = c.id_rif
         WHERE r.id_inquerito = %(inq)s AND c.no_contraparte IS NOT NULL LIMIT %(lim)s""", "nc"),
    ("""SELECT DISTINCT c.no_titular, c.nu_cpf_cnpj
          FROM investigacao.smb_tb02_contas c
          JOIN investigacao.smb_tb01_extratos x ON x.id_extrato = c.id_extrato
         WHERE x.id_inquerito = %(inq)s AND c.no_titular IS NOT NULL LIMIT %(lim)s""", "nc"),
    # contrapartes já vêm agregadas; smb_tb03_lancamentos.no_od é a mesma informação linha a linha
    # (milhões de registros num extrato real) e ficou de fora por causa disso
    ("""SELECT DISTINCT c.no_contraparte, c.nu_cpf_cnpj
          FROM investigacao.smb_tb04_contrapartes c
          JOIN investigacao.smb_tb01_extratos x ON x.id_extrato = c.id_extrato
         WHERE x.id_inquerito = %(inq)s AND c.no_contraparte IS NOT NULL LIMIT %(lim)s""", "nc"),
    ("""SELECT DISTINCT no_titular, nu_cpf, nu_telefone, nu_celular, no_email
          FROM investigacao.irp_tb01_declaracoes
         WHERE id_inquerito = %(inq)s AND no_titular IS NOT NULL LIMIT %(lim)s""", "nctte"),
    ("""SELECT DISTINCT d.no_dependente, d.nu_cpf
          FROM investigacao.irp_tb03_dependentes d
          JOIN investigacao.irp_tb01_declaracoes i ON i.id_irpf = d.id_irpf
         WHERE i.id_inquerito = %(inq)s AND d.no_dependente IS NOT NULL LIMIT %(lim)s""", "nc"),
    # IPEI (quebra de sigilo fiscal): o contribuinte analisado e quem aparece ligado a ele — sócio,
    # empregador, outorgante de procuração, dono do MAC coincidente. Sem isso o nome sai em claro
    # na conversa de IA anonimizada justamente onde a quebra de sigilo mais expõe terceiros.
    ("""SELECT DISTINCT a.no_alvo, a.nu_cpf_cnpj
          FROM investigacao.ipe_tb02_alvos a
          JOIN investigacao.ipe_tb01_ipeis i ON i.id_ipei = a.id_ipei
         WHERE i.id_inquerito = %(inq)s AND a.no_alvo IS NOT NULL LIMIT %(lim)s""", "nc"),
    ("""SELECT DISTINCT r.no_relacionado, r.nu_cpf_cnpj
          FROM investigacao.ipe_tb03_relacionados r
          JOIN investigacao.ipe_tb01_ipeis i ON i.id_ipei = r.id_ipei
         WHERE i.id_inquerito = %(inq)s AND r.no_relacionado IS NOT NULL LIMIT %(lim)s""", "nc"),
    ("""SELECT DISTINCT c.tx_valor
          FROM investigacao.ipe_tb09_contatos c
          JOIN investigacao.ipe_tb01_ipeis i ON i.id_ipei = c.id_ipei
         WHERE i.id_inquerito = %(inq)s AND c.tp_contato = 'email' LIMIT %(lim)s""", "e"),
    ("""SELECT DISTINCT c.tx_valor
          FROM investigacao.ipe_tb09_contatos c
          JOIN investigacao.ipe_tb01_ipeis i ON i.id_ipei = c.id_ipei
         WHERE i.id_inquerito = %(inq)s AND c.tp_contato = 'telefone' LIMIT %(lim)s""", "t"),
    # pessoas que a análise de documento anexado extraiu (jq_pessoas é JSONB)
    ("""SELECT DISTINCT jsonb_array_elements(jq_pessoas)->>'no_pessoa'
          FROM investigacao.inq_tb04_documentos
         WHERE id_inquerito = %(inq)s AND jq_pessoas IS NOT NULL LIMIT %(lim)s""", "n"),
)


def _consultar(cur, sql: str, params: dict) -> list:
    """Roda uma consulta do dicionário isolada das outras.

    Cada fonte tem seu SAVEPOINT: numa base que ainda não recebeu alguma migração, a tabela
    inexistente aborta a transação inteira no Postgres — e uma fonte a menos não pode custar o
    dicionário inteiro (nem o turno de IA)."""
    try:
        cur.execute("SAVEPOINT fonte_anonimizacao")
        cur.execute(sql, params)
        linhas = cur.fetchall()
        cur.execute("RELEASE SAVEPOINT fonte_anonimizacao")
        return linhas
    except Exception:
        cur.execute("ROLLBACK TO SAVEPOINT fonte_anonimizacao")
        return []


def _partes(valor) -> list[str]:
    """Quebra os campos que guardam várias grafias numa string só (no_variantes do IPL é
    "GRAFIA A, GRAFIA B"; nu_cpf_cnpj do IPL também vem separado por vírgula)."""
    return [p.strip() for p in re.split(r"[,;|/\n]+", str(valor or "")) if p.strip()]


def _campos_da_linha(linha, assinatura: str) -> dict:
    """Distribui as colunas de uma linha nos parâmetros de `registrar_pessoa`, conforme a
    assinatura da fonte (ver _FONTES_PESSOA_INQUERITO). O tipo 'x' é o campo do UFDR que às vezes
    é telefone e às vezes é id de conta: entra como telefone E como e-mail, e as normalizações
    descartam o que não for nenhum dos dois."""
    campos = {"nomes": [], "cpfs": [], "telefones": [], "emails": []}
    for valor, tipo in zip(linha, assinatura):
        for parte in _partes(valor):
            if tipo == "n":
                campos["nomes"].append(parte)
            elif tipo == "c":
                campos["cpfs"].append(parte)
            elif tipo == "t":
                campos["telefones"].append(parte)
            elif tipo == "e":
                campos["emails"].append(parte)
            elif tipo == "x":
                campos["telefones"].append(parte)
                campos["emails"].append(parte)
    return campos


def anonimizador_para_inquerito(id_inquerito: int) -> Anonimizador:
    """Dicionário do inquérito: pessoas identificadas (com os identificadores que as definem) mais
    os nomes das demais origens (contato, agenda, dono, IPL, RIF, SIMBA, IRPF, IPEI, documentos)."""
    anon = Anonimizador()
    params = {"inq": id_inquerito, "lim": _LIMITE_POR_FONTE}
    with get_connection() as conn:
        with conn.cursor() as cur:
            # 1) entidades canônicas: uma linha por pessoa, com nome e identificadores agrupados.
            # É o único lugar onde a fusão "mesmo telefone = mesma pessoa" já foi feita, então é
            # daqui que sai a garantia de que as grafias de alguém caem todas no mesmo FULANO.
            canonicas = _consultar(
                cur,
                """
                SELECT c.id_canonica, c.no_entidade,
                       coalesce(array_agg(i.tp_identificador || ':' || i.co_valor)
                                FILTER (WHERE i.id_identificador IS NOT NULL), '{}'::text[]) AS ids
                  FROM investigacao.ent_tb05_canonicas c
                  LEFT JOIN investigacao.ent_tb08_canonica_identificadores i
                         ON i.id_canonica = c.id_canonica
                 WHERE c.id_inquerito = %(inq)s AND c.tp_entidade = 'pessoa'
                 GROUP BY c.id_canonica, c.no_entidade
                 LIMIT %(lim)s
                """,
                params,
            )
            for _id, no_entidade, ids in canonicas:
                nomes = [no_entidade] if no_entidade else []
                cpfs, telefones, emails = [], [], []
                for bruto in ids or []:
                    # "telefone:5551996772307" — tp_identificador nunca tem ':', entao o
                    # primeiro separador e o certo mesmo quando o valor contem dois-pontos
                    tipo, _, valor = str(bruto).partition(":")
                    if tipo == "nome":
                        nomes.append(valor)
                    elif tipo == "cpf":
                        cpfs.append(valor)
                    elif tipo == "telefone":
                        telefones.append(valor)
                    elif tipo == "email":
                        emails.append(valor)
                anon.registrar_pessoa(nomes, cpfs, telefones, emails)

            # 2) demais origens: cada linha é uma pessoa, com o que a tabela souber dela
            for sql, assinatura in _FONTES_PESSOA_INQUERITO:
                for linha in _consultar(cur, sql, params):
                    anon.registrar_pessoa(**_campos_da_linha(linha, assinatura))

    anon._ordenar_indice()
    return anon


def nomes_no_texto(texto: str) -> list[str]:
    """Nomes próprios prováveis num texto livre, pelo mesmo filtro da heurística. Serve para
    semear o dicionário a partir de campo que é texto e não coluna de nome."""
    dobrado = _dobrar(texto or "")
    tokens = _tokenizar(dobrado)
    encontrados = []
    i = 0
    while i < len(tokens):
        fim, palavras = Anonimizador._sequencia_capitalizada(tokens, i)
        if fim is not None and _parece_nome(palavras):
            encontrados.append(texto[tokens[i][1]:tokens[fim][2]])
            i = fim + 1
        else:
            i += 1
    return encontrados

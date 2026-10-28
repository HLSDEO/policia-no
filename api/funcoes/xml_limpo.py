"""Stream de XML que reescreve referências numéricas de caracteres ilegais antes do parser.

O report.xml do Cellebrite escapa emoji como par de surrogados — `&#xD83D;&#xDE00;` — e às vezes
solta referências a caracteres de controle (`&#0;`). Isso é ilegal em XML 1.0 e o expat aborta a
leitura inteira com "reference to invalid character number", derrubando a importação do .ufdr
todo por causa de uma mensagem com emoji no meio.

O invólucro daqui mantém o streaming (o arquivo tem dezenas de GB, nada é carregado na RAM): lê
blocos crus, reescreve só as referências inválidas — juntando o par de surrogados no emoji de
verdade, trocando o resto por U+FFFD — e entrega ao parser. Referências válidas e o texto
escapado (`&amp;#xD83D;`, que é texto literal, não referência) passam intactos.
"""

import re

# referência numérica completa (o parser resolve sozinho)
_REF = re.compile(rb"&#([xX])?([0-9A-Fa-f]{1,12});")
# cauda que ainda é referência em andamento: zero ou mais completas + possivelmente uma
# parcial no fim. Segurar isso entre blocos é o que impede o corte de partir uma referência
# (ou o par de surrogados) ao meio.
_CADEIA_REF = re.compile(
    rb"(?:&#(?:[xX])?[0-9A-Fa-f]{1,12};)*&(?:#(?:[xX])?[0-9A-Fa-f]{0,12})?;?$")
# uma referência completa não passa de `&#x10FFFF;`; 24 cobre duas (o par de surrogados)
_JANELA = 24

_SURROGADO_ALTA = (0xD800, 0xDBFF)
_SURROGADO_BAIXA = (0xDC00, 0xDFFF)
_FFFD = "\ufffd".encode()


def _eh_xml(n: int) -> bool:
    """n é um codepoint legal para XML 1.0 (tab/LF/CR incluídos, surrogados e fora do plano não)."""
    return (n in (0x9, 0xA, 0xD)
            or 0x20 <= n <= 0xD7FF
            or 0xE000 <= n <= 0xFFFD
            or 0x10000 <= n <= 0x10FFFF)


def _alta(n: int) -> bool:
    return _SURROGADO_ALTA[0] <= n <= _SURROGADO_ALTA[1]


def _baixa(n: int) -> bool:
    return _SURROGADO_BAIXA[0] <= n <= _SURROGADO_BAIXA[1]


def _numero(m) -> int:
    return int(m.group(2), 16 if m.group(1) else 10)


def sanear(dados: bytes) -> bytes:
    """Reescreve as referências numéricas ilegais de um trecho de XML (sem partir nada)."""
    if b"&#" not in dados:
        return dados
    saida: list[bytes] = []
    pos = 0
    achados = list(_REF.finditer(dados))
    i = 0
    while i < len(achados):
        m = achados[i]
        n = _numero(m)
        if _eh_xml(n):
            i += 1
            continue  # referência legítima: o expat resolve sozinho
        # par de surrogados adjacente é um emoji do plano astral escrito em duas peças —
        # junto vira o caractere de verdade; separado seria lixo (ou a mensagem toda)
        if _alta(n) and i + 1 < len(achados) and achados[i + 1].start() == m.end():
            m2 = achados[i + 1]
            n2 = _numero(m2)
            if _baixa(n2):
                if m.start() > pos:
                    saida.append(dados[pos:m.start()])
                saida.append(chr(0x10000 + (n - 0xD800) * 0x400 + (n2 - 0xDC00)).encode())
                pos = m2.end()
                i += 2
                continue
        if m.start() > pos:
            saida.append(dados[pos:m.start()])
        saida.append(_FFFD)
        pos = m.end()
        i += 1
    if not saida:
        return dados
    saida.append(dados[pos:])
    return b"".join(saida)


class FluxoXmlSeguro:
    """File-like (só read/tell/close) sobre um stream de bytes de XML já saneado.

    O corte entre blocos nunca parte uma referência ao meio: a cauda que ainda pode ser o
    começo de uma referência (ou um surrogado alto completo à espera da metade de baixo) é
    segurada e remexida com o bloco seguinte."""

    def __init__(self, base, bloco: int = 1 << 18) -> None:
        self._base = base
        self._bloco = bloco
        self._pendente = b""
        self._saida = b""
        self._emitidos = 0
        self._fim = False

    def _encher(self) -> None:
        bruto = self._base.read(self._bloco)
        if not bruto:
            self._fim = True
            self._saida = sanear(self._pendente)
            self._pendente = b""
            return
        dados = self._pendente + bruto
        corte = len(dados) - self._guarda(dados)
        self._saida = sanear(dados[:corte])
        self._pendente = dados[corte:]

    @staticmethod
    def _guarda(dados: bytes) -> int:
        """Quantos bytes da cauda ainda podem ser o começo de uma referência (segurar)."""
        cauda = dados[-_JANELA:]
        guardar = 0
        for m in re.finditer(rb"&", cauda):
            # do '&' até o fim precisa ser só referência (completa, e no máximo uma parcial
            # no fim) — segurar do '&' MAIS À ESQUERDA possível, senão a metade de cima do
            # par de surrogados fica para trás e vira U+FFFD antes da de baixo chegar
            if _CADEIA_REF.fullmatch(cauda, m.start()):
                guardar = len(cauda) - m.start()
                break
        return guardar

    def readable(self) -> bool:
        return True

    def read(self, tam: int = -1) -> bytes:
        while not self._saida and not self._fim:
            self._encher()
        if tam is None or tam < 0:
            dados, self._saida = self._saida, b""
        else:
            dados, self._saida = self._saida[:tam], self._saida[tam:]
        self._emitidos += len(dados)
        return dados

    def tell(self) -> int:
        # posição aproximada para barra de progresso: o saneamento só encolhe o stream
        return self._emitidos

    def close(self) -> None:
        self._base.close()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()
        return False


def abrir(zf, nome: str = "report.xml") -> FluxoXmlSeguro:
    """Abre um membro do ZIP já com o saneamento ativo (fechar o invólucro fecha o membro)."""
    return FluxoXmlSeguro(zf.open(nome))

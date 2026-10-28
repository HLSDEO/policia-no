"""Levantamento determinístico de "achados" no texto de um documento — telefones, CPFs/CNPJs,
e-mails, valores, contas, processos, placas… — sem IA, por regex.

É o mesmo trabalho que `ipl_utils._extrair_achados` faz sobre as folhas de um IPL, adaptado
para um texto solto.
"""

import re
import unicodedata
from datetime import date

from funcoes.ipl_utils import (
    _RE_AGENCIA,
    _RE_CNPJ,
    _RE_CONTA,
    _RE_CPF,
    _RE_EMAIL,
    _RE_ENDERECO,
    _MESES,
    _RE_OAB,
    _RE_PROCEDIMENTO,
    _RE_PROCESSO,
    _RE_TELEFONE,
    _RE_TIPIFICACAO,
    _RE_VALOR,
    _contexto,
    _limpo,
    _MIN_VALOR,
    _telefone_valido,
    _valor_br,
)

_MAX_ACHADOS_POR_TIPO = 300  # um documento gigante não deve produzir milhares de linhas por tipo

# Placa Mercosul (ABC1D23) e o modelo antigo (ABC1234), sempre coladas — o "-" do modelo antigo
# é opcional e o espaço não é aceito (senão "SALA 1234" viraria placa).
_RE_PLACA = re.compile(r"\b([A-Z]{3})-?(\d)([A-Z0-9])(\d{2})\b")


def _so_digitos(texto: str) -> str:
    return re.sub(r"\D", "", texto or "")


def _dv_mod11(base: str, pesos: list[int]) -> int:
    soma = sum(int(d) * p for d, p in zip(base, pesos))
    resto = soma % 11
    return 0 if resto < 2 else 11 - resto


def cpf_valido(valor: str) -> bool:
    d = _so_digitos(valor)
    if len(d) != 11 or len(set(d)) == 1:
        return False
    dv1 = _dv_mod11(d[:9], list(range(10, 1, -1)))
    dv2 = _dv_mod11(d[:10], list(range(11, 1, -1)))
    return d[9] == str(dv1) and d[10] == str(dv2)


def cnpj_valido(valor: str) -> bool:
    d = _so_digitos(valor)
    if len(d) != 14 or len(set(d)) == 1:
        return False
    p1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    p2 = [6] + p1
    return d[12] == str(_dv_mod11(d[:12], p1)) and d[13] == str(_dv_mod11(d[:13], p2))


def _formatar_cpf(d: str) -> str:
    return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"


def _formatar_cnpj(d: str) -> str:
    return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"


def extrair_achados_de_texto(texto: str) -> list[dict]:
    """Achados de interesse do texto, deduplicados por (tipo, valor normalizado), guardando o
    contexto da 1ª ocorrência e contando quantas vezes aparece.

    Devolve `[{tp_achado, tx_valor, vl_numerico, qt_ocorrencias, tx_contexto}]` — o mesmo formato
    de `IPL_TB04_ACHADOS` menos a folha (documento solto não tem folha)."""
    texto = texto or ""
    plano = re.sub(r"[ \t]+", " ", texto)
    achados: dict[tuple[str, str], dict] = {}

    def _registrar(tipo: str, valor: str, numerico, inicio: int, fim: int) -> None:
        valor = _limpo(valor)
        if not valor:
            return
        chave = (tipo, re.sub(r"\s+", "", valor).upper())
        atual = achados.get(chave)
        if atual is not None:
            atual["qt_ocorrencias"] += 1
            return
        if sum(1 for t, _ in achados if t == tipo) >= _MAX_ACHADOS_POR_TIPO:
            return
        achados[chave] = {
            "tp_achado": tipo,
            "tx_valor": valor[:400],
            "vl_numerico": numerico,
            "qt_ocorrencias": 1,
            "tx_contexto": _contexto(plano, inicio, fim),
        }

    for m in _RE_CPF.finditer(plano):
        if cpf_valido(m.group(1)):
            _registrar("cpf", _formatar_cpf(_so_digitos(m.group(1))), None, m.start(), m.end())

    for m in _RE_CNPJ.finditer(plano):
        if cnpj_valido(m.group(1)):
            _registrar("cnpj", _formatar_cnpj(_so_digitos(m.group(1))), None, m.start(), m.end())

    for m in _RE_EMAIL.finditer(plano):
        _registrar("email", m.group(0).lower(), None, m.start(), m.end())

    for m in _RE_TELEFONE.finditer(plano):
        formatado = _telefone_valido(m.group(1), m.group(2), m.group(3))
        if formatado:
            _registrar("telefone", formatado, None, m.start(), m.end())

    for m in _RE_VALOR.finditer(plano):
        numerico = _valor_br(m.group(1))
        if numerico is not None and numerico >= _MIN_VALOR:
            _registrar("valor", "R$ " + _limpo(m.group(1)), numerico, m.start(), m.end())

    for m in _RE_PROCESSO.finditer(plano):
        _registrar("processo", m.group(0), None, m.start(), m.end())

    for m in _RE_ENDERECO.finditer(plano):
        _registrar("endereco", m.group(1), None, m.start(), m.end())

    for m in _RE_CONTA.finditer(plano):
        digitos = _so_digitos(m.group(1))
        if 4 <= len(digitos) <= 15:
            _registrar("conta", digitos, None, m.start(), m.end())

    for m in _RE_AGENCIA.finditer(plano):
        _registrar("agencia", m.group(1), None, m.start(), m.end())

    for m in _RE_OAB.finditer(plano):
        _registrar("oab", _limpo(("OAB/" + m.group(1) if m.group(1) else "OAB") + " " + m.group(2)),
                   None, m.start(), m.end())

    for m in _RE_PROCEDIMENTO.finditer(plano):
        sigla = re.sub(r"\s+", "", m.group(1)).upper().replace("SEIC", "SEI-C")
        _registrar("procedimento", f"{sigla} {m.group(2)}", None, m.start(), m.end())

    for m in _RE_TIPIFICACAO.finditer(plano):
        _registrar("tipificacao", _limpo(m.group(0)), None, m.start(), m.end())

    for m in _RE_PLACA.finditer(plano):
        _registrar("placa_veiculo", "".join(m.groups()), None, m.start(), m.end())

    return sorted(achados.values(), key=lambda a: (a["tp_achado"], -a["qt_ocorrencias"], a["tx_valor"]))


# ---------------------------------------------------------------------------
# Datas do documento -> eventos de uma linha do tempo
# ---------------------------------------------------------------------------

_ANO_MIN, _ANO_MAX = 1990, date.today().year + 2

_RE_DATA_BR = re.compile(r"(?<!\d)(\d{1,2})[/.](\d{1,2})[/.](\d{4})(?!\d)")
_RE_DATA_ISO = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
_RE_DATA_EXTENSO = re.compile(
    r"(\d{1,2})\s+de\s+([A-Za-zçãéêáíóúÇÃÉÊÁÍÓÚ]{3,9})\s+de\s+(\d{4})", re.IGNORECASE)
# datas dentro de citação legal ("Lei 11.419, de 19 de dezembro de 2006") não são evento do caso
_CITACAO_LEGAL = ("LEI ", "LEI N", "DECRETO", "MEDIDA PROVIS", "CODIGO", "CONSTITUI", "SUMULA", "PORTARIA N")
_MAX_EVENTOS = 200


def _sem_acento_maiusc(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto or "") if unicodedata.category(c) != "Mn").upper()


def _em_citacao_legal(plano_maiusc: str, inicio: int) -> bool:
    # janela curta e presa à frase corrente: "Lei X, de <data legal>. Em <data do fato>" —
    # a segunda data não é citação legal só porque a Lei apareceu na frase anterior.
    janela = plano_maiusc[max(0, inicio - 55):inicio]
    corte = max(janela.rfind(". "), janela.rfind("\n"))
    if corte != -1:
        janela = janela[corte + 1:]
    return any(t in janela for t in _CITACAO_LEGAL)


def _iso(dia: int, mes: int, ano: int) -> str | None:
    if not (_ANO_MIN <= ano <= _ANO_MAX):
        return None
    try:
        return date(ano, mes, dia).isoformat()
    except ValueError:
        return None


def extrair_datas_de_texto(texto: str) -> list[dict]:
    """Datas citadas no texto (dd/mm/aaaa, aaaa-mm-dd e '19 de dezembro de 2006'), cada uma com
    um trecho de contexto — a base determinística da linha do tempo do caso. Deduplicada por
    data, ignorando datas dentro de citação legal. Devolve
    `[{dt_evento (ISO 'AAAA-MM-DD'), tx_contexto, qt_ocorrencias}]`, mais antiga primeiro."""
    texto = texto or ""
    plano = re.sub(r"[ \t]+", " ", texto)
    plano_maiusc = _sem_acento_maiusc(plano)
    eventos: dict[str, dict] = {}

    def _registrar(iso: str | None, inicio: int, fim: int) -> None:
        if not iso or _em_citacao_legal(plano_maiusc, inicio):
            return
        atual = eventos.get(iso)
        if atual is not None:
            atual["qt_ocorrencias"] += 1
            return
        if len(eventos) >= _MAX_EVENTOS:
            return
        eventos[iso] = {"dt_evento": iso, "qt_ocorrencias": 1, "tx_contexto": _contexto(plano, inicio, fim)}

    for m in _RE_DATA_BR.finditer(plano):
        _registrar(_iso(int(m.group(1)), int(m.group(2)), int(m.group(3))), m.start(), m.end())
    for m in _RE_DATA_ISO.finditer(plano):
        _registrar(_iso(int(m.group(3)), int(m.group(2)), int(m.group(1))), m.start(), m.end())
    for m in _RE_DATA_EXTENSO.finditer(plano):
        mes = _MESES.get(_sem_acento_maiusc(m.group(2)))
        if mes:
            _registrar(_iso(int(m.group(1)), mes, int(m.group(3))), m.start(), m.end())

    return sorted(eventos.values(), key=lambda e: e["dt_evento"])

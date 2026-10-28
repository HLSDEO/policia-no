"""Busca de uma lista de pessoas/empresas em **tudo** que foi importado para o inquérito.

Antes isto vivia dentro da rota e olhava seis lugares (RIF e SIMBA). A pergunta do investigador,
porém, nunca foi "esse nome está no RIF?" e sim **"esse nome aparece em algum lugar do caso?"** —
o CPF citado no OCR de um documento fotografado, o nome escrito com um erro de digitação na agenda
do celular, o telefone que só existe no extrato da operadora. Daí o registro declarativo de
`FONTES` abaixo: cada fonte diz onde procurar nome, documento, telefone e texto livre, e o mesmo
motor monta a consulta para todas.

Como o casamento é feito
------------------------
Cinco critérios independentes, sempre em OR dentro de cada fonte (quem sabe só o CPF acha mesmo
com o nome grafado de outro jeito; quem só tem o nome ainda acha):

- **hash** — MD5/SHA-1/SHA-256 comparado por igualdade (minúsculo) com o hash de custódia do
  arquivo da extração, do documento anexado e de cada arquivo do aparelho;
- **documento** — CPF/CNPJ comparado por igualdade, dos dois lados reduzidos a dígitos;
- **telefone** — comparado pelo sufixo, porque cada fonte guarda o número com um pedaço a mais
  ou a menos (DDI 55, o nono dígito, o "+" na frente);
- **nome exato** — igualdade depois de normalizar (minúscula, sem acento, pontuação virando
  espaço), que é o que faz "JOSÉ DA SILVA" casar com "Jose da Silva";
- **nome aproximado** (opcional) — `similarity()` do pg_trgm acima do limiar, nome contido no
  campo, ou todos os tokens do nome presentes em qualquer ordem.

Em coluna de **texto livre** (a análise do COAF, o corpo da mensagem, o OCR do arquivo) não há o
que comparar por igualdade: procura-se a *menção*. O modo exato usa ILIKE com as variantes
literais do termo (o que aproveita os índices GIN trigram — ver db/init/37_indices_busca_trgm.sql);
o modo aproximado acrescenta uma expressão regular tolerante a acento e a pontuação, que é o que
encontra o CPF quebrado no meio por uma mudança de linha do PDF (ver docs/rif.md, e a mesma
armadilha em `ipl_utils`). Essa expressão não usa índice — é a parte cara da busca aproximada, e
por isso não é aplicada às fontes marcadas como `pesada` (mensagens, arquivos, navegação).

Nada aqui é IA: é consulta determinística, para que todo achado possa ser reconferido no
documento original.
"""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from funcoes.db_utils import get_connection

logger = logging.getLogger(__name__)

# Teto de linhas devolvidas por fonte e por entidade. A tela é de triagem — o que importa é saber
# *se* e *onde* a pessoa aparece; o volume se vê na tela do documento. O total real de cada fonte
# vem junto (count() sobre a janela), então "50 de 312" continua sendo uma informação honesta.
LIMITE_POR_FONTE = 50
LIMITE_MAX_POR_FONTE = 200

# Acima de quanto duas grafias são "a mesma pessoa" para o pg_trgm. 0.42 foi calibrado no material
# real: pega "JOAO P. DA SILVA" × "JOAO PEDRO DA SILVA" e não junta dois Silvas quaisquer.
LIMIAR_SIMILARIDADE = 0.42

# Quantas fontes são consultadas ao mesmo tempo. Cada uma pega sua conexão do pool; quatro é o
# meio-termo entre a busca terminar rápido e não engolir o pool que as outras telas dividem.
FONTES_EM_PARALELO = 4

_ACENTOS = "áàâãäéèêëíìîïóòôõöúùûüçñ"
_SEM_ACENTO = "aaaaaeeeeiiiiooooouuuucn"
_TABELA_ACENTOS = str.maketrans(_ACENTOS + _ACENTOS.upper(), _SEM_ACENTO + _SEM_ACENTO.upper())

# Vogais e cedilha viram classe na expressão regular do texto: o investigador digita "Joao" e o
# documento traz "João" — sem isto o modo aproximado não acharia justamente o caso mais comum.
_CLASSES_ACENTO = {
    "a": "[aáàâãä]", "e": "[eéèêë]", "i": "[iíìîï]",
    "o": "[oóòôõö]", "u": "[uúùûü]", "c": "[cç]", "n": "[nñ]",
}

# Palavras que não identificam ninguém sozinhas — não valem como token de busca aproximada.
_PARTICULAS = {"de", "da", "do", "das", "dos", "e", "ltda", "me", "epp", "eireli", "sa"}

_SO_DIGITOS = re.compile(r"\D")
_NAO_HEX = re.compile(r"[^0-9a-f]")

# Tamanhos de hash de custódia aceitos: MD5, SHA-1 e SHA-256.
_COMPRIMENTOS_HASH = (32, 40, 64)


def _digitos(valor: str | None) -> str:
    return _SO_DIGITOS.sub("", valor or "")


def _hex(valor: str | None) -> str:
    return _NAO_HEX.sub("", (valor or "").lower())


def normalizar(texto: str | None) -> str:
    """Minúscula, sem acento, pontuação virando espaço — a forma em que dois nomes se comparam."""
    limpo = (texto or "").translate(_TABELA_ACENTOS).lower()
    return re.sub(r"[^a-z0-9]+", " ", limpo).strip()


def _tokens(nome_norm: str) -> list[str]:
    """Tokens que identificam: partícula ("da", "de") e token de uma letra não entram."""
    return [t for t in nome_norm.split() if len(t) > 2 and t not in _PARTICULAS]


def _formatar_documento(digitos: str) -> str | None:
    if len(digitos) == 11:
        return f"{digitos[:3]}.{digitos[3:6]}.{digitos[6:9]}-{digitos[9:]}"
    if len(digitos) == 14:
        return f"{digitos[:2]}.{digitos[2:5]}.{digitos[5:8]}/{digitos[8:12]}-{digitos[12:]}"
    return None


def _variantes_telefone(digitos: str) -> list[str]:
    """As grafias em que o mesmo número aparece: com e sem DDI, com e sem o nono dígito, formatado.

    Serve só para procurar dentro de texto livre; nas colunas de telefone quem compara é o sufixo
    (ver `_sufixo_telefone`), que dispensa adivinhar o formato."""
    nu = digitos.removeprefix("55") if len(digitos) > 11 and digitos.startswith("55") else digitos
    variantes = {digitos, nu}
    if len(nu) >= 10:
        ddd, resto = nu[:2], nu[2:]
        variantes.add(resto)
        variantes.add(f"({ddd}) {resto[:-4]}-{resto[-4:]}")
        variantes.add(f"({ddd}){resto[:-4]}-{resto[-4:]}")
        variantes.add(f"{ddd} {resto[:-4]}-{resto[-4:]}")
        variantes.add(f"{resto[:-4]}-{resto[-4:]}")
    return [v for v in variantes if len(v) >= 8]


def _sufixo_telefone(digitos: str) -> str:
    """Os últimos 8 dígitos — o pedaço que não muda entre DDI, DDD e nono dígito."""
    return digitos[-8:]


def _regex_nome(nome_norm: str) -> str:
    """Regex do nome tolerante a acento e a qualquer pontuação/quebra de linha entre as palavras.

    É o que acha "JOÃO PEDRO\nDA SILVA" partido pela largura fixa da linha do PDF do RIF."""
    tokens = [t for t in nome_norm.split() if t]
    if not tokens:
        return ""
    partes = ["".join(_CLASSES_ACENTO.get(c, re.escape(c)) for c in token) for token in tokens]
    return "[^[:alnum:]]{1,4}".join(partes)


def _regex_documento(digitos: str) -> str:
    """Regex do CPF/CNPJ que aceita qualquer separador entre os algarismos (ponto, barra, hífen,
    espaço e a quebra de linha que o extrator de PDF insere no meio do número)."""
    return "[^0-9]{0,3}".join(digitos)


@dataclass(frozen=True)
class Alvo:
    """Uma entidade procurada, já normalizada e com as variantes prontas para o SQL."""

    indice: int
    nome: str
    cpf_cnpj: str
    telefone: str
    hash: str
    nome_norm: str
    tokens: list[str]
    docs: list[str]
    fones: list[str]
    hashes: list[str]
    textos_like: list[str]
    textos_regex: str

    @property
    def vazio(self) -> bool:
        return not (self.nome_norm or self.docs or self.fones or self.hashes)


def preparar_alvo(indice: int, nome: str | None, cpf_cnpj: str | None,
                  telefone: str | None, aproximada: bool,
                  hash: str | None = None) -> Alvo:
    """Transforma o que veio do formulário nas formas em que o dado aparece guardado."""
    nome = (nome or "").strip()
    nome_norm = normalizar(nome)
    doc = _digitos(cpf_cnpj)
    fone = _digitos(telefone)
    hash_norm = _hex(hash)

    docs = [doc] if len(doc) in (11, 14) else ([doc] if len(doc) >= 8 else [])
    fones = [_sufixo_telefone(fone)] if len(fone) >= 8 else []
    hashes = [hash_norm] if len(hash_norm) in _COMPRIMENTOS_HASH else []

    # Padrões ILIKE para as colunas de texto livre: o nome como foi digitado e cada grafia
    # possível do documento e do telefone.
    like: list[str] = []
    if len(nome_norm) >= 4:
        like.append(f"%{nome}%")
        if nome_norm != nome.lower():
            like.append(f"%{nome_norm}%")
    for d in docs:
        like.append(f"%{d}%")
        formatado = _formatar_documento(d)
        if formatado:
            like.append(f"%{formatado}%")
    if fone:
        like.extend(f"%{v}%" for v in _variantes_telefone(fone))
    # O hash também pode estar citado dentro de um texto (a perícia que transcreve o MD5 do
    # arquivo no laudo) — o ILIKE é insensível a maiúsculas, então só a grafia minúscula basta.
    for h in hashes:
        like.append(f"%{h}%")

    # A expressão regular tolerante só entra no modo aproximado: é ela que não usa índice.
    alternativas: list[str] = []
    if aproximada:
        if len(nome_norm) >= 4:
            alternativas.append(_regex_nome(nome_norm))
        alternativas.extend(_regex_documento(d) for d in docs)
    regex = "|".join(f"(?:{a})" for a in alternativas if a)

    return Alvo(
        indice=indice, nome=nome, cpf_cnpj=cpf_cnpj or "", telefone=telefone or "",
        hash=hash_norm,
        nome_norm=nome_norm, tokens=_tokens(nome_norm), docs=docs, fones=[f"%{f}" for f in fones],
        hashes=hashes, textos_like=sorted(set(like)), textos_regex=regex,
    )


# ---------------------------------------------------------------------------------------------
# Registro de fontes
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Fonte:
    """Um lugar onde uma pessoa pode estar citada.

    origem/onde: o `FROM` (com os joins até chegar ao inquérito) e a condição que prende a
    consulta ao inquérito. O alias da tabela principal é sempre `d`.
    titulo/detalhe/local: expressões SQL que descrevem a linha achada — o que a tela mostra na
    coluna "registro", na linha de apoio e em "de onde veio" (o RIF, o extrato, o aparelho).
    nomes/docs/fones/textos: as colunas em que cada critério procura.
    pesada: fonte de volume (mensagem, arquivo, chamada) — fica de fora da regex tolerante.
    """

    chave: str
    rotulo: str
    grupo: str
    origem: str
    onde: str
    id_col: str
    titulo: str
    detalhe: str = "NULL"
    local: str = "NULL"
    data: str | None = None
    nomes: tuple[str, ...] = ()
    docs: tuple[str, ...] = ()
    fones: tuple[str, ...] = ()
    # Colunas de hash de custódia (MD5/SHA-1/SHA-256) — o critério "hash" compara por igualdade,
    # em minúsculo, com o hash digitado.
    hashes: tuple[str, ...] = ()
    textos: tuple[str, ...] = ()
    pesada: bool = False
    dica: str = ""
    # URL que abre a evidência onde a linha foi achada (página da fonte, já com o id do registro e
    # o deep-link `destacar`/`msg`). NULL quando a fonte não tem tela própria para abrir o registro.
    link: str = "NULL"


# Chaves de disponibilidade: uma fonte só é consultada se o inquérito tem aquele tipo de material
# (ver `fontes_disponiveis`). É o que impede uma busca de varrer quarenta tabelas vazias.
GRUPOS = {
    "rif": "RIF (COAF)",
    "simba": "Extrato bancário (SIMBA)",
    "ipl": "Inquérito em PDF",
    "irpf": "Declaração de IRPF",
    "ipei": "IPEI (patrimonial)",
    "telefonia": "Telefonia (operadora)",
    "documentos": "Documentos anexados",
    "celular": "Celular / extração",
    "nuvem": "Contas na nuvem",
    "analise": "Análise do caso",
}

_RIF_ORIGEM = "investigacao.rif_tb01_rifs r"
_RIF_LOCAL = "coalesce('RIF ' || r.nu_rif, r.no_pasta)"
_SMB_LOCAL = "s.no_arquivo"
_EXT_LOCAL = "coalesce(e.no_dispositivo, e.no_arquivo)"
_EXTRACAO = "JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = d.id_extracao"
_ONDE_EXTRACAO = "e.id_inquerito = %(inq)s"


FONTES: tuple[Fonte, ...] = (
    # ---------- RIF ----------
    Fonte(
        chave="rif_envolvidos", rotulo="RIF · envolvidos", grupo="rif",
        origem=f"investigacao.rif_tb03_envolvidos d JOIN {_RIF_ORIGEM} ON r.id_rif = d.id_rif",
        onde="r.id_inquerito = %(inq)s",
        id_col="d.id_envolvido_rif", titulo="d.no_envolvido",
        detalhe="concat_ws(' · ', d.tp_envolvido, nullif(d.nu_cpf_cnpj, ''), "
                "nullif(concat_ws('/', d.nu_agencia, d.nu_conta), ''), nullif(d.tx_pep, ''))",
        local=_RIF_LOCAL,
        nomes=("d.no_envolvido",), docs=("d.nu_cpf_cnpj",),
        link="'rif.html?inquerito=' || r.id_inquerito || '&rif=' || r.id_rif",
    ),
    Fonte(
        chave="rif_comunicacoes", rotulo="RIF · comunicações (texto original)", grupo="rif",
        origem=f"investigacao.rif_tb02_comunicacoes d JOIN {_RIF_ORIGEM} ON r.id_rif = d.id_rif",
        onde="r.id_inquerito = %(inq)s",
        id_col="d.id_comunicacao_rif",
        titulo="concat_ws(' ', 'Comunicação', d.nu_indexador)",
        detalhe="concat_ws(' · ', d.no_comunicante, nullif(d.no_agencia, ''), "
                "nullif(d.cd_segmento, ''))",
        local=_RIF_LOCAL, data="d.dt_operacao",
        nomes=("d.no_comunicante",), docs=("d.nu_cpf_cnpj_comunicante",),
        textos=("d.tx_informacoes_adicionais",),
        dica="a análise que o banco escreveu — é ali que o nome de quem depositou costuma estar",
        link="'rif.html?inquerito=' || r.id_inquerito || '&rif=' || r.id_rif",
    ),
    Fonte(
        chave="rif_contrapartes", rotulo="RIF · contrapartes", grupo="rif",
        origem=f"investigacao.rif_tb05_contrapartes d JOIN {_RIF_ORIGEM} ON r.id_rif = d.id_rif",
        onde="r.id_inquerito = %(inq)s",
        id_col="d.id_contraparte_rif", titulo="d.no_contraparte",
        detalhe="concat_ws(' · ', d.cd_direcao, nullif(d.nu_cpf_cnpj, ''), "
                "d.qt_lancamentos || ' lançamento(s)', 'R$ ' || d.vl_total, nullif(d.tx_obs, ''))",
        local=_RIF_LOCAL,
        nomes=("d.no_contraparte",), docs=("d.nu_cpf_cnpj",), textos=("d.tx_obs",),
        link="'rif.html?inquerito=' || r.id_inquerito || '&rif=' || r.id_rif",
    ),
    Fonte(
        chave="rif_identificacoes", rotulo="RIF · identificações do investigador", grupo="rif",
        origem=f"investigacao.rif_tb06_identificacoes d JOIN {_RIF_ORIGEM} ON r.id_rif = d.id_rif",
        onde="r.id_inquerito = %(inq)s",
        id_col="d.id_identificacao_rif", titulo="d.no_contraparte",
        detalhe="concat_ws(' · ', d.cd_direcao, nullif(d.nu_cpf_cnpj, ''), "
                "'R$ ' || d.vl_total, nullif(d.tx_obs, ''))",
        local=_RIF_LOCAL,
        nomes=("d.no_contraparte",), docs=("d.nu_cpf_cnpj",), textos=("d.tx_obs",),
        link="'rif.html?inquerito=' || r.id_inquerito || '&rif=' || r.id_rif",
    ),
    Fonte(
        chave="rif_ocorrencias", rotulo="RIF · ocorrências", grupo="rif",
        origem=f"investigacao.rif_tb04_ocorrencias d JOIN {_RIF_ORIGEM} ON r.id_rif = d.id_rif",
        onde="r.id_inquerito = %(inq)s",
        id_col="d.id_ocorrencia_rif",
        titulo="concat_ws(' ', 'Ocorrência', d.id_ocorrencia)",
        detalhe="left(d.tx_ocorrencia, 200)", local=_RIF_LOCAL,
        textos=("d.tx_ocorrencia",),
        link="'rif.html?inquerito=' || r.id_inquerito || '&rif=' || r.id_rif",
    ),
    # ---------- SIMBA ----------
    Fonte(
        chave="simba_contas", rotulo="SIMBA · contas", grupo="simba",
        origem="investigacao.smb_tb02_contas d "
               "JOIN investigacao.smb_tb01_extratos s ON s.id_extrato = d.id_extrato",
        onde="s.id_inquerito = %(inq)s",
        id_col="d.id_conta_simba", titulo="d.no_titular",
        detalhe="concat_ws(' · ', nullif(d.nu_cpf_cnpj, ''), d.no_banco, "
                "nullif(concat_ws('/', d.nu_agencia, d.nu_conta), ''), d.tp_conta)",
        local=_SMB_LOCAL,
        nomes=("d.no_titular",), docs=("d.nu_cpf_cnpj",), textos=("d.tx_identificador",),
        link="'simba.html?inquerito=' || s.id_inquerito || '&simba=' || s.id_extrato",
    ),
    Fonte(
        chave="simba_lancamentos", rotulo="SIMBA · lançamentos", grupo="simba",
        origem="investigacao.smb_tb03_lancamentos d "
               "JOIN investigacao.smb_tb01_extratos s ON s.id_extrato = d.id_extrato "
               "JOIN investigacao.smb_tb02_contas c ON c.id_conta_simba = d.id_conta_simba",
        onde="s.id_inquerito = %(inq)s",
        id_col="d.id_lancamento_simba",
        titulo="concat_ws(' ', case d.cd_natureza when 'C' then 'Crédito' when 'D' then 'Débito' "
               "else d.cd_natureza end, 'R$ ' || d.vl_operacao)",
        detalhe="concat_ws(' · ', nullif(d.no_od, ''), nullif(d.nu_cpf_cnpj_od, ''), "
                "nullif(d.tx_descricao, ''), 'conta de ' || c.no_titular)",
        local=_SMB_LOCAL, data="d.dt_lancamento",
        nomes=("d.no_od",), docs=("d.nu_cpf_cnpj_od",),
        textos=("d.tx_descricao", "d.tx_observacao_od", "d.no_endossante_cheque"),
        pesada=True,
        link="'simba.html?inquerito=' || s.id_inquerito || '&simba=' || s.id_extrato",
    ),
    Fonte(
        chave="simba_contrapartes", rotulo="SIMBA · contrapartes consolidadas", grupo="simba",
        origem="investigacao.smb_tb04_contrapartes d "
               "JOIN investigacao.smb_tb01_extratos s ON s.id_extrato = d.id_extrato "
               "JOIN investigacao.smb_tb02_contas c ON c.id_conta_simba = d.id_conta_simba",
        onde="s.id_inquerito = %(inq)s",
        id_col="d.id_contraparte_simba", titulo="d.no_contraparte",
        detalhe="concat_ws(' · ', d.cd_direcao, nullif(d.nu_cpf_cnpj, ''), "
                "d.qt_lancamentos || ' lançamento(s)', 'R$ ' || d.vl_total, "
                "'conta de ' || c.no_titular)",
        local=_SMB_LOCAL,
        nomes=("d.no_contraparte",), docs=("d.nu_cpf_cnpj",),
        link="'simba.html?inquerito=' || s.id_inquerito || '&simba=' || s.id_extrato",
    ),
    # ---------- Inquérito em PDF ----------
    Fonte(
        chave="ipl_pessoas", rotulo="Inquérito PDF · pessoas citadas", grupo="ipl",
        origem="investigacao.ipl_tb03_pessoas d "
               "JOIN investigacao.ipl_tb01_inqueritos_pdf p ON p.id_ipl = d.id_ipl",
        onde="p.id_inquerito = %(inq)s",
        id_col="d.id_pessoa_ipl", titulo="d.no_pessoa",
        detalhe="concat_ws(' · ', d.tp_papel, nullif(d.nu_cpf_cnpj, ''), nullif(d.no_vulgo, ''), "
                "'fl. ' || d.nu_folha, d.qt_mencoes || ' menção(ões)')",
        local="coalesce(p.nu_ipl, p.no_arquivo)",
        nomes=("d.no_pessoa", "d.no_variantes", "d.no_vulgo"), docs=("d.nu_cpf_cnpj",),
        textos=("d.tx_contexto",),
        link="'inquerito_pdf.html?inquerito=' || p.id_inquerito || '&ipl=' || p.id_ipl",
    ),
    Fonte(
        chave="ipl_achados", rotulo="Inquérito PDF · achados no texto", grupo="ipl",
        origem="investigacao.ipl_tb04_achados d "
               "JOIN investigacao.ipl_tb01_inqueritos_pdf p ON p.id_ipl = d.id_ipl",
        onde="p.id_inquerito = %(inq)s",
        id_col="d.id_achado_ipl", titulo="concat_ws(': ', d.tp_achado, d.tx_valor)",
        detalhe="concat_ws(' · ', 'fl. ' || d.nu_folha, d.qt_ocorrencias || ' ocorrência(s)')",
        local="coalesce(p.nu_ipl, p.no_arquivo)",
        textos=("d.tx_valor", "d.tx_contexto"),
        link="'inquerito_pdf.html?inquerito=' || p.id_inquerito || '&ipl=' || p.id_ipl",
    ),
    Fonte(
        chave="ipl_pecas", rotulo="Inquérito PDF · peças", grupo="ipl",
        origem="investigacao.ipl_tb02_pecas d "
               "JOIN investigacao.ipl_tb01_inqueritos_pdf p ON p.id_ipl = d.id_ipl",
        onde="p.id_inquerito = %(inq)s",
        id_col="d.id_peca", titulo="concat_ws(' ', d.tp_peca, d.nu_peca)",
        detalhe="concat_ws(' · ', nullif(d.no_orgao, ''), nullif(d.no_assinante, ''), "
                "'fls. ' || concat_ws('-', d.nu_folha_inicial, d.nu_folha_final))",
        local="coalesce(p.nu_ipl, p.no_arquivo)", data="d.dt_peca",
        nomes=("d.no_assinante",), textos=("d.tx_resumo",),
        link="'inquerito_pdf.html?inquerito=' || p.id_inquerito || '&ipl=' || p.id_ipl",
    ),
    Fonte(
        chave="ipl_cabecalho", rotulo="Inquérito PDF · resumo dos fatos", grupo="ipl",
        origem="investigacao.ipl_tb01_inqueritos_pdf d",
        onde="d.id_inquerito = %(inq)s",
        id_col="d.id_ipl", titulo="coalesce(d.nu_ipl, d.no_arquivo)",
        detalhe="concat_ws(' · ', nullif(d.no_unidade, ''), nullif(d.no_autoridade, ''), "
                "nullif(d.tx_tipificacao, ''))",
        local="d.no_arquivo", data="d.dt_instauracao",
        nomes=("d.no_autoridade",), textos=("d.tx_resumo_fatos", "d.tx_tipificacao"),
        link="'inquerito_pdf.html?inquerito=' || d.id_inquerito || '&ipl=' || d.id_ipl",
    ),
    # ---------- IRPF ----------
    Fonte(
        chave="irpf_declaracao", rotulo="IRPF · declarante", grupo="irpf",
        origem="investigacao.irp_tb01_declaracoes d",
        onde="d.id_inquerito = %(inq)s",
        id_col="d.id_irpf", titulo="d.no_titular",
        detalhe="concat_ws(' · ', nullif(d.nu_cpf, ''), 'exercício ' || d.nu_exercicio, "
                "nullif(d.tx_ocupacao_principal, ''), nullif(d.no_municipio, ''))",
        local="d.no_arquivo",
        nomes=("d.no_titular",), docs=("d.nu_cpf",), fones=("d.nu_telefone", "d.nu_celular"),
        textos=("d.tx_endereco", "d.no_email"),
        link="'declaracao_irpf.html?inquerito=' || d.id_inquerito || '&irpf=' || d.id_irpf",
    ),
    Fonte(
        chave="irpf_dependentes", rotulo="IRPF · dependentes", grupo="irpf",
        origem="investigacao.irp_tb03_dependentes d "
               "JOIN investigacao.irp_tb01_declaracoes p ON p.id_irpf = d.id_irpf",
        onde="p.id_inquerito = %(inq)s",
        id_col="d.id_dependente", titulo="d.no_dependente",
        detalhe="concat_ws(' · ', nullif(d.nu_cpf, ''), nullif(d.tx_relacao, ''), "
                "'declaração de ' || p.no_titular)",
        local="p.no_arquivo",
        nomes=("d.no_dependente",), docs=("d.nu_cpf",),
        link="'declaracao_irpf.html?inquerito=' || p.id_inquerito || '&irpf=' || p.id_irpf",
    ),
    Fonte(
        chave="irpf_rendimentos", rotulo="IRPF · fontes pagadoras", grupo="irpf",
        origem="investigacao.irp_tb04_rendimentos d "
               "JOIN investigacao.irp_tb01_declaracoes p ON p.id_irpf = d.id_irpf",
        onde="p.id_inquerito = %(inq)s",
        id_col="d.id_rendimento", titulo="d.no_fonte_pagadora",
        detalhe="concat_ws(' · ', nullif(d.nu_cnpj_cpf, ''), d.tp_origem, "
                "'R$ ' || d.vl_rendimento, 'declaração de ' || p.no_titular)",
        local="p.no_arquivo",
        nomes=("d.no_fonte_pagadora",), docs=("d.nu_cnpj_cpf",),
        link="'declaracao_irpf.html?inquerito=' || p.id_inquerito || '&irpf=' || p.id_irpf",
    ),
    Fonte(
        chave="irpf_doacoes", rotulo="IRPF · doações e pagamentos", grupo="irpf",
        origem="investigacao.irp_tb06_doacoes d "
               "JOIN investigacao.irp_tb01_declaracoes p ON p.id_irpf = d.id_irpf",
        onde="p.id_inquerito = %(inq)s",
        id_col="d.id_doacao", titulo="d.no_beneficiario",
        detalhe="concat_ws(' · ', nullif(d.nu_cnpj_cpf, ''), d.tp_doacao, 'R$ ' || d.vl_doacao, "
                "'declaração de ' || p.no_titular)",
        local="p.no_arquivo",
        nomes=("d.no_beneficiario",), docs=("d.nu_cnpj_cpf",),
        link="'declaracao_irpf.html?inquerito=' || p.id_inquerito || '&irpf=' || p.id_irpf",
    ),
    Fonte(
        chave="irpf_bens", rotulo="IRPF · bens e dívidas", grupo="irpf",
        origem="(SELECT id_bem AS id_linha, id_irpf, no_codigo, tx_discriminacao, vl_ano_atual, "
               "       'bem' AS tp_linha FROM investigacao.irp_tb02_bens_direitos "
               " UNION ALL "
               " SELECT id_divida, id_irpf, no_codigo, tx_discriminacao, vl_ano_atual, 'dívida' "
               "   FROM investigacao.irp_tb05_dividas) d "
               "JOIN investigacao.irp_tb01_declaracoes p ON p.id_irpf = d.id_irpf",
        onde="p.id_inquerito = %(inq)s",
        id_col="d.id_linha", titulo="concat_ws(' · ', d.tp_linha, d.no_codigo)",
        detalhe="concat_ws(' · ', left(d.tx_discriminacao, 200), 'R$ ' || d.vl_ano_atual, "
                "'declaração de ' || p.no_titular)",
        local="p.no_arquivo",
        textos=("d.tx_discriminacao",),
        link="'declaracao_irpf.html?inquerito=' || p.id_inquerito || '&irpf=' || p.id_irpf",
    ),
    # ---------- IPEI ----------
    Fonte(
        chave="ipei_alvos", rotulo="IPEI · alvos", grupo="ipei",
        origem="investigacao.ipe_tb02_alvos d "
               "JOIN investigacao.ipe_tb01_ipeis p ON p.id_ipei = d.id_ipei",
        onde="p.id_inquerito = %(inq)s",
        id_col="d.id_alvo", titulo="d.no_alvo",
        detalhe="concat_ws(' · ', nullif(d.nu_cpf_cnpj, ''), nullif(d.tx_ocupacao, ''), "
                "nullif(d.no_municipio, ''), nullif(d.tx_situacao_cadastral, ''))",
        local="coalesce(p.nu_ipei, p.no_arquivo)",
        nomes=("d.no_alvo", "d.no_mae"), docs=("d.nu_cpf_cnpj",), textos=("d.tx_endereco",),
        link="'ipei.html?inquerito=' || p.id_inquerito || '&ipei=' || p.id_ipei",
    ),
    Fonte(
        chave="ipei_relacionados", rotulo="IPEI · relacionados", grupo="ipei",
        origem="investigacao.ipe_tb03_relacionados d "
               "JOIN investigacao.ipe_tb01_ipeis p ON p.id_ipei = d.id_ipei "
               "LEFT JOIN investigacao.ipe_tb02_alvos al ON al.id_alvo = d.id_alvo",
        onde="p.id_inquerito = %(inq)s",
        id_col="d.id_relacionado", titulo="d.no_relacionado",
        detalhe="concat_ws(' · ', d.tp_relacao, nullif(d.nu_cpf_cnpj, ''), "
                "nullif(d.tx_qualificacao, ''), 'alvo: ' || al.no_alvo)",
        local="coalesce(p.nu_ipei, p.no_arquivo)",
        nomes=("d.no_relacionado",), docs=("d.nu_cpf_cnpj",),
        textos=("d.tx_qualificacao", "d.tx_contexto"),
        link="'ipei.html?inquerito=' || p.id_inquerito || '&ipei=' || p.id_ipei",
    ),
    Fonte(
        chave="ipei_fontes", rotulo="IPEI · fontes de renda", grupo="ipei",
        origem="investigacao.ipe_tb05_fontes d "
               "JOIN investigacao.ipe_tb01_ipeis p ON p.id_ipei = d.id_ipei "
               "LEFT JOIN investigacao.ipe_tb02_alvos al ON al.id_alvo = d.id_alvo",
        onde="p.id_inquerito = %(inq)s",
        id_col="d.id_fonte", titulo="d.no_fonte",
        detalhe="concat_ws(' · ', nullif(d.nu_cpf_cnpj, ''), d.tp_registro, d.nu_ano::text, "
                "'R$ ' || d.vl_rendimento, 'alvo: ' || al.no_alvo)",
        local="coalesce(p.nu_ipei, p.no_arquivo)",
        nomes=("d.no_fonte",), docs=("d.nu_cpf_cnpj",), textos=("d.tx_natureza",),
        link="'ipei.html?inquerito=' || p.id_inquerito || '&ipei=' || p.id_ipei",
    ),
    Fonte(
        chave="ipei_contas", rotulo="IPEI · contas bancárias", grupo="ipei",
        origem="investigacao.ipe_tb06_contas d "
               "JOIN investigacao.ipe_tb01_ipeis p ON p.id_ipei = d.id_ipei "
               "LEFT JOIN investigacao.ipe_tb02_alvos al ON al.id_alvo = d.id_alvo",
        onde="p.id_inquerito = %(inq)s",
        id_col="d.id_conta", titulo="concat_ws(' · ', d.no_banco, d.nu_conta)",
        detalhe="concat_ws(' · ', nullif(d.no_titular, ''), nullif(d.tx_relacao, ''), "
                "d.nu_ano::text, 'alvo: ' || al.no_alvo)",
        local="coalesce(p.nu_ipei, p.no_arquivo)",
        nomes=("d.no_titular",), docs=("d.nu_titular",), textos=("d.tx_relacao",),
        link="'ipei.html?inquerito=' || p.id_inquerito || '&ipei=' || p.id_ipei",
    ),
    Fonte(
        chave="ipei_notas", rotulo="IPEI · notas fiscais", grupo="ipei",
        origem="investigacao.ipe_tb08_notas_fiscais d "
               "JOIN investigacao.ipe_tb01_ipeis p ON p.id_ipei = d.id_ipei",
        onde="p.id_inquerito = %(inq)s",
        id_col="d.id_nota", titulo="concat_ws(' → ', d.no_emitente, d.no_destinatario)",
        detalhe="concat_ws(' · ', 'NF ' || d.nu_nota, nullif(d.nu_cnpj_emitente, ''), "
                "nullif(d.nu_cpf_destinatario, ''), 'R$ ' || d.vl_total, "
                "left(d.tx_descricao, 120))",
        local="coalesce(p.nu_ipei, p.no_arquivo)", data="d.dt_emissao",
        nomes=("d.no_emitente", "d.no_destinatario"),
        docs=("d.nu_cnpj_emitente", "d.nu_cpf_destinatario"),
        textos=("d.tx_descricao",),
        link="'ipei.html?inquerito=' || p.id_inquerito || '&ipei=' || p.id_ipei",
    ),
    Fonte(
        chave="ipei_bens", rotulo="IPEI · bens declarados", grupo="ipei",
        origem="investigacao.ipe_tb07_bens d "
               "JOIN investigacao.ipe_tb01_ipeis p ON p.id_ipei = d.id_ipei "
               "LEFT JOIN investigacao.ipe_tb02_alvos al ON al.id_alvo = d.id_alvo",
        onde="p.id_inquerito = %(inq)s",
        id_col="d.id_bem", titulo="concat_ws(' · ', d.tp_bem, d.nu_identificador)",
        detalhe="concat_ws(' · ', left(d.tx_descricao, 200), 'R$ ' || d.vl_ano, "
                "'alvo: ' || al.no_alvo)",
        local="coalesce(p.nu_ipei, p.no_arquivo)",
        textos=("d.tx_descricao", "d.tx_contexto", "d.nu_identificador"),
        link="'ipei.html?inquerito=' || p.id_inquerito || '&ipei=' || p.id_ipei",
    ),
    Fonte(
        chave="ipei_contatos", rotulo="IPEI · contatos do alvo", grupo="ipei",
        origem="investigacao.ipe_tb09_contatos d "
               "JOIN investigacao.ipe_tb01_ipeis p ON p.id_ipei = d.id_ipei "
               "LEFT JOIN investigacao.ipe_tb02_alvos al ON al.id_alvo = d.id_alvo",
        onde="p.id_inquerito = %(inq)s",
        id_col="d.id_contato", titulo="concat_ws(': ', d.tp_contato, d.tx_valor)",
        detalhe="concat_ws(' · ', d.nu_ano::text, 'alvo: ' || al.no_alvo)",
        local="coalesce(p.nu_ipei, p.no_arquivo)",
        fones=("d.tx_valor",), textos=("d.tx_valor", "d.tx_contexto"),
        link="'ipei.html?inquerito=' || p.id_inquerito || '&ipei=' || p.id_ipei",
    ),
    # ---------- Telefonia ----------
    Fonte(
        chave="tel_linhas", rotulo="Telefonia · cadastro da linha", grupo="telefonia",
        origem="investigacao.tel_tb03_linhas d "
               "JOIN investigacao.tel_tb01_telefonia t ON t.id_telefonia = d.id_telefonia",
        onde="t.id_inquerito = %(inq)s",
        id_col="d.id_linha_telefonia", titulo="concat_ws(' · ', d.nu_linha, d.no_cliente)",
        detalhe="concat_ws(' · ', nullif(d.nu_cpf_cnpj, ''), nullif(d.no_mae, ''), "
                "nullif(d.tp_situacao, ''), nullif(d.no_municipio, ''))",
        local="t.no_operadora",
        nomes=("d.no_cliente", "d.no_mae"), docs=("d.nu_cpf_cnpj",), fones=("d.nu_linha",),
        textos=("d.tx_endereco",),
        link="'telefonia.html?inquerito=' || t.id_inquerito || '&telefonia=' || t.id_telefonia",
    ),
    Fonte(
        chave="tel_chamadas", rotulo="Telefonia · chamadas", grupo="telefonia",
        origem="investigacao.tel_tb04_chamadas d "
               "JOIN investigacao.tel_tb01_telefonia t ON t.id_telefonia = d.id_telefonia",
        onde="t.id_inquerito = %(inq)s",
        id_col="d.id_chamada_telefonia",
        titulo="concat_ws(' → ', d.nu_chamador, d.nu_chamado)",
        detalhe="concat_ws(' · ', d.tp_registro, d.qt_duracao_segundos || 's', "
                "nullif(d.no_servico, ''))",
        local="t.no_operadora", data="d.dt_chamada",
        fones=("d.nu_chamador", "d.nu_chamado"), pesada=True,
        link="'telefonia.html?inquerito=' || t.id_inquerito || '&telefonia=' || t.id_telefonia",
    ),
    Fonte(
        chave="tel_numeros", rotulo="Telefonia · números e IMEI", grupo="telefonia",
        origem="investigacao.tel_tb06_numeros d "
               "JOIN investigacao.tel_tb01_telefonia t ON t.id_telefonia = d.id_telefonia",
        onde="t.id_inquerito = %(inq)s",
        id_col="d.id_numero_telefonia", titulo="d.nu_telefone",
        detalhe="concat_ws(' · ', nullif(d.nu_imei, ''), d.tp_fonte)",
        local="t.no_operadora",
        fones=("d.nu_telefone",), textos=("d.nu_imei",),
        link="'telefonia.html?inquerito=' || t.id_inquerito || '&telefonia=' || t.id_telefonia",
    ),
    # ---------- Documentos anexados ----------
    Fonte(
        chave="documentos", rotulo="Documentos anexados (texto)", grupo="documentos",
        origem="investigacao.inq_tb04_documentos d",
        onde="d.id_inquerito = %(inq)s",
        id_col="d.id_documento", titulo="d.no_arquivo",
        detalhe="concat_ws(' · ', d.tp_arquivo, d.qt_caracteres || ' caractere(s)')",
        local="'documento anexado'", data="d.dt_processado",
        hashes=("d.co_hash_md5", "d.co_hash_sha1", "d.co_hash_sha256"),
        textos=("d.tx_conteudo",),
        dica="o texto inteiro do PDF/DOCX anexado ao inquérito — e o hash de custódia do arquivo",
        link="'documento.html?inquerito=' || d.id_inquerito || '&doc=' || d.id_documento",
    ),
    # ---------- Arquivo da extração (custódia do .ufdr) ----------
    Fonte(
        chave="extracao_arquivo", rotulo="Celular · arquivo da extração (.ufdr)", grupo="celular",
        origem="investigacao.inq_tb02_extracoes d",
        onde="d.id_inquerito = %(inq)s",
        id_col="d.id_extracao",
        titulo="coalesce(nullif(d.no_apelido, ''), nullif(d.no_dispositivo, ''), d.no_arquivo)",
        detalhe="concat_ws(' · ', case when d.in_nuvem then 'nuvem' else 'aparelho' end, "
                "nullif(d.no_fabricante, ''), d.cd_status)",
        local="d.no_arquivo", data="d.dt_upload",
        hashes=("d.co_hash_md5", "d.co_hash_sha1", "d.co_hash_sha256"),
        dica="o hash de custódia do arquivo .ufdr importado",
        link="'arquivos.html?inquerito=' || d.id_inquerito || '&extracao=' || d.id_extracao",
    ),
    # ---------- Celular / extração ----------
    Fonte(
        chave="agenda", rotulo="Celular · agenda", grupo="celular",
        origem=f"investigacao.ctt_tb01_contatos d {_EXTRACAO}", onde=_ONDE_EXTRACAO,
        id_col="d.id_contato", titulo="d.no_contato",
        detalhe="concat_ws(' · ', nullif(d.no_aplicativo_origem, ''), nullif(d.no_organizacao, ''), "
                "nullif(left(d.tx_notas, 120), ''))",
        local=_EXT_LOCAL,
        nomes=("d.no_contato", "d.no_organizacao"),
        textos=("d.tx_notas", "d.tx_endereco", "d.tx_info_adicional"),
        link="'contatos.html?inquerito=' || e.id_inquerito || '&extracao=' || e.id_extracao "
             "|| '&destacar=' || d.id_contato",
    ),
    Fonte(
        chave="agenda_entradas", rotulo="Celular · telefones e e-mails da agenda", grupo="celular",
        origem="investigacao.ctt_tb02_contato_entradas d "
               "JOIN investigacao.ctt_tb01_contatos ct ON ct.id_contato = d.id_contato "
               "JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = ct.id_extracao",
        onde=_ONDE_EXTRACAO,
        id_col="d.id_entrada", titulo="concat_ws(': ', d.tp_entrada, d.tx_valor)",
        detalhe="concat_ws(' · ', ct.no_contato, nullif(d.no_categoria, ''))",
        local=_EXT_LOCAL,
        fones=("d.tx_valor",), textos=("d.tx_valor",),
        link="'contatos.html?inquerito=' || e.id_inquerito || '&extracao=' || e.id_extracao "
             "|| '&destacar=' || ct.id_contato",
    ),
    Fonte(
        chave="wpp_contatos", rotulo="WhatsApp · contatos", grupo="celular",
        origem=f"investigacao.wpp_tb01_contatos d {_EXTRACAO}", onde=_ONDE_EXTRACAO,
        id_col="d.id_contato", titulo="coalesce(d.no_contato, d.nu_whatsapp_id)",
        detalhe="concat_ws(' · ', d.nu_whatsapp_id, "
                "case when d.in_dono_dispositivo then 'dono do aparelho' end)",
        local=_EXT_LOCAL,
        nomes=("d.no_contato",), fones=("d.nu_whatsapp_id",), textos=("d.nu_whatsapp_id",),
        link="'whatsapp.html?inquerito=' || e.id_inquerito || '&extracao=' || e.id_extracao "
             "|| '&fonte=wpp'",
    ),
    Fonte(
        chave="wpp_mensagens", rotulo="WhatsApp · mensagens", grupo="celular",
        origem="investigacao.wpp_tb04_mensagens d "
               "JOIN investigacao.wpp_tb02_conversas cv ON cv.id_conversa = d.id_conversa "
               "JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = cv.id_extracao "
               "LEFT JOIN investigacao.wpp_tb01_contatos ct "
               "  ON ct.id_contato = d.id_contato_remetente",
        onde=_ONDE_EXTRACAO,
        id_col="d.id_mensagem",
        titulo="coalesce(nullif(cv.no_grupo, ''), coalesce(ct.no_contato, ct.nu_whatsapp_id), "
               "'conversa ' || cv.id_conversa)",
        detalhe="concat_ws(' · ', case when d.in_enviada then 'enviada' else 'recebida' end, "
                "coalesce(ct.no_contato, ct.nu_whatsapp_id))",
        local=_EXT_LOCAL, data="d.dt_mensagem",
        textos=("d.tx_mensagem",), pesada=True,
        link="'whatsapp.html?inquerito=' || e.id_inquerito || '&fonte=wpp&msg=' || d.id_mensagem",
    ),
    Fonte(
        chave="wpp_anexos", rotulo="WhatsApp · anexos e transcrições", grupo="celular",
        origem="investigacao.wpp_tb05_anexos d "
               "JOIN investigacao.wpp_tb04_mensagens m ON m.id_mensagem = d.id_mensagem "
               "JOIN investigacao.wpp_tb02_conversas cv ON cv.id_conversa = m.id_conversa "
               "JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = cv.id_extracao",
        onde=_ONDE_EXTRACAO,
        id_col="d.id_anexo", titulo="coalesce(nullif(d.tx_titulo, ''), d.no_arquivo)",
        detalhe="concat_ws(' · ', d.tp_conteudo, nullif(cv.no_grupo, ''))",
        local=_EXT_LOCAL, data="m.dt_mensagem",
        textos=("d.tx_transcricao", "d.tx_titulo", "d.no_arquivo"), pesada=True,
        link="'whatsapp.html?inquerito=' || e.id_inquerito || '&fonte=wpp&msg=' || m.id_mensagem",
    ),
    Fonte(
        chave="sms_contatos", rotulo="SMS · contatos", grupo="celular",
        origem=f"investigacao.sms_tb01_contatos d {_EXTRACAO}", onde=_ONDE_EXTRACAO,
        id_col="d.id_contato", titulo="coalesce(d.no_contato, d.nu_telefone)",
        detalhe="d.nu_telefone", local=_EXT_LOCAL,
        nomes=("d.no_contato",), fones=("d.nu_telefone",),
        link="'mensageiros.html?inquerito=' || e.id_inquerito || '&extracao=' || e.id_extracao "
             "|| '&fonte=sms'",
    ),
    Fonte(
        chave="sms_mensagens", rotulo="SMS · mensagens", grupo="celular",
        origem="investigacao.sms_tb04_mensagens d "
               "JOIN investigacao.sms_tb02_conversas cv ON cv.id_conversa = d.id_conversa "
               "JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = cv.id_extracao "
               "LEFT JOIN investigacao.sms_tb01_contatos ct "
               "  ON ct.id_contato = d.id_contato_remetente",
        onde=_ONDE_EXTRACAO,
        id_col="d.id_mensagem",
        titulo="coalesce(ct.no_contato, ct.nu_telefone, nullif(cv.no_grupo, ''), "
               "'conversa ' || cv.id_conversa)",
        detalhe="concat_ws(' · ', case when d.in_enviada then 'enviada' else 'recebida' end, "
                "nullif(d.tx_destinatarios, ''))",
        local=_EXT_LOCAL, data="d.dt_mensagem",
        textos=("d.tx_mensagem",), pesada=True,
        link="'mensageiros.html?inquerito=' || e.id_inquerito || '&fonte=sms&msg=' || d.id_mensagem",
    ),
    Fonte(
        chave="msg_contatos", rotulo="Mensageiros · contatos", grupo="celular",
        origem=f"investigacao.msg_tb01_contatos d {_EXTRACAO}", onde=_ONDE_EXTRACAO,
        id_col="d.id_contato", titulo="coalesce(d.no_contato, d.nu_identificador)",
        detalhe="concat_ws(' · ', d.tp_app, d.nu_identificador)", local=_EXT_LOCAL,
        nomes=("d.no_contato",), fones=("d.nu_identificador",), textos=("d.nu_identificador",),
        link="'mensageiros.html?inquerito=' || e.id_inquerito || '&extracao=' || e.id_extracao "
             "|| '&fonte=msg&app=' || replace(d.tp_app, ' ', '%%20')",
    ),
    Fonte(
        chave="msg_mensagens", rotulo="Mensageiros · mensagens", grupo="celular",
        origem="investigacao.msg_tb04_mensagens d "
               "JOIN investigacao.msg_tb02_conversas cv ON cv.id_conversa = d.id_conversa "
               "JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = cv.id_extracao "
               "LEFT JOIN investigacao.msg_tb01_contatos ct "
               "  ON ct.id_contato = d.id_contato_remetente",
        onde=_ONDE_EXTRACAO,
        id_col="d.id_mensagem",
        titulo="concat_ws(' · ', cv.tp_app, coalesce(ct.no_contato, ct.nu_identificador, "
               "nullif(cv.no_grupo, '')))",
        detalhe="case when d.in_enviada then 'enviada' else 'recebida' end",
        local=_EXT_LOCAL, data="d.dt_mensagem",
        textos=("d.tx_mensagem",), pesada=True,
        link="case when strpos(lower(cv.tp_app), 'whatsapp') > 0 "  # regra de mapa_telas.js: qualquer app com whatsapp no nome é WhatsApp
             "then 'whatsapp.html' else 'mensageiros.html' end "
             "|| '?inquerito=' || e.id_inquerito || '&fonte=msg' "
             "|| '&app=' || replace(cv.tp_app, ' ', '%%20') || '&msg=' || d.id_mensagem",
    ),
    Fonte(
        chave="ligacoes", rotulo="Celular · ligações", grupo="celular",
        origem=f"investigacao.lig_tb01_ligacoes d {_EXTRACAO}", onde=_ONDE_EXTRACAO,
        id_col="d.id_ligacao",
        titulo="coalesce(nullif(d.no_contato, ''), d.nu_telefone_contato)",
        detalhe="concat_ws(' · ', d.tp_direcao, d.nu_telefone_contato, "
                "d.qt_duracao_segundos || 's', nullif(d.no_aplicativo, ''))",
        local=_EXT_LOCAL, data="d.dt_ligacao",
        nomes=("d.no_contato",), fones=("d.nu_telefone_contato",), pesada=True,
        link="'ligacoes.html?inquerito=' || e.id_inquerito || '&extracao=' || e.id_extracao "
             "|| '&destacar=' || d.id_ligacao",
    ),
    Fonte(
        chave="voicemail", rotulo="Celular · caixa postal", grupo="celular",
        origem=f"investigacao.vms_tb01_voicemail d {_EXTRACAO}", onde=_ONDE_EXTRACAO,
        id_col="d.id_voicemail", titulo="coalesce(nullif(d.no_contato, ''), d.nu_telefone)",
        detalhe="concat_ws(' · ', d.nu_telefone, d.qt_duracao_segundos || 's')",
        local=_EXT_LOCAL, data="d.dt_recebimento",
        nomes=("d.no_contato",), fones=("d.nu_telefone",), textos=("d.tx_transcricao",),
    ),
    Fonte(
        chave="emails", rotulo="Celular · e-mails", grupo="celular",
        origem=f"investigacao.eml_tb01_emails d {_EXTRACAO}", onde=_ONDE_EXTRACAO,
        id_col="d.id_email", titulo="coalesce(nullif(d.tx_assunto, ''), '(sem assunto)')",
        detalhe="concat_ws(' · ', d.no_remetente, left(d.tx_destinatarios, 120))",
        local=_EXT_LOCAL, data="d.dt_email",
        textos=("d.no_remetente", "d.tx_destinatarios", "d.tx_assunto", "d.tx_corpo"),
        pesada=True,
        link="'emails.html?inquerito=' || e.id_inquerito || '&extracao=' || e.id_extracao "
             "|| '&destacar=' || d.id_email",
    ),
    Fonte(
        chave="notas", rotulo="Celular · notas", grupo="celular",
        origem=f"investigacao.not_tb01_notas d {_EXTRACAO}", onde=_ONDE_EXTRACAO,
        id_col="d.id_nota", titulo="coalesce(nullif(d.tx_titulo, ''), '(nota sem título)')",
        detalhe="concat_ws(' · ', nullif(d.no_aplicativo, ''), nullif(d.no_conta, ''))",
        local=_EXT_LOCAL, data="d.dt_modificacao",
        textos=("d.tx_titulo", "d.tx_corpo"),
        link="'notas.html?inquerito=' || e.id_inquerito || '&extracao=' || e.id_extracao "
             "|| '&destacar=' || d.id_nota",
    ),
    Fonte(
        chave="notificacoes", rotulo="Celular · notificações", grupo="celular",
        origem=f"investigacao.ntf_tb01_notificacoes d {_EXTRACAO}", onde=_ONDE_EXTRACAO,
        id_col="d.id_notificacao", titulo="coalesce(nullif(d.no_assunto, ''), d.no_pacote_app)",
        detalhe="concat_ws(' · ', d.no_pacote_app, left(d.tx_conteudo, 160))",
        local=_EXT_LOCAL, data="d.dt_notificacao",
        textos=("d.tx_conteudo", "d.no_assunto"), pesada=True,
        link="'notificacoes.html?inquerito=' || e.id_inquerito || '&extracao=' || e.id_extracao "
             "|| '&destacar=' || d.id_notificacao",
    ),
    Fonte(
        chave="calendario", rotulo="Celular · compromissos", grupo="celular",
        origem=f"investigacao.cal_tb01_calendario d {_EXTRACAO}", onde=_ONDE_EXTRACAO,
        id_col="d.id_compromisso", titulo="d.tx_assunto",
        detalhe="concat_ws(' · ', nullif(d.tx_local, ''), nullif(d.no_organizador, ''), "
                "left(d.tx_convidados, 120))",
        local=_EXT_LOCAL, data="d.dt_inicio",
        textos=("d.tx_assunto", "d.tx_detalhes", "d.tx_local", "d.tx_convidados",
                "d.no_organizador"),
        link="'calendario.html?inquerito=' || e.id_inquerito || '&extracao=' || e.id_extracao "
             "|| '&destacar=' || d.id_compromisso",
    ),
    Fonte(
        chave="pix", rotulo="Celular · chaves PIX citadas", grupo="celular",
        origem=f"investigacao.pix_tb01_chaves d {_EXTRACAO}", onde=_ONDE_EXTRACAO,
        id_col="d.id_chave_pix", titulo="concat_ws(' · ', d.tp_chave, d.co_chave)",
        detalhe="concat_ws(' · ', nullif(d.no_remetente, ''), nullif(d.no_conversa, ''), "
                "left(d.tx_trecho, 160))",
        local=_EXT_LOCAL, data="d.dt_mensagem",
        nomes=("d.no_remetente",), docs=("d.co_chave",), fones=("d.co_chave",),
        textos=("d.co_chave", "d.tx_chave_original", "d.tx_trecho"),
        link="'chaves_pix.html?inquerito=' || e.id_inquerito || '&extracao=' || e.id_extracao "
             "|| '&destacar=' || d.id_chave_pix",
    ),
    Fonte(
        chave="contas_financeiras", rotulo="Celular · contas financeiras", grupo="celular",
        origem=f"investigacao.bnc_tb01_contas_financeiras d {_EXTRACAO}", onde=_ONDE_EXTRACAO,
        id_col="d.id_conta_financeira",
        titulo="coalesce(nullif(d.no_titular, ''), d.no_instituicao)",
        detalhe="concat_ws(' · ', d.no_instituicao, "
                "nullif(concat_ws('/', d.nu_agencia, d.nu_conta), ''), d.tp_conta)",
        local=_EXT_LOCAL,
        nomes=("d.no_titular",), textos=("d.nu_conta", "d.no_instituicao"),
        link="'contas_financeiras.html?inquerito=' || e.id_inquerito || '&extracao=' || e.id_extracao "
             "|| '&destacar=' || d.id_conta_financeira",
    ),
    Fonte(
        chave="contas_usuario", rotulo="Celular · contas de aplicativo", grupo="celular",
        origem=f"investigacao.cta_tb01_contas_usuario d {_EXTRACAO}", onde=_ONDE_EXTRACAO,
        id_col="d.id_conta_usuario", titulo="coalesce(nullif(d.no_usuario, ''), d.no_servico)",
        detalhe="concat_ws(' · ', d.no_servico, nullif(d.no_titular, ''))",
        local=_EXT_LOCAL, data="d.dt_criacao",
        nomes=("d.no_titular",), textos=("d.no_usuario",),
    ),
    Fonte(
        chave="cartoes", rotulo="Celular · cartões", grupo="celular",
        origem=f"investigacao.crt_tb01_cartoes_credito d {_EXTRACAO}", onde=_ONDE_EXTRACAO,
        id_col="d.id_cartao", titulo="coalesce(nullif(d.no_titular, ''), d.no_bandeira)",
        detalhe="concat_ws(' · ', d.no_bandeira, d.nu_cartao)",
        local=_EXT_LOCAL,
        nomes=("d.no_titular",),
    ),
    Fonte(
        chave="senhas", rotulo="Celular · credenciais salvas", grupo="celular",
        origem=f"investigacao.snh_tb01_senhas d {_EXTRACAO}", onde=_ONDE_EXTRACAO,
        id_col="d.id_senha", titulo="coalesce(nullif(d.no_usuario, ''), d.no_servico)",
        detalhe="concat_ws(' · ', d.no_servico, d.tp_senha)",
        local=_EXT_LOCAL,
        textos=("d.no_usuario", "d.tx_dados", "d.no_servico"),
        link="'credenciais.html?inquerito=' || e.id_inquerito || '&extracao=' || e.id_extracao "
             "|| '&destacar=' || d.id_senha",
    ),
    Fonte(
        chave="autofill", rotulo="Celular · preenchimento automático", grupo="celular",
        origem=f"investigacao.afl_tb01_autofill d {_EXTRACAO}", onde=_ONDE_EXTRACAO,
        id_col="d.id_autofill", titulo="concat_ws(': ', d.no_chave, left(d.tx_valor, 120))",
        detalhe="d.no_fonte", local=_EXT_LOCAL, data="d.dt_ultimo_uso",
        docs=("d.tx_valor",), fones=("d.tx_valor",), textos=("d.tx_valor", "d.no_chave"),
    ),
    Fonte(
        chave="donos", rotulo="Celular · donos do aparelho", grupo="celular",
        origem=f"investigacao.don_tb01_donos d {_EXTRACAO}", onde=_ONDE_EXTRACAO,
        id_col="d.id_dono", titulo="coalesce(nullif(d.no_dono, ''), d.nu_identificador)",
        detalhe="concat_ws(' · ', d.tp_usuario, d.nu_identificador)",
        local=_EXT_LOCAL, data="d.dt_ultimo_login",
        nomes=("d.no_dono",), fones=("d.nu_identificador",), textos=("d.nu_identificador",),
    ),
    Fonte(
        chave="buscas", rotulo="Celular · buscas na internet", grupo="celular",
        origem=f"investigacao.bsc_tb01_buscas d {_EXTRACAO}", onde=_ONDE_EXTRACAO,
        id_col="d.id_busca", titulo="d.tx_termo",
        detalhe="concat_ws(' · ', nullif(d.no_aplicativo, ''), nullif(d.no_origem, ''))",
        local=_EXT_LOCAL, data="d.dt_busca",
        textos=("d.tx_termo", "d.tx_resultados"),
        link="'social_buscas.html?inquerito=' || e.id_inquerito || '&extracao=' || e.id_extracao "
             "|| '&destacar=' || d.id_busca",
    ),
    Fonte(
        chave="navegacao", rotulo="Celular · páginas visitadas", grupo="celular",
        origem=f"investigacao.nav_tb01_paginas_visitadas d {_EXTRACAO}", onde=_ONDE_EXTRACAO,
        id_col="d.id_pagina", titulo="coalesce(nullif(d.tx_titulo, ''), d.tx_url)",
        detalhe="left(d.tx_url, 200)",         local=_EXT_LOCAL, data="d.dt_visita",
        textos=("d.tx_titulo", "d.tx_url"), pesada=True,
        link="'navegacao.html?inquerito=' || e.id_inquerito || '&extracao=' || e.id_extracao "
             "|| '&destacar=' || d.id_pagina",
    ),
    Fonte(
        chave="atividade_social", rotulo="Celular · redes sociais", grupo="celular",
        origem=f"investigacao.rds_tb01_atividade_social d {_EXTRACAO}", onde=_ONDE_EXTRACAO,
        id_col="d.id_atividade",
        titulo="coalesce(nullif(d.tx_titulo, ''), left(d.tx_corpo, 120))",
        detalhe="concat_ws(' · ', d.no_plataforma, d.tp_atividade, nullif(d.no_autor, ''))",
        local=_EXT_LOCAL, data="d.dt_atividade",
        nomes=("d.no_autor",),
        textos=("d.tx_titulo", "d.tx_corpo", "d.tx_marcados", "d.no_autor"), pesada=True,
        link="'social_buscas.html?inquerito=' || e.id_inquerito || '&extracao=' || e.id_extracao "
             "|| '&destacar=' || d.id_atividade",
    ),
    Fonte(
        chave="transferencias", rotulo="Celular · transferências em app", grupo="celular",
        origem=f"investigacao.trf_tb01_transferencias d {_EXTRACAO}", onde=_ONDE_EXTRACAO,
        id_col="d.id_transferencia",
        titulo="concat_ws(' · ', d.no_aplicativo, 'R$ ' || d.vl_valor)",
        detalhe="concat_ws(' · ', left(d.tx_descricao, 160), left(d.tx_participantes, 120))",
        local=_EXT_LOCAL, data="d.dt_envio",
        textos=("d.tx_descricao", "d.tx_participantes"),
    ),
    Fonte(
        chave="arquivos", rotulo="Celular · arquivos (nome, OCR e transcrição)", grupo="celular",
        origem=f"investigacao.arq_tb01_arquivos d {_EXTRACAO}", onde=_ONDE_EXTRACAO,
        id_col="d.id_arquivo", titulo="d.no_arquivo",
        detalhe="concat_ws(' · ', d.no_categoria, d.tp_mime, left(d.tx_caminho, 160))",
        local=_EXT_LOCAL, data="d.dt_modificacao",
        hashes=("d.co_hash_md5", "d.co_hash_sha1", "d.co_hash_sha256"),
        # tx_identificadores é jsonb (a lista que o parser extraiu do arquivo) — sem o cast o
        # Postgres não tem operador ILIKE para ela.
        textos=("d.no_arquivo", "d.tx_caminho", "d.tx_ocr", "d.tx_transcricao",
                "d.tx_identificadores::text"),
        pesada=True,
        dica="inclui o texto reconhecido por OCR nas imagens, a transcrição de áudio/vídeo e o "
             "hash MD5/SHA do arquivo",
        link="'arquivos.html?inquerito=' || e.id_inquerito || '&extracao=' || e.id_extracao "
             "|| '&destacar=' || d.id_arquivo",
    ),
    Fonte(
        chave="info_aparelho", rotulo="Celular · informações do aparelho", grupo="celular",
        origem=f"investigacao.dev_tb01_info_aparelho d {_EXTRACAO}", onde=_ONDE_EXTRACAO,
        id_col="d.id_info", titulo="concat_ws(': ', d.no_campo, left(d.tx_valor, 120))",
        detalhe="concat_ws(' · ', nullif(d.no_categoria, ''), nullif(d.no_aplicativo, ''))",
        local=_EXT_LOCAL,
        textos=("d.tx_valor", "d.no_campo"),
        link="'identidade_aparelho.html?inquerito=' || e.id_inquerito || '&extracao=' || e.id_extracao "
             "|| '&destacar=' || d.id_info",
    ),
    Fonte(
        chave="dispositivos_reconhecidos", rotulo="Celular · dispositivos reconhecidos",
        grupo="celular",
        origem=f"investigacao.rcd_tb01_dispositivos d {_EXTRACAO}", onde=_ONDE_EXTRACAO,
        id_col="d.id_dispositivo", titulo="coalesce(nullif(d.no_dispositivo, ''), d.no_mac)",
        detalhe="concat_ws(' · ', d.tp_dispositivo, d.no_modelo, d.no_mac)",
        local=_EXT_LOCAL,
        textos=("d.no_dispositivo", "d.tx_identificadores", "d.nu_serie"),
        link="'dispositivos_reconhecidos.html?inquerito=' || e.id_inquerito "
             "|| '&extracao=' || e.id_extracao || '&destacar=' || d.id_dispositivo",
    ),
    # ---------- Contas na nuvem ----------
    Fonte(
        chave="nuvem_perfil", rotulo="Nuvem · perfil da conta", grupo="nuvem",
        origem=f"investigacao.prf_tb01_perfil d {_EXTRACAO}", onde=_ONDE_EXTRACAO,
        id_col="d.id_perfil", titulo="coalesce(nullif(d.no_nome, ''), d.tx_email)",
        detalhe="concat_ws(' · ', d.tx_email, nullif(d.tx_email_alternativos, ''), d.no_fonte)",
        local=_EXT_LOCAL,
        nomes=("d.no_nome",), textos=("d.tx_email", "d.tx_email_alternativos", "d.tx_servicos"),
        link="'perfil_conta.html?inquerito=' || e.id_inquerito || '&extracao=' || e.id_extracao "
             "|| '&destacar=' || d.id_perfil",
    ),
    Fonte(
        chave="nuvem_eventos", rotulo="Nuvem · agenda", grupo="nuvem",
        origem=f"investigacao.evt_tb01_eventos d {_EXTRACAO}", onde=_ONDE_EXTRACAO,
        id_col="d.id_evento", titulo="d.tx_titulo",
        detalhe="concat_ws(' · ', nullif(d.tx_local, ''), nullif(d.tx_organizador, ''), "
                "left(d.tx_participantes, 120))",
        local=_EXT_LOCAL, data="d.dt_inicio",
        textos=("d.tx_titulo", "d.tx_descricao", "d.tx_local", "d.tx_organizador",
                "d.tx_participantes"),
        link="'agenda.html?inquerito=' || e.id_inquerito || '&extracao=' || e.id_extracao "
             "|| '&destacar=' || d.id_evento",
    ),
    Fonte(
        chave="nuvem_atividades", rotulo="Nuvem · atividades", grupo="nuvem",
        origem=f"investigacao.atv_tb01_atividades d {_EXTRACAO}", onde=_ONDE_EXTRACAO,
        id_col="d.id_atividade", titulo="coalesce(nullif(d.tx_produto, ''), d.no_fonte)",
        detalhe="left(d.tx_descricao, 200)",         local=_EXT_LOCAL, data="d.dt_atividade",
        textos=("d.tx_descricao", "d.tx_url"), pesada=True,
        link="'atividades.html?inquerito=' || e.id_inquerito || '&extracao=' || e.id_extracao "
             "|| '&destacar=' || d.id_atividade",
    ),
    Fonte(
        chave="nuvem_dispositivos", rotulo="Nuvem · dispositivos da conta", grupo="nuvem",
        origem=f"investigacao.dvc_tb01_dispositivos d {_EXTRACAO}", onde=_ONDE_EXTRACAO,
        id_col="d.id_dispositivo", titulo="d.no_dispositivo",
        detalhe="concat_ws(' · ', nullif(d.tx_fabricante, ''), nullif(d.tx_imei, ''), "
                "nullif(d.tx_serial, ''))",
        local=_EXT_LOCAL,
        textos=("d.no_dispositivo", "d.tx_imei", "d.tx_serial", "d.tx_outros"),
        link="'dispositivos_conta.html?inquerito=' || e.id_inquerito || '&extracao=' || e.id_extracao "
             "|| '&destacar=' || d.id_dispositivo",
    ),
    Fonte(
        chave="nuvem_arquivos", rotulo="Nuvem · arquivos", grupo="nuvem",
        origem=f"investigacao.nuv_tb01_metadados_arquivo d {_EXTRACAO}", onde=_ONDE_EXTRACAO,
        id_col="d.id_metadado",
        titulo="coalesce(nullif(d.tx_titulo, ''), d.no_arquivo_alvo)",
        detalhe="concat_ws(' · ', nullif(d.no_proprietario, ''), "
                "left(d.tx_compartilhado_com, 120))",
        local=_EXT_LOCAL, data="d.dt_modificacao",
        nomes=("d.no_proprietario",),
        textos=("d.no_arquivo_alvo", "d.tx_titulo", "d.tx_compartilhado_com"), pesada=True,
    ),
    Fonte(
        chave="nuvem_acessos", rotulo="Nuvem · identificadores de acesso", grupo="nuvem",
        origem=f"investigacao.acs_tb02_agregados d {_EXTRACAO}", onde=_ONDE_EXTRACAO,
        id_col="d.id_acesso_agr", titulo="concat_ws(': ', d.tp_identificador, d.tx_identificador)",
        detalhe="concat_ws(' · ', d.no_fonte, d.qt_dias_ativos || ' dia(s) ativo(s)')",
        local=_EXT_LOCAL, data="d.dt_ultima",
        fones=("d.tx_identificador",), textos=("d.tx_identificador",),
        link="'acessos_agregados.html?inquerito=' || e.id_inquerito || '&extracao=' || e.id_extracao "
             "|| '&destacar=' || d.id_acesso_agr",
    ),
    # ---------- Análise do caso ----------
    Fonte(
        chave="canonicas", rotulo="Mesa · entidades do caso", grupo="analise",
        origem="investigacao.ent_tb05_canonicas d",
        onde="d.id_inquerito = %(inq)s",
        id_col="d.id_canonica", titulo="d.no_entidade",
        detalhe="concat_ws(' · ', d.tp_entidade, nullif(d.nu_chave, ''), "
                "left(d.tx_observacao, 160))",
        local="'mesa de análise'", data="d.dt_criacao",
        nomes=("d.no_entidade",), textos=("d.nu_chave", "d.tx_observacao"),
        link="'mesa_inquerito.html?inquerito=' || d.id_inquerito",
    ),
    Fonte(
        chave="identificadores", rotulo="Mesa · identificadores das entidades", grupo="analise",
        origem="investigacao.ent_tb08_canonica_identificadores d "
               "JOIN investigacao.ent_tb05_canonicas c ON c.id_canonica = d.id_canonica",
        onde="d.id_inquerito = %(inq)s",
        id_col="d.id_identificador",
        titulo="concat_ws(': ', d.tp_identificador, d.co_valor)",
        detalhe="concat_ws(' · ', c.no_entidade, nullif(d.tx_origem, ''))",
        local="'mesa de análise'",
        docs=("d.co_valor",), fones=("d.co_valor",), textos=("d.co_valor",),
        link="'mesa_inquerito.html?inquerito=' || d.id_inquerito",
    ),
    Fonte(
        chave="hipoteses", rotulo="Hipóteses do inquérito", grupo="analise",
        origem="investigacao.hip_tb01_hipoteses d",
        onde="d.id_inquerito = %(inq)s",
        id_col="d.id_hipotese", titulo="left(d.tx_hipotese, 160)",
        detalhe="concat_ws(' · ', d.cd_status, nullif(d.tp_natureza, ''))",
        local="'workspace de hipóteses'", data="d.dt_criacao",
        textos=("d.tx_hipotese",),
        link="'hipoteses.html?inquerito=' || d.id_inquerito",
    ),
    Fonte(
        chave="evidencias_hipotese", rotulo="Evidências de hipóteses", grupo="analise",
        origem="investigacao.hip_tb02_evidencias d "
               "JOIN investigacao.hip_tb01_hipoteses h ON h.id_hipotese = d.id_hipotese",
        onde="h.id_inquerito = %(inq)s",
        id_col="d.id_evidencia", titulo="left(d.tx_evidencia, 160)",
        detalhe="concat_ws(' · ', d.tp_lado, left(h.tx_hipotese, 80))",
        local="'workspace de hipóteses'", data="d.dt_criacao",
        textos=("d.tx_evidencia",),
        link="'hipoteses.html?inquerito=' || d.id_inquerito || '#hipotese=' || d.id_hipotese",
    ),
    Fonte(
        chave="entidades_mesa", rotulo="Mesa · comentários das entidades", grupo="analise",
        origem="investigacao.ent_tb01_entidades d",
        onde="d.id_inquerito = %(inq)s",
        id_col="d.id_entidade", titulo="left(d.tx_comentario, 160)",
        detalhe="concat_ws(' · ', d.tp_origem, nullif(d.cd_revisao, ''))",
        local="'mesa de análise'", data="d.dt_criacao",
        textos=("d.tx_comentario", "d.tx_justificativa"),
        link="'mesa_inquerito.html?inquerito=' || d.id_inquerito",
    ),
)

FONTES_POR_CHAVE = {f.chave: f for f in FONTES}


# ---------------------------------------------------------------------------------------------
# Motor
# ---------------------------------------------------------------------------------------------

# `translate` no lugar de unaccent(): a extensão não está instalada no banco (ver
# db/init/01_schemas.sql, que cria só pg_trgm, fuzzystrmatch e pgcrypto) e adicioná-la exigiria
# superusuário em toda instalação já existente.
_SQL_ACENTOS = _ACENTOS + _ACENTOS.upper()
_SQL_SEM_ACENTO = _SEM_ACENTO + _SEM_ACENTO.upper()


def _sql_norm(coluna: str) -> str:
    """A mesma normalização de `normalizar()`, do lado do banco."""
    return (f"btrim(regexp_replace(lower(translate(coalesce({coluna}, ''), "
            f"'{_SQL_ACENTOS}', '{_SQL_SEM_ACENTO}')), '[^a-z0-9]+', ' ', 'g'))")


def _sql_digitos(coluna: str) -> str:
    return f"regexp_replace(coalesce({coluna}, ''), '[^0-9]', '', 'g')"


def _ou(predicados: list[str]) -> str:
    return "(" + " OR ".join(predicados) + ")" if predicados else "false"


def _predicados(fonte: Fonte, aproximada: bool) -> dict[str, str]:
    """As cinco famílias de predicado da fonte, cada uma já reduzida a uma expressão booleana."""
    doc = [f"({_sql_digitos(c)} <> '' AND {_sql_digitos(c)} = ANY(a.docs))" for c in fonte.docs]
    fone = [f"(length({_sql_digitos(c)}) >= 8 AND {_sql_digitos(c)} LIKE ANY(a.fones))"
            for c in fonte.fones]
    # CHAR(32/40/64) vem com espaços de padding do banco e hash pode estar gravado em maiúsculo:
    # dos dois lados o que compara é o hex minúsculo despadronizado.
    hash_ = [f"(lower(btrim(coalesce({c}, ''))) = ANY(a.hashes))" for c in fonte.hashes]
    nome = [f"(a.nome_norm <> '' AND {_sql_norm(c)} = a.nome_norm)" for c in fonte.nomes]

    aprox: list[str] = []
    if aproximada:
        for c in fonte.nomes:
            norm = _sql_norm(c)
            aprox.append(
                f"(a.nome_norm <> '' AND {norm} <> '' AND ("
                f"{norm} LIKE '%%' || a.nome_norm || '%%'"
                f" OR similarity({norm}, a.nome_norm) >= %(limiar)s"
                f" OR (cardinality(a.tokens) > 0 AND NOT EXISTS ("
                f"SELECT 1 FROM unnest(a.tokens) tk WHERE {norm} NOT LIKE '%%' || tk || '%%'))"
                f"))"
            )

    texto = [f"(cardinality(a.textos_like) > 0 AND {c} ILIKE ANY(a.textos_like))"
             for c in fonte.textos]
    if aproximada and not fonte.pesada:
        texto += [f"(a.textos_regex <> '' AND {c} ~* a.textos_regex)" for c in fonte.textos]

    return {
        "documento": _ou(doc), "telefone": _ou(fone), "nome": _ou(nome),
        "nome_aproximado": _ou(aprox), "texto": _ou(texto), "hash": _ou(hash_),
    }


def _sql_fonte(fonte: Fonte, alvos: list[Alvo], aproximada: bool) -> str:
    preds = _predicados(fonte, aproximada)
    condicao = _ou([p for p in preds.values() if p != "false"])

    similaridade = "0::real"
    if fonte.nomes:
        partes = ", ".join(f"similarity({_sql_norm(c)}, a.nome_norm)" for c in fonte.nomes)
        similaridade = f"greatest({partes})::real"

    # Cada coluna de texto declarada volta cortada em 4000 caracteres: é o bastante para montar o
    # trecho com destaque sem trazer um PDF inteiro por linha.
    textos_sel = "".join(f", substr(coalesce({c}, ''), 1, 4000) AS txt_{i}"
                         for i, c in enumerate(fonte.textos))

    ordem = f"{fonte.data} DESC NULLS LAST, {fonte.id_col}" if fonte.data else fonte.id_col
    valores = ", ".join(
        f"(%(a{i}_idx)s::int, %(a{i}_nome)s::text, %(a{i}_tokens)s::text[], "
        f"%(a{i}_docs)s::text[], %(a{i}_fones)s::text[], %(a{i}_hashes)s::text[], "
        f"%(a{i}_like)s::text[], %(a{i}_regex)s::text)"
        for i in range(len(alvos))
    )

    return f"""
        WITH alvos (idx, nome_norm, tokens, docs, fones, hashes, textos_like, textos_regex) AS (
            VALUES {valores}
        )
        SELECT * FROM (
            SELECT a.idx AS idx,
                   {fonte.id_col} AS id_registro,
                   {fonte.titulo} AS titulo,
                   {fonte.detalhe} AS detalhe,
                   {fonte.local} AS fonte_local,
                   {fonte.data or 'NULL::timestamp'} AS data,
                   {fonte.link} AS link,
                   {preds['hash']} AS bateu_hash,
                   {preds['documento']} AS bateu_documento,
                   {preds['telefone']} AS bateu_telefone,
                   {preds['nome']} AS bateu_nome,
                   {preds['nome_aproximado']} AS bateu_nome_aproximado,
                   {preds['texto']} AS bateu_texto,
                   {similaridade} AS similaridade,
                   row_number() OVER (PARTITION BY a.idx ORDER BY {ordem}) AS rn,
                   count(*) OVER (PARTITION BY a.idx) AS total{textos_sel}
            FROM alvos a, {fonte.origem}
            WHERE {fonte.onde} AND {condicao}
        ) t
        WHERE t.rn <= %(limite)s
        ORDER BY t.idx, t.rn
    """


def _parametros(alvos: list[Alvo], id_inquerito: int, limite: int) -> dict:
    params: dict = {"inq": id_inquerito, "limite": limite, "limiar": LIMIAR_SIMILARIDADE}
    for i, alvo in enumerate(alvos):
        params[f"a{i}_idx"] = alvo.indice
        params[f"a{i}_nome"] = alvo.nome_norm
        params[f"a{i}_tokens"] = alvo.tokens
        params[f"a{i}_docs"] = alvo.docs
        params[f"a{i}_fones"] = alvo.fones
        params[f"a{i}_hashes"] = alvo.hashes
        params[f"a{i}_like"] = alvo.textos_like
        params[f"a{i}_regex"] = alvo.textos_regex
    return params


def _normalizar_posicional(texto: str) -> str:
    """Normaliza **sem mudar o comprimento**: cada caractere continua na mesma posição.

    `normalizar()` colapsa a pontuação num espaço só, o que é certo para comparar dois nomes e
    errado para localizar a menção — a posição achada no texto normalizado tem de valer como
    posição no texto original, senão o recorte sai deslocado."""
    return re.sub(r"[^a-z0-9]", " ", texto.translate(_TABELA_ACENTOS).lower())


def _achar_mencao(original: str, alvo: Alvo) -> tuple[int, int] | None:
    """Onde a menção começa e termina no texto original (nome, CPF/CNPJ ou telefone)."""
    norm = _normalizar_posicional(original)

    tokens = alvo.nome_norm.split()
    if len("".join(tokens)) >= 4:
        # Os espaços entre as palavras do nome viram "qualquer separador": é o que encontra o
        # nome partido pela quebra de linha do PDF.
        padrao = re.compile(r"[^a-z0-9]+".join(re.escape(t) for t in tokens))
        achou = padrao.search(norm)
        if achou:
            return achou.start(), achou.end()

    for digitos in alvo.docs + [f.lstrip("%") for f in alvo.fones]:
        posicoes = [i for i, c in enumerate(original) if c.isdigit()]
        somente = "".join(original[i] for i in posicoes)
        inicio = somente.find(digitos)
        if inicio >= 0:
            return posicoes[inicio], posicoes[inicio + len(digitos) - 1] + 1

    for h in alvo.hashes:
        inicio = original.lower().find(h)
        if inicio >= 0:
            return inicio, inicio + len(h)
    return None


def _trecho(linha: dict, fonte: Fonte, alvo: Alvo) -> dict | None:
    """O pedaço do texto em volta da menção, para o achado poder ser lido sem abrir o documento."""
    for indice in range(len(fonte.textos)):
        original = linha.get(f"txt_{indice}") or ""
        if not original:
            continue
        posicao = _achar_mencao(original, alvo)
        if not posicao:
            continue
        pos, fim_marca = posicao
        inicio = max(0, pos - 120)
        fim = min(len(original), fim_marca + 160)
        return {
            "texto": ("…" if inicio else "") + original[inicio:fim].strip()
                     + ("…" if fim < len(original) else ""),
            "marca": original[pos:fim_marca],
            "coluna": fonte.textos[indice].split("::")[0].split(".")[-1],
        }
    return None


def _consultar_fonte(fonte: Fonte, alvos: list[Alvo], id_inquerito: int,
                     aproximada: bool, limite: int) -> list[dict]:
    """Roda a consulta de uma fonte e devolve as linhas já no formato da tela."""
    sql = _sql_fonte(fonte, alvos, aproximada)
    params = _parametros(alvos, id_inquerito, limite)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            colunas = [d.name for d in cur.description]
            brutas = [dict(zip(colunas, linha)) for linha in cur.fetchall()]

    achados = []
    for linha in brutas:
        alvo = alvos[linha["idx"]]
        criterios = [nome for nome, chave in (
            ("hash", "bateu_hash"), ("documento", "bateu_documento"),
            ("telefone", "bateu_telefone"),
            ("nome", "bateu_nome"), ("nome_aproximado", "bateu_nome_aproximado"),
            ("texto", "bateu_texto")) if linha[chave]]
        achados.append({
            "idx": linha["idx"],
            "fonte": fonte.chave,
            "rotulo": fonte.rotulo,
            "grupo": fonte.grupo,
            "id_registro": linha["id_registro"],
            "titulo": linha["titulo"],
            "detalhe": linha["detalhe"],
            "local": linha["fonte_local"],
            "data": linha["data"],
            "link": linha["link"],
            "criterios": criterios,
            "similaridade": round(float(linha["similaridade"] or 0), 3),
            "trecho": _trecho(linha, fonte, alvo) if "texto" in criterios else None,
            "total_fonte": linha["total"],
        })
    return achados


def fontes_disponiveis(id_inquerito: int) -> dict:
    """Que tipos de material o inquérito tem — é o que decide quais fontes valem a consulta.

    Uma contagem barata por grupo (exists), não por fonte: o que a tela precisa saber é se
    oferece a caixa "RIF" ou "Celular", e o que o motor precisa é não varrer tabela vazia."""
    consultas = {
        "rif": "SELECT EXISTS (SELECT 1 FROM investigacao.rif_tb01_rifs WHERE id_inquerito = %s)",
        "simba": "SELECT EXISTS (SELECT 1 FROM investigacao.smb_tb01_extratos "
                 "WHERE id_inquerito = %s)",
        "ipl": "SELECT EXISTS (SELECT 1 FROM investigacao.ipl_tb01_inqueritos_pdf "
               "WHERE id_inquerito = %s)",
        "irpf": "SELECT EXISTS (SELECT 1 FROM investigacao.irp_tb01_declaracoes "
                "WHERE id_inquerito = %s)",
        "ipei": "SELECT EXISTS (SELECT 1 FROM investigacao.ipe_tb01_ipeis WHERE id_inquerito = %s)",
        "telefonia": "SELECT EXISTS (SELECT 1 FROM investigacao.tel_tb01_telefonia "
                     "WHERE id_inquerito = %s)",
        "documentos": "SELECT EXISTS (SELECT 1 FROM investigacao.inq_tb04_documentos "
                      "WHERE id_inquerito = %s)",
        "celular": "SELECT EXISTS (SELECT 1 FROM investigacao.inq_tb02_extracoes "
                   "WHERE id_inquerito = %s)",
        "nuvem": "SELECT EXISTS (SELECT 1 FROM investigacao.inq_tb02_extracoes "
                 "WHERE id_inquerito = %s)",
        "analise": "SELECT EXISTS (SELECT 1 FROM investigacao.ent_tb05_canonicas "
                   "WHERE id_inquerito = %s) OR EXISTS (SELECT 1 FROM "
                   "investigacao.hip_tb01_hipoteses WHERE id_inquerito = %s)",
    }
    disponiveis: dict[str, bool] = {}
    with get_connection() as conn:
        with conn.cursor() as cur:
            for grupo, sql in consultas.items():
                cur.execute(sql, (id_inquerito,) * sql.count("%s"))
                disponiveis[grupo] = bool(cur.fetchone()[0])

    return {
        "grupos": [
            {
                "chave": chave,
                "rotulo": rotulo,
                "disponivel": disponiveis.get(chave, False),
                "fontes": [{"chave": f.chave, "rotulo": f.rotulo, "dica": f.dica}
                           for f in FONTES if f.grupo == chave],
            }
            for chave, rotulo in GRUPOS.items()
        ]
    }


def buscar_entidades(id_inquerito: int, entidades: list[dict], *, aproximada: bool = True,
                     grupos: list[str] | None = None, limite: int = LIMITE_POR_FONTE) -> dict:
    """Procura cada entidade em todas as fontes disponíveis do inquérito.

    `grupos` restringe a busca (nulo/vazio = tudo que o inquérito tem); `aproximada` liga o
    casamento por semelhança de nome e a varredura tolerante do texto livre."""
    limite = max(1, min(int(limite or LIMITE_POR_FONTE), LIMITE_MAX_POR_FONTE))

    alvos = []
    for entidade in entidades:
        alvo = preparar_alvo(len(alvos), entidade.get("nome"), entidade.get("cpf_cnpj"),
                             entidade.get("telefone"), aproximada, entidade.get("hash"))
        if not alvo.vazio:
            alvos.append(alvo)
    if not alvos:
        return {"resultados": [], "fontes_consultadas": [], "aproximada": aproximada}

    disponiveis = {g["chave"] for g in fontes_disponiveis(id_inquerito)["grupos"] if g["disponivel"]}
    if grupos:
        disponiveis &= set(grupos)

    selecionadas = [f for f in FONTES if f.grupo in disponiveis]

    achados: list[dict] = []
    erros: list[str] = []

    def executar(fonte: Fonte) -> list[dict]:
        try:
            return _consultar_fonte(fonte, alvos, id_inquerito, aproximada, limite)
        except Exception:
            # Uma fonte que falha (tabela de uma migração que ainda não rodou, timeout de uma
            # varredura pesada) não pode derrubar a busca inteira — o investigador prefere o
            # resultado parcial com o aviso a uma tela de erro.
            logger.exception("busca de entidades: falha na fonte %s", fonte.chave)
            erros.append(fonte.rotulo)
            return []

    if selecionadas:
        with ThreadPoolExecutor(max_workers=FONTES_EM_PARALELO) as executor:
            for linhas in executor.map(executar, selecionadas):
                achados.extend(linhas)

    resultados = []
    for alvo in alvos:
        meus = [a for a in achados if a["idx"] == alvo.indice]
        meus.sort(key=lambda a: (a["grupo"], a["fonte"], -a["similaridade"]))
        por_fonte: dict[str, dict] = {}
        for achado in meus:
            bloco = por_fonte.setdefault(achado["fonte"], {
                "fonte": achado["fonte"], "rotulo": achado["rotulo"], "grupo": achado["grupo"],
                "total": achado["total_fonte"], "achados": [],
            })
            bloco["achados"].append({k: v for k, v in achado.items()
                                     if k not in ("idx", "total_fonte")})
        resultados.append({
            "nome": alvo.nome,
            "cpf_cnpj": alvo.cpf_cnpj,
            "telefone": alvo.telefone,
            "hash": alvo.hash,
            "qt_achados": sum(b["total"] for b in por_fonte.values()),
            "qt_exibidos": len(meus),
            "fontes": list(por_fonte.values()),
        })

    return {
        "resultados": resultados,
        "fontes_consultadas": [f.chave for f in selecionadas],
        "grupos_consultados": sorted(disponiveis),
        "aproximada": aproximada,
        "limite": limite,
        "fontes_com_erro": erros,
    }

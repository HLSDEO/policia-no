"""Identificação de pessoas na ingestão — quem é quem por trás dos registros do
aparelho E dos documentos financeiros do inquérito, por cruzamento DETERMINÍSTICO
(sem IA) e 100% auditável.

Várias fontes, nenhuma sozinha identifica ninguém: o contato de WhatsApp tem nome e
número, a agenda repete o número e às vezes traz o e-mail, as ligações confirmam o
número, o cartão de contato compartilhado numa conversa amarra nome, telefone e JID do mesmo
registro, as chaves PIX citadas nas conversas trazem CPF e e-mail, os aplicativos
bancários trazem o titular da conta, as contas de aplicativo trazem o e-mail/usuário
do dono — isso tudo vem do APARELHO. O RIF (SISCOAF) e o extrato SIMBA (R304) são do
INQUÉRITO, não do aparelho, mas têm a mesma força de identidade: o titular de uma
conta bancária e o envolvido citado num RIF vêm com CPF documental, e a contraparte de
um lançamento (SIMBA) ou de uma comunicação (RIF) também. Junto, identifica.

Roda por INQUÉRITO (não por extração — identidade é do caso, não do aparelho: com dois
celulares apreendidos, a dona de um costuma ser contato salvo no outro, e o titular do
extrato SIMBA costuma ser o dono de um dos aparelhos — cruzando tudo num índice só, ela
vira uma pessoa só com evidência de todas as fontes), ao final de cada extração e sob
demanda pelo botão da Mesa (e também depois de importar um RIF ou um extrato SIMBA, via
agendar_identificacao_pos_importacao):

1. Varre essas fontes (todas as extrações do aparelho + o RIF/SIMBA do inquérito
   inteiro, uma vez só) e monta PERFIS, unindo registros que compartilham um
   identificador forte — telefone normalizado, CPF, e-mail, usuário de app — ou o
   mesmo nome completo. Um segundo passe funde a grafia curta na completa
   ("João Silva" e "João Pedro da Silva" viram uma pessoa só).
2. Resolve o CPF na base de referência (DADOS_EXTRAIDOS.PES_TB01_PESSOAS) para dar
   nome (e nascimento) à pessoa por trás da chave PIX.
3. Materializa só quem está de fato IDENTIFICADO (ver _identificada): o dono do
   aparelho, quem tem CPF (inclusive vindo só do RIF/SIMBA), os cinco interlocutores
   de WhatsApp mais frequentes de CADA aparelho (ver _marcar_top_wpp) e quem tem nome
   e aparece em alguma fonte que PROMOVE (ver _GRUPOS_QUE_NAO_PROMOVEM). Contato que só existe
   na agenda, ou só num cartão de contato que alguém compartilhou, NÃO vira nó — isso já é a
   página de Contatos, e encher a mesa de contato solto não ajuda a investigação. Essas fontes
   entram no índice pelo que sabem AMARRAR: dão nome a quem estava salvo só como número e
   fundem perfis separados, enriquecendo quem já é nó por outro motivo.
4. Grava cada pessoa como entidade canônica 'pessoa' (ENT_TB05) com os registros
   de origem pendurados como evidência (ENT_TB06 — cada evidência diz de onde
   aquele pedaço da identidade veio), os identificadores em ENT_TB08 e as relações
   que dá para afirmar sem achismo: "conversou com" o dono do aparelho, "chave PIX
   citada por" quem mandou a chave na conversa, "contato compartilhado por" quem mandou o
   cartão de um terceiro, e "recebeu de"/"pagou a" entre o titular e a contraparte de um
   lançamento SIMBA ou de uma comunicação RIF.

Duas invariantes protegem contra a fusão em cascata, que é o jeito de isso dar errado
(vista em UFDR real): identificador que não identifica ninguém — rótulo de app do
Android, chave montada com campo vazio, CNPJ (é empresa, não pessoa) — não pode servir
de ponte (ver _usuario_app, _email, _telefone_br, _cpf_de_documento), e dois DONOS de
aparelhos diferentes só fundem por CPF, telefone ou e-mail em comum (ver
_Indice.fundir). O "dono do aparelho" em si só vem de DON_TB01_DONOS (a declaração
explícita do relatório do UFDR) e de contas configuradas no aparelho — NÃO do flag
"IsPhoneOwner" por conversa (ver _coletar_contatos_conversa): esse flag já veio errado
em UFDR real, marcando pessoas diferentes como dono e estourando o perfil.

Idempotente: rodar de novo só acrescenta o que faltou — a pessoa é reencontrada por
QUALQUER identificador dela (ENT_TB08), não só pela chave do nó.
"""

import logging
import re
import threading
import time
import unicodedata

from funcoes.db_utils import get_connection
from funcoes.ufdr_parser import PREFIXO_CONTATO_COMPARTILHADO

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Normalização
# ---------------------------------------------------------------------------

_SO_DIGITOS = re.compile(r"\D")
_RE_EMAIL = re.compile(r"^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$")
_CONECTORES = {"DE", "DA", "DO", "DAS", "DOS", "E"}
# domínios de JID de mensageiro — parecem e-mail e não são (ver _email)
_DOMINIOS_DE_MENSAGEIRO = {"s.whatsapp.net", "g.us", "c.us", "lid", "broadcast"}


def _sem_acento(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto or "") if unicodedata.category(c) != "Mn")


def _telefone(valor: str | None) -> str | None:
    """Telefone reduzido aos 11 últimos dígitos — casa '+55 51 99677-2307',
    '5551996772307' e '51996772307' na mesma chave. Exige 10 dígitos (DDD + número):
    número local sem DDD não identifica ninguém e ainda colide entre estados."""
    digitos = _SO_DIGITOS.sub("", valor or "")
    if len(digitos) in (12, 13) and digitos.startswith("55"):
        digitos = digitos[2:]
    if not 10 <= len(digitos) <= 13:
        return None
    return digitos[-11:]


def _telefone_de_jid(jid: str | None) -> str | None:
    """Telefone do JID do WhatsApp ('555196772307@s.whatsapp.net'). Ignora grupo
    (@g.us) e identificador oculto (@lid), que não são o número de ninguém."""
    if not jid:
        return None
    usuario, _, dominio = jid.partition("@")
    if dominio and not dominio.startswith("s.whatsapp"):
        return None
    return _telefone(usuario)


def _telefone_br(valor: str | None) -> str | None:
    """Versão estrita, para campo que PODE ser telefone mas também pode ser outra coisa
    (o Identifier de um mensageiro é telefone no Telegram e um id numérico no Facebook):
    exige formato de telefone brasileiro — 10/11 dígitos com DDD válido, com ou sem o 55.
    Sem isso, o id numérico da conta vira 'telefone' e funde pessoas que nada têm a ver."""
    texto = (valor or "").strip()
    # '@handle' de mensageiro (Instagram, etc.) não é telefone: o '@' nunca aparece num número
    # real, e o id numérico por trás dele (ex. Instagram @60046198415) passaria na validação
    # de DDD e fundiria gente que nada tem a ver — a conta só vira telefone quando NÃO é handle.
    if "@" in texto:
        return None
    digitos = _SO_DIGITOS.sub("", texto)
    if len(digitos) in (12, 13) and digitos.startswith("55"):
        digitos = digitos[2:]
    if len(digitos) not in (10, 11) or not 11 <= int(digitos[:2]) <= 99:
        return None
    return digitos[-11:]


def _email(valor: str | None) -> str | None:
    """E-mail de verdade. O JID do WhatsApp ('5527...@s.whatsapp.net') passa em
    qualquer regex de e-mail e não é um: ele é o telefone, e entra como telefone."""
    v = (valor or "").strip().lower()
    if not _RE_EMAIL.match(v) or v.partition("@")[2] in _DOMINIOS_DE_MENSAGEIRO:
        return None
    return v


def _cpf_de_documento(valor: str | None) -> str | None:
    """CPF (11 dígitos) de um campo CPF/CNPJ do RIF ou do SIMBA — só quando é claramente pessoa
    física. CNPJ (14 dígitos) não identifica uma PESSOA (identifica uma empresa), então não entra
    aqui: usar o nome da empresa como se fosse nome de gente criaria um nó falso na mesa. Documento
    de dígito único repetido (sentinela de "não informado") também não conta."""
    digitos = _SO_DIGITOS.sub("", valor or "")
    if len(digitos) == 11 and len(set(digitos)) > 1:
        return digitos
    return None


def _cnpj_de_documento(valor: str | None) -> str | None:
    """CNPJ (14 dígitos) de um campo CPF/CNPJ. O espelho de _cpf_de_documento: aqui o que interessa
    é justamente o que lá é descartado, porque a empresa vira nó próprio, não pessoa."""
    digitos = _SO_DIGITOS.sub("", valor or "")
    if len(digitos) == 14 and len(set(digitos)) > 1:
        return digitos
    return None


def _usuario_app(servico: str | None, usuario: str | None) -> str | None:
    """Identificador 'conta em um serviço'. Só vale com as DUAS partes preenchidas
    (serviço ou usuário vazio geraria a chave ':', que fundiria numa pessoa só todo
    registro sem conta) e sem espaço no usuário: "WhatsApp Business", "Facebook User"
    são RÓTULO de aplicativo, iguais em qualquer aparelho — nome de conta não tem
    espaço."""
    servico = (servico or "").strip().lower()
    usuario = (usuario or "").strip().lower()
    if not servico or len(usuario) < 2 or " " in usuario:
        return None
    return f"{servico}:{usuario}"[:300]


def _tokens_nome(nome: str | None) -> list[str]:
    """Nome em tokens maiúsculos sem acento, sem conectores e sem pedaço com dígito
    (contato salvo como 'Maria 2' ou 'João 99999-0000' é comum na agenda)."""
    tokens = []
    for bruto in re.split(r"[\s.]+", _sem_acento(nome or "").upper()):
        token = re.sub(r"[^A-Z]", "", bruto)
        if token and token not in _CONECTORES and not any(c.isdigit() for c in bruto):
            tokens.append(token)
    return tokens


def _nome_util(nome: str | None) -> bool:
    """Serve como nome de exibição? Descarta vazio, rótulo que é só o número e e-mail:
    contato sincronizado do Google costuma trazer o e-mail no lugar do nome, e e-mail é
    identificador, não nome de pessoa — como rótulo do nó ele esconde quem é."""
    if "@" in (nome or ""):
        return False
    return bool(_tokens_nome(nome))


def _nome_completo(nome: str | None) -> str | None:
    """Chave de fusão por nome: só nome com 2+ partes e corpo razoável vira
    identificador — 'Mãe', 'Chefe' e 'Zé' não podem unir pessoas diferentes."""
    if not _nome_util(nome):
        return None
    tokens = _tokens_nome(nome)
    if len(tokens) < 2 or sum(len(t) for t in tokens) < 8:
        return None
    return " ".join(tokens)


def _cpf_formatado(cpf: str) -> str:
    return f"{cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:]}"


def _valor_br(valor: float | None) -> str:
    """Valor em reais com milhar e decimal em pt-BR, à mão: o contêiner não garante locale
    pt_BR instalado, e locale.currency() cairia no C padrão, gravando "45,320.50" no detalhe."""
    inteiro, centavos = f"{float(valor or 0):.2f}".split(".")
    sinal = ""
    if inteiro.startswith("-"):
        sinal, inteiro = "-", inteiro[1:]
    milhar = ""
    while len(inteiro) > 3:
        milhar = "." + inteiro[-3:] + milhar
        inteiro = inteiro[:-3]
    return f"R$ {sinal}{inteiro}{milhar},{centavos}"


def _telefone_formatado(tel: str) -> str:
    if len(tel) == 11:
        return f"({tel[:2]}) {tel[2:7]}-{tel[7:]}"
    if len(tel) == 10:
        return f"({tel[:2]}) {tel[2:6]}-{tel[6:]}"
    return tel


# ---------------------------------------------------------------------------
# Perfis
# ---------------------------------------------------------------------------

# Grupos de fonte, usados pela régua de _identificada(). Agenda, WhatsApp, SMS e
# ligações são de propósito o MESMO grupo: são a lista de contatos do aparelho vista
# de vários ângulos, não confirmação independente de que aquela pessoa existe.
GRUPO_CONTATOS = "contatos"
GRUPO_FINANCEIRO = "financeiro"


class _MovimentoFinanceiro:
    """Quanto dinheiro, em quantos lançamentos e em que período — somado por par de pessoas.

    Existe porque a relação "pagou a (RIF)" / "recebeu de (SIMBA)" nascia só com o rótulo: na
    Mesa de Inquérito (e na carta exportada para o i2) ela dizia QUE houve pagamento e escondia
    QUANTO, deixando o investigador voltar ao extrato para saber se eram R$ 200 ou R$ 2 milhões.

    Soma em vez de máximo, ao contrário do grafo do SIMBA: lá o mesmo dinheiro chega DUAS vezes
    (débito no extrato de quem pagou, crédito no de quem recebeu) e somar contaria em dobro;
    aqui cada linha é uma CONTA/COMUNICAÇÃO diferente do mesmo par de pessoas, e o que se quer é
    o total movimentado entre elas."""

    __slots__ = ("valor", "lancamentos", "primeiro", "ultimo")

    def __init__(self, valor=None, lancamentos=None, primeiro=None, ultimo=None):
        self.valor = float(valor or 0)
        self.lancamentos = int(lancamentos or 0)
        self.primeiro = primeiro
        self.ultimo = ultimo

    def somar(self, outro: "_MovimentoFinanceiro") -> None:
        self.valor += outro.valor
        self.lancamentos += outro.lancamentos
        if outro.primeiro and (not self.primeiro or outro.primeiro < self.primeiro):
            self.primeiro = outro.primeiro
        if outro.ultimo and (not self.ultimo or outro.ultimo > self.ultimo):
            self.ultimo = outro.ultimo

    def descrever(self) -> str | None:
        """Texto de TX_DETALHE (VARCHAR(500)) — None quando não há nada a dizer, para não
        sobrescrever um detalhe existente com string vazia."""
        partes = []
        if self.valor:
            partes.append(_valor_br(self.valor))
        if self.lancamentos:
            partes.append(f"{self.lancamentos} lançamento(s)")
        texto = " em ".join(partes) if len(partes) == 2 else (partes[0] if partes else "")
        if self.primeiro and self.ultimo:
            intervalo = (f"em {self.primeiro:%d/%m/%Y}" if self.primeiro == self.ultimo
                         else f"entre {self.primeiro:%d/%m/%Y} e {self.ultimo:%d/%m/%Y}")
            texto = f"{texto}, {intervalo}" if texto else intervalo
        return texto[:500] or None
GRUPO_CONTAS = "contas"
GRUPO_DONO = "dono"
# Furo na régua acima: com quem o dono MAIS conversou no WhatsApp é a exceção que merece sair
# da lista de contatos, porque o volume é evidência por si só — ver _marcar_top_wpp.
GRUPO_TOP_WPP = "top_wpp"

# Cartão de contato compartilhado numa conversa ("Contato compartilhado: Felipo / CELL: …").
# Entra em _GRUPOS_QUE_NAO_PROMOVEM: enriquece quem já é nó, sem promover ninguém sozinho.
GRUPO_COMPARTILHADO = "contato_compartilhado"

# Quantos interlocutores de WhatsApp de cada aparelho entram na mesa por volume de conversa.
_TOP_CONVERSAS_WPP = 5
# Piso para o caso degenerado (aparelho quase sem WhatsApp): abaixo disso não é "das conversas
# mais frequentes do aparelho", é um punhado de mensagens, e pôr na mesa como top exageraria o
# vínculo. Nas oito extrações da base o 5º colocado tem de 348 a 34.153 mensagens, então o piso
# nunca corta ninguém de verdade — ele só existe para o aparelho vazio.
_MIN_MENSAGENS_TOP_WPP = 20

_ROTULO_GRUPO = {
    GRUPO_CONTATOS: "contatos do aparelho",
    GRUPO_FINANCEIRO: "PIX/contas bancárias",
    GRUPO_CONTAS: "contas de aplicativo",
    GRUPO_DONO: "dono do aparelho",
    GRUPO_TOP_WPP: f"top {_TOP_CONVERSAS_WPP} de conversa",
    GRUPO_COMPARTILHADO: "cartão de contato compartilhado em conversa",
}

# prioridade do nome por procedência (maior ganha na hora de rotular o nó)
NOME_BASE_REFERENCIA = 6
NOME_DOCUMENTAL = 5   # dono do aparelho, titular de conta bancária
NOME_CADASTRADO = 4   # agenda do aparelho, titular de conta de aplicativo
NOME_PERFIL = 3       # nome público do contato de WhatsApp/mensageiro
NOME_SECUNDARIO = 2   # contato de SMS, nome que veio junto da ligação


class _Perfil:
    """Uma pessoa em construção: os identificadores que a apontam, os nomes que ela
    recebeu (com a procedência de cada um) e os registros que a evidenciam."""

    def __init__(self):
        self.identificadores: set[tuple[str, str]] = set()
        self.nomes: list[tuple[int, str]] = []
        self.evidencias: dict[tuple[str, int], str] = {}
        self.grupos: set[str] = set()
        self.extracoes: set[int] = set()   # aparelhos em que ela aparece
        self.dono = False
        self.top_wpp: list[str] = []       # "2ª conversa mais frequente do Apple D84AP (...)"
        # Linhas que um DOCUMENTO diz estarem no nome desta pessoa (hoje, a ficha cadastral da
        # operadora). Ficam de fora do teto de _identificada: dez telefones no mesmo perfil é
        # sinal de fusão errada quando eles vieram da agenda de vários aparelhos, e é apenas o
        # ofício respondendo à quebra de sigilo quando vieram do cadastro — que é justamente o
        # caso do alvo da telefonia, o nó que mais importa ali.
        self.telefones_documentais: set[str] = set()
        self.id_canonica: int | None = None
        self.substituto: "_Perfil | None" = None   # apontador de fusão

    def raiz(self) -> "_Perfil":
        perfil = self
        while perfil.substituto is not None:
            perfil = perfil.substituto
        return perfil

    def valores(self, tipo: str) -> list[str]:
        return sorted(v for t, v in self.identificadores if t == tipo)

    def melhor_nome(self) -> str | None:
        if not self.nomes:
            return None
        # mais confiável primeiro; empatou, o mais completo — pelo comprimento, não pela
        # contagem de partes: "Casa Raçao V" tem 3 partes e diz menos que "Planeta Animal"
        return max(self.nomes, key=lambda n: (n[0], len(n[1])))[1]


# identificadores que sustentam sozinhos a afirmação "é a mesma pessoa"
_TIPOS_FORTES = ("cpf", "telefone", "email")


def _fortes(perfil: _Perfil) -> set[tuple[str, str]]:
    return {(t, v) for t, v in perfil.identificadores if t in _TIPOS_FORTES}


class _Indice:
    """Índice identificador → perfil, que é o que faz a fusão acontecer: registrar um
    contato cujo telefone já foi visto em outra fonte cai no mesmo perfil."""

    def __init__(self):
        self.perfis: list[_Perfil] = []
        self._por_identificador: dict[tuple[str, str], _Perfil] = {}
        self._por_origem: dict[tuple[str, int], _Perfil] = {}

    def registrar(self, identificadores, *, id_extracao, nome=None, prioridade=NOME_PERFIL,
                  evidencia=None, detalhe=None, grupo=None, dono=False) -> _Perfil | None:
        """Junta um registro ao perfil de quem ele fala (criando o perfil, se for a
        primeira vez). Devolve None quando o registro não tem nada que identifique.

        id_extracao é None para fontes que não são de um aparelho (RIF, SIMBA — documentos do
        INQUÉRITO): a pessoa ainda entra no índice normalmente, só não conta como "vista neste
        aparelho"."""
        identificadores = [i for i in identificadores if i and i[1]]
        chave_nome = _nome_completo(nome)
        if chave_nome:
            identificadores.append(("nome", chave_nome))
        if not identificadores:
            return None

        alvo = None
        for ident in identificadores:
            existente = self._por_identificador.get(ident)
            if existente is None:
                continue
            existente = existente.raiz()
            alvo = existente if alvo is None else self.fundir(alvo, existente)
        if alvo is None:
            alvo = _Perfil()
            self.perfis.append(alvo)

        for ident in identificadores:
            alvo.identificadores.add(ident)
            self._por_identificador[ident] = alvo
        if nome and _nome_util(nome):
            alvo.nomes.append((prioridade, " ".join(nome.split())[:200]))
        if evidencia:
            alvo.evidencias.setdefault(evidencia, detalhe)
            self._por_origem[evidencia] = alvo
        if grupo:
            alvo.grupos.add(grupo)
        if id_extracao is not None:
            alvo.extracoes.add(id_extracao)
        alvo.dono = alvo.dono or dono
        return alvo

    def perfil_do_identificador(self, ident: tuple[str, str]) -> "_Perfil | None":
        """Reencontra o perfil já registrado para um identificador exato (ex.: casar a
        contraparte do SIMBA/RIF com o perfil do titular da conta, já registrado antes)."""
        perfil = self._por_identificador.get(ident)
        return perfil.raiz() if perfil else None

    def fundir(self, a: _Perfil, b: _Perfil) -> _Perfil:
        """Funde dois perfis em um só (o mais antigo absorve), reapontando o índice.

        Recusa um caso: dois DONOS de aparelhos diferentes só fundem se tiverem CPF,
        telefone ou e-mail em comum — a mesma pessoa com dois celulares funde, dois
        titulares diferentes não. Sem isso, basta um identificador fraco em comum
        (rótulo de app do Android, por exemplo) para o inquérito inteiro virar uma
        pessoa só, e o dono é justamente o perfil que atrai mais registro."""
        a, b = a.raiz(), b.raiz()
        if a is b:
            return a
        donos_a, donos_b = set(a.valores("dono")), set(b.valores("dono"))
        if donos_a and donos_b and donos_a != donos_b and not (_fortes(a) & _fortes(b)):
            return a
        a.identificadores |= b.identificadores
        a.nomes.extend(b.nomes)
        for chave, detalhe in b.evidencias.items():
            a.evidencias.setdefault(chave, detalhe)
        a.grupos |= b.grupos
        a.extracoes |= b.extracoes
        a.top_wpp.extend(b.top_wpp)
        a.telefones_documentais |= b.telefones_documentais
        a.dono = a.dono or b.dono
        b.substituto = a
        # o índice não precisa ser reescrito além disto: toda leitura passa por raiz()
        for ident in b.identificadores:
            self._por_identificador[ident] = a
        return a

    def vivos(self) -> list[_Perfil]:
        return [p for p in self.perfis if p.substituto is None]

    def de_origem(self, origem) -> _Perfil | None:
        perfil = self._por_origem.get(origem)
        return perfil.raiz() if perfil else None

    def dono_de(self, id_extracao: int) -> _Perfil | None:
        """Dono de UM aparelho — um inquérito com duas extrações tem dois donos."""
        perfil = self._por_identificador.get(("dono", f"extracao:{id_extracao}"))
        return perfil.raiz() if perfil else None


def _fundir_por_nome_abreviado(indice: _Indice) -> None:
    """Segundo passe: mesma primeira e última parte do nome = mesma pessoa dentro do
    inquérito ('João Silva' ⊂ 'João Pedro da Silva'). É como o CPF resolvido na base
    de referência encontra o contato salvo com o nome curto. Não funde quando os dois
    perfis têm CPF diferente — aí são homônimos, e o documento manda."""
    baldes: dict[tuple[str, str], list[_Perfil]] = {}
    for perfil in indice.vivos():
        tokens = _tokens_nome(perfil.melhor_nome())
        if len(tokens) >= 2:
            baldes.setdefault((tokens[0], tokens[-1]), []).append(perfil)

    for grupo in baldes.values():
        base = grupo[0]
        for outro in grupo[1:]:
            base, outro = base.raiz(), outro.raiz()
            if base is outro:
                continue
            cpfs_a, cpfs_b = set(base.valores("cpf")), set(outro.valores("cpf"))
            if cpfs_a and cpfs_b and not (cpfs_a & cpfs_b):
                continue
            base = indice.fundir(base, outro)


# Grupos que, SOZINHOS, não fazem a pessoa virar nó da mesa.
#   contatos: é a agenda do aparelho vista de vários ângulos, e para ela existe a página de
#     Contatos; a mesa inteira viraria a agenda.
#   contato_compartilhado: medido no inquérito 8 em 18/08/2026 são 1.168 pessoas distintas em
#     1.847 cartões, contra 296 nós de pessoa na mesa. Promover cada cartão multiplicaria a
#     mesa por cinco com gente de quem só se sabe que o número passou por uma conversa.
# Os dois grupos seguem entrando no índice, e é aí que eles valem: o cartão amarra nome +
# telefone + JID do MESMO registro, então ENRIQUECE (nome para quem era só número, telefone a
# mais, fusão de perfis) quem já virou nó por outra fonte, e rende a aresta acima.
_GRUPOS_QUE_NAO_PROMOVEM = {GRUPO_CONTATOS, GRUPO_COMPARTILHADO}


def _identificada(perfil: _Perfil) -> bool:
    """A régua do que vira nó na mesa: o dono do aparelho, quem tem CPF, quem está no top
    _TOP_CONVERSAS_WPP de conversa do aparelho, e quem tem nome e aparece em ALGUMA fonte
    além das que não promovem (chave PIX, conta bancária, conta de aplicativo — ver
    _GRUPOS_QUE_NAO_PROMOVEM). Quem só existe na agenda, ou só num cartão de contato que
    alguém compartilhou, não é "pessoa identificada" — é contato, e para isso já existe a
    página de Contatos; despejar a agenda inteira na mesa não ajudaria ninguém a enxergar nada.

    O top do WhatsApp entra sem exigir nome: quem trocou dezenas de milhares de mensagens com
    o investigado é peça central do caso mesmo salvo só como número, e o rótulo do nó cai no
    telefone formatado (ver _nome_exibicao)."""
    # o teto conta só as linhas que NINGUÉM declarou dela: a ficha cadastral da operadora
    # dizendo que sete linhas estão no mesmo CPF não é fusão errada, é a resposta do ofício —
    # e sem essa ressalva o alvo da quebra de sigilo telefônico sumiria da mesa justamente no
    # caso em que ele tem mais linhas (ver _Perfil.telefones_documentais)
    eventuais = set(perfil.valores("telefone")) - perfil.telefones_documentais
    if len(eventuais) > _MAX_TELEFONES_POR_PESSOA:
        return False  # fusão que deu errado: melhor não afirmar nada do que afirmar isso
    if perfil.dono or perfil.valores("cpf") or GRUPO_TOP_WPP in perfil.grupos:
        return True
    return bool(perfil.melhor_nome()) and bool(perfil.grupos - _GRUPOS_QUE_NAO_PROMOVEM)


_PREFIXO_CHAVE = (("cpf", "cpf"), ("telefone", "tel"), ("email", "email"),
                  ("usuario_app", "app"), ("nome", "nome"))


def _chaves_candidatas(perfil: _Perfil) -> list[str]:
    """Todas as chaves de nó (NU_CHAVE) que esta pessoa poderia ter, da mais forte
    para a mais fraca. A primeira é a que ela recebe agora; as demais reencontram o
    nó gravado numa rodada anterior, quando ainda se sabia menos dela (só o telefone,
    por exemplo, e o CPF só apareceu agora)."""
    return [f"{prefixo}:{valor}"[:3000]
            for tipo, prefixo in _PREFIXO_CHAVE
            for valor in perfil.valores(tipo)]


def _nome_exibicao(perfil: _Perfil, chave: str) -> str:
    """Rótulo do nó: o melhor nome conhecido e, sem nome nenhum, o documento ou o
    telefone formatado — nó de pessoa sem rótulo legível não serve na mesa."""
    nome = perfil.melhor_nome()
    if nome:
        return nome
    if perfil.valores("cpf"):
        return "CPF " + _cpf_formatado(perfil.valores("cpf")[0])
    if perfil.valores("telefone"):
        return _telefone_formatado(perfil.valores("telefone")[0])
    return chave


def _observacao(perfil: _Perfil, nomes_base: dict[str, str]) -> str:
    """Resumo do que se sabe da pessoa, mostrado no modal do nó da mesa."""
    partes = []
    for cpf in perfil.valores("cpf"):
        nome_base = nomes_base.get(cpf)
        partes.append("CPF " + _cpf_formatado(cpf) + (f" — {nome_base} (base de referência)" if nome_base else ""))
    telefones = perfil.valores("telefone")
    if telefones:
        partes.append("Telefone(s): " + ", ".join(_telefone_formatado(t) for t in telefones[:6]))
    emails = perfil.valores("email")
    if emails:
        partes.append("E-mail(s): " + ", ".join(emails[:6]))
    contas = perfil.valores("usuario_app")
    if contas:
        partes.append("Conta(s): " + ", ".join(contas[:6]))
    if perfil.dono:
        partes.append("Dono do aparelho")
    # antes do rodapé de procedência: é o motivo de esta pessoa estar na mesa (ver _identificada)
    partes.extend(sorted(perfil.top_wpp))
    fontes = ", ".join(_ROTULO_GRUPO.get(g, g) for g in sorted(perfil.grupos))
    aparelhos = f" em {len(perfil.extracoes)} aparelhos" if len(perfil.extracoes) > 1 else ""
    partes.append(f"Identificada na ingestão por {fontes} ({len(perfil.evidencias)} registro(s){aparelhos})")
    return " · ".join(partes)[:4000]


# ---------------------------------------------------------------------------
# Coleta das fontes
# ---------------------------------------------------------------------------

_TP_ORIGEM_MENSAGEM = {"wpp": "wpp_mensagem", "sms": "sms_mensagem", "msg": "msg_mensagem"}
_TP_ORIGEM_CONTATO = {"wpp": "contato_wpp", "sms": "contato_sms", "msg": "contato_msg"}


def _coletar_contatos_conversa(cur, id_extracao: int, indice: _Indice) -> None:
    """Contatos de WhatsApp, SMS e mensageiros: nome do perfil + número/usuário da conta.

    NÃO usa IN_DONO_DISPOSITIVO (o "IsPhoneOwner" do Cellebrite, por Party/conversa) como sinal
    de fusão de identidade — só o DON_TB01_DONOS (_coletar_donos, a declaração explícita do
    próprio relatório do UFDR) decide quem é o dono. Visto em UFDR real: esse flag por conversa
    vem errado às vezes (grupo do iOS, app "Biome" tratado como mensageiro) e marca PESSOAS
    DIFERENTES como IsPhoneOwner — juntar tudo pela mesma chave "dono" cria um perfil com
    dezenas de telefones de gente sem nenhuma relação, que o guard de _identificada() descarta
    inteiro (incluindo o dono de verdade) por segurança. Resultado antes desta correção: mesa
    vazia sem nenhum aviso. O flag ainda aparece no detalhe da evidência, só não funde ninguém."""
    cur.execute(
        "SELECT id_contato, nu_whatsapp_id, no_contato, coalesce(in_dono_dispositivo, false) "
        "FROM investigacao.wpp_tb01_contatos WHERE id_extracao = %s",
        (id_extracao,),
    )
    for id_contato, jid, nome, dono in cur.fetchall():
        indice.registrar(
            [("telefone", _telefone_de_jid(jid))], id_extracao=id_extracao, nome=nome,
            prioridade=NOME_PERFIL, evidencia=("contato_wpp", id_contato),
            detalhe="contato de WhatsApp" + (" (marcado IsPhoneOwner numa conversa)" if dono else ""),
            grupo=GRUPO_CONTATOS,
        )

    cur.execute(
        "SELECT id_contato, nu_telefone, no_contato, coalesce(in_dono_dispositivo, false) "
        "FROM investigacao.sms_tb01_contatos WHERE id_extracao = %s",
        (id_extracao,),
    )
    for id_contato, telefone, nome, dono in cur.fetchall():
        indice.registrar(
            [("telefone", _telefone(telefone))], id_extracao=id_extracao, nome=nome,
            prioridade=NOME_SECUNDARIO, evidencia=("contato_sms", id_contato),
            detalhe="contato de SMS" + (" (marcado IsPhoneOwner numa conversa)" if dono else ""),
            grupo=GRUPO_CONTATOS,
        )

    cur.execute(
        "SELECT id_contato, tp_app, nu_identificador, no_contato, coalesce(in_dono_dispositivo, false) "
        "FROM investigacao.msg_tb01_contatos WHERE id_extracao = %s",
        (id_extracao,),
    )
    for id_contato, app, identificador, nome, dono in cur.fetchall():
        # o Identifier do mensageiro pode ser usuário, telefone (Telegram/Signal), e-mail
        # ou um id numérico da conta (Facebook) — daí o telefone estrito
        identificadores = [
            ("usuario_app", _usuario_app(app, identificador)),
            ("telefone", _telefone_de_jid(identificador) or _telefone_br(identificador)),
            ("email", _email(identificador)),
        ]
        indice.registrar(
            identificadores, id_extracao=id_extracao, nome=nome, prioridade=NOME_PERFIL,
            evidencia=("contato_msg", id_contato),
            detalhe=f"contato de {app}" + (" (marcado IsPhoneOwner numa conversa)" if dono else ""),
            grupo=GRUPO_CONTATOS,
        )


# Um contato de agenda com mais números que isto não é uma pessoa: é cartão agregado
# pelo próprio aparelho (aparece com 20+ números sob um nome só). Usá-lo como ponte
# funde meia agenda numa "pessoa" só — visto em UFDR real, não é hipótese.
_MAX_TELEFONES_POR_CONTATO = 4
# Mesma ideia depois da fusão: ninguém tem dezenas de linhas. Cluster assim é artefato.
_MAX_TELEFONES_POR_PESSOA = 8


def _coletar_agenda(cur, id_extracao: int, indice: _Indice) -> None:
    """Agenda do aparelho: é o que costuma amarrar telefone e e-mail da MESMA pessoa."""
    cur.execute(
        """
        SELECT c.id_contato, c.no_contato, e.tp_entrada, e.tx_valor
        FROM investigacao.ctt_tb01_contatos c
        LEFT JOIN investigacao.ctt_tb02_contato_entradas e ON e.id_contato = c.id_contato
        WHERE c.id_extracao = %s
        """,
        (id_extracao,),
    )
    por_contato: dict[int, dict] = {}
    for id_contato, nome, tp_entrada, valor in cur.fetchall():
        registro = por_contato.setdefault(id_contato, {"nome": nome, "identificadores": []})
        # nome do contato que é um e-mail (sincronizado do Google) identifica, mesmo
        # quando o contato não tem entrada EmailAddress nenhuma
        registro["identificadores"].append(("email", _email(nome)))
        if tp_entrada == "PhoneNumber":
            registro["identificadores"].append(("telefone", _telefone(valor)))
        elif tp_entrada == "EmailAddress":
            registro["identificadores"].append(("email", _email(valor)))

    for id_contato, registro in por_contato.items():
        identificadores = [i for i in registro["identificadores"] if i[1]]
        if sum(1 for tipo, _ in identificadores if tipo == "telefone") > _MAX_TELEFONES_POR_CONTATO:
            continue  # cartão agregado: não diz de quem é número nenhum
        indice.registrar(
            identificadores, id_extracao=id_extracao, nome=registro["nome"], prioridade=NOME_CADASTRADO,
            evidencia=("contato_agenda", id_contato), detalhe="entrada da agenda do aparelho",
            grupo=GRUPO_CONTATOS,
        )


_MIN_LIGACOES_RELACAO = 3  # 1-2 ligações é ruído (engano, insistência isolada) — não confirma
# vínculo; mesmo espírito de _MINIMO_PONTOS_LOCAL (canonicas_utils.py) para local em comum.


def _coletar_ligacoes(cur, id_extracao: int, indice: _Indice) -> list[tuple]:
    """Ligações, agrupadas por número: uma evidência por telefone (não uma por chamada).
    Devolve os pares (perfil, id_extracao, qtd, primeira, ultima) com qtd >=
    _MIN_LIGACOES_RELACAO, para virarem relação "ligou para" em _materializar, depois que
    os dois lados tiverem canônica. primeira/ultima são as datas da primeira e da última
    chamada com aquele número — vão pro detalhe da relação ("39 ligação(ões) entre
    11/12/2025 e 15/01/2026") e é delas que a mesa tira o período de atividade do nó."""
    cur.execute(
        """
        SELECT min(id_ligacao), nu_telefone_contato, max(no_contato), count(*),
               min(dt_ligacao), max(dt_ligacao)
        FROM investigacao.lig_tb01_ligacoes
        WHERE id_extracao = %s AND nu_telefone_contato IS NOT NULL
        GROUP BY nu_telefone_contato
        """,
        (id_extracao,),
    )
    pares = []
    for id_ligacao, telefone, nome, qt, primeira, ultima in cur.fetchall():
        perfil = indice.registrar(
            [("telefone", _telefone(telefone))], id_extracao=id_extracao, nome=nome, prioridade=NOME_SECUNDARIO,
            evidencia=("ligacao", id_ligacao), detalhe=f"{qt} ligação(ões) com este número",
            grupo=GRUPO_CONTATOS,
        )
        if perfil is not None and qt >= _MIN_LIGACOES_RELACAO:
            pares.append((perfil, id_extracao, qt, primeira, ultima))
    return pares


def _coletar_donos(cur, id_extracao: int, indice: _Indice) -> None:
    """Dono do aparelho declarado no relatório (User do UFDR).

    NU_IDENTIFICADOR entra como identificador forte, e é o que faz o dono deste aparelho ser
    reconhecido COMO A MESMA PESSOA do outro lado: o titular do WhatsApp vem aqui como
    '+5511950882711' e esse mesmo número está salvo na agenda do OUTRO celular apreendido —
    sem isso, o dono do aparelho 2 e o contato "Tiktok" do aparelho 1 ficavam dois perfis
    diferentes, e as centenas de mensagens trocadas entre os dois aparelhos apareciam penduradas
    num nó anônimo. O campo também traz o Apple ID (e-mail) do iPhone; no Android costuma vir
    '0' ou um id de usuário do sistema, que _telefone/_email descartam sozinhos."""
    pseudo_dono = ("dono", f"extracao:{id_extracao}")
    cur.execute(
        "SELECT id_dono, no_dono, tp_usuario, nu_identificador "
        "FROM investigacao.don_tb01_donos WHERE id_extracao = %s",
        (id_extracao,),
    )
    for id_dono, nome, tipo, identificador in cur.fetchall():
        indice.registrar(
            [pseudo_dono, ("telefone", _telefone_br(identificador)), ("email", _email(identificador))],
            id_extracao=id_extracao, nome=nome, prioridade=NOME_DOCUMENTAL,
            evidencia=("dono", id_dono),
            detalhe=f"dono do aparelho ({tipo})" if tipo else "dono do aparelho (relatório da extração)",
            grupo=GRUPO_DONO, dono=True,
        )


def _coletar_contas_usuario(cur, id_extracao: int, indice: _Indice) -> None:
    """Contas de aplicativo (UserAccount): Apple ID, Google, redes sociais.

    Numa extração de APARELHO (UFDR) as contas configuradas são todas do dono — marcar todas
    como dono é correto. Mas a mesma tabela serve a Nuvem IPED, onde a categoria "User Accounts"
    mistura contas de VÁRIAS pessoas (a do dono e a de quem aparece nos dados — e, num caso
    iCloud, até "contas" de lixo cujo nome é um arquivo SQLite). Marcar todas como dono fundiria
    o inquérito inteiro numa pessoa só. Por isso o dono é eleito pela CONTA DOMINANTE: o e-mail
    válido que aparece em mais contas; o resto entra como conta normal, fundido por e-mail, e
    conta sem e-mail válido não identifica ninguém."""
    pseudo_dono = ("dono", f"extracao:{id_extracao}")
    cur.execute(
        "SELECT id_conta_usuario, no_servico, no_usuario, no_titular "
        "FROM investigacao.cta_tb01_contas_usuario WHERE id_extracao = %s",
        (id_extracao,),
    )
    linhas = cur.fetchall()

    contagem: dict[str, int] = {}
    for _id, _servico, usuario, _titular in linhas:
        email = _email(usuario)
        if email:
            contagem[email] = contagem.get(email, 0) + 1
    email_dono = max(contagem, key=contagem.get) if contagem else None

    for id_conta, servico, usuario, titular in linhas:
        # NÃO usa o usuário da conta como identificador de fusão: o Android enche o UserAccount
        # de linha que é rótulo de app ou do próprio sistema ('com.whatsapp.w4b' /
        # 'WhatsApp Business', 'vnd.sec.contact.sim' / 'primary.sim.account_name'), idêntica em
        # todo aparelho — usar isso fundiu os donos de dois celulares diferentes num só. O e-mail
        # (validado) identifica; o resto é evidência.
        email = _email(usuario)
        eh_dono = email is not None and email == email_dono
        identificadores = [("email", email)] if email else []
        if eh_dono:
            identificadores.append(pseudo_dono)
        if not identificadores:
            continue  # sem e-mail válido (rótulo de app, nome de arquivo): não identifica ninguém
        indice.registrar(
            identificadores, id_extracao=id_extracao, nome=titular, prioridade=NOME_CADASTRADO,
            evidencia=("conta_usuario", id_conta),
            detalhe=f"conta {servico or 'de aplicativo'}: {usuario or '—'}"[:300],
            grupo=GRUPO_CONTAS, dono=eh_dono,
        )


# Campos do DeviceInfoEntry que identificam o APARELHO (não a pessoa). O Cellebrite escreve o
# rótulo em português ou inglês conforme a versão, daí casar por trecho normalizado.
# "Randomized Mac Address" fica de fora de propósito: é endereço rotativo de privacidade,
# muda a cada rede e não identifica nada.
_CAMPOS_APARELHO = (
    ("imei", ("imei",)),
    ("iccid", ("iccid",)),
    ("imsi", ("imsi",)),
    ("serial", ("serial", "factory number", "numero de serie")),
    ("mac", ("endereco mac", "mac address", "bluetooth mac")),
)
# Campos do DeviceInfoEntry que identificam a PESSOA dona do aparelho.
_CAMPO_TELEFONE_DONO = ("msisdn",)
_CAMPO_CONTA_DONO = ("id da apple", "apple id")


def _identificador_util(valor: str | None) -> bool:
    """Recusa o identificador de aparelho que não identifica aparelho nenhum.

    Mesma invariante que já protege o CPF (ver _cpf_de_documento): valor de caractere único
    repetido é sentinela de "não informado", não um número. Visto em produção — um iPhone
    trouxe ICCID '00000000000000000000', e como identificador ele faria dois inquéritos
    quaisquer com o mesmo placeholder "convergirem" por chip em comum."""
    if not valor or len(valor) < 8:
        return False
    return len(set(valor)) > 1


def _campo_aparelho(nome_campo: str | None) -> str | None:
    """Tipo de identificador de aparelho que este campo do DeviceInfoEntry representa."""
    alvo = _sem_acento((nome_campo or "").strip().lower())
    if "randomized" in alvo:
        return None
    for tipo, trechos in _CAMPOS_APARELHO:
        if any(t in alvo for t in trechos):
            return tipo
    return None


def _coletar_info_aparelho(cur, id_extracao: int, indice: _Indice) -> list[tuple[str, str, str, int]]:
    """Identidade do aparelho (DeviceInfoEntry): IMEI, ICCID, IMSI, série, MAC, MSISDN e Apple ID.

    Duas coisas saem daqui, e é importante não confundi-las:

    - o que identifica a PESSOA (MSISDN = a linha do aparelho; ID da Apple = e-mail do titular)
      entra no perfil do dono, como qualquer outra fonte. Hoje o titular de iPhone é DEDUZIDO
      varrendo as contas de aplicativo e filtrando por domínio Apple; aqui ele vem declarado.
    - o que identifica o APARELHO (IMEI/ICCID/IMSI/série/MAC) NÃO é identificador de pessoa e
      não pode entrar no perfil: viraria chave de fusão e juntaria pessoas diferentes que
      usaram o mesmo celular. Volta como lista para virar uma canônica própria do tipo
      'dispositivo' (ver _materializar_dispositivos).

    Devolve [(tipo, valor, tp_origem, id_origem)] dos identificadores do aparelho —
    o par tp_origem/id_origem acompanha o identificador para _materializar_dispositivos
    gravar a evidência na tabela certa (o UFDR usa `info_aparelho`, o IPED/nuvem usa
    `dispositivo_conta`)."""
    pseudo_dono = ("dono", f"extracao:{id_extracao}")
    do_aparelho: list[tuple[str, str, str, int]] = []
    cur.execute(
        "SELECT id_info, no_campo, tx_valor FROM investigacao.dev_tb01_info_aparelho "
        "WHERE id_extracao = %s AND tx_valor IS NOT NULL AND btrim(tx_valor) <> ''",
        (id_extracao,),
    )
    for id_info, campo, valor in cur.fetchall():
        valor = (valor or "").strip()
        alvo = _sem_acento((campo or "").strip().lower())

        tipo_aparelho = _campo_aparelho(campo)
        if tipo_aparelho:
            # só dígitos para IMEI/ICCID/IMSI; MAC e série ficam como vieram, em minúsculas
            limpo = re.sub(r"\D", "", valor) if tipo_aparelho in ("imei", "iccid", "imsi") else valor.lower()
            if _identificador_util(limpo):
                do_aparelho.append((tipo_aparelho, limpo[:300], "info_aparelho", id_info))
            continue

        if any(t in alvo for t in _CAMPO_TELEFONE_DONO):
            telefone = _telefone(valor)
            if telefone:
                indice.registrar(
                    [pseudo_dono, ("telefone", telefone)], id_extracao=id_extracao,
                    evidencia=("info_aparelho", id_info),
                    detalhe=f"linha do aparelho ({campo})"[:300],
                    grupo=GRUPO_DONO, dono=True,
                )
        elif any(t in alvo for t in _CAMPO_CONTA_DONO):
            email = _email(valor)
            if email:
                indice.registrar(
                    [pseudo_dono, ("email", email)], id_extracao=id_extracao,
                    evidencia=("info_aparelho", id_info),
                    detalhe=f"conta do aparelho ({campo}): {valor}"[:300],
                    grupo=GRUPO_CONTAS, dono=True,
                )
    return do_aparelho


def _coletar_info_aparelho_nuvem(cur, id_extracao: int, indice: _Indice) -> list[tuple[str, str, str, int]]:
    """Identidade do aparelho vinda da nuvem (IPED), gravada em DVC_TB01_DISPOSITIVOS.

    A extração do IPED não escreve em DEV_TB01_INFO_APARELHO (tabela do UFDR): os
    dispositivos que ela conhece são os da conta Google — o produto "Android Device
    Configuration Service" do Takeout, roteado por takeout_parsers.parse_adcs para
    DVC_TB01_DISPOSITIVOS com modelo, marca, Android ID, IMEI e serial. Esta é a ponte que
    faltava para esses aparelhos virarem nó 'dispositivo' na mesa: mesmo papel de
    _coletar_info_aparelho, alimentando _materializar_dispositivos. IMEI e serial entram
    como identificador de APARELHO (não de pessoa); Android ID fica de fora de propósito —
    não está em _PRIORIDADE_CHAVE_APARELHO e não sustenta convergência entre casos."""
    do_aparelho: list[tuple[str, str, str, int]] = []
    cur.execute(
        "SELECT id_dispositivo, tx_imei, tx_serial "
        "FROM investigacao.dvc_tb01_dispositivos WHERE id_extracao = %s",
        (id_extracao,),
    )
    for id_dispositivo, imei, serial in cur.fetchall():
        # o ADCS do Google pode gravar vários valores no mesmo campo ("IMEI(s)" de um
        # dual-SIM, "Serial Number(s)"), separados por vírgula — um identificador por valor.
        for tipo, bruto in (("imei", imei), ("serial", serial)):
            for pedaco in re.split(r"[,;|\n]+", (bruto or "")):
                limpo = re.sub(r"\D", "", pedaco) if tipo == "imei" else pedaco.strip().lower()
                if _identificador_util(limpo):
                    do_aparelho.append((tipo, limpo[:300], "dispositivo_conta", id_dispositivo))
    return do_aparelho


def _coletar_eventos_nuvem(cur, id_extracao: int, indice: _Indice) -> None:
    """Organizador e participantes de eventos de agenda da nuvem (EVT_TB01_EVENTOS), que o
    IPED preenche para caso de nuvem — mesmo papel de _coletar_convidados_compromisso do UFDR:
    enriquece quem já é nó (dá nome a quem estava salvo só como número e funde perfis), sem
    promover ninguém sozinho (GRUPO_CONTATOS, ver _GRUPOS_QUE_NAO_PROMOVEM)."""
    cur.execute(
        "SELECT id_evento, tx_titulo, tx_organizador, tx_participantes "
        "FROM investigacao.evt_tb01_eventos "
        "WHERE id_extracao = %s AND (tx_organizador IS NOT NULL OR tx_participantes IS NOT NULL)",
        (id_extracao,),
    )
    for id_evento, titulo, organizador, participantes in cur.fetchall():
        for texto, papel in ((organizador, "organizador"), (participantes, "participante")):
            for nome, ident in _partes_de_rotulos(texto):
                identificadores = _identificadores_de_parte(ident)
                if not identificadores and not _nome_util(nome):
                    continue
                indice.registrar(
                    identificadores, id_extracao=id_extracao, nome=nome,
                    prioridade=NOME_CADASTRADO,
                    evidencia=("evento", id_evento),
                    detalhe=f"{papel} de \"{titulo or 'evento'}\""[:300],
                    grupo=GRUPO_CONTATOS,
                )


def _partes_de_rotulos(texto: str | None) -> list[tuple[str | None, str | None]]:
    """Quebra "Fulano <fulano@x.com>, Beltrano <+5527...>" em [(nome, identificador)].

    É o formato que o parser produz para participante (ufdr_parser._rotulo_parties), usado em
    convidado de compromisso e participante de transferência. A quebra é feita pelos
    identificadores entre < >, não por vírgula: nome com vírgula existe ("SILVA, JOAO") e
    partir por vírgula fundiria pessoas erradas — que é o pior erro possível aqui."""
    if not texto:
        return []
    partes: list[tuple[str | None, str | None]] = []
    ultimo_fim = 0
    for m in re.finditer(r"<([^>]+)>", texto):
        nome = texto[ultimo_fim:m.start()].strip(" ,;")
        partes.append((nome or None, m.group(1).strip() or None))
        ultimo_fim = m.end()
    if not partes:
        # sem < >: o Cellebrite trouxe só o nome, sem identificador nenhum
        resto = texto.strip(" ,;")
        if resto:
            partes.append((resto, None))
    return partes


def _identificadores_de_parte(valor: str | None) -> list[tuple[str, str]]:
    """Converte o identificador de um participante em (tipo, valor) — e-mail ou telefone."""
    email = _email(valor)
    if email:
        return [("email", email)]
    telefone = _telefone(valor)
    if telefone:
        return [("telefone", telefone)]
    return []


def _coletar_convidados_compromisso(cur, id_extracao: int, indice: _Indice) -> None:
    """Convidados de compromisso da agenda (CalendarEntry/Attendees).

    Cada convidado vem como participante com e-mail ou telefone e nome — mesma forma dos
    contatos. Marcar encontro com alguém é vínculo declarado pela própria pessoa."""
    cur.execute(
        "SELECT id_compromisso, tx_assunto, tx_convidados, no_organizador "
        "FROM investigacao.cal_tb01_calendario "
        "WHERE id_extracao = %s AND (tx_convidados IS NOT NULL OR no_organizador IS NOT NULL)",
        (id_extracao,),
    )
    for id_compromisso, assunto, convidados, organizador in cur.fetchall():
        for texto, papel in ((convidados, "convidado"), (organizador, "organizador")):
            for nome, ident in _partes_de_rotulos(texto):
                identificadores = _identificadores_de_parte(ident)
                if not identificadores and not _nome_util(nome):
                    continue
                indice.registrar(
                    identificadores, id_extracao=id_extracao, nome=nome,
                    prioridade=NOME_CADASTRADO,
                    evidencia=("compromisso", id_compromisso),
                    detalhe=f"{papel} de \"{assunto or 'compromisso'}\""[:300],
                    grupo=GRUPO_CONTATOS,
                )


def _coletar_participantes_transferencia(cur, id_extracao: int, indice: _Indice) -> list[tuple]:
    """Participantes de transferência de valores (TransferOfFunds).

    Mesma natureza das contrapartes de SIMBA/RIF, que já são fonte do grupo financeiro — com a
    vantagem de já vir amarrada ao aparelho apreendido. Devolve (id_extracao, perfil, detalhe)
    para virar relação de dinheiro entre o dono do aparelho e o participante."""
    pares: list[tuple] = []
    cur.execute(
        "SELECT id_transferencia, tx_participantes, vl_valor, no_moeda, tp_transferencia, dt_envio "
        "FROM investigacao.trf_tb01_transferencias "
        "WHERE id_extracao = %s AND tx_participantes IS NOT NULL",
        (id_extracao,),
    )
    for id_transf, participantes, valor, moeda, tipo, dt_envio in cur.fetchall():
        for nome, ident in _partes_de_rotulos(participantes):
            identificadores = _identificadores_de_parte(ident)
            if not identificadores and not _nome_util(nome):
                continue
            detalhe = "transferência"
            if valor is not None:
                detalhe += f" de {valor} {moeda or ''}".rstrip()
            if tipo:
                detalhe += f" ({tipo})"
            perfil = indice.registrar(
                identificadores, id_extracao=id_extracao, nome=nome,
                prioridade=NOME_DOCUMENTAL, evidencia=("transferencia", id_transf),
                detalhe=detalhe[:300], grupo=GRUPO_FINANCEIRO,
            )
            if perfil is not None:
                pares.append((id_extracao, perfil, detalhe[:300]))
    return pares


def _coletar_cartoes_credito(cur, id_extracao: int, indice: _Indice) -> None:
    """Titular impresso no cartão de crédito (CreditCard/NameOnCard).

    Nome documental, mesma confiabilidade do titular de conta bancária: é o que transforma um
    contato de apelido curto em pessoa com nome completo."""
    pseudo_dono = ("dono", f"extracao:{id_extracao}")
    cur.execute(
        "SELECT id_cartao, no_titular, no_bandeira, nu_cartao FROM investigacao.crt_tb01_cartoes_credito "
        "WHERE id_extracao = %s AND no_titular IS NOT NULL",
        (id_extracao,),
    )
    for id_cartao, titular, bandeira, numero in cur.fetchall():
        if not _nome_util(titular):
            continue
        indice.registrar(
            [pseudo_dono], id_extracao=id_extracao, nome=titular, prioridade=NOME_DOCUMENTAL,
            evidencia=("cartao_credito", id_cartao),
            detalhe=f"titular do cartão {bandeira or ''} {numero or ''}".strip()[:300],
            grupo=GRUPO_FINANCEIRO, dono=True,
        )


def _coletar_contas_financeiras(cur, id_extracao: int, indice: _Indice) -> None:
    """Aplicativos bancários: o titular da conta é um nome documental — é o que
    transforma um contato de nome curto em pessoa com nome completo."""
    cur.execute(
        "SELECT id_conta_financeira, no_instituicao, no_titular "
        "FROM investigacao.bnc_tb01_contas_financeiras WHERE id_extracao = %s",
        (id_extracao,),
    )
    for id_conta, instituicao, titular in cur.fetchall():
        if not _nome_completo(titular):
            continue  # sem nome de pessoa, a conta não identifica ninguém
        indice.registrar(
            [], id_extracao=id_extracao, nome=titular, prioridade=NOME_DOCUMENTAL,
            evidencia=("conta_financeira", id_conta),
            detalhe=f"titular da conta em {instituicao or 'instituição não informada'}"[:300],
            grupo=GRUPO_FINANCEIRO,
        )


# ---------------------------------------------------------------------------
# RIF (SISCOAF) e SIMBA (R304) — documentos de inteligência financeira anexados ao
# INQUÉRITO (não a uma extração de aparelho), então rodam uma vez só, fora do laço por
# extração — ver identificar_pessoas_inquerito. Só entram com CPF (11 dígitos): CNPJ é
# empresa, e nome de empresa sozinho fundiria negócios homônimos ou criaria pessoa falsa.
# ---------------------------------------------------------------------------


def _coletar_titulares_simba(cur, id_inquerito: int, indice: _Indice) -> None:
    """Titulares de conta do extrato bancário do SIMBA (R304) — documento do banco, então
    NOME_DOCUMENTAL (mesma confiança do titular de app bancário e do dono do aparelho)."""
    cur.execute(
        """
        SELECT c.id_conta_simba, c.no_titular, c.nu_cpf_cnpj, c.no_banco
        FROM investigacao.smb_tb02_contas c
        JOIN investigacao.smb_tb01_extratos s ON s.id_extrato = c.id_extrato
        WHERE s.id_inquerito = %s
        """,
        (id_inquerito,),
    )
    for id_conta, nome, doc, banco in cur.fetchall():
        cpf = _cpf_de_documento(doc)
        if not cpf:
            continue
        indice.registrar(
            [("cpf", cpf)], id_extracao=None, nome=nome, prioridade=NOME_DOCUMENTAL,
            evidencia=("simba_conta", id_conta),
            detalhe=f"titular de conta bancária (SIMBA) em {banco or 'instituição não informada'}"[:300],
            grupo=GRUPO_FINANCEIRO,
        )


def _coletar_contrapartes_simba(cur, id_inquerito: int, indice: _Indice) -> list[tuple]:
    """Contrapartes do extrato SIMBA (quem transacionou com as contas afastadas), ligadas ao
    titular da conta correspondente — devolve os pares (perfil do titular, perfil da
    contraparte, direção) para virarem relação em _materializar, depois que os dois lados já
    tiverem canônica."""
    cur.execute(
        """
        SELECT cp.id_contraparte_simba, cp.no_contraparte, cp.nu_cpf_cnpj, cp.cd_direcao,
               c.nu_cpf_cnpj AS doc_titular,
               cp.vl_total, cp.qt_lancamentos, cp.dt_primeiro, cp.dt_ultimo
        FROM investigacao.smb_tb04_contrapartes cp
        JOIN investigacao.smb_tb02_contas c ON c.id_conta_simba = cp.id_conta_simba
        JOIN investigacao.smb_tb01_extratos s ON s.id_extrato = cp.id_extrato
        WHERE s.id_inquerito = %s
        """,
        (id_inquerito,),
    )
    pares = []
    for (id_cp, nome_cp, doc_cp, direcao, doc_titular,
         vl_total, qt_lanc, dt_primeiro, dt_ultimo) in cur.fetchall():
        cpf_cp = _cpf_de_documento(doc_cp)
        if not cpf_cp:
            continue
        perfil_cp = indice.registrar(
            [("cpf", cpf_cp)], id_extracao=None, nome=nome_cp, prioridade=NOME_DOCUMENTAL,
            evidencia=("simba_contraparte", id_cp),
            detalhe="contraparte em extrato bancário (SIMBA)"
                    + (" — creditou o titular" if direcao == "credito" else " — foi debitado pelo titular"),
            grupo=GRUPO_FINANCEIRO,
        )
        cpf_titular = _cpf_de_documento(doc_titular)
        perfil_titular = indice.perfil_do_identificador(("cpf", cpf_titular)) if cpf_titular else None
        if perfil_cp is not None and perfil_titular is not None:
            pares.append((perfil_titular, perfil_cp, direcao,
                          _MovimentoFinanceiro(vl_total, qt_lanc, dt_primeiro, dt_ultimo)))
    return pares


def _coletar_titulares_rif(cur, id_inquerito: int, indice: _Indice) -> None:
    """Envolvidos do RIF (COAF) — nome + CPF documental do relatório de inteligência
    financeira, qualquer papel (Titular/Remetente/Beneficiário/...)."""
    cur.execute(
        """
        SELECT ev.id_envolvido_rif, ev.no_envolvido, ev.nu_cpf_cnpj, ev.tp_envolvido, r.nu_rif
        FROM investigacao.rif_tb03_envolvidos ev
        JOIN investigacao.rif_tb01_rifs r ON r.id_rif = ev.id_rif
        WHERE r.id_inquerito = %s
        """,
        (id_inquerito,),
    )
    for id_envolvido, nome, doc, papel, nu_rif in cur.fetchall():
        cpf = _cpf_de_documento(doc)
        if not cpf:
            continue
        indice.registrar(
            [("cpf", cpf)], id_extracao=None, nome=nome, prioridade=NOME_DOCUMENTAL,
            evidencia=("rif_envolvido", id_envolvido),
            detalhe=f"envolvido ({papel or 'papel não informado'}) no RIF {nu_rif or ''}"[:300],
            grupo=GRUPO_FINANCEIRO,
        )


def _coletar_contrapartes_rif(cur, id_inquerito: int, indice: _Indice) -> list[tuple]:
    """Contrapartes estruturadas do RIF (RIF_TB05, por comunicação), ligadas ao titular
    principal do RIF (o mesmo critério do cabeçalho de rif.html: envolvido mais citado como
    'Titular') — devolve os pares para virarem relação em _materializar."""
    cur.execute(
        """
        SELECT r.id_rif,
               (array_agg(tit.nu_cpf_cnpj ORDER BY tit.qtd DESC))[1] AS doc_titular
        FROM investigacao.rif_tb01_rifs r
        JOIN LATERAL (
            SELECT nu_cpf_cnpj, count(*) AS qtd
            FROM investigacao.rif_tb03_envolvidos
            WHERE id_rif = r.id_rif AND trim(tp_envolvido) = 'Titular' AND nu_cpf_cnpj IS NOT NULL
            GROUP BY nu_cpf_cnpj
        ) tit ON true
        WHERE r.id_inquerito = %s
        GROUP BY r.id_rif
        """,
        (id_inquerito,),
    )
    titular_por_rif = {id_rif: _cpf_de_documento(doc) for id_rif, doc in cur.fetchall()}
    if not titular_por_rif:
        return []

    cur.execute(
        """
        SELECT cp.id_contraparte_rif, cp.no_contraparte, cp.nu_cpf_cnpj, cp.cd_direcao, cp.id_rif,
               cp.vl_total, cp.qt_lancamentos,
               -- a data não está na contraparte: é da COMUNICAÇÃO em que ela aparece. Operação
               -- quando o RIF a traz, senão o recebimento — a mesma regra do filtro de período
               -- do grafo do RIF, para o intervalo gravado bater com o que se vê na tela.
               COALESCE(com.dt_operacao, com.dt_recebimento::date) AS dt_com
        FROM investigacao.rif_tb05_contrapartes cp
        JOIN investigacao.rif_tb01_rifs r ON r.id_rif = cp.id_rif
        LEFT JOIN investigacao.rif_tb02_comunicacoes com
               ON com.id_comunicacao_rif = cp.id_comunicacao_rif
        WHERE r.id_inquerito = %s
        """,
        (id_inquerito,),
    )
    pares = []
    for (id_cp, nome_cp, doc_cp, direcao, id_rif,
         vl_total, qt_lanc, dt_com) in cur.fetchall():
        cpf_titular = titular_por_rif.get(id_rif)
        if not cpf_titular:
            continue
        cpf_cp = _cpf_de_documento(doc_cp)
        if not cpf_cp:
            continue
        perfil_cp = indice.registrar(
            [("cpf", cpf_cp)], id_extracao=None, nome=nome_cp, prioridade=NOME_DOCUMENTAL,
            evidencia=("rif_contraparte", id_cp),
            detalhe="contraparte estruturada do RIF"
                    + (" — creditou o titular" if direcao == "credito" else " — foi debitado pelo titular"),
            grupo=GRUPO_FINANCEIRO,
        )
        perfil_titular = indice.perfil_do_identificador(("cpf", cpf_titular))
        if perfil_cp is not None and perfil_titular is not None:
            pares.append((perfil_titular, perfil_cp, direcao,
                          _MovimentoFinanceiro(vl_total, qt_lanc, dt_com, dt_com)))
    return pares


# grupo próprio (não GRUPO_FINANCEIRO): quem é citado só no IPL, sem estar em RIF/SIMBA/
# contatos, não deve ganhar o rótulo "PIX/contas bancárias" na observação da canônica.
GRUPO_IPL = "ipl"
_ROTULO_GRUPO[GRUPO_IPL] = "citada em inquérito (IPL)"


def _coletar_pessoas_ipl(cur, id_inquerito: int, indice: _Indice) -> None:
    """Pessoas citadas em inquéritos policiais em PDF (IPL_TB03_PESSOAS) — nome, CPF/CNPJ,
    vulgo e papel extraídos por ipl_utils.py. Diferente de RIF/SIMBA, registra mesmo sem CPF:
    o mais comum no IPL é vulgo/nome sem documento (delegado, advogado, "vulgo Magrelo"), e
    essa é a única fonte automática que cobriria esse caso. _identificada() já filtra o ruído:
    materializa quem tem CPF OU (nome + fonte fora de GRUPO_CONTATOS) — como GRUPO_IPL nunca é
    GRUPO_CONTATOS, nome+vulgo do IPL sempre passa, igual chave PIX ou conta bancária passam."""
    cur.execute(
        """
        SELECT pe.id_pessoa_ipl, pe.no_pessoa, pe.nu_cpf_cnpj, pe.no_vulgo, pe.tp_papel
        FROM investigacao.ipl_tb03_pessoas pe
        JOIN investigacao.ipl_tb01_inqueritos_pdf p ON p.id_ipl = pe.id_ipl
        WHERE p.id_inquerito = %s
        """,
        (id_inquerito,),
    )
    for id_pessoa, nome, doc, vulgo, papel in cur.fetchall():
        cpf = _cpf_de_documento(doc)
        detalhe = f"citada no IPL ({papel or 'papel não informado'})"
        if vulgo:
            detalhe += f", vulgo {vulgo}"
        indice.registrar(
            [("cpf", cpf)] if cpf else [], id_extracao=None, nome=nome, prioridade=NOME_DOCUMENTAL,
            evidencia=("ipl_pessoa", id_pessoa), detalhe=detalhe[:300], grupo=GRUPO_IPL,
        )


# ---------------------------------------------------------------------------
# Peças compartilhadas pelas fontes DOCUMENTAIS que também criam nó de empresa
# (IPEI, IRPF e telefonia): o upsert do nó, a evidência, o identificador e a
# "ponta de aresta" que ainda não sabe qual é o nó dela.
# ---------------------------------------------------------------------------


def _upsert_canonica(cur, id_inquerito: int, tp_entidade: str, nome: str, chave: str,
                     observacao: str | None = None,
                     atualizar_nome: bool = True) -> tuple[int | None, bool]:
    """Cria (ou reencontra pela chave de deduplicação) um nó da mesa; devolve
    (id_canonica, criada).

    Nunca sobrescreve nó manual: o UPDATE é condicionado a IN_AUTOMATICO, e o SELECT de
    resgate cobre justamente o caso em que esse WHERE barrou o UPDATE — aí o RETURNING não
    devolve linha nenhuma, e sem o resgate a aresta ficaria sem uma das pontas.

    atualizar_nome=False para o nó que OUTRA fonte rotula melhor. O IMEI que a operadora
    devolve no ofício e o IMEI do aparelho apreendido caem na MESMA chave ('imei:...') — e o
    rótulo bom ali é "Apple iPhone 13" do UFDR, não "IMEI 3591...". Sem isso, quem rodasse por
    último decidiria o rótulo do nó."""
    sets = ["tx_observacao = COALESCE(EXCLUDED.tx_observacao, ent_tb05_canonicas.tx_observacao)"]
    if atualizar_nome:
        sets.insert(0, "no_entidade = EXCLUDED.no_entidade")
    cur.execute(
        f"""
        INSERT INTO investigacao.ent_tb05_canonicas
            (id_inquerito, tp_entidade, no_entidade, nu_chave, tx_observacao, in_automatico)
        VALUES (%s, %s, %s, %s, %s, true)
        ON CONFLICT (id_inquerito, tp_entidade, nu_chave) DO UPDATE
            SET {", ".join(sets)}
            WHERE ent_tb05_canonicas.in_automatico
        RETURNING id_canonica, (xmax = 0) AS criada
        """,
        (id_inquerito, tp_entidade, (nome or chave)[:200], chave[:3000],
         (observacao or None) and observacao[:4000]),
    )
    linha = cur.fetchone()
    if linha is not None:
        return linha[0], linha[1]
    cur.execute(
        "SELECT id_canonica FROM investigacao.ent_tb05_canonicas "
        "WHERE id_inquerito = %s AND tp_entidade = %s AND nu_chave = %s",
        (id_inquerito, tp_entidade, chave[:3000]),
    )
    linha = cur.fetchone()
    return (linha[0] if linha else None), False


def _gravar_evidencia(cur, id_canonica: int, tp_origem: str, id_origem: int,
                      detalhe: str | None) -> int:
    """Pendura o registro extraído como evidência do nó. Devolve 1 quando é nova."""
    cur.execute(
        """
        INSERT INTO investigacao.ent_tb06_canonica_evidencias
            (id_canonica, tp_origem, id_origem, tx_detalhe)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (id_canonica, tp_origem, id_origem) DO NOTHING
        """,
        (id_canonica, tp_origem, id_origem, (detalhe or "")[:3000] or None),
    )
    return cur.rowcount


def _gravar_identificador(cur, id_canonica: int, id_inquerito: int, tipo: str, valor: str,
                          origem: str) -> None:
    """Identificador do nó (ENT_TB08) — é por ele que a busca de entidades e a convergência
    entre casos reencontram a mesma coisa em outro inquérito."""
    cur.execute(
        """
        INSERT INTO investigacao.ent_tb08_canonica_identificadores
            (id_canonica, id_inquerito, tp_identificador, co_valor, tx_origem)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (id_canonica, tp_identificador, co_valor) DO NOTHING
        """,
        (id_canonica, id_inquerito, tipo[:20], valor[:300], origem[:300]),
    )


class _Empresas(dict):
    """CNPJ -> (melhor nome, evidências) das empresas que uma fonte documental levantou.

    Empresa NÃO entra no _Indice: ele é índice de PESSOA, e usar nome de empresa como nome de
    gente criaria nó falso e fundiria negócios homônimos (ver _cpf_de_documento). Ela vira nó
    próprio na materialização, e é isso que dá à aresta "sociedade", "recebeu de" ou "titular
    da linha" um lugar para chegar."""

    def lembrar(self, cnpj: str, nome: str | None, origem: tuple[str, int], detalhe: str) -> None:
        nome_atual, evidencias = self.get(cnpj, (None, []))
        self[cnpj] = (nome_atual or nome,
                      evidencias + [(origem[0], origem[1], (detalhe or "")[:300])])


# Teto de evidências por empresa: uma fonte pagadora do IRPF ou um emitente de NF-e da IPEI
# rende centenas de linhas do mesmo CNPJ, e a lista do nó na mesa não fica mais verdadeira
# com 400 itens do que com 20 — só fica ilegível (e a gravação, lenta).
_MAX_EVIDENCIAS_POR_EMPRESA = 20


def _materializar_organizacoes(cur, id_inquerito: int, empresas: dict, observacao: str,
                               tx_origem: str, resumo: dict) -> dict[str, int]:
    """Cria um nó 'organizacao' por CNPJ levantado e devolve {cnpj: id_canonica}."""
    canonica_por_cnpj: dict[str, int] = {}
    for cnpj, (nome, evidencias) in sorted(empresas.items()):
        id_canonica, criada = _upsert_canonica(
            cur, id_inquerito, "organizacao", nome or f"CNPJ {cnpj}", cnpj, observacao)
        if id_canonica is None:
            continue
        canonica_por_cnpj[cnpj] = id_canonica
        chave_resumo = "organizacoes_criadas" if criada else "organizacoes_atualizadas"
        resumo[chave_resumo] = resumo.get(chave_resumo, 0) + 1
        _gravar_identificador(cur, id_canonica, id_inquerito, "cnpj", cnpj, tx_origem)
        for tp_origem, id_origem, detalhe in evidencias[:_MAX_EVIDENCIAS_POR_EMPRESA]:
            resumo["evidencias"] = resumo.get("evidencias", 0) + _gravar_evidencia(
                cur, id_canonica, tp_origem, id_origem, detalhe)
    return canonica_por_cnpj


# Ponta de uma aresta montada ANTES de os nós existirem. Três formas: ("perfil", _Perfil) para
# quem o _Indice já conhece (o id_canonica dele só nasce em _materializar), ("cnpj", str) para
# a empresa que _materializar_organizacoes ainda vai criar e ("no", id_canonica) para o nó já
# resolvido. _resolver_lado converte as três em id_canonica na hora de gravar a aresta.
def _lado_perfil(perfil) -> tuple | None:
    return ("perfil", perfil) if perfil is not None else None


def _lado_cnpj(cnpj: str) -> tuple:
    return ("cnpj", cnpj)


def _resolver_lado(lado, canonica_por_cnpj: dict[str, int]) -> int | None:
    if lado is None:
        return None
    tipo, valor = lado
    if tipo == "perfil":
        return valor.raiz().id_canonica
    if tipo == "cnpj":
        return canonica_por_cnpj.get(valor)
    return valor


def _registrar_ponta_documental(indice: _Indice, empresas: _Empresas, documento: str | None,
                                nome: str | None, origem: tuple[str, int], detalhe: str,
                                grupo: str, permitir_sem_documento: bool = False):
    """Registra a ponta de uma aresta pelo CPF/CNPJ que o documento traz: CPF vira perfil de
    PESSOA no índice (e funde com o que já se sabia dela), CNPJ vira EMPRESA.

    Sem documento devolve None — não dá para dizer com quem o vínculo é. A exceção é
    permitir_sem_documento, para a fonte em que nome sem CPF é a regra e não a falha (o
    dependente menor de idade no IRPF, o cliente da linha pré-paga): aí a ponta vale pelo nome
    completo, que é o identificador que _Indice.registrar deriva do próprio nome."""
    cpf = _cpf_de_documento(documento)
    if cpf:
        return _lado_perfil(indice.registrar(
            [("cpf", cpf)], id_extracao=None, nome=nome, prioridade=NOME_DOCUMENTAL,
            evidencia=origem, detalhe=detalhe[:300], grupo=grupo))
    cnpj = _cnpj_de_documento(documento)
    if cnpj:
        empresas.lembrar(cnpj, nome, origem, detalhe)
        return _lado_cnpj(cnpj)
    if permitir_sem_documento and _nome_completo(nome):
        return _lado_perfil(indice.registrar(
            [], id_extracao=None, nome=nome, prioridade=NOME_DOCUMENTAL,
            evidencia=origem, detalhe=detalhe[:300], grupo=grupo))
    return None


# ---------------------------------------------------------------------------
# IPEI — quebra de sigilo fiscal (Receita Federal)
# ---------------------------------------------------------------------------
# A IPEI é a única fonte automática que diz, COM DOCUMENTO dos dois lados, quem é sócio de quem,
# quem empregou quem, quem deu procuração a quem e — o mais forte — quais investigados
# transmitiram declaração de imposto do MESMO computador. Isso é exatamente o que a mesa precisa:
# não a lista de linhas da tabela, mas "fulano —relação→ entidade".
#
# Por isso esta fonte é a única que também cria nó de EMPRESA (tp_entidade 'organizacao'): metade
# dos vínculos da IPEI tem CNPJ de um lado, e sem o nó da empresa a aresta não teria onde chegar —
# a sociedade entre dois investigados na mesma empresa ficaria invisível, que é justamente o
# desenho que a investigação procura.

GRUPO_IPEI = "ipei"
_ROTULO_GRUPO[GRUPO_IPEI] = "quebra de sigilo fiscal (IPEI)"

# rótulo da aresta por tipo de vínculo. O sufixo "(IPEI)" não é enfeite: é ele que a mesa usa para
# colorir a aresta por natureza (ver NATUREZAS_RELACAO em web/js/mesa_inquerito.js), do mesmo jeito
# que "(SIMBA)" e "(RIF)" já fazem.
_RELACAO_IPEI = {
    "societario": "sociedade (IPEI)",
    "empregaticio": "vínculo empregatício (IPEI)",
    "procuracao": "procuração/escritura (IPEI)",
    "procuracao_rfb": "procuração perante a RFB (IPEI)",
    "mac_coincidente": "declaração do mesmo computador (IPEI)",
}
# quem transmitiu do mesmo MAC não tem "origem" e "destino": os dois estão no mesmo pé, e duas
# setas dizendo a mesma coisa só sujam o grafo
_RELACOES_IPEI_SIMETRICAS = {"mac_coincidente"}


# O ANEXO da IPEI (a planilha) traz mais três coisas com documento dos dois lados, e cada uma
# delas é um desenho que a tabela esconde: quem mais figura na CONTA BANCÁRIA do alvo, de quem
# ele COMPRA (e para quem vende) por NF-e, e qual VEÍCULO está no nome dele. Junto com os
# vínculos societários acima, é isso que transforma a IPEI em grafo.
_RELACAO_IPEI_CONTA = "cotitular de conta (IPEI)"
_RELACAO_IPEI_COMPRA = "comprou de (IPEI)"
_RELACAO_IPEI_VENDA = "vendeu para (IPEI)"
_RELACAO_IPEI_VEICULO = "proprietário de (IPEI)"

# Teto de contrapartes de NF-e por alvo. Um alvo com movimento de varejo tem centenas de
# emitentes (posto de gasolina, supermercado, farmácia) e despejá-los na mesa afogaria os
# vínculos que importam — as maiores são as que dizem alguma coisa. O corte é por VALOR
# movimentado, não por quantidade: dez notas de R$ 40 no posto dizem menos que uma de R$ 300 mil.
_MAX_CONTRAPARTES_NFE = 15


def _placa_de_veiculo(valor: str | None) -> str | None:
    """Placa normalizada (sem hífen/espaço, maiúscula) — 7 posições, serve para o formato
    antigo (AAA9999) e para o Mercosul (AAA9A99)."""
    placa = re.sub(r"[^A-Z0-9]", "", (valor or "").upper())
    return placa if len(placa) == 7 and any(c.isdigit() for c in placa) else None


def _coletar_ipei(cur, id_inquerito: int, indice: _Indice) -> dict:
    """Alvos, vínculos, contas, notas fiscais, veículos e contatos das IPEIs do inquérito.

    Devolve {"empresas", "pares", "veiculos"} — as empresas que vão virar nó em
    _materializar_ipei, as arestas já com as duas pontas resolvidas em `lado` (ver
    _resolver_lado) e os veículos que viram nó próprio. Pessoa física entra no índice aqui
    mesmo, como em RIF/SIMBA/IPL; empresa não, porque não é pessoa (ver _cpf_de_documento)."""
    empresas = _Empresas()
    pares: list[tuple] = []
    veiculos: list[tuple] = []
    lado_por_alvo: dict[int, tuple] = {}
    cpf_por_alvo: dict[int, str] = {}
    doc_por_alvo: dict[int, str] = {}
    nome_por_alvo: dict[int, str] = {}

    cur.execute(
        """
        SELECT a.id_alvo, a.no_alvo, a.nu_cpf_cnpj, a.tx_ocupacao, i.nu_ipei
        FROM investigacao.ipe_tb02_alvos a
        JOIN investigacao.ipe_tb01_ipeis i ON i.id_ipei = a.id_ipei
        WHERE i.id_inquerito = %s
        """,
        (id_inquerito,),
    )
    for id_alvo, nome, doc, ocupacao, nu_ipei in cur.fetchall():
        detalhe = f"alvo da quebra de sigilo fiscal (IPEI {nu_ipei or ''})".strip()
        if ocupacao:
            detalhe += f" — {ocupacao}"
        doc_por_alvo[id_alvo] = doc or ""
        nome_por_alvo[id_alvo] = nome or ""
        cpf = _cpf_de_documento(doc)
        if cpf:
            cpf_por_alvo[id_alvo] = cpf
        lado = _registrar_ponta_documental(
            indice, empresas, doc, nome, ("ipei_alvo", id_alvo), detalhe, GRUPO_IPEI)
        if lado is not None:
            lado_por_alvo[id_alvo] = lado

    cur.execute(
        """
        SELECT r.id_relacionado, r.tp_relacao, r.no_relacionado, r.nu_cpf_cnpj,
               r.tx_qualificacao, r.dt_inicio, r.dt_fim, r.id_alvo, i.nu_ipei
        FROM investigacao.ipe_tb03_relacionados r
        JOIN investigacao.ipe_tb01_ipeis i ON i.id_ipei = r.id_ipei
        WHERE i.id_inquerito = %s
        """,
        (id_inquerito,),
    )
    for (id_rel, tp_relacao, nome, doc, qualificacao,
         dt_inicio, dt_fim, id_alvo, nu_ipei) in cur.fetchall():
        rotulo = _RELACAO_IPEI.get(tp_relacao)
        if rotulo is None or id_alvo not in lado_por_alvo:
            continue
        detalhe = f"{qualificacao or rotulo} (IPEI {nu_ipei or ''})".strip()
        lado = _registrar_ponta_documental(
            indice, empresas, doc, nome, ("ipei_relacionado", id_rel), detalhe, GRUPO_IPEI)
        if lado is None:
            continue  # sem documento não dá para dizer com quem o vínculo é
        periodo = " ".join(p for p in (
            f"de {dt_inicio:%d/%m/%Y}" if dt_inicio else "",
            f"até {dt_fim:%d/%m/%Y}" if dt_fim else "") if p)
        pares.append((lado_por_alvo[id_alvo], lado, rotulo,
                      (f"{qualificacao} " if qualificacao else "") + periodo,
                      tp_relacao in _RELACOES_IPEI_SIMETRICAS))

    # ---- contatos do alvo em outras bases da RFB: telefone e e-mail declarados ----
    # Não viram nó nem aresta: viram IDENTIFICADOR do próprio alvo, junto com o CPF dele. É o
    # que faz o alvo da IPEI e o contato salvo no celular apreendido virarem UMA pessoa — a
    # fusão do _Indice acontece por identificador em comum, e até aqui a IPEI só trazia CPF.
    cur.execute(
        """
        SELECT c.id_contato, c.id_alvo, c.tp_contato, c.tx_valor, c.nu_ano
        FROM investigacao.ipe_tb09_contatos c
        JOIN investigacao.ipe_tb01_ipeis i ON i.id_ipei = c.id_ipei
        WHERE i.id_inquerito = %s AND c.tp_contato IN ('telefone', 'email')
        """,
        (id_inquerito,),
    )
    for id_contato, id_alvo, tp_contato, valor, ano in cur.fetchall():
        cpf = cpf_por_alvo.get(id_alvo)
        if not cpf:
            continue  # alvo pessoa jurídica: o telefone é da empresa, não de uma pessoa
        identificador = (("telefone", _telefone_br(valor)) if tp_contato == "telefone"
                         else ("email", _email(valor)))
        if not identificador[1]:
            continue
        indice.registrar(
            [("cpf", cpf), identificador], id_extracao=None,
            nome=nome_por_alvo.get(id_alvo), prioridade=NOME_DOCUMENTAL,
            evidencia=("ipei_contato", id_contato),
            detalhe=f"{tp_contato} do alvo em base da RFB{f' ({ano})' if ano else ''}"[:300],
            grupo=GRUPO_IPEI,
        )

    # ---- contas bancárias do anexo: quem MAIS figura na conta do alvo ----
    cur.execute(
        """
        SELECT c.id_conta, c.id_alvo, c.no_banco, c.nu_conta, c.no_titular, c.nu_titular,
               c.tx_relacao
        FROM investigacao.ipe_tb06_contas c
        JOIN investigacao.ipe_tb01_ipeis i ON i.id_ipei = c.id_ipei
        WHERE i.id_inquerito = %s AND c.nu_titular IS NOT NULL
        """,
        (id_inquerito,),
    )
    vistos_conta: set[tuple[int, str]] = set()
    for id_conta, id_alvo, banco, conta, no_titular, nu_titular, relacao in cur.fetchall():
        if id_alvo not in lado_por_alvo:
            continue
        digitos = _SO_DIGITOS.sub("", nu_titular or "")
        if not digitos or digitos == _SO_DIGITOS.sub("", doc_por_alvo.get(id_alvo) or ""):
            continue  # é o próprio alvo figurando na conta dele
        if (id_alvo, digitos) in vistos_conta:
            continue  # a mesma conta vem mês a mês; o vínculo é um só
        vistos_conta.add((id_alvo, digitos))
        onde = " ".join(p for p in (banco, conta and f"conta {conta}") if p) or "conta bancária"
        detalhe = f"{relacao or 'figura na conta'} — {onde} (e-Financeira/IPEI)"
        lado = _registrar_ponta_documental(
            indice, empresas, nu_titular, no_titular, ("ipei_conta", id_conta),
            detalhe, GRUPO_IPEI)
        if lado is None:
            continue
        pares.append((lado_por_alvo[id_alvo], lado, _RELACAO_IPEI_CONTA, detalhe, True))

    # ---- notas fiscais eletrônicas: de quem o alvo compra e para quem vende ----
    cur.execute(
        """
        SELECT n.id_nota, n.id_alvo, n.dt_emissao, n.no_emitente, n.nu_cnpj_emitente,
               n.no_destinatario, n.nu_cpf_destinatario, n.vl_total
        FROM investigacao.ipe_tb08_notas_fiscais n
        JOIN investigacao.ipe_tb01_ipeis i ON i.id_ipei = n.id_ipei
        WHERE i.id_inquerito = %s
        """,
        (id_inquerito,),
    )
    # A planilha de anexo identifica o emitente pela RAIZ do CNPJ (8 dígitos, "19111420"), não
    # pelo CNPJ inteiro — é assim que a RFB exporta. Raiz não identifica um estabelecimento, e
    # promovê-la a nó criaria uma empresa paralela à que os vínculos societários já trouxeram
    # inteira. Então ela só vale quando casa, SEM AMBIGUIDADE, com um CNPJ completo que a
    # própria IPEI levantou; fora disso não dá para dizer com qual empresa o vínculo é.
    por_raiz: dict[str, set[str]] = {}
    for cnpj_conhecido in empresas:
        por_raiz.setdefault(cnpj_conhecido[:8], set()).add(cnpj_conhecido)
    cnpj_por_raiz = {raiz: c.pop() for raiz, c in por_raiz.items() if len(c) == 1}

    def _documento_nfe(valor: str | None) -> str:
        digitos = _SO_DIGITOS.sub("", valor or "")
        return cnpj_por_raiz.get(digitos, "") if len(digitos) == 8 else digitos

    notas: dict[tuple[int, str, str], dict] = {}
    for (id_nota, id_alvo, dt_emissao, no_emitente, doc_emitente,
         no_destinatario, doc_destinatario, vl_total) in cur.fetchall():
        if id_alvo not in lado_por_alvo:
            continue
        doc_alvo = _SO_DIGITOS.sub("", doc_por_alvo.get(id_alvo) or "")
        alvo_e_emitente = bool(doc_alvo) and doc_alvo == _documento_nfe(doc_emitente)
        rotulo = _RELACAO_IPEI_VENDA if alvo_e_emitente else _RELACAO_IPEI_COMPRA
        nome_outro = no_destinatario if alvo_e_emitente else no_emitente
        digitos = (_SO_DIGITOS.sub("", doc_destinatario or "") if alvo_e_emitente
                   else _documento_nfe(doc_emitente))
        if not digitos or digitos == doc_alvo:
            continue
        doc_outro = digitos
        agregado = notas.setdefault(
            (id_alvo, rotulo, digitos),
            {"nome": nome_outro, "doc": doc_outro, "qtd": 0, "valor": 0.0,
             "primeira": None, "ultima": None, "ids": []},
        )
        agregado["nome"] = agregado["nome"] or nome_outro
        agregado["qtd"] += 1
        agregado["valor"] += float(vl_total or 0)
        if dt_emissao:
            if not agregado["primeira"] or dt_emissao < agregado["primeira"]:
                agregado["primeira"] = dt_emissao
            if not agregado["ultima"] or dt_emissao > agregado["ultima"]:
                agregado["ultima"] = dt_emissao
        if len(agregado["ids"]) < _MAX_EVIDENCIAS_POR_EMPRESA:
            agregado["ids"].append(id_nota)

    por_alvo: dict[int, list] = {}
    for (id_alvo, rotulo, _digitos), agregado in notas.items():
        por_alvo.setdefault(id_alvo, []).append((rotulo, agregado))
    for id_alvo, itens in por_alvo.items():
        itens.sort(key=lambda item: item[1]["valor"], reverse=True)
        for rotulo, agregado in itens[:_MAX_CONTRAPARTES_NFE]:
            periodo = ""
            if agregado["primeira"] and agregado["ultima"]:
                periodo = (f", entre {agregado['primeira']:%d/%m/%Y} e "
                           f"{agregado['ultima']:%d/%m/%Y}")
            detalhe = (f"{agregado['qtd']} nota(s) fiscal(is), "
                       f"{_valor_br(agregado['valor'])}{periodo}")
            lado = None
            for id_nota in agregado["ids"]:
                lado = _registrar_ponta_documental(
                    indice, empresas, agregado["doc"], agregado["nome"],
                    ("ipei_nota", id_nota), detalhe, GRUPO_IPEI)
            if lado is None:
                continue
            pares.append((lado_por_alvo[id_alvo], lado, rotulo, detalhe, False))

    # ---- veículos do RENAVAM: a placa é identificador, e o nó de veículo já existe na mesa ----
    cur.execute(
        """
        SELECT b.id_bem, b.id_alvo, b.nu_identificador, b.tx_descricao, b.tx_situacao, b.nu_ano
        FROM investigacao.ipe_tb07_bens b
        JOIN investigacao.ipe_tb01_ipeis i ON i.id_ipei = b.id_ipei
        WHERE i.id_inquerito = %s AND b.tp_bem LIKE 'veiculo%%'
          AND b.nu_identificador IS NOT NULL
        """,
        (id_inquerito,),
    )
    for id_bem, id_alvo, identificador, descricao, situacao, ano in cur.fetchall():
        placa = _placa_de_veiculo(identificador)
        if placa is None or id_alvo not in lado_por_alvo:
            continue
        observacao = " · ".join(p for p in (
            " ".join((descricao or "").split())[:300],
            f"Restrição: {situacao}" if situacao else "",
            f"Levantado no ano {ano}" if ano else "",
            "Veículo levantado na quebra de sigilo fiscal (IPEI)") if p)
        # a discriminação do bem vem do PDF com quebra de linha no meio; rótulo de nó não pode
        # ter quebra de linha, senão o texto do SVG sai cortado
        rotulo = " ".join((descricao or placa).split())[:150]
        veiculos.append((placa, rotulo, observacao,
                         lado_por_alvo[id_alvo], ("ipei_bem", id_bem)))

    return {"empresas": empresas, "pares": pares, "veiculos": veiculos}


def _materializar_ipei(cur, id_inquerito: int, dados: dict) -> dict:
    """Cria os nós de empresa e de veículo da IPEI e liga cada vínculo às duas pontas.

    Roda DEPOIS de _materializar, como os aparelhos e os locais: a aresta precisa da canônica
    da pessoa (perfil.id_canonica) já criada dos dois lados."""
    resumo = {"organizacoes_criadas": 0, "organizacoes_atualizadas": 0,
              "veiculos_criados": 0, "veiculos_atualizados": 0,
              "relacoes": 0, "evidencias": 0}
    canonica_por_cnpj = _materializar_organizacoes(
        cur, id_inquerito, dados["empresas"],
        "empresa levantada na quebra de sigilo fiscal (IPEI)",
        "quebra de sigilo fiscal (IPEI)", resumo)

    for placa, nome, observacao, lado_dono, (tp_origem, id_origem) in dados["veiculos"]:
        id_veiculo, criada = _upsert_canonica(
            cur, id_inquerito, "veiculo", f"{placa} — {nome}"[:200], f"placa:{placa}", observacao)
        if id_veiculo is None:
            continue
        resumo["veiculos_criados" if criada else "veiculos_atualizados"] += 1
        _gravar_identificador(cur, id_veiculo, id_inquerito, "placa", placa,
                              "quebra de sigilo fiscal (IPEI)")
        resumo["evidencias"] += _gravar_evidencia(
            cur, id_veiculo, tp_origem, id_origem, "veículo em nome do alvo (IPEI)")
        id_dono = _resolver_lado(lado_dono, canonica_por_cnpj)
        if id_dono:
            resumo["relacoes"] += _gravar_relacao(
                cur, id_inquerito, id_dono, id_veiculo, _RELACAO_IPEI_VEICULO,
                detalhe=(observacao or None) and observacao[:500])

    for lado_a, lado_b, rotulo, detalhe, simetrica in dados["pares"]:
        id_a = _resolver_lado(lado_a, canonica_por_cnpj)
        id_b = _resolver_lado(lado_b, canonica_por_cnpj)
        if id_a is None or id_b is None:
            continue
        resumo["relacoes"] += _gravar_relacao(
            cur, id_inquerito, id_a, id_b, rotulo, simetrica=simetrica,
            detalhe=(detalhe or None) and detalhe[:500])
    return resumo


# ---------------------------------------------------------------------------
# IRPF — declaração de imposto de renda da pessoa física (Receita Federal)
# ---------------------------------------------------------------------------
# A declaração é a fonte mais barata de vínculo COM DOCUMENTO que existe no inquérito: o
# declarante diz o CPF dos dependentes, o CNPJ de quem lhe pagou e o de quem ele doou. Bens e
# dívidas ficam de fora de propósito — ali a contraparte é texto livre ("apartamento na rua
# X"), e inventar nó a partir de discriminação de bem encheria a mesa de coisa que não se
# consegue afirmar. O que a declaração tem de identificado vira aresta; o resto continua sendo
# lido na tela do IRPF.

GRUPO_IRPF = "irpf"
_ROTULO_GRUPO[GRUPO_IRPF] = "declaração de IRPF"

_RELACAO_IRPF_DEPENDENTE = "dependente (IRPF)"
_RELACAO_IRPF_RENDIMENTO = "recebeu de (IRPF)"
_RELACAO_IRPF_DOACAO = "doou a (IRPF)"

# Teto de fontes pagadoras e beneficiários por declaração. Uma declaração normal tem meia
# dúzia; o teto existe para o caso patológico (declaração de quem recebeu de cem fontes) não
# virar cem nós de uma vez. Corta pelo maior valor, que é o que diz alguma coisa.
_MAX_CONTRAPARTES_IRPF = 20


def _coletar_irpf(cur, id_inquerito: int, indice: _Indice) -> dict:
    """Declarante, dependentes, fontes pagadoras e beneficiários de doação das declarações de
    IRPF do inquérito. Devolve {"empresas", "pares"}, no mesmo formato da IPEI.

    O declarante entra com CPF, telefone, celular e e-mail DECLARADOS — e é justamente isso
    que o torna útil aqui: até então o telefone dele só existia se aparecesse em algum
    aparelho, e é o telefone que funde o contribuinte com o contato salvo no celular."""
    empresas = _Empresas()
    pares: list[tuple] = []

    cur.execute(
        """
        SELECT i.id_irpf, i.no_titular, i.nu_cpf, i.nu_telefone, i.nu_celular, i.no_email,
               i.tx_ocupacao_principal, i.nu_exercicio
        FROM investigacao.irp_tb01_declaracoes i
        WHERE i.id_inquerito = %s
        """,
        (id_inquerito,),
    )
    lado_por_irpf: dict[int, tuple] = {}
    exercicio_por_irpf: dict[int, int] = {}
    for id_irpf, nome, doc, telefone, celular, email, ocupacao, exercicio in cur.fetchall():
        cpf = _cpf_de_documento(doc)
        if not cpf:
            continue  # declaração sem CPF legível no cabeçalho: não dá para dizer de quem é
        exercicio_por_irpf[id_irpf] = exercicio
        identificadores = [("cpf", cpf)]
        for bruto in (telefone, celular):
            numero = _telefone_br(bruto)
            if numero:
                identificadores.append(("telefone", numero))
        endereco_email = _email(email)
        if endereco_email:
            identificadores.append(("email", endereco_email))
        detalhe = f"declarante do IRPF{f' (exercício {exercicio})' if exercicio else ''}"
        if ocupacao:
            detalhe += f" — {ocupacao}"
        perfil = indice.registrar(
            identificadores, id_extracao=None, nome=nome, prioridade=NOME_DOCUMENTAL,
            evidencia=("irpf_declaracao", id_irpf), detalhe=detalhe[:300], grupo=GRUPO_IRPF)
        lado = _lado_perfil(perfil)
        if lado is not None:
            lado_por_irpf[id_irpf] = lado

    cur.execute(
        """
        SELECT d.id_dependente, d.id_irpf, d.no_dependente, d.nu_cpf, d.tx_relacao
        FROM investigacao.irp_tb03_dependentes d
        JOIN investigacao.irp_tb01_declaracoes i ON i.id_irpf = d.id_irpf
        WHERE i.id_inquerito = %s
        """,
        (id_inquerito,),
    )
    for id_dependente, id_irpf, nome, doc, relacao in cur.fetchall():
        if id_irpf not in lado_por_irpf:
            continue
        detalhe = f"{relacao or 'dependente'} declarado(a) no IRPF"
        # dependente menor costuma vir sem CPF: aí a ponta vale pelo nome completo, como no IPL
        lado = _registrar_ponta_documental(
            indice, empresas, doc, nome, ("irpf_dependente", id_dependente), detalhe,
            GRUPO_IRPF, permitir_sem_documento=True)
        if lado is None:
            continue
        pares.append((lado_por_irpf[id_irpf], lado, _RELACAO_IRPF_DEPENDENTE, detalhe, False))

    # Fonte pagadora e beneficiário de doação: mesma forma (documento + valor), rótulos
    # opostos. A direção é sempre do ponto de vista do declarante, como em SIMBA/RIF.
    for tabela, campo_id, campo_nome, campo_doc, campo_valor, rotulo, extra in (
        ("irp_tb04_rendimentos", "id_rendimento", "no_fonte_pagadora", "nu_cnpj_cpf",
         "vl_rendimento", _RELACAO_IRPF_RENDIMENTO, "tp_origem"),
        ("irp_tb06_doacoes", "id_doacao", "no_beneficiario", "nu_cnpj_cpf",
         "vl_doacao", _RELACAO_IRPF_DOACAO, "tp_doacao"),
    ):
        cur.execute(
            f"""
            SELECT t.{campo_id}, t.id_irpf, t.{campo_nome}, t.{campo_doc}, t.{campo_valor},
                   t.{extra}
            FROM investigacao.{tabela} t
            JOIN investigacao.irp_tb01_declaracoes i ON i.id_irpf = t.id_irpf
            WHERE i.id_inquerito = %s AND t.{campo_doc} IS NOT NULL
            """,
            (id_inquerito,),
        )
        agregados: dict[tuple[int, str], dict] = {}
        for id_linha, id_irpf, nome, doc, valor, qualificador in cur.fetchall():
            if id_irpf not in lado_por_irpf:
                continue
            digitos = _SO_DIGITOS.sub("", doc or "")
            if not digitos:
                continue
            agregado = agregados.setdefault(
                (id_irpf, digitos),
                {"nome": nome, "doc": doc, "valor": 0.0, "qtd": 0,
                 "qualificadores": set(), "ids": []},
            )
            agregado["nome"] = agregado["nome"] or nome
            agregado["valor"] += float(valor or 0)
            agregado["qtd"] += 1
            if qualificador:
                agregado["qualificadores"].add(str(qualificador))
            if len(agregado["ids"]) < _MAX_EVIDENCIAS_POR_EMPRESA:
                agregado["ids"].append(id_linha)

        por_declaracao: dict[int, list] = {}
        for (id_irpf, _digitos), agregado in agregados.items():
            por_declaracao.setdefault(id_irpf, []).append(agregado)
        for id_irpf, itens in por_declaracao.items():
            itens.sort(key=lambda item: item["valor"], reverse=True)
            exercicio = exercicio_por_irpf.get(id_irpf)
            for agregado in itens[:_MAX_CONTRAPARTES_IRPF]:
                detalhe = _valor_br(agregado["valor"])
                if agregado["qtd"] > 1:
                    detalhe += f" em {agregado['qtd']} lançamento(s)"
                if agregado["qualificadores"]:
                    detalhe += f" ({', '.join(sorted(agregado['qualificadores']))})"
                if exercicio:
                    detalhe += f" — IRPF exercício {exercicio}"
                lado = None
                for id_linha in agregado["ids"]:
                    tp_origem = ("irpf_rendimento" if rotulo == _RELACAO_IRPF_RENDIMENTO
                                 else "irpf_doacao")
                    lado = _registrar_ponta_documental(
                        indice, empresas, agregado["doc"], agregado["nome"],
                        (tp_origem, id_linha), detalhe, GRUPO_IRPF)
                if lado is None:
                    continue
                pares.append((lado_por_irpf[id_irpf], lado, rotulo, detalhe, False))

    return {"empresas": empresas, "pares": pares}


def _materializar_irpf(cur, id_inquerito: int, dados: dict) -> dict:
    """Cria os nós de empresa do IRPF e liga cada vínculo às duas pontas já canônicas."""
    resumo = {"organizacoes_criadas": 0, "organizacoes_atualizadas": 0,
              "relacoes": 0, "evidencias": 0}
    canonica_por_cnpj = _materializar_organizacoes(
        cur, id_inquerito, dados["empresas"], "empresa citada na declaração de IRPF",
        "declaração de IRPF", resumo)
    for lado_a, lado_b, rotulo, detalhe, simetrica in dados["pares"]:
        id_a = _resolver_lado(lado_a, canonica_por_cnpj)
        id_b = _resolver_lado(lado_b, canonica_por_cnpj)
        if id_a is None or id_b is None:
            continue
        resumo["relacoes"] += _gravar_relacao(
            cur, id_inquerito, id_a, id_b, rotulo, simetrica=simetrica,
            detalhe=(detalhe or None) and detalhe[:500])
    return resumo


# ---------------------------------------------------------------------------
# Telefonia — o retorno da operadora na quebra de sigilo telefônico
# ---------------------------------------------------------------------------
# É a única fonte do sistema que cria nó de TELEFONE, e é onde esse tipo de nó sempre fez
# sentido: no CDR o vértice natural é a linha, não a pessoa. O número de quem ligou não tem
# nome — tem volume de chamada, IMEI e horário —, e forçá-lo a virar "pessoa" seria afirmar
# uma identidade que a operadora não deu. Então a linha vira nó próprio, o titular do cadastro
# se liga a ela, o aparelho (IMEI) se liga a ela, e o CDR vira aresta entre linhas.
#
# A ponte com o resto da mesa é o CADASTRO: ali a operadora dá nome + CPF do cliente, e esse
# perfil entra no _Indice com o telefone junto — é o que funde o titular da linha com o
# contato salvo no celular apreendido, com o alvo da IPEI e com o envolvido do RIF.

GRUPO_TELEFONIA = "telefonia"
_ROTULO_GRUPO[GRUPO_TELEFONIA] = "quebra de sigilo telefônico"

_RELACAO_TELEFONIA_TITULAR = "titular da linha (telefonia)"
_RELACAO_TELEFONIA_USA = "usa a linha (telefonia)"
_RELACAO_TELEFONIA_LIGOU = "ligou para (telefonia)"
_RELACAO_TELEFONIA_APARELHO = "linha usada no aparelho (telefonia)"

# Quantas contrapartes de CDR cada linha investigada traz para a mesa, e o piso de chamadas
# para uma delas contar. O CDR de um ano tem milhares de números distintos: sem corte, a mesa
# vira a lista telefônica do investigado e some o que interessa — é o mesmo raciocínio do
# _TOP_CONVERSAS_WPP, e pelo mesmo motivo o corte é por VOLUME, que aqui é evidência por si só.
_TOP_CONTRAPARTES_TELEFONIA = 8
_MIN_CHAMADAS_TELEFONIA = 3
# Quantos aparelhos por linha. Chip que roda em dez aparelhos existe, mas o décimo já não
# ajuda a ver nada — e o IMEI zerado, que seria o pior deles, já entra nulo na ingestão.
_MAX_IMEIS_POR_LINHA = 5


def _imei_util(valor: str | None) -> str | None:
    """IMEI reduzido a dígitos. Mesma normalização de _coletar_info_aparelho, de propósito: é
    ela que faz o IMEI do ofício da operadora e o do aparelho apreendido caírem na MESMA chave
    de nó ('imei:...') — e então "a linha rodou neste aparelho" e "fulano é dono deste
    aparelho" apontam para o mesmo dispositivo, que é o vínculo que se quer enxergar."""
    digitos = _SO_DIGITOS.sub("", valor or "")
    return digitos[:300] if _identificador_util(digitos) else None


def _coletar_telefonia(cur, id_inquerito: int, indice: _Indice) -> dict:
    """Linhas cadastradas, aparelhos e registro de chamadas dos cards de telefonia do
    inquérito. Devolve {"empresas", "linhas", "imeis", "chamadas"}."""
    empresas = _Empresas()
    linhas: dict[str, dict] = {}
    imeis: dict[str, dict[str, int]] = {}

    def _linha(numero: str) -> dict:
        return linhas.setdefault(numero, {
            "nome": None, "lado": None, "operadoras": set(), "fichas": set(),
            "evidencias": [], "cadastro": False,
        })

    # ---- 1 · ficha cadastral: é aqui que a linha ganha dono ----
    cur.execute(
        """
        SELECT l.id_linha_telefonia, l.nu_linha, l.no_cliente, l.nu_cpf_cnpj, l.tp_modalidade,
               l.tp_situacao, l.no_municipio, l.sg_uf, t.no_operadora
        FROM investigacao.tel_tb03_linhas l
        JOIN investigacao.tel_tb01_telefonia t ON t.id_telefonia = l.id_telefonia
        WHERE t.id_inquerito = %s
        ORDER BY l.id_linha_telefonia
        """,
        (id_inquerito,),
    )
    for (id_linha, nu_linha, cliente, doc, modalidade, situacao,
         municipio, uf, operadora) in cur.fetchall():
        numero = _telefone_br(nu_linha)
        if not numero:
            continue
        info = _linha(numero)
        info["cadastro"] = True
        if operadora:
            info["operadoras"].add(operadora)
        ficha = " ".join(p for p in (modalidade, situacao) if p)
        if ficha:
            info["fichas"].add(ficha)
        local = ", ".join(p for p in (municipio, uf) if p)
        if local:
            info["fichas"].add(local)
        detalhe = (f"titular da linha {_telefone_formatado(numero)}"
                   f"{f' na {operadora}' if operadora else ''} (quebra de sigilo telefônico)")
        info["evidencias"].append(("telefonia_linha", id_linha, detalhe))
        info["nome"] = info["nome"] or cliente

        cnpj = _cnpj_de_documento(doc)
        if cnpj:
            empresas.lembrar(cnpj, cliente, ("telefonia_linha", id_linha), detalhe)
            lado = _lado_cnpj(cnpj)
        else:
            # o TELEFONE entra no perfil junto com o CPF: é ele que funde o titular do cadastro
            # com o contato salvo no aparelho apreendido, que é o ganho todo desta fonte
            cpf = _cpf_de_documento(doc)
            identificadores = [("telefone", numero)] + ([("cpf", cpf)] if cpf else [])
            perfil = indice.registrar(
                identificadores, id_extracao=None, nome=cliente, prioridade=NOME_DOCUMENTAL,
                evidencia=("telefonia_linha", id_linha), detalhe=detalhe[:300],
                grupo=GRUPO_TELEFONIA)
            if perfil is not None:
                # é o ofício declarando a linha no nome dela, não um telefone que apareceu
                # numa agenda qualquer — ver _Perfil.telefones_documentais e _identificada
                perfil.raiz().telefones_documentais.add(numero)
            lado = _lado_perfil(perfil)
        info["lado"] = info["lado"] or lado

    # ---- 2 · a linha que o ofício nomeia, mesmo sem ficha cadastral junto ----
    cur.execute(
        """
        SELECT a.nu_linha_alvo, t.no_operadora
        FROM investigacao.tel_tb02_arquivos a
        JOIN investigacao.tel_tb01_telefonia t ON t.id_telefonia = a.id_telefonia
        WHERE t.id_inquerito = %s AND a.nu_linha_alvo IS NOT NULL
        """,
        (id_inquerito,),
    )
    for nu_linha_alvo, operadora in cur.fetchall():
        numero = _telefone_br(nu_linha_alvo)
        if not numero:
            continue
        info = _linha(numero)
        if operadora:
            info["operadoras"].add(operadora)
        info["fichas"].add("Linha alvo do ofício")

    # ---- 3 · aparelho por linha (o resumo número + IMEI do fim do ofício) ----
    cur.execute(
        """
        SELECT n.id_numero_telefonia, n.nu_telefone, n.nu_imei
        FROM investigacao.tel_tb06_numeros n
        JOIN investigacao.tel_tb01_telefonia t ON t.id_telefonia = n.id_telefonia
        WHERE t.id_inquerito = %s AND n.nu_imei IS NOT NULL
        ORDER BY n.id_numero_telefonia
        """,
        (id_inquerito,),
    )
    for id_numero, nu_telefone, nu_imei in cur.fetchall():
        numero, imei = _telefone_br(nu_telefone), _imei_util(nu_imei)
        if not numero or not imei:
            continue
        imeis.setdefault(numero, {}).setdefault(imei, id_numero)

    # ---- 4 · o CDR, agregado por par de linhas ----
    # O corte de volume vai no HAVING, e não em Python: é ele que impede a consulta de trazer
    # a cauda longa (milhares de números com uma chamada só) para dentro do processo.
    cur.execute(
        """
        SELECT c.nu_chamador, c.nu_chamado, count(*) AS qtd,
               sum(COALESCE(c.qt_duracao_segundos, 0)) AS segundos,
               min(c.dt_chamada) AS dt_min, max(c.dt_chamada) AS dt_max
        FROM investigacao.tel_tb04_chamadas c
        JOIN investigacao.tel_tb01_telefonia t ON t.id_telefonia = c.id_telefonia
        WHERE t.id_inquerito = %s
          AND c.nu_chamador IS NOT NULL AND c.nu_chamado IS NOT NULL
        GROUP BY c.nu_chamador, c.nu_chamado
        HAVING count(*) >= %s
        """,
        (id_inquerito, _MIN_CHAMADAS_TELEFONIA),
    )
    agregados: dict[tuple[str, str], dict] = {}
    for chamador, chamado, qtd, segundos, dt_min, dt_max in cur.fetchall():
        a, b = _telefone_br(chamador), _telefone_br(chamado)
        if not a or not b or a == b:
            continue
        # ida e volta viram UMA aresta: duas setas dizendo "estes dois se falam" só sujam o
        # grafo, e o que interessa (quanto se falaram) é a soma das duas
        agregado = agregados.setdefault(
            tuple(sorted((a, b))),
            {"qtd": 0, "segundos": 0, "primeira": None, "ultima": None},
        )
        agregado["qtd"] += int(qtd or 0)
        agregado["segundos"] += int(segundos or 0)
        if dt_min and (not agregado["primeira"] or dt_min < agregado["primeira"]):
            agregado["primeira"] = dt_min
        if dt_max and (not agregado["ultima"] or dt_max > agregado["ultima"]):
            agregado["ultima"] = dt_max

    por_linha: dict[str, list] = {}
    for (a, b), agregado in agregados.items():
        for conhecida, outra in ((a, b), (b, a)):
            if conhecida in linhas:
                por_linha.setdefault(conhecida, []).append((outra, agregado))

    chamadas: list[tuple] = []
    vistos: set[tuple[str, str]] = set()
    for conhecida, itens in sorted(por_linha.items()):
        itens.sort(key=lambda item: item[1]["qtd"], reverse=True)
        for outra, agregado in itens[:_TOP_CONTRAPARTES_TELEFONIA]:
            par = tuple(sorted((conhecida, outra)))
            if par in vistos:
                continue
            vistos.add(par)
            chamadas.append((conhecida, outra, agregado))

    return {"empresas": empresas, "linhas": linhas, "imeis": imeis, "chamadas": chamadas}


def _materializar_telefonia(cur, id_inquerito: int, indice: _Indice, dados: dict) -> dict:
    """Cria os nós de telefone, de empresa titular e de aparelho, e liga tudo.

    Roda DEPOIS de _materializar, como os aparelhos e os locais: a aresta com o titular precisa
    da canônica da pessoa (perfil.id_canonica) já criada."""
    resumo = {"telefones_criados": 0, "telefones_atualizados": 0,
              "organizacoes_criadas": 0, "organizacoes_atualizadas": 0,
              "dispositivos_criados": 0, "dispositivos_atualizados": 0,
              "relacoes": 0, "evidencias": 0}
    canonica_por_cnpj = _materializar_organizacoes(
        cur, id_inquerito, dados["empresas"],
        "empresa titular de linha na quebra de sigilo telefônico",
        "quebra de sigilo telefônico", resumo)

    linhas = dados["linhas"]
    no_por_telefone: dict[str, int] = {}

    def _no_telefone(numero: str, observacao: str) -> int | None:
        """Nó da linha, criado uma vez por número (a mesma linha é contraparte de várias)."""
        if numero in no_por_telefone:
            return no_por_telefone[numero]
        id_no, criada = _upsert_canonica(
            cur, id_inquerito, "telefone", _telefone_formatado(numero), f"tel:{numero}",
            observacao)
        if id_no is None:
            return None
        no_por_telefone[numero] = id_no
        resumo["telefones_criados" if criada else "telefones_atualizados"] += 1
        _gravar_identificador(cur, id_no, id_inquerito, "telefone", numero,
                              "quebra de sigilo telefônico")
        return id_no

    # ---- linhas do cadastro/ofício e o titular de cada uma ----
    titular_do_no: dict[int, int] = {}
    for numero, info in sorted(linhas.items()):
        partes = []
        if info["nome"]:
            partes.append("Titular: " + info["nome"])
        operadoras = sorted(o for o in info["operadoras"] if o)
        if operadoras:
            partes.append("Operadora(s): " + ", ".join(operadoras))
        partes.extend(sorted(info["fichas"])[:4])
        partes.append("Linha levantada na quebra de sigilo telefônico")
        id_no = _no_telefone(numero, " · ".join(partes))
        if id_no is None:
            continue
        for tp_origem, id_origem, detalhe in info["evidencias"][:_MAX_EVIDENCIAS_POR_EMPRESA]:
            resumo["evidencias"] += _gravar_evidencia(cur, id_no, tp_origem, id_origem, detalhe)
        id_titular = _resolver_lado(info["lado"], canonica_por_cnpj)
        if id_titular:
            titular_do_no[id_no] = id_titular
            resumo["relacoes"] += _gravar_relacao(
                cur, id_inquerito, id_titular, id_no, _RELACAO_TELEFONIA_TITULAR)

    # ---- o CDR: aresta entre linhas, com quanto se falaram ----
    for numero_a, numero_b, agregado in dados["chamadas"]:
        # numero_a é sempre linha conhecida (o laço acima já criou o nó dela); numero_b pode
        # ser contraparte nova — _no_telefone devolve o nó existente quando já há um, então a
        # observação de contraparte não sobrescreve a da linha cadastrada.
        id_a = no_por_telefone.get(numero_a)
        id_b = _no_telefone(
            numero_b, "Contraparte no registro de chamadas da quebra de sigilo telefônico")
        if not id_a or not id_b:
            continue
        detalhe = f"{agregado['qtd']} chamada(s)"
        # em segundos abaixo de um minuto: "0 min no total" é pior que não dizer nada, e
        # chamada curta (tentativa, secretária eletrônica) é exatamente o que aparece aqui
        minutos = agregado["segundos"] // 60
        if minutos:
            detalhe += f", {minutos} min no total"
        elif agregado["segundos"]:
            detalhe += f", {agregado['segundos']} s no total"
        if agregado["primeira"] and agregado["ultima"]:
            detalhe += (f", entre {agregado['primeira']:%d/%m/%Y} e "
                        f"{agregado['ultima']:%d/%m/%Y}")
        resumo["relacoes"] += _gravar_relacao(
            cur, id_inquerito, id_a, id_b, _RELACAO_TELEFONIA_LIGOU,
            simetrica=True, detalhe=detalhe[:500])

    # ---- quem o resto do inquérito diz que usa cada linha ----
    # Vale para a contraparte do CDR que o UFDR, o RIF ou a IPEI já identificaram: o número
    # entra sem nome no ofício da operadora e ganha dono aqui. Não repete a aresta quando o
    # dono já é o titular do cadastro daquela mesma linha.
    for numero, id_no in sorted(no_por_telefone.items()):
        perfil = indice.perfil_do_identificador(("telefone", numero))
        id_pessoa = perfil.id_canonica if perfil is not None else None
        if not id_pessoa or titular_do_no.get(id_no) == id_pessoa:
            continue
        resumo["relacoes"] += _gravar_relacao(
            cur, id_inquerito, id_pessoa, id_no, _RELACAO_TELEFONIA_USA)

    # ---- aparelho em que a linha rodou ----
    for numero, id_no in sorted(no_por_telefone.items()):
        for imei, id_numero in sorted(dados["imeis"].get(numero, {}).items())[:_MAX_IMEIS_POR_LINHA]:
            id_aparelho, criada = _upsert_canonica(
                cur, id_inquerito, "dispositivo", f"IMEI {imei}", f"imei:{imei}",
                "Aparelho informado pela operadora na quebra de sigilo telefônico",
                atualizar_nome=False)
            if id_aparelho is None:
                continue
            resumo["dispositivos_criados" if criada else "dispositivos_atualizados"] += 1
            _gravar_identificador(cur, id_aparelho, id_inquerito, "imei", imei,
                                  "quebra de sigilo telefônico")
            resumo["evidencias"] += _gravar_evidencia(
                cur, id_aparelho, "telefonia_numero", id_numero,
                f"linha {_telefone_formatado(numero)} vista neste aparelho")
            resumo["relacoes"] += _gravar_relacao(
                cur, id_inquerito, id_no, id_aparelho, _RELACAO_TELEFONIA_APARELHO)
    return resumo


def _nomes_da_base_por_cpf(cur, cpfs: list[str]) -> dict[str, str]:
    """Nome na base de referência para cada CPF, só quando é inequívoco: o dataset
    tem CPF repetido, e CPF com duas grafias de nome não serve para nomear ninguém."""
    if not cpfs:
        return {}
    cur.execute(
        """
        SELECT nu_cpf, min(no_pessoa), count(DISTINCT no_pessoa)
        FROM dados_extraidos.pes_tb01_pessoas
        WHERE nu_cpf = ANY(%s)
        GROUP BY nu_cpf
        """,
        (cpfs,),
    )
    return {cpf.strip(): nome for cpf, nome, qt_nomes in cur.fetchall() if qt_nomes == 1 and nome}


def _coletar_chaves_pix(cur, id_extracao: int, indice: _Indice):
    """Chaves PIX citadas nas conversas. A chave é de uma pessoa (CPF, telefone ou
    e-mail) que NÃO é necessariamente quem mandou a mensagem — então a chave vira
    perfil próprio e a ligação com o remetente fica registrada como relação
    "chave PIX citada por". Quando os dois são a mesma pessoa (mesmo telefone, ou o
    nome do CPF na base bate com o nome do contato), a fusão por identificador já
    junta os dois sozinha e a relação some.

    Devolve (pares, nomes_base): os pares (perfil da chave, quem citou) para virarem
    relação depois que os nós existirem, e os nomes que a base de referência deu a
    cada CPF. "Quem citou" é a origem do contato remetente ou, quando foi o próprio
    dono que mandou a chave, o número da extração — que resolve o dono DAQUELE
    aparelho na hora de gravar a relação."""
    cur.execute(
        """
        SELECT min(k.id_mensagem), k.no_fonte, k.tp_chave, k.co_chave, k.id_contato_remetente,
               max(k.no_remetente), bool_or(coalesce(k.in_remetente_dono, false)),
               max(k.no_conversa), count(*)
        FROM investigacao.pix_tb01_chaves k
        WHERE k.id_extracao = %s AND k.tp_chave IN ('cpf', 'telefone', 'email')
        GROUP BY k.no_fonte, k.tp_chave, k.co_chave, k.id_contato_remetente
        """,
        (id_extracao,),
    )
    linhas = cur.fetchall()
    nomes_base = _nomes_da_base_por_cpf(cur, sorted({l[3] for l in linhas if l[2] == "cpf"}))

    pares: list[tuple[_Perfil, tuple[str, int] | None, int]] = []
    for id_mensagem, fonte, tp_chave, chave, id_remetente, no_remetente, dono_remetente, no_conversa, qt in linhas:
        tp_origem = _TP_ORIGEM_MENSAGEM.get(fonte)
        if tp_origem is None:
            continue
        nome = nomes_base.get(chave) if tp_chave == "cpf" else None
        onde = f"conversa com {no_conversa}" if no_conversa else "conversa"
        detalhe = (
            f"chave PIX ({tp_chave}) {chave} citada na {onde}"
            + (f" por {no_remetente}" if no_remetente else "")
            + (f" — {qt} menção(ões)" if qt > 1 else "")
        )
        perfil = indice.registrar(
            [(tp_chave, chave)], id_extracao=id_extracao, nome=nome, prioridade=NOME_BASE_REFERENCIA,
            evidencia=(tp_origem, id_mensagem), detalhe=detalhe[:300], grupo=GRUPO_FINANCEIRO,
        )
        if perfil is None:
            continue
        origem_remetente = None
        if id_remetente is not None and fonte in _TP_ORIGEM_CONTATO:
            origem_remetente = (_TP_ORIGEM_CONTATO[fonte], id_remetente)
        pares.append((perfil, origem_remetente, id_extracao))
    return pares, nomes_base


# Uma mensagem com mais cartões que isto é despejo de lista de contatos, e não "compartilhou o
# contato de alguém": nas oito extrações da base há mensagens com 244, 71 e 48 cartões. Os
# cartões continuam identificando gente normalmente (cada um é um nome com um número); o que o
# limite corta é a RELAÇÃO com quem enviou — 244 arestas saindo do mesmo nó cegam o grafo e não
# dizem mais do que "esta pessoa mandou a agenda inteira de uma vez".
_MAX_CARTOES_PARA_RELACAO = 8


def _cartoes_compartilhados(texto: str | None) -> list[tuple[str | None, list]]:
    """Desmonta o texto de TX_MENSAGEM de volta nos cartões de contato compartilhado.

    Devolve [(nome, [(categoria, valor), ...]), ...]. O formato é o que
    ufdr_parser._texto_contatos_compartilhados escreveu: o prefixo, o nome do contato na mesma
    linha e, nas seguintes, "<Categoria>: <valor>". Vários cartões na mesma mensagem recomeçam
    no prefixo. A primeira linha de cada bloco é SEMPRE o nome (pode vir vazia)."""
    if not texto or PREFIXO_CONTATO_COMPARTILHADO not in texto:
        return []
    cartoes = []
    for bloco in texto.split(PREFIXO_CONTATO_COMPARTILHADO)[1:]:
        linhas = bloco.split("\n")
        entradas = []
        for linha in linhas[1:]:
            linha = linha.strip()
            if not linha:
                continue
            categoria, separador, valor = linha.partition(": ")
            entradas.append((categoria, valor) if separador else (None, linha))
        cartoes.append((linhas[0].strip() or None, entradas))
    return cartoes


def _identificadores_de_cartao(entradas) -> list[tuple[str, str]]:
    """Identificadores de UM cartão. Ao virar texto, a entrada do vCard perdeu o tipo do modelo
    (PhoneNumber/EmailAddress/UserID), então o valor é lido pelo que ele é:

    - tem "@": é JID de mensageiro, e JID é TELEFONE (ver _telefone_de_jid), ou é e-mail de
      verdade — os dois normalizadores sabem distinguir um do outro;
    - categoria com a palavra "id" ("Facebook Id"): é conta de aplicativo e NUNCA telefone. O id
      do Facebook é numérico, e tratá-lo como número funde gente que nada tem a ver — a mesma
      armadilha do NU_IDENTIFICADOR em _coletar_contatos_conversa. O serviço sai da categoria sem
      a palavra "id" ("Facebook Id" → "facebook") justamente para casar com a chave que
      _coletar_contatos_conversa monta do TP_APP; sem isso cartão e contato do app não fundem;
    - resto: telefone, no formato BR estrito. As categorias vêm no idioma do aparelho (CELL,
      Celular, Telemóvel, HOME, Casa, WORK, 手机…), então listar as que são telefone não escala —
      quem filtra é _telefone_br, e é ele que descarta id numérico solto."""
    identificadores = []
    for categoria, valor in entradas:
        if "@" in valor:
            identificadores.append(("telefone", _telefone_de_jid(valor)))
            identificadores.append(("email", _email(valor)))
        elif "id" in _sem_acento(categoria or "").lower().split():
            servico = " ".join(p for p in (categoria or "").split()
                               if _sem_acento(p).lower() != "id")
            identificadores.append(("usuario_app", _usuario_app(servico, valor)))
        else:
            identificadores.append(("telefone", _telefone_br(valor)))
    return [i for i in identificadores if i[1]]


def _coletar_contatos_compartilhados(cur, id_extracao: int, indice: _Indice) -> list[tuple]:
    """Cartões de contato compartilhados DENTRO das conversas — o "compartilhar contato" do
    WhatsApp/Messenger, que grava nome, telefone(s), e-mail e JID do MESMO registro.

    É a única fonte do UFDR que amarra JID a telefone a nome num só lugar, e por isso vale como
    entrada de identidade mesmo não promovendo ninguém a nó (ver _GRUPOS_QUE_NAO_PROMOVEM): dá
    nome a quem estava salvo só como número e funde perfis que estavam separados. Onde o telefone
    do JID e o telefone listado DIVERGEM (117 cartões no inquérito 8) o cartão amarra dois números
    da mesma pessoa — WhatsApp num, fixo ou linha antiga no outro — que nenhuma outra fonte liga.

    A pessoa do cartão NÃO é quem mandou a mensagem: é um terceiro cujo contato passou de mão em
    mão. Como no PIX (_coletar_chaves_pix), a ligação com quem enviou fica como RELAÇÃO, e a fusão
    por identificador junta os dois sozinha quando forem a mesma pessoa.

    Devolve os pares (perfil do cartão, origem de quem enviou, id_extracao) para virarem relação
    depois que os nós existirem."""
    pares: list[tuple] = []
    for prefixo in ("wpp", "sms", "msg"):
        tp_origem = _TP_ORIGEM_MENSAGEM[prefixo]
        cur.execute(
            f"""
            SELECT m.id_mensagem, m.tx_mensagem, m.id_contato_remetente,
                   coalesce(m.in_enviada, false), c.no_grupo
            FROM investigacao.{prefixo}_tb04_mensagens m
            JOIN investigacao.{prefixo}_tb02_conversas c ON c.id_conversa = m.id_conversa
            WHERE c.id_extracao = %s AND m.tx_mensagem LIKE %s
            ORDER BY m.id_mensagem
            """,
            (id_extracao, PREFIXO_CONTATO_COMPARTILHADO + "%"),
        )
        # Agrupa por (pessoa do cartão, quem enviou): a MESMA pessoa é compartilhada dezenas de
        # vezes (21x a mais repetida no inquérito 8), e uma evidência por mensagem encheria o
        # modal do nó sem dizer nada de novo. Guarda a 1ª mensagem e conta as demais — igual ao
        # PIX. Fica dentro do laço de família porque id_mensagem só é único dentro dela.
        agrupado: dict[tuple, dict] = {}
        for id_mensagem, texto, id_remetente, enviada, no_conversa in cur.fetchall():
            cartoes = _cartoes_compartilhados(texto)
            em_lote = len(cartoes) > _MAX_CARTOES_PARA_RELACAO
            for nome, entradas in cartoes:
                identificadores = _identificadores_de_cartao(entradas)
                if sum(1 for tipo, _ in identificadores
                       if tipo == "telefone") > _MAX_TELEFONES_POR_CONTATO:
                    continue  # cartão agregado pelo aparelho: não diz de quem é número nenhum
                chave = (tuple(sorted(identificadores)), _nome_completo(nome),
                         id_remetente, enviada)
                registro = agrupado.setdefault(chave, {
                    "id_mensagem": id_mensagem, "nome": nome,
                    "identificadores": identificadores, "id_remetente": id_remetente,
                    "enviada": enviada, "no_conversa": no_conversa,
                    "em_lote": em_lote, "qt": 0,
                })
                registro["qt"] += 1
                # só é "em lote" se TODAS as vezes vieram em lote: compartilhado uma vez sozinho
                # e outra dentro de um despejo, vale a relação
                registro["em_lote"] = registro["em_lote"] and em_lote

        for registro in agrupado.values():
            onde = (f"conversa com {registro['no_conversa']}"
                    if registro["no_conversa"] else "conversa")
            quem = ("pelo dono do aparelho" if registro["enviada"]
                    else "por quem conversava com o dono")
            detalhe = (
                f"cartão de contato compartilhado na {onde}, {quem}"
                + (f" — {registro['qt']} vezes" if registro["qt"] > 1 else "")
                + (" (mensagem com lista de contatos)" if registro["em_lote"] else "")
            )
            perfil = indice.registrar(
                registro["identificadores"], id_extracao=id_extracao, nome=registro["nome"],
                # A prioridade MAIS BAIXA, de propósito: o nome do cartão é como um TERCEIRO
                # salvou aquela pessoa na agenda DELE, e isso rende "Mae", "Chefe", "Advogado" —
                # apelido relativo à vida de outra pessoa, que não deve tomar o lugar nem do nome
                # da agenda deste aparelho (NOME_CADASTRADO) nem do nome que a própria pessoa pôs
                # no perfil (NOME_PERFIL). Ele ainda rotula o nó quando é o único nome conhecido.
                prioridade=NOME_SECUNDARIO,
                evidencia=(tp_origem, registro["id_mensagem"]), detalhe=detalhe[:300],
                grupo=GRUPO_COMPARTILHADO,
            )
            if perfil is None or registro["em_lote"]:
                continue
            origem_remetente = None
            if registro["id_remetente"] is not None:
                origem_remetente = (_TP_ORIGEM_CONTATO[prefixo], registro["id_remetente"])
            pares.append((perfil, origem_remetente, id_extracao))
    return pares


def _dados_conversa_individual(cur, id_extracao: int) -> dict[tuple[str, int], dict]:
    """Contatos que têm conversa individual na extração — ou seja, que trocaram mensagem
    com o dono do aparelho (base da relação "conversou com") — com a contagem de mensagens
    e as datas primeira/última, para enriquecer o detalhe da relação."""
    dados: dict[tuple[str, int], dict] = {}
    for prefixo, tp_origem in (("wpp", "contato_wpp"), ("sms", "contato_sms"), ("msg", "contato_msg")):
        cur.execute(
            f"""
            SELECT p.id_contato, count(m.id_mensagem), min(m.dt_mensagem), max(m.dt_mensagem)
            FROM investigacao.{prefixo}_tb03_participantes p
            JOIN investigacao.{prefixo}_tb02_conversas c ON c.id_conversa = p.id_conversa
            JOIN investigacao.{prefixo}_tb04_mensagens m ON m.id_conversa = c.id_conversa
            WHERE c.id_extracao = %s AND c.tp_conversa = 'individual'
            GROUP BY p.id_contato
            """,
            (id_extracao,),
        )
        for id_contato, qtd, primeira, ultima in cur.fetchall():
            dados[(tp_origem, id_contato)] = {"qtd": qtd, "primeira": primeira, "ultima": ultima}
    return dados


def _marcar_top_wpp(cur, indice: _Indice, origens_conversa: dict[int, dict[tuple[str, int], dict]]) -> dict[int, int]:
    """Marca, em CADA aparelho, os _TOP_CONVERSAS_WPP interlocutores com quem o dono mais
    trocou mensagem — eles passam a virar nó na mesa mesmo sem nada além da lista de contatos
    a favor deles (ver _identificada).

    A régua geral existe para não despejar a agenda inteira no grafo, mas ela estava deixando
    de fora justamente quem mais importa: no inquérito 8, dos dez interlocutores mais frequentes
    dos dois celulares apreendidos, nove não estavam na mesa — inclusive um contato com 35.713
    mensagens trocadas com o investigado. Volume de conversa é evidência sozinho; nome salvo na
    agenda não é.

    Três cuidados:

    - só pessoa de verdade. `@newsletter` (CNN Brasil, g1, CazéTV), `@g.us` (grupo) e `@lid`
      (id oculto) rendem contagens altíssimas e não são ninguém — no D84AP três dos cinco
      primeiros eram canal de notícia. O telefone de JID/número (e a recusa de @handle, via
      _telefone_br) filtra tudo isso.
    - o DONO do aparelho lidera a lista por construção (ele participa de toda conversa
      individual, então soma todas elas) e não pode ocupar vaga; some junto a pessoa que já
      fundiu com ele.
    - a mesma pessoa com dois identificadores funde num perfil só e ocupa UMA vaga, não duas.

    Cobre WhatsApp/SMS (família do UFDR) E mensageiro genérico (`msg`, onde a Nuvem IPED grava
    tudo — WhatsApp, Instagram, Telegram…). Reaproveita a contagem que _dados_conversa_individual
    já fez — não custa uma segunda varredura de mensagens. Devolve id_extracao -> quantos foram
    marcados."""
    marcados: dict[int, int] = {}
    for id_extracao, origens in sorted(origens_conversa.items()):
        cur.execute(
            "SELECT no_fabricante, no_dispositivo FROM investigacao.inq_tb02_extracoes WHERE id_extracao = %s",
            (id_extracao,),
        )
        linha = cur.fetchone() or (None, None)
        aparelho = " ".join(p for p in linha if p) or f"aparelho {id_extracao}"

        telefone_do_contato: dict[tuple[str, int], str] = {}
        cur.execute(
            "SELECT id_contato, nu_whatsapp_id FROM investigacao.wpp_tb01_contatos WHERE id_extracao = %s",
            (id_extracao,),
        )
        for id_contato, jid in cur.fetchall():
            tel = _telefone_de_jid(jid)
            if tel:
                telefone_do_contato[("contato_wpp", id_contato)] = tel
        cur.execute(
            "SELECT id_contato, nu_identificador FROM investigacao.msg_tb01_contatos WHERE id_extracao = %s",
            (id_extracao,),
        )
        for id_contato, identificador in cur.fetchall():
            tel = _telefone_de_jid(identificador) or _telefone_br(identificador)
            if tel:
                telefone_do_contato[("contato_msg", id_contato)] = tel

        ranking = sorted(
            (
                (dados["qtd"], origem)
                for origem, dados in origens.items()
                if dados["qtd"] >= _MIN_MENSAGENS_TOP_WPP
                and telefone_do_contato.get(origem)
            ),
            reverse=True,
        )

        dono = indice.dono_de(id_extracao)
        escolhidos: list[_Perfil] = []
        for qtd, origem in ranking:
            if len(escolhidos) >= _TOP_CONVERSAS_WPP:
                break
            perfil = indice.de_origem(origem)
            if perfil is None or (dono is not None and perfil is dono.raiz()):
                continue
            if any(perfil is escolhido for escolhido in escolhidos):
                continue
            escolhidos.append(perfil)
            perfil.grupos.add(GRUPO_TOP_WPP)
            posicao = f"{len(escolhidos)}ª conversa mais frequente do {aparelho} ({qtd} mensagens)"
            # A colocação vai na OBSERVAÇÃO do nó, não só na evidência: a evidência é gravada
            # com ON CONFLICT DO NOTHING, então em quem já era nó de rodadas anteriores ela
            # nunca seria atualizada — o 1º colocado do aparelho ficava sem dizer que é o 1º.
            # A observação é reescrita a cada rodada (ver _materializar).
            perfil.top_wpp.append(posicao)
            perfil.evidencias[origem] = posicao
        marcados[id_extracao] = len(escolhidos)
    return marcados


# ---------------------------------------------------------------------------
# Materialização na mesa (entidades canônicas)
# ---------------------------------------------------------------------------


def _encontrar_canonica(cur, id_inquerito: int, perfil: _Perfil, chaves: list[str]) -> int | None:
    """Reencontra o nó da pessoa por QUALQUER identificador dela (ou pela chave do
    nó, para o que foi criado antes de ENT_TB08 existir). Pega o mais antigo."""
    identificadores = [f"{t}:{v}" for t, v in sorted(perfil.identificadores)]
    cur.execute(
        """
        SELECT min(id_canonica) FROM (
            SELECT c.id_canonica
            FROM investigacao.ent_tb05_canonicas c
            WHERE c.id_inquerito = %(inq)s AND c.tp_entidade = 'pessoa' AND c.nu_chave = ANY(%(chaves)s)
            UNION
            SELECT i.id_canonica
            FROM investigacao.ent_tb08_canonica_identificadores i
            JOIN investigacao.ent_tb05_canonicas c2 ON c2.id_canonica = i.id_canonica
            WHERE i.id_inquerito = %(inq)s AND c2.tp_entidade = 'pessoa'
              AND (i.tp_identificador || ':' || i.co_valor) = ANY(%(ids)s)
        ) achadas
        """,
        {"inq": id_inquerito, "chaves": chaves, "ids": identificadores},
    )
    row = cur.fetchone()
    return row[0] if row and row[0] else None


def _gravar_relacao(cur, id_inquerito: int, id_origem: int, id_destino: int, tipo: str,
                    simetrica: bool = False, detalhe: str | None = None) -> int:
    """Grava a aresta entre duas canônicas. simetrica=True não grava a volta quando a ida
    já existe: "conversou com" entre os donos dos dois celulares apreendidos sairia
    duplicada (uma por aparelho), e duas setas dizendo o mesmo só sujam o grafo.

    detalhe (contagem/período: "42 mensagens entre 01/01/2026 e 20/03/2026") atualiza a
    cada rodada quando muda — permite refazer a identificação depois de uma nova extração
    sem duplicar a relação. Só atualiza se ainda for automática (in_automatico): edição
    manual do investigador não é sobrescrita por uma rodada automática seguinte."""
    if id_origem == id_destino:
        return 0
    if detalhe is None:
        cur.execute(
            """
            INSERT INTO investigacao.ent_tb07_canonica_relacoes
                (id_inquerito, id_canonica_origem, id_canonica_destino, tx_tipo_relacao, in_automatico)
            SELECT %s, %s, %s, %s, true
            WHERE NOT (%s AND EXISTS (
                SELECT 1 FROM investigacao.ent_tb07_canonica_relacoes r
                WHERE r.id_canonica_origem = %s AND r.id_canonica_destino = %s AND r.tx_tipo_relacao = %s))
            ON CONFLICT (id_canonica_origem, id_canonica_destino, tx_tipo_relacao) DO NOTHING
            """,
            (id_inquerito, id_origem, id_destino, tipo,
             simetrica, id_destino, id_origem, tipo),
        )
    else:
        cur.execute(
            """
            INSERT INTO investigacao.ent_tb07_canonica_relacoes
                (id_inquerito, id_canonica_origem, id_canonica_destino, tx_tipo_relacao, in_automatico, tx_detalhe)
            SELECT %s, %s, %s, %s, true, %s
            WHERE NOT (%s AND EXISTS (
                SELECT 1 FROM investigacao.ent_tb07_canonica_relacoes r
                WHERE r.id_canonica_origem = %s AND r.id_canonica_destino = %s AND r.tx_tipo_relacao = %s))
            ON CONFLICT (id_canonica_origem, id_canonica_destino, tx_tipo_relacao)
            DO UPDATE SET tx_detalhe = EXCLUDED.tx_detalhe
            WHERE ent_tb07_canonica_relacoes.in_automatico
              AND ent_tb07_canonica_relacoes.tx_detalhe IS DISTINCT FROM EXCLUDED.tx_detalhe
            """,
            (id_inquerito, id_origem, id_destino, tipo, detalhe,
             simetrica, id_destino, id_origem, tipo),
        )
    return cur.rowcount


# Rótulo da aresta entre os titulares de dois aparelhos apreendidos que conversam entre si.
# Curto de propósito: é rótulo de aresta no grafo, cortado na tela por truncar().
RELACAO_DONOS_QUE_CONVERSAM = "dono do outro aparelho"


def _relacionar_donos_que_conversam(cur, id_inquerito: int, indice: _Indice,
                                    entre_donos: dict[tuple[int, int], dict[int, dict]]) -> int:
    """Liga, com aresta própria, os titulares de DOIS aparelhos apreendidos no mesmo inquérito
    que conversam UM COM O OUTRO.

    É um achado que o investigador tira do UFDR em dois minutos e a mesa escondia: quando cada
    celular apreendido guarda uma ponta do MESMO diálogo, ou os dois titulares são a mesma
    pessoa operando duas identidades (um "personagem" por aparelho — no inquérito 8 o titular
    do segundo iPhone se apresenta como "Tiktok" e troca 900+ mensagens com o titular do
    primeiro, que é o investigado), ou são dois investigados em contato direto. Nos dois casos
    é a primeira coisa que precisa saltar da tela, e não mais uma aresta "conversou com" no meio
    de trezentos contatos.

    O que NÃO se faz aqui é fundir os dois perfis. Afirmar "é a mesma pessoa" sem um
    identificador em comum é exatamente a fusão em cascata que _Indice.fundir recusa: dois
    celulares apreendidos juntos são, com a mesma frequência, de duas pessoas diferentes. A mesa
    mostra o vínculo e o investigador decide — o botão de fundir canônicas está a um clique."""
    if not entre_donos:
        return 0

    ids = {ext for por_extracao in entre_donos.values() for ext in por_extracao}
    cur.execute(
        "SELECT id_extracao, no_fabricante, no_dispositivo FROM investigacao.inq_tb02_extracoes "
        "WHERE id_extracao = ANY(%s)", (sorted(ids),))
    nome_aparelho = {
        id_extracao: " ".join(p for p in (fabricante, dispositivo) if p) or f"aparelho {id_extracao}"
        for id_extracao, fabricante, dispositivo in cur.fetchall()
    }

    relacoes = 0
    for (id_a, id_b), por_extracao in sorted(entre_donos.items()):
        # uma linha por aparelho que guardou o diálogo: com os dois celulares extraídos, o
        # mesmo papo aparece duas vezes, uma de cada lado — e é justamente isso que se quer ver
        lados = ", ".join(
            f"{dados['qtd']} mensagem(ns) no {nome_aparelho.get(id_extracao, 'aparelho')}"
            for id_extracao, dados in sorted(por_extracao.items()))
        detalhe = f"Titulares de dois aparelhos apreendidos neste inquérito, conversando entre si: {lados}."
        relacoes += _gravar_relacao(
            cur, id_inquerito, id_a, id_b, RELACAO_DONOS_QUE_CONVERSAM,
            simetrica=True, detalhe=detalhe[:500],
        )
    return relacoes


def _limpar_posses_obsoletas(cur, id_inquerito: int, tipo_relacao: str, tp_destino: str,
                             corretas: set[tuple[int, int]]) -> int:
    """Apaga as arestas AUTOMÁTICAS `tipo_relacao` que apontam para um nó `tp_destino` deste
    inquérito e que a varredura de agora não reafirmou.

    Existe porque a posse do aparelho já foi gravada errada: até a correção de
    _donos_por_extracao, o primeiro dono varrido reivindicava TODOS os aparelhos do inquérito.
    _gravar_relacao só insere, então, sem esta limpeza, rodar a identificação de novo somaria a
    aresta certa à errada em vez de substituí-la. Só toca no que a própria identificação criou
    (IN_AUTOMATICO): relação que o investigador desenhou na mesa não se mexe."""
    if not corretas:
        return 0
    cur.execute(
        """
        DELETE FROM investigacao.ent_tb07_canonica_relacoes r
        USING investigacao.ent_tb05_canonicas d
        WHERE d.id_canonica = r.id_canonica_destino
          AND r.id_inquerito = %(inq)s AND r.in_automatico
          AND r.tx_tipo_relacao = %(tipo)s AND d.tp_entidade = %(tp_destino)s
          AND d.id_canonica = ANY(%(destinos)s)
          AND NOT ((r.id_canonica_origem, r.id_canonica_destino) IN (
                   SELECT * FROM unnest(%(origens)s::bigint[], %(alvos)s::bigint[])))
        """,
        {
            "inq": id_inquerito, "tipo": tipo_relacao, "tp_destino": tp_destino,
            "destinos": sorted({destino for _, destino in corretas}),
            "origens": [origem for origem, _ in sorted(corretas)],
            "alvos": [destino for _, destino in sorted(corretas)],
        },
    )
    return cur.rowcount


# Prioridade da chave de deduplicação do aparelho: o IMEI é do aparelho e não muda; o ICCID é
# do chip (migra de celular, mas é o que reencontra a MESMA linha em outro caso); série e MAC
# fecham o rodapé quando não veio nem um nem outro.
_PRIORIDADE_CHAVE_APARELHO = ("imei", "iccid", "serial", "mac", "imsi")

# Rótulo gravado em ENT_TB08.TX_ORIGEM conforme a tabela de onde o identificador de aparelho
# veio — o UFDR lê de DEV_TB01_INFO_APARELHO (`info_aparelho`), o IPED/nuvem de
# DVC_TB01_DISPOSITIVOS (`dispositivo_conta`). Só é usado em _materializar_dispositivos.
_ROTULO_ORIGEM_APARELHO = {
    "info_aparelho": "identidade do aparelho (UFDR)",
    "dispositivo_conta": "identidade do aparelho (IPED/nuvem)",
}


def _donos_por_extracao(indice: _Indice, extracoes) -> dict[int, int]:
    """id_extracao -> id_canonica da pessoa dona DAQUELE aparelho (quando ela foi materializada).

    Resolve pelo pseudo-identificador ("dono", "extracao:N"), que só o dono daquela extração
    tem — e NÃO varrendo perfil.extracoes, que é "em quais aparelhos esta pessoa aparece": num
    inquérito com dois celulares, o dono de um é contato salvo no outro, então o primeiro dono
    varrido reivindicava os DOIS aparelhos e o segundo ficava sem nenhum. Resultado no inquérito
    8 (dois iPhones do mesmo investigado): a mesa dizia "HÉRCULES é dono de" os dois aparelhos e
    escondia que o segundo está declarado em nome de outra identidade.

    Roda depois de _materializar, que é quem preenche perfil.id_canonica."""
    donos: dict[int, int] = {}
    for id_extracao in extracoes:
        perfil = indice.dono_de(id_extracao)
        if perfil is not None and perfil.id_canonica is not None:
            donos[id_extracao] = perfil.id_canonica
    return donos


def _materializar_dispositivos(cur, id_inquerito: int, indice: _Indice,
                               identidades: dict[int, list[tuple[str, str, str, int]]]) -> dict:
    """Cria uma canônica 'dispositivo' por aparelho, com IMEI/ICCID/IMSI/série/MAC.

    O aparelho é entidade própria porque tem identidade própria: o mesmo celular (IMEI) ou o
    mesmo chip (ICCID) reaparecendo em outro inquérito é o vínculo mais forte que existe entre
    casos, e não cabe pendurar isso na pessoa — pessoa troca de aparelho e aparelho troca de
    dono. A relação "é dono de" liga os dois sem misturá-los.

    Cada identificador carrega o par (tp_origem, id_origem) da tabela de onde veio: o UFDR
    grava em DEV_TB01_INFO_APARELHO (`info_aparelho`) e o IPED/nuvem em DVC_TB01_DISPOSITIVOS
    (`dispositivo_conta`) — a evidência tem que apontar para a origem certa, senão dois IDs de
    tabelas diferentes colidiriam e a evidência abriria o registro errado."""
    resumo = {"dispositivos_criados": 0, "dispositivos_atualizados": 0, "relacoes": 0, "evidencias": 0}
    donos = _donos_por_extracao(indice, identidades)
    posses: set[tuple[int, int]] = set()

    for id_extracao, identificadores in identidades.items():
        if not identificadores:
            continue
        por_tipo: dict[str, list[str]] = {}
        for tipo, valor, _tp_origem, _id_origem in identificadores:
            if valor not in por_tipo.setdefault(tipo, []):
                por_tipo[tipo].append(valor)

        chave = None
        for tipo in _PRIORIDADE_CHAVE_APARELHO:
            if por_tipo.get(tipo):
                chave = f"{tipo}:{sorted(por_tipo[tipo])[0]}"
                break
        if chave is None:
            continue

        cur.execute(
            "SELECT no_dispositivo, no_fabricante FROM investigacao.inq_tb02_extracoes "
            "WHERE id_extracao = %s", (id_extracao,))
        linha = cur.fetchone() or (None, None)
        nome = " ".join(p for p in (linha[1], linha[0]) if p) or "Aparelho"
        detalhes = "; ".join(
            f"{tipo.upper()}: {', '.join(sorted(vals)[:3])}"
            for tipo, vals in sorted(por_tipo.items()))

        cur.execute(
            """
            INSERT INTO investigacao.ent_tb05_canonicas
                (id_inquerito, tp_entidade, no_entidade, nu_chave, tx_observacao, in_automatico)
            VALUES (%s, 'dispositivo', %s, %s, %s, true)
            ON CONFLICT (id_inquerito, tp_entidade, nu_chave) DO UPDATE
                SET no_entidade = EXCLUDED.no_entidade, tx_observacao = EXCLUDED.tx_observacao
                WHERE ent_tb05_canonicas.in_automatico
            RETURNING id_canonica, (xmax = 0) AS criada
            """,
            (id_inquerito, nome[:200], chave, detalhes[:2000]),
        )
        resultado = cur.fetchone()
        if resultado is None:   # conflito em nó que o investigador tornou manual
            cur.execute(
                "SELECT id_canonica FROM investigacao.ent_tb05_canonicas "
                "WHERE id_inquerito = %s AND tp_entidade = 'dispositivo' AND nu_chave = %s",
                (id_inquerito, chave))
            resultado = (cur.fetchone() or [None])[0], False
        id_canonica, criada = resultado
        if id_canonica is None:
            continue
        resumo["dispositivos_criados" if criada else "dispositivos_atualizados"] += 1

        for tipo, valores in por_tipo.items():
            for valor in valores:
                cur.execute(
                    """
                    INSERT INTO investigacao.ent_tb08_canonica_identificadores
                        (id_canonica, id_inquerito, tp_identificador, co_valor, tx_origem)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (id_canonica, tp_identificador, co_valor) DO NOTHING
                    """,
                    (id_canonica, id_inquerito, tipo, valor[:300],
                     _ROTULO_ORIGEM_APARELHO.get(identificadores[0][2], "identidade do aparelho")),
                )
        for _tipo, _valor, tp_origem, id_origem in identificadores:
            cur.execute(
                """
                INSERT INTO investigacao.ent_tb06_canonica_evidencias
                    (id_canonica, tp_origem, id_origem, tx_detalhe)
                VALUES (%s, %s, %s, 'identidade do aparelho')
                ON CONFLICT (id_canonica, tp_origem, id_origem) DO NOTHING
                """,
                (id_canonica, tp_origem, id_origem),
            )
            resumo["evidencias"] += cur.rowcount

        id_dono = donos.get(id_extracao)
        if id_dono:
            posses.add((id_dono, id_canonica))
            resumo["relacoes"] += _gravar_relacao(
                cur, id_inquerito, id_dono, id_canonica, "é dono de")

    resumo["posses_corrigidas"] = _limpar_posses_obsoletas(
        cur, id_inquerito, "é dono de", "dispositivo", posses)
    return resumo


# Um ponto vira "local" quando o aparelho conhece MUITAS redes na mesma coordenada (~11 m):
# é onde ele ficou, não por onde passou. Com menos que isso a mesa encheria de ponto de
# passagem — nos oito extratos auditados, 10+ redes deixa 75 pontos, e 3+ deixaria 1.199.
_MINIMO_REDES_LOCAL = 10


def _materializar_locais(cur, id_inquerito: int, indice: _Indice, extracoes_inquerito) -> dict:
    """Cria canônicas 'local' a partir das coordenadas das redes Wi-Fi conhecidas.

    A coordenada do Wi-Fi é bem mais precisa que a de texto livre: identifica o roteador, não
    um endereço digitado. Só vira nó o ponto com muitas redes conhecidas — ver
    _MINIMO_REDES_LOCAL."""
    resumo = {"locais_criados": 0, "locais_atualizados": 0, "relacoes": 0, "evidencias": 0}
    # o laço abaixo reusa o nome `extracoes` para as extrações de CADA ponto Wi-Fi
    donos = _donos_por_extracao(indice, extracoes_inquerito)
    presencas: set[tuple[int, int]] = set()

    cur.execute(
        f"""
        SELECT round(w.vl_latitude, 4) AS lat, round(w.vl_longitude, 4) AS lon,
               count(*) AS qt_redes,
               array_agg(DISTINCT w.id_extracao) AS extracoes,
               (array_agg(w.id_rede_wifi ORDER BY w.id_rede_wifi))[1:20] AS amostra,
               max(w.no_ssid) AS ssid
        FROM investigacao.wifi_tb01_redes w
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = w.id_extracao
        WHERE e.id_inquerito = %s AND w.vl_latitude IS NOT NULL AND w.vl_longitude IS NOT NULL
        GROUP BY round(w.vl_latitude, 4), round(w.vl_longitude, 4)
        HAVING count(*) >= {_MINIMO_REDES_LOCAL}
        """,
        (id_inquerito,),
    )
    for lat, lon, qt_redes, extracoes, amostra, ssid in cur.fetchall():
        chave = f"wifi:{lat},{lon}"
        nome = f"{lat}, {lon}"
        observacao = (f"{qt_redes} redes Wi-Fi conhecidas neste ponto (~11 m)"
                      + (f"; ex.: {ssid}" if ssid else ""))
        cur.execute(
            """
            INSERT INTO investigacao.ent_tb05_canonicas
                (id_inquerito, tp_entidade, no_entidade, nu_chave, tx_observacao, in_automatico)
            VALUES (%s, 'local', %s, %s, %s, true)
            ON CONFLICT (id_inquerito, tp_entidade, nu_chave) DO UPDATE
                SET tx_observacao = EXCLUDED.tx_observacao
                WHERE ent_tb05_canonicas.in_automatico
            RETURNING id_canonica, (xmax = 0) AS criada
            """,
            (id_inquerito, nome[:200], chave, observacao[:2000]),
        )
        resultado = cur.fetchone()
        if resultado is None:
            continue
        id_canonica, criada = resultado
        resumo["locais_criados" if criada else "locais_atualizados"] += 1

        for id_rede in (amostra or []):
            cur.execute(
                """
                INSERT INTO investigacao.ent_tb06_canonica_evidencias
                    (id_canonica, tp_origem, id_origem, tx_detalhe)
                VALUES (%s, 'wifi', %s, 'rede conhecida neste ponto')
                ON CONFLICT (id_canonica, tp_origem, id_origem) DO NOTHING
                """,
                (id_canonica, id_rede),
            )
            resumo["evidencias"] += cur.rowcount

        for id_extracao in (extracoes or []):
            id_dono = donos.get(id_extracao)
            if id_dono:
                presencas.add((id_dono, id_canonica))
                resumo["relacoes"] += _gravar_relacao(
                    cur, id_inquerito, id_dono, id_canonica, "esteve em")

    resumo["presencas_corrigidas"] = _limpar_posses_obsoletas(
        cur, id_inquerito, "esteve em", "local", presencas)
    return resumo


def _materializar(conn, id_inquerito: int, indice: _Indice, nomes_base: dict[str, str],
                  pares_pix, origens_conversa: dict[int, dict[tuple[str, int], dict]],
                  pares_simba=(), pares_rif=(), pares_ligacoes=(),
                  pares_transferencia=(), pares_compartilhados=()) -> dict:
    """Grava os perfis identificados como entidades canônicas 'pessoa', com
    identificadores, evidências e relações. Nunca sobrescreve nó manual: o UPDATE
    de nome/observação só toca no que a própria identificação criou."""
    resumo = {"pessoas_criadas": 0, "pessoas_atualizadas": 0, "evidencias": 0, "relacoes": 0}
    perfis = [p for p in indice.vivos() if _identificada(p)]

    # Diagnóstico: um "dono do aparelho" (don_tb01_donos ou contato com in_dono_dispositivo)
    # SEMPRE deveria virar pessoa (ver _identificada) — se não virou, é a fusão que deu errado
    # (mais de _MAX_TELEFONES_POR_PESSOA telefones no mesmo perfil). Sem isso, esse tipo de
    # falha fica invisível: o processo termina com sucesso e "0 pessoas identificadas" sem
    # nenhuma pista do porquê.
    resumo["donos_excluidos"] = [
        {
            "extracoes": sorted(p.extracoes),
            "qt_telefones": len(p.valores("telefone")),
            "identificadores": sorted(f"{t}:{v}" for t, v in p.identificadores)[:20],
            "nomes": [n for _, n in p.nomes][:10],
        }
        for p in indice.vivos() if p.dono and p not in perfis
    ][:20]

    with conn.cursor() as cur:
        for perfil in perfis:
            chaves = _chaves_candidatas(perfil)
            if not chaves:
                continue
            chave = chaves[0]
            nome = _nome_exibicao(perfil, chave)
            observacao = _observacao(perfil, nomes_base)

            id_canonica = _encontrar_canonica(cur, id_inquerito, perfil, chaves)
            if id_canonica is None:
                cur.execute(
                    """
                    INSERT INTO investigacao.ent_tb05_canonicas
                        (id_inquerito, tp_entidade, no_entidade, nu_chave, tx_observacao, in_automatico)
                    VALUES (%s, 'pessoa', %s, %s, %s, true)
                    ON CONFLICT (id_inquerito, tp_entidade, nu_chave) DO NOTHING
                    RETURNING id_canonica
                    """,
                    (id_inquerito, nome[:200], chave, observacao),
                )
                row = cur.fetchone()
                if row:
                    id_canonica = row[0]
                    resumo["pessoas_criadas"] += 1
                else:
                    cur.execute(
                        "SELECT id_canonica FROM investigacao.ent_tb05_canonicas "
                        "WHERE id_inquerito = %s AND tp_entidade = 'pessoa' AND nu_chave = %s",
                        (id_inquerito, chave),
                    )
                    id_canonica = cur.fetchone()[0]
            else:
                cur.execute(
                    """
                    UPDATE investigacao.ent_tb05_canonicas
                    SET no_entidade = %s, tx_observacao = %s
                    WHERE id_canonica = %s AND in_automatico
                      AND (no_entidade <> %s OR tx_observacao IS DISTINCT FROM %s)
                    """,
                    (nome[:200], observacao, id_canonica, nome[:200], observacao),
                )
                resumo["pessoas_atualizadas"] += cur.rowcount
            perfil.id_canonica = id_canonica

            for tipo, valor in sorted(perfil.identificadores):
                if tipo == "dono":
                    continue  # marcador interno da fusão, não é identificador da pessoa
                cur.execute(
                    """
                    INSERT INTO investigacao.ent_tb08_canonica_identificadores
                        (id_canonica, id_inquerito, tp_identificador, co_valor, tx_origem)
                    VALUES (%s, %s, %s, %s, 'identificação automática na ingestão')
                    ON CONFLICT (id_canonica, tp_identificador, co_valor) DO NOTHING
                    """,
                    (id_canonica, id_inquerito, tipo, valor[:300]),
                )

            for (tp_origem, id_origem), detalhe in sorted(perfil.evidencias.items()):
                cur.execute(
                    """
                    INSERT INTO investigacao.ent_tb06_canonica_evidencias
                        (id_canonica, tp_origem, id_origem, tx_detalhe)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (id_canonica, tp_origem, id_origem) DO NOTHING
                    """,
                    (id_canonica, tp_origem, id_origem, (detalhe or "")[:3000] or None),
                )
                resumo["evidencias"] += cur.rowcount
        conn.commit()

        # ---- relações entre as pessoas ----
        # "conversou com" é por APARELHO: cada dono se liga a quem conversou com ele
        # naquele celular. Num inquérito com dois aparelhos, são dois donos.
        # De passagem, anota o caso em que o interlocutor é ELE PRÓPRIO dono de outro
        # aparelho do inquérito — ver _relacionar_donos_que_conversam.
        entre_donos: dict[tuple[int, int], dict[int, dict]] = {}
        for id_extracao, origens in origens_conversa.items():
            dono = indice.dono_de(id_extracao)
            if dono is None or dono.id_canonica is None:
                continue
            for perfil in perfis:
                if perfil.id_canonica is None or perfil is dono:
                    continue
                batidas = [origens[o] for o in perfil.evidencias if o in origens]
                if not batidas:
                    continue
                qtd = sum(b["qtd"] for b in batidas)
                primeiras = [b["primeira"] for b in batidas if b["primeira"]]
                ultimas = [b["ultima"] for b in batidas if b["ultima"]]
                detalhe = f"{qtd} mensagem(ns)"
                if primeiras and ultimas:
                    detalhe += f" entre {min(primeiras):%d/%m/%Y} e {max(ultimas):%d/%m/%Y}"
                resumo["relacoes"] += _gravar_relacao(
                    cur, id_inquerito, dono.id_canonica, perfil.id_canonica, "conversou com",
                    simetrica=True, detalhe=detalhe,
                )
                # o interlocutor carrega ("dono", "extracao:N") => ele é o titular declarado
                # de OUTRO aparelho do inquérito (o deste já saiu no `perfil is dono` acima)
                if any(tipo == "dono" for tipo, _ in perfil.identificadores):
                    par = tuple(sorted((dono.id_canonica, perfil.id_canonica)))
                    entre_donos.setdefault(par, {})[id_extracao] = {"qtd": qtd}

        resumo["relacoes"] += _relacionar_donos_que_conversam(
            cur, id_inquerito, indice, entre_donos)

        for perfil, id_extracao, qtd, primeira, ultima in pares_ligacoes:
            perfil = perfil.raiz()
            dono = indice.dono_de(id_extracao)
            if dono is None or dono.id_canonica is None or perfil.id_canonica is None or perfil is dono:
                continue
            detalhe = f"{qtd} ligação(ões)"
            if primeira and ultima:
                detalhe += f" entre {primeira:%d/%m/%Y} e {ultima:%d/%m/%Y}"
            resumo["relacoes"] += _gravar_relacao(
                cur, id_inquerito, dono.id_canonica, perfil.id_canonica, "ligou para",
                simetrica=True, detalhe=detalhe,
            )

        for perfil_chave, origem_remetente, id_extracao in pares_pix:
            perfil_chave = perfil_chave.raiz()
            if perfil_chave.id_canonica is None:
                continue
            # sem contato remetente resolvido, quem citou foi o dono daquele aparelho
            remetente = indice.de_origem(origem_remetente) if origem_remetente else indice.dono_de(id_extracao)
            if remetente is None or remetente.id_canonica is None:
                continue
            resumo["relacoes"] += _gravar_relacao(
                cur, id_inquerito, perfil_chave.id_canonica, remetente.id_canonica, "chave PIX citada por"
            )

        # Cartão de contato compartilhado: a pessoa DO CARTÃO se liga a quem mandou o cartão.
        # Mesma forma do PIX acima — e, como lá, a aresta só existe quando as duas pontas já são
        # nó por mérito próprio: o cartão sozinho não promove ninguém (_GRUPOS_QUE_NAO_PROMOVEM),
        # então isto acrescenta ligação à mesa sem acrescentar gente.
        for perfil_cartao, origem_remetente, id_extracao in pares_compartilhados:
            perfil_cartao = perfil_cartao.raiz()
            if perfil_cartao.id_canonica is None:
                continue
            # sem contato remetente resolvido, quem compartilhou foi o dono daquele aparelho
            remetente = (indice.de_origem(origem_remetente) if origem_remetente
                         else indice.dono_de(id_extracao))
            if remetente is None or remetente.id_canonica is None:
                continue
            resumo["relacoes"] += _gravar_relacao(
                cur, id_inquerito, perfil_cartao.id_canonica, remetente.id_canonica,
                "contato compartilhado por"
            )

        # SIMBA/RIF: titular <-> contraparte, rotulado pela direção do dinheiro (do ponto de
        # vista do titular da conta/RIF) — "recebeu de" quando a contraparte creditou, "pagou a"
        # quando foi debitada por ele.
        # Uma contraparte rende VÁRIAS linhas do mesmo par de pessoas (uma por conta afastada no
        # SIMBA, uma por comunicação no RIF). Soma tudo antes de gravar: a relação é uma só, e o
        # detalhe dela tem de ser o total movimentado entre as duas — não o da última linha lida.
        movimento_por_relacao: dict[tuple[int, int, str], _MovimentoFinanceiro] = {}
        for perfil_titular, perfil_contraparte, direcao, movimento, fonte in (
            [(t, c, d, m, "SIMBA") for t, c, d, m in pares_simba]
            + [(t, c, d, m, "RIF") for t, c, d, m in pares_rif]
        ):
            perfil_titular, perfil_contraparte = perfil_titular.raiz(), perfil_contraparte.raiz()
            if perfil_titular.id_canonica is None or perfil_contraparte.id_canonica is None:
                continue
            tipo = f"recebeu de ({fonte})" if direcao == "credito" else f"pagou a ({fonte})"
            chave = (perfil_titular.id_canonica, perfil_contraparte.id_canonica, tipo)
            movimento_por_relacao.setdefault(chave, _MovimentoFinanceiro()).somar(movimento)

        for (id_titular, id_contraparte, tipo), movimento in movimento_por_relacao.items():
            resumo["relacoes"] += _gravar_relacao(
                cur, id_inquerito, id_titular, id_contraparte, tipo,
                detalhe=movimento.descrever(),
            )

        # Transferência do aparelho: o outro lado é o dono do celular em que ela foi achada.
        # O UFDR não diz a direção do dinheiro (não há débito/crédito como no SIMBA), então a
        # relação é neutra — afirmar "pagou a" sem base seria inventar.
        donos = _donos_por_extracao(indice, origens_conversa)
        for id_extracao, perfil_participante, detalhe in pares_transferencia:
            id_dono = donos.get(id_extracao)
            perfil_participante = perfil_participante.raiz()
            if not id_dono or perfil_participante.id_canonica is None:
                continue
            resumo["relacoes"] += _gravar_relacao(
                cur, id_inquerito, id_dono, perfil_participante.id_canonica,
                "transferência com", detalhe=detalhe,
            )
        conn.commit()

    resumo["pessoas"] = len([p for p in perfis if p.id_canonica])
    # por extração: quantas das pessoas identificadas aparecem naquele aparelho
    resumo["por_extracao"] = {
        id_extracao: len([p for p in perfis if p.id_canonica and id_extracao in p.extracoes])
        for id_extracao in sorted(origens_conversa)
    }
    return resumo


# ---------------------------------------------------------------------------
# Entradas públicas
# ---------------------------------------------------------------------------

# Chave do advisory lock que serializa a identificação de UM inquérito. Arbitrária, só não pode
# colidir com as outras do sistema: 72301 é do runner de migrações, 72302 da vaga de job pesado,
# 72303 da fila de extração e 72304 do valor identificado do RIF.
_LOCK_IDENTIFICACAO_INQUERITO = 72305


class IdentificacaoEmAndamento(Exception):
    """Já há uma identificação rodando neste inquérito. Duas ao mesmo tempo varreriam as mesmas
    tabelas e disputariam os locks de escrita das ENT_TB* — a segunda não acrescenta nada e ainda
    faz a primeira falhar por lock_timeout."""


def identificar_pessoas_inquerito(id_inquerito: int, incluir_extracao: int | None = None) -> dict:
    """Identifica as pessoas do INQUÉRITO, varrendo todas as extrações dele MAIS os
    documentos anexados a ele (RIF, SIMBA, IPL, IPEI, IRPF e telefonia), de uma vez.

    Por inquérito, e não por extração, de propósito: identidade é do caso, não do
    aparelho. Com dois celulares apreendidos, a dona do primeiro costuma ser contato
    salvo no segundo — cruzando tudo num índice só, ela vira UMA pessoa com evidência
    dos dois aparelhos (e do RIF/SIMBA, se também aparecer lá). Extração por extração,
    o registro do outro celular ficaria órfão (sozinho ele não identifica ninguém,
    então seria descartado). RIF e SIMBA são documentos do inquérito, sem aparelho
    associado, então entram uma vez só, fora do laço por extração.

    incluir_extracao força a entrada de uma extração que ainda está 'processando' —
    é o caso da chamada feita pela própria ingestão, no fim do processamento dela.
    As demais em andamento ficam de fora (dado sendo gravado).

    Idempotente. Atualiza QT_PESSOAS_IDENTIFICADAS de cada extração varrida.

    Levanta IdentificacaoEmAndamento se já houver uma rodando no mesmo inquérito.

    Conexão LONGA de propósito. Isto é uma varredura analítica do inquérito inteiro — ~13
    coletores por extração, mais RIF, SIMBA e IPL, mais a materialização de pessoas, dispositivos
    e locais — numa transação só. No perfil web (statement_timeout de 60 s) um inquérito grande
    estourava o teto, a transação inteira fazia rollback e, no caminho da ingestão, o
    `except Exception` best-effort de _processar_extracao engolia o QueryCanceled: a Mesa
    simplesmente não populava pessoa nenhuma e nada aparecia na tela. Quanto maior o caso, mais
    garantido o silêncio. O perfil longo (statement_timeout 0, lock_timeout 30 s) é o que já
    serve os laços de ingestão — ver db_utils.get_connection."""
    indice = _Indice()
    pares_pix: list = []
    pares_ligacoes: list = []
    pares_transferencia: list = []
    pares_compartilhados: list = []
    # com a forma já montada: se uma fonte não existir no inquérito, o materializador dela
    # ainda roda e não precisa saber que veio vazia
    dados_ipei: dict = {"empresas": {}, "pares": [], "veiculos": []}
    dados_irpf: dict = {"empresas": {}, "pares": []}
    dados_telefonia: dict = {"empresas": {}, "linhas": {}, "imeis": {}, "chamadas": []}
    identidades_aparelho: dict[int, list] = {}
    nomes_base: dict[str, str] = {}
    origens_conversa: dict[int, dict[tuple[str, int], dict]] = {}

    with get_connection(longa=True) as conn:
        with conn.cursor() as cur:
            # Lock de TRANSAÇÃO: solta sozinho no commit/rollback lá embaixo, então não vaza
            # quando a conexão volta para o pool — que é o risco do pg_try_advisory_lock de
            # sessão. A variante `try` não espera: quem chega depois vai embora na hora.
            cur.execute("SELECT pg_try_advisory_xact_lock(%s, %s)",
                        (_LOCK_IDENTIFICACAO_INQUERITO, id_inquerito))
            if not cur.fetchone()[0]:
                raise IdentificacaoEmAndamento(
                    "Já existe uma identificação de pessoas em andamento neste inquérito.")
            cur.execute(
                # tudo que não está em andamento: extração cancelada ou que falhou no meio
                # deixou dados gravados e navegáveis, e as pessoas deles valem igual
                "SELECT id_extracao FROM investigacao.inq_tb02_extracoes "
                "WHERE id_inquerito = %s AND (cd_status NOT IN ('pendente', 'processando') OR id_extracao = %s) "
                "ORDER BY id_extracao",
                (id_inquerito, incluir_extracao),
            )
            extracoes = [linha[0] for linha in cur.fetchall()]

            for id_extracao in extracoes:
                _coletar_contatos_conversa(cur, id_extracao, indice)
                _coletar_agenda(cur, id_extracao, indice)
                pares_ligacoes.extend(_coletar_ligacoes(cur, id_extracao, indice))
                _coletar_donos(cur, id_extracao, indice)
                _coletar_contas_usuario(cur, id_extracao, indice)
                _coletar_contas_financeiras(cur, id_extracao, indice)
                # domínios da auditoria de aproveitamento do UFDR
                identidades_aparelho[id_extracao] = _coletar_info_aparelho(cur, id_extracao, indice)
                _coletar_convidados_compromisso(cur, id_extracao, indice)
                _coletar_cartoes_credito(cur, id_extracao, indice)
                # domínios do IPED/nuvem, que gravam nas tabelas genéricas de conta — a
                # identidade do aparelho e os eventos de agenda que o UFDR lê de outras tabelas
                identidades_aparelho[id_extracao].extend(
                    _coletar_info_aparelho_nuvem(cur, id_extracao, indice))
                _coletar_eventos_nuvem(cur, id_extracao, indice)
                pares_transferencia.extend(
                    _coletar_participantes_transferencia(cur, id_extracao, indice))
                pares, nomes = _coletar_chaves_pix(cur, id_extracao, indice)
                pares_pix.extend(pares)
                nomes_base.update(nomes)
                pares_compartilhados.extend(
                    _coletar_contatos_compartilhados(cur, id_extracao, indice))
                origens_conversa[id_extracao] = _dados_conversa_individual(cur, id_extracao)

            # RIF, SIMBA, IPL, IPEI, IRPF e telefonia são documentos do INQUÉRITO, não de uma
            # extração — rodam uma vez só, fora do laço acima. Titulares primeiro: as
            # contrapartes precisam achar o perfil do titular já registrado
            # (perfil_do_identificador) para virarem relação.
            _coletar_titulares_simba(cur, id_inquerito, indice)
            pares_simba = _coletar_contrapartes_simba(cur, id_inquerito, indice)
            _coletar_titulares_rif(cur, id_inquerito, indice)
            pares_rif = _coletar_contrapartes_rif(cur, id_inquerito, indice)
            _coletar_pessoas_ipl(cur, id_inquerito, indice)
            dados_ipei = _coletar_ipei(cur, id_inquerito, indice)
            dados_irpf = _coletar_irpf(cur, id_inquerito, indice)
            dados_telefonia = _coletar_telefonia(cur, id_inquerito, indice)

            # depois de todas as fontes: precisa do dono de cada aparelho já registrado
            # (_coletar_donos) para não lhe dar uma vaga no próprio ranking
            top_wpp = _marcar_top_wpp(cur, indice, origens_conversa)

        _fundir_por_nome_abreviado(indice)
        resumo = _materializar(
            conn, id_inquerito, indice, nomes_base, pares_pix, origens_conversa,
            pares_simba=pares_simba, pares_rif=pares_rif, pares_ligacoes=pares_ligacoes,
            pares_transferencia=pares_transferencia,
            pares_compartilhados=pares_compartilhados,
        )

        # Aparelho e local viram nós próprios, depois das pessoas: a relação de posse e a de
        # presença precisam da canônica da pessoa já criada (perfil.id_canonica).
        with conn.cursor() as cur:
            # Toda contagem SOMA, não sobrescreve — o update() cru que havia aqui fazia a
            # conta dos aparelhos sobrescrever a das pessoas ("0 relações" numa rodada que
            # criou 23). Hoje três fases criam nó de empresa (IPEI, IRPF e telefonia) e duas
            # criam nó de aparelho, então a mesma chave chega de mais de um lugar.
            # A telefonia vem ANTES dos dispositivos de propósito: o IMEI do ofício da
            # operadora e o do aparelho apreendido caem na mesma chave de nó, e o rótulo bom
            # ali é o do UFDR ("Apple iPhone 13"), não "IMEI 3591…".
            for parcial in (
                _materializar_telefonia(cur, id_inquerito, indice, dados_telefonia),
                _materializar_dispositivos(cur, id_inquerito, indice, identidades_aparelho),
                _materializar_locais(cur, id_inquerito, indice, extracoes),
                _materializar_ipei(cur, id_inquerito, dados_ipei),
                _materializar_irpf(cur, id_inquerito, dados_irpf),
            ):
                for chave, valor in parcial.items():
                    if isinstance(valor, int) and not isinstance(valor, bool):
                        resumo[chave] = resumo.get(chave, 0) + valor
                    else:
                        resumo[chave] = valor

        with conn.cursor() as cur:
            for id_extracao, qt in resumo.pop("por_extracao").items():
                cur.execute(
                    "UPDATE investigacao.inq_tb02_extracoes SET qt_pessoas_identificadas = %s WHERE id_extracao = %s",
                    (qt, id_extracao),
                )
        conn.commit()

    resumo["extracoes"] = len(extracoes)
    resumo["top_wpp_por_extracao"] = top_wpp
    return resumo


# ---------------------------------------------------------------------------
# Identificação pós-importação (RIF, SIMBA) — dispara e esquece
# ---------------------------------------------------------------------------

# Intervalo entre tentativas e quantas vezes se espera quando já há uma identificação rodando no
# inquérito. Diferente da ingestão, aqui o dado JÁ está commitado: pular na primeira colisão
# deixaria o RIF/extrato novo de fora da varredura que está rodando (ela abriu antes do commit) e
# a pessoa só entraria na Mesa pelo botão manual. Então espera a varredura em andamento terminar e
# varre de novo — agora incluindo o que foi importado. 20 × 30 s cobre a varredura de um caso
# grande; além disso desiste com aviso no log (o botão da Mesa continua lá).
_ESPERA_IDENTIFICACAO_S = 30
_TENTATIVAS_IDENTIFICACAO = 20


def agendar_identificacao_pos_importacao(id_inquerito: int, origem: str) -> None:
    """Dispara a identificação do inquérito numa thread daemon depois de importar uma fonte sem
    aparelho (RIF, SIMBA) — o mesmo espírito do que a ingestão do UFDR já faz no fim do
    processamento: o envolvido do RIF e o titular/contraparte do extrato entram no grafo da Mesa
    sem ninguém precisar clicar em "Identificar pessoas" depois.

    Não bloqueia quem chama de propósito: a importação do RIF espera dentro da requisição HTTP e
    uma varredura completa leva minutos num caso grande. Best-effort: falha aqui nunca derruba uma
    importação que já terminou."""

    def _laco():
        for tentativa in range(_TENTATIVAS_IDENTIFICACAO):
            try:
                resumo = identificar_pessoas_inquerito(id_inquerito)
                logger.info("Identificação de pessoas após %s (inquérito %s): %s pessoas, %s "
                            "evidências", origem, id_inquerito,
                            resumo.get("pessoas"), resumo.get("evidencias"))
                return
            except IdentificacaoEmAndamento:
                logger.info("Identificação após %s (inquérito %s): outra em andamento, nova "
                            "tentativa em %ss (%s/%s)", origem, id_inquerito,
                            _ESPERA_IDENTIFICACAO_S, tentativa + 1, _TENTATIVAS_IDENTIFICACAO)
                time.sleep(_ESPERA_IDENTIFICACAO_S)
            except Exception:
                logger.exception("Falha na identificação de pessoas após %s (inquérito %s) "
                                 "(best-effort)", origem, id_inquerito)
                return
        logger.warning("Identificação após %s (inquérito %s) desistiu: houve outra identificação "
                       "em andamento por mais de %s min", origem, id_inquerito,
                       _ESPERA_IDENTIFICACAO_S * _TENTATIVAS_IDENTIFICACAO // 60)

    threading.Thread(target=_laco, daemon=True,
                     name=f"identificacao-pos-importacao-{id_inquerito}").start()

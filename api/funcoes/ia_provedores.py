"""Chama a API de IA configurada em Configurações (.env: AI_PROVIDER/AI_MODEL/AI_API_URL/AI_API_KEY).

Cada provedor tem um formato de requisição/resposta próprio; esta função
isola essa diferença para o resto do app só precisar mandar uma lista de
mensagens [{"role": "user"|"assistant", "content": str}] e receber texto de volta.

Suporta function calling (ferramentas + funcoes, ver funcoes.ia_investigacao) para
OpenAI-compatível, Anthropic e Ollama (API nativa) — os formatos de tool-calling que o
loop abaixo sabe orquestrar. Google segue só texto por enquanto (chamado sem tools; não
há regressão, só não ganha acesso aos dados extraídos).

Ollama usa a API nativa (/api/chat), não o endpoint /v1/chat/completions compatível com
OpenAI: só assim dá pra fixar o contexto (num_ctx) por requisição — ver _chamar_ollama.

O provedor "openai-cert" é o mesmo formato OpenAI, mas atrás de um gateway com certificado
TLS próprio (autoassinado) — ver funcoes.cert_utils: sem ensinar o certificado ao Python a
conexão nem chega a ser aberta.

Usa só a biblioteca padrão (urllib) para não precisar de mais uma dependência
externa só para chamadas HTTP.
"""

import http.client
import json
import logging
import random
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from funcoes import cert_utils
from funcoes.env_utils import parse_env, read_env_text

logger = logging.getLogger(__name__)

TIMEOUT_SEGUNDOS = 60
TIMEOUTS_POR_PROVEDOR = {
    "ollama": 1200,      # modelos locais: mais lentos
    "openai": 60,       # APIs remotas: rápidas
    # gateway interno (LiteLLM): a rede é local, mas os modelos vêm com raciocínio ativo e o
    # primeiro token pode demorar mais que o de uma API pública
    "openai-cert": 300,
    "anthropic": 60,
    "google": 60,
    "deepseek": 90,
}
# 429 do provedor ("Rate limit exceeded… max_parallel_requests" etc.): em vez de falhar na hora,
# espera e tenta de novo — o limite costuma liberar em segundos, quando o processamento paralelo
# concorrente termina. Só depois de esgotadas as tentativas o erro chega ao usuário.
# A espera cresce exponencialmente (5 s, 10, 20… até 90 s): com o limite do provedor em
# "1 requisição paralela", uma fila de centenas de documentos leva MINUTOS para andar entre um
# 429 e outro — retentativa curta de intervalo fixo (era 5×10 s) esgotava antes da vez do
# documento chegar, e a fila inteira morria em erro. O jitter evita que todos os retries
# alinhados voltem juntos no mesmo instante e batam no limite de novo (thundering herd).
RETENTATIVAS_429 = 10
INTERVALO_RETENTATIVA_429 = 5   # segundos da primeira espera; dobra a cada tentativa…
TETO_RETENTATIVA_429 = 90       # …até este teto
# trava de segurança contra a IA insistir em chamar ferramentas sem parar — generoso o
# bastante pra busca automática de entidades (vários prompts, cada um com várias chamadas),
# que usa o mesmo loop do chat normal.
MAX_ITERACOES_FERRAMENTAS = 25

# ~chars por token em português (baixo de propósito: superestimar tokens é o lado seguro). Nasceu
# em ia_historico, que continua reexportando a constante — vive aqui porque o corte de resultado de
# ferramenta, abaixo, é o primeiro a precisar dela e ia_historico importa deste módulo.
CHARS_POR_TOKEN = 3.3

# --- Teto de tamanho do que volta das ferramentas ---------------------------------------------
# O resultado de uma ferramenta entra INTEIRO na conversa mandada ao modelo, e fica lá pelas
# rodadas seguintes do mesmo turno. Sem teto, uma única busca pode estourar a janela sozinha: um
# RIF grande tem centenas de comunicações e o texto de análise do COAF passa de 3 mil caracteres
# em cada uma — buscar 250 delas dá ~900 mil caracteres, ~270 mil tokens, e o provedor recusa o
# turno inteiro (ContextWindowExceededError). Os cortes abaixo são a rede de segurança, válida
# para QUALQUER ferramenta: quem chama não precisa lembrar de se limitar.
MAX_CHARS_RESULTADO_FERRAMENTA = 40_000  # ~12 mil tokens por resultado (usado sem janela conhecida)
MAX_CHARS_CAMPO_TEXTO = 4_000            # campo de texto livre dentro de um registro
# O teto por resultado acompanha a janela do modelo: um quarto dela, entre um piso que ainda
# permite responder e um teto que impede uma única busca de tomar a conversa toda. Fixo demais
# desperdiçaria a janela de 262 mil do gateway; solto demais repetiria o estouro no modelo local.
FRACAO_JANELA_RESULTADO = 0.25
MIN_CHARS_RESULTADO_FERRAMENTA = 20_000
MAX_CHARS_RESULTADO_TETO = 200_000
# Quanto da janela do modelo o prompt inteiro pode ocupar; o resto é da resposta e da folga da
# nossa estimativa de tokens. O prompt de sistema e os schemas das ferramentas são descontados à
# parte (são fixos no turno), e o que sobra é o teto da conversa — que é o único pedaço que cresce.
FRACAO_JANELA_CONVERSA = 0.75
# Piso do teto da conversa: com um prompt de sistema gigante (modo "conteúdo completo" do
# inquérito) a conta acima chegaria a zero e podaria todo resultado de ferramenta em toda rodada.
MIN_CHARS_CONVERSA = 8_000
_MARCADOR_RESULTADO_PODADO = (
    "[resultado descartado para a conversa caber na janela de contexto do modelo — se precisar "
    "dele, refaça a busca com filtros mais estreitos (período, valor mínimo, limite menor)]"
)


# Provedores de chat que o despachante abaixo sabe falar. É a lista que as duas telas que
# escolhem provedor de IA validam contra: Configurações (rotas/configuracao_admin.py, que ainda
# soma os de visão) e a IA por usuário (funcoes/config_ia_usuario.py).
PROVEDORES_CHAT = {
    "openai", "anthropic", "google", "azure-openai", "deepseek",
    # openai-cert: mesmo formato do OpenAI, atrás de gateway com certificado TLS próprio (cert_utils)
    "openai-cert",
    "ollama", "lmstudio", "outro",
}

# Provedores que falam o formato OpenAI atrás de um gateway com certificado TLS próprio: a
# conexão só fecha se o certificado gravado em AI_CA_CERT entrar na cadeia de confiança
# (funcoes.cert_utils). Fora desta lista o Python usa as autoridades públicas do sistema, como
# deve ser para OpenAI/Anthropic/Google.
PROVEDORES_COM_CERTIFICADO = {"openai-cert"}

# Esforço de raciocínio aceito pelo gateway (campo reasoning_effort). O primeiro é o padrão:
# qualquer valor fora desta lista o gateway trata como "xhigh", que é justamente o que causa
# resposta vazia — por isso o valor é validado aqui e na tela, não repassado cru.
ESFORCOS_RACIOCINIO = ("low", "medium", "xhigh")
ESFORCO_RACIOCINIO_PADRAO = "low"
# Teto de saída por esforço. O raciocínio gasta o MESMO orçamento de max_tokens da resposta
# (ver §6 do guia de conexão), então subir o esforço sem subir o teto troca uma resposta curta
# por uma resposta vazia com finish_reason "length".
MAX_TOKENS_POR_ESFORCO = {"low": 4096, "medium": 8192, "xhigh": 16384}


def esforco_raciocinio_valido(esforco: str) -> str:
    """Normaliza o esforço de raciocínio vindo da configuração; valor vazio ou estranho cai no padrão."""
    esforco = (esforco or "").strip().lower()
    return esforco if esforco in ESFORCOS_RACIOCINIO else ESFORCO_RACIOCINIO_PADRAO


class ErroProvedorIA(Exception):
    """Erro ao chamar o provedor de IA (rede, autenticação, resposta inesperada etc.)."""


class ErroContextoExcedido(ErroProvedorIA):
    """O prompt passou da janela de contexto do modelo (o provedor recusou a chamada).

    Vale a pena distinguir do erro genérico: o loop de ferramentas reage a ele podando a conversa
    e tentando de novo, e o que sobra vira uma mensagem em português para o investigador em vez do
    JSON cru do gateway ("litellm.ContextWindowExceededError: ...")."""


# Trechos que os provedores usam para dizer "não coube": OpenAI/LiteLLM
# ("maximum context length", "context_length_exceeded", "ContextWindowExceededError"),
# Anthropic ("prompt is too long") e Ollama/LM Studio ("context length").
_SINAIS_CONTEXTO_EXCEDIDO = (
    "context length", "context_length", "contextwindowexceeded", "context window",
    "prompt is too long", "too many tokens", "maximum context",
)


def _e_erro_de_contexto(detalhe: str) -> bool:
    d = (detalhe or "").lower()
    return any(sinal in d for sinal in _SINAIS_CONTEXTO_EXCEDIDO)


# Janela de contexto (tokens) por modelo. A chave é um trecho do nome do modelo, testado em ordem —
# o primeiro que casar vale, então os mais específicos vêm antes dos genéricos. Modelo desconhecido
# cai no padrão conservador. Serve para dimensionar quanto conteúdo do inquérito cabe num envio
# (ver funcoes.ia_utils); um número menor que o real só faz enviar menos, nunca estoura.
JANELAS_CONTEXTO = [
    ("deepseek", 64_000),
    ("claude-3", 200_000), ("claude-sonnet", 200_000), ("claude-opus", 200_000),
    ("claude-haiku", 200_000), ("claude", 200_000),
    ("gpt-4o", 128_000), ("gpt-4.1", 1_000_000), ("gpt-4-turbo", 128_000),
    ("gpt-4", 8_192), ("gpt-3.5", 16_385), ("o1", 128_000), ("o3", 128_000),
    ("gemini-1.5", 1_000_000), ("gemini-2", 1_000_000), ("gemini", 32_000),
    # Locais (Ollama/LM Studio): valores conservadores — o limite real depende do
    # contexto configurado no servidor local (ex.: OLLAMA_CONTEXT_LENGTH), não só do modelo.
    ("gpt-oss", 32_000), ("mistral-small", 32_000),
    ("llama3.1", 32_000), ("llama-3.1", 32_000),
    ("llama", 32_000), ("mistral", 32_000), ("qwen", 32_000),
]
JANELA_CONTEXTO_PADRAO = 32_000

# Preço (US$ por 1 milhão de tokens: entrada, saída) por modelo, para o custo estimado da
# auditoria. Mesma mecânica de JANELAS_CONTEXTO: a chave é um trecho do nome, testada em ordem
# — valores aproximados das tabelas públicas dos provedores; modelo local custa zero e modelo
# desconhecido fica sem estimativa (NULL na auditoria, "—" na tela).
PRECOS_USD_POR_MILHAO = [
    ("gpt-4o-mini", 0.15, 0.60), ("gpt-4o", 2.50, 10.00),
    ("gpt-4.1-nano", 0.10, 0.40), ("gpt-4.1-mini", 0.40, 1.60), ("gpt-4.1", 2.00, 8.00),
    ("gpt-4-turbo", 10.00, 30.00), ("gpt-3.5", 0.50, 1.50),
    ("o3-mini", 1.10, 4.40), ("o3", 2.00, 8.00), ("o1", 15.00, 60.00),
    ("opus", 5.00, 25.00), ("sonnet", 3.00, 15.00), ("haiku", 1.00, 5.00),
    ("fable", 5.00, 25.00), ("claude", 3.00, 15.00),
    ("deepseek", 0.27, 1.10),
    ("gemini-1.5-pro", 1.25, 5.00), ("gemini-1.5-flash", 0.075, 0.30),
    ("gemini-2", 0.10, 0.40), ("gemini", 0.50, 1.50),
    # Locais (Ollama/LM Studio): sem custo por token
    ("gpt-oss", 0.0, 0.0), ("llama", 0.0, 0.0), ("qwen", 0.0, 0.0),
    ("mistral", 0.0, 0.0), ("gemma", 0.0, 0.0), ("phi", 0.0, 0.0),
]


def estimar_custo_usd(modelo: str, tokens_entrada: int, tokens_saida: int) -> float | None:
    """Custo estimado (US$) de um turno pelo preço tabelado do modelo; None se o modelo for desconhecido."""
    m = (modelo or "").lower()
    for chave, preco_entrada, preco_saida in PRECOS_USD_POR_MILHAO:
        if chave in m:
            return (tokens_entrada * preco_entrada + tokens_saida * preco_saida) / 1_000_000
    return None


class AuditoriaIA:
    """Acumula as métricas de um turno de IA para a auditoria (funcoes.ia_auditoria):
    quantas chamadas HTTP foram feitas, tokens de entrada/saída informados pelo provedor
    e cada ferramenta chamada via function calling (com os argumentos).

    Acumula também a TRILHA do turno — o que é exibido recolhido na própria bolha da
    resposta (js/utils/ia_raciocinio.js): uma entrada por rodada HTTP, com o raciocínio
    que o modelo devolveu naquela rodada e as ferramentas que ela disparou. As rodadas
    saem de registrar_chamada, que os quatro despachantes já chamam exatamente uma vez
    por requisição — não é preciso um contador à parte no laço de ferramentas."""

    def __init__(self, on_atualizar=None):
        self.qt_chamadas_api = 0
        self.qt_tokens_entrada = 0
        self.qt_tokens_saida = 0
        self.ferramentas: list[dict] = []
        self.trilha: list[dict] = []
        self._on_atualizar = on_atualizar

    def _notificar_atualizacao(self) -> None:
        """Publica uma fotografia parcial da trilha, quando o chamador acompanha o turno ao vivo.

        A auditoria continua independente da interface: falhar ao publicar o progresso nunca pode
        interromper uma resposta da IA.
        """
        if not self._on_atualizar:
            return
        try:
            self._on_atualizar(self.trilha)
        except Exception:
            logger.exception("Falha ao publicar progresso do raciocínio da IA")

    def registrar_chamada(self, tokens_entrada, tokens_saida) -> None:
        self.qt_chamadas_api += 1
        self.qt_tokens_entrada += int(tokens_entrada or 0)
        self.qt_tokens_saida += int(tokens_saida or 0)
        self.trilha.append({"rodada": len(self.trilha) + 1, "raciocinio": "", "ferramentas": []})
        self._notificar_atualizacao()

    def registrar_raciocinio(self, texto: str) -> None:
        """Guarda o raciocínio da rodada corrente. Nem todo provedor devolve um (ver
        _extrair_raciocinio); quando não devolve, a rodada fica só com as ferramentas."""
        if texto and self.trilha:
            self.trilha[-1]["raciocinio"] = texto
            self._notificar_atualizacao()

    def registrar_ferramenta(self, nome: str, argumentos: dict) -> None:
        self.ferramentas.append({"ferramenta": nome, "argumentos": argumentos})
        # a lista plana acima é a da TELA de auditoria (não muda); esta é a mesma
        # informação agrupada por rodada, para a trilha da bolha
        if self.trilha:
            self.trilha[-1]["ferramentas"].append({"ferramenta": nome, "argumentos": argumentos})
            self._notificar_atualizacao()


def trilha_para_gravar(auditoria: "AuditoriaIA | None") -> list[dict] | None:
    """Trilha pronta para gravar junto da mensagem, ou None quando não há o que mostrar.

    Descarta as rodadas vazias (sem raciocínio nem ferramenta): um turno em que a IA
    respondeu de primeira, sem buscar nada e sem devolver raciocínio, não ganha botão."""
    if not auditoria:
        return None
    util = [r for r in auditoria.trilha if r.get("raciocinio") or r.get("ferramentas")]
    return util or None


def janela_contexto(modelo: str) -> int:
    """Janela de contexto (em tokens) do modelo pelo nome; padrão conservador se não reconhecer."""
    m = (modelo or "").lower()
    for chave, janela in JANELAS_CONTEXTO:
        if chave in m:
            return janela
    return JANELA_CONTEXTO_PADRAO


def janela_da_configuracao(context_length, modelo: str) -> int:
    """Janela a usar: o "Tamanho do contexto" configurado, se houver; senão a deduzida do nome.

    O nome do modelo nem sempre diz a janela — num gateway (LiteLLM) ele é um apelido interno
    ("text-model", "auto") que não casa com nenhuma entrada de JANELAS_CONTEXTO e cai no padrão
    conservador de 32 mil, quando a janela real é de 262 mil. O campo das Configurações é a forma
    de contar a verdade ao app: com ele, o corte de conteúdo e a poda do turno usam a régua certa."""
    try:
        configurada = int(context_length or 0)
    except (TypeError, ValueError):
        configurada = 0
    return configurada if configurada > 0 else janela_contexto(modelo)


def janela_contexto_configurada(id_usuario: int | None = None) -> int:
    """Janela de contexto do modelo que ESTE turno vai usar: o da IA própria do usuário, se ele
    tiver uma, senão o do .env.

    Quem chama passa id_usuario pelo mesmo motivo de enviar_mensagem — a régua tem que ser a do
    modelo que vai receber o prompt. Um usuário com modelo de janela menor que a do padrão
    receberia mais conteúdo do que cabe, e a chamada voltaria com erro de contexto."""
    from funcoes.auditoria_acesso import id_usuario_da_requisicao
    from funcoes.config_ia_usuario import config_para_ia

    do_usuario = config_para_ia(id_usuario if id_usuario is not None else id_usuario_da_requisicao())
    env = parse_env(read_env_text())
    return janela_da_configuracao(
        do_usuario.get("contextLength") or env.get("AI_CONTEXT_LENGTH", ""),
        do_usuario.get("model") or env.get("AI_MODEL", ""),
    )


def timeout_para_provedor(provedor: str, override: int = 0) -> int:
    """Timeout (segundos) recomendado para o provedor; padrão conservador se não reconhecer.

    override > 0 substitui o padrão do provedor — é o AI_TIMEOUT gravado na aba Prompt IA das
    Configurações."""
    if override > 0:
        return override
    return TIMEOUTS_POR_PROVEDOR.get(provedor.lower(), TIMEOUT_SEGUNDOS)


def _espera_429(erro: "urllib.error.HTTPError", espera: float) -> float:
    """Segundos a aguardar após um 429: o Retry-After do provedor quando vier em segundos
    (é o caso do nosso gateway), senão a espera exponencial com jitter de ±20%."""
    cabecalho = erro.headers.get("Retry-After") if erro.headers else None
    if cabecalho:
        try:
            return max(1.0, min(float(cabecalho.strip()), float(TETO_RETENTATIVA_429)))
        except ValueError:
            pass  # Retry-After em data ("Wed, 21 Oct...") — não é o caso aqui; usa o backoff
    return max(1.0, espera * (0.8 + random.random() * 0.4))


def _post_json(
    url: str, headers: dict, corpo: dict, timeout: int = TIMEOUT_SEGUNDOS, contexto_ssl=None
) -> dict:
    """POST genérico com corpo JSON; devolve o JSON de resposta ou levanta ErroProvedorIA.

    contexto_ssl, quando dado, substitui a validação TLS padrão pela do certificado configurado
    (provedores com gateway autoassinado); None mantém as autoridades públicas do sistema.

    Um 429 (limite de requisições do provedor) não falha na primeira: espera e reenvia, até
    RETENTATIVAS_429 vezes, com espera exponencial (ver RETENTATIVAS_429) e respeitando o
    Retry-After quando o provedor manda — assim uma fila de análises de documentos consegue
    atravessar um limite de "1 requisição paralela" sem perder nenhum turno. Esgotadas as
    tentativas, o 429 vira o ErroProvedorIA de sempre."""
    dados = json.dumps(corpo).encode("utf-8")
    cabecalhos = {"Content-Type": "application/json", **headers}
    req = urllib.request.Request(url, data=dados, headers=cabecalhos, method="POST")
    retentativas = RETENTATIVAS_429
    espera = INTERVALO_RETENTATIVA_429
    while True:
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=contexto_ssl) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detalhe = e.read().decode("utf-8", errors="replace")[:500]
            if e.code == 429 and retentativas > 0:
                retentativas -= 1
                time.sleep(_espera_429(e, espera))
                espera = min(espera * 2, TETO_RETENTATIVA_429)
                continue
            if _e_erro_de_contexto(detalhe):
                raise ErroContextoExcedido(
                    "A conversa passou do tamanho que este modelo aceita de uma vez. Comece uma "
                    "conversa nova (ou faça uma pergunta mais estreita: um período, um valor mínimo, "
                    "uma pessoa) — o histórico longo e os resultados de busca deste turno não cabem "
                    f"juntos na janela do modelo. Resposta do provedor: {detalhe}"
                )
            raise ErroProvedorIA(f"A API respondeu com erro {e.code}: {detalhe}")
        except urllib.error.URLError as e:
            raise ErroProvedorIA(f"Não foi possível conectar na API de IA: {e.reason}")
        except (http.client.HTTPException, OSError) as e:
            # conexão caiu no meio da resposta (IncompleteRead, reset, timeout de socket…)
            raise ErroProvedorIA(f"A conexão com a API de IA foi interrompida: {e}")
        except (json.JSONDecodeError, KeyError, IndexError) as e:
            raise ErroProvedorIA(f"Resposta da API em formato inesperado: {e}")


def _formatar_prompt_completo(
    sistema: str | list[str] | None, mensagens: list[dict], ferramentas: list[dict] | None
) -> str:
    """Serializa o prompt montado (sistema + ferramentas + histórico com resultados de tools) em texto
    legível para copiar e colar em outra IA."""
    partes: list[str] = []
    if sistema:
        partes.append("=== PROMPT DE SISTEMA ===\n" + _juntar_sistema(sistema).strip())
    if ferramentas:
        partes.append(
            "\n=== FERRAMENTAS DISPONÍVEIS ===\n"
            + json.dumps(ferramentas, ensure_ascii=False, indent=2)
        )
    partes.append("\n=== MENSAGENS ===")
    for m in mensagens:
        papel = m.get("role", "?")
        conteudo = m.get("content")
        if isinstance(conteudo, list):
            partes.append(f"\n--- {papel} ---\n" + json.dumps(conteudo, ensure_ascii=False, indent=2))
        elif papel == "tool":
            partes.append(f"\n--- resultado de ferramenta ---\n{conteudo}")
        else:
            partes.append(f"\n--- {papel} ---\n{conteudo or ''}")
    return "\n".join(partes)


def _juntar_sistema(sistema: str | list[str] | None) -> str | None:
    """Junta os blocos do prompt de sistema num único texto (OpenAI-compatível, Ollama e Google só
    aceitam string no system). A separação entre blocos é fixa, então o prefixo estável é
    preservado para o cache automático por prefixo desses provedores."""
    if sistema is None:
        return None
    if isinstance(sistema, str):
        return sistema
    return "\n\n".join(b for b in sistema if b) or None


# Campos em que cada API devolve o raciocínio do modelo, quando devolve. Não há padrão: o
# gateway LiteLLM e o DeepSeek usam "reasoning_content"; a Anthropic e o Ollama nativo usam
# "thinking"; alguns proxies usam "reasoning". A OpenAI pública não devolve nada — ela cobra os
# tokens de raciocínio e só expõe um resumo pela Responses API, que este app não usa. Por isso a
# trilha da bolha nunca depende só disto: as ferramentas chamadas aparecem em qualquer provedor.
_CAMPOS_RACIOCINIO = ("reasoning_content", "reasoning", "thinking")

_RE_THINK = re.compile(r"<think>(.*?)</think>\s*", re.DOTALL | re.IGNORECASE)


def _texto_de_raciocinio(valor) -> str:
    """Normaliza para texto o que veio num campo de raciocínio: string, dicionário
    ({"content": …}, {"summary": …}) ou lista de blocos ({"text": …}/{"thinking": …})."""
    if isinstance(valor, str):
        return valor.strip()
    if isinstance(valor, dict):
        for chave in ("content", "text", "summary", "thinking"):
            if valor.get(chave):
                return _texto_de_raciocinio(valor[chave])
        return ""
    if isinstance(valor, list):
        partes = [_texto_de_raciocinio(item) for item in valor]
        return "\n\n".join(p for p in partes if p)
    return ""


def _extrair_raciocinio(msg: dict) -> str:
    """Raciocínio de uma mensagem de resposta, no campo que o provedor tiver usado; "" se não
    houver. Deliberadamente tolerante: o formato varia por gateway e um campo inesperado não
    pode derrubar o turno."""
    if not isinstance(msg, dict):
        return ""
    for campo in _CAMPOS_RACIOCINIO:
        texto = _texto_de_raciocinio(msg.get(campo))
        if texto:
            return texto
    return ""


def _separar_think(conteudo: str) -> tuple[str, str]:
    """Separa o raciocínio embutido em <think>…</think> do texto da resposta.

    Modelos de raciocínio rodando local (DeepSeek-R1, Qwen e afins no Ollama/LM Studio) não têm
    campo próprio: escrevem o pensamento no meio do content. Sem isso ele aparece cru na bolha."""
    if not conteudo or "<think" not in conteudo.lower():
        return conteudo, ""
    limpo = _RE_THINK.sub("", conteudo).strip()
    if not limpo:
        # o modelo só pensou e não respondeu: melhor devolver o que veio do que uma bolha vazia
        return conteudo, ""
    pensamentos = [t.strip() for t in _RE_THINK.findall(conteudo)]
    return limpo, "\n\n".join(p for p in pensamentos if p)


def _capturar_raciocinio(auditoria, msg, anonimizador) -> None:
    """Registra na trilha o raciocínio desta rodada, já com os dados reais no lugar dos códigos.

    A descodificação é aqui, e não no fim como a da resposta (ver enviar_mensagem), porque a
    trilha viaja pelo objeto de auditoria e nunca passa pelo texto de retorno — sem isso o
    investigador leria "FULANO A" no raciocínio enquanto a resposta traz o nome verdadeiro."""
    if not auditoria:
        return
    texto = _extrair_raciocinio(msg)
    if texto and anonimizador:
        texto = anonimizador.decodificar(texto)
    auditoria.registrar_raciocinio(texto)


# --- Validação dos argumentos que voltam do modelo ----------------------------------------------
# Modelos (sobretudo os locais) erram o tipo do JSON: mandam limite="10" como string, escrevem a
# lista como texto "[1, 2]", trocam true por "sim", ou inventam campo que não está no schema.
# Sem validar, o TypeError do kwargs errado vira erro genérico dentro da função e a IA repete o
# mesmo erro na rodada seguinte. Aqui o que é claramente formatação se conserta sozinho (coerção
# branda) e o que não é vira erro descritivo — o loop do agente devolve o erro como resultado de
# ferramenta, e é assim que a IA se autocorrige.
_ENUM_SENTINEL = object()


def _coager_inteiro(valor):
    if isinstance(valor, bool):
        raise ValueError(f"esperava um número inteiro, recebeu {valor!r}")
    if isinstance(valor, int):
        return valor
    if isinstance(valor, float) and valor.is_integer():
        return int(valor)
    if isinstance(valor, str):
        bruto = valor.strip().replace("_", "")
        try:
            return int(bruto)
        except ValueError:
            try:
                numero = float(bruto.replace(",", "."))
            except ValueError:
                raise ValueError(f"esperava um número inteiro, recebeu {valor!r}") from None
            if numero.is_integer():
                return int(numero)
    raise ValueError(f"esperava um número inteiro, recebeu {valor!r}")


def _coager_numero(valor):
    if isinstance(valor, bool):
        raise ValueError(f"esperava um número, recebeu {valor!r}")
    if isinstance(valor, (int, float)):
        return valor
    if isinstance(valor, str):
        try:
            return float(valor.strip().replace(",", "."))
        except ValueError:
            pass
    raise ValueError(f"esperava um número, recebeu {valor!r}")


def _coager_booleano(valor):
    if isinstance(valor, bool):
        return valor
    if isinstance(valor, str):
        bruto = valor.strip().lower()
        if bruto in ("true", "1", "sim", "yes"):
            return True
        if bruto in ("false", "0", "nao", "não", "no"):
            return False
    if valor in (0, 1):
        return bool(valor)
    raise ValueError(f"esperava verdadeiro/falso, recebeu {valor!r}")


def _coager_lista(valor, items: dict | None):
    bruto = valor
    if isinstance(valor, str):
        try:
            bruto = json.loads(valor)
        except json.JSONDecodeError:
            # "a,b,c" solto no lugar de ["a","c"]: um único item vira lista de um, vários viram
            # texto — aí a validação do tipo dos itens decide se a lista tem conserto
            bruto = [peça.strip() for peça in valor.split(",")] if "," in valor else valor
    if bruto is None or bruto == "":
        return []
    if not isinstance(bruto, list):
        if isinstance(bruto, (str, int, float, bool)):
            bruto = [bruto]  # o modelo mandou um item solto onde a schema pede lista
        else:
            raise ValueError(f"esperava uma lista, recebeu {type(valor).__name__}")
    tipo_item = (items or {}).get("type")
    if not tipo_item:
        return list(bruto)
    itens = []
    for i, item in enumerate(bruto):
        try:
            itens.append(_coager_argumento(item, tipo_item, None, (items or {}).get("enum", _ENUM_SENTINEL)))
        except ValueError as exc:
            raise ValueError(f"item {i} da lista: {exc}") from None
    return itens


def _coager_objeto(valor):
    if isinstance(valor, dict):
        return valor
    if isinstance(valor, str):
        try:
            parseado = json.loads(valor)
        except json.JSONDecodeError:
            raise ValueError(f"esperava um objeto JSON, recebeu {valor!r}") from None
        if isinstance(parseado, dict):
            return parseado
    raise ValueError(f"esperava um objeto JSON, recebeu {valor!r}")


def _coager_argumento(valor, tipo: str | None, items: dict | None, enum):
    """Valor coerido para o tipo declarado no schema; levanta ValueError com o motivo se não houver
    conserto brando. enum (quando presente no schema) é checado depois da coerção, tolerando
    maiúsculas diferentes — modelos locais escrevem "WPP" onde o schema pede "wpp"."""
    if tipo == "integer":
        valor = _coager_inteiro(valor)
    elif tipo == "number":
        valor = _coager_numero(valor)
    elif tipo == "boolean":
        valor = _coager_booleano(valor)
    elif tipo == "array":
        valor = _coager_lista(valor, items)
    elif tipo == "object":
        valor = _coager_objeto(valor)
    else:  # string, ou tipo não declarado: números viram texto, o resto precisa já ser texto
        if isinstance(valor, str):
            pass
        elif isinstance(valor, bool):
            valor = "true" if valor else "false"
        elif isinstance(valor, (int, float)):
            valor = str(valor)
        else:
            raise ValueError(f"esperava texto, recebeu {type(valor).__name__}")
    if enum is not _ENUM_SENTINEL and enum is not None:
        if valor in enum:
            return valor
        mapa = {str(v).lower(): v for v in enum}
        if isinstance(valor, str) and valor.lower() in mapa:
            return mapa[valor.lower()]
        raise ValueError(
            f"valor {valor!r} não está entre os permitidos: {', '.join(str(v) for v in enum)}"
        )
    return valor


def _validar_argumentos(esquema: dict | None, argumentos: dict) -> tuple[dict, list[str]]:
    """Confere os argumentos da chamada de ferramenta contra o schema exposto à IA.

    Devolve (argumentos corrigidos, erros). Campos fora do schema não são repassados à função
    (_kwargs errados seriam TypeError genérico) e aparecem no erro com a lista dos válidos — a IA
    re-chama acertando na rodada seguinte. Só há conserto brando de FORMATO; valores e campos
    obrigatórios continuam sob responsabilidade da própria ferramenta (validação de domínio)."""
    propriedades = (esquema or {}).get("properties") or {}
    if not propriedades:
        return dict(argumentos), []  # sem propriedades no schema não há o que validar — passa cru
    obrigatorios = (esquema or {}).get("required") or []
    erros: list[str] = []
    corrigidos: dict = {}

    desconhecidos = sorted(c for c in argumentos if c not in propriedades)
    faltando = [c for c in obrigatorios if argumentos.get(c) is None]
    if faltando:
        erros.append(
            f"campo(s) obrigatório(s) faltando: {', '.join(faltando)}. "
            f"Campos aceitos: {', '.join(sorted(propriedades)) or '(nenhum)'}."
        )
    if desconhecidos:
        erros.append(
            f"campo(s) não existe(m) nesta ferramenta: {', '.join(desconhecidos)}. "
            f"Campos aceitos: {', '.join(sorted(propriedades)) or '(nenhum)'} — refaça a chamada "
            "sem esses campos."
        )

    for campo, spec in propriedades.items():
        if campo not in argumentos or argumentos[campo] is None:
            continue
        if not isinstance(spec, dict):
            corrigidos[campo] = argumentos[campo]
            continue
        try:
            corrigidos[campo] = _coager_argumento(
                argumentos[campo], spec.get("type"), spec.get("items"),
                spec.get("enum", _ENUM_SENTINEL),
            )
        except ValueError as exc:
            erros.append(f"campo '{campo}': {exc}")

    return corrigidos, erros


def _parsear_argumentos(bruto) -> dict | None:
    """Argumentos de uma chamada de ferramenta como dict; None quando o modelo escreveu JSON
    inválido (ou um JSON que não é objeto). O chamador transforma em erro descritivo — antes
    viravam {} em silêncio e a ferramenta rodava sem os argumentos pedidos."""
    if isinstance(bruto, dict):
        return bruto
    if bruto is None or bruto == "":
        return {}
    if not isinstance(bruto, str):
        return None
    try:
        valor = json.loads(bruto)
    except json.JSONDecodeError:
        return None
    return valor if isinstance(valor, dict) else None


def _executar_ferramenta(
    nome: str, argumentos: dict | None, funcoes: dict, on_ferramenta=None, anonimizador=None,
    esquema: dict | None = None,
):
    """Roda uma ferramenta pelo nome; erro vira um resultado descritivo (a IA reage a ele, não trava a conversa).

    on_ferramenta, se dado, é chamado com (nome,) antes da execução — usado para registrar
    a etapa atual no banco e exibir em tempo real no front-end.

    esquema (o input_schema desta ferramenta), se dado, valida e coage os argumentos ANTES de
    executar: formato errado volta como erro instrutivo para a IA se corrigir, em vez do TypeError
    genérico que chegaria do corpo da função. argumentos None = JSON que nem parseou.

    anonimizador, se dado, codifica o RESULTADO antes de ele virar mensagem no histórico: é por
    aqui que sai o grosso do dado pessoal do turno (dezenas de nomes, CPF e telefone por busca).
    Os argumentos já chegam descodificados — quem despacha faz isso antes, porque a auditoria
    também precisa deles em claro."""
    if on_ferramenta:
        on_ferramenta(nome)
    fn = funcoes.get(nome) if funcoes else None
    if fn is None:
        return {"erro": f"Ferramenta desconhecida: {nome}"}
    if argumentos is None:
        return {"erro": (
            "Os argumentos desta chamada de ferramenta não são um objeto JSON válido. "
            "Refaça a chamada com JSON válido, respeitando os tipos e nomes de campo do schema."
        )}
    if esquema:
        argumentos, erros = _validar_argumentos(esquema, argumentos)
        if erros:
            return {"erro": "Argumentos inválidos: " + " ".join(erros)}
    try:
        resultado = fn(**argumentos)
    except Exception as exc:
        resultado = {"erro": str(exc)}
    return anonimizador.codificar_dados(resultado) if anonimizador else resultado


def _decodificar_chamadas(
    chamadas: list[tuple[str, dict | None]], anonimizador
) -> list[tuple[str, dict | None]]:
    """Devolve os argumentos das chamadas de ferramenta com os códigos trocados pelos valores reais.

    Sem isso a IA pede `buscar_contato_extracao(nome="FULANO A")` e a busca não acha nada — ela só
    conhece o rótulo que nós mesmos inventamos. Roda antes da auditoria de propósito: a tela de
    auditoria é do investigador e mostra o argumento verdadeiro. Argumentos None (JSON que não
    parseou) passam intactos — viram erro descritivo na execução."""
    if not anonimizador:
        return chamadas
    return [
        (nome, None if args is None else anonimizador.decodificar_dados(args))
        for nome, args in chamadas
    ]


def _executar_ferramentas_em_paralelo(
    chamadas: list[tuple[str, dict | None]], funcoes: dict, on_ferramenta=None, anonimizador=None,
    esquemas: dict | None = None,
) -> list:
    """Roda as ferramentas pedidas numa mesma rodada em paralelo; devolve os resultados na mesma
    ordem das chamadas. Cada ferramenta abre sua própria conexão com o banco (db_utils.get_connection),
    então não há estado compartilhado a proteger — o ganho é latência quando o modelo pede várias
    buscas independentes de uma vez (ex.: buscar_mensagens + buscar_ligacoes, combinação que o
    próprio prompt de sistema recomenda).

    esquemas ({nome: input_schema}), se dado, habilita a validação dos argumentos por ferramenta
    antes da execução — quem monta o lote no loop do agente já tem os schemas em mãos."""
    def executar(nome, argumentos):
        esquema = esquemas.get(nome) if esquemas else None
        return _executar_ferramenta(nome, argumentos, funcoes, on_ferramenta, anonimizador, esquema)

    if len(chamadas) <= 1:
        return [executar(nome, argumentos) for nome, argumentos in chamadas]
    with ThreadPoolExecutor(max_workers=len(chamadas)) as pool:
        futuros = [pool.submit(executar, nome, argumentos) for nome, argumentos in chamadas]
        return [f.result() for f in futuros]


def _json(valor) -> str:
    return json.dumps(valor, ensure_ascii=False, default=str)


def _encolher_textos(valor, teto_campo: int):
    """Trunca, recursivamente, toda string longa dentro do resultado de uma ferramenta.

    É o primeiro corte porque é o menos destrutivo: preserva TODOS os registros e todos os campos
    curtos (nome, CPF, data, valor — o que a IA cita como evidência), sacrificando só a cauda dos
    campos de texto livre, que são os que explodem (a análise do COAF numa comunicação de RIF, o
    corpo de um documento). O aviso no lugar do corte diz o tamanho real, para a IA saber que há
    mais texto e poder buscar de novo com um filtro que isole aquele registro."""
    if isinstance(valor, str):
        if len(valor) <= teto_campo:
            return valor
        return valor[:teto_campo] + f"… [texto cortado: {teto_campo} de {len(valor)} caracteres]"
    if isinstance(valor, list):
        return [_encolher_textos(v, teto_campo) for v in valor]
    if isinstance(valor, dict):
        return {c: _encolher_textos(v, teto_campo) for c, v in valor.items()}
    return valor


def _cortar_maior_lista(dado: dict, teto_chars: int) -> dict | None:
    """Corta a maior lista do dicionário até o resultado caber no teto; None se não houver lista.

    Cobre o formato que as ferramentas de busca devolvem ({"registros": [...], "total_disponivel":
    N, ...} — ver funcoes/ia_investigacao._executar_com_meta) sem depender do nome da chave."""
    chave = max(
        (c for c, v in dado.items() if isinstance(v, list) and v),
        key=lambda c: len(dado[c]), default=None,
    )
    if chave is None:
        return None
    itens = dado[chave]
    fixo = len(_json({c: v for c, v in dado.items() if c != chave})) + 200  # 200: chaves de aviso
    cabem, usado = 0, 0
    for item in itens:
        usado += len(_json(item)) + 1
        if fixo + usado > teto_chars:
            break
        cabem += 1
    resultado = dict(dado)
    resultado[chave] = itens[:cabem]
    resultado["truncado_por_tamanho"] = True
    resultado["aviso_tamanho"] = (
        f"Resultado grande demais para a janela de contexto: enviados {cabem} de {len(itens)} "
        "itens. Refaça a busca com filtros mais estreitos (período, valor mínimo/máximo, termo) "
        "para ver o resto — não conclua nada sobre o que ficou de fora."
    )
    return resultado


def _serializar_resultado(resultado, teto_chars: int = MAX_CHARS_RESULTADO_FERRAMENTA) -> str:
    """Resultado de ferramenta em JSON, garantidamente dentro do teto de caracteres.

    Três cortes, do menos ao mais destrutivo: encolher os campos de texto livre, depois descartar
    itens da maior lista, e por fim — resultado que não é lista nenhuma, como o texto inteiro de um
    documento — cortar o JSON bruto. Sempre sai JSON válido e sempre com aviso do que faltou: a IA
    precisa saber que o dado está incompleto, senão responde como se aquilo fosse tudo."""
    texto = _json(resultado)
    if len(texto) <= teto_chars:
        return texto

    # o corte de campo começa largo e vai apertando: um resultado de campo único (o texto de um
    # documento) aproveita quase todo o orçamento, e só uma listagem de muitos registros desce até
    # o piso — cortar todo mundo em MAX_CHARS_CAMPO_TEXTO de saída jogaria fora orçamento à toa
    teto_campo = teto_chars
    while teto_campo > MAX_CHARS_CAMPO_TEXTO:
        candidato = _json(_encolher_textos(resultado, teto_campo))
        if len(candidato) <= teto_chars:
            return candidato
        teto_campo //= 2

    encolhido = _encolher_textos(resultado, MAX_CHARS_CAMPO_TEXTO)
    texto = _json(encolhido)
    if len(texto) <= teto_chars:
        return texto

    if isinstance(encolhido, dict):
        cortado = _cortar_maior_lista(encolhido, teto_chars)
        if cortado is not None:
            texto = _json(cortado)
            if len(texto) <= teto_chars:
                return texto

    return _json({
        "truncado_por_tamanho": True,
        "aviso_tamanho": (
            f"Resultado grande demais para a janela de contexto ({len(_json(resultado))} "
            "caracteres): abaixo vai só o começo, em texto. Peça um recorte menor."
        ),
        "conteudo_parcial": texto[:teto_chars],
    })


def _tamanho_conversa(historico: list[dict]) -> int:
    return len(_json(historico))


def _podar_conversa(historico: list[dict], teto_chars: int) -> bool:
    """Troca os resultados de ferramenta MAIS ANTIGOS por um marcador até a conversa caber no teto.

    O turno acumula: cada rodada de function calling deixa no histórico a chamada e o resultado
    inteiro, e são até MAX_ITERACOES_FERRAMENTAS rodadas. Mesmo com cada resultado dentro do teto
    individual, a soma estoura. Aqui os antigos viram um marcador — a IA continua vendo que houve
    aquela busca (e o marcador diz como refazê-la), mas o volume sai do prompt.

    Preserva perguntas e respostas: só mexe no conteúdo de resultado de ferramenta, nos dois
    formatos que este módulo produz — mensagem role="tool" (OpenAI/Ollama) e blocos "tool_result"
    dentro de uma mensagem do usuário (Anthropic). Devolve se podou alguma coisa."""
    if _tamanho_conversa(historico) <= teto_chars:
        return False
    podou = False
    for msg in historico:  # do mais antigo para o mais novo
        if _podar_mensagem(msg):
            podou = True
            if _tamanho_conversa(historico) <= teto_chars:
                break
    return podou


def _podar_mensagem(msg: dict) -> bool:
    """Esvazia os resultados de ferramenta de UMA mensagem; True se havia o que esvaziar."""
    if not isinstance(msg, dict):
        return False
    conteudo = msg.get("content")
    if msg.get("role") == "tool":
        if conteudo == _MARCADOR_RESULTADO_PODADO:
            return False
        msg["content"] = _MARCADOR_RESULTADO_PODADO
        return True
    if isinstance(conteudo, list):
        podou = False
        for bloco in conteudo:
            if (isinstance(bloco, dict) and bloco.get("type") == "tool_result"
                    and bloco.get("content") != _MARCADOR_RESULTADO_PODADO):
                bloco["content"] = _MARCADOR_RESULTADO_PODADO
                podou = True
        return podou
    return False


def _teto_conversa(janela_tokens: int | None, chars_fixos: int = 0) -> int:
    """Caracteres que a conversa do turno pode ocupar: a janela do modelo menos o que é fixo.

    chars_fixos é o prompt de sistema mais os schemas das ferramentas — eles não crescem durante o
    turno, mas ocupam a janela, e sem descontá-los a conta acha que há espaço que não existe."""
    janela = janela_tokens or JANELA_CONTEXTO_PADRAO
    disponivel = int(janela * FRACAO_JANELA_CONVERSA * CHARS_POR_TOKEN) - chars_fixos
    return max(disponivel, MIN_CHARS_CONVERSA)


def _teto_resultado(janela_tokens: int | None) -> int:
    """Caracteres que UM resultado de ferramenta pode ocupar, pela janela do modelo."""
    janela = janela_tokens or JANELA_CONTEXTO_PADRAO
    pela_janela = int(janela * FRACAO_JANELA_RESULTADO * CHARS_POR_TOKEN)
    return max(MIN_CHARS_RESULTADO_FERRAMENTA, min(pela_janela, MAX_CHARS_RESULTADO_TETO))


def _post_com_poda(
    url: str, cabecalhos: dict, corpo: dict, timeout: int, contexto_ssl,
    historico: list[dict], teto_chars: int,
) -> dict:
    """POST que sobrevive a um contexto estourado: poda a conversa e tenta de novo, uma vez.

    A poda acontece in-place na lista que o corpo referencia, então basta reenviar o mesmo corpo.
    Serve de rede para o caso de a nossa conta de tokens ficar abaixo da real (janela do modelo
    configurada errada, prompt de sistema gigante, tokenizador diferente do nosso ~3,3 chars)."""
    try:
        return _post_json(url, cabecalhos, corpo, timeout=timeout, contexto_ssl=contexto_ssl)
    except ErroContextoExcedido:
        if not _podar_conversa(historico, teto_chars // 2):
            raise
        return _post_json(url, cabecalhos, corpo, timeout=timeout, contexto_ssl=contexto_ssl)


_INSTRUCAO_FINAL_SEM_TOOLS = (
    "As rodadas de chamada de ferramenta desta conversa acabaram. NÃO chame mais ferramentas: "
    "responda agora à pergunta do investigador com as informações já coletadas aqui. Se algo "
    "ficou faltando para completar a resposta, diga explicitamente o que não foi possível consultar."
)


def _ultima_chamada_sem_tools(
    historico: list[dict], mensagem_final: dict, url: str, cabecalhos: dict, corpo: dict,
    extrair_texto, timeout: int, contexto_ssl, teto_chars: int, registrar_uso=None,
) -> str | None:
    """Fecha o turno quando as iterações de ferramenta esgotaram: UMA última chamada, sem tools.

    A alternativa era o erro seco que jogava fora um turno inteiro em que a IA já tinha meiado o
    inquérito — sem ferramentas a resposta sai obrigatoriamente, com o que foi coletado. None se
    até essa chamada falhar (contexto, rede): aí o chamador recai no erro de sempre. O corpo é
    reconstruído sem "tools" e a conversa, podada de novo — o orçamento agora é todo da resposta."""
    corpo = {campo: valor for campo, valor in corpo.items() if campo != "tools"}
    historico.append(mensagem_final)
    corpo["messages"] = historico
    _podar_conversa(historico, teto_chars)
    try:
        resposta = _post_com_poda(
            url, cabecalhos, corpo, timeout, contexto_ssl, historico, teto_chars
        )
    except Exception as exc:
        logger.warning("Chamada final sem ferramentas falhou: %s", exc)
        return None
    if registrar_uso:
        registrar_uso(resposta)
    try:
        return extrair_texto(resposta)
    except (KeyError, TypeError, IndexError) as exc:
        logger.warning("Resposta final sem ferramentas veio em formato inesperado: %s", exc)
        return None


def _chamar_openai_compativel(
    api_url: str, api_key: str, modelo: str, mensagens: list[dict], sistema: str | list[str] | None,
    ferramentas: list[dict] | None, funcoes: dict | None, max_iteracoes: int = MAX_ITERACOES_FERRAMENTAS,
    auditoria: AuditoriaIA | None = None, on_iteracao=None, timeout: int = TIMEOUT_SEGUNDOS,
    on_ferramenta=None, provider: str = "", anonimizador=None, contexto_ssl=None,
    esforco_raciocinio: str = ESFORCO_RACIOCINIO_PADRAO, janela_tokens: int | None = None,
    max_tokens_saida: int | None = None,
) -> str:
    """OpenAI, DeepSeek, Azure OpenAI, Ollama (endpoint /v1) e "outro" — todos falam o mesmo formato.

    on_iteracao, se dado, é chamado antes de cada rodada (chamada HTTP) — é o ponto onde o worker
    verifica o cancelamento e aborta (levantando a própria exceção) entre as chamadas de ferramenta."""
    tools = None
    if ferramentas:
        tools = [
            {"type": "function", "function": {"name": f["name"], "description": f["description"], "parameters": f["input_schema"]}}
            for f in ferramentas
        ]
    esquemas = {f["name"]: f.get("input_schema") for f in ferramentas} if ferramentas else None
    historico = ([{"role": "system", "content": _juntar_sistema(sistema)}] if _juntar_sistema(sistema) else []) + list(mensagens)
    cabecalhos = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    url = f"{api_url.rstrip('/')}/chat/completions"
    # o prompt de sistema é a 1ª mensagem do histórico aqui: já entra na medida da conversa, e o
    # que se desconta à parte são só os schemas das ferramentas
    teto_conversa = _teto_conversa(janela_tokens, len(_json(tools or [])))
    teto_resultado = _teto_resultado(janela_tokens)

    for _ in range(max_iteracoes):
        if on_iteracao:
            on_iteracao()
        _podar_conversa(historico, teto_conversa)
        corpo = {"model": modelo, "messages": historico}
        if tools:
            corpo["tools"] = tools
        if provider == "openai-cert":
            # Gateway LiteLLM: os modelos de chat vêm com raciocínio ativo em "xhigh" por padrão e
            # o raciocínio gasta o mesmo orçamento de max_tokens da resposta. Sem estes dois
            # campos a chamada volta HTTP 200 com content vazio e finish_reason "length" — o
            # raciocínio consumiu tudo antes de escrever a resposta, e aumentar max_tokens sozinho
            # não resolve (o raciocínio se expande para preencher). Ver §6 do guia de conexão.
            # O esforço vem das Configurações (AI_REASONING_EFFORT); o teto de saída acompanha.
            esforco = esforco_raciocinio_valido(esforco_raciocinio)
            # max_tokens_saida sobrescreve o teto do esforço quando o chamador pede: respostas
            # estruturadas longas (ex.: análise de documentos) podem ser cortadas pela metade no
            # teto padrão, devolvendo JSON truncado — ver documentos_utils._processar_analise_documento.
            corpo["max_tokens"] = max_tokens_saida or MAX_TOKENS_POR_ESFORCO[esforco]
            corpo["reasoning_effort"] = esforco
        elif provider == "lmstudio":
            # LM Studio não tem o mesmo output generoso por padrão das APIs cloud; sem isso a
            # resposta pode ser cortada antes de terminar. O contexto (janela de entrada) do LM
            # Studio é fixado ao carregar o modelo na própria interface do app, não por requisição.
            corpo["max_tokens"] = max_tokens_saida or 4096
        resposta = _post_com_poda(
            url, cabecalhos, corpo, timeout, contexto_ssl, historico, teto_conversa
        )
        if auditoria:
            uso = resposta.get("usage") or {}
            auditoria.registrar_chamada(uso.get("prompt_tokens"), uso.get("completion_tokens"))
        msg = resposta["choices"][0]["message"]
        _capturar_raciocinio(auditoria, msg, anonimizador)
        chamadas = msg.get("tool_calls")
        if not chamadas:
            # modelos que embutem o pensamento no texto (<think>…</think>) só podem ser separados
            # aqui, onde a resposta final é conhecida; o que sai da bolha é o texto já limpo
            texto, pensamento = _separar_think(msg.get("content") or "")
            if pensamento and auditoria and not (auditoria.trilha and auditoria.trilha[-1]["raciocinio"]):
                _capturar_raciocinio(auditoria, {"thinking": pensamento}, anonimizador)
            return texto

        historico.append(msg)
        nomes_args = _decodificar_chamadas(
            [
                (c["function"]["name"], _parsear_argumentos(c["function"].get("arguments")))
                for c in chamadas
            ],
            anonimizador,
        )
        if auditoria:
            for nome, argumentos in nomes_args:
                auditoria.registrar_ferramenta(nome, argumentos or {})
        resultados = _executar_ferramentas_em_paralelo(
            nomes_args, funcoes, on_ferramenta=on_ferramenta, anonimizador=anonimizador,
            esquemas=esquemas,
        )
        for chamada, resultado in zip(chamadas, resultados):
            historico.append({
                "role": "tool",
                "tool_call_id": chamada["id"],
                "content": _serializar_resultado(resultado, teto_resultado),
            })

    # esgotaram as rodadas de ferramenta: em vez do erro seco que joga o turno fora, UMA última
    # chamada sem tools força a resposta com o que a IA já coletou (ver _ultima_chamada_sem_tools)
    if on_iteracao:
        on_iteracao()
    corpo_final = {"model": modelo, "messages": historico}
    if provider == "openai-cert":
        esforco = esforco_raciocinio_valido(esforco_raciocinio)
        corpo_final["max_tokens"] = max_tokens_saida or MAX_TOKENS_POR_ESFORCO[esforco]
        corpo_final["reasoning_effort"] = esforco
    elif provider == "lmstudio":
        corpo_final["max_tokens"] = max_tokens_saida or 4096

    def registrar_uso(resposta):
        uso = resposta.get("usage") or {}
        auditoria.registrar_chamada(uso.get("prompt_tokens"), uso.get("completion_tokens"))

    texto = _ultima_chamada_sem_tools(
        historico, {"role": "user", "content": _INSTRUCAO_FINAL_SEM_TOOLS},
        url, cabecalhos, corpo_final,
        lambda r: _separar_think(r["choices"][0]["message"].get("content") or "")[0],
        timeout, contexto_ssl, teto_conversa,
        registrar_uso=registrar_uso if auditoria else None,
    )
    if not texto:
        raise ErroProvedorIA("A IA não terminou a resposta depois de várias chamadas de ferramenta.")
    return texto


def _chamar_ollama(
    api_url: str, api_key: str, modelo: str, mensagens: list[dict], sistema: str | list[str] | None,
    ferramentas: list[dict] | None, funcoes: dict | None, max_iteracoes: int = MAX_ITERACOES_FERRAMENTAS,
    auditoria: AuditoriaIA | None = None, on_iteracao=None, timeout: int = TIMEOUT_SEGUNDOS,
    on_ferramenta=None, num_ctx: int | None = None, anonimizador=None, contexto_ssl=None,
    janela_tokens: int | None = None,
) -> str:
    """Ollama via API nativa (/api/chat), não o endpoint /v1/chat/completions compatível com OpenAI.

    Só a API nativa aceita fixar o tamanho do contexto (num_ctx) por requisição — no endpoint
    compatível com OpenAI o Ollama sempre usa o padrão do servidor (baixo, ex.: 4096), ignorando
    qualquer coisa mandada no corpo; com o catálogo de ferramentas deste app (dezenas de schemas) +
    resultados de busca, isso estourava o contexto e a IA local perdia dados silenciosamente.

    Formato nativo difere do compatível com OpenAI em dois pontos: "arguments" da chamada de
    ferramenta já vem como objeto (não string JSON), e não há id de chamada — o resultado é
    devolvido como mensagem "tool" na mesma ordem das chamadas, com "tool_name" só de referência."""
    tools = None
    if ferramentas:
        tools = [
            {"type": "function", "function": {"name": f["name"], "description": f["description"], "parameters": f["input_schema"]}}
            for f in ferramentas
        ]
    esquemas = {f["name"]: f.get("input_schema") for f in ferramentas} if ferramentas else None
    historico = ([{"role": "system", "content": _juntar_sistema(sistema)}] if _juntar_sistema(sistema) else []) + list(mensagens)
    cabecalhos = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    base = api_url.rstrip("/")
    if base.lower().endswith("/v1"):
        base = base[: -len("/v1")]
    url = f"{base}/api/chat"
    options = {}
    if num_ctx:
        options["num_ctx"] = num_ctx
        # sem isso a saída também fica curta no padrão do servidor, mesmo com contexto maior
        options["num_predict"] = 4096
    # o num_ctx desta requisição é a janela de verdade aqui — mais confiável que adivinhar pelo
    # nome do modelo, já que o contexto do Ollama é escolhido na configuração, não pelo modelo
    teto_conversa = _teto_conversa(num_ctx or janela_tokens, len(_json(tools or [])))
    teto_resultado = _teto_resultado(num_ctx or janela_tokens)

    for _ in range(max_iteracoes):
        if on_iteracao:
            on_iteracao()
        _podar_conversa(historico, teto_conversa)
        corpo = {"model": modelo, "messages": historico, "stream": False}
        if tools:
            corpo["tools"] = tools
        if options:
            corpo["options"] = options
        resposta = _post_com_poda(
            url, cabecalhos, corpo, timeout, contexto_ssl, historico, teto_conversa
        )
        if auditoria:
            auditoria.registrar_chamada(resposta.get("prompt_eval_count"), resposta.get("eval_count"))
        msg = resposta["message"]
        _capturar_raciocinio(auditoria, msg, anonimizador)
        chamadas = msg.get("tool_calls")
        if not chamadas:
            texto, pensamento = _separar_think(msg.get("content") or "")
            if pensamento and auditoria and not (auditoria.trilha and auditoria.trilha[-1]["raciocinio"]):
                _capturar_raciocinio(auditoria, {"thinking": pensamento}, anonimizador)
            return texto

        historico.append(msg)
        # na API nativa o Ollama costuma entregar "arguments" como objeto; _parsear_argumentos
        # aceita também a string JSON de servidores/serventes mais criativos
        nomes_args = _decodificar_chamadas(
            [(c["function"]["name"], _parsear_argumentos(c["function"].get("arguments")))
             for c in chamadas],
            anonimizador,
        )
        if auditoria:
            for nome, argumentos in nomes_args:
                auditoria.registrar_ferramenta(nome, argumentos or {})
        resultados = _executar_ferramentas_em_paralelo(
            nomes_args, funcoes, on_ferramenta=on_ferramenta, anonimizador=anonimizador,
            esquemas=esquemas,
        )
        for (nome, _args), resultado in zip(nomes_args, resultados):
            historico.append({
                "role": "tool",
                "tool_name": nome,
                "content": _serializar_resultado(resultado, teto_resultado),
            })

    # esgotaram as rodadas de ferramenta: última chamada sem tools, mesmo mecanismo do OpenAI-compatível
    if on_iteracao:
        on_iteracao()
    corpo_final = {"model": modelo, "messages": historico, "stream": False}
    if options:
        corpo_final["options"] = options

    def registrar_uso(resposta):
        auditoria.registrar_chamada(resposta.get("prompt_eval_count"), resposta.get("eval_count"))

    texto = _ultima_chamada_sem_tools(
        historico, {"role": "user", "content": _INSTRUCAO_FINAL_SEM_TOOLS},
        url, cabecalhos, corpo_final,
        lambda r: _separar_think(r["message"].get("content") or "")[0],
        timeout, contexto_ssl, teto_conversa,
        registrar_uso=registrar_uso if auditoria else None,
    )
    if not texto:
        raise ErroProvedorIA("A IA não terminou a resposta depois de várias chamadas de ferramenta.")
    return texto


def _system_anthropic(sistema: str | list[str]) -> list[dict]:
    """Blocos de sistema para o corpo da Anthropic.

    Como LISTA, cada bloco vira um texto próprio e TODO bloco exceto o ÚLTIMO recebe o marcador de
    cache: o último é o bloco dinâmico (resumo do histórico, que muda entre turnos) — marcá-lo
    quebraria o prefixo em cache e a própria API rejeita breakpoint no fim."""
    blocos = [b for b in (sistema if isinstance(sistema, list) else [sistema]) if b]
    return [
        {"type": "text", "text": bloco,
         **({"cache_control": {"type": "ephemeral"}} if i < len(blocos) - 1 else {})}
        for i, bloco in enumerate(blocos)
    ]


def _chamar_anthropic(
    api_url: str, api_key: str, modelo: str, mensagens: list[dict], sistema: str | list[str] | None,
    ferramentas: list[dict] | None, funcoes: dict | None, max_iteracoes: int = MAX_ITERACOES_FERRAMENTAS,
    auditoria: AuditoriaIA | None = None, on_iteracao=None, timeout: int = TIMEOUT_SEGUNDOS,
    on_ferramenta=None, anonimizador=None, contexto_ssl=None, janela_tokens: int | None = None,
) -> str:
    tools = None
    if ferramentas:
        tools = [{"name": f["name"], "description": f["description"], "input_schema": f["input_schema"]} for f in ferramentas]
        # Marca o fim do catálogo de ferramentas como ponto de cache: o schema é o mesmo em toda
        # iteração deste loop (até max_iteracoes por turno) e entre turnos da mesma conversa — sem
        # isso a Anthropic recontaria/recobraria integralmente esses tokens a cada chamada. Abaixo do
        # mínimo cacheável (~1024 tokens) o marcador é simplesmente ignorado, sem erro.
        tools[-1] = {**tools[-1], "cache_control": {"type": "ephemeral"}}
    esquemas = {f["name"]: f.get("input_schema") for f in ferramentas} if ferramentas else None
    historico = list(mensagens)
    cabecalhos = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
    url = f"{api_url.rstrip('/')}/messages"
    # aqui o sistema viaja em campo próprio (fora do histórico), então entra no desconto
    teto_conversa = _teto_conversa(
        janela_tokens, len(_juntar_sistema(sistema) or "") + len(_json(tools or []))
    )
    teto_resultado = _teto_resultado(janela_tokens)

    for _ in range(max_iteracoes):
        if on_iteracao:
            on_iteracao()
        _podar_conversa(historico, teto_conversa)
        corpo = {"model": modelo, "max_tokens": 4096, "messages": historico}
        if sistema:
            corpo["system"] = _system_anthropic(sistema)
        if tools:
            corpo["tools"] = tools
        resposta = _post_com_poda(
            url, cabecalhos, corpo, timeout, contexto_ssl, historico, teto_conversa
        )
        if auditoria:
            uso = resposta.get("usage") or {}
            # input_tokens exclui o que foi lido/gravado em cache — soma os três para o total real
            # de entrada não ficar subestimado na auditoria (o desconto de custo do cache não é
            # refletido aqui; superestimar o custo é preferível a subestimar o volume auditado).
            tokens_entrada = (
                (uso.get("input_tokens") or 0)
                + (uso.get("cache_creation_input_tokens") or 0)
                + (uso.get("cache_read_input_tokens") or 0)
            )
            auditoria.registrar_chamada(tokens_entrada, uso.get("output_tokens"))
        blocos = resposta["content"]
        # blocos de pensamento existem quando o corpo pede "thinking"; hoje não pedimos, então
        # isto fica vazio — e passa a valer sozinho no dia em que passarmos a pedir
        _capturar_raciocinio(
            auditoria,
            {"thinking": [b for b in blocos if b.get("type") == "thinking"]},
            anonimizador,
        )

        if resposta.get("stop_reason") != "tool_use":
            return "".join(b.get("text", "") for b in blocos if b.get("type") == "text")

        historico.append({"role": "assistant", "content": blocos})
        blocos_tool = [b for b in blocos if b.get("type") == "tool_use"]
        nomes_args = _decodificar_chamadas(
            [(b["name"], _parsear_argumentos(b.get("input"))) for b in blocos_tool], anonimizador
        )
        if auditoria:
            for nome, argumentos in nomes_args:
                auditoria.registrar_ferramenta(nome, argumentos or {})
        resultados = _executar_ferramentas_em_paralelo(
            nomes_args, funcoes, on_ferramenta=on_ferramenta, anonimizador=anonimizador,
            esquemas=esquemas,
        )
        resultados_tool = [
            {
                "type": "tool_result",
                "tool_use_id": bloco["id"],
                "content": _serializar_resultado(resultado, teto_resultado),
            }
            for bloco, resultado in zip(blocos_tool, resultados)
        ]
        historico.append({"role": "user", "content": resultados_tool})

    # esgotaram as rodadas de ferramenta: última chamada sem tools, mesmo mecanismo dos outros
    # provedores — a mensagem final entra como bloco de texto do usuário, formato nativo daqui
    if on_iteracao:
        on_iteracao()
    corpo_final = {"model": modelo, "max_tokens": 4096, "messages": historico}
    if sistema:
        corpo_final["system"] = _system_anthropic(sistema)

    def registrar_uso(resposta):
        uso = resposta.get("usage") or {}
        tokens_entrada = (
            (uso.get("input_tokens") or 0)
            + (uso.get("cache_creation_input_tokens") or 0)
            + (uso.get("cache_read_input_tokens") or 0)
        )
        auditoria.registrar_chamada(tokens_entrada, uso.get("output_tokens"))

    texto = _ultima_chamada_sem_tools(
        historico,
        {"role": "user", "content": [{"type": "text", "text": _INSTRUCAO_FINAL_SEM_TOOLS}]},
        url, cabecalhos, corpo_final,
        lambda r: "".join(b.get("text", "") for b in r["content"] if b.get("type") == "text"),
        timeout, contexto_ssl, teto_conversa,
        registrar_uso=registrar_uso if auditoria else None,
    )
    if not texto:
        raise ErroProvedorIA("A IA não terminou a resposta depois de várias chamadas de ferramenta.")
    return texto


def _chamar_google(
    api_url: str, api_key: str, modelo: str, mensagens: list[dict], sistema: str | list[str] | None,
    ferramentas: list[dict] | None, funcoes: dict | None, max_iteracoes: int = MAX_ITERACOES_FERRAMENTAS,
    auditoria: AuditoriaIA | None = None, on_iteracao=None, timeout: int = TIMEOUT_SEGUNDOS,
    on_ferramenta=None, anonimizador=None, contexto_ssl=None, janela_tokens: int | None = None,
) -> str:
    """Sem function calling por enquanto — só texto (a IA não acessa os dados extraídos aqui).

    on_ferramenta, anonimizador e janela_tokens são aceitos e ignorados: enviar_mensagem passa o
    mesmo kwargs a todos os provedores, e sem ferramenta não há resultado a codificar nem conversa
    a podar — o prompt e a resposta já passaram pelo anonimizador em enviar_mensagem."""
    if on_iteracao:
        on_iteracao()  # checkpoint de cancelamento antes da (única) chamada
    conteudos = [
        {"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
        for m in mensagens
    ]
    corpo = {"contents": conteudos}
    if sistema:
        corpo["systemInstruction"] = {"parts": [{"text": _juntar_sistema(sistema)}]}
    resposta = _post_json(
        f"{api_url.rstrip('/')}/models/{modelo}:generateContent?key={api_key}", {}, corpo,
        timeout=timeout, contexto_ssl=contexto_ssl,
    )
    if auditoria:
        uso = resposta.get("usageMetadata") or {}
        auditoria.registrar_chamada(uso.get("promptTokenCount"), uso.get("candidatesTokenCount"))
    return resposta["candidates"][0]["content"]["parts"][0]["text"]


_DESPACHANTES = {
    "anthropic": _chamar_anthropic,
    "google": _chamar_google,
    "ollama": _chamar_ollama,
}


def contexto_ssl_do_provedor(provider: str, valor_cert: str):
    """Contexto TLS para o provedor: o do certificado próprio, quando ele exige um; None (padrão do
    sistema) para os demais.

    valor_cert é o que está gravado no .env para a SEÇÃO que está chamando (AI_CA_CERT,
    REPORT_AI_CA_CERT, DOC_AI_CA_CERT, EMBED_CA_CERT) — cada aba pode apontar para um gateway
    diferente, com certificado diferente. Quem chama resolve a herança "vazio = usa o da aba
    Prompt IA", igual ao que já vale para modelo, URL e chave."""
    if provider not in PROVEDORES_COM_CERTIFICADO:
        return None
    try:
        contexto = cert_utils.contexto_ssl(valor_cert)
    except ValueError as e:
        raise ErroProvedorIA(f"Certificado do servidor de IA inválido: {e}")
    if contexto is None:
        raise ErroProvedorIA(
            "O provedor escolhido exige o certificado do servidor. Informe o arquivo .crt do "
            "gateway na aba correspondente das Configurações."
        )
    return contexto


def enviar_mensagem(
    mensagens: list[dict],
    sistema: str | list[str] | None = None,
    ferramentas: list[dict] | None = None,
    funcoes: dict | None = None,
    max_iteracoes: int = MAX_ITERACOES_FERRAMENTAS,
    auditoria: AuditoriaIA | None = None,
    on_iteracao=None,
    on_ferramenta=None,
    config: dict | None = None,
    anonimizador=None,
    id_usuario: int | None = None,
    max_tokens_saida: int | None = None,
) -> tuple[str, str]:
    """Manda o histórico pro provedor configurado, orquestrando function calling se houver ferramentas.

    sistema pode ser um texto único ou uma LISTA de blocos: lista é o jeito de os provedores com
    cache de prompt economizarem tokens entre turnos — a parte estática (prompt de sistema,
    panorama) fica num bloco cacheável e o bloco dinâmico (resumo do histórico) fica por último,
    fora do prefixo. Na Anthropic cada bloco vira um system block com cache_control (menos o
    último); nos demais provedores os blocos são unidos num texto único com separador fixo, e o
    cache automático por prefixo continua valendo.

    max_iteracoes limita as rodadas de ferramenta do turno — jobs longos (ex.: busca automática
    sobre muitos documentos) podem pedir mais que o padrão do chat.

    on_iteracao, se dado, é chamado antes de cada rodada (chamada HTTP) — usado para checar o
    cancelamento e abortar o turno entre as chamadas de ferramenta.

    config, se dado, sobrescreve a configuração do .env (chaves: provider, model, apiUrl, apiKey,
    contextLength, reasoningEffort, caCert) — usado por seções com IA própria, como as opções de IA do relatório (resumo das
    conversas e reescrita dos comentários, REPORT_AI_*). Campos ausentes/vazios caem na configuração
    padrão (AI_*).

    id_usuario identifica de quem é a chamada, para o caso de esse usuário ter IA própria
    (Usuários → "IA do usuário", ver funcoes.config_ia_usuario) — a exceção que vence tudo, até a
    configuração da seção, porque a razão dela existir é o usuário consumir a cota dele. Só
    precisa ser passado por quem roda FORA de uma requisição (threads de busca automática, de
    análise de documento); no caminho normal o usuário vem do escopo da requisição.

    anonimizador (funcoes.anonimizacao.Anonimizador), se dado, é o checkbox "Anonimizar dados" das
    Configurações: nome de pessoa, CPF, telefone e e-mail viram FULANO A/CPF A/TELEFONE A/EMAIL A na
    ida e voltam ao valor real na volta. É aqui, e não em cada chamador, porque o dado sai por três
    portas — prompt de sistema, histórico e resultado de ferramenta — e a terceira é a maior.
    A heurística de nome próprio fica DESLIGADA no prompt de sistema: ele é instrução, e trocar
    "Mesa de Inquérito" por um FULANO estragaria o que o modelo tem para seguir. Quem monta um
    sistema que carrega dado (ia_utils, no modo "incluir conteúdo") codifica esse trecho antes.

    max_tokens_saida, se dado, sobrescreve o teto de saída (max_tokens) nos provedores que o
    enviam (openai-cert e lmstudio); nos demais é ignorado. Serve para respostas estruturadas
    longas — a análise de documentos, por exemplo, cujo JSON pode ser cortado pela metade pelo
    teto padrão do esforço configurado (ver documentos_utils._processar_analise_documento).

    Devolve (texto_resposta, modelo_usado)."""
    if anonimizador:
        if isinstance(sistema, list):
            sistema = [anonimizador.codificar(b, heuristica=False) if b else b for b in sistema]
        elif sistema:
            sistema = anonimizador.codificar(sistema, heuristica=False)
        mensagens = anonimizador.codificar_mensagens(mensagens)

    env = parse_env(read_env_text())
    cfg = config or {}
    # Import tardio: config_ia_usuario importa este módulo (provedores válidos), e no topo isso
    # seria um ciclo.
    from funcoes.auditoria_acesso import id_usuario_da_requisicao
    from funcoes.config_ia_usuario import config_para_ia

    # Três camadas, da mais específica para a mais geral: IA própria do usuário > configuração da
    # seção (REPORT_AI_*, DOC_AI_*) > .env da instalação. Quase todo usuário fica no {} da
    # primeira, e aí nada muda em relação ao comportamento antigo.
    do_usuario = config_para_ia(id_usuario if id_usuario is not None else id_usuario_da_requisicao())

    def _campo(chave: str, chave_env: str):
        return do_usuario.get(chave) or cfg.get(chave) or env.get(chave_env, "")

    provider = _campo("provider", "AI_PROVIDER")
    modelo = _campo("model", "AI_MODEL")
    api_url = _campo("apiUrl", "AI_API_URL")
    api_key = _campo("apiKey", "AI_API_KEY")

    if not provider or not modelo or not api_url:
        raise ErroProvedorIA("Nenhum provedor de IA configurado. Configure em Configurações antes de usar o chat.")

    try:
        timeout_override = int(_campo("timeout", "AI_TIMEOUT") or 0)
    except (TypeError, ValueError):
        timeout_override = 0
    timeout = timeout_para_provedor(provider, timeout_override)
    despachante = _DESPACHANTES.get(provider, _chamar_openai_compativel)
    kwargs = {
        "auditoria": auditoria, "on_iteracao": on_iteracao, "timeout": timeout,
        "on_ferramenta": on_ferramenta, "anonimizador": anonimizador,
        "contexto_ssl": contexto_ssl_do_provedor(provider, _campo("caCert", "AI_CA_CERT")),
        # régua para os cortes de tamanho do turno (resultados de ferramenta e conversa acumulada)
        "janela_tokens": janela_da_configuracao(_campo("contextLength", "AI_CONTEXT_LENGTH"), modelo),
    }
    if despachante is _chamar_openai_compativel:
        kwargs["provider"] = provider
        kwargs["esforco_raciocinio"] = _campo("reasoningEffort", "AI_REASONING_EFFORT")
        kwargs["max_tokens_saida"] = max_tokens_saida
    if despachante is _chamar_ollama:
        try:
            ctx = _campo("contextLength", "AI_CONTEXT_LENGTH") or 0
            kwargs["num_ctx"] = int(ctx) or None
        except ValueError:
            kwargs["num_ctx"] = None
    texto = despachante(api_url, api_key, modelo, mensagens, sistema, ferramentas, funcoes, max_iteracoes, **kwargs)
    if anonimizador:
        texto = anonimizador.decodificar(texto)
    return texto, modelo

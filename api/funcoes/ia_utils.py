"""Funções acessórias para o chat de IA por inquérito: persistência da conversa no banco.

O modo "conteúdo completo do inquérito" anexa o conteúdo extraído do aparelho ao contexto. Se ele
cabe na janela do modelo configurado, a resposta sai na hora (síncrona). Se não cabe, o conteúdo é
dividido por data em lotes, cada lote vira uma chamada e as parciais são combinadas numa resposta
final (map-reduce) — como são várias chamadas em sequência, isso roda em background (mesma ideia da
busca automática de entidades) e o front-end faz polling de ia_tb03_processamentos.
"""

import json
import logging
import re
import threading
import time
from functools import lru_cache

import psycopg

from funcoes.anonimizacao import anonimizacao_ativa, anonimizador_para_inquerito
from funcoes.db_utils import get_connection
from funcoes.ia_auditoria import registrar_auditoria
from funcoes.ia_historico import CHARS_POR_TOKEN, montar_historico, tokens_historico
from funcoes.ia_investigacao import (
    MAX_CHARS_CONTEUDO,
    MAX_LOTES,
    ferramentas_para_inquerito,
    montar_conteudo_inquerito,
    montar_lotes_por_data,
    _cabecalho_inquerito,
)
from funcoes.ia_contexto_tela import bloco_contexto_tela
from funcoes import ia_jobs_turno
from funcoes.ia_prompt_niveis import prompt_niveis as prompt_niveis_do_usuario
from funcoes.ia_provedores import (
    AuditoriaIA,
    ErroProvedorIA,
    _formatar_prompt_completo,
    enviar_mensagem,
    janela_contexto_configurada,
    trilha_para_gravar,
)
from funcoes.ia_planilha_utils import apagar_planilhas_da_conversa
from funcoes.ia_relatorio_docx import (
    INSTRUCAO_RELATORIO,
    anexar_relatorio,
    apagar_relatorios_da_conversa,
    pedido_de_relatorio,
)

logger = logging.getLogger(__name__)

TAMANHO_TITULO = 60

# Conversa de IA sem nenhuma mensagem é apagada sozinha depois deste tempo. O painel flutuante
# (web/js/utils/ia_flutuante.js) cria uma conversa por thread de mensageiro assim que abre, então
# o investigador que só navega entre contatos no WhatsApp acumulava dezenas de conversas vazias.
_MINUTOS_CONVERSA_VAZIA = 5

# Após este tempo, um processamento ainda "pendente/processando" é considerado preso (ex.: o
# servidor reiniciou no meio de um job) e é liberado, para a trava por conversa não travar a
# conversa para sempre. Folgado para não matar um job legítimo grande (map-reduce de muitos lotes).
_MINUTOS_TRAVA_STALE = 30


class ConversaOcupada(Exception):
    """Já existe um processamento em andamento para esta conversa (trava por conversa)."""


class CancelamentoSolicitado(Exception):
    """O investigador pediu para abortar o turno (fl_cancelar); o worker encerra sem gravar resposta."""

# --- Orçamento de conteúdo x janela do modelo ---------------------------------
# Da janela de contexto (tokens) do modelo, reservamos uma parte para o que NÃO é o conteúdo
# anexado — prompt base, schemas das ferramentas, histórico da conversa e a resposta do modelo. O
# que sobra vira o orçamento do conteúdo. Estimativas conservadoras: melhor enviar um pouco menos
# do que arriscar estourar a janela (o provedor devolveria erro).
_TOKENS_RESERVA_SAIDA = 4096          # espaço para a resposta do modelo (max_tokens)
_TOKENS_RESERVA_BASE = 1500           # prompt de sistema base + margem de segurança
_CHARS_POR_TOKEN = CHARS_POR_TOKEN    # ~chars por token em português (de ia_historico: uma só verdade)
_MIN_TOKENS_CONTEUDO = 2000           # piso, para modelos de janela pequena não zerarem o orçamento

# Tabelas do chat por inquérito para a compactação do histórico (ver funcoes.ia_historico).
_TABELAS_HISTORICO = ("investigacao.ia_tb01_conversas", "investigacao.ia_tb02_mensagens")


NOME_PARA_DESCRICAO = {
    "buscar_contato_extracao": "Buscando contatos no aparelho…",
    "buscar_mensagens": "Buscando mensagens…",
    "buscar_hibrida": "Buscando evidências (literal + sentido + grafo)…",
    "buscar_ligacoes": "Buscando ligações…",
    "listar_localizacoes": "Buscando localizações…",
    "buscar_notificacoes": "Buscando notificações…",
    "buscar_notas": "Buscando notas…",
    "buscar_navegacao": "Buscando histórico de navegação…",
    "buscar_aplicativos": "Buscando aplicativos instalados…",
    "listar_donos": "Buscando dados do dono do aparelho…",
    "resumo_inquerito": "Gerando resumo do inquérito…",
    "listar_documentos_inquerito": "Listando documentos do inquérito…",
    "ler_documento_inquerito": "Lendo documento…",
    "buscar_pessoas_ipl": "Buscando pessoas nos IPLs…",
    "buscar_achados_ipl": "Buscando achados nos IPLs…",
    "listar_contas_simba": "Listando contas do extrato bancário…",
    "listar_contrapartes_simba": "Consolidando contrapartes do extrato bancário…",
    "buscar_lancamentos_simba": "Buscando lançamentos bancários…",
    "buscar_chaves_pix": "Buscando chaves PIX citadas nas conversas…",
    "buscar_redes_wifi": "Buscando redes Wi-Fi conhecidas…",
    "buscar_emails": "Buscando e-mails…",
    "listar_contas_financeiras": "Buscando contas bancárias…",
    "listar_contas_usuario": "Buscando contas de aplicativo…",
    "buscar_arquivos_dispositivo": "Buscando arquivos do aparelho…",
    "listar_pessoas_identificadas": "Listando pessoas identificadas…",
    "listar_deteccoes_padroes": "Buscando padrões de crime detectados…",
    "listar_marcacoes_relatorio": "Lendo o que foi marcado para o relatório…",
    "buscar_pessoa_referencia": "Buscando pessoa na base de referência…",
    "buscar_candidato_referencia": "Buscando candidato na base do TSE…",
    "cruzar_identidade": "Cruzando identidade…",
    "listar_documentos": "Listando documentos…",
    "ler_documento": "Lendo documento…",
    "listar_entidades": "Listando entidades…",
    "listar_relacoes": "Listando relações…",
    "listar_padroes_crime": "Listando padrões de crime…",
    "listar_entidades_existentes": "Listando entidades existentes…",
    "criar_entidade": "Criando entidade…",
    "criar_relacao": "Criando relação…",
    "resumir_conversa": "Resumindo a conversa anterior…",  # compactação do histórico (ia_historico)
}


@lru_cache(maxsize=1)
def _tokens_ferramentas() -> int:
    """Tokens ocupados pelos schemas das ferramentas, MEDIDOS na especificação real.

    Era uma constante (3000), que silenciosamente ficou defasada conforme o catálogo de ferramentas
    cresceu — e subestimar aqui infla o orçamento do conteúdo, que é justamente como se estoura a
    janela do modelo. Medir custa um json.dumps uma vez por processo (o catálogo é estático) e não
    volta a defasar quando alguém acrescentar a próxima ferramenta.

    O id_inquerito é irrelevante para o tamanho: ferramentas_para_inquerito só remove esse campo do
    schema exposto, sem tocar no banco.
    """
    schemas, _ = ferramentas_para_inquerito(0)
    return int(len(json.dumps(schemas, ensure_ascii=False)) / _CHARS_POR_TOKEN)


def _orcamento_chars_conteudo(janela_tokens: int, tokens_historico: int = 0) -> int:
    """Quantos caracteres de conteúdo cabem na janela do modelo, descontadas as reservas."""
    reserva = _TOKENS_RESERVA_SAIDA + _tokens_ferramentas() + _TOKENS_RESERVA_BASE + tokens_historico
    tokens_conteudo = max(janela_tokens - reserva, _MIN_TOKENS_CONTEUDO)
    return int(tokens_conteudo * _CHARS_POR_TOKEN)


_INTRO_CONTEUDO_SECOES = (
    "\n\nO conteúdo extraído deste inquérito segue abaixo POR COMPLETO, dividido em {qt} seções por "
    "período — a divisão é só para facilitar a leitura: juntas, as seções cobrem todo o período do "
    "inquérito, em ordem cronológica. Cada mensagem traz seu marcador [msg:wpp:ID] ou [msg:sms:ID] "
    "— cite-o ao referenciar a mensagem.\n\n"
)

_INTRO_CONTEUDO = (
    "\n\nO conteúdo extraído deste inquérito segue abaixo, já anexado (cada mensagem traz seu marcador "
    "[msg:wpp:ID] ou [msg:sms:ID] — cite-o ao referenciar a mensagem). Use-o direto para responder e "
    "recorra às ferramentas só para o que faltar (ex.: identificar uma pessoa nas bases de referência).\n\n"
)

# Método investigativo compartilhado por todos os prompts — persistência na busca, evidência citável,
# separação fato/inferência e atenção a padrões. É o núcleo que aumenta a efetividade da investigação.
_METODO_INVESTIGATIVO = (
    "MÉTODO DE TRABALHO:\n"
    "1. PERSISTÊNCIA E GÍRIA: nunca conclua 'nada encontrado' após uma única busca. O alvo raramente usa a "
    "palavra formal — varra sinônimos, siglas e gíria (de preferência com o parâmetro 'termos', numa chamada "
    "só) conforme o tipo penal em questão:\n"
    "   • Drogas: pó, branca, farinha (cocaína); pedra, crack; verde, erva, beck, baseado (maconha); bagulho, "
    "mercadoria, material, carga, bloco, tijolo; bala, doce, K, ácido (sintéticos); bucha, trouxinha, grama (porção).\n"
    "   • Dinheiro/lavagem: pila, conto, grana, verba, troco, 'x paus', 'x mil', pix, depósito, transferência, "
    "'lavar', 'esquentar'.\n"
    "   • Armas: ferro, peça, brinquedo, 'aparelho', ponto/calibre, doze; munição, cápsula, projétil.\n"
    "   • Homicídio/ameaça: apagar, sumir com, desovar, 'dar um fim', acerto, 'tá marcado', queimar, 'vou te pegar'.\n"
    "   • Polícia/fuga: gambé, os homi, os cana, viatura, 'tá quente'; sumir, vazar, cair fora.\n"
    "   Em pergunta aberta ou de CONCEITO, prefira UMA chamada de buscar_hibrida (busca híbrida: literal "
    "por palavras + sentido por embeddings + expansão pelo grafo de entidades canônicas, tudo fundido "
    "sem duplicatas) — ela substitui rodar as buscas avulsas em paralelo. Use as "
    "ferramentas avulsas só para algo específico de um pilar (palavra exata num contato, ou o grafo). "
    "Adapte as gírias à região e ao caso.\n"
    "2. EVIDÊNCIA CITÁVEL: toda afirmação relevante cita a data, o remetente e um trecho literal entre "
    "aspas, e referencia a mensagem de origem com o marcador [msg:wpp:ID] (WhatsApp), [msg:sms:ID] (SMS) ou "
    "[msg:msg:ID] (demais mensageiros), usando o id_mensagem que a ferramenta retornou — nunca invente um id. "
    "O investigador clica nele para abrir a mensagem original.\n"
    "2b. EVIDÊNCIA DE DOCUMENTO: ao usar documento anexado (listar_documentos_inquerito/"
    "ler_documento_inquerito), IPL (buscar_pessoas_ipl/buscar_achados_ipl) ou resultado de buscar_hibrida "
    "com referencia [doc:...], cite o nome do arquivo, um trecho literal entre aspas e, logo depois, o "
    "marcador [doc:ID] com o id_documento — é ele que vira o link 📄 que abre o documento no trecho citado. "
    "Formato: \"trecho literal\" (NomeDoArquivo.pdf) [doc:12]. O id está no campo id_documento/referencia do "
    "que a ferramenta retornou — nunca diga que não tem o id: se faltar, chame listar_documentos_inquerito "
    "para obtê-lo. O texto vem com linhas [[página N]] marcando onde cada página do arquivo começa: quando o "
    "trecho estiver depois de uma delas, escreva a página no marcador — [doc:12:16] abre já na página 16. Use "
    "SEMPRE o N dessa linha, nunca o número impresso no rodapé do papel (folha do processo, campo nu_folha). "
    "Documento sem essas linhas não tem página — cite só [doc:ID]. Nunca invente id ou página. Escrever só "
    "\"Página 16\" em texto livre NÃO gera link.\n"
    "3. FATO x INFERÊNCIA: distinga o que o dado mostra do que é sua dedução; não afirme hipótese como fato.\n"
    "4. PADRÕES SUSPEITOS: fique atento a uso de múltiplos nomes/identidades pela mesma pessoa (comum no "
    "tráfico: o mesmo indivíduo opera vários números/chips/contas, e falar de si mesmo em terceira pessoa é "
    "técnica deliberada de dissimulação, não prova de serem pessoas diferentes), linguagem cifrada, valores "
    "fracionados, horários atípicos, menção a terceiros e combinações de encontro.\n"
    "5. IDENTIFICAÇÃO: se um contato já tiver pessoa_associada no resultado, use-a (identificação confirmada "
    "pelo investigador). Para descobrir quem é alguém, use as bases de referência via cruzar_identidade. "
    "CUIDADO ao concluir que dois contatos/apelidos são pessoas DIFERENTES: números de telefone distintos, "
    "nomes de agenda distintos ou uma mensagem em terceira pessoa ('em nome de X', 'X mandou') NÃO são prova "
    "disso — são exatamente o padrão que uma mesma pessoa usando múltiplos números produziria de propósito. "
    "Nunca apresente 'são pessoas diferentes' como conclusão fechada ('Conclusão: ... NÃO são a mesma "
    "pessoa') a partir só desse tipo de indício; trate como INFERÊNCIA, com o grau de confiança explícito e "
    "o que a fragilizaria, e recomende diligência para confirmar (cruzar_identidade, comparar padrão de "
    "escrita/horário/localização entre os números, e sobretudo perguntar ao investigador — sua experiência "
    "de campo no tipo penal em jogo vale mais que a superfície do texto).\n"
    "Baseie tudo no que as ferramentas/o conteúdo de fato trouxerem — nunca invente. Responda em português."
)

SISTEMA_PROMPT = (
    "Você é um analista de investigação criminal ajudando um investigador a explorar os dados extraídos "
    "do celular de um inquérito: contatos e mensagens de WhatsApp/SMS (com a transcrição dos áudios no "
    "campo transcricao_audio — mensagem de voz não tem texto), ligações, localizações, notificações, "
    "navegação e aplicativos. O inquérito também pode ter:\n"
    "• Documentos anexados (ex.: o PDF do próprio inquérito no ePol, com depoimentos e relatórios) — "
    "veja listar_documentos_inquerito e leia com ler_documento_inquerito quando a pergunta envolver "
    "o teor do inquérito, e cruze o que consta no papel com o que foi extraído do aparelho.\n"
    "• IPLs (Inquéritos Policiais em PDF): use buscar_pessoas_ipl para saber quem é citado (investigado, "
    "advogado, delegado, testemunha) e buscar_achados_ipl para encontrar telefones, e-mails, valores, "
    "contas, endereços e outras evidências mencionadas no inquérito.\n"
    "• RIFs (Relatórios de Inteligência Financeira do COAF): use buscar_comunicacoes_rif_filtradas "
    "para analisar transações bancárias suspeitas e buscar_envolvidos_rif_filtrados para identificar "
    "pessoas/empresas envolvidas no fluxo financeiro.\n"
    "• Extratos bancários do SIMBA (quebra de sigilo, relatório R304): comece por listar_contas_simba "
    "(quanto passou por cada conta de cada investigado), use listar_contrapartes_simba para saber quem "
    "mandava e quem recebia dinheiro deles, e buscar_lancamentos_simba para detalhar uma movimentação. "
    "Diferente do RIF, aqui está o extrato completo — os totais vêm das duas primeiras ferramentas; "
    "buscar_lancamentos_simba é sempre uma amostra limitada e não serve para somar.\n"
    "Em pergunta aberta ou de panorama, comece por resumo_inquerito (volumes, período, contatos mais "
    "ativos) antes das buscas específicas. Quando a pergunta envolver investigados, pessoas citadas, "
    "endereços, valores ou transações financeiras, use as ferramentas de IPL/RIF para complementar a "
    "análise dos dados do aparelho.\n\n"
    + _METODO_INVESTIGATIVO
    + "\n\nTAMANHO DA RESPOSTA: adapte-a à pergunta. Pergunta simples ou factual (ex.: 'quantas mensagens?', "
    "'quem é X?') pede resposta curta e direta, sem seções. Só use a estrutura completa Achados → Evidências "
    "(com citação e marcador) → Lacunas → Próximas diligências em perguntas abertas/analíticas."
    "\n\nNUNCA prometa uma tabela/planilha gigante na resposta: ela é cortada pelo teto de tokens no meio "
    "e o 'continue' do investigador não recupera o resto. Pedido de planilha/consolidação sobre muitos "
    "documentos (ex.: 'todas as armas apreendidas em 2026 a partir dos PDFs') é trabalho para o AGENTE em "
    "segundo plano: chame gerar_planilha_consolidada com avaliar=true, explique ao investigador o que o "
    "agente vai fazer e o tempo estimado, e PERGUNTE se pode acionar. Só dispare depois do 'sim'. "
    "Trabalho menor — poucas linhas que cabem na resposta — resolva direto na conversa, sem job."
)

_RESUMO_METODO = (
    "\n\nTAMANHO DA RESPOSTA: adapte-a à pergunta. Pergunta simples ou factual pede resposta curta e "
    "direta, sem seções. Só use a estrutura completa Achados → Evidências (com citação) → Lacunas → "
    "Próximas diligências em perguntas abertas/analíticas."
)

# Prompts de sistema para conversa restrita a UM documento (botão "Prompt de IA específico" num
# RIF/extrato do SIMBA/IPL) — substituem o SISTEMA_PROMPT geral porque as ferramentas de fora do
# escopo (mensagens, ligações, localizações etc.) nem existem nessa conversa; descrevê-las
# confundiria o modelo. Ver ia_investigacao.ferramentas_para_inquerito.
SISTEMA_PROMPT_ESCOPO = {
    "rif": (
        "Você é um analista de investigação criminal ajudando um investigador a analisar UM ÚNICO RIF "
        "(Relatório de Inteligência Financeira do COAF) anexado ao inquérito — não o inquérito inteiro. "
        "Use buscar_comunicacoes_rif_filtradas (transações) e buscar_envolvidos_rif_filtrados "
        "(pessoas/empresas envolvidas). As ferramentas já estão presas a este RIF: "
        "você não tem acesso a outros RIFs, extratos do SIMBA, IPLs ou ao conteúdo do aparelho deste "
        "inquérito — não afirme nada sobre o que não pode ver.\n\n" + _METODO_INVESTIGATIVO + _RESUMO_METODO
    ),
    "simba": (
        "Você é um analista de investigação criminal ajudando um investigador a analisar UM ÚNICO "
        "extrato bancário do SIMBA (quebra de sigilo, relatório R304) anexado ao inquérito — não o "
        "inquérito inteiro. Comece por listar_contas_simba (quanto passou por cada conta), use "
        "listar_contrapartes_simba para saber quem mandava/recebia dinheiro e buscar_lancamentos_simba "
        "para detalhar uma movimentação (é sempre uma amostra limitada, não serve para somar — os "
        "totais vêm das duas primeiras ferramentas). As ferramentas já estão presas a este extrato: você "
        "não tem acesso a outros extratos, RIFs, IPLs ou ao conteúdo do aparelho deste inquérito — não "
        "afirme nada sobre o que não pode ver.\n\n" + _METODO_INVESTIGATIVO + _RESUMO_METODO
    ),
    "ipl": (
        "Você é um analista de investigação criminal ajudando um investigador a analisar UM ÚNICO "
        "Inquérito Policial em PDF (IPL) anexado ao inquérito — não o inquérito inteiro. Use "
        "buscar_pessoas_ipl para saber quem é citado (investigado, advogado, delegado, testemunha) e "
        "buscar_achados_ipl para telefones, e-mails, valores, contas, endereços e outras evidências "
        "mencionadas no PDF. As ferramentas já estão presas a este IPL: você não tem acesso a outros "
        "IPLs, RIFs, extratos do SIMBA ou ao conteúdo do aparelho deste inquérito — não afirme nada "
        "sobre o que não pode ver.\n\n" + _METODO_INVESTIGATIVO + _RESUMO_METODO
    ),
    "irpf": (
        "Você é um analista de investigação criminal ajudando um investigador a analisar UMA ÚNICA "
        "declaração de IRPF (Receita Federal) anexada ao inquérito — não o inquérito inteiro. Use "
        "obter_declaracao_irpf para identificação do contribuinte e resumo (rendimentos, imposto, "
        "evolução patrimonial — bens e dívidas em 31/12 do ano anterior e do atual, o sinal mais "
        "forte pra apuração patrimonial), buscar_bens_direitos_irpf para os bens declarados, "
        "buscar_dependentes_irpf para vínculos familiares, buscar_rendimentos_irpf para as fontes "
        "pagadoras (pessoa jurídica) e buscar_dividas_irpf/buscar_doacoes_irpf para dívidas e "
        "doações. Extração por regex (sem IA) — seções raras como atividade rural, renda variável "
        "e ganhos de capital não foram extraídas; se a pergunta depender delas, avise que essa "
        "informação não está disponível aqui em vez de inventar. As ferramentas já estão presas a "
        "esta declaração: você não tem acesso a outras declarações de IRPF, RIFs, IPLs, extratos do "
        "SIMBA ou ao conteúdo do aparelho deste inquérito — não afirme nada sobre o que não pode "
        "ver.\n\n" + _METODO_INVESTIGATIVO + _RESUMO_METODO
    ),
    "ipei": (
        "Você é um analista de investigação criminal ajudando um investigador a analisar UMA ÚNICA "
        "IPEI — Informação de Pesquisa e Investigação da Receita Federal, o relatório da quebra de "
        "sigilo fiscal — anexada ao inquérito, e não o inquérito inteiro. Use obter_ipei para o "
        "cabeçalho e os alvos analisados (pessoa física ou jurídica, com dados cadastrais), "
        "buscar_relacionados_ipei para os vínculos (sócios, empregadores, procurações e quem "
        "transmitiu declaração do MESMO endereço MAC — esse último é o achado mais forte para ligar "
        "investigados entre si), buscar_anos_ipei para o consolidado ano a ano (a razão "
        "movimentação financeira / rendimento declarado é o indicador central de incompatibilidade), "
        "buscar_contas_ipei para as contas da e-Financeira mês a mês, buscar_bens_ipei para bens, "
        "veículos e imóveis, e buscar_notas_ipei para as notas fiscais eletrônicas. Extração por "
        "regex sobre o PDF e a planilha de anexo (sem IA): seção que o relatório traz como 'Nada "
        "consta' simplesmente não tem linhas aqui, e uma tabela que o extrator de PDF embaralhou "
        "pode ter vindo incompleta — se a pergunta depender de algo que você não encontra, diga que "
        "a informação não está disponível em vez de inventar. As ferramentas já estão presas a esta "
        "IPEI: você não tem acesso a outras quebras de sigilo, RIFs, IPLs, extratos do SIMBA ou ao "
        "conteúdo do aparelho deste inquérito — não afirme nada sobre o que não pode "
        "ver.\n\n" + _METODO_INVESTIGATIVO + _RESUMO_METODO
    ),
    "telefonia": (
        "Você é um analista de investigação criminal ajudando um investigador a analisar o retorno "
        "de UMA ÚNICA OPERADORA na quebra de sigilo telefônico deste inquérito — não as demais "
        "operadoras nem o inquérito inteiro. Comece por obter_telefonia_operadora, que diz o que "
        "existe: quais arquivos entraram, que período cobrem e qual linha foi pedida no ofício. "
        "Use buscar_linhas_telefonia para o cadastro (titular, CPF, endereço, pré/pós, habilitação "
        "e rescisão — é o que responde de quem é um número), buscar_chamadas_telefonia para o CDR "
        "(com filtro por número, período e só-completadas, e já com o endereço da antena resolvido; "
        "o filtro `cgi` lista as chamadas de uma antena específica — é o que responde 'qual linha "
        "usou esta antena'), "
        "buscar_erbs_telefonia para as antenas ordenadas por movimento (onde a pessoa fica × por "
        "onde só passou — o campo `linhas_alvo` diz quais linhas pedidas no ofício usaram cada "
        "antena), buscar_numeros_telefonia para o ranking de interlocutores — nesta "
        "última, o filtro por IMEI acha números diferentes usando o MESMO aparelho, que é o achado "
        "mais forte desta base — e buscar_dados_moveis_telefonia para as sessões de dados (em que "
        "intervalo a linha ficou presa a cada antena, com que IP e volumes): o paradeiro por dados, "
        "complementar ao CDR de voz.\n\n"
        "Três cuidados que valem para todo CDR e que você deve respeitar nas respostas: chamada "
        "'Nao Compl.' é tentativa, não conversa, e contar as duas juntas infla qualquer estatística; "
        "a antena diz a região da torre, não a posição da pessoa, então fale em 'esteve na área "
        "coberta por', nunca em 'estava no endereço'; e o período é o que o ofício pediu — ausência "
        "de chamada fora dele não significa que não houve. O que a operadora não mandou (uma seção "
        "que voltou 'não há registro', um IMEI zerado) simplesmente não está aqui: diga que a "
        "informação não veio, em vez de inventar. As ferramentas já estão presas a esta operadora: "
        "você não tem acesso ao retorno de outras operadoras, a RIFs, IPLs, extratos do SIMBA ou ao "
        "conteúdo do aparelho deste inquérito — não afirme nada sobre o que não pode "
        "ver.\n\n" + _METODO_INVESTIGATIVO + _RESUMO_METODO
    ),
    "extracao": (
        "Você é um analista de investigação criminal ajudando um investigador a explorar os dados "
        "extraídos de UM ÚNICO aparelho (extração UFDR) deste inquérito — não os demais aparelhos, "
        "nem RIFs/extratos do SIMBA/IPLs anexados. Contatos e mensagens de WhatsApp/SMS/outros "
        "mensageiros (com a transcrição dos áudios no campo transcricao_audio — mensagem de voz não "
        "tem texto), ligações, localizações, notificações, notas, navegação, aplicativos instalados, "
        "dono(s) do aparelho, chaves PIX citadas nas conversas, redes Wi-Fi conhecidas, e-mails, contas "
        "bancárias/financeiras e contas de aplicativo logadas — tudo já filtrado para este aparelho. "
        "As ferramentas já estão presas a esta extração: você não tem acesso a outros aparelhos do "
        "mesmo inquérito, nem a RIFs, extratos do SIMBA ou IPLs — não afirme nada sobre o que não "
        "pode ver.\n\n" + _METODO_INVESTIGATIVO + _RESUMO_METODO
    ),
    "documento": (
        "Você é um analista de investigação criminal ajudando um investigador a analisar UM ÚNICO "
        "documento anexado ao inquérito (PDF, CSV, XLSX, DOCX, TXT ou HTML extraído na tela de "
        "investigação — não o inquérito inteiro). Use ler_documento_inquerito para ler o texto "
        "extraído do documento (a ferramenta já está presa a este documento: você não vê outros "
        "documentos, RIFs, extratos do SIMBA, IPLs ou o conteúdo do aparelho deste inquérito — "
        "não afirme nada sobre o que não pode ver). Resuma o documento, destaque nomes, datas, "
        "valores e fatos relevantes para a investigação.\n\n"
        "Ao citar, marque a página: o texto vem com linhas [[página N]] indicando onde cada página "
        "do arquivo começa, e o marcador [doc:ID:N] abre o documento já naquela página para o "
        "investigador. Use o N dessas linhas, nunca o número impresso no rodapé do papel — esse é "
        "a folha do processo e não corresponde à página do arquivo. Documento sem essas linhas "
        "não tem página; cite só [doc:ID].\n\n" + _METODO_INVESTIGATIVO + _RESUMO_METODO
    ),
}

# Escopo de mensageiro (um app só, de um aparelho ou do inquérito inteiro) e de conversa única
# (uma thread específica) — vêm do painel flutuante em whatsapp.html/mensageiros.html. Gerados em
# laço porque os três mensageiros (WhatsApp/SMS/outros) têm o mesmo texto, só trocando o nome.
_NOME_MENSAGEIRO = {
    "wpp": "WhatsApp", "sms": "SMS",
    "msg": "outros mensageiros (Instagram, Telegram, Facebook, Discord, TikTok, Twitter etc.)",
}
for _fonte, _nome_fonte in _NOME_MENSAGEIRO.items():
    SISTEMA_PROMPT_ESCOPO[_fonte] = (
        f"Você é um analista de investigação criminal ajudando um investigador a explorar só as "
        f"mensagens de {_nome_fonte} deste inquérito — não outros mensageiros, nem outros tipos de "
        f"dado do aparelho (ligações, localizações, notas etc.), nem RIFs/extratos do SIMBA/IPLs. Use "
        f"buscar_mensagens para procurar por conteúdo e/ou pelo nome do contato (já vem restrito a "
        f"este mensageiro). Não afirme nada sobre o que não pode ver.\n\n"
        + _METODO_INVESTIGATIVO + _RESUMO_METODO
    )
    SISTEMA_PROMPT_ESCOPO["conversa_" + _fonte] = (
        f"Você é um analista de investigação criminal ajudando um investigador a analisar UMA ÚNICA "
        f"conversa de {_nome_fonte} — não outras conversas, contatos, ligações ou qualquer outro dado "
        f"deste inquérito. Use buscar_mensagens para ler o conteúdo (já vem restrito a esta conversa). "
        f"Não afirme nada sobre o que não pode ver.\n\n" + _METODO_INVESTIGATIVO + _RESUMO_METODO
    )
del _fonte, _nome_fonte

SISTEMA_PROMPT_LOTE = (
    "Você é um analista de investigação criminal analisando UM RECORTE POR PERÍODO do conteúdo extraído "
    "de um inquérito (o inquérito é grande e foi dividido por data). Analise só o que está neste recorte "
    "e extraia o que for relevante para a pergunta — trechos literais, nomes, datas, valores, com o "
    "marcador [msg:wpp:ID]/[msg:sms:ID] de cada mensagem citada. Não conclua ainda de forma definitiva: "
    "outra etapa vai juntar os recortes.\n\n" + _METODO_INVESTIGATIVO
)

SISTEMA_PROMPT_SINTESE = (
    "Você é um analista de investigação criminal. O inquérito é grande e foi analisado em partes, por "
    "período. As análises parciais de cada período seguem abaixo. Combine-as numa resposta única e "
    "coerente à pergunta do investigador, sem repetir, PRESERVANDO os trechos, nomes, datas e os "
    "marcadores [msg:wpp:ID]/[msg:sms:ID] citados. Organize em Achados → Evidências → Lacunas → Próximas "
    "diligências quando fizer sentido. Use as ferramentas só para o que faltar. Responda em português."
)

_INTRO_RESPOSTA_PROMPT = (
    "**Prompt completo** — não enviado à IA para gerar resposta. "
    "Copie o bloco abaixo e cole em outra IA, se quiser.\n\n```\n"
)
_INTRO_RESPOSTA_PROMPT_CORTADO = (
    "**Prompt completo** — não enviado à IA para gerar resposta. Com o conteúdo do inquérito "
    "anexado ele ficou grande demais para caber na conversa ({total} caracteres): abaixo vai só "
    "o começo. **O texto íntegro está no .md** do link no fim desta mensagem.\n\n```\n"
)
_FIM_BLOCO_PROMPT = "\n```"
# Acima disso o prompt não vai inteiro para a bolha do chat: a conversa é recarregada do banco a
# cada envio e trafegar/renderizar megabytes de texto trava o navegador. O .md não tem esse teto.
_MAX_CHARS_PROMPT_INLINE = 60_000


def _limpar_prompts_antigos():
    """Apaga arquivos .md com mais de 30 dias e seus registros no banco."""
    from datetime import datetime, timedelta
    from pathlib import Path

    from funcoes.db_utils import get_connection

    marco = datetime.now() - timedelta(days=30)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id_arquivo_prompt, caminho_arquivo "
                "FROM investigacao.ia_tb04_arquivos_prompt "
                "WHERE dt_criacao < %s",
                (marco,),
            )
            antigos = cur.fetchall()
            if not antigos:
                return
            for id_arq, caminho in antigos:
                try:
                    Path(caminho).unlink(missing_ok=True)
                except Exception:
                    pass
            cur.execute(
                "DELETE FROM investigacao.ia_tb04_arquivos_prompt "
                "WHERE dt_criacao < %s",
                (marco,),
            )
        conn.commit()


def _salvar_prompt_para_analise(
    id_conversa_ia: int, id_inquerito: int, id_usuario: int,
    titulo: str, conteudo_prompt: str, modo: str = "ia"
) -> dict:
    """Salva o prompt completo em um arquivo .md para interpretação posterior.

    Args:
        id_conversa_ia: ID da conversa de IA
        id_inquerito: ID do inquérito
        id_usuario: ID do usuário que gerou o prompt
        titulo: Título da conversa/prompt
        conteudo_prompt: Conteúdo completo do prompt
        modo: Modo do prompt (ia/inquerito)

    Returns:
        Dict com {id_arquivo_prompt, nome_arquivo, caminho_arquivo}
    """
    from datetime import datetime
    from pathlib import Path

    from funcoes.db_utils import get_connection

    # Limpeza lazy de arquivos antigos (>30 dias)
    _limpar_prompts_antigos()

    # Formata o título para nome de arquivo
    titulo_arquivo = re.sub(r'[^\w\s-]', '', titulo).strip().replace(' ', '_')
    if not titulo_arquivo:
        titulo_arquivo = "prompt_sem_titulo"

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    nome_arquivo = f"{timestamp}_{titulo_arquivo}_{id_conversa_ia}.md"
    
    # Salva o conteúdo do prompt
    conteudo_com_metadados = (
        f"# Prompt de Análise de Investigação\n\n"
        f"**Modo**: {modo}\n"
        f"**Inquérito**: {id_inquerito}\n"
        f"**Conversa IA**: {id_conversa_ia}\n"
        f"**Gerado por**: Usuário {id_usuario}\n"
        f"**Data/Hora**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"## Conteúdo do Prompt\n\n"
        f"```\n{conteudo_prompt}\n```\n"
    )

    # Caminho para a pasta de análises
    pasta_analises = Path("data/analises_prompts")
    pasta_analises.mkdir(parents=True, exist_ok=True)
    
    caminho_arquivo = pasta_analises / nome_arquivo
    caminho_arquivo.write_text(conteudo_com_metadados, encoding="utf-8")
    
    # Registra o arquivo no banco de dados
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO investigacao.ia_tb04_arquivos_prompt (
                    id_conversa_ia, id_inquerito, id_usuario, 
                    no_arquivo, caminho_arquivo, dt_criacao
                ) VALUES (%s, %s, %s, %s, %s, now())
                RETURNING id_arquivo_prompt
                """,
                (
                    id_conversa_ia, id_inquerito, id_usuario,
                    nome_arquivo, str(caminho_arquivo)
                ),
            )
            id_arquivo_prompt = cur.fetchone()[0]
        conn.commit()
    
    return {
        "id_arquivo_prompt": id_arquivo_prompt,
        "nome_arquivo": nome_arquivo,
        "caminho_arquivo": str(caminho_arquivo),
    }


def _empacotar_resposta_prompt(texto_prompt: str) -> str:
    """Envolve o prompt serializado num bloco de código markdown, fácil de copiar.

    Prompt grande demais (o normal desde que o conteúdo do inquérito passou a ir anexado) entra
    cortado NA EXIBIÇÃO — ver _MAX_CHARS_PROMPT_INLINE. O .md gerado ao lado guarda o prompt
    inteiro, e é ele o caminho de copiar/colar nesse caso."""
    if len(texto_prompt) > _MAX_CHARS_PROMPT_INLINE:
        total = f"{len(texto_prompt):,}".replace(",", ".")
        return (
            _INTRO_RESPOSTA_PROMPT_CORTADO.format(total=total)
            + texto_prompt[:_MAX_CHARS_PROMPT_INLINE]
            + "\n\n[... corte da exibição — o restante está no arquivo .md]"
            + _FIM_BLOCO_PROMPT
        )
    return _INTRO_RESPOSTA_PROMPT + texto_prompt + _FIM_BLOCO_PROMPT


_TABELA_ESCOPO = {
    "rif": ("investigacao.rif_tb01_rifs", "id_rif"),
    "simba": ("investigacao.smb_tb01_extratos", "id_extrato"),
    "ipl": ("investigacao.ipl_tb01_inqueritos_pdf", "id_ipl"),
    "irpf": ("investigacao.irp_tb01_declaracoes", "id_irpf"),
    "ipei": ("investigacao.ipe_tb01_ipeis", "id_ipei"),
    "telefonia": ("investigacao.tel_tb01_telefonia", "id_telefonia"),
    "extracao": ("investigacao.inq_tb02_extracoes", "id_extracao"),
    "documento": ("investigacao.inq_tb04_documentos", "id_documento"),
    "wpp": ("investigacao.inq_tb02_extracoes", "id_extracao"),
    "sms": ("investigacao.inq_tb02_extracoes", "id_extracao"),
    "msg": ("investigacao.inq_tb02_extracoes", "id_extracao"),
}

# Escopo de UMA conversa (thread) de mensageiro — a tabela não tem id_inquerito direto, então a
# validação passa pelo id_extracao (JOIN com inq_tb02_extracoes).
_TABELA_CONVERSA_ESCOPO = {
    "conversa_wpp": "investigacao.wpp_tb02_conversas",
    "conversa_sms": "investigacao.sms_tb02_conversas",
    "conversa_msg": "investigacao.msg_tb02_conversas",
}

# "wpp"/"sms"/"msg" aceitam id_escopo vazio (nenhum aparelho específico selecionado — restringe
# só ao mensageiro, em todo o inquérito); os demais exigem um id_escopo de verdade.
_ESCOPO_ACEITA_SEM_DOCUMENTO = {"wpp", "sms", "msg"}


def _validar_escopo_documento(id_inquerito: int, tp_escopo: str, id_escopo: int) -> None:
    """Confere que o documento (RIF/extrato/IPL/extração) existe e pertence a este inquérito,
    antes de prender a conversa a ele — sem isso, um id de outro inquérito vazaria dado entre casos."""
    tabela, coluna = _TABELA_ESCOPO[tp_escopo]
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT 1 FROM {tabela} WHERE {coluna} = %s AND id_inquerito = %s",
                (id_escopo, id_inquerito),
            )
            if not cur.fetchone():
                raise ErroProvedorIA("Documento de escopo não encontrado neste inquérito.")


def _validar_escopo_conversa(id_inquerito: int, tp_escopo: str, id_escopo: int) -> None:
    """Confere que a conversa (thread de WhatsApp/SMS/mensageiro) existe e pertence, pelo aparelho
    dela, a este inquérito — mesma razão de _validar_escopo_documento."""
    tabela = _TABELA_CONVERSA_ESCOPO[tp_escopo]
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT 1 FROM {tabela} c
                JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = c.id_extracao
                WHERE c.id_conversa = %s AND e.id_inquerito = %s
                """,
                (id_escopo, id_inquerito),
            )
            if not cur.fetchone():
                raise ErroProvedorIA("Conversa de escopo não encontrada neste inquérito.")


def criar_conversa(
    id_inquerito: int, titulo: str | None = None, id_usuario: int | None = None,
    tp_escopo: str | None = None, id_escopo: int | None = None,
) -> int:
    """Cria uma nova conversa de IA para o inquérito; devolve o ID.

    tp_escopo/id_escopo (opcionais) prendem a conversa a um único RIF/extrato do SIMBA/IPL/
    aparelho/mensageiro/conversa — ver ia_investigacao.ferramentas_para_inquerito, que usa isso
    para restringir as ferramentas expostas à IA nesta conversa."""
    if tp_escopo is not None:
        if tp_escopo in _TABELA_CONVERSA_ESCOPO:
            if not id_escopo:
                raise ErroProvedorIA("Escopo de conversa inválido.")
            _validar_escopo_conversa(id_inquerito, tp_escopo, id_escopo)
        elif tp_escopo in _TABELA_ESCOPO:
            if not id_escopo and tp_escopo not in _ESCOPO_ACEITA_SEM_DOCUMENTO:
                raise ErroProvedorIA("Escopo de conversa inválido.")
            if id_escopo:
                _validar_escopo_documento(id_inquerito, tp_escopo, id_escopo)
        else:
            raise ErroProvedorIA("Escopo de conversa inválido.")
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO investigacao.ia_tb01_conversas
                    (id_inquerito, no_titulo, id_usuario_criador, tp_escopo, id_escopo)
                VALUES (%s, %s, %s, %s, %s) RETURNING id_conversa_ia
                """,
                (id_inquerito, titulo, id_usuario, tp_escopo, id_escopo),
            )
            id_conversa = cur.fetchone()[0]
        conn.commit()
    return id_conversa


def _apagar_conversas_vazias_antigas(id_inquerito: int, preservar: int | None = None) -> None:
    """Apaga as conversas de IA deste inquérito que continuam sem nenhuma mensagem passados
    _MINUTOS_CONVERSA_VAZIA minutos da criação. Limpeza preguiçosa: não há agendador no projeto e
    toda tela de IA (e o painel flutuante) lista as conversas ao abrir — ver listar_conversas_ia.

    `preservar` é a conversa aberta agora na tela de quem pediu a lista: nunca é apagada, mesmo
    vazia e velha, porque o investigador pode estar redigindo a primeira mensagem sem ter enviado.
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.id_conversa_ia
                FROM investigacao.ia_tb01_conversas c
                WHERE c.id_inquerito = %s
                  AND c.dt_criacao < now() - make_interval(mins => %s)
                  AND NOT EXISTS (
                      SELECT 1 FROM investigacao.ia_tb02_mensagens m
                      WHERE m.id_conversa_ia = c.id_conversa_ia
                  )
                """,
                (id_inquerito, _MINUTOS_CONVERSA_VAZIA),
            )
            ids = [linha[0] for linha in cur.fetchall()]
    for id_conversa in ids:
        if id_conversa == preservar:
            continue
        try:
            apagar_conversa_ia(id_conversa)
        except Exception:
            logger.exception("falha ao apagar conversa de IA vazia %s", id_conversa)


def listar_conversas_ia(id_inquerito: int, preservar: int | None = None) -> list[dict]:
    """Lista as conversas de IA de um inquérito, a de atividade mais recente primeiro, com
    contagem e data da última mensagem.

    Antes de listar, apaga as conversas vazias e antigas (ver _apagar_conversas_vazias_antigas);
    `preservar` é a conversa aberta agora na tela de quem chama, poupada dessa limpeza."""
    _apagar_conversas_vazias_antigas(id_inquerito, preservar)
    query = """
        SELECT c.id_conversa_ia, c.no_titulo, c.dt_criacao,
               c.id_usuario_criador, u.no_usuario AS criado_por,
               c.tp_escopo, c.id_escopo, mc.tp_app AS no_app,
               msg.qt_mensagens, msg.dt_ultima_mensagem
        FROM investigacao.ia_tb01_conversas c
        LEFT JOIN investigacao.msg_tb02_conversas mc
               ON c.tp_escopo = 'conversa_msg' AND mc.id_conversa = c.id_escopo
        LEFT JOIN seguranca.usr_tb01_usuarios u ON u.id_usuario = c.id_usuario_criador
        LEFT JOIN LATERAL (
            SELECT count(*) AS qt_mensagens, max(m.dt_mensagem) AS dt_ultima_mensagem
            FROM investigacao.ia_tb02_mensagens m
            WHERE m.id_conversa_ia = c.id_conversa_ia
        ) msg ON true
        WHERE c.id_inquerito = %s
        ORDER BY COALESCE(msg.dt_ultima_mensagem, c.dt_criacao) DESC, c.id_conversa_ia DESC
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (id_inquerito,))
            colunas = [d.name for d in cur.description]
            return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


def listar_mensagens_ia(id_conversa_ia: int) -> list[dict]:
    """Lista as mensagens de uma conversa de IA, em ordem cronológica.

    Cada resposta do assistente vem com o gasto do próprio turno (tokens, tempo e custo estimado),
    lido da auditoria pela ID_MENSAGEM — é o que a bolha mostra ao lado do modelo. Nulo nas
    respostas anteriores à migração 0018 que o backfill não conseguiu casar, e nas do modo
    "não enviar+exibir prompt" (nada foi para o provedor)."""
    query = """
        SELECT m.id_mensagem_ia, m.tp_papel, m.tx_conteudo, m.no_modelo, m.dt_mensagem,
               m.tx_raciocinio, m.co_jobs, u.no_usuario AS enviado_por,
               a.qt_tokens_entrada, a.qt_tokens_saida, a.in_tokens_estimados,
               a.nu_tempo_ms, a.vl_custo_usd, a.qt_chars_contexto
        FROM investigacao.ia_tb02_mensagens m
        LEFT JOIN seguranca.usr_tb01_usuarios u ON u.id_usuario = m.id_usuario
        -- LATERAL com LIMIT 1 (e não um LEFT JOIN direto): se um dia dois registros de auditoria
        -- apontarem para a mesma mensagem, a listagem não duplica a bolha
        LEFT JOIN LATERAL (
            SELECT aud.qt_tokens_entrada, aud.qt_tokens_saida, aud.in_tokens_estimados,
                   aud.nu_tempo_ms, aud.vl_custo_usd, aud.qt_chars_contexto
            FROM investigacao.ia_tb04_auditoria aud
            WHERE aud.id_mensagem = m.id_mensagem_ia
            ORDER BY aud.id_auditoria DESC
            LIMIT 1
        ) a ON true
        WHERE m.id_conversa_ia = %s
        ORDER BY m.dt_mensagem, m.id_mensagem_ia
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (id_conversa_ia,))
            colunas = [d.name for d in cur.description]
            return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


def _gravar_mensagem(
    cur, id_conversa_ia: int, papel: str, conteudo: str, modelo: str | None, id_usuario: int | None = None,
    raciocinio: list[dict] | None = None, co_jobs: list[dict] | None = None,
) -> int:
    # id_usuario fica nulo em mensagens do assistente — não foi "enviada" por um humano
    # co_jobs (só no assistente): jobs pesados que o turno disparou, para o cartão de progresso (0045)
    cur.execute(
        """
        INSERT INTO investigacao.ia_tb02_mensagens
            (id_conversa_ia, tp_papel, tx_conteudo, no_modelo, id_usuario, tx_raciocinio, co_jobs)
        VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id_mensagem_ia
        """,
        (id_conversa_ia, papel, conteudo, modelo, id_usuario,
         json.dumps(raciocinio, ensure_ascii=False, default=str) if raciocinio else None,
         json.dumps(co_jobs, ensure_ascii=False) if co_jobs else None),
    )
    return cur.fetchone()[0]


def apagar_conversa_ia(id_conversa_ia: int) -> bool:
    """Apaga uma conversa de IA, suas mensagens e processamentos; False se a conversa não existir."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            # processamentos primeiro: têm FK para as mensagens e para a conversa
            cur.execute("DELETE FROM investigacao.ia_tb03_processamentos WHERE id_conversa_ia = %s", (id_conversa_ia,))
            cur.execute("DELETE FROM investigacao.ia_tb02_mensagens WHERE id_conversa_ia = %s", (id_conversa_ia,))
            cur.execute("DELETE FROM investigacao.ia_tb04_arquivos_prompt WHERE id_conversa_ia = %s", (id_conversa_ia,))
            apagar_relatorios_da_conversa(cur, id_conversa_ia)  # .docx gerados a pedido de relatório
            apagar_planilhas_da_conversa(cur, id_conversa_ia)  # .xlsx do job de consolidação
            cur.execute("DELETE FROM investigacao.ia_tb01_conversas WHERE id_conversa_ia = %s", (id_conversa_ia,))
            apagou = cur.rowcount > 0
        conn.commit()
    return apagou


def renomear_conversa_ia(id_conversa_ia: int, titulo: str) -> bool:
    """Troca o título de uma conversa de IA; False se a conversa não existir."""
    titulo = (titulo or "").strip()[:200]
    if not titulo:
        raise ErroProvedorIA("Título inválido.")
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE investigacao.ia_tb01_conversas SET no_titulo = %s WHERE id_conversa_ia = %s",
                (titulo, id_conversa_ia),
            )
            renomeou = cur.rowcount > 0
        conn.commit()
    return renomeou


def duplicar_conversa_ia(id_conversa_origem: int, id_usuario_criador: int | None = None) -> int:
    """Duplica uma conversa de IA (exportar 1-clique): nova conversa com o mesmo escopo e
    cópia das mensagens, sem processamentos/auditoria/feedbacks — a cópia recomeça limpa.

    O título ganha o prefixo "Cópia de " (truncado no limite da API); conversa sem título
    vira só "Cópia". ErroProvedorIA se a origem não existir."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id_inquerito, no_titulo, tp_escopo, id_escopo
                FROM investigacao.ia_tb01_conversas WHERE id_conversa_ia = %s
                """,
                (id_conversa_origem,),
            )
            origem = cur.fetchone()
            if not origem:
                raise ErroProvedorIA("Conversa de IA não encontrada.")
            id_inquerito, titulo_origem, tp_escopo, id_escopo = origem
            titulo_novo = ("Cópia de " + (titulo_origem or "")).strip()[:200] or "Cópia"
            cur.execute(
                """
                INSERT INTO investigacao.ia_tb01_conversas
                    (id_inquerito, no_titulo, id_usuario_criador, tp_escopo, id_escopo)
                VALUES (%s, %s, %s, %s, %s) RETURNING id_conversa_ia
                """,
                (id_inquerito, titulo_novo, id_usuario_criador, tp_escopo, id_escopo),
            )
            id_nova = cur.fetchone()[0]
            # copia direta por INSERT..SELECT: preserva papel/conteúdo/modelo/autor/trilha e a
            # ordem cronológica, sem passar pelo _gravar_mensagem (que re-serializaria o JSONB)
            cur.execute(
                """
                INSERT INTO investigacao.ia_tb02_mensagens
                    (id_conversa_ia, tp_papel, tx_conteudo, no_modelo, id_usuario, tx_raciocinio, dt_mensagem)
                SELECT %s, tp_papel, tx_conteudo, no_modelo, id_usuario, tx_raciocinio, dt_mensagem
                FROM investigacao.ia_tb02_mensagens
                WHERE id_conversa_ia = %s
                ORDER BY dt_mensagem, id_mensagem_ia
                """,
                (id_nova, id_conversa_origem),
            )
        conn.commit()
    return id_nova


def obter_id_conversa_de_mensagem_ia(id_mensagem_ia: int) -> int | None:
    """Resolve a conversa dona de uma mensagem de IA, para checar permissão em rotas que só
    recebem id_mensagem_ia. None se a mensagem não existir."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id_conversa_ia FROM investigacao.ia_tb02_mensagens WHERE id_mensagem_ia = %s",
                (id_mensagem_ia,),
            )
            row = cur.fetchone()
            return row[0] if row else None


def registrar_feedback_mensagem(
    id_mensagem_ia: int, id_usuario: int, tp_feedback: str | None
) -> dict:
    """Registra (ou remove, com None) o voto 👍/👎 de um usuário numa resposta da IA.

    Um voto por usuário e mensagem: votar no mesmo sentido mantém, no oposto troca.
    Só vale para mensagem do assistente. Devolve {positivo, negativo, meu} já agregados."""
    if tp_feedback is not None and tp_feedback not in ("positivo", "negativo"):
        raise ErroProvedorIA("Feedback inválido.")
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT tp_papel FROM investigacao.ia_tb02_mensagens WHERE id_mensagem_ia = %s",
                (id_mensagem_ia,),
            )
            row = cur.fetchone()
            if not row:
                raise ErroProvedorIA("Mensagem de IA não encontrada.")
            if row[0] != "assistant":
                raise ErroProvedorIA("Feedback só para respostas da IA.")
            if tp_feedback is None:
                cur.execute(
                    "DELETE FROM investigacao.ia_tb06_feedback WHERE id_mensagem_ia = %s AND id_usuario = %s",
                    (id_mensagem_ia, id_usuario),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO investigacao.ia_tb06_feedback (id_mensagem_ia, id_usuario, tp_feedback)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (id_mensagem_ia, id_usuario)
                    DO UPDATE SET tp_feedback = EXCLUDED.tp_feedback, dt_criacao = now()
                    """,
                    (id_mensagem_ia, id_usuario, tp_feedback),
                )
            cur.execute(
                """
                SELECT
                    COUNT(*) FILTER (WHERE tp_feedback = 'positivo'),
                    COUNT(*) FILTER (WHERE tp_feedback = 'negativo'),
                    MAX(tp_feedback) FILTER (WHERE id_usuario = %s)
                FROM investigacao.ia_tb06_feedback WHERE id_mensagem_ia = %s
                """,
                (id_usuario, id_mensagem_ia),
            )
            qt_pos, qt_neg, meu = cur.fetchone()
        conn.commit()
    return {"positivo": int(qt_pos or 0), "negativo": int(qt_neg or 0), "meu": meu}


def obter_feedbacks_conversa(id_conversa_ia: int, id_usuario_atual: int | None = None) -> dict:
    """Agregado de 👍/👎 por mensagem da conversa: {id_mensagem_ia: {positivo, negativo, meu}}.

    `meu` é o voto do usuário atual (ou None). Mensagem sem voto não aparece no mapa."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT f.id_mensagem_ia,
                       COUNT(*) FILTER (WHERE f.tp_feedback = 'positivo'),
                       COUNT(*) FILTER (WHERE f.tp_feedback = 'negativo'),
                       MAX(f.tp_feedback) FILTER (WHERE f.id_usuario = %s)
                FROM investigacao.ia_tb06_feedback f
                JOIN investigacao.ia_tb02_mensagens m ON m.id_mensagem_ia = f.id_mensagem_ia
                WHERE m.id_conversa_ia = %s
                GROUP BY f.id_mensagem_ia
                """,
                (id_usuario_atual, id_conversa_ia),
            )
            mapa = {}
            for id_msg, qt_pos, qt_neg, meu in cur.fetchall():
                mapa[int(id_msg)] = {
                    "positivo": int(qt_pos or 0),
                    "negativo": int(qt_neg or 0),
                    "meu": meu,
                }
            return mapa


def obter_id_inquerito_de_conversa_ia(id_conversa_ia: int) -> int | None:
    """Resolve o id_inquerito dono de uma conversa de IA, para checar permissão em rotas que só
    recebem id_conversa_ia. Devolve None se a conversa não existir."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id_inquerito FROM investigacao.ia_tb01_conversas WHERE id_conversa_ia = %s",
                (id_conversa_ia,),
            )
            row = cur.fetchone()
            return row[0] if row else None


def _buscar_id_inquerito(id_conversa_ia: int) -> int:
    id_inquerito = obter_id_inquerito_de_conversa_ia(id_conversa_ia)
    if id_inquerito is None:
        raise ErroProvedorIA("Conversa de IA não encontrada.")
    return id_inquerito


def _buscar_escopo_conversa(id_conversa_ia: int) -> tuple[str | None, int | None]:
    """(tp_escopo, id_escopo) da conversa — (None, None) para conversa normal (inquérito inteiro)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT tp_escopo, id_escopo FROM investigacao.ia_tb01_conversas WHERE id_conversa_ia = %s",
                (id_conversa_ia,),
            )
            row = cur.fetchone()
            return (row[0], row[1]) if row else (None, None)


def _bloco_contexto_caso(id_inquerito: int) -> str:
    """Bloco com o contexto livre da investigação (informado pelo investigador), para prepender ao
    prompt de sistema. Vazio se não houver contexto cadastrado."""
    from funcoes.investigacao_utils import obter_contexto_investigacao

    ctx = (obter_contexto_investigacao(id_inquerito) or "").strip()
    if not ctx:
        return ""
    return "\n\n## CONTEXTO DA INVESTIGAÇÃO (informado pelo investigador — leve isto em conta)\n" + ctx


def _panorama_curto(id_inquerito: int) -> str:
    """Dono(s) + resumo do inquérito (sem a lista de apps), para dar contexto mínimo ao modo normal."""
    return "\n\n## PANORAMA DO APARELHO\n" + _cabecalho_inquerito(id_inquerito, com_apps=False)


def _rotulo_item_escopo(id_inquerito: int, tp: str, id_: int) -> str:
    """Rótulo amigável de um item da multisseleção (ex.: 'RIF 2024.001', 'Aparelho Galaxy S20')."""
    try:
        if tp == "rif":
            from funcoes.rif_utils import listar_rifs_inquerito
            for r in listar_rifs_inquerito(id_inquerito):
                if r["id_rif"] == id_:
                    return f"RIF {r.get('nu_rif') or r.get('no_pasta') or id_}"
        elif tp == "simba":
            from funcoes.simba_utils import listar_extratos_inquerito
            for s in listar_extratos_inquerito(id_inquerito):
                if s["id_extrato"] == id_:
                    return f"Extrato SIMBA {s.get('no_arquivo') or id_}"
        elif tp == "ipl":
            from funcoes.ipl_utils import listar_ipls_inquerito
            for p in listar_ipls_inquerito(id_inquerito):
                if p["id_ipl"] == id_:
                    return f"IPL {p.get('nu_ipl') or p.get('no_arquivo') or id_}"
        elif tp == "extracao":
            from funcoes.investigacao_utils import listar_extracoes_do_inquerito
            for x in listar_extracoes_do_inquerito(id_inquerito):
                if x["id_extracao"] == id_:
                    return f"Aparelho {x.get('no_dispositivo') or x.get('no_fabricante') or x.get('no_arquivo') or id_}"
        elif tp == "documento":
            from funcoes.ia_investigacao import listar_documentos_inquerito
            for d in listar_documentos_inquerito(id_inquerito):
                if d["id_documento"] == id_:
                    return f"Documento {d.get('no_arquivo') or id_}"
    except Exception:
        pass
    return f"{tp} {id_}"


def _bloco_escopos_selecionados(id_inquerito: int, escopos: list[dict]) -> str:
    """Bloco do prompt de sistema avisando a IA de que esta mensagem está restrita aos itens
    selecionados na multisseleção do chat geral (substitui o panorama do inquérito inteiro)."""
    itens = [_rotulo_item_escopo(id_inquerito, e["tp_escopo"], e["id_escopo"]) for e in escopos]
    return (
        "\n\n## RESTRIÇÃO DE ESCOPO (selecionada pelo investigador nesta mensagem)\n"
        "Esta mensagem considera SOMENTE os itens abaixo — não use ferramentas nem cite conteúdo "
        "de outros RIFs, extratos SIMBA, IPLs, documentos ou aparelhos deste inquérito:\n- "
        + "\n- ".join(itens)
    )


def _historico(id_conversa_ia: int, auditoria=None, on_etapa=None, checkpoint=None, compactar: bool = True,
               anonimizador=None, id_usuario: int | None = None):
    """(bloco de resumo para o prompt de sistema, mensagens) — ver funcoes.ia_historico.

    Conversa curta: bloco vazio e todas as mensagens, como sempre foi. Conversa que passou do teto
    da janela: as antigas viram um resumo (gravado na conversa, reaproveitado nos próximos turnos)
    e só as últimas mensagens vão na íntegra."""
    return montar_historico(
        id_conversa_ia, *_TABELAS_HISTORICO,
        auditoria=auditoria, on_etapa=on_etapa, checkpoint=checkpoint, compactar=compactar,
        anonimizador=anonimizador, id_usuario=id_usuario,
    )


def _tokens_historico(id_conversa_ia: int) -> int:
    """Estimativa grosseira de tokens já ocupados pelo histórico da conversa."""
    return tokens_historico(id_conversa_ia, *_TABELAS_HISTORICO)


def _gravar_resposta(
    id_conversa_ia: int, texto: str, modelo: str | None, raciocinio: list[dict] | None = None,
    co_jobs: list[dict] | None = None,
) -> int:
    """Grava a mensagem do assistente (com a trilha do turno e os jobs que ele disparou, quando
    houver); devolve o id. co_jobs alimenta o cartão de progresso do OCR na bolha."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            id_mensagem = _gravar_mensagem(
                cur, id_conversa_ia, "assistant", texto, modelo, raciocinio=raciocinio,
                co_jobs=co_jobs,
            )
        conn.commit()
    return id_mensagem


def _liberar_processamentos_travados(id_conversa_ia: int) -> None:
    """Marca como 'erro' os processamentos desta conversa presos há muito tempo (servidor caiu no
    meio de um job), liberando a trava por conversa para um novo envio."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE investigacao.ia_tb03_processamentos
                SET cd_status = 'erro', tx_erro = 'Processamento interrompido (liberado por inatividade).', dt_fim = now()
                WHERE id_conversa_ia = %s AND cd_status IN ('pendente', 'processando')
                  AND dt_inicio < now() - make_interval(mins => %s)
                """,
                (id_conversa_ia, _MINUTOS_TRAVA_STALE),
            )
        conn.commit()


def enviar_mensagem_usuario(
    id_conversa_ia: int, texto: str, id_usuario: int | None = None,
    incluir_conteudo: bool = False, exibir_prompt: bool = False,
    escopos: list[dict] | None = None, contexto_tela: dict | None = None,
) -> dict:
    """Grava a mensagem do usuário e dispara a resposta da IA em background, devolvendo na hora
    {'modo': 'async', 'id_processamento', 'qt_lotes'} para o front acompanhar por polling.

    escopos (multisseleção do chat geral): lista de {tp_escopo, id_escopo} com 'rif'/'simba'/'ipl'/
    'extracao'/'documento' — restringe esta mensagem aos itens escolhidos (ferramentas + conteúdo anexado).
    Ignorado em conversa que já tem escopo próprio (o escopo da conversa prevalece).

    contexto_tela: painel de filtros da tela de grafo aberta, quando a pergunta vem de uma delas
    (ver funcoes/ia_contexto_tela.py) — deixa a IA propor um recorte do grafo no fim da resposta.

    TODO envio roda em background (thread) — mesmo o modo normal (function calling) — porque um turno
    pode passar do timeout do proxy (várias chamadas de ferramenta, ou o conteúdo completo injetado).
    No modo "conteúdo completo", o conteúdo é anexado ao prompt (uma chamada se couber na janela, ou
    dividido por data em lotes com map-reduce se não couber).

    Trava por conversa: a gravação da pergunta e a criação do processamento são atômicas e protegidas
    por um índice único parcial (um processamento ativo por conversa). Um envio concorrente na mesma
    conversa levanta ConversaOcupada — nada é gravado.
    """
    id_inquerito = _buscar_id_inquerito(id_conversa_ia)
    _liberar_processamentos_travados(id_conversa_ia)

    tp_escopo, id_escopo = _buscar_escopo_conversa(id_conversa_ia)
    # "conteúdo completo do inquérito" só existe para o conteúdo do aparelho (datado por
    # WhatsApp/SMS/etc.) — numa conversa restrita a um RIF/extrato/IPL não há o que anexar, e
    # ligar o modo mesmo assim contradiria o escopo. Ignora silenciosamente em vez de erro: o
    # front já esconde esse controle para conversas com escopo.
    if tp_escopo:
        incluir_conteudo = False

    # "não enviar+exibir prompt" anexa o conteúdo por padrão: sem chamada à IA nenhuma ferramenta de
    # busca roda, então o conteúdo anexado é a única forma de o prompt (e o .md) sair com dado do
    # inquérito dentro — antes saía só com o catálogo de ferramentas, sem uma linha de conversa.
    # Continua sem chamada nenhuma: as branches de conteúdo completo montam o prompt localmente.
    if exibir_prompt and not tp_escopo:
        incluir_conteudo = True

    # multisseleção do chat geral: valida cada item contra o inquérito e separa os aparelhos
    # (só eles têm conteúdo datado a anexar no modo "conteúdo completo")
    extracoes_filtro = None
    if escopos and not tp_escopo:
        escopos_validados = []
        for e in escopos:
            tp = e.get("tp_escopo")
            id_ = e.get("id_escopo")
            if tp not in ("rif", "simba", "ipl", "extracao", "documento") or not id_:
                raise ErroProvedorIA("Item de escopo inválido na mensagem.")
            _validar_escopo_documento(id_inquerito, tp, int(id_))
            escopos_validados.append({"tp_escopo": tp, "id_escopo": int(id_)})
        escopos = escopos_validados
        extracoes_filtro = [e["id_escopo"] for e in escopos if e["tp_escopo"] == "extracao"]
    else:
        escopos = None
    if incluir_conteudo and escopos and not extracoes_filtro:
        incluir_conteudo = False  # sem aparelho selecionado não há conteúdo do aparelho a anexar

    # Instruções adicionais da cascata geral → unidade → delegacia → usuário (ver
    # funcoes/ia_prompt_niveis.py). Resolvido AQUI e não na thread porque o orçamento do conteúdo
    # anexado precisa descontar o bloco antes de fatiar o inquérito — ele também é prompt de
    # sistema e sai da mesma janela do modelo.
    bloco_niveis = prompt_niveis_do_usuario(id_usuario)

    lotes = None
    budget_chars = 0
    qt_lotes = 1
    if incluir_conteudo:
        # No modo "exibir prompt" o texto não vai para o modelo configurado — a janela dele não é
        # régua nenhuma aqui, e usá-la fatiava o inquérito em seções minúsculas (com janela de 32k
        # o orçamento cai no piso, ~6 mil chars): estourava MAX_LOTES e o FIM do inquérito ficava de
        # fora do arquivo. Seções de MAX_CHARS_CONTEUDO cobrem casos grandes sem chegar perto do
        # teto de eventos por seção.
        budget_chars = (
            MAX_CHARS_CONTEUDO if exibir_prompt
            else _orcamento_chars_conteudo(janela_contexto_configurada(id_usuario), _tokens_historico(id_conversa_ia))
        )
        if bloco_niveis:
            # o bloco de instruções também mora no prompt de sistema: desconta do mesmo orçamento
            budget_chars = max(budget_chars - len(bloco_niveis),
                               int(_MIN_TOKENS_CONTEUDO * _CHARS_POR_TOKEN))
        budget_datado = max(budget_chars - len(_cabecalho_inquerito(id_inquerito)), int(_MIN_TOKENS_CONTEUDO * _CHARS_POR_TOKEN))
        lotes = montar_lotes_por_data(id_inquerito, budget_datado, extracoes=extracoes_filtro)
        qt_lotes = max(len(lotes), 1)  # 0 lotes (sem conteúdo datado) ou 1 lote = uma chamada única

    # pergunta + processamento na MESMA transação: se a trava (índice único parcial) barrar a
    # criação do processamento, o rollback desfaz também a pergunta — não fica mensagem órfã.
    with get_connection() as conn:
        with conn.cursor() as cur:
            id_mensagem_usuario = _gravar_mensagem(cur, id_conversa_ia, "user", texto, None, id_usuario)
            cur.execute(
                "SELECT no_titulo FROM investigacao.ia_tb01_conversas WHERE id_conversa_ia = %s",
                (id_conversa_ia,),
            )
            (titulo_atual,) = cur.fetchone()
            if not titulo_atual:
                cur.execute(
                    "UPDATE investigacao.ia_tb01_conversas SET no_titulo = %s WHERE id_conversa_ia = %s",
                    (texto.strip()[:TAMANHO_TITULO], id_conversa_ia),
                )
            try:
                cur.execute(
                    """
                    INSERT INTO investigacao.ia_tb03_processamentos
                        (id_conversa_ia, id_mensagem_usuario, qt_lotes_total)
                    VALUES (%s, %s, %s) RETURNING id_processamento
                    """,
                    (id_conversa_ia, id_mensagem_usuario, qt_lotes),
                )
                id_processamento = cur.fetchone()[0]
            except psycopg.errors.UniqueViolation:
                conn.rollback()
                raise ConversaOcupada(
                    "Já há uma resposta em andamento nesta conversa. Aguarde ela terminar antes de enviar outra."
                )
        conn.commit()

    threading.Thread(
        target=_processar_mensagem,
        args=(
            id_processamento, id_conversa_ia, id_inquerito, texto,
            incluir_conteudo, lotes, budget_chars, id_usuario, exibir_prompt,
            tp_escopo, id_escopo, escopos, extracoes_filtro, contexto_tela, bloco_niveis,
        ),
        daemon=True,
    ).start()
    return {"modo": "async", "id_processamento": id_processamento, "qt_lotes": qt_lotes}


# --- Processamento em background (thread) ---------------------------------------------------

def _processar_mensagem(
    id_processamento: int, id_conversa_ia: int, id_inquerito: int, pergunta: str,
    incluir_conteudo: bool, lotes: list | None, budget_chars: int, id_usuario: int | None = None,
    exibir_prompt: bool = False, tp_escopo: str | None = None, id_escopo: int | None = None,
    escopos: list[dict] | None = None, extracoes_filtro: list[int] | None = None,
    contexto_tela: dict | None = None, bloco_niveis: str = "",
) -> None:
    """Roda o turno em thread e grava a resposta. Turno normal: uma chamada com function calling.
    Conteúdo completo cabendo (0/1 lote): uma chamada com tudo anexado. Não cabendo: uma chamada por
    período (map) + uma síntese (reduce). escopos/extracoes_filtro vêm da multisseleção do chat geral."""
    auditoria = AuditoriaIA(on_atualizar=lambda trilha: _atualizar_trilha_processamento(id_processamento, trilha))
    inicio_turno = time.monotonic()
    sistema_usado = ""
    final = None
    modelo = None
    try:
        # Busca o título da conversa para nomear os arquivos .md
        titulo_conversa = ""
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT no_titulo FROM investigacao.ia_tb01_conversas WHERE id_conversa_ia = %s",
                    (id_conversa_ia,),
                )
                row = cur.fetchone()
                if row:
                    titulo_conversa = row[0] or pergunta[:60]

        _marcar_processando(id_processamento)
        ferramentas, funcoes = ferramentas_para_inquerito(
            id_inquerito, tp_escopo, id_escopo, escopos=escopos, id_usuario=id_usuario,
            id_conversa_ia=id_conversa_ia, id_processamento=id_processamento,
        )
        # O workspace de hipóteses é do inquérito inteiro; conversa restrita a uma mídia vê só as
        # hipóteses dela (vínculo próprio ou evidência apontando àquele item) — hipoteses_utils
        # importa deste módulo, daí o import tardio.
        from funcoes.hipoteses_utils import bloco_hipoteses_inquerito
        # as instruções da cascata (geral → unidade → delegacia → usuário) entram no contexto do
        # CASO de propósito: é ele que todas as branches deste turno montam no prompt de sistema —
        # geral, escopo de documento (SISTEMA_PROMPT_ESCOPO), lote e síntese. Complementam, nunca
        # substituem o método base (ver funcoes/ia_prompt_niveis.py).
        contexto_caso = (_bloco_contexto_caso(id_inquerito)
                         + bloco_hipoteses_inquerito(id_inquerito, tp_escopo, id_escopo)
                         + bloco_niveis)
        # Pedido de relatório: a resposta deste turno também vira um .docx para download (ver
        # funcoes/ia_relatorio_docx.py), então o prompt pede um documento — títulos, seções, sem
        # saudação — em vez de uma resposta de conversa. No modo "não enviar+exibir prompt" não
        # há resposta da IA para converter, e a instrução prometeria um Word que não vem.
        quer_relatorio = not exibir_prompt and pedido_de_relatorio(pergunta)
        bloco_relatorio = INSTRUCAO_RELATORIO if quer_relatorio else ""
        # Pergunta feita de uma tela de grafo (grafo do RIF/do SIMBA, mesa de inquérito): o painel
        # de filtros daquela tela entra no prompt, e com ele a instrução de terminar a resposta com
        # o marcador [filtro:{...}] que vira o botão "Aplicar no grafo" (ver ia_contexto_tela.py).
        bloco_tela = bloco_contexto_tela(contexto_tela)
        # cancelamento cooperativo: o provedor chama isto antes de cada rodada e aborta se pedido
        checkpoint = _checkpoint_cancelamento(id_processamento)
        registrar_etapa = lambda nome: _atualizar_etapa(id_processamento, nome)
        # "Anonimizar dados" (Configurações → Prompt IA): monta a tabela de-para do inquérito uma
        # vez por turno e a passa a TODA chamada ao provedor deste turno — inclusive à compactação
        # do histórico. No modo "não enviar+exibir prompt" não se aplica: nada sai da máquina, e o
        # .md é para o investigador ler com os nomes reais.
        anonimizador = (
            anonimizador_para_inquerito(id_inquerito)
            if not exibir_prompt and anonimizacao_ativa("AI") else None
        )
        # histórico: resumo das mensagens antigas (se a conversa já ficou longa) + as recentes.
        # Compactar chama a IA, então não roda no modo "não enviar+exibir prompt".
        resumo_hist, historico = _historico(
            id_conversa_ia, auditoria=auditoria, on_etapa=registrar_etapa,
            checkpoint=checkpoint, compactar=not exibir_prompt, anonimizador=anonimizador,
        )

        if not incluir_conteudo:
            # conversa restrita a um documento: prompt específico daquele tipo, sem o panorama do
            # aparelho (fora do escopo — as ferramentas de mensagens/ligações/etc. nem existem aqui)
            if tp_escopo:
                estatico = SISTEMA_PROMPT_ESCOPO.get(tp_escopo, SISTEMA_PROMPT) + contexto_caso
                # Em conversa com escopo o ID fica fixo e some do schema da ferramenta — sem isto a IA
                # realmente não tem como montar [doc:ID:N] (foi o "listar não está disponível" relatado).
                if tp_escopo == "documento" and id_escopo:
                    estatico += (
                        f"\n\nDOCUMENTO DESTA CONVERSA: id_documento={id_escopo} — cite como "
                        f"[doc:{id_escopo}] ou, com página das linhas [[página N]], [doc:{id_escopo}:N]. "
                        f"Não diga que não tem o ID."
                    )
            elif escopos:
                estatico = SISTEMA_PROMPT + contexto_caso + _bloco_escopos_selecionados(id_inquerito, escopos)
            else:
                estatico = SISTEMA_PROMPT + contexto_caso + _panorama_curto(id_inquerito)
            estatico += bloco_relatorio
            # o resumo do histórico é o bloco DINÂMICO do prompt de sistema: vai por último, fora
            # do prefixo cacheável (ver enviar_mensagem em ia_provedores — lista = blocos). O
            # contexto da tela de grafo vai junto e pelo mesmo motivo: ele carrega os filtros
            # ATIVOS AGORA, então muda de uma pergunta para a outra — no prefixo estático, ele
            # invalidaria o cache do provedor a cada mensagem feita de uma tela de grafo.
            dinamico = bloco_tela + resumo_hist
            sistema_usado = estatico + dinamico
            sistema_envio = [estatico, dinamico] if dinamico else estatico
            if exibir_prompt:
                # nada vai para o provedor: o prompt é serializado aqui mesmo, como nas branches de
                # conteúdo completo abaixo. Sem chamada a IA não tem como escolher ferramentas, então
                # o prompt sai com o CATÁLOGO delas — não com resultados de busca.
                prompt = _formatar_prompt_completo(sistema_usado, historico, ferramentas)
                final = _empacotar_resposta_prompt(prompt)
                modelo = "(somente prompt)"
                info_arquivo = _salvar_prompt_para_analise(
                    id_conversa_ia, id_inquerito, id_usuario,
                    titulo_conversa, prompt, "ia"
                )
                link = f"\n\n📄 [Baixar arquivo .md gerado](/api/investigacao/ia/prompts/{info_arquivo['id_arquivo_prompt']}/download)"
                final += link
            else:
                final, modelo = enviar_mensagem(
                    historico,
                    sistema=sistema_envio,
                    ferramentas=ferramentas,
                    funcoes=funcoes,
                    auditoria=auditoria,
                    on_iteracao=checkpoint,
                    on_ferramenta=registrar_etapa,
                    anonimizador=anonimizador,
                    id_usuario=id_usuario,
                )
            _incrementar_lote(id_processamento)
        elif len(lotes) <= 1:
            conteudo = montar_conteudo_inquerito(id_inquerito, max_chars=budget_chars, extracoes=extracoes_filtro)
            estatico = SISTEMA_PROMPT + contexto_caso + bloco_relatorio + bloco_tela + _INTRO_CONTEUDO + conteudo
            # sistema_usado é o que vai para a AUDITORIA: fica sempre com os nomes reais, porque a
            # tela de auditoria é do investigador. O que sai para o provedor é o sistema_envio.
            sistema_usado = estatico + resumo_hist
            if anonimizador:
                # o dump do aparelho é o único pedaço do prompt de sistema que é DADO, não
                # instrução: só aqui a heurística de nome próprio pode rodar sem risco de estragar
                # o método que o modelo tem de seguir (enviar_mensagem codifica o resto sem ela)
                estatico = (
                    SISTEMA_PROMPT + contexto_caso + bloco_relatorio + bloco_tela + _INTRO_CONTEUDO
                    + anonimizador.codificar(conteudo)
                )
            sistema_envio = [estatico, resumo_hist] if resumo_hist else estatico
            if exibir_prompt:
                prompt = _formatar_prompt_completo(
                    sistema_usado, historico, ferramentas
                )
                final = _empacotar_resposta_prompt(prompt)
                modelo = "(somente prompt)"
                info_arquivo = _salvar_prompt_para_analise(
                    id_conversa_ia, id_inquerito, id_usuario,
                    titulo_conversa, prompt, "content"
                )
                link = f"\n\n📄 [Baixar arquivo .md gerado](/api/investigacao/ia/prompts/{info_arquivo['id_arquivo_prompt']}/download)"
                final += link
            else:
                final, modelo = enviar_mensagem(
                    historico,
                    sistema=sistema_envio,
                    ferramentas=ferramentas,
                    funcoes=funcoes,
                    auditoria=auditoria,
                    on_iteracao=checkpoint,
                    on_ferramenta=registrar_etapa,
                    anonimizador=anonimizador,
                    id_usuario=id_usuario,
                )
            _incrementar_lote(id_processamento)
        else:
            if exibir_prompt:
                # aqui os lotes são só paginação da leitura no banco, não map-reduce: nada será
                # enviado, então o .md sai como UM prompt coerente — prompt normal + o conteúdo
                # inteiro em seções por período. Usar SISTEMA_PROMPT_LOTE/SINTESE mandaria a outra
                # IA "juntar as análises parciais de cada período", que não existem no arquivo.
                secoes: list[str] = []
                for i, (inicio, fim) in enumerate(lotes):
                    conteudo = montar_conteudo_inquerito(
                        id_inquerito, max_chars=budget_chars, inicio=inicio, fim=fim,
                        extracoes=extracoes_filtro, com_cabecalho=(i == 0),
                    )
                    periodo = f"{inicio:%Y-%m-%d %H:%M} a {fim:%Y-%m-%d %H:%M}"
                    secoes.append(f"--- PARTE {i + 1}/{len(lotes)} — período {periodo} ---\n{conteudo}")
                    _incrementar_lote(id_processamento)
                # inquérito grande demais até para MAX_LOTES seções: o fim do período sai
                # cortado (montar_conteudo_inquerito corta cada seção em max_chars) — melhor dizer
                # isso no arquivo do que deixar quem for ler supor que está tudo ali.
                aviso_corte = (
                    "\n\nATENÇÃO: este inquérito passou do limite de seções deste arquivo — o "
                    "trecho final do período pode estar cortado.\n"
                ) if len(lotes) >= MAX_LOTES else ""
                sistema_usado = (
                    SISTEMA_PROMPT + contexto_caso
                    + _INTRO_CONTEUDO_SECOES.format(qt=len(lotes)) + aviso_corte
                    + "\n\n".join(secoes) + resumo_hist
                )
                prompt = _formatar_prompt_completo(
                    sistema_usado, historico, ferramentas
                )
                final = _empacotar_resposta_prompt(prompt)
                modelo = "(somente prompt)"
                info_arquivo = _salvar_prompt_para_analise(
                    id_conversa_ia, id_inquerito, id_usuario,
                    titulo_conversa, prompt, "conteudo-secoes"
                )
                link = f"\n\n📄 [Baixar arquivo .md gerado](/api/investigacao/ia/prompts/{info_arquivo['id_arquivo_prompt']}/download)"
                final += link
            else:
                parciais = []
                for i, (inicio, fim) in enumerate(lotes):
                    if _cancelamento_solicitado(id_processamento):  # checkpoint antes de cada parte
                        raise CancelamentoSolicitado()
                    conteudo = montar_conteudo_inquerito(id_inquerito, max_chars=budget_chars, inicio=inicio, fim=fim, extracoes=extracoes_filtro)
                    if anonimizador:
                        conteudo = anonimizador.codificar(conteudo)
                    parcial, _modelo = enviar_mensagem(
                        [{"role": "user", "content": pergunta}],
                        sistema=SISTEMA_PROMPT_LOTE + contexto_caso + "\n\n" + conteudo,
                        auditoria=auditoria,
                        anonimizador=anonimizador,
                        id_usuario=id_usuario,
                    )
                    periodo = f"{inicio:%Y-%m-%d %H:%M} a {fim:%Y-%m-%d %H:%M}"
                    parciais.append(f"### Parte {i + 1}/{len(lotes)} — período {periodo}\n{parcial}")
                    _incrementar_lote(id_processamento)
                # só a síntese pede formato de relatório: as parciais de cada período são insumo,
                # e mandar cada uma sair como documento fechado só atrapalharia a junção
                estatico = SISTEMA_PROMPT_SINTESE + contexto_caso + bloco_relatorio + bloco_tela
                dinamico = resumo_hist + "\n\n" + "\n\n".join(parciais)
                sistema_usado = estatico + dinamico
                # as parciais voltaram descodificadas de cada chamada; a síntese as reenvia, então
                # precisam ser codificadas de novo — e com heurística, porque aqui é texto que a
                # própria IA escreveu sobre o conteúdo, não instrução nossa
                dinamico_envio = anonimizador.codificar(dinamico) if anonimizador else dinamico
                sistema_envio = [estatico, dinamico_envio] if dinamico.strip() else estatico
                final, modelo = enviar_mensagem(
                    historico,
                    sistema=sistema_envio,
                    ferramentas=ferramentas,
                    funcoes=funcoes,
                    auditoria=auditoria,
                    on_iteracao=checkpoint,
                    on_ferramenta=registrar_etapa,
                    anonimizador=anonimizador,
                    id_usuario=id_usuario,
                )

        if quer_relatorio:
            # o .docx sai da resposta já pronta, antes de gravá-la: o link entra na própria
            # mensagem, então quem reabrir a conversa depois continua achando o arquivo
            final = anexar_relatorio(
                final, pergunta, titulo_conversa, id_conversa_ia, id_usuario, modelo,
                "/api/investigacao/ia/relatorios", id_inquerito=id_inquerito,
            )

        id_mensagem = _gravar_resposta(
            id_conversa_ia, final, modelo, trilha_para_gravar(auditoria),
            co_jobs=ia_jobs_turno.coletar(id_processamento),
        )
        _concluir_processamento(id_processamento, id_mensagem)
        registrar_auditoria(
            "chat_inquerito", pergunta, sistema_usado, auditoria, modelo,
            int((time.monotonic() - inicio_turno) * 1000), resposta=final,
            id_inquerito=id_inquerito, id_conversa_ia=id_conversa_ia,
            id_mensagem=id_mensagem, id_usuario=id_usuario,
        )
        _avisar_webhook_ia("concluido", id_processamento, id_conversa_ia, id_inquerito,
                           id_usuario, titulo_conversa)
    except CancelamentoSolicitado:  # abortado pelo investigador: encerra sem gravar resposta
        _marcar_cancelado(id_processamento)
        registrar_auditoria(
            "chat_inquerito", pergunta, sistema_usado, auditoria, modelo,
            int((time.monotonic() - inicio_turno) * 1000), status="cancelado", erro="Cancelado pelo usuário.",
            id_inquerito=id_inquerito, id_conversa_ia=id_conversa_ia, id_usuario=id_usuario,
        )
        _avisar_webhook_ia("cancelado", id_processamento, id_conversa_ia, id_inquerito,
                           id_usuario, locals().get("titulo_conversa", ""))
    except Exception as exc:  # não deixa o job preso em "processando"
        _erro_processamento(id_processamento, str(exc))
        registrar_auditoria(
            "chat_inquerito", pergunta, sistema_usado, auditoria, modelo,
            int((time.monotonic() - inicio_turno) * 1000), status="erro", erro=str(exc),
            id_inquerito=id_inquerito, id_conversa_ia=id_conversa_ia, id_usuario=id_usuario,
        )
        _avisar_webhook_ia("erro", id_processamento, id_conversa_ia, id_inquerito,
                           id_usuario, locals().get("titulo_conversa", ""), erro=str(exc))


def _checkpoint_cancelamento(id_processamento: int):
    """Devolve uma função sem argumentos que levanta CancelamentoSolicitado se o investigador pediu
    para abortar. Passada como on_iteracao ao provedor, roda antes de cada chamada de IA — então o
    cancelamento é sentido entre as chamadas de ferramenta e entre as partes do map-reduce."""
    def checkpoint() -> None:
        if _cancelamento_solicitado(id_processamento):
            raise CancelamentoSolicitado()

    return checkpoint


def _atualizar_processamento(id_processamento: int, set_sql: str, params: tuple) -> None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE investigacao.ia_tb03_processamentos SET {set_sql} WHERE id_processamento = %s",
                (*params, id_processamento),
            )
        conn.commit()


def _atualizar_etapa(id_processamento: int, nome_ferramenta: str) -> None:
    """Registra a etapa atual do processamento para exibição em tempo real no front-end."""
    descricao = NOME_PARA_DESCRICAO.get(nome_ferramenta, f"Executando {nome_ferramenta}…")
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE investigacao.ia_tb03_processamentos SET tx_etapa_atual = %s WHERE id_processamento = %s",
                (descricao, id_processamento),
            )
        conn.commit()


def _atualizar_trilha_processamento(id_processamento: int, trilha: list[dict]) -> None:
    """Persiste a trilha ainda incompleta para o acompanhamento ao vivo do tema Orbit."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE investigacao.ia_tb03_processamentos SET tx_progresso = %s::jsonb "
                "WHERE id_processamento = %s",
                (json.dumps(trilha, ensure_ascii=False, default=str), id_processamento),
            )
        conn.commit()


def _cancelamento_solicitado(id_processamento: int) -> bool:
    """True se o investigador pediu para cancelar este processamento (checkpoint entre lotes)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT fl_cancelar FROM investigacao.ia_tb03_processamentos WHERE id_processamento = %s",
                (id_processamento,),
            )
            row = cur.fetchone()
    return bool(row and row[0])


def solicitar_cancelamento(id_processamento: int) -> bool:
    """Liga fl_cancelar se o processamento ainda estiver ativo; devolve se havia o que cancelar.
    O status continua 'processando' (a trava por conversa segue valendo) até o worker parar de fato."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE investigacao.ia_tb03_processamentos SET fl_cancelar = true "
                "WHERE id_processamento = %s AND cd_status IN ('pendente', 'processando')",
                (id_processamento,),
            )
            marcou = cur.rowcount > 0
        conn.commit()
    return marcou


def _marcar_cancelado(id_processamento: int) -> None:
    _atualizar_processamento(id_processamento, "cd_status = 'cancelado', dt_fim = now()", ())


def _marcar_processando(id_processamento: int) -> None:
    _atualizar_processamento(id_processamento, "cd_status = 'processando'", ())


def _incrementar_lote(id_processamento: int) -> None:
    _atualizar_processamento(id_processamento, "qt_lotes_concluidos = qt_lotes_concluidos + 1", ())


def _concluir_processamento(id_processamento: int, id_mensagem_resposta: int) -> None:
    _atualizar_processamento(
        id_processamento,
        "cd_status = 'concluido', id_mensagem_resposta = %s, dt_fim = now()",
        (id_mensagem_resposta,),
    )


def _erro_processamento(id_processamento: int, mensagem: str) -> None:
    _atualizar_processamento(
        id_processamento, "cd_status = 'erro', tx_erro = %s, dt_fim = now()", (mensagem[:2000],)
    )


def _avisar_webhook_ia(
    status: str, id_processamento: int, id_conversa_ia: int, id_inquerito: int,
    id_usuario: int | None, titulo: str | None = None, erro: str | None = None,
) -> None:
    """Avisa via webhook o solicitante + criador de que o turno terminou (concluído/erro/cancelado).

    Fire-and-forget: nunca levanta, nunca trava o turno — webhook lento ou fora do ar só vai para
    o log. Cobre ia.html e o painel flutuante (mesmo backend). Sem URL cadastrada, silencioso."""
    try:
        from funcoes import ia_webhook

        ia_webhook.notificar_inquerito(
            status, id_conversa_ia, id_inquerito, id_processamento,
            id_usuario, titulo=titulo, erro=erro,
        )
    except Exception:
        logger.exception("webhook IA (inquérito): falha ao agendar aviso")


def obter_processamento(id_processamento: int) -> dict | None:
    """Status de um processamento em lotes, para o polling do front-end (None se não existir)."""
    query = """
        SELECT id_processamento, id_conversa_ia, cd_status, qt_lotes_total, qt_lotes_concluidos,
               tx_erro, id_mensagem_resposta, tx_etapa_atual, tx_progresso, dt_inicio
        FROM investigacao.ia_tb03_processamentos
        WHERE id_processamento = %s
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (id_processamento,))
            linha = cur.fetchone()
            if not linha:
                return None
            colunas = [d.name for d in cur.description]
            return dict(zip(colunas, linha))


def obter_arquivo_prompt(id_arquivo_prompt: int) -> dict | None:
    """Retorna os dados de um arquivo .md de prompt salvo."""
    query = """
        SELECT id_arquivo_prompt, id_conversa_ia, id_inquerito, id_usuario,
               no_arquivo, caminho_arquivo, dt_criacao
        FROM investigacao.ia_tb04_arquivos_prompt
        WHERE id_arquivo_prompt = %s
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (id_arquivo_prompt,))
            linha = cur.fetchone()
            if not linha:
                return None
            colunas = [d.name for d in cur.description]
            return dict(zip(colunas, linha))

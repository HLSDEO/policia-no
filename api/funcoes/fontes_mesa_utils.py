"""Resolvedor de FONTES da Mesa de Inquérito (painel "🗂 Fontes").

A mesa desenha nós que NASCEM em mídias diferentes do inquérito — aparelho (extração),
RIF, extrato do SIMBA, IPL, IRPF, IPEI, telefonia — e o investigador quer ver só o que
vem de algumas delas. Cada nó da camada do banco carrega a origem polimórfica
(TP_ORIGEM/ID_ORIGEM, ver entidades_utils); este módulo traduz "fontes marcadas"
({"extracao": [1, 2], "rif": [5]}) num predicado SQL sobre (tp_origem, id_origem) que
restringe entidades e evidências de canônicas exatamente às origens daquelas fontes.

O mapa de famílias espelha _VERIFICACAO_ORIGEM (entidades_utils): cada tp_origem pertence
a UMA fonte, resolvida pela mesma junção que já valida a criação da entidade. O teste
tests/test_fontes_mesa.py confere a cobertura — tp_origem novo que não for declarado aqui
faz o teste falhar em vez de sumir do grafo filtrado em silêncio.

'documento' NÃO tem família aqui: a camada de documentos (dd*/dp*/da*) já tem recorte
próprio por id_documento (documentos_utils.grafo_documentos_inquerito) e é resolvida na
rota, não por tp_origem.
"""

from funcoes.entidades_utils import _DOMINIOS_UFDR_ANOTAVEIS

FONTE_TIPOS = ("extracao", "rif", "simba", "ipl", "irpf", "ipei", "telefonia", "documento")

# Famílias "diretas": a tabela da origem tem a coluna da fonte na própria linha —
# (tp_origem, tabela, PK, coluna da fonte).
_DIRETAS: dict[str, list[tuple[str, str, str, str]]] = {
    "extracao": [
        ("contato_agenda", "ctt_tb01_contatos", "id_contato", "id_extracao"),
        ("contato_wpp", "wpp_tb01_contatos", "id_contato", "id_extracao"),
        ("contato_sms", "sms_tb01_contatos", "id_contato", "id_extracao"),
        ("contato_msg", "msg_tb01_contatos", "id_contato", "id_extracao"),
        ("wpp_conversa", "wpp_tb02_conversas", "id_conversa", "id_extracao"),
        ("sms_conversa", "sms_tb02_conversas", "id_conversa", "id_extracao"),
        ("msg_conversa", "msg_tb02_conversas", "id_conversa", "id_extracao"),
        ("ligacao", "lig_tb01_ligacoes", "id_ligacao", "id_extracao"),
        ("localizacao", "loc_tb01_localizacoes", "id_localizacao", "id_extracao"),
        ("notificacao", "ntf_tb01_notificacoes", "id_notificacao", "id_extracao"),
        ("nota", "not_tb01_notas", "id_nota", "id_extracao"),
        ("navegacao", "nav_tb01_paginas_visitadas", "id_pagina", "id_extracao"),
        ("email", "eml_tb01_emails", "id_email", "id_extracao"),
        ("wifi", "wifi_tb01_redes", "id_rede_wifi", "id_extracao"),
        ("conta_financeira", "bnc_tb01_contas_financeiras", "id_conta_financeira", "id_extracao"),
        ("conta_usuario", "cta_tb01_contas_usuario", "id_conta_usuario", "id_extracao"),
        ("aplicativo", "app_tb01_aplicativos", "id_aplicativo", "id_extracao"),
        ("dono", "don_tb01_donos", "id_dono", "id_extracao"),
        ("arquivo", "arq_tb01_arquivos", "id_arquivo", "id_extracao"),
        ("dispositivo_conta", "dvc_tb01_dispositivos", "id_dispositivo", "id_extracao"),
        ("evento", "evt_tb01_eventos", "id_evento", "id_extracao"),
    ]
    + [(tp, tabela, pk, "id_extracao") for tp, tabela, pk in _DOMINIOS_UFDR_ANOTAVEIS],
    "rif": [
        ("rif_comunicacao", "rif_tb02_comunicacoes", "id_comunicacao_rif", "id_rif"),
        ("rif_envolvido", "rif_tb03_envolvidos", "id_envolvido_rif", "id_rif"),
        ("rif_contraparte", "rif_tb05_contrapartes", "id_contraparte_rif", "id_rif"),
    ],
    "simba": [
        ("simba_conta", "smb_tb02_contas", "id_conta_simba", "id_extrato"),
        ("simba_lancamento", "smb_tb03_lancamentos", "id_lancamento_simba", "id_extrato"),
        ("simba_contraparte", "smb_tb04_contrapartes", "id_contraparte_simba", "id_extrato"),
    ],
    "ipl": [
        ("ipl_pessoa", "ipl_tb03_pessoas", "id_pessoa_ipl", "id_ipl"),
        ("ipl_achado", "ipl_tb04_achados", "id_achado_ipl", "id_ipl"),
    ],
    "irpf": [
        ("irpf_declaracao", "irp_tb01_declaracoes", "id_irpf", "id_irpf"),
        ("irpf_bem", "irp_tb02_bens_direitos", "id_bem", "id_irpf"),
        ("irpf_dependente", "irp_tb03_dependentes", "id_dependente", "id_irpf"),
        ("irpf_rendimento", "irp_tb04_rendimentos", "id_rendimento", "id_irpf"),
        ("irpf_divida", "irp_tb05_dividas", "id_divida", "id_irpf"),
        ("irpf_doacao", "irp_tb06_doacoes", "id_doacao", "id_irpf"),
    ],
    "ipei": [
        ("ipei_alvo", "ipe_tb02_alvos", "id_alvo", "id_ipei"),
        ("ipei_relacionado", "ipe_tb03_relacionados", "id_relacionado", "id_ipei"),
        ("ipei_conta", "ipe_tb06_contas", "id_conta", "id_ipei"),
        ("ipei_bem", "ipe_tb07_bens", "id_bem", "id_ipei"),
        ("ipei_nota", "ipe_tb08_notas_fiscais", "id_nota", "id_ipei"),
        ("ipei_contato", "ipe_tb09_contatos", "id_contato", "id_ipei"),
    ],
    "telefonia": [
        ("telefonia_linha", "tel_tb03_linhas", "id_linha_telefonia", "id_telefonia"),
        ("telefonia_chamada", "tel_tb04_chamadas", "id_chamada_telefonia", "id_telefonia"),
        ("telefonia_numero", "tel_tb06_numeros", "id_numero_telefonia", "id_telefonia"),
    ],
}

# Mensagens não são linha direta: pendem numa conversa, e é ela a filha da extração —
# mesma junção do verificador de origem. (tp_origem, tabela, PK da msg, tabela/PK da msg,
# tabela da conversa, PK da conversa).
_MSG_PARA_EXTRACAO = [
    ("wpp_mensagem", "wpp_tb04_mensagens", "id_mensagem", "wpp_tb02_conversas"),
    ("sms_mensagem", "sms_tb04_mensagens", "id_mensagem", "sms_tb02_conversas"),
    ("msg_mensagem", "msg_tb04_mensagens", "id_mensagem", "msg_tb02_conversas"),
]

# Cobertura declarada — o teste compara com TIPOS_ORIGEM_VALIDOS.
_ORIGENS_COBERTAS: set[str] = {tp for familias in _DIRETAS.values() for tp, *_ in familias}
_ORIGENS_COBERTAS |= {tp for tp, *_ in _MSG_PARA_EXTRACAO}


def parse_fontes(valor: str | None) -> dict[str, list[int]]:
    """'extracao:1,extracao:2,rif:5' -> {'extracao': [1, 2], 'rif': [5]}.

    None/vazio devolve {}. Tp desconhecido ou id não numérico levantam ValueError —
    a rota traduz para 400 em vez de filtrar com lixo."""
    fontes: dict[str, list[int]] = {}
    for parte in str(valor or "").split(","):
        parte = parte.strip()
        if not parte:
            continue
        tp, sep, cru = parte.partition(":")
        tp = tp.strip().lower()
        if not sep or tp not in FONTE_TIPOS:
            raise ValueError(f"Fonte inválida: {parte!r} (use {'/'.join(FONTE_TIPOS)}:id).")
        try:
            numero = int(cru.strip())
        except ValueError:
            raise ValueError(f"Id de fonte inválido: {parte!r}.")
        if numero <= 0:
            raise ValueError(f"Id de fonte inválido: {parte!r}.")
        if numero not in fontes.setdefault(tp, []):
            fontes[tp].append(numero)
    return fontes


def _fragmentos() -> dict[str, list[tuple[str, str]]]:
    """{tp_fonte: [(tp_origem, sql EXISTS com %s pros ids e {alvo} pra resolver)]}."""
    saida: dict[str, list[tuple[str, str]]] = {}
    for tp_f, familias in _DIRETAS.items():
        for tp_origem, tabela, pk, col_fonte in familias:
            sql = (
                f"SELECT 1 FROM investigacao.{tabela} d"
                f" WHERE d.{pk} = {{alvo}}.id_origem AND d.{col_fonte} = ANY(%s)"
            )
            saida.setdefault(tp_f, []).append((tp_origem, sql))
    for tp_origem, tabela, pk, tabela_conv in _MSG_PARA_EXTRACAO:
        sql = (
            f"SELECT 1 FROM investigacao.{tabela} m"
            f" JOIN investigacao.{tabela_conv} c ON c.id_conversa = m.id_conversa"
            f" WHERE m.{pk} = {{alvo}}.id_origem AND c.id_extracao = ANY(%s)"
        )
        saida.setdefault("extracao", []).append((tp_origem, sql))
    return saida


def fragmento_fontes(
    fontes: dict[str, list[int]], alvo_alias: str
) -> tuple[str, list] | None:
    """Predicado SQL que casa (alvo.tp_origem, alvo.id_origem) com alguma fonte marcada.

    Devolve (sql, params) ou None quando não há nada para filtrar (fontes vazio ou só
    'documento', que a rota resolve na camada de documentos). Os params vêm na ordem dos
    %s do sql: o tp_origem literal e o array de ids de cada família. 'documento' é
    ignorado aqui."""
    if not fontes:
        return None
    fragments: list[str] = []
    params: list = []
    for tp_f in FONTE_TIPOS:
        ids = fontes.get(tp_f)
        if not ids or tp_f == "documento":
            continue
        for tp_origem, sql in _FRAGMENTOS[tp_f]:
            fragments.append(
                f"({alvo_alias}.tp_origem = %s AND EXISTS ({sql.format(alvo=alvo_alias)}))"
            )
            params.extend([tp_origem, list(ids)])
    if not fragments:
        return None
    return "(" + " OR ".join(fragments) + ")", params


_FRAGMENTOS = _fragmentos()

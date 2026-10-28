"""Entidades: anotação livre do investigador em cima de qualquer dado extraído (mensagem,
contato, ligação, localização, notificação) — base para o grafo de relacionamento futuro.

Cada verificação em _VERIFICACAO_ORIGEM confirma, antes de gravar, que o id_origem
existe e pertence ao mesmo inquérito — mesmo isolamento por caso aplicado no resto do app.
"""

from funcoes.db_utils import get_connection
from funcoes.imagens_mesa import apagar_imagem_no

_VERIFICACAO_ORIGEM = {
    "wpp_mensagem": """
        SELECT 1 FROM investigacao.wpp_tb04_mensagens m
        JOIN investigacao.wpp_tb02_conversas c ON c.id_conversa = m.id_conversa
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = c.id_extracao
        WHERE m.id_mensagem = %(id_origem)s AND e.id_inquerito = %(id_inquerito)s
    """,
    "sms_mensagem": """
        SELECT 1 FROM investigacao.sms_tb04_mensagens m
        JOIN investigacao.sms_tb02_conversas c ON c.id_conversa = m.id_conversa
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = c.id_extracao
        WHERE m.id_mensagem = %(id_origem)s AND e.id_inquerito = %(id_inquerito)s
    """,
    # A CONVERSA — o contato naquele mensageiro —, não uma mensagem dela. É a TRIAGEM do
    # analista pela lista lateral do WhatsApp: quais fios ele já leu, quais são relevantes
    # (ver web/js/whatsapp.js). Marcar a conversa NÃO marca as mensagens dela: são duas
    # marcações independentes, com finalidades diferentes.
    "wpp_conversa": """
        SELECT 1 FROM investigacao.wpp_tb02_conversas c
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = c.id_extracao
        WHERE c.id_conversa = %(id_origem)s AND e.id_inquerito = %(id_inquerito)s
    """,
    "sms_conversa": """
        SELECT 1 FROM investigacao.sms_tb02_conversas c
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = c.id_extracao
        WHERE c.id_conversa = %(id_origem)s AND e.id_inquerito = %(id_inquerito)s
    """,
    "msg_conversa": """
        SELECT 1 FROM investigacao.msg_tb02_conversas c
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = c.id_extracao
        WHERE c.id_conversa = %(id_origem)s AND e.id_inquerito = %(id_inquerito)s
    """,
    "contato_agenda": """
        SELECT 1 FROM investigacao.ctt_tb01_contatos c
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = c.id_extracao
        WHERE c.id_contato = %(id_origem)s AND e.id_inquerito = %(id_inquerito)s
    """,
    "contato_wpp": """
        SELECT 1 FROM investigacao.wpp_tb01_contatos c
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = c.id_extracao
        WHERE c.id_contato = %(id_origem)s AND e.id_inquerito = %(id_inquerito)s
    """,
    "contato_sms": """
        SELECT 1 FROM investigacao.sms_tb01_contatos c
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = c.id_extracao
        WHERE c.id_contato = %(id_origem)s AND e.id_inquerito = %(id_inquerito)s
    """,
    "msg_mensagem": """
        SELECT 1 FROM investigacao.msg_tb04_mensagens m
        JOIN investigacao.msg_tb02_conversas c ON c.id_conversa = m.id_conversa
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = c.id_extracao
        WHERE m.id_mensagem = %(id_origem)s AND e.id_inquerito = %(id_inquerito)s
    """,
    "contato_msg": """
        SELECT 1 FROM investigacao.msg_tb01_contatos c
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = c.id_extracao
        WHERE c.id_contato = %(id_origem)s AND e.id_inquerito = %(id_inquerito)s
    """,
    "ligacao": """
        SELECT 1 FROM investigacao.lig_tb01_ligacoes l
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = l.id_extracao
        WHERE l.id_ligacao = %(id_origem)s AND e.id_inquerito = %(id_inquerito)s
    """,
    "localizacao": """
        SELECT 1 FROM investigacao.loc_tb01_localizacoes lo
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = lo.id_extracao
        WHERE lo.id_localizacao = %(id_origem)s AND e.id_inquerito = %(id_inquerito)s
    """,
    "notificacao": """
        SELECT 1 FROM investigacao.ntf_tb01_notificacoes n
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = n.id_extracao
        WHERE n.id_notificacao = %(id_origem)s AND e.id_inquerito = %(id_inquerito)s
    """,
    "nota": """
        SELECT 1 FROM investigacao.not_tb01_notas n
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = n.id_extracao
        WHERE n.id_nota = %(id_origem)s AND e.id_inquerito = %(id_inquerito)s
    """,
    "navegacao": """
        SELECT 1 FROM investigacao.nav_tb01_paginas_visitadas p
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = p.id_extracao
        WHERE p.id_pagina = %(id_origem)s AND e.id_inquerito = %(id_inquerito)s
    """,
    "email": """
        SELECT 1 FROM investigacao.eml_tb01_emails m
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = m.id_extracao
        WHERE m.id_email = %(id_origem)s AND e.id_inquerito = %(id_inquerito)s
    """,
    "wifi": """
        SELECT 1 FROM investigacao.wifi_tb01_redes w
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = w.id_extracao
        WHERE w.id_rede_wifi = %(id_origem)s AND e.id_inquerito = %(id_inquerito)s
    """,
    "conta_financeira": """
        SELECT 1 FROM investigacao.bnc_tb01_contas_financeiras c
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = c.id_extracao
        WHERE c.id_conta_financeira = %(id_origem)s AND e.id_inquerito = %(id_inquerito)s
    """,
    "conta_usuario": """
        SELECT 1 FROM investigacao.cta_tb01_contas_usuario ct
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = ct.id_extracao
        WHERE ct.id_conta_usuario = %(id_origem)s AND e.id_inquerito = %(id_inquerito)s
    """,
    "aplicativo": """
        SELECT 1 FROM investigacao.app_tb01_aplicativos a
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = a.id_extracao
        WHERE a.id_aplicativo = %(id_origem)s AND e.id_inquerito = %(id_inquerito)s
    """,
    "dono": """
        SELECT 1 FROM investigacao.don_tb01_donos d
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = d.id_extracao
        WHERE d.id_dono = %(id_origem)s AND e.id_inquerito = %(id_inquerito)s
    """,
    "rif_comunicacao": """
        SELECT 1 FROM investigacao.rif_tb02_comunicacoes co
        JOIN investigacao.rif_tb01_rifs r ON r.id_rif = co.id_rif
        WHERE co.id_comunicacao_rif = %(id_origem)s AND r.id_inquerito = %(id_inquerito)s
    """,
    "rif_envolvido": """
        SELECT 1 FROM investigacao.rif_tb03_envolvidos ev
        JOIN investigacao.rif_tb01_rifs r ON r.id_rif = ev.id_rif
        WHERE ev.id_envolvido_rif = %(id_origem)s AND r.id_inquerito = %(id_inquerito)s
    """,
    "rif_contraparte": """
        SELECT 1 FROM investigacao.rif_tb05_contrapartes cp
        JOIN investigacao.rif_tb01_rifs r ON r.id_rif = cp.id_rif
        WHERE cp.id_contraparte_rif = %(id_origem)s AND r.id_inquerito = %(id_inquerito)s
    """,
    "simba_conta": """
        SELECT 1 FROM investigacao.smb_tb02_contas c
        JOIN investigacao.smb_tb01_extratos s ON s.id_extrato = c.id_extrato
        WHERE c.id_conta_simba = %(id_origem)s AND s.id_inquerito = %(id_inquerito)s
    """,
    "simba_lancamento": """
        SELECT 1 FROM investigacao.smb_tb03_lancamentos l
        JOIN investigacao.smb_tb01_extratos s ON s.id_extrato = l.id_extrato
        WHERE l.id_lancamento_simba = %(id_origem)s AND s.id_inquerito = %(id_inquerito)s
    """,
    "simba_contraparte": """
        SELECT 1 FROM investigacao.smb_tb04_contrapartes cp
        JOIN investigacao.smb_tb01_extratos s ON s.id_extrato = cp.id_extrato
        WHERE cp.id_contraparte_simba = %(id_origem)s AND s.id_inquerito = %(id_inquerito)s
    """,
    "ipl_pessoa": """
        SELECT 1 FROM investigacao.ipl_tb03_pessoas pe
        JOIN investigacao.ipl_tb01_inqueritos_pdf p ON p.id_ipl = pe.id_ipl
        WHERE pe.id_pessoa_ipl = %(id_origem)s AND p.id_inquerito = %(id_inquerito)s
    """,
    "ipl_achado": """
        SELECT 1 FROM investigacao.ipl_tb04_achados ac
        JOIN investigacao.ipl_tb01_inqueritos_pdf p ON p.id_ipl = ac.id_ipl
        WHERE ac.id_achado_ipl = %(id_origem)s AND p.id_inquerito = %(id_inquerito)s
    """,
    "irpf_bem": """
        SELECT 1 FROM investigacao.irp_tb02_bens_direitos b
        JOIN investigacao.irp_tb01_declaracoes i ON i.id_irpf = b.id_irpf
        WHERE b.id_bem = %(id_origem)s AND i.id_inquerito = %(id_inquerito)s
    """,
    "irpf_dependente": """
        SELECT 1 FROM investigacao.irp_tb03_dependentes d
        JOIN investigacao.irp_tb01_declaracoes i ON i.id_irpf = d.id_irpf
        WHERE d.id_dependente = %(id_origem)s AND i.id_inquerito = %(id_inquerito)s
    """,
    "irpf_rendimento": """
        SELECT 1 FROM investigacao.irp_tb04_rendimentos r
        JOIN investigacao.irp_tb01_declaracoes i ON i.id_irpf = r.id_irpf
        WHERE r.id_rendimento = %(id_origem)s AND i.id_inquerito = %(id_inquerito)s
    """,
    "irpf_divida": """
        SELECT 1 FROM investigacao.irp_tb05_dividas dv
        JOIN investigacao.irp_tb01_declaracoes i ON i.id_irpf = dv.id_irpf
        WHERE dv.id_divida = %(id_origem)s AND i.id_inquerito = %(id_inquerito)s
    """,
    "irpf_doacao": """
        SELECT 1 FROM investigacao.irp_tb06_doacoes doa
        JOIN investigacao.irp_tb01_declaracoes i ON i.id_irpf = doa.id_irpf
        WHERE doa.id_doacao = %(id_origem)s AND i.id_inquerito = %(id_inquerito)s
    """,
    "ipei_alvo": """
        SELECT 1 FROM investigacao.ipe_tb02_alvos al
        JOIN investigacao.ipe_tb01_ipeis i ON i.id_ipei = al.id_ipei
        WHERE al.id_alvo = %(id_origem)s AND i.id_inquerito = %(id_inquerito)s
    """,
    "ipei_relacionado": """
        SELECT 1 FROM investigacao.ipe_tb03_relacionados rl
        JOIN investigacao.ipe_tb01_ipeis i ON i.id_ipei = rl.id_ipei
        WHERE rl.id_relacionado = %(id_origem)s AND i.id_inquerito = %(id_inquerito)s
    """,
    "ipei_conta": """
        SELECT 1 FROM investigacao.ipe_tb06_contas ct
        JOIN investigacao.ipe_tb01_ipeis i ON i.id_ipei = ct.id_ipei
        WHERE ct.id_conta = %(id_origem)s AND i.id_inquerito = %(id_inquerito)s
    """,
    "ipei_bem": """
        SELECT 1 FROM investigacao.ipe_tb07_bens bn
        JOIN investigacao.ipe_tb01_ipeis i ON i.id_ipei = bn.id_ipei
        WHERE bn.id_bem = %(id_origem)s AND i.id_inquerito = %(id_inquerito)s
    """,
    "ipei_nota": """
        SELECT 1 FROM investigacao.ipe_tb08_notas_fiscais nf
        JOIN investigacao.ipe_tb01_ipeis i ON i.id_ipei = nf.id_ipei
        WHERE nf.id_nota = %(id_origem)s AND i.id_inquerito = %(id_inquerito)s
    """,
    "ipei_contato": """
        SELECT 1 FROM investigacao.ipe_tb09_contatos ctt
        JOIN investigacao.ipe_tb01_ipeis i ON i.id_ipei = ctt.id_ipei
        WHERE ctt.id_contato = %(id_origem)s AND i.id_inquerito = %(id_inquerito)s
    """,
    # A declaração de IRPF inteira (e não uma linha dela): é a evidência do DECLARANTE, que a
    # identificação materializa a partir do cabeçalho — CPF, telefone e e-mail declarados.
    "irpf_declaracao": """
        SELECT 1 FROM investigacao.irp_tb01_declaracoes i
        WHERE i.id_irpf = %(id_origem)s AND i.id_inquerito = %(id_inquerito)s
    """,
    "telefonia_linha": """
        SELECT 1 FROM investigacao.tel_tb03_linhas l
        JOIN investigacao.tel_tb01_telefonia t ON t.id_telefonia = l.id_telefonia
        WHERE l.id_linha_telefonia = %(id_origem)s AND t.id_inquerito = %(id_inquerito)s
    """,
    "telefonia_chamada": """
        SELECT 1 FROM investigacao.tel_tb04_chamadas ch
        JOIN investigacao.tel_tb01_telefonia t ON t.id_telefonia = ch.id_telefonia
        WHERE ch.id_chamada_telefonia = %(id_origem)s AND t.id_inquerito = %(id_inquerito)s
    """,
    "telefonia_numero": """
        SELECT 1 FROM investigacao.tel_tb06_numeros nu
        JOIN investigacao.tel_tb01_telefonia t ON t.id_telefonia = nu.id_telefonia
        WHERE nu.id_numero_telefonia = %(id_origem)s AND t.id_inquerito = %(id_inquerito)s
    """,
    "arquivo": """
        SELECT 1 FROM investigacao.arq_tb01_arquivos ar
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = ar.id_extracao
        WHERE ar.id_arquivo = %(id_origem)s AND e.id_inquerito = %(id_inquerito)s
    """,
    # Tabelas genéricas de conta na nuvem (29_nuvem.sql), preenchidas pela extração do IPED.
    # Mesma forma das demais: tabela filha da extração.
    "dispositivo_conta": """
        SELECT 1 FROM investigacao.dvc_tb01_dispositivos d
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = d.id_extracao
        WHERE d.id_dispositivo = %(id_origem)s AND e.id_inquerito = %(id_inquerito)s
    """,
    "evento": """
        SELECT 1 FROM investigacao.evt_tb01_eventos ev
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = ev.id_extracao
        WHERE ev.id_evento = %(id_origem)s AND e.id_inquerito = %(id_inquerito)s
    """,
}

# Domínios do UFDR importados na auditoria de aproveitamento. Todos têm a mesma forma — uma
# tabela filha da extração —, então a verificação de origem e o mapa de tabela saem de uma
# lista só, em vez de vinte blocos SQL iguais. É o que permite o analista marcar para o
# relatório uma conexão de Bluetooth, uma busca ou uma antena, como já fazia com uma ligação.
_DOMINIOS_UFDR_ANOTAVEIS = [
    ("uso_app",            "uso_tb01_uso_apps",           "id_uso_app"),
    ("dispositivo_reconhecido", "rcd_tb01_dispositivos",  "id_dispositivo"),
    ("conexao",            "cnx_tb01_conexoes",           "id_conexao"),
    ("cookie",             "coo_tb01_cookies",            "id_cookie"),
    ("favorito",           "fav_tb01_favoritos",          "id_favorito"),
    ("download",           "dwn_tb01_downloads",          "id_download"),
    ("uso_rede",           "rde_tb01_uso_rede",           "id_uso_rede"),
    ("atividade_social",   "rds_tb01_atividade_social",   "id_atividade"),
    ("busca",              "bsc_tb01_buscas",             "id_busca"),
    ("envio_nuvem",        "upl_tb01_envios_nuvem",       "id_envio"),
    ("gravacao",           "grv_tb01_gravacoes",          "id_gravacao"),
    ("compromisso",        "cal_tb01_calendario",         "id_compromisso"),
    ("uso_aplicativo",     "apu_tb01_uso_aplicativo",     "id_uso"),
    ("trajeto",            "jrn_tb01_trajetos",           "id_trajeto"),
    ("info_aparelho",      "dev_tb01_info_aparelho",      "id_info"),
    ("antena",             "erb_tb01_antenas",            "id_antena"),
    ("voicemail",          "vms_tb01_voicemail",          "id_voicemail"),
    ("transferencia",      "trf_tb01_transferencias",     "id_transferencia"),
    ("cartao_credito",     "crt_tb01_cartoes_credito",    "id_cartao"),
    ("cartao_carteira",    "mbc_tb01_cartoes_carteira",   "id_cartao_carteira"),
    ("autofill",           "afl_tb01_autofill",           "id_autofill"),
]

for _tp, _tabela, _pk in _DOMINIOS_UFDR_ANOTAVEIS:
    _VERIFICACAO_ORIGEM[_tp] = f"""
        SELECT 1 FROM investigacao.{_tabela} d
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = d.id_extracao
        WHERE d.{_pk} = %(id_origem)s AND e.id_inquerito = %(id_inquerito)s
    """

TIPOS_ORIGEM_VALIDOS = set(_VERIFICACAO_ORIGEM)


# TP_ORIGEM -> (tabela, coluna de PK) do registro anotado. Espelha _VERIFICACAO_ORIGEM sem o
# recorte por inquérito: aquele responde "esta anotação é deste inquérito?" uma linha por vez,
# este permite varrer órfãs em massa (ver limpar_anotacoes_orfas).
_TABELA_ORIGEM = {
    "wpp_mensagem":      ("wpp_tb04_mensagens", "id_mensagem"),
    "sms_mensagem":      ("sms_tb04_mensagens", "id_mensagem"),
    "msg_mensagem":      ("msg_tb04_mensagens", "id_mensagem"),
    "wpp_conversa":      ("wpp_tb02_conversas", "id_conversa"),
    "sms_conversa":      ("sms_tb02_conversas", "id_conversa"),
    "msg_conversa":      ("msg_tb02_conversas", "id_conversa"),
    "contato_agenda":    ("ctt_tb01_contatos", "id_contato"),
    "contato_wpp":       ("wpp_tb01_contatos", "id_contato"),
    "contato_sms":       ("sms_tb01_contatos", "id_contato"),
    "contato_msg":       ("msg_tb01_contatos", "id_contato"),
    "ligacao":           ("lig_tb01_ligacoes", "id_ligacao"),
    "localizacao":       ("loc_tb01_localizacoes", "id_localizacao"),
    "notificacao":       ("ntf_tb01_notificacoes", "id_notificacao"),
    "nota":              ("not_tb01_notas", "id_nota"),
    "navegacao":         ("nav_tb01_paginas_visitadas", "id_pagina"),
    "email":             ("eml_tb01_emails", "id_email"),
    "wifi":              ("wifi_tb01_redes", "id_rede_wifi"),
    "conta_financeira":  ("bnc_tb01_contas_financeiras", "id_conta_financeira"),
    "conta_usuario":     ("cta_tb01_contas_usuario", "id_conta_usuario"),
    "aplicativo":        ("app_tb01_aplicativos", "id_aplicativo"),
    "dono":              ("don_tb01_donos", "id_dono"),
    "arquivo":           ("arq_tb01_arquivos", "id_arquivo"),
    "rif_comunicacao":   ("rif_tb02_comunicacoes", "id_comunicacao_rif"),
    "rif_envolvido":     ("rif_tb03_envolvidos", "id_envolvido_rif"),
    "rif_contraparte":   ("rif_tb05_contrapartes", "id_contraparte_rif"),
    "simba_conta":       ("smb_tb02_contas", "id_conta_simba"),
    "simba_lancamento":  ("smb_tb03_lancamentos", "id_lancamento_simba"),
    "simba_contraparte": ("smb_tb04_contrapartes", "id_contraparte_simba"),
    "ipl_pessoa":        ("ipl_tb03_pessoas", "id_pessoa_ipl"),
    "ipl_achado":        ("ipl_tb04_achados", "id_achado_ipl"),
    "irpf_declaracao":   ("irp_tb01_declaracoes", "id_irpf"),
    "irpf_bem":          ("irp_tb02_bens_direitos", "id_bem"),
    "irpf_dependente":   ("irp_tb03_dependentes", "id_dependente"),
    "irpf_rendimento":   ("irp_tb04_rendimentos", "id_rendimento"),
    "irpf_divida":       ("irp_tb05_dividas", "id_divida"),
    "irpf_doacao":       ("irp_tb06_doacoes", "id_doacao"),
    "ipei_alvo":         ("ipe_tb02_alvos", "id_alvo"),
    "ipei_relacionado":  ("ipe_tb03_relacionados", "id_relacionado"),
    "ipei_conta":        ("ipe_tb06_contas", "id_conta"),
    "ipei_bem":          ("ipe_tb07_bens", "id_bem"),
    "ipei_nota":         ("ipe_tb08_notas_fiscais", "id_nota"),
    "ipei_contato":      ("ipe_tb09_contatos", "id_contato"),
    "telefonia_linha":   ("tel_tb03_linhas", "id_linha_telefonia"),
    "telefonia_chamada": ("tel_tb04_chamadas", "id_chamada_telefonia"),
    "telefonia_numero":  ("tel_tb06_numeros", "id_numero_telefonia"),
    "dispositivo_conta": ("dvc_tb01_dispositivos", "id_dispositivo"),
    "evento":            ("evt_tb01_eventos", "id_evento"),
}

# Tabelas que anotam um registro por (TP_ORIGEM, ID_ORIGEM), sem FK que segure o vínculo.
TABELAS_ANOTACAO = (
    "ent_tb06_canonica_evidencias",   # evidência de pessoa canônica
    "ent_tb01_entidades",             # entidade/comentário do investigador
    "pdr_tb03_deteccoes",             # detecção de padrão de crime
    "rel_tb03_marcacoes",             # marcado para o relatório
)


def limpar_anotacoes_orfas(cur, *tipos_origem: str) -> int:
    """Remove as anotações que apontam para um registro que não existe mais, nos TP_ORIGEM dados.

    O vínculo é polimórfico (TP_ORIGEM/ID_ORIGEM) e por isso NÃO tem FK: quando a tabela de origem
    é regravada — o reprocessamento de contrapartes do RIF e do SIMBA apaga e reinsere, e o ID é
    IDENTITY — a anotação fica pendurada num ID que já morreu. Na tela isso vira uma pessoa
    canônica com uma origem que não abre e uma aresta do grafo para lugar nenhum.

    Deve ser chamada DEPOIS de regravar/apagar a origem, na mesma transação de quem regravou.
    Devolve o total removido."""
    total = 0
    for tp in tipos_origem:
        tabela_origem, pk = _TABELA_ORIGEM[tp]
        for tabela_anotacao in TABELAS_ANOTACAO:
            cur.execute(
                f"""
                DELETE FROM investigacao.{tabela_anotacao} a
                WHERE a.tp_origem = %s
                  AND NOT EXISTS (SELECT 1 FROM investigacao.{tabela_origem} o
                                   WHERE o.{pk} = a.id_origem)
                """,
                (tp,),
            )
            total += cur.rowcount
    return total


def criar_entidade(
    id_inquerito: int, tp_origem: str, id_origem: int, comentario: str, id_usuario: int | None = None,
    automatico: bool = False, id_busca: int | None = None, justificativa: str | None = None,
) -> int:
    """Cria uma entidade (comentário) presa a um registro de origem; devolve o ID.

    Entidade manual nasce 'confirmada'; a criada por busca automática nasce 'sugerida'
    (fica tracejada na mesa até o investigador confirmar ou descartar), guardando de
    qual busca veio e a justificativa/evidência citada pela IA.

    Levanta ValueError se o tipo de origem for desconhecido, o comentário vier vazio,
    ou o id_origem não existir dentro do inquérito informado."""
    verificacao = _VERIFICACAO_ORIGEM.get(tp_origem)
    if verificacao is None:
        raise ValueError(f"Tipo de origem inválido: {tp_origem!r}")
    comentario = (comentario or "").strip()
    if not comentario:
        raise ValueError("Comentário não pode ser vazio.")

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(verificacao, {"id_origem": id_origem, "id_inquerito": id_inquerito})
            if cur.fetchone() is None:
                raise ValueError("Registro de origem não encontrado neste inquérito.")
            cur.execute(
                """
                INSERT INTO investigacao.ent_tb01_entidades
                    (id_inquerito, tp_origem, id_origem, tx_comentario, id_usuario_criador,
                     in_automatico, cd_revisao, id_busca, tx_justificativa)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id_entidade
                """,
                (
                    id_inquerito, tp_origem, id_origem, comentario, id_usuario, automatico,
                    "sugerida" if automatico else "confirmada", id_busca, justificativa,
                ),
            )
            id_entidade = cur.fetchone()[0]
        conn.commit()
    return id_entidade


def listar_entidades_inquerito(
    id_inquerito: int,
    tipos: list[str] | None = None,
    busca: str | None = None,
    revisoes: list[str] | None = None,
    limite: int | None = None,
    fontes: dict[str, list[int]] | None = None,
) -> list[dict]:
    """Lista TODAS as entidades de um inquérito (qualquer tipo de origem), com um rótulo
    legível da origem — são os nós do grafo da Mesa de Inquérito, e também o contexto que a
    IA recebe (via funcoes.ia_investigacao) sobre o que já existe antes de buscar mais.

    Filtros server-side opcionais (inquéritos com milhares de nós): ``tipos``
    restringe TP_ORIGEM, ``revisoes`` restringe CD_REVISAO, ``busca`` faz ILIKE no
    comentário/justificativa, ``limite`` impõe teto ordenado por criação (mais
    recentes primeiro), ``fontes`` ({tp: [ids]}, ver fontes_mesa_utils) mantém só as
    entidades nascidas numa das fontes marcadas do painel "🗂 Fontes" da mesa. Sem
    filtros, comportamento idêntico ao anterior."""
    query = """
        SELECT e.id_entidade, e.tp_origem, e.id_origem, e.tx_comentario, e.dt_criacao,
               e.in_automatico, e.cd_revisao, e.id_busca, e.tx_justificativa,
               e.nu_pos_x, e.nu_pos_y, u.no_usuario AS criado_por,
               COALESCE(
                   wm.tx_mensagem, sm.tx_mensagem, mm.tx_mensagem,
                   ca.no_contato, cw.no_contato, cs.no_contato, cm.no_contato,
                   lg.no_contato, lg.nu_telefone_contato,
                    lo.tx_descricao, lo.tx_endereco,
                     nt.tx_conteudo, nt.no_pacote_app,
                     nota.tx_titulo, nota.tx_corpo,
                     nv.tx_titulo, nv.tx_url,
                     em.tx_assunto,
                     wf.no_ssid, wf.no_bssid,
                     cf.no_instituicao, cf.no_titular,
                     ctu.no_usuario, ctu.no_titular, ctu.no_servico,
                   ap.no_aplicativo, ap.nu_pacote,
                   dn.no_dono,
                   rifc.tx_informacoes_adicionais, rifc.no_comunicante,
                   rife.no_envolvido, rife.nu_cpf_cnpj,
                   rifcp.no_contraparte, rifcp.nu_cpf_cnpj,
                   smbc.no_titular, smbc.nu_cpf_cnpj,
                   smbl.tx_descricao, smbl.no_od,
                   smbcp.no_contraparte, smbcp.nu_cpf_cnpj,
                   iplp.no_pessoa, iplp.nu_cpf_cnpj, iplp.no_vulgo,
                   ipla.tx_valor,
                   -- domínios do UFDR importados na auditoria de aproveitamento
                   bsc.tx_termo,
                   rds.tx_corpo, rds.tx_titulo, rds.no_autor,
                   cal.tx_assunto, cal.tx_local,
                   erb.nu_cid,
                   cnx.no_dispositivo, cnx.no_mac,
                   jrn.tx_endereco_destino, jrn.tx_endereco_origem, jrn.no_trajeto,
                   trf.tx_participantes, trf.tx_descricao,
                   mbc.tx_descricao, mbc.no_organizacao,
                   crt.no_bandeira, crt.no_titular,
                   vms.no_contato, vms.nu_telefone,
                   upl.no_arquivo, grv.tx_titulo,
                   dev.no_campo, rcd.no_dispositivo, rcd.no_mac,
                   coo.no_dominio, fav.tx_titulo, fav.tx_url, dwn.tx_url,
                   afl.no_chave, rde.no_ssid, usoa.nu_pacote, apu.nu_pacote,
                   -- conversa inteira: os aliases wcv/scv/mcv só casam quando tp_origem é de
                   -- conversa, então entram no fim do COALESCE sem risco de virar o "conteúdo"
                   -- de uma mensagem sem texto
                   wcv.no_grupo, scv.no_grupo, mcv.no_grupo,
                   -- IPEI (quebra de sigilo fiscal): sem estes o nó marcado a partir da tela da
                   -- IPEI nasceria em branco no grafo e sumiria da linha do tempo da mesa
                   ipa.no_alvo, ipr.no_relacionado, ipc.nu_conta,
                   COALESCE(ipb.nu_identificador, ipb.tx_descricao), ipn.tx_descricao, ipt.tx_valor,
                   -- tabelas genéricas de conta na nuvem (preenchidas pelo IPED)
                   dvc.no_dispositivo, dvc.tx_fabricante, evt.tx_titulo
               ) AS rotulo_origem,
               COALESCE(
                   wm.dt_mensagem, sm.dt_mensagem, mm.dt_mensagem, lg.dt_ligacao, lo.dt_localizacao,
                   nt.dt_notificacao, COALESCE(nota.dt_modificacao, nota.dt_criacao),
                    nv.dt_visita, ap.dt_instalacao, dn.dt_ultimo_login,
                     em.dt_email,
                     COALESCE(wf.dt_ultima_conexao, wf.dt_primeira_conexao),
                     rifc.dt_recebimento,
                     smbl.dt_lancamento,
                     bsc.dt_busca, rds.dt_atividade, cal.dt_inicio, erb.dt_registro,
                     cnx.dt_conexao, jrn.dt_inicio, trf.dt_envio, mbc.dt_compra,
                     crt.dt_ultimo_uso, vms.dt_recebimento, upl.dt_envio, grv.dt_gravacao,
                     dev.dt_registro, coo.dt_ultimo_acesso, fav.dt_ultima_visita,
                     dwn.dt_inicio, afl.dt_ultimo_uso, rde.dt_inicio,
                     usoa.dt_inicio, apu.dt_ultima_abertura,
                     wcv.dt_ultima_atividade, scv.dt_ultima_atividade, mcv.dt_ultima_atividade,
                     ipn.dt_emissao,
                     evt.dt_inicio
                 ) AS dt_origem
        FROM investigacao.ent_tb01_entidades e
        LEFT JOIN seguranca.usr_tb01_usuarios u ON u.id_usuario = e.id_usuario_criador
        LEFT JOIN investigacao.wpp_tb04_mensagens wm ON e.tp_origem = 'wpp_mensagem' AND wm.id_mensagem = e.id_origem
        LEFT JOIN investigacao.sms_tb04_mensagens sm ON e.tp_origem = 'sms_mensagem' AND sm.id_mensagem = e.id_origem
        LEFT JOIN investigacao.msg_tb04_mensagens mm ON e.tp_origem = 'msg_mensagem' AND mm.id_mensagem = e.id_origem
        LEFT JOIN investigacao.wpp_tb02_conversas wcv ON e.tp_origem = 'wpp_conversa' AND wcv.id_conversa = e.id_origem
        LEFT JOIN investigacao.sms_tb02_conversas scv ON e.tp_origem = 'sms_conversa' AND scv.id_conversa = e.id_origem
        LEFT JOIN investigacao.msg_tb02_conversas mcv ON e.tp_origem = 'msg_conversa' AND mcv.id_conversa = e.id_origem
        LEFT JOIN investigacao.ctt_tb01_contatos ca ON e.tp_origem = 'contato_agenda' AND ca.id_contato = e.id_origem
        LEFT JOIN investigacao.wpp_tb01_contatos cw ON e.tp_origem = 'contato_wpp' AND cw.id_contato = e.id_origem
        LEFT JOIN investigacao.sms_tb01_contatos cs ON e.tp_origem = 'contato_sms' AND cs.id_contato = e.id_origem
        LEFT JOIN investigacao.msg_tb01_contatos cm ON e.tp_origem = 'contato_msg' AND cm.id_contato = e.id_origem
        LEFT JOIN investigacao.lig_tb01_ligacoes lg ON e.tp_origem = 'ligacao' AND lg.id_ligacao = e.id_origem
        LEFT JOIN investigacao.loc_tb01_localizacoes lo ON e.tp_origem = 'localizacao' AND lo.id_localizacao = e.id_origem
        LEFT JOIN investigacao.ntf_tb01_notificacoes nt ON e.tp_origem = 'notificacao' AND nt.id_notificacao = e.id_origem
        LEFT JOIN investigacao.not_tb01_notas nota ON e.tp_origem = 'nota' AND nota.id_nota = e.id_origem
        LEFT JOIN investigacao.nav_tb01_paginas_visitadas nv ON e.tp_origem = 'navegacao' AND nv.id_pagina = e.id_origem
        LEFT JOIN investigacao.eml_tb01_emails em ON e.tp_origem = 'email' AND em.id_email = e.id_origem
        LEFT JOIN investigacao.wifi_tb01_redes wf ON e.tp_origem = 'wifi' AND wf.id_rede_wifi = e.id_origem
        LEFT JOIN investigacao.bnc_tb01_contas_financeiras cf ON e.tp_origem = 'conta_financeira' AND cf.id_conta_financeira = e.id_origem
        LEFT JOIN investigacao.cta_tb01_contas_usuario ctu ON e.tp_origem = 'conta_usuario' AND ctu.id_conta_usuario = e.id_origem
        LEFT JOIN investigacao.app_tb01_aplicativos ap ON e.tp_origem = 'aplicativo' AND ap.id_aplicativo = e.id_origem
        LEFT JOIN investigacao.don_tb01_donos dn ON e.tp_origem = 'dono' AND dn.id_dono = e.id_origem
        LEFT JOIN investigacao.rif_tb02_comunicacoes rifc ON e.tp_origem = 'rif_comunicacao' AND rifc.id_comunicacao_rif = e.id_origem
        LEFT JOIN investigacao.rif_tb03_envolvidos rife ON e.tp_origem = 'rif_envolvido' AND rife.id_envolvido_rif = e.id_origem
        LEFT JOIN investigacao.rif_tb05_contrapartes rifcp ON e.tp_origem = 'rif_contraparte' AND rifcp.id_contraparte_rif = e.id_origem
        LEFT JOIN investigacao.smb_tb02_contas smbc ON e.tp_origem = 'simba_conta' AND smbc.id_conta_simba = e.id_origem
        LEFT JOIN investigacao.smb_tb03_lancamentos smbl ON e.tp_origem = 'simba_lancamento' AND smbl.id_lancamento_simba = e.id_origem
        LEFT JOIN investigacao.smb_tb04_contrapartes smbcp ON e.tp_origem = 'simba_contraparte' AND smbcp.id_contraparte_simba = e.id_origem
        LEFT JOIN investigacao.ipl_tb03_pessoas iplp ON e.tp_origem = 'ipl_pessoa' AND iplp.id_pessoa_ipl = e.id_origem
        LEFT JOIN investigacao.ipl_tb04_achados ipla ON e.tp_origem = 'ipl_achado' AND ipla.id_achado_ipl = e.id_origem
        LEFT JOIN investigacao.ipe_tb02_alvos ipa ON e.tp_origem = 'ipei_alvo' AND ipa.id_alvo = e.id_origem
        LEFT JOIN investigacao.ipe_tb03_relacionados ipr ON e.tp_origem = 'ipei_relacionado' AND ipr.id_relacionado = e.id_origem
        LEFT JOIN investigacao.ipe_tb06_contas ipc ON e.tp_origem = 'ipei_conta' AND ipc.id_conta = e.id_origem
        LEFT JOIN investigacao.ipe_tb07_bens ipb ON e.tp_origem = 'ipei_bem' AND ipb.id_bem = e.id_origem
        LEFT JOIN investigacao.ipe_tb08_notas_fiscais ipn ON e.tp_origem = 'ipei_nota' AND ipn.id_nota = e.id_origem
        LEFT JOIN investigacao.ipe_tb09_contatos ipt ON e.tp_origem = 'ipei_contato' AND ipt.id_contato = e.id_origem
        -- domínios do UFDR importados na auditoria de aproveitamento (ver extração_ufdr.md):
        -- sem estes joins o nó marcado a partir deles nasceria sem rótulo e sem data, some
        -- da linha do tempo da mesa e aparece como bolinha em branco no grafo
        LEFT JOIN investigacao.bsc_tb01_buscas bsc ON e.tp_origem = 'busca' AND bsc.id_busca = e.id_origem
        LEFT JOIN investigacao.rds_tb01_atividade_social rds ON e.tp_origem = 'atividade_social' AND rds.id_atividade = e.id_origem
        LEFT JOIN investigacao.cal_tb01_calendario cal ON e.tp_origem = 'compromisso' AND cal.id_compromisso = e.id_origem
        LEFT JOIN investigacao.erb_tb01_antenas erb ON e.tp_origem = 'antena' AND erb.id_antena = e.id_origem
        LEFT JOIN investigacao.cnx_tb01_conexoes cnx ON e.tp_origem = 'conexao' AND cnx.id_conexao = e.id_origem
        LEFT JOIN investigacao.jrn_tb01_trajetos jrn ON e.tp_origem = 'trajeto' AND jrn.id_trajeto = e.id_origem
        LEFT JOIN investigacao.trf_tb01_transferencias trf ON e.tp_origem = 'transferencia' AND trf.id_transferencia = e.id_origem
        LEFT JOIN investigacao.mbc_tb01_cartoes_carteira mbc ON e.tp_origem = 'cartao_carteira' AND mbc.id_cartao_carteira = e.id_origem
        LEFT JOIN investigacao.crt_tb01_cartoes_credito crt ON e.tp_origem = 'cartao_credito' AND crt.id_cartao = e.id_origem
        LEFT JOIN investigacao.vms_tb01_voicemail vms ON e.tp_origem = 'voicemail' AND vms.id_voicemail = e.id_origem
        LEFT JOIN investigacao.upl_tb01_envios_nuvem upl ON e.tp_origem = 'envio_nuvem' AND upl.id_envio = e.id_origem
        LEFT JOIN investigacao.grv_tb01_gravacoes grv ON e.tp_origem = 'gravacao' AND grv.id_gravacao = e.id_origem
        LEFT JOIN investigacao.dev_tb01_info_aparelho dev ON e.tp_origem = 'info_aparelho' AND dev.id_info = e.id_origem
        LEFT JOIN investigacao.rcd_tb01_dispositivos rcd ON e.tp_origem = 'dispositivo_reconhecido' AND rcd.id_dispositivo = e.id_origem
        LEFT JOIN investigacao.coo_tb01_cookies coo ON e.tp_origem = 'cookie' AND coo.id_cookie = e.id_origem
        LEFT JOIN investigacao.fav_tb01_favoritos fav ON e.tp_origem = 'favorito' AND fav.id_favorito = e.id_origem
        LEFT JOIN investigacao.dwn_tb01_downloads dwn ON e.tp_origem = 'download' AND dwn.id_download = e.id_origem
        LEFT JOIN investigacao.afl_tb01_autofill afl ON e.tp_origem = 'autofill' AND afl.id_autofill = e.id_origem
        LEFT JOIN investigacao.rde_tb01_uso_rede rde ON e.tp_origem = 'uso_rede' AND rde.id_uso_rede = e.id_origem
        LEFT JOIN investigacao.uso_tb01_uso_apps usoa ON e.tp_origem = 'uso_app' AND usoa.id_uso_app = e.id_origem
        LEFT JOIN investigacao.apu_tb01_uso_aplicativo apu ON e.tp_origem = 'uso_aplicativo' AND apu.id_uso = e.id_origem
        LEFT JOIN investigacao.dvc_tb01_dispositivos dvc ON e.tp_origem = 'dispositivo_conta' AND dvc.id_dispositivo = e.id_origem
        LEFT JOIN investigacao.evt_tb01_eventos evt ON e.tp_origem = 'evento' AND evt.id_evento = e.id_origem
        WHERE e.id_inquerito = %s
        ORDER BY e.dt_criacao DESC
    """
    _tipos = [t for t in (tipos or []) if t]
    _revs = [r for r in (revisoes or []) if r]
    _busca = (busca or "").strip().replace("%", "").replace("_", "")
    _frag_fontes = None
    if fontes:
        from funcoes.fontes_mesa_utils import fragmento_fontes  # local: evita import circular
        _frag_fontes = fragmento_fontes(fontes, "e")
    if _tipos or _revs or _busca or limite or _frag_fontes:
        conds: list[str] = []
        params: list = []
        if _tipos:
            conds.append("e.tp_origem = ANY(%s)")
            params.append(_tipos)
        if _revs:
            conds.append("e.cd_revisao = ANY(%s)")
            params.append(_revs)
        if _busca:
            conds.append("(e.tx_comentario ILIKE %s OR e.tx_justificativa ILIKE %s)")
            like = "%" + _busca + "%"
            params.extend([like, like])
        if _frag_fontes:
            conds.append(_frag_fontes[0])
            params.extend(_frag_fontes[1])
        extra = (" AND " + " AND ".join(conds)) if conds else ""
        query = query.replace(
            "WHERE e.id_inquerito = %s\n        ORDER BY e.dt_criacao DESC",
            "WHERE e.id_inquerito = %s" + extra + "\n        ORDER BY e.dt_criacao DESC",
        )
        if limite:
            query = query.rstrip() + "\n        LIMIT %s"
            params.append(int(limite))
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (id_inquerito, *params))
                colunas = [d.name for d in cur.description]
                return [dict(zip(colunas, linha)) for linha in cur.fetchall()]
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (id_inquerito,))
            colunas = [d.name for d in cur.description]
            return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


def criar_relacao(
    id_inquerito: int, id_entidade_origem: int, id_entidade_destino: int, tipo_relacao: str,
    id_usuario: int | None = None, automatico: bool = False,
    id_busca: int | None = None, justificativa: str | None = None,
) -> int:
    """Cria uma relação (aresta do grafo) entre duas entidades do mesmo inquérito; devolve o ID.

    Como nas entidades, relação manual nasce 'confirmada' e a da busca automática 'sugerida'.

    Levanta ValueError se as entidades forem a mesma, o tipo vier vazio, ou alguma delas
    não existir/não pertencer a esse inquérito (mesmo isolamento por caso do resto do app)."""
    tipo_relacao = (tipo_relacao or "").strip()
    if not tipo_relacao:
        raise ValueError("Tipo de relação não pode ser vazio.")
    if id_entidade_origem == id_entidade_destino:
        raise ValueError("Uma relação precisa de duas entidades diferentes.")

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM investigacao.ent_tb01_entidades WHERE id_entidade IN (%s, %s) AND id_inquerito = %s",
                (id_entidade_origem, id_entidade_destino, id_inquerito),
            )
            if cur.fetchone()[0] != 2:
                raise ValueError("Uma das entidades não existe neste inquérito.")
            cur.execute(
                """
                INSERT INTO investigacao.ent_tb03_relacoes
                    (id_inquerito, id_entidade_origem, id_entidade_destino, tx_tipo_relacao,
                     in_automatico, id_usuario_criador, cd_revisao, id_busca, tx_justificativa)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id_relacao
                """,
                (
                    id_inquerito, id_entidade_origem, id_entidade_destino, tipo_relacao,
                    automatico, id_usuario, "sugerida" if automatico else "confirmada",
                    id_busca, justificativa,
                ),
            )
            id_relacao = cur.fetchone()[0]
        conn.commit()
    return id_relacao


def listar_relacoes(id_inquerito: int) -> list[dict]:
    """Lista as relações (arestas do grafo) de um inquérito."""
    query = """
        SELECT r.id_relacao, r.id_entidade_origem, r.id_entidade_destino, r.tx_tipo_relacao,
               r.in_automatico, r.cd_revisao, r.tx_justificativa, r.dt_criacao,
               u.no_usuario AS criado_por
        FROM investigacao.ent_tb03_relacoes r
        LEFT JOIN seguranca.usr_tb01_usuarios u ON u.id_usuario = r.id_usuario_criador
        WHERE r.id_inquerito = %s
        ORDER BY r.dt_criacao
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (id_inquerito,))
            colunas = [d.name for d in cur.description]
            return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


def listar_entidades(id_inquerito: int, tp_origem: str) -> list[dict]:
    """Lista as entidades de um tipo de origem, mais recente primeiro — usado pra marcar
    quais registros já têm comentário e mostrar o histórico no popup."""
    query = """
        SELECT e.id_entidade, e.id_origem, e.tx_comentario, e.dt_criacao, u.no_usuario AS criado_por
        FROM investigacao.ent_tb01_entidades e
        LEFT JOIN seguranca.usr_tb01_usuarios u ON u.id_usuario = e.id_usuario_criador
        WHERE e.id_inquerito = %s AND e.tp_origem = %s
        ORDER BY e.dt_criacao DESC
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (id_inquerito, tp_origem))
            colunas = [d.name for d in cur.description]
            return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


def obter_id_inquerito_de_entidade(id_entidade: int) -> int | None:
    """Resolve o id_inquerito dono de uma entidade, para checar permissão em rotas que só
    recebem id_entidade. Devolve None se a entidade não existir."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id_inquerito FROM investigacao.ent_tb01_entidades WHERE id_entidade = %s", (id_entidade,))
            row = cur.fetchone()
            return row[0] if row else None


def remover_entidade(id_entidade: int) -> bool:
    """Remove uma entidade (e as relações que a tocam, senão a FK impede; a foto do nó sai na
    mesma cascata manual); devolve False se o id não existir."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            apagar_imagem_no(cur, "entidade", id_entidade)
            cur.execute(
                "DELETE FROM investigacao.ent_tb03_relacoes WHERE id_entidade_origem = %s OR id_entidade_destino = %s",
                (id_entidade, id_entidade),
            )
            cur.execute("DELETE FROM investigacao.ent_tb01_entidades WHERE id_entidade = %s", (id_entidade,))
            removido = cur.rowcount > 0
        conn.commit()
    return removido


def atualizar_entidade(id_entidade: int, comentario: str) -> bool:
    """Troca o comentário de uma entidade; devolve False se o id não existir."""
    comentario = (comentario or "").strip()
    if not comentario:
        raise ValueError("Comentário não pode ser vazio.")
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE investigacao.ent_tb01_entidades SET tx_comentario = %s WHERE id_entidade = %s",
                (comentario, id_entidade),
            )
            atualizado = cur.rowcount > 0
        conn.commit()
    return atualizado


def atualizar_posicao(id_entidade: int, pos_x: float | None, pos_y: float | None) -> bool:
    """Fixa (ou limpa, com None) a posição de um nó arrastado na mesa; devolve False se o id não existir."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE investigacao.ent_tb01_entidades SET nu_pos_x = %s, nu_pos_y = %s WHERE id_entidade = %s",
                (pos_x, pos_y, id_entidade),
            )
            atualizado = cur.rowcount > 0
        conn.commit()
    return atualizado


REVISOES_VALIDAS = {"sugerida", "confirmada", "descartada"}


def revisar_entidade(id_entidade: int, revisao: str) -> bool:
    """Muda o status de revisão de uma entidade sugerida pela IA (confirmar/descartar).
    Descartar também descarta as relações que a tocam — aresta órfã não faz sentido no grafo."""
    if revisao not in REVISOES_VALIDAS:
        raise ValueError(f"Revisão inválida: {revisao!r}")
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE investigacao.ent_tb01_entidades SET cd_revisao = %s WHERE id_entidade = %s",
                (revisao, id_entidade),
            )
            atualizado = cur.rowcount > 0
            if atualizado and revisao == "descartada":
                cur.execute(
                    """
                    UPDATE investigacao.ent_tb03_relacoes SET cd_revisao = 'descartada'
                    WHERE id_entidade_origem = %s OR id_entidade_destino = %s
                    """,
                    (id_entidade, id_entidade),
                )
        conn.commit()
    return atualizado


def revisar_relacao(id_relacao: int, revisao: str) -> bool:
    """Muda o status de revisão de uma relação sugerida pela IA (confirmar/descartar)."""
    if revisao not in REVISOES_VALIDAS:
        raise ValueError(f"Revisão inválida: {revisao!r}")
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE investigacao.ent_tb03_relacoes SET cd_revisao = %s WHERE id_relacao = %s",
                (revisao, id_relacao),
            )
            atualizado = cur.rowcount > 0
        conn.commit()
    return atualizado


def obter_id_inquerito_de_relacao(id_relacao: int) -> int | None:
    """Resolve o id_inquerito dono de uma relação, para checar permissão. None se não existir."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id_inquerito FROM investigacao.ent_tb03_relacoes WHERE id_relacao = %s", (id_relacao,))
            row = cur.fetchone()
            return row[0] if row else None


def remover_relacao(id_relacao: int) -> bool:
    """Remove uma relação; devolve False se o id não existir."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM investigacao.ent_tb03_relacoes WHERE id_relacao = %s", (id_relacao,))
            removido = cur.rowcount > 0
        conn.commit()
    return removido

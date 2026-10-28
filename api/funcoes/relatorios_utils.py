"""Padrão de relatórios + marcações "Marcar para o relatório".

Duas responsabilidades, espelhando a divisão do 30_relatorios.sql:

  1) PADRÕES (REL_TB01/REL_TB02) — biblioteca global do administrador, mantida na página
     "Padrão de relatórios" (o CRUD aqui é consumido por rotas/relatorios.py com exigir_admin,
     como os prompts de entidade em prompts_entidade_utils.py). Cada padrão é uma lista
     ordenada de blocos: trechos de texto com campos {{variavel}} e blocos que, na geração,
     expandem os registros marcados do caso.

  2) MARCAÇÕES (REL_TB03) — "marcar para o relatório" em cima de qualquer registro extraído,
     com comentário opcional. A validação de origem reaproveita _VERIFICACAO_ORIGEM de
     entidades_utils (mesmo isolamento por caso), e a resolução do registro em texto legível
     reaproveita o JOIN COALESCE de listar_entidades_inquerito.
"""

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from funcoes.db_utils import get_connection
from funcoes.entidades_utils import _VERIFICACAO_ORIGEM
from funcoes.ia_provedores import ErroProvedorIA, enviar_mensagem
# resolve o arquivo de origem (.ufdr, CSV do SIMBA) na pasta de dados, onde quer que ele esteja;
# organizacao_arquivos é leve de propósito — não puxa o módulo pesado de investigação
from funcoes.organizacao_arquivos import resolver as resolver_em_dados

# Tipos de bloco de um padrão — precisa bater com o CHECK CK03 do 45/46_*.sql:
#   capa/sumario/texto/tabela_campos → conteúdo escrito pelo admin
#   marcacoes/whatsapp/contas/extracoes → montados na geração a partir dos dados do caso
TIPOS_BLOCO_VALIDOS = {
    "capa", "sumario", "texto", "tabela_campos",
    "marcacoes", "whatsapp", "contas", "extracoes", "chat",
    # extrato bancário do SIMBA (ver funcoes/simba_utils.py e db/init/30_relatorios.sql)
    "bancario_contas", "bancario_contrapartes", "bancario_alertas",
    "bancario_arquivos",
    # RIF — Relatório de Inteligência Financeira do COAF (ver db/init/30_relatorios.sql)
    "rif_resumo", "rif_comunicacoes", "rif_contrapartes",
    "rif_envolvidos", "rif_ocorrencias", "rif_arquivos",
    # inquérito em PDF do ePol (ver funcoes/ipl_utils.py e db/init/30_relatorios.sql)
    "ipl_resumo", "ipl_pecas", "ipl_pessoas", "ipl_achados",
    # chaves PIX detectadas nas conversas (ver funcoes/pix_utils.py)
    "pix_chaves",
    # contas de nuvem e IPED (ver funcoes/contas_nuvem_utils.py e funcoes/iped_utils.py)
    "nuvem_perfil", "nuvem_dispositivos", "nuvem_acessos", "iped_extracoes",
}

# Rótulo curto de cada tipo de bloco. Vive aqui, e não só no JS, porque é usado em dois lugares
# no servidor — a etapa que a geração em background informa ao polling e a lista de blocos que a
# tela de geração oferece para marcar/desmarcar. Bloco sem título cai neste rótulo.
ROTULO_TP_BLOCO = {
    "capa": "Capa",
    "sumario": "Sumário",
    "texto": "Texto",
    "tabela_campos": "Tabela de campos",
    "marcacoes": "Registros marcados",
    "whatsapp": "WhatsApp (tabela)",
    "chat": "Conversas (tela de celular)",
    "contas": "Contas de usuário",
    "extracoes": "Arquivos de extração",
    "bancario_contas": "Bancário — contas",
    "bancario_contrapartes": "Bancário — contrapartes",
    "bancario_alertas": "Bancário — pontos de atenção",
    "bancario_arquivos": "Bancário — metadados dos arquivos",
    "rif_resumo": "RIF — síntese",
    "rif_comunicacoes": "RIF — comunicações",
    "rif_contrapartes": "RIF — contrapartes",
    "rif_envolvidos": "RIF — envolvidos",
    "rif_ocorrencias": "RIF — ocorrências",
    "rif_arquivos": "RIF — metadados dos arquivos",
    "ipl_resumo": "Inquérito (ePol) — síntese",
    "ipl_pecas": "Inquérito (ePol) — peças",
    "ipl_pessoas": "Inquérito (ePol) — pessoas citadas",
    "ipl_achados": "Inquérito (ePol) — achados",
    "pix_chaves": "Chaves PIX",
    "nuvem_perfil": "Nuvem — perfil da conta",
    "nuvem_dispositivos": "Nuvem — dispositivos",
    "nuvem_acessos": "Nuvem — acessos consolidados",
    "iped_extracoes": "IPED — casos importados",
}

# Variáveis preenchidas automaticamente a partir do caso (o resto dos {{tokens}} usados num
# padrão é considerado "manual" e pedido na hora de gerar). Mantido em sincronia com
# variaveis_auto() e exposto pela API para o painel de ajuda do construtor de padrões.
CATALOGO_VARIAVEIS_AUTO = [
    {"token": "numero_inquerito", "descricao": "Número do inquérito (ePol)"},
    {"token": "data_geracao", "descricao": "Data de geração (dd/mm/aaaa)"},
    {"token": "data_hora_geracao", "descricao": "Data e hora de geração"},
    {"token": "dispositivos", "descricao": "Aparelhos das extrações do inquérito"},
    {"token": "total_marcacoes", "descricao": "Quantidade de registros marcados no caso"},
    {"token": "investigador", "descricao": "Nome do usuário que gera o relatório"},
    # extrato bancário (SIMBA) — ficam em "—" quando o inquérito não tem extrato importado
    {"token": "bancario_contas", "descricao": "Quantidade de contas bancárias afastadas"},
    {"token": "bancario_titulares", "descricao": "Quantidade de titulares das contas afastadas"},
    {"token": "bancario_credito", "descricao": "Total de créditos do extrato bancário"},
    {"token": "bancario_debito", "descricao": "Total de débitos do extrato bancário"},
    {"token": "bancario_periodo", "descricao": "Período coberto pelo extrato bancário"},
    # RIF (SISCOAF) — ficam em "—" quando o inquérito não tem RIF importado
    {"token": "rif_quantidade", "descricao": "Quantidade de RIFs importados"},
    {"token": "rif_numeros", "descricao": "Números dos RIFs importados"},
    {"token": "rif_comunicacoes", "descricao": "Quantidade de comunicações"},
    {"token": "rif_envolvidos", "descricao": "Quantidade de envolvidos"},
    {"token": "rif_ocorrencias", "descricao": "Quantidade de ocorrências"},
    {"token": "rif_credito_total", "descricao": "Total de créditos nos RIFs"},
    {"token": "rif_debito_total", "descricao": "Total de débitos nos RIFs"},
    # inquérito em PDF (ePol) — ficam em "—" quando o inquérito não tem IPL importado
    {"token": "ipl_quantidade", "descricao": "Quantidade de inquéritos em PDF importados"},
    {"token": "ipl_numeros", "descricao": "Números dos inquéritos em PDF importados"},
    {"token": "ipl_folhas", "descricao": "Total de folhas dos autos importados"},
    {"token": "ipl_pecas", "descricao": "Quantidade de peças identificadas nos autos"},
    {"token": "ipl_pessoas", "descricao": "Quantidade de pessoas citadas nos autos"},
    {"token": "ipl_achados", "descricao": "Quantidade de achados extraídos dos autos"},
]
_NOMES_AUTO = {v["token"] for v in CATALOGO_VARIAVEIS_AUTO}

_TOKEN_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")

# Rótulo amigável de cada tipo de origem, para o cabeçalho de cada trecho marcado no relatório.
ROTULO_TP_ORIGEM = {
    "wpp_mensagem": "Mensagem de WhatsApp",
    "sms_mensagem": "Mensagem de SMS",
    "msg_mensagem": "Mensagem",
    "wpp_conversa": "Conversa de WhatsApp",
    "sms_conversa": "Conversa de SMS",
    "msg_conversa": "Conversa",
    "contato_agenda": "Contato da agenda",
    "contato_wpp": "Contato de WhatsApp",
    "contato_sms": "Contato de SMS",
    "contato_msg": "Contato",
    "ligacao": "Ligação",
    "localizacao": "Localização",
    "notificacao": "Notificação",
    "nota": "Nota (bloco de notas)",
    "navegacao": "Navegação",
    "aplicativo": "Aplicativo",
    "dono": "Dono do aparelho",
    "rif_comunicacao": "Comunicação de RIF",
    "rif_envolvido": "Envolvido em RIF",
    "rif_contraparte": "Transação de RIF",
    "simba_conta": "Conta afastada (SIMBA)",
    "simba_lancamento": "Lançamento bancário (SIMBA)",
    "simba_contraparte": "Contraparte bancária (SIMBA)",
    "ipl_pessoa": "Pessoa citada no inquérito",
    "ipl_achado": "Achado no inquérito",
    "ipei_alvo": "Alvo da quebra de sigilo fiscal",
    "ipei_relacionado": "Vínculo na quebra de sigilo fiscal",
    "ipei_conta": "Conta na e-Financeira (IPEI)",
    "ipei_bem": "Bem levantado na IPEI",
    "ipei_nota": "Nota fiscal (IPEI)",
    "ipei_contato": "Contato levantado na IPEI",
    "arquivo": "Arquivo do aparelho",
}

# mesma paleta do seletor de cor da marcação (ver investigacao.js/COR_NOMES) — usada para rotular
# o resumo de IA quando uma conversa tem mais de uma cor marcada
COR_NOMES = {
    "#E74C3C": "Vermelho", "#E67E22": "Laranja", "#F1C40F": "Amarelo",
    "#2ECC71": "Verde", "#1ABC9C": "Verde-água", "#3498DB": "Azul",
    "#9B59B6": "Roxo", "#E91E63": "Rosa", "#795548": "Marrom",
    # #FF9800 era "Laranja" como o #E67E22: duas cores diferentes saíam com a MESMA etiqueta no
    # .docx, e o único de nome dos marcadores (UX98) não aceitaria as duas
    "#607D8B": "Cinza", "#00BCD4": "Ciano", "#FF9800": "Âmbar",
}


def nome_cor(cor: str | None) -> str:
    if not cor:
        return "Preto"
    return COR_NOMES.get(cor.upper(), cor)


def marcadores_das_marcacoes(ids_marcacoes, id_usuario: int | None) -> dict[int, list[dict]]:
    """{id_marcacao: [{id_marcador, no_marcador, co_cor}, ...]} para os marcadores de um usuário.

    Uma consulta só, depois de a listagem principal já ter as linhas — bem mais simples do que
    pendurar um LATERAL em cada ramo do UNION de listar_chat_marcado."""
    ids = list({int(i) for i in (ids_marcacoes or []) if i is not None})
    if not ids or id_usuario is None:
        return {}
    query = """
        SELECT ap.id_marcacao, mc.id_marcador, mc.no_marcador, mc.co_cor
        FROM investigacao.mrc_tb02_aplicacoes ap
        JOIN investigacao.mrc_tb01_marcadores mc ON mc.id_marcador = ap.id_marcador
        WHERE ap.id_marcacao = ANY(%s) AND mc.id_usuario = %s
        ORDER BY mc.nu_ordem, lower(mc.no_marcador)
    """
    agrupado: dict[int, list[dict]] = {}
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (ids, id_usuario))
            for id_marcacao, id_marcador, nome, cor in cur.fetchall():
                agrupado.setdefault(id_marcacao, []).append(
                    {"id_marcador": id_marcador, "no_marcador": nome, "co_cor": cor}
                )
    return agrupado


def _anexar_marcadores(linhas: list[dict], id_usuario: int | None) -> list[dict]:
    """Preenche a chave 'marcadores' de cada linha que tenha id_marcacao."""
    mapa = marcadores_das_marcacoes([l.get("id_marcacao") for l in linhas], id_usuario)
    for linha in linhas:
        linha["marcadores"] = mapa.get(linha.get("id_marcacao"), [])
    return linhas


def rotulo_marcador(registro: dict) -> str:
    """Como o grupo de um registro marcado é chamado no relatório.

    Os nomes dos marcadores do investigador que está gerando ("Dinheiro", "TRÁFICO DE DROGAS"),
    unidos quando há mais de um. Sem nenhum marcador — caso anterior aos marcadores nomeados, ou
    marcado por outra pessoa — cai para o nome da COR, que era o rótulo antigo. É isso que faz o
    relatório de um caso velho continuar saindo igual ao de antes."""
    nomes = [m["no_marcador"] for m in (registro.get("marcadores") or []) if m.get("no_marcador")]
    if nomes:
        return " + ".join(nomes)
    return nome_cor(registro.get("co_cor"))


def cor_marcador(registro: dict) -> str | None:
    """Cor que representa o registro: a do primeiro marcador, senão a CO_COR antiga."""
    marcadores = registro.get("marcadores") or []
    if marcadores:
        return marcadores[0].get("co_cor")
    return registro.get("co_cor")


# =====================================================================
# Padrões de relatório (biblioteca global do admin)
# =====================================================================


def _validar_tipo_bloco(tp_bloco: str) -> str:
    tp_bloco = (tp_bloco or "").strip()
    if tp_bloco not in TIPOS_BLOCO_VALIDOS:
        raise ValueError(
            f"Tipo de bloco inválido: {tp_bloco!r} (use um de: {', '.join(sorted(TIPOS_BLOCO_VALIDOS))})."
        )
    return tp_bloco


def criar_padrao(nome: str, descricao: str | None, id_usuario: int | None = None) -> int:
    """Cria um padrão de relatório vazio (sem blocos ainda); devolve o ID."""
    nome = (nome or "").strip()
    if not nome:
        raise ValueError("O nome do padrão é obrigatório.")
    descricao = (descricao or "").strip() or None
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO investigacao.rel_tb01_padroes (no_padrao, tx_descricao, id_usuario_criador)
                VALUES (%s, %s, %s)
                RETURNING id_padrao
                """,
                (nome, descricao, id_usuario),
            )
            id_padrao = cur.fetchone()[0]
        conn.commit()
    return id_padrao


def listar_padroes() -> list[dict]:
    """Lista todos os padrões (ativos e inativos), com a contagem de blocos, mais recente primeiro."""
    query = """
        SELECT p.id_padrao, p.no_padrao, p.tx_descricao, p.in_ativo, p.dt_cadastro,
               u.no_usuario AS criado_por,
               (SELECT count(*) FROM investigacao.rel_tb02_blocos b WHERE b.id_padrao = p.id_padrao) AS qt_blocos
        FROM investigacao.rel_tb01_padroes p
        LEFT JOIN seguranca.usr_tb01_usuarios u ON u.id_usuario = p.id_usuario_criador
        ORDER BY p.dt_cadastro DESC
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query)
            colunas = [d.name for d in cur.description]
            return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


def listar_padroes_ativos() -> list[dict]:
    """Só os padrões ativos (id + nome + descrição) — o que a tela de geração oferece."""
    query = """
        SELECT id_padrao, no_padrao, tx_descricao
        FROM investigacao.rel_tb01_padroes
        WHERE in_ativo
        ORDER BY no_padrao
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query)
            colunas = [d.name for d in cur.description]
            return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


def obter_padrao(id_padrao: int) -> dict | None:
    """Devolve o padrão com seus blocos ordenados; None se não existir."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id_padrao, no_padrao, tx_descricao, in_ativo, dt_cadastro FROM investigacao.rel_tb01_padroes WHERE id_padrao = %s",
                (id_padrao,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            colunas = [d.name for d in cur.description]
            padrao = dict(zip(colunas, row))
            cur.execute(
                """
                SELECT id_bloco, nu_ordem, tp_bloco, no_titulo, tx_conteudo
                FROM investigacao.rel_tb02_blocos
                WHERE id_padrao = %s
                ORDER BY nu_ordem, id_bloco
                """,
                (id_padrao,),
            )
            colunas_b = [d.name for d in cur.description]
            padrao["blocos"] = [dict(zip(colunas_b, b)) for b in cur.fetchall()]
    return padrao


def atualizar_padrao(id_padrao: int, nome: str, descricao: str | None) -> bool:
    """Atualiza nome/descrição do padrão; devolve False se o id não existir."""
    nome = (nome or "").strip()
    if not nome:
        raise ValueError("O nome do padrão é obrigatório.")
    descricao = (descricao or "").strip() or None
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE investigacao.rel_tb01_padroes SET no_padrao = %s, tx_descricao = %s WHERE id_padrao = %s",
                (nome, descricao, id_padrao),
            )
            atualizado = cur.rowcount > 0
        conn.commit()
    return atualizado


def definir_ativo(id_padrao: int, ativo: bool) -> bool:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE investigacao.rel_tb01_padroes SET in_ativo = %s WHERE id_padrao = %s",
                (ativo, id_padrao),
            )
            atualizado = cur.rowcount > 0
        conn.commit()
    return atualizado


def remover_padrao(id_padrao: int) -> bool:
    """Remove um padrão e seus blocos (FK impede apagar o padrão antes dos blocos)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM investigacao.rel_tb02_blocos WHERE id_padrao = %s", (id_padrao,))
            cur.execute("DELETE FROM investigacao.rel_tb01_padroes WHERE id_padrao = %s", (id_padrao,))
            removido = cur.rowcount > 0
        conn.commit()
    return removido


# =====================================================================
# Blocos de um padrão
# =====================================================================


def criar_bloco(id_padrao: int, tp_bloco: str, titulo: str | None, conteudo: str | None) -> int:
    """Adiciona um bloco ao fim do padrão (nu_ordem = último + 1); devolve o ID."""
    tp_bloco = _validar_tipo_bloco(tp_bloco)
    titulo = (titulo or "").strip() or None
    conteudo = conteudo if (conteudo or "").strip() else None
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM investigacao.rel_tb01_padroes WHERE id_padrao = %s", (id_padrao,)
            )
            if cur.fetchone() is None:
                raise ValueError("Padrão não encontrado.")
            cur.execute(
                "SELECT COALESCE(max(nu_ordem), -1) + 1 FROM investigacao.rel_tb02_blocos WHERE id_padrao = %s",
                (id_padrao,),
            )
            ordem = cur.fetchone()[0]
            cur.execute(
                """
                INSERT INTO investigacao.rel_tb02_blocos (id_padrao, nu_ordem, tp_bloco, no_titulo, tx_conteudo)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id_bloco
                """,
                (id_padrao, ordem, tp_bloco, titulo, conteudo),
            )
            id_bloco = cur.fetchone()[0]
        conn.commit()
    return id_bloco


def atualizar_bloco(id_bloco: int, tp_bloco: str, titulo: str | None, conteudo: str | None) -> bool:
    """Atualiza tipo/título/conteúdo de um bloco; devolve False se o id não existir."""
    tp_bloco = _validar_tipo_bloco(tp_bloco)
    titulo = (titulo or "").strip() or None
    conteudo = conteudo if (conteudo or "").strip() else None
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE investigacao.rel_tb02_blocos SET tp_bloco = %s, no_titulo = %s, tx_conteudo = %s WHERE id_bloco = %s",
                (tp_bloco, titulo, conteudo, id_bloco),
            )
            atualizado = cur.rowcount > 0
        conn.commit()
    return atualizado


def remover_bloco(id_bloco: int) -> bool:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM investigacao.rel_tb02_blocos WHERE id_bloco = %s", (id_bloco,))
            removido = cur.rowcount > 0
        conn.commit()
    return removido


def mover_bloco(id_bloco: int, direcao: str) -> bool:
    """Troca a ordem de um bloco com o vizinho ('cima' ou 'baixo'). Devolve False se não há
    vizinho naquele sentido (bloco já é o primeiro/último) ou o bloco não existe."""
    if direcao not in ("cima", "baixo"):
        raise ValueError("Direção inválida (use 'cima' ou 'baixo').")
    comparador = "<" if direcao == "cima" else ">"
    ordenacao = "DESC" if direcao == "cima" else "ASC"
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id_padrao, nu_ordem FROM investigacao.rel_tb02_blocos WHERE id_bloco = %s",
                (id_bloco,),
            )
            row = cur.fetchone()
            if row is None:
                return False
            id_padrao, ordem = row
            cur.execute(
                f"""
                SELECT id_bloco, nu_ordem FROM investigacao.rel_tb02_blocos
                WHERE id_padrao = %s AND nu_ordem {comparador} %s
                ORDER BY nu_ordem {ordenacao}
                LIMIT 1
                """,
                (id_padrao, ordem),
            )
            vizinho = cur.fetchone()
            if vizinho is None:
                return False
            id_vizinho, ordem_vizinho = vizinho
            cur.execute("UPDATE investigacao.rel_tb02_blocos SET nu_ordem = %s WHERE id_bloco = %s", (ordem_vizinho, id_bloco))
            cur.execute("UPDATE investigacao.rel_tb02_blocos SET nu_ordem = %s WHERE id_bloco = %s", (ordem, id_vizinho))
        conn.commit()
    return True


# =====================================================================
# Marcações "marcar para o relatório"
# =====================================================================

# Tipos de origem (REL_TB03_MARCACOES.TP_ORIGEM) que pertencem a um "documento próprio":
# um RIF (SISCOAF) ou um extrato bancário (SIMBA/R304). São as âncoras da seção "Registros
# marcados pela equipe" dos relatórios de RIF e de dados bancários — ali SÓ entram marcações
# dos arquivos escolhidos na geração (ver gerar_relatorio_docx), nunca as de mensagens,
# contatos ou outras extrações.
TIPOS_ORIGEM_MARCACAO_RIF = ("rif_comunicacao", "rif_envolvido", "rif_contraparte")
TIPOS_ORIGEM_MARCACAO_SIMBA = ("simba_conta", "simba_lancamento", "simba_contraparte")

# Tipos de bloco das seções de RIF/extrato bancário — uma cópia única para a tela de geração
# (rotas/relatorios.py) e para o gerador (relatorios_docx.py) decidirem quando a seção de
# marcações deve ser restrita aos arquivos escolhidos. Espelha os valores permitidos de
# CK03_REL_TB02_TP_BLOCO (ver db/init/30_relatorios.sql).
TIPOS_BLOCO_RIF = {"rif_resumo", "rif_comunicacoes", "rif_contrapartes", "rif_envolvidos", "rif_ocorrencias", "rif_arquivos"}
TIPOS_BLOCO_BANCARIO = {"bancario_contas", "bancario_contrapartes", "bancario_alertas", "bancario_arquivos"}


def criar_marcacao(id_inquerito: int, tp_origem: str, id_origem: int, comentario: str | None, id_usuario: int | None = None, cor: str | None = None) -> tuple[int, bool]:
    """Marca um registro de origem para o relatório (comentário e cor opcionais); devolve
    (id_marcacao, ja_estava_marcado).

    IDEMPOTENTE por registro: marcar de novo o que já está marcado ATUALIZA a marcação
    existente em vez de criar outra (índice único UX95_REL_TB03_REGISTRO, ver
    db/init/30_relatorios.sql). Sem isso, marcar duas vezes sem perceber — o que acontece
    com facilidade em conversas longas e na marcação em lote — fazia o mesmo trecho sair
    duplicado no .docx.

    Na atualização nada é perdido: a cor nova substitui a antiga (foi uma escolha explícita do
    usuário agora), e o comentário novo é ACRESCENTADO numa nova linha, a menos que já esteja
    lá — comentário é registro do trabalho do analista, não um campo de rascunho.

    Levanta ValueError se o tipo de origem for desconhecido ou o id_origem não existir dentro
    do inquérito — mesma validação de isolamento por caso das entidades."""
    verificacao = _VERIFICACAO_ORIGEM.get(tp_origem)
    if verificacao is None:
        raise ValueError(f"Tipo de origem inválido: {tp_origem!r}")
    comentario = (comentario or "").strip() or None
    cor = (cor or "").strip() or None
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(verificacao, {"id_origem": id_origem, "id_inquerito": id_inquerito})
            if cur.fetchone() is None:
                raise ValueError("Registro de origem não encontrado neste inquérito.")
            cur.execute(
                """
                INSERT INTO investigacao.rel_tb03_marcacoes (id_inquerito, tp_origem, id_origem, tx_comentario, co_cor, id_usuario_criador)
                VALUES (%(inq)s, %(tp)s, %(id)s, %(comentario)s, %(cor)s, %(usuario)s)
                ON CONFLICT (id_inquerito, tp_origem, id_origem) DO UPDATE
                   SET tx_comentario = CASE
                           WHEN EXCLUDED.tx_comentario IS NULL THEN rel_tb03_marcacoes.tx_comentario
                           WHEN rel_tb03_marcacoes.tx_comentario IS NULL THEN EXCLUDED.tx_comentario
                           WHEN position(EXCLUDED.tx_comentario IN rel_tb03_marcacoes.tx_comentario) > 0
                                THEN rel_tb03_marcacoes.tx_comentario
                           ELSE rel_tb03_marcacoes.tx_comentario || E'\n' || EXCLUDED.tx_comentario
                       END,
                       co_cor = COALESCE(EXCLUDED.co_cor, rel_tb03_marcacoes.co_cor)
                RETURNING id_marcacao, (xmax <> 0) AS ja_existia
                """,
                {"inq": id_inquerito, "tp": tp_origem, "id": id_origem,
                 "comentario": comentario, "cor": cor, "usuario": id_usuario},
            )
            id_marcacao, ja_existia = cur.fetchone()
        conn.commit()
    return id_marcacao, bool(ja_existia)


def listar_marcacoes(id_inquerito: int, tp_origem: str, id_usuario: int | None = None) -> list[dict]:
    """Marcações de um tipo de origem, mais recente primeiro — usado pra marcar quais registros
    já foram incluídos e mostrar o histórico no popup do widget.

    Vem junto a lista de marcadores (ver marcadores_utils.py) aplicados por id_usuario: a
    marcação é compartilhada no inquérito, mas as etiquetas são individuais, então o widget
    precisa das MINHAS para pintar o botão e montar os chips — numa consulta só."""
    query = """
        SELECT m.id_marcacao, m.id_origem, m.tx_comentario, m.co_cor, m.dt_criacao,
               u.no_usuario AS criado_por,
               COALESCE(mk.marcadores, '[]'::json) AS marcadores
        FROM investigacao.rel_tb03_marcacoes m
        LEFT JOIN seguranca.usr_tb01_usuarios u ON u.id_usuario = m.id_usuario_criador
        LEFT JOIN LATERAL (
            SELECT json_agg(json_build_object('id_marcador', mc.id_marcador,
                                              'no_marcador', mc.no_marcador,
                                              'co_cor', mc.co_cor)
                            ORDER BY mc.nu_ordem, lower(mc.no_marcador)) AS marcadores
              FROM investigacao.mrc_tb02_aplicacoes ap
              JOIN investigacao.mrc_tb01_marcadores mc ON mc.id_marcador = ap.id_marcador
             WHERE ap.id_marcacao = m.id_marcacao AND mc.id_usuario = %(usr)s
        ) mk ON true
        WHERE m.id_inquerito = %(inq)s AND m.tp_origem = %(tp)s
        ORDER BY m.dt_criacao DESC
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, {"inq": id_inquerito, "tp": tp_origem, "usr": id_usuario})
            colunas = [d.name for d in cur.description]
            return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


def listar_marcacoes_inquerito(id_inquerito: int, id_usuario: int | None = None,
                               somente_origens: set[str] | None = None,
                               ids_rif: list[int] | None = None,
                               ids_extratos: list[int] | None = None) -> list[dict]:
    """TODAS as marcações do inquérito, com um trecho legível da origem (rotulo_origem), a data
    do registro (dt_origem) e de qual extração (.ufdr) ela veio (no_arquivo/no_dispositivo).

    Reaproveita o mesmo JOIN COALESCE de listar_entidades_inquerito. A extração sai do
    id_extracao do registro de origem — nas mensagens ele fica na CONVERSA, não na mensagem,
    daí os joins extras nas tabelas de conversa. Registros de RIF não vêm de UFDR (são do
    SISCOAF), então saem sem extração.

    Os três parâmetros extras servem à seção "Registros marcados pela equipe" dos relatórios de
    RIF e de dados bancários: somente_origens restringe aos tipos de origem daquele documento
    (TIPOS_ORIGEM_MARCACAO_RIF/TIPOS_ORIGEM_MARCACAO_SIMBA), e ids_rif/ids_extratos restringem
    às marcações dos arquivos escolhidos na tela de geração (None = todos os do inquérito). Sem
    filtro, o comportamento é o de sempre — todas as marcações."""
    query = """
        WITH base AS (
        SELECT m.id_marcacao, m.tp_origem, m.id_origem, m.tx_comentario, m.co_cor, m.dt_criacao,
               u.no_usuario AS criado_por,
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
                   arq.no_arquivo, arq.tx_caminho,
                   -- conversa marcada (triagem do contato): o "conteúdo" é a identificação do
                   -- fio. Vai num CASE, não solto no COALESCE, senão o nome do grupo viraria o
                   -- conteúdo de uma MENSAGEM sem texto (só anexo) da mesma conversa.
                   CASE WHEN m.tp_origem IN ('wpp_conversa', 'sms_conversa', 'msg_conversa')
                        THEN COALESCE(wc.no_grupo, wpp_ct.no_contato,
                                      sc.no_grupo, sms_ct.no_contato,
                                      mc.no_grupo, msg_ct.no_contato)
                   END
               ) AS rotulo_origem,
               arq.tx_caminho AS tx_caminho_arquivo,
               COALESCE(
                   -- chat messages: nome do grupo ou contato da conversa
                   wc.no_grupo, wpp_ct.no_contato,
                   sc.no_grupo, sms_ct.no_contato,
                   mc.no_grupo, msg_ct.no_contato,
                   -- demais tipos: mesmo campo do rotulo_origem
                   ca.no_contato, cw.no_contato, cs.no_contato, cm.no_contato,
                   lg.no_contato,
                   lo.tx_descricao, lo.tx_endereco,
                   nt.no_pacote_app,
                   nota.no_aplicativo,
                     nv.tx_titulo, nv.tx_url,
                     ap.no_aplicativo,
                     dn.no_dono,
                     em.no_fonte, em.no_remetente,
                     wf.no_ssid, wf.no_bssid,
                     cf.no_instituicao, cf.no_titular,
                    rifc.no_comunicante,
                    rife.no_envolvido,
                    rifcp.no_contraparte,
                    smbc.no_titular,
                    smbl.no_od,
                    smbcp.no_contraparte,
                    iplp.no_pessoa,
                    ipla.tx_valor,
                    arq.no_arquivo
                ) AS no_contato,
               COALESCE(
                   wm.dt_mensagem, sm.dt_mensagem, mm.dt_mensagem, lg.dt_ligacao, lo.dt_localizacao,
                   nt.dt_notificacao, COALESCE(nota.dt_modificacao, nota.dt_criacao),
                     nv.dt_visita, ap.dt_instalacao, dn.dt_ultimo_login,
                     em.dt_email,
                     COALESCE(wf.dt_ultima_conexao, wf.dt_primeira_conexao),
                     rifc.dt_recebimento,
                     smbl.dt_lancamento,
                     arq.dt_modificacao,
                     CASE WHEN m.tp_origem IN ('wpp_conversa', 'sms_conversa', 'msg_conversa')
                          THEN COALESCE(wc.dt_ultima_atividade, sc.dt_ultima_atividade, mc.dt_ultima_atividade)
                     END
                 ) AS dt_origem,
               COALESCE(
                   wc.id_extracao, sc.id_extracao, mc.id_extracao,
                   ca.id_extracao, cw.id_extracao, cs.id_extracao, cm.id_extracao,
                    lg.id_extracao, lo.id_extracao, nt.id_extracao, nota.id_extracao, nv.id_extracao,
                     em.id_extracao,
                     wf.id_extracao, cf.id_extracao,
                     ap.id_extracao, dn.id_extracao,
                     arq.id_extracao
               ) AS id_extracao,
               lo.vl_latitude, lo.vl_longitude, geo.tx_endereco AS tx_endereco_geo,
               -- aplicativo de origem: os mensageiros guardam o app na conversa/contato
               -- (tp_app); WhatsApp e SMS têm tabelas próprias, então saem como literal
               CASE m.tp_origem
                   WHEN 'wpp_mensagem' THEN 'WhatsApp'
                   WHEN 'contato_wpp' THEN 'WhatsApp'
                   WHEN 'sms_mensagem' THEN 'SMS'
                   WHEN 'contato_sms' THEN 'SMS'
                   WHEN 'msg_mensagem' THEN mc.tp_app
                   WHEN 'wpp_conversa' THEN 'WhatsApp'
                   WHEN 'sms_conversa' THEN 'SMS'
                   WHEN 'msg_conversa' THEN mc.tp_app
                   WHEN 'contato_msg' THEN cm.tp_app
                   WHEN 'contato_agenda' THEN 'Agenda do aparelho'
                   WHEN 'ligacao' THEN 'Telefonia'
                   WHEN 'localizacao' THEN lo.no_aplicativo
                   WHEN 'notificacao' THEN nt.no_pacote_app
                   WHEN 'nota' THEN nota.no_aplicativo
                     WHEN 'navegacao' THEN nv.no_aplicativo
                     WHEN 'email' THEN em.no_fonte
                     WHEN 'wifi' THEN wf.no_fonte
                     WHEN 'conta_financeira' THEN cf.no_fonte
                     WHEN 'aplicativo' THEN ap.no_aplicativo
                   WHEN 'dono' THEN 'Conta do aparelho'
                   WHEN 'rif_comunicacao' THEN 'RIF (SISCOAF)'
                   WHEN 'rif_envolvido' THEN 'RIF (SISCOAF)'
                   WHEN 'rif_contraparte' THEN 'RIF (SISCOAF)'
                   WHEN 'simba_conta' THEN 'Extrato bancário (SIMBA)'
                   WHEN 'simba_lancamento' THEN 'Extrato bancário (SIMBA)'
                   WHEN 'simba_contraparte' THEN 'Extrato bancário (SIMBA)'
                   WHEN 'arquivo' THEN 'Arquivos do aparelho'
               END AS no_aplicativo
        FROM investigacao.rel_tb03_marcacoes m
        LEFT JOIN seguranca.usr_tb01_usuarios u ON u.id_usuario = m.id_usuario_criador
        LEFT JOIN investigacao.wpp_tb04_mensagens wm ON m.tp_origem = 'wpp_mensagem' AND wm.id_mensagem = m.id_origem
        LEFT JOIN investigacao.sms_tb04_mensagens sm ON m.tp_origem = 'sms_mensagem' AND sm.id_mensagem = m.id_origem
        LEFT JOIN investigacao.msg_tb04_mensagens mm ON m.tp_origem = 'msg_mensagem' AND mm.id_mensagem = m.id_origem
        -- a conversa entra por dois caminhos: como a conversa-mãe da mensagem marcada, ou como o
        -- próprio registro marcado (tp_origem '*_conversa', a conversa marcada pela lista
        -- lateral do WhatsApp). Reusar o mesmo alias faz id_extracao/no_contato/participantes
        -- resolverem de graça nos dois casos.
        LEFT JOIN investigacao.wpp_tb02_conversas wc
               ON wc.id_conversa = COALESCE(wm.id_conversa, CASE WHEN m.tp_origem = 'wpp_conversa' THEN m.id_origem END)
        LEFT JOIN investigacao.sms_tb02_conversas sc
               ON sc.id_conversa = COALESCE(sm.id_conversa, CASE WHEN m.tp_origem = 'sms_conversa' THEN m.id_origem END)
        LEFT JOIN investigacao.msg_tb02_conversas mc
               ON mc.id_conversa = COALESCE(mm.id_conversa, CASE WHEN m.tp_origem = 'msg_conversa' THEN m.id_origem END)
        LEFT JOIN investigacao.ctt_tb01_contatos ca ON m.tp_origem = 'contato_agenda' AND ca.id_contato = m.id_origem
        LEFT JOIN investigacao.wpp_tb01_contatos cw ON m.tp_origem = 'contato_wpp' AND cw.id_contato = m.id_origem
        LEFT JOIN investigacao.sms_tb01_contatos cs ON m.tp_origem = 'contato_sms' AND cs.id_contato = m.id_origem
        LEFT JOIN investigacao.msg_tb01_contatos cm ON m.tp_origem = 'contato_msg' AND cm.id_contato = m.id_origem
        -- contato da conversa (interlocutor) para mensageiros — primeiro participante que não é o dono do aparelho
        LEFT JOIN LATERAL (SELECT p.no_contato FROM investigacao.wpp_tb03_participantes pp JOIN investigacao.wpp_tb01_contatos p ON p.id_contato = pp.id_contato AND NOT COALESCE(p.in_dono_dispositivo, false) WHERE pp.id_conversa = wc.id_conversa LIMIT 1) wpp_ct ON m.tp_origem IN ('wpp_mensagem', 'wpp_conversa')
        LEFT JOIN LATERAL (SELECT p.no_contato FROM investigacao.sms_tb03_participantes pp JOIN investigacao.sms_tb01_contatos p ON p.id_contato = pp.id_contato AND NOT COALESCE(p.in_dono_dispositivo, false) WHERE pp.id_conversa = sc.id_conversa LIMIT 1) sms_ct ON m.tp_origem IN ('sms_mensagem', 'sms_conversa')
        LEFT JOIN LATERAL (SELECT p.no_contato FROM investigacao.msg_tb03_participantes pp JOIN investigacao.msg_tb01_contatos p ON p.id_contato = pp.id_contato AND NOT COALESCE(p.in_dono_dispositivo, false) WHERE pp.id_conversa = mc.id_conversa LIMIT 1) msg_ct ON m.tp_origem IN ('msg_mensagem', 'msg_conversa')
        LEFT JOIN investigacao.lig_tb01_ligacoes lg ON m.tp_origem = 'ligacao' AND lg.id_ligacao = m.id_origem
        LEFT JOIN investigacao.loc_tb01_localizacoes lo ON m.tp_origem = 'localizacao' AND lo.id_localizacao = m.id_origem
        -- endereço da geocodificação reversa: o arredondamento a 5 casas TEM que ser feito no SQL
        -- nos dois lados (ver 15_localizacao.sql), senão o join não casa
        LEFT JOIN investigacao.geo_tb01_enderecos geo
               ON round(geo.vl_latitude, 5) = round(lo.vl_latitude, 5)
              AND round(geo.vl_longitude, 5) = round(lo.vl_longitude, 5)
        LEFT JOIN investigacao.ntf_tb01_notificacoes nt ON m.tp_origem = 'notificacao' AND nt.id_notificacao = m.id_origem
        LEFT JOIN investigacao.not_tb01_notas nota ON m.tp_origem = 'nota' AND nota.id_nota = m.id_origem
        LEFT JOIN investigacao.nav_tb01_paginas_visitadas nv ON m.tp_origem = 'navegacao' AND nv.id_pagina = m.id_origem
        LEFT JOIN investigacao.eml_tb01_emails em ON m.tp_origem = 'email' AND em.id_email = m.id_origem
        LEFT JOIN investigacao.wifi_tb01_redes wf ON m.tp_origem = 'wifi' AND wf.id_rede_wifi = m.id_origem
        LEFT JOIN investigacao.bnc_tb01_contas_financeiras cf ON m.tp_origem = 'conta_financeira' AND cf.id_conta_financeira = m.id_origem
        LEFT JOIN investigacao.app_tb01_aplicativos ap ON m.tp_origem = 'aplicativo' AND ap.id_aplicativo = m.id_origem
        LEFT JOIN investigacao.don_tb01_donos dn ON m.tp_origem = 'dono' AND dn.id_dono = m.id_origem
        LEFT JOIN investigacao.rif_tb02_comunicacoes rifc ON m.tp_origem = 'rif_comunicacao' AND rifc.id_comunicacao_rif = m.id_origem
        LEFT JOIN investigacao.rif_tb03_envolvidos rife ON m.tp_origem = 'rif_envolvido' AND rife.id_envolvido_rif = m.id_origem
        LEFT JOIN investigacao.rif_tb05_contrapartes rifcp ON m.tp_origem = 'rif_contraparte' AND rifcp.id_contraparte_rif = m.id_origem
        LEFT JOIN investigacao.smb_tb02_contas smbc ON m.tp_origem = 'simba_conta' AND smbc.id_conta_simba = m.id_origem
        LEFT JOIN investigacao.smb_tb03_lancamentos smbl ON m.tp_origem = 'simba_lancamento' AND smbl.id_lancamento_simba = m.id_origem
        LEFT JOIN investigacao.smb_tb04_contrapartes smbcp ON m.tp_origem = 'simba_contraparte' AND smbcp.id_contraparte_simba = m.id_origem
        -- RIF/extrato da marcação de "documento próprio": é por eles que a seção "Registros
        -- marcados pela equipe" restringe às escolhas da tela de geração (uma marcação de
        -- mensagem não casa aqui — COALESCE vira NULL e o join não prende nada)
        LEFT JOIN investigacao.rif_tb01_rifs rrif
               ON rrif.id_rif = COALESCE(rifc.id_rif, rife.id_rif, rifcp.id_rif)
        LEFT JOIN investigacao.smb_tb01_extratos sext
               ON sext.id_extrato = COALESCE(smbc.id_extrato, smbl.id_extrato, smbcp.id_extrato)
        LEFT JOIN investigacao.ipl_tb03_pessoas iplp ON m.tp_origem = 'ipl_pessoa' AND iplp.id_pessoa_ipl = m.id_origem
        LEFT JOIN investigacao.ipl_tb04_achados ipla ON m.tp_origem = 'ipl_achado' AND ipla.id_achado_ipl = m.id_origem
        LEFT JOIN investigacao.arq_tb01_arquivos arq ON m.tp_origem = 'arquivo' AND arq.id_arquivo = m.id_origem
        WHERE m.id_inquerito = %(inq)s
          -- restringe aos tipos de origem do documento (RIF ou extrato bancário) quando o
          -- relatório for um deles; sem isso (None), vale o comportamento de sempre. O cast
          -- explícito é obrigatório: o mesmo parâmetro aparece em "IS NULL" e em "ANY", e sem
          -- o cast o PostgreSQL não infere o tipo do array (erro de geração, não de dados)
          AND (%(somente)s::text[] IS NULL OR m.tp_origem = ANY(%(somente)s::text[]))
          -- e, dentro dos tipos do documento, só as marcações dos arquivos escolhidos na geração
          AND (
                %(ids_rif)s::bigint[] IS NULL
                OR (m.tp_origem = ANY(%(tipos_rif)s::text[]) AND rrif.id_rif = ANY(%(ids_rif)s::bigint[]))
              )
          AND (
                %(ids_extratos)s::bigint[] IS NULL
                OR (m.tp_origem = ANY(%(tipos_simba)s::text[]) AND sext.id_extrato = ANY(%(ids_extratos)s::bigint[]))
              )
        )
        SELECT base.*, ex.no_arquivo, ex.no_dispositivo
        FROM base
        LEFT JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = base.id_extracao
        ORDER BY base.tp_origem, base.dt_criacao
    """
    params = {
        "inq": id_inquerito,
        "somente": list(somente_origens) if somente_origens else None,
        "ids_rif": list(ids_rif) if ids_rif is not None else None,
        "ids_extratos": list(ids_extratos) if ids_extratos is not None else None,
        "tipos_rif": list(TIPOS_ORIGEM_MARCACAO_RIF),
        "tipos_simba": list(TIPOS_ORIGEM_MARCACAO_SIMBA),
    }
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            colunas = [d.name for d in cur.description]
            marcacoes = [dict(zip(colunas, linha)) for linha in cur.fetchall()]

    for m in marcacoes:
        if m["tp_origem"] == "localizacao":
            m["rotulo_origem"] = _conteudo_localizacao(m)
        elif m["tp_origem"] == "arquivo":
            m["rotulo_origem"] = _conteudo_arquivo(m)
    marcacoes = deduplicar_por_registro(marcacoes, lambda m: (m["tp_origem"], m["id_origem"]))
    return _anexar_marcadores(marcacoes, id_usuario)


def deduplicar_por_registro(linhas: list[dict], chave) -> list[dict]:
    """Uma linha por registro de origem, mesmo que ele tenha sido marcado mais de uma vez.

    O índice único UX95_REL_TB03_REGISTRO (30_relatorios.sql) já impede a marcação
    repetida, mas a geração do relatório não pode depender disso: um banco com a migração
    ainda não aplicada continuaria repetindo o mesmo trecho no .docx — que é justamente o que
    o investigador não tem como conferir depois, e é a falha mais cara aqui (relatório
    assinado com a mesma mensagem duas vezes).

    Fica a primeira ocorrência (a marcação mais antiga); o comentário das repetições é anexado
    ao dela quando traz texto novo, e a cor é aproveitada se a primeira não tiver nenhuma."""
    unicas: dict = {}
    for linha in linhas:
        k = chave(linha)
        mantida = unicas.get(k)
        if mantida is None:
            unicas[k] = linha
            continue
        novo = (linha.get("tx_comentario") or "").strip()
        atual = (mantida.get("tx_comentario") or "").strip()
        if novo and novo not in atual:
            mantida["tx_comentario"] = (atual + "\n" + novo) if atual else novo
        if not mantida.get("co_cor") and linha.get("co_cor"):
            mantida["co_cor"] = linha["co_cor"]
    return list(unicas.values())


def _conteudo_localizacao(m: dict) -> str | None:
    """Localização quase nunca tem descrição/endereço no próprio UFDR — o que ela sempre tem é
    a coordenada. Monta endereço (do UFDR ou da geocodificação reversa) + as coordenadas, para
    o registro não sair como "(sem conteúdo textual)" no relatório."""
    partes = []
    endereco = m.get("rotulo_origem") or m.get("tx_endereco_geo")
    if endereco:
        partes.append(str(endereco))
    lat, lon = m.get("vl_latitude"), m.get("vl_longitude")
    if lat is not None and lon is not None:
        partes.append(f"Coordenadas: {lat}, {lon}")
    return "\n".join(partes) if partes else None


def _conteudo_arquivo(m: dict) -> str | None:
    """Arquivo do aparelho: nome + caminho completo. O caminho é o que localiza a mídia na
    perícia (DCIM/Camera, pasta do WhatsApp, …), então sai junto do nome no relatório."""
    partes: list[str] = []
    for valor in (m.get("rotulo_origem"), m.get("tx_caminho_arquivo")):
        if valor and str(valor) not in partes:
            partes.append(str(valor))
    return "\n".join(partes) if partes else None


def atualizar_marcacao(id_marcacao: int, comentario: str | None) -> bool:
    """Troca o comentário (opcional) de uma marcação; devolve False se o id não existir."""
    comentario = (comentario or "").strip() or None
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE investigacao.rel_tb03_marcacoes SET tx_comentario = %s WHERE id_marcacao = %s",
                (comentario, id_marcacao),
            )
            atualizado = cur.rowcount > 0
        conn.commit()
    return atualizado


def remover_marcacao(id_marcacao: int) -> bool:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM investigacao.rel_tb03_marcacoes WHERE id_marcacao = %s", (id_marcacao,))
            removido = cur.rowcount > 0
        conn.commit()
    return removido


def obter_id_inquerito_de_marcacao(id_marcacao: int) -> int | None:
    """Resolve o id_inquerito dono de uma marcação, para checar permissão em rotas que só
    recebem id_marcacao. None se não existir."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id_inquerito FROM investigacao.rel_tb03_marcacoes WHERE id_marcacao = %s", (id_marcacao,))
            row = cur.fetchone()
            return row[0] if row else None


# =====================================================================
# Geração do relatório (variáveis + dados; o .docx é montado em relatorios_docx.py)
# =====================================================================


TZ_BR = ZoneInfo("America/Sao_Paulo")


def para_hora_local(dt):
    """Converte um datetime do banco (naive, gravado em UTC) para America/Sao_Paulo. Datetime
    já aware é só convertido; None e valores sem hora (date) passam sem mudança."""
    if not isinstance(dt, datetime):
        return dt
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc).astimezone(TZ_BR)
    return dt.astimezone(TZ_BR)


def _iso_para_datetime(valor: str):
    """Lê uma string ISO de datetime. Sem fuso, vale como UTC (naive) — o mesmo contrato que a
    tela assume (ver formatarDataHora em web/js/utils/utils.js); None quando não é um datetime."""
    try:
        return datetime.fromisoformat(valor)
    except (ValueError, TypeError):
        return None


def formatar_data_hora(dt) -> str:
    """dd/mm/aaaa HH:MM:SS -03:00 — carimbo completo, sem ambiguidade (mesmo formato da bolha
    do WhatsApp, ver formatarCarimbo em whatsapp.js). dt vem naive do banco (TIMESTAMP sem fuso,
    gravado em UTC — o mesmo contrato que a tela assume ao exibir); convertemos para Brasília
    antes de formatar, e o deslocamento anexado é o correto pra aquele dia (o horário de verão
    foi extinto em 2019, mas dados antigos do UFDR podem cair num período em que ele valia —
    ZoneInfo resolve isso sem hardcode). Datas que chegam como string ISO (a mensagem citada
    embutida no JSON, por exemplo) são convertidas antes, com a mesma regra."""
    if not dt:
        return ""
    if isinstance(dt, str):
        dt = _iso_para_datetime(dt) or dt
    if isinstance(dt, datetime):
        dt = para_hora_local(dt)
        offset = dt.strftime("%z")  # "-0300" ou "-0200" (horário de verão)
        offset = offset[:3] + ":" + offset[3:]
        return dt.strftime("%d/%m/%Y %H:%M:%S") + " " + offset
    return str(dt)


def formatar_data(d) -> str:
    """Só a data (dd/mm/aaaa) — o extrato bancário trabalha em dia, não em hora."""
    if not d:
        return "—"
    if isinstance(d, (datetime, date)):
        return d.strftime("%d/%m/%Y")
    return str(d)


def formatar_periodo(inicio, fim) -> str:
    if not inicio and not fim:
        return "—"
    return f"{formatar_data(inicio)} a {formatar_data(fim)}"


def formatar_reais(v) -> str:
    """R$ no padrão brasileiro. Vale para Decimal (é o que o Postgres devolve em NUMERIC)."""
    try:
        n = float(v or 0)
    except (TypeError, ValueError):
        return "—"
    return "R$ " + f"{n:,.2f}".replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def formatar_documento(doc) -> str:
    """CPF/CNPJ com pontuação — no banco fica só com dígitos (ver simba_utils._doc)."""
    s = "".join(ch for ch in str(doc or "") if ch.isdigit())
    if len(s) == 11:
        return f"{s[:3]}.{s[3:6]}.{s[6:9]}-{s[9:]}"
    if len(s) == 14:
        return f"{s[:2]}.{s[2:5]}.{s[5:8]}/{s[8:12]}-{s[12:]}"
    return s or "—"


def obter_numero_inquerito(id_inquerito: int) -> str:
    """Número do inquérito no ePol; "" se não existir. Usado para nomear o arquivo gerado."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT nu_inquerito FROM investigacao.inq_tb01_inqueritos WHERE id_inquerito = %s",
                (id_inquerito,),
            )
            row = cur.fetchone()
    return (row[0] or "") if row else ""


def variaveis_auto(id_inquerito: int, usuario: dict | None = None) -> dict:
    """Valores das variáveis automáticas (ver CATALOGO_VARIAVEIS_AUTO) para um inquérito."""
    agora = datetime.now()
    numero = ""
    dispositivos = []
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT nu_inquerito FROM investigacao.inq_tb01_inqueritos WHERE id_inquerito = %s", (id_inquerito,))
            row = cur.fetchone()
            if row:
                numero = row[0] or ""
            cur.execute(
                "SELECT DISTINCT no_dispositivo FROM investigacao.inq_tb02_extracoes WHERE id_inquerito = %s AND no_dispositivo IS NOT NULL",
                (id_inquerito,),
            )
            dispositivos = [r[0] for r in cur.fetchall() if r[0]]
            cur.execute("SELECT count(*) FROM investigacao.rel_tb03_marcacoes WHERE id_inquerito = %s", (id_inquerito,))
            total_marcacoes = cur.fetchone()[0]
            # dados do RIF (SISCOAF)
            cur.execute(
                """
                SELECT count(*), coalesce(sum(qt_comunicacoes), 0), coalesce(sum(qt_envolvidos), 0),
                       coalesce(sum(qt_ocorrencias), 0), string_agg(nu_rif, '; ' ORDER BY nu_rif)
                FROM investigacao.rif_tb01_rifs
                WHERE id_inquerito = %s
                """,
                (id_inquerito,),
            )
            rif_row = cur.fetchone()
            tem_rif = bool(rif_row and rif_row[0])
            rif_qt = rif_row[0] if tem_rif else 0
            rif_coms = rif_row[1] if tem_rif else 0
            rif_envs = rif_row[2] if tem_rif else 0
            rif_ocos = rif_row[3] if tem_rif else 0
            rif_nums = rif_row[4] if tem_rif else ""
            # totais financeiros do RIF (vl_campo_b = crédito, vl_campo_c = débito)
            cur.execute(
                """
                SELECT coalesce(sum(coalesce(vl_campo_b, 0)), 0), coalesce(sum(coalesce(vl_campo_c, 0)), 0)
                FROM investigacao.rif_tb02_comunicacoes c
                JOIN investigacao.rif_tb01_rifs r ON r.id_rif = c.id_rif
                WHERE r.id_inquerito = %s
                """,
                (id_inquerito,),
            )
            rif_vls = cur.fetchone()
            rif_cred = rif_vls[0] if rif_vls and tem_rif else 0
            rif_deb = rif_vls[1] if rif_vls and tem_rif else 0
            # inquérito em PDF (ePol) — os contadores já vêm somados na linha do IPL
            cur.execute(
                """
                SELECT count(*), coalesce(sum(qt_folhas), 0), coalesce(sum(qt_pecas), 0),
                       coalesce(sum(qt_pessoas), 0), coalesce(sum(qt_achados), 0),
                       string_agg(nu_ipl, '; ' ORDER BY nu_ipl)
                FROM investigacao.ipl_tb01_inqueritos_pdf
                WHERE id_inquerito = %s
                """,
                (id_inquerito,),
            )
            ipl_row = cur.fetchone()
            tem_ipl = bool(ipl_row and ipl_row[0])
            # só extratos concluídos: um em andamento tem totais parciais, e número parcial
            # dentro de um relatório assinado é pior que número nenhum
            cur.execute(
                """
                SELECT coalesce(sum(qt_contas), 0), coalesce(sum(qt_titulares), 0),
                       coalesce(sum(vl_credito), 0), coalesce(sum(vl_debito), 0),
                       min(dt_primeiro_lancamento), max(dt_ultimo_lancamento), count(*)
                FROM investigacao.smb_tb01_extratos
                WHERE id_inquerito = %s AND cd_status = 'concluido'
                """,
                (id_inquerito,),
            )
            bc = cur.fetchone()

    tem_extrato = bool(bc and bc[6])
    return {
        "numero_inquerito": numero,
        "data_geracao": agora.strftime("%d/%m/%Y"),
        "data_hora_geracao": agora.strftime("%d/%m/%Y %H:%M"),
        "dispositivos": ", ".join(dispositivos) if dispositivos else "—",
        "total_marcacoes": str(total_marcacoes),
        "investigador": (usuario or {}).get("no_usuario", "") or "",
        "bancario_contas": str(bc[0]) if tem_extrato else "—",
        "bancario_titulares": str(bc[1]) if tem_extrato else "—",
        "bancario_credito": formatar_reais(bc[2]) if tem_extrato else "—",
        "bancario_debito": formatar_reais(bc[3]) if tem_extrato else "—",
        "bancario_periodo": (formatar_periodo(bc[4], bc[5]) if tem_extrato else "—"),
        "rif_quantidade": str(rif_qt) if tem_rif else "—",
        "rif_numeros": rif_nums if tem_rif else "—",
        "rif_comunicacoes": str(rif_coms) if tem_rif else "—",
        "rif_envolvidos": str(rif_envs) if tem_rif else "—",
        "rif_ocorrencias": str(rif_ocos) if tem_rif else "—",
        "rif_credito_total": formatar_reais(rif_cred) if tem_rif else "—",
        "rif_debito_total": formatar_reais(rif_deb) if tem_rif else "—",
        "ipl_quantidade": str(ipl_row[0]) if tem_ipl else "—",
        "ipl_numeros": (ipl_row[5] or "—") if tem_ipl else "—",
        "ipl_folhas": str(ipl_row[1]) if tem_ipl else "—",
        "ipl_pecas": str(ipl_row[2]) if tem_ipl else "—",
        "ipl_pessoas": str(ipl_row[3]) if tem_ipl else "—",
        "ipl_achados": str(ipl_row[4]) if tem_ipl else "—",
    }


def _tokens_do_padrao(padrao: dict) -> set[str]:
    tokens: set[str] = set()
    for bloco in padrao.get("blocos", []):
        for parte in (bloco.get("no_titulo"), bloco.get("tx_conteudo")):
            if parte:
                tokens.update(_TOKEN_RE.findall(parte))
    return tokens


def tokens_manuais(padrao: dict) -> list[str]:
    """Tokens {{...}} usados no padrão que não são preenchidos automaticamente — a tela de
    geração pede o valor de cada um. Ordenados para uma UI estável."""
    return sorted(_tokens_do_padrao(padrao) - _NOMES_AUTO)


def substituir_texto(texto: str | None, valores: dict) -> str:
    """Substitui {{token}} pelos valores (texto puro, para o .docx). Tokens sem valor viram
    o placeholder original, para o autor notar o que faltou."""
    if not texto:
        return ""

    def troca(m):
        nome = m.group(1)
        if nome in valores and str(valores[nome]).strip() != "":
            return str(valores[nome])
        return "{{" + nome + "}}"

    return _TOKEN_RE.sub(troca, texto)


def montar_valores(id_inquerito: int, valores_manuais: dict | None, usuario: dict | None) -> dict:
    """Junta as variáveis automáticas do caso com as manuais informadas na geração."""
    valores = variaveis_auto(id_inquerito, usuario)
    valores.update(valores_manuais or {})
    return valores


def marcacoes_filtradas(id_inquerito: int, ids_marcacoes: list[int] | None,
                        id_usuario: int | None = None,
                        somente_origens: set[str] | None = None,
                        ids_rif: list[int] | None = None,
                        ids_extratos: list[int] | None = None) -> list[dict]:
    """Marcações do inquérito, restritas às selecionadas (ou todas, se ids_marcacoes for None).

    somente_origens/ids_rif/ids_extratos repassam os filtros de "documento próprio" para
    listar_marcacoes_inquerito — a seção "Registros marcados pela equipe" dos relatórios de RIF
    e de dados bancários só mostra as marcações dos arquivos escolhidos na geração."""
    marcacoes = listar_marcacoes_inquerito(
        id_inquerito, id_usuario,
        somente_origens=somente_origens,
        ids_rif=ids_rif,
        ids_extratos=ids_extratos,
    )
    if ids_marcacoes is not None:
        selecionadas = set(ids_marcacoes)
        marcacoes = [m for m in marcacoes if m["id_marcacao"] in selecionadas]
    return marcacoes


# Coluna do identificador (telefone/JID/username) de cada família de tabelas de mensagem.
_COLUNA_IDENT_CONTATO = {"wpp": "nu_whatsapp_id", "sms": "nu_telefone", "msg": "nu_identificador"}


def _completar_remetente_dono(linhas: list[dict], chave) -> None:
    """Nomeia o dono do aparelho nas mensagens ENVIADAS em que a extração não gravou o remetente.

    O Cellebrite só põe o <Party> "From" em parte das mensagens de saída: o resto vem sem
    remetente nenhum, e o gravador da ingestão deixa ID_CONTATO_REMETENTE nulo marcando apenas
    IN_ENVIADA (ver investigacao_utils._gravar_conversa). Na mesma conversa isso produzia dois
    cabeçalhos de bolha para a MESMA pessoa — umas com "Eu (5527...@s.whatsapp.net)", que veio
    do contato, e outras com o genérico "Dono do aparelho".

    Aqui a identidade que falta é recuperada do próprio contato do dono: primeiro entre os
    participantes da conversa (é a identidade com que aquele chat nomeia o dono), e, se ele não
    estiver na lista, entre os contatos da extração marcados com IN_DONO_DISPOSITIVO.

    O nome só é aproveitado quando há UM único candidato a dono no escopo. O IsPhoneOwner do
    UFDR, de onde vem essa marca, é confiável no WhatsApp (um contato por extração) e sofrível
    no SMS, onde o Cellebrite chega a marcar mais de dez números da mesma extração — atribuir
    uma mensagem enviada ao primeiro deles seria pôr na peça um nome que não é o do dono. Na
    dúvida (nenhum candidato ou mais de um), a bolha continua caindo no rótulo genérico.

    `chave(m)` devolve (prefixo, id_conversa, id_extracao, tp_app) da linha — os mensageiros
    guardam contatos por (extração, app), e o dono do Telegram não pode nomear o do Instagram.
    """
    pendentes = [m for m in linhas
                 if m.get("in_enviada") and not (m.get("remetente") or m.get("remetente_jid"))]
    if not pendentes:
        return

    conversas: dict[str, set] = {}
    extracoes: dict[str, set] = {}
    for m in pendentes:
        prefixo, id_conversa, id_extracao, _app = chave(m)
        if id_conversa is not None:
            conversas.setdefault(prefixo, set()).add(id_conversa)
        if id_extracao is not None:
            extracoes.setdefault(prefixo, set()).add(id_extracao)

    candidatos_conversa: dict[tuple, list] = {}
    candidatos_extracao: dict[tuple, list] = {}
    with get_connection() as conn:
        with conn.cursor() as cur:
            for prefixo, ids in conversas.items():
                cur.execute(
                    f"""
                    SELECT pa.id_conversa, d.no_contato, d.{_COLUNA_IDENT_CONTATO[prefixo]}
                      FROM investigacao.{prefixo}_tb03_participantes pa
                      JOIN investigacao.{prefixo}_tb01_contatos d ON d.id_contato = pa.id_contato
                     WHERE pa.id_conversa = ANY(%s) AND d.in_dono_dispositivo
                    """,
                    (list(ids),),
                )
                for id_conversa, nome, ident in cur.fetchall():
                    candidatos_conversa.setdefault((prefixo, id_conversa), []).append((nome, ident))
            for prefixo, ids in extracoes.items():
                app = "d.tp_app" if prefixo == "msg" else "''"
                cur.execute(
                    f"""
                    SELECT d.id_extracao, {app}, d.no_contato, d.{_COLUNA_IDENT_CONTATO[prefixo]}
                      FROM investigacao.{prefixo}_tb01_contatos d
                     WHERE d.id_extracao = ANY(%s) AND d.in_dono_dispositivo
                    """,
                    (list(ids),),
                )
                for id_extracao, tp_app, nome, ident in cur.fetchall():
                    candidatos_extracao.setdefault((prefixo, id_extracao, tp_app), []).append((nome, ident))

    por_conversa = {k: v[0] for k, v in candidatos_conversa.items() if len(v) == 1}
    por_extracao = {k: v[0] for k, v in candidatos_extracao.items() if len(v) == 1}

    for m in pendentes:
        prefixo, id_conversa, id_extracao, tp_app = chave(m)
        dono = por_conversa.get((prefixo, id_conversa))
        if dono is None:
            dono = por_extracao.get((prefixo, id_extracao, tp_app if prefixo == "msg" else ""))
        if dono:
            m["remetente"], m["remetente_jid"] = dono


def listar_whatsapp_marcado(id_inquerito: int, ids_marcacoes: list[int] | None,
                            id_usuario: int | None = None,
                            expandir_conversas: bool = False) -> list[dict]:
    """Mensagens de WhatsApp marcadas para o relatório, agrupadas por conversa (seção 6 do
    modelo PF). Cada conversa vira {titulo, mensagens:[...]} com remetente, se é do dono do
    aparelho (in_enviada → cor verde), corpo, anexos e transcrições.

    `expandir_conversas=True` traz o fio completo das CONVERSAS marcadas (mesma semântica de
    listar_chat_marcado): sem isso a marcação da conversa é só triagem e nenhuma bolha sai.

    Cada anexo traz também mime + onde o binário está (caminho_zip dentro de qual .ufdr), para
    o relatorios_docx.py embutir a imagem no .docx lendo sob demanda do arquivo original, e o
    MD5 do binário (hash_md5), que sai impresso embaixo do nome do arquivo na coluna "Anexo".

    Além de 'mensagens' (a conversa inteira, na ordem), vem 'trechos': as mesmas mensagens
    quebradas nos pontos em que o fio original foi pulado — ver dividir_em_trechos."""
    query = """
        SELECT m.id_mensagem, rel.id_marcacao, m.tx_mensagem, m.dt_mensagem, m.in_enviada, m.in_encaminhada,
               m.vl_latitude, m.vl_longitude,
               ct.no_contato AS remetente, ct.nu_whatsapp_id AS remetente_jid,
               cv.id_conversa, cv.id_extracao, cv.no_grupo,
               (SELECT count(*) FROM investigacao.wpp_tb04_mensagens mo
                 WHERE mo.id_conversa = m.id_conversa AND mo.id_mensagem <= m.id_mensagem) AS nu_ordem,
               ex.no_arquivo, ex.no_dispositivo, rel.co_cor,
               -- 'hash_md5': o anexo NÃO tem coluna de hash — WPP_TB05_ANEXOS guarda só o
               -- ponteiro para o binário (CO_CAMINHO_ZIP). O MD5 mora na linha do arquivo do
               -- aparelho, onde o parser o põe lendo o próprio <file> do UFDR; o casamento por
               -- CO_CAMINHO_ZIP é o mesmo que a tela "Comparar celulares" faz, ao contrário.
               -- LIMIT 1: o mesmo binário do ZIP pode ser apontado por mais de um <file>, e aí
               -- o hash é igual nos dois — é o mesmo conteúdo.
               (SELECT json_agg(json_build_object('nome', a.no_arquivo, 'titulo', a.tx_titulo, 'transcricao', a.tx_transcricao,
                                                  'tp_conteudo', a.tp_conteudo,
                                                  'caminho_zip', a.co_caminho_zip,
                                                  'hash_md5', (SELECT ar.co_hash_md5
                                                                 FROM investigacao.arq_tb01_arquivos ar
                                                                WHERE ar.id_extracao = cv.id_extracao
                                                                  AND ar.co_caminho_zip = a.co_caminho_zip
                                                                LIMIT 1),
                                                  'ufdr', ex.no_arquivo)
                                ORDER BY a.id_anexo)
                  FROM investigacao.wpp_tb05_anexos a WHERE a.id_mensagem = m.id_mensagem) AS anexos
        FROM investigacao.rel_tb03_marcacoes rel
        JOIN investigacao.wpp_tb04_mensagens m
             ON (rel.tp_origem = 'wpp_mensagem' AND m.id_mensagem = rel.id_origem)
             OR (%(expandir)s::boolean AND rel.tp_origem = 'wpp_conversa' AND m.id_conversa = rel.id_origem)
        JOIN investigacao.wpp_tb02_conversas cv ON cv.id_conversa = m.id_conversa
        LEFT JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = cv.id_extracao
        LEFT JOIN investigacao.wpp_tb01_contatos ct ON ct.id_contato = m.id_contato_remetente
        WHERE rel.id_inquerito = %(inq)s
        {filtro}
        ORDER BY cv.id_conversa, m.id_mensagem
    """
    params = {"inq": id_inquerito, "expandir": bool(expandir_conversas)}
    filtro = ""
    if ids_marcacoes is not None:
        if not ids_marcacoes:
            return []
        filtro = "AND rel.id_marcacao = ANY(%(ids)s)"
        params["ids"] = list(ids_marcacoes)

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query.format(filtro=filtro), params)
            colunas = [d.name for d in cur.description]
            linhas = [dict(zip(colunas, linha)) for linha in cur.fetchall()]

    # a mesma mensagem marcada mais de uma vez sairia como duas linhas na tabela
    linhas = deduplicar_por_registro(linhas, lambda m: m["id_mensagem"])
    _completar_remetente_dono(
        linhas, lambda m: ("wpp", m.get("id_conversa"), m.get("id_extracao"), "WhatsApp"))
    _anexar_marcadores(linhas, id_usuario)

    # agrupa por conversa preservando a ordem de aparição
    conversas: dict[int, dict] = {}
    for m in linhas:
        conv = conversas.setdefault(
            m["id_conversa"],
            {"no_grupo": m["no_grupo"], "no_arquivo": m.get("no_arquivo"),
             "no_dispositivo": m.get("no_dispositivo"), "mensagens": []},
        )
        conv["mensagens"].append(m)

    resultado = []
    for conv in conversas.values():
        resultado.append({
            "titulo": _titulo_conversa(conv),
            "no_arquivo": conv.get("no_arquivo"),
            "no_dispositivo": conv.get("no_dispositivo"),
            "mensagens": conv["mensagens"],
            "trechos": dividir_em_trechos(conv["mensagens"]),
        })
    return resultado


def dividir_em_trechos(mensagens: list[dict]) -> list[dict]:
    """Quebra as mensagens marcadas de uma conversa nos pontos em que o fio original foi PULADO.

    Marcar "mensagem, resposta, mensagem" quase nunca é marcar três bolhas seguidas: entre uma e
    outra costuma haver dezenas de mensagens não marcadas. Emendadas na tela do relatório, elas
    passam a impressão de um diálogo contínuo que não existiu — leitura errada de prova. Então
    cada sequência realmente contígua vira um TRECHO à parte, e o renderizador começa um novo
    "chat" a cada quebra, dizendo quantas mensagens ficaram de fora.

    Contiguidade vem de nu_ordem, a posição da mensagem DENTRO da conversa inteira (calculada no
    SQL); a diferença entre duas posições consecutivas menos 1 é exatamente quantas mensagens do
    fio não foram marcadas. Conversa marcada por inteiro traz todas as mensagens e sai num
    trecho só. Devolve [{qt_omitidas_antes, mensagens:[...]}, ...]."""
    trechos: list[dict] = []
    ordem_anterior = None
    for msg in mensagens:
        ordem = msg.get("nu_ordem")
        omitidas = 0
        if ordem_anterior is not None and ordem is not None:
            omitidas = max(0, ordem - ordem_anterior - 1)
        if not trechos or omitidas:
            trechos.append({"qt_omitidas_antes": omitidas, "mensagens": []})
        trechos[-1]["mensagens"].append(msg)
        if ordem is not None:
            ordem_anterior = ordem
    return trechos


def _titulo_conversa(conv: dict) -> str:
    """Rótulo do chat: nome do grupo, ou o(s) interlocutor(es) que não é(são) o dono do aparelho.
    Prefere o nome do contato; só cai para o JID/telefone quando não há nome (ex.: contato não
    salvo na agenda) — nunca os dois juntos, senão o título sai com "5511... Fulano"."""
    if conv.get("no_grupo"):
        return conv["no_grupo"]
    for m in conv["mensagens"]:
        if not m.get("in_enviada"):
            nome = m.get("remetente") or m.get("remetente_jid")
            if nome:
                return nome
    # nenhuma mensagem recebida na seleção (só saíram mensagens do próprio dono): o título fica
    # genérico de propósito. O nome do DONO não serve de título — a conversa sairia chamada
    # "Eu" —, e é ele que nomeia toda bolha enviada (ver _completar_remetente_dono).
    return "Conversa"


def listar_chat_marcado(id_inquerito: int, ids_marcacoes: list[int] | None,
                        id_usuario: int | None = None,
                        expandir_conversas: bool = False) -> list[dict]:
    """Mensagens de WhatsApp, SMS e demais mensageiros (MSG_TB0x) marcadas para o relatório,
    agrupadas por conversa, para o bloco 'chat' (tela de celular, em vez da tabela do bloco
    'whatsapp'). Cada conversa vira {tp_app, titulo, mensagens:[...]}, com o comentário da
    marcação (tx_comentario) preservado em cada mensagem — é o que popula a subseção
    "Comentários do investigador" logo depois de cada trecho de conversa em
    relatorios_docx._bloco_chat.

    Mesma estrutura de tabelas para os três aplicativos (TB01 contatos / TB02 conversas / TB03
    participantes / TB04 mensagens / TB05 anexos — ver 10_whatsapp.sql, 08_investigacao_
    extras.sql e 12_mensageiros.sql), unidas aqui numa só consulta. SMS não guarda caminho no
    zip nem transcrição de anexo (SMS_TB05 não tem essas colunas), por isso saem NULL nesse
    ramo. id_conversa não é globalmente único entre as três famílias de tabela (cada uma tem
    sua própria sequência), daí a chave_conversa prefixada por aplicativo para agrupar certo.

    Marcar a CONVERSA na lista lateral é outra coisa, e NÃO entra aqui por padrão: é triagem do
    contato ("já li", "é relevante"), não uma forma de marcar as mensagens dele de uma vez. Um
    fio de 2.000 mensagens marcado como "lido" despejaria 2.000 bolhas no relatório sem ninguém
    ter pedido. As conversas marcadas saem como ITENS, por listar_conversas_marcadas().

    `expandir_conversas=True` liga a segunda porta do JOIN e traz o fio completo das conversas
    marcadas — usado só na exportação, quando o analista pede o conteúdo explicitamente. Nesse
    caso a cor/marcador da conversa vale para todas as bolhas (é classificação), mas o
    COMENTÁRIO não: ele é uma anotação só, da conversa, e sai em tx_comentario_conversa (uma
    linha antes da primeira tela, no .docx). Se fosse copiado para cada mensagem, os
    "Comentários do investigador" de cada trecho repetiriam o mesmo texto uma vez por bolha.

    Cada conversa traz 'mensagens' (todas as marcadas, em ordem) e 'trechos' — as mesmas
    mensagens quebradas onde o fio original foi pulado, que é o que o .docx desenha como telas
    de chat separadas (ver dividir_em_trechos)."""
    query = """
        WITH mensagens AS (
            SELECT 'wpp_mensagem'::text AS tp_origem, 'WhatsApp'::text AS tp_app,
                   m.id_mensagem, rel.id_marcacao, m.tx_mensagem, m.dt_mensagem, m.in_enviada, m.in_encaminhada,
                   m.in_temporaria, m.nu_ident_citado,
                   m.vl_latitude, m.vl_longitude,
                   ct.no_contato AS remetente, ct.nu_whatsapp_id AS remetente_jid,
                   ('wpp:' || m.id_conversa) AS chave_conversa,
                   m.id_conversa, cv.id_extracao, cv.no_grupo,
                   (SELECT count(*) FROM investigacao.wpp_tb04_mensagens mo
                     WHERE mo.id_conversa = m.id_conversa AND mo.id_mensagem <= m.id_mensagem) AS nu_ordem,
                   ex.no_arquivo, ex.no_dispositivo,
                   CASE WHEN rel.tp_origem = 'wpp_mensagem' THEN rel.tx_comentario END AS tx_comentario,
                   CASE WHEN rel.tp_origem = 'wpp_conversa' THEN rel.tx_comentario END AS tx_comentario_conversa,
                   rel.co_cor,
                   -- 'hash_md5' vem da linha do arquivo do aparelho, casada por CO_CAMINHO_ZIP:
                   -- o anexo só guarda o ponteiro para o binário (ver listar_whatsapp_marcado)
                   (SELECT json_agg(json_build_object('nome', a.no_arquivo, 'titulo', a.tx_titulo, 'transcricao', a.tx_transcricao,
                                                      'tp_conteudo', a.tp_conteudo,
                                                      'caminho_zip', a.co_caminho_zip,
                                                      'hash_md5', (SELECT ar.co_hash_md5
                                                                     FROM investigacao.arq_tb01_arquivos ar
                                                                    WHERE ar.id_extracao = cv.id_extracao
                                                                      AND ar.co_caminho_zip = a.co_caminho_zip
                                                                    LIMIT 1),
                                                      'ufdr', ex.no_arquivo)
                                    ORDER BY a.id_anexo)
                      FROM investigacao.wpp_tb05_anexos a WHERE a.id_mensagem = m.id_mensagem) AS anexos,
                   -- mensagem de referência quando esta responde a outra (mesma regra da tela de
                   -- análise: NU_IDENT_CITADO casa com NU_IDENTIFICADOR_ORIGINAL dentro da mesma
                   -- extração, preferindo a mesma conversa — ver _sql_mensagem_citada em
                   -- investigacao_utils). Sem ela a resposta saía no relatório como mensagem solta,
                   -- sem a tarja de citação que o WhatsApp e o IPED mostram.
                   (SELECT json_build_object('remetente', ctq.no_contato,
                                             'enviada', q.in_enviada,
                                             'texto', q.tx_mensagem,
                                             'dt_mensagem', q.dt_mensagem,
                                             'apagada', q.in_apagada_remetente,
                                             'tem_coordenada', (q.vl_latitude IS NOT NULL),
                                             'outra_conversa', (q.id_conversa <> m.id_conversa),
                                             'tp_conversa', CASE
                                                 WHEN cq.nu_whatsapp_chat_id LIKE 'Status:%%'
                                                   OR cq.nu_whatsapp_chat_id LIKE '%%@status'
                                                   OR cq.nu_whatsapp_chat_id LIKE '%%.status'
                                                   OR cq.nu_whatsapp_chat_id = 'status@broadcast' THEN 'status'
                                                 WHEN cq.nu_whatsapp_chat_id LIKE '%%@newsletter' THEN 'canal'
                                                 WHEN cq.nu_whatsapp_chat_id LIKE '%%@broadcast' THEN 'transmissao'
                                                 WHEN cq.nu_whatsapp_chat_id LIKE '%%@g.us' THEN 'grupo'
                                                 WHEN cq.tp_conversa = 'grupo' THEN 'grupo'
                                                 ELSE 'conversa'
                                               END,
                                             'conversa_nome', coalesce(cq.no_grupo, cq.nu_whatsapp_chat_id),
                                             'anexo', (SELECT json_build_object('titulo', aq.tx_titulo,
                                                                                'nome', aq.no_arquivo,
                                                                                'tipo', aq.tp_conteudo)
                                                         FROM investigacao.wpp_tb05_anexos aq
                                                        WHERE aq.id_mensagem = q.id_mensagem
                                                        ORDER BY aq.id_anexo LIMIT 1))
                      FROM investigacao.wpp_tb04_mensagens q
                      JOIN investigacao.wpp_tb02_conversas cq ON cq.id_conversa = q.id_conversa
                      LEFT JOIN investigacao.wpp_tb01_contatos ctq ON ctq.id_contato = q.id_contato_remetente
                     WHERE m.nu_ident_citado IS NOT NULL AND m.nu_ident_citado <> ''
                       AND cq.id_extracao = cv.id_extracao
                       AND q.nu_identificador_original = m.nu_ident_citado
                     ORDER BY (q.id_conversa = m.id_conversa) DESC, q.id_mensagem
                     LIMIT 1) AS citada
            FROM investigacao.rel_tb03_marcacoes rel
            JOIN investigacao.wpp_tb04_mensagens m
                 ON (rel.tp_origem = 'wpp_mensagem' AND m.id_mensagem = rel.id_origem)
                 OR (%(expandir)s::boolean AND rel.tp_origem = 'wpp_conversa' AND m.id_conversa = rel.id_origem)
            JOIN investigacao.wpp_tb02_conversas cv ON cv.id_conversa = m.id_conversa
            LEFT JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = cv.id_extracao
            LEFT JOIN investigacao.wpp_tb01_contatos ct ON ct.id_contato = m.id_contato_remetente
            WHERE rel.id_inquerito = %(inq)s {filtro}

            UNION ALL

            SELECT 'sms_mensagem'::text, 'SMS'::text,
                   m.id_mensagem, rel.id_marcacao, m.tx_mensagem, m.dt_mensagem, m.in_enviada, m.in_encaminhada,
                   m.in_temporaria, m.nu_ident_citado,
                   m.vl_latitude, m.vl_longitude,
                   ct.no_contato, ct.nu_telefone,
                   ('sms:' || m.id_conversa), m.id_conversa, cv.id_extracao, cv.no_grupo,
                   (SELECT count(*) FROM investigacao.sms_tb04_mensagens mo
                     WHERE mo.id_conversa = m.id_conversa AND mo.id_mensagem <= m.id_mensagem),
                   ex.no_arquivo, ex.no_dispositivo,
                   CASE WHEN rel.tp_origem = 'sms_mensagem' THEN rel.tx_comentario END,
                   CASE WHEN rel.tp_origem = 'sms_conversa' THEN rel.tx_comentario END,
                   rel.co_cor,
                   (SELECT json_agg(json_build_object('nome', a.no_arquivo, 'titulo', a.tx_titulo, 'transcricao', NULL,
                                                      'tp_conteudo', a.tp_conteudo,
                                                      'caminho_zip', NULL,
                                                      'ufdr', ex.no_arquivo)
                                    ORDER BY a.id_anexo)
                      FROM investigacao.sms_tb05_anexos a WHERE a.id_mensagem = m.id_mensagem) AS anexos,
                   (SELECT json_build_object('remetente', ctq.no_contato,
                                             'enviada', q.in_enviada,
                                             'texto', q.tx_mensagem,
                                             'dt_mensagem', q.dt_mensagem,
                                             'apagada', q.in_apagada_remetente,
                                             'tem_coordenada', (q.vl_latitude IS NOT NULL),
                                             'outra_conversa', (q.id_conversa <> m.id_conversa),
                                             'tp_conversa', 'conversa',
                                             'conversa_nome', cq.no_grupo,
                                             'anexo', (SELECT json_build_object('titulo', aq.tx_titulo,
                                                                                'nome', aq.no_arquivo,
                                                                                'tipo', aq.tp_conteudo)
                                                         FROM investigacao.sms_tb05_anexos aq
                                                        WHERE aq.id_mensagem = q.id_mensagem
                                                        ORDER BY aq.id_anexo LIMIT 1))
                      FROM investigacao.sms_tb04_mensagens q
                      JOIN investigacao.sms_tb02_conversas cq ON cq.id_conversa = q.id_conversa
                      LEFT JOIN investigacao.sms_tb01_contatos ctq ON ctq.id_contato = q.id_contato_remetente
                     WHERE m.nu_ident_citado IS NOT NULL AND m.nu_ident_citado <> ''
                       AND cq.id_extracao = cv.id_extracao
                       AND q.nu_identificador_original = m.nu_ident_citado
                     ORDER BY (q.id_conversa = m.id_conversa) DESC, q.id_mensagem
                     LIMIT 1) AS citada
            FROM investigacao.rel_tb03_marcacoes rel
            JOIN investigacao.sms_tb04_mensagens m
                 ON (rel.tp_origem = 'sms_mensagem' AND m.id_mensagem = rel.id_origem)
                 OR (%(expandir)s::boolean AND rel.tp_origem = 'sms_conversa' AND m.id_conversa = rel.id_origem)
            JOIN investigacao.sms_tb02_conversas cv ON cv.id_conversa = m.id_conversa
            LEFT JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = cv.id_extracao
            LEFT JOIN investigacao.sms_tb01_contatos ct ON ct.id_contato = m.id_contato_remetente
            WHERE rel.id_inquerito = %(inq)s {filtro}

            UNION ALL

            SELECT 'msg_mensagem'::text, COALESCE(cv.tp_app, 'Mensageiro'),
                   m.id_mensagem, rel.id_marcacao, m.tx_mensagem, m.dt_mensagem, m.in_enviada, m.in_encaminhada,
                   m.in_temporaria, m.nu_ident_citado,
                   m.vl_latitude, m.vl_longitude,
                   ct.no_contato, ct.nu_identificador,
                   ('msg:' || m.id_conversa), m.id_conversa, cv.id_extracao, cv.no_grupo,
                   (SELECT count(*) FROM investigacao.msg_tb04_mensagens mo
                     WHERE mo.id_conversa = m.id_conversa AND mo.id_mensagem <= m.id_mensagem),
                   ex.no_arquivo, ex.no_dispositivo,
                   CASE WHEN rel.tp_origem = 'msg_mensagem' THEN rel.tx_comentario END,
                   CASE WHEN rel.tp_origem = 'msg_conversa' THEN rel.tx_comentario END,
                   rel.co_cor,
                   (SELECT json_agg(json_build_object('nome', a.no_arquivo, 'titulo', a.tx_titulo, 'transcricao', a.tx_transcricao,
                                                      'tp_conteudo', a.tp_conteudo,
                                                      'caminho_zip', a.co_caminho_zip,
                                                      'hash_md5', (SELECT ar.co_hash_md5
                                                                     FROM investigacao.arq_tb01_arquivos ar
                                                                    WHERE ar.id_extracao = cv.id_extracao
                                                                      AND ar.co_caminho_zip = a.co_caminho_zip
                                                                    LIMIT 1),
                                                      'ufdr', ex.no_arquivo)
                                    ORDER BY a.id_anexo)
                      FROM investigacao.msg_tb05_anexos a WHERE a.id_mensagem = m.id_mensagem) AS anexos,
                   (SELECT json_build_object('remetente', ctq.no_contato,
                                             'enviada', q.in_enviada,
                                             'texto', q.tx_mensagem,
                                             'dt_mensagem', q.dt_mensagem,
                                             'apagada', q.in_apagada_remetente,
                                             'tem_coordenada', (q.vl_latitude IS NOT NULL),
                                             'outra_conversa', (q.id_conversa <> m.id_conversa),
                                             'tp_conversa', 'conversa',
                                             'conversa_nome', cq.no_grupo,
                                             'anexo', (SELECT json_build_object('titulo', aq.tx_titulo,
                                                                                'nome', aq.no_arquivo,
                                                                                'tipo', aq.tp_conteudo)
                                                         FROM investigacao.msg_tb05_anexos aq
                                                        WHERE aq.id_mensagem = q.id_mensagem
                                                        ORDER BY aq.id_anexo LIMIT 1))
                      FROM investigacao.msg_tb04_mensagens q
                      JOIN investigacao.msg_tb02_conversas cq ON cq.id_conversa = q.id_conversa
                      LEFT JOIN investigacao.msg_tb01_contatos ctq ON ctq.id_contato = q.id_contato_remetente
                     WHERE m.nu_ident_citado IS NOT NULL AND m.nu_ident_citado <> ''
                       AND cq.id_extracao = cv.id_extracao
                       AND q.nu_identificador_original = m.nu_ident_citado
                     ORDER BY (q.id_conversa = m.id_conversa) DESC, q.id_mensagem
                     LIMIT 1) AS citada
            FROM investigacao.rel_tb03_marcacoes rel
            JOIN investigacao.msg_tb04_mensagens m
                 ON (rel.tp_origem = 'msg_mensagem' AND m.id_mensagem = rel.id_origem)
                 OR (%(expandir)s::boolean AND rel.tp_origem = 'msg_conversa' AND m.id_conversa = rel.id_origem)
            JOIN investigacao.msg_tb02_conversas cv ON cv.id_conversa = m.id_conversa
            LEFT JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = cv.id_extracao
            LEFT JOIN investigacao.msg_tb01_contatos ct ON ct.id_contato = m.id_contato_remetente
            WHERE rel.id_inquerito = %(inq)s {filtro}
        )
        -- ordena por id_mensagem, não por dt_mensagem: dentro de uma conversa o id é a ordem de
        -- inserção do UFDR (cronológica e sem nulos — ver listar_mensagens_conversa, que pagina
        -- o visualizador por ele), enquanto dt_mensagem é anulável e jogaria a mensagem sem data
        -- para o fim do fio. É também a ordem que nu_ordem conta, e é dela que sai a quebra
        -- entre trechos em dividir_em_trechos.
        SELECT * FROM mensagens ORDER BY chave_conversa, id_mensagem
    """
    params = {"inq": id_inquerito, "expandir": bool(expandir_conversas)}
    filtro = ""
    if ids_marcacoes is not None:
        if not ids_marcacoes:
            return []
        filtro = "AND rel.id_marcacao = ANY(%(ids)s)"
        params["ids"] = list(ids_marcacoes)

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query.format(filtro=filtro), params)
            colunas = [d.name for d in cur.description]
            linhas = [dict(zip(colunas, linha)) for linha in cur.fetchall()]

    # comentário da CONVERSA marcada: colhido ANTES da deduplicação. Uma mensagem que está
    # marcada individualmente e também dentro de uma conversa marcada vem em duas linhas; a
    # dedup fica com uma delas, e se fosse a da marcação individual o comentário do fio sumiria.
    comentario_por_conversa: dict[str, str] = {}
    for linha in linhas:
        texto = (linha.get("tx_comentario_conversa") or "").strip()
        if texto:
            comentario_por_conversa.setdefault(linha["chave_conversa"], texto)

    # id_mensagem não é único entre as três famílias de tabela, daí a chave com o tp_origem
    linhas = deduplicar_por_registro(linhas, lambda m: (m["tp_origem"], m["id_mensagem"]))
    _completar_remetente_dono(
        linhas,
        lambda m: (m["tp_origem"].split("_")[0], m.get("id_conversa"),
                   m.get("id_extracao"), m.get("tp_app")),
    )
    _anexar_marcadores(linhas, id_usuario)

    conversas: dict[str, dict] = {}
    for m in linhas:
        conv = conversas.setdefault(
            m["chave_conversa"],
            {"tp_app": m["tp_app"], "no_grupo": m["no_grupo"], "no_arquivo": m.get("no_arquivo"),
             "no_dispositivo": m.get("no_dispositivo"), "chave_conversa": m["chave_conversa"],
             "mensagens": []},
        )
        conv["mensagens"].append(m)

    resultado = []
    for conv in conversas.values():
        resultado.append({
            "tp_app": conv["tp_app"],
            "titulo": _titulo_conversa(conv),
            "no_arquivo": conv.get("no_arquivo"),
            "no_dispositivo": conv.get("no_dispositivo"),
            "tx_comentario_conversa": comentario_por_conversa.get(conv["chave_conversa"]),
            "mensagens": conv["mensagens"],
            "trechos": dividir_em_trechos(conv["mensagens"]),
        })
    return resultado


_SQL_CONVERSA_MARCADA = """
    SELECT {app} AS tp_app, rel.id_marcacao, rel.tx_comentario, rel.co_cor,
           cv.id_conversa, cv.no_grupo, itl.no_contato AS interlocutor,
           ex.no_arquivo, ex.no_dispositivo,
           (SELECT count(*) FROM {mensagens} m WHERE m.id_conversa = cv.id_conversa) AS qt_mensagens,
           (SELECT min(m.dt_mensagem) FROM {mensagens} m WHERE m.id_conversa = cv.id_conversa) AS dt_inicio,
           (SELECT max(m.dt_mensagem) FROM {mensagens} m WHERE m.id_conversa = cv.id_conversa) AS dt_fim
      FROM investigacao.rel_tb03_marcacoes rel
      JOIN {conversas} cv ON rel.tp_origem = %(tp_conv)s AND cv.id_conversa = rel.id_origem
      LEFT JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = cv.id_extracao
      LEFT JOIN LATERAL (
            SELECT p.no_contato FROM {participantes} pp
              JOIN {contatos} p ON p.id_contato = pp.id_contato
                               AND NOT COALESCE(p.in_dono_dispositivo, false)
             WHERE pp.id_conversa = cv.id_conversa LIMIT 1
      ) itl ON true
     WHERE rel.id_inquerito = %(inq)s {filtro}
"""


def listar_conversas_marcadas(id_inquerito: int, ids_marcacoes: list[int] | None,
                              id_usuario: int | None = None) -> list[dict]:
    """As CONVERSAS marcadas, como itens — não as mensagens delas.

    Marcar a conversa é triagem do contato: "já li este fio", "este é relevante". O que importa
    aqui é a identificação (com quem, em qual aparelho), o tamanho e o período do fio, mais o
    comentário e os marcadores do analista. O conteúdo só entra na exportação quando ele pede
    (ver listar_chat_marcado(expandir_conversas=True))."""
    from funcoes.marcadores_utils import FONTES  # local: marcadores_utils importa este módulo

    linhas: list[dict] = []
    filtro = ""
    params: dict = {"inq": id_inquerito}
    if ids_marcacoes is not None:
        if not ids_marcacoes:
            return []
        filtro = "AND rel.id_marcacao = ANY(%(ids)s)"
        params["ids"] = list(ids_marcacoes)

    with get_connection() as conn:
        with conn.cursor() as cur:
            for cfg in FONTES.values():
                cur.execute(
                    _SQL_CONVERSA_MARCADA.format(filtro=filtro, **cfg),
                    dict(params, tp_conv=cfg["tp_conversa"]),
                )
                colunas = [d.name for d in cur.description]
                linhas.extend(dict(zip(colunas, linha)) for linha in cur.fetchall())

    _anexar_marcadores(linhas, id_usuario)
    for linha in linhas:
        linha["titulo"] = linha.get("no_grupo") or linha.get("interlocutor") or "Conversa"
    linhas.sort(key=lambda c: ((c.get("tp_app") or "").lower(), c["titulo"].lower()))
    return linhas


def _config_ia_relatorio() -> dict | None:
    """Configuração da IA do relatório (aba "Comentário IA no Relatório" em Configurações,
    REPORT_AI_*). None quando nada foi configurado ali — aí a chamada cai na IA padrão do prompt,
    que é o comportamento antigo."""
    from funcoes.env_utils import parse_env, read_env_text
    env = parse_env(read_env_text())
    if not (env.get("REPORT_AI_PROVIDER") or env.get("REPORT_AI_MODEL") or env.get("REPORT_AI_API_URL")):
        return None
    return {
        "provider": env.get("REPORT_AI_PROVIDER", ""),
        "model": env.get("REPORT_AI_MODEL", ""),
        "apiUrl": env.get("REPORT_AI_API_URL", ""),
        "apiKey": env.get("REPORT_AI_API_KEY", ""),
        # vazios caem no da aba Prompt IA, como os demais campos (ver enviar_mensagem)
        "caCert": env.get("REPORT_AI_CA_CERT", ""),
        "reasoningEffort": env.get("REPORT_AI_REASONING_EFFORT", ""),
    }


def reescrever_comentario_ia(comentario: str, anonimizador=None, id_usuario: int | None = None) -> str | None:
    """Reescreve UM comentário do analista (o texto que ele digitou na marcação) na mesma IA do
    resumo — usado quando a opção "Reescrever comentários do analista por IA" é marcada na tela de
    geração.

    O serviço é de redação, não de análise: corrigir o que foi escrito às pressas e fechar a frase,
    mantendo o que o analista quis dizer. Só o texto da anotação é enviado — nem a mensagem
    marcada, nem a conversa. Foi decisão de projeto: sem material novo à frente do modelo, ele não
    tem de onde trazer fato que o analista não escreveu, e a mesma anotação repetida em dezenas de
    marcações vira sempre a mesma frase reescrita (o que permite o cache de
    reescrever_comentarios_ia). O comentário continua sendo a afirmação do analista, que assina o
    relatório.

    None se não houver o que reescrever ou se a chamada falhar: quem chama devolve o texto original
    ao documento, então uma IA fora do ar não apaga a anotação de ninguém."""
    comentario = (comentario or "").strip()
    if not comentario:
        return None

    sistema = (
        "Você revisa anotações de um investigador de polícia para saírem num relatório oficial. "
        "Reescreva a anotação recebida em português formal, corrigindo erros de grafia e "
        "pontuação e completando a frase quando ela estiver truncada ou abreviada. "
        "PRESERVE integralmente o sentido e a afirmação do investigador: não acrescente fato, "
        "conclusão, nome ou circunstância que não esteja na anotação, e não tente adivinhar o que "
        "ela não diz. Não opine. "
        # o caixa-alta de nome/apelido é deliberado no relatório policial (é assim que o
        # investigado é destacado no texto), e o modelo normalizava "HÉRCULES" para "Hércules"
        "Copie nomes, apelidos, siglas e números EXATAMENTE como estão, inclusive as maiúsculas: "
        "quem aparece em CAIXA-ALTA continua em CAIXA-ALTA. "
        "Responda só com a anotação reescrita, em no máximo duas frases, sem título, sem aspas e "
        "sem introdução."
    )
    try:
        texto, _ = enviar_mensagem(
            [{"role": "user", "content": "Anotação do investigador: " + comentario}],
            sistema=sistema,
            config=_config_ia_relatorio(),
            anonimizador=anonimizador,
            id_usuario=id_usuario,
        )
    except ErroProvedorIA:
        return None
    return (texto or "").strip() or None


# quantas reescritas ao mesmo tempo. Em série, um relatório real (471 comentários marcados) fez
# 471 chamadas uma atrás da outra e passou de meia hora com a tela parada em "6. Conversas" — o
# usuário leu como travamento, e não estava errado.
_WORKERS_REESCRITA = 6


def reescrever_comentarios_ia(comentarios, progresso=None, anonimizador=None,
                              id_usuario: int | None = None) -> dict[str, str]:
    """Reescreve os comentários de um relatório inteiro de uma vez e devolve
    {texto original: texto reescrito}, só com os que a IA respondeu.

    Duas economias, e as duas apareceram no mesmo caso real (471 comentários marcados, apenas 37
    textos DISTINTOS — marcar um bloco de mensagens com a mesma anotação é o normal): o texto
    repetido é reescrito UMA vez e reaproveitado, e as reescritas saem em paralelo. 471 chamadas
    em série viraram 37 em seis frentes.

    progresso(feitos, total), se dado, é chamado a cada reescrita concluída — é o que mantém a tela
    de geração dizendo o que está acontecendo em vez de parecer travada."""
    textos = list(dict.fromkeys(t for t in ((c or "").strip() for c in comentarios) if t))
    if not textos:
        return {}
    resultado: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=min(_WORKERS_REESCRITA, len(textos))) as pool:
        futuros = {pool.submit(reescrever_comentario_ia, t, anonimizador, id_usuario): t for t in textos}
        for feitos, futuro in enumerate(as_completed(futuros), start=1):
            try:
                novo = futuro.result()
            except Exception:
                # a reescrita é enfeite: nada nela pode derrubar a geração do relatório
                novo = None
            if novo:
                resultado[futuros[futuro]] = novo
            if progresso is not None:
                progresso(feitos, len(textos))
    return resultado


def resumir_mensagens_ia(mensagens: list[dict], titulo_conversa: str, anonimizador=None,
                         id_usuario: int | None = None) -> str | None:
    """Resumo (1 parágrafo curto) de um conjunto de mensagens marcadas, pela IA configurada em
    Configurações (aba "Comentário IA no Relatório" — REPORT_AI_*; sem ela, cai na IA do prompt)
    — usado no bloco 'chat'/'whatsapp' quando a opção "Resumo das conversas por IA" é marcada
    na tela de geração. Cada cor de marcação vira um conjunto separado (ver relatorios_docx),
    então esta função resume só o que foi passado, sem tentar decidir agrupamento.

    None se não houver texto para resumir ou se a chamada falhar (provedor não configurado, rede
    fora etc.) — o relatório sai sem aquele resumo em vez de travar a geração inteira por causa
    de uma seção opcional."""
    linhas = []
    for m in mensagens:
        texto = (m.get("tx_mensagem") or "").strip()
        if not texto:
            continue
        quem = m.get("remetente") or m.get("remetente_jid") or ("Dono do aparelho" if m.get("in_enviada") else "Desconhecido")
        linhas.append(f"{quem}: {texto}")
    if not linhas:
        return None

    sistema = (
        "Você é um assistente de investigação policial. Resuma, em um parágrafo curto e "
        "objetivo (no máximo 4 frases), o teor das mensagens marcadas de uma conversa, "
        "destacando os fatos relevantes para a apuração. Não invente informação que não esteja "
        "no texto das mensagens. Responda só com o resumo corrido, sem título e sem introdução."
    )
    try:
        texto, _ = enviar_mensagem(
            [{"role": "user", "content": f"Conversa: {titulo_conversa}\n\n" + "\n".join(linhas)}],
            sistema=sistema,
            # IA isolada do comentário do relatório (aba própria em Configurações); sem ela
            # configurada, cai na IA padrão do prompt — comportamento antigo
            config=_config_ia_relatorio(),
            anonimizador=anonimizador,
            id_usuario=id_usuario,
        )
    except ErroProvedorIA:
        return None
    texto = (texto or "").strip()
    return texto or None


# tabelas (mensagens, conversas, anexos) de cada tipo de origem que pode ter anexo — mesma
# divisão por fonte de _TABELAS_CONVERSA em investigacao_utils
_TABELAS_ANEXO_ORIGEM = {
    "wpp_mensagem": ("wpp_tb04_mensagens", "wpp_tb02_conversas", "wpp_tb05_anexos"),
    "sms_mensagem": ("sms_tb04_mensagens", "sms_tb02_conversas", "sms_tb05_anexos"),
    "msg_mensagem": ("msg_tb04_mensagens", "msg_tb02_conversas", "msg_tb05_anexos"),
}


def anexos_de_marcacoes(marcacoes: list[dict], id_inquerito: int | None = None) -> dict[int, list[dict]]:
    """Anexos das marcações que são mensagens (WhatsApp/SMS/mensageiros) e o binário das que são
    arquivos do aparelho, indexados por id_marcacao — é o que permite a imagem de um registro
    marcado sair no .docx.

    Como em listar_whatsapp_marcado, devolve só os metadados (nome, mime, MD5 e onde o binário
    está dentro de qual .ufdr); o conteúdo é lido na hora de montar o documento.

    id_inquerito amarra a consulta ao caso (ver A5): sem ele, um id_marcacao estranho na lista
    já filtrada resolveria anexo de outro inquérito."""
    ids_por_origem: dict[str, list[int]] = {}
    for m in marcacoes:
        if m["tp_origem"] in _TABELAS_ANEXO_ORIGEM:
            ids_por_origem.setdefault(m["tp_origem"], []).append(m["id_marcacao"])
    ids_arquivo = [m["id_marcacao"] for m in marcacoes if m["tp_origem"] == "arquivo"]
    if not ids_por_origem and not ids_arquivo:
        return {}

    filtro_inq_arquivo = " AND rel.id_inquerito = %s" if id_inquerito is not None else ""
    params_inq_arquivo = (ids_arquivo, id_inquerito) if id_inquerito is not None else (ids_arquivo,)
    resultado: dict[int, list[dict]] = {}
    with get_connection() as conn:
        with conn.cursor() as cur:
            if ids_arquivo:
                # o próprio arquivo é o "anexo" da marcação. nome vai NULL de propósito: a coluna
                # "Conteúdo" da tabela já traz nome + caminho (ver _conteudo_arquivo), então aqui
                # só interessa a imagem embaixo dela.
                cur.execute(
                    """
                    SELECT rel.id_marcacao, NULL::text AS nome, NULL::text AS titulo,
                           ar.tp_mime AS tp_conteudo,
                           ar.co_caminho_zip AS caminho_zip, ex.no_arquivo AS ufdr,
                           ar.co_hash_md5 AS hash_md5
                    FROM investigacao.rel_tb03_marcacoes rel
                    JOIN investigacao.arq_tb01_arquivos ar ON ar.id_arquivo = rel.id_origem
                    LEFT JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = ar.id_extracao
                    WHERE rel.tp_origem = 'arquivo' AND rel.id_marcacao = ANY(%s)
                    """ + filtro_inq_arquivo + """
                    ORDER BY rel.id_marcacao
                    """,
                    params_inq_arquivo,
                )
                colunas = [d.name for d in cur.description]
                for linha in cur.fetchall():
                    anexo = dict(zip(colunas, linha))
                    resultado.setdefault(anexo.pop("id_marcacao"), []).append(anexo)

            for tp_origem, ids in ids_por_origem.items():
                mensagens, conversas, anexos = _TABELAS_ANEXO_ORIGEM[tp_origem]
                filtro_inq = " AND rel.id_inquerito = %s" if id_inquerito is not None else ""
                params = (tp_origem, ids, id_inquerito) if id_inquerito is not None else (tp_origem, ids)
                cur.execute(
                    f"""
                    SELECT rel.id_marcacao, a.no_arquivo AS nome, a.tx_titulo AS titulo,
                           a.tp_conteudo,
                           a.co_caminho_zip AS caminho_zip, ex.no_arquivo AS ufdr,
                           -- mesmo casamento de listar_whatsapp_marcado: o hash está na linha
                           -- do arquivo do aparelho, não no anexo
                           (SELECT ar.co_hash_md5
                              FROM investigacao.arq_tb01_arquivos ar
                             WHERE ar.id_extracao = c.id_extracao
                               AND ar.co_caminho_zip = a.co_caminho_zip
                             LIMIT 1) AS hash_md5
                    FROM investigacao.rel_tb03_marcacoes rel
                    JOIN investigacao.{mensagens} m ON m.id_mensagem = rel.id_origem
                    JOIN investigacao.{conversas} c ON c.id_conversa = m.id_conversa
                    JOIN investigacao.{anexos} a ON a.id_mensagem = m.id_mensagem
                    LEFT JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = c.id_extracao
                    WHERE rel.tp_origem = %s AND rel.id_marcacao = ANY(%s){filtro_inq}
                    ORDER BY rel.id_marcacao, a.id_anexo
                    """,
                    params,
                )
                colunas = [d.name for d in cur.description]
                for linha in cur.fetchall():
                    anexo = dict(zip(colunas, linha))
                    resultado.setdefault(anexo.pop("id_marcacao"), []).append(anexo)
    return resultado


def formatar_bytes(n) -> str:
    """Número de bytes com separador de milhar pt-BR (ex.: 59742531405 → '59.742.531.405')."""
    if n is None:
        return ""
    return f"{int(n):,}".replace(",", ".")


def _tamanho_em_disco(no_arquivo: str) -> int | None:
    """Tamanho em bytes do arquivo em data/; None se não estiver mais lá.

    O .ufdr e o CSV do SIMBA vivem em data/<delegacia>/<inquérito>/<categoria>/ depois da
    importação, e o que foi importado antes disso pode estar solto em data/ ou em
    data/<delegacia>/ — resolver_em_dados cobre os dois casos (funcoes/organizacao_arquivos.py)."""
    try:
        caminho = resolver_em_dados(no_arquivo)
        return caminho.stat().st_size if caminho else None
    except OSError:
        return None


def listar_extracoes_inquerito(id_inquerito: int) -> list[dict]:
    """Arquivos .ufdr das extrações do inquérito, com nome, aparelho, tamanho (lido do disco;
    None se o arquivo não estiver na pasta de dados) e o SHA-256 calculado na importação
    (`25_rif.sql`). O hash sai NULL só para extração importada antes daquela migração;
    o relatório declara isso na célula em vez de deixá-la vazia."""
    query = """
        SELECT no_arquivo, no_dispositivo, no_fabricante, co_hash_sha256
        FROM investigacao.inq_tb02_extracoes
        WHERE id_inquerito = %s
        ORDER BY id_extracao
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (id_inquerito,))
            colunas = [d.name for d in cur.description]
            linhas = [dict(zip(colunas, linha)) for linha in cur.fetchall()]
    for e in linhas:
        e["tamanho_bytes"] = _tamanho_em_disco(e.get("no_arquivo"))
    return linhas


def listar_contas_inquerito(id_inquerito: int) -> list[dict]:
    """Contas de usuário dos aparelhos extraídos (don_tb01_donos) — a seção 5 do modelo PF."""
    query = """
        SELECT d.tp_usuario, d.no_dono, d.nu_identificador, d.dt_ultimo_login,
               e.no_dispositivo
        FROM investigacao.don_tb01_donos d
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = d.id_extracao
        WHERE e.id_inquerito = %s
        ORDER BY e.id_extracao, d.id_dono
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (id_inquerito,))
            colunas = [d.name for d in cur.description]
            return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


# =====================================================================
# Extrato bancário (SIMBA R304) para o relatório
#
# Os três blocos abaixo espelham as abas da página simba.html — contas, contrapartes e
# alertas — porque é essa a estrutura em que a análise bancária é lida e cobrada.
#
# Todas as consultas filtram CD_STATUS = 'concluido': a importação roda em background, e um
# extrato em andamento tem os totais por conta gravados mas os lançamentos ainda entrando.
# Número parcial dentro de um relatório assinado é pior que a seção vir vazia.
# =====================================================================

def _extratos_concluidos(cur, id_inquerito: int) -> list[int]:
    cur.execute(
        "SELECT id_extrato FROM investigacao.smb_tb01_extratos "
        "WHERE id_inquerito = %s AND cd_status = 'concluido'",
        (id_inquerito,),
    )
    return [r[0] for r in cur.fetchall()]


def listar_bancario_contas(id_inquerito: int, ids_extratos: list[int] | None = None) -> list[dict]:
    """Contas afastadas do inquérito, da que mais movimentou para a que menos movimentou.

    ids_extratos restringe aos extratos escolhidos na tela de geração; None traz todos."""
    if ids_extratos is not None and not ids_extratos:
        return []
    query = """
        SELECT c.no_titular, c.nu_cpf_cnpj, c.tp_pessoa, c.no_banco, c.nu_agencia, c.nu_conta,
               c.tp_conta, c.dt_abertura, c.dt_encerramento, c.qt_lancamentos,
               c.vl_credito, c.vl_debito,
               c.dt_primeiro_lancamento, c.dt_ultimo_lancamento
        FROM investigacao.smb_tb02_contas c
        JOIN investigacao.smb_tb01_extratos s ON s.id_extrato = c.id_extrato
        WHERE s.id_inquerito = %(inq)s AND s.cd_status = 'concluido'
        {filtro}
        ORDER BY c.vl_credito + c.vl_debito DESC
    """
    params = {"inq": id_inquerito}
    filtro = ""
    if ids_extratos is not None:
        filtro = "AND s.id_extrato = ANY(%(ids)s)"
        params["ids"] = list(ids_extratos)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query.format(filtro=filtro), params)
            colunas = [d.name for d in cur.description]
            return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


def listar_bancario_contrapartes(id_inquerito: int, ids_extratos: list[int] | None = None, limite: int = 40) -> dict:
    """Contrapartes consolidadas por pessoa (somando todas as contas), com o que ficou de fora.

    Devolve {"linhas": [...], "qt_total": n, "vl_agrupado": x}: o relatório precisa dizer quantas
    contrapartes existem além das listadas e quanto elas somam, senão a tabela passa a impressão
    de ser a lista completa."""
    if ids_extratos is not None and not ids_extratos:
        return {"linhas": [], "qt_total": 0, "qt_agrupadas": 0, "vl_agrupado": 0.0}
    limite = max(1, min(int(limite or 40), 500))
    params = {"inq": id_inquerito}
    filtro = ""
    if ids_extratos is not None:
        filtro = "AND s.id_extrato = ANY(%(ids)s)"
        params["ids"] = list(ids_extratos)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT (array_agg(cp.no_contraparte ORDER BY cp.vl_total DESC))[1] AS no_contraparte,
                       cp.nu_cpf_cnpj,
                       sum(cp.qt_lancamentos) AS qt_lancamentos,
                       coalesce(sum(cp.vl_total) FILTER (WHERE cp.cd_direcao = 'credito'), 0) AS vl_creditou,
                       coalesce(sum(cp.vl_total) FILTER (WHERE cp.cd_direcao = 'debito'), 0) AS vl_recebeu,
                       min(cp.dt_primeiro) AS dt_primeiro, max(cp.dt_ultimo) AS dt_ultimo,
                       bool_or(cp.in_titular_no_extrato) AS in_titular_no_extrato,
                       sum(cp.vl_total) AS vl_total
                FROM investigacao.smb_tb04_contrapartes cp
                JOIN investigacao.smb_tb01_extratos s ON s.id_extrato = cp.id_extrato
                WHERE s.id_inquerito = %(inq)s AND s.cd_status = 'concluido'
                {filtro}
                GROUP BY coalesce(cp.nu_cpf_cnpj, 'NOME:' || upper(cp.no_contraparte)), cp.nu_cpf_cnpj
                ORDER BY sum(cp.vl_total) DESC
                """,
                params,
            )
            colunas = [d.name for d in cur.description]
            todas = [dict(zip(colunas, linha)) for linha in cur.fetchall()]

    exibidas = todas[:limite]
    resto = todas[limite:]
    return {
        "linhas": exibidas,
        "qt_total": len(todas),
        "qt_agrupadas": len(resto),
        "vl_agrupado": sum(float(c["vl_total"] or 0) for c in resto),
    }


def resumo_bancario(id_inquerito: int, ids_extratos: list[int] | None = None) -> dict | None:
    """Cabeçalho da seção bancária: arquivos, contas, período e totais. None sem extrato."""
    if ids_extratos is not None and not ids_extratos:
        return None
    params = {"inq": id_inquerito}
    filtro = ""
    if ids_extratos is not None:
        filtro = "AND id_extrato = ANY(%(ids)s)"
        params["ids"] = list(ids_extratos)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT count(*) AS qt_extratos,
                       coalesce(sum(qt_contas), 0) AS qt_contas,
                       coalesce(sum(qt_titulares), 0) AS qt_titulares,
                       coalesce(sum(qt_lancamentos), 0) AS qt_lancamentos,
                       coalesce(sum(vl_credito), 0) AS vl_credito,
                       coalesce(sum(vl_debito), 0) AS vl_debito,
                       min(dt_primeiro_lancamento) AS dt_inicio,
                       max(dt_ultimo_lancamento) AS dt_fim,
                       string_agg(no_arquivo, '; ' ORDER BY no_arquivo) AS arquivos
                FROM investigacao.smb_tb01_extratos
                WHERE id_inquerito = %(inq)s AND cd_status = 'concluido'
                {filtro}
                """,
                params,
            )
            colunas = [d.name for d in cur.description]
            linha = dict(zip(colunas, cur.fetchone()))
    return linha if linha["qt_extratos"] else None


# =====================================================================
# RIF (SISCOAF) para o relatório
#
# As funções abaixo consultam os RIFs importados num inquérito e
# espelham as abas da página rif.html — comunicações, contrapartes,
# envolvidos e ocorrências.
# =====================================================================


def resumo_rif(id_inquerito: int, ids_rif: list[int] | None = None) -> dict | None:
    """Síntese dos RIFs do inquérito (total de RIFs, comunicações, envolvidos,
    ocorrências e números). None se não houver RIF importado.

    ids_rif restringe aos RIFs escolhidos na tela de geração; None traz todos."""
    if ids_rif is not None and not ids_rif:
        return None
    params = {"inq": id_inquerito}
    filtro = ""
    if ids_rif is not None:
        filtro = "AND id_rif = ANY(%(ids)s)"
        params["ids"] = list(ids_rif)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT count(*) AS qt_rifs,
                       coalesce(sum(qt_comunicacoes), 0) AS qt_comunicacoes,
                       coalesce(sum(qt_envolvidos), 0) AS qt_envolvidos,
                       coalesce(sum(qt_ocorrencias), 0) AS qt_ocorrencias,
                       string_agg(nu_rif, '; ' ORDER BY nu_rif) AS nu_rifs
                FROM investigacao.rif_tb01_rifs
                WHERE id_inquerito = %(inq)s
                {filtro}
                """,
                params,
            )
            colunas = [d.name for d in cur.description]
            linha = dict(zip(colunas, cur.fetchone()))
    return linha if linha and linha["qt_rifs"] else None


def listar_arquivos_rif(id_inquerito: int, ids_rif: list[int] | None = None) -> list[dict]:
    """Arquivos que compõem os RIFs do inquérito (3 CSVs + PDF), com nome, tipo, tamanho e os
    hashes MD5/SHA-1/SHA-256 gravados na importação (`25_rif.sql`) — a tabela de
    metadados dos arquivos do relatório de RIF (bloco 'rif_arquivos'), para conferência de
    integridade. ids_rif restringe aos RIFs escolhidos na tela de geração; None traz todos."""
    if ids_rif is not None and not ids_rif:
        return []
    params = {"inq": id_inquerito}
    filtro = ""
    if ids_rif is not None:
        filtro = "AND r.id_rif = ANY(%(ids)s)"
        params["ids"] = list(ids_rif)
    query = """
        SELECT r.nu_rif, r.no_pasta, a.no_arquivo, a.tp_arquivo, a.qt_bytes,
               a.co_hash_md5, a.co_hash_sha1, a.co_hash_sha256
        FROM investigacao.rif_tb06_arquivos a
        JOIN investigacao.rif_tb01_rifs r ON r.id_rif = a.id_rif
        WHERE r.id_inquerito = %(inq)s
        {filtro}
        ORDER BY r.id_rif, a.tp_arquivo, a.no_arquivo
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query.format(filtro=filtro), params)
            colunas = [d.name for d in cur.description]
            return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


def listar_rif_comunicacoes(id_inquerito: int, ids_rif: list[int] | None = None) -> list[dict]:
    """Comunicações (movimentações) dos RIFs do inquérito."""
    if ids_rif is not None and not ids_rif:
        return []
    query = """
        SELECT c.id_comunicacao_rif, c.nu_indexador, c.id_comunicacao,
               c.dt_recebimento, c.dt_operacao, c.no_comunicante,
               c.no_cidade_agencia, c.sg_uf_agencia, c.no_agencia, c.nu_agencia,
               c.vl_campo_b AS vl_credito, c.vl_campo_c AS vl_debito,
               c.tx_informacoes_adicionais, c.cd_segmento,
               r.nu_rif
        FROM investigacao.rif_tb02_comunicacoes c
        JOIN investigacao.rif_tb01_rifs r ON r.id_rif = c.id_rif
        WHERE r.id_inquerito = %(inq)s
        {filtro}
        ORDER BY c.dt_recebimento DESC NULLS LAST, c.nu_indexador
    """
    params = {"inq": id_inquerito}
    filtro = ""
    if ids_rif is not None:
        filtro = "AND r.id_rif = ANY(%(ids)s)"
        params["ids"] = list(ids_rif)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query.format(filtro=filtro), params)
            colunas = [d.name for d in cur.description]
            return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


def listar_rif_contrapartes(id_inquerito: int, ids_rif: list[int] | None = None, limite: int = 40) -> dict:
    """Contrapartes consolidadas por pessoa (somando os RIFs selecionados),
    com o que ficou de fora."""
    if ids_rif is not None and not ids_rif:
        return {"linhas": [], "qt_total": 0, "qt_agrupadas": 0, "vl_agrupado": 0.0}
    limite = max(1, min(int(limite or 40), 500))
    params = {"inq": id_inquerito}
    filtro = ""
    if ids_rif is not None:
        filtro = "AND r.id_rif = ANY(%(ids)s)"
        params["ids"] = list(ids_rif)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT (array_agg(cp.no_contraparte ORDER BY cp.vl_total DESC))[1] AS no_contraparte,
                       cp.nu_cpf_cnpj,
                       sum(cp.qt_lancamentos) AS qt_lancamentos,
                       coalesce(sum(cp.vl_total) FILTER (WHERE cp.cd_direcao = 'credito'), 0) AS vl_creditou,
                       coalesce(sum(cp.vl_total) FILTER (WHERE cp.cd_direcao = 'debito'), 0) AS vl_recebeu,
                       count(DISTINCT cp.id_comunicacao_rif) AS qt_comunicacoes,
                       sum(cp.vl_total) AS vl_total
                FROM investigacao.rif_tb05_contrapartes cp
                JOIN investigacao.rif_tb01_rifs r ON r.id_rif = cp.id_rif
                WHERE r.id_inquerito = %(inq)s
                {filtro}
                GROUP BY coalesce(cp.nu_cpf_cnpj, 'NOME:' || upper(cp.no_contraparte)), cp.nu_cpf_cnpj
                ORDER BY sum(cp.vl_total) DESC
                """,
                params,
            )
            colunas = [d.name for d in cur.description]
            todas = [dict(zip(colunas, linha)) for linha in cur.fetchall()]

    exibidas = todas[:limite]
    resto = todas[limite:]
    return {
        "linhas": exibidas,
        "qt_total": len(todas),
        "qt_agrupadas": len(resto),
        "vl_agrupado": sum(float(c["vl_total"] or 0) for c in resto),
    }


def listar_rif_envolvidos(id_inquerito: int, ids_rif: list[int] | None = None) -> list[dict]:
    """Pessoas/empresas citadas nos RIFs do inquérito."""
    if ids_rif is not None and not ids_rif:
        return []
    query = """
        SELECT e.id_envolvido_rif, e.nu_indexador, e.nu_cpf_cnpj, e.no_envolvido,
               e.tp_envolvido, e.nu_agencia, e.nu_conta,
               e.dt_abertura_conta, e.tx_pep, e.tx_pessoa_obrigada, e.tx_servidor,
               r.nu_rif
        FROM investigacao.rif_tb03_envolvidos e
        JOIN investigacao.rif_tb01_rifs r ON r.id_rif = e.id_rif
        WHERE r.id_inquerito = %(inq)s
        {filtro}
        ORDER BY e.no_envolvido
    """
    params = {"inq": id_inquerito}
    filtro = ""
    if ids_rif is not None:
        filtro = "AND r.id_rif = ANY(%(ids)s)"
        params["ids"] = list(ids_rif)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query.format(filtro=filtro), params)
            colunas = [d.name for d in cur.description]
            return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


def listar_contrapartes_por_comunicacao_rif(id_inquerito: int, ids_rif: list[int] | None = None) -> dict[int, dict]:
    """Contrapartes de cada comunicação, separadas por direção (credito/debito),
    indexadas por id_comunicacao_rif. Cada entrada tem {credito: [...], debito: [...]}."""
    if ids_rif is not None and not ids_rif:
        return {}
    query = """
        SELECT cp.id_comunicacao_rif, cp.cd_direcao,
               cp.id_contraparte_rif, cp.no_contraparte, cp.nu_cpf_cnpj,
               cp.qt_lancamentos, cp.vl_total, cp.tx_obs
        FROM investigacao.rif_tb05_contrapartes cp
        JOIN investigacao.rif_tb01_rifs r ON r.id_rif = cp.id_rif
        WHERE r.id_inquerito = %(inq)s
        {filtro}
        ORDER BY cp.id_comunicacao_rif, cp.cd_direcao, cp.nu_ordem
    """
    params = {"inq": id_inquerito}
    filtro = ""
    if ids_rif is not None:
        filtro = "AND r.id_rif = ANY(%(ids)s)"
        params["ids"] = list(ids_rif)
    resultado: dict[int, dict] = {}
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query.format(filtro=filtro), params)
            colunas = [d.name for d in cur.description]
            for linha in cur.fetchall():
                row = dict(zip(colunas, linha))
                id_com = row.pop("id_comunicacao_rif")
                direcao = row.pop("cd_direcao")
                comunicacao = resultado.setdefault(id_com, {"credito": [], "debito": []})
                comunicacao[direcao].append(row)
    return resultado


def listar_rif_ocorrencias(id_inquerito: int, ids_rif: list[int] | None = None) -> list[dict]:
    """Enquadramentos/alertas dos RIFs do inquérito."""
    if ids_rif is not None and not ids_rif:
        return []
    query = """
        SELECT o.id_ocorrencia_rif, o.nu_indexador, o.id_ocorrencia, o.tx_ocorrencia,
               r.nu_rif
        FROM investigacao.rif_tb04_ocorrencias o
        JOIN investigacao.rif_tb01_rifs r ON r.id_rif = o.id_rif
        WHERE r.id_inquerito = %(inq)s
        {filtro}
        ORDER BY o.nu_indexador, o.id_ocorrencia
    """
    params = {"inq": id_inquerito}
    filtro = ""
    if ids_rif is not None:
        filtro = "AND r.id_rif = ANY(%(ids)s)"
        params["ids"] = list(ids_rif)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query.format(filtro=filtro), params)
            colunas = [d.name for d in cur.description]
            return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


def listar_bancario_alertas(id_inquerito: int, ids_extratos: list[int] | None = None, limite: int = 30) -> dict:
    """Alertas do extrato para o relatório, reaproveitando o cálculo da tela (simba_utils).

    Os alertas são apurados por extrato; aqui eles são concatenados e reordenados por valor,
    porque no relatório o que importa é o caso (ou os extratos escolhidos), não cada arquivo
    importado isoladamente."""
    from funcoes.simba_utils import calcular_alertas

    vazio = {"especie": [], "fracionamento": [], "internas": [], "triangulacoes": [],
             "passagens": [], "resumo": None}
    if ids_extratos is not None and not ids_extratos:
        return dict(vazio)

    with get_connection() as conn:
        with conn.cursor() as cur:
            ids = _extratos_concluidos(cur, id_inquerito)
    if ids_extratos is not None:
        selecionados = set(ids_extratos)
        ids = [i for i in ids if i in selecionados]
    if not ids:
        return dict(vazio)

    especie, fracionamento, internas, triangulacoes, passagens = [], [], [], [], []
    resumo = {"qt_especie": 0, "vl_especie": 0.0, "qt_redondos": 0, "vl_redondos": 0.0,
              "qt_redondos_internos": 0, "vl_redondos_internos": 0.0, "vl_internas": 0.0,
              "qt_passagem": 0, "vl_passagem": 0.0}
    parametros = None
    for id_extrato in ids:
        a = calcular_alertas(id_extrato)
        especie += a["especie"]
        fracionamento += a["fracionamento"]
        internas += a["internas"]
        # a triangulação é apurada dentro de cada extrato (é onde se tem os dois lados da conta);
        # entre extratos diferentes os ciclos não se somam, então aqui é só concatenação
        triangulacoes += a["triangulacoes"]
        passagens += a.get("passagens", [])
        parametros = a["parametros"]
        for chave in resumo:
            resumo[chave] += float(a["resumo"].get(chave) or 0)

    especie.sort(key=lambda x: -(float(x["vl_depositos"] or 0) + float(x["vl_saques"] or 0)))
    fracionamento.sort(key=lambda x: -float(x["vl_total"] or 0))
    internas.sort(key=lambda x: -float(x["vl_total"] or 0))
    triangulacoes.sort(key=lambda x: -float(x["vl_menor"] or 0))
    passagens.sort(key=lambda x: -float(x.get("vl_giro") or 0))
    return {
        "especie": especie[:limite],
        "fracionamento": fracionamento[:limite],
        "internas": internas[:limite],
        "triangulacoes": triangulacoes[:limite],
        "passagens": passagens[:limite],
        "resumo": resumo,
        "parametros": parametros,
        "qt_fracionamento": len(fracionamento),
        "qt_internas": len(internas),
        "qt_triangulacoes": len(triangulacoes),
        "qt_passagens": len(passagens),
    }


def listar_arquivos_bancario(id_inquerito: int, ids_extratos: list[int] | None = None) -> list[dict]:
    """Arquivos de extrato bancário (R304 do SIMBA) do inquérito, com nome, tamanho (lido do
    disco; None se o arquivo não estiver na pasta de dados) e os hashes MD5/SHA-1/SHA-256
    gravados na importação (`25_rif.sql`) — a tabela de metadados dos arquivos do
    relatório de dados bancários (bloco 'bancario_arquivos').

    ids_extratos restringe aos extratos escolhidos na tela de geração; None traz todos. Só os
    extratos concluídos entram — mesmo critério das demais seções bancárias (arquivo ainda
    importando pode ter os totais parciais, e número parcial não vai para relatório assinado)."""
    if ids_extratos is not None and not ids_extratos:
        return []
    params = {"inq": id_inquerito}
    filtro = ""
    if ids_extratos is not None:
        filtro = "AND s.id_extrato = ANY(%(ids)s)"
        params["ids"] = list(ids_extratos)
    query = """
        SELECT s.no_arquivo, s.co_hash_md5, s.co_hash_sha1, s.co_hash_sha256
        FROM investigacao.smb_tb01_extratos s
        WHERE s.id_inquerito = %(inq)s AND s.cd_status = 'concluido'
        {filtro}
        ORDER BY s.id_extrato
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query.format(filtro=filtro), params)
            colunas = [d.name for d in cur.description]
            linhas = [dict(zip(colunas, linha)) for linha in cur.fetchall()]
    for s in linhas:
        s["tamanho_bytes"] = _tamanho_em_disco(s.get("no_arquivo"))
    return linhas


# =====================================================================
# Inquérito em PDF (ePol/IPL) para o relatório
#
# Espelham as abas de inquerito_pdf.html. As funções de ipl_utils.py
# filtram por ID_IPL (é o que a página precisa); aqui o recorte é o
# inquérito inteiro, com subconjunto opcional dos arquivos escolhidos
# na tela de geração — mesma forma das listar_rif_* acima.
# =====================================================================


def _filtro_ipl(ids_ipl: list[int] | None, params: dict) -> str:
    """Trecho AND ... do WHERE para restringir aos IPLs escolhidos (e alimenta params)."""
    if ids_ipl is None:
        return ""
    params["ids"] = list(ids_ipl)
    return "AND p.id_ipl = ANY(%(ids)s)"


def listar_ipl_resumo(id_inquerito: int, ids_ipl: list[int] | None = None) -> list[dict]:
    """Cabeçalho de cada inquérito em PDF importado (unidade, autoridade, instauração)."""
    if ids_ipl is not None and not ids_ipl:
        return []
    params = {"inq": id_inquerito}
    filtro = _filtro_ipl(ids_ipl, params)
    query = f"""
        SELECT p.id_ipl, p.no_arquivo, p.nu_ipl, p.no_unidade, p.no_autoridade,
               p.dt_instauracao, p.tx_tipificacao, p.tx_resumo_fatos, p.nu_processo_judicial,
               p.qt_folhas, p.qt_paginas, p.qt_pecas, p.qt_pessoas, p.qt_achados
        FROM investigacao.ipl_tb01_inqueritos_pdf p
        WHERE p.id_inquerito = %(inq)s
        {filtro}
        ORDER BY p.dt_instauracao NULLS LAST, p.nu_ipl
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            colunas = [d.name for d in cur.description]
            return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


def listar_ipl_pecas(id_inquerito: int, ids_ipl: list[int] | None = None) -> list[dict]:
    """Peças que compõem os autos, na ordem das folhas."""
    if ids_ipl is not None and not ids_ipl:
        return []
    params = {"inq": id_inquerito}
    filtro = _filtro_ipl(ids_ipl, params)
    query = f"""
        SELECT c.id_peca, c.tp_peca, c.nu_peca, c.dt_peca, c.nu_folha_inicial, c.nu_folha_final,
               c.qt_folhas, c.no_orgao, c.no_assinante, c.tx_resumo, p.nu_ipl
        FROM investigacao.ipl_tb02_pecas c
        JOIN investigacao.ipl_tb01_inqueritos_pdf p ON p.id_ipl = c.id_ipl
        WHERE p.id_inquerito = %(inq)s
        {filtro}
        ORDER BY p.nu_ipl, c.nu_folha_inicial NULLS LAST, c.id_peca
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            colunas = [d.name for d in cur.description]
            return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


def listar_ipl_pessoas(id_inquerito: int, ids_ipl: list[int] | None = None) -> list[dict]:
    """Pessoas citadas nos autos, da mais mencionada para a menos mencionada."""
    if ids_ipl is not None and not ids_ipl:
        return []
    params = {"inq": id_inquerito}
    filtro = _filtro_ipl(ids_ipl, params)
    query = f"""
        SELECT s.id_pessoa_ipl, s.no_pessoa, s.no_variantes, s.nu_cpf_cnpj, s.no_vulgo,
               s.tp_papel, s.nu_folha, s.qt_mencoes, s.tx_contexto, p.nu_ipl
        FROM investigacao.ipl_tb03_pessoas s
        JOIN investigacao.ipl_tb01_inqueritos_pdf p ON p.id_ipl = s.id_ipl
        WHERE p.id_inquerito = %(inq)s
        {filtro}
        ORDER BY s.qt_mencoes DESC NULLS LAST, s.no_pessoa
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            colunas = [d.name for d in cur.description]
            return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


def listar_ipl_achados(id_inquerito: int, ids_ipl: list[int] | None = None) -> dict:
    """Achados dos autos agrupados por tipo: {tp_achado: [linhas]}, na ordem do tipo.

    O bloco monta uma tabela por tipo (telefone, conta, valor...) — juntar tudo numa tabela só
    obrigaria a repetir o tipo em cada linha e misturaria coisas que não se comparam."""
    if ids_ipl is not None and not ids_ipl:
        return {}
    params = {"inq": id_inquerito}
    filtro = _filtro_ipl(ids_ipl, params)
    query = f"""
        SELECT a.id_achado_ipl, a.tp_achado, a.tx_valor, a.vl_numerico, a.nu_folha,
               a.qt_ocorrencias, a.tx_contexto, p.nu_ipl
        FROM investigacao.ipl_tb04_achados a
        JOIN investigacao.ipl_tb01_inqueritos_pdf p ON p.id_ipl = a.id_ipl
        WHERE p.id_inquerito = %(inq)s
        {filtro}
        ORDER BY a.tp_achado, a.qt_ocorrencias DESC NULLS LAST, a.tx_valor
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            colunas = [d.name for d in cur.description]
            linhas = [dict(zip(colunas, linha)) for linha in cur.fetchall()]

    grupos: dict[str, list[dict]] = {}
    for linha in linhas:
        grupos.setdefault(linha.get("tp_achado") or "outro", []).append(linha)
    return grupos


# =====================================================================
# Chaves PIX, contas de nuvem e IPED para o relatório
#
# SQL próprio, como nos blocos bancários: as funções das páginas
# (contas_nuvem_utils) são paginadas e devolvem {"total", "itens"}, e a
# de PIX (pix_utils.listar_chaves_pix) devolve uma linha por MENÇÃO —
# o relatório quer uma linha por chave.
# =====================================================================


def _cortar(todas: list[dict], limite: int) -> dict:
    """{"linhas": até `limite`, "qt_total": n, "qt_agrupadas": resto} — o bloco declara quantas
    ficaram de fora, para a tabela nunca passar a impressão de ser a lista completa."""
    exibidas = todas[:limite]
    return {"linhas": exibidas, "qt_total": len(todas), "qt_agrupadas": len(todas) - len(exibidas)}


def listar_pix_chaves(id_inquerito: int, limite: int = 40) -> dict:
    """Chaves PIX detectadas nas conversas, consolidadas por chave (não por menção).

    Uma chave costuma aparecer dezenas de vezes na mesma conversa; o relatório precisa da chave,
    de onde ela apareceu e de quando — não da repetição."""
    limite = max(1, min(int(limite or 40), 500))
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT k.co_chave, k.tp_chave,
                       count(*) AS qt_mencoes,
                       count(DISTINCT k.id_conversa) AS qt_conversas,
                       string_agg(DISTINCT k.no_app, ', ' ORDER BY k.no_app) AS no_apps,
                       string_agg(DISTINCT k.no_conversa, '; ' ORDER BY k.no_conversa) AS no_conversas,
                       bool_or(k.in_remetente_dono) AS in_dono_citou,
                       min(k.dt_mensagem) AS dt_primeira, max(k.dt_mensagem) AS dt_ultima
                FROM investigacao.pix_tb01_chaves k
                JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = k.id_extracao
                WHERE ex.id_inquerito = %s
                GROUP BY k.co_chave, k.tp_chave
                ORDER BY count(*) DESC, k.tp_chave, k.co_chave
                """,
                (id_inquerito,),
            )
            colunas = [d.name for d in cur.description]
            todas = [dict(zip(colunas, linha)) for linha in cur.fetchall()]
    return _cortar(todas, limite)


def listar_nuvem_perfil(id_inquerito: int) -> list[dict]:
    """Perfil da(s) conta(s) de nuvem importada(s) (normalmente uma por extração)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.no_nome, p.no_nome_dado, p.no_nome_familia, p.tx_email,
                       p.tx_email_alternativos, p.dt_criacao, p.tx_idioma, p.tx_pais,
                       p.tx_nascimento, p.tx_servicos, p.tx_status, p.no_fonte,
                       ex.no_arquivo
                FROM investigacao.prf_tb01_perfil p
                JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = p.id_extracao
                WHERE ex.id_inquerito = %s
                ORDER BY p.id_perfil
                """,
                (id_inquerito,),
            )
            colunas = [d.name for d in cur.description]
            return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


def listar_nuvem_dispositivos(id_inquerito: int, limite: int = 40) -> dict:
    """Dispositivos conhecidos pela conta de nuvem — é onde aparece o IMEI, que costuma ser o
    elo entre a conta e um aparelho apreendido."""
    limite = max(1, min(int(limite or 40), 500))
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT d.no_dispositivo, d.tx_fabricante, d.tx_imei, d.tx_android_id,
                       d.tx_serial, d.tx_versao, d.no_fonte
                FROM investigacao.dvc_tb01_dispositivos d
                JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = d.id_extracao
                WHERE ex.id_inquerito = %s
                ORDER BY d.no_dispositivo NULLS LAST, d.id_dispositivo
                """,
                (id_inquerito,),
            )
            colunas = [d.name for d in cur.description]
            todas = [dict(zip(colunas, linha)) for linha in cur.fetchall()]
    return _cortar(todas, limite)


def listar_nuvem_acessos(id_inquerito: int, limite: int = 40) -> dict:
    """Acessos à conta já CONSOLIDADOS por identificador (ACS_TB02_AGREGADOS).

    A tabela de acessos crus (ACS_TB01) tem dezenas de milhares de linhas e não cabe num
    relatório; o agregado é a mesma informação na granularidade que se lê."""
    limite = max(1, min(int(limite or 40), 500))
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT a.tp_identificador, a.tx_identificador, a.dt_primeira, a.dt_ultima,
                       a.qt_dias_ativos, a.in_multilogin, a.tp_canal_autenticacao, a.no_fonte
                FROM investigacao.acs_tb02_agregados a
                JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = a.id_extracao
                WHERE ex.id_inquerito = %s
                ORDER BY a.qt_dias_ativos DESC NULLS LAST, a.dt_ultima DESC NULLS LAST
                """,
                (id_inquerito,),
            )
            colunas = [d.name for d in cur.description]
            todas = [dict(zip(colunas, linha)) for linha in cur.fetchall()]
    return _cortar(todas, limite)


def listar_iped_extracoes(id_inquerito: int) -> list[dict]:
    """Casos do IPED importados no inquérito — cadeia de custódia do material de nuvem, no mesmo
    papel que o bloco 'extracoes' cumpre para os .ufdr. Só os concluídos: uma importação em
    andamento tem contadores parciais, e número parcial em relatório assinado é pior que seção
    vazia (mesmo critério dos extratos do SIMBA)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT i.no_pasta, i.no_conta, i.ds_versao_iped, i.qt_itens_total, i.qt_arquivos,
                       i.qt_emails, i.qt_contatos, i.qt_conversas, i.qt_localizacoes,
                       i.qt_contas_financeiras, i.dt_importacao,
                       u.no_usuario AS importado_por
                FROM investigacao.ipd_tb01_extracoes i
                LEFT JOIN seguranca.usr_tb01_usuarios u ON u.id_usuario = i.id_usuario
                WHERE i.id_inquerito = %s AND i.cd_status = 'concluido'
                ORDER BY i.dt_importacao
                """,
                (id_inquerito,),
            )
            colunas = [d.name for d in cur.description]
            return [dict(zip(colunas, linha)) for linha in cur.fetchall()]

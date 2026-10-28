"""Compartilhar prova: copia uma extração já processada para OUTRO inquérito, sem reimportar
o .ufdr.

O mesmo celular apreendido costuma ser prova em mais de um inquérito — a apreensão de um
flagrante que vira também objeto de uma investigação de organização criminosa, o aparelho do
mesmo alvo em duas delegacias. Reimportar o .ufdr no segundo inquérito custa horas (o passe do
report.xml, os hashes de dezenas de GB, a transcrição dos áudios, a indexação semântica) e
produz exatamente o mesmo resultado da primeira vez. A cópia leva minutos.

**O ARQUIVO NÃO É DUPLICADO NO DISCO.** NO_ARQUIVO da cópia aponta para o MESMO .ufdr da
origem, e é de lá que os anexos, as fotos e os áudios dos dois inquéritos são lidos
(_localizar_ufdr). Consequência prática: apagar a EXTRAÇÃO de origem (ou o inquérito dela) não
apaga o arquivo do disco — nenhuma das duas operações mexe em data/ —, então a cópia continua
inteira. Quem apagar o .ufdr à mão da pasta de dados tira os anexos dos dois, e os dois passam
a mostrar o aviso "Arquivo ausente" (com o botão de re-localizar).

O QUE É COPIADO — tudo que é dado da prova:

  * a linha da extração, com contagens, hashes, datas e a identidade do aparelho;
  * as tabelas de domínio penduradas em ID_EXTRACAO (descobertas por introspecção, ver
    _tabelas_filhas_diretas — domínio novo entra na cópia sozinho);
  * as "netas", que chegam pela linha do pai: participantes, mensagens e anexos das conversas,
    entradas da agenda e o embedding de imagem do arquivo (_NETAS);
  * os vetores da busca semântica das mensagens copiadas — o texto já foi embeddado uma vez, e
    reindexar no destino custaria de novo o modelo inteiro.

O QUE NÃO É COPIADO — o trabalho do analista, que é do INQUÉRITO e não da prova: entidades e
nós da Mesa, pessoas canônicas, achados do analista, marcações de relatório, conversas de IA e
detecções de padrões de crime. No fim da cópia a identificação de pessoas roda no inquérito de
destino e materializa a Mesa de lá a partir do que acabou de chegar — que é o mesmo que a
ingestão faz no fim de uma extração de verdade.

CUIDADO AO ACRESCENTAR DOMÍNIO: coluna que aponta para OUTRA LINHA do banco (id de conversa, de
mensagem, de contato, de arquivo) precisa ser remapeada aqui — copiada como está, ela apontaria
para a linha do inquérito de ORIGEM, e o destino passaria a exibir dado de outro inquérito. Hoje
o único caso fora das FKs é PIX_TB01_CHAVES (_REFS_PIX). Tabela nova com FK para o que é copiado
e que fique de fora do plano faz a cópia parar com mensagem legível (_conferir_cobertura), em
vez de gerar uma prova pela metade em silêncio.
"""

import logging
import threading

from funcoes.db_utils import get_connection
from funcoes.investigacao_utils import (
    _Batimento,
    _filtro_acesso_sql,
    liberar_extracoes_travadas,
)

logger = logging.getLogger(__name__)

# Tabelas com ID_EXTRACAO que NÃO são dado da prova: a própria linha da extração (copiada à
# parte, com os campos de controle zerados), a linha de controle da importação da Nuvem IPED
# (copiada à parte também, ver _copiar_linha_iped) e a fila de jobs pesados (OCR, classificação
# de imagem — trabalho agendado, não prova).
_TABELAS_CONTROLE = {"inq_tb02_extracoes", "ipd_tb01_extracoes", "job_tb01_jobs"}

# PIX_TB01_CHAVES sai do laço genérico porque depende dos mapas de mensagem/conversa/contato,
# que só existem depois das netas (ver _REFS_PIX e a ordem em _copiar_dados).
_TABELA_PIX = "pix_tb01_chaves"

# Tabelas que apontam para o que estamos copiando e ficam DE FORA de propósito. Qualquer outra
# que apareça no grafo de FKs faz _conferir_cobertura parar a cópia.
_FORA_DA_COPIA = {"ipd_tb01_extracoes", "job_tb01_jobs"}

# Netas: não têm ID_EXTRACAO e chegam pela linha do pai. Ordem = pai antes de filho.
# (tabela, {coluna: tabela_pai}) — a coluna NOT NULL vira JOIN (e é ela que filtra as linhas
# desta extração); a anulável vira LEFT JOIN (só traduz o valor quando existe).
_NETAS = (
    ("wpp_tb03_participantes", {"id_conversa": "wpp_tb02_conversas", "id_contato": "wpp_tb01_contatos"}),
    ("wpp_tb04_mensagens", {"id_conversa": "wpp_tb02_conversas", "id_contato_remetente": "wpp_tb01_contatos"}),
    ("wpp_tb05_anexos", {"id_mensagem": "wpp_tb04_mensagens"}),
    ("sms_tb03_participantes", {"id_conversa": "sms_tb02_conversas", "id_contato": "sms_tb01_contatos"}),
    ("sms_tb04_mensagens", {"id_conversa": "sms_tb02_conversas", "id_contato_remetente": "sms_tb01_contatos"}),
    ("sms_tb05_anexos", {"id_mensagem": "sms_tb04_mensagens"}),
    ("msg_tb03_participantes", {"id_conversa": "msg_tb02_conversas", "id_contato": "msg_tb01_contatos"}),
    ("msg_tb04_mensagens", {"id_conversa": "msg_tb02_conversas", "id_contato_remetente": "msg_tb01_contatos"}),
    ("msg_tb05_anexos", {"id_mensagem": "msg_tb04_mensagens"}),
    ("ctt_tb02_contato_entradas", {"id_contato": "ctt_tb01_contatos"}),
    ("arq_tb02_embedding_imagem", {"id_arquivo": "arq_tb01_arquivos"}),
)

# Chaves PIX: NO_FONTE diz em qual dos três mensageiros a chave foi vista, e os ids de mensagem,
# conversa e contato são daquele conjunto de tabelas (sem FK — ver funcoes/pix_utils.py). Cada
# coluna é traduzida pelo mapa da fonte certa: a condição entra no ON do LEFT JOIN e o valor sai
# de um COALESCE, porque o mesmo número pode existir nas três tabelas (sequences independentes).
_REFS_PIX = {
    "id_mensagem": (
        ("wpp_tb04_mensagens", "o.no_fonte = 'wpp'"),
        ("sms_tb04_mensagens", "o.no_fonte = 'sms'"),
        ("msg_tb04_mensagens", "o.no_fonte = 'msg'"),
    ),
    "id_conversa": (
        ("wpp_tb02_conversas", "o.no_fonte = 'wpp'"),
        ("sms_tb02_conversas", "o.no_fonte = 'sms'"),
        ("msg_tb02_conversas", "o.no_fonte = 'msg'"),
    ),
    "id_contato_remetente": (
        ("wpp_tb01_contatos", "o.no_fonte = 'wpp'"),
        ("sms_tb01_contatos", "o.no_fonte = 'sms'"),
        ("msg_tb01_contatos", "o.no_fonte = 'msg'"),
    ),
}

# Busca semântica: uma linha por trecho de mensagem, escopada por inquérito e ligada à mensagem
# por TP_FONTE + ID_MENSAGEM (sem FK). 'doc' fica de fora — é documento anexado ao inquérito, não
# vem do aparelho.
_FONTES_EMBEDDING = {"wpp": "wpp_tb04_mensagens", "sms": "sms_tb04_mensagens", "msg": "msg_tb04_mensagens"}

# Tabelas cujo id novo precisa ser conhecido por alguém depois (netas, PIX, embeddings). Só elas
# ganham tabela de mapa — guardar o mapa das outras custaria uma segunda escrita de milhões de
# linhas (arquivos, localizações) para ninguém ler.
_COM_MAPA = (
    {pai for _tabela, refs in _NETAS for pai in refs.values()}
    | {pai for alternativas in _REFS_PIX.values() for pai, _cond in alternativas}
    | {"wpp_tb04_mensagens", "sms_tb04_mensagens", "msg_tb04_mensagens"}
)

# Status em que a extração pode ser compartilhada: a cancelada preserva o que foi lido até a
# parada e é prova como qualquer outra (é o mesmo critério do prompt de IA e dos achados).
_STATUS_COMPARTILHAVEL = ("concluido", "cancelado")


# --- Introspecção do schema ---------------------------------------------------------------

def _colunas(cur, tabela: str) -> list[tuple[str, str, str]]:
    """(nome, is_identity, is_nullable) de cada coluna, na ordem do CREATE TABLE."""
    cur.execute(
        """
        SELECT column_name, is_identity, is_nullable
        FROM information_schema.columns
        WHERE table_schema = 'investigacao' AND table_name = %s
        ORDER BY ordinal_position
        """,
        (tabela,),
    )
    return [(c, i, n) for c, i, n in cur.fetchall()]


def _pk_identidade(cur, tabela: str) -> str | None:
    """Nome da coluna que é chave primária E identidade — o id que a cópia precisa gerar de novo.

    Devolve None quando a tabela não tem um id próprio (ARQ_TB02_EMBEDDING_IMAGEM, cuja PK é
    ID_ARQUIVO + NO_MODELO): aí não há id a gerar, só a FK a traduzir."""
    for nome, identidade, _nulavel in _colunas(cur, tabela):
        if identidade == "YES":
            return nome
    return None


def _tabelas_filhas_diretas(cur) -> list[str]:
    """Tabelas de dado penduradas direto em ID_EXTRACAO, em ordem alfabética (determinística).

    Por introspecção, e não por lista no código, de propósito: domínio novo do parser cria a
    tabela com ID_EXTRACAO e entra na cópia sem ninguém lembrar de vir aqui — o mesmo motivo pelo
    qual apagar extração tem a rede de segurança do ForeignKeyViolation."""
    cur.execute(
        """
        SELECT table_name FROM information_schema.columns
        WHERE table_schema = 'investigacao' AND column_name = 'id_extracao'
        ORDER BY table_name
        """
    )
    return [t for (t,) in cur.fetchall() if t not in _TABELAS_CONTROLE]


def _conferir_cobertura(cur, copiadas: set[str]) -> None:
    """Falha se alguma tabela aponta (por FK) para o que estamos copiando e ficou fora do plano.

    Rede de segurança do mesmo tipo da de apagar_extracao: sem ela, uma tabela-neta nova (mais um
    filho de mensagem, por exemplo) sairia calada da cópia, e o inquérito de destino receberia uma
    prova incompleta sem ninguém perceber — o pior defeito possível aqui."""
    cur.execute(
        """
        SELECT DISTINCT tc.table_name, ccu.table_name AS tabela_alvo
        FROM information_schema.table_constraints tc
        JOIN information_schema.constraint_column_usage ccu
          ON ccu.constraint_name = tc.constraint_name AND ccu.table_schema = tc.table_schema
        WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = 'investigacao'
          AND ccu.table_name = ANY(%s)
        """,
        (list(copiadas | {"inq_tb02_extracoes"}),),
    )
    faltando = sorted(
        {t for t, _alvo in cur.fetchall() if t not in copiadas and t not in _FORA_DA_COPIA}
    )
    if faltando:
        raise ValueError(
            "Não foi possível compartilhar: a(s) tabela(s) " + ", ".join(faltando) +
            " apontam para os dados desta extração e não estão no plano de cópia. "
            "Avise o administrador do sistema."
        )


# --- Cópia de uma tabela ------------------------------------------------------------------

def _nome_mapa(tabela: str) -> str:
    return "mapa_" + tabela


def _copiar_tabela(cur, tabela: str, *, id_origem: int | None, id_nova: int,
                   refs: dict | None = None, com_mapa: bool = False) -> int:
    """Copia as linhas de uma tabela para a extração nova e devolve quantas foram.

    As colunas vêm da introspecção (coluna nova acompanha a cópia sozinha) e cada uma vira:
    o id novo da extração (ID_EXTRACAO), o id novo do pai (colunas em `refs`), o id novo da
    própria linha (a PK identidade, quando `com_mapa`) ou o valor de origem, sem alteração.

    `id_origem` é o filtro das filhas diretas (ID_EXTRACAO = origem). Nas netas ele é None e
    quem filtra são os JOINs com os mapas dos pais — só existe mapa para as linhas desta
    extração, então o JOIN já recorta o conjunto certo."""
    refs = refs or {}
    colunas = _colunas(cur, tabela)
    nulavel = {nome: nul == "YES" for nome, _ident, nul in colunas}
    pk = _pk_identidade(cur, tabela)

    joins = []
    expr_ref = {}
    for coluna, alvo in refs.items():
        alternativas = alvo if isinstance(alvo, tuple) else ((alvo, None),)
        partes = []
        for pai, condicao in alternativas:
            apelido = "j_" + coluna + "_" + pai
            # LEFT JOIN quando a coluna aceita nulo ou quando há mais de uma fonte possível
            # (a chave PIX de WhatsApp não casa com o mapa de SMS, e não pode sumir por isso).
            tipo = "LEFT JOIN" if (nulavel[coluna] or condicao) else "JOIN"
            extra = f" AND {condicao}" if condicao else ""
            joins.append(f"{tipo} {_nome_mapa(pai)} {apelido} ON {apelido}.id_antigo = o.{coluna}{extra}")
            partes.append(f"{apelido}.id_novo")
        expr_ref[coluna] = partes[0] if len(partes) == 1 else "COALESCE(" + ", ".join(partes) + ")"

    onde = " WHERE o.id_extracao = %s" if id_origem is not None else ""
    origem_sql = f"FROM investigacao.{tabela} o " + " ".join(joins) + onde
    params_origem = (id_origem,) if id_origem is not None else ()

    if com_mapa:
        if pk is None:
            raise ValueError(f"Tabela {tabela} não tem id próprio para mapear.")
        cur.execute(
            f"CREATE TEMP TABLE {_nome_mapa(tabela)} "
            "(id_antigo BIGINT PRIMARY KEY, id_novo BIGINT NOT NULL) ON COMMIT DROP"
        )
        # nextval reserva os ids novos de uma vez, um por linha de origem, e o mapa guarda o
        # de-para. É o que permite copiar pai e filho em statements separados sem depender da
        # ordem do RETURNING (que o Postgres não promete casar com a do SELECT).
        cur.execute(
            f"INSERT INTO {_nome_mapa(tabela)} (id_antigo, id_novo) "
            f"SELECT o.{pk}, nextval(pg_get_serial_sequence('investigacao.{tabela}', '{pk}')) "
            + origem_sql,
            params_origem,
        )
        joins.append(f"JOIN {_nome_mapa(tabela)} m_self ON m_self.id_antigo = o.{pk}")
        origem_sql = f"FROM investigacao.{tabela} o " + " ".join(joins) + onde

    destino, valores, params = [], [], []
    for nome, identidade, _nul in colunas:
        if identidade == "YES":
            if not com_mapa:
                continue  # sem mapa: deixa a própria sequência da tabela gerar o id
            destino.append(nome)
            valores.append("m_self.id_novo")
        elif nome == "id_extracao":
            destino.append(nome)
            valores.append("%s")
            params.append(id_nova)
        elif nome in expr_ref:
            destino.append(nome)
            valores.append(expr_ref[nome])
        else:
            destino.append(nome)
            valores.append(f"o.{nome}")

    sobrepor = "OVERRIDING SYSTEM VALUE " if com_mapa else ""
    cur.execute(
        f"INSERT INTO investigacao.{tabela} ({', '.join(destino)}) {sobrepor}"
        f"SELECT {', '.join(valores)} " + origem_sql,
        tuple(params) + params_origem,
    )
    return cur.rowcount


def _copiar_embeddings(cur, id_inquerito_origem: int, id_inquerito_destino: int) -> int:
    """Leva os vetores da busca semântica das mensagens copiadas para o inquérito de destino.

    Sem isto a busca semântica do destino só voltaria a enxergar este aparelho depois de uma
    reindexação inteira — que refaz, pagando o modelo de embeddings de novo, exatamente os
    vetores que já estão no banco."""
    total = 0
    for fonte, tabela in _FONTES_EMBEDDING.items():
        cur.execute(
            f"""
            INSERT INTO investigacao.emb_tb01_embeddings
                (id_inquerito, tp_fonte, id_mensagem, tx_trecho, vl_vetor, nr_trecho)
            SELECT %s, e.tp_fonte, m.id_novo, e.tx_trecho, e.vl_vetor, e.nr_trecho
            FROM investigacao.emb_tb01_embeddings e
            JOIN {_nome_mapa(tabela)} m ON m.id_antigo = e.id_mensagem
            WHERE e.id_inquerito = %s AND e.tp_fonte = %s
            ON CONFLICT (id_inquerito, tp_fonte, id_mensagem, nr_trecho) DO NOTHING
            """,
            (id_inquerito_destino, id_inquerito_origem, fonte),
        )
        total += cur.rowcount
    return total


def _copiar_linha_iped(cur, id_origem: int, id_nova: int, id_inquerito_destino: int) -> bool:
    """Copia a linha de controle da Nuvem IPED, quando a extração é a ponte de um caso do IPED.

    Sem ela o destino mostraria os dados sem o card "Nuvem IPED — <conta>" que os explica. NO_PASTA
    aponta para a pasta do caso na origem, pelo mesmo motivo de NO_ARQUIVO: nada é copiado no
    disco.

    Nasce junto com a linha da extração, e já em 'processando'/'copiando': é ELA que vira o card
    da importação de nuvem no destino (o card de aparelho ignora a extração-ponte), então sem
    isso o inquérito de destino ficaria minutos sem mostrar nada enquanto a cópia roda. Quem a
    fecha é _finalizar."""
    colunas = _colunas(cur, "ipd_tb01_extracoes")
    fixos = {"id_extracao": id_nova, "id_inquerito": id_inquerito_destino,
             "cd_status": "processando", "cd_fase": "copiando", "qt_progresso_fase": 0,
             "tx_erro": None, "fl_cancelar": False}
    destino, valores, params = [], [], []
    for nome, identidade, _nul in colunas:
        if identidade == "YES":
            continue
        destino.append(nome)
        if nome in fixos:
            valores.append("%s")
            params.append(fixos[nome])
        else:
            valores.append(f"o.{nome}")
    cur.execute(
        f"INSERT INTO investigacao.ipd_tb01_extracoes ({', '.join(destino)}) "
        f"SELECT {', '.join(valores)} FROM investigacao.ipd_tb01_extracoes o WHERE o.id_extracao = %s",
        tuple(params) + (id_origem,),
    )
    return cur.rowcount > 0


# --- Orquestração -------------------------------------------------------------------------

def _dados_extracao(id_extracao: int) -> dict | None:
    """Cabeçalho da extração + número do inquérito dela, para as conferências e para a cópia."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT e.id_extracao, e.id_inquerito, e.cd_status, e.no_arquivo,
                       e.co_hash_sha256, i.nu_inquerito
                FROM investigacao.inq_tb02_extracoes e
                JOIN investigacao.inq_tb01_inqueritos i ON i.id_inquerito = e.id_inquerito
                WHERE e.id_extracao = %s
                """,
                (id_extracao,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return dict(zip([d.name for d in cur.description], row))


def listar_destinos(id_extracao: int, usuario: dict) -> dict:
    """Inquéritos para onde esta prova pode ser copiada, para o seletor da tela.

    Traz todos os inquéritos visíveis ao usuário (o mesmo filtro de acesso das outras listagens —
    o isolamento por delegacia sai de graça daí), menos o de origem, marcando com JA_TEM os que
    já têm este mesmo arquivo. Marcar em vez de esconder é de propósito: o investigador precisa
    entender por que o inquérito que ele procura não está disponível."""
    origem = _dados_extracao(id_extracao)
    if origem is None:
        return {"origem": None, "destinos": []}
    filtro, params = _filtro_acesso_sql(usuario, "i")
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT i.id_inquerito, i.nu_inquerito, i.no_delegacia,
                       EXISTS (
                           SELECT 1 FROM investigacao.inq_tb02_extracoes x
                           WHERE x.id_inquerito = i.id_inquerito
                             AND (x.no_arquivo = %s
                                  OR (x.co_hash_sha256 IS NOT NULL AND x.co_hash_sha256 = %s))
                       ) AS ja_tem
                FROM investigacao.inq_tb01_inqueritos i
                WHERE TRUE{filtro} AND i.id_inquerito <> %s
                ORDER BY i.nu_inquerito
                """,
                (origem["no_arquivo"], origem["co_hash_sha256"]) + params + (origem["id_inquerito"],),
            )
            destinos = [dict(zip([d.name for d in cur.description], linha)) for linha in cur.fetchall()]
    return {
        "origem": {"id_extracao": id_extracao, "nu_inquerito": origem["nu_inquerito"],
                   "no_arquivo": origem["no_arquivo"]},
        "destinos": destinos,
    }


def compartilhar_extracao(id_extracao: int, id_inquerito_destino: int, id_usuario: int | None) -> dict:
    """Cria no inquérito de destino a cópia desta extração e dispara a cópia dos dados em
    segundo plano.

    Devolve na hora {ok, id_extracao} da linha nova — ela já aparece no card do inquérito de
    destino com o badge "Copiando prova", e o polling da tela acompanha o resto, como em qualquer
    extração. Levanta ValueError (a rota converte em 4xx com a mensagem) quando a extração não
    pode ser compartilhada."""
    # extração presa (worker morto) não pode barrar a cópia para sempre — mesmo cuidado de apagar
    liberar_extracoes_travadas()
    origem = _dados_extracao(id_extracao)
    if origem is None:
        raise ValueError("Extração não encontrada.")
    if origem["cd_status"] not in _STATUS_COMPARTILHAVEL:
        raise ValueError(
            "Só dá para compartilhar uma extração já processada — esta está com status "
            f"'{origem['cd_status']}'.")
    if origem["id_inquerito"] == id_inquerito_destino:
        raise ValueError("A prova já é deste inquérito.")

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT nu_inquerito FROM investigacao.inq_tb01_inqueritos WHERE id_inquerito = %s",
                (id_inquerito_destino,),
            )
            row = cur.fetchone()
            if row is None:
                raise ValueError("Inquérito de destino não encontrado.")
            nu_destino = row[0]
            # Mesma prova duas vezes no mesmo inquérito é ruído puro: o investigador passaria a
            # ver cada conversa em duplicata sem nenhuma informação nova.
            cur.execute(
                """
                SELECT 1 FROM investigacao.inq_tb02_extracoes
                WHERE id_inquerito = %s
                  AND (no_arquivo = %s
                       OR (co_hash_sha256 IS NOT NULL AND co_hash_sha256 = %s))
                """,
                (id_inquerito_destino, origem["no_arquivo"], origem["co_hash_sha256"]),
            )
            if cur.fetchone() is not None:
                raise ValueError(f"O inquérito {nu_destino} já tem esta prova.")

            # A linha nova nasce com os campos de PROVA da origem (arquivo, hashes, contagens,
            # datas, identidade do aparelho) e os de CONTROLE zerados: quem está processando
            # agora é esta cópia. Montada por introspecção para que uma contagem nova de domínio
            # entre na cópia sem passar por aqui.
            fixos = {
                "id_inquerito": id_inquerito_destino,
                "id_usuario": id_usuario,
                "cd_status": "processando",
                "cd_fase": "copiando",
                "qt_progresso_fase": 0,
                "tx_erro": None,
                "fl_cancelar": False,
                "dt_inicio_processamento": None,   # now() abaixo
                "dt_fim_processamento": None,
                "dt_ultima_atividade": None,       # now() abaixo
                "id_extracao_origem": id_extracao,
                "nu_inquerito_origem": origem["nu_inquerito"],
                "dt_compartilhamento": None,       # now() abaixo
            }
            agora = {"dt_inicio_processamento", "dt_ultima_atividade", "dt_compartilhamento"}
            destino, valores, params = [], [], []
            for nome, identidade, _nul in _colunas(cur, "inq_tb02_extracoes"):
                if identidade == "YES":
                    continue
                destino.append(nome)
                if nome in agora:
                    valores.append("now()")
                elif nome in fixos:
                    valores.append("%s")
                    params.append(fixos[nome])
                else:
                    valores.append(f"o.{nome}")
            cur.execute(
                f"INSERT INTO investigacao.inq_tb02_extracoes ({', '.join(destino)}) "
                f"SELECT {', '.join(valores)} FROM investigacao.inq_tb02_extracoes o "
                "WHERE o.id_extracao = %s RETURNING id_extracao",
                tuple(params) + (id_extracao,),
            )
            id_nova = cur.fetchone()[0]
            _copiar_linha_iped(cur, id_extracao, id_nova, id_inquerito_destino)
        conn.commit()

    threading.Thread(
        target=_processar_copia,
        args=(id_extracao, id_nova, origem["id_inquerito"], id_inquerito_destino),
        daemon=True,
        name=f"compartilhar-prova-{id_extracao}-{id_nova}",
    ).start()

    logger.info("Compartilhando prova: extração %s (inquérito %s) -> extração %s (inquérito %s)",
                id_extracao, origem["nu_inquerito"], id_nova, nu_destino)
    return {"ok": True, "id_extracao": id_nova, "id_inquerito": id_inquerito_destino,
            "nu_inquerito": nu_destino,
            "mensagem": f"Cópia iniciada para o inquérito {nu_destino} — acompanhe o progresso "
                        "no card do aparelho lá."}


def _processar_copia(id_origem: int, id_nova: int, id_inq_origem: int, id_inq_destino: int) -> None:
    """Roda a cópia em segundo plano e fecha a extração nova (concluído, cancelado ou erro)."""
    bat = _Batimento(id_nova)
    bat.iniciar_vigia()
    try:
        cancelou = _copiar_dados(id_origem, id_nova, id_inq_origem, id_inq_destino, bat)
        if cancelou:
            # Cópia interrompida no meio não vira extração: os dados já inseridos voltaram com o
            # rollback, e uma extração vazia no destino seria pior do que nenhuma — passaria por
            # prova completa. A linha inteira sai.
            _apagar_linha(id_nova)
            logger.info("Cópia da extração %s cancelada — a extração %s foi removida", id_origem, id_nova)
            return
        bat.fase("identificando_pessoas")
        _identificar_pessoas(id_inq_destino, id_nova)
        _finalizar(id_nova)
        logger.info("Prova compartilhada: extração %s copiada para a extração %s", id_origem, id_nova)
    except Exception as exc:
        logger.exception("Falha ao compartilhar a prova da extração %s", id_origem)
        _marcar_erro(id_nova, str(exc))
    finally:
        bat.parar_vigia()


def _copiar_dados(id_origem: int, id_nova: int, id_inq_origem: int, id_inq_destino: int,
                  bat: "_Batimento") -> bool:
    """Copia tudo numa transação só e devolve True se o usuário pediu para parar.

    UMA transação de propósito: prova pela metade não existe. Qualquer falha no meio (ou o pedido
    de parada) desfaz a cópia inteira, e o destino não fica com um aparelho que parece completo e
    não é. A conexão é `longa` porque é exatamente o perfil dos laços de ingestão — sem
    statement_timeout, que num aparelho de centenas de milhares de mensagens cortaria um INSERT
    legítimo no meio."""
    with get_connection(longa=True) as conn:
        with conn.cursor() as cur:
            diretas = [t for t in _tabelas_filhas_diretas(cur) if t != _TABELA_PIX]
            plano = diretas + [t for t, _refs in _NETAS] + [_TABELA_PIX]
            _conferir_cobertura(cur, set(plano))

            total_passos = len(plano) + 1  # + os vetores da busca semântica
            copiadas = 0
            for tabela in diretas:
                if bat.cancelar_pedido():
                    conn.rollback()
                    return True
                _copiar_tabela(cur, tabela, id_origem=id_origem, id_nova=id_nova,
                               com_mapa=tabela in _COM_MAPA)
                copiadas += 1
                bat.progresso(copiadas, total_passos)

            for tabela, refs in _NETAS:
                if bat.cancelar_pedido():
                    conn.rollback()
                    return True
                _copiar_tabela(cur, tabela, id_origem=None, id_nova=id_nova, refs=refs,
                               com_mapa=tabela in _COM_MAPA)
                copiadas += 1
                bat.progresso(copiadas, total_passos)

            # PIX depois das netas: as chaves apontam para as mensagens (ver _REFS_PIX)
            _copiar_tabela(cur, _TABELA_PIX, id_origem=id_origem, id_nova=id_nova, refs=_REFS_PIX)
            copiadas += 1
            bat.progresso(copiadas, total_passos)

            _copiar_embeddings(cur, id_inq_origem, id_inq_destino)
            copiadas += 1
            bat.progresso(copiadas, total_passos)

            # A origem pode ter sido apagada por outra pessoa enquanto isto rodava (a cópia leva
            # minutos). Apagar extração é UMA transação — dados e linha vão juntos —, então as
            # leituras acima viram ou o estado inteiro de antes, ou nada; e se a linha sumiu, parte
            # do que copiamos pode ter vindo de depois do DELETE. Uma prova pela metade é pior do
            # que nenhuma: desfaz tudo e avisa.
            cur.execute("SELECT 1 FROM investigacao.inq_tb02_extracoes WHERE id_extracao = %s",
                        (id_origem,))
            if cur.fetchone() is None:
                raise ValueError("A extração de origem foi apagada enquanto a prova era copiada.")
        conn.commit()
    return False


def _identificar_pessoas(id_inquerito: int, id_extracao: int) -> None:
    """Materializa na Mesa do inquérito de destino as pessoas que a prova recém-chegada
    identifica — mesma chamada que a ingestão faz no fim de uma extração. Best-effort: a cópia
    já está gravada e não pode ser derrubada por isto."""
    from funcoes.identificacao_pessoas import IdentificacaoEmAndamento, identificar_pessoas_inquerito
    try:
        identificar_pessoas_inquerito(id_inquerito, incluir_extracao=id_extracao)
    except IdentificacaoEmAndamento:
        logger.info("Identificação de pessoas da extração %s pulada: já havia uma em andamento "
                    "no inquérito %s", id_extracao, id_inquerito)
    except Exception:
        logger.exception("Falha na identificação de pessoas do inquérito %s (best-effort)", id_inquerito)


def _finalizar(id_extracao: int) -> None:
    """Fecha a cópia como 'concluido'. O status da origem não é herdado: 'cancelado' lá quer
    dizer "a LEITURA do .ufdr parou no meio", e a cópia leu tudo que existia."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE investigacao.inq_tb02_extracoes
                SET cd_status = 'concluido', cd_fase = NULL, qt_progresso_fase = NULL,
                    tx_erro = NULL, dt_fim_processamento = now(), dt_ultima_atividade = now()
                WHERE id_extracao = %s
                """,
                (id_extracao,),
            )
            # a linha da Nuvem IPED (quando existe) é o card do destino: fecha junto
            cur.execute(
                """
                UPDATE investigacao.ipd_tb01_extracoes
                SET cd_status = 'concluido', cd_fase = NULL, qt_progresso_fase = NULL,
                    tx_erro = NULL, dt_fim_processamento = now()
                WHERE id_extracao = %s
                """,
                (id_extracao,),
            )
        conn.commit()


def _marcar_erro(id_extracao: int, mensagem: str) -> None:
    """Deixa a cópia visível como 'erro' (com a mensagem) em vez de sumir: os dados foram
    desfeitos pelo rollback, e quem pediu precisa saber que não pode contar com ela. A linha vazia
    é apagável pelo menu do card, como qualquer extração que falhou."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE investigacao.inq_tb02_extracoes
                SET cd_status = 'erro', cd_fase = NULL, qt_progresso_fase = NULL,
                    tx_erro = %s, dt_fim_processamento = now(), dt_ultima_atividade = now()
                WHERE id_extracao = %s
                """,
                (("Falha ao copiar a prova: " + mensagem)[:2000], id_extracao),
            )
            cur.execute(
                """
                UPDATE investigacao.ipd_tb01_extracoes
                SET cd_status = 'erro', cd_fase = NULL, qt_progresso_fase = NULL, tx_erro = %s,
                    dt_fim_processamento = now()
                WHERE id_extracao = %s
                """,
                (("Falha ao copiar a prova: " + mensagem)[:2000], id_extracao),
            )
        conn.commit()


def _apagar_linha(id_extracao: int) -> None:
    """Remove a linha da cópia cancelada (o rollback já desfez os dados dela). A linha de
    controle da Nuvem IPED sai antes: ela aponta para a extração."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM investigacao.ipd_tb01_extracoes WHERE id_extracao = %s",
                        (id_extracao,))
            cur.execute("DELETE FROM investigacao.inq_tb02_extracoes WHERE id_extracao = %s",
                        (id_extracao,))
        conn.commit()

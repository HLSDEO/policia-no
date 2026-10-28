"""Teste da política de conteúdo da indexação semântica (o que MERECE ir para o embedding):
dedup de lote (a mesma corrente encaminhada N vezes paga 1 chamada de API), corte por tamanho
mínimo de mensagem, heurística de fatia de documento (extrato/planilha/OCR lixo não veta vetor)
e o SQL de pendentes com os filtros novos (validado de verdade contra um Postgres com pgvector,
via EMBED_TEST_PG_DSN — sem a variável, roda só a parte pura).

    docker compose exec api-1 python /app/tools/teste_politica_embed.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from funcoes import embed_utils as eu  # noqa: E402

PROSA = ("A investigacao apurou que o servidor publico recebia valores a titulo de propina "
         "em troca de licencas irregulares. ") * 200


def teste_knobs():
    ambiente = dict(os.environ)
    try:
        os.environ.pop("EMBED_MIN_TEXTO", None)
        assert eu._min_texto() == 15
        os.environ["EMBED_MIN_TEXTO"] = "0"
        assert eu._min_texto() == 0
        os.environ["EMBED_MIN_TEXTO"] = "abc"
        assert eu._min_texto() == 15
        os.environ.pop("EMBED_DEDUP", None)
        assert eu._dedup_ativo()
        for valor in ("0", "false", "nao", "off"):
            os.environ["EMBED_DEDUP"] = valor
            assert not eu._dedup_ativo(), valor
    finally:
        os.environ.clear()
        os.environ.update(ambiente)
    print("knobs: EMBED_MIN_TEXTO/EMBED_DEDUP com default e desligamento")


def teste_dedup_lote():
    assert eu._chave_dedup("  Bom   DIA\n") == eu._chave_dedup("bom dia")
    lote = ([{"texto": "Corrente solidaria, compartilhe!"}] * 5
            + [{"texto": "O processo tramita em sigilo."}] * 2
            + [{"texto": "CORRENTE SOLIDARIA, COMPARTILHE!"}])
    chaves, distintos, posicoes = {}, [], []
    for r in lote:
        chave = eu._chave_dedup(r["texto"])
        if chave not in chaves:
            chaves[chave] = len(distintos)
            distintos.append(r["texto"])
        posicoes.append(chaves[chave])
    assert len(distintos) == 2, distintos
    assert posicoes == [0] * 5 + [1] * 2 + [0], posicoes
    print("dedup: lote de 8 com 2 textos distintos -> 2 chamadas, vetor redistribuido")


def teste_fatia_relevante():
    assert eu._fatia_relevante("O prefeito autorizou o contrato sem licitacao, segundo o relatorio.")
    assert not eu._fatia_relevante("123.456,78 01/02/2023 987.654,32 30/06/2024 " * 50)
    assert not eu._fatia_relevante("|||| || | |---|---| --- |||| | | " * 50)
    assert not eu._fatia_relevante("ok!!")
    assert not eu._fatia_relevante("")
    print("fatia: prosa passa; tabela numerica, OCR lixo e vazio sao descartados")


def teste_fatiar_documento():
    assert eu._fatiar_documento("") == []
    curto = "Documento curto com prosa relevante sobre o caso."
    assert eu._fatiar_documento(curto) == [curto]
    assert eu._fatiar_documento("123 456 789 000 111 222") == []
    fatias = eu._fatiar_documento(PROSA)
    assert 1 < len(fatias) <= eu._MAX_TRECHOS_DOCUMENTO
    assert all(len(f) <= eu._TAMANHO_TRECHO_DOCUMENTO for f in fatias)
    assert eu._fatiar_documento("000.000,00 111.111,11 " * 2000) == []
    print("fatiar: teto e tamanho respeitados; documento so-numeros nao indexa nada")


def _tabelas_sql(cur):
    cur.execute("CREATE SCHEMA IF NOT EXISTS investigacao")
    cur.execute("""
        CREATE TABLE investigacao.inq_tb02_extracoes (id_extracao int PRIMARY KEY, id_inquerito int)
    """)
    cur.execute("""
        CREATE TABLE investigacao.inq_tb04_documentos (
            id_documento int PRIMARY KEY, id_inquerito int, tx_conteudo text, tx_erro text)
    """)
    for tabela in ("wpp", "sms", "msg"):
        cur.execute(f"""
            CREATE TABLE investigacao.{tabela}_tb02_conversas (
                id_conversa int PRIMARY KEY, id_extracao int)
        """)
        cur.execute(f"""
            CREATE TABLE investigacao.{tabela}_tb04_mensagens (
                id_mensagem int PRIMARY KEY, id_conversa int, tx_mensagem text)
        """)
        cur.execute(f"""
            CREATE TABLE investigacao.{tabela}_tb05_anexos (
                id_anexo int PRIMARY KEY, id_mensagem int, tx_transcricao text)
        """)
    cur.execute("""
        CREATE TABLE investigacao.emb_tb01_embeddings (
            id_inquerito int, tp_fonte text, id_mensagem int, nr_trecho int,
            tx_trecho text, vl_vetor vector,
            UNIQUE (id_inquerito, tp_fonte, id_mensagem, nr_trecho))
    """)


def teste_sql_pendentes():
    """Roda o _SQL_MSGS_INDEXAR real contra um Postgres de teste (EMBED_TEST_PG_DSN)."""
    dsn = os.environ.get("EMBED_TEST_PG_DSN")
    if not dsn:
        print("sql pendentes: pulado (defina EMBED_TEST_PG_DSN)")
        return
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA IF EXISTS investigacao CASCADE")
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
            _tabelas_sql(cur)
            cur.execute("INSERT INTO investigacao.inq_tb02_extracoes VALUES (1, 187)")
            cur.execute("INSERT INTO investigacao.wpp_tb02_conversas VALUES (1, 1)")
            cur.execute("""
                INSERT INTO investigacao.wpp_tb04_mensagens VALUES
                    (1, 1, 'ok'),
                    (2, 1, '👍👍👍👍👍👍👍👍👍👍👍👍👍👍👍'),
                    (3, 1, 'https://exemplo.com.br/link'),
                    (4, 1, 'O dinheiro foi entregue na casa do vereador a noite')
            """)
            cur.execute("INSERT INTO investigacao.sms_tb02_conversas VALUES (1, 1)")
            cur.execute("INSERT INTO investigacao.sms_tb04_mensagens VALUES (1, 1, NULL)")
            cur.execute("INSERT INTO investigacao.msg_tb02_conversas VALUES (1, 1)")
            cur.execute("INSERT INTO investigacao.msg_tb04_mensagens VALUES (1, 1, NULL)")
            cur.execute("INSERT INTO investigacao.msg_tb05_anexos VALUES (1, 1, 'transcricao do audio com conteudo relevante sobre o caso')")

        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(eu._SQL_MSGS_INDEXAR, {"id": 187, "min_texto": eu._min_texto()})
            textos = sorted(r["texto"] for r in cur)
        assert textos == sorted([
            "O dinheiro foi entregue na casa do vereador a noite",
            "transcricao do audio com conteudo relevante sobre o caso",
        ]), textos

        with conn.cursor() as cur:
            cur.execute(eu._SQL_MSGS_INDEXAR, {"id": 187, "min_texto": 0})
            n_sem_corte = cur.rowcount
        assert n_sem_corte > 2, n_sem_corte
        conn.rollback()
    print("sql pendentes: filtros de tamanho/emoji/URL validados no Postgres")


def main():
    teste_knobs()
    teste_dedup_lote()
    teste_fatia_relevante()
    teste_fatiar_documento()
    teste_sql_pendentes()
    print("POLITICA DE EMBED OK")


if __name__ == "__main__":
    main()

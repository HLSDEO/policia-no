"""Teste de integração do "Compartilhar prova" — roda contra o Postgres de verdade.

Copia uma extração já existente para um inquérito de teste, confere a cópia e **desfaz tudo no
final** (apaga o inquérito de teste inteiro): nenhuma linha sobra no banco.

O que fica provado, que é o que uma prova compartilhada precisa garantir:

1. **Nada some.** Cada tabela do plano de cópia tem, no destino, exatamente a mesma contagem da
   origem — inclusive as netas (mensagens, anexos, participantes) e os vetores da busca
   semântica.
2. **Nada aponta para o inquérito de origem.** Toda mensagem da cópia está numa conversa da
   cópia, todo anexo numa mensagem da cópia, toda chave PIX numa mensagem da cópia e todo vetor
   numa mensagem da cópia. É o defeito que arruinaria a feature em silêncio: id copiado como
   está continua sendo o id do OUTRO inquérito, e a tela do destino mostraria dado alheio.
3. **A prova não é duplicada no disco.** NO_ARQUIVO da cópia é o mesmo da origem.

    docker compose exec api-1 python /app/tools/teste_compartilhar_prova.py [id_extracao]
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from funcoes.compartilhar_prova import (  # noqa: E402
    _NETAS,
    _TABELA_PIX,
    _tabelas_filhas_diretas,
    compartilhar_extracao,
)
from funcoes.db_utils import get_connection  # noqa: E402
from funcoes.investigacao_utils import apagar_inquerito  # noqa: E402

NU_INQUERITO_TESTE = "TESTE-COMPARTILHAR-PROVA"

falhas = []


def conferir(condicao, descricao):
    print(("  ok   " if condicao else "  FALHA ") + descricao)
    if not condicao:
        falhas.append(descricao)


def escolher_extracao(cur, argumento):
    if argumento:
        return int(argumento)
    cur.execute(
        """SELECT id_extracao FROM investigacao.inq_tb02_extracoes
           WHERE cd_status IN ('concluido', 'cancelado')
           ORDER BY coalesce(qt_mensagens, 0) DESC LIMIT 1"""
    )
    row = cur.fetchone()
    if row is None:
        sys.exit("Nenhuma extração concluída no banco para testar.")
    return row[0]


def contar(cur, tabela, coluna, valor):
    cur.execute(f"SELECT count(*) FROM investigacao.{tabela} WHERE {coluna} = %s", (valor,))
    return cur.fetchone()[0]


def contar_neta(cur, tabela, coluna_pai, tabela_pai, coluna_pai_pk, id_extracao):
    cur.execute(
        f"""SELECT count(*) FROM investigacao.{tabela} f
            JOIN investigacao.{tabela_pai} p ON p.{coluna_pai_pk} = f.{coluna_pai}
            WHERE p.id_extracao = %s""",
        (id_extracao,),
    )
    return cur.fetchone()[0]


def main():
    id_origem = None
    id_copia = None
    id_inq_teste = None
    with get_connection() as conn:
        with conn.cursor() as cur:
            id_origem = escolher_extracao(cur, sys.argv[1] if len(sys.argv) > 1 else None)
            cur.execute(
                "SELECT id_inquerito, no_arquivo, no_dispositivo FROM investigacao.inq_tb02_extracoes "
                "WHERE id_extracao = %s", (id_origem,))
            id_inq_origem, no_arquivo, dispositivo = cur.fetchone()
            cur.execute(
                "INSERT INTO investigacao.inq_tb01_inqueritos (nu_inquerito, id_usuario_criador) "
                "VALUES (%s, (SELECT min(id_usuario) FROM seguranca.usr_tb01_usuarios)) "
                "RETURNING id_inquerito", (NU_INQUERITO_TESTE,))
            id_inq_teste = cur.fetchone()[0]
        conn.commit()

    print(f"Origem: extração {id_origem} ({dispositivo}) do inquérito {id_inq_origem}")
    print(f"Destino: inquérito de teste {id_inq_teste}")
    try:
        inicio = time.monotonic()
        resultado = compartilhar_extracao(id_origem, id_inq_teste, None)
        id_copia = resultado["id_extracao"]
        status = "processando"
        while status in ("pendente", "processando"):
            time.sleep(2)
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT cd_status, cd_fase, qt_progresso_fase, tx_erro "
                        "FROM investigacao.inq_tb02_extracoes WHERE id_extracao = %s", (id_copia,))
                    status, fase, pct, erro = cur.fetchone()
            print(f"  ... {status} {fase or ''} {pct if pct is not None else ''}")
        print(f"Cópia terminou em {time.monotonic() - inicio:.0f}s com status {status} ({erro or 'sem erro'})")
        conferir(status == "concluido", "cópia concluída sem erro")
        if status != "concluido":
            return

        with get_connection() as conn:
            with conn.cursor() as cur:
                # 1. contagens iguais, tabela a tabela
                for tabela in _tabelas_filhas_diretas(cur):
                    n_origem = contar(cur, tabela, "id_extracao", id_origem)
                    n_copia = contar(cur, tabela, "id_extracao", id_copia)
                    if n_origem or n_copia:
                        conferir(n_origem == n_copia, f"{tabela}: {n_origem} linhas nos dois")
                pais = {
                    "wpp_tb02_conversas": "id_conversa", "sms_tb02_conversas": "id_conversa",
                    "msg_tb02_conversas": "id_conversa", "wpp_tb01_contatos": "id_contato",
                    "sms_tb01_contatos": "id_contato", "msg_tb01_contatos": "id_contato",
                    "ctt_tb01_contatos": "id_contato", "arq_tb01_arquivos": "id_arquivo",
                }
                for tabela, refs in _NETAS:
                    coluna, pai = next(iter(refs.items()))
                    if pai not in pais:  # anexos: o pai é a tabela de mensagens, tratada abaixo
                        continue
                    n_origem = contar_neta(cur, tabela, coluna, pai, pais[pai], id_origem)
                    n_copia = contar_neta(cur, tabela, coluna, pai, pais[pai], id_copia)
                    if n_origem or n_copia:
                        conferir(n_origem == n_copia, f"{tabela}: {n_origem} linhas nos dois")
                for fonte in ("wpp", "sms", "msg"):
                    cur.execute(
                        f"""SELECT count(*) FROM investigacao.{fonte}_tb05_anexos a
                            JOIN investigacao.{fonte}_tb04_mensagens m ON m.id_mensagem = a.id_mensagem
                            JOIN investigacao.{fonte}_tb02_conversas c ON c.id_conversa = m.id_conversa
                            WHERE c.id_extracao = ANY(%s)
                            GROUP BY c.id_extracao ORDER BY c.id_extracao = %s DESC""",
                        ([id_origem, id_copia], id_origem),
                    )
                    linhas = [n for (n,) in cur.fetchall()]
                    if linhas:
                        conferir(len(linhas) == 2 and linhas[0] == linhas[1],
                                 f"{fonte}_tb05_anexos: {linhas[0]} anexos nos dois")

                # 2. nenhum ponteiro para o inquérito de origem
                for fonte in ("wpp", "sms", "msg"):
                    cur.execute(
                        f"""SELECT count(*) FROM investigacao.{fonte}_tb04_mensagens m
                            JOIN investigacao.{fonte}_tb02_conversas c ON c.id_conversa = m.id_conversa
                            WHERE c.id_extracao = %s AND (
                                m.id_contato_remetente IS NOT NULL AND NOT EXISTS (
                                    SELECT 1 FROM investigacao.{fonte}_tb01_contatos k
                                    WHERE k.id_contato = m.id_contato_remetente AND k.id_extracao = %s))""",
                        (id_copia, id_copia),
                    )
                    conferir(cur.fetchone()[0] == 0,
                             f"{fonte}: remetente de toda mensagem da cópia é contato da cópia")
                cur.execute(
                    f"""SELECT count(*) FROM investigacao.{_TABELA_PIX} k
                        WHERE k.id_extracao = %s AND NOT EXISTS (
                            SELECT 1 FROM investigacao.wpp_tb04_mensagens m
                            JOIN investigacao.wpp_tb02_conversas c ON c.id_conversa = m.id_conversa
                            WHERE m.id_mensagem = k.id_mensagem AND c.id_extracao = %s)
                          AND k.no_fonte = 'wpp'""",
                    (id_copia, id_copia),
                )
                conferir(cur.fetchone()[0] == 0, "toda chave PIX da cópia aponta para mensagem da cópia")
                cur.execute(
                    """SELECT count(*) FROM investigacao.emb_tb01_embeddings e
                       WHERE e.id_inquerito = %s AND e.tp_fonte = 'wpp' AND NOT EXISTS (
                           SELECT 1 FROM investigacao.wpp_tb04_mensagens m
                           JOIN investigacao.wpp_tb02_conversas c ON c.id_conversa = m.id_conversa
                           WHERE m.id_mensagem = e.id_mensagem AND c.id_extracao = %s)""",
                    (id_inq_teste, id_copia),
                )
                conferir(cur.fetchone()[0] == 0, "todo vetor copiado aponta para mensagem da cópia")
                cur.execute(
                    "SELECT count(*) FROM investigacao.emb_tb01_embeddings WHERE id_inquerito = %s",
                    (id_inq_teste,))
                n_vetores = cur.fetchone()[0]
                cur.execute(
                    """SELECT count(*) FROM investigacao.emb_tb01_embeddings e
                       WHERE e.id_inquerito = %s AND e.tp_fonte <> 'doc' AND EXISTS (
                           SELECT 1 FROM investigacao.wpp_tb04_mensagens m
                           JOIN investigacao.wpp_tb02_conversas c ON c.id_conversa = m.id_conversa
                           WHERE m.id_mensagem = e.id_mensagem AND c.id_extracao = %s)""",
                    (id_inq_origem, id_origem),
                )
                conferir(n_vetores >= cur.fetchone()[0], f"{n_vetores} vetores copiados para o destino")

                # 3. arquivo não duplicado no disco + procedência registrada
                cur.execute(
                    "SELECT no_arquivo, id_extracao_origem, nu_inquerito_origem, dt_compartilhamento "
                    "FROM investigacao.inq_tb02_extracoes WHERE id_extracao = %s", (id_copia,))
                arq_copia, origem_reg, nu_origem, dt_comp = cur.fetchone()
                conferir(arq_copia == no_arquivo, "a cópia aponta para o mesmo .ufdr da origem")
                conferir(origem_reg == id_origem and nu_origem and dt_comp is not None,
                         "procedência registrada na cópia")
                # extração-ponte da Nuvem IPED: o card do destino é a linha de controle dela,
                # e sem a cópia dessa linha os dados chegariam sem card nenhum
                cur.execute("SELECT count(*) FROM investigacao.ipd_tb01_extracoes WHERE id_extracao = %s",
                            (id_origem,))
                if cur.fetchone()[0]:
                    cur.execute(
                        "SELECT cd_status FROM investigacao.ipd_tb01_extracoes WHERE id_extracao = %s",
                        (id_copia,))
                    linha = cur.fetchone()
                    conferir(linha is not None and linha[0] == "concluido",
                             "a linha da Nuvem IPED foi copiada e fechada como concluída")
    finally:
        # Apaga o INQUÉRITO inteiro, e não a extração: além de levar tudo (extração, entidades,
        # canônicas, vetores), é o caminho que já falhou aqui uma vez — apagar inquérito com
        # importação da Nuvem IPED esbarrava no sidecar de nuvem, que ficava de fora da limpeza.
        # Falha na limpeza é falha do teste.
        apagar_inquerito(id_inq_teste)
        print("Limpeza feita (cópia e inquérito de teste apagados).")

    print()
    if falhas:
        print(f"{len(falhas)} FALHA(S):")
        for f in falhas:
            print("  - " + f)
        sys.exit(1)
    print("Tudo certo.")


if __name__ == "__main__":
    main()

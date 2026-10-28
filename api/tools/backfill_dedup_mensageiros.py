"""Remove conversas e mensagens DUPLICADAS que o report.xml da Cellebrite reprocessou.

O que aconteceu (diagnóstico da extração 37): o report.xml repete o MESMO <Chat> 2-8 vezes
quando o Cellebrite reprocessa o mesmo banco em passes diferentes (Secure Folder de Samsung,
extração física). A importação antiga não tinha filtro: no mensageiro genérico cada cópia
virava uma conversa nova na tela (a mesma 5527997455680 aparecia 4x); no WhatsApp principal
o UNIQUE de chat_id fundia as conversas mas reinseria TODAS as mensagens (54% de 243.730).
A importação já filtra isso (funcoes/investigacao_utils.py, _filtrar_duplicatas) — esta
ferramenta conserta o que foi gravado antes dela.

O que a ferramenta faz, por extração afetada:
  1. funde conversas MSG_TB02 repetidas (mesma extração+app+nu_chat_id): o menor id_conversa
     sobrevive, mensagens/participantes/datas migram para ele, as cópias somem;
  2. apaga mensagens duplicadas DENTRO de cada conversa (tb04 das 3 famílias) — só cópia
     idêntica: mesmo identificador original E mesma data, corpo e remetente. Apps de saúde
     (Biome/KnowledgeC) repetem o identificador com conteúdo diferente e são preservados;
  3. apaga anexos órfãos (tb05 sem mensagem) dos duplicados removidos;
  4. refaz os contadores QT_* de INQ_TB02 a partir das tabelas.

Nada em ARQ_TB01/EML é tocado (e-mail e arquivos não têm o problema).

Uso, de dentro do contêiner da API:
    python /app/tools/backfill_dedup_mensageiros.py --listar
    python /app/tools/backfill_dedup_mensageiros.py --extracao 37 --dry-run
    python /app/tools/backfill_dedup_mensageiros.py --extracao 37
    python /app/tools/backfill_dedup_mensageiros.py --todas
"""

import argparse
import sys

sys.path.insert(0, "/app")

from funcoes.db_utils import get_connection  # noqa: E402

_FAMILIAS = ("wpp", "sms", "msg")

# contagem de duplicatas por extração (uma linha por família afetada) — mesma chave
# da deleção em _desduplicar_mensagens: ident + data + corpo + remetente
_SQL_SCAN = """
SELECT c.id_extracao, count(*) grupos, sum(n) - count(*) linhas
  FROM (SELECT id_conversa, nu_identificador_original, dt_mensagem,
               COALESCE(tx_mensagem, '') corpo, COALESCE(id_contato_remetente, -1) rem, count(*) n
          FROM investigacao.{fam}_tb04_mensagens
         WHERE nu_identificador_original IS NOT NULL
         GROUP BY 1, 2, 3, 4, 5
        HAVING count(*) > 1) d
  JOIN investigacao.{fam}_tb02_conversas c USING (id_conversa)
 WHERE c.id_extracao = %s
 GROUP BY 1
"""

# conversas MSG_TB02 repetidas (o mensageiro genérico não tem UNIQUE de chat_id)
_SQL_CONVERSAS_DUP = """
SELECT id_extracao, tp_app, nu_chat_id, min(id_conversa) vencedora,
       array_agg(id_conversa ORDER BY id_conversa) todas
  FROM investigacao.msg_tb02_conversas
 WHERE nu_chat_id IS NOT NULL AND id_extracao = %s
 GROUP BY 1, 2, 3
HAVING count(*) > 1
 ORDER BY 2, 3
"""


def _scan(cur, id_extracao: int):
    """(grupos, linhas_duplicadas) somando as 3 famílias de uma extração."""
    total_g = total_l = 0
    for fam in _FAMILIAS:
        cur.execute(_SQL_SCAN.format(fam=fam), (id_extracao,))
        for _, g, l in cur.fetchall():
            total_g += g
            total_l += l
    return total_g, total_l


def _mesclar_conversas_msg(cur, id_extracao: int) -> int:
    """Funde os clones de MSG_TB02 (mesma extração+app+chat_id) na conversa de menor id.
    As mensagens migram todas; a desduplicação de mensagens (passo 2) cuida das repetidas."""
    cur.execute(_SQL_CONVERSAS_DUP, (id_extracao,))
    grupos = cur.fetchall()
    fundidas = 0
    for _, app, chat_id, vencedora, todas in grupos:
        for perdedora in todas:
            if perdedora == vencedora:
                continue
            cur.execute(
                """
                UPDATE investigacao.msg_tb04_mensagens
                   SET id_conversa = %s
                 WHERE id_conversa = %s
                """,
                (vencedora, perdedora),
            )
            cur.execute(
                """
                INSERT INTO investigacao.msg_tb03_participantes (id_conversa, id_contato)
                SELECT %s, id_contato FROM investigacao.msg_tb03_participantes
                 WHERE id_conversa = %s
                ON CONFLICT (id_conversa, id_contato) DO NOTHING
                """,
                (vencedora, perdedora),
            )
            cur.execute(
                "DELETE FROM investigacao.msg_tb03_participantes WHERE id_conversa = %s",
                (perdedora,),
            )
            cur.execute(
                """
                UPDATE investigacao.msg_tb02_conversas v
                   SET dt_inicio = LEAST(v.dt_inicio, p.dt_inicio),
                       dt_ultima_atividade = GREATEST(v.dt_ultima_atividade, p.dt_ultima_atividade),
                       no_grupo = COALESCE(v.no_grupo, p.no_grupo)
                  FROM investigacao.msg_tb02_conversas p
                 WHERE v.id_conversa = %s AND p.id_conversa = %s
                """,
                (vencedora, perdedora),
            )
            cur.execute(
                "DELETE FROM investigacao.msg_tb02_conversas WHERE id_conversa = %s",
                (perdedora,),
            )
            fundidas += 1
        print(f"    fundidas 2+→1: {app} · {chat_id} (conversa {vencedora})")
    return fundidas


def _desduplicar_mensagens(cur, fam: str, id_extracao: int) -> tuple[int, int]:
    """Apaga cópias idênticas dentro da mesma conversa. Iden-ti-co: mesmo identificador
    original E mesma data, corpo e remetente — registro legítimo de app de saúde que
    repete o identificador com conteúdo diferente não é tocado. Devolve
    (mensagens apagadas, anexos apagados)."""
    escopo = f"""id_conversa IN (SELECT id_conversa FROM investigacao.{fam}_tb02_conversas
                                 WHERE id_extracao = {int(id_extracao)})"""
    tmp = f"_alvos_{fam}"
    cur.execute(f"DROP TABLE IF EXISTS {tmp}")
    cur.execute(f"""
        CREATE TEMP TABLE {tmp} ON COMMIT DROP AS
        SELECT id_mensagem FROM (
            SELECT id_mensagem,
                   row_number() OVER (
                       PARTITION BY id_conversa, nu_identificador_original,
                                    dt_mensagem, COALESCE(tx_mensagem, ''),
                                    COALESCE(id_contato_remetente, -1)
                       ORDER BY id_mensagem
                   ) rn
              FROM investigacao.{fam}_tb04_mensagens
             WHERE nu_identificador_original IS NOT NULL AND {escopo}
        ) x WHERE rn > 1
    """)
    cur.execute(f"SELECT count(*) FROM {tmp}")
    n_msgs = cur.fetchone()[0]
    if not n_msgs:
        cur.execute(f"DROP TABLE IF EXISTS {tmp}")
        return 0, 0
    cur.execute(f"""
        DELETE FROM investigacao.{fam}_tb05_anexos a
         WHERE a.id_mensagem IN (SELECT id_mensagem FROM {tmp})
    """)
    n_anexos = cur.rowcount
    cur.execute(f"""
        DELETE FROM investigacao.{fam}_tb04_mensagens m
         WHERE m.id_mensagem IN (SELECT id_mensagem FROM {tmp})
    """)
    n_alvo = cur.rowcount
    cur.execute(f"DROP TABLE IF EXISTS {tmp}")
    return n_alvo, n_anexos


def _repor_contadores(cur, id_extracao: int) -> None:
    cur.execute(
        """
        UPDATE investigacao.inq_tb02_extracoes e
           SET qt_conversas     = (SELECT count(*) FROM investigacao.wpp_tb02_conversas c
                                    WHERE c.id_extracao = e.id_extracao),
               qt_mensagens     = (SELECT count(*) FROM investigacao.wpp_tb04_mensagens m
                                    JOIN investigacao.wpp_tb02_conversas c USING (id_conversa)
                                    WHERE c.id_extracao = e.id_extracao),
               qt_sms_conversas = (SELECT count(*) FROM investigacao.sms_tb02_conversas c
                                    WHERE c.id_extracao = e.id_extracao),
               qt_sms_mensagens = (SELECT count(*) FROM investigacao.sms_tb04_mensagens m
                                    JOIN investigacao.sms_tb02_conversas c USING (id_conversa)
                                    WHERE c.id_extracao = e.id_extracao),
               qt_msg_conversas = (SELECT count(*) FROM investigacao.msg_tb02_conversas c
                                    WHERE c.id_extracao = e.id_extracao),
               qt_msg_mensagens = (SELECT count(*) FROM investigacao.msg_tb04_mensagens m
                                    JOIN investigacao.msg_tb02_conversas c USING (id_conversa)
                                    WHERE c.id_extracao = e.id_extracao)
         WHERE e.id_extracao = %s
        """,
        (id_extracao,),
    )


def _extracoes(cur) -> list[int]:
    cur.execute("SELECT id_extracao FROM investigacao.inq_tb02_extracoes ORDER BY 1")
    return [r[0] for r in cur.fetchall()]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extracao", type=int, help="id de INQ_TB02_EXTRACOES")
    parser.add_argument("--todas", action="store_true", help="percorre todas as extrações")
    parser.add_argument("--listar", action="store_true",
                        help="lista as extrações com duplicatas, sem apagar nada")
    parser.add_argument("--dry-run", action="store_true", help="conta, não apaga")
    args = parser.parse_args()

    if not args.listar and not args.extracao and not args.todas:
        parser.error("passe --listar, --extracao <id> ou --todas")

    with get_connection() as conn:
        with conn.cursor() as cur:
            alvos = [args.extracao] if args.extracao else _extracoes(cur)

            if args.listar:
                for id_ext in alvos:
                    grupos, linhas = _scan(cur, id_ext)
                    cur.execute(_SQL_CONVERSAS_DUP, (id_ext,))
                    convs_dup = sum(len(t) - 1 for _, _, _, _, t in cur.fetchall())
                    if grupos or convs_dup:
                        print(f"  extracao {id_ext:>4}: {grupos} msg duplicada(s) "
                              f"({linhas} linhas), {convs_dup} conversa(s) clonada(s)")
                return

            for id_ext in alvos:
                grupos, linhas = _scan(cur, id_ext)
                cur.execute(_SQL_CONVERSAS_DUP, (id_ext,))
                n_grupos = len(cur.fetchall())
                if not grupos and not n_grupos:
                    continue
                print(f"-- extração {id_ext}: {linhas} msg(ns) duplicada(s), "
                      f"{n_grupos} conversa(s) clonada(s)")
                if args.dry_run:
                    print("   ** dry-run: nada alterado")
                    continue
                fundidas = _mesclar_conversas_msg(cur, id_ext)
                apag_total = anex_total = 0
                for fam in _FAMILIAS:
                    apag, anex = _desduplicar_mensagens(cur, fam, id_ext)
                    if apag:
                        print(f"   {fam}: {apag} mensagem(ns) e {anex} anexo(s) removidos")
                    apag_total += apag
                    anex_total += anex
                _repor_contadores(cur, id_ext)
                conn.commit()
                print(f"   == ok: {fundidas} conversa(s) fundida(s), {apag_total} msg(ns), "
                      f"{anex_total} anexo(s) removidos; contadores refeitos")


if __name__ == "__main__":
    main()

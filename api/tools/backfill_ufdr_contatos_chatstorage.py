"""Backfill dos cartões de contato que o Cellebrite não copiou para o report.xml.

O cartão compartilhado enviado/encaminhado pelo WhatsApp Web mora na mídia da mensagem
no ChatStorage.sqlite (ZWAMEDIAITEM.ZVCARDNAME/ZVCARDSTRING) que o próprio .ufdr carrega,
e no report.xml o <model> da mensagem chega com SharedContacts VAZIO — a bolha saía como
"(sem conteúdo)" enquanto o Reader e o IPED mostravam o cartão (lêem as bases deles, não
o XML). Caso real: extração 57 do inquérito 196, mensagem 4017709. A importação foi
corrigida em ufdr_parser.contatos_compartilhados_chatstorage; este script completa as
extrações importadas antes da correção.

Casa só pela chave natural — NU_IDENTIFICADOR_ORIGINAL = ZSTANZAID, dentro das conversas
da extração — e SÓ PREENCHE LACUNA: nunca sobrescreve tx_mensagem que já tem texto.
Idempotente — rodar de novo não muda nada além do que ainda estiver vazio.

Uso, de dentro do contêiner da API:
    python /app/tools/backfill_ufdr_contatos_chatstorage.py --listar
    python /app/tools/backfill_ufdr_contatos_chatstorage.py --extracao 57 --dry-run
    python /app/tools/backfill_ufdr_contatos_chatstorage.py --extracao 57
    python /app/tools/backfill_ufdr_contatos_chatstorage.py --todas
"""

import argparse
import sys
sys.path.insert(0, "/app")

from funcoes import ufdr_zip                                      # noqa: E402
from funcoes.db_utils import get_connection                      # noqa: E402
from funcoes.investigacao_utils import (                          # noqa: E402
    _localizar_ufdr,
    _resolver_contatos_chatstorage,
)
from funcoes.ufdr_parser import contatos_compartilhados_chatstorage  # noqa: E402


def listar_extracoes(cur, apenas=None):
    """Extrações concluídas (id, nome do arquivo), opcionalmente filtradas por id."""
    sql = ("SELECT id_extracao, no_arquivo FROM investigacao.inq_tb02_extracoes "
           "WHERE cd_status = 'concluido' AND no_arquivo IS NOT NULL")
    params = ()
    if apenas:
        sql += " AND id_extracao = ANY(%s)"
        params = (list(apenas),)
    cur.execute(sql + " ORDER BY id_extracao", params)
    return cur.fetchall()


def filtrar_pendentes(cur, id_extracao, mapa):
    """Recorta o mapa ao que a extração ainda tem sem texto.

    O mapa inteiro (45 mil cartões num iPhone auditado) em qualquer ANY(...) vira varredura
    longa e morre no statement_timeout de produção; um SELECT simples das mensagens vazias
    + interseção em Python custa só uma passada na tabela de mensagens da extração."""
    cur.execute(
        """
        SELECT m.nu_identificador_original
          FROM investigacao.wpp_tb04_mensagens m,
               investigacao.wpp_tb02_conversas c
         WHERE c.id_conversa = m.id_conversa
           AND c.id_extracao = %s
           AND m.tx_mensagem IS NULL
           AND m.nu_identificador_original IS NOT NULL
        """,
        (id_extracao,),
    )
    vazias = {r[0] for r in cur.fetchall()}
    return {k: v for k, v in mapa.items() if k in vazias}


def backfill(id_extracao, no_arquivo, dry_run):
    caminho = _localizar_ufdr(no_arquivo)
    if caminho is None:
        print(f"  !! .ufdr não encontrado em data/: {no_arquivo} — pulando")
        return

    with ufdr_zip.abrir(caminho) as zf:
        mapa = contatos_compartilhados_chatstorage(zf)
    if not mapa:
        print("  sem ChatStorage.sqlite com cartões no .ufdr — nada a fazer")
        return
    print(f"  cartões no ChatStorage: {len(mapa)}", flush=True)

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = 0")  # varredura de mensagens grandes é esperada
            mapa = filtrar_pendentes(cur, id_extracao, mapa)
            if not mapa:
                print("  nenhuma mensagem sem texto coberta por cartão — nada a fazer")
                return
            if dry_run:
                print(f"  [DRY-RUN] mensagens sem texto que seriam preenchidas: {len(mapa)}")
                conn.rollback()
                return
            atualizadas = _resolver_contatos_chatstorage(cur, id_extracao, mapa)
        conn.commit()
    print(f"  mensagens com cartão recuperado: {atualizadas}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--extracao", type=int, action="append",
                    help="id da extração (pode repetir); sem isto, use --todas")
    ap.add_argument("--todas", action="store_true", help="processa todas as extrações concluídas")
    ap.add_argument("--listar", action="store_true", help="só lista as extrações e sai")
    ap.add_argument("--dry-run", action="store_true",
                    help="percorre tudo e mostra o que mudaria, sem gravar")
    args = ap.parse_args()

    with get_connection() as conn:
        with conn.cursor() as cur:
            alvos = listar_extracoes(cur, None if args.todas or args.listar else args.extracao)

    if args.listar:
        for id_extracao, no_arquivo in alvos:
            print(f"{id_extracao}\t{no_arquivo}")
        return
    if not alvos:
        print("Nada a fazer: informe --extracao N ou --todas.")
        return

    for id_extracao, no_arquivo in alvos:
        print(f"\n=== extração {id_extracao} — {no_arquivo} ===", flush=True)
        try:
            backfill(id_extracao, no_arquivo, args.dry_run)
        except Exception as exc:  # uma extração problemática não derruba as demais
            print(f"  !! erro: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()

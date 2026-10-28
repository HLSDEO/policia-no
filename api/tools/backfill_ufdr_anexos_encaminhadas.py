"""Backfill das extrações UFDR importadas antes das correções do parser.

Corrige, SEM reimportar, as lacunas que os parsers antigos deixavam:

1. tx_mensagem NULL em mensagem ENCAMINHADA — o texto fica no EmbeddedInstantMessage
   e era descartado (commit 590f7e1, ver ufdr_parser._corpo_mensagem).
2. co_caminho_zip vazio em anexo — o parser antigo só casava o binário por tamanho
   exato e desistia em qualquer colisão; agora usa o attachment_extracted_path
   gravado pelo Cellebrite (ver ufdr_parser._extrair_anexos).
3. tx_mensagem NULL em mensagem que COMPARTILHA CONTATO — o cartão (nome, telefone,
   e-mail, ID do app) fica em SharedContacts e a mensagem não tem Body nenhum, então
   a bolha aparecia como "(sem conteúdo)". Vale para o cartão enviado direto e para o
   ENCAMINHADO, em que o SharedContacts fica só no EmbeddedInstantMessage
   (ver ufdr_parser._texto_contatos_compartilhados).

Faz UM passe pelo report.xml de cada UFDR e SÓ PREENCHE LACUNA: nunca sobrescreve
tx_mensagem que já tem texto, nem co_caminho_zip que já aponta para algo. Idempotente —
rodar de novo não muda nada além do que ainda estiver vazio.

Como cada linha do banco é reencontrada no XML (chave por família de tabelas):
  wpp  (id_conversa via nu_whatsapp_chat_id, nu_identificador_original)
  msg  (id_conversa via tp_app + nu_chat_id, nu_identificador_original)
  sms  (id_extracao, nu_identificador_original) — SMS_TB02 não guarda id de chat,
       mas essa chave não tem nenhuma colisão no banco (conferido).
Mensagem sem nu_identificador_original, ou em conversa que o XML não reencontra, fica
de fora — o script informa o total no fim.

Uso, de dentro do contêiner da API:
    python /app/tools/backfill_ufdr_anexos_encaminhadas.py --listar
    python /app/tools/backfill_ufdr_anexos_encaminhadas.py --extracao 11 --dry-run
    python /app/tools/backfill_ufdr_anexos_encaminhadas.py --extracao 11
    python /app/tools/backfill_ufdr_anexos_encaminhadas.py --todas
"""

import argparse
import sys
import time
sys.path.insert(0, "/app")

from funcoes import ufdr_zip                                      # noqa: E402
from funcoes.db_utils import get_connection                      # noqa: E402
from funcoes.investigacao_utils import _localizar_ufdr           # noqa: E402
from funcoes.ufdr_parser import (                                # noqa: E402
    APP_DESCONHECIDO,
    FONTE_SMS,
    FONTE_WHATSAPP,
    indexar_indicadores,
    iterar_registros,
)

TABELAS = {
    "wpp": {"msgs": "wpp_tb04_mensagens", "anexos": "wpp_tb05_anexos"},
    "sms": {"msgs": "sms_tb04_mensagens", "anexos": "sms_tb05_anexos"},
    "msg": {"msgs": "msg_tb04_mensagens", "anexos": "msg_tb05_anexos"},
}

LOTE = 5000


def familia_da_fonte(fonte):
    """Roteia o Source do Chat para a família de tabelas, igual à ingestão."""
    if fonte == FONTE_WHATSAPP:
        return "wpp"
    if fonte == FONTE_SMS:
        return "sms"
    return "msg"


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


def mapa_conversas(cur, id_extracao):
    """Chaves do XML -> id_conversa. wpp: chat_id; msg: (app, chat_id). SMS não entra
    (casa direto por id_extracao, ver aplicar_*)."""
    mapa_wpp, mapa_msg = {}, {}
    cur.execute(
        "SELECT nu_whatsapp_chat_id, id_conversa FROM investigacao.wpp_tb02_conversas "
        "WHERE id_extracao = %s AND nu_whatsapp_chat_id IS NOT NULL", (id_extracao,))
    for chat_id, id_conversa in cur.fetchall():
        mapa_wpp[chat_id] = id_conversa
    cur.execute(
        "SELECT tp_app, nu_chat_id, id_conversa FROM investigacao.msg_tb02_conversas "
        "WHERE id_extracao = %s AND nu_chat_id IS NOT NULL", (id_extracao,))
    for app, chat_id, id_conversa in cur.fetchall():
        mapa_msg[(app, chat_id)] = id_conversa
    return mapa_wpp, mapa_msg


def aplicar_corpos(cur, fam, linhas, id_extracao):
    """Preenche tx_mensagem SÓ onde está NULL. Devolve quantas linhas mudaram.
    linhas: (chave, ident, corpo) — chave é id_conversa (wpp/msg) e ignorada no sms."""
    if not linhas:
        return 0
    t = TABELAS[fam]
    if fam == "sms":
        cur.execute(
            f"""
            UPDATE investigacao.{t['msgs']} m
               SET tx_mensagem = v.corpo
            FROM (SELECT * FROM unnest(%s::text[], %s::text[]) AS x(ident, corpo)) v,
                 investigacao.sms_tb02_conversas c
            WHERE c.id_conversa = m.id_conversa
              AND c.id_extracao = %s
              AND m.nu_identificador_original = v.ident
              AND m.tx_mensagem IS NULL
            """,
            ([l[1] for l in linhas], [l[2] for l in linhas], id_extracao),
        )
    else:
        cur.execute(
            f"""
            UPDATE investigacao.{t['msgs']} m
               SET tx_mensagem = v.corpo
            FROM (SELECT * FROM unnest(%s::bigint[], %s::text[], %s::text[])
                           AS x(id_conversa, ident, corpo)) v
            WHERE m.id_conversa = v.id_conversa
              AND m.nu_identificador_original = v.ident
              AND m.tx_mensagem IS NULL
            """,
            ([l[0] for l in linhas], [l[1] for l in linhas], [l[2] for l in linhas]),
        )
    return cur.rowcount


def aplicar_anexos(cur, fam, linhas, id_extracao):
    """Preenche co_caminho_zip SÓ onde está vazio. Devolve quantas linhas mudaram.
    linhas: (chave, ident, nome_arquivo, caminho_zip)."""
    if not linhas:
        return 0
    t = TABELAS[fam]
    if fam == "sms":
        cur.execute(
            f"""
            UPDATE investigacao.{t['anexos']} a
               SET co_caminho_zip = v.caminho
            FROM (SELECT * FROM unnest(%s::text[], %s::text[], %s::text[])
                           AS x(ident, nome, caminho)) v,
                 investigacao.{t['msgs']} m,
                 investigacao.sms_tb02_conversas c
            WHERE c.id_conversa = m.id_conversa
              AND c.id_extracao = %s
              AND m.nu_identificador_original = v.ident
              AND a.id_mensagem = m.id_mensagem
              AND a.no_arquivo IS NOT DISTINCT FROM v.nome
              AND (a.co_caminho_zip IS NULL OR a.co_caminho_zip = '')
            """,
            ([l[1] for l in linhas], [l[2] for l in linhas], [l[3] for l in linhas], id_extracao),
        )
    else:
        cur.execute(
            f"""
            UPDATE investigacao.{t['anexos']} a
               SET co_caminho_zip = v.caminho
            FROM (SELECT * FROM unnest(%s::bigint[], %s::text[], %s::text[], %s::text[])
                           AS x(id_conversa, ident, nome, caminho)) v,
                 investigacao.{t['msgs']} m
            WHERE m.id_conversa = v.id_conversa
              AND m.nu_identificador_original = v.ident
              AND a.id_mensagem = m.id_mensagem
              AND a.no_arquivo IS NOT DISTINCT FROM v.nome
              AND (a.co_caminho_zip IS NULL OR a.co_caminho_zip = '')
            """,
            ([l[0] for l in linhas], [l[1] for l in linhas],
             [l[2] for l in linhas], [l[3] for l in linhas]),
        )
    return cur.rowcount


def backfill(id_extracao, no_arquivo, dry_run):
    caminho = _localizar_ufdr(no_arquivo)
    if caminho is None:
        print(f"  !! .ufdr não encontrado em data/: {no_arquivo} — pulando")
        return
    print(f"  arquivo: {caminho}", flush=True)

    inicio = time.time()
    upd_corpo = upd_anexo = 0
    sem_ident = chats_sem_par = 0

    with get_connection() as conn:
        with conn.cursor() as cur:
            mapa_wpp, mapa_msg = mapa_conversas(cur, id_extracao)
            print(f"  conversas no banco: wpp={len(mapa_wpp)} msg={len(mapa_msg)}", flush=True)

            with ufdr_zip.abrir(caminho) as zf:
                indice, _fotos, caminhos_zip, contagem_tamanhos = indexar_indicadores(zf=zf)
                print(f"  arquivos no zip: {len(caminhos_zip)}", flush=True)

                pend_corpo = {f: [] for f in TABELAS}
                pend_anexo = {f: [] for f in TABELAS}

                def descarrega(forcar=False):
                    nonlocal upd_corpo, upd_anexo
                    for f in TABELAS:
                        if pend_corpo[f] and (forcar or len(pend_corpo[f]) >= LOTE):
                            if not dry_run:
                                upd_corpo += aplicar_corpos(cur, f, pend_corpo[f], id_extracao)
                            pend_corpo[f].clear()
                        if pend_anexo[f] and (forcar or len(pend_anexo[f]) >= LOTE):
                            if not dry_run:
                                upd_anexo += aplicar_anexos(cur, f, pend_anexo[f], id_extracao)
                            pend_anexo[f].clear()

                progresso = {}
                pct_ant = -1
                for tipo, dados in iterar_registros(indice_binarios=indice,
                                                    caminhos_zip=caminhos_zip,
                                                    contagem_tamanhos=contagem_tamanhos, zf=zf,
                                                    progresso=progresso):
                    if progresso.get("total"):
                        pct = int(progresso["lidos"] * 100 / progresso["total"])
                        if pct != pct_ant and pct % 10 == 0:
                            pct_ant = pct
                            print(f"    {pct}% do report.xml", flush=True)
                    if tipo != "chat":
                        continue
                    fonte, chat = dados
                    fam = familia_da_fonte(fonte)
                    chat_id = chat.get("chat_id")
                    if fam == "wpp":
                        chave = mapa_wpp.get(chat_id)
                    elif fam == "msg":
                        chave = mapa_msg.get((fonte or APP_DESCONHECIDO, chat_id))
                    else:
                        chave = id_extracao  # sms casa por extração, não por conversa
                    if chave is None:
                        chats_sem_par += 1
                        continue
                    for m in chat["mensagens"]:
                        ident = m.get("identificador_original")
                        if not ident:
                            sem_ident += 1
                            continue
                        if m.get("corpo"):
                            pend_corpo[fam].append((chave, ident, m["corpo"]))
                        for a in m.get("anexos") or []:
                            if a.get("caminho_zip"):
                                pend_anexo[fam].append(
                                    (chave, ident, a.get("nome_arquivo"), a["caminho_zip"]))
                    descarrega()

                descarrega(forcar=True)

        if dry_run:
            conn.rollback()
        else:
            conn.commit()

    dur = time.time() - inicio
    modo = "[DRY-RUN, nada gravado] " if dry_run else ""
    print(f"  {modo}mensagens com texto recuperado: {upd_corpo} | "
          f"anexos com binário recuperado: {upd_anexo}")
    print(f"  msgs do XML sem identificador (não casáveis): {sem_ident} | "
          f"chats sem par no banco: {chats_sem_par}")
    print(f"  tempo: {dur / 60:.1f} min", flush=True)


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

"""Normaliza os cartoes de PERFIL compartilhado (Facebook/Messenger) para um rotulo honesto.

O Cellebrite modela como ``Contact Type=Shared`` tambem o cartao de PERFIL compartilhado no
Messenger: sem Name e com uma unica entrada de ID do app (Category "Facebook Id", Domain
"User ID"). Na tela a bolha dizia "Contato compartilhado: Facebook Id: 1186056656797238", mas
o extrato NAO traz nem o nome nem a foto desse perfil (auditado em producao, extracao 11: a
tabela attachments do fb-msys nao tem a mensagem nem attachment_fbid igual ao ID; report.xml
traz Attachments/Photos/EmbeddedInstantMessage vazios). Nao ha imagem a exibir — e so um ID de
perfil. A importacao foi corrigida em ufdr_parser._texto_contatos_compartilhados
(PREFIXO_PERFIL_COMPARTILHADO = "Perfil compartilhado:"); este script regrava o que ja foi
importado com um dos rotulos antigos.

Regra (a mesma do parser): a linha comeca com um dos prefixos antigos SEM nome depois dos
dois-pontos e TODAS as linhas de entrada abaixo sao IDs de app (categoria contem "id" —
"Facebook Id", "WhatsApp User Id", "User ID"…). Cartao de verdade (tem nome, telefone ou
e-mail) nao e tocado. A troca so mexe no prefixo: as linhas de ID continuam la, pesquisaveis.
Idempotente — o texto novo ("Perfil compartilhado:") nao casa mais nos padroes antigos.

Uso, de dentro do contêiner da API:
    python /app/tools/backfill_cartao_compartilhado_messenger.py --todas --dry-run
    python /app/tools/backfill_cartao_compartilhado_messenger.py --extracao 11
    python /app/tools/backfill_cartao_compartilhado_messenger.py --todas
"""

import argparse
import sys
sys.path.insert(0, "/app")

from funcoes.db_utils import get_connection                      # noqa: E402
from funcoes.ufdr_parser import PREFIXO_PERFIL_COMPARTILHADO      # noqa: E402

# Prefixos que este script normaliza para PREFIXO_PERFIL_COMPARTILHADO:
#  - "Contato compartilhado:"  -> importacao anterior ao parser corrigido (cartão de perfil
#    sem nome gravada como se fosse contato digitado);
#  - "Cartao compartilhado (com foto):" -> janela curta em que o parser rotulou assim antes de
#    se constatar (no fb-msys + report.xml) que o extrato nao traz foto nenhuma desses perfis.
_PREFIXOS_ANTIGOS = (
    "Contato compartilhado:",
    "Cartão compartilhado (com foto):",
)

# conversas -> mensagens dos mensageiros que saem do report.xml (wpp e msg; SMS nao tem
# SharedContacts)
_TABELAS = (
    ("wpp", "investigacao.wpp_tb04_mensagens", "investigacao.wpp_tb02_conversas"),
    ("msg", "investigacao.msg_tb04_mensagens", "investigacao.msg_tb02_conversas"),
)


def _reclassificar(texto):
    """Novo texto quando o cartão é só ID de app sem nome; None caso contrário."""
    if not texto:
        return None
    resto = None
    for antigo in _PREFIXOS_ANTIGOS:
        if texto.startswith(antigo):
            resto = texto[len(antigo):]
            break
    if resto is None:
        return None
    primeira, _, cauda = resto.partition("\n")
    if primeira.strip():
        return None  # tem nome: e um cartao de contato como outro qualquer
    linhas = [l for l in cauda.split("\n") if l.strip()]
    if not linhas:
        return None
    if not all("id" in l.split(":")[0].lower() for l in linhas):
        return None  # tem telefone/e-mail: contato de verdade
    return PREFIXO_PERFIL_COMPARTILHADO + resto


def corrigir_tabela(cur, app, tabela_msg, tabela_conv, extracoes):
    """Troca o prefixo nos cartões só-com-ID da tabela. Retorna quantas mensagens mudaram."""
    condicoes = " OR ".join(["m.tx_mensagem LIKE %s"] * len(_PREFIXOS_ANTIGOS))
    sql = (f"""
        SELECT m.id_mensagem, m.tx_mensagem
          FROM {tabela_msg} m
          JOIN {tabela_conv} c ON c.id_conversa = m.id_conversa
         WHERE ({condicoes})
    """)
    params = [f"{antigo}\n%" for antigo in _PREFIXOS_ANTIGOS]
    if extracoes:
        sql += " AND c.id_extracao = ANY(%s)"
        params.append(list(extracoes))
    cur.execute(sql + " ORDER BY m.id_mensagem", tuple(params))
    alvos = [(mid, novo) for mid, txt in cur.fetchall() if (novo := _reclassificar(txt))]
    if alvos:
        cur.executemany(
            f"UPDATE {tabela_msg} SET tx_mensagem = %s WHERE id_mensagem = %s",
            [(novo, mid) for mid, novo in alvos],
        )
    return len(alvos)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--extracao", type=int, action="append",
                    help="id da extração (pode repetir); sem isto, use --todas")
    ap.add_argument("--todas", action="store_true", help="todas as extrações")
    ap.add_argument("--dry-run", action="store_true",
                    help="mostra o que mudaria, sem gravar")
    args = ap.parse_args()
    if not args.todas and not args.extracao:
        print("Nada a fazer: informe --extracao N ou --todas.")
        return

    total = 0
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = 0")
            for app, tabela_msg, tabela_conv in _TABELAS:
                n = corrigir_tabela(cur, app, tabela_msg, tabela_conv, args.extracao)
                total += n
                print(f"  {app}: {n} mensagens reclassificadas"
                      + (" [dry-run, nada gravado]" if args.dry_run else ""))
            if args.dry_run:
                conn.rollback()
            else:
                conn.commit()
    print(f"total: {total}{' (dry-run)' if args.dry_run else ''}")


if __name__ == "__main__":
    main()

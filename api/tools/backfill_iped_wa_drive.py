"""Reconstroi a "conversa-mídia" do WhatsApp no backup do Google Drive para extrações IPED
importadas ANTES do recurso (funcoes/iped_wa_drive.py).

O que a ferramenta faz: varre o índice Lucene do caso (mesmo caminho da importação), coleta as
mídias do WhatsApp em ``Drive Mobile Backups/wa_<numero>/`` (IMG/VID/AUD/PTT/DOC/STK com o nome
determinístico ``AAAAMMDD-WAnnnn``) e grava UMA conversa em MSG_TB02 por conta (tp_app
"WhatsApp (backup Google Drive)"), com uma mensagem por mídia datada pelo nome e o binário no
anexo (ponteiro IPED::, o mesmo da tela Arquivos). NADA é recriado em ARQ_TB01 — as mídias já
estão lá; aqui só enriquece a tela de conversas com a linha do tempo.

Limitação herdada do formato (documentada em iped_wa_drive): o backup do Drive NÃO preserva o
contato de cada mídia (banco .crypt14 criptografado; metadado do Drive sem JID) — por isso a
conversa é da CONTA, não por contato. Não inventa remetente.

Idempotente: conversa da conta já existente (mesmo tp_app e nu_chat_id) é pulada.

Uso, de dentro do contêiner da API:
    python /app/tools/backfill_iped_wa_drive.py --listar
    python /app/tools/backfill_iped_wa_drive.py --extracao 4 --dry-run
    python /app/tools/backfill_iped_wa_drive.py --extracao 4
    python /app/tools/backfill_iped_wa_drive.py --todas
"""

import argparse
import json
import subprocess
import sys

sys.path.insert(0, "/app")

from funcoes.db_utils import get_connection                       # noqa: E402
from funcoes.iped_utils import (                                   # noqa: E402
    _classpath_dumper,
    _JAVA_BIN,
    _pasta_modulo_iped,
)
from funcoes.iped_wa_drive import APP, Colecao                     # noqa: E402
from funcoes.organizacao_arquivos import resolver as resolver_em_dados  # noqa: E402


def listar_extracoes(cur, id_extracao: int | None) -> list[dict]:
    sql = """SELECT i.id_extracao_iped, i.id_extracao, i.no_pasta, i.no_conta
               FROM investigacao.ipd_tb01_extracoes i
              WHERE i.cd_status = 'concluido' AND i.id_extracao IS NOT NULL"""
    params: list = []
    if id_extracao is not None:
        sql += " AND i.id_extracao_iped = %s"
        params.append(id_extracao)
    cur.execute(sql + " ORDER BY i.id_extracao_iped", params)
    colunas = ["id_extracao_iped", "id_extracao", "no_pasta", "no_conta"]
    return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


def processar(ext: dict, dry_run: bool) -> None:
    pasta = resolver_em_dados(ext["no_pasta"], pasta=True)
    if pasta is None or not pasta.exists():
        print(f"  !! pasta do caso não encontrada em data/: {ext['no_pasta']} — pulando")
        return
    pasta_modulo = _pasta_modulo_iped(pasta)
    if pasta_modulo is None:
        print(f"  !! {ext['no_pasta']} não tem módulo IPED (index/) — pulando")
        return

    colecao = Colecao(ext["id_extracao"])
    proc = subprocess.Popen(
        [_JAVA_BIN, "-cp", _classpath_dumper(pasta_modulo), "IpedDumper", str(pasta_modulo)],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1,
    )
    try:
        for linha in proc.stdout:
            try:
                campos = json.loads(linha)["fields"]
            except (json.JSONDecodeError, KeyError):
                continue
            colecao.coletar(campos)
    finally:
        proc.kill()

    contas, midias = colecao.resumo()
    if colecao.vazia:
        print("  nenhuma mídia de WhatsApp no backup do Drive — nada a fazer")
        return
    print(f"  {midias} mídia(s) de WhatsApp no backup do Drive, em {contas} conta(s)")
    if dry_run:
        print("  ** dry-run: nenhuma gravação feita")
        return
    with get_connection() as conn:
        with conn.cursor() as cur:
            contagens = colecao.gravar(cur)
        conn.commit()
    print(f"  == {APP}: {contagens['wa_midia_conversas']} conversa(s), "
          f"{contagens['wa_midia_mensagens']} mensagem(ns) com mídia")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extracao", type=int,
                        help="id_extracao_iped (ID da tabela IPD_TB01_EXTRACOES)")
    parser.add_argument("--todas", action="store_true",
                        help="percorre todas as extrações IPED concluídas")
    parser.add_argument("--listar", action="store_true",
                        help="lista as extrações IPED disponíveis")
    parser.add_argument("--dry-run", action="store_true",
                        help="conta as mídias sem gravar nada")
    args = parser.parse_args()

    if args.listar:
        with get_connection() as conn:
            with conn.cursor() as cur:
                for ext in listar_extracoes(cur, None):
                    print(f"  id_extracao_iped={ext['id_extracao_iped']:>4}  "
                          f"ponte={ext['id_extracao']}  {ext['no_pasta']}  ({ext['no_conta']})")
        return
    if not args.extracao and not args.todas:
        parser.error("passe --extracao <id>, --todas ou --listar")

    with get_connection() as conn:
        with conn.cursor() as cur:
            alvos = listar_extracoes(cur, args.extracao)
    if not alvos:
        print("nenhuma extração IPED concluída com esse ID")
        return
    for ext in alvos:
        print(f"-- extração IPED {ext['id_extracao_iped']} (ponte {ext['id_extracao']}) "
              f"· {ext['no_pasta']}")
        processar(ext, args.dry_run)


if __name__ == "__main__":
    main()

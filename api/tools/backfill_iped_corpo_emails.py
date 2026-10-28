"""Backfill do corpo INTEGRAL dos e-mails de extrações IPED importadas com prévia no lugar.

O IPED grava o `Message-Body` no índice só até ~132 caracteres — o que a importação gravava em
TX_CORPO era essa prévia, e o texto integral (o que a tela do próprio IPED mostra) só existe no
blob .eml do storage do caso. Desde a correção em iped_utils._email_de_item/_corpo_parece_previa
a importação nova já lê o blob quando o índice é prévia; esta ferramenta refaz o mesmo caminho
nas extrações importadas ANTES da correção, SEM reimportar e SEM trocar ID: é só UPDATE de
TX_CORPO (e TX_ANEXOS, se faltarem) na linha existente — as anotações do analista apontam para
os IDs atuais e continuam valendo.

Como cada linha é reencontrada: pela chave natural `NU_MENSAGEM` (o Message-ID do cabeçalho,
que só o IPED preenche e é único por mensagem dentro da caixa). Linha sem NU_MENSAGEM fica
intocada — não há como casá-la com segurança com o item do índice.

Idempotente: só escreve quando o corpo lido do .eml é MAIS LONGO que o do banco; rodar de novo
não muda nada.

Uso, de dentro do contêiner da API:
    python /app/tools/backfill_iped_corpo_emails.py --listar
    python /app/tools/backfill_iped_corpo_emails.py --extracao 38 --dry-run
    python /app/tools/backfill_iped_corpo_emails.py --extracao 38
    python /app/tools/backfill_iped_corpo_emails.py --todas
"""

import argparse
import json
import subprocess
import sys
import time

sys.path.insert(0, "/app")

from funcoes.db_utils import get_connection                        # noqa: E402
from funcoes.iped_utils import (                                    # noqa: E402
    _classpath_dumper,
    _corpo_e_anexos_do_eml,
    _corpo_parece_previa,
    _JAVA_BIN,
    _pasta_modulo_iped,
    _valor,
)
from funcoes.organizacao_arquivos import resolver as resolver_em_dados  # noqa: E402

LOTE = 500  # commits parciais: uma queda no meio não perde o que já foi atualizado


def listar_extracoes(cur, id_extracao: int | None) -> list[dict]:
    sql = """SELECT i.id_extracao_iped, i.id_extracao, i.no_pasta, i.no_conta, i.qt_emails
             FROM investigacao.ipd_tb01_extracoes i
             WHERE i.cd_status = 'concluido' AND i.id_extracao IS NOT NULL"""
    params: list = []
    if id_extracao is not None:
        sql += " AND i.id_extracao_iped = %s"
        params.append(id_extracao)
    cur.execute(sql + " ORDER BY i.id_extracao_iped", params)
    colunas = ["id_extracao_iped", "id_extracao", "no_pasta", "no_conta", "qt_emails"]
    return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


def carregar_linhas(cur, id_extracao_ponte: int) -> dict[str, tuple[int, int]]:
    """Message-ID -> (id_email, tamanho atual do corpo). Sem Message-ID não há chave: fica
    fora (ver docstring). Repetido: vale a primeira linha (a mais antiga)."""
    cur.execute(
        """SELECT nu_mensagem, id_email, coalesce(length(tx_corpo), 0)
             FROM investigacao.eml_tb01_emails
            WHERE id_extracao = %s AND nu_mensagem IS NOT NULL
            ORDER BY id_email""",
        (id_extracao_ponte,),
    )
    mapa: dict[str, tuple[int, int]] = {}
    for msgid, id_email, tam in cur.fetchall():
        mapa.setdefault(msgid, (id_email, tam))
    return mapa


def atualizar_extracao(ext: dict, dry_run: bool) -> None:
    pasta = resolver_em_dados(ext["no_pasta"], pasta=True)
    if pasta is None or not pasta.exists():
        print(f"  !! pasta do caso não encontrada em data/: {ext['no_pasta']} — pulando")
        return
    pasta_modulo = _pasta_modulo_iped(pasta)
    if pasta_modulo is None:
        print(f"  !! {ext['no_pasta']} não tem módulo IPED (index/) — pulando")
        return

    with get_connection() as conn:
        with conn.cursor() as cur:
            linhas = carregar_linhas(cur, ext["id_extracao"])
    print(f"  {len(linhas):,} linhas com Message-ID no banco")

    proc = subprocess.Popen(
        [_JAVA_BIN, "-cp", _classpath_dumper(pasta_modulo), "IpedDumper", str(pasta_modulo)],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1,
    )
    atualizados = ja_integros = sem_blob = 0
    pendentes: list[tuple[str, str, int]] = []
    t0 = time.time()

    def flush() -> None:
        if not pendentes:
            return
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    """UPDATE investigacao.eml_tb01_emails
                          SET tx_corpo = %s, tx_anexos = COALESCE(tx_anexos, %s)
                        WHERE id_email = %s""",
                    [(corpo, anexos, id_email) for corpo, anexos, id_email in pendentes],
                )
            conn.commit()
        pendentes.clear()

    try:
        for linha in proc.stdout:
            try:
                campos = json.loads(linha)["fields"]
            except (json.JSONDecodeError, KeyError):
                continue
            if _valor(campos, "category") != "Emails":
                continue
            msgid = _valor(campos, "Message:Raw-Header:Message-ID")
            alvo = linhas.get(msgid) if msgid else None
            if alvo is None:
                continue
            id_email, tam_atual = alvo
            corpo_indice = _valor(campos, "Message-Body")
            # só paga a leitura do blob quando o índice era prévia E o banco continua com o
            # tamanho do índice — corpo já maior que a prévia significa backfill já feito
            if not _corpo_parece_previa(corpo_indice, _valor(campos, "textSize")) or \
                    tam_atual > len(corpo_indice or "") + 64:
                ja_integros += 1
                continue
            corpo, anexos = _corpo_e_anexos_do_eml(pasta_modulo, campos)
            if not corpo or len(corpo) <= tam_atual:
                sem_blob += 1
                continue
            if dry_run:
                print(f"  id {id_email}: {tam_atual} -> {len(corpo)} caracteres")
            else:
                pendentes.append((corpo, anexos, id_email))
                if len(pendentes) >= LOTE:
                    flush()
                    print(f"  {atualizados + sem_blob + ja_integros:,} itens processados, "
                          f"{atualizados:,} atualizados...")
            atualizados += 1
    finally:
        if not dry_run:
            flush()
        proc.kill()

    print(f"  == {ext['no_pasta']}: {atualizados:,} corps restaurados, "
          f"{ja_integros:,} já íntegros, {sem_blob:,} sem blob maior "
          f"({time.time() - t0:.0f}s){' [dry-run]' if dry_run else ''}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    grupo = ap.add_mutually_exclusive_group(required=True)
    grupo.add_argument("--listar", action="store_true", help="lista as extrações IPED")
    grupo.add_argument("--extracao", type=int, help="id_extracao_iped de uma extração")
    grupo.add_argument("--todas", action="store_true", help="todas as concluídas")
    ap.add_argument("--dry-run", action="store_true", help="mostra o que mudaria, sem gravar")
    args = ap.parse_args()

    if args.listar:
        with get_connection() as conn:
            with conn.cursor() as cur:
                for ext in listar_extracoes(cur, None):
                    print(f"{ext['id_extracao_iped']:>3}  ponte {ext['id_extracao']:>3}  "
                          f"{ext['qt_emails'] or 0:>6,} e-mails  {ext['no_pasta']}")
        return

    with get_connection() as conn:
        with conn.cursor() as cur:
            alvo = listar_extracoes(cur, None if args.todas else args.extracao)
    if not alvo:
        print("Nenhuma extração IPED concluída com esse filtro.")
        return
    for ext in alvo:
        print(f"== extração IPED {ext['id_extracao_iped']} (ponte {ext['id_extracao']}): "
              f"{ext['no_pasta']}")
        atualizar_extracao(ext, args.dry_run)


if __name__ == "__main__":
    main()

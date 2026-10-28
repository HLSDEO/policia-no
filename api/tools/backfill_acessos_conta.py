"""Backfill das sessões de conta na nuvem (RemoteServiceSession) do Cellebrite.

As extrações importadas antes do handler de `<model type="RemoteServiceSession">` não
gravaram nada em ACS_TB01_ACESSOS — as sessões de login (Facebook/Instagram/Google, com IP,
user-agent, tipo de dispositivo e o PERÍODO da sessão) existiam no report.xml mas caíam no
balde dos tipos ignorados. Este tool relê o report.xml de cada extração e grava só essas
sessões, SEM reimportar e SEM tocar em nenhum outro dado.

Idempotente: apaga as linhas daquela extração gravadas por este caminho
(`no_fonte LIKE 'Cellebrite (%'`) e reinsere, então rodar de novo não duplica. No fim
recalcula QT_ACESSOS em INQ_TB02_EXTRACOES com a contagem real da tabela.

Uso, de dentro do contêiner da API (rode depois de a extração concluir):
    python /app/tools/backfill_acessos_conta.py --listar
    python /app/tools/backfill_acessos_conta.py --extracao 47 --dry-run
    python /app/tools/backfill_acessos_conta.py --extracao 47 --extracao 51
"""

import argparse
import sys

sys.path.insert(0, "/app")

from funcoes import ufdr_zip                                        # noqa: E402
from funcoes.contas_nuvem_utils import _gravar_acesso_conta         # noqa: E402
from funcoes.db_utils import get_connection                         # noqa: E402
from funcoes.investigacao_utils import _localizar_ufdr              # noqa: E402
from funcoes.ufdr_parser import indexar_indicadores, iterar_registros  # noqa: E402

_FONTES_CELLEBRITE = "no_fonte LIKE 'Cellebrite (%%'"


def _extracoes_alvo(ids):
    with get_connection() as conn:
        with conn.cursor() as cur:
            if ids:
                cur.execute(
                    "SELECT id_extracao, no_arquivo FROM investigacao.inq_tb02_extracoes "
                    "WHERE id_extracao = ANY(%s) ORDER BY id_extracao", (ids,))
            else:
                cur.execute(
                    "SELECT id_extracao, no_arquivo FROM investigacao.inq_tb02_extracoes "
                    "WHERE cd_status = 'concluido' AND lower(no_arquivo) LIKE '%%.ufdr' "
                    "ORDER BY id_extracao")
            return cur.fetchall()


def _sessoes_do_ufdr(caminho):
    """Relê o report.xml e devolve só os dicts de acesso (RemoteServiceSession)."""
    sessoes = []
    with ufdr_zip.abrir(caminho) as zf:
        if "report.xml" not in zf.namelist():
            return sessoes
        indice, _fotos, caminhos, contagem = indexar_indicadores(zf=zf)
        for tipo, dados in iterar_registros(indice_binarios=indice, caminhos_zip=caminhos,
                                            contagem_tamanhos=contagem, zf=zf):
            if tipo == "acesso_conta":
                sessoes.append(dados)
            elif tipo == "arquivo":
                continue  # não é o que viemos procurar
    return sessoes


def processar(cur, id_extracao, no_arquivo, dry_run=False):
    caminho = _localizar_ufdr(no_arquivo)
    if caminho is None:
        print(f"  [{id_extracao}] !! arquivo não encontrado na pasta de dados: {no_arquivo}")
        return None
    sessoes = _sessoes_do_ufdr(caminho)
    if dry_run:
        print(f"  [{id_extracao}] {len(sessoes)} sessão(ões) de conta encontrada(s) (dry-run)")
        return len(sessoes)

    cur.execute(f"DELETE FROM investigacao.acs_tb01_acessos WHERE id_extracao = %s AND {_FONTES_CELLEBRITE}",
                (id_extracao,))
    for acesso in sessoes:
        _gravar_acesso_conta(cur, id_extracao, acesso)
    cur.execute(
        """UPDATE investigacao.inq_tb02_extracoes
           SET qt_acessos = (SELECT count(*) FROM investigacao.acs_tb01_acessos WHERE id_extracao = %s)
           WHERE id_extracao = %s""",
        (id_extracao, id_extracao),
    )
    print(f"  [{id_extracao}] {len(sessoes)} sessão(ões) gravada(s)")
    return len(sessoes)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--extracao", type=int, action="append", dest="ids",
                    help="id de extração (repetível); sem ele, percorre as concluídas")
    ap.add_argument("--listar", action="store_true", help="só lista as extrações-alvo")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    alvo = _extracoes_alvo(args.ids)
    if args.listar:
        for id_ext, nome in alvo:
            print(f"{id_ext}: {nome}")
        return

    total = 0
    with get_connection() as conn:
        with conn.cursor() as cur:
            for id_ext, nome in alvo:
                print(f"extração {id_ext}: {nome}")
                feitas = processar(cur, id_ext, nome, dry_run=args.dry_run)
                if feitas:
                    total += feitas
        conn.commit()
    print(f"total: {total} sessão(ões){' (dry-run, nada gravado)' if args.dry_run else ''}")


if __name__ == "__main__":
    main()

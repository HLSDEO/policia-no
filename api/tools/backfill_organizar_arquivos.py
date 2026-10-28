"""Backfill da organização dos arquivos de investigação em data/.

Move o que foi importado ANTES da estrutura por inquérito para
data/<delegacia>/<inquérito>/<categoria>/ e grava o caminho novo no banco:

    ufdr        INQ_TB02_EXTRACOES.NO_ARQUIVO   -> .../CELULAR/
    iped        IPD_TB01_EXTRACOES.NO_PASTA     -> .../NUVEM/   (e a extração-ponte em INQ_TB02)
    rif         RIF_TB01_RIFS.NO_PASTA          -> .../RIF/     (nome padronizado RIF_<número>)
    simba       SMB_TB01_EXTRATOS.NO_ARQUIVO    -> .../BANCARIO/
    documentos  INQ_TB04_DOCUMENTOS.NO_ARQUIVO  -> .../OUTROS/

É o mesmo destino que a importação passou a usar sozinha — ver funcoes/organizacao_arquivos.py
e docs/organizacao_arquivos.md.

Regras:
- Idempotente: o que já está em .../<categoria>/ é pulado.
- Sem inquérito com número -> o arquivo fica onde está.
- Arquivo/pasta que não está mais em data/ -> só reporta, não derruba o passe.
- Colisão de nome no destino resolve com sufixo numérico, como na importação.
- A delegacia sai do primeiro segmento do caminho atual do arquivo (a pasta de onde ele veio),
  não do cadastro da unidade.
- O PDF do RIF não entra no passe de documentos: ele mora dentro da pasta do RIF e vai junto
  com ela.

Uso, de dentro do contêiner da API:
    python /app/tools/backfill_organizar_arquivos.py --listar
    python /app/tools/backfill_organizar_arquivos.py --dry-run
    python /app/tools/backfill_organizar_arquivos.py
    python /app/tools/backfill_organizar_arquivos.py --categoria ufdr --categoria simba --dry-run
"""

import argparse
import sys

sys.path.insert(0, "/app")

from funcoes.db_utils import get_connection                      # noqa: E402
from funcoes.organizacao_arquivos import (                       # noqa: E402
    CATEGORIA_BANCARIO,
    CATEGORIA_CELULAR,
    CATEGORIA_NUVEM,
    CATEGORIA_OUTROS,
    CATEGORIA_RIF,
    PASTA_DADOS,
    buscar_arquivo,
    buscar_pasta,
    delegacia_de,
    destino,
    ja_organizado,
    organizar,
    sanitizar_inquerito,
)

# Cada passe: a consulta dos alvos, se o alvo é pasta ou arquivo, a categoria de destino e o
# UPDATE que grava o caminho novo. A consulta devolve sempre
# (id, nome atual, número do inquérito, número do RIF ou NULL).
PASSES = {
    "ufdr": {
        "categoria": CATEGORIA_CELULAR,
        "pasta": False,
        "sql": """
            SELECT e.id_extracao, e.no_arquivo, i.nu_inquerito, NULL
            FROM investigacao.inq_tb02_extracoes e
            JOIN investigacao.inq_tb01_inqueritos i ON i.id_inquerito = e.id_inquerito
            WHERE e.no_arquivo <> ''
              AND lower(e.no_arquivo) LIKE '%.ufdr'
              AND (e.no_fabricante IS NULL OR e.no_fabricante <> 'IPED')
            ORDER BY e.id_extracao
        """,
        "update": ["UPDATE investigacao.inq_tb02_extracoes SET no_arquivo = %s WHERE id_extracao = %s"],
    },
    "iped": {
        "categoria": CATEGORIA_NUVEM,
        "pasta": True,
        "sql": """
            SELECT p.id_extracao_iped, p.no_pasta, i.nu_inquerito, NULL
            FROM investigacao.ipd_tb01_extracoes p
            JOIN investigacao.inq_tb01_inqueritos i ON i.id_inquerito = p.id_inquerito
            WHERE p.no_pasta <> ''
            ORDER BY p.id_extracao_iped
        """,
        # a pasta-caso responde por duas linhas: a própria e a extração-ponte em INQ_TB02
        "update": [
            "UPDATE investigacao.ipd_tb01_extracoes SET no_pasta = %s WHERE id_extracao_iped = %s",
            "UPDATE investigacao.inq_tb02_extracoes SET no_arquivo = %s WHERE id_extracao = "
            "(SELECT id_extracao FROM investigacao.ipd_tb01_extracoes WHERE id_extracao_iped = %s)",
        ],
    },
    "rif": {
        "categoria": CATEGORIA_RIF,
        "pasta": True,
        "sql": """
            SELECT r.id_rif, r.no_pasta, i.nu_inquerito, r.nu_rif
            FROM investigacao.rif_tb01_rifs r
            JOIN investigacao.inq_tb01_inqueritos i ON i.id_inquerito = r.id_inquerito
            WHERE r.no_pasta <> ''
            ORDER BY r.id_rif
        """,
        "update": ["UPDATE investigacao.rif_tb01_rifs SET no_pasta = %s WHERE id_rif = %s"],
    },
    "simba": {
        "categoria": CATEGORIA_BANCARIO,
        "pasta": False,
        "sql": """
            SELECT s.id_extrato, s.no_arquivo, i.nu_inquerito, NULL
            FROM investigacao.smb_tb01_extratos s
            JOIN investigacao.inq_tb01_inqueritos i ON i.id_inquerito = s.id_inquerito
            WHERE s.no_arquivo <> ''
            ORDER BY s.id_extrato
        """,
        "update": ["UPDATE investigacao.smb_tb01_extratos SET no_arquivo = %s WHERE id_extrato = %s"],
    },
    "documentos": {
        "categoria": CATEGORIA_OUTROS,
        "pasta": False,
        "sql": """
            SELECT d.id_documento, d.no_arquivo, i.nu_inquerito, NULL
            FROM investigacao.inq_tb04_documentos d
            JOIN investigacao.inq_tb01_inqueritos i ON i.id_inquerito = d.id_inquerito
            WHERE d.no_arquivo <> ''
              AND NOT EXISTS (SELECT 1 FROM investigacao.rif_tb01_rifs r
                              WHERE r.id_documento_pdf = d.id_documento)
            ORDER BY d.id_documento
        """,
        "update": ["UPDATE investigacao.inq_tb04_documentos SET no_arquivo = %s WHERE id_documento = %s"],
    },
}


def _localizar(valor: str, pasta: bool):
    """Caminho real do que está gravado no banco: o valor pode já ser o caminho relativo novo ou
    só o nome do formato antigo (aí vale a busca em data/)."""
    candidato = PASTA_DADOS / valor
    if (candidato.is_dir() if pasta else candidato.is_file()):
        return candidato
    nome = valor.replace("\\", "/").split("/")[-1]
    return buscar_pasta(nome) if pasta else buscar_arquivo(nome)


def _rodar(categoria: str, dry_run: bool) -> dict:
    """Um passe por categoria; devolve os contadores para o resumo final."""
    passe = PASSES[categoria]
    contadores = {"total": 0, "movidos": 0, "ja_organizado": 0,
                  "sem_inquerito": 0, "nao_encontrado": 0, "erros": 0}

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(passe["sql"])
            alvos = cur.fetchall()

        for id_registro, valor, numero, nu_rif in alvos:
            contadores["total"] += 1
            try:
                caminho = _localizar(valor, passe["pasta"])
                if caminho is None:
                    contadores["nao_encontrado"] += 1
                    print(f"  !! [{id_registro}] {valor} — não está mais em data/")
                    continue
                if ja_organizado(caminho):
                    contadores["ja_organizado"] += 1
                    continue
                if not (numero or "").strip():
                    contadores["sem_inquerito"] += 1
                    print(f"  -- [{id_registro}] {valor} — sem número de inquérito; fica onde está")
                    continue

                novo_nome = f"RIF_{nu_rif}" if (categoria == "rif" and nu_rif) else None
                if dry_run:
                    alvo = destino(caminho, delegacia_de(caminho),
                                   sanitizar_inquerito(numero), passe["categoria"], novo_nome)
                    print(f"  -> [{id_registro}] {valor}\n       => {alvo.relative_to(PASTA_DADOS).as_posix()}")
                    contadores["movidos"] += 1
                    continue

                relativo = organizar(caminho, numero, passe["categoria"], novo_nome)
                if relativo is None:
                    contadores["erros"] += 1
                    print(f"  !! [{id_registro}] {valor} — não foi possível mover (ver log da API)")
                    continue
                with conn.cursor() as cur:
                    for sql in passe["update"]:
                        cur.execute(sql, (relativo, id_registro))
                conn.commit()
                contadores["movidos"] += 1
                print(f"  -> [{id_registro}] {valor}\n       => {relativo}")
            except Exception as exc:  # um registro problemático não derruba o passe
                conn.rollback()
                contadores["erros"] += 1
                print(f"  !! [{id_registro}] {valor} — erro: {type(exc).__name__}: {exc}")

    return contadores


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--categoria", action="append", choices=sorted(PASSES),
                    help="só processa esta categoria (pode repetir); sem isto, processa todas")
    ap.add_argument("--listar", action="store_true",
                    help="mesma coisa que --dry-run, mantido por conveniência")
    ap.add_argument("--dry-run", action="store_true",
                    help="mostra o que mudaria, sem mover arquivo nem gravar no banco")
    args = ap.parse_args()

    dry_run = args.dry_run or args.listar
    categorias = args.categoria or list(PASSES)
    total_movidos = 0
    for categoria in categorias:
        print(f"\n=== {categoria.upper()} ===", flush=True)
        contadores = _rodar(categoria, dry_run)
        total_movidos += contadores["movidos"]
        modo = "[DRY-RUN, nada gravado] " if dry_run else ""
        print(f"  {modo}" + " | ".join(f"{k}: {v}" for k, v in contadores.items()))

    print(f"\nTotal de arquivos/pastas movidos: {total_movidos}"
          + (" (dry-run)" if dry_run else ""))


if __name__ == "__main__":
    main()

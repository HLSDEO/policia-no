"""Verificação diária da saúde dos dumps de backup (lado da verificação; o lado do dump é o
sidecar `backups` do docker-compose.yml — ver docs/backup.md).

**Por que existe.** Um backup que ninguém verifica é uma esperança, não um backup: o sidecar
pode estar há semanas sem conseguir autenticar no banco, o disco pode ter enchido, o dump pode
ter nascido de 0 bytes — e todos esses modos de falha são SILENCIOSOS até o dia do desastre.
Esta verificação não restaura nada (restaurar de verdade é o teste rodado no host por
`scripts/backup-teste-restore.sh`, que precisa de Docker); ela fecha o vão do dia a dia com
três perguntas baratas sobre a pasta de dumps:

1. existe pelo menos um dump?
2. o mais recente tem tamanho > 0?
3. o mais recente tem menos de 26 h? (o dump é diário; 26 h dá 1 h de folga ao relógio do
   sidecar sem disparar alarme falso na virada do horário de verão de ninguém)

O agendador (funcoes/agendador.py, tarefa `verificacao_backup`) roda isto um vez por dia e o
resultado fica no ledger AGD_TB01_EXECUCOES — 'erro' com o motivo, visível para sempre no log
e na consulta de execuções. A pasta é lida do bind MONTADO SOMENTE LEITURA no container da API
(./backups -> /app/backups): a API nunca escreve backup, só lê.
"""

import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# O dump diário pode atrasar 1 h sem ser emergência; atrasou mais, o dia está perdido.
_IDADE_MAXIMA_S = 26 * 3600.0

# Os dois formatos que o sidecar sabe produzir (FORMAT=custom -> .dump; imagem antiga/config
# plain+ZIP -> .sql.gz). Qualquer outro arquivo na pasta (lock, log) é ignorado.
_SUFIXOS = (".dump", ".sql.gz", ".sql")


def pasta_configurada() -> bool:
    """A instalação tem o sidecar de backup montado? Sem a pasta a verificação não registra
    nada: quem não contratou o serviço não recebe alarme do serviço."""
    return _pasta().is_dir()


def _pasta() -> Path:
    bruto = (os.environ.get("BACKUP_PASTA") or "").strip() or "/app/backups"
    return Path(bruto)


def verificar() -> dict:
    """As três perguntas do cabeçalho sobre a pasta de dumps.

    Devolve {ok, motivo?, arquivos, mais_recente?}. Nunca levanta: quem chama (agendador)
    grava o veredito no ledger e segue — a varredura de backup não derruba nada."""
    pasta = _pasta()
    mais: tuple[float, str, int] | None = None
    total = 0
    try:
        for raiz, _dirs, nomes in os.walk(pasta):
            for nome in nomes:
                if not nome.endswith(_SUFIXOS):
                    continue
                try:
                    st = (Path(raiz) / nome).stat()
                except OSError:
                    continue
                total += 1
                if mais is None or st.st_mtime > mais[0]:
                    mais = (st.st_mtime, nome, st.st_size)
    except OSError:
        return {"ok": False, "motivo": f"falha ao ler a pasta de backups ({pasta})",
                "arquivos": 0}

    if mais is None:
        return {"ok": False, "motivo": f"nenhum dump de backup em {pasta} "
                                       "(o sidecar 'backups' está subindo?)", "arquivos": 0}

    _mtime, nome, tamanho = mais
    if tamanho <= 0:
        return {"ok": False, "motivo": f"o dump mais recente ({nome}) tem 0 bytes",
                "arquivos": total, "mais_recente": nome}

    idade = time.time() - _mtime
    if idade > _IDADE_MAXIMA_S:
        horas = idade / 3600
        return {"ok": False, "motivo": f"o dump mais recente ({nome}) tem {horas:.0f} h — "
                                       "o sidecar não gravou ontem",
                "arquivos": total, "mais_recente": nome}

    return {"ok": True, "arquivos": total, "mais_recente": nome}
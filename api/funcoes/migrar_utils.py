"""Runner das migrações de schema — aplica os deltas de `db/migracoes/` que ainda não rodaram.

**Contexto.** `db/init/*.sql` descreve o schema COMPLETO e só roda em volume vazio (primeira
subida do Postgres — ver `db/bootstrap/00_aplicar_migracoes.sh`). Toda mudança de schema num
banco que JÁ existe entra como um arquivo novo em `db/migracoes/` (`ALTER`/`UPDATE`/`CREATE
INDEX`…), e este módulo é quem aplica esses arquivos — uma vez cada, em ordem — registrando no
ledger `SEGURANCA.SIS_TB01_MIGRACOES`.

**Quando roda.**
- No startup da API (`main.py`), a menos que `MIGRAR_NO_STARTUP=0` no `.env`. É o que garante que
  nenhuma rota suba consultando coluna que ainda não existe — o erro de deploy mais comum.
- Na mão: `docker compose exec api-1 python /app/tools/migrar.py` (mesma função).
- Pelo `analyser-update`, logo depois de subir os containers.

**Regras.**
- *Forward-only*: nunca desfaz. Reversão real é um delta compensatório novo.
- Uma transação por arquivo — falha reverte o arquivo inteiro e PARA o processo; o ledger não
  registra, então o próximo deploy retenta do zero.
- Arquivo cuja primeira linha é `-- migracao: sem-transacao` roda em autocommit (para
  `CREATE INDEX CONCURRENTLY`, `VACUUM`, backfill que faz o próprio commit). Esses precisam ser
  idempotentes — escreva `CREATE INDEX CONCURRENTLY IF NOT EXISTS`.
- `pg_advisory_lock` serializa duas instâncias/dois workers subindo ao mesmo tempo.
- O sha256 de cada arquivo aplicado fica no ledger; divergência depois = arquivo editado após
  publicado (proibido) e vira aviso no log, não erro.
"""

import hashlib
import logging
import os
import re
import time
from pathlib import Path

from funcoes.db_utils import get_connection

logger = logging.getLogger(__name__)

DIR_MIGRACOES = Path(os.environ.get("DIR_MIGRACOES", "/app/db/migracoes"))

# Chave do advisory lock — arbitrária e fixa, só não pode colidir com outro pg_advisory_lock do
# código (não há outro hoje).
_LOCK_ID = 72301

# Quanto um worker espera por outro que esteja migrando (10 min, em fatias de 5 s com log):
# com API_WORKERS > 1 todos disputam este lock no startup, e morrer no lock_timeout para o
# supervisor ressuscitar em loop era o comportamento antigo (ver C5).
_ESPERA_LOCK_SEGUNDOS = 600.0
_INTERVAL_LOCK_SEGUNDOS = 5.0

_RE_NUMERO = re.compile(r"^(\d+)_")
_MARCA_SEM_TRANSACAO = "-- migracao: sem-transacao"

_DDL_LEDGER = """
CREATE TABLE IF NOT EXISTS SEGURANCA.SIS_TB01_MIGRACOES (
    NU_MIGRACAO      INTEGER      NOT NULL PRIMARY KEY,
    NO_ARQUIVO       VARCHAR(200) NOT NULL,
    CO_HASH          CHAR(64)     NOT NULL,
    DS_ORIGEM        VARCHAR(20)  NOT NULL,
    NU_MS_DURACAO    INTEGER,
    NO_APLICADA_POR  VARCHAR(100),
    DT_APLICACAO     TIMESTAMP    NOT NULL DEFAULT now()
)
"""


def _numero(nome: str) -> int | None:
    m = _RE_NUMERO.match(nome)
    return int(m.group(1)) if m else None


def _arquivos() -> list[Path]:
    """Deltas de `db/migracoes/`, em ordem numérica. Nome sem prefixo `NNNN_` é ignorado com aviso."""
    if not DIR_MIGRACOES.is_dir():
        logger.warning("diretório de migrações não encontrado: %s (montado no compose?)", DIR_MIGRACOES)
        return []
    encontrados: list[tuple[int, Path]] = []
    for caminho in DIR_MIGRACOES.glob("*.sql"):
        num = _numero(caminho.name)
        if num is None:
            logger.warning("migração ignorada (nome sem prefixo numérico): %s", caminho.name)
            continue
        encontrados.append((num, caminho))
    return [caminho for _, caminho in sorted(encontrados)]


def _numeros_duplicados(arquivos: list[Path]) -> dict[int, list[str]]:
    """Números usados por mais de um arquivo — `{21: ["0021_a.sql", "0021_b.sql"]}`.

    Isto já aconteceu de verdade (dois `0021_` vindos de branches diferentes) e o estrago é
    silencioso: o prefixo é a identidade da migração no ledger (NU_MIGRACAO é PRIMARY KEY), então
    o runner aplicava o primeiro, registrava o número e **pulava o segundo pelo `continue` de "já
    registrada"** — sem erro, sem aviso, e com o schema divergindo entre instalações conforme a
    ordem em que cada uma aplicou. Melhor parar o deploy com a causa na tela."""
    por_numero: dict[int, list[str]] = {}
    for caminho in arquivos:
        num = _numero(caminho.name)
        if num is not None:
            por_numero.setdefault(num, []).append(caminho.name)
    return {num: nomes for num, nomes in por_numero.items() if len(nomes) > 1}


def _erro_duplicados(duplicados: dict[int, list[str]]) -> str:
    detalhe = "; ".join(f"{num}: {' e '.join(nomes)}" for num, nomes in sorted(duplicados.items()))
    return ("migrações com o mesmo número (o prefixo é a identidade no ledger, então uma delas "
            f"nunca seria aplicada) — {detalhe}. Renumere uma para o próximo número livre e "
            "deixe-a idempotente (IF NOT EXISTS), porque parte das instalações já aplicou a outra.")


def _sha256(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def _garantir_ledger(cur) -> None:
    cur.execute("CREATE SCHEMA IF NOT EXISTS SEGURANCA")
    cur.execute(_DDL_LEDGER)


def estado() -> dict:
    """Resumo para o endpoint: última migração registrada, pendentes e as que mudaram após aplicadas."""
    arquivos = _arquivos()
    with get_connection() as conn:
        with conn.cursor() as cur:
            _garantir_ledger(cur)
            cur.execute("SELECT NU_MIGRACAO, CO_HASH FROM SEGURANCA.SIS_TB01_MIGRACOES")
            registradas = {int(n): (h or "").strip() for n, h in cur.fetchall()}
        conn.commit()

    pendentes, alteradas = [], []
    for caminho in arquivos:
        num = _numero(caminho.name)
        if num not in registradas:
            pendentes.append(caminho.name)
        elif registradas[num] != _sha256(caminho.read_text(encoding="utf-8-sig")):
            alteradas.append(caminho.name)

    return {
        "ultima_aplicada": max(registradas) if registradas else None,
        "total_registradas": len(registradas),
        "total_arquivos": len(arquivos),
        "pendentes": pendentes,
        "alteradas_apos_aplicadas": alteradas,
        # ["0021_a.sql e 0021_b.sql"] — a tela do servidor mostra em vermelho; aqui é só relato,
        # quem recusa aplicar é o executar()
        "numeros_duplicados": [" e ".join(nomes)
                               for _, nomes in sorted(_numeros_duplicados(arquivos).items())],
    }


def executar(aplicada_por: str = "manual") -> dict:
    """Aplica os deltas pendentes. Devolve `{aplicadas: [nomes], ja_registradas: int, erro: str|None}`.

    Não levanta exceção por migração que falha — devolve em `erro`, e quem chama decide o que
    fazer (o startup da API vira `RuntimeError`; o CLI imprime e sai != 0)."""
    arquivos = _arquivos()
    aplicadas: list[str] = []

    # Antes de qualquer coisa: dois arquivos com o mesmo número fariam este runner pular um deles
    # em silêncio (ver _numeros_duplicados). Falhar aqui é o que transforma um schema divergente
    # e invisível num deploy que não sobe — no startup da API isto vira RuntimeError com a causa.
    duplicados = _numeros_duplicados(arquivos)
    if duplicados:
        erro = _erro_duplicados(duplicados)
        logger.error("%s", erro)
        return {"aplicadas": [], "ja_registradas": 0, "erro": erro}

    # Perfil longa de propósito (ver C5 e db_utils): migração é DDL que pode levar minutos —
    # no perfil web o statement_timeout de 60 s matava qualquer migração mais demorada e o
    # lock_timeout de 5 s matava os workers que chegavam depois do primeiro.
    with get_connection(longa=True) as conn:
        # Lock ANTES do CREATE TABLE IF NOT EXISTS: sem isso, duas instâncias subindo juntas
        # podem colidir no próprio IF NOT EXISTS (condição de corrida conhecida do Postgres).
        # Espera ocupada com pg_try_advisory_lock em vez do pg_advisory_lock bloqueante: com
        # API_WORKERS > 1 todos os workers disputam este lock no startup, e o bloqueante morria
        # no lock_timeout — o supervisor ressuscitava o worker, que morria de novo, em loop.
        with conn.cursor() as cur:
            inicio_espera = time.monotonic()
            while True:
                cur.execute("SELECT pg_try_advisory_lock(%s)", (_LOCK_ID,))
                if cur.fetchone()[0]:
                    break
                if time.monotonic() - inicio_espera > _ESPERA_LOCK_SEGUNDOS:
                    erro = ("outro processo está aplicando migrações há mais de "
                            f"{int(_ESPERA_LOCK_SEGUNDOS // 60)} min — desista ou verifique trava")
                    logger.error("%s", erro)
                    return {"aplicadas": aplicadas, "ja_registradas": 0, "erro": erro}
                logger.info("migrações em curso em outro processo — aguardando a vez…")
                conn.commit()  # try_lock não abre transação útil; commita para não acumular idle
                time.sleep(_INTERVAL_LOCK_SEGUNDOS)
        conn.commit()

        try:
            with conn.cursor() as cur:
                _garantir_ledger(cur)
                conn.commit()
                cur.execute("SELECT NU_MIGRACAO FROM SEGURANCA.SIS_TB01_MIGRACOES")
                registradas = {int(r[0]) for r in cur.fetchall()}
                conn.commit()

            for caminho in arquivos:
                num = _numero(caminho.name)
                if num in registradas:
                    continue

                sql = caminho.read_text(encoding="utf-8-sig")
                sem_transacao = sql.lstrip().startswith(_MARCA_SEM_TRANSACAO)
                inicio = time.monotonic()

                try:
                    if sem_transacao:
                        conn.autocommit = True
                        with conn.cursor() as cur:
                            cur.execute(sql)
                        conn.autocommit = False
                    else:
                        with conn.cursor() as cur:
                            cur.execute(sql)

                    ms = int((time.monotonic() - inicio) * 1000)
                    with conn.cursor() as cur:
                        cur.execute(
                            """INSERT INTO SEGURANCA.SIS_TB01_MIGRACOES
                                   (NU_MIGRACAO, NO_ARQUIVO, CO_HASH, DS_ORIGEM, NU_MS_DURACAO, NO_APLICADA_POR)
                               VALUES (%s, %s, %s, 'runner', %s, %s)""",
                            (num, caminho.name, _sha256(sql), ms, (aplicada_por or "")[:100]),
                        )
                    conn.commit()
                except Exception as exc:  # noqa: BLE001 — qualquer erro do psql precisa parar o runner
                    try:
                        conn.rollback()
                        conn.autocommit = False
                    except Exception:
                        pass
                    logger.error("migração %s FALHOU: %s", caminho.name, exc)
                    return {"aplicadas": aplicadas, "ja_registradas": len(registradas), "erro": f"{caminho.name}: {exc}"}

                aplicadas.append(caminho.name)
                logger.info("migração aplicada: %s (%d ms)", caminho.name, ms)

            # aviso — não erro — para arquivo alterado depois de registrado
            for caminho in arquivos:
                num = _numero(caminho.name)
                if num in registradas:
                    with conn.cursor() as cur:
                        cur.execute("SELECT CO_HASH FROM SEGURANCA.SIS_TB01_MIGRACOES WHERE NU_MIGRACAO = %s", (num,))
                        gravado = (cur.fetchone()[0] or "").strip()
                    if gravado and gravado != _sha256(caminho.read_text(encoding="utf-8-sig")):
                        logger.warning(
                            "migração %s foi editada DEPOIS de aplicada (hash difere) — o correto é um delta novo",
                            caminho.name,
                        )
                    conn.commit()
        finally:
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT pg_advisory_unlock(%s)", (_LOCK_ID,))
                conn.commit()
            except Exception:
                logger.exception("falha ao soltar o advisory lock (liberado ao fechar a conexão)")

    return {"aplicadas": aplicadas, "ja_registradas": len(registradas), "erro": None}


def marcar_baseline(origem: str = "manual", aplicada_por: str = "cutover") -> dict:
    """Registra TODOS os deltas atuais como já aplicados, **sem executá-los**.

    É o cutover de um servidor que já existia antes deste sistema: o schema dele foi mantido à
    mão e já contém o efeito dos deltas, então rodá-los quebraria (coluna duplicada). O bootstrap
    faz o equivalente numa instalação nova (Fase 2 de db/bootstrap/00_aplicar_migracoes.sh).

    Idempotente: `ON CONFLICT DO NOTHING`, e nunca mexe em linha já registrada."""
    arquivos = _arquivos()
    marcadas: list[str] = []

    # Mesmo motivo do executar(): com dois arquivos no mesmo número, o ON CONFLICT DO NOTHING
    # abaixo registraria um e engoliria o outro — e o runner passaria a considerar os DOIS
    # aplicados, deixando o efeito que faltou invisível para sempre.
    duplicados = _numeros_duplicados(arquivos)
    if duplicados:
        erro = _erro_duplicados(duplicados)
        logger.error("%s", erro)
        return {"marcadas": [], "total_arquivos": len(arquivos), "erro": erro}

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_lock(%s)", (_LOCK_ID,))
        conn.commit()
        try:
            with conn.cursor() as cur:
                _garantir_ledger(cur)
                conn.commit()
                for caminho in arquivos:
                    num = _numero(caminho.name)
                    sql = caminho.read_text(encoding="utf-8-sig")
                    cur.execute(
                        """INSERT INTO SEGURANCA.SIS_TB01_MIGRACOES
                               (NU_MIGRACAO, NO_ARQUIVO, CO_HASH, DS_ORIGEM, NO_APLICADA_POR)
                           VALUES (%s, %s, %s, %s, %s)
                           ON CONFLICT (NU_MIGRACAO) DO NOTHING""",
                        (num, caminho.name, _sha256(sql), origem, (aplicada_por or "")[:100]),
                    )
                    if cur.rowcount:
                        marcadas.append(caminho.name)
                conn.commit()
        finally:
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT pg_advisory_unlock(%s)", (_LOCK_ID,))
                conn.commit()
            except Exception:
                logger.exception("falha ao soltar o advisory lock")

    logger.info("baseline de migrações: %d marcada(s) sem executar", len(marcadas))
    return {"marcadas": marcadas, "total_arquivos": len(arquivos), "erro": None}

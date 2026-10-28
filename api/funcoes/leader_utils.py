"""Eleição de líder entre réplicas da API via advisory lock de SESSÃO no Postgres.

**Por que existe.** A API nasceu para rodar como um processo só e ganhou, ao longo do tempo,
laços de fundo (agendador, watchdogs) que devem rodar UMA vez no sistema, não uma vez por
processo. Com uma segunda réplica do container (escala atrás do nginx), cada processo subia o
seu laço e o sistema passava a ter dois agendadores varrendo o banco a cada tique. As filas que
reivindicam por banco (despachante de extrações, jobs pesados, o ledger do agendador) já são
seguras sozinhas — advisory lock de transação + PRIMARY KEY resolvem a exclusividade MUTUA de
cada item. Este módulo resolve a outra metade: laços periódicos que não têm o que reivindicar
(eu só VARRO) e que, rodando em dobro, produzem trabalho redundante e log duplicado.

**Como funciona.** `iniciar_loop_lider(nome, chave, tique_fn, intervalo)` sobe uma thread que:

1. abre uma conexão dedicada (funcoes/db_utils.nova_conexao_dedicada) e tenta
   `pg_try_advisory_lock(chave)`;
2. SE era livre: segura o lock, roda `tique_fn()` a cada `intervalo` segundos;
3. SE estava tomado: dorme `disputa` segundos e tenta de novo;
4. se a conexão ou o tique estourar: fecha a conexão (o Postgres solta o lock sozinho), vira
   candidata de novo e espera `disputa` segundos.

O failover é a consequência do mecanismo: o lock de SESSÃO morre com a conexão, e a conexão
morre com o processo — uma réplica que tomba devolve a liderança para as vivas em até `disputa`
segundos, sem nada instalado (no limit: sem zookeeper, sem consul, sem tabela nova).

**A chave é um inteiro arbitrário e fixo**, igual às outras do sistema: 72301 migrações
(migrar_utils), 72302 vaga de job pesado (jobs_pesados), 72303 fila de extrações
(investigacao_utils), 72304 identificação de RIF (rif_utils). As daqui começam em 72310 —
antes de criar uma chave nova, confira essa lista.

**Isto NÃO é substituto da reivindicação por banco.** O líder pode falhar no meio de um tique,
e no intervalo entre perder o lock e alguém assumir dois candidatos podem rodar o MESMO tique.
Por isso o contrato de `tique_fn` é o mesmo dos laços que já existem: cada tique tem que ser
idempotente/seguro para rodar junto (o ledger do agendador continua sendo a garantia; o líder é
só quem evita o varrer-em-dobro do caminho feliz).
"""

import logging
import threading
import time

from funcoes import db_utils

logger = logging.getLogger(__name__)

# Chaves fixas dos laços com dono único (ver a lista no cabeçalho do módulo).
AGENDADOR = 72310
WATCHDOG_ANALISES = 72311

# Quanto uma candidata espera para tentar o lock de novo. 30 s é discreto para a madrugada do
# agendador (tique de 5 min) e curto o bastante para a liderança não ficar viúva de um processo.
_ESPERA_DISPUTA_S = 30.0

# Estado observável: nome -> é o líder neste processo agora. Escrita só pela thread do próprio
# loop; leitura de qualquer lado (dict é atômico por chave sob o GIL).
_lider: dict[str, bool] = {}
_iniciados: dict[str, int] = {}
_trava = threading.Lock()


def eh_lider(nome: str) -> bool:
    """Este processo é o líder do laço `nome` agora? (laço sem dono = False em todos)."""
    return _lider.get(nome, False)


def estado_liderancas() -> dict[str, bool]:
    """Fotografia `{nome: é_lider}` de todos os laços eleitos deste processo (diagnóstico)."""
    return dict(_lider)


def iniciar_loop_lider(nome: str, chave: int, tique_fn, intervalo_s: float,
                       espera_inicial_s: float = 0.0) -> None:
    """Sobe o laço `tique_fn` como líder elected. Idempotente por nome (igual ao padrão dos
    outros `iniciar_*`: startup pode chamar duas vezes sem subir dois laços).

    `espera_inicial_s` preserva o respiro que os laços tinham no startup (o watchdog esperava
    90 s antes da primeira varredura — para não disputar com as faxinas do próprio startup)."""
    with _trava:
        if nome in _iniciados:
            return
        if chave in _iniciados.values():
            raise ValueError(f"chave de lock {chave} já usada pelo laço "
                             f"{[n for n, c in _iniciados.items() if c == chave][0]}")
        _iniciados[nome] = chave
        _lider[nome] = False
    threading.Thread(target=_ciclo_lideranca,
                     args=(nome, chave, tique_fn, intervalo_s, espera_inicial_s),
                     daemon=True, name=f"lider-{nome}").start()


def _ciclo_lideranca(nome: str, chave: int, tique_fn, intervalo_s: float,
                     espera_inicial_s: float) -> None:
    if espera_inicial_s > 0:
        time.sleep(espera_inicial_s)
    conn = None
    while True:
        try:
            if conn is None:
                conn = db_utils.nova_conexao_dedicada()
                with conn.cursor() as cur:
                    cur.execute("SELECT pg_try_advisory_lock(%s)", (chave,))
                    pegou = bool(cur.fetchone()[0])
                if not pegou:
                    conn.close()
                    conn = None
            if conn is None:
                if _lider.get(nome):
                    logger.info("lideranca(%s): perdeu o lock — candidata de novo em %.0f s",
                                nome, _ESPERA_DISPUTA_S)
                _lider[nome] = False
                time.sleep(_ESPERA_DISPUTA_S)
                continue
            if not _lider.get(nome):
                logger.info("lideranca(%s): assumiu a liderança do laço", nome)
                _lider[nome] = True
            # Sonda a conexão que SEGURA o lock antes de ticar. O lock é de SESSÃO e morre com
            # o TCP — mas uma queda silenciosa (firewall/NAT matando a conexão ociosa entre
            # ticks, rede instável) não acorda ninguém: sem esta sonda o Postgres tinha liberado
            # o lock, a réplica espera tinha assumido, e este processo continuaria ticando como
            # líder fantasma para sempre (o "varrer-em-dobro" que a eleição existe para evitar,
            # de volta em silêncio). Um SELECT 1 por tick é de graça; se o canal morreu ele
            # levanta e o bloco abaixo devolve a liderança e volta a ser candidata.
            with conn.cursor() as cur_sonda:
                cur_sonda.execute("SELECT 1")
                cur_sonda.fetchone()
            tique_fn()
        except Exception:
            # Qualquer exceção (banco fora, tique que deixou vazar) devolve a liderança: fechar a
            # conexão solta o lock de sessão no servidor. A réplica que estiver esperando assume.
            logger.exception("lideranca(%s): falha — devolvendo o lock e tentando de novo em %.0f s",
                             nome, _ESPERA_DISPUTA_S)
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
                conn = None
            _lider[nome] = False
            time.sleep(_ESPERA_DISPUTA_S)
            continue
        time.sleep(intervalo_s)
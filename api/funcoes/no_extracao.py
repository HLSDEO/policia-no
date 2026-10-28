# -*- coding: utf-8 -*-
"""O nó como OPERÁRIO de extração (docs/no_descentralizado.md, fase "nó extrai").

Fluxo novo (este módulo + funcoes/canal_extracao.py + rotas):

1. O central, ao despachar uma extração de nó, não abre o arquivo: chama
   POST /no/extrair (no_servico.py) e sai da frente.
2. Aqui no nó, uma thread roda a pipeline REAL (`_processar_extracao` com um
   `CanalEntrega`): o .ufdr é lido do disco LOCAL, o parse e a transcrição consomem a CPU
   da ESTAÇÃO, e cada linha produzida vira um registro JSON que sobe ao central em lotes
   (o central reproduz com as MESMAS funções de gravação — dedup/ON CONFLICT preservados).
3. Estados (fase, progresso, batida de vida, hashes, contagens, finalizar) são callbacks
   que atualizam a linha da extração no central — é o que mantém a órbita, o badge e o
   vigia (watchdog) funcionando sem o central tocar no arquivo.

Concorrência: `EXTRACOES_SIMULTANEAS` agora também é o limite LOCAL do nó — cada estação
drena a sua fila na sua CPU, e o central deixa de ser o gargalo de parse dos 20 nós.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

import requests

try:
    import fcntl  # lock inter-processos da pasta de entrega (o nó é container Linux)
except ImportError:  # pragma: no cover — fora do container vale só o _RODANDO em memória
    fcntl = None

from funcoes import canal_extracao

logger = logging.getLogger(__name__)

# uma thread por extração, até o teto local; o resto espera a vaga no semaphore
_LIMITE = None
# capacidade COM QUEM o semaphore nasceu — o .env pode mudar depois sem restart (a fila seguiria
# no teto velho), e a tela precisa mostrar os dois: o teto EM VIGOR (processo) e o do arquivo
_LIMITE_CAP = 0
_RODANDO: dict[int, dict] = {}
_TRAVA = threading.Lock()


def _workers() -> int:
    """Processos uvicorn do container (API_WORKERS entra no ambiente via compose). O semaphore é
    POR PROCESSO — sem dividir, 'EXTRACOES_SIMULTANEAS=4' com 4 workers viraria 16 extrações
    juntas, exatamente o oposto do teto que a tela mostra."""
    try:
        return max(1, int(os.getenv("API_WORKERS") or 1))
    except ValueError:
        return 1


def _limite_local() -> int:
    try:
        # mesmo leitor do central (desempenho_utils): o valor mora no arquivo .env montado, que
        # nunca vira variável do ambiente do container — os.getenv sozinho sempre veria vazio
        from funcoes import desempenho_utils

        total = desempenho_utils.valor_int("EXTRACOES_SIMULTANEAS", 2)
        return max(1, -(-total // _workers()))  # ceil: com 2 vagas e 3 workers, 1 por processo
    except ValueError:
        return 2


def _semaphore():
    global _LIMITE, _LIMITE_CAP
    if _LIMITE is None:
        with _TRAVA:
            if _LIMITE is None:
                _LIMITE_CAP = _limite_local()
                _LIMITE = threading.Semaphore(_LIMITE_CAP)
    return _LIMITE


def rodando() -> list[int]:
    with _TRAVA:
        return sorted(_RODANDO)


def fila_estado() -> dict:
    """Instantâneo da fila local para a tela Administração → Servidores: quantas vagas do
    teto estão ocupadas, quantos trabalhos esperam vaga no semaphore e o quê roda agora
    (id + arquivo). O `_value` do semaphore é uso interno assumido de propósito — é só
    telemetria, nada aqui decide execução."""
    limite_arquivo = max(1, _limite_local() * _workers())  # o teto do .env AGORA x workers
    sem = _semaphore()
    # capacidade EM VIGOR neste processo, convertida de volta para o total do nó (cap é por
    # worker); com API_WORKERS>1 cada worker responde pela sua — _RODANDO/ocupadas daqui são por
    # processo mesmo, é telemetria aproximada de propósito, nada decide execução
    cap = (_LIMITE_CAP or _limite_local()) * _workers()
    livres = max(0, min(_LIMITE_CAP or cap, getattr(sem, "_value", cap)))
    ocupadas = max(0, (_LIMITE_CAP or cap) - livres)
    limite = limite_arquivo
    with _TRAVA:
        # só campos serializáveis: a entrada guarda também um threading.Event da troca de
        # geração, que não pode vazar para o JSON de /no/estado
        itens = [{"id_extracao": i, "no_arquivo": v.get("no_arquivo"),
                  "geracao": v.get("geracao")} for i, v in sorted(_RODANDO.items())]
    return {
        "limite": limite,
        "limite_processo": cap,
        "ocupadas": min(ocupadas, len(itens)) if itens else 0,
        "esperando": max(0, len(itens) - ocupadas),
        "trabalhos": itens,
    }


class DiscoInsuficienteError(RuntimeError):
    """O disco deste nó não tem folga para processar o arquivo (ver _exigir_disco); a rota
    devolve 507 e o central marca a extração com a mensagem — melhor recusar na portaria do
    que estourar o disco no meio do parse e derrubar o container (e a extração vizinha)."""


def _exigir_disco(caminho: Path) -> None:
    """Guard de espaço livre ANTES de aceitar o trabalho. A base é o tamanho REAL do arquivo
    aqui no nó (stat — mais honesto que o tamanho declarado no despacho: quem sofre o disco
    cheio é este disco). Exigência: livre ≥ fator × arquivo (o estepe é do spool JSONL dos
    lotes, dos .parcial do organize e dos temporários do parse), com piso em GB mesmo para
    arquivo pequeno — o spool cresce com o que for parseado e o container inteiro cai
    quando o disco zera. `NO_FATOR_DISCO` (padrão 2) e `NO_DISCO_MINIMO_GB` (padrão 2)
    afinam por estação."""
    import shutil

    try:
        tamanho = caminho.stat().st_size
        livre = shutil.disk_usage(caminho.parent).free
    except OSError:
        return  # sem estatística não se trava o mundo: segue, e o vigia continua vendo
    try:
        fator = max(1, int((os.getenv("NO_FATOR_DISCO") or "2").strip()))
        piso = max(256, int((os.getenv("NO_DISCO_MINIMO_GB") or "2").strip())) * 2**30
    except ValueError:
        fator, piso = 2, 2 * 2**30
    necessario = max(piso, tamanho * fator)
    if livre < necessario:
        raise DiscoInsuficienteError(
            f"O disco deste nó tem {livre / 2**30:.1f} GB livres e esta extração pede pelo "
            f"menos {necessario / 2**30:.1f} GB ({fator}x o tamanho do arquivo, que tem "
            f"{tamanho / 2**30:.1f} GB). Libere espaço no nó ou processe pelo central.")


def aceitar(id_extracao: int, no_arquivo: str, modelo_transcricao: str | None,
            transcrever_video: bool, numero_inquerito: str | None = None,
            geracao: int = 0) -> dict:
    """Valida o trabalho e coloca na fila local. Idempotente: o mesmo id E a mesma geração
    duas vezes (reenvio do central) não processa de novo. Geração DIFERENTE para um id já
    em andamento é o redespacho do watchdog alcançando uma passada VIVA (partição de rede
    curada): a passada velha recebe a ordem de parar e a nova assume o lugar — sem isso, o
    central teria limpado a tentativa parcial (reiniciar_entrega) contra uma entrega que
    ainda escrevia. Levanta FileNotFoundError se o arquivo não está na pasta data() daqui
    — o central marca erro com essa mensagem."""
    from funcoes.organizacao_arquivos import resolver as resolver_em_dados
    caminho = resolver_em_dados(no_arquivo)
    if caminho is None or not caminho.is_file() or caminho.suffix.lower() != ".ufdr":
        raise FileNotFoundError(f"{no_arquivo} não está na pasta data() deste nó")
    with _TRAVA:
        entrada = _RODANDO.get(id_extracao)
        if entrada is not None:
            if entrada.get("geracao") == geracao:
                return {"ok": True, "fila": False, "motivo": "já em andamento"}
        # o guard só faz sentido para trabalho NOVO: um reenvio da passada que já roda foi
        # aceito com o disco de ontem e continuar não custa espaço novo
        _exigir_disco(caminho)
        if entrada is not None:
            entrada.setdefault("cancelar_troca", threading.Event()).set()
            logger.warning("Extração %s redespachada (geração %s → %s): passada viva vai "
                           "dar lugar à nova", id_extracao, entrada.get("geracao"), geracao)
        _RODANDO[id_extracao] = {"no_arquivo": no_arquivo, "geracao": geracao,
                                 "cancelar_troca": threading.Event()}
    # Sem rmtree aqui de propósito (ver C4): a limpeza da tentativa abortada acontece no
    # _operario, DEPOIS de conquistar o lock da pasta — um redispatch que caia em outro
    # worker/thread nunca apaga os lotes da extração que ainda está rodando.
    try:
        threading.Thread(target=_operario,
                         args=(id_extracao, caminho, (modelo_transcricao, bool(transcrever_video)),
                               numero_inquerito, geracao),
                         daemon=True, name=f"no-extrai-{id_extracao}").start()
    except Exception:
        # exaustão de threads: sem devolver a entrada, o id ficaria "já em andamento"
        # PARA SEMPRE — nenhum reenvio do central passaria da dedup até o vigia de 60 min
        with _TRAVA:
            if _RODANDO.get(id_extracao, {}).get("geracao") == geracao:
                _RODANDO.pop(id_extracao, None)
        logger.exception("Extração %s: não consegui subir o worker", id_extracao)
        raise
    return {"ok": True, "fila": True}


# teto de espera por vaga na fila local e pelo flock da pasta: a passada substituída por um
# redespacho (geração nova) recebe o 409 no próximo callback e se desfaz em segundos — se
# 30 min depois o lock seguir ocupado, é outra coisa travada, e esperar mais só esconde
_ESPERA_VAGA_SEGUNDOS = 30 * 60


def _esperar_flock(fd: int) -> bool:
    """flock com espera: na troca de geração o dono velho ainda está desembalando a própria
    entrega (apagar lotes, soltar o lock) e o lugar abre sozinho. Sem espera, o _operario da
    geração nova desistia no primeiro LOCK_NB e a extração morria sem ninguém tentar."""
    if fcntl is None:
        return True
    limite = time.time() + _ESPERA_VAGA_SEGUNDOS
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            if time.time() >= limite:
                return False
            time.sleep(5)


def _operario(id_extracao: int, caminho: Path, config_tx: tuple,
              numero_inquerito: str | None = None, geracao: int = 0) -> None:
    from funcoes.investigacao_utils import _processar_extracao

    sem = _semaphore()
    if not sem.acquire(timeout=_ESPERA_VAGA_SEGUNDOS):
        logger.error("Extração %s: fila local ocupada há %s min; desistindo",
                     id_extracao, _ESPERA_VAGA_SEGUNDOS // 60)
        _reportar_erro(id_extracao, "A fila local do nó está ocupada há tempo demais "
                                    "(tentativa anterior não terminou de sair). Rode de novo.")
        with _TRAVA:
            if _RODANDO.get(id_extracao, {}).get("geracao") == geracao:
                _RODANDO.pop(id_extracao, None)
        return
    fd = None
    canal = None
    try:
        pasta = _pasta_entrega_livre(id_extracao)
        # Lock da pasta de entrega (ver C4): _RODANDO é por processo, e o nó pode rodar com
        # API_WORKERS > 1 — sem isto, um redispatch caindo em outro processo passava na
        # dedup e apagava os lotes da extração viva. Com flock, o segundo espera o primeiro
        # sair (troca de geração) e desiste só se o lock seguir tomado por 30 min.
        if fcntl is not None:
            try:
                fd = os.open(pasta / ".lock", os.O_CREAT | os.O_RDWR, 0o600)
            except OSError:
                fd = None
            if fd is not None and not _esperar_flock(fd):
                logger.warning("Extração %s: lock da entrega tomado por outra passada há "
                               "%s min; desistindo", id_extracao, _ESPERA_VAGA_SEGUNDOS // 60)
                _reportar_erro(id_extracao, "Outra tentativa desta extração não liberou a "
                                            "pasta de entrega. Confira o nó e rode de novo.")
                os.close(fd)
                return
        # Só quem tem o lock limpa: sobra de tentativa abortada (queda no meio) com
        # numeração velha NUNCA pode ser reenviada — a nova passada gera os seus do zero.
        # Apaga só os lotes, nunca a pasta (o .lock segurado seria removido junto).
        # "lote_*" (não "lote_*.jsonl"): pega também o .tmp de uma escrita atômica
        # interrompida no meio, que senão órfão impediria até o rmdir do limpar().
        for velho in pasta.glob("lote_*"):
            try:
                velho.unlink()
            except OSError:
                pass
        # Construção do canal DENTRO do try (ver C4): antes, uma exceção aqui (pasta sem
        # permissão, disco cheio) pulava o finally e prendia o id em _RODANDO para sempre.
        canal = CanalCentral(id_extracao, pasta, geracao=geracao)
        canal.config_transcricao = config_tx
        canal.numero_inquerito = numero_inquerito
        with _TRAVA:
            entrada = _RODANDO.get(id_extracao)
            if entrada is not None and entrada.get("geracao") == geracao:
                # evento de troca de geração: o aceitar() de um redespacho futuro ordena
                # esta passada parar sem esperar o próximo callback 409
                canal._cancelar_extra = entrada["cancelar_troca"]
        try:
            _processar_extracao(id_extracao, caminho, canal=canal)
        except Exception:  # _processar_extracao já reportou o erro por callback; só loga
            logger.exception("Extração %s explodiu fora do relatório", id_extracao)
        finally:
            canal.finalizar_lotes_soltos()
            canal.limpar()
    except Exception as exc:  # noqa: BLE001 — nem chegou a processar (spool injornalável,
        # disco cheio, canal não construído): sem este callback o central ficaria
        # "processando" para sempre esperando entrega que nunca vai existir
        logger.exception("Extração %s não conseguiu começar", id_extracao)
        _reportar_erro(id_extracao, f"O nó não conseguiu preparar a entrega: {exc}")
    finally:
        if fd is not None:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(fd)
        sem.release()
        with _TRAVA:
            # só a NOSSA geração sai: numa troca, o dicionário já é do sucessor — apagar
            # aqui devolveria o "não está rodando" para a passada que acabou de assumir
            if _RODANDO.get(id_extracao, {}).get("geracao") == geracao:
                _RODANDO.pop(id_extracao, None)


def _reportar_erro(id_extracao: int, mensagem: str) -> None:
    """'erro' ao central para falhas que nem chegaram à pipeline (sem canal para o callback)."""
    try:
        requests.post(f"{CanalCentral._base()}/api/no/entrega/{id_extracao}/estado",
                      json={"t": "erro", "mensagem": mensagem[:2000]},
                      headers=CanalCentral._headers(), timeout=(10, 60))
    except Exception:
        logger.warning("Sem central acessível para reportar o erro da extração %s", id_extracao)


def _pasta_entrega(id_extracao: int) -> Path:
    from funcoes.organizacao_arquivos import PASTA_DADOS
    pasta = PASTA_DADOS / ".entrega" / str(id_extracao)
    return pasta


def spool_estado() -> dict:
    """Profundidade do spool de entrega (data/.entrega + reserva em /tmp): quantas extrações
    têm lotes aguardando ack, quantos lotes e quantos bytes. É a métrica que denuncia a
    entrega que NÃO fecha — spool que só cresce com o nó "verde" é rede ruim ou central
    mudo muito antes de virar card vermelho na tela."""
    import tempfile

    pastas = 0
    lotes = 0
    bytes_tot = 0
    rejeitados = 0
    for raiz in (_pasta_entrega(0).parent,
                 Path(tempfile.gettempdir()) / "analyser-entrega"):
        if not raiz.is_dir():
            continue
        try:
            candidatas = [p for p in raiz.iterdir() if p.is_dir()]
        except OSError:
            continue
        for pasta in candidatas:
            try:
                arquivos = list(pasta.glob("lote_*.jsonl"))
                rejeitados += len(list(pasta.glob("*.rejeitado")))
            except OSError:
                continue
            if not arquivos:
                continue
            pastas += 1
            lotes += len(arquivos)
            for a in arquivos:
                try:
                    bytes_tot += a.stat().st_size
                except OSError:
                    pass
    # quarentenas ficam VISÍVEIS mesmo sem lote pendente: era o canto escuro onde um
    # central mal-versionado rejeitava passada após passada sem ninguém ver
    return {"entregas_pendentes": pastas, "lotes": lotes, "bytes": bytes_tot,
            "lotes_rejeitados": rejeitados}


def _pasta_entrega_livre(id_extracao: int) -> Path:
    """Spool de entrega em data/.entrega; se o data/ do nó não for gravável pelo usuário do
    container (instalação que deixou a pasta como root — o Docker cria o bind mount assim),
    cai para o /tmp: a entrega continua, e o log diz o conserto definitivo."""
    pasta = _pasta_entrega(id_extracao)
    try:
        pasta.mkdir(parents=True, exist_ok=True)
        return pasta
    except PermissionError:
        import tempfile
        reserva = Path(tempfile.gettempdir()) / "analyser-entrega" / str(id_extracao)
        reserva.mkdir(parents=True, exist_ok=True)
        logger.warning("data/ sem permissão de escrita no nó — spool de entrega em %s; "
                       "conserto definitivo: chown -R do data do nó para o usuário do "
                       "container (%s)", reserva, pasta.parent)
        return reserva


class CanalCentral(canal_extracao.CanalEntrega):
    """CanalEntrega com os dois botões de HTTP nó→central (Bearer = NO_TOKEN do .env)."""

    def __init__(self, id_extracao: int, pasta: Path, geracao: int = 0):
        super().__init__(id_extracao, pasta,
                         enviar_lote=self._enviar_lote, enviar_estado=self._enviar_estado)
        # geração do despacho (INQ_TB02_EXTRACOES.NU_GERACAO no momento do claim): viaja em
        # toda entrega; o central recusa 409 a passada substituída pelo redespacho
        self.geracao = geracao

    @staticmethod
    def _base() -> str:
        url = (os.getenv("CENTRAL_URL") or "").rstrip("/")
        if not url:
            raise RuntimeError("CENTRAL_URL não configurada no .env do nó")
        return url

    @staticmethod
    def _headers() -> dict:
        token = (os.getenv("NO_TOKEN") or "").strip()
        if not token:
            raise RuntimeError("NO_TOKEN não configurada no .env do nó")
        return {"Authorization": f"Bearer {token}"}

    def _enviar_lote(self, seq: int, corpo: bytes) -> int | None:
        r = requests.post(f"{self._base()}/api/no/entrega/{self.id_extracao}/lote/{seq}",
                          params={"geracao": self.geracao},
                          data=corpo,
                          headers={**self._headers(), "Content-Type": "application/x-ndjson"},
                          timeout=(10, 300))
        if r.status_code == 409:  # extração não está mais 'processando' (apagada/cancelada)
            # ou despacho substituído (geração): considera entregue para não travar o
            # pipeline; a resposta de estado seguinte devolve cancelar e a passada encerra
            from funcoes import metricas_no
            metricas_no.incr("entrega_409")
            self._cancelar = True
            return seq
        if r.status_code == 422:
            # LOTE ENVENENADO: o central nunca vai aceitar este arquivo (linha corrompida
            # ou tipo de registro que ele não conhece — nó/central de versões diferentes).
            # Reenviar o mesmo lote para sempre era o deadlock clássico. Quarentena local
            # (o arquivo sobrevive para diagnóstico) e o erro sobe como estado: a extração
            # vira 'erro' com causa clara, nunca 'concluida' com um lote faltando.
            from funcoes import metricas_no
            metricas_no.incr("lote_quarentena")
            arq = self._arquivo_lote(seq)
            detalhe = ""
            try:
                detalhe = str((r.json() or {}).get("detail", ""))[:500]
            except ValueError:
                pass
            try:
                arq.replace(arq.with_name(f"{arq.stem}.rejeitado"))
            except OSError:
                pass
            logger.error("Central rejeitou o lote %s da extração %s (quarentena em %s): %s",
                         seq, self.id_extracao, arq.name, detalhe)
            self._enviar_estado({"t": "erro",
                                 "mensagem": f"O central rejeitou um lote de dados desta "
                                             f"extração (lote {seq}, gravado como .rejeitado "
                                             f"na pasta do nó): {detalhe}"})
            # considera entregue para a fila andar: o 'erro' acima já fechou a extração no
            # central, e os callbacks seguintes devolvem cancelar — a pipeline para limpo
            return seq
        r.raise_for_status()
        return int(r.json().get("recebido", seq))

    def _enviar_estado(self, payload: dict):
        payload = {**payload, "g": self.geracao}
        r = requests.post(f"{self._base()}/api/no/entrega/{self.id_extracao}/estado",
                          json=payload, headers=self._headers(), timeout=(10, 60))
        if r.status_code == 409:
            self._cancelar = True
            return {"cancelar": True}
        r.raise_for_status()
        return r.json() if r.content else {}

    def finalizar_lotes_soltos(self) -> None:
        """Última chance de entregar o que ficou no disco (queda de rede no meio da fase).
        Se ainda houver lote pendente, o central nunca recebeu o final de verdade — ele vai
        virar 'erro' pelo vigia; os arquivos ficam aqui para um reprocessamento manual."""
        try:
            self.enviar_pendentes()
        except Exception:
            logger.warning("Entrega da extração %s seguiu com lotes pendentes no disco",
                           self.id_extracao)

    def limpar(self) -> None:
        """Extração concluída e entregue: a pasta de entrega não precisa sobreviver."""
        try:
            if not any(self.pasta.glob("lote_*.jsonl")):
                self.pasta.rmdir()
        except OSError:
            pass

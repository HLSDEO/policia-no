"""Transcrição de áudio (WhatsApp/SMS) local, via faster-whisper, sem GPU.

Roda em processos separados (não threads — contorna o GIL para o trabalho
pesado de inferência) para transcrever vários áudios em paralelo. Cada
worker carrega o modelo uma vez e reaproveita para todos os áudios que
processar, evitando pagar o custo de carga a cada arquivo.
"""

import contextlib
import multiprocessing as mp
import os
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from typing import Callable

from funcoes import ufdr_zip
from funcoes.env_utils import parse_env, read_env_text

_modelo_worker = None  # uma instância por processo filho, carregada em _inicializar_worker
_VAD_WORKER = True     # filtro de atividade de voz no transcribe (decidido pelo pai, ver _configuracao)
_BEAM_WORKER = 1       # 1 = busca gulosa (CPU); >1 = feixe largo (ver TRANSCRICAO_BEAM)

# Prioridade dos workers de transcrição na disputa de CPU (nice do Linux; 0 é o padrão dos
# demais processos). Com TRANSCRICAO_THREADS × extrações simultâneas passando do número de
# núcleos, a divisão igualitária esfomeava a thread do relatório, o uvicorn e o Postgres —
# um relatório de segundos levou minutos. nice 10 põe a transcrição por último: ela é
# CPU-bound e só roda com o que sobra, então não perde throughput no longo prazo, mas a
# tela e o relatório voltam a responder sob carga.
_NICE_WORKER = 10

# Vigia do pool de transcrição: sem NENHUMA conclusão neste tempo, os workers estão presos (o
# faster-whisper pode não terminar um áudio corrompido) e o lote é encerrado devolvendo o que já
# veio — os áudios não transcritos continuam pendentes e podem ser reprocessados. Folgado: um
# áudio legítimo longo pode demorar bem mais que isso, mas o disparo exige que TODOS os workers
# estejam ocupados há tanto tempo.
_SEGUNDOS_SEM_COMPLETAR = 2 * 60 * 60
_PASSO_VIGIA_SEGUNDOS = 30  # a cada quanto o vigia recheca cancelamento e inatividade
# Se a fila já acabou (tudo em andamento) e nada conclui neste tempo, algum worker morreu (crash,
# OOM) e a contagem nunca zeraria sozinha — encerra devolvendo o que já veio, em vez de pendurar
# o lote para sempre.
_SEGUNDOS_TAIL_SEM_COMPLETAR = 5 * 60


class ModeloTranscricaoIndisponivel(Exception):
    """O modelo do faster-whisper não está no cache e não pôde ser baixado."""

    def __init__(self, modelo: str, causa: Exception):
        self.modelo = modelo
        super().__init__(
            f"Os áudios não foram transcritos: o modelo de transcrição \"{modelo}\" não está baixado "
            f"no servidor e não foi possível baixá-lo ({causa}). Baixe o modelo e use o botão "
            f"\"Transcrever\" da extração."
        )


def _configuracao(modelo_override: str | None = None) -> tuple[int, str, bool, int]:
    """Lê TRANSCRICAO_THREADS/TRANSCRICAO_MODELO/TRANSCRICAO_VAD/TRANSCRICAO_BEAM do .env, com
    defaults razoáveis. Se modelo_override for fornecido, ele substitui o modelo lido do .env.
    Em CPU puro (o nosso caso), os dois últimos valem dinheiro:
    - VAD (padrão ligado): gravação policial tem 30-70% de silêncio; o filtro de atividade de voz
      pula os trechos mudos antes de passar pelo modelo — em áudio longo isso corta o tempo da
      transcrição praticamente pela metade, sem perder nada do que tem fala;
    - BEAM=1 (padrão): busca gulosa em vez de feixe 5 — ~2-3x mais rápido em CPU, com perda de
      qualidade mínima com o modelo small em português. BEAM>1 volta ao feixe largo."""
    env = parse_env(read_env_text())
    try:
        threads = int(env.get("TRANSCRICAO_THREADS", "4"))
    except ValueError:
        threads = 4
    vad = (env.get("TRANSCRICAO_VAD", "1") or "1").strip().lower() not in ("0", "false", "nao", "off")
    try:
        beam = max(1, int(env.get("TRANSCRICAO_BEAM", "1")))
    except ValueError:
        beam = 1
    modelo = modelo_override or env.get("TRANSCRICAO_MODELO", "small") or "small"
    return max(1, threads), modelo, vad, beam


def _inicializar_worker(modelo: str, vad: bool, beam: int) -> None:
    """Roda uma vez por processo filho do pool: rebaixa a prioridade de CPU, guarda os parâmetros
    de decodificação e carrega o modelo (uma vez por processo, reaproveitada em todos os áudios)."""
    global _modelo_worker, _VAD_WORKER, _BEAM_WORKER
    try:
        os.nice(_NICE_WORKER)
    except OSError:
        pass  # sem permissão para mudar a prioridade: transcrição segue com nice padrão
    from faster_whisper import WhisperModel  # import tardio: só workers de transcrição precisam disso

    _VAD_WORKER, _BEAM_WORKER = vad, beam
    _modelo_worker = WhisperModel(modelo, device="cpu", compute_type="int8", cpu_threads=2)


_ZF_LOCAL_DOS_WORKERS: dict = {}


def _abrir_para_o_worker(caminho_ufdr):
    """ZipFile do áudio atual — com reaproveitamento POR PROCESSO nos handles remotos.

    Localmente reabrir o .ufdr por áudio é barato (o diretório central fica no cache de SO). Por
    sobre a rede não: cada abertura relê o diretório central do pacote (dezenas de MB num .ufdr
    grande), multiplicado por milhares de áudios. O cache vive no processo do worker (spawn = um
    por processo, tasks sequenciais dentro dele) e NÃO é fechado pelo `with` do chamador — o
    nullcontext devolve o mesmo handle sem fechar; o pool termina o processo ao fim do lote."""
    chave = str(caminho_ufdr)
    if getattr(caminho_ufdr, "eh_remoto", False):
        zf = _ZF_LOCAL_DOS_WORKERS.get(chave)
        if zf is None:
            zf = _ZF_LOCAL_DOS_WORKERS[chave] = ufdr_zip.abrir(caminho_ufdr)
        return contextlib.nullcontext(zf)
    return ufdr_zip.abrir(caminho_ufdr)


def _transcrever_um(tarefa: tuple[str, object, str]) -> tuple[str, str | None]:
    """Extrai um áudio do .ufdr para um arquivo temporário e transcreve; devolve (chave, texto|None).

    `caminho_ufdr` é o Path local OU uma `no_ponte.FonteRemota` (áudio de um .ufdr que mora num
    NÓ — docs/no_descentralizado.md): o `_abrir_para_o_worker` resolve os dois, e a fonte remota
    chega aqui ilesa pelo pickle do spawn porque é só dict+string (nenhum handle vivo). O áudio
    em si (alguns MB) atravessa a LAN; o modelo, não — o whisper continua neste host."""
    chave, caminho_ufdr, caminho_zip = tarefa
    try:
        with _abrir_para_o_worker(caminho_ufdr) as zf:
            dados = zf.read(caminho_zip)
    except (KeyError, zipfile.BadZipFile, OSError):
        return chave, None

    sufixo = Path(caminho_zip).suffix or ".opus"
    try:
        with tempfile.NamedTemporaryFile(suffix=sufixo, delete=True) as tmp:
            tmp.write(dados)
            tmp.flush()
            segmentos, _info = _modelo_worker.transcribe(
                tmp.name, language="pt", vad_filter=_VAD_WORKER, beam_size=_BEAM_WORKER)
            texto = " ".join(s.text.strip() for s in segmentos).strip()
    except Exception:
        return chave, None

    return chave, (texto or None)


def transcrever_lote(caminho_ufdr, tarefas: list[tuple[str, str]],
                     modelo_override: str | None = None,
                     deve_cancelar: Callable[[], bool] | None = None,
                     ao_progredir: Callable[[int, int], None] | None = None) -> dict[str, str]:
    """Transcreve vários áudios em paralelo.

    caminho_ufdr: Path local OU `no_ponte.FonteRemota` — o lote inteiro lê de um único .ufdr, e
    desde o passaporte de nó (docs/no_descentralizado.md) ele pode morar na pasta da delegacia;
    quem decide é `_transcrever_audios_pendentes`, que repassa a fonte sem stringificar.

    tarefas: lista de (chave, caminho_zip) — chave é um identificador opaco escolhido por quem
    chama (ex. "wpp:123"), só usado para casar o resultado de volta com a mensagem certa.
    modelo_override: opcional — nome do modelo faster-whisper (ex.: "base", "small", "large-v3").
    Se omitido, usa TRANSCRICAO_MODELO do .env.
    deve_cancelar: opcional — checado a cada áudio que conclui e, enquanto houver áudio em
    andamento, também a cada _PASSO_VIGIA_SEGUNDOS por um vigia (um worker preso não pode segurar
    "Parar extração"); se True, encerra o pool e devolve só o que já tinha sido transcrito
    (cancelamento cooperativo).
    ao_progredir: opcional — chamado com (concluídos, total) a cada áudio que termina, dê certo ou
    não; é o que alimenta a porcentagem no badge da extração. Falha dele nunca derruba o lote.
    Devolve {chave: texto} só para os que deram certo — chaves sem sucesso (erro, arquivo
    corrompido, sem fala reconhecível) ficam de fora e continuam com transcrição nula no banco.

    O despacho é por janela deslizante (apply_async + callback) em vez de imap_unordered: um
    worker preso num áudio emperra o imap na espera do resultado e a checagem de cancelamento —
    que só rodava quando um resultado chegava — nunca acontecia; aqui a checagem corre a cada
    conclusão e num vigia periódico, e o pool inteiro preso é encerrado após _SEGUNDOS_SEM_COMPLETAR
    sem conclusões, devolvendo o que já veio.
    """
    if not tarefas:
        return {}

    threads, modelo, vad, beam = _configuracao(modelo_override)
    n_workers = min(threads, len(tarefas))
    entradas = [(chave, caminho_ufdr, caminho_zip) for chave, caminho_zip in tarefas]
    total = len(entradas)

    # Baixa/verifica o modelo UMA vez no processo pai antes de subir os workers.
    # Sem isso, todos os N workers tentam baixar o mesmo modelo (~500MB) do
    # HuggingFace Hub ao mesmo tempo na primeira execução (cache frio), o que é
    # lento, desperdiça banda e pode travar em contenção de lock de arquivo.
    from faster_whisper.utils import download_model

    try:
        download_model(modelo)
    except Exception as exc:
        # sem internet e sem o modelo no cache: não é falha da extração, só da transcrição —
        # quem chama transforma isso em aviso e mantém a extração concluída
        raise ModeloTranscricaoIndisponivel(modelo, exc) from exc

    resultado: dict[str, str] = {}
    concluidos = 0
    cancelado = False
    ativos = 0
    ultimo_retorno = time.monotonic()
    trava = threading.Lock()
    fim = threading.Event()

    ctx = mp.get_context("spawn")
    pool = ctx.Pool(n_workers, initializer=_inicializar_worker, initargs=(modelo, vad, beam))
    fila_tarefas = iter(entradas)

    def _despachar() -> None:
        """Enfileira o próximo áudio da fila, se ainda houver (janela cheia). Só sob a trava."""
        nonlocal ativos
        tarefa = next(fila_tarefas, None)
        if tarefa is None:
            return
        ativos += 1
        pool.apply_async(_transcrever_um, (tarefa,), callback=_ao_concluir)

    def _ao_concluir(res) -> None:
        """Roda na thread de resultados do pool a cada áudio que termina — não fica bloqueada por
        áudio travado, então o cancelamento é visto assim que QUALQUER áudio conclui. Em
        apply_async, o callback recebe direto o valor de retorno da tarefa, não o AsyncResult."""
        nonlocal ativos, concluidos, cancelado, ultimo_retorno
        chave, texto = res
        with trava:
            ativos -= 1
            concluidos += 1
            ultimo_retorno = time.monotonic()
            if ao_progredir:
                try:
                    ao_progredir(concluidos, total)
                except Exception:
                    pass
            if not cancelado:
                if deve_cancelar and deve_cancelar():
                    cancelado = True
                    pool.terminate()
                    fim.set()
                else:
                    if texto:
                        resultado[chave] = texto
                    _despachar()
            if ativos == 0:
                fim.set()

    def _vigia() -> None:
        """Vigia do pool (thread daemon): enquanto houver áudio em andamento, recheca o
        cancelamento a cada _PASSO_VIGIA_SEGUNDOS — um worker preso não pode atrasar "Parar
        extração" — e encerra o lote (devolvendo o que já veio) se nada concluir por
        _SEGUNDOS_SEM_COMPLETAR (pool todo travado) ou por _SEGUNDOS_TAIL_SEM_COMPLETAR com a
        fila já exaurida (worker morto que nunca responde)."""
        while not fim.wait(_PASSO_VIGIA_SEGUNDOS):
            nonlocal cancelado
            with trava:
                ocupado = ativos > 0
                parado = time.monotonic() - ultimo_retorno > _SEGUNDOS_SEM_COMPLETAR
                fila_exaurida = concluidos + ativos >= total
                tail_parado = (fila_exaurida and
                               time.monotonic() - ultimo_retorno > _SEGUNDOS_TAIL_SEM_COMPLETAR)
                pedido = bool(deve_cancelar and deve_cancelar())
                if (parado or tail_parado or pedido) and ocupado and not cancelado:
                    cancelado = True
                    pool.terminate()
                    fim.set()

    try:
        with trava:
            for _ in range(n_workers):
                _despachar()
        threading.Thread(target=_vigia, daemon=True).start()
        fim.wait()
    finally:
        pool.terminate()
        pool.join()
    return resultado

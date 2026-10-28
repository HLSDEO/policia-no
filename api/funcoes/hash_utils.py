"""Hash (MD5/SHA-1/SHA-256) de arquivos de origem, para cadeia de custódia — ver db/init/25_rif.sql."""
import contextlib
import hashlib
import json
import os
import queue
import threading
from collections.abc import Callable, Sequence
from pathlib import Path

_TAMANHO_BLOCO = 8 * 1024 * 1024  # 8 MiB — .ufdr pode ter GBs; nunca carrega o arquivo inteiro na memória
_FILA_LIMITE = 2  # blocos enfileirados por algoritmo: limita a memória a ~n_algoritmos × 2 blocos
_FIM = object()   # sentinela: "acabaram os blocos"

# usedforsecurity=False no MD5/SHA-1: é digesto de custódia, não construtores de segurança —
# em builds OpenSSL com FIPS isso libera o caminho rápido (e documenta que ninguém usa isto
# para integridade contra atacante).
_FABRICAS = {
    "md5": lambda: hashlib.md5(usedforsecurity=False),
    "sha1": lambda: hashlib.sha1(usedforsecurity=False),
    "sha256": hashlib.sha256,
}


class HasheadorStreaming:
    """Alimenta hashers incrementais conforme os bytes passam — para hashear DURANTE a gravação
    de um upload/cópia e eliminar a passada de leitura que viria depois (o arquivo escrito é,
    byte a byte, o arquivo hasheado). Sequencial por desenho: o gargalo de um upload é o disco de
    destino, e os digestos baratos (md5+sha1) somam ~350 MB/s de CPU, acima de qualquer gravação
    real."""

    def __init__(self, algoritmos: Sequence[str] = ("md5", "sha1", "sha256")) -> None:
        algoritmos = tuple(dict.fromkeys(algoritmos))
        desconhecidos = set(algoritmos) - set(_FABRICAS)
        if desconhecidos or not algoritmos:
            raise ValueError(f"algoritmo(s) de hash desconhecido(s): {sorted(desconhecidos) or '(nenhum)'}")
        self._algoritmos = algoritmos
        self._hashers = {nome: _FABRICAS[nome]() for nome in algoritmos}

    def atualizar(self, bloco: bytes) -> None:
        for hasher in self._hashers.values():
            hasher.update(bloco)

    def finalizar(self) -> dict:
        return {nome: self._hashers[nome].hexdigest() for nome in self._algoritmos}


def _consumidor_blocos(fila: queue.Queue, algoritmo: str,
                       resultados: dict, erros: dict) -> None:
    """Um thread por algoritmo: consome os blocos da fila e atualiza seu hasher. O GIL é liberado
    dentro de update() (OpenSSL), então os algoritmos correm em paralelo de verdade, cada um num
    núcleo — o passe inteiro cai de `soma dos algoritmos` para `max deles`."""
    hasher = _FABRICAS[algoritmo]()
    try:
        while True:
            bloco = fila.get()
            if bloco is _FIM:
                break
            hasher.update(bloco)
        resultados[algoritmo] = hasher.hexdigest()
    except BaseException as exc:  # a leitura adiante precisa morrer junto, não dar join em vazio
        erros[algoritmo] = exc


def calcular_hashes_arquivo(caminho: Path, ao_progredir: Callable[[int, int], None] | None = None,
                            algoritmos: Sequence[str] = ("md5", "sha1", "sha256")) -> dict:
    """Lê o arquivo em UMA passada e devolve {algoritmo: hex} para os algoritmos pedidos
    (padrão: {"md5", "sha1", "sha256"}).

    ao_progredir: callback opcional (feitos, total) em bytes, chamado a cada bloco lido — um
    .ufdr de dezenas de GB demora minutos só nesta passada, e sem feedback a tela fica parada
    em 0% antes de qualquer extração começar.

    A leitura é de UM thread (uma passada no disco, como sempre foi); cada algoritmo roda num
    thread próprio alimentado pela MESMA sequência de blocos via fila limitada. Antes os três
    updates aconteciam em série no thread leitor — 1 núcleo para 3 hashes, e o passe de hash de
    um .ufdr grande era CPU-bound justamente nisso. Com o GIL liberado dentro do update() do
    OpenSSL, os threads rodam em paralelo e o tempo cai para o do algoritmo mais lento."""
    algoritmos = tuple(dict.fromkeys(algoritmos))
    desconhecidos = set(algoritmos) - set(_FABRICAS)
    if desconhecidos or not algoritmos:
        raise ValueError(f"algoritmo(s) de hash desconhecido(s): {sorted(desconhecidos) or '(nenhum)'}")

    total = caminho.stat().st_size
    feitos = 0
    resultados: dict[str, str] = {}
    erros: dict[str, BaseException] = {}
    filas = [queue.Queue(maxsize=_FILA_LIMITE) for _ in algoritmos]
    consumidores = [
        threading.Thread(target=_consumidor_blocos, args=(fila, nome, resultados, erros),
                         daemon=True, name=f"hash-{nome}")
        for fila, nome in zip(filas, algoritmos)
    ]
    for thread in consumidores:
        thread.start()
    try:
        with open(caminho, "rb") as f:
            for bloco in iter(lambda: f.read(_TAMANHO_BLOCO), b""):
                if erros:
                    raise erros[next(iter(erros))]
                for fila in filas:
                    while True:
                        try:
                            fila.put(bloco, timeout=1)
                            break
                        except queue.Full:
                            if erros:
                                raise erros[next(iter(erros))] from None
                feitos += len(bloco)
                if ao_progredir:
                    ao_progredir(feitos, total)
    finally:
        for fila in filas:
            # put com drenagem: se um consumidor morreu com erro, a fila pode estar cheia e o
            # _FIM nunca entraria — o join penduraria o passe inteiro. Quem chegou até aqui com
            # erro não quer o resultado de nenhum dos algoritmos.
            while True:
                try:
                    fila.put(_FIM, timeout=1)
                    break
                except queue.Full:
                    with contextlib.suppress(queue.Empty):
                        fila.get_nowait()
        for thread in consumidores:
            thread.join()
    if erros:
        raise erros[next(iter(erros))]
    if ao_progredir:
        ao_progredir(total, total)
    return {nome: resultados[nome] for nome in algoritmos}


# ------------------------------------------------------------------ sidecar de upload
#
# Upload/cópia hasheada DURANTE a gravação (HasheadorStreaming) não precisa da passada de
# leitura depois — mas quem recebe o arquivo (salvar_ufdr_enviado) muitas vezes ainda não tem
# a linha da extração para onde gravar os digestos. Eles vão para um `<arquivo>.hashes.json`
# ao lado, que a importação lê no lugar do passe de hash quando o tamanho confere. Não casa
# com o glob("*.ufdr") do seletor de arquivos, e é sempre rejeitado se o .ufdr mudou de
# tamanho desde então (arquivo editado/copiado por cima = digestos velhos não valem).

_SUFIXO_SIDECAR = ".hashes.json"


def _caminho_sidecar(caminho: Path) -> Path:
    return caminho.with_name(caminho.name + _SUFIXO_SIDECAR)


def gravar_sidecar_hashes(caminho: Path, hashes: dict, tamanho: int) -> None:
    """Melhor esforço: um sidecar que não foi gravado só custa a passada de hash depois."""
    conteudo = {**{k: hashes[k] for k in hashes if k in _FABRICAS}, "tamanho": tamanho}
    sidecar = _caminho_sidecar(caminho)
    try:
        temporario = sidecar.with_name(sidecar.name + ".parcial")
        temporario.write_text(json.dumps(conteudo), encoding="utf-8")
        os.replace(temporario, sidecar)
    except OSError:
        pass


def ler_sidecar_hashes(caminho: Path) -> dict | None:
    """Os digestos do sidecar, se existirem e baterem com o arquivo de agora (mesmo tamanho) —
    md5 e sha1 são obrigatórios; sha256 é bem-vindo se estiver lá. None = hashear de verdade."""
    try:
        dados = json.loads(_caminho_sidecar(caminho).read_text(encoding="utf-8"))
        if dados.get("tamanho") != caminho.stat().st_size:
            return None
        if not dados.get("md5") or not dados.get("sha1"):
            return None
        resultado = {"md5": dados["md5"], "sha1": dados["sha1"]}
        if dados.get("sha256"):
            resultado["sha256"] = dados["sha256"]
        return resultado
    except (OSError, ValueError):
        return None


def remover_sidecar_hashes(caminho: Path) -> None:
    with contextlib.suppress(OSError):
        _caminho_sidecar(caminho).unlink(missing_ok=True)


def calcular_hashes_bytes(conteudo: bytes) -> dict:
    """Mesma coisa, para conteúdo já em memória (ex.: upload)."""
    return {
        "md5": hashlib.md5(conteudo).hexdigest(),
        "sha1": hashlib.sha1(conteudo).hexdigest(),
        "sha256": hashlib.sha256(conteudo).hexdigest(),
    }

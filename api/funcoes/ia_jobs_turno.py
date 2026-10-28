"""Coletores de jobs pesados disparados por ferramentas da IA durante um turno de chat.

Ferramentas de ação (hoje só ``processar_texto_em_imagens``) disparam um job de background e o turno
do chat não pode esperar por ele: a IA recebe o ``id_job`` e o turno encerra com uma mensagem só. Para
que o investigador consiga acompanhar o resultado DEPOIS (cartão de progresso na bolha, que sobrevive
ao F5), o turno precisa saber quais jobs ele produziu e gravá-los junto da resposta.

As ferramentas de ação rodam em threads do pool de function calling (``ia_provedores.
_executar_ferramentas_em_paralelo``), não na thread do worker — por isso o acumulador é um dicionário
global protegido por lock, indexado pelo ``id_processamento`` do turno, e não um thread-local. O worker
chama :func:`coletar` uma vez ao gravar a resposta, o que também limpa a entrada do turno.

Nada aqui toca o banco: é só o corredor em memória entre a ferramenta (que produz o id_job) e o worker
(que o persiste na mensagem).
"""

from __future__ import annotations

import threading

_lock = threading.Lock()
_por_processamento: dict[int, list[dict]] = {}


def registrar(id_processamento: int | None, id_job: int, tp_job: str,
              id_extracao: int | None = None) -> None:
    """Anota que o turno ``id_processamento`` disparou o job ``id_job``. No-op sem turno (ex.: a
    mesma ferramenta chamada fora do chat, sem processamento por trás). ``id_extracao`` (quando o
    recorte é de um único aparelho) entra no ``CO_JOBS`` para o link do cartão abrir o aparelho certo."""
    if not id_processamento or not id_job:
        return
    entrada = {"id_job": int(id_job), "tp_job": tp_job}
    if id_extracao is not None:
        entrada["id_extracao"] = int(id_extracao)
    with _lock:
        _por_processamento.setdefault(int(id_processamento), []).append(entrada)


def coletar(id_processamento: int | None) -> list[dict] | None:
    """Devolve (e remove) os jobs acumulados do turno; ``None`` se nenhum foi disparado — é o valor
    que vai para ``CO_JOBS`` quando não há cartão a desenhar."""
    if not id_processamento:
        return None
    with _lock:
        jobs = _por_processamento.pop(int(id_processamento), None)
    return jobs or None

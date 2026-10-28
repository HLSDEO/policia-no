"""Corrige o supervisor de workers do uvicorn (--workers) que mata worker vivo por engano.

O uvicorn >=0.30 (uvicorn/supervisors/multiprocess.py) faz no processo-PAI um ping/pong por
Pipe a cada 0.5s com timeout de 5s: se o filho não responde o "pong" a tempo, o pai considera
o worker "hung" e chama process.kill() — SIGKILL, sem traceback, sem rastro no syslog. O pong
roda em thread daemon dentro do worker, e GIL + GC de um heap grande NÃO são preemptáveis o
suficiente: uma extração de .ufdr grande (o parse do report.xml segura a GIL e faz o RSS
crescer GB a GB) atrasa o pong além dos 5s e o pai mata um worker perfeitamente vivo — a
extração morre junto (era a "morte silenciosa" aos ~78s das importações de 300 GB, com o resto
do container ótimo de saúde; o mesmo worker reencarnado batia ponto normalmente).

O Python importa este sitecustomize automaticamente em TODO interpretador — é o único ponto
que executa no PAI antes de o supervisor nascer (main:app só é importado nos filhos, via
spawn). O patch troca só o ping: o worker passa a ser considerado vivo enquanto o processo
existe (o que o waitpid do próprio multiprocessing já sabe); a parte útil do supervisor —
reencarnar worker realmente morto — continua intacta.
"""
import sys


def _aplicar() -> None:
    # só no processo que roda o uvicorn (o PAI tem 'uvicorn' no argv; os filhos spawn_main
    # não precisam do patch, e scripts soltos (healthcheck, psql, tools) não pagam o import)
    if not any("uvicorn" in argumento for argumento in sys.argv):
        return
    try:
        from uvicorn.supervisors import multiprocess
    except Exception:
        return
    processo = getattr(multiprocess, "Process", None)
    if processo is None or not hasattr(processo, "ping"):
        return  # uvicorn mudou por dentro: sem patch, sem surpresa

    def _ping(self, timeout: float = 5.0) -> bool:
        return self.process.is_alive()

    processo.ping = _ping  # type: ignore[method-assign]


_aplicar()

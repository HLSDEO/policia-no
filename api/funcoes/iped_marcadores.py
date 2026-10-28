"""Marcadores do perito de um caso do IPED — o resultado da análise humana sobre o caso.

Dentro do IPED, o perito seleciona itens e os agrupa em "bookmarks" com nome e comentário
("material de interesse", "conversas do alvo"). É a triagem que já foi feita, e até aqui a
importação a ignorava: o investigador recebia a pasta-caso com centenas de milhares de itens
sem nenhuma pista de quais o perito tinha separado.

Onde ficam: `<caso>/iped/bookmarks.iped` — serialização de objeto Java, não banco nem XML
(ver `Bookmarks.saveState` no iped-engine). Ler isso do lado Python significaria
reimplementar o formato, então a leitura roda num subprocesso Java
(`api/tools/iped_dumper/IpedBookmarks.java`), que usa o desserializador oficial com os jars
do próprio caso no classpath.

TUDO AQUI É OPCIONAL. Caso nunca marcado (o normal em entrega automática), caso processado
por uma versão de IPED que mudou a classe, Java ausente, arquivo corrompido: qualquer um
desses devolve `Marcadores` vazio e a importação segue igual. Nenhuma exceção sobe.
"""

import json
import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# Mesmas variáveis de ambiente que iped_utils usa para achar o dumper e o java, para não haver
# duas configurações para a mesma coisa.
_DUMPER_DIR = Path(os.environ.get("IPED_DUMPER_DIR", "/app/tools/iped_dumper"))
_JAVA_BIN = os.environ.get("IPED_JAVA_BIN", "java")

# Teto de segurança. Um caso pode ter todos os itens marcados; o dicionário resultante fica em
# memória durante a importação, e acima disso o custo não se paga (com meio milhão de itens
# marcados, "marcado" deixou de ser um filtro útil de qualquer forma).
_MAX_ITENS = 500_000

# O subprocesso lê um arquivo e escreve JSON; não faz I/O de rede nem varre o índice. Se
# passar disso, algo travou e não vale segurar a importação.
_TIMEOUT_SEGUNDOS = 300


class Marcadores:
    """Marcadores de um caso. `por_item` mapeia o id do item do caso para (nomes, selecionado)."""

    def __init__(self):
        self.nomes: dict[int, str] = {}
        self.comentarios: dict[int, str] = {}
        self.por_item: dict[int, tuple[str, bool]] = {}

    def __bool__(self) -> bool:
        return bool(self.por_item)

    def de(self, id_item) -> tuple[str | None, bool | None]:
        """(marcadores separados por vírgula, selecionado) do item, ou (None, None).

        Aceita o id como string porque é assim que ele chega do índice."""
        if not self.por_item or id_item is None:
            return (None, None)
        try:
            chave = int(id_item)
        except (TypeError, ValueError):
            return (None, None)
        achado = self.por_item.get(chave)
        return achado if achado is not None else (None, None)

    @property
    def total(self) -> int:
        return len(self.por_item)


def ler(pasta_modulo: Path, classpath: str) -> Marcadores:
    """Lê os marcadores do caso. Devolve sempre um `Marcadores` — vazio quando não há nada a
    ler ou quando qualquer coisa dá errado.

    `classpath` é o mesmo que a importação monta para o dumper (os jars do PRÓPRIO caso mais a
    pasta das classes compiladas) — o desserializador precisa da versão do iped-engine que
    gravou o arquivo.

    O estado principal é <caso>/bookmarks.iped; quando ele não rende nada (versão incompatível,
    arquivo corrompido), tenta os backups que o próprio IPED mantém em bkp/bookmarks.N.bkp.iped
    (do mais novo para o mais velho) — o laudo pode ser recuperável de um estado anterior."""
    arquivo = pasta_modulo / "bookmarks.iped"
    candidatos = [arquivo]
    try:
        candidatos += sorted((pasta_modulo / "bkp").glob("bookmarks.*.bkp.iped"), reverse=True)
    except OSError:
        pass

    for candidato in candidatos:
        marcadores = _ler_estado(pasta_modulo, classpath, candidato)
        if marcadores:
            return marcadores
    return Marcadores()


def _ler_estado(pasta_modulo: Path, classpath: str, arquivo: Path) -> Marcadores:
    """Tenta ler UM arquivo de estado (o principal ou um backup) via subprocesso Java.
    Devolve o `Marcadores` se rendeu itens, vazio caso contrário — nunca levanta."""
    marcadores = Marcadores()
    if not arquivo.is_file():
        return marcadores  # caso nunca marcado: o normal, não é erro

    try:
        proc = subprocess.run(
            [_JAVA_BIN, "-Djava.awt.headless=true", "-cp", classpath, "IpedBookmarks",
             str(pasta_modulo), str(arquivo)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=_TIMEOUT_SEGUNDOS,
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        logger.warning("Nuvem IPED: não foi possível ler os marcadores de %s", pasta_modulo,
                       exc_info=True)
        return marcadores

    if proc.returncode != 0:
        # versão de IPED incompatível com a classe, arquivo truncado, AWT indisponível
        logger.warning("Nuvem IPED: leitura de marcadores de %s falhou (rc=%s): %s",
                       arquivo.name, proc.returncode, (proc.stderr or "").strip()[-500:])
        return marcadores

    for linha in (proc.stdout or "").splitlines():
        linha = linha.strip()
        if not linha:
            continue
        try:
            registro = json.loads(linha)
        except ValueError:
            continue
        if registro.get("tipo") == "marcador":
            ident = registro.get("id")
            if isinstance(ident, int):
                marcadores.nomes[ident] = (registro.get("nome") or "")[:300]
                if registro.get("comentario"):
                    marcadores.comentarios[ident] = registro["comentario"][:2000]
        elif registro.get("tipo") == "item":
            if len(marcadores.por_item) >= _MAX_ITENS:
                continue
            ident = registro.get("id")
            if not isinstance(ident, int):
                continue
            nomes = [n for n in (registro.get("marcadores") or []) if n]
            selecionado = bool(registro.get("selecionado"))
            texto = ", ".join(nomes)[:2000] or None
            if texto or selecionado:
                marcadores.por_item[ident] = (texto, selecionado)

    if marcadores.por_item:
        logger.info("Nuvem IPED: %s itens marcados pelo perito em %s marcador(es)",
                    len(marcadores.por_item), len(marcadores.nomes))
    return marcadores

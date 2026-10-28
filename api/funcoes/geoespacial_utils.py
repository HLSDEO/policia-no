"""Camadas geoespaciais (shapefile) desenhadas sobre o mapa de localizações.

**O que é.** O analista traz de fora um `.shp` com polígonos — perímetro de uma operação, limite
de bairro, setor censitário, área de risco, gleba — e ele passa a valer como camada de fundo do
mapa da aba "Grafo" das localizações, com duas perguntas respondidas em cima dele: *quantos
registros caíram dentro de cada área* e *me mostre só o que está dentro desta área*.

**Um "arquivo .shp" nunca é um arquivo só.** O formato da ESRI é um conjunto que anda junto:

| extensão | conteúdo                          | obrigatório |
|----------|-----------------------------------|-------------|
| `.shp`   | a geometria                       | sim         |
| `.dbf`   | a tabela de atributos (dBASE)     | sim, na prática — sem ela a feição não tem nome |
| `.shx`   | índice de acesso direto ao `.shp` | não (lemos sequencialmente) |
| `.prj`   | a projeção, em WKT                | não, mas sem ela não dá para saber onde o polígono cai |
| `.cpg`   | a codificação de texto do `.dbf`  | não |

Por isso a importação aceita tanto um `.zip` com o conjunto quanto os arquivos soltos
selecionados de uma vez.

**Projeção.** Shapefile brasileiro raramente vem em lat/lon: o comum é SIRGAS 2000 / UTM, cujas
coordenadas são metros na casa dos milhões — jogadas cruas num mapa, o polígono some do planeta.
O `.prj` é lido e a geometria é reprojetada para WGS 84 (EPSG:4326) na importação, que é o mesmo
referencial de LOC_TB01. Daqui para baixo, no banco e na tela, tudo já é lat/lon.

**Sem PostGIS.** O Postgres do projeto é o `pgvector/pgvector:pg16` — só tem a extensão `vector`
(da busca semântica), não PostGIS. A contenção ponto-em-área
usa o tipo `POLYGON` nativo e o operador `@>`, com a caixa envolvente gravada ao lado de cada
anel para rejeitar barato antes de percorrer os vértices (ver db/init/15_localizacao.sql).
"""

import hashlib
import io
import json
import logging
import re
import zipfile
from datetime import date, datetime

import shapefile  # pyshp
from pyproj import CRS, Transformer
from pyproj.exceptions import CRSError

from funcoes.db_utils import get_connection

# O recorte da contagem tem de ser EXATAMENTE o mesmo da tabela e do grafo — se divergir, a
# contagem por área passa a responder outra pergunta que a da tela. Por isso o construtor de
# WHERE é reaproveitado daqui em vez de reescrito.
from funcoes.investigacao_utils import _JOIN_GEO_LOCALIZACAO, _where_localizacao

logger = logging.getLogger(__name__)

# Teto do upload: o limite geral (UPLOAD_DOCUMENTO_MB, ajustável em Administração →
# Desempenho), aplicado ao TOTAL somando todos os arquivos do conjunto — um `.zip` único ou os
# arquivos soltos (.shp + .dbf + .prj + .shx). É total, e não por arquivo: o conjunto anda junto
# e barrar um .shp isolado pelo teto seria recusar um envio que cabe no geral.
# Um shapefile de perímetro tem alguns KB; um de setores censitários de capital inteira passa de
# 100 MB e não é o caso de uso (o analista recorta a área antes de trazer) — os tetos de
# feições/vértices abaixo continuam valendo como rede de proteção do processamento.
# Só o fallback de _limite_upload_bytes — igual ao padrão geral (50 MB), para não voltar a um
# teto próprio que o analista não enxerga em Administração.
TAMANHO_MAX_BYTES = 50 * 1024 * 1024


def _limite_upload_bytes() -> int:
    """Limite geral vigente, em bytes — o mesmo da aba Arquivo genérico.

    Lido na hora via documentos_utils (vale sem reiniciar); cai para TAMANHO_MAX_BYTES quando
    indisponível. Import lazy de propósito: documentos_utils puxa investigacao_utils, e importar
    no topo formaria ciclo."""
    try:
        from funcoes.documentos_utils import limite_upload_bytes

        return limite_upload_bytes()
    except Exception:
        return TAMANHO_MAX_BYTES


MAX_FEICOES = 5000
# Teto de vértices depois da simplificação. Acima disso o desenho trava o navegador — é a tela,
# não o banco, que impõe o limite.
MAX_VERTICES = 400_000
# Aborta a leitura antes de estourar a memória do container quando o arquivo é grande demais.
MAX_VERTICES_BRUTOS = 3_000_000

# Simplificação (Douglas-Peucker) em graus. A primeira é ~0,2 m: invisível mesmo no zoom máximo
# do Leaflet (~0,3 m/pixel), e já corta a redundância de vértice que malha oficial costuma ter.
# As seguintes só entram se a camada continuar acima de MAX_VERTICES.
TOLERANCIAS = [0.000002, 0.00001, 0.00003, 0.0001, 0.0003]

_EXTENSOES = ("shp", "shx", "dbf", "prj", "cpg")

# tipos de shape do formato ESRI que são polígono (o "Z"/"M" só acrescenta altitude/medida)
_TIPOS_POLIGONO = {5, 15, 25}
_NOMES_TIPO = {
    0: "nulo", 1: "ponto", 3: "linha", 5: "polígono", 8: "multiponto",
    11: "pontoZ", 13: "linhaZ", 15: "polígonoZ", 18: "multipontoZ",
    21: "pontoM", 23: "linhaM", 25: "polígonoM", 28: "multipontoM", 31: "multipatch",
}

# campos do .dbf que costumam trazer o nome da área, do mais específico ao mais genérico
_CAMPOS_ROTULO = re.compile(
    r"^(nm_|no_|nome|name|titulo|title|label|rotulo|descr|desc$|bairro|municipio|localidade|"
    r"logradouro|zona|setor|regiao|area)", re.IGNORECASE
)

_COR_PADRAO = "#f59e0b"
_RE_COR = re.compile(r"^#[0-9a-fA-F]{6}$")


# ------------------------------------------------------------------ leitura do arquivo


def _decodificar(dados: bytes) -> str:
    for codec in ("utf-8", "latin-1"):
        try:
            return dados.decode(codec).strip()
        except UnicodeDecodeError:
            continue
    return dados.decode("utf-8", errors="replace").strip()


def _separar_arquivos(enviados: list[tuple[str, bytes]]) -> dict[str, bytes]:
    """Normaliza o que veio do navegador num dicionário extensão -> bytes.

    Aceita um `.zip` com o conjunto dentro (o caso comum, porque é assim que shapefile circula)
    ou os arquivos soltos. Dentro do zip só um conjunto por vez: dois `.shp` no mesmo pacote são
    duas camadas diferentes e importá-las como uma só misturaria coisas que não se misturam."""
    partes: dict[str, bytes] = {}

    for nome, dados in enviados:
        extensao = (nome.rsplit(".", 1)[-1] if "." in nome else "").lower()
        if extensao == "zip":
            partes.update(_abrir_zip(dados))
        elif extensao in _EXTENSOES:
            partes[extensao] = dados

    if "shp" not in partes:
        raise ValueError(
            "Nenhum .shp encontrado. Envie o .zip do shapefile ou selecione de uma vez os arquivos "
            "do conjunto (.shp, .dbf, .prj e .shx)."
        )
    if "dbf" not in partes:
        raise ValueError(
            "Falta o .dbf do shapefile — é ele que traz o nome e os atributos de cada área. "
            "Envie o conjunto completo."
        )
    return partes


def _abrir_zip(dados: bytes) -> dict[str, bytes]:
    try:
        pacote = zipfile.ZipFile(io.BytesIO(dados))
    except zipfile.BadZipFile:
        raise ValueError("O arquivo .zip está corrompido ou não é um zip.")

    membros = [m for m in pacote.namelist() if not m.endswith("/")]
    shps = [m for m in membros if m.lower().endswith(".shp")]
    if not shps:
        raise ValueError("O .zip não contém nenhum .shp.")
    if len(shps) > 1:
        nomes = ", ".join(sorted(m.rsplit("/", 1)[-1] for m in shps))
        raise ValueError(
            f"O .zip tem mais de um shapefile ({nomes}). Importe um de cada vez — cada .shp vira "
            "uma camada própria."
        )

    base = shps[0][: -len(".shp")]
    partes: dict[str, bytes] = {}
    for membro in membros:
        if not membro.startswith(base + "."):
            continue
        extensao = membro.rsplit(".", 1)[-1].lower()
        if extensao in _EXTENSOES:
            partes[extensao] = pacote.read(membro)
    return partes


def _codificacao_dbf(partes: dict[str, bytes]) -> str:
    """Codificação do texto do .dbf: o que o .cpg disser, senão UTF-8 (o pyshp cai para os bytes
    crus no que não decodificar, porque passamos encodingErrors='replace')."""
    cpg = _decodificar(partes["cpg"]).lower() if "cpg" in partes else ""
    if not cpg:
        return "utf-8"
    if "8859-1" in cpg or "latin" in cpg or cpg in ("iso88591", "ansi", "1252", "cp1252", "windows-1252"):
        return "latin-1"
    if "utf" in cpg:
        return "utf-8"
    try:
        "teste".encode(cpg)
        return cpg
    except LookupError:
        return "utf-8"


# ------------------------------------------------------------------ projeção


def _transformador(prj_texto: str | None):
    """Devolve (transformador, nome_da_projecao). Sem `.prj`, devolve (None, None) e as
    coordenadas são aceitas como lat/lon se estiverem na faixa — ver `_projetar`."""
    if not prj_texto:
        return None, None
    try:
        crs = CRS.from_user_input(prj_texto)
    except (CRSError, ValueError) as exc:
        raise ValueError(
            f"Não foi possível entender a projeção do .prj deste shapefile ({exc}). "
            "Reprojete a camada para WGS 84 ou SIRGAS 2000 antes de importar."
        )
    # always_xy: o shapefile guarda (x, y) = (leste, norte) / (longitude, latitude), e sem esta
    # opção o pyproj entrega lat/lon na ordem do EPSG — que para 4326 é (lat, lon), invertida.
    return Transformer.from_crs(crs, "EPSG:4326", always_xy=True), crs.name


def _projetar(x: float, y: float, transformador) -> tuple[float, float] | None:
    """(x, y) da origem -> (longitude, latitude) em WGS 84. None quando o ponto não é aproveitável."""
    if transformador is not None:
        x, y = transformador.transform(x, y)
    if x != x or y != y:  # NaN: o pyproj devolve inf/NaN para ponto fora do domínio da projeção
        return None
    if not (-180.0 <= x <= 180.0 and -90.0 <= y <= 90.0):
        return None
    return (round(x, 6), round(y, 6))


# ------------------------------------------------------------------ simplificação


def _distancia_ao_segmento(p, a, b) -> float:
    """Distância perpendicular de p ao segmento ab, em graus (planar — a tolerância é pequena o
    bastante para a curvatura não importar)."""
    ax, ay = a
    bx, by = b
    px, py = p
    dx, dy = bx - ax, by - ay
    if dx == 0.0 and dy == 0.0:  # anel fechado: o primeiro e o último ponto coincidem
        return ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    return ((px - (ax + t * dx)) ** 2 + (py - (ay + t * dy)) ** 2) ** 0.5


def _simplificar(pontos: list[tuple[float, float]], tolerancia: float) -> list[tuple[float, float]]:
    """Douglas-Peucker iterativo (sem recursão: anel de malha oficial tem dezenas de milhares de
    vértices e estouraria a pilha do Python)."""
    if tolerancia <= 0 or len(pontos) < 3:
        return pontos

    manter = [False] * len(pontos)
    manter[0] = manter[-1] = True
    pilha = [(0, len(pontos) - 1)]

    while pilha:
        inicio, fim = pilha.pop()
        if fim <= inicio + 1:
            continue
        pior, distancia_maxima = -1, tolerancia
        for i in range(inicio + 1, fim):
            d = _distancia_ao_segmento(pontos[i], pontos[inicio], pontos[fim])
            if d > distancia_maxima:
                pior, distancia_maxima = i, d
        if pior < 0:
            continue
        manter[pior] = True
        pilha.append((inicio, pior))
        pilha.append((pior, fim))

    return [p for p, fica in zip(pontos, manter) if fica]


def _simplificar_geometria(geometria: dict, tolerancia: float) -> tuple[dict | None, int]:
    """Aplica a simplificação a todos os anéis. Devolve (geometria, nº de vértices); a geometria é
    None quando não sobrou anel válido (área menor que a própria tolerância)."""
    poligonos_saida, total = [], 0
    for aneis in geometria["coordinates"]:
        novos = []
        for indice, anel in enumerate(aneis):
            simplificado = _simplificar(anel, tolerancia)
            # anel fechado precisa de 3 vértices distintos + a repetição do primeiro
            if len(simplificado) < 4:
                if indice == 0:
                    novos = []
                    break
                continue  # buraco que sumiu na simplificação: some, e o polígono continua
            novos.append(simplificado)
            total += len(simplificado)
        if novos:
            poligonos_saida.append(novos)

    if not poligonos_saida:
        return None, 0
    return {"type": "MultiPolygon", "coordinates": poligonos_saida}, total


# ------------------------------------------------------------------ atributos


def _valor_atributo(valor):
    if isinstance(valor, (datetime, date)):
        return valor.isoformat()
    if isinstance(valor, bytes):
        return _decodificar(valor)
    if isinstance(valor, float) and valor != valor:
        return None
    return valor


def _rotulo(atributos: dict, ordem: int) -> str:
    """Nome de exibição da feição, tirado do .dbf: o primeiro campo cujo nome parece de nome
    (NM_BAIRRO, NOME, NAME…), senão o primeiro campo de texto preenchido."""
    for chave, valor in atributos.items():
        if isinstance(valor, str) and valor.strip() and _CAMPOS_ROTULO.match(chave):
            return valor.strip()[:200]
    for valor in atributos.values():
        if isinstance(valor, str) and valor.strip():
            return valor.strip()[:200]
    return f"Área {ordem}"


# ------------------------------------------------------------------ importação


def importar_shapefile(
    id_inquerito: int,
    enviados: list[tuple[str, bytes]],
    nome_camada: str | None = None,
    cor: str | None = None,
    importada_por: str | None = None,
) -> dict:
    """Lê o shapefile, reprojeta para WGS 84 e grava como camada do inquérito.

    `enviados` é [(nome_do_arquivo, bytes)] — um `.zip` ou os arquivos soltos do conjunto.
    O teto é o limite geral (UPLOAD_DOCUMENTO_MB) sobre o TOTAL somado, não por arquivo.
    Levanta ValueError com mensagem para a tela em tudo que for problema do arquivo."""
    limite_bytes = _limite_upload_bytes()
    limite_mb = limite_bytes // 1048576
    total_bytes = sum(len(dados) for _, dados in enviados)
    if total_bytes > limite_bytes:
        raise ValueError(
            f"Arquivo grande demais ({total_bytes / 1048576:.0f} MB no total; o limite geral é "
            f"{limite_mb} MB). Recorte a área de interesse antes de importar."
        )
    if not total_bytes:
        raise ValueError("Nenhum arquivo enviado.")

    partes = _separar_arquivos(enviados)
    prj_texto = _decodificar(partes["prj"]) if "prj" in partes else None
    transformador, nome_projecao = _transformador(prj_texto)

    feicoes = _ler_feicoes(partes, transformador)
    if not feicoes:
        raise ValueError(
            "Nenhum polígono aproveitável no shapefile — todas as feições vieram vazias ou com "
            "coordenadas fora do mundo. Confira se o .prj corresponde ao arquivo."
        )

    tolerancia, vertices = _ajustar_detalhe(feicoes)

    conteudo = b"".join(partes[e] for e in sorted(partes))
    co_hash = hashlib.sha256(conteudo).hexdigest()
    nome = (nome_camada or "").strip()[:200] or _nome_padrao(enviados)
    cor_final = cor if cor and _RE_COR.match(cor) else _COR_PADRAO

    return _gravar_camada(
        id_inquerito=id_inquerito,
        nome=nome,
        no_arquivo=_nome_enviado(enviados),
        co_hash=co_hash,
        nome_projecao=(nome_projecao or "")[:200] or None,
        prj_texto=prj_texto,
        cor=cor_final,
        tolerancia=tolerancia,
        vertices=vertices,
        feicoes=feicoes,
        importada_por=(importada_por or "")[:200] or None,
    )


def _so_o_arquivo(caminho: str) -> str:
    """Alguns navegadores mandam o caminho junto do nome; guardamos só o arquivo."""
    return caminho.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]


def _nome_padrao(enviados: list[tuple[str, bytes]]) -> str:
    nome = _so_o_arquivo(enviados[0][0]) if enviados else "camada"
    return (nome.rsplit(".", 1)[0] or "camada")[:200]


def _nome_enviado(enviados: list[tuple[str, bytes]]) -> str:
    """O que fica registrado como origem: o nome do zip, ou "N arquivos" no envio solto."""
    if len(enviados) == 1:
        return _so_o_arquivo(enviados[0][0])[:300]
    return f"{len(enviados)} arquivos ({_nome_padrao(enviados)}.*)"[:300]


def _ler_feicoes(partes: dict[str, bytes], transformador) -> list[dict]:
    """Percorre o shapefile devolvendo [{ordem, rotulo, atributos, geometria}] já em WGS 84."""
    # o .shx é opcional (lemos o .shp em sequência), mas passar shx=None faz o pyshp tentar tratar
    # o None como caminho de arquivo — por isso a chave só entra quando existe mesmo
    fontes = {"shp": io.BytesIO(partes["shp"]), "dbf": io.BytesIO(partes["dbf"])}
    if "shx" in partes:
        fontes["shx"] = io.BytesIO(partes["shx"])

    try:
        leitor = shapefile.Reader(
            **fontes, encoding=_codificacao_dbf(partes), encodingErrors="replace"
        )
    except shapefile.ShapefileException as exc:
        raise ValueError(f"Não foi possível ler o shapefile: {exc}")

    # tipo 0 (nulo) no cabeçalho existe em arquivo gerado por ferramenta desleixada e ainda assim
    # traz polígonos — aí a checagem fica por conta de cada feição, em _geometria_wgs84
    if leitor.shapeType not in _TIPOS_POLIGONO and leitor.shapeType != 0:
        tipo = _NOMES_TIPO.get(leitor.shapeType, str(leitor.shapeType))
        raise ValueError(
            f"Este shapefile é de {tipo}, não de polígonos. A camada do mapa é de áreas — para "
            "pontos e linhas, use as próprias localizações da extração."
        )

    feicoes: list[dict] = []
    brutos = 0
    descartadas = 0

    for indice, registro in enumerate(leitor.iterShapeRecords(), start=1):
        if len(feicoes) >= MAX_FEICOES:
            raise ValueError(
                f"O shapefile tem mais de {MAX_FEICOES:,} áreas. Recorte a região de interesse "
                "antes de importar.".replace(",", ".")
            )

        geometria, quantos = _geometria_wgs84(registro.shape, transformador)
        brutos += quantos
        if brutos > MAX_VERTICES_BRUTOS:
            raise ValueError(
                "O shapefile é detalhado demais para ser desenhado no mapa. Recorte a região de "
                "interesse ou simplifique a camada antes de importar."
            )
        if geometria is None:
            descartadas += 1
            continue

        atributos = {
            campo: _valor_atributo(valor)
            for campo, valor in zip(
                (c[0] for c in leitor.fields if c[0] != "DeletionFlag"), registro.record
            )
        }
        feicoes.append({
            "ordem": indice,
            "rotulo": _rotulo(atributos, indice),
            "atributos": atributos,
            "geometria": geometria,
        })

    if descartadas:
        logger.info("shapefile: %d feição(ões) descartada(s) por geometria vazia ou fora do mundo", descartadas)
    return feicoes


def _geometria_wgs84(shape, transformador) -> tuple[dict | None, int]:
    """Converte um shape do pyshp em MultiPolygon já reprojetado. O `__geo_interface__` é quem
    resolve qual anel é contorno e qual é buraco (pelo sentido de giro, como manda o formato)."""
    try:
        bruto = shape.__geo_interface__
    except Exception:  # noqa: BLE001 — shape corrompido não pode derrubar a importação inteira
        return None, 0

    tipo = bruto.get("type")
    if tipo == "Polygon":
        poligonos = [bruto["coordinates"]]
    elif tipo == "MultiPolygon":
        poligonos = bruto["coordinates"]
    else:
        return None, 0

    saida, total = [], 0
    for aneis in poligonos:
        convertidos = []
        for indice, anel in enumerate(aneis):
            total += len(anel)
            pontos = []
            for vertice in anel:
                ponto = _projetar(float(vertice[0]), float(vertice[1]), transformador)
                if ponto is not None:
                    pontos.append(ponto)
            if pontos and pontos[0] != pontos[-1]:
                pontos.append(pontos[0])   # o desenho e o `@>` esperam o anel fechado
            if len(pontos) < 4:
                if indice == 0:
                    convertidos = []
                    break
                continue
            convertidos.append(pontos)
        if convertidos:
            saida.append(convertidos)

    if not saida:
        return None, total
    return {"type": "MultiPolygon", "coordinates": saida}, total


def _ajustar_detalhe(feicoes: list[dict]) -> tuple[float, int]:
    """Simplifica as geometrias até caberem em MAX_VERTICES, devolvendo (tolerância, vértices).

    A primeira tolerância é imperceptível e vale sempre; as maiores só entram se a camada
    continuar pesada. Quem chama registra a tolerância na camada, para a tela poder dizer que a
    geometria desenhada é aproximada.

    Cada passada parte da geometria ORIGINAL, e não da já simplificada: simplificar em cima de
    simplificado acumula desvio maior que a tolerância anunciada."""
    for tolerancia in TOLERANCIAS:
        total, sobreviventes = 0, []
        for feicao in feicoes:
            geometria, quantos = _simplificar_geometria(feicao["geometria"], tolerancia)
            if geometria is None:
                continue
            sobreviventes.append({**feicao, "geometria": geometria})
            total += quantos
        if not sobreviventes:
            raise ValueError("As áreas do shapefile são pequenas demais para serem desenhadas no mapa.")
        if total <= MAX_VERTICES or tolerancia == TOLERANCIAS[-1]:
            feicoes[:] = sobreviventes
            if total > MAX_VERTICES:
                raise ValueError(
                    "O shapefile continua detalhado demais mesmo depois de simplificado. Recorte a "
                    "região de interesse antes de importar."
                )
            return tolerancia, total
    raise ValueError("Não foi possível simplificar a camada.")   # inalcançável: a última passada sempre decide


def _caixa(geometria: dict) -> tuple[float, float, float, float]:
    """(lat_min, lat_max, lon_min, lon_max) de uma geometria."""
    lats, lons = [], []
    for aneis in geometria["coordinates"]:
        for anel in aneis:
            for lon, lat in anel:
                lons.append(lon)
                lats.append(lat)
    return (min(lats), max(lats), min(lons), max(lons))


def _literal_poligono(anel: list[tuple[float, float]]) -> str:
    """Anel -> literal do tipo POLYGON do Postgres, em (x=longitude, y=latitude). O vértice
    repetido do fechamento sai: o tipo nativo fecha sozinho."""
    pontos = anel[:-1] if len(anel) > 3 and anel[0] == anel[-1] else anel
    return "(" + ",".join(f"({lon},{lat})" for lon, lat in pontos) + ")"


def _gravar_camada(
    *,
    id_inquerito: int,
    nome: str,
    no_arquivo: str,
    co_hash: str,
    nome_projecao: str | None,
    prj_texto: str | None,
    cor: str,
    tolerancia: float,
    vertices: int,
    feicoes: list[dict],
    importada_por: str | None,
) -> dict:
    caixas = [_caixa(f["geometria"]) for f in feicoes]
    ja_importado = None

    with get_connection() as conn:
        with conn.cursor() as cur:
            # o mesmo arquivo já importado antes não é erro (pode ser recorte novo do exame), mas a
            # tela avisa — camada duplicada conta o mesmo registro duas vezes na leitura
            cur.execute(
                """SELECT no_camada FROM investigacao.geo_tb02_camadas
                   WHERE id_inquerito = %s AND co_hash = %s
                   ORDER BY dt_importacao LIMIT 1""",
                (id_inquerito, co_hash),
            )
            anterior = cur.fetchone()
            ja_importado = anterior[0] if anterior else None

            cur.execute(
                """INSERT INTO investigacao.geo_tb02_camadas
                       (id_inquerito, no_camada, no_arquivo, co_hash, no_projecao, tx_projecao,
                        qt_feicoes, qt_vertices, vl_tolerancia, vl_lat_min, vl_lat_max,
                        vl_lon_min, vl_lon_max, co_cor, no_importada_por)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING id_camada""",
                (
                    id_inquerito, nome, no_arquivo, co_hash,
                    nome_projecao, prj_texto, len(feicoes), vertices, tolerancia,
                    min(c[0] for c in caixas), max(c[1] for c in caixas),
                    min(c[2] for c in caixas), max(c[3] for c in caixas),
                    cor, importada_por,
                ),
            )
            id_camada = cur.fetchone()[0]

            cur.executemany(
                """INSERT INTO investigacao.geo_tb03_feicoes
                       (id_camada, nu_ordem, no_feicao, tx_atributos, tx_geometria,
                        vl_lat_min, vl_lat_max, vl_lon_min, vl_lon_max)
                   VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s)""",
                [
                    (
                        id_camada, feicao["ordem"], feicao["rotulo"],
                        json.dumps(feicao["atributos"], ensure_ascii=False, default=str),
                        json.dumps(feicao["geometria"]),
                        caixa[0], caixa[1], caixa[2], caixa[3],
                    )
                    for feicao, caixa in zip(feicoes, caixas)
                ],
            )

            # os anéis precisam do id_feicao, que só existe depois do INSERT acima; nu_ordem é a
            # ponte entre as duas listas
            cur.execute(
                "SELECT nu_ordem, id_feicao FROM investigacao.geo_tb03_feicoes WHERE id_camada = %s",
                (id_camada,),
            )
            id_por_ordem = dict(cur.fetchall())

            linhas_anel = []
            for feicao in feicoes:
                id_feicao = id_por_ordem[feicao["ordem"]]
                for aneis in feicao["geometria"]["coordinates"]:
                    for indice, anel in enumerate(aneis):
                        lats = [p[1] for p in anel]
                        lons = [p[0] for p in anel]
                        linhas_anel.append((
                            id_feicao, id_camada, indice > 0,   # anel 0 é o contorno; os demais, buracos
                            min(lats), max(lats), min(lons), max(lons),
                            _literal_poligono(anel),
                        ))

            cur.executemany(
                """INSERT INTO investigacao.geo_tb04_aneis
                       (id_feicao, id_camada, in_buraco, vl_lat_min, vl_lat_max,
                        vl_lon_min, vl_lon_max, pg_anel)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s::polygon)""",
                linhas_anel,
            )
        conn.commit()

    logger.info(
        "camada geoespacial %s importada no inquérito %s: %d feição(ões), %d vértice(s), projeção %s",
        id_camada, id_inquerito, len(feicoes), vertices, nome_projecao or "não informada",
    )
    return {
        "id_camada": id_camada,
        "no_camada": nome,
        "qt_feicoes": len(feicoes),
        "qt_vertices": vertices,
        "no_projecao": nome_projecao,
        "vl_tolerancia": float(tolerancia),
        "sem_prj": prj_texto is None,
        "ja_importado": ja_importado,   # nome da camada anterior com o mesmo arquivo, ou None
    }


# ------------------------------------------------------------------ consulta


def listar_camadas(id_inquerito: int) -> list[dict]:
    """Camadas do inquérito, da mais recente para a mais antiga (sem a geometria)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id_camada, no_camada, no_arquivo, no_projecao, qt_feicoes, qt_vertices,
                          vl_tolerancia, vl_lat_min, vl_lat_max, vl_lon_min, vl_lon_max,
                          co_cor, dt_importacao, no_importada_por
                   FROM investigacao.geo_tb02_camadas
                   WHERE id_inquerito = %s
                   ORDER BY dt_importacao DESC, id_camada DESC""",
                (id_inquerito,),
            )
            colunas = [d.name for d in cur.description]
            camadas = [dict(zip(colunas, linha)) for linha in cur.fetchall()]
    for camada in camadas:
        for chave in ("vl_tolerancia", "vl_lat_min", "vl_lat_max", "vl_lon_min", "vl_lon_max"):
            if camada[chave] is not None:
                camada[chave] = float(camada[chave])
    return camadas


def obter_id_inquerito_de_camada(id_camada: int) -> int | None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id_inquerito FROM investigacao.geo_tb02_camadas WHERE id_camada = %s",
                (id_camada,),
            )
            linha = cur.fetchone()
    return linha[0] if linha else None


def geojson_camada(id_camada: int) -> dict:
    """A camada inteira como FeatureCollection, do jeito que o Leaflet consome."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id_camada, no_camada, co_cor, qt_feicoes, vl_tolerancia,
                          vl_lat_min, vl_lat_max, vl_lon_min, vl_lon_max
                   FROM investigacao.geo_tb02_camadas WHERE id_camada = %s""",
                (id_camada,),
            )
            camada = cur.fetchone()
            if camada is None:
                raise ValueError("Camada não encontrada.")

            cur.execute(
                """SELECT id_feicao, nu_ordem, no_feicao, tx_atributos, tx_geometria
                   FROM investigacao.geo_tb03_feicoes
                   WHERE id_camada = %s
                   ORDER BY nu_ordem""",
                (id_camada,),
            )
            feicoes = [
                {
                    "type": "Feature",
                    "id": id_feicao,
                    "properties": {
                        "id_feicao": id_feicao,
                        "nu_ordem": ordem,
                        "no_feicao": nome,
                        "atributos": atributos or {},
                    },
                    "geometry": geometria,
                }
                for id_feicao, ordem, nome, atributos, geometria in cur.fetchall()
            ]

    return {
        "type": "FeatureCollection",
        "id_camada": camada[0],
        "no_camada": camada[1],
        "co_cor": camada[2],
        "qt_feicoes": camada[3],
        "vl_tolerancia": float(camada[4]) if camada[4] is not None else None,
        "caixa": {
            "lat_min": float(camada[5]), "lat_max": float(camada[6]),
            "lon_min": float(camada[7]), "lon_max": float(camada[8]),
        },
        "features": feicoes,
    }


def renomear_camada(id_camada: int, nome: str, cor: str | None = None) -> bool:
    nome = (nome or "").strip()[:200]
    if not nome:
        raise ValueError("O nome da camada não pode ficar vazio.")
    with get_connection() as conn:
        with conn.cursor() as cur:
            if cor and _RE_COR.match(cor):
                cur.execute(
                    "UPDATE investigacao.geo_tb02_camadas SET no_camada = %s, co_cor = %s WHERE id_camada = %s",
                    (nome, cor, id_camada),
                )
            else:
                cur.execute(
                    "UPDATE investigacao.geo_tb02_camadas SET no_camada = %s WHERE id_camada = %s",
                    (nome, id_camada),
                )
            mudou = cur.rowcount > 0
        conn.commit()
    return mudou


def apagar_camada(id_camada: int) -> bool:
    """Apaga a camada. Feições e anéis vão junto pelo ON DELETE CASCADE."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM investigacao.geo_tb02_camadas WHERE id_camada = %s", (id_camada,))
            apagou = cur.rowcount > 0
        conn.commit()
    return apagou


# ------------------------------------------------------------------ cruzamento com as localizações

# "o ponto está dentro deste anel": a caixa envolvente rejeita a esmagadora maioria dos pares
# barato, e só o que sobra paga o `@>`, que percorre os vértices um a um.
_SQL_ANEL_CONTEM = (
    "lo.vl_latitude BETWEEN {a}.vl_lat_min AND {a}.vl_lat_max "
    "AND lo.vl_longitude BETWEEN {a}.vl_lon_min AND {a}.vl_lon_max "
    "AND {a}.pg_anel @> point(lo.vl_longitude::float8, lo.vl_latitude::float8)"
)


def contagem_por_feicao(
    id_inquerito: int,
    id_camada: int,
    id_extracao: int | None = None,
    busca: str | None = None,
    app: str | None = None,
    categoria: str | None = None,
    data_de: str | None = None,
    data_ate: str | None = None,
    periodo: str | None = None,
    ids_extracao: list[int] | None = None,
) -> dict:
    """Quantas localizações do recorte atual caíram dentro de cada área da camada.

    O recorte é o mesmo da tabela e do grafo (mesmos filtros); o que muda é só o agrupamento. Só
    volta feição com contagem — numa camada de centenas de áreas, as vazias seriam a maior parte
    da resposta e não são desenhadas de forma diferente na tela."""
    filtros, params = _where_localizacao(
        id_inquerito, id_extracao, busca, app, categoria, True, data_de, data_ate, periodo,
        None, None, ids_extracao,
    )
    where = " AND ".join(filtros)
    dentro = _SQL_ANEL_CONTEM.format(a="an")
    fora_do_buraco = _SQL_ANEL_CONTEM.format(a="bu")

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT an.id_feicao, count(DISTINCT lo.id_localizacao) AS qt
                FROM investigacao.loc_tb01_localizacoes lo
                JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = lo.id_extracao
                {_JOIN_GEO_LOCALIZACAO}
                JOIN investigacao.geo_tb04_aneis an
                     ON an.id_camada = %s AND NOT an.in_buraco AND {dentro}
                WHERE {where}
                  AND NOT EXISTS (
                      SELECT 1 FROM investigacao.geo_tb04_aneis bu
                      WHERE bu.id_feicao = an.id_feicao AND bu.in_buraco AND {fora_do_buraco}
                  )
                GROUP BY an.id_feicao
                """,
                [id_camada] + params,
            )
            por_feicao = {int(id_feicao): int(qt) for id_feicao, qt in cur.fetchall()}

            # O total NÃO é a soma das contagens acima: áreas sobrepostas (comum em camada de
            # zonas de influência) contariam o mesmo registro duas vezes. Aqui a pergunta é
            # "dentro da camada", que o próprio filtro de área responde sem duplicar.
            filtros_camada, params_camada = _where_localizacao(
                id_inquerito, id_extracao, busca, app, categoria, True, data_de, data_ate, periodo,
                id_camada, None,
            )
            cur.execute(
                f"""SELECT count(*)
                    FROM investigacao.loc_tb01_localizacoes lo
                    JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = lo.id_extracao
                    {_JOIN_GEO_LOCALIZACAO}
                    WHERE {" AND ".join(filtros_camada)}""",
                params_camada,
            )
            total_dentro = cur.fetchone()[0]

    return {
        "id_camada": id_camada,
        "contagens": por_feicao,
        "total_dentro": total_dentro,
        "qt_feicoes_com_registro": len(por_feicao),
    }

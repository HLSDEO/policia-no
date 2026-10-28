"""Funções acessórias para a página de extração de dados de investigação (UFDR -> banco)."""

import contextlib
import hashlib
import logging
import os
import re
import struct
import threading
import time
import zipfile

from datetime import datetime
from io import BytesIO
from pathlib import Path

import psycopg
from fastapi import Depends, HTTPException
from PIL import Image, ImageFile, ImageOps
from psycopg.types.json import Jsonb

# Anexos de .ufdr reais chegam TRUNCADOS (exportação do Cellebrite corta o fim do JPEG — e a
# leitura pela ponte às vezes também). Para prévia/relatório/pré-processamento de comparação,
# mostrar o que existe vale mais que um 422: o PIL levanta "image file is truncated" e o
# investigador ficava sem imagem nenhuma. Flag de processo (única que o PIL oferece): só
# relaxa o decode, nada aqui usa a imagem para decisão probatória — a prova é o binário original.
ImageFile.LOAD_TRUNCATED_IMAGES = True

# iPhone grava as fotos da câmera em HEIC, que o navegador não exibe e o Pillow só abre com este
# plugin — registrado no import para que redimensionar_imagem() converta HEIC (e afins) em JPEG na
# prévia. Best-effort: se o pacote faltar, imagens comuns (jpg/png) seguem funcionando.
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:
    pass

from funcoes import canal_extracao, ufdr_cache, ufdr_zip
from funcoes.auditoria_acesso import registrar_acesso
from funcoes.auth_utils import exigir_aprovado
from funcoes.db_utils import get_connection
from funcoes.ia_delegacia import (
    delegacias_sem_ia,
    normalizar_delegacia,
    usuario_com_ia_desativada,
)
from funcoes.hash_utils import (
    HasheadorStreaming,
    calcular_hashes_arquivo,
    gravar_sidecar_hashes,
    ler_sidecar_hashes,
)
from funcoes.organizacao_arquivos import (
    CATEGORIA_CELULAR,
    PASTA_DADOS,
    destino_em_dados,
    indice_entrada,
    organizar,
    resolver as resolver_em_dados,
    resolver_estrito,
)
from funcoes.transcricao import ModeloTranscricaoIndisponivel, transcrever_lote
from funcoes.ufdr_parser import (
    APP_DESCONHECIDO,
    FONTE_SMS,
    FONTE_WHATSAPP,
    categoria_de_extensao,
    contatos_compartilhados_chatstorage,
    indexar_indicadores,
    iterar_registros,
    ler_info_dispositivo,
)

logger = logging.getLogger(__name__)

# PASTA_DADOS (a pasta ./data do host, montada no container — ver docker-compose.yml) é definida
# em funcoes/organizacao_arquivos.py e reexportada aqui, que é de onde o resto do sistema a
# importa desde sempre.

# Teto de linhas por página nas listagens. A tela pede algumas dezenas por vez; quem pede muito é a
# exportação para Excel (web/js/exportar_excel.js), que manda `limite` alto de propósito para levar o
# conjunto inteiro já filtrado. O valor abaixo existe só para não deixar um `limite` absurdo virar um
# SELECT sem freio — não é para cortar exportação legítima.
LIMITE_MAX_LISTAGEM = 1_000_000

# Tamanho do bloco ao gravar um upload em disco. Mesmo valor do hash_utils, pelo mesmo motivo:
# nunca carregar o arquivo inteiro na memória do processo.
_BLOCO_UPLOAD = 4 * 1024 * 1024


def _sanitizar_delegacia(nome: str | None) -> str | None:
    """Converte nome da delegacia para nome de pasta: maiúsculas, sem caracteres especiais,
    'FICCO/ES' vira 'FICCO'."""
    if not nome:
        return None
    nome = nome.strip().upper().replace("/", " ").replace("\\", " ")
    # Remove tudo que não for letra, número, espaço, hífen ou underscore
    nome = re.sub(r"[^A-Z0-9 _-]", "", nome)
    nome = re.sub(r"\s+", " ", nome).strip().replace(" ", "-")
    return nome if nome else None


def _subpasta_delegacia(subpasta: str | None) -> Path:
    """Devolve PASTA_DADOS / subpasta se subpasta for informada, senão PASTA_DADOS.

    Confinada a data/ por construção: a subpasta é re-sanitizada aqui (não confia só na
    chamada a delegacia_permitida da rota), então um caller futuro não reabre traversal."""
    if not subpasta:
        return PASTA_DADOS
    limpa = _sanitizar_delegacia(subpasta)
    if limpa is None or "/" in limpa or limpa in (".", ".."):
        raise ValueError(f"Subpasta de delegacia inválida: {subpasta!r}")
    base = PASTA_DADOS.resolve()
    pasta = (PASTA_DADOS / limpa).resolve()
    if pasta != base and base not in pasta.parents:
        raise ValueError(f"Subpasta de delegacia inválida: {subpasta!r}")
    return pasta


def delegacia_permitida(delegacia_pedida: str | None, usuario: dict) -> str | None:
    """Sanitiza a delegacia pedida e confere se o usuário pode acessá-la.

    Quem tem VE_TODAS_DELEGACIAS (chefia de inteligência, auditoria, administrador) enxerga qualquer
    delegacia — é a mesma capacidade que abre os cards de outra unidade na listagem
    (pode_ver_inquerito), e o modal "Importar" manda justamente a delegacia do card: negar aqui
    deixava esses perfis com o card aberto e o upload 403. Usuário comum só pode
    listar/extrair arquivos da própria delegacia (usr_tb01_usuarios.id_delegacia): pedir uma
    delegacia diferente da sua — ou nenhuma, quando tem uma cadastrada — é negado, para não
    vazar arquivos .ufdr de investigações de outra unidade (a pasta data/<delegacia>/ é
    compartilhada por todos os usuários no mesmo container). Usuário comum SEM delegacia
    cadastrada não lista a raiz de data/: sem unidade não há pasta própria, e a raiz mistura
    as pastas de todas as unidades — um administrador vincula a delegacia na tela Usuários
    (ver A2 e A7)."""
    pedida = _sanitizar_delegacia(delegacia_pedida)
    if usuario.get("in_admin") or usuario.get("ve_todas_delegacias"):
        return pedida
    propria = _sanitizar_delegacia(usuario.get("no_delegacia"))
    if propria is None:
        raise HTTPException(
            status_code=403,
            detail="Seu usuário ainda não tem delegacia vinculada — peça a um administrador.",
        )
    if pedida is not None and pedida != propria:
        raise HTTPException(status_code=403, detail="Sem permissão para acessar arquivos de outra delegacia.")
    return propria


def listar_arquivos_ufdr(subpasta: str | None = None) -> list[dict]:
    """Lista os arquivos .ufdr disponíveis na pasta de dados, para popular o seletor da página."""
    pasta = _subpasta_delegacia(subpasta)
    if not pasta.is_dir():
        return []
    arquivos = []
    for caminho in sorted(pasta.glob("*.ufdr")):
        arquivos.append({
            "nome": caminho.name,
            "tamanho_bytes": caminho.stat().st_size,
        })
    return arquivos


def _localizar_ufdr(no_arquivo: str) -> Path | None:
    """Resolve o .ufdr de uma extração a partir do que está gravado em NO_ARQUIVO. Devolve o
    Path completo se encontrado, None caso contrário.

    Depois da importação o arquivo é movido para data/<delegacia>/<inquérito>/CELULAR/ e o
    caminho relativo passa a ser o valor gravado (ver funcoes/organizacao_arquivos.py) — esse é
    o caso direto. Extração importada antes disso guardou só o nome do arquivo, e o
    investigador pode ter movido a pasta à mão: aí vale a busca pelo nome em data/ e abaixo."""
    return resolver_em_dados(no_arquivo)


def fonte_ufdr(no_arquivo: str | None):
    """O "passaporte" de quem VAI LER o .ufdr (nunca movê-lo/hash dele em disco): Path local se o
    arquivo está no data() do central — como sempre — senão uma `no_ponte.FonteRemota` se a
    extração pertence a um nó ativo (docs/no_descentralizado.md). None = ninguém tem o arquivo.

    É a pergunta que rotas de anexo, OCR, CLIP, comparação facial, transcrição e relatórios fazem
    no lugar de `_localizar_ufdr` — tudo que eles fazem com o resultado (`ufdr_zip.abrir`,
    `ufdr_cache.obter_zip/ler_membro`) aceita as duas fontes da mesma interface, então UM ponto de
    decisão decide o arquivo de 100 delegacias. O local manda no empate: se alguém copiou o .ufdr
    para o central, processa-se aqui, sem WAN."""
    if not no_arquivo:
        return None
    local = resolver_em_dados(no_arquivo)
    if local is not None and local.is_file():
        return local
    return _fonte_ufdr_remota(no_arquivo)


def _fonte_ufdr_remota(no_arquivo: str):
    """A metade remota de fonte_ufdr — o arquivo mora num nó ativo? Não toca no disco local:
    quem procura em data/ é o próprio fonte_ufdr/resolver_em_dados (e _situacoes_arquivos_ufdr,
    que troca N varreduras por uma e chega aqui direto para as linhas sem achado local)."""
    from funcoes import no_ponte, no_utils  # tardio: ponte importa requests, nó só quando há nó

    no = no_utils.servidor_por_arquivo(no_arquivo)
    if no is None:
        return None
    # A instância fica em cache (não só o nó): o TAMANHO do arquivo no nó vem de um round-trip,
    # e a FonteRemota viva poupa esse round-trip em cada anexo da galeria seguinte. 60 s casa com
    # o cache de no_utils — trocar o token do nó propaga dentro de um minuto.
    agora = time.monotonic()
    guardada = _FONTE_UFDR_CACHE.get(no_arquivo)
    if guardada is not None and agora - guardada[0] < 60.0:
        return guardada[1]
    # A sonda de presença usa timeout CURTO: com o link do nó saturado por uma entrega em
    # andamento, a resposta do /no/arquivo (um cabeçalho!) fica atrás de dezenas de blocos de
    # 4 MiB e o timeout generoso de 600 s travava o poll da tela de investigações inteiro —
    # o navegador desistia aos 30 s e o analista lia "Sem conexão com o servidor" em todo
    # inquérito de nó. 10 s é o teto do badge; o leitor de anexo de verdade segue esperando.
    fonte = no_ponte.FonteRemota(no, no_arquivo, timeout=no_ponte._TIMEOUT_PROBADA)
    fonte = fonte if fonte.is_file() else None
    ts_ok = agora
    if fonte is None and guardada is not None and guardada[1] is not None \
            and agora - guardada[2] < _FONTE_UFDR_GRACE_S:
        # Nó ocupado não é arquivo ausente: segura a última resposta boa por mais alguns minutos
        # (o badge não pisca "arquivo não está na pasta" durante uma entrega) e sonda de novo
        # na próxima batida. Se o arquivo sumiu de verdade, a leitura do anexo dirá isso, 404.
        fonte = guardada[1]
        ts_ok = guardada[2]
    if len(_FONTE_UFDR_CACHE) > 5000:
        _FONTE_UFDR_CACHE.clear()
    _FONTE_UFDR_CACHE[no_arquivo] = (agora, fonte, ts_ok)
    return fonte


_FONTE_UFDR_CACHE: dict = {}

# Por quanto tempo uma sonda de presença que FALHOU (link do nó saturado, túnel atrasado) ainda
# vale a última resposta boa. 10 min cobrem a janela de uma entrega sem esconder um arquivo
# apagado por tempo demais — quem aposta no arquivo (leitura de anexo) paga o 404 honesto.
_FONTE_UFDR_GRACE_S = 600.0


def _resolver_arquivo_zip(caminho_zip: str, no_arquivo_ufdr: str | None) -> tuple | None:
    """Resolve a fonte de origem (Path local OU FonteRemota de um nó) e o caminho do membro
    dentro dela, para servir o binário de uma linha de ARQ_TB01_ARQUIVOS: um único .ufdr por
    aparelho, localizado por `fonte_ufdr` — CO_CAMINHO_ZIP já é o caminho interno puro. (A Nuvem
    IPED usa um formato de ponteiro à parte, "IPED::...", tratado por _resolver_arquivo_iped antes
    de chegar aqui.) Devolve None se a fonte de origem não existe mais — nem no central, nem no nó."""
    if "::" in caminho_zip:
        rel_zip, caminho_interno = caminho_zip.split("::", 1)
        # rel_zip vem do banco, gravado por parsers sobre conteúdo do investigado:
        # resolver_estrito barra "..", absoluto e "C:/…" (sem fallback por nome, que
        # poderia cair num homônimo de outro inquérito). Ver F1.
        caminho_zip_real = resolver_estrito(rel_zip)
        return (caminho_zip_real, caminho_interno) if caminho_zip_real is not None else None
    fonte = fonte_ufdr(no_arquivo_ufdr) if no_arquivo_ufdr else None
    return (fonte, caminho_zip) if fonte else None


def _resolver_arquivo_iped(caminho_zip: str) -> tuple[Path, str, str, str] | None:
    """Resolve o ponteiro de um item da Nuvem IPED — formato "IPED::<id_extracao>::
    <dataSourcePath>::<hash>::<idInDataSource>", gravado por iped_utils._item_para_arquivo —
    na pasta módulo do caso hoje em disco + os dados que iped_cache.ler_blob precisa.

    Busca o NO_PASTA atual em IPD_TB01_EXTRACOES pelo id_extracao (não guarda o caminho no
    próprio ponteiro): a pasta do caso só é renomeada para o e-mail da conta detectada ao
    FINAL da importação (ver iped_utils._processar_importacao_iped), depois que os itens já
    foram gravados — buscar o caminho fresco aqui é o que mantém os ponteiros válidos depois
    da renomeação (e de qualquer renomeação manual futura).

    Devolve None se a extração não existir, a pasta não estiver mais em data/, ou não for
    mais um caso válido do IPED."""
    from funcoes.iped_utils import _pasta_modulo_iped, _resolver_pasta_caso  # import tardio: evita ciclo (iped_utils já importa daqui)

    partes = caminho_zip.split("::", 4)
    if len(partes) != 5:
        return None
    _, id_extracao_str, origem, hash_md5, id_fonte = partes
    try:
        id_extracao = int(id_extracao_str)
    except ValueError:
        return None

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT no_pasta FROM investigacao.ipd_tb01_extracoes WHERE id_extracao = %s",
                (id_extracao,))
            row = cur.fetchone()
    if row is None:
        return None
    # NO_PASTA vem do banco: revalida e confina a data/ como na criação (ver F7) — sem isto um
    # valor envenenado virava pasta módulo fora de data/ e o blob saía de qualquer lugar.
    try:
        caminho = _resolver_pasta_caso(row[0])
    except (ValueError, FileNotFoundError):
        return None
    pasta_modulo = _pasta_modulo_iped(caminho)
    if pasta_modulo is None:
        return None
    return (pasta_modulo, origem, hash_md5, id_fonte)


def _situacao_arquivo_ufdr(no_arquivo: str | None) -> dict:
    """Confere se o .ufdr da extração ainda está acessível (no data() do central OU na pasta do
    nó da delegacia — docs/no_descentralizado.md). Os anexos (imagens, áudios, vídeos, documentos)
    não são copiados para o banco — são lidos sob demanda do .ufdr original — então, se o arquivo
    foi apagado ou renomeado, esses conteúdos deixam de abrir. Devolve
    {in_arquivo_presente, qt_bytes_arquivo} para a listagem avisar o investigador."""
    if not no_arquivo:
        return {"in_arquivo_presente": False, "qt_bytes_arquivo": None}
    fonte = fonte_ufdr(no_arquivo)
    if fonte is not None:
        return {"in_arquivo_presente": True, "qt_bytes_arquivo": fonte.stat().st_size}
    return {"in_arquivo_presente": False, "qt_bytes_arquivo": None}


def _situacoes_arquivos_ufdr(no_arquivos: list) -> dict:
    """Versão em lote de _situacao_arquivo_ufdr: devolve {no_arquivo: situacao} para todos.

    Existe porque a versão linha a linha transformava a listagem num timeout: sem o .ufdr no
    disco (o caso comum de quem não importa o arquivo nesta máquina), cada linha pagava o
    plano B completo do resolver() — e numa montagem de storage (Windows/SMB) o pior pedaço é
    o STAT do caminho INEXISTENTE, ~60 ms por linha: 197 extrações passavam de um minuto e a
    tela de investigações ficava presa no skeleton. Aqui a árvore é varrida UMA vez
    (organizacao_arquivos.indice_entrada, cacheada) e todas as respostas saem do índice — nenhum
    stat em caminho que não existe. O que não estiver local vai para a checagem de nó remoto,
    igual ao fonte_ufdr (a diferença conhecida: um .ufder escondido dentro de uma das
    PASTAS_PULADAS do índice só seria visto aqui pelo leitor de anexo, que usa o fonte_ufdr
    completo — para o badge da listagem isso é irrelevante)."""
    ausente = {"in_arquivo_presente": False, "qt_bytes_arquivo": None}
    resultado: dict = {}
    validos: list = []
    for no_arquivo in no_arquivos:
        limpo = (no_arquivo or "").strip().replace("\\", "/").strip("/")
        # mesmos guardas do resolver(): só caminho relativo dentro de data/ toca no disco
        if not limpo or ".." in limpo.split("/") or re.match(r"^[A-Za-z]:", limpo):
            resultado[no_arquivo] = dict(ausente)
        else:
            validos.append((no_arquivo, limpo))
    if not validos:
        return resultado

    # indice + mapa de relativos já montados e cacheados (30 s) por indice_entrada — ver o
    # comentário lá: reconstruir o mapa a cada batida do poll custava mais que a varredura
    indice, por_caminho = indice_entrada()
    remotos: list = []
    for no_arquivo, limpo in validos:
        # mesma preferência do resolver(): o caminho exato gravado primeiro, o nome depois
        achado = por_caminho.get(limpo) or indice.get(limpo.split("/")[-1])
        situacao = None
        if achado is not None:
            try:
                situacao = {"in_arquivo_presente": True, "qt_bytes_arquivo": achado.stat().st_size}
            except OSError:  # apagado entre a varredura e o stat
                situacao = None
        if situacao is None:
            remotos.append(no_arquivo)
        else:
            resultado[no_arquivo] = situacao
    # O que sobrou pode morar num NÓ: é round-trip de rede por arquivo (o tamanho vem do
    # X-Tamanho do nó). Em série, uma lista cheia de prova em nó custava ~12 s no primeiro
    # acesso a cada janela de 60 s do cache; em 8 requisições concurrentes o pior caso fica
    # em ~2 s — o nó responde um HEAD por arquivo, e 8 é o mesmo teto educado de sempre.
    if remotos:
        def _situacao_remota(no_arquivo: str) -> dict:
            fonte = _fonte_ufdr_remota(no_arquivo)
            if fonte is not None:
                with contextlib.suppress(OSError):
                    return {"in_arquivo_presente": True, "qt_bytes_arquivo": fonte.stat().st_size}
            return dict(ausente)

        if len(remotos) > 1:
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(max_workers=8, thread_name_prefix="ufdr-situacao") as ex:
                for no_arquivo, situacao in zip(remotos, ex.map(_situacao_remota, remotos)):
                    resultado[no_arquivo] = situacao
        else:
            resultado[remotos[0]] = _situacao_remota(remotos[0])
    return resultado


def restaurar_arquivo_ufdr(id_extracao: int, caminho_origem: str, usuario: dict) -> dict:
    """Copia o .ufdr de um caminho externo para a pasta de dados, tornando-o novamente
    disponível para leitura de anexos. Devolve {in_arquivo_presente, qt_bytes_arquivo}.

    O container só tem acesso ao host via o volume ./data (ver docker-compose.yml) — não há
    caminho legítimo de restauração fora de PASTA_DADOS, então caminho_origem (vindo do
    cliente) é sempre resolvido e confinado a essa pasta. Usuário não-admin fica ainda
    restrito à própria delegacia, para não copiar o .ufdr de um caso de outra unidade para
    dentro do próprio inquérito via este endpoint."""
    import shutil

    base = PASTA_DADOS.resolve()
    caminho_origem = Path(caminho_origem).resolve()
    if not caminho_origem.is_file():
        raise FileNotFoundError(f"Arquivo não encontrado: {caminho_origem}")
    if base != caminho_origem and base not in caminho_origem.parents:
        raise ValueError("Caminho fora da pasta de dados.")
    if not usuario.get("in_admin"):
        propria = _sanitizar_delegacia(usuario.get("no_delegacia"))
        relativo = caminho_origem.relative_to(base)
        subpasta_origem = relativo.parts[0] if len(relativo.parts) > 1 else None
        if subpasta_origem != propria:
            raise ValueError("Sem permissão para restaurar arquivo de outra delegacia.")

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT no_arquivo FROM investigacao.inq_tb02_extracoes WHERE id_extracao = %s",
                (id_extracao,),
            )
            row = cur.fetchone()
            if row is None:
                raise ValueError("Extração não encontrada.")
            no_arquivo = row[0]

    # NO_ARQUIVO é o caminho relativo dentro de data/ (…/<inquérito>/CELULAR/arquivo.ufdr) desde
    # a organização por inquérito — a pasta pode não existir mais se alguém a apagou junto.
    # Confinado via destino_em_dados: o valor vem do banco e nunca pode virar escrita fora
    # de data/ (absoluto descarta a base no join, ".." escapa). Ver F3.
    destino = destino_em_dados(no_arquivo)
    destino.parent.mkdir(parents=True, exist_ok=True)
    # Cópia em blocos com os hashers no caminho (ver HasheadorStreaming): o arquivo lido É o
    # arquivo gravado, então a passada de hash que viria depois — leitura completa de dezenas de
    # GB — não acontece. Em .parcial + os.replace como no upload: meia-cópia nunca ocupa o lugar
    # da boa.
    parcial = destino.with_name(destino.name + ".parcial")
    hasheador = HasheadorStreaming(_ALGORITMOS_HASH_UFDR)
    try:
        with open(caminho_origem, "rb") as entrada, open(parcial, "wb") as saida:
            for bloco in iter(lambda: entrada.read(_BLOCO_UPLOAD), b""):
                hasheador.atualizar(bloco)
                saida.write(bloco)
        shutil.copystat(str(caminho_origem), str(parcial))  # o que o antigo copy2 preservava
        os.replace(parcial, destino)
    except BaseException:
        parcial.unlink(missing_ok=True)
        raise
    hashes = hasheador.finalizar()
    _gravar_hash_extracao(id_extracao, hashes)
    gravar_sidecar_hashes(destino, hashes, destino.stat().st_size)
    return {"in_arquivo_presente": True, "qt_bytes_arquivo": destino.stat().st_size}


def restaurar_arquivo_ufdr_upload(id_extracao: int, fluxo) -> dict:
    """Salva em disco o .ufdr enviado pelo navegador. Devolve {in_arquivo_presente, qt_bytes_arquivo}.

    Recebe o FLUXO do upload, não os bytes. A versão anterior recebia `conteudo: bytes` e fazia
    `destino.write_bytes(conteudo)` seguido de `calcular_hashes_bytes(conteudo)` — o arquivo
    inteiro na memória do processo, duas vezes, e um .ufdr passa de dezenas de GB. Com trinta
    analistas o pico de memória de alguns uploads simultâneos derruba a API para todo mundo, que
    é justamente o que o teto de corpo do nginx (client_max_body_size) estava contendo.

    Grava num arquivo `.parcial` e só então renomeia: upload interrompido no meio deixaria um
    .ufdr truncado no lugar do bom, e o erro só apareceria depois, na extração, como
    "arquivo ZIP incompleto". O hash sai dos blocos que passaram pela gravação
    (HasheadorStreaming) — nada de reler o arquivo inteiro depois de escrevê-lo."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT no_arquivo FROM investigacao.inq_tb02_extracoes WHERE id_extracao = %s",
                (id_extracao,),
            )
            row = cur.fetchone()
            if row is None:
                raise ValueError("Extração não encontrada.")
            no_arquivo = row[0]

    # Destino confinado a data/ como em restaurar_arquivo_ufdr (ver F3): no_arquivo vem do
    # banco e aqui viram bytes enviados pelo navegador — sem o confine seria escrita arbitrária.
    destino = destino_em_dados(no_arquivo)
    destino.parent.mkdir(parents=True, exist_ok=True)
    parcial = destino.with_name(destino.name + ".parcial")
    total = 0
    hasheador = HasheadorStreaming(_ALGORITMOS_HASH_UFDR)
    try:
        with open(parcial, "wb") as saida:
            for bloco in iter(lambda: fluxo.read(_BLOCO_UPLOAD), b""):
                total += len(bloco)
                hasheador.atualizar(bloco)
                saida.write(bloco)
        if total == 0:
            raise ValueError("Arquivo vazio.")
        os.replace(parcial, destino)
    except BaseException:
        parcial.unlink(missing_ok=True)
        raise

    hashes = hasheador.finalizar()
    _gravar_hash_extracao(id_extracao, hashes)
    gravar_sidecar_hashes(destino, hashes, total)
    return {"in_arquivo_presente": True, "qt_bytes_arquivo": destino.stat().st_size}


_RE_TOKEN_UPLOAD = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def salvar_ufdr_enviado(fluxo, nome_original: str, subpasta: str | None = None,
                        token: str | None = None) -> dict:
    """Grava em data/[<delegacia>/] um .ufdr enviado pelo navegador, na pasta de entrega — a
    mesma que GET /api/investigacao/arquivos lista e de onde POST /api/investigacao/extrair
    importa. Devolve {"nome", "tamanho_bytes"}: "nome" é como o arquivo ficou em disco (pode
    ganhar sufixo numérico quando já existe arquivo com o mesmo nome), e é ele que a tela manda
    de volta na extração.

    Sem o limite de UPLOAD_DOCUMENTO_MB (um .ufdr passa de dezenas de GB — o teto de corpo desta
    rota é o location próprio no nginx, e não a API), mas com as mesmas regras de sanidade: nome
    sanitizado, extensão normalizada para ".ufdr" minúsculo (o seletor usa glob("*.ufdr"),
    case-sensitive), gravação em blocos e nada do corpo na memória do processo (o que vale também
    para restaurar_arquivo_ufdr_upload e salvar_documento_enviado).

    O corpo é gravado em `<token>.ufdr.parcial` — o nome vem do token que o front sorteia e passa
    junto: é por ele que GET /api/investigacao/ufdr-upload/progresso acha o arquivo pelo tamanho
    em disco enquanto a gravação roda, SEM estado compartilhado entre workers. O `.parcial` não
    casa com o glob("*.ufdr") do seletor e, órfão (processo morto no meio), é varrido pelo
    limpar_ufdr_parciais_orfas no startup. O nome final é REIVINDICADO NO FIM, com criação
    exclusiva ("xb") — como em salvar_documento_enviado, dois investigadores enviando o mesmo
    nome ao mesmo tempo não sobrescrevem a prova um do outro; quem chega depois ganha sufixo. A
    janela em que o nome final existe vazio (entre a reivindicação e o os.replace) é de
    microssegundos, e não do upload inteiro, porque a posse acontece depois de gravar tudo.
    Levanta ValueError com a mensagem que a tela mostra: nome inválido, extensão que não é .ufdr
    ou arquivo vazio."""
    from funcoes.documentos_utils import _nome_arquivo_seguro

    nome = _nome_arquivo_seguro(nome_original)
    if not nome:
        raise ValueError("Nome de arquivo inválido.")
    extensao = Path(nome).suffix.lower()
    if extensao != ".ufdr":
        raise ValueError(f"{nome}: envie o arquivo .ufdr (extensão {extensao or 'nenhuma'} não é aceita).")
    raiz = nome[: len(nome) - len(Path(nome).suffix)]
    nome = raiz + ".ufdr"

    if not token or not _RE_TOKEN_UPLOAD.match(token):
        raise ValueError("Token de acompanhamento inválido.")

    # Delegacia com nó ativo (docs/no_descentralizado.md): o upload VAI para a pasta da delegacia,
    # não para o central — é lá que a evidência mora e de onde a extração a lerá. O central
    # repassa o corpo em streaming (PUT /no/enviar) sem nunca materializar os GB, e o NÓ decide o
    # nome final (reivindicação exclusiva na pasta dele). O progresso acompanha pelo mesmo
    # mecanismo: .parcial nomeado pelo token, perguntado ao nó em progresso_ufdr_upload.
    from funcoes import no_utils

    no = no_utils.servidor_ativo_para(subpasta)
    if no is not None:
        from funcoes import no_ponte

        relativo = f"{subpasta}/{nome}" if subpasta else nome
        try:
            return no_ponte.enviar_arquivo(no, fluxo, relativo, token=token)
        except no_ponte.NoIndisponivel as exc:
            raise ValueError(str(exc)) from exc

    pasta = _subpasta_delegacia(subpasta)
    pasta.mkdir(parents=True, exist_ok=True)
    parcial = pasta / f"{token}.ufdr.parcial"

    total = 0
    # Digestos calculados DURANTE a gravação (md5+sha1, os mesmos da importação) e gravados no
    # sidecar <nome>.hashes.json: quando a extração chegar, a passada de hash encontra o sidecar
    # e não relê os GB. O sidecar é melhor esforço — sem ele a importação só hasheia normal.
    hasheador = HasheadorStreaming(_ALGORITMOS_HASH_UFDR)
    try:
        with open(parcial, "wb") as saida:
            while True:
                bloco = fluxo.read(_BLOCO_UPLOAD)
                if not bloco:
                    break
                total += len(bloco)
                hasheador.atualizar(bloco)
                saida.write(bloco)
        if total == 0:
            raise ValueError(f"{nome}: arquivo vazio.")

        # Só aqui o nome final é decidido: a gravação inteira já aconteceu, então a posse é curta
        # e a conferência "não sobrescrever prova de ninguém" vale contra o que existir agora (e
        # não contra o que existia horas atrás, quando o upload começou).
        sufixo = 1
        while True:
            nome_final = raiz + (f" ({sufixo})" if sufixo > 1 else "") + ".ufdr"
            alvo = pasta / nome_final
            try:
                open(alvo, "xb").close()
                break
            except FileExistsError:
                if sufixo > 999:
                    raise ValueError(f"{nome}: já existem arquivos demais com este nome em data/.")
                sufixo += 1
        os.replace(parcial, alvo)
    except BaseException:
        # nada de meio-arquivo em data/: o .parcial (e o alvo vazio, se a posse chegou a
        # acontecer) somem, senão a listagem mostraria um .ufdr que não está inteiro e a
        # extração falharia depois, longe daqui
        parcial.unlink(missing_ok=True)
        if "alvo" in locals() and alvo.exists() and alvo.stat().st_size == 0:
            alvo.unlink(missing_ok=True)
        raise

    gravar_sidecar_hashes(alvo, hasheador.finalizar(), total)
    logger.info("UFDR enviado pelo navegador: %s (%s bytes)", alvo, total)
    return {"nome": alvo.name, "tamanho_bytes": total}


def progresso_ufdr_upload(token: str, subpasta: str | None = None) -> dict | None:
    """Tamanho em disco do .parcial de um upload de .ufdr em andamento, para o front mostrar o
    progresso da fase SERVIDOR (a barra do navegador cobre só o envio dos bytes; gravar o spool
    em data/ demora o mesmo tanto num arquivo de dezenas de GB e acontece DEPOIS de o navegador
    terminar de mandar). Devolve None se não houver upload com esse token — sem estado em memória
    nem no banco: a resposta sai do tamanho do arquivo em disco, então funciona com qualquer
    número de workers. O token é opaco e é o front que sorteia; o tamanho de um .parcial de
    outra pessoa na MESMA delegacia é o único vazamento possível, e o volume da pasta já é
    visível para quem pode listá-la. O token passa pela mesma regex da gravação: sem isso um
    token "../../x" viraria stat() fora da delegacia (oráculo de existência/tamanho). Ver F4."""
    if not token or not _RE_TOKEN_UPLOAD.match(token):
        return None
    pasta = _subpasta_delegacia(subpasta)
    caminho = pasta / f"{token}.ufdr.parcial"
    try:
        return {"bytes": caminho.stat().st_size}
    except OSError:
        pass
    # upload de delegacia com nó: o .parcial está na pasta do NÓ (salvar_ufdr_enviado repassou) —
    # a pergunta vai à mesma pasta pela ponte, com o mesmo nome
    from funcoes import no_utils

    no = no_utils.servidor_ativo_para(subpasta)
    if no is None:
        return None
    from funcoes import no_ponte

    bytes_no = no_ponte.progresso_envio(no, f"{subpasta}/{token}.ufdr.parcial")
    return {"bytes": bytes_no} if bytes_no is not None else None


def limpar_ufdr_parciais_orfas(idade_minima_segundos: int = 24 * 60 * 60) -> int:
    """Remove arquivos `.ufdr.parcial` deixados por uploads interrompidos (processo morreu no meio
    da gravação). Varre a raiz de data/ e as subpastas de delegacia — exatamente os lugares em que
    salvar_ufdr_enviado grava. Só toca em arquivos antigos, para um upload em andamento em OUTRO
    worker da API não ser apagado por engano (mesmo cuidado do predicado temporal de
    liberar_extracoes_travadas). Devolve quantos removeu."""
    agora = time.time()
    removidos = 0
    pastas = [PASTA_DADOS]
    if PASTA_DADOS.is_dir():
        pastas += [c for c in PASTA_DADOS.iterdir() if c.is_dir()]
    for pasta in pastas:
        for caminho in pasta.glob("*.ufdr.parcial"):
            try:
                if agora - caminho.stat().st_mtime < idade_minima_segundos:
                    continue
                caminho.unlink()
                removidos += 1
                logger.warning("Removido .ufdr.parcial órfão: %s", caminho)
            except OSError:
                logger.exception("Não foi possível remover .ufdr.parcial órfão: %s", caminho)
    return removidos


def _gravar_hash_extracao(id_extracao: int, hashes: dict) -> None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE investigacao.inq_tb02_extracoes
                SET co_hash_md5 = %s, co_hash_sha1 = %s, co_hash_sha256 = %s
                WHERE id_extracao = %s
                """,
                (hashes["md5"], hashes["sha1"], hashes.get("sha256"), id_extracao),
            )
        conn.commit()


class InqueritoDeOutraDelegacia(PermissionError):
    """O número informado já existe e pertence a outra unidade. As rotas traduzem em 403."""


def _conferir_delegacia_do_inquerito(cur, numero_inquerito: str, id_inquerito: int,
                                      id_usuario: int | None) -> bool:
    """Barra a "adesão" automática a um inquérito de OUTRA delegacia. Devolve True quando a
    adesão é da mesma unidade (e o auto-grant abaixo pode acontecer); False quando passou por
    bypass (admin global ou chamada sem usuário — sem auto-grant, sem linha espúria).

    O auto-grant logo abaixo é intencional e útil: é assim que um colega da mesma unidade se junta
    a um inquérito já criado, sem depender de alguém conceder acesso antes. O problema é que as
    cinco rotas que chegam aqui (extrair, extrair-documento, extrair-rif, extrair-simba,
    extrair-iped) só validam a PASTA DE ORIGEM do arquivo (delegacia_permitida) — nunca o número
    do inquérito, que vem solto no corpo e casa com um regex frouxo. Bastava anexar um .txt da
    própria pasta informando o número de outra unidade para ganhar acesso permanente ao inquérito
    dela: conversas, anexos, RIF, SIMBA, IPED, grafo e IA.

    A checagem é feita AQUI, e não nas rotas, de propósito: assim ela vale para as cinco de uma
    vez e não há como uma sexta esquecer. A consulta extra só acontece no caminho de conflito
    (inquérito já existia), que não é quente.

    Deixa passar em dois casos: administrador (fiscalização global, sem auto-grant) e mesma
    unidade (com auto-grant). Inquérito legado sem no_delegacia NÃO passa mais: a unidade do
    usuário é autodeclarada no cadastro (ver A2), então "sem unidade" era adesão livre a
    qualquer caso antigo — um administrador regulariza o cadastro antes. Ver A6.

    Efeito colateral desejado: NU_INQUERITO é UNIQUE global (db/init/08_inqueritos.sql), então
    duas unidades com a mesma numeração eram silenciosamente FUNDIDAS num inquérito só. Agora isso
    vira um erro visível em vez de um vazamento."""
    if id_usuario is None:
        return False
    cur.execute(
        """
        SELECT i.no_delegacia, u.in_admin, d.no_delegacia
        FROM investigacao.inq_tb01_inqueritos i
        CROSS JOIN seguranca.usr_tb01_usuarios u
        LEFT JOIN seguranca.delegacias d ON d.id_delegacia = u.id_delegacia
        WHERE i.id_inquerito = %s AND u.id_usuario = %s
        """,
        (id_inquerito, id_usuario),
    )
    linha = cur.fetchone()
    if linha is None:
        return False
    do_inquerito, eh_admin, do_usuario = linha
    if eh_admin:
        return False
    alvo = _sanitizar_delegacia(do_inquerito)
    if alvo is None:
        raise InqueritoDeOutraDelegacia(
            f"O inquérito {numero_inquerito} ainda não tem unidade registrada. "
            "Peça a um administrador para regularizar o cadastro antes de extrair para ele."
        )
    if alvo == _sanitizar_delegacia(do_usuario):
        return True
    raise InqueritoDeOutraDelegacia(
        f"O inquérito {numero_inquerito} já está cadastrado na unidade {do_inquerito}. "
        "Peça acesso a quem o criou, ou confira o número informado."
    )


def _obter_ou_criar_inquerito(cur, numero_inquerito: str, id_usuario: int | None,
                              delegacia: str | None = None,
                              in_caso_inteligencia: bool = False) -> int:
    """Garante que existe uma linha em INQ_TB01_INQUERITOS para o número informado; devolve o ID.

    Se o número já existir e quem está extraindo não for o criador, concede a ele
    permissão de acesso automaticamente (é assim que alguém "se junta" a um inquérito
    já existente, sem precisar que o criador conceda manualmente antes) — desde que seja da
    MESMA delegacia; ver _conferir_delegacia_do_inquerito.

    Se delegacia for informada e o inquérito ainda não tiver no_delegacia preenchido,
    atualiza o registro existente.

    in_caso_inteligencia=True marca a linha como caso de inteligência (IN_CASO_INTELIGENCIA).
    Quando o número vem vazio nesse modo (caso não tem número de ePol), um número único é
    gerado no formato "CASO-AAAA.NNNN" — com retry no ON CONFLICT, para corridas entre
    importações simultâneas.

    Concorrência: usa INSERT ... ON CONFLICT DO NOTHING + SELECT para ser atômico.
    O padrão anterior (SELECT → INSERT) permitia race: duas requisições simultâneas
    com o mesmo nu_inquerito passavam no SELECT com row=None e ambas tentavam INSERT,
    gerando IntegrityError (UNIQUE nu_inquerito). O ON CONFLICT elimina a janela.
    Ver db/init/08_inqueritos.sql: NU_INQUERITO UNIQUE."""
    numero_inquerito = (numero_inquerito or "").strip()
    if in_caso_inteligencia and not numero_inquerito:
        # Número gerado: "CASO-AAAA.NNNN" (sequência por ano). O INSERT abaixo com
        # ON CONFLICT DO NOTHING protege contra duas importações simultâneas; o loop
        # só segue adiante para o próximo número quando o conflito acontece.
        ano = datetime.now().year
        for _ in range(50):
            cur.execute(
                "SELECT count(*) FROM investigacao.inq_tb01_inqueritos WHERE nu_inquerito LIKE %s",
                (f"CASO-{ano}.%",),
            )
            seq = (cur.fetchone()[0] or 0) + 1
            candidato = f"CASO-{ano}.{seq:04d}"
            cur.execute(
                """
                INSERT INTO investigacao.inq_tb01_inqueritos
                    (nu_inquerito, in_caso_inteligencia, id_usuario_criador, no_delegacia)
                VALUES (%s, true, %s, %s)
                ON CONFLICT (nu_inquerito) DO NOTHING
                RETURNING id_inquerito
                """,
                (candidato, id_usuario, delegacia),
            )
            row = cur.fetchone()
            if row is not None:
                return row[0]
        raise RuntimeError("Não foi possível gerar um número único para o caso de inteligência.")

    cur.execute(
        """
        INSERT INTO investigacao.inq_tb01_inqueritos
            (nu_inquerito, in_caso_inteligencia, id_usuario_criador, no_delegacia)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (nu_inquerito) DO NOTHING
        RETURNING id_inquerito, id_usuario_criador
        """,
        (numero_inquerito, in_caso_inteligencia, id_usuario, delegacia),
    )
    row = cur.fetchone()
    if row is not None:
        # Inserção venceu a corrida — somos o criador.
        id_inquerito = row[0]
        # Permissão não é necessária para o criador; delegacia já foi inserida.
        return id_inquerito

    # Conflito: outra transação inseriu primeiro — busca a linha já commitada.
    cur.execute(
        "SELECT id_inquerito, id_usuario_criador FROM investigacao.inq_tb01_inqueritos WHERE nu_inquerito = %s",
        (numero_inquerito,),
    )
    fetched = cur.fetchone()
    if fetched is None:
        # Caso raro de corrida com ROLLBACK da outra transação — tenta novamente.
        cur.execute(
            """
            INSERT INTO investigacao.inq_tb01_inqueritos
                (nu_inquerito, in_caso_inteligencia, id_usuario_criador, no_delegacia)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (nu_inquerito) DO NOTHING
            RETURNING id_inquerito
            """,
            (numero_inquerito, in_caso_inteligencia, id_usuario, delegacia),
        )
        row2 = cur.fetchone()
        if row2 is not None:
            return row2[0]
        cur.execute(
            "SELECT id_inquerito, id_usuario_criador FROM investigacao.inq_tb01_inqueritos WHERE nu_inquerito = %s",
            (numero_inquerito,),
        )
        fetched = cur.fetchone()
        if fetched is None:
            raise RuntimeError(f"Falha ao obter/criar inquérito {numero_inquerito!r}")

    id_inquerito, id_usuario_criador = fetched
    # antes de qualquer escrita: o inquérito já existia, então é aqui que a adesão automática
    # aconteceria — e é aqui que ela é barrada se for de outra unidade (ou sem unidade, ver A6)
    mesma_unidade = _conferir_delegacia_do_inquerito(cur, numero_inquerito, id_inquerito, id_usuario)
    if delegacia:
        cur.execute(
            """
            UPDATE investigacao.inq_tb01_inqueritos
            SET no_delegacia = %s
            WHERE id_inquerito = %s AND (no_delegacia IS NULL OR no_delegacia = '')
            """,
            (delegacia, id_inquerito),
        )
    # auto-grant SÓ na mesma unidade: _conferir deixou passar admin global e chamadas sem
    # usuário também, mas adesão automática não é para eles (o admin já enxerga tudo) — evita
    # linha espúria e fecha a porta para qualquer caminho que chegue aqui sem unidade. Ver A2.
    if id_usuario is not None and id_usuario != id_usuario_criador and mesma_unidade:
        cur.execute(
            """
            INSERT INTO investigacao.inq_tb03_permissoes (id_inquerito, id_usuario)
            VALUES (%s, %s)
            ON CONFLICT (id_inquerito, id_usuario) DO NOTHING
            """,
            (id_inquerito, id_usuario),
        )
    return id_inquerito


def no_ponte_arquivo_existe(no: dict, relativo: str) -> bool:
    """Pergunta ao nó se o arquivo está na pasta data() dele. Qualquer erro de rede conta como
    "não está" — é a checagem que decide entre 404 honesto e mandar o job para um nó que não tem
    o arquivo; melhor recusar na hora do que enfileirar fútil."""
    from funcoes import no_ponte

    try:
        return no_ponte.arquivo_existe(no, relativo)
    except Exception:
        logger.warning("Falha ao consultar o nó %s sobre %s", no.get("no_servidor"), relativo)
        return False


def _resolver_servidor_da_extracao(id_servidor: int) -> dict | None:
    """Resolve (e valida ativo) o nó de uma extração na hora de processar. None se foi removido
    ou inativado desde que a extração foi enfileirada — o despachante traduz em erro claro."""
    from funcoes import no_utils

    no = no_utils.obter(id_servidor)
    if no is None or not no.get("in_ativa"):
        return None
    return no


def iniciar_extracao(nome_arquivo: str, numero_inquerito: str, id_usuario: int | None = None,
                     modelo_transcricao: str | None = None, subpasta: str | None = None,
                     transcrever_video: bool = False, delegacia: str | None = None,
                     in_caso_inteligencia: bool = False) -> dict:
    """Cria a linha de extração (status 'pendente') e dispara o parsing em background; devolve
    {"id_extracao", "id_inquerito"} (o id do inquérito permite à tela abrir o item recém-criado,
    inclusive quando o número foi gerado automaticamente para um caso de inteligência).
    modelo_transcricao: opcional — nome do modelo faster-whisper (ex.: "base", "small", "large-v3").
    Se omitido, usa TRANSCRICAO_MODELO do .env.
    transcrever_video: se True, os anexos de vídeo entram na transcrição junto com os de áudio.
    in_caso_inteligencia: quando True, o inquérito nasce como caso de inteligência (número gerado
    automaticamente se não informado).

    Arquivo no NÓ (docs/no_descentralizado.md): o arquivo não está no data() do central mas a
    delegacia tem nó ativo que O tem? A extração nasce com ID_SERVIDOR — o despachante então pula
    a resolução local e _processar_extracao lê o .ufdr por faixas via HTTP, com o hash calculado
    no próprio nó. O arquivo local manda: se ele existe, o central processa em casa, como sempre
    (deixa o caminho de migração — reprocessar extrações antigas — funcionando sem esforço)."""
    from funcoes import no_utils

    caminho = _subpasta_delegacia(subpasta) / nome_arquivo
    id_servidor = None
    if not caminho.is_file():
        no = no_utils.servidor_ativo_para(delegacia)
        if no is None or not no_ponte_arquivo_existe(no, nome_arquivo):
            raise FileNotFoundError(nome_arquivo)
        id_servidor = no["id_servidor"]
        # o NO_ARQUIVO do nó é o caminho RELATIVO à pasta dele — mesmo esquema de subpastas
        # (data/<delegacia>/...) do central, então o relativo local serve dos dois lados
        nome_arquivo = caminho.relative_to(PASTA_DADOS).as_posix()

    with get_connection() as conn:
        with conn.cursor() as cur:
            id_inquerito = _obter_ou_criar_inquerito(cur, numero_inquerito, id_usuario, delegacia,
                                                     in_caso_inteligencia)
            cur.execute(
                """
                INSERT INTO investigacao.inq_tb02_extracoes
                    (id_inquerito, no_arquivo, cd_status, id_usuario, tx_modelo_transcricao,
                     in_transcrever_video, id_servidor)
                VALUES (%s, %s, 'pendente', %s, %s, %s, %s)
                RETURNING id_extracao
                """,
                (id_inquerito, nome_arquivo, id_usuario, modelo_transcricao, transcrever_video,
                 id_servidor),
            )
            id_extracao = cur.fetchone()[0]
        conn.commit()

    # A extração NÃO sobe uma thread aqui. Ela fica 'pendente' e quem a tira da fila é o
    # despachante (_despachante_extracoes), respeitando o teto de simultâneas. Antes daqui era uma
    # thread por extração, sem teto nenhum: cinco .ufdr de dezenas de GB ao mesmo tempo disputavam
    # disco, memória (cada um sobe TRANSCRICAO_THREADS processos de whisper) e conexões.
    # O evento é só para não esperar o tique de 10 s quando a fila está vazia.
    _EVENTO_NOVA_EXTRACAO.set()
    return {"id_extracao": id_extracao, "id_inquerito": id_inquerito}


# Teto de extrações rodando ao mesmo tempo NO SISTEMA e o intervalo de checagem da fila.
_ESPERA_DESPACHANTE = 10.0
_EVENTO_NOVA_EXTRACAO = threading.Event()
_despachante_ligado = False

# Chave do advisory lock que serializa a reivindicação de uma extração da fila. Arbitrária, só não
# pode colidir com as outras do sistema: 72301 (migrações), 72302 (vaga de job pesado),
# 72304 (identificação de RIF).
_LOCK_FILA_EXTRACAO = 72303


def _limite_extracoes() -> int:
    from funcoes import desempenho_utils

    return max(1, desempenho_utils.valor_int("EXTRACOES_SIMULTANEAS", 2))


# Teto das extrações DELEGADAS a nós em 'processando' no central. Não é limite de CPU (a CPU é a
# de cada estação, com o teto local dela) — é só o freio para o recebedor de entrega não abrir
# centenas de conexões/INSERTs simultâneos contra o Postgres de uma vez.
_LIMITE_REMOTAS = 100


def _reivindicar_extracao() -> dict | None:
    """Tira a próxima extração da fila, se houver vaga. Devolve {id_extracao, no_arquivo} ou None.

    Um statement só, dentro de uma transação: o advisory lock fecha a janela entre contar as que
    estão rodando e marcar mais uma, e o SKIP LOCKED evita que dois workers disputem a mesma
    linha. FIFO por dt_upload, para não haver fila do lado de quem clicou primeiro.

    EXTRACOES_SIMULTANEAS limita a CPU do CENTRAL — ele só conta as extrações LOCAIS. As de NÓ
    (docs/no_descentralizado.md, "nó extrai") são processadas na estação, com o teto LOCAL do
    próprio nó; o central apenas entrega e recebe, então despachar 2-de-cada-vez enforaria as
    50 delegacias na janela de um laptop que não é mais usada por elas. Um teto alto vale como
    rede de segurança para o recebedor de entrega não afogar o Postgres."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(%s)", (_LOCK_FILA_EXTRACAO,))
            cur.execute(
                """
                WITH alvo AS (
                    SELECT e.id_extracao
                    FROM investigacao.inq_tb02_extracoes e
                    WHERE e.cd_status = 'pendente'
                      AND (
                            (e.id_servidor IS NULL
                             AND (SELECT count(*) FROM investigacao.inq_tb02_extracoes
                                   WHERE cd_status = 'processando' AND id_servidor IS NULL) < %s)
                         OR (e.id_servidor IS NOT NULL
                             AND (SELECT count(*) FROM investigacao.inq_tb02_extracoes
                                   WHERE cd_status = 'processando' AND id_servidor IS NOT NULL) < %s)
                      )
                    ORDER BY e.dt_upload
                    FOR UPDATE OF e SKIP LOCKED
                    LIMIT 1
                )
                UPDATE investigacao.inq_tb02_extracoes e
                   SET cd_status = 'processando', dt_inicio_processamento = now(),
                       dt_ultima_atividade = now(),
                       -- geração do despacho: TODO claim sobe. O nó carimba este número em
                       -- cada lote/estado, e o central recusa (409) a passada de uma geração
                       -- anterior — o redespacho do watchdog nunca mais divide o banco com
                       -- uma entrega viva de uma partição de rede recém-curada
                       nu_geracao = e.nu_geracao + 1
                  FROM alvo
                 WHERE e.id_extracao = alvo.id_extracao
                 RETURNING e.id_extracao, e.no_arquivo, e.id_servidor,
                           e.nu_geracao,
                           (SELECT i.nu_inquerito FROM investigacao.inq_tb01_inqueritos i
                             WHERE i.id_inquerito = e.id_inquerito)
                 """,
                 (_limite_extracoes(), _LIMITE_REMOTAS),
             )
            row = cur.fetchone()
        conn.commit()
    if not row:
        return None
    return {"id_extracao": row[0], "no_arquivo": row[1], "id_servidor": row[2],
            "nu_geracao": row[3], "nu_inquerito": row[4]}


def _marcar_erro_extracao(id_extracao: int, mensagem: str) -> None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE investigacao.inq_tb02_extracoes "
                "SET cd_status = 'erro', cd_fase = NULL, qt_progresso_fase = NULL, "
                "tx_erro = %s, dt_fim_processamento = now() WHERE id_extracao = %s",
                (mensagem[:2000], id_extracao),
            )
        conn.commit()


def _reverter_claim_extracao(id_extracao: int) -> None:
    """Devolve uma extração reivindicada para 'pendente' quando algo falhou ENTRE o claim e o
    início do worker (resolver servidor, localizar .ufdr, subir a thread). Sem isto a linha
    ficava em 'processando' sem dono até o reaper de 60 min — stall invisível (ver C7). O
    UPDATE é condicional: se um worker legítimo já assumiu, não mexe."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE investigacao.inq_tb02_extracoes "
                    "SET cd_status = 'pendente', dt_ultima_atividade = now() "
                    "WHERE id_extracao = %s AND cd_status = 'processando'",
                    (id_extracao,),
                )
            conn.commit()
    except Exception:
        logger.exception("Falha ao reverter claim da extração %s", id_extracao)


def _despachar_linha(linha: dict) -> None:
    """Despacha UMA extração já reivindicada: delega ao nó (remota) ou sobe a thread local.

    Extraída do corpo do despachante para que o caminho claim→início tenha reversão (ver C7):
    qualquer exceção aqui devolve a linha para 'pendente'. Erro definitivo (nó removido,
    arquivo sumido, nó recusou) continua marcado 'erro' na hora, sem exceção."""
    servidor = None
    if linha.get("id_servidor") is not None:
        # extração de nó (docs/no_descentralizado.md): o ARQUIVO e a CPU são de lá.
        # Desde "nó extrai", o central não abre mais o .ufdr remoto: ele DELEGA o
        # processamento (POST /no/extrair) e recebe de volta só os dados estruturados
        # em lotes (funcoes/no_entrega.py), reproduzidos aqui com as mesmas funções
        # de gravação. Se o nó foi removido/desativado desde o enfileiramento, erro
        # claro — igual ao comportamento antigo de resolver o servidor.
        servidor = _resolver_servidor_da_extracao(linha["id_servidor"])
        if servidor is None:
            _marcar_erro_extracao(
                linha["id_extracao"],
                "O servidor descentralizado que guardava este arquivo não está mais "
                "cadastrado/ativo no central. Reative-o (ou reimporte o arquivo no "
                "central) e rode de novo.")
            return
        from funcoes import no_ponte
        try:
            from funcoes import no_entrega
            if no_entrega.ja_tem_entrega(linha["id_extracao"]):
                # redespacho depois de queda no meio da entrega: apaga a tentativa
                # parcial antes (o nó vai reemitir o arquivo inteiro do lote 0). A
                # GERAÇÃO já subiu no claim (_reivindicar_extracao): se uma passada da
                # geração anterior ainda estiver viva lá no nó (partição recém-curada),
                # o primeiro lote/estado dela volta 409, ela se cancela, e o /no/extrair
                # abaixo entrega a ela a ordem de sair do caminho (no_extracao.aceitar)
                no_entrega.reiniciar_entrega(linha["id_extracao"])
            modelo_tx, video = _obter_config_transcricao(linha["id_extracao"])
            no_ponte.iniciar_extracao_remota(
                servidor, linha["id_extracao"], linha["no_arquivo"], modelo_tx, video,
                numero_inquerito=linha.get("nu_inquerito"),
                geracao=linha.get("nu_geracao") or 0)
        except FileNotFoundError as exc:
            _marcar_erro_extracao(linha["id_extracao"], str(exc))
        except Exception as exc:
            _marcar_erro_extracao(
                linha["id_extracao"],
                f"O servidor da delegacia não aceitou processar esta extração: {exc}. "
                "Confira se ele está online e na mesma versão do central, e rode de novo.")
        return
    # o caminho é resolvido AGORA: a linha pode ter sido enfileirada há minutos, e o
    # organizador de arquivos pode ter movido o .ufdr nesse meio-tempo
    caminho = _localizar_ufdr(linha["no_arquivo"])
    if caminho is None:
        _marcar_erro_extracao(
            linha["id_extracao"],
            "O arquivo .ufdr não foi encontrado na pasta de dados quando chegou a vez "
            "desta extração na fila. Restaure o arquivo e importe de novo.")
        logger.error("Extração %s falhou: arquivo .ufdr não encontrado na pasta de dados (%s)",
                     linha["id_extracao"], linha["no_arquivo"])
        return
    threading.Thread(target=_processar_extracao,
                     args=(linha["id_extracao"], caminho), daemon=True).start()


def _despachante_extracoes() -> None:
    """Tira extrações da fila conforme abre vaga. Roda numa thread daemon, uma por worker.

    A fila é a própria INQ_TB02_EXTRACOES em 'pendente', e é por isso que ela é DURÁVEL: uma
    extração enfileirada sobrevive a um restart da API. Com a thread por extração de antes, um
    restart matava a thread e a linha só virava 'erro' 60 minutos depois, pelo watchdog — o
    analista perdia o trabalho e tinha que reimportar."""
    while True:
        try:
            _EVENTO_NOVA_EXTRACAO.wait(timeout=_ESPERA_DESPACHANTE)
            _EVENTO_NOVA_EXTRACAO.clear()
            liberar_extracoes_travadas()  # devolve a vaga de um worker que morreu
            # 'finalizar' de nó chegou e o central morreu no meio da cauda: retoma os
            # fechamentos órfãos (cd_fase='fechando' parada) em vez de deixar a linha
            # presa em 'processando' até o vigia de 60 min marcá-la erro
            try:
                from funcoes import no_entrega
                no_entrega.recuperar_fechamentos()
            except Exception:
                logger.exception("Falha ao procurar fechamentos de nó órfãos")
            while True:
                linha = _reivindicar_extracao()
                if linha is None:
                    break
                try:
                    _despachar_linha(linha)
                except Exception:
                    # Falha entre o claim e o início do worker (resolver servidor, localizar
                    # .ufdr, Thread.start sob exaustão): devolve para 'pendente' em vez de
                    # deixar a linha em 'processando' sem dono até o reaper de 60 min (ver C7).
                    # Os caminhos de erro definitivo (_marcar_erro_extracao) não levantam,
                    # então este except só pega o inesperado.
                    logger.exception("Falha ao despachar extração %s; devolvendo à fila",
                                     linha.get("id_extracao"))
                    _reverter_claim_extracao(linha["id_extracao"])
        except Exception:
            logger.exception("Falha no despachante de extrações; seguindo no próximo ciclo")
            time.sleep(_ESPERA_DESPACHANTE)


def iniciar_despachante_extracoes() -> None:
    """Sobe o despachante no startup da API (main.py). Idempotente."""
    global _despachante_ligado
    if _despachante_ligado:
        return
    _despachante_ligado = True
    threading.Thread(target=_despachante_extracoes, daemon=True,
                     name="despachante-extracoes").start()
    # há extração enfileirada de antes do restart? o primeiro ciclo já a pega
    _EVENTO_NOVA_EXTRACAO.set()


# ---------------------------------------------------------------------------
# Permissões de acesso a inquérito — admin vê tudo; usuário comum só vê o que
# criou ou o que lhe foi concedido em INQ_TB03_PERMISSOES (ver também o
# auto-grant em _obter_ou_criar_inquerito, acima).
# ---------------------------------------------------------------------------

def usuario_tem_acesso_inquerito(id_inquerito: int, usuario: dict, *, escrita: bool = False) -> bool:
    """Devolve True se o usuário pode ver este inquérito: é admin, é quem criou, ou tem permissão concedida.

    Toda decisão de acesso a dado de inquérito passa por aqui — tanto a dependency
    exigir_acesso_inquerito quanto as checagens inline das rotas que recebem
    id_conversa/id_anexo/id_entidade —, por isso é aqui que a auditoria de acesso é gravada
    (funcoes/auditoria_acesso.py), com resultado permitido/negado. O admin também é auditado: ele
    enxerga todos os inquéritos, e é justamente de quem TEM acesso que a trilha precisa dar conta.

    escrita=True é para as rotas que ALTERAM o inquérito (apelido, transcrição, associação,
    entidades, marcadores, disparo de job…): aí ver-tudo não basta — precisa ser criador,
    permitido, ou ter edita_todas_delegacias (inteligência/administrador). Sem isso o perfil
    auditoria, que é leitura global de propósito, virava mutador universal. Ver A1."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1 FROM investigacao.inq_tb01_inqueritos i
                WHERE i.id_inquerito = %s
                  AND (i.id_usuario_criador = %s
                       OR EXISTS (
                           SELECT 1 FROM investigacao.inq_tb03_permissoes p
                           WHERE p.id_inquerito = i.id_inquerito AND p.id_usuario = %s
                       ))
                """,
                (id_inquerito, usuario["id_usuario"], usuario["id_usuario"]),
            )
            vinculado = cur.fetchone() is not None
    if vinculado:
        registrar_acesso(usuario["id_usuario"], id_inquerito, True)
        return True
    if not escrita and usuario.get("ve_todas_delegacias"):
        registrar_acesso(usuario["id_usuario"], id_inquerito, True)
        return True
    if escrita and usuario.get("edita_todas_delegacias"):
        registrar_acesso(usuario["id_usuario"], id_inquerito, True)
        return True
    registrar_acesso(usuario["id_usuario"], id_inquerito, False)
    return False


def eh_gestor_inquerito(id_inquerito: int, usuario: dict) -> bool:
    """Devolve True se o usuário pode gerenciar as permissões deste inquérito:
    é admin global, é quem criou, ou tem permissão com in_admin = true.

    Auditado como usuario_tem_acesso_inquerito, e não por acaso: as rotas que passam por aqui
    (apagar inquérito, renomear, conceder/revogar permissão) são as mais graves do sistema.
    """
    if usuario["in_admin"]:
        registrar_acesso(usuario["id_usuario"], id_inquerito, True)
        return True
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1 FROM investigacao.inq_tb01_inqueritos i
                WHERE i.id_inquerito = %s AND i.id_usuario_criador = %s
                UNION
                SELECT 1 FROM investigacao.inq_tb03_permissoes p
                WHERE p.id_inquerito = %s AND p.id_usuario = %s AND p.in_admin = true
                """,
                (id_inquerito, usuario["id_usuario"], id_inquerito, usuario["id_usuario"]),
            )
            permitido = cur.fetchone() is not None
    registrar_acesso(usuario["id_usuario"], id_inquerito, permitido)
    return permitido


def exigir_acesso_inquerito(id_inquerito: int, usuario: dict = Depends(exigir_aprovado)) -> dict:
    """Dependency do FastAPI: exige que o usuário tenha acesso ao inquérito do path (admin, criador ou permitido)."""
    if not usuario_tem_acesso_inquerito(id_inquerito, usuario):
        raise HTTPException(status_code=403, detail="Sem permissão para este inquérito.")
    return usuario


def exigir_escrita_inquerito(id_inquerito: int, usuario: dict = Depends(exigir_aprovado)) -> dict:
    """Dependency do FastAPI para rotas que ALTERAM o inquérito: exige vínculo (criador ou
    permitido) ou edita_todas_delegacias. O perfil auditoria passa em exigir_acesso_inquerito
    mas NÃO passa aqui — é leitura global de propósito (ver A1 e usuario_tem_acesso_inquerito)."""
    if not usuario_tem_acesso_inquerito(id_inquerito, usuario, escrita=True):
        raise HTTPException(status_code=403, detail="Sem permissão para alterar este inquérito.")
    return usuario


def mesma_delegacia_inqueritos(id_inquerito_a: int, id_inquerito_b: int) -> bool:
    """True se os dois inquéritos são da mesma unidade (comparação sanitizada).

    Sem unidade registrada de um dos lados vale como "não é a mesma": compartilhar entre
    casos legados sem delegacia atravessaria unidades sem deixar rastro. Ver A3."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT no_delegacia FROM investigacao.inq_tb01_inqueritos "
                "WHERE id_inquerito = ANY(%s)",
                ([id_inquerito_a, id_inquerito_b],),
            )
            linhas = cur.fetchall()
    if len(linhas) != 2:
        return False
    a = _sanitizar_delegacia(linhas[0][0])
    b = _sanitizar_delegacia(linhas[1][0])
    return a is not None and a == b


def exigir_gestor_inquerito(id_inquerito: int, usuario: dict = Depends(exigir_aprovado)) -> dict:
    """Dependency do FastAPI: exige que o usuário possa gerenciar as permissões do inquérito
    (admin global, criador ou usuário com in_admin concedido)."""
    if not eh_gestor_inquerito(id_inquerito, usuario):
        raise HTTPException(
            status_code=403, detail="Apenas o criador do inquérito ou um administrador pode gerenciar permissões."
        )
    return usuario


def _filtro_acesso_sql(usuario: dict, alias_inquerito: str) -> tuple[str, tuple]:
    """Fragmento SQL (e seus params) que restringe uma listagem aos inquéritos visíveis pelo usuário.

    Vazio para quem vê todas as delegacias (admin, chefia de inteligência, auditoria). Para os
    demais, exige ser o criador ou ter permissão concedida."""
    if usuario.get("ve_todas_delegacias"):
        return "", ()
    condicao = (
        f" AND ({alias_inquerito}.id_usuario_criador = %s OR EXISTS ("
        f"SELECT 1 FROM investigacao.inq_tb03_permissoes p "
        f"WHERE p.id_inquerito = {alias_inquerito}.id_inquerito AND p.id_usuario = %s))"
    )
    return condicao, (usuario["id_usuario"], usuario["id_usuario"])


def listar_permissoes_inquerito(id_inquerito: int) -> list[dict]:
    """Lista os usuários com permissão adicional concedida neste inquérito (não inclui o criador)."""
    query = """
        SELECT p.id_usuario, u.no_usuario, u.no_login, p.in_admin, p.dt_concessao
        FROM investigacao.inq_tb03_permissoes p
        JOIN seguranca.usr_tb01_usuarios u ON u.id_usuario = p.id_usuario
        WHERE p.id_inquerito = %s
        ORDER BY u.no_usuario
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (id_inquerito,))
            colunas = [d.name for d in cur.description]
            return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


def conceder_permissao(id_inquerito: int, id_usuario: int, in_admin: bool = False) -> None:
    """Concede a um usuário acesso a um inquérito (idempotente).
    in_admin=true concede também a gestão de permissões."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO investigacao.inq_tb03_permissoes (id_inquerito, id_usuario, in_admin)
                VALUES (%s, %s, %s)
                ON CONFLICT (id_inquerito, id_usuario) DO UPDATE SET in_admin = %s
                """,
                (id_inquerito, id_usuario, in_admin, in_admin),
            )
        conn.commit()


def revogar_permissao(id_inquerito: int, id_usuario: int) -> bool:
    """Revoga o acesso concedido a um usuário; devolve False se não havia permissão para revogar."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM investigacao.inq_tb03_permissoes WHERE id_inquerito = %s AND id_usuario = %s",
                (id_inquerito, id_usuario),
            )
            revogado = cur.rowcount > 0
        conn.commit()
    return revogado


def obter_id_inquerito_de_conversa(id_conversa: int, fonte: str) -> int | None:
    """Resolve o id_inquerito dono de uma conversa (WhatsApp ou SMS), para checar permissão em rotas
    que só recebem id_conversa. Devolve None se a conversa não existir."""
    t = _TABELAS_CONVERSA[fonte]
    query = f"""
        SELECT e.id_inquerito
        FROM investigacao.{t['conversas']} c
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = c.id_extracao
        WHERE c.id_conversa = %s
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (id_conversa,))
            row = cur.fetchone()
            return row[0] if row else None


def obter_id_inquerito_de_contato(id_contato: int, fonte: str) -> int | None:
    """Resolve o id_inquerito dono de um contato de conversa (WhatsApp ou SMS), para checar permissão
    em rotas que só recebem id_contato. Devolve None se o contato não existir."""
    t = _TABELAS_CONVERSA[fonte]
    query = f"""
        SELECT e.id_inquerito
        FROM investigacao.{t['contatos']} c
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = c.id_extracao
        WHERE c.id_contato = %s
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (id_contato,))
            row = cur.fetchone()
            return row[0] if row else None


def _foto_de_jid(jid: str | None, mapa_fotos: dict[str, str] | None) -> str | None:
    """Caminho da foto de perfil (dentro do .ufdr) para um jid do WhatsApp, via o mapa telefone->
    caminho (ufdr_parser.indexar_fotos_perfil). O telefone é o trecho de dígitos antes do '@'."""
    if not mapa_fotos or not jid:
        return None
    m = re.match(r"(\d{10,15})", jid)
    return mapa_fotos.get(m.group(1)) if m else None


_TABELAS_DIRECAO = (
    ("wpp_tb04_mensagens", "wpp_tb02_conversas", "wpp_tb01_contatos"),
    ("sms_tb04_mensagens", "sms_tb02_conversas", "sms_tb01_contatos"),
    ("msg_tb04_mensagens", "msg_tb02_conversas", "msg_tb01_contatos"),
)


def corrigir_direcao_por_dono(cur, id_extracao: int) -> int:
    """Marca como ENVIADA toda mensagem cujo remetente é um contato do dono do aparelho.

    IN_ENVIADA é decidido mensagem a mensagem, comparando o remetente com UM jid de dono — o
    telefone do metadado (LastUsedMSISDN) ou o participante com IsPhoneOwner daquela conversa.
    Só que um aparelho costuma ter MAIS DE UMA identidade de dono (dois chips, o contato "Eu" e o
    titular do WhatsApp), e IsPhoneOwner não vem marcado em toda conversa. Onde o jid escolhido
    não era o que assinou a mensagem, a mensagem do próprio dono era gravada como recebida e
    aparecia do lado do interlocutor na tela — invertendo quem falou o quê.

    Este passe roda no fim da ingestão, quando já se sabe quem são TODOS os donos daquela extração
    (a flag é pegajosa, ver _upsert_contato), e conserta o que ficou para trás. Idempotente.
    Devolve quantas mensagens mudaram de lado."""
    total = 0
    for tab_msg, tab_conv, tab_contato in _TABELAS_DIRECAO:
        cur.execute(
            f"""
            UPDATE investigacao.{tab_msg} m
               SET in_enviada = true
            FROM investigacao.{tab_contato} ct, investigacao.{tab_conv} cv
            WHERE ct.id_contato = m.id_contato_remetente
              AND cv.id_conversa = m.id_conversa
              AND cv.id_extracao = %s
              AND ct.in_dono_dispositivo
              AND m.in_enviada IS DISTINCT FROM true
            """,
            (id_extracao,),
        )
        total += cur.rowcount
    return total


def _upsert_contato(cur, tabela: str, id_extracao: int, jid: str, nome: str | None,
                    caminho_foto: str | None = None,
                    dono_dispositivo: bool | None = None) -> int | None:
    """Insere/atualiza um contato de conversa (WPP ou SMS) pela chave natural (JID/telefone); devolve o ID.
    Para WhatsApp, grava também o caminho da foto de perfil no .ufdr (só o ponteiro, nunca o binário)."""
    if not jid:
        return None
    if tabela == "wpp_tb01_contatos":
        cur.execute(
            """
            INSERT INTO investigacao.wpp_tb01_contatos (id_extracao, nu_whatsapp_id, no_contato, co_caminho_foto, in_dono_dispositivo)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (id_extracao, nu_whatsapp_id) DO UPDATE
                SET no_contato = COALESCE(EXCLUDED.no_contato, investigacao.wpp_tb01_contatos.no_contato),
                    co_caminho_foto = COALESCE(EXCLUDED.co_caminho_foto, investigacao.wpp_tb01_contatos.co_caminho_foto),
                    -- ser dono é PEGAJOSO: IsPhoneOwner vem False na maioria das parties, e com
                    -- COALESCE o último False sobrescrevia o True já descoberto, apagando quem é
                    -- o dono do aparelho (visto na prática: o contato "Eu" acabava com false)
                    in_dono_dispositivo = CASE
                        WHEN EXCLUDED.in_dono_dispositivo IS TRUE THEN true
                        ELSE investigacao.wpp_tb01_contatos.in_dono_dispositivo
                    END
            RETURNING id_contato
            """,
            (id_extracao, jid, nome, caminho_foto, dono_dispositivo),
        )
        return cur.fetchone()[0]
    cur.execute(
        f"""
        INSERT INTO investigacao.{tabela} (id_extracao, nu_telefone, no_contato, in_dono_dispositivo)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (id_extracao, nu_telefone) DO UPDATE
            SET no_contato = COALESCE(EXCLUDED.no_contato, investigacao.{tabela}.no_contato),
                in_dono_dispositivo = CASE
                    WHEN EXCLUDED.in_dono_dispositivo IS TRUE THEN true
                    ELSE investigacao.{tabela}.in_dono_dispositivo
                END
        RETURNING id_contato
        """,
        (id_extracao, jid, nome, dono_dispositivo),
    )
    return cur.fetchone()[0]


# Releitura do FL_CANCELAR no máximo a cada tantos segundos. O laço da extração pede o
# cancelamento a cada 200 registros, o que no passe dos <file> dá dezenas de vezes por segundo —
# com conexão curta seriam dezenas de idas ao pool por segundo, só para ler um booleano que quase
# nunca muda. Dois segundos de atraso para parar são imperceptíveis na tela.
_INTERVALO_CANCELAMENTO = 2.0

# De quanto em quanto tempo a thread de batimento toca DT_ULTIMA_ATIVIDADE. Um UPDATE de uma
# linha a cada dois minutos é irrelevante para o banco e fica com folga larga diante dos
# _MINUTOS_EXTRACAO_STALE (60 min) que o watchdog espera.
_INTERVALO_VIGIA_BATIMENTO = 120.0


class _Batimento:
    """Fase, progresso e cancelamento de UMA extração, cada operação em conexão curta própria.

    Substitui os antigos _marcar_fase/_marcar_progresso/_cancelamento_solicitado, que recebiam a
    conexão de quem chamava. Existe por dois motivos:

    1. Eles eram os únicos usos da conexão FORA do laço do report.xml, e era por causa deles que
       _processar_extracao segurava uma conexão do pool do começo ao fim — atravessando o hash de
       dezenas de GB, a transcrição no faster-whisper e a indexação semântica, que juntos passam
       de horas. Poucas extrações simultâneas prendiam o pool inteiro e a aplicação respondia 500
       para todo mundo. Com o batimento independente, a conexão longa encolhe para o trecho que
       de fato está gravando.
    2. Estes três métodos são chamados de TRÊS threads (a principal, a de resultados do mp.Pool
       da transcrição e o vigia dela). Antes as três compartilhavam o mesmo objeto de conexão, e
       o commit() do callback de progresso fechava a transação em voo da thread principal. Agora
       cada chamada tem a sua conexão, então isso é seguro por construção.

    O estado guardado (pct da fase corrente, última leitura do cancelamento) fica sob lock
    justamente porque as três threads batem aqui."""

    def __init__(self, id_extracao: int) -> None:
        self.id_extracao = id_extracao
        self._lock = threading.Lock()
        self._pct = -1
        self._cancelado = False
        self._lido_em = 0.0
        self._parar_vigia = threading.Event()
        self._thread_vigia: threading.Thread | None = None

    def tocar(self) -> None:
        """Só DT_ULTIMA_ATIVIDADE: 'este worker está vivo', sem mexer em fase nem progresso."""
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE investigacao.inq_tb02_extracoes SET dt_ultima_atividade = now() "
                    "WHERE id_extracao = %s",
                    (self.id_extracao,),
                )
            conn.commit()

    def iniciar_vigia(self) -> None:
        """Sobe a thread que bate DT_ULTIMA_ATIVIDADE de tempos em tempos enquanto o worker vive.

        O batimento era efeito colateral de fase/progresso/cancelamento — ou seja, só existia
        onde alguém tinha lembrado de medir alguma coisa. As etapas do fim da extração não medem
        nada: PIX, contas de apps iOS, correção de direção e identificação de pessoas rodam
        inteiras entre duas trocas de fase, e a identificação cruza o INQUÉRITO todo. Passando de
        _MINUTOS_EXTRACAO_STALE, o watchdog dava a extração VIVA por travada, marcava 'erro' e
        liberava a vaga da fila — outra extração entrava e o teto de simultâneas era furado; no
        fim, o worker que nunca morreu gravava 'concluido' por cima.

        Com a thread, DT_ULTIMA_ATIVIDADE quer dizer o que o watchdog sempre supôs que queria: o
        processo está de pé. Ela é daemon e morre junto com o processo, então o predicado
        puramente temporal do watchdog continua valendo para worker morto — que é o caso que ele
        existe para resolver."""
        if self._thread_vigia is not None:
            return
        self._parar_vigia.clear()

        def _bater():
            while not self._parar_vigia.wait(_INTERVALO_VIGIA_BATIMENTO):
                try:
                    self.tocar()
                except Exception:
                    logger.exception("Falha no batimento da extração %s (segue tentando)",
                                     self.id_extracao)

        self._thread_vigia = threading.Thread(
            target=_bater, daemon=True, name=f"batimento-extracao-{self.id_extracao}")
        self._thread_vigia.start()

    def parar_vigia(self) -> None:
        """Encerra a thread de batimento. Idempotente; chamada no finally de _processar_extracao."""
        self._parar_vigia.set()
        thread = self._thread_vigia
        self._thread_vigia = None
        if thread is not None:
            thread.join(timeout=5)

    def fase(self, nome: str) -> None:
        """Fase corrente da extração, para o badge detalhado da tela. Zera o progresso: cada fase
        recomeça do zero, e a que não sabe medir fica sem porcentagem em vez de herdar a da
        anterior. Toca também DT_ULTIMA_ATIVIDADE — o batimento que o watchdog de extrações presas
        (liberar_extracoes_travadas) usa para distinguir lenta de travada."""
        with self._lock:
            self._pct = -1
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE investigacao.inq_tb02_extracoes "
                    "SET cd_fase = %s, qt_progresso_fase = NULL, dt_ultima_atividade = now() "
                    "WHERE id_extracao = %s",
                    (nome, self.id_extracao),
                )
            conn.commit()

    def progresso(self, feitos: float, total: float) -> None:
        """Porcentagem (0–99) da fase corrente. Mesma assinatura do `ao_progredir` que
        calcular_hashes_arquivo, indexar_indicadores, transcrever_lote e indexar_sincrono já
        esperam, então é passada direto como callback, sem lambda.

        Decide ANTES de pedir conexão: o UPDATE só sai quando o número inteiro muda. Essa guarda
        já existia, mas rodava com a conexão na mão; agora é o que evita dezenas de aquisições de
        pool por segundo no passe dos <file>.

        Trava em 99 até a fase de fato acabar: o denominador é aproximado (bytes do XML lidos com
        buffer à frente, áudios que podem falhar) e badge parado em "100%" por minutos passa
        impressão de travamento pior do que não ter número nenhum."""
        if not total:
            return
        pct = min(99, max(0, int(feitos * 100 / total)))
        with self._lock:
            if pct == self._pct:
                return
            self._pct = pct
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE investigacao.inq_tb02_extracoes "
                    "SET qt_progresso_fase = %s, dt_ultima_atividade = now() "
                    "WHERE id_extracao = %s",
                    (pct, self.id_extracao),
                )
            conn.commit()

    def cancelar_pedido(self) -> bool:
        """True se o usuário pediu para parar. Mesma assinatura do `deve_cancelar` de
        transcrever_lote. A resposta fica em cache por _INTERVALO_CANCELAMENTO; uma vez
        cancelado, não desfaz."""
        with self._lock:
            if self._cancelado:
                return True
            if (time.monotonic() - self._lido_em) < _INTERVALO_CANCELAMENTO:
                return False
            self._lido_em = time.monotonic()
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT fl_cancelar FROM investigacao.inq_tb02_extracoes WHERE id_extracao = %s",
                    (self.id_extracao,),
                )
                row = cur.fetchone()
        pedido = bool(row and row[0])
        if pedido:
            with self._lock:
                self._cancelado = True
        return pedido


# Escritas pontuais do ciclo de vida da extração. Cada uma abre e devolve a sua conexão: são
# UPDATEs de uma linha, entre fases que podem durar horas — segurar conexão entre elas é o que
# esta etapa existe para acabar.

def _marcar_inicio_extracao(id_extracao: int) -> None:
    if canal_extracao.em_modo_entrega():
        return  # nó: o central já marcou 'processando' ao delegar
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE investigacao.inq_tb02_extracoes "
                "SET cd_status = 'processando', cd_fase = 'extraindo', qt_progresso_fase = 0, "
                "dt_inicio_processamento = now(), dt_ultima_atividade = now() "
                "WHERE id_extracao = %s",
                (id_extracao,),
            )
        conn.commit()


# Algoritmos calculados na importação de .ufdr "por enquanto": o SHA-256 (o mais caro dos três)
# está desligado para encurrar a etapa de hash; as peças que dependiam dele — comparação de
# duplicidade e relatórios — tratam o digesto ausente. Os demais fluxos (RIF, SIMBA) seguem
# calculando os três.
_ALGORITMOS_HASH_UFDR = ("md5", "sha1")


def _hashes_gravadas_extracao(id_extracao: int) -> dict | None:
    """Os hashes já gravados NA LINHA da extração, ou None se faltar algum dos obrigatórios
    (md5+sha1 — o SHA-256 pode vir ausente, ver _ALGORITMOS_HASH_UFDR).

    Serve ao pulo da fase de hash em reimportações do MESMO arquivo: o requeue preserva
    CO_HASH_* da tentativa interrompida e a passada de hash (leitura completa de dezenas de
    GB) não precisa recomeçar do zero só para reconfirmar o que já está no banco."""
    if canal_extracao.em_modo_entrega():
        return None  # nó sem banco: a linha (e seus hashes) mora no central
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT co_hash_md5, co_hash_sha1, co_hash_sha256 "
                "FROM investigacao.inq_tb02_extracoes WHERE id_extracao = %s",
                (id_extracao,),
            )
            row = cur.fetchone()
    if not row or not row[0] or not row[1]:
        return None
    return {"md5": row[0], "sha1": row[1], "sha256": row[2]}


def _gravar_hashes_extracao(id_extracao: int, hashes: dict) -> None:
    if canal_extracao.estado_agora({"t": "hashes", **{k: hashes.get(k) for k in ("md5", "sha1", "sha256")}}):
        return  # nó: os digestos viajam ao central (que inclusive confere a duplicidade lá)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE investigacao.inq_tb02_extracoes "
                "SET co_hash_md5 = %s, co_hash_sha1 = %s, co_hash_sha256 = %s "
                "WHERE id_extracao = %s",
                (hashes["md5"], hashes["sha1"], hashes.get("sha256"), id_extracao),
            )
        conn.commit()


class ExtracaoDuplicada(Exception):
    """O mesmo .ufdr (mesmo SHA-256) já está no inquérito. Vira TX_ERRO com texto acionável."""


def _recusar_se_duplicada(id_extracao: int, hashes: dict) -> None:
    """Interrompe a extração se este .ufdr já foi importado no MESMO inquérito.

    Nada impedia importar duas vezes o mesmo aparelho: o nome do arquivo pode mudar, e com vários
    analistas no mesmo inquérito (cada um com a sua cópia na pasta da delegacia) isso acontece.
    O resultado é o aparelho inteiro em dobro — contagens dobradas, a mesma conversa duas vezes na
    busca, a mesma pessoa identificada duas vezes na mesa — e ninguém percebe pela tela, porque
    cada extração parece legítima sozinha.

    A comparação é pelo SHA-256, que já é calculado logo antes para a cadeia de custódia: é o
    mesmo arquivo, byte a byte, independentemente do nome. Extração em 'erro' ou 'cancelado' NÃO
    conta — reimportar depois de uma falha é justamente o caminho de recuperação.

    Se as duas cópias forem disparadas juntas e estiverem as duas em 'processando', quem recusa é
    a de ID maior: a mais antiga segue e importa, em vez de as duas se recusarem mutuamente e o
    aparelho não entrar."""
    # A importação de .ufdr hoje só calcula md5+sha1 (ver _ALGORITMOS_HASH_UFDR), então a
    # comparação cai para o MD5 quando não há SHA-256 — para o caso de uso ("o MESMO arquivo
    # byte a byte foi enviado duas vezes") a colisão prática é inexistente; o risco maior do
    # MD5, crafted-collision, exige atacante com controle do conteúdo, e aí a cadeia de custódia
    # já está perdida em outra frente. Extrações antigas têm os dois digestos.
    hashes = hashes or {}
    alvo, coluna = hashes.get("sha256"), "co_hash_sha256"
    if not alvo:
        alvo, coluna = hashes.get("md5"), "co_hash_md5"
    if not alvo:
        return
    if canal_extracao.em_modo_entrega():
        return  # nó: quem confere a duplicidade interestações é o central, ao receber os hashes
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT e.id_extracao, e.no_arquivo, e.dt_upload
                FROM investigacao.inq_tb02_extracoes e
                WHERE e.{coluna} = %s
                  AND e.id_extracao <> %s
                  AND (e.cd_status = 'concluido'
                       OR (e.cd_status = 'processando' AND e.id_extracao < %s))
                  AND e.id_inquerito = (SELECT id_inquerito
                                        FROM investigacao.inq_tb02_extracoes
                                        WHERE id_extracao = %s)
                ORDER BY e.dt_upload
                LIMIT 1
                """,
                (alvo, id_extracao, id_extracao, id_extracao),
            )
            row = cur.fetchone()
    if row is None:
        return
    quando = row[2].strftime("%d/%m/%Y %H:%M") if row[2] else "data desconhecida"
    digesto = "SHA-256" if coluna == "co_hash_sha256" else "MD5"
    raise ExtracaoDuplicada(
        f"Este .ufdr já foi importado neste inquérito: extração #{row[0]} "
        f"(\"{row[1]}\", de {quando}), com o mesmo {digesto}. Importar de novo duplicaria todo o "
        f"aparelho — conversas, contatos, arquivos e contagens. Se a intenção é reimportar, "
        f"apague antes a extração #{row[0]}."
    )


def _gravar_info_dispositivo(id_extracao: int, info: dict, *, in_nuvem: bool = False,
                             conta: str | None = None) -> None:
    data_extração = info["data_extracao"]
    if canal_extracao.estado_agora({
            "t": "info_dispositivo",
            "no_dispositivo": conta if in_nuvem else info["modelo"],
            "no_fabricante": info["fabricante"],
            "dt_extracao": data_extração.isoformat() if hasattr(data_extração, "isoformat") else data_extração,
            "in_nuvem": bool(in_nuvem)}):
        return  # nó: metadata do aparelho vira callback de estado
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE investigacao.inq_tb02_extracoes
                SET no_dispositivo = %s, no_fabricante = %s, dt_extracao = %s, in_nuvem = %s
                WHERE id_extracao = %s
                """,
                (conta if in_nuvem else info["modelo"], info["fabricante"],
                 info["data_extracao"], in_nuvem, id_extracao),
            )
        conn.commit()


def _indexar_semantica_ao_final(id_extracao: int, bat: "_Batimento") -> str | None:
    if canal_extracao.em_modo_entrega():
        return None  # nó: embeddings exigem o banco do central — roda lá após a entrega
    """Encadeia a indexação semântica como fase final da extração, se configurada. Best-effort:
    nada aqui pode derrubar a extração — sem embeddings configurados, apenas pula; uma indexação já
    em curso não é duplicada; qualquer outra falha é engolida (fica registrada no status da própria
    indexação). Só marca a fase 'indexando_semantica' quando de fato vai indexar.

    A indexação em CPU pode levar horas sem tocar em INQ_TB02_EXTRACOES (o progresso dela vai para
    EMB_TB02_INDEXACOES), então o progresso é espelhado aqui via ao_progredir — o badge mostra a
    porcentagem E o watchdog de extrações presas não a libera por engano.

    Devolve o texto do aviso quando a indexação falhou (tipicamente o modelo de embeddings não
    baixado), para a extração concluir com o alerta em vez de parecer completa."""
    from funcoes.embed_utils import IndexacaoOcupada, embeddings_configurados, indexar_sincrono, obter_indexacao

    if bat.cancelar_pedido() or not embeddings_configurados():
        return None
    id_inquerito = obter_id_inquerito_de_extracao(id_extracao)
    if id_inquerito is None:
        return None
    bat.fase("indexando_semantica")

    try:
        indexar_sincrono(id_inquerito, ao_progredir=bat.progresso)
    except IndexacaoOcupada:
        return None  # já há uma indexação rodando para este inquérito — não duplica
    except Exception as exc:
        logger.exception("Falha na indexação semântica ao final da extração %s (best-effort)", id_extracao)
        return _aviso_indexacao(str(exc))
    # _processar_indexacao engole a falha e a grava no status da própria indexação
    try:
        st = obter_indexacao(id_inquerito) or {}
    except Exception:
        return None
    if st.get("cd_status") == "erro":
        return _aviso_indexacao(st.get("tx_erro") or "erro não informado")
    return None


def _aviso_indexacao(detalhe: str) -> str:
    return ("A busca semântica não foi indexada: o modelo de embeddings não está disponível "
            f"({detalhe[:300]}). Baixe o modelo em Configurações e use \"Reindexar\".")


def _organizar_arquivo_extracao(id_extracao: int, caminho: Path) -> None:
    """Move o .ufdr já extraído para data/<delegacia>/<inquérito>/CELULAR/ e grava o novo caminho
    (relativo a data/) em NO_ARQUIVO — é por ele que os anexos são lidos depois (_localizar_ufdr).

    Só mexe em extração concluída: .ufdr que deu erro ou foi cancelado fica na pasta de entrega
    da delegacia, para o investigador conferir e reimportar. Best-effort: nada aqui derruba uma
    extração que já terminou."""
    canal = canal_extracao.emissor_ativo()
    if canal is not None:
        # nó (modo entrega): a pasta data/ é a daqui — move na estação e informa o novo relativo
        # pelo callback; o central grava em NO_ARQUIVO (é lá que a leitura de anexos procura).
        if canal.cancelar or not getattr(canal, "numero_inquerito", None):
            return  # cancelada: fica na pasta de entrega para conferir, como no central
        try:
            ufdr_cache.fechar_zip(caminho)
            relativo = organizar(caminho, canal.numero_inquerito, CATEGORIA_CELULAR)
            if relativo:
                canal.estado({"t": "arquivo_organizado", "no_arquivo": relativo})
        except Exception:
            logger.exception("Falha ao organizar %s na pasta do nó (best-effort)", caminho.name)
        return
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT i.nu_inquerito, e.cd_status
                    FROM investigacao.inq_tb02_extracoes e
                    JOIN investigacao.inq_tb01_inqueritos i ON i.id_inquerito = e.id_inquerito
                    WHERE e.id_extracao = %s
                    """,
                    (id_extracao,),
                )
                row = cur.fetchone()
                if row is None or row[1] != "concluido":
                    return
                # handle do zip em cache (leitura de anexo de uma extração anterior do mesmo
                # arquivo) segura o rename no bind mount de data/ — fecha antes de mover
                ufdr_cache.fechar_zip(caminho)
                relativo = organizar(caminho, row[0], CATEGORIA_CELULAR)
                if not relativo:
                    return
                cur.execute(
                    "UPDATE investigacao.inq_tb02_extracoes SET no_arquivo = %s WHERE id_extracao = %s",
                    (relativo, id_extracao),
                )
            conn.commit()
    except Exception:
        logger.exception("Falha ao organizar o arquivo da extração %s (best-effort)", id_extracao)


def _gravar_contagens_extracao(id_extracao: int, contagens: dict) -> None:
    if canal_extracao.estado_agora({"t": "contagens", "contagens": contagens}):
        return  # nó: as contagens viajam por callback e o central grava
    """Totais por domínio, gravados assim que o laço do report.xml termina — antes da transcrição,
    que é lenta, para o card já mostrar tudo (inclusive os botões de mensageiro)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE investigacao.inq_tb02_extracoes
                SET qt_conversas = %s, qt_mensagens = %s,
                    qt_sms_conversas = %s, qt_sms_mensagens = %s,
                    qt_msg_conversas = %s, qt_msg_mensagens = %s,
                    qt_contatos = %s, qt_ligacoes = %s,
                    qt_localizacoes = %s, qt_notificacoes = %s, qt_notas = %s,
                    qt_navegacao = %s, qt_aplicativos = %s, qt_donos = %s,
                    qt_arquivos = %s, qt_emails = %s, qt_contas_financeiras = %s,
                    qt_redes_wifi = %s, qt_senhas = %s, qt_autofill = %s,
                    qt_uso_apps = %s,
                    qt_dispositivos_reconhecidos = %s, qt_conexoes_dispositivo = %s,
                    qt_cookies = %s, qt_atividade_social = %s, qt_buscas = %s,
                    qt_envios_nuvem = %s, qt_calendario = %s, qt_uso_aplicativo = %s,
                    qt_trajetos = %s, qt_info_aparelho = %s, qt_antenas = %s,
                    qt_voicemail = %s, qt_favoritos = %s, qt_downloads = %s,
                    qt_transferencias = %s, qt_cartoes_carteira = %s, qt_gravacoes = %s,
                    qt_cartoes_credito = %s, qt_uso_rede = %s,
                    qt_atividades = %s, qt_eventos = %s, qt_acessos = %s,
                    qt_dispositivos = %s, qt_perfil = %s, qt_metadados_nuvem = %s
                WHERE id_extracao = %s
                """,
                (
                    contagens["conversas"], contagens["mensagens"],
                    contagens["sms_conversas"], contagens["sms_mensagens"],
                    contagens["msg_conversas"], contagens["msg_mensagens"],
                    contagens["contatos"], contagens["ligacoes"],
                    contagens["localizacoes"], contagens["notificacoes"], contagens["notas"],
                    contagens["navegacao"], contagens["aplicativos"], contagens["donos"],
                    contagens["arquivos"], contagens["emails"], contagens["contas_financeiras"],
                    contagens["redes_wifi"],
                    contagens["senhas"], contagens["autofill"],
                    contagens["uso_apps"],
                    contagens["dispositivos_reconhecidos"], contagens["conexoes_dispositivo"],
                    contagens["cookies"], contagens["atividade_social"], contagens["buscas"],
                    contagens["envios_nuvem"], contagens["calendario"], contagens["uso_aplicativo"],
                    contagens["trajetos"], contagens["info_aparelho"], contagens["antenas"],
                    contagens["voicemail"], contagens["favoritos"], contagens["downloads"],
                    contagens["transferencias"], contagens["cartoes_carteira"], contagens["gravacoes"],
                    contagens["cartoes_credito"], contagens["uso_rede"],
                    contagens["atividades"], contagens["eventos"], contagens["acessos"],
                    contagens["dispositivos"], contagens["perfil"], contagens["metadados_nuvem"],
                    id_extracao,
                ),
            )
        conn.commit()


def _gravar_total_contas_financeiras(id_extracao: int, total: int) -> None:
    if canal_extracao.estado_agora({"t": "contas_total", "total": total}):
        return  # nó: idem, por callback
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE investigacao.inq_tb02_extracoes SET qt_contas_financeiras = %s WHERE id_extracao = %s",
                (total, id_extracao),
            )
        conn.commit()


def _extrair_contas_de_apps_ios_extracao(id_extracao: int, caminho: Path) -> int:
    """_extrair_contas_de_apps_ios com conexão própria.

    Existe porque, antes desta etapa, a chamada usava o `cur` de um `with conn.cursor()` que já
    tinha FECHADO algumas linhas acima — em psycopg3 isso levanta InterfaceError, que o
    `except Exception` best-effort da extração engolia. Ou seja: contas de apps iOS nunca eram
    extraídas, e o log só dizia "falha (best-effort)". O mesmo valia para a direção das mensagens
    do dono (ver _corrigir_direcao_dono_extracao)."""
    if canal_extracao.em_modo_entrega():
        # nó: sem banco — a leitura do SQLite roda aqui, e cada INSERT vira registro do canal
        return _extrair_contas_de_apps_ios(None, id_extracao, caminho)
    with get_connection() as conn:
        with conn.cursor() as cur:
            qt = _extrair_contas_de_apps_ios(cur, id_extracao, caminho)
        conn.commit()
    return qt


def _corrigir_direcao_dono_extracao(id_extracao: int) -> int:
    if canal_extracao.emitir("corrigir_direcao", (id_extracao,)):
        return 0  # casa por contatos in_dono_dispositivo — reproduzível no central
    """corrigir_direcao_por_dono com conexão própria — ver _extrair_contas_de_apps_ios_extracao."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            corrigidas = corrigir_direcao_por_dono(cur, id_extracao)
        conn.commit()
    return corrigidas


def _finalizar_extracao(id_extracao: int, audios_transcritos: int, aviso: str | None = None) -> None:
    _canal = canal_extracao.emissor_ativo()
    if _canal is not None:
        _canal.finalizar({"t": "finalizar", "audios_transcritos": audios_transcritos,
                            "aviso": aviso})  # fecha lotes, entrega tudo e avisa o central
        return
    """Fecha a extração: 'cancelado' se o usuário pediu para parar, 'concluido' se não.
    aviso: o que ficou por fazer sem ser falha (modelo de transcrição/embeddings não baixado) —
    vai para TX_AVISO e o card mostra um badge de atenção.

    Zera TX_ERRO junto. Sem isso, uma extração que o watchdog tinha dado por travada e marcado
    'erro' (ver iniciar_vigia) terminava aqui como 'concluido' carregando a mensagem de erro
    antiga — o card mostrava "Concluído" e o detalhe dizia que tinha sido interrompida."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE investigacao.inq_tb02_extracoes
                SET cd_status = CASE WHEN fl_cancelar THEN 'cancelado' ELSE 'concluido' END,
                    cd_fase = NULL, qt_progresso_fase = NULL, tx_erro = NULL, tx_aviso = %s,
                    dt_fim_processamento = now(), qt_audios_transcritos = %s
                WHERE id_extracao = %s
                """,
                (aviso, audios_transcritos, id_extracao),
            )
        conn.commit()


def _laco_report_xml(cur, conn, bat: "_Batimento", id_extracao: int, zf,
                     indice_binarios, caminhos_zip, mapa_fotos, contagem_tamanhos,
                     fone_metadado, jid_dono_wpp, jid_dono_sms, contagens: dict,
                     pular_localizacoes_warrant: bool = False,
                     pular_arquivos_icloud: bool = False) -> bool:
    """Passe único pelo report.xml, gravando tudo que ele traz. Devolve True se o usuário pediu
    para cancelar no meio.

    É a única fase da extração que justifica segurar uma conexão por muito tempo: ela está
    gravando o tempo todo, com commit a cada 200 registros. Saiu de dentro de _processar_extracao
    sem nenhuma mudança de lógica, só para deixar visível que o resto da função — hash,
    transcrição, embeddings — roda sem conexão nenhuma.

    `contagens` é alterado no lugar (o chamador o usa depois para gravar os totais)."""
    # import tardio: contas_nuvem_utils importa deste módulo (ciclo se fosse no topo)
    from funcoes.contas_nuvem_utils import _gravar_acesso_conta

    processados = 0
    buffer_arquivos: list[tuple] = []
    contas_ios: dict[str, dict] = {}
    fone_dono: dict[str, dict] = {}
    mensagens_soltas: dict[str, list[dict]] = {}
    # filtro de chats duplicados do report.xml (ver _filtrar_duplicatas): selos das
    # mensagens já gravadas e conversa já aberta por chat visto no passe
    chats_vistos: dict = {}
    chats_abertos: dict = {}
    # localização compartilhada encaminhada: os dois lados do cruzamento que só
    # fecha no fim do passe (ver _resolver_localizacoes_compartilhadas)
    alvos_localizacao: dict[str, tuple] = {}
    coords_de_mensagem: dict[str, tuple] = {}
    tem_dono_user = False
    cancelado = False
    # o parser mantém {'lidos','total'} em bytes do report.xml; é o único
    # denominador disponível aqui (não há como saber quantos registros existem
    # sem ler o arquivo inteiro antes)
    progresso_xml: dict = {}
    # tipos de <model> que o parser não sabe interpretar, contados por tipo. Alguns são
    # deliberados (LogEntry, DictionaryWord...), outros são tipo novo de uma versão nova do
    # Physical Analyzer — e esses são dado do aparelho sendo perdido. Resumo no log ao final.
    tipos_desconhecidos: dict = {}
    for tipo, dados in iterar_registros(indice_binarios=indice_binarios,
                                        caminhos_zip=caminhos_zip,
                                        contagem_tamanhos=contagem_tamanhos, zf=zf,
                                        progresso=progresso_xml,
                                        desconhecidos=tipos_desconhecidos):
        if tipo == "arquivo":
            # iCloud: o Cellebrite extrai a mídia em files/Image com nome "achatado" (sem o JID
            # do WhatsApp) e o MessagesMetadata em files/Text — o passe de nuvem do iCloud
            # (ufdr_icloud) reindexa a mídia do .tar com o JID no caminho, então esses <file>
            # planos são pulados para não duplicar (e não mostrar mídia sem o contato de origem).
            if pular_arquivos_icloud and (
                (dados["caminho_zip"] or "").startswith("files/Image/")
                or (dados["caminho_zip"] or "").startswith("files/Text/")
            ):
                continue
            buffer_arquivos.append((
                id_extracao, dados["nome"][:2000], dados["caminho"], dados["categoria"],
                dados["mime"], dados["bytes"], dados["dt_modificacao"],
                dados["status"], dados["caminho_zip"],
                dados["hash_md5"], None, dados["hash_sha256"],  # SHA1 o UFDR não traz
                None, None,  # OCR: só a Nuvem IPED preenche
                dados["id_ufdr"],
                # transcrição, identificadores, extensão divergente e recuperado: vêm do
                # processamento que o IPED já fez, então só a Nuvem IPED preenche
                None, None, None, None,
            ))
            contagens["arquivos"] += 1
            if len(buffer_arquivos) >= 20000:
                _drenar_arquivos(cur, buffer_arquivos)
                buffer_arquivos.clear()
            processados += 1
            if processados % 200 == 0:
                conn.commit()
                bat.progresso(progresso_xml.get("lidos", 0), progresso_xml.get("total", 0))
                if bat.cancelar_pedido():
                    cancelado = True
                    break
            continue
        if tipo == "chat":
            fonte_chat, chat = dados
            if fonte_chat in (FONTE_WHATSAPP, FONTE_SMS):
                _coletar_fone_dono(chat, fone_dono, fone_metadado=fone_metadado)
            if fonte_chat == FONTE_WHATSAPP:
                reuse, escrever = _filtrar_duplicatas("wpp", chat, chats_vistos, chats_abertos)
                if escrever:
                    id_conv = _gravar_conversa(cur, "wpp", id_extracao, chat, mapa_fotos,
                                               jid_dono=jid_dono_wpp, id_conversa_existente=reuse)
                    chats_abertos[_chave_chat("wpp", chat)] = id_conv
                    if reuse is None:
                        contagens["conversas"] += 1
                    contagens["mensagens"] += len(chat["mensagens"])
                _coletar_alvos_localizacao(chat, "wpp", alvos_localizacao)
            elif fonte_chat == FONTE_SMS:
                reuse, escrever = _filtrar_duplicatas("sms", chat, chats_vistos, chats_abertos)
                if escrever:
                    id_conv = _gravar_conversa(cur, "sms", id_extracao, chat,
                                               jid_dono=jid_dono_sms, id_conversa_existente=reuse)
                    chats_abertos[_chave_chat("sms", chat)] = id_conv
                    if reuse is None:
                        contagens["sms_conversas"] += 1
                    contagens["sms_mensagens"] += len(chat["mensagens"])
                _coletar_alvos_localizacao(chat, "sms", alvos_localizacao)
            else:
                app_msg = fonte_chat or APP_DESCONHECIDO
                pref = f"msg:{app_msg}"
                reuse, escrever = _filtrar_duplicatas(pref, chat, chats_vistos, chats_abertos)
                if escrever:
                    id_conv = _gravar_conversa_msg(cur, id_extracao, app_msg, chat,
                                                   jid_dono=jid_dono_wpp,
                                                   id_conversa_existente=reuse)
                    chats_abertos[_chave_chat(pref, chat)] = id_conv
                    if reuse is None:
                        contagens["msg_conversas"] += 1
                    contagens["msg_mensagens"] += len(chat["mensagens"])
                _coletar_alvos_localizacao(chat, "msg", alvos_localizacao)
        elif tipo == "contato":
            _gravar_contato_agenda(cur, id_extracao, dados)
            contagens["contatos"] += 1
        elif tipo == "ligacao":
            _gravar_ligacao(cur, id_extracao, dados)
            contagens["ligacoes"] += 1
        elif tipo == "localizacao":
            # Nuvem Google (warrant return): as mesmas coordenadas vêm, mais ricas, no
            # Location History dos ZIPs aninhados (accuracy/deviceTag/platform). Para não
            # duplicar, o Location de Source='Google Warrant Return' do XML é descartado e o
            # dado entra pelo parser dos Archives (ver ufdr_nuvem.processar_nuvem_google).
            if pular_localizacoes_warrant and dados.get("aplicativo") == "Google Warrant Return":
                continue
            _gravar_localizacao(cur, id_extracao, dados)
            if dados.get("id_mensagem_ufdr"):
                coords_de_mensagem[dados["id_mensagem_ufdr"]] = (
                    dados["latitude"], dados["longitude"])
            contagens["localizacoes"] += 1
        elif tipo == "notificacao":
            _gravar_notificacao(cur, id_extracao, dados)
            contagens["notificacoes"] += 1
        elif tipo == "nota":
            _gravar_nota(cur, id_extracao, dados)
            contagens["notas"] += 1
        elif tipo == "navegacao":
            _gravar_pagina_visitada(cur, id_extracao, dados)
            contagens["navegacao"] += 1
        elif tipo == "aplicativo":
            _gravar_aplicativo(cur, id_extracao, dados)
            contagens["aplicativos"] += 1
        elif tipo == "dono":
            _gravar_dono(cur, id_extracao, dados)
            contagens["donos"] += 1
            tem_dono_user = True
        elif tipo == "conta_usuario":
            _gravar_conta_usuario(cur, id_extracao, dados)
            _coletar_conta_ios(dados, contas_ios)
        elif tipo == "email":
            _gravar_email(cur, id_extracao, dados)
            contagens["emails"] += 1
        elif tipo == "conta_financeira":
            _gravar_conta_financeira(cur, id_extracao, dados)
            contagens["contas_financeiras"] += 1
        elif tipo == "wifi":
            _gravar_rede_wifi(cur, id_extracao, dados)
            contagens["redes_wifi"] += 1
        elif tipo == "senha":
            _gravar_senha(cur, id_extracao, dados)
            contagens["senhas"] += 1
        elif tipo == "autofill":
            _gravar_autofill(cur, id_extracao, dados)
            contagens["autofill"] += 1
        elif tipo == "mensagem_solta":
            # mensagem sem Chat pai: acumula por aplicativo e é gravada ao fim,
            # numa conversa sintética por app (não dá para saber a conversa)
            fonte_msg, msg = dados
            mensagens_soltas.setdefault(fonte_msg or APP_DESCONHECIDO, []).append(msg)
        elif tipo == "acesso_conta":
            # sessão de login de conta na nuvem (RemoteServiceSession) — a MESMA tabela da
            # Nuvem Google/IPED (tela "Acessos"), populada direto do report.xml do Cellebrite
            _gravar_acesso_conta(cur, id_extracao, dados)
            contagens["acessos"] += 1
        elif tipo in _DOMINIOS_NOVOS:
            _gravar_dominio_novo(cur, tipo, id_extracao, dados)
            contagens[_CONTADOR_DOMINIO[tipo]] += 1

        processados += 1
        if processados % 200 == 0:
            conn.commit()
            bat.progresso(progresso_xml.get("lidos", 0), progresso_xml.get("total", 0))
            if bat.cancelar_pedido():
                cancelado = True
                break
    if buffer_arquivos:
        _drenar_arquivos(cur, buffer_arquivos)
        buffer_arquivos.clear()

    # Mensagens sem Chat pai, uma conversa sintética por aplicativo. Só agora,
    # porque o agrupamento precisa de todas elas.
    for fonte_msg, lista in mensagens_soltas.items():
        chat_sintetico = _chat_sinteticas(lista)
        if fonte_msg == FONTE_WHATSAPP:
            reuse, escrever = _filtrar_duplicatas("wpp", chat_sintetico, chats_vistos, chats_abertos)
            if escrever:
                id_conv = _gravar_conversa(cur, "wpp", id_extracao, chat_sintetico, mapa_fotos,
                                           jid_dono=jid_dono_wpp, id_conversa_existente=reuse)
                chats_abertos[_chave_chat("wpp", chat_sintetico)] = id_conv
                if reuse is None:
                    contagens["conversas"] += 1
                contagens["mensagens"] += len(chat_sintetico["mensagens"])
            _coletar_alvos_localizacao(chat_sintetico, "wpp", alvos_localizacao)
        elif fonte_msg == FONTE_SMS:
            reuse, escrever = _filtrar_duplicatas("sms", chat_sintetico, chats_vistos, chats_abertos)
            if escrever:
                id_conv = _gravar_conversa(cur, "sms", id_extracao, chat_sintetico,
                                           jid_dono=jid_dono_sms, id_conversa_existente=reuse)
                chats_abertos[_chave_chat("sms", chat_sintetico)] = id_conv
                if reuse is None:
                    contagens["sms_conversas"] += 1
                contagens["sms_mensagens"] += len(chat_sintetico["mensagens"])
            _coletar_alvos_localizacao(chat_sintetico, "sms", alvos_localizacao)
        else:
            pref = f"msg:{fonte_msg}"
            reuse, escrever = _filtrar_duplicatas(pref, chat_sintetico, chats_vistos, chats_abertos)
            if escrever:
                id_conv = _gravar_conversa_msg(cur, id_extracao, fonte_msg, chat_sintetico,
                                               jid_dono=jid_dono_wpp,
                                               id_conversa_existente=reuse)
                chats_abertos[_chave_chat(pref, chat_sintetico)] = id_conv
                if reuse is None:
                    contagens["msg_conversas"] += 1
                contagens["msg_mensagens"] += len(chat_sintetico["mensagens"])
            _coletar_alvos_localizacao(chat_sintetico, "msg", alvos_localizacao)

    # Anexo -> binário pelo file_id. Só dá para fechar agora: os <file> e os
    # <model> vêm intercalados no report.xml, então durante o passe o <file>
    # alvo pode ainda não ter sido lido.
    _resolver_anexos_por_file_id(cur, id_extracao)

    # Coordenada da localização compartilhada encaminhada -> bolha da conversa.
    # Também só agora: as Locations vêm depois dos Chats no report.xml.
    _resolver_localizacoes_compartilhadas(
        cur, id_extracao, alvos_localizacao, coords_de_mensagem)

    # Cartão de contato que o Cellebrite não copiou para o report.xml: devolvido do
    # ChatStorage.sqlite que o próprio .ufdr carrega (ver a função). Best-effort — o
    # artefato pode não existir (Android/nuvem) ou o sqlite não abrir; a extração segue.
    try:
        _resolver_contatos_chatstorage(
            cur, id_extracao, contatos_compartilhados_chatstorage(zf))
    except Exception:
        logger.exception("Falha ao resolver contatos compartilhados do ChatStorage "
                         "(extração %s, best-effort)", id_extracao)

    if not tem_dono_user:
        contagens["donos"] += _derivar_dono_ios(cur, id_extracao, contas_ios, fone_dono)

    _registrar_tipos_desconhecidos(id_extracao, tipos_desconhecidos)

    return cancelado


# Tipos de <model> que o parser ignora POR DECISÃO DE ESCOPO (ver docs/extração_ufdr.md): muito
# volume para o valor probatório. Ficam de fora do aviso — o aviso existe para o que ninguém
# decidiu ignorar.
_TIPOS_IGNORADOS_DE_PROPOSITO = frozenset({
    "LogEntry", "DictionaryWord", "DeviceEvent", "ActivitySensorData",
})


def _registrar_tipos_desconhecidos(id_extracao: int, tipos: dict) -> None:
    """Loga, ao fim do passe, o que o parser não soube interpretar — um resumo por tipo.

    Antes cada registro desses saía num log de nível debug, um por linha: em produção ninguém
    via, e um tipo novo (o Cellebrite cria tipos a cada versão do Physical Analyzer) era dado do
    aparelho descartado em silêncio. Os ignorados de propósito saem em INFO, para conferência; os
    demais em WARNING, que é o que vale investigar."""
    if not tipos:
        return
    conhecidos = {t: q for t, q in tipos.items() if t in _TIPOS_IGNORADOS_DE_PROPOSITO}
    novos = {t: q for t, q in tipos.items() if t not in _TIPOS_IGNORADOS_DE_PROPOSITO}

    def _resumo(d):
        return ", ".join(f"{t}={q}" for t, q in sorted(d.items(), key=lambda kv: -kv[1]))

    if conhecidos:
        logger.info("Extração %s: tipos ignorados por escopo — %s", id_extracao, _resumo(conhecidos))
    if novos:
        logger.warning(
            "Extração %s: %s registro(s) de %s tipo(s) de <model> SEM tratamento no parser — %s. "
            "Se algum for relevante, ele está sendo descartado; ver iterar_registros em ufdr_parser.py",
            id_extracao, sum(novos.values()), len(novos), _resumo(novos))


@contextlib.contextmanager
def _conn_da_pipeline(canal):
    """A conexão longa do laço — ou, no modo entrega, a falsificação que roteia commits
    para o canal (nenhum acesso ao banco acontece: os escritores estão guardados e o
    cursor falso grita se alguém esquecer um guard)."""
    if canal is not None:
        yield canal_extracao.ConnFalsa(canal), None
        return
    with get_connection(longa=True) as conn, conn.cursor() as cur:
        yield conn, cur


def _processar_extracao(id_extracao: int, caminho: Path, servidor: dict | None = None,
                        canal=None) -> None:
    """Roda em background: lê o .ufdr inteiro, grava tudo no banco e atualiza o status/contagens ao final.

    servidor (docs/no_descentralizado.md): quando não-nulo, o .ufdr mora na pasta data() de um nó
    descentralizado e `caminho` é o RELATIVO a essa pasta. Só três coisas mudam de lugar — o hash
    (calculado no nó, é a leitura inteira do arquivo), a abertura do ZIP (por faixas via HTTP) e o
    fim-de-fluxo (nada de mover o arquivo nem transcrever no central; o parse/indexação rodam
    aqui do mesmo jeito porque tudo que lê o pacote recebe o handle `zf`, não o caminho). Com
    servidor=None o comportamento é IDÊNTICO ao de sempre.

    canal (funcoes/canal_extracao.py): MODO ENTREGA — é assim que um NÓ processa para o central.
    O arquivo (`caminho`) é local ao processo, mas nada toca o banco: cada escrita de domínio
    vira um registro JSON entregue ao central (que reproduz com as mesmas funções), e cada
    estado (fase/progresso/contagens/finalizar) vira um callback. O emissor é ativado só
    dentro desta thread — o resto do processo segue normal."""
    _token_emissor = canal_extracao.ativar(canal) if canal is not None else None
    contagens = {
        "conversas": 0, "mensagens": 0,
        "sms_conversas": 0, "sms_mensagens": 0,
        "msg_conversas": 0, "msg_mensagens": 0,
        "contatos": 0, "ligacoes": 0, "localizacoes": 0, "notificacoes": 0, "notas": 0,
        "navegacao": 0, "aplicativos": 0, "donos": 0, "arquivos": 0,
        "emails": 0, "contas_financeiras": 0,
        "redes_wifi": 0,
        "senhas": 0, "autofill": 0,
        "audios_transcritos": 0,
        # produtos de nuvem (UFDR "Google Warrant Return"), contados no passe sobre files/Archives/
        "atividades": 0, "eventos": 0, "acessos": 0, "dispositivos": 0, "perfil": 0,
        "metadados_nuvem": 0,
        # domínios que passaram a ser importados (ver _DOMINIOS_NOVOS)
        **{chave: 0 for chave in _CONTADOR_DOMINIO.values()},
    }
    avisos: list[str] = []  # etapas puladas sem derrubar a extração (vão para TX_AVISO)
    bat = canal_extracao.BatimentoPonte(canal) if canal is not None else _Batimento(id_extracao)
    cancelado = False
    fonte_leitura = caminho  # Path local, ou a FonteRemota do nó no caminho remoto abaixo
    if servidor is not None:
        from funcoes import no_ponte  # tardio: só o caminho remoto puxa requests
        # `caminho` é o RELATIVO à pasta do nó; a fonte é o mesmo arquivo visto pela ponte.
        fonte_leitura = no_ponte.FonteRemota(servidor, caminho.as_posix())
    try:
        _marcar_inicio_extracao(id_extracao)
        # "este worker está vivo", independente de haver o que medir — ver iniciar_vigia
        bat.iniciar_vigia()

        # Passe de hash antes de abrir o ZIP: é a cópia do arquivo tal como chegou, antes de
        # qualquer leitura seletiva por zipfile — a referência de integridade para conferência
        # posterior (cadeia de custódia). Fase própria com porcentagem: é uma leitura completa
        # do .ufdr (dezenas de GB), e sem isso o badge fica parado em "Extraindo textos: 0%".
        # Sem conexão de banco: só o batimento toca o banco aqui, em conexões curtas próprias.
        # Pulo quando a linha já traz os hashes (requeue que preservou CO_HASH_* da tentativa
        # interrompida, ver _hashes_gravadas_extracao): o arquivo no disco é o mesmo que foi
        # hasheado, e reler dezenas de GB só atrasaria a retomada. O aviso vai para
        # TX_AVISO para a cadeia de custódia registrar que o hash é da passada anterior.
        hashes = _hashes_gravadas_extracao(id_extracao)
        if hashes:
            avisos.append("Etapa de hash pulada: reaproveitados os hashes da importação anterior "
                          "do mesmo arquivo (reprocessamento).")
        elif servidor is not None:
            # arquivo no nó: o hash é a leitura completa do .ufdr, e é exatamente ela que não
            # pode atravessar a WAN — quem hasheia é o próprio nó (POST /no/hash), o central só
            # recebe os digestos. O batimento segue batendo para o vigia saber que vive.
            # O nó aproveita o sidecar .hashes.json do upload dele mesmo (ver no_servico).
            bat.fase("hasheando")
            try:
                hashes = no_ponte.calcular_hashes(servidor, caminho.as_posix(),
                                                  algoritmos=_ALGORITMOS_HASH_UFDR)
            except FileNotFoundError:
                raise FileNotFoundError(
                    "O arquivo .ufdr não estava na pasta de dados do servidor da delegacia "
                    "quando chegou a vez desta extração na fila. Confirme o arquivo no nó e "
                    "rode de novo.")
            _gravar_hashes_extracao(id_extracao, hashes)
        else:
            # sidecar .hashes.json (escrito por salvar_ufdr_enviado ao gravar o upload, com os
            # digestos calculados DURANTE a gravação): o tamanho confere, então são os digestos
            # deste exato arquivo — a passada de hash não precisa ler os GB de novo.
            sidecar = ler_sidecar_hashes(caminho)
            if sidecar:
                hashes = sidecar
                avisos.append("Etapa de hash pulada: os digestos saíram da gravação do upload "
                              "(conferido pelo tamanho do arquivo), sem reler o .ufdr.")
            else:
                bat.fase("hasheando")
                hashes = calcular_hashes_arquivo(caminho, ao_progredir=bat.progresso,
                                                 algoritmos=_ALGORITMOS_HASH_UFDR)
            _gravar_hashes_extracao(id_extracao, hashes)
        _recusar_se_duplicada(id_extracao, hashes)

        # Abre o .ufdr UMA ÚNICA VEZ e reaproveita o mesmo ZipFile em todas as funções
        # abaixo — elimina 3 reaberturas desnecessárias do arquivo (que pode ter GBs).
        # ufdr_zip.abrir tolera o índice final perdido (cópia truncada): reconstrói o
        # catálogo pelos cabeçalhos locais e a extração segue com que sobreviveu.
        # No nó, `fonte_leitura` é uma FonteRemota e o mesmo abrir() produz o ZIP por faixas
        # (HTTP Range) sobre o ArquivoRemoto — o pacote de dezenas de GB nunca atravessa a
        # rede inteiro, só o diretório central + os membros lidos (sem tolerância a truncamento).
        with ufdr_zip.abrir(fonte_leitura) as zf:
            # O Cellebrite grava o report.xml no FIM do pacote (depois dos binários). Se o
            # truncamento chegou nele, não há o que importar: sem o report.xml não existem
            # conversas/contatos/ligações para gravar — dizer isso é melhor que a extração
            # morrer num KeyError solto no meio do passe.
            if getattr(zf, "recuperado", False) and "report.xml" not in zf.namelist():
                raise ValueError(
                    "O .ufdr está truncado e o report.xml — o catálogo de tudo o que o "
                    "Cellebrite extraiu, gravado no fim do pacote — caiu justamente na parte "
                    "perdida. A cópia não pode ser importada: recopie o arquivo a partir do "
                    "original na estação Cellebrite (grave o SHA256 dos dois lados para "
                    "conferir).")
            if getattr(zf, "recuperado", False):
                avisos.append(
                    f"Atenção: o índice final do .ufdr está perdido (cópia truncada). A importação "
                    f"foi parcial: {len(zf.namelist())} entradas recuperadas e cerca de "
                    f"{zf.bytes_descartados / 1e9:.1f} GB do fim do arquivo estavam vazios e foram "
                    "ignorados. Compare com o original na estação Cellebrite e recopie se faltar "
                    "material.")
            # Nuvem embutida no .ufdr: Google ("Google Warrant Return", files/Archives/*.zip) ou
            # Apple ("ICLOUD_BACKUP", zips -CONTACTS- / MessagesMetadata.txt — NUNCA o
            # Backup.plist, que também existe em dump de aparelho, ver tem_icloud). Ambas marcam a extração
            # como conta na nuvem — o card renderiza o bloco de nuvem, não o de aparelho. Import
            # tardio: evita ciclo (os módulos importam deste).
            from funcoes.ufdr_icloud import detectar_conta_icloud, processar_icloud, tem_icloud
            from funcoes.ufdr_nuvem import detectar_conta_google, processar_nuvem_google, tem_nuvem_google

            # O iCloud tem zips -CONTACTS- em files/Archives/ — sem o
            # "and not tem_icloud" o tem_nuvem_google (que só checa files/Archives/) daria True
            # para o iCloud e a conta sairia do nome do ZIP (errado). O iCloud é a fonte mais
            # específica, então é desambiguado primeiro.
            tem_nuvem_icloud = tem_icloud(zf)
            tem_nuvem_google = tem_nuvem_google(zf) and not tem_nuvem_icloud
            tem_nuvem = tem_nuvem_google or tem_nuvem_icloud
            conta = None
            if tem_nuvem_google:
                conta = detectar_conta_google(zf)
            elif tem_nuvem_icloud:
                conta = detectar_conta_icloud(fonte_leitura)

            info = ler_info_dispositivo(zf=zf)
            _gravar_info_dispositivo(id_extracao, info, in_nuvem=tem_nuvem, conta=conta)

            fone_metadado = info.get("telefone")
            jid_dono_wpp = f"{fone_metadado}@s.whatsapp.net" if fone_metadado else None
            jid_dono_sms = fone_metadado

            # Índices (anexos + fotos perfil) num ÚNICO passe pelo namelist do ZIP
            # (antes eram duas varreduras separadas do ZIP). É o trecho mais longo antes
            # de o report.xml começar a ser lido — em .ufdr de dezenas de GB leva minutos
            # —, então ganhou fase própria e porcentagem (o denominador é a contagem de
            # entradas do ZIP, exata). Também sem conexão.
            bat.fase("indexando")
            indice_binarios, mapa_fotos, caminhos_zip, contagem_tamanhos = indexar_indicadores(
                zf=zf, ao_progredir=bat.progresso)
            bat.fase("extraindo")

            # A ÚNICA conexão longa da extração, e só enquanto o laço está gravando.
            # No modo entrega (canal), a "conexão" é o próprio canal: commit fecha lote.
            with _conn_da_pipeline(canal) as (conn, cur):
                cancelado = _laco_report_xml(
                    cur, conn, bat, id_extracao, zf,
                    indice_binarios, caminhos_zip, mapa_fotos, contagem_tamanhos,
                    fone_metadado, jid_dono_wpp, jid_dono_sms, contagens,
                    pular_localizacoes_warrant=tem_nuvem_google,
                    pular_arquivos_icloud=tem_nuvem_icloud)
                # Nuvem embutida (ZIP/TAR aninhado) é lida por CAMINHO via ufdr_cache — que desde
                # o passaporte de nó aceita a FonteRemota no lugar do Path (as janelas de bytes
                # do zip aninhado viram faixas HTTP). `fonte_leitura` é o Path local ou a fonte
                # remota conforme o caso; o `zf` acima já é o handle aberto pela mesma fonte.
                if not cancelado and tem_nuvem_google:
                    cancelado = processar_nuvem_google(zf, fonte_leitura, cur, conn, bat, id_extracao,
                                                       contagens)
                if not cancelado and tem_nuvem_icloud:
                    cancelado = processar_icloud(zf, fonte_leitura, cur, conn, bat, id_extracao,
                                                 contagens)
                conn.commit()


        # Todos os dados já carregados: grava as contagens AGORA (o status segue 'processando'
        # até a transcrição terminar). Assim o card mostra tudo — inclusive os botões de
        # mensageiro — antes da etapa lenta de transcrição de áudio.
        bat.fase("contagens")
        _gravar_contagens_extracao(id_extracao, contagens)

        # Cancelamento pedido durante o laço principal: pula o resto (PIX, contas iOS,
        # transcrição, indexação) — os dados já gravados até aqui ficam preservados.
        if not cancelado:
            # Chaves PIX: liga cada chave mencionada nas conversas ao contato. Depende só das
            # mensagens (já gravadas), é rápido, então roda antes da transcrição. Import tardio para
            # evitar ciclo (pix_utils importa deste módulo). Best-effort: não derruba a extração e
            # deixa QT_CHAVES_PIX como 'já escaneado' para o card mostrar a contagem.
            try:
                from funcoes.pix_utils import detectar_chaves_pix
                detectar_chaves_pix(id_extracao)
            except Exception:
                logger.exception("Falha na detecção de chaves PIX da extração %s (best-effort)", id_extracao)

            # Contas bancárias de apps iOS (iCloud backup): extrai dados de SQLite databases
            # encontrados nos arquivos extraídos do aparelho. Best-effort: falhas são silenciosas.
            # Lê o sqlite por ufdr_zip.abrir — que resolve Path local e FonteRemota de nó.
            try:
                qt_app = _extrair_contas_de_apps_ios_extracao(id_extracao, fonte_leitura)
                if qt_app:
                    contagens["contas_financeiras"] += qt_app
                    _gravar_total_contas_financeiras(id_extracao, contagens["contas_financeiras"])
            except Exception:
                logger.exception("Falha ao extrair contas de apps iOS da extração %s (best-effort)", id_extracao)

            # Direção das mensagens do próprio dono: só dá para acertar agora, com todas as
            # identidades de dono já descobertas (ver corrigir_direcao_por_dono).
            try:
                corrigidas = _corrigir_direcao_dono_extracao(id_extracao)
                if corrigidas:
                    logger.info("Extração %s: %s mensagens do dono corrigidas para enviadas",
                                id_extracao, corrigidas)
            except Exception:
                logger.exception("Falha ao corrigir a direção das mensagens do dono da extração %s "
                                 "(best-effort)", id_extracao)

            # Identificação de pessoas: cruza contatos, agenda, ligações, chaves PIX, contas
            # bancárias e contas de aplicativo e materializa na Mesa quem foi identificado.
            # Roda no INQUÉRITO inteiro (não só nesta extração): o dono deste aparelho pode
            # ser contato salvo no outro celular do caso, e é junto que isso se resolve.
            # Depende de tudo acima (inclusive PIX e contas de apps iOS), é rápido e só mexe
            # no banco. Best-effort, como o resto: não derruba a extração.
            bat.fase("identificando_pessoas")
            try:
                from funcoes.identificacao_pessoas import (
                    IdentificacaoEmAndamento,
                    identificar_pessoas_inquerito,
                )
                id_inq = obter_id_inquerito_de_extracao(id_extracao)
                if id_inq is not None:
                    identificar_pessoas_inquerito(id_inq, incluir_extracao=id_extracao)
            except IdentificacaoEmAndamento:
                # Duas extrações do mesmo inquérito terminando juntas: a outra já está varrendo o
                # inquérito inteiro e vai enxergar esta também (a varredura é por inquérito, não
                # por extração). Não é falha — não polui o log com stack trace.
                logger.info("Identificação de pessoas da extração %s pulada: já havia uma em "
                            "andamento no inquérito", id_extracao)
            except Exception:
                logger.exception("Falha na identificação de pessoas do inquérito da extração %s (best-effort)", id_extracao)

            # Transcrição por ÚLTIMO (faster-whisper em CPU é a parte lenta): não bloqueia a
            # visibilidade de conversas/contatos/etc., que já estão gravados e navegáveis acima.
            # Continua checando o cancelamento a cada áudio (ver deve_cancelar), então um pedido
            # de parar que chegue durante essa fase também interrompe rápido.
            # "nenhuma": o usuário pediu a extração sem transcrição — pula a fase inteira, sem
            # carregar o modelo do faster-whisper nem tocar no badge.
            modelo_tx, incluir_video = _obter_config_transcricao(id_extracao)
            if modelo_tx and modelo_tx.strip().lower() == "nenhuma":
                logger.info("Extração %s: transcrição de áudio desabilitada (qualidade 'nenhuma')",
                            id_extracao)
            else:
                bat.fase("transcrevendo")
                # best-effort como a indexação: os dados já estão gravados, e modelo não baixado
                # (servidor sem internet) vira aviso na extração concluída, não falha dela
                try:
                    # com nó, o lote lê os áudios pela ponte via fonte_leitura — o modelo não
                    # viaja, só os MB de cada áudio (funcoes/transcricao.py reaproveita o ZIP
                    # aberto por processo de worker para não reler o diretório central por áudio)
                    contagens["audios_transcritos"] = _transcrever_audios_pendentes(
                        id_extracao, fonte_leitura, bat, modelo_tx, incluir_video
                    )
                except ModeloTranscricaoIndisponivel as exc:
                    logger.warning("Extração %s: %s", id_extracao, exc)
                    avisos.append(str(exc))
                except Exception as exc:
                    logger.exception("Falha na transcrição da extração %s (best-effort)", id_extracao)
                    avisos.append(f"Os áudios não foram transcritos: {exc}. Use o botão \"Transcrever\" "
                                  "da extração para tentar de novo.")

            # Indexação semântica (embeddings) como fase FINAL, se estiver configurada. Encadeada
            # inline para que a extração só conclua depois — best-effort: nunca derruba a extração
            # (config ausente é pulada; falha vira aviso; cancelamento também é checado dentro dela).
            aviso_indexacao = _indexar_semantica_ao_final(id_extracao, bat)
            if aviso_indexacao:
                avisos.append(aviso_indexacao)

        _finalizar_extracao(id_extracao, contagens["audios_transcritos"],
                            "\n".join(avisos)[:2000] if avisos else None)

        # com a extração concluída, o .ufdr sai da pasta de entrega da delegacia e vai para a
        # pasta do inquérito (data/<delegacia>/<inquérito>/CELULAR/) — depois daqui porque o
        # arquivo é lido do começo ao fim durante a extração e a transcrição. No remoto o arquivo
        # fica onde está (no nó) e o NO_ARQUIVO relativo continua valendo como chave lá.
        if servidor is None:
            _organizar_arquivo_extracao(id_extracao, caminho)
    except Exception as exc:  # armazena qualquer falha para exibir na tela, em vez de deixar o job travado
        if isinstance(exc, ExtracaoDuplicada):
            # não é falha de leitura: é recusa deliberada, e a mensagem já diz o que fazer
            mensagem = str(exc)
            logger.warning("Extração %s recusada por duplicidade: %s", id_extracao, mensagem)
        elif isinstance(exc, zipfile.BadZipFile):
            # "File is not a zip file" cru não ajuda ninguém: o caso típico é o .ufdr truncado
            # (cópia para data/ interrompida — o fim do ZIP, onde fica o índice, não existe)
            mensagem = (
                "O arquivo .ufdr está incompleto ou corrompido (o fim do arquivo ZIP não foi "
                "encontrado). Isso costuma ser cópia interrompida — confira se o tamanho em data/ "
                "é igual ao do original e copie novamente. Detalhe técnico: " + str(exc)
            )
            # ERROR de propósito: o motivo da falha precisa ir para o log (logs/erros.log), não
            # só para TX_ERRO — a linha some se a extração for apagada da tela, e o log fica.
            logger.error("Extração %s falhou: %s", id_extracao, mensagem)
        else:
            mensagem = str(exc)
            logger.exception("Extração %s falhou: %s", id_extracao, mensagem)
        try:
            if canal is not None:
                # nó: sem banco para gravar o erro — o central recebe por callback e a
                # entrega em andamento continua útil (lotes já confirmados não se perdem)
                canal.estado({"t": "erro", "mensagem": mensagem})
                canal.enviar_pendentes()
                return
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE investigacao.inq_tb02_extracoes "
                        "SET cd_status = 'erro', cd_fase = NULL, qt_progresso_fase = NULL, "
                        "tx_erro = %s, dt_fim_processamento = now() WHERE id_extracao = %s",
                        (mensagem[:2000], id_extracao),
                    )
                conn.commit()
        except Exception:
            # se nem isso funcionar, a extração fica presa em "processando" — visível no banco, mas
            # sem log a causa dessa falha dupla (exceção original + falha ao gravá-la) some de vez
            logger.exception("Falha ao gravar status de erro da extração %s", id_extracao)
    finally:
        # a thread de batimento não pode sobreviver ao job: uma extração já encerrada continuaria
        # se dizendo viva, e o vigia perderia o único sinal que tem
        bat.parar_vigia()
        if _token_emissor is not None:
            canal_extracao.desativar(_token_emissor)


# ChatType do UFDR -> o tipo que a tela usa. O valor do arquivo é mais confiável que contar
# participantes: lista de transmissão tem um participante só e vinha classificada como conversa
# individual (1.080 delas nos oito extratos auditados).
_TIPO_POR_CHATTYPE = {
    "OneOnOne": "individual",
    "Group": "grupo",
    "Broadcast": "transmissao",
}


def _tipo_de_conversa(chat: dict) -> str:
    """Tipo da conversa: pelo ChatType do UFDR quando ele existe, senão pelo domínio do JID e, em
    último caso, pela contagem de participantes (o ChatType "Unknown" é comum).

    A contagem DESCONTA o dono: um aparelho aparece com mais de uma identidade na lista de
    participantes (o chip e o contato "Eu"), então um 1:1 tinha 3 participantes e virava grupo."""
    tipo = _TIPO_POR_CHATTYPE.get(chat.get("tipo_chat") or "")
    if tipo:
        return tipo
    chat_id = (chat.get("chat_id") or "").lower()
    if chat_id.endswith("@g.us"):
        return "grupo"
    if chat_id.endswith("@s.whatsapp.net") or chat_id.endswith("@lid"):
        return "individual"
    outros = [p for p in chat["participantes"] if not p.get("dono_dispositivo")]
    return "grupo" if len(outros) > 2 else "individual"


# ---------------------------------------------------------------------------
# Filtro de chats duplicados: o report.xml repete o MESMO <Chat> várias vezes.
# A Cellebrite reprocessa o mesmo banco em passes diferentes (Secure Folder de
# Samsung, extração física multi-passe): na extração 37 a mesma conversa do
# WhatsApp (Secure Folder) apareceu 4x — no mensageiro genérico, sem UNIQUE de
# chat_id, viravam conversas novas na tela; no WhatsApp principal o UNIQUE
# fundia as conversas mas reinseria todas as mensagens (54% do total). O filtro
# abaixo guarda em memória o selo das mensagens já gravadas no passe: cópia
# idêntica do chat é pulada inteira, cópia com mensagens novas reaproveita a
# conversa existente. Só mensagem COM identificador original pode ser duplicata
# — e só quando data, corpo e remetente batem também: apps de saúde (Biome,
# KnowledgeC — extração 11) repetem o identificador com conteúdo DIFERENTE por
# design, e esses registros são legítimos.
def _chave_chat(prefixo: str, chat: dict) -> tuple:
    if chat.get("chat_id"):
        return (prefixo, chat["chat_id"])
    return (prefixo, None, (chat.get("nome_grupo") or "")[:200],
            tuple(sorted(p["jid"] for p in chat.get("participantes") or () if p.get("jid"))))


def _selo_mensagem(msg: dict) -> bytes:
    remetente = msg.get("remetente") or {}
    h = hashlib.blake2b(digest_size=16)
    for parte in (msg.get("identificador_original"), msg.get("timestamp"),
                  msg.get("corpo"), remetente.get("jid")):
        h.update(str(parte if parte is not None else "").encode("utf-8", "replace"))
        h.update(b"\x1f")
    return h.digest()


def _filtrar_duplicatas(prefixo: str, chat: dict, vistas: dict, conversas: dict):
    """Tira de chat["mensagens"] o que já foi gravado neste passe.

    Devolve (id_conversa existente ou None, escrever?): escrever é False quando o
    chat já foi aberto e a cópia atual não traz nenhuma mensagem nova."""
    chave = _chave_chat(prefixo, chat)
    vistos = vistas.setdefault(chave, set())
    novas = []
    for msg in chat["mensagens"]:
        if not msg.get("identificador_original"):
            novas.append(msg)
            continue
        selo = _selo_mensagem(msg)
        if selo in vistos:
            continue
        vistos.add(selo)
        novas.append(msg)
    chat["mensagens"] = novas
    reuse = conversas.get(chave)
    return reuse, bool(novas) or reuse is None


def _recortar_mapa_fotos(chat: dict, mapa_fotos: dict | None) -> dict:
    """Subconjunto do mapa telefone->foto que interessa a UM chat (participantes +
    remetentes). No modo entrega (canal) o mapa global — que pode ter dezenas de milhares
    de entradas — não pode ser serializado junto de cada conversa; só estes pedaços viajam."""
    if not mapa_fotos:
        return {}
    jids = [(p or {}).get("jid") for p in chat.get("participantes") or ()]
    # `or {}` e nao .get("remetente", {}): mensagem sem <model From> vem com a chave presente e
    # valor None (ufdr_parser._extrair_mensagem) — o default do .get nao cobre None.
    jids += [(m.get("remetente") or {}).get("jid") for m in chat.get("mensagens") or ()]
    recorte = {}
    for jid in jids:
        m = re.match(r"(\d{10,15})", jid or "")
        if m and m.group(1) in mapa_fotos:
            recorte[m.group(1)] = mapa_fotos[m.group(1)]
    return recorte


def _gravar_conversa(cur, prefixo: str, id_extracao: int, chat: dict,
                     mapa_fotos: dict[str, str] | None = None,
                     jid_dono: str | None = None,
                     id_conversa_existente: int | None = None) -> int:
    if canal_extracao.emitir(  # nó entrega ao central em vez de gravar (fotos só do chat)
            "conversa", (prefixo, id_extracao, chat,
                         _recortar_mapa_fotos(chat, mapa_fotos), jid_dono, None)):
        return 0
    """Grava uma conversa (participantes, mensagens, anexos) nas tabelas do prefixo 'wpp' ou 'sms'.
    mapa_fotos (telefone->caminho da foto no .ufdr) só é usado no WhatsApp.
    id_conversa_existente: reaproveita a conversa aberta neste passe para a cópia anterior do
    mesmo chat (filtro de duplicatas) em vez de abrir uma nova."""
    tabela_contatos = f"{prefixo}_tb01_contatos"
    tabela_conversas = f"{prefixo}_tb02_conversas"
    tabela_participantes = f"{prefixo}_tb03_participantes"
    tabela_mensagens = f"{prefixo}_tb04_mensagens"
    tabela_anexos = f"{prefixo}_tb05_anexos"

    tipo_conversa = _tipo_de_conversa(chat)

    if id_conversa_existente is not None:
        id_conversa = id_conversa_existente
    elif prefixo == "wpp" and chat.get("chat_id"):
        cur.execute(
            """
            INSERT INTO investigacao.wpp_tb02_conversas
                (id_extracao, nu_whatsapp_chat_id, no_grupo, tp_conversa, dt_inicio, dt_ultima_atividade,
                 tp_chat_ufdr, tx_descricao, no_conta, tx_status_chat, cd_status_ufdr)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id_extracao, nu_whatsapp_chat_id) DO UPDATE
                SET dt_inicio = LEAST(investigacao.wpp_tb02_conversas.dt_inicio, EXCLUDED.dt_inicio),
                    dt_ultima_atividade = GREATEST(investigacao.wpp_tb02_conversas.dt_ultima_atividade, EXCLUDED.dt_ultima_atividade),
                    no_grupo = COALESCE(investigacao.wpp_tb02_conversas.no_grupo, EXCLUDED.no_grupo)
            RETURNING id_conversa
            """,
             (id_extracao, chat["chat_id"], chat["nome_grupo"], tipo_conversa, chat["inicio"],
              chat["ultima_atividade"], chat.get("tipo_chat"), chat.get("descricao"),
              chat.get("conta"), chat.get("status_chat"), chat.get("estado")),
        )
        id_conversa = cur.fetchone()[0]
    else:
        # SMS não tem chat_id único (usa telefone) ou WPP sem chat_id
        cur.execute(
            f"""
            INSERT INTO investigacao.{tabela_conversas}
                (id_extracao, tp_conversa, dt_inicio, dt_ultima_atividade,
                 tp_chat_ufdr, tx_descricao, no_conta, tx_status_chat, cd_status_ufdr)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id_conversa
            """,
             (id_extracao, tipo_conversa, chat["inicio"], chat["ultima_atividade"],
              chat.get("tipo_chat"), chat.get("descricao"), chat.get("conta"),
              chat.get("status_chat"), chat.get("estado")),
        )
        id_conversa = cur.fetchone()[0]

    for participante in chat["participantes"]:
        id_contato = _upsert_contato(
            cur, tabela_contatos, id_extracao, participante["jid"], participante["nome"],
            caminho_foto=_foto_de_jid(participante["jid"], mapa_fotos) if prefixo == "wpp" else None,
            dono_dispositivo=participante.get("dono_dispositivo"),
        )
        if id_contato is None:
            continue
        if prefixo == "wpp":
            cur.execute(
                """
                INSERT INTO investigacao.wpp_tb03_participantes (id_conversa, id_contato, in_admin_grupo)
                VALUES (%s, %s, %s)
                ON CONFLICT (id_conversa, id_contato) DO NOTHING
                """,
                (id_conversa, id_contato, participante["admin_grupo"]),
            )
        else:
            cur.execute(
                f"""
                INSERT INTO investigacao.{tabela_participantes} (id_conversa, id_contato)
                VALUES (%s, %s)
                ON CONFLICT (id_conversa, id_contato) DO NOTHING
                """,
                (id_conversa, id_contato),
            )

    # Determina o JID do dono do dispositivo: prefere o telefone lido dos metadados
    # do UFDR (LastUsedMSISDN), que funciona em iPhones (onde IsPhoneOwner não existe);
    # fallback: procura IsPhoneOwner nos participantes da conversa.
    if not jid_dono:
        jid_dono = next(
            (p["jid"] for p in chat["participantes"] if p.get("dono_dispositivo") and p.get("jid")),
            None,
        )

    # Cache de contatos para evitar upserts repetidos (mesmo remetente em múltiplas msgs)
    cache_contatos: dict[str, int | None] = {}
    def _upsert_contato_cache(jid, nome, foto=None):
        if jid not in cache_contatos:
            cache_contatos[jid] = _upsert_contato(cur, tabela_contatos, id_extracao, jid, nome, caminho_foto=foto)
        return cache_contatos[jid]

    # Os remetentes são resolvidos ANTES, num passo só: o upsert de contato usa o mesmo cursor,
    # e ele não pode ficar intercalado com o lote de mensagens (ver _gravar_mensagens_em_lote).
    linhas: list[tuple[tuple, list]] = []
    for msg in chat["mensagens"]:
        remetente = msg["remetente"]
        id_remetente = None
        enviada = None
        if remetente and remetente["jid"]:
            id_remetente = _upsert_contato_cache(
                remetente["jid"], remetente["nome"],
                foto=_foto_de_jid(remetente["jid"], mapa_fotos) if prefixo == "wpp" else None,
            )
            enviada = bool(jid_dono and remetente["jid"] == jid_dono)
        elif jid_dono:
            enviada = True
        linhas.append((_linha_mensagem(id_conversa, id_remetente, enviada, msg), msg["anexos"]))

    _gravar_mensagens_em_lote(cur, tabela_mensagens, tabela_anexos, linhas)

    return id_conversa


# As três famílias (wpp/sms/msg) têm exatamente as mesmas colunas de mensagem e de anexo, então
# o SQL é montado uma vez e só a tabela muda.
_COLUNAS_MENSAGEM = (
    "id_conversa, id_contato_remetente, tx_mensagem, dt_mensagem, tp_mensagem, "
    "cd_status_mensagem, no_pasta, in_enviada, nu_identificador_original, "
    "vl_latitude, vl_longitude, cd_status_ufdr, in_encaminhada, "
    "in_apagada_remetente, in_temporaria, in_editada, tx_rotulos, "
    "nu_ident_citado, tx_destinatarios, tx_assunto, tx_motivo_exclusao, "
    "nu_smsc, dt_entregue, dt_lida, dt_reproduzida"
)
_COLUNAS_ANEXO = (
    "id_mensagem, no_arquivo, tx_titulo, tp_conteudo, qt_bytes, co_url_original, "
    "co_caminho_zip, co_file_id, tx_transcricao"
)

# Tamanho do lote de mensagens por ida ao banco. Alto o bastante para o ganho de round-trip
# aparecer, baixo o bastante para os resultados do RETURNING de um lote caberem folgados na
# memória mesmo num grupo de dezenas de milhares de mensagens.
_LOTE_MENSAGENS = 500


def _linha_mensagem(id_conversa: int, id_remetente: int | None, enviada: bool | None,
                    msg: dict) -> tuple:
    """Tupla de uma mensagem na ordem de _COLUNAS_MENSAGEM."""
    return (
        id_conversa, id_remetente, msg["corpo"], msg["timestamp"], msg["tipo"],
        msg["status"], msg["pasta"], enviada, msg["identificador_original"],
        msg["latitude"], msg["longitude"], msg["estado"], msg["encaminhada"],
        msg["apagada_remetente"], msg["temporaria"], msg["editada"], msg["rotulos"],
        msg["identificador_citado"], msg["destinatarios"], msg["assunto"],
        msg["motivo_exclusao"], msg["smsc"], msg["entregue"], msg["lida"],
        msg["reproduzida"],
    )


def _gravar_mensagens_em_lote(cur, tabela_mensagens: str, tabela_anexos: str,
                              mensagens: list[tuple[tuple, list]]) -> None:
    """Grava mensagens e anexos em lote. `mensagens` é [(tupla_da_mensagem, anexos), ...].

    Era um INSERT ... RETURNING por mensagem e outro por anexo, cada um com a sua ida e volta ao
    Postgres. Num aparelho de 300 mil mensagens isso é 300 mil round-trips só de mensagem — com
    o banco em outro contêiner, o passe do report.xml passava a maior parte do tempo esperando a
    rede, não lendo XML nem gravando. Os <file> já tinham sido resolvidos assim (COPY em lote);
    as mensagens ficaram para trás porque precisam do ID de volta para pendurar os anexos.

    `executemany(..., returning=True)` do psycopg 3 resolve isso sem abrir mão da segurança: ele
    enfileira o lote em pipeline e devolve UM conjunto de resultados POR ENTRADA, na ordem das
    entradas. O id sai casado com a sua mensagem por construção — diferente de um INSERT
    multivalorado com RETURNING, em que a ordem das linhas devolvidas é hábito do Postgres e não
    promessa, e um dia trocaria o anexo de dono em silêncio. Aqui, anexo trocado é foto de uma
    pessoa aparecendo na conversa de outra."""
    if not mensagens:
        return
    sql_msg = (f"INSERT INTO investigacao.{tabela_mensagens} ({_COLUNAS_MENSAGEM}) "
               f"VALUES ({', '.join(['%s'] * 25)}) RETURNING id_mensagem")
    sql_anexo = (f"INSERT INTO investigacao.{tabela_anexos} ({_COLUNAS_ANEXO}) "
                 f"VALUES ({', '.join(['%s'] * 9)})")

    for inicio in range(0, len(mensagens), _LOTE_MENSAGENS):
        lote = mensagens[inicio:inicio + _LOTE_MENSAGENS]
        cur.executemany(sql_msg, [linha for linha, _anexos in lote], returning=True)

        ids: list[int] = []
        while True:
            linha = cur.fetchone()
            if linha is not None:
                ids.append(linha[0])
            if not cur.nextset():
                break
        if len(ids) != len(lote):
            # nunca deve acontecer; se acontecer, parar é o certo — seguir penduraria anexo na
            # mensagem errada, que é pior do que a extração falhar com erro visível
            raise RuntimeError(
                f"{tabela_mensagens}: o banco devolveu {len(ids)} id(s) para {len(lote)} "
                f"mensagem(ns) — anexos não podem ser associados com segurança.")

        linhas_anexo = [
            (id_msg, anexo["nome_arquivo"], anexo["titulo"], anexo["tipo_conteudo"],
             anexo["bytes"], anexo["url"], anexo["caminho_zip"], anexo["file_id"],
             anexo["transcricao"])
            for id_msg, (_linha, anexos) in zip(ids, lote)
            for anexo in anexos
        ]
        if linhas_anexo:
            cur.executemany(sql_anexo, linhas_anexo)


def _gravar_anexo(cur, tabela_anexos: str, id_mensagem: int, anexo: dict) -> None:
    """Grava um anexo de mensagem. As três tabelas (wpp/sms/msg) têm as mesmas colunas desde
    que SMS e mensageiros ganharam CO_URL_ORIGINAL — antes só o WhatsApp guardava a URL.

    TX_TRANSCRICAO nasce preenchida quando o próprio Cellebrite já transcreveu o áudio; a fase
    de transcrição local pula esses anexos (ver _transcrever_audios_pendentes)."""
    cur.execute(
        f"""
        INSERT INTO investigacao.{tabela_anexos}
            (id_mensagem, no_arquivo, tx_titulo, tp_conteudo, qt_bytes, co_url_original,
             co_caminho_zip, co_file_id, tx_transcricao)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (id_mensagem, anexo["nome_arquivo"], anexo["titulo"], anexo["tipo_conteudo"],
         anexo["bytes"], anexo["url"], anexo["caminho_zip"], anexo["file_id"],
         anexo["transcricao"]),
    )


def _upsert_contato_msg(cur, id_extracao: int, app: str, ident: str, nome: str | None,
                        dono_dispositivo: bool | None = None) -> int | None:
    """Insere/atualiza um contato de mensageiro pela chave natural (extração + app + identificador)."""
    if not ident:
        return None
    cur.execute(
        """
        INSERT INTO investigacao.msg_tb01_contatos (id_extracao, tp_app, nu_identificador, no_contato, in_dono_dispositivo)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (id_extracao, tp_app, nu_identificador) DO UPDATE
            SET no_contato = COALESCE(EXCLUDED.no_contato, investigacao.msg_tb01_contatos.no_contato),
                in_dono_dispositivo = COALESCE(EXCLUDED.in_dono_dispositivo, investigacao.msg_tb01_contatos.in_dono_dispositivo)
        RETURNING id_contato
        """,
        (id_extracao, app, ident, nome, dono_dispositivo),
    )
    return cur.fetchone()[0]


def _gravar_conversa_msg(cur, id_extracao: int, app: str, chat: dict,
                          jid_dono: str | None = None,
                          id_conversa_existente: int | None = None) -> int:
    if canal_extracao.emitir("conversa_msg", (id_extracao, app, chat, jid_dono, None)):
        return 0  # nó entrega ao central em vez de gravar
    """Grava uma conversa de mensageiro (Instagram/Telegram/…) nas tabelas genéricas MSG_TB0x,
    com tp_app = app. Espelha _gravar_conversa (WhatsApp), sem a especialização de campos do WPP.
    id_conversa_existente: reaproveita a conversa da cópia anterior do mesmo chat no passe."""
    tipo_conversa = _tipo_de_conversa(chat)
    if id_conversa_existente is not None:
        id_conversa = id_conversa_existente
    else:
        cur.execute(
            """
            INSERT INTO investigacao.msg_tb02_conversas
                (id_extracao, tp_app, nu_chat_id, no_grupo, tp_conversa, dt_inicio, dt_ultima_atividade,
                 tp_chat_ufdr, tx_descricao, no_conta, tx_status_chat, cd_status_ufdr)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id_conversa
            """,
            (id_extracao, app, chat["chat_id"], chat["nome_grupo"], tipo_conversa, chat["inicio"],
             chat["ultima_atividade"], chat.get("tipo_chat"), chat.get("descricao"),
             chat.get("conta"), chat.get("status_chat"), chat.get("estado")),
        )
        id_conversa = cur.fetchone()[0]

    for participante in chat["participantes"]:
        id_contato = _upsert_contato_msg(
            cur, id_extracao, app, participante["jid"], participante["nome"],
            dono_dispositivo=participante.get("dono_dispositivo"),
        )
        if id_contato is None:
            continue
        cur.execute(
            """
            INSERT INTO investigacao.msg_tb03_participantes (id_conversa, id_contato)
            VALUES (%s, %s) ON CONFLICT (id_conversa, id_contato) DO NOTHING
            """,
            (id_conversa, id_contato),
        )

    # Determina o JID do dono do dispositivo: prefere o telefone lido dos metadados
    # do UFDR (LastUsedMSISDN); fallback para IsPhoneOwner nos participantes.
    if not jid_dono:
        jid_dono = next(
            (p["jid"] for p in chat["participantes"] if p.get("dono_dispositivo") and p.get("jid")),
            None,
        )

    cache_contatos_msg: dict[str, int | None] = {}
    def _upsert_contato_msg_cache(ident, nome):
        if ident not in cache_contatos_msg:
            cache_contatos_msg[ident] = _upsert_contato_msg(cur, id_extracao, app, ident, nome)
        return cache_contatos_msg[ident]

    linhas: list[tuple[tuple, list]] = []
    for msg in chat["mensagens"]:
        remetente = msg["remetente"]
        id_remetente = None
        enviada = None
        if remetente and remetente["jid"]:
            id_remetente = _upsert_contato_msg_cache(remetente["jid"], remetente["nome"])
            enviada = bool(jid_dono and remetente["jid"] == jid_dono)
        elif jid_dono:
            enviada = True
        linhas.append((_linha_mensagem(id_conversa, id_remetente, enviada, msg), msg["anexos"]))

    _gravar_mensagens_em_lote(cur, "msg_tb04_mensagens", "msg_tb05_anexos", linhas)

    return id_conversa


# Nome da conversa sintética onde entram as mensagens que o UFDR traz sem Chat pai. Elas
# existem (755 nos quatro iPhones auditados) e eram descartadas em silêncio; sem um Chat
# não há participante nem título, então ficam agrupadas por aplicativo.
CHAT_SEM_CONVERSA = "(mensagens sem conversa)"


def _chat_sinteticas(mensagens: list[dict]) -> dict:
    """Monta um "chat" no formato que _gravar_conversa espera, para as mensagens soltas."""
    return {
        "chat_id": CHAT_SEM_CONVERSA,
        "nome_grupo": CHAT_SEM_CONVERSA,
        "inicio": min((m["timestamp"] for m in mensagens if m["timestamp"]), default=None),
        "ultima_atividade": max((m["timestamp"] for m in mensagens if m["timestamp"]), default=None),
        "participantes": [],
        "mensagens": mensagens,
        "tipo_chat": None,
        "descricao": None,
        "conta": None,
        "status_chat": None,
        "estado": None,
    }


def _obter_config_transcricao(id_extracao: int) -> tuple[str | None, bool]:
    _canal = canal_extracao.emissor_ativo()
    if _canal is not None:
        # nó: a config veio no corpo do POST /no/extrair (não há banco aqui)
        return _canal.config_transcricao or (None, False)
    """Lê o modelo de transcrição escolhido para esta extração (pode vir do .env se não foi
    definido por extração) e se os vídeos também devem ser transcritos."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT tx_modelo_transcricao, in_transcrever_video "
                "FROM investigacao.inq_tb02_extracoes WHERE id_extracao = %s",
                (id_extracao,),
            )
            row = cur.fetchone()
    if not row:
        return None, False
    return (row[0] or None), bool(row[1])


# Nome das tabelas de anexos/conversas por prefixo de mensageiro — o mesmo padrão é usado na
# transcrição e na listagem de pendentes (e em várias outras consultas do módulo).
_TABELA_ANEXO = {"wpp": "wpp_tb05_anexos", "sms": "sms_tb05_anexos", "msg": "msg_tb05_anexos"}


def _listar_anexos_audio_pendentes(id_extracao: int,
                                   incluir_video: bool = False) -> list[tuple[str, int, str]]:
    """Lista os anexos de áudio (ou vídeo, com incluir_video) desta extração que ainda não foram
    transcritos e cujo binário foi localizado no .ufdr. Devolve (prefixo_tabela, id_anexo,
    co_caminho_zip) — o mesmo conjunto que _transcrever_audios_pendentes processa, numa forma
    reutilizável para contagem e para o lote de transcrição em segundo plano.

    Anexo que já chegou transcrito do Cellebrite (campo Transcript do UFDR) é pulado: refazer
    no faster-whisper custaria a fase mais lenta da extração para chegar ao mesmo lugar.
    Anexo da Nuvem IPED (ponteiro "IPED::...") também fica de fora: o binário está no storage
    do caso, não no .ufdr, e o leitor da transcrição local não o alcança — e quando o IPED já
    transcreveu, a importação trouxe o texto pronto (TX_TRANSCRICAO).
    Um binário cujo caminho já foi transcrito em OUTRA extração (reimportação do mesmo
    aparelho repete o caminho dentro do .ufdr) também não volta para a fila: o trabalho já
    foi feito, e numa extração de 110 mil áudios cada repetição economizada é hora de CPU."""
    # o filtro de tipo é o mesmo nas três tabelas; vídeo só entra quando o usuário pediu
    filtro_tipo = "(a.tp_conteudo LIKE 'audio/%%' OR a.tp_conteudo LIKE 'video/%%')" if incluir_video \
        else "a.tp_conteudo LIKE 'audio/%%'"
    pendentes: list[tuple[str, int, str]] = []
    with get_connection() as conn:
        with conn.cursor() as cur:
            for prefixo, tabela_anexo in _TABELA_ANEXO.items():
                tabela_mensagem = tabela_anexo.replace("_tb05_anexos", "_tb04_mensagens")
                tabela_conversa = tabela_anexo.replace("_tb05_anexos", "_tb02_conversas")
                cur.execute(
                    f"""
                    SELECT a.id_anexo, a.co_caminho_zip
                    FROM investigacao.{tabela_anexo} a
                    JOIN investigacao.{tabela_mensagem} m ON m.id_mensagem = a.id_mensagem
                    JOIN investigacao.{tabela_conversa} c ON c.id_conversa = m.id_conversa
                    WHERE c.id_extracao = %s AND a.co_caminho_zip IS NOT NULL AND {filtro_tipo}
                      AND a.tx_transcricao IS NULL
                      AND a.co_caminho_zip NOT LIKE 'IPED::%%'
                      AND NOT EXISTS (
                          SELECT 1 FROM investigacao.{tabela_anexo} t
                          WHERE t.co_caminho_zip = a.co_caminho_zip
                            AND t.tx_transcricao IS NOT NULL)
                    """,
                    (id_extracao,),
                )
                pendentes += [(prefixo, id_anexo, caminho_zip) for id_anexo, caminho_zip in cur.fetchall()]
    return pendentes


def _transcrever_audios_pendentes(id_extracao: int, caminho, bat: "_Batimento",
                                   modelo: str | None = None,
                                   incluir_video: bool = False) -> int:
    """Busca os anexos de áudio desta extração com o binário localizado (WhatsApp + SMS + demais
    mensageiros, num só pool de processos), transcreve e grava o resultado; devolve quantos deram
    certo. modelo: opcional — nome do modelo faster-whisper; se omitido, usa o padrão do .env.
    incluir_video: se True, os anexos de vídeo entram junto — o faster-whisper lê a trilha de
    áudio do container; vídeo mudo simplesmente não rende transcrição.
    caminho: Path local OU FonteRemota (fonte_ufdr) — o pool abre o .ufdr dos dois jeitos
    (funcoes/transcricao.py); o modelo nunca muda de host."""
    # "nenhuma" (qualidade escolhida na tela): transcrição desabilitada — nem entra no pool
    if modelo and modelo.strip().lower() == "nenhuma":
        return 0
    canal = canal_extracao.emissor_ativo()
    if canal is not None:
        return _transcrever_audios_pendentes_no(canal, id_extracao, caminho, bat,
                                                modelo, incluir_video)
    pendentes = _listar_anexos_audio_pendentes(id_extracao, incluir_video)
    if not pendentes:
        return 0
    # O MESMO binário (mesmo caminho dentro do .ufdr) mora em vários anexos — áudio reencaminhado
    # para dezenas de conversas. Transcreve-se uma vez por binário e o texto é espalhado por
    # todos os anexos dele; foi assim que a fila de 110 mil chegou ao tamanho real do trabalho.
    por_binario: dict[tuple[str, str], list[int]] = {}
    for prefixo, id_anexo, caminho_zip in pendentes:
        por_binario.setdefault((prefixo, caminho_zip), []).append(id_anexo)
    tarefas = [(f"{prefixo}|{caminho_zip}", caminho_zip) for prefixo, caminho_zip in por_binario]

    # NENHUMA conexão de banco enquanto o pool do faster-whisper roda: é a fase mais longa da
    # extração (horas, em CPU), e era ela que segurava a conexão do pool. A porcentagem e o
    # cancelamento vão pelo batimento, que abre a sua conexão curta a cada toque — e isso também
    # desfaz a armadilha de três threads (esta, a de resultados do mp.Pool e o vigia) commitarem
    # na mesma conexão.
    resultado = transcrever_lote(
        caminho, tarefas, modelo,
        deve_cancelar=bat.cancelar_pedido,
        ao_progredir=bat.progresso,
    )

    with get_connection() as conn:
        with conn.cursor() as cur:
            for chave, texto in resultado.items():
                prefixo_tabela, caminho_zip = chave.split("|", 1)
                ids = por_binario.get((prefixo_tabela, caminho_zip)) or []
                if not ids:
                    continue
                # um UPDATE só por binário: o texto vai para TODOS os anexos que apontam para ele
                cur.execute(
                    f"UPDATE investigacao.{_TABELA_ANEXO[prefixo_tabela]}"
                    " SET tx_transcricao = %s WHERE id_anexo = ANY(%s) AND tx_transcricao IS NULL",
                    (texto, ids),
                )
        conn.commit()

    return len(resultado)


def _transcrever_audios_pendentes_no(canal, id_extracao: int, caminho, bat,
                                     modelo: str | None, incluir_video: bool) -> int:
    """Fase 3 do nó-extrai: o áudio JÁ está no disco do nó, então o whisper roda AQUI (na
    CPU da estação) e só o TEXTO volta ao central, como registro do canal. A lista de
    pendentes vem do próprio canal (os anexos que ele acabou de emitir) — no nó não há
    SELECT de anexos; e o central aplica o texto por co_caminho_zip (chave natural)."""
    pendentes = [p for p in canal.audios_pendentes
                 if p["tipo"].startswith("audio")
                 or (incluir_video and p["tipo"].startswith("video"))]
    vistos: set[str] = set()
    tarefas = []
    for p in pendentes:
        if p["caminho_zip"] in vistos:
            continue  # o mesmo binário pode aparecer em mais de um anexo: transcreve uma vez
        vistos.add(p["caminho_zip"])
        tarefas.append((f'{p["prefixo"]}|{p["caminho_zip"]}', p["caminho_zip"]))
    if not tarefas:
        return 0
    resultado = transcrever_lote(
        caminho, tarefas, modelo,
        deve_cancelar=bat.cancelar_pedido,
        ao_progredir=bat.progresso,
    )
    for chave, texto in resultado.items():
        prefixo, caminho_zip = chave.split("|", 1)
        canal.emitir("transcricao", (prefixo, id_extracao, caminho_zip, texto))
    return len(resultado)


def _aplicar_transcricao(cur, prefixo: str, id_extracao: int, caminho_zip: str,
                         texto: str | None) -> None:
    """Replay central do registro 'transcricao' vindo do nó: grava o texto nos anexos desta
    extração cujo binário é o mesmo caminho dentro do .ufdr (todos os anexos que apontam
    para ele, como faz o UPDATE por id_anexo no caminho local)."""
    tabela_anexo = _TABELA_ANEXO[prefixo]
    tabela_mensagem = tabela_anexo.replace("_tb05_anexos", "_tb04_mensagens")
    tabela_conversa = tabela_anexo.replace("_tb05_anexos", "_tb02_conversas")
    cur.execute(
        f"""
        UPDATE investigacao.{tabela_anexo} a SET tx_transcricao = %s
        FROM investigacao.{tabela_mensagem} m, investigacao.{tabela_conversa} c
        WHERE a.id_mensagem = m.id_mensagem AND m.id_conversa = c.id_conversa
          AND c.id_extracao = %s AND a.co_caminho_zip = %s AND a.tx_transcricao IS NULL
        """,
        (texto, id_extracao, caminho_zip),
    )


def iniciar_transcricao_extracao(id_extracao: int, modelo: str = "small") -> dict:
    """Dispara, em segundo plano, a transcrição dos áudios ainda não transcritos de uma extração
    (o botão "Transcrever" do modal "todas as extrações"). Usa sempre o modelo passado — padrão
    "small", a qualidade média — e respeita o IN_TRANSCREVER_VIDEO que o usuário marcou na
    extração original.

    Devolve {"ok": True, "pendentes": n} se a transcrição foi iniciada, ou
    {"ok": False, "mensagem": ...} se não havia o que transcrever. Levanta ValueError quando a
    extração ainda está em andamento ou o .ufdr não está mais na pasta de dados (a rota converte
    em 409/400 com mensagem legível)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT cd_status, no_arquivo, in_transcrever_video "
                "FROM investigacao.inq_tb02_extracoes WHERE id_extracao = %s",
                (id_extracao,),
            )
            row = cur.fetchone()
        if row is None:
            raise ValueError("Extração não encontrada.")
        cd_status, no_arquivo, incluir_video = row
        if cd_status in ("pendente", "processando"):
            raise ValueError("A extração ainda está em andamento — aguarde terminar para transcrever.")

    caminho = fonte_ufdr(no_arquivo)
    if caminho is None:
        raise ValueError("O arquivo .ufdr desta extração não está mais acessível (nem no central, "
                         "nem no servidor da delegacia) — restaure o arquivo antes de transcrever.")

    pendentes = _listar_anexos_audio_pendentes(id_extracao, bool(incluir_video))
    if not pendentes:
        return {"ok": False, "mensagem": "Não há áudios pendentes de transcrição nesta extração."}

    # Transição atômica para 'processando': dois cliques (ou duas abas) não podem subir dois pools
    # de transcrição sobre o mesmo conjunto — o segundo UPDATE não devolve linha e a rota responde
    # 409. Deixa também o estado visível na hora para o badge e o polling da tela.
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE investigacao.inq_tb02_extracoes
                   SET cd_status = 'processando', cd_fase = 'transcrevendo', qt_progresso_fase = 0,
                       dt_ultima_atividade = now(), fl_cancelar = false, tx_erro = NULL
                   WHERE id_extracao = %s AND cd_status NOT IN ('pendente', 'processando')
                   RETURNING id_extracao""",
                (id_extracao,),
            )
            ok = cur.fetchone() is not None
        conn.commit()

    if not ok:
        raise ValueError("A extração ainda está em andamento — aguarde terminar para transcrever.")

    thread = threading.Thread(
        target=_processar_transcricao_extracao,
        args=(id_extracao, caminho, modelo, bool(incluir_video)),
        daemon=True,
    )
    thread.start()
    n = len(pendentes)
    return {"ok": True, "pendentes": n,
            "mensagem": f"Transcrição iniciada de {n} áudio(s) pendente(s) — acompanhe o progresso no status da extração."}


def _processar_transcricao_extracao(id_extracao: int, caminho: Path, modelo: str,
                                    incluir_video: bool) -> None:
    """Worker em segundo plano do botão "Transcrever": roda o mesmo lote de transcrição da
    extração (_transcrever_audios_pendentes), alimentando fase/progresso para o badge, e devolve
    a extração ao estado concluído ao final. O status 'processando' e a fase 'transcrevendo' já
    foram marcados por iniciar_transcricao_extracao antes de este worker subir; a contagem de
    transcritos é acumulada em QT_AUDIOS_TRANSCRITOS (o total da extração, não só desta rodada)."""
    try:
        # mesma razão de _processar_extracao: o lote do faster-whisper leva horas e não pode
        # segurar conexão do pool nesse tempo todo — o batimento abre a sua a cada toque
        aviso = None
        try:
            qt = _transcrever_audios_pendentes(id_extracao, caminho, _Batimento(id_extracao),
                                               modelo, incluir_video)
        except ModeloTranscricaoIndisponivel as exc:
            # a extração em si continua íntegra: volta para concluída, com o aviso no card
            logger.warning("Extração %s: %s", id_extracao, exc)
            qt, aviso = 0, str(exc)[:2000]
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE investigacao.inq_tb02_extracoes
                       SET cd_status = 'concluido', cd_fase = NULL, qt_progresso_fase = NULL,
                           dt_fim_processamento = now(), tx_aviso = %s,
                           qt_audios_transcritos = coalesce(qt_audios_transcritos, 0) + %s
                       WHERE id_extracao = %s""",
                    (aviso, qt, id_extracao),
                )
            conn.commit()
    except Exception:
        logger.exception("Falha na transcrição em segundo plano da extração %s", id_extracao)
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE investigacao.inq_tb02_extracoes "
                        "SET cd_status = 'erro', cd_fase = NULL, qt_progresso_fase = NULL, "
                        "tx_erro = %s, dt_fim_processamento = now() WHERE id_extracao = %s",
                        ("Falha ao transcrever os áudios pendentes.", id_extracao),
                    )
                conn.commit()
        except Exception:
            logger.exception("Falha ao gravar status de erro da transcrição da extração %s", id_extracao)


def _gravar_contato_agenda(cur, id_extracao: int, contato: dict) -> None:
    if canal_extracao.emitir("contato_agenda", (id_extracao, contato)):  # nó entrega ao central em vez de gravar
        return
    """Grava um registro de agenda (Contact) e seus telefones/e-mails (Entries)."""
    cur.execute(
        """
        INSERT INTO investigacao.ctt_tb01_contatos
            (id_extracao, no_contato, no_aplicativo_origem, no_grupo, tp_contato,
             dt_criacao, dt_modificacao, tx_notas, tx_endereco, no_organizacao, no_conta,
             tx_nascimento, tx_info_adicional, co_caminho_foto, cd_status_ufdr)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id_contato
        """,
        (id_extracao, contato["nome"], contato["aplicativo_origem"], contato["grupo"],
         contato["tipo"], contato["criacao"], contato["modificacao"], contato["notas"],
         contato["endereco"], contato["organizacao"], contato["conta"], contato["nascimento"],
         contato["info_adicional"], contato["caminho_foto"], contato["estado"]),
    )
    id_contato = cur.fetchone()[0]
    for entrada in contato["entradas"]:
        cur.execute(
            """
            INSERT INTO investigacao.ctt_tb02_contato_entradas
                (id_contato, tp_entrada, no_categoria, tx_valor, no_dominio)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (id_contato, entrada["tipo"], entrada["categoria"], entrada["valor"],
             entrada["dominio"]),
        )


def _gravar_ligacao(cur, id_extracao: int, ligacao: dict) -> None:
    if canal_extracao.emitir("ligacao", (id_extracao, ligacao)):  # nó entrega ao central em vez de gravar
        return
    """Grava um registro de ligação (Call)."""
    cur.execute(
        """
        INSERT INTO investigacao.lig_tb01_ligacoes
            (id_extracao, no_aplicativo, tp_direcao, cd_status, dt_ligacao,
             qt_duracao_segundos, in_videochamada, nu_telefone_contato, no_contato,
             tp_desconexao, no_conta, cd_status_ufdr)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            id_extracao, ligacao["aplicativo"], ligacao["direcao"], ligacao["status"], ligacao["timestamp"],
            ligacao["duracao_segundos"], ligacao["videochamada"], ligacao["telefone"], ligacao["nome_contato"],
            ligacao["desconexao"], ligacao["conta"], ligacao["estado"],
        ),
    )


def _gravar_localizacao(cur, id_extracao: int, loc: dict) -> None:
    if canal_extracao.emitir("localizacao", (id_extracao, loc)):  # nó entrega ao central em vez de gravar
        return
    """Grava um registro de localização (Location)."""
    cur.execute(
        """
        INSERT INTO investigacao.loc_tb01_localizacoes
            (id_extracao, no_aplicativo, dt_localizacao, vl_latitude, vl_longitude, tx_endereco,
             no_categoria, tx_descricao, vl_precisao, vl_elevacao, tp_localizacao, no_origem,
             dt_fim, no_conta, cd_status_ufdr)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            id_extracao, loc["aplicativo"], loc["timestamp"], loc["latitude"], loc["longitude"],
            loc["endereco"], loc["categoria"], loc["descricao"], loc["precisao"], loc["elevacao"],
            loc["tipo"], loc["origem"], loc["fim"], loc["conta"], loc["estado"],
        ),
    )


def _gravar_notificacao(cur, id_extracao: int, notif: dict) -> None:
    if canal_extracao.emitir("notificacao", (id_extracao, notif)):  # nó entrega ao central em vez de gravar
        return
    """Grava um registro de notificação (Notification)."""
    cur.execute(
        """
        INSERT INTO investigacao.ntf_tb01_notificacoes
            (id_extracao, no_aplicativo_log, no_pacote_app, tx_conteudo, dt_notificacao,
             no_assunto, tp_notificacao, cd_status, cd_status_ufdr)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (id_extracao, notif["aplicativo_log"], notif["pacote_app"], notif["conteudo"],
         notif["timestamp"], notif["assunto"], notif["tipo"], notif["status"], notif["estado"]),
    )


def _gravar_nota(cur, id_extracao: int, nota: dict) -> None:
    if canal_extracao.emitir("nota", (id_extracao, nota)):  # nó entrega ao central em vez de gravar
        return
    """Grava uma nota de bloco de notas (Note: Samsung Notes, Apple Notes/iCloud Backup, Google Keep…)."""
    cur.execute(
        """
        INSERT INTO investigacao.not_tb01_notas
            (id_extracao, no_aplicativo, tx_titulo, tx_corpo, dt_criacao, dt_modificacao,
             cd_status, tx_anexos, tx_resumo, no_pasta, no_conta)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            id_extracao, nota["aplicativo"], nota["titulo"], nota["corpo"],
            nota["criacao"], nota["modificacao"], nota["status"],
            ", ".join(nota["anexos"]) if nota["anexos"] else None,
            nota["resumo"], nota["pasta"], nota["conta"],
        ),
    )


def _gravar_pagina_visitada(cur, id_extracao: int, pagina: dict) -> None:
    if canal_extracao.emitir("pagina_visitada", (id_extracao, pagina)):  # nó entrega ao central em vez de gravar
        return
    """Grava um registro de página visitada (VisitedPage)."""
    cur.execute(
        """
        INSERT INTO investigacao.nav_tb01_paginas_visitadas
            (id_extracao, no_aplicativo, tx_titulo, tx_url, dt_visita, qt_visitas, tx_conta,
             cd_status_ufdr)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            id_extracao, pagina["aplicativo"], pagina["titulo"], pagina["url"],
            pagina["timestamp"], pagina["qt_visitas"], pagina["conta"], pagina["estado"],
        ),
    )


def _gravar_aplicativo(cur, id_extracao: int, app: dict) -> None:
    if canal_extracao.emitir("aplicativo", (id_extracao, app)):  # nó entrega ao central em vez de gravar
        return
    """Grava um registro de aplicativo instalado (InstalledApplication)."""
    cur.execute(
        """
        INSERT INTO investigacao.app_tb01_aplicativos
            (id_extracao, nu_pacote, no_aplicativo, nu_versao, tx_descricao, dt_instalacao,
             dt_ultimo_uso, tx_categorias, tx_permissoes, tx_diretorios, cd_status_ufdr)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            id_extracao, app["pacote"], app["nome"], app["versao"],
            app["descricao"], app["instalacao"], app["ultimo_uso"],
            app["categorias"], app["permissoes"], app["diretorios"], app["estado"],
        ),
    )


# Postgres recusa NUL (0x00) em coluna TEXT e o encoder UTF-8 recusa surrogate solto. O IPED
# pode entregar caminho/nome com byte nulo vindo de metadado de filesystem corrompido, e um único
# caractere desses derrubava o COPY inteiro de arq_tb01_arquivos (a importação falhava com
# "invalid byte sequence for encoding UTF8: 0x00" e pedia para apagar o registro). Mesmo
# tratamento de documentos_utils._limpar_texto_para_banco.
_RE_CTRL_INVALIDO = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _limpar_str_para_banco(valor: str) -> str:
    return _RE_CTRL_INVALIDO.sub("", valor.encode("utf-8", "replace").decode("utf-8"))


def _limpar_json_para_banco(obj):
    """Mesma limpeza de _limpar_str_para_banco, aplicada recursivamente dentro de um Jsonb."""
    if isinstance(obj, str):
        return _limpar_str_para_banco(obj)
    if isinstance(obj, list):
        return [_limpar_json_para_banco(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _limpar_json_para_banco(v) for k, v in obj.items()}
    return obj


def _limpar_texto_para_banco(valor):
    """Remove NUL e demais caracteres de controle de uma str; os demais tipos (int, bool, None,
    datetime) passam intocados. Jsonb não é subclass de str, então recebe tratamento próprio: o
    psycopg serializa a string interna com "\u0000", que o Postgres recusa ao converter para text
    ("unsupported Unicode escape sequence ... \u0000 cannot be converted to text") — foi o que
    derrubava o COPY de arq_tb01_arquivos num QR code binário da Nuvem IPED."""
    if isinstance(valor, str):
        return _limpar_str_para_banco(valor)
    if isinstance(valor, Jsonb):
        return Jsonb(_limpar_json_para_banco(valor.obj))
    return valor


def _drenar_arquivos(cur, buffer: list[tuple]) -> None:
    if canal_extracao.emitir("arquivos", (buffer,)):  # nó entrega o COPY como lote de linhas
        return
    """Insere um lote de arquivos do aparelho (<file>) via COPY — muito mais rápido que INSERT por
    linha, essencial porque um iPhone tem centenas de milhares deles. Cada tupla segue a ordem das
    colunas do COPY abaixo (ver _processar_extracao, onde o buffer é montado).

    As colunas de OCR nascem com a extração Nuvem IPED — que já recebe texto/OCR pronto do IPED —
    e no UFDR seguem vazias. As de hash, não: o Cellebrite grava MD5 e SHA256 de praticamente
    todo arquivo (1.700.636 de 1.703.877 nos oito extratos auditados) e isso ficava sendo
    descartado; agora vem do próprio <file> (SHA1 é o único que o UFDR não traz).

    Da transcrição em diante vale a mesma regra do OCR: são colunas que saem do processamento
    que o IPED já fez (transcrição de áudio, identificadores, extensão divergente, item
    recuperado, linha do tempo, ids do item no caso, triagem de nudez/rosto/hashset e os
    marcadores do perito) e ficam NULL no UFDR — ver iped_utils._item_para_arquivo.

    Por isso a tupla pode vir CURTA: o UFDR monta as 19 primeiras colunas e para por aí, e a
    Nuvem IPED monta a lista inteira. Completar aqui com None é o que permite as duas origens
    usarem o mesmo COPY sem o UFDR ter de carregar uma dúzia de None a cada arquivo (são
    centenas de milhares por aparelho)."""
    if not buffer:
        return
    colunas = (
        "id_extracao, no_arquivo, tx_caminho, no_categoria, tp_mime, qt_bytes, "
        "dt_modificacao, cd_status_arquivo, co_caminho_zip, "
        "co_hash_md5, co_hash_sha1, co_hash_sha256, tx_ocr, dt_ocr, nu_id_ufdr, "
        "tx_transcricao, tx_identificadores, in_extensao_divergente, in_recuperado, "
        "dt_criacao, dt_acesso, tx_linha_tempo, co_track_id, nu_id_origem, nu_id_pai, "
        "co_evidencia, vl_nudez, tx_classe_nudez, qt_faces, tx_alerta_hash, "
        "tx_marcadores, in_selecionado"
    )
    total = colunas.count(",") + 1
    copy_sql = f"COPY investigacao.arq_tb01_arquivos ({colunas}) FROM STDIN"
    with cur.copy(copy_sql) as cp:
        for linha in buffer:
            if len(linha) < total:
                linha = tuple(linha) + (None,) * (total - len(linha))
            cp.write_row(tuple(_limpar_texto_para_banco(v) for v in linha))


def _resolver_anexos_por_file_id(cur, id_extracao: int) -> int:
    if canal_extracao.emitir("resolver_anexos", (id_extracao,)):
        return 0  # o UPDATE casa por chave natural (file_id) — a central reproduz no replay
    """Liga cada anexo ao binário do ZIP pelo atributo file_id do UFDR.

    O parser já tenta duas vias durante a leitura: o caminho que o Cellebrite gravou no anexo
    (attachment_extracted_path) e o casamento por tamanho exato quando ele é inequívoco. As duas
    juntas achavam 69% dos anexos com binário nos oito extratos auditados. O file_id aponta
    direto para o <file id=…>, cujo "Local Path" é o caminho no ZIP, e sobe esse número para 94%.

    Fica aqui, e não no parser, porque os <file> e os <model> vêm intercalados no report.xml:
    durante o passe o <file> alvo pode ainda não ter sido lido. Como os métodos são
    complementares (em alguns iPhones o antigo acerta mais que o file_id, em outros o contrário),
    só preenche o que ficou sem caminho — nunca sobrescreve.
    Devolve quantos anexos foram resolvidos."""
    total = 0
    for tabela, conversas, mensagens in (
        ("wpp_tb05_anexos", "wpp_tb02_conversas", "wpp_tb04_mensagens"),
        ("sms_tb05_anexos", "sms_tb02_conversas", "sms_tb04_mensagens"),
        ("msg_tb05_anexos", "msg_tb02_conversas", "msg_tb04_mensagens"),
    ):
        cur.execute(
            f"""
            UPDATE investigacao.{tabela} a
               SET co_caminho_zip = f.co_caminho_zip
              FROM investigacao.arq_tb01_arquivos f,
                   investigacao.{mensagens} m,
                   investigacao.{conversas} c
             WHERE a.id_mensagem = m.id_mensagem
               AND m.id_conversa = c.id_conversa
               AND c.id_extracao = %s
               AND a.co_caminho_zip IS NULL
               AND a.co_file_id IS NOT NULL
               AND f.id_extracao = c.id_extracao
               AND f.nu_id_ufdr = a.co_file_id
               AND f.co_caminho_zip IS NOT NULL
            """,
            (id_extracao,),
        )
        total += cur.rowcount or 0
        # CORRIGE o anexo que ficou com o binário ERRADO. O casamento por tamanho do parser é
        # um palpite: quando ele aponta para um arquivo e o file_id — a referência explícita do
        # próprio UFDR — aponta para outro CUJO NOME É O DO ANEXO, quem está certo é o file_id.
        # Foi o que pôs a foto de uma pessoa no lugar da miniatura do mapa numa mensagem de
        # localização (2.830 anexos nas oito extrações). A condição é estreita de propósito: sem
        # o nome batendo nada é sobrescrito, porque divergência entre o Filename e o caminho
        # extraído é comum e legítima (172 mil casos) e ali o palpite costuma estar certo.
        cur.execute(
            f"""
            UPDATE investigacao.{tabela} a
               SET co_caminho_zip = f.co_caminho_zip
              FROM investigacao.arq_tb01_arquivos f,
                   investigacao.{mensagens} m,
                   investigacao.{conversas} c
             WHERE a.id_mensagem = m.id_mensagem
               AND m.id_conversa = c.id_conversa
               AND c.id_extracao = %s
               AND a.co_file_id IS NOT NULL
               AND f.id_extracao = c.id_extracao
               AND f.nu_id_ufdr = a.co_file_id
               AND f.co_caminho_zip IS NOT NULL
               AND a.co_caminho_zip IS NOT NULL
               AND a.co_caminho_zip <> f.co_caminho_zip
               AND a.no_arquivo IS NOT NULL
               AND f.no_arquivo = a.no_arquivo
            """,
            (id_extracao,),
        )
        total += cur.rowcount or 0
    return total


def _coletar_alvos_localizacao(chat: dict, familia: str, alvos: dict) -> None:
    """Acumula, por id de <model> do UFDR, a mensagem de localização compartilhada que ficou
    SEM coordenada — para _resolver_localizacoes_compartilhadas devolvê-la depois.

    A mensagem é anotada pelo identificador original (o campo Identifier do WhatsApp), e não
    pelo id do banco: durante o passe as mensagens entram em lote, sem RETURNING, e esse
    identificador é a chave que já está gravada na linha."""
    for msg in chat["mensagens"]:
        ident = msg.get("identificador_original")
        if not ident:
            continue
        for id_modelo in msg.get("ids_alvo_localizacao") or ():
            alvos[id_modelo] = (familia, ident)


def _resolver_localizacoes_compartilhadas(cur, id_extracao: int, alvos: dict, coordenadas: dict) -> int:
    if canal_extracao.emitir("resolver_localizacoes", (id_extracao, alvos, coordenadas)):
        return 0  # casa por nu_identificador_original (chave natural) — reproduzível no central
    """Devolve à bolha da conversa a coordenada de uma localização compartilhada ENCAMINHADA.

    Nesse caso o Cellebrite não repete a coordenada dentro da mensagem: ela fica num
    <model type="Location"> de topo que aponta de volta para o <model> da mensagem embutida
    (ver ufdr_parser._ids_alvo_localizacao). Como as Locations vêm DEPOIS dos Chats no
    report.xml, o cruzamento só pode ser fechado agora, ao fim do passe — mesma razão de
    _resolver_anexos_por_file_id.

    `alvos` mapeia id do <model> -> (família, identificador original da mensagem) e
    `coordenadas` id do <model> -> (latitude, longitude). Só preenche lacuna: mensagem que já
    tem coordenada não é tocada. Devolve quantas mensagens foram completadas."""
    por_familia: dict[str, list[tuple]] = {}
    for id_modelo, (familia, ident) in alvos.items():
        coord = coordenadas.get(id_modelo)
        if coord and coord[0] is not None and coord[1] is not None:
            por_familia.setdefault(familia, []).append((coord[0], coord[1], id_extracao, ident))
    total = 0
    for familia, linhas in por_familia.items():
        t = _TABELAS_CONVERSA[familia]
        cur.executemany(
            f"""
            UPDATE investigacao.{t['mensagens']} m
               SET vl_latitude = %s, vl_longitude = %s
              FROM investigacao.{t['conversas']} c
             WHERE c.id_conversa = m.id_conversa
               AND c.id_extracao = %s
               AND m.nu_identificador_original = %s
               AND m.vl_latitude IS NULL AND m.vl_longitude IS NULL
            """,
            linhas,
        )
        total += len(linhas)
    return total


def _resolver_contatos_chatstorage(cur, id_extracao: int, mapa: dict) -> int:
    if canal_extracao.emitir("resolver_contatos_chatstorage", (id_extracao, mapa)):
        return 0  # casa por nu_identificador_original (chave natural) — reproduzível no central
    """Devolve à bolha do WhatsApp o cartão de contato que o Cellebrite não copiou para
    o report.xml.

    O cartão mandado pelo WhatsApp Web mora na mídia da mensagem no ChatStorage.sqlite
    (ZVCARDNAME/ZVCARDSTRING) e no report.xml a mensagem chega com SharedContacts vazio
    — sem isto a bolha mostra "(sem conteúdo)" enquanto o Reader e o IPED mostram o
    cartão (ver ufdr_parser.contatos_compartilhados_chatstorage).

    `mapa` é {ZSTANZAID -> texto} e o ZSTANZAID é exatamente o Identifier do WhatsApp, a
    chave que já está gravada em NU_IDENTIFICADOR_ORIGINAL — mesma chave natural dos
    outros resolvers. Só preenche lacuna: mensagem com texto não é tocada. O cartão é
    texto como o dos SharedContacts do XML, então cai na identificação de pessoas que
    roda adiante como qualquer cartão. Devolve quantas mensagens foram completadas."""
    if not mapa:
        return 0
    # NU_IDENTIFICADOR_ORIGINAL não tem índice: um UPDATE só, com o mapa inteiro (45 mil
    # cartões num iPhone auditado), vira varredura longa e morre no statement_timeout.
    # Lotes de 2.000 mantêm cada statement curto e o total idempotente do mesmo jeito.
    chaves = list(mapa)
    total = 0
    for i in range(0, len(chaves), 2000):
        lote = chaves[i:i + 2000]
        cur.execute(
            """
            UPDATE investigacao.wpp_tb04_mensagens m
               SET tx_mensagem = v.texto
              FROM (SELECT * FROM unnest(%s::text[], %s::text[]) AS x(ident, texto)) v,
                   investigacao.wpp_tb02_conversas c
             WHERE c.id_conversa = m.id_conversa
               AND c.id_extracao = %s
               AND m.nu_identificador_original = v.ident
               AND m.tx_mensagem IS NULL
            """,
            (lote, [mapa[k] for k in lote], id_extracao),
        )
        total += cur.rowcount or 0
    return total


def _gravar_dono(cur, id_extracao: int, dono: dict) -> None:
    if canal_extracao.emitir("dono", (id_extracao, dono)):  # nó entrega ao central em vez de gravar
        return
    """Grava um registro de dono/usuário do aparelho (User)."""
    cur.execute(
        """
        INSERT INTO investigacao.don_tb01_donos
            (id_extracao, no_dono, tp_usuario, nu_identificador, dt_ultimo_login)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (id_extracao, dono["nome"], dono["tipo_usuario"], dono["identificador"], dono["ultimo_login"]),
    )


def _gravar_email(cur, id_extracao: int, email: dict) -> None:
    if canal_extracao.emitir("email", (id_extracao, email)):  # nó entrega ao central em vez de gravar
        return
    """NU_THREAD/NU_MENSAGEM só o IPED preenche (X-GM-THRID e Message-ID dos cabeçalhos do
    Gmail); o UFDR não expõe nenhum dos dois, por isso entram com .get() e não pela chave
    obrigatória — extração de celular continua chamando esta função como sempre chamou."""
    cur.execute(
        """INSERT INTO investigacao.eml_tb01_emails
           (id_extracao, no_fonte, no_remetente, tx_destinatarios, tx_cc, tx_cco, tx_assunto,
            tx_corpo, dt_email, tp_modelo, no_pasta, cd_status_email, no_conta, tx_rotulos,
            tx_anexos, cd_status_ufdr, nu_thread, nu_mensagem)
           VALUES (%(id_extracao)s, %(fonte)s, %(remetente)s, %(destinatarios)s, %(cc)s, %(cco)s,
                   %(assunto)s, %(corpo)s, %(data)s, %(tipo)s, %(pasta)s, %(status)s, %(conta)s,
                   %(rotulos)s, %(anexos)s, %(estado)s, %(thread)s, %(mensagem)s)""",
        {**email, "id_extracao": id_extracao,
         "thread": email.get("thread"), "mensagem": email.get("mensagem")},
    )


def _gravar_conta_usuario(cur, id_extracao: int, conta: dict) -> None:
    if canal_extracao.emitir("conta_usuario", (id_extracao, conta)):  # nó entrega ao central em vez de gravar
        return
    """Preserva toda conta UserAccount do UFDR; a conta Apple segue sendo usada também
    para inferir o dono de iPhones, mas as demais ficam disponíveis no modal."""
    cur.execute(
        """INSERT INTO investigacao.cta_tb01_contas_usuario
           (id_extracao, no_fonte, no_servico, no_usuario, no_titular, dt_criacao, cd_status_ufdr)
           VALUES (%(id_extracao)s, %(fonte)s, %(servico)s, %(usuario)s, %(nome)s, %(criado_em)s,
                   %(estado)s)""",
        {**conta, "id_extracao": id_extracao},
    )


def _gravar_conta_financeira(cur, id_extracao: int, conta: dict) -> None:
    if canal_extracao.emitir("conta_financeira", (id_extracao, conta)):  # nó entrega ao central em vez de gravar
        return
    cur.execute(
        """INSERT INTO investigacao.bnc_tb01_contas_financeiras
           (id_extracao, no_fonte, no_instituicao, no_titular, nu_conta, nu_agencia, tp_conta,
            no_moeda, vl_saldo, tp_modelo, cd_status_ufdr)
           VALUES (%(id_extracao)s, %(fonte)s, %(instituicao)s, %(titular)s, %(numero_conta)s,
                   %(agencia)s, %(tipo_conta)s, %(moeda)s, %(saldo)s, %(tipo_modelo)s, %(estado)s)""",
        {**conta, "id_extracao": id_extracao},
    )


def _gravar_senha(cur, id_extracao: int, senha: dict) -> None:
    if canal_extracao.emitir("senha", (id_extracao, senha)):  # nó entrega ao central em vez de gravar
        return
    cur.execute(
        """INSERT INTO investigacao.snh_tb01_senhas
           (id_extracao, no_fonte, no_servico, no_usuario, tx_dados, tp_modelo,
            tp_senha, no_grupo_acesso, cd_status_ufdr)
           VALUES (%(id_extracao)s, %(fonte)s, %(servico)s, %(usuario)s, %(dados)s,
                   %(tipo_modelo)s, %(tipo_senha)s, %(grupo_acesso)s, %(estado)s)""",
        {**senha, "id_extracao": id_extracao},
    )


def _gravar_autofill(cur, id_extracao: int, autofill: dict) -> None:
    if canal_extracao.emitir("autofill", (id_extracao, autofill)):  # nó entrega ao central em vez de gravar
        return
    cur.execute(
        """INSERT INTO investigacao.afl_tb01_autofill
           (id_extracao, no_fonte, no_chave, tx_valor, tp_modelo, dt_registro, dt_ultimo_uso,
            cd_status_ufdr)
           VALUES (%(id_extracao)s, %(fonte)s, %(chave)s, %(valor)s, %(tipo_modelo)s,
                   %(registro)s, %(ultimo_uso)s, %(estado)s)""",
        {**autofill, "id_extracao": id_extracao},
    )


def _gravar_rede_wifi(cur, id_extracao: int, rede: dict) -> None:
    if canal_extracao.emitir("rede_wifi", (id_extracao, rede)):  # nó entrega ao central em vez de gravar
        return
    cur.execute(
        """INSERT INTO investigacao.wifi_tb01_redes
           (id_extracao, no_ssid, no_bssid, tp_seguranca, co_senha, qt_conexoes,
            dt_primeira_conexao, dt_ultima_conexao, nr_canal, nr_sinal,
            in_rede_oculta, tp_autenticacao, no_fabricante, in_favorita, no_fonte,
            vl_latitude, vl_longitude, tp_conexao, cd_status_ufdr)
           VALUES (%(id_extracao)s, %(ssid)s, %(bssid)s, %(seguranca)s, %(senha)s, %(qt_conexoes)s,
                   %(primeira_conexao)s, %(ultima_conexao)s, %(canal)s, %(sinal)s,
                   %(rede_oculta)s, %(autenticacao)s, %(fabricante)s, %(favorita)s, %(fonte)s,
                   %(latitude)s, %(longitude)s, %(tipo_conexao)s, %(estado)s)""",
        {**rede, "id_extracao": id_extracao},
    )


# =====================================================================
# Domínios do UFDR que passaram a ser importados (ver db/init/17_aparelho_redes.sql).
#
# Cada um é uma inserção de uma linha por registro, sem filhos e sem chave natural, então em
# vez de 21 funções iguais fica um mapa tipo -> (tabela, colunas). Cada par é
# (coluna no banco, chave no dict que o parser devolve); a ordem não importa.
# =====================================================================

_DOMINIOS_NOVOS: dict[str, tuple[str, tuple[tuple[str, str], ...]]] = {
    "uso_app": ("uso_tb01_uso_apps", (
        ("no_aplicativo", "aplicativo"), ("nu_pacote", "pacote"), ("tp_acao", "acao"),
        ("dt_inicio", "inicio"), ("dt_fim", "fim"), ("no_submodulo", "submodulo"),
        ("no_servico", "servico"),
    )),
    "dispositivo_reconhecido": ("rcd_tb01_dispositivos", (
        ("no_aplicativo", "aplicativo"), ("no_dispositivo", "nome"),
        ("tp_dispositivo", "tipo_dispositivo"), ("no_modelo", "modelo"), ("nu_serie", "serie"),
        ("no_mac", "mac"), ("tx_identificadores", "identificadores"),
    )),
    "conexao_dispositivo": ("cnx_tb01_conexoes", (
        ("no_aplicativo", "aplicativo"), ("tp_metodo", "metodo"), ("tp_natureza", "natureza"),
        ("tp_dispositivo", "tipo_dispositivo"), ("no_dispositivo", "nome_dispositivo"),
        ("no_mac", "mac"), ("dt_conexao", "timestamp"),
    )),
    "cookie": ("coo_tb01_cookies", (
        ("no_aplicativo", "aplicativo"), ("no_cookie", "nome"), ("no_dominio", "dominio"),
        ("tx_caminho", "caminho"), ("tx_valor", "valor"),
        ("no_app_relacionado", "app_relacionado"), ("dt_criacao", "criacao"),
        ("dt_ultimo_acesso", "ultimo_acesso"), ("dt_expiracao", "expiracao"),
    )),
    "atividade_social": ("rds_tb01_atividade_social", (
        ("no_aplicativo", "aplicativo"), ("no_plataforma", "plataforma"),
        ("tp_atividade", "tipo_atividade"), ("tx_titulo", "titulo"), ("tx_corpo", "corpo"),
        ("tx_url", "url"), ("dt_atividade", "timestamp"), ("no_autor", "autor_nome"),
        ("nu_autor", "autor_id"), ("no_conta", "conta"), ("cd_situacao", "situacao"),
        ("tp_canal", "canal"), ("tx_privacidade", "privacidade"), ("qt_reacoes", "reacoes"),
        ("qt_compartilhamentos", "compartilhamentos"), ("qt_comentarios", "comentarios"),
        ("vl_latitude", "latitude"), ("vl_longitude", "longitude"), ("tx_marcados", "marcados"),
        ("tx_anexos", "anexos"), ("nu_post_original", "post_original"),
        ("in_do_log_atividade", "do_log_atividade"),
    )),
    "busca": ("bsc_tb01_buscas", (
        ("no_aplicativo", "aplicativo"), ("no_origem", "origem"), ("tx_termo", "termo"),
        ("dt_busca", "timestamp"), ("no_conta", "conta"), ("tx_resultados", "resultados"),
        ("vl_latitude", "latitude"), ("vl_longitude", "longitude"),
    )),
    "envio_nuvem": ("upl_tb01_envios_nuvem", (
        ("no_aplicativo", "aplicativo"), ("no_arquivo", "nome_arquivo"), ("tx_titulo", "titulo"),
        ("tx_url", "url"), ("tp_arquivo", "tipo_arquivo"), ("no_pasta", "pasta"),
        ("no_conta", "conta"), ("no_dono", "dono"),
        ("tx_compartilhamento", "compartilhamento"), ("dt_envio", "dt_envio"),
        ("dt_modificacao", "dt_modificacao"),
    )),
    "compromisso": ("cal_tb01_calendario", (
        ("no_aplicativo", "aplicativo"), ("no_categoria", "categoria"), ("tx_assunto", "assunto"),
        ("tx_detalhes", "detalhes"), ("tx_local", "local"), ("dt_inicio", "inicio"),
        ("dt_fim", "fim"), ("tx_convidados", "convidados"), ("no_organizador", "organizador"),
        ("tx_repeticao", "repeticao"), ("tx_lembretes", "lembretes"), ("cd_situacao", "situacao"),
        ("tx_prioridade", "prioridade"), ("tx_disponibilidade", "disponibilidade"),
        ("no_conta", "conta"), ("vl_latitude", "latitude"), ("vl_longitude", "longitude"),
    )),
    "uso_aplicativo": ("apu_tb01_uso_aplicativo", (
        ("no_aplicativo", "aplicativo"), ("nu_pacote", "pacote"), ("qt_aberturas", "aberturas"),
        ("qt_ativacoes", "ativacoes"), ("qt_tempo_ativo", "tempo_ativo"),
        ("qt_tempo_fundo", "tempo_fundo"), ("qt_ultima_duracao", "ultima_duracao"),
        ("dt_inicio", "inicio"), ("dt_fim", "fim"), ("dt_ultima_abertura", "ultima_abertura"),
    )),
    "trajeto": ("jrn_tb01_trajetos", (
        ("no_aplicativo", "aplicativo"), ("tp_trajeto", "tipo"), ("no_trajeto", "nome"),
        ("vl_lat_origem", "lat_origem"), ("vl_lon_origem", "lon_origem"),
        ("tx_endereco_origem", "endereco_origem"), ("vl_lat_destino", "lat_destino"),
        ("vl_lon_destino", "lon_destino"), ("tx_endereco_destino", "endereco_destino"),
        ("qt_pontos", "qt_pontos"), ("dt_inicio", "inicio"), ("dt_fim", "fim"),
        ("no_conta", "conta"),
    )),
    "info_aparelho": ("dev_tb01_info_aparelho", (
        ("no_aplicativo", "aplicativo"), ("no_categoria", "categoria"), ("no_campo", "campo"),
        ("tx_valor", "valor"), ("dt_registro", "timestamp"),
    )),
    "antena": ("erb_tb01_antenas", (
        ("tp_rede", "tipo_rede"), ("nu_mcc", "mcc"), ("nu_mnc", "mnc"), ("nu_lac", "lac"),
        ("nu_cid", "cid"), ("vl_latitude", "latitude"), ("vl_longitude", "longitude"),
        ("dt_registro", "timestamp"),
    )),
    "voicemail": ("vms_tb01_voicemail", (
        ("no_aplicativo", "aplicativo"), ("nu_telefone", "telefone"),
        ("no_contato", "nome_contato"), ("dt_recebimento", "timestamp"),
        ("dt_modificacao", "modificacao"), ("qt_duracao_segundos", "duracao_segundos"),
        ("in_ouvida", "ouvida"), ("tp_voicemail", "tipo"), ("tx_transcricao", "transcricao"),
    )),
    "favorito": ("fav_tb01_favoritos", (
        ("no_aplicativo", "aplicativo"), ("tx_titulo", "titulo"), ("tx_url", "url"),
        ("tx_pasta", "pasta"), ("qt_visitas", "qt_visitas"),
        ("dt_ultima_visita", "ultima_visita"),
    )),
    "download": ("dwn_tb01_downloads", (
        ("no_aplicativo", "aplicativo"), ("tx_url", "url"), ("tx_destino", "destino"),
        ("qt_bytes", "bytes"), ("qt_bytes_recebidos", "bytes_recebidos"),
        ("cd_situacao", "situacao"), ("dt_inicio", "inicio"), ("dt_fim", "fim"),
        ("dt_ultimo_acesso", "ultimo_acesso"),
        ("tx_redirecionamentos", "redirecionamentos"),
    )),
    "transferencia": ("trf_tb01_transferencias", (
        ("no_aplicativo", "aplicativo"), ("tp_transferencia", "tipo"),
        ("tx_descricao", "descricao"), ("vl_valor", "valor"), ("no_moeda", "moeda"),
        ("cd_situacao", "situacao"), ("tx_participantes", "participantes"),
        ("dt_envio", "dt_envio"), ("dt_processamento", "dt_processamento"),
    )),
    "cartao_credito": ("crt_tb01_cartoes_credito", (
        ("no_aplicativo", "aplicativo"), ("no_bandeira", "bandeira"), ("nu_cartao", "numero"),
        ("dt_validade", "validade"), ("no_titular", "titular"),
        ("dt_ultimo_uso", "ultimo_uso"),
    )),
    "cartao_carteira": ("mbc_tb01_cartoes_carteira", (
        ("no_aplicativo", "aplicativo"), ("tx_descricao", "descricao"),
        ("no_organizacao", "organizacao"), ("tp_cartao", "tipo"),
        ("co_barras", "codigo_barras"), ("dt_compra", "dt_compra"),
        ("dt_modificacao", "dt_modificacao"), ("dt_expiracao", "dt_expiracao"),
        ("vl_latitude", "latitude"), ("vl_longitude", "longitude"),
    )),
    "gravacao": ("grv_tb01_gravacoes", (
        ("no_aplicativo", "aplicativo"), ("tx_titulo", "titulo"), ("tp_gravacao", "tipo"),
        ("dt_gravacao", "timestamp"), ("qt_duracao_segundos", "duracao_segundos"),
    )),
    "uso_rede": ("rde_tb01_uso_rede", (
        ("no_aplicativo", "aplicativo"), ("no_ssid", "ssid"), ("tp_conexao", "tipo_conexao"),
        ("tp_uso", "tipo_uso"), ("in_roaming", "roaming"),
        ("qt_bytes_recebidos", "bytes_recebidos"), ("qt_bytes_enviados", "bytes_enviados"),
        ("dt_inicio", "inicio"), ("dt_fim", "fim"),
    )),
}

# Contador de INQ_TB02 que cada domínio novo alimenta.
_CONTADOR_DOMINIO = {
    "uso_app": "uso_apps", "dispositivo_reconhecido": "dispositivos_reconhecidos",
    "conexao_dispositivo": "conexoes_dispositivo", "cookie": "cookies",
    "atividade_social": "atividade_social", "busca": "buscas", "envio_nuvem": "envios_nuvem",
    "compromisso": "calendario", "uso_aplicativo": "uso_aplicativo", "trajeto": "trajetos",
    "info_aparelho": "info_aparelho", "antena": "antenas", "voicemail": "voicemail",
    "favorito": "favoritos", "download": "downloads", "transferencia": "transferencias",
    "cartao_credito": "cartoes_credito", "cartao_carteira": "cartoes_carteira",
    "gravacao": "gravacoes", "uso_rede": "uso_rede",
}

# Colunas VARCHAR(3000) por convenção de nome; as TX_ são TEXT e não são cortadas.
_PREFIXOS_CURTOS = ("no_", "tp_", "cd_", "nu_", "co_")
_LIMITE_CURTO = 3000


def _gravar_dominio_novo(cur, tipo: str, id_extracao: int, dados: dict) -> None:
    if canal_extracao.emitir("dominio_novo", (tipo, id_extracao, dados)):  # nó entrega ao central em vez de gravar
        return
    """Grava um registro de um dos domínios de _DOMINIOS_NOVOS."""
    tabela, colunas = _DOMINIOS_NOVOS[tipo]
    campos = ["id_extracao"] + [coluna for coluna, _ in colunas] + ["cd_status_ufdr"]
    valores: list = [id_extracao]
    for coluna, chave in colunas:
        valor = dados.get(chave)
        if isinstance(valor, str) and coluna.startswith(_PREFIXOS_CURTOS):
            valor = valor[:_LIMITE_CURTO]
        valores.append(valor)
    estado = dados.get("estado")
    valores.append(estado[:50] if isinstance(estado, str) else estado)
    marcadores = ", ".join(["%s"] * len(campos))
    cur.execute(
        f"INSERT INTO investigacao.{tabela} ({', '.join(campos)}) VALUES ({marcadores})",
        valores,
    )


# domínios de e-mail que identificam a conta Apple/iCloud (titular do iPhone)
_DOMINIOS_APPLE = ("@icloud.com", "@me.com", "@mac.com", "@apple.com")


def _coletar_conta_ios(conta: dict, acc: dict) -> None:
    """Acumula a(s) conta(s) Apple/iCloud (o titular do iPhone) vindas de <model type='UserAccount'>.

    Dos ~200 UserAccount de um iPhone a maioria são contas de apps; só interessa a Apple/iCloud.
    Dedup por e-mail/usuário — o mesmo Apple ID aparece repetido (IMAPMail, Find My, CardDAV…)."""
    usuario = (conta.get("usuario") or "").strip()
    if not usuario:
        return
    servico = (conta.get("servico") or "").lower()
    ehApple = usuario.lower().endswith(_DOMINIOS_APPLE) or "apple" in servico or "icloud" in servico
    if not ehApple:
        return
    atual = acc.get(usuario.lower())
    if atual is None:
        acc[usuario.lower()] = dict(conta)
    else:  # completa nome/data a partir de outra linha do mesmo Apple ID
        if not atual.get("nome") and conta.get("nome"):
            atual["nome"] = conta["nome"]
        if not atual.get("criado_em") and conta.get("criado_em"):
            atual["criado_em"] = conta["criado_em"]


def _coletar_fone_dono(chat: dict, acc: dict,
                        fone_metadado: str | None = None) -> None:
    """Acumula o telefone do titular a partir dos participantes marcados IsPhoneOwner (o número
    do dono aparece em toda conversa). Conta as ocorrências para escolher o mais frequente.
    fone_metadado: telefone lido do metadado LastUsedMSISDN (fallback quando IsPhoneOwner
    não está presente no UFDR — comum em iPhones)."""
    # Se temos o telefone do metadado, já registra como dono (funciona quando IsPhoneOwner
    # não existe no UFDR — iPhones). A conversa atual conta como +1 ocorrência.
    if fone_metadado:
        reg = acc.setdefault("+" + fone_metadado, {"nome": None, "n": 0})
        reg["n"] += 1

    for p in chat.get("participantes", []):
        if not p.get("dono_dispositivo"):
            continue
        m = re.match(r"(\d{10,15})", p.get("jid") or "")
        if not m:
            continue
        reg = acc.setdefault("+" + m.group(1), {"nome": None, "n": 0})
        reg["n"] += 1
        if not reg["nome"] and p.get("nome"):
            reg["nome"] = p["nome"]


def _derivar_dono_ios(cur, id_extracao: int, contas_ios: dict, fone_dono: dict) -> int:
    if canal_extracao.emitir("dono_ios", (id_extracao, contas_ios, fone_dono)):
        # nó entrega ao central; o número de donos que a central vai gravar é determinístico
        return len(contas_ios) + (1 if fone_dono else 0)
    """iPhone (sem <model type='User'>): grava o dono a partir da conta Apple/iCloud e do número
    IsPhoneOwner. Devolve quantos registros de dono foram gravados."""
    n = 0
    for conta in contas_ios.values():
        usuario = (conta.get("usuario") or "")[:120]
        cur.execute(
            """
            INSERT INTO investigacao.don_tb01_donos
                (id_extracao, no_dono, tp_usuario, nu_identificador, dt_ultimo_login)
            VALUES (%s, %s, %s, %s, %s)
            """,
            # a conta iCloud costuma vir sem Name: usa o e-mail como rótulo pra o nó não ficar "(sem rótulo)"
            (id_extracao, conta.get("nome") or usuario, "Apple ID (iOS)", usuario, conta.get("criado_em")),
        )
        n += 1
    if fone_dono:  # o número que aparece como dono no maior nº de conversas
        fone, reg = max(fone_dono.items(), key=lambda kv: kv[1]["n"])
        cur.execute(
            """
            INSERT INTO investigacao.don_tb01_donos
                (id_extracao, no_dono, tp_usuario, nu_identificador, dt_ultimo_login)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (id_extracao, reg.get("nome"), "Titular (WhatsApp)", fone[:120], None),
        )
        n += 1
    return n


def obter_id_inquerito_de_extracao(id_extracao: int) -> int | None:
    if canal_extracao.em_modo_entrega():
        return None  # nó: identificação de pessoas/indexação são tarefas do central
    """Resolve o id_inquerito dono de uma extração, para checar permissão em rotas que só
    recebem id_extracao. Devolve None se a extração não existir."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id_inquerito FROM investigacao.inq_tb02_extracoes WHERE id_extracao = %s",
                (id_extracao,),
            )
            row = cur.fetchone()
            return row[0] if row else None


# Uma linha por tp_origem de entidade (ver 21_entidades_padroes.sql) com a subconsulta que resolve, a
# partir de um id_extracao, os id_origem daquele tipo que pertencem a ela — usado só para apagar
# extração (apagar inquérito inteiro é mais simples: mexe direto por id_inquerito).
_ORIGENS_ENTIDADE_POR_EXTRACAO = [
    ("wpp_mensagem", """
        SELECT m.id_mensagem FROM investigacao.wpp_tb04_mensagens m
        JOIN investigacao.wpp_tb02_conversas c ON c.id_conversa = m.id_conversa
        WHERE c.id_extracao = %s
    """),
    ("sms_mensagem", """
        SELECT m.id_mensagem FROM investigacao.sms_tb04_mensagens m
        JOIN investigacao.sms_tb02_conversas c ON c.id_conversa = m.id_conversa
        WHERE c.id_extracao = %s
    """),
    ("msg_mensagem", """
        SELECT m.id_mensagem FROM investigacao.msg_tb04_mensagens m
        JOIN investigacao.msg_tb02_conversas c ON c.id_conversa = m.id_conversa
        WHERE c.id_extracao = %s
    """),
    ("contato_agenda", "SELECT id_contato FROM investigacao.ctt_tb01_contatos WHERE id_extracao = %s"),
    ("contato_wpp", "SELECT id_contato FROM investigacao.wpp_tb01_contatos WHERE id_extracao = %s"),
    ("contato_sms", "SELECT id_contato FROM investigacao.sms_tb01_contatos WHERE id_extracao = %s"),
    ("contato_msg", "SELECT id_contato FROM investigacao.msg_tb01_contatos WHERE id_extracao = %s"),
    ("ligacao", "SELECT id_ligacao FROM investigacao.lig_tb01_ligacoes WHERE id_extracao = %s"),
    ("localizacao", "SELECT id_localizacao FROM investigacao.loc_tb01_localizacoes WHERE id_extracao = %s"),
    ("notificacao", "SELECT id_notificacao FROM investigacao.ntf_tb01_notificacoes WHERE id_extracao = %s"),
    ("nota", "SELECT id_nota FROM investigacao.not_tb01_notas WHERE id_extracao = %s"),
    ("navegacao", "SELECT id_pagina FROM investigacao.nav_tb01_paginas_visitadas WHERE id_extracao = %s"),
    ("aplicativo", "SELECT id_aplicativo FROM investigacao.app_tb01_aplicativos WHERE id_extracao = %s"),
    ("dono", "SELECT id_dono FROM investigacao.don_tb01_donos WHERE id_extracao = %s"),
    ("wifi", "SELECT id_rede_wifi FROM investigacao.wifi_tb01_redes WHERE id_extracao = %s"),
    ("conta_financeira", "SELECT id_conta_financeira FROM investigacao.bnc_tb01_contas_financeiras WHERE id_extracao = %s"),
    ("conta_usuario", "SELECT id_conta_usuario FROM investigacao.cta_tb01_contas_usuario WHERE id_extracao = %s"),
]


def _apagar_anotacoes_de_extracao(cur, id_extracao: int) -> None:
    """Remove tudo que aponta (via tp_origem/id_origem, sem FK — ver 21_entidades_padroes.sql e
    21_entidades_padroes.sql) para registros desta extração especificamente, sem mexer em anotações
    de outras extrações do mesmo inquérito: entidades/relações, evidências de entidades canônicas
    e detecções de padrões de crime. Precisa rodar antes de _apagar_dados_extracao, enquanto os
    registros de origem ainda existem para as subconsultas encontrarem."""
    for tp_origem, subconsulta in _ORIGENS_ENTIDADE_POR_EXTRACAO:
        cur.execute(
            f"""
            DELETE FROM investigacao.ent_tb03_relacoes
            WHERE id_entidade_origem IN (
                SELECT id_entidade FROM investigacao.ent_tb01_entidades
                WHERE tp_origem = %s AND id_origem IN ({subconsulta})
            ) OR id_entidade_destino IN (
                SELECT id_entidade FROM investigacao.ent_tb01_entidades
                WHERE tp_origem = %s AND id_origem IN ({subconsulta})
            )
            """,
            (tp_origem, id_extracao, tp_origem, id_extracao),
        )
        # a foto do nó vai junto com a entidade que a carregava (cascata manual: ENT_TB09
        # aponta o nó por TP_NO/ID_NO, sem FK). Antes do DELETE, enquanto o id ainda existe.
        cur.execute(
            f"""
            DELETE FROM investigacao.ent_tb09_imagens
            WHERE tp_no = 'entidade' AND id_no IN (
                SELECT id_entidade FROM investigacao.ent_tb01_entidades
                WHERE tp_origem = %s AND id_origem IN ({subconsulta})
            )
            """,
            (tp_origem, id_extracao),
        )
        cur.execute(
            f"""
            DELETE FROM investigacao.ent_tb01_entidades
            WHERE tp_origem = %s AND id_origem IN ({subconsulta})
            """,
            (tp_origem, id_extracao),
        )
        # evidências de canônicas e detecções de padrões usam o mesmo domínio tp_origem/id_origem.
        # A canônica em si (ent_tb05) não é apagada aqui: ela pode ter evidências de outras
        # extrações do mesmo inquérito — só somem os vínculos com os registros que estão indo embora.
        cur.execute(
            f"""
            DELETE FROM investigacao.ent_tb06_canonica_evidencias
            WHERE tp_origem = %s AND id_origem IN ({subconsulta})
            """,
            (tp_origem, id_extracao),
        )
        cur.execute(
            f"""
            DELETE FROM investigacao.pdr_tb03_deteccoes
            WHERE tp_origem = %s AND id_origem IN ({subconsulta})
            """,
            (tp_origem, id_extracao),
        )

    # Pessoa criada pela identificação automática que ficou sem NENHUMA evidência é
    # sobra do aparelho que acabou de sair: some junto (identificadores e relações caem
    # em cascata). Só a automática, e só se ninguém a relacionou a mão na mesa — nó que
    # o investigador tocou é dele, não do processo.
    cur.execute(
        """
        DELETE FROM investigacao.ent_tb05_canonicas c
        WHERE c.in_automatico
          AND c.id_inquerito = (SELECT id_inquerito FROM investigacao.inq_tb02_extracoes WHERE id_extracao = %s)
          AND NOT EXISTS (SELECT 1 FROM investigacao.ent_tb06_canonica_evidencias ev
                           WHERE ev.id_canonica = c.id_canonica)
          AND NOT EXISTS (SELECT 1 FROM investigacao.ent_tb07_canonica_relacoes r
                           WHERE (r.id_canonica_origem = c.id_canonica OR r.id_canonica_destino = c.id_canonica)
                             AND NOT r.in_automatico)
        """,
        (id_extracao,),
    )

    # Fotos que ficaram sem nó (as das canônicas que acabaram de sair). É uma varredura do que
    # sobrou, e não um DELETE espelhando o predicado acima: repetir aquelas três condições em
    # dois lugares é o tipo de coisa que sai do ar assim que uma delas mudar.
    cur.execute(
        """
        DELETE FROM investigacao.ent_tb09_imagens i
        WHERE i.tp_no = 'canonica'
          AND i.id_inquerito = (SELECT id_inquerito FROM investigacao.inq_tb02_extracoes WHERE id_extracao = %s)
          AND NOT EXISTS (SELECT 1 FROM investigacao.ent_tb05_canonicas c WHERE c.id_canonica = i.id_no)
        """,
        (id_extracao,),
    )


def _apagar_dados_extracao(cur, id_extracao: int) -> None:
    """Remove conversas/mensagens/anexos/contatos/ligações/localizações/notificações de uma
    extração. Não mexe em entidades nem na própria linha de INQ_TB02_EXTRACOES — quem chama
    decide isso (apagar_extracao apaga as duas coisas; apagar_inquerito trata entidades à parte,
    por id_inquerito, e apaga todas as extrações de uma vez no final)."""
    cur.execute(
        """
        DELETE FROM investigacao.wpp_tb05_anexos WHERE id_mensagem IN (
            SELECT m.id_mensagem FROM investigacao.wpp_tb04_mensagens m
            JOIN investigacao.wpp_tb02_conversas c ON c.id_conversa = m.id_conversa
            WHERE c.id_extracao = %s
        )
        """,
        (id_extracao,),
    )
    cur.execute(
        """
        DELETE FROM investigacao.sms_tb05_anexos WHERE id_mensagem IN (
            SELECT m.id_mensagem FROM investigacao.sms_tb04_mensagens m
            JOIN investigacao.sms_tb02_conversas c ON c.id_conversa = m.id_conversa
            WHERE c.id_extracao = %s
        )
        """,
        (id_extracao,),
    )
    cur.execute(
        "DELETE FROM investigacao.wpp_tb04_mensagens WHERE id_conversa IN "
        "(SELECT id_conversa FROM investigacao.wpp_tb02_conversas WHERE id_extracao = %s)",
        (id_extracao,),
    )
    cur.execute(
        "DELETE FROM investigacao.sms_tb04_mensagens WHERE id_conversa IN "
        "(SELECT id_conversa FROM investigacao.sms_tb02_conversas WHERE id_extracao = %s)",
        (id_extracao,),
    )
    cur.execute(
        "DELETE FROM investigacao.wpp_tb03_participantes WHERE id_conversa IN "
        "(SELECT id_conversa FROM investigacao.wpp_tb02_conversas WHERE id_extracao = %s)",
        (id_extracao,),
    )
    cur.execute(
        "DELETE FROM investigacao.sms_tb03_participantes WHERE id_conversa IN "
        "(SELECT id_conversa FROM investigacao.sms_tb02_conversas WHERE id_extracao = %s)",
        (id_extracao,),
    )
    cur.execute(
        """
        DELETE FROM investigacao.msg_tb05_anexos WHERE id_mensagem IN (
            SELECT m.id_mensagem FROM investigacao.msg_tb04_mensagens m
            JOIN investigacao.msg_tb02_conversas c ON c.id_conversa = m.id_conversa
            WHERE c.id_extracao = %s
        )
        """,
        (id_extracao,),
    )
    cur.execute(
        "DELETE FROM investigacao.msg_tb04_mensagens WHERE id_conversa IN "
        "(SELECT id_conversa FROM investigacao.msg_tb02_conversas WHERE id_extracao = %s)",
        (id_extracao,),
    )
    cur.execute(
        "DELETE FROM investigacao.msg_tb03_participantes WHERE id_conversa IN "
        "(SELECT id_conversa FROM investigacao.msg_tb02_conversas WHERE id_extracao = %s)",
        (id_extracao,),
    )
    cur.execute("DELETE FROM investigacao.wpp_tb02_conversas WHERE id_extracao = %s", (id_extracao,))
    cur.execute("DELETE FROM investigacao.sms_tb02_conversas WHERE id_extracao = %s", (id_extracao,))
    cur.execute("DELETE FROM investigacao.msg_tb02_conversas WHERE id_extracao = %s", (id_extracao,))
    cur.execute("DELETE FROM investigacao.wpp_tb01_contatos WHERE id_extracao = %s", (id_extracao,))
    cur.execute("DELETE FROM investigacao.sms_tb01_contatos WHERE id_extracao = %s", (id_extracao,))
    cur.execute("DELETE FROM investigacao.msg_tb01_contatos WHERE id_extracao = %s", (id_extracao,))
    cur.execute(
        "DELETE FROM investigacao.ctt_tb02_contato_entradas WHERE id_contato IN "
        "(SELECT id_contato FROM investigacao.ctt_tb01_contatos WHERE id_extracao = %s)",
        (id_extracao,),
    )
    cur.execute("DELETE FROM investigacao.ctt_tb01_contatos WHERE id_extracao = %s", (id_extracao,))
    cur.execute("DELETE FROM investigacao.lig_tb01_ligacoes WHERE id_extracao = %s", (id_extracao,))
    cur.execute("DELETE FROM investigacao.loc_tb01_localizacoes WHERE id_extracao = %s", (id_extracao,))
    cur.execute("DELETE FROM investigacao.ntf_tb01_notificacoes WHERE id_extracao = %s", (id_extracao,))
    cur.execute("DELETE FROM investigacao.not_tb01_notas WHERE id_extracao = %s", (id_extracao,))
    cur.execute("DELETE FROM investigacao.nav_tb01_paginas_visitadas WHERE id_extracao = %s", (id_extracao,))
    cur.execute("DELETE FROM investigacao.app_tb01_aplicativos WHERE id_extracao = %s", (id_extracao,))
    cur.execute("DELETE FROM investigacao.don_tb01_donos WHERE id_extracao = %s", (id_extracao,))
    cur.execute("DELETE FROM investigacao.cta_tb01_contas_usuario WHERE id_extracao = %s", (id_extracao,))
    cur.execute("DELETE FROM investigacao.eml_tb01_emails WHERE id_extracao = %s", (id_extracao,))
    cur.execute("DELETE FROM investigacao.bnc_tb01_contas_financeiras WHERE id_extracao = %s", (id_extracao,))
    cur.execute("DELETE FROM investigacao.pix_tb01_chaves WHERE id_extracao = %s", (id_extracao,))
    cur.execute("DELETE FROM investigacao.arq_tb01_arquivos WHERE id_extracao = %s", (id_extracao,))
    # sidecar de nuvem do arquivo ("<nome>-info.json" do Drive, "<foto>.jpg.json" do Google
    # Photos — ver funcoes/nuvem_metadados.py). Só a Nuvem IPED grava aqui, mas a tabela pende
    # da EXTRAÇÃO como qualquer outra: deixá-la para o lado do IPED fazia apagar o INQUÉRITO
    # falhar com ForeignKeyViolation, porque apagar_inquerito passa por esta função, e não pelo
    # caminho do card da nuvem.
    cur.execute("DELETE FROM investigacao.nuv_tb01_metadados_arquivo WHERE id_extracao = %s", (id_extracao,))
    cur.execute("DELETE FROM investigacao.wifi_tb01_redes WHERE id_extracao = %s", (id_extracao,))
    cur.execute("DELETE FROM investigacao.snh_tb01_senhas WHERE id_extracao = %s", (id_extracao,))
    cur.execute("DELETE FROM investigacao.afl_tb01_autofill WHERE id_extracao = %s", (id_extracao,))
    # domínios que passaram a ser importados (ver _DOMINIOS_NOVOS e
    # db/init/17_aparelho_redes.sql). Todos são filhos diretos da extração, sem
    # tabela-neta — daí o DELETE simples. A rede de segurança de apagar_extracao reclama
    # se algum ficar de fora.
    for tabela, _colunas in _DOMINIOS_NOVOS.values():
        cur.execute(f"DELETE FROM investigacao.{tabela} WHERE id_extracao = %s", (id_extracao,))
    # tabelas genéricas de conta na nuvem (Nuvem IPED hoje, Microsoft/Apple depois — ver
    # funcoes/contas_nuvem_utils.py)
    cur.execute("DELETE FROM investigacao.atv_tb01_atividades WHERE id_extracao = %s", (id_extracao,))
    cur.execute("DELETE FROM investigacao.acs_tb01_acessos WHERE id_extracao = %s", (id_extracao,))
    cur.execute("DELETE FROM investigacao.acs_tb02_agregados WHERE id_extracao = %s", (id_extracao,))
    cur.execute("DELETE FROM investigacao.acs_tb03_conexoes WHERE id_extracao = %s", (id_extracao,))
    cur.execute("DELETE FROM investigacao.dvc_tb01_dispositivos WHERE id_extracao = %s", (id_extracao,))
    cur.execute("DELETE FROM investigacao.prf_tb01_perfil WHERE id_extracao = %s", (id_extracao,))
    cur.execute("DELETE FROM investigacao.evt_tb01_eventos WHERE id_extracao = %s", (id_extracao,))


def apagar_extracao(id_extracao: int) -> bool:
    """Apaga uma extração e tudo o que veio dela (conversas, mensagens, anexos, contatos,
    ligações, localizações, notificações, entidades, evidências de canônicas e detecções de
    padrões ligadas a esses registros). Devolve False se a extração não existir. Levanta
    ValueError se ela ainda estiver em andamento, ou se sobrar alguma tabela filha não tratada
    aqui (mensagem legível em vez de 500 genérico — mesma rede de segurança de apagar_inquerito)."""
    # uma extração presa (worker morto) não pode trancar o apagar para sempre: libera primeiro
    liberar_extracoes_travadas()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT cd_status FROM investigacao.inq_tb02_extracoes WHERE id_extracao = %s", (id_extracao,))
            row = cur.fetchone()
            if row is None:
                return False
            # 'pendente' agora quer dizer "na fila, nenhum worker tocou nela" — dá para apagar
            # sem risco. Só 'processando' precisa esperar (ou ser cancelada antes).
            if row[0] == "processando":
                raise ValueError("Esta extração ainda está em andamento — aguarde terminar para apagar.")

            # A ponte da Nuvem IPED tem status próprio em IPD_TB01, e é ELE que diz se a thread
            # ainda está gravando: quem manda o card para 'erro' pode ter sido o watchdog, e a
            # importação seguir viva (ver _BATIMENTOS_PONTE em iped_utils). Sem esta checagem,
            # apagar aqui deleta os filhos por baixo da thread — ForeignKeyViolation nela,
            # importação perdida — contornando o guard que apagar_iped já faz certo.
            cur.execute(
                "SELECT 1 FROM investigacao.ipd_tb01_extracoes "
                "WHERE id_extracao = %s AND cd_status IN ('pendente', 'processando')",
                (id_extracao,),
            )
            if cur.fetchone() is not None:
                raise ValueError(
                    "A importação da Nuvem IPED desta extração ainda está em andamento — "
                    "cancele-a e aguarde terminar para apagar.")

            _apagar_anotacoes_de_extracao(cur, id_extracao)
            _apagar_dados_extracao(cur, id_extracao)
            # extração-ponte da Nuvem IPED: a linha de controle aponta pra cá via ID_EXTRACAO
            # (a Nuvem Google — GTK — foi removida do schema pela migração 92)
            cur.execute("DELETE FROM investigacao.ipd_tb01_extracoes WHERE id_extracao = %s", (id_extracao,))
            try:
                cur.execute("DELETE FROM investigacao.inq_tb02_extracoes WHERE id_extracao = %s", (id_extracao,))
            except psycopg.errors.ForeignKeyViolation as exc:
                tabela = getattr(exc.diag, "table_name", None) or "desconhecida"
                raise ValueError(
                    f"Não foi possível apagar: ainda há registros ligados a esta extração na tabela "
                    f"{tabela}. Avise o administrador do sistema."
                )
        conn.commit()
    return True


# Extração que não toca o banco por este tempo é considerada presa (worker morto num restart da
# API, ou travado — ex.: pool de transcrição emperrado num áudio) e é liberada por
# liberar_extracoes_travadas. Extração saudável grava a cada poucos minutos em qualquer fase
# (progresso a cada 200 registros no passe principal, a cada áudio transcrito, a cada bloco do
# hash, a cada lote da indexação semântica), então o folgado não mata job legítimo.
_MINUTOS_EXTRACAO_STALE = 60


def liberar_extracoes_travadas() -> int:
    """Libera extrações presas: linha em 'processando' sem atividade há tempo demais
    (DT_ULTIMA_ATIVIDADE — fase/progresso/pedido de parada — ou, na falta, o início do
    processamento/upload, para linhas antigas anteriores à coluna). Vira 'cancelado' se havia
    pedido de parada (FL_CANCELAR — os dados já gravados ficam preservados, que é o que o
    investigador pediu) e 'erro' com mensagem legível caso contrário; badge sai de "Processando…"/
    "Parando extração…" sozinho e a extração volta a poder ser apagada (o DELETE recusa
    'processando'). Sem isto, um restart da API no meio de uma extração deixava o status preso
    para sempre. Devolve quantas liberou.

    O predicado é PURAMENTE TEMPORAL, e precisa continuar sendo: com mais de um worker do uvicorn
    não há como saber, daqui, quais linhas são de um processo morto e quais são de um processo
    vivo do lado. Um "libera tudo que está ativo" no startup mataria a extração de um colega."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE investigacao.inq_tb02_extracoes
                SET cd_status = CASE WHEN fl_cancelar THEN 'cancelado' ELSE 'erro' END,
                    cd_fase = NULL, qt_progresso_fase = NULL,
                    dt_fim_processamento = now(),
                    tx_erro = CASE WHEN fl_cancelar THEN NULL
                                   ELSE 'Extração interrompida (liberada por inatividade).' END
                -- SÓ 'processando'. Antes da fila, 'pendente' significava "thread já disparada,
                -- ainda não bateu", e entrava aqui. Agora significa "na fila, esperando vaga": uma
                -- extração pode ficar horas assim de forma perfeitamente saudável, atrás de outra
                -- de 40 GB, e matá-la por inatividade seria o watchdog comendo a própria fila.
                -- Quem tira as 'pendente' da fila é o despachante (_despachante_extracoes).
                WHERE cd_status = 'processando'
                  AND COALESCE(dt_ultima_atividade, dt_inicio_processamento, dt_upload)
                      < now() - make_interval(mins => %s)
                """,
                (_MINUTOS_EXTRACAO_STALE,),
            )
            liberadas = cur.rowcount
        conn.commit()
    if liberadas:
        logger.warning("Liberadas %s extração(ões) presa(s) por inatividade", liberadas)
    return liberadas


def solicitar_cancelamento_extracao(id_extracao: int) -> bool:
    """Pede que o worker interrompa a extração; durante embeddings, também sinaliza a indexação."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE investigacao.inq_tb02_extracoes
                   SET fl_cancelar = true, cd_fase = 'cancelando', qt_progresso_fase = NULL,
                       dt_ultima_atividade = now()
                   WHERE id_extracao = %s AND cd_status = 'processando'
                   RETURNING id_inquerito""",
                (id_extracao,),
            )
            row = cur.fetchone()
            if row is None:
                # ainda na fila, sem worker nenhum para obedecer ao fl_cancelar: fecha na hora
                cur.execute(
                    """UPDATE investigacao.inq_tb02_extracoes
                       SET cd_status = 'cancelado', cd_fase = NULL, qt_progresso_fase = NULL,
                           dt_fim_processamento = now(), dt_ultima_atividade = now()
                       WHERE id_extracao = %s AND cd_status = 'pendente'
                       RETURNING id_inquerito""",
                    (id_extracao,),
                )
                conn.commit()
                return cur.fetchone() is not None
        conn.commit()
    if not row:
        return False
    from funcoes.embed_utils import solicitar_cancelamento_indexacao
    solicitar_cancelamento_indexacao(row[0])
    return True


def apagar_inquerito(id_inquerito: int) -> bool:
    """Apaga um inquérito inteiro: todas as suas extrações (e os dados de cada uma), entidades,
    relações, entidades canônicas (com evidências e relações), histórico de busca automática,
    execuções/detecções de padrões de crime, conversas de IA, permissões concedidas, RIFs,
    inquéritos em PDF, extratos do SIMBA, declarações de IRPF, IPEIs de sigilo fiscal, documentos
    anexados, marcações de relatório, marcadores nomeados, .docx gerados pela IA, auditoria
    de IA e o índice da busca semântica — ou seja, toda tabela com FK para INQ_TB01. Devolve False
    se o inquérito não existir. Levanta ValueError se alguma extração ainda estiver em andamento
    ou se sobrar alguma tabela filha não tratada aqui."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM investigacao.inq_tb02_extracoes WHERE id_inquerito = %s AND cd_status IN ('pendente', 'processando')",
                (id_inquerito,),
            )
            if cur.fetchone() is not None:
                raise ValueError("Este inquérito tem uma extração em andamento — aguarde terminar para apagar.")

            cur.execute("SELECT id_extracao FROM investigacao.inq_tb02_extracoes WHERE id_inquerito = %s", (id_inquerito,))
            ids_extracao = [linha[0] for linha in cur.fetchall()]

            try:
                _apagar_dados_inquerito(cur, id_inquerito, ids_extracao)
            except psycopg.errors.ForeignKeyViolation as exc:
                # Uma tabela nova passou a apontar para o inquérito (ou pra algo dele) e ficou de
                # fora da limpeza abaixo. Vira mensagem legível na tela em vez de 500 genérico,
                # dizendo qual é — mais fácil de diagnosticar do que um "Erro interno do servidor".
                tabela = getattr(exc.diag, "table_name", None) or "desconhecida"
                raise ValueError(
                    f"Não foi possível apagar: ainda há registros ligados a este inquérito na tabela "
                    f"{tabela}. Avise o administrador do sistema."
                )
            apagou = cur.rowcount > 0
        conn.commit()
    return apagou


def _apagar_dados_inquerito(cur, id_inquerito: int, ids_extracao: list[int]) -> None:
    """Faz as exclusões de apagar_inquerito propriamente ditas — inclusive a linha do próprio
    inquérito, por último —, isoladas numa função à parte só pra poder envolver tudo num único
    try/except ForeignKeyViolation (ver apagar_inquerito)."""
    # fotos dos nós da mesa primeiro: ENT_TB09 aponta o nó por TP_NO/ID_NO (sem FK), mas TEM
    # FK para o inquérito — deixá-las para trás faria o DELETE do inquérito falhar no fim
    cur.execute("DELETE FROM investigacao.ent_tb09_imagens WHERE id_inquerito = %s", (id_inquerito,))
    cur.execute("DELETE FROM investigacao.ent_tb03_relacoes WHERE id_inquerito = %s", (id_inquerito,))
    cur.execute("DELETE FROM investigacao.ent_tb01_entidades WHERE id_inquerito = %s", (id_inquerito,))
    cur.execute("DELETE FROM investigacao.ent_tb04_buscas_automaticas WHERE id_inquerito = %s", (id_inquerito,))
    cur.execute("DELETE FROM investigacao.ent_tb07_canonica_relacoes WHERE id_inquerito = %s", (id_inquerito,))
    # ent_tb06_canonica_evidencias cai em cascata (ON DELETE CASCADE de ent_tb05)
    cur.execute("DELETE FROM investigacao.ent_tb05_canonicas WHERE id_inquerito = %s", (id_inquerito,))
    cur.execute("DELETE FROM investigacao.pdr_tb03_deteccoes WHERE id_inquerito = %s", (id_inquerito,))
    cur.execute("DELETE FROM investigacao.pdr_tb02_execucoes WHERE id_inquerito = %s", (id_inquerito,))
    # arquivos .md de prompt e processamentos primeiro: têm FK para a conversa (e os
    # processamentos, também para as mensagens)
    cur.execute("DELETE FROM investigacao.ia_tb04_arquivos_prompt WHERE id_inquerito = %s", (id_inquerito,))
    cur.execute(
        "DELETE FROM investigacao.ia_tb03_processamentos WHERE id_conversa_ia IN "
        "(SELECT id_conversa_ia FROM investigacao.ia_tb01_conversas WHERE id_inquerito = %s)",
        (id_inquerito,),
    )
    cur.execute(
        "DELETE FROM investigacao.ia_tb02_mensagens WHERE id_conversa_ia IN "
        "(SELECT id_conversa_ia FROM investigacao.ia_tb01_conversas WHERE id_inquerito = %s)",
        (id_inquerito,),
    )
    # .docx gerados a partir das conversas: o arquivo sai do disco antes da linha (depois do
    # DELETE não há mais como saber qual era). Import local pelo mesmo motivo do de relatórios.
    from funcoes.ia_relatorio_docx import apagar_relatorios_de_inquerito
    apagar_relatorios_de_inquerito(cur, id_inquerito)
    # .xlsx das consolidações pedidas no chat — mesmo desenho dos .docx acima
    from funcoes.ia_planilha_utils import apagar_planilhas_de_inquerito
    apagar_planilhas_de_inquerito(cur, id_inquerito)
    cur.execute("DELETE FROM investigacao.ia_tb01_conversas WHERE id_inquerito = %s", (id_inquerito,))
    cur.execute("DELETE FROM investigacao.inq_tb03_permissoes WHERE id_inquerito = %s", (id_inquerito,))
    # hipóteses do workspace: evidências e pendências saem pelo ON DELETE CASCADE das duas —
    # o vínculo polimórfico (rif/simba/ipl/extracao/documento) nas evidências não tem FK pros
    # documentos, mas sai junto com a hipótese
    cur.execute("DELETE FROM investigacao.hip_tb01_hipoteses WHERE id_inquerito = %s", (id_inquerito,))
    # RIFs e inquéritos em PDF apontam para inq_tb04_documentos: saem antes dos documentos
    for tabela in (
        "rif_tb06_identificacoes", "rif_tb06_arquivos", "rif_tb05_contrapartes",
        "rif_tb02_comunicacoes", "rif_tb03_envolvidos", "rif_tb04_ocorrencias",
    ):
        cur.execute(
            f"DELETE FROM investigacao.{tabela} WHERE id_rif IN "
            "(SELECT id_rif FROM investigacao.rif_tb01_rifs WHERE id_inquerito = %s)",
            (id_inquerito,),
        )
    cur.execute("DELETE FROM investigacao.rif_tb01_rifs WHERE id_inquerito = %s", (id_inquerito,))
    for tabela in ("ipl_tb02_pecas", "ipl_tb03_pessoas", "ipl_tb04_achados"):
        cur.execute(
            f"DELETE FROM investigacao.{tabela} WHERE id_ipl IN "
            "(SELECT id_ipl FROM investigacao.ipl_tb01_inqueritos_pdf WHERE id_inquerito = %s)",
            (id_inquerito,),
        )
    cur.execute("DELETE FROM investigacao.ipl_tb01_inqueritos_pdf WHERE id_inquerito = %s", (id_inquerito,))
    # extratos do SIMBA: também apontam para inq_tb04_documentos (a síntese textual)
    for tabela in ("smb_tb04_contrapartes", "smb_tb03_lancamentos", "smb_tb02_contas"):
        cur.execute(
            f"DELETE FROM investigacao.{tabela} WHERE id_extrato IN "
            "(SELECT id_extrato FROM investigacao.smb_tb01_extratos WHERE id_inquerito = %s)",
            (id_inquerito,),
        )
    cur.execute("DELETE FROM investigacao.smb_tb01_extratos WHERE id_inquerito = %s", (id_inquerito,))
    # declarações de IRPF: as linhas filhas apontam para IRP_TB01 e a declaração aponta para
    # inq_tb04_documentos (FK231) — tudo sai antes dos documentos, como RIF/IPL/SIMBA acima
    for tabela in (
        "irp_tb02_bens_direitos", "irp_tb03_dependentes", "irp_tb04_rendimentos",
        "irp_tb05_dividas", "irp_tb06_doacoes",
    ):
        cur.execute(
            f"DELETE FROM investigacao.{tabela} WHERE id_irpf IN "
            "(SELECT id_irpf FROM investigacao.irp_tb01_declaracoes WHERE id_inquerito = %s)",
            (id_inquerito,),
        )
    cur.execute("DELETE FROM investigacao.irp_tb01_declaracoes WHERE id_inquerito = %s", (id_inquerito,))
    # IPEIs (quebra de sigilo fiscal): as linhas filhas apontam para IPE_TB01/IPE_TB02, e a IPEI
    # aponta para DOIS documentos (o PDF e a planilha de anexo) — tudo sai antes dos documentos
    for tabela in (
        "ipe_tb03_relacionados", "ipe_tb04_anos", "ipe_tb05_fontes", "ipe_tb06_contas",
        "ipe_tb07_bens", "ipe_tb08_notas_fiscais", "ipe_tb09_contatos",
    ):
        cur.execute(
            f"DELETE FROM investigacao.{tabela} WHERE id_ipei IN "
            "(SELECT id_ipei FROM investigacao.ipe_tb01_ipeis WHERE id_inquerito = %s)",
            (id_inquerito,),
        )
    cur.execute(
        "DELETE FROM investigacao.ipe_tb02_alvos WHERE id_ipei IN "
        "(SELECT id_ipei FROM investigacao.ipe_tb01_ipeis WHERE id_inquerito = %s)",
        (id_inquerito,),
    )
    cur.execute("DELETE FROM investigacao.ipe_tb01_ipeis WHERE id_inquerito = %s", (id_inquerito,))
    # importações da Nuvem IPED: os dados em si já saem no loop de _apagar_dados_extracao abaixo
    # (id_extracao, a extração-ponte), porque moram nas tabelas compartilhadas com o UFDR — só a
    # linha de controle própria precisa ser apagada aqui.
    cur.execute("DELETE FROM investigacao.ipd_tb01_extracoes WHERE id_inquerito = %s", (id_inquerito,))
    cur.execute("DELETE FROM investigacao.inq_tb04_documentos WHERE id_inquerito = %s", (id_inquerito,))
    # marcações "marcar para o relatório" e auditoria de IA (o custo/turno da IA)
    cur.execute("DELETE FROM investigacao.rel_tb03_marcacoes WHERE id_inquerito = %s", (id_inquerito,))
    # marcadores nomeados: presos ao inquérito (FK202 sem CASCADE — sem este DELETE o
    # próprio inquérito não sai). As aplicações (mrc_tb02) já foram embora em cascata
    # com as marcações acima; o que sobra aqui são as etiquetas em si.
    cur.execute("DELETE FROM investigacao.mrc_tb01_marcadores WHERE id_inquerito = %s", (id_inquerito,))
    # relatórios gerados: o .docx sai do disco ANTES da linha ir embora — depois do DELETE não há
    # mais como saber qual arquivo era, e ele ficaria órfão em data/relatorios.
    # import local: relatorios_geracao → relatorios_docx → este módulo, o ciclo não fecha no topo
    from funcoes.relatorios_geracao import apagar_arquivos_de_inquerito
    apagar_arquivos_de_inquerito(cur, id_inquerito)
    cur.execute("DELETE FROM investigacao.rel_tb04_geracoes WHERE id_inquerito = %s", (id_inquerito,))
    cur.execute("DELETE FROM investigacao.ia_tb04_auditoria WHERE id_inquerito = %s", (id_inquerito,))
    # busca semântica: vetores e histórico de indexação têm FK para o inquérito
    cur.execute("DELETE FROM investigacao.emb_tb01_embeddings WHERE id_inquerito = %s", (id_inquerito,))
    cur.execute("DELETE FROM investigacao.emb_tb02_indexacoes WHERE id_inquerito = %s", (id_inquerito,))
    # camadas geoespaciais importadas de shapefile: as feições e os anéis saem em cascata
    cur.execute("DELETE FROM investigacao.geo_tb02_camadas WHERE id_inquerito = %s", (id_inquerito,))

    for id_extracao in ids_extracao:
        _apagar_dados_extracao(cur, id_extracao)
    cur.execute("DELETE FROM investigacao.inq_tb02_extracoes WHERE id_inquerito = %s", (id_inquerito,))

    # resumo noturno: linhas por fonte + consolidado saem com o inquérito.
    # Guarda para instalação antiga sem a migração 0020: sem ele, o DELETE numa tabela
    # inexistente falharia no planejamento e travaria apagar qualquer inquérito até migrar.
    cur.execute("SELECT to_regclass('investigacao.inq_tb05_resumo_noturno')")
    if cur.fetchone()[0] is not None:
        cur.execute(
            "DELETE FROM investigacao.inq_tb05_resumo_noturno WHERE id_inquerito = %s",
            (id_inquerito,),
        )

    cur.execute("DELETE FROM investigacao.inq_tb01_inqueritos WHERE id_inquerito = %s", (id_inquerito,))


def listar_extracoes(usuario: dict, id_inquerito: int | None = None) -> list[dict]:
    """Lista as extrações (status, contagens) visíveis ao usuário, mais recente primeiro —
    usado no modal "todas as extrações". Admin vê todas; os demais só as de inquéritos próprios/permitidos.

    Quando id_inquerito é informado, retorna só as extrações daquele inquérito — usado no
    deep-link investigacao.html?inquerito=ID para não trazer todas as extrações do sistema."""
    # o badge é alimentado por polling desta listagem: libera aqui as presas (worker morto) para o
    # status sair de "Processando…"/"Parando extração…" sozinho na próxima batida
    liberar_extracoes_travadas()
    filtro, filtro_params = _filtro_acesso_sql(usuario, "i")
    if id_inquerito is not None:
        filtro += " AND i.id_inquerito = %s"
        filtro_params = filtro_params + (int(id_inquerito),)
    id_usuario = usuario["id_usuario"]
    if usuario["in_admin"]:
        pode_gerenciar_sql = "true"
        gerenciar_params = ()
    else:
        pode_gerenciar_sql = "(i.id_usuario_criador = %s OR EXISTS (SELECT 1 FROM investigacao.inq_tb03_permissoes p WHERE p.id_inquerito = i.id_inquerito AND p.id_usuario = %s AND p.in_admin = true))"
        gerenciar_params = (id_usuario, id_usuario)
    # Ordem dos params segue a ordem dos placeholders no SQL: {pode_gerenciar_sql} mora no SELECT
    # (antes do WHERE), então os params dele vêm ANTES dos do filtro — senão o psycopg amarra o
    # id_inquerito no placeholder errado e o deep-link ?inquerito=ID devolve vazio.
    params = gerenciar_params + filtro_params
    query = f"""
        SELECT i.id_inquerito, i.id_usuario_criador, i.nu_inquerito, e.id_extracao, e.no_arquivo, e.no_dispositivo, e.no_fabricante,
               e.in_nuvem, e.no_apelido,
               e.dt_extracao, e.dt_upload, e.dt_inicio_processamento, e.dt_fim_processamento,
               e.co_hash_md5, e.co_hash_sha1, e.co_hash_sha256,
               e.cd_status, e.tx_erro, e.tx_aviso, e.qt_conversas, e.qt_mensagens,
               e.cd_fase, e.qt_progresso_fase,
               -- prova compartilhada: cópia de uma extração de outro inquérito (ver
               -- funcoes/compartilhar_prova.py). O card usa para o badge "Prova compartilhada",
               -- que diz de qual inquérito ela veio.
               e.id_extracao_origem, e.nu_inquerito_origem, e.dt_compartilhamento,
               e.qt_sms_conversas, e.qt_sms_mensagens, e.qt_msg_conversas, e.qt_msg_mensagens,
               e.qt_contatos, e.qt_ligacoes,
               e.qt_localizacoes, e.qt_notificacoes, e.qt_navegacao, e.qt_aplicativos, e.qt_donos,
                e.qt_arquivos, e.qt_chaves_pix, e.qt_emails, e.qt_contas_financeiras,
                e.qt_redes_wifi, e.qt_pessoas_identificadas,
                e.qt_notas, e.qt_senhas, e.qt_autofill,
                -- domínios importados na auditoria de aproveitamento: o card usa para decidir
                -- quais botões mostrar (o que o aparelho não tem não vira botão)
                e.qt_uso_apps, e.qt_dispositivos_reconhecidos, e.qt_conexoes_dispositivo, e.qt_cookies,
                e.qt_atividade_social, e.qt_buscas, e.qt_envios_nuvem, e.qt_calendario,
                e.qt_uso_aplicativo, e.qt_trajetos, e.qt_info_aparelho, e.qt_antenas,
                e.qt_voicemail, e.qt_favoritos, e.qt_downloads, e.qt_transferencias,
                e.qt_cartoes_carteira, e.qt_gravacoes, e.qt_cartoes_credito, e.qt_uso_rede,
                -- produtos de nuvem (card de nuvem): contagens dos ZIPs aninhados
                e.qt_atividades, e.qt_eventos, e.qt_acessos, e.qt_dispositivos, e.qt_perfil,
                e.qt_metadados_nuvem,
                -- "WhatsApp (parcial)" tem o que mostrar? Mídia do WhatsApp em nuvem: tars do
                -- iCloud (files/Archives/Media…::) ou backup do Google Drive (wa_<numero>/).
                -- Só nas nuvens (inclusive a extração-ponte do IPED, que marca o fabricante, não
                -- IN_NUVEM) — num celular o botão jamais aparece, e um celular tem centenas
                -- de milhares de arquivos para o EXISTS varrer à toa a cada pintura da tela.
                -- strpos em vez de LIKE porque esta query é parametrizada (nota acima).
                CASE WHEN coalesce(e.in_nuvem, false) OR e.no_fabricante = 'IPED' THEN EXISTS (
                    SELECT 1 FROM investigacao.arq_tb01_arquivos a
                     WHERE a.id_extracao = e.id_extracao
                       AND (strpos(a.co_caminho_zip, 'files/Archives/Media') = 1
                            OR strpos(a.tx_caminho, 'Drive Mobile Backups/wa_') > 0))
                END AS tem_wa_parcial,
               -- IDENTIDADE DO APARELHO para a linha de resumo do cartão: o nome que o dono deu
               -- ao celular, a conta do sistema e o modelo detectado. Nada disso está em
               -- INQ_TB02 — são pares nome/valor do DeviceInfoEntry (DEV_TB01) e contas de app
               -- (CTA_TB01), lidos aqui por subconsulta (todas indexadas por id_extracao) para o
               -- cartão não precisar de uma chamada HTTP por extração. O rótulo do Cellebrite vem
               -- em português ou inglês conforme a versão, daí casar as duas formas.
               (SELECT d.tx_valor FROM investigacao.dev_tb01_info_aparelho d
                 WHERE d.id_extracao = e.id_extracao
                   AND lower(btrim(d.no_campo)) IN ('device name', 'nome do dispositivo')
                   AND coalesce(btrim(d.tx_valor), '') <> ''
                 ORDER BY d.id_info LIMIT 1) AS no_nome_dispositivo,
               -- só o modelo legível ("iPhone 13", "Galaxy A06"); o campo vizinho "Identificador
               -- do modelo de telefone detectado" (iPhone14,5) é código interno e fica de fora de
               -- propósito — por isso a comparação é exata, e não LIKE.
               (SELECT d.tx_valor FROM investigacao.dev_tb01_info_aparelho d
                 WHERE d.id_extracao = e.id_extracao
                   AND lower(btrim(d.no_campo)) IN ('modelo de telefone detectado', 'detected phone model')
                   AND coalesce(btrim(d.tx_valor), '') <> ''
                 ORDER BY d.id_info LIMIT 1) AS no_modelo_detectado,
               -- iPhone: o Apple ID vem declarado no DeviceInfoEntry. Sem esse campo, cai no dono
               -- que a ingestão sintetizou das contas iCloud (ver _derivar_dono_ios).
               COALESCE(
                 (SELECT d.tx_valor FROM investigacao.dev_tb01_info_aparelho d
                   WHERE d.id_extracao = e.id_extracao
                     AND lower(btrim(d.no_campo)) IN ('id da apple', 'apple id')
                     AND coalesce(btrim(d.tx_valor), '') <> ''
                   ORDER BY d.id_info LIMIT 1),
                 (SELECT dn.nu_identificador FROM investigacao.don_tb01_donos dn
                   WHERE dn.id_extracao = e.id_extracao AND dn.tp_usuario = 'Apple ID (iOS)'
                     AND coalesce(btrim(dn.nu_identificador), '') <> ''
                   ORDER BY dn.id_dono LIMIT 1)
               ) AS no_conta_apple,
               -- Android: a conta do Google não está no DeviceInfoEntry, e sim entre as contas de
               -- app. 'com.google' é a do gerenciador de contas do sistema (a conta DO APARELHO);
               -- Chrome/Drive/Fotos costumam repetir a mesma e servem de reserva. strpos em vez
               -- de LIKE porque a query leva parâmetros: o curinga do LIKE é lido como placeholder
               -- pelo psycopg e quebra a consulta inteira (vale também para comentário aqui dentro).
               (SELECT c.no_usuario FROM investigacao.cta_tb01_contas_usuario c
                 WHERE c.id_extracao = e.id_extracao
                   AND strpos(coalesce(c.no_usuario, ''), '@') > 0
                   AND (strpos(lower(coalesce(c.no_servico, '')), 'google') > 0
                        OR strpos(lower(coalesce(c.no_fonte, '')), 'google') > 0)
                 ORDER BY (lower(coalesce(c.no_servico, '')) = 'com.google') DESC,
                          (lower(coalesce(c.no_servico, '')) = 'google') DESC,
                          c.id_conta_usuario
                 LIMIT 1) AS no_conta_google,
               u.no_usuario AS registrado_por,
               {pode_gerenciar_sql} AS pode_gerenciar
        FROM investigacao.inq_tb02_extracoes e
        JOIN investigacao.inq_tb01_inqueritos i ON i.id_inquerito = e.id_inquerito
        LEFT JOIN seguranca.usr_tb01_usuarios u ON u.id_usuario = e.id_usuario
        WHERE true{filtro}
        ORDER BY e.dt_upload DESC
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            colunas = [d.name for d in cur.description]
            linhas = [dict(zip(colunas, linha)) for linha in cur.fetchall()]
    # o .ufdr precisa continuar na pasta para os anexos abrirem — ver _situacoes_arquivos_ufdr
    situacoes = _situacoes_arquivos_ufdr(list(dict.fromkeys(e["no_arquivo"] for e in linhas)))
    for e in linhas:
        e.update(situacoes[e["no_arquivo"]])
    return linhas


def listar_inqueritos(usuario: dict, id_inquerito: int | None = None) -> list[dict]:
    """Agrupa as extrações por inquérito visível ao usuário, para a seção "Investigações"
    (um card por inquérito). Admin vê todos; os demais só os que criaram ou receberam permissão.

    Quando id_inquerito é informado, retorna só aquele inquérito — usado no deep-link
    investigacao.html?inquerito=ID para não trazer todos os inquéritos do sistema."""
    filtro, filtro_params = _filtro_acesso_sql(usuario, "i")
    if id_inquerito is not None:
        filtro += " AND i.id_inquerito = %s"
        filtro_params = filtro_params + (int(id_inquerito),)
    id_usuario = usuario["id_usuario"]
    if usuario["in_admin"]:
        pode_gerenciar_sql = "true"
        gerenciar_params = ()
    else:
        pode_gerenciar_sql = "(i.id_usuario_criador = %s OR EXISTS (SELECT 1 FROM investigacao.inq_tb03_permissoes p WHERE p.id_inquerito = i.id_inquerito AND p.id_usuario = %s AND p.in_admin = true))"
        gerenciar_params = (id_usuario, id_usuario)
    # Ordem dos params segue a ordem dos placeholders no SQL: {pode_gerenciar_sql} mora no SELECT
    # (antes do WHERE), então os params dele vêm ANTES dos do filtro — senão o psycopg amarra o
    # id_inquerito no placeholder errado e o deep-link ?inquerito=ID devolve vazio.
    params = gerenciar_params + filtro_params
    query = f"""
        SELECT i.id_inquerito, i.nu_inquerito, i.in_caso_inteligencia, i.id_usuario_criador,
               i.tx_contexto_investigacao, i.tp_situacao,
               COALESCE(i.no_delegacia, d.no_delegacia, (
                   SELECT dd.no_delegacia
                   FROM investigacao.inq_tb03_permissoes p
                   JOIN seguranca.usr_tb01_usuarios u2 ON u2.id_usuario = p.id_usuario
                   JOIN seguranca.delegacias dd ON dd.id_delegacia = u2.id_delegacia
                   WHERE p.id_inquerito = i.id_inquerito
                   LIMIT 1
               )) AS delegacia,
               uc.no_usuario AS administrador,
               {pode_gerenciar_sql} AS pode_gerenciar,
               -- "extração" = fonte de CELULAR: a ponte de uma importação do IPED
               -- (NO_FABRICANTE='IPED') não conta como aparelho — ela era somada aqui e inflava
               -- os gates de "Comparar celulares"/"Nuvem × aparelho" com conta de nuvem. Caso
               -- do IPED entra quando a fonte é de fato um celular (mesma regra do tp_fonte do
               -- card: conversa/senha/rede/autofill decodificados).
               count(e.id_extracao) FILTER (WHERE e.no_fabricante IS DISTINCT FROM 'IPED')
               + (SELECT count(*) FROM investigacao.ipd_tb01_extracoes p
                  WHERE p.id_inquerito = i.id_inquerito AND p.cd_status = 'concluido'
                    AND (coalesce(p.qt_conversas, 0) > 0 OR coalesce(p.qt_senhas, 0) > 0
                         OR coalesce(p.qt_redes, 0) > 0 OR coalesce(p.qt_autofill, 0) > 0))
               AS qt_extracoes,
               coalesce(sum(e.qt_conversas), 0) AS qt_conversas,
               coalesce(sum(e.qt_mensagens), 0) AS qt_mensagens,
               coalesce(sum(e.qt_sms_conversas), 0) AS qt_sms_conversas,
               coalesce(sum(e.qt_sms_mensagens), 0) AS qt_sms_mensagens,
               coalesce(sum(e.qt_contatos), 0) AS qt_contatos,
               coalesce(sum(e.qt_ligacoes), 0) AS qt_ligacoes,
               coalesce(sum(e.qt_localizacoes), 0) AS qt_localizacoes,
               coalesce(sum(e.qt_notificacoes), 0) AS qt_notificacoes,
               coalesce(sum(e.qt_navegacao), 0) AS qt_navegacao,
               coalesce(sum(e.qt_aplicativos), 0) AS qt_aplicativos,
               coalesce(sum(e.qt_donos), 0) AS qt_donos,
               coalesce(sum(e.qt_arquivos), 0) AS qt_arquivos,
               coalesce(sum(e.qt_msg_conversas), 0) AS qt_msg_conversas,
               coalesce(sum(e.qt_msg_mensagens), 0) AS qt_msg_mensagens,
               (SELECT count(*) FROM investigacao.inq_tb04_documentos d
                 WHERE d.id_inquerito = i.id_inquerito) AS qt_documentos,
               (SELECT count(*) FROM investigacao.rif_tb01_rifs r
                 WHERE r.id_inquerito = i.id_inquerito) AS qt_rifs,
               (SELECT count(*) FROM investigacao.ipl_tb01_inqueritos_pdf p
                 WHERE p.id_inquerito = i.id_inquerito) AS qt_ipls,
               (SELECT count(*) FROM investigacao.irp_tb01_declaracoes p
                 WHERE p.id_inquerito = i.id_inquerito) AS qt_irpfs,
               (SELECT count(*) FROM investigacao.ipe_tb01_ipeis p
                 WHERE p.id_inquerito = i.id_inquerito) AS qt_ipeis,
               -- um card por operadora, não por arquivo (ver funcoes/telefonia_utils.py)
               (SELECT count(*) FROM investigacao.tel_tb01_telefonia p
                 WHERE p.id_inquerito = i.id_inquerito) AS qt_telefonia,
               (SELECT count(*) FROM investigacao.smb_tb01_extratos s
                 WHERE s.id_inquerito = i.id_inquerito) AS qt_simba,
               (SELECT count(*) FROM investigacao.ipd_tb01_extracoes p
                 WHERE p.id_inquerito = i.id_inquerito) AS qt_iped,
               -- a tela usa isto para decidir se continua repolling; as importações do SIMBA e da
               -- Nuvem IPED também rodam em background, então também seguram o poll - e o
               -- mesmo vale para a análise de entidades dos documentos anexados (cd_analise)
               (bool_or(e.cd_status IN ('pendente', 'processando'))
                OR EXISTS (SELECT 1 FROM investigacao.smb_tb01_extratos s
                            WHERE s.id_inquerito = i.id_inquerito
                              AND s.cd_status IN ('pendente', 'processando'))
                OR EXISTS (SELECT 1 FROM investigacao.ipd_tb01_extracoes p
                            WHERE p.id_inquerito = i.id_inquerito
                              AND p.cd_status IN ('pendente', 'processando'))
                OR EXISTS (SELECT 1 FROM investigacao.inq_tb04_documentos d
                            WHERE d.id_inquerito = i.id_inquerito
                              AND d.cd_analise IN ('pendente', 'processando'))
               ) AS tem_extracao_em_andamento,
               (SELECT e2.cd_fase FROM investigacao.inq_tb02_extracoes e2
                 WHERE e2.id_inquerito = i.id_inquerito AND e2.cd_status IN ('pendente', 'processando')
                 ORDER BY e2.dt_inicio_processamento DESC NULLS LAST LIMIT 1) AS fase_em_andamento,
               (SELECT e2.qt_progresso_fase FROM investigacao.inq_tb02_extracoes e2
                 WHERE e2.id_inquerito = i.id_inquerito AND e2.cd_status IN ('pendente', 'processando')
                 ORDER BY e2.dt_inicio_processamento DESC NULLS LAST LIMIT 1) AS progresso_em_andamento,
-- ÚLTIMA ATUALIZAÇÃO: último acesso ao inquérito (mesmo usado na auditoria)
                NULLIF(
                    COALESCE((SELECT max(dt_ultimo_acesso) FROM seguranca.usr_tb05_acessos_inquerito a
                         WHERE a.id_inquerito = i.id_inquerito), '1970-01-01'::timestamp),
                    '1970-01-01'::timestamp
                ) AS dt_ultima_atualizacao
        FROM investigacao.inq_tb01_inqueritos i
        LEFT JOIN seguranca.usr_tb01_usuarios uc ON uc.id_usuario = i.id_usuario_criador
        LEFT JOIN seguranca.delegacias d ON d.id_delegacia = uc.id_delegacia
        LEFT JOIN investigacao.inq_tb02_extracoes e ON e.id_inquerito = i.id_inquerito
        WHERE true{filtro}
        GROUP BY i.id_inquerito, i.nu_inquerito, i.in_caso_inteligencia, i.id_usuario_criador, i.tx_contexto_investigacao, i.tp_situacao, i.no_delegacia, d.no_delegacia, uc.no_usuario
        ORDER BY i.dt_cadastro DESC
    """
    delegacia_fallback = usuario.get("no_delegacia")
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            colunas = [d.name for d in cur.description]
            linhas = [dict(zip(colunas, linha)) for linha in cur.fetchall()]
    # ia_ativa: decisão pronta do flag IN_IA_ATIVA para o front não repetir a regra (era isso que
    # deixava o botão "Prompt de IA" cinza no card enquanto o resto da tela seguia liberado). Vale
    # a mesma delegacia que o card mostra, mais a do usuário logado — ver funcoes/ia_delegacia.py.
    sem_ia = delegacias_sem_ia()
    # a pessoa também pode ter a IA desligada sozinha (tela Usuários) — aí nenhum card libera IA
    usuario_sem_ia = normalizar_delegacia(delegacia_fallback) in sem_ia or usuario_com_ia_desativada(
        usuario.get("id_usuario")
    )
    for linha in linhas:
        if not linha.get("delegacia") and delegacia_fallback:
            linha["delegacia"] = delegacia_fallback
        linha["ia_ativa"] = not usuario_sem_ia and normalizar_delegacia(linha.get("delegacia")) not in sem_ia
    return linhas


def estatisticas_delegacia(usuario: dict, delegacia: str | None = None) -> dict:
    """Retorna estatísticas agregadas por delegacia para o dashboard.

    Regras atuais (pós-correção):
    - qt_inqueritos: total de inquéritos visíveis ao usuário (COUNT DISTINCT), filtrado por delegacia se informada.
    - qt_importacoes_processadas: soma de todos os tipos de arquivo concluídos no inquérito
      (UFDR concluído + SIMBA concluído + IPED concluído + RIF + IPL + IRPF + IPEI + documentos avulsos).
    - qt_mensagens_importadas: só UFDR concluído (qt_mensagens+qt_sms+qt_msg com cd_status='concluido').
    - qt_marcadores_realizados: contagem de aplicações de marcadores nos inquéritos filtrados.
    - qt_casos: total de inquéritos marcados como caso de inteligência visíveis ao usuário
      (IN_CASO_INTELIGENCIA), no mesmo escopo e filtro de delegacia dos demais.
    - qt_convergencias_abertas mantido como 0 para compatibilidade (KPI removido da tela).
    """
    filtro_acesso, params_acesso = _filtro_acesso_sql(usuario, "i")

    filtro_delegacia = ""
    params_delegacia: tuple = ()
    if delegacia:
        # Valor "—" (em dash) vem do front para delegacia indefinida (COALESCE fallback);
        # nesse caso filtra IS NULL em vez de igualdade literal, senão nunca bate.
        if delegacia == "—":
            filtro_delegacia = " AND COALESCE(i.no_delegacia, d.no_delegacia) IS NULL"
            params_delegacia = ()
        else:
            filtro_delegacia = " AND (i.no_delegacia = %s OR d.no_delegacia = %s)"
            params_delegacia = (delegacia, delegacia)

    filtro_inq = filtro_acesso + filtro_delegacia
    params_inq = params_acesso + params_delegacia

    with get_connection() as conn:
        with conn.cursor() as cur:
            # qt_inqueritos: total distinto (sem GROUP BY - corrige bug de retornar só maior delegacia)
            cur.execute(
                f"""
                SELECT COUNT(DISTINCT i.id_inquerito)
                FROM investigacao.inq_tb01_inqueritos i
                LEFT JOIN seguranca.usr_tb01_usuarios uc ON uc.id_usuario = i.id_usuario_criador
                LEFT JOIN seguranca.delegacias d ON d.id_delegacia = uc.id_delegacia
                WHERE true{filtro_inq}
                """,
                params_inq,
            )
            qt_inqueritos = cur.fetchone()[0] or 0

            # qt_mensagens_importadas: só UFDR concluído (filtra status)
            cur.execute(
                f"""
                SELECT
                    COALESCE(SUM(CASE WHEN e.cd_status = 'concluido' THEN COALESCE(e.qt_mensagens, 0) ELSE 0 END), 0)
                  + COALESCE(SUM(CASE WHEN e.cd_status = 'concluido' THEN COALESCE(e.qt_sms_mensagens, 0) ELSE 0 END), 0)
                  + COALESCE(SUM(CASE WHEN e.cd_status = 'concluido' THEN COALESCE(e.qt_msg_mensagens, 0) ELSE 0 END), 0)
                FROM investigacao.inq_tb02_extracoes e
                JOIN investigacao.inq_tb01_inqueritos i ON i.id_inquerito = e.id_inquerito
                LEFT JOIN seguranca.usr_tb01_usuarios uc ON uc.id_usuario = i.id_usuario_criador
                LEFT JOIN seguranca.delegacias d ON d.id_delegacia = uc.id_delegacia
                WHERE true{filtro_inq}
                """,
                params_inq,
            )
            qt_mensagens_importadas = cur.fetchone()[0] or 0

            # qt_importacoes_processadas: soma de todos os tipos
            # UFDR concluído
            cur.execute(
                f"""
                SELECT COUNT(*)
                FROM investigacao.inq_tb02_extracoes e
                JOIN investigacao.inq_tb01_inqueritos i ON i.id_inquerito = e.id_inquerito
                LEFT JOIN seguranca.usr_tb01_usuarios uc ON uc.id_usuario = i.id_usuario_criador
                LEFT JOIN seguranca.delegacias d ON d.id_delegacia = uc.id_delegacia
                WHERE e.cd_status = 'concluido' AND true{filtro_inq}
                """,
                params_inq,
            )
            qt_ufdr = cur.fetchone()[0] or 0

            # SIMBA concluído
            cur.execute(
                f"""
                SELECT COUNT(*)
                FROM investigacao.smb_tb01_extratos s
                JOIN investigacao.inq_tb01_inqueritos i ON i.id_inquerito = s.id_inquerito
                LEFT JOIN seguranca.usr_tb01_usuarios uc ON uc.id_usuario = i.id_usuario_criador
                LEFT JOIN seguranca.delegacias d ON d.id_delegacia = uc.id_delegacia
                WHERE s.cd_status = 'concluido' AND true{filtro_inq}
                """,
                params_inq,
            )
            qt_simba = cur.fetchone()[0] or 0

            # IPED concluído
            cur.execute(
                f"""
                SELECT COUNT(*)
                FROM investigacao.ipd_tb01_extracoes p
                JOIN investigacao.inq_tb01_inqueritos i ON i.id_inquerito = p.id_inquerito
                LEFT JOIN seguranca.usr_tb01_usuarios uc ON uc.id_usuario = i.id_usuario_criador
                LEFT JOIN seguranca.delegacias d ON d.id_delegacia = uc.id_delegacia
                WHERE p.cd_status = 'concluido' AND true{filtro_inq}
                """,
                params_inq,
            )
            qt_iped = cur.fetchone()[0] or 0

            # RIF (sem status)
            cur.execute(
                f"""
                SELECT COUNT(*)
                FROM investigacao.rif_tb01_rifs r
                JOIN investigacao.inq_tb01_inqueritos i ON i.id_inquerito = r.id_inquerito
                LEFT JOIN seguranca.usr_tb01_usuarios uc ON uc.id_usuario = i.id_usuario_criador
                LEFT JOIN seguranca.delegacias d ON d.id_delegacia = uc.id_delegacia
                WHERE true{filtro_inq}
                """,
                params_inq,
            )
            qt_rif = cur.fetchone()[0] or 0

            # IPL (sem status)
            cur.execute(
                f"""
                SELECT COUNT(*)
                FROM investigacao.ipl_tb01_inqueritos_pdf p
                JOIN investigacao.inq_tb01_inqueritos i ON i.id_inquerito = p.id_inquerito
                LEFT JOIN seguranca.usr_tb01_usuarios uc ON uc.id_usuario = i.id_usuario_criador
                LEFT JOIN seguranca.delegacias d ON d.id_delegacia = uc.id_delegacia
                WHERE true{filtro_inq}
                """,
                params_inq,
            )
            qt_ipl = cur.fetchone()[0] or 0

            # IRPF (sem status)
            cur.execute(
                f"""
                SELECT COUNT(*)
                FROM investigacao.irp_tb01_declaracoes ir
                JOIN investigacao.inq_tb01_inqueritos i ON i.id_inquerito = ir.id_inquerito
                LEFT JOIN seguranca.usr_tb01_usuarios uc ON uc.id_usuario = i.id_usuario_criador
                LEFT JOIN seguranca.delegacias d ON d.id_delegacia = uc.id_delegacia
                WHERE true{filtro_inq}
                """,
                params_inq,
            )
            qt_irpf = cur.fetchone()[0] or 0

            # IPEI - quebra de sigilo fiscal (sem status)
            cur.execute(
                f"""
                SELECT COUNT(*)
                FROM investigacao.ipe_tb01_ipeis ip
                JOIN investigacao.inq_tb01_inqueritos i ON i.id_inquerito = ip.id_inquerito
                LEFT JOIN seguranca.usr_tb01_usuarios uc ON uc.id_usuario = i.id_usuario_criador
                LEFT JOIN seguranca.delegacias d ON d.id_delegacia = uc.id_delegacia
                WHERE true{filtro_inq}
                """,
                params_inq,
            )
            qt_ipei = cur.fetchone()[0] or 0

            # Documentos avulsos do inquérito
            cur.execute(
                f"""
                SELECT COUNT(*)
                FROM investigacao.inq_tb04_documentos doc
                JOIN investigacao.inq_tb01_inqueritos i ON i.id_inquerito = doc.id_inquerito
                LEFT JOIN seguranca.usr_tb01_usuarios uc ON uc.id_usuario = i.id_usuario_criador
                LEFT JOIN seguranca.delegacias d ON d.id_delegacia = uc.id_delegacia
                WHERE true{filtro_inq}
                """,
                params_inq,
            )
            qt_docs = cur.fetchone()[0] or 0

            qt_importacoes_processadas = (qt_ufdr + qt_simba + qt_iped + qt_rif + qt_ipl
                                          + qt_irpf + qt_ipei + qt_docs)

            # qt_marcadores_realizados
            cur.execute(
                f"""
                SELECT COUNT(*)
                FROM investigacao.mrc_tb02_aplicacoes ap
                JOIN investigacao.mrc_tb01_marcadores mc ON mc.id_marcador = ap.id_marcador
                JOIN investigacao.inq_tb01_inqueritos i ON i.id_inquerito = mc.id_inquerito
                LEFT JOIN seguranca.usr_tb01_usuarios uc ON uc.id_usuario = i.id_usuario_criador
                LEFT JOIN seguranca.delegacias d ON d.id_delegacia = uc.id_delegacia
                WHERE true{filtro_inq}
                """,
                params_inq,
            )
            qt_marcadores_realizados = cur.fetchone()[0] or 0

            # qt_casos: inquéritos marcados como caso de inteligência, no mesmo escopo de acesso
            # e filtro de delegacia dos demais números
            cur.execute(
                f"""
                SELECT COUNT(*)
                FROM investigacao.inq_tb01_inqueritos i
                LEFT JOIN seguranca.usr_tb01_usuarios uc ON uc.id_usuario = i.id_usuario_criador
                LEFT JOIN seguranca.delegacias d ON d.id_delegacia = uc.id_delegacia
                WHERE i.in_caso_inteligencia AND true{filtro_inq}
                """,
                params_inq,
            )
            qt_casos = cur.fetchone()[0] or 0

    return {
        "delegacia": delegacia,
        "qt_inqueritos": int(qt_inqueritos or 0),
        "qt_importacoes_processadas": int(qt_importacoes_processadas or 0),
        "qt_mensagens_importadas": int(qt_mensagens_importadas or 0),
        "qt_marcadores_realizados": int(qt_marcadores_realizados or 0),
        "qt_casos": int(qt_casos or 0),
        "qt_convergencias_abertas": 0,
    }


def obter_conversa_de_mensagem(id_mensagem: int, fonte: str) -> tuple[int, int, int] | None:
    """Resolve (id_conversa, id_inquerito, id_extracao) de uma mensagem, para o deep-link "abrir
    mensagem original" a partir de um achado da IA. Devolve None se a mensagem não existir.

    id_extracao vai junto para o front (js/whatsapp.js) poder adotar o aparelho certo quando o
    link não veio com ?extracao= (o marcador [msg:wpp:ID] não carrega essa informação) — sem
    isso, o painel flutuante de IA (js/ia_flutuante.js) trata a aba "Geral" como "todo o
    inquérito" em vez do aparelho de onde a mensagem citada realmente veio."""
    if fonte not in _TABELAS_CONVERSA:
        return None
    t = _TABELAS_CONVERSA[fonte]
    query = f"""
        SELECT m.id_conversa, e.id_inquerito, c.id_extracao
        FROM investigacao.{t['mensagens']} m
        JOIN investigacao.{t['conversas']} c ON c.id_conversa = m.id_conversa
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = c.id_extracao
        WHERE m.id_mensagem = %s
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (id_mensagem,))
            row = cur.fetchone()
            return (row[0], row[1], row[2]) if row else None


def obter_evidencia(fonte: str, id_prova: int) -> dict | None:
    """Metadados de cadeia de custódia por trás de um marcador da IA — o que o modal de
    informação (js/utils/evidencia_info.js) mostra quando o investigador clica no ℹ️ ao lado
    da citação [msg:wpp|sms|msg:ID] ou [doc:ID].

    fonte 'wpp'|'sms'|'msg': a mensagem aponta (via conversa) para a extração .ufdr de onde
    veio — traz o apelido, o arquivo, o dispositivo e os hashes do .ufdr, além da data/hora e
    do epoch (timestamp) da mensagem. fonte 'doc': o próprio documento anexado — apelido
    (INQ_TB06_APELIDOS, tp_prova='documento'), arquivo e hashes; sem data de mensagem.
    Devolve None se a prova não existir; id_inquerito vai junto para a rota checar acesso."""
    if fonte in _TABELAS_CONVERSA:
        t = _TABELAS_CONVERSA[fonte]
        query = f"""
            SELECT e.id_inquerito, e.no_apelido, e.no_arquivo, e.no_dispositivo,
                   m.dt_mensagem,
                   FLOOR(EXTRACT(EPOCH FROM m.dt_mensagem))::BIGINT AS nu_epoch,
                   -- CHAR(n) é estofado com espaços: sem TRIM, o "copiar hash" do modal
                   -- copiaria padding e a conferência de custódia não bateria
                   TRIM(e.co_hash_md5), TRIM(e.co_hash_sha1), TRIM(e.co_hash_sha256)
            FROM investigacao.{t['mensagens']} m
            JOIN investigacao.{t['conversas']} c ON c.id_conversa = m.id_conversa
            JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = c.id_extracao
            WHERE m.id_mensagem = %s
        """
    elif fonte == "doc":
        query = """
            SELECT d.id_inquerito, ap.no_apelido, d.no_arquivo, NULL::VARCHAR AS no_dispositivo,
                   NULL::TIMESTAMP AS dt_mensagem, NULL::BIGINT AS nu_epoch,
                   TRIM(d.co_hash_md5), TRIM(d.co_hash_sha1), TRIM(d.co_hash_sha256)
            FROM investigacao.inq_tb04_documentos d
            LEFT JOIN investigacao.inq_tb06_apelidos ap
                   ON ap.tp_prova = 'documento' AND ap.id_prova = d.id_documento
            WHERE d.id_documento = %s
        """
    else:
        return None
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (id_prova,))
            row = cur.fetchone()
    if not row:
        return None
    return {
        "tp_evidencia": "mensagem" if fonte != "doc" else "documento",
        "fonte": fonte,
        "id_prova": id_prova,
        "id_inquerito": row[0],
        "no_apelido": row[1],
        "no_arquivo": row[2],
        "no_dispositivo": row[3],
        "dt_mensagem": row[4],
        "nu_epoch": row[5],
        "co_hash_md5": row[6],
        "co_hash_sha1": row[7],
        "co_hash_sha256": row[8],
    }


def obter_contexto_investigacao(id_inquerito: int) -> str | None:
    """Texto de contexto livre da investigação (preenchido pelo investigador), ou None."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT tx_contexto_investigacao FROM investigacao.inq_tb01_inqueritos WHERE id_inquerito = %s",
                (id_inquerito,),
            )
            row = cur.fetchone()
            return row[0] if row else None


def renomear_inquerito(id_inquerito: int, novo_numero: str) -> bool:
    """Altera o número (NU_INQUERITO) de um inquérito. Devolve False se o inquérito não existir;
    levanta ValueError se o novo número já pertencer a outro inquérito (NU_INQUERITO é único)."""
    novo_numero = (novo_numero or "").strip()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id_inquerito FROM investigacao.inq_tb01_inqueritos WHERE nu_inquerito = %s",
                (novo_numero,),
            )
            existente = cur.fetchone()
            if existente is not None and existente[0] != id_inquerito:
                raise ValueError("Já existe um inquérito com esse número.")
            cur.execute(
                "UPDATE investigacao.inq_tb01_inqueritos SET nu_inquerito = %s WHERE id_inquerito = %s",
                (novo_numero, id_inquerito),
            )
            renomeado = cur.rowcount > 0
        conn.commit()
    return renomeado


def atualizar_delegacia_inquerito(id_inquerito: int, no_delegacia: str | None) -> bool:
    """Atualiza a delegacia (NO_DELEGACIA) de um inquérito. Devolve False se não existir."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE investigacao.inq_tb01_inqueritos SET no_delegacia = %s WHERE id_inquerito = %s",
                ((no_delegacia or "").strip() or None, id_inquerito),
            )
            atualizado = cur.rowcount > 0
        conn.commit()
    return atualizado


def atualizar_situacao_inquerito(id_inquerito: int, situacao: str) -> bool:
    """Grava a situação do inquérito ('ativo' | 'arquivado'). Devolve False se não existir.

    A situação é do inquérito em si, não do processamento: um caso arquivado continua íntegro e
    navegável, só sai do foco da listagem. Só o gestor muda (ver rota PUT .../situacao)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE investigacao.inq_tb01_inqueritos SET tp_situacao = %s WHERE id_inquerito = %s",
                (situacao, id_inquerito),
            )
            atualizado = cur.rowcount > 0
        conn.commit()
    return atualizado


def salvar_contexto_investigacao(id_inquerito: int, texto: str | None) -> None:
    """Grava (ou limpa) o texto de contexto da investigação de um inquérito."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE investigacao.inq_tb01_inqueritos SET tx_contexto_investigacao = %s WHERE id_inquerito = %s",
                ((texto or "").strip() or None, id_inquerito),
            )
        conn.commit()


def atualizar_apelido_extracao(id_extracao: int, apelido: str | None) -> bool:
    """Grava (ou limpa) o apelido livre de uma extração (.ufdr).

    Só identificação no card de investigacao.html — não altera arquivo, escopo de IA nem
    achados. Devolve False se a extração não existir."""
    apelido_limpo = (apelido or "").strip() or None
    if apelido_limpo is not None and len(apelido_limpo) > 200:
        apelido_limpo = apelido_limpo[:200]
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE investigacao.inq_tb02_extracoes SET no_apelido = %s WHERE id_extracao = %s",
                (apelido_limpo, id_extracao),
            )
            atualizado = cur.rowcount > 0
        conn.commit()
    return atualizado


def atualizar_apelido_prova(tp_prova: str, id_prova: int, apelido: str | None,
                            id_usuario: int | None = None) -> bool:
    """Grava (ou limpa, com vazio) o apelido livre de qualquer prova do card de investigação —
    rif, simba, documento, iped, ipl, irpf, ipei, telefonia (a extração .ufdr tem coluna própria,
    ver atualizar_apelido_extracao). Upsert pelo par (tp_prova, id_prova) de INQ_TB06_APELIDOS.

    Só identificação no card — não altera arquivo, escopo de IA nem achados. A existência da
    prova é checada na rota (via obter_id_inquerito_de_*), então aqui o upsert sempre dá."""
    apelido_limpo = (apelido or "").strip() or None
    if apelido_limpo is not None and len(apelido_limpo) > 200:
        apelido_limpo = apelido_limpo[:200]
    with get_connection() as conn:
        with conn.cursor() as cur:
            if apelido_limpo is None:
                cur.execute(
                    "DELETE FROM investigacao.inq_tb06_apelidos WHERE tp_prova = %s AND id_prova = %s",
                    (tp_prova, id_prova),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO investigacao.inq_tb06_apelidos
                        (tp_prova, id_prova, no_apelido, id_usuario, dt_atualizacao)
                    VALUES (%s, %s, %s, %s, now())
                    ON CONFLICT (tp_prova, id_prova)
                    DO UPDATE SET no_apelido = EXCLUDED.no_apelido,
                                  id_usuario = EXCLUDED.id_usuario,
                                  dt_atualizacao = now()
                    """,
                    (tp_prova, id_prova, apelido_limpo, id_usuario),
                )
        conn.commit()
    return True


# Tabelas de conversa por fonte — WhatsApp e SMS têm o mesmo formato (ver 07/08_investigacao*.sql),
# então as duas próximas funções são genéricas e só trocam de tabela conforme a fonte.
# Pasta dentro do .ufdr -> o que o binário daquela pasta é. O Cellebrite separa o material
# extraído por tipo REAL do arquivo, e essa é a única pista quando o membro do zip vem sem
# extensão nenhuma (é o caso da miniatura de vídeo do WhatsApp: "files/Image/3ABB239E97…").
_PASTA_ZIP_CATEGORIA = {
    "image": "imagem", "video": "video", "audio": "audio",
    "document": "documento", "archives": "documento", "text": "documento",
}

# Extensões que o Cellebrite usa para a miniatura que ele gera do vídeo/PDF/link
_EXT_MINIATURA = (".thumb",)


def categoria_binario_anexo(caminho_zip: str | None) -> str | None:
    """O que o binário extraído REALMENTE é: 'imagem', 'video', 'audio', 'documento' ou None.

    NÃO usa o TP_CONTEUDO do anexo, e é justamente esse o ponto. O tipo declarado descreve a
    mídia ORIGINAL, não o arquivo que veio na extração: um vídeo do WhatsApp cujo binário não
    foi recuperado chega como `video/mp4` com uma miniatura JPEG de 1 KB no lugar (10.681
    anexos só num aparelho auditado; com previews de link e de PDF passa de 66 mil). Servido
    como vídeo, o player abria vazio — "Não há nenhum vídeo com formato ou tipo MIME
    suportados" — e a miniatura, que é o que o investigador tem para ver, ficava inacessível.

    Decide pela extensão do membro do zip; quando não há extensão, pela pasta em que o
    Cellebrite o guardou. Exceção: miniatura de vídeo que o Cellebrite salva como
    `files/Image/<GUID>.mp4` — extensão diz vídeo, mas o conteúdo é JPEG de 300-700 bytes
    (327 casos numa extração auditada, todos JPEG). A pasta `Image` diz a verdade aqui,
    então ela ganha da extensão. None quando não há binário localizado."""
    if not caminho_zip:
        return None
    nome = str(caminho_zip)
    ext = nome.rsplit("/", 1)[-1].rsplit(".", 1)
    ext = "." + ext[-1].lower() if len(ext) > 1 else ""
    if ext in _EXT_MINIATURA:
        return "imagem"
    categoria = categoria_de_extensao(nome)
    if categoria == "Imagem":
        return "imagem"
    # miniatura JPEG com nome de vídeo em files/Image/: o Cellebrite guarda o preview do
    # vídeo não recuperado com a extensão original (.mp4), mas na pasta de imagens e com
    # conteúdo JPEG — verificado por magic bytes FF D8 FF em todos os 327 casos auditados.
    # Sem isso o front montava um player <video> para um JPEG e o navegador recusava.
    if categoria == "Vídeo":
        partes_pasta = nome.replace("\\", "/").split("/")
        if len(partes_pasta) >= 2 and partes_pasta[-2].lower() == "image":
            return "imagem"
        return "video"
    if categoria == "Áudio":
        return "audio"
    if categoria in ("Documento", "Compactado"):
        return "documento"
    partes = nome.replace("\\", "/").split("/")
    if len(partes) >= 2:
        return _PASTA_ZIP_CATEGORIA.get(partes[-2].lower())
    return None


def _classificar_anexos(mensagens: list[dict]) -> None:
    """Acerta, IN PLACE, os flags de cada anexo pelo binário que existe de fato.

    Roda em Python (e não no SQL do json_agg) para que a regra de categoria_binario_anexo
    valha igual aqui e nas rotas que servem o binário — antes cada lado decidia por
    `tp_conteudo LIKE 'image/%'` e os dois erravam junto. O caminho dentro do zip é usado
    aqui e removido do payload: é detalhe interno da extração, a tela não precisa dele."""
    for msg in mensagens:
        citada = msg.get("citada") or {}
        for anexo in list(msg.get("anexos") or ()) + ([citada["anexo"]] if citada.get("anexo") else []):
            caminho = anexo.pop("caminho_zip", None)
            if caminho and caminho.startswith("IPED::"):
                # ponteiro da Nuvem IPED não tem extensão: o binário existe no storage do caso e
                # o tipo declarado (tp_conteudo do item de mídia) é confiável — ao contrário do
                # .ufdr, em que o tipo declarado pode ser a mídia original com uma miniatura no
                # lugar. Decide pelo MIME declarado.
                tipo = (anexo.get("tipo") or "").lower()
                real = ("imagem" if tipo.startswith("image/")
                        else "audio" if tipo.startswith("audio/")
                        else "video" if tipo.startswith("video/")
                        else "documento" if tipo.startswith(("application/", "text/")) else None)
            else:
                real = categoria_binario_anexo(caminho)
            anexo["categoria_binario"] = real
            anexo["imagem_disponivel"] = real == "imagem"
            anexo["audio_disponivel"] = real == "audio"
            # o binário existe, mas é só a miniatura de uma mídia que não veio na extração
            anexo["so_miniatura"] = bool(
                real == "imagem" and not (anexo.get("tipo") or "").lower().startswith("image/"))


_TABELAS_CONVERSA = {
    "wpp": {
        "contatos": "wpp_tb01_contatos", "conversas": "wpp_tb02_conversas",
        "participantes": "wpp_tb03_participantes", "mensagens": "wpp_tb04_mensagens",
        "anexos": "wpp_tb05_anexos", "coluna_identificador": "nu_whatsapp_id",
    },
    "sms": {
        "contatos": "sms_tb01_contatos", "conversas": "sms_tb02_conversas",
        "participantes": "sms_tb03_participantes", "mensagens": "sms_tb04_mensagens",
        "anexos": "sms_tb05_anexos", "coluna_identificador": "nu_telefone",
    },
    # mensageiros diversos (Instagram, Telegram, …) compartilham as mesmas tabelas MSG_TB0x;
    # o app específico é passado à parte (ver o parâmetro `app`) e filtra só a listagem de conversas.
    "msg": {
        "contatos": "msg_tb01_contatos", "conversas": "msg_tb02_conversas",
        "participantes": "msg_tb03_participantes", "mensagens": "msg_tb04_mensagens",
        "anexos": "msg_tb05_anexos", "coluna_identificador": "nu_identificador", "tem_app": True,
    },
}


# Classifica pelo JID que o Cellebrite gravou — é o que separa conversa de verdade das transmissões
# de mão única, que o WhatsApp guarda como se fossem chat e por isso apareciam na lista sem nada que
# as distinguisse de uma conversa com o contato:
#   'Status: <jid>' · '<jid>@status' · '<jid>@lid.status' · 'status@broadcast'
#        -> Atualizações de Status (Stories): registro de visualização, não é conversa
#   '<id>@newsletter'  -> Canal do WhatsApp (CNN Brasil, g1, GloboNews…), conteúdo de mão única
#   '<id>@broadcast'   -> Lista de transmissão
#   '<id>@g.us'        -> grupo
# O TP_CONVERSA da tabela entra só como queda para grupo: ele marca 'grupo' em conversa cujo JID é
# @s.whatsapp.net, então não serve para decidir os casos acima. O WhatsApp Business chega como
# mensageiro (tabelas MSG_TB0x), mas o Cellebrite grava os mesmos JIDs — a classificação vale para
# as duas fontes, mudando só o nome da coluna do chat id. SMS não tem JID: sobra grupo/conversa.
def _expr_tp_lista(fonte: str) -> str:
    col = ("c.nu_whatsapp_chat_id" if fonte == "wpp"
           else "c.nu_chat_id" if fonte == "msg" else None)
    if not col:
        return "CASE WHEN c.tp_conversa = 'grupo' THEN 'grupo' ELSE 'conversa' END"
    return f"""CASE
              WHEN {col} LIKE 'Status:%%'
                OR {col} LIKE '%%@status'
                OR {col} LIKE '%%.status'
                OR {col} = 'status@broadcast'  THEN 'status'
              WHEN {col} LIKE '%%@newsletter'   THEN 'canal'
              WHEN {col} LIKE '%%@broadcast'    THEN 'transmissao'
              WHEN {col} LIKE '%%@g.us'         THEN 'grupo'
              WHEN c.tp_conversa = 'grupo'      THEN 'grupo'
              ELSE 'conversa'
            END"""


# Aba da tela -> tipos que ela agrupa. "difusao" junta os três de mão única (Status, canal, lista
# de transmissão); separá-los encheria a barra sem ajudar. "conversa" e "grupo" são literais.
_TIPOS_DIFUSAO = ("status", "canal", "transmissao")


def listar_conversas(
    id_inquerito: int, fonte: str, id_extracao: int | None = None, app: str | None = None, *,
    busca: str | None = None, tipo: str = "todos", offset: int = 0, limite: int = 1000,
    conversa_id: int | None = None,
) -> dict:
    """Uma PÁGINA das conversas de um inquérito, com quem é o outro lado, mais as contagens por aba.

    fonte: 'wpp', 'sms' ou 'msg' (mensageiros diversos — nesse caso `app` filtra o mensageiro).
    id_extracao opcional restringe a uma única extração (um aparelho).

    Paginado no servidor (não devolve o caso inteiro — um aparelho com 13 mil conversas virava um
    JSON de megabytes, 5 s para abrir e travava com muita gente simultânea). Devolve
    {conversas (até `limite`), total, contagens {todos,conversa,grupo,difusao}, offset, limite,
    tem_mais}. `busca` (nome do grupo, nome do contato ou dígitos do número) e `tipo`
    ('todos'|'conversa'|'grupo'|'difusao') filtram no banco, então as abas e a busca continuam
    corretos mesmo com só uma página carregada. As `contagens` respeitam a `busca` mas ignoram o
    `tipo` — é o que faz cada aba mostrar o total daquela categoria, não só da página.

    conversa_id: atalho para o deep-link de mensagem — traz só aquela conversa (mesmo formato),
    ignorando busca/tipo/paginação, para a tela conseguir abrir uma conversa fora da página 1."""
    t = _TABELAS_CONVERSA[fonte]
    tp_expr = _expr_tp_lista(fonte)
    tipo = tipo if tipo in ("todos", "conversa", "grupo", "difusao") else "todos"
    limite = max(1, min(int(limite or 1000), 2000))   # teto de segurança
    offset = max(0, int(offset or 0))

    # filtros que valem para a página E para as contagens (inquérito + aparelho + app + busca).
    where = ["e.id_inquerito = %(id_inquerito)s",
             "(%(id_extracao)s::bigint IS NULL OR c.id_extracao = %(id_extracao)s)"]
    params: dict = {"id_inquerito": id_inquerito, "id_extracao": id_extracao}
    if app and t.get("tem_app"):
        where.append("c.tp_app = %(app)s")
        params["app"] = app

    termo = (busca or "").strip()
    if termo:
        col_id = t["coluna_identificador"]
        digitos = "".join(ch for ch in termo if ch.isdigit())
        pred_part = f"(ct.no_contato ILIKE %(q)s OR ct.{col_id} ILIKE %(q)s"
        if len(digitos) >= 4:
            # o identificador vem formatado (JID, "+55 27 99872-1615"): casa dígitos com dígitos
            pred_part += f" OR regexp_replace(ct.{col_id}, '\\D', '', 'g') LIKE %(digitos)s"
            params["digitos"] = digitos
        pred_part += ")"
        where.append(
            f"(c.no_grupo ILIKE %(q)s OR EXISTS (SELECT 1 FROM investigacao.{t['participantes']} p "
            f"JOIN investigacao.{t['contatos']} ct ON ct.id_contato = p.id_contato "
            f"WHERE p.id_conversa = c.id_conversa AND {pred_part}))"
        )
        params["q"] = f"%{termo}%"

    where_base = " AND ".join(where)

    # deep-link: só esta conversa; ignora tipo (e a busca, se vier, já está em `where`).
    if conversa_id is not None:
        where_base += " AND c.id_conversa = %(conversa_id)s"
        params["conversa_id"] = conversa_id

    with get_connection() as conn:
        with conn.cursor() as cur:
            # contagens por categoria: base + busca, SEM o filtro de aba — é o número de cada aba.
            cur.execute(
                f"""
                SELECT {tp_expr} AS tp, count(*) AS qt
                FROM investigacao.{t['conversas']} c
                JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = c.id_extracao
                WHERE {where_base}
                GROUP BY 1
                """,
                params,
            )
            bruto = dict(cur.fetchall())
            contagens = {
                "todos": sum(bruto.values()),
                "conversa": bruto.get("conversa", 0),
                "grupo": bruto.get("grupo", 0),
                "difusao": sum(bruto.get(tp, 0) for tp in _TIPOS_DIFUSAO),
            }
            if tipo == "conversa":
                total = contagens["conversa"]
            elif tipo == "grupo":
                total = contagens["grupo"]
            elif tipo == "difusao":
                total = contagens["difusao"]
            else:
                total = contagens["todos"]

            # filtro de aba (só na página; as contagens acima já saíram sem ele). No deep-link de
            # conversa (conversa_id) não filtra por aba: a conversa alvo tem de abrir sozinha,
            # qualquer que seja a aba ativa.
            where_pagina = where_base
            if conversa_id is None:
                if tipo == "conversa":
                    where_pagina += f" AND ({tp_expr}) = 'conversa'"
                elif tipo == "grupo":
                    where_pagina += f" AND ({tp_expr}) = 'grupo'"
                elif tipo == "difusao":
                    where_pagina += f" AND ({tp_expr}) IN ('status', 'canal', 'transmissao')"

            # Nome E identificador dos participantes saem do MESMO lateral (a tela mostra os dois);
            # a última mensagem é um top-1 pelo índice (id_conversa, dt_mensagem DESC). O LIMIT/
            # OFFSET garante que esse trabalho por conversa roda no máximo para `limite` linhas —
            # antes corria para todas as conversas do aparelho, frio, e era o que dava os 5 s.
            cur.execute(
                f"""
                SELECT c.id_conversa, c.no_grupo, c.tp_conversa, c.dt_ultima_atividade,
                       {tp_expr} AS tp_lista,
                       pa.participantes,
                       pa.identificadores,
                       (SELECT m.tx_mensagem FROM investigacao.{t['mensagens']} m
                         WHERE m.id_conversa = c.id_conversa ORDER BY m.dt_mensagem DESC NULLS LAST LIMIT 1
                       ) AS ultima_mensagem
                FROM investigacao.{t['conversas']} c
                JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = c.id_extracao
                LEFT JOIN LATERAL (
                    SELECT string_agg(DISTINCT ct.no_contato, ', ') AS participantes,
                           string_agg(DISTINCT ct.{t['coluna_identificador']}, '|') AS identificadores
                      FROM investigacao.{t['participantes']} p
                      JOIN investigacao.{t['contatos']} ct ON ct.id_contato = p.id_contato
                     WHERE p.id_conversa = c.id_conversa AND NOT coalesce(ct.in_dono_dispositivo, false)
                ) pa ON true
                WHERE {where_pagina}
                ORDER BY c.dt_ultima_atividade DESC NULLS LAST
                LIMIT %(limite)s OFFSET %(offset)s
                """,
                {**params, "limite": limite, "offset": offset},
            )
            colunas = [d.name for d in cur.description]
            conversas = [dict(zip(colunas, linha)) for linha in cur.fetchall()]

    # eh_status continua no payload porque a tela usa em mais de um lugar; sai derivado de tp_lista
    for c in conversas:
        c["eh_status"] = c["tp_lista"] == "status"

    return {
        "conversas": conversas,
        "total": total,
        "contagens": contagens,
        "offset": offset,
        "limite": limite,
        "tem_mais": offset + len(conversas) < total,
    }


def listar_apps_mensageiros(id_inquerito: int, id_extracao: int | None = None) -> list[dict]:
    """Lista os mensageiros diversos (Instagram, Telegram, …) presentes, com contagem de conversas e
    mensagens por app — usado para renderizar um botão por app no card da extração."""
    query = """
        SELECT c.tp_app,
               count(DISTINCT c.id_conversa) AS qt_conversas,
               count(m.id_mensagem) AS qt_mensagens
        FROM investigacao.msg_tb02_conversas c
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = c.id_extracao
        LEFT JOIN investigacao.msg_tb04_mensagens m ON m.id_conversa = c.id_conversa
        WHERE e.id_inquerito = %(id_inquerito)s
          AND (%(id_extracao)s::bigint IS NULL OR c.id_extracao = %(id_extracao)s)
        GROUP BY c.tp_app
        ORDER BY qt_mensagens DESC, c.tp_app
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, {"id_inquerito": id_inquerito, "id_extracao": id_extracao})
            colunas = [d.name for d in cur.description]
            return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


# Classificação da conversa CITADA, para a bolha dizer de onde veio a mensagem de referência.
# Mesmos JIDs de listar_conversas — o caso que aparece na prática é a resposta a um Status
# (Stories), que o Cellebrite guarda num pseudo-chat separado da conversa real.
_TIPO_CONVERSA_CITADA = """
    CASE
      WHEN cq.nu_whatsapp_chat_id LIKE 'Status:%%'
        OR cq.nu_whatsapp_chat_id LIKE '%%@status'
        OR cq.nu_whatsapp_chat_id LIKE '%%.status'
        OR cq.nu_whatsapp_chat_id = 'status@broadcast'  THEN 'status'
      WHEN cq.nu_whatsapp_chat_id LIKE '%%@newsletter'   THEN 'canal'
      WHEN cq.nu_whatsapp_chat_id LIKE '%%@broadcast'    THEN 'transmissao'
      WHEN cq.nu_whatsapp_chat_id LIKE '%%@g.us'         THEN 'grupo'
      WHEN cq.tp_conversa = 'grupo'                      THEN 'grupo'
      ELSE 'conversa'
    END
"""


def _sql_mensagem_citada(t: dict, fonte: str) -> str:
    """Subconsulta que resolve a mensagem de REFERÊNCIA de uma resposta.

    Responder citando outra mensagem grava em NU_IDENT_CITADO o Identifier da original
    (QuotedMessageData → OriginalMessageID); a mensagem citada é outra linha da mesma
    extração, reencontrada por NU_IDENTIFICADOR_ORIGINAL. Sem isto a bolha aparecia solta,
    sem o trecho citado que o IPED e o próprio WhatsApp mostram em cima da resposta.

    A citada NEM SEMPRE está na conversa aberta: responder a um Status cita uma mensagem do
    pseudo-chat de Status daquele contato. Por isso a busca é por extração (e não por
    conversa), com desempate preferindo a mesma conversa — o Identifier é praticamente único
    dentro de uma extração (271 repetidos em 1,3 milhão), mas "praticamente" não é "sempre".

    Vem como um único json_build_object para não multiplicar linhas nem exigir outro
    roundtrip: o front desenha a citação direto do que chega aqui."""
    tipo_conversa = _TIPO_CONVERSA_CITADA if fonte == "wpp" else "'conversa'"
    nome_conversa = "cq.nu_whatsapp_chat_id" if fonte == "wpp" else "cq.no_grupo"
    return f"""
        (SELECT json_build_object(
                    'id_mensagem', q.id_mensagem,
                    'id_conversa', q.id_conversa,
                    'remetente', ctq.no_contato,
                    'enviada', q.in_enviada,
                    'texto', q.tx_mensagem,
                    'dt_mensagem', q.dt_mensagem,
                    'apagada', q.in_apagada_remetente,
                    'tem_coordenada', (q.vl_latitude IS NOT NULL),
                    'outra_conversa', (q.id_conversa <> m.id_conversa),
                    'tp_conversa', {tipo_conversa},
                    'conversa_nome', coalesce(cq.no_grupo, {nome_conversa}),
                    'anexo', (SELECT json_build_object('titulo', aq.tx_titulo, 'nome', aq.no_arquivo,
                                                       'tipo', aq.tp_conteudo, 'id', aq.id_anexo,
                                                       'caminho_zip', aq.co_caminho_zip,
                                                       'transcricao', aq.tx_transcricao)
                                FROM investigacao.{t['anexos']} aq
                               WHERE aq.id_mensagem = q.id_mensagem
                               ORDER BY aq.id_anexo LIMIT 1)
                )
           FROM investigacao.{t['mensagens']} q
           JOIN investigacao.{t['conversas']} cq ON cq.id_conversa = q.id_conversa
           LEFT JOIN investigacao.{t['contatos']} ctq ON ctq.id_contato = q.id_contato_remetente
          WHERE m.nu_ident_citado IS NOT NULL AND m.nu_ident_citado <> ''
            AND cq.id_extracao = %(extracao)s
            AND q.nu_identificador_original = m.nu_ident_citado
          ORDER BY (q.id_conversa = m.id_conversa) DESC, q.id_mensagem
          LIMIT 1)"""


def listar_mensagens_conversa(
    id_conversa: int, fonte: str, limite: int = 200,
    antes_id: int | None = None, depois_id: int | None = None, centro_id: int | None = None,
    tipo: str | None = None,
) -> dict:
    """Devolve uma JANELA de mensagens de uma conversa, paginada — o essencial para grupos/conversas
    grandes (dezenas de milhares de mensagens) não travarem o navegador.

    Ordena por id_mensagem (= ordem de inserção do UFDR, cronológica e sem nulos — ver checagem no
    banco). Modos, mutuamente exclusivos:
      - sem cursor: as `limite` mensagens MAIS RECENTES (fim da conversa);
      - antes_id:   as `limite` anteriores a esse id (rolar para cima / carregar mais antigas);
      - depois_id:  as `limite` posteriores a esse id (carregar mais novas);
      - centro_id:  uma janela em torno desse id (deep-link de um achado da IA).
    `tipo` filtra o conteúdo: 'texto' (bolhas só de texto, sem anexo) ou 'midia' (bolhas com
    anexo). O filtro participa de TODAS as janelas e dos flags tem_anteriores/tem_posteriores,
    para a paginação continuar coerente com ele.
    Retorna {mensagens: [...] (ordem cronológica), tem_anteriores, tem_posteriores}."""
    t = _TABELAS_CONVERSA[fonte]
    limite = max(1, min(int(limite or 200), 500))  # teto de segurança
    # Filtro de conteúdo, aplicado tanto na janela quanto nos EXISTS de tem_anteriores/
    # tem_posteriores. Usa o alias `m` (mensagens) — o mesmo das duas consultas.
    # 'texto' exclui bolhas com anexo (a legenda da mídia vai junto da mídia, em 'midia').
    filtro_conteudo = ""
    if tipo == "texto":
        filtro_conteudo = (
            f"AND (m.tx_mensagem IS NOT NULL AND m.tx_mensagem <> '')"
            f" AND NOT EXISTS (SELECT 1 FROM investigacao.{t['anexos']} a WHERE a.id_mensagem = m.id_mensagem)"
        )
    elif tipo == "midia":
        filtro_conteudo = (
            f"AND EXISTS (SELECT 1 FROM investigacao.{t['anexos']} a WHERE a.id_mensagem = m.id_mensagem)"
        )
    cols = f"""
        m.id_mensagem, m.tx_mensagem, m.dt_mensagem, m.in_enviada, m.cd_status_mensagem,
        m.vl_latitude, m.vl_longitude, m.in_encaminhada, m.nu_ident_citado,
        {_sql_mensagem_citada(t, fonte)} AS citada,
        -- assunto e motivo de exclusao vao para a bolha SO quando nao ha texto/anexo/
        -- coordenada: sem eles a tela mostrava "(sem conteudo)" em mensagem que o UFDR
        -- descreve (Subject do Facebook/Messenger, DeletionReason de apagada).
        m.tx_assunto, m.tx_motivo_exclusao, m.in_apagada_remetente,
        -- rótulo SelfDestruct: com anexo SEM binário, é a mídia de "visualização única"
        -- que o WhatsApp apaga do aparelho assim que é aberta (ver a bolha em whatsapp.js)
        m.in_temporaria,
        ct.no_contato AS remetente, ct.{t['coluna_identificador']} AS remetente_identificador,
        (SELECT json_agg(json_build_object(
                     'id', a.id_anexo, 'nome', a.no_arquivo, 'tipo', a.tp_conteudo,
                     'titulo', a.tx_titulo,
                     'transcricao', a.tx_transcricao,
                     'disponivel', (a.co_caminho_zip IS NOT NULL),
                     -- o caminho é consumido e REMOVIDO por _classificar_anexos, que decide
                     -- imagem/audio/vídeo pelo binário que existe de fato (ver o porquê lá)
                     'caminho_zip', a.co_caminho_zip,
                     'eh_figurinha', (a.tp_conteudo = 'image/webp')
                 ))
           FROM investigacao.{t['anexos']} a WHERE a.id_mensagem = m.id_mensagem
        ) AS anexos"""
    base = f"""
        FROM investigacao.{t['mensagens']} m
        LEFT JOIN investigacao.{t['contatos']} ct ON ct.id_contato = m.id_contato_remetente
        WHERE m.id_conversa = %(conv)s {filtro_conteudo}"""

    def _buscar(cur, where_extra: str, direcao: str, lim: int, extra_params: dict) -> list[dict]:
        # o json_agg dos anexos só roda para as `lim` linhas da página (antes rodava para TODAS)
        cur.execute(
            f"SELECT {cols} {base} {where_extra} ORDER BY m.id_mensagem {direcao} LIMIT %(lim)s",
            {"conv": id_conversa, "lim": lim, "extracao": id_extracao, **extra_params},
        )
        colunas = [d.name for d in cur.description]
        return [dict(zip(colunas, linha)) for linha in cur.fetchall()]

    with get_connection() as conn:
        with conn.cursor() as cur:
            # a mensagem citada é procurada na EXTRAÇÃO (ver _sql_mensagem_citada), então a
            # janela precisa saber de qual aparelho esta conversa veio
            cur.execute(
                f"SELECT id_extracao FROM investigacao.{t['conversas']} WHERE id_conversa = %s",
                (id_conversa,),
            )
            linha_extracao = cur.fetchone()
            id_extracao = linha_extracao[0] if linha_extracao else None
            if centro_id is not None:
                metade = limite // 2
                antigas = _buscar(cur, "AND m.id_mensagem <= %(c)s", "DESC", metade + (limite % 2), {"c": centro_id})
                novas = _buscar(cur, "AND m.id_mensagem > %(c)s", "ASC", metade, {"c": centro_id})
                mensagens = list(reversed(antigas)) + novas
            elif antes_id is not None:
                mensagens = list(reversed(_buscar(cur, "AND m.id_mensagem < %(a)s", "DESC", limite, {"a": antes_id})))
            elif depois_id is not None:
                mensagens = _buscar(cur, "AND m.id_mensagem > %(d)s", "ASC", limite, {"d": depois_id})
            else:
                mensagens = list(reversed(_buscar(cur, "", "DESC", limite, {})))

            tem_anteriores = tem_posteriores = False
            if mensagens:
                cur.execute(
                    f"SELECT EXISTS(SELECT 1 FROM investigacao.{t['mensagens']} m WHERE m.id_conversa = %(conv)s AND m.id_mensagem < %(mn)s {filtro_conteudo}),"
                    f"       EXISTS(SELECT 1 FROM investigacao.{t['mensagens']} m WHERE m.id_conversa = %(conv)s AND m.id_mensagem > %(mx)s {filtro_conteudo})",
                    {"conv": id_conversa, "mn": mensagens[0]["id_mensagem"], "mx": mensagens[-1]["id_mensagem"]},
                )
                tem_anteriores, tem_posteriores = cur.fetchone()

    _classificar_anexos(mensagens)
    return {"mensagens": mensagens, "tem_anteriores": tem_anteriores, "tem_posteriores": tem_posteriores}


def buscar_mensagens_conversa(id_conversa: int, fonte: str, termo: str, limite: int = 30) -> list[dict]:
    """Localiza mensagens em todo o histórico de uma conversa, inclusive transcrições de anexo.

    Devolve apenas uma prévia leve; a tela usa o id da mensagem para abrir a janela paginada
    centrada no resultado, sem carregar um grupo inteiro no navegador."""
    texto = (termo or "").strip()
    if not texto:
        return []
    t = _TABELAS_CONVERSA[fonte]
    limite = max(1, min(int(limite or 30), 50))
    padrao = f"%{texto}%"
    query = f"""
        SELECT m.id_mensagem, m.tx_mensagem, m.dt_mensagem, ct.no_contato AS remetente,
               (SELECT string_agg(a.tx_transcricao, ' ')
                  FROM investigacao.{t['anexos']} a
                 WHERE a.id_mensagem = m.id_mensagem AND a.tx_transcricao IS NOT NULL) AS transcricao
        FROM investigacao.{t['mensagens']} m
        LEFT JOIN investigacao.{t['contatos']} ct ON ct.id_contato = m.id_contato_remetente
        WHERE m.id_conversa = %(conv)s
          AND (m.tx_mensagem ILIKE %(padrao)s OR EXISTS (
              SELECT 1 FROM investigacao.{t['anexos']} a
               WHERE a.id_mensagem = m.id_mensagem AND a.tx_transcricao ILIKE %(padrao)s
          ))
        ORDER BY m.dt_mensagem DESC NULLS LAST, m.id_mensagem DESC
        LIMIT %(lim)s
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, {"conv": id_conversa, "padrao": padrao, "lim": limite})
            colunas = [d.name for d in cur.description]
            return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


def obter_binario_anexo(id_anexo: int, fonte: str) -> dict | None:
    """Resolve tipo/caminho-dentro-do-zip/arquivo .ufdr/inquérito de um anexo, para servir o
    binário (imagem) sob demanda direto do .ufdr original, sem duplicá-lo no banco.
    None se o anexo não existir."""
    t = _TABELAS_CONVERSA[fonte]
    query = f"""
        SELECT a.tp_conteudo, a.co_caminho_zip, a.no_arquivo, e.no_arquivo, e.id_inquerito
        FROM investigacao.{t['anexos']} a
        JOIN investigacao.{t['mensagens']} m ON m.id_mensagem = a.id_mensagem
        JOIN investigacao.{t['conversas']} c ON c.id_conversa = m.id_conversa
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = c.id_extracao
        WHERE a.id_anexo = %s
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (id_anexo,))
            row = cur.fetchone()
    if row is None:
        return None
    tp_conteudo, caminho_zip, no_anexo, no_arquivo, id_inquerito = row
    return {
        "tp_conteudo": tp_conteudo,
        "caminho_zip": caminho_zip,
        "no_anexo": no_anexo,
        "no_arquivo_ufdr": no_arquivo,
        "id_inquerito": id_inquerito,
    }


def atualizar_transcricao_anexo(id_anexo: int, fonte: str, texto: str | None) -> bool:
    """Corrige manualmente TX_TRANSCRICAO de um anexo de áudio/vídeo — revisão humana do texto
    que o faster-whisper gerou na extração (ver docs/transcricao_audio.md), quando o investigador
    percebe que ele não bate com o que foi dito. Devolve False se o anexo não existir."""
    t = _TABELAS_CONVERSA[fonte]
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE investigacao.{t['anexos']} SET tx_transcricao = %s WHERE id_anexo = %s",
                (texto, id_anexo),
            )
            atualizado = cur.rowcount > 0
        conn.commit()
    return atualizado


_CATEGORIAS_ARQUIVO = {"Imagem", "Vídeo", "Áudio", "Documento", "Compactado", "Outro",
                       "Metadado"}


# ordenações permitidas na listagem de arquivos (whitelist — o valor cru nunca entra no SQL)
_ORDENACOES_ARQUIVO = {
    "categoria": "a.no_categoria, a.no_arquivo NULLS LAST, a.id_arquivo",
    "nome": "a.no_arquivo NULLS LAST, a.id_arquivo",
    "nome_desc": "a.no_arquivo DESC NULLS LAST, a.id_arquivo",
    "maiores": "a.qt_bytes DESC NULLS LAST, a.id_arquivo",
    "menores": "a.qt_bytes ASC NULLS LAST, a.id_arquivo",
    "recentes": "a.dt_modificacao DESC NULLS LAST, a.id_arquivo",
    "antigos": "a.dt_modificacao ASC NULLS LAST, a.id_arquivo",
}
_RE_DATA_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# faixa de horário (hora local de Brasília da data de modificação) -> intervalo de horas [ini, fim]
_PERIODOS_ARQUIVO = {
    "madrugada": (0, 5),    # 00h–05h59
    "manha": (6, 11),       # 06h–11h59
    "tarde": (12, 17),      # 12h–17h59
    "noite": (18, 23),      # 18h–23h59
}
# hora local (Brasília) da modificação: o valor é guardado como UTC (a tela também assume isso ao
# exibir), então convertemos UTC -> America/Sao_Paulo antes de extrair a hora
_SQL_HORA_LOCAL = (
    "EXTRACT(HOUR FROM (a.dt_modificacao AT TIME ZONE 'UTC' AT TIME ZONE 'America/Sao_Paulo'))"
)
# caminho do arquivo normalizado para a navegação por pastas: o UFDR mistura '/' e '\' (e às vezes
# repete a barra), então uniformizamos em '/' simples e sem barra inicial — assim 'DCIM/Camera' é
# sempre a mesma pasta, venha o caminho do iPhone ou do Android.
_SQL_CAMINHO_NORM = r"ltrim(regexp_replace(replace(a.tx_caminho, '\', '/'), '/+', '/', 'g'), '/')"


# Mensagem de conversa que carregou o MESMO binário deste arquivo: os dois registros apontam para
# o mesmo membro do ZIP (ARQ_TB01.CO_CAMINHO_ZIP vem do "Local Path" do <file>; ANEXOS.CO_CAMINHO_ZIP
# vem do casamento por tamanho da ingestão). A extração entra na condição porque o caminho dentro do
# zip só é único dentro de um .ufdr — dois aparelhos podem ter 'files/Image/1.jpg'.
# Alimenta o botão "Ver na conversa" da tela de arquivos; depende dos índices do 76_*.sql.
_SQL_CHAT_DO_ARQUIVO = """
    SELECT u.fonte, u.id_mensagem, u.tp_app FROM (
        SELECT 'wpp'::text AS fonte, m.id_mensagem, NULL::text AS tp_app
        FROM investigacao.wpp_tb05_anexos x
        JOIN investigacao.wpp_tb04_mensagens m ON m.id_mensagem = x.id_mensagem
        JOIN investigacao.wpp_tb02_conversas c ON c.id_conversa = m.id_conversa
        WHERE x.co_caminho_zip = p.co_caminho_zip AND c.id_extracao = p.id_extracao
        UNION ALL
        SELECT 'sms', m.id_mensagem, NULL
        FROM investigacao.sms_tb05_anexos x
        JOIN investigacao.sms_tb04_mensagens m ON m.id_mensagem = x.id_mensagem
        JOIN investigacao.sms_tb02_conversas c ON c.id_conversa = m.id_conversa
        WHERE x.co_caminho_zip = p.co_caminho_zip AND c.id_extracao = p.id_extracao
        UNION ALL
        SELECT 'msg', m.id_mensagem, c.tp_app
        FROM investigacao.msg_tb05_anexos x
        JOIN investigacao.msg_tb04_mensagens m ON m.id_mensagem = x.id_mensagem
        JOIN investigacao.msg_tb02_conversas c ON c.id_conversa = m.id_conversa
        WHERE x.co_caminho_zip = p.co_caminho_zip AND c.id_extracao = p.id_extracao
    ) u
    LIMIT 1
"""


def normalizar_pasta(caminho: str | None) -> str:
    """Normaliza o caminho de uma pasta do mesmo jeito que _SQL_CAMINHO_NORM faz com o do arquivo:
    barras '/' simples, sem barra no início nem no fim. Pasta raiz vira ''."""
    if not caminho:
        return ""
    limpo = re.sub(r"/+", "/", caminho.replace("\\", "/"))
    return limpo.strip("/")


def _filtros_arquivos(
    id_inquerito: int,
    id_extracao: int | None,
    categoria: str | None,
    busca: str | None,
    so_disponivel: bool,
    status: str | None,
    tamanho_min: int | None,
    data_de: str | None,
    data_ate: str | None,
    periodo: str | None,
    ocr: str | None = None,
    classificacao_ia: str | None = None,
    marcados: bool = False,
) -> tuple[str, list]:
    """Monta o WHERE (e seus parâmetros) comum à listagem de arquivos e à navegação por pastas.
    A tabela precisa estar aliasada como `a` e a extração como `ex`.

    classificacao_ia: 'com' = classificadas, 'sem' = imagens sem classificação, ou uma string exata"""
    filtros = ["ex.id_inquerito = %s"]
    params: list = [id_inquerito]
    if id_extracao is not None:
        filtros.append("a.id_extracao = %s")
        params.append(id_extracao)
    if categoria in _CATEGORIAS_ARQUIVO:
        filtros.append("a.no_categoria = %s")
        params.append(categoria)
    else:
        # sem filtro de categoria explícito, os metadados de sincronização do Google
        # (sidecars do Photos, _file_metadata.json do Drive, MessageInformation do Gmail)
        # ficam ocultos — num caso de nuvem real são ~44 mil dos 50 mil "documentos", e
        # afogavam o conteúdo do usuário na primeira página. A opção "Metadados" no filtro
        # do frontend (value "Metadado") mostra a categoria quando o investigador quiser
        filtros.append("a.no_categoria IS DISTINCT FROM 'Metadado'")
    if so_disponivel:
        filtros.append("a.co_caminho_zip IS NOT NULL")
    if status == "intacto":
        filtros.append("a.cd_status_arquivo = 'Intact'")
    elif status == "apagado":
        # apagado/recuperado: qualquer status que não seja 'Intact' (ex.: 'Trash', 'Deleted')
        filtros.append("a.cd_status_arquivo IS NOT NULL AND a.cd_status_arquivo <> 'Intact'")
    if tamanho_min:
        filtros.append("a.qt_bytes >= %s")
        params.append(int(tamanho_min))
    if data_de and _RE_DATA_ISO.match(data_de):
        filtros.append("a.dt_modificacao >= %s::date")
        params.append(data_de)
    if data_ate and _RE_DATA_ISO.match(data_ate):
        filtros.append("a.dt_modificacao < (%s::date + 1)")   # inclui o dia inteiro de data_ate
        params.append(data_ate)
    if periodo in _PERIODOS_ARQUIVO:
        hora_ini, hora_fim = _PERIODOS_ARQUIVO[periodo]
        filtros.append(f"{_SQL_HORA_LOCAL} BETWEEN %s AND %s")   # NULL (sem data) fica de fora, ok
        params.extend([hora_ini, hora_fim])
    # 'com' = imagem em que o OCR achou texto; 'sem' = já passou pelo OCR e não tinha texto;
    # 'pendente' = ainda não processada (é a fila do botão "Extrair texto (OCR)")
    if ocr == "com":
        filtros.append("a.tx_ocr IS NOT NULL")
    elif ocr == "sem":
        filtros.append("a.dt_ocr IS NOT NULL AND a.tx_ocr IS NULL")
    elif ocr == "pendente":
        filtros.append("a.dt_ocr IS NULL")
    # Filtro de classificação por IA: 'com' = classificadas, 'sem' = sem classificação
    if classificacao_ia == "com":
        filtros.append("a.classificacao_ia IS NOT NULL")
    elif classificacao_ia == "sem":
        filtros.append("a.classificacao_ia IS NULL")
    elif classificacao_ia:
        # se for um valor exato (ex: "Documento", "Objetos proibidos"), filtra por ele
        filtros.append("a.classificacao_ia = %s")
        params.append(classificacao_ia)
    if marcados:
        # triagem do PERITO dentro do IPED (bookmarks.iped): itens que ele marcou ou selecionou
        filtros.append("(a.tx_marcadores IS NOT NULL OR a.in_selecionado = true)")
    if busca:
        termo = f"%{busca.strip()}%"
        # o texto do OCR entra na busca junto com nome/caminho: é o que faz um print de comprovante
        # PIX aparecer numa busca por CPF, chave ou valor
        filtros.append("(a.no_arquivo ILIKE %s OR a.tx_caminho ILIKE %s OR a.tx_ocr ILIKE %s)")
        params.extend([termo, termo, termo])
    return " AND ".join(filtros), params


def listar_arquivos(
    id_inquerito: int,
    id_extracao: int | None = None,
    categoria: str | None = None,
    busca: str | None = None,
    so_disponivel: bool = False,
    status: str | None = None,
    tamanho_min: int | None = None,
    data_de: str | None = None,
    data_ate: str | None = None,
    periodo: str | None = None,
    ordenar: str | None = None,
    pagina: int = 1,
    limite: int = 100,
    pasta: str | None = None,
    ocr: str | None = None,
    classificacao_ia: str | None = None,
    marcados: bool = False,
) -> dict:
    """Lista, paginado, os arquivos do aparelho (<file>) de um inquérito. Uma extração tem de dezenas
    de milhares (Android) a centenas de milhares (iPhone) de arquivos — por isso paginação e filtros
    são feitos no servidor.

    Filtros: id_extracao (aparelho), categoria, busca (nome/caminho/texto do OCR), so_disponivel (tem
    binário), status ('intacto'|'apagado'), tamanho_min (bytes), data_de/data_ate ('AAAA-MM-DD', sobre
    a data de modificação), periodo ∈ _PERIODOS_ARQUIVO (faixa de horário local da modificação),
    ocr ('com'|'sem'|'pendente'). ordenar ∈ _ORDENACOES_ARQUIVO (default 'categoria').

    pasta: quando informada (pode ser '' para a raiz), traz SÓ os arquivos diretamente dentro dela —
    é o que alimenta a visualização por navegação em pastas. None = todas as pastas (visão lista).

    Devolve {total, pagina, limite, itens}. `disponivel` indica se há binário no zip (co_caminho_zip)."""
    pagina = max(1, pagina)
    limite = max(1, min(LIMITE_MAX_LISTAGEM, limite))
    offset = (pagina - 1) * limite

    where, params = _filtros_arquivos(
        id_inquerito, id_extracao, categoria, busca, so_disponivel,
        status, tamanho_min, data_de, data_ate, periodo, ocr, classificacao_ia, marcados,
    )
    if pasta is not None:
        # arquivo direto na pasta = começa com o prefixo dela e o que sobra não tem mais nenhuma '/'
        prefixo = normalizar_pasta(pasta)
        prefixo = prefixo + "/" if prefixo else ""
        where += (
            f" AND left({_SQL_CAMINHO_NORM}, %s) = %s"
            f" AND strpos(substr({_SQL_CAMINHO_NORM}, %s), '/') = 0"
        )
        params.extend([len(prefixo), prefixo, len(prefixo) + 1])
    order_by = _ORDENACOES_ARQUIVO.get(ordenar or "", _ORDENACOES_ARQUIVO["categoria"])

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT count(*)
                    FROM investigacao.arq_tb01_arquivos a
                    JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = a.id_extracao
                    WHERE {where}""",
                params,
            )
            total = cur.fetchone()[0]

            # a busca da mensagem de origem (LATERAL) só roda em cima da PÁGINA já recortada —
            # daí a CTE: fora dela, seria uma busca por arquivo do inquérito inteiro
            cur.execute(
                f"""
                WITH pagina AS (
                    SELECT a.id_arquivo, a.id_extracao, a.no_arquivo, a.tx_caminho, a.no_categoria,
                           a.tp_mime, a.qt_bytes, a.dt_modificacao, a.cd_status_arquivo,
                           a.classificacao_ia, a.confianca_ia, a.co_caminho_zip,
                           a.co_hash_md5, a.co_hash_sha1, a.co_hash_sha256,
                           -- só um trecho: o texto inteiro de um print pode ter milhares de
                           -- caracteres e a página traz 100 linhas (o completo vem no modal)
                           left(a.tx_ocr, 400) AS tx_ocr, a.dt_ocr,
                           a.tx_marcadores, a.in_selecionado
                    FROM investigacao.arq_tb01_arquivos a
                    JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = a.id_extracao
                    WHERE {where}
                    ORDER BY {order_by}
                    LIMIT %s OFFSET %s
                )
                SELECT p.id_arquivo, p.no_arquivo, p.tx_caminho, p.no_categoria, p.tp_mime,
                       p.qt_bytes, p.dt_modificacao, p.cd_status_arquivo,
                       p.classificacao_ia, p.confianca_ia, p.tx_ocr,
                       p.co_hash_md5, p.co_hash_sha1, p.co_hash_sha256,
                       (p.dt_ocr IS NOT NULL) AS ocr_feito,
                       (p.co_caminho_zip IS NOT NULL) AS disponivel,
                       p.tx_marcadores AS marcadores_perito, p.in_selecionado AS selecionado_perito,
                       chat.fonte AS chat_fonte, chat.id_mensagem AS chat_id_mensagem,
                       chat.tp_app AS chat_app
                FROM pagina p
                LEFT JOIN LATERAL ({_SQL_CHAT_DO_ARQUIVO}) chat ON true
                ORDER BY {order_by.replace('a.', 'p.')}
                """,
                params + [limite, offset],
            )
            colunas = [d.name for d in cur.description]
            itens = [dict(zip(colunas, linha)) for linha in cur.fetchall()]

    return {"total": total, "pagina": pagina, "limite": limite, "itens": itens}


def listar_pastas_arquivos(
    id_inquerito: int,
    id_extracao: int | None = None,
    pasta: str | None = None,
    categoria: str | None = None,
    busca: str | None = None,
    so_disponivel: bool = False,
    status: str | None = None,
    tamanho_min: int | None = None,
    data_de: str | None = None,
    data_ate: str | None = None,
    periodo: str | None = None,
    ocr: str | None = None,
    classificacao_ia: str | None = None,
) -> dict:
    """Subpastas imediatas de `pasta` ('' = raiz do aparelho), para a navegação em pastas da tela
    de arquivos. Aceita os mesmos filtros de listar_arquivos, de modo que a árvore reflita sempre o
    recorte em tela (ex.: filtrando por Imagem, só aparecem as pastas que contêm imagens).

    Cada subpasta traz a contagem e o volume RECURSIVOS (tudo abaixo dela), que é o que interessa
    para saber onde está o material. Os arquivos soltos na própria pasta vêm de listar_arquivos com
    o mesmo `pasta`, que pagina e ordena normalmente.

    Devolve {pasta, pastas: [{no_pasta, qt_arquivos, qt_bytes}], qt_arquivos_direto}."""
    prefixo = normalizar_pasta(pasta)
    prefixo = prefixo + "/" if prefixo else ""

    where, params = _filtros_arquivos(
        id_inquerito, id_extracao, categoria, busca, so_disponivel,
        status, tamanho_min, data_de, data_ate, periodo, ocr, classificacao_ia,
    )
    # `resto` = o que vem depois do prefixo da pasta atual; se ainda tem '/', o primeiro segmento
    # é uma subpasta — se não tem, o arquivo está solto aqui (contado à parte, em qt_arquivos_direto)
    resto = f"substr({_SQL_CAMINHO_NORM}, %s)"

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT split_part({resto}, '/', 1) AS no_pasta,
                       count(*) AS qt_arquivos,
                       coalesce(sum(a.qt_bytes), 0) AS qt_bytes
                FROM investigacao.arq_tb01_arquivos a
                JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = a.id_extracao
                WHERE {where}
                  AND left({_SQL_CAMINHO_NORM}, %s) = %s
                  AND strpos({resto}, '/') > 0
                GROUP BY 1
                ORDER BY 1
                """,
                [len(prefixo) + 1] + params + [len(prefixo), prefixo, len(prefixo) + 1],
            )
            pastas = [
                {"no_pasta": nome, "qt_arquivos": qt, "qt_bytes": int(bytes_)}
                for nome, qt, bytes_ in cur.fetchall()
            ]

            cur.execute(
                f"""
                SELECT count(*)
                FROM investigacao.arq_tb01_arquivos a
                JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = a.id_extracao
                WHERE {where}
                  AND left({_SQL_CAMINHO_NORM}, %s) = %s
                  AND strpos({resto}, '/') = 0
                """,
                params + [len(prefixo), prefixo, len(prefixo) + 1],
            )
            qt_direto = cur.fetchone()[0]

    return {
        "pasta": normalizar_pasta(pasta),
        "pastas": pastas,
        "qt_arquivos_direto": qt_direto,
    }


def periodo_arquivos(id_inquerito: int, id_extracao: int | None = None) -> dict:
    """Data de modificação mínima e máxima dos arquivos de um inquérito (?extracao=ID restringe a um
    aparelho) — âncora dos atalhos de período da tela ("últimos 30 dias" termina no registro mais
    recente, não em hoje: a extração costuma ser de meses atrás). Devolve {min, max} em 'AAAA-MM-DD'
    ou {} quando não há arquivo com data."""
    params: list = [id_inquerito]
    extracao_sql = ""
    if id_extracao is not None:
        extracao_sql = " AND a.id_extracao = %s"
        params.append(id_extracao)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT min(a.dt_modificacao)::date, max(a.dt_modificacao)::date
                    FROM investigacao.arq_tb01_arquivos a
                    JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = a.id_extracao
                    WHERE ex.id_inquerito = %s{extracao_sql}""",
                params,
            )
            minimo, maximo = cur.fetchone()
    if not minimo or not maximo:
        return {}
    return {"min": minimo.isoformat(), "max": maximo.isoformat()}


def _exif_para_decimal(valor, ref) -> float | None:
    """Converte coordenada EXIF (graus, minutos, segundos) + hemisfério (N/S/E/W) em grau decimal."""
    try:
        # EXIF corrompido é comum: IFDRational(0, 0) faz float() levantar ZeroDivisionError
        # (visto em produção, 500 no metadados), e o valor pode nem vir como tripla.
        graus, minutos, segundos = (float(x) for x in valor)
    except (TypeError, ValueError, ArithmeticError):
        return None
    decimal = graus + minutos / 60 + segundos / 3600
    if str(ref).upper() in ("S", "W"):
        decimal = -decimal
    return round(decimal, 6)


def _exif_data_iso(texto) -> str | None:
    """EXIF grava data como 'AAAA:MM:DD HH:MM:SS'; troca os ':' da data por '-' para ficar legível."""
    if not texto:
        return None
    texto = str(texto).strip()
    # "AAAA:MM:DD HH:MM:SS" -> "AAAA-MM-DD HH:MM:SS" (só os dois ':' da parte da data)
    if len(texto) >= 10 and texto[4] == ":" and texto[7] == ":":
        return texto[:4] + "-" + texto[5:7] + "-" + texto[8:]
    return texto


def extrair_metadados_imagem(dados: bytes) -> dict:
    """Lê metadados EXIF de uma imagem (dimensões, câmera, data da captura, GPS, software, lente).

    Best-effort: devolve só o que existir (imagem sem EXIF volta só com dimensões; ilegível volta {}).
    HEIC do iPhone funciona pelo pillow-heif já registrado no import deste módulo."""
    meta: dict = {}
    try:
        img = Image.open(BytesIO(dados))
    except Exception:
        return meta
    meta["largura"] = img.width
    meta["altura"] = img.height

    try:
        exif = img.getexif()
    except Exception:
        exif = None
    if not exif:
        return meta

    fabricante = exif.get(0x010F)   # Make
    modelo = exif.get(0x0110)       # Model
    camera = " ".join(str(x).strip() for x in (fabricante, modelo) if x).strip()
    if camera:
        meta["camera"] = camera
    if exif.get(0x0131):            # Software
        meta["software"] = str(exif.get(0x0131)).strip()

    data_captura = None
    try:
        ifd_exif = exif.get_ifd(0x8769)  # sub-IFD Exif
    except Exception:
        ifd_exif = {}
    if ifd_exif:
        data_captura = ifd_exif.get(0x9003) or ifd_exif.get(0x9004)  # DateTimeOriginal / DateTimeDigitized
        if ifd_exif.get(0xA434):     # LensModel
            meta["lente"] = str(ifd_exif.get(0xA434)).strip()
    data_captura = data_captura or exif.get(0x0132)  # DateTime (IFD0)
    if data_captura:
        meta["data_captura"] = _exif_data_iso(data_captura)

    try:
        gps = exif.get_ifd(0x8825)  # sub-IFD GPSInfo
    except Exception:
        gps = None
    if gps:
        lat = _exif_para_decimal(gps.get(2), gps.get(1))   # GPSLatitude / GPSLatitudeRef
        lon = _exif_para_decimal(gps.get(4), gps.get(3))   # GPSLongitude / GPSLongitudeRef
        if lat is not None and lon is not None:
            meta["gps_lat"] = lat
            meta["gps_lon"] = lon

    return meta


# localização gravada pelo iPhone no vídeo: string ISO 6709 tipo "-20.3384-40.3345+010.000/"
# (lat, lon e altitude opcional). O '/' final a torna distinguível do resto dos bytes do moov.
_RE_VIDEO_ISO6709 = re.compile(rb"([+-]\d{2,3}\.\d+)([+-]\d{2,3}\.\d+)(?:[+-][0-9.]+)?/")
# data de criação (com.apple.quicktime.creationdate), no padrão ISO dentro do moov
_RE_VIDEO_DATA = re.compile(rb"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)")


def _ler_moov(fp) -> bytes | None:
    """Percorre os átomos de topo de um MP4/MOV (fp seekable) e devolve os bytes do átomo 'moov'
    (onde ficam os metadados), pulando o 'mdat' (o vídeo em si) sem lê-lo. None se não achar."""
    while True:
        cabecalho = fp.read(8)
        if len(cabecalho) < 8:
            return None
        tamanho = struct.unpack(">I", cabecalho[:4])[0]
        tipo = cabecalho[4:8]
        if tamanho == 1:                       # tamanho de 64 bits vem nos 8 bytes seguintes
            tamanho = struct.unpack(">Q", fp.read(8))[0]
            corpo = tamanho - 16
        elif tamanho == 0:                     # vai até o fim do arquivo
            corpo = -1
        else:
            corpo = tamanho - 8
        if tipo == b"moov":
            return fp.read() if corpo < 0 else fp.read(corpo)
        if corpo < 0:
            return None
        fp.seek(corpo, 1)                      # pula o corpo deste átomo (ex.: mdat) e segue


def extrair_metadados_video(fp) -> dict:
    """Extrai localização (GPS) e data de criação de um vídeo MP4/MOV do iPhone, lendo só o átomo
    'moov' (fp seekable, ex.: zipfile.open). Best-effort: {} se não for MP4/MOV ou não tiver os dados."""
    meta: dict = {}
    try:
        moov = _ler_moov(fp)
    except Exception:
        moov = None
    if not moov:
        return meta
    m = _RE_VIDEO_ISO6709.search(moov)
    if m:
        try:
            meta["gps_lat"] = round(float(m.group(1)), 6)
            meta["gps_lon"] = round(float(m.group(2)), 6)
        except ValueError:
            pass
    d = _RE_VIDEO_DATA.search(moov)
    if d:
        meta["data_captura"] = d.group(1).decode("ascii", "ignore").replace("T", " ")
    return meta


def obter_binario_arquivo(id_arquivo: int) -> dict | None:
    """Resolve mime/caminho-no-zip/nome/arquivo .ufdr/inquérito de um arquivo do aparelho, para servir
    o binário sob demanda direto do .ufdr (sem duplicar no banco). None se não existir."""
    query = """
        SELECT a.tp_mime, a.co_caminho_zip, a.no_arquivo, e.no_arquivo, e.id_inquerito,
               a.id_extracao
        FROM investigacao.arq_tb01_arquivos a
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = a.id_extracao
        WHERE a.id_arquivo = %s
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (id_arquivo,))
            row = cur.fetchone()
    if row is None:
        return None
    tp_mime, caminho_zip, no_arquivo_dev, no_arquivo_ufdr, id_inquerito, id_extracao = row
    return {
        "tp_mime": tp_mime,
        "caminho_zip": caminho_zip,
        "no_arquivo": no_arquivo_dev,
        "no_arquivo_ufdr": no_arquivo_ufdr,
        "id_inquerito": id_inquerito,
        "id_extracao": id_extracao,
    }


def atualizar_texto_ocr(id_arquivo: int, texto: str | None) -> bool:
    """Revisão manual do TX_OCR de um arquivo — mesmo espírito da correção de transcrição
    (atualizar_transcricao_anexo): o OCR erra em print torto/fonte pequena, e quem corrigiu à mão
    não pode perder a correção no próximo reprocessamento. DT_OCR é carimbado SEMPRE, mesmo com
    texto vazio: a fila de pendentes é o índice parcial em DT_OCR IS NULL (IX78) — arquivo
    revisado por humano já foi processado, com ou sem texto."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE investigacao.arq_tb01_arquivos SET tx_ocr = %s, dt_ocr = now() "
                "WHERE id_arquivo = %s",
                (texto, id_arquivo),
            )
            atualizado = cur.rowcount > 0
        conn.commit()
    return atualizado


def atualizar_classificacao_arquivo(id_arquivo: int, classificacao: str | None) -> bool:
    """Classificação manual de um arquivo (revisão da classe que a IA atribuiu). A confiança vai
    1.0 — certeza humana — e o job em lote da CLIP só seleciona arquivos com classificacao_ia
    IS NULL, então a revisão manual nunca é sobrescrita pela IA. None limpa a classificação
    (o arquivo volta a ficar "sem classe" e, sim, elegível ao job)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE investigacao.arq_tb01_arquivos "
                "SET classificacao_ia = %s, confianca_ia = %s WHERE id_arquivo = %s",
                (classificacao, 1.0 if classificacao else None, id_arquivo),
            )
            atualizado = cur.rowcount > 0
        conn.commit()
    return atualizado


def obter_foto_conversa(id_conversa: int) -> dict | None:
    """Resolve a foto de perfil do OUTRO lado de uma conversa de WhatsApp (o participante que não é
    o dono do aparelho), para servir a imagem sob demanda direto do .ufdr — só o ponteiro fica no
    banco. Devolve {caminho_zip, no_arquivo_ufdr} do primeiro participante com foto, ou None."""
    query = """
        SELECT ct.co_caminho_foto, e.no_arquivo
        FROM investigacao.wpp_tb03_participantes p
        JOIN investigacao.wpp_tb01_contatos ct ON ct.id_contato = p.id_contato
        JOIN investigacao.wpp_tb02_conversas c ON c.id_conversa = p.id_conversa
        JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = c.id_extracao
        WHERE p.id_conversa = %s AND NOT coalesce(ct.in_dono_dispositivo, false)
              AND ct.co_caminho_foto IS NOT NULL
        ORDER BY ct.no_contato NULLS LAST
        LIMIT 1
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (id_conversa,))
            row = cur.fetchone()
    if row is None:
        return None
    return {"caminho_zip": row[0], "no_arquivo_ufdr": row[1]}


# Moeda da LOAD_TRUNCATED_IMAGES: com o decode tolerante a arquivo picotado, o cabeçalho
# declarado pode mentir (bomba de descompressão, mosaico doentio de 300 MP). Uma prévia de
# 800 px não tem motivo nenhum para decodificar 100+ megapixels — acima disto é erro
# honesto (422 na tela), não OOM silencioso no worker da API.
_TETO_PIXELS_PREVIA = 100_000_000


def redimensionar_imagem(dados: bytes, tamanho_max: int = 800, qualidade: int = 75) -> bytes:
    """Reduz uma imagem para no máximo tamanho_max px no lado maior e reencoda como JPEG.

    É só pra pré-visualização no chat — evita transferir (e, se algum dia guardássemos em
    cache, armazenar) o binário original em resolução cheia.

    Levanta ValueError com mensagem legível quando o arquivo declara mais de
    _TETO_PIXELS_PREVIA pixels (as rotas traduzem em 422; o download do original não é
    afetado — a prova continua sendo o binário, nunca esta prévia)."""
    try:
        imagem = Image.open(BytesIO(dados))
        if imagem.width * imagem.height > _TETO_PIXELS_PREVIA:
            raise ValueError(
                f"Imagem de {imagem.width}×{imagem.height} px grande demais para a prévia.")
        # foto de celular carrega a rotação no EXIF, não nos pixels: sem transpor, toda
        # foto vertical de Android/iPhone sai deitada na tela (o download direto não
        # sofre, é o navegador quem aplica o EXIF lá)
        try:
            imagem = ImageOps.exif_transpose(imagem)
        except Exception:
            pass  # EXIF de arquivo truncado é secundário: a prévia vale sem a rotação
        imagem.thumbnail((tamanho_max, tamanho_max))
        if imagem.mode != "RGB":
            imagem = imagem.convert("RGB")
        saida = BytesIO()
        imagem.save(saida, format="JPEG", quality=qualidade)
        return saida.getvalue()
    except Image.DecompressionBombError as exc:
        raise ValueError("Imagem grande demais para ser pré-visualizada.") from exc


def listar_participantes_conversa(id_conversa: int, fonte: str) -> list[dict]:
    """Lista os participantes de uma conversa (exceto o dono do aparelho), com a associação manual atual."""
    t = _TABELAS_CONVERSA[fonte]
    query = f"""
        SELECT ct.id_contato, ct.no_contato AS nome, ct.{t['coluna_identificador']} AS identificador,
               ct.id_pessoa_associada, pes.no_pessoa AS pessoa_nome, pes.nu_cpf AS pessoa_cpf,
               ct.id_municipio_associado, mun.no_municipio AS municipio_nome,
               ct.id_uf_associada, uf.sg_uf AS uf_sigla, uf.no_uf AS uf_nome,
               ct.id_usuario_associou, ua.no_usuario AS associado_por
        FROM investigacao.{t['participantes']} p
        JOIN investigacao.{t['contatos']} ct ON ct.id_contato = p.id_contato
        LEFT JOIN dados_extraidos.pes_tb01_pessoas pes ON pes.id_pessoa = ct.id_pessoa_associada
        LEFT JOIN dados_referencia.ibge_tb08_municipio mun ON mun.id_municipio = ct.id_municipio_associado
        LEFT JOIN dados_referencia.ibge_tb03_uf uf ON uf.id_uf = ct.id_uf_associada
        LEFT JOIN seguranca.usr_tb01_usuarios ua ON ua.id_usuario = ct.id_usuario_associou
        WHERE p.id_conversa = %s AND NOT coalesce(ct.in_dono_dispositivo, false)
        ORDER BY ct.no_contato NULLS LAST
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (id_conversa,))
            colunas = [d.name for d in cur.description]
            return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


def associar_contato(
    id_contato: int, fonte: str, id_pessoa: int | None, id_municipio: int | None, id_uf: int | None,
    id_usuario: int | None = None,
) -> bool:
    """Grava (ou limpa, passando tudo None) a associação manual de um contato a pessoa/local de referência.

    id_usuario fica registrado mesmo ao limpar — é sempre "quem fez a última alteração aqui".
    Devolve False se o id_contato não existir na tabela da fonte informada."""
    t = _TABELAS_CONVERSA[fonte]
    query = f"""
        UPDATE investigacao.{t['contatos']}
        SET id_pessoa_associada = %s, id_municipio_associado = %s, id_uf_associada = %s, id_usuario_associou = %s
        WHERE id_contato = %s
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (id_pessoa, id_municipio, id_uf, id_usuario, id_contato))
            atualizado = cur.rowcount > 0
        conn.commit()
    return atualizado


# ordenações permitidas na listagem de contatos (whitelist — o valor cru nunca entra no SQL)
_ORDENACOES_CONTATO = {
    "nome": "c.no_contato NULLS LAST, c.id_contato",
    "nome_desc": "c.no_contato DESC NULLS LAST, c.id_contato",
    "grupo": "c.no_grupo NULLS LAST, c.no_contato NULLS LAST",
    "grupo_desc": "c.no_grupo DESC NULLS LAST, c.no_contato NULLS LAST",
    "origem": "c.no_aplicativo_origem NULLS LAST, c.no_contato NULLS LAST",
    "origem_desc": "c.no_aplicativo_origem DESC NULLS LAST, c.no_contato NULLS LAST",
}


def listar_contatos(
    id_inquerito: int,
    id_extracao: int | None = None,
    busca: str | None = None,
    tipo: str | None = None,
    ordenar: str | None = None,
    pagina: int = 1,
    limite: int = 100,
) -> dict:
    """Lista, paginado, os contatos da agenda (Contact) de um inquérito, com telefones/e-mails agregados.

    id_extracao opcional restringe a uma única extração (um aparelho) do inquérito.
    busca filtra por nome, grupo, app de origem ou telefone/e-mail.
    tipo filtra por tp_contato. ordenar ∈ _ORDENACOES_CONTATO (default: nome).
    Devolve {total, pagina, limite, itens}."""
    pagina = max(1, pagina)
    limite = max(1, min(LIMITE_MAX_LISTAGEM, limite))
    offset = (pagina - 1) * limite

    filtros, params = _where_contato(id_inquerito, id_extracao, busca, tipo)
    where = " AND ".join(filtros)
    order_by = _ORDENACOES_CONTATO.get(ordenar or "", _ORDENACOES_CONTATO["nome"])

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT count(*)
                    FROM investigacao.ctt_tb01_contatos c
                    JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = c.id_extracao
                    WHERE {where}""",
                params,
            )
            total = cur.fetchone()[0]

            cur.execute(
                f"""SELECT c.id_contato, c.no_contato, c.no_aplicativo_origem, c.no_grupo, c.tp_contato,
                           (SELECT string_agg(e.tx_valor, ', ' ORDER BY e.tx_valor)
                              FROM investigacao.ctt_tb02_contato_entradas e WHERE e.id_contato = c.id_contato
                           ) AS entradas
                    FROM investigacao.ctt_tb01_contatos c
                    JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = c.id_extracao
                    WHERE {where}
                    ORDER BY {order_by}
                    LIMIT %s OFFSET %s""",
                params + [limite, offset],
            )
            colunas = [d.name for d in cur.description]
            itens = [dict(zip(colunas, linha)) for linha in cur.fetchall()]

    return {"total": total, "pagina": pagina, "limite": limite, "itens": itens}


def _where_contato(
    id_inquerito: int, id_extracao: int | None, busca: str | None, tipo: str | None,
) -> tuple[list[str], list]:
    """Monta a cláusula WHERE (filtros + params) para a listagem de contatos."""
    filtros = ["ex.id_inquerito = %s"]
    params: list = [id_inquerito]
    if id_extracao is not None:
        filtros.append("c.id_extracao = %s")
        params.append(id_extracao)
    if tipo:
        filtros.append("c.tp_contato = %s")
        params.append(tipo)
    if busca:
        termo = f"%{busca.strip()}%"
        filtros.append(
            "(c.no_contato ILIKE %s OR c.no_aplicativo_origem ILIKE %s OR c.no_grupo ILIKE %s "
            "OR EXISTS (SELECT 1 FROM investigacao.ctt_tb02_contato_entradas e WHERE e.id_contato = c.id_contato AND e.tx_valor ILIKE %s))"
        )
        params.extend([termo, termo, termo, termo])
    return filtros, params


def facetas_contato(id_inquerito: int, id_extracao: int | None = None) -> dict:
    """Valores distintos (tipos de contato) para popular os selects de filtro da página de contatos."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT DISTINCT c.tp_contato
                   FROM investigacao.ctt_tb01_contatos c
                   JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = c.id_extracao
                   WHERE ex.id_inquerito = %s AND (%s::bigint IS NULL OR c.id_extracao = %s)
                     AND c.tp_contato IS NOT NULL AND c.tp_contato <> ''
                   ORDER BY c.tp_contato""",
                (id_inquerito, id_extracao, id_extracao),
            )
            tipos = [linha[0] for linha in cur.fetchall()]
    return {"tipos": tipos}


# ordenações permitidas na listagem de ligações (whitelist — o valor cru nunca entra no SQL)
_ORDENACOES_LIGACAO = {
    "recentes": "l.dt_ligacao DESC NULLS LAST, l.id_ligacao",
    "antigos": "l.dt_ligacao ASC NULLS LAST, l.id_ligacao",
    "duracao": "l.qt_duracao_segundos DESC NULLS LAST, l.id_ligacao",
    "duracao_asc": "l.qt_duracao_segundos ASC NULLS LAST, l.id_ligacao",
    "contato": "l.no_contato NULLS LAST, l.dt_ligacao DESC NULLS LAST",
    "contato_desc": "l.no_contato DESC NULLS LAST, l.dt_ligacao DESC NULLS LAST",
}


def listar_ligacoes(
    id_inquerito: int,
    id_extracao: int | None = None,
    busca: str | None = None,
    direcao: str | None = None,
    data_de: str | None = None,
    data_ate: str | None = None,
    periodo: str | None = None,
    ordenar: str | None = None,
    pagina: int = 1,
    limite: int = 100,
) -> dict:
    """Lista, paginado, as ligações de um inquérito, mais recente primeiro.

    id_extracao opcional restringe a uma única extração (um aparelho) do inquérito.
    busca filtra por nome do contato, telefone ou app.
    direcao filtra por tp_direcao.
    data_de/data_ate ('AAAA-MM-DD') e periodo (madrugada/manha/tarde/noite) filtram por data/hora.
    ordenar ∈ _ORDENACOES_LIGACAO (default: recentes). Devolve {total, pagina, limite, itens}."""
    pagina = max(1, pagina)
    limite = max(1, min(LIMITE_MAX_LISTAGEM, limite))
    offset = (pagina - 1) * limite

    filtros, params = _where_ligacao(id_inquerito, id_extracao, busca, direcao, data_de, data_ate, periodo)
    where = " AND ".join(filtros)
    order_by = _ORDENACOES_LIGACAO.get(ordenar or "", _ORDENACOES_LIGACAO["recentes"])

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT count(*)
                    FROM investigacao.lig_tb01_ligacoes l
                    JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = l.id_extracao
                    WHERE {where}""",
                params,
            )
            total = cur.fetchone()[0]

            cur.execute(
                f"""SELECT l.id_ligacao, l.no_aplicativo, l.tp_direcao, l.cd_status, l.dt_ligacao,
                           l.qt_duracao_segundos, l.in_videochamada, l.nu_telefone_contato, l.no_contato
                    FROM investigacao.lig_tb01_ligacoes l
                    JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = l.id_extracao
                    WHERE {where}
                    ORDER BY {order_by}
                    LIMIT %s OFFSET %s""",
                params + [limite, offset],
            )
            colunas = [d.name for d in cur.description]
            itens = [dict(zip(colunas, linha)) for linha in cur.fetchall()]

    return {"total": total, "pagina": pagina, "limite": limite, "itens": itens}


def _where_ligacao(
    id_inquerito: int, id_extracao: int | None, busca: str | None, direcao: str | None,
    data_de: str | None, data_ate: str | None, periodo: str | None,
) -> tuple[list[str], list]:
    """Monta a cláusula WHERE (filtros + params) para a listagem de ligações."""
    filtros = ["ex.id_inquerito = %s"]
    params: list = [id_inquerito]
    if id_extracao is not None:
        filtros.append("l.id_extracao = %s")
        params.append(id_extracao)
    if direcao:
        filtros.append("l.tp_direcao = %s")
        params.append(direcao)
    if data_de and _RE_DATA_ISO.match(data_de):
        filtros.append("l.dt_ligacao >= %s::date")
        params.append(data_de)
    if data_ate and _RE_DATA_ISO.match(data_ate):
        filtros.append("l.dt_ligacao < (%s::date + 1)")
        params.append(data_ate)
    if periodo in _PERIODOS_ARQUIVO:
        hora_ini, hora_fim = _PERIODOS_ARQUIVO[periodo]
        filtros.append(f"{_hora_local_sql('l.dt_ligacao')} BETWEEN %s AND %s")
        params.extend([hora_ini, hora_fim])
    if busca:
        termo = f"%{busca.strip()}%"
        filtros.append(
            "(l.no_contato ILIKE %s OR l.nu_telefone_contato ILIKE %s OR l.no_aplicativo ILIKE %s)"
        )
        params.extend([termo, termo, termo])
    return filtros, params


def facetas_ligacao(id_inquerito: int, id_extracao: int | None = None) -> dict:
    """Valores distintos (direções) para popular os selects de filtro da página de ligações."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT DISTINCT l.tp_direcao
                   FROM investigacao.lig_tb01_ligacoes l
                   JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = l.id_extracao
                   WHERE ex.id_inquerito = %s AND (%s::bigint IS NULL OR l.id_extracao = %s)
                     AND l.tp_direcao IS NOT NULL AND l.tp_direcao <> ''
                   ORDER BY l.tp_direcao""",
                (id_inquerito, id_extracao, id_extracao),
            )
            direcoes = [linha[0] for linha in cur.fetchall()]
    return {"direcoes": direcoes}


def _hora_local_sql(coluna: str) -> str:
    """Hora local (Brasília) de uma coluna timestamp guardada em UTC — para filtro por faixa de horário."""
    return f"EXTRACT(HOUR FROM ({coluna} AT TIME ZONE 'UTC' AT TIME ZONE 'America/Sao_Paulo'))"


# ordenações permitidas na listagem de localizações (whitelist — o valor cru nunca entra no SQL)
_ORDENACOES_LOCALIZACAO = {
    "recentes": "lo.dt_localizacao DESC NULLS LAST, lo.id_localizacao",
    "antigos": "lo.dt_localizacao ASC NULLS LAST, lo.id_localizacao",
}


# "o ponto está dentro deste anel de uma camada geoespacial importada" (ver
# funcoes/geoespacial_utils.py). A caixa envolvente do anel rejeita a esmagadora maioria dos pares
# de graça; só o que sobra paga o `@>`, que percorre os vértices um a um. Sem PostGIS: `polygon` e
# `point` são tipos nativos do Postgres.
_SQL_LOCALIZACAO_NO_ANEL = (
    "lo.vl_latitude BETWEEN {a}.vl_lat_min AND {a}.vl_lat_max "
    "AND lo.vl_longitude BETWEEN {a}.vl_lon_min AND {a}.vl_lon_max "
    "AND {a}.pg_anel @> point(lo.vl_longitude::float8, lo.vl_latitude::float8)"
)


def _filtro_area_localizacao(
    id_inquerito: int, id_camada: int | None, id_feicao: int | None
) -> tuple[list[str], list]:
    """Recorte "só o que está dentro desta área", por camada inteira ou por uma feição dela.

    Dentro de uma área = está em algum contorno externo dela E em nenhum buraco DELA (a ilha vazada
    do polígono). O NOT EXISTS é preso a `ae.id_feicao` de propósito: preso à camada, um buraco de
    uma área excluiria pontos que caem numa área vizinha sobreposta.

    O JOIN com a camada carrega o id_inquerito: sem ele, bastaria trocar o id na URL para recortar
    por uma área de outro inquérito."""
    if not id_camada and not id_feicao:
        return [], []

    coluna = "id_feicao" if id_feicao else "id_camada"   # nome fixo no código, nunca vem da URL
    valor = id_feicao or id_camada
    return (
        [
            "EXISTS (SELECT 1 FROM investigacao.geo_tb04_aneis ae "
            "JOIN investigacao.geo_tb02_camadas ca "
            "  ON ca.id_camada = ae.id_camada AND ca.id_inquerito = %s "
            f"WHERE ae.{coluna} = %s AND NOT ae.in_buraco "
            f"  AND {_SQL_LOCALIZACAO_NO_ANEL.format(a='ae')} "
            "  AND NOT EXISTS (SELECT 1 FROM investigacao.geo_tb04_aneis ab "
            "                  WHERE ab.id_feicao = ae.id_feicao AND ab.in_buraco "
            f"                    AND {_SQL_LOCALIZACAO_NO_ANEL.format(a='ab')}))"
        ],
        [id_inquerito, valor],
    )


def _where_localizacao(
    id_inquerito: int, id_extracao: int | None, busca: str | None, app: str | None,
    categoria: str | None, so_coordenadas: bool, data_de: str | None, data_ate: str | None,
    periodo: str | None, id_camada: int | None = None, id_feicao: int | None = None,
    ids_extracao: list[int] | None = None,
) -> tuple[list[str], list]:
    """Monta a cláusula WHERE (filtros + params) compartilhada pela tabela e pelo mapa de localizações."""
    filtros = ["ex.id_inquerito = %s"]
    params: list = [id_inquerito]
    if id_extracao is not None:
        filtros.append("lo.id_extracao = %s")
        params.append(id_extracao)
    # Seleção de fontes da tela (vários aparelhos de uma vez, ver web/js/localizacoes_fontes.js).
    # Lista VAZIA é uma escolha legítima — "nenhum aparelho, só as antenas" — e por isso não vira
    # "sem filtro": o ANY de um array vazio não casa com nada, que é exatamente o pedido.
    if ids_extracao is not None:
        filtros.append("lo.id_extracao = ANY(%s)")
        params.append(list(ids_extracao))
    if app:
        filtros.append("lo.no_aplicativo = %s")
        params.append(app)
    if categoria:
        filtros.append("lo.no_categoria = %s")
        params.append(categoria)
    if so_coordenadas:
        filtros.append("lo.vl_latitude IS NOT NULL AND lo.vl_longitude IS NOT NULL")
    if data_de and _RE_DATA_ISO.match(data_de):
        filtros.append("lo.dt_localizacao >= %s::date")
        params.append(data_de)
    if data_ate and _RE_DATA_ISO.match(data_ate):
        filtros.append("lo.dt_localizacao < (%s::date + 1)")   # inclui o dia inteiro de data_ate
        params.append(data_ate)
    if periodo in _PERIODOS_ARQUIVO:
        hora_ini, hora_fim = _PERIODOS_ARQUIVO[periodo]
        filtros.append(f"{_hora_local_sql('lo.dt_localizacao')} BETWEEN %s AND %s")
        params.extend([hora_ini, hora_fim])
    filtros_area, params_area = _filtro_area_localizacao(id_inquerito, id_camada, id_feicao)
    filtros.extend(filtros_area)
    params.extend(params_area)
    if busca:
        termo = f"%{busca.strip()}%"
        # g.tx_endereco = endereço geocodificado (cache); por isso as duas queries fazem o LEFT JOIN geo
        filtros.append(
            "(lo.tx_endereco ILIKE %s OR lo.tx_descricao ILIKE %s OR lo.no_aplicativo ILIKE %s OR g.tx_endereco ILIKE %s)"
        )
        params.extend([termo, termo, termo, termo])
    return filtros, params


# LEFT JOIN no cache de geocodificação. A coordenada é arredondada a 5 casas NO SQL (mesma regra usada
# ao gravar o cache), o que casa o jitter de GPS de um mesmo ponto num só endereço. A extração quase
# nunca traz endereço, então usamos o geocodificado sob demanda quando houver. Fica em count e select
# para o filtro de busca também alcançar o endereço geocodificado.
_JOIN_GEO_LOCALIZACAO = (
    "LEFT JOIN investigacao.geo_tb01_enderecos g "
    "ON g.vl_latitude = round(lo.vl_latitude, 5) AND g.vl_longitude = round(lo.vl_longitude, 5)"
)


def listar_localizacoes(
    id_inquerito: int,
    id_extracao: int | None = None,
    busca: str | None = None,
    app: str | None = None,
    categoria: str | None = None,
    so_coordenadas: bool = False,
    data_de: str | None = None,
    data_ate: str | None = None,
    periodo: str | None = None,
    ordenar: str | None = None,
    pagina: int = 1,
    limite: int = 100,
    id_camada: int | None = None,
    id_feicao: int | None = None,
    ids_extracao: list[int] | None = None,
) -> dict:
    """Lista, paginado, as localizações de um inquérito (mais recentes primeiro por padrão).

    Filtros: id_extracao (aparelho), busca (endereço da extração/geocodificado, descrição, app), app (no_aplicativo),
    categoria (no_categoria), so_coordenadas (só com lat/lon), data_de/data_ate ('AAAA-MM-DD'),
    periodo ∈ _PERIODOS_ARQUIVO (faixa de horário local), id_camada/id_feicao (só o que cai dentro
    de uma área importada de shapefile). ordenar ∈ _ORDENACOES_LOCALIZACAO.

    Devolve {total, pagina, limite, itens}."""
    pagina = max(1, pagina)
    limite = max(1, min(LIMITE_MAX_LISTAGEM, limite))
    offset = (pagina - 1) * limite

    filtros, params = _where_localizacao(
        id_inquerito, id_extracao, busca, app, categoria, so_coordenadas, data_de, data_ate,
        periodo, id_camada, id_feicao, ids_extracao
    )
    where = " AND ".join(filtros)
    order_by = _ORDENACOES_LOCALIZACAO.get(ordenar or "", _ORDENACOES_LOCALIZACAO["recentes"])

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT count(*)
                    FROM investigacao.loc_tb01_localizacoes lo
                    JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = lo.id_extracao
                    {_JOIN_GEO_LOCALIZACAO}
                    WHERE {where}""",
                params,
            )
            total = cur.fetchone()[0]

            cur.execute(
                f"""
                SELECT lo.id_localizacao, lo.no_aplicativo, lo.dt_localizacao, lo.vl_latitude, lo.vl_longitude,
                       COALESCE(NULLIF(lo.tx_endereco, ''), g.tx_endereco) AS tx_endereco,
                       (g.id_endereco IS NOT NULL) AS geocodificado,
                       lo.no_categoria, lo.tx_descricao
                FROM investigacao.loc_tb01_localizacoes lo
                JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = lo.id_extracao
                {_JOIN_GEO_LOCALIZACAO}
                WHERE {where}
                ORDER BY {order_by}
                LIMIT %s OFFSET %s
                """,
                params + [limite, offset],
            )
            colunas = [d.name for d in cur.description]
            itens = [dict(zip(colunas, linha)) for linha in cur.fetchall()]

    return {"total": total, "pagina": pagina, "limite": limite, "itens": itens}


# Grade de agregação do grafo de localizações, em graus, do mais grosso ao mais fino. O nível é
# escolhido pelo zoom do mapa na tela: quanto mais fundo o zoom, menor a célula e mais nós aparecem —
# é isso que faz um "cluster" se abrir em vários lugares conforme se aproxima.
_NIVEIS_GRAFO_LOCALIZACAO = [2.0, 0.5, 0.1, 0.02, 0.005, 0.001, 0.0002]
# teto de nós devolvidos por requisição (os maiores primeiro) — segura a tela quando o recorte pega
# uma região densa demais para o nível pedido
_LIMITE_NOS_GRAFO = 300
# teto de arestas (as mais repetidas primeiro). Num nível fino a malha de deslocamentos chega a milhares
# de trajetos percorridos uma única vez, que só emborrachariam a tela e escondem a rotina.
_LIMITE_ARESTAS_GRAFO = 500
# intervalo máximo entre dois registros consecutivos para eles contarem como um deslocamento. Acima
# disso o aparelho só reapareceu noutro lugar depois de muito tempo, o que não é uma viagem observada.
_INTERVALO_MAX_DESLOCAMENTO = "24 hours"


def grafo_localizacoes(
    id_inquerito: int,
    id_extracao: int | None = None,
    busca: str | None = None,
    app: str | None = None,
    categoria: str | None = None,
    data_de: str | None = None,
    data_ate: str | None = None,
    periodo: str | None = None,
    nivel: int = 0,
    lat_min: float | None = None,
    lat_max: float | None = None,
    lon_min: float | None = None,
    lon_max: float | None = None,
    id_camada: int | None = None,
    id_feicao: int | None = None,
    ids_extracao: list[int] | None = None,
) -> dict:
    """Agrega as localizações num grafo de lugares para o nível de detalhe pedido.

    Os pontos são agrupados em células de grade de _NIVEIS_GRAFO_LOCALIZACAO[nivel] graus: cada célula
    com registro vira um nó (com contagem, período, app dominante e endereço representativo). As arestas
    são deslocamentos — registros consecutivos no tempo, dentro da mesma extração, que caem em células
    diferentes — com a contagem de vezes que o trajeto se repetiu.

    lat_min/lat_max/lon_min/lon_max recortam os nós à área visível do mapa (as arestas que saem dela são
    descartadas, porque só se desenha o que tem as duas pontas na tela). id_camada/id_feicao recortam
    ao que cai dentro de uma área importada de shapefile — diferente do recorte da tela, este vale
    para o grafo inteiro, inclusive as arestas e o enquadramento. Os filtros são os mesmos da
    tabela, para as duas visões mostrarem sempre o mesmo conjunto.

    Devolve {nivel, passo, nos, arestas, total_registros, total_celulas, foco}."""
    nivel = max(0, min(len(_NIVEIS_GRAFO_LOCALIZACAO) - 1, nivel))
    passo = _NIVEIS_GRAFO_LOCALIZACAO[nivel]

    filtros, params = _where_localizacao(
        id_inquerito, id_extracao, busca, app, categoria, True, data_de, data_ate, periodo,
        id_camada, id_feicao, ids_extracao
    )
    where = " AND ".join(filtros)

    # CTE compartilhada por nós e arestas: já traz a célula da grade e o horário local de Brasília
    # (usado tanto para o filtro de período quanto para a detecção de âncoras).
    base = f"""
        WITH base AS (
            SELECT lo.id_extracao,
                   lo.dt_localizacao,
                   (lo.dt_localizacao AT TIME ZONE 'UTC' AT TIME ZONE 'America/Sao_Paulo') AS dt_local,
                   NULLIF(lo.no_aplicativo, '') AS no_aplicativo,
                   lo.vl_latitude, lo.vl_longitude,
                   COALESCE(NULLIF(lo.tx_endereco, ''), g.tx_endereco) AS tx_endereco,
                   floor(lo.vl_latitude / %s)::bigint  AS cy,
                   floor(lo.vl_longitude / %s)::bigint AS cx
            FROM investigacao.loc_tb01_localizacoes lo
            JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = lo.id_extracao
            {_JOIN_GEO_LOCALIZACAO}
            WHERE {where}
        )
    """
    params_base = [passo, passo] + params

    # recorte pela área visível do mapa, aplicado só aos nós
    filtro_bbox, params_bbox = "", []
    for coluna, valor, operador in (
        ("vl_latitude", lat_min, ">="), ("vl_latitude", lat_max, "<="),
        ("vl_longitude", lon_min, ">="), ("vl_longitude", lon_max, "<="),
    ):
        if valor is not None:
            filtro_bbox += f" AND {coluna} {operador} %s"
            params_bbox.append(valor)

    with get_connection() as conn:
        with conn.cursor() as cur:
            # ---- nós: uma linha por célula da grade ----
            cur.execute(
                base + f"""
                SELECT cy, cx,
                       count(*)                                     AS qt_registros,
                       avg(vl_latitude)::float                      AS vl_latitude,
                       avg(vl_longitude)::float                     AS vl_longitude,
                       min(dt_localizacao)                          AS dt_primeira,
                       max(dt_localizacao)                          AS dt_ultima,
                       count(DISTINCT dt_local::date)               AS qt_dias,
                       mode() WITHIN GROUP (ORDER BY no_aplicativo)  AS no_aplicativo,
                       count(DISTINCT no_aplicativo)                AS qt_apps,
                       mode() WITHIN GROUP (ORDER BY tx_endereco)    AS tx_endereco,
                       -- ponto exato mais frequente da célula. O centroide não serve para geocodificar:
                       -- o cache de endereços casa por coordenada arredondada a 5 casas, e a média de
                       -- vários pontos não coincide com nenhum registro real — não voltaria no JOIN.
                       -- Como o par tem de vir do MESMO ponto, o mode() é sobre "lat,lon" concatenado.
                       mode() WITHIN GROUP (
                           ORDER BY round(vl_latitude, 5)::text || ',' || round(vl_longitude, 5)::text
                       )                                             AS ponto_ref,
                       -- madrugadas distintas: o lugar onde se dorme é candidato a residência
                       count(DISTINCT CASE WHEN EXTRACT(HOUR FROM dt_local) < 6
                                           THEN dt_local::date END) AS qt_noites,
                       -- dias úteis em horário comercial: candidato a trabalho
                       count(DISTINCT CASE WHEN EXTRACT(ISODOW FROM dt_local) <= 5
                                            AND EXTRACT(HOUR FROM dt_local) BETWEEN 9 AND 17
                                           THEN dt_local::date END) AS qt_dias_uteis,
                       count(*) OVER ()                             AS total_celulas
                FROM base
                WHERE true {filtro_bbox}
                GROUP BY cy, cx
                ORDER BY qt_registros DESC
                LIMIT %s
                """,
                params_base + params_bbox + [_LIMITE_NOS_GRAFO],
            )
            colunas = [d.name for d in cur.description]
            nos = [dict(zip(colunas, linha)) for linha in cur.fetchall()]

            # count(*) OVER () conta as linhas do resultado agrupado, ou seja, quantas células existem
            # antes do LIMIT — serve para a tela avisar que há mais lugares fora do recorte
            total_celulas = nos[0].pop("total_celulas") if nos else 0
            for no in nos:
                no.pop("total_celulas", None)
                no["id_no"] = f"{no['cy']}:{no['cx']}"
                lat_ref, _, lon_ref = (no.pop("ponto_ref", None) or "").partition(",")
                no["vl_latitude_ref"] = float(lat_ref) if lat_ref else None
                no["vl_longitude_ref"] = float(lon_ref) if lon_ref else None

            # ---- arestas: deslocamentos entre células consecutivos no tempo ----
            cur.execute(
                base + f"""
                , seq AS (
                    SELECT cy, cx, dt_localizacao,
                           lag(cy)              OVER w AS cy_ant,
                           lag(cx)              OVER w AS cx_ant,
                           lag(dt_localizacao)  OVER w AS dt_ant
                    FROM base
                    WHERE dt_localizacao IS NOT NULL
                    WINDOW w AS (PARTITION BY id_extracao ORDER BY dt_localizacao, cy, cx)
                )
                SELECT cy_ant, cx_ant, cy, cx,
                       count(*)                                                   AS qt,
                       (percentile_cont(0.5) WITHIN GROUP (
                            ORDER BY EXTRACT(EPOCH FROM (dt_localizacao - dt_ant))
                       ) / 60)::int                                               AS qt_minutos_mediana
                FROM seq
                WHERE dt_ant IS NOT NULL
                  AND (cy_ant, cx_ant) IS DISTINCT FROM (cy, cx)
                  AND dt_localizacao - dt_ant <= INTERVAL '{_INTERVALO_MAX_DESLOCAMENTO}'
                GROUP BY cy_ant, cx_ant, cy, cx
                ORDER BY qt DESC
                """,
                params_base,
            )
            arestas_brutas = cur.fetchall()

            # ---- foco: recorte que concentra a maior parte dos registros ----
            # os outliers (um registro no exterior) esticariam o enquadramento inicial até o mundo
            # inteiro e esconderiam a área de interesse; por isso o primeiro enquadramento usa os
            # percentis 2/98 e não o mínimo/máximo absolutos. Os pontos de fora continuam no grafo.
            cur.execute(
                base + """
                SELECT percentile_cont(0.02) WITHIN GROUP (ORDER BY vl_latitude)::float,
                       percentile_cont(0.98) WITHIN GROUP (ORDER BY vl_latitude)::float,
                       percentile_cont(0.02) WITHIN GROUP (ORDER BY vl_longitude)::float,
                       percentile_cont(0.98) WITHIN GROUP (ORDER BY vl_longitude)::float,
                       count(*)
                FROM base
                """,
                params_base,
            )
            p_lat_min, p_lat_max, p_lon_min, p_lon_max, total_registros = cur.fetchone()

    # só sobrevivem as arestas com as duas pontas entre os nós devolvidos (a busca já veio ordenada
    # pelas mais repetidas, então o corte no teto descarta os trajetos de uma vez só)
    ids = {no["id_no"] for no in nos}
    arestas = []
    for cy_ant, cx_ant, cy, cx, qt, minutos in arestas_brutas:
        origem, destino = f"{cy_ant}:{cx_ant}", f"{cy}:{cx}"
        if origem in ids and destino in ids:
            arestas.append({"origem": origem, "destino": destino, "qt": qt, "qt_minutos_mediana": minutos})
            if len(arestas) >= _LIMITE_ARESTAS_GRAFO:
                break

    _marcar_ancoras(nos)

    return {
        "nivel": nivel,
        "passo": passo,
        "nos": nos,
        "arestas": arestas,
        "total_registros": total_registros or 0,
        "total_celulas": total_celulas,
        "foco": (
            {"lat_min": p_lat_min, "lat_max": p_lat_max, "lon_min": p_lon_min, "lon_max": p_lon_max}
            if p_lat_min is not None else None
        ),
    }


# nº mínimo de dias distintos para um lugar ser apontado como residência/trabalho — abaixo disso é
# coincidência de poucos registros, não rotina
_MIN_DIAS_ANCORA = 3


def _marcar_ancoras(nos: list[dict]) -> None:
    """Aponta, entre os nós, o candidato a residência e o a trabalho (campo tp_ancora), pela rotina.

    Residência = onde há mais madrugadas distintas; trabalho = onde há mais dias úteis distintos em
    horário comercial, excluído o já marcado como residência. É indício para orientar o exame, não
    conclusão: só marca com pelo menos _MIN_DIAS_ANCORA dias e o rótulo diz "provável"."""
    if not nos:
        return
    candidatos_noite = [n for n in nos if (n.get("qt_noites") or 0) >= _MIN_DIAS_ANCORA]
    residencia = max(candidatos_noite, key=lambda n: n["qt_noites"], default=None)
    if residencia:
        residencia["tp_ancora"] = "residencia"

    candidatos_dia = [
        n for n in nos
        if (n.get("qt_dias_uteis") or 0) >= _MIN_DIAS_ANCORA and n.get("tp_ancora") != "residencia"
    ]
    trabalho = max(candidatos_dia, key=lambda n: n["qt_dias_uteis"], default=None)
    if trabalho:
        trabalho["tp_ancora"] = "trabalho"


def facetas_localizacao(id_inquerito: int, id_extracao: int | None = None) -> dict:
    """Valores distintos (apps e categorias) para popular os selects de filtro da página de localizações."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT DISTINCT lo.no_aplicativo
                   FROM investigacao.loc_tb01_localizacoes lo
                   JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = lo.id_extracao
                   WHERE ex.id_inquerito = %s AND (%s::bigint IS NULL OR lo.id_extracao = %s)
                     AND lo.no_aplicativo IS NOT NULL AND lo.no_aplicativo <> ''
                   ORDER BY lo.no_aplicativo""",
                (id_inquerito, id_extracao, id_extracao),
            )
            apps = [linha[0] for linha in cur.fetchall()]
            cur.execute(
                """SELECT DISTINCT lo.no_categoria
                   FROM investigacao.loc_tb01_localizacoes lo
                   JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = lo.id_extracao
                   WHERE ex.id_inquerito = %s AND (%s::bigint IS NULL OR lo.id_extracao = %s)
                     AND lo.no_categoria IS NOT NULL AND lo.no_categoria <> ''
                   ORDER BY lo.no_categoria""",
                (id_inquerito, id_extracao, id_extracao),
            )
            categorias = [linha[0] for linha in cur.fetchall()]
    return {"apps": apps, "categorias": categorias}


# ordenações permitidas na listagem de notificações (whitelist — o valor cru nunca entra no SQL)
_ORDENACOES_NOTIFICACAO = {
    "recentes": "n.dt_notificacao DESC NULLS LAST, n.id_notificacao",
    "antigos": "n.dt_notificacao ASC NULLS LAST, n.id_notificacao",
    "app": "n.no_pacote_app NULLS LAST, n.dt_notificacao DESC NULLS LAST",
    "app_desc": "n.no_pacote_app DESC NULLS LAST, n.dt_notificacao DESC NULLS LAST",
}


def listar_notificacoes(
    id_inquerito: int,
    id_extracao: int | None = None,
    busca: str | None = None,
    app: str | None = None,
    data_de: str | None = None,
    data_ate: str | None = None,
    periodo: str | None = None,
    ordenar: str | None = None,
    pagina: int = 1,
    limite: int = 100,
) -> dict:
    """Lista, paginado, as notificações de um inquérito, mais recente primeiro.

    id_extracao opcional restringe a uma única extração (um aparelho) do inquérito.
    busca filtra por pacote app, conteúdo ou app de log.
    app filtra por no_pacote_app.
    data_de/data_ate ('AAAA-MM-DD') e periodo (madrugada/manha/tarde/noite) filtram por data/hora.
    ordenar ∈ _ORDENACOES_NOTIFICACAO (default: recentes). Devolve {total, pagina, limite, itens}."""
    pagina = max(1, pagina)
    limite = max(1, min(LIMITE_MAX_LISTAGEM, limite))
    offset = (pagina - 1) * limite

    filtros, params = _where_notificacao(id_inquerito, id_extracao, busca, app, data_de, data_ate, periodo)
    where = " AND ".join(filtros)
    order_by = _ORDENACOES_NOTIFICACAO.get(ordenar or "", _ORDENACOES_NOTIFICACAO["recentes"])

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT count(*)
                    FROM investigacao.ntf_tb01_notificacoes n
                    JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = n.id_extracao
                    WHERE {where}""",
                params,
            )
            total = cur.fetchone()[0]

            cur.execute(
                f"""SELECT n.id_notificacao, n.no_aplicativo_log, n.no_pacote_app, n.tx_conteudo, n.dt_notificacao
                    FROM investigacao.ntf_tb01_notificacoes n
                    JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = n.id_extracao
                    WHERE {where}
                    ORDER BY {order_by}
                    LIMIT %s OFFSET %s""",
                params + [limite, offset],
            )
            colunas = [d.name for d in cur.description]
            itens = [dict(zip(colunas, linha)) for linha in cur.fetchall()]

    return {"total": total, "pagina": pagina, "limite": limite, "itens": itens}


def _where_notificacao(
    id_inquerito: int, id_extracao: int | None, busca: str | None, app: str | None,
    data_de: str | None, data_ate: str | None, periodo: str | None,
) -> tuple[list[str], list]:
    """Monta a cláusula WHERE (filtros + params) para a listagem de notificações."""
    filtros = ["ex.id_inquerito = %s"]
    params: list = [id_inquerito]
    if id_extracao is not None:
        filtros.append("n.id_extracao = %s")
        params.append(id_extracao)
    if app:
        filtros.append("n.no_pacote_app = %s")
        params.append(app)
    if data_de and _RE_DATA_ISO.match(data_de):
        filtros.append("n.dt_notificacao >= %s::date")
        params.append(data_de)
    if data_ate and _RE_DATA_ISO.match(data_ate):
        filtros.append("n.dt_notificacao < (%s::date + 1)")
        params.append(data_ate)
    if periodo in _PERIODOS_ARQUIVO:
        hora_ini, hora_fim = _PERIODOS_ARQUIVO[periodo]
        filtros.append(f"{_hora_local_sql('n.dt_notificacao')} BETWEEN %s AND %s")
        params.extend([hora_ini, hora_fim])
    if busca:
        termo = f"%{busca.strip()}%"
        filtros.append(
            "(n.no_pacote_app ILIKE %s OR n.tx_conteudo ILIKE %s OR n.no_aplicativo_log ILIKE %s)"
        )
        params.extend([termo, termo, termo])
    return filtros, params


def facetas_notificacao(id_inquerito: int, id_extracao: int | None = None) -> dict:
    """Valores distintos (apps) para popular os selects de filtro da página de notificações."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT DISTINCT n.no_pacote_app
                   FROM investigacao.ntf_tb01_notificacoes n
                   JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = n.id_extracao
                   WHERE ex.id_inquerito = %s AND (%s::bigint IS NULL OR n.id_extracao = %s)
                     AND n.no_pacote_app IS NOT NULL AND n.no_pacote_app <> ''
                   ORDER BY n.no_pacote_app""",
                (id_inquerito, id_extracao, id_extracao),
            )
            apps = [linha[0] for linha in cur.fetchall()]
    return {"apps": apps}


def _chave_anexo(nome: str) -> str:
    """Nome de anexo reduzido ao que dá para comparar com ARQ_TB01_ARQUIVOS: só o nome do arquivo
    (o Cellebrite às vezes grava o caminho junto), sem espaço em volta e em minúsculas."""
    return nome.replace("\\", "/").rsplit("/", 1)[-1].strip().lower()


def resolver_anexos_por_nome(registros: list[dict], campo: str = "tx_anexos",
                             saida: str = "anexos") -> None:
    """Liga os anexos gravados como texto (nomes separados por vírgula) aos arquivos do mesmo
    aparelho, para a tela poder abrir o binário em vez de só mostrar o nome.

    Notas e atividade de rede social guardam os anexos assim porque é só isso que o Cellebrite dá
    nesses modelos — sem o file_id que os anexos de mensagem têm (ver
    _resolver_anexos_por_file_id) —, então a ligação é feita pelo nome, dentro da MESMA extração:
    nome igual em dois aparelhos é outro arquivo. A comparação ignora maiúsculas e o caminho que
    às vezes vem junto do nome; quando o mesmo nome existe mais de uma vez no aparelho, ganha o
    que tem binário no .ufdr.

    Preenche cada registro com `saida` = [{nome, id_arquivo, no_categoria, disponivel}]; anexo que
    não casou com arquivo nenhum fica com id_arquivo None, e a tela mostra o nome sem link. Os
    registros precisam trazer id_extracao."""
    pares: set[tuple[int, str]] = set()
    for registro in registros:
        registro[saida] = [
            {"nome": nome, "id_arquivo": None, "no_categoria": None, "disponivel": False}
            for nome in (n.strip() for n in (registro.get(campo) or "").split(","))
            if nome
        ]
        for anexo in registro[saida]:
            chave = _chave_anexo(anexo["nome"])
            if chave:
                pares.add((registro["id_extracao"], chave))
    if not pares:
        return

    mapa: dict[tuple[int, str], dict] = {}
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT a.id_extracao, lower(a.no_arquivo), a.id_arquivo, a.no_categoria,
                          (a.co_caminho_zip IS NOT NULL)
                   FROM investigacao.arq_tb01_arquivos a
                   WHERE (a.id_extracao, lower(a.no_arquivo)) IN (
                       SELECT unnest(%s::bigint[]), unnest(%s::text[])
                   )
                   ORDER BY (a.co_caminho_zip IS NOT NULL) DESC, a.id_arquivo""",
                ([p[0] for p in pares], [p[1] for p in pares]),
            )
            for id_ext, nome, id_arquivo, categoria, disponivel in cur.fetchall():
                # ordenado com o melhor candidato primeiro; o resto do grupo é descartado
                mapa.setdefault((id_ext, nome), {"id_arquivo": id_arquivo,
                                                 "no_categoria": categoria,
                                                 "disponivel": disponivel})

    for registro in registros:
        for anexo in registro[saida]:
            achado = mapa.get((registro["id_extracao"], _chave_anexo(anexo["nome"])))
            if achado:
                anexo.update(achado)


def listar_notas(id_inquerito: int, id_extracao: int | None = None) -> list[dict]:
    """Lista as notas de blocos de notas (Samsung Notes, Apple Notes/iCloud Backup, Google Keep…)
    de um inquérito, mais recente primeiro (modificação, caindo para criação quando ausente).

    id_extracao opcional restringe a uma única extração (um aparelho) do inquérito.

    Cada nota devolve também ``anexos``, com o nome de cada anexo já resolvido para o arquivo
    correspondente do aparelho (ver resolver_anexos_por_nome)."""
    query = """
        SELECT n.id_nota, n.no_aplicativo, n.tx_titulo, n.tx_corpo, n.dt_criacao, n.dt_modificacao,
               n.cd_status, n.tx_anexos, n.id_extracao
        FROM investigacao.not_tb01_notas n
        JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = n.id_extracao
        WHERE ex.id_inquerito = %s AND (%s::bigint IS NULL OR n.id_extracao = %s)
        ORDER BY COALESCE(n.dt_modificacao, n.dt_criacao) DESC NULLS LAST
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (id_inquerito, id_extracao, id_extracao))
            colunas = [d.name for d in cur.description]
            notas = [dict(zip(colunas, linha)) for linha in cur.fetchall()]

    resolver_anexos_por_nome(notas)
    return notas


# ordenações permitidas no histórico de navegação (whitelist — o valor cru nunca entra no SQL)
_ORDENACOES_NAVEGACAO = {
    "recentes": "p.dt_visita DESC NULLS LAST, p.id_pagina",
    "antigos": "p.dt_visita ASC NULLS LAST, p.id_pagina",
    "visitas": "p.qt_visitas DESC NULLS LAST, p.id_pagina",
    "titulo": "p.tx_titulo NULLS LAST, p.id_pagina",
}

# host da URL (sem protocolo, caminho, query ou "www.") — usado para agrupar por site
_SQL_DOMINIO_NAV = (
    "regexp_replace(regexp_replace(regexp_replace(lower(coalesce(p.tx_url, '')), "
    "'^[a-zA-Z][a-zA-Z0-9+.-]*://', ''), '[/?#].*$', ''), '^www\\.', '')"
)


def _filtros_navegacao(
    id_extracao: int | None,
    busca: str | None,
    navegador: str | None,
    data_de: str | None,
    data_ate: str | None,
    periodo: str | None,
) -> tuple[list[str], list]:
    """Monta a cláusula WHERE (filtros comuns) usada tanto na listagem quanto no resumo/KPIs
    do histórico de navegação. Devolve (lista de condições, parâmetros)."""
    filtros = ["ex.id_inquerito = %s"]
    params: list = []
    if id_extracao is not None:
        filtros.append("p.id_extracao = %s")
        params.append(id_extracao)
    if navegador:
        filtros.append("p.no_aplicativo = %s")
        params.append(navegador)
    if data_de and _RE_DATA_ISO.match(data_de):
        filtros.append("p.dt_visita >= %s::date")
        params.append(data_de)
    if data_ate and _RE_DATA_ISO.match(data_ate):
        filtros.append("p.dt_visita < (%s::date + 1)")   # inclui o dia inteiro de data_ate
        params.append(data_ate)
    if periodo in _PERIODOS_ARQUIVO:
        hora_ini, hora_fim = _PERIODOS_ARQUIVO[periodo]
        filtros.append(f"{_hora_local_sql('p.dt_visita')} BETWEEN %s AND %s")
        params.extend([hora_ini, hora_fim])
    if busca:
        termo = f"%{busca.strip()}%"
        filtros.append("(p.tx_titulo ILIKE %s OR p.tx_url ILIKE %s OR p.tx_conta ILIKE %s)")
        params.extend([termo, termo, termo])
    return filtros, params


def listar_paginas_visitadas(
    id_inquerito: int,
    id_extracao: int | None = None,
    busca: str | None = None,
    navegador: str | None = None,
    data_de: str | None = None,
    data_ate: str | None = None,
    periodo: str | None = None,
    ordenar: str | None = None,
    pagina: int = 1,
    limite: int = 100,
) -> dict:
    """Lista, paginado, o histórico de navegação (páginas visitadas) de um inquérito.

    Filtros: id_extracao (aparelho), busca (título/URL/conta), navegador (no_aplicativo),
    data_de/data_ate ('AAAA-MM-DD', sobre a data da visita), periodo ∈ _PERIODOS_ARQUIVO
    (faixa de horário local). ordenar ∈ _ORDENACOES_NAVEGACAO (default 'recentes').

    Devolve {total, pagina, limite, itens}."""
    pagina = max(1, pagina)
    limite = max(1, min(LIMITE_MAX_LISTAGEM, limite))
    offset = (pagina - 1) * limite

    filtros, params = _filtros_navegacao(id_extracao, busca, navegador, data_de, data_ate, periodo)
    params = [id_inquerito] + params
    where = " AND ".join(filtros)
    order_by = _ORDENACOES_NAVEGACAO.get(ordenar or "", _ORDENACOES_NAVEGACAO["recentes"])

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT count(*)
                    FROM investigacao.nav_tb01_paginas_visitadas p
                    JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = p.id_extracao
                    WHERE {where}""",
                params,
            )
            total = cur.fetchone()[0]

            cur.execute(
                f"""
                SELECT p.id_pagina, p.no_aplicativo, p.tx_titulo, p.tx_url, p.dt_visita, p.qt_visitas, p.tx_conta
                FROM investigacao.nav_tb01_paginas_visitadas p
                JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = p.id_extracao
                WHERE {where}
                ORDER BY {order_by}
                LIMIT %s OFFSET %s
                """,
                params + [limite, offset],
            )
            colunas = [d.name for d in cur.description]
            itens = [dict(zip(colunas, linha)) for linha in cur.fetchall()]

    return {"total": total, "pagina": pagina, "limite": limite, "itens": itens}


def facetas_navegacao(id_inquerito: int, id_extracao: int | None = None) -> dict:
    """Navegadores distintos para popular o select de filtro da página de histórico de navegação."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT DISTINCT p.no_aplicativo
                   FROM investigacao.nav_tb01_paginas_visitadas p
                   JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = p.id_extracao
                   WHERE ex.id_inquerito = %s AND (%s::bigint IS NULL OR p.id_extracao = %s)
                     AND p.no_aplicativo IS NOT NULL AND p.no_aplicativo <> ''
                   ORDER BY p.no_aplicativo""",
                (id_inquerito, id_extracao, id_extracao),
            )
            navegadores = [linha[0] for linha in cur.fetchall()]
    return {"navegadores": navegadores}


def resumo_navegacao(
    id_inquerito: int,
    id_extracao: int | None = None,
    busca: str | None = None,
    navegador: str | None = None,
    data_de: str | None = None,
    data_ate: str | None = None,
    periodo: str | None = None,
) -> dict:
    """KPIs do histórico de navegação de um inquérito, respeitando os mesmos filtros da listagem
    (ver _filtros_navegacao). Agrega no servidor pois o histórico pode ter dezenas de milhares de
    registros.

    Devolve {total_registros, total_visitas, sites_unicos, total_navegadores, total_contas,
    primeira_visita, ultima_visita, top_sites, por_navegador, por_periodo_dia}."""
    filtros, params_filtro = _filtros_navegacao(id_extracao, busca, navegador, data_de, data_ate, periodo)
    params = [id_inquerito] + params_filtro
    where = " AND ".join(filtros)
    base_from = """
        FROM investigacao.nav_tb01_paginas_visitadas p
        JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = p.id_extracao
        WHERE {where}
    """.format(where=where)

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT
                    count(*),
                    coalesce(sum(coalesce(p.qt_visitas, 1)), 0),
                    count(DISTINCT {_SQL_DOMINIO_NAV}) FILTER (WHERE {_SQL_DOMINIO_NAV} <> ''),
                    count(DISTINCT p.no_aplicativo) FILTER (WHERE p.no_aplicativo IS NOT NULL AND p.no_aplicativo <> ''),
                    count(DISTINCT p.tx_conta) FILTER (WHERE p.tx_conta IS NOT NULL AND p.tx_conta <> ''),
                    min(p.dt_visita),
                    max(p.dt_visita)
                {base_from}
                """,
                params,
            )
            (
                total_registros, total_visitas, sites_unicos, total_navegadores,
                total_contas, primeira_visita, ultima_visita,
            ) = cur.fetchone()

            cur.execute(
                f"""
                SELECT {_SQL_DOMINIO_NAV} AS dominio, count(*) AS paginas,
                       coalesce(sum(coalesce(p.qt_visitas, 1)), 0) AS visitas
                {base_from}
                  AND {_SQL_DOMINIO_NAV} <> ''
                GROUP BY 1
                ORDER BY visitas DESC, paginas DESC
                LIMIT 12
                """,
                params,
            )
            colunas = [d.name for d in cur.description]
            top_sites = [dict(zip(colunas, linha)) for linha in cur.fetchall()]

            cur.execute(
                f"""
                SELECT coalesce(nullif(p.no_aplicativo, ''), 'Não identificado') AS navegador,
                       count(*) AS registros,
                       coalesce(sum(coalesce(p.qt_visitas, 1)), 0) AS visitas
                {base_from}
                GROUP BY 1
                ORDER BY registros DESC
                LIMIT 12
                """,
                params,
            )
            colunas = [d.name for d in cur.description]
            por_navegador = [dict(zip(colunas, linha)) for linha in cur.fetchall()]

            cur.execute(
                f"""
                SELECT
                    CASE
                        WHEN hora BETWEEN 0 AND 5 THEN 'madrugada'
                        WHEN hora BETWEEN 6 AND 11 THEN 'manha'
                        WHEN hora BETWEEN 12 AND 17 THEN 'tarde'
                        ELSE 'noite'
                    END AS periodo_dia,
                    count(*) AS registros
                FROM (
                    SELECT {_hora_local_sql('p.dt_visita')} AS hora
                    {base_from}
                      AND p.dt_visita IS NOT NULL
                ) t
                GROUP BY 1
                """,
                params,
            )
            por_periodo_dia = {linha[0]: linha[1] for linha in cur.fetchall()}

    return {
        "total_registros": total_registros,
        "total_visitas": total_visitas,
        "sites_unicos": sites_unicos,
        "total_navegadores": total_navegadores,
        "total_contas": total_contas,
        "primeira_visita": primeira_visita,
        "ultima_visita": ultima_visita,
        "top_sites": top_sites,
        "por_navegador": por_navegador,
        "por_periodo_dia": por_periodo_dia,
    }


def listar_aplicativos(
    id_inquerito: int,
    id_extracao: int | None = None,
    busca: str | None = None,
    pagina: int = 1,
    limite: int = 100,
) -> dict:
    """Lista, paginado, os aplicativos instalados de um inquérito.

    id_extracao opcional restringe a uma única extração (um aparelho) do inquérito.
    busca filtra por nome do aplicativo ou pacote.
    Devolve {total, pagina, limite, itens}."""
    pagina = max(1, pagina)
    limite = max(1, min(LIMITE_MAX_LISTAGEM, limite))
    offset = (pagina - 1) * limite

    with get_connection() as conn:
        with conn.cursor() as cur:
            where_parts = [
                "ex.id_inquerito = %s",
                "(%s::bigint IS NULL OR a.id_extracao = %s)",
            ]
            params = [id_inquerito, id_extracao, id_extracao]

            if busca:
                termo = f"%{busca.strip()}%"
                where_parts.append(
                    "(a.no_aplicativo ILIKE %s OR a.nu_pacote ILIKE %s)"
                )
                params.extend([termo, termo])

            where = " AND ".join(where_parts)

            cur.execute(
                f"""SELECT count(*)
                    FROM investigacao.app_tb01_aplicativos a
                    JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = a.id_extracao
                    WHERE {where}""",
                params,
            )
            total = cur.fetchone()[0]

            cur.execute(
                f"""SELECT a.id_aplicativo, a.nu_pacote, a.no_aplicativo, a.nu_versao,
                           a.tx_descricao, a.dt_instalacao, a.dt_ultimo_uso
                    FROM investigacao.app_tb01_aplicativos a
                    JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = a.id_extracao
                    WHERE {where}
                    ORDER BY a.nu_pacote NULLS LAST
                    LIMIT %s OFFSET %s""",
                params + [limite, offset],
            )
            colunas = [d.name for d in cur.description]
            itens = [dict(zip(colunas, linha)) for linha in cur.fetchall()]

    return {"total": total, "pagina": pagina, "limite": limite, "itens": itens}


_RE_DATA_ISO_EMAIL = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _filtros_emails(
    id_extracao: int | None,
    busca: str | None,
    fonte: str | None,
    remetente: str | None,
    data_de: str | None,
    data_ate: str | None,
) -> tuple[list[str], list]:
    """Monta a cláusula WHERE (filtros comuns) usada na listagem de e-mails.
    Devolve (lista de condições, parâmetros)."""
    filtros = ["ex.id_inquerito = %s"]
    params: list = []
    if id_extracao is not None:
        filtros.append("m.id_extracao = %s")
        params.append(id_extracao)
    if fonte:
        filtros.append("m.no_fonte = %s")
        params.append(fonte)
    if remetente:
        filtros.append("m.no_remetente = %s")
        params.append(remetente)
    if data_de and _RE_DATA_ISO_EMAIL.match(data_de):
        filtros.append("m.dt_email >= %s::date")
        params.append(data_de)
    if data_ate and _RE_DATA_ISO_EMAIL.match(data_ate):
        filtros.append("m.dt_email < (%s::date + 1)")
        params.append(data_ate)
    if busca:
        termo = f"%{busca.strip()}%"
        filtros.append(
            "(m.tx_assunto ILIKE %s OR m.no_remetente ILIKE %s OR "
            "m.tx_destinatarios ILIKE %s OR m.tx_corpo ILIKE %s)"
        )
        params.extend([termo, termo, termo, termo])
    return filtros, params


# ordenações permitidas na listagem de e-mails (whitelist — o valor cru nunca entra no SQL)
_ORDENACOES_EMAIL = {
    "recentes": "m.dt_email DESC NULLS LAST, m.id_email DESC",
    "antigos": "m.dt_email ASC NULLS LAST, m.id_email",
    "assunto": "m.tx_assunto NULLS LAST, m.dt_email DESC NULLS LAST",
    "assunto_desc": "m.tx_assunto DESC NULLS LAST, m.dt_email DESC NULLS LAST",
    "remetente": "m.no_remetente NULLS LAST, m.dt_email DESC NULLS LAST",
    "remetente_desc": "m.no_remetente DESC NULLS LAST, m.dt_email DESC NULLS LAST",
}


# Prévia do corpo que a LISTAGEM de e-mails transporta (o integral fica no modal "Ver HTML",
# servido por obter_corpo_email). Desde que a importação do IPED passou a gravar o corpo
# INTEGRAL (antes o índice só tinha ~132 caracteres por e-mail), a lista completa de uma página
# chegava a dezenas de MB e a coluna "Conteúdo" esticava a linha inteira.
PREVIEW_CORPO_EMAIL = 700


def listar_emails(
    id_inquerito: int,
    id_extracao: int | None = None,
    busca: str | None = None,
    fonte: str | None = None,
    remetente: str | None = None,
    data_de: str | None = None,
    data_ate: str | None = None,
    ordenar: str | None = None,
    pagina: int = 1,
    limite: int = 100,
) -> dict:
    """Lista, paginado, os e-mails extraídos dos aparelhos do inquérito.

    Filtros: id_extracao (aparelho), busca (assunto/remetente/destinatários/corpo),
    fonte (no_fonte), remetente (no_remetente), data_de/data_ate ('AAAA-MM-DD').
    ordenar ∈ _ORDENACOES_EMAIL (default: recentes). Paginação: ?pagina= e ?limite=.

    Devolve {total, pagina, limite, itens}."""
    pagina = max(1, pagina)
    limite = max(1, min(LIMITE_MAX_LISTAGEM, limite))
    offset = (pagina - 1) * limite

    filtros, params = _filtros_emails(id_extracao, busca, fonte, remetente, data_de, data_ate)
    params = [id_inquerito] + params
    where = " AND ".join(filtros)
    order_by = _ORDENACOES_EMAIL.get(ordenar or "", _ORDENACOES_EMAIL["recentes"])

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT count(*)
                    FROM investigacao.eml_tb01_emails m
                    JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = m.id_extracao
                    WHERE {where}""",
                params,
            )
            total = cur.fetchone()[0]

            cur.execute(
                f"""SELECT m.id_email, m.no_fonte, m.no_remetente, m.tx_destinatarios, m.tx_cc,
                          m.tx_cco, m.tx_assunto, left(m.tx_corpo, {PREVIEW_CORPO_EMAIL}) AS tx_corpo,
                          length(m.tx_corpo) > {PREVIEW_CORPO_EMAIL} AS corpo_truncado,
                          m.dt_email, m.tp_modelo
                   FROM investigacao.eml_tb01_emails m
                   JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = m.id_extracao
                   WHERE {where}
                   ORDER BY {order_by}
                   LIMIT %s OFFSET %s""",
                params + [limite, offset],
            )
            colunas = [d.name for d in cur.description]
            itens = [dict(zip(colunas, linha)) for linha in cur.fetchall()]

    return {"total": total, "pagina": pagina, "limite": limite, "itens": itens}


def obter_corpo_email(id_inquerito: int, id_email: int) -> str | None:
    """Corpo INTEGRAL de um e-mail, para o modal "Ver HTML" da listagem — a lista transporta só
    uma prévia (ver PREVIEW_CORPO_EMAIL). None quando o e-mail não é do inquérito (a rota vira
    404), o que também vale para e-mail inexistente."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT m.tx_corpo
                     FROM investigacao.eml_tb01_emails m
                     JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = m.id_extracao
                    WHERE m.id_email = %s AND ex.id_inquerito = %s""",
                (id_email, id_inquerito),
            )
            row = cur.fetchone()
    return row[0] if row else None


def facetas_emails(id_inquerito: int, id_extracao: int | None = None) -> dict:
    """Fontes e remetentes distintos para popular os selects de filtro da página de e-mails."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT DISTINCT m.no_fonte
                   FROM investigacao.eml_tb01_emails m
                   JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = m.id_extracao
                   WHERE ex.id_inquerito = %s AND (%s::bigint IS NULL OR m.id_extracao = %s)
                     AND m.no_fonte IS NOT NULL AND m.no_fonte <> ''
                   ORDER BY m.no_fonte""",
                (id_inquerito, id_extracao, id_extracao),
            )
            fontes = [linha[0] for linha in cur.fetchall()]

            cur.execute(
                """SELECT DISTINCT m.no_remetente
                   FROM investigacao.eml_tb01_emails m
                   JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = m.id_extracao
                   WHERE ex.id_inquerito = %s AND (%s::bigint IS NULL OR m.id_extracao = %s)
                     AND m.no_remetente IS NOT NULL AND m.no_remetente <> ''
                   ORDER BY m.no_remetente""",
                (id_inquerito, id_extracao, id_extracao),
            )
            remetentes = [linha[0] for linha in cur.fetchall()]

    return {"fontes": fontes, "remetentes": remetentes}


def listar_contas_financeiras(id_inquerito: int, id_extracao: int | None = None) -> list[dict]:
    """Lista contas bancárias/financeiras que o UFDR modelou explicitamente."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT c.id_conta_financeira, c.no_fonte, c.no_instituicao, c.no_titular,
                          c.nu_conta, c.nu_agencia, c.tp_conta, c.no_moeda, c.vl_saldo,
                          c.tp_modelo, c.cd_status_ufdr
                   FROM investigacao.bnc_tb01_contas_financeiras c
                   JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = c.id_extracao
                   WHERE ex.id_inquerito = %s AND (%s::bigint IS NULL OR c.id_extracao = %s)
                   ORDER BY CASE c.tp_conta
                                WHEN 'banco' THEN 1 WHEN 'conta_corrente' THEN 1
                                WHEN 'pagamento' THEN 2 WHEN 'cripto' THEN 3
                                WHEN 'aposta' THEN 4 WHEN 'capitalizacao' THEN 5
                                WHEN 'credito' THEN 6 ELSE 7 END,
                            c.no_instituicao NULLS LAST, c.nu_conta NULLS LAST""",
                (id_inquerito, id_extracao, id_extracao),
            )
            colunas = [d.name for d in cur.description]
            return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


def listar_donos(id_inquerito: int, id_extracao: int | None = None) -> list[dict]:
    """Lista o(s) dono(s)/usuário(s) do aparelho de um inquérito.

    id_extracao opcional restringe a uma única extração (um aparelho) do inquérito."""
    query = """
        SELECT d.id_dono, d.no_dono, d.tp_usuario, d.nu_identificador, d.dt_ultimo_login
        FROM investigacao.don_tb01_donos d
        JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = d.id_extracao
        WHERE ex.id_inquerito = %s AND (%s::bigint IS NULL OR d.id_extracao = %s)
        ORDER BY d.no_dono NULLS LAST
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (id_inquerito, id_extracao, id_extracao))
            colunas = [d.name for d in cur.description]
            return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


# Conjuntos da timeline: recortes temáticos que agrupam categorias de evento, para o investigador
# ver de uma vez só o que responde à mesma pergunta (por onde andou, com quem falou, o que gastou)
# sem marcar categoria por categoria. Um item terminado em '*' casa por prefixo — 'mensagem_*' pega
# todos os mensageiros, cuja categoria é montada com o nome do app ('mensagem_telegram' etc.).
# Fora "investigativo" (que atravessa os temas, definido pelo uso), os conjuntos cobrem todas as
# categorias sem sobreposição.
_CONJUNTOS_TIMELINE = {
    "investigativo": ["ligacao", "mensagem_wpp", "localizacao", "trajeto", "compromisso", "transferencia"],
    "comunicacao": ["ligacao", "mensagem_*", "email"],
    "localizacao": ["localizacao", "compromisso", "trajeto", "antena"],
    "internet": ["navegacao", "busca", "social"],
    "financeiro": ["transferencia"],
    "aparelho": ["notificacao", "nota", "conexao", "arquivo_iped"],
}

# valor de TX_LINHA_TEMPO em formato de data ISO-8601 com Z (o que o timeStamp do IPED grava)
_RE_DATA_ISO_Z = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T.+Z$"

# As 11 colunas que cada ramo da UNION da timeline emite, na ordem. O alias entra na subconsulta
# porque os nomes saem do PRIMEIRO ramo — e quando o filtro descarta justamente o primeiro (geo
# deixa o WhatsApp na frente), os rótulos das fontes trocariam.
COLUNAS_TIMELINE = ("dt_evento, tp_categoria, no_aplicativo, tx_descricao, id_extracao, "
                    "id_referencia, tp_origem, tx_link, vl_lat, vl_lon, tx_dados")

# Total do recorte da timeline, por alguns minutos — a contagem relê o caso inteiro e só
# interessa para a paginação, que pede o MESMO recorte página após página.
_TOTAIS_TIMELINE: dict[tuple, tuple[float, int]] = {}
_CANDADO_TOTAIS_TIMELINE = threading.Lock()
_TTL_TOTAIS_TIMELINE = 300.0


def _timeline_casa_categoria(categoria_ramo: str, filtro: list[str] | None) -> bool:
    """A categoria de um ramo da UNION (exata, ou o padrão de prefixo 'mensagem_*') casa com o
    filtro de categorias/conjuntos (None = todos)? Usado para DESCARTAR RAMOS INTEIROS ainda em
    Python: a timeline de um caso grande passa por centenas de milhares de eventos, e varrer a
    tabela de um mensageiro que o recorte do investigador nunca vai mostrar é o que deixava a
    página de antes levar 6 segundos."""
    if filtro is None:
        return True
    for item in filtro:
        if item.endswith("*"):
            if categoria_ramo.startswith(item[:-1]):
                return True
        elif categoria_ramo.endswith("*"):
            if item.startswith(categoria_ramo[:-1]):
                return True
        elif item == categoria_ramo:
            return True
    return False


def listar_timeline(
    id_inquerito: int,
    id_extracao: int | list[int] | tuple[int, ...] | None = None,
    busca: str | None = None,
    categoria: str | None = None,
    data_de: str | None = None,
    data_ate: str | None = None,
    conjunto: str | None = None,
    periodo: str | None = None,
    somente_geo: bool = False,
    pagina: int = 1,
    limite: int = 200,
    analitico: bool = False,
    apenas_total: bool = False,
    sem_total: bool = False,
) -> dict:
    """Retorna a timeline unificada de todos os eventos do inquérito (ligações, mensagens,
    localizações, notificações, notas, navegação, e-mails, buscas, sociais, compromissos,
    antenas, conexões, trajetos, transferências e arquivos da nuvem), ordenada por data.

    Filtros: id_extracao aceita UM id ou uma LISTA de ids — a tela permite marcar mais de um
    aparelho de uma vez; busca texto livre; categoria em CSV; conjunto (∈ _CONJUNTOS_TIMELINE);
    horário local (∈ _PERIODOS_ARQUIVO); faixa de datas; somente_geo (só eventos com
    coordenada). No conjunto investigativo os feriados importados automaticamente pelo Google
    Calendar (categoria 'Feriados no Brasil' e congêneres) ficam de fora — na agenda do
    investigador eles eram ruído puro. Cada evento traz vl_lat/vl_lon e tx_dados (JSON
    estruturado da origem — duração, valor, participantes etc. — que a tx_descricao achata).

    Performance (a consulta sem filtro passava dos 6s num caso de 860 mil eventos): os ramos da
    UNION são montados só com o que o filtro pode responder — categoria, conjunto e geo
    descartam tabelas inteiras antes do SQL —; nenhum ramo joina mais inq_tb02_extracoes (os ids
    de extração são resolvidos aqui e chegam como `id_extracao = ANY(...)`, índice próprio de
    cada tabela); e a deduplicação de eventos idênticos saiu do GROUP BY — agora o ORDER BY +
    LIMIT desce direto às fontes (merge ordenado com sort limitado por ramo) e a tela conta as
    repetições no crachá ×N. GROUP BY na timeline inteira era o que obrigava a ler e agrupar o
    caso todo a cada página. Com sem_total a página é respondida sem a contagem do recorte
    (total=None no retorno) — a tela pede o total em paralelo; apenas_total devolve só a
    contagem (com cache de alguns minutos por recorte, que é o que a paginação repete)."""
    if id_extracao is None:
        filtro_ids = None
    elif isinstance(id_extracao, (list, tuple, set)):
        filtro_ids = [int(i) for i in id_extracao] or None
    else:
        filtro_ids = [int(id_extracao)]

    resumo_vazio = {
        "total_eventos": 0, "total_dias": 0, "total_categorias": 0,
        "total_extracoes": 0, "total_aplicativos": 0,
        "data_inicial": None, "data_final": None, "dia_pico": None,
        "eventos_dia_pico": 0,
        "qt_madrugada": 0, "qt_manha": 0, "qt_tarde": 0, "qt_noite": 0,
    }

    # Os ids de extração do inquérito (já no recorte pedido) resolvem o vínculo com
    # inq_tb02_extracoes uma única vez; os ramos filtram por eles e no_arquivo/no_dispositivo
    # chegam no fim, em Python — sem o join de um milhão de linhas em cada fonte.
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id_extracao, no_arquivo, no_dispositivo
                   FROM investigacao.inq_tb02_extracoes
                   WHERE id_inquerito = %s
                     AND (%s::bigint[] IS NULL OR id_extracao = ANY(%s::bigint[]))""",
                (id_inquerito, filtro_ids, filtro_ids),
            )
            extracoes = cur.fetchall()
    ids = [linha[0] for linha in extracoes]
    info_extracao = {linha[0]: (linha[1], linha[2]) for linha in extracoes}

    def _vazio() -> dict:
        if analitico:
            return {"resumo": dict(resumo_vazio), "pontos": []}
        return {"eventos": [], "total": 0, "pagina": pagina, "limite": limite}

    if not ids:
        return _vazio()

    filtro_cats = [c.strip() for c in (categoria or "").split(",") if c.strip()] or None
    filtro_conjunto = _CONJUNTOS_TIMELINE.get(conjunto)

    # os wheres são montados com o alias '@' e materializados por onde vão — 'e' na consulta
    # de fora/count e 'b' dentro de cada ramo (o recorte precisa valer ANTES do LIMIT do ramo)
    wheres: list[str] = []
    params: dict = {"ids": ids, "inq": id_inquerito, "iped_re": _RE_DATA_ISO_Z}

    if busca:
        wheres.append("@.tx_descricao ILIKE %(busca)s")
        params["busca"] = f"%{busca}%"

    if filtro_cats:
        nomes = []
        for i, cat in enumerate(filtro_cats):
            nome = f"cat{i}"
            params[nome] = cat
            nomes.append(f"%({nome})s")
        wheres.append(f"@.tp_categoria IN ({', '.join(nomes)})")

    if filtro_conjunto:
        # constantes do módulo — seguro embutir direto no texto SQL
        ors: list[str] = []
        exatas = [c for c in filtro_conjunto if not c.endswith("*")]
        if exatas:
            ors.append("@.tp_categoria IN (%s)" % ", ".join(f"'{c}'" for c in exatas))
        for prefixo in (c[:-1] for c in filtro_conjunto if c.endswith("*")):
            ors.append(f"@.tp_categoria LIKE '{prefixo}%%'")
        wheres.append("(" + " OR ".join(ors) + ")")

    # Faixa de horário pela hora local de Brasília, a mesma que a tela mostra no evento
    if periodo in _PERIODOS_ARQUIVO:
        hora_ini, hora_fim = _PERIODOS_ARQUIVO[periodo]
        wheres.append(f"{_hora_local_sql('@.dt_evento')} BETWEEN {int(hora_ini)} AND {int(hora_fim)}")

    # "só com geolocalização": a tela usa para desenhar a trilha temporal no mapa do app
    if somente_geo:
        wheres.append("@.vl_lat IS NOT NULL")

    if data_de:
        wheres.append("@.dt_evento >= %(data_de)s::timestamp")
        params["data_de"] = data_de
    if data_ate:
        wheres.append("@.dt_evento < (%(data_ate)s::timestamp + interval '1 day')")
        params["data_ate"] = data_ate

    def _where(alias: str) -> str:
        if not wheres:
            return ""
        return "WHERE " + " AND ".join(w.replace("@.", f"{alias}.") for w in wheres)

    # O Google Calendar sincroniza o calendário "Feriados no Brasil" (e os de outros países)
    # como compromissos de um dia inteiro; no conjunto investigativo o investigador quer a
    # agenda da pessoa, não o calendário fiscal do país.
    cond_feriado = ("AND COALESCE(cal.no_categoria, '') NOT ILIKE 'Feriados%%' "
                    "AND COALESCE(cal.no_categoria, '') NOT ILIKE 'Holidays%%'") \
        if conjunto == "investigativo" else ""

    def _ramos() -> list[tuple[tuple[str, ...], bool, str]]:
        """Ramos da UNION: (categorias que o ramo emite, tem coordenada?, SQL). Cada ramo emite
        as mesmas 11 colunas, na mesma ordem: dt_evento, tp_categoria, no_aplicativo,
        tx_descricao, id_extracao, id_referencia, tp_origem, tx_link, vl_lat, vl_lon, tx_dados.
        tx_link usa %(inq)s; o recorte de aparelho entra como %(ids)s em cada tabela."""
        return [
            (("ligacao",), False, """
        SELECT l.dt_ligacao AS dt_evento,
               'ligacao' AS tp_categoria,
               l.no_aplicativo,
               CASE WHEN l.tp_direcao = 'Incoming' THEN 'Recebida'
                    WHEN l.tp_direcao = 'Outgoing' THEN 'Realizada'
                    ELSE l.tp_direcao END ||
               ' de ' || COALESCE(l.no_contato, l.nu_telefone_contato, 'desconhecido') ||
               CASE WHEN l.qt_duracao_segundos IS NOT NULL
                    THEN ' (' || l.qt_duracao_segundos || 's)'
                    ELSE '' END AS tx_descricao,
               l.id_extracao, l.id_ligacao AS id_referencia, 'ligacao' AS tp_origem,
               NULL::text AS tx_link,
               NULL::numeric AS vl_lat, NULL::numeric AS vl_lon,
               jsonb_strip_nulls(jsonb_build_object(
                   'direcao', l.tp_direcao, 'contato', l.no_contato,
                   'telefone', l.nu_telefone_contato, 'duracao_segundos', l.qt_duracao_segundos,
                   'situacao', l.cd_status, 'videochamada', l.in_videochamada)) AS tx_dados
        FROM investigacao.lig_tb01_ligacoes l
        WHERE l.dt_ligacao IS NOT NULL
          AND l.id_extracao = ANY(%(ids)s)"""),

            (("mensagem_wpp",), True, """
        SELECT m.dt_mensagem, 'mensagem_wpp',
               'WhatsApp',
               CASE WHEN m.in_enviada THEN 'Enviou' ELSE 'Recebeu' END ||
               ' de ' || COALESCE(ct.no_contato, '(desconhecido)') || ': ' ||
               LEFT(COALESCE(m.tx_mensagem, '(mídia)'), 200),
               c.id_extracao, m.id_mensagem, 'wpp_mensagem',
               'whatsapp.html?inquerito=' || %(inq)s::text || '&fonte=wpp&msg=' || m.id_mensagem,
               m.vl_latitude, m.vl_longitude,
               jsonb_strip_nulls(jsonb_build_object(
                   'enviada', m.in_enviada, 'contato', ct.no_contato, 'tipo', m.tp_mensagem,
                   'situacao', m.cd_status_mensagem, 'encaminhada', m.in_encaminhada,
                   'apagada', m.in_apagada_remetente, 'temporaria', m.in_temporaria,
                   'editada', m.in_editada, 'destinatarios', m.tx_destinatarios,
                   'assunto', m.tx_assunto))
        FROM investigacao.wpp_tb04_mensagens m
        JOIN investigacao.wpp_tb02_conversas c ON c.id_conversa = m.id_conversa
        LEFT JOIN investigacao.wpp_tb01_contatos ct ON ct.id_contato = m.id_contato_remetente
        WHERE m.dt_mensagem IS NOT NULL
          AND c.id_extracao = ANY(%(ids)s)"""),

            (("mensagem_sms",), True, """
        SELECT m.dt_mensagem, 'mensagem_sms',
               'SMS',
               CASE WHEN m.in_enviada THEN 'Enviou SMS' ELSE 'Recebeu SMS' END ||
               ' de ' || COALESCE(ct.no_contato, '(desconhecido)') || ': ' ||
               LEFT(COALESCE(m.tx_mensagem, '(mídia)'), 200),
               c.id_extracao, m.id_mensagem, 'sms_mensagem',
               'mensageiros.html?inquerito=' || %(inq)s::text || '&fonte=sms&msg=' || m.id_mensagem,
               m.vl_latitude, m.vl_longitude,
               jsonb_strip_nulls(jsonb_build_object(
                   'enviada', m.in_enviada, 'contato', ct.no_contato, 'tipo', m.tp_mensagem,
                   'situacao', m.cd_status_mensagem, 'encaminhada', m.in_encaminhada,
                   'apagada', m.in_apagada_remetente, 'temporaria', m.in_temporaria,
                   'editada', m.in_editada, 'destinatarios', m.tx_destinatarios,
                   'assunto', m.tx_assunto))
        FROM investigacao.sms_tb04_mensagens m
        JOIN investigacao.sms_tb02_conversas c ON c.id_conversa = m.id_conversa
        LEFT JOIN investigacao.sms_tb01_contatos ct ON ct.id_contato = m.id_contato_remetente
        WHERE m.dt_mensagem IS NOT NULL
          AND c.id_extracao = ANY(%(ids)s)"""),

            # Mensageiros diversos: a categoria é montada com o nome do app ('mensagem_*').
            (("mensagem_*",), True, """
        SELECT m.dt_mensagem, 'mensagem_' || LOWER(mg.tp_app),
               mg.tp_app,
               CASE WHEN m.in_enviada THEN 'Enviou' ELSE 'Recebeu' END ||
               ' de ' || COALESCE(ct.no_contato, '(desconhecido)') || ': ' ||
               LEFT(COALESCE(m.tx_mensagem, '(mídia)'), 200),
               mg.id_extracao, m.id_mensagem, 'msg_mensagem',
               -- mesma regra de MapaTelas.paginaConversa (web/js/utils/mapa_telas.js): todo app com
               -- "whatsapp" no nome é WhatsApp e abre na tela dele — o UFDR nem sempre rotula
               -- "WhatsApp" (nuvem: modelo cru do Cellebrite, ex. "whatsapp_replaced_on_308").
               -- O &app deixa a lista já filtrada no app da mensagem; só o espaço precisa de escape
               -- (nome de app do Cellebrite não traz '&' nem '?').
               CASE WHEN POSITION('whatsapp' IN LOWER(mg.tp_app)) > 0
                    THEN 'whatsapp.html' ELSE 'mensageiros.html' END ||
               '?inquerito=' || %(inq)s::text || '&fonte=msg' ||
               '&app=' || replace(mg.tp_app, ' ', '%%20') || '&msg=' || m.id_mensagem,
               m.vl_latitude, m.vl_longitude,
               jsonb_strip_nulls(jsonb_build_object(
                   'enviada', m.in_enviada, 'contato', ct.no_contato, 'tipo', m.tp_mensagem,
                   'situacao', m.cd_status_mensagem, 'encaminhada', m.in_encaminhada,
                   'apagada', m.in_apagada_remetente, 'temporaria', m.in_temporaria,
                   'editada', m.in_editada, 'destinatarios', m.tx_destinatarios,
                   'assunto', m.tx_assunto))
        FROM investigacao.msg_tb04_mensagens m
        JOIN investigacao.msg_tb02_conversas mg ON mg.id_conversa = m.id_conversa
        LEFT JOIN investigacao.msg_tb01_contatos ct ON ct.id_contato = m.id_contato_remetente
        WHERE m.dt_mensagem IS NOT NULL
          AND mg.id_extracao = ANY(%(ids)s)"""),

            (("localizacao",), True, """
        SELECT l.dt_localizacao, 'localizacao',
               l.no_aplicativo,
               COALESCE(l.tx_endereco, l.tx_descricao, l.no_categoria, 'Localização') ||
               CASE WHEN l.vl_latitude IS NOT NULL THEN ' (' || l.vl_latitude::text || ', ' || l.vl_longitude::text || ')' ELSE '' END,
               l.id_extracao, l.id_localizacao, 'localizacao',
               NULL::text,
               l.vl_latitude, l.vl_longitude,
               jsonb_strip_nulls(jsonb_build_object(
                   'endereco', l.tx_endereco, 'categoria', l.no_categoria, 'descricao', l.tx_descricao,
                   'tipo', l.tp_localizacao, 'origem', l.no_origem, 'precisao_metros', l.vl_precisao,
                   'elevacao_metros', l.vl_elevacao, 'fim_permanencia', l.dt_fim))
        FROM investigacao.loc_tb01_localizacoes l
        WHERE l.dt_localizacao IS NOT NULL
          AND l.id_extracao = ANY(%(ids)s)"""),

            (("notificacao",), False, """
        SELECT n.dt_notificacao, 'notificacao',
               COALESCE(n.no_pacote_app, n.no_aplicativo_log, 'Sistema'),
               LEFT(COALESCE(n.tx_conteudo, '(notificação sem conteúdo)'), 200),
               n.id_extracao, n.id_notificacao, 'notificacao',
               NULL::text,
               NULL::numeric, NULL::numeric,
               jsonb_strip_nulls(jsonb_build_object(
                   'app', n.no_pacote_app, 'registrada_por', n.no_aplicativo_log,
                   'assunto', n.no_assunto, 'tipo', n.tp_notificacao, 'situacao', n.cd_status))
        FROM investigacao.ntf_tb01_notificacoes n
        WHERE n.dt_notificacao IS NOT NULL
          AND n.id_extracao = ANY(%(ids)s)"""),

            (("nota",), False, """
        SELECT COALESCE(nt2.dt_modificacao, nt2.dt_criacao), 'nota',
               COALESCE(nt2.no_aplicativo, 'Bloco de notas'),
               COALESCE(nt2.tx_titulo || ': ', '') || LEFT(COALESCE(nt2.tx_corpo, '(nota sem conteúdo)'), 200),
               nt2.id_extracao, nt2.id_nota, 'nota',
               NULL::text,
               NULL::numeric, NULL::numeric,
               jsonb_strip_nulls(jsonb_build_object(
                   'titulo', nt2.tx_titulo, 'corpo', LEFT(nt2.tx_corpo, 2000),
                   'criacao', nt2.dt_criacao, 'modificacao', nt2.dt_modificacao,
                   'pasta', nt2.no_pasta, 'situacao', nt2.cd_status, 'anexos', nt2.tx_anexos))
        FROM investigacao.not_tb01_notas nt2
        WHERE COALESCE(nt2.dt_modificacao, nt2.dt_criacao) IS NOT NULL
          AND nt2.id_extracao = ANY(%(ids)s)"""),

            (("navegacao",), False, """
        SELECT p.dt_visita, 'navegacao',
               p.no_aplicativo,
               COALESCE(p.tx_titulo, p.tx_url, '(página)') ||
               CASE WHEN p.qt_visitas > 1 THEN ' (' || p.qt_visitas || ' visitas)' ELSE '' END,
               p.id_extracao, p.id_pagina, 'navegacao',
               p.tx_url AS tx_link,
               NULL::numeric, NULL::numeric,
               jsonb_strip_nulls(jsonb_build_object(
                   'url', p.tx_url, 'titulo', p.tx_titulo, 'visitas', p.qt_visitas,
                   'conta', p.tx_conta))
        FROM investigacao.nav_tb01_paginas_visitadas p
        WHERE p.dt_visita IS NOT NULL
          AND p.id_extracao = ANY(%(ids)s)"""),

            (("email",), False, """
        SELECT em.dt_email, 'email',
               COALESCE(em.no_fonte, 'E-mail'),
               COALESCE(em.tx_assunto, '(sem assunto)') || ' — ' || COALESCE(em.no_remetente, 'remetente desconhecido'),
               em.id_extracao, em.id_email, 'email',
               NULL::text,
               NULL::numeric, NULL::numeric,
               jsonb_strip_nulls(jsonb_build_object(
                   'assunto', em.tx_assunto, 'remetente', em.no_remetente,
                   'destinatarios', em.tx_destinatarios, 'cc', em.tx_cc, 'pasta', em.no_pasta,
                   'situacao', em.cd_status_email, 'conta', em.no_conta, 'anexos', em.tx_anexos))
        FROM investigacao.eml_tb01_emails em
        WHERE em.dt_email IS NOT NULL
          AND em.id_extracao = ANY(%(ids)s)"""),

            # Buscas (SearchedItem): o termo que a pessoa digitou, no navegador, no e-mail ou
            # dentro de um aplicativo
            (("busca",), True, """
        SELECT b.dt_busca, 'busca',
               b.no_aplicativo,
               'Buscou: ' || LEFT(COALESCE(b.tx_termo, '(termo não registrado)'), 200),
               b.id_extracao, b.id_busca, 'busca',
               NULL::text,
               b.vl_latitude, b.vl_longitude,
               jsonb_strip_nulls(jsonb_build_object(
                   'termo', b.tx_termo, 'origem', b.no_origem, 'conta', b.no_conta,
                   'resultados', LEFT(b.tx_resultados, 300)))
        FROM investigacao.bsc_tb01_buscas b
        WHERE b.dt_busca IS NOT NULL
          AND b.id_extracao = ANY(%(ids)s)"""),

            # Atividade em redes sociais (SocialMediaActivity): post, comentário, check-in
            (("social",), True, """
        SELECT s.dt_atividade, 'social',
               COALESCE(s.no_plataforma, s.no_aplicativo),
               COALESCE(s.tp_atividade, 'Publicação') ||
               COALESCE(' de ' || s.no_autor, '') || ': ' ||
               LEFT(COALESCE(s.tx_corpo, s.tx_titulo, s.tx_url, '(sem conteúdo)'), 200),
               s.id_extracao, s.id_atividade, 'atividade_social',
               NULL::text,
               s.vl_latitude, s.vl_longitude,
               jsonb_strip_nulls(jsonb_build_object(
                   'plataforma', s.no_plataforma, 'tipo', s.tp_atividade, 'autor', s.no_autor,
                   'url', s.tx_url, 'reacoes', s.qt_reacoes, 'compartilhamentos',
                   s.qt_compartilhamentos, 'comentarios', s.qt_comentarios,
                   'marcados', s.tx_marcados, 'privacidade', s.tx_privacidade))
        FROM investigacao.rds_tb01_atividade_social s
        WHERE s.dt_atividade IS NOT NULL
          AND s.id_extracao = ANY(%(ids)s)"""),

            # Compromissos (CalendarEntry): entra pela data de início do evento
            (("compromisso",), True, f"""
        SELECT cal.dt_inicio, 'compromisso',
               cal.no_aplicativo,
               'Compromisso: ' || COALESCE(cal.tx_assunto, '(sem assunto)') ||
               COALESCE(' — ' || cal.tx_local, '') ||
               COALESCE(' · com ' || LEFT(cal.tx_convidados, 120), ''),
               cal.id_extracao, cal.id_compromisso, 'compromisso',
               NULL::text,
               cal.vl_latitude, cal.vl_longitude,
               jsonb_strip_nulls(jsonb_build_object(
                   'assunto', cal.tx_assunto, 'local', cal.tx_local, 'calendario',
                   cal.no_categoria,
                   'detalhes', LEFT(cal.tx_detalhes, 1000), 'convidados', cal.tx_convidados,
                   'organizador', cal.no_organizador, 'fim', cal.dt_fim, 'situacao',
                   cal.cd_situacao, 'repeticao', cal.tx_repeticao))
        FROM investigacao.cal_tb01_calendario cal
        WHERE cal.dt_inicio IS NOT NULL
          AND cal.id_extracao = ANY(%(ids)s)
          {cond_feriado}"""),

            # Antenas de telefonia (CellTower): rastro de deslocamento independente de GPS
            (("antena",), True, """
        SELECT erb.dt_registro, 'antena',
               erb.tp_rede,
               'Antena ' || COALESCE(erb.nu_cid, '?') ||
               COALESCE(' (LAC ' || erb.nu_lac || ')', '') ||
               COALESCE(' · operadora ' || erb.nu_mnc, ''),
               erb.id_extracao, erb.id_antena, 'antena',
               NULL::text,
               erb.vl_latitude, erb.vl_longitude,
               jsonb_strip_nulls(jsonb_build_object(
                   'rede', erb.tp_rede, 'cid', erb.nu_cid, 'lac', erb.nu_lac,
                   'mcc', erb.nu_mcc, 'operadora', erb.nu_mnc))
        FROM investigacao.erb_tb01_antenas erb
        WHERE erb.dt_registro IS NOT NULL
          AND erb.id_extracao = ANY(%(ids)s)"""),

            # Conexões de Wi-Fi/Bluetooth. Só as que identificam o outro lado: conexão sem nome
            # nem MAC não diz com o quê o aparelho falou (mesmo recorte da tela de Wi-Fi)
            (("conexao",), False, """
        SELECT cnx.dt_conexao, 'conexao',
               cnx.tp_metodo,
               'Conectou por ' || COALESCE(cnx.tp_metodo, '?') || ' a ' ||
               COALESCE(cnx.no_dispositivo, cnx.no_mac),
               cnx.id_extracao, cnx.id_conexao, 'conexao',
               NULL::text,
               NULL::numeric, NULL::numeric,
               jsonb_strip_nulls(jsonb_build_object(
                   'dispositivo', cnx.no_dispositivo, 'mac', cnx.no_mac,
                   'tipo', cnx.tp_dispositivo, 'natureza', cnx.tp_natureza))
        FROM investigacao.cnx_tb01_conexoes cnx
        WHERE cnx.dt_conexao IS NOT NULL
          AND cnx.id_extracao = ANY(%(ids)s)
          AND (cnx.no_dispositivo IS NOT NULL OR cnx.no_mac IS NOT NULL)"""),

            # Trajetos (Journey): de onde para onde, pela hora de início. A maioria não tem
            # endereço, só coordenada — e "de (sem endereço) para (sem endereço)" não diz nada,
            # então a coordenada entra como texto quando o endereço falta.
            (("trajeto",), True, """
        SELECT jrn.dt_inicio, 'trajeto',
               jrn.no_aplicativo,
               'Trajeto de ' || COALESCE(
                   LEFT(jrn.tx_endereco_origem, 90),
                   round(jrn.vl_lat_origem, 5) || ', ' || round(jrn.vl_lon_origem, 5),
                   '(origem desconhecida)') ||
               ' para ' || COALESCE(
                   LEFT(jrn.tx_endereco_destino, 90),
                   round(jrn.vl_lat_destino, 5) || ', ' || round(jrn.vl_lon_destino, 5),
                   '(destino desconhecido)'),
               jrn.id_extracao, jrn.id_trajeto, 'trajeto',
               NULL::text,
               jrn.vl_lat_origem, jrn.vl_lon_origem,
               jsonb_strip_nulls(jsonb_build_object(
                   'origem', jrn.tx_endereco_origem, 'destino', jrn.tx_endereco_destino,
                   'lat_origem', jrn.vl_lat_origem, 'lon_origem', jrn.vl_lon_origem,
                   'lat_destino', jrn.vl_lat_destino, 'lon_destino', jrn.vl_lon_destino,
                   'tipo', jrn.tp_trajeto, 'nome', jrn.no_trajeto, 'pontos', jrn.qt_pontos,
                   'fim', jrn.dt_fim))
        FROM investigacao.jrn_tb01_trajetos jrn
        WHERE jrn.dt_inicio IS NOT NULL
          AND jrn.id_extracao = ANY(%(ids)s)"""),

            # Transferências de valores (TransferOfFunds): dinheiro que passou pelo aparelho
            (("transferencia",), False, """
        SELECT trf.dt_envio, 'transferencia',
               trf.no_aplicativo,
               'Transferência' ||
               COALESCE(' de ' || trim(to_char(trf.vl_valor, '999999990.99')) ||
                        COALESCE(' ' || trf.no_moeda, ''), '') ||
               COALESCE(' · ' || LEFT(trf.tx_participantes, 120), '') ||
               COALESCE(' · ' || LEFT(trf.tx_descricao, 100), ''),
               trf.id_extracao, trf.id_transferencia, 'transferencia',
               NULL::text,
               NULL::numeric, NULL::numeric,
               jsonb_strip_nulls(jsonb_build_object(
                   'valor', trf.vl_valor, 'moeda', trf.no_moeda, 'tipo', trf.tp_transferencia,
                   'participantes', trf.tx_participantes, 'situacao', trf.cd_situacao,
                   'processamento', trf.dt_processamento, 'descricao', LEFT(trf.tx_descricao, 300)))
        FROM investigacao.trf_tb01_transferencias trf
        WHERE trf.dt_envio IS NOT NULL
          AND trf.id_extracao = ANY(%(ids)s)"""),

            # Arquivos da Nuvem IPED com linha do tempo própria (TX_LINHA_TEMPO, JSONB gravado na
            # importação, um par evento->data por chave, vindo do par timeStamp × timeEvent do
            # índice Lucene). Cada evento vira um ponto; o recorte por categoria
            # (Imagem/Vídeo/Áudio/Documento/Compactado) evita os milhões de eventos de arquivos de
            # sistema de um caso de celular — que na timeline seriam ruído puro.
            (("arquivo_iped",), False, """
        SELECT replace(ev.value, 'Z', '')::timestamp AS dt_evento, 'arquivo_iped',
               arq.no_categoria,
               'Arquivo "' || LEFT(arq.no_arquivo, 120) || '" · ' || ev.key,
               arq.id_extracao, arq.id_arquivo, 'arquivo_iped',
               NULL::text,
               NULL::numeric, NULL::numeric,
               jsonb_strip_nulls(jsonb_build_object(
                   'categoria', arq.no_categoria, 'evento', ev.key,
                   'arquivo', LEFT(arq.no_arquivo, 200)))
        FROM investigacao.arq_tb01_arquivos arq
        JOIN LATERAL jsonb_each_text(arq.tx_linha_tempo) ev ON true
        WHERE arq.tx_linha_tempo IS NOT NULL
          AND arq.id_extracao = ANY(%(ids)s)
          AND ev.value ~ %(iped_re)s
          AND arq.no_categoria IN ('Imagem', 'Vídeo', 'Áudio', 'Documento', 'Compactado')"""),
        ]

    # Só entram na query os ramos que o filtro pode responder — varrer tabela que o recorte
    # nunca mostraria foi o que fez a página custar 6 segundos num caso grande.
    ramos_sql = [
        sql for cats, tem_geo, sql in _ramos()
        if (not somente_geo or tem_geo)
        and any(_timeline_casa_categoria(c, filtro_cats) and _timeline_casa_categoria(c, filtro_conjunto)
                for c in cats)
    ]
    if not ramos_sql:
        return _vazio()
    uniao = "\n\n        UNION ALL\n\n".join(ramos_sql)
    where_clause = _where("e")

    with get_connection() as conn:
        with conn.cursor() as cur:
            if analitico:
                descricao = ", e.tx_descricao" if busca else ""
                hora = _hora_local_sql("eventos.dt_evento")
                cur.execute(f"""
                WITH eventos AS MATERIALIZED (
                    SELECT e.dt_evento, e.tp_categoria, e.no_aplicativo, e.id_extracao,
                           e.vl_lat{descricao}
                    FROM ({uniao}) e({COLUNAS_TIMELINE})
                    {where_clause}
                ),
                estatisticas AS (
                    SELECT count(*) AS total_eventos,
                           count(DISTINCT (dt_evento AT TIME ZONE 'UTC' AT TIME ZONE 'America/Sao_Paulo')::date) AS total_dias,
                           count(DISTINCT tp_categoria) AS total_categorias,
                           count(DISTINCT id_extracao) AS total_extracoes,
                           count(DISTINCT COALESCE(NULLIF(no_aplicativo, ''), 'Sem aplicativo')) AS total_aplicativos,
                           min(dt_evento) AS data_inicial,
                           max(dt_evento) AS data_final,
                           count(*) FILTER (WHERE {hora} BETWEEN 0 AND 5)  AS qt_madrugada,
                           count(*) FILTER (WHERE {hora} BETWEEN 6 AND 11) AS qt_manha,
                           count(*) FILTER (WHERE {hora} BETWEEN 12 AND 17) AS qt_tarde,
                           count(*) FILTER (WHERE {hora} BETWEEN 18 AND 23) AS qt_noite
                    FROM eventos
                ),
                dias AS (
                    SELECT (dt_evento AT TIME ZONE 'UTC' AT TIME ZONE 'America/Sao_Paulo')::date AS dt_dia,
                           tp_categoria,
                           mode() WITHIN GROUP (
                               ORDER BY COALESCE(NULLIF(no_aplicativo, ''), 'Sem aplicativo')
                           ) AS no_aplicativo,
                           count(*) AS quantidade
                    FROM eventos
                    GROUP BY 1, 2
                )
                SELECT d.dt_dia, d.tp_categoria, d.no_aplicativo, d.quantidade,
                       s.total_eventos, s.total_dias, s.total_categorias, s.total_extracoes,
                       s.total_aplicativos, s.data_inicial, s.data_final,
                       s.qt_madrugada, s.qt_manha, s.qt_tarde, s.qt_noite
                FROM dias d
                CROSS JOIN estatisticas s
                ORDER BY d.dt_dia, d.tp_categoria
                """, params)
                colunas = [d.name for d in cur.description]
                rows = [dict(zip(colunas, linha)) for linha in cur.fetchall()]
            else:
                # Contar o recorte relê o caso inteiro (uns 3s nos grandes) e só serve para a
                # paginação. Então: (1) o resultado fica guardado por alguns minutos, indexado
                # pelo recorte exato — virar página não reconta; (2) com sem_total a lista é
                # respondida SEM contar (a tela pinta em ~1s e pede o total em paralelo, por
                # apenas_total); (3) casos pequenos nem precisam de contagem — a primeira
                # página menor que o limite já é a prova do total.
                chave = (id_inquerito, tuple(ids), (busca or "").lower(),
                         tuple(filtro_cats or ()), conjunto or "", periodo or "",
                         bool(somente_geo), data_de or "", data_ate or "")
                agora = time.monotonic()
                total = None
                with _CANDADO_TOTAIS_TIMELINE:
                    guardado = _TOTAIS_TIMELINE.get(chave)
                    if guardado and agora - guardado[0] < _TTL_TOTAIS_TIMELINE:
                        total = guardado[1]

                def _guardar_total(valor: int) -> None:
                    with _CANDADO_TOTAIS_TIMELINE:
                        if len(_TOTAIS_TIMELINE) > 256:  # nunca deixa o dicionário crescer à toa
                            for k in [k for k, v in _TOTAIS_TIMELINE.items()
                                      if agora - v[0] >= _TTL_TOTAIS_TIMELINE]:
                                del _TOTAIS_TIMELINE[k]
                        _TOTAIS_TIMELINE[chave] = (agora, valor)

                if apenas_total:
                    if total is None:
                        cur.execute(
                            f"SELECT count(*) FROM ({uniao}) e({COLUNAS_TIMELINE}) {where_clause}",
                            params)
                        total = cur.fetchone()[0]
                        _guardar_total(total)
                    return {"total": total}
                if total is None and not sem_total:
                    cur.execute(
                        f"SELECT count(*) FROM ({uniao}) e({COLUNAS_TIMELINE}) {where_clause}",
                        params)
                    total = cur.fetchone()[0]
                    _guardar_total(total)
                rows = []
                if total != 0:
                    ofs = (pagina - 1) * limite
                    params["ofs"] = ofs
                    params["lim"] = limite
                    # ORDER BY+LIMIT só no UNION inteiro faria o PostgreSQL ordenar o caso todo
                    # (com o tx_dados JSONB de cada linha) antes de devolver a página — o que
                    # demorava 6s. O limite desce então para dentro de cada ramo: cada fonte
                    # entrega suas ofs+limite linhas mais recentes e a consulta de fora só faz
                    # o merge das candidatas. Matematicamente é a mesma página: nada que caiba
                    # na janela global pode estar fora da janela do seu próprio ramo.
                    ramos_top = "\n\n        UNION ALL\n\n".join(
                        f"(SELECT * FROM ({sql}) b({COLUNAS_TIMELINE}) {_where('b')}\n"
                        f"         ORDER BY b.dt_evento DESC NULLS LAST LIMIT %(topn)s)"
                        for sql in ramos_sql
                    )
                    params["topn"] = ofs + limite
                    cur.execute(f"""
                    SELECT e.*
                    FROM ({ramos_top}) e({COLUNAS_TIMELINE})
                    ORDER BY e.dt_evento DESC NULLS LAST
                    OFFSET %(ofs)s LIMIT %(lim)s
                    """, params)
                    colunas = [d.name for d in cur.description]
                    rows = [dict(zip(colunas, linha)) for linha in cur.fetchall()]
                if total is None and pagina == 1 and len(rows) < limite:
                    total = len(rows)   # a primeira página cabe inteira: ela É a contagem
                    _guardar_total(total)

    if analitico:
        if not rows:
            return {"resumo": dict(resumo_vazio), "pontos": []}

        campos_resumo = (
            "total_eventos", "total_dias", "total_categorias", "total_extracoes",
            "total_aplicativos", "data_inicial", "data_final",
            "qt_madrugada", "qt_manha", "qt_tarde", "qt_noite",
        )
        resumo = {campo: rows[0][campo] for campo in campos_resumo}
        totais_por_dia: dict = {}
        pontos = []
        for row in rows:
            dia = row["dt_dia"]
            totais_por_dia[dia] = totais_por_dia.get(dia, 0) + int(row["quantidade"] or 0)
            pontos.append({
                "dt_dia": dia,
                "tp_categoria": row["tp_categoria"],
                "no_aplicativo": row["no_aplicativo"],
                "quantidade": row["quantidade"],
            })
        dia_pico, eventos_dia_pico = max(totais_por_dia.items(), key=lambda item: item[1])
        resumo["dia_pico"] = dia_pico
        resumo["eventos_dia_pico"] = eventos_dia_pico
        return {"resumo": resumo, "pontos": pontos}

    # no_arquivo/no_dispositivo saem do dicionário resolvido lá em cima — não pesam mais em
    # cada ramo da UNION
    for r in rows:
        arquivo, dispositivo = info_extracao.get(r["id_extracao"], (None, None))
        r["no_arquivo"] = arquivo
        r["no_dispositivo"] = dispositivo
        r["id_inquerito"] = id_inquerito

    return {"eventos": rows, "total": total, "pagina": pagina, "limite": limite}


def listar_extracoes_do_inquerito(id_inquerito: int) -> list[dict]:
    """Lista as extrações de um inquérito (id, arquivo, dispositivo) para filtros."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id_extracao, no_arquivo, no_dispositivo, no_fabricante, cd_status, no_apelido
                   FROM investigacao.inq_tb02_extracoes
                   WHERE id_inquerito = %s
                   ORDER BY id_extracao DESC""",
                (id_inquerito,),
            )
            colunas = [d.name for d in cur.description]
            return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


def _gravar_conta_apps_ios(cur, id_extracao: int, titular: str, conta: str,
                           agencia: str, tipo: str) -> bool:
    """Conta bancária achada em SQLite de app iOS (Sicoob). No modo entrega o registro viaja
    pelo canal e o central reproduz AQUI — inclusive o SELECT anti-duplicata, que no central
    consulta o banco certo."""
    if canal_extracao.emitir("conta_apps_ios", (id_extracao, titular, conta, agencia, tipo)):
        return True
    cur.execute(
        "SELECT 1 FROM investigacao.bnc_tb01_contas_financeiras "
        "WHERE id_extracao = %s AND nu_conta = %s AND nu_agencia = %s AND no_instituicao = 'Sicoob'",
        (id_extracao, conta, agencia),
    )
    if cur.fetchone():
        return False
    cur.execute(
        """INSERT INTO investigacao.bnc_tb01_contas_financeiras
           (id_extracao, no_fonte, no_instituicao, no_titular, nu_conta, nu_agencia, tp_conta, tp_modelo)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
        (id_extracao, "Sicoob (app database)", "Sicoob", titular, conta, agencia, tipo, "AppDatabase"),
    )
    return True


def _extrair_contas_de_apps_ios(cur, id_extracao: int, caminho_ufdr) -> int:
    """Extrai contas bancárias de SQLite databases de apps iOS encontrados no UFDR.
    Atualmente suporta Sicoob (CoreDataContas.sqlite).
    Retorna a quantidade de contas inseridas."""
    import sqlite3
    import tempfile
    import os

    # str() só no caminho local: por sobre a ponte quem viaja é a FonteRemota (ufdr_zip.abrir
    # recusa a string "no://..." de propósito — ver o guard lá). O ".ufdr" do teste vale dos
    # dois lados porque str(FonteRemota) termina em ...ufdr também.
    if not str(caminho_ufdr).endswith(".ufdr"):
        return 0
    inseridas = 0

    try:
        with ufdr_zip.abrir(caminho_ufdr) as zf:
            # --- Sicoob: CoreDataContas.sqlite ---
            for caminho_zip in zf.namelist():
                if "CoreDataContas.sqlite" in caminho_zip:
                    dados = zf.read(caminho_zip)
                    if len(dados) < 16 or dados[:16] != b"SQLite format 3\x00":
                        continue
                    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".sqlite")
                    tmp.write(dados)
                    tmp.close()
                    try:
                        # closing (e não conn.close() no fim): se o SELECT estourar (tabela
                        # ZCONTA ausente, BD corrompida), a conexão fecha mesmo assim — senão
                        # fica segurada até o GC e o unlink abaixo falha no Windows, vazando
                        # o arquivo temporário a cada tentativa.
                        with contextlib.closing(sqlite3.connect(tmp.name)) as conn:
                            with conn.cursor() as cursor:
                                cursor.execute(
                                    "SELECT ZCONTACORRENTE, ZCOOPERATIVA, ZAPELIDO, ZTIPO "
                                    "FROM ZCONTA WHERE ZCONTACORRENTE IS NOT NULL"
                                )
                                rows = cursor.fetchall()

                        tipo_conta_map = {1: "conta_corrente"}
                        for row in rows:
                            conta_num, agencia, apelido, tipo_int = row
                            if not conta_num or not str(conta_num).strip():
                                continue
                            if _gravar_conta_apps_ios(
                                    cur, id_extracao, str(apelido or "").strip(),
                                    str(conta_num).strip(), str(agencia or "").strip(),
                                    tipo_conta_map.get(tipo_int, "desconhecido")):
                                inseridas += 1
                    finally:
                        try:
                            os.unlink(tmp.name)
                        except Exception:
                            pass
                    break  # só um CoreDataContas.sqlite esperado
    except Exception:
        logger.exception("Falha ao extrair contas Sicoob de app iOS (best-effort)")

    return inseridas


def listar_contas_usuario(id_inquerito: int, id_extracao: int | None = None) -> list[dict]:
    """Lista todas as contas UserAccount preservadas da extração do celular."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT c.id_conta_usuario, c.no_fonte, c.no_servico, c.no_usuario,
                          c.no_titular, c.dt_criacao
                   FROM investigacao.cta_tb01_contas_usuario c
                   JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = c.id_extracao
                   WHERE ex.id_inquerito = %s AND (%s::bigint IS NULL OR c.id_extracao = %s)
                   ORDER BY c.no_servico NULLS LAST, c.no_usuario NULLS LAST""",
                (id_inquerito, id_extracao, id_extracao),
            )
            colunas = [d.name for d in cur.description]
            return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


def listar_senhas(id_inquerito: int, id_extracao: int | None = None) -> list[dict]:
    """Lista as senhas/segredos do chaveiro (UFDR Password e Nuvem IPED) de um inquérito.

    Antes a Nuvem IPED importava 2.512 senhas do Keychain de um iPhone e o card contava
    "N senhas" — mas não havia tela nem endpoint para vê-las."""
    filtros = ["ex.id_inquerito = %s"]
    params: list = [id_inquerito]
    if id_extracao is not None:
        filtros.append("s.id_extracao = %s")
        params.append(id_extracao)
    where = " AND ".join(filtros)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT s.id_senha, s.no_fonte, s.no_servico, s.no_usuario, s.tx_dados,
                           s.tp_senha, s.no_grupo_acesso
                    FROM investigacao.snh_tb01_senhas s
                    JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = s.id_extracao
                    WHERE {where}
                    ORDER BY s.no_servico NULLS LAST, s.no_usuario NULLS LAST""",
                params,
            )
            colunas = [d.name for d in cur.description]
            return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


def listar_autofill(id_inquerito: int, id_extracao: int | None = None) -> list[dict]:
    """Lista os campos de preenchimento automático (UFDR Autofill e Nuvem IPED)."""
    filtros = ["ex.id_inquerito = %s"]
    params: list = [id_inquerito]
    if id_extracao is not None:
        filtros.append("a.id_extracao = %s")
        params.append(id_extracao)
    where = " AND ".join(filtros)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT a.id_autofill, a.no_fonte, a.no_chave, a.tx_valor, a.dt_registro,
                           a.dt_ultimo_uso
                    FROM investigacao.afl_tb01_autofill a
                    JOIN investigacao.inq_tb02_extracoes ex ON ex.id_extracao = a.id_extracao
                    WHERE {where}
                    ORDER BY a.no_chave NULLS LAST, a.tx_valor NULLS LAST""",
                params,
            )
            colunas = [d.name for d in cur.description]
            return [dict(zip(colunas, linha)) for linha in cur.fetchall()]

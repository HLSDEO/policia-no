"""Documentos diversos: arquivos soltos em data/ que não são .ufdr (PDF, CSV, XLS, XLSX, DOCX, TXT, HTML).

Listagem e upload pelo navegador (POST /api/documentos/upload), extração de texto e o anexo ao
inquérito (INQ_TB04_DOCUMENTOS) — o mesmo caminho vale para inquérito e caso de inteligência
(o caso é um inquérito com IN_CASO_INTELIGENCIA). Também é aqui que fica a análise por IA de um
documento anexado (resumo + pessoas + achados) e o visualizador (web/documento.html).
"""

import json
import logging
import queue
import re
import shutil
import tempfile
import threading
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException

from funcoes.db_utils import get_connection
from funcoes.entidades_auto_utils import (
    chave_achado,
    extrair_automaticas_texto,
    listar_padroes_ativos,
    normalizar_tp_achado,
    normalizar_valor,
)
from funcoes.env_utils import parse_env, read_env_text
from funcoes.hash_utils import calcular_hashes_arquivo
from funcoes.ia_auditoria import registrar_auditoria
from funcoes.ia_provedores import AuditoriaIA, enviar_mensagem
from funcoes.investigacao_utils import PASTA_DADOS, _obter_ou_criar_inquerito
from funcoes.organizacao_arquivos import (
    CATEGORIA_OUTROS,
    organizar,
    resolver as resolver_em_dados,
    resolver_estrito as resolver_estrito_em_dados,
)

logger = logging.getLogger(__name__)

# =====================================================================
# Listagem de arquivos (fora do .ufdr) e extração de texto
# =====================================================================

_EXTRATORES_TEXTO = {}  # preenchido abaixo, depois de cada _extrair_texto_* estar definido


def _subpasta_docs(subpasta: str | None = None) -> Path:
    return PASTA_DADOS / subpasta if subpasta else PASTA_DADOS


_EXTENSAO_ZIP = ".zip"

# Imagem como "outra prova": print de tela, foto de documento — entra no inquérito SEM camada
# de texto (o visualizador mostra a aba "Original", a análise de IA pula). Sem OCR aqui de
# propósito: OCR é a tela de Arquivos, não o importador de avulsos.
_IMAGENS_PROVA = {".jpg", ".jpeg", ".png"}

# Assinatura (magic bytes) por extensão — sanidade barata contra extensão mentirosa: um .exe
# renomeado para .pdf passava no upload e só explodia horas depois, na extração de texto.
# Tipos de texto puro (.txt/.csv/.html/.htm) não têm assinatura: não há o que conferir.
# Para as extensões ZIP (docx/xlsx/zip) vale o "PK" nu: o sufixo do bloco varia (zip64/span).
_ASSINATURAS = {
    ".pdf": (b"%PDF",),
    ".docx": (b"PK",), ".xlsx": (b"PK",), ".zip": (b"PK",),
    ".xls": (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",),
    ".jpg": (b"\xff\xd8\xff",), ".jpeg": (b"\xff\xd8\xff",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
}


def _assinatura_bate(caminho: Path) -> bool:
    """True quando os primeiros bytes do arquivo batem com o que a extensão promete (ou quando
    a extensão é dos tipos sem assinatura). Best-effort por leitura de 16 bytes."""
    assinaturas = _ASSINATURAS.get(caminho.suffix.lower())
    if not assinaturas:
        return True
    try:
        with open(caminho, "rb") as fp:
            cabeca = fp.read(16)
    except OSError:
        return False
    return any(cabeca.startswith(assinatura) for assinatura in assinaturas)


def _tipo_suportado(extensao: str) -> bool:
    """Extensão aceita no upload/listagem de prova avulsa: as que têm extrator de texto, as
    imagens sem texto e o .zip (que é desmontado antes)."""
    return extensao in _EXTRATORES_TEXTO or extensao in _IMAGENS_PROVA


def eh_zip(nome: str | None) -> bool:
    """True quando o arquivo é um .zip — ele não tem extrator de texto próprio, mas é
    suportado na listagem porque a importação o expande (ver expandir_zip_em_disco)."""
    return Path(nome or "").suffix.lower() == _EXTENSAO_ZIP


def listar_arquivos_diversos(subpasta: str | None = None) -> list[dict]:
    """Lista os arquivos de data/ que não são .ufdr, para o seletor da tela "Documentos diversos".

    O .zip aparece como suportado (eh_zip=True): a importação o expande e importa o
    conteúdo de uma vez — é o caminho para ZIPs com subpastas copiados à mão para data/."""
    pasta = _subpasta_docs(subpasta)
    if not pasta.is_dir():
        return []
    arquivos = []
    for caminho in sorted(pasta.iterdir()):
        if not caminho.is_file() or caminho.suffix.lower() == ".ufdr":
            continue
        extensao = caminho.suffix.lower()
        zipado = extensao == _EXTENSAO_ZIP
        arquivos.append({
            "nome": caminho.name,
            "tamanho_bytes": caminho.stat().st_size,
            "suportado": _tipo_suportado(extensao) or zipado,
            "eh_zip": zipado,
        })
    return arquivos


# =====================================================================
# Upload de documento pelo navegador
# =====================================================================
# O caminho de sempre é o investigador copiar o arquivo à mão para data/<DELEGACIA>/ no servidor.
# Quem não alcança aquela pasta (a maioria: ela vive no servidor, não na máquina de quem analisa)
# dependia de alguém copiar por ele. O upload abaixo grava EXATAMENTE no mesmo lugar, e por isso o
# resto do fluxo não muda em nada: o arquivo aparece na listagem, é escolhido no formulário e, ao
# ser anexado, vai para <inquérito>/OUTROS/ como qualquer outro (ver organizacao_arquivos.py).

# Teto por arquivo — ajustável em Administração → Desempenho (UPLOAD_DOCUMENTO_MB no .env,
# ver funcoes/desempenho_utils.py). Vale na hora, sem reiniciar: cada upload relê o .env.
# NÃO vale para o .zip: como o .ufdr, ele não tem teto de envio (ver _salvar_documento_zip
# e POST /api/documentos/upload-zip) — as travas dele são anti-zip-bomb, abaixo.
# Casa com o client_max_body_size do nginx (nginx/default.conf), que fica ACIMA do teto máximo
# permitido aqui de propósito: assim quem recusa é a API, com mensagem em JSON que a tela sabe
# ler, e não o proxy com um 413 opaco.
_UPLOD_MB_PADRAO = 50
_UPLOD_MB_MAXIMO = 500
# Mantido para compatibilidade com quem importa a constante: reflete o padrão, não o valor
# configurado — quem precisa do valor vigente usa limite_upload_bytes().
MAX_BYTES_UPLOAD_DOCUMENTO = _UPLOD_MB_PADRAO * 1024 * 1024


def limite_upload_mb() -> int:
    """Limite vigente por arquivo, em MB — o fixado no .env ou o padrão de 50."""
    try:
        from funcoes import desempenho_utils

        valor = desempenho_utils.valor_int("UPLOAD_DOCUMENTO_MB", _UPLOD_MB_PADRAO)
    except Exception:
        return _UPLOD_MB_PADRAO
    return max(1, min(valor, _UPLOD_MB_MAXIMO))


def limite_upload_bytes() -> int:
    """Limite vigente por arquivo, em bytes."""
    return limite_upload_mb() * 1024 * 1024

# O arquivo é copiado em blocos, e não com um read() inteiro: a API é um processo só, que atende
# todo mundo e ainda roda as extrações em thread — carregar 50 MB por upload simultâneo na memória
# dela é desnecessário quando o destino é o disco de qualquer jeito.
_BLOCO_UPLOAD = 1024 * 1024

_RE_CARACTERE_PROIBIDO = re.compile(r'[<>:"/\|?*\x00-\x1f]')


def _nome_arquivo_seguro(nome: str) -> str:
    """Nome de arquivo a partir do que o navegador mandou.

    O nome vem do cliente e vira caminho em disco — é o único ponto por onde ele escolheria ONDE
    gravar. Tira componente de caminho ("../../etc/x.pdf" vira "x.pdf") e caractere que o Windows
    recusa; a extensão sobrevive mesmo quando o nome precisa ser encurtado."""
    nome = (nome or "").replace("\\", "/").split("/")[-1].strip()
    nome = _RE_CARACTERE_PROIBIDO.sub("-", nome).strip().strip(".")
    if not nome:
        return ""
    extensao = Path(nome).suffix
    raiz = nome[: len(nome) - len(extensao)].strip() or "documento"
    return raiz[: 120 - len(extensao)] + extensao


def _abrir_destino_unico(pasta: Path, nome: str) -> tuple[Path, object]:
    """Cria <pasta>/<nome> com "xb" (criação exclusiva) e devolve (caminho, arquivo aberto).

    "xb" e não "wb", de propósito: dois investigadores enviando o mesmo nome ao mesmo tempo
    seria um sobrescrevendo a prova do outro. Quem chega depois ganha sufixo numérico, como em
    organizacao_arquivos.destino()."""
    extensao = Path(nome).suffix
    raiz = nome[: len(nome) - len(extensao)]
    alvo = pasta / nome
    sufixo = 2
    while True:
        try:
            return alvo, open(alvo, "xb")
        except FileExistsError:
            if sufixo > 999:
                raise ValueError(f"{nome}: já existem arquivos demais com este nome em data/.")
            alvo = pasta / f"{raiz} ({sufixo}){extensao}"
            sufixo += 1


# Teto de documentos extraídos de um único ZIP: protege contra archive com milhares de itens
# minúsculos (cada um viraria um documento na listagem e uma extração de texto depois).
# É trava anti-zip-bomb sobre o conteúdo extraído — NÃO é o teto de envio, que para o ZIP
# não existe (como no .ufdr, ver _salvar_documento_zip). Dimensionado para prova real
# (quebra de sigilo com centenas de anexos), ainda longe do que um archive malicioso tenta.
_MAX_DOCUMENTOS_POR_ZIP = 2000

# Teto do conteúdo descomprimido SOMADO de um ZIP (20 GB). O file_size do cabeçalho não é
# confiável: zip bomb declara uns poucos KB e infla gigabytes a partir de poucos KB
# comprimidos — por isso a defesa real não é confiar no cabeçalho, e sim copiar cada
# entrada em blocos contando o acumulado real (ver _salvar_documento_zip): o que passar
# do teto é descartado antes de nascer por inteiro em disco. 20 GB cobre ZIPs de prova
# de vários GB (comprimido) sem deixar uma bomba encher o disco de data/.
_MAX_BYTES_TOTAIS_ZIP = 20 * 1024 * 1024 * 1024  # 20 GB


def _expandir_zipfile(zf, pasta: Path) -> tuple[list[dict], int, bool]:
    """Núcleo da expansão de ZIP: grava em `pasta` apenas os arquivos de dentro cujo tipo é
    suportado (_EXTRATORES_TEXTO), ignorando o resto — subpastas não importam (achatado: só
    o nome do arquivo vale, com sufixo (2) em colisão), o caminho interno nunca vira caminho
    em disco (anti zip slip).

    Devolve (salvos, ignorados, truncado): salvos é [{"nome", "tamanho_bytes"}], ignorados
    conta pastas/__MACOSX/ocultos/cripto/tipo-não-suportado/vazias/corrompidas, truncado diz
    se os tetos anti-zip-bomb (_MAX_DOCUMENTOS_POR_ZIP / _MAX_BYTES_TOTAIS_ZIP) pararam o laço
    antes do fim. A cópia é em blocos com contagem real do acumulado — sem zf.read() inteiro
    e sem confiar no file_size do cabeçalho (que mente em zip bomb)."""
    import zipfile

    salvos: list[dict] = []
    ignorados = 0
    truncado = False
    total_extraido = 0
    for info in zf.infolist():
        if len(salvos) >= _MAX_DOCUMENTOS_POR_ZIP or total_extraido >= _MAX_BYTES_TOTAIS_ZIP:
            truncado = True
            break
        if info.is_dir():
            ignorados += 1
            continue
        if "__MACOSX" in info.filename.replace("\\", "/").split("/"):
            ignorados += 1
            continue
        # só o nome: o caminho interno do ZIP nunca vira caminho em disco (zip slip)
        nome_interno = info.filename.replace("\\", "/").split("/")[-1]
        nome_seguro = _nome_arquivo_seguro(nome_interno) if nome_interno else ""
        if not nome_seguro or nome_seguro.startswith("."):
            ignorados += 1
            continue
        if info.flag_bits & 0x1:  # entrada criptografada: não há o que ler
            ignorados += 1
            continue
        if not _tipo_suportado(Path(nome_seguro).suffix.lower()):
            ignorados += 1
            continue
        # Cópia em blocos com contagem real do acumulado — sem zf.read() (que
        # descompactaria a entrada inteira sem teto) e sem confiar no file_size
        # do cabeçalho. Se o teto estourar no meio da entrada, ela é descartada
        # e o laço para (o acumulado já bateu no teto mesmo).
        try:
            alvo, destino = _abrir_destino_unico(pasta, nome_seguro)
        except ValueError:
            truncado = True
            break
        escrito = 0
        teto = False
        try:
            with destino:
                with zf.open(info) as fz:
                    while True:
                        bloco = fz.read(_BLOCO_UPLOAD)
                        if not bloco:
                            break
                        if total_extraido + len(bloco) > _MAX_BYTES_TOTAIS_ZIP:
                            teto = True
                            break
                        destino.write(bloco)
                        escrito += len(bloco)
                        total_extraido += len(bloco)
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError, ValueError):
            alvo.unlink(missing_ok=True)
            ignorados += 1
            continue  # entrada corrompida ou método de compressão exótico: pula só ela
        if teto or escrito == 0:
            alvo.unlink(missing_ok=True)
            if teto:
                truncado = True
                break
            ignorados += 1
            continue  # vazia
        # dentro do ZIP a mentira também vale: entrada .pdf que não é PDF não entra em data/
        if not _assinatura_bate(alvo):
            alvo.unlink(missing_ok=True)
            ignorados += 1
            continue
        salvos.append({"nome": alvo.name, "tamanho_bytes": escrito,
                       "renomeado": alvo.name != nome_seguro})
    return salvos, ignorados, truncado


def _salvar_documento_zip(fluxo, nome: str, subpasta: str | None) -> list[dict]:
    """Expande um ZIP enviado pelo navegador: grava em data/[<delegacia>/] apenas os arquivos de
    dentro cujo tipo é suportado (_EXTRATORES_TEXTO), ignorando o resto — subpastas não importam,
    o que vale é o nome do arquivo. O próprio ZIP não fica em data/: só seu conteúdo útil.

    Sem limite de tamanho, como o .ufdr (ver salvar_ufdr_enviado): o corpo passa pelo
    location próprio no nginx (POST /api/documentos/upload-zip, client_max_body_size 0) e
    é gravado em blocos, sem passar pela memória do processo — nem o ZIP comprimido, nem
    as entradas ao sair dele. O que continua valendo são as travas anti-zip-bomb sobre o
    conteúdo EXTRAÍDO real: no máximo _MAX_DOCUMENTOS_POR_ZIP documentos somando no máximo
    _MAX_BYTES_TOTAIS_ZIP (ver _expandir_zipfile).

    Entradas ignoradas em silêncio: pastas, ocultos ("." inicial, __MACOSX), criptografadas,
    vazias e tipos não suportados — um ZIP de conversa de celular costuma vir com meia
    dúzia de tipos que o sistema nunca leria. Se NADA aproveitável sair do ZIP, levanta
    ValueError com a mensagem que a tela mostra."""
    import zipfile

    # o fluxo do upload não é buscável de forma confiável (SpooledTemporaryFile rolado) e
    # zipfile.ZipFile exige seek — o ZIP inteiro passa por um temporário em disco, em blocos
    # e sem teto de tamanho (como o .ufdr, só o disco limita)
    with tempfile.NamedTemporaryFile(prefix="upload_zip_") as tmp:
        total = 0
        while True:
            bloco = fluxo.read(_BLOCO_UPLOAD)
            if not bloco:
                break
            total += len(bloco)
            tmp.write(bloco)
        if total == 0:
            raise ValueError(f"{nome}: arquivo vazio.")
        tmp.flush()

        try:
            zf = zipfile.ZipFile(tmp)
        except zipfile.BadZipFile:
            raise ValueError(f"{nome}: ZIP inválido ou corrompido.")

        pasta = _subpasta_docs(subpasta)
        pasta.mkdir(parents=True, exist_ok=True)
        try:
            salvos, _ignorados, _truncado = _expandir_zipfile(zf, pasta)
        finally:
            zf.close()

    if not salvos:
        raise ValueError(
            f"{nome}: nenhum arquivo suportado dentro do ZIP — ele deve conter "
            "PDF, CSV, XLS, XLSX, DOCX, TXT, HTML, JPG ou PNG."
        )
    logger.info("ZIP enviado pelo navegador: %s → %d documento(s) extraído(s)", nome, len(salvos))
    return salvos


def expandir_zip_em_disco(caminho_zip: Path, subpasta: str | None = None) -> dict:
    """Expande um .zip que JÁ está em data/[<delegacia>/] (copiado à mão, via rede ou script).

    Usa o mesmo núcleo do upload (_expandir_zipfile): achatado, só tipos suportados, mesmas
    travas anti-zip-bomb. O ZIP original não é apagado aqui — quem chama decide (a importação
    em fila o move para OUTROS junto com o resto; ver expandir_zip_para_importacao).

    Devolve {"arquivos": [...], "ignorados": int, "truncado": bool}. Levanta ValueError com
    mensagem para a tela quando o ZIP é inválido ou não tem nada aproveitável, e
    FileNotFoundError quando o caminho não existe."""
    import zipfile

    if not caminho_zip.is_file():
        raise FileNotFoundError(str(caminho_zip))
    try:
        zf = zipfile.ZipFile(caminho_zip)
    except zipfile.BadZipFile:
        raise ValueError(f"{caminho_zip.name}: ZIP inválido ou corrompido.")
    pasta = _subpasta_docs(subpasta)
    pasta.mkdir(parents=True, exist_ok=True)
    try:
        salvos, ignorados, truncado = _expandir_zipfile(zf, pasta)
    finally:
        zf.close()
    if not salvos:
        raise ValueError(
            f"{caminho_zip.name}: nenhum arquivo suportado dentro do ZIP — ele deve conter "
            "PDF, CSV, XLS, XLSX, DOCX, TXT, HTML, JPG ou PNG."
        )
    logger.info("ZIP em disco expandido: %s → %d documento(s) extraído(s)", caminho_zip, len(salvos))
    return {"arquivos": salvos, "ignorados": ignorados, "truncado": truncado}


def salvar_documento_enviado(fluxo, nome_original: str, subpasta: str | None = None) -> list[dict]:
    """Grava em data/[<delegacia>/] um documento enviado pelo navegador.

    Devolve a lista de {"nome", "tamanho_bytes", "renomeado"} gravados — um item para arquivo
    comum, N para um ZIP, que é expandido ali na hora e só tem guardados os arquivos de dentro
    com tipo suportado (o ZIP em si não fica em data/). "nome" é como o arquivo ficou em disco,
    e é ele que a tela manda de volta na importação — pode ser diferente do enviado
    (sanitização, ou sufixo numérico quando já existe arquivo com o mesmo nome, caso em que
    "renomeado" é True: a tela avisa, porque descobrir o " (2)" sozinho é azar). Antes de dar
    por salvo, o arquivo é conferido pela assinatura da extensão (ver _ASSINATURAS): extensão
    que mente vira falha nomeada AQUI, não erro genérico na extração de horas depois.
    Levanta ValueError com a mensagem que a tela mostra: tipo não suportado, arquivo vazio,
    maior que o limite vigente (limite_upload_bytes(), ajustável em Desempenho) ou assinatura
    divergente — o .zip é a exceção do tamanho: não tem teto de envio, como o .ufdr
    (ver _salvar_documento_zip)."""
    nome = _nome_arquivo_seguro(nome_original)
    if not nome:
        raise ValueError("Nome de arquivo inválido.")
    extensao = Path(nome).suffix.lower()

    # Delegacia com nó ativo (docs/no_descentralizado.md): o documento vai para a pasta DA
    # DELEGACIA — o central só repassa o corpo em streaming. O ZIP é expandido NO NÓ pela mesma
    # _salvar_documento_zip (mesmo código, disco certo, mesmas travas anti-zip-bomb); o arquivo
    # único obedece ao mesmo limite vigente, imposto durante o streaming pelo próprio nó.
    from funcoes import no_utils

    no = no_utils.servidor_ativo_para(subpasta)
    if no is not None:
        from funcoes import no_ponte

        try:
            if extensao == ".zip":
                return no_ponte.enviar_zip(no, fluxo, subpasta, nome)
            if not _tipo_suportado(extensao):
                raise ValueError(f"{nome}: tipo não suportado — envie PDF, CSV, XLS, XLSX, DOCX, "
                                 "TXT, HTML, JPG/PNG ou ZIP.")
            salvo = no_ponte.enviar_arquivo(no, fluxo, f"{subpasta}/{nome}" if subpasta else nome,
                                            max_bytes=limite_upload_bytes())
            return [salvo]
        except no_ponte.NoIndisponivel as exc:
            raise ValueError(str(exc)) from exc

    if extensao == ".zip":
        return _salvar_documento_zip(fluxo, nome, subpasta)
    if not _tipo_suportado(extensao):
        raise ValueError(f"{nome}: tipo não suportado — envie PDF, CSV, XLS, XLSX, DOCX, TXT, "
                         "HTML, JPG/PNG ou ZIP.")

    pasta = _subpasta_docs(subpasta)
    pasta.mkdir(parents=True, exist_ok=True)
    alvo, arquivo_destino = _abrir_destino_unico(pasta, nome)

    limite_bytes = limite_upload_bytes()
    limite_mb = limite_upload_mb()
    total = 0
    try:
        with arquivo_destino:
            while True:
                bloco = fluxo.read(_BLOCO_UPLOAD)
                if not bloco:
                    break
                total += len(bloco)
                if total > limite_bytes:
                    raise ValueError(f"{nome}: maior que o limite de {limite_mb} MB por arquivo.")
                arquivo_destino.write(bloco)
        if total == 0:
            raise ValueError(f"{nome}: arquivo vazio.")
        # a extensão manda, o conteúdo obedece: .exe renomeado para .pdf morre aqui, com nome,
        # e não como "falha na extração" genérica dias depois (ver _ASSINATURAS)
        if not _assinatura_bate(alvo):
            raise ValueError(f"{nome}: o conteúdo não bate com a extensão — não é um arquivo "
                             f"{extensao} de verdade.")
    except BaseException:
        # nada de meio-arquivo em data/: ele apareceria na listagem como se estivesse inteiro, e
        # a extração de texto falharia depois, longe daqui
        alvo.unlink(missing_ok=True)
        raise

    renomeado = alvo.name != nome
    if renomeado:
        logger.info("Documento já existia com o nome %s — gravado como %s", nome, alvo.name)
    logger.info("Documento enviado pelo navegador: %s (%s bytes)", alvo, total)
    return [{"nome": alvo.name, "tamanho_bytes": total, "renomeado": renomeado}]


# =====================================================================
# Marcador de página no texto extraído
# =====================================================================
# A IA sempre citou "está na página X" lendo o número impresso no rodapé do papel — que é o
# número da FOLHA do processo, não o da página do arquivo, e some de vez num PDF sem carimbo.
# Marcar a quebra no próprio texto extraído dá à IA (e ao visualizador, web/js/documento.js) a
# única página que os dois conseguem apontar de verdade: a do arquivo.
#
# O marcador fica sozinho numa linha, e é isso que o torna seguro de fatiar de volta: um
# "[[página 3]]" no meio de uma frase do documento é conteúdo, não quebra.
#
# ATENÇÃO: o marcador entra em TX_CONTEUDO, ou seja, muda o texto que a IA lê e o que já está
# indexado no RAG (EMB_TB01). Documento anexado antes disto não tem marcador nenhum: o
# visualizador cai para página única e oferece o botão "Reextrair" (reextrair_texto_documento).
MARCA_PAGINA = "[[página {n}]]"
_RE_MARCA_PAGINA = re.compile(r"^\[\[página (\d+)\]\][ \t]*$", re.MULTILINE)


def _juntar_paginas(paginas: list[str]) -> str:
    """Emenda as páginas de um documento com o marcador de quebra antes de cada uma.

    Documento de uma página só sai sem marcador nenhum: numerar a única página é ruído no texto
    que a IA lê, e o visualizador já mostra tudo numa folha quando não acha marcador."""
    paginas = [(p or "").strip() for p in paginas]
    if len(paginas) <= 1:
        return (paginas[0] if paginas else "").strip()
    partes = []
    for i, texto in enumerate(paginas, 1):
        partes.append(MARCA_PAGINA.format(n=i))
        partes.append(texto)
    return "\n\n".join(partes).strip()


def paginas_do_texto(texto: str) -> list[dict]:
    """Desfaz _juntar_paginas: devolve [{"nu_pagina", "tx_conteudo"}] do texto já gravado.

    Serve ao visualizador (web/js/documento.js) e à citação por página. Texto sem marcador — de
    um CSV, de um PDF de uma página só, ou de um documento anexado antes desta feature — volta
    como uma página única; quem chama distingue os dois casos por texto_paginado()."""
    texto = texto or ""
    marcas = list(_RE_MARCA_PAGINA.finditer(texto))
    if not marcas:
        return [{"nu_pagina": 1, "tx_conteudo": texto.strip()}]
    paginas = []
    # o que vier antes do primeiro marcador (não deveria existir) entra como página 0 pra não
    # sumir da tela: esconder texto do investigador é pior do que uma folha fora da numeração
    cabeca = texto[: marcas[0].start()].strip()
    if cabeca:
        paginas.append({"nu_pagina": 0, "tx_conteudo": cabeca})
    for i, m in enumerate(marcas):
        fim = marcas[i + 1].start() if i + 1 < len(marcas) else len(texto)
        paginas.append({"nu_pagina": int(m.group(1)), "tx_conteudo": texto[m.end():fim].strip()})
    return paginas


def texto_paginado(texto: str) -> bool:
    """True quando o texto traz marcadores de página — ou seja, foi extraído com esta feature."""
    return bool(_RE_MARCA_PAGINA.search(texto or ""))


def _extrair_texto_pdf(caminho: Path) -> str:
    # via funcoes/pdf_texto.py: PDF de órgão público sem ToUnicode válido sai do pypdf como nome
    # de glifo ("/uni0049/uni004E..."), e sem desfazer isso o texto que chega à IA é ilegível
    from funcoes.pdf_texto import paginas_pdf

    return _juntar_paginas(paginas_pdf(caminho))


# Export de órgão público quase nunca é UTF-8 com vírgula: o Excel brasileiro salva CSV em
# cp1252/latin-1 e separa por ';'. Ler tudo como utf-8 corrompe acento em silêncio e um
# separador errado joga a linha inteira numa célula só — a IA então não enxerga as colunas.


def _detectar_encoding(dados: bytes) -> str:
    """Encoding mais provável de um arquivo de texto/CSV. utf-8 quando a detecção não tem certeza."""
    from charset_normalizer import from_bytes

    melhor = from_bytes(dados).best()
    return melhor.encoding if melhor and melhor.encoding else "utf-8"


def _formatar_valor_celula(valor) -> str:
    """Valor de célula de planilha em texto legível: data/hora em ISO, float inteiro sem o '.0'."""
    import datetime as _dt

    if valor is None:
        return ""
    if isinstance(valor, (_dt.datetime, _dt.date, _dt.time)):
        return valor.isoformat()
    if isinstance(valor, float) and valor.is_integer():
        return str(int(valor))
    return str(valor)


def _linha_tabela(celulas: list[str]) -> str:
    """Uma linha de tabela markdown — '|' vira '/' na célula pra não quebrar a coluna."""
    return "| " + " | ".join(c.replace("|", "/").replace("\n", " ").strip() for c in celulas) + " |"


def _extrair_texto_csv(caminho: Path) -> str:
    import csv
    import io

    dados = caminho.read_bytes()
    texto = dados.decode(_detectar_encoding(dados), errors="replace")
    amostra = texto[:8192]
    try:
        separador = csv.Sniffer().sniff(amostra, delimiters=";,\t|").delimiter
    except csv.Error:
        separador = ";" if amostra.count(";") > amostra.count(",") else ","

    linhas_saida: list[str] = []
    for i, campos in enumerate(csv.reader(io.StringIO(texto), delimiter=separador)):
        if not any(c.strip() for c in campos):
            continue
        linhas_saida.append(_linha_tabela(campos))
        if i == 0 and campos:
            linhas_saida.append(_linha_tabela(["---"] * len(campos)))
    return "\n".join(linhas_saida).strip()


def _extrair_texto_xlsx(caminho: Path) -> str:
    from openpyxl import load_workbook

    # read_only mantém o .xlsx (que é um zip) ABERTO até o close() explícito — a documentação do
    # openpyxl é clara nisso. Sem fechar, o arquivo continua com handle vivo depois da extração, e
    # quem vem logo em seguida mover o documento para a pasta do inquérito
    # (_organizar_documento) faz isso com o arquivo em uso.
    livro = load_workbook(str(caminho), read_only=True, data_only=True)
    try:
        return _abas_do_xlsx(livro)
    finally:
        livro.close()


def _extrair_texto_xls(caminho: Path) -> str:
    """Texto de um .xls — tanto o binário BIFF/OLE2 (CDR da TIM) quanto o HTML que a Oi entrega
    com essa extensão. A leitura das abas/linhas é a mesma que a importação de telefonia usa
    (funcoes.telefonia_utils._abas_planilha), aqui só reformatada como texto para a IA."""
    from funcoes.telefonia_utils import _abas_planilha

    return _abas_de_linhas(_abas_planilha(caminho))


def _abas_de_linhas(abas: list[tuple[str, list[list]]]) -> str:
    """Formata [(título, linhas)] como texto de tabela por aba, na íntegra — serve às planilhas
    que não vêm do openpyxl (o .xls e o CSV, quando precisarem)."""
    saida: list[str] = []
    for titulo, linhas in abas:
        partes: list[str] = [f"### {titulo}"]
        for j, linha in enumerate(linhas):
            celulas = [_formatar_valor_celula(v) for v in linha]
            if not any(celulas):
                continue
            partes.append(_linha_tabela(celulas))
            if j == 0:
                partes.append(_linha_tabela(["---"] * len(celulas)))
        saida.append("\n".join(partes))
    return _juntar_paginas(saida)


def _abas_do_xlsx(livro) -> str:
    # cada aba é uma "página" da planilha: é a unidade que o investigador navega e a que a IA
    # consegue citar. O "### <aba>" logo abaixo do marcador de página continua nomeando qual é.
    abas: list[str] = []
    for aba in livro.worksheets:
        # Nem todo gerador de .xlsx escreve a área usada da aba: a planilha que a operadora de
        # telefonia manda grava <dimension ref="A1"/>, e em read_only o openpyxl acredita nela —
        # uma aba de 87 linhas sai como UMA linha vazia, sem erro e sem aviso. O documento inteiro
        # virava 200 caracteres de títulos de aba, e a IA lia uma planilha vazia achando que era
        # isso que estava no papel. reset_dimensions() manda medir pelas células que existem.
        # É método, e não atributo: `aba.reset_dimensions = True` só apaga o método, em silêncio.
        aba.reset_dimensions()
        partes: list[str] = [f"### {aba.title}"]
        for j, linha in enumerate(aba.iter_rows(values_only=True)):
            celulas = [_formatar_valor_celula(v) for v in linha]
            if not any(celulas):
                continue
            partes.append(_linha_tabela(celulas))
            if j == 0:
                partes.append(_linha_tabela(["---"] * len(celulas)))
        abas.append("\n".join(partes))
    return _juntar_paginas(abas)


# Onde o Word quebra a página, no XML do .docx. `w:br type="page"` é a quebra que o autor
# inseriu; `w:lastRenderedPageBreak` é onde o Word calculou a quebra da última vez que abriu o
# arquivo — não é oficial, mas é o único registro de paginação que um .docx guarda, e num
# documento salvo pelo Word ele bate com o que sai na impressora. Sem nenhum dos dois (documento
# gerado por script, por exemplo) o texto sai sem marcador e o visualizador mostra folha única:
# inventar uma paginação por contagem de linhas apontaria a página errada, que é pior do que
# não apontar nenhuma.
_RE_QUEBRA_PAGINA_DOCX = re.compile(
    r'<w:br\b[^>]*w:type="page"|<w:lastRenderedPageBreak\b'
)


def _extrair_texto_docx(caminho: Path) -> str:
    """Texto de um .docx: parágrafos e tabelas na ordem em que aparecem no documento (o
    python-docx expõe as duas listas separadas, então a ordem sai do próprio XML do corpo).
    O .doc antigo (Word 97-2003) não é OOXML e não abre aqui — precisa ser salvo como .docx."""
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    documento = Document(str(caminho))
    corpo = documento.element.body
    paginas: list[list[str]] = [[]]
    for elemento in corpo.iterchildren():
        if elemento.tag.endswith("}p"):
            paragrafo = Paragraph(elemento, documento)
            # a quebra pode estar antes do texto do próprio parágrafo, então ela abre a página
            # nova primeiro e o parágrafo já cai na página de baixo
            if _RE_QUEBRA_PAGINA_DOCX.search(elemento.xml):
                paginas.append([])
            texto = paragrafo.text.strip()
            if texto:
                paginas[-1].append(texto)
        elif elemento.tag.endswith("}tbl"):
            # a tabela sai no mesmo markdown do extrator de planilha (_linha_tabela): o
            # visualizador devolve <table> de verdade e a IA passa a ler a tabela como tabela
            for j, linha in enumerate(Table(elemento, documento).rows):
                celulas = [celula.text.strip() for celula in linha.cells]
                if not any(celulas):
                    continue
                paginas[-1].append(_linha_tabela(celulas))
                if j == 0:
                    paginas[-1].append(_linha_tabela(["---"] * len(celulas)))
    return _juntar_paginas(["\n".join(p) for p in paginas])


# ------------------------------------------------- leitura formatada do .docx (documento.html)
# O modo "Formatado" da tela não mostra o HTML cru do arquivo — devolve BLOCOS de texto
# (título, parágrafo, lista, tabela) montados a partir dos objetos do python-docx. O front pinta
# o HTML que quiser e aplica o realce bloco a bloco, como já faz com a planilha: o texto que a
# busca acha e a IA lê continua sendo exatamente o texto extraído, sem tag no meio.

_RE_TITULO_DOCX = re.compile(r"^\s*(?:heading|title|t[íi]tulo|cabe[çc]alho)\b[\s:.-]*(\d)?", re.IGNORECASE)


def _estilo_titulo(paragrafo) -> int | None:
    """Nível de título (1..6) pelo nome do estilo, nos dois idiomas que o Word grava
    ("Heading 2" no Word inglês, "Título 2" no português). "Título/Title" sem número é o
    título do documento — vira o maior de todos."""
    nome = (paragrafo.style.name or "") if paragrafo.style is not None else ""
    m = _RE_TITULO_DOCX.match(nome)
    if not m:
        return None
    return min(6, int(m.group(1)) if m.group(1) else 1)


def _paragrafo_lista(paragrafo) -> tuple[bool, bool]:
    """(é item de lista, é ordenada). O marcador pode estar no parágrafo (numPr direto) ou no
    estilo ("List Bullet"/"Lista com marcadores" numeram por herança); o nome do estilo também
    diz se a lista é numerada — é o registro mais barato e cobre o Word de sempre."""
    nome = ((paragrafo.style.name or "") if paragrafo.style is not None else "").lower()
    ordem = "number" in nome or "numeric" in nome or "numérica" in nome or "numerica" in nome
    if "list" in nome or "lista" in nome:
        return True, ordem
    pPr = paragrafo._p.pPr
    if pPr is not None and pPr.numPr is not None:
        return True, ordem
    try:  # numPr herdado do estilo: pPr do parágrafo não existe, o do estilo existe
        estilo_pPr = paragrafo.style.element.pPr if paragrafo.style is not None else None
    except (AttributeError, KeyError):
        estilo_pPr = None
    return (estilo_pPr is not None and estilo_pPr.numPr is not None), ordem


def _runs_formatados(paragrafo) -> list[dict]:
    """Runs do parágrafo com negrito/itálico/sublinhado, vizinhos de mesma formatação fundidos
    (o Word fragmenta runs por rsid, não por estilo — fundir é o que faz um parágrafo normal
    sair como UM bloco, e o realce da busca casar o texto inteiro)."""
    runs: list[dict] = []
    for run in paragrafo.runs:
        if not run.text:
            continue
        fmt = (bool(run.bold), bool(run.italic), bool(run.underline))
        if runs and runs[-1]["fmt"] == fmt:
            runs[-1]["x"] += run.text
        else:
            runs.append({"x": run.text, "fmt": fmt})
    for i, r in enumerate(runs):
        if i == 0:
            r["x"] = r["x"].lstrip()
        if i == len(runs) - 1:
            r["x"] = r["x"].rstrip()
    return [r for r in runs if r["x"]]


def _celulas_da_linha(linha) -> list[str]:
    """Células da linha sem as repetições da mesclagem horizontal: o python-docx devolve a MESMA
    célula uma vez por coluna coberta — no visualizador isso duplicaria o texto na tela."""
    celulas: list[str] = []
    anterior = None
    for celula in linha.cells:
        tc = id(celula._tc)
        if tc == anterior:
            continue
        anterior = tc
        celulas.append(" ".join(p.strip() for p in celula.text.split("\n") if p.strip()))
    return celulas


def _bloco_tabela(tabela) -> dict | None:
    linhas = [_celulas_da_linha(linha) for linha in tabela.rows]
    linhas = [ln for ln in linhas if any(ln)]
    if not linhas:
        return None
    return {"t": "t", "cabecalho": linhas[0], "linhas": linhas[1:]}


def _bloco_paragrafo(paragrafo) -> dict | None:
    texto = paragrafo.text.strip()
    if not texto:
        return None
    eh_lista, ordem = _paragrafo_lista(paragrafo)
    if eh_lista:
        return {"t": "l", "ordem": ordem, "itens": [texto]}
    nivel = _estilo_titulo(paragrafo)
    if nivel:
        return {"t": "h", "n": nivel, "x": texto}
    runs = _runs_formatados(paragrafo)
    formatado = any(r["fmt"] != (False, False, False) for r in runs)
    if formatado and len(runs) <= 12:  # parágrafo todo picotado de formatação não é leitura, é
        return {"t": "p",                      # ruído: nesse caso o texto puro é melhor
                "runs": [{"x": r["x"], "b": r["fmt"][0], "i": r["fmt"][1], "u": r["fmt"][2]}
                         for r in runs]}
    return {"t": "p", "x": texto}


def renderizar_blocos_docx(caminho: Path) -> list[dict]:
    """O .docx em blocos de leitura, página a página — as MESMAS quebras do extrator de texto
    (_RE_QUEBRA_PAGINA_DOCX), para o "Página 7" do Formatado ser o do Texto e o da IA."""
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    documento = Document(str(caminho))
    corpo = documento.element.body
    paginas: list[list[dict]] = [[]]
    for elemento in corpo.iterchildren():
        if elemento.tag.endswith("}p"):
            paragrafo = Paragraph(elemento, documento)
            if _RE_QUEBRA_PAGINA_DOCX.search(elemento.xml):
                paginas.append([])
            bloco = _bloco_paragrafo(paragrafo)
            if bloco is None:
                continue
            anterior = paginas[-1][-1] if paginas[-1] else None
            if (anterior is not None and anterior["t"] == "l" and bloco["t"] == "l"
                    and anterior["ordem"] == bloco["ordem"]):
                anterior["itens"].extend(bloco["itens"])
            else:
                paginas[-1].append(bloco)
        elif elemento.tag.endswith("}tbl"):
            bloco = _bloco_tabela(Table(elemento, documento))
            if bloco is not None:
                paginas[-1].append(bloco)
    return [{"blocos": p} for p in paginas]


def renderizar_documento(escopo: str, id_pai: int, id_documento: int) -> dict:
    """Os blocos do .docx para o modo Formatado da tela; levanta ValueError com o motivo quando
    não dá (não é docx, arquivo sumiu de data/, Word velho)."""
    tabela, coluna_pai = _tabela_documento(escopo)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT no_arquivo, tp_arquivo FROM {tabela} "
                f"WHERE id_documento = %s AND {coluna_pai} = %s",
                (id_documento, id_pai),
            )
            linha = cur.fetchone()
    if linha is None:
        raise ValueError("Documento não encontrado.")
    no_arquivo, tp_arquivo = linha
    if (tp_arquivo or "").lower() != "docx":
        raise ValueError("Só documento .docx tem leitura formatada.")
    caminho = caminho_original_documento(no_arquivo)
    if caminho is None:
        raise ValueError(
            f"O arquivo original ({nome_curto_documento(no_arquivo)}) não está mais na pasta de dados."
        )
    try:
        return {"paginas": renderizar_blocos_docx(caminho)}
    except Exception as exc:
        raise ValueError(f"Não foi possível ler o documento formatado: {str(exc)[:300]}")


def _decodificar_texto(dados: bytes) -> str:
    """Decodifica bytes de um arquivo de texto: UTF-8 estrito primeiro (cobre ASCII,
    UTF-8 e HTML com entidades), detecção só para legado (latin-1 etc.).

    A detecção em arquivo pequeno erra para ASCII quando o conteúdo real é UTF-8 —
    e decodificar UTF-8 como ASCII com errors="replace" corrompe cada byte
    não-ASCII num �. Tentando UTF-8 válido primeiro, esse caminho nem passa pelo
    detector."""
    try:
        return dados.decode("utf-8")
    except UnicodeDecodeError:
        return dados.decode(_detectar_encoding(dados), errors="replace")


def _extrair_texto_txt(caminho: Path) -> str:
    return _decodificar_texto(caminho.read_bytes()).strip()


# Tags cujo conteúdo nunca é texto do documento (código, estilo, modelo).
_TAGS_HTML_IGNORADAS = frozenset({"script", "style", "noscript", "template"})

# Tags que quebram linha no texto extraído — sem elas, dois parágrafos colariam numa
# frase só e a IA leria "fim do parágrafo.Início do próximo".
_TAGS_HTML_BLOCO = frozenset({
    "p", "div", "section", "article", "header", "footer", "main", "aside",
    "h1", "h2", "h3", "h4", "h5", "h6", "li", "ul", "ol", "tr", "table",
    "blockquote", "pre", "br", "hr", "thead", "tbody", "tfoot",
})


def _extrair_texto_html(caminho: Path) -> str:
    """Texto visível de um .html/.htm: remove tags, scripts e estilos, preservando
    parágrafos e linhas de tabela como quebras de linha. Só stdlib (html.parser),
    sem dependência nova — HTML de relatório/exportação costuma vir com CSS/JS
    embutido que não pode vazar para o texto que a IA lê."""
    import html as _html
    from html.parser import HTMLParser

    bruto = _decodificar_texto(caminho.read_bytes())

    partes: list[str] = []
    ignoradas = 0

    class _Parser(HTMLParser):
        def handle_starttag(self, tag, attrs):
            nonlocal ignoradas
            tag = (tag or "").lower()
            if tag in _TAGS_HTML_IGNORADAS:
                ignoradas += 1
            elif ignoradas == 0 and tag in _TAGS_HTML_BLOCO:
                partes.append("\n")

        def handle_endtag(self, tag):
            nonlocal ignoradas
            tag = (tag or "").lower()
            if tag in _TAGS_HTML_IGNORADAS:
                ignoradas = max(0, ignoradas - 1)
            elif ignoradas == 0 and tag in _TAGS_HTML_BLOCO:
                partes.append("\n")
            elif ignoradas == 0 and tag in ("td", "th"):
                partes.append("\t")

        def handle_data(self, data):
            if ignoradas == 0 and data and data.strip():
                partes.append(_html.unescape(data))

    # convert_charrefs=True: entidades (&atilde;, &#227;, ...) chegam em handle_data já
    # decodificadas — com False elas iriam para handle_entityref/handle_charref e seriam
    # descartadas em silêncio (o unescape em handle_data não as alcançaria).
    parser = _Parser(convert_charrefs=True)
    parser.feed(bruto)
    parser.close()

    linhas: list[str] = []
    for linha in "".join(partes).splitlines():
        # colapsa espaços dentro da linha, mantendo uma quebra por bloco
        limpa = re.sub(r"[ \t\xa0]+", " ", linha).strip()
        if limpa:
            linhas.append(limpa)
    return "\n".join(linhas).strip()


_EXTRATORES_TEXTO = {
    ".pdf": _extrair_texto_pdf,
    ".csv": _extrair_texto_csv,
    ".xlsx": _extrair_texto_xlsx,
    ".xls": _extrair_texto_xls,
    ".docx": _extrair_texto_docx,
    ".txt": _extrair_texto_txt,
    ".html": _extrair_texto_html,
    ".htm": _extrair_texto_html,
}

# Postgres recusa NUL (0x00) em coluna TEXT e o encoder UTF-8 recusa surrogate solto — os dois
# saem do pypdf quando o PDF tem ToUnicode/CMap quebrado ou fonte CID/Type3 (típico de "Upload"
# de sistema judicial e de digitalização com camada de texto ruim). Sem esta limpeza o INSERT
# em TX_CONTEUDO estoura psycopg.DataError/UnicodeEncodeError e o anexo cai com 500.
_RE_CTRL_INVALIDO = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _limpar_texto_para_banco(texto: str) -> str:
    texto = texto.encode("utf-8", "replace").decode("utf-8")  # remove surrogate solto
    return _RE_CTRL_INVALIDO.sub("", texto)


def extrair_texto_documento(caminho: Path) -> tuple[str | None, str | None]:
    """Extrai o texto de um documento; devolve (texto, None) em caso de sucesso ou (None, erro).
    Imagem de prova (_IMAGENS_PROVA) devolve (None, None): ela entra no inquérito SEM camada de
    texto por design — não é erro, é a natureza do arquivo (o visualizador mostra o original)."""
    extensao = caminho.suffix.lower()
    if extensao in _IMAGENS_PROVA:
        return None, None
    extrator = _EXTRATORES_TEXTO.get(extensao)
    if extrator is None:
        if extensao == ".doc":
            return None, "Word no formato antigo (.doc): salve o arquivo como .docx e importe de novo."
        return None, f"Tipo de arquivo não suportado para extração de texto: {caminho.suffix or '(sem extensão)'}"
    try:
        texto = extrator(caminho)
    except Exception as exc:
        return None, str(exc)[:2000]
    if texto:
        texto = _limpar_texto_para_banco(texto)
    if not texto:
        return None, "Nenhum texto extraído (documento vazio ou sem camada de texto — ex.: PDF escaneado sem OCR)."
    return texto, None


# =====================================================================
# Busca dentro de um documento (ferramenta da IA) — `ler_documento` já volta o texto em partes
# de MAX_CHARS_PARTE_DOCUMENTO (funcoes.ia_investigacao.documento_em_partes); esta é a via
# direta: achar um nome/CPF/valor num documento grande sem varrer parte por parte.
# =====================================================================

_CONTEXTO_BUSCA_DOC = 320      # caracteres de cada lado de uma ocorrência
_MAX_OCORRENCIAS_BUSCA_DOC = 12


def _remover_acentos(texto: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", texto or "") if unicodedata.category(c) != "Mn")


def buscar_no_texto_documento(texto: str | None, termo: str) -> dict:
    """Ocorrências de `termo` no texto (sem acento, sem caixa), cada uma com um trecho de
    contexto ao redor. `qt_ocorrencias` é o total; `trechos` mostra até _MAX_OCORRENCIAS_BUSCA_DOC."""
    texto = texto or ""
    termo = (termo or "").strip()
    if len(termo) < 2:
        return {"erro": "Informe um termo com pelo menos 2 caracteres."}
    alvo = _remover_acentos(texto).lower()
    agulha = _remover_acentos(termo).lower()
    trechos: list[str] = []
    inicio = 0
    while len(trechos) < _MAX_OCORRENCIAS_BUSCA_DOC:
        pos = alvo.find(agulha, inicio)
        if pos == -1:
            break
        ini = max(0, pos - _CONTEXTO_BUSCA_DOC)
        fim = min(len(texto), pos + len(termo) + _CONTEXTO_BUSCA_DOC)
        trechos.append(("…" if ini > 0 else "") + texto[ini:fim].strip() + ("…" if fim < len(texto) else ""))
        inicio = pos + len(termo)
    total = alvo.count(agulha)
    resultado = {"termo": termo, "qt_ocorrencias": total, "trechos": trechos}
    if total == 0:
        resultado["dica"] = "Nada encontrado — tente uma variação do termo ou leia o documento com ler_documento."
    elif total > len(trechos):
        resultado["dica"] = f"{total - len(trechos)} ocorrência(s) a mais — refine o termo ou use ler_documento(parte=N)."
    return resultado


# =====================================================================
# Documentos anexados a um inquérito ("Extrair de PDF" na tela de Investigação):
# o texto extraído fica preso ao inquérito e a IA o lê via listar_documentos_inquerito /
# ler_documento_inquerito (ver funcoes.ia_investigacao). Extração síncrona — ler um
# PDF/CSV/XLS/XLSX/DOCX é rápido, não precisa do padrão de job em background dos casos.
# =====================================================================

def _puxar_documento_do_no(caminho: Path, delegacia: str | None) -> bool:
    """Espelha do nó da delegacia um documento que só está na pasta data() dele (ver
    docs/no_descentralizado.md). Documentos são pequenos (teto UPLOAD_DOCUMENTO_MB) e a aba
    "Original", a reextração e os blocos IPL/IRPF vão relê-los em disco local de qualquer jeito —
    então, ao contrário do .ufdr (lido por faixas), o documento vem inteiro para o central e o
    resto do fluxo segue idêntico. False = sem nó ativo ou falha de rede (a rota devolve 404)."""
    from funcoes import no_ponte, no_utils

    try:
        no = no_utils.servidor_ativo_para(delegacia)
        if no is None:
            return False
        relativo = caminho.relative_to(PASTA_DADOS).as_posix()
        no_ponte.baixar(no, relativo, caminho)
        return caminho.is_file()
    except (no_ponte.NoIndisponivel, ValueError):
        logger.warning("Falha ao espelhar %s do nó da delegacia %s", caminho.name, delegacia)
        return False
    except Exception:
        logger.exception("Erro inesperado ao espelhar %s do nó", caminho.name)
        return False


def espelhar_pasta_do_no(pasta: Path, delegacia: str | None) -> bool:
    """Espelha uma PASTA inteira da data() do nó (RIF/SIMBA chegam como pastas, não como arquivo
    único). Lista a pasta no nó pelo MESMO /no/listar do seletor e baixa arquivo por arquivo —
    conteúdo de subpastas aninhadas não vem (pacotes RIF/SIMBA são pastas planas de XML/PDF/CSV;
    se um dia vier pacote aninhado, o espelho é o /no/arquivo por faixa, não isto). True se a
    pasta existe agora."""
    from funcoes import no_ponte, no_utils

    try:
        no = no_utils.servidor_ativo_para(delegacia)
        if no is None:
            return False
        relativo = pasta.relative_to(PASTA_DADOS).as_posix()
        arquivos = no_ponte.listar(no, relativo, "diversos")
        if not arquivos:
            return False
        pasta.mkdir(parents=True, exist_ok=True)
        for item in arquivos:
            nome = (item or {}).get("nome") or ""
            if not nome or "/" in nome or "\\" in nome or ".." in nome:
                continue
            no_ponte.baixar(no, f"{relativo}/{nome}", pasta / nome)
        return pasta.is_dir()
    except (no_ponte.NoIndisponivel, ValueError):
        logger.warning("Falha ao espelhar a pasta %s do nó da delegacia %s", pasta.name, delegacia)
        return False
    except Exception:
        logger.exception("Erro inesperado ao espelhar a pasta %s do nó", pasta.name)
        return False


def expandir_zip_para_importacao(nome_arquivo: str, subpasta: str | None = None,
                                 delegacia: str | None = None) -> dict:
    """Expande um .zip de data/ para importação em fila (ver funcoes/zip_import.py).

    Subpastas do ZIP são achatadas (só o nome do arquivo vale, colisão ganha " (2)"), como no
    upload-zip — os seletores só listam a raiz de data/. Entradas ignoradas (pastas, __MACOSX,
    ocultos, cripto, tipo não suportado, vazias, corrompidas) contam em `ignorados`; se os tetos
    anti-zip-bomb pararem antes do fim, `truncado` vem True.

    Devolve {"arquivos": [nomes], "ignorados": int, "truncado": bool, "finalizar": fn} — a
    `finalizar(numero_inquerito)` move o ZIP original para OUTROS do inquérito (best-effort,
    no nó quando ele mora lá) para sumir do seletor, sem virar documento. Levanta ValueError
    (ZIP inválido ou sem nada aproveitável) ou FileNotFoundError."""
    from funcoes.organizacao_arquivos import CATEGORIA_OUTROS, organizar

    caminho = _subpasta_docs(subpasta) / nome_arquivo
    no = None
    id_servidor_zip = None
    if caminho.is_file():
        expansao = expandir_zip_em_disco(caminho, subpasta)
        arquivos = [a["nome"] for a in expansao["arquivos"]]
        ignorados = expansao["ignorados"]
        truncado = expansao["truncado"]
    else:
        # ZIP mora no nó da delegacia: a expansão roda LÁ (mesmo código, disco certo) e os
        # arquivos de dentro são anexados um a um pelo caminho de nó (parse remoto).
        no = _no_com_arquivo(caminho, delegacia)
        if no is None:
            if not _puxar_documento_do_no(caminho, delegacia):
                raise FileNotFoundError(nome_arquivo)
            expansao = expandir_zip_em_disco(caminho, subpasta)
            arquivos = [a["nome"] for a in expansao["arquivos"]]
            ignorados = expansao["ignorados"]
            truncado = expansao["truncado"]
            no = None
        else:
            from funcoes import no_ponte

            relativo = caminho.relative_to(PASTA_DADOS).as_posix()
            try:
                expansao = no_ponte.expandir_zip(no, relativo)
            except no_ponte.NoIndisponivel:
                if not _puxar_documento_do_no(caminho, delegacia):
                    raise FileNotFoundError(nome_arquivo)
                expansao = expandir_zip_em_disco(caminho, subpasta)
                arquivos = [a["nome"] for a in expansao["arquivos"]]
                ignorados = expansao["ignorados"]
                truncado = expansao["truncado"]
                no = None
            else:
                arquivos = [a["nome"] for a in (expansao.get("arquivos") or [])]
                ignorados = int(expansao.get("ignorados") or 0)
                truncado = bool(expansao.get("truncado"))
                id_servidor_zip = no["id_servidor"]
                if not arquivos:
                    raise ValueError(
                        f"{nome_arquivo}: nenhum arquivo suportado dentro do ZIP — ele deve conter "
                        "PDF, CSV, XLS, XLSX, DOCX, TXT, HTML, JPG ou PNG."
                    )

    def _finalizar(numero_inquerito: str | None) -> None:
        # o ZIP sai da pasta de entrega como qualquer documento (para sumir do seletor), mas
        # sem virar linha em INQ_TB04 — só o conteúdo dele é documento
        try:
            if id_servidor_zip and no is not None:
                from funcoes import no_ponte

                relativo_zip = caminho.relative_to(PASTA_DADOS).as_posix()
                no_ponte.organizar(no, relativo_zip, numero_inquerito or "", "OUTROS")
            elif caminho.is_file():
                organizar(caminho, numero_inquerito, CATEGORIA_OUTROS)
        except Exception:
            logger.exception("Falha ao organizar o ZIP %s (best-effort)", nome_arquivo)

    return {"arquivos": arquivos, "ignorados": ignorados, "truncado": truncado,
            "finalizar": _finalizar}


def anexar_documento_inquerito(nome_arquivo: str, numero_inquerito: str, id_usuario: int | None,
                               subpasta: str | None = None, delegacia: str | None = None,
                               in_caso_inteligencia: bool = False) -> dict:
    """Extrai o texto de um documento de data/ e o anexa ao inquérito (criando o inquérito se o
    número ainda não existir, como na extração de UFDR). Um .zip não entra aqui: a rota o desvia
    para a fila de importação (ver funcoes/zip_import.py) — sem isso, um ZIP grande estouraria o
    timeout da requisição. Levanta ValueError para seleção inválida e FileNotFoundError se o
    arquivo não estiver em data/."""
    nome_arquivo = (nome_arquivo or "").strip()
    # nada de path traversal — só o nome do arquivo, sem separador de diretório
    if not nome_arquivo or "/" in nome_arquivo or "\\" in nome_arquivo:
        raise ValueError("Arquivo inválido.")
    if nome_arquivo.lower().endswith(".ufdr"):
        raise ValueError("Arquivo .ufdr vai na extração de UFDR ao lado, não aqui.")
    if eh_zip(nome_arquivo):
        raise ValueError("ZIP é importado em fila (job) — use a importação de Arquivo genérico.")
    caminho = _subpasta_docs(subpasta) / nome_arquivo
    id_servidor = None
    no = None
    if caminho.is_file():
        texto, erro = extrair_texto_documento(caminho)
    else:
        # delegacia com nó: o arquivo mora na estação (upload repassado ou solto na pasta lá).
        # O texto é extraído NO NÓ (POST /no/parse) e o arquivo nunca é espelhado para cá — a
        # prova fica lá, o banco guarda o texto e ID_SERVIDOR diz de onde reler quando preciso.
        no = _no_com_arquivo(caminho, delegacia)
        if no is not None:
            from funcoes import no_ponte
            id_servidor = no["id_servidor"]
            relativo = caminho.relative_to(PASTA_DADOS).as_posix()
            try:
                parse = no_ponte.parse_documento(no, relativo)
                texto, erro = parse.get("texto"), parse.get("erro")
            except no_ponte.NoIndisponivel:
                # o nó caiu no meio do caminho: último recurso é o espelho antigo (copia p/ cá)
                id_servidor, no = None, None
                if not _puxar_documento_do_no(caminho, delegacia):
                    raise FileNotFoundError(nome_arquivo)
                texto, erro = extrair_texto_documento(caminho)
        elif _puxar_documento_do_no(caminho, delegacia):
            # nó sem o arquivo (ou pasta RIF/SIMBA): espelho como sempre — o arquivo vem p/ cá
            texto, erro = extrair_texto_documento(caminho)
        else:
            raise FileNotFoundError(nome_arquivo)

    # Hash do arquivo original (cadeia de custódia — ver db/migracoes/0044_documento_hashes.sql).
    # Só quando o arquivo está em disco AQUI: documento que mora num NÓ (id_servidor) nunca é
    # espelhado, e o central não tem como hashear o que não tem. Best-effort: hash indisponível
    # não pode derrubar a anexação — o card diz "hash ainda não calculado" em vez de inventar.
    hashes = None
    if id_servidor is None and caminho.is_file():
        try:
            hashes = calcular_hashes_arquivo(caminho)
        except OSError:
            logger.exception("Falha ao calcular o hash de %s (best-effort)", nome_arquivo)

    with get_connection() as conn:
        with conn.cursor() as cur:
            id_inquerito = _obter_ou_criar_inquerito(cur, numero_inquerito, id_usuario, delegacia,
                                                     in_caso_inteligencia)
            if in_caso_inteligencia and not (numero_inquerito or "").strip():
                cur.execute(
                    "SELECT nu_inquerito FROM investigacao.inq_tb01_inqueritos WHERE id_inquerito = %s",
                    (id_inquerito,),
                )
                numero_inquerito = cur.fetchone()[0]
            cur.execute(
                """
                INSERT INTO investigacao.inq_tb04_documentos
                    (id_inquerito, no_arquivo, tp_arquivo, tx_conteudo, qt_caracteres, tx_erro, id_usuario,
                     id_servidor, co_hash_md5, co_hash_sha1, co_hash_sha256)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id_documento
                """,
                (
                    id_inquerito,
                    relativo if id_servidor else caminho.name,
                    (caminho.suffix.lstrip(".").lower() or "?"),
                    texto, len(texto) if texto else None, erro, id_usuario,
                    id_servidor,
                    (hashes or {}).get("md5"), (hashes or {}).get("sha1"), (hashes or {}).get("sha256"),
                ),
            )
            id_documento = cur.fetchone()[0]
        conn.commit()

    # texto já extraído: o documento sai da pasta de entrega da delegacia e vai para
    # data/<delegacia>/<inquérito>/OUTROS/ — NO NÓ quando ele mora lá (o nó arruma a própria
    # pasta; o arquivo não atravessa a WAN). O caminho novo volta em no_arquivo porque a rota
    # ainda passa o arquivo adiante (IPL e IRPF leem o PDF de novo, ver rotas/investigacao.py).
    if id_servidor:
        no_arquivo_final = _organizar_documento_no(id_documento, no, relativo, numero_inquerito)
    else:
        no_arquivo_final = _organizar_documento(id_documento, caminho, numero_inquerito) or nome_arquivo
    # nu_inquerito volta na resposta para o cliente fixar o caso GERADO nos arquivos seguintes
    # de um lote (caso de inteligência: o 1º POST cria o CASO-AAAA.NNNN, os demais precisam
    # entrar nele — sem isto, cada arquivo do lote criava um caso novo)
    return {"id_documento": id_documento, "id_inquerito": id_inquerito,
            "nu_inquerito": numero_inquerito, "no_arquivo": no_arquivo_final,
            "qt_caracteres": len(texto) if texto else 0, "tx_erro": erro}


def _no_com_arquivo(caminho: Path, delegacia: str | None) -> dict | None:
    """Nó ativo da delegacia que TEM este arquivo na pasta data() dele — None se não há nó ativo
    ou o arquivo não está lá (aí o chamador cai no espelho/404 de sempre)."""
    from funcoes import no_ponte, no_utils

    try:
        no = no_utils.servidor_ativo_para(delegacia)
        if no is None:
            return None
        relativo = caminho.relative_to(PASTA_DADOS).as_posix()
        return no if no_ponte.arquivo_existe(no, relativo) else None
    except (no_ponte.NoIndisponivel, ValueError):
        return None
    except Exception:
        logger.exception("Erro inesperado ao procurar %s no nó da delegacia %s",
                         caminho.name, delegacia)
        return None


def _organizar_documento_no(id_documento: int, no: dict, relativo: str,
                            numero_inquerito: str) -> str:
    """Manda o NÓ mover o documento para data/<delegacia>/<inquérito>/OUTROS/ na estação e grava o
    caminho novo (relativo ao data() do nó) em NO_ARQUIVO + ID_SERVIDOR já aponta a leitura futura
    pela ponte. Best-effort como o organize local: se o nó não responder, o documento anexado fica
    apontando para onde está hoje."""
    from funcoes import no_ponte

    try:
        novo = no_ponte.organizar(no, relativo, numero_inquerito)
        if not novo:
            return relativo
        with get_connection() as conn:
            with conn.cursor() as cur:
                # ID_SERVIDOR aqui NÃO é enfeite: a leitura "só no nó" (organizacao_arquivos.
                # _cache_no — aba Original, reextração, blocos IPL/IRPF/IPEI/telefonia) só
                # atravessa a ponte para documentos com id_servidor preenchido; sem isto o
                # documento importado ficava gravado no nó e invisível para essas telas.
                cur.execute(
                    "UPDATE investigacao.inq_tb04_documentos "
                    "SET no_arquivo = %s, id_servidor = %s WHERE id_documento = %s",
                    (novo, no["id_servidor"], id_documento),
                )
            conn.commit()
        return novo
    except Exception:
        logger.exception("Falha ao organizar o documento %s no nó (best-effort)", id_documento)
        return relativo


def _organizar_documento(id_documento: int, caminho: Path, numero_inquerito: str) -> str | None:
    """Move o documento anexado para data/<delegacia>/<inquérito>/OUTROS/ e grava o caminho novo
    (relativo a data/) em NO_ARQUIVO; devolve esse caminho, ou None se nada foi movido.
    Best-effort: falha aqui não desfaz o anexo, que já está no banco."""
    try:
        relativo = organizar(caminho, numero_inquerito, CATEGORIA_OUTROS)
        if not relativo:
            return None
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE investigacao.inq_tb04_documentos SET no_arquivo = %s WHERE id_documento = %s",
                    (relativo, id_documento),
                )
            conn.commit()
        return relativo
    except Exception:
        logger.exception("Falha ao organizar o documento %s (best-effort)", id_documento)
        return None


def obter_id_inquerito_de_documento(id_documento: int) -> int | None:
    """Resolve o inquérito dono de um documento anexado, para checar permissão na rota de apagar."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id_inquerito FROM investigacao.inq_tb04_documentos WHERE id_documento = %s",
                (id_documento,),
            )
            row = cur.fetchone()
            return row[0] if row else None


def mover_para_lixeira(caminho: Path, subpasta: str) -> Path | None:
    """Move um arquivo para data/_lixeira/<subpasta>/ com carimbo de tempo no nome ("o removido
    fica recuperável, não vira pó"). `subpasta` agrupa a origem: o id do inquérito para documento
    removido do card, "uploads" para o upload abandonado que a higiene recolhe
    (ver funcoes/higiene_data.py). Devolve o caminho novo, ou None se não havia arquivo / o
    movimento falhou (best-effort: quem chamou já decidiu, a remoção não volta atrás)."""
    from funcoes.organizacao_arquivos import PASTA_LIXEIRA

    if caminho is None or not caminho.is_file():
        return None
    try:
        pasta = PASTA_DADOS / PASTA_LIXEIRA / str(subpasta)
        pasta.mkdir(parents=True, exist_ok=True)
        carimbo = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        destino = pasta / f"{carimbo}__{caminho.name}"
        shutil.move(str(caminho), str(destino))
        logger.info("Arquivo removido movido para a lixeira: %s", destino)
        return destino
    except OSError:
        logger.exception("Falha ao mover arquivo removido para a lixeira (best-effort)")
        return None


def apagar_documento_inquerito(id_documento: int) -> bool:
    """Remove um documento anexado ao inquérito; False se não existir. A extração estruturada do
    PDF (peças/pessoas/achados) sobrevive — só perde o vínculo com o texto que ia para a IA.
    Os trechos vetorizados do documento também saem da busca semântica. O ARQUIVO sai do lugar de
    prova e vai para data/_lixeira/ (mover_para_lixeira), onde fica recuperável até a expurgo —
    apagar documento sem deixar rastro é fácil demais de fazer por engano."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id_inquerito, no_arquivo, id_servidor FROM investigacao.inq_tb04_documentos "
                "WHERE id_documento = %s",
                (id_documento,),
            )
            row = cur.fetchone()
            if row is None:
                return False
            id_inquerito, no_arquivo, id_servidor = row
            cur.execute(
                "UPDATE investigacao.ipl_tb01_inqueritos_pdf SET id_documento = NULL WHERE id_documento = %s",
                (id_documento,),
            )
            cur.execute(
                "DELETE FROM investigacao.emb_tb01_embeddings WHERE id_inquerito = %s AND tp_fonte = 'doc' AND id_mensagem = %s",
                (id_inquerito, id_documento),
            )
            cur.execute("DELETE FROM investigacao.inq_tb04_documentos WHERE id_documento = %s", (id_documento,))
            apagou = cur.rowcount > 0
        conn.commit()
    if id_servidor and no_arquivo:
        # documento de nó: a cópia volátil que a leitura sob demanda materializou também sai de cena
        # (a prova, essa nunca veio para cá — fica na estação; apagar aqui é só faxina do cache)
        from funcoes.organizacao_arquivos import PASTA_CACHE_NO, destino_em_dados
        try:
            destino_em_dados(f"{PASTA_CACHE_NO}/{no_arquivo}").unlink(missing_ok=True)
        except (OSError, ValueError):
            pass
    if apagou and not id_servidor and no_arquivo:
        # prova local: sai da pasta do inquérito e vai para a lixeira (recuperável até o expurgo
        # de higiene_data). Documento que mora no nó nunca veio para cá — nada a mover daqui.
        #
        # AQUI é destrutivo, então o resolutor é o ESTRITO (só o caminho exato gravado no
        # banco): o fallback por nome do resolver() comum — ótimo para leitura, quando o
        # arquivo mudou de pasta e a gente quer reencontrá-lo — num DELETE pode achar um
        # homônimo de OUTRO inquérito e mandá-lo para a lixeira junto com o registro certo
        # ("cair num arquivo homônimo de outro inquérito seria vazar prova alheia", ver
        # organizacao_arquivos.resolver_estrito). Caminho gravado que não existe mais: nada
        # é movido, só avisado.
        caminho = resolver_estrito_em_dados(no_arquivo)
        if caminho is None:
            logger.warning(
                "Documento %s apagado sem mover arquivo: caminho gravado (%s) não existe mais "
                "em data/ — nada foi movido para a lixeira (evitou homônimo de outro inquérito)",
                id_documento, no_arquivo,
            )
        else:
            mover_para_lixeira(caminho, str(id_inquerito))
    return apagou


# =====================================================================
# Análise de entidades de um documento anexado ao inquérito: a IA lê o texto extraído e devolve
# resumo + pessoas + achados estruturados (mesmo espírito da extração de IPL, mas por documento),
# gravados nas colunas CD_ANALISE/TX_RESUMO_IA/JQ_PESSOAS/JQ_ACHADOS do INQ_TB04_DOCUMENTOS.
# Disparada automaticamente ao anexar um documento que não é IPL, e de novo pelo botão no bloco
# do card — mesmo padrão de job em background das buscas automáticas (thread daemon + polling).
# =====================================================================

_LIMITE_ANALISE_CARACTERES = 100_000  # cabe folgado na janela de contexto dos provedores

# Teto de saída da 2ª tentativa da análise. O gateway openai-cert corta a resposta em
# MAX_TOKENS_POR_ESFORCO do esforço configurado (o raciocínio gasta o MESMO orçamento), e
# documentos grandes (planilhas) pedem mais saída que o teto do "low": o JSON chega pela metade
# e a 1ª tentativa falha no parse. Com este teto a resposta completa cabe (o prompt pede no
# máximo 30 pessoas + 50 achados).
_TETO_SAIDA_ANALISE_DOCUMENTO = 16384

SISTEMA_PROMPT_ANALISE_DOCUMENTO = (
    "Você é um analista de investigação criminal. Você recebeu o texto extraído de um documento "
    "anexado a um inquérito (PDF, planilha, texto). Analise-o e devolva SOMENTE um objeto JSON "
    "válido — sem markdown, sem texto antes ou depois — neste formato exato:\n"
    "{\n"
    '  "resumo": "parágrafo curto (até ~200 palavras) do que o documento contém e sua relevância",\n'
    '  "pessoas": [\n'
    '    {"no_pessoa": "nome completo ou apelido como consta no documento", '
    '"no_variantes": "outras grafias/abreviações citadas, ou vazio", '
    '"no_vulgo": "alcunha, se houver, ou vazio", "nu_cpf_cnpj": "CPF/CNPJ citado, ou vazio", '
    '"tp_papel": "papel no contexto (ex.: investigado, vítima, testemunha, advogado, delegado, envolvido)", '
    '"qt_mencoes": 1, "tx_contexto": "trecho curto do documento onde a pessoa aparece"}\n'
    '  ],\n'
    '  "achados": [\n'
    '    {"tp_achado": "telefone|email|valor|conta|agencia|processo|procedimento|tipificacao|endereco|oab|placa_veiculo|documento", '
    '"tx_valor": "o dado em si (ex.: (11) 99999-0000, joao@x.com, R$ 15.000,00, 123456-7)", '
    '"vl_numerico": 15000.0 se for valor numérico puro, senão null, '
    '"qt_ocorrencias": 1, "tx_contexto": "trecho curto do documento onde o achado aparece"}\n'
    '  ]\n'
    "}\n"
    "Regras: no máximo as 30 pessoas e os 50 achados mais relevantes; pessoa só entra se o "
    "documento a nomear de fato (nunca invente); se não houver, deixe a lista vazia. O resumo "
    "sempre preencha. Responda em português. "
    "Complemento determinístico: antes de você, um extrator automático já varreu o texto e "
    "achou os dados padronizados (CPF, CNPJ, telefones, e-mails, valores, contas, processos) — "
    "a chamada inclui a lista em 'Dados já extraídos automaticamente'. NÃO repita nenhum item "
    "dessa lista em achados; concentre-se no que só você alcança (pessoas, vínculos, "
    "tipificação, endereços). Use SOMENTE estes tp_achado: "
    "telefone|email|valor|conta|agencia|processo|procedimento|tipificacao|endereco|oab|"
    "placa_veiculo|documento — nunca cpf, cnpj, cpf_cnpj ou outro. Número de IPL "
    "(ex.: IPL 2024.0081190) é sempre 'procedimento', nunca 'processo'."
)


def _config_ia_documentos() -> dict | None:
    """Configuração da IA de análise de documentos (aba "Análise de documentos" em Configurações,
    DOC_AI_*). None quando nada foi configurado ali — aí a chamada cai na IA padrão do prompt,
    que é o comportamento antigo."""
    env = parse_env(read_env_text())
    if not (env.get("DOC_AI_PROVIDER") or env.get("DOC_AI_MODEL") or env.get("DOC_AI_API_URL")):
        return None
    return {
        "provider": env.get("DOC_AI_PROVIDER", ""),
        "model": env.get("DOC_AI_MODEL", ""),
        "apiUrl": env.get("DOC_AI_API_URL", ""),
        "apiKey": env.get("DOC_AI_API_KEY", ""),
        # vazios caem no da aba Prompt IA, como os demais campos (ver enviar_mensagem)
        "caCert": env.get("DOC_AI_CA_CERT", ""),
        "reasoningEffort": env.get("DOC_AI_REASONING_EFFORT", ""),
    }


def _ia_configurada(id_usuario: int | None = None) -> bool:
    """Há IA utilizável para analisar documento? Vale o mesmo preenchimento por campo que o
    enviar_mensagem faz: a IA própria do usuário vence, depois cada campo de DOC_AI_* sobrescreve o
    AI_* correspondente, e o que estiver vazio ali cai no do prompt.

    Contar a IA própria importa numa instalação que não configurou IA nenhuma no .env e só deu
    provedor a alguns analistas: sem isso o anexo desses analistas nunca dispararia a análise."""
    from funcoes.config_ia_usuario import config_para_ia
    if config_para_ia(id_usuario):
        return True
    env = parse_env(read_env_text())
    return all(
        env.get(f"DOC_AI_{campo}") or env.get(f"AI_{campo}")
        for campo in ("PROVIDER", "MODEL", "API_URL")
    )


def _extrair_json_analise(resposta: str) -> dict:
    """Extrai o objeto JSON da resposta da IA, tolerando cercas ```json e texto em volta."""
    resposta = (resposta or "").strip()
    inicio = resposta.find("{")
    fim = resposta.rfind("}")
    if inicio == -1 or fim == -1 or fim <= inicio:
        raise ValueError("A IA não devolveu um JSON válido na análise do documento.")
    try:
        dados = json.loads(resposta[inicio:fim + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON inválido na resposta da análise do documento: {exc}")
    if not isinstance(dados, dict):
        raise ValueError("A resposta da análise do documento não é um objeto.")
    return dados


def _vl_numerico_automatico(a: dict):
    """vl_numerico de um achado do pré-passe (só 'valor' tem numérico; o resto é null,
    como a IA devolve)."""
    if a.get("tp_achado") != "valor":
        return None
    try:
        return float(normalizar_valor(a.get("tx_valor") or ""))
    except ValueError:
        return None


# Fila global de análises: um documento anexado dispara uma análise, e um ZIP (ou pasta
# arrastada) anexa centenas de uma vez. Com um thread por documento, todos caíam na API de IA
# ao mesmo tempo; o provedor (limite de 1 requisição paralela por chave) respondia 429 em
# enxame e a fila inteira morria em 'erro' antes da sua vez. Agora os pedidos entram numa fila
# e UMA única worker os processa de um por vez, na ordem de chegada — e o candado advisory do
# Postgres (abaixo) estende o "um por vez" aos outros processos da API (uvicorn --workers 3),
# já que cada processo tem a própria fila.
_FILA_ANALISES: "queue.Queue[tuple[int, int, int | None, bool]]" = queue.Queue()
_FILA_TRAVA = threading.Lock()
_fila_worker_vivo = False

# chave do candado advisory (pg_advisory_lock): arbitraria, só não pode colidir com outra do app
_CANDADO_ANALISE_IA = 774_001

# Teto da espera pela vez no candado. Sem teto, um dono que trava o lock para sempre (ou um
# dono que MOROU e cujo lock só se solta quando o TCP dele expirar) pendura o ÚNICO worker da
# fila indefinidamente: a fila morre e _garantir_worker_analises não refaz, porque
# _fila_worker_vivo continua True. 6 h é o horizonte do próprio watchdog
# (_minutos_analise_stale): esperar além disso é esperar por uma linha que ele já declarou
# morta — melhor desistir com 'erro' honesto do que segurar a fila e o coração do pool.
_ESPERA_CANDADO_MAX_S = 6 * 3600.0


def _worker_analises() -> None:
    """Consome a fila para sempre: uma análise por vez, na ordem. Sobrevive a documento que
    falhar (o erro já fica na própria linha) — a fila continua para os próximos."""
    while True:
        item = _FILA_ANALISES.get()
        try:
            _processar_analise_documento(*item[:3], usar_ia=item[3] if len(item) > 3 else True)
        except Exception:
            logger.exception("Falha inesperada na análise enfileirada do documento %s", item[1])
        finally:
            _FILA_ANALISES.task_done()


def _garantir_worker_analises() -> None:
    """Sobe a thread consumidora se ela ainda não existe (idempotente, por processo)."""
    global _fila_worker_vivo
    with _FILA_TRAVA:
        if not _fila_worker_vivo:
            threading.Thread(target=_worker_analises, daemon=True, name="analise-documentos").start()
            _fila_worker_vivo = True


def iniciar_analise_documento(id_inquerito: int, id_documento: int, id_usuario: int | None,
                              usar_ia: bool = True) -> None:
    """Coloca a análise de entidades (resumo + pessoas + achados) de um documento anexado na
    fila global (a worker roda uma por vez — ver _FILA_ANALISES). Funciona SEM IA: o pré-passe
    determinístico (entidades_auto_utils) roda sempre e persiste sozinho; sem IA E sem nenhum
    padrão automático ativo é que levanta ValueError. Se já houver análise rodando no documento,
    a execução é ignorada sem erro.

    Estado 'pendente' DURÁVEL no banco (ver C10): a fila em memória morre com o processo, mas a
    linha marca que há trabalho a fazer — restart não perde mais a análise, só atrasa: o startup
    e o watchdog reenfileiram os 'pendente' órfãos (ver reenfileirar_analises_pendentes)."""
    if not (usar_ia and _ia_configurada(id_usuario)) and not _tem_padroes_ativos():
        raise ValueError("Nenhum provedor de IA configurado — configure em Configurações antes de analisar documentos.")
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE investigacao.inq_tb04_documentos
                SET cd_analise = 'pendente', dt_inicio_analise = now(),
                    dt_ultima_atividade_analise = now(), id_usuario_analise = %s
                WHERE id_documento = %s AND id_inquerito = %s
                  AND cd_analise NOT IN ('processando', 'pendente')
                """,
                (id_usuario, id_documento, id_inquerito),
            )
            enfileirou = cur.rowcount > 0
        conn.commit()
    if not enfileirou:
        return  # já pendente ou rodando: sem put duplicado
    _FILA_ANALISES.put((id_inquerito, id_documento, id_usuario, usar_ia))
    _garantir_worker_analises()


def reenfileirar_analises_pendentes() -> int:
    """Devolve à fila em memória os documentos em 'pendente' órfãos (worker morreu antes de
    pegar, ou item perdido no restart). Chamado no startup e a cada tick do watchdog.

    Só os envelhecidos (sem atividade há mais de um tick do watchdog): item fresco está na fila
    em memória de algum worker vivo, e put duplicado é inofensivo de todo jeito — o claim do
    _processar_analise_documento é atômico e o segundo a chegar não acha linha para reivindicar.
    O usar_ia original não é persistido: reenfileirado roda com True (se a IA foi configurada
    depois, melhor ainda). Devolve quantos reenfileirou."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id_inquerito, id_documento, id_usuario_analise
                FROM investigacao.inq_tb04_documentos
                WHERE cd_analise = 'pendente'
                  AND COALESCE(dt_ultima_atividade_analise, dt_inicio_analise, dt_processado, now())
                      < now() - make_interval(mins => %s)
                """,
                (int(_INTERVALO_WATCHDOG_ANALISES // 60),),
            )
            orfaos = cur.fetchall()
    for id_inquerito, id_documento, id_usuario in orfaos:
        _FILA_ANALISES.put((id_inquerito, id_documento, id_usuario, True))
    if orfaos:
        _garantir_worker_analises()
        logger.warning("Análises pendentes reenfileiradas: %s", ", ".join(map(str, [o[1] for o in orfaos])))
    return len(orfaos)


def _tem_padroes_ativos() -> bool:
    """Há algum padrão automático ativo? A consulta não pode derrubar a fila/análise."""
    try:
        return bool(listar_padroes_ativos())
    except Exception:
        logger.exception("Falha ao listar padrões automáticos")
        return False


def _achados_automaticos_completos(automaticos: list[dict]) -> list[dict]:
    """Achados do pré-passe no formato do JQ_ACHADOS (o mesmo objeto que a IA devolve)."""
    return [
        {
            "tp_achado": a["tp_achado"],
            "tx_valor": a["tx_valor"],
            "vl_numerico": _vl_numerico_automatico(a),
            "qt_ocorrencias": a["qt_ocorrencias"],
            "tx_contexto": a["tx_trecho"],
            "in_automatico": True,
        }
        for a in automaticos[:30]
    ]


def _fundir_achados(automaticos: list[dict], achados_ia: list) -> list[dict]:
    """Automáticos primeiro (validados por dígito/formato), a IA preenche o resto sem repetir
    o que já saiu daqui (mesma chave normalizada)."""
    vistos = set()
    achados = []
    for a in _achados_automaticos_completos(automaticos):
        achados.append(a)
        vistos.add(chave_achado(a["tp_achado"], a["tx_valor"]))
    for a in achados_ia or []:
        if not isinstance(a, dict) or not (a.get("tx_valor") or "").strip():
            continue
        chave = chave_achado(a.get("tp_achado"), a.get("tx_valor") or "")
        if chave in vistos:
            continue
        vistos.add(chave)
        achados.append(a)
    return achados[:50]


def _bater_analise(id_inquerito: int, id_documento: int, marco: datetime | None = None) -> None:
    """Atualiza DT_ULTIMA_ATIVIDADE_ANALISE — é o que diz ao watchdog que há vida, inclusive
    enquanto a análise espera a vez no candado global (ver C3). Best-effort: nunca derruba
    a análise que está em curso.

    Com `marco` (o DT_INICIO_ANALISE devolvido pela claim), a batida vale só enquanto a linha
    é a MESMA análise: se o watchdog ceifou a nossa e o usuário re-analisou, a linha tem outro
    carimbo e este worker parado no candado não bate pela análise alheia."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                if marco is None:
                    cur.execute(
                        "UPDATE investigacao.inq_tb04_documentos "
                        "SET dt_ultima_atividade_analise = now() "
                        "WHERE id_documento = %s AND id_inquerito = %s AND cd_analise = 'processando'",
                        (id_documento, id_inquerito),
                    )
                else:
                    cur.execute(
                        "UPDATE investigacao.inq_tb04_documentos "
                        "SET dt_ultima_atividade_analise = now() "
                        "WHERE id_documento = %s AND id_inquerito = %s AND cd_analise = 'processando' "
                        "  AND dt_inicio_analise = %s",
                        (id_documento, id_inquerito, marco),
                    )
            conn.commit()
    except Exception:
        logger.exception("Falha ao bater atividade da análise do documento %s", id_documento)


def _gravar_analise(id_inquerito: int, id_documento: int, resumo: str | None,
                    pessoas: list, achados: list, marco: datetime | None = None) -> None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            # Guarda de status como nos jobs (ver C2/M5): se o watchdog ceifou a análise no meio
            # do caminho, o worker vivo não sobrescreve 'erro' com 'concluido'. Com `marco` a
            # guarda é mais forte ainda: 'processando' SOZINHO não basta quando o usuário mandou
            # re-analisar — a linha ceifada virou 'processando' de OUTRA rodada, e o resultado
            # velho (zumbi que esperava o candado) não pode carimbar a rodada nova. O marco é o
            # DT_INICIO_ANALISE da nossa claim: quem mudou o carimbo perdeu a vez de gravar.
            if marco is None:
                cur.execute(
                    """
                    UPDATE investigacao.inq_tb04_documentos
                    SET cd_analise = 'concluido', dt_fim_analise = now(), tx_erro_analise = NULL,
                        tx_resumo_ia = %s, jq_pessoas = %s, jq_achados = %s
                    WHERE id_documento = %s AND id_inquerito = %s AND cd_analise = 'processando'
                    """,
                    (
                        resumo or None,
                        json.dumps(pessoas, ensure_ascii=False),
                        json.dumps(achados, ensure_ascii=False),
                        id_documento, id_inquerito,
                    ),
                )
            else:
                cur.execute(
                    """
                    UPDATE investigacao.inq_tb04_documentos
                    SET cd_analise = 'concluido', dt_fim_analise = now(), tx_erro_analise = NULL,
                        tx_resumo_ia = %s, jq_pessoas = %s, jq_achados = %s
                    WHERE id_documento = %s AND id_inquerito = %s AND cd_analise = 'processando'
                      AND dt_inicio_analise = %s
                    """,
                    (
                        resumo or None,
                        json.dumps(pessoas, ensure_ascii=False),
                        json.dumps(achados, ensure_ascii=False),
                        id_documento, id_inquerito, marco,
                    ),
                )
                if cur.rowcount == 0:
                    logger.warning(
                        "Resultado da análise do documento %s descartado: a linha foi ceifada e "
                        "reivindicada por outra rodada enquanto esta esperava/rodava", id_documento)
        conn.commit()


def _analisar_documento_ia(conteudo: str, mensagem_usuario: str, automaticos: list[dict],
                           auditoria: AuditoriaIA, inicio_turno: float,
                           id_inquerito: int, id_documento: int,
                           id_usuario: int | None,
                           marco: datetime | None = None) -> tuple[str, list, list]:
    """Chamada de IA propriamente dita (a parte CARA e instável da análise): devolve
    (resumo, pessoas, achados já fundidos com o pré-passe). Levanta em qualquer falha —
    quem chama decide o que fazer com o que o determinístico já achou."""
    # "um por vez" GLOBAL na API de IA: a fila já serializa dentro deste processo, e o
    # candado advisory numa conexão dedicada vale também para os outros workers do uvicorn
    # (e scripts soltos) — quem chega aqui espera a vez no pg_advisory_lock em vez de
    # tomar 429 do provedor (limite de 1 requisição paralela por chave). Se o processo
    # morrer, a conexão cai e o candado se solta sozinho.
    #
    # Conexão longa + autocommit, e não a do perfil web (ver C1): a chamada de IA dura minutos,
    # e segurar o lock numa transação aberta estourava idle_in_transaction (60 s) e prendia um
    # slot do pool pelo tempo inteiro. Advisory lock é de sessão — funciona em autocommit, e o
    # unlock do finally continua valendo.
    with get_connection(longa=True) as conn_candado:
        conn_candado.autocommit = True
        with conn_candado.cursor() as cur_candado:
            # Espera ocupada com batimento em vez do pg_advisory_lock bloqueante: a fila global
            # pode segurar a vez por horas, e o watchdog mede pela última atividade — sem bater
            # aqui, esperar a vez seria indistinguível de processo morto (ver C3).
            #
            # COM TETO (ver _ESPERA_CANDADO_MAX_S): sem teto, um lock cujo dono nunca o solta
            # pendurava este laço para sempre — o ÚNICO worker da fila, com uma conexão longa do
            # pool, enquanto o watchdog já tinha declarado a análise morta. Melhor um 'erro'
            # honesto re-analisável do que a fila inteira congelada até o restart do processo.
            espera_candado = 0.0
            while True:
                cur_candado.execute("SELECT pg_try_advisory_lock(%s)", (_CANDADO_ANALISE_IA,))
                row_lock = cur_candado.fetchone()
                if row_lock is not None and row_lock[0]:
                    break
                if espera_candado >= _ESPERA_CANDADO_MAX_S:
                    raise TimeoutError(
                        "A espera pela vez na API de IA passou do limite "
                        f"({int(_ESPERA_CANDADO_MAX_S // 3600)} h) e foi cancelada — o documento "
                        "pode ser analisado de novo em instantes."
                    )
                _bater_analise(id_inquerito, id_documento, marco)
                time.sleep(30.0)
                espera_candado += 30.0
            try:
                resposta, modelo = enviar_mensagem(
                    [{"role": "user", "content": mensagem_usuario}],
                    sistema=SISTEMA_PROMPT_ANALISE_DOCUMENTO,
                    auditoria=auditoria,
                    config=_config_ia_documentos(),
                    id_usuario=id_usuario,
                )
                try:
                    dados = _extrair_json_analise(resposta)
                except ValueError:
                    # A resposta pode ter vindo cortada pelo teto de saída do provedor (finish_reason
                    # "length"), deixando o JSON pela metade — planilhas grandes são o caso típico.
                    # Tenta uma vez com teto maior antes de desistir. Nos provedores que não enviam
                    # max_tokens o teto é ignorado e a 2ª chamada serve de nova chance para uma
                    # falha pontual do modelo.
                    resposta, modelo = enviar_mensagem(
                        [{"role": "user", "content": mensagem_usuario}],
                        sistema=SISTEMA_PROMPT_ANALISE_DOCUMENTO,
                        auditoria=auditoria,
                        config=_config_ia_documentos(),
                        id_usuario=id_usuario,
                        max_tokens_saida=_TETO_SAIDA_ANALISE_DOCUMENTO,
                    )
                    try:
                        dados = _extrair_json_analise(resposta)
                    except ValueError as exc:
                        raise ValueError(
                            "A resposta da IA não veio como JSON válido nas duas tentativas — a "
                            "saída pode ter sido cortada pelo limite de tokens do provedor. "
                            f"Detalhe da última tentativa: {exc}"
                        ) from exc
            finally:
                cur_candado.execute("SELECT pg_advisory_unlock(%s)", (_CANDADO_ANALISE_IA,))
    resumo = str(dados.get("resumo") or "").strip()[:4000]
    pessoas = [
        p for p in (dados.get("pessoas") or [])
        if isinstance(p, dict) and (p.get("no_pessoa") or "").strip()
    ][:30]
    achados = _fundir_achados(automaticos, dados.get("achados") or [])

    registrar_auditoria(
        "documento_analise", mensagem_usuario, SISTEMA_PROMPT_ANALISE_DOCUMENTO, auditoria, modelo,
        int((time.monotonic() - inicio_turno) * 1000), resposta=resposta,
        id_inquerito=id_inquerito, id_usuario=id_usuario,
    )
    return resumo, pessoas, achados


def _processar_analise_documento(id_inquerito: int, id_documento: int, id_usuario: int | None,
                                 usar_ia: bool = True) -> None:
    auditoria = AuditoriaIA()
    inicio_turno = time.monotonic()
    mensagem_usuario = ""
    # DT_INICIO_ANALISE devolvido pela claim: o "carimbo" desta análise na linha. Toda
    # gravação/batida posterior confere este valor — se o watchdog ceifou a linha e o usuário
    # re-analisou (claim nova, carimbo novo), este worker zumbi não escreve mais nada.
    marco: datetime | None = None
    try:
        # trava atômica da análise no próprio documento. O UPDATE condicional
        # com RETURNING é atômico em Postgres: duas threads concorrentes disputam
        # o row-lock; a segunda bloqueia até a primeira commitar e então
        # reavalia o WHERE, retornando 0 linhas se já virou 'processando'.
        # Estados permitidos para iniciar: 'pendente' (fila durável, ver C10), '' (nunca
        # analisado), 'concluido' e 'erro' (re-análise). 'processando' nunca pode ser
        # sobrescrito. Isso evita que dois workers marquem o mesmo documento como
        # 'processando' e dupliquem custo de IA.
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE investigacao.inq_tb04_documentos
                    SET cd_analise = 'processando', dt_inicio_analise = now(),
                        dt_ultima_atividade_analise = now(),
                        tx_erro_analise = NULL, id_usuario_analise = %s
                    WHERE id_documento = %s AND id_inquerito = %s
                      AND cd_analise IN ('pendente', '', 'concluido', 'erro')
                    RETURNING dt_inicio_analise
                    """,
                    (id_usuario, id_documento, id_inquerito),
                )
                linha_claim = cur.fetchone()
                if linha_claim is None:
                    return
                marco = linha_claim[0]
            conn.commit()

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT tx_conteudo, tx_erro FROM investigacao.inq_tb04_documentos "
                    "WHERE id_documento = %s AND id_inquerito = %s",
                    (id_documento, id_inquerito),
                )
                linha = cur.fetchone()
        if linha is None:
            raise ValueError("Documento não encontrado neste inquérito.")
        conteudo, erro = linha
        if erro:
            raise ValueError(erro)
        conteudo = (conteudo or "").strip()
        if not conteudo:
            raise ValueError("Documento sem texto extraído para analisar.")
        if len(conteudo) > _LIMITE_ANALISE_CARACTERES:
            conteudo = conteudo[:_LIMITE_ANALISE_CARACTERES]

        # Pré-passe determinístico (ver entidades_auto_utils): o que o regex com validação
        # já acha não precisa de IA — vai na mensagem para ela não repetir, e é fundido aos
        # achados dela depois. Diferente da IA, ELE SEMPRE PERSISTE: sem IA configurada, ou
        # com a IA quebrada, o documento sai 'concluido' só com os achados automáticos —
        # perder o determinístico porque o modelo falhou era o pior dos dois mundos.
        automaticos: list[dict] = []
        try:
            automaticos = extrair_automaticas_texto(conteudo, listar_padroes_ativos())
        except Exception:
            logger.exception("Pré-passe automático falhou no documento %s; segue só com a IA", id_documento)

        if usar_ia and _ia_configurada(id_usuario):
            if automaticos:
                ja = "\n".join(f"- {a['tp_achado']}: {a['tx_valor']}" for a in automaticos[:80])
                mensagem_usuario = (
                    f"Texto extraído do documento:\n\n{conteudo}\n\n"
                    f"Dados já extraídos automaticamente (NÃO os repita em achados):\n{ja}"
                )
            else:
                mensagem_usuario = f"Texto extraído do documento:\n\n{conteudo}"
            try:
                _bater_analise(id_inquerito, id_documento, marco)
                resumo, pessoas, achados = _analisar_documento_ia(
                    conteudo, mensagem_usuario, automaticos, auditoria, inicio_turno,
                    id_inquerito, id_documento, id_usuario, marco,
                )
            except Exception as exc:
                if not automaticos:
                    raise
                resumo = (
                    f"Extração automática: {len(automaticos)} dado(s) padronizado(s) encontrados. "
                    f"A análise por IA falhou ({str(exc)[:200]}) — os achados acima são só os determinísticos."
                )
                pessoas = []
                achados = _fundir_achados(automaticos, [])
                logger.warning("IA falhou na análise do documento %s; persistiu só o determinístico: %s",
                               id_documento, exc)
        else:
            if not automaticos:
                raise ValueError(
                    "Nenhum provedor de IA disponível e nenhum achado automático foi encontrado — "
                    "configure a IA em Configurações ou padrões na aba Entidades automatizadas."
                )
            resumo = (
                f"Extração automática: {len(automaticos)} dado(s) padronizado(s) encontrados "
                "(a IA não rodou — desligada ou não configurada — então não há resumo, pessoas "
                "nem achados de contexto; só os dados padronizados acima)."
            )
            pessoas = []
            achados = _fundir_achados(automaticos, [])

        _gravar_analise(id_inquerito, id_documento, resumo, pessoas, achados, marco)
    except Exception as exc:  # guarda a falha na própria linha, pra tela, em vez de ficar presa em "processando"
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    # A gravação do erro segue a mesma guarda do sucesso: com o marco da
                    # claim, um zumbi que enfim falhou (ex.: estourou o teto do candado
                    # DEPOIS de a linha ter sido ceifada e re-analisada) não pisa 'erro'
                    # sobre a análise nova de outra rodada.
                    if marco is None:
                        cur.execute(
                            """
                            UPDATE investigacao.inq_tb04_documentos
                            SET cd_analise = 'erro', dt_fim_analise = now(), tx_erro_analise = %s
                            WHERE id_documento = %s AND id_inquerito = %s
                            """,
                            (str(exc)[:2000], id_documento, id_inquerito),
                        )
                    else:
                        cur.execute(
                            """
                            UPDATE investigacao.inq_tb04_documentos
                            SET cd_analise = 'erro', dt_fim_analise = now(), tx_erro_analise = %s
                            WHERE id_documento = %s AND id_inquerito = %s
                              AND cd_analise = 'processando' AND dt_inicio_analise = %s
                            """,
                            (str(exc)[:2000], id_documento, id_inquerito, marco),
                        )
                conn.commit()
        except Exception:
            logger.exception("Falha ao gravar status de erro da análise do documento %s", id_documento)


def _minutos_analise_stale() -> int:
    """Idade mínima (min) de uma análise em 'processando' para considerá-la órfã. Ajustável por
    MINUTOS_ANALISE_DOC_STALE no .env, lido a cada uso como o resto do sistema."""
    try:
        return max(30, int(parse_env(read_env_text()).get("MINUTOS_ANALISE_DOC_STALE", "360")))
    except (ValueError, TypeError):
        return 360


# =====================================================================
# Análises de documento presas (o watchdog).
#
# Itens ainda NÃO pegos pela worker sobrevivem ao restart como 'pendente' no banco (fila durável,
# ver C10 e reenfileirar_analises_pendentes) — só o que já virou 'processando' precisa do resgate
# abaixo. Sem ele, um restart da API no meio de uma análise deixava a linha presa em
# 'processando' PARA SEMPRE — e como listar_inqueritos considera o inquérito "em andamento" se
# QUALQUER documento anexado está em 'pendente'/'processando', o card inteiro congela em
# "Processando…" e o polling da tela nunca para (caso real: inquérito preso desde
# 28/09 com o documento 485 travado depois de um restart da API).
# Irmão de liberar_extracoes_travadas e liberar_jobs_travados: o predicado é PURAMENTE TEMPORAL
# — com API_WORKERS > 1 não há como saber daqui quais linhas são de um processo morto; um
# "libera tudo no startup" mataria a análise de um worker vivo. Uma análise é duas chamadas de
# IA (com retentativa) mais a espera no candado advisory; 6 h é folgado para a fila mais lenta.
# =====================================================================

_INTERVALO_WATCHDOG_ANALISES = 30 * 60.0
_ESPERA_INICIAL_WATCHDOG = 90.0
_watchdog_analises_vivo = False
_watchdog_analises_trava = threading.Lock()


def liberar_analises_documentos_travadas() -> int:
    """Fecha análises de documento presas: linha em 'processando' sem atividade mais recente que
    _minutos_analise_stale() — o processo que a tocava morreu. Vira 'erro' com mensagem legível
    (o botão "Analisar" aceita re-análise a partir de 'erro'), e o badge do inquérito sai de
    "Processando…" sozinho. Devolve quantas liberou.

    O predicado mede pela ÚLTIMA ATIVIDADE (ver C3), não pelo início: a espera no candado global
    e a chamada de IA batem DT_ULTIMA_ATIVIDADE_ANALISE, então fila lenta legítima não é ceifada.
    Linhas antigas (sem batimento, de antes da migração 0054) caem no dt_inicio_analise como antes."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE investigacao.inq_tb04_documentos
                SET cd_analise = 'erro', dt_fim_analise = now(),
                    tx_erro_analise = coalesce(nullif(tx_erro_analise, ''),
                        'A análise foi interrompida (processo reiniciado) e encerrada automaticamente. '
                        'Clique em Analisar para tentar de novo.')
                WHERE cd_analise = 'processando'
                  AND COALESCE(dt_ultima_atividade_analise, dt_inicio_analise, dt_processado, now())
                      < now() - make_interval(mins => %s)
                RETURNING id_documento
                """,
                (_minutos_analise_stale(),),
            )
            liberadas = [r[0] for r in cur.fetchall()]
        conn.commit()
    if liberadas:
        logger.warning("Análises de documento presas liberadas: %s", ", ".join(map(str, liberadas)))
    return len(liberadas)


def _tique_watchdog() -> None:
    """Uma varredura. O UPDATE é idempotente e o predicado é temporal, então mesmo uma troca de
    líder no meio do tique não pisa em análise viva. O reenfileirar dos 'pendente' órfãos roda
    no mesmo tick (ver C10)."""
    try:
        liberar_analises_documentos_travadas()
    except Exception:
        logger.exception("Falha na varredura de análises de documento presas")
    try:
        reenfileirar_analises_pendentes()
    except Exception:
        logger.exception("Falha ao reenfileirar análises pendentes")


def iniciar_watchdog_analises() -> None:
    """Sobe o vigia das análises de documento presas no startup da API (main.py). Idempotente.

    Roda como LÍDER (funcoes/leader_utils.py): com N réplicas da API, N vigias varriendo a
    mesma tabela e reenfileirando os mesmos órfãos para as suas próprias filas em memória era
    só ruído — as reivindicações são atômicas, ninguém processava duas vezes, mas o log
    duplicava e o banco pagava N varreduras. Um líder basta; se ele tombar, outro assume."""
    global _watchdog_analises_vivo
    with _watchdog_analises_trava:
        if _watchdog_analises_vivo:
            return
        _watchdog_analises_vivo = True
        from funcoes import leader_utils

        leader_utils.iniciar_loop_lider("watchdog-analises", leader_utils.WATCHDOG_ANALISES,
                                        _tique_watchdog, _INTERVALO_WATCHDOG_ANALISES,
                                        _ESPERA_INICIAL_WATCHDOG)


# =====================================================================
# Grafo dos documentos genéricos: agrega as entidades que a análise de IA extraiu de TODOS os
# documentos anexados ao inquérito num único grafo — é o que faz um caso de inteligência
# (inquérito com IN_CASO_INTELIGENCIA, em geral só PDFs jogados dentro) montar a rede de
# entidades relevantes a partir dos seus documentos, como montava o antigo sistema de casos.
# Nada é gravado: o grafo deriva na hora do JQ_PESSOAS/JQ_ACHADOS dos documentos, então reflete
# sempre a última análise de cada um e não existe deduplicação a "refazer". A mesa
# (web/mesa_inquerito.js) pinta esses nós sob o filtro "Documentos genéricos (IA)".
# =====================================================================

# Teto de nós de achado no grafo: num caso com centenas de documentos os achados passam de
# mil, e a esmagadora maioria aparece num documento só (um telefone citado uma vez não é
# inteligência). Entra o achado que liga dois ou mais documentos ou foi citado várias vezes;
# ainda assim, no máximo os mais conectados abaixo.
_MAX_ACHADOS_NO_GRAFO = 600

# Teto de pessoas no grafo: com 280 documentos as citações únicas passam de mil. Acima do
# teto ficam as mais conectadas (nº de documentos, desempate por menções) — a periferia de
# citação única some do desenho, mas continua no visualizador de cada documento.
_MAX_PESSOAS_NO_GRAFO = 2000

# Teto de arestas por documento em caso grande (>50 documentos): um PDF longo cita centenas
# de pessoas/achados e sozinho já estoura o desenho. Acima do teto ficam as citações mais
# fortes (menções da pessoa, ocorrências do achado) e o resto vira contador no nó do
# documento (qt_citacoes_omitidas). Em caso pequeno não se aplica — nada muda.
_MAX_DOCS_PARA_TETO_ARESTA = 50
_MAX_ARESTAS_POR_DOCUMENTO = 100

# Máximo de documentos listados no detalhe de uma pessoa no grafo (o contador diz o total).
_MAX_DOCS_NO_DETALHE = 40


def _chave_normalizada(texto) -> str:
    """Chave que deduplica nomes e valores: maiúscula, sem acento, só letras e dígitos.
    "João da Silva", "JOAO DA SILVA" e "joao.dasilva" viram a mesma chave."""
    texto = unicodedata.normalize("NFD", str(texto or ""))
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]", "", texto.lower())


def _so_digitos(texto) -> str:
    return re.sub(r"\D", "", str(texto or ""))


def grafo_documentos_inquerito(
    id_inquerito: int,
    docs: list[int] | None = None,
    tipos_achado: list[str] | None = None,
    min_docs: int = 0,
    busca: str | None = None,
) -> dict:
    """Nós e arestas derivados da análise de IA dos documentos anexados ao inquérito.

    Formato pronto para a mesa desenhar: ``nos`` com ``id``/``tipo``/``rotulo``/``dado`` e
    ``arestas`` com ``a``/``b``/``tipo``/``dado``. Os ids têm prefixo próprio (dp=pessoa,
    da=achado, dd=documento) para nunca colidir com os "e"/"c" das camadas do banco.
    Pessoa se deduplica pelo CPF/CNPJ quando a IA o achou, senão pelo nome normalizado;
    achado, pelo tipo + valor normalizado. Quem aparece em mais de um documento é o fio da
    rede — por isso o documento entra como nó e a pessoa "pendurada" nele.

    Ponte com a camada do banco: achado cujo valor é um CPF/CNPJ válido (mesmo digitado com
    pontos e traço, e mesmo quando a IA etiquetou como "documento") e pessoa cujo CPF/CNPJ E
    nome batem com uma canônica ganham aresta para o nó "c<id_canonica>" da mesa — sem isso,
    o dossiê de um investigado fica flutuando ao lado da pessoa que o RIF/SIMBA já desenharam.

    Recorte server-side (para inquérito com centenas de documentos — sem params, devolve
    tudo como antes): ``docs`` restringe aos documentos listados (pessoas/achados passam a
    contar só esses); ``tipos_achado`` filtra tp_achado; ``min_docs`` exige citação em pelo
    menos N documentos (vale para pessoas e achados; para achados substitui a regra de
    relevância); ``busca`` é substring case-insensitive em nome/valor."""
    from funcoes.cpf_cnpj_utils import valida_cnpj, valida_cpf

    filtro_docs = {int(d) for d in (docs or []) if str(d).strip().lstrip("-").isdigit()}
    tipos_q = {str(t).strip().lower() for t in (tipos_achado or []) if str(t).strip()}
    busca_q = str(busca or "").strip().lower() or None
    try:
        min_docs = max(0, int(min_docs or 0))
    except (TypeError, ValueError):
        min_docs = 0
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id_documento, no_arquivo, tp_arquivo, tx_resumo_ia, jq_pessoas, jq_achados
                FROM investigacao.inq_tb04_documentos
                WHERE id_inquerito = %s AND cd_analise = 'concluido'
                  AND (jq_pessoas IS NOT NULL OR jq_achados IS NOT NULL)
                ORDER BY id_documento
                """,
                (id_inquerito,),
            )
            linhas = cur.fetchall()

            # Canônicas que se identificam por CPF/CNPJ ('cpf:…'/'cnpj:…' em NU_CHAVE): é nelas
            # que a ponte da camada de documentos vai encostar. Mesma chave pode aparecer em
            # 'pessoa' e 'empresa' — pessoa primeiro (é o dono mais provável do documento).
            cur.execute(
                """
                SELECT id_canonica, no_entidade, nu_chave
                FROM investigacao.ent_tb05_canonicas
                WHERE id_inquerito = %s AND (nu_chave LIKE 'cpf:%%' OR nu_chave LIKE 'cnpj:%%')
                ORDER BY (tp_entidade = 'pessoa') DESC, id_canonica
                """,
                (id_inquerito,),
            )
            canon_por_documento: dict[str, dict] = {}
            for id_canonica, no_entidade, nu_chave in cur.fetchall():
                digitos = _so_digitos(str(nu_chave).partition(":")[2])
                if digitos and digitos not in canon_por_documento:
                    canon_por_documento[digitos] = {
                        "id": f"c{id_canonica}",
                        "nome": str(no_entidade or "").strip(),
                        "nome_norm": _chave_normalizada(no_entidade),
                    }

    docs: dict[int, dict] = {}
    pessoas: dict[str, dict] = {}
    achados: dict[str, dict] = {}

    def _casa_busca(*textos: str) -> bool:
        if not busca_q:
            return True
        junto = " ".join(str(t or "") for t in textos).lower()
        return busca_q in junto

    for id_documento, no_arquivo, tp_arquivo, resumo, jq_pessoas, jq_achados in linhas:
        if filtro_docs and id_documento not in filtro_docs:
            continue
        nome_curto = nome_curto_documento(no_arquivo)
        docs[id_documento] = {
            "id_documento": id_documento,
            "no_arquivo": nome_curto,
            "tp_arquivo": tp_arquivo,
            "tx_resumo": (resumo or "")[:1200],
        }
        for p in jq_pessoas or []:
            if not isinstance(p, dict):
                continue
            nome = str(p.get("no_pessoa") or "").strip()[:300]
            if not nome:
                continue
            cpf = _so_digitos(p.get("nu_cpf_cnpj"))
            cpf = cpf if len(cpf) in (11, 14) else ""
            chave = f"cpf:{cpf}" if cpf else "nome:" + (_chave_normalizada(nome) or nome.lower())
            per = pessoas.get(chave)
            if per is None:
                per = pessoas[chave] = {
                    "no_pessoa": nome, "nu_cpf_cnpj": str(p.get("nu_cpf_cnpj") or "").strip()[:40] if cpf else "",
                    "no_vulgo": "", "variantes": set(), "papeis": {}, "qt_mencoes": 0, "docs": {},
                    "cpf_digitos": cpf, "nome_norm": _chave_normalizada(nome),
                }
            try:
                mencoes = max(1, int(p.get("qt_mencoes") or 1))
            except (TypeError, ValueError):
                mencoes = 1
            per["qt_mencoes"] += mencoes
            if not per["no_vulgo"]:
                per["no_vulgo"] = str(p.get("no_vulgo") or "").strip()[:200]
            for variante in re.split(r"[;,/]", str(p.get("no_variantes") or "")):
                variante = variante.strip()
                if variante and variante.lower() != nome.lower():
                    per["variantes"].add(variante[:200])
            papel = str(p.get("tp_papel") or "").strip()[:120]
            if papel:
                per["papeis"][papel] = per["papeis"].get(papel, 0) + 1
            anterior = per["docs"].get(id_documento)
            if anterior is None or mencoes > anterior["qt_mencoes"]:
                per["docs"][id_documento] = {
                    "qt_mencoes": mencoes,
                    "tx_contexto": str(p.get("tx_contexto") or "").strip()[:240],
                }

        for a in jq_achados or []:
            if not isinstance(a, dict):
                continue
            # normaliza variantes que a IA inventou (cpf/cnpj/endereço — ver produção):
            # sem isto cada grafia vira um nó distinto no grafo
            tp = normalizar_tp_achado(str(a.get("tp_achado") or "documento").strip().lower()[:40] or "documento")
            valor = str(a.get("tx_valor") or "").strip()[:200]
            if not valor:
                continue
            chave = tp + ":" + (_chave_normalizada(valor) or valor.lower())
            ach = achados.get(chave)
            if ach is None:
                ach = achados[chave] = {
                    "tp_achado": tp, "tx_valor": valor, "tx_contexto": "",
                    "qt_ocorrencias": 0, "docs": set(),
                }
            try:
                ach["qt_ocorrencias"] += max(1, int(a.get("qt_ocorrencias") or 1))
            except (TypeError, ValueError):
                ach["qt_ocorrencias"] += 1
            if not ach["tx_contexto"]:
                ach["tx_contexto"] = str(a.get("tx_contexto") or "").strip()[:240]
            ach["docs"].add(id_documento)

    nos: list[dict] = []
    arestas: list[dict] = []
    # peso por aresta doc (para o teto por documento): pessoa pesa menções, achado pesa
    # ocorrências — a citação forte sobrevive, a fraca vira contador no documento.
    peso_aresta: dict[tuple[str, str], float] = {}

    for id_documento, doc in docs.items():
        nos.append({
            "id": f"dd{id_documento}",
            "tipo": "doc_documento",
            "rotulo": doc["no_arquivo"],
            "dado": {**doc, "tx_comentario": doc["tx_resumo"]},
        })

    pessoas_lista = list(pessoas.values())
    if min_docs:
        pessoas_lista = [p for p in pessoas_lista if len(p["docs"]) >= min_docs]
    if busca_q:
        pessoas_lista = [p for p in pessoas_lista if _casa_busca(
            p["no_pessoa"], p["no_vulgo"], p["nu_cpf_cnpj"], " ".join(sorted(p["variantes"])))]
    # em caso grande, a periferia de citação única sai do desenho (segue no visualizador)
    pessoas_lista.sort(key=lambda p: (len(p["docs"]), p["qt_mencoes"]), reverse=True)
    pessoas_lista = pessoas_lista[:_MAX_PESSOAS_NO_GRAFO]
    for i, per in enumerate(pessoas_lista, start=1):
        papeis = sorted(per["papeis"].items(), key=lambda kv: -kv[1])
        variantes = ", ".join(sorted(per["variantes"])[:6])
        docs_ordenados = sorted(per["docs"].items(), key=lambda kv: -kv[1]["qt_mencoes"])
        nos.append({
            "id": f"dp{i}",
            "tipo": "doc_pessoa",
            "rotulo": per["no_pessoa"],
            "dado": {
                "no_pessoa": per["no_pessoa"],
                "nu_cpf_cnpj": per["nu_cpf_cnpj"],
                "no_vulgo": per["no_vulgo"],
                "tx_variantes": variantes,
                "tp_papel": ", ".join(p for p, _ in papeis[:4]),
                "qt_mencoes": per["qt_mencoes"],
                "qt_documentos": len(per["docs"]),
                # tx_comentario participa da busca da mesa (casaBusca) — o contexto entra inteiro
                "tx_comentario": " — ".join(filter(None, [per["no_vulgo"] and f"vulgo: {per['no_vulgo']}",
                                                           per["nu_cpf_cnpj"], variantes])),
                "documentos": [
                    {"id_documento": id_doc, "no_arquivo": docs[id_doc]["no_arquivo"],
                     "qt_mencoes": info["qt_mencoes"], "tx_contexto": info["tx_contexto"]}
                    for id_doc, info in docs_ordenados[:_MAX_DOCS_NO_DETALHE]
                ],
                "qt_documentos_omitidos": max(0, len(per["docs"]) - _MAX_DOCS_NO_DETALHE),
            },
        })
        for id_doc, info in per["docs"].items():
            arestas.append({
                "a": f"dp{i}", "b": f"dd{id_doc}",
                "tipo": "citado em (documento)",
                "dado": {"tx_detalhe": info["tx_contexto"]},
            })
            peso_aresta[(f"dp{i}", f"dd{id_doc}")] = float(info["qt_mencoes"])
        # Ponte com a camada do banco — só com CPF/CNPJ E nome batendo: a IA repete o CPF do
        # filtro do documento em toda pessoa citada ("ENDINGER THAYNA no CPF do Flávio"), e
        # ligar pelo documento sozinho amarraria gente errada na canônica.
        canon = canon_por_documento.get(per["cpf_digitos"]) if per["cpf_digitos"] else None
        if canon and (per["nome_norm"] == canon["nome_norm"]
                      or any(_chave_normalizada(v) == canon["nome_norm"] for v in per["variantes"])):
            arestas.append({
                "a": f"dp{i}", "b": canon["id"],
                "tipo": "mesmo CPF/CNPJ",
                "dado": {"tx_detalhe": "pessoa citada no documento com o mesmo CPF/CNPJ e nome da entidade da mesa"},
            })

    # só entra o achado que cruza documentos ou se repete — o resto é citação de roda.
    # Com min_docs o corte passa a ser ele (N documentos ou mais), ignorando a regra acima.
    if min_docs:
        relevantes = [a for a in achados.values() if len(a["docs"]) >= min_docs]
    else:
        relevantes = [a for a in achados.values() if len(a["docs"]) >= 2 or a["qt_ocorrencias"] >= 3]
    if tipos_q:
        relevantes = [a for a in relevantes if a["tp_achado"] in tipos_q]
    if busca_q:
        relevantes = [a for a in relevantes if _casa_busca(a["tx_valor"], a["tx_contexto"])]
    relevantes.sort(key=lambda a: (len(a["docs"]), a["qt_ocorrencias"]), reverse=True)
    for i, ach in enumerate(relevantes[:_MAX_ACHADOS_NO_GRAFO], start=1):
        nos.append({
            "id": f"da{i}",
            "tipo": "doc_achado",
            "rotulo": ach["tx_valor"],
            "dado": {
                "tp_achado": ach["tp_achado"], "tx_valor": ach["tx_valor"],
                "tx_contexto": ach["tx_contexto"],
                "qt_ocorrencias": ach["qt_ocorrencias"], "qt_documentos": len(ach["docs"]),
                "tx_comentario": ach["tx_contexto"],
                "documentos": [
                    {"id_documento": id_doc, "no_arquivo": docs[id_doc]["no_arquivo"]}
                    for id_doc in sorted(ach["docs"])[:_MAX_DOCS_NO_DETALHE]
                ],
                "qt_documentos_omitidos": max(0, len(ach["docs"]) - _MAX_DOCS_NO_DETALHE),
            },
        })
        for id_doc in ach["docs"]:
            arestas.append({
                "a": f"da{i}", "b": f"dd{id_doc}",
                "tipo": "achado em (documento)",
                "dado": {"tx_detalhe": ach["tp_achado"]},
            })
            peso_aresta[(f"da{i}", f"dd{id_doc}")] = float(ach["qt_ocorrencias"])
        # Ponte com a camada do banco: o valor é um CPF/CNPJ de verdade (dígito verificador
        # bate, com ou sem pontuação) e existe canônica com aquela chave → o achado É a
        # entidade. Vale também para o tipo "documento": a IA etiqueta assim o CPF que acha
        # em cabeçalho de dossiê ("DOCUMENTO: 149.408.157-14") — sem pontuação limpa aqui,
        # ele nunca encostava na pessoa que o RIF/SIMBA já desenharam ao lado.
        if ach["tp_achado"] in ("cpf", "cnpj", "cpf_cnpj", "documento"):
            digitos = _so_digitos(ach["tx_valor"])
            eh_cpf = len(digitos) == 11 and valida_cpf(digitos)
            if eh_cpf or (len(digitos) == 14 and valida_cnpj(digitos)):
                canon = canon_por_documento.get(digitos)
                if canon:
                    arestas.append({
                        "a": f"da{i}", "b": canon["id"],
                        "tipo": "é o CPF de" if eh_cpf else "é o CNPJ de",
                        "dado": {"tx_detalhe": f"o valor do achado ({ach['tx_valor']}) é o "
                                                "CPF/CNPJ da entidade da mesa"},
                    })

    # Teto de arestas por documento (só em caso grande): mantém as citações mais fortes de
    # cada documento e conta o resto no próprio nó dd (qt_citacoes_omitidas). Nós dp/da que
    # ficarem sem nenhuma aresta saem junto — eram só aquela citação fraca.
    if len(docs) > _MAX_DOCS_PARA_TETO_ARESTA:
        por_doc: dict[str, list] = {}
        for idx, a in enumerate(arestas):
            dd = a["b"] if a["b"].startswith("dd") else (a["a"] if a["a"].startswith("dd") else None)
            if dd is None:
                continue
            por_doc.setdefault(dd, []).append(idx)
        cortar: set[int] = set()
        omitidos_por_doc: dict[str, int] = {}
        for dd, idxs in por_doc.items():
            if len(idxs) <= _MAX_ARESTAS_POR_DOCUMENTO:
                continue
            idxs.sort(key=lambda k: peso_aresta.get(
                (arestas[k]["a"], arestas[k]["b"]), 0.0), reverse=True)
            for k in idxs[_MAX_ARESTAS_POR_DOCUMENTO:]:
                cortar.add(k)
            omitidos_por_doc[dd] = len(idxs) - _MAX_ARESTAS_POR_DOCUMENTO
        if cortar:
            arestas = [a for k, a in enumerate(arestas) if k not in cortar]
            com_aresta = set()
            for a in arestas:
                com_aresta.add(a["a"])
                com_aresta.add(a["b"])
            nos = [n for n in nos
                   if n["tipo"] == "doc_documento" or n["id"] in com_aresta]
            for n in nos:
                if n["tipo"] == "doc_documento" and n["id"] in omitidos_por_doc:
                    n["dado"]["qt_citacoes_omitidas"] = omitidos_por_doc[n["id"]]

    return {"nos": nos, "arestas": arestas}


# =====================================================================
# Visualizador de documento (web/documento.html)
# =====================================================================
# Antes desta tela o texto extraído só aparecia num modal de <pre> — em inquérito por
# investigacao.js — e a citação da IA não tinha para onde levar. O escopo serve para a rota
# validar o dono do documento ("inquerito" é o único hoje; o caso de inteligência usa as
# mesmas tabelas do inquérito).

_TABELAS_DOCUMENTO = {
    "inquerito": ("investigacao.inq_tb04_documentos", "id_inquerito"),
}


def _tabela_documento(escopo: str) -> tuple[str, str]:
    try:
        return _TABELAS_DOCUMENTO[escopo]
    except KeyError:
        raise ValueError(f"Escopo de documento inválido: {escopo!r}")


def nome_curto_documento(no_arquivo: str | None) -> str:
    """Só o arquivo, sem a pasta — NO_ARQUIVO guarda o caminho relativo a data/."""
    return (no_arquivo or "").replace("\\", "/").rstrip("/").split("/")[-1]


def caminho_original_documento(no_arquivo: str | None):
    """O arquivo original em data/, ou None se não estiver mais lá.

    Passa por organizacao_arquivos.resolver, que é quem barra ".." e caminho absoluto — o valor
    vem do banco, mas é ele que vira leitura de disco."""
    return resolver_em_dados(no_arquivo)


def obter_documento_visualizacao(escopo: str, id_pai: int, id_documento: int) -> dict | None:
    """O documento pronto para a tela: metadados + o texto já fatiado em páginas.

    None quando o documento não é deste inquérito/caso — quem chama devolve 404. Documento com
    TX_ERRO volta com `paginas` vazio e o erro preenchido: a tela mostra o motivo e ainda oferece
    o original, que é justamente o que o investigador quer ver quando a extração falhou."""
    tabela, coluna_pai = _tabela_documento(escopo)
    query = f"""
        SELECT id_documento, no_arquivo, tp_arquivo, qt_caracteres, tx_erro, tx_conteudo, dt_processado
        FROM {tabela}
        WHERE id_documento = %s AND {coluna_pai} = %s
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (id_documento, id_pai))
            linha = cur.fetchone()
            if linha is None:
                return None
            doc = dict(zip([d.name for d in cur.description], linha))

    conteudo = doc.pop("tx_conteudo", None) or ""
    paginas = paginas_do_texto(conteudo) if conteudo else []
    original = caminho_original_documento(doc.get("no_arquivo"))
    return {
        **doc,
        "no_curto": nome_curto_documento(doc.get("no_arquivo")),
        "paginas": paginas,
        "qt_paginas": len(paginas),
        # in_paginado=False + mais de zero páginas = documento extraído antes do marcador (ou
        # formato sem página nenhuma, como CSV): a tela explica a diferença e oferece reextrair
        "in_paginado": texto_paginado(conteudo),
        "in_original": original is not None,
        # o visualizador do navegador só abre PDF; nos outros o botão vira download do original
        "in_pdf": (doc.get("tp_arquivo") or "").lower() == "pdf",
    }


def resposta_documento_original(escopo: str, id_pai: int, id_documento: int):
    """FileResponse com o arquivo original — o PDF que a aba "Original" do visualizador embute.

    `inline` (e não `attachment`) porque o destino é um <iframe>: com attachment o navegador
    baixaria o arquivo em vez de exibi-lo. O nome sai em filename* codificado, senão um acento no
    nome do arquivo quebra o cabeçalho."""
    from fastapi.responses import FileResponse
    from urllib.parse import quote

    from funcoes.ufdr_parser import mime_de_extensao

    tabela, coluna_pai = _tabela_documento(escopo)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT no_arquivo FROM {tabela} WHERE id_documento = %s AND {coluna_pai} = %s",
                (id_documento, id_pai),
            )
            linha = cur.fetchone()
    if linha is None:
        raise HTTPException(status_code=404, detail="Documento não encontrado.")

    caminho = caminho_original_documento(linha[0])
    if caminho is None:
        raise HTTPException(
            status_code=404,
            detail=f"O arquivo original ({nome_curto_documento(linha[0])}) não está mais na pasta de dados.",
        )
    nome = nome_curto_documento(linha[0])
    return FileResponse(
        str(caminho),
        media_type=mime_de_extensao(nome),
        headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(nome)}"},
    )


def reextrair_texto_documento(escopo: str, id_pai: int, id_documento: int) -> dict:
    """Relê o arquivo original em data/ e regrava TX_CONTEUDO — é o que traz os marcadores de
    página para um documento anexado antes deles.

    Levanta ValueError com o motivo quando não dá (documento de outro inquérito/caso, arquivo
    que saiu de data/). A extração que falha não é erro de rota: grava TX_ERRO como o anexo
    original faria e devolve o motivo, para a tela mostrar o mesmo recado de sempre."""
    tabela, coluna_pai = _tabela_documento(escopo)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT no_arquivo FROM {tabela} WHERE id_documento = %s AND {coluna_pai} = %s",
                (id_documento, id_pai),
            )
            linha = cur.fetchone()
    if linha is None:
        raise ValueError("Documento não encontrado.")

    no_arquivo = linha[0]
    caminho = caminho_original_documento(no_arquivo)
    if caminho is None:
        raise ValueError(
            f"O arquivo original ({nome_curto_documento(no_arquivo)}) não está mais na pasta de "
            "dados — sem ele não há o que reextrair."
        )

    conteudo, erro = extrair_texto_documento(caminho)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE {tabela}
                   SET tx_conteudo = %s, qt_caracteres = %s, tx_erro = %s
                 WHERE id_documento = %s AND {coluna_pai} = %s
                """,
                (conteudo, len(conteudo) if conteudo else None, erro, id_documento, id_pai),
            )
            # o índice semântico guarda trechos do texto ANTIGO e só reindexa o que ainda não
            # está lá (embed_utils._SQL_DOCS_INDEXAR filtra por NOT EXISTS): sem apagar, o RAG
            # continuaria respondendo com o texto sem página. Só o inquérito é indexado.
            if escopo == "inquerito":
                cur.execute(
                    "DELETE FROM investigacao.emb_tb01_embeddings "
                    "WHERE id_inquerito = %s AND tp_fonte = 'doc' AND id_mensagem = %s",
                    (id_pai, id_documento),
                )
        conn.commit()

    logger.info(
        "Documento %s (%s %s) reextraído: %s caracteres%s",
        id_documento, escopo, id_pai, len(conteudo) if conteudo else 0,
        f" — erro: {erro}" if erro else "",
    )
    return {
        "qt_caracteres": len(conteudo) if conteudo else 0,
        "tx_erro": erro,
        "in_paginado": texto_paginado(conteudo or ""),
        "qt_paginas": len(paginas_do_texto(conteudo)) if conteudo else 0,
    }

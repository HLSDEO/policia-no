"""Extração 'Nuvem IPED': importa uma pasta-caso inteira processada pelo IPED (ferramenta
forense da polícia/perícia) — índice Lucene + blobs em storage-N.db + JRE/jars embutidos.
Formato verificado contra data/SIP/iped_google/ (ver docs/extracao_iped.md e
docs/extracao_java_iped.md para o relato completo da investigação).

Segue a mesma extração-ponte (INQ_TB02_EXTRACOES) que o UFDR usa: pasta em data/, thread em
background, tabela própria com status/fase/progresso, sync final de status na ponte.

Fase 1: cada item do caso vira uma linha em ARQ_TB01_ARQUIVOS (mesma tabela do UFDR),
carregando hash (MD5/SHA1/SHA256, que o IPED já calcula) e a categoria/contentType que o
IPED já classificou.

Fase 3: dois desvios, achados reais rodando contra o exemplo (não domínios UFED — o exemplo
não tem nenhum item `x-ufed-*`, ver docs/extracao_iped.md):
- category "Emails": o IPED já quebra o mbox em uma mensagem por item, com campos semânticos
  prontos (Communication:From/To/Date, Message-Subject, Message-Body) — mapeado direto pra
  EML_TB01_EMAILS, sem reler o mbox bruto.
- category "Contacts" (.vcf): o IPED NÃO quebra o vCard em contatos — o arquivo inteiro é um
  item só — busca o blob (iped_cache) e usa o parser de vCard de takeout_parsers (parse_vcf).

- demais produtos do Google (Chrome, Access Log Activity, Minha Atividade, Play Store,
  Calendar, Duo, Maps/Saved/Timeline, Keep, ADCS, Google Account): o caminho do item guarda o
  caminho interno do ZIP original depois de ">>", então `takeout_parsers.destino()` classifica
  e o parser correspondente entrega o dict que as tabelas de domínio já sabem gravar.

Os parsers vieram de funcoes/google_takeout_utils.py, removido junto com a extração "Nuvem
Google (ZIPs)" — hoje vivem em funcoes/takeout_parsers.py. Ver docs/extracao_iped.md."""

import email
import email.policy
import json
import logging
import os
import re
import subprocess
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from psycopg import errors as psycopg_errors
from psycopg.types.json import Jsonb

from funcoes import iped_campos as campos_iped
from funcoes import iped_marcadores
from funcoes.db_utils import get_connection
from funcoes.iped_dominios import Roteador
from funcoes.iped_wa_drive import Colecao as _ColecaoMidiaDrive
from funcoes.ufdr_parser import eh_metadado
from funcoes.organizacao_arquivos import CATEGORIA_CELULAR, CATEGORIA_NUVEM, organizar
from funcoes.iped_cache import fechar_conexoes as _fechar_conexoes_iped
from funcoes.iped_cache import ler_blob as _ler_blob_iped
from funcoes.iped_texto import TextosDoCaso
from funcoes.nuvem_metadados import gravar_metadado as _gravar_metadado_nuvem
from funcoes.nuvem_metadados import parse_sidecar as _parse_sidecar_nuvem
from funcoes.investigacao_utils import (
    PASTA_DADOS,
    _apagar_dados_extracao,
    _Batimento,
    _drenar_arquivos,
    _gravar_aplicativo,
    _gravar_autofill,
    _gravar_contato_agenda,
    _gravar_email,
    _gravar_ligacao,
    _gravar_localizacao,
    _gravar_nota,
    _gravar_pagina_visitada,
    _obter_ou_criar_inquerito,
    _subpasta_delegacia,
)
from funcoes.contas_nuvem_utils import (
    _gravar_acesso_conta,
    _gravar_agregado_conta,
    _gravar_atividade_conta,
    _gravar_conexao_conta,
    _gravar_dispositivo_conta,
    _gravar_evento_conta,
    _gravar_perfil_conta,
)

logger = logging.getLogger(__name__)

# Parsers do conteúdo dos produtos do Google e o mapa que classifica cada item pelo
# caminho interno preservado depois de ">>" — ver funcoes/takeout_parsers.py.
from funcoes.takeout_parsers import (
    _RE_CONTA,
    destino,
    parse_activity_csv,
    parse_adcs,
    parse_agregado_csv,
    parse_bookmarks_html,
    parse_chrome_autofill,
    parse_chrome_history,
    parse_duo_call_history,
    parse_geojson_places,
    parse_ics,
    parse_keep_note,
    parse_my_activity,
    parse_playstore_csv,
    parse_saved_places,
    parse_subscriber_info,
    parse_timeline_json,
    parse_vcf,
)


# ---------------------------------------------------------------------------
# Campo multivalorado
#
# O Lucene permite o mesmo nome de campo várias vezes no mesmo documento e o IPED usa isso a
# rodo. Desde que o IpedDumper passou a agrupar (ver o cabeçalho do IpedDumper.java), um campo
# chega como string quando tem um valor só e como LISTA quando tem vários — então nenhum
# `campos.get(...)` pode ser usado direto: `_valor` e `_valores` são a única porta de entrada.
#
# Antes disso o dumper emitia chave JSON repetida e o json.loads ficava só com a última
# ocorrência. No caso de exemplo do repo isso descartava 6.932 valores de Regex:URL, 5.272 de
# Regex:EMAIL e 5.106 de Regex:PHONE, além de dar categoria errada a 60 itens e comer
# destinatário de e-mail com mais de um Communication:To.
# ---------------------------------------------------------------------------

# A implementação mora em funcoes/iped_campos.py junto com as outras armadilhas do índice
# (sufixo de tipo `:number`/`:date`, campo binário que sai como null, linha do tempo
# timeStamp×timeEvent). Os apelidos ficam porque o módulo inteiro os usa em toda linha.
_valor = campos_iped.valor
_valores = campos_iped.valores
_juntar = campos_iped.juntar
_variantes = campos_iped.valor_variantes


# A limpeza dos dados da extração-ponte é `_apagar_dados_extracao`, a MESMA função que o UFDR
# usa — não existe lista própria aqui. Ela existiu enquanto a importação gravava em meia dúzia
# de tabelas; agora que grava conversa, ligação, senha, rede, conta e financeiro, duas listas em
# paralelo garantiriam que uma ficasse para trás, e "apaguei a importação e as conversas
# continuaram lá" é o tipo de sobra que vira contaminação entre inquéritos. O sidecar de nuvem
# (NUV_TB01), última coisa que ainda saía só por aqui, foi para lá também: apagar o INQUÉRITO
# inteiro passa por `_apagar_dados_extracao` e não por este módulo, e falhava com
# ForeignKeyViolation quando o caso tinha metadado de nuvem.

# Onde o dumper compilado (IpedDumper.class) mora dentro da imagem da API — ver api/Dockerfile
# e api/tools/iped_dumper/IpedDumper.java. Configurável via env só para facilitar teste local.
_DUMPER_DIR = Path(os.environ.get("IPED_DUMPER_DIR", "/app/tools/iped_dumper"))
_JAVA_BIN = os.environ.get("IPED_JAVA_BIN", "java")

_LOTE_ARQUIVOS = 2000   # mesmo espírito do buffer de 20 mil do UFDR, mas mais conservador:
                        # aqui cada item já chega em memória via JSON, não só uma tupla leve
_CHECKPOINT = 200       # cadência de commit/progresso/checagem de cancelamento, igual ao UFDR

# Posições na tupla que _item_para_arquivo monta (na ordem das colunas de _drenar_arquivos em
# investigacao_utils.py). O laço de importação lê CO_CAMINHO_ZIP e CO_HASH_SHA256 da tupla
# para registrar o binário no roteador (anexos de conversa) — mudar a tupla exige mudar aqui.
_IDX_CAMINHO = 8
_IDX_SHA256 = 11


def java_disponivel() -> bool:
    """True se há um `java` executável na imagem — usado para desabilitar a aba no front
    quando falta (mesmo padrão de ocr_disponivel() em ocr_utils.py)."""
    try:
        subprocess.run([_JAVA_BIN, "-version"], capture_output=True, timeout=10)
        return True
    except Exception:
        return False


def _pasta_modulo_iped(caminho: Path) -> Path | None:
    """Devolve a pasta 'módulo' do caso (a que tem IPEDConfig.txt/index/lib direto dentro),
    aceitando tanto essa pasta direto quanto a raiz do caso que a contém em 'iped/'.

    Descoberta na Fase 0: o IPEDSource oficial exige casePath/iped/index; mas o exemplo do
    repo já vem só com a pasta 'iped' (renomeada), sem o wrapper. O IpedDumper (Lucene puro)
    aceita as duas formas — ver resolveIndexDir() em IpedDumper.java — e esta função espelha
    a mesma lógica do lado Python, para validar a pasta antes de disparar o subprocesso."""
    if (caminho / "IPEDConfig.txt").is_file() and (caminho / "index").is_dir():
        return caminho
    via_modulo = caminho / "iped"
    if (via_modulo / "IPEDConfig.txt").is_file() and (via_modulo / "index").is_dir():
        return via_modulo
    return None


def listar_casos_iped_disponiveis(subpasta: str | None = None) -> list[dict]:
    """Lista as pastas em data/ que parecem casos do IPED (busca recursiva por
    IPEDConfig.txt — casos costumam estar aninhados em data/<delegacia>/<pedido>/),
    para o seletor da tela."""
    raiz = _subpasta_delegacia(subpasta)
    if not raiz.is_dir():
        return []
    pastas: set[Path] = set()
    for config in raiz.rglob("IPEDConfig.txt"):
        modulo = config.parent
        if not (modulo / "index").is_dir():
            continue
        caso = modulo.parent if modulo.name == "iped" else modulo
        pastas.add(caso)
    achados = []
    for caso in sorted(pastas):
        try:
            rel = caso.relative_to(PASTA_DADOS).as_posix()
        except ValueError:
            continue
        achados.append({"tx_pasta": rel, "no_pasta": caso.name})
    return achados


def _resolver_pasta_caso(tx_pasta: str, subpasta: str | None = None) -> Path:
    """Resolve a pasta em data/, barrando path traversal, e confere que é mesmo um caso do IPED."""
    tx_pasta_norm = (tx_pasta or "").strip().replace("\\", "/").strip("/")
    if not tx_pasta_norm or tx_pasta_norm.startswith("/") or ".." in tx_pasta_norm.split("/"):
        raise ValueError("Pasta inválida.")
    caminho = PASTA_DADOS / tx_pasta_norm
    if not caminho.is_dir():
        raise FileNotFoundError(tx_pasta)
    if _pasta_modulo_iped(caminho) is None:
        raise ValueError("A pasta não parece um caso do IPED (falta IPEDConfig.txt/index).")
    return caminho


# nº de itens lidos antes de desistir de detectar a conta na PRÉ-VISUALIZAÇÃO (subprocesso
# próprio, ver detectar_conta_iped) — a maioria dos casos reais tem o sinal forte
# (user_info.json ou nome de arquivo com e-mail) bem no início do índice.
_LIMITE_ITENS_DETECCAO = 3000


class _DetectorConta:
    """Acumula sinais de conta (e-mail) item a item — usado tanto pela pré-visualização
    (detectar_conta_iped, com subprocesso e limite de itens próprios) quanto pelo
    processamento real (_processar_importacao_iped, sem custo extra: os mesmos itens já
    estão sendo lidos por outro motivo — por isso a detecção não roda como um passo à parte
    na importação de verdade, só na pré-visualização).

    Dois sinais, em ordem de confiança: 1) um item "user_info.json" com campo "email" no
    JSON (visto no exemplo real: {"user": {"email": "...", ...}}); 2) o e-mail mais frequente
    no nome/caminho dos itens, no mesmo padrão do Google Takeout (_RE_CONTA) — Takeout ZIPs
    ainda não expandidos trazem o e-mail no próprio nome do arquivo."""

    def __init__(self, pasta_modulo: Path):
        self._pasta_modulo = pasta_modulo
        self._contagem: Counter = Counter()
        self.resultado: str | None = None

    def avaliar(self, campos: dict) -> None:
        if self.resultado:
            return
        nome = _valor(campos, "name") or ""
        if nome == "user_info.json":
            origem = _valor(campos, "dataSourcePath") or ""
            hash_md5 = _valor(campos, "md5") or _valor(campos, "hash")
            if origem and hash_md5:
                try:
                    dados = _ler_blob_iped(self._pasta_modulo, origem, hash_md5,
                                           _valor(campos, "idInDataSource") or "")
                    info = json.loads(dados.decode("utf-8", "replace"))
                    email = ((info.get("user") or {}).get("email") or "").strip()
                    if email and "@" in email:
                        self.resultado = email
                        return
                except (FileNotFoundError, ValueError, AttributeError):
                    pass
        m = _RE_CONTA.search(nome) or _RE_CONTA.search(_valor(campos, "path") or "")
        if m:
            self._contagem[m.group(1)] += 1

    def melhor_palpite(self) -> str | None:
        if self.resultado:
            return self.resultado
        if self._contagem:
            return self._contagem.most_common(1)[0][0]
        return None


def detectar_conta_iped(caminho: Path) -> str | None:
    """Pré-visualização (rota /iped-detectar-conta): roda um subprocesso Java próprio,
    limitado a _LIMITE_ITENS_DETECCAO itens, só para sugerir a conta antes de confirmar a
    importação de verdade — que faz a mesma detecção sem gastar um segundo subprocesso
    (ver _DetectorConta e _processar_importacao_iped)."""
    pasta_modulo = _pasta_modulo_iped(caminho)
    if pasta_modulo is None:
        return None
    caso_raiz = pasta_modulo.parent if pasta_modulo.name == "iped" else pasta_modulo
    classpath = _classpath_dumper(pasta_modulo)

    try:
        proc = subprocess.Popen(
            [_JAVA_BIN, "-cp", classpath, "IpedDumper", str(caso_raiz)],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace",
        )
    except FileNotFoundError:
        return None

    detector = _DetectorConta(pasta_modulo)
    try:
        for i, linha in enumerate(proc.stdout):
            if i >= _LIMITE_ITENS_DETECCAO or detector.resultado:
                break
            linha = linha.strip()
            if not linha:
                continue
            try:
                campos = (json.loads(linha).get("fields")) or {}
            except ValueError:
                continue
            detector.avaliar(campos)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    return detector.melhor_palpite()


def _renomear_pasta_para_conta(caminho: Path, conta: str, tx_pasta: str) -> str:
    """Renomeia a pasta do caso, dentro de data/, para o e-mail detectado — facilita achar o
    caso certo depois só pelo nome. Evita colisão anexando um sufixo numérico. Devolve o novo
    tx_pasta (relativo a PASTA_DADOS); devolve tx_pasta inalterado se a renomeação falhar
    (best-effort — nunca impede a importação de continuar)."""
    nome_seguro = re.sub(r'[<>:"/\\|?*]', "_", conta).strip()
    if not nome_seguro or caminho.name == nome_seguro:
        return tx_pasta
    destino = caminho.parent / nome_seguro
    sufixo = 2
    while destino.exists():
        destino = caminho.parent / f"{nome_seguro} ({sufixo})"
        sufixo += 1
    # a renomeação falha transitoriamente logo após o subprocesso de detecção terminar —
    # observado em teste real neste ambiente (Docker Desktop no Windows, bind mount de
    # data/): o `java -cp ... IpedDumper` do detectar_conta_iped mapeia o índice Lucene em
    # memória (MMapDirectory) e o SO demora um pouco a soltar esses mapeamentos depois que o
    # processo já terminou (proc.wait() só garante o processo morto, não os mmaps liberados)
    # — algumas tentativas com espera curta resolvem; ver docs/extracao_iped.md.
    for tentativa in range(8):
        try:
            caminho.rename(destino)
            break
        except OSError:
            if tentativa == 7:
                logger.warning("Não foi possível renomear a pasta do caso IPED %s para %s", caminho, destino)
                return tx_pasta
            time.sleep(1)
    return destino.relative_to(PASTA_DADOS).as_posix()


def _organizar_pasta_iped(id_extracao_iped: int, tx_pasta: str, tp_fonte: str) -> str:
    """Move a pasta-caso do IPED para data/<delegacia>/<inquérito>/<NUVEM|CELULAR>/ e devolve
    o novo caminho relativo — ou tx_pasta inalterado se não deu para mover (best-effort, como
    a renomeação para a conta). Quem grava no banco é _finalizar, com o valor devolvido aqui.

    `tp_fonte` é o mesmo "celular"|"nuvem" que o card exibe: um caso de celular vai para a
    pasta CELULAR/ como um .ufdr, não para NUVEM/ (antes todo caso — inclusive um UFDR
    decodificado de iPhone — ia para NUVEM/)."""
    categoria = CATEGORIA_CELULAR if tp_fonte == "celular" else CATEGORIA_NUVEM
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT i.nu_inquerito
                    FROM investigacao.ipd_tb01_extracoes p
                    JOIN investigacao.inq_tb01_inqueritos i ON i.id_inquerito = p.id_inquerito
                    WHERE p.id_extracao_iped = %s
                    """,
                    (id_extracao_iped,),
                )
                row = cur.fetchone()
        if row is None:
            return tx_pasta
        # o índice Lucene foi mapeado em memória pelo subprocesso e o storage do caso tem
        # conexões sqlite cacheadas: as duas coisas seguram a pasta e impedem o move
        _fechar_conexoes_iped(PASTA_DADOS / tx_pasta)
        return organizar(PASTA_DADOS / tx_pasta, row[0], categoria) or tx_pasta
    except Exception:
        logger.exception("Falha ao organizar a pasta do caso IPED %s (best-effort)", id_extracao_iped)
        return tx_pasta


def iniciar_importacao_iped(tx_pasta: str, numero_inquerito: str, id_usuario: int | None,
                            subpasta: str | None = None, conta: str | None = None,
                            delegacia: str | None = None,
                            in_caso_inteligencia: bool = False) -> dict:
    """Cria a extração-ponte + a linha própria em IPD_TB01_EXTRACOES (status 'pendente') e
    dispara o processamento em background; devolve os IDs.

    A identificação da conta e a renomeação da pasta (pedido explícito do usuário, pra
    facilitar achar o caso certo depois) acontecem DEPOIS que o processamento em background
    já leu o caso inteiro — não antes, e não bloqueando esta chamada. Duas razões: 1) evita
    rodar o subprocesso Java duas vezes (a detecção usa os mesmos sinais que a Fase 1/3 já
    passam por cima); 2) numa tentativa de rename logo após aquele subprocesso terminar,
    observei falha transitória neste ambiente (Docker Desktop no Windows) — o SO ainda não
    tinha soltado os mapeamentos de memória do índice Lucene; esperar até o fim do
    processamento inteiro dá tempo de sobra. `conta`, se informado (usuário já confirmou/
    editou via /iped-detectar-conta), pula a autodetecção mas a renomeação ainda só
    acontece ao final, pelo mesmo motivo."""
    _resolver_pasta_caso(tx_pasta, subpasta)  # só valida antes de tocar o banco

    with get_connection() as conn:
        with conn.cursor() as cur:
            id_inquerito = _obter_ou_criar_inquerito(cur, numero_inquerito, id_usuario, delegacia,
                                                     in_caso_inteligencia)
            cur.execute(
                "SELECT 1 FROM investigacao.ipd_tb01_extracoes WHERE id_inquerito = %s AND no_pasta = %s",
                (id_inquerito, tx_pasta),
            )
            if cur.fetchone():
                raise ValueError(
                    "Essa pasta já foi importada neste inquérito — apague a importação "
                    "existente para importar de novo.")
            try:
                cur.execute(
                    """
                    INSERT INTO investigacao.inq_tb02_extracoes
                        (id_inquerito, no_arquivo, no_dispositivo, no_fabricante, cd_status)
                    VALUES (%s, %s, %s, 'IPED', 'processando')
                    RETURNING id_extracao
                    """,
                    (id_inquerito, tx_pasta, conta),
                )
                id_extracao_ponte = cur.fetchone()[0]
                cur.execute(
                    """
                    INSERT INTO investigacao.ipd_tb01_extracoes
                        (id_inquerito, no_pasta, no_conta, cd_status, id_usuario, id_extracao)
                    VALUES (%s, %s, %s, 'pendente', %s, %s)
                    RETURNING id_extracao_iped
                    """,
                    (id_inquerito, tx_pasta, conta, id_usuario, id_extracao_ponte),
                )
                id_extracao_iped = cur.fetchone()[0]
            except psycopg_errors.UniqueViolation:
                # Dois cliques simultâneos passaram juntos pelo SELECT acima: quem perdeu o
                # empate esbarra na UQ02_IPD_TB01_INQUERITO_PASTA e recebe o mesmo erro de
                # sempre (a rota traduz ValueError em 400/409), em vez de 500 cru do banco —
                # e sem subir um segundo IpedDumper sobre a mesma pasta. Ver C6.
                conn.rollback()
                raise ValueError(
                    "Essa pasta já foi importada neste inquérito — apague a importação "
                    "existente para importar de novo.")
        conn.commit()

    threading.Thread(target=_importar_em_background, args=(id_extracao_iped,), daemon=True).start()
    return {"id_inquerito": id_inquerito, "id_extracao_iped": id_extracao_iped}


# ---------------------------------------------------------------------------
# status/fase/progresso/cancelamento — tabela própria (IPD_TB01), sync do status
# na extração-ponte só ao final: durante o processamento só a tabela específica é
# atualizada; a ponte existe para o card/listagem genérica de extrações, não para
# acompanhar fase a fase.
# ---------------------------------------------------------------------------

# Batimento da EXTRAÇÃO-PONTE (INQ_TB02), por importação do IPED em curso.
#
# A ponte nasce 'processando' e, durante todo o processamento, só IPD_TB01 é atualizada — decisão
# de projeto (ver o cabeçalho da seção de status abaixo): a ponte existe para o card genérico, não
# para acompanhar fase a fase. O efeito colateral era fatal: liberar_extracoes_travadas usa
# COALESCE(dt_ultima_atividade, dt_inicio_processamento, dt_upload) < now() - 60 min, e como os
# dois primeiros ficavam NULL valia o dt_upload, o instante do INSERT. Toda importação de caso
# grande — o dumper Java leva horas — virava "erro: Extração interrompida (liberada por
# inatividade)" aos 60 minutos, com o Java ainda rodando e o Python ainda gravando arquivos.
#
# É o mesmo problema que o UFDR já resolveu, então usa a mesma peça: _Batimento.iniciar_vigia()
# sobe uma thread daemon que toca DT_ULTIMA_ATIVIDADE a cada dois minutos em conexão curta
# própria. Fica registrado aqui por id_extracao_iped porque quem encerra é _finalizar, que é o
# funil de TODOS os desfechos (erro, cancelado, concluído) e não enxerga o objeto.
_BATIMENTOS_PONTE: dict[int, _Batimento] = {}
_BATIMENTOS_LOCK = threading.Lock()


def _iniciar_batimento_ponte(id_extracao_iped: int, id_extracao_ponte: int) -> None:
    batimento = _Batimento(id_extracao_ponte)
    with _BATIMENTOS_LOCK:
        anterior = _BATIMENTOS_PONTE.get(id_extracao_iped)
        _BATIMENTOS_PONTE[id_extracao_iped] = batimento
    if anterior is not None:
        anterior.parar_vigia()
    batimento.iniciar_vigia()


def _parar_batimento_ponte(id_extracao_iped: int) -> None:
    """Idempotente: chamada em _finalizar e de novo no finally da thread, como rede."""
    with _BATIMENTOS_LOCK:
        batimento = _BATIMENTOS_PONTE.pop(id_extracao_iped, None)
    if batimento is not None:
        batimento.parar_vigia()


def _marcar_fase_iped(conn, id_extracao_iped: int, fase: str | None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE investigacao.ipd_tb01_extracoes SET cd_fase = %s, qt_progresso_fase = NULL "
            "WHERE id_extracao_iped = %s",
            (fase, id_extracao_iped),
        )
    conn.commit()


def _marcar_progresso_iped(conn, id_extracao_iped: int, feitos: float, total: float,
                           pct_anterior: int = -1) -> int:
    if not total:
        return pct_anterior
    pct = min(99, max(0, int(feitos * 100 / total)))
    if pct != pct_anterior:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE investigacao.ipd_tb01_extracoes SET qt_progresso_fase = %s WHERE id_extracao_iped = %s",
                (pct, id_extracao_iped),
            )
        conn.commit()
    return pct


def _cancelamento_solicitado_iped(conn, id_extracao_iped: int) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT fl_cancelar FROM investigacao.ipd_tb01_extracoes WHERE id_extracao_iped = %s",
            (id_extracao_iped,))
        row = cur.fetchone()
        return bool(row and row[0])


def solicitar_cancelamento_iped(id_extracao_iped: int) -> bool:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE investigacao.ipd_tb01_extracoes
                SET fl_cancelar = true, cd_fase = 'cancelando', qt_progresso_fase = NULL
                WHERE id_extracao_iped = %s AND cd_status IN ('pendente', 'processando')
                RETURNING id_extracao_iped
                """,
                (id_extracao_iped,),
            )
            achou = cur.fetchone() is not None
        conn.commit()
    return achou


def _linhas(cur) -> list[dict]:
    colunas = [d.name for d in cur.description]
    return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


def _situacao_pasta_iped(no_pasta: str | None) -> dict:
    """Confere se a pasta-caso continua em data/. Os binários não são copiados para o banco —
    são lidos sob demanda do storage-N.db e do índice do caso — então, se a pasta saiu de
    data/, as prévias, downloads e anexos deixam de abrir. Devolve {in_arquivo_presente} para
    o card avisar o investigador (mesmo papel de _situacao_arquivo_ufdr para o .ufdr)."""
    if not no_pasta:
        return {"in_arquivo_presente": False}
    try:
        caminho = _resolver_pasta_caso(no_pasta)
    except (ValueError, FileNotFoundError):
        return {"in_arquivo_presente": False}
    if caminho.is_dir() and _pasta_modulo_iped(caminho) is not None:
        return {"in_arquivo_presente": True}
    return {"in_arquivo_presente": False}


def listar_extracoes_iped_inquerito(id_inquerito: int) -> list[dict]:
    """Uma linha por caso do IPED importado, com status/fase/progresso e contagens — o
    card usa para o título e os chips de contagem (mesmo papel de listar_extracoes_gtk_inquerito)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.id_extracao_iped, p.id_extracao, p.no_pasta, p.no_conta,
                       p.ds_versao_iped, p.qt_itens_total, p.qt_arquivos, p.qt_emails,
                       p.qt_contatos, p.qt_conversas, p.qt_localizacoes, p.qt_contas_financeiras,
                       p.qt_navegacao, p.qt_acessos, p.qt_acessos_agregados, p.qt_conexoes,
                       p.qt_atividades, p.qt_apps, p.qt_dispositivos, p.qt_perfil,
                       p.qt_eventos, p.qt_ligacoes, p.qt_notas, p.qt_metadados_nuvem,
                       p.qt_mensagens, p.qt_contas_usuario, p.qt_senhas, p.qt_autofill,
                        p.qt_redes, p.qt_marcadores,
                        -- "WhatsApp (parcial)" tem o que mostrar? Mídia do WhatsApp no backup do
                        -- Drive (wa_<numero>/) ou nos tars do iCloud. O card usa para o botão —
                        -- a extração-ponte não preenche QT_CONVERSAS num caso de nuvem, então os
                        -- botões de app não cobrem esta tela. strpos (não LIKE) porque a query é
                        -- parametrizada; só roda por caso importado, não por arquivo do inquérito.
                        CASE WHEN p.id_extracao IS NOT NULL THEN EXISTS (
                            SELECT 1 FROM investigacao.arq_tb01_arquivos a
                             WHERE a.id_extracao = p.id_extracao
                               AND (strpos(a.co_caminho_zip, 'files/Archives/Media') = 1
                                    OR strpos(a.tx_caminho, 'Drive Mobile Backups/wa_') > 0))
                        END AS tem_wa_parcial,
                        p.cd_status, p.cd_fase, p.qt_progresso_fase, p.tx_erro,
                        p.dt_importacao, u.no_usuario AS importado_por,
                        ap.no_apelido,
                        -- hash do arquivo-fonte: o IPED não tem hash próprio, mas a extração-ponte
                        -- (INQ_TB02) criada na importação guarda o dos arquivos copiados
                        e.co_hash_md5, e.co_hash_sha1, e.co_hash_sha256,
                       -- a natureza da fonte, deduzida do que o caso trouxe: os domínios
                       -- decodificados (conversa, senha, rede, autofill) só existem quando a fonte
                       -- é um CELULAR (UFDR decodificado / bancos de app); um caso de nuvem não os
                       -- produz. O card usa para o título ("Celular IPED" × "Nuvem IPED").
                       CASE WHEN coalesce(p.qt_conversas, 0) > 0
                                 OR coalesce(p.qt_senhas, 0) > 0
                                 OR coalesce(p.qt_redes, 0) > 0
                                 OR coalesce(p.qt_autofill, 0) > 0
                            THEN 'celular' ELSE 'nuvem' END AS tp_fonte,
                       -- prova compartilhada: esta importação é a cópia de uma de outro
                       -- inquérito (ver funcoes/compartilhar_prova.py). O card usa para o badge.
                       e.id_extracao_origem, e.nu_inquerito_origem, e.dt_compartilhamento
                FROM investigacao.ipd_tb01_extracoes p
                LEFT JOIN seguranca.usr_tb01_usuarios u ON u.id_usuario = p.id_usuario
                LEFT JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = p.id_extracao
                -- apelido livre do card (INQ_TB06_APELIDOS — ver menu "Mais ações" em investigacao.js)
                LEFT JOIN investigacao.inq_tb06_apelidos ap
                       ON ap.tp_prova = 'iped' AND ap.id_prova = p.id_extracao_iped
                WHERE p.id_inquerito = %s
                ORDER BY p.dt_importacao DESC
                """,
                (id_inquerito,),
            )
            linhas = _linhas(cur)
    # a pasta-caso precisa continuar em data/ para os binários abrirem — ver _situacao_pasta_iped
    for linha in linhas:
        linha.update(_situacao_pasta_iped(linha.get("no_pasta")))
    return linhas


def obter_id_inquerito_de_iped(id_extracao_iped: int) -> int | None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id_inquerito FROM investigacao.ipd_tb01_extracoes WHERE id_extracao_iped = %s",
                (id_extracao_iped,))
            row = cur.fetchone()
            return row[0] if row else None


def apagar_iped(id_extracao_iped: int) -> bool:
    """Remove a extração e tudo que veio dela (arquivos/e-mails/contatos, inclusive da
    extração-ponte). False se ainda estiver processando."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id_extracao, cd_status FROM investigacao.ipd_tb01_extracoes "
                "WHERE id_extracao_iped = %s",
                (id_extracao_iped,))
            row = cur.fetchone()
            if row is None:
                return False
            id_extracao_ponte, status = row
            if status in ("pendente", "processando"):
                return False
            if id_extracao_ponte is not None:
                _apagar_dados_extracao(cur, id_extracao_ponte)
            cur.execute("DELETE FROM investigacao.ipd_tb01_extracoes WHERE id_extracao_iped = %s",
                        (id_extracao_iped,))
            if id_extracao_ponte is not None:
                cur.execute("DELETE FROM investigacao.inq_tb02_extracoes WHERE id_extracao = %s",
                            (id_extracao_ponte,))
        conn.commit()
    return True


# ---------------------------------------------------------------------------
# Mapeamento item do IPED -> linha de ARQ_TB01_ARQUIVOS
# ---------------------------------------------------------------------------

_parse_iso = campos_iped.parse_data  # a implementação mora em iped_campos


# MIME que é conteúdo de documento de verdade. O que não casar com nenhuma das regras de
# _categoria_arquivo vira "Outro" — é onde caem .so, .dex, .db, .xml e o resto do sistema, e é o
# mesmo lugar para onde o UFDR os manda (ufdr_parser.categoria_de_extensao). Até aqui a função
# devolvia "Documento" como fallback e nunca emitia "Outro": o filtro "Conteúdo de usuário" da
# tela Comparar celulares (CATEGORIAS_USUARIO em comparar_celulares_utils) trata "Documento" como
# conteúdo do usuário, então cada .db de app da nuvem entrava no cruzamento como se fosse peça, e
# o filtro que existe justamente para tirar ruído não tirava nada.
_MIMES_DOCUMENTO = (
    "application/pdf", "application/rtf", "application/msword", "application/vnd.ms-",
    "application/vnd.openxmlformats-", "application/vnd.oasis.opendocument",
    "application/epub", "application/x-mobipocket", "application/json", "application/xml",
    "application/javascript", "message/", "text/",
)

_MIMES_COMPACTADO = (
    "application/zip", "application/x-rar-compressed", "application/x-7z-compressed",
    "application/gzip", "application/x-tar", "application/x-bzip2",
)


def _categoria_arquivo(campos: dict) -> str:
    """Categoria normalizada (Imagem/Vídeo/Áudio/Documento/Compactado/Outro), mesmo
    vocabulário usado pelo UFDR/Takeout (ufdr_parser.categoria_de_extensao) — pelo
    contentType que o IPED já calculou, não pela extensão do nome (que em itens extraídos
    de e-mail/container costuma ser vazia ou enganosa, ex. assunto do e-mail como "nome").
    Metadado de sincronização (sidecar do Photos, ``_file_metadata.json`` do Drive,
    MessageInformation do Gmail) sai na frente: vira a categoria própria, oculta por padrão
    na tela — ver ufdr_parser.eh_metadado."""
    if eh_metadado(_valor(campos, "name"), _valor(campos, "path")):
        return "Metadado"
    ct = (_valor(campos, "contentType") or "").lower()
    if ct.startswith("image/"):
        return "Imagem"
    if ct.startswith("video/"):
        return "Vídeo"
    if ct.startswith("audio/"):
        return "Áudio"
    if ct in _MIMES_COMPACTADO or "Compressed Archives" in _valores(campos, "category"):
        return "Compactado"
    if ct.startswith(_MIMES_DOCUMENTO):
        return "Documento"
    return "Outro"


# ---------------------------------------------------------------------------
# Identificadores que o IPED já extraiu por item
#
# A varredura dos campos "Regex:<TIPO>" e os normalizadores moraram aqui até a v2; hoje vivem
# em funcoes/iped_campos.py, porque o conjunto de tipos NÃO é fixo — vem do RegexConfig.txt do
# próprio caso. Medido nos casos do repo: um tem 34 tipos, outro tem 18, e uma operação pode
# acrescentar regra própria. A lista fechada de 8 campos que existia aqui descartava, entre
# outros, semente e chave privada de carteira cripto, conta bancária, CNH, boleto, título de
# eleitor e MAC — e ainda procurava "Regex:BITCOIN_ADDRESS", um nome que o IPED nunca emitiu
# (o real é CRYPTOCOIN_BITCOIN_ADDRESS, e era CRIPTOCOIN_ no 4.2.2 que processou o caso do repo).
# ---------------------------------------------------------------------------

def _identificadores_e_json(campos: dict):
    """TX_IDENTIFICADORES pronto para o COPY do psycopg (Jsonb) ou None — a coluna é JSONB.

    Junta o que veio dos campos Regex do IPED com o texto dos QR codes que ele decodificou
    (`QRCodeText`). O QR entra como mais um identificador em vez de coluna própria porque a
    pergunta é a mesma — "quais arquivos citam isto" — e o índice GIN já responde. Num
    celular é onde apareceria um BR Code de PIX; nos casos do repo são chaves de NF-e."""
    achados = campos_iped.identificadores_do_item(campos) or {}
    qr = campos_iped.qrcodes(campos)
    if qr:
        achados = {**achados, "qrcode": qr}
    return Jsonb(achados) if achados else None


# ---------------------------------------------------------------------------
# Triagem que o IPED já fez
#
# `nudityScore`/`nudityClass` (detecção de nudez), `face_count` (rostos na imagem) e
# `hashDb:status`/`hashDb:set` (hashset carregado no caso) são atributos extras que o IPED
# grava por item quando as tasks correspondentes estão ligadas. Nenhum é obrigatório: caso
# processado sem elas simplesmente não tem os campos, e as colunas ficam NULL.
# ---------------------------------------------------------------------------

def _alerta_hash(campos: dict) -> str | None:
    """"pedo (Projeto X)" quando o hash do item bateu com hashset carregado no caso. É ALERTA
    para triagem, não conclusão: quem decide é quem analisa."""
    status = _variantes(campos, "hashDb:status")
    conjuntos = _valores(campos, "hashDb:set")
    if not status and not conjuntos:
        return None
    texto = str(status or "").strip()
    if conjuntos:
        texto = f"{texto} ({', '.join(str(c).strip() for c in conjuntos if c)})".strip()
    return texto[:200] or None


def _numero_real(campos: dict, chave: str) -> float | None:
    bruto = _variantes(campos, chave)
    if bruto in (None, ""):
        return None
    try:
        return float(str(bruto).strip().replace(",", "."))
    except ValueError:
        return None


def _inteiro_ou_nulo(bruto) -> int | None:
    if bruto in (None, ""):
        return None
    try:
        return int(float(str(bruto).strip()))
    except (TypeError, ValueError):
        return None


def _item_para_arquivo(id_extracao: int, campos: dict, textos: TextosDoCaso | None = None,
                       marcadores=None) -> tuple | None:
    """Converte um item do dumper (dict de campos do Lucene) numa tupla pronta para
    investigacao_utils._drenar_arquivos. None quando o item deve ser pulado (pasta, item
    vazio) — o roteamento de domínio desvia chat e mensagem antes de chegar aqui; o resto vira
    arquivo genérico, inclusive contato e e-mail, que são arquivos de verdade no aparelho.

    `textos`, quando informado, é o leitor dos SQLite de OCR/transcrição do caso
    (funcoes/iped_texto.py); `marcadores`, o que o perito marcou dentro do IPED
    (funcoes/iped_marcadores.py). Os dois são opcionais: sem eles as colunas saem NULL, que é
    o comportamento de antes."""
    categorias = _valores(campos, "category")
    if _valor(campos, "isDir") == "true" or ("Folders" in categorias or "Empty Files" in categorias):
        return None
    nome = (_valor(campos, "name") or "").strip()
    if not nome:
        return None

    tamanho = _valor(campos, "size")
    try:
        tamanho = int(tamanho) if tamanho not in (None, "") else None
    except ValueError:
        tamanho = None

    # Ponteiro pro binário: "IPED::<id_extracao>::<dataSourcePath>::<hash>::<idInDataSource>"
    # — resolvido por iped_cache.ler_blob. Usa o ID da extração-ponte, NÃO o caminho da pasta
    # do caso: a pasta pode ser renomeada DEPOIS que este item já foi gravado (a identificação
    # da conta só roda ao final da importação — ver _processar_importacao_iped), então gravar
    # o caminho aqui deixaria o ponteiro desatualizado assim que a pasta fosse renomeada.
    # _resolver_arquivo_iped busca o no_pasta atual em IPD_TB01_EXTRACOES na hora de servir.
    # Sem dataSourcePath/hash não há como servir o binário depois (item só de metadado) —
    # fica NULL, mesmo comportamento do UFDR/Takeout para arquivo sem binário localizado.
    origem = _valor(campos, "dataSourcePath") or ""
    hash_md5 = _valor(campos, "md5") or _valor(campos, "hash")
    id_fonte = _valor(campos, "idInDataSource") or ""
    caminho_zip = f"IPED::{id_extracao}::{origem}::{hash_md5}::{id_fonte}" if (origem and hash_md5) else None

    # OCR e transcrição vêm prontos do caso, chaveados pelo MD5 do item — o IPED já pagou o
    # custo de reconhecer/transcrever durante o processamento. DT_OCR marca "já processado"
    # para a fila de OCR do analyser (índice IX78) não reprocessar o que já tem texto; fica
    # NULL quando não veio OCR nenhum, para o item continuar elegível à fila.
    ocr = textos.ocr(hash_md5) if textos is not None else None
    transcricao = textos.transcricao(hash_md5) if textos is not None else None

    # Datas: `modified` do índice ainda manda, mas `created`/`accessed` vêm string VAZIA na
    # maioria dos casos e a data de verdade está no par timeStamp × timeEvent — ver
    # iped_campos.datas_do_item.
    datas = campos_iped.datas_do_item(campos)
    marcado, selecionado = marcadores.de(_valor(campos, "id")) if marcadores else (None, None)

    return (
        id_extracao,
        nome[:2000],
        _valor(campos, "path"),
        _categoria_arquivo(campos),
        _valor(campos, "contentType") or "application/octet-stream",
        tamanho,
        datas["modificacao"],
        "Deleted" if _valor(campos, "deleted") == "true" else "Intact",
        caminho_zip,
        hash_md5,
        _valor(campos, "sha-1"),
        _valor(campos, "sha-256"),
        ocr,
        datetime.now(timezone.utc).replace(tzinfo=None) if ocr else None,  # dt_ocr
        None,  # nu_id_ufdr — identificador do Physical Analyzer; o do IPED vai em co_track_id
        transcricao,
        _identificadores_e_json(campos),
        _valor(campos, "extMismatch") == "true",
        _valor(campos, "carved") == "true",
        datas["criacao"],
        datas["acesso"],
        Jsonb(datas["tempos"]) if datas["tempos"] else None,
        _valor(campos, "trackId") or _valor(campos, "globalId") or None,
        _inteiro_ou_nulo(_valor(campos, "id")),
        _inteiro_ou_nulo(_valor(campos, "parentId")),
        _valor(campos, "evidenceUUID") or None,
        _numero_real(campos, "nudityScore"),
        _variantes(campos, "nudityClass") or None,
        _inteiro_ou_nulo(_variantes(campos, "face_count")),
        _alerta_hash(campos),
        marcado,
        selecionado,
    )


def _registrar_transcricao_audio(roteador, campos: dict, textos) -> None:
    """Liga a transcrição de um binário de áudio/vídeo ao Roteador, pelo SHA-256 — a chave que
    `linkedItems` da mensagem usa para apontar o binário. Só itens com SHA-256 e transcrição
    entram; o resto é um lookup barato.

    A transcrição pode vir de duas fontes: o campo `audio:transcription` do índice (que o
    UfedXmlReader grava a partir do `Transcript` do UFDR) ou o `text/transcriptions.db` do
    AudioTranscriptTask do IPED (chaveado por MD5, ver funcoes/iped_texto.py)."""
    sha256 = _valor(campos, "sha-256")
    if not sha256:
        return
    transcricao = _variantes(campos, "audio:transcription")
    if not transcricao and textos is not None:
        transcricao = textos.transcricao(_valor(campos, "md5") or _valor(campos, "hash"))
    if transcricao:
        roteador.registrar_transcricao(sha256, transcricao)


# Teto do corpo lido do .eml. Um e-mail com uma planilha colada inteira no HTML rende
# megabytes; TX_CORPO alimenta índice trigrama e busca semântica, e passar disso incha o
# índice sem melhorar a busca. Mesmo espírito do corte de iped_texto.LIMITE_CARACTERES.
_LIMITE_CORPO_EMAIL = 200_000

# Padrão clássico de texto UTF-8 lido como Latin-1 ("Olá" -> "OlÃ¡"). Medido no caso de
# nuvem do repo: 152 de 1.342 e-mails têm o Message-Body do ÍNDICE assim — o IPED decodificou
# o quoted-printable do mbox com o charset errado — enquanto o .eml no storage decodifica
# certo com a biblioteca de e-mail do Python. O índice com mojibake é substituído pelo corpo
# lido do blob (ver _email_de_item).
_RE_MOJIBAKE = re.compile(r"[ÃÂ][\x80-\xbf]")


def _parece_mojibake(texto: str | None) -> bool:
    return bool(texto) and bool(_RE_MOJIBAKE.search(texto[:2000]))


def _corpo_parece_previa(corpo: str | None, text_size: str | None) -> bool:
    """True quando o Message-Body do índice é mais curto que o texto que o PRÓPRIO item declara
    no campo `textSize` — ou seja, o que chegou é prévia, não o corpo.

    O IPED grava o corpo no índice só até ~132 caracteres (medido num caso de 38.462 e-mails:
    ~32 mil corpos paravam exatamente nesse comprimento e nenhum passava dele, enquanto o
    `textSize` declarado pelos itens chegava a 22 mil caracteres). O texto integral só existe
    no blob .eml do storage — é o que a tela do próprio IPED mostra. A tolerância de 64 existe
    porque `textSize` é o tamanho do TEXTO EXTRAÍDO (do HTML renderizado, com cabeçalhos de
    link etc.), não a contagem de caracteres do corpo decodificado: corpo completo costuma
    declared um textSize da mesma ordem, nunca 50x maior."""
    if not corpo or not text_size:
        return False
    try:
        return int(text_size) > len(corpo) + 64
    except ValueError:
        return False


def _corpo_e_anexos_do_eml(pasta_modulo: Path, campos: dict) -> tuple[str | None, str | None]:
    """(corpo, nomes dos anexos) lidos do .eml bruto no storage do caso.

    Por que é preciso: `Message-Body` NÃO EXISTE nos itens que o RFC822Parser do IPED produz —
    conferido nos 8 e-mails do caso iCloud do repo, nenhum tem o campo. O texto extraído
    também não dá para recuperar do índice: `IndexTask` grava o campo `content` com
    `Field.Store.NO` (é justamente por isso que metade dos documentos do índice não tem campo
    armazenado nenhum — são fragmentos de texto, e o dumper os pula). Sem isto, e-mail de caso
    de nuvem entrava no banco com o corpo vazio.

    Best-effort em toda linha: sem blob, com blob corrompido ou com codificação exótica,
    devolve (None, None) e o e-mail entra com o que o índice tiver."""
    dados = _blob_do_item(pasta_modulo, campos)
    if not dados:
        return (None, None)
    try:
        msg = email.message_from_bytes(dados, policy=email.policy.default)
    except Exception:
        return (None, None)

    corpo = None
    try:
        parte = msg.get_body(preferencelist=("plain", "html"))
        if parte is not None:
            corpo = parte.get_content()
    except Exception:
        corpo = None
    if corpo:
        corpo = str(corpo).strip()[:_LIMITE_CORPO_EMAIL] or None

    anexos: list[str] = []
    try:
        for parte in msg.walk():
            nome = parte.get_filename()
            if nome and nome not in anexos:
                anexos.append(str(nome)[:500])
    except Exception:
        pass
    return (corpo, ", ".join(anexos)[:3000] or None)


def _email_de_item(campos: dict, pasta_modulo: Path | None = None) -> dict | None:
    """Converte um item category=='Emails' (o IPED já quebra o mbox em uma mensagem por
    item, com campos semânticos prontos) num dict pronto para investigacao_utils._gravar_email.
    None se faltar remetente/assunto/corpo — item degenerado (ex. cabeçalho corrompido).

    Destinatário, cópia e cópia oculta passam por `_juntar`: e-mail com vários destinatários
    grava um `Communication:To` por destinatário, e antes da correção do dumper só o último
    chegava aqui — a lista de quem recebeu a mensagem ficava truncada em um nome.

    `X-Gmail-Labels` e `X-GM-THRID` vêm dos cabeçalhos crus que o IPED indexa em todo item de
    um Takeout do Gmail. O THRID é o que reconstrói a conversa: todos os e-mails de uma troca
    compartilham o mesmo valor, mesmo quando alguém reescreveu o assunto.

    CORPO E ANEXO têm duas vias, nesta ordem: os campos do índice (que existem quando o e-mail
    veio de um mbox do Takeout) e, quando faltam — ou quando o do índice é PREVIEW (o IPED só
    grava ~132 caracteres no índice; ver `_corpo_parece_previa`) —, o .eml bruto lido do storage
    do caso (`pasta_modulo`) — ver `_corpo_e_anexos_do_eml`. Quando o blob responde, vale o
    corpo MAIS LONGO entre os dois: o do índice nunca é maior que o real, e o mais longo é o
    integral."""
    remetente = _valor(campos, "Communication:From")
    assunto = _valor(campos, "Message-Subject") or _valor(campos, "name")
    corpo = _valor(campos, "Message-Body")
    anexos = _juntar(campos, "Message:AttachmentNames")
    qt_anexos = _valor(campos, "Message:AttachmentCount")

    # O .eml bruto só é lido quando PRECISA: corpo ausente, com mojibake (o IPED decodificou
    # o quoted-printable com charset errado — ver _RE_MOJIBAKE) ou como prévia de 132 caracteres
    # (ver _corpo_parece_previa), ou quando o índice declara anexo e não traz os nomes.
    # AttachmentCount == "0" sem esses motivos dispensa a leitura do blob inteiro, que antes
    # acontecia para TODO e-mail sem anexo só para confirmar que não havia anexo.
    precisa_blob = ((not corpo or _parece_mojibake(corpo)
                     or _corpo_parece_previa(corpo, _valor(campos, "textSize")))
                    or (not anexos and qt_anexos not in (None, "", "0")))
    if pasta_modulo is not None and precisa_blob:
        corpo_eml, anexos_eml = _corpo_e_anexos_do_eml(pasta_modulo, campos)
        if corpo_eml and (not corpo or _parece_mojibake(corpo) or len(corpo_eml) > len(corpo)):
            corpo = corpo_eml
        anexos = anexos or anexos_eml

    if not (remetente or assunto or corpo):
        return None
    rotulos = _juntar(campos, "Message:Raw-Header:X-Gmail-Labels")
    return {
        "fonte": "IPED",
        "remetente": remetente,
        "destinatarios": _juntar(campos, "Communication:To", "Message-Recipient-Address"),
        "cc": _juntar(campos, "Message-Cc", "Message:Raw-Header:Cc"),
        "cco": _juntar(campos, "Message-Bcc", "Message:Raw-Header:Bcc"),
        "assunto": assunto,
        "corpo": corpo,
        "data": _parse_iso(_valor(campos, "Communication:Date")
                           or _valor(campos, "Message-Sent-Date")),
        "tipo": _valor(campos, "contentType") or "message/rfc822",
        "thread": (_valor(campos, "Message:Raw-Header:X-GM-THRID") or "")[:80] or None,
        "mensagem": (_valor(campos, "Message:Raw-Header:Message-ID") or "")[:998] or None,
        # o Gmail entrega a caixa como rótulo ("Inbox", "Sent", "Spam"), não como pasta —
        # NO_PASTA fica com o primeiro rótulo, que é o que a tela de e-mails exibe como origem
        "pasta": (rotulos or "").split(",")[0].strip() or None,
        "rotulos": rotulos,
        "anexos": anexos,
        # campos que só existem no UFDR (status de leitura, conta dona da caixa, estado do
        # registro no Physical Analyzer). Vão explícitos como None porque _gravar_email os
        # exige como chave nomeada — omiti-los quebra a query inteira, não só a coluna.
        "status": None,
        "conta": None,
        "estado": None,
    }


def _caminho_interno(campos: dict) -> str:
    """Caminho do item dentro do ZIP original do Takeout. O IPED expande containers e
    guarda o caminho como "<...>.Chrome.History_001.zip>>Chrome/History.json" — o que vem
    depois do último ">>" é exatamente o caminho que o Takeout usava, e é ele que
    takeout_parsers.destino() sabe classificar."""
    caminho = _valor(campos, "path") or ""
    return caminho.rsplit(">>", 1)[-1].lstrip("/") if ">>" in caminho else ""


def _blob_do_item(pasta_modulo: Path, campos: dict) -> bytes | None:
    """Conteúdo do item, lido do storage-N.db do caso. None quando o item não tem blob
    (diretório, item vazio) ou o storage não o tem — best-effort, não derruba a importação."""
    origem = _valor(campos, "dataSourcePath") or ""
    hash_md5 = _valor(campos, "md5") or _valor(campos, "hash")
    if not (origem and hash_md5):
        return None
    try:
        return _ler_blob_iped(pasta_modulo, origem, hash_md5,
                              _valor(campos, "idInDataSource") or "")
    except FileNotFoundError:
        return None


# A implementação mora em iped_campos, porque o roteamento por domínio (iped_dominios) monta
# linhas com a mesma tolerância a campo ausente e não pode importar daqui (importaria em
# círculo).
_LinhaParcial = campos_iped.LinhaParcial


# destino de takeout_parsers -> (função de gravação, chave do contador). Os destinos que o
# IPED já resolve sozinho ficam de fora de propósito: "emails"/"contatos" (o IPED quebra o
# mbox e o vCard em itens próprios, category Emails/Contacts) e "arquivos" (todo item vira
# linha em ARQ_TB01 de qualquer jeito) — roteá-los aqui duplicaria o dado.
_GRAVAR_POR_TABELA = {
    "autofill": (_gravar_autofill, "autofill"),
    "tb04": (_gravar_pagina_visitada, "navegacao"),
    "tb06": (_gravar_acesso_conta, "acessos"),
    "tb07": (_gravar_agregado_conta, "acessos_agregados"),
    "tb08": (_gravar_conexao_conta, "conexoes"),
    "tb09": (_gravar_atividade_conta, "atividades"),
    "tb11": (_gravar_aplicativo, "apps"),
    "tb12": (_gravar_dispositivo_conta, "dispositivos"),
    "tb13": (_gravar_perfil_conta, "perfil"),
    "tb14": (_gravar_evento_conta, "eventos"),
    "tb16": (_gravar_ligacao, "ligacoes"),
    "tb17": (_gravar_nota, "notas"),
    "tb18": (_gravar_localizacao, "localizacoes"),
}

# Destinos do takeout_parsers que NÃO viram linha de tabela de domínio aqui: "tb02"/"tb03"
# (e-mail) e "tb05" (contato) porque o IPED já quebra o mbox e o vCard em itens próprios, e
# roteá-los de novo duplicaria.
#
# "tb10"/"tb10_info" (Drive, Google Photos, Drive Mobile Backups) saíram desta lista: o arquivo
# em si continua virando linha em ARQ_TB01 como sempre, mas os JSON que o Takeout entrega ao
# LADO dele ("<nome>-info.json" do Drive, "<foto>.jpg.json" do Photos) não são arquivos do
# usuário — descrevem outro arquivo e trazem data de servidor, lixeira, compartilhamento e de
# qual aparelho a foto subiu. Agora são desviados para NUV_TB01_METADADOS_ARQUIVO
# (funcoes/nuvem_metadados.py), que é o que sustenta a tela "Nuvem × aparelho".
_TABELAS_IGNORADAS = {"tb02", "tb03", "tb05"}

# Destinos cujo conteúdo pode ser um sidecar de nuvem. O parser confere o formato: JSON que não
# for sidecar (Photo Features.json, Deletion Markers.json) devolve None e segue só como arquivo.
_TABELAS_SIDECAR = {"tb10", "tb10_info"}


def _linhas_de_produto(tabela: str, interno: str, blob: bytes) -> list[tuple[str, dict]]:
    """Roda o parser do produto e devolve (tabela_destino, dict) por linha. A tabela sai do
    parser e não do mapa porque dois destinos se desdobram: tb07_08 (um CSV que traz serviços
    agregados E conexões entre identificadores) e tb04/tb17/tb18 (o mesmo destino atende
    formatos diferentes, decididos pelo caminho)."""
    if tabela == "tb04":
        texto = blob.decode("utf-8", "replace")
        linhas = (parse_chrome_history(texto) if interno.lower().endswith(".json")
                  else parse_bookmarks_html(texto))
        return [("tb04", l) for l in linhas]
    if tabela == "tb06":
        return [("tb06", l) for bloco in parse_activity_csv(blob) for l in bloco]
    if tabela == "tb07_08":
        servicos, conexoes = parse_agregado_csv(blob)
        return [("tb07", l) for l in servicos] + [("tb08", l) for l in conexoes]
    if tabela == "tb09":
        return [("tb09", l) for l in parse_my_activity(blob.decode("utf-8", "replace"))]
    if tabela == "tb11":
        return [("tb11", l) for bloco in parse_playstore_csv(blob) for l in bloco]
    if tabela == "tb12":
        return [("tb12", l) for l in parse_adcs(blob.decode("utf-8", "replace"), "adcs")]
    if tabela == "tb13":
        linha = parse_subscriber_info(blob.decode("utf-8", "replace"))
        return [("tb13", linha)] if linha else []
    if tabela == "tb14":
        return [("tb14", l) for l in parse_ics(blob.decode("utf-8", "replace"))]
    if tabela == "tb16":
        return [("tb16", l) for bloco in parse_duo_call_history(blob) for l in bloco]
    if tabela == "autofill":
        return [("autofill", l)
                for l in parse_chrome_autofill(blob.decode("utf-8", "replace"))]
    if tabela == "tb17":
        texto = blob.decode("utf-8", "replace")
        linha = parse_keep_note(texto) if interno.startswith("Keep/") else None
        return [("tb17", linha)] if linha else []
    if tabela == "tb18":
        texto = blob.decode("utf-8", "replace")
        if interno.startswith("Maps/"):
            return [("tb18", l) for l in parse_geojson_places(texto, "Google Maps (locais rotulados)")]
        if interno.startswith("Saved/"):
            return [("tb18", l) for l in parse_saved_places(texto, "Google (locais salvos)")]
        if interno.startswith("Timeline/"):
            return [("tb18", l) for l in parse_timeline_json(texto)]
    return []


def _rotear_produto_google(cur, id_extracao: int, pasta_modulo: Path, campos: dict,
                           contagens: dict) -> None:
    """Se o item veio de um produto do Google que temos parser, decompõe o conteúdo nas
    tabelas de domínio. O item continua virando linha em ARQ_TB01 de qualquer jeito (quem
    chama já cuida disso) — aqui é só o enriquecimento. Falha de parser é engolida: um
    produto com formato inesperado não pode derrubar a importação inteira."""
    interno = _caminho_interno(campos)
    if not interno:
        return
    alvo = destino(interno)
    if alvo is None or alvo[0] in _TABELAS_IGNORADAS:
        return
    blob = _blob_do_item(pasta_modulo, campos)
    if not blob:
        return

    if alvo[0] in _TABELAS_SIDECAR:
        try:
            sidecar = _parse_sidecar_nuvem(_valor(campos, "name") or "", blob)
        except Exception:
            logger.warning("Nuvem IPED: falha ao interpretar o sidecar %s", interno, exc_info=True)
            return
        if sidecar is not None:
            _gravar_metadado_nuvem(cur, id_extracao, sidecar)
            contagens["metadados_nuvem"] = contagens.get("metadados_nuvem", 0) + 1
        return

    try:
        linhas = _linhas_de_produto(alvo[0], interno, blob)
    except Exception:
        logger.warning("Nuvem IPED: falha ao interpretar %s", interno, exc_info=True)
        return
    for tabela, linha in linhas:
        fn, chave = _GRAVAR_POR_TABELA[tabela]
        fn(cur, id_extracao, _LinhaParcial(linha))
        contagens[chave] = contagens.get(chave, 0) + 1


def _contatos_de_item(pasta_modulo: Path, campos: dict) -> list[dict]:
    """Busca o blob de um item category=='Contacts' (.vcf — o IPED não quebra em contatos
    individuais, ao contrário dos e-mails) e reaproveita o parser de vCard do Google Takeout.
    Lista vazia se o blob não puder ser lido (item ausente/erro) — best-effort, não derruba
    a importação.

    ESTE É O CAMINHO DE ÚLTIMO RECURSO. O primeiro é `iped_dominios.contato_de_item`, que lê
    os campos que o próprio IPED já decodificou. A premissa original ("o IPED não quebra o
    vCard") é falsa em parte dos casos: no caso iCloud do repo o VCardParser quebrou a agenda
    em 554 itens, um contato cada, com `userName`/`phoneNumber`/`userOrganization` prontos no
    índice — e a importação baixava 554 blobs do storage para reparsear o que já estava lá.
    Aqui se cai só quando o item de contato não tem nenhum campo semântico, que é o caso do
    .vcf inteiro entrando como item único (agenda exportada de Takeout, por exemplo)."""
    origem = _valor(campos, "dataSourcePath") or ""
    hash_md5 = _valor(campos, "md5") or _valor(campos, "hash")
    if not (origem and hash_md5):
        return []
    try:
        dados = _ler_blob_iped(pasta_modulo, origem, hash_md5,
                               _valor(campos, "idInDataSource") or "")
    except FileNotFoundError:
        return []
    texto = dados.decode("utf-8", "replace")
    # o vCard do Google não traz data de criação, organização, foto nem estado do registro,
    # e a entrada de telefone/e-mail não traz domínio — todos lidos por chave obrigatória em
    # _gravar_contato_agenda, que também percorre "entradas" (ver _LinhaParcial)
    return [_LinhaParcial({**c, "entradas": [_LinhaParcial(e) for e in c["entradas"]]})
            for c in parse_vcf(texto, _valor(campos, "name") or "IPED")]


# Domínios cujo item NÃO deve virar também linha de ARQ_TB01_ARQUIVOS.
#
# Chat e mensagem são registros decodificados de dentro do banco de um aplicativo: não existem
# como arquivo no aparelho, não têm binário próprio (o WhatsAppParser cria o subitem com
# `EmptyInputStream` e `BasicProps.LENGTH` vazio) e viravam linha de arquivo chamada
# "chat_message_37", com tamanho nulo e nada mais — ruído puro na tela Arquivos e no
# cruzamento entre aparelhos.
#
# Contato fica de fora pelo motivo oposto: manter o comportamento que já existia (o .vcf
# alimenta a agenda e não duplica na lista de arquivos).
#
# "ignorado" é o mesmo recorte de escopo do UFDR (_TIPOS_IGNORADOS_DE_PROPOSITO): tipo
# decodificado com muito volume para o valor probatório (Device Events) — nem arquivo, nem
# linha de domínio.
_DOMINIOS_SEM_ARQUIVO = {"chat", "mensagem", "contato", "ignorado"}


def _tem_binario(campos: dict) -> bool:
    """True quando o item aponta para um binário próprio (storage do caso ou arquivo
    original). É o que separa "arquivo de verdade que também alimenta um domínio" — um
    Bookmarks.html de um HD, que vai para NAV_TB01 e continua na tela Arquivos — de "registro
    decodificado", que só existe como linha de domínio."""
    return bool((_valor(campos, "dataSourcePath") or "")
                and (_valor(campos, "md5") or _valor(campos, "hash")))


# ---------------------------------------------------------------------------
# Processamento em background
# ---------------------------------------------------------------------------

def _finalizar(id_extracao_iped: int, id_extracao_ponte: int, *, status: str,
              contagens: dict | None = None, total_itens: int | None = None,
              erro: str | None = None, no_pasta: str | None = None,
              no_conta: str | None = None, versao: str | None = None) -> None:
    # funil único de todos os desfechos: é aqui que a thread de batimento da ponte para
    _parar_batimento_ponte(id_extracao_iped)
    contagens = contagens or {}
    with get_connection() as conn:
        with conn.cursor() as cur:
            if no_pasta is not None:
                cur.execute(
                    "UPDATE investigacao.ipd_tb01_extracoes SET no_pasta = %s, no_conta = %s "
                    "WHERE id_extracao_iped = %s",
                    (no_pasta, no_conta, id_extracao_iped),
                )
                cur.execute(
                    "UPDATE investigacao.inq_tb02_extracoes SET no_arquivo = %s, no_dispositivo = %s "
                    "WHERE id_extracao = %s",
                    (no_pasta, no_conta, id_extracao_ponte),
                )
            cur.execute(
                """
                UPDATE investigacao.ipd_tb01_extracoes
                SET cd_status = %s, cd_fase = NULL, qt_progresso_fase = NULL,
                    tx_erro = %s, qt_arquivos = %s, qt_emails = %s, qt_contatos = %s,
                    qt_navegacao = %s, qt_acessos = %s, qt_acessos_agregados = %s,
                    qt_conexoes = %s, qt_atividades = %s, qt_apps = %s,
                    qt_dispositivos = %s, qt_perfil = %s, qt_eventos = %s,
                    qt_ligacoes = %s, qt_notas = %s, qt_localizacoes = %s,
                    qt_metadados_nuvem = %s,
                    qt_conversas = %s, qt_mensagens = %s, qt_contas_usuario = %s,
                    qt_contas_financeiras = %s, qt_senhas = %s, qt_autofill = %s,
                    qt_redes = %s, qt_marcadores = %s,
                    qt_itens_total = %s, ds_versao_iped = coalesce(%s, ds_versao_iped),
                    dt_fim_processamento = now()
                WHERE id_extracao_iped = %s
                """,
                (status, (erro or None) and erro[:2000], contagens.get("arquivos"),
                 contagens.get("emails"), contagens.get("contatos"),
                 contagens.get("navegacao"), contagens.get("acessos"),
                 contagens.get("acessos_agregados"), contagens.get("conexoes"),
                 contagens.get("atividades"), contagens.get("apps"),
                 contagens.get("dispositivos"), contagens.get("perfil"),
                 contagens.get("eventos"), contagens.get("ligacoes"),
                 contagens.get("notas"), contagens.get("localizacoes"),
                 contagens.get("metadados_nuvem"),
                 # domínios decodificados (só um caso de celular preenche; nuvem e HD ficam
                 # em NULL, que é como se lê "este caso não tinha conversa nenhuma")
                 contagens.get("conversas"), contagens.get("mensagens"),
                 contagens.get("contas_usuario"), contagens.get("contas_financeiras"),
                 contagens.get("senhas"), contagens.get("autofill"),
                 contagens.get("redes"), contagens.get("marcadores"),
                 total_itens, versao, id_extracao_iped),
            )
            cur.execute(
                """
                UPDATE investigacao.inq_tb02_extracoes
                SET cd_status = %s, tx_erro = %s,
                    qt_conversas = %s, qt_mensagens = %s, qt_ligacoes = %s,
                    qt_localizacoes = %s, qt_notas = %s, qt_senhas = %s,
                    qt_autofill = %s, qt_redes_wifi = %s
                WHERE id_extracao = %s
                """,
                (status, (erro or None) and erro[:2000],
                 contagens.get("conversas"), contagens.get("mensagens"),
                 contagens.get("ligacoes"), contagens.get("localizacoes"),
                 contagens.get("notas"), contagens.get("senhas"),
                 contagens.get("autofill"), contagens.get("redes"),
                 id_extracao_ponte),
            )
        conn.commit()


def _identificar_pessoas_do_caso(id_extracao_ponte: int) -> None:
    """Dispara a identificação de pessoas do inquérito depois que a importação do IPED
    termina (best-effort, no mesmo espírito do UFDR — ver investigacao_utils). A
    identificação é por INQUÉRITO, então cruza este caso com os demais aparelhos e com os
    documentos financeiros anexados: os contatos, ligações, contas, mensagens e o dono do
    aparelho lidos do IPED viram pessoas e relações na Mesa de Inquérito, exatamente como
    já acontece com uma extração UFDR — os coletores são table-driven, não fonte-driven."""
    try:
        from funcoes.identificacao_pessoas import (
            IdentificacaoEmAndamento,
            identificar_pessoas_inquerito,
        )
        from funcoes.investigacao_utils import obter_id_inquerito_de_extracao
        id_inq = obter_id_inquerito_de_extracao(id_extracao_ponte)
        if id_inq is not None:
            identificar_pessoas_inquerito(id_inq, incluir_extracao=id_extracao_ponte)
    except IdentificacaoEmAndamento:
        logger.info("Identificação de pessoas do caso IPED %s pulada: já havia uma em "
                    "andamento no inquérito", id_extracao_ponte)
    except Exception:
        logger.exception("Falha na identificação de pessoas do inquérito da extração IPED "
                         "%s (best-effort)", id_extracao_ponte)


def _classpath_dumper(pasta_modulo: Path) -> str:
    """Monta o classpath do subprocesso: os jars do PRÓPRIO caso (lib/*, tem o Lucene na
    versão certa para aquele índice — ver docs/extracao_java_iped.md sobre por que isso
    importa para compatibilidade entre versões do IPED) + a pasta do dumper compilado."""
    return f"{pasta_modulo / 'lib'}{os.sep}*{os.pathsep}{_DUMPER_DIR}"


# "iped-engine-4.2.2.jar" -> "4.2.2". Os jars sem versão no nome (iped-hashdb.jar,
# iped-search-app.jar) não casam, e é justamente por isso que a regex exige o número.
_RE_VERSAO_JAR = re.compile(r"^iped-(?:engine|api|utils|parsers-impl)-(\d+(?:\.\d+)*)\.jar$", re.I)


def versao_iped(pasta_modulo: Path) -> str | None:
    """Versão do IPED que processou o caso, lida do nome dos jars em lib/ — a coluna
    DS_VERSAO_IPED existia desde o início e nunca era preenchida.

    Serve a diagnóstico: o dumper depende de nomes de campo do índice serem estáveis entre
    versões (ver "Não-objetivos" em docs/extracao_iped.md), e quando um caso importa torto a
    primeira pergunta é de que versão ele veio. Best-effort: caso sem os jars canônicos
    devolve None e a importação segue igual."""
    lib = pasta_modulo / "lib"
    if not lib.is_dir():
        return None
    try:
        for jar in sorted(lib.iterdir()):
            m = _RE_VERSAO_JAR.match(jar.name)
            if m:
                return m.group(1)[:200]
    except OSError:
        return None
    return None


def _importar_em_background(id_extracao_iped: int) -> None:
    """Alvo da thread. Existe só pelo finally: _finalizar já encerra o batimento da ponte em todos
    os desfechos previstos, mas uma exceção que escape de _processar_importacao_iped deixaria a
    thread de batimento viva para sempre, marcando como ativa uma importação que morreu — o
    contrário exato do que o watchdog precisa enxergar."""
    try:
        _processar_importacao_iped(id_extracao_iped)
    finally:
        _parar_batimento_ponte(id_extracao_iped)


def _processar_importacao_iped(id_extracao_iped: int) -> None:
    """Roda em background: dispara o IpedDumper como subprocesso, lê o JSONL do stdout item a
    item, grava lote em ARQ_TB01_ARQUIVOS (Fase 1) e atualiza status/fase/progresso/contagens."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT no_pasta, no_conta, id_extracao FROM investigacao.ipd_tb01_extracoes "
                "WHERE id_extracao_iped = %s",
                (id_extracao_iped,))
            row = cur.fetchone()
    if row is None:
        return
    tx_pasta, conta, id_extracao_ponte = row
    # NO_PASTA vem do banco e é lido aqui, horas depois da criação: revalida e confina a
    # data/ como na criação (ver F7) — um valor envenenado montaria classpath com lib/*.jar
    # de fora do caso antes do Popen (RCE como appuser).
    try:
        caminho = _resolver_pasta_caso(tx_pasta)
    except (ValueError, FileNotFoundError) as exc:
        _finalizar(id_extracao_iped, id_extracao_ponte, status="erro",
                   erro=f"Pasta do caso inválida: {exc}")
        return
    pasta_modulo = _pasta_modulo_iped(caminho)
    # None quando a conta já veio confirmada pelo investigador (payload.conta) — nesse caso
    # não há nada a detectar, só renomear ao final (ver bloco após o laço principal).
    detector = _DetectorConta(pasta_modulo) if (pasta_modulo is not None and not conta) else None

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE investigacao.ipd_tb01_extracoes SET cd_status = 'processando', "
                "dt_inicio_processamento = now() WHERE id_extracao_iped = %s",
                (id_extracao_iped,))
            # a ponte também: o watchdog lê DT_INICIO_PROCESSAMENTO dela, não a de IPD_TB01
            cur.execute(
                "UPDATE investigacao.inq_tb02_extracoes SET dt_inicio_processamento = now(), "
                "dt_ultima_atividade = now() WHERE id_extracao = %s",
                (id_extracao_ponte,))
        conn.commit()
        _marcar_fase_iped(conn, id_extracao_iped, "extraindo")

    # a partir daqui a importação pode levar horas — ver _BATIMENTOS_PONTE
    _iniciar_batimento_ponte(id_extracao_iped, id_extracao_ponte)

    if pasta_modulo is None:
        _finalizar(id_extracao_iped, id_extracao_ponte, status="erro",
                  erro="A pasta não é mais um caso válido do IPED (IPEDConfig.txt/index ausente).")
        return

    caso_raiz = pasta_modulo.parent if pasta_modulo.name == "iped" else pasta_modulo
    classpath = _classpath_dumper(pasta_modulo)

    try:
        proc = subprocess.Popen(
            [_JAVA_BIN, "-cp", classpath, "IpedDumper", str(caso_raiz)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
        )
    except FileNotFoundError:
        _finalizar(id_extracao_iped, id_extracao_ponte, status="erro",
                  erro="Java não está disponível nesta instalação — não é possível ler casos do IPED.")
        return

    # "lidos"/"docs" são documentos do índice Lucene percorridos (o denominador do progresso);
    # "itens" é quantos deles descreviam mesmo um item e viraram linha de JSON. Os dois diferem
    # por um fator de ~2: metade dos documentos do índice não tem campo armazenado nenhum e o
    # dumper não os emite mais (ver o cabeçalho do IpedDumper.java). Enquanto os dois eram a
    # mesma variável, QT_ITENS_TOTAL gravava maxDoc — o dobro do número real de itens do caso.
    progresso = {"lidos": 0, "docs": 0, "itens": 0}
    log_stderr: list[str] = []

    def _ler_stderr():
        for linha in proc.stderr:
            linha = linha.rstrip("\n")
            if linha.startswith("PROGRESS "):
                partes = linha[len("PROGRESS "):].split("/")
                if len(partes) == 2 and partes[0].isdigit() and partes[1].isdigit():
                    progresso["lidos"] = int(partes[0])
                    progresso["docs"] = int(partes[1])
            elif linha.startswith("DONE "):
                bruto = linha[len("DONE "):].strip()
                if bruto.isdigit():
                    progresso["itens"] = int(bruto)
            elif linha:
                log_stderr.append(linha)

    th_stderr = threading.Thread(target=_ler_stderr, daemon=True)
    th_stderr.start()

    # OCR e transcrição que o IPED já calculou, lidos de <caso>/text/*.db. As conexões SQLite
    # seguram a pasta e precisam ser fechadas antes do rename/move do fim (mesmo cuidado que
    # _fechar_conexoes_iped resolve para o storage) — daí o try/finally lá embaixo.
    textos = TextosDoCaso(pasta_modulo)

    # Marcadores do perito, lidos ANTES do laço: é um subprocesso curto sobre um arquivo
    # (bookmarks.iped), não toca o índice Lucene, e ter o mapa em mãos deixa TX_MARCADORES
    # entrar já no COPY em vez de exigir um passe de UPDATE depois. Caso nunca marcado devolve
    # vazio e nada muda — ver funcoes/iped_marcadores.py.
    marcadores = iped_marcadores.ler(pasta_modulo, classpath)

    # Roteamento por domínio: chat, mensagem, ligação, localização, senha, rede… Um caso de HD
    # ou de nuvem pura não casa com nenhum domínio e o roteador não gasta nada — nem cria o
    # arquivo temporário das mensagens. Ver funcoes/iped_dominios.py.
    roteador = Roteador(id_extracao_ponte)

    # Mídia do WhatsApp no backup do Google Drive: o caso vira também uma "conversa-mídia"
    # por conta (linha do tempo com os binários) — o Drive não preserva o contato de cada
    # arquivo, então não há como reconstituir por conversa; ver funcoes/iped_wa_drive.py.
    midia_drive = _ColecaoMidiaDrive(id_extracao_ponte)

    contagens = {"arquivos": 0, "emails": 0, "contatos": 0}
    if marcadores.total:
        contagens["marcadores"] = marcadores.total
    buffer: list[tuple] = []
    processados = 0
    pct = -1
    cancelado = False

    # O laço inteiro sob try/except: antes, qualquer erro inesperado aqui (JSON estranho, blob
    # corrompido, queda do banco) subia pela thread de background e a extração ficava presa em
    # "processando" para sempre, sem mensagem — o investigador via um card girando e não tinha
    # o que fazer além de apagar a importação. Agora vira status 'erro' com o texto no card.
    try:
        with get_connection(longa=True) as conn_status:
            for linha in proc.stdout:
                linha = linha.strip()
                if not linha:
                    continue
                try:
                    item = json.loads(linha)
                except ValueError:
                    continue
                campos = item.get("fields") or {}
                if not campos:
                    continue

                if detector is not None:
                    detector.avaliar(campos)

                # transcrição de áudio que o IPED já produziu, ligada ao binário por sha-256 —
                # registrada para TODO item com sha-256 (não só o que vira arquivo): a chave
                # vale para quem a mensagem apontar via linkedItems
                _registrar_transcricao_audio(roteador, campos, textos)

                # membership e não igualdade: 60 itens do caso de exemplo têm 2 category
                categorias_iped = _valores(campos, "category")
                if "Emails" in categorias_iped:
                    email_lido = _email_de_item(campos, pasta_modulo)
                    if email_lido is not None:
                        with conn_status.cursor() as cur:
                            _gravar_email(cur, id_extracao_ponte, email_lido)
                        contagens["emails"] += 1
                    processados += 1
                    continue

                # Domínio decodificado pelo IPED (conversa, ligação, senha, rede, contato…).
                # Devolve None quando o item não é de domínio nenhum — o caso da esmagadora
                # maioria dos itens de um HD ou de uma nuvem, que são arquivos mesmo.
                with conn_status.cursor() as cur:
                    dominio = roteador.consumir(cur, campos)

                # Item de localização que o IPED decodificou mas sem coordenada nem endereço no
                # índice (comum no NSKeyArchiver do Instagram): como registro decodificado que
                # é, virar "arquivo" só criaria uma linha sem binário e sem nada útil na tela
                # Arquivos — é descartado como os demais registros de domínio.
                if dominio is None and ({"Locations", "Journeys"} & set(categorias_iped)):
                    processados += 1
                    continue

                if dominio in _DOMINIOS_SEM_ARQUIVO:
                    processados += 1
                    continue

                if dominio == "contato_sem_campos":
                    # item de contato que o IPED não decodificou em campos (o .vcf inteiro
                    # como item único): cai no parser de vCard sobre o blob
                    for contato in _contatos_de_item(pasta_modulo, campos):
                        with conn_status.cursor() as cur:
                            _gravar_contato_agenda(cur, id_extracao_ponte, contato)
                        contagens["contatos"] += 1
                    processados += 1
                    continue

                # Item de domínio que TAMBÉM é um arquivo de verdade (tem binário próprio):
                # um Bookmarks.html de um HD alimenta NAV_TB01 e continua aparecendo na tela
                # Arquivos. Já o registro decodificado de dentro de um banco de aplicativo não
                # tem binário e não vira arquivo — antes virava, com nome "chat_message_37",
                # tamanho vazio e nada mais.
                if dominio is None or _tem_binario(campos):
                    tupla = _item_para_arquivo(id_extracao_ponte, campos, textos, marcadores)
                    if tupla is not None:
                        buffer.append(tupla)
                        contagens["arquivos"] += 1
                        # binário do caso por sha-256: o roteador usa para dar ponteiro aos
                        # anexos de conversa (mídia do WhatsApp que abre na tela) — ver
                        # iped_dominios.registrar_binario
                        roteador.registrar_binario(tupla[_IDX_SHA256], tupla[_IDX_CAMINHO])
                    # Roteamento por produto do Google no mesmo critério do arquivo, e não
                    # só quando o item não é de domínio nenhum: um export do Takeout dentro
                    # de um caso misto pode cair numa categoria de domínio (um .vcf da agenda
                    # em "Contacts", por exemplo) e continuar sendo um arquivo de produto que
                    # os parsers sabem decompor. A função sai na primeira linha quando o
                    # caminho não tem ">>" ou o produto não é conhecido, então rodá-la mais
                    # vezes não custa nada.
                    with conn_status.cursor() as cur:
                        _rotear_produto_google(cur, id_extracao_ponte, pasta_modulo, campos,
                                               contagens)
                    # mídia do WhatsApp no backup do Drive → linha do tempo (ver iped_wa_drive)
                    midia_drive.coletar(campos)
                processados += 1

                if len(buffer) >= _LOTE_ARQUIVOS:
                    with conn_status.cursor() as cur:
                        _drenar_arquivos(cur, buffer)
                    conn_status.commit()
                    buffer.clear()

                if processados % _CHECKPOINT == 0:
                    conn_status.commit()  # confirma e-mails/contatos fora do buffer de arquivos
                    # o progresso é medido em DOCUMENTOS percorridos, não em itens gravados: o
                    # denominador que o dumper conhece de antemão é o maxDoc do índice
                    total = progresso["docs"] or processados
                    pct = _marcar_progresso_iped(conn_status, id_extracao_iped,
                                                 progresso["lidos"] or processados, total, pct)
                    if _cancelamento_solicitado_iped(conn_status, id_extracao_iped):
                        cancelado = True
                        proc.terminate()
                        break

            if buffer:
                with conn_status.cursor() as cur:
                    _drenar_arquivos(cur, buffer)
                buffer.clear()

            # As conversas só podem ser gravadas agora: a mensagem é item FILHO do chat e o
            # dumper entrega os documentos na ordem do índice, que não garante o chat antes
            # das suas mensagens (ver funcoes/iped_dominios.py). Até aqui elas ficaram num
            # SQLite temporário indexado por parentId. Vale também para um cancelamento: o
            # que já foi lido é gravado, em vez de descartado.
            if roteador.tem_conversas:
                _marcar_fase_iped(conn_status, id_extracao_iped, "gravando_conversas")

            # o pct fica num mutável porque _marcar_progresso_iped só escreve no banco quando
            # a porcentagem muda, e é essa comparação que evita um UPDATE por conversa
            pct_conversas = [-1]

            def _progresso_conversas(feitas, total):
                pct_conversas[0] = _marcar_progresso_iped(
                    conn_status, id_extracao_iped, feitas, total, pct_conversas[0])

            with conn_status.cursor() as cur:
                contagens.update(roteador.finalizar(
                    cur, commit=conn_status.commit, progresso=_progresso_conversas))
            conn_status.commit()  # último lote de arquivos, conversas e e-mails pendentes

            # linha do tempo da mídia do WhatsApp no backup do Drive (ver iped_wa_drive) —
            # contagens próprias, NÃO "conversas"/"mensagens": esses chaves também definem
            # tp_fonte='celular' abaixo, e um caso de nuvem pura deve continuar 'nuvem'
            if not midia_drive.vazia:
                with conn_status.cursor() as cur:
                    contagens.update(midia_drive.gravar(cur))
                conn_status.commit()
    except Exception as exc:
        logger.exception("Nuvem IPED: falha ao processar a extração %s", id_extracao_iped)
        textos.fechar()
        proc.kill()
        th_stderr.join(timeout=5)
        _finalizar(id_extracao_iped, id_extracao_ponte, status="erro", contagens=contagens,
                   total_itens=processados,
                   erro=f"Falha ao processar o caso do IPED: {exc}")
        return
    finally:
        # o SQLite temporário das mensagens não pode sobreviver à importação por nenhum
        # caminho de saída: nem o normal, nem o erro acima (que sai por `return` e passa por
        # aqui antes), nem uma exceção que escape
        roteador.fechar()

    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
    th_stderr.join(timeout=5)

    # a versão só pode ser lida enquanto a pasta ainda está no lugar de origem
    versao = versao_iped(pasta_modulo)
    # solta os SQLite de OCR/transcrição antes do rename/move, pelo mesmo motivo que
    # _fechar_conexoes_iped existe: conexão aberta segura a pasta no Windows
    textos.fechar()

    # Identificação da conta + renomeação da pasta acontecem só agora — de propósito, ver o
    # docstring da função: o subprocesso Java já terminou há tempo suficiente para o SO soltar
    # os mapeamentos de memória do índice (achado real neste ambiente de desenvolvimento).
    conta_final = conta or (detector.melhor_palpite() if detector is not None else None)
    tx_pasta_final = tx_pasta
    if conta_final and pasta_modulo is not None:
        # fecha conexões sqlite cacheadas pro storage deste caso ANTES de renomear — achado
        # real: uma conexão aberta (iped_cache, TTL de até 1h) bloqueava o rename da pasta
        # pai com PermissionError neste ambiente (Docker Desktop no Windows). Ver
        # iped_cache.fechar_conexoes.
        _fechar_conexoes_iped(pasta_modulo)
        # renomeia a pasta que o investigador escolheu (caminho == PASTA_DADOS/tx_pasta), não
        # necessariamente pasta_modulo — as duas só coincidem quando o caso não tem o wrapper
        # "iped/" (ver _pasta_modulo_iped); num caso "case_root/iped/" de verdade, renomear a
        # subpasta "iped" quebraria a própria detecção de estrutura numa próxima leitura.
        tx_pasta_final = _renomear_pasta_para_conta(caminho, conta_final, tx_pasta)

    # itens de verdade (linhas emitidas pelo dumper), não documentos do índice. Numa parada por
    # cancelamento o DONE nunca chega, e aí vale o que foi efetivamente processado.
    total_itens = progresso["itens"] or processados

    if cancelado:
        _finalizar(id_extracao_iped, id_extracao_ponte, status="cancelado",
                  contagens=contagens, total_itens=processados,
                  no_pasta=tx_pasta_final, no_conta=conta_final, versao=versao)
        # o cancelamento preserva o que já foi lido: os dados parciais também valem na mesa
        _identificar_pessoas_do_caso(id_extracao_ponte)
        return

    if proc.returncode not in (0, None):
        erro = "Falha ao ler o caso do IPED."
        if log_stderr:
            erro += " Detalhe: " + " | ".join(log_stderr[-3:])
        logger.error("IpedDumper falhou (extração %s): %s", id_extracao_iped, "\n".join(log_stderr[-20:]))
        _finalizar(id_extracao_iped, id_extracao_ponte, status="erro",
                  contagens=contagens, total_itens=total_itens, erro=erro,
                  no_pasta=tx_pasta_final, no_conta=conta_final, versao=versao)
        return

    # importação concluída: a pasta-caso sai da pasta de entrega da delegacia e vai para
    # data/<delegacia>/<inquérito>/NUVEM/ (ou CELULAR/, quando a fonte é um celular — depois
    # do rename para a conta, e só agora porque o índice Lucene é lido do começo ao fim
    # acima). _finalizar já grava o caminho novo nas duas pontas (IPD_TB01_EXTRACOES.NO_PASTA
    # e a extração-ponte em INQ_TB02_EXTRACOES.NO_ARQUIVO).
    tp_fonte = "celular" if (contagens.get("conversas") or contagens.get("senhas")
                             or contagens.get("redes") or contagens.get("autofill")) else "nuvem"
    tx_pasta_final = _organizar_pasta_iped(id_extracao_iped, tx_pasta_final, tp_fonte)

    _finalizar(id_extracao_iped, id_extracao_ponte, status="concluido",
              contagens=contagens, total_itens=total_itens,
              no_pasta=tx_pasta_final, no_conta=conta_final, versao=versao)

    # identifica as pessoas do inquérito (contatos, ligações, contas, conversas e o dono do
    # aparelho lidos deste caso) e materializa na Mesa de Inquérito — como o UFDR já faz.
    _identificar_pessoas_do_caso(id_extracao_ponte)


# ---------------------------------------------------------------------------
# Importação IPED NO NÓ (no_importar.py): dumper na CPU da estação + registros pelo canal
#
# Espelho do laço acima em MODO ENTREGA (o mesmo desenho da extração .ufdr do nó,
# funcoes/no_extracao.py): com o canal ativo (canal_extracao.ativar) cada writer de
# investigacao_utils/contas_nuvem_utils entrega o registro ao central em vez de gravar — os
# guards estão nos próprios _gravar_* e o ConnFalsa/_CursorFalso garantem que qualquer
# consulta sem guard FALHE ALTO aqui, em vez de sumir dados em silêncio. O que o laço local
# faz direto no banco (fase/progresso/cancelamento/status/_finalizar) vira callback do canal
# (BatimentoPonte) e o relatório do fim volta na chave "ipd" do 'finalizar' — o central
# aplica nas duas pontas com o MESMO _finalizar (aplicar_relatorio_importacao_no) e roda a
# identificação de pessoas lá (varre o banco inteiro). Rodam aqui, na estação: o subprocesso
# IpedDumper, os .db de OCR/transcrição do caso, os marcadores do perito, a detecção/
# renomeação da conta e o organize da pasta.
# ---------------------------------------------------------------------------


def importar_iped_no(canal, tx_pasta: str, opcoes: dict) -> dict:
    """Caso IPED processado NA ESTAÇÃO (funcoes/no_importar.py). `tx_pasta` é o caminho da
    pasta em data/ DO NÓ (o mesmo relativo que o central guarda em NO_PASTA); `opcoes` traz
    conta (confirmada na tela — pula a autodetecção) e numero_inquerito (destino do
    organize). Devolve o relatório — que também vai no 'finalizar' do canal."""
    from funcoes.canal_extracao import BatimentoPonte, ConnFalsa

    id_extracao_ponte = canal.id_extracao  # a ponte é a chave de TODOS os registros no central
    conta = (opcoes.get("conta") or "").strip() or None

    caminho = _resolver_pasta_caso(tx_pasta)
    pasta_modulo = _pasta_modulo_iped(caminho)
    if pasta_modulo is None:
        raise ValueError("A pasta não é mais um caso válido do IPED (IPEDConfig.txt/index ausente).")
    detector = _DetectorConta(pasta_modulo) if not conta else None

    caso_raiz = pasta_modulo.parent if pasta_modulo.name == "iped" else pasta_modulo
    classpath = _classpath_dumper(pasta_modulo)

    bat = BatimentoPonte(canal)
    conn = ConnFalsa(canal)
    cur = conn.cursor()  # guard é o que decide; o cursor falso nunca é consultado

    def _fechar(status: str, *, contagens: dict | None = None, total_itens: int | None = None,
                erro: str | None = None, no_pasta: str | None = None,
                no_conta: str | None = None, versao: str | None = None) -> dict:
        """Equivalente ao _finalizar local: o relatório vai no 'finalizar' do canal e o
        central aplica nas duas pontas (ipd_tb01 + ponte) com o mesmo UPDATE."""
        relatorio = {"audios_transcritos": 0,
                     "ipd": {"status": status, "contagens": contagens or {},
                             "total_itens": total_itens,
                             "erro": (erro or None) and str(erro)[:2000],
                             "no_pasta": no_pasta, "no_conta": no_conta, "versao": versao}}
        canal.finalizar(relatorio)
        return relatorio["ipd"]

    bat.fase("extraindo")
    bat.iniciar_vigia()  # caso grande leva horas na CPU da estação — a ponte não pode parecer parada
    try:
        try:
            proc = subprocess.Popen(
                [_JAVA_BIN, "-cp", classpath, "IpedDumper", str(caso_raiz)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace",
            )
        except FileNotFoundError:
            return _fechar("erro", erro="Java não está disponível nesta estação — não é "
                                        "possível ler casos do IPED.")

        progresso = {"lidos": 0, "docs": 0, "itens": 0}
        log_stderr: list[str] = []

        def _ler_stderr():
            for linha in proc.stderr:
                linha = linha.rstrip("\n")
                if linha.startswith("PROGRESS "):
                    partes = linha[len("PROGRESS "):].split("/")
                    if len(partes) == 2 and partes[0].isdigit() and partes[1].isdigit():
                        progresso["lidos"] = int(partes[0])
                        progresso["docs"] = int(partes[1])
                elif linha.startswith("DONE "):
                    bruto = linha[len("DONE "):].strip()
                    if bruto.isdigit():
                        progresso["itens"] = int(bruto)
                elif linha:
                    log_stderr.append(linha)

        th_stderr = threading.Thread(target=_ler_stderr, daemon=True)
        th_stderr.start()

        # criados FORA do try que os referencia no except (mesma ordem do laço local): se um
        # deles falhar, a exceção sobe limpa para o no_importar, sem NameError por cima
        textos = TextosDoCaso(pasta_modulo)
        marcadores = iped_marcadores.ler(pasta_modulo, classpath)
        roteador = Roteador(id_extracao_ponte)
        midia_drive = _ColecaoMidiaDrive(id_extracao_ponte)

        contagens = {"arquivos": 0, "emails": 0, "contatos": 0}
        if marcadores.total:
            contagens["marcadores"] = marcadores.total
        buffer: list[tuple] = []
        processados = 0
        cancelado = False

        try:
            for linha in proc.stdout:
                linha = linha.strip()
                if not linha:
                    continue
                try:
                    item = json.loads(linha)
                except ValueError:
                    continue
                campos_item = item.get("fields") or {}
                if not campos_item:
                    continue

                if detector is not None:
                    detector.avaliar(campos_item)

                _registrar_transcricao_audio(roteador, campos_item, textos)

                categorias_iped = _valores(campos_item, "category")
                if "Emails" in categorias_iped:
                    email_lido = _email_de_item(campos_item, pasta_modulo)
                    if email_lido is not None:
                        _gravar_email(cur, id_extracao_ponte, email_lido)  # → canal (guard)
                        contagens["emails"] += 1
                    processados += 1
                    continue

                dominio = roteador.consumir(cur, campos_item)  # writers internos → canal

                if dominio is None and ({"Locations", "Journeys"} & set(categorias_iped)):
                    processados += 1
                    continue
                if dominio in _DOMINIOS_SEM_ARQUIVO:
                    processados += 1
                    continue
                if dominio == "contato_sem_campos":
                    for contato in _contatos_de_item(pasta_modulo, campos_item):
                        _gravar_contato_agenda(cur, id_extracao_ponte, contato)
                        contagens["contatos"] += 1
                    processados += 1
                    continue

                if dominio is None or _tem_binario(campos_item):
                    tupla = _item_para_arquivo(id_extracao_ponte, campos_item, textos, marcadores)
                    if tupla is not None:
                        buffer.append(tupla)
                        contagens["arquivos"] += 1
                        roteador.registrar_binario(tupla[_IDX_SHA256], tupla[_IDX_CAMINHO])
                    _rotear_produto_google(cur, id_extracao_ponte, pasta_modulo, campos_item,
                                           contagens)
                    midia_drive.coletar(campos_item)
                processados += 1

                if len(buffer) >= _LOTE_ARQUIVOS:
                    _drenar_arquivos(cur, buffer)  # → lote do canal (guard)
                    conn.commit()
                    buffer.clear()

                if processados % _CHECKPOINT == 0:
                    conn.commit()  # fecha o lote corrente e aciona o envio
                    bat.progresso(progresso["lidos"] or processados,
                                  progresso["docs"] or processados)
                    if bat.cancelar_pedido():  # o "fl_cancelar" chega na resposta do callback
                        cancelado = True
                        proc.terminate()
                        break

            if buffer:
                _drenar_arquivos(cur, buffer)
                buffer.clear()

            if roteador.tem_conversas:
                bat.fase("gravando_conversas")

            def _progresso_conversas(feitas, total):
                bat.progresso(feitas, total)

            contagens.update(roteador.finalizar(cur, commit=conn.commit,
                                                progresso=_progresso_conversas))
            conn.commit()  # último lote (arquivos, conversas e e-mails pendentes)

            if not midia_drive.vazia:
                contagens.update(midia_drive.gravar(cur))
                conn.commit()
        except Exception as exc:
            logger.exception("IPED no nó: falha ao processar o caso %s", tx_pasta)
            textos.fechar()
            proc.kill()
            th_stderr.join(timeout=5)
            return _fechar("erro", contagens=contagens, total_itens=processados,
                           erro=f"Falha ao processar o caso do IPED: {exc}")
        finally:
            roteador.fechar()

        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
        th_stderr.join(timeout=5)

        # a versão só pode ser lida enquanto a pasta ainda está no lugar de origem
        versao = versao_iped(pasta_modulo)
        textos.fechar()  # solta os SQLite de OCR/transcrição antes do rename/move

        conta_final = conta or (detector.melhor_palpite() if detector is not None else None)
        tx_pasta_final = tx_pasta
        if conta_final:
            _fechar_conexoes_iped(pasta_modulo)
            tx_pasta_final = _renomear_pasta_para_conta(caminho, conta_final, tx_pasta)

        total_itens = progresso["itens"] or processados

        if cancelado:
            # o cancelamento preserva o que já foi lido (os lotes entregues ficam no central)
            return _fechar("cancelado", contagens=contagens, total_itens=processados,
                           no_pasta=tx_pasta_final, no_conta=conta_final, versao=versao)

        if proc.returncode not in (0, None):
            erro = "Falha ao ler o caso do IPED."
            if log_stderr:
                erro += " Detalhe: " + " | ".join(log_stderr[-3:])
            logger.error("IpedDumper falhou (%s): %s", tx_pasta, "\n".join(log_stderr[-20:]))
            return _fechar("erro", contagens=contagens, total_itens=total_itens, erro=erro,
                           no_pasta=tx_pasta_final, no_conta=conta_final, versao=versao)

        # organize DENTRO da estação: a pasta sai da pasta de entrega e vai para
        # data/<delegacia>/<inquérito>/NUVEM|CELULAR/ (o central guarda NO_PASTA com o
        # relativo novo, que vem no relatório). Melhor esforço, como no caminho local.
        tp_fonte = "celular" if (contagens.get("conversas") or contagens.get("senhas")
                                 or contagens.get("redes") or contagens.get("autofill")) else "nuvem"
        try:
            numero = opcoes.get("numero_inquerito")
            if numero:
                categoria = CATEGORIA_CELULAR if tp_fonte == "celular" else CATEGORIA_NUVEM
                _fechar_conexoes_iped(PASTA_DADOS / tx_pasta_final)
                tx_pasta_final = (organizar(PASTA_DADOS / tx_pasta_final, numero, categoria)
                                  or tx_pasta_final)
        except Exception:
            logger.exception("organizar pasta do caso IPED no nó (melhor esforço)")

        return _fechar("concluido", contagens=contagens, total_itens=total_itens,
                       no_pasta=tx_pasta_final, no_conta=conta_final, versao=versao)
    finally:
        bat.parar_vigia()


def aplicar_relatorio_importacao_no(id_extracao_iped: int, id_extracao_ponte: int,
                                    payload: dict) -> None:
    """(paridade com o central — o relatório é aplicado no central, nunca aqui) Ver a versão
    do central em api/funcoes/iped_utils.py."""
    raise NotImplementedError("o fecho do IPED remoto roda só no central")

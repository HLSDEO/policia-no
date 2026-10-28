"""Parsers dos produtos do Google (Takeout) usados pela extração "Nuvem IPED".

Um caso do IPED sobre uma pasta de Takeout guarda, dentro do índice, os arquivos que
vieram de dentro dos ZIPs do Google — e o caminho do item preserva o caminho interno
original depois de ">>" (ex.: "…Chrome.History_001.zip>>Chrome/History.json"). É esse
sufixo que `destino()` classifica, com o mesmo mapa que a antiga extração "Nuvem Google
(ZIPs)" usava; os parsers abaixo vieram de lá inteiros (funcoes/google_takeout_utils.py,
removido em 6070ed8) e continuam entregando dicts no formato que
funcoes/contas_nuvem_utils.py e funcoes/investigacao_utils.py já sabem gravar.

Diferença em relação à origem: lá o conteúdo vinha de uma entrada de ZipFile; aqui vem
do storage-N.db do caso (funcoes/iped_cache.ler_blob), então os parsers de CSV recebem
os bytes do item em vez de um par (ZipFile, caminho).
"""

import base64
import csv
import email
import email.utils
import gzip
import html as html_mod
import io
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

_BLOCO_GRAVACAO = 2_000                # linhas por lote dos parsers que rendem em blocos
_LIMITE_CORPO_EMAIL = 500_000          # teto de caracteres do corpo gravado no banco
_LIMITE_TEXTO_LIVRE = 8_000            # teto de campos TEXT de metadados (labels, donos, outros)

# Nome de ZIP do Takeout: "<conta>.<números>.<Produto>.<Assunto>_001.zip" — a mesma regex
# identifica a conta dona do caso a partir do nome dos itens (ver iped_utils._DetectorConta).
_RE_CONTA = re.compile(r"([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+)\.\d+\.")


# ---------------------------------------------------------------------------
# Normalização de campos
# ---------------------------------------------------------------------------

_MESES = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


def _txt(valor) -> str | None:
    if valor is None:
        return None
    # postgres não aceita NUL (0x00) em campo texto — mbox/JSON corrompidos do Takeout
    # já apareceram com isso embutido (ver _decodificar_partes)
    v = str(valor).replace("\x00", "").strip()
    if not v or v in ("-", "0", "0 X 0"):
        return None
    return v[:3000]


def _texto_livre(valor, limite=_LIMITE_TEXTO_LIVRE) -> str | None:
    if valor is None:
        return None
    v = str(valor).replace("\x00", "").strip()
    if not v:
        return None
    if len(v) > limite:
        v = v[:limite]
    return v


def _data_google(valor) -> datetime | None:
    """Converte as datas dos Takeout em datetime UTC (naive — as tabelas são TIMESTAMP).

    Formatos vistos nos arquivos: '2023-04-27 20:30:12 UTC', '2019-05-21 17:41:59 Z',
    'Feb 24, 2025, 2:07:42 PM UTC', '20250829T000000Z' (ICS) e epoch inteiro (CSV antigo)."""
    if valor is None:
        return None
    v = str(valor).strip()
    if not v:
        return None
    try:
        v_sem_z = v.replace(" UTC", "").replace(" Z", "").replace("Z", "").strip()
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f",
                    "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
            try:
                return datetime.strptime(v_sem_z, fmt)
            except ValueError:
                continue
        m = re.match(
            r"([A-Z][a-z]{2}) (\d{1,2}), (\d{4}), (\d{1,2}):(\d{2}):(\d{2}) (AM|PM)", v)
        if m:
            hora = int(m.group(4)) % 12 + (12 if m.group(7) == "PM" else 0)
            return datetime(int(m.group(3)), _MESES[m.group(1)], int(m.group(2)),
                            hora, int(m.group(5)), int(m.group(6)))
        # ICS: '20190828T080000' e '20190828' (a forma com Z já foi normalizada acima)
        for fmt in ("%Y%m%dT%H%M%S", "%Y%m%d"):
            try:
                return datetime.strptime(v_sem_z, fmt)
            except ValueError:
                continue
        # epoch inteiro (10 dígitos) — só depois das formas datadas, para um
        # '20251105171422' do ICS não ser lido como epoch
        if v_sem_z.isdigit() and 9 <= len(v_sem_z) <= 11:
            return datetime.fromtimestamp(int(v_sem_z), tz=timezone.utc).replace(tzinfo=None)
    except (ValueError, OverflowError):
        pass
    return None


def _doc(valor) -> str | None:
    v = _txt(valor)
    return v if v else None



# ---------------------------------------------------------------------------
# Parser de e-mails (mbox)
# ---------------------------------------------------------------------------

def _iterar_linhas(arquivo):
    """Itera as linhas em bytes de um arquivo ZIP sem carregar tudo na memória
    (um mbox pode ter centenas de MB)."""
    for linha in arquivo:
        yield linha


def _decodificar_partes(msg) -> str | None:
    """Texto legível da mensagem: primeira parte text/plain (senão text/html sem
    tags). Anexos binários não entram. Teto de _LIMITE_CORPO_EMAIL caracteres."""
    texto_plano = None
    texto_html = None
    for parte in msg.walk():
        tp = (parte.get_content_type() or "").lower()
        if tp == "text/plain" and texto_plano is None:
            texto_plano = parte
        elif tp == "text/html" and texto_html is None:
            texto_html = parte
    parte = texto_plano or texto_html
    if parte is None:
        return None
    try:
        bruto = parte.get_payload(decode=True)
    except Exception:
        return None
    if bruto is None:
        return None
    charset = parte.get_content_charset() or "utf-8"
    try:
        texto = bruto.decode(charset, "replace")
    except (LookupError, UnicodeDecodeError):
        texto = bruto.decode("utf-8", "replace")
    if tp == "text/html" or (parte.get_content_type() or "").lower() == "text/html":
        texto = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", texto)
        texto = re.sub(r"(?s)<[^>]+>", " ", texto)
        texto = html_mod.unescape(texto)
    texto = re.sub(r"[ \t]+", " ", texto).replace("\x00", "").strip()
    return texto[:_LIMITE_CORPO_EMAIL] or None


def _dt_header(msg) -> datetime | None:
    try:
        d = email.utils.parsedate_to_datetime(msg.get("Date", ""))
    except Exception:
        return None
    if d is None:
        return None
    return d.astimezone(timezone.utc).replace(tzinfo=None)


def parse_mbox(arquivo, ao_progredir=None) -> list[dict]:
    """Lê um mbox linha a linha (de QUALQUER objeto iterável por linha em bytes — membro de
    ZIP aberto ou io.BytesIO em memória, ver funcoes/iped_utils.py) e devolve dicts no
    formato de investigacao_utils._gravar_email (mesma tabela EML_TB01_EMAILS do UFDR).

    O separador é a linha 'From <endereço> <data>' no começo de cada mensagem. A separação é
    manual (não usa mailbox.mbox) porque, vindo de dentro de um ZIP, mailboxes de dezenas de
    MB não devem ser copiadas para disco nem lidas inteiras na memória só para serem
    indexadas — reaproveitado tal qual pela Nuvem IPED, que já recebe o mbox inteiro em
    memória (o IPED não expõe leitura em streaming do conteúdo de um item)."""
    linhas = []
    atual = None  # bytes da mensagem corrente
    contador = 0
    for linha in _iterar_linhas(arquivo):
        if linha.startswith(b"From ") and atual is not None:
            linhas.extend(parse_uma_mensagem(atual))
            atual = b""
            contador += 1
            if contador % 200 == 0:
                _checkpoint(ao_progredir)
            if len(linhas) >= _BLOCO_GRAVACAO:
                yield linhas
                linhas = []
        elif linha.startswith(b"From "):
            atual = b""
        elif atual is not None:
            atual += linha
            if len(atual) > 10_000_000:      # mensagem patológica (anexo enorme) — não acumular
                atual = b""
    if atual:
        linhas.extend(parse_uma_mensagem(atual))
    if linhas:
        yield linhas


def parse_uma_mensagem(bruto: bytes) -> list[dict]:
    try:
        msg = email.message_from_bytes(bruto)
    except Exception:
        return []
    return [{
        "fonte": "Google (Gmail)",
        "remetente": _txt(msg.get("From")),
        "destinatarios": _txt(msg.get("To")),
        "cc": _txt(msg.get("Cc")),
        "cco": None,
        "assunto": _txt(msg.get("Subject")),
        "corpo": _decodificar_partes(msg),
        "data": _dt_header(msg),
        "tipo": "Gmail (Google Takeout)",
    }]


def _checkpoint(ao_progredir):
    if ao_progredir is not None:
        ao_progredir()



# ---------------------------------------------------------------------------
# Parser de navegação (Chrome.History/Bookmarks)
# ---------------------------------------------------------------------------

def parse_chrome_history(texto: str) -> list[dict]:
    """Devolve dicts no formato de investigacao_utils._gravar_pagina_visitada
    (mesma tabela NAV_TB01_PAGINAS_VISITADAS do UFDR)."""
    try:
        dados = json.loads(texto)
    except (ValueError, TypeError):
        return []
    linhas = []
    # Formato atual do Takeout: {"Browser History": [{url, title, time_usec, ...}]}
    for nav in (dados or {}).get("Browser History") or []:
        ts = nav.get("time_usec")
        dt = None
        if ts:
            try:
                dt = datetime.fromtimestamp(int(ts) / 1_000_000, tz=timezone.utc).replace(tzinfo=None)
            except (ValueError, OverflowError, OSError):
                dt = None
        url = _txt(nav.get("url") or nav.get("virtual_url"))
        titulo = _txt(nav.get("title"))
        if url or titulo or dt:
            linhas.append({"aplicativo": "Chrome (Google Takeout)", "titulo": titulo, "url": url,
                            "timestamp": dt, "qt_visitas": None, "conta": None})
    # Formato antigo: {"Session": [tabs com navigation]} — mantido para backups antigos
    for sessao in (dados or {}).get("Session") or []:
        for tab in _procurar_tabs(sessao):
            for nav in tab.get("navigation") or []:
                ts = nav.get("timestamp_msec")
                dt = None
                if ts:
                    try:
                        dt = datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc).replace(tzinfo=None)
                    except (ValueError, OverflowError, OSError):
                        dt = None
                url = _txt(nav.get("url") or nav.get("virtual_url"))
                titulo = _txt(nav.get("title"))
                if url or titulo or dt:
                    linhas.append({"aplicativo": "Chrome (Google Takeout)", "titulo": titulo, "url": url,
                                    "timestamp": dt, "qt_visitas": None, "conta": None})
    return linhas


def _procurar_tabs(sessao: dict) -> list[dict]:
    """A árvore do History.json varia (a tab às vezes vem como dict, às vezes a
    sessão é uma lista) — varre procurando dicts com a chave 'navigation'."""
    tabs = []
    fila = [sessao]
    while fila:
        n = fila.pop()
        if isinstance(n, dict):
            if isinstance(n.get("navigation"), list) and ("tab" in n or "tab_id" in n):
                tabs.append(n)
            fila.extend(n.values())
        elif isinstance(n, list):
            fila.extend(n)
    return tabs


def parse_bookmarks_html(texto: str) -> list[dict]:
    """Bookmarks.html do Takeout. Formato atual: NETSCAPE (<DT><A HREF="..."
    ADD_DATE="ts">título</A>); o formato antigo era uma tabela <tr><td>.</td></tr>.
    Devolve dicts no formato de investigacao_utils._gravar_pagina_visitada."""
    linhas = []
    for m in re.finditer(r'<DT><A\b[^>]*HREF="([^"]*)"[^>]*>(.*?)</A>', texto, re.S | re.I):
        url = html_mod.unescape(m.group(1)).strip()
        titulo = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        dt = None
        atributos = m.group(0)
        m_ts = re.search(r'ADD_DATE="(\d+)"', atributos)
        if m_ts:
            dt = datetime.fromtimestamp(int(m_ts.group(1)), tz=timezone.utc).replace(tzinfo=None)
        if url or titulo:
            linhas.append({"aplicativo": "Chrome (favorito)", "titulo": _txt(titulo) or _txt(url),
                            "url": _txt(url), "timestamp": dt, "qt_visitas": None, "conta": None})
    if linhas:
        return linhas
    for linha in re.findall(r"<tr[^>]*>(.*?)</tr>", texto, re.S | re.I):
        celulas = [re.sub(r"<[^>]+>", "", c).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", linha, re.S | re.I)]
        if len(celulas) < 2:
            continue
        titulo, url = celulas[0], celulas[1]
        dt = _data_google(celulas[2]) if len(celulas) > 2 else None
        if url or titulo:
            linhas.append({"aplicativo": "Chrome (favorito)", "titulo": _txt(titulo),
                            "url": _txt(url), "timestamp": dt, "qt_visitas": None, "conta": None})
    return linhas



# ---------------------------------------------------------------------------
# Parser de contatos (VCF)
# ---------------------------------------------------------------------------

def _vcf_linhas(texto: str):
    """Desdobra linhas continuadas (começam com espaço) e ignora as vazias."""
    buf = ""
    for linha in texto.replace("\r\n", "\n").split("\n"):
        if linha[:1] in (" ", "\t"):
            buf += linha[1:]
            continue
        if buf:
            yield buf
        buf = linha.strip()
    if buf:
        yield buf


def _vcf_campo(linha: str) -> tuple[str, list[str], str]:
    """'item1.EMAIL;TYPE=INTERNET:ajuda@x.com' -> ('email', ['internet'], 'ajuda@x.com').
    O prefixo 'itemN.' do Android é descartado para achar o nome do campo."""
    nome, sep, valor = linha.partition(":")
    if sep and "." in nome:
        nome = nome.split(".", 1)[1]
    # o atributo pode vir como "TYPE=CELL" (padrão) ou "type=CELL" (Apple): em ambos o VALOR
    # depois do '=' é a categoria; sem '=' (ex. "pref"), o próprio token é a categoria.
    atributos = [a.split("=", 1)[-1].lower() for a in nome.split(";")[1:]]
    return nome.split(";", 1)[0].upper(), atributos, valor


def parse_vcf(texto: str, no_origem: str) -> list[dict]:
    """Devolve dicts no formato de investigacao_utils._gravar_contato_agenda (mesmas
    tabelas CTT_TB01_CONTATOS/CTT_TB02_CONTATO_ENTRADAS do UFDR) — cada telefone/e-mail
    vira uma entrada própria, com a categoria do TYPE= do vCard quando presente."""
    contatos = []
    cartao = None
    for linha in _vcf_linhas(texto):
        if linha.startswith("BEGIN:VCARD"):
            cartao = {}
        elif linha.startswith("END:VCARD") and cartao is not None:
            telefones = cartao.get("tel") or []
            emails = cartao.get("email") or []
            nome = _txt(cartao.get("fn") or cartao.get("n")) or (_txt(emails[0][0]) if emails else None)
            entradas = [
                {"tipo": "PhoneNumber", "categoria": _txt(", ".join(cat)) if cat else None, "valor": valor}
                for valor, cat in telefones if valor
            ] + [
                {"tipo": "EmailAddress", "categoria": _txt(", ".join(cat)) if cat else None, "valor": valor}
                for valor, cat in emails if valor
            ]
            if nome or entradas:
                contatos.append({
                    "nome": nome,
                    "aplicativo_origem": "Google Contacts",
                    "grupo": Path(no_origem).stem,
                    "tipo": "Google",
                    "entradas": entradas,
                })
            cartao = None
        elif cartao is not None:
            nome, atributos, valor = _vcf_campo(linha)
            if nome in ("TEL", "EMAIL"):
                cartao.setdefault(nome.lower(), []).append((valor, atributos))
            else:
                cartao.setdefault(nome.lower(), valor)
    return contatos



# ---------------------------------------------------------------------------
# Parser de acessos (Access Log Activity)
# ---------------------------------------------------------------------------

def parse_activity_csv(blob: bytes):
    """Devolve dicts no formato de contas_nuvem_utils._gravar_acesso_conta
    (mesma tabela ACS_TB01_ACESSOS genérica, ver 29_nuvem.sql)."""
    linhas = []
    texto = io.StringIO(blob.decode("utf-8", errors="replace"), newline="")
    leitor = csv.DictReader(texto)
    for linha in leitor:
        linhas.append({
            "timestamp": _data_google(linha.get("Activity Timestamp")),
            "ip": _txt(linha.get("IP Address")),
            "ip_proxy": _txt(linha.get("Proxiedhost IP Address")),
            "ip_nao_roteavel": (linha.get("Is Non-routable IP Address") or "").strip().lower() in ("yes", "true", "1"),
            "user_agent": _texto_livre(linha.get("User Agent String"), 3000),
            "produto": _txt(linha.get("Product Name")),
            "subproduto": _txt(linha.get("Sub-Product Name")),
            "tipo": _txt(linha.get("Activity Type")),
            "canal": _txt(linha.get("Gmail Access Channel")),
            "android_id": _txt(linha.get("Android ID")),
            "ios_id": _txt(linha.get("IOS ID")),
            "outro_dispositivo": _txt(linha.get("Other Device ID")),
            "imap_id": _txt(linha.get("Imap ID")),
            "activesync_id": _txt(linha.get("Activesync ID")),
            "resolucao": _txt(linha.get("Screen Resolution")),
            "oauth_login_id": _txt(linha.get("Oauth Login ID")),
            "stateful_token_id": _txt(linha.get("Stateful Token ID")),
            "cookie_auth_time": _txt(linha.get("Cookie Auth Time")),
            "fonte": "Google (AccessLogActivity)",
        })
        if len(linhas) >= _BLOCO_GRAVACAO * 2:
            yield linhas
            linhas = []
    if linhas:
        yield linhas


def parse_agregado_csv(blob: bytes):
    """Devolve (dicts_agregados, dicts_conexoes) no formato de
    contas_nuvem_utils._gravar_agregado_conta/_gravar_conexao_conta. A separação é
    feita pelo cabeçalho: cada arquivo das AggregatedActivities tem colunas próprias."""
    servicos = []
    conexoes = []
    texto = io.StringIO(blob.decode("utf-8", errors="replace"), newline="")
    leitor = csv.DictReader(texto)
    for linha in leitor:
        if linha.get("Identifier Type 1") is not None and linha.get("ID 1") is not None:
            conexoes.append({
                "primeira": _data_google(linha.get("First Connection Timestamp")),
                "ultima": _data_google(linha.get("Last Connection Timestamp")),
                "tipo_1": _txt(linha.get("Identifier Type 1")),
                "identificador_1": _texto_livre(linha.get("ID 1"), 3000),
                "tipo_2": _txt(linha.get("Identifier Type 2")),
                "identificador_2": _texto_livre(linha.get("ID 2"), 3000),
                "fonte": "Google (AggregatedActivities)",
            })
        else:
            multilogin = (linha.get("Multilogin Used") or "").strip().lower()
            servicos.append({
                "primeira": _data_google(linha.get("First Activity Timestamp")),
                "ultima": _data_google(linha.get("Last Activity Timestamp")),
                "dias_ativos": _txt(linha.get("Days Active")),
                "tipo": _txt(linha.get("Identifier Type")),
                "identificador": _texto_livre(linha.get("ID"), 3000),
                "multilogin": multilogin in ("yes", "true", "1"),
                "canal_autenticacao": _txt(linha.get("Authentication Channel")),
                "fonte": "Google (AggregatedActivities)",
            })
    return servicos, conexoes



# ---------------------------------------------------------------------------
# Parser de Minha Atividade (HTML)
# ---------------------------------------------------------------------------

_RE_BLOCO_ATIVIDADE = re.compile(
    r'<div class="outer-cell[^"]*"[^>]*>(.*?)</div>\s*</div>\s*</div>', re.S)
_RE_TITULO_BLOCO = re.compile(r'header-cell[^>]*>(.*?)</div>', re.S)
# O bloco acima termina antes do </div> que fecha o content-cell interno, então o
# corpo é "até o fim do bloco", não "até o próximo </div>".
_RE_CORPO_BLOCO = re.compile(
    r'content-cell[^"]*mdl-typography--body-1[^>]*>(.*)$', re.S)
_RE_DATA_ATIVIDADE = re.compile(
    r"([A-Z][a-z]{2} \d{1,2}, \d{4}, \d{1,2}:\d{2}:\d{2} (?:AM|PM) UTC)")


def parse_my_activity(texto: str) -> list[dict]:
    """Devolve dicts no formato de contas_nuvem_utils._gravar_atividade_conta."""
    linhas = []
    for bloco in _RE_BLOCO_ATIVIDADE.findall(texto):
        m_titulo = _RE_TITULO_BLOCO.search(bloco)
        produto = re.sub(r"<[^>]+>", "", m_titulo.group(1)).strip() if m_titulo else None
        m_corpo = _RE_CORPO_BLOCO.search(bloco)
        if not m_corpo:
            continue
        conteudo = m_corpo.group(1)
        m_url = re.search(r'href="([^"]+)"', conteudo)
        url = m_url.group(1) if m_url else None
        texto_limpo = html_mod.unescape(re.sub(r"<[^>]+>", "", conteudo))
        texto_limpo = texto_limpo.replace("\xa0", " ").replace("\u202f", " ")
        texto_limpo = re.sub(r"[ \t]+", " ", texto_limpo).strip()
        m_data = _RE_DATA_ATIVIDADE.search(texto_limpo)
        dt = _data_google(m_data.group(1)) if m_data else None
        descricao = texto_limpo
        if m_data:
            descricao = (texto_limpo[:m_data.start()].strip() or None)
        if dt or descricao:
            linhas.append({
                "timestamp": dt,
                "produto": _txt(produto),
                "descricao": _texto_livre(descricao, 3000),
                "url": _txt(url),
                "fonte": "Google (MyActivity)",
            })
    return linhas



# ---------------------------------------------------------------------------
# Parser de apps (Google Play Store)
# ---------------------------------------------------------------------------

def parse_playstore_csv(blob: bytes):
    """Devolve dicts no formato de investigacao_utils._gravar_aplicativo (mesma
    tabela APP_TB01_APLICATIVOS do UFDR). Cobre os 4 CSVs do Play Store (Installs,
    Library, InstallHistory, UserActivity) — todos com as mesmas colunas de origem."""
    linhas = []
    texto = io.StringIO(blob.decode("utf-8", errors="replace"), newline="")
    leitor = csv.DictReader(texto)
    for linha in leitor:
        linhas.append({
            "pacote": _txt(linha.get("Package Name") or linha.get("Docid")),
            "nome": _txt(linha.get("Doc Title")),
            "versao": None,
            "descricao": _txt(linha.get("State")),
            "instalacao": _data_google(linha.get("First Installation Time") or linha.get("Installed Time") or linha.get("Install Time")),
            "ultimo_uso": _data_google(linha.get("Last Update Time") or linha.get("Last Updated Time")),
        })
        if len(linhas) >= _BLOCO_GRAVACAO:
            yield linhas
            linhas = []
    if linhas:
        yield linhas



# ---------------------------------------------------------------------------
# Parser de dispositivos (Android Device Configuration Service)
# ---------------------------------------------------------------------------

_RE_SECAO_ADCS = re.compile(r'<h3 class="category-title">(.*?)</h3>(.*?)(?=<h3 class="category-title">|<hr|$)', re.S)
_RE_LINHA_ADCS = re.compile(r"^\s*([A-Za-z][A-Za-z /()'-]*?):\s*(.*?)\s*$")


def parse_adcs(texto: str, tp_origem: str) -> list[dict]:
    """Devolve dicts no formato de contas_nuvem_utils._gravar_dispositivo_conta."""
    pares: dict[str, str] = {}
    for _, corpo in _RE_SECAO_ADCS.findall(texto):
        for trecho in corpo.replace("<br/>", "\n").replace("<br>", "\n").split("\n"):
            m = _RE_LINHA_ADCS.match(trecho)
            if m and m.group(2):
                pares[m.group(1).strip()] = html_mod.unescape(
                    re.sub(r"<[^>]+>", "", m.group(2)).strip())
    if not pares:
        return []
    android_id = _txt(pares.get("Android ID"))
    if not android_id:
        return []
    return [{
        "nome": _txt(pares.get("Model")),
        "fabricante": _txt(pares.get("Brand") or pares.get("Manufacturer")),
        "android_id": android_id,
        "imei": _texto_livre(pares.get("IMEI(s)"), 500),
        "versao": None,
        "serial": _texto_livre(pares.get("Serial Number(s)"), 500),
        "outros": _texto_livre(json.dumps(pares, ensure_ascii=False)),
        "origem": tp_origem,
        "fonte": "Google (AndroidDeviceConfigurationService)",
    }]



# ---------------------------------------------------------------------------
# Parser de eventos (ICS)
# ---------------------------------------------------------------------------

def _ics_desdobrar(texto: str) -> list[str]:
    linhas = []
    for linha in texto.replace("\r\n", "\n").split("\n"):
        if linha[:1] in (" ", "\t") and linhas:
            linhas[-1] += linha[1:]
        else:
            linhas.append(linha.strip())
    return linhas


def _ics_data(valor: str):
    """'20190828T080000' (o parse_ics já separou o TZID no ':') -> datetime.
    Datas sem Z são locais (sem fuso) — guardadas como vieram; com Z, UTC."""
    return _data_google(valor)


def parse_ics(texto: str) -> list[dict]:
    """Devolve dicts no formato de contas_nuvem_utils._gravar_evento_conta."""
    eventos = []
    atual = None
    for linha in _ics_desdobrar(texto):
        if linha == "BEGIN:VEVENT":
            atual = {}
        elif linha == "END:VEVENT" and atual is not None:
            def _limpar_email(v: str) -> str:
                v = v.strip()
                if v.lower().startswith("mailto:"):
                    v = v[7:]
                if v.startswith("CN="):
                    v = v.split(":", 1)[-1] if ":" in v else v[3:]
                return v.strip()

            participantes = [_limpar_email(p) for p in atual.get("attendee", []) if p]
            organizador = _limpar_email(atual.get("organizer", ""))
            eventos.append({
                "inicio": _ics_data(atual.get("dtstart", "")),
                "fim": _ics_data(atual.get("dtend", "")),
                "criacao": _data_google(atual.get("created", "")),
                "alteracao": _data_google(atual.get("last-modified", "")),
                "titulo": _txt(atual.get("summary")),
                "local": _txt(atual.get("location")),
                "descricao": _texto_livre(atual.get("description"), 3000),
                "organizador": _txt(organizador),
                "participantes": _texto_livre("; ".join(participantes), 3000) or None,
                "fonte": "Google (Calendar)",
            })
            atual = None
        elif atual is not None and ":" in linha:
            nome, _, valor = linha.partition(":")
            chave = nome.split(";", 1)[0].lower()
            if chave in ("summary", "location", "description", "dtstart", "dtend",
                         "created", "last-modified", "organizer"):
                atual[chave] = valor
            elif chave == "attendee":
                atual.setdefault("attendee", []).append(valor)
    return eventos



# ---------------------------------------------------------------------------
# Parser do perfil da conta (SubscriberInfo HTML)
# ---------------------------------------------------------------------------

def parse_subscriber_info(texto: str) -> dict | None:
    """Devolve um dict no formato de contas_nuvem_utils._gravar_perfil_conta."""
    pares: dict[str, str] = {}
    for li in re.findall(r"<li>(.*?)</li>", texto, re.S):
        nome, sep, valor = li.partition(":")
        if sep:
            pares[nome.strip()] = html_mod.unescape(valor.strip())
    if not pares.get("Google Account ID"):
        return None
    return {
        "nome": _txt(pares.get("Name")),
        "nome_dado": _txt(pares.get("Given Name")),
        "nome_familia": _txt(pares.get("Family Name")),
        "email": _txt(pares.get("e-Mail")),
        "email_alternativos": _texto_livre(pares.get("Alternate e-Mails"), 3000) or None,
        "criacao": _data_google(pares.get("Created on")),
        "ip_tos": _txt(pares.get("Terms of Service IP")),
        "idioma": _txt(pares.get("Terms of Service Language")),
        "pais": _txt(pares.get("Terms of Service Country")),
        "nascimento": _txt(pares.get("Birthday (Month Day, Year)")),
        "servicos": _texto_livre(pares.get("Services")),
        "ultima_atualizacao": _data_google(pares.get("Last Updated Date")),
        "exclusao": _data_google(pares.get("Deletion Date")),
        "status": _txt(pares.get("Status")),
        "fonte": "Google (GoogleAccount.SubscriberInfo)",
    }



# ---------------------------------------------------------------------------
# Parser de ligações (Duo/Call History.csv)
# ---------------------------------------------------------------------------

def parse_duo_call_history(blob: bytes):
    """Duo/Call History.csv — log de chamadas de voz/vídeo do Google Duo (hoje
    fundido ao Meet). Devolve dicts no formato de investigacao_utils._gravar_ligacao
    (mesma tabela LIG_TB01_LIGACOES do UFDR — reaproveitada em vez de criar uma
    tabela nova). StateKey vem como 'email:x@y.com' ou 'tel:+55...'; o prefixo é
    descartado. O cabeçalho real do Takeout tem espaços sobrando em alguns nomes de
    coluna ('CallDurationSeconds ') — por isso as chaves são normalizadas primeiro."""
    linhas = []
    texto = io.StringIO(blob.decode("utf-8", errors="replace"), newline="")
    leitor = csv.DictReader(texto)
    for bruta in leitor:
        linha = {(k or "").strip(): v for k, v in bruta.items()}
        outgoing = (linha.get("IsOutgoingCall") or "").strip().lower() == "true"
        video = (linha.get("IsVideoCall") or "").strip().lower() == "true"
        try:
            duracao = int(float(linha.get("CallDurationSeconds") or ""))
        except ValueError:
            duracao = None
        contato = _txt(linha.get("StateKey"))
        if contato:
            contato = re.sub(r"^(email|tel):", "", contato)
        linhas.append({
            "aplicativo": "Google Duo",
            "direcao": "Outgoing" if outgoing else "Incoming",
            "status": _txt(linha.get("CallResult")) or _txt(linha.get("CallState")),
            "timestamp": _data_google(linha.get("UpdatedAt")),
            "duracao_segundos": duracao,
            "videochamada": video,
            "telefone": contato,
            "nome_contato": None,
        })
        if len(linhas) >= _BLOCO_GRAVACAO:
            yield linhas
            linhas = []
    if linhas:
        yield linhas



# ---------------------------------------------------------------------------
# Parser de localizações (Maps/Saved — GeoJSON; Timeline — histórico de localização)
# ---------------------------------------------------------------------------

def _e7_para_grau(valor) -> float | None:
    """Converte um campo '...E7' do Google (grau × 10^7) pra float. Confirmado contra
    dado real (Saved/Want to go.json): valores negativos às vezes vêm como inteiro
    UNSIGNED de 32 bits — ex. latE7 = 4091633210 é na verdade -20.3334086
    (4091633210 - 2**32); sem essa correção a divisão direta por 1e7 dá uma
    'latitude' de 409°, fora de qualquer intervalo válido."""
    try:
        v = int(valor)
    except (TypeError, ValueError):
        return None
    if v > 0x7FFFFFFF:
        v -= 0x100000000
    return v / 1e7


def _linhas_geojson(dados: dict, aplicativo: str) -> list[dict]:
    """Núcleo comum dos locais em GeoJSON simples ('features': [{geometry, properties}]).
    'coordinates' é [longitude, latitude] (padrão GeoJSON). Pontos (0, 0) são o
    sentinela do Takeout pra 'local sem coordenada conhecida' e são descartados."""
    linhas = []
    for feat in (dados or {}).get("features") or []:
        geom = feat.get("geometry") or {}
        coords = geom.get("coordinates")
        if not isinstance(coords, list) or len(coords) < 2:
            continue
        try:
            lon, lat = float(coords[0]), float(coords[1])
        except (TypeError, ValueError):
            continue
        if lat == 0 and lon == 0:
            continue
        props = feat.get("properties") or {}
        linhas.append({
            "aplicativo": aplicativo,
            "timestamp": _data_google(props.get("date") or props.get("Date")),
            "latitude": lat,
            "longitude": lon,
            "endereco": _txt(props.get("address")),
            "categoria": _txt(props.get("google_maps_url")),
            "descricao": _txt(props.get("name") or props.get("Comment")),
        })
    return linhas


def parse_geojson_places(texto: str, aplicativo: str) -> list[dict]:
    """Maps/My labeled places/Labeled places.json — locais rotulados (endereço de
    casa/trabalho), sempre em GeoJSON simples. Devolve dicts no formato de
    investigacao_utils._gravar_localizacao (mesma tabela LOC_TB01_LOCALIZACOES do UFDR)."""
    try:
        dados = json.loads(texto)
    except (ValueError, TypeError):
        return []
    return _linhas_geojson(dados, aplicativo)


def parse_saved_places(texto: str, aplicativo: str) -> list[dict]:
    """Saved/*.json (locais salvos/favoritados) — DOIS formatos vistos dentro do
    mesmo produto (Saved.SavedCollections): GeoJSON simples (Stars.json — mas na
    prática costuma vir com coordenada (0, 0) + comentário "No location
    information...", então normalmente não gera nada) e a lista mais rica
    ('Want to go.json', 'Saved for later.json', 'Images.json' — list.listItems[],
    com 'place.latLng' em E7 + título + URL do Maps). Tenta os dois formatos; cada
    arquivo real do produto só bate com um deles."""
    try:
        dados = json.loads(texto)
    except (ValueError, TypeError):
        return []
    linhas = _linhas_geojson(dados, aplicativo)
    if linhas:
        return linhas
    for item in ((dados or {}).get("list") or {}).get("listItems") or []:
        lat_lng = (item.get("place") or {}).get("latLng") or {}
        lat = _e7_para_grau(lat_lng.get("latE7"))
        lon = _e7_para_grau(lat_lng.get("lngE7"))
        if lat is None or lon is None:
            continue
        linhas.append({
            "aplicativo": aplicativo,
            "timestamp": _data_google(item.get("createTime")),
            "latitude": lat,
            "longitude": lon,
            "endereco": None,
            "categoria": _txt((item.get("viewer") or {}).get("url")),
            "descricao": _txt(item.get("title")),
        })
    return linhas


_RE_GEO = re.compile(r"geo:(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)")


def _geo_latlon(valor) -> tuple[float, float] | None:
    if not valor:
        return None
    m = _RE_GEO.search(str(valor))
    if not m:
        return None
    try:
        return float(m.group(1)), float(m.group(2))
    except ValueError:
        return None


def parse_timeline_json(texto: str) -> list[dict]:
    """Timeline (antigo 'Histórico de localização') — NÃO PÔDE ser validado contra um
    ZIP real: todas as contas da amostra em data/SIP/0.IMPLEMENTAÇÃO-GOOGLE vieram sem
    registros pra Timeline.Records/SemanticLocationHistory. Cobre os dois formatos
    documentados do Takeout — o clássico ('locations': [...] em lat/lonE7) e o atual
    ('semanticSegments'/'rawSignals' do Timeline on-device, com 'geo:lat,lng') — mas
    precisa ser conferido no primeiro caso real com dados antes de confiar cegamente."""
    try:
        dados = json.loads(texto)
    except (ValueError, TypeError):
        return []
    linhas = []
    for loc in (dados or {}).get("locations") or []:
        lat = _e7_para_grau(loc.get("latitudeE7"))
        lon = _e7_para_grau(loc.get("longitudeE7"))
        if lat is None or lon is None:
            continue
        linhas.append({
            "aplicativo": "Google (Timeline)",
            "timestamp": _data_google(loc.get("timestamp")),
            "latitude": lat, "longitude": lon,
            "endereco": None, "categoria": None,
            "descricao": _txt(loc.get("activity")),
        })
    for seg in (dados or {}).get("semanticSegments") or []:
        visita = (seg.get("visit") or {}).get("topCandidate") or {}
        ponto = _geo_latlon(visita.get("placeLocation"))
        if ponto:
            linhas.append({
                "aplicativo": "Google (Timeline)",
                "timestamp": _data_google(seg.get("startTime")),
                "latitude": ponto[0], "longitude": ponto[1],
                "endereco": None, "categoria": _txt(visita.get("semanticType")),
                "descricao": _txt(visita.get("placeId")),
            })
        for ponto_path in seg.get("timelinePath") or []:
            p = _geo_latlon(ponto_path.get("point"))
            if p:
                linhas.append({
                    "aplicativo": "Google (Timeline)",
                    "timestamp": _data_google(ponto_path.get("time")),
                    "latitude": p[0], "longitude": p[1],
                    "endereco": None, "categoria": "timelinePath", "descricao": None,
                })
    for sinal in (dados or {}).get("rawSignals") or []:
        pos = sinal.get("position") or {}
        p = _geo_latlon(pos.get("LatLng") or pos.get("point"))
        if p:
            linhas.append({
                "aplicativo": "Google (Timeline)",
                "timestamp": _data_google(pos.get("timestamp")),
                "latitude": p[0], "longitude": p[1],
                "endereco": None, "categoria": "rawSignal", "descricao": None,
            })
    return linhas



# ---------------------------------------------------------------------------
# Parser de notas (Chrome Autofill; Keep — Google Keep)
# ---------------------------------------------------------------------------

_EPOCA_CHROME = datetime(1601, 1, 1)


def _data_chrome(valor) -> datetime | None:
    """Timestamp do Chrome: microssegundos desde 1601-01-01 (WebKit/Chrome epoch,
    diferente do epoch Unix usado no resto do arquivo)."""
    try:
        v = int(valor)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    try:
        return _EPOCA_CHROME + timedelta(microseconds=v)
    except OverflowError:
        return None


def _data_epoch_usec(valor) -> datetime | None:
    """Timestamp em microssegundos desde o epoch Unix (formato do Google Keep)."""
    try:
        v = int(valor)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    try:
        return datetime.fromtimestamp(v / 1_000_000, tz=timezone.utc).replace(tzinfo=None)
    except (OverflowError, OSError, ValueError):
        return None


def parse_chrome_autofill(texto: str) -> list[dict]:
    """Chrome/Addresses and more.json — autofill do Chrome (nome, CPF, endereço,
    telefone, e até campos de senha digitados em formulário chegam aqui). Uma linha
    de AFL_TB01_AUTOFILL por entrada (mesma tela "Preenchimento automático" do UFDR),
    com o campo (name) em NO_CHAVE e o dado (value) em TX_VALOR. Já passou por duas
    regras erradas: a de Chrome/*.json do histórico (que não reconhece a chave
    'Autofill' e descartava o arquivo) e a de nota, que embrulhava tudo numa nota
    única — o lugar certo é o domínio autofill, campo por campo."""
    try:
        dados = json.loads(texto)
    except (ValueError, TypeError):
        return []
    linhas = []
    for item in (dados or {}).get("Autofill") or []:
        nome = _txt(item.get("name"))
        valor = _txt(item.get("value"))
        if not nome and not valor:
            continue
        ts = item.get("usage_timestamp")
        usos = [dt for t in (ts if isinstance(ts, list) else ([ts] if ts is not None else []))
                if (dt := _data_chrome(t))]
        linhas.append({
            "fonte": "Chrome (Google Takeout)",
            "chave": nome,
            "valor": valor,
            "tipo_modelo": None,
            "registro": _data_chrome(item.get("timestamp")),
            "ultimo_uso": max(usos) if usos else None,
            "estado": "Intact",
        })
    return linhas


def parse_keep_note(texto: str) -> dict | None:
    """Keep/*.json — uma nota por arquivo (formato estável do Takeout há vários anos:
    title/textContent, ou listContent pra nota de lista; timestamps em microssegundos
    desde o epoch Unix). Vazio na amostra usada pra validar este parser (todas as
    contas de data/SIP/0.IMPLEMENTAÇÃO-GOOGLE vieram sem registros pra Keep.Content) —
    conferir no primeiro caso real com notas. Devolve dict no formato de
    investigacao_utils._gravar_nota (mesma tabela NOT_TB01_NOTAS de Samsung/Apple Notes)."""
    try:
        dados = json.loads(texto)
    except (ValueError, TypeError):
        return None
    if not isinstance(dados, dict):
        return None
    titulo = _txt(dados.get("title"))
    corpo = _texto_livre(dados.get("textContent"), limite=50_000)
    itens_lista = dados.get("listContent")
    if not corpo and isinstance(itens_lista, list) and itens_lista:
        corpo = _texto_livre(
            "\n".join(("[x] " if item.get("isChecked") else "[ ] ") + (item.get("text") or "")
                      for item in itens_lista),
            limite=50_000,
        )
    if not titulo and not corpo:
        return None
    anexos = [_txt(a.get("filePath")) for a in (dados.get("attachments") or []) if a.get("filePath")]
    return {
        "aplicativo": "Google Keep",
        "titulo": titulo,
        "corpo": corpo,
        "criacao": _data_epoch_usec(dados.get("createdTimestampUsec")),
        "modificacao": _data_epoch_usec(dados.get("userEditedTimestampUsec")),
        "status": "Trash" if dados.get("isTrashed") else "Intact",
        "anexos": [a for a in anexos if a] or None,
    }



# ---------------------------------------------------------------------------
# Apple iCloud (Backup) — nuvem Apple dentro do .ufdr do Cellebrite
#
# Um .ufdr "ICLOUD_BACKUP" não traz NADA decodificado no report.xml: todo o conteúdo vive nos
# arquivos crus — vCards da agenda, o MessagesMetadata.txt (JSON CloudKit com o histórico de
# SMS/iMessage) e os tars/zips de mídia. Os parsers abaixo cobrem o conteúdo textual; a mídia
# e o iCloud Drive viram arquivos (ARQ_TB01) no roteador de funcoes/ufdr_icloud.py.
# ---------------------------------------------------------------------------

# Epoch da Apple (2001-01-01) em nanossegundos — o campo `time` do CloudKit.
_EPOCA_APPLE = datetime(2001, 1, 1)


def _data_apple_ns(valor) -> datetime | None:
    """Nanossegundos desde 2001-01-01 (campo `time` do Messages in iCloud) -> datetime UTC."""
    try:
        v = int(valor)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    try:
        return _EPOCA_APPLE + timedelta(microseconds=v / 1000)
    except OverflowError:
        return None


def decodificar_corpo_msgproto(valor: str) -> str | None:
    """Corpo de uma mensagem do Messages in iCloud, decodificado do campo `msgProto`.

    O msgProto é base64 → gzip → protobuf (formato interno do Apple/iMessage). O corpo é o
    campo 3 do protobuf (length-delimited, UTF-8); os campos seguintes são a versão
    NSAttributedString do mesmo texto (binária, redundante) e os metadados. None quando não há
    corpo aproveitável ou o valor não decodifica."""
    if not valor:
        return None
    try:
        raw = base64.b64decode(valor)
        gz = gzip.decompress(raw)
    except Exception:
        return None
    i = 0
    n = len(gz)
    while i < n:
        tag = 0
        shift = 0
        while True:
            if i >= n:
                return None
            x = gz[i]
            i += 1
            tag |= (x & 0x7F) << shift
            shift += 7
            if not (x & 0x80):
                break
        numero = tag >> 3
        tipo = tag & 7
        if tipo == 0:
            while True:
                if i >= n:
                    return None
                x = gz[i]
                i += 1
                if not (x & 0x80):
                    break
        elif tipo == 2:
            tamanho = 0
            shift = 0
            while True:
                if i >= n:
                    return None
                x = gz[i]
                i += 1
                tamanho |= (x & 0x7F) << shift
                shift += 7
                if not (x & 0x80):
                    break
            valor_bytes = gz[i:i + tamanho]
            i += tamanho
            if numero == 3:
                try:
                    texto = valor_bytes.decode("utf-8").strip()
                except UnicodeDecodeError:
                    texto = valor_bytes.decode("utf-8", "replace").strip()
                if texto:
                    return texto
        elif tipo == 5:
            i += 4
        elif tipo == 1:
            i += 8
        else:
            break
    return None


def parse_messages_icloud(texto: str) -> list[dict]:
    """Messages in iCloud (MessagesMetadata.txt) — JSON CloudKit com o histórico de SMS/iMessage.

    Cada registro MessageEncryptedV3 vira um dict cru: svc ("SMS"|"iMessage"), chat_id, dc_id
    (o número do dono da conta), sender, guid, corpo, timestamp e apagada. Quem chama agrupa por
    chat_id e monta as conversas (funcoes/ufdr_icloud.py)."""
    try:
        dados = json.loads(texto)
    except (ValueError, TypeError):
        return []
    mensagens = []
    for registro in dados or []:
        campos = registro.get("fields") or {}
        def _f(nome):
            return (campos.get(nome) or {}).get("value")
        svc = _f("svc")
        corpo = decodificar_corpo_msgproto(_f("msgProto"))
        utm = _f("utm")
        criado = (registro.get("created") or {}).get("timestamp")
        timestamp = _data_epoch_ms(utm) or _data_epoch_ms(criado) or _data_apple_ns(_f("time"))
        if not corpo and not timestamp:
            continue
        mensagens.append({
            "svc": svc,
            "chat_id": _f("chatID"),
            "dc_id": _f("dcId"),
            "sender": _f("sender"),
            "guid": _f("guid"),
            "corpo": corpo,
            "timestamp": timestamp,
            "apagada": bool(registro.get("deleted")),
        })
    return mensagens


# ---------------------------------------------------------------------------
# Classificação das entradas do ZIP
# ---------------------------------------------------------------------------

# (prefixo, sufixo, tabela, fase) — a primeira que casar decide o destino da entrada.
# 'aggregated' vem ANTES de 'activities -' porque "Aggregated Activities - connections..."
# contém o segundo como substring; e a checagem do sufixo é case-insensitive
# ("An aggregated representation of the Google services accessed by your device.csv").
_ROTEIRO = [
    ("Mail/", ".mbox", "tb02", "emails"),
    ("Mail/", ".json", "tb03", "info_emails"),
    # "Addresses and more" tem que vir ANTES da regra genérica de Chrome/*.json — senão
    # cai no parser de histórico de navegação, que não reconhece a chave "Autofill" e
    # descarta o arquivo inteiro sem erro. Vai para o domínio "autofill" (AFL_TB01),
    # não para nota: dado de formulário é preenchimento automático, não anotação do
    # usuário (a nota única que nascia aqui poluía a tela Notas).
    ("Chrome/", "addresses and more", "autofill", "autofill"),
    ("Chrome/", ".json", "tb04", "navegacao"),   # tb04; o parser decide History.json x Bookmarks.html
    ("Chrome/", ".html", "tb04", "navegacao"),
    ("Access Log Activity/", "aggregated", "tb07_08", "acessos"),
    ("Access Log Activity/", "activities -", "tb06", "acessos"),
    ("My Activity/", ".html", "tb09", "atividades"),
    ("Drive/", "-info.json", "tb10_info", "arquivos"),
    ("Drive/", None, "tb10", "arquivos"),
    ("Google Photos/", "photos/", "tb10", "arquivos"),
    ("Drive Mobile Backups/", None, "tb10", "arquivos"),
    ("Contacts/", ".vcf", "tb05", "contatos"),
    ("Calendar/", ".ics", "tb14", "eventos"),
    ("Google Play Store/", "installs", "tb11", "apps"),
    ("Google Play Store/", "library", "tb11", "apps"),
    # com espaço mesmo — o nome real do CSV é "Install History.csv"/"User Activity.html";
    # os sufixos sem espaço de antes nunca casavam e os dois produtos eram 100% perdidos.
    ("Google Play Store/", "install history", "tb11", "apps"),
    ("Google Play Store/", "user activity", "tb11", "apps"),
    ("Android Device Configuration Service/", ".html", "tb12", "dispositivos"),
    ("Google Account/", ".html", "tb13", "perfil_conta"),
    ("Duo/", "call history", "tb16", "ligacoes"),
    ("Maps/", "labeled places", "tb18", "localizacoes"),
    ("Saved/", ".json", "tb18", "localizacoes"),
    ("Timeline/", ".json", "tb18", "localizacoes"),
    ("Keep/", ".json", "tb17", "notas"),
]

_IGNORAR_PADRAO = re.compile(r"ExportSummary|NoRecords|Deletion Markers|Photo Features|\.txt$")
_IGNORAR_DIR = ("Google Photos/User/", "Google Photos/Photo Features.json")


def destino(nome: str) -> tuple[str, str] | None:
    if _IGNORAR_PADRAO.search(nome) or any(nome.startswith(p) for p in _IGNORAR_DIR):
        return None
    nome_low = nome.lower()
    for prefixo, sufixo, tabela, fase in _ROTEIRO:
        if nome.startswith(prefixo):
            if sufixo is None or (sufixo in nome_low):
                return tabela, fase
    return None


# ---------------------------------------------------------------------------
# Google Warrant Return (nuvem Google dentro do .ufdr do Cellebrite)
#
# A extração de nuvem do Cellebrite guarda os produtos do Google em ZIPs aninhados
# (files/Archives/<conta>.<Produto>_001.zip) cujo layout NÃO é o do Takeout padrão:
# o Histórico de localização vem como "Location History/*.json" (com os campos
# timestampMs/latitudeE7 no Records e timelineObjects no Semantic), o Play Store como
# "Play.AppInstalls.csv" (colunas "Doc Title"/"Doc Url"), e os locais salvos como texto
# protobuf. Os parsers abaixo cobrem esses formatos; o roteamento deles fica em
# funcoes/ufdr_nuvem.py (o `destino()` acima é o do Takeout clássico usado pela Nuvem IPED).
# ---------------------------------------------------------------------------


def _data_epoch_ms(valor) -> datetime | None:
    """Epoch em milissegundos (formato `timestampMs` do Histórico de localização) -> datetime."""
    try:
        v = int(valor)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    try:
        return datetime.fromtimestamp(v / 1000, tz=timezone.utc).replace(tzinfo=None)
    except (OverflowError, OSError, ValueError):
        return None


def _num_localizacao(valor) -> float | None:
    try:
        v = float(valor)
    except (TypeError, ValueError):
        return None
    return v


def parse_location_history_records(texto: str) -> list[dict]:
    """Location History/Records.json do Google Warrant Return — o histórico cru de
    coordenadas (`locations[]` com `timestampMs`, `latitudeE7`/`longitudeE7`, `accuracy`,
    `source`, `deviceTag`, `platform`).

    Devolve dicts no formato de investigacao_utils._gravar_localizacao (mesma tabela
    LOC_TB01_LOCALIZACOES do UFDR). Mais rico que o <model type="Location"> que o Cellebrite
    também emite no report.xml para o mesmo dado: aqui a precisão, o deviceTag e a platform
    sobrevivem — por isso quem ingere usa ESTE parser e descarta os Location de
    Source='Google Warrant Return' do XML."""
    try:
        dados = json.loads(texto)
    except (ValueError, TypeError):
        return []
    linhas = []
    for loc in (dados or {}).get("locations") or []:
        lat = _e7_para_grau(loc.get("latitudeE7"))
        lon = _e7_para_grau(loc.get("longitudeE7"))
        if lat is None or lon is None:
            continue
        linhas.append({
            "aplicativo": "Google (Histórico de localização)",
            "timestamp": _data_epoch_ms(loc.get("timestampMs")),
            "latitude": lat,
            "longitude": lon,
            "endereco": None,
            "categoria": _txt(loc.get("source")),
            "descricao": None,
            "precisao": _num_localizacao(loc.get("accuracy")),
            "elevacao": None,
            "tipo": None,
            "origem": _txt(loc.get("platform")),
            "fim": None,
            "conta": None,
            "estado": None,
        })
    return linhas


def _descricao_segmento(seg: dict) -> str | None:
    """Texto legível de um activitySegment: distância e confiança, para a coluna descrição."""
    partes = []
    distancia = seg.get("distance")
    if distancia is not None:
        partes.append(f"{distancia} m")
    confianca = seg.get("confidence")
    if confianca:
        partes.append(f"confiança {confianca}")
    return ", ".join(partes) or None


def parse_location_history_semantic(texto: str) -> list[dict]:
    """Location History/SemanticLocationHistory.json do Google Warrant Return — os segmentos
    DERIVADOS do histórico (`timelineObjects[]`: `activitySegment` com tipo de deslocamento e
    `placeVisit` com locais visitados). Não existe no report.xml; é o dado que o Cellebrite não
    decodifica em <model>.

    Devolve dicts no formato de investigacao_utils._gravar_localizacao. O segmento usa o ponto
    de INÍCIO como coordenada e o fim do deslocamento em `fim`; a visita usa a coordenada do
    lugar e o endereço quando o Google traz."""
    try:
        dados = json.loads(texto)
    except (ValueError, TypeError):
        return []
    linhas = []
    for objeto in (dados or {}).get("timelineObjects") or []:
        if not isinstance(objeto, dict):
            continue
        seg = objeto.get("activitySegment")
        if isinstance(seg, dict):
            inicio = seg.get("startLocation") or {}
            duracao = seg.get("duration") or {}
            lat = _e7_para_grau(inicio.get("latitudeE7"))
            lon = _e7_para_grau(inicio.get("longitudeE7"))
            if lat is not None and lon is not None:
                linhas.append({
                    "aplicativo": "Google (Histórico de localização)",
                    "timestamp": _data_epoch_ms(duracao.get("startTimestampMs")),
                    "latitude": lat,
                    "longitude": lon,
                    "endereco": None,
                    "categoria": _txt(seg.get("activityType")),
                    "descricao": _descricao_segmento(seg),
                    "precisao": None,
                    "elevacao": None,
                    "tipo": None,
                    "origem": None,
                    "fim": _data_epoch_ms(duracao.get("endTimestampMs")),
                    "conta": None,
                    "estado": None,
                })
        visita = objeto.get("placeVisit")
        if isinstance(visita, dict):
            lugar = visita.get("location") or {}
            duracao = visita.get("duration") or {}
            lat = _e7_para_grau(lugar.get("latitudeE7"))
            lon = _e7_para_grau(lugar.get("longitudeE7"))
            if lat is not None and lon is not None:
                linhas.append({
                    "aplicativo": "Google (Histórico de localização)",
                    "timestamp": _data_epoch_ms(duracao.get("startTimestampMs")),
                    "latitude": lat,
                    "longitude": lon,
                    "endereco": _txt(lugar.get("address")),
                    "categoria": "placeVisit",
                    "descricao": _txt(lugar.get("placeId")) or _txt(lugar.get("name")),
                    "precisao": None,
                    "elevacao": None,
                    "tipo": None,
                    "origem": None,
                    "fim": _data_epoch_ms(duracao.get("endTimestampMs")),
                    "conta": None,
                    "estado": None,
                })
    return linhas


def parse_playstore_warrant_csv(blob: bytes):
    """Google Play Store/*.csv do Google Warrant Return (AppInstalls/Purchases), com colunas
    "GAIA Id,Doc Title,Doc Url,First Installation Time,...". Diferente do Takeout padrão
    ("Package Name,..."): aqui o pacote não vem em coluna própria — está no parâmetro `id=` da
    Doc Url ou, quando o título é o próprio pacote (ex. "com.alcatel.music5"), no Doc Title.

    Devolve dicts no formato de investigacao_utils._gravar_aplicativo (APP_TB01_APLICATIVOS)."""
    linhas_brutas = blob.decode("utf-8", errors="replace").splitlines()
    # o CSV começa com uma linha de confidencialidade ("Google Confidential and Proprietary")
    # antes do cabeçalho — o DictReader usaria essa linha como nome das colunas e perderia tudo
    inicio = 0
    for i, linha in enumerate(linhas_brutas):
        if "GAIA Id" in linha or "Doc Title" in linha:
            inicio = i
            break
    texto = io.StringIO("\n".join(linhas_brutas[inicio:]), newline="")
    leitor = csv.DictReader(texto)
    linhas = []
    for linha in leitor:
        titulo = _txt(linha.get("Doc Title"))
        url = linha.get("Doc Url") or ""
        pacote = None
        m = re.search(r"[?&]id=([^&#]+)", url)
        if m:
            pacote = _txt(m.group(1))
        # Doc Title com cara de pacote ("com.alcatel.music5") e sem nome legível ao lado
        if not pacote and titulo and "." in titulo and " " not in titulo:
            pacote = titulo
        linhas.append({
            "pacote": pacote,
            "nome": _txt(titulo) if titulo != pacote else None,
            "versao": _txt(linha.get("Version")),
            "descricao": None,
            "instalacao": _data_google(linha.get("First Installation Time")),
            "ultimo_uso": _data_google(linha.get("Last Update Time")),
        })
        if len(linhas) >= _BLOCO_GRAVACAO:
            yield linhas
            linhas = []
    if linhas:
        yield linhas


_RE_BLOCO_RESULTADO = re.compile(r"Result \{(.*?)\n\}", re.S)
_RE_ATRIBUTO_TEXTO = re.compile(r'name:\s*"([A-Z_]+)"[^}]*?string_value:\s*"([^"]*)"', re.S)
_RE_ATRIBUTO_LOCAL = re.compile(
    r'name:\s*"LOCATION"[^}]*?lat:\s*([-\d.]+)\s+lon:\s*([-\d.]+)', re.S)


def _unescape_protobuf(valor: str) -> str:
    """Decodifica os escapes octais do texto protobuf ("Te\303\263filo" -> "Teófilo").

    O protobuf-texto grava byte não-ASCII como escape octal (o byte UTF-8 em base 8); aqui cada
    escape vira o byte correspondente e o resultado é decodificado como UTF-8."""
    if not valor or "\\" not in valor:
        return valor
    saida = bytearray()
    i = 0
    n = len(valor)
    while i < n:
        if valor[i] == "\\" and i + 3 < n and all(c in "01234567" for c in valor[i + 1:i + 4]):
            saida.append(int(valor[i + 1:i + 4], 8))
            i += 4
            continue
        saida.extend(valor[i].encode("utf-8"))
        i += 1
    return bytes(saida).decode("utf-8", "replace")


def parse_saved_places_txt(texto: str) -> list[dict]:
    """SavedPlaces (tmp/geo-ugc-lists-*/zipit-raw-place-listsplaces.txt) do Google Warrant
    Return — locais salvos/rotulados no Maps em formato de texto protobuf. Cada bloco
    `Result { … }` traz TITLE, SINGLE_LINE_ADDRESS, LOCATION (lat/lon) e o
    creation_timestamp do metadata.

    Devolve dicts no formato de investigacao_utils._gravar_localizacao."""
    linhas = []
    for bloco in _RE_BLOCO_RESULTADO.findall(texto):
        atributos = {m.group(1): m.group(2) for m in _RE_ATRIBUTO_TEXTO.finditer(bloco)}
        m_local = _RE_ATRIBUTO_LOCAL.search(bloco)
        lat = lon = None
        if m_local:
            lat = _num_localizacao(m_local.group(1))
            lon = _num_localizacao(m_local.group(2))
        m_ts = re.search(r"creation_timestamp:\s*(\d+)", bloco)
        ts = _data_epoch_ms(int(m_ts.group(1)) // 1000 if m_ts else None) if m_ts else None
        titulo = _txt(_unescape_protobuf(atributos.get("TITLE") or ""))
        endereco = _txt(_unescape_protobuf(atributos.get("SINGLE_LINE_ADDRESS") or ""))
        if not (titulo or endereco) and lat is None and lon is None:
            continue
        linhas.append({
            "aplicativo": "Google (locais salvos)",
            "timestamp": ts,
            "latitude": lat,
            "longitude": lon,
            "endereco": endereco,
            "categoria": None,
            "descricao": titulo,
            "precisao": None,
            "elevacao": None,
            "tipo": None,
            "origem": None,
            "fim": None,
            "conta": None,
            "estado": None,
        })
    return linhas


# ---------------------------------------------------------------------------

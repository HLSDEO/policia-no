"""WhatsApp "parcial" — a mídia do WhatsApp de uma nuvem reconstruída como conversa.

Dois pacotes de nuvem trazem mídia do WhatsApp sem o texto das mensagens, e esta tela trata os
dois:

**iCloud** — o banco de conversas vem criptografado (ChatStorage.sqlite.enc), mas a mídia NÃO é
criptografada e vem organizada por JID: `/Media/<jid>/<dir>/<uuid>.<ext>`, com o mtime do tar
carregando a data/hora da mensagem original. A "conversa" de cada contato/grupo renasce.

**Google Drive** (backup do Takeout lido pelo IPED) — os arquivos moram em
``Drive Mobile Backups/wa_<numero>/`` com o nome canônico do WhatsApp
(``IMG-AAAAMMDD-WAnnnn.jpg``). Aqui o pacote NÃO tem o contato de cada mídia (banco .crypt14
criptografado; o metadado do Drive só tem md5/tamanho/data — ver funcoes/iped_wa_drive), então a
reconstrução é UMA conversa da CONTA em linha do tempo, datada pelo nome do arquivo — a data do
Drive (upload) é deixada de propósito fora desta tela por não ser a data da mensagem.
"""

import re
from funcoes.db_utils import get_connection
from funcoes.iped_wa_drive import conta_drive, nome_midia

# a mídia do WhatsApp fica nos tars com caminho interno começando por "/Media/<jid>/..."
_PREFIXO_MIDIA = "files/Archives/"
_TARS_WHATSAPP = ("Media.tar", "Media_1.tar", "Document.tar")

_RE_JID = re.compile(r"^/Media/(?P<jid>[^/]+)/")


def _numero_jid(jid: str) -> str:
    """Só os dígitos do JID — o número do contato (ou do criador, no caso de grupo)."""
    return re.sub(r"\D", "", jid)


_TIPOS_MIDIA = ("audio", "imagem", "video", "documento")


def _tipo_midia(linha: dict) -> str:
    """Categoria exibível da mídia (audio|imagem|video|documento), pelo MIME — como a tela
    renderiza — com fallback na categoria normalizada pela extensão (ufdr_parser)."""
    mime = (linha.get("tp_mime") or "").lower()
    if mime.startswith("audio/"):
        return "audio"
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("image/"):
        return "imagem"
    cat = (linha.get("no_categoria") or "").strip().lower()
    if cat in ("áudio", "audio"):
        return "audio"
    if cat in ("vídeo", "video"):
        return "video"
    if cat == "imagem":
        return "imagem"
    return "documento"


def _tipo_jid(jid: str) -> str:
    if "@g.us" in jid:
        return "grupo"
    if "@status" in jid or "@broadcast" in jid:
        return "status"
    return "individual"


def _mapa_nomes_contatos(id_extracao: int) -> dict[str, str]:
    """telefone (só dígitos) -> nome do contato, a partir da agenda (CTT_TB0x) da extração."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.no_contato, e.tx_valor
                  FROM investigacao.ctt_tb01_contatos c
                  JOIN investigacao.ctt_tb02_contato_entradas e ON e.id_contato = c.id_contato
                 WHERE c.id_extracao = %s
                   AND lower(e.tp_entrada) = 'phonenumber'
                   AND coalesce(btrim(e.tx_valor), '') <> ''
                """,
                (id_extracao,),
            )
            mapa: dict[str, str] = {}
            for nome, valor in cur.fetchall():
                if not nome:
                    continue
                digitos = re.sub(r"\D", "", valor or "")
                if digitos:
                    mapa.setdefault(digitos, nome)
            return mapa


def _casamento(nome_digitos: dict[str, str], numero: str) -> str | None:
    """Nome do contato para o número, por igualdade exata ou pelo sufixo (sem o DDI 55)."""
    if not numero:
        return None
    if numero in nome_digitos:
        return nome_digitos[numero]
    # tolera o código do país (55) a mais ou a menos e o 9 do celular
    alvo = numero[-10:] if len(numero) > 10 else numero
    for chave, nome in nome_digitos.items():
        if chave == numero or chave.endswith(numero) or numero.endswith(chave[-10:]):
            return nome
        if chave[-8:] == alvo[-8:] and len(chave[-8:]) >= 8:
            return nome
    return None


def _arquivos_whatsapp(id_extracao: int) -> list[dict]:
    """Os arquivos de mídia do WhatsApp da extração — iCloud (tars Media/Document com o JID no
    caminho interno) e backup do Google Drive (pasta ``wa_<numero>/``, nome canônico), com o JID
    já extraído (no Drive, o JID é o número da CONTA)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT a.id_arquivo, a.no_arquivo, a.tx_caminho, a.tp_mime, a.dt_modificacao,
                       a.co_caminho_zip, a.qt_bytes, a.no_categoria
                  FROM investigacao.arq_tb01_arquivos a
                 WHERE a.id_extracao = %s
                   AND a.co_caminho_zip IS NOT NULL
                   AND (   (a.co_caminho_zip LIKE 'files/Archives/Media%%::%%'
                            AND a.tx_caminho LIKE '/Media/%%@%%')
                        OR strpos(a.tx_caminho, 'Drive Mobile Backups/wa_') > 0)
                 ORDER BY a.dt_modificacao ASC NULLS LAST, a.id_arquivo
                """,
                (id_extracao,),
            )
            colunas = [d.name for d in cur.description]
            linhas = [dict(zip(colunas, linha)) for linha in cur.fetchall()]
    saida: list[dict] = []
    for linha in linhas:
        numero = conta_drive(linha["tx_caminho"])
        if numero is not None:
            midi = nome_midia(linha["no_arquivo"])
            if midi is None:
                continue  # _file_metadata.json, .crypt14 e afins: não são mídia
            # a data da MÍDIA é a do nome canônico do WhatsApp; a dt_modificacao do índice é a
            # data de SINCRONIZAÇÃO do Drive (medido: todos os uploads num mesmo dia de julho,
            # enquanto os nomes vão de julho a agosto) — mostrar a do upload embaralharia a
            # linha do tempo, então a da tela é a do nome
            _, dt, _seq = midi
            linha["dt_modificacao"] = dt
            linha["jid"] = f"{numero}@s.whatsapp.net"
        else:
            m = _RE_JID.match(linha["tx_caminho"] or "")
            linha["jid"] = m.group("jid") if m else None
        linha["tipo_jid"] = _tipo_jid(linha["jid"] or "")
        linha["tipo"] = _tipo_midia(linha)
        if linha["jid"]:
            saida.append(linha)
    return saida


def resolver_extracao_nuvem(id_inquerito: int, extracao: int | None) -> int:
    """Resolve o id_extracao para as telas de nuvem: usa o informado ou, sem ele, a extração de
    nuvem do inquérito. Levanta ValueError quando não há o que mostrar (a rota vira 404)."""
    if extracao is not None:
        return extracao
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT e.id_extracao FROM investigacao.inq_tb02_extracoes e
                    WHERE e.id_inquerito = %s AND coalesce(e.in_nuvem, false)
                    ORDER BY e.id_extracao DESC LIMIT 1""",
                (id_inquerito,),
            )
            row = cur.fetchone()
    if row is None:
        raise ValueError("Nenhuma extração de nuvem neste inquérito.")
    return row[0]


def listar_conversas_whatsapp_parcial(id_extracao: int) -> list[dict]:
    """As "conversas" reconstruídas: uma por JID, com nome (se houver na agenda), tipo, contagem
    (total e por categoria de mídia) e período coberto. Ordenadas pela mídia mais recente."""
    arquivos = _arquivos_whatsapp(id_extracao)
    nomes = _mapa_nomes_contatos(id_extracao)
    por_jid: dict[str, dict] = {}
    for a in arquivos:
        jid = a["jid"]
        c = por_jid.get(jid)
        if c is None:
            numero = _numero_jid(jid)
            c = {
                "jid": jid,
                "numero": numero,
                "tipo": a["tipo_jid"],
                "nome": _casamento(nomes, numero),
                "qt_midia": 0,
                "qt_por_tipo": {"audio": 0, "imagem": 0, "video": 0, "documento": 0},
                "primeira": a["dt_modificacao"],
                "ultima": a["dt_modificacao"],
            }
            por_jid[jid] = c
        c["qt_midia"] += 1
        c["qt_por_tipo"][a["tipo"]] += 1
        if a["dt_modificacao"]:
            if c["primeira"] is None or a["dt_modificacao"] < c["primeira"]:
                c["primeira"] = a["dt_modificacao"]
            if c["ultima"] is None or a["dt_modificacao"] > c["ultima"]:
                c["ultima"] = a["dt_modificacao"]
    conversas = list(por_jid.values())
    conversas.sort(key=lambda c: (c["ultima"] is None, c["ultima"] or ""), reverse=True)
    return conversas


def listar_midias_whatsapp_parcial(
    id_extracao: int, jid: str, offset: int = 0, limite: int = 20, tipo: str | None = None
) -> dict:
    """Uma janela da sequência cronológica de mídias de UM contato/grupo (a "conversa"
    reconstruída), opcionalmente filtrada por categoria (audio|imagem|video|documento).

    ``offset`` negativo conta do fim (``-20`` = as 20 mais recentes — como a tela abre a
    conversa); positivo, do início (o caso do "Carregar mais").

    Devolve {midias, total, total_geral, por_tipo, offset} — ``total`` vale para o conjunto
    FILTRADO (a paginação anda por ele), enquanto ``total_geral`` e ``por_tipo`` valem para a
    conversa inteira (os chips de filtro ficam estáveis ao filtrar, e voltar para "Todos" sempre
    mostra a contagem real)."""
    arquivos = _arquivos_whatsapp(id_extracao)
    midias = [a for a in arquivos if a["jid"] == jid]
    midias.sort(key=lambda a: (a["dt_modificacao"] is None, a["dt_modificacao"] or "", a["id_arquivo"]))
    por_tipo = {t: 0 for t in _TIPOS_MIDIA}
    for a in midias:
        por_tipo[a["tipo"]] += 1
    total_geral = len(midias)
    if tipo in _TIPOS_MIDIA:
        midias = [a for a in midias if a["tipo"] == tipo]
    total = len(midias)
    if offset < 0:
        offset = max(0, total + offset)
    offset = max(0, min(offset, total))
    pagina = midias[offset:offset + max(1, limite)]
    return {"midias": pagina, "total": total, "total_geral": total_geral, "por_tipo": por_tipo, "offset": offset}

"""Exercita as CORREÇÕES da rodada de bugs da Nuvem IPED contra o banco e os casos reais —
sem importar o caso (nada é renomeado nem movido); extração de teste é apagada no fim.

    docker compose run --rm -v ./api:/app api python /app/tools/teste_iped_correcoes.py

Cobre: A1 (mojibake de e-mail), A2 (bookmarks BitmapBookmarks), A4 (SSId), A5 (nome de app),
A6 (busca ufed:Value), A8 (contato Unknown), A3/B1 (anexo UFED pendurado na mensagem + ponteiro
de binário no anexo), B3 (filtro de marcados), B5 (timeline com arquivo_iped).
"""

import json
import subprocess
import sys
import traceback

sys.path.insert(0, "/app")

from funcoes import iped_utils as U  # noqa: E402
from funcoes import iped_marcadores  # noqa: E402
from funcoes import iped_dominios as D  # noqa: E402
from funcoes.db_utils import get_connection  # noqa: E402
from funcoes.investigacao_utils import (  # noqa: E402
    PASTA_DADOS,
    listar_arquivos,
    listar_timeline,
)

falhas: list[str] = []
passos = 0


def checar(rotulo, fn):
    global passos
    passos += 1
    try:
        resultado = fn()
        print(f"  ok   {rotulo}" + (f" -> {resultado}" if resultado is not None else ""))
        return resultado
    except Exception:
        falhas.append(rotulo)
        print(f"  ERRO {rotulo}")
        print("       " + "\n       ".join(traceback.format_exc().strip().splitlines()[-3:]))
        return None


NUVEM = PASTA_DADOS / "DELECIBER" / "nuvem_op_clean"
LAUDO = PASTA_DADOS / "DELECIBER" / "laudo_bahia"


# ---------------------------------------------------------------------------
# A2 — marcadores do perito (BitmapBookmarks do IPED 4.2.2)
# ---------------------------------------------------------------------------

print("\n[A2 · marcadores do perito]")
pasta_modulo_laudo = U._pasta_modulo_iped(LAUDO)


def _a2():
    marcadores = iped_marcadores.ler(pasta_modulo_laudo, U._classpath_dumper(pasta_modulo_laudo))
    if not marcadores:
        raise AssertionError("nenhum marcador lido (antes era ClassCastException)")
    return f"{marcadores.total} itens marcados, {len(marcadores.nomes)} marcadores"


checar("bookmarks.iped do laudo_bahia lidos (BitmapBookmarks)", _a2)


# ---------------------------------------------------------------------------
# A1 — corpo de e-mail com mojibake substituído pelo .eml decodificado
# ---------------------------------------------------------------------------

print("\n[A1 · mojibake de e-mail]")


def _a1():
    pasta_modulo = U._pasta_modulo_iped(NUVEM)
    alvo = None
    proc = subprocess.Popen(
        [U._JAVA_BIN, "-cp", U._classpath_dumper(pasta_modulo), "IpedDumper", str(NUVEM)],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, encoding="utf-8", errors="replace",
    )
    try:
        for linha in proc.stdout:
            linha = linha.strip()
            if not linha:
                continue
            try:
                campos = json.loads(linha).get("fields") or {}
            except ValueError:
                continue
            cats = U._valores(campos, "category")
            corpo = U._valor(campos, "Message-Body")
            if "Emails" in cats and corpo and U._parece_mojibake(corpo):
                alvo = campos
                break
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    if alvo is None:
        raise AssertionError("nenhum e-mail com mojibake encontrado no caso de nuvem")
    email = U._email_de_item(alvo, pasta_modulo)
    if email is None:
        raise AssertionError("e-mail não convertido")
    if U._parece_mojibake(email["corpo"]):
        raise AssertionError("corpo continua com mojibake depois do desvio")
    return "corpo decodificado do .eml sem mojibake"


checar("corpo do e-mail sem mojibake (lido do blob)", _a1)


# ---------------------------------------------------------------------------
# A4/A5/A6/A8 — conversores de domínio (itens sintéticos no formato do índice)
# ---------------------------------------------------------------------------

print("\n[A4–A8 · conversores]")


def _a4():
    linha = D.rede_de_item({"ufed:SSId": "BIGORU-CLIENTES", "ufed:Password": "x", "deleted": "false"})
    if linha["ssid"] != "BIGORU-CLIENTES":
        raise AssertionError(f"ssid errado: {linha['ssid']!r}")
    return linha["ssid"]


def _a5():
    linha = D.aplicativo_de_item({"ufed:Identifier": "net.whatsapp.WhatsApp",
                                  "name": "AppsUsageLog_uuid", "deleted": "false"})
    if linha["nome"] != "net.whatsapp.WhatsApp":
        raise AssertionError(f"nome errado: {linha['nome']!r}")
    return linha["nome"]


def _a6():
    linha = D.busca_de_item({"ufed:Value": "cookies chocolate", "ufed:Source": "Instagram",
                             "ufed:Account": "60046198415", "deleted": "false"})
    if linha is None or linha["termo"] != "cookies chocolate":
        raise AssertionError(f"termo errado: {linha and linha['termo']!r}")
    return linha["termo"]


def _a8():
    linha = D.contato_de_item({"name": "Contact-Unknown-[01573991224014]",
                               "ufed:Source": "Recents", "deleted": "false"})
    if linha is None:
        raise AssertionError("contato Unknown descartado")
    if not any(e["valor"] == "01573991224014" for e in linha["entradas"]):
        raise AssertionError(f"telefone não extraído: {linha['entradas']!r}")
    return linha["entradas"][0]["valor"]


checar("SSID lido de ufed:SSId (A4)", _a4)
checar("nome de app vem de ufed:Identifier (A5)", _a5)
checar("termo de busca vem de ufed:Value (A6)", _a6)
checar("contato Unknown tem o número extraído do name (A8)", _a8)

# classificar: as novas categorias/MIMEs
_rotas = {
    "application/x-ufed-searcheditem": "busca",
    "application/x-ufed-socialmediaactivity": "social",
    "application/x-ufed-deviceconnectivity": "conectividade",
    "application/x-ufed-cookie": "cookie",
    "application/x-ufed-voicemail": "voicemail",
    "application/x-ufed-recording": "gravacao",
}


def _classificacoes():
    erradas = []
    for mime, esperado in _rotas.items():
        if D.classificar({"contentType": mime}) != esperado:
            erradas.append(mime)
    if D.classificar({"category": "Device Events"}) != "ignorado":
        erradas.append("Device Events")
    if D.classificar({"category": "Financial Account"}) != "financeiro":
        erradas.append("Financial Account singular")
    if erradas:
        raise AssertionError(f"classificação errada para: {erradas}")
    return f"{len(_rotas) + 2} rotas novas ok"


checar("classificar() roteia os domínios UFDR restantes", _classificacoes)


# ---------------------------------------------------------------------------
# A3 + B1 — anexo UFED pendurado na mensagem; anexo ganha o ponteiro do binário
# ---------------------------------------------------------------------------

print("\n[A3+B1 · anexos de mensagem]")


def _a3b1():
    roteador = D.Roteador(999)
    # chat (WhatsAppParser, com participantes)
    roteador.consumir(None, {"id": "10", "contentType": "application/x-whatsapp-chat",
                             "category": "WhatsApp", "name": "Chat",
                             "html:chatId": "42",
                             "Communication:Participants": "Fulano (557399087401)",
                             "deleted": "false"})
    # mensagem
    roteador.consumir(None, {"id": "20", "contentType": "message/x-whatsapp-message",
                             "category": "Instant Messages", "parentId": "10",
                             "Message-Body": "oi", "Communication:From": "Fulano (557399087401)",
                             "Communication:To": ". (557399087401)",
                             "Communication:Date": "2025-01-01T00:00:00Z", "deleted": "false"})
    # anexo UFDR (filho da MENSAGEM, não do chat) — antes virava mensagem órfã em conversa sintética
    roteador.consumir(None, {"id": "30", "contentType": "application/x-ufed-attachment",
                             "category": "Message Attachments", "parentId": "20",
                             "ufed:Title": "legenda", "ufed:attachment_extracted_path": "files/Image/x.jpg",
                             "deleted": "false"})
    # binário do caso por sha-256 (o que o linkedItems da mensagem aponta)
    roteador.registrar_binario("DBA486427C915DF08B586C7711BAB2E9FDB763AB216D7A7C0E51F1E0CB5AE1A8",
                               "IPED::999::iped/storage/storage-13.db::DBA4::0")
    # mensagem com mídia apontando o mesmo sha-256
    roteador.consumir(None, {"id": "21", "contentType": "message/x-whatsapp-attachment",
                             "category": "Instant Messages", "parentId": "10",
                             "Message-Body": "! IMAGE_MESSAGE",
                             "mediaName": "Media/x.jpg", "mediaMime": "image/jpeg", "mediaSize": "123",
                             "linkedItems": "sha-256:DBA486427C915DF08B586C7711BAB2E9FDB763AB216D7A7C0E51F1E0CB5AE1A8",
                             "Communication:From": "Fulano (557399087401)",
                             "Communication:To": ". (557399087401)",
                             "Communication:Date": "2025-01-02T00:00:00Z", "deleted": "false"})

    if roteador._anexos_de_mensagem.get("20") is None:
        raise AssertionError("anexo UFDR não foi pendurado na mensagem pai")
    if roteador._ids_de_mensagem != {"20", "21"}:
        raise AssertionError(f"ids de mensagem errados: {roteador._ids_de_mensagem}")
    # a conversa sintética de órfãs não deve existir para o anexo UFDR
    pais_esperados = {"10"}
    pais = set(roteador._buffer.pais())
    if not pais_esperados.issubset(pais) or "20" in pais or "30" in pais:
        raise AssertionError(f"pais do buffer errados: {pais}")

    # grava de verdade numa extração descartável e confere o resultado no banco
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO investigacao.inq_tb01_inqueritos (nu_inquerito) "
                        "VALUES ('TESTE-IPED-ANEXO') RETURNING id_inquerito")
            id_inq = cur.fetchone()[0]
            cur.execute(
                """INSERT INTO investigacao.inq_tb02_extracoes
                       (id_inquerito, no_arquivo, no_fabricante, cd_status)
                   VALUES (%s, 'teste', 'IPED', 'concluido') RETURNING id_extracao""",
                (id_inq,))
            id_ext = cur.fetchone()[0]
        conn.commit()
        roteador.id_extracao = id_ext
        try:
            with conn.cursor() as cur:
                contagens = roteador.finalizar(cur, commit=conn.commit)
            if contagens.get("conversas") != 1:
                raise AssertionError(f"conversas: {contagens.get('conversas')}")
            # 2 mensagens: o "oi" + a mensagem de mídia. O anexo UFDR NÃO vira mais mensagem
            # órfã — fica pendurado na mensagem pai (antes eram 3 mensagens numa conversa
            # sintética separada)
            if contagens.get("mensagens") != 2:
                raise AssertionError(f"mensagens: {contagens.get('mensagens')}")
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT count(*) AS anexos,
                              count(*) FILTER (WHERE co_caminho_zip LIKE 'IPED::%%') AS com_ponteiro,
                              count(*) FILTER (WHERE tx_titulo = 'legenda') AS ufed_na_mensagem
                       FROM investigacao.msg_tb05_anexos a
                       JOIN investigacao.msg_tb04_mensagens m ON m.id_mensagem = a.id_mensagem
                       JOIN investigacao.msg_tb02_conversas c ON c.id_conversa = m.id_conversa
                       WHERE c.id_extracao = %s""",
                    (id_ext,))
                linha = dict(zip([d.name for d in cur.description], cur.fetchone()))
            if linha["anexos"] != 2:
                raise AssertionError(f"anexos no banco: {linha}")
            if linha["com_ponteiro"] != 1:
                raise AssertionError(f"anexo sem ponteiro IPED: {linha}")
            if linha["ufed_na_mensagem"] != 1:
                raise AssertionError(f"anexo UFDR não ficou na mensagem pai: {linha}")
            return linha
        finally:
            roteador.fechar()
            with conn.cursor() as cur:
                U._apagar_dados_extracao(cur, id_ext)
                cur.execute("DELETE FROM investigacao.inq_tb02_extracoes WHERE id_extracao = %s",
                            (id_ext,))
                cur.execute("DELETE FROM investigacao.inq_tb01_inqueritos WHERE id_inquerito = %s",
                            (id_inq,))
            conn.commit()


checar("anexo UFDR pendurado na mensagem pai + ponteiro IPED no anexo", _a3b1)


# ---------------------------------------------------------------------------
# B3 — filtro "marcados" na listagem de arquivos
# ---------------------------------------------------------------------------

print("\n[B3 · filtro de marcados]")


def _b3():
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO investigacao.inq_tb01_inqueritos (nu_inquerito) "
                        "VALUES ('TESTE-IPED-MARCADOS') RETURNING id_inquerito")
            id_inq = cur.fetchone()[0]
            cur.execute(
                """INSERT INTO investigacao.inq_tb02_extracoes
                       (id_inquerito, no_arquivo, no_fabricante, cd_status)
                   VALUES (%s, 'teste', 'IPED', 'concluido') RETURNING id_extracao""",
                (id_inq,))
            id_ext = cur.fetchone()[0]
            cur.execute(
                """INSERT INTO investigacao.arq_tb01_arquivos
                       (id_extracao, no_arquivo, no_categoria, tx_marcadores)
                   VALUES (%s, 'a.jpg', 'Imagem', 'Provavelmente compartilhados'),
                          (%s, 'b.jpg', 'Imagem', NULL),
                          (%s, 'c.jpg', 'Imagem', NULL)""",
                (id_ext, id_ext, id_ext))
            cur.execute("UPDATE investigacao.arq_tb01_arquivos SET in_selecionado = true "
                        "WHERE id_extracao = %s AND no_arquivo = 'c.jpg'", (id_ext,))
        conn.commit()
        try:
            resp = listar_arquivos(id_inq, id_extracao=id_ext, marcados=True)
            nomes = sorted(i["no_arquivo"] for i in resp["itens"])
            if nomes != ["a.jpg", "c.jpg"]:
                raise AssertionError(f"filtro marcados errado: {nomes}")
            if not all(i.get("marcadores_perito") or i.get("selecionado_perito") for i in resp["itens"]):
                raise AssertionError("colunas de marcador não vieram na resposta")
            return f"{resp['total']} marcados de 3"
        finally:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM investigacao.arq_tb01_arquivos WHERE id_extracao = %s",
                            (id_ext,))
                cur.execute("DELETE FROM investigacao.inq_tb02_extracoes WHERE id_extracao = %s",
                            (id_ext,))
                cur.execute("DELETE FROM investigacao.inq_tb01_inqueritos WHERE id_inquerito = %s",
                            (id_inq,))
            conn.commit()


checar("listar_arquivos filtra e devolve marcadores do perito", _b3)


# ---------------------------------------------------------------------------
# B5 — timeline consome TX_LINHA_TEMPO (arquivo_iped)
# ---------------------------------------------------------------------------

print("\n[B5 · timeline com arquivo_iped]")


def _b5():
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO investigacao.inq_tb01_inqueritos (nu_inquerito) "
                        "VALUES ('TESTE-IPED-TIMELINE') RETURNING id_inquerito")
            id_inq = cur.fetchone()[0]
            cur.execute(
                """INSERT INTO investigacao.inq_tb02_extracoes
                       (id_inquerito, no_arquivo, no_fabricante, cd_status)
                   VALUES (%s, 'teste', 'IPED', 'concluido') RETURNING id_extracao""",
                (id_inq,))
            id_ext = cur.fetchone()[0]
            cur.execute(
                """INSERT INTO investigacao.arq_tb01_arquivos
                       (id_extracao, no_arquivo, no_categoria, tx_linha_tempo)
                   VALUES (%s, 'foto.jpg', 'Imagem',
                           '{"modified": "2025-03-01T10:00:00Z", "created": "2025-02-01T10:00:00Z"}'),
                          (%s, 'lixo.bin', 'Outro',
                           '{"modified": "2025-03-01T10:00:00Z"}')""",
                (id_ext, id_ext))
        conn.commit()
        try:
            resp = listar_timeline(id_inq, id_extracao=id_ext)
            eventos = resp["eventos"]
            if not any(e["tp_categoria"] == "arquivo_iped" for e in eventos):
                raise AssertionError(f"nenhum arquivo_iped na timeline: {[e['tp_categoria'] for e in eventos][:5]}")
            if any("lixo.bin" in (e["tx_descricao"] or "") for e in eventos):
                raise AssertionError("categoria 'Outro' entrou na timeline (deveria ficar de fora)")
            if not any("foto.jpg" in (e["tx_descricao"] or "") for e in eventos):
                raise AssertionError("evento do arquivo de Imagem não apareceu")
            return f"{sum(1 for e in eventos if e['tp_categoria'] == 'arquivo_iped')} eventos de arquivo"
        finally:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM investigacao.arq_tb01_arquivos WHERE id_extracao = %s",
                            (id_ext,))
                cur.execute("DELETE FROM investigacao.inq_tb02_extracoes WHERE id_extracao = %s",
                            (id_ext,))
                cur.execute("DELETE FROM investigacao.inq_tb01_inqueritos WHERE id_inquerito = %s",
                            (id_inq,))
            conn.commit()


checar("timeline inclui arquivo_iped (só categorias de conteúdo)", _b5)

print(f"\n{passos - len(falhas)}/{passos} passos ok")
if falhas:
    print("FALHARAM: " + ", ".join(falhas))
sys.exit(1 if falhas else 0)

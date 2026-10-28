"""Exercita contra o banco REAL todas as consultas da Nuvem IPED enriquecida e da tela
"Nuvem × aparelho", sem depender de haver um caso do IPED importado.

Por que existe: as consultas de `correlacao_nuvem_utils.py` são montadas por concatenação (o eixo
do cruzamento, o `EXISTS` das três famílias de conversa, a ordenação) — são 12 combinações de
critério × ordenação, e um erro de coluna ambígua ou de tipo só aparece quando o Postgres analisa
o texto final. Um parser de SQL não pega isso; o banco pega.

O truque para não precisar de dado: cada consulta roda dentro de uma transação que é revertida no
fim, e as que só existem com dado (a paginação, as lacunas) recebem uma extração de nuvem e uma de
aparelho criadas na hora, com dois arquivos plantados. Nada sobra no banco.

    docker compose run --rm -v ./api:/app api python /app/tools/teste_correlacao_nuvem.py
"""

import sys
import traceback

sys.path.insert(0, "/app")

from funcoes import correlacao_nuvem_utils as C  # noqa: E402
from funcoes.db_utils import get_connection  # noqa: E402

falhas: list[str] = []
passos = 0


def checar(rotulo, fn):
    """Roda um passo, conta e registra a falha em vez de abortar — interessa a lista inteira de
    problemas numa passada, não o primeiro."""
    global passos
    passos += 1
    try:
        resultado = fn()
        print(f"  ok   {rotulo}" + (f" -> {resultado}" if resultado is not None else ""))
    except Exception:
        falhas.append(rotulo)
        print(f"  ERRO {rotulo}")
        print("       " + "\n       ".join(traceback.format_exc().strip().splitlines()[-3:]))


# ---------------------------------------------------------------------------
# 1. As consultas que não precisam de dado nenhum
# ---------------------------------------------------------------------------

print("\n[1] Consultas sem dado (inquérito inexistente: valida o SQL, não o resultado)")

checar("listar_fontes", lambda: C.listar_fontes(-1))
checar("resumo", lambda: C.resumo(-1))
checar("correlacionar_arquivos (sem extrações -> curto-circuito)",
       lambda: C.correlacionar_arquivos(-1)["total"])


# ---------------------------------------------------------------------------
# 2. As 12 combinações de critério × ordenação, com extrações de mentira
# ---------------------------------------------------------------------------

print("\n[2] Cruzamento completo com dado plantado (revertido no fim)")


def _plantar(cur) -> tuple[int, int, int]:
    """Cria inquérito + uma extração de nuvem + uma de aparelho, com mídia de WhatsApp que
    reproduz os três estados e uma lacuna de sequência. Devolve (inquérito, nuvem, aparelho)."""
    cur.execute(
        "INSERT INTO investigacao.inq_tb01_inqueritos (nu_inquerito) VALUES ('TESTE-CORRELACAO') "
        "RETURNING id_inquerito")
    id_inquerito = cur.fetchone()[0]

    def extracao(fabricante, nome):
        cur.execute(
            """INSERT INTO investigacao.inq_tb02_extracoes
                   (id_inquerito, no_arquivo, no_dispositivo, no_fabricante, cd_status)
               VALUES (%s, %s, %s, %s, 'concluido') RETURNING id_extracao""",
            (id_inquerito, nome, nome, fabricante))
        return cur.fetchone()[0]

    nuvem = extracao("IPED", "conta@exemplo.com")
    aparelho = extracao("samsung", "celular.ufdr")

    def arquivo(id_extracao, nome, categoria="Imagem", status="Intact", md5=None, bytes_=1_000_000):
        cur.execute(
            """INSERT INTO investigacao.arq_tb01_arquivos
                   (id_extracao, no_arquivo, tx_caminho, no_categoria, tp_mime, qt_bytes,
                    cd_status_arquivo, co_hash_md5, co_caminho_zip)
               VALUES (%s, %s, %s, %s, 'image/jpeg', %s, %s, %s, 'IPED::x::y::z::0')
               RETURNING id_arquivo""",
            (id_extracao, nome, "/pasta/" + nome, categoria, bytes_, status, md5))
        return cur.fetchone()[0]

    # na nuvem: três que somem do aparelho, sendo WA0042 um buraco entre WA0041 e WA0043
    arquivo(nuvem, "IMG-20230715-WA0041.jpg", md5="a" * 32)
    arquivo(nuvem, "IMG-20230715-WA0042.jpg", md5="b" * 32)
    arquivo(nuvem, "IMG-20230715-WA0043.jpg", md5="c" * 32)
    arquivo(nuvem, "IMG-20230715-WA0044.jpg", md5="d" * 32)
    # no aparelho: 41 íntegro (não deve aparecer), 43 carveado (recuperado)
    arquivo(aparelho, "IMG-20230715-WA0041.jpg", md5="a" * 32)
    arquivo(aparelho, "IMG-20230715-WA0043.jpg", status="Deleted", md5="c" * 32)

    # sidecar do Google Fotos para o WA0042, com origem de aparelho (testa so_do_aparelho)
    cur.execute(
        """INSERT INTO investigacao.nuv_tb01_metadados_arquivo
               (id_extracao, no_arquivo_alvo, no_fonte, dt_captura, tp_origem_upload,
                no_dispositivo_upload, no_pasta_dispositivo)
           VALUES (%s, 'img-20230715-wa0042.jpg', 'Google Photos', now(),
                   'Upload de aparelho', 'ANDROID_PHONE', 'WhatsApp Images')""",
        (nuvem,))

    # conversa que carregou o WA0044 e cujo binário sumiu do aparelho -> estado 'apagado'
    cur.execute(
        "INSERT INTO investigacao.wpp_tb02_conversas (id_extracao, nu_whatsapp_chat_id, tp_conversa)"
        " VALUES (%s, 'chat-1', 'individual') RETURNING id_conversa", (aparelho,))
    id_conversa = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO investigacao.wpp_tb04_mensagens (id_conversa, tx_mensagem, dt_mensagem, in_enviada)"
        " VALUES (%s, 'olha isso', now(), false) RETURNING id_mensagem", (id_conversa,))
    id_mensagem = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO investigacao.wpp_tb05_anexos (id_mensagem, no_arquivo, tp_conteudo)"
        " VALUES (%s, 'IMG-20230715-WA0044.jpg', 'image/jpeg')", (id_mensagem,))

    return id_inquerito, nuvem, aparelho


with get_connection() as conn:
    with conn.cursor() as cur:
        id_inquerito, nuvem, aparelho = _plantar(cur)
    conn.commit()

    try:
        checar("listar_fontes (com dado)", lambda: {
            "nuvem": len(C.listar_fontes(id_inquerito)["nuvem"]),
            "aparelhos": len(C.listar_fontes(id_inquerito)["aparelhos"]),
            "midia_wa_no_aparelho": C.listar_fontes(id_inquerito)["aparelhos"][0]["qt_midia_whatsapp"],
            "com_sidecar": C.listar_fontes(id_inquerito)["nuvem"][0]["qt_com_sidecar"],
        })
        checar("resumo (com dado)", lambda: C.resumo(id_inquerito))

        for criterio in ("nome", "hash", "tamanho"):
            for ordenar in ("relevancia", "tamanho", "data", "nome"):
                checar(
                    f"correlacionar criterio={criterio} ordenar={ordenar}",
                    lambda c=criterio, o=ordenar: C.correlacionar_arquivos(
                        id_inquerito, criterio=c, ordenar=o)["total"])

        for estado in (None, "apagado", "recuperado", "so_nuvem"):
            checar(f"filtro estado={estado}",
                   lambda e=estado: [i["no_arquivo"] for i in C.correlacionar_arquivos(
                       id_inquerito, estado=e)["itens"]])

        checar("filtro so_do_aparelho=True",
               lambda: [i["no_arquivo"] for i in C.correlacionar_arquivos(
                   id_inquerito, so_do_aparelho=True)["itens"]])
        checar("filtro so_whatsapp=False + categoria + busca",
               lambda: C.correlacionar_arquivos(
                   id_inquerito, so_whatsapp=False, categoria="Imagem", busca="WA00")["total"])
        checar("paginação (limite=1, página 2)",
               lambda: C.correlacionar_arquivos(id_inquerito, limite=1, pagina=2)["pagina"])

        # o que a tela promete: WA0042 é buraco entre WA0041 e WA0043, WA0044 não é (nada depois)
        def _lacunas():
            itens = C.correlacionar_arquivos(id_inquerito)["itens"]
            return {i["no_arquivo"]: i["in_lacuna"] for i in itens}
        checar("lacuna de sequência", _lacunas)

    finally:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM investigacao.wpp_tb05_anexos WHERE id_mensagem IN "
                        "(SELECT id_mensagem FROM investigacao.wpp_tb04_mensagens m "
                        " JOIN investigacao.wpp_tb02_conversas c ON c.id_conversa = m.id_conversa "
                        " WHERE c.id_extracao = %s)", (aparelho,))
            cur.execute("DELETE FROM investigacao.wpp_tb04_mensagens WHERE id_conversa IN "
                        "(SELECT id_conversa FROM investigacao.wpp_tb02_conversas WHERE id_extracao = %s)",
                        (aparelho,))
            cur.execute("DELETE FROM investigacao.wpp_tb02_conversas WHERE id_extracao = %s", (aparelho,))
            cur.execute("DELETE FROM investigacao.nuv_tb01_metadados_arquivo WHERE id_extracao = %s", (nuvem,))
            cur.execute("DELETE FROM investigacao.arq_tb01_arquivos WHERE id_extracao = ANY(%s)",
                        ([nuvem, aparelho],))
            cur.execute("DELETE FROM investigacao.inq_tb02_extracoes WHERE id_inquerito = %s", (id_inquerito,))
            cur.execute("DELETE FROM investigacao.inq_tb01_inqueritos WHERE id_inquerito = %s", (id_inquerito,))
        conn.commit()
        print("  (dado de teste removido)")


print(f"\n{passos - len(falhas)}/{passos} passos ok")
if falhas:
    print("FALHARAM: " + ", ".join(falhas))
sys.exit(1 if falhas else 0)

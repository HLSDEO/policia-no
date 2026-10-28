"""Testes de integração da ingestão UFDR — rodam contra o Postgres de verdade.

**DESFAZEM TUDO no final** (`rollback`): nenhuma linha sobra no banco.

1. **Lote de mensagens/anexos.** `_gravar_mensagens_em_lote` trocou um `INSERT ... RETURNING` por
   mensagem por `executemany(..., returning=True)`, e o que precisa ficar provado é que cada id
   volta casado com a SUA mensagem — anexo pendurado na mensagem errada é a foto de uma pessoa
   aparecendo na conversa de outra, e passaria despercebido.
2. **Recusa de .ufdr duplicado.** `_recusar_se_duplicada` barra a reimportação do mesmo arquivo
   no mesmo inquérito. Um teste ruim aqui bloquearia importação legítima, então as três
   permissões também são cobradas: outro inquérito pode, extração que deu erro pode, e a mais
   antiga das duas concorrentes segue.

    docker compose exec api-1 python /app/tools/teste_ingestao_ufdr.py
"""
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from funcoes.db_utils import get_connection  # noqa: E402
from funcoes.investigacao_utils import (  # noqa: E402
    ExtracaoDuplicada,
    _gravar_conversa,
    _gravar_conversa_msg,
    _recusar_se_duplicada,
)
from funcoes.ufdr_parser import (  # noqa: E402
    indexar_indicadores,
    iterar_registros,
    ler_info_dispositivo,
)

NS = "http://pa.cellebrite.com/report/2.0"
CAMINHO = "/tmp/teste_lote.ufdr"
N_CHATS = 3
N_MSGS = 1200  # > _LOTE_MENSAGENS (500), para exercitar mais de um lote por conversa


def montar_ufdr():
    """.ufdr sintético: cada mensagem carrega no corpo e no anexo a sua própria identidade,
    para o teste conseguir afirmar que o anexo N ficou na mensagem N."""
    partes = [f'<project xmlns="{NS}" id="p1">',
              '<metadata section="Extraction Data">',
              '<item name="DeviceInfoSelectedManufacturer">Apple</item>',
              '<item name="LastUsedMSISDN">5527999991234</item>',
              '</metadata>', '<decodedData>']
    for c in range(N_CHATS):
        fonte = "WhatsApp" if c == 0 else "Telegram"
        partes.append(f'<model type="Chat" id="c{c}" deleted_state="Intact">')
        partes.append(f'<field name="Source"><value type="String">{fonte}</value></field>')
        partes.append(f'<field name="Id"><value type="String">grupo{c}@g.us</value></field>')
        partes.append('<multiModelField name="Participants">'
                      f'<model type="Party" id="pt{c}">'
                      '<field name="Identifier"><value type="String">5527999991234@s.whatsapp.net</value></field>'
                      '</model></multiModelField>')
        partes.append('<multiModelField name="Messages">')
        for m in range(N_MSGS):
            partes.append(f'<model type="InstantMessage" id="m{c}_{m}">')
            partes.append(f'<field name="Body"><value type="String">corpo-{c}-{m}</value></field>')
            partes.append('<multiModelField name="Attachments">'
                          '<model type="Attachment">'
                          f'<field name="Filename"><value type="String">anexo-{c}-{m}.jpg</value></field>'
                          '</model></multiModelField>')
            partes.append('</model>')
        partes.append('</multiModelField></model>')
    partes.append('</decodedData></project>')

    with zipfile.ZipFile(CAMINHO, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("report.xml", "".join(partes))


def main():
    montar_ufdr()
    print("info:", ler_info_dispositivo(CAMINHO))

    with zipfile.ZipFile(CAMINHO) as zf:
        indice, fotos, caminhos, contagem = indexar_indicadores(zf=zf)
        chats = [dados for tipo, dados in iterar_registros(
            indice_binarios=indice, caminhos_zip=caminhos,
            contagem_tamanhos=contagem, zf=zf) if tipo == "chat"]

    assert len(chats) == N_CHATS, f"esperava {N_CHATS} conversas, veio {len(chats)}"
    for _fonte, chat in chats:
        assert len(chat["mensagens"]) == N_MSGS, len(chat["mensagens"])

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO investigacao.inq_tb01_inqueritos (nu_inquerito) VALUES (%s) "
                "RETURNING id_inquerito", ("TESTE-LOTE-ROLLBACK",))
            id_inq = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO investigacao.inq_tb02_extracoes (id_inquerito, no_arquivo, cd_status) "
                "VALUES (%s, %s, 'processando') RETURNING id_extracao",
                (id_inq, "teste_lote.ufdr"))
            id_ext = cur.fetchone()[0]

            for fonte, chat in chats:
                if fonte == "WhatsApp":
                    _gravar_conversa(cur, "wpp", id_ext, chat, fotos,
                                     jid_dono="5527999991234@s.whatsapp.net")
                else:
                    _gravar_conversa_msg(cur, id_ext, fonte, chat)

            # a prova: o nome do anexo tem que corresponder ao corpo da SUA mensagem
            erros = 0
            gravadas = 0
            for tabela_msg, tabela_anexo, tabela_conv in (
                ("wpp_tb04_mensagens", "wpp_tb05_anexos", "wpp_tb02_conversas"),
                ("msg_tb04_mensagens", "msg_tb05_anexos", "msg_tb02_conversas"),
            ):
                cur.execute(
                    f"""
                    SELECT m.tx_mensagem, a.no_arquivo
                      FROM investigacao.{tabela_msg} m
                      JOIN investigacao.{tabela_conv} c ON c.id_conversa = m.id_conversa
                      LEFT JOIN investigacao.{tabela_anexo} a ON a.id_mensagem = m.id_mensagem
                     WHERE c.id_extracao = %s
                    """,
                    (id_ext,),
                )
                for corpo, no_arquivo in cur.fetchall():
                    gravadas += 1
                    esperado = corpo.replace("corpo-", "anexo-") + ".jpg"
                    if no_arquivo != esperado:
                        erros += 1
                        if erros <= 5:
                            print(f"  DESCASADO: mensagem {corpo!r} -> anexo {no_arquivo!r} "
                                  f"(esperado {esperado!r})")

            print(f"mensagens gravadas: {gravadas} (esperado {N_CHATS * N_MSGS})")
            print(f"anexos descasados: {erros}")
            assert gravadas == N_CHATS * N_MSGS, "faltou ou sobrou mensagem"
            assert erros == 0, "anexo ficou na mensagem errada"

        conn.rollback()
        print("rollback feito — nada persistiu")


SHA_FALSO = "0" * 63 + "1"  # não colide com hash de arquivo real nenhum


def testar_duplicidade():
    """Recusa de .ufdr repetido no mesmo inquérito, e as três situações em que NÃO deve recusar.

    Este bloco COMMITA as linhas de apoio (`_recusar_se_duplicada` pega a sua própria conexão do
    pool e não enxergaria uma transação aberta aqui) e as apaga no `finally`. Os nomes começam
    com TESTE- para que qualquer sobra de uma execução interrompida seja óbvia."""
    ids = {}
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                for rotulo in ("A", "B"):
                    cur.execute(
                        "INSERT INTO investigacao.inq_tb01_inqueritos (nu_inquerito) VALUES (%s) "
                        "RETURNING id_inquerito", (f"TESTE-DUPLICIDADE-{rotulo}-{os.getpid()}",))
                    ids[f"inq{rotulo}"] = cur.fetchone()[0]

                def nova(inq, status, sha=SHA_FALSO):
                    cur.execute(
                        "INSERT INTO investigacao.inq_tb02_extracoes "
                        "(id_inquerito, no_arquivo, cd_status, co_hash_sha256) "
                        "VALUES (%s, %s, %s, %s) RETURNING id_extracao",
                        (inq, "teste_dup.ufdr", status, sha))
                    return cur.fetchone()[0]

                original = nova(ids["inqA"], "concluido")
                repetida = nova(ids["inqA"], "processando")
                outro_inq = nova(ids["inqB"], "processando")
                falhada = nova(ids["inqA"], "erro", sha="f" * 64)
                depois_de_falha = nova(ids["inqA"], "processando", sha="f" * 64)
                ids["extracoes"] = [original, repetida, outro_inq, falhada, depois_de_falha]
            conn.commit()

        casos = [
            ("mesmo arquivo, mesmo inquérito", repetida, True),
            ("mesmo arquivo, OUTRO inquérito", outro_inq, False),
            ("a original já concluída", original, False),
            ("reimportação depois de erro", depois_de_falha, False),
        ]
        for rotulo, id_ext, deve_recusar in casos:
            sha = "f" * 64 if id_ext == depois_de_falha else SHA_FALSO
            try:
                _recusar_se_duplicada(id_ext, {"sha256": sha})
                recusou = False
            except ExtracaoDuplicada as exc:
                recusou = True
                detalhe = str(exc)
            print(f"  {rotulo}: {'RECUSA' if recusou else 'permite'} "
                  f"(esperado {'RECUSA' if deve_recusar else 'permite'})")
            assert recusou == deve_recusar, rotulo
            if recusou:
                assert f"#{original}" in detalhe, f"a mensagem tem de apontar a original: {detalhe}"
    finally:
        with get_connection() as conn:
            with conn.cursor() as cur:
                if ids.get("extracoes"):
                    cur.execute("DELETE FROM investigacao.inq_tb02_extracoes "
                                "WHERE id_extracao = ANY(%s)", (ids["extracoes"],))
                for chave in ("inqA", "inqB"):
                    if ids.get(chave):
                        cur.execute("DELETE FROM investigacao.inq_tb01_inqueritos "
                                    "WHERE id_inquerito = %s", (ids[chave],))
            conn.commit()
        print("linhas de apoio da duplicidade apagadas")


if __name__ == "__main__":
    print("== lote de mensagens/anexos ==")
    main()
    print("== recusa de .ufdr duplicado ==")
    testar_duplicidade()
    print("OK")

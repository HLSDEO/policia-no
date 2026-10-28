"""Teste do parser UFDR contra um .ufdr sintético — sem banco, roda em qualquer lugar.

Cobre o que não dá para verificar olhando um .ufdr real (que é grande, sigiloso e não cabe no
repositório) e o que quebraria em silêncio:

- **memória**: o iterparse do ElementTree monta a árvore inteira enquanto lê, e `elem.clear()`
  não tira o registro da lista de filhos do pai. O teste mede o pico com e sem a limpeza do pai
  (`_PilhaConteineres`) e cobra a diferença;
- **cobertura**: todo `<model>` de topo e todo `<file>` têm de sair do passe, inclusive com os
  registros dentro de contêineres aninhados (que é como o report.xml de verdade é);
- **casamento de anexo por tamanho**: as três situações que decidem se o investigador vê o
  binário certo, o binário errado ou nenhum.

    docker compose exec api-1 python /app/tools/teste_parser_ufdr.py
"""
import os
import sys
import tracemalloc
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from funcoes import ufdr_parser as up  # noqa: E402

NS = "http://pa.cellebrite.com/report/2.0"
CAMINHO = "/tmp/teste_parser.ufdr"
N_CHATS = 200
N_MSGS = 10
N_FILES = 40000


def montar_xml():
    p = [f'<project xmlns="{NS}" id="p1">',
         '<metadata section="Extraction Data">',
         '<item name="DeviceInfoSelectedManufacturer">Apple</item>',
         '<item name="DeviceInfoSelectedDeviceName">iPhone 13</item>',
         '<item name="DeviceInfoExtractionStartDateTime">01/02/2025 10:20:30</item>',
         '<item name="LastUsedMSISDN">+55 (27) 99999-1234</item>',
         '</metadata>',
         # contêiner: no report.xml real os <model> não são filhos diretos da raiz
         '<decodedData>']
    for c in range(N_CHATS):
        p.append(f'<model type="Chat" id="c{c}" deleted_state="Intact">'
                 '<field name="Source"><value type="String">WhatsApp</value></field>'
                 f'<field name="Id"><value type="String">grupo{c}@g.us</value></field>'
                 '<multiModelField name="Participants">'
                 f'<model type="Party" id="pt{c}">'
                 '<field name="Identifier"><value type="String">5527999991234@s.whatsapp.net</value></field>'
                 '</model></multiModelField>'
                 '<multiModelField name="Messages">')
        for m in range(N_MSGS):
            corpo = f"msg {c}-{m}"
            if c == 0 and m == 3:
                # o defeito real das extrações 70/72: o Cellebrite escreve o emoji como par de
                # surrogados, ilegal em XML 1.0 — sem o saneamento do xml_limpo o passe explode
                corpo += " &#xD83D;&#xDE00;"
            p.append(f'<model type="InstantMessage" id="m{c}_{m}">'
                     f'<field name="Body"><value type="String">{corpo}</value></field>')
            anexo = None
            if m == 0:
                # tamanho ambíguo no zip, MAS o nome bate com um candidato
                anexo = ("foto_boa.jpg", 777)
            elif m == 1:
                # três candidatos do mesmo tamanho; só o nome desempata
                anexo = ("alvo.jpg", 555)
            elif m == 2:
                # tamanho ambíguo e nome que não bate com nada: tem de ficar SEM binário
                anexo = ("nao_existe_no_zip.jpg", 777)
            if anexo:
                p.append('<multiModelField name="Attachments"><model type="Attachment">'
                         f'<field name="Filename"><value type="String">{anexo[0]}</value></field>'
                         f'<dataField name="Data"><source length="{anexo[1]}"/></dataField>'
                         '</model></multiModelField>')
            p.append('</model>')
        p.append('</multiModelField></model>')
    p.append('<model type="TipoNovoDoPA" id="x1"/><model type="TipoNovoDoPA" id="x2"/>')
    p.append('<model type="Contact" id="ct1">'
             '<field name="Name"><value type="String">Fulano</value></field></model>')
    p.append('</decodedData><taggedFiles>')
    for i in range(N_FILES):
        p.append(f'<file id="f{i}" path="files/Image/img{i}.jpg" size="10">'
                 '<metadata section="File">'
                 f'<item name="Local Path">files\\Image\\img{i}.jpg</item>'
                 f'<item name="MD5">md5_{i}</item></metadata></file>')
    p.append('</taggedFiles></project>')
    return "".join(p)


def montar_ufdr():
    with zipfile.ZipFile(CAMINHO, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("report.xml", montar_xml())
        # 777 bytes: um .jpg candidato E um arquivo de extensão fora do índice — é ele que torna
        # o tamanho ambíguo de verdade, e era ele que o índice não enxergava
        z.writestr("files/Image/foto_boa.jpg", b"a" * 777)
        z.writestr("files/Other/cache_sem_extensao.bin", b"b" * 777)
        # 555 bytes: três candidatos, um com o nome do anexo
        z.writestr("files/Image/alvo.jpg", b"c" * 555)
        z.writestr("files/Image/outro1.jpg", b"d" * 555)
        z.writestr("files/Image/outro2.jpg", b"e" * 555)


def ler(zf, indice, caminhos, contagem, desconhecidos=None, progresso=None, corpos=None):
    tipos, anexos = {}, []
    for tipo, dados in up.iterar_registros(indice_binarios=indice, caminhos_zip=caminhos,
                                           contagem_tamanhos=contagem, zf=zf,
                                           progresso=progresso, desconhecidos=desconhecidos):
        tipos[tipo] = tipos.get(tipo, 0) + 1
        if tipo == "chat":
            for msg in dados[1]["mensagens"]:
                anexos.extend((a["nome_arquivo"], a["caminho_zip"]) for a in msg["anexos"])
                if corpos is not None and msg.get("corpo"):
                    corpos.append(msg["corpo"])
    return tipos, anexos


def medir_pico(zf, indice, caminhos, contagem, limpar):
    original = up._PilhaConteineres.descartar_processados
    if not limpar:
        up._PilhaConteineres.descartar_processados = lambda self: None
    try:
        tracemalloc.start()
        ler(zf, indice, caminhos, contagem)
        _atual, pico = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        return pico
    finally:
        up._PilhaConteineres.descartar_processados = original


def main():
    montar_ufdr()

    info = up.ler_info_dispositivo(CAMINHO)
    assert info["fabricante"] == "Apple", info
    assert info["telefone"] == "5527999991234", info
    print("metadados do aparelho:", info["fabricante"], info["modelo"], info["telefone"])

    with zipfile.ZipFile(CAMINHO) as zf:
        indice, _fotos, caminhos, contagem = up.indexar_indicadores(zf=zf)
        assert contagem[777] == 2, "o .bin sem extensão conhecida tem de contar como colisão"
        assert len(indice[777]) == 1, "só o .jpg é candidato a anexo"
        assert len(indice[555]) == 3, indice[555]

        desconhecidos, progresso = {}, {}
        tipos, anexos = ler(zf, indice, caminhos, contagem, desconhecidos, progresso)

        print("registros por tipo:", tipos)
        assert tipos.get("chat") == N_CHATS, tipos
        assert tipos.get("arquivo") == N_FILES, tipos
        assert tipos.get("contato") == 1, tipos
        assert desconhecidos == {"TipoNovoDoPA": 2}, desconhecidos
        assert progresso["lidos"] > 0 and progresso["total"] > 0, progresso

        distintos = dict(sorted(set(anexos)))
        print("anexos:", distintos)
        assert distintos["alvo.jpg"] == "files/Image/alvo.jpg", "desempate por nome falhou"
        assert distintos["foto_boa.jpg"] == "files/Image/foto_boa.jpg", "o nome devia resgatar"
        assert distintos["nao_existe_no_zip.jpg"] is None, (
            "tamanho ambíguo sem nome que case tem de ficar SEM binário — servir um palpite aqui "
            "põe o arquivo de outra pessoa dentro da conversa")

        sem_limpar = medir_pico(zf, indice, caminhos, contagem, limpar=False)
        limpando = medir_pico(zf, indice, caminhos, contagem, limpar=True)

    mib = 1024 * 1024
    print(f"pico de memória: {sem_limpar/mib:.1f} MiB sem limpar o pai, "
          f"{limpando/mib:.1f} MiB limpando ({(1 - limpando/sem_limpar)*100:.0f}% menos)")
    assert limpando < sem_limpar / 2, "a limpeza do pai não está surtindo efeito"

    # par de surrogados (emoji ilegal em XML 1.0) no meio do report.xml tem de chegar ao
    # parser como o emoji de verdade — ver funcoes/xml_limpo.py e o erro das extrações 70/72
    corpos: list[str] = []
    with zipfile.ZipFile(CAMINHO) as zf:
        indice, _fotos, caminhos, contagem = up.indexar_indicadores(zf=zf)
        ler(zf, indice, caminhos, contagem, corpos=corpos)
    com_emoji = [c for c in corpos if "\U0001F600" in c]
    assert len(com_emoji) == 1, f"emoji do par de surrogados não chegou: {com_emoji}"
    print("emoji via par de surrogados no report.xml chegou intacto ao parser")

    # nome de contato gigante não pode passar do limite do btree do IX198 (extração 73:
    # "index row size 5824 exceeds btree version 4 maximum 2704" derrubou a importação)
    from xml.etree import ElementTree as ET
    modelo = ET.fromstring(
        f'<model xmlns="{NS}" type="Contact">'
        f'<field name="Name"><value type="String">{"x" * 6000}</value></field></model>')
    gigante = up._extrair_contato(modelo)
    assert len(gigante["nome"]) == 500, f"nome tinha de sair cortado a 500: {len(gigante['nome'])}"
    print("nome de contato gigante cortado antes de estourar o índice")

    os.remove(CAMINHO)
    print("OK")


if __name__ == "__main__":
    main()

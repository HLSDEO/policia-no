"""Montagem do relatório em .docx (python-docx), a partir de um padrão (blocos) + variáveis
do caso + registros marcados. Reproduz o modelo de Relatório de Análise de Material Apreendido
da PF: cabeçalho/rodapé em toda página, sumário (campo TOC que o Word atualiza), e um
renderizador por tipo de bloco, com as tabelas de conversa do WhatsApp coloridas por remetente
(verde = dono do aparelho / investigado, azul = demais).

Os dados vêm de funcoes/relatorios_utils.py (obter_padrao, montar_valores, substituir_texto,
marcacoes_filtradas, listar_whatsapp_marcado)."""

import math
import re
from collections.abc import Callable
from datetime import datetime
from io import BytesIO
from pathlib import Path

from PIL import Image
from docx import Document
from docx.enum.table import WD_ROW_HEIGHT_RULE
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from funcoes import anonimizacao as an
from funcoes import relatorios_utils as ru
from funcoes import ufdr_cache
from funcoes.investigacao_utils import (fonte_ufdr, categoria_binario_anexo,
                                       redimensionar_imagem)

VERDE = "D9EAD3"   # mensagens do dono do aparelho (investigado)
AZUL = "CFE2F3"    # mensagens dos demais interlocutores
CINZA = "EFEFEF"   # cabeçalho de tabela

COLUNAS_WPP = ["De", "Marcação de tempo-Hora", "Corpo", "Anexo", "Transcrição/observação"]

# tabela do bloco 'extracoes' (nome do arquivo | tamanho | SHA-256), em fração da largura útil:
# o hash de 64 caracteres precisa de quase metade da linha para caber em duas
EXTRACOES_FRACOES_COLUNAS = (0.39, 0.20, 0.41)
# extração importada antes de 25_rif.sql não tem hash gravado no banco
TEXTO_HASH_AUSENTE = "Não calculado na importação."

# tabelas dos blocos 'rif_arquivos'/'bancario_arquivos' (metadados dos arquivos do relatório),
# em fração da largura útil — como no bloco 'extracoes', a coluna de hashes leva quase metade da
# linha: o SHA-256 tem 64 caracteres e não pode estourar a página
RIF_ARQUIVOS_FRACOES_COLUNAS = (0.13, 0.23, 0.10, 0.13, 0.41)
BANCARIO_ARQUIVOS_FRACOES_COLUNAS = (0.42, 0.16, 0.42)

# rótulo amigável de cada arquivo da pasta de um RIF (RIF_TB06_ARQUIVOS.TP_ARQUIVO) — o mesmo
# mapa que a tela de investigação usa no botão de hash (ver textoHashesRif em investigacao.js)
ROTULO_TIPO_ARQUIVO_RIF = {
    "comunicacoes": "Comunicações",
    "envolvidos": "Envolvidos",
    "ocorrencias": "Ocorrências",
    "pdf": "PDF",
}

# Lado máximo (cm) da imagem de um anexo embutida numa célula: a imagem é encaixada num
# quadrado desse lado, sem distorcer. A coluna "Anexo" da tabela de WhatsApp é estreita
# (5 colunas na página), a de conteúdo dos registros marcados é mais larga (4 colunas).
IMG_ANEXO_WPP_CM = 4.0
IMG_ANEXO_MARCACAO_CM = 5.5

# bloco 'chat': a conversa sai como a tela de um celular. A tabela que sustenta o mockup é uma
# GRADE de CHAT_COLUNAS colunas iguais, e cada linha mescla um trecho dessa grade: a bolha ocupa
# de CHAT_MIN_COLUNAS a CHAT_MAX_COLUNAS colunas, o resto vira papel de parede. É essa grade que
# dá a bolha "do tamanho do texto" do WhatsApp de verdade — com uma tabela de 2 colunas de
# largura fixa, "Tô indo" saía numa bolha atravessando a página, que é o que mais destoa do app.
# Mesclagem de colunas de uma grade é a única forma de largura variável por linha que o Word
# respeita sem discussão (largura de célula solta, linha a linha, ele reinterpreta).
#
# As larguras são sempre calculadas sobre a largura ÚTIL real da página (_largura_util_cm, a
# partir da seção do documento) — nunca um valor fixo em cm: a área útil do _preparar_documento()
# é ~15,24cm (papel A4/Carta padrão do python-docx menos as margens de 3,175cm), bem menor que os
# ~16,5cm que a receita mais comum na internet assume, e um valor fixo overestimado faz a tabela
# vazar a margem direita da página.
CHAT_COLUNAS = 24
CHAT_MARGEM_COLUNAS = 1  # respiro entre a bolha e a borda da "tela", como no app
CHAT_MIN_COLUNAS = 4     # ~17% da página: o suficiente para a hora no rodapé da bolha
CHAT_MAX_COLUNAS = 17    # com a margem, 75% da página — a proporção máxima da bolha no app


def _largura_util_cm(doc) -> float:
    secao = doc.sections[-1]
    return (secao.page_width - secao.left_margin - secao.right_margin) / 360000

# paleta do bloco 'chat' — as mesmas cores do WhatsApp (barra verde-escura no topo, papel de
# parede bege, bolha verde clara do dono do aparelho, bolha branca dos demais), para o mockup
# lembrar a tela do app de verdade
CHAT_FUNDO = "E5DDD5"
CHAT_BOLHA_ENVIADA = "DCF8C6"
CHAT_BOLHA_RECEBIDA = "FFFFFF"
CHAT_COR_TEXTO = "111B21"
CHAT_COR_HORA = "667781"
CHAT_COR_DIVISOR_DATA = "D1D7DB"
CHAT_BARRA = "075E54"        # verde-escuro da barra de título do WhatsApp
CHAT_BARRA_TEXTO = "FFFFFF"
CHAT_BARRA_SUBTEXTO = "B7D8D3"
CHAT_MOLDURA = "C9D1D3"      # contorno fino da "tela" do celular
CHAT_FONTE = "Segoe UI"

# altura das linhas vazias que separam as bolhas: pouca entre mensagens seguidas do mesmo
# remetente (no app elas saem quase coladas) e mais entre remetentes diferentes
CHAT_ESPACO_MESMO_PT = 2
CHAT_ESPACO_OUTRO_PT = 6

# imagem dentro de uma bolha: bem maior que na coluna estreita da tabela do bloco 'whatsapp' —
# aqui a bolha tem até 75% da página, e mídia miúda é justamente o que não parece o app
CHAT_IMG_CM = 6.5

# Largura média de um caractere, em cm POR PONTO de tamanho de fonte (Segoe UI: ~0,17cm a 10pt,
# medido em texto corrido em português no PDF gerado pelo Word). Serve só para escolher quantas
# colunas da grade a bolha ocupa; quem quebra a linha de verdade é o Word — errar para mais só
# deixa a bolha um pouco mais larga que o texto, enquanto errar para menos quebra o nome do
# remetente em duas linhas, que é feio e não acontece no app.
_CM_POR_CARACTERE_PT = 0.017
# folga da bolha: as margens internas da célula (0,19cm de cada lado) mais um respiro
CHAT_FOLGA_CM = 0.6

# caixa "Comentários do investigador", que sai depois de cada trecho de conversa: papel levemente
# creme, barra de acento à esquerda no verde da barra do WhatsApp e, dentro dela, cada anotação
# precedida da CITAÇÃO da mensagem comentada (nome, carimbo e um trecho do conteúdo), no desenho
# que o próprio app usa para resposta. Antes eram parágrafos soltos no corpo do relatório, com a
# mensagem referida só pelo carimbo de hora: o leitor tinha que caçar a bolha correspondente na
# tela acima, e num trecho com várias marcações isso era uma linha igual à outra.
COMENT_FUNDO = "F4F2EF"
COMENT_BARRA = "075E54"
COMENT_BARRA_PT = 18          # oitavos de ponto (18/8 ≈ 2,25pt)
COMENT_CITACAO_BARRA = "C9D1D3"
COMENT_CITACAO_PT = 10
COMENT_COR_ROTULO = "3B4A54"  # título da caixa e carimbo da citação
COMENT_MAX_CHARS_CITACAO = 120   # trecho citado: o bastante para reconhecer a mensagem

# cores dos nomes de remetente num grupo (o WhatsApp colore cada participante de um jeito, para
# diferenciar quem falou o quê sem precisar ler o nome toda hora); escolha determinística por
# nome, para o mesmo remetente sair sempre com a mesma cor no relatório inteiro
_PALETA_REMETENTES = ["C0392B", "8E44AD", "2471A3", "117A65", "B9770E", "AD1457", "0B5345", "6C3483"]


def _cor_remetente(nome: str) -> str:
    return _PALETA_REMETENTES[sum(ord(c) for c in nome) % len(_PALETA_REMETENTES)] if nome else "3B4A54"

# UFDR nem sempre traz o ContentType do anexo; nesse caso a extensão decide a categoria
_EXTENSOES_IMAGEM = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".heic", ".heif", ".tiff", ".tif")
_EXTENSOES_AUDIO = (".opus", ".mp3", ".m4a", ".aac", ".ogg", ".oga", ".amr", ".wav", ".3ga", ".caf")
_EXTENSOES_VIDEO = (".mp4", ".3gp", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".mpg", ".mpeg")

# Mensagem apagada ou mídia que a extração não recuperou: o registro existe no UFDR, o binário
# não. Antes a célula saía VAZIA, e célula vazia num relatório assinado é pior que uma frase —
# o leitor não distingue "o analista esqueceu de preencher" de "esse dado não existe mais". As
# frases abaixo são as mesmas que a janela de análise mostra no lugar da mídia.
TEXTO_SEM_CONTEUDO = "Conteúdo não disponível."
_ROTULO_CATEGORIA = {"imagem": " de imagem", "audio": " de áudio", "video": " de vídeo"}
# na coluna/bolha do próprio anexo, onde o nome do arquivo já está escrito, a frase inteira
# seria repetição — basta a marca curta
_MARCA_ANEXO_INDISPONIVEL = "(arquivo indisponível na extração)"

# MD5 do binário do anexo, impresso embaixo do nome do arquivo nos blocos 'marcacoes',
# 'whatsapp' e 'chat'. É o que liga o arquivo citado no relatório ao binário periciado: dois
# nomes iguais não provam nada, o hash sim. Vem pronto do <file> do UFDR (ver ufdr_parser); o
# relatório só transcreve.
_ROTULO_HASH_MD5 = "HASH - MD5: "
# Sem hash no banco em anexo cujo binário EXISTE: ou a extração foi ingerida antes de o parser
# passar a ler MD5/SHA-256 do <file>, ou o binário não casou com nenhuma linha de arquivo. Nos
# dois casos a célula declara — num relatório de cadeia de custódia, silêncio não distingue
# "não temos o hash" de "esqueceram de escrever". Some reprocessando a extração.
_TEXTO_HASH_MD5_AUSENTE = _ROTULO_HASH_MD5 + "não disponível nesta extração."


def _categoria_anexo(anexo: dict) -> str:
    """imagem | audio | video | outro.

    O `tp_conteudo` do UFDR é irregular: às vezes é MIME de verdade (`image/webp`,
    `audio/ogg; codecs=opus`), às vezes é a categoria por extenso do Cellebrite (`Image`,
    `Sticker`, `Voice message`, `GIF`), às vezes é `URL` ou lixo (`__________`). Por isso a
    palavra-chave é procurada em qualquer posição, e o que não casar cai na extensão do nome do
    arquivo — comparar o começo com "image/" descartava justamente os `Image`/`Sticker`.

    Vídeo vem antes de imagem porque o Cellebrite marca GIF animado como `GIF/video/mp4`: o
    binário é mp4, não imagem."""
    valor = (anexo.get("tp_conteudo") or "").lower()
    for chaves, categoria in (
        (("video",), "video"),
        (("audio", "voice", "ptt"), "audio"),
        (("image", "imagem", "sticker", "figurinha", "gif", "photo"), "imagem"),
    ):
        if any(c in valor for c in chaves):
            return categoria
    nome = (anexo.get("nome") or "").lower()
    if nome.endswith(_EXTENSOES_IMAGEM):
        return "imagem"
    if nome.endswith(_EXTENSOES_AUDIO):
        return "audio"
    if nome.endswith(_EXTENSOES_VIDEO):
        return "video"
    return "outro"


def _anexo_disponivel(anexo: dict) -> bool:
    """O binário foi extraído? Mesmo critério da tela de análise (co_caminho_zip preenchido)."""
    return bool(anexo.get("caminho_zip"))


def _texto_anexo_indisponivel(anexo: dict, de_mensagem: bool = True) -> str:
    return (
        "Arquivo"
        + (" de mensagem" if de_mensagem else "")
        + _ROTULO_CATEGORIA.get(_categoria_anexo(anexo), "")
        + " indisponível."
    )


def _texto_sem_conteudo(anexos, de_mensagem: bool = True) -> str:
    """O que escrever no lugar do corpo quando o registro não tem texto.

    Devolve "" quando há anexo disponível (a imagem/nome do arquivo já é o conteúdo, e repetir
    "sem conteúdo" ao lado de uma foto só polui); a frase específica do tipo de mídia quando os
    anexos existem mas o binário não veio; e a frase genérica quando não há nada."""
    anexos = anexos or []
    if any(_anexo_disponivel(a) for a in anexos):
        return ""
    frases: list[str] = []
    for a in anexos:
        frase = _texto_anexo_indisponivel(a, de_mensagem)
        if frase not in frases:
            frases.append(frase)
    return "\n".join(frases) if frases else TEXTO_SEM_CONTEUDO


# O Cellebrite grava a LEGENDA da mídia no Title do anexo (TX_TITULO), e não no Body da
# mensagem: numa mídia de visualização única — que o WhatsApp apaga do aparelho assim que é
# aberta — a legenda é a ÚNICA coisa que sobra dela na extração. Em áudio e figurinha o mesmo
# campo não é legenda: vem "Duration: 00:00:16" ou "Sticker", metadado que não é conteúdo da
# mensagem e não pode entrar no corpo. Mesma regra da janela de análise (whatsapp.js).
_TITULO_METADADO = re.compile(r"^(duration:\s*\d{1,2}:\d{2}(:\d{2})?|sticker)$", re.IGNORECASE)


def _legenda_anexo(anexo: dict) -> str:
    """A legenda que o anexo carrega, "" quando o Title é metadado ou repete o nome do arquivo."""
    titulo = (anexo.get("titulo") or "").strip()
    if not titulo or _TITULO_METADADO.match(titulo):
        return ""
    if titulo == (anexo.get("nome") or "").strip():
        return ""
    return titulo


def _legendas_dos_anexos(anexos, texto: str = "") -> list[str]:
    """Legendas dos anexos, na ordem, sem repetir o corpo da mensagem nem uma à outra."""
    legendas: list[str] = []
    for anexo in anexos or []:
        legenda = _legenda_anexo(anexo)
        if legenda and legenda != (texto or "").strip() and legenda not in legendas:
            legendas.append(legenda)
    return legendas


def _texto_localizacao(lat: float, lon: float) -> str:
    return (
        "\U0001F4CD Localização compartilhada: " + f"{lat:.6f}, {lon:.6f}"
        + "\nhttps://www.google.com/maps?q=" + f"{lat},{lon}"
    )


def _corpo_do_registro(texto, anexos, de_mensagem: bool = True,
                        lat: float | None = None, lon: float | None = None) -> tuple[str, bool]:
    """(o que escrever no corpo, se esse texto já declara a indisponibilidade do anexo).

    O segundo valor vai para `declarar_indisponivel` de `_anexos_na_celula`: quando o corpo já
    diz "Arquivo de mensagem de áudio indisponível.", o anexo logo abaixo não repete a marca.

    lat/lon: mensagem de "Localização compartilhada" do WhatsApp não tem Body nem anexo — sem
    isso ela saía como "Conteúdo não disponível." no relatório."""
    texto = texto or ""
    # a legenda da mídia é conteúdo da mensagem, e vai INTEIRA: cabe aqui, junto do corpo, e
    # não na coluna/bolha do anexo, onde ela disputaria a linha com o nome do arquivo
    legendas = _legendas_dos_anexos(anexos, texto)
    if texto or legendas:
        return "\n".join(([texto] if texto else []) + legendas), True
    if lat is not None and lon is not None:
        return _texto_localizacao(lat, lon), True
    corpo = _texto_sem_conteudo(anexos, de_mensagem)
    return corpo, not corpo


# ---------- helpers de baixo nível (oxml) ----------


# O w:tcPr tem ordem fixa no schema (…gridSpan, shd, noWrap, tcMar, vAlign…) e o Word recusa o
# arquivo ("conteúdo ilegível") se um filho aparecer fora de ordem — daí inserir antes dos
# sucessores em vez de dar append no fim do elemento.
_SUCESSORES_SHD = ("w:noWrap", "w:tcMar", "w:textDirection", "w:tcFitText", "w:vAlign", "w:hideMark")
_SUCESSORES_TCMAR = ("w:textDirection", "w:tcFitText", "w:vAlign", "w:hideMark")
_SUCESSORES_TBLBORDERS = ("w:shd", "w:tblLayout", "w:tblCellMar", "w:tblLook")
# w:tcBorders vem ANTES do w:shd no w:tcPr; w:pBdr, antes do w:shd no w:pPr
_SUCESSORES_TCBORDERS = ("w:shd", "w:noWrap", "w:tcMar", "w:textDirection", "w:tcFitText",
                         "w:vAlign", "w:hideMark")
_SUCESSORES_PBDR = ("w:shd", "w:tabs", "w:spacing", "w:ind", "w:jc", "w:rPr", "w:sectPr")


def _shade(cell, cor_hex: str):
    """Pinta o fundo de uma célula (w:shd)."""
    tcPr = cell._tc.get_or_add_tcPr()
    for antigo in tcPr.findall(qn("w:shd")):
        tcPr.remove(antigo)
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), cor_hex)
    tcPr.insert_element_before(shd, *_SUCESSORES_SHD)


def _margens_celula(cell, cima_cm: float, lados_cm: float, baixo_cm: float | None = None):
    """Margens internas da célula (w:tcMar) — é o que dá o "acolchoado" da bolha de mensagem:
    sem isso o texto encosta na borda colorida e a bolha vira um retângulo pintado."""
    tcPr = cell._tc.get_or_add_tcPr()
    for antigo in tcPr.findall(qn("w:tcMar")):
        tcPr.remove(antigo)
    tcMar = OxmlElement("w:tcMar")
    valores = (("top", cima_cm), ("left", lados_cm),
               ("bottom", cima_cm if baixo_cm is None else baixo_cm), ("right", lados_cm))
    for lado, cm in valores:
        el = OxmlElement("w:" + lado)
        el.set(qn("w:w"), str(int(round(cm * 567))))  # cm -> twips (1cm = 567 dxa)
        el.set(qn("w:type"), "dxa")
        tcMar.append(el)
    tcPr.insert_element_before(tcMar, *_SUCESSORES_TCMAR)


def _bordas_tabela(tabela, cor_externa: str | None = None, espessura: int = 4):
    """Zera as bordas da tabela (o mockup de chat não pode ter grade) e, opcionalmente, desenha
    só o contorno externo — a "moldura" da tela do celular."""
    tblPr = tabela._tbl.tblPr
    for antigo in tblPr.findall(qn("w:tblBorders")):
        tblPr.remove(antigo)
    bordas = OxmlElement("w:tblBorders")
    for lado in ("top", "left", "bottom", "right", "insideH", "insideV"):
        externa = bool(cor_externa) and lado in ("top", "left", "bottom", "right")
        el = OxmlElement("w:" + lado)
        el.set(qn("w:val"), "single" if externa else "none")
        el.set(qn("w:sz"), str(espessura) if externa else "0")
        el.set(qn("w:space"), "0")
        el.set(qn("w:color"), cor_externa if externa else "auto")
        bordas.append(el)
    tblPr.insert_element_before(bordas, *_SUCESSORES_TBLBORDERS)


def _bordas_celula(cell, **lados) -> None:
    """Bordas de UMA célula (w:tcBorders): _bordas_celula(c, left=("075E54", 18)) desenha só a
    barra de acento à esquerda. Cada lado recebe (cor, espessura em oitavos de ponto); lado não
    citado sai sem borda. É o que _bordas_tabela não faz — lá a borda é da tabela inteira."""
    tcPr = cell._tc.get_or_add_tcPr()
    for antigo in tcPr.findall(qn("w:tcBorders")):
        tcPr.remove(antigo)
    bordas = OxmlElement("w:tcBorders")
    for lado in ("top", "left", "bottom", "right"):
        cor_espessura = lados.get(lado)
        el = OxmlElement("w:" + lado)
        el.set(qn("w:val"), "single" if cor_espessura else "none")
        el.set(qn("w:sz"), str(cor_espessura[1]) if cor_espessura else "0")
        el.set(qn("w:space"), "0")
        el.set(qn("w:color"), cor_espessura[0] if cor_espessura else "auto")
        bordas.append(el)
    tcPr.insert_element_before(bordas, *_SUCESSORES_TCBORDERS)


def _borda_esquerda_paragrafo(paragrafo, cor_hex: str, espessura: int = 12) -> None:
    """Filete vertical à esquerda de um parágrafo (w:pBdr) — a marca de citação. Acompanha a
    altura do parágrafo, então serve para o trecho citado que ocupa duas linhas; uma célula de
    tabela aninhada, que seria a outra forma, não acompanha."""
    pPr = paragrafo._p.get_or_add_pPr()
    for antigo in pPr.findall(qn("w:pBdr")):
        pPr.remove(antigo)
    pbdr = OxmlElement("w:pBdr")
    el = OxmlElement("w:left")
    el.set(qn("w:val"), "single")
    el.set(qn("w:sz"), str(espessura))
    el.set(qn("w:space"), "6")
    el.set(qn("w:color"), cor_hex)
    pbdr.append(el)
    pPr.insert_element_before(pbdr, *_SUCESSORES_PBDR)


def _repetir_no_topo(row):
    """Marca a linha como cabeçalho (w:tblHeader): quando a conversa passa de página, a barra
    verde com o nome do contato reaparece no alto da página seguinte, como na tela do celular."""
    trPr = row._tr.get_or_add_trPr()
    el = OxmlElement("w:tblHeader")
    trPr.append(el)


def _campo(paragrafo, instrucao: str, texto_provisorio: str = ""):
    """Insere um campo do Word (ex.: PAGE, TOC) num parágrafo."""
    run = paragrafo.add_run()
    ini = OxmlElement("w:fldChar"); ini.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText"); instr.set(qn("xml:space"), "preserve"); instr.text = instrucao
    sep = OxmlElement("w:fldChar"); sep.set(qn("w:fldCharType"), "separate")
    txt = OxmlElement("w:t"); txt.text = texto_provisorio
    fim = OxmlElement("w:fldChar"); fim.set(qn("w:fldCharType"), "end")
    for el in (ini, instr, sep, txt, fim):
        run._r.append(el)


def _cell(cell, texto: str, tamanho: int = 9, negrito: bool = False, cor_fundo: str | None = None):
    cell.text = ""
    p = cell.paragraphs[0]
    for i, linha in enumerate(str(texto or "").split("\n")):
        if i > 0:
            p.add_run().add_break()
        run = p.add_run(linha)
        run.font.size = Pt(tamanho)
        run.bold = negrito
    if cor_fundo:
        _shade(cell, cor_fundo)


def _linhas_conteudo(texto: str):
    """Quebra o conteúdo em parágrafos, preservando parágrafos em branco como separadores."""
    return (texto or "").split("\n")


# ---------- anexos (nome do arquivo + a imagem, quando for imagem) ----------


def _eh_imagem(anexo: dict) -> bool:
    """O binário que veio na extração é uma imagem? Decide pelo ARQUIVO, não pelo tipo
    declarado — e é essa a diferença que importa: vídeo do WhatsApp cujo binário não foi
    recuperado chega como `video/mp4` com um JPEG de 1 KB no lugar (o `.thumb` que o
    Cellebrite guarda). Pelo tipo declarado ele é vídeo e o relatório não mostrava nada;
    pelo binário é imagem, e a miniatura — muitas vezes a única coisa que se pode VER
    daquela mídia — entra no documento. Mesma regra da tela de análise
    (investigacao_utils.categoria_binario_anexo, usada pelo chat via `so_miniatura`).

    Sem binário localizado, cai no tipo declarado: não há o que exibir de qualquer forma, e a
    categoria só serve para a frase de indisponibilidade."""
    caminho_zip = anexo.get("caminho_zip")
    if not caminho_zip:
        return _categoria_anexo(anexo) == "imagem"
    return categoria_binario_anexo(caminho_zip) == "imagem"


def _so_miniatura(anexo: dict) -> bool:
    """O binário é imagem, mas a mídia original NÃO era — é só a miniatura que o Cellebrite
    guardou de um vídeo/PDF/link/localização que não veio na extração. É o mesmo aviso que a
    tela de análise dá (`so_miniatura`, em investigacao_utils), com uma diferença: lá a
    comparação é `tipo.startswith("image/")` e aqui passa por _categoria_anexo, que também
    reconhece as formas por extenso do Cellebrite ("Image", "Sticker") — senão uma figurinha
    de verdade sairia rotulada como miniatura de outra coisa."""
    return _eh_imagem(anexo) and _categoria_anexo(anexo) != "imagem"


# "do vídeo"/"do áudio"/... para a frase da miniatura: o rótulo descreve a mídia ORIGINAL,
# que é o que o tipo declarado diz — não o JPEG que sobrou dela
_MIDIA_ORIGINAL = {"video": "do vídeo", "audio": "do áudio", "imagem": "da imagem"}


def _imagem_do_anexo(anexo: dict) -> bytes | None:
    """Bytes (JPEG já reduzido) da imagem de um anexo, lidos sob demanda do .ufdr original —
    o binário não é copiado para o banco nem para o disco, mesmo caminho da prévia no chat
    (inclusive o cache em memória, com a mesma chave). None quando o anexo não é imagem, o
    .ufdr não está na pasta de dados ou o Pillow não consegue decodificar."""
    caminho_zip = anexo.get("caminho_zip")
    ufdr = anexo.get("ufdr")
    if not caminho_zip or not ufdr or not _eh_imagem(anexo):
        return None
    caminho_ufdr = fonte_ufdr(ufdr)
    if not caminho_ufdr:
        return None
    chave = (str(caminho_ufdr), caminho_zip, "img")
    dados = ufdr_cache.obter_conteudo(chave)
    if dados is not None:
        return dados
    try:
        dados = redimensionar_imagem(ufdr_cache.ler_membro(caminho_ufdr, caminho_zip))
    except Exception:
        return None  # arquivo ausente/corrompido: o relatório sai só com o nome do anexo
    ufdr_cache.guardar_conteudo(chave, dados)
    return dados


def _proximo_paragrafo(cell):
    """Próximo parágrafo livre da célula: reaproveita o último enquanto estiver vazio (toda
    célula já nasce com um), senão acrescenta outro."""
    p = cell.paragraphs[-1]
    return p if not p.runs else cell.add_paragraph()


# 1 px = 1/96 de polegada, a mesma equivalência que o navegador usa. É o teto de ampliação:
# a miniatura de vídeo do WhatsApp tem 54x96 px e, esticada para os 6,5 cm da bolha, virava um
# borrão de pixels num documento assinado. Foto de verdade sai sempre maior que a caixa (a
# prévia já vem com 800 px no lado maior), então para ela o teto nunca pega.
_CM_POR_PIXEL = 2.54 / 96


def _inserir_imagem(paragrafo, dados: bytes, lado_cm: float) -> None:
    """Insere a imagem encaixada num quadrado de lado_cm, preservando a proporção (foto em
    retrato não pode esticar a linha da tabela por uma página inteira). Imagem menor que a
    caixa sai no tamanho natural, sem ampliação — ver _CM_POR_PIXEL."""
    try:
        with Image.open(BytesIO(dados)) as img:
            largura_px, altura_px = img.size
        escala = min(lado_cm / largura_px, lado_cm / altura_px, _CM_POR_PIXEL)
        paragrafo.add_run().add_picture(
            BytesIO(dados), width=Cm(largura_px * escala), height=Cm(altura_px * escala)
        )
    except Exception:
        pass  # não conseguiu embutir: o nome do arquivo já está na célula


def _linha_hash_md5(cell, anexo: dict, tamanho: int, cor: str | None = None) -> None:
    """Escreve "HASH - MD5: …" logo abaixo do nome do arquivo do anexo.

    Só para anexo com binário na extração: o hash é do binário, e onde ele não veio não há o
    que citar — a célula logo acima já declarou a indisponibilidade. Um ponto menor que o nome,
    porque o MD5 tem 32 caracteres e a coluna "Anexo" é estreita."""
    if not _anexo_disponivel(anexo):
        return
    hash_md5 = (anexo.get("hash_md5") or "").strip()
    run = _proximo_paragrafo(cell).add_run(
        (_ROTULO_HASH_MD5 + hash_md5) if hash_md5 else _TEXTO_HASH_MD5_AUSENTE
    )
    run.font.size = Pt(max(tamanho - 1, 6))
    run.italic = not hash_md5
    if cor:
        run.font.color.rgb = RGBColor.from_string(cor)


def _anexos_na_celula(cell, anexos, lado_cm: float, tamanho: int = 8, de_mensagem: bool = True,
                      declarar_indisponivel: bool = True, com_hash: bool = False,
                      cor_metadados: str | None = None) -> None:
    """Acrescenta à célula o nome de cada anexo e, quando ele é uma imagem, a própria imagem
    logo abaixo do nome. Anexo cujo binário não veio na extração (mídia apagada) sai declarado
    como indisponível, nunca em silêncio — a menos que o corpo da mensagem já tenha declarado
    isso (`declarar_indisponivel=False`), senão a mesma informação sai duas vezes seguidas.

    `com_hash` acrescenta o MD5 do binário embaixo do nome. Ligado nos três blocos que citam
    arquivo — 'whatsapp' e 'marcacoes' (padrão "relatório de análise - tabela simples") e
    'chat' (padrão "relatório de análise - whatsapp") —, porque é o hash que liga o arquivo
    citado no relatório ao binário periciado.

    `cor_metadados` pinta nome, hash e avisos do anexo (miniatura/indisponível) nessa cor —
    a bolha do 'chat' usa o cinza do app para o metadado de arquivo, reservando o preto para
    o conteúdo (corpo e transcrição); nas tabelas sai na cor padrão do documento."""
    for anexo in anexos or []:
        nome = anexo.get("nome") or ""
        dados = _imagem_do_anexo(anexo)
        indisponivel = declarar_indisponivel and not _anexo_disponivel(anexo)
        if not nome and dados is None and not indisponivel:
            continue
        if nome:
            run = _proximo_paragrafo(cell).add_run(nome)
            run.font.size = Pt(tamanho)
            if cor_metadados:
                run.font.color.rgb = RGBColor.from_string(cor_metadados)
        if com_hash:
            _linha_hash_md5(cell, anexo, tamanho, cor_metadados)
        if dados is not None:
            _inserir_imagem(_proximo_paragrafo(cell), dados, lado_cm)
            # a imagem que entrou não é a mídia: é a miniatura que sobrou dela. Sem dizer isso,
            # o relatório mostra um quadro de vídeo como se fosse a foto enviada.
            if _so_miniatura(anexo):
                midia = _MIDIA_ORIGINAL.get(_categoria_anexo(anexo), "do arquivo")
                run = _proximo_paragrafo(cell).add_run(f"(só a miniatura {midia} foi extraída)")
                run.font.size = Pt(max(tamanho - 1, 6))
                run.italic = True
                if cor_metadados:
                    run.font.color.rgb = RGBColor.from_string(cor_metadados)
        elif indisponivel:
            run = _proximo_paragrafo(cell).add_run(
                _MARCA_ANEXO_INDISPONIVEL if nome else _texto_anexo_indisponivel(anexo, de_mensagem)
            )
            run.font.size = Pt(tamanho)
            run.italic = True
            if cor_metadados:
                run.font.color.rgb = RGBColor.from_string(cor_metadados)


# ---------- documento ----------


def _preparar_documento():
    doc = Document()
    normal = doc.styles["Normal"]
    normal.font.name = "Times New Roman"
    normal.font.size = Pt(12)

    secao = doc.sections[0]

    # cabeçalho: brasão centralizado acima de duas linhas "SERVIÇO PÚBLICO FEDERAL" /
    # "MJSP - POLÍCIA FEDERAL"
    cab = secao.header
    cab.is_linked_to_previous = False
    # tenta localizar o brasão em locais conhecidos (dev vs container)
    brasao_path = None
    for candidato in (
        Path(__file__).with_name("brasao.png"),
        Path(__file__).resolve().parents[2] / "web" / "img" / "brasao.png",
        Path("/app/funcoes/brasao.png"),
        Path("/app/web/img/brasao.png"),
    ):
        try:
            if candidato.is_file():
                brasao_path = candidato
                break
        except Exception:
            continue

    p0 = cab.paragraphs[0]
    p0.alignment = WD_ALIGN_PARAGRAPH.CENTER
    tem_brasao = False
    if brasao_path is not None:
        try:
            p0.paragraph_format.space_after = Pt(2)
            p0.paragraph_format.space_before = Pt(0)
            run_img = p0.add_run()
            run_img.add_picture(str(brasao_path), width=Cm(2.0))
            tem_brasao = True
        except Exception:
            # Pillow/docx falhou — ignora imagem e segue só com texto
            try:
                p0.clear()
            except Exception:
                pass
            tem_brasao = False

    if tem_brasao:
        p1 = cab.add_paragraph()
        p1.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p1.paragraph_format.space_after = Pt(0)
        p1.paragraph_format.space_before = Pt(0)
        r1 = p1.add_run("SERVIÇO PÚBLICO FEDERAL")
        r1.font.size = Pt(10)
        p2 = cab.add_paragraph()
        p2.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p2.paragraph_format.space_after = Pt(0)
        p2.paragraph_format.space_before = Pt(0)
        r2 = p2.add_run("MJSP - POLÍCIA FEDERAL")
        r2.font.size = Pt(10)
    else:
        # sem brasão — reaproveita o primeiro parágrafo para a primeira linha
        p0.paragraph_format.space_after = Pt(0)
        p0.paragraph_format.space_before = Pt(0)
        r1 = p0.add_run("SERVIÇO PÚBLICO FEDERAL")
        r1.font.size = Pt(10)
        p2 = cab.add_paragraph()
        p2.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p2.paragraph_format.space_after = Pt(0)
        p2.paragraph_format.space_before = Pt(0)
        r2 = p2.add_run("MJSP - POLÍCIA FEDERAL")
        r2.font.size = Pt(10)

    return doc, secao


def _montar_rodape(secao, numero_rama: str):
    rod = secao.footer
    rod.is_linked_to_previous = False
    p = rod.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    prefixo = (numero_rama + "  ·  ") if numero_rama else ""
    r = p.add_run(prefixo + "Página ")
    r.font.size = Pt(9)
    _campo(p, "PAGE", "1")


# ---------- renderizadores por bloco ----------


def _titulo_secao(doc, texto: str, nivel: int = 1):
    if not texto:
        return
    doc.add_heading(texto, level=nivel)


def _bloco_capa(doc, bloco, valores):
    titulo = ru.substituir_texto(bloco.get("no_titulo"), valores)
    if titulo:
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = p.add_run(titulo)
        run.bold = True
        run.font.size = Pt(16)
    conteudo = ru.substituir_texto(bloco.get("tx_conteudo"), valores)
    for linha in _linhas_conteudo(conteudo):
        if linha.strip() == "":
            continue
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        if "|" in linha:
            chave, valor = linha.split("|", 1)
            r = p.add_run(chave.strip() + ": ")
            r.bold = True
            p.add_run(valor.strip())
        else:
            p.add_run(linha.strip())


def _bloco_sumario(doc, bloco, valores):
    _titulo_secao(doc, ru.substituir_texto(bloco.get("no_titulo"), valores) or "SUMÁRIO")
    p = doc.add_paragraph()
    _campo(p, 'TOC \\o "1-3" \\h \\z \\u', "Clique com o botão direito e escolha “Atualizar campo” para gerar o sumário.")
    doc.add_page_break()


def _bloco_texto(doc, bloco, valores):
    _titulo_secao(doc, ru.substituir_texto(bloco.get("no_titulo"), valores))
    conteudo = ru.substituir_texto(bloco.get("tx_conteudo"), valores)
    for linha in _linhas_conteudo(conteudo):
        p = doc.add_paragraph(linha)
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY


def _bloco_tabela_campos(doc, bloco, valores):
    _titulo_secao(doc, ru.substituir_texto(bloco.get("no_titulo"), valores))
    conteudo = ru.substituir_texto(bloco.get("tx_conteudo"), valores)
    linhas = [l for l in _linhas_conteudo(conteudo) if l.strip() != ""]
    if not linhas:
        return
    tabela = doc.add_table(rows=0, cols=2)
    tabela.style = "Table Grid"
    for linha in linhas:
        chave, _, valor = linha.partition("|")
        row = tabela.add_row().cells
        _cell(row[0], chave.strip(), tamanho=11, negrito=True, cor_fundo=CINZA)
        _cell(row[1], valor.strip(), tamanho=11)


def _origem_extracao(registro) -> str:
    """De qual UFDR o registro veio (arquivo + aparelho). Vazio para origens que não são de
    extração de aparelho (ex.: RIF do SISCOAF)."""
    arquivo = registro.get("no_arquivo")
    dispositivo = registro.get("no_dispositivo")
    if not arquivo and not dispositivo:
        return "—"
    partes = [p for p in (arquivo, dispositivo) if p]
    return "\n".join(partes)


def _texto_do_comentario(comentario, reescritas: dict | None) -> str:
    """Comentário do analista como ele vai sair no documento: o que foi digitado na marcação ou,
    quando a geração pediu "Reescrever comentários do analista por IA", a versão reescrita.

    `reescritas` é o mapa {original: reescrito} que gerar_relatorio_docx monta ANTES de desenhar os
    blocos (ru.reescrever_comentarios_ia) — aqui não há chamada de IA nenhuma. Texto fora do mapa
    (IA não respondeu, provedor fora ou opção desligada) sai ORIGINAL: anotação de investigador não
    desaparece do relatório por causa de uma chamada que falhou."""
    texto = (comentario or "").strip()
    if not texto or not reescritas:
        return texto
    return reescritas.get(texto) or texto


def _bloco_marcacoes(doc, bloco, valores, marcacoes, anexos_por_marcacao=None, reescritas=None):
    """Registros marcados para o relatório, agrupados por tipo de origem (aplicativo), com a
    extração (.ufdr) de onde cada um veio. As mensagens de WhatsApp já detalhadas num bloco
    'whatsapp' do mesmo padrão são excluídas aqui para não duplicar."""
    _titulo_secao(doc, ru.substituir_texto(bloco.get("no_titulo"), valores))
    intro = ru.substituir_texto(bloco.get("tx_conteudo"), valores)
    for linha in _linhas_conteudo(intro):
        if linha.strip():
            doc.add_paragraph(linha).alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    if not marcacoes:
        doc.add_paragraph("Nenhum registro marcado para o relatório.")
        return

    # agrupa por tipo de origem, preservando a ordem em que aparecem
    grupos: dict[str, list] = {}
    for m in marcacoes:
        grupos.setdefault(m["tp_origem"], []).append(m)

    for tp, itens in grupos.items():
        _titulo_secao(doc, ru.ROTULO_TP_ORIGEM.get(tp, tp) + f" ({len(itens)})", nivel=2)
        tabela = doc.add_table(rows=1, cols=4)
        tabela.style = "Table Grid"
        for c, titulo in zip(tabela.rows[0].cells, ["Extração (UFDR)", "Data", "Conteúdo", "Observação"]):
            _cell(c, titulo, tamanho=9, negrito=True, cor_fundo=CINZA)
        # 'arquivo' é arquivo do aparelho, não anexo de mensagem — a frase de indisponibilidade
        # não pode dizer "de mensagem" aí
        de_mensagem = tp != "arquivo"
        for m in itens:
            anexos = (anexos_por_marcacao or {}).get(m["id_marcacao"])
            row = tabela.add_row().cells
            _cell(row[0], _origem_extracao(m), tamanho=8)
            _cell(row[1], ru.formatar_data_hora(m.get("dt_origem")), tamanho=8)
            corpo, declarar = _corpo_do_registro(m.get("rotulo_origem"), anexos, de_mensagem)
            _cell(row[2], corpo, tamanho=8)
            # anexo do registro (foto de uma mensagem, por exemplo) junto do conteúdo
            _anexos_na_celula(row[2], anexos, IMG_ANEXO_MARCACAO_CM, de_mensagem=de_mensagem,
                              declarar_indisponivel=declarar, com_hash=True)
            # "Observação" é o comentário do analista, o mesmo texto que o bloco 'chat' imprime
            # depois do trecho: se a geração pediu a reescrita por IA, vale para os dois
            _cell(row[3], _texto_do_comentario(m.get("tx_comentario"), reescritas), tamanho=8)


def _resumos_ia_por_marcador(conv, anonimizador=None, id_usuario=None) -> list[tuple[str, str]]:
    """Resumo de IA de uma conversa, um por MARCADOR presente nela (ver ru.resumir_mensagens_ia).

    O agrupamento era por cor e o resumo saía rotulado "Vermelho:" — agora sai "TRÁFICO DE
    DROGAS:", que é o que o investigador realmente quis dizer. A chave é o conjunto de nomes
    (ru.rotulo_marcador), não um marcador por vez: uma mensagem em duas etiquetas entra num
    grupo só, senão o mesmo trecho seria resumido duas vezes."""
    grupos: dict[str, list] = {}
    for msg in conv["mensagens"]:
        grupos.setdefault(ru.rotulo_marcador(msg), []).append(msg)
    resumos = []
    for rotulo, msgs in grupos.items():
        texto = ru.resumir_mensagens_ia(msgs, conv["titulo"], anonimizador, id_usuario)
        if texto:
            resumos.append((rotulo, texto))
    return resumos


def _inserir_resumo_ia(doc, conv, anonimizador=None, id_usuario=None) -> None:
    resumos = _resumos_ia_por_marcador(conv, anonimizador, id_usuario)
    if not resumos:
        return
    p = doc.add_paragraph()
    p.add_run("Resumo:" if len(resumos) == 1 else "Resumos:").bold = True
    for rotulo, texto in resumos:
        linha = doc.add_paragraph()
        linha.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        if len(resumos) > 1:
            r = linha.add_run(rotulo + ": ")
            r.bold = True
            r.font.size = Pt(9)
        run = linha.add_run(texto)
        run.font.size = Pt(10)


def _texto_salto_conversa(qt_omitidas: int) -> str:
    """Aviso que separa dois trechos da mesma conversa: quantas mensagens do fio original ficaram
    de fora entre um e outro (ver ru.dividir_em_trechos). Sem ele, mensagens marcadas com dezenas
    de outras no meio saem emendadas, como se fossem um diálogo seguido."""
    quantas = "1 mensagem não marcada" if qt_omitidas == 1 else f"{qt_omitidas} mensagens não marcadas"
    return f"Continuação da mesma conversa — {quantas} entre este trecho e o anterior."


def _linha_salto_tabela(tabela, qt_omitidas: int) -> None:
    """O mesmo aviso de salto, como linha cinza mesclada de ponta a ponta da tabela do bloco
    'whatsapp' (o bloco 'chat' usa _aviso_salto_chat, entre uma tela de celular e a seguinte)."""
    celulas = tabela.add_row().cells
    celula = celulas[0].merge(celulas[-1])
    celula.text = ""
    _shade(celula, CINZA)
    p = celula.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run(_texto_salto_conversa(qt_omitidas))
    run.italic = True
    run.font.size = Pt(8)


def _bloco_whatsapp(doc, bloco, valores, conversas, comentario_ia=False, anonimizador=None,
                    id_usuario=None):
    titulo = ru.substituir_texto(bloco.get("no_titulo"), valores)
    _titulo_secao(doc, titulo)
    # número da seção (ex.: "6. WhatsApp" → 6) para numerar as subseções 6.1, 6.2...
    m = re.match(r"\s*(\d+)", titulo or "")
    prefixo = (m.group(1) + ".") if m else ""

    intro = ru.substituir_texto(bloco.get("tx_conteudo"), valores)
    for linha in _linhas_conteudo(intro):
        if linha.strip():
            doc.add_paragraph(linha).alignment = WD_ALIGN_PARAGRAPH.JUSTIFY

    if not conversas:
        doc.add_paragraph("Nenhuma mensagem de WhatsApp marcada para o relatório.")
        return

    for i, conv in enumerate(conversas, start=1):
        sub = (prefixo + str(i) + ". ") if prefixo else ""
        _titulo_secao(doc, sub + "Chat com " + conv["titulo"], nivel=2)
        origem = _origem_extracao(conv)
        if origem and origem != "—":
            p = doc.add_paragraph()
            r = p.add_run("Extração: " + origem.replace("\n", " · "))
            r.font.size = Pt(9)
            r.italic = True
        if comentario_ia:
            _inserir_resumo_ia(doc, conv, anonimizador, id_usuario)
        rotulos_distintos = {ru.rotulo_marcador(m) for m in conv["mensagens"]}
        mostrar_cor = comentario_ia and len(rotulos_distintos) > 1
        tabela = doc.add_table(rows=1, cols=len(COLUNAS_WPP))
        tabela.style = "Table Grid"
        for c, nome in zip(tabela.rows[0].cells, COLUNAS_WPP):
            _cell(c, nome, tamanho=9, negrito=True, cor_fundo=CINZA)
        # a tabela continua sendo uma só, mas cada salto no fio original entra como linha de
        # aviso: linhas seguidas não podem passar por mensagens seguidas da conversa
        trechos = conv.get("trechos") or [{"qt_omitidas_antes": 0, "mensagens": conv["mensagens"]}]
        linhas = []   # (mensagem, quantas mensagens do fio ficaram de fora logo antes dela)
        for trecho in trechos:
            for pos, msg in enumerate(trecho["mensagens"]):
                linhas.append((msg, trecho.get("qt_omitidas_antes") if pos == 0 else 0))
        for msg, salto in linhas:
            if salto:
                _linha_salto_tabela(tabela, salto)
            cor = VERDE if msg.get("in_enviada") else AZUL
            anexos = msg.get("anexos") or []
            transcricoes = "\n".join(a["transcricao"] for a in anexos if a.get("transcricao"))
            de = "\n".join(p for p in (msg.get("remetente_jid"), msg.get("remetente")) if p)
            if mostrar_cor:
                etiqueta = "[" + ru.rotulo_marcador(msg) + "]"
                de = (etiqueta + "\n" + de) if de else etiqueta
            row = tabela.add_row().cells
            _cell(row[0], de, tamanho=8, cor_fundo=cor)
            _cell(row[1], ru.formatar_data_hora(msg.get("dt_mensagem")), tamanho=8, cor_fundo=cor)
            # sem texto (mensagem apagada, só mídia): declara o que faltou em vez de célula vazia
            corpo, declarar = _corpo_do_registro(
                msg.get("tx_mensagem"), anexos, lat=msg.get("vl_latitude"), lon=msg.get("vl_longitude")
            )
            if msg.get("in_encaminhada"):
                corpo = ("↪ Encaminhada\n" + corpo) if corpo else "↪ Encaminhada"
            _cell(row[2], corpo, tamanho=8, cor_fundo=cor)
            # coluna "Anexo": nome do arquivo e, se for imagem, a imagem embaixo do nome
            _anexos_na_celula(row[3], anexos, IMG_ANEXO_WPP_CM, declarar_indisponivel=declarar,
                              com_hash=True)
            _shade(row[3], cor)
            _cell(row[4], transcricoes, tamanho=8, cor_fundo=cor)


def _largura_texto_cm(texto: str, pontos: float) -> float:
    """Largura aproximada de um trecho de texto sem quebra de linha."""
    return len(texto or "") * pontos * _CM_POR_CARACTERE_PT


def _faixa_chat(tabela, larguras: list[int], coluna_cm: float):
    """Acrescenta uma linha à grade do chat mesclando-a nos trechos pedidos (em colunas da
    grade, somando CHAT_COLUNAS) e devolve (linha, [células]). É esse mesclar que permite bolha
    curta, selo de data centralizado e aviso de sistema estreito na mesma tabela."""
    row = tabela.add_row()
    celulas_grade = row.cells
    resultado = []
    i = 0
    for n in larguras:
        celula = celulas_grade[i] if n == 1 else celulas_grade[i].merge(celulas_grade[i + n - 1])
        celula.width = Cm(n * coluna_cm)
        resultado.append(celula)
        i += n
    return row, resultado


def _colunas_centralizadas(texto: str, pontos: float, coluna_cm: float) -> list[int]:
    """Trechos [lado, selo, lado] para um selo centralizado na grade: o selo cresce com o texto,
    sempre com um número PAR de colunas, para sobrar a mesma quantidade dos dois lados."""
    n = math.ceil((_largura_texto_cm(texto, pontos) + CHAT_FOLGA_CM) / coluna_cm)
    n = min(CHAT_COLUNAS - 2, max(2, n + (n % 2)))
    lado = (CHAT_COLUNAS - n) // 2
    return [lado, n, lado]


def _selo_chat(tabela, texto: str, coluna_cm: float, cor_fundo: str, italico: bool = False,
               pontos: float = 8) -> None:
    """Selo cinza centralizado sobre o papel de parede: divisor de data e aviso de sistema. O
    WhatsApp mostra os dois assim — uma etiqueta curta no meio da tela, não uma barra de ponta a
    ponta (que é o que uma linha mesclada inteira vira, e que denuncia a tabela por baixo)."""
    larguras = _colunas_centralizadas(texto, pontos, coluna_cm)
    _, celulas = _faixa_chat(tabela, larguras, coluna_cm)
    for lateral in (celulas[0], celulas[2]):
        lateral.text = ""
        _shade(lateral, CHAT_FUNDO)
    selo = celulas[1]
    selo.text = ""
    _shade(selo, cor_fundo)
    _margens_celula(selo, 0.05, 0.15)
    p = selo.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_after = Pt(0)
    run = p.add_run(texto)
    run.font.size = Pt(pontos)
    run.font.name = CHAT_FONTE
    run.italic = italico
    run.font.color.rgb = RGBColor.from_string("54656F")


def _linha_data_chat(tabela, texto: str, coluna_cm: float) -> None:
    """Divisor de data, do jeito que apps de mensagem separam os dias na tela."""
    _selo_chat(tabela, texto, coluna_cm, CHAT_COR_DIVISOR_DATA)


def _linha_espaco_chat(tabela, coluna_cm: float, altura_pt: int = CHAT_ESPACO_OUTRO_PT) -> None:
    """Linha vazia e baixa entre duas bolhas — sem ela as bolhas saem coladas, uma na outra, e o
    mockup perde a cara de conversa real. Entre mensagens seguidas do mesmo remetente o espaço é
    menor, como no app, o que agrupa a sequência visualmente."""
    row, celulas = _faixa_chat(tabela, [CHAT_COLUNAS], coluna_cm)
    row.height = Pt(altura_pt)
    row.height_rule = WD_ROW_HEIGHT_RULE.EXACTLY
    celula = celulas[0]
    celula.text = ""
    _shade(celula, CHAT_FUNDO)
    _margens_celula(celula, 0, 0)
    p = celula.paragraphs[0]
    p.paragraph_format.space_after = Pt(0)
    p.add_run().font.size = Pt(1)


def _linha_sistema_chat(tabela, texto: str, coluna_cm: float, carimbo: str | None = None) -> None:
    """Evento de sistema embutido na conversa (ligação perdida, criação de grupo etc.) — o
    Cellebrite grava esses eventos com o remetente sintético "System Message"; o WhatsApp de
    verdade mostra isso como um aviso cinza centralizado, sem bolha e sem remetente, e é isso
    que se replica aqui em vez de uma bolha comum.

    O carimbo completo (data, hora e fuso) acompanha o texto do evento: sem ele, a ligação
    saía sem data e hora, e cada registro do relatório tem que ser datável sozinho, sem
    depender do divisor de data que ficou para trás — a mesma regra da bolha comum."""
    if carimbo:
        texto = f"{texto}  ·  {carimbo}"
    _selo_chat(tabela, texto, coluna_cm, CHAT_COR_DIVISOR_DATA, italico=True)


def _barra_titulo_chat(tabela, titulo: str, subtitulo: str, coluna_cm: float) -> None:
    """Barra verde-escura do topo da tela do WhatsApp, com o nome do contato/grupo e, embaixo, o
    aplicativo e a extração de origem. Repete no alto de cada página quando a conversa é longa."""
    row, celulas = _faixa_chat(tabela, [CHAT_COLUNAS], coluna_cm)
    _repetir_no_topo(row)
    celula = celulas[0]
    celula.text = ""
    _shade(celula, CHAT_BARRA)
    _margens_celula(celula, 0.12, 0.2)
    p = celula.paragraphs[0]
    p.paragraph_format.space_after = Pt(0)
    run = p.add_run(titulo)
    run.bold = True
    run.font.size = Pt(11)
    run.font.name = CHAT_FONTE
    run.font.color.rgb = RGBColor.from_string(CHAT_BARRA_TEXTO)
    if subtitulo:
        p2 = celula.add_paragraph()
        p2.paragraph_format.space_after = Pt(0)
        run = p2.add_run(subtitulo)
        run.font.size = Pt(7.5)
        run.font.name = CHAT_FONTE
        run.font.color.rgb = RGBColor.from_string(CHAT_BARRA_SUBTEXTO)


def _rotulo_remetente(msg, mostrar: bool) -> str:
    """Nome (e o telefone/JID entre parênteses, quando os dois existem) que encabeça a bolha, ou
    "" quando a bolha não leva rótulo. O WhatsApp só nomeia quem fala em grupo, e só na primeira
    de uma sequência do mesmo remetente — mas o relatório é peça de investigação, então o número
    acompanha o nome (para a atribuição não depender da agenda do aparelho) e a bolha ENVIADA
    também é nomeada: no app o "eu" é óbvio para quem está com o celular na mão, no relatório
    não é, e o leitor precisa saber de qual linha partiu a mensagem.

    O chamador nomeia TODAS as bolhas: bolha citada em separado, ou lida numa página em que a
    sequência já começou, tem que dizer sozinha de quem é.

    Mensagem enviada cujo remetente a extração não gravou cai em "Dono do aparelho" — a
    identificação está no cabeçalho da conversa e na seção de extrações do relatório."""
    if not mostrar:
        return ""
    nome = (msg.get("remetente") or "").strip()
    jid = (msg.get("remetente_jid") or "").strip()
    if nome and jid and jid not in nome:
        return f"{nome} ({jid})"
    return nome or jid or ("Dono do aparelho" if msg.get("in_enviada") else "")


# Mídia de visualização única: mesma marca da tela de análise (whatsapp.js: rótulo
# SelfDestruct + anexo sem binário): o rótulo sozinho também marca mensagens temporárias
# comuns cujo arquivo veio, daí exigir o anexo indisponível.
TEXTO_VISUALIZACAO_UNICA = "⓵ Visualização única"


def _eh_visualizacao_unica(msg, anexos) -> bool:
    """A mensagem traz mídia de visualização única?"""
    if not msg.get("in_temporaria"):
        return False
    return any(not _anexo_disponivel(a) for a in (anexos or []))


# Rótulo curto da mídia citada, como a tela mostra na tarja de resposta (Foto/Áudio/Vídeo…).
_ROTULO_CITADA_MIDIA = {"image": "Foto", "audio": "Áudio", "video": "Vídeo", "pdf": "Documento"}
_ICONES_CITADA_MIDIA = {"image": "🖼️", "audio": "🎵", "video": "🎞️", "pdf": "📄", "generico": "📎"}


def _resumo_citada(citada: dict) -> str:
    """Prévia de uma linha da mensagem citada, como a tela desenha na tarja de resposta:
    o texto, ou — quando a citada não tem texto — o que ela era (mídia, localização, apagada)."""
    texto = ((citada.get("texto") or "").strip())
    if texto:
        return " ".join(texto.split())
    anexo = citada.get("anexo") or {}
    if anexo:
        tipo = (anexo.get("tipo") or "").lower()
        cat = "generico"
        for chave in ("image", "audio", "video", "pdf"):
            if chave in tipo:
                cat = chave
                break
        rotulo = _ROTULO_CITADA_MIDIA.get(cat, "Anexo")
        titulo = (anexo.get("titulo") or anexo.get("nome") or "").strip()
        icone = _ICONES_CITADA_MIDIA.get(cat, "📎")
        return f"{icone} {rotulo}" + (f" — {titulo}" if titulo else "")
    if citada.get("tem_coordenada"):
        return "📍 Localização compartilhada"
    if citada.get("apagada"):
        return "Mensagem apagada pelo remetente"
    return "(sem conteúdo)"


def _origem_citada(citada: dict) -> str:
    """De onde veio a mensagem citada, quando não está na conversa aberta — o caso comum é a
    resposta a um Status, que o Cellebrite guarda num pseudo-chat à parte (mesma regra da tela)."""
    if not citada.get("outra_conversa"):
        return ""
    tp = citada.get("tp_conversa")
    if tp == "status":
        return "Status citado"
    if tp == "canal":
        return "Citada de um canal"
    if tp == "transmissao":
        return "Citada de uma lista de transmissão"
    nome = (citada.get("conversa_nome") or "").strip()
    return "Citada de outra conversa" + (": " + nome if nome else "")


def _texto_citacao_bolha(msg) -> str | None:
    """Linha de citação que abre a bolha quando a mensagem responde a outra, ou None quando não
    é resposta. Com a original localizada, identifica autor, origem e trecho; sem ela (apagada
    antes da coleta ou fora do recorte), declara — a resposta nunca sai como mensagem solta."""
    citada = msg.get("citada")
    if citada:
        autor = "Você" if citada.get("enviada") else ((citada.get("remetente") or "").strip()
                                                      or "(sem identificação)")
        partes = ["↩ Em resposta a " + autor]
        origem = _origem_citada(citada)
        if origem:
            partes.append(origem)
        carimbo = ru.formatar_data_hora(citada.get("dt_mensagem"))
        if carimbo:
            partes.append(carimbo)
        trecho = _resumo_citada(citada)
        if len(trecho) > COMENT_MAX_CHARS_CITACAO:
            trecho = trecho[:COMENT_MAX_CHARS_CITACAO].rstrip() + "…"
        return " · ".join(partes) + ": “" + trecho + "”"
    if (msg.get("nu_ident_citado") or "").strip():
        return "↩ Em resposta a mensagem não recuperada na extração."
    return None


def _colunas_da_bolha(corpo: str, anexos, rotulo: str, carimbo: str, coluna_cm: float,
                       msg=None) -> int:
    """Quantas colunas da grade a bolha ocupa — a bolha do WhatsApp acompanha o tamanho do
    texto, e é isso que se estima aqui: a maior linha do conteúdo (o Word ainda quebra o que
    passar do máximo), com piso para caber o carimbo de data/hora e teto de CHAT_MAX_COLUNAS.
    Imagem entra como CHAT_IMG_CM, o lado do quadrado em que ela é encaixada — ir direto ao teto
    deixava um vão branco à direita da foto dentro da bolha."""
    larguras = [_largura_texto_cm(linha, 10) for linha in _linhas_conteudo(corpo)]
    if any(_eh_imagem(a) for a in anexos or []):
        larguras.append(CHAT_IMG_CM)
    if rotulo:
        larguras.append(_largura_texto_cm(rotulo, 9) * 1.08)  # negrito ocupa um pouco mais
    # a tarja de resposta e o aviso de visualização única abrem a bolha e também pedem
    # largura: sem entrar na conta, quebravam em duas linhas dentro de bolha estreita
    if msg is not None:
        citacao = _texto_citacao_bolha(msg)
        if citacao:
            larguras.append(_largura_texto_cm(citacao, 8))
        if _eh_visualizacao_unica(msg, anexos):
            larguras.append(_largura_texto_cm(TEXTO_VISUALIZACAO_UNICA, 8))
    # o carimbo completo (data + hora + fuso) é largo e não pode quebrar em duas linhas no
    # rodapé da bolha: numa mensagem curta é ele, e não o texto, que define a largura
    larguras.append(_largura_texto_cm(carimbo, 7))
    for anexo in anexos or []:
        larguras.append(_largura_texto_cm(anexo.get("nome") or "", 8))
        # a linha "HASH - MD5: …" é maior que o nome do arquivo na maioria dos anexos: sem
        # entrar na conta, ela quebrava em duas dentro da bolha
        if _anexo_disponivel(anexo):
            larguras.append(_largura_texto_cm(_ROTULO_HASH_MD5 + (anexo.get("hash_md5") or ""), 7))
        if anexo.get("transcricao"):
            larguras.append(_largura_texto_cm("Transcrição " + anexo["transcricao"], 10))
    n = math.ceil((max(larguras or [0]) + CHAT_FOLGA_CM) / coluna_cm)
    return max(CHAT_MIN_COLUNAS, min(CHAT_MAX_COLUNAS, n))


def _bolha_mensagem(cell, msg, anexos, cor_fundo: str, rotulo: str, mostrar_cor: bool = False) -> None:
    """Preenche a bolha de uma mensagem: rótulo do remetente (só do lado de quem não é o dono do
    aparelho — o dono é óbvio pelo lado/cor, como no app), corpo, anexo (com a imagem, se for o
    caso), transcrição de áudio e a hora no rodapé da bolha, como o WhatsApp mostra.

    mostrar_cor liga a etiqueta com a cor da marcação (● Verde-água) no topo da bolha — só faz
    sentido quando a conversa tem mais de uma cor marcada e o comentário de IA está ligado
    (é o resumo por cor que cria a ambiguidade de "qual bolha é de qual cor")."""
    cell.text = ""
    # `cell.text = ""` deixa um run VAZIO no primeiro parágrafo, e run vazio não é parágrafo
    # livre para o _proximo_paragrafo: sem tirá-lo, a bolha começava com uma linha em branco
    # sempre que o primeiro conteúdo vinha do anexo (áudio sem texto, por exemplo)
    primeiro = cell.paragraphs[0]
    for run in list(primeiro.runs):
        run._r.getparent().remove(run._r)
    _shade(cell, cor_fundo)
    _margens_celula(cell, 0.1, 0.18)

    def paragrafo():
        # mesmo alocador do _anexos_na_celula, para o nome do anexo, a transcrição e a hora
        # saírem sempre na ordem em que são escritos
        p = _proximo_paragrafo(cell)
        p.paragraph_format.space_after = Pt(0)
        return p

    def texto(conteudo, tamanho, cor_hex, negrito=False, italico=False, p=None):
        run = (p or paragrafo()).add_run(conteudo)
        run.font.size = Pt(tamanho)
        run.font.name = CHAT_FONTE
        run.bold = negrito
        run.italic = italico
        run.font.color.rgb = RGBColor.from_string(cor_hex)
        return run

    if mostrar_cor:
        cor_marcacao = ru.cor_marcador(msg)
        texto("● " + ru.rotulo_marcador(msg), 7, (cor_marcacao or "#000000").lstrip("#"), negrito=True)

    if rotulo:
        texto(rotulo, 9, _cor_remetente(rotulo), negrito=True)

    # marca de encaminhada: metadado, não conteúdo — vem discreto antes do corpo, como na tela
    # (whatsapp.js) e no IPED
    if msg.get("in_encaminhada"):
        texto("↪ Encaminhada", 9, CHAT_COR_HORA, italico=True)

    # resposta a outra mensagem: a tarja de citação abre a bolha, como no WhatsApp e no IPED —
    # sem ela a resposta saía como mensagem solta e o leitor não sabia a que ela se referia
    citacao = _texto_citacao_bolha(msg)
    if citacao:
        p = paragrafo()
        p.paragraph_format.left_indent = Cm(0.1)
        _borda_esquerda_paragrafo(p, COMENT_CITACAO_BARRA, COMENT_CITACAO_PT)
        run = p.add_run(citacao)
        run.font.size = Pt(8)
        run.font.name = CHAT_FONTE
        run.italic = True
        run.font.color.rgb = RGBColor.from_string(CHAT_COR_HORA)

    # sem texto: a bolha declara o que faltou (mídia apagada) ou fica só com o anexo, quando o
    # anexo em si é o conteúdo — bolha vazia é que não pode sair
    corpo, declarar = _corpo_do_registro(
        msg.get("tx_mensagem"), anexos, lat=msg.get("vl_latitude"), lon=msg.get("vl_longitude")
    )
    if corpo:
        for linha in _linhas_conteudo(corpo):
            texto(linha, 10, CHAT_COR_TEXTO)

    # visualização única: o binário nunca vem na extração, e sem o aviso a bolha parecia só
    # mais uma mídia indisponível — mesma marca da tela de análise (whatsapp.js)
    if _eh_visualizacao_unica(msg, anexos):
        texto(TEXTO_VISUALIZACAO_UNICA, 8, CHAT_COR_HORA, italico=True)

    _anexos_na_celula(cell, anexos, CHAT_IMG_CM, declarar_indisponivel=declarar, com_hash=True,
                      cor_metadados=CHAT_COR_HORA)

    # a transcrição não é conteúdo da mensagem, é leitura do áudio feita depois — sai com rótulo
    # próprio e em itálico, para nunca se confundir com o que a pessoa digitou; em preto, como
    # o corpo: o cinza fica para o metadado de arquivo (nome e hash)
    for anexo in anexos or []:
        if anexo.get("transcricao"):
            p = paragrafo()
            texto("Transcrição: ", 10, CHAT_COR_TEXTO, negrito=True, p=p)
            texto(anexo["transcricao"], 10, CHAT_COR_TEXTO, italico=True, p=p)

    # carimbo COMPLETO (data, hora e fuso), não a hora curta do app: o relatório é peça de
    # investigação e cada mensagem tem que ser datável sozinha, sem depender do divisor de data
    # que ficou páginas atrás
    carimbo = ru.formatar_data_hora(msg.get("dt_mensagem"))
    if carimbo:
        p = paragrafo()
        p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        run = p.add_run(carimbo)
        run.font.size = Pt(7)
        run.font.name = CHAT_FONTE
        run.font.color.rgb = RGBColor.from_string(CHAT_COR_HORA)

    # o que o _anexos_na_celula escreveu vem com o estilo Normal do relatório (Times New Roman e
    # 8pt de espaço depois do parágrafo): dentro da bolha isso destoa do resto e abre buracos
    # entre as linhas, então a bolha inteira é uniformizada aqui no fim
    for p in cell.paragraphs:
        p.paragraph_format.space_after = Pt(0)
        p.paragraph_format.line_spacing = 1.0
        for run in p.runs:
            if run.font.name is None:
                run.font.name = CHAT_FONTE


def _aviso_salto_chat(doc, qt_omitidas: int) -> None:
    """Linha de corte entre duas telas de chat da mesma conversa."""
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(6)
    p.paragraph_format.space_after = Pt(6)
    run = p.add_run(_texto_salto_conversa(qt_omitidas))
    run.italic = True
    run.font.size = Pt(8)
    run.font.color.rgb = RGBColor.from_string(CHAT_COR_HORA)


def _tela_chat(doc, conv, mensagens, mostrar_cor: bool) -> None:
    """Desenha UMA tela de celular (barra de título + bolhas) com as mensagens recebidas. Uma
    conversa rende uma tela por trecho contíguo, não uma só com tudo emendado."""
    coluna_cm = _largura_util_cm(doc) / CHAT_COLUNAS
    tabela = doc.add_table(rows=0, cols=CHAT_COLUNAS)
    tabela.autofit = False
    _bordas_tabela(tabela, CHAT_MOLDURA)
    for coluna in tabela.columns:
        coluna.width = Cm(coluna_cm)

    subtitulo = " · ".join(p for p in (conv.get("tp_app"), _origem_extracao(conv).replace("\n", " · "))
                           if p and p != "—")
    _barra_titulo_chat(tabela, conv["titulo"], subtitulo, coluna_cm)

    data_atual = None
    anterior = None   # (enviada, remetente) da bolha anterior, para o espaço entre bolhas
    for msg in mensagens:
        dt_msg = msg.get("dt_mensagem")
        dt_local = ru.para_hora_local(dt_msg) if isinstance(dt_msg, datetime) else dt_msg
        data_str = dt_local.strftime("%d/%m/%Y") if isinstance(dt_local, datetime) else None
        sistema = (msg.get("remetente") or "").strip().lower() == "system message"
        enviada = bool(msg.get("in_enviada"))
        quem = None if sistema else (enviada, (msg.get("remetente") or msg.get("remetente_jid") or "").strip())

        if data_str and data_str != data_atual:
            _linha_espaco_chat(tabela, coluna_cm, CHAT_ESPACO_OUTRO_PT)
            _linha_data_chat(tabela, data_str, coluna_cm)
            data_atual = data_str
            anterior = None
        _linha_espaco_chat(
            tabela, coluna_cm,
            CHAT_ESPACO_MESMO_PT if (quem is not None and quem == anterior) else CHAT_ESPACO_OUTRO_PT,
        )

        if sistema:
            # ligação perdida, criação de grupo etc. — evento de sistema, não mensagem de
            # alguém; sai como aviso centralizado, não como bolha de um dos lados
            _linha_sistema_chat(tabela, msg.get("tx_mensagem") or "(evento do sistema)", coluna_cm,
                                ru.formatar_data_hora(msg.get("dt_mensagem")))
            anterior = None
            continue

        # TODA bolha é nomeada, dos dois lados. O WhatsApp nomeia só a primeira de uma
        # sequência (o resto o usuário deduz pela posição na tela), mas o relatório é peça
        # de investigação: cada bolha citada em separado, ou lida numa página em que a
        # sequência já começou, precisa dizer sozinha de quem é.
        rotulo = _rotulo_remetente(msg, True)

        anexos = msg.get("anexos") or []
        corpo, _ = _corpo_do_registro(
            msg.get("tx_mensagem"), anexos, lat=msg.get("vl_latitude"), lon=msg.get("vl_longitude")
        )
        n = _colunas_da_bolha(corpo, anexos, rotulo, ru.formatar_data_hora(msg.get("dt_mensagem")),
                               coluna_cm, msg)
        resto = CHAT_COLUNAS - CHAT_MARGEM_COLUNAS - n
        larguras = [resto, n, CHAT_MARGEM_COLUNAS] if enviada else [CHAT_MARGEM_COLUNAS, n, resto]
        _, celulas = _faixa_chat(tabela, larguras, coluna_cm)
        bolha = celulas[1]
        for vazia in (celulas[0], celulas[2]):
            vazia.text = ""
            _shade(vazia, CHAT_FUNDO)
        cor_bolha = CHAT_BOLHA_ENVIADA if enviada else CHAT_BOLHA_RECEBIDA
        _bolha_mensagem(bolha, msg, anexos, cor_bolha, rotulo, mostrar_cor)
        anterior = quem

    _linha_espaco_chat(tabela, coluna_cm, CHAT_ESPACO_OUTRO_PT)


def _caixa_comentario(doc):
    """Abre a caixa de comentários: uma tabela de célula única, com o papel creme e a barra de
    acento à esquerda, e devolve a célula para quem vai escrever dentro.

    Tabela de uma célula, e não parágrafos sombreados, porque é o sombreado da CÉLULA que
    envolve várias anotações num bloco só — parágrafo a parágrafo, o Word deixa um risco branco
    entre eles e a caixa deixa de parecer uma caixa."""
    espaco = doc.add_paragraph()   # respiro entre a "tela" do celular e a caixa
    espaco.paragraph_format.space_after = Pt(0)
    espaco.add_run().font.size = Pt(4)

    largura = _largura_util_cm(doc)
    tabela = doc.add_table(rows=1, cols=1)
    tabela.autofit = False
    tabela.columns[0].width = Cm(largura)
    _bordas_tabela(tabela)
    celula = tabela.rows[0].cells[0]
    celula.width = Cm(largura)
    celula.text = ""
    # `cell.text = ""` deixa um run VAZIO no primeiro parágrafo, e run vazio faz o
    # _proximo_paragrafo abrir OUTRO parágrafo: sem tirá-lo, a caixa começava com uma linha em
    # branco antes do título (mesma armadilha documentada em _bolha_mensagem)
    for run in list(celula.paragraphs[0].runs):
        run._r.getparent().remove(run._r)
    _bordas_celula(celula, left=(COMENT_BARRA, COMENT_BARRA_PT))
    _shade(celula, COMENT_FUNDO)
    _margens_celula(celula, 0.18, 0.28)
    return celula


def _titulo_caixa_comentario(celula, texto: str) -> None:
    """Cabeçalho da caixa, em versalete: diz de quem é a voz que vem a seguir sem competir com os
    títulos numerados das seções do relatório (que são o índice do documento)."""
    p = _proximo_paragrafo(celula)
    p.paragraph_format.space_after = Pt(4)
    run = p.add_run(texto)
    run.bold = True
    run.font.small_caps = True   # em Run isso seria um atributo solto, sem efeito no documento
    run.font.size = Pt(9)
    run.font.color.rgb = RGBColor.from_string(COMENT_COR_ROTULO)


_ROTULO_MIDIA_CITADA = {"imagem": "Imagem", "audio": "Áudio", "video": "Vídeo"}


def _texto_citado(msg) -> str:
    """Trecho da mensagem comentada que vai na citação.

    Ordem: o texto da mensagem; senão a mídia dela DESCRITA (categoria e nome do arquivo, mais a
    transcrição quando existe); senão o que a bolha mostrou no lugar do corpo (mídia
    indisponível, localização). A bolha pode se dar ao luxo de exibir a foto e o nome do anexo
    logo abaixo do corpo vazio — a citação não exibe nada disso, e uma citação que sai "—" não
    identifica bolha nenhuma, que é justamente o que ela existe para fazer.

    Cortada em COMENT_MAX_CHARS_CITACAO: é para RECONHECER a bolha lá em cima, não para
    reproduzi-la de novo."""
    anexos = msg.get("anexos") or []
    corpo = (msg.get("tx_mensagem") or "").strip()
    if not corpo:
        partes = []
        for anexo in anexos:
            rotulo = _ROTULO_MIDIA_CITADA.get(_categoria_anexo(anexo), "Arquivo")
            nome = (anexo.get("nome") or "").strip()
            partes.append(f"{rotulo} ({nome})" if nome else rotulo)
        # a bolha declara "Arquivo ... indisponível." no lugar do corpo; a citação nomeia o
        # arquivo (identifica melhor a bolha) e não pode calar que o binário não veio
        if anexos and not any(_anexo_disponivel(a) for a in anexos):
            partes.append("arquivo indisponível")
        transcricoes = " ".join(a["transcricao"] for a in anexos if a.get("transcricao"))
        if transcricoes.strip():
            partes.append("transcrição: " + transcricoes.strip())
        corpo = " — ".join(partes)
    if not corpo:
        corpo, _ = _corpo_do_registro(
            msg.get("tx_mensagem"), anexos, lat=msg.get("vl_latitude"), lon=msg.get("vl_longitude")
        )
    corpo = " ".join((corpo or "").split())   # a citação é uma linha, não o parágrafo inteiro
    if len(corpo) > COMENT_MAX_CHARS_CITACAO:
        corpo = corpo[:COMENT_MAX_CHARS_CITACAO].rstrip() + "…"
    return corpo or "—"


def _citacao_da_mensagem(celula, msg) -> None:
    """A mensagem comentada, citada acima da anotação: filete à esquerda, remetente na cor que ele
    tem nas bolhas, carimbo completo e o trecho do conteúdo. É o mesmo desenho da resposta do
    WhatsApp, então o leitor já sabe lê-lo — e a entrada passa a ser legível sozinha, sem subir a
    página para descobrir qual bolha foi comentada."""
    p = _proximo_paragrafo(celula)
    p.paragraph_format.space_after = Pt(1)
    p.paragraph_format.left_indent = Cm(0.1)
    _borda_esquerda_paragrafo(p, COMENT_CITACAO_BARRA, COMENT_CITACAO_PT)

    de = _rotulo_remetente(msg, True) or "—"
    run = p.add_run(de)
    run.bold = True
    run.font.size = Pt(8)
    run.font.name = CHAT_FONTE
    run.font.color.rgb = RGBColor.from_string(_cor_remetente(de))

    carimbo = ru.formatar_data_hora(msg.get("dt_mensagem"))
    if carimbo:
        run = p.add_run("  ·  " + carimbo)
        run.font.size = Pt(8)
        run.font.name = CHAT_FONTE
        run.font.color.rgb = RGBColor.from_string(CHAT_COR_HORA)

    p.add_run().add_break()
    run = p.add_run("“" + _texto_citado(msg) + "”")
    run.italic = True
    run.font.size = Pt(8)
    run.font.name = CHAT_FONTE
    run.font.color.rgb = RGBColor.from_string(CHAT_COR_HORA)


def _texto_da_anotacao(celula, texto: str) -> None:
    """A anotação do analista em si — na fonte do relatório, não na do mockup de chat: quem
    escreveu foi ele, e o parágrafo tem que ler como o resto do documento assinado."""
    p = _proximo_paragrafo(celula)
    p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    p.paragraph_format.space_after = Pt(6)
    p.paragraph_format.left_indent = Cm(0.1)
    run = p.add_run(texto)
    run.font.size = Pt(10)


def _comentarios_do_trecho(doc, mensagens, reescritas: dict | None = None) -> None:
    """Comentários que o investigador escreveu nas marcações das mensagens de UM trecho, logo
    depois da tela correspondente, cada um precedido da citação da mensagem que comenta.

    Não sai uma lista só no fim da conversa: quando a conversa rende várias telas, o comentário
    ficava páginas depois da bolha que explica, e o leitor tinha que voltar e casar cada
    comentário com a sua mensagem pela data. Colado no trecho, o comentário é lido junto do
    diálogo que comenta.

    A citação resolve o resto: a referência era só o carimbo de hora no meio do parágrafo, e num
    trecho com várias marcações as linhas ficavam iguais umas às outras."""
    comentarios = [m for m in mensagens if (m.get("tx_comentario") or "").strip()]
    if not comentarios:
        return
    celula = _caixa_comentario(doc)
    _titulo_caixa_comentario(celula, "Comentários do investigador")
    for msg in comentarios:
        _citacao_da_mensagem(celula, msg)
        _texto_da_anotacao(celula, _texto_do_comentario(msg["tx_comentario"], reescritas))
    celula.paragraphs[-1].paragraph_format.space_after = Pt(0)


def _comentario_da_conversa(doc, texto: str) -> None:
    """O comentário da marcação da CONVERSA, na mesma caixa das anotações de mensagem, mas sem
    citação: ele não é de uma bolha, é do fio inteiro. Sai antes da primeira tela."""
    celula = _caixa_comentario(doc)
    _titulo_caixa_comentario(celula, "Comentário do investigador sobre a conversa")
    _texto_da_anotacao(celula, texto)
    celula.paragraphs[-1].paragraph_format.space_after = Pt(0)


def _bloco_chat(doc, bloco, valores, conversas, comentario_ia=False, reescritas=None, anonimizador=None,
                id_usuario=None):
    """Conversas marcadas (WhatsApp, SMS e demais mensageiros) como se fosse a tela de um
    celular: bolhas verdes à direita (dono do aparelho) e azuis à esquerda (demais), separadas
    por divisor de data, com os comentários do investigador depois de cada trecho de conversa —
    em vez da tabela do bloco 'whatsapp'.

    Cada TRECHO contíguo da conversa (ru.dividir_em_trechos) vira uma tela própria, com barra de
    título repetida e o aviso de quantas mensagens ficaram de fora entre uma e outra: marcar três
    mensagens distantes entre si não pode sair como um diálogo seguido de três bolhas."""
    titulo = ru.substituir_texto(bloco.get("no_titulo"), valores)
    _titulo_secao(doc, titulo)
    m = re.match(r"\s*(\d+)", titulo or "")
    prefixo = (m.group(1) + ".") if m else ""

    intro = ru.substituir_texto(bloco.get("tx_conteudo"), valores)
    for linha in _linhas_conteudo(intro):
        if linha.strip():
            doc.add_paragraph(linha).alignment = WD_ALIGN_PARAGRAPH.JUSTIFY

    if not conversas:
        doc.add_paragraph("Nenhuma conversa marcada para o relatório.")
        return

    for i, conv in enumerate(conversas, start=1):
        sub = (prefixo + str(i) + ". ") if prefixo else ""
        cabecalho = sub + "Chat com " + conv["titulo"]
        if conv.get("tp_app"):
            cabecalho += f" ({conv['tp_app']})"
        _titulo_secao(doc, cabecalho, nivel=2)
        if comentario_ia:
            _inserir_resumo_ia(doc, conv, anonimizador, id_usuario)
        # com o resumo por marcador, o leitor precisa saber qual bolha corresponde a qual grupo —
        # só liga a etiqueta quando a conversa realmente mistura marcadores, senão é ruído
        rotulos_distintos = {ru.rotulo_marcador(m) for m in conv["mensagens"]}
        mostrar_cor = comentario_ia and len(rotulos_distintos) > 1

        # o comentário da marcação da CONVERSA vale para o fio todo, não para uma bolha: sai uma
        # vez, antes da primeira tela, e não misturado com os comentários de mensagem (que agora
        # pertencem cada um ao seu trecho)
        comentario_conv = _texto_do_comentario(conv.get("tx_comentario_conversa"), reescritas)
        if comentario_conv:
            _comentario_da_conversa(doc, comentario_conv)

        # conversa vinda de uma origem que não calcula trechos (chamador antigo) sai como antes:
        # uma tela só com tudo o que foi marcado
        trechos = conv.get("trechos") or [{"qt_omitidas_antes": 0, "mensagens": conv["mensagens"]}]
        for trecho in trechos:
            if trecho.get("qt_omitidas_antes"):
                _aviso_salto_chat(doc, trecho["qt_omitidas_antes"])
            _tela_chat(doc, conv, trecho["mensagens"], mostrar_cor)
            # os comentários vêm aqui, entre esta tela e o aviso de salto da próxima: cada trecho
            # é lido com o que o investigador anotou sobre ele
            _comentarios_do_trecho(doc, trecho["mensagens"], reescritas)


def _bloco_extracoes(doc, bloco, valores, extracoes):
    _titulo_secao(doc, ru.substituir_texto(bloco.get("no_titulo"), valores))
    intro = ru.substituir_texto(bloco.get("tx_conteudo"), valores)
    for linha in _linhas_conteudo(intro):
        if linha.strip():
            doc.add_paragraph(linha).alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    if not extracoes:
        doc.add_paragraph("Nenhuma extração neste inquérito.")
        return
    tabela = doc.add_table(rows=1, cols=3)
    tabela.style = "Table Grid"
    for c, titulo in zip(tabela.rows[0].cells, ["Nome do arquivo", "Tamanho (bytes)", "Hash SHA-256"]):
        _cell(c, titulo, tamanho=10, negrito=True, cor_fundo=CINZA)
    # o SHA-256 tem 64 caracteres: sem alargar a coluna dele (e apertar as outras duas) o hash
    # quebra em quatro linhas e a tabela fica ilegível
    tabela.autofit = False
    largura_util = _largura_util_cm(doc)
    for coluna, fracao in zip(tabela.columns, EXTRACOES_FRACOES_COLUNAS):
        coluna.width = Cm(largura_util * fracao)
    for e in extracoes:
        row = tabela.add_row().cells
        _cell(row[0], e.get("no_arquivo") or "", tamanho=10)
        _cell(row[1], ru.formatar_bytes(e.get("tamanho_bytes")), tamanho=10)
        # o hash é calculado na importação (25_rif.sql) e sai pronto no relatório;
        # extração importada antes daquela migração não tem hash gravado, e aí a célula DECLARA
        # isso — célula vazia num relatório de cadeia de custódia não distingue "não calculado"
        # de "o analista esqueceu"
        hash_sha256 = (e.get("co_hash_sha256") or "").strip()
        _cell(row[2], hash_sha256 or TEXTO_HASH_AUSENTE, tamanho=8 if hash_sha256 else 10)


def _celula_hashes(cell, md5, sha1, sha256, tamanho: int = 8) -> None:
    """Os três hashes de um arquivo (MD5, SHA-1 e SHA-256) empilhados na célula, como no botão
    "🔒 Hash" da tela de investigação. Sem nenhum hash gravado (importação anterior a
    25_rif.sql), a célula DECLARA isso — célula vazia num relatório de cadeia de
    custódia não distingue "não calculado" de "o analista esqueceu"."""
    linhas = []
    for rotulo, valor in (("MD5", md5), ("SHA-1", sha1), ("SHA-256", sha256)):
        valor = (valor or "").strip()
        if valor:
            linhas.append(rotulo + ": " + valor)
    if not linhas:
        _cell(cell, TEXTO_HASH_AUSENTE, tamanho=10)
        return
    _cell(cell, "\n".join(linhas), tamanho=tamanho)


def _bloco_rif_arquivos(doc, bloco, valores, arquivos):
    """Arquivos que compõem o(s) RIF(s) escolhido(s) na geração (3 CSVs + PDF), com nome,
    tipo, tamanho e hashes — o espelho de 'extracoes' (Tabela 1 do modelo PF) para o relatório
    de RIF: o documento precisa dizer quais arquivos de origem embasam a análise e que o que
    está no relatório é byte-a-byte o que foi importado."""
    _abre_secao(doc, bloco, valores)
    if not arquivos:
        doc.add_paragraph("Nenhum RIF importado para este inquérito.")
        return
    tabela = _tabela_com_cabecalho(
        doc,
        ["RIF", "Nome do arquivo", "Tipo", "Tamanho (bytes)", "Hashes (MD5/SHA-1/SHA-256)"],
        [round(_largura_util_cm(doc) * f, 3) for f in RIF_ARQUIVOS_FRACOES_COLUNAS],
    )
    for a in arquivos:
        row = tabela.add_row().cells
        _cell(row[0], ("RIF " + (a.get("nu_rif") or "").strip()).strip() or (a.get("no_pasta") or "—"), tamanho=8)
        _cell(row[1], a.get("no_arquivo") or "", tamanho=8)
        _cell(row[2], ROTULO_TIPO_ARQUIVO_RIF.get(a.get("tp_arquivo"), a.get("tp_arquivo") or "—"), tamanho=8)
        _cell(row[3], ru.formatar_bytes(a.get("qt_bytes")), tamanho=8)
        _celula_hashes(row[4], a.get("co_hash_md5"), a.get("co_hash_sha1"), a.get("co_hash_sha256"))


def _bloco_bancario_arquivos(doc, bloco, valores, arquivos):
    """Arquivos de extrato bancário (R304 do SIMBA) escolhidos na geração, com nome, tamanho
    e hashes — o mesmo espelho de 'extracoes' para o relatório de dados bancários."""
    _abre_secao(doc, bloco, valores)
    if not arquivos:
        doc.add_paragraph("Nenhum extrato bancário (SIMBA/R304) importado para este inquérito.")
        return
    tabela = _tabela_com_cabecalho(
        doc,
        ["Nome do arquivo", "Tamanho (bytes)", "Hashes (MD5/SHA-1/SHA-256)"],
        [round(_largura_util_cm(doc) * f, 3) for f in BANCARIO_ARQUIVOS_FRACOES_COLUNAS],
    )
    for a in arquivos:
        row = tabela.add_row().cells
        _cell(row[0], a.get("no_arquivo") or "", tamanho=8)
        _cell(row[1], ru.formatar_bytes(a.get("tamanho_bytes")), tamanho=8)
        _celula_hashes(row[2], a.get("co_hash_md5"), a.get("co_hash_sha1"), a.get("co_hash_sha256"))


def _bloco_contas(doc, bloco, valores, contas):
    _titulo_secao(doc, ru.substituir_texto(bloco.get("no_titulo"), valores))
    intro = ru.substituir_texto(bloco.get("tx_conteudo"), valores)
    for linha in _linhas_conteudo(intro):
        if linha.strip():
            doc.add_paragraph(linha).alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    if not contas:
        doc.add_paragraph("Nenhuma conta de usuário registrada nas extrações.")
        return
    tabela = doc.add_table(rows=1, cols=4)
    tabela.style = "Table Grid"
    for c, titulo in zip(tabela.rows[0].cells, ["Tipo", "Conta / Nome", "Identificador", "Aparelho"]):
        _cell(c, titulo, tamanho=10, negrito=True, cor_fundo=CINZA)
    for co in contas:
        row = tabela.add_row().cells
        _cell(row[0], co.get("tp_usuario") or "", tamanho=10)
        _cell(row[1], co.get("no_dono") or "", tamanho=10)
        _cell(row[2], co.get("nu_identificador") or "", tamanho=10)
        _cell(row[3], co.get("no_dispositivo") or "", tamanho=10)


# ---------- blocos do extrato bancário (SIMBA) ----------

def _abre_secao(doc, bloco, valores):
    """Título + introdução (com as {{variáveis}} substituídas), comuns a todos os blocos que
    montam o conteúdo a partir dos dados do caso — bancário, RIF, IPL, PIX, nuvem e IPED."""
    _titulo_secao(doc, ru.substituir_texto(bloco.get("no_titulo"), valores))
    intro = ru.substituir_texto(bloco.get("tx_conteudo"), valores)
    for linha in _linhas_conteudo(intro):
        if linha.strip():
            doc.add_paragraph(linha).alignment = WD_ALIGN_PARAGRAPH.JUSTIFY


def _tabela_com_cabecalho(doc, titulos, larguras_cm=None):
    tabela = doc.add_table(rows=1, cols=len(titulos))
    tabela.style = "Table Grid"
    for celula, titulo in zip(tabela.rows[0].cells, titulos):
        _cell(celula, titulo, tamanho=8, negrito=True, cor_fundo=CINZA)
    if larguras_cm:
        for linha in tabela.rows:
            for celula, largura in zip(linha.cells, larguras_cm):
                celula.width = Cm(largura)
    return tabela


def _bloco_bancario_contas(doc, bloco, valores, contas, resumo):
    _abre_secao(doc, bloco, valores)
    if not resumo or not contas:
        doc.add_paragraph("Nenhum extrato bancário (SIMBA/R304) importado para este inquérito.")
        return

    # o parágrafo de síntese é o que dá sentido à tabela: sem ele o leitor teria que somar
    # 200 linhas para saber de quanto se está falando
    # o separador de milhar sai num passo próprio: aplicar o replace sobre a frase inteira
    # trocaria também as vírgulas do texto e as decimais dos valores
    qt_lanc = f"{int(resumo['qt_lancamentos'] or 0):,}".replace(",", ".")
    doc.add_paragraph(
        f"Foram analisadas {resumo['qt_contas']} contas bancárias de "
        f"{resumo['qt_titulares']} titulares, no período de "
        f"{ru.formatar_periodo(resumo['dt_inicio'], resumo['dt_fim'])}, totalizando "
        f"{ru.formatar_reais(resumo['vl_credito'])} em créditos e "
        f"{ru.formatar_reais(resumo['vl_debito'])} em débitos, em {qt_lanc} lançamentos."
    ).alignment = WD_ALIGN_PARAGRAPH.JUSTIFY

    tabela = _tabela_com_cabecalho(
        doc,
        ["Titular", "CPF/CNPJ", "Instituição", "Ag./Conta", "Período", "Lanç.", "Crédito", "Débito"],
        [3.6, 2.3, 2.8, 2.2, 2.6, 1.0, 2.3, 2.3],
    )
    for c in contas:
        row = tabela.add_row().cells
        _cell(row[0], c.get("no_titular") or "", tamanho=8)
        _cell(row[1], ru.formatar_documento(c.get("nu_cpf_cnpj")), tamanho=8)
        _cell(row[2], c.get("no_banco") or "", tamanho=8)
        _cell(row[3], f"{c.get('nu_agencia') or '—'} / {c.get('nu_conta') or '—'}", tamanho=8)
        _cell(row[4], ru.formatar_periodo(c.get("dt_primeiro_lancamento"), c.get("dt_ultimo_lancamento")), tamanho=8)
        _cell(row[5], str(c.get("qt_lancamentos") or 0), tamanho=8)
        _cell(row[6], ru.formatar_reais(c.get("vl_credito")), tamanho=8)
        _cell(row[7], ru.formatar_reais(c.get("vl_debito")), tamanho=8)


def _bloco_bancario_contrapartes(doc, bloco, valores, dados):
    _abre_secao(doc, bloco, valores)
    linhas = dados.get("linhas") or []
    if not linhas:
        doc.add_paragraph("Nenhuma contraparte identificada no extrato bancário.")
        return

    tabela = _tabela_com_cabecalho(
        doc,
        ["Contraparte", "CPF/CNPJ", "Lanç.", "Creditou", "Recebeu", "Período", "Obs."],
        [4.2, 2.3, 1.0, 2.3, 2.3, 2.6, 2.3],
    )
    for c in linhas:
        row = tabela.add_row().cells
        _cell(row[0], c.get("no_contraparte") or "", tamanho=8)
        _cell(row[1], ru.formatar_documento(c.get("nu_cpf_cnpj")), tamanho=8)
        _cell(row[2], str(c.get("qt_lancamentos") or 0), tamanho=8)
        _cell(row[3], ru.formatar_reais(c.get("vl_creditou")), tamanho=8)
        _cell(row[4], ru.formatar_reais(c.get("vl_recebeu")), tamanho=8)
        _cell(row[5], ru.formatar_periodo(c.get("dt_primeiro"), c.get("dt_ultimo")), tamanho=8)
        _cell(row[6], "Titular afastado" if c.get("in_titular_no_extrato") else "", tamanho=8)

    # o que a tabela não mostra precisa estar escrito, senão ela parece a lista completa
    if dados.get("qt_agrupadas"):
        doc.add_paragraph(
            f"A tabela traz as {len(linhas)} contrapartes de maior volume, de um total de "
            f"{dados['qt_total']}. As demais {dados['qt_agrupadas']} somam "
            f"{ru.formatar_reais(dados['vl_agrupado'])} e não estão detalhadas acima."
        ).alignment = WD_ALIGN_PARAGRAPH.JUSTIFY


def _bloco_bancario_alertas(doc, bloco, valores, alertas):
    _abre_secao(doc, bloco, valores)
    resumo = alertas.get("resumo")
    if not resumo:
        doc.add_paragraph("Nenhum extrato bancário (SIMBA/R304) importado para este inquérito.")
        return

    doc.add_paragraph(
        "Os pontos abaixo são apurados automaticamente sobre os lançamentos importados, por "
        "critério fixo e verificável no extrato original. Não são conclusões: são operações que "
        "pedem explicação."
    ).alignment = WD_ALIGN_PARAGRAPH.JUSTIFY

    def milhar(n):
        return f"{int(n or 0):,}".replace(",", ".")

    # os patamares são configuráveis (Administração > Alertas SIMBA) e vêm junto com os alertas —
    # o relatório enuncia o que foi realmente aplicado, não um "R$ 10 mil" fixo no texto
    parametros = alertas.get("parametros") or {}
    vl_referencia = parametros.get("limite_comunicacao", 10000)
    multiplo = parametros.get("multiplo_redondo", 1000)
    min_frac = parametros.get("min_fracionamento", 3)

    doc.add_paragraph(
        f"Movimentação em espécie: {ru.formatar_reais(resumo['vl_especie'])} em "
        f"{milhar(resumo['qt_especie'])} lançamentos. "
        f"Operações de valor redondo (a partir de {ru.formatar_reais(vl_referencia)}, em múltiplos "
        f"exatos de {ru.formatar_reais(multiplo)}): {milhar(resumo['qt_redondos'])}, das quais "
        f"{milhar(resumo.get('qt_redondos_internos'))} são aplicação, resgate ou estorno — "
        f"movimentação do próprio titular, redonda por natureza. "
        f"Repasses entre titulares do próprio afastamento: "
        f"{ru.formatar_reais(resumo['vl_internas'])}. "
        f"Contas de passagem (entra ~= sai): {milhar(resumo.get('qt_passagem', 0))}."
    ).alignment = WD_ALIGN_PARAGRAPH.JUSTIFY

    if alertas.get("especie"):
        doc.add_paragraph("Movimentação em espécie por conta:").runs[0].bold = True
        tabela = _tabela_com_cabecalho(
            doc, ["Titular", "Instituição", "Ag./Conta", "Lanç.", "Depósitos", "Saques"],
            [4.2, 3.2, 2.4, 1.1, 2.5, 2.5],
        )
        for a in alertas["especie"]:
            row = tabela.add_row().cells
            _cell(row[0], a.get("no_titular") or "", tamanho=8)
            _cell(row[1], a.get("no_banco") or "", tamanho=8)
            _cell(row[2], f"{a.get('nu_agencia') or '—'} / {a.get('nu_conta') or '—'}", tamanho=8)
            _cell(row[3], str(a.get("qt_lancamentos") or 0), tamanho=8)
            _cell(row[4], ru.formatar_reais(a.get("vl_depositos")), tamanho=8)
            _cell(row[5], ru.formatar_reais(a.get("vl_saques")), tamanho=8)

    if alertas.get("fracionamento"):
        doc.add_paragraph(
            f"Possível fracionamento ({min_frac} ou mais operações em espécie na mesma conta, no "
            f"mesmo dia e no mesmo sentido, somando {ru.formatar_reais(vl_referencia)} ou mais, cada "
            f"uma isoladamente abaixo desse patamar):"
        ).runs[0].bold = True
        tabela = _tabela_com_cabecalho(
            doc, ["Titular", "Data", "Sentido", "Operações", "Total", "Maior"],
            [4.6, 2.0, 2.0, 1.6, 2.6, 2.6],
        )
        for a in alertas["fracionamento"]:
            row = tabela.add_row().cells
            _cell(row[0], a.get("no_titular") or "", tamanho=8)
            _cell(row[1], ru.formatar_data(a.get("dt_lancamento")), tamanho=8)
            _cell(row[2], "Depósitos" if a.get("cd_natureza") == "C" else "Saques", tamanho=8)
            _cell(row[3], str(a.get("qt_lancamentos") or 0), tamanho=8)
            _cell(row[4], ru.formatar_reais(a.get("vl_total")), tamanho=8)
            _cell(row[5], ru.formatar_reais(a.get("vl_maior")), tamanho=8)
        if alertas.get("qt_fracionamento", 0) > len(alertas["fracionamento"]):
            doc.add_paragraph(
                f"Listadas as {len(alertas['fracionamento'])} ocorrências de maior valor, "
                f"de um total de {alertas['qt_fracionamento']}."
            )

    if alertas.get("internas"):
        doc.add_paragraph(
            "Repasses entre titulares do próprio afastamento (a contraparte também é titular de "
            "conta analisada):"
        ).runs[0].bold = True
        tabela = _tabela_com_cabecalho(
            doc, ["Titular da conta", "Sentido", "Contraparte", "CPF/CNPJ", "Lanç.", "Total"],
            [3.8, 1.9, 3.8, 2.3, 1.0, 2.6],
        )
        for a in alertas["internas"]:
            row = tabela.add_row().cells
            _cell(row[0], a.get("no_titular") or "", tamanho=8)
            _cell(row[1], "Recebeu de" if a.get("cd_direcao") == "credito" else "Repassou a", tamanho=8)
            _cell(row[2], a.get("no_contraparte") or "", tamanho=8)
            _cell(row[3], ru.formatar_documento(a.get("nu_cpf_cnpj")), tamanho=8)
            _cell(row[4], str(a.get("qt_lancamentos") or 0), tamanho=8)
            _cell(row[5], ru.formatar_reais(a.get("vl_total")), tamanho=8)
        if alertas.get("qt_internas", 0) > len(alertas["internas"]):
            doc.add_paragraph(
                f"Listados os {len(alertas['internas'])} pares de maior valor, "
                f"de um total de {alertas['qt_internas']}."
            )

    if alertas.get("passagens"):
        doc.add_paragraph(
            "Contas de passagem (entra quase o mesmo que sai, com giro alto — possível conta de repasse):"
        ).runs[0].bold = True
        tabela = _tabela_com_cabecalho(
            doc, ["Titular", "Conta", "Entradas", "Saídas", "Giro", "Lanç."],
            [4.2, 2.6, 2.4, 2.4, 2.4, 1.0],
        )
        for a in alertas["passagens"]:
            row = tabela.add_row().cells
            _cell(row[0], a.get("no_titular") or "", tamanho=8)
            _cell(row[1], f"{a.get('nu_agencia') or '—'} / {a.get('nu_conta') or '—'}", tamanho=8)
            _cell(row[2], ru.formatar_reais(a.get("vl_credito")), tamanho=8)
            _cell(row[3], ru.formatar_reais(a.get("vl_debito")), tamanho=8)
            _cell(row[4], ru.formatar_reais(a.get("vl_giro")), tamanho=8)
            _cell(row[5], str(a.get("qt_lancamentos") or 0), tamanho=8)
        if alertas.get("qt_passagens", 0) > len(alertas["passagens"]):
            doc.add_paragraph(
                f"Listadas as {len(alertas['passagens'])} contas de maior giro, "
                f"de um total de {alertas['qt_passagens']}."
            )

    if alertas.get("triangulacoes"):
        doc.add_paragraph(
            "Triangulação de repasses (o dinheiro sai de um titular, passa por um segundo e volta "
            "ao primeiro por um terceiro — todos com conta neste afastamento):"
        ).runs[0].bold = True
        tabela = _tabela_com_cabecalho(
            doc, ["Ciclo", "Trechos", "Menor trecho", "Soma dos trechos", "Período"],
            [3.8, 4.6, 2.2, 2.2, 2.6],
        )
        for a in alertas["triangulacoes"]:
            nomes = [n.get("nome") or "—" for n in a.get("nos") or []]
            row = tabela.add_row().cells
            _cell(row[0], " → ".join(nomes + nomes[:1]), tamanho=8)
            _cell(row[1], "\n".join(
                f"{t.get('de_nome') or '—'} → {t.get('para_nome') or '—'}: "
                f"{ru.formatar_reais(t.get('vl_total'))} ({t.get('qt_lancamentos') or 0} lanç.)"
                for t in a.get("trechos") or []
            ), tamanho=8)
            _cell(row[2], ru.formatar_reais(a.get("vl_menor")), tamanho=8)
            _cell(row[3], ru.formatar_reais(a.get("vl_total")), tamanho=8)
            _cell(row[4], f"{ru.formatar_data(a.get('dt_primeiro'))} a "
                          f"{ru.formatar_data(a.get('dt_ultimo'))}", tamanho=8)
        doc.add_paragraph(
            "O valor que pode ter percorrido o circuito inteiro é o do menor trecho; os demais "
            "podem incluir dinheiro de outra origem. Um circuito fechado não é, por si só, indício "
            "de ilicitude — pagamentos cruzados entre sócios e empresas do mesmo grupo produzem o "
            "mesmo desenho."
        ).alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        if alertas.get("qt_triangulacoes", 0) > len(alertas["triangulacoes"]):
            doc.add_paragraph(
                f"Listados os {len(alertas['triangulacoes'])} ciclos de maior valor, "
                f"de um total de {alertas['qt_triangulacoes']}."
            )


# ---------- blocos do RIF (SISCOAF) ----------

def _bloco_rif_resumo(doc, bloco, valores, resumo):
    _abre_secao(doc, bloco, valores)
    if not resumo:
        doc.add_paragraph("Nenhum RIF (SISCOAF) importado para este inquérito.")
        return

    doc.add_paragraph(
        f"Foram analisados {resumo['qt_rifs']} RIF(s) — "
        f"{resumo['qt_comunicacoes']} comunicação(ões), "
        f"{resumo['qt_envolvidos']} envolvido(s) e "
        f"{resumo['qt_ocorrencias']} ocorrência(s)."
        f"{'  Nº do(s) RIF(s): ' + resumo['nu_rifs'] if resumo.get('nu_rifs') else ''}"
    ).alignment = WD_ALIGN_PARAGRAPH.JUSTIFY


def _tabela_contrapartes_rif(doc, contrapartes, titulo):
    """Pequena tabela auxiliar com as contrapartes de uma direção (crédito/débito)."""
    if not contrapartes:
        return
    p = doc.add_paragraph()
    r = p.add_run(titulo)
    r.bold = True
    r.font.size = Pt(9)
    tab = doc.add_table(rows=1, cols=4)
    tab.style = "Table Grid"
    for c, t in zip(tab.rows[0].cells, ["Contraparte", "CPF/CNPJ", "Lanç.", "Valor (R$)"]):
        _cell(c, t, tamanho=8, negrito=True, cor_fundo=CINZA)
    for cp in contrapartes:
        row = tab.add_row().cells
        _cell(row[0], cp.get("no_contraparte") or "DESCONHECIDO", tamanho=8)
        _cell(row[1], ru.formatar_documento(cp.get("nu_cpf_cnpj")), tamanho=8)
        _cell(row[2], str(cp.get("qt_lancamentos") or 1), tamanho=8)
        _cell(row[3], ru.formatar_reais(cp.get("vl_total")), tamanho=8)


def _bloco_rif_comunicacoes(doc, bloco, valores, comunicacoes, contrapartes_por_com):
    _abre_secao(doc, bloco, valores)
    if not comunicacoes:
        doc.add_paragraph("Nenhuma comunicação registrada nos RIFs.")
        return

    # extrai o número da seção (ex.: "4. Comunicações" → "4.") para numerar subseções
    m = re.match(r"\s*(\d+)", bloco.get("no_titulo") or "")
    prefixo = (m.group(1) + ".") if m else ""

    for i, c in enumerate(comunicacoes, start=1):
        id_com = c.get("id_comunicacao_rif")
        cps = contrapartes_por_com.get(id_com, {"credito": [], "debito": []})

        # título da comunicação (numerado como subseção: 4.1, 4.2...)
        sub = (prefixo + str(i) + ". ") if prefixo else ""
        titulo = f"{sub}{c.get('no_comunicante') or '—'}"
        if c.get("nu_rif"):
            titulo += f"  (RIF {c['nu_rif']})"
        _titulo_secao(doc, titulo, nivel=2)

        # dados gerais em linha
        geral = []
        if c.get("dt_recebimento"):
            geral.append(f"Recebimento: {ru.formatar_data(c['dt_recebimento'])}")
        if c.get("nu_agencia"):
            geral.append(f"Agência: {c['nu_agencia']}")
        if c.get("no_cidade_agencia") or c.get("sg_uf_agencia"):
            geral.append(f"Local: {c.get('no_cidade_agencia') or ''}/{c.get('sg_uf_agencia') or ''}")
        if c.get("dt_operacao"):
            geral.append(f"Data da operação: {ru.formatar_data(c['dt_operacao'])}")
        if geral:
            p = doc.add_paragraph()
            r = p.add_run(" · ".join(geral))
            r.font.size = Pt(9)
            r.italic = True

        # totais da comunicação
        doc.add_paragraph(
            f"Total a crédito: {ru.formatar_reais(c.get('vl_credito'))}    "
            f"Total a débito: {ru.formatar_reais(c.get('vl_debito'))}"
        ).alignment = WD_ALIGN_PARAGRAPH.JUSTIFY

        # contrapartes de crédito (entradas)
        _tabela_contrapartes_rif(doc, cps.get("credito", []), "Entradas (crédito):")

        # contrapartes de débito (saídas)
        _tabela_contrapartes_rif(doc, cps.get("debito", []), "Saídas (débito):")


def _bloco_rif_contrapartes(doc, bloco, valores, dados):
    _abre_secao(doc, bloco, valores)
    linhas = dados.get("linhas") or []
    if not linhas:
        doc.add_paragraph("Nenhuma contraparte identificada nos RIFs.")
        return

    tabela = _tabela_com_cabecalho(
        doc,
        ["Contraparte", "CPF/CNPJ", "Lanç.", "Creditou", "Recebeu", "Comuns."],
        [4.5, 2.3, 1.0, 2.3, 2.3, 1.5],
    )
    for c in linhas:
        row = tabela.add_row().cells
        _cell(row[0], c.get("no_contraparte") or "DESCONHECIDO", tamanho=8)
        _cell(row[1], ru.formatar_documento(c.get("nu_cpf_cnpj")), tamanho=8)
        _cell(row[2], str(c.get("qt_lancamentos") or 0), tamanho=8)
        _cell(row[3], ru.formatar_reais(c.get("vl_creditou")), tamanho=8)
        _cell(row[4], ru.formatar_reais(c.get("vl_recebeu")), tamanho=8)
        _cell(row[5], str(c.get("qt_comunicacoes") or 0), tamanho=8)

    if dados.get("qt_agrupadas"):
        doc.add_paragraph(
            f"A tabela traz as {len(linhas)} contrapartes de maior volume, de um total de "
            f"{dados['qt_total']}. As demais {dados['qt_agrupadas']} somam "
            f"{ru.formatar_reais(dados['vl_agrupado'])} e não estão detalhadas acima."
        ).alignment = WD_ALIGN_PARAGRAPH.JUSTIFY


def _bloco_rif_envolvidos(doc, bloco, valores, envolvidos):
    _abre_secao(doc, bloco, valores)
    if not envolvidos:
        doc.add_paragraph("Nenhum envolvido registrado nos RIFs.")
        return

    tabela = _tabela_com_cabecalho(
        doc,
        ["Nome", "CPF/CNPJ", "Tipo", "Agência", "Conta", "Marcadores"],
        [4.5, 2.3, 2.5, 1.5, 2.0, 2.5],
    )
    for e in envolvidos:
        marcadores = []
        if e.get("tx_pep"):
            marcadores.append("PEP")
        if e.get("tx_servidor"):
            marcadores.append("Servidor")
        if e.get("tx_pessoa_obrigada"):
            marcadores.append("Pessoa obrigada")
        row = tabela.add_row().cells
        _cell(row[0], e.get("no_envolvido") or "—", tamanho=8)
        _cell(row[1], ru.formatar_documento(e.get("nu_cpf_cnpj")), tamanho=8)
        _cell(row[2], e.get("tp_envolvido") or "—", tamanho=8)
        _cell(row[3], e.get("nu_agencia") or "—", tamanho=8)
        _cell(row[4], e.get("nu_conta") or "—", tamanho=8)
        _cell(row[5], ", ".join(marcadores) if marcadores else "—", tamanho=8)


def _bloco_rif_ocorrencias(doc, bloco, valores, ocorrencias):
    _abre_secao(doc, bloco, valores)
    if not ocorrencias:
        doc.add_paragraph("Nenhuma ocorrência registrada nos RIFs.")
        return

    for o in ocorrencias:
        p = doc.add_paragraph()
        r = p.add_run(
            f"{o.get('id_ocorrencia') or '—'} "
            f"{'(' + str(o.get('nu_indexador')) + ')' if o.get('nu_indexador') else ''}"
        )
        r.bold = True
        r.font.size = Pt(10)
        if o.get("tx_ocorrencia"):
            doc.add_paragraph(o["tx_ocorrencia"]).alignment = WD_ALIGN_PARAGRAPH.JUSTIFY


# ---------- blocos do inquérito em PDF (ePol/IPL) ----------

# rótulo legível de cada tipo de achado extraído dos autos (IPL_TB04.TP_ACHADO); tipo novo que a
# extração passe a reconhecer cai no próprio código, capitalizado, em vez de sumir da tabela
ROTULO_TP_ACHADO_IPL = {
    "telefone": "Telefones",
    "email": "E-mails",
    "cpf": "CPF",
    "cnpj": "CNPJ",
    "valor": "Valores",
    "conta": "Contas bancárias",
    "agencia": "Agências",
    "placa": "Placas de veículo",
    "endereco": "Endereços",
}


def _bloco_ipl_resumo(doc, bloco, valores, ipls):
    _abre_secao(doc, bloco, valores)
    if not ipls:
        doc.add_paragraph("Nenhum inquérito em PDF (ePol) importado para este inquérito.")
        return

    qt_folhas = sum(int(i.get("qt_folhas") or 0) for i in ipls)
    doc.add_paragraph(
        f"Foram analisados {len(ipls)} caderno(s) de inquérito em formato PDF, somando "
        f"{qt_folhas} folha(s), dos quais foram extraídas "
        f"{sum(int(i.get('qt_pecas') or 0) for i in ipls)} peça(s), "
        f"{sum(int(i.get('qt_pessoas') or 0) for i in ipls)} pessoa(s) citada(s) e "
        f"{sum(int(i.get('qt_achados') or 0) for i in ipls)} achado(s)."
    ).alignment = WD_ALIGN_PARAGRAPH.JUSTIFY

    tabela = _tabela_com_cabecalho(
        doc,
        ["Nº do inquérito", "Unidade", "Autoridade", "Instauração", "Tipificação", "Folhas"],
        [2.6, 3.0, 3.0, 2.0, 3.2, 1.2],
    )
    for i in ipls:
        row = tabela.add_row().cells
        _cell(row[0], i.get("nu_ipl") or i.get("no_arquivo") or "—", tamanho=8)
        _cell(row[1], i.get("no_unidade") or "—", tamanho=8)
        _cell(row[2], i.get("no_autoridade") or "—", tamanho=8)
        _cell(row[3], ru.formatar_data(i.get("dt_instauracao")), tamanho=8)
        _cell(row[4], i.get("tx_tipificacao") or "—", tamanho=8)
        _cell(row[5], str(i.get("qt_folhas") or 0), tamanho=8)

    # o resumo dos fatos é texto corrido do despacho: sai como parágrafo, não espremido na tabela
    for i in ipls:
        if not i.get("tx_resumo_fatos"):
            continue
        p = doc.add_paragraph()
        r = p.add_run(f"Resumo dos fatos — {i.get('nu_ipl') or i.get('no_arquivo') or 'inquérito'}: ")
        r.bold = True
        r.font.size = Pt(9)
        r2 = p.add_run(i["tx_resumo_fatos"])
        r2.font.size = Pt(9)
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY


def _bloco_ipl_pecas(doc, bloco, valores, pecas):
    _abre_secao(doc, bloco, valores)
    if not pecas:
        doc.add_paragraph("Nenhuma peça identificada nos autos importados.")
        return

    tabela = _tabela_com_cabecalho(
        doc,
        ["Peça", "Nº", "Data", "Folhas", "Órgão", "Assinante"],
        [3.6, 1.4, 1.8, 1.8, 3.2, 3.2],
    )
    for c in pecas:
        # a folha é o que localiza a peça nos autos; sem a final, a inicial sozinha já serve
        inicial, final = c.get("nu_folha_inicial"), c.get("nu_folha_final")
        if inicial and final and final != inicial:
            folhas = f"{inicial}–{final}"
        else:
            folhas = str(inicial or final or "—")
        row = tabela.add_row().cells
        _cell(row[0], c.get("tp_peca") or "—", tamanho=8)
        _cell(row[1], str(c.get("nu_peca") or "—"), tamanho=8)
        _cell(row[2], ru.formatar_data(c.get("dt_peca")), tamanho=8)
        _cell(row[3], folhas, tamanho=8)
        _cell(row[4], c.get("no_orgao") or "—", tamanho=8)
        _cell(row[5], c.get("no_assinante") or "—", tamanho=8)


def _bloco_ipl_pessoas(doc, bloco, valores, pessoas):
    _abre_secao(doc, bloco, valores)
    if not pessoas:
        doc.add_paragraph("Nenhuma pessoa citada foi identificada nos autos importados.")
        return

    tabela = _tabela_com_cabecalho(
        doc,
        ["Nome", "CPF/CNPJ", "Vulgo", "Papel", "Folha", "Menções"],
        [4.4, 2.4, 2.2, 2.6, 1.2, 1.4],
    )
    for s in pessoas:
        row = tabela.add_row().cells
        _cell(row[0], s.get("no_pessoa") or "—", tamanho=8)
        _cell(row[1], ru.formatar_documento(s.get("nu_cpf_cnpj")), tamanho=8)
        _cell(row[2], s.get("no_vulgo") or "—", tamanho=8)
        _cell(row[3], s.get("tp_papel") or "—", tamanho=8)
        _cell(row[4], str(s.get("nu_folha") or "—"), tamanho=8)
        _cell(row[5], str(s.get("qt_mencoes") or 0), tamanho=8)


def _bloco_ipl_achados(doc, bloco, valores, achados_por_tipo):
    _abre_secao(doc, bloco, valores)
    if not achados_por_tipo:
        doc.add_paragraph("Nenhum achado foi extraído dos autos importados.")
        return

    # uma tabela por tipo: telefone, conta e valor não se comparam entre si, e numa tabela única o
    # tipo teria que se repetir em cada linha
    for tp, linhas in achados_por_tipo.items():
        p = doc.add_paragraph()
        r = p.add_run(f"{ROTULO_TP_ACHADO_IPL.get(tp, tp.capitalize())} ({len(linhas)}):")
        r.bold = True
        r.font.size = Pt(9)

        tabela = _tabela_com_cabecalho(doc, ["Valor", "Folha", "Ocorrências", "Contexto"], [3.4, 1.2, 1.6, 8.8])
        for a in linhas:
            row = tabela.add_row().cells
            _cell(row[0], a.get("tx_valor") or "—", tamanho=8)
            _cell(row[1], str(a.get("nu_folha") or "—"), tamanho=8)
            _cell(row[2], str(a.get("qt_ocorrencias") or 1), tamanho=8)
            _cell(row[3], a.get("tx_contexto") or "—", tamanho=8)


# ---------- blocos de PIX, contas de nuvem e IPED ----------

def _aviso_corte(doc, dados, singular: str, plural: str):
    """Parágrafo "N ficaram de fora" das tabelas que cortam num limite.

    Sem ele a tabela passa a impressão de ser a lista completa quando não é — o mesmo cuidado
    das contrapartes bancárias."""
    if not dados.get("qt_agrupadas"):
        return
    qt = dados["qt_agrupadas"]
    doc.add_paragraph(
        f"A tabela traz {len(dados['linhas'])} {plural}, de um total de {dados['qt_total']}. "
        f"{qt} {singular if qt == 1 else plural} não {'está' if qt == 1 else 'estão'} "
        f"detalhad{'o' if qt == 1 else 'os'} acima."
    ).alignment = WD_ALIGN_PARAGRAPH.JUSTIFY


def _bloco_pix_chaves(doc, bloco, valores, dados):
    _abre_secao(doc, bloco, valores)
    linhas = dados.get("linhas") or []
    if not linhas:
        doc.add_paragraph("Nenhuma chave PIX foi detectada nas conversas deste inquérito.")
        return

    tabela = _tabela_com_cabecalho(
        doc,
        ["Chave", "Tipo", "Menções", "Conversas", "Aplicativo", "Período"],
        [4.0, 1.8, 1.2, 1.4, 2.2, 4.4],
    )
    for k in linhas:
        row = tabela.add_row().cells
        _cell(row[0], k.get("co_chave") or "—", tamanho=8)
        _cell(row[1], (k.get("tp_chave") or "—").capitalize(), tamanho=8)
        _cell(row[2], str(k.get("qt_mencoes") or 0), tamanho=8)
        _cell(row[3], str(k.get("qt_conversas") or 0), tamanho=8)
        _cell(row[4], k.get("no_apps") or "—", tamanho=8)
        _cell(row[5], ru.formatar_periodo(k.get("dt_primeira"), k.get("dt_ultima")), tamanho=8)
    _aviso_corte(doc, dados, "chave", "chaves")


def _bloco_nuvem_perfil(doc, bloco, valores, perfis):
    _abre_secao(doc, bloco, valores)
    if not perfis:
        doc.add_paragraph("Nenhum perfil de conta de nuvem importado para este inquérito.")
        return

    # perfil é um punhado de campos de uma conta só: tabela chave/valor lê melhor que uma tabela
    # larga com uma linha
    campos = [
        ("Nome", "no_nome"), ("E-mail", "tx_email"), ("E-mails alternativos", "tx_email_alternativos"),
        ("Criação da conta", "dt_criacao"), ("Nascimento informado", "tx_nascimento"),
        ("País", "tx_pais"), ("Idioma", "tx_idioma"), ("Serviços", "tx_servicos"),
        ("Situação", "tx_status"), ("Origem", "no_fonte"),
    ]
    for p in perfis:
        tabela = _tabela_com_cabecalho(doc, ["Campo", "Valor"], [4.5, 10.7])
        for rotulo, chave in campos:
            valor = p.get(chave)
            if not valor:
                continue
            valor = ru.formatar_data(valor) if chave == "dt_criacao" else str(valor)
            row = tabela.add_row().cells
            _cell(row[0], rotulo, tamanho=9, negrito=True)
            _cell(row[1], valor, tamanho=9)


def _bloco_nuvem_dispositivos(doc, bloco, valores, dados):
    _abre_secao(doc, bloco, valores)
    linhas = dados.get("linhas") or []
    if not linhas:
        doc.add_paragraph("Nenhum dispositivo associado à conta de nuvem foi importado.")
        return

    tabela = _tabela_com_cabecalho(
        doc,
        ["Dispositivo", "Fabricante", "IMEI", "Android ID", "Nº de série", "Versão"],
        [3.2, 2.2, 3.0, 2.6, 2.2, 2.0],
    )
    for d in linhas:
        row = tabela.add_row().cells
        _cell(row[0], d.get("no_dispositivo") or "—", tamanho=8)
        _cell(row[1], d.get("tx_fabricante") or "—", tamanho=8)
        _cell(row[2], d.get("tx_imei") or "—", tamanho=8)
        _cell(row[3], d.get("tx_android_id") or "—", tamanho=8)
        _cell(row[4], d.get("tx_serial") or "—", tamanho=8)
        _cell(row[5], d.get("tx_versao") or "—", tamanho=8)
    _aviso_corte(doc, dados, "dispositivo", "dispositivos")


def _bloco_nuvem_acessos(doc, bloco, valores, dados):
    _abre_secao(doc, bloco, valores)
    linhas = dados.get("linhas") or []
    if not linhas:
        doc.add_paragraph("Nenhum acesso à conta de nuvem foi importado para este inquérito.")
        return

    tabela = _tabela_com_cabecalho(
        doc,
        ["Tipo", "Identificador", "Primeiro acesso", "Último acesso", "Dias ativos", "Multilogin"],
        [2.2, 3.6, 2.6, 2.6, 1.6, 1.8],
    )
    for a in linhas:
        row = tabela.add_row().cells
        _cell(row[0], (a.get("tp_identificador") or "—").upper(), tamanho=8)
        _cell(row[1], a.get("tx_identificador") or "—", tamanho=8)
        _cell(row[2], ru.formatar_data(a.get("dt_primeira")), tamanho=8)
        _cell(row[3], ru.formatar_data(a.get("dt_ultima")), tamanho=8)
        _cell(row[4], str(a.get("qt_dias_ativos") or 0), tamanho=8)
        _cell(row[5], "Sim" if a.get("in_multilogin") else "Não", tamanho=8)
    _aviso_corte(doc, dados, "acesso", "acessos")


def _bloco_iped_extracoes(doc, bloco, valores, extracoes_iped):
    _abre_secao(doc, bloco, valores)
    if not extracoes_iped:
        doc.add_paragraph("Nenhum caso do IPED importado para este inquérito.")
        return

    tabela = _tabela_com_cabecalho(
        doc,
        ["Caso (pasta)", "Conta", "Versão do IPED", "Itens", "Arquivos", "E-mails", "Conversas", "Importação"],
        [2.8, 3.0, 1.8, 1.2, 1.3, 1.3, 1.4, 2.4],
    )
    for i in extracoes_iped:
        row = tabela.add_row().cells
        _cell(row[0], i.get("no_pasta") or "—", tamanho=8)
        _cell(row[1], i.get("no_conta") or "—", tamanho=8)
        _cell(row[2], i.get("ds_versao_iped") or "—", tamanho=8)
        _cell(row[3], str(i.get("qt_itens_total") or 0), tamanho=8)
        _cell(row[4], str(i.get("qt_arquivos") or 0), tamanho=8)
        _cell(row[5], str(i.get("qt_emails") or 0), tamanho=8)
        _cell(row[6], str(i.get("qt_conversas") or 0), tamanho=8)
        _cell(row[7], ru.formatar_data(i.get("dt_importacao")), tamanho=8)


_RENDER = {
    "capa": _bloco_capa,
    "sumario": _bloco_sumario,
    "texto": _bloco_texto,
    "tabela_campos": _bloco_tabela_campos,
}


def gerar_relatorio_docx(
    id_inquerito: int,
    id_padrao: int,
    valores_manuais: dict | None = None,
    ids_marcacoes: list[int] | None = None,
    usuario: dict | None = None,
    ids_rif: list[int] | None = None,
    ids_extratos: list[int] | None = None,
    comentario_ia: bool = False,
    ids_ipl: list[int] | None = None,
    ids_blocos: list[int] | None = None,
    progresso: Callable[[int, str], None] | None = None,
    reescrever_comentarios: bool = False,
    expandir_conversas: bool = False,
) -> bytes:
    """Monta o .docx do relatório. Levanta ValueError se o padrão não existir.

    ids_rif/ids_extratos/ids_ipl restringem as seções de RIF/extrato bancário/inquérito em PDF aos
    arquivos escolhidos na tela de geração (None = todos os importados no inquérito). ids_blocos
    restringe aos blocos do padrão marcados na geração (None = todos).

    As duas opções de IA da tela de geração são independentes: comentario_ia ("Resumo das conversas
    por IA") liga o resumo de cada conjunto de mensagens marcadas (um por marcador) nos blocos
    'chat'/'whatsapp'; reescrever_comentarios ("Reescrever comentários do analista por IA") reescreve
    os comentários das marcações — todos de uma vez, antes de montar os blocos — e o bloco 'chat' e a
    coluna "Observação" do bloco 'marcacoes' imprimem o resultado.

    expandir_conversas, quando True, imprime o fio COMPLETO das conversas marcadas nos blocos
    'chat'/'whatsapp' (sem ela a marcação da conversa é só triagem e nenhuma bolha sai — ver
    listar_chat_marcado). É o que a página do WhatsApp usa ao gerar o relatório da conversa aberta.

    progresso, quando informado, é chamado a cada bloco com (índice, rótulo) — é por ele que o job
    em background alimenta a barra de andamento (ver funcoes/relatorios_geracao.py)."""
    padrao = ru.obter_padrao(id_padrao)
    if padrao is None:
        raise ValueError("Padrão não encontrado.")

    # o filtro de blocos vem ANTES do cálculo de `tipos`: assim um bloco desmarcado também não paga
    # pela consulta dele (é o mesmo motivo de as consultas abaixo serem condicionais)
    if ids_blocos is not None:
        escolhidos = {int(i) for i in ids_blocos}
        padrao = {**padrao, "blocos": [b for b in padrao["blocos"] if b["id_bloco"] in escolhidos]}

    valores = ru.montar_valores(id_inquerito, valores_manuais, usuario)
    # os marcadores são pessoais: o relatório é rotulado pelas etiquetas de QUEM está gerando,
    # caindo para o nome da cor quando o registro não tem nenhuma (ver ru.rotulo_marcador)
    id_usuario = (usuario or {}).get("id_usuario")

    # nenhuma consulta é feita "por via das dúvidas": só o que os blocos DESTA geração usam. O
    # extrato bancário é o caso extremo (os alertas varrem os lançamentos), mas a resolução das
    # marcações também é cara — é o JOIN COALESCE que cobre todas as tabelas de origem.
    tipos = {b["tp_bloco"] for b in padrao["blocos"]}
    # Relatório de RIF ou de dados bancários: a seção "Registros marcados pela equipe" é DO
    # documento — só entra o que foi marcado nos arquivos escolhidos na geração, nunca marcações
    # de mensagens/contatos/extrações. O recorte sai em duas partes: os TIPOS de origem
    # (somente_origens) e, dentro deles, os arquivos (ids_rif/ids_extratos).
    tem_bloco_rif = bool(tipos & ru.TIPOS_BLOCO_RIF)
    tem_bloco_bancario = bool(tipos & ru.TIPOS_BLOCO_BANCARIO)
    somente_origens = None
    if tem_bloco_rif and tem_bloco_bancario:
        somente_origens = set(ru.TIPOS_ORIGEM_MARCACAO_RIF) | set(ru.TIPOS_ORIGEM_MARCACAO_SIMBA)
    elif tem_bloco_rif:
        somente_origens = set(ru.TIPOS_ORIGEM_MARCACAO_RIF)
    elif tem_bloco_bancario:
        somente_origens = set(ru.TIPOS_ORIGEM_MARCACAO_SIMBA)
    marcacoes = (
        ru.marcacoes_filtradas(
            id_inquerito, ids_marcacoes, id_usuario,
            somente_origens=somente_origens,
            ids_rif=ids_rif if tem_bloco_rif else None,
            ids_extratos=ids_extratos if tem_bloco_bancario else None,
        )
        if "marcacoes" in tipos
        else []
    )
    conversas_wpp = (ru.listar_whatsapp_marcado(id_inquerito, ids_marcacoes, id_usuario,
                                                expandir_conversas=expandir_conversas)
                     if "whatsapp" in tipos else [])
    extracoes = ru.listar_extracoes_inquerito(id_inquerito) if "extracoes" in tipos else []
    contas = ru.listar_contas_inquerito(id_inquerito) if "contas" in tipos else []
    conversas_chat = (ru.listar_chat_marcado(id_inquerito, ids_marcacoes, id_usuario,
                                             expandir_conversas=expandir_conversas)
                      if "chat" in tipos else [])
    resumo_banc = ru.resumo_bancario(id_inquerito, ids_extratos) if tipos & {"bancario_contas"} else None
    contas_banc = ru.listar_bancario_contas(id_inquerito, ids_extratos) if "bancario_contas" in tipos else []
    contrapartes_banc = (
        ru.listar_bancario_contrapartes(id_inquerito, ids_extratos) if "bancario_contrapartes" in tipos else {}
    )
    alertas_banc = ru.listar_bancario_alertas(id_inquerito, ids_extratos) if "bancario_alertas" in tipos else {}

    # RIF — consultas também condicionais para evitar queries desnecessárias
    resumo_rif = ru.resumo_rif(id_inquerito, ids_rif) if tipos & {"rif_resumo"} else None
    comunicacoes_rif = ru.listar_rif_comunicacoes(id_inquerito, ids_rif) if "rif_comunicacoes" in tipos else []
    contrapartes_rif = (
        ru.listar_rif_contrapartes(id_inquerito, ids_rif) if "rif_contrapartes" in tipos else {}
    )
    envolvidos_rif = ru.listar_rif_envolvidos(id_inquerito, ids_rif) if "rif_envolvidos" in tipos else []
    ocorrencias_rif = ru.listar_rif_ocorrencias(id_inquerito, ids_rif) if "rif_ocorrencias" in tipos else []
    # contrapartes detalhadas por comunicação (entradas/saídas)
    contrapartes_por_com = (
        ru.listar_contrapartes_por_comunicacao_rif(id_inquerito, ids_rif) if "rif_comunicacoes" in tipos else {}
    )
    # metadados dos arquivos (nome, tamanho e hashes) dos RIFs/extratos escolhidos na geração
    arquivos_rif = ru.listar_arquivos_rif(id_inquerito, ids_rif) if "rif_arquivos" in tipos else []
    arquivos_banc = ru.listar_arquivos_bancario(id_inquerito, ids_extratos) if "bancario_arquivos" in tipos else []

    # inquérito em PDF (ePol), chaves PIX, contas de nuvem e IPED — mesma regra: só o que o padrão usa
    ipl_resumo = ru.listar_ipl_resumo(id_inquerito, ids_ipl) if "ipl_resumo" in tipos else []
    ipl_pecas = ru.listar_ipl_pecas(id_inquerito, ids_ipl) if "ipl_pecas" in tipos else []
    ipl_pessoas = ru.listar_ipl_pessoas(id_inquerito, ids_ipl) if "ipl_pessoas" in tipos else []
    ipl_achados = ru.listar_ipl_achados(id_inquerito, ids_ipl) if "ipl_achados" in tipos else {}
    pix_chaves = ru.listar_pix_chaves(id_inquerito) if "pix_chaves" in tipos else {}
    nuvem_perfil = ru.listar_nuvem_perfil(id_inquerito) if "nuvem_perfil" in tipos else []
    nuvem_dispositivos = ru.listar_nuvem_dispositivos(id_inquerito) if "nuvem_dispositivos" in tipos else {}
    nuvem_acessos = ru.listar_nuvem_acessos(id_inquerito) if "nuvem_acessos" in tipos else {}
    iped_extracoes = ru.listar_iped_extracoes(id_inquerito) if "iped_extracoes" in tipos else []

    doc, secao = _preparar_documento()
    _montar_rodape(secao, valores.get("numero_rama") or valores.get("numero_inquerito", ""))

    # se o padrão já detalha as mensagens num bloco próprio ('whatsapp' ou 'chat'), o bloco
    # genérico de marcações não repete esses registros (evita o mesmo registro aparecer duas
    # vezes no relatório) — 'chat' cobre WhatsApp, SMS e demais mensageiros de uma vez
    tem_bloco_wpp = any(b["tp_bloco"] == "whatsapp" for b in padrao["blocos"])
    origens_excluidas = ({"wpp_mensagem"} if tem_bloco_wpp else set())
    if "chat" in tipos:
        origens_excluidas |= {"wpp_mensagem", "sms_mensagem", "msg_mensagem"}
    marcacoes_genericas = (
        [m for m in marcacoes if m["tp_origem"] not in origens_excluidas] if origens_excluidas else marcacoes
    )
    anexos_marcacoes = ru.anexos_de_marcacoes(marcacoes_genericas, id_inquerito)

    # A reescrita por IA acontece toda AQUI, antes de desenhar qualquer bloco, e não dentro dos
    # renderizadores. Foi o que resolveu a geração que parecia travada: um caso real tinha 471
    # comentários marcados (e só 37 textos distintos), uma chamada por comentário, em série, com a
    # tela parada no bloco "6. Conversas" por mais de meia hora. Em bloco, o texto repetido é
    # reescrito uma vez, as chamadas saem em paralelo e o andamento aparece na tela.

    # "Anonimizar dados" (Configurações → Comentário IA no relatório). Uma tabela de-para por
    # geração, compartilhada pela reescrita dos comentários e pelos resumos de conversa — assim a
    # mesma pessoa é o mesmo FULANO no documento inteiro. O .docx sai com os nomes reais: quem
    # descodifica é enviar_mensagem, na volta de cada chamada.
    # id_usuario segue junto do anonimizador daqui para baixo: se quem pediu o relatório tiver IA
    # própria (Usuários → "IA do usuário"), é o provedor dele que atende o resumo das conversas e a
    # reescrita dos comentários. A geração roda em thread, fora da requisição, então o id tem que
    # viajar na mão até as chamadas.
    anonimizador = (
        an.anonimizador_para_inquerito(id_inquerito)
        if (comentario_ia or reescrever_comentarios) and an.anonimizacao_ativa("REPORT_AI")
        else None
    )

    reescritas = None
    if reescrever_comentarios:
        textos = [m.get("tx_comentario") for conv in conversas_chat for m in conv["mensagens"]]
        textos += [conv.get("tx_comentario_conversa") for conv in conversas_chat]
        textos += [m.get("tx_comentario") for m in marcacoes_genericas]

        def _andamento(feitos: int, total: int) -> None:
            if progresso is not None:
                # índice 0: nenhum bloco foi montado ainda, a barra continua honesta no começo
                progresso(0, f"Reescrevendo comentários do analista por IA ({feitos}/{total})…")

        reescritas = ru.reescrever_comentarios_ia(textos, _andamento, anonimizador, id_usuario)

    for indice, bloco in enumerate(padrao["blocos"]):
        tp = bloco["tp_bloco"]
        if progresso is not None:
            progresso(indice, bloco.get("no_titulo") or ru.ROTULO_TP_BLOCO.get(tp, tp))
        if tp == "marcacoes":
            _bloco_marcacoes(doc, bloco, valores, marcacoes_genericas, anexos_marcacoes, reescritas)
        elif tp == "whatsapp":
            _bloco_whatsapp(doc, bloco, valores, conversas_wpp, comentario_ia, anonimizador, id_usuario)
        elif tp == "chat":
            _bloco_chat(doc, bloco, valores, conversas_chat, comentario_ia, reescritas, anonimizador, id_usuario)
        elif tp == "extracoes":
            _bloco_extracoes(doc, bloco, valores, extracoes)
        elif tp == "contas":
            _bloco_contas(doc, bloco, valores, contas)
        elif tp == "bancario_contas":
            _bloco_bancario_contas(doc, bloco, valores, contas_banc, resumo_banc)
        elif tp == "bancario_contrapartes":
            _bloco_bancario_contrapartes(doc, bloco, valores, contrapartes_banc)
        elif tp == "bancario_alertas":
            _bloco_bancario_alertas(doc, bloco, valores, alertas_banc)
        elif tp == "bancario_arquivos":
            _bloco_bancario_arquivos(doc, bloco, valores, arquivos_banc)
        elif tp == "rif_resumo":
            _bloco_rif_resumo(doc, bloco, valores, resumo_rif)
        elif tp == "rif_comunicacoes":
            _bloco_rif_comunicacoes(doc, bloco, valores, comunicacoes_rif, contrapartes_por_com)
        elif tp == "rif_contrapartes":
            _bloco_rif_contrapartes(doc, bloco, valores, contrapartes_rif)
        elif tp == "rif_envolvidos":
            _bloco_rif_envolvidos(doc, bloco, valores, envolvidos_rif)
        elif tp == "rif_ocorrencias":
            _bloco_rif_ocorrencias(doc, bloco, valores, ocorrencias_rif)
        elif tp == "rif_arquivos":
            _bloco_rif_arquivos(doc, bloco, valores, arquivos_rif)
        elif tp == "ipl_resumo":
            _bloco_ipl_resumo(doc, bloco, valores, ipl_resumo)
        elif tp == "ipl_pecas":
            _bloco_ipl_pecas(doc, bloco, valores, ipl_pecas)
        elif tp == "ipl_pessoas":
            _bloco_ipl_pessoas(doc, bloco, valores, ipl_pessoas)
        elif tp == "ipl_achados":
            _bloco_ipl_achados(doc, bloco, valores, ipl_achados)
        elif tp == "pix_chaves":
            _bloco_pix_chaves(doc, bloco, valores, pix_chaves)
        elif tp == "nuvem_perfil":
            _bloco_nuvem_perfil(doc, bloco, valores, nuvem_perfil)
        elif tp == "nuvem_dispositivos":
            _bloco_nuvem_dispositivos(doc, bloco, valores, nuvem_dispositivos)
        elif tp == "nuvem_acessos":
            _bloco_nuvem_acessos(doc, bloco, valores, nuvem_acessos)
        elif tp == "iped_extracoes":
            _bloco_iped_extracoes(doc, bloco, valores, iped_extracoes)
        else:
            _RENDER.get(tp, _bloco_texto)(doc, bloco, valores)

    buffer = BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def gerar_marcadores_docx(numero_inquerito: str, titulo: str, conversas: list[dict],
                          conversas_marcadas: list[dict] | None = None,
                          conteudo_das_conversas: bool = False) -> bytes:
    """Exportação avulsa do que está marcado, em bolhas de chat.

    Não é um relatório: não tem padrão, capa nem variáveis — é o investigador levando embora o
    que etiquetou. Reaproveita _bloco_chat inteiro (o mesmo desenho de tela de celular do bloco
    'chat'), montando na mão o "bloco" que ele espera.

    Duas coisas diferentes saem aqui, porque são duas marcações diferentes:
      · CONVERSAS marcadas — triagem do contato ("já li", "é relevante"): saem como uma TABELA de
        identificação (com quem, aparelho, tamanho e período do fio, comentário). O conteúdo só
        entra quando conteudo_das_conversas=True, escolha explícita do analista.
      · MENSAGENS marcadas — saem como bolhas, agrupadas por conversa."""
    doc, secao = _preparar_documento()
    _montar_rodape(secao, numero_inquerito or "")

    if conversas_marcadas:
        _titulo_secao(doc, "Conversas marcadas")
        doc.add_paragraph(
            "Fios que o investigador marcou na lista de conversas. A marcação é da conversa "
            "(triagem do contato), não das mensagens dela."
        ).alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        colunas = ["Aplicativo", "Conversa", "Aparelho", "Mensagens", "Período", "Marcadores", "Comentário"]
        tabela = doc.add_table(rows=1, cols=len(colunas))
        tabela.style = "Table Grid"
        for celula, nome in zip(tabela.rows[0].cells, colunas):
            _cell(celula, nome, tamanho=9, negrito=True, cor_fundo=CINZA)
        for conv in conversas_marcadas:
            periodo = " a ".join(
                p for p in (ru.formatar_data_hora(conv.get("dt_inicio")),
                            ru.formatar_data_hora(conv.get("dt_fim"))) if p
            )
            row = tabela.add_row().cells
            _cell(row[0], conv.get("tp_app") or "—", tamanho=8)
            _cell(row[1], conv.get("titulo") or "—", tamanho=8)
            _cell(row[2], conv.get("no_dispositivo") or conv.get("no_arquivo") or "—", tamanho=8)
            _cell(row[3], str(conv.get("qt_mensagens") or 0), tamanho=8)
            _cell(row[4], periodo or "—", tamanho=8)
            _cell(row[5], ru.rotulo_marcador(conv), tamanho=8)
            _cell(row[6], conv.get("tx_comentario") or "", tamanho=8)

    subtitulo = (
        "Mensagens marcadas e, por escolha do investigador, o conteúdo completo das conversas marcadas."
        if conteudo_das_conversas
        else "Mensagens marcadas pelo investigador, agrupadas por conversa."
    )
    _bloco_chat(doc, {"no_titulo": titulo, "tx_conteudo": subtitulo}, {}, conversas, comentario_ia=False)

    buffer = BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def nome_arquivo(id_padrao: int, numero_inquerito: str, padrao: dict | None = None) -> str:
    """Nome sugerido para o arquivo baixado.
    Se o padrão contiver blocos de RIF ou de inquérito em PDF, o prefixo muda para
    'relatorio_rif'/'relatorio_ipl'."""
    base = "relatorio"
    if padrao:
        tipos = {b["tp_bloco"] for b in (padrao.get("blocos") or [])}
        if "rif_comunicacoes" in tipos:
            base = "relatorio_rif"
        elif "ipl_pessoas" in tipos:
            base = "relatorio_ipl"
    seguro = re.sub(r"[^0-9A-Za-z._-]+", "_", (numero_inquerito or "").strip()) or str(id_padrao)
    return f"{base}_{seguro}.docx"

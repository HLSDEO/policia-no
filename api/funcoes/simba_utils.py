"""Extrato bancário do SIMBA (relatório R304) anexado a um inquérito.

O SIMBA (Sistema de Investigação de Movimentações Bancárias, do MPF) exporta o
afastamento de sigilo em vários relatórios; o R304 é o dos LANÇAMENTOS — um CSV
delimitado por ';', codificado em Windows-1252, cujo nome carrega "R304"
(ex.: ``002-PF-002310-30_R304_20260727_171432.csv``).

Grão das linhas (importante para não somar errado): o R304 traz uma linha por PERNA
do lançamento — os campos ``od_*`` ("outro documento") descrevem a contraparte, e um
lançamento com dois depositantes vira duas linhas com o mesmo ``lancamento_codigo_simba``
e ``od_valor_operacao`` parcial. Logo:
  · somar ``od_valor_operacao`` em todas as linhas dá o volume correto (não duplica);
  · contar lançamentos de verdade exige ``COUNT(DISTINCT cd_lancamento_simba)``.

A importação roda em BACKGROUND (thread, como a extração do .ufdr): o CSV cresce com o número de
contas e o período deferido, e uma importação que passe do timeout do proxy apareceria como erro
para o investigador mesmo tendo terminado bem no servidor. O andamento fica em SMB_TB01
(CD_STATUS/CD_FASE/QT_PROGRESSO_FASE, ver db/init/26_simba.sql) e a tela acompanha.
Além de carregar as tabelas, a importação faz duas coisas de análise:
  · consolida as contrapartes por conta e direção (SMB_TB04), marcando quem também é
    titular de outra conta afastada no mesmo arquivo — a movimentação "entre investigados";
  · anexa ao inquérito uma síntese textual (INQ_TB04) para a IA, já que o CSV cru é grande
    demais para virar contexto.

Ver db/init/26_simba.sql, rotas/investigacao.py e a página web simba.html.
"""

import codecs
import csv
import io
import logging
import re
import threading
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from dateutil.relativedelta import relativedelta

from funcoes.cpf_cnpj_utils import valida_cnpj, valida_cpf
from funcoes.db_utils import get_connection
from funcoes.entidades_utils import limpar_anotacoes_orfas
from funcoes.env_utils import parse_env, read_env_text
from funcoes.hash_utils import calcular_hashes_arquivo
from funcoes.investigacao_utils import PASTA_DADOS, _obter_ou_criar_inquerito
from funcoes.organizacao_arquivos import CATEGORIA_BANCARIO, organizar

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Descoberta dos arquivos em data/
# ---------------------------------------------------------------------------

_RE_R304 = re.compile(r"r304", re.IGNORECASE)

# colunas que caracterizam um R304 — se faltar alguma, o arquivo não é o relatório certo
_COLUNAS_OBRIGATORIAS = ("conta_numero_conta", "lancamento_data", "od_valor_operacao", "lancamento_natureza")


def _subpasta_simba(subpasta: str | None = None) -> Path:
    return PASTA_DADOS / subpasta if subpasta else PASTA_DADOS


def listar_arquivos_simba(subpasta: str | None = None) -> list[dict]:
    """Lista os *R304*.csv de data/, para o seletor da tela de investigação."""
    pasta = _subpasta_simba(subpasta)
    if not pasta.is_dir():
        return []
    arquivos = []
    for caminho in sorted(pasta.iterdir()):
        if not caminho.is_file() or caminho.suffix.lower() != ".csv":
            continue
        if not _RE_R304.search(caminho.name):
            continue
        arquivos.append({"nome": caminho.name, "tamanho_bytes": caminho.stat().st_size})
    return arquivos


def _validar_arquivo(no_arquivo: str, subpasta: str | None = None) -> Path:
    """Resolve o CSV em data/, barrando path traversal. Levanta ValueError/FileNotFoundError.

    Delegacia com NÓ (docs/no_descentralizado.md): o seletor listou a pasta DO NÓ, então um CSV
    que não está no central pode estar na delegacia — espelha antes de recusar (o R304 é um CSV,
    viaja inteiro e o parse roda aqui mesmo)."""
    no_arquivo = (no_arquivo or "").strip()
    if not no_arquivo or "/" in no_arquivo or "\\" in no_arquivo or no_arquivo in (".", ".."):
        raise ValueError("Arquivo inválido.")
    caminho = _subpasta_simba(subpasta) / no_arquivo
    if not caminho.is_file():
        from funcoes.documentos_utils import _puxar_documento_do_no

        if not _puxar_documento_do_no(caminho, subpasta):
            raise FileNotFoundError(no_arquivo)
    if caminho.suffix.lower() != ".csv" or not _RE_R304.search(caminho.name):
        raise ValueError("O arquivo não é um relatório R304 do SIMBA (*R304*.csv).")
    return caminho


# ---------------------------------------------------------------------------
# Leitura do CSV (';' + Windows-1252) e normalização dos campos
# ---------------------------------------------------------------------------

def _detectar_encoding(caminho: Path) -> str:
    """SIMBA exporta em Windows-1252, mas há instalações que reexportam em UTF-8 — decide olhando
    o começo do arquivo (é o suficiente: o cabeçalho e as primeiras linhas já trazem acentuação)."""
    with open(caminho, "rb") as f:
        amostra = f.read(1_000_000)
    if amostra.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    try:
        amostra.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        return "cp1252"


def _conferir_cabecalho(campos) -> None:
    faltando = [c for c in _COLUNAS_OBRIGATORIAS if c not in (campos or [])]
    if faltando:
        raise ValueError("CSV não parece um R304 do SIMBA — faltam as colunas: " + ", ".join(faltando))


def validar_cabecalho(caminho: Path) -> None:
    """Lê só o cabeçalho para recusar o arquivo errado NA HORA, ainda dentro da requisição —
    com a importação em background, um erro descoberto depois viraria um extrato em 'erro' na
    tela em vez de uma mensagem no formulário."""
    encoding = _detectar_encoding(caminho)
    with open(caminho, "r", encoding=encoding, errors="replace", newline="") as f:
        _conferir_cabecalho(csv.DictReader(f, delimiter=";").fieldnames)


# ---------------------------------------------------------------------------
# Upload do R304 pelo navegador
# ---------------------------------------------------------------------------


def salvar_simba_enviado(fluxo, nome_original: str, subpasta: str | None = None) -> dict:
    """Grava em data/[<delegacia>/] um relatório R304 enviado pelo navegador (aba do SIMBA).

    Espelha o upload de documento genérico (documentos_utils.salvar_documento_enviado): devolve
    {"nome", "tamanho_bytes"} com o nome como o arquivo ficou em disco — é ele que a tela manda
    de volta em POST /api/investigacao/extrair-simba. As diferenças para o documento genérico:
      · só aceita CSV, e confere o cabeçalho do R304 NA HORA: um CSV que não é o relatório certo
        volta como mensagem no formulário em vez de entrar em data/ e virar extrato em 'erro';
      · garante "R304" no nome (prefixa quando falta), porque a listagem do SIMBA só enxerga
        *R304*.csv — sem isso o arquivo subiria e não apareceria no seletor.
    Levanta ValueError com a mensagem que a tela mostra."""
    from funcoes.documentos_utils import _BLOCO_UPLOAD, _nome_arquivo_seguro, limite_upload_bytes, limite_upload_mb

    nome = _nome_arquivo_seguro(nome_original)
    if not nome:
        raise ValueError("Nome de arquivo inválido.")
    if Path(nome).suffix.lower() != ".csv":
        raise ValueError(f"{nome}: envie o CSV do relatório R304 do SIMBA.")
    if not _RE_R304.search(nome):
        nome = "R304_" + nome

    # Delegacia com nó ativo (docs/no_descentralizado.md): o R304 vai para a data() DO NÓ — o
    # central só faz a ponte, sem ficar com cópia da prova.
    from funcoes import no_utils

    no = no_utils.servidor_ativo_para(subpasta)
    if no is not None:
        return _salvar_simba_no(no, fluxo, nome, subpasta)

    pasta = _subpasta_simba(subpasta)
    pasta.mkdir(parents=True, exist_ok=True)

    # "xb" (criação exclusiva) e não "wb": dois investigadores enviando o mesmo nome ao mesmo
    # tempo seria um sobrescrevendo a prova do outro. Quem chega depois ganha sufixo, como em
    # organizacao_arquivos.destino().
    extensao = Path(nome).suffix
    raiz = nome[: len(nome) - len(extensao)]
    alvo = pasta / nome
    sufixo = 2
    while True:
        try:
            arquivo_destino = open(alvo, "xb")
            break
        except FileExistsError:
            if sufixo > 999:
                raise ValueError(f"{nome}: já existem arquivos demais com este nome em data/.")
            alvo = pasta / f"{raiz} ({sufixo}){extensao}"
            sufixo += 1

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
        validar_cabecalho(alvo)
    except BaseException:
        # nada de meio-arquivo em data/: ele apareceria na listagem como se estivesse inteiro, e
        # a importação falharia depois, longe daqui
        alvo.unlink(missing_ok=True)
        raise

    logger.info("R304 do SIMBA enviado pelo navegador: %s (%s bytes)", alvo, total)
    return {"nome": alvo.name, "tamanho_bytes": total}


def _salvar_simba_no(no, fluxo, nome: str, subpasta: str | None) -> dict:
    """R304 em delegacia com NÓ: o CSV vai para a data() DO NÓ (docs/no_descentralizado.md) —
    o central repassa em streaming (PUT /no/enviar) e não fica com cópia. O cabeçalho do R304 é
    conferido ANTES de tocar o nó: arquivo errado volta como mensagem no formulário sem deixar
    lixo na estante da delegacia (é por isso o temporário de sistema — o validador lê de disco).
    O temporário morre junto com a requisição, em qualquer saída."""
    import os
    import tempfile

    from funcoes import no_ponte
    from funcoes.documentos_utils import _BLOCO_UPLOAD, limite_upload_bytes, limite_upload_mb

    limite_bytes = limite_upload_bytes()
    limite_mb = limite_upload_mb()
    total = 0
    fd, caminho_temp = tempfile.mkstemp(suffix=".csv", prefix="simba_upload_")
    os.close(fd)
    temporario = Path(caminho_temp)
    try:
        with open(temporario, "wb") as saida:
            while True:
                bloco = fluxo.read(_BLOCO_UPLOAD)
                if not bloco:
                    break
                total += len(bloco)
                if total > limite_bytes:
                    raise ValueError(f"{nome}: maior que o limite de {limite_mb} MB por arquivo.")
                saida.write(bloco)
        if total == 0:
            raise ValueError(f"{nome}: arquivo vazio.")
        validar_cabecalho(temporario)
        base_rel = f"{subpasta}/" if subpasta else ""
        try:
            with open(temporario, "rb") as corpo:
                res = no_ponte.enviar_arquivo(no, corpo, base_rel + nome, max_bytes=limite_bytes)
        except no_ponte.NoIndisponivel as exc:
            raise ValueError(str(exc)) from exc
        logger.info("R304 do SIMBA enviado AO NÓ %s: %s (%s bytes)",
                    no.get("no_servidor"), res["nome"], res["tamanho_bytes"])
        return {"nome": res["nome"], "tamanho_bytes": res["tamanho_bytes"]}
    finally:
        temporario.unlink(missing_ok=True)


def _iterar_csv(caminho: Path, ao_progredir=None):
    """Percorre o CSV linha a linha (sem carregar tudo na memória), devolvendo dicts.

    ao_progredir(bytes_lidos) é chamado a cada 2 mil linhas para alimentar a barra de progresso.
    A posição vem do arquivo binário por baixo do decodificador, então ela corre um pouco à frente
    do que já foi entregue (o buffer lê adiante) — a mesma aproximação da extração do .ufdr, e o
    motivo de _marcar_progresso travar em 99%."""
    encoding = _detectar_encoding(caminho)
    with open(caminho, "rb") as bruto:
        with io.TextIOWrapper(bruto, encoding=encoding, errors="replace", newline="") as f:
            leitor = csv.DictReader(f, delimiter=";")
            _conferir_cabecalho(leitor.fieldnames)
            for numero, linha in enumerate(leitor, 1):
                if ao_progredir is not None and numero % 2000 == 0:
                    ao_progredir(bruto.tell())
                yield linha


def _txt(valor) -> str | None:
    """Normaliza um campo textual: tira o apóstrofo que o SIMBA põe para o Excel não comer zero à
    esquerda ('2028 -> 2028); vazio, '-' e '0' isolado (usado como "sem informação" em banco/agência
    da contraparte) viram None."""
    if valor is None:
        return None
    v = str(valor).strip().lstrip("'").strip()
    if v in ("", "-", "0"):
        return None
    return v


def _texto_livre(valor) -> str | None:
    """Como _txt, mas mantém '0' — para descrições e observações, onde '0' pode ser conteúdo."""
    if valor is None:
        return None
    v = str(valor).strip().lstrip("'").strip()
    return v or None


def _doc(valor) -> str | None:
    """CPF/CNPJ só com dígitos e com o zero à esquerda recomposto.

    O R304 traz o documento do TITULAR com os zeros à esquerda comidos (o CNPJ 03.785.719/0001-73
    vem como '3785719000173', 13 dígitos) enquanto o da CONTRAPARTE costuma vir completo, com 14 —
    sem repor o zero, a mesma empresa nunca casaria dos dois lados, e o cruzamento "a contraparte
    também é titular de conta afastada" simplesmente não acharia nada. Por isso: 8 a 11 dígitos
    completam 11 (CPF) e 12 a 14 completam 14 (CNPJ). Abaixo de 8 dígitos não é documento (o CSV
    tem restos de 1 a 6 dígitos no campo) e fica como veio, sem virar um CPF inventado.

    Documento formado por um único dígito repetido (99.999.999/9999-99, 000.000.000-00) é o
    sentinela de "não informado" que os bancos mandam — vira None, senão milhares de contrapartes
    diferentes e não identificadas se juntariam num só CNPJ de dezenas de milhões de reais.

    O caminho inverso também existe e é pior: o documento da CONTRAPARTE costuma vir num campo
    largo, de CNPJ, e o banco preenche o CPF com zeros à esquerda até os 14 dígitos — o CPF
    032.533.836-10 chega como '00003253383610'. Lido como CNPJ, ele vira "00.003.253/3836-10" na
    tela e a MESMA pessoa aparece duas vezes (uma como CPF, outra como esse CNPJ inexistente),
    inclusive repassando dinheiro "para si mesma" no alerta de repasses entre titulares. Por isso
    os 14 dígitos passam pelo dígito verificador: se não fecham como CNPJ mas os últimos 11 fecham
    como CPF, é CPF. A checagem (e não um simples "tem zeros na frente") é o que preserva os CNPJ
    legítimos que começam com zero — 00.000.000/0001-91, do Banco do Brasil, é um deles."""
    v = "".join(ch for ch in str(valor or "") if ch.isdigit())
    if not v or len(set(v)) == 1:
        return None
    if 8 <= len(v) <= 11:
        return v.zfill(11)
    if 12 <= len(v) <= 14:
        v = v.zfill(14)
        if v.startswith("000") and not valida_cnpj(v) and valida_cpf(v[-11:]):
            return v[-11:]
        return v
    return v


def _valor_br(valor) -> Decimal | None:
    """Converte '281.500,00' (pt-BR) em Decimal('281500.00'). None para vazio/'-'.

    Decimal (e não float) porque os totais por conta são somados aqui, linha a linha, ao longo de
    mais de cem mil lançamentos — em float o arredondamento binário faria o total fechar com
    centavos de diferença do extrato original, que é justamente o que o investigador confere."""
    v = _texto_livre(valor)
    if v is None:
        return None
    v = v.replace("R$", "").replace(" ", "").replace(".", "").replace(",", ".")
    try:
        return Decimal(v)
    except InvalidOperation:
        return None


def _data_br(valor) -> date | None:
    """Converte 'dd/mm/aaaa' (com hora opcional) em date. None se vazio/inválido.

    31/12/9999 é o sentinela que o SIMBA usa para "conta não encerrada" (é a data de encerramento
    da maioria das linhas do arquivo) — vira None, senão a tela informaria toda conta aberta como
    encerrada numa data no ano 9999."""
    v = _texto_livre(valor)
    if v is None:
        return None
    try:
        d = datetime.strptime(v.split(" ")[0], "%d/%m/%Y").date()
    except ValueError:
        return None
    return None if d.year >= 9999 else d


def _natureza(valor) -> str | None:
    """'C'/'D'; qualquer outra coisa (o CSV traz '*' quando a instituição não classificou) vira None
    — esses lançamentos aparecem à parte, em vez de entrar em crédito ou débito por chute."""
    v = (str(valor or "").strip() or "").upper()[:1]
    return v if v in ("C", "D") else None


def _chave_conta(linha: dict) -> str | None:
    """Identidade da conta na planilha: o código do SIMBA quando existe; senão banco-agência-conta."""
    cd = _txt(linha.get("conta_codigo_simba"))
    if cd:
        return cd
    partes = [_txt(linha.get("conta_numero_banco")), _txt(linha.get("conta_numero_agencia")),
              _txt(linha.get("conta_numero_conta"))]
    partes = [p for p in partes if p]
    return "-".join(partes) if partes else None


# ---------------------------------------------------------------------------
# Importação
# ---------------------------------------------------------------------------

_COLUNAS_COPY = (
    "id_extrato", "id_conta_simba", "cd_lancamento_simba", "cd_od_simba", "dt_lancamento",
    "tp_lancamento", "tx_descricao", "nu_documento", "tx_local", "vl_operacao", "cd_natureza",
    "vl_saldo", "cd_natureza_saldo", "nu_cpf_cnpj_od", "no_od", "tp_pessoa_od", "nu_banco_od",
    "nu_agencia_od", "nu_conta_od", "tp_conta_od", "tx_observacao_od", "tx_doc_identificacao_od",
    "no_endossante_cheque",
)


class _Cancelada(Exception):
    """Sinaliza que o investigador pediu para parar a importação — tratada, não é erro."""


# ATENÇÃO: fase, progresso e checagem de cancelamento usam uma conexão SÓ DELES (conn_status),
# nunca a da carga. Dois motivos: durante o COPY dos lançamentos a conexão de trabalho está em
# modo de cópia e não aceita outro comando; e cada gravação de progresso precisa de COMMIT para a
# tela enxergar, o que na conexão de trabalho encerraria a transação da carga no meio.


def _marcar_fase(conn, id_extrato: int, fase: str | None) -> None:
    """Troca a fase corrente e zera o progresso (cada fase recomeça do zero; a que não sabe medir
    fica sem porcentagem em vez de herdar a da anterior)."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE investigacao.smb_tb01_extratos SET cd_fase = %s, qt_progresso_fase = NULL "
            "WHERE id_extrato = %s",
            (fase, id_extrato),
        )
    conn.commit()


def _marcar_progresso(conn, id_extrato: int, feitos: float, total: float, pct_anterior: int = -1) -> int:
    """Grava a porcentagem (0–100) da fase e devolve o valor gravado, para o chamador passar como
    pct_anterior no próximo checkpoint: só escreve quando o inteiro muda. Trava em 99 até a fase
    acabar de fato, porque o denominador é aproximado (leitura com buffer à frente)."""
    if not total:
        return pct_anterior
    pct = min(99, max(0, int(feitos * 100 / total)))
    if pct != pct_anterior:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE investigacao.smb_tb01_extratos SET qt_progresso_fase = %s WHERE id_extrato = %s",
                (pct, id_extrato),
            )
        conn.commit()
    return pct


def _cancelamento_solicitado(conn, id_extrato: int) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT fl_cancelar FROM investigacao.smb_tb01_extratos WHERE id_extrato = %s",
                    (id_extrato,))
        row = cur.fetchone()
        return bool(row and row[0])


def _levantar_contas(caminho: Path, ao_progredir=None) -> tuple[dict, dict]:
    """1ª passada: monta o cadastro das contas e seus totais, sem tocar no banco.

    Devolve (contas_por_chave, totais_gerais). Os totais por conta saem daqui em vez de um
    GROUP BY depois porque o mesmo laço já precisa varrer o arquivo para descobrir as contas."""
    contas: dict[str, dict] = {}
    geral = {"linhas": 0, "credito": Decimal(0), "debito": Decimal(0), "sem_natureza": 0,
             "dt_min": None, "dt_max": None}

    for linha in _iterar_csv(caminho, ao_progredir):
        chave = _chave_conta(linha)
        if chave is None:
            continue  # linha sem conta identificável (rodapé/legenda) — descartada
        conta = contas.get(chave)
        if conta is None:
            conta = contas[chave] = {
                "cd_conta_simba": _txt(linha.get("conta_codigo_simba")),
                "tx_identificador": _texto_livre(linha.get("conta_identificador_com_titular")),
                "no_titular": _texto_livre(linha.get("conta_nome_titular")),
                "nu_cpf_cnpj": _doc(linha.get("conta_cpf_cnpj")),
                "tp_pessoa": _txt(linha.get("conta_tipo_pessoa")),
                "nu_banco": _txt(linha.get("conta_numero_banco")),
                "no_banco": _texto_livre(linha.get("conta_nome_banco")),
                "nu_agencia": _txt(linha.get("conta_numero_agencia")),
                "nu_conta": _txt(linha.get("conta_numero_conta")),
                "tp_conta": _texto_livre(linha.get("conta_tipo")),
                "dt_abertura": _data_br(linha.get("conta_data_abertura")),
                "dt_encerramento": _data_br(linha.get("conta_data_encerramento")),
                "tx_atendimento": _texto_livre(linha.get("atendimento_identificador")),
                "qt": 0, "credito": Decimal(0), "debito": Decimal(0), "dt_min": None, "dt_max": None,
            }

        valor = _valor_br(linha.get("od_valor_operacao")) or Decimal(0)
        natureza = _natureza(linha.get("lancamento_natureza"))
        dt = _data_br(linha.get("lancamento_data"))

        conta["qt"] += 1
        geral["linhas"] += 1
        if natureza == "C":
            conta["credito"] += valor
            geral["credito"] += valor
        elif natureza == "D":
            conta["debito"] += valor
            geral["debito"] += valor
        else:
            geral["sem_natureza"] += 1
        if dt is not None:
            for alvo in (conta, geral):
                if alvo["dt_min"] is None or dt < alvo["dt_min"]:
                    alvo["dt_min"] = dt
                if alvo["dt_max"] is None or dt > alvo["dt_max"]:
                    alvo["dt_max"] = dt

    return contas, geral


def _linha_copy(linha: dict, id_extrato: int, id_conta: int) -> tuple:
    return (
        id_extrato, id_conta,
        _txt(linha.get("lancamento_codigo_simba")), _txt(linha.get("od_codigo_simba")),
        _data_br(linha.get("lancamento_data")), _txt(linha.get("lancamento_tipo")),
        _texto_livre(linha.get("lancamento_descricao")), _txt(linha.get("lancamento_numero_documento")),
        _texto_livre(linha.get("lancamento_local_transacao")), _valor_br(linha.get("od_valor_operacao")),
        _natureza(linha.get("lancamento_natureza")), _valor_br(linha.get("lancamento_valor_saldo")),
        _natureza(linha.get("lancamento_natureza_saldo")), _doc(linha.get("od_cpf_cnpj")),
        _texto_livre(linha.get("od_nome_pessoa")), _txt(linha.get("od_tipo_pessoa")),
        _txt(linha.get("od_banco")), _txt(linha.get("od_agencia")), _txt(linha.get("od_conta")),
        _texto_livre(linha.get("od_tipo_conta")), _texto_livre(linha.get("od_observacao")),
        _txt(linha.get("od_documento_identicacao")), _texto_livre(linha.get("od_nome_endossante_cheque")),
    )


def _consolidar_contrapartes(cur, id_extrato: int) -> int:
    """Agrega SMB_TB03 em SMB_TB04: uma linha por (conta, contraparte, direção).

    A contraparte é identificada pelo CPF/CNPJ e, quando ele não vem, pelo nome em maiúsculas —
    é o mesmo critério que um analista usa ao consolidar o extrato à mão. Só entram lançamentos
    com natureza definida (C/D) e com alguma identificação da contraparte.

    Nome, banco e conta saem do lançamento de MAIOR valor do grupo, não de um max() alfabético: o
    mesmo CNPJ costuma aparecer escrito de formas diferentes pelo banco (às vezes com o nome do
    sócio no lugar do da empresa), e o rótulo da relação principal é o que descreve o vínculo."""
    cur.execute(
        """
        INSERT INTO investigacao.smb_tb04_contrapartes
            (id_extrato, id_conta_simba, cd_direcao, no_contraparte, nu_cpf_cnpj,
             nu_banco, nu_agencia, nu_conta, qt_lancamentos, vl_total, dt_primeiro, dt_ultimo)
        SELECT l.id_extrato,
               l.id_conta_simba,
               CASE l.cd_natureza WHEN 'C' THEN 'credito' ELSE 'debito' END,
               (array_agg(l.no_od ORDER BY l.vl_operacao DESC NULLS LAST))[1],
               max(l.nu_cpf_cnpj_od),
               (array_agg(l.nu_banco_od ORDER BY l.vl_operacao DESC NULLS LAST))[1],
               (array_agg(l.nu_agencia_od ORDER BY l.vl_operacao DESC NULLS LAST))[1],
               (array_agg(l.nu_conta_od ORDER BY l.vl_operacao DESC NULLS LAST))[1],
               count(*),
               coalesce(sum(l.vl_operacao), 0),
               min(l.dt_lancamento),
               max(l.dt_lancamento)
        FROM investigacao.smb_tb03_lancamentos l
        WHERE l.id_extrato = %(id)s
          AND l.cd_natureza IN ('C', 'D')
          AND coalesce(l.nu_cpf_cnpj_od, l.no_od) IS NOT NULL
        GROUP BY l.id_extrato, l.id_conta_simba, l.cd_natureza,
                 coalesce(l.nu_cpf_cnpj_od, 'NOME:' || upper(l.no_od))
        """,
        {"id": id_extrato},
    )
    inseridas = cur.rowcount
    # marca as contrapartes que também são titulares de conta afastada no mesmo arquivo —
    # é o dinheiro circulando entre os próprios investigados. O _doc() já desfaz o CPF preenchido
    # com zeros num campo largo de CNPJ (ver a docstring dele), então nas importações novas o `=`
    # simples basta; o segundo termo do OR — 14 dígitos cujos últimos 11 são o CPF de um titular
    # daqui — continua para os extratos importados ANTES dessa correção, que têm o documento
    # gravado nas duas formas (reprocessar_titular_interno_simba normaliza esses).
    cur.execute(
        """
        UPDATE investigacao.smb_tb04_contrapartes cp
        SET in_titular_no_extrato = true
        WHERE cp.id_extrato = %(id)s AND cp.nu_cpf_cnpj IS NOT NULL
          AND EXISTS (
            SELECT 1 FROM investigacao.smb_tb02_contas c
             WHERE c.id_extrato = cp.id_extrato
               AND (c.nu_cpf_cnpj = cp.nu_cpf_cnpj
                    OR (length(cp.nu_cpf_cnpj) = 14 AND length(c.nu_cpf_cnpj) = 11
                        AND right(cp.nu_cpf_cnpj, 11) = c.nu_cpf_cnpj))
          )
        """,
        {"id": id_extrato},
    )
    return inseridas


def iniciar_importacao_simba(no_arquivo: str, numero_inquerito: str, id_usuario: int | None, subpasta: str | None = None, delegacia: str | None = None, in_caso_inteligencia: bool = False) -> dict:
    """Cria a linha do extrato (status 'pendente') e dispara a importação em background.

    O que fica NA REQUISIÇÃO é só o que precisa de resposta imediata: validar o arquivo e o
    cabeçalho (arquivo errado tem que virar mensagem no formulário, não um extrato em 'erro' na
    tela) e garantir o inquérito. O resto vai para a thread — ver _processar_importacao."""
    caminho = _validar_arquivo(no_arquivo, subpasta)
    validar_cabecalho(caminho)

    with get_connection() as conn:
        with conn.cursor() as cur:
            id_inquerito = _obter_ou_criar_inquerito(cur, numero_inquerito, id_usuario, delegacia,
                                                     in_caso_inteligencia)
            cur.execute(
                """
                INSERT INTO investigacao.smb_tb01_extratos
                    (id_inquerito, no_arquivo, cd_status, id_usuario)
                VALUES (%s, %s, 'pendente', %s)
                RETURNING id_extrato
                """,
                (id_inquerito, caminho.name, id_usuario),
            )
            id_extrato = cur.fetchone()[0]
        conn.commit()

    threading.Thread(target=_processar_importacao, args=(id_extrato, caminho, id_usuario),
                     daemon=True).start()
    return {"id_extrato": id_extrato, "id_inquerito": id_inquerito, "no_arquivo": caminho.name}


def _carregar_extrato(conn, conn_status, id_extrato: int, caminho: Path, id_usuario: int | None) -> None:
    """O trabalho em si, em duas passadas pelo CSV: a primeira levanta as contas e seus totais, a
    segunda carrega os lançamentos por COPY (é o que torna viável um arquivo de centenas de
    milhares de linhas). Levanta _Cancelada se o investigador pedir para parar.

    conn faz a carga; conn_status só marca fase/progresso (ver a nota em _marcar_fase)."""
    tamanho = caminho.stat().st_size

    _marcar_fase(conn_status, id_extrato, "contas")
    estado = {"pct": -1, "checagem": 0}

    def progresso(lidos: int) -> None:
        estado["pct"] = _marcar_progresso(conn_status, id_extrato, lidos, tamanho, estado["pct"])
        estado["checagem"] += 1
        # o cancelamento é conferido a cada ~10 checkpoints (20 mil linhas) — não a cada um,
        # para não transformar o pedido de parada em uma consulta por bloco lido
        if estado["checagem"] % 10 == 0 and _cancelamento_solicitado(conn_status, id_extrato):
            raise _Cancelada()

    contas, geral = _levantar_contas(caminho, progresso)
    if not contas:
        raise ValueError("Nenhuma conta encontrada no arquivo — confira se é mesmo o relatório R304.")

    titulares = {c["nu_cpf_cnpj"] or (c["no_titular"] or "").upper() for c in contas.values()}
    titulares.discard("")

    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE investigacao.smb_tb01_extratos
            SET qt_contas = %s, qt_titulares = %s, qt_lancamentos = %s, vl_credito = %s,
                vl_debito = %s, dt_primeiro_lancamento = %s, dt_ultimo_lancamento = %s
            WHERE id_extrato = %s
            """,
            (len(contas), len(titulares), geral["linhas"], geral["credito"], geral["debito"],
             geral["dt_min"], geral["dt_max"], id_extrato),
        )

        id_por_chave = {}
        for chave, c in contas.items():
            cur.execute(
                """
                INSERT INTO investigacao.smb_tb02_contas
                    (id_extrato, cd_conta_simba, tx_identificador, no_titular, nu_cpf_cnpj,
                     tp_pessoa, nu_banco, no_banco, nu_agencia, nu_conta, tp_conta,
                     dt_abertura, dt_encerramento, tx_atendimento, qt_lancamentos,
                     vl_credito, vl_debito, dt_primeiro_lancamento, dt_ultimo_lancamento)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id_conta_simba
                """,
                (id_extrato, c["cd_conta_simba"], c["tx_identificador"], c["no_titular"],
                 c["nu_cpf_cnpj"], c["tp_pessoa"], c["nu_banco"], c["no_banco"], c["nu_agencia"],
                 c["nu_conta"], c["tp_conta"], c["dt_abertura"], c["dt_encerramento"],
                 c["tx_atendimento"], c["qt"], c["credito"], c["debito"], c["dt_min"], c["dt_max"]),
            )
            id_por_chave[chave] = cur.fetchone()[0]
    conn.commit()

    _marcar_fase(conn_status, id_extrato, "lancamentos")
    estado["pct"] = -1
    with conn.cursor() as cur:
        sql_copy = "COPY investigacao.smb_tb03_lancamentos (%s) FROM STDIN" % ", ".join(_COLUNAS_COPY)
        with cur.copy(sql_copy) as copia:
            for linha in _iterar_csv(caminho, progresso):
                id_conta = id_por_chave.get(_chave_conta(linha) or "")
                if id_conta is None:
                    continue
                copia.write_row(_linha_copy(linha, id_extrato, id_conta))
    conn.commit()

    _marcar_fase(conn_status, id_extrato, "contrapartes")
    with conn.cursor() as cur:
        qt_contrapartes = _consolidar_contrapartes(cur, id_extrato)
        cur.execute(
            "UPDATE investigacao.smb_tb01_extratos SET qt_contrapartes = %s WHERE id_extrato = %s",
            (qt_contrapartes, id_extrato),
        )
    conn.commit()

    # síntese textual para a IA (o CSV cru é grande demais para virar contexto)
    _marcar_fase(conn_status, id_extrato, "sintese")
    with conn.cursor() as cur:
        cur.execute("SELECT id_inquerito FROM investigacao.smb_tb01_extratos WHERE id_extrato = %s",
                    (id_extrato,))
        id_inquerito = cur.fetchone()[0]
        texto = _montar_sintese(cur, id_extrato, caminho.name)
        cur.execute(
            """
            INSERT INTO investigacao.inq_tb04_documentos
                (id_inquerito, no_arquivo, tp_arquivo, tx_conteudo, qt_caracteres, id_usuario)
            VALUES (%s, %s, 'csv', %s, %s, %s)
            RETURNING id_documento
            """,
            (id_inquerito, "Síntese do extrato SIMBA — " + caminho.name, texto, len(texto), id_usuario),
        )
        cur.execute(
            "UPDATE investigacao.smb_tb01_extratos SET id_documento_sintese = %s WHERE id_extrato = %s",
            (cur.fetchone()[0], id_extrato),
        )
    conn.commit()


def _descartar_carga_parcial(conn, id_extrato: int) -> None:
    """Joga fora o que já tinha entrado quando a importação para no meio.

    Extrato parcial é pior que extrato nenhum: os totais por conta são gravados na 1ª passada e
    ficariam maiores que os lançamentos de fato carregados, e ninguém teria como perceber isso
    olhando a tela. A linha do extrato fica (com o status), para o investigador ver o que houve.

    O rollback na entrada é obrigatório: quando a interrupção veio de uma falha no próprio banco,
    a transação está abortada e nenhum DELETE passaria sem ele."""
    conn.rollback()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM investigacao.smb_tb04_contrapartes WHERE id_extrato = %s", (id_extrato,))
        cur.execute("DELETE FROM investigacao.smb_tb03_lancamentos WHERE id_extrato = %s", (id_extrato,))
        cur.execute("DELETE FROM investigacao.smb_tb02_contas WHERE id_extrato = %s", (id_extrato,))
        cur.execute(
            """
            UPDATE investigacao.smb_tb01_extratos
            SET qt_contas = 0, qt_titulares = 0, qt_lancamentos = 0, qt_contrapartes = 0,
                vl_credito = 0, vl_debito = 0, dt_primeiro_lancamento = NULL,
                dt_ultimo_lancamento = NULL
            WHERE id_extrato = %s
            """,
            (id_extrato,),
        )
    conn.commit()


def _finalizar(id_extrato: int, status: str, erro: str | None = None) -> None:
    """Fecha o extrato com o status final. Em conexão própria e tolerante a falha: se nem isso
    funcionar, o extrato fica em 'processando' — visível e investigável no banco."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE investigacao.smb_tb01_extratos
                    SET cd_status = %s, cd_fase = NULL, qt_progresso_fase = NULL,
                        tx_erro = %s, dt_fim_processamento = now()
                    WHERE id_extrato = %s
                    """,
                    (status, (erro or None) and erro[:2000], id_extrato),
                )
            conn.commit()
    except Exception:
        logger.exception("Falha ao gravar status final do extrato SIMBA %s", id_extrato)


def _organizar_arquivo_extrato(id_extrato: int, caminho: Path) -> None:
    """Move o R304 já carregado para data/<delegacia>/<inquérito>/BANCARIO/ e grava o caminho
    novo (relativo a data/) em NO_ARQUIVO. Best-effort: não desfaz a importação.

    Delegacia com NÓ (docs/no_descentralizado.md): a prova mora na estação — quem organiza é o
    próprio nó (POST /no/organizar); o central apaga o espelho que baixou para parsear. Nó
    indisponível: o espelho fica onde está, à vista do usuário — a reimportação readquire do nó."""
    try:
        from funcoes import no_ponte, no_utils
        from funcoes.organizacao_arquivos import delegacia_de
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT i.nu_inquerito
                    FROM investigacao.smb_tb01_extratos s
                    JOIN investigacao.inq_tb01_inqueritos i ON i.id_inquerito = s.id_inquerito
                    WHERE s.id_extrato = %s
                    """,
                    (id_extrato,),
                )
                row = cur.fetchone()
                if row is None:
                    return
                no = no_utils.servidor_ativo_para(delegacia_de(caminho))
                relativo = None
                if no is not None:
                    try:
                        relativo = no_ponte.organizar(
                            no, caminho.relative_to(PASTA_DADOS).as_posix(),
                            row[0], CATEGORIA_BANCARIO)
                    except (no_ponte.NoIndisponivel, FileNotFoundError) as exc:
                        logger.warning("Falha ao organizar o R304 do extrato %s no nó (%s); "
                                       "espelho fica no central", id_extrato, exc)
                        return
                if relativo is None and no is None:
                    relativo = organizar(caminho, row[0], CATEGORIA_BANCARIO)
                if not relativo:
                    return
                cur.execute(
                    "UPDATE investigacao.smb_tb01_extratos SET no_arquivo = %s WHERE id_extrato = %s",
                    (relativo, id_extrato),
                )
            conn.commit()
            if no is not None:
                # arrumado dentro do nó — o espelho do parse não fica no central (era este o bug)
                caminho.unlink(missing_ok=True)
    except Exception:
        logger.exception("Falha ao organizar o arquivo do extrato SIMBA %s (best-effort)", id_extrato)


def _processar_importacao(id_extrato: int, caminho: Path, id_usuario: int | None) -> None:
    """Roda em background: carrega o extrato inteiro e fecha o status ao final. Nada aqui pode
    escapar sem fechar o status — um extrato preso em 'processando' fica girando na tela para
    sempre."""
    try:
        with get_connection() as conn_status:
            with conn_status.cursor() as cur:
                cur.execute(
                    "UPDATE investigacao.smb_tb01_extratos "
                    "SET cd_status = 'processando', dt_inicio_processamento = now() "
                    "WHERE id_extrato = %s",
                    (id_extrato,),
                )
            conn_status.commit()

        # Hash FORA do bloco que segura conexão: é uma leitura completa do CSV, que num extrato
        # grande leva minutos, e antes daqui ela acontecia com DUAS conexões do pool retidas sem
        # necessidade nenhuma.
        hashes = calcular_hashes_arquivo(caminho)
        with get_connection() as conn_status:
            with conn_status.cursor() as cur:
                cur.execute(
                    "UPDATE investigacao.smb_tb01_extratos "
                    "SET co_hash_md5 = %s, co_hash_sha1 = %s, co_hash_sha256 = %s "
                    "WHERE id_extrato = %s",
                    (hashes["md5"], hashes["sha1"], hashes["sha256"], id_extrato),
                )
            conn_status.commit()

        # As duas conexões continuam justificadas aqui dentro: são duas passadas pelo CSV
        # gravando o tempo todo (ver a nota em _marcar_fase). `longa=True` pelo mesmo motivo do
        # laço do report.xml — sem statement_timeout e com idle_in_transaction folgado, porque
        # entre um commit e o próximo o Python está lendo o bloco seguinte do arquivo.
        with get_connection(longa=True) as conn, get_connection(longa=True) as conn_status:
            try:
                _carregar_extrato(conn, conn_status, id_extrato, caminho, id_usuario)
            except _Cancelada:
                _descartar_carga_parcial(conn, id_extrato)
                _finalizar(id_extrato, "cancelado")
                return
            except Exception:
                try:
                    _descartar_carga_parcial(conn, id_extrato)
                except Exception:
                    pass  # a causa original é o que interessa; o descarte é melhor esforço
                raise
        _finalizar(id_extrato, "concluido")
        # com o extrato carregado, o CSV sai da pasta de entrega da delegacia e vai para
        # data/<delegacia>/<inquérito>/BANCARIO/ (só depois das duas passadas pelo arquivo)
        _organizar_arquivo_extrato(id_extrato, caminho)
        # Titular e contraparte do extrato são gente na Mesa: identifica o inquérito (thread
        # própria, best-effort) para o SIMBA entrar no grafo sem depender do botão "Identificar
        # pessoas" — o mesmo que a ingestão do UFDR já faz no fim da extração. Não pode derrubar
        # a importação: o extrato já está 'concluido' e o except lá de baixo marcaria 'erro'.
        try:
            from funcoes.identificacao_pessoas import agendar_identificacao_pos_importacao
            id_inq = obter_id_inquerito_de_extrato(id_extrato)
            if id_inq is not None:
                agendar_identificacao_pos_importacao(id_inq, f"SIMBA do extrato {id_extrato}")
        except Exception:
            logger.exception("Falha ao agendar a identificação de pessoas após o SIMBA %s "
                             "(best-effort)", id_extrato)
    except Exception as exc:
        _finalizar(id_extrato, "erro", str(exc))


# ---------------------------------------------------------------------------
# Importação SIMBA NO NÓ (no_importar.py): parse local + emissão dos registros smb_*
#
# Mesmo desenho do importar_rif_no: o nó levanta as contas e varre o R304 com os MESMOS
# helpers do importador local (a CPU é a da estação), e o que lá era UPDATE/INSERT/COPY vira
# registros smb_meta/smb_contas/smb_lancamentos pelo canal. O central reproduz
# (reproduzir_simba) e o que só o banco faz — contrapartes, síntese e o 'concluido' — roda no
# fecho da entrega (fechar_importacao_remota), sem registro próprio no canal.
# ---------------------------------------------------------------------------

_LOTE_SMB_CONTAS = 200        # contas por registro (18 colunas cada — o lote de 8 MB cuida do resto)
_LOTE_SMB_LANCAMENTOS = 20000  # pernas por fatia de COPY no central (23 colunas por linha)


def importar_simba_no(canal, no_arquivo: str, opcoes: dict) -> dict:
    """SIMBA processado NA ESTAÇÃO (funcoes/no_importar.py). `no_arquivo` é o NOME do R304 em
    data/ do nó (foi daqui que o seletor da tela listou); `opcoes` traz delegacia_pasta
    (subpasta) e numero_inquerito (destino do organize). Devolve o resumo — que também vai no
    'finalizar' do canal, onde o central fecha a ponte."""
    try:
        return _importar_simba_no_interno(canal, no_arquivo, opcoes)
    except _Cancelada:
        raise ValueError("Importação do SIMBA cancelada no central.")


def _importar_simba_no_interno(canal, no_arquivo: str, opcoes: dict) -> dict:
    from funcoes.canal_extracao import BatimentoPonte

    nome = (no_arquivo or "").strip()
    if not nome or "/" in nome or "\\" in nome or nome in (".", ".."):
        raise ValueError("Arquivo inválido.")
    subpasta = (opcoes.get("delegacia_pasta") or "").strip() or None
    caminho = _subpasta_simba(subpasta) / nome
    if not caminho.is_file():
        raise FileNotFoundError(f"O arquivo {nome} não está no disco desta estação.")
    if caminho.suffix.lower() != ".csv" or not _RE_R304.search(caminho.name):
        raise ValueError("O arquivo não é um relatório R304 do SIMBA (*R304*.csv).")

    hashes = calcular_hashes_arquivo(caminho)  # o central grava no fecho (aqui não há banco)
    bat = BatimentoPonte(canal)
    tamanho = caminho.stat().st_size
    contadores = {"checagem": 0}

    def _progresso(lidos: int) -> None:
        bat.progresso(lidos, tamanho)
        contadores["checagem"] += 1
        # mesma cadência do laço local: o cancelamento (que vem da RESPOSTA do callback de
        # estado, cache de 2 s) é conferido a cada ~10 checkpoints, não a cada bloco lido
        if contadores["checagem"] % 10 == 0 and bat.cancelar_pedido():
            raise _Cancelada()

    bat.fase("contas")
    contas, geral = _levantar_contas(caminho, _progresso)
    if not contas:
        raise ValueError("Nenhuma conta encontrada no arquivo — confira se é mesmo o relatório R304.")

    titulares = {c["nu_cpf_cnpj"] or (c["no_titular"] or "").upper() for c in contas.values()}
    titulares.discard("")

    # os MESMOS totais do UPDATE local em smb_tb01 (reproduzir_simba só troca o id_extrato pelo
    # da linha-pai, que a ponte já aponta)
    canal.emitir("smb_meta", (len(contas), len(titulares), geral["linhas"], geral["credito"],
                              geral["debito"], geral["dt_min"], geral["dt_max"]))

    lista_contas = []
    for c in contas.values():
        # MESMA ordem de colunas do INSERT smb_contas do central; cd_conta_simba na frente é o
        # que resolve o id_conta do COPY no replay (chave natural, única por extrato)
        lista_contas.append([
            c["cd_conta_simba"], c["tx_identificador"], c["no_titular"], c["nu_cpf_cnpj"],
            c["tp_pessoa"], c["nu_banco"], c["no_banco"], c["nu_agencia"], c["nu_conta"],
            c["tp_conta"], c["dt_abertura"], c["dt_encerramento"], c["tx_atendimento"],
            c["qt"], c["credito"], c["debito"], c["dt_min"], c["dt_max"],
        ])
    for i in range(0, len(lista_contas), _LOTE_SMB_CONTAS):
        canal.emitir("smb_contas", (lista_contas[i:i + _LOTE_SMB_CONTAS],))
        canal.commit()

    bat.fase("lancamentos")
    contadores["checagem"] = 0
    fatia: list = []
    for linha in _iterar_csv(caminho, _progresso):
        conta = contas.get(_chave_conta(linha) or "")
        if conta is None:
            continue  # linha sem conta identificável (rodapé/legenda) — mesmo descarte do laço
        fatia.append([conta["cd_conta_simba"], *_linha_copy(linha, 0, 0)[2:]])
        if len(fatia) >= _LOTE_SMB_LANCAMENTOS:
            canal.emitir("smb_lancamentos", (fatia,))
            canal.commit()
            fatia = []
    if fatia:
        canal.emitir("smb_lancamentos", (fatia,))
        canal.commit()

    # contrapartes, síntese e o 'concluido' são do central (fechar_importacao_remota); aqui só
    # arruma o arquivo DENTRO da estação (data/<delegacia>/<inquérito>/BANCARIO/) — melhor
    # esforço, igual ao caminho local
    try:
        numero = opcoes.get("numero_inquerito")
        if numero:
            organizar(caminho, numero, CATEGORIA_BANCARIO)
    except Exception:
        logger.exception("organizar arquivo do SIMBA no nó (melhor esforço)")

    resumo = {"qt_contas": len(contas), "qt_lancamentos": geral["linhas"],
              "arquivo": caminho.name}
    canal.finalizar({"audios_transcritos": 0, "hashes": hashes,
                     "aviso": f"SIMBA {caminho.name}: {len(contas)} contas, "
                              f"{geral['linhas']} lançamentos (importado na estação)."})
    return resumo


def reproduzir_simba(cur, tipo: str, args: tuple, fonte: dict) -> None:
    """(paridade com o central — o replay roda no central, nunca aqui) Reproduz um registro
    smb_* do nó; ver a versão do central em api/funcoes/simba_utils.py."""
    raise NotImplementedError("o replay do SIMBA roda só no central")


def fechar_importacao_remota(id_extrato: int) -> str | None:
    """(paridade com o central — o fecho roda no central, nunca aqui) Ver a versão do central
    em api/funcoes/simba_utils.py."""
    raise NotImplementedError("o fecho da importação remota roda só no central")


def solicitar_cancelamento_simba(id_extrato: int) -> bool:
    """Pede que a thread interrompa a importação. False se o extrato não estiver processando."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE investigacao.smb_tb01_extratos
                SET fl_cancelar = true, cd_fase = 'cancelando', qt_progresso_fase = NULL
                WHERE id_extrato = %s AND cd_status IN ('pendente', 'processando')
                RETURNING id_extrato
                """,
                (id_extrato,),
            )
            achou = cur.fetchone() is not None
        conn.commit()
    return achou


# ---------------------------------------------------------------------------
# Síntese textual (o que a IA lê sobre o extrato)
# ---------------------------------------------------------------------------

def _reais(v) -> str:
    return "R$ " + f"{float(v or 0):,.2f}".replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def _dia(d) -> str:
    return d.strftime("%d/%m/%Y") if d else "—"


def _montar_sintese(cur, id_extrato: int, no_arquivo: str) -> str:
    """Resumo em texto do extrato — contas, totais, principais contrapartes e alertas — anexado como
    documento do inquérito para a IA responder sobre a movimentação sem precisar do CSV inteiro."""
    cur.execute(
        """
        SELECT qt_contas, qt_titulares, qt_lancamentos, qt_contrapartes, vl_credito, vl_debito,
               dt_primeiro_lancamento, dt_ultimo_lancamento
        FROM investigacao.smb_tb01_extratos WHERE id_extrato = %s
        """,
        (id_extrato,),
    )
    e = cur.fetchone()

    resumo_credito = _resumo_credito_extrato(cur, id_extrato)

    linhas = [
        f"EXTRATO BANCÁRIO — SIMBA R304 — arquivo {no_arquivo}",
        "",
        f"Contas afastadas: {e[0]} · titulares distintos: {e[1]} · lançamentos (pernas): {e[2]}",
        f"Período: {_dia(e[6])} a {_dia(e[7])}",
        f"Crédito total: {_reais(e[4])} · Débito total: {_reais(e[5])}",
        "",
        "ATENÇÃO — o \"crédito total\" acima soma TUDO, inclusive dinheiro que não veio de outra "
        "pessoa/empresa. Pra responder \"quanto X recebeu/pagou de terceiros\" use a quebra abaixo, "
        "não o crédito total:",
        f"  · Recebido/pago de contraparte externa identificada (o número certo para \"terceiros\"): "
        f"C {_reais(resumo_credito['vl_credito_externo'])} · D {_reais(resumo_credito['vl_debito_externo'])}",
        f"  · Circulou entre contas do(s) próprio(s) titular(es) deste afastamento (NÃO é terceiro): "
        f"C {_reais(resumo_credito['vl_credito_interno'])} · D {_reais(resumo_credito['vl_debito_interno'])}",
        f"  · Aplicação financeira automática/resgate/rendimento ou estorno/devolução, sem "
        f"contraparte (o banco girando o próprio saldo, ou desfazendo uma operação — NÃO é "
        f"terceiro): C {_reais(resumo_credito['vl_credito_financeiro_proprio'])} · "
        f"D {_reais(resumo_credito['vl_debito_financeiro_proprio'])}",
        f"  · Outras sem contraparte identificada (saque, depósito em espécie, tarifa, juros — o "
        f"R304 não trouxe nome/CPF do outro lado): "
        f"C {_reais(resumo_credito['vl_credito_outros_sem_contraparte'])} · "
        f"D {_reais(resumo_credito['vl_debito_outros_sem_contraparte'])}",
        "",
        "CONTAS (titular · CPF/CNPJ · banco/agência/conta · lançamentos · crédito · débito · período)",
    ]

    cur.execute(
        """
        SELECT no_titular, nu_cpf_cnpj, no_banco, nu_agencia, nu_conta, tp_conta,
               qt_lancamentos, vl_credito, vl_debito, dt_primeiro_lancamento, dt_ultimo_lancamento
        FROM investigacao.smb_tb02_contas
        WHERE id_extrato = %s
        ORDER BY vl_credito + vl_debito DESC
        LIMIT 500
        """,
        (id_extrato,),
    )
    for c in cur.fetchall():
        linhas.append(
            f"- {c[0] or '—'} · {c[1] or '—'} · {c[2] or '—'} ag {c[3] or '—'} cc {c[4] or '—'}"
            f" ({c[5] or '—'}) · {c[6]} lanç. · C {_reais(c[7])} · D {_reais(c[8])}"
            f" · {_dia(c[9])} a {_dia(c[10])}"
        )

    cur.execute(
        """
        SELECT count(*), coalesce(sum(vl_operacao), 0)
        FROM investigacao.smb_tb03_lancamentos
        WHERE id_extrato = %s AND coalesce(nu_cpf_cnpj_od, no_od) IS NULL
        """,
        (id_extrato,),
    )
    qt_sem_cp, vl_sem_cp = cur.fetchone()

    linhas += [
        "",
        "PRINCIPAIS CONTRAPARTES (consolidadas por pessoa, todas as contas)",
        f"[Atenção: {qt_sem_cp} lançamentos, somando {_reais(vl_sem_cp)}, não trazem nome nem CPF/CNPJ "
        f"da contraparte e por isso NÃO entram nesta lista nem nos totais por contraparte — a quebra "
        f"dessa cifra em aplicação/estorno vs. outras operações sem contraparte está no topo deste "
        f"documento.]",
    ]
    cur.execute(
        """
        SELECT (array_agg(no_contraparte ORDER BY vl_total DESC))[1] AS nome, nu_cpf_cnpj,
               sum(qt_lancamentos) AS qt,
               sum(vl_total) FILTER (WHERE cd_direcao = 'credito') AS creditou,
               sum(vl_total) FILTER (WHERE cd_direcao = 'debito') AS recebeu,
               bool_or(in_titular_no_extrato) AS interno
        FROM investigacao.smb_tb04_contrapartes
        WHERE id_extrato = %s
        GROUP BY coalesce(nu_cpf_cnpj, 'NOME:' || upper(no_contraparte)), nu_cpf_cnpj
        ORDER BY sum(vl_total) DESC
        LIMIT 150
        """,
        (id_extrato,),
    )
    for c in cur.fetchall():
        linhas.append(
            f"- {c[0] or '—'} ({c[1] or 'sem CPF/CNPJ'}) · {c[2]} lanç. · creditou {_reais(c[3])}"
            f" · recebeu {_reais(c[4])}" + (" · TAMBÉM É TITULAR DE CONTA AFASTADA" if c[5] else "")
        )

    alertas = calcular_alertas(id_extrato, cur=cur)
    p = alertas["parametros"]
    linhas += ["", "ALERTAS APURADOS AUTOMATICAMENTE"]
    linhas.append(
        f"- Movimentação em espécie: {_reais(alertas['resumo']['vl_especie'])} em "
        f"{alertas['resumo']['qt_especie']} lançamentos"
    )
    linhas.append(
        f"- Possível fracionamento ({p['min_fracionamento']}+ operações em espécie no mesmo dia/conta "
        f"somando {_reais(p['limite_comunicacao'])}+, cada uma abaixo desse limite): "
        f"{len(alertas['fracionamento'])} ocorrências"
    )
    linhas.append(
        f"- Lançamentos de valor redondo (a partir de {_reais(p['limite_comunicacao'])} e múltiplos "
        f"exatos de {_reais(p['multiplo_redondo'])}): {alertas['resumo']['qt_redondos']}, somando "
        f"{_reais(alertas['resumo']['vl_redondos'])} — destes, "
        f"{alertas['resumo']['qt_redondos_internos']} "
        f"({_reais(alertas['resumo']['vl_redondos_internos'])}) são aplicação, resgate ou estorno "
        f"(dinheiro do próprio titular, redondo por natureza) e não contam como indício"
    )
    linhas.append(
        f"- Repasses entre titulares do próprio afastamento: {len(alertas['internas'])} pares, "
        f"{_reais(alertas['resumo']['vl_internas'])}"
    )
    linhas.append(
        f"- Triangulação (A repassa a B, B a C e C de volta a A, todos titulares deste afastamento): "
        f"{len(alertas['triangulacoes'])} ciclos"
    )
    linhas.append(
        f"- Contas de passagem (entra ~= sai, giro a partir de {_reais(p.get('giro_passagem', 50000))}, "
        f"saldo residual até {p.get('tolerancia_passagem_pct', 10)}%, "
        f"{p.get('min_lancamentos_passagem', 10)}+ lançamentos): "
        f"{len(alertas.get('passagens', []))} contas"
    )
    for f in alertas["fracionamento"][:25]:
        linhas.append(
            f"  · fracionamento — {f['no_titular']} em {_dia(f['dt_lancamento'])}: {f['qt_lancamentos']}"
            f" operações {'de entrada' if f['cd_natureza'] == 'C' else 'de saída'} somando {_reais(f['vl_total'])}"
        )
    for i in alertas["internas"][:25]:
        linhas.append(
            f"  · interno — {i['no_titular']} {'recebeu de' if i['cd_direcao'] == 'credito' else 'repassou a'}"
            f" {i['no_contraparte']}: {_reais(i['vl_total'])} em {i['qt_lancamentos']} lançamentos"
            + (" (mesmo CPF/CNPJ — são duas contas da mesma pessoa)" if i["in_mesmo_titular"] else "")
        )
    for t in alertas["triangulacoes"][:15]:
        caminho = " -> ".join(n["nome"] or "—" for n in t["nos"])
        linhas.append(
            f"  · triangulação — {caminho} -> {t['nos'][0]['nome'] or '—'}: "
            + " | ".join(
                f"{x['de_nome']} pagou {_reais(x['vl_total'])} a {x['para_nome']} "
                f"({x['qt_lancamentos']} lanç.)" for x in t["trechos"]
            )
            + f" · menor trecho do ciclo {_reais(t['vl_menor'])}"
        )
    for c in alertas.get("passagens", [])[:15]:
        linhas.append(
            f"  · passagem — {c.get('no_titular')} ({c.get('nu_cpf_cnpj') or 'sem doc'}) "
            f"ag {c.get('nu_agencia') or '—'} cc {c.get('nu_conta') or '—'}: "
            f" entrou {_reais(c.get('vl_credito'))}, saiu {_reais(c.get('vl_debito'))} "
            f"(giro {_reais(c.get('vl_giro'))}, residual {c.get('pc_saldo_residual') or 0:.1f}%) "
            f"em {c.get('qt_lancamentos')} lanç."
        )

    return "\n".join(linhas)


# ---------------------------------------------------------------------------
# Consultas (card da investigação + página simba.html)
# ---------------------------------------------------------------------------

def _linhas(cur) -> list[dict]:
    colunas = [d.name for d in cur.description]
    return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


def listar_extratos_inquerito(id_inquerito: int) -> list[dict]:
    """Extratos SIMBA importados de um inquérito, para o bloco no card da investigação.
    Inclui o titular com mais contas no extrato (o alvo do pedido judicial; as demais contas
    costumam ser contrapartes trazidas para cruzamento) para identificar o extrato de relance."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT s.id_extrato, s.no_arquivo, s.id_documento_sintese, s.qt_contas, s.qt_titulares,
                       s.qt_lancamentos, s.qt_contrapartes, s.vl_credito, s.vl_debito,
                       s.dt_primeiro_lancamento, s.dt_ultimo_lancamento, s.dt_importacao,
                       s.co_hash_md5, s.co_hash_sha1, s.co_hash_sha256,
                       s.cd_status, s.cd_fase, s.qt_progresso_fase, s.tx_erro,
                       u.no_usuario AS importado_por,
                       ap.no_apelido,
                       tit.no_titular, tit.nu_cpf_cnpj AS nu_cpf_cnpj_titular
                FROM investigacao.smb_tb01_extratos s
                LEFT JOIN seguranca.usr_tb01_usuarios u ON u.id_usuario = s.id_usuario
                -- apelido livre do card (INQ_TB06_APELIDOS — ver menu "Mais ações" em investigacao.js)
                LEFT JOIN investigacao.inq_tb06_apelidos ap
                       ON ap.tp_prova = 'simba' AND ap.id_prova = s.id_extrato
                LEFT JOIN LATERAL (
                    SELECT (array_agg(no_titular ORDER BY qtd DESC))[1] AS no_titular,
                           (array_agg(nu_cpf_cnpj ORDER BY qtd DESC))[1] AS nu_cpf_cnpj
                    FROM (
                        SELECT no_titular, nu_cpf_cnpj, count(*) AS qtd
                        FROM investigacao.smb_tb02_contas
                        WHERE id_extrato = s.id_extrato AND no_titular IS NOT NULL
                        GROUP BY no_titular, nu_cpf_cnpj
                    ) x
                ) tit ON true
                WHERE s.id_inquerito = %s
                ORDER BY s.dt_importacao DESC
                """,
                (id_inquerito,),
            )
            return _linhas(cur)


def obter_id_inquerito_de_extrato(id_extrato: int) -> int | None:
    """Resolve o inquérito dono de um extrato, para checar permissão nas rotas."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id_inquerito FROM investigacao.smb_tb01_extratos WHERE id_extrato = %s",
                        (id_extrato,))
            row = cur.fetchone()
            return row[0] if row else None


def obter_extrato(id_extrato: int) -> dict | None:
    """Cabeçalho do extrato + os totais que a página usa no resumo."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT s.id_extrato, s.id_inquerito, s.no_arquivo, s.qt_contas, s.qt_titulares,
                       s.qt_lancamentos, s.qt_contrapartes, s.vl_credito, s.vl_debito,
                       s.dt_primeiro_lancamento, s.dt_ultimo_lancamento, s.dt_importacao,
                       s.id_documento_sintese, s.cd_status, s.cd_fase, s.qt_progresso_fase,
                       s.tx_erro, u.no_usuario AS importado_por,
                       (SELECT count(DISTINCT l.cd_lancamento_simba)
                          FROM investigacao.smb_tb03_lancamentos l
                         WHERE l.id_extrato = s.id_extrato) AS qt_lancamentos_distintos,
                       (SELECT count(*) FROM investigacao.smb_tb03_lancamentos l
                         WHERE l.id_extrato = s.id_extrato AND l.cd_natureza IS NULL) AS qt_sem_natureza,
                       -- movimentação que NÃO aparece na aba de contrapartes: o extrato não traz nem
                       -- nome nem CPF/CNPJ de quem está do outro lado (tarifa, juros, saque no caixa,
                       -- ou contraparte que a instituição não informou)
                       (SELECT count(*) FROM investigacao.smb_tb03_lancamentos l
                         WHERE l.id_extrato = s.id_extrato
                           AND coalesce(l.nu_cpf_cnpj_od, l.no_od) IS NULL) AS qt_sem_contraparte,
                       (SELECT coalesce(sum(l.vl_operacao), 0) FROM investigacao.smb_tb03_lancamentos l
                         WHERE l.id_extrato = s.id_extrato
                           AND coalesce(l.nu_cpf_cnpj_od, l.no_od) IS NULL) AS vl_sem_contraparte
                FROM investigacao.smb_tb01_extratos s
                LEFT JOIN seguranca.usr_tb01_usuarios u ON u.id_usuario = s.id_usuario
                WHERE s.id_extrato = %s
                """,
                (id_extrato,),
            )
            linhas = _linhas(cur)
            if not linhas:
                return None
            extrato = linhas[0]
            # quebra o "sem contraparte" e o crédito/débito total nas categorias que realmente
            # importam pra investigação — ver _resumo_credito_extrato
            extrato.update(_resumo_credito_extrato(cur, id_extrato))
            return extrato


def listar_contas_simba(id_extrato: int) -> list[dict]:
    """Todas as contas afastadas do extrato, com os totais já apurados na importação."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id_conta_simba, cd_conta_simba, tx_identificador, no_titular, nu_cpf_cnpj,
                       tp_pessoa, nu_banco, no_banco, nu_agencia, nu_conta, tp_conta,
                       dt_abertura, dt_encerramento, tx_atendimento, qt_lancamentos,
                       vl_credito, vl_debito, dt_primeiro_lancamento, dt_ultimo_lancamento
                FROM investigacao.smb_tb02_contas
                WHERE id_extrato = %s
                ORDER BY vl_credito + vl_debito DESC
                """,
                (id_extrato,),
            )
            return _linhas(cur)


def listar_lancamentos_simba(
    id_extrato: int, id_conta: int | None = None, termo: str | None = None,
    natureza: str | None = None, dt_inicio: str | None = None, dt_fim: str | None = None,
    valor_minimo: float | None = None, pagina: int = 1, tamanho: int = 200,
) -> dict:
    """Página de lançamentos com os filtros da tela. Devolve {total, pagina, tamanho, linhas}.

    A paginação é do lado do servidor porque um R304 passa fácil de 100 mil linhas — mandar tudo
    para o navegador (como a página do RIF faz com as comunicações) não escala aqui."""
    tamanho = max(1, min(int(tamanho or 200), 1000))
    pagina = max(1, int(pagina or 1))

    where = ["l.id_extrato = %(id)s"]
    params: dict = {"id": id_extrato}
    if id_conta:
        where.append("l.id_conta_simba = %(conta)s")
        params["conta"] = id_conta
    if natureza in ("C", "D"):
        where.append("l.cd_natureza = %(nat)s")
        params["nat"] = natureza
    elif natureza == "sem":
        where.append("l.cd_natureza IS NULL")
    if dt_inicio:
        where.append("l.dt_lancamento >= %(dt_ini)s")
        params["dt_ini"] = dt_inicio
    if dt_fim:
        where.append("l.dt_lancamento <= %(dt_fim)s")
        params["dt_fim"] = dt_fim
    if valor_minimo:
        where.append("l.vl_operacao >= %(vmin)s")
        params["vmin"] = valor_minimo
    if termo:
        where.append("(l.tx_descricao ILIKE %(termo)s OR l.no_od ILIKE %(termo)s"
                     " OR l.nu_cpf_cnpj_od ILIKE %(termo)s OR l.tx_observacao_od ILIKE %(termo)s)")
        params["termo"] = "%" + termo.strip() + "%"
    filtro = " AND ".join(where)

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT count(*), coalesce(sum(l.vl_operacao) FILTER (WHERE l.cd_natureza = 'C'), 0),"
                f" coalesce(sum(l.vl_operacao) FILTER (WHERE l.cd_natureza = 'D'), 0)"
                f" FROM investigacao.smb_tb03_lancamentos l WHERE {filtro}",
                params,
            )
            total, vl_credito, vl_debito = cur.fetchone()

            params["limite"] = tamanho
            params["salto"] = (pagina - 1) * tamanho
            cur.execute(
                f"""
                SELECT l.id_lancamento_simba, l.id_conta_simba, c.no_titular, c.no_banco,
                       c.nu_agencia, c.nu_conta, l.cd_lancamento_simba, l.dt_lancamento,
                       l.tp_lancamento, l.tx_descricao, l.nu_documento, l.tx_local, l.vl_operacao,
                       l.cd_natureza, l.vl_saldo, l.cd_natureza_saldo, l.nu_cpf_cnpj_od, l.no_od,
                       l.tp_pessoa_od, l.nu_banco_od, l.nu_agencia_od, l.nu_conta_od, l.tp_conta_od,
                       l.tx_observacao_od, l.no_endossante_cheque
                FROM investigacao.smb_tb03_lancamentos l
                JOIN investigacao.smb_tb02_contas c ON c.id_conta_simba = l.id_conta_simba
                WHERE {filtro}
                ORDER BY l.dt_lancamento DESC NULLS LAST, l.vl_operacao DESC
                LIMIT %(limite)s OFFSET %(salto)s
                """,
                params,
            )
            return {"total": total, "pagina": pagina, "tamanho": tamanho,
                    "vl_credito": vl_credito, "vl_debito": vl_debito, "linhas": _linhas(cur)}


def listar_contrapartes_simba(
    id_extrato: int, id_conta: int | None = None, direcao: str | None = None,
    termo: str | None = None, so_internas: bool = False, limite: int = 500,
) -> list[dict]:
    """Contrapartes por conta e direção (grão de SMB_TB04)."""
    limite = max(1, min(int(limite or 500), 5000))
    where = ["cp.id_extrato = %(id)s"]
    params: dict = {"id": id_extrato, "limite": limite}
    if id_conta:
        where.append("cp.id_conta_simba = %(conta)s")
        params["conta"] = id_conta
    if direcao in ("credito", "debito"):
        where.append("cp.cd_direcao = %(dir)s")
        params["dir"] = direcao
    if so_internas:
        where.append("cp.in_titular_no_extrato")
    if termo:
        where.append("(cp.no_contraparte ILIKE %(termo)s OR cp.nu_cpf_cnpj ILIKE %(termo)s)")
        params["termo"] = "%" + termo.strip() + "%"

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT cp.id_contraparte_simba, cp.id_conta_simba, c.no_titular, c.nu_cpf_cnpj AS doc_titular,
                       c.no_banco, c.nu_agencia, c.nu_conta, cp.cd_direcao, cp.no_contraparte,
                       cp.nu_cpf_cnpj, cp.nu_banco, cp.nu_agencia AS nu_agencia_contraparte,
                       cp.nu_conta AS nu_conta_contraparte, cp.qt_lancamentos, cp.vl_total,
                       cp.dt_primeiro, cp.dt_ultimo, cp.in_titular_no_extrato
                FROM investigacao.smb_tb04_contrapartes cp
                JOIN investigacao.smb_tb02_contas c ON c.id_conta_simba = cp.id_conta_simba
                WHERE {" AND ".join(where)}
                ORDER BY cp.vl_total DESC
                LIMIT %(limite)s
                """,
                params,
            )
            return _linhas(cur)


def consolidar_contrapartes_simba(
    id_extrato: int, termo: str | None = None, so_internas: bool = False, limite: int = 500,
) -> list[dict]:
    """Contrapartes consolidadas por pessoa (somando todas as contas do extrato) — a visão que
    responde "quem mais movimentou dinheiro com os investigados"."""
    limite = max(1, min(int(limite or 500), 5000))
    where = ["cp.id_extrato = %(id)s"]
    params: dict = {"id": id_extrato, "limite": limite}
    if so_internas:
        where.append("cp.in_titular_no_extrato")
    if termo:
        where.append("(cp.no_contraparte ILIKE %(termo)s OR cp.nu_cpf_cnpj ILIKE %(termo)s)")
        params["termo"] = "%" + termo.strip() + "%"

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT (array_agg(cp.no_contraparte ORDER BY cp.vl_total DESC))[1] AS no_contraparte,
                       cp.nu_cpf_cnpj,
                       count(DISTINCT cp.id_conta_simba) AS qt_contas,
                       sum(cp.qt_lancamentos) AS qt_lancamentos,
                       coalesce(sum(cp.vl_total) FILTER (WHERE cp.cd_direcao = 'credito'), 0) AS vl_creditou,
                       coalesce(sum(cp.vl_total) FILTER (WHERE cp.cd_direcao = 'debito'), 0) AS vl_recebeu,
                       min(cp.dt_primeiro) AS dt_primeiro,
                       max(cp.dt_ultimo) AS dt_ultimo,
                       bool_or(cp.in_titular_no_extrato) AS in_titular_no_extrato
                FROM investigacao.smb_tb04_contrapartes cp
                WHERE {" AND ".join(where)}
                GROUP BY coalesce(cp.nu_cpf_cnpj, 'NOME:' || upper(cp.no_contraparte)), cp.nu_cpf_cnpj
                ORDER BY sum(cp.vl_total) DESC
                LIMIT %(limite)s
                """,
                params,
            )
            return _linhas(cur)


def serie_mensal_simba(id_extrato: int, id_conta: int | None = None) -> list[dict]:
    """Crédito x débito por mês — a curva que mostra quando a movimentação apareceu/parou."""
    where = ["l.id_extrato = %(id)s", "l.dt_lancamento IS NOT NULL"]
    params: dict = {"id": id_extrato}
    if id_conta:
        where.append("l.id_conta_simba = %(conta)s")
        params["conta"] = id_conta
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT to_char(date_trunc('month', l.dt_lancamento), 'YYYY-MM') AS mes,
                       coalesce(sum(l.vl_operacao) FILTER (WHERE l.cd_natureza = 'C'), 0) AS vl_credito,
                       coalesce(sum(l.vl_operacao) FILTER (WHERE l.cd_natureza = 'D'), 0) AS vl_debito,
                       count(*) AS qt_lancamentos
                FROM investigacao.smb_tb03_lancamentos l
                WHERE {" AND ".join(where)}
                GROUP BY 1
                ORDER BY 1
                """,
                params,
            )
            return _linhas(cur)


# ---------------------------------------------------------------------------
# Linha do tempo financeira — pessoa
# ---------------------------------------------------------------------------
# A soma de tudo escondia de quem era o movimento ("parece que está unificando tudo, mesmo
# sendo de pessoas diferentes"): a timeline do inquérito somava as contas de todos os
# titulares e todas as comunicações do RIF no mesmo monte. A identidade é a MESMA chave do
# grafo SIMBA (_chave_pessoa_sql): CPF/CNPJ quando existe, senão o nome em maiúsculas — assim
# o titular da conta no SIMBA e o envolvido da comunicação no RIF com o mesmo documento caem
# na mesma pessoa, e a tela pode filtrar/comparar por ela.
#   · SIMBA: a pessoa é o TITULAR da conta do lançamento (smb_tb02_contas);
#   · RIF: a comunicação entra na pessoa quando ela é ENVOLVIDA daquela comunicação
#     (rif_tb03_envolvidos ligado pelo indexador) — não do RIF inteiro, senão um RIF que
#     cita cinco pessoas inflaria a timeline de cada uma.

_TIMELINE_CHAVE_TITULAR = (
    "coalesce(nullif(btrim(ct.nu_cpf_cnpj), ''), 'NOME:' || upper(btrim(ct.no_titular)))"
)
_TIMELINE_CHAVE_ENVOLVIDO = (
    "coalesce(nullif(btrim(e.nu_cpf_cnpj), ''), 'NOME:' || upper(btrim(e.no_envolvido)))"
)
# comparação lado a lado: só as N pessoas de maior volume viram linha — mais que isso nenhuma
# linha se distingue no gráfico (mesmo espírito do "Demais contrapartes" do grafo)
_LIMITE_PESSOAS_SERIE = 8


def _tem_documento_ou_nome(col_doc: str, col_nome: str) -> str:
    """Predicado: a linha identifica a pessoa (documento ou nome preenchidos)."""
    return (
        f"(nullif(btrim(coalesce({col_doc}, '')), '') IS NOT NULL"
        f" OR nullif(btrim(coalesce({col_nome}, '')), '') IS NOT NULL)"
    )


def pessoas_timeline_financeira(id_inquerito: int) -> list[dict]:
    """Pessoas da linha do tempo financeira: titulares de conta no SIMBA + envolvidos no RIF.

    Uma entrada por pessoa (chave = CPF/CNPJ, senão `NOME:`+maiúsculas), marcando em quais
    fontes ela tem movimento — é o que alimenta o filtro "Pessoa" e a legenda da comparação
    na tela timeline_financeira."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT {_TIMELINE_CHAVE_TITULAR} AS chave,
                       (array_agg(ct.no_titular ORDER BY ct.vl_credito + ct.vl_debito DESC))[1] AS nome,
                       max(nullif(btrim(ct.nu_cpf_cnpj), '')) AS documento,
                       (array_agg(coalesce(nullif(btrim(ct.tp_pessoa), ''), '-')
                                  ORDER BY ct.vl_credito + ct.vl_debito DESC))[1] AS tp_pessoa,
                       coalesce(sum(ct.vl_credito), 0) AS vl_credito,
                       coalesce(sum(ct.vl_debito), 0) AS vl_debito
                FROM investigacao.smb_tb02_contas ct
                JOIN investigacao.smb_tb01_extratos s ON s.id_extrato = ct.id_extrato
                WHERE s.id_inquerito = %(inq)s AND s.cd_status = 'concluido'
                  AND {_tem_documento_ou_nome('ct.nu_cpf_cnpj', 'ct.no_titular')}
                GROUP BY 1
                """,
                {"inq": id_inquerito},
            )
            simba = _linhas(cur)
            cur.execute(
                f"""
                SELECT {_TIMELINE_CHAVE_ENVOLVIDO} AS chave,
                       (array_agg(e.no_envolvido ORDER BY e.id_envolvido_rif))[1] AS nome,
                       max(nullif(btrim(e.nu_cpf_cnpj), '')) AS documento
                FROM investigacao.rif_tb03_envolvidos e
                JOIN investigacao.rif_tb01_rifs r ON r.id_rif = e.id_rif
                WHERE r.id_inquerito = %(inq)s
                  AND {_tem_documento_ou_nome('e.nu_cpf_cnpj', 'e.no_envolvido')}
                GROUP BY 1
                """,
                {"inq": id_inquerito},
            )
            rif = _linhas(cur)

    por_chave: dict[str, dict] = {}
    for linha in simba:
        chave = linha.get("chave")
        if not chave:
            continue
        por_chave[chave] = {
            "chave": chave,
            "nome": linha.get("nome") or chave,
            "documento": linha.get("documento"),
            "tp_pessoa": None if linha.get("tp_pessoa") == "-" else linha.get("tp_pessoa"),
            "tem_simba": True,
            "tem_rif": False,
            "vl_credito": float(linha.get("vl_credito") or 0),
            "vl_debito": float(linha.get("vl_debito") or 0),
        }
    for linha in rif:
        chave = linha.get("chave")
        if not chave:
            continue
        alvo = por_chave.get(chave)
        if alvo:
            alvo["tem_rif"] = True
            if not alvo["documento"]:
                alvo["documento"] = linha.get("documento")
        else:
            por_chave[chave] = {
                "chave": chave,
                "nome": linha.get("nome") or chave,
                "documento": linha.get("documento"),
                "tp_pessoa": None,
                "tem_simba": False,
                "tem_rif": True,
                "vl_credito": 0.0,
                "vl_debito": 0.0,
            }
    return sorted(por_chave.values(),
                  key=lambda p: (-(p["vl_credito"] + p["vl_debito"]), p["nome"]))


def _filtros_timeline_pessoa(filtro: bool) -> tuple[str, str, str, str]:
    """Fragmentos SQL do filtro por pessoa: JOIN+predicado no SIMBA, EXISTS no RIF.

    Sem filtro o JOIN das contas não entra (a consulta mensal não precisa dele), então a
    soma "todas as pessoas" continua exatamente a query de sempre."""
    join_ct = ("JOIN investigacao.smb_tb02_contas ct ON ct.id_conta_simba = l.id_conta_simba"
               if filtro else "")
    fil_ct = f"AND {_TIMELINE_CHAVE_TITULAR} = %(pessoa)s" if filtro else ""
    fil_env = (
        "AND EXISTS (SELECT 1 FROM investigacao.rif_tb03_envolvidos e"
        "           WHERE e.id_rif = c.id_rif"
        "             AND e.nu_indexador IS NOT DISTINCT FROM c.nu_indexador"
        f"             AND {_TIMELINE_CHAVE_ENVOLVIDO} = %(pessoa)s)"
        if filtro else ""
    )
    fil_env_rif = (
        "AND EXISTS (SELECT 1 FROM investigacao.rif_tb03_envolvidos e"
        f"           WHERE e.id_rif = r.id_rif AND {_TIMELINE_CHAVE_ENVOLVIDO} = %(pessoa)s)"
        if filtro else ""
    )
    return join_ct, fil_ct, fil_env, fil_env_rif


def timeline_financeira_inquerito(id_inquerito: int, pessoa: str | None = None) -> dict:
    """Linha do tempo financeira do inquérito: SIMBA + RIF por mês, num JSON único.

    Junta, mês a mês, o que cada fonte conta sozinha em telas separadas: o extrato SIMBA
    (lançamentos datados de todos os extratos concluídos do inquérito) e o RIF (comunicações
    por data da operação, caindo para data de recebimento quando a operação não tem data).
    Mês sem uma das fontes volta zerado nela — o gráfico desenha as duas curvas sobre o mesmo
    eixo temporal em vez de duas telas com períodos diferentes.

    `pessoa` (chave de pessoas_timeline_financeira) restringe TUDO — meses, extratos, RIFs e
    resumo — a uma pessoa só: no SIMBA vale o titular da conta do lançamento, no RIF vale a
    comunicação em que ela é envolvida. Sem `pessoa` volta a soma de todas, como sempre.
    Vêm também `pessoas` (o conjunto filtrável) e `por_pessoa` (série mensal das pessoas de
    maior volume) para o modo comparação lado a lado da tela.

    Só entram extratos concluídos e RIFs importados: número parcial não vai para análise
    assinada (mesmo critério dos blocos bancario_* do relatório).
    """
    filtro = bool(pessoa and pessoa.strip())
    params: dict = {"inq": id_inquerito}
    if filtro:
        params["pessoa"] = pessoa.strip()
    join_ct, fil_ct, fil_env, fil_env_rif = _filtros_timeline_pessoa(filtro)
    fil_extrato = (
        "AND EXISTS (SELECT 1 FROM investigacao.smb_tb02_contas ct"
        f"           WHERE ct.id_extrato = s.id_extrato AND {_TIMELINE_CHAVE_TITULAR} = %(pessoa)s)"
        if filtro else ""
    )

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT to_char(date_trunc('month', l.dt_lancamento), 'YYYY-MM') AS mes,
                       coalesce(sum(l.vl_operacao) FILTER (WHERE l.cd_natureza = 'C'), 0) AS vl_credito,
                       coalesce(sum(l.vl_operacao) FILTER (WHERE l.cd_natureza = 'D'), 0) AS vl_debito,
                       count(*) AS qt_lancamentos
                FROM investigacao.smb_tb03_lancamentos l
                JOIN investigacao.smb_tb01_extratos s ON s.id_extrato = l.id_extrato
                {join_ct}
                WHERE s.id_inquerito = %(inq)s AND s.cd_status = 'concluido'
                  AND l.dt_lancamento IS NOT NULL {fil_ct}
                GROUP BY 1
                ORDER BY 1
                """,
                params,
            )
            simba = _linhas(cur)
            cur.execute(
                f"""
                SELECT to_char(date_trunc('month', coalesce(c.dt_operacao, c.dt_recebimento::date)),
                               'YYYY-MM') AS mes,
                       coalesce(sum(c.vl_campo_b), 0) AS vl_credito,
                       coalesce(sum(c.vl_campo_c), 0) AS vl_debito,
                       count(*) AS qt_comunicacoes,
                       count(*) FILTER (WHERE c.dt_operacao IS NULL) AS qt_sem_data_operacao
                FROM investigacao.rif_tb02_comunicacoes c
                JOIN investigacao.rif_tb01_rifs r ON r.id_rif = c.id_rif
                WHERE r.id_inquerito = %(inq)s
                  AND coalesce(c.dt_operacao, c.dt_recebimento::date) IS NOT NULL {fil_env}
                GROUP BY 1
                ORDER BY 1
                """,
                params,
            )
            rif = _linhas(cur)
            cur.execute(
                f"""
                SELECT s.id_extrato, s.no_arquivo, s.qt_contas, s.vl_credito, s.vl_debito,
                       s.dt_primeiro_lancamento, s.dt_ultimo_lancamento
                FROM investigacao.smb_tb01_extratos s
                WHERE s.id_inquerito = %(inq)s AND s.cd_status = 'concluido' {fil_extrato}
                ORDER BY s.id_extrato
                """,
                params,
            )
            extratos = _linhas(cur)
            cur.execute(
                f"""
                SELECT r.id_rif, r.nu_rif, r.qt_comunicacoes,
                       min(c.dt_operacao) AS dt_primeira, max(c.dt_operacao) AS dt_ultima,
                       coalesce(sum(c.vl_campo_b), 0) AS vl_credito,
                       coalesce(sum(c.vl_campo_c), 0) AS vl_debito
                FROM investigacao.rif_tb01_rifs r
                LEFT JOIN investigacao.rif_tb02_comunicacoes c ON c.id_rif = r.id_rif
                WHERE r.id_inquerito = %(inq)s {fil_env_rif}
                GROUP BY r.id_rif, r.nu_rif, r.qt_comunicacoes
                ORDER BY r.id_rif
                """,
                params,
            )
            rifs = _linhas(cur)

            # série por pessoa (modo comparação): sempre o inquérito inteiro, ignorando o
            # filtro — a legenda da comparação precisa continuar mostrando todo mundo
            cur.execute(
                f"""
                SELECT {_TIMELINE_CHAVE_TITULAR} AS chave,
                       to_char(date_trunc('month', l.dt_lancamento), 'YYYY-MM') AS mes,
                       coalesce(sum(l.vl_operacao) FILTER (WHERE l.cd_natureza = 'C'), 0) AS vl_credito,
                       coalesce(sum(l.vl_operacao) FILTER (WHERE l.cd_natureza = 'D'), 0) AS vl_debito,
                       count(*) AS qt
                FROM investigacao.smb_tb03_lancamentos l
                JOIN investigacao.smb_tb01_extratos s ON s.id_extrato = l.id_extrato
                JOIN investigacao.smb_tb02_contas ct ON ct.id_conta_simba = l.id_conta_simba
                WHERE s.id_inquerito = %(inq)s AND s.cd_status = 'concluido'
                  AND l.dt_lancamento IS NOT NULL
                  AND {_tem_documento_ou_nome('ct.nu_cpf_cnpj', 'ct.no_titular')}
                GROUP BY 1, 2
                ORDER BY 1, 2
                """,
                {"inq": id_inquerito},
            )
            serie_simba = _linhas(cur)
            cur.execute(
                f"""
                SELECT chave, mes,
                       coalesce(sum(vl_b), 0) AS vl_credito,
                       coalesce(sum(vl_c), 0) AS vl_debito,
                       count(*) AS qt
                FROM (
                    SELECT DISTINCT {_TIMELINE_CHAVE_ENVOLVIDO} AS chave,
                           to_char(date_trunc('month', coalesce(c.dt_operacao, c.dt_recebimento::date)),
                                   'YYYY-MM') AS mes,
                           c.id_comunicacao_rif, c.vl_campo_b AS vl_b, c.vl_campo_c AS vl_c
                    FROM investigacao.rif_tb02_comunicacoes c
                    JOIN investigacao.rif_tb01_rifs r ON r.id_rif = c.id_rif
                    JOIN investigacao.rif_tb03_envolvidos e
                      ON e.id_rif = c.id_rif
                     AND e.nu_indexador IS NOT DISTINCT FROM c.nu_indexador
                    WHERE r.id_inquerito = %(inq)s
                      AND coalesce(c.dt_operacao, c.dt_recebimento::date) IS NOT NULL
                      AND {_tem_documento_ou_nome('e.nu_cpf_cnpj', 'e.no_envolvido')}
                ) d
                GROUP BY 1, 2
                ORDER BY 1, 2
                """,
                {"inq": id_inquerito},
            )
            serie_rif = _linhas(cur)

    por_mes: dict[str, dict] = {}
    for linha in simba:
        mes = linha.get("mes")
        if not mes:
            continue
        alvo = por_mes.setdefault(mes, {"mes": mes, "simba_credito": 0.0, "simba_debito": 0.0,
                                        "simba_qt": 0, "rif_credito": 0.0, "rif_debito": 0.0,
                                        "rif_qt": 0, "rif_sem_data_operacao": 0})
        alvo["simba_credito"] = float(linha.get("vl_credito") or 0)
        alvo["simba_debito"] = float(linha.get("vl_debito") or 0)
        alvo["simba_qt"] = int(linha.get("qt_lancamentos") or 0)
    for linha in rif:
        mes = linha.get("mes")
        if not mes:
            continue
        alvo = por_mes.setdefault(mes, {"mes": mes, "simba_credito": 0.0, "simba_debito": 0.0,
                                        "simba_qt": 0, "rif_credito": 0.0, "rif_debito": 0.0,
                                        "rif_qt": 0, "rif_sem_data_operacao": 0})
        alvo["rif_credito"] = float(linha.get("vl_credito") or 0)
        alvo["rif_debito"] = float(linha.get("vl_debito") or 0)
        alvo["rif_qt"] = int(linha.get("qt_comunicacoes") or 0)
        alvo["rif_sem_data_operacao"] = int(linha.get("qt_sem_data_operacao") or 0)
    meses = sorted(por_mes.values(), key=lambda x: x["mes"])
    for linha in meses:
        linha["total_credito"] = linha["simba_credito"] + linha["rif_credito"]
        linha["total_debito"] = linha["simba_debito"] + linha["rif_debito"]

    pessoas = pessoas_timeline_financeira(id_inquerito)
    nomes = {p["chave"]: p["nome"] for p in pessoas}

    volume: dict[str, float] = {}
    for linha in serie_simba + serie_rif:
        chave = linha.get("chave")
        if not chave:
            continue
        volume[chave] = volume.get(chave, 0.0) + float(linha.get("vl_credito") or 0) \
            + float(linha.get("vl_debito") or 0)
    top = {chave for chave, _ in
           sorted(volume.items(), key=lambda kv: -kv[1])[:_LIMITE_PESSOAS_SERIE]}

    serie: dict[str, dict[str, dict]] = {}

    def _acumula(linha: dict, campo: str) -> None:
        chave, mes = linha.get("chave"), linha.get("mes")
        if not chave or not mes or chave not in top:
            return
        alvo = serie.setdefault(chave, {}).setdefault(
            mes, {"mes": mes, "simba_credito": 0.0, "simba_debito": 0.0,
                  "rif_credito": 0.0, "rif_debito": 0.0})
        alvo[campo + "_credito"] = float(linha.get("vl_credito") or 0)
        alvo[campo + "_debito"] = float(linha.get("vl_debito") or 0)

    for linha in serie_simba:
        _acumula(linha, "simba")
    for linha in serie_rif:
        _acumula(linha, "rif")
    por_pessoa = []
    for p in pessoas:  # preserve a ordem por volume da lista de pessoas
        if p["chave"] not in top or p["chave"] not in serie:
            continue
        serie_meses = sorted(serie[p["chave"]].values(), key=lambda x: x["mes"])
        por_pessoa.append({"chave": p["chave"], "nome": nomes.get(p["chave"], p["chave"]),
                           "meses": serie_meses})

    return {
        "meses": meses,
        "extratos": extratos,
        "rifs": rifs,
        "pessoas": pessoas,
        "por_pessoa": por_pessoa,
        "resumo": {
            "qt_meses": len(meses),
            "qt_extratos": len(extratos),
            "qt_rifs": len(rifs),
            "simba_credito": sum(m["simba_credito"] for m in meses),
            "simba_debito": sum(m["simba_debito"] for m in meses),
            "rif_credito": sum(m["rif_credito"] for m in meses),
            "rif_debito": sum(m["rif_debito"] for m in meses),
        },
    }


def timeline_financeira_detalhes_mes(
    id_inquerito: int,
    mes: str,
    limite: int = 200,
    simba_offset: int = 0,
    rif_offset: int = 0,
    pessoa: str | None = None,
) -> dict:
    """Retorna os lançamentos SIMBA e comunicações RIF detalhados de um mês específico.

    Paginado por fonte (``simba_offset``/``rif_offset``), com os totais de cada lista em
    ``simba_total``/``rif_total`` — meses com milhares de lançamentos não voltam de uma vez.

    ``pessoa`` aplica o mesmo filtro da timeline (titular da conta no SIMBA, envolvido da
    comunicação no RIF). Cada linha traz a pessoa a que pertence (``no_titular`` no SIMBA,
    ``no_envolvido`` no RIF) — é a coluna "Pessoa" da gaveta, que denuncia a soma quando a
    tela está em "todas as pessoas".
    """
    if not mes or not re.match(r"^\d{4}-\d{2}$", mes):
        return {"simba": [], "rif": [], "simba_total": 0, "rif_total": 0}

    limite = max(1, min(int(limite or 200), 1000))
    simba_offset = max(0, int(simba_offset or 0))
    rif_offset = max(0, int(rif_offset or 0))

    mes_inicio = mes + "-01"
    dt_inicio = datetime.strptime(mes_inicio, "%Y-%m-%d")
    dt_fim = dt_inicio + relativedelta(months=1)

    filtro = bool(pessoa and pessoa.strip())
    _, fil_ct, fil_env, _ = _filtros_timeline_pessoa(filtro)
    params: dict = {"inq": id_inquerito, "dt_ini": dt_inicio.date(), "dt_fim": dt_fim.date(),
                    "limite": limite, "off_simba": simba_offset, "off_rif": rif_offset}
    if filtro:
        params["pessoa"] = pessoa.strip()

    simba_where = (
        "s.id_inquerito = %(inq)s AND s.cd_status = 'concluido'"
        " AND l.dt_lancamento >= %(dt_ini)s AND l.dt_lancamento < %(dt_fim)s"
        f" {fil_ct}"
    )
    rif_where = (
        "r.id_inquerito = %(inq)s"
        " AND coalesce(c.dt_operacao, c.dt_recebimento::date) >= %(dt_ini)s"
        " AND coalesce(c.dt_operacao, c.dt_recebimento::date) < %(dt_fim)s"
        f" {fil_env}"
    )
    # envolvido exibido na linha: com filtro, o que casa com a pessoa filtrada; sem filtro, o
    # que tem documento (senão o primeiro citado) — é a coluna "Pessoa" da gaveta
    envolvido_ordem = (
        "ORDER BY "
        + (f"CASE WHEN {_TIMELINE_CHAVE_ENVOLVIDO} = %(pessoa)s THEN 0 ELSE 1 END," if filtro else "")
        + " CASE WHEN nullif(btrim(coalesce(e.nu_cpf_cnpj, '')), '') IS NOT NULL"
        " THEN 0 ELSE 1 END, e.id_envolvido_rif"
    )

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT count(*)
                FROM investigacao.smb_tb03_lancamentos l
                JOIN investigacao.smb_tb01_extratos s ON s.id_extrato = l.id_extrato
                JOIN investigacao.smb_tb02_contas ct ON ct.id_conta_simba = l.id_conta_simba
                WHERE {simba_where}
                """,
                params,
            )
            qt_simba = cur.fetchone()[0]

            cur.execute(
                f"""
                SELECT l.id_lancamento_simba, l.dt_lancamento, l.vl_operacao, l.cd_natureza,
                       l.tx_descricao AS ds_historico,
                       ct.no_banco, ct.nu_conta,
                       nullif(btrim(ct.no_titular), '') AS no_titular,
                       nullif(btrim(ct.nu_cpf_cnpj), '') AS nu_cpf_cnpj_titular,
                       l.no_od AS no_contraparte,
                       l.nu_cpf_cnpj_od AS nu_cpf_cnpj_contraparte,
                       s.id_extrato, s.no_arquivo
                FROM investigacao.smb_tb03_lancamentos l
                JOIN investigacao.smb_tb01_extratos s ON s.id_extrato = l.id_extrato
                JOIN investigacao.smb_tb02_contas ct ON ct.id_conta_simba = l.id_conta_simba
                WHERE {simba_where}
                ORDER BY l.dt_lancamento, l.id_lancamento_simba
                LIMIT %(limite)s OFFSET %(off_simba)s
                """,
                params,
            )
            simba = _linhas(cur)

            cur.execute(
                f"""
                SELECT count(*)
                FROM investigacao.rif_tb02_comunicacoes c
                JOIN investigacao.rif_tb01_rifs r ON r.id_rif = c.id_rif
                WHERE {rif_where}
                """,
                params,
            )
            qt_rif = cur.fetchone()[0]

            cur.execute(
                f"""
                SELECT c.id_comunicacao_rif,
                       coalesce(c.dt_operacao, c.dt_recebimento::date) AS dt_operacao,
                       c.dt_operacao IS NULL AS usa_recebimento,
                       c.vl_campo_b, c.vl_campo_c, c.cd_segmento AS tp_comunicacao,
                       r.id_rif, r.nu_rif,
                       (SELECT cp.no_contraparte
                          FROM investigacao.rif_tb05_contrapartes cp
                         WHERE cp.id_comunicacao_rif = c.id_comunicacao_rif
                         ORDER BY CASE WHEN cp.cd_direcao = 'credito' THEN 0 ELSE 1 END,
                                  cp.nu_ordem
                         LIMIT 1) AS no_contraparte,
                       (SELECT cp.nu_cpf_cnpj
                          FROM investigacao.rif_tb05_contrapartes cp
                         WHERE cp.id_comunicacao_rif = c.id_comunicacao_rif
                         ORDER BY CASE WHEN cp.cd_direcao = 'credito' THEN 0 ELSE 1 END,
                                  cp.nu_ordem
                         LIMIT 1) AS nu_cpf_cnpj_contraparte,
                       (SELECT e.no_envolvido
                          FROM investigacao.rif_tb03_envolvidos e
                         WHERE e.id_rif = c.id_rif
                           AND e.nu_indexador IS NOT DISTINCT FROM c.nu_indexador
                           {envolvido_ordem}
                         LIMIT 1) AS no_envolvido,
                       (SELECT nullif(btrim(e.nu_cpf_cnpj), '')
                          FROM investigacao.rif_tb03_envolvidos e
                         WHERE e.id_rif = c.id_rif
                           AND e.nu_indexador IS NOT DISTINCT FROM c.nu_indexador
                           {envolvido_ordem}
                         LIMIT 1) AS nu_cpf_cnpj_envolvido
                FROM investigacao.rif_tb02_comunicacoes c
                JOIN investigacao.rif_tb01_rifs r ON r.id_rif = c.id_rif
                WHERE {rif_where}
                ORDER BY coalesce(c.dt_operacao, c.dt_recebimento::date), c.id_comunicacao_rif
                LIMIT %(limite)s OFFSET %(off_rif)s
                """,
                params,
            )
            rif = _linhas(cur)

    return {
        "simba": simba,
        "rif": rif,
        "simba_total": int(qt_simba),
        "rif_total": int(qt_rif),
    }


# ---------------------------------------------------------------------------
# Grafo do fluxo financeiro — BANCO → TITULAR → CONTRAPARTE
# ---------------------------------------------------------------------------
# O grafo é montado JÁ AGREGADO, em três colunas, porque a versão anterior (um nó por
# pessoa e uma aresta por par, com layout por força) virava um novelo: um R304 comum tem
# milhares de contrapartes, e nenhuma delas dava para ler.
#
# O que muda aqui:
#   · pessoa é UMA só, somando todas as contas dela (a mesma empresa em cinco bancos era
#     cinco nós antes);
#   · o par titular↔contraparte é UMA aresta com os dois sentidos somados, em vez de uma
#     aresta por direção;
#   · o banco entra como nó de origem, ligado ao titular pelo total movimentado ali;
#   · só as N contrapartes de maior volume viram nó — todo o resto é somado num nó
#     "Demais contrapartes", para que o dinheiro que ficou de fora continue visível em vez
#     de sumir da tela.

_LIMITE_CONTRAPARTES_GRAFO = 25


def _chave_pessoa_sql(alias_doc: str, alias_nome: str) -> str:
    """Identidade da pessoa no grafo: CPF/CNPJ quando existe, senão o nome em maiúsculas."""
    return f"coalesce({alias_doc}, 'NOME:' || upper({alias_nome}))"


def grafo_simba(
    id_extrato: int, banco: str | None = None,
    direcao: str | None = None,
    valor_minimo: float | None = None,
    so_internas: bool = False,
    tp_pessoa: str | None = None,
    termo: str | None = None,
    limite: int | None = None,
    contexto: bool = False,
) -> dict:
    """Fluxo financeiro agregado em três colunas: banco → titular → contraparte.

    Devolve ``{"nos": […], "arestas": […], "bancos": […], "resumo": {…}}``. Cada nó traz
    ``tipo`` ('banco' | 'titular' | 'contraparte' | 'outros') para a tela dispor em colunas,
    e cada aresta já vem com os dois sentidos somados (``vl_entrada`` = a contraparte creditou
    o titular; ``vl_saida`` = o titular pagou a contraparte).
    ``contexto`` combina com ``termo``: além dos pares que casam com o termo, traz os pares
    inteiros dos titulares tocados — a entidade buscada aparece rodeada de 1 nível de contexto,
    em vez de sozinha com uma aresta só.
    """
    limite = max(5, min(int(limite or _LIMITE_CONTRAPARTES_GRAFO), 150))

    chave_tit = _chave_pessoa_sql("c.nu_cpf_cnpj", "c.no_titular")
    chave_cp = _chave_pessoa_sql("cp.nu_cpf_cnpj", "cp.no_contraparte")
    chaves_passagem_grafo: set[str] = set()

    where = ["cp.id_extrato = %(id)s"]
    params: dict = {"id": id_extrato}
    if banco:
        where.append("c.no_banco = %(banco)s")
        params["banco"] = banco
    if so_internas:
        where.append("cp.in_titular_no_extrato")
    if tp_pessoa in ("PF", "PJ"):
        where.append("c.tp_pessoa = %(tp)s")
        params["tp"] = tp_pessoa
    if direcao in ("credito", "debito"):
        where.append("cp.cd_direcao = %(dir)s")
        params["dir"] = direcao
    if termo:
        params["termo"] = "%" + termo.strip() + "%"
    termo_clause = (
        "(c.no_titular ILIKE %(termo)s OR cp.no_contraparte ILIKE %(termo)s "
        "OR c.nu_cpf_cnpj ILIKE %(termo)s OR cp.nu_cpf_cnpj ILIKE %(termo)s)"
    ) if termo else None

    with get_connection() as conn:
        with conn.cursor() as cur:
            # 0 · contexto do achado (clique no painel): a entidade casada sozinha é uma aresta
            # sem explicação; traz também tudo que os TITULARES dos pares casados tocam
            where_pares = list(where)
            if termo_clause:
                if contexto:
                    cur.execute(
                        f"SELECT DISTINCT {chave_tit} AS chave "
                        f"FROM investigacao.smb_tb04_contrapartes cp "
                        f"JOIN investigacao.smb_tb02_contas c ON c.id_conta_simba = cp.id_conta_simba "
                        f"WHERE {' AND '.join(where)} AND {termo_clause}",
                        params,
                    )
                    params["titulares_ctx"] = [l["chave"] for l in _linhas(cur)]
                    where_pares.append(f"({termo_clause} OR {chave_tit} = ANY(%(titulares_ctx)s))")
                else:
                    where_pares.append(termo_clause)

            # 1 · uma linha por par (titular, contraparte), somando contas e direções
            cur.execute(
                f"""
                SELECT {chave_tit} AS chave_titular,
                       (array_agg(c.no_titular ORDER BY cp.vl_total DESC))[1] AS no_titular,
                       max(c.nu_cpf_cnpj) AS doc_titular,
                       max(c.tp_pessoa) AS tp_titular,
                       {chave_cp} AS chave_contraparte,
                       (array_agg(cp.no_contraparte ORDER BY cp.vl_total DESC))[1] AS no_contraparte,
                       max(cp.nu_cpf_cnpj) AS doc_contraparte,
                       bool_or(cp.in_titular_no_extrato) AS contraparte_interna,
                       coalesce(sum(cp.vl_total) FILTER (WHERE cp.cd_direcao = 'credito'), 0) AS vl_entrada,
                       coalesce(sum(cp.vl_total) FILTER (WHERE cp.cd_direcao = 'debito'), 0) AS vl_saida,
                       sum(cp.qt_lancamentos) AS qt_lancamentos,
                       min(cp.dt_primeiro) AS dt_primeiro,
                       max(cp.dt_ultimo) AS dt_ultimo
                FROM investigacao.smb_tb04_contrapartes cp
                JOIN investigacao.smb_tb02_contas c ON c.id_conta_simba = cp.id_conta_simba
                WHERE {" AND ".join(where_pares)}
                GROUP BY 1, 5
                ORDER BY sum(cp.vl_total) DESC
                """,
                params,
            )
            pares = _linhas(cur)

            # 2 · bancos das contas afastadas, por titular (a coluna da esquerda)
            where_conta = ["c.id_extrato = %(id)s"]
            params_conta: dict = {"id": id_extrato}
            if banco:
                where_conta.append("c.no_banco = %(banco)s")
                params_conta["banco"] = banco
            if tp_pessoa in ("PF", "PJ"):
                where_conta.append("c.tp_pessoa = %(tp)s")
                params_conta["tp"] = tp_pessoa
            cur.execute(
                f"""
                SELECT {chave_tit} AS chave_titular,
                       coalesce(c.no_banco, 'Sem instituição') AS no_banco,
                       count(*) AS qt_contas,
                       coalesce(sum(c.vl_credito), 0) AS vl_credito,
                       coalesce(sum(c.vl_debito), 0) AS vl_debito
                FROM investigacao.smb_tb02_contas c
                WHERE {" AND ".join(where_conta)}
                GROUP BY 1, 2
                """,
                params_conta,
            )
            contas_banco = _linhas(cur)
            try:
                chaves_passagem_grafo = _chaves_passagem(cur, id_extrato)
            except Exception:
                chaves_passagem_grafo = set()

    if valor_minimo:
        pares = [p for p in pares if float(p["vl_entrada"]) + float(p["vl_saida"]) >= float(valor_minimo)]

    # --- ranking das contrapartes: só as maiores viram nó próprio ---
    volume_cp: dict[str, float] = {}
    for p in pares:
        chave = p["chave_contraparte"]
        volume_cp[chave] = volume_cp.get(chave, 0.0) + float(p["vl_entrada"]) + float(p["vl_saida"])
    ranking = sorted(volume_cp, key=lambda k: -volume_cp[k])
    visiveis = set(ranking[:limite])
    ocultas = [k for k in ranking[limite:]]

    nos: dict[str, dict] = {}
    arestas: dict[str, dict] = {}

    def no(chave: str, tipo: str, rotulo: str, **extra) -> dict:
        if chave not in nos:
            nos[chave] = {"id": chave, "tipo": tipo, "rotulo": rotulo, "cpf_cnpj": "",
                          "vl_entrada": 0.0, "vl_saida": 0.0, "qt_lancamentos": 0, **extra}
        return nos[chave]

    def aresta(origem: str, destino: str, tipo: str) -> dict:
        chave = f"{tipo}|{origem}|{destino}"
        if chave not in arestas:
            arestas[chave] = {"id": chave, "origem": origem, "destino": destino, "tipo": tipo,
                              "vl_entrada": 0.0, "vl_saida": 0.0, "qt_lancamentos": 0,
                              "dt_primeiro": None, "dt_ultimo": None}
        return arestas[chave]

    for p in pares:
        k_tit = "T:" + p["chave_titular"]
        n_tit = no(k_tit, "titular", p["no_titular"] or "—")
        n_tit["cpf_cnpj"] = p["doc_titular"] or ""
        n_tit["tp_pessoa"] = p["tp_titular"] or ""
        if p["chave_titular"] in chaves_passagem_grafo:
            n_tit["in_passagem"] = True

        entrada, saida = float(p["vl_entrada"]), float(p["vl_saida"])
        qt = int(p["qt_lancamentos"] or 0)
        n_tit["vl_entrada"] += entrada
        n_tit["vl_saida"] += saida
        n_tit["qt_lancamentos"] += qt

        agrupada = p["chave_contraparte"] not in visiveis
        if agrupada:
            k_cp = "OUTROS"
            n_cp = no(k_cp, "outros", "Demais contrapartes", qt_agregados=0)
        else:
            k_cp = "C:" + p["chave_contraparte"]
            n_cp = no(k_cp, "contraparte", p["no_contraparte"] or "—")
            n_cp["cpf_cnpj"] = p["doc_contraparte"] or ""
            n_cp["interna"] = bool(p["contraparte_interna"])
        # do ponto de vista da contraparte os sentidos se invertem
        n_cp["vl_entrada"] += saida
        n_cp["vl_saida"] += entrada
        n_cp["qt_lancamentos"] += qt

        a = aresta(k_tit, k_cp, "movimento")
        a["vl_entrada"] += entrada
        a["vl_saida"] += saida
        a["qt_lancamentos"] += qt
        for campo, escolher in (("dt_primeiro", min), ("dt_ultimo", max)):
            if p[campo] is not None:
                a[campo] = p[campo] if a[campo] is None else escolher(a[campo], p[campo])

    if "OUTROS" in nos:
        nos["OUTROS"]["qt_agregados"] = len(ocultas)

    # bancos: só os dos titulares que sobraram no grafo
    bancos_set: set[str] = set()
    for linha in contas_banco:
        k_tit = "T:" + linha["chave_titular"]
        if k_tit not in nos:
            continue
        nome_banco = linha["no_banco"]
        bancos_set.add(nome_banco)
        k_banco = "B:" + nome_banco
        # com filtro de direção a aresta do banco mostra só aquele sentido, senão ela contaria
        # um movimento que o resto do desenho está escondendo
        vl_c = float(linha["vl_credito"]) if direcao != "debito" else 0.0
        vl_d = float(linha["vl_debito"]) if direcao != "credito" else 0.0

        n_banco = no(k_banco, "banco", nome_banco, qt_contas=0)
        n_banco["qt_contas"] += int(linha["qt_contas"] or 0)
        n_banco["vl_entrada"] += vl_c
        n_banco["vl_saida"] += vl_d

        a = aresta(k_banco, k_tit, "conta")
        a["vl_entrada"] += vl_c
        a["vl_saida"] += vl_d
        a["qt_contas"] = a.get("qt_contas", 0) + int(linha["qt_contas"] or 0)
        nos[k_tit]["banco"] = nome_banco if not nos[k_tit].get("banco") else nos[k_tit]["banco"]

    vl_exibido = sum(volume_cp[k] for k in visiveis if k in volume_cp)
    vl_oculto = sum(volume_cp[k] for k in ocultas)

    return {
        "nos": list(nos.values()),
        "arestas": list(arestas.values()),
        "bancos": sorted(bancos_set, key=lambda x: x.lower()),
        "resumo": {
            "qt_contrapartes_total": len(volume_cp),
            "qt_contrapartes_exibidas": len(visiveis),
            "qt_contrapartes_agrupadas": len(ocultas),
            "vl_exibido": vl_exibido,
            "vl_agrupado": vl_oculto,
            "limite": limite,
        },
    }


# ---------------------------------------------------------------------------
# Alertas — leituras determinísticas do extrato, sem IA
# ---------------------------------------------------------------------------

# Descrições que caracterizam movimentação em espécie no extrato bancário. É um filtro pelo texto
# do lançamento (é o que o R304 oferece), então a leitura certa é "operações que a instituição
# descreveu como saque/depósito em dinheiro" — cheques ficam de fora de propósito.
_RE_ESPECIE = r"(SAQUE|SAQ\.|DEPOSITO|DEPÓSITO|DEP\.|DINHEIRO|ESPECIE|ESPÉCIE|NUMERARIO|NUMERÁRIO)"
_RE_NAO_ESPECIE = r"(CHEQUE|CHQ|TARIFA|IOF|JUROS|RENDIMENTO|ESTORNO)"

# Lançamentos que NÃO são dinheiro entrando ou saindo de outra pessoa/empresa — é o banco girando
# o próprio saldo do titular (aplicação automática de fim de dia e o resgate/rendimento
# correspondente) ou desfazendo uma operação anterior (estorno/devolução). Por não trazerem nome
# nem CPF/CNPJ de contraparte, já ficam fora de SMB_TB04_CONTRAPARTES — mas sem essa marcação eles
# somem dentro do genérico "sem contraparte" em vez de aparecerem como categoria própria, e um
# extrato com esse tipo de conta (poupança automática, por ex.) pode ter mais da metade do crédito
# total sendo o banco aplicando e resgatando o mesmo dinheiro repetidas vezes.
# ATENÇÃO: estas expressões são avaliadas pelo POSTGRES (operador ~*), não pelo módulo `re`. O
# Postgres usa ARE, em que `\b` é o caractere backspace e NÃO a borda de palavra — a borda é `\y`.
# Escrever `\bESTORNO\b` faz a alternativa simplesmente nunca casar, silenciosamente.
_RE_APLICACAO_AUTOMATICA = r"APLIC\w*\s*AUT|AUT\w*\s*APLIC|RESGATE\s*AUTOM|POUPAN[CÇ]A\s*AUT"
_RE_ESTORNO_DEVOLUCAO = r"^EST\s|\yESTORNO\y|\yDEVOLU"
_RE_MOVIMENTO_FINANCEIRO_PROPRIO = _RE_APLICACAO_AUTOMATICA + "|" + _RE_ESTORNO_DEVOLUCAO

# Aplicação/resgate/investimento em geral — mais larga que _RE_APLICACAO_AUTOMATICA, que só quer a
# varredura automática de fim de dia. Usada no alerta de VALORES REDONDOS, onde a pergunta é outra:
# não "o banco fez isso sozinho?", e sim "tem alguém do outro lado?". Aplicar e resgatar é o titular
# movendo o próprio dinheiro, e é redondo por natureza (aplica-se R$ 1.300.000,00, não
# R$ 1.312.457,83) — sem separar isso, o topo da lista de valores redondos vira uma fila de
# aplicações e o alerta não serve para nada.
_RE_APLICACAO_RESGATE = (r"APLICA[CÇ]|RESGATE|\yCDB\y|\yRDB\y|POUPAN[CÇ]|BB\s*FIX|"
                         r"FUNDO\s*DE\s*INVEST|INVESTIMENT")
_RE_SEM_TERCEIRO_REDONDO = _RE_APLICACAO_RESGATE + "|" + _RE_ESTORNO_DEVOLUCAO

# Pesos dos alertas de fracionamento/valor redondo/espécie/conta de passagem —
# personalizáveis em Administração > Alertas SIMBA (api/rotas/simba_alertas_admin.py),
# gravados no .env como as demais configurações administráveis do sistema (ver env_utils.py).
# Os valores abaixo são o padrão de fábrica: R$ 10 mil é o corte clássico de análise de extrato
# bancário (não é um limite legal, é o patamar a partir do qual uma operação em espécie deixa de
# ser rotineira); 3 lançamentos e múltiplo de R$ 1.000 são convenções comuns de
# fracionamento/valor redondo — nenhum deles é lei, por isso viraram configuráveis em vez de
# fixos no código. Conta de passagem: giro mínimo de R$ 50 mil com saldo residual de até 10% e
# pelo menos 10 lançamentos — o desenho de quem só repassa (entra ~= sai).
PESOS_ALERTA_SIMBA_PADRAO = {
    "limite_comunicacao": 10000,
    "min_fracionamento": 3,
    "multiplo_redondo": 1000,
    "giro_passagem": 50000,
    "tolerancia_passagem_pct": 10,
    "min_lancamentos_passagem": 10,
}
_ENV_PESOS_ALERTA_SIMBA = {
    "limite_comunicacao": "SIMBA_ALERTA_LIMITE_COMUNICACAO",
    "min_fracionamento": "SIMBA_ALERTA_MIN_FRACIONAMENTO",
    "multiplo_redondo": "SIMBA_ALERTA_MULTIPLO_REDONDO",
    "giro_passagem": "SIMBA_ALERTA_GIRO_PASSAGEM",
    "tolerancia_passagem_pct": "SIMBA_ALERTA_TOLERANCIA_PASSAGEM",
    "min_lancamentos_passagem": "SIMBA_ALERTA_MIN_LANC_PASSAGEM",
}


def pesos_alerta_simba() -> dict:
    """Lê do .env os pesos dos alertas do SIMBA, com o padrão de fábrica pra quem nunca configurou.

    Lido do arquivo a cada chamada (não cacheado) — mesmo padrão de config.ai em ia_provedores.py —
    porque a tela de Administração > Alertas SIMBA grava no .env e o efeito precisa aparecer na
    próxima consulta, sem reiniciar o servidor."""
    env = parse_env(read_env_text())
    pesos = dict(PESOS_ALERTA_SIMBA_PADRAO)
    for chave, var_env in _ENV_PESOS_ALERTA_SIMBA.items():
        bruto = env.get(var_env)
        if bruto:
            try:
                valor = int(bruto)
                if valor > 0:
                    pesos[chave] = valor
            except ValueError:
                pass
    return pesos


def _resumo_credito_extrato(cur, id_extrato: int, id_conta: int | None = None) -> dict:
    """Quebra o crédito e o débito de um extrato (ou de uma conta) em quatro categorias, para não
    confundir "dinheiro que entrou/saiu de outra pessoa" com dinheiro que só girou dentro da mesma
    investigação:

    - externo: contraparte identificada (nome/CPF/CNPJ) que NÃO é titular de conta afastada aqui —
      é o único que responde de fato "quanto X recebeu/pagou de terceiros";
    - interno: contraparte identificada que TAMBÉM é titular de conta afastada no extrato —
      dinheiro circulando entre os próprios investigados;
    - financeiro_proprio: aplicação automática/resgate/rendimento ou estorno/devolução, sem
      contraparte (o banco mexendo no saldo do próprio titular, ou desfazendo uma operação);
    - outros_sem_contraparte: o resto sem contraparte identificada (saque, depósito em espécie,
      tarifa, juros — o R304 não traz nome/CPF de quem está do outro lado).

    externo + interno + financeiro_proprio + outros_sem_contraparte == o crédito/débito total."""
    filtro_conta_l = " AND l.id_conta_simba = %(conta)s" if id_conta else ""
    filtro_conta_cp = " AND cp.id_conta_simba = %(conta)s" if id_conta else ""
    params = {"id": id_extrato, "conta": id_conta, "fin": _RE_MOVIMENTO_FINANCEIRO_PROPRIO}

    cur.execute(
        f"""
        SELECT
          coalesce(sum(cp.vl_total) FILTER (WHERE cp.cd_direcao = 'credito' AND NOT cp.in_titular_no_extrato), 0),
          coalesce(sum(cp.vl_total) FILTER (WHERE cp.cd_direcao = 'debito' AND NOT cp.in_titular_no_extrato), 0),
          coalesce(sum(cp.vl_total) FILTER (WHERE cp.cd_direcao = 'credito' AND cp.in_titular_no_extrato), 0),
          coalesce(sum(cp.vl_total) FILTER (WHERE cp.cd_direcao = 'debito' AND cp.in_titular_no_extrato), 0)
        FROM investigacao.smb_tb04_contrapartes cp
        WHERE cp.id_extrato = %(id)s{filtro_conta_cp}
        """,
        params,
    )
    vl_credito_externo, vl_debito_externo, vl_credito_interno, vl_debito_interno = cur.fetchone()

    cur.execute(
        f"""
        SELECT
          coalesce(sum(l.vl_operacao) FILTER (WHERE l.cd_natureza = 'C' AND l.tx_descricao ~* %(fin)s), 0),
          coalesce(sum(l.vl_operacao) FILTER (WHERE l.cd_natureza = 'D' AND l.tx_descricao ~* %(fin)s), 0),
          coalesce(sum(l.vl_operacao) FILTER (WHERE l.cd_natureza = 'C' AND l.tx_descricao !~* %(fin)s), 0),
          coalesce(sum(l.vl_operacao) FILTER (WHERE l.cd_natureza = 'D' AND l.tx_descricao !~* %(fin)s), 0)
        FROM investigacao.smb_tb03_lancamentos l
        WHERE l.id_extrato = %(id)s{filtro_conta_l}
          AND coalesce(l.nu_cpf_cnpj_od, l.no_od) IS NULL
        """,
        params,
    )
    (vl_credito_financeiro, vl_debito_financeiro,
     vl_credito_outros_sem_cp, vl_debito_outros_sem_cp) = cur.fetchone()

    return {
        "vl_credito_externo": vl_credito_externo, "vl_debito_externo": vl_debito_externo,
        "vl_credito_interno": vl_credito_interno, "vl_debito_interno": vl_debito_interno,
        "vl_credito_financeiro_proprio": vl_credito_financeiro, "vl_debito_financeiro_proprio": vl_debito_financeiro,
        "vl_credito_outros_sem_contraparte": vl_credito_outros_sem_cp,
        "vl_debito_outros_sem_contraparte": vl_debito_outros_sem_cp,
    }


def resumo_credito_extrato(id_extrato: int, id_conta: int | None = None) -> dict:
    """Wrapper com conexão própria de _resumo_credito_extrato — para chamadores fora deste módulo
    (ex.: a ferramenta de IA em ia_investigacao.py) que não têm um cursor aberto."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            return _resumo_credito_extrato(cur, id_extrato, id_conta=id_conta)


def calcular_alertas(id_extrato: int, cur=None) -> dict:
    """Apura os alertas do extrato. Tudo é SQL determinístico — nada de IA — para que cada número
    possa ser reconferido no extrato original.

    Aceita um cursor aberto (usado na importação, ao montar a síntese) ou abre a própria conexão."""
    if cur is not None:
        return _calcular_alertas(cur, id_extrato)
    with get_connection() as conn:
        with conn.cursor() as c:
            return _calcular_alertas(c, id_extrato)


# Identidade de uma pessoa nos alertas, em SQL: desfaz o CPF que o banco preencheu até os 14
# dígitos de um CNPJ (a forma antiga, anterior à correção do _doc — ver a docstring dele). Sem isso
# a mesma pessoa entra como duas, e o cruzamento "é a mesma dos dois lados" nunca fecha nos extratos
# importados antes da correção. Não precisa acertar o TIPO do documento, precisa devolver a MESMA
# chave para a mesma pessoa: dois documentos de 14 dígitos começados em '000' com os 11 últimos
# iguais são necessariamente o mesmo número. Use com .format('tabela.coluna').
_SQL_CHAVE_DOC = "CASE WHEN length({0}) = 14 AND left({0}, 3) = '000' THEN right({0}, 11) ELSE {0} END"

# Acima disso a busca por ciclos deixa de ser instantânea e, principalmente, deixa de ser legível:
# um afastamento com centenas de titulares produziria triangulações demais para alguém conferir uma
# a uma. Na prática o R304 tem dezenas de titulares, não centenas.
_MAX_NOS_TRIANGULACAO = 300


def _contas_passagem(cur, id_extrato: int, pesos: dict | None = None, limite: int = 100) -> list[dict]:
    """Contas de passagem (possível laranja): entra ~= sai, com giro alto.

    O desenho é o da conta que só repassa — muito crédito E muito débito, saldo residual pequeno
    diante do giro, vários lançamentos e vida curta o bastante para não ser conta operacional
    antiga. Conta-salário/conta de comércio legítimo também gira, por isso o critério exige os
    três juntos (giro + saldo residual % + mínimo de lançamentos) em vez de só "movimentou muito".

    Usa os totais já apurados em SMB_TB02 (vl_credito/vl_debito/qt_lancamentos), sem varrer o
    lançamento a lançamento: é barato mesmo no extrato grande. Pesos em pesos_alerta_simba():
    giro_passagem (soma C+D mínima), tolerancia_passagem_pct (saldo residual máximo em % do maior
    lado) e min_lancamentos_passagem.
    """
    pesos = pesos or pesos_alerta_simba()
    try:
        giro_min = float(pesos.get("giro_passagem") or 0)
    except (TypeError, ValueError):
        giro_min = 0
    try:
        tolerancia = float(pesos.get("tolerancia_passagem_pct") or 0)
    except (TypeError, ValueError):
        tolerancia = 0
    try:
        min_lanc = int(pesos.get("min_lancamentos_passagem") or 0)
    except (TypeError, ValueError):
        min_lanc = 0
    if giro_min <= 0 or tolerancia <= 0 or min_lanc <= 0:
        return []
    cur.execute(
        """
        SELECT c.id_conta_simba, c.no_titular, c.nu_cpf_cnpj, c.no_banco, c.nu_agencia,
               c.nu_conta, c.tp_pessoa, c.qt_lancamentos, c.vl_credito, c.vl_debito,
               (c.vl_credito + c.vl_debito) AS vl_giro,
               (c.vl_credito - c.vl_debito) AS vl_saldo_fluxo,
               CASE WHEN greatest(c.vl_credito, c.vl_debito) > 0
                    THEN abs(c.vl_credito - c.vl_debito) / greatest(c.vl_credito, c.vl_debito) * 100
                    ELSE NULL END AS pc_saldo_residual,
               c.dt_primeiro_lancamento, c.dt_ultimo_lancamento,
               CASE WHEN c.dt_primeiro_lancamento IS NOT NULL AND c.dt_ultimo_lancamento IS NOT NULL
                    THEN (c.dt_ultimo_lancamento - c.dt_primeiro_lancamento + 1)
                    ELSE NULL END AS qt_dias_ativa
        FROM investigacao.smb_tb02_contas c
        WHERE c.id_extrato = %(id)s
          AND c.vl_credito > 0 AND c.vl_debito > 0
          AND (c.vl_credito + c.vl_debito) >= %(giro)s
          AND c.qt_lancamentos >= %(min_qt)s
          AND (abs(c.vl_credito - c.vl_debito) / NULLIF(greatest(c.vl_credito, c.vl_debito), 0) * 100) <= %(tol)s
        ORDER BY (c.vl_credito + c.vl_debito) DESC
        LIMIT %(lim)s
        """,
        {"id": id_extrato, "giro": giro_min, "tol": tolerancia, "min_qt": min_lanc, "lim": limite},
    )
    return _linhas(cur)


def _chaves_passagem(cur, id_extrato: int, pesos: dict | None = None) -> set[str]:
    """Chaves de pessoa (doc ou NOME:...) das contas de passagem — para marcar o nó no grafo."""
    chaves: set[str] = set()
    for linha in _contas_passagem(cur, id_extrato, pesos=pesos, limite=500):
        doc = (linha.get("nu_cpf_cnpj") or "").strip()
        nome = (linha.get("no_titular") or "").strip().upper()
        if doc:
            chaves.add(doc)
            chaves.add("NOME:" + nome if nome else doc)
        elif nome:
            chaves.add("NOME:" + nome)
    return chaves


def _triangulacoes(cur, id_extrato: int, limite: int = 50) -> list[dict]:
    """Ciclos A → B → C → A entre os titulares afastados: o dinheiro sai de A, passa por B e por C
    e volta para A, sem que nenhum par isolado pareça um vaivém.

    É o alerta de "repasses entre titulares" olhado como grafo em vez de lista de pares. Só entram
    titulares do próprio afastamento (é o que o extrato permite afirmar: para uma contraparte de
    fora não se tem o outro lado da conta) e só ciclos de TRÊS pessoas distintas — ida e volta entre
    duas já aparece na lista de repasses, e não é triangulação.

    Cada transferência entre duas contas afastadas está gravada duas vezes em SMB_TB04: como débito
    no extrato de quem pagou e como crédito no de quem recebeu. Somar as duas dobraria o valor, por
    isso cada trecho fica com a visão de QUEM PAGOU (o débito), caindo para a de quem recebeu só
    quando o pagador não tem conta com aquele lançamento no arquivo."""
    cur.execute(
        f"""
        SELECT {_SQL_CHAVE_DOC.format('c.nu_cpf_cnpj')} AS k_titular, c.no_titular,
               c.nu_cpf_cnpj AS doc_titular,
               {_SQL_CHAVE_DOC.format('cp.nu_cpf_cnpj')} AS k_contraparte, cp.no_contraparte,
               cp.nu_cpf_cnpj AS doc_contraparte, cp.cd_direcao, cp.qt_lancamentos, cp.vl_total,
               cp.dt_primeiro, cp.dt_ultimo
        FROM investigacao.smb_tb04_contrapartes cp
        JOIN investigacao.smb_tb02_contas c ON c.id_conta_simba = cp.id_conta_simba
        WHERE cp.id_extrato = %(id)s AND cp.in_titular_no_extrato
          AND c.nu_cpf_cnpj IS NOT NULL AND cp.nu_cpf_cnpj IS NOT NULL
        """,
        {"id": id_extrato},
    )
    linhas = _linhas(cur)
    if not linhas:
        return []

    nomes_titular: dict[str, str] = {}
    nomes_contraparte: dict[str, str] = {}
    docs_titular: dict[str, str] = {}
    docs_contraparte: dict[str, str] = {}
    # (origem, destino) -> {"debito": acumulador, "credito": acumulador}
    trechos: dict[tuple[str, str], dict[str, dict]] = {}

    for r in linhas:
        a, b = r["k_titular"], r["k_contraparte"]
        if not a or not b or a == b:
            continue  # a == b é a própria pessoa entre duas contas dela — não é triangulação
        nomes_titular.setdefault(a, r["no_titular"] or "")
        docs_titular.setdefault(a, r["doc_titular"])
        if r["no_contraparte"]:
            nomes_contraparte.setdefault(b, r["no_contraparte"])
        docs_contraparte.setdefault(b, r["doc_contraparte"])

        origem, destino = (a, b) if r["cd_direcao"] == "debito" else (b, a)
        visao = trechos.setdefault((origem, destino), {}).setdefault(
            r["cd_direcao"], {"qt": 0, "vl": Decimal(0), "dt1": None, "dt2": None})
        visao["qt"] += int(r["qt_lancamentos"] or 0)
        visao["vl"] += Decimal(r["vl_total"] or 0)
        if r["dt_primeiro"] and (visao["dt1"] is None or r["dt_primeiro"] < visao["dt1"]):
            visao["dt1"] = r["dt_primeiro"]
        if r["dt_ultimo"] and (visao["dt2"] is None or r["dt_ultimo"] > visao["dt2"]):
            visao["dt2"] = r["dt_ultimo"]

    aresta = {par: (v.get("debito") or v.get("credito")) for par, v in trechos.items()}
    nos = {k for par in aresta for k in par}
    if len(nos) > _MAX_NOS_TRIANGULACAO:
        logger.warning("SIMBA %s: %s titulares com repasse interno — triangulação não apurada",
                       id_extrato, len(nos))
        return []

    saida: dict[str, list[str]] = {}
    for origem, destino in aresta:
        saida.setdefault(origem, []).append(destino)

    def nome(k: str) -> str:
        return nomes_titular.get(k) or nomes_contraparte.get(k) or "—"

    def doc(k: str) -> str | None:
        return docs_titular.get(k) or docs_contraparte.get(k)

    ciclos: list[dict] = []
    vistos: set[tuple[str, str, str]] = set()
    for a in saida:
        for b in saida[a]:
            for c in saida.get(b, ()):
                if c == a or c == b or (c, a) not in aresta:
                    continue
                # o mesmo ciclo é encontrado três vezes (uma por ponto de partida); a chave é a
                # rotação que começa no menor documento. A ordem inversa (A → C → B → A) NÃO é a
                # mesma coisa — é dinheiro correndo no outro sentido — e fica como ciclo próprio.
                giro = min(range(3), key=lambda i: (a, b, c)[i])
                chave = tuple((a, b, c)[(giro + i) % 3] for i in range(3))
                if chave in vistos:
                    continue
                vistos.add(chave)

                pares = [(chave[0], chave[1]), (chave[1], chave[2]), (chave[2], chave[0])]
                legs = [aresta[p] for p in pares]
                datas1 = [x["dt1"] for x in legs if x["dt1"]]
                datas2 = [x["dt2"] for x in legs if x["dt2"]]
                ciclos.append({
                    "nos": [{"doc": doc(k), "nome": nome(k)} for k in chave],
                    "trechos": [
                        {"de_doc": doc(o), "de_nome": nome(o), "para_doc": doc(d), "para_nome": nome(d),
                         "qt_lancamentos": x["qt"], "vl_total": x["vl"],
                         "dt_primeiro": x["dt1"], "dt_ultimo": x["dt2"]}
                        for (o, d), x in zip(pares, legs)
                    ],
                    # o gargalo do ciclo: por mais que um trecho tenha movimentado milhões, só pode
                    # ter dado a volta inteira o valor do MENOR trecho
                    "vl_menor": min(x["vl"] for x in legs),
                    "vl_total": sum((x["vl"] for x in legs), Decimal(0)),
                    "qt_lancamentos": sum(x["qt"] for x in legs),
                    "dt_primeiro": min(datas1) if datas1 else None,
                    "dt_ultimo": max(datas2) if datas2 else None,
                })

    ciclos.sort(key=lambda x: x["vl_menor"], reverse=True)
    return ciclos[:limite]


def _calcular_alertas(cur, id_extrato: int) -> dict:
    pesos = pesos_alerta_simba()
    params = {"id": id_extrato, "especie": _RE_ESPECIE, "nao": _RE_NAO_ESPECIE,
              "sem_terceiro": _RE_SEM_TERCEIRO_REDONDO,
              "limite": pesos["limite_comunicacao"], "min_frac": pesos["min_fracionamento"],
              "multiplo": pesos["multiplo_redondo"]}
    filtro_especie = ("l.tx_descricao ~* %(especie)s AND l.tx_descricao !~* %(nao)s")

    # 1 · movimentação em espécie por conta.
    # A linha traz o numerador E o denominador do percentual, e os dois batem com as colunas ao
    # lado. Duas armadilhas resolvidas aqui:
    #   · nem todo lançamento em espécie tem C/D (o R304 traz '*' quando a instituição não
    #     classificou), então DEPÓSITOS + SAQUES não fecha com o total em espécie — o que sobra sai
    #     em vl_sem_natureza, em vez de sumir e fazer o percentual parecer errado;
    #   · o denominador é a soma de TUDO que passou na conta, contada do mesmo jeito
    #     (sum(vl_operacao) sobre todos os lançamentos dela) e não c.vl_credito + c.vl_debito, que
    #     deixa de fora justamente os lançamentos sem C/D — numerador e denominador têm que contar
    #     as mesmas linhas, senão a razão não é de nada.
    cur.execute(
        f"""
        SELECT c.id_conta_simba, c.no_titular, c.nu_cpf_cnpj, c.no_banco, c.nu_agencia, c.nu_conta,
               count(*) AS qt_lancamentos,
               coalesce(sum(l.vl_operacao) FILTER (WHERE l.cd_natureza = 'C'), 0) AS vl_depositos,
               coalesce(sum(l.vl_operacao) FILTER (WHERE l.cd_natureza = 'D'), 0) AS vl_saques,
               coalesce(sum(l.vl_operacao) FILTER (WHERE l.cd_natureza IS NULL), 0) AS vl_sem_natureza,
               coalesce(sum(l.vl_operacao), 0) AS vl_especie,
               c.vl_credito, c.vl_debito,
               (SELECT coalesce(sum(t.vl_operacao), 0)
                  FROM investigacao.smb_tb03_lancamentos t
                 WHERE t.id_conta_simba = c.id_conta_simba) AS vl_movimentado_conta
        FROM investigacao.smb_tb03_lancamentos l
        JOIN investigacao.smb_tb02_contas c ON c.id_conta_simba = l.id_conta_simba
        WHERE l.id_extrato = %(id)s AND {filtro_especie}
        GROUP BY c.id_conta_simba, c.no_titular, c.nu_cpf_cnpj, c.no_banco, c.nu_agencia,
                 c.nu_conta, c.vl_credito, c.vl_debito
        ORDER BY sum(l.vl_operacao) DESC
        LIMIT 100
        """,
        params,
    )
    especie = _linhas(cur)

    # 2 · possível fracionamento: 3+ operações em espécie na mesma conta, no mesmo dia e no mesmo
    # sentido, somando R$ 10 mil ou mais com cada uma isoladamente abaixo desse patamar
    cur.execute(
        f"""
        SELECT c.id_conta_simba, c.no_titular, c.nu_cpf_cnpj, c.no_banco, c.nu_agencia, c.nu_conta,
               l.dt_lancamento, l.cd_natureza, count(*) AS qt_lancamentos,
               sum(l.vl_operacao) AS vl_total, max(l.vl_operacao) AS vl_maior
        FROM investigacao.smb_tb03_lancamentos l
        JOIN investigacao.smb_tb02_contas c ON c.id_conta_simba = l.id_conta_simba
        WHERE l.id_extrato = %(id)s AND l.cd_natureza IN ('C', 'D')
          AND l.dt_lancamento IS NOT NULL AND {filtro_especie}
        GROUP BY c.id_conta_simba, c.no_titular, c.nu_cpf_cnpj, c.no_banco, c.nu_agencia,
                 c.nu_conta, l.dt_lancamento, l.cd_natureza
        HAVING count(*) >= %(min_frac)s AND sum(l.vl_operacao) >= %(limite)s AND max(l.vl_operacao) < %(limite)s
        ORDER BY sum(l.vl_operacao) DESC
        LIMIT 200
        """,
        params,
    )
    fracionamento = _linhas(cur)

    # 3 · lançamentos de valor redondo a partir do valor de referência (múltiplos do passo)
    # Além do lançamento, a linha traz o que faz o valor redondo dizer alguma coisa: a conta em que
    # ele caiu, o local da transação (é o que identifica o caixa/agência de um saque, já que saque
    # não tem contraparte), se a descrição é de dinheiro em espécie e quantas vezes ESSE MESMO valor
    # se repete entre os redondos do extrato — um R$ 250.000,00 que aparece cinco vezes é um fato
    # diferente de um R$ 250.000,00 isolado.
    # A LISTA deixa de fora aplicação, resgate e estorno (_RE_SEM_TERCEIRO_REDONDO): esse dinheiro
    # não muda de dono e é redondo por natureza — aplica-se R$ 1.300.000,00, não R$ 1.312.457,83.
    # Num extrato real eles são os maiores valores redondos e, com a lista ordenada por valor e
    # cortada em 200, ocupavam sozinhos o topo do alerta. Ficar de fora não é sumir: o total geral
    # (qt_redondos/vl_redondos) continua contando TUDO e o quanto foi separado sai em
    # qt_redondos_internos/vl_redondos_internos, que a tela mostra junto do critério — e a aba
    # Lançamentos continua tendo todos eles, pesquisáveis.
    cur.execute(
        f"""
        SELECT l.id_lancamento_simba, c.id_conta_simba, c.no_titular, c.nu_cpf_cnpj,
               c.no_banco, c.nu_agencia, c.nu_conta,
               l.dt_lancamento, l.tx_descricao, l.tx_local,
               l.vl_operacao, l.cd_natureza, l.no_od, l.nu_cpf_cnpj_od,
               ({filtro_especie}) AS in_especie,
               count(*) OVER (PARTITION BY l.vl_operacao) AS qt_mesmo_valor
        FROM investigacao.smb_tb03_lancamentos l
        JOIN investigacao.smb_tb02_contas c ON c.id_conta_simba = l.id_conta_simba
        WHERE l.id_extrato = %(id)s AND l.vl_operacao >= %(limite)s
          AND mod(l.vl_operacao, %(multiplo)s) = 0
          AND NOT coalesce(l.tx_descricao ~* %(sem_terceiro)s, false)
        ORDER BY l.vl_operacao DESC
        LIMIT 200
        """,
        params,
    )
    redondos = _linhas(cur)

    # 4 · repasses entre titulares do próprio afastamento.
    # A conta (dos dois lados) e o in_mesmo_titular vêm junto porque sem eles a tabela fica
    # ambígua: o mesmo CPF/CNPJ costuma ter várias contas afastadas — e cada banco cadastrou o nome
    # de um jeito ("MUNICIPIO DE X", "PM X", "P M DE X") —, então a linha "FULANO repassou a FULANO"
    # só faz sentido quando dá para ver que são duas contas diferentes da mesma pessoa.
    cur.execute(
        f"""
        SELECT cp.id_contraparte_simba, c.id_conta_simba, c.no_titular,
               c.nu_cpf_cnpj AS doc_titular, c.no_banco, c.nu_agencia, c.nu_conta,
               cp.cd_direcao, cp.no_contraparte, cp.nu_cpf_cnpj,
               cp.nu_banco AS nu_banco_contraparte, cp.nu_agencia AS nu_agencia_contraparte,
               cp.nu_conta AS nu_conta_contraparte,
               cp.qt_lancamentos, cp.vl_total, cp.dt_primeiro, cp.dt_ultimo,
               (c.nu_cpf_cnpj IS NOT NULL AND cp.nu_cpf_cnpj IS NOT NULL
                AND {_SQL_CHAVE_DOC.format('cp.nu_cpf_cnpj')}
                  = {_SQL_CHAVE_DOC.format('c.nu_cpf_cnpj')}) AS in_mesmo_titular
        FROM investigacao.smb_tb04_contrapartes cp
        JOIN investigacao.smb_tb02_contas c ON c.id_conta_simba = cp.id_conta_simba
        WHERE cp.id_extrato = %(id)s AND cp.in_titular_no_extrato
        ORDER BY cp.vl_total DESC
        LIMIT 300
        """,
        params,
    )
    internas = _linhas(cur)

    # 5 · triangulação: ciclos fechados de repasse entre três titulares afastados
    triangulacoes = _triangulacoes(cur, id_extrato)

    # 6 · contas de passagem: entra ~= sai, com giro alto — o desenho da conta que só repassa.
    # Usa os totais por conta (barato), não os lançamentos um a um.
    passagens = _contas_passagem(cur, id_extrato, pesos=pesos)

    # totais dos alertas (independentes dos LIMIT das listas acima)
    cur.execute(
        f"""
        SELECT
          (SELECT count(*) FROM investigacao.smb_tb03_lancamentos l
            WHERE l.id_extrato = %(id)s AND {filtro_especie}) AS qt_especie,
          (SELECT coalesce(sum(l.vl_operacao), 0) FROM investigacao.smb_tb03_lancamentos l
            WHERE l.id_extrato = %(id)s AND {filtro_especie}) AS vl_especie,
          (SELECT count(*) FROM investigacao.smb_tb03_lancamentos l
            WHERE l.id_extrato = %(id)s AND l.vl_operacao >= %(limite)s
              AND mod(l.vl_operacao, %(multiplo)s) = 0) AS qt_redondos,
          (SELECT coalesce(sum(l.vl_operacao), 0) FROM investigacao.smb_tb03_lancamentos l
            WHERE l.id_extrato = %(id)s AND l.vl_operacao >= %(limite)s
              AND mod(l.vl_operacao, %(multiplo)s) = 0) AS vl_redondos,
          (SELECT count(*) FROM investigacao.smb_tb03_lancamentos l
            WHERE l.id_extrato = %(id)s AND l.vl_operacao >= %(limite)s
              AND mod(l.vl_operacao, %(multiplo)s) = 0
              AND l.tx_descricao ~* %(sem_terceiro)s) AS qt_redondos_internos,
          (SELECT coalesce(sum(l.vl_operacao), 0) FROM investigacao.smb_tb03_lancamentos l
            WHERE l.id_extrato = %(id)s AND l.vl_operacao >= %(limite)s
              AND mod(l.vl_operacao, %(multiplo)s) = 0
              AND l.tx_descricao ~* %(sem_terceiro)s) AS vl_redondos_internos,
          (SELECT coalesce(sum(cp.vl_total), 0) FROM investigacao.smb_tb04_contrapartes cp
            WHERE cp.id_extrato = %(id)s AND cp.in_titular_no_extrato) AS vl_internas,
          (SELECT count(*) FROM investigacao.smb_tb03_lancamentos l
            WHERE l.id_extrato = %(id)s AND l.cd_natureza IS NULL) AS qt_sem_natureza,
          (SELECT count(*) FROM investigacao.smb_tb02_contas c
            WHERE c.id_extrato = %(id)s
              AND c.vl_credito > 0 AND c.vl_debito > 0
              AND (c.vl_credito + c.vl_debito) >= %(giro_pass)s
              AND c.qt_lancamentos >= %(min_qt_pass)s
              AND (abs(c.vl_credito - c.vl_debito)
                   / NULLIF(greatest(c.vl_credito, c.vl_debito), 0) * 100) <= %(tol_pass)s) AS qt_passagem,
          (SELECT coalesce(sum(c.vl_credito + c.vl_debito), 0) FROM investigacao.smb_tb02_contas c
            WHERE c.id_extrato = %(id)s
              AND c.vl_credito > 0 AND c.vl_debito > 0
              AND (c.vl_credito + c.vl_debito) >= %(giro_pass)s
              AND c.qt_lancamentos >= %(min_qt_pass)s
              AND (abs(c.vl_credito - c.vl_debito)
                   / NULLIF(greatest(c.vl_credito, c.vl_debito), 0) * 100) <= %(tol_pass)s) AS vl_passagem
        """,
        {**params, "giro_pass": pesos.get("giro_passagem", 50000),
         "tol_pass": pesos.get("tolerancia_passagem_pct", 10),
         "min_qt_pass": pesos.get("min_lancamentos_passagem", 10)},
    )
    resumo = _linhas(cur)[0]
    resumo.update(_resumo_credito_extrato(cur, id_extrato))

    return {"resumo": resumo, "especie": especie, "fracionamento": fracionamento,
            "redondos": redondos, "internas": internas, "triangulacoes": triangulacoes,
            "passagens": passagens,
            # os parâmetros vão junto para a tela e o relatório poderem enunciar o critério com o
            # número realmente usado (são configuráveis em Administração > Alertas SIMBA) em vez de
            # repetir "R$ 10 mil" quando a unidade trabalha com outro patamar
            "parametros": pesos}


# ---------------------------------------------------------------------------
# Remoção
# ---------------------------------------------------------------------------

class ImportacaoEmAndamento(Exception):
    """Tentativa de apagar um extrato que a thread de importação ainda está gravando."""


def apagar_simba(id_extrato: int) -> bool:
    """Remove o extrato, suas contas/lançamentos/contrapartes e a síntese anexada. False se não existir.

    Levanta ImportacaoEmAndamento se a importação ainda estiver rodando: apagar as linhas debaixo
    da thread deixaria o COPY gravando contra um extrato que não existe mais. O caminho certo é
    cancelar primeiro (solicitar_cancelamento_simba) e apagar depois."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id_documento_sintese, cd_status FROM investigacao.smb_tb01_extratos "
                "WHERE id_extrato = %s",
                (id_extrato,),
            )
            row = cur.fetchone()
            if row is None:
                return False
            if row[1] in ("pendente", "processando"):
                raise ImportacaoEmAndamento()
            id_documento = row[0]
            cur.execute("DELETE FROM investigacao.smb_tb04_contrapartes WHERE id_extrato = %s", (id_extrato,))
            cur.execute("DELETE FROM investigacao.smb_tb03_lancamentos WHERE id_extrato = %s", (id_extrato,))
            cur.execute("DELETE FROM investigacao.smb_tb02_contas WHERE id_extrato = %s", (id_extrato,))
            cur.execute("DELETE FROM investigacao.smb_tb01_extratos WHERE id_extrato = %s", (id_extrato,))
            # conta/lançamento/contraparte deste extrato acabaram de sair; o que os anotava por
            # (TP_ORIGEM, ID_ORIGEM) não tem FK e sobreviveria apontando para o vazio
            limpar_anotacoes_orfas(cur, "simba_conta", "simba_lancamento", "simba_contraparte")
            if id_documento is not None:
                cur.execute("DELETE FROM investigacao.inq_tb04_documentos WHERE id_documento = %s",
                            (id_documento,))
        conn.commit()
    return True


# ---------------------------------------------------------------------------
# Reprocessamento (extratos importados antes das correções de CPF/CNPJ)
# ---------------------------------------------------------------------------

def _normalizar_documentos_extrato(cur, id_extrato: int) -> int:
    """Reaplica o _doc() atual aos CPF/CNPJ já gravados de um extrato (contas e lançamentos).

    Só mexe no que muda de forma — na prática, o CPF que o banco entregou preenchido com zeros até
    os 14 dígitos de um CNPJ e que ficou gravado como um CNPJ inexistente. A conversão roda em
    Python, e não em SQL, para ser exatamente a mesma regra da importação (inclusive o dígito
    verificador). Devolve quantas linhas foram reescritas."""
    reescritas = 0
    for tabela, coluna in (("smb_tb02_contas", "nu_cpf_cnpj"),
                           ("smb_tb03_lancamentos", "nu_cpf_cnpj_od")):
        cur.execute(
            f"SELECT DISTINCT {coluna} FROM investigacao.{tabela} "
            f"WHERE id_extrato = %s AND {coluna} IS NOT NULL",
            (id_extrato,),
        )
        for (bruto,) in cur.fetchall():
            novo = _doc(bruto)
            if novo == bruto:
                continue
            cur.execute(
                f"UPDATE investigacao.{tabela} SET {coluna} = %s "
                f"WHERE id_extrato = %s AND {coluna} = %s",
                (novo, id_extrato, bruto),
            )
            reescritas += cur.rowcount
    return reescritas


def reprocessar_titular_interno_simba(id_extrato: int) -> dict:
    """Reaplica, num extrato JÁ IMPORTADO, as correções de CPF/CNPJ que vieram depois dele e
    regenera a síntese textual anexada, se houver.

    Duas coisas, nessa ordem:
      · normaliza os documentos gravados (ver _normalizar_documentos_extrato) — sem isso a mesma
        pessoa continua aparecendo como CPF de um lado e como um CNPJ inexistente do outro, com as
        contrapartes partidas em duas linhas e "repassando dinheiro para si mesma" nos alertas;
      · reconsolida SMB_TB04 a partir dos lançamentos já normalizados, o que junta essas linhas
        partidas e remarca IN_TITULAR_NO_EXTRATO.

    Não reimporta o CSV e não cria documento novo — sobrescreve o texto da síntese existente.
    Idempotente: rodar de novo sem nada a corrigir não altera nada. Devolve
    {documentos_normalizados, contrapartes_corrigidas, sintese_atualizada}."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            documentos_normalizados = _normalizar_documentos_extrato(cur, id_extrato)

            cur.execute("SELECT count(*) FROM investigacao.smb_tb04_contrapartes "
                        "WHERE id_extrato = %s AND in_titular_no_extrato", (id_extrato,))
            internas_antes = cur.fetchone()[0]

            cur.execute("DELETE FROM investigacao.smb_tb04_contrapartes WHERE id_extrato = %s",
                        (id_extrato,))
            qt_contrapartes = _consolidar_contrapartes(cur, id_extrato)
            # SMB_TB04 acabou de ser regravada e o ID é IDENTITY: as linhas novas têm IDs novos, e
            # quem anotava as antigas por (TP_ORIGEM='simba_contraparte', ID_ORIGEM) ficaria
            # apontando para ID morto — sem FK, o banco não impede (ver limpar_anotacoes_orfas).
            anotacoes_orfas = limpar_anotacoes_orfas(cur, "simba_contraparte")
            cur.execute(
                "UPDATE investigacao.smb_tb01_extratos SET qt_contrapartes = %s WHERE id_extrato = %s",
                (qt_contrapartes, id_extrato),
            )

            cur.execute("SELECT count(*) FROM investigacao.smb_tb04_contrapartes "
                        "WHERE id_extrato = %s AND in_titular_no_extrato", (id_extrato,))
            contrapartes_corrigidas = cur.fetchone()[0] - internas_antes

            cur.execute(
                "SELECT no_arquivo, id_documento_sintese FROM investigacao.smb_tb01_extratos "
                "WHERE id_extrato = %s",
                (id_extrato,),
            )
            row = cur.fetchone()
            sintese_atualizada = False
            if row is not None and row[1] is not None:
                no_arquivo, id_documento_sintese = row
                texto = _montar_sintese(cur, id_extrato, no_arquivo)
                cur.execute(
                    "UPDATE investigacao.inq_tb04_documentos SET tx_conteudo = %s, qt_caracteres = %s "
                    "WHERE id_documento = %s",
                    (texto, len(texto), id_documento_sintese),
                )
                sintese_atualizada = True
        conn.commit()
    return {"documentos_normalizados": documentos_normalizados,
            "contrapartes_corrigidas": contrapartes_corrigidas,
            "sintese_atualizada": sintese_atualizada,
            "anotacoes_orfas_removidas": anotacoes_orfas}


def reprocessar_titular_interno_todos_simba() -> list[dict]:
    """Roda reprocessar_titular_interno_simba em todos os extratos já concluídos — para aplicar
    de uma vez a correção de titular interno a extratos importados antes dela existir.

    Devolve uma linha por extrato com id_extrato/no_arquivo/documentos_normalizados/
    contrapartes_corrigidas/sintese_atualizada, só para os que tiveram alguma correção
    (lista vazia == nada a corrigir)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id_extrato, no_arquivo FROM investigacao.smb_tb01_extratos "
                "WHERE cd_status = 'concluido' ORDER BY id_extrato"
            )
            extratos = cur.fetchall()
    resultado = []
    for id_extrato, no_arquivo in extratos:
        r = reprocessar_titular_interno_simba(id_extrato)
        if r["contrapartes_corrigidas"] or r["documentos_normalizados"]:
            resultado.append({"id_extrato": id_extrato, "no_arquivo": no_arquivo, **r})
    return resultado

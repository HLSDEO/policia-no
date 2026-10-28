"""Exercita contra o banco REAL o caminho de GRAVAÇÃO da Nuvem IPED, com itens de verdade lidos
do índice Lucene de um caso do IPED — sem importar o caso (nada é renomeado nem movido).

Por que existe: a importação escreve por três caminhos que só o Postgres valida de fato —
`COPY` com uma coluna JSONB (`TX_IDENTIFICADORES`), o `INSERT` de e-mail que ganhou
`NU_THREAD`/`NU_MENSAGEM`, e o `INSERT` do sidecar de nuvem. Um erro de aridade, de tipo ou de
nome de coluna aí só aparece no primeiro lote de uma importação de verdade, que demora minutos.

O caso é lido em modo somente leitura (o dumper abre o índice, nada mais), e a extração de teste é
apagada no fim.

    docker compose run --rm -v ./api:/app api python /app/tools/teste_iped_gravacao.py [pasta]

`pasta` é o caminho relativo a data/; sem argumento, usa o primeiro caso do IPED que encontrar.
"""

import json
import subprocess
import sys
import traceback

sys.path.insert(0, "/app")

from funcoes import iped_utils as U  # noqa: E402
from funcoes.db_utils import get_connection  # noqa: E402
from funcoes.investigacao_utils import (  # noqa: E402
    PASTA_DADOS,
    _drenar_arquivos,
    _gravar_email,
)
from funcoes.iped_texto import TextosDoCaso  # noqa: E402

LIMITE_ITENS = 4000  # o suficiente para pegar e-mail, sidecar, OCR e identificadores

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


# ---------------------------------------------------------------------------
# Achar o caso e ler o índice
# ---------------------------------------------------------------------------

def _regressao_ufdr():
    """A checagem que importa para quem NÃO tem caso do IPED nenhum.

    `_drenar_arquivos` e `_gravar_email` são compartilhados com a importação de UFDR
    (`investigacao_utils._processar_extracao`). Como as colunas novas entraram no `COPY` e no
    `INSERT`, uma tupla no formato do UFDR que ficasse com a aridade antiga quebraria a
    importação de celular — que é o que roda em produção hoje. Este passo grava uma linha no
    formato exato que o UFDR monta e a apaga em seguida."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO investigacao.inq_tb01_inqueritos (nu_inquerito) "
                        "VALUES ('TESTE-UFDR-COPY') RETURNING id_inquerito")
            id_inq = cur.fetchone()[0]
            cur.execute(
                """INSERT INTO investigacao.inq_tb02_extracoes
                       (id_inquerito, no_arquivo, no_fabricante, cd_status)
                   VALUES (%s, 'x.ufdr', 'samsung', 'concluido') RETURNING id_extracao""",
                (id_inq,))
            id_ext = cur.fetchone()[0]
        conn.commit()
        try:
            # exatamente o que investigacao_utils._processar_extracao empilha por <file>
            tupla_ufdr = (id_ext, "foto.jpg", "/DCIM/foto.jpg", "Imagem", "image/jpeg", 12345,
                          None, "Intact", "x.zip::/DCIM/foto.jpg", "e" * 32, None, "f" * 64,
                          None, None, "12345", None, None, None, None)
            with conn.cursor() as cur:
                _drenar_arquivos(cur, [tupla_ufdr])
                # e o e-mail no formato do UFDR, que não conhece thread nem Message-ID
                _gravar_email(cur, id_ext, {
                    "fonte": "UFDR", "remetente": "a@b.com", "destinatarios": "c@d.com",
                    "cc": None, "cco": None, "assunto": "teste", "corpo": "corpo",
                    "data": None, "tipo": "message/rfc822", "pasta": "Inbox",
                    "status": "Read", "conta": "a@b.com", "rotulos": None,
                    "anexos": None, "estado": "Intact"})
            conn.commit()
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM investigacao.arq_tb01_arquivos WHERE id_extracao = %s",
                            (id_ext,))
                arq = cur.fetchone()[0]
                cur.execute("SELECT count(*) FROM investigacao.eml_tb01_emails WHERE id_extracao = %s",
                            (id_ext,))
                eml = cur.fetchone()[0]
            if (arq, eml) != (1, 1):
                raise AssertionError(f"esperava 1 arquivo e 1 e-mail, veio {(arq, eml)}")
            return "tupla e e-mail do UFDR gravados sem alteração de chamada"
        finally:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM investigacao.eml_tb01_emails WHERE id_extracao = %s", (id_ext,))
                cur.execute("DELETE FROM investigacao.arq_tb01_arquivos WHERE id_extracao = %s", (id_ext,))
                cur.execute("DELETE FROM investigacao.inq_tb02_extracoes WHERE id_extracao = %s", (id_ext,))
                cur.execute("DELETE FROM investigacao.inq_tb01_inqueritos WHERE id_inquerito = %s", (id_inq,))
            conn.commit()


print("\n[regressão do UFDR: o COPY e o INSERT de e-mail são compartilhados]")
checar("importação de UFDR continua gravando", _regressao_ufdr)

if len(sys.argv) > 1:
    caso = PASTA_DADOS / sys.argv[1]
else:
    casos = U.listar_casos_iped_disponiveis()
    if not casos:
        print("\nNenhum caso do IPED encontrado em data/ — a parte da Nuvem IPED fica de fora.")
        print(f"\n{passos - len(falhas)}/{passos} passos ok")
        sys.exit(1 if falhas else 0)
    caso = PASTA_DADOS / casos[0]["tx_pasta"]

pasta_modulo = U._pasta_modulo_iped(caso)
print(f"\nCaso: {caso}")
print(f"Módulo: {pasta_modulo}")
if pasta_modulo is None:
    print("A pasta não é um caso válido do IPED.")
    sys.exit(1)

print(f"Versão do IPED: {U.versao_iped(pasta_modulo)!r}")

textos = TextosDoCaso(pasta_modulo)
print(f"OCR do IPED disponível: {textos.tem_ocr} | transcrições: {textos.tem_transcricao}")

caso_raiz = pasta_modulo.parent if pasta_modulo.name == "iped" else pasta_modulo
proc = subprocess.Popen(
    [U._JAVA_BIN, "-cp", U._classpath_dumper(pasta_modulo), "IpedDumper", str(caso_raiz)],
    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    text=True, encoding="utf-8", errors="replace",
)

itens = []
for i, linha in enumerate(proc.stdout):
    if i >= LIMITE_ITENS:
        break
    linha = linha.strip()
    if not linha:
        continue
    try:
        campos = json.loads(linha).get("fields") or {}
    except ValueError:
        continue
    if campos:
        itens.append(campos)
proc.terminate()
try:
    proc.wait(timeout=10)
except subprocess.TimeoutExpired:
    proc.kill()

print(f"Itens lidos do índice: {len(itens)}")
if not itens:
    print("O dumper não devolveu item nenhum — Java ausente ou índice ilegível.")
    sys.exit(1)

vazios = sum(1 for c in itens if not c)
multivalorados = sum(1 for c in itens if any(isinstance(v, list) for v in c.values()))
print(f"  itens sem campo (deveria ser 0): {vazios}")
print(f"  itens com pelo menos um campo multivalorado: {multivalorados}")

# ---------------------------------------------------------------------------
# Gravar de verdade, numa extração descartável
# ---------------------------------------------------------------------------

print("\n[gravação no banco]")


def _apagar_e_conferir(conn, id_extracao):
    """A limpeza tem que levar junto o sidecar — sem isso, apagar uma importação deixaria linhas
    órfãs em NUV_TB01_METADADOS_ARQUIVO presas por FK."""
    with conn.cursor() as cur:
        U._apagar_dados_extracao(cur, id_extracao)
        cur.execute("SELECT (SELECT count(*) FROM investigacao.arq_tb01_arquivos WHERE id_extracao = %(e)s),"
                    "       (SELECT count(*) FROM investigacao.eml_tb01_emails WHERE id_extracao = %(e)s),"
                    "       (SELECT count(*) FROM investigacao.nuv_tb01_metadados_arquivo WHERE id_extracao = %(e)s)",
                    {"e": id_extracao})
        restou = cur.fetchone()
    conn.commit()
    if any(restou):
        raise AssertionError(f"sobrou dado depois de apagar: {restou}")
    return "nada sobrou"


def _drenar(conn, buffer):
    with conn.cursor() as cur:
        _drenar_arquivos(cur, buffer)
    conn.commit()
    return f"{len(buffer)} linhas"


with get_connection() as conn:
    with conn.cursor() as cur:
        cur.execute("INSERT INTO investigacao.inq_tb01_inqueritos (nu_inquerito) "
                    "VALUES ('TESTE-IPED-GRAVACAO') RETURNING id_inquerito")
        id_inquerito = cur.fetchone()[0]
        cur.execute(
            """INSERT INTO investigacao.inq_tb02_extracoes
                   (id_inquerito, no_arquivo, no_dispositivo, no_fabricante, cd_status)
               VALUES (%s, 'teste', 'teste', 'IPED', 'processando') RETURNING id_extracao""",
            (id_inquerito,))
        id_extracao = cur.fetchone()[0]
    conn.commit()

    try:
        buffer, emails, sidecars = [], 0, 0
        com_ocr = com_identificadores = com_thread = 0

        for campos in itens:
            categorias = U._valores(campos, "category")
            if "Emails" in categorias:
                email = U._email_de_item(campos)
                if email is not None:
                    with conn.cursor() as cur:
                        _gravar_email(cur, id_extracao, email)
                    emails += 1
                    if email["thread"]:
                        com_thread += 1
                continue
            if "Contacts" in categorias:
                continue
            tupla = U._item_para_arquivo(id_extracao, campos, textos)
            if tupla is not None:
                buffer.append(tupla)
                if tupla[12]:
                    com_ocr += 1
                if tupla[16] is not None:
                    com_identificadores += 1
            with conn.cursor() as cur:
                antes = sidecars
                contagens = {}
                U._rotear_produto_google(cur, id_extracao, pasta_modulo, campos, contagens)
                sidecars += contagens.get("metadados_nuvem", 0)

        checar("COPY de arquivos (inclui a coluna JSONB de identificadores)",
               lambda: _drenar(conn, buffer))

        checar("e-mails gravados com NU_THREAD/NU_MENSAGEM",
               lambda: f"{emails} e-mails, {com_thread} com thread")
        checar("sidecars de nuvem gravados", lambda: sidecars)

        # confere o que realmente entrou no banco, e não só o que o Python achou que mandou
        def _conferir():
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT count(*) AS arquivos,
                              count(*) FILTER (WHERE tx_ocr IS NOT NULL) AS com_ocr,
                              count(*) FILTER (WHERE tx_identificadores IS NOT NULL) AS com_ident,
                              count(*) FILTER (WHERE in_extensao_divergente) AS ext_divergente,
                              count(DISTINCT no_categoria) AS categorias
                         FROM investigacao.arq_tb01_arquivos WHERE id_extracao = %s""",
                    (id_extracao,))
                arq = dict(zip([d.name for d in cur.description], cur.fetchone()))
                cur.execute(
                    """SELECT count(*) AS emails,
                              count(*) FILTER (WHERE nu_thread IS NOT NULL) AS com_thread,
                              count(*) FILTER (WHERE nu_mensagem IS NOT NULL) AS com_message_id,
                              max(length(tx_destinatarios)) AS maior_destinatarios
                         FROM investigacao.eml_tb01_emails WHERE id_extracao = %s""",
                    (id_extracao,))
                eml = dict(zip([d.name for d in cur.description], cur.fetchone()))
                cur.execute(
                    "SELECT count(*) FROM investigacao.nuv_tb01_metadados_arquivo WHERE id_extracao = %s",
                    (id_extracao,))
                nuv = cur.fetchone()[0]
            return {"arq": arq, "eml": eml, "sidecars_no_banco": nuv}

        estado = checar("contagens lidas de volta do banco", _conferir)

        # o JSONB tem que voltar como objeto consultável, não como texto
        def _consulta_jsonb():
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT jsonb_object_keys(tx_identificadores) AS tipo, count(*)
                         FROM investigacao.arq_tb01_arquivos
                        WHERE id_extracao = %s AND tx_identificadores IS NOT NULL
                        GROUP BY 1 ORDER BY 2 DESC""",
                    (id_extracao,))
                return {linha[0]: linha[1] for linha in cur.fetchall()}
        checar("TX_IDENTIFICADORES consultável como JSONB", _consulta_jsonb)

        def _consulta_contains():
            """O operador que o índice GIN jsonb_path_ops serve: 'quais arquivos citam X'."""
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT tx_identificadores->'cpf' FROM investigacao.arq_tb01_arquivos "
                    "WHERE id_extracao = %s AND tx_identificadores ? 'cpf' LIMIT 1", (id_extracao,))
                linha = cur.fetchone()
                if not linha:
                    return "nenhum CPF neste caso (nada a testar)"
                cpf = linha[0][0]
                cur.execute(
                    "SELECT count(*) FROM investigacao.arq_tb01_arquivos "
                    "WHERE id_extracao = %s AND tx_identificadores @> %s::jsonb",
                    (id_extracao, json.dumps({"cpf": [cpf]})))
                return f"@> achou {cur.fetchone()[0]} arquivo(s) com o CPF {cpf}"
        checar("busca por identificador com @> (o operador do índice GIN)", _consulta_contains)

        checar("apagar_iped/_apagar_dados_extracao limpa as tabelas novas",
               lambda: _apagar_e_conferir(conn, id_extracao))

    finally:
        textos.fechar()
        with conn.cursor() as cur:
            U._apagar_dados_extracao(cur, id_extracao)
            cur.execute("DELETE FROM investigacao.inq_tb02_extracoes WHERE id_extracao = %s",
                        (id_extracao,))
            cur.execute("DELETE FROM investigacao.inq_tb01_inqueritos WHERE id_inquerito = %s",
                        (id_inquerito,))
        conn.commit()
        print("  (extração de teste removida)")


print(f"\n{passos - len(falhas)}/{passos} passos ok")
if falhas:
    print("FALHARAM: " + ", ".join(falhas))
sys.exit(1 if falhas else 0)

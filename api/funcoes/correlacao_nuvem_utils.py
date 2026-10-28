"""Correlação Nuvem × aparelho — o arquivo que sumiu do celular e continua na conta na nuvem.

O caso que a tela resolve: o suspeito apagou a foto/vídeo/áudio do WhatsApp no aparelho antes da
apreensão. O Cellebrite recupera parte disso do não alocado, mas o que o banco do WhatsApp já
tinha compactado se perde. A conta do Google, porém, guarda uma cópia — o backup automático da
galeria sobe `WhatsApp Images`, `WhatsApp Video` e `WhatsApp Audio` para o Google Photos, e essa
cópia sobrevive ao apagamento no telefone.

Ou seja: a mídia que interessa costuma estar viva na extração de nuvem e ausente na do celular.
Enquanto as duas extrações eram só duas listas de arquivos, achar isso à mão significava abrir
duas galerias de centenas de milhares de itens.

## Por que não dá para reaproveitar "Comparar celulares"

`comparar_celulares_utils.comparar_arquivos` já cruza `ARQ_TB01_ARQUIVOS` por MD5 entre extrações
do mesmo inquérito, e a extração-ponte da Nuvem IPED (`NO_FABRICANTE = 'IPED'`) já entra lá como
se fosse mais um aparelho. Mas aquela tela responde à pergunta oposta: acha o que está em DOIS ou
mais lugares. Aqui a pergunta é o **anti-join** — o que está na nuvem e NÃO está mais no celular.

## Os três eixos, do mais forte ao mais circunstancial

**nome** (padrão) — o WhatsApp nomeia mídia de forma determinística e esse nome sobrevive ao
backup para o Google Photos:

    IMG-AAAAMMDD-WA####.jpg   VID-AAAAMMDD-WA####.mp4   AUD-AAAAMMDD-WA####.opus
    PTT-AAAAMMDD-WA####.opus  DOC-AAAAMMDD-WA####.pdf   STK-AAAAMMDD-WA####.webp

Um `IMG-20230715-WA0042.jpg` na nuvem sem par no celular É mídia de WhatsApp que saiu do
aparelho. E como `WA####` é sequencial por dia, dá para detectar **buraco na sequência**: se o
celular tem WA0041 e WA0043 do mesmo dia e o WA0042 só existe na nuvem, houve apagamento
SELETIVO — uma frase muito mais forte num relatório do que "arquivo ausente". É o
`in_lacuna` de cada item.

**hash** — anti-join por MD5. Prova forte, com uma limitação que a tela precisa dizer em voz
alta: o Google **reencoda** mídia no upload (Google Fotos em "economia de espaço"), então o MD5
da nuvem difere do MD5 do celular para foto e vídeo. Funciona bem para PDF, documento e áudio (o
WhatsApp guarda `.opus` intacto); mal para foto reencodada. Mesma ressalva de
`comparar_celulares_utils`.

**tamanho** — para o anexo que o Cellebrite viu na conversa mas cujo binário não recuperou
(`WPP_TB05_ANEXOS` com `CO_CAMINHO_ZIP IS NULL`): casa `QT_BYTES` exato. Circunstancial, mas é
exatamente o caso em que os outros dois eixos não têm o que comparar.

## Os três estados — o miolo do valor investigativo

Não é um booleano "está/não está":

| estado         | como se detecta                                             | o que significa |
|----------------|-------------------------------------------------------------|-----------------|
| `recuperado`   | linha em ARQ_TB01 do celular com CD_STATUS_ARQUIVO='Deleted' | o Cellebrite carveou; a nuvem CONFIRMA e entrega o binário íntegro |
| `apagado`      | referência em *_TB05_ANEXOS, nenhuma linha em ARQ_TB01       | a mensagem prova que o arquivo existiu na conversa; **a nuvem é a única cópia** |
| `so_nuvem`     | nem referência                                              | apagado antes do backup do banco do WhatsApp, ou nunca esteve neste aparelho |

E o ganho que fecha o caso: o item da nuvem TEM binário no `storage-N.db` do caso do IPED, e
`iped_cache.ler_blob` já sabe servi-lo. A tela não mostra só que o arquivo sumiu — mostra a foto
que o suspeito apagou, dentro da conversa em que ela trafegou.

## Guarda contra falso positivo

O arquivo da nuvem pode nunca ter estado NESTE aparelho (outro celular da mesma conta, álbum
compartilhado, anexo de e-mail). Dois amarradores, os dois vindos do sidecar que
`nuvem_metadados.py` passou a gravar: `TP_ORIGEM_UPLOAD` = "Upload de aparelho" e
`NO_PASTA_DISPOSITIVO` = a pasta de origem no telefone ("WhatsApp Images"). O filtro
`so_do_aparelho` exige os dois; sem o sidecar (caso importado antes desta versão) ele não tem o
que filtrar e a tela avisa.

Ver docs/correlacao_nuvem.md.
"""

import logging
import re

from funcoes.db_utils import get_connection

logger = logging.getLogger(__name__)

LIMITE_MAX = 200

# Nome canônico da mídia do WhatsApp. O mesmo padrão em duas formas: POSIX para o Postgres
# filtrar/agrupar sem trazer a tabela para o Python, e `re` para desmontar o nome dos itens da
# página em (tipo, data, sequencial).
_SQL_REGEX_WHATSAPP = r"^(IMG|VID|AUD|PTT|STK|DOC)-[0-9]{8}-WA[0-9]{4}"
_RE_WHATSAPP = re.compile(r"^(IMG|VID|AUD|PTT|STK|DOC)-(\d{8})-WA(\d{4})", re.I)

# Rótulo de cada prefixo, para a tela não obrigar o investigador a decorar a convenção.
TIPOS_WHATSAPP = {
    "IMG": "Imagem", "VID": "Vídeo", "AUD": "Áudio enviado como arquivo",
    "PTT": "Áudio gravado na hora", "STK": "Figurinha", "DOC": "Documento",
}

# Categorias que são conteúdo do usuário — mesmo vocabulário de comparar_celulares_utils, e
# mesma razão: sem isso o cruzamento vem soterrado em ícone e cache de app.
CATEGORIAS_USUARIO = ("Imagem", "Vídeo", "Áudio", "Documento")

# Abaixo disso é miniatura/ícone, não peça. Mesmo piso de comparar_celulares_utils.
TAMANHO_MINIMO_PADRAO = 8 * 1024

_CRITERIOS = ("nome", "hash", "tamanho")

_ESTADOS = ("so_nuvem", "apagado", "recuperado")

# Sempre qualificadas com `f.`: a consulta de detalhe junta `filtrado f` com
# `arq_tb01_arquivos a` (para o contexto de conversa), e as duas têm qt_bytes/no_arquivo — sem o
# prefixo o Postgres recusa a coluna como ambígua.
#
# `in_lacuna` NÃO entra na ordenação de propósito: a lacuna de sequência é calculada depois, em
# _marcar_lacunas, só para os itens da página (calcular no SQL exigiria um EXISTS por linha sobre
# substring de nome, que nenhum índice cobre). Ela é selo na tela, não critério de ordem — se
# ordenasse por ela, a ordem mudaria conforme a página, que é pior do que não ordenar.
_ORDENACOES = {
    "relevancia": "f.cd_ordem_estado, f.qt_bytes DESC NULLS LAST",
    "tamanho": "f.qt_bytes DESC NULLS LAST",
    "data": "f.dt_referencia DESC NULLS LAST",
    "nome": "lower(f.no_arquivo)",
}

# As três famílias de conversa do UFDR têm o mesmo desenho de tabelas — descrever isso uma vez
# evita a mesma consulta escrita três vezes (mesmo recurso de comparar_celulares_utils).
_FONTES_CHAT = (
    {"fonte": "wpp", "prefixo": "wpp", "identificador": "nu_whatsapp_id", "app": "NULL::text"},
    {"fonte": "sms", "prefixo": "sms", "identificador": "nu_telefone", "app": "NULL::text"},
    {"fonte": "msg", "prefixo": "msg", "identificador": "nu_identificador", "app": "c.tp_app"},
)


def _linhas(cur) -> list[dict]:
    colunas = [d.name for d in cur.description]
    return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


def listar_fontes(id_inquerito: int) -> dict:
    """As extrações do inquérito separadas em nuvem e aparelho, com o que a tela precisa para
    montar os chips e avisar quando o cruzamento não vai funcionar.

    `qt_com_sidecar` é o número que decide se o filtro "só o que subiu deste aparelho" pode ser
    oferecido: caso do IPED importado antes de a leitura de sidecar existir não tem nenhuma linha
    em NUV_TB01_METADADOS_ARQUIVO, e aí a origem do upload é desconhecida — a tela mostra o aviso
    em vez de um filtro que zeraria o resultado sem explicação."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT e.id_extracao, e.no_arquivo, e.no_dispositivo, e.no_fabricante,
                       e.dt_extracao,
                       -- nuvem = IPED (ponte) OU UFDR "Google Warrant Return" (IN_NUVEM). coalesce
                       -- porque os campos são nullable: sem ele o teste dá NULL para extração sem
                       -- fabricante e o ORDER BY DESC jogaria essas para o topo, acima das de nuvem
                       -- (no Postgres, DESC é NULLS FIRST)
                       (coalesce(e.in_nuvem, false) OR coalesce(e.no_fabricante = 'IPED', false)) AS in_nuvem,
                       coalesce(e.qt_arquivos, 0) AS qt_arquivos,
                       (SELECT count(*) FROM investigacao.arq_tb01_arquivos a
                         WHERE a.id_extracao = e.id_extracao AND a.co_hash_md5 IS NOT NULL
                       ) AS qt_arquivos_com_hash,
                       (SELECT count(*) FROM investigacao.arq_tb01_arquivos a
                         WHERE a.id_extracao = e.id_extracao
                           AND a.no_arquivo ~* %(regex_wa)s
                       ) AS qt_midia_whatsapp,
                       (SELECT count(*) FROM investigacao.nuv_tb01_metadados_arquivo n
                         WHERE n.id_extracao = e.id_extracao
                       ) AS qt_com_sidecar
                  FROM investigacao.inq_tb02_extracoes e
                 WHERE e.id_inquerito = %(id_inquerito)s AND e.cd_status = 'concluido'
                 ORDER BY (coalesce(e.in_nuvem, false) OR coalesce(e.no_fabricante = 'IPED', false)) DESC,
                          e.id_extracao
                """,
                {"id_inquerito": id_inquerito, "regex_wa": _SQL_REGEX_WHATSAPP},
            )
            todas = _linhas(cur)
    return {
        "nuvem": [e for e in todas if e["in_nuvem"]],
        "aparelhos": [e for e in todas if not e["in_nuvem"]],
    }


def _separar_extracoes(cur, id_inquerito: int, extracoes: list[int] | None) -> tuple[list[int], list[int]]:
    """(ids de nuvem, ids de aparelho) do inquérito, restritos aos escolhidos na tela.

    Passa pelo banco em vez de confiar na lista que veio da URL: é o que garante que ninguém
    cruze a nuvem de um inquérito com o celular de outro mandando ?extracoes= à mão (mesma
    guarda de comparar_celulares_utils._extracoes_do_inquerito)."""
    cur.execute(
        """SELECT id_extracao, (coalesce(in_nuvem, false) OR coalesce(no_fabricante = 'IPED', false)) AS in_nuvem
             FROM investigacao.inq_tb02_extracoes
            WHERE id_inquerito = %(id_inquerito)s AND cd_status = 'concluido'
            ORDER BY id_extracao""",
        {"id_inquerito": id_inquerito},
    )
    escolhidas = set(extracoes or [])
    nuvem, aparelhos = [], []
    for id_extracao, in_nuvem in cur.fetchall():
        if escolhidas and id_extracao not in escolhidas:
            continue
        (nuvem if in_nuvem else aparelhos).append(id_extracao)
    return nuvem, aparelhos


def _sql_presenca_no_aparelho(criterio: str) -> str:
    """A condição de "este arquivo da nuvem tem par no aparelho", por eixo. Correlacionada com o
    alias `a` (o arquivo da nuvem) e `p` (candidato no aparelho)."""
    if criterio == "hash":
        return "a.co_hash_md5 IS NOT NULL AND p.co_hash_md5 = a.co_hash_md5"
    if criterio == "tamanho":
        # tamanho sozinho casaria qualquer par de arquivos pequenos; exige categoria igual e um
        # piso de tamanho para o eixo circunstancial não virar ruído puro
        return ("a.qt_bytes IS NOT NULL AND a.qt_bytes >= %(tamanho_min)s"
                " AND p.qt_bytes = a.qt_bytes AND p.no_categoria = a.no_categoria")
    return "lower(p.no_arquivo) = lower(a.no_arquivo)"


def _sql_referencia_em_conversa() -> str:
    """EXISTS: "o nome deste arquivo aparece como anexo de alguma conversa do aparelho".

    É o que separa `apagado` de `so_nuvem`: a mensagem continua no banco do WhatsApp mesmo depois
    de o binário sumir, então o anexo órfão é a prova de que o arquivo ESTEVE no aparelho."""
    return " OR ".join(
        f"""EXISTS (
              SELECT 1 FROM investigacao.{f['prefixo']}_tb05_anexos x
                JOIN investigacao.{f['prefixo']}_tb04_mensagens m ON m.id_mensagem = x.id_mensagem
                JOIN investigacao.{f['prefixo']}_tb02_conversas c ON c.id_conversa = m.id_conversa
               WHERE c.id_extracao = ANY(%(aparelhos)s)
                 AND lower(x.no_arquivo) = lower(a.no_arquivo))"""
        for f in _FONTES_CHAT
    )


def _sql_chat_do_arquivo() -> str:
    """Contexto da conversa em que o arquivo trafegou, pelo NOME do anexo (e não pelo
    CO_CAMINHO_ZIP como em comparar_celulares_utils): o item da nuvem tem outro ponteiro de
    binário, e o nome é justamente o que os dois lados compartilham.

    A frase que isso monta — "IMG-20230715-WA0042.jpg foi recebida de 'João' em 15/07 14:02 e não
    está mais no aparelho" — é o resultado da tela."""
    partes = []
    for f in _FONTES_CHAT:
        p = f["prefixo"]
        partes.append(f"""
        SELECT '{f['fonte']}'::text AS fonte, {f['app']} AS tp_app,
               m.id_mensagem, c.id_conversa,
               coalesce(c.no_grupo, (
                   SELECT string_agg(DISTINCT ct2.no_contato, ', ')
                     FROM investigacao.{p}_tb03_participantes p2
                     JOIN investigacao.{p}_tb01_contatos ct2 ON ct2.id_contato = p2.id_contato
                    WHERE p2.id_conversa = c.id_conversa
                      AND NOT coalesce(ct2.in_dono_dispositivo, false)
               )) AS no_conversa,
               m.dt_mensagem, m.in_enviada,
               rem.no_contato AS no_remetente, rem.{f['identificador']} AS nu_remetente,
               m.in_apagada_remetente
          FROM investigacao.{p}_tb05_anexos x
          JOIN investigacao.{p}_tb04_mensagens m ON m.id_mensagem = x.id_mensagem
          JOIN investigacao.{p}_tb02_conversas c ON c.id_conversa = m.id_conversa
          LEFT JOIN investigacao.{p}_tb01_contatos rem ON rem.id_contato = m.id_contato_remetente
         WHERE lower(x.no_arquivo) = lower(a.no_arquivo) AND c.id_extracao = ANY(%(aparelhos)s)
        """)
    return (
        "SELECT u.* FROM (" + "\nUNION ALL\n".join(partes) + ") u "
        # a mais antiga conta a origem do arquivo naquele aparelho; reenvio posterior é
        # consequência, não o evento que interessa
        "ORDER BY u.dt_mensagem NULLS LAST LIMIT 1"
    )


def _wa_partes(nome: str | None) -> tuple[str, str, int] | None:
    """"IMG-20230715-WA0042.jpg" -> ("IMG", "20230715", 42). None quando não é nome canônico."""
    m = _RE_WHATSAPP.match((nome or "").strip())
    if not m:
        return None
    return m.group(1).upper(), m.group(2), int(m.group(3))


def _marcar_lacunas(cur, itens: list[dict], aparelhos: list[int]) -> None:
    """Marca `in_lacuna` nos itens cuja sequência do WhatsApp está FALTANDO no aparelho embora o
    aparelho tenha vizinhos do mesmo dia e tipo.

    Só um item ausente não diz muito — pode ser mídia que nunca chegou àquele telefone. Ausente
    ENTRE dois presentes, sim: no dia 15/07 o aparelho tem WA0041 e WA0043 e não tem WA0042, que
    está na nuvem. Isso é apagamento seletivo, não sincronização incompleta.

    Roda como consulta separada, só para as chaves (tipo, data) da PÁGINA em tela — trazer o
    universo de sequências do aparelho inteiro para calcular isso seria pagar o preço da tabela
    toda por 25 linhas."""
    chaves = {(it["wa_tipo"], it["wa_data"]) for it in itens if it.get("wa_tipo")}
    if not chaves or not aparelhos:
        return
    # filtra por tipo e data em arrays separados (e não por par tipo+data): comparar um
    # row-constructor com um array de tuplas exige um tipo composto no Postgres, e a folga que
    # isso traz — no máximo (tipos × datas) grupos em vez dos pares exatos — é irrelevante para
    # os 25 itens de uma página, porque o casamento fino acontece no dicionário logo abaixo
    cur.execute(
        """
        SELECT upper(substring(a.no_arquivo from 1 for 3)) AS wa_tipo,
               substring(a.no_arquivo from 5 for 8)        AS wa_data,
               array_agg(DISTINCT (substring(a.no_arquivo from 16 for 4))::int) AS sequencias
          FROM investigacao.arq_tb01_arquivos a
         WHERE a.id_extracao = ANY(%(aparelhos)s)
           AND a.no_arquivo ~* %(regex_wa)s
           AND upper(substring(a.no_arquivo from 1 for 3)) = ANY(%(tipos)s)
           AND substring(a.no_arquivo from 5 for 8) = ANY(%(datas)s)
         GROUP BY 1, 2
        """,
        {"aparelhos": aparelhos, "regex_wa": _SQL_REGEX_WHATSAPP,
         "tipos": sorted({t for t, _ in chaves}), "datas": sorted({d for _, d in chaves})},
    )
    presentes = {(linha[0], linha[1]): set(linha[2] or []) for linha in cur.fetchall()}
    for item in itens:
        chave = (item.get("wa_tipo"), item.get("wa_data"))
        sequencias = presentes.get(chave)
        seq = item.get("wa_sequencial")
        if not sequencias or seq is None:
            continue
        # vizinho de qualquer lado prova que o aparelho recebeu mídia daquele dia/tipo
        item["in_lacuna"] = bool(any(s < seq for s in sequencias)
                                 and any(s > seq for s in sequencias))


def correlacionar_arquivos(
    id_inquerito: int,
    extracoes: list[int] | None = None,
    criterio: str = "nome",
    estado: str | None = None,
    categoria: str | None = None,
    so_whatsapp: bool = True,
    so_do_aparelho: bool = False,
    busca: str | None = None,
    tamanho_min: int | None = None,
    ordenar: str = "relevancia",
    pagina: int = 1,
    limite: int = 25,
) -> dict:
    """Arquivos da nuvem sem par no aparelho. Devolve {total, pagina, limite, criterio, itens}.

    `criterio` escolhe o eixo (nome/hash/tamanho); `estado` filtra
    so_nuvem/apagado/recuperado; `so_whatsapp` restringe ao nome canônico da mídia do WhatsApp
    (o padrão, e o caso de uso que motivou a tela); `so_do_aparelho` exige o sidecar dizendo que
    o arquivo subiu de um celular, e não de álbum compartilhado ou navegador."""
    pagina = max(1, pagina)
    limite = max(1, min(LIMITE_MAX, limite))
    criterio = criterio if criterio in _CRITERIOS else "nome"
    ordem = _ORDENACOES.get(ordenar, _ORDENACOES["relevancia"])
    estado = estado if estado in _ESTADOS else None

    with get_connection() as conn:
        with conn.cursor() as cur:
            nuvem, aparelhos = _separar_extracoes(cur, id_inquerito, extracoes)
            if not nuvem or not aparelhos:
                return {"total": 0, "pagina": pagina, "limite": limite, "criterio": criterio,
                        "itens": [], "sem_par": True}

            parametros = {
                "nuvem": nuvem,
                "aparelhos": aparelhos,
                "categorias_usuario": list(CATEGORIAS_USUARIO),
                "tamanho_min": tamanho_min if tamanho_min is not None else TAMANHO_MINIMO_PADRAO,
                "regex_wa": _SQL_REGEX_WHATSAPP,
                "categoria": categoria or None,
                "busca": f"%{busca.strip()}%" if (busca or "").strip() else None,
                "limite": limite,
                "deslocamento": (pagina - 1) * limite,
            }

            # `base` é o universo: arquivo de conteúdo da nuvem, com o estado já resolvido.
            # O estado sai de dois LEFT JOIN LATERAL baratos (presença no aparelho e referência
            # em conversa) em vez de subconsulta por linha na projeção.
            sql_base = f"""
            WITH base AS (
                SELECT a.id_arquivo, a.id_extracao, a.no_arquivo, a.tx_caminho, a.no_categoria,
                       a.tp_mime, a.qt_bytes, a.co_hash_md5,
                       -- só uma amostra: OCR de PDF longo passa de 100 KB por linha e a tela
                       -- usa isso como prévia, não como texto integral
                       left(a.tx_ocr, 400) AS tx_ocr,
                       left(a.tx_transcricao, 400) AS tx_transcricao,
                       (a.co_caminho_zip IS NOT NULL) AS disponivel,
                       n.dt_captura, n.dt_criacao_servidor, n.dt_modificacao_servidor,
                       n.tp_origem_upload, n.no_dispositivo_upload, n.no_pasta_dispositivo,
                       n.in_lixeira, n.no_proprietario, n.tx_compartilhado_com,
                       n.vl_latitude, n.vl_longitude,
                       coalesce(n.dt_captura, n.dt_criacao_servidor, a.dt_modificacao)
                           AS dt_referencia,
                       par.id_arquivo    AS par_id_arquivo,
                       par.id_extracao   AS par_id_extracao,
                       par.tx_caminho    AS par_tx_caminho,
                       par.cd_status_arquivo AS par_status,
                       (a.no_arquivo ~* %(regex_wa)s) AS in_whatsapp,
                       ({_sql_referencia_em_conversa()}) AS in_referenciado
                  FROM investigacao.arq_tb01_arquivos a
                  -- LATERAL com LIMIT 1 e não JOIN simples: o mesmo nome de arquivo pode ter
                  -- sidecar em duas pastas do Drive, e um JOIN duplicaria a linha do arquivo
                  LEFT JOIN LATERAL (
                        SELECT * FROM investigacao.nuv_tb01_metadados_arquivo n0
                         WHERE n0.id_extracao = a.id_extracao
                           AND n0.no_arquivo_alvo = lower(a.no_arquivo)
                         ORDER BY n0.id_metadado
                         LIMIT 1
                  ) n ON true
                  LEFT JOIN LATERAL (
                        SELECT p.id_arquivo, p.id_extracao, p.tx_caminho, p.cd_status_arquivo
                          FROM investigacao.arq_tb01_arquivos p
                         WHERE p.id_extracao = ANY(%(aparelhos)s)
                           AND ({_sql_presenca_no_aparelho(criterio)})
                         -- o par íntegro manda: se o celular ainda tem o arquivo inteiro, não é
                         -- caso de "sumiu"; a cópia carveada é que interessa como confirmação
                         ORDER BY (p.cd_status_arquivo = 'Deleted'), p.id_arquivo
                         LIMIT 1
                  ) par ON true
                 WHERE a.id_extracao = ANY(%(nuvem)s)
                   AND a.no_categoria = ANY(%(categorias_usuario)s)
            ),
            classificado AS (
                SELECT b.*,
                       CASE
                         WHEN b.par_id_arquivo IS NOT NULL AND b.par_status = 'Deleted'
                              THEN 'recuperado'
                         WHEN b.par_id_arquivo IS NOT NULL THEN 'presente'
                         WHEN b.in_referenciado THEN 'apagado'
                         ELSE 'so_nuvem'
                       END AS cd_estado
                  FROM base b
            ),
            filtrado AS (
                SELECT c.*,
                       CASE c.cd_estado WHEN 'apagado' THEN 0 WHEN 'recuperado' THEN 1 ELSE 2 END
                           AS cd_ordem_estado,
                       false AS in_lacuna
                  FROM classificado c
                 -- 'presente' nunca aparece: a tela é sobre o que NÃO está mais no aparelho
                 WHERE c.cd_estado <> 'presente'
                   AND (%(estado)s::text IS NULL OR c.cd_estado = %(estado)s)
                   AND (%(categoria)s::text IS NULL OR c.no_categoria = %(categoria)s)
                   AND (NOT %(so_whatsapp)s::boolean OR c.in_whatsapp)
                   AND (NOT %(so_do_aparelho)s::boolean
                        OR (c.tp_origem_upload = 'Upload de aparelho'
                            AND c.no_pasta_dispositivo IS NOT NULL))
                   AND (%(busca)s::text IS NULL
                        OR c.no_arquivo ILIKE %(busca)s OR c.tx_caminho ILIKE %(busca)s)
            )
            """

            cur.execute(
                sql_base + " SELECT count(*) FROM filtrado",
                {**parametros, "estado": estado, "so_whatsapp": so_whatsapp,
                 "so_do_aparelho": so_do_aparelho},
            )
            total = cur.fetchone()[0]
            if not total:
                return {"total": 0, "pagina": pagina, "limite": limite, "criterio": criterio,
                        "itens": []}

            cur.execute(
                sql_base + f"""
                SELECT f.*, (SELECT row_to_json(ch) FROM ({_sql_chat_do_arquivo()}) ch) AS chat
                  FROM filtrado f
                  JOIN investigacao.arq_tb01_arquivos a ON a.id_arquivo = f.id_arquivo
                 ORDER BY {ordem}, f.id_arquivo
                 LIMIT %(limite)s OFFSET %(deslocamento)s
                """,
                {**parametros, "estado": estado, "so_whatsapp": so_whatsapp,
                 "so_do_aparelho": so_do_aparelho},
            )
            itens = _linhas(cur)

            for item in itens:
                partes = _wa_partes(item.get("no_arquivo"))
                item["wa_tipo"], item["wa_data"], item["wa_sequencial"] = partes or (None, None, None)
                item["wa_rotulo"] = TIPOS_WHATSAPP.get(item["wa_tipo"] or "")
            _marcar_lacunas(cur, itens, aparelhos)

    return {"total": total, "pagina": pagina, "limite": limite, "criterio": criterio,
            "itens": itens}


def resumo(id_inquerito: int, extracoes: list[int] | None = None) -> dict:
    """Contagem por estado, para os chips da tela — a mesma classificação de
    `correlacionar_arquivos` pelo eixo do nome, sem paginação nem contexto de conversa.

    Existe separado porque a tela precisa do panorama ("38 apagados, 12 recuperados") antes de o
    investigador escolher um filtro, e trazer isso junto de cada página seria recontar o
    universo a cada clique de paginação."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            nuvem, aparelhos = _separar_extracoes(cur, id_inquerito, extracoes)
            if not nuvem or not aparelhos:
                return {"so_nuvem": 0, "apagado": 0, "recuperado": 0, "sem_par": True}
            cur.execute(
                f"""
                SELECT CASE
                         WHEN par.id_arquivo IS NOT NULL AND par.cd_status_arquivo = 'Deleted'
                              THEN 'recuperado'
                         WHEN par.id_arquivo IS NOT NULL THEN 'presente'
                         WHEN ({_sql_referencia_em_conversa()}) THEN 'apagado'
                         ELSE 'so_nuvem'
                       END AS cd_estado,
                       count(*) AS qt
                  FROM investigacao.arq_tb01_arquivos a
                  LEFT JOIN LATERAL (
                        SELECT p.id_arquivo, p.cd_status_arquivo
                          FROM investigacao.arq_tb01_arquivos p
                         WHERE p.id_extracao = ANY(%(aparelhos)s)
                           AND lower(p.no_arquivo) = lower(a.no_arquivo)
                         ORDER BY (p.cd_status_arquivo = 'Deleted'), p.id_arquivo
                         LIMIT 1
                  ) par ON true
                 WHERE a.id_extracao = ANY(%(nuvem)s)
                   AND a.no_categoria = ANY(%(categorias_usuario)s)
                   AND a.no_arquivo ~* %(regex_wa)s
                 GROUP BY 1
                """,
                {"nuvem": nuvem, "aparelhos": aparelhos,
                 "categorias_usuario": list(CATEGORIAS_USUARIO),
                 "regex_wa": _SQL_REGEX_WHATSAPP},
            )
            contagem = {linha[0]: linha[1] for linha in cur.fetchall()}
    return {
        "so_nuvem": contagem.get("so_nuvem", 0),
        "apagado": contagem.get("apagado", 0),
        "recuperado": contagem.get("recuperado", 0),
        "presente": contagem.get("presente", 0),
    }

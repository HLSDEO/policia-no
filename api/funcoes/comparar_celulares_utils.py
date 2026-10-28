"""Comparação entre os aparelhos de um MESMO inquérito — o que dois (ou mais) celulares têm em
comum.

O caso que motivou a tela: A não recebe o dinheiro do ilícito e manda pagarem para B. O celular
de A envia o boleto para B, e B devolve o comprovante para A. Nenhum dos dois diz nada
incriminador em texto — o vínculo está no ARQUIVO que passou de um aparelho para o outro. Achar
isso à mão significa abrir duas galerias de centenas de milhares de arquivos e comparar de olho.

Quatro eixos, do mais forte ao mais circunstancial:

  arquivos  — MESMO arquivo (hash MD5 idêntico) nos dois aparelhos. É o eixo principal. O
              Cellebrite grava MD5/SHA-256 de praticamente todo <file> (ver
              investigacao_utils._drenar_arquivos) e o IPED também, então o cruzamento é exato,
              não heurístico. Um PDF/áudio/vídeo enviado por mensageiro chega ao destinatário
              byte a byte igual; imagem reencodada pelo app na hora do envio, não — por isso
              foto batida na hora costuma NÃO casar, e documento casa quase sempre.
  contatos  — mesmo telefone/e-mail na agenda dos dois aparelhos.
  conversas — os dois aparelhos conversaram com o MESMO interlocutor (ou um com o outro, que a
              tela destaca à parte quando o identificador é o dono de um dos aparelhos).
  wifi      — mesmo BSSID (o MAC do roteador): os dois aparelhos estiveram na mesma rede. Sinal
              de lugar, não de pessoa — e ruidoso, porque hotspot de operadora aparece em
              qualquer aparelho que ande pela cidade (mesma ressalva de canonicas_utils).

## Por que o eixo de arquivos precisa de filtro de relevância

Dois Samsung do mesmo modelo compartilham dezenas de milhares de arquivos idênticos: ícone de
app, som de notificação, fonte, cache do Android. Sem filtro, o boleto que interessa fica
soterrado. Daí os três níveis de `relevancia` (ver _FILTRO_RELEVANCIA):

  anexos   — só o que foi anexo de conversa em pelo menos um dos aparelhos. É a prova de que o
             arquivo TRAFEGOU, não que os dois aparelhos por acaso o têm.
  usuario  — padrão: conteúdo de usuário (categoria conhecida, fora das pastas de sistema/cache,
             acima de um tamanho mínimo).
  tudo     — sem filtro nenhum, para quando o investigador quer ver o ruído também.

Em qualquer nível, o grupo que é anexo de conversa vem primeiro na ordenação.

Ver docs/comparar_celulares.md.
"""

import logging

from funcoes.db_utils import get_connection

logger = logging.getLogger(__name__)

LIMITE_MAX = 200

# Tamanho mínimo (bytes) do nível "usuario": abaixo disso é ícone, sprite, miniatura de cache.
TAMANHO_MINIMO_PADRAO = 8 * 1024

# Categorias que são conteúdo de usuário (ufdr_parser.categoria_de_extensao). "Outro" fica de
# fora do nível "usuario" de propósito: é onde caem .so, .dex, .db, .xml e o resto do sistema.
CATEGORIAS_USUARIO = ("Imagem", "Vídeo", "Áudio", "Documento", "Compactado")

# Pastas cujo conteúdo é do sistema/do app, não do usuário — em minúsculas, para LIKE sobre
# lower(tx_caminho). A lista é conservadora de propósito: filtrar demais esconde prova. O que
# está aqui é o que aparece idêntico em QUALQUER aparelho da mesma marca/versão.
_CAMINHOS_SISTEMA = (
    "%/system/%",            # Android: /system/**, e o /System/Library do iOS
    "%/apex/%",              # Android 10+: módulos do sistema
    "%/vendor/%",            # partição do fabricante
    "%/product/%",           # apps e recursos pré-instalados
    "%/data/app/%",          # APK instalado
    "%/dalvik-cache/%",
    "%/cache/%",             # cache de app (Android) e /Library/Caches (iOS)
    "%/.thumbnails/%",       # miniaturas geradas pela galeria
    "%/thumbnails/%",
    "%/containers/bundle/%",  # iOS: binário e recursos do app instalado
    "%/applications/%",      # iOS: apps do sistema
)


def _extracoes_do_inquerito(cur, id_inquerito: int, extracoes: list[int] | None) -> list[int]:
    """Ids das extrações concluídas do inquérito, restritas às escolhidas na tela (quando houver).

    Passa pelo banco em vez de confiar na lista que veio da URL: é o que garante que ninguém
    compare o aparelho de um inquérito com o de outro mandando ?extracoes= à mão."""
    cur.execute(
        """SELECT id_extracao FROM investigacao.inq_tb02_extracoes
            WHERE id_inquerito = %(id_inquerito)s AND cd_status = 'concluido'
            ORDER BY id_extracao""",
        {"id_inquerito": id_inquerito},
    )
    todas = [linha[0] for linha in cur.fetchall()]
    if not extracoes:
        return todas
    escolhidas = set(extracoes)
    return [e for e in todas if e in escolhidas]


def listar_aparelhos(id_inquerito: int) -> list[dict]:
    """Os aparelhos comparáveis do inquérito, com o que a tela precisa para montar os chips e
    avisar quando a comparação não vai funcionar.

    Tudo em uma consulta só, com as contagens AGREGADAS por extração e juntadas por JOIN. Com
    subcorrelacionados por linha do SELECT a tela abria com 504 num inquérito de quatro celulares
    — cada contagem relia o mundo, e a de hash nem índice por extração tinha (IX263 resolveu
    isso; o JOIN deixa o plano enxuto de qualquer forma).

    `qt_arquivos_com_hash` é o número que importa: extração ingerida antes do parser passar a
    ler MD5/SHA-256 do <file> tem os arquivos no banco mas sem hash nenhum, e o eixo de arquivos
    fica vazio para ela sem que isso seja um erro — a tela mostra o aviso em vez de um "nenhum
    resultado" enganoso. Reprocessar a extração preenche."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH extras AS (
                    SELECT id_extracao
                      FROM investigacao.inq_tb02_extracoes
                     WHERE id_inquerito = %(id_inquerito)s AND cd_status = 'concluido'
                       AND NOT coalesce(in_nuvem, false)
                )
                SELECT e.id_extracao, e.no_arquivo, e.no_dispositivo, e.no_fabricante,
                       e.dt_extracao, coalesce(e.qt_arquivos, 0) AS qt_arquivos,
                       coalesce(h.qt_com_hash, 0) AS qt_arquivos_com_hash,
                       coalesce(d.no_dono, w.no_dono) AS no_dono
                  FROM investigacao.inq_tb02_extracoes e
                  JOIN extras x ON x.id_extracao = e.id_extracao
                  LEFT JOIN (
                        SELECT a.id_extracao, count(*) AS qt_com_hash
                          FROM investigacao.arq_tb01_arquivos a
                          JOIN extras xa ON xa.id_extracao = a.id_extracao
                         WHERE a.co_hash_md5 IS NOT NULL
                         GROUP BY a.id_extracao
                       ) h ON h.id_extracao = e.id_extracao
                  LEFT JOIN (
                        SELECT dn.id_extracao,
                               string_agg(DISTINCT dn.no_dono, ', ') AS no_dono
                          FROM investigacao.don_tb01_donos dn
                          JOIN extras xd ON xd.id_extracao = dn.id_extracao
                         WHERE dn.no_dono IS NOT NULL
                         GROUP BY dn.id_extracao
                       ) d ON d.id_extracao = e.id_extracao
                  LEFT JOIN (
                        SELECT ct.id_extracao,
                               string_agg(DISTINCT ct.no_contato, ', ') AS no_dono
                          FROM investigacao.wpp_tb01_contatos ct
                          JOIN extras xc ON xc.id_extracao = ct.id_extracao
                         WHERE ct.in_dono_dispositivo AND ct.no_contato IS NOT NULL
                         GROUP BY ct.id_extracao
                       ) w ON w.id_extracao = e.id_extracao
                 ORDER BY e.id_extracao
                """,
                {"id_inquerito": id_inquerito},
            )
            colunas = [d.name for d in cur.description]
            return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


# As três famílias de conversa do UFDR têm o mesmo desenho de tabelas e diferem só no nome
# delas, no identificador do contato e em ter (ou não) app. Descrever isso uma vez evita a
# mesma consulta escrita três vezes em cada eixo.
_FONTES_CHAT = (
    {
        "fonte": "wpp", "prefixo": "wpp", "identificador": "nu_whatsapp_id",
        "app": "NULL::text",
    },
    {
        "fonte": "sms", "prefixo": "sms", "identificador": "nu_telefone",
        "app": "NULL::text",
    },
    {
        "fonte": "msg", "prefixo": "msg", "identificador": "nu_identificador",
        "app": "c.tp_app",
    },
)


def _sql_anexos_por_extracao() -> str:
    """(id_extracao, co_caminho_zip) de todo anexo de conversa das extrações comparadas.

    UNION (e não UNION ALL) porque o mesmo binário costuma estar em várias mensagens — e aqui
    só interessa "este arquivo foi anexo alguma vez", não quantas."""
    return "\nUNION\n".join(
        f"""
        SELECT c.id_extracao, x.co_caminho_zip
          FROM investigacao.{f['prefixo']}_tb05_anexos x
          JOIN investigacao.{f['prefixo']}_tb04_mensagens m ON m.id_mensagem = x.id_mensagem
          JOIN investigacao.{f['prefixo']}_tb02_conversas c ON c.id_conversa = m.id_conversa
         WHERE c.id_extracao = ANY(%(extracoes)s) AND x.co_caminho_zip IS NOT NULL
        """
        for f in _FONTES_CHAT
    )


def _sql_chat_do_arquivo() -> str:
    """Contexto de conversa de UM arquivo (correlacionado com o alias `a` de fora): em que
    conversa ele apareceu, quando, em que sentido e por quem.

    Parente rico do investigacao_utils._SQL_CHAT_DO_ARQUIVO — que devolve só a mensagem para o
    deep-link. Aqui é o miolo da tela: "boleto.pdf saiu do aparelho de A para 'João' em
    12/03 14:02 e entrou no aparelho de B na mesma conversa" é a frase que o investigador
    precisa ler sem abrir as duas conversas.

    O nome da conversa individual é o do OUTRO lado (participante que não é o dono do
    aparelho); em grupo, o nome do grupo."""
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
               rem.no_contato AS no_remetente, rem.{f['identificador']} AS nu_remetente
          FROM investigacao.{p}_tb05_anexos x
          JOIN investigacao.{p}_tb04_mensagens m ON m.id_mensagem = x.id_mensagem
          JOIN investigacao.{p}_tb02_conversas c ON c.id_conversa = m.id_conversa
          LEFT JOIN investigacao.{p}_tb01_contatos rem ON rem.id_contato = m.id_contato_remetente
         WHERE x.co_caminho_zip = a.co_caminho_zip AND c.id_extracao = a.id_extracao
        """)
    return (
        "SELECT u.* FROM (" + "\nUNION ALL\n".join(partes) + ") u "
        # a mais antiga é a que conta a origem do arquivo naquele aparelho; reenvio posterior
        # é consequência, não o evento que interessa
        "ORDER BY u.dt_mensagem NULLS LAST LIMIT 1"
    )


# Como cada nível de relevância decide se UMA ocorrência conta. O grupo entra na tela quando
# pelo menos uma ocorrência passa (bool_or lá no HAVING) — nunca se perde o arquivo por causa
# da cópia que ficou no cache de um dos aparelhos.
_FILTRO_RELEVANCIA = {
    "tudo": "true",
    "anexos": "an.co_caminho_zip IS NOT NULL",
    "usuario": (
        "(an.co_caminho_zip IS NOT NULL"
        " OR (a.no_categoria = ANY(%(categorias_usuario)s)"
        "     AND coalesce(a.qt_bytes, 0) >= %(tamanho_min)s"
        "     AND coalesce(lower(a.tx_caminho), '') NOT LIKE ALL (%(caminhos_sistema)s)))"
    ),
}

_ORDENACOES = {
    # anexo primeiro, depois o que está em mais aparelhos, depois o maior — o arquivo grande
    # que passou por conversa é o que costuma ser a peça (vídeo, PDF, áudio)
    "relevancia": "in_anexo DESC, qt_aparelhos DESC, qt_bytes DESC NULLS LAST",
    "tamanho": "qt_bytes DESC NULLS LAST",
    "aparelhos": "qt_aparelhos DESC, qt_bytes DESC NULLS LAST",
    "nome": "lower(no_arquivo)",
    "data": "dt_mais_antiga NULLS LAST",
}


def comparar_arquivos(
    id_inquerito: int,
    extracoes: list[int] | None = None,
    relevancia: str = "usuario",
    categoria: str | None = None,
    busca: str | None = None,
    tamanho_min: int | None = None,
    ordenar: str = "relevancia",
    pagina: int = 1,
    limite: int = 25,
) -> dict:
    """Arquivos com o MESMO hash MD5 em dois ou mais dos aparelhos comparados.

    Devolve {total, pagina, limite, itens}, um item por hash:
    {co_hash_md5, no_arquivo, no_categoria, tp_mime, qt_bytes, qt_aparelhos, in_anexo,
     ocorrencias: [{id_extracao, id_arquivo, tx_caminho, disponivel, chat…}]}.

    A ocorrência traz o contexto de conversa quando o arquivo é anexo naquele aparelho — é o
    que mostra o sentido do trânsito (in_enviada) e com quem."""
    pagina = max(1, pagina)
    limite = max(1, min(LIMITE_MAX, limite))
    relevancia = relevancia if relevancia in _FILTRO_RELEVANCIA else "usuario"
    order_by = _ORDENACOES.get(ordenar or "", _ORDENACOES["relevancia"])

    with get_connection() as conn:
        with conn.cursor() as cur:
            ids = _extracoes_do_inquerito(cur, id_inquerito, extracoes)
            if len(ids) < 2:
                return {"total": 0, "pagina": pagina, "limite": limite, "itens": []}

            params = {
                "extracoes": ids,
                "categorias_usuario": list(CATEGORIAS_USUARIO),
                "caminhos_sistema": list(_CAMINHOS_SISTEMA),
                "tamanho_min": TAMANHO_MINIMO_PADRAO if tamanho_min is None else max(0, tamanho_min),
                "categoria": categoria or None,
                "busca": f"%{busca.strip()}%" if (busca or "").strip() else None,
                "limite": limite,
                "offset": (pagina - 1) * limite,
            }

            cur.execute(
                f"""
                WITH candidatos AS (
                    -- passe barato: só (id_extracao, co_hash_md5), que o índice parcial cobre
                    -- por inteiro. Sem ele, o group by varreria a tabela larga de arquivos —
                    -- centenas de milhares de linhas por iPhone.
                    SELECT co_hash_md5
                      FROM investigacao.arq_tb01_arquivos
                     WHERE id_extracao = ANY(%(extracoes)s) AND co_hash_md5 IS NOT NULL
                     GROUP BY co_hash_md5
                    HAVING count(DISTINCT id_extracao) >= 2
                ),
                anexos AS ({_sql_anexos_por_extracao()}),
                itens AS (
                    SELECT a.id_extracao, a.co_hash_md5, a.no_arquivo, a.no_categoria,
                           a.tp_mime, a.qt_bytes, a.dt_modificacao,
                           (an.co_caminho_zip IS NOT NULL) AS in_anexo,
                           ({_FILTRO_RELEVANCIA[relevancia]}) AS in_relevante,
                           (%(categoria)s::text IS NULL OR a.no_categoria = %(categoria)s) AS in_categoria,
                           (%(busca)s::text IS NULL
                            OR a.no_arquivo ILIKE %(busca)s OR a.tx_caminho ILIKE %(busca)s) AS in_busca
                      FROM investigacao.arq_tb01_arquivos a
                      JOIN candidatos ca ON ca.co_hash_md5 = a.co_hash_md5
                      LEFT JOIN anexos an ON an.id_extracao = a.id_extracao
                                         AND an.co_caminho_zip = a.co_caminho_zip
                     WHERE a.id_extracao = ANY(%(extracoes)s)
                ),
                grupos AS (
                    SELECT co_hash_md5,
                           -- o nome mais curto entre os que interessam: o mesmo binário costuma
                           -- ter o nome original num aparelho e o do cache do app no outro
                           (array_agg(no_arquivo
                                      ORDER BY in_anexo DESC,
                                               length(coalesce(no_arquivo, ''))))[1] AS no_arquivo,
                           min(no_categoria) AS no_categoria,
                           min(tp_mime) AS tp_mime,
                           max(qt_bytes) AS qt_bytes,
                           min(dt_modificacao) AS dt_mais_antiga,
                           count(DISTINCT id_extracao) AS qt_aparelhos,
                           count(*) AS qt_ocorrencias,
                           bool_or(in_anexo) AS in_anexo
                      FROM itens
                     GROUP BY co_hash_md5
                    HAVING count(DISTINCT id_extracao) >= 2
                       AND bool_or(in_relevante) AND bool_or(in_categoria) AND bool_or(in_busca)
                )
                -- a ordenação vive fora do GROUP BY porque ordena por APELIDO agregado
                -- (no_arquivo, qt_aparelhos): dentro dele, 'lower(no_arquivo)' seria lido como
                -- a coluna crua de itens, que não está agrupada
                SELECT g.*, count(*) OVER () AS total
                  FROM grupos g
                 ORDER BY {order_by}
                 LIMIT %(limite)s OFFSET %(offset)s
                """,
                params,
            )
            colunas = [d.name for d in cur.description]
            grupos = [dict(zip(colunas, linha)) for linha in cur.fetchall()]
            total = grupos[0].pop("total") if grupos else 0
            for g in grupos:
                g.pop("total", None)
                g["ocorrencias"] = []
            if not grupos:
                return {"total": 0, "pagina": pagina, "limite": limite, "itens": []}

            # detalhe só da página: o LATERAL do contexto de conversa é caro por linha e aqui
            # roda sobre algumas dezenas de arquivos, não sobre o inquérito inteiro
            por_hash = {g["co_hash_md5"]: g for g in grupos}
            cur.execute(
                f"""
                SELECT a.co_hash_md5, a.id_arquivo, a.id_extracao, a.no_arquivo, a.tx_caminho,
                       a.no_categoria, a.tp_mime, a.qt_bytes, a.dt_modificacao,
                       a.cd_status_arquivo, (a.co_caminho_zip IS NOT NULL) AS disponivel,
                       chat.fonte AS chat_fonte, chat.tp_app AS chat_app,
                       chat.id_mensagem AS chat_id_mensagem, chat.id_conversa AS chat_id_conversa,
                       chat.no_conversa AS chat_no_conversa, chat.dt_mensagem AS chat_dt_mensagem,
                       chat.in_enviada AS chat_in_enviada, chat.no_remetente AS chat_no_remetente,
                       chat.nu_remetente AS chat_nu_remetente
                  FROM investigacao.arq_tb01_arquivos a
                  LEFT JOIN LATERAL ({_sql_chat_do_arquivo()}) chat ON true
                 WHERE a.id_extracao = ANY(%(extracoes)s) AND a.co_hash_md5 = ANY(%(hashes)s)
                 ORDER BY a.co_hash_md5, a.id_extracao, chat.dt_mensagem NULLS LAST, a.id_arquivo
                """,
                {"extracoes": ids, "hashes": list(por_hash.keys())},
            )
            colunas = [d.name for d in cur.description]
            for linha in cur.fetchall():
                ocorrencia = dict(zip(colunas, linha))
                grupo = por_hash.get(ocorrencia.pop("co_hash_md5"))
                if grupo is not None:
                    grupo["ocorrencias"].append(ocorrencia)

    return {"total": total, "pagina": pagina, "limite": limite, "itens": grupos}


def _paginar(itens: list[dict], pagina: int, limite: int) -> dict:
    """Empacota o resultado no formato que as telas de listagem já consomem.

    O total vem de `count(*) OVER ()` embutido em cada linha — uma consulta só em vez de repetir
    o agrupamento inteiro num SELECT count(*) à parte."""
    total = itens[0].get("total", 0) if itens else 0
    for item in itens:
        item.pop("total", None)
    return {"total": total, "pagina": pagina, "limite": limite, "itens": itens}


# Chave de cruzamento de um identificador de pessoa. Telefone vira os 11 últimos dígitos, para
# '+55 51 99677-2307', '5551996772307' e '51996772307' caírem na mesma chave (mesma régua de
# identificacao_pessoas._telefone); o resto entra como texto minúsculo. O teste do '@' vem antes
# do de dígitos porque e-mail com número no nome tem dígito suficiente para passar por telefone.
def _sql_chave(coluna: str) -> str:
    digitos = f"regexp_replace({coluna}, '[^0-9]', '', 'g')"
    return (
        f"CASE WHEN position('@' in {coluna}) = 0"
        f"       AND length({digitos}) BETWEEN 10 AND 13"
        f"     THEN 'tel:' || right({digitos}, 11)"
        f"     ELSE 'id:' || lower(btrim({coluna})) END"
    )


def comparar_contatos(
    id_inquerito: int,
    extracoes: list[int] | None = None,
    busca: str | None = None,
    pagina: int = 1,
    limite: int = 50,
) -> dict:
    """Telefones/e-mails que estão na agenda (CTT_TB01/CTT_TB02) de dois ou mais dos aparelhos
    comparados. Um item por identificador, com o nome que cada aparelho deu ao contato — nome
    diferente para o mesmo número nos dois aparelhos costuma ser informação em si ("Contador" em
    um, "Beto Boleto" no outro)."""
    pagina = max(1, pagina)
    limite = max(1, min(LIMITE_MAX, limite))
    with get_connection() as conn:
        with conn.cursor() as cur:
            ids = _extracoes_do_inquerito(cur, id_inquerito, extracoes)
            if len(ids) < 2:
                return {"total": 0, "pagina": pagina, "limite": limite, "itens": []}
            cur.execute(
                f"""
                WITH entradas AS (
                    SELECT c.id_extracao, c.no_contato, e.tp_entrada, btrim(e.tx_valor) AS tx_valor,
                           {_sql_chave('e.tx_valor')} AS co_valor
                      FROM investigacao.ctt_tb02_contato_entradas e
                      JOIN investigacao.ctt_tb01_contatos c ON c.id_contato = e.id_contato
                     WHERE c.id_extracao = ANY(%(extracoes)s)
                       AND coalesce(btrim(e.tx_valor), '') <> ''
                ),
                por_aparelho AS (
                    SELECT co_valor, id_extracao, min(tx_valor) AS tx_valor,
                           min(tp_entrada) AS tp_entrada,
                           string_agg(DISTINCT no_contato, ', ') AS nomes
                      FROM entradas
                     WHERE (%(busca)s::text IS NULL
                            OR tx_valor ILIKE %(busca)s OR no_contato ILIKE %(busca)s)
                     GROUP BY co_valor, id_extracao
                ),
                grupos AS (
                    SELECT co_valor, min(tx_valor) AS tx_exibicao, min(tp_entrada) AS tp_entrada,
                           count(*) AS qt_aparelhos,
                           json_agg(json_build_object('id_extracao', id_extracao,
                                                      'tx_valor', tx_valor,
                                                      'nomes', nomes)
                                    ORDER BY id_extracao) AS aparelhos
                      FROM por_aparelho
                     GROUP BY co_valor
                    HAVING count(*) >= 2
                )
                SELECT g.*, count(*) OVER () AS total
                  FROM grupos g
                 ORDER BY qt_aparelhos DESC, lower(tx_exibicao)
                 LIMIT %(limite)s OFFSET %(offset)s
                """,
                {
                    "extracoes": ids,
                    "busca": f"%{busca.strip()}%" if (busca or "").strip() else None,
                    "limite": limite,
                    "offset": (pagina - 1) * limite,
                },
            )
            colunas = [d.name for d in cur.description]
            itens = [dict(zip(colunas, linha)) for linha in cur.fetchall()]
    return _paginar(itens, pagina, limite)


def comparar_conversas(
    id_inquerito: int,
    extracoes: list[int] | None = None,
    busca: str | None = None,
    pagina: int = 1,
    limite: int = 50,
) -> dict:
    """Interlocutores que aparecem nas conversas de dois ou mais dos aparelhos comparados.

    Dois achados diferentes moram na mesma lista, e a tela separa pelo campo `in_direto`:

    - o identificador é DONO de um dos aparelhos comparados → os dois aparelhos conversaram
      ENTRE SI (é o dono de A aparecendo na conversa de B);
    - o identificador não é dono de ninguém → os dois aparelhos falaram com a MESMA terceira
      pessoa, mesmo sem nunca terem se falado.

    Cruza WhatsApp, SMS e mensageiros pela mesma chave: quem foi contatado por WhatsApp num
    aparelho e por SMS no outro é a mesma pessoa e precisa aparecer uma vez só."""
    pagina = max(1, pagina)
    limite = max(1, min(LIMITE_MAX, limite))
    partes = []
    for f in _FONTES_CHAT:
        p = f["prefixo"]
        # JID de grupo (@g.us) e o oculto (@lid) não são o número de ninguém — viram NULL e caem
        # fora no WHERE, senão "o grupo tal" entraria na lista como interlocutor em comum
        if f["fonte"] == "wpp":
            bruto = ("CASE WHEN ct.nu_whatsapp_id LIKE '%%@s.whatsapp.net'"
                     "     THEN split_part(ct.nu_whatsapp_id, '@', 1)"
                     "     WHEN position('@' in ct.nu_whatsapp_id) = 0 THEN ct.nu_whatsapp_id END")
        else:
            bruto = "ct." + f["identificador"]
        partes.append(f"""
        SELECT c.id_extracao, '{f["fonte"]}'::text AS fonte, {f["app"]} AS tp_app,
               ({bruto}) AS bruto, ct.no_contato,
               coalesce(ct.in_dono_dispositivo, false) AS in_dono,
               c.id_conversa, c.dt_ultima_atividade
          FROM investigacao.{p}_tb03_participantes pa
          JOIN investigacao.{p}_tb01_contatos ct ON ct.id_contato = pa.id_contato
          JOIN investigacao.{p}_tb02_conversas c ON c.id_conversa = pa.id_conversa
         WHERE c.id_extracao = ANY(%(extracoes)s)
        """)
    with get_connection() as conn:
        with conn.cursor() as cur:
            ids = _extracoes_do_inquerito(cur, id_inquerito, extracoes)
            if len(ids) < 2:
                return {"total": 0, "pagina": pagina, "limite": limite, "itens": []}
            cur.execute(
                f"""
                WITH partes AS ({"UNION ALL".join(partes)}),
                normalizadas AS (
                    SELECT id_extracao, fonte, tp_app, no_contato, in_dono, id_conversa,
                           dt_ultima_atividade, bruto,
                           {_sql_chave('bruto')} AS co_valor
                      FROM partes
                     WHERE coalesce(btrim(bruto), '') <> ''
                ),
                por_aparelho AS (
                    SELECT co_valor, id_extracao,
                           min(bruto) AS bruto,
                           string_agg(DISTINCT no_contato, ', ') AS nomes,
                           string_agg(DISTINCT coalesce(tp_app, fonte), ', ') AS apps,
                           bool_or(in_dono) AS in_dono,
                           count(DISTINCT id_conversa) AS qt_conversas,
                           max(dt_ultima_atividade) AS dt_ultima_atividade
                      FROM normalizadas
                     WHERE (%(busca)s::text IS NULL
                            OR bruto ILIKE %(busca)s OR no_contato ILIKE %(busca)s)
                     GROUP BY co_valor, id_extracao
                ),
                grupos AS (
                    SELECT co_valor, min(bruto) AS tx_exibicao,
                           count(*) AS qt_aparelhos,
                           bool_or(in_dono) AS in_direto,
                           max(dt_ultima_atividade) AS dt_ultima_atividade,
                           json_agg(json_build_object('id_extracao', id_extracao,
                                                      'tx_valor', bruto,
                                                      'nomes', nomes,
                                                      'apps', apps,
                                                      'in_dono', in_dono,
                                                      'qt_conversas', qt_conversas)
                                    ORDER BY id_extracao) AS aparelhos
                      FROM por_aparelho
                     GROUP BY co_valor
                    HAVING count(*) >= 2
                )
                SELECT g.*, count(*) OVER () AS total
                  FROM grupos g
                 -- contato direto entre os aparelhos primeiro: é o achado, o resto é contexto
                 ORDER BY in_direto DESC, qt_aparelhos DESC, dt_ultima_atividade DESC NULLS LAST
                 LIMIT %(limite)s OFFSET %(offset)s
                """,
                {
                    "extracoes": ids,
                    "busca": f"%{busca.strip()}%" if (busca or "").strip() else None,
                    "limite": limite,
                    "offset": (pagina - 1) * limite,
                },
            )
            colunas = [d.name for d in cur.description]
            itens = [dict(zip(colunas, linha)) for linha in cur.fetchall()]
    return _paginar(itens, pagina, limite)


def comparar_wifi(
    id_inquerito: int,
    extracoes: list[int] | None = None,
    busca: str | None = None,
    pagina: int = 1,
    limite: int = 50,
) -> dict:
    """Redes Wi-Fi (mesmo BSSID) conhecidas por dois ou mais dos aparelhos comparados.

    BSSID é o MAC do roteador, então rede com o mesmo SSID em endereços diferentes não casa por
    engano. Mas o sinal é de LUGAR, não de vínculo: hotspot de operadora e Wi-Fi de shopping
    aparecem em qualquer aparelho que ande pela cidade — a mesma ressalva que manteve o BSSID
    fora da convergência entre inquéritos (ver canonicas_utils). A tela mostra o SSID e quantas
    vezes cada aparelho conectou justamente para o investigador separar um caso do outro."""
    pagina = max(1, pagina)
    limite = max(1, min(LIMITE_MAX, limite))
    with get_connection() as conn:
        with conn.cursor() as cur:
            ids = _extracoes_do_inquerito(cur, id_inquerito, extracoes)
            if len(ids) < 2:
                return {"total": 0, "pagina": pagina, "limite": limite, "itens": []}
            cur.execute(
                """
                WITH por_aparelho AS (
                    SELECT lower(btrim(no_bssid)) AS co_bssid, id_extracao,
                           string_agg(DISTINCT no_ssid, ', ') AS ssids,
                           sum(coalesce(qt_conexoes, 0)) AS qt_conexoes,
                           max(dt_ultima_conexao) AS dt_ultima_conexao,
                           max(vl_latitude) AS vl_latitude, max(vl_longitude) AS vl_longitude
                      FROM investigacao.wifi_tb01_redes
                     WHERE id_extracao = ANY(%(extracoes)s)
                       AND coalesce(btrim(no_bssid), '') <> ''
                       AND (%(busca)s::text IS NULL
                            OR no_ssid ILIKE %(busca)s OR no_bssid ILIKE %(busca)s)
                     GROUP BY 1, 2
                ),
                grupos AS (
                    SELECT co_bssid,
                           string_agg(DISTINCT ssids, ', ') AS ssids,
                           count(*) AS qt_aparelhos,
                           max(dt_ultima_conexao) AS dt_ultima_conexao,
                           max(vl_latitude) AS vl_latitude, max(vl_longitude) AS vl_longitude,
                           json_agg(json_build_object('id_extracao', id_extracao,
                                                      'ssids', ssids,
                                                      'qt_conexoes', qt_conexoes,
                                                      'dt_ultima_conexao', dt_ultima_conexao)
                                    ORDER BY id_extracao) AS aparelhos
                      FROM por_aparelho
                     GROUP BY co_bssid
                    HAVING count(*) >= 2
                )
                SELECT g.*, count(*) OVER () AS total
                  FROM grupos g
                 ORDER BY qt_aparelhos DESC, dt_ultima_conexao DESC NULLS LAST
                 LIMIT %(limite)s OFFSET %(offset)s
                """,
                {
                    "extracoes": ids,
                    "busca": f"%{busca.strip()}%" if (busca or "").strip() else None,
                    "limite": limite,
                    "offset": (pagina - 1) * limite,
                },
            )
            colunas = [d.name for d in cur.description]
            itens = [dict(zip(colunas, linha)) for linha in cur.fetchall()]
    return _paginar(itens, pagina, limite)

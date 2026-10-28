"""Consulta dos domínios do UFDR que passaram a ser importados na auditoria de
aproveitamento (ver db/init/17_aparelho_redes.sql e ufdr_parser).

Todos têm a mesma forma — uma tabela filha da extração, sem tabela-neta e sem chave natural —
então em vez de 22 funções de listagem quase idênticas há um registro por domínio com a tabela,
as colunas, a ordenação e onde a busca livre procura. As telas consomem tudo pela mesma rota
(`/api/investigacao/inqueritos/{id}/ufdr/{dominio}`), o que também evita 22 rotas iguais.
"""

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from funcoes.db_utils import get_connection
from funcoes.investigacao_utils import (
    LIMITE_MAX_LISTAGEM,
    obter_id_inquerito_de_extracao,
    resolver_anexos_por_nome,
)
# As faixas do dia (madrugada/manhã/tarde/noite) e a conversão para hora de Brasília são as
# mesmas da tela de Arquivos e das de Ligações/Localizações — o investigador aprende o filtro
# uma vez e ele quer dizer a mesma coisa em toda listagem.
from funcoes.investigacao_utils import _PERIODOS_ARQUIVO as FAIXAS_DIA, _hora_local_sql


def _sql_host(coluna: str) -> str:
    """Host de uma coluna de endereço — sem protocolo, caminho, query ou 'www.'.

    É o "facebook.com" que agrupa as publicações por site, do mesmo jeito que a tela de
    Navegação agrupa o histórico (ver _SQL_DOMINIO_NAV em investigacao_utils)."""
    return ("regexp_replace(regexp_replace(regexp_replace(lower(coalesce(" + coluna + ", '')), "
            "'^[a-zA-Z][a-zA-Z0-9+.-]*://', ''), '[/?#].*$', ''), '^www\\.', '')")


class _Dominio:
    """Um domínio consultável: tabela, colunas devolvidas, ordenação e campos da busca livre.

    filtro: condição SQL fixa, aplicada sempre. Serve para domínio em que parte das linhas não
    tem uso na tela — o trajeto sem nenhuma coordenada, por exemplo, não diz de onde para onde.
    pacote: coluna do pacote do aplicativo, quando o domínio tem uma; é o que permite abrir a
    lista já filtrada por um app (ver o modal de uso na tela de Aplicativos).
    anexos: coluna com os nomes dos anexos (lista separada por vírgula); declarada, cada item
    ganha `anexos` já resolvido para os arquivos do aparelho, e a tela abre o binário.

    Os demais são opcionais e ligam filtros extras na tela (ver ufdr_dominio.js); domínio que não
    os declara continua só com busca livre e "só apagados":
    data: coluna de data/hora usada no filtro por período (de/até);
    hora: além do período, oferece a faixa do dia (madrugada/manhã/tarde/noite) sobre `data`;
    facetas: colunas de valor repetido oferecidas como select — os valores presentes vêm de
    facetas_dominio(), com a contagem de cada um;
    derivados: {nome_da_faceta: expressão SQL} para faceta que não é coluna (o site do endereço);
    marcadores: {chave: condição SQL} de recorte liga/desliga (só o que tem anexo, só o que tem
    coordenada) — a tela mostra cada um com a contagem que facetas_dominio() devolve;
    valor: coluna numérica com filtro de faixa (mínimo/máximo).
    fonte: em vez da tabela crua, uma consulta (sem o alias) que já vem pronta para ser o
    `FROM ... d` da listagem e das facetas. É o que permite a um domínio ENTREGAR LINHAS
    MESCLADAS — o iPhone grava o mesmo dispositivo em registros separados por aplicativo
    (KnowledgeC com o MAC, Find My com o modelo), e a tela mostraria o mesmo aparelho em
    linhas incompletas. O SQL da fonte precisa expor as colunas declaradas em `colunas`.
    ordenacoes: {chave: ORDER BY} oferecido ao cabeçalho clicável da tela — o `?ordenar=` vindo
    da query string só vale se a chave estiver aqui (o texto cru da URL nunca entra no SQL);
    chave fora da lista (ou ausente) cai na `ordem` padrão do domínio."""

    def __init__(self, tabela: str, colunas: str, ordem: str, busca: tuple[str, ...] = (),
                 rotulo: str = "", filtro: str | None = None, pacote: str | None = None,
                 data: str | None = None, facetas: tuple[str, ...] = (), valor: str | None = None,
                 hora: bool = False, derivados: dict[str, str] | None = None,
                 marcadores: dict[str, str] | None = None, anexos: str | None = None,
                 fonte: str | None = None, ordenacoes: dict[str, str] | None = None):
        self.tabela = tabela
        self.colunas = colunas
        self.ordem = ordem
        self.busca = busca
        self.rotulo = rotulo
        self.filtro = filtro
        self.pacote = pacote
        self.data = data
        self.facetas = facetas
        self.valor = valor
        self.hora = hora
        self.derivados = derivados or {}
        self.marcadores = marcadores or {}
        self.anexos = anexos
        self.fonte = fonte
        self.ordenacoes = ordenacoes or {}

    def de(self) -> str:
        """O `FROM` do domínio: a tabela crua ou a fonte mesclada, sempre pronta para o alias d."""
        return f"({self.fonte})" if self.fonte else f"investigacao.{self.tabela}"

    def expressao(self, faceta: str) -> str:
        """SQL da faceta: a própria coluna, ou a expressão declarada em `derivados`."""
        return self.derivados.get(faceta) or f"d.{faceta}"


DOMINIOS: dict[str, _Dominio] = {
    # --- tela "Atividade do aparelho" ---
    "uso-apps": _Dominio(
        "uso_tb01_uso_apps",
        "id_uso_app, no_aplicativo, nu_pacote, tp_acao, dt_inicio, dt_fim, no_submodulo, "
        "no_servico, cd_status_ufdr",
        "dt_inicio DESC NULLS LAST, id_uso_app",
        ("nu_pacote", "no_aplicativo", "tp_acao", "no_submodulo"),
        "uso de aplicativo",
        pacote="nu_pacote",
    ),
    # --- tela "Dispositivos reconhecidos" ---
    # O iPhone grava o MESMO aparelho em registros separados por aplicativo (KnowledgeC com o
    # MAC, Biome com o MAC, Find My com o modelo, SearchParty sem nada) e a tela mostrava o
    # mesmo AirPod em três linhas com informações soltas. A fonte mescla por MAC; registro sem
    # MAC entra no grupo do MAC quando o nome+tipo bate com um que tem (é o Find My sem MAC do
    # mesmo fone); sem nenhuma das duas chaves, fica sozinho. macs-placeholder (tudo zero) não
    # identificam nada e são tratados como sem MAC. qt_registros conta quantos registros a
    # linha representa — a tela mostra o selo "N unidos".
    "dispositivos-reconhecidos": _Dominio(
        "rcd_tb01_dispositivos",
        "id_dispositivo, no_aplicativo, no_dispositivo, tp_dispositivo, no_modelo, nu_serie, "
        "no_mac, tx_identificadores, cd_status_ufdr, qt_registros",
        "no_dispositivo NULLS LAST, id_dispositivo",
        ("no_dispositivo", "no_mac", "no_modelo", "tp_dispositivo"),
        "dispositivo reconhecido",
        # filtros sobre a fonte mesclada (alias d): tipo do aparelho e o que a linha tem de
        # identificável — "unidos" isola justamente os grupos que a mesclagem formou
        facetas=("tp_dispositivo",),
        marcadores={
            "com_mac": "d.no_mac IS NOT NULL",
            "com_serie": "nullif(btrim(coalesce(d.nu_serie, '')), '') IS NOT NULL",
            "unidos": "d.qt_registros > 1",
        },
        fonte="""
            SELECT
              min(b.id_dispositivo) AS id_dispositivo,
              b.id_extracao,
              string_agg(DISTINCT nullif(btrim(coalesce(b.no_aplicativo, '')), ''), ', ')
                  AS no_aplicativo,
              (array_agg(b.no_dispositivo ORDER BY b.id_dispositivo) FILTER (
                  WHERE nullif(btrim(coalesce(b.no_dispositivo, '')), '') IS NOT NULL))[1]
                  AS no_dispositivo,
              (array_agg(b.tp_dispositivo ORDER BY b.id_dispositivo) FILTER (
                  WHERE nullif(btrim(coalesce(b.tp_dispositivo, '')), '') IS NOT NULL))[1]
                  AS tp_dispositivo,
              (array_agg(b.no_modelo ORDER BY length(coalesce(b.no_modelo, '')) DESC,
                         b.id_dispositivo))[1] AS no_modelo,
              (array_agg(b.nu_serie ORDER BY length(coalesce(b.nu_serie, '')) DESC,
                         b.id_dispositivo))[1] AS nu_serie,
              (array_agg(b.no_mac ORDER BY b.id_dispositivo) FILTER (
                  WHERE nullif(btrim(coalesce(b.no_mac, '')), '') IS NOT NULL))[1] AS no_mac,
              (array_agg(b.tx_identificadores ORDER BY
                         length(coalesce(b.tx_identificadores, '')) DESC, b.id_dispositivo))[1]
                  AS tx_identificadores,
              -- status: um excluído na lista pesa mais que os íntegros (a marca não some na mescla)
              (array_agg(b.cd_status_ufdr ORDER BY
                         (coalesce(b.cd_status_ufdr, '') = 'Intact')::int, b.id_dispositivo))[1]
                  AS cd_status_ufdr,
              count(*) AS qt_registros
            FROM (
              SELECT r.*,
                CASE WHEN nullif(btrim(coalesce(r.no_mac, '')), '') IS NOT NULL
                          AND upper(btrim(r.no_mac)) NOT IN ('00:00:00:00:00:00',
                                                             '00:00:00:00:00:01')
                     THEN 'm:' || upper(btrim(r.no_mac)) END AS _chave_mac,
                CASE WHEN nullif(btrim(coalesce(r.no_dispositivo, '')), '') IS NOT NULL
                     THEN 'n:' || lower(btrim(r.no_dispositivo)) || '#'
                          || lower(btrim(coalesce(r.tp_dispositivo, ''))) END AS _chave_nome
              FROM investigacao.rcd_tb01_dispositivos r
            ) b
            LEFT JOIN (
              SELECT CASE WHEN nullif(btrim(coalesce(no_dispositivo, '')), '') IS NOT NULL
                          THEN 'n:' || lower(btrim(no_dispositivo)) || '#'
                               || lower(btrim(coalesce(tp_dispositivo, ''))) END AS _chave_nome,
                     min('m:' || upper(btrim(no_mac))) AS _chave_mac
              FROM investigacao.rcd_tb01_dispositivos
              WHERE nullif(btrim(coalesce(no_mac, '')), '') IS NOT NULL
                AND upper(btrim(no_mac)) NOT IN ('00:00:00:00:00:00', '00:00:00:00:00:01')
              GROUP BY 1
            ) nm ON nm._chave_nome IS NOT NULL AND nm._chave_nome = b._chave_nome
            GROUP BY b.id_extracao,
                     COALESCE(b._chave_mac, nm._chave_mac, b._chave_nome,
                              'i:' || b.id_dispositivo::text)
        """,
    ),
    # --- aba da tela de Wi-Fi ---
    "conexoes": _Dominio(
        "cnx_tb01_conexoes",
        "id_conexao, no_aplicativo, tp_metodo, tp_natureza, tp_dispositivo, no_dispositivo, "
        "no_mac, dt_conexao, cd_status_ufdr",
        "dt_conexao DESC NULLS LAST, id_conexao",
        ("no_dispositivo", "no_mac", "tp_metodo"),
        "conexão",
        filtro="(d.no_dispositivo IS NOT NULL OR d.no_mac IS NOT NULL)",
    ),
    # --- abas da tela de Navegação ---
    "cookies": _Dominio(
        "coo_tb01_cookies",
        "id_cookie, no_aplicativo, no_cookie, no_dominio, tx_caminho, tx_valor, "
        "no_app_relacionado, dt_criacao, dt_ultimo_acesso, dt_expiracao, cd_status_ufdr",
        "dt_ultimo_acesso DESC NULLS LAST, id_cookie",
        ("no_dominio", "no_cookie", "no_app_relacionado"),
        "cookie",
        # a tabela de cookies é quase sempre a maior da extração (dezenas de milhares de
        # linhas) — sem recorte por período/domínio a tela só permite pescar por texto.
        # "expirado" compara com now() em UTC: o UFDR grava o timestamp ingênuo em UTC
        data="dt_ultimo_acesso",
        hora=True,
        facetas=("no_dominio", "no_aplicativo", "no_app_relacionado"),
        marcadores={
            "expirados": "d.dt_expiracao IS NOT NULL AND "
                         "d.dt_expiracao < (now() AT TIME ZONE 'UTC')",
            "expira_breve": "d.dt_expiracao IS NOT NULL AND "
                            "d.dt_expiracao >= (now() AT TIME ZONE 'UTC') AND "
                            "d.dt_expiracao < (now() AT TIME ZONE 'UTC') + interval '30 days'",
            "sessao": "d.dt_expiracao IS NULL",
        },
        ordenacoes={
            "acesso_desc": "dt_ultimo_acesso DESC NULLS LAST, id_cookie",
            "acesso_asc": "dt_ultimo_acesso ASC NULLS LAST, id_cookie",
            "criacao_desc": "dt_criacao DESC NULLS LAST, id_cookie",
            "criacao_asc": "dt_criacao ASC NULLS LAST, id_cookie",
            "expiracao_desc": "dt_expiracao DESC NULLS LAST, id_cookie",
            "expiracao_asc": "dt_expiracao ASC NULLS LAST, id_cookie",
            "dominio": "no_dominio ASC NULLS LAST, id_cookie",
            "nome": "no_cookie ASC NULLS LAST, id_cookie",
            "app": "no_app_relacionado ASC NULLS LAST, id_cookie",
        },
    ),
    "favoritos": _Dominio(
        "fav_tb01_favoritos",
        "id_favorito, no_aplicativo, tx_titulo, tx_url, tx_pasta, qt_visitas, "
        "dt_ultima_visita, cd_status_ufdr",
        "qt_visitas DESC NULLS LAST, id_favorito",
        ("tx_titulo", "tx_url", "tx_pasta"),
        "favorito",
        data="dt_ultima_visita",
        hora=True,
        facetas=("no_aplicativo", "tx_pasta"),
        ordenacoes={
            "visitas_desc": "qt_visitas DESC NULLS LAST, id_favorito",
            "visitas_asc": "qt_visitas ASC NULLS LAST, id_favorito",
            "visita_desc": "dt_ultima_visita DESC NULLS LAST, id_favorito",
            "visita_asc": "dt_ultima_visita ASC NULLS LAST, id_favorito",
            "titulo": "tx_titulo ASC NULLS LAST, id_favorito",
        },
    ),
    "downloads": _Dominio(
        "dwn_tb01_downloads",
        "id_download, no_aplicativo, tx_url, tx_destino, qt_bytes, qt_bytes_recebidos, "
        "cd_situacao, dt_inicio, dt_fim, dt_ultimo_acesso, tx_redirecionamentos, cd_status_ufdr",
        "dt_inicio DESC NULLS LAST, id_download",
        ("tx_url", "tx_destino"),
        "download",
        data="dt_inicio",
        hora=True,
        facetas=("no_aplicativo", "cd_situacao"),
        # interrompido/incompleto: recebeu menos do que o tamanho anunciado (ou nada)
        marcadores={
            "incompletos": "d.qt_bytes IS NOT NULL AND d.qt_bytes > 0 AND "
                           "coalesce(d.qt_bytes_recebidos, 0) < d.qt_bytes",
        },
        ordenacoes={
            "inicio_desc": "dt_inicio DESC NULLS LAST, id_download",
            "inicio_asc": "dt_inicio ASC NULLS LAST, id_download",
            "bytes_desc": "qt_bytes DESC NULLS LAST, id_download",
            "bytes_asc": "qt_bytes ASC NULLS LAST, id_download",
            "url": "tx_url ASC NULLS LAST, id_download",
        },
    ),
    # o autofill já era gravado desde antes, mas nunca teve tela; entra aqui junto com os
    # domínios novos porque é onde o FormData (formulário salvo) desemboca
    "autofill": _Dominio(
        "afl_tb01_autofill",
        "id_autofill, no_fonte, no_chave, tx_valor, tp_modelo, dt_registro, dt_ultimo_uso, "
        "cd_status_ufdr",
        "no_chave NULLS LAST, id_autofill",
        ("no_chave", "tx_valor", "no_fonte"),
        "preenchimento automático",
        data="dt_ultimo_uso",
        facetas=("no_fonte",),
        ordenacoes={
            "campo_desc": "no_chave DESC NULLS LAST, id_autofill",
            "uso_desc": "dt_ultimo_uso DESC NULLS LAST, id_autofill",
            "uso_asc": "dt_ultimo_uso ASC NULLS LAST, id_autofill",
            "registro_desc": "dt_registro DESC NULLS LAST, id_autofill",
        },
    ),
    # senhas do chaveiro: gravadas desde a extração UFDR, mas nunca teve tela — a Nuvem IPED
    # trouxe 2.512 de um iPhone só e o card contava "N senhas" sem lugar para vê-las
    "senhas": _Dominio(
        "snh_tb01_senhas",
        "id_senha, no_fonte, no_servico, no_usuario, tx_dados, tp_modelo, tp_senha, "
        "no_grupo_acesso, cd_status_ufdr",
        "no_servico NULLS LAST, id_senha",
        ("no_servico", "no_usuario", "tx_dados", "no_grupo_acesso"),
        "senha salva",
    ),
    "uso-rede": _Dominio(
        "rde_tb01_uso_rede",
        "id_uso_rede, no_aplicativo, no_ssid, tp_conexao, tp_uso, in_roaming, "
        "qt_bytes_recebidos, qt_bytes_enviados, dt_inicio, dt_fim, cd_status_ufdr",
        "dt_inicio DESC NULLS LAST, id_uso_rede",
        ("no_ssid", "tp_conexao"),
        "uso de rede",
        data="dt_inicio",
        hora=True,
        facetas=("tp_conexao",),
        # tráfego em roaming é o que liga o consumo da tela a uma viagem
        marcadores={"roaming": "d.in_roaming IS TRUE"},
        ordenacoes={
            "inicio_desc": "dt_inicio DESC NULLS LAST, id_uso_rede",
            "inicio_asc": "dt_inicio ASC NULLS LAST, id_uso_rede",
            "recv_desc": "qt_bytes_recebidos DESC NULLS LAST, id_uso_rede",
            "env_desc": "qt_bytes_enviados DESC NULLS LAST, id_uso_rede",
        },
    ),
    # --- tela "Atividade social e buscas" ---
    "atividade-social": _Dominio(
        "rds_tb01_atividade_social",
        "id_atividade, no_aplicativo, no_plataforma, tp_atividade, tx_titulo, tx_corpo, tx_url, "
        "dt_atividade, no_autor, nu_autor, no_conta, cd_situacao, tp_canal, tx_privacidade, "
        "qt_reacoes, qt_compartilhamentos, qt_comentarios, vl_latitude, vl_longitude, "
        "tx_marcados, tx_anexos, cd_status_ufdr",
        "dt_atividade DESC NULLS LAST, id_atividade",
        ("tx_corpo", "tx_titulo", "no_autor", "tx_url"),
        "atividade em rede social",
        data="dt_atividade",
        hora=True,
        # "dominio" não é coluna: é o host de TX_URL (facebook.com, instagram.com), que é como o
        # investigador pensa a publicação — de que site ela veio, não em que app foi extraída
        facetas=("no_aplicativo", "tp_atividade", "dominio", "no_autor"),
        derivados={"dominio": _sql_host("d.tx_url")},
        marcadores={
            "com_anexo": "d.tx_anexos IS NOT NULL AND btrim(d.tx_anexos) <> ''",
            "com_local": "d.vl_latitude IS NOT NULL AND d.vl_longitude IS NOT NULL",
        },
        anexos="tx_anexos",
    ),
    "buscas": _Dominio(
        "bsc_tb01_buscas",
        "id_busca, no_aplicativo, no_origem, tx_termo, dt_busca, no_conta, tx_resultados, "
        "vl_latitude, vl_longitude, cd_status_ufdr",
        "dt_busca DESC NULLS LAST, id_busca",
        ("tx_termo", "no_aplicativo", "tx_resultados"),
        "busca",
        data="dt_busca",
        hora=True,
        facetas=("no_aplicativo", "no_origem", "no_conta"),
        marcadores={"com_local": "d.vl_latitude IS NOT NULL AND d.vl_longitude IS NOT NULL"},
    ),
    # --- abas da tela de Arquivos ---
    "envios-nuvem": _Dominio(
        "upl_tb01_envios_nuvem",
        "id_envio, no_aplicativo, no_arquivo, tx_titulo, tx_url, tp_arquivo, no_pasta, no_conta, "
        "no_dono, tx_compartilhamento, dt_envio, dt_modificacao, cd_status_ufdr",
        "dt_envio DESC NULLS LAST, id_envio",
        ("no_arquivo", "tx_titulo", "no_pasta", "no_conta"),
        "envio para a nuvem",
    ),
    "gravacoes": _Dominio(
        "grv_tb01_gravacoes",
        "id_gravacao, no_aplicativo, tx_titulo, tp_gravacao, dt_gravacao, qt_duracao_segundos, "
        "cd_status_ufdr",
        "dt_gravacao DESC NULLS LAST, id_gravacao",
        ("tx_titulo", "tp_gravacao"),
        "gravação",
    ),
    # --- tela "Calendário" ---
    "calendario": _Dominio(
        "cal_tb01_calendario",
        "id_compromisso, no_aplicativo, no_categoria, tx_assunto, tx_detalhes, tx_local, "
        "dt_inicio, dt_fim, tx_convidados, no_organizador, tx_repeticao, tx_lembretes, "
        "cd_situacao, tx_prioridade, tx_disponibilidade, no_conta, vl_latitude, vl_longitude, "
        "cd_status_ufdr",
        "dt_inicio DESC NULLS LAST, id_compromisso",
        ("tx_assunto", "tx_detalhes", "tx_local", "tx_convidados"),
        "compromisso",
        # período sobre o início (a tela tem vista de mês, que consulta mês a mês com de/ate) e
        # recortes que são como o investigador pensa a agenda: de qual app/cat/conta vem, se é
        # recorrência, se tem lugar ou convidados
        data="dt_inicio",
        facetas=("no_aplicativo", "no_categoria", "no_conta", "cd_situacao"),
        marcadores={
            # regex (!~*) e não LIKE: o texto do marcador vai cru numa query com params,
            # e o psycopg reclama do "%" do padrão LIKE como se fosse placeholder
            "sem_feriados": "coalesce(d.no_categoria, '') !~* '(feriado|holiday)'",
            "recorrente": "d.tx_repeticao IS NOT NULL AND btrim(d.tx_repeticao) <> ''",
            "com_local": "d.tx_local IS NOT NULL AND btrim(d.tx_local) <> '' OR "
                         "(d.vl_latitude IS NOT NULL AND d.vl_longitude IS NOT NULL)",
            "com_convidados": "d.tx_convidados IS NOT NULL AND btrim(d.tx_convidados) <> ''",
        },
    ),
    # --- aba da tela de Aplicativos ---
    "uso-aplicativo": _Dominio(
        "apu_tb01_uso_aplicativo",
        "id_uso, no_aplicativo, nu_pacote, qt_aberturas, qt_ativacoes, qt_tempo_ativo, "
        "qt_tempo_fundo, qt_ultima_duracao, dt_inicio, dt_fim, dt_ultima_abertura, cd_status_ufdr",
        "qt_tempo_ativo DESC NULLS LAST, id_uso",
        ("nu_pacote", "no_aplicativo"),
        "app com uso registrado",
        pacote="nu_pacote",
    ),
    # --- aba da tela de Localizações ---
    "trajetos": _Dominio(
        "jrn_tb01_trajetos",
        "id_trajeto, no_aplicativo, tp_trajeto, no_trajeto, vl_lat_origem, vl_lon_origem, "
        "tx_endereco_origem, vl_lat_destino, vl_lon_destino, tx_endereco_destino, qt_pontos, "
        "dt_inicio, dt_fim, no_conta, cd_status_ufdr",
        "dt_inicio DESC NULLS LAST, id_trajeto",
        ("tx_endereco_origem", "tx_endereco_destino", "no_trajeto"),
        "trajeto",
        filtro="(d.vl_lat_origem IS NOT NULL OR d.vl_lat_destino IS NOT NULL)",
    ),
    # --- tela "Identidade do aparelho" ---
    "donos": _Dominio(
        "don_tb01_donos",
        "id_dono, no_dono, tp_usuario, nu_identificador, dt_ultimo_login",
        "no_dono NULLS LAST, id_dono",
        ("no_dono", "nu_identificador", "tp_usuario"),
        "dono",
    ),
    "contas-usuario": _Dominio(
        "cta_tb01_contas_usuario",
        "id_conta_usuario, no_fonte, no_servico, no_usuario, no_titular, dt_criacao, cd_status_ufdr",
        "no_servico NULLS LAST, id_conta_usuario",
        ("no_usuario", "no_titular", "no_servico", "no_fonte"),
        "conta de usuário",
    ),
    # O aparelho repete o MESMO campo dezenas de vezes (um iPhone grava ~60 "Randomized Mac
    # Address" e um ICCID por chip), e a tabela crua enterrava IMEI/série/Apple ID no meio do
    # montão. A fonte agrupa por categoria+campo e junta os valores com quebra de linha — a tela
    # mostra "ICCID — 7 valores" e expande quem quiser ver todos. A busca bate no conjunto.
    "info-aparelho": _Dominio(
        "dev_tb01_info_aparelho",
        "id_info, no_aplicativo, no_categoria, no_campo, tx_valor, dt_registro, cd_status_ufdr",
        "no_categoria NULLS LAST, no_campo NULLS LAST, id_info",
        ("no_campo", "tx_valor", "no_categoria"),
        "informação do aparelho",
        fonte="""
            SELECT
              min(b.id_info) AS id_info,
              b.id_extracao,
              b.no_categoria,
              b.no_campo,
              string_agg(DISTINCT b._valor, chr(10) ORDER BY b._valor)
                  FILTER (WHERE b._valor IS NOT NULL) AS tx_valor,
              string_agg(DISTINCT nullif(btrim(coalesce(b.no_aplicativo, '')), ''), ', ')
                  AS no_aplicativo,
              min(b.dt_registro) AS dt_registro,
              -- status: um excluído na lista pesa mais que os íntegros (a marca não some na mescla)
              (array_agg(b.cd_status_ufdr ORDER BY
                         (coalesce(b.cd_status_ufdr, '') = 'Intact')::int, b.id_info))[1]
                  AS cd_status_ufdr
            FROM (
              SELECT i.*, nullif(btrim(coalesce(i.tx_valor, '')), '') AS _valor
              FROM investigacao.dev_tb01_info_aparelho i
            ) b
            GROUP BY b.id_extracao, b.no_categoria, b.no_campo
        """,
    ),
    # A mesma antena (LAC/CID) é regrava a cada troca de célula — a janela conta quantas vezes a
    # célula aparece na extração, e a tela mostra "N×" para o investigador saber onde o aparelho
    # ficou de verdade (antena frequente = lugar conhecido) sem contar linhas na mão.
    "antenas": _Dominio(
        "erb_tb01_antenas",
        "id_antena, tp_rede, nu_mcc, nu_mnc, nu_lac, nu_cid, vl_latitude, vl_longitude, "
        "dt_registro, cd_status_ufdr, qt_ocorrencias",
        "dt_registro DESC NULLS LAST, id_antena",
        ("nu_cid", "nu_lac", "tp_rede"),
        "antena",
        fonte="""
            SELECT a.id_antena, a.id_extracao, a.tp_rede, a.nu_mcc, a.nu_mnc, a.nu_lac, a.nu_cid,
                   a.vl_latitude, a.vl_longitude, a.dt_registro, a.cd_status_ufdr,
                   count(*) OVER (PARTITION BY a.id_extracao, coalesce(a.nu_lac::text, ''),
                                  coalesce(a.nu_cid::text, ''), coalesce(a.tp_rede, ''))
                       AS qt_ocorrencias
            FROM investigacao.erb_tb01_antenas a
        """,
    ),
    # --- aba da tela de Ligações ---
    "voicemail": _Dominio(
        "vms_tb01_voicemail",
        "id_voicemail, no_aplicativo, nu_telefone, no_contato, dt_recebimento, dt_modificacao, "
        "qt_duracao_segundos, in_ouvida, tp_voicemail, tx_transcricao, cd_status_ufdr",
        "dt_recebimento DESC NULLS LAST, id_voicemail",
        ("nu_telefone", "no_contato", "tx_transcricao"),
        "mensagem de voz",
    ),
    # --- abas da tela de Contas bancárias ---
    "transferencias": _Dominio(
        "trf_tb01_transferencias",
        "id_transferencia, no_aplicativo, tp_transferencia, tx_descricao, vl_valor, no_moeda, "
        "cd_situacao, tx_participantes, dt_envio, dt_processamento, cd_status_ufdr",
        "dt_envio DESC NULLS LAST, id_transferencia",
        ("tx_descricao", "tx_participantes", "tp_transferencia"),
        "transferência",
        data="dt_envio",
        facetas=("tp_transferencia", "cd_situacao", "no_moeda", "no_aplicativo"),
        valor="vl_valor",
    ),
    "cartoes-credito": _Dominio(
        "crt_tb01_cartoes_credito",
        "id_cartao, no_aplicativo, no_bandeira, nu_cartao, dt_validade, no_titular, "
        "dt_ultimo_uso, cd_status_ufdr",
        "no_bandeira NULLS LAST, id_cartao",
        ("no_bandeira", "no_titular", "nu_cartao"),
        "cartão de crédito",
        data="dt_ultimo_uso",
        facetas=("no_bandeira", "no_aplicativo"),
    ),
    "cartoes-carteira": _Dominio(
        "mbc_tb01_cartoes_carteira",
        "id_cartao_carteira, no_aplicativo, tx_descricao, no_organizacao, tp_cartao, co_barras, "
        "dt_compra, dt_modificacao, dt_expiracao, vl_latitude, vl_longitude, cd_status_ufdr",
        "dt_compra DESC NULLS LAST, id_cartao_carteira",
        ("tx_descricao", "no_organizacao", "tp_cartao"),
        "cartão da carteira",
        data="dt_compra",
        facetas=("tp_cartao", "no_organizacao", "no_aplicativo"),
    ),
}

# Contador de INQ_TB02_EXTRACOES de cada domínio — o front usa para saber quais abas mostrar
# sem precisar buscar a lista inteira. NULL no contador = extração carregada antes do domínio
# existir, e a tela distingue isso de "carregada e vazia".
CONTADORES = {
    "uso-apps": "qt_uso_apps",
    "dispositivos-reconhecidos": "qt_dispositivos_reconhecidos",
    "conexoes": "qt_conexoes_dispositivo",
    "cookies": "qt_cookies",
    "favoritos": "qt_favoritos",
    "downloads": "qt_downloads",
    "autofill": "qt_autofill",
    "senhas": "qt_senhas",
    "uso-rede": "qt_uso_rede",
    "atividade-social": "qt_atividade_social",
    "buscas": "qt_buscas",
    "envios-nuvem": "qt_envios_nuvem",
    "gravacoes": "qt_gravacoes",
    "calendario": "qt_calendario",
    "uso-aplicativo": "qt_uso_aplicativo",
    "trajetos": "qt_trajetos",
    "donos": "qt_donos",
    # sem contador próprio em INQ_TB02: contado ao vivo (ver contagens_dominios)
    "contas-usuario": None,
    "info-aparelho": "qt_info_aparelho",
    "antenas": "qt_antenas",
    "voicemail": "qt_voicemail",
    "transferencias": "qt_transferencias",
    "cartoes-credito": "qt_cartoes_credito",
    "cartoes-carteira": "qt_cartoes_carteira",
}


def _data(valor) -> date | None:
    """Converte 'AAAA-MM-DD' vindo da tela em date; texto inválido é ignorado (filtro não
    aplicado) em vez de virar erro 500 — o campo de data do navegador pode chegar pela metade."""
    if not valor:
        return None
    if isinstance(valor, date):
        return valor
    try:
        return date.fromisoformat(str(valor)[:10])
    except ValueError:
        return None


def _decimal(valor) -> Decimal | None:
    """Idem para os campos de valor mínimo/máximo. Aceita a forma brasileira (1.234,56), que é
    como o investigador digita quando copia o valor da tela."""
    if valor is None or str(valor).strip() == "":
        return None
    texto = str(valor).strip()
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    try:
        return Decimal(texto)
    except InvalidOperation:
        return None


def listar_dominio(dominio: str, id_inquerito: int, id_extracao: int | None = None,
                   busca: str | None = None, apagados: bool = False,
                   pagina: int = 1, limite: int = 100, pacote: str | None = None,
                   de=None, ate=None, facetas: dict | None = None,
                   valor_min=None, valor_max=None, faixa_dia: str | None = None,
                   marcadores: list[str] | None = None,
                   ordenar: str | None = None) -> dict:
    """Lista, paginado, um domínio do UFDR de um inquérito (ou de uma extração).

    busca: termo livre, procurado nos campos que o domínio declara.
    apagados: só registros com deleted_state diferente de Intact — o filtro que separa o que
    foi apagado do aparelho, disponível em todos os domínios porque o atributo é universal.
    de/ate: recorte por período (datas 'AAAA-MM-DD'), na coluna de data que o domínio declara;
    'ate' inclui o dia inteiro.
    faixa_dia: madrugada/manha/tarde/noite — hora de Brasília, nos domínios que declaram `hora`.
    facetas: {coluna: valor} de igualdade; só valem as colunas declaradas em cfg.facetas — o
    nome vem da query string, então a lista branca é o que impede injeção de identificador.
    marcadores: chaves liga/desliga declaradas pelo domínio (só com anexo, só com coordenada);
    chave fora da lista declarada é ignorada, pela mesma razão.
    valor_min/valor_max: faixa na coluna numérica do domínio (valor da transferência).
    ordenar: chave de ordenação do cabeçalho clicável — só vale se declarada em cfg.ordenacoes
    (a whitelist é o que impede o texto cru da URL de chegar ao ORDER BY); senão, ordem padrão.

    Devolve {total, pagina, limite, itens}."""
    cfg = DOMINIOS.get(dominio)
    if cfg is None:
        raise KeyError(dominio)

    pagina = max(1, pagina)
    limite = max(1, min(LIMITE_MAX_LISTAGEM, limite))
    offset = (pagina - 1) * limite

    filtros = ["e.id_inquerito = %s"]
    params: list = [id_inquerito]
    if id_extracao is not None:
        filtros.append("d.id_extracao = %s")
        params.append(id_extracao)
    if cfg.filtro:
        filtros.append(cfg.filtro)
    # o dono do aparelho é o único domínio sem deleted_state (o <model type="User"> não tem),
    # então o filtro de apagados simplesmente não se aplica a ele
    if apagados and "cd_status_ufdr" in cfg.colunas:
        filtros.append("d.cd_status_ufdr IS NOT NULL AND d.cd_status_ufdr <> 'Intact'")
    if pacote and cfg.pacote:
        filtros.append(f"d.{cfg.pacote} = %s")
        params.append(pacote)
    if cfg.data:
        data_de, data_ate = _data(de), _data(ate)
        if data_de:
            filtros.append(f"d.{cfg.data} >= %s")
            params.append(data_de)
        if data_ate:
            # < dia seguinte: 'até 10/03' tem que pegar o que aconteceu às 23h do dia 10
            filtros.append(f"d.{cfg.data} < %s")
            params.append(data_ate + timedelta(days=1))
        if cfg.hora and faixa_dia in FAIXAS_DIA:
            hora_ini, hora_fim = FAIXAS_DIA[faixa_dia]
            filtros.append(f"{_hora_local_sql('d.' + cfg.data)} BETWEEN %s AND %s")
            params.extend([hora_ini, hora_fim])
    if cfg.valor:
        v_min, v_max = _decimal(valor_min), _decimal(valor_max)
        if v_min is not None:
            filtros.append(f"d.{cfg.valor} >= %s")
            params.append(v_min)
        if v_max is not None:
            filtros.append(f"d.{cfg.valor} <= %s")
            params.append(v_max)
    for campo, escolha in (facetas or {}).items():
        if campo in cfg.facetas and escolha:
            filtros.append(f"{cfg.expressao(campo)} = %s")
            params.append(escolha)
    for chave in (marcadores or ()):
        if chave in cfg.marcadores:
            filtros.append(f"({cfg.marcadores[chave]})")
    if busca and busca.strip() and cfg.busca:
        termo = f"%{busca.strip()}%"
        ors = " OR ".join(f"d.{c} ILIKE %s" for c in cfg.busca)
        filtros.append(f"({ors})")
        params.extend([termo] * len(cfg.busca))
    where = " AND ".join(filtros)
    # a whitelist decide o ORDER BY: o ?ordenar= cru da URL nunca vai ao SQL
    ordem = cfg.ordenacoes.get(ordenar or "") or cfg.ordem

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT count(*) FROM {cfg.de()} d
                    JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = d.id_extracao
                    WHERE {where}""",
                params,
            )
            total = cur.fetchone()[0]
            cur.execute(
                f"""SELECT d.id_extracao, {', '.join('d.' + c.strip() for c in cfg.colunas.split(','))}
                    FROM {cfg.de()} d
                    JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = d.id_extracao
                    WHERE {where}
                    ORDER BY {ordem}
                    LIMIT %s OFFSET %s""",
                params + [limite, offset],
            )
            nomes = [c.name for c in cur.description]
            itens = [dict(zip(nomes, linha)) for linha in cur.fetchall()]
    # só a página desenhada é resolvida: o nome do anexo vira o arquivo do aparelho, e a tela
    # abre o binário em vez de mostrar texto solto
    if cfg.anexos:
        resolver_anexos_por_nome(itens, cfg.anexos)
    return {"total": total, "pagina": pagina, "limite": limite, "itens": itens}


def facetas_dominio(dominio: str, id_inquerito: int, id_extracao: int | None = None,
                    limite: int = 200) -> dict:
    """Valores que existem de fato nas colunas de filtro do domínio, para montar os selects.

    Só devolve o que está no banco (nada de lista fixa de "tipos possíveis": o que o Cellebrite
    grava em TransferType varia por aplicativo), com a contagem de cada valor — o investigador vê
    de cara onde está o volume. Junto vão os extremos do período e do valor, que a tela usa para
    limitar os campos de data e para escrever a faixa disponível.

    Os marcadores (só com anexo, só com coordenada) vêm com a contagem de quantos registros
    atendem cada um — a tela esconde o que daria lista vazia, do mesmo jeito que faz com o select
    sem valor nenhum.

    Devolve {facetas: {coluna: [{valor, qt}]}, periodo: {min, max}|None, valor: {min, max}|None,
    marcadores: {chave: qt}}."""
    cfg = DOMINIOS.get(dominio)
    if cfg is None:
        raise KeyError(dominio)

    onde = ["e.id_inquerito = %s"]
    params: list = [id_inquerito]
    if id_extracao is not None:
        onde.append("d.id_extracao = %s")
        params.append(id_extracao)
    if cfg.filtro:
        onde.append(cfg.filtro)
    where = " AND ".join(onde)
    de_join = (f"FROM {cfg.de()} d "
               f"JOIN investigacao.inq_tb02_extracoes e ON e.id_extracao = d.id_extracao "
               f"WHERE {where}")

    resposta: dict = {"facetas": {}, "periodo": None, "valor": None, "marcadores": {}}
    with get_connection() as conn:
        with conn.cursor() as cur:
            for campo in cfg.facetas:
                expr = cfg.expressao(campo)
                cur.execute(
                    f"""SELECT {expr}, count(*) {de_join}
                          AND {expr} IS NOT NULL AND btrim({expr}::text) <> ''
                        GROUP BY 1
                        ORDER BY count(*) DESC, 1
                        LIMIT %s""",
                    params + [limite],
                )
                resposta["facetas"][campo] = [
                    {"valor": valor, "qt": qt} for valor, qt in cur.fetchall()
                ]
            for chave, condicao in cfg.marcadores.items():
                cur.execute(f"SELECT count(*) {de_join} AND ({condicao})", params)
                resposta["marcadores"][chave] = cur.fetchone()[0]
            if cfg.data:
                cur.execute(f"SELECT min(d.{cfg.data}), max(d.{cfg.data}) {de_join}", params)
                minimo, maximo = cur.fetchone()
                if minimo is not None:
                    # [:10] serve para timestamp e para date — a tela só quer o dia, que é a
                    # granularidade do <input type="date">
                    resposta["periodo"] = {"min": minimo.isoformat()[:10],
                                           "max": maximo.isoformat()[:10]}
            if cfg.valor:
                cur.execute(f"SELECT min(d.{cfg.valor}), max(d.{cfg.valor}) {de_join}", params)
                minimo, maximo = cur.fetchone()
                if minimo is not None:
                    resposta["valor"] = {"min": float(minimo), "max": float(maximo)}
    return resposta


def contagens_dominios(id_extracao: int) -> dict | None:
    """Contagem de cada domínio novo desta extração, para a tela decidir o que exibir.

    Devolve {id_inquerito, contagens: {dominio: n|None}}; None se a extração não existir.
    Contagem None quer dizer "extração carregada antes deste domínio existir" — é diferente de
    zero, e a tela usa isso para não prometer que o aparelho não tem o dado."""
    id_inquerito = obter_id_inquerito_de_extracao(id_extracao)
    if id_inquerito is None:
        return None
    # Domínio com filtro fixo não pode usar o contador de INQ_TB02: aquele número conta a
    # tabela inteira, e a aba prometeria linhas que a listagem não mostra (a conexão sem
    # dispositivo nem MAC, o trajeto sem coordenada). Esses contam ao vivo, com o filtro.
    com_coluna = {d: c for d, c in CONTADORES.items() if c and not DOMINIOS[d].filtro
                  and not DOMINIOS[d].fonte}
    ao_vivo = [d for d, c in CONTADORES.items()
               if not c or DOMINIOS[d].filtro or DOMINIOS[d].fonte]
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {', '.join(com_coluna.values())} "
                f"FROM investigacao.inq_tb02_extracoes WHERE id_extracao = %s",
                (id_extracao,),
            )
            linha = cur.fetchone()
            if linha is None:
                return None
            contagens = dict(zip(com_coluna.keys(), linha))
            # domínio sem contador em INQ_TB02 (não vale uma coluna nova só para isso):
            # conta na hora, que é barato porque a tabela é indexada por extração
            for dominio in ao_vivo:
                cfg = DOMINIOS[dominio]
                onde = "id_extracao = %s" + (f" AND {cfg.filtro}" if cfg.filtro else "")
                cur.execute(
                    f"SELECT count(*) FROM {cfg.de()} d WHERE {onde}",
                    (id_extracao,),
                )
                contagens[dominio] = cur.fetchone()[0]
    return {"id_inquerito": id_inquerito, "contagens": contagens}

"""IA própria por hierarquia: usuário, delegacia e unidade superior — a exceção ao global (.env).

O .env é o padrão da instalação: provedor, modelo, URL e chave que valem para todo mundo. Acima
dele existe a exceção em três níveis, avaliada do mais específico para o mais geral, e o primeiro
que tem linha ATIVA vence inteiro (config_para_ia abaixo):

    usuário (seguranca.usr_tb06_config_ia, tela Usuários → "IA do usuário")
      → delegacia ┐
      → unidades  ┘ seguranca.config_ia_entidade, tela Gerenciar delegacias (docs/unidades_prompts.md)
        (do pai imediato para a raiz)
          → .env

Vence a linha INTEIRA, e não campo a campo, de propósito: "provedor da delegacia + chave do
usuário" é o tipo de mistura que quebra silenciosa quando alguém troca o gateway. Quem não tem
linha em nível nenhum — a maioria — continua caindo no .env sem notar diferença; quem tem passa a
consumir o provedor/modelo/chave daquela linha em TODA chamada de IA que fizer (chat, busca
automática, análise de documento, relatório).

Vencer também as configurações de seção (REPORT_AI_*, DOC_AI_*) é deliberado: a razão de existir
desta tela é "este analista consome a cota DELE", e uma exceção que valesse só no chat deixaria a
maior parte do gasto na conta de todo mundo.

Como enviar_mensagem (funcoes/ia_provedores.py) descobre de quem é a vez: pelo usuário publicado
no escopo da requisição por exigir_login (funcoes/auditoria_acesso.py). Em thread de job — busca
automática, análise de documento — não há requisição, e quem chama passa id_usuario na mão.
"""

import logging

from funcoes.db_utils import get_connection
from funcoes.ia_provedores import ESFORCOS_RACIOCINIO, PROVEDORES_CHAT, PROVEDORES_COM_CERTIFICADO

logger = logging.getLogger(__name__)

# Provedores locais: rodam na rede interna sem autenticação, então exigir chave neles só
# atrapalharia quem usa Ollama/LM Studio.
PROVEDORES_SEM_CHAVE = {"ollama", "lmstudio"}

_COLUNAS = (
    "id_usuario, in_ativa, co_provedor, no_modelo, tx_api_url, tx_api_key, tx_ca_cert, "
    "co_esforco_raciocinio, qt_contexto, id_usuario_autor, dt_atualizacao"
)


def _linha_para_dict(linha) -> dict:
    return {
        "id_usuario": linha[0], "in_ativa": linha[1], "co_provedor": linha[2],
        "no_modelo": linha[3], "tx_api_url": linha[4], "tx_api_key": linha[5] or "",
        "tx_ca_cert": linha[6] or "", "co_esforco_raciocinio": linha[7] or "",
        "qt_contexto": linha[8], "id_usuario_autor": linha[9], "dt_atualizacao": linha[10],
    }


def obter(id_usuario: int) -> dict | None:
    """Linha de configuração do usuário (com a chave e o certificado crus), ou None se não houver.

    A chave nunca sai daqui para o navegador — a rota devolve só a máscara.
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {_COLUNAS} FROM seguranca.usr_tb06_config_ia WHERE id_usuario = %s",
                (id_usuario,),
            )
            linha = cur.fetchone()
    return _linha_para_dict(linha) if linha else None


def ids_configurados() -> list[int]:
    """Ids dos usuários que têm configuração própria ATIVA — usado só para o selo na listagem."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id_usuario FROM seguranca.usr_tb06_config_ia WHERE in_ativa")
            return [linha[0] for linha in cur.fetchall()]


def validar(provedor: str, modelo: str, api_url: str, esforco: str, api_key: str, ca_cert: str) -> None:
    """Levanta ValueError com a mensagem que a tela mostra. Só valida o que é verificável aqui —
    se a chave está certa, quem responde é o provedor, na primeira pergunta.

    Ao contrário das Configurações, campo em branco NÃO é aceito: lá o vazio significa "mantém o
    que já está no .env", e aqui uma configuração pela metade viraria uma mistura silenciosa
    (o modelo deste usuário com a URL de todo mundo) — justamente o que é difícil de depurar
    quando a IA de um analista para de responder.
    """
    if provedor not in PROVEDORES_CHAT:
        raise ValueError("Provedor inválido.")
    if not modelo:
        raise ValueError("Informe o modelo.")
    if not api_url:
        raise ValueError("Informe a URL da API.")
    if not api_url.lower().startswith(("http://", "https://")):
        raise ValueError("URL da API deve começar com http:// ou https://.")
    if esforco and esforco not in ESFORCOS_RACIOCINIO:
        raise ValueError("Esforço de raciocínio inválido.")
    if not api_key and provedor not in PROVEDORES_SEM_CHAVE:
        raise ValueError("Informe a chave de API deste usuário.")
    if not ca_cert and provedor in PROVEDORES_COM_CERTIFICADO:
        raise ValueError("O provedor escolhido exige o certificado (.crt) do gateway.")


def salvar(
    id_usuario: int,
    *,
    ativa: bool,
    provedor: str,
    modelo: str,
    api_url: str,
    api_key: str,
    ca_cert: str,
    esforco: str,
    contexto: int | None,
    id_autor: int | None,
) -> None:
    """Grava (insere ou substitui) a configuração do usuário. Chave e certificado já resolvidos
    por quem chama — em branco no formulário significa "mantém o que está gravado", como nas
    Configurações."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO seguranca.usr_tb06_config_ia
                    (id_usuario, in_ativa, co_provedor, no_modelo, tx_api_url, tx_api_key,
                     tx_ca_cert, co_esforco_raciocinio, qt_contexto, id_usuario_autor)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id_usuario) DO UPDATE SET
                    in_ativa = EXCLUDED.in_ativa,
                    co_provedor = EXCLUDED.co_provedor,
                    no_modelo = EXCLUDED.no_modelo,
                    tx_api_url = EXCLUDED.tx_api_url,
                    tx_api_key = EXCLUDED.tx_api_key,
                    tx_ca_cert = EXCLUDED.tx_ca_cert,
                    co_esforco_raciocinio = EXCLUDED.co_esforco_raciocinio,
                    qt_contexto = EXCLUDED.qt_contexto,
                    id_usuario_autor = EXCLUDED.id_usuario_autor,
                    dt_atualizacao = now()
                """,
                (id_usuario, ativa, provedor, modelo, api_url, api_key or None,
                 ca_cert or None, esforco or None, contexto, id_autor),
            )
        conn.commit()


def remover(id_usuario: int) -> bool:
    """Apaga a configuração própria — o usuário volta ao padrão do .env. False se não havia."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM seguranca.usr_tb06_config_ia WHERE id_usuario = %s", (id_usuario,))
            apagou = cur.rowcount > 0
        conn.commit()
    return apagou


# ---------------------------------------------------------------------------
# IA própria por unidade/delegacia (seguranca.config_ia_entidade) — elo do meio da hierarquia
# ---------------------------------------------------------------------------

# prioridade na cascata: a delegacia antes das unidades, e entre unidades a mais próxima da
# delegacia primeiro (profundidade 0 = pai imediato). LIMIT 1 pega a linha vencedora inteira.
_CADEIA_ENTIDADE_SQL = """
WITH RECURSIVE alvo AS (
    SELECT d.id_delegacia, d.id_unidade
    FROM seguranca.usr_tb01_usuarios u
    JOIN seguranca.delegacias d ON d.id_delegacia = u.id_delegacia
    WHERE u.id_usuario = %s
), ancestrais(id_unidade, id_pai, profundidade) AS (
    SELECT un.id_unidade, un.id_unidade_pai, 0
    FROM alvo a
    JOIN seguranca.unidades un ON un.id_unidade = a.id_unidade
    UNION ALL
    SELECT p.id_unidade, p.id_unidade_pai, x.profundidade + 1
    FROM ancestrais x
    JOIN seguranca.unidades p ON p.id_unidade = x.id_pai
    WHERE x.profundidade < 10
)
SELECT c.co_provedor, c.no_modelo, c.tx_api_url, c.tx_api_key, c.tx_ca_cert,
       c.co_esforco_raciocinio, c.qt_contexto
FROM seguranca.config_ia_entidade c
LEFT JOIN ancestrais an ON an.id_unidade = c.id_entidade
WHERE c.in_ativa
  AND ((c.tp_nivel = 'delegacia' AND c.id_entidade = (SELECT id_delegacia FROM alvo))
       OR (c.tp_nivel = 'unidade' AND an.id_unidade IS NOT NULL))
ORDER BY CASE WHEN c.tp_nivel = 'delegacia' THEN 0 ELSE 1 + an.profundidade END
LIMIT 1
"""


def _dict_para_ia(linha) -> dict:
    """(provedor, modelo, url, chave, cert, esforço, contexto) → o dict que enviar_mensagem entende."""
    return {
        "provider": linha[0],
        "model": linha[1],
        "apiUrl": linha[2],
        "apiKey": linha[3] or "",
        "caCert": linha[4] or "",
        "reasoningEffort": linha[5] or "",
        "contextLength": linha[6] or 0,
    }


def obter_entidade(tp_nivel: str, id_entidade: int) -> dict | None:
    """A linha da unidade/delegacia (uma só por nível+entidade; in_ativa é o liga/desliga dela)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id_config, in_ativa, co_provedor, no_modelo, tx_api_url, tx_api_key,
                       tx_ca_cert, co_esforco_raciocinio, qt_contexto, dt_atualizacao
                FROM seguranca.config_ia_entidade
                WHERE tp_nivel = %s AND id_entidade = %s
                """,
                (tp_nivel, id_entidade),
            )
            linha = cur.fetchone()
    if not linha:
        return None
    return {
        "id_config": linha[0], "in_ativa": linha[1], "co_provedor": linha[2],
        "no_modelo": linha[3], "tx_api_url": linha[4], "tx_api_key": linha[5] or "",
        "tx_ca_cert": linha[6] or "", "co_esforco_raciocinio": linha[7] or "",
        "qt_contexto": linha[8], "dt_atualizacao": linha[9],
    }


def salvar_entidade(
    tp_nivel: str,
    id_entidade: int,
    *,
    ativa: bool,
    provedor: str,
    modelo: str,
    api_url: str,
    api_key: str,
    ca_cert: str,
    esforco: str,
    contexto: int | None,
    id_autor: int | None,
) -> None:
    """Insere ou substitui a linha da unidade/delegacia (chave/cert já resolvidos por quem chama).

    UPDATE-then-INSERT em vez de ON CONFLICT: o índice único é parcial (só linhas ativas) e um
    INSERT conflituaria errado com uma linha inativa — uma linha por nível+entidade é a regra."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE seguranca.config_ia_entidade SET in_ativa = %s, co_provedor = %s, "
                "no_modelo = %s, tx_api_url = %s, tx_api_key = %s, tx_ca_cert = %s, "
                "co_esforco_raciocinio = %s, qt_contexto = %s, id_usuario_autor = %s, "
                "dt_atualizacao = now() "
                "WHERE tp_nivel = %s AND id_entidade = %s",
                (ativa, provedor, modelo, api_url, api_key or None, ca_cert or None,
                 esforco or None, contexto, id_autor, tp_nivel, id_entidade),
            )
            if cur.rowcount == 0:
                cur.execute(
                    "INSERT INTO seguranca.config_ia_entidade "
                    "(tp_nivel, id_entidade, in_ativa, co_provedor, no_modelo, tx_api_url, "
                    " tx_api_key, tx_ca_cert, co_esforco_raciocinio, qt_contexto, id_usuario_autor) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (tp_nivel, id_entidade, ativa, provedor, modelo, api_url,
                     api_key or None, ca_cert or None, esforco or None, contexto, id_autor),
                )
        conn.commit()


def remover_entidade(tp_nivel: str, id_entidade: int) -> bool:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM seguranca.config_ia_entidade WHERE tp_nivel = %s AND id_entidade = %s",
                (tp_nivel, id_entidade),
            )
            apagou = cur.rowcount > 0
        conn.commit()
    return apagou


def config_para_ia(id_usuario: int | None) -> dict:
    """A linha vencedora da hierarquia no formato que enviar_mensagem entende
    (provider/model/apiUrl/apiKey/caCert/reasoningEffort/contextLength), ou {} quando ninguém da
    cadeia tem exceção — que é o caso normal e cai no .env.

    Ordem: usuário → delegacia → unidades (pai imediato → raiz). Nunca levanta: uma consulta que
    falhe aqui derrubaria toda a IA do sistema por causa de uma exceção que quase ninguém tem. O
    erro fica no log e a chamada segue no padrão do .env.
    """
    if not id_usuario:
        return {}
    try:
        linha = obter(id_usuario)
        if linha and linha["in_ativa"]:
            return {
                "provider": linha["co_provedor"],
                "model": linha["no_modelo"],
                "apiUrl": linha["tx_api_url"],
                "apiKey": linha["tx_api_key"],
                "caCert": linha["tx_ca_cert"],
                "reasoningEffort": linha["co_esforco_raciocinio"],
                "contextLength": linha["qt_contexto"] or 0,
            }
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(_CADEIA_ENTIDADE_SQL, (id_usuario,))
                vencedora = cur.fetchone()
        return _dict_para_ia(vencedora) if vencedora else {}
    except Exception:
        logger.exception("Falha ao ler a IA própria da hierarquia", extra={"id_usuario": id_usuario})
        return {}

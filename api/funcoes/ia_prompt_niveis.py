"""Prompt de IA complementar em 4 níveis: geral → unidade superior → delegacia → usuário.

O prompt de sistema base continua em código (`ia_utils.SISTEMA_PROMPT`); a tabela
`SEGURANCA.PROMPTS_IA` (ver docs/unidades_prompts.md) guarda só instruções ADICIONAIS escritas
pelo administrador, que se ACUMULAM em cascata — cada nível complementa o anterior, e o texto do
usuário é o último antes da pergunta. A ordem dos ancestrais da unidade é raiz→folha (DF:
geral → SR/PF-DF → DIP → delegacia → usuário).

A cadeia parte da delegacia do USUÁRIO (o mesmo lado "quem pede" do portão `ia_delegacia`), não a
do inquérito: as instruções que descrevem como o analista trabalha são as da unidade dele.

Sem cache de propósito: duas consultas a tabelas de dezenas de linhas por TURNO de chat (não por
rodada de ferramenta), CRUD de prompts raríssimo — uma consulta por checagem sai mais barata (e
mais correta) que um cache a invalidar no CRUD, mesmo raciocínio de
`ia_delegacia.delegacias_sem_ia`.
"""

from funcoes.db_utils import get_connection

# Teto do bloco inteiro no prompt de sistema. Instrução de administração boa cabe em parágrafos;
# passar disso é coleção de texto colado, e o preço é pago por TODO turno em tokens. O corte avisa
# dentro do próprio bloco: falta instrução, não erra.
LIMITE_CHARS_PROMPTS_NIVEIS = 8_000

# Profundidade máxima andada na árvore de unidades. A árvore real tem 2 níveis (UF → coordenação);
# o teto é cinto de segurança contra ciclo — o anti-ciclo do CRUD (rotas/unidades.py) fecha a
# porta comum, e um ciclo gravado à mão no banco não pode pendurar o chat.
_MAX_PROFUNDIDADE_UNIDADE = 10

_CABECALHO = (
    "\n\nINSTRUÇÕES ADICIONAIS DA ADMINISTRAÇÃO (complementam o método de trabalho acima; se "
    "contradizerem a pergunta do investigador, a pergunta dele vence):\n"
)

# O CTE sobe a árvore a partir da delegacia escolhida (a do usuário, ou id_delegacia direto no
# preview da tela Configurações). Devolve a delegacia + os ancestrais com profundidade — o
# chamador ordena raiz→folha invertendo a profundidade.
_CADEIA_SQL = """
WITH RECURSIVE alvo AS (
    SELECT d.id_delegacia, d.no_delegacia, d.id_unidade
    FROM seguranca.delegacias d
    WHERE d.id_delegacia = COALESCE(
        %(id_delegacia)s,
        (SELECT id_delegacia FROM seguranca.usr_tb01_usuarios WHERE id_usuario = %(id_usuario)s)
    )
), ancestrais(id_unidade, no_unidade, sg_unidade, id_pai, profundidade) AS (
    SELECT un.id_unidade, un.no_unidade, un.sg_unidade, un.id_unidade_pai, 0
    FROM alvo a
    JOIN seguranca.unidades un ON un.id_unidade = a.id_unidade
    UNION ALL
    SELECT p.id_unidade, p.no_unidade, p.sg_unidade, p.id_unidade_pai, x.profundidade + 1
    FROM ancestrais x
    JOIN seguranca.unidades p ON p.id_unidade = x.id_pai
    WHERE x.profundidade < %(max_prof)s
)
SELECT a.id_delegacia, a.no_delegacia,
       c.id_unidade, c.no_unidade, c.sg_unidade, c.profundidade
FROM alvo a
LEFT JOIN ancestrais c ON TRUE
"""

_PROMPTS_SQL = """
SELECT tp_nivel, id_entidade, tx_prompt
FROM seguranca.prompts_ia
WHERE in_ativa
  AND (tp_nivel = 'sistema'
       OR (tp_nivel = 'unidade'   AND id_entidade = ANY(%(ids_unidades)s::bigint[]))
       OR (tp_nivel = 'delegacia' AND id_entidade = %(id_delegacia)s)
       OR (tp_nivel = 'usuario'   AND id_entidade = %(id_usuario)s))
"""


def _buscar_cadeia(id_usuario: int | None, id_delegacia: int | None) -> list[tuple[str, str]]:
    """[(rótulo do bloco, texto)] na ordem da cascata, sem cabeçalho e sem corte.

    Delegacia inexistente (usuário sem unidade) não é erro: a cadeia começa e termina no nível
    'sistema' (+ 'usuario', se houver)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(_CADEIA_SQL, {
                "id_usuario": id_usuario,
                "id_delegacia": id_delegacia,
                "max_prof": _MAX_PROFUNDIDADE_UNIDADE,
            })
            linhas = cur.fetchall()

        if not linhas:  # sem delegacia na cadeia — sobra só o que não depende dela
            id_delegacia_resolvida, nome_delegacia = None, None
            ancestrais: list[tuple] = []
        else:
            id_delegacia_resolvida, nome_delegacia = linhas[0][0], linhas[0][1]
            # profundidade 0 = pai imediato; raiz→folha é o inverso
            ancestrais = sorted(
                [(u, no, sg, prof) for _, _, u, no, sg, prof in linhas if u is not None],
                key=lambda x: -x[3],
            )

        with conn.cursor() as cur:
            cur.execute(_PROMPTS_SQL, {
                "ids_unidades": [u for u, _, _, _ in ancestrais] or [0],
                "id_delegacia": id_delegacia_resolvida,
                "id_usuario": id_usuario,
            })
            prompts = cur.fetchall()

    por_entidade: dict[tuple[str, int | None], str] = {}
    for tp_nivel, id_entidade, tx_prompt in prompts:
        por_entidade.setdefault((tp_nivel, id_entidade), (tx_prompt or "").strip())

    blocos: list[tuple[str, str]] = []
    geral = por_entidade.get(("sistema", None))
    if geral:
        blocos.append(("GERAIS (administração do sistema)", geral))
    for id_unidade, no_unidade, sg_unidade, _prof in ancestrais:
        texto = por_entidade.get(("unidade", id_unidade))
        if not texto:
            continue
        sigla = (sg_unidade or "").strip()
        rotulo = f"{no_unidade} ({sigla})" if sigla else (no_unidade or "?")
        blocos.append((f"DA UNIDADE {rotulo.upper()}", texto))
    if id_delegacia_resolvida is not None:
        texto = por_entidade.get(("delegacia", id_delegacia_resolvida))
        if texto:
            blocos.append((f"DA DELEGACIA {nome_delegacia or '?'}", texto))
    if id_usuario is not None:
        texto = por_entidade.get(("usuario", id_usuario))
        if texto:
            blocos.append(("DO USUÁRIO", texto))
    return blocos


def prompt_niveis(id_usuario: int | None = None, id_delegacia: int | None = None) -> str:
    """Bloco pronto para o prompt de sistema ("" quando não há nada ativo em nenhum nível).

    `id_delegacia` existe para o preview da tela Configurações ("o que esta delegacia recebe?") —
    o chat chama só com o usuário."""
    if id_usuario is None and id_delegacia is None:
        return ""
    blocos = _buscar_cadeia(id_usuario, id_delegacia)
    if not blocos:
        return ""
    corpo = "\n".join(f"--- {rotulo} ---\n{texto}" for rotulo, texto in blocos)
    bloco = _CABECALHO + corpo
    if len(bloco) > LIMITE_CHARS_PROMPTS_NIVEIS:
        bloco = (
            bloco[:LIMITE_CHARS_PROMPTS_NIVEIS]
            + "\n--- (instruções adicionais cortadas no limite de tamanho) ---"
        )
    return bloco

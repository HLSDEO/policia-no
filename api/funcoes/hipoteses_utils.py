"""Workspace de hipóteses do inquérito: o que antes era "achado do analista" preso a um único
documento (ACH_TB01, removido na migração 0046) virou objeto estruturado e DONO DO INQUÉRITO
inteiro — hipótese com status kanban (proposta → em análise → confirmada / refutada /
arquivada), responsável, evidências a favor e contra (texto livre e/ou vínculo a um item
importado via par TP_ESCOPO/ID_ESCOPO) e pendências de diligência. Ver rotas/hipoteses.py e
web/hipoteses.html. O chat de IA do inquérito injeta o estado atual do workspace no prompt de
sistema (ver bloco_hipoteses_inquerito, usado em funcoes/ia_utils.py)."""

from funcoes.db_utils import get_connection
from funcoes.ia_utils import _TABELA_ESCOPO, _validar_escopo_documento

STATUS = ("proposta", "em_analise", "confirmada", "refutada", "arquivada")
NATUREZAS = ("hipotese", "normativa", "fato")
LADOS = ("favoravel", "contraria")

_ROTULO_STATUS = {
    "proposta": "proposta", "em_analise": "em análise", "confirmada": "confirmada",
    "refutada": "refutada", "arquivada": "arquivada",
}
_ROTULO_NATUREZA = {"hipotese": "hipótese de trabalho", "normativa": "disposição normativa", "fato": "fato apurado"}


def _linhas(cur) -> list[dict]:
    colunas = [d.name for d in cur.description]
    return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


def listar_hipoteses(id_inquerito: int, tp_escopo: str | None = None, id_escopo: int | None = None) -> list[dict]:
    """Lista as hipóteses do inquérito com contagens de evidências e pendências — é a carga
    inicial do quadro kanban (web/hipoteses.html), que agrupa por status no front. Com
    tp_escopo/id_escopo vira a visão "Hipótese da mídia": só as ligadas àquela mídia — seja pelo
    vínculo próprio da hipótese, seja por uma evidência que aponte a ela."""
    filtro_midia = """
          AND (
                (h.tp_escopo = %(tpe)s AND h.id_escopo = %(ide)s)
             OR EXISTS (SELECT 1 FROM investigacao.hip_tb02_evidencias v
                         WHERE v.id_hipotese = h.id_hipotese
                           AND v.tp_escopo = %(tpe)s AND v.id_escopo = %(ide)s)
          )
    """ if tp_escopo and id_escopo else ""
    query = f"""
        SELECT h.id_hipotese, h.tx_hipotese, h.cd_status, h.tp_natureza, h.fl_contestavel_ia,
               h.dt_criacao, h.dt_atualizacao, h.id_usuario_responsavel,
               h.tp_escopo, h.id_escopo,
               uc.no_usuario AS criado_por, ur.no_usuario AS responsavel,
               (SELECT count(*) FROM investigacao.hip_tb02_evidencias e
                 WHERE e.id_hipotese = h.id_hipotese AND e.tp_lado = 'favoravel') AS qt_favoraveis,
               (SELECT count(*) FROM investigacao.hip_tb02_evidencias e
                 WHERE e.id_hipotese = h.id_hipotese AND e.tp_lado = 'contraria') AS qt_contrarias,
               (SELECT count(*) FROM investigacao.hip_tb03_pendencias p
                 WHERE p.id_hipotese = h.id_hipotese AND NOT p.in_concluida) AS qt_pendencias
        FROM investigacao.hip_tb01_hipoteses h
        LEFT JOIN seguranca.usr_tb01_usuarios uc ON uc.id_usuario = h.id_usuario_criador
        LEFT JOIN seguranca.usr_tb01_usuarios ur ON ur.id_usuario = h.id_usuario_responsavel
        WHERE h.id_inquerito = %(inq)s{filtro_midia}
        ORDER BY h.dt_criacao DESC
    """
    params: dict = {"inq": id_inquerito}
    if tp_escopo and id_escopo:
        params["tpe"] = tp_escopo
        params["ide"] = id_escopo
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            return _linhas(cur)


def obter_hipotese(id_hipotese: int) -> dict | None:
    """Hipótese com evidências e pendências completas — o painel de detalhe da tela."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT h.id_hipotese, h.id_inquerito, h.tx_hipotese, h.cd_status, h.tp_natureza,
                       h.fl_contestavel_ia, h.id_usuario_responsavel, h.dt_criacao, h.dt_atualizacao,
                       h.tp_escopo, h.id_escopo,
                       uc.no_usuario AS criado_por, ur.no_usuario AS responsavel
                FROM investigacao.hip_tb01_hipoteses h
                LEFT JOIN seguranca.usr_tb01_usuarios uc ON uc.id_usuario = h.id_usuario_criador
                LEFT JOIN seguranca.usr_tb01_usuarios ur ON ur.id_usuario = h.id_usuario_responsavel
                WHERE h.id_hipotese = %s
                """,
                (id_hipotese,),
            )
            hipoteses = _linhas(cur)
            if not hipoteses:
                return None
            hipotese = hipoteses[0]
            cur.execute(
                """
                SELECT e.id_evidencia, e.tp_lado, e.tx_evidencia, e.tp_escopo, e.id_escopo,
                       e.dt_criacao, u.no_usuario AS criado_por
                FROM investigacao.hip_tb02_evidencias e
                LEFT JOIN seguranca.usr_tb01_usuarios u ON u.id_usuario = e.id_usuario_criador
                WHERE e.id_hipotese = %s
                ORDER BY e.dt_criacao
                """,
                (id_hipotese,),
            )
            hipotese["evidencias"] = _linhas(cur)
            cur.execute(
                """
                SELECT p.id_pendencia, p.tx_pendencia, p.in_concluida, p.dt_criacao, p.dt_conclusao,
                       u.no_usuario AS criado_por
                FROM investigacao.hip_tb03_pendencias p
                LEFT JOIN seguranca.usr_tb01_usuarios u ON u.id_usuario = p.id_usuario_criador
                WHERE p.id_hipotese = %s
                ORDER BY p.in_concluida, p.dt_criacao
                """,
                (id_hipotese,),
            )
            hipotese["pendencias"] = _linhas(cur)
    return hipotese


def _validar_midia(id_inquerito: int, tp_escopo: str | None, id_escopo: int | None) -> None:
    """Confere o par (tp_escopo, id_escopo) de vínculo com mídia: os dois juntos, escopo
    conhecido e item pertencente a este inquérito — mesma checagem que ancora uma evidência."""
    if (tp_escopo is None) != (id_escopo is None):
        raise ValueError("Vínculo com mídia exige tipo e id juntos.")
    if tp_escopo:
        if tp_escopo not in _TABELA_ESCOPO:
            raise ValueError("Escopo inválido.")
        _validar_escopo_documento(id_inquerito, tp_escopo, id_escopo)


def criar_hipotese(
    id_inquerito: int,
    texto: str,
    id_usuario: int | None,
    cd_status: str = "proposta",
    id_usuario_responsavel: int | None = None,
    tp_natureza: str = "hipotese",
    fl_contestavel_ia: bool = False,
    tp_escopo: str | None = None,
    id_escopo: int | None = None,
) -> int:
    """Registra uma nova hipótese no workspace do inquérito, opcionalmente associada a uma
    mídia importada (é por esse vínculo que ela entra no chat de IA específico dela)."""
    if cd_status not in STATUS:
        raise ValueError("Status inválido.")
    if tp_natureza not in NATUREZAS:
        raise ValueError("Natureza inválida.")
    _validar_midia(id_inquerito, tp_escopo, id_escopo)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO investigacao.hip_tb01_hipoteses
                    (id_inquerito, tx_hipotese, cd_status, id_usuario_responsavel,
                     id_usuario_criador, tp_natureza, fl_contestavel_ia, tp_escopo, id_escopo)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id_hipotese
                """,
                (id_inquerito, texto, cd_status, id_usuario_responsavel, id_usuario,
                 tp_natureza, fl_contestavel_ia, tp_escopo, id_escopo),
            )
            id_hipotese = cur.fetchone()[0]
        conn.commit()
    return id_hipotese


def obter_id_inquerito_de_hipotese(id_hipotese: int) -> int | None:
    """Resolve o id_inquerito dono de uma hipótese, para checar permissão em rotas que só recebem
    id_hipotese. Devolve None se a hipótese não existir."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id_inquerito FROM investigacao.hip_tb01_hipoteses WHERE id_hipotese = %s",
                (id_hipotese,),
            )
            row = cur.fetchone()
            return row[0] if row else None


def editar_hipotese(
    id_hipotese: int,
    texto: str,
    cd_status: str,
    id_usuario_responsavel: int | None,
    tp_natureza: str,
    fl_contestavel_ia: bool,
    id_inquerito: int,
    tp_escopo: str | None = None,
    id_escopo: int | None = None,
) -> bool:
    """Atualiza uma hipótese existente (texto, status do quadro, responsável, mídia associada);
    False se não existir. id_inquerito dono entra para validar o vínculo de mídia."""
    if cd_status not in STATUS:
        raise ValueError("Status inválido.")
    if tp_natureza not in NATUREZAS:
        raise ValueError("Natureza inválida.")
    _validar_midia(id_inquerito, tp_escopo, id_escopo)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE investigacao.hip_tb01_hipoteses
                SET tx_hipotese = %s, cd_status = %s, id_usuario_responsavel = %s,
                    tp_natureza = %s, fl_contestavel_ia = %s, tp_escopo = %s, id_escopo = %s,
                    dt_atualizacao = now()
                WHERE id_hipotese = %s
                """,
                (texto, cd_status, id_usuario_responsavel, tp_natureza, fl_contestavel_ia,
                 tp_escopo, id_escopo, id_hipotese),
            )
            atualizou = cur.rowcount > 0
        conn.commit()
    return atualizou


def apagar_hipotese(id_hipotese: int) -> bool:
    """Apaga uma hipótese; evidências e pendências saem junto pelo ON DELETE CASCADE."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM investigacao.hip_tb01_hipoteses WHERE id_hipotese = %s", (id_hipotese,))
            apagou = cur.rowcount > 0
        conn.commit()
    return apagou


def criar_evidencia(
    id_hipotese: int,
    tp_lado: str,
    texto: str,
    tp_escopo: str | None,
    id_escopo: int | None,
    id_usuario: int | None,
) -> int:
    """Registra uma evidência (a favor ou contra) na hipótese. Quando houver vínculo com item
    importado, o par (tp_escopo, id_escopo) é validado contra o inquérito dono da hipótese —
    mesma checagem que prende uma conversa de IA a um documento."""
    if tp_lado not in LADOS:
        raise ValueError("Lado da evidência inválido.")
    if (tp_escopo is None) != (id_escopo is None):
        raise ValueError("Vínculo com item exige tipo e id juntos.")
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id_inquerito FROM investigacao.hip_tb01_hipoteses WHERE id_hipotese = %s",
                        (id_hipotese,))
            row = cur.fetchone()
            if not row:
                raise ValueError("Hipótese não encontrada.")
            if tp_escopo:
                if tp_escopo not in _TABELA_ESCOPO:
                    raise ValueError("Escopo inválido.")
                _validar_escopo_documento(row[0], tp_escopo, id_escopo)
            cur.execute(
                """
                INSERT INTO investigacao.hip_tb02_evidencias
                    (id_hipotese, tp_lado, tx_evidencia, tp_escopo, id_escopo, id_usuario_criador)
                VALUES (%s, %s, %s, %s, %s, %s) RETURNING id_evidencia
                """,
                (id_hipotese, tp_lado, texto, tp_escopo, id_escopo, id_usuario),
            )
            id_evidencia = cur.fetchone()[0]
        conn.commit()
    return id_evidencia


def obter_id_hipotese_de_evidencia(id_evidencia: int) -> int | None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id_hipotese FROM investigacao.hip_tb02_evidencias WHERE id_evidencia = %s",
                        (id_evidencia,))
            row = cur.fetchone()
            return row[0] if row else None


def apagar_evidencia(id_evidencia: int) -> bool:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM investigacao.hip_tb02_evidencias WHERE id_evidencia = %s", (id_evidencia,))
            apagou = cur.rowcount > 0
        conn.commit()
    return apagou


def criar_pendencia(id_hipotese: int, texto: str, id_usuario: int | None) -> int:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO investigacao.hip_tb03_pendencias (id_hipotese, tx_pendencia, id_usuario_criador)
                VALUES (%s, %s, %s) RETURNING id_pendencia
                """,
                (id_hipotese, texto, id_usuario),
            )
            id_pendencia = cur.fetchone()[0]
        conn.commit()
    return id_pendencia


def obter_id_hipotese_de_pendencia(id_pendencia: int) -> int | None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id_hipotese FROM investigacao.hip_tb03_pendencias WHERE id_pendencia = %s",
                        (id_pendencia,))
            row = cur.fetchone()
            return row[0] if row else None


def editar_pendencia(id_pendencia: int, texto: str, in_concluida: bool) -> bool:
    """Marca/desmarca a pendência como concluída (e permite corrigir o texto)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE investigacao.hip_tb03_pendencias
                SET tx_pendencia = %s, in_concluida = %s,
                    dt_conclusao = CASE WHEN %s AND dt_conclusao IS NULL THEN now()
                                        WHEN NOT %s THEN NULL ELSE dt_conclusao END
                WHERE id_pendencia = %s
                """,
                (texto, in_concluida, in_concluida, in_concluida, id_pendencia),
            )
            atualizou = cur.rowcount > 0
        conn.commit()
    return atualizou


def apagar_pendencia(id_pendencia: int) -> bool:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM investigacao.hip_tb03_pendencias WHERE id_pendencia = %s", (id_pendencia,))
            apagou = cur.rowcount > 0
        conn.commit()
    return apagou


def usuarios_para_responsavel(id_inquerito: int) -> list[dict]:
    """Quem pode ser responsável por uma hipótese deste inquérito: quem o criou, quem tem
    permissão concedida, admins e os colegas de delegacia de quem criou — todos ativos e
    aprovados. É a lista do seletor "Responsável" no painel da hipótese."""
    query = """
        SELECT DISTINCT u.id_usuario, u.no_usuario
        FROM seguranca.usr_tb01_usuarios u
        JOIN investigacao.inq_tb01_inqueritos i ON i.id_inquerito = %s
        WHERE u.in_ativo AND u.in_aprovado
          AND (u.id_usuario = i.id_usuario_criador
               OR u.in_admin
               OR EXISTS (SELECT 1 FROM investigacao.inq_tb03_permissoes p
                           WHERE p.id_inquerito = i.id_inquerito AND p.id_usuario = u.id_usuario)
               OR (u.id_delegacia IS NOT NULL
                   AND u.id_delegacia = (SELECT c.id_delegacia FROM seguranca.usr_tb01_usuarios c
                                          WHERE c.id_usuario = i.id_usuario_criador)))
        ORDER BY u.no_usuario
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (id_inquerito,))
            return _linhas(cur)


def bloco_hipoteses_inquerito(
    id_inquerito: int, tp_escopo: str | None = None, id_escopo: int | None = None
) -> str:
    """Bloco pro prompt de sistema com o estado do workspace de hipóteses do inquérito inteiro —
    é o sucessor do antigo bloco_achados_analista (que só enxergava o documento da conversa).
    Conversa do inquérito inteiro vê o quadro completo; conversa RESTRITA a uma mídia (tp_escopo/
    id_escopo) vê só as hipóteses daquela mídia — as associadas a ela pelo vínculo próprio OU por
    uma evidência que aponte a ela ("hipótese da mídia" no chat específico dela). Cada hipótese
    traz status, responsável, evidências dos dois lados e pendências em aberto. Hipóteses
    arquivadas ficam de fora — quem arquivou não quer mais aquilo no radar da IA. Vazio se não
    houver hipótese nenhuma no recorte."""
    filtro_midia = """
          AND (
                (h.tp_escopo = %(tpe)s AND h.id_escopo = %(ide)s)
             OR EXISTS (SELECT 1 FROM investigacao.hip_tb02_evidencias v
                         WHERE v.id_hipotese = h.id_hipotese
                           AND v.tp_escopo = %(tpe)s AND v.id_escopo = %(ide)s)
          )
    """ if tp_escopo and id_escopo else ""
    with get_connection() as conn:
        with conn.cursor() as cur:
            params: dict = {"inq": id_inquerito}
            if tp_escopo and id_escopo:
                params["tpe"] = tp_escopo
                params["ide"] = id_escopo
            cur.execute(
                f"""
                SELECT h.id_hipotese, h.tx_hipotese, h.cd_status, h.tp_natureza,
                       h.fl_contestavel_ia, ur.no_usuario
                FROM investigacao.hip_tb01_hipoteses h
                LEFT JOIN seguranca.usr_tb01_usuarios ur ON ur.id_usuario = h.id_usuario_responsavel
                WHERE h.id_inquerito = %(inq)s AND h.cd_status <> 'arquivada'{filtro_midia}
                ORDER BY h.dt_criacao
                """,
                params,
            )
            hipoteses = cur.fetchall()
            if not hipoteses:
                return ""
            ids = [h[0] for h in hipoteses]
            cur.execute(
                """
                SELECT id_hipotese, tp_lado, tx_evidencia FROM investigacao.hip_tb02_evidencias
                WHERE id_hipotese = ANY(%s) ORDER BY dt_criacao
                """,
                (ids,),
            )
            evidencias = cur.fetchall()
            cur.execute(
                """
                SELECT id_hipotese, tx_pendencia FROM investigacao.hip_tb03_pendencias
                WHERE id_hipotese = ANY(%s) AND NOT in_concluida ORDER BY dt_criacao
                """,
                (ids,),
            )
            pendencias = cur.fetchall()
    por_hipotese: dict[int, dict] = {h[0]: {"fav": [], "con": [], "pen": []} for h in hipoteses}
    for id_hipotese, lado, texto in evidencias:
        por_hipotese[id_hipotese]["fav" if lado == "favoravel" else "con"].append(texto)
    for id_hipotese, texto in pendencias:
        por_hipotese[id_hipotese]["pen"].append(texto)
    linhas = []
    for id_hipotese, texto, status, natureza, contestavel, responsavel in hipoteses:
        tag = _ROTULO_STATUS.get(status, status) + " · " + _ROTULO_NATUREZA.get(natureza, natureza)
        if responsavel:
            tag += f" · responsável: {responsavel}"
        if not contestavel:
            tag += " · não contestável pela IA"
        linhas.append(f"- [{tag}] {texto}")
        extra = [f"    · evidência a favor: {t}" for t in por_hipotese[id_hipotese]["fav"]]
        extra += [f"    · evidência CONTRA: {t}" for t in por_hipotese[id_hipotese]["con"]]
        extra += [f"    · pendência em aberto: {t}" for t in por_hipotese[id_hipotese]["pen"]]
        linhas += extra
    itens = "\n".join(linhas)
    return (
        "\n\n## HIPÓTESES DO INQUÉRITO (workspace do analista)\n"
        "O investigador mantém um quadro de hipóteses para o inquérito inteiro. Cada hipótese "
        "traz status no quadro (proposta / em análise / confirmada / refutada), responsável, "
        "natureza (fato apurado / hipótese de trabalho / disposição normativa) e suas evidências "
        "a favor e contra. Hipóteses marcadas como 'não contestável pela IA' devem ser tratadas "
        "como verdade já estabelecida — não tente confirmar, refutar ou pedir mais provas sobre "
        "elas. Nas demais, pondere as evidências dos dois lados antes de concluir, e leve em "
        "conta as pendências em aberto (diligências que ainda faltam).\n" + itens
    )

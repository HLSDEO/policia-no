"""Resumo noturno por inquérito (batch da madrugada).

Gera, por inquérito, um consolidado do caso + uma linha por fonte (cada celular,
cada RIF/SIMBA/IPEI/IPL/IRPF/telefonia/IPED e o agregado de documentos genéricos),
gravados em INVESTIGACAO.INQ_TB05_RESUMO_NOTURNO. A tela
investigacao.html?inquerito=ID lê só daqui — nunca chama a IA no request.

Pipeline (uma chamada de IA por fonte + uma de síntese, sem function calling):
1. por celular: conteudo do aparelho (montar_conteudo_inquerito com extracoes=[id])
   -> "liste as principais conversas suspeitas + prática delitiva suspeita".
2. demais arquivos (RIF/SIMBA/...): agregados tabulares (totais + amostras), nunca o
   extrato integral -> "só o que tiver de suspeito".
3. peças genéricas: reuso de INQ_TB04_DOCUMENTOS.TX_RESUMO_IA (sem nova chamada).
4. síntese INQUÉRITO: junta 1+2+3 em {resumo, por_celular, financeiro, diligências}.

Só reprocessa o que mudou: TX_HASH_ENTRADA agrega contagens + datas de importação +
versões de análise. Igual ao último concluído = pula sem chamar a IA.
"""

import hashlib
import json
import logging
import time

from funcoes.db_utils import get_connection
from funcoes.env_utils import parse_env, read_env_text

logger = logging.getLogger(__name__)

# Teto de caracteres por fonte no prompt. O dump integral de um celular grande passa de
# MB — sem teto, uma fonte sozinha estoura a janela e derruba o passe inteiro.
MAX_CHARS_CELULAR = 60_000
MAX_CHARS_FONTE_FINANCEIRA = 20_000
MAX_CHARS_SINTESE_ENTRADA = 40_000

_TP_FONTES_ARQUIVO = ("rif", "simba", "ipei", "ipl", "irpf", "telefonia", "iped")


# =====================================================================
# Config própria (RESUMO_NOTURNO_*), com queda para AI_*
# =====================================================================

def _config_resumo_noturno() -> dict | None:
    """Config da IA do resumo noturno (aba "Resumo noturno" em Configurações, RESUMO_NOTURNO_*).

    None quando nada foi configurado ali — aí a chamada cai na IA padrão do prompt,
    que é o comportamento antigo (mesmo padrão de DOC_AI_*/REPORT_AI_*)."""
    env = parse_env(read_env_text())
    if not (env.get("RESUMO_NOTURNO_PROVIDER") or env.get("RESUMO_NOTURNO_MODEL")
            or env.get("RESUMO_NOTURNO_API_URL")):
        return None
    return {
        "provider": env.get("RESUMO_NOTURNO_PROVIDER", ""),
        "model": env.get("RESUMO_NOTURNO_MODEL", ""),
        "apiUrl": env.get("RESUMO_NOTURNO_API_URL", ""),
        "apiKey": env.get("RESUMO_NOTURNO_API_KEY", ""),
        "caCert": env.get("RESUMO_NOTURNO_CA_CERT", ""),
        "reasoningEffort": env.get("RESUMO_NOTURNO_REASONING_EFFORT", ""),
    }


def resumo_noturno_configurado(id_usuario: int | None = None) -> bool:
    """Há IA utilizável para o passe? Vale o mesmo preenchimento por campo do enviar_mensagem:
    IA própria do usuário vence, depois cada campo RESUMO_NOTURNO_* sobrescreve o AI_*."""
    try:
        from funcoes.config_ia_usuario import config_para_ia
        if config_para_ia(id_usuario):
            return True
    except Exception:
        pass
    env = parse_env(read_env_text())
    return all(
        env.get(f"RESUMO_NOTURNO_{campo}") or env.get(f"AI_{campo}")
        for campo in ("PROVIDER", "MODEL", "API_URL")
    )


def _limite_chars_fonte(nome: str) -> int:
    try:
        env = parse_env(read_env_text())
        bruto = int(env.get(nome) or 0)
        if bruto > 0:
            return max(10_000, min(bruto, 200_000))
    except ValueError:
        pass
    return 0


# =====================================================================
# Leitura (o que a tela consome)
# =====================================================================

def _tabela_existe() -> bool:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'investigacao' AND table_name = 'inq_tb05_resumo_noturno'"
            )
            return cur.fetchone() is not None


def obter_resumo_inquerito(id_inquerito: int) -> dict:
    """Último passe concluído do inquérito: consolidado + linhas por fonte.

    Devolve {"tem": bool, "consolidado": {...}|None, "por_fonte": [...],
    "desatualizado": bool, "hash_atual": str}. Nunca levanta se a tabela ainda não
    existir (migração pendente) — a tela mostra "sem resumo" em vez de 500."""
    vazio: dict = {"tem": False, "consolidado": None, "por_fonte": [],
                   "desatualizado": False, "hash_atual": None}
    try:
        if not _tabela_existe():
            return vazio
    except Exception:
        return vazio
    try:
        hash_atual = calcular_hash_entrada(id_inquerito)
    except Exception:
        hash_atual = None
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id_resumo, id_inquerito, id_extracao, tp_fonte, id_fonte,
                       tx_resumo, jq_conversas, no_modelo,
                       qt_tokens_entrada, qt_tokens_saida, vl_custo_usd,
                       tx_hash_entrada, cd_status, tx_erro, dt_geracao, dt_fim
                FROM investigacao.inq_tb05_resumo_noturno
                WHERE id_inquerito = %s AND cd_status = 'concluido'
                ORDER BY dt_geracao DESC
                """,
                (id_inquerito,),
            )
            colunas = [d.name for d in cur.description]
            linhas = [dict(zip(colunas, l)) for l in cur.fetchall()]
    if not linhas:
        vazio["hash_atual"] = hash_atual
        return vazio
    # só o passe mais recente por (tp_fonte, id_fonte/id_extracao)
    vistos = set()
    recentes: list[dict] = []
    for linha in linhas:
        chave = (linha["tp_fonte"], linha["id_extracao"], linha["id_fonte"])
        if chave in vistos:
            continue
        vistos.add(chave)
        recentes.append(linha)
    consolidado = next((l for l in recentes if l["tp_fonte"] == "consolidado"), None)
    por_fonte = [l for l in recentes if l["tp_fonte"] != "consolidado"]
    ordem = {"celular": 0, "rif": 1, "simba": 2, "ipei": 3, "ipl": 4, "irpf": 5,
             "telefonia": 6, "iped": 7, "documentos": 8}
    por_fonte.sort(key=lambda l: (ordem.get(l["tp_fonte"], 9), str(l.get("id_fonte") or "")))
    desatualizado = bool(
        consolidado and hash_atual and consolidado.get("tx_hash_entrada")
        and consolidado["tx_hash_entrada"] != hash_atual
    )
    return {"tem": True, "consolidado": consolidado, "por_fonte": por_fonte,
            "desatualizado": desatualizado, "hash_atual": hash_atual}


# =====================================================================
# Hash de entrada ("só se mudou")
# =====================================================================

def calcular_hash_entrada(id_inquerito: int) -> str:
    """Assinatura do material do inquérito: extrações concluídas, RIF/SIMBA/IPL/IRPF/
    IPEI/telefonia/IPED e documentos + versões de análise. Mudou qualquer coisa
    relevante -> hash diferente -> o passe regenera."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT coalesce(string_agg(
                    e.id_extracao || ':' || e.cd_status || ':' || coalesce(e.qt_mensagens,0)
                    || ':' || coalesce(e.qt_sms_mensagens,0) || ':' || coalesce(e.qt_msg_mensagens,0)
                    || ':' || coalesce(to_char(e.dt_fim_processamento,'YYYYMMDDHH24MISS'),'-'), '|'
                    ORDER BY e.id_extracao), '')
                FROM investigacao.inq_tb02_extracoes e WHERE e.id_inquerito = %s
                """,
                (id_inquerito,),
            )
            extracoes = cur.fetchone()[0] or ""
            cur.execute(
                """
                SELECT (SELECT count(*) FROM investigacao.rif_tb01_rifs WHERE id_inquerito = %s),
                       (SELECT count(*) FROM investigacao.smb_tb01_extratos WHERE id_inquerito = %s),
                       (SELECT count(*) FROM investigacao.ipl_tb01_inqueritos_pdf WHERE id_inquerito = %s),
                       (SELECT count(*) FROM investigacao.irp_tb01_declaracoes WHERE id_inquerito = %s),
                       (SELECT count(*) FROM investigacao.ipe_tb01_ipeis WHERE id_inquerito = %s),
                       (SELECT count(*) FROM investigacao.tel_tb01_telefonia WHERE id_inquerito = %s),
                       (SELECT count(*) FROM investigacao.ipd_tb01_extracoes WHERE id_inquerito = %s)
                """,
                (id_inquerito,) * 7,
            )
            contagens = "|".join(str(v) for v in cur.fetchone())
            cur.execute(
                """
                SELECT coalesce(string_agg(
                    d.id_documento || ':' || coalesce(d.cd_analise,'') || ':'
                    || coalesce(d.qt_caracteres,0), '|' ORDER BY d.id_documento), '')
                FROM investigacao.inq_tb04_documentos d WHERE d.id_inquerito = %s
                """,
                (id_inquerito,),
            )
            docs = cur.fetchone()[0] or ""
    bruto = f"{extracoes}#{contagens}#{docs}"
    return hashlib.sha256(bruto.encode("utf-8")).hexdigest()[:32]


def _tem_extracao_concluida(id_inquerito: int) -> bool:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM investigacao.inq_tb02_extracoes "
                "WHERE id_inquerito = %s AND cd_status = 'concluido' LIMIT 1",
                (id_inquerito,),
            )
            return cur.fetchone() is not None


def listar_inqueritos_para_passe(id_inquerito: int | None = None) -> list[int]:
    """Inquéritos com ao menos uma extração concluída (o passe só faz sentido com
    material de celular; RIF/SIMBA/docs sozinhos não disparam o consolidado)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            if id_inquerito is not None:
                cur.execute(
                    "SELECT id_inquerito FROM investigacao.inq_tb01_inqueritos WHERE id_inquerito = %s",
                    (id_inquerito,),
                )
                return [r[0] for r in cur.fetchall()]
            cur.execute(
                """
                SELECT DISTINCT e.id_inquerito
                FROM investigacao.inq_tb02_extracoes e
                WHERE e.cd_status = 'concluido'
                ORDER BY e.id_inquerito
                """
            )
            return [r[0] for r in cur.fetchall()]


# =====================================================================
# Chamada de IA (uma por fonte, sem tools)
# =====================================================================

_SISTEMA_BASE = (
    "Você é um analista de investigação criminal. Responda em português, só com o que o "
    "material mostra — distinga FATO do que é sua INFERÊNCIA e nunca invente. "
    "Devolva SOMENTE um objeto JSON válido, sem markdown nem texto em volta."
)

_PROMPT_CELULAR = (
    _SISTEMA_BASE +
    "\n\nAnalise o conteúdo extraído deste APARELHO (um celular do inquérito) e devolva neste "
    "formato exato:\n"
    '{"resumo": "até ~10 linhas: de quem é o aparelho, com quem fala, o que chama atenção", '
    '"conversas_suspeitas": [{"contato": "nome", "app": "WhatsApp|SMS|...", '
    '"motivo": "por que é suspeita (trecho curto)", "pratica_suspeita": '
    '"tráfico de drogas|lavagem de dinheiro|arma|homicídio/ameaça|corrupção|fraude|outro", '
    '"refs": ["[msg:wpp:123]"]}], '
    '"pratica_principal": "a prática delitiva mais provável neste aparelho", '
    '"confianca": "alta|media|baixa", "lacunas": "o que faltou para concluir"}\n'
    "Até 10 conversas, as mais suspeitas primeiro. Mensagem de voz vale pela transcrição. "
    "Se nada parecer suspeito, diga isso em resumo e deixe conversas_suspeitas vazia."
)

_PROMPT_FINANCEIRO = (
    _SISTEMA_BASE +
    "\n\nAnalise este AGREGADO financeiro/jurídico do inquérito (não é o extrato integral, "
    "são totais + amostras) e devolva neste formato exato:\n"
    '{"resumo": "até ~10 linhas: quem movimentou, valores, contrapartes, o que é suspeito", '
    '"achados": [{"descricao": "achado", "valor": "R$ ...", "envolvidos": "quem"}], '
    '"pratica_suspeita": "lavagem|incompatibilidade patrimonial|fraude|...|nada evidente", '
    '"confianca": "alta|media|baixa"}\n'
    "Não some amostras como se fossem totais — os totais já vêm calculados no texto."
)

_PROMPT_SINTESE = (
    _SISTEMA_BASE +
    "\n\nJunte as análises parciais deste INQUÉRITO (vários celulares + financeiro + peças) "
    "numa resposta única neste formato exato:\n"
    '{"resumo": "até ~15 linhas: o caso em poucas frases (o xpto do topo)", '
    '"por_celular": [{"dispositivo": "arquivo/aparelho", "resumo": "..."}], '
    '"financeiro": "síntese RIF/SIMBA/IPEI/... em até ~8 linhas", '
    '"pecas": "síntese dos documentos genéricos em até ~5 linhas", '
    '"praticas_suspeitas": ["..."], '
    '"proximas_diligencias": ["..."]}\n'
    "Preserve os marcadores [msg:...]/[doc:...] citados nas parciais."
)


def _chamar_ia(sistema: str, usuario: str, id_usuario: int | None = None) -> tuple[str, str, object]:
    from funcoes.ia_provedores import AuditoriaIA, enviar_mensagem

    auditoria = AuditoriaIA()
    resposta, modelo = enviar_mensagem(
        [{"role": "user", "content": usuario}],
        sistema=sistema,
        auditoria=auditoria,
        config=_config_resumo_noturno(),
        id_usuario=id_usuario,
    )
    return resposta, modelo, auditoria


def _auditar(origem: str, prompt: str, sistema: str, auditoria, modelo: str | None,
             inicio: float, resposta: str, id_inquerito: int,
             id_usuario: int | None) -> None:
    from funcoes.ia_auditoria import registrar_auditoria
    try:
        registrar_auditoria(
            origem, prompt, sistema, auditoria, modelo,
            int((time.monotonic() - inicio) * 1000), resposta=resposta,
            id_inquerito=id_inquerito, id_usuario=id_usuario,
        )
    except Exception:
        logger.exception("resumo_noturno: falha ao auditar turno do inquérito %s", id_inquerito)


def _extrair_json(resposta: str) -> dict:
    texto = (resposta or "").strip()
    inicio = texto.find("{")
    fim = texto.rfind("}")
    if inicio == -1 or fim == -1 or fim <= inicio:
        raise ValueError("A IA não devolveu um JSON válido no resumo noturno.")
    dados = json.loads(texto[inicio:fim + 1])
    if not isinstance(dados, dict):
        raise ValueError("A resposta do resumo noturno não é um objeto.")
    return dados


def _resumo_legivel(tp_fonte: str, dados: dict, rotulo: str) -> tuple[str, object]:
    """Texto corrido para a tela + conversas estruturadas (só o celular tem)."""
    if tp_fonte == "celular":
        linhas = [str(dados.get("resumo") or "").strip()]
        if dados.get("pratica_principal"):
            linhas.append(f"Prática principal suspeita: {dados['pratica_principal']}.")
        for c in (dados.get("conversas_suspeitas") or [])[:10]:
            if not isinstance(c, dict):
                continue
            refs = " ".join(c.get("refs") or [])
            linhas.append(
                f"• {c.get('contato') or '?'} ({c.get('app') or '?'}): "
                f"{c.get('motivo') or ''} [suspeita: {c.get('pratica_suspeita') or '?'}]"
                + (f" {refs}" if refs else "")
            )
        if dados.get("lacunas"):
            linhas.append(f"Lacunas: {dados['lacunas']}.")
        convs = [c for c in (dados.get("conversas_suspeitas") or []) if isinstance(c, dict)][:10]
        return "\n".join(l for l in linhas if l).strip(), convs or None
    if tp_fonte == "consolidado":
        linhas = [str(dados.get("resumo") or "").strip()]
        if dados.get("financeiro"):
            linhas.append(f"\nFinanceiro: {dados['financeiro']}")
        if dados.get("pecas"):
            linhas.append(f"\nPeças: {dados['pecas']}")
        if dados.get("praticas_suspeitas"):
            prat = ", ".join(dados["praticas_suspeitas"]) if isinstance(dados["praticas_suspeitas"], list) else str(dados["praticas_suspeitas"])
            linhas.append(f"\nPráticas suspeitas: {prat}.")
        if dados.get("proximas_diligencias"):
            dil = dados["proximas_diligencias"]
            dil_tx = "\n".join(f"• {d}" for d in dil) if isinstance(dil, list) else str(dil)
            linhas.append(f"\nPróximas diligências:\n{dil_tx}")
        return "\n".join(l for l in linhas if l).strip(), None
    linhas = [str(dados.get("resumo") or "").strip()]
    for a in (dados.get("achados") or [])[:15]:
        if isinstance(a, dict):
            linhas.append(f"• {a.get('descricao') or ''} ({a.get('valor') or ''} — {a.get('envolvidos') or ''})".strip())
        else:
            linhas.append(f"• {a}")
    if dados.get("pratica_suspeita"):
        linhas.append(f"Suspeita: {dados['pratica_suspeita']}.")
    return "\n".join(l for l in linhas if l).strip(), None


# =====================================================================
# Montagem das entradas por fonte (agregados, nunca integrais)
# =====================================================================

def _extracoes_concluidas(id_inquerito: int) -> list[dict]:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id_extracao, no_arquivo, no_dispositivo, qt_mensagens,
                       qt_sms_mensagens, qt_msg_mensagens, dt_fim_processamento
                FROM investigacao.inq_tb02_extracoes
                WHERE id_inquerito = %s AND cd_status = 'concluido'
                  AND (no_fabricante IS NULL OR no_fabricante <> 'IPED')
                ORDER BY id_extracao
                """,
                (id_inquerito,),
            )
            cols = [d.name for d in cur.description]
            return [dict(zip(cols, l)) for l in cur.fetchall()]


def _texto_celular(id_inquerito: int, ext: dict) -> str:
    from funcoes.ia_investigacao import montar_conteudo_inquerito
    teto = _limite_chars_fonte("RESUMO_NOTURNO_MAX_CHARS_CELULAR") or MAX_CHARS_CELULAR
    try:
        conteudo = montar_conteudo_inquerito(
            id_inquerito, max_chars=teto, extracoes=[ext["id_extracao"]])
    except Exception as exc:
        logger.exception("resumo_noturno: falha ao montar conteúdo da extração %s", ext["id_extracao"])
        conteudo = f"(não foi possível montar o conteúdo do aparelho: {exc})"
    nome = ext.get("no_dispositivo") or (ext.get("no_arquivo") or "").split("/")[-1]
    return f"APARELHO: {nome} (arquivo {ext.get('no_arquivo')})\n\n{conteudo}"[: teto + 500]


def _texto_rif(id_inquerito: int, id_rif: int) -> str:
    from funcoes.relatorios_utils import resumo_rif
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT nu_rif, qt_comunicacoes, qt_envolvidos, qt_ocorrencias "
                "FROM investigacao.rif_tb01_rifs WHERE id_rif = %s AND id_inquerito = %s",
                (id_rif, id_inquerito),
            )
            row = cur.fetchone()
            if not row:
                return ""
            cur.execute(
                """
                SELECT c.dt_operacao, c.no_comunicante, c.vl_campo_b, c.vl_campo_c,
                       left(c.tx_informacoes_adicionais, 1200) AS analise
                FROM investigacao.rif_tb02_comunicacoes c
                WHERE c.id_rif = %s
                ORDER BY greatest(coalesce(c.vl_campo_b,0), coalesce(c.vl_campo_c,0)) DESC NULLS LAST
                LIMIT 15
                """,
                (id_rif,),
            )
            cols = [d.name for d in cur.description]
            coms = [dict(zip(cols, l)) for l in cur.fetchall()]
            cur.execute(
                """
                SELECT no_envolvido, tp_envolvido, count(*) AS qt
                FROM investigacao.rif_tb03_envolvidos WHERE id_rif = %s
                GROUP BY no_envolvido, tp_envolvido ORDER BY qt DESC LIMIT 15
                """,
                (id_rif,),
            )
            cols2 = [d.name for d in cur.description]
            envs = [dict(zip(cols2, l)) for l in cur.fetchall()]
    try:
        sint = resumo_rif(id_inquerito, [id_rif]) or {}
    except Exception:
        sint = {}
    partes = [f"RIF {row[0] or id_rif}: {row[1]} comunicações, {row[2]} envolvidos, {row[3]} ocorrências."]
    if sint.get("nu_rifs"):
        partes.append(f"RIFs: {sint['nu_rifs']}.")
    for c in coms:
        partes.append(
            f"• {c.get('dt_operacao') or '?'} {c.get('no_comunicante') or ''} "
            f"crédito {c.get('vl_campo_b') or 0} débito {c.get('vl_campo_c') or 0} — "
            f"{(c.get('analise') or '')[:600]}".strip())
    if envs:
        partes.append("Envolvidos: " + "; ".join(
            f"{e.get('no_envolvido')} ({e.get('tp_envolvido')}, {e.get('qt')}x)" for e in envs))
    return "\n".join(partes)[:MAX_CHARS_FONTE_FINANCEIRA]


def _texto_simba(id_inquerito: int, id_extrato: int) -> str:
    from funcoes.simba_utils import listar_contas_simba, obter_extrato
    try:
        ext = obter_extrato(id_extrato)
    except Exception:
        ext = None
    if not ext or ext.get("id_inquerito") != id_inquerito:
        return ""
    try:
        contas = listar_contas_simba(id_extrato)[:10]
    except Exception:
        contas = []
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.no_titular, cp.no_contraparte, cp.cd_direcao, cp.vl_total, cp.qt_lancamentos
                FROM investigacao.smb_tb04_contrapartes cp
                JOIN investigacao.smb_tb02_contas c ON c.id_conta_simba = cp.id_conta_simba
                WHERE cp.id_extrato = %s
                ORDER BY cp.vl_total DESC NULLS LAST LIMIT 15
                """,
                (id_extrato,),
            )
            cols = [d.name for d in cur.description]
            cps = [dict(zip(cols, l)) for l in cur.fetchall()]
    partes = [f"SIMBA {ext.get('no_arquivo')}: {ext.get('qt_contas')} contas, "
              f"{ext.get('qt_lancamentos')} lançamentos, crédito {ext.get('vl_credito')} "
              f"débito {ext.get('vl_debito')}, período {ext.get('dt_primeiro_lancamento')} a "
              f"{ext.get('dt_ultimo_lancamento')}."]
    for c in contas:
        partes.append(f"• Conta {c.get('no_titular')} {c.get('nu_conta')} ({c.get('no_banco')}): "
                      f"crédito {c.get('vl_credito')} débito {c.get('vl_debito')}.")
    for cp in cps:
        partes.append(f"• {cp.get('cd_direcao')}: {cp.get('no_contraparte')} — "
                      f"{cp.get('vl_total')} em {cp.get('qt_lancamentos')} lanç. "
                      f"(titular: {cp.get('no_titular')})")
    return "\n".join(partes)[:MAX_CHARS_FONTE_FINANCEIRA]


def _texto_generico_fonte(tp: str, id_inquerito: int, id_fonte: int) -> str:
    """Agregado curto para ipei/ipl/irpf/telefonia/iped — totais + top linhas."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            if tp == "ipl":
                cur.execute(
                    "SELECT nu_ipl, qt_folhas FROM investigacao.ipl_tb01_inqueritos_pdf "
                    "WHERE id_ipl = %s AND id_inquerito = %s", (id_fonte, id_inquerito))
                row = cur.fetchone()
                if not row:
                    return ""
                cur.execute(
                    "SELECT no_pessoa, tp_papel, qt_mencoes FROM investigacao.ipl_tb03_pessoas "
                    "WHERE id_ipl = %s ORDER BY qt_mencoes DESC NULLS LAST LIMIT 15", (id_fonte,))
                det = [f"{l[0]} ({l[1]}, {l[2]}x)" for l in cur.fetchall()]
                return f"IPL {row[0] or id_fonte} ({row[1] or '?'} folhas). Pessoas: " + "; ".join(det)
            if tp == "irpf":
                cur.execute(
                    "SELECT no_titular, nu_exercicio, vl_rendimentos_tributaveis, vl_bens_ano_atual "
                    "FROM investigacao.irp_tb01_declaracoes WHERE id_irpf = %s AND id_inquerito = %s",
                    (id_fonte, id_inquerito))
                row = cur.fetchone()
                return f"IRPF {row[0] if row else id_fonte}: exercício {row[1] if row else '?'} " \
                    f"rendimentos {row[2] if row else '?'} bens {row[3] if row else '?'}." if row else ""
            if tp == "ipei":
                cur.execute(
                    "SELECT nu_ipei FROM investigacao.ipe_tb01_ipeis WHERE id_ipei = %s AND id_inquerito = %s",
                    (id_fonte, id_inquerito))
                row = cur.fetchone()
                if not row:
                    return ""
                cur.execute(
                    "SELECT a.no_alvo, n.nu_ano, n.vl_rend_total, n.vl_mov_creditos, n.vl_razao_mov_rend "
                    "FROM investigacao.ipe_tb04_anos n "
                    "JOIN investigacao.ipe_tb02_alvos a ON a.id_alvo = n.id_alvo "
                    "WHERE n.id_ipei = %s ORDER BY n.vl_razao_mov_rend DESC NULLS LAST LIMIT 12",
                    (id_fonte,))
                det = [f"{l[0]} {l[1]}: rend {l[2]} mov {l[3]} razão {l[4]}" for l in cur.fetchall()]
                return f"IPEI {row[0] or id_fonte}. Anos: " + "; ".join(det)
            if tp == "telefonia":
                cur.execute(
                    "SELECT no_operadora, qt_linhas, qt_chamadas, qt_numeros "
                    "FROM investigacao.tel_tb01_telefonia "
                    "WHERE id_telefonia = %s AND id_inquerito = %s", (id_fonte, id_inquerito))
                row = cur.fetchone()
                if not row:
                    return ""
                cur.execute(
                    """
                    SELECT n.nu_telefone,
                           (SELECT count(*) FROM investigacao.tel_tb04_chamadas c
                             WHERE c.id_telefonia = n.id_telefonia
                               AND (c.nu_chamador = n.nu_telefone OR c.nu_chamado = n.nu_telefone)) AS qt
                    FROM investigacao.tel_tb06_numeros n
                    WHERE n.id_telefonia = %s
                    ORDER BY qt DESC, n.nu_telefone LIMIT 12
                    """,
                    (id_fonte,))
                det = [f"{l[0]} ({l[1]} chamadas)" for l in cur.fetchall() if l[0]]
                return (f"Telefonia {row[0] or id_fonte}: {row[1] or 0} linhas, "
                        f"{row[2] or 0} chamadas, {row[3] or 0} números. "
                        f"Top interlocutores: " + ("; ".join(det) or "—"))
            if tp == "iped":
                cur.execute(
                    "SELECT no_conta, qt_conversas, qt_mensagens, qt_emails "
                    "FROM investigacao.ipd_tb01_extracoes WHERE id_extracao_iped = %s AND id_inquerito = %s",
                    (id_fonte, id_inquerito))
                row = cur.fetchone()
                return f"Nuvem IPED {row[0] if row else id_fonte}: {row[1] if row else '?'} conversas, " \
                    f"{row[2] if row else '?'} mensagens, {row[3] if row else '?'} e-mails." if row else ""
    return ""


def _ids_fontes(id_inquerito: int, tp: str) -> list[int]:
    mapa = {
        "rif": ("investigacao.rif_tb01_rifs", "id_rif"),
        "simba": ("investigacao.smb_tb01_extratos", "id_extrato"),
        "ipl": ("investigacao.ipl_tb01_inqueritos_pdf", "id_ipl"),
        "irpf": ("investigacao.irp_tb01_declaracoes", "id_irpf"),
        "ipei": ("investigacao.ipe_tb01_ipeis", "id_ipei"),
        "telefonia": ("investigacao.tel_tb01_telefonia", "id_telefonia"),
        "iped": ("investigacao.ipd_tb01_extracoes", "id_extracao_iped"),
    }
    tabela, coluna = mapa[tp]
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT {coluna} FROM {tabela} WHERE id_inquerito = %s ORDER BY {coluna}",
                        (id_inquerito,))
            return [r[0] for r in cur.fetchall()]


def _texto_documentos(id_inquerito: int) -> str:
    from funcoes.ia_investigacao import listar_documentos_inquerito
    try:
        docs = listar_documentos_inquerito(id_inquerito)
    except Exception:
        return ""
    partes = []
    for d in docs:
        if d.get("in_ipl") or d.get("in_rif") or d.get("in_simba") or d.get("in_irpf") \
                or d.get("in_ipei") or d.get("in_telefonia"):
            continue  # já tem bloco próprio; aqui só as peças genéricas
        if d.get("tx_resumo_ia"):
            partes.append(f"• [doc:{d['id_documento']}] {d.get('no_arquivo')}: {d['tx_resumo_ia']}")
        elif d.get("tx_erro"):
            partes.append(f"• {d.get('no_arquivo')}: sem resumo IA (erro de extração).")
        else:
            partes.append(f"• {d.get('no_arquivo')}: sem resumo IA ainda (análise pendente).")
    return "\n".join(partes)[:MAX_CHARS_FONTE_FINANCEIRA]


# =====================================================================
# Gravação
# =====================================================================

def _gravar_linha(id_inquerito: int, tp_fonte: str, tx_resumo: str,
                  jq_conversas=None, id_extracao: int | None = None,
                  id_fonte: int | None = None, modelo: str | None = None,
                  auditoria=None, hash_entrada: str | None = None,
                  status: str = "concluido", erro: str | None = None) -> None:
    tok_in = getattr(auditoria, "qt_tokens_entrada", None) or None
    tok_out = getattr(auditoria, "qt_tokens_saida", None) or None
    custo = None
    try:
        if modelo and (tok_in or tok_out):
            from funcoes.ia_provedores import estimar_custo_usd
            custo = estimar_custo_usd(modelo, tok_in or 0, tok_out or 0)
    except Exception:
        custo = None
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO investigacao.inq_tb05_resumo_noturno
                    (id_inquerito, id_extracao, tp_fonte, id_fonte, tx_resumo, jq_conversas,
                     no_modelo, qt_tokens_entrada, qt_tokens_saida, vl_custo_usd,
                     tx_hash_entrada, cd_status, tx_erro, dt_fim)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                """,
                (id_inquerito, id_extracao, tp_fonte, id_fonte, tx_resumo,
                 json.dumps(jq_conversas, ensure_ascii=False) if jq_conversas else None,
                 modelo, tok_in, tok_out, custo,
                 hash_entrada, status, erro),
            )
        conn.commit()


# =====================================================================
# Passe por inquérito
# =====================================================================

def gerar_resumo_inquerito(id_inquerito: int, id_usuario: int | None = None,
                           forcar: bool = False) -> dict:
    """Roda o passe completo num inquérito. Devolve {"status": gerado|pulso|sem_material|erro}.

    Idempotente por hash: sem mudança e sem forcar, não chama a IA."""
    if not _tabela_existe():
        return {"status": "erro", "motivo": "migração 0020 pendente — rode as migrações."}
    if not _tem_extracao_concluida(id_inquerito):
        return {"status": "sem_material"}
    hash_entrada = calcular_hash_entrada(id_inquerito)
    if not forcar:
        atual = obter_resumo_inquerito(id_inquerito)
        if atual.get("tem") and atual.get("consolidado", {}).get("tx_hash_entrada") == hash_entrada \
                and not atual.get("desatualizado"):
            return {"status": "pulso", "motivo": "nada mudou desde o último passe"}
    if not resumo_noturno_configurado(id_usuario):
        _gravar_linha(id_inquerito, "consolidado", "", hash_entrada=hash_entrada,
                      status="erro", erro="Nenhum provedor de IA configurado (RESUMO_NOTURNO_*/AI_*).")
        return {"status": "erro", "motivo": "IA não configurada"}

    parciais_tx: list[str] = []
    try:
        # 1. por celular
        for ext in _extracoes_concluidas(id_inquerito):
            texto = _texto_celular(id_inquerito, ext)
            inicio = time.monotonic()
            try:
                resp, modelo, aud = _chamar_ia(
                    _PROMPT_CELULAR,
                    f"INQUÉRITO {id_inquerito}. {texto}",
                    id_usuario)
                _auditar("resumo_noturno", texto[-2000:], _PROMPT_CELULAR, aud, modelo,
                         inicio, resp, id_inquerito, id_usuario)
                dados = _extrair_json(resp)
                tx, convs = _resumo_legivel("celular", dados, ext.get("no_arquivo") or "")
            except Exception as exc:
                logger.exception("resumo_noturno: celular %s", ext["id_extracao"])
                _gravar_linha(id_inquerito, "celular", "", id_extracao=ext["id_extracao"],
                              modelo=None, hash_entrada=hash_entrada, status="erro",
                              erro=str(exc)[:2000])
                continue
            _gravar_linha(id_inquerito, "celular", tx, convs, id_extracao=ext["id_extracao"],
                          modelo=modelo, auditoria=aud, hash_entrada=hash_entrada)
            nome = ext.get("no_dispositivo") or (ext.get("no_arquivo") or "").split("/")[-1]
            parciais_tx.append(f"[celular {ext['id_extracao']} — {nome}]\n{tx}")

        # 2. demais arquivos
        for tp in _TP_FONTES_ARQUIVO:
            try:
                ids = _ids_fontes(id_inquerito, tp)
            except Exception:
                continue
            for fid in ids:
                try:
                    if tp == "rif":
                        texto = _texto_rif(id_inquerito, fid)
                    elif tp == "simba":
                        texto = _texto_simba(id_inquerito, fid)
                    else:
                        texto = _texto_generico_fonte(tp, id_inquerito, fid)
                except Exception:
                    logger.exception("resumo_noturno: agregado %s %s", tp, fid)
                    continue
                if not (texto or "").strip():
                    continue
                inicio = time.monotonic()
                try:
                    resp, modelo, aud = _chamar_ia(
                        _PROMPT_FINANCEIRO,
                        f"INQUÉRITO {id_inquerito}. Fonte {tp.upper()} id {fid}.\n\n{texto}",
                        id_usuario)
                    _auditar("resumo_noturno", texto[-2000:], _PROMPT_FINANCEIRO, aud, modelo,
                             inicio, resp, id_inquerito, id_usuario)
                    dados = _extrair_json(resp)
                    tx, _ = _resumo_legivel(tp, dados, f"{tp} {fid}")
                except Exception as exc:
                    logger.exception("resumo_noturno: fonte %s %s", tp, fid)
                    _gravar_linha(id_inquerito, tp, "", id_fonte=fid,
                                  hash_entrada=hash_entrada, status="erro",
                                  erro=str(exc)[:2000])
                    continue
                _gravar_linha(id_inquerito, tp, tx, id_fonte=fid, modelo=modelo,
                              auditoria=aud, hash_entrada=hash_entrada)
                parciais_tx.append(f"[{tp.upper()} {fid}]\n{tx}")

        # 3. peças genéricas (sem IA nova)
        try:
            tx_docs = _texto_documentos(id_inquerito)
        except Exception:
            tx_docs = ""
        if (tx_docs or "").strip():
            _gravar_linha(id_inquerito, "documentos", tx_docs, hash_entrada=hash_entrada,
                          modelo=None)
            parciais_tx.append(f"[DOCUMENTOS]\n{tx_docs}")

        # 4. síntese
        entrada = "\n\n".join(parciais_tx)[: (
            _limite_chars_fonte("RESUMO_NOTURNO_MAX_CHARS_SINTESE") or MAX_CHARS_SINTESE_ENTRADA)]
        if not entrada.strip():
            _gravar_linha(id_inquerito, "consolidado", "", hash_entrada=hash_entrada,
                          status="erro", erro="Nada para resumir (fontes vazias).")
            return {"status": "erro", "motivo": "fontes vazias"}
        inicio = time.monotonic()
        resp, modelo, aud = _chamar_ia(
            _PROMPT_SINTESE, f"INQUÉRITO {id_inquerito}.\n\n{entrada}", id_usuario)
        _auditar("resumo_noturno", entrada[-2000:], _PROMPT_SINTESE, aud, modelo,
                 inicio, resp, id_inquerito, id_usuario)
        dados = _extrair_json(resp)
        tx, _ = _resumo_legivel("consolidado", dados, "")
        _gravar_linha(id_inquerito, "consolidado", tx, modelo=modelo,
                      auditoria=aud, hash_entrada=hash_entrada)
        return {"status": "gerado"}
    except Exception as exc:
        logger.exception("resumo_noturno: falha no inquérito %s", id_inquerito)
        try:
            _gravar_linha(id_inquerito, "consolidado", "", hash_entrada=hash_entrada,
                          status="erro", erro=str(exc)[:2000])
        except Exception:
            pass
        return {"status": "erro", "motivo": str(exc)[:500]}


def processar_todos(id_inquerito: int | None = None, id_usuario: int | None = None,
                    forcar: bool = False) -> dict:
    """Passe da madrugada: todos (ou um) os inquéritos com extração concluída."""
    resultados: dict = {}
    for iid in listar_inqueritos_para_passe(id_inquerito):
        try:
            resultados[iid] = gerar_resumo_inquerito(iid, id_usuario, forcar)
        except Exception as exc:  # um inquérito nunca derruba o passe inteiro
            logger.exception("resumo_noturno: inquérito %s", iid)
            resultados[iid] = {"status": "erro", "motivo": str(exc)[:500]}
    return resultados

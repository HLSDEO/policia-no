from fastapi import APIRouter, Cookie, Depends, HTTPException
from psycopg import errors as psycopg_errors
from pydantic import BaseModel, Field

from funcoes.auth_utils import exigir_admin, validar_sessao
from funcoes.db_utils import get_connection
from funcoes.unidades_utils import uf_efetiva_da_unidade

router = APIRouter()
router_admin = APIRouter(dependencies=[Depends(exigir_admin)])


class DelegaciaIn(BaseModel):
    no_delegacia: str = Field(..., min_length=1, max_length=300)
    uf: str | None = Field(default=None, max_length=2)
    # None = não mexe (toggle da tabela manda só o campo alterado). Sim/Não = liga/desliga.
    # Antes in_ia_ativa tinha default True, então um PUT que alterava só o nó sem mandar a IA
    # resetava a IA para True — era o bug de "um toggle sobrescreve o outro" da tela.
    in_ia_ativa: bool | None = None
    # None = não mexe (a tela de usuários salva só o nome). Sim/Não = ligar/desligar o nó da
    # delegacia: desligado, upload/seletor/extração voltam a ser 100 % locais sem descadastrar
    # o nó (ver docs/no_descentralizado.md).
    in_no_ativa: bool | None = None
    # Pai na árvore de unidades superiores (ver docs/unidades_prompts.md). None = não mexe, igual
    # aos toggles; para DESVINCULAR o modal manda limpar_unidade = true junto (não dá para
    # distinguir "sem unidade" de "campo não enviado" com None sozinho).
    id_unidade: int | None = None
    limpar_unidade: bool = False


def _uf_bruta(uf: str | None) -> str | None:
    """UF sempre em maiúsculas de 2 letras; vazio = NULL (ainda não informada)."""
    if uf is None:
        return None
    bruta = uf.strip().upper()
    if not bruta:
        return None
    if len(bruta) != 2 or not bruta.isalpha():
        raise HTTPException(status_code=400, detail="UF deve ter 2 letras (ex.: SP).")
    return bruta


def _conferir_unidade(cur, id_unidade: int | None) -> None:
    """A unidade precisa existir antes de amarrar a delegacia a ela — o FK daria 500 num id
    digitado errado no PUT; aqui vira 400 legível (ver rotas/unidades.py)."""
    if id_unidade is None:
        return
    cur.execute("SELECT 1 FROM seguranca.unidades WHERE id_unidade = %s", (id_unidade,))
    if cur.fetchone() is None:
        raise HTTPException(status_code=400, detail="Unidade superior não encontrada.")


# Nenhuma delegacia fica sem unidade superior. Sem escolha explícita no formulário, a
# unidade vem do padrão institucional SR/PF/<UF> (ex.: SR/PF/ES para a UF ES), criado na hora
# se ainda não existir (tp_nivel 'uf' — a superintendência do estado).
SEM_UNIDADE_MSG = ("Nenhuma delegacia pode ficar sem unidade superior. Escolha a unidade "
                   "(padrão SR/PF/UF — ex.: SR/PF/ES) ou informe a UF para criar a padrão.")


def _garantir_unidade_padrao(cur, uf: str | None) -> int | None:
    """id da unidade SR/PF/<UF> da UF — criando-a se necessário. None se não há UF."""
    if not uf:
        return None
    nome = f"SR/PF/{uf}"
    cur.execute("INSERT INTO seguranca.unidades (no_unidade, tp_nivel, uf) "
                "VALUES (%s, 'uf', %s) ON CONFLICT (no_unidade) DO NOTHING", (nome, uf))
    cur.execute("SELECT id_unidade FROM seguranca.unidades WHERE no_unidade = %s", (nome,))
    row = cur.fetchone()
    return row[0] if row else None


@router.get("/api/delegacias")
def listar(analyser_sessao: str | None = Cookie(default=None)):
    """Lista as delegacias disponíveis (usado no cadastro e na edição de usuários).

    Continua acessível sem login por causa do autocadastro — mas anônimo recebe só
    id/nome/UF. Os flags de postura (IA ativa, nó ativo), a unidade e a data de criação
    só saem para quem tem sessão válida (a tela Administração usa este mesmo endpoint).
    Ver A8."""
    logado = validar_sessao(analyser_sessao) is not None
    with get_connection() as conn:
        with conn.cursor() as cur:
            if logado:
                cur.execute(
                    "SELECT id_delegacia, no_delegacia, uf, in_ia_ativa, in_no_ativa, id_unidade, "
                    "dt_criacao FROM seguranca.delegacias ORDER BY no_delegacia"
                )
            else:
                cur.execute(
                    "SELECT id_delegacia, no_delegacia, uf FROM seguranca.delegacias "
                    "ORDER BY no_delegacia"
                )
            colunas = [d.name for d in cur.description]
            return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


@router_admin.post("/api/delegacias")
def criar(payload: DelegaciaIn):
    nome = payload.no_delegacia.strip().upper()
    if not nome:
        raise HTTPException(status_code=400, detail="Nome da delegacia é obrigatório.")
    uf = _uf_bruta(payload.uf)  # fora do try: o 400 da validação não pode virar 409 aqui embaixo
    with get_connection() as conn:
        with conn.cursor() as cur:
            _conferir_unidade(cur, payload.id_unidade)
            # sem escolha explícita, amarra na unidade padrão da UF (SR/PF/UF) — criada na hora
            id_unidade = payload.id_unidade
            if id_unidade is None:
                id_unidade = _garantir_unidade_padrao(cur, uf)
                if id_unidade is None:
                    raise HTTPException(status_code=400, detail=SEM_UNIDADE_MSG)
            # com unidade superior, a UF é da árvore, não do formulário (docs/unidades_prompts.md)
            uf = uf_efetiva_da_unidade(cur, id_unidade) or uf
            try:
                cur.execute(
                    "INSERT INTO seguranca.delegacias (no_delegacia, uf, in_ia_ativa, in_no_ativa, "
                    "id_unidade) VALUES (%s, %s, COALESCE(%s, TRUE), COALESCE(%s, TRUE), %s) "
                    "RETURNING id_delegacia",
                    (nome, uf, payload.in_ia_ativa, payload.in_no_ativa, id_unidade),
                )
                id_delegacia = cur.fetchone()[0]
            except psycopg_errors.UniqueViolation:
                raise HTTPException(status_code=409, detail="Delegacia já cadastrada.")
        conn.commit()
    return {"ok": True, "id_delegacia": id_delegacia}


@router_admin.put("/api/delegacias/{id_delegacia}")
def atualizar(id_delegacia: int, payload: DelegaciaIn):
    nome = payload.no_delegacia.strip().upper()
    if not nome:
        raise HTTPException(status_code=400, detail="Nome da delegacia é obrigatório.")
    with get_connection() as conn:
        with conn.cursor() as cur:
            _conferir_unidade(cur, payload.id_unidade)
            cur.execute(
                "SELECT uf, id_unidade FROM seguranca.delegacias WHERE id_delegacia = %s",
                (id_delegacia,),
            )
            atual = cur.fetchone()
            if atual is None:
                raise HTTPException(status_code=404, detail="Delegacia não encontrada.")
            uf = _uf_bruta(payload.uf) or atual[0]
            # unidade efetiva depois deste PUT: ninguém desacopla a delegacia da árvore — no
            # máximo TROCA de unidade. Sem unidade (legado ou PUT de toggle antigo), entra a
            # padrão da UF (SR/PF/UF), criada na hora se preciso.
            if payload.limpar_unidade:
                raise HTTPException(status_code=400,
                                    detail="Nenhuma delegacia pode ficar sem unidade superior — "
                                           "troque de unidade em vez de remover.")
            unidade_efetiva = payload.id_unidade if payload.id_unidade is not None else atual[1]
            if unidade_efetiva is None:
                unidade_efetiva = _garantir_unidade_padrao(cur, uf)
                if unidade_efetiva is None:
                    raise HTTPException(status_code=400, detail=SEM_UNIDADE_MSG)
            # e com unidade, a UF passa a ser a da árvore
            uf = uf_efetiva_da_unidade(cur, unidade_efetiva) or uf
            cur.execute(
                "UPDATE seguranca.delegacias SET no_delegacia = %s, "
                "in_ia_ativa = COALESCE(%s, in_ia_ativa), "
                "in_no_ativa = COALESCE(%s, in_no_ativa), uf = %s, "
                "id_unidade = %s "
                "WHERE id_delegacia = %s",
                (nome, payload.in_ia_ativa, payload.in_no_ativa, uf,
                 unidade_efetiva, id_delegacia),
            )
        conn.commit()
    return {"ok": True}


@router_admin.delete("/api/delegacias/{id_delegacia}")
def excluir(id_delegacia: int):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE seguranca.usr_tb01_usuarios SET id_delegacia = NULL WHERE id_delegacia = %s",
                (id_delegacia,),
            )
            # prompt e IA própria da delegacia vão junto: ID_ENTIDADE é polimórfico e não tem FK,
            # então a limpeza é aqui — mesma regra da exclusão de unidade (rotas/unidades.py)
            cur.execute(
                "DELETE FROM seguranca.prompts_ia WHERE tp_nivel = 'delegacia' AND id_entidade = %s",
                (id_delegacia,),
            )
            cur.execute(
                "DELETE FROM seguranca.config_ia_entidade WHERE tp_nivel = 'delegacia' AND id_entidade = %s",
                (id_delegacia,),
            )
            cur.execute(
                "DELETE FROM seguranca.delegacias WHERE id_delegacia = %s",
                (id_delegacia,),
            )
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="Delegacia não encontrada.")
        conn.commit()
    return {"ok": True}

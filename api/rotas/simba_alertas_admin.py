"""Rota de configuração administrativa: pesos dos alertas do extrato bancário SIMBA, salvos no
.env (mesmo padrão de configuracao_admin.py) — não são limites legais, então ficam ajustáveis em
vez de fixos no código. Ver funcoes/simba_utils.py (PESOS_ALERTA_SIMBA_PADRAO/pesos_alerta_simba)."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from funcoes.auth_utils import exigir_admin
from funcoes.env_utils import read_env_text, read_modify_write_env, upsert_env_values, write_env_text
from funcoes.simba_utils import PESOS_ALERTA_SIMBA_PADRAO, pesos_alerta_simba

router = APIRouter(dependencies=[Depends(exigir_admin)])


class PesosAlertaSimbaIn(BaseModel):
    """Corpo aceito pelo POST de configuração dos alertas SIMBA."""

    limiteComunicacao: int = Field(..., ge=1, le=100_000_000)
    minFracionamento: int = Field(..., ge=2, le=1000)
    multiploRedondo: int = Field(..., ge=1, le=1_000_000)
    giroPassagem: int = Field(..., ge=1, le=1_000_000_000)
    toleranciaPassagemPct: int = Field(..., ge=1, le=50)
    minLancamentosPassagem: int = Field(..., ge=2, le=100000)


class PesosAlertaSimbaOut(BaseModel):
    """Resposta do GET — os mesmos pesos, já com o padrão de fábrica aplicado quando ausentes."""

    limiteComunicacao: int
    minFracionamento: int
    multiploRedondo: int
    giroPassagem: int
    toleranciaPassagemPct: int
    minLancamentosPassagem: int
    padrao: dict


@router.get("/api/config/simba-alertas", response_model=PesosAlertaSimbaOut)
def get_pesos_alerta_simba():
    """Lê os pesos atuais dos alertas do SIMBA (do .env, com o padrão de fábrica quando ausente)."""
    pesos = pesos_alerta_simba()
    return PesosAlertaSimbaOut(
        limiteComunicacao=pesos["limite_comunicacao"],
        minFracionamento=pesos["min_fracionamento"],
        multiploRedondo=pesos["multiplo_redondo"],
        giroPassagem=pesos["giro_passagem"],
        toleranciaPassagemPct=pesos["tolerancia_passagem_pct"],
        minLancamentosPassagem=pesos["min_lancamentos_passagem"],
        padrao=PESOS_ALERTA_SIMBA_PADRAO,
    )


@router.post("/api/config/simba-alertas")
def salvar_pesos_alerta_simba(payload: PesosAlertaSimbaIn):
    """Grava os pesos no .env — o efeito aparece na próxima consulta de alertas, sem reiniciar."""
    updates = {
        "SIMBA_ALERTA_LIMITE_COMUNICACAO": str(payload.limiteComunicacao),
        "SIMBA_ALERTA_MIN_FRACIONAMENTO": str(payload.minFracionamento),
        "SIMBA_ALERTA_MULTIPLO_REDONDO": str(payload.multiploRedondo),
        "SIMBA_ALERTA_GIRO_PASSAGEM": str(payload.giroPassagem),
        "SIMBA_ALERTA_TOLERANCIA_PASSAGEM": str(payload.toleranciaPassagemPct),
        "SIMBA_ALERTA_MIN_LANC_PASSAGEM": str(payload.minLancamentosPassagem),
    }
    read_modify_write_env(updates)  # atômico: não perde write concorrente (ver C9)
    return {"ok": True}

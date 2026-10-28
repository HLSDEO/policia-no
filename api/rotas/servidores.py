"""Administração → Servidores: registro dos nós descentralizados (ver docs/no_descentralizado.md).

CRUD clássico com uma particularidade: o token. Ele é GERADO aqui, mostrado UMA vez na resposta de
criação/troca (o instalar-host do nó cola no .env), e nunca volta numa listagem — por isso o
tx_token é removido antes de qualquer dict sair daqui. Revogar = trocar o token: o nó para de
autenticar nos dois sentidos na hora, sem reiniciar o central.

O DELETE não apaga o histórico: extrações/jobs guardam o id do nó SEM foreign key (mesmo padrão de
ID_EXTRACAO_ORIGEM em 09_extracoes.sql) — as linhas antigas ficam apontando para um nó que não
existe mais, e os fluxos remotos tratam isso como "arquivo indisponível".
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from funcoes import no_utils
from funcoes.auth_utils import exigir_admin

router = APIRouter(dependencies=[Depends(exigir_admin)])


class ServidorIn(BaseModel):
    no_servidor: str = Field(..., min_length=1, max_length=100)
    # Um nó atende N delegacias (UX110 no vínculo trava o contrário: cada delegacia em um só nó)
    ids_delegacia: list[int] = Field(..., min_length=1)
    tx_url_base: str = Field(..., min_length=8, max_length=500)


class ServidorEdicaoIn(BaseModel):
    tx_url_base: str | None = Field(default=None, max_length=500)
    in_ativa: bool | None = None
    # None = não mexe no vínculo; lista = passa a ser ESTAS as delegacias atendidas
    ids_delegacia: list[int] | None = Field(default=None, min_length=1)
    # Mídia direta (docs/no_midia_direta.md): None = não mexe; string vazia limpa a URL
    tx_midia_url: str | None = Field(default=None, max_length=500)
    in_midia_direta: bool | None = None


def _sem_token(d: dict) -> dict:
    d.pop("tx_token", None)
    return d


@router.get("/api/servidores")
def listar():
    """Todos os nós com online (batimento < 3 min) e a versão reportada. O token fica de fora."""
    return [_sem_token(d) for d in no_utils.listar()]


@router.post("/api/servidores")
def criar(payload: ServidorIn):
    try:
        id_servidor, token = no_utils.criar(payload.no_servidor, payload.ids_delegacia,
                                            payload.tx_url_base)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "id_servidor": id_servidor, "token": token,
            "aviso": "Guarde o token agora: ele não aparece de novo. É o valor de NO_TOKEN no "
                     ".env do nó."}


@router.put("/api/servidores/{id_servidor}")
def atualizar(id_servidor: int, payload: ServidorEdicaoIn):
    try:
        no_utils.atualizar(id_servidor, url_base=payload.tx_url_base, in_ativa=payload.in_ativa,
                           ids_delegacia=payload.ids_delegacia,
                           midia_url=payload.tx_midia_url, midia_ativa=payload.in_midia_direta)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@router.post("/api/servidores/{id_servidor}/token")
def trocar_token(id_servidor: int):
    try:
        token = no_utils.trocar_token(id_servidor)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True, "token": token,
            "aviso": "Atualize NO_TOKEN no .env do nó e reinicie o container dele — até lá o "
                     "nó não autentica."}


@router.delete("/api/servidores/{id_servidor}")
def excluir(id_servidor: int):
    try:
        no_utils.excluir(id_servidor)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True}


@router.post("/api/servidores/{id_servidor}/testar")
def testar(id_servidor: int):
    """O central bate no /no/saude do nó e diz o que viu — o botão "Testar" da tela, que responde
    a pergunta mais frequente do instalador ("a rede entre os dois está certa?")."""
    no = no_utils.obter(id_servidor)
    if no is None:
        raise HTTPException(status_code=404, detail="Servidor não encontrado.")
    try:
        import requests

        res = requests.get(f"{no['tx_url_base'].rstrip('/')}/no/saude", timeout=(5, 5))
        dados = res.json() if res.status_code == 200 else None
    except Exception as exc:
        return {"ok": False, "detail": f"Sem resposta do nó: {exc}"}
    if not dados or not dados.get("ok"):
        return {"ok": False, "detail": f"Nó respondeu {res.status_code}.", "resposta": dados}
    try:
        from funcoes import versao_utils
        versao_central = versao_utils.versao_instalada()
    except Exception:
        versao_central = None
    return {"ok": True, "versao_no": dados.get("versao"), "versao_central": versao_central,
            "mesma_versao": (dados.get("versao") == versao_central)}


@router.post("/api/servidores/{id_servidor}/testar-midia")
def testar_midia(id_servidor: int):
    """Botão "Testar mídia" (docs/no_midia_direta.md): o central finge ser o navegador e bate no
    /no/saude do no-web por HTTPS. Não prova que a ESTAÇÃO alcança a porta 8443 (o central enxerga
    outra rota), mas prova o tripé que o admin controla: o no-web de pé, o certificado válido e o
    DNS resolvendo — os erros de DNS/certificado/porta aparecem aqui como mensagem do requests."""
    no = no_utils.obter(id_servidor)
    if no is None:
        raise HTTPException(status_code=404, detail="Servidor não encontrado.")
    base = (no.get("tx_midia_url") or "").strip()
    if not base:
        return {"ok": False, "detail": "Sem URL de mídia configurada — preencha antes de testar "
                                       "(checklist da TI no docs/no_midia_direta.md)."}
    import requests

    # Instalações sem certificado da CA da PF usam a CA própria do Analiser (runbook "sem CGTI"
    # do docs/no_midia_direta.md): o par CA está montado em /app/certs-nos pelo compose e é a
    # âncora de verificação quando existe. Sem o arquivo, segue a confiança do sistema — o
    # navegador da estação tem exatamente a mesma escolha (importar o ca-analyser.crt ou confiar
    # na CA da PF), então o que este botão diz é o que a estação vai ver.
    import os

    ca_no = "/app/certs-nos/ca-analyser.crt"
    ancoragem = ca_no if os.path.exists(ca_no) else True

    try:
        # verify padrão (CA do sistema do host): se a CA interna da PF não estiver instalada no
        # host, o erro "unable to get local issuer certificate" é exatamente o aviso que o
        # navegador daria — não pulem a verificação aqui "para funcionar"
        res = requests.get(f"{base.rstrip('/')}/no/saude", timeout=(5, 5), verify=ancoragem)
        dados = res.json() if res.status_code == 200 else None
    except requests.exceptions.SSLError as exc:
        return {"ok": False, "detail": f"TLS recusado: {exc}"}
    except Exception as exc:
        return {"ok": False, "detail": f"Sem resposta do no-web: {exc}"}
    if not dados or not dados.get("ok"):
        return {"ok": False, "detail": f"no-web respondeu {res.status_code}.", "resposta": dados}
    return {"ok": True, "versao_no": dados.get("versao"),
            "detalhe": "no-web respondendo por HTTPS — falta só a confirmação de uma estação da "
                       "LAN (o navegador sondará o mesmo endereço)."}


@router.post("/api/servidores/verificar")
def verificar():
    """Botão "Verificar" da tela: força o host a consultar o GitHub e devolve o quadro completo —
    versão do central, release mais novo, e QUAIS nós estão atrás dele. É a resposta à mensagem do
    nó ("clique em Verificar no central"), que até então não apontava para nada na tela."""
    from funcoes import versao_utils

    d = versao_utils.checar_atualizacao(forcar=True)
    atrasados: list[str] = []
    alvo = str(d.get("versao_disponivel") or "").lstrip("v")
    if alvo:
        for no in no_utils.listar():
            if not no.get("in_ativa"):
                continue
            atual = str(no.get("tx_versao") or "").lstrip("v")
            try:
                if not atual or versao_utils.como_tupla(atual) < versao_utils.como_tupla(alvo):
                    atrasados.append(no["no_servidor"])
            except ValueError:
                atrasados.append(no["no_servidor"])  # versão ilegível no heartbeat também é atraso
    return {
        "central": d.get("instalada"),
        "versao_disponivel": alvo or None,
        "publicada_em": d.get("publicada_em"),
        "url": d.get("url"),
        "erro": d.get("erro"),
        "nos_atrasados": atrasados,
    }


@router.post("/api/servidores/{id_servidor}/atualizar")
def atualizar_no(id_servidor: int):
    """Botão "Atualizar" da tela: repassa ao nó o POST /no/atualizar — o nó grava o pedido.json
    no seu .deploy/controle e o agente do host dele faz o resto (release, build, up, health).
    O central não baixa nem empurra nada: quem se atualiza é o próprio nó, com o token dele.

    A VERSÃO-ALVO vem do central: é ele quem tem internet e consulta o GitHub — o disponivel.json
    do nó é velho por natureza (o check do host roda 4×/dia) e usar o alvo de lá recusaria o
    pedido com "X não é mais nova que a instalada" mesmo havendo release novo.
    """
    no = no_utils.obter(id_servidor)
    if no is None:
        raise HTTPException(status_code=404, detail="Servidor não encontrado.")
    def _alvo() -> str:
        # forcar=True: o disponivel.json em cache pode ter dias — o botão precisa do release
        # MAIS NOVO de verdade (o host consulta o GitHub e reescreve o arquivo, ~10 s).
        from funcoes import versao_utils

        d = versao_utils.checar_atualizacao(forcar=True)
        return str(d.get("versao_disponivel") or "").lstrip("v")

    try:
        alvo = _alvo()
    except Exception:
        alvo = ""  # sem alvo explícito: o nó decide pelo disponivel.json dele (fluxo antigo)
    from funcoes import no_ponte

    def _dispara(alvo_atual: str) -> dict:
        res = no_ponte.atualizar_no(no, alvo_atual or None)
        if res.status_code != 200:
            raise HTTPException(status_code=502,
                                detail=f"O nó respondeu {res.status_code}: {res.text[:200]}")
        return res.json()

    try:
        resposta = _dispara(alvo)
    except no_ponte.NoIndisponivel as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    # O host do central pode devolver o disponivel.json de UMA CARGA SÓ atrás do push (o check
    # forçado tem pressa). Se o nó recusou por "já está em dia" com esse alvo velho, refazer o
    # check e tentar de novo com o alvo fresco vale mais que o operador clicar duas vezes.
    if (not resposta.get("aceito") and "já está em dia" in str(resposta.get("erro", ""))):
        try:
            alvo2 = _alvo()
            if alvo2 and alvo2 != alvo:
                resposta = _dispara(alvo2)
        except Exception:
            pass
    return resposta


@router.get("/api/servidores/{id_servidor}/atualizacao")
def atualizacao_no(id_servidor: int):
    """Log/resultado da atualização em curso no nó (repassa o GET /no/atualizacao). Durante o
    build/restart o nó fica fora do ar de propósito — 502 aqui é esperado, a tela tolera."""
    no = no_utils.obter(id_servidor)
    if no is None:
        raise HTTPException(status_code=404, detail="Servidor não encontrado.")
    from funcoes import no_ponte

    try:
        res = no_ponte.atualizacao_estado(no)
    except no_ponte.NoIndisponivel as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if res.status_code != 200:
        raise HTTPException(status_code=502,
                            detail=f"O nó respondeu {res.status_code}: {res.text[:200]}")
    return res.json()


class ConfigNoIn(BaseModel):
    valores: dict[str, str | int] = Field(..., min_length=1)
    # True (padrão): se a mudança exigir recriar o container, já emenda o POST /no/reiniciar
    reiniciar: bool = True


@router.get("/api/servidores/{id_servidor}/config")
def config_no(id_servidor: int):
    """Desempenho DO NÓ (repassa o GET /no/config): o que está no .env da estação + a recomendação
    que o próprio nó calcula para o hardware dele. Não guardamos cópia no central de propósito —
    o .env do nó é a fonte, e lido ao vivo ele não mente sobre o que está em vigor."""
    no = no_utils.obter(id_servidor)
    if no is None:
        raise HTTPException(status_code=404, detail="Servidor não encontrado.")
    from funcoes import no_ponte

    try:
        res = no_ponte.config_ler(no)
    except no_ponte.NoIndisponivel as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if res.status_code != 200:
        raise HTTPException(status_code=502,
                            detail=f"O nó respondeu {res.status_code}: {res.text[:200]}")
    return res.json()


@router.post("/api/servidores/{id_servidor}/config")
def aplicar_config_no(id_servidor: int, payload: ConfigNoIn):
    """Grava as chaves no .env do nó (POST /no/config) e, quando a mudança exige recriar o
    container e `reiniciar` não foi desligado, emenda o POST /no/reiniciar — o nó fica offline
    ~30 s e volta sozinho (path unit + agente no host do nó)."""
    no = no_utils.obter(id_servidor)
    if no is None:
        raise HTTPException(status_code=404, detail="Servidor não encontrado.")
    cabecalho = {"Authorization": f"Bearer {no['tx_token']}"}
    from funcoes import no_ponte

    try:
        res = no_ponte.config_aplicar(no, payload.valores)
    except no_ponte.NoIndisponivel as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if res.status_code != 200:
        detalhe = res.text[:300]
        try:
            detalhe = (res.json() or {}).get("detail", detalhe)
        except ValueError:
            pass
        raise HTTPException(status_code=400 if res.status_code == 400 else 502, detail=detalhe)
    resultado = res.json()
    resultado["reiniciando"] = False
    if resultado.get("precisa_reiniciar") and payload.reiniciar:
        try:
            res = no_ponte.reiniciar(no)
            if res.status_code == 200 and (res.json() or {}).get("aceito"):
                resultado["reiniciando"] = True
            else:
                resultado["aviso"] = ("config gravada, mas o reinicio foi recusado: "
                                      f"{(res.json() or {}).get('erro') if res.status_code == 200 else res.status_code}")
        except Exception as exc:
            resultado["aviso"] = f"config gravada, mas o reinicio nao disparou: {exc}"
    return resultado


@router.post("/api/servidores/{id_servidor}/reiniciar")
def reiniciar_no(id_servidor: int):
    """Reinicia o container do nó (repassa o POST /no/reiniciar). É o botão "Reiniciar agora" do
    modal Desempenho quando a config aplicada exige recriar o container."""
    no = no_utils.obter(id_servidor)
    if no is None:
        raise HTTPException(status_code=404, detail="Servidor não encontrado.")
    from funcoes import no_ponte

    try:
        res = no_ponte.reiniciar(no)
    except no_ponte.NoIndisponivel as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if res.status_code != 200:
        raise HTTPException(status_code=502,
                            detail=f"O nó respondeu {res.status_code}: {res.text[:200]}")
    return res.json()


@router.get("/api/servidores/{id_servidor}/estado")
def estado_no(id_servidor: int):
    """Uso da máquina do nó (CPU/memória/disco/uptime) + fila local de extração — repassa o
    GET /no/estado. A tela usa para a coluna "Fila" e o modal "Estado"."""
    no = no_utils.obter(id_servidor)
    if no is None:
        raise HTTPException(status_code=404, detail="Servidor não encontrado.")
    from funcoes import no_ponte

    try:
        res = no_ponte.estado_maquina(no)
    except no_ponte.NoIndisponivel as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if res.status_code != 200:
        raise HTTPException(status_code=502,
                            detail=f"O nó respondeu {res.status_code}: {res.text[:200]}")
    return res.json()


# ------------------------------------------------------------------ rollout em levas
# (docs/no_descentralizado.md, "rollout de update"). Um clique atualiza a frota: o job global
# (tp 'rollout_nos', escopo 'nos') roda em thread na réplica que recebeu o POST e conduz levas
# de até max_parallel nós, confirmando cada volta pela versão reportada. O histórico/progresso/
# cancelamento são os da JOB_TB01 — a tela lee GET /api/servidores/atualizacao-todos.


class RolloutIn(BaseModel):
    max_parallel: int = Field(default=3, ge=1, le=10)


@router.post("/api/servidores/atualizar-todos")
def atualizar_todos(payload: RolloutIn, usuario: dict = Depends(exigir_admin)):
    from funcoes import jobs_no, jobs_utils

    try:
        id_job = jobs_utils.criar(
            jobs_utils.TP_ROLLOUT_NOS, usuario["id_usuario"], escopo="nos",
            pesado=False, parametros={"max_parallel": payload.max_parallel})
    except jobs_utils.JobOcupado as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    import threading

    threading.Thread(target=jobs_no.rollout_nos, args=(id_job, payload.max_parallel),
                     daemon=True, name=f"rollout-{id_job}").start()
    return {"ok": True, "id_job": id_job}


@router.get("/api/servidores/atualizacao-todos")
def atualizacao_todos_estado():
    """O rollout mais recente (em curso ou histórico) para o botão/painel da tela."""
    from funcoes import jobs_utils

    job = jobs_utils.ultimo(jobs_utils.TP_ROLLOUT_NOS, escopo="nos")
    return job or {"rodando": False, "cd_status": None}

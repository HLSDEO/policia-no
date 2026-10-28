"""Rotas do painel de ingestão de dados: contagem de registros por conjunto de dados e disparo dos imports.

Router de admin. A tela ("Administração -> Ingestão", web/js/utils/mapa_telas.js) sempre foi de
admin, mas as rotas só exigiam `exigir_aprovado`: qualquer analista aprovado, de qualquer
delegacia, disparava a reimportação global das bases de referência (Detran, TSE, IBGE, Serasa) —
uma carga que pesa no banco inteiro e afeta todo mundo ao mesmo tempo.
"""

from fastapi import APIRouter, Depends, HTTPException

from funcoes.auth_utils import exigir_admin
from funcoes.db_utils import count_table
from funcoes.import_utils import iniciar_import, status_job

router = APIRouter(dependencies=[Depends(exigir_admin)])

# Um grupo por conjunto de dados (card na tela de ingestão). "tabelas" lista o que é
# exibido/contado; "scripts" são os arquivos de db/import/ rodados, em ordem, pelo botão Importar.
GRUPOS = {
    "veiculos": {
        "label": "Veículos (Detran)",
        "scripts": ["01_import_referencia.sql", "02_import_detran.sql"],
        "tabelas": [
            ("DADOS_REFERENCIA", "VEI_TB01_MARCA", "Marca"),
            ("DADOS_REFERENCIA", "VEI_TB02_SEGMENTO", "Segmento"),
            ("DADOS_REFERENCIA", "VEI_TB03_SUB_SEGMENTO", "Sub-segmento"),
            ("DADOS_REFERENCIA", "VEI_TB04_GRUPO_MODELO", "Grupo de modelo"),
            ("DADOS_REFERENCIA", "VEI_TB05_MODELO", "Modelo"),
            ("DADOS_REFERENCIA", "VEI_TB06_MUNICIPIO", "Município"),
            ("DADOS_REFERENCIA", "VEI_TB07_COMBUSTIVEL", "Combustível"),
            ("DADOS_REFERENCIA", "VEI_TB08_COR", "Cor"),
            ("DADOS_REFERENCIA", "VEI_TB09_CARROCERIA", "Carroceria"),
            ("DADOS_REFERENCIA", "VEI_TB10_ESPECIE_VEICULO", "Espécie de veículo"),
            ("DADOS_REFERENCIA", "VEI_TB11_TIPO_VEICULO", "Tipo de veículo"),
            ("DADOS_REFERENCIA", "VEI_TB12_NACIONALIDADE", "Nacionalidade"),
            ("DADOS_REFERENCIA", "VEI_TB13_REGIAO", "Região"),
            ("DADOS_REFERENCIA", "VEI_TB14_RESTRICAO", "Restrição"),
            ("DADOS_REFERENCIA", "VEI_TB15_TIPO_PESSOA", "Tipo de pessoa"),
            ("DADOS_EXTRAIDOS", "VEI_TB01_DETRAN", "Veículos (Detran)"),
        ],
    },
    "pessoas": {
        "label": "Pessoas (Serasa)",
        "scripts": ["03_import_pessoas.sql"],
        "tabelas": [
            ("DADOS_EXTRAIDOS", "PES_TB01_PESSOAS", "Pessoas"),
        ],
    },
    "ibge": {
        "label": "Localidades (IBGE)",
        "scripts": ["04_import_ibge.sql"],
        "tabelas": [
            ("DADOS_REFERENCIA", "IBGE_TB01_PAIS", "País"),
            ("DADOS_REFERENCIA", "IBGE_TB02_REGIAO", "Região"),
            ("DADOS_REFERENCIA", "IBGE_TB03_UF", "UF"),
            ("DADOS_REFERENCIA", "IBGE_TB04_MESORREGIAO", "Mesorregião"),
            ("DADOS_REFERENCIA", "IBGE_TB05_MICRORREGIAO", "Microrregião"),
            ("DADOS_REFERENCIA", "IBGE_TB06_REGIAO_INTERMEDIARIA", "Região intermediária"),
            ("DADOS_REFERENCIA", "IBGE_TB07_REGIAO_IMEDIATA", "Região imediata"),
            ("DADOS_REFERENCIA", "IBGE_TB08_MUNICIPIO", "Município"),
            ("DADOS_REFERENCIA", "IBGE_TB09_DISTRITO", "Distrito"),
            ("DADOS_REFERENCIA", "IBGE_TB10_SUBDISTRITO", "Subdistrito"),
        ],
    },
    "tse": {
        "label": "Eleições (TSE)",
        "scripts": ["05_import_tse.sql"],
        "tabelas": [
            ("DADOS_EXTRAIDOS", "TSE_TB01_CANDIDATOS", "Candidatos"),
            ("DADOS_EXTRAIDOS", "TSE_TB02_PARTIDOS", "Partidos"),
            ("DADOS_EXTRAIDOS", "TSE_TB03_ELEICOES", "Eleições"),
            ("DADOS_EXTRAIDOS", "TSE_TB04_VOTACAO_CANDIDATO", "Votação por candidato"),
            ("DADOS_EXTRAIDOS", "TSE_TB05_VOTACAO_PARTIDO", "Votação por partido"),
        ],
    },
}


@router.get("/api/ingestao/status")
def listar_status():
    """Lista, para cada grupo de dados, a contagem de registros de cada tabela e se um import está rodando."""
    resposta = []
    for grupo, config in GRUPOS.items():
        tabelas = [
            {"schema": schema, "tabela": tabela, "label": label, "registros": count_table(schema, tabela)}
            for schema, tabela, label in config["tabelas"]
        ]
        job = status_job(grupo)
        resposta.append({
            "grupo": grupo,
            "label": config["label"],
            "tabelas": tabelas,
            "rodando": job["rodando"],
            "ultimo_resultado": None if job["rodando"] or job["ok"] is None else {"ok": job["ok"], "mensagem": job["mensagem"]},
        })
    return resposta


@router.post("/api/ingestao/importar/{grupo}")
def importar(grupo: str, usuario: dict = Depends(exigir_admin)):
    """Dispara em background os scripts de import do grupo informado (ex.: 'veiculos', 'tse')."""
    config = GRUPOS.get(grupo)
    if not config:
        raise HTTPException(status_code=404, detail="Grupo de dados desconhecido.")

    # o 409 vem do próprio iniciar_import: o guard é o índice único da tabela de jobs, então dois
    # cliques simultâneos não passam os dois (a checagem antes do INSERT tinha essa janela)
    if not iniciar_import(grupo, config["scripts"], usuario["id_usuario"]):
        raise HTTPException(status_code=409, detail="Esse grupo já está importando.")
    return {"ok": True, "status": "iniciado"}

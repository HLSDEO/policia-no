"""Importação de .zip de documentos em fila (JOB_TB01_JOBS, tp_job='import_zip').

Por que fila: um ZIP de prova com centenas de documentos leva minutos para expandir, extrair
texto e rodar os parsers (IPL/IRPF/IPEI/telefonia) — bem além do `proxy_read_timeout` de 300 s
do nginx para rotas comuns (ver nginx/default.conf). Na requisição síncrona o nginx cortava a
resposta com 504 e a tela não sabia o que entrou ou não. Aqui o POST só valida, cria o
inquérito e abre o job; uma thread daemon faz o trabalho com progresso por arquivo em
JOB_TB01_JOBS (mesmo padrão da cópia de rede, funcoes/rede_smb.py) e a tela acompanha por
GET /api/investigacao/jobs/{id_job}.

Desenho:

- Escopo do guard é o inquérito (um ZIP por vez por inquérito, como o OCR): o segundo POST
  concorrente leva 409 com quem começou e quando (ver jobs_utils._mensagem_ocupado).
- pesado=False: é I/O + extração de texto/parsers, não CLIP/transcrição — não ocupa vaga de
  jobs_pesados.
- Cada arquivo anexado commita na hora (anexar_documento_inquerito): fechar a página ou
  reiniciar a API não perde o que já entrou; o progresso mora no banco, não na memória.
- Cancelamento (fl_cancelar) é checado a cada arquivo; ao cancelar ou concluir, o ZIP sai da
  pasta de entrega para OUTROS (best-effort) — retry exige reenviar/recopiar o ZIP, e nunca
  duplica o que já foi anexado.
"""

import logging
import threading

from funcoes import jobs_utils

logger = logging.getLogger(__name__)

# Teto de falhas detalhadas no TX_RESULTADO (que só guarda 256 KB — ver
# jobs_utils._MAX_RESULTADO_BYTES): o contador total vai em qt_falhas sempre.
_MAX_FALHAS_NO_RESULTADO = 50

# Acima disto, a lista por-documento sai do resultado (só contadores + falhas): 2000
# documentos detalhados estourariam o teto do TX_RESULTADO.
_MAX_DOCS_NO_RESULTADO = 500


def pos_processar_documento(resultado: dict, id_usuario: int | None,
                            no_delegacia_usuario: str | None = None) -> dict:
    """Roteamento pós-anexo de UM documento: IPL/IRPF/IPEI/telefonia ou análise de IA.

    O anexo já moveu o arquivo para a pasta do inquérito (data/<delegacia>/<nº>/OUTROS/),
    então o que segue adiante é o caminho devolvido por ele, não mais o nome do formulário.
    `no_delegacia_usuario` alimenta o bloqueio_ia no lugar do dict do request (a fila não tem
    request — a rota fotografa o valor no disparo)."""
    from funcoes.ipei_utils import extrair_ipei
    from funcoes.ipl_utils import extrair_inquerito
    from funcoes.irpf_utils import extrair_irpf
    from funcoes.telefonia_utils import extrair_telefonia

    arquivo_anexado = resultado["no_arquivo"]
    # sendo o PDF do próprio inquérito, levanta ainda o índice de peças, as pessoas citadas e
    # os dados de interesse — o documento vira um bloco navegável no card, como a extração e o RIF
    ipl = extrair_inquerito(
        arquivo_anexado, resultado["id_inquerito"], resultado["id_documento"], id_usuario)
    # sendo o PDF de uma declaração de IRPF (Receita Federal), levanta identificação, bens,
    # dependentes, rendimentos, dívidas e doações — mesmo tratamento diferenciado do IPL
    irpf = extrair_irpf(
        arquivo_anexado, resultado["id_inquerito"], resultado["id_documento"], id_usuario)
    # sendo uma IPEI (quebra de sigilo fiscal da Receita Federal), levanta alvos, vínculos,
    # consolidado anual, contas, bens, notas fiscais e contatos. Aqui entra também a PLANILHA
    # de anexo: ela não vira uma segunda importação — extrair_ipei a costura na IPEI de mesmo
    # número já importada neste inquérito, e o par PDF + anexo aparece como um bloco só no card
    ipei = extrair_ipei(
        arquivo_anexado, resultado["id_inquerito"], resultado["id_documento"], id_usuario)
    # sendo um arquivo de operadora (cadastro de linhas, planilha do ofício ou o PDF dela),
    # levanta as linhas, o CDR, as ERBs, os números/IMEI e as sessões WiFi. Aqui, ao contrário
    # dos anteriores, o bloco NÃO é por arquivo: todos os arquivos da mesma operadora caem no
    # mesmo card do inquérito, porque a resposta da quebra de sigilo telefônico vem partida em
    # vários (as ERBs de um período grande vêm em levas) e é tudo o mesmo material
    telefonia = extrair_telefonia(
        arquivo_anexado, resultado["id_inquerito"], resultado["id_documento"], id_usuario)
    # não sendo nada disso, a IA ainda varre o documento em busca de pessoas e achados
    # (resumo + listas estruturadas no bloco do card) — silencioso quando não há IA
    # configurada, e só quando o texto foi extraído com sucesso — e só quando a IA está
    # liberada para a delegacia de quem anexa e para a do inquérito (funcoes/ia_delegacia.py):
    # aqui a análise é efeito colateral do anexo, então ela é pulada em silêncio em vez de
    # derrubar o anexo com 403 (o documento fica anexado do mesmo jeito)
    # qt_caracteres > 0: imagem de prova entra sem camada de texto por design (ver
    # _IMAGENS_PROVA) — não há o que a IA varrer, e rodar de graça polui a fila
    if (ipl is None and irpf is None and ipei is None and telefonia is None
            and not resultado["tx_erro"] and resultado.get("qt_caracteres")):
        from funcoes.documentos_utils import _ia_configurada, _tem_padroes_ativos, iniciar_analise_documento
        from funcoes.ia_delegacia import bloqueio_ia
        ia_livre = _ia_configurada(id_usuario) and not bloqueio_ia(
            {"no_delegacia": no_delegacia_usuario, "id_usuario": id_usuario},
            id_inquerito=resultado["id_inquerito"])
        # com a IA bloqueada/desligada ainda vale rodar: o pré-passe determinístico
        # (entidades_auto_utils) persiste sozinho (usar_ia=False)
        if ia_livre or _tem_padroes_ativos():
            iniciar_analise_documento(resultado["id_inquerito"], resultado["id_documento"],
                                       id_usuario, usar_ia=ia_livre)
    return {"inquerito": ipl, "irpf": irpf, "ipei": ipei, "telefonia": telefonia}


def iniciar_importacao_zip(*, arquivo: str, id_inquerito: int, nu_inquerito: str,
                           subpasta: str | None, delegacia: str | None,
                           id_usuario: int | None, no_delegacia_usuario: str | None = None,
                           caso_inteligencia: bool = False) -> int:
    """Abre o job de importação do ZIP e dispara a thread. Devolve o id_job.

    O inquérito já vem criado pela rota (barato e síncrono) — é ele que escopa o guard e o
    link "Ver andamento". Levanta jobs_utils.JobOcupado se já houver importação de ZIP ativa
    neste inquérito."""
    id_job = jobs_utils.criar(
        jobs_utils.TP_IMPORT_ZIP, id_usuario,
        id_inquerito=id_inquerito,
        parametros={"arquivo": arquivo, "nu_inquerito": nu_inquerito,
                    "subpasta": subpasta, "delegacia": delegacia,
                    "caso_inteligencia": bool(caso_inteligencia)},
        pesado=False,  # I/O + parsers, não CLIP: não ocupa vaga de jobs_pesados
    )
    jobs_utils.progresso(id_job, total=0, processados=0,
                         metricas={"fase": "expandindo", "arquivo": arquivo})
    logger.info("Importação de ZIP %s iniciada por usuario=%s no inquérito %s (job %s)",
                arquivo, id_usuario, nu_inquerito, id_job)
    t = threading.Thread(target=_executar_importacao_zip,
                         args=(id_job, arquivo, id_inquerito, nu_inquerito,
                               subpasta, delegacia, id_usuario, no_delegacia_usuario,
                               bool(caso_inteligencia)),
                         name=f"import-zip-{id_job}", daemon=True)
    t.start()
    return id_job


def _executar_importacao_zip(id_job: int, arquivo: str, id_inquerito: int, nu_inquerito: str,
                             subpasta: str | None, delegacia: str | None,
                             id_usuario: int | None, no_delegacia_usuario: str | None,
                             caso_inteligencia: bool) -> None:
    from funcoes.documentos_utils import (
        anexar_documento_inquerito,
        expandir_zip_para_importacao,
    )

    jobs_utils.marcar_processando(id_job)
    try:
        expansao = expandir_zip_para_importacao(arquivo, subpasta, delegacia)
    except (ValueError, FileNotFoundError) as exc:
        jobs_utils.falhar(id_job, str(exc)[:2000])
        return
    except Exception as exc:  # noqa: BLE001 — job nunca pode morrer sem status
        logger.exception("Falha ao expandir o ZIP %s (job %s)", arquivo, id_job)
        jobs_utils.falhar(id_job, f"Falha ao expandir o ZIP: {exc}"[:2000])
        return

    arquivos = expansao["arquivos"]
    finalizar = expansao["finalizar"]
    # RIFs avulsos no ZIP (CSVs soltos, sem pasta): agrupa por número e importa como
    # RIF estruturado em vez de documento genérico (caso 2024.0081190: 4 RIFs viraram
    # OUTROS). Só vale para arquivos locais — no nó remoto, montar_pasta levanta
    # FileNotFoundError e o grupo cai de volta para o fluxo genérico abaixo.
    rifs_importados: list[dict] = []
    try:
        from funcoes.rif_utils import (
            montar_pasta_grupo_rif,
            separar_grupos_rif_avulsos,
        )
        from funcoes.investigacao_utils import PASTA_DADOS

        grupos_rif, arquivos = separar_grupos_rif_avulsos(arquivos)
    except Exception:
        logger.exception("Falha ao separar grupos de RIF do ZIP %s (job %s)", arquivo, id_job)
        grupos_rif, arquivos = {}, arquivos
    if grupos_rif:
        try:
            from funcoes.rif_utils import importar_rif
        except Exception:
            logger.exception("Falha ao carregar importar_rif (job %s)", id_job)
            grupos_rif = {}
    cancelado_pre = False
    for nu_rif in sorted(grupos_rif):
        grupo = grupos_rif[nu_rif]
        if jobs_utils.progresso(id_job, processados=0,
                                 metricas={"fase": "importando", "atual": f"RIF {nu_rif}"}):
            # cancelado antes dos genéricos: guarda o parcial com o que já entrou
            cancelado_pre = True
            break
        try:
            pasta_rif = montar_pasta_grupo_rif(nu_rif, grupo, subpasta)
            res_rif = importar_rif(pasta_rif, nu_inquerito, id_usuario, subpasta=subpasta,
                                   delegacia=delegacia, in_caso_inteligencia=caso_inteligencia)
            rifs_importados.append({"nu_rif": res_rif.get("nu_rif") or nu_rif,
                                    "id_rif": res_rif.get("id_rif"),
                                    "qt_comunicacoes": res_rif.get("qt_comunicacoes", 0),
                                    "qt_envolvidos": res_rif.get("qt_envolvidos", 0),
                                    "qt_ocorrencias": res_rif.get("qt_ocorrencias", 0)})
            # sucesso: os soltos eram só staging (a cópia já está no RIF/ organizado) —
            # remove para não virarem documento genérico duplicado abaixo
            base_docs = PASTA_DADOS / subpasta if subpasta else PASTA_DADOS
            for nome_solto in grupo.values():
                try:
                    (base_docs / nome_solto).unlink(missing_ok=True)
                except Exception:
                    logger.exception("Falha ao remover solto %s do RIF %s (job %s)",
                                     nome_solto, nu_rif, id_job)
        except (FileNotFoundError, ValueError) as exc:
            # nó remoto (sem arquivo local) ou pasta inválida: cai para genérico abaixo,
            # para não perder a prova — o motivo fica em falhas
            logger.warning("RIF %s do ZIP %s vai como genérico: %s", nu_rif, arquivo, exc)
            # devolve os arquivos do grupo à fila genérica, uma vez só
            for nome_solto in grupo.values():
                if nome_solto not in arquivos:
                    arquivos.append(nome_solto)
            try:
                from funcoes.rif_utils import _subpasta_rif as _sub_rif
                import shutil as _shutil
                for sufixo_cand in ("", " (2)", " (3)"):
                    cand = _sub_rif(subpasta) / f"RIF_{nu_rif}_zip{sufixo_cand}"
                    if cand.is_dir():
                        _shutil.rmtree(cand, ignore_errors=True)
                        break
            except Exception:
                pass
            # registra como falha de RIF, mas segue para o genérico
            # (a lista `falhas` é criada logo abaixo — usa variável temporária)
            rifs_importados.append({"nu_rif": nu_rif, "id_rif": None, "erro": str(exc)[:300],
                                    "como_generico": True})
        except Exception as exc:  # noqa: BLE001 — parser do RIF não pode matar o lote
            logger.exception("Falha ao importar RIF %s do ZIP %s (job %s)", nu_rif, arquivo, id_job)
            for nome_solto in grupo.values():
                if nome_solto not in arquivos:
                    arquivos.append(nome_solto)
            rifs_importados.append({"nu_rif": nu_rif, "id_rif": None, "erro": str(exc)[:300],
                                    "como_generico": True})
    if cancelado_pre:
        jobs_utils.progresso(id_job, processados=0, erros=0)
        finalizar(nu_inquerito)
        jobs_utils.marcar_cancelado(id_job)
        try:
            from psycopg.types.json import Jsonb
            from funcoes.db_utils import get_connection
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE investigacao.job_tb01_jobs SET tx_resultado = %s WHERE id_job = %s",
                        (Jsonb({"qt_rifs": len([r for r in rifs_importados if r.get('id_rif')]),
                                "rifs": rifs_importados, "cancelado_antes_genericos": True}), id_job),
                    )
                conn.commit()
        except Exception:
            logger.exception("Falha ao gravar resultado parcial do job %s", id_job)
        return
    jobs_utils.progresso(id_job, total=len(arquivos), processados=0, erros=0,
                         metricas={"fase": "importando", "ignorados": expansao["ignorados"],
                                   "truncado": expansao["truncado"],
                                   "rifs_detectados": len(rifs_importados)})

    qt_erro_texto = 0
    qt_reconhecidos = 0
    qt_ipeis = 0
    qt_anexos_ipei = 0
    qt_anexados = 0
    operadoras: dict[str, bool] = {}
    detalhes: list[dict] = []
    falhas: list[str] = []
    cancelado = False

    for i, nome_interno in enumerate(arquivos, 1):
        # Batimento + checagem de cancelamento a cada arquivo (o progresso() devolve
        # fl_cancelar): job cancelado para aqui; o que já entrou fica (cada anexo commita).
        if jobs_utils.progresso(id_job, processados=i - 1,
                                metricas={"fase": "importando", "atual": nome_interno}):
            cancelado = True
            break
        try:
            res = anexar_documento_inquerito(nome_interno, nu_inquerito, id_usuario, subpasta,
                                             delegacia, caso_inteligencia)
        except (ValueError, FileNotFoundError, UnicodeError) as exc:
            falhas.append(f"{nome_interno}: {exc}")
            continue
        except Exception as exc:  # noqa: BLE001
            try:
                import psycopg

                if isinstance(exc, psycopg.DataError):
                    falhas.append(f"{nome_interno}: texto com caracteres que o banco não aceita.")
                    continue
            except ImportError:
                pass
            logger.exception("Falha ao anexar %s do ZIP %s (job %s)", nome_interno, arquivo, id_job)
            falhas.append(f"{nome_interno}: {exc}"[:500])
            continue
        qt_anexados += 1
        try:
            pos = pos_processar_documento(res, id_usuario, no_delegacia_usuario)
        except Exception as exc:  # noqa: BLE001 — parser não pode matar o lote
            logger.exception("Falha no pós-processamento de %s (job %s)", nome_interno, id_job)
            falhas.append(f"{nome_interno}: falha no reconhecimento ({exc})"[:500])
            pos = {"inquerito": None, "irpf": None, "ipei": None, "telefonia": None}
        if res.get("tx_erro"):
            qt_erro_texto += 1
        if pos["inquerito"]:
            qt_reconhecidos += 1
        anexo_ipei = bool((pos["ipei"] or {}).get("anexo")) if pos["ipei"] else False
        if pos["ipei"]:
            if anexo_ipei:
                qt_anexos_ipei += 1
            else:
                qt_ipeis += 1
        operadora = (pos["telefonia"] or {}).get("no_operadora") if pos["telefonia"] else None
        if operadora:
            operadoras[str(operadora)] = True
        if len(detalhes) < _MAX_DOCS_NO_RESULTADO:
            detalhes.append({"n": nome_interno, "d": res["id_documento"],
                             "e": (res.get("tx_erro") or "")[:200] or None,
                             "ipl": bool(pos["inquerito"]), "ipei": bool(pos["ipei"]),
                             "anexo": anexo_ipei, "to": str(operadora)[:40] if operadora else None})

    jobs_utils.progresso(id_job, processados=len(arquivos) if not cancelado else None,
                         erros=len(falhas))
    finalizar(nu_inquerito)

    resultado = {
        "qt_anexados": qt_anexados,
        "qt_erro_texto": qt_erro_texto,
        "qt_reconhecidos": qt_reconhecidos,
        "qt_ipeis": qt_ipeis,
        "qt_anexos_ipei": qt_anexos_ipei,
        "operadoras": sorted(operadoras),
        "ignorados": expansao["ignorados"],
        "truncado": expansao["truncado"],
        "qt_falhas": len(falhas),
        "falhas": falhas[:_MAX_FALHAS_NO_RESULTADO],
        "documentos": detalhes,
        "qt_rifs": len([r for r in rifs_importados if r.get("id_rif")]),
        "rifs": rifs_importados,
    }
    if len(arquivos) > _MAX_DOCS_NO_RESULTADO:
        # lista capada: guarda só os com erro (até 100) + contadores; o resto está no banco
        resultado["documentos"] = [d for d in detalhes if d["e"]][:100]

    if cancelado:
        _fechar_cancelado_com_resultado(id_job, resultado)
        return
    tem_rif_ok = any(r.get("id_rif") for r in rifs_importados)
    if qt_anexados == 0 and not tem_rif_ok:
        jobs_utils.falhar(id_job, f"{arquivo}: nada pôde ser anexado — "
                                  f"{'; '.join(falhas[:5]) if falhas else 'sem conteúdo aproveitável'}"[:2000])
        return
    jobs_utils.concluir(id_job, resultado=resultado)


def limite_doc_sincrono_bytes() -> int:
    """Acima disto, um documento avulso vai para a fila (TP_DOC_IMPORT) em vez de extrair na
    requisição — o mesmo motivo do ZIP: proxy_read_timeout de 300 s do nginx. Ajustável por
    DOC_IMPORTE_SINCRONO_MB (padrão 25; 0 = nunca enfileira, volta tudo ao caminho síncrono)."""
    from funcoes.env_utils import parse_env, read_env_text

    try:
        bruto = (parse_env(read_env_text()).get("DOC_IMPORTE_SINCRONO_MB") or "").strip() or "25"
        return max(0, int(bruto)) * 1024 * 1024
    except ValueError:
        return 25 * 1024 * 1024


def iniciar_importacao_documento(*, arquivo: str, id_inquerito: int, nu_inquerito: str,
                                 subpasta: str | None, delegacia: str | None,
                                 id_usuario: int | None, no_delegacia_usuario: str | None = None,
                                 caso_inteligencia: bool = False) -> int:
    """Enfileira a importação de UM documento pesado (ver limite_doc_sincrono_bytes). O inquérito
    vem criado pela rota (mesma razão do ZIP: escopa o guard). Devolve o id_job."""
    id_job = jobs_utils.criar(
        jobs_utils.TP_DOC_IMPORT, id_usuario,
        id_inquerito=id_inquerito,
        parametros={"arquivo": arquivo, "nu_inquerito": nu_inquerito,
                    "subpasta": subpasta, "delegacia": delegacia,
                    "caso_inteligencia": bool(caso_inteligencia)},
        pesado=False,  # I/O + parser, não disputa vaga de CLIP/Tesseract
    )
    jobs_utils.progresso(id_job, total=1, processados=0,
                         metricas={"fase": "extraindo", "arquivo": arquivo})
    logger.info("Importação de documento pesado %s enfileirada (job %s, inquérito %s)",
                arquivo, id_job, nu_inquerito)
    t = threading.Thread(target=_executar_importacao_documento,
                         args=(id_job, arquivo, id_inquerito, nu_inquerito,
                               subpasta, delegacia, id_usuario, no_delegacia_usuario,
                               bool(caso_inteligencia)),
                         name=f"import-doc-{id_job}", daemon=True)
    t.start()
    return id_job


def _executar_importacao_documento(id_job: int, arquivo: str, id_inquerito: int,
                                   nu_inquerito: str, subpasta: str | None,
                                   delegacia: str | None, id_usuario: int | None,
                                   no_delegacia_usuario: str | None,
                                   caso_inteligencia: bool) -> None:
    """Worker: anexa (extraindo o texto, que é o passo lento) e roda o pós-processamento —
    exatamente o que a rota faria na requisição, só que sem relógio correndo contra o proxy."""
    from funcoes.documentos_utils import anexar_documento_inquerito

    jobs_utils.marcar_processando(id_job)
    try:
        resultado = anexar_documento_inquerito(
            arquivo, nu_inquerito, id_usuario, subpasta,
            delegacia=delegacia, in_caso_inteligencia=caso_inteligencia)
        pos = pos_processar_documento(resultado, id_usuario, no_delegacia_usuario)
    except (ValueError, FileNotFoundError) as exc:
        jobs_utils.falhar(id_job, str(exc)[:2000])
        return
    except Exception as exc:  # noqa: BLE001 — job nunca pode morrer sem status
        logger.exception("Falha na importação do documento %s (job %s)", arquivo, id_job)
        jobs_utils.falhar(id_job, f"Falha ao importar o documento: {exc}"[:2000])
        return
    reconhecido = any(pos.get(chave) for chave in
                      ("inquerito", "irpf", "ipei", "telefonia"))
    jobs_utils.progresso(id_job, processados=1, total=1)
    # o resultado tem o MESMO formato do job de ZIP (qt_anexados/qt_reconhecidos/falhas) de
    # propósito: o polling da tela (acompanharJob* em inqueritos_importar.js) é um só
    erro_anexo = resultado.get("tx_erro")
    jobs_utils.concluir(id_job, resultado={
        "qt_anexados": 1,
        "qt_reconhecidos": 1 if reconhecido else 0,
        "falhas": [f"anexado sem texto ({erro_anexo})"] if erro_anexo else [],
        "id_documento": resultado["id_documento"],
        "qt_caracteres": resultado.get("qt_caracteres", 0),
    })


def _fechar_cancelado_com_resultado(id_job: int, resultado: dict) -> None:
    """Marca cancelado E guarda o parcial (marcar_cancelado não aceita resultado — ver
    jobs_utils._fechar): a tela mostra o que já entrou antes do cancelamento."""
    from psycopg.types.json import Jsonb

    from funcoes.db_utils import get_connection

    jobs_utils.marcar_cancelado(id_job)
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE investigacao.job_tb01_jobs SET tx_resultado = %s "
                    "WHERE id_job = %s",
                    (Jsonb(resultado), id_job),
                )
            conn.commit()
    except Exception:
        logger.exception("Falha ao gravar resultado parcial do job %s", id_job)

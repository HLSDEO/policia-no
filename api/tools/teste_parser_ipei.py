"""Teste do parser de IPEI (quebra de sigilo fiscal) — sem banco, roda em qualquer lugar.

Duas metades:

- **sintética**, sempre: um texto de IPEI montado aqui, com as armadilhas que o extrator de PDF
  cria de verdade e que quebrariam em silêncio — nome de glifo no lugar do caractere, célula
  vazia marcada com "-", CNPJ partido na quebra de linha, tabela emendada no parágrafo que vem
  logo abaixo dela, mês colado na última letra do nome do banco e ordem de coluna diferente
  entre relatórios. Cada uma dessas já custou uma linha errada (ou a linha inteira) durante o
  desenvolvimento;
- **corpus**, quando existe: aponte para uma pasta com IPEIs reais e o teste imprime o que
  levantou de cada uma. Serve para conferir um lote novo antes de importar de verdade.

    docker compose exec api-1 python /app/tools/teste_parser_ipei.py
    docker compose exec api-1 python /app/tools/teste_parser_ipei.py /app/data/DELEGACIA/PASTA
"""
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from funcoes import ipei_utils as ip  # noqa: E402
from funcoes.pdf_texto import desglifar  # noqa: E402

CPF = "144.558.737-83"

# Página 1 e rodapé: é daqui que saem o número, a data e a unidade — e é aqui que o gerador
# escreve nome de glifo em vez de caractere (o "INFORMAÇÃO" abaixo está em /uniXXXX de propósito).
CAPA = (
    "/uni0049/uni004E/uni0046/uni004F/uni0052/uni004D/uni0041/uni00C7/uni00C3/uni004F"
    " PROTEGIDA POR SIGILO FISCAL\n"
    "Coordenação-Geral de Pesquisa e Investigação\n"
    "IPEI Nº: VT20230001\n"
    "ALVO: LEONARDO MARTINS ZEKEL – CPF 144.558.737-83\n"
)

RODAPE = ("INFORMAÇÃO PROTEGIDA POR SIGILO FISCAL\n"
          "(Fl.    7   /   40    da IPEI nº VT20230001, de    19 de junho de 2023"
          "   – RFB/Copei/Nupei em Vitória/ES)   \n")


def pagina(corpo: str) -> str:
    return RODAPE + corpo


PAGINAS_SINTETICAS = [
    CAPA,
    pagina(
        "1 INTRODUÇÃO\n"
        "Nos autos do Processo nº 0001941-73.2022.8.08.0047, exarou-se decisão judicial, em\n"
        "25/05/2023, deferindo pedidos formulados pela Polícia Federal.\n"
    ),
    pagina(
        "2 INFORMAÇÕES FISCAIS DE LEONARDO MARTINS ZEKEL\n"
        "2.1 Dados Cadastrais\n"
        "CPF144.558.737-83\n"
        "NomeLEONARDO MARTINS ZEKEL\n"
        "Nome da MãeIZABEL DE JESUS MARTINS ZEKEL\n"
        "Data de Nascimento23/10/1994\n"
        "Endereço CompletoCEP 29050600 - R CARLOS ALVES, 170 - BENTO FERREIRA - VITÓRIA-ES\n"
        "Ocupação Principal: \nDescriçãoADVOGADO\n"
        "Situação Cadastral: REGULAR\n"
        "2.2 Participações em Empresas\n"
        "CNPJ Nome Razão Social QualificaçãoSit.Cadastral\n"
        # o CNPJ parte no meio na coluna estreita, e a qualificação vem colada no nome
        "23.750.632/0001\n-78\nTYPICA COMERCIO DE\nGRAOS LTDA \nSocio \nadministrador\nAtiva\n"
        "23/01/2020\n31/03/2022\n-0,00% 100,00%\n"
    ),
    pagina(
        "2.5 Rendimentos Declarados\n"
        "CPFAno Calendário Tributáveis Isentos Exclusiva Total\n"
        "144.558.737-83 2018 38.848,70 15.056,26 2.665,04 56.570,00\n"
        # 2019 tem a coluna de isentos VAZIA, marcada com "-": sem contar como coluna, os dois
        # valores seguintes escorregam e o total do ano vira o da tributação exclusiva
        "144.558.737-83 2019 38.389,17 - 2.873,60 41.262,77\n"
        "2.13 Apuração da Variação Patrimonial\n"
        "CPF Ano Bens ano Bens anterior Dívidas ano Dívidas anterior Variação Dif transp\n"
        "144.558.737-83 2022 83.456,16 50.981,51 0,00  0,00 32.474,65 -756.049,27\n"
        # a tabela emenda no parágrafo logo abaixo dela; os valores da FRASE não podem entrar
        "A declaração referente ao ano calendário 2022 apresenta incoerências, como a informação\n"
        "de que os bens do ano anterior seriam no montante de R$ 50.981,51.\n"
    ),
    pagina(
        "2.9 Movimentação financeira\n"
        "2.9.1 Movimentação de contas de depósito\n"
        # layout A: banco, conta + agência, mês, titulares, créditos, débitos, saldo
        "144.558.737-83 2018\nBANESTES SA BANCO DO ESTADO\nDO ESPIRITO SANTO\n"
        "0277.101.0000022\n542203 02/2018 1 4.380,36 3.708,67 2.979,91\n"
        # layout B: banco, mês COLADO na última letra, conta, tipo de relação, créditos, débitos
        "144.558.737-83 BANESTES SA BANCO DO ESTADO DO \nESPIRITO SANTO03/2018"
        " 0208.102.0000011523776 TITULAR 1.230,00 1.223,62\n"
    ),
    pagina(
        "2.17 Veículos Registrados no RENAVAM\n"
        "PLACACPF SITUACAO MARCA/MODELO ANO-MODELO RESTRICOES\n"
        "QRB3F07 144.558.737-83 CIRCULACAO LR/EVOQUE P240 HSE DYN 2018 14/10/2022"
        " ALIENACAO FIDUCIARIA\n"
        "2.26 Endereços MAC coincidentes\n"
        "94-DE-80-F7-66-9D 45.7.164.140 23/03/2022 15:45:00 109.472.477-77"
        " RAMON COELHO DOS SANTOS IRPF 2022 Original 2021\n"
        "2.27 Endereços, números de telefones e e-mails – Outros sistemas\n"
        "144.558.737-83 2018 AV RESPLENDOR, 38, , UNIVERSAL, VIANA, ES\n"
        "144.558.737-83 2021 27996117499 LEONARDO1MARTINSZ@HOTMAIL.COM\n"
        "3 CONCLUSÃO\n"
        "A análise da situação fiscal do contribuinte permitiu inferir que houve aumento\n"
        "expressivo nas movimentações a partir de 2021.\n"
    ),
]


def _um(lista, **filtros):
    """A única linha que casa com os filtros — falha alto se houver zero ou mais de uma."""
    achados = [x for x in lista if all(x.get(k) == v for k, v in filtros.items())]
    assert len(achados) == 1, f"esperava 1 linha com {filtros}, achei {len(achados)}: {achados}"
    return achados[0]


def conferir(condicao, mensagem):
    print(("  ok   " if condicao else "  FALHA") + " · " + mensagem)
    return bool(condicao)


def testar_sintetico() -> bool:
    paginas = [desglifar(p) for p in PAGINAS_SINTETICAS]
    ok = conferir(ip.parece_ipei(paginas), "reconhece o PDF como IPEI (com o timbre em nome de glifo)")
    ok &= conferir(not ip.parece_ipei(["Ofício nº 123 — encaminhamento"]),
                   "não confunde o ofício de encaminhamento da mesma pasta com uma IPEI")

    secoes = ip._secoes(paginas)
    numeros = [s["numero"] for s in secoes]
    ok &= conferir(numeros == ["1", "2", "2.1", "2.2", "2.5", "2.13", "2.9", "2.9.1", "2.17",
                               "2.26", "2.27", "3"],
                   f"índice de seções reconstruído sem pegar linha de tabela: {numeros}")

    cab = ip._extrair_cabecalho(paginas, secoes)
    ok &= conferir(cab["nu_ipei"] == "VT20230001", "número da IPEI")
    ok &= conferir(cab["dt_ipei"] == "2023-06-19", "data de emissão (por extenso no rodapé)")
    ok &= conferir(cab["no_unidade"] == "RFB/Copei/Nupei em Vitória/ES", "unidade emissora")
    ok &= conferir(cab["nu_processo_judicial"] == "0001941-73.2022.8.08.0047", "processo judicial")
    ok &= conferir(cab["dt_decisao"] == "2023-05-25", "data da decisão que afastou o sigilo")
    ok &= conferir(cab["qt_folhas"] == 40, "total de folhas")
    ok &= conferir("aumento" in (cab["tx_conclusao"] or ""), "texto da conclusão")

    alvos = ip._extrair_alvos(secoes)
    ok &= conferir(len(alvos) == 1, f"um alvo (a seção com 'Dados Cadastrais'): {len(alvos)}")
    alvo = alvos[0]
    ok &= conferir(alvo["nu_cpf_cnpj"] == CPF, "CPF do alvo (rótulo colado no valor)")
    ok &= conferir(alvo["no_alvo"] == "LEONARDO MARTINS ZEKEL", "nome do alvo")
    ok &= conferir(alvo["no_mae"] == "IZABEL DE JESUS MARTINS ZEKEL",
                   "nome da mãe (rótulo longo ganha de 'Nome')")
    ok &= conferir(alvo["dt_nascimento"] == "1994-10-23", "data de nascimento")
    ok &= conferir(alvo["nu_cep"] == "29050600", "CEP tirado de dentro do endereço completo")
    ok &= conferir(alvo["tx_ocupacao"] == "ADVOGADO", "ocupação sem o rótulo 'Descrição' colado")
    ok &= conferir(alvo["tx_situacao_cadastral"] == "REGULAR", "situação cadastral")

    relacionados = ip._extrair_relacionados(secoes, alvo)
    societario = _um(relacionados, tp_relacao="societario")
    ok &= conferir(societario["nu_cpf_cnpj"] == "23.750.632/0001-78",
                   "CNPJ remendado depois da quebra de linha")
    ok &= conferir(societario["no_relacionado"] == "TYPICA COMERCIO DE GRAOS LTDA",
                   f"razão social sem roubar a letra do campo seguinte: {societario['no_relacionado']!r}")
    ok &= conferir(societario["pc_participacao"] == 100.0, "percentual de participação")

    vinculos_mac, contatos_mac = ip._extrair_mac(secoes, alvo)
    mac = _um(vinculos_mac, tp_relacao="mac_coincidente")
    ok &= conferir(mac["nu_cpf_cnpj"] == "109.472.477-77",
                   "quem transmitiu declaração do mesmo MAC vira vínculo")
    ok &= conferir(any(c["tp_contato"] == "mac" for c in contatos_mac),
                   "o MAC/IP do próprio alvo vira contato")

    anos = {a["nu_ano"]: a for a in ip._extrair_anos(secoes, alvo)}
    ok &= conferir(anos[2018]["vl_rend_total"] == 56570.00, "total de rendimentos de 2018")
    ok &= conferir(anos[2019]["vl_rend_isentos"] is None and anos[2019]["vl_rend_total"] == 41262.77,
                   "coluna vazia ('-') conta como coluna e não desloca o total de 2019")
    ok &= conferir(anos[2022]["vl_bens_ano"] == 83456.16,
                   "variação patrimonial não é contaminada pelo parágrafo colado na tabela")
    ok &= conferir(anos[2022]["vl_dif_transposicao"] == -756049.27, "diferença de transposição")

    contas = ip._extrair_contas(secoes, alvo)
    ok &= conferir(len(contas) == 2, f"as duas linhas de conta, em ordens de coluna diferentes: {len(contas)}")
    conta_a = _um(contas, nu_mes=2)
    ok &= conferir(conta_a["nu_conta"] == "0277.101.0000022", "conta do layout com agência ao lado")
    ok &= conferir(conta_a["no_banco"] == "BANESTES SA BANCO DO ESTADO DO ESPIRITO SANTO",
                   f"banco sem o número da conta junto: {conta_a['no_banco']!r}")
    ok &= conferir((conta_a["vl_creditos"], conta_a["vl_debitos"], conta_a["vl_saldo"])
                   == (4380.36, 3708.67, 2979.91), "crédito, débito e saldo do mês")
    conta_b = _um(contas, nu_mes=3)
    ok &= conferir(conta_b["nu_conta"] == "0208.102.0000011523776",
                   "conta do layout em que o mês vem antes dela")
    ok &= conferir(conta_b["tx_relacao"] == "TITULAR", "tipo de relação do declarado")

    bens = ip._extrair_bens(secoes, alvo)
    veiculo = _um(bens, tp_bem="veiculo")
    ok &= conferir(veiculo["nu_identificador"] == "QRB3F07", "placa do RENAVAM")
    ok &= conferir(veiculo["tx_situacao"] == "ALIENACAO FIDUCIARIA", "restrição do veículo")

    contatos = ip._extrair_contatos(secoes, alvo)
    ok &= conferir(any(c["tp_contato"] == "endereco" and "RESPLENDOR" in c["tx_valor"] for c in contatos),
                   "endereço de outras bases")
    ok &= conferir(any(c["tp_contato"] == "email" and c["tx_valor"].endswith("HOTMAIL.COM") for c in contatos),
                   "e-mail de outras bases")
    ok &= conferir(any(c["tp_contato"] == "telefone" and c["tx_valor"] == "27996117499" for c in contatos),
                   "telefone de outras bases")

    ok &= conferir(ip.numero_ipei_do_nome("VT20230001 Anexo LEONARDO - NOTA FISCAL.xlsx") == "VT20230001",
                   "o anexo acha o PDF pelo número no nome do arquivo")
    ok &= conferir(ip.numero_ipei_do_nome("Alvos Quebra de Sigilo Fiscal.xlsx") is None,
                   "planilha sem número de IPEI no nome não se cola a nenhuma")
    return bool(ok)


def testar_corpus(pasta: str) -> bool:
    arquivos = sorted(glob.glob(os.path.join(pasta, "*.pdf")))
    if not arquivos:
        print(f"nenhum PDF em {pasta}")
        return True
    for caminho in arquivos:
        nome = os.path.basename(caminho)
        analise = ip.analisar_ipei(caminho)
        if analise is None:
            print(f"  (não é IPEI) {nome}")
            continue
        for alvo in analise["alvos"]:
            contagens = {k: len(alvo[k]) for k in
                         ("relacionados", "anos", "fontes", "contas", "bens", "notas", "contatos")}
            print(f"  {analise['cabecalho']['nu_ipei']} {(alvo['no_alvo'] or '')[:32]:32}"
                  f" {alvo['nu_cpf_cnpj']} {contagens}")
    return True


def main():
    print("IPEI sintética (armadilhas do extrator de PDF):")
    ok = testar_sintetico()
    if len(sys.argv) > 1:
        print(f"\nCorpus em {sys.argv[1]}:")
        ok &= testar_corpus(sys.argv[1])
    print("\nRESULTADO:", "ok" if ok else "FALHOU")
    sys.exit(0 if ok else 1)


main()

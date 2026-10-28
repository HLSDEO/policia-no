"""Dígito verificador de CPF e CNPJ.

Módulo próprio (e não uma função escondida em quem usa) porque a checagem serve a dois propósitos
bem diferentes no sistema: separar uma chave PIX de um número solto na conversa (funcoes/pix_utils)
e decidir se um documento de 14 dígitos vindo do banco é mesmo um CNPJ ou um CPF que o banco
completou com zeros à esquerda num campo largo (funcoes/simba_utils).
"""


def valida_cpf(cpf: str) -> bool:
    """Valida os dois dígitos verificadores do CPF (11 dígitos). Rejeita sequências repetidas."""
    if len(cpf) != 11 or cpf == cpf[0] * 11:
        return False
    for tam in (9, 10):
        soma = sum(int(cpf[i]) * ((tam + 1) - i) for i in range(tam))
        dig = (soma * 10) % 11
        if dig == 10:
            dig = 0
        if dig != int(cpf[tam]):
            return False
    return True


def valida_cnpj(cnpj: str) -> bool:
    """Valida os dois dígitos verificadores do CNPJ (14 dígitos). Rejeita sequências repetidas."""
    if len(cnpj) != 14 or cnpj == cnpj[0] * 14:
        return False
    pesos1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    pesos2 = [6] + pesos1
    for pesos, pos in ((pesos1, 12), (pesos2, 13)):
        soma = sum(int(cnpj[i]) * pesos[i] for i in range(pos))
        resto = soma % 11
        dig = 0 if resto < 2 else 11 - resto
        if dig != int(cnpj[pos]):
            return False
    return True

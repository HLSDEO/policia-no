"""Confirma, de ponta a ponta no banco, que trocar a senha encerra as sessões antigas e que só
um identificador NOVO abre acesso depois da troca.

Cobre o achado de segurança em que a troca de senha logada (`POST /api/auth/senha` ->
`definir_senha`) atualizava o hash da senha sem invalidar `USR_TB02_SESSOES`: um identificador de
sessão interceptado antes da troca continuava válido. `definir_senha` agora apaga as sessões do
usuário na mesma transação do UPDATE da senha, e a rota devolve cookie novo (`_abrir_sessao`).

O script trabalha com um usuário descartável criado e apagado por ele mesmo — não toca em conta
real. Rodar com a stack de pé:

    docker compose run --rm -v ./api:/app api python /app/tools/teste_troca_senha_sessao.py
"""

import secrets
import sys
import traceback

sys.path.insert(0, "/app")

from funcoes.auth_utils import (  # noqa: E402
    autenticar, criar_sessao, criar_usuario, definir_senha, validar_sessao,
)
from funcoes.db_utils import get_connection  # noqa: E402

SENHA_1 = "senha-antiga-123"
SENHA_2 = "senha-nova-456"

falhas: list[str] = []
passos = 0


def checar(rotulo, fn):
    global passos
    passos += 1
    try:
        print(f"  ok   {rotulo} -> {fn()}")
    except AssertionError:
        falhas.append(rotulo)
        print(f"  ERRO {rotulo}: afirmação falsa")
    except Exception:
        falhas.append(rotulo)
        print(f"  ERRO {rotulo}")
        print("       " + "\n       ".join(traceback.format_exc().strip().splitlines()[-3:]))


login = f"teste-troca-sessao-{secrets.token_hex(4)}"
id_usuario = criar_usuario(login, "Usuário de Teste", SENHA_1)
print(f"\nusuário descartável #{id_usuario} ({login}) criado")

try:
    print("\n[antes da troca]")
    checar("login com a senha antiga autentica",
           lambda: autenticar(login, SENHA_1) is not None)
    token_velho = criar_sessao(id_usuario)
    token_outro_disp = criar_sessao(id_usuario)
    checar("sessão do dispositivo 1 é aceita",
           lambda: validar_sessao(token_velho)["id_usuario"] == id_usuario)
    checar("sessão do dispositivo 2 é aceita",
           lambda: validar_sessao(token_outro_disp)["id_usuario"] == id_usuario)

    print("\n[troca de senha]")
    definir_senha(id_usuario, SENHA_2)
    checar("senha nova autentica", lambda: autenticar(login, SENHA_2) is not None)
    checar("senha antiga já não autentica", lambda: autenticar(login, SENHA_1) is None)

    print("\n[depois da troca — o ponto do achado]")
    checar("identificador de sessão ANTERIOR à troca é rejeitado",
           lambda: validar_sessao(token_velho) is None)
    checar("sessão de OUTRO dispositivo também caiu",
           lambda: validar_sessao(token_outro_disp) is None)
    token_novo = criar_sessao(id_usuario)
    checar("identificador NOVO é aceito",
           lambda: validar_sessao(token_novo)["id_usuario"] == id_usuario)

    print("\n[transação]")
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM seguranca.usr_tb02_sessoes WHERE id_usuario = %s",
                (id_usuario,),
            )
            restantes = cur.fetchone()[0]
    passos += 1
    if restantes != 1:  # só o token_novo, criado depois da troca
        falhas.append("nenhuma sessão sobrevivente à troca")
        print(f"  ERRO sessões do usuário após a troca -> {restantes} (esperado: só a nova)")
    else:
        print(f"  ok   nenhuma sessão emitida antes da troca sobreviveu ({restantes} = a nova)")
finally:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM seguranca.usr_tb02_sessoes WHERE id_usuario = %s", (id_usuario,))
            cur.execute("DELETE FROM seguranca.usr_tb01_usuarios WHERE id_usuario = %s", (id_usuario,))
        conn.commit()
    print(f"\nusuário descartável #{id_usuario} removido")

print(f"\n{passos - len(falhas)}/{passos} passos ok")
if falhas:
    print("FALHARAM: " + ", ".join(falhas))
sys.exit(1 if falhas else 0)

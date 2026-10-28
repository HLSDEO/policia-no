"""Autenticação: usuários, hash de senha, sessões e recuperação de senha.

Sessão é server-side (token opaco de 256 bits em cookie httpOnly; só o hash
SHA-256 do token fica no banco) — diferente de JWT, dá pra revogar uma sessão
específica (logout) ou todas de um usuário (desativação) sem precisar de
blocklist.

Recuperação de senha usa o mesmo desenho de token (opaco, só o hash no banco),
com duas origens: 'admin' (o administrador gera o link e entrega) e 'email' (o
usuário pede e recebe). Ver db/init/07_seguranca_usuarios.sql.

São duas formas de PROVAR quem a pessoa é, e um cadastro só: a senha conferida
aqui (PolícIA) e a senha conferida pela API da PF (ver funcoes/api_pf_utils.py).
Quem entra pela API-PF tem o cadastro criado/atualizado aqui a partir das claims
do token — o registro local é a fonte da verdade sobre aprovação, delegacia e
privilégio de administrador, não a PF. Ver db/init/35_autenticacao_api_pf.sql.
"""

import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import bcrypt
import psycopg
from fastapi import Cookie, Depends, HTTPException, Request, Response

from funcoes.auditoria_acesso import definir_usuario_da_requisicao
from funcoes.db_utils import get_connection

if TYPE_CHECKING:  # só para a anotação de sincronizar_usuario_api_pf
    # Import adiado de propósito: este módulo é a camada de sessão, comum aos dois caminhos de
    # entrada, e não deve depender em tempo de execução do cliente de um provedor específico.
    from funcoes.api_pf_utils import IdentidadeApiPf

COOKIE_SESSAO = "analyser_sessao"
DURACAO_SESSAO = timedelta(hours=12)
# Sessão deslizante: enquanto o usuário está usando o sistema, cada requisição válida empurra a
# expiração para frente (só desconecta após DURACAO_SESSAO de INATIVIDADE). Para não escrever no
# banco a cada request, a renovação é limitada a ~1x por RENOVAR_SESSAO_A_CADA por sessão.
RENOVAR_SESSAO_A_CADA = timedelta(hours=1)
TAMANHO_MINIMO_SENHA = 8

# Limite de tentativas de login (ver db/init/07_seguranca_usuarios.sql). Duas contagens sobre a mesma
# janela deslizante: por LOGIN, contra força bruta em uma conta específica; por IP, contra a
# varredura que troca de login a cada palpite (e que passaria despercebida pela contagem por login).
# O limite por IP é bem mais folgado porque uma delegacia inteira sai pelo mesmo IP de saída — e um
# limite apertado ali derrubaria todo mundo junto.
JANELA_TENTATIVAS = timedelta(minutes=15)
MAX_TENTATIVAS_LOGIN = 5
MAX_TENTATIVAS_IP = 30
# Por quanto tempo a trilha de tentativas fica guardada depois de já não contar mais para o
# bloqueio — é o que permite responder "tentaram entrar como fulano 40 vezes na madrugada".
RETENCAO_TENTATIVAS = timedelta(days=90)

# Throttle do AUTOcadastro (rota pública). O bloqueio de login protege a porta de entrada, mas
# o POST /api/auth/cadastro respondia "já existe esse login/e-mail" sem nenhum limite — um
# anônimo podia varrer a lista inteira de logins e e-mails do sistema. O limite é por IP e
# bem folgado porque uma delegacia inteira cadastra gente atrás do mesmo IP de saída, e o
# cadastro legítimo é um evento raro (uma pessoa, uma vez).
JANELA_CADASTROS = timedelta(hours=1)
MAX_CADASTROS_IP = 15
# Trilha curta: diferente das tentativas de login (90 dias, valor de auditoria), a linha de
# cadastro só existe para a janela deslizante — vence e sai.
RETENCAO_CADASTROS = timedelta(days=7)

# Validade do link de redefinição. O do e-mail é curto porque chega sozinho na caixa do usuário;
# o do admin é longo porque ele pode gerar agora e repassar o link só quando encontrar a pessoa.
VALIDADE_TOKEN_EMAIL = timedelta(hours=2)
VALIDADE_TOKEN_ADMIN = timedelta(hours=24)
# Intervalo mínimo entre dois pedidos de "esqueci minha senha" do mesmo usuário — evita que alguém
# use o formulário público para inundar a caixa de e-mail de outra pessoa.
INTERVALO_MINIMO_PEDIDO = timedelta(minutes=2)

# Validação deliberadamente frouxa (tem que ter algo@algo.algo, sem espaço): a única garantia real
# de que um endereço existe é o e-mail chegar, e regex de e-mail "completa" só cria falso negativo.
RE_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# Perfis de usuário: o que define as capacidades de cada um. IN_ADMIN continua existindo no banco
# como espelho de 'administrador' (escrito em sincronia na criação/edição); as demais capacidades
# são calculadas aqui, num único lugar, e somadas a todo dict de usuário que sai do banco
# (autenticar, validar_sessao, obter_usuario, listar_usuarios).
PERFIL_PADRAO = "analista"
PERFIS_VALIDOS = ("analista", "inteligencia", "agente_inteligencia", "auditoria", "administrador")


def _capacidades(perfil: str | None) -> dict:
    """Deriva as capacidades de um perfil. Valor desconhecido/em branco cai no padrão (analista):
    assim uma linha antiga, ou um valor digitado errado no banco, não vira privilégio indevido."""
    perfil = (perfil or PERFIL_PADRAO).strip().lower()
    if perfil not in PERFIS_VALIDOS:
        perfil = PERFIL_PADRAO
    return {
        "tp_perfil": perfil,
        "in_admin": perfil == "administrador",
        # vê (lê) os inquéritos/casos de todas as delegacias — convergência global
        "ve_todas_delegacias": perfil in ("inteligencia", "auditoria", "administrador"),
        # ESCREVE em inquérito de qualquer delegacia sem ser criador/permitido: chefia de
        # inteligência e administrador. Auditoria é leitura global DE PROPÓSITO — ver A1:
        # antes, ve_todas_delegacias liberava leitura E escrita, e a auditoria virava
        # mutadora universal (apelido, transcrição, entidades, marcadores, jobs…).
        "edita_todas_delegacias": perfil in ("inteligencia", "administrador"),
        # pode importar como caso de inteligência (inquérito com a flag) — chefia de
        # inteligência e inteligência (agente); administrador não vê o seletor
        "pode_caso_inteligencia": perfil in ("inteligencia", "agente_inteligencia"),
        # enxerga a tela de Auditoria no menu (ao lado de Convergência)
        "pode_auditoria": perfil in ("auditoria", "administrador"),
    }


def enriquecer_usuario(usuario: dict) -> dict:
    """Adiciona TP_PERFIL e as capacidades derivadas a um dict de usuário. É o único ponto que
    calcula as capacidades: todo dict que sai do banco passa por aqui."""
    usuario.update(_capacidades(usuario.get("tp_perfil")))
    return usuario


def chegou_https(request: Request) -> bool:
    """True se a requisição chegou por HTTPS ao nginx, lida de X-Forwarded-Proto.

    O valor vem do $scheme do nginx (nginx/default.conf), que SEMPRE sobrescreve o cabeçalho —
    e é seguro confiar nele porque a API não tem porta publicada no host: o único caminho até
    ela é o proxy (mesma premissa de _ip_cliente em rotas/auth.py). Se um dia a API for exposta
    direto, isto passa a ser forjável, e o flag Secure do cookie junto.

    Secure é condicional (e não fixo em True) porque o sistema continua acessível por HTTP em
    IPs internos — com Secure incondicional, o navegador descartaria o cookie lá e o login
    deixaria de funcionar nessas origens. Em quem usa HTTPS (o FQDN, hoje redirecionado no
    nginx) o cookie nasce Secure e não vaza por canal puro."""
    proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    return proto == "https"


def _definir_cookie_sessao(response: Response, token: str, request: Request) -> None:
    """Grava o cookie de sessão (httpOnly) com validade renovada a partir de agora — usado tanto ao
    abrir a sessão quanto para deslizá-la a cada requisição autenticada."""
    response.set_cookie(
        COOKIE_SESSAO, token,
        max_age=int(DURACAO_SESSAO.total_seconds()),
        httponly=True, samesite="lax", path="/",
        secure=chegou_https(request),
    )


def _hash_senha(senha: str) -> str:
    return bcrypt.hashpw(senha.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verificar_senha(senha: str, hash_armazenado: str | None) -> bool:
    """Confere a senha contra o hash guardado. Hash nulo (usuário da API-PF que ainda não criou
    a senha do PolícIA) e hash em formato irreconhecível valem como senha errada, nunca certa."""
    if not hash_armazenado:
        return False
    try:
        return bcrypt.checkpw(senha.encode("utf-8"), hash_armazenado.encode("utf-8"))
    except ValueError:
        return False


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _agora_utc() -> datetime:
    """"Agora" em UTC NAIVE — a convenção do resto do código (iped_utils et al.) e a única que
    faz sentido em colunas TIMESTAMP SEM fuso como DT_EXPIRACAO de sessão/token.

    Um datetime AWARE escrito nessas colunas é convertido pelo fuso da SESSÃO Postgres: o
    banco compara depois com now(), que também segue o fuso da sessão — enquanto toda sessão
    for UTC (db_utils fixa TimeZone=UTC em toda conexão, ver _aplicar_timeouts) as duas
    convenções coincidem; com um datetime ingênuo aqui coincidem SEMPRE. Um valor aware
    gravado num cluster com fuso America/Sao_Paulo, por exemplo, nasceria 3 h no passado e a
    sessão expiraria na hora de abrir."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _normalizar_email(email: str | None) -> str | None:
    """Normaliza um e-mail para gravação (minúsculo, sem espaços nas pontas); devolve None se vazio.

    Levanta ValueError se não parecer um endereço — vale a checagem porque um e-mail com erro de
    digitação só apareceria como problema muito depois, na hora em que o usuário precisa dele para
    recuperar a senha e o link não chega.
    """
    email = (email or "").strip().lower()
    if not email:
        return None
    if not RE_EMAIL.match(email):
        raise ValueError("E-mail inválido.")
    return email


def criar_usuario(
    login: str, nome: str, senha: str, perfil: str = PERFIL_PADRAO, aprovado: bool = True,
    email: str | None = None, id_delegacia: int | None = None,
) -> int:
    """Cria um usuário; levanta ValueError se faltar campo, senha curta, login/e-mail duplicado
    ou perfil inválido.

    aprovado=False é usado no autocadastro (o usuário consegue entrar mas fica sem acesso
    às funcionalidades até um administrador aprovar); usuário criado por um admin já nasce
    aprovado — é a própria criação que já é a aprovação.

    email é opcional: sem ele o usuário só recupera a senha pelo caminho do administrador.
    """
    login = (login or "").strip().lower()
    nome = (nome or "").strip()
    email = _normalizar_email(email)
    capacidades = _capacidades(perfil)
    perfil = capacidades["tp_perfil"]
    admin = capacidades["in_admin"]
    if not login or not nome:
        raise ValueError("Login e nome são obrigatórios.")
    if len(senha or "") < TAMANHO_MINIMO_SENHA:
        raise ValueError(f"A senha precisa ter pelo menos {TAMANHO_MINIMO_SENHA} caracteres.")

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM seguranca.usr_tb01_usuarios WHERE no_login = %s", (login,))
            if cur.fetchone():
                raise ValueError("Já existe um usuário com esse login.")
            if email:
                cur.execute("SELECT 1 FROM seguranca.usr_tb01_usuarios WHERE lower(no_email) = %s", (email,))
                if cur.fetchone():
                    raise ValueError("Já existe um usuário com esse e-mail.")
            try:
                cur.execute(
                    """
                    INSERT INTO seguranca.usr_tb01_usuarios
                        (no_login, no_usuario, tx_senha_hash, in_admin, tp_perfil, in_aprovado, no_email, id_delegacia)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id_usuario
                    """,
                    (login, nome, _hash_senha(senha), admin, perfil, aprovado, email, id_delegacia),
                )
                id_usuario = cur.fetchone()[0]
            except psycopg.errors.UniqueViolation:
                # dois cadastros simultâneos passaram juntos nos SELECTs acima: quem perdeu o
                # empate recebe o mesmo erro de sempre, não um 500 cru do banco
                raise ValueError("Já existe um usuário com esse login ou e-mail.")
        conn.commit()
    return id_usuario


def sincronizar_usuario_api_pf(identidade: "IdentidadeApiPf") -> dict:
    """Cria (ou atualiza) o cadastro do PolícIA de quem acabou de se autenticar pela API-PF.

    É o que dá existência local a um usuário da PF: aprovação, delegacia, histórico e uma linha
    na tela Usuários — sem que a senha dele jamais passe por este banco (TX_SENHA_HASH nasce
    NULL e só é preenchido quando a pessoa cria a senha daqui, no primeiro acesso).

    O casamento é pelo **login** normalizado: se a pessoa já tem cadastro aqui (criada à mão por
    um administrador, ou vinda de um login anterior pela PF), é aquela linha que é atualizada,
    nunca uma segunda.

    O que a PF manda e o que é decisão nossa:

    - **Nome** é regravado a cada login: quanto a quem a pessoa é, a PF é a fonte da verdade.
    - **E-mail** só entra se a linha ainda não tiver um — trocá-lo é trocar a credencial de
      recuperação de senha, e isso continua sendo um ato do próprio usuário (POST /api/auth/email).
    - **IN_APROVADO, IN_ADMIN, IN_ATIVO, ID_DELEGACIA e a senha local NÃO são tocados** no
      reencontro. Uma credencial válida da PF prova identidade, não autorização a ver inquérito:
      quem foi desativado ou rebaixado aqui não volta ao sistema por logar de novo na PF.

    Um cadastro NOVO nasce igual ao do autocadastro: sem admin e **pendente de aprovação** de um
    administrador (ver exigir_aprovado).
    """
    login = (identidade.no_login or "").strip().lower()
    nome = (identidade.no_usuario or "").strip()
    if not login or not nome:
        raise ValueError("A API-PF não devolveu login e nome do usuário.")

    # E-mail vindo de fora não pode derrubar o login por causa do índice único: se estiver
    # inválido ou já pertencer a outro usuário, entra como NULL e o usuário/admin resolve na tela.
    try:
        email = _normalizar_email(identidade.no_email)
    except ValueError:
        email = None

    with get_connection() as conn:
        with conn.cursor() as cur:
            if email:
                cur.execute(
                    """
                    SELECT 1 FROM seguranca.usr_tb01_usuarios
                    WHERE lower(no_email) = %s AND no_login <> %s
                    """,
                    (email, login),
                )
                if cur.fetchone():
                    email = None

            cur.execute(
                """
                INSERT INTO seguranca.usr_tb01_usuarios AS alvo
                    (no_login, no_usuario, tx_senha_hash, in_admin, in_aprovado, no_email, co_provedor)
                VALUES (%s, %s, NULL, false, false, %s, 'API_PF')
                ON CONFLICT (no_login) DO UPDATE
                    SET no_usuario = EXCLUDED.no_usuario,
                        no_email = COALESCE(alvo.no_email, EXCLUDED.no_email)
                RETURNING id_usuario
                """,
                (login, nome, email),
            )
            id_usuario = cur.fetchone()[0]
        conn.commit()

    usuario = obter_usuario(id_usuario)
    if usuario is None:  # o INSERT acabou de garantir a linha
        raise RuntimeError("Falha ao sincronizar o cadastro do usuário.")
    return usuario


def definir_senha_inicial(id_usuario: int, senha_nova: str, id_delegacia: int | None) -> None:
    """Primeiro acesso de quem entrou pela API-PF: cria a senha do PolícIA e grava a delegacia.

    Sem senha atual porque não existe uma — e é justamente por isso que o UPDATE carrega
    `tx_senha_hash IS NULL`: sem essa condição, esta seria uma rota para trocar a senha de
    qualquer conta sem conhecer a anterior. Quando a linha já tem senha, levanta LookupError e
    quem chamou responde 409.

    A senha é do PolícIA, não da PF: é ela que permite entrar quando a rede da PF estiver fora.
    """
    if len(senha_nova or "") < TAMANHO_MINIMO_SENHA:
        raise ValueError(f"A senha precisa ter pelo menos {TAMANHO_MINIMO_SENHA} caracteres.")

    with get_connection() as conn:
        with conn.cursor() as cur:
            if id_delegacia is not None:
                cur.execute(
                    "SELECT 1 FROM seguranca.delegacias WHERE id_delegacia = %s", (id_delegacia,)
                )
                if not cur.fetchone():
                    raise ValueError("Delegacia inválida.")
            cur.execute(
                """
                UPDATE seguranca.usr_tb01_usuarios
                SET tx_senha_hash = %s, id_delegacia = COALESCE(%s, id_delegacia)
                WHERE id_usuario = %s AND tx_senha_hash IS NULL
                """,
                (_hash_senha(senha_nova), id_delegacia, id_usuario),
            )
            if cur.rowcount == 0:
                raise LookupError("Sua senha do PolícIA já foi criada.")
        conn.commit()


def definir_email(id_usuario: int, email: str | None) -> None:
    """Grava (ou limpa, com email vazio) o e-mail de recuperação do próprio usuário.

    Levanta ValueError se o endereço for inválido ou já pertencer a outro usuário.
    """
    email = _normalizar_email(email)
    with get_connection() as conn:
        with conn.cursor() as cur:
            if email:
                cur.execute(
                    "SELECT 1 FROM seguranca.usr_tb01_usuarios WHERE lower(no_email) = %s AND id_usuario <> %s",
                    (email, id_usuario),
                )
                if cur.fetchone():
                    raise ValueError("Já existe um usuário com esse e-mail.")
            cur.execute(
                "UPDATE seguranca.usr_tb01_usuarios SET no_email = %s WHERE id_usuario = %s",
                (email, id_usuario),
            )
        conn.commit()


def _normalizar_login(login: str | None) -> str:
    return (login or "").strip().lower()[:60]


# Duas contagens de falhas sobre a mesma janela deslizante, numa consulta só.
#
# A contagem por LOGIN ignora tudo que veio antes do último login bem-sucedido daquele mesmo login
# (CTE `marco`): quem acabou de entrar não deve carregar as falhas de antes: senha digitada errado
# três vezes de manhã não pode somar com duas à tarde e trancar quem sabe a senha. A contagem por
# IP não tem esse alívio de propósito — senão um atacante que conheça UMA credencial válida
# zeraria o contador do IP a cada acerto e varreria o resto à vontade.
#
# A espera devolvida é o tempo até a falha MAIS ANTIGA da janela envelhecer, que é quando a
# contagem volta a ficar abaixo do limite. Como tentativa bloqueada não é gravada (ver
# registrar_tentativa_login), esse instante não é empurrado para frente por quem insiste.
_SQL_FALHAS_RECENTES = """
    WITH janela AS (
        SELECT now() - %(janela)s::interval AS desde
    ),
    marco AS (
        SELECT coalesce(max(t.dt_tentativa), '-infinity'::timestamp) AS dt_sucesso
        FROM seguranca.usr_tb04_tentativas_login t, janela
        WHERE t.no_login = %(login)s AND t.in_sucesso AND t.dt_tentativa > janela.desde
    )
    SELECT
        count(*) FILTER (WHERE t.no_login = %(login)s AND t.dt_tentativa > marco.dt_sucesso)
            AS falhas_login,
        ceil(extract(epoch FROM (
            min(t.dt_tentativa) FILTER (WHERE t.no_login = %(login)s AND t.dt_tentativa > marco.dt_sucesso)
            + %(janela)s::interval - now()
        )))::bigint AS espera_login,
        count(*) FILTER (WHERE %(ip)s::text IS NOT NULL AND t.co_ip = %(ip)s)
            AS falhas_ip,
        ceil(extract(epoch FROM (
            min(t.dt_tentativa) FILTER (WHERE %(ip)s::text IS NOT NULL AND t.co_ip = %(ip)s)
            + %(janela)s::interval - now()
        )))::bigint AS espera_ip
    FROM seguranca.usr_tb04_tentativas_login t, janela, marco
    WHERE NOT t.in_sucesso AND t.dt_tentativa > janela.desde
"""


def segundos_bloqueio_login(login: str, ip: str | None) -> int:
    """Segundos que ainda faltam para este login/IP poder tentar de novo; 0 se não está bloqueado.

    Chame ANTES de conferir a senha: o ponto é não gastar bcrypt (nem dar mais um palpite) para
    quem já estourou o limite.
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                _SQL_FALHAS_RECENTES,
                {"login": _normalizar_login(login), "ip": ip, "janela": JANELA_TENTATIVAS},
            )
            falhas_login, espera_login, falhas_ip, espera_ip = cur.fetchone()

    esperas = []
    if falhas_login >= MAX_TENTATIVAS_LOGIN:
        esperas.append(espera_login or 0)
    if falhas_ip >= MAX_TENTATIVAS_IP:
        esperas.append(espera_ip or 0)
    # max(): estourando os dois limites, vale o que soltar por último
    return max((int(e) for e in esperas), default=0)


def registrar_tentativa_login(login: str, ip: str | None, sucesso: bool) -> None:
    """Grava uma tentativa de login (sucesso ou falha) para a contagem e para a auditoria.

    Só é chamada para tentativas que de fato conferiram a senha — tentativa recusada por bloqueio
    NÃO entra, senão quem fica martelando empurraria a janela para frente sozinho e o bloqueio
    nunca expiraria (o atacante viraria dono do destravamento da conta alheia).
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO seguranca.usr_tb04_tentativas_login (no_login, co_ip, in_sucesso)
                VALUES (%s, %s, %s)
                """,
                (_normalizar_login(login), ip, sucesso),
            )
            if sucesso:
                # expurgo oportunista, preso ao evento raro (login que dá certo) em vez de rodar a
                # cada palpite errado — que é justamente o caminho que precisa ficar barato
                cur.execute(
                    "DELETE FROM seguranca.usr_tb04_tentativas_login WHERE dt_tentativa < now() - %s",
                    (RETENCAO_TENTATIVAS,),
                )
        conn.commit()


def segundos_bloqueio_cadastro(ip: str | None) -> int:
    """Segundos que faltam para este IP poder pedir outro cadastro; 0 se dentro do limite.

    A resposta "já existe esse login/e-mail" da rota de cadastro entrega informação a um
    anônimo (quem tem conta), então ela não pode ser feita sem limite: cada tentativa que
    chegou a conferir duplicidade conta aqui, sucesso ou falha. Mesma janela deslizante do
    login, com margem larga — quem estoura é varredura, não delegacia cadastrando gente."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT count(*),
                       ceil(extract(epoch FROM (
                           min(dt_tentativa) + %s::interval - now()
                       )))::bigint
                FROM seguranca.usr_tb09_tentativas_cadastro
                WHERE co_ip IS NOT DISTINCT FROM %s AND dt_tentativa > now() - %s::interval
                """,
                (JANELA_CADASTROS, ip, JANELA_CADASTROS),
            )
            total, espera = cur.fetchone()
    if total >= MAX_CADASTROS_IP:
        return max(0, int(espera or 0))
    return 0


def registrar_tentativa_cadastro(ip: str | None, sucesso: bool) -> None:
    """Grava uma tentativa de autocadastro (criou conta ou esbarrou em duplicidade) para a
    contagem da janela. O expurgo do que venceu junto ao evento raro espelha
    registrar_tentativa_login — a cada palpite errado o INSERT tem que ficar barato."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO seguranca.usr_tb09_tentativas_cadastro (co_ip, in_sucesso) VALUES (%s, %s)",
                (ip, sucesso),
            )
            if sucesso:
                cur.execute(
                    "DELETE FROM seguranca.usr_tb09_tentativas_cadastro WHERE dt_tentativa < now() - %s",
                    (RETENCAO_CADASTROS,),
                )
        conn.commit()


def autenticar(login: str, senha: str) -> dict | None:
    """Confere login/senha do PolícIA; devolve o usuário (sem o hash) ou None se inválido/inativo.

    Quem veio da API-PF e ainda não criou a senha daqui tem TX_SENHA_HASH nulo e cai no mesmo
    None (ver _verificar_senha): a conta existe, mas não tem senha local para conferir — o
    caminho dela é a aba API-PF da tela de login.
    """
    login = (login or "").strip().lower()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id_usuario, no_login, no_usuario, tx_senha_hash, in_admin, in_ativo, in_aprovado, tp_perfil
                FROM seguranca.usr_tb01_usuarios WHERE no_login = %s
                """,
                (login,),
            )
            row = cur.fetchone()
            if row is None or not row[5] or not _verificar_senha(senha, row[3]):
                return None
            cur.execute(
                "UPDATE seguranca.usr_tb01_usuarios SET dt_ultimo_login = now() WHERE id_usuario = %s",
                (row[0],),
            )
        conn.commit()
    return enriquecer_usuario(
        {"id_usuario": row[0], "no_login": row[1], "no_usuario": row[2], "tp_perfil": row[7], "in_aprovado": row[6]}
    )


def definir_senha(id_usuario: int, senha_nova: str) -> None:
    """Troca a senha de um usuário; levanta ValueError se a senha nova for curta demais.

    Na mesma transação, todas as sessões do usuário são encerradas: se a senha foi trocada, os
    identificadores de sessão emitidos sob a senha anterior deixam de valer — inclusive um
    identificador que alguém tenha interceptado. Quem quiser continuar usa precisa de sessão nova.
    """
    if len(senha_nova or "") < TAMANHO_MINIMO_SENHA:
        raise ValueError(f"A senha precisa ter pelo menos {TAMANHO_MINIMO_SENHA} caracteres.")
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE seguranca.usr_tb01_usuarios SET tx_senha_hash = %s WHERE id_usuario = %s",
                (_hash_senha(senha_nova), id_usuario),
            )
            cur.execute("DELETE FROM seguranca.usr_tb02_sessoes WHERE id_usuario = %s", (id_usuario,))
        conn.commit()


def obter_usuario(id_usuario: int) -> dict | None:
    """Devolve os dados de um usuário por id (sem o hash da senha), ou None se não existir."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT u.id_usuario, u.no_login, u.no_usuario, u.no_email, u.in_admin, u.in_ativo, u.in_aprovado,
                       u.id_delegacia, d.no_delegacia, u.co_provedor, u.tp_perfil,
                       (u.tx_senha_hash IS NULL) AS senha_local_pendente
                FROM seguranca.usr_tb01_usuarios u
                LEFT JOIN seguranca.delegacias d ON d.id_delegacia = u.id_delegacia
                WHERE u.id_usuario = %s
                """,
                (id_usuario,),
            )
            row = cur.fetchone()
            colunas = [d.name for d in cur.description]
    return enriquecer_usuario(dict(zip(colunas, row))) if row else None


def usuario_para_recuperacao(login_ou_email: str) -> dict | None:
    """Acha o usuário de um pedido de "esqueci minha senha", aceitando login OU e-mail.

    Aceita os dois porque quem esqueceu a senha frequentemente também não lembra se cadastrou
    login ou e-mail. Devolve None se não achar ou o usuário estiver inativo — quem chama tem que
    responder a mesma coisa nos dois casos, para o formulário público não virar um verificador de
    quem tem conta no sistema.
    """
    termo = (login_ou_email or "").strip().lower()
    if not termo:
        return None
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id_usuario, no_login, no_usuario, no_email
                FROM seguranca.usr_tb01_usuarios
                WHERE (no_login = %s OR lower(no_email) = %s) AND in_ativo
                """,
                (termo, termo),
            )
            row = cur.fetchone()
    if row is None:
        return None
    return {"id_usuario": row[0], "no_login": row[1], "no_usuario": row[2], "no_email": row[3]}


def pedido_redefinicao_recente(id_usuario: int) -> bool:
    """Diz se já houve um pedido por e-mail para este usuário há menos de INTERVALO_MINIMO_PEDIDO."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1 FROM seguranca.usr_tb03_redefinicao_senha
                WHERE id_usuario = %s AND tp_origem = 'email' AND dt_criacao > now() - %s
                LIMIT 1
                """,
                (id_usuario, INTERVALO_MINIMO_PEDIDO),
            )
            return cur.fetchone() is not None


def horas_validade_token(origem: str) -> int:
    """Validade do token em horas inteiras, para compor o texto do e-mail sem duplicar a constante."""
    validade = VALIDADE_TOKEN_ADMIN if origem == "admin" else VALIDADE_TOKEN_EMAIL
    return int(validade.total_seconds() // 3600)


def criar_token_redefinicao(id_usuario: int, origem: str) -> tuple[str, datetime]:
    """Cria um token de redefinição; devolve (token em texto puro, expiração).

    Como em criar_sessao, o texto puro só existe aqui — no banco vai o hash. Tokens anteriores
    ainda não usados da MESMA origem são invalidados, para não deixar vários links válidos
    circulando (o último pedido é o que vale). A invalidação não cruza origens de propósito:
    quem conhece só o login e pede "esqueci minha senha" não pode derrubar o link gerado
    pessoalmente pelo administrador (tp_origem='admin'), que é justamente o caminho de
    recuperação de quem não tem e-mail cadastrado.
    """
    if origem not in ("admin", "email"):
        raise ValueError("Origem de redefinição inválida.")
    token = secrets.token_urlsafe(32)
    validade = VALIDADE_TOKEN_ADMIN if origem == "admin" else VALIDADE_TOKEN_EMAIL
    expiracao = _agora_utc() + validade  # naive-UTC: ver _agora_utc
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM seguranca.usr_tb03_redefinicao_senha "
                "WHERE id_usuario = %s AND tp_origem = %s AND dt_uso IS NULL",
                (id_usuario, origem),
            )
            cur.execute(
                """
                INSERT INTO seguranca.usr_tb03_redefinicao_senha
                    (id_token_hash, id_usuario, tp_origem, dt_expiracao)
                VALUES (%s, %s, %s, %s)
                """,
                (_hash_token(token), id_usuario, origem, expiracao),
            )
        conn.commit()
    return token, expiracao


def validar_token_redefinicao(token: str | None) -> dict | None:
    """Confere um token de redefinição sem consumi-lo — a tela usa isso para saber se o link
    ainda vale (e de quem é) antes de pedir a senha nova. Devolve None se inválido, expirado,
    já usado ou o usuário estiver inativo."""
    if not token:
        return None
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT u.id_usuario, u.no_login, u.no_usuario
                FROM seguranca.usr_tb03_redefinicao_senha r
                JOIN seguranca.usr_tb01_usuarios u ON u.id_usuario = r.id_usuario
                WHERE r.id_token_hash = %s AND r.dt_uso IS NULL AND r.dt_expiracao > now() AND u.in_ativo
                """,
                (_hash_token(token),),
            )
            row = cur.fetchone()
    if row is None:
        return None
    return {"id_usuario": row[0], "no_login": row[1], "no_usuario": row[2]}


def redefinir_senha_com_token(token: str | None, senha_nova: str) -> bool:
    """Troca a senha usando um token de redefinição. Devolve False se o token não servir (inválido,
    expirado, já usado, usuário inativo); levanta ValueError se a senha nova for curta demais.

    A senha é validada ANTES de marcar o token como usado, senão um erro de digitação na senha
    queimaria o link e obrigaria o usuário a pedir outro.

    Marcar o uso e trocar a senha acontece na mesma transação, e o UPDATE do token é o que garante
    o uso único: quem conseguir o rowcount é o único que segue adiante, mesmo com dois cliques
    simultâneos no mesmo link.

    Ao final, todas as sessões do usuário são encerradas — senha esquecida pode significar conta
    comprometida, e não faz sentido manter de pé uma sessão aberta com a senha antiga.
    """
    if not token:
        return False
    if len(senha_nova or "") < TAMANHO_MINIMO_SENHA:
        raise ValueError(f"A senha precisa ter pelo menos {TAMANHO_MINIMO_SENHA} caracteres.")
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE seguranca.usr_tb03_redefinicao_senha r
                SET dt_uso = now()
                WHERE r.id_token_hash = %s AND r.dt_uso IS NULL AND r.dt_expiracao > now()
                      AND EXISTS (
                          SELECT 1 FROM seguranca.usr_tb01_usuarios u
                          WHERE u.id_usuario = r.id_usuario AND u.in_ativo
                      )
                RETURNING r.id_usuario
                """,
                (_hash_token(token),),
            )
            row = cur.fetchone()
            if row is None:
                return False
            id_usuario = row[0]
            cur.execute(
                "UPDATE seguranca.usr_tb01_usuarios SET tx_senha_hash = %s WHERE id_usuario = %s",
                (_hash_senha(senha_nova), id_usuario),
            )
            cur.execute("DELETE FROM seguranca.usr_tb02_sessoes WHERE id_usuario = %s", (id_usuario,))
        conn.commit()
    return True


def criar_sessao(id_usuario: int) -> str:
    """Cria uma sessão nova; devolve o token em texto puro — só existe aqui, no banco fica só o hash."""
    token = secrets.token_urlsafe(32)
    expiracao = _agora_utc() + DURACAO_SESSAO  # naive-UTC: ver _agora_utc
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO seguranca.usr_tb02_sessoes (id_sessao_hash, id_usuario, dt_expiracao) VALUES (%s, %s, %s)",
                (_hash_token(token), id_usuario, expiracao),
            )
        conn.commit()
    return token


def validar_sessao(token: str | None) -> dict | None:
    """Devolve o usuário da sessão se o token for válido, não expirado e o usuário estiver ativo.

    Sessão deslizante: quando a sessão válida está a menos de RENOVAR_SESSAO_A_CADA de completar sua
    janela, empurra a expiração para now()+DURACAO_SESSAO. Assim um usuário ativo nunca é
    desconectado; só cai após DURACAO_SESSAO de inatividade. O gate fica no próprio UPDATE (em SQL,
    com now() do servidor) para não escrever a cada request e evitar confusão de fuso na comparação."""
    if not token:
        return None
    token_hash = _hash_token(token)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT u.id_usuario, u.no_login, u.no_usuario, u.in_admin, u.in_aprovado, u.tp_tema, u.no_email,
                       u.id_delegacia, d.no_delegacia, u.co_provedor, u.tp_perfil,
                       (u.tx_senha_hash IS NULL) AS senha_local_pendente
                FROM seguranca.usr_tb02_sessoes s
                JOIN seguranca.usr_tb01_usuarios u ON u.id_usuario = s.id_usuario
                LEFT JOIN seguranca.delegacias d ON d.id_delegacia = u.id_delegacia
                WHERE s.id_sessao_hash = %s AND s.dt_expiracao > now() AND u.in_ativo
                """,
                (token_hash,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            # renova só quando falta menos de (DURACAO - intervalo) — no máximo ~1 escrita/hora/sessão
            cur.execute(
                """
                UPDATE seguranca.usr_tb02_sessoes
                SET dt_expiracao = now() + %s
                WHERE id_sessao_hash = %s AND dt_expiracao > now()
                      AND dt_expiracao < now() + %s
                """,
                (DURACAO_SESSAO, token_hash, DURACAO_SESSAO - RENOVAR_SESSAO_A_CADA),
            )
        conn.commit()
    return enriquecer_usuario({
        "id_usuario": row[0], "no_login": row[1], "no_usuario": row[2],
        "in_aprovado": row[4], "tp_tema": row[5], "no_email": row[6],
        "id_delegacia": row[7], "no_delegacia": row[8],
        # Origem do cadastro e "ainda não tem senha do PolícIA" — é esta flag que faz o front
        # abrir a caixa bloqueante de primeiro acesso (web/js/utils/primeiro_acesso.js) para quem
        # entrou pela API-PF e fechou o navegador antes de terminar.
        "co_provedor": row[9], "tp_perfil": row[10], "senha_local_pendente": row[11],
    })


# Temas que a interface oferece. Espelha a lista TEMAS de web/js/utils/theme.js (que é quem monta
# o dropdown "Temas" do menu) — tema novo entra nos dois lugares, senão o POST /api/auth/tema
# recusa a escolha e ela não sobrevive ao reload.
TEMAS_VALIDOS = ("dark", "light")


def definir_tema(id_usuario: int, tema: str) -> None:
    """Salva a preferência de tema do usuário; levanta ValueError se não for um tema conhecido."""
    if tema not in TEMAS_VALIDOS:
        raise ValueError("Tema inválido (use um destes: " + ", ".join(TEMAS_VALIDOS) + ").")
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE seguranca.usr_tb01_usuarios SET tp_tema = %s WHERE id_usuario = %s", (tema, id_usuario))
        conn.commit()


def encerrar_sessao(token: str | None) -> None:
    if not token:
        return
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM seguranca.usr_tb02_sessoes WHERE id_sessao_hash = %s", (_hash_token(token),))
        conn.commit()


def listar_usuarios() -> list[dict]:
    query = """
        SELECT u.id_usuario, u.no_login, u.no_usuario, u.no_email, u.in_admin, u.in_ativo, u.in_aprovado,
               u.dt_cadastro, u.dt_ultimo_login, u.id_delegacia, d.no_delegacia, u.tp_perfil, u.in_ia_ativa
        FROM seguranca.usr_tb01_usuarios u
        LEFT JOIN seguranca.delegacias d ON d.id_delegacia = u.id_delegacia
        ORDER BY u.no_usuario
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query)
            colunas = [d.name for d in cur.description]
            return [enriquecer_usuario(dict(zip(colunas, linha))) for linha in cur.fetchall()]


def listar_usuarios_basico() -> list[dict]:
    """Lista só id/nome/login dos usuários ativos e aprovados — usado para popular seletores de
    "adicionar usuário" (ex.: permissões de inquérito) sem expor a tela de administração completa."""
    query = """
        SELECT id_usuario, no_login, no_usuario
        FROM seguranca.usr_tb01_usuarios
        WHERE in_ativo AND in_aprovado
        ORDER BY no_usuario
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query)
            colunas = [d.name for d in cur.description]
            return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


def definir_ativo(id_usuario: int, ativo: bool) -> bool:
    """Ativa/desativa um usuário; devolve False se o id não existir. Desativar não derruba
    sessões já abertas na hora — elas só param de validar na próxima checagem (validar_sessao
    já filtra por in_ativo), o que é aceitável dado o TTL curto da sessão."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE seguranca.usr_tb01_usuarios SET in_ativo = %s WHERE id_usuario = %s",
                (ativo, id_usuario),
            )
            atualizado = cur.rowcount > 0
        conn.commit()
    return atualizado


def definir_aprovado(id_usuario: int, aprovado: bool) -> bool:
    """Aprova/revoga o acesso de um usuário autocadastrado; devolve False se o id não existir."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE seguranca.usr_tb01_usuarios SET in_aprovado = %s WHERE id_usuario = %s",
                (aprovado, id_usuario),
            )
            atualizado = cur.rowcount > 0
        conn.commit()
    return atualizado


def exigir_login(request: Request, response: Response, analyser_sessao: str | None = Cookie(default=None)) -> dict:
    """Dependency do FastAPI: exige sessão válida, devolve o usuário logado (aprovado ou não —
    usada só nas rotas que um usuário pendente de aprovação ainda precisa acessar: ver seu
    próprio status, trocar senha, sair).

    Além de validar (e deslizar a sessão no banco), reemite o cookie a cada requisição autenticada
    para que a validade no navegador acompanhe — sem isso o cookie ainda expiraria em 12h contadas
    do login, desconectando um usuário ativo mesmo com a sessão renovada no servidor.

    Também publica o usuário no escopo da requisição (ver auditoria_acesso), para quem precisa
    saber quem está chamando sem receber id_usuario na assinatura."""
    usuario = validar_sessao(analyser_sessao)
    if usuario is None:
        raise HTTPException(status_code=401, detail="Não autenticado.")
    _definir_cookie_sessao(response, analyser_sessao, request)
    # Publica quem é para o resto da requisição: é assim que a IA descobre, lá no fundo
    # (funcoes/ia_provedores.py), que este usuário tem configuração de IA própria.
    definir_usuario_da_requisicao(usuario)
    return usuario


def exigir_aprovado(usuario: dict = Depends(exigir_login)) -> dict:
    """Dependency do FastAPI: exige sessão válida E aprovação de um administrador. É a que
    protege as funcionalidades de verdade — quem se autocadastrou passa em exigir_login mas
    não passa aqui até ser aprovado."""
    if not usuario["in_aprovado"]:
        raise HTTPException(status_code=403, detail="Cadastro pendente de aprovação do administrador.")
    return usuario


def exigir_admin(usuario: dict = Depends(exigir_aprovado)) -> dict:
    """Dependency do FastAPI: exige sessão válida, aprovada, E administrador."""
    if not usuario["in_admin"]:
        raise HTTPException(status_code=403, detail="Requer administrador.")
    return usuario


def exigir_auditoria(usuario: dict = Depends(exigir_aprovado)) -> dict:
    """Dependency do FastAPI: exige sessão válida, aprovada, E perfil com acesso à Auditoria
    (auditoria ou administrador)."""
    if not usuario.get("pode_auditoria"):
        raise HTTPException(status_code=403, detail="Requer perfil de auditoria ou administrador.")
    return usuario


def exigir_caso_inteligencia(usuario: dict = Depends(exigir_aprovado)) -> dict:
    """Dependency do FastAPI: exige sessão válida, aprovada, E perfil de inteligência
    (chefia ou agente — os únicos que podem importar caso de inteligência)."""
    if not usuario.get("pode_caso_inteligencia"):
        raise HTTPException(status_code=403, detail="Este perfil não pode importar caso de inteligência.")
    return usuario

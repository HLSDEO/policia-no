"""Imagem do nó da mesa de inquérito — a foto que o investigador põe NO LUGAR da bolinha do
grafo (retrato do suspeito, foto do veículo apreendido, do local).

Vale para as duas camadas de nó que a mesa desenha, então o alvo é polimórfico (TP_NO/ID_NO,
mesmo padrão de TP_ORIGEM/ID_ORIGEM em ENT_TB01): ``canonica`` aponta ENT_TB05 e ``entidade``
aponta ENT_TB01. Uma imagem por nó — reenviar substitui a anterior (UNIQUE em TP_NO/ID_NO).

O que entra no banco NÃO é o arquivo enviado: toda imagem é reduzida a LADO_MAX px no lado maior
e reencodada em JPEG (ver ``normalizar_imagem``), o que traz uma foto de celular de 4 MB para
algumas dezenas de KB. É o que torna razoável guardar o binário em ENT_TB09_IMAGENS em vez de em
disco — a foto acompanha o inquérito no mesmo backup, e apagar o nó apaga a imagem junto, sem
órfão para varrer depois.
"""

import logging

from io import BytesIO

from PIL import Image, ImageOps

# Retrato tirado de iPhone chega em HEIC, que o navegador não exibe — mesmo plugin que a prévia
# de anexos usa (funcoes/investigacao_utils.py). Best-effort: sem o pacote, jpg/png seguem
# funcionando e o HEIC vira erro legível de "formato não reconhecido".
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:
    pass

from funcoes.db_utils import get_connection

logger = logging.getLogger(__name__)

TP_NO_VALIDOS = {"canonica", "entidade"}

# Lado máximo da imagem guardada. O nó tem ~44 px de diâmetro no zoom padrão, mas a mesa dá zoom
# de até 8x e o PNG exportado sai em 2x — 320 px mantém o rosto nítido nos dois casos sem passar
# de algumas dezenas de KB por foto.
LADO_MAX = 320
QUALIDADE_JPEG = 82

# Teto do que o navegador pode mandar. Bem abaixo do client_max_body_size do nginx (520 MB):
# a foto útil aqui é um retrato, e recusar cedo evita ler centenas de MB na memória para jogar fora depois.
TAMANHO_MAX_ENVIO = 20 * 1024 * 1024


def normalizar_imagem(dados: bytes) -> bytes:
    """Reduz a LADO_MAX px no lado maior e reencoda em JPEG. Levanta ValueError se o arquivo
    não for uma imagem que o Pillow abra.

    Duas correções que a foto de celular precisa: a orientação do EXIF (sem ela, retrato tirado
    deitado aparece de lado no grafo) e o achatamento da transparência sobre BRANCO — PNG com
    fundo transparente convertido direto para RGB vira um retângulo preto."""
    try:
        imagem = Image.open(BytesIO(dados))
        imagem = ImageOps.exif_transpose(imagem)
        if imagem.mode in ("RGBA", "LA", "P"):
            imagem = imagem.convert("RGBA")
            fundo = Image.new("RGB", imagem.size, (255, 255, 255))
            fundo.paste(imagem, mask=imagem.split()[-1])
            imagem = fundo
        elif imagem.mode != "RGB":
            imagem = imagem.convert("RGB")
        imagem.thumbnail((LADO_MAX, LADO_MAX))
        saida = BytesIO()
        imagem.save(saida, format="JPEG", quality=QUALIDADE_JPEG)
        return saida.getvalue()
    except Exception as exc:
        logger.info("Imagem de nó recusada: %s", exc)
        raise ValueError("Arquivo não reconhecido como imagem (use JPG, PNG, WEBP ou HEIC).")


def _validar_tp_no(tp_no: str) -> str:
    if tp_no not in TP_NO_VALIDOS:
        raise ValueError(f"Tipo de nó inválido: {tp_no!r}")
    return tp_no


def salvar_imagem_no(
    id_inquerito: int, tp_no: str, id_no: int, dados: bytes,
    nome_arquivo: str | None = None, id_usuario: int | None = None,
) -> dict:
    """Grava (ou substitui) a imagem de um nó da mesa; devolve o resumo que a tela usa."""
    _validar_tp_no(tp_no)
    if not dados:
        raise ValueError("Nenhum arquivo enviado.")
    if len(dados) > TAMANHO_MAX_ENVIO:
        raise ValueError(f"Imagem grande demais (máximo {TAMANHO_MAX_ENVIO // (1024 * 1024)} MB).")
    conteudo = normalizar_imagem(dados)
    nome_arquivo = (nome_arquivo or "")[:300] or None
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO investigacao.ent_tb09_imagens
                    (id_inquerito, tp_no, id_no, no_arquivo, tp_mime, nu_bytes, im_conteudo, id_usuario)
                VALUES (%s, %s, %s, %s, 'image/jpeg', %s, %s, %s)
                ON CONFLICT (tp_no, id_no) DO UPDATE
                    SET id_inquerito = EXCLUDED.id_inquerito,
                        no_arquivo   = EXCLUDED.no_arquivo,
                        tp_mime      = EXCLUDED.tp_mime,
                        nu_bytes     = EXCLUDED.nu_bytes,
                        im_conteudo  = EXCLUDED.im_conteudo,
                        id_usuario   = EXCLUDED.id_usuario,
                        dt_envio     = now()
                RETURNING id_imagem, nu_bytes, dt_envio
                """,
                (id_inquerito, tp_no, id_no, nome_arquivo, len(conteudo), conteudo, id_usuario),
            )
            id_imagem, nu_bytes, dt_envio = cur.fetchone()
        conn.commit()
    return {
        "id_imagem": id_imagem, "tp_no": tp_no, "id_no": id_no,
        "no_arquivo": nome_arquivo, "nu_bytes": nu_bytes, "dt_envio": dt_envio,
    }


def obter_imagem_no(tp_no: str, id_no: int) -> dict | None:
    """Binário da imagem de um nó (para a rota que devolve a foto). None se o nó não tem foto."""
    _validar_tp_no(tp_no)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT im_conteudo, tp_mime, dt_envio FROM investigacao.ent_tb09_imagens "
                "WHERE tp_no = %s AND id_no = %s",
                (tp_no, id_no),
            )
            linha = cur.fetchone()
    if linha is None:
        return None
    return {"conteudo": bytes(linha[0]), "tp_mime": linha[1], "dt_envio": linha[2]}


def remover_imagem_no(tp_no: str, id_no: int) -> bool:
    """Tira a foto do nó (ele volta a ser desenhado com a cor e o ícone do tipo)."""
    _validar_tp_no(tp_no)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM investigacao.ent_tb09_imagens WHERE tp_no = %s AND id_no = %s",
                (tp_no, id_no),
            )
            removido = cur.rowcount > 0
        conn.commit()
    return removido


def listar_imagens_inquerito(id_inquerito: int) -> list[dict]:
    """Quais nós do inquérito têm foto — só os metadados, sem o binário: vai junto com o grafo,
    a cada carga da mesa, e o front busca cada imagem uma vez pela rota própria (o binário no
    JSON do grafo custaria centenas de KB a cada refresh)."""
    query = """
        SELECT i.tp_no, i.id_no, i.no_arquivo, i.nu_bytes, i.dt_envio, u.no_usuario AS enviada_por
        FROM investigacao.ent_tb09_imagens i
        LEFT JOIN seguranca.usr_tb01_usuarios u ON u.id_usuario = i.id_usuario
        WHERE i.id_inquerito = %s
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (id_inquerito,))
            colunas = [d.name for d in cur.description]
            return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


def apagar_imagem_no(cur, tp_no: str, id_no: int) -> None:
    """Versão em cursor, para entrar na MESMA transação de quem apaga o nó (cascata manual:
    ENT_TB09 não tem FK para o nó, porque o alvo é polimórfico)."""
    cur.execute(
        "DELETE FROM investigacao.ent_tb09_imagens WHERE tp_no = %s AND id_no = %s",
        (tp_no, id_no),
    )


def transferir_imagem(cur, id_canonica_origem: int, id_canonica_destino: int) -> None:
    """Fusão de canônicas: a foto da origem passa para o destino se ele ainda não tiver uma;
    se tiver, a do destino prevalece e a da origem some junto com ela."""
    cur.execute(
        """
        UPDATE investigacao.ent_tb09_imagens
        SET id_no = %(destino)s
        WHERE tp_no = 'canonica' AND id_no = %(origem)s
          AND NOT EXISTS (
              SELECT 1 FROM investigacao.ent_tb09_imagens d
              WHERE d.tp_no = 'canonica' AND d.id_no = %(destino)s
          )
        """,
        {"origem": id_canonica_origem, "destino": id_canonica_destino},
    )
    apagar_imagem_no(cur, "canonica", id_canonica_origem)

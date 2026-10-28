"""Leitura de upload sem carregar na memória mais do que a rota aceita.

O padrão que existia antes daqui era `dados = arquivo.file.read()` seguido de
`if len(dados) > MAX: erro` — o corpo inteiro já estava alocado quando a checagem rodava. Quem
decide o tamanho do corpo não é a rota: é o `client_max_body_size` do nginx (520 MB hoje). Numa
rota que aceita 10 MB isso significa que qualquer um pode fazer o processo alocar 520 MB, e com
trinta analistas usando ao mesmo tempo o pico vira mais de 1 GB para nada.

Para arquivo que vai parar em disco (o .ufdr da restauração de extração), o certo é gravar em
blocos e nem chegar a ter os bytes na mão — ver investigacao_utils.restaurar_arquivo_ufdr_upload.
Este módulo é para o outro caso: a imagem pequena que precisa mesmo virar `bytes` porque será
decodificada (CLIP na comparação facial, reencode em JPEG na foto do nó da mesa).
"""

_BLOCO = 1024 * 1024


def _legivel(bytes_: int) -> str:
    """"10 MB" / "500 KB" — sem arredondar um limite de 500 KB para "0 MB"."""
    if bytes_ >= 1024 * 1024:
        return f"{bytes_ / (1024 * 1024):.0f} MB"
    return f"{bytes_ / 1024:.0f} KB"


def ler_limitado(fluxo, max_bytes: int, rotulo: str = "Arquivo") -> bytes:
    """Lê o upload em blocos e aborta assim que passa de `max_bytes`.

    Levanta ValueError (que as rotas já traduzem em 400) com a mensagem pronta para o usuário,
    tanto no caso de grande demais quanto no de vazio. As mensagens usam "<rótulo>: ..." em vez
    de concordar com o rótulo, para servirem a rótulo masculino e feminino."""
    partes: list[bytes] = []
    total = 0
    while True:
        bloco = fluxo.read(_BLOCO)
        if not bloco:
            break
        total += len(bloco)
        if total > max_bytes:
            raise ValueError(f"{rotulo}: tamanho acima do limite de {_legivel(max_bytes)}.")
        partes.append(bloco)
    if total == 0:
        raise ValueError(f"{rotulo}: arquivo vazio.")
    return b"".join(partes)

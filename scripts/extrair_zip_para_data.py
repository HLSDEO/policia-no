"""Expande um ZIP de prova na pasta de entrega (data/<DELEGACIA>/) — contorno para
ZIPs grandes demais para o upload pelo navegador, sem reiniciar nenhum serviço.

Por que existe: o upload de documento pelo navegador (POST /api/documentos/upload)
corta em UPLOAD_DOCUMENTO_MB por arquivo, e o nginx corta qualquer corpo em 520 MB.
A IMPORTAÇÃO (POST /api/investigacao/extrair-documento, botão Importar da aba
"Arquivo genérico") não tem limite nenhum — ela lê o que já está em data/. Então,
para um ZIP de GBs com job rodando (sem poder reiniciar a API para aplicar a rota
sem limite), o caminho é expandir aqui no host e importar pela tela.

Faz o mesmo que _salvar_documento_zip faria no servidor (só tipos suportados,
sem __MACOSX/ocultos/criptografados/vazios, sem zip slip, sufixo (2) em colisão),
menos os tetos de upload — que não se aplicam a arquivo já em disco.

Uso (no host onde está o ./data do compose):
    python3 scripts/extrair_zip_para_data.py <arquivo.zip> <pasta-data-DELEGACIA>

Ex.:
    python3 scripts/extrair_zip_para_data.py /tmp/prova.zip ./data/SE/
    # depois: Nova importação → Arquivo genérico → os arquivos já estão na lista

Só stdlib (zipfile/pathlib). Não fala com banco nem com container nenhum.
"""

import sys
import zipfile
from pathlib import Path

SUPORTADAS = {".pdf", ".csv", ".xls", ".xlsx", ".docx", ".txt", ".html", ".htm"}

_BLOCO = 1024 * 1024


def _nome_seguro(nome: str) -> str:
    """Igual ao servidor: só o nome-base, sem caminho do ZIP (anti zip slip)."""
    base = (nome or "").replace("\\", "/").split("/")[-1].strip()
    if not base or base.startswith("."):
        return ""
    if len(base) > 120:
        ext = Path(base).suffix
        base = base[: 120 - len(ext)] + ext
    return base


def _destino_unico(pasta: Path, nome: str) -> Path:
    raiz, ext = nome[: len(nome) - len(Path(nome).suffix)], Path(nome).suffix
    alvo = pasta / nome
    sufixo = 2
    while True:
        try:
            alvo.touch(exist_ok=False)
            return alvo
        except FileExistsError:
            if sufixo > 999:
                raise SystemExit(f"Arquivos demais com o nome {nome} na pasta.")
            alvo = pasta / f"{raiz} ({sufixo}){ext}"
            sufixo += 1


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("Uso: extrair_zip_para_data.py <arquivo.zip> <pasta-data-DELEGACIA>")
    zip_path = Path(sys.argv[1])
    pasta = Path(sys.argv[2])
    if not zip_path.is_file():
        raise SystemExit(f"ZIP não encontrado: {zip_path}")
    if not pasta.is_dir():
        raise SystemExit(f"Pasta de destino não existe: {pasta} (é data/<DELEGACIA>/ do compose)")

    salvos, ignorados, total = 0, 0, 0
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile:
        raise SystemExit(f"ZIP inválido ou corrompido: {zip_path}")
    with zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            if "__MACOSX" in info.filename.replace("\\", "/").split("/"):
                ignorados += 1
                continue
            nome = _nome_seguro(info.filename)
            if not nome or Path(nome).suffix.lower() not in SUPORTADAS:
                ignorados += 1
                continue
            if info.flag_bits & 0x1:  # criptografada
                ignorados += 1
                continue
            try:
                alvo = _destino_unico(pasta, nome)
                with open(alvo, "wb") as saida, zf.open(info) as fz:
                    escrito = 0
                    while True:
                        bloco = fz.read(_BLOCO)
                        if not bloco:
                            break
                        saida.write(bloco)
                        escrito += len(bloco)
                if escrito == 0:
                    alvo.unlink(missing_ok=True)
                    ignorados += 1
                    continue
            except (zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError):
                try:
                    alvo.unlink(missing_ok=True)
                except OSError:
                    pass
                ignorados += 1
                continue
            salvos += 1
            total += escrito
            if salvos % 100 == 0:
                print(f"  ... {salvos} arquivos ({total / 1e9:.1f} GB)", flush=True)

    print(f"OK: {salvos} documento(s) extraído(s) em {pasta} ({total / 1e6:.0f} MB).")
    if ignorados:
        print(f"Ignoradas {ignorados} entrada(s) (pastas, __MACOSX, tipo não suportado, vazia, cripto ou corrompida).")
    print("Na tela: Nova importacao (ou + Importar) -> Arquivo generico -> selecione e Importe.")


if __name__ == "__main__":
    main()

# resumo-noturno.ps1 — passe noturno do resumo por inquérito (Windows + Docker Desktop/WSL).
#
# Uso:
#   .\resumo-noturno.ps1                    # todos os inquéritos com extração concluída
#   .\resumo-noturno.ps1 -Inquerito 101     # só um inquérito (piloto / teste)
#   .\resumo-noturno.ps1 -Forcar            # regenera mesmo sem mudança
#   .\resumo-noturno.ps1 -DryRun            # lista o que seria processado, sem chamar a IA
#
# NÃO é mais preciso agendar isto: quem dispara o passe às 02:00 é a própria API
# (api/funcoes/agendador.py), que sobe com o container e vale igual em Windows e em Linux.
# Este script serve para rodar na mão (piloto, teste, forçar um inquérito).
#
# Se ainda assim você preferir o Agendador de Tarefas, dá para conviver — um passe completo daqui
# é registrado como a execução do dia e a thread não repete. Uma vez, como administrador:
#   schtasks /Create /TN "analyser-resumo-noturno" /TR "powershell -File C:\Git\analyser\scripts\resumo-noturno.ps1" /SC DAILY /ST 02:00
# E, para ficar só com ele, RESUMO_NOTURNO_AGENDADOR=0 no .env.
#
# Ver docs/resumo_noturno.md.
param(
  [int]$Inquerito = 0,
  [switch]$Forcar,
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$Dir = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Dir

$args = @()
if ($Inquerito -gt 0) { $args += "--inquerito"; $args += "$Inquerito" }
if ($Forcar) { $args += "--forcar" }
if ($DryRun) { $args += "--dry-run" }

& docker compose exec -T api-1 python /app/tools/resumo_noturno.py @args
exit $LASTEXITCODE

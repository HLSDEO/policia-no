# observar-reinicio.ps1 - vigia do botão "Reiniciar servidor" (tela Desempenho) para
# máquina SEM systemd (dev no Windows, Docker Desktop).
#
# A API roda dentro do container e não alcança o `docker compose`; ela só grava o pedido em
# .deploy/controle/reiniciar.req (pasta compartilhada com o host). Este script, rodando NO HOST,
# consome o pedido: roda `docker compose up -d`, espera a API voltar e escreve o resultado
# para a tela acompanhar. Equivale à path unit analyser-reiniciar@.path do systemd em produção.
#
# Uso (na raiz do repo, deixe o terminal aberto - rode UMA instância só):
#   powershell -ExecutionPolicy Bypass -File scripts\observar-reinicio.ps1
#
# Sem este script rodando, o botão mostra o comando para rodar na mão - mesmo efeito, sem
# automação. Ctrl+C para parar (o "coração" some e o botão volta a orientar o comando manual).

$ErrorActionPreference = "Stop"

$Repo = Split-Path -Parent $PSScriptRoot
$Controle = Join-Path $Repo ".deploy\controle"
$Req = Join-Path $Controle "reiniciar.req"
$Log = Join-Path $Controle "reiniciar.log"
$Resultado = Join-Path $Controle "reiniciar-resultado.json"
$Coracao = Join-Path $Controle "observador-reinicio.ok"
$TimeoutS = 180

function Escrever-Log([string]$linha) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -LiteralPath $Log -Value "[$ts] $linha" -Encoding UTF8
}

function Escrever-Resultado([bool]$ok, [string]$versao, [string]$erro) {
    $obj = @{
        ok = $ok
        versao = $versao
        erro = $(if ([string]::IsNullOrWhiteSpace($erro)) { $null } else { $erro })
        ts = (Get-Date -Format "yyyy-MM-ddTHH:mm:sszzzz")
    }
    $obj | ConvertTo-Json -Compress | Set-Content -LiteralPath $Resultado -Encoding UTF8
}

function Ler-Versao() {
    $arq = Join-Path $Repo "VERSION"
    if (Test-Path -LiteralPath $arq) {
        $v = (Get-Content -LiteralPath $arq -TotalCount 1).Trim()
        if ($v) { return $v }
    }
    return "desconhecida"
}

function Ler-Porto() {
    $envFile = Join-Path $Repo ".env"
    if (Test-Path -LiteralPath $envFile) {
        foreach ($linha in (Get-Content -LiteralPath $envFile)) {
            if ($linha -match "^WEB_PORT=(.*)$") {
                $p = $Matches[1].Trim()
                if ($p) { return $p }
            }
        }
    }
    return "8080"
}

function Esperar-Api([string]$health) {
    $fim = (Get-Date).AddSeconds($TimeoutS)
    while ((Get-Date) -lt $fim) {
        try {
            Invoke-WebRequest -Uri $health -UseBasicParsing -TimeoutSec 5 | Out-Null
            return $true
        } catch {
            Start-Sleep -Seconds 3
        }
    }
    return $false
}

New-Item -ItemType Directory -Force -Path $Controle | Out-Null
$Porto = Ler-Porto
$Health = "http://localhost:$Porto/api/health"
Write-Host "observar-reinicio: vigiando $Req (health: $Health). Ctrl+C para parar."

try {
    while ($true) {
        # coração: é isto que diz à API que há um vigia consumindo o pedido
        Set-Content -LiteralPath $Coracao -Value (Get-Date -Format "o") -Encoding UTF8

        if (Test-Path -LiteralPath $Req) {
            $solicitante = "?"
            try {
                $pedido = Get-Content -LiteralPath $Req -Raw | ConvertFrom-Json
                if ($pedido.solicitante) { $solicitante = $pedido.solicitante }
            } catch {
            }

            try {
                Escrever-Log "reiniciar: pedido de $solicitante - docker compose up -d..."
                Push-Location -LiteralPath $Repo
                try {
                    $saida = & docker compose up -d 2>&1
                    $codigo = $LASTEXITCODE
                } finally {
                    Pop-Location
                }
                Add-Content -LiteralPath $Log -Value ($saida -join [Environment]::NewLine) -Encoding UTF8

                $versao = Ler-Versao
                if ($codigo -ne 0) {
                    Escrever-Log "ERRO: docker compose up -d saiu com código $codigo"
                    Escrever-Resultado $false $versao "docker compose up -d falhou (código $codigo)"
                } elseif (Esperar-Api $Health) {
                    Escrever-Log "OK - API respondeu /api/health"
                    Escrever-Resultado $true $versao ""
                } else {
                    Escrever-Log "ERRO: API não respondeu /api/health a tempo - ver 'docker compose logs api-1'"
                    Escrever-Resultado $false $versao "health check falhou após o up"
                }
            } catch {
                Escrever-Log ("ERRO: " + $_.Exception.Message)
                Escrever-Resultado $false (Ler-Versao) $_.Exception.Message
            } finally {
                # some com o pedido em qualquer caso: re-arma a vigia para o próximo clique
                Remove-Item -LiteralPath $Req -ErrorAction SilentlyContinue
            }
        }

        Start-Sleep -Seconds 5
    }
} finally {
    Remove-Item -LiteralPath $Coracao -ErrorAction SilentlyContinue
}

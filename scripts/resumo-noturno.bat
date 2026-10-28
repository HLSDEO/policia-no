@echo off
REM resumo-noturno.bat — atalho sem PowerShell: repassa tudo para o .ps1.
REM Uso: resumo-noturno.bat [--inquerito 101] [--forcar] [--dry-run]
setlocal
cd /d "%~dp0.."
powershell -ExecutionPolicy Bypass -File "%~dp0resumo-noturno.ps1" %*

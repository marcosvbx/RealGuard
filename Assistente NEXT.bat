@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title RealGuard - Assistente NEXT
set "RG_BOOT="
if exist "runtime\python.exe" (
  set "RG_BOOT=runtime\python.exe"
  goto bootstrap
)
py -3 -c "import sys; assert sys.version_info >= (3,9)" >nul 2>&1
if not errorlevel 1 set "RG_BOOT=py -3"
if defined RG_BOOT goto bootstrap
python -c "import sys; assert sys.version_info >= (3,9)" >nul 2>&1
if not errorlevel 1 set "RG_BOOT=python"
if not defined RG_BOOT (
  echo Instale Python 3.9 ou superior no notebook e abra novamente.
  pause
  exit /b 1
)
:bootstrap
if not exist ".venv-assistente\Scripts\python.exe" (
  %RG_BOOT% -m venv .venv-assistente
  if errorlevel 1 goto failed
)
".venv-assistente\Scripts\python.exe" -c "import paramiko" >nul 2>&1
if errorlevel 1 (
  echo Preparando o assistente no notebook. Esta etapa precisa de internet.
  ".venv-assistente\Scripts\python.exe" -m pip install "paramiko>=3.4,<4"
  if errorlevel 1 goto failed
)
".venv-assistente\Scripts\python.exe" scripts\next_assistant.py
if errorlevel 1 goto failed
exit /b 0
:failed
echo O assistente nao foi concluido. Confira a mensagem acima.
pause
exit /b 1

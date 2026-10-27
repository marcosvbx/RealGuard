@echo off
rem Usa um Python funcional; uma .venv incompleta nao bloqueia o notebook.
cd /d "%~dp0"
if "%~1"=="" (
  start "" "%~dp0RealGuard.exe"
  exit /b 0
)
set "RG_PY="
if exist .venv\Scripts\python.exe (
  .venv\Scripts\python.exe -c "import cv2,numpy,onnxruntime,fastapi,uvicorn,multipart,insightface" >nul 2>&1
  if not errorlevel 1 set "RG_PY=.venv\Scripts\python.exe"
)
if defined RG_PY goto run
python -c "import cv2,numpy,onnxruntime,fastapi,uvicorn,multipart,insightface" >nul 2>&1
if not errorlevel 1 (
  set "RG_PY=python"
  goto run
)
py -3.11 -c "import cv2,numpy,onnxruntime,fastapi,uvicorn,multipart,insightface" >nul 2>&1
if not errorlevel 1 (
  set "RG_PY=py -3.11"
  goto run
)
echo Nenhum Python com as dependencias do RealGuard foi encontrado.
echo Instale com: python -m pip install -r requirements.txt
pause
exit /b 1
:run
echo Iniciando RealGuard com %RG_PY%
%RG_PY% main.py %*
pause

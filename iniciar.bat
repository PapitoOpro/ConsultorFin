@echo off
rem ConsultorFin: duplo clique para abrir o painel.
rem Nao precisa ativar o ambiente nem liberar scripts do PowerShell.
rem Uso opcional: iniciar.bat 8001   (para usar outra porta)
setlocal
cd /d "%~dp0"
set "PORTA=%~1"
if "%PORTA%"=="" set "PORTA=8000"
set "PY=.venv\Scripts\python.exe"

if not exist "%PY%" (
  echo Primeira execucao: preparando o ambiente. Isso pode levar alguns minutos...
  where py >nul 2>nul
  if not errorlevel 1 (
    py -3 -m venv .venv
  ) else (
    python -m venv .venv
  )
  if not exist "%PY%" (
    echo.
    echo Nao foi possivel criar o ambiente. Instale o Python 3.11 ou superior em https://www.python.org/downloads/
    echo e marque a opcao "Add python.exe to PATH" na instalacao. Depois execute este arquivo de novo.
    pause
    exit /b 1
  )
  "%PY%" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo.
    echo Falha ao instalar as dependencias. Verifique a conexao com a internet e execute este arquivo de novo.
    pause
    exit /b 1
  )
)

echo.
echo Abrindo o ConsultorFin em http://localhost:%PORTA%
echo Para encerrar, feche esta janela ou pressione Ctrl+C.
echo.
if not defined SEM_NAVEGADOR start "" /b cmd /c "ping -n 4 127.0.0.1 >nul & start http://localhost:%PORTA%"
"%PY%" -m uvicorn app.main:create_app --factory --port %PORTA%
if errorlevel 1 (
  echo.
  echo O painel nao iniciou. Se a porta %PORTA% estiver em uso, tente: iniciar.bat 8001
)
pause
endlocal

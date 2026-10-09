@echo off
setlocal
chcp 65001 >nul
title CRM Your Home
cd /d "%~dp0"
set "PYTHONUTF8=1"

echo.
echo  ===============================================
echo    CRM Your Home - iniciando...
echo  ===============================================
echo.

rem --- 1. O zip foi extraido? ---
if not exist "app.py" goto erro_zip
if not exist "requirements.txt" goto erro_zip

rem --- 2. Encontrar o Python 3.9+ ---
set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info>=(3,9) else 1)" >nul 2>nul && set "PY=py -3"
if defined PY goto achou_python
python -c "import sys; sys.exit(0 if sys.version_info>=(3,9) else 1)" >nul 2>nul && set "PY=python"
if defined PY goto achou_python
python3 -c "import sys; sys.exit(0 if sys.version_info>=(3,9) else 1)" >nul 2>nul && set "PY=python3"
if defined PY goto achou_python
goto erro_python

:achou_python
for /f "delims=" %%v in ('%PY% -c "import sys; print(sys.version.split()[0])"') do set "PYVER=%%v"
echo  [ok] Python %PYVER% encontrado.

rem --- 3. Ambiente do sistema: cria ou recria se estiver quebrado ---
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -c "import flask, waitress" >nul 2>nul
  if errorlevel 1 (
    echo  [..] Ambiente incompleto, recriando...
    rmdir /s /q ".venv"
  )
)
if exist ".venv\Scripts\python.exe" goto pronto

echo  [..] Preparando o sistema pela primeira vez. Isso leva 1 a 2 minutos...
%PY% -m venv .venv
if errorlevel 1 goto erro_venv
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 goto erro_pip
echo  [ok] Instalacao concluida.

:pronto
rem --- 4. Porta livre ---
set "PORT=5000"
netstat -ano | findstr /r /c:":5000 .*LISTENING" >nul && set "PORT=5050"
if "%PORT%"=="5050" echo  [!] A porta 5000 estava ocupada. Usando a porta 5050.

rem --- 5. Abre o navegador em 3 segundos e inicia o servidor ---
start "" powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 3; Start-Process 'http://localhost:%PORT%'"
echo.
echo  O navegador vai abrir em http://localhost:%PORT%
echo  Se o Windows perguntar sobre o Firewall, clique em "Permitir acesso".
echo  NAO feche esta janela enquanto estiver usando o sistema.
echo.
".venv\Scripts\python.exe" app.py
echo.
echo  [ERRO] O sistema parou. Tire um print desta janela e envie para o suporte.
pause
exit /b 1

:erro_zip
echo  [ERRO] Nao encontrei os arquivos do sistema nesta pasta.
echo.
echo  Voce provavelmente abriu o iniciar.bat de dentro do arquivo .zip.
echo  Faca assim:
echo    1. Clique com o botao direito no arquivo .zip e escolha "Extrair tudo..."
echo    2. Abra a pasta extraida  yourhome-crm
echo    3. De dois cliques no iniciar.bat que esta dentro dela
echo.
pause
exit /b 1

:erro_python
echo  [ERRO] Python 3.9 ou mais novo nao foi encontrado neste computador.
echo.
echo    1. Baixe em https://www.python.org/downloads/
echo    2. Na primeira tela do instalador, MARQUE a opcao "Add python.exe to PATH"
echo    3. Conclua a instalacao, feche esta janela e rode o iniciar.bat de novo
echo.
echo  Se ja instalou: em Configuracoes ^> Aplicativos ^> Configuracoes avancadas de aplicativos
echo  ^> Aliases de execucao de aplicativo, DESLIGUE "python.exe" e "python3.exe" da Microsoft Store.
echo.
start "" "https://www.python.org/downloads/"
pause
exit /b 1

:erro_venv
echo  [ERRO] Nao consegui criar o ambiente do sistema.
echo  Mova a pasta yourhome-crm para um lugar simples, por exemplo C:\yourhome-crm, e tente de novo.
echo.
pause
exit /b 1

:erro_pip
echo  [ERRO] Nao consegui baixar os componentes Flask e Waitress.
echo  Verifique a internet. Na rede da empresa, pode haver bloqueio: teste em outra rede
echo  ou peca ao TI para liberar o site pypi.org.
if exist ".venv" rmdir /s /q ".venv"
echo.
pause
exit /b 1

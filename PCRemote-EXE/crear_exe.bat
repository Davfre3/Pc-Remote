@echo off
chcp 65001 >nul
rem Crea el programa del PC: dist\PC Remote\PC Remote.exe  (y el instalador si Inno Setup esta instalado)
cd /d "%~dp0"
set PY=..\.venv\Scripts\python.exe
if not exist "%PY%" set PY=python
"%PY%" -m PyInstaller --noconfirm --clean PCRemote.spec || goto :err
set ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe
if not exist "%ISCC%" set ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe
if exist "%ISCC%" ( "%ISCC%" instalador.iss || goto :err ) else echo (Sin Inno Setup: no se crea el instalador, usa la carpeta dist\PC Remote)
echo.
echo LISTO. Programa: dist\PC Remote\PC Remote.exe
pause
exit /b 0
:err
echo ERROR al crear el programa.
pause
exit /b 1

@echo off
cd /d "%~dp0"
echo Instalando lo que necesita tu TMS (solo se hace una vez)...
py -m pip install -r requisitos.txt
echo.
echo Listo. Ya puedes cerrar esta ventana y abrir iniciar_tms.bat
pause

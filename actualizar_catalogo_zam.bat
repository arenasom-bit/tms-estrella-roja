@echo off
cd /d "%~dp0"
echo (OPCIONAL) Actualiza el catalogo desde ZAM cada 10 minutos aunque nadie tenga abierto el TMS.
echo Cierra esta ventana para detener.
py zam.py
pause

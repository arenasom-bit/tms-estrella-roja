@echo off
cd /d "%~dp0"
echo Abriendo tu TMS... NO cierres esta ventana mientras lo uses.
echo.
echo Liga para otros equipos de la red Estrella Roja:
echo    http://%COMPUTERNAME%.estrellaroja.com.mx:8501
echo.
py -m streamlit run app.py --server.port 8501 --server.address 0.0.0.0
pause

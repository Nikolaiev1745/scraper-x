@echo off
rem Lanza la interfaz web del scraper. Doble clic para abrir.
rem Va por .bat (no .ps1) para no depender de la ExecutionPolicy de PowerShell.
rem Toda la logica vive en run_web.py, que es multiplataforma: verifica el
rem entorno virtual, elige un puerto libre, abre el navegador y levanta el
rem servidor.
cd /d "%~dp0"

rem Se detecta el lanzador con 'where' y no por errorlevel: 'if errorlevel N'
rem compara MAYOR O IGUAL, y al frenar el servidor con Ctrl+C Windows devuelve
rem 0xC000013A (3221225786), que es mayor que cualquier centinela y volveria a
rem arrancar el servidor justo cuando lo estas cerrando.
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 run_web.py %*
) else (
    python run_web.py %*
)

pause

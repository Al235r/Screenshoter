@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Установка библиотек...
py -3 -m pip install --upgrade -r requirements.txt || python -m pip install --upgrade -r requirements.txt
echo.
echo Готово. Теперь запустите run.bat
pause

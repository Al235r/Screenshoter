@echo off
cd /d "%~dp0"
where pyw >/dev/null 2>/dev/null && (start "" pyw -3 "%~dp0screenshoter.py" & exit /b)
start "" pythonw "%~dp0screenshoter.py"

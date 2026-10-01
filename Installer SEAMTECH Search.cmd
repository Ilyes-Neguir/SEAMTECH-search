@echo off
setlocal

set "PROJECT_ROOT=%~dp0"
set "INSTALLER=%PROJECT_ROOT%scripts\installer_poste_windows.ps1"

echo ==========================================================
echo   Installation de SEAMTECH Search sur ce poste
echo ==========================================================
echo.
echo Laissez cette fenetre ouverte jusqu'a la fin de l'installation.
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%INSTALLER%" %*
set "EXIT_CODE=%ERRORLEVEL%"

echo.
if "%EXIT_CODE%"=="0" (
    echo Installation terminee.
    echo Double-cliquez sur l'icone "SEAMTECH Search" posee sur le bureau.
) else (
    echo L'installation s'est arretee - code %EXIT_CODE%.
    echo Lisez les lignes marquees !! ci-dessus, corrigez, puis relancez.
)
echo.
pause
exit /b %EXIT_CODE%

@echo off
rem Build music-folder-organizer.exe (single file, no console) with PyInstaller.
rem Usage: build.bat          -> dist\music-folder-organizer.exe
rem third_party\fpcalc.exe (Chromaprint) is bundled when present.
setlocal
cd /d "%~dp0"

python -m PyInstaller --version >nul 2>&1 || python -m pip install pyinstaller
python -m pip install -r requirements.txt

set FPCALC=
if exist "third_party\fpcalc.exe" set FPCALC=--add-binary "third_party\fpcalc.exe;third_party"

python -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name music-folder-organizer ^
  --icon "assets\icon.ico" ^
  --add-data "assets\icon.png;assets" ^
  --add-data "lang;lang" ^
  --add-data "third_party\LICENSE-chromaprint;third_party" ^
  %FPCALC% ^
  --collect-data tkinterdnd2 ^
  main.py

if errorlevel 1 (
  echo Build failed.
  exit /b 1
)
echo.
echo Done: dist\music-folder-organizer.exe
endlocal

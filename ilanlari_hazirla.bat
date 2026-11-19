@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo KamuRadar ilanlari toplaniyor, lutfen bekle...
python ilan_cek_web.py
echo.
echo Bitti. Simdi acilan GitHub sayfasina ilanlar.json dosyasini surukle ve Commit changes de.
start https://github.com/19ynsemre05/kamuradar/upload/main
explorer /select,"%~dp0ilanlar.json"
pause

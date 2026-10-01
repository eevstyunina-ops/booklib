@echo off
python -m pip install --upgrade pip
pip install -r requirements.txt
pyinstaller --noconfirm --onefile --name BookLib --add-data "booklib/templates;booklib/templates" run.py
echo Готово: dist\BookLib.exe
pause

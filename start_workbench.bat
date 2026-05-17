@echo off
setlocal
cd /d "%~dp0"
call "%USERPROFILE%\miniconda3\condabin\conda.bat" activate cad2ar
python -m cadconverter.cli workbench --directory "%CD%" --host 127.0.0.1 --port 8765

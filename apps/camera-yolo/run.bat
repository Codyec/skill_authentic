@echo off
REM Arranca camera-yolo (:8090). Tarda ~25 s (init CUDA + carga YOLO).
REM Deja esta ventana abierta mientras uses la camara.
cd /d "%~dp0"
set PYEXE=C:\Users\dmore\anaconda3\envs\vision_leche\python.exe
if not exist "%PYEXE%" set PYEXE=python
"%PYEXE%" main.py
pause

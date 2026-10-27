@echo off
title SWGEmu CLIP Training
color 0A

:: Path to your Python executable
set PYTHON_EXE="C:\Users\marc\AppData\Local\Programs\Python\Python311\python.exe"

:: Path to your data
set DATA_DIR="E:\SWGEMU_bot\Video capture\Desktop\screenshots\data\processed"

:: Training Parameters
set EPOCHS=10
set BATCH_SIZE=32
set LR=5e-5

echo ====================================================
echo   SWGEmu Vision Training: Starting Fine-Tuning
echo ====================================================
echo.

%PYTHON_EXE% train_clip.py --data_dir %DATA_DIR% --epochs %EPOCHS% --batch_size %BATCH_SIZE% --lr %LR%

echo.
echo ====================================================
echo   Training Session Finished!
echo ====================================================
pause
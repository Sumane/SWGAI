@echo off
echo ============================================
echo  Install PyTorch with CUDA Support
echo ============================================
echo.
echo This will install GPU-accelerated PyTorch
echo.
echo Choose your CUDA version:
echo   [1] CUDA 11.8 (most GPUs, RTX 30xx series)
echo   [2] CUDA 12.1 (RTX 40xx series, newer)
echo   [3] CPU only (no GPU)
echo.
set /p choice="Enter 1, 2, or 3: "

if "%choice%"=="1" (
    echo Installing PyTorch with CUDA 11.8...
    pip install torch torchvision --index-url https://download.pytorch.org/whl/cu118
    goto verify
)

if "%choice%"=="2" (
    echo Installing PyTorch with CUDA 12.1...
    pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121
    goto verify
)

if "%choice%"=="3" (
    echo Installing CPU-only PyTorch...
    pip install torch torchvision
    goto verify
)

echo Invalid choice
goto end

:verify
echo.
echo Verifying installation...
python -c "import torch; print(f'PyTorch: {torch.__version__}'); print(f'CUDA available: {torch.cuda.is_available()}'); print(f'Device: {torch.cuda.get_device_name(0) if torch.cuda.is_available() else \"CPU\"}')"

if errorlevel 1 (
    echo.
    echo ERROR: Installation failed
    pause
    exit /b 1
)

:end
echo.
echo ============================================
echo  Installation complete!
echo ============================================
echo.
pause

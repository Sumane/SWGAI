@echo off
REM SWGEmu Training Video Extractor
REM One-click frame extraction with GPU acceleration

echo SWGEmu Training Video Extractor
echo =================================
echo.

REM Check if PowerShell script exists in same folder
set "PS_SCRIPT=%~dp0extract_frames_gpu.ps1"

if not exist "%PS_SCRIPT%" (
    echo ERROR: extract_frames_gpu.ps1 not found!
    echo Please make sure both files are in the same folder.
    pause
    exit /b 1
)

REM Check if video exists
echo Checking for video...
if not exist "E:\SWGEMU_bot\Video capture\Desktop\training.mp4" (
    echo ERROR: training.mp4 not found!
    echo Expected: E:\SWGEMU_bot\Video capture\Desktop\training.mp4
    pause
    exit /b 1
)

echo Found video: E:\SWGEMU_bot\Video capture\Desktop\training.mp4
echo Starting extraction...
echo.

REM Run PowerShell script with hardcoded paths
powershell -ExecutionPolicy Bypass -File "%~dp0extract_frames_gpu.ps1" -Scale

echo.
if errorlevel 1 (
    echo Extraction failed!
    pause
    exit /b 1
) else (
    echo Extraction complete!
    echo Screenshots saved to: E:\SWGEMU_bot\Video capture\Desktop\screenshots
)

pause

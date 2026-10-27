@echo off
echo ============================================
echo  Cleanup: Delete Training Images
echo ============================================
echo.
echo This will DELETE:
echo   - All frame_*.jpg files
echo   - labeled/ folder
echo   - data/ folder
echo   - Temporary JSON files
echo.
echo Keeps:
echo   - models\clip_swgemu_best.pth (your trained model)
echo   - All .py and .bat scripts
echo.
set /p confirm="Type 'delete' to confirm: "

if /I not "%confirm%"=="delete" (
    echo Cancelled.
    pause
    exit /b 0
)

echo.
echo Deleting frame images...
for %%f in (frame_*.jpg frame_*.png frame_*.jpeg) do (
    if exist "%%f" (
        echo   Deleting %%f
        del "%%f"
    )
)

echo.
echo Deleting folders...
if exist "labeled" (
    rmdir /s /q "labeled"
    echo   Deleted labeled/
)

if exist "data" (
    rmdir /s /q "data"
    echo   Deleted data/
)

if exist "models" (
    echo   Keeping models/ (contains trained model)
)

echo.
echo Deleting temporary files...
if exist "image_features.json" del "image_features.json"
if exist "clusters.json" del "clusters.json"

echo.
echo ============================================
echo  CLEANUP COMPLETE!
echo ============================================
echo.
echo Kept files:
if exist "models\clip_swgemu_best.pth" (
    echo   - models\clip_swgemu_best.pth (trained model)
) else (
    echo   - (no model found - training may have failed)
)
echo   - All .py scripts
echo   - All .bat scripts
echo.
pause

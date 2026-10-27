@echo off
echo ============================================
echo  Cleanup: Delete Training Images
echo ============================================
echo.
echo This will DELETE all frame_*.jpg files in this folder
echo and the labeled/ folder after training.
echo.
echo Press Ctrl+C to cancel, or
echo.
pause

echo.
echo Deleting frame images...
for %%f in (frame_*.jpg) do (
    echo   Deleting %%f
    del "%%f"
)

echo.
echo Deleting labeled folder...
if exist "labeled" (
    rmdir /s /q "labeled"
    echo   Deleted labeled/
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
echo   - models\clip_swgemu_best.pth (your trained model)
echo   - All .py scripts
echo   - This .bat file
echo.
pause

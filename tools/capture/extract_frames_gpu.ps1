# SWGEmu Video Frame Extractor
# Handles corrupted/broken AV1 videos by using IVF conversion + GPU decoding
# Usage: .\extract_frames_gpu.ps1 -InputVideo "D:\recordings\training.mp4" [-OutputDir ...] [-Fps 1] [-Scale]
#        (OutputDir defaults to a "screenshots" folder next to the video)

param(
    [Parameter(Mandatory = $true)]
    [string]$InputVideo,
    
    [string]$OutputDir = "",
    
    [int]$Fps = 1,
    
    [string]$FramePattern = "frame_%04d.jpg",
    
    [switch]$Scale,
    
    [string]$ScaleResolution = "640:640",
    
    [switch]$KeepIvf,
    
    [switch]$DeleteOriginal
)

# Check if ffmpeg exists
$ffmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue
if (-not $ffmpeg) {
    if (Test-Path ".\ffmpeg.exe") {
        $ffmpeg = ".\ffmpeg.exe"
    } else {
        Write-Host "ERROR: ffmpeg not found. Make sure ffmpeg.exe is in PATH or current directory." -ForegroundColor Red
        exit 1
    }
} else {
    $ffmpeg = "ffmpeg"
}

# Check if input video exists
if (-not (Test-Path $InputVideo)) {
    Write-Host "ERROR: Input video not found at: $InputVideo" -ForegroundColor Red
    exit 1
}

if (-not $OutputDir) {
    $OutputDir = Join-Path (Split-Path -Parent $InputVideo) "screenshots"
}

# Create output directory
if (-not (Test-Path $OutputDir)) {
    New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
}

$VideoName = [System.IO.Path]::GetFileNameWithoutExtension($InputVideo)
$IvfFile = Join-Path $OutputDir "temp_${VideoName}.ivf"
$FrameOutput = Join-Path $OutputDir $FramePattern

Write-Host ""
Write-Host "SWGEmu Frame Extractor" -ForegroundColor Cyan
Write-Host "=================================" -ForegroundColor Cyan
Write-Host "Input: $InputVideo"
Write-Host "Output: $OutputDir"
Write-Host "FPS: $Fps"
Write-Host ""

# Step 1: Convert to IVF
Write-Host "Step 1: Converting to IVF..." -ForegroundColor Yellow
$step1Args = @(
    "-y",
    "-i", $InputVideo,
    "-map", "0:v",
    "-c:v", "copy",
    "-f", "ivf",
    $IvfFile
)

try {
    & $ffmpeg @step1Args 2>&1 | ForEach-Object { Write-Host "." -NoNewline }
    
    if (-not (Test-Path $IvfFile)) {
        throw "IVF file was not created"
    }
    
    Write-Host ""
    Write-Host "Step 1 Complete: IVF file created" -ForegroundColor Green
} catch {
    Write-Host ""
    Write-Host "Step 1 FAILED: $_" -ForegroundColor Red
    exit 1
}

# Step 2: Extract frames using GPU
Write-Host ""
Write-Host "Step 2: Extracting frames with GPU..." -ForegroundColor Yellow

$vfFilter = "fps=$Fps,format=yuv420p"
if ($Scale) {
    $vfFilter = "fps=$Fps,scale=$ScaleResolution,format=yuv420p"
    Write-Host "Scaling to: $ScaleResolution" -ForegroundColor Magenta
}

$step2Args = @(
    "-y",
    "-c:v", "av1_cuvid",
    "-i", $IvfFile,
    "-vf", $vfFilter,
    $FrameOutput
)

try {
    & $ffmpeg @step2Args 2>&1 | ForEach-Object { Write-Host "." -NoNewline }
    
    Write-Host ""
    Write-Host "Step 2 Complete: Frames extracted!" -ForegroundColor Green
} catch {
    Write-Host ""
    Write-Host "Step 2 FAILED, trying software fallback..." -ForegroundColor Yellow
    
    $fallbackArgs = @(
        "-y",
        "-err_detect", "ignore_err",
        "-i", $IvfFile,
        "-vf", $vfFilter,
        $FrameOutput
    )
    
    & $ffmpeg @fallbackArgs 2>&1 | ForEach-Object { Write-Host "." -NoNewline }
    
    $extractedFrames = Get-ChildItem $OutputDir -Filter "frame_*.jpg" | Measure-Object
    if ($extractedFrames.Count -eq 0) {
        Write-Host ""
        Write-Host "Fallback also failed." -ForegroundColor Red
        exit 1
    }
    
    Write-Host ""
    Write-Host "Fallback succeeded!" -ForegroundColor Green
}

# Count extracted frames
$frameCount = (Get-ChildItem $OutputDir -Filter "frame_*.jpg" | Measure-Object).Count
Write-Host ""
Write-Host "Results:" -ForegroundColor Cyan
Write-Host "  Frames extracted: $frameCount"
Write-Host "  Location: $OutputDir"

# Cleanup
Write-Host ""
if (-not $KeepIvf) {
    Remove-Item $IvfFile -ErrorAction SilentlyContinue
    Write-Host "Cleaned up temp IVF file" -ForegroundColor Green
}

if ($DeleteOriginal) {
    Remove-Item $InputVideo -ErrorAction SilentlyContinue
    Write-Host "Original video deleted" -ForegroundColor Green
}

Write-Host ""
Write-Host "All done! Your frames are ready." -ForegroundColor Cyan

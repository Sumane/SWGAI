#!/usr/bin/env python3
"""
SWGEmu Video Watcher - Auto-extracts frames from new recordings
Monitors a folder and automatically processes new videos using
IVF conversion + GPU acceleration for corrupted AV1 files.

Usage:
    python tools/capture/video_watcher.py --watch-dir "D:/recordings" [--output-dir ...] [--fps 1]
"""

import os
import sys
import time
import argparse
import subprocess
import shutil
from pathlib import Path
from datetime import datetime


class VideoWatcher:
    def __init__(self, watch_dir, output_dir=None, fps=1, scale=None, keep_ivf=False):
        self.watch_dir = Path(watch_dir)
        self.output_dir = Path(output_dir) if output_dir else self.watch_dir / "extracted_frames"
        self.fps = fps
        self.scale = scale
        self.keep_ivf = keep_ivf
        
        # Track processed files to avoid re-processing
        self.processed_files = set()
        
        # Find ffmpeg
        self.ffmpeg = self._find_ffmpeg()
        if not self.ffmpeg:
            raise RuntimeError("❌ ffmpeg not found! Make sure it's in PATH.")
        
        # Create output directory
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        print(f"🎬 SWGEmu Video Watcher started")
        print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print(f"👀 Watching: {self.watch_dir}")
        print(f"📂 Output: {self.output_dir}")
        print(f"🎯 FPS: {self.fps}")
        print(f"🔧 FFmpeg: {self.ffmpeg}")
        print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print(f"⏳ Waiting for new videos... (Ctrl+C to stop)")
    
    def _find_ffmpeg(self):
        """Find ffmpeg executable"""
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg:
            return ffmpeg
        # Check common locations
        for path in ["./ffmpeg.exe", "./ffmpeg", "../ffmpeg.exe"]:
            if Path(path).exists():
                return path
        return None
    
    def find_video_files(self):
        """Find all video files in watch directory"""
        video_extensions = {'.mp4', '.mkv', '.avi', '.mov', '.webm', '.ivf'}
        videos = []
        for ext in video_extensions:
            videos.extend(self.watch_dir.glob(f"*{ext}"))
        return videos
    
    def extract_frames(self, video_path):
        """Extract frames using IVF conversion + GPU acceleration"""
        video_name = video_path.stem
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        session_dir = self.output_dir / f"{video_name}_{timestamp}"
        session_dir.mkdir(parents=True, exist_ok=True)
        
        ivf_file = session_dir / "temp_recovery.ivf"
        frame_pattern = session_dir / "frame_%04d.jpg"
        
        print(f"\n🎬 Processing: {video_path.name}")
        print(f"📁 Session: {session_dir}")
        
        # Step 1: Convert to IVF
        print(f"🔧 Step 1: Converting to IVF...")
        cmd1 = [
            self.ffmpeg, "-y",
            "-i", str(video_path),
            "-map", "0:v",
            "-c:v", "copy",
            "-f", "ivf",
            str(ivf_file)
        ]
        
        try:
            result1 = subprocess.run(cmd1, capture_output=True, text=True, timeout=60)
            if result1.returncode != 0:
                print(f"⚠️  IVF conversion warning: {result1.stderr[-500:]}")
            
            if not ivf_file.exists():
                raise RuntimeError("IVF file was not created")
            
            print(f"✅ IVF created: {ivf_file.stat().st_size / 1024 / 1024:.1f} MB")
        except Exception as e:
            print(f"❌ Step 1 failed: {e}")
            return False
        
        # Step 2: Extract frames with GPU
        print(f"🚀 Step 2: Extracting frames with GPU...")
        vf_filter = f"fps={self.fps},format=yuv420p"
        if self.scale:
            vf_filter = f"fps={self.fps},scale={self.scale},format=yuv420p"
            print(f"📐 Scaling to: {self.scale}")
        
        cmd2 = [
            self.ffmpeg, "-y",
            "-c:v", "av1_cuvid",
            "-i", str(ivf_file),
            "-vf", vf_filter,
            str(frame_pattern)
        ]
        
        try:
            result2 = subprocess.run(cmd2, capture_output=True, text=True, timeout=300)
            
            # Count extracted frames
            frames = list(session_dir.glob("frame_*.jpg"))
            frame_count = len(frames)
            
            if frame_count == 0:
                # Try software fallback
                print(f"🔄 GPU failed, trying software fallback...")
                cmd_fallback = [
                    self.ffmpeg, "-y",
                    "-err_detect", "ignore_err",
                    "-i", str(ivf_file),
                    "-vf", vf_filter,
                    str(frame_pattern)
                ]
                result_fallback = subprocess.run(cmd_fallback, capture_output=True, text=True, timeout=300)
                frames = list(session_dir.glob("frame_*.jpg"))
                frame_count = len(frames)
                
                if frame_count == 0:
                    raise RuntimeError("No frames extracted (both GPU and software failed)")
                
                print(f"✅ Software fallback succeeded!")
            else:
                print(f"✅ GPU extraction succeeded!")
            
            print(f"📊 Extracted: {frame_count} frames")
            
            # Cleanup IVF
            if not self.keep_ivf:
                ivf_file.unlink()
                print(f"🧹 Cleaned up temp IVF file")
            
            return True
            
        except Exception as e:
            print(f"❌ Step 2 failed: {e}")
            return False
    
    def run(self, interval=5, once=False):
        """Main watch loop"""
        try:
            while True:
                videos = self.find_video_files()
                
                for video in videos:
                    # Skip already processed files
                    if str(video) in self.processed_files:
                        continue
                    
                    # Wait for file to be fully written (not being modified)
                    try:
                        size1 = video.stat().st_size
                        time.sleep(2)
                        size2 = video.stat().st_size
                        if size1 != size2:
                            continue  # Still being written
                    except:
                        continue  # File locked or inaccessible
                    
                    # Process the video
                    success = self.extract_frames(video)
                    if success:
                        self.processed_files.add(str(video))
                        print(f"✅ Marked as processed: {video.name}")
                
                if once:
                    break
                    
                time.sleep(interval)
                
        except KeyboardInterrupt:
            print(f"\n👋 Stopped by user")


def main():
    parser = argparse.ArgumentParser(description="Watch for new videos and extract frames")
    parser.add_argument("--watch-dir", "-w", required=True, help="Directory to watch")
    parser.add_argument("--output-dir", "-o", help="Output directory (default: watch_dir/extracted_frames)")
    parser.add_argument("--fps", "-f", type=int, default=1, help="Frames per second to extract (default: 1)")
    parser.add_argument("--scale", "-s", help="Scale resolution (e.g., 640:640 for AI training)")
    parser.add_argument("--keep-ivf", action="store_true", help="Keep temporary IVF files")
    parser.add_argument("--once", action="store_true", help="Process existing videos once, then exit (don't watch)")
    parser.add_argument("--interval", "-i", type=int, default=5, help="Watch interval in seconds (default: 5)")
    
    args = parser.parse_args()
    
    try:
        watcher = VideoWatcher(
            watch_dir=args.watch_dir,
            output_dir=args.output_dir,
            fps=args.fps,
            scale=args.scale,
            keep_ivf=args.keep_ivf
        )
        
        watcher.run(interval=args.interval, once=args.once)
        
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

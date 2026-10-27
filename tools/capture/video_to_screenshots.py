#!/usr/bin/env python3
"""
Video to Screenshots Tool for SWGEmu Training Data

Extracts frames from a video at regular intervals.
Perfect for generating training data from gameplay recordings.

Usage:
    python video_to_screenshots.py /path/to/video.mp4 --output_dir ./screenshots --interval 1.0
    
    # Extract 1 frame every second
    python video_to_screenshots.py gameplay.mp4 --interval 1.0
    
    # Extract 5 frames per second (for smooth action)
    python video_to_screenshots.py combat.mp4 --interval 0.2 --label combat_active
    
    # Extract specific time range
    python video_to_screenshots.py video.mp4 --start 00:01:30 --end 00:02:00 --interval 0.5
"""

import os
import sys
import argparse
import subprocess
import json
from pathlib import Path
from datetime import datetime


def get_video_duration(video_path):
    """Get video duration in seconds using ffprobe."""
    cmd = [
        'ffprobe',
        '-v', 'error',
        '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1',
        str(video_path)
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return float(result.stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("Error: ffprobe not found. Install ffmpeg first.")
        return None


def time_to_seconds(time_str):
    """Convert HH:MM:SS or MM:SS to seconds."""
    parts = time_str.split(':')
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    elif len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    else:
        return float(parts[0])


def seconds_to_time(seconds):
    """Convert seconds to HH:MM:SS format."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:06.3f}"


def extract_frame(video_path, timestamp, output_path):
    """Extract a single frame at given timestamp."""
    cmd = [
        'ffmpeg',
        '-ss', str(timestamp),
        '-i', str(video_path),
        '-vframes', '1',
        '-q:v', '2',  # High quality
        '-y',  # Overwrite
        str(output_path)
    ]
    
    try:
        subprocess.run(cmd, capture_output=True, check=True)
        return True
    except subprocess.CalledProcessError:
        return False


def extract_screenshots(video_path, output_dir, interval=1.0, start_time=0, end_time=None, 
                        label=None, prefix=None):
    """
    Extract screenshots from video at regular intervals.
    
    Args:
        video_path: Path to video file
        output_dir: Directory to save screenshots
        interval: Seconds between each screenshot
        start_time: Start time in seconds (or None for beginning)
        end_time: End time in seconds (or None for end)
        label: Label for these screenshots (e.g., 'combat_active')
        prefix: Filename prefix
    """
    video_path = Path(video_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Get video duration
    duration = get_video_duration(video_path)
    if duration is None:
        return []
    
    print(f"Video duration: {seconds_to_time(duration)}")
    
    # Set time range
    start = start_time if start_time else 0
    end = end_time if end_time else duration
    
    print(f"Extracting from {seconds_to_time(start)} to {seconds_to_time(end)}")
    print(f"Interval: {interval}s")
    
    # Generate timestamps
    timestamps = []
    current = start
    while current < end:
        timestamps.append(current)
        current += interval
    
    print(f"Will extract {len(timestamps)} frames...")
    
    # Extract frames
    extracted = []
    labels_data = []
    
    for i, timestamp in enumerate(timestamps):
        # Generate filename
        if prefix:
            filename = f"{prefix}_{i:04d}_{timestamp:.2f}s.png"
        elif label:
            filename = f"{label}_{i:04d}_{timestamp:.2f}s.png"
        else:
            filename = f"frame_{i:04d}_{timestamp:.2f}s.png"
        
        output_path = output_dir / filename
        
        # Extract frame
        if extract_frame(video_path, timestamp, output_path):
            extracted.append(output_path)
            
            # Add to labels if label provided
            if label:
                labels_data.append({
                    'path': str(output_path.relative_to(output_dir.parent) if output_dir.parent else output_path),
                    'label': label,
                    'timestamp': timestamp,
                    'source_video': str(video_path.name)
                })
            
            print(f"  ✓ {filename}")
        else:
            print(f"  ✗ Failed: {filename}")
    
    # Save labels if provided
    if label and labels_data:
        labels_file = output_dir.parent / 'labels.json'
        
        # Load existing labels
        if labels_file.exists():
            with open(labels_file, 'r') as f:
                existing = json.load(f)
        else:
            existing = []
        
        # Add new labels
        existing.extend(labels_data)
        
        # Save
        with open(labels_file, 'w') as f:
            json.dump(existing, f, indent=2)
        
        print(f"\nLabels saved to: {labels_file}")
        print(f"Total dataset: {len(existing)} samples")
    
    print(f"\n✅ Extracted {len(extracted)} frames to: {output_dir}")
    return extracted


def interactive_mode():
    """Interactive mode for easy use."""
    print("🎬 Video to Screenshots Tool")
    print("=" * 40)
    
    video_path = input("Video file path: ").strip()
    if not os.path.exists(video_path):
        print("❌ File not found!")
        return
    
    print("\nOptions:")
    print("1. Extract all frames (every N seconds)")
    print("2. Extract specific time range")
    
    choice = input("\nChoice (1/2): ").strip()
    
    if choice == '1':
        interval = float(input("Interval in seconds (e.g., 1.0): ") or "1.0")
        extract_screenshots(video_path, './screenshots', interval=interval)
        
    elif choice == '2':
        start = input("Start time (HH:MM:SS or seconds): ").strip()
        end = input("End time (HH:MM:SS or seconds): ").strip()
        interval = float(input("Interval in seconds: ") or "1.0")
        
        start_sec = time_to_seconds(start) if ':' in start else float(start)
        end_sec = time_to_seconds(end) if ':' in end else float(end)
        
        extract_screenshots(video_path, './screenshots', 
                          interval=interval, 
                          start_time=start_sec, 
                          end_time=end_sec)


def main():
    parser = argparse.ArgumentParser(
        description='Extract screenshots from video for SWGEmu training data',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Extract 1 frame every second
  %(prog)s gameplay.mp4 --interval 1.0
  
  # Extract with label for training
  %(prog)s combat.mp4 --interval 0.5 --label combat_active
  
  # Extract specific range (1:30 to 2:00)
  %(prog)s video.mp4 --start 00:01:30 --end 00:02:00 --interval 0.2
  
  # Output to specific directory
  %(prog)s video.mp4 --output_dir ./data/raw --label menu_open
        """
    )
    
    parser.add_argument('video', nargs='?', help='Path to video file')
    parser.add_argument('--output_dir', '-o', default='./screenshots',
                       help='Output directory (default: ./screenshots)')
    parser.add_argument('--interval', '-i', type=float, default=1.0,
                       help='Seconds between screenshots (default: 1.0)')
    parser.add_argument('--start', '-s', type=str,
                       help='Start time (HH:MM:SS or seconds)')
    parser.add_argument('--end', '-e', type=str,
                       help='End time (HH:MM:SS or seconds)')
    parser.add_argument('--label', '-l', type=str,
                       help='Label for these screenshots (e.g., combat_active)')
    parser.add_argument('--prefix', '-p', type=str,
                       help='Filename prefix')
    parser.add_argument('--interactive', action='store_true',
                       help='Interactive mode')
    
    args = parser.parse_args()
    
    # Check ffmpeg
    try:
        subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("❌ ffmpeg not found! Install it first:")
        print("   sudo apt install ffmpeg  # Ubuntu/Debian")
        print("   brew install ffmpeg      # macOS")
        sys.exit(1)
    
    # Interactive mode
    if args.interactive or not args.video:
        interactive_mode()
        return
    
    # Check video exists
    if not os.path.exists(args.video):
        print(f"❌ Video not found: {args.video}")
        sys.exit(1)
    
    # Parse times
    start_time = time_to_seconds(args.start) if args.start else None
    end_time = time_to_seconds(args.end) if args.end else None
    
    # Extract
    extract_screenshots(
        args.video,
        args.output_dir,
        interval=args.interval,
        start_time=start_time,
        end_time=end_time,
        label=args.label,
        prefix=args.prefix
    )


if __name__ == '__main__':
    main()

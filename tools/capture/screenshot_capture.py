import os
import time
import argparse
from datetime import datetime
import pyautogui

def capture_screenshots(output_dir, interval=3, prefix="swg"):
    """
    Capture screenshots at regular intervals.
    
    Args:
        output_dir: Directory to save screenshots
        interval: Seconds between screenshots (default: 3)
        prefix: Filename prefix (default: swg)
    """
    os.makedirs(output_dir, exist_ok=True)

    print(f"Starting screenshot capture every {interval} seconds.")
    print(f"Saving to: '{output_dir}'")
    print(f"Press Ctrl+C to stop.\n")
    print("Starting in 5 seconds - switch to your game window...")
    time.sleep(5)

    count = 0
    try:
        while True:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = os.path.join(output_dir, f"{prefix}_{timestamp}_{count:05d}.png")
            pyautogui.screenshot(filename)
            count += 1
            print(f"[{count}] Captured: {filename}")
            time.sleep(interval)

    except KeyboardInterrupt:
        print(f"\nStopped. Captured {count} screenshots total.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Capture screenshots at regular intervals")
    parser.add_argument("--output", default="./screenshots",
                        help="Output directory (default: ./screenshots)")
    parser.add_argument("--interval", type=float, default=3,
                        help="Seconds between screenshots (default: 3)")
    parser.add_argument("--prefix", default="swg",
                        help="Filename prefix (default: swg)")

    args = parser.parse_args()
    capture_screenshots(args.output, args.interval, args.prefix)


# run: python tools/capture/screenshot_capture.py --output <folder> --interval 3
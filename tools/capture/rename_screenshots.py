"""
Rename screenshots to  <prefix>_YYYYMMDD_HHMMSS_00001.png  in time order,
using each file's modified time. (Was label_work/rename_screenshots.py.)

    python tools/capture/rename_screenshots.py <folder> --prefix swg --dry-run
"""
import argparse
import os
from datetime import datetime
from pathlib import Path


def rename_files(folder: Path, prefix: str, dry_run: bool = False) -> int:
    files = sorted((f for f in folder.iterdir() if f.suffix.lower() in (".png", ".jpg")),
                   key=lambda f: f.stat().st_mtime)
    print(f"Found {len(files)} files.")
    count = 0
    for f in files:
        count += 1
        stamp = datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y%m%d_%H%M%S")
        new = folder / f"{prefix}_{stamp}_{count:05d}{f.suffix.lower()}"
        while new.exists() and new != f:
            count += 1
            new = folder / f"{prefix}_{stamp}_{count:05d}{f.suffix.lower()}"
        if dry_run:
            print(f"  {f.name} -> {new.name}")
        else:
            os.rename(f, new)
    print(f"Done! {'Would rename' if dry_run else 'Renamed'} {len(files)} files.")
    return len(files)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder")
    ap.add_argument("--prefix", default="swg")
    ap.add_argument("--dry-run", action="store_true", help="show the new names without renaming")
    a = ap.parse_args()
    if not Path(a.folder).exists():
        raise SystemExit(f"Error: '{a.folder}' does not exist.")
    rename_files(Path(a.folder), a.prefix, a.dry_run)

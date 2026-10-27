"""
Remove the confidence column from YOLO label files (prelabel.py writes
6 columns; training needs 5). Rewrites files in place. (Was fixlabels.py.)

    python tools/labeling/strip_confidence.py <labels_dir> [<labels_dir> ...]
"""
import argparse
from pathlib import Path


def strip_file(path: Path) -> None:
    lines = [" ".join(parts[:5]) for parts in
             (line.split() for line in path.read_text().splitlines()) if len(parts) >= 5]
    path.write_text("\n".join(lines))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dirs", nargs="+")
    a = ap.parse_args()
    for d in map(Path, a.dirs):
        if not d.exists():
            print(f"Skipping missing path: {d}")
            continue
        files = list(d.glob("*.txt"))
        print(f"Cleaning {len(files)} files in {d}...")
        for f in files:
            if f.name != "classes.txt":
                strip_file(f)
    print("✅ Labels cleaned.")


if __name__ == "__main__":
    main()

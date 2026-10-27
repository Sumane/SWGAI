"""
Start Label Studio with its data in <data_dir> (was label_studio.bat).

    python tools/labeling/start_label_studio.py

Uses tools.label_studio_exe from config, or `label-studio` on your PATH.
"""
import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))  # noqa: E401,E702

import shutil
import subprocess

from tools.common import data_dir, settings

if __name__ == "__main__":
    exe = settings.raw.get("tools", {}).get("label_studio_exe") or shutil.which("label-studio")
    if not exe:
        raise SystemExit("❌ label-studio not found. Set tools.label_studio_exe in config/settings.local.yaml")
    subprocess.run([exe, "start", "--data-dir", str(data_dir())])

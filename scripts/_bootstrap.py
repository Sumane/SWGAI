"""Lets the scripts in this folder `import jedai` without installing anything."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Vision models on the GPU the local LLM isn't using (harness.cuda_visible_devices,
# e.g. "0" = the 3060 in PCI order). Must happen before torch is imported.
# FIX (7 Nov): only harness_live set this, so scripts run on their own
# (clean_waypoints.py) put EasyOCR on the full 5070 Ti and ran out of memory.
import os as _os

if "CUDA_VISIBLE_DEVICES" not in _os.environ:
    try:
        import yaml as _yaml
        _cuda = str((_yaml.safe_load(open(ROOT / "config" / "settings.yaml")) or {})
                    .get("harness", {}).get("cuda_visible_devices", "") or "")
        if _cuda:
            _os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
            _os.environ["CUDA_VISIBLE_DEVICES"] = _cuda
    except Exception:  # noqa: BLE001 - never stop a script over this
        pass

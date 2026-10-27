# review.py - open clusters and label them
import json
import shutil
from pathlib import Path

with open("clusters.json", "r") as f:
    clusters = json.load(f)

# Create labeled folders
for cluster_id, paths in clusters.items():
    if cluster_id == "-1":
        continue  # Skip noise
    
    print(f"\n=== Cluster {cluster_id} ===")
    print(f"Sample images: {paths[:3]}")
    
    label = input("What state is this? (or 'skip'): ").strip()
    if label and label != "skip":
        Path(f"labeled/{label}").mkdir(parents=True, exist_ok=True)
        for p in paths:
            shutil.copy(p, f"labeled/{label}/")

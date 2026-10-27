# cluster.py
import json
import numpy as np
from sklearn.cluster import DBSCAN

with open("image_features.json", "r") as f:
    data = json.load(f)

features = np.array([d["features"] for d in data])

# Find natural clusters
clustering = DBSCAN(eps=0.3, min_samples=3).fit(features)
labels = clustering.labels_

# Group by cluster
clusters = {}
for i, label in enumerate(labels):
    if label not in clusters:
        clusters[label] = []
    clusters[label].append(data[i]["path"])

# Save clusters
for label, paths in clusters.items():
    if label == -1:
        print(f"\nNoise (unclustered): {len(paths)} images")
    else:
        print(f"\nCluster {label}: {len(paths)} images")
        for p in paths[:5]:  # Show first 5
            print(f"  - {p}")

# Save for review
with open("clusters.json", "w") as f:
    json.dump({str(k): v for k, v in clusters.items()}, f, indent=2)

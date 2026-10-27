# quick_extract.py - save this in your screenshots folder
import torch
import clip
from PIL import Image
import json
from pathlib import Path

device = "cuda" if torch.cuda.is_available() else "cpu"
model, preprocess = clip.load("ViT-B/32", device=device)

screenshots = list(Path(".").glob("**/*.jpg")) + list(Path(".").glob("**/*.png"))

data = []
for img_path in screenshots:
    image = preprocess(Image.open(img_path).convert("RGB")).unsqueeze(0).to(device)
    with torch.no_grad():
        features = model.encode_image(image)
        features /= features.norm(dim=-1, keepdim=True)
    
    data.append({
        "path": str(img_path),
        "features": features.cpu().numpy().tolist()[0]
    })

# Save features
with open("image_features.json", "w") as f:
    json.dump(data, f)

print(f"Processed {len(data)} images")

"""
JedAI Vision - CLIP Inference Loop
====================================
What this file does:
  1. Loads your trained CLIP model (clip_swgemu_best.pth)
  2. Takes a screenshot every 0.5 seconds (2fps)
  3. Runs CLIP on each screenshot and outputs ALL labels + confidence scores
  4. If ANY label confidence is below 80%, saves the screenshot for review
  5. Logs everything to a file so you can review later

How to run:
  python jedai_vision.py

Requirements:
  pip install pillow torch clip mss

Author: JedAI Project
"""

import os
import time
import json
import torch
import torch.nn as nn
import clip
from PIL import Image
from datetime import datetime
import mss          # Fast screen capture library
import mss.tools


# ============================================================
# CONFIGURATION - Change these to match your setup
# ============================================================

# Path to your trained model file
MODEL_PATH = r"E:\SWGEMU_bot\Video capture\Desktop\screenshots\models\clip_swgemu_best.pth"

# Where to save screenshots that CLIP is unsure about
UNCERTAIN_SAVE_DIR = r"E:\SWGEMU_bot\Video capture\Desktop\screenshots\uncertain_captures"

# Where to save the log file (what CLIP saw and when)
LOG_PATH = r"E:\SWGEMU_bot\Video capture\Desktop\screenshots\jedai_vision_log.jsonl"

# How often to take a screenshot (0.5 = 2 times per second = 2fps)
CAPTURE_INTERVAL = 0.5

# Confidence threshold - anything BELOW this gets saved for review
# 0.80 = 80%
CONFIDENCE_THRESHOLD = 0.80

# Which CLIP model was used during training - must match what you trained with
CLIP_MODEL_NAME = "ViT-B/32"


# ============================================================
# THE MODEL - Same structure as train_clip.py
# This MUST match exactly or the model won't load correctly
# ============================================================

class CLIPClassifier(nn.Module):
    """
    This is the same class as in train_clip.py.
    It wraps CLIP with a small classification head on top.
    
    Think of it as:
      CLIP (frozen, knows about images generally)
        └── Classification head (trained by you, knows SWGemu states)
    """
    
    def __init__(self, clip_model, num_classes):
        super().__init__()
        self.clip_model = clip_model
        self.embedding_dim = clip_model.visual.output_dim
        
        # This small "brain" on top is what you actually trained
        # It takes CLIP's understanding of an image and maps it
        # to one of your game state labels
        self.classifier = nn.Sequential(
            nn.Linear(self.embedding_dim, 256),
            nn.ReLU(),
            nn.Dropout(0.2),
            nn.Linear(256, num_classes)
        )
    
    def forward(self, images):
        # Step 1: CLIP looks at the image and creates a description (features)
        with torch.no_grad():
            features = self.clip_model.encode_image(images)
        
        features = features.float()
        
        # Step 2: Your classifier takes those features and picks a game state
        logits = self.classifier(features)
        return logits


# ============================================================
# LOAD MODEL - Read the saved .pth file and rebuild the model
# ============================================================

def load_model(model_path, device):
    """
    Loads your trained model from disk.
    
    The .pth file contains:
      - The trained weights (what the model learned)
      - The label names (so we know what number=what label)
      - Which CLIP variant was used
    
    Returns the model ready to use, plus the list of labels.
    """
    print(f"Loading model from: {model_path}")
    
    # Load the saved data - map_location handles CPU/GPU differences
    checkpoint = torch.load(model_path, map_location=device)
    
    # Get the label list that was saved during training
    # This tells us: label 0 = "character creation", label 1 = "exploring" etc.
    labels = checkpoint['labels']
    num_classes = len(labels)
    
    print(f"Model knows {num_classes} labels: {labels}")
    
    # Rebuild CLIP itself
    clip_model, preprocess = clip.load(CLIP_MODEL_NAME, device=device)
    
    # Wrap it in your classifier
    model = CLIPClassifier(clip_model, num_classes)
    
    # Load the trained weights into the classifier
    # strict=False means it won't crash if minor differences exist
    model.load_state_dict(checkpoint['model_state_dict'], strict=False)
    
    # Put model in evaluation mode - disables dropout, stabilises predictions
    model.eval()
    model = model.to(device)
    
    print("Model loaded successfully.")
    return model, labels, preprocess


# ============================================================
# SCREENSHOT - Capture the current screen
# ============================================================

def capture_screen():
    """
    Takes a screenshot of your entire primary monitor.
    Returns a PIL Image object (same format as your training data).
    
    Uses mss which is much faster than PIL's ImageGrab.
    """
    with mss.mss() as sct:
        # monitor[2] = first monitor (monitor[0] = all monitors combined)
        monitor = sct.monitors[2]
        screenshot = sct.grab(monitor)
        
        # Convert from mss format to PIL Image
        img = Image.frombytes("RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")
        return img


# ============================================================
# INFERENCE - Run the model on a screenshot
# ============================================================

def run_inference(model, image, labels, preprocess, device):
    """
    Runs your model on a single screenshot.
    
    Returns a list of (label, confidence) pairs sorted by confidence,
    highest first.
    
    Example output:
      [
        ("exploring", 0.94),
        ("player_inv", 0.03),
        ("login screen", 0.01),
        ...
      ]
    """
    # Preprocess the image the same way training data was preprocessed
    # This resizes, normalises etc. to match what CLIP expects
    image_tensor = preprocess(image).unsqueeze(0).to(device)
    # unsqueeze(0) adds a batch dimension - model expects batches not single images
    
    with torch.no_grad():  # Don't calculate gradients - we're just predicting
        logits = model(image_tensor)
        
        # Softmax converts raw scores into probabilities that sum to 1.0
        # e.g. [2.1, 0.3, -0.5] becomes [0.85, 0.12, 0.03]
        probabilities = torch.softmax(logits, dim=1)[0]
    
    # Build results list
    results = []
    for idx, prob in enumerate(probabilities):
        results.append((labels[idx], prob.item()))
    
    # Sort by confidence, highest first
    results.sort(key=lambda x: x[1], reverse=True)
    
    return results


# ============================================================
# ANOMALY DETECTION - Check if the current state makes sense
# ============================================================

# Define which state transitions are IMPLAUSIBLE
# If we see these, it's probably a bad prediction not a real change
IMPLAUSIBLE_TRANSITIONS = {
    # (from_state, to_state) pairs that shouldn't happen instantly
    ("exploring", "login screen"),
    ("exploring", "server selection"),
    ("exploring", "character creation"),
    ("player_inv", "login screen"),
    ("survey_tool", "login screen"),
}

def check_anomaly(previous_label, current_label, current_confidence):
    """
    Checks if the current prediction makes sense given what we saw before.
    
    Returns True if this looks like an anomaly (probably a bad prediction).
    Returns False if the transition seems plausible.
    """
    if previous_label is None:
        return False  # No history yet, can't check
    
    # Check if this transition is in our implausible list
    transition = (previous_label, current_label)
    if transition in IMPLAUSIBLE_TRANSITIONS:
        return True
    
    return False


# ============================================================
# LOGGING - Save what the model saw to a file
# ============================================================

def log_frame(log_path, timestamp, results, saved_screenshot, anomaly_detected):
    """
    Saves one frame's worth of data to the log file.
    
    Uses JSONL format (one JSON object per line) which is easy to read
    and doesn't require loading the whole file at once.
    
    Each line looks like:
    {"timestamp": "2026-03-09 22:15:03", "top_label": "exploring", 
     "confidence": 0.94, "all_labels": [...], "saved": false, "anomaly": false}
    """
    log_entry = {
        "timestamp": timestamp,
        "top_label": results[0][0],           # Best guess label
        "top_confidence": results[0][1],       # Confidence for best guess
        "all_predictions": [                   # All labels with scores
            {"label": label, "confidence": round(conf, 4)} 
            for label, conf in results
        ],
        "screenshot_saved": saved_screenshot,  # Did we save this frame?
        "anomaly_detected": anomaly_detected   # Did this look wrong?
    }
    
    # Append to log file (one line per frame)
    with open(log_path, 'a') as f:
        f.write(json.dumps(log_entry) + '\n')


# ============================================================
# MAIN LOOP - The heart of JedAI Vision
# ============================================================

def main():
    print("=" * 60)
    print("JedAI Vision - Starting up")
    print("=" * 60)
    
    # Setup
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Using device: {device}")
    
    # Create uncertain captures folder if it doesn't exist
    os.makedirs(UNCERTAIN_SAVE_DIR, exist_ok=True)
    
    # Load the model
    model, labels, preprocess = load_model(MODEL_PATH, device)
    
    print(f"\nCapturing every {CAPTURE_INTERVAL}s ({1/CAPTURE_INTERVAL:.0f}fps)")
    print(f"Saving uncertain frames (below {CONFIDENCE_THRESHOLD*100:.0f}%) to: {UNCERTAIN_SAVE_DIR}")
    print(f"Logging to: {LOG_PATH}")
    print("\nPress Ctrl+C to stop.\n")
    print("-" * 60)
    
    # State tracking for anomaly detection
    previous_label = None
    previous_confident_label = None  # Last label we were confident about
    frame_count = 0
    uncertain_count = 0
    
    try:
        while True:
            loop_start = time.time()
            frame_count += 1
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            
            # Step 1: Grab screenshot
            screenshot = capture_screen()
            
            # Step 2: Run CLIP inference
            results = run_inference(model, screenshot, labels, preprocess, device)
            
            top_label = results[0][0]
            top_confidence = results[0][1]
            
            # Step 3: Check for anomalies
            anomaly = check_anomaly(previous_confident_label, top_label, top_confidence)
            
            # Step 4: Decide whether to save this screenshot
            # Save if: confidence is low OR an anomaly was detected
            should_save = (top_confidence < CONFIDENCE_THRESHOLD) or anomaly
            saved_path = None
            
            if should_save:
                uncertain_count += 1
                # Filename includes timestamp and top prediction for easy review
                safe_label = top_label.replace(" ", "_").replace("/", "-")
                filename = f"uncertain_{timestamp.replace(':', '-').replace(' ', '_')}_{safe_label}_{top_confidence:.2f}.png"
                saved_path = os.path.join(UNCERTAIN_SAVE_DIR, filename)
                screenshot.save(saved_path)
            
            # Step 5: Log this frame
            log_frame(LOG_PATH, timestamp, results, saved_path is not None, anomaly)
            
            # Step 6: Print to console so you can watch in real time
            confidence_bar = "█" * int(top_confidence * 20)  # Visual bar
            status = ""
            if anomaly:
                status = " ⚠️  ANOMALY"
            elif should_save:
                status = " 📸 SAVED"
            
            print(f"[{timestamp}] {top_label:<30} {top_confidence:.1%} {confidence_bar}{status}")
            
            # Show second-best guess if top confidence is low - helps you understand confusion
            if top_confidence < CONFIDENCE_THRESHOLD:
                print(f"           2nd: {results[1][0]:<28} {results[1][1]:.1%}")
            
            # Update state history
            # Only update "confident" history if we're actually confident
            if top_confidence >= CONFIDENCE_THRESHOLD and not anomaly:
                previous_confident_label = top_label
            previous_label = top_label
            
            # Step 7: Wait until next capture
            # Subtract processing time so we stay close to target interval
            elapsed = time.time() - loop_start
            sleep_time = max(0, CAPTURE_INTERVAL - elapsed)
            time.sleep(sleep_time)
    
    except KeyboardInterrupt:
        # Ctrl+C pressed - clean shutdown
        print("\n" + "=" * 60)
        print("JedAI Vision stopped.")
        print(f"Frames processed: {frame_count}")
        print(f"Uncertain captures saved: {uncertain_count}")
        print(f"Uncertain captures location: {UNCERTAIN_SAVE_DIR}")
        print(f"Full log: {LOG_PATH}")
        print("=" * 60)


# ============================================================
# ENTRY POINT
# This means: only run main() if this file is run directly
# Not if it's imported by another script
# ============================================================

if __name__ == "__main__":
    main()

"""
CLIP Training Script for SWGEmu Game State Classification

Usage:
    python train_clip.py --data_dir data/processed --output_dir models --epochs 10
"""

import os
import json
import argparse
from pathlib import Path

import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader
from torchvision import transforms
from PIL import Image
import clip
from tqdm import tqdm


class SWGEmuDataset(Dataset):
    """Dataset for SWGEmu screenshots that discovers files automatically."""
    
    def __init__(self, data_dir, preprocess):
        self.data_dir = Path(data_dir)
        self.preprocess = preprocess
        self.data = []
        
        # Define the image formats you actually have
        valid_extensions = {'.png', '.jpg', '.jpeg'}
        
        # Scan every subfolder in the 'models' directory
        for subdir in self.data_dir.iterdir():
            if subdir.is_dir():
                label = subdir.name
                for img_path in subdir.glob('*'):
                    if img_path.suffix.lower() in valid_extensions:
                        self.data.append({
                            'path': img_path,
                            'label': label
                        })
        
        # Create label mapping from the folders found
        self.labels = sorted(list(set(item['label'] for item in self.data)))
        self.label_to_idx = {label: idx for idx, label in enumerate(self.labels)}
        
        if not self.data:
            print(f"ERROR: No images found in {data_dir}")
        else:
            print(f"Loaded {len(self.data)} images from {len(self.labels)} folders.")

    def __len__(self):
        return len(self.data)
    
    def __getitem__(self, idx):
        item = self.data[idx]
        image = Image.open(item['path']).convert('RGB')
        image = self.preprocess(image)
        label = self.label_to_idx[item['label']]
        return image, label


class CLIPClassifier(nn.Module):
    """CLIP with classification head for fine-tuning."""
    
    def __init__(self, clip_model, num_classes, freeze_clip=True):
        super().__init__()
        self.clip_model = clip_model
        
        # Freeze CLIP if doing linear probe
        if freeze_clip:
            for param in self.clip_model.parameters():
                param.requires_grad = False
        
        # Get embedding dimension
        self.embedding_dim = clip_model.visual.output_dim
        
        # Classification head
        self.classifier = nn.Sequential(
            nn.Linear(self.embedding_dim, 256),
            nn.ReLU(),
            nn.Dropout(0.2),
            nn.Linear(256, num_classes)
        )
    
    def forward(self, images):
        # Get CLIP image features
        if self.training and not self.clip_model.visual.training:
            with torch.no_grad():
                features = self.clip_model.encode_image(images)
        else:
            features = self.clip_model.encode_image(images)
        
        features = features.float()
        
        # Classify
        logits = self.classifier(features)
        return logits


def train_epoch(model, dataloader, criterion, optimizer, device):
    """Train for one epoch."""
    model.train()
    total_loss = 0
    correct = 0
    total = 0
    
    pbar = tqdm(dataloader, desc="Training")
    for images, labels in pbar:
        images = images.to(device)
        labels = labels.to(device)
        
        # Forward
        optimizer.zero_grad()
        outputs = model(images)
        loss = criterion(outputs, labels)
        
        # Backward
        loss.backward()
        optimizer.step()
        
        # Stats
        total_loss += loss.item()
        _, predicted = outputs.max(1)
        total += labels.size(0)
        correct += predicted.eq(labels).sum().item()
        
        pbar.set_postfix({
            'loss': total_loss / (pbar.n + 1),
            'acc': 100. * correct / total
        })
    
    return total_loss / len(dataloader), 100. * correct / total


def validate(model, dataloader, criterion, device):
    """Validate model."""
    model.eval()
    total_loss = 0
    correct = 0
    total = 0
    
    with torch.no_grad():
        for images, labels in tqdm(dataloader, desc="Validating"):
            images = images.to(device)
            labels = labels.to(device)
            
            outputs = model(images)
            loss = criterion(outputs, labels)
            
            total_loss += loss.item()
            _, predicted = outputs.max(1)
            total += labels.size(0)
            correct += predicted.eq(labels).sum().item()
    
    return total_loss / len(dataloader), 100. * correct / total


def main():
    parser = argparse.ArgumentParser(description='Train CLIP on SWGEmu data')
    parser.add_argument('--data_dir', type=str, default='data/processed',
                        help='Directory containing processed images')
    parser.add_argument('--labels_file', type=str, default='data/labels.json',
                        help='JSON file with image labels')
    parser.add_argument('--output_dir', type=str, default='models',
                        help='Directory to save models')
    parser.add_argument('--clip_model', type=str, default='ViT-B/32',
                        choices=['RN50', 'RN101', 'ViT-B/32', 'ViT-B/16', 'ViT-L/14'],
                        help='CLIP model variant')
    parser.add_argument('--batch_size', type=int, default=32,
                        help='Batch size')
    parser.add_argument('--epochs', type=int, default=10,
                        help='Number of epochs')
    parser.add_argument('--lr', type=float, default=1e-5,
                        help='Learning rate')
    parser.add_argument('--freeze_clip', action='store_true',
                        help='Freeze CLIP and train only classifier (linear probe)')
    parser.add_argument('--device', type=str, default='auto',
                        help='Device: cuda, cpu, or auto')
    
    args = parser.parse_args()
    
    # Setup device
    if args.device == 'auto':
        device = 'cuda' if torch.cuda.is_available() else 'cpu'
    else:
        device = args.device
    
    print(f"Using device: {device}")
    
    # Create output directory
    os.makedirs(args.output_dir, exist_ok=True)
    
    # Load CLIP
    print(f"Loading CLIP model: {args.clip_model}")
    clip_model, preprocess = clip.load(args.clip_model, device=device)
    clip_model = clip_model.float()
    
    # Load dataset
    dataset = SWGEmuDataset(args.data_dir, preprocess)
    
    # Split train/val
    train_size = int(0.8 * len(dataset))
    val_size = len(dataset) - train_size
    train_dataset, val_dataset = torch.utils.data.random_split(dataset, [train_size, val_size])
    
# Change num_workers from 4 to 0 for Windows stability
    train_loader = DataLoader(train_dataset, batch_size=args.batch_size, shuffle=True, num_workers=0)
    val_loader = DataLoader(val_dataset, batch_size=args.batch_size, shuffle=False, num_workers=0)
    
    # Create model
    num_classes = len(dataset.labels)
    model = CLIPClassifier(clip_model, num_classes, freeze_clip=args.freeze_clip)
    model = model.to(device)
    
    print(f"Model: {args.clip_model}")
    print(f"Classes: {num_classes}")
    print(f"Training samples: {len(train_dataset)}")
    print(f"Validation samples: {len(val_dataset)}")
    print(f"Freeze CLIP: {args.freeze_clip}")
    
    # Loss and optimizer
    criterion = nn.CrossEntropyLoss()
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=args.epochs)
    
    # Training loop
    best_val_acc = 0
    
    for epoch in range(args.epochs):
        print(f"\nEpoch {epoch + 1}/{args.epochs}")
        
        train_loss, train_acc = train_epoch(model, train_loader, criterion, optimizer, device)
        val_loss, val_acc = validate(model, val_loader, criterion, device)
        
        scheduler.step()
        
        print(f"Train Loss: {train_loss:.4f}, Train Acc: {train_acc:.2f}%")
        print(f"Val Loss: {val_loss:.4f}, Val Acc: {val_acc:.2f}%")
        
        # Save best model
        if val_acc > best_val_acc:
            best_val_acc = val_acc
            checkpoint = {
                'epoch': epoch,
                'model_state_dict': model.state_dict(),
                'optimizer_state_dict': optimizer.state_dict(),
                'val_acc': val_acc,
                'labels': dataset.labels,
                'clip_model': args.clip_model
            }
            save_path = os.path.join(args.output_dir, 'clip_swgemu_best.pth')
            torch.save(checkpoint, save_path)
            print(f"Saved best model: {save_path}")
    
    # Save final model
    final_path = os.path.join(args.output_dir, 'clip_swgemu_final.pth')
    torch.save({
        'model_state_dict': model.state_dict(),
        'labels': dataset.labels,
        'clip_model': args.clip_model
    }, final_path)
    
    print(f"\nTraining complete!")
    print(f"Best validation accuracy: {best_val_acc:.2f}%")
    print(f"Models saved to: {args.output_dir}")


if __name__ == '__main__':
    main()

import tkinter as tk
import math

class CompassOverlay:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("16-Point Precision Compass")
        
        # Window settings
        self.root.geometry("400x400") 
        self.root.attributes("-topmost", True)      
        self.root.attributes("-alpha", 0.6)         
        
        # Windows transparency setup
        self.root.config(bg='white')
        self.root.wm_attributes("-transparentcolor", "white")
        
        self.canvas = tk.Canvas(self.root, bg="white", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        
        self.root.bind("<Configure>", self.draw_compass)
        self.root.mainloop()

    def draw_compass(self, event=None):
        self.canvas.delete("all")
        w = self.root.winfo_width()
        h = self.root.winfo_height()
        cx, cy = w // 2, h // 2
        radius = min(cx, cy) - 40
        
        # Colors
        main_color = "red"     # N, E, S, W
        ord_color = "blue"     # NE, SE, SW, NW
        helper_color = "#555555"  # NNE, ENE, ESE, etc. (The "Borders")

        # Define all 16 angles (360 / 16 = 22.5 degrees each)
        # We start at -90 degrees because 0 in math is "East"
        directions = [
            (0, "N", main_color, 2), (22.5, "NNE", helper_color, 1),
            (45, "NE", ord_color, 2), (67.5, "ENE", helper_color, 1),
            (90, "E", main_color, 2), (112.5, "ESE", helper_color, 1),
            (135, "SE", ord_color, 2), (157.5, "SSE", helper_color, 1),
            (180, "S", main_color, 2), (202.5, "SSW", helper_color, 1),
            (225, "SW", ord_color, 2), (247.5, "WSW", helper_color, 1),
            (270, "W", main_color, 2), (292.5, "WNW", helper_color, 1),
            (315, "NW", ord_color, 2), (337.5, "NNW", helper_color, 1)
        ]

        for angle_deg, label, color, thickness in directions:
            # Convert degrees to radians and adjust for Screen coordinates
            angle_rad = math.radians(angle_deg - 90)
            
            x_end = cx + radius * math.cos(angle_rad)
            y_end = cy + radius * math.sin(angle_rad)
            
            # Draw the line
            dash_pattern = (4, 4) if thickness == 1 else None
            self.canvas.create_line(cx, cy, x_end, y_end, fill=color, width=thickness, dash=dash_pattern)
            
            # Draw label only for your 8 main training directions
            if thickness == 2:
                text_x = cx + (radius + 20) * math.cos(angle_rad)
                text_y = cy + (radius + 20) * math.sin(angle_rad)
                self.canvas.create_text(text_x, text_y, text=label, fill=color, font=("Arial", 10, "bold"))

        # Center indicator
        self.canvas.create_oval(cx-3, cy-3, cx+3, cy+3, fill="black")

if __name__ == "__main__":
    CompassOverlay()

# A see-through 16-point compass overlay (Windows) for lining up the arrow
# directions while labelling.  run: python tools/validation/compass_overlay.py

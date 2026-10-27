"""
HUD readers: turn a screenshot into game state using the YOLO models.

    coords = CoordinateReader.from_settings(settings)
    x, y, z = coords.read(frame)

    hud = HudReader.from_settings(settings)
    for wp in hud.waypoints(frame):
        print(wp.name, wp.distance, wp.arrow)

Model cascade for waypoints:  group -> panel -> components (name | distance)
                                              -> arrow (from the panel crop)
Model cascade for position:   minimap -> coordinate fields -> digits
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterator

import cv2

from .screen import safe_crop
from .text import assemble_digits, parse_distance


def _load_yolo(path):
    from ultralytics import YOLO  # heavy import, only when models are loaded

    if not path.exists():
        raise FileNotFoundError(
            f"Model weights not found: {path}\n"
            f"Put the .pt file there, or set paths.models_dir in config/settings.local.yaml"
        )
    return YOLO(str(path))


def read_digits(model, crop, conf: float = 0.3) -> str | None:
    """Run a per-character YOLO digit model on a crop and return the string."""
    if crop is None or crop.size == 0:
        return None
    results = model.predict(crop, conf=conf, verbose=False)
    if not results or not results[0].boxes:
        return None
    names = model.names
    detections = [(float(b.xyxy[0][0]), names[int(b.cls[0])]) for b in results[0].boxes]
    return assemble_digits(detections)


# =============================================================================
class CoordinateReader:
    """Reads the player's x/y/z from the minimap coordinate readout."""

    def __init__(self, minimap_model, coords_model, digit_model):
        self.m_minimap = minimap_model
        self.m_coords = coords_model
        self.m_digit = digit_model

    @classmethod
    def from_settings(cls, settings):
        return cls(
            _load_yolo(settings.model_path("minimap")),
            _load_yolo(settings.model_path("coords")),
            _load_yolo(settings.model_path("co_digit")),
        )

    def read(self, frame):
        """Returns (x, y, z); any of them can be None. Ground plane is X/Z."""
        res_mm = self.m_minimap.predict(frame, conf=0.4, verbose=False)
        if not res_mm or not res_mm[0].boxes:
            return None, None, None
        mx1, my1, mx2, my2 = map(int, res_mm[0].boxes[0].xyxy[0])
        mm_crop = safe_crop(frame, mx1, my1, mx2, my2)
        if mm_crop is None:
            return None, None, None

        res_co = self.m_coords.predict(mm_crop, conf=0.4, verbose=False)
        if not res_co or not res_co[0].boxes:
            return None, None, None

        values = {}
        names = self.m_coords.names
        for box in res_co[0].boxes:
            field = names[int(box.cls[0])]
            bx1, by1, bx2, by2 = map(int, box.xyxy[0])
            raw = read_digits(self.m_digit, safe_crop(mm_crop, bx1, by1, bx2, by2))
            if raw is not None:
                try:
                    values[field] = float(raw)
                except ValueError:
                    pass
        return values.get("x_pos"), values.get("y_pos"), values.get("z_pos")


# =============================================================================
@dataclass
class Waypoint:
    name: str | None
    distance: int | None
    center: tuple[int, int]      # screen position of the panel
    arrow: str | None            # N, NE, E, SE, S, SW, W, NW (relative bearing)


class WaypointReader:
    """Reads every waypoint row in the waypoint window."""

    def __init__(self, group_model, panel_model, comp_model, arrow_model, digit_model, ocr):
        self.m_group = group_model
        self.m_panel = panel_model
        self.m_comp = comp_model
        self.m_arrow = arrow_model
        self.m_digit = digit_model
        self.ocr = ocr

    @classmethod
    def from_settings(cls, settings):
        import easyocr

        try:
            ocr = easyocr.Reader(["en"], gpu=True, verbose=False)
        except Exception:
            ocr = easyocr.Reader(["en"], gpu=False, verbose=False)
        return cls(
            _load_yolo(settings.model_path("group")),
            _load_yolo(settings.model_path("panel")),
            _load_yolo(settings.model_path("comp")),
            _load_yolo(settings.model_path("arrow")),
            _load_yolo(settings.model_path("wp_digit")),
            ocr,
        )

    def _ocr_name(self, crop):
        if crop is None or crop.size == 0:
            return None
        try:
            results = self.ocr.readtext(crop, detail=0)
            return " ".join(results).strip() if results else None
        except Exception:
            return None

    def arrow_direction(self, panel_crop):
        if panel_crop is None or panel_crop.size == 0:
            return None
        gray = cv2.cvtColor(panel_crop, cv2.COLOR_BGR2GRAY)
        upscaled = cv2.resize(cv2.merge([gray, gray, gray]), None, fx=3, fy=3,
                              interpolation=cv2.INTER_CUBIC)
        results = self.m_arrow.predict(upscaled, conf=0.3, verbose=False)
        if not results or len(results[0].boxes) == 0:
            return None
        return self.m_arrow.names[int(results[0].boxes[0].cls[0])]

    def waypoints(self, frame, should_stop: Callable[[], bool] = lambda: False
                  ) -> Iterator[Waypoint]:
        res_g = self.m_group.predict(frame, conf=0.4, verbose=False)
        if not res_g or not res_g[0].boxes:
            return
        for g_box in res_g[0].boxes:
            if should_stop():
                return
            gx1, gy1, gx2, gy2 = map(int, g_box.xyxy[0])
            group_crop = safe_crop(frame, gx1, gy1, gx2, gy2)
            if group_crop is None:
                continue
            res_p = self.m_panel.predict(group_crop, conf=0.4, verbose=False)
            if not res_p or not res_p[0].boxes:
                continue
            for p_box in res_p[0].boxes:
                if should_stop():
                    return
                px1, py1, px2, py2 = map(int, p_box.xyxy[0])
                p_crop = safe_crop(group_crop, px1, py1, px2, py2)
                if p_crop is None:
                    continue
                center = (gx1 + (px1 + px2) // 2, gy1 + (py1 + py2) // 2)

                name, dist = "unknown", None
                res_c = self.m_comp.predict(p_crop, conf=0.3, verbose=False)
                if res_c and res_c[0].boxes:
                    for c_box in res_c[0].boxes:
                        cx1, cy1, cx2, cy2 = map(int, c_box.xyxy[0])
                        c_crop = safe_crop(p_crop, cx1, cy1, cx2, cy2)
                        if c_crop is None:
                            continue
                        if int(c_box.cls[0]) == 0:      # 0 = description
                            name = self._ocr_name(c_crop)
                        else:                           # 1 = distance
                            dist = parse_distance(read_digits(self.m_digit, c_crop))
                yield Waypoint(name, dist, center, self.arrow_direction(p_crop))


# =============================================================================
class HudReader:
    """Everything the navigator reads from the screen."""

    def __init__(self, coords: CoordinateReader, waypoints: WaypointReader):
        self.coords = coords
        self.waypoint_reader = waypoints

    @classmethod
    def from_settings(cls, settings):
        return cls(CoordinateReader.from_settings(settings),
                   WaypointReader.from_settings(settings))

    def read_coords(self, frame):
        return self.coords.read(frame)

    def waypoints(self, frame, should_stop=lambda: False):
        return self.waypoint_reader.waypoints(frame, should_stop)

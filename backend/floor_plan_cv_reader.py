#!/usr/bin/env python
"""Read a generated AAU map PDF with image/CV methods.

This reader intentionally does not use the companion details JSON. It renders
the PDF to pixels, segments room fills and red door marks with OpenCV, reads
machine text/label positions from the PDF, and emits an aau_map_details JSON
file compatible with the existing translation/loading tools.
"""

from __future__ import annotations

import argparse
import csv
import html
import io
import json
import math
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import cv2
import numpy as np

import floor_plan_details_reader


PDF_METADATA_PREFIX = "AAU_MAP_METADATA_JSON="
SCALE_BLOCK_BEGIN = "AAU_SCALE_BLOCK_BEGIN"
SCALE_BLOCK_END = "AAU_SCALE_BLOCK_END"
DOOR_LENGTH_M = 0.72


TYPE_COLORS = {
    "ROOM_GENERIC": "#B3D9FF",
    "ROOM_OFFICE": "#A8CCF0",
    "ROOM_CLASSROOM": "#B3D9FF",
    "ROOM_LECTURE_HALL": "#9EC5E8",
    "ROOM_LAB": "#8BB8E0",
    "ROOM_MEETING": "#B3D9FF",
    "ROOM_STORAGE": "#C8DFF5",
    "ROOM_UTILITY": "#C8DFF5",
    "CORRIDOR": "#E0E0E0",
    "CORRIDOR_SEGMENT": "#E0E0E0",
    "LOBBY": "#D8D8D8",
    "WAITING_AREA": "#D8D8D8",
    "RECEPTION": "#D8D8D8",
    "ENTRANCE": "#90EE90",
    "ENTRANCE_SECONDARY": "#A8F0A8",
    "EXIT_EMERGENCY": "#FFB3B3",
    "STAIRCASE": "#FFD699",
    "ELEVATOR": "#FFCC80",
    "ESCALATOR": "#FFD699",
    "RAMP": "#FFD699",
    "BRIDGE": "#FFD699",
    "TUNNEL": "#D0D0D0",
    "COVERED_WALKWAY": "#D8E8D8",
    "OUTDOOR_PATH": "#C8F0C8",
    "OUTDOOR_PLAZA": "#C8F0C8",
    "OUTDOOR_COURTYARD": "#C8F0C8",
    "OUTDOOR_STAIRS": "#C8F0C8",
    "PARKING": "#D0E8D0",
    "RESTROOM": "#FFB3D9",
    "RESTROOM_ACCESSIBLE": "#FFB3D9",
    "CAFETERIA": "#FFEB99",
    "CAFE": "#FFEB99",
    "LIBRARY": "#FFEB99",
    "GYM": "#FFEB99",
    "AUDITORIUM": "#FFEB99",
    "SHOP": "#FFEB99",
    "INACCESSIBLE": "#808080",
}

ROOM_FILL_COLORS = sorted(set(TYPE_COLORS.values()) - {"#F0F0F0"})
ROOM_ID_RE = re.compile(r"R\d{3}")
DOOR_ID_RE = re.compile(r"D\d{3}[ULRD]")
DIMENSION_LINE_RE = re.compile(
    r"\b\d+(?:[.,]\d+)?\s*[xX]\s*\d+(?:[.,]\d+)?\s*M?\b"
)
BROKEN_DIMENSION_LINE_RE = re.compile(
    r"\b[xX]\s*[.,]?\d+(?:[.,]\d+)?\s*M?\b", re.IGNORECASE
)
DOOR_COUNT_LINE_RE = re.compile(
    r"\b\d+\s+(?:DOORS?|DØRE?|DOERE?|DOR|DORE)\b",
    re.IGNORECASE,
)
WALL_DISTANCE_LINE_RE = re.compile(
    r"\b(?:WALL|VÆG|VAEG|NÆRMESTE\s+VÆG|NAERMESTE\s+VAEG)\s+\d+(?:[.,]\d+)?\s*M?\b",
    re.IGNORECASE,
)
WORD_RE = re.compile(
    r'<word xMin="([^"]+)" yMin="([^"]+)" xMax="([^"]+)" yMax="([^"]+)">(.*?)</word>'
)
PAGE_RE = re.compile(r'<page width="([^"]+)" height="([^"]+)">')
OCR_DIGIT_TRANSLATION = str.maketrans(
    {
        "O": "0",
        "Q": "0",
        "I": "1",
        "L": "1",
        "|": "1",
        "S": "5",
        "B": "8",
    }
)
ROOM_CODE_RE = re.compile(
    r"\b(?:[A-Z]?\d+(?:[.-]\d+){1,}[A-Za-z]?|[A-Z]\d+)\b",
    re.IGNORECASE,
)
ROOM_TRAILING_NOISE_TOKENS = {"I", "J", "L", "EE"}


@dataclass(frozen=True)
class PdfWord:
    text: str
    x0: float
    y0_top: float
    x1: float
    y1_top: float

    @property
    def center_top(self) -> tuple[float, float]:
        return ((self.x0 + self.x1) / 2, (self.y0_top + self.y1_top) / 2)


@dataclass(frozen=True)
class MapGeometry:
    page_width_pt: float
    page_height_pt: float
    dpi: int
    scale_pt_per_m: float
    bounds: dict[str, float]
    frame: tuple[float, float, float, float]

    @property
    def px_per_pt(self) -> float:
        return self.dpi / 72.0

    @property
    def px_per_m(self) -> float:
        return self.scale_pt_per_m * self.px_per_pt

    @property
    def map_origin_pdf_pt(self) -> tuple[float, float]:
        frame_x, frame_y, frame_w, frame_h = self.frame
        width_m = self.bounds["max_x"] - self.bounds["min_x"]
        height_m = self.bounds["max_y"] - self.bounds["min_y"]
        x0 = frame_x + (frame_w - width_m * self.scale_pt_per_m) / 2
        y0 = frame_y + (frame_h - height_m * self.scale_pt_per_m) / 2
        return x0, y0

    def crop_box_px(self) -> tuple[int, int, int, int]:
        frame_x, frame_y, frame_w, frame_h = self.frame
        x0 = round(frame_x * self.px_per_pt)
        x1 = round((frame_x + frame_w) * self.px_per_pt)
        y0 = round((self.page_height_pt - frame_y - frame_h) * self.px_per_pt)
        y1 = round((self.page_height_pt - frame_y) * self.px_per_pt)
        return x0, y0, x1, y1

    def pixel_to_world(self, x_px: float, y_px: float) -> tuple[float, float]:
        x_pt = x_px / self.px_per_pt
        y_pt = self.page_height_pt - y_px / self.px_per_pt
        return self.pdf_to_world(x_pt, y_pt)

    def top_text_to_world(self, x_pt: float, y_top_pt: float) -> tuple[float, float]:
        return self.pdf_to_world(x_pt, self.page_height_pt - y_top_pt)

    def pdf_to_world(self, x_pt: float, y_pt: float) -> tuple[float, float]:
        x0, y0 = self.map_origin_pdf_pt
        return (
            self.bounds["min_x"] + (x_pt - x0) / self.scale_pt_per_m,
            self.bounds["min_y"] + (y_pt - y0) / self.scale_pt_per_m,
        )

    def world_to_pdf(self, x_m: float, y_m: float) -> tuple[float, float]:
        x0, y0 = self.map_origin_pdf_pt
        return (
            x0 + (x_m - self.bounds["min_x"]) * self.scale_pt_per_m,
            y0 + (y_m - self.bounds["min_y"]) * self.scale_pt_per_m,
        )


@dataclass(frozen=True)
class ImageGeometry:
    image_width_px: int
    image_height_px: int
    px_per_m: float

    def crop_box_px(self) -> tuple[int, int, int, int]:
        return 0, 0, self.image_width_px, self.image_height_px

    def pixel_to_world(self, x_px: float, y_px: float) -> tuple[float, float]:
        return (
            x_px / self.px_per_m,
            (self.image_height_px - y_px) / self.px_per_m,
        )

    def top_text_to_world(self, x_px: float, y_top_px: float) -> tuple[float, float]:
        return self.pixel_to_world(x_px, y_top_px)


def run_text(command: list[str]) -> str:
    completed = subprocess.run(command, check=True, capture_output=True, text=True)
    return completed.stdout


def render_pdf(pdf_path: Path, dpi: int, output_prefix: Path) -> Path:
    subprocess.run(
        [
            "pdftoppm",
            "-r",
            str(dpi),
            "-png",
            "-singlefile",
            str(pdf_path),
            str(output_prefix),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return output_prefix.with_suffix(".png")


def parse_number(value: str) -> str | int | float:
    value = value.strip()
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    if re.fullmatch(r"-?\d+(?:\.\d+)?", value):
        return float(value)
    return value


def extract_scale_block(pdf_text: str) -> dict[str, Any]:
    in_block = False
    scale: dict[str, Any] = {}
    for raw_line in pdf_text.splitlines():
        line = raw_line.strip()
        if SCALE_BLOCK_BEGIN in line:
            in_block = True
            continue
        if SCALE_BLOCK_END in line:
            return scale
        if not in_block:
            continue
        match = re.search(r"(AAU_SCALE_[A-Z0-9_]+)=([^ ]+)", line)
        if match:
            scale[match.group(1)] = parse_number(match.group(2))
    raise ValueError(f"PDF does not contain {SCALE_BLOCK_BEGIN}/{SCALE_BLOCK_END}")


def extract_metadata(pdf_text: str) -> dict[str, Any]:
    for line in pdf_text.splitlines():
        if PDF_METADATA_PREFIX not in line:
            metadata = None
        else:
            metadata = line.split(PDF_METADATA_PREFIX, 1)[1].strip()
        if metadata:
            try:
                return json.loads(metadata)
            except json.JSONDecodeError:
                break
    compact = {
        key: value
        for key, value in re.findall(r"(AAU_MAP_META_[A-Z_]+)=([^\s]+)", pdf_text)
    }
    if compact:
        return {
            "schema": "aau_map_pdf",
            "version": 1,
            "building_name": unquote(compact.get("AAU_MAP_META_BUILDING", "")),
            "floor_index": int(compact.get("AAU_MAP_META_FLOOR_INDEX", "0")),
            "floor_name": unquote(compact.get("AAU_MAP_META_FLOOR", "")),
            "spaces_count": int(compact.get("AAU_MAP_META_SPACES_COUNT", "0")),
            "doors_count": int(compact.get("AAU_MAP_META_DOORS_COUNT", "0")),
        }
    raise ValueError(f"PDF does not contain {PDF_METADATA_PREFIX} metadata")


def read_pdf_text(pdf_path: Path) -> str:
    layout = run_text(["pdftotext", "-layout", str(pdf_path), "-"])
    raw = run_text(["pdftotext", "-raw", str(pdf_path), "-"])
    return layout if raw in layout else f"{layout}\n{raw}"


def read_pdf_words(pdf_path: Path) -> tuple[float, float, list[PdfWord]]:
    bbox_html = run_text(["pdftotext", "-bbox", str(pdf_path), "-"])
    page_match = PAGE_RE.search(bbox_html)
    if not page_match:
        raise ValueError("Could not read page size from pdftotext -bbox output")
    page_width, page_height = map(float, page_match.groups())
    words: list[PdfWord] = []
    for match in WORD_RE.finditer(bbox_html):
        text = html.unescape(match.group(5))
        words.append(
            PdfWord(
                text=text,
                x0=float(match.group(1)),
                y0_top=float(match.group(2)),
                x1=float(match.group(3)),
                y1_top=float(match.group(4)),
            )
        )
    return page_width, page_height, words


def clean_ocr_text(text: str) -> str:
    text = text.strip()
    if "\t" in text or "\n" in text or "\r" in text:
        return ""
    return re.sub(r"\s+", " ", text)


def hex_to_bgr(value: str) -> np.ndarray:
    return np.array(
        [int(value[5:7], 16), int(value[3:5], 16), int(value[1:3], 16)],
        dtype=np.uint8,
    )


def hex_to_rgb_tuple(value: str) -> tuple[int, int, int]:
    return (int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16))


def room_fill_mask(crop_bgr: np.ndarray, tolerance: int) -> np.ndarray:
    mask = np.zeros(crop_bgr.shape[:2], dtype=np.uint8)
    for color in ROOM_FILL_COLORS:
        bgr = hex_to_bgr(color).astype(np.int16)
        lower = np.maximum(0, bgr - tolerance).astype(np.uint8)
        upper = np.minimum(255, bgr + tolerance).astype(np.uint8)
        mask = cv2.bitwise_or(mask, cv2.inRange(crop_bgr, lower, upper))

    kernel = np.ones((3, 3), dtype=np.uint8)
    return cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)


def door_mask(crop_bgr: np.ndarray) -> np.ndarray:
    lower = np.array([0, 0, 150], dtype=np.uint8)
    upper = np.array([120, 120, 255], dtype=np.uint8)
    mask = cv2.inRange(crop_bgr, lower, upper)
    kernel = np.ones((3, 3), dtype=np.uint8)
    return cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)


def detect_scale_bar_pixels(image_bgr: np.ndarray) -> float | None:
    height, width = image_bgr.shape[:2]
    roi_top = int(height * 0.72)
    roi_right = int(width * 0.45)
    roi = image_bgr[roi_top:height, 0:roi_right]
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    dark = cv2.inRange(gray, 0, 80)
    kernel = np.ones((2, 12), dtype=np.uint8)
    dark = cv2.morphologyEx(dark, cv2.MORPH_CLOSE, kernel)
    components, _, stats, _ = cv2.connectedComponentsWithStats(dark)

    candidates: list[tuple[int, int, int, int]] = []
    for label_idx in range(1, components):
        left = int(stats[label_idx, cv2.CC_STAT_LEFT])
        top = int(stats[label_idx, cv2.CC_STAT_TOP])
        box_width = int(stats[label_idx, cv2.CC_STAT_WIDTH])
        box_height = int(stats[label_idx, cv2.CC_STAT_HEIGHT])
        area = int(stats[label_idx, cv2.CC_STAT_AREA])
        if box_width < 80 or box_height > 36:
            continue
        if box_width / max(box_height, 1) < 8:
            continue
        if area < box_width * 0.35:
            continue
        candidates.append((box_width, box_height, left, top))

    if not candidates:
        return None
    return float(max(candidates, key=lambda item: item[0])[0])


def resolve_pixels_per_meter(
    image_bgr: np.ndarray,
    *,
    pixels_per_meter: float | None,
    scale_bar_meters: float | None,
) -> tuple[float, list[str]]:
    warnings: list[str] = []
    if pixels_per_meter and pixels_per_meter > 0:
        return pixels_per_meter, warnings

    if scale_bar_meters and scale_bar_meters > 0:
        scale_bar_pixels = detect_scale_bar_pixels(image_bgr)
        if scale_bar_pixels:
            return scale_bar_pixels / scale_bar_meters, warnings
        warnings.append(
            "Could not detect the scale bar in the image; using 1 px/m fallback"
        )

    warnings.append(
        "Image-only extraction has no embedded scale. Pass --pixels-per-meter "
        "or --scale-bar-meters for metric coordinates."
    )
    return 1.0, warnings


def contour_bbox_world(
    geometry: MapGeometry,
    crop_origin: tuple[int, int],
    left: int,
    top: int,
    width: int,
    height: int,
) -> tuple[list[list[float]], dict[str, float], dict[str, float]]:
    ox, oy = crop_origin
    x0, y0 = geometry.pixel_to_world(ox + left, oy + top + height)
    x1, y1 = geometry.pixel_to_world(ox + left + width, oy + top)
    min_x, max_x = sorted((x0, x1))
    min_y, max_y = sorted((y0, y1))
    polygon = [
        [round(min_x, 4), round(min_y, 4)],
        [round(max_x, 4), round(min_y, 4)],
        [round(max_x, 4), round(max_y, 4)],
        [round(min_x, 4), round(max_y, 4)],
        [round(min_x, 4), round(min_y, 4)],
    ]
    dimensions = {
        "width": round(max_x - min_x, 4),
        "height": round(max_y - min_y, 4),
    }
    centroid = {
        "x": round((min_x + max_x) / 2, 4),
        "y": round((min_y + max_y) / 2, 4),
    }
    return polygon, dimensions, centroid


def bbox_from_polygon(polygon: list[list[float]]) -> tuple[float, float, float, float]:
    xs = [point[0] for point in polygon]
    ys = [point[1] for point in polygon]
    return min(xs), min(ys), max(xs), max(ys)


def point_in_bbox(
    point: tuple[float, float],
    bbox: tuple[float, float, float, float],
    pad: float = 0.0,
) -> bool:
    x, y = point
    min_x, min_y, max_x, max_y = bbox
    return min_x - pad <= x <= max_x + pad and min_y - pad <= y <= max_y + pad


def point_to_bbox_distance(
    point: tuple[float, float],
    bbox: tuple[float, float, float, float],
) -> float:
    x, y = point
    min_x, min_y, max_x, max_y = bbox
    dx = max(min_x - x, 0.0, x - max_x)
    dy = max(min_y - y, 0.0, y - max_y)
    return math.hypot(dx, dy)


def classify_room_type(crop_bgr: np.ndarray, component_mask: np.ndarray) -> str:
    pixels = crop_bgr[component_mask > 0]
    if pixels.size == 0:
        return "ROOM_GENERIC"
    median_bgr = np.median(pixels, axis=0)
    median_rgb = np.array([median_bgr[2], median_bgr[1], median_bgr[0]], dtype=float)

    best_type = "ROOM_GENERIC"
    best_distance = float("inf")
    for room_type, color in TYPE_COLORS.items():
        rgb = np.array(hex_to_rgb_tuple(color), dtype=float)
        distance = float(np.linalg.norm(median_rgb - rgb))
        if distance < best_distance:
            best_type = room_type
            best_distance = distance
    return best_type


def detect_rooms(
    crop_bgr: np.ndarray,
    geometry: MapGeometry,
    crop_origin: tuple[int, int],
    *,
    min_area_m2: float,
    color_tolerance: int,
) -> list[dict[str, Any]]:
    mask = room_fill_mask(crop_bgr, color_tolerance)
    min_area_px = max(20, int(min_area_m2 * geometry.px_per_m * geometry.px_per_m))
    components, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    rooms: list[dict[str, Any]] = []

    for label_idx in range(1, components):
        area_px = int(stats[label_idx, cv2.CC_STAT_AREA])
        if area_px < min_area_px:
            continue
        left = int(stats[label_idx, cv2.CC_STAT_LEFT])
        top = int(stats[label_idx, cv2.CC_STAT_TOP])
        width = int(stats[label_idx, cv2.CC_STAT_WIDTH])
        height = int(stats[label_idx, cv2.CC_STAT_HEIGHT])
        if width < 3 or height < 3:
            continue

        component_mask = (labels[top : top + height, left : left + width] == label_idx).astype(
            np.uint8
        )
        room_type = classify_room_type(
            crop_bgr[top : top + height, left : left + width],
            component_mask,
        )
        polygon, dimensions, centroid = contour_bbox_world(
            geometry,
            crop_origin,
            left,
            top,
            width,
            height,
        )
        area_m2 = dimensions["width"] * dimensions["height"]
        if area_m2 < min_area_m2:
            continue
        rooms.append(
            {
                "_bbox": bbox_from_polygon(polygon),
                "_bbox_px": [left, top, width, height],
                "_area_px": area_px,
                "_label_point": None,
                "area_m2": round(area_m2, 4),
                "centroid_m": centroid,
                "dimensions_m": dimensions,
                "door_count": 0,
                "doors": [],
                "polygon_m": polygon,
                "type": room_type,
            }
        )

    return sorted(rooms, key=lambda r: (r["centroid_m"]["y"], r["centroid_m"]["x"]))


def detect_doors(
    crop_bgr: np.ndarray,
    geometry: MapGeometry,
    crop_origin: tuple[int, int],
    *,
    min_length_m: float,
    max_length_m: float,
) -> list[dict[str, Any]]:
    mask = door_mask(crop_bgr)
    components, _, stats, centroids = cv2.connectedComponentsWithStats(mask)
    min_len_px = min_length_m * geometry.px_per_m
    max_len_px = max_length_m * geometry.px_per_m
    doors: list[dict[str, Any]] = []

    for label_idx in range(1, components):
        area_px = int(stats[label_idx, cv2.CC_STAT_AREA])
        left = int(stats[label_idx, cv2.CC_STAT_LEFT])
        top = int(stats[label_idx, cv2.CC_STAT_TOP])
        width = int(stats[label_idx, cv2.CC_STAT_WIDTH])
        height = int(stats[label_idx, cv2.CC_STAT_HEIGHT])
        major = max(width, height)
        minor = min(width, height)
        if area_px < 12 or major < min_len_px or major > max_len_px or minor < 2:
            continue
        orientation = "horizontal" if width >= height else "vertical"
        cx_px = crop_origin[0] + float(centroids[label_idx][0])
        cy_px = crop_origin[1] + float(centroids[label_idx][1])
        cx_m, cy_m = geometry.pixel_to_world(cx_px, cy_px)
        doors.append(
            {
                "_bbox_px": [left, top, width, height],
                "_label_point": None,
                "_length_px": major,
                "_area_px": area_px,
                "centroid_m": {"x": round(cx_m, 4), "y": round(cy_m, 4)},
                "orientation": orientation,
                "size_m": round(major / geometry.px_per_m, 4),
                "type": "DOOR",
            }
        )

    return sorted(doors, key=lambda d: (d["centroid_m"]["y"], d["centroid_m"]["x"]))


def filter_image_footer_artifacts(
    rooms: list[dict[str, Any]],
    doors: list[dict[str, Any]],
    image_height_px: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    footer_top = image_height_px * 0.82

    filtered_rooms: list[dict[str, Any]] = []
    removed_rooms = 0
    for room in rooms:
        left, top, width, height = room.get("_bbox_px", [0, 0, 0, 0])
        if top >= footer_top and width < 90 and height < 90:
            removed_rooms += 1
            continue
        filtered_rooms.append(room)

    filtered_doors: list[dict[str, Any]] = []
    removed_doors = 0
    for door in doors:
        left, top, width, height = door.get("_bbox_px", [0, 0, 0, 0])
        if top >= footer_top and width < 120 and height < 40:
            removed_doors += 1
            continue
        filtered_doors.append(door)

    if removed_rooms:
        warnings.append(f"Ignored {removed_rooms} footer/legend room-color artifacts")
    if removed_doors:
        warnings.append(f"Ignored {removed_doors} footer/legend door-color artifacts")
    return filtered_rooms, filtered_doors, warnings


def frame_contains_word(word: PdfWord, geometry: MapGeometry, pad_pt: float = 1.0) -> bool:
    frame_x, frame_y, frame_w, frame_h = geometry.frame
    frame_top = geometry.page_height_pt - frame_y - frame_h
    frame_bottom = geometry.page_height_pt - frame_y
    x, y = word.center_top
    return (
        frame_x - pad_pt <= x <= frame_x + frame_w + pad_pt
        and frame_top - pad_pt <= y <= frame_bottom + pad_pt
    )


def label_words(words: list[PdfWord], geometry: MapGeometry) -> tuple[list[PdfWord], list[PdfWord]]:
    room_words: list[PdfWord] = []
    door_words: list[PdfWord] = []
    for word in words:
        if not frame_contains_word(word, geometry):
            continue
        if ROOM_ID_RE.fullmatch(word.text):
            room_words.append(word)
        elif DOOR_ID_RE.fullmatch(word.text):
            door_words.append(word)
    return room_words, door_words


def read_ocr_words(
    image_path: Path,
    *,
    tesseract_command: str,
    language: str,
    min_confidence: float,
    scale: float,
) -> tuple[list[PdfWord], list[str]]:
    warnings: list[str] = []
    if shutil.which(tesseract_command) is None:
        return [], [f"OCR skipped: {tesseract_command!r} was not found on PATH"]

    ocr_image_path = image_path
    with tempfile.TemporaryDirectory(prefix="aau-map-ocr-") as tmpdir:
        if scale and scale != 1.0:
            image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
            if image is None:
                return [], [f"OCR skipped: could not read {image_path}"]
            resized = cv2.resize(
                image,
                None,
                fx=scale,
                fy=scale,
                interpolation=cv2.INTER_CUBIC,
            )
            ocr_image_path = Path(tmpdir) / "ocr.png"
            cv2.imwrite(str(ocr_image_path), resized)

        command = [
            tesseract_command,
            str(ocr_image_path),
            "stdout",
            "--psm",
            "6",
            "-l",
            language,
            "tsv",
        ]
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
        )

    if completed.returncode != 0:
        return [], [f"OCR failed: {completed.stderr.strip() or completed.returncode}"]

    words = parse_tesseract_tsv(
        completed.stdout,
        scale=scale,
        min_confidence=min_confidence,
    )

    if not words:
        warnings.append("OCR produced no usable words")
    return words, warnings


def parse_tesseract_tsv(
    output: str,
    *,
    scale: float,
    min_confidence: float,
    x_offset: float = 0.0,
    y_offset: float = 0.0,
) -> list[PdfWord]:
    words: list[PdfWord] = []
    reader = csv.DictReader(io.StringIO(output), delimiter="\t")
    for row in reader:
        text = clean_ocr_text(row.get("text") or "")
        if not text:
            continue
        try:
            confidence = float(row.get("conf") or -1)
            left = float(row.get("left") or 0)
            top = float(row.get("top") or 0)
            width = float(row.get("width") or 0)
            height = float(row.get("height") or 0)
        except ValueError:
            continue
        if confidence < min_confidence or width <= 0 or height <= 0:
            continue
        words.append(
            PdfWord(
                text=text,
                x0=left / scale + x_offset,
                y0_top=top / scale + y_offset,
                x1=(left + width) / scale + x_offset,
                y1_top=(top + height) / scale + y_offset,
            )
        )
    return words


def prepare_ocr_crop(image_bgr: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return binary


def read_ocr_words_from_image(
    image_bgr: np.ndarray,
    *,
    tesseract_command: str,
    language: str,
    min_confidence: float,
    scale: float,
    psm: int,
    preprocess: bool,
    whitelist: str | None = None,
) -> tuple[list[PdfWord], list[str]]:
    if shutil.which(tesseract_command) is None:
        return [], [f"OCR skipped: {tesseract_command!r} was not found on PATH"]
    if image_bgr.size == 0:
        return [], ["OCR skipped: empty crop"]

    warnings: list[str] = []
    border_px = 8
    source = prepare_ocr_crop(image_bgr) if preprocess else image_bgr
    source = cv2.copyMakeBorder(
        source,
        border_px,
        border_px,
        border_px,
        border_px,
        cv2.BORDER_CONSTANT,
        value=255,
    )

    with tempfile.TemporaryDirectory(prefix="aau-map-crop-ocr-") as tmpdir:
        if scale and scale != 1.0:
            source = cv2.resize(
                source,
                None,
                fx=scale,
                fy=scale,
                interpolation=cv2.INTER_CUBIC,
            )
        ocr_image_path = Path(tmpdir) / "crop.png"
        cv2.imwrite(str(ocr_image_path), source)
        command = [
            tesseract_command,
            str(ocr_image_path),
            "stdout",
            "--psm",
            str(psm),
            "-l",
            language,
        ]
        if whitelist:
            command.extend(["-c", f"tessedit_char_whitelist={whitelist}"])
        command.append("tsv")
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
        )

    if completed.returncode != 0:
        message = completed.stderr.strip() or str(completed.returncode)
        return [], [f"OCR failed for crop: {message}"]

    words = parse_tesseract_tsv(
        completed.stdout,
        scale=scale,
        min_confidence=min_confidence,
        x_offset=-border_px,
        y_offset=-border_px,
    )
    if not words:
        warnings.append("OCR produced no usable words for crop")
    return words, warnings


def image_crop_with_origin(
    image_bgr: np.ndarray,
    bbox_px: list[int],
    *,
    pad_px: int,
) -> tuple[np.ndarray, tuple[int, int]]:
    left, top, width, height = bbox_px
    x0 = max(0, left - pad_px)
    y0 = max(0, top - pad_px)
    x1 = min(image_bgr.shape[1], left + width + pad_px)
    y1 = min(image_bgr.shape[0], top + height + pad_px)
    return image_bgr[y0:y1, x0:x1], (x0, y0)


def offset_words(words: list[PdfWord], origin: tuple[int, int]) -> list[PdfWord]:
    ox, oy = origin
    return [
        PdfWord(
            text=word.text,
            x0=word.x0 + ox,
            y0_top=word.y0_top + oy,
            x1=word.x1 + ox,
            y1_top=word.y1_top + oy,
        )
        for word in words
    ]


def normalize_ocr_token(text: str) -> str:
    return text.strip().replace(" ", "").upper()


def image_door_label_words(words: list[PdfWord]) -> list[PdfWord]:
    labels: list[PdfWord] = []
    for word in words:
        normalized = normalize_door_label_candidate(word.text)
        if not DOOR_ID_RE.fullmatch(normalized):
            continue
        labels.append(
            PdfWord(
                text=normalized,
                x0=word.x0,
                y0_top=word.y0_top,
                x1=word.x1,
                y1_top=word.y1_top,
            )
        )
    return labels


def group_words_by_line(words: list[PdfWord]) -> list[list[PdfWord]]:
    if not words:
        return []

    ordered = sorted(words, key=lambda word: (word.center_top[1], word.center_top[0]))
    line_groups: list[list[PdfWord]] = []
    for word in ordered:
        word_height = max(1.0, word.y1_top - word.y0_top)
        if not line_groups:
            line_groups.append([word])
            continue
        last_group = line_groups[-1]
        last_center_y = sum(item.center_top[1] for item in last_group) / len(last_group)
        if abs(word.center_top[1] - last_center_y) <= max(4.0, word_height * 0.65):
            last_group.append(word)
        else:
            line_groups.append([word])
    return line_groups


def group_words_into_lines(words: list[PdfWord]) -> list[str]:
    line_groups = group_words_by_line(words)
    lines: list[str] = []
    for group in line_groups:
        line = " ".join(word.text for word in sorted(group, key=lambda item: item.x0))
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            lines.append(line)
    return lines


def line_looks_like_room_metadata(line: str) -> bool:
    compact = line.strip()
    upper = compact.upper()
    return (
        bool(DIMENSION_LINE_RE.search(compact))
        or bool(BROKEN_DIMENSION_LINE_RE.search(compact))
        or bool(DOOR_COUNT_LINE_RE.search(compact))
        or bool(WALL_DISTANCE_LINE_RE.search(compact))
        or bool(DOOR_ID_RE.fullmatch(upper.replace(" ", "")))
        or "DOOR" in upper
        or "DØR" in upper
        or "DØRE" in upper
        or "VÆG" in upper
        or "NÆRMESTE" in upper
        or "D00R" in upper
        or "D0OR" in upper
        or upper in {"M", "DOOR", "DOORS", "DØR", "DØRE", "WALL", "VÆG", "NÆRMESTE", "H", "B"}
    )


def remove_room_metadata_from_line(line: str) -> str:
    line = DIMENSION_LINE_RE.sub(" ", line)
    line = BROKEN_DIMENSION_LINE_RE.sub(" ", line)
    line = DOOR_COUNT_LINE_RE.sub(" ", line)
    line = WALL_DISTANCE_LINE_RE.sub(" ", line)
    line = re.sub(r"\b\d+\s+D[O0]{2}RS?\b", " ", line, flags=re.IGNORECASE)
    line = re.sub(r"\b\d+\s+D(?:Ø|OE|O)RE?\b", " ", line, flags=re.IGNORECASE)
    line = re.sub(r"\b(?:NÆRMESTE\s+VÆG|NAERMESTE\s+VAEG|WALL|VÆG|VAEG)\s+-?\b", " ", line, flags=re.IGNORECASE)
    line = re.sub(r"\s+", " ", line).strip(" -")
    return line


def normalize_room_name_candidate(line: str) -> str:
    line = clean_ocr_text(line)
    line = re.sub(r"[^0-9A-Za-zÆØÅæøå ._-]+", " ", line)
    line = re.sub(r"\s+", " ", line).strip(" -")
    line = re.sub(r"\s+\d{2,4}\s*M$", "", line, flags=re.IGNORECASE)
    match = ROOM_CODE_RE.search(line)
    if match:
        prefix_words = re.findall(r"[0-9A-Za-zÆØÅæøå]+", line[: match.start()])
        if not prefix_words or all(len(word) <= 1 for word in prefix_words):
            line = line[match.start() :].strip(" -")
    tokens = [
        token.strip(".,;:")
        for token in line.split()
        if re.search(r"[0-9A-Za-zÆØÅæøå]", token)
    ]
    while len(tokens) > 2 and tokens[-1].upper() in ROOM_TRAILING_NOISE_TOKENS:
        tokens.pop()
    return " ".join(tokens)


def room_name_from_ocr_lines(lines: list[str]) -> str:
    name_lines: list[str] = []
    for raw_line in lines:
        line = clean_ocr_text(raw_line)
        if not line:
            continue
        candidate = remove_room_metadata_from_line(line)
        if not candidate and line_looks_like_room_metadata(line):
            if name_lines:
                break
            continue
        if line_looks_like_room_metadata(line) and not candidate:
            continue
        candidate = candidate or line
        candidate = normalize_room_name_candidate(candidate)
        compact = re.sub(r"[^A-Za-z0-9]", "", candidate)
        if len(compact) < 2:
            continue
        name_lines.append(candidate)
        if len(name_lines) >= 3:
            break
    return normalize_room_name_candidate(" ".join(name_lines))


def normalize_door_label_candidate(text: str) -> str:
    compact = re.sub(r"[^A-Z0-9|]", "", text.upper())
    for start in range(0, max(0, len(compact) - 4)):
        if compact[start] != "D":
            continue
        digits = compact[start + 1 : start + 4].translate(OCR_DIGIT_TRANSLATION)
        suffix = compact[start + 4]
        if digits.isdigit() and suffix in {"U", "L", "R", "D"}:
            return f"D{digits}{suffix}"
    return compact


def door_label_from_ocr_words(words: list[PdfWord]) -> PdfWord | None:
    for group in group_words_by_line(words):
        ordered = sorted(group, key=lambda item: item.x0)
        line = " ".join(word.text for word in ordered)
        label = normalize_door_label_candidate(line)
        if not DOOR_ID_RE.fullmatch(label):
            continue
        return PdfWord(
            text=label,
            x0=min(word.x0 for word in ordered),
            y0_top=min(word.y0_top for word in ordered),
            x1=max(word.x1 for word in ordered),
            y1_top=max(word.y1_top for word in ordered),
        )
    for word in words:
        label = normalize_door_label_candidate(word.text)
        if DOOR_ID_RE.fullmatch(label):
            return PdfWord(
                text=label,
                x0=word.x0,
                y0_top=word.y0_top,
                x1=word.x1,
                y1_top=word.y1_top,
            )
    return None


def assign_image_room_names_from_crops(
    image_bgr: np.ndarray,
    rooms: list[dict[str, Any]],
    geometry: ImageGeometry,
    *,
    tesseract_command: str,
    language: str,
    min_confidence: float,
    scale: float,
) -> tuple[int, list[str]]:
    warnings: list[str] = []
    named = 0
    missing_crop_warnings = 0

    for room in rooms:
        bbox_px = room.get("_bbox_px")
        if not bbox_px:
            continue
        _, _, width, height = bbox_px
        pad_px = max(3, int(min(width, height) * 0.04))
        crop, origin = image_crop_with_origin(image_bgr, bbox_px, pad_px=pad_px)
        words, ocr_warnings = read_ocr_words_from_image(
            crop,
            tesseract_command=tesseract_command,
            language=language,
            min_confidence=min_confidence,
            scale=scale,
            psm=6,
            preprocess=True,
        )
        if ocr_warnings and not words:
            missing_crop_warnings += 1
        lines = group_words_into_lines(words)
        name = room_name_from_ocr_lines(lines)
        if not name:
            continue
        room["name"] = name
        room["_ocr_lines"] = lines
        room["_label_point"] = geometry.top_text_to_world(
            bbox_px[0] + bbox_px[2] / 2,
            bbox_px[1] + bbox_px[3] / 2,
        )
        named += 1

    if missing_crop_warnings:
        warnings.append(
            f"Room crop OCR produced no usable words for {missing_crop_warnings} rooms"
        )
    return named, warnings


def assign_image_door_labels_from_crops(
    image_bgr: np.ndarray,
    doors: list[dict[str, Any]],
    geometry: ImageGeometry,
    *,
    tesseract_command: str,
    language: str,
    min_confidence: float,
    scale: float,
    radius_m: float,
) -> tuple[int, list[str]]:
    warnings: list[str] = []
    labels_seen = 0
    missing_crop_warnings = 0
    used_labels: set[str] = set()
    radius_px = max(80, int(radius_m * geometry.px_per_m))

    for door in doors:
        bbox_px = door.get("_bbox_px")
        if not bbox_px:
            continue
        crop, origin = image_crop_with_origin(image_bgr, bbox_px, pad_px=radius_px)
        words, ocr_warnings = read_ocr_words_from_image(
            crop,
            tesseract_command=tesseract_command,
            language=language,
            min_confidence=min_confidence,
            scale=scale,
            psm=11,
            preprocess=True,
            whitelist="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.|",
        )
        if ocr_warnings and not words:
            missing_crop_warnings += 1
        label = door_label_from_ocr_words(offset_words(words, origin))
        if label is None:
            continue
        if label.text in used_labels:
            continue
        used_labels.add(label.text)
        door["unique_id"] = label.text
        door["base_id"] = label.text[:-1]
        door["direction"] = label.text[-1]
        door["name"] = label.text
        door["source_id"] = f"cv:{label.text}"
        door["_label_point"] = geometry.top_text_to_world(*label.center_top)
        door["detection_method"] = "red_pixel_component_crop_ocr"
        labels_seen += 1

    if missing_crop_warnings:
        warnings.append(
            f"Door crop OCR produced no usable words for {missing_crop_warnings} doors"
        )
    return labels_seen, warnings


def assign_image_room_text(
    rooms: list[dict[str, Any]],
    words: list[PdfWord],
    geometry: ImageGeometry,
    *,
    id_mode: str,
) -> list[str]:
    warnings: list[str] = []
    used_names: set[str] = set()

    for room_idx, room in enumerate(rooms, start=1):
        name = str(room.get("name") or "").strip()
        if not name:
            room_words = [
                word
                for word in words
                if point_in_bbox(
                    geometry.top_text_to_world(*word.center_top),
                    room["_bbox"],
                    pad=0.05,
                )
            ]
            lines = group_words_into_lines(room_words)
            name = room_name_from_ocr_lines(lines)
            if name:
                room["name"] = name
                room["_ocr_lines"] = lines
                room["_label_point"] = geometry.top_text_to_world(*room_words[0].center_top)

        if not name:
            generated = f"CVR{room_idx:03d}"
            name = generated
            warnings.append(f"Generated {generated} for room without OCR name")

        if id_mode == "sequence":
            unique_id = f"CVR{room_idx:03d}"
        else:
            unique_id = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower() or name
            base_id = unique_id[:48] or f"cvr-{room_idx:03d}"
            unique_id = base_id
            suffix = 2
            while unique_id in used_names:
                unique_id = f"{base_id}-{suffix}"
                suffix += 1
        used_names.add(unique_id)
        room["unique_id"] = unique_id
        room["name"] = name
        room["source_id"] = f"image:{unique_id}"

    return warnings


def assign_room_labels(
    rooms: list[dict[str, Any]],
    labels: list[PdfWord],
    geometry: MapGeometry,
) -> list[str]:
    warnings: list[str] = []
    used_rooms: set[int] = set()

    for word in labels:
        point = geometry.top_text_to_world(*word.center_top)
        candidates = [
            (idx, room)
            for idx, room in enumerate(rooms)
            if idx not in used_rooms and point_in_bbox(point, room["_bbox"], pad=0.08)
        ]
        if not candidates:
            candidates = [
                (idx, room)
                for idx, room in enumerate(rooms)
                if idx not in used_rooms
                and point_to_bbox_distance(point, room["_bbox"]) <= 0.8
            ]
        if not candidates:
            warnings.append(f"Room label {word.text} could not be matched to a detected polygon")
            continue

        idx, room = min(candidates, key=lambda item: item[1]["area_m2"])
        used_rooms.add(idx)
        room["unique_id"] = word.text
        room["name"] = word.text
        room["source_id"] = f"cv:{word.text}"
        room["_label_point"] = point

    next_idx = 1
    for room in rooms:
        if room.get("unique_id"):
            continue
        while f"CVR{next_idx:03d}" in {r.get("unique_id") for r in rooms}:
            next_idx += 1
        generated = f"CVR{next_idx:03d}"
        room["unique_id"] = generated
        room["name"] = generated
        room["source_id"] = f"cv:{generated}"
        warnings.append(f"Generated {generated} for unlabeled room polygon")
        next_idx += 1

    return warnings


def assign_pdf_room_names_from_text(
    rooms: list[dict[str, Any]],
    words: list[PdfWord],
    geometry: MapGeometry,
) -> int:
    def useful_room_word(word: PdfWord) -> bool:
        text = clean_ocr_text(word.text)
        upper = text.upper()
        if not text:
            return False
        if DOOR_ID_RE.fullmatch(normalize_door_label_candidate(text)):
            return False
        if upper in {"WALL", "VÆG", "NÆRMESTE", "M", "DOOR", "DOORS", "DØR", "DØRE", "H", "B", "X"}:
            return False
        if re.fullmatch(r"\d+(?:[.,]\d+)?", text):
            return False
        return True

    named = 0
    frame_words = [
        word
        for word in words
        if frame_contains_word(word, geometry) and useful_room_word(word)
    ]
    for room in rooms:
        room_words = [
            word
            for word in frame_words
            if point_in_bbox(
                geometry.top_text_to_world(*word.center_top),
                room["_bbox"],
                pad=0.05,
            )
        ]
        if not room_words:
            continue
        lines = group_words_into_lines(room_words)
        name = room_name_from_ocr_lines(lines)
        if not name:
            continue
        room["name"] = name
        room["_ocr_lines"] = lines
        room["_label_point"] = geometry.top_text_to_world(*room_words[0].center_top)
        named += 1
    return named


def orientation_matches_label(door: dict[str, Any], label: PdfWord) -> bool:
    suffix = label.text[-1]
    if suffix in {"U", "D"}:
        return door["orientation"] == "horizontal"
    if suffix in {"L", "R"}:
        return door["orientation"] == "vertical"
    return True


def assign_door_labels(
    doors: list[dict[str, Any]],
    labels: list[PdfWord],
    geometry: MapGeometry,
    *,
    max_distance_m: float,
) -> list[str]:
    warnings: list[str] = []
    label_points = {idx: geometry.top_text_to_world(*label.center_top) for idx, label in enumerate(labels)}

    pairs: list[tuple[float, int, int]] = []
    for door_idx, door in enumerate(doors):
        door_point = (door["centroid_m"]["x"], door["centroid_m"]["y"])
        for label_idx, label in enumerate(labels):
            if not orientation_matches_label(door, label):
                continue
            label_point = label_points[label_idx]
            distance = math.hypot(door_point[0] - label_point[0], door_point[1] - label_point[1])
            if distance <= max_distance_m:
                pairs.append((distance, door_idx, label_idx))

    used_doors: set[int] = {
        idx for idx, door in enumerate(doors) if door.get("unique_id")
    }
    used_labels: set[int] = set()
    for _, door_idx, label_idx in sorted(pairs):
        if door_idx in used_doors or label_idx in used_labels:
            continue
        label = labels[label_idx]
        door = doors[door_idx]
        door["unique_id"] = label.text
        door["base_id"] = label.text[:-1]
        door["direction"] = label.text[-1]
        door["name"] = label.text
        door["source_id"] = f"cv:{label.text}"
        door["_label_point"] = label_points[label_idx]
        door["detection_method"] = "red_pixel_component"
        used_doors.add(door_idx)
        used_labels.add(label_idx)

    next_idx = 1
    for door in doors:
        if door.get("unique_id"):
            continue
        suffix = "U" if door["orientation"] == "horizontal" else "L"
        generated = f"CVD{next_idx:03d}{suffix}"
        door["unique_id"] = generated
        door["base_id"] = generated[:-1]
        door["direction"] = suffix
        door["name"] = generated
        door["source_id"] = f"cv:{generated}"
        door["detection_method"] = "red_pixel_component_unlabeled"
        warnings.append(f"Generated {generated} for unlabeled red door mark")
        next_idx += 1

    unused_labels = [label.text for idx, label in enumerate(labels) if idx not in used_labels]
    for label_idx, label in enumerate(labels):
        if label_idx in used_labels:
            continue
        label_point = label_points[label_idx]
        orientation = "horizontal" if label.text[-1] in {"U", "D"} else "vertical"
        doors.append(
            {
                "_label_point": label_point,
                "_red_mark_detected": False,
                "base_id": label.text[:-1],
                "centroid_m": {
                    "x": round(label_point[0], 4),
                    "y": round(label_point[1], 4),
                },
                "detection_method": "label_nearest_wall_fallback",
                "direction": label.text[-1],
                "name": label.text,
                "orientation": orientation,
                "size_m": DOOR_LENGTH_M,
                "source_id": f"cv:{label.text}:label-nearest-wall",
                "type": "DOOR",
                "unique_id": label.text,
            }
        )
    if unused_labels:
        warnings.append(
            "Door labels inferred from nearest wall because no separate red mark "
            f"was detected: {', '.join(unused_labels[:12])}"
        )

    return warnings


def room_distance_for_door(
    door: dict[str, Any],
    room: dict[str, Any],
    tolerance_m: float,
) -> tuple[str, float] | None:
    x = door["centroid_m"]["x"]
    y = door["centroid_m"]["y"]
    min_x, min_y, max_x, max_y = room["_bbox"]
    cx = room["centroid_m"]["x"]
    cy = room["centroid_m"]["y"]

    if door["orientation"] == "horizontal":
        if x < min_x - tolerance_m or x > max_x + tolerance_m:
            return None
        if y < min_y:
            return "U", min_y - y
        if y > max_y:
            return "D", y - max_y
        return ("U" if cy >= y else "D"), 0.0

    if y < min_y - tolerance_m or y > max_y + tolerance_m:
        return None
    if x < min_x:
        return "R", min_x - x
    if x > max_x:
        return "L", x - max_x
    return ("R" if cx >= x else "L"), 0.0


def nearest_rooms_for_door(
    door: dict[str, Any],
    rooms: list[dict[str, Any]],
    *,
    tolerance_m: float,
) -> list[dict[str, Any]]:
    by_side: dict[str, tuple[float, dict[str, Any]]] = {}
    for room in rooms:
        side_distance = room_distance_for_door(door, room, tolerance_m)
        if side_distance is None:
            continue
        side, distance = side_distance
        if distance <= tolerance_m and (
            side not in by_side or distance < by_side[side][0]
        ):
            by_side[side] = (distance, room)

    selected = [item[1] for item in sorted(by_side.values(), key=lambda item: item[0])]
    unique: list[dict[str, Any]] = []
    for room in selected:
        if room not in unique:
            unique.append(room)
        if len(unique) == 2:
            return unique

    door_point = (door["centroid_m"]["x"], door["centroid_m"]["y"])
    fallback = sorted(
        rooms,
        key=lambda room: point_to_bbox_distance(door_point, room["_bbox"]),
    )
    for room in fallback:
        if room not in unique:
            unique.append(room)
        if len(unique) == 2:
            break
    return unique


def room_for_label_point(
    point: tuple[float, float] | None,
    rooms: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if point is None:
        return None
    containing = [room for room in rooms if point_in_bbox(point, room["_bbox"], pad=0.08)]
    if containing:
        return min(containing, key=lambda room: room["area_m2"])
    nearby = [
        room
        for room in rooms
        if point_to_bbox_distance(point, room["_bbox"]) <= 0.8
    ]
    if nearby:
        return min(nearby, key=lambda room: point_to_bbox_distance(point, room["_bbox"]))
    return None


def clamp(value: float, lower: float, upper: float) -> float:
    return min(max(value, lower), upper)


def snap_label_fallback_door_to_wall(
    door: dict[str, Any],
    rooms: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if door.get("detection_method") != "label_nearest_wall_fallback":
        return None

    reference = room_for_label_point(door.get("_label_point"), rooms)
    if reference is None:
        return None

    min_x, min_y, max_x, max_y = reference["_bbox"]
    label_x, label_y = door.get("_label_point") or (
        door["centroid_m"]["x"],
        door["centroid_m"]["y"],
    )
    direction = door.get("direction")

    if direction == "U":
        x, y = clamp(label_x, min_x, max_x), max_y
        door["orientation"] = "horizontal"
    elif direction == "D":
        x, y = clamp(label_x, min_x, max_x), min_y
        door["orientation"] = "horizontal"
    elif direction == "L":
        x, y = min_x, clamp(label_y, min_y, max_y)
        door["orientation"] = "vertical"
    elif direction == "R":
        x, y = max_x, clamp(label_y, min_y, max_y)
        door["orientation"] = "vertical"
    else:
        distances = [
            (abs(label_y - max_y), clamp(label_x, min_x, max_x), max_y, "horizontal"),
            (abs(label_y - min_y), clamp(label_x, min_x, max_x), min_y, "horizontal"),
            (abs(label_x - min_x), min_x, clamp(label_y, min_y, max_y), "vertical"),
            (abs(label_x - max_x), max_x, clamp(label_y, min_y, max_y), "vertical"),
        ]
        _, x, y, orientation = min(distances)
        door["orientation"] = orientation

    door["centroid_m"] = {"x": round(x, 4), "y": round(y, 4)}
    return reference


def connect_doors_to_rooms(
    doors: list[dict[str, Any]],
    rooms: list[dict[str, Any]],
    *,
    tolerance_m: float,
) -> list[str]:
    warnings: list[str] = []
    rooms_by_id = {room["unique_id"]: room for room in rooms}

    for door in doors:
        snapped_reference = snap_label_fallback_door_to_wall(door, rooms)
        connected = nearest_rooms_for_door(door, rooms, tolerance_m=tolerance_m)
        reference = room_for_label_point(door.get("_label_point"), rooms)
        if snapped_reference is not None:
            reference = snapped_reference
        if reference and reference not in connected:
            connected = [reference, *connected[:1]]
        if len(connected) < 2:
            warnings.append(f"{door['unique_id']} connected to fewer than two rooms")

        connected = connected[:2]
        door["connected_rooms"] = [
            {
                "unique_id": room["unique_id"],
                "source_id": room["source_id"],
                "name": room["name"],
            }
            for room in connected
        ]
        if not reference and connected:
            reference = connected[0]
        if reference:
            door["reference_room"] = {
                "unique_id": reference["unique_id"],
                "source_id": reference["source_id"],
                "name": reference["name"],
            }
            ref_bbox = reference["_bbox"]
            if door["orientation"] == "horizontal":
                wall_length = ref_bbox[2] - ref_bbox[0]
            else:
                wall_length = ref_bbox[3] - ref_bbox[1]
            door["wall_length_m"] = round(wall_length, 4)
        else:
            door["reference_room"] = {}
            door["wall_length_m"] = None

        for room_ref in door["connected_rooms"]:
            room = rooms_by_id.get(room_ref["unique_id"])
            if not room:
                continue
            if door["unique_id"] not in room["doors"]:
                room["doors"].append(door["unique_id"])
                room["door_count"] = len(room["doors"])

    return warnings


def clean_internal_fields(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for item in items:
        cleaned.append({key: value for key, value in item.items() if not key.startswith("_")})
    return cleaned


@dataclass(frozen=True)
class PdfCvOptions:
    dpi: int = 180
    min_room_area_m2: float = 0.18
    room_color_tolerance: int = 10
    min_door_length_m: float = 0.25
    max_door_length_m: float = 1.15
    max_door_label_distance_m: float = 4.5
    door_room_tolerance_m: float = 0.75


@dataclass(frozen=True)
class ImageCvOptions:
    pixels_per_meter: float | None = None
    scale_bar_meters: float | None = None
    min_room_area_m2: float = 0.18
    room_color_tolerance: int = 10
    min_door_length_m: float = 0.25
    max_door_length_m: float = 1.15
    max_door_label_distance_m: float = 4.5
    door_room_tolerance_m: float = 0.75
    run_ocr: bool = True
    tesseract_command: str = "tesseract"
    ocr_language: str = "eng+dan"
    ocr_min_confidence: float = 5
    ocr_scale: float = 2
    ocr_crop_scale: float = 7
    door_ocr_radius_m: float = 2.75
    image_room_id_mode: str = "sequence"
    run_door_crop_ocr: bool = False


@dataclass(frozen=True)
class FloorDetailsExtractor:
    def from_pdf(self, pdf_path: Path, options: PdfCvOptions | None = None) -> dict[str, Any]:
        options = options or PdfCvOptions()
        return PdfCvExtraction(pdf_path, options).build()

    def from_image(
        self,
        image_path: Path,
        *,
        building_name: str,
        floor_name: str,
        floor_index: int,
        options: ImageCvOptions | None = None,
    ) -> dict[str, Any]:
        options = options or ImageCvOptions()
        return ImageCvExtraction(
            image_path,
            building_name=building_name,
            floor_name=floor_name,
            floor_index=floor_index,
            options=options,
        ).build()


@dataclass(frozen=True)
class PdfCvExtraction:
    pdf_path: Path
    options: PdfCvOptions

    def build(self) -> dict[str, Any]:
        pdf_text = read_pdf_text(self.pdf_path)
        metadata = extract_metadata(pdf_text)
        scale = extract_scale_block(pdf_text)
        embedded = self._embedded_details(scale)
        if embedded:
            return embedded
        text_details = self._embedded_text_details(pdf_text, metadata, scale)
        if text_details:
            return text_details

        page_width, page_height, words = read_pdf_words(self.pdf_path)
        geometry = self._geometry(scale, page_width, page_height)
        crop, origin = self._render_crop(geometry)
        rooms, doors, red_marks_detected = self._detect_features(crop, geometry, origin)
        room_labels, door_labels = label_words(words, geometry)
        warnings, pdf_room_names_seen = self._assign_text_and_doors(
            rooms=rooms,
            doors=doors,
            words=words,
            geometry=geometry,
            room_labels=room_labels,
            door_labels=door_labels,
            red_marks_detected=red_marks_detected,
            expected_doors=metadata.get("doors_count"),
        )
        return self._details(
            metadata=metadata,
            geometry=geometry,
            page_width=page_width,
            page_height=page_height,
            room_labels=room_labels,
            door_labels=door_labels,
            rooms=rooms,
            doors=doors,
            red_marks_detected=red_marks_detected,
            pdf_room_names_seen=pdf_room_names_seen,
            warnings=warnings,
        )

    def _embedded_details(self, scale: dict[str, Any]) -> dict[str, Any] | None:
        embedded_details = floor_plan_details_reader.read_embedded_details_json(self.pdf_path)
        if not embedded_details:
            return None
        embedded_details["cv_diagnostics"] = {
            "source": "pdf_embedded_details",
            "source_pdf": str(self.pdf_path),
            "scale_pdf_points_per_meter": scale.get("AAU_SCALE_PDF_POINTS_PER_METER"),
            "rooms_detected": len(embedded_details.get("rooms", [])),
            "doors_detected": len(embedded_details.get("doors", [])),
            "pdf_room_names_seen": len(embedded_details.get("rooms", [])),
            "door_labels_seen": len(embedded_details.get("doors", [])),
            "warnings": [],
        }
        return embedded_details

    def _embedded_text_details(
        self,
        pdf_text: str,
        metadata: dict[str, Any],
        scale: dict[str, Any],
    ) -> dict[str, Any] | None:
        if (
            floor_plan_details_reader.PDF_DETAILS_SCHEMA_V1_LINE not in pdf_text
            and floor_plan_details_reader.PDF_DETAILS_SCHEMA_V2_LINE not in pdf_text
        ):
            return None
        details = floor_plan_details_reader.read_lookup_from_pdf_text(pdf_text, metadata)
        details["cv_diagnostics"] = {
            "source": "pdf_embedded_details",
            "source_pdf": str(self.pdf_path),
            "scale_pdf_points_per_meter": scale.get("AAU_SCALE_PDF_POINTS_PER_METER"),
            "rooms_detected": len(details.get("rooms", [])),
            "doors_detected": len(details.get("doors", [])),
            "pdf_room_names_seen": len(details.get("rooms", [])),
            "door_labels_seen": len(details.get("doors", [])),
            "warnings": [],
        }
        return details

    def _geometry(
        self,
        scale: dict[str, Any],
        page_width: float,
        page_height: float,
    ) -> MapGeometry:
        return MapGeometry(
            page_width_pt=page_width,
            page_height_pt=page_height,
            dpi=self.options.dpi,
            scale_pt_per_m=float(scale["AAU_SCALE_PDF_POINTS_PER_METER"]),
            bounds={
                "min_x": float(scale["AAU_SCALE_BOUNDS_MIN_X_M"]),
                "min_y": float(scale["AAU_SCALE_BOUNDS_MIN_Y_M"]),
                "max_x": float(scale["AAU_SCALE_BOUNDS_MAX_X_M"]),
                "max_y": float(scale["AAU_SCALE_BOUNDS_MAX_Y_M"]),
            },
            frame=(
                float(scale["AAU_SCALE_FRAME_X_PT"]),
                float(scale["AAU_SCALE_FRAME_Y_PT"]),
                float(scale["AAU_SCALE_FRAME_WIDTH_PT"]),
                float(scale["AAU_SCALE_FRAME_HEIGHT_PT"]),
            ),
        )

    def _render_crop(self, geometry: MapGeometry) -> tuple[np.ndarray, tuple[int, int]]:
        with tempfile.TemporaryDirectory(prefix="aau-map-cv-") as tmpdir:
            image_path = render_pdf(self.pdf_path, self.options.dpi, Path(tmpdir) / "page")
            image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError(f"Could not read rendered PDF image for {self.pdf_path}")

        x0, y0, x1, y1 = geometry.crop_box_px()
        x0 = max(0, min(x0, image.shape[1]))
        x1 = max(0, min(x1, image.shape[1]))
        y0 = max(0, min(y0, image.shape[0]))
        y1 = max(0, min(y1, image.shape[0]))
        return image[y0:y1, x0:x1], (x0, y0)

    def _detect_features(
        self,
        crop: np.ndarray,
        geometry: MapGeometry,
        origin: tuple[int, int],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
        rooms = detect_rooms(
            crop,
            geometry,
            origin,
            min_area_m2=self.options.min_room_area_m2,
            color_tolerance=self.options.room_color_tolerance,
        )
        doors = detect_doors(
            crop,
            geometry,
            origin,
            min_length_m=self.options.min_door_length_m,
            max_length_m=self.options.max_door_length_m,
        )
        return rooms, doors, len(doors)

    def _assign_text_and_doors(
        self,
        *,
        rooms: list[dict[str, Any]],
        doors: list[dict[str, Any]],
        words: list[PdfWord],
        geometry: MapGeometry,
        room_labels: list[PdfWord],
        door_labels: list[PdfWord],
        red_marks_detected: int,
        expected_doors: Any,
    ) -> tuple[list[str], int]:
        warnings: list[str] = []
        warnings.extend(assign_room_labels(rooms, room_labels, geometry))
        pdf_room_names_seen = assign_pdf_room_names_from_text(rooms, words, geometry)
        warnings.extend(
            assign_door_labels(
                doors,
                door_labels,
                geometry,
                max_distance_m=self.options.max_door_label_distance_m,
            )
        )
        if expected_doors is not None and red_marks_detected >= int(expected_doors):
            fallback_count = sum(
                1 for door in doors if door.get("detection_method") == "label_nearest_wall_fallback"
            )
            if fallback_count:
                doors[:] = [
                    door
                    for door in doors
                    if door.get("detection_method") != "label_nearest_wall_fallback"
                ]
                warnings.append(f"Ignored {fallback_count} unmatched door label fallback")
        warnings.extend(
            connect_doors_to_rooms(
                doors,
                rooms,
                tolerance_m=self.options.door_room_tolerance_m,
            )
        )
        return warnings, pdf_room_names_seen

    def _details(
        self,
        *,
        metadata: dict[str, Any],
        geometry: MapGeometry,
        page_width: float,
        page_height: float,
        room_labels: list[PdfWord],
        door_labels: list[PdfWord],
        rooms: list[dict[str, Any]],
        doors: list[dict[str, Any]],
        red_marks_detected: int,
        pdf_room_names_seen: int,
        warnings: list[str],
    ) -> dict[str, Any]:
        building_name = str(metadata.get("building_name", ""))
        floor_name = str(metadata.get("floor_name", metadata.get("floor_index", "")))
        floor_index = int(metadata.get("floor_index", 0))
        for room in rooms:
            room["building"] = building_name
            room["floor"] = floor_name
            room["floor_index"] = floor_index
            room["doors"] = sorted(room["doors"])
        for door in doors:
            door["building"] = building_name
            door["floor"] = floor_name
            door["floor_index"] = floor_index

        expected_rooms = metadata.get("spaces_count")
        expected_doors = metadata.get("doors_count")
        if expected_rooms is not None and int(expected_rooms) != len(rooms):
            warnings.append(f"PDF metadata expects {expected_rooms} rooms, CV detected {len(rooms)}")
        if expected_doors is not None and int(expected_doors) != len(doors):
            warnings.append(f"PDF metadata expects {expected_doors} doors, CV detected {len(doors)}")

        return {
            "schema": "aau_map_details",
            "version": 1,
            "building": building_name,
            "floor": floor_name,
            "floor_index": floor_index,
            "rooms": clean_internal_fields(sorted(rooms, key=lambda room: room["unique_id"])),
            "doors": clean_internal_fields(sorted(doors, key=lambda door: door["unique_id"])),
            "cv_diagnostics": {
                "source": "pdf_image_cv",
                "source_pdf": str(self.pdf_path),
                "dpi": self.options.dpi,
                "page_size_pt": [page_width, page_height],
                "scale_pdf_points_per_meter": geometry.scale_pt_per_m,
                "pixels_per_meter": round(geometry.px_per_m, 4),
                "room_labels_seen": len(room_labels),
                "pdf_room_names_seen": pdf_room_names_seen,
                "door_labels_seen": len(door_labels),
                "red_marks_detected": red_marks_detected,
                "rooms_detected": len(rooms),
                "doors_detected": len(doors),
                "warnings": warnings,
            },
        }


def build_cv_details(
    pdf_path: Path,
    *,
    dpi: int,
    min_room_area_m2: float,
    room_color_tolerance: int,
    min_door_length_m: float,
    max_door_length_m: float,
    max_door_label_distance_m: float,
    door_room_tolerance_m: float,
) -> dict[str, Any]:
    return PdfCvExtraction(
        pdf_path,
        PdfCvOptions(
            dpi=dpi,
            min_room_area_m2=min_room_area_m2,
            room_color_tolerance=room_color_tolerance,
            min_door_length_m=min_door_length_m,
            max_door_length_m=max_door_length_m,
            max_door_label_distance_m=max_door_label_distance_m,
            door_room_tolerance_m=door_room_tolerance_m,
        ),
    ).build()


@dataclass(frozen=True)
class ImageCvExtraction:
    image_path: Path
    building_name: str
    floor_name: str
    floor_index: int
    options: ImageCvOptions

    def build(self) -> dict[str, Any]:
        image = self._read_image()
        resolved_pixels_per_meter, warnings = resolve_pixels_per_meter(
            image,
            pixels_per_meter=self.options.pixels_per_meter,
            scale_bar_meters=self.options.scale_bar_meters,
        )
        geometry = ImageGeometry(
            image_width_px=image.shape[1],
            image_height_px=image.shape[0],
            px_per_m=resolved_pixels_per_meter,
        )
        rooms, doors, red_marks_detected, artifact_warnings = self._detect_features(image, geometry)
        warnings.extend(artifact_warnings)
        ocr_words, room_crop_names, door_crop_labels_seen = self._read_and_assign_ocr(
            image,
            rooms,
            doors,
            geometry,
            warnings,
        )
        door_labels = image_door_label_words(ocr_words)
        warnings.extend(
            assign_door_labels(
                doors,
                door_labels,
                geometry,
                max_distance_m=self.options.max_door_label_distance_m,
            )
        )
        warnings.extend(
            connect_doors_to_rooms(
                doors,
                rooms,
                tolerance_m=self.options.door_room_tolerance_m,
            )
        )
        return self._details(
            rooms=rooms,
            doors=doors,
            resolved_pixels_per_meter=resolved_pixels_per_meter,
            ocr_words=ocr_words,
            room_crop_names=room_crop_names,
            door_crop_labels_seen=door_crop_labels_seen,
            door_labels=door_labels,
            red_marks_detected=red_marks_detected,
            warnings=warnings,
        )

    def _read_image(self) -> np.ndarray:
        image = cv2.imread(str(self.image_path), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError(f"Could not read image for {self.image_path}")
        return image

    def _detect_features(
        self,
        image: np.ndarray,
        geometry: ImageGeometry,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int, list[str]]:
        rooms = detect_rooms(
            image,
            geometry,
            (0, 0),
            min_area_m2=self.options.min_room_area_m2,
            color_tolerance=self.options.room_color_tolerance,
        )
        doors = detect_doors(
            image,
            geometry,
            (0, 0),
            min_length_m=self.options.min_door_length_m,
            max_length_m=self.options.max_door_length_m,
        )
        rooms, doors, artifact_warnings = filter_image_footer_artifacts(
            rooms,
            doors,
            image.shape[0],
        )
        return rooms, doors, len(doors), artifact_warnings

    def _read_and_assign_ocr(
        self,
        image: np.ndarray,
        rooms: list[dict[str, Any]],
        doors: list[dict[str, Any]],
        geometry: ImageGeometry,
        warnings: list[str],
    ) -> tuple[list[PdfWord], int, int]:
        ocr_words: list[PdfWord] = []
        room_crop_names = 0
        door_crop_labels_seen = 0
        if self.options.run_ocr:
            ocr_words, ocr_warnings = read_ocr_words(
                self.image_path,
                tesseract_command=self.options.tesseract_command,
                language=self.options.ocr_language,
                min_confidence=self.options.ocr_min_confidence,
                scale=self.options.ocr_scale,
            )
            warnings.extend(ocr_warnings)
            room_crop_names, room_crop_warnings = assign_image_room_names_from_crops(
                image,
                rooms,
                geometry,
                tesseract_command=self.options.tesseract_command,
                language=self.options.ocr_language,
                min_confidence=self.options.ocr_min_confidence,
                scale=self.options.ocr_crop_scale,
            )
            warnings.extend(room_crop_warnings)
            if self.options.run_door_crop_ocr:
                door_crop_labels_seen, door_crop_warnings = assign_image_door_labels_from_crops(
                    image,
                    doors,
                    geometry,
                    tesseract_command=self.options.tesseract_command,
                    language=self.options.ocr_language,
                    min_confidence=self.options.ocr_min_confidence,
                    scale=self.options.ocr_crop_scale,
                    radius_m=self.options.door_ocr_radius_m,
                )
                warnings.extend(door_crop_warnings)
            else:
                warnings.append("Door crop OCR disabled; door IDs will use generated labels")
        else:
            warnings.append("OCR disabled; room and door labels will be generated where needed")

        warnings.extend(
            assign_image_room_text(
                rooms,
                ocr_words,
                geometry,
                id_mode=self.options.image_room_id_mode,
            )
        )
        return ocr_words, room_crop_names, door_crop_labels_seen

    def _details(
        self,
        *,
        rooms: list[dict[str, Any]],
        doors: list[dict[str, Any]],
        resolved_pixels_per_meter: float,
        ocr_words: list[PdfWord],
        room_crop_names: int,
        door_crop_labels_seen: int,
        door_labels: list[PdfWord],
        red_marks_detected: int,
        warnings: list[str],
    ) -> dict[str, Any]:
        for room in rooms:
            room["building"] = self.building_name
            room["floor"] = self.floor_name
            room["floor_index"] = self.floor_index
            room["doors"] = sorted(room["doors"])
        for door in doors:
            door["building"] = self.building_name
            door["floor"] = self.floor_name
            door["floor_index"] = self.floor_index

        return {
            "schema": "aau_map_details",
            "version": 1,
            "building": self.building_name,
            "floor": self.floor_name,
            "floor_index": self.floor_index,
            "rooms": clean_internal_fields(sorted(rooms, key=lambda room: room["unique_id"])),
            "doors": clean_internal_fields(sorted(doors, key=lambda door: door["unique_id"])),
            "cv_diagnostics": {
                "source": "image_cv",
                "source_image": str(self.image_path),
                "pixels_per_meter": round(resolved_pixels_per_meter, 4),
                "ocr_words_seen": len(ocr_words),
                "room_crop_names_seen": room_crop_names,
                "door_crop_labels_seen": door_crop_labels_seen,
                "door_labels_seen": len(door_labels) + door_crop_labels_seen,
                "red_marks_detected": red_marks_detected,
                "rooms_detected": len(rooms),
                "doors_detected": len(doors),
                "warnings": warnings,
            },
        }


def build_image_cv_details(
    image_path: Path,
    *,
    building_name: str,
    floor_name: str,
    floor_index: int,
    pixels_per_meter: float | None,
    scale_bar_meters: float | None,
    min_room_area_m2: float,
    room_color_tolerance: int,
    min_door_length_m: float,
    max_door_length_m: float,
    max_door_label_distance_m: float,
    door_room_tolerance_m: float,
    run_ocr: bool,
    tesseract_command: str,
    ocr_language: str,
    ocr_min_confidence: float,
    ocr_scale: float,
    ocr_crop_scale: float,
    door_ocr_radius_m: float,
    image_room_id_mode: str,
    run_door_crop_ocr: bool,
) -> dict[str, Any]:
    return ImageCvExtraction(
        image_path=image_path,
        building_name=building_name,
        floor_name=floor_name,
        floor_index=floor_index,
        options=ImageCvOptions(
            pixels_per_meter=pixels_per_meter,
            scale_bar_meters=scale_bar_meters,
            min_room_area_m2=min_room_area_m2,
            room_color_tolerance=room_color_tolerance,
            min_door_length_m=min_door_length_m,
            max_door_length_m=max_door_length_m,
            max_door_label_distance_m=max_door_label_distance_m,
            door_room_tolerance_m=door_room_tolerance_m,
            run_ocr=run_ocr,
            tesseract_command=tesseract_command,
            ocr_language=ocr_language,
            ocr_min_confidence=ocr_min_confidence,
            ocr_scale=ocr_scale,
            ocr_crop_scale=ocr_crop_scale,
            door_ocr_radius_m=door_ocr_radius_m,
            image_room_id_mode=image_room_id_mode,
            run_door_crop_ocr=run_door_crop_ocr,
        ),
    ).build()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--pdf",
        type=Path,
        help="Read a generated map PDF instead of a rendered image.",
    )
    parser.add_argument(
        "--image",
        type=Path,
        help="Read a rendered PNG/JPEG directly instead of a generated PDF.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("outputs/floor_details.json"),
    )
    parser.add_argument("--building-name", default="Image-derived building")
    parser.add_argument("--floor-name", default="Image-derived floor")
    parser.add_argument("--floor-index", type=int, default=0)
    parser.add_argument("--pixels-per-meter", type=float)
    parser.add_argument(
        "--scale-bar-meters",
        type=float,
        help=(
            "Known metric length printed on the image scale bar. Used to infer "
            "pixels-per-meter when --pixels-per-meter is omitted."
        ),
    )
    parser.add_argument("--no-ocr", action="store_true")
    parser.add_argument("--tesseract-command", default="tesseract")
    parser.add_argument("--ocr-language", default="eng+dan")
    parser.add_argument("--ocr-min-confidence", type=float, default=35.0)
    parser.add_argument("--ocr-scale", type=float, default=2.0)
    parser.add_argument("--ocr-crop-scale", type=float, default=6.0)
    parser.add_argument("--door-ocr-radius-m", type=float, default=2.75)
    parser.add_argument("--no-door-crop-ocr", action="store_true")
    parser.add_argument(
        "--image-room-id-mode",
        choices=("sequence", "name-slug"),
        default="sequence",
        help=(
            "How image-derived room unique IDs are created. Display names still "
            "come from OCR when available."
        ),
    )
    parser.add_argument("--dpi", type=int, default=180)
    parser.add_argument("--min-room-area-m2", type=float, default=0.18)
    parser.add_argument("--room-color-tolerance", type=int, default=10)
    parser.add_argument("--min-door-length-m", type=float, default=0.25)
    parser.add_argument("--max-door-length-m", type=float, default=1.15)
    parser.add_argument("--max-door-label-distance-m", type=float, default=4.5)
    parser.add_argument("--door-room-tolerance-m", type=float, default=0.75)
    args = parser.parse_args()

    if args.image:
        details = build_image_cv_details(
            args.image,
            building_name=args.building_name,
            floor_name=args.floor_name,
            floor_index=args.floor_index,
            pixels_per_meter=args.pixels_per_meter,
            scale_bar_meters=args.scale_bar_meters,
            min_room_area_m2=args.min_room_area_m2,
            room_color_tolerance=args.room_color_tolerance,
            min_door_length_m=args.min_door_length_m,
            max_door_length_m=args.max_door_length_m,
            max_door_label_distance_m=args.max_door_label_distance_m,
            door_room_tolerance_m=args.door_room_tolerance_m,
            run_ocr=not args.no_ocr,
            tesseract_command=args.tesseract_command,
            ocr_language=args.ocr_language,
            ocr_min_confidence=args.ocr_min_confidence,
            ocr_scale=args.ocr_scale,
            ocr_crop_scale=args.ocr_crop_scale,
            door_ocr_radius_m=args.door_ocr_radius_m,
            image_room_id_mode=args.image_room_id_mode,
            run_door_crop_ocr=not args.no_door_crop_ocr,
        )
    else:
        if not args.pdf:
            parser.error("provide --image or --pdf")
        details = build_cv_details(
            args.pdf,
            dpi=args.dpi,
            min_room_area_m2=args.min_room_area_m2,
            room_color_tolerance=args.room_color_tolerance,
            min_door_length_m=args.min_door_length_m,
            max_door_length_m=args.max_door_length_m,
            max_door_label_distance_m=args.max_door_label_distance_m,
            door_room_tolerance_m=args.door_room_tolerance_m,
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(details, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    diagnostics = details["cv_diagnostics"]
    print(f"Source: {args.image or args.pdf}")
    print(f"Output: {args.output}")
    print(
        "Detected: "
        f"{diagnostics['rooms_detected']} rooms, "
        f"{diagnostics['doors_detected']} doors "
        f"at {diagnostics['pixels_per_meter']} px/m"
    )
    print(
        "Labels: "
        f"{diagnostics.get('room_labels_seen', diagnostics.get('ocr_words_seen', 0))} "
        f"{'room IDs' if 'room_labels_seen' in diagnostics else 'OCR words'}, "
        f"{diagnostics['door_labels_seen']} door IDs"
    )
    if diagnostics["warnings"]:
        print("Warnings:")
        for warning in diagnostics["warnings"][:20]:
            print(f"- {warning}")
        if len(diagnostics["warnings"]) > 20:
            print(f"- ... {len(diagnostics['warnings']) - 20} more")
    else:
        print("Validation: OK")


if __name__ == "__main__":
    main()

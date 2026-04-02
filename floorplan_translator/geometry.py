from __future__ import annotations

from math import sqrt
import re
import unicodedata

import cv2
import numpy as np


def ensure_odd(value: int) -> int:
    value = max(1, int(value))
    return value if value % 2 == 1 else value + 1


def scale_kernel(base_value: int, image_shape: tuple[int, int], reference_long_edge: int = 3200) -> int:
    height, width = image_shape[:2]
    scale = max(height, width) / float(reference_long_edge)
    scaled = max(1, int(round(base_value * scale)))
    return ensure_odd(scaled)


def resize_long_edge(image: np.ndarray, max_dimension: int) -> tuple[np.ndarray, float]:
    height, width = image.shape[:2]
    long_edge = max(height, width)
    if long_edge <= max_dimension:
        return image.copy(), 1.0
    ratio = max_dimension / float(long_edge)
    resized = cv2.resize(
        image,
        (int(round(width * ratio)), int(round(height * ratio))),
        interpolation=cv2.INTER_AREA,
    )
    return resized, ratio


def contour_to_polygon(contour: np.ndarray, epsilon_ratio: float) -> list[list[float]]:
    perimeter = cv2.arcLength(contour, True)
    epsilon = max(1.0, perimeter * epsilon_ratio)
    polygon = cv2.approxPolyDP(contour, epsilon, True)
    return [[float(point[0][0]), float(point[0][1])] for point in polygon]


def polygon_area(polygon: list[list[float]]) -> float:
    if len(polygon) < 3:
        return 0.0
    area = 0.0
    for index, point in enumerate(polygon):
        next_point = polygon[(index + 1) % len(polygon)]
        area += point[0] * next_point[1]
        area -= next_point[0] * point[1]
    return abs(area) / 2.0


def polygon_centroid(polygon: list[list[float]]) -> tuple[float, float]:
    if len(polygon) < 3:
        xs = [point[0] for point in polygon] or [0.0]
        ys = [point[1] for point in polygon] or [0.0]
        return sum(xs) / len(xs), sum(ys) / len(ys)

    area_factor = 0.0
    centroid_x = 0.0
    centroid_y = 0.0
    for index, point in enumerate(polygon):
        next_point = polygon[(index + 1) % len(polygon)]
        cross = point[0] * next_point[1] - next_point[0] * point[1]
        area_factor += cross
        centroid_x += (point[0] + next_point[0]) * cross
        centroid_y += (point[1] + next_point[1]) * cross

    area_factor *= 0.5
    if abs(area_factor) < 1e-6:
        xs = [point[0] for point in polygon]
        ys = [point[1] for point in polygon]
        return sum(xs) / len(xs), sum(ys) / len(ys)

    centroid_x /= 6.0 * area_factor
    centroid_y /= 6.0 * area_factor
    return centroid_x, centroid_y


def point_in_polygon(x: float, y: float, polygon: list[list[float]]) -> bool:
    inside = False
    if len(polygon) < 3:
        return False
    j = len(polygon) - 1
    for i in range(len(polygon)):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        intersects = ((yi > y) != (yj > y)) and (
            x < (xj - xi) * (y - yi) / max(1e-6, (yj - yi)) + xi
        )
        if intersects:
            inside = not inside
        j = i
    return inside


def bbox_from_points(points: np.ndarray) -> tuple[int, int, int, int]:
    ys, xs = np.where(points > 0)
    if xs.size == 0 or ys.size == 0:
        return 0, 0, points.shape[1], points.shape[0]
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def clamp_bbox(
    bbox: tuple[int, int, int, int],
    width: int,
    height: int,
    padding: int = 0,
) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = bbox
    return (
        max(0, x1 - padding),
        max(0, y1 - padding),
        min(width, x2 + padding),
        min(height, y2 + padding),
    )


def normalize_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    ascii_only = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_only.lower()).strip("-")
    return ascii_only or "item"


def bbox_dimensions(polygon: list[list[float]]) -> tuple[float, float]:
    xs = [point[0] for point in polygon]
    ys = [point[1] for point in polygon]
    return (max(xs) - min(xs), max(ys) - min(ys))


def euclidean_distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2)

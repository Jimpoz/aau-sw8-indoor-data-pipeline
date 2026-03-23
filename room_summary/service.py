from __future__ import annotations

import base64
import json
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any, Sequence
from xml.sax.saxutils import escape

import cv2
import numpy as np
import svgwrite

from pathfinding import query_runner as query_runner_module

from .runtime_env import configure_runtime_env

configure_runtime_env()

from ultralytics import YOLO

from .class_labels import load_class_labels, resolve_class_label
from .Detection import Detection
from .model_download import ensure_model_path
from .model_config import resolve_model_selection
from .RoomImageInput import RoomImageInput
from . import RoomObjectDetectionSetupResult as room_object_detection_setup_result_module
from . import RoomSummaryRepository as room_summary_repository_module
from .RoomSummaryOnlyResult import RoomSummaryOnlyResult
from .RoomSummaryResult import RoomSummaryResult
from .ViewSummary import ViewSummary


@lru_cache(maxsize=4)
def _load_model(model_path: str) -> YOLO:
    return YOLO(model_path)


class RoomSummaryMiddleware:
    def __init__(
        self,
        model_path: str | Path | None = None,
        model_config_path: str | Path | None = None,
        model_profile: str | None = None,
        class_config_path: str | Path | None = None,
        confidence_threshold: float = 0.25,
        vector_palette_size: int = 8,
        max_vector_width: int = 480,
    ) -> None:
        selection = resolve_model_selection(
            config_path=model_config_path,
            requested_profile=model_profile,
            fallback_model_path=model_path,
            fallback_confidence_threshold=confidence_threshold,
        )

        self.model_profile = selection.profile_name
        self.model_path = selection.model_path
        explicit_class_config = (
            Path(class_config_path) if class_config_path is not None else None
        )
        self.class_config_path = explicit_class_config or selection.class_config_path
        self.confidence_threshold = selection.confidence_threshold
        self.vector_palette_size = max(4, vector_palette_size)
        self.max_vector_width = max(240, max_vector_width)

        self.model_path = ensure_model_path(self.model_path)
        self._validate_model_path()
        self.class_overrides = load_class_labels(self.class_config_path)

    def list_room_names(self, conn: Any) -> list[str]:
        return self._room_summary_repository(conn).list_room_names()

    def summarize_images(
        self,
        images: Sequence[RoomImageInput],
        expected_views: int = 4,
    ) -> RoomSummaryResult:
        if len(images) != expected_views:
            raise ValueError(f"Exactly {expected_views} images are required.")

        overall_counts: Counter[str] = Counter()
        views: list[ViewSummary] = []

        for view_index, image in enumerate(images, start=1):
            detections = self._detect_objects(image.frame)
            counts = Counter(detection.label for detection in detections)
            overall_counts.update(counts)

            svg = self._vectorize_frame(
                frame=image.frame,
                view_index=view_index,
                source_name=self._display_source_name(image.source_name, view_index),
                object_counts=dict(sorted(counts.items())),
            )

            views.append(
                ViewSummary(
                    view_index=view_index,
                    source_name=image.source_name,
                    object_counts=dict(sorted(counts.items())),
                    svg=svg,
                )
            )

        return RoomSummaryResult(
            model_profile=self.model_profile,
            model_path=str(self.model_path),
            overall_object_counts=dict(sorted(overall_counts.items())),
            views=views,
        )

    def summarize_room(
        self,
        room_name: str,
        images: Sequence[RoomImageInput],
        expected_views: int = 4,
    ) -> RoomSummaryOnlyResult:
        normalized_room_name = room_name.strip()
        if not normalized_room_name:
            raise ValueError("Room name is required.")

        result = self.summarize_images(
            images=images,
            expected_views=expected_views,
        )
        return RoomSummaryOnlyResult(
            room_name=normalized_room_name,
            room_summary=result.views,
        )

    def setup_room_object_detection(
        self,
        room_name: str,
        images: Sequence[RoomImageInput],
        conn: Any,
        expected_views: int = 4,
    ) -> "room_object_detection_setup_result_module.RoomObjectDetectionSetupResult":
        normalized_room_name = room_name.strip()
        if not normalized_room_name:
            raise ValueError("Room name is required.")

        result = self.summarize_images(
            images=images,
            expected_views=expected_views,
        )
        room_objects = self._build_room_objects(result.overall_object_counts)
        room_object_counts_json = self._build_room_object_counts_json(
            result.overall_object_counts,
        )
        stored_room_name = self._room_summary_repository(conn).replace_room_detection_setup(
            room_name=normalized_room_name,
            room_objects=room_objects,
            room_object_counts_json=room_object_counts_json,
            room_images=[
                self._frame_to_embedded_svg(image.frame, image.source_name)
                for image in images
            ],
        )
        return room_object_detection_setup_result_module.RoomObjectDetectionSetupResult(
            room_name=stored_room_name,
            room_objects=room_objects,
            room_object_counts_json=room_object_counts_json,
            room_summary=result.views,
        )

    def _detect_objects(self, frame: np.ndarray) -> list[Detection]:
        result = _load_model(str(self.model_path)).predict(
            source=frame,
            conf=self.confidence_threshold,
            verbose=False,
        )[0]

        detections: list[Detection] = []
        model_names = result.names

        for box in result.boxes:
            class_id = int(box.cls[0].item())
            x1, y1, x2, y2 = (int(value) for value in box.xyxy[0].tolist())
            label = resolve_class_label(
                model_names=model_names,
                class_id=class_id,
                overrides=self.class_overrides,
            )
            detections.append(
                Detection(
                    class_id=class_id,
                    label=label,
                    confidence=float(box.conf[0].item()),
                    bbox=(x1, y1, x2, y2),
                )
            )

        return detections

    def _vectorize_frame(
        self,
        frame: np.ndarray,
        view_index: int,
        source_name: str,
        object_counts: dict[str, int],
    ) -> str:
        scaled = self._resize_for_vectorization(frame)
        height, width = scaled.shape[:2]
        footer_height = 88

        drawing = svgwrite.Drawing(size=(width, height + footer_height))
        drawing.add(
            drawing.rect(
                insert=(0, 0),
                size=(width, height + footer_height),
                fill="#f5f1e8",
            )
        )

        quantized, labels, centers = self._quantize_colors(scaled)
        total_area = width * height
        min_area = max(120, total_area // 450)

        for color_index, center in enumerate(centers):
            mask = np.where(labels == color_index, 255, 0).astype(np.uint8)
            mask = cv2.morphologyEx(
                mask,
                cv2.MORPH_OPEN,
                np.ones((3, 3), dtype=np.uint8),
            )
            contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
            contours = sorted(contours, key=cv2.contourArea, reverse=True)[:48]

            fill = self._rgb_hex(tuple(int(channel) for channel in center))
            for contour in contours:
                area = cv2.contourArea(contour)
                if area < min_area:
                    continue
                epsilon = 0.012 * cv2.arcLength(contour, True)
                polygon = cv2.approxPolyDP(contour, epsilon, True)
                points = [
                    (int(point[0][0]), int(point[0][1]))
                    for point in polygon
                    if len(point[0]) == 2
                ]
                if len(points) < 3:
                    continue
                drawing.add(
                    drawing.polygon(
                        points=points,
                        fill=fill,
                        stroke=fill,
                        stroke_width=1,
                    )
                )

        edges = cv2.Canny(cv2.cvtColor(quantized, cv2.COLOR_BGR2GRAY), 60, 140)
        contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        for contour in sorted(
            contours,
            key=lambda contour_points: cv2.arcLength(contour_points, False),
            reverse=True,
        )[:120]:
            if cv2.arcLength(contour, False) < 30:
                continue
            points = [
                (int(point[0][0]), int(point[0][1]))
                for point in contour[::4]
            ]
            if len(points) < 2:
                continue
            drawing.add(
                drawing.polyline(
                    points=points,
                    fill="none",
                    stroke="#2b241d",
                    stroke_width=1,
                    opacity=0.18,
                )
            )

        drawing.add(
            drawing.rect(
                insert=(0, height),
                size=(width, footer_height),
                fill="#1f1c18",
                opacity=0.94,
            )
        )
        drawing.add(
            drawing.text(
                f"View {view_index}",
                insert=(18, height + 28),
                fill="#f8f3eb",
                font_size=20,
                font_family="Helvetica, Arial, sans-serif",
                font_weight="bold",
            )
        )
        drawing.add(
            drawing.text(
                source_name,
                insert=(18, height + 54),
                fill="#d6cbbb",
                font_size=14,
                font_family="Helvetica, Arial, sans-serif",
            )
        )

        summary_text = self._format_counts(object_counts)
        drawing.add(
            drawing.text(
                summary_text[0],
                insert=(140, height + 35),
                fill="#f8f3eb",
                font_size=14,
                font_family="Helvetica, Arial, sans-serif",
            )
        )
        if len(summary_text) > 1:
            drawing.add(
                drawing.text(
                    summary_text[1],
                    insert=(140, height + 55),
                    fill="#f8f3eb",
                    font_size=14,
                    font_family="Helvetica, Arial, sans-serif",
                )
            )
        drawing.add(
            drawing.text(
                "Room summary generated from the uploaded image set",
                insert=(140, height + 74),
                fill="#d6cbbb",
                font_size=12,
                font_family="Helvetica, Arial, sans-serif",
            )
        )
        return drawing.tostring()

    def _resize_for_vectorization(self, frame: np.ndarray) -> np.ndarray:
        height, width = frame.shape[:2]
        if width <= self.max_vector_width:
            return frame.copy()

        scale = self.max_vector_width / width
        resized_height = max(1, int(round(height * scale)))
        return cv2.resize(
            frame,
            (self.max_vector_width, resized_height),
            interpolation=cv2.INTER_AREA,
        )

    def _quantize_colors(
        self,
        frame: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        pixels = np.float32(frame.reshape((-1, 3)))
        criteria = (
            cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER,
            12,
            1.0,
        )
        _, labels, centers = cv2.kmeans(
            pixels,
            self.vector_palette_size,
            None,
            criteria,
            2,
            cv2.KMEANS_PP_CENTERS,
        )
        centers = np.uint8(centers)
        quantized = centers[labels.flatten()].reshape(frame.shape)
        return quantized, labels.reshape(frame.shape[:2]), centers

    def _format_counts(self, object_counts: dict[str, int]) -> list[str]:
        if not object_counts:
            return ["No objects detected in this view"]

        chunks: list[str] = []
        current_chunk = ""

        for token in (
            f"{label} x{count}"
            for label, count in sorted(object_counts.items())
        ):
            separator = " | " if current_chunk else ""
            candidate = f"{current_chunk}{separator}{token}"
            if len(candidate) > 48 and current_chunk:
                chunks.append(current_chunk)
                current_chunk = token
            else:
                current_chunk = candidate

        if current_chunk:
            chunks.append(current_chunk)

        return chunks[:2]

    @staticmethod
    def _build_room_objects(object_counts: dict[str, int]) -> list[str]:
        return [label for label, count in sorted(object_counts.items()) if count > 0]

    @staticmethod
    def _build_room_object_counts_json(object_counts: dict[str, int]) -> str:
        return json.dumps(dict(sorted(object_counts.items())), separators=(",", ":"))

    @staticmethod
    def _frame_to_embedded_svg(frame: np.ndarray, source_name: str) -> str:
        height, width = frame.shape[:2]
        success, encoded = cv2.imencode(
            ".jpg",
            frame,
            [int(cv2.IMWRITE_JPEG_QUALITY), 92],
        )
        if not success:
            raise ValueError(f"Could not encode uploaded image {source_name!r}.")

        image_base64 = base64.b64encode(encoded.tobytes()).decode("ascii")
        safe_name = escape(Path(source_name).name or "image")
        return (
            f"<svg xmlns='http://www.w3.org/2000/svg' width='{width}' height='{height}' "
            f"viewBox='0 0 {width} {height}'>"
            f"<title>{safe_name}</title>"
            f"<image width='{width}' height='{height}' preserveAspectRatio='none' "
            f"href='data:image/jpeg;base64,{image_base64}' />"
            "</svg>"
        )

    @staticmethod
    def _room_summary_repository(conn: Any):
        return room_summary_repository_module.RoomSummaryRepository(
            query_runner_module.Neo4jQueryRunner(conn),
        )

    def _validate_model_path(self) -> None:
        if not self.model_path.exists():
            raise FileNotFoundError(f"YOLO model not found: {self.model_path}")

        if self.model_path.stat().st_size < 1024:
            header = self.model_path.read_text(encoding="utf-8", errors="ignore")[:128]
            if header.startswith("version https://git-lfs.github.com/spec/v1"):
                raise FileNotFoundError(
                    "YOLO model file is a Git LFS pointer, not the real weights: "
                    f"{self.model_path}. Pull the actual .pt weights with Git LFS "
                    "or point the selected profile/path to a real model file."
                )

    def _display_source_name(self, source_name: str, view_index: int) -> str:
        display_name = Path(source_name).name.strip() or f"image_{view_index}"
        if len(display_name) <= 28:
            return display_name
        return f"{display_name[:25]}..."

    def _rgb_hex(self, color: tuple[int, int, int]) -> str:
        blue, green, red = color
        return f"#{red:02x}{green:02x}{blue:02x}"

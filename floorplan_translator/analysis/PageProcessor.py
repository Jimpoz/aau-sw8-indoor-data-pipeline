from __future__ import annotations

from typing import Any

import cv2
import numpy as np

from .DebugArtifactBuilder import DebugArtifactBuilder
from .TranslatorOverrides import TranslatorOverrides
from ..configuration.TranslatorConfig import TranslatorConfig
from ..documents.LoadedPage import LoadedPage
from ..documents.SourceDocument import SourceDocument
from ..geometry import (
    bbox_dimensions,
    bbox_from_points,
    clamp_bbox,
    contour_to_polygon,
    polygon_area,
    polygon_centroid,
    resize_long_edge,
    scale_kernel,
)
from ..inference.NameInferenceService import NameInferenceService
from ..inference.OcrTextExtractor import OcrTextExtractor
from ..inference.ScaleInferenceService import ScaleInferenceService
from ..inference.SpaceTypeClassifier import SpaceTypeClassifier
from ..inference.TextBlock import TextBlock
from ..results.AnalyzedConnection import AnalyzedConnection
from ..results.AnalyzedSpace import AnalyzedSpace
from ..results.PageAnalysis import PageAnalysis


class PageProcessor:
    def __init__(
        self,
        config: TranslatorConfig,
        name_inference: NameInferenceService,
        text_extractor: OcrTextExtractor,
        scale_inference: ScaleInferenceService,
        space_classifier: SpaceTypeClassifier,
        debug_builder: DebugArtifactBuilder,
    ) -> None:
        self.config = config
        self.name_inference = name_inference
        self.text_extractor = text_extractor
        self.scale_inference = scale_inference
        self.space_classifier = space_classifier
        self.debug_builder = debug_builder

    def analyze_page(
        self,
        page: LoadedPage,
        document: SourceDocument,
        overrides: TranslatorOverrides,
        building_id: str,
    ) -> PageAnalysis:
        warnings: list[str] = []
        resized, resize_ratio = resize_long_edge(page.image, self.config.input.processing_max_dimension)
        fractional_crop = self._fractional_crop(resized)
        gray = cv2.cvtColor(fractional_crop, cv2.COLOR_BGR2GRAY)

        blur_kernel = scale_kernel(self.config.geometry.blur_kernel, gray.shape)
        blurred = cv2.GaussianBlur(gray, (blur_kernel, blur_kernel), 0)
        _, binary = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

        wall_mask = self._extract_wall_mask(binary)
        barrier_mask = self._build_enclosure_barrier(binary, wall_mask)
        drawing_bbox = clamp_bbox(
            bbox_from_points(barrier_mask if np.any(barrier_mask) else wall_mask),
            width=barrier_mask.shape[1],
            height=barrier_mask.shape[0],
            padding=self.config.input.bbox_padding_px,
        )
        x1, y1, x2, y2 = drawing_bbox

        crop = fractional_crop[y1:y2, x1:x2]
        gray = gray[y1:y2, x1:x2]
        cropped_blurred = blurred[y1:y2, x1:x2]
        binary = binary[y1:y2, x1:x2]
        wall_mask = wall_mask[y1:y2, x1:x2]
        barrier_mask = barrier_mask[y1:y2, x1:x2]
        sealed_wall_mask = self._seal_wall_openings(barrier_mask)
        interior_mask, region_labels, footprint_mask, enclosure_strategy = self._extract_enclosed_regions(
            barrier_mask,
            sealed_wall_mask,
        )
        opening_mask = self._extract_opening_mask(barrier_mask, sealed_wall_mask)
        text_blocks = self.text_extractor.extract_text_blocks(crop)
        floor_index, floor_name = self.name_inference.infer_floor(
            document.source_name,
            document.metadata,
            page.page_index,
            explicit_name=overrides.floor_name,
            explicit_index=overrides.floor_index,
        )

        scale_value, scale_source = self.scale_inference.resolve_scale(
            text_blocks=text_blocks,
            image_shape=gray.shape,
            override_scale=overrides.scale_meters_per_pixel,
        )
        if scale_value is None:
            warnings.append("Scale could not be inferred. Geometry is exported in pixels and metric fields are omitted.")

        spaces = self._extract_spaces(
            interior_mask=interior_mask,
            region_labels=region_labels,
            crop_shape=gray.shape,
            text_blocks=text_blocks,
            meters_per_pixel=scale_value,
            building_id=building_id,
            floor_index=floor_index,
        )
        doors = self._extract_connections(
            opening_mask=opening_mask,
            region_labels=region_labels,
            spaces=spaces,
            meters_per_pixel=scale_value,
            building_id=building_id,
            floor_index=floor_index,
        )
        self._apply_connection_counts(spaces, doors)
        overlay = self._build_overlay(crop, spaces, doors)
        debug_enabled = (
            overrides.debug
            or overrides.preview_pipeline_images
            or self.config.debug.save_artifacts
            or overrides.include_debug_images
        )
        include_debug_images = (
            overrides.include_debug_images
            or overrides.preview_pipeline_images
            or self.config.debug.embed_debug_images
        )
        debug_payload = self.debug_builder.build(
            enabled=debug_enabled,
            include_debug_images=include_debug_images,
            debug_dir=overrides.debug_dir,
            document_name=document.source_name,
            page_index=page.page_index,
            images={
                "00_resized_input": resized,
                "01_fractional_crop": fractional_crop,
                "02_drawing_crop": crop,
                "03_gray": gray,
                "04_blurred": cropped_blurred,
                "05_binary": binary,
                "06_wall_mask": barrier_mask,
                "07_sealed_wall_mask": sealed_wall_mask,
                "08_footprint_mask": footprint_mask,
                "09_interior_mask": interior_mask,
                "10_openings": opening_mask,
                "11_overlay": overlay,
            },
        )
        if debug_payload:
            debug_payload["preview_pipeline_images"] = overrides.preview_pipeline_images

        return PageAnalysis(
            id=f"{building_id}-f{floor_index}",
            floor_index=floor_index,
            display_name=floor_name,
            source_page=page.page_index + 1,
            coordinate_unit="m" if scale_value is not None else "px",
            scale_meters_per_pixel=scale_value,
            scale_source=scale_source,
            footprint=self._extract_footprint(footprint_mask, scale_value),
            walls=self._extract_wall_polygons(wall_mask, scale_value),
            spaces=spaces,
            doors=doors,
            warnings=warnings,
            debug=debug_payload,
            metadata={
                "resize_ratio": resize_ratio,
                "drawing_bbox": {"x1": x1, "y1": y1, "x2": x2, "y2": y2},
                "enclosure_strategy": enclosure_strategy,
                "pipeline_preview_enabled": overrides.preview_pipeline_images,
                "ocr_text_count": len(text_blocks),
            },
        )

    def _fractional_crop(self, image: np.ndarray) -> np.ndarray:
        height, width = image.shape[:2]
        top = int(round(height * self.config.input.crop_top_fraction))
        bottom = int(round(height * (1.0 - self.config.input.crop_bottom_fraction)))
        left = int(round(width * self.config.input.crop_left_fraction))
        right = int(round(width * (1.0 - self.config.input.crop_right_fraction)))
        return image[top:bottom, left:right]

    def _extract_wall_mask(self, binary: np.ndarray) -> np.ndarray:
        kernel_size = scale_kernel(self.config.geometry.wall_open_kernel, binary.shape)
        square_opened = cv2.morphologyEx(
            binary,
            cv2.MORPH_OPEN,
            np.ones((kernel_size, kernel_size), dtype=np.uint8),
        )
        horizontal_length = max(9, binary.shape[1] // 180)
        vertical_length = max(9, binary.shape[0] // 180)
        horizontal = cv2.morphologyEx(
            square_opened,
            cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_RECT, (horizontal_length, 1)),
        )
        vertical = cv2.morphologyEx(
            square_opened,
            cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_RECT, (1, vertical_length)),
        )
        opened = cv2.bitwise_or(horizontal, vertical)
        dilate_size = scale_kernel(self.config.geometry.wall_dilate_kernel, binary.shape)
        dilated = cv2.dilate(
            opened,
            np.ones((dilate_size, dilate_size), dtype=np.uint8),
            iterations=1,
        )
        component_count, labels, stats, _ = cv2.connectedComponentsWithStats(dilated, connectivity=8)
        cleaned = np.zeros_like(dilated)
        for label_index in range(1, component_count):
            area = stats[label_index, cv2.CC_STAT_AREA]
            if area >= self.config.geometry.wall_component_min_area:
                cleaned[labels == label_index] = 255
        return cleaned

    def _build_enclosure_barrier(self, binary: np.ndarray, wall_mask: np.ndarray) -> np.ndarray:
        axis_line_mask = self._extract_axis_line_mask(binary)
        combined = cv2.bitwise_or(wall_mask, axis_line_mask)
        cleanup_kernel = scale_kernel(max(3, self.config.geometry.wall_dilate_kernel), binary.shape)
        combined = cv2.morphologyEx(
            combined,
            cv2.MORPH_CLOSE,
            np.ones((cleanup_kernel, cleanup_kernel), dtype=np.uint8),
        )
        component_count, labels, stats, _ = cv2.connectedComponentsWithStats(combined, connectivity=8)
        cleaned = np.zeros_like(combined)
        min_long_dimension = max(24, int(round(max(binary.shape[:2]) / 90.0)))
        min_area = max(24, self.config.geometry.wall_component_min_area // 2)
        for label_index in range(1, component_count):
            area = stats[label_index, cv2.CC_STAT_AREA]
            width = stats[label_index, cv2.CC_STAT_WIDTH]
            height = stats[label_index, cv2.CC_STAT_HEIGHT]
            if area >= min_area or max(width, height) >= min_long_dimension:
                cleaned[labels == label_index] = 255
        return cleaned

    def _extract_axis_line_mask(self, binary: np.ndarray) -> np.ndarray:
        height, width = binary.shape[:2]
        long_edge = max(height, width)
        vote_threshold = max(20, int(round(long_edge / 65.0)))
        min_line_length = max(20, int(round(long_edge / 48.0)))
        max_line_gap = max(4, int(round(long_edge / 360.0)))
        axis_slack = max(3, int(round(long_edge / 700.0)))
        line_thickness = max(1, scale_kernel(max(1, self.config.geometry.wall_dilate_kernel - 1), binary.shape))
        mask = np.zeros_like(binary)
        lines = cv2.HoughLinesP(
            binary,
            rho=1,
            theta=np.pi / 180.0,
            threshold=vote_threshold,
            minLineLength=min_line_length,
            maxLineGap=max_line_gap,
        )
        if lines is None:
            return mask

        for line in lines[:, 0, :]:
            x1, y1, x2, y2 = (int(value) for value in line)
            delta_x = abs(x2 - x1)
            delta_y = abs(y2 - y1)
            if min(delta_x, delta_y) > axis_slack:
                continue
            if max(delta_x, delta_y) < min_line_length:
                continue
            cv2.line(mask, (x1, y1), (x2, y2), 255, line_thickness)
        return mask

    def _seal_wall_openings(self, wall_mask: np.ndarray) -> np.ndarray:
        kernel_size = scale_kernel(self.config.geometry.opening_close_kernel, wall_mask.shape)
        horizontal = cv2.morphologyEx(
            wall_mask,
            cv2.MORPH_CLOSE,
            cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_size, 1)),
        )
        vertical = cv2.morphologyEx(
            wall_mask,
            cv2.MORPH_CLOSE,
            cv2.getStructuringElement(cv2.MORPH_RECT, (1, kernel_size)),
        )
        micro_kernel = max(3, kernel_size // 5)
        if micro_kernel % 2 == 0:
            micro_kernel += 1
        local_fill = cv2.morphologyEx(
            wall_mask,
            cv2.MORPH_CLOSE,
            np.ones((micro_kernel, micro_kernel), dtype=np.uint8),
        )
        return cv2.bitwise_or(wall_mask, cv2.bitwise_or(horizontal, cv2.bitwise_or(vertical, local_fill)))

    def _extract_enclosed_regions(
        self,
        barrier_mask: np.ndarray,
        sealed_wall_mask: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, str]:
        border_fill_candidate = self._extract_enclosed_regions_by_border_fill(sealed_wall_mask)
        contour_candidate = self._extract_enclosed_regions_by_contours(barrier_mask, sealed_wall_mask)
        if self._score_region_candidate(border_fill_candidate) >= self._score_region_candidate(contour_candidate):
            interior, labels, footprint = border_fill_candidate
            return interior, labels, footprint, "border-fill"
        interior, labels, footprint = contour_candidate
        return interior, labels, footprint, "contour-fallback"

    def _extract_enclosed_regions_by_border_fill(
        self,
        sealed_wall_mask: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        free_space = cv2.bitwise_not(sealed_wall_mask)
        enclosed_free_space = self._remove_border_connected_regions(free_space)
        footprint_kernel = max(scale_kernel(max(5, self.config.geometry.wall_dilate_kernel * 3), sealed_wall_mask.shape), 3)
        interior_kernel = max(scale_kernel(max(5, self.config.geometry.wall_dilate_kernel * 2), sealed_wall_mask.shape), 3)
        smoothed_enclosed = cv2.morphologyEx(
            enclosed_free_space,
            cv2.MORPH_OPEN,
            np.ones((interior_kernel, interior_kernel), dtype=np.uint8),
        )
        smoothed_enclosed = cv2.morphologyEx(
            smoothed_enclosed,
            cv2.MORPH_CLOSE,
            np.ones((interior_kernel, interior_kernel), dtype=np.uint8),
        )
        footprint_mask = cv2.bitwise_or(smoothed_enclosed, sealed_wall_mask)
        footprint_mask = cv2.morphologyEx(
            footprint_mask,
            cv2.MORPH_CLOSE,
            np.ones((footprint_kernel, footprint_kernel), dtype=np.uint8),
        )

        interior = cv2.bitwise_and(footprint_mask, cv2.bitwise_not(sealed_wall_mask))
        interior = self._remove_border_connected_regions(interior)
        return (*self._finalize_region_components(interior), footprint_mask)

    def _extract_enclosed_regions_by_contours(
        self,
        barrier_mask: np.ndarray,
        sealed_wall_mask: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        footprint_kernel = max(
            scale_kernel(self.config.geometry.opening_close_kernel * 2, barrier_mask.shape),
            scale_kernel(35, barrier_mask.shape),
        )
        footprint_seed = cv2.morphologyEx(
            barrier_mask,
            cv2.MORPH_CLOSE,
            np.ones((footprint_kernel, footprint_kernel), dtype=np.uint8),
        )
        contours, _ = cv2.findContours(footprint_seed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        footprint_mask = np.zeros_like(sealed_wall_mask)
        min_footprint_area = barrier_mask.shape[0] * barrier_mask.shape[1] * 0.003
        for contour in contours:
            if cv2.contourArea(contour) >= min_footprint_area:
                cv2.drawContours(footprint_mask, [contour], -1, 255, thickness=-1)

        interior = cv2.bitwise_and(footprint_mask, cv2.bitwise_not(sealed_wall_mask))
        return (*self._finalize_region_components(interior), footprint_mask)

    def _finalize_region_components(self, interior: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        min_area = max(40, int(interior.shape[0] * interior.shape[1] * self.config.geometry.space_min_area_ratio))
        component_count, labels, stats, _ = cv2.connectedComponentsWithStats(interior, connectivity=8)
        cleaned = np.zeros_like(interior)
        relabeled = np.zeros_like(labels)
        new_label = 1
        for label_index in range(1, component_count):
            area = stats[label_index, cv2.CC_STAT_AREA]
            if area < min_area:
                continue
            cleaned[labels == label_index] = 255
            relabeled[labels == label_index] = new_label
            new_label += 1
        return cleaned, relabeled

    def _score_region_candidate(self, candidate: tuple[np.ndarray, np.ndarray, np.ndarray]) -> float:
        interior_mask, region_labels, footprint_mask = candidate
        interior_area = float(np.count_nonzero(interior_mask))
        footprint_area = float(np.count_nonzero(footprint_mask))
        component_count = int(region_labels.max())
        if interior_area <= 0.0 or footprint_area <= 0.0:
            return 0.0
        coverage = interior_area / footprint_area
        coverage_penalty = 0.0
        if coverage < 0.015 or coverage > 0.92:
            coverage_penalty = interior_area * 0.35
        return interior_area * (1.0 + min(component_count, 12) / 10.0) - coverage_penalty

    def _remove_border_connected_regions(self, mask: np.ndarray) -> np.ndarray:
        remaining = mask.copy()
        height, width = remaining.shape[:2]
        flood_mask = np.zeros((height + 2, width + 2), dtype=np.uint8)

        for x in range(width):
            if remaining[0, x] > 0:
                cv2.floodFill(remaining, flood_mask, (x, 0), 0)
            if remaining[height - 1, x] > 0:
                cv2.floodFill(remaining, flood_mask, (x, height - 1), 0)

        for y in range(height):
            if remaining[y, 0] > 0:
                cv2.floodFill(remaining, flood_mask, (0, y), 0)
            if remaining[y, width - 1] > 0:
                cv2.floodFill(remaining, flood_mask, (width - 1, y), 0)

        return remaining

    def _extract_opening_mask(self, wall_mask: np.ndarray, sealed_wall_mask: np.ndarray) -> np.ndarray:
        openings = cv2.subtract(sealed_wall_mask, wall_mask)
        component_count, labels, stats, _ = cv2.connectedComponentsWithStats(openings, connectivity=8)
        cleaned = np.zeros_like(openings)
        for label_index in range(1, component_count):
            area = stats[label_index, cv2.CC_STAT_AREA]
            if self.config.geometry.opening_min_area <= area <= self.config.geometry.opening_max_area:
                cleaned[labels == label_index] = 255
        return cleaned

    def _extract_spaces(
        self,
        interior_mask: np.ndarray,
        region_labels: np.ndarray,
        crop_shape: tuple[int, int],
        text_blocks: list[TextBlock],
        meters_per_pixel: float | None,
        building_id: str,
        floor_index: int,
    ) -> list[AnalyzedSpace]:
        contours, _ = cv2.findContours(interior_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        crop_area = crop_shape[0] * crop_shape[1]
        spaces: list[AnalyzedSpace] = []
        for index, contour in enumerate(sorted(contours, key=cv2.contourArea, reverse=True), start=1):
            area_px = float(cv2.contourArea(contour))
            if area_px <= 0:
                continue
            polygon_px = contour_to_polygon(contour, self.config.geometry.polygon_epsilon_ratio)
            centroid_px = polygon_centroid(polygon_px)
            area_ratio = area_px / max(crop_area, 1)
            contour_mask = np.zeros_like(interior_mask)
            cv2.drawContours(contour_mask, [contour], -1, 255, thickness=-1)
            labels_in_region = region_labels[contour_mask > 0]
            labels_in_region = labels_in_region[labels_in_region > 0]
            region_label = int(np.bincount(labels_in_region).argmax()) if labels_in_region.size else index
            label = self.text_extractor.label_space(
                polygon=polygon_px,
                text_blocks=text_blocks,
                fallback_prefix=self.config.project.default_space_name_prefix,
                index=index,
                min_confidence=self.config.geometry.room_label_min_confidence,
            )
            space_type = self.space_classifier.classify(label, 0, area_ratio)
            polygon_units = self._convert_polygon_units(polygon_px, meters_per_pixel)
            width_units, height_units = bbox_dimensions(polygon_units)
            centroid_units = self._convert_point_units(centroid_px, meters_per_pixel)
            space_id = self.name_inference.slug_id(f"{building_id}-f{floor_index}-{label}-{index}", prefix="space")
            metadata = {
                "translator": {
                    "raw_area_px": area_px,
                    "area_ratio": area_ratio,
                    "label_source": "ocr" if not label.startswith(self.config.project.default_space_name_prefix) else "generated",
                    "coordinate_unit": "m" if meters_per_pixel is not None else "px",
                    "meters_per_pixel": meters_per_pixel,
                }
            }
            spaces.append(
                AnalyzedSpace(
                    id=space_id,
                    display_name=label,
                    space_type=space_type,
                    centroid_x=centroid_units[0],
                    centroid_y=centroid_units[1],
                    polygon=polygon_units,
                    width_m=width_units if meters_per_pixel is not None else None,
                    length_m=height_units if meters_per_pixel is not None else None,
                    area_m2=polygon_area(polygon_units) if meters_per_pixel is not None else None,
                    tags=[],
                    metadata=metadata,
                    raw_polygon_px=polygon_px,
                    raw_centroid_px=centroid_px,
                    area_ratio=area_ratio,
                    region_label=region_label,
                )
            )
        return spaces

    def _extract_connections(
        self,
        opening_mask: np.ndarray,
        region_labels: np.ndarray,
        spaces: list[AnalyzedSpace],
        meters_per_pixel: float | None,
        building_id: str,
        floor_index: int,
    ) -> list[AnalyzedConnection]:
        connections: list[AnalyzedConnection] = []
        component_count, labels, _stats, centroids = cv2.connectedComponentsWithStats(opening_mask, connectivity=8)
        touch_kernel = scale_kernel(self.config.geometry.opening_touch_kernel, opening_mask.shape)
        dilate_kernel = np.ones((touch_kernel, touch_kernel), dtype=np.uint8)
        label_to_space = {space.region_label: space for space in spaces}

        for component_index in range(1, component_count):
            component_mask = np.where(labels == component_index, 255, 0).astype(np.uint8)
            dilated = cv2.dilate(component_mask, dilate_kernel, iterations=1)
            touching_labels = sorted(int(label) for label in np.unique(region_labels[dilated > 0]) if label > 0)
            if len(touching_labels) != 2:
                continue
            space_a = label_to_space.get(touching_labels[0])
            space_b = label_to_space.get(touching_labels[1])
            if not space_a or not space_b or space_a.id == space_b.id:
                continue

            opening_contours, _ = cv2.findContours(component_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if not opening_contours:
                continue
            contour = max(opening_contours, key=cv2.contourArea)
            polygon_px = contour_to_polygon(contour, self.config.geometry.polygon_epsilon_ratio)
            centroid_px = (float(centroids[component_index][0]), float(centroids[component_index][1]))
            polygon_units = self._convert_polygon_units(polygon_px, meters_per_pixel)
            centroid_units = self._convert_point_units(centroid_px, meters_per_pixel)
            width_units, height_units = bbox_dimensions(polygon_units)
            orientation = "horizontal" if width_units >= height_units else "vertical"
            connection_id = self.name_inference.slug_id(
                f"{building_id}-f{floor_index}-door-{component_index}",
                prefix="conn",
            )
            connections.append(
                AnalyzedConnection(
                    id=connection_id,
                    display_name=f"Door {len(connections) + 1}",
                    space_type=self.config.project.default_door_type,
                    connects=[space_a.id, space_b.id],
                    centroid_x=centroid_units[0],
                    centroid_y=centroid_units[1],
                    polygon=polygon_units,
                    tags=[orientation, f"floor:{floor_index}"],
                    metadata={
                        "translator": {
                            "width": max(width_units, height_units),
                            "orientation": orientation,
                            "coordinate_unit": "m" if meters_per_pixel is not None else "px",
                        }
                    },
                    raw_centroid_px=centroid_px,
                )
            )
        return connections

    def _apply_connection_counts(self, spaces: list[AnalyzedSpace], connections: list[AnalyzedConnection]) -> None:
        door_counts: dict[str, int] = {}
        for connection in connections:
            for space_id in connection.connects:
                door_counts[space_id] = door_counts.get(space_id, 0) + 1

        for space in spaces:
            door_count = door_counts.get(space.id, 0)
            resolved_space_type = None
            if space.display_name.startswith(f"{self.config.project.default_space_name_prefix} "):
                resolved_space_type = self.space_classifier.classify(
                    label=space.display_name,
                    door_count=door_count,
                    area_ratio=space.area_ratio,
                )
            space.apply_door_count(door_count, resolved_space_type)

    def _extract_footprint(self, footprint_mask: np.ndarray, meters_per_pixel: float | None) -> list[list[float]]:
        contours, _ = cv2.findContours(footprint_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return []
        footprint = contour_to_polygon(max(contours, key=cv2.contourArea), self.config.geometry.polygon_epsilon_ratio)
        return self._convert_polygon_units(footprint, meters_per_pixel)

    def _extract_wall_polygons(self, wall_mask: np.ndarray, meters_per_pixel: float | None) -> list[list[list[float]]]:
        contours, _ = cv2.findContours(wall_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        polygons: list[list[list[float]]] = []
        for contour in contours:
            if cv2.contourArea(contour) < self.config.geometry.wall_component_min_area:
                continue
            polygon = contour_to_polygon(contour, self.config.geometry.polygon_epsilon_ratio)
            polygons.append(self._convert_polygon_units(polygon, meters_per_pixel))
        polygons.sort(key=polygon_area, reverse=True)
        return polygons

    def _build_overlay(
        self,
        crop: np.ndarray,
        spaces: list[AnalyzedSpace],
        connections: list[AnalyzedConnection],
    ) -> np.ndarray:
        overlay = crop.copy()
        for space in spaces:
            polygon = np.array(space.raw_polygon_px, dtype=np.int32)
            if polygon.size == 0:
                continue
            cv2.polylines(overlay, [polygon], True, (50, 170, 255), 2)
            if space.raw_centroid_px is not None:
                cv2.putText(
                    overlay,
                    space.display_name[:24],
                    (int(space.raw_centroid_px[0]), int(space.raw_centroid_px[1])),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.4,
                    (0, 60, 180),
                    1,
                    cv2.LINE_AA,
                )
        for connection in connections:
            if connection.raw_centroid_px is None:
                continue
            cv2.circle(
                overlay,
                (int(connection.raw_centroid_px[0]), int(connection.raw_centroid_px[1])),
                5,
                (0, 0, 255),
                -1,
            )
        return overlay

    @staticmethod
    def _convert_point_units(point: tuple[float, float], meters_per_pixel: float | None) -> tuple[float, float]:
        if meters_per_pixel is None:
            return round(point[0], 3), round(point[1], 3)
        return round(point[0] * meters_per_pixel, 3), round(point[1] * meters_per_pixel, 3)

    @staticmethod
    def _convert_polygon_units(
        polygon: list[list[float]],
        meters_per_pixel: float | None,
    ) -> list[list[float]]:
        return [
            [
                round(point[0] * meters_per_pixel, 3) if meters_per_pixel is not None else round(point[0], 3),
                round(point[1] * meters_per_pixel, 3) if meters_per_pixel is not None else round(point[1], 3),
            ]
            for point in polygon
        ]

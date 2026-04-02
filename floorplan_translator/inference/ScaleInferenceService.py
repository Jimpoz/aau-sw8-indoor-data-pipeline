from __future__ import annotations

from ..configuration.TranslatorConfig import TranslatorConfig
from .OcrTextExtractor import OcrTextExtractor
from .TextBlock import TextBlock


class ScaleInferenceService:
    def __init__(self, config: TranslatorConfig, text_extractor: OcrTextExtractor) -> None:
        self.config = config
        self.text_extractor = text_extractor

    def resolve_scale(
        self,
        text_blocks: list[TextBlock],
        image_shape: tuple[int, int],
        override_scale: float | None,
    ) -> tuple[float | None, str]:
        if override_scale is not None:
            return override_scale, "request"

        inferred_scale, source = self.guess_scale_from_text(text_blocks, image_shape)
        if inferred_scale is not None:
            return inferred_scale, source or "ocr-dimension"

        if self.config.scale.use_default_scale_as_fallback and self.config.scale.default_meters_per_pixel is not None:
            return self.config.scale.default_meters_per_pixel, "config-default"

        return None, "unscaled-pixels"

    def guess_scale_from_text(
        self,
        text_blocks: list[TextBlock],
        image_shape: tuple[int, int],
    ) -> tuple[float | None, str | None]:
        if not text_blocks:
            return None, None

        width = image_shape[1]
        height = image_shape[0]
        horizontal_candidates: list[float] = []
        vertical_candidates: list[float] = []
        for block in text_blocks:
            meters = self.text_extractor.parse_dimension_text_to_meters(block.text)
            if meters is None:
                continue
            left, top, right, bottom = block.bbox
            center_x = (left + right) / 2.0
            center_y = (top + bottom) / 2.0
            if center_y < height * 0.15 or center_y > height * 0.85:
                horizontal_candidates.append(meters / max(width, 1))
            if center_x < width * 0.15 or center_x > width * 0.85:
                vertical_candidates.append(meters / max(height, 1))

        candidates = horizontal_candidates + vertical_candidates
        if not candidates:
            return None, None
        return sum(candidates) / len(candidates), "ocr-dimension"

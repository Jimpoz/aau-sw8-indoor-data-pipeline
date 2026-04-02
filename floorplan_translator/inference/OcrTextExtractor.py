from __future__ import annotations

from pathlib import Path
import csv
import io
import shutil
import subprocess
import tempfile

import cv2
import numpy as np

from ..configuration.TranslatorConfig import TranslatorConfig
from ..geometry import point_in_polygon
from .TextBlock import TextBlock


class OcrTextExtractor:
    def __init__(self, config: TranslatorConfig) -> None:
        self.config = config

    def extract_text_blocks(self, image: np.ndarray) -> list[TextBlock]:
        if self.config.text.ocr_engine.lower() != "tesseract":
            return []
        if not self._tesseract_available():
            return []

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
            temp_path = handle.name
        try:
            cv2.imwrite(temp_path, image)
            result = subprocess.run(
                [
                    self.config.text.tesseract_binary,
                    temp_path,
                    "stdout",
                    "--psm",
                    "11",
                    "tsv",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.SubprocessError:
            return []
        finally:
            Path(temp_path).unlink(missing_ok=True)

        rows = csv.DictReader(io.StringIO(result.stdout), delimiter="\t")
        blocks: list[TextBlock] = []
        for row in rows:
            text = (row.get("text") or "").strip()
            if not text:
                continue
            try:
                confidence = float(row.get("conf") or "-1")
                if confidence < 0:
                    continue
                left = int(row.get("left") or 0)
                top = int(row.get("top") or 0)
                width = int(row.get("width") or 0)
                height = int(row.get("height") or 0)
            except ValueError:
                continue
            blocks.append(
                TextBlock(
                    text=text,
                    confidence=confidence,
                    bbox=(left, top, left + width, top + height),
                )
            )
        return blocks

    def label_space(
        self,
        polygon: list[list[float]],
        text_blocks: list[TextBlock],
        fallback_prefix: str,
        index: int,
        min_confidence: float,
    ) -> str:
        matches: list[tuple[float, str]] = []
        for block in text_blocks:
            if block.confidence < min_confidence:
                continue
            left, top, right, bottom = block.bbox
            center_x = (left + right) / 2.0
            center_y = (top + bottom) / 2.0
            if point_in_polygon(center_x, center_y, polygon):
                matches.append((block.confidence, block.text))

        if matches:
            matches.sort(reverse=True)
            return " ".join(text for _, text in matches[:3]).strip()
        return f"{fallback_prefix} {index}"

    @staticmethod
    def parse_dimension_text_to_meters(raw_text: str) -> float | None:
        text = raw_text.strip().replace(" ", "")
        import re

        feet_inches = re.fullmatch(r"(?P<feet>\d+)'(?P<inches>\d{1,2})?\"?", text)
        if feet_inches:
            feet = int(feet_inches.group("feet"))
            inches = int(feet_inches.group("inches") or "0")
            return feet * 0.3048 + inches * 0.0254

        feet_only = re.fullmatch(r"(?P<feet>\d+(?:\.\d+)?)'", text)
        if feet_only:
            return float(feet_only.group("feet")) * 0.3048

        meters = re.fullmatch(r"(?P<meters>\d+(?:\.\d+)?)m", text.lower())
        if meters:
            return float(meters.group("meters"))

        return None

    def _tesseract_available(self) -> bool:
        return shutil.which(self.config.text.tesseract_binary) is not None

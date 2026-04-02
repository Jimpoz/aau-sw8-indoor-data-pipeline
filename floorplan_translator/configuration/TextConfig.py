from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class TextConfig:
    ocr_engine: str = "none"
    tesseract_binary: str = "tesseract"
    use_pdf_metadata: bool = True
    use_filename: bool = True
    require_campus_name_if_not_inferred: bool = True

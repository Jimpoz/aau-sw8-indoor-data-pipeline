from __future__ import annotations

from pathlib import Path
import mimetypes
import subprocess
import tempfile
from typing import Any

import cv2
import numpy as np

from .LoadedPage import LoadedPage
from .SourceDocument import SourceDocument
from ..configuration.TranslatorConfig import TranslatorConfig


class SourceDocumentLoader:
    def __init__(self, config: TranslatorConfig) -> None:
        self.config = config

    def load_from_path(self, source_path: str) -> SourceDocument:
        path = Path(source_path).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"Source path {source_path!r} does not exist.")

        source_type = self._detect_source_type(path.name)
        if source_type == "pdf":
            metadata = self._pdf_metadata(str(path))
            pages = [
                LoadedPage(page_index=index, image=image, metadata={"page_number": index + 1})
                for index, image in enumerate(self._render_pdf_pages(str(path)))
            ]
        else:
            image = cv2.imread(str(path), cv2.IMREAD_COLOR)
            if image is None:
                raise ValueError(f"Could not decode image source {source_path!r}.")
            metadata = {"source_path": str(path)}
            pages = [LoadedPage(page_index=0, image=image, metadata={"page_number": 1})]

        return SourceDocument(
            source_name=path.name,
            source_type=source_type,
            source_path=str(path),
            metadata=metadata,
            pages=pages,
        )

    def load_from_bytes(self, payload: bytes, source_name: str) -> SourceDocument:
        if self._detect_source_type(source_name) == "pdf":
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
                handle.write(payload)
                temp_path = handle.name
            try:
                document = self.load_from_path(temp_path)
                document.source_name = source_name
                document.source_path = None
                return document
            finally:
                Path(temp_path).unlink(missing_ok=True)

        image = self._decode_image_bytes(payload, source_name)
        return SourceDocument(
            source_name=source_name,
            source_type="image",
            source_path=None,
            metadata={"source_name": source_name, "detected_image_type": mimetypes.guess_type(source_name)[0]},
            pages=[LoadedPage(page_index=0, image=image, metadata={"page_number": 1})],
        )

    @staticmethod
    def _run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(command, check=True, capture_output=True, text=True)

    @staticmethod
    def _parse_pdfinfo_output(raw_output: str) -> dict[str, str]:
        metadata: dict[str, str] = {}
        for line in raw_output.splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            metadata[key.strip().lower().replace(" ", "_")] = value.strip()
        return metadata

    def _pdf_page_count(self, source_path: str) -> int:
        info = self._parse_pdfinfo_output(self._run_command(["pdfinfo", source_path]).stdout)
        return int(info.get("pages", "1"))

    def _pdf_metadata(self, source_path: str) -> dict[str, Any]:
        info = self._parse_pdfinfo_output(self._run_command(["pdfinfo", source_path]).stdout)
        metadata: dict[str, Any] = dict(info)
        metadata["pdf_path"] = source_path
        return metadata

    def _render_pdf_pages(self, source_path: str) -> list[np.ndarray]:
        pages: list[np.ndarray] = []
        page_count = self._pdf_page_count(source_path)
        with tempfile.TemporaryDirectory(prefix="floorplan_render_") as temp_dir:
            for page_number in range(1, page_count + 1):
                output_prefix = str(Path(temp_dir) / f"page_{page_number}")
                self._run_command(
                    [
                        "pdftoppm",
                        "-png",
                        "-r",
                        str(self.config.input.pdf_dpi),
                        "-f",
                        str(page_number),
                        "-l",
                        str(page_number),
                        "-singlefile",
                        source_path,
                        output_prefix,
                    ]
                )
                image = cv2.imread(f"{output_prefix}.png", cv2.IMREAD_COLOR)
                if image is None:
                    raise ValueError(f"Could not render page {page_number} from {source_path}.")
                pages.append(image)
        return pages

    @staticmethod
    def _decode_image_bytes(payload: bytes, source_name: str) -> np.ndarray:
        frame = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError(f"Uploaded source {source_name!r} is not a supported image.")
        return frame

    @staticmethod
    def _detect_source_type(source_name: str) -> str:
        return "pdf" if source_name.lower().endswith(".pdf") else "image"

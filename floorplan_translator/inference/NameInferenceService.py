from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from ..configuration.TranslatorConfig import TranslatorConfig
from ..geometry import normalize_name


GENERIC_NAME_WORDS = {
    "master",
    "revision",
    "plan",
    "floor",
    "sheet",
    "layout",
    "drawing",
    "model",
    "page",
    "copy",
}

LOW_SIGNAL_NAME_WORDS = {
    "acrobat",
    "distiller",
    "windows",
    "adobe",
    "pscript",
    "creator",
    "producer",
    "author",
    "litc",
}

FLOOR_PATTERNS = [
    (re.compile(r"\bground\s+floor\b", re.IGNORECASE), 0, "Ground Floor"),
    (re.compile(r"\bstue\b", re.IGNORECASE), 0, "Ground Floor"),
    (re.compile(r"\bfirst\s+floor\b", re.IGNORECASE), 1, "First Floor"),
    (re.compile(r"\bsecond\s+floor\b", re.IGNORECASE), 2, "Second Floor"),
    (re.compile(r"\bthird\s+floor\b", re.IGNORECASE), 3, "Third Floor"),
    (re.compile(r"\b(\d+)\s*(?:st|nd|rd|th)?\s+floor\b", re.IGNORECASE), None, None),
    (re.compile(r"\b(\d+)\s*[\._-]?\s*sal\b", re.IGNORECASE), None, None),
    (re.compile(r"\bf(\d+)\b", re.IGNORECASE), None, None),
]


class NameInferenceService:
    def __init__(self, config: TranslatorConfig) -> None:
        self.config = config
        self.preferred_metadata_keys = ("title", "subject", "pdf_path")

    def infer_campus_name(
        self,
        source_name: str,
        metadata: dict[str, Any],
        explicit_name: str | None,
    ) -> tuple[str | None, list[str]]:
        warnings: list[str] = []
        if explicit_name:
            return explicit_name, warnings

        candidates = self.collect_name_candidates(source_name, metadata)
        ranked = sorted(candidates, key=self._segment_score, reverse=True)
        if ranked and self._segment_score(ranked[0]) > 10.0:
            return ranked[0], warnings

        if self.config.text.require_campus_name_if_not_inferred:
            warnings.append("Campus name could not be inferred from the source. Provide campus_name explicitly.")
            return None, warnings

        return "Imported Campus", warnings

    def infer_building_name(
        self,
        campus_name: str | None,
        source_name: str,
        metadata: dict[str, Any],
        explicit_name: str | None,
    ) -> str:
        if explicit_name:
            return explicit_name
        candidates = self.collect_name_candidates(source_name, metadata)
        ranked = sorted(candidates, key=self._segment_score, reverse=True)
        if ranked and self._segment_score(ranked[0]) > 10.0:
            return ranked[0]
        if campus_name:
            return campus_name
        return self.config.project.default_building_name

    def infer_floor(
        self,
        source_name: str,
        metadata: dict[str, Any],
        page_index: int,
        explicit_name: str | None = None,
        explicit_index: int | None = None,
    ) -> tuple[int, str]:
        if explicit_index is not None:
            return explicit_index, explicit_name or f"Floor {explicit_index}"
        if explicit_name:
            return page_index, explicit_name

        for candidate in self.build_floor_candidates(source_name, metadata, page_index):
            for pattern, fixed_index, fixed_name in FLOOR_PATTERNS:
                match = pattern.search(candidate)
                if match is None:
                    continue
                if fixed_index is not None:
                    return fixed_index, fixed_name or f"Floor {fixed_index}"
                parsed_index = int(match.group(1))
                return parsed_index, f"{parsed_index}. Sal" if "sal" in candidate.lower() else f"Floor {parsed_index}"
        return page_index, f"Floor {page_index}"

    def build_floor_candidates(self, source_name: str, metadata: dict[str, Any], page_index: int) -> list[str]:
        candidates = self.collect_name_candidates(source_name, metadata)
        candidates.append(f"page-{page_index + 1}")
        return candidates

    def collect_name_candidates(self, source_name: str, metadata: dict[str, Any]) -> list[str]:
        candidates: list[str] = []
        if self.config.text.use_filename and source_name:
            candidates.extend(Path(source_name).stem.split("/"))
            candidates.append(Path(source_name).stem)

        if self.config.text.use_pdf_metadata:
            for key, value in metadata.items():
                if not isinstance(value, str):
                    continue
                if key not in self.preferred_metadata_keys:
                    continue
                candidates.extend(re.split(r"[\\/]", value))

        cleaned: list[str] = []
        for candidate in candidates:
            normalized = self._clean_candidate(candidate)
            if normalized and normalized not in cleaned:
                cleaned.append(normalized)
        return cleaned

    @staticmethod
    def slug_id(value: str, prefix: str | None = None) -> str:
        slug = normalize_name(value)
        return f"{prefix}-{slug}" if prefix else slug

    @staticmethod
    def _clean_candidate(candidate: str) -> str:
        cleaned = re.sub(r"[_\-]+", " ", candidate)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned

    @staticmethod
    def _segment_score(candidate: str) -> float:
        lowered = candidate.lower()
        if not re.search(r"[a-zA-Z]", candidate):
            return -100.0
        score = len(candidate)
        if any(word in lowered for word in ("vej", "vange", "gate", "street", "road", "avenue", "campus")):
            score += 20.0
        if re.search(r"\d", candidate):
            score += 10.0
        if lowered in GENERIC_NAME_WORDS:
            score -= 40.0
        if any(word in lowered for word in LOW_SIGNAL_NAME_WORDS):
            score -= 60.0
        if lowered.startswith("page "):
            score -= 20.0
        return score

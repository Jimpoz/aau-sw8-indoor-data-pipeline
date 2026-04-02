from __future__ import annotations

from pathlib import Path
from typing import Any

from floorplan_translator.analysis.FloorPlanTranslator import FloorPlanTranslator
from floorplan_translator.analysis.TranslatorOverrides import TranslatorOverrides
from floorplan_translator.config import load_translator_config
from floorplan_translator.configuration.TranslatorConfig import TranslatorConfig


class FloorPlanTranslatorService:
    """Service facade that aggregates the floor-plan translator modules."""

    def __init__(
        self,
        config: TranslatorConfig | None = None,
        *,
        config_path: str | Path | None = None,
        config_overrides: dict[str, Any] | None = None,
    ) -> None:
        self.config = config or load_translator_config(config_path=config_path, overrides=config_overrides)
        self.translator = FloorPlanTranslator(self.config)

    @classmethod
    def from_config(
        cls,
        config_path: str | Path | None = None,
        config_overrides: dict[str, Any] | None = None,
    ) -> "FloorPlanTranslatorService":
        return cls(config_path=config_path, config_overrides=config_overrides)

    def defaults(self) -> dict[str, Any]:
        return self.config.to_dict()

    def translate_path(
        self,
        source_path: str,
        overrides: TranslatorOverrides | None = None,
    ) -> dict[str, Any]:
        return self.translator.translate_path(source_path, overrides=overrides)

    def translate_bytes(
        self,
        payload: bytes,
        source_name: str,
        overrides: TranslatorOverrides | None = None,
    ) -> dict[str, Any]:
        return self.translator.translate_bytes(payload, source_name=source_name, overrides=overrides)

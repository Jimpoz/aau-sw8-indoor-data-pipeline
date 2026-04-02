from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class ExportConfig:
    schema_version: str = "1.0"
    emit_backend_map: bool = True
    emit_rich_translation: bool = True
    create_exterior_space: bool = False

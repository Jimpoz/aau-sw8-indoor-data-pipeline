from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .DebugConfig import DebugConfig
from .ExportConfig import ExportConfig
from .GeometryConfig import GeometryConfig
from .InputConfig import InputConfig
from .ProjectConfig import ProjectConfig
from .ScaleConfig import ScaleConfig
from .TextConfig import TextConfig


@dataclass(slots=True)
class TranslatorConfig:
    project: ProjectConfig = field(default_factory=ProjectConfig)
    input: InputConfig = field(default_factory=InputConfig)
    geometry: GeometryConfig = field(default_factory=GeometryConfig)
    text: TextConfig = field(default_factory=TextConfig)
    scale: ScaleConfig = field(default_factory=ScaleConfig)
    export: ExportConfig = field(default_factory=ExportConfig)
    debug: DebugConfig = field(default_factory=DebugConfig)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TranslatorConfig":
        return cls(
            project=ProjectConfig(**data.get("project", {})),
            input=InputConfig(**data.get("input", {})),
            geometry=GeometryConfig(**data.get("geometry", {})),
            text=TextConfig(**data.get("text", {})),
            scale=ScaleConfig(**data.get("scale", {})),
            export=ExportConfig(**data.get("export", {})),
            debug=DebugConfig(**data.get("debug", {})),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

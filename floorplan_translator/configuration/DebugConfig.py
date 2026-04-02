from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class DebugConfig:
    save_artifacts: bool = False
    embed_debug_images: bool = False

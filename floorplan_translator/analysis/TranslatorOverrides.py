from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class TranslatorOverrides:
    mode: str = "normal"
    campus_name: str | None = None
    campus_id: str | None = None
    building_name: str | None = None
    building_id: str | None = None
    floor_name: str | None = None
    floor_index: int | None = None
    scale_meters_per_pixel: float | None = None
    debug: bool = False
    debug_dir: str | None = None
    include_debug_images: bool = False
    preview_pipeline_images: bool = False
    import_to_backend: bool = False
    backend_import_base_url: str | None = None

    def apply_mode_defaults(self) -> "TranslatorOverrides":
        normalized_mode = (self.mode or "normal").strip().lower()
        if normalized_mode not in {"normal", "debug"}:
            raise ValueError(f"Unsupported mode {self.mode!r}. Use 'normal' or 'debug'.")
        self.mode = normalized_mode
        if normalized_mode == "debug":
            self.debug = True
            self.include_debug_images = True
            self.preview_pipeline_images = True
        return self

"""Workspace and local file helpers for the Floor Data Tool."""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import floor_import_builder


ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = ROOT / "frontend"
OUTPUTS_DIR = ROOT / "outputs"
UI_HTML = FRONTEND_DIR / "index.html"

TEXT_EXTENSIONS = {".json", ".csv"}
FLOOR_PLAN_EXTENSIONS = {".png", ".jpg", ".jpeg", ".pdf"}
SOURCE_EXTENSIONS = {*FLOOR_PLAN_EXTENSIONS, ".json", ".csv", ".svg"}
SKIPPED_SCAN_DIRS = {".git", ".venv", "proj", "__pycache__", ".cache", "node_modules"}


def resolve_path(value: str | None) -> Path:
    if not value:
        raise ValueError("Path is required")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    return path


def relative_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def output_path(value: str | None, *, fallback_name: str) -> Path:
    if value:
        return resolve_path(value)
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    return OUTPUTS_DIR / fallback_name


def safe_upload_name(value: str) -> str:
    path = Path(value or "floor_plan").name
    suffix = Path(path).suffix.lower()
    stem = floor_import_builder.stable_id(Path(path).stem).replace("-", "_")
    if suffix not in FLOOR_PLAN_EXTENSIONS:
        raise ValueError("Choose a PNG, JPG, or PDF floor plan")
    return f"{stem}{suffix}"


def list_local_files() -> dict[str, list[dict[str, str]]]:
    files: list[Path] = []
    for directory in (OUTPUTS_DIR, ROOT):
        if not directory.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(directory):
            dirnames[:] = [name for name in dirnames if name not in SKIPPED_SCAN_DIRS]
            folder = Path(dirpath)
            for filename in filenames:
                path = folder / filename
                if path.suffix.lower() in SOURCE_EXTENSIONS:
                    files.append(path)

    seen: set[Path] = set()
    unique: list[dict[str, str]] = []
    for path in sorted(files, key=relative_path):
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append({"path": relative_path(path), "name": path.name, "type": path.suffix.lower()})
    return {
        "sources": unique,
        "floor_plans": [item for item in unique if item["type"] in FLOOR_PLAN_EXTENSIONS],
        "details": [item for item in unique if item["type"] in TEXT_EXTENSIONS],
        "save_as": [item for item in unique if item["path"].startswith("outputs/")],
    }


@dataclass(frozen=True)
class AppWorkspace:
    root: Path = ROOT
    outputs_dir: Path = OUTPUTS_DIR
    ui_html: Path = UI_HTML

    def html(self) -> str:
        return self.ui_html.read_text(encoding="utf-8")

    def resolve(self, value: str | None) -> Path:
        return resolve_path(value)

    def output(self, value: str | None, *, fallback_name: str) -> Path:
        return output_path(value, fallback_name=fallback_name)

    def relative(self, path: Path) -> str:
        return relative_path(path)

    def list_files(self) -> dict[str, list[dict[str, str]]]:
        return list_local_files()

    def write_json(self, path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def upload_floor_plan(self, filename: str, content_base64: str) -> dict[str, str]:
        name = safe_upload_name(filename)
        if not content_base64:
            raise ValueError("No file content was received")
        upload_dir = self.outputs_dir / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        path = upload_dir / name
        path.write_bytes(base64.b64decode(content_base64))
        return {"path": self.relative(path), "name": path.name, "type": path.suffix.lower()}

    def local_file(self, query: str) -> tuple[bytes, str, str]:
        values = parse_qs(query)
        path_value = (values.get("path") or [""])[0]
        path = self.resolve(path_value)
        if not path.is_file():
            raise ValueError("File was not found")
        content_types = {
            ".pdf": "application/pdf",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".svg": "image/svg+xml; charset=utf-8",
        }
        return path.read_bytes(), content_types.get(path.suffix.lower(), "application/octet-stream"), path.name

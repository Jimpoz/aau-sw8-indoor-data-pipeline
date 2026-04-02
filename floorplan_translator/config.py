from __future__ import annotations

import configparser
from pathlib import Path
from types import UnionType
from typing import Any, Union, get_args, get_origin, get_type_hints
import tomllib

from .configuration.DebugConfig import DebugConfig
from .configuration.ExportConfig import ExportConfig
from .configuration.GeometryConfig import GeometryConfig
from .configuration.InputConfig import InputConfig
from .configuration.ProjectConfig import ProjectConfig
from .configuration.ScaleConfig import ScaleConfig
from .configuration.TextConfig import TextConfig
from .configuration.TranslatorConfig import TranslatorConfig

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = ROOT_DIR / "config" / "defaults.cfg"
CONFIG_SECTION_TYPES = {
    "project": ProjectConfig,
    "input": InputConfig,
    "geometry": GeometryConfig,
    "text": TextConfig,
    "scale": ScaleConfig,
    "export": ExportConfig,
    "debug": DebugConfig,
}


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _load_toml_file(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _is_optional_type(value_type: Any) -> bool:
    origin = get_origin(value_type)
    return origin in (UnionType, Union) and type(None) in get_args(value_type)


def _unwrap_optional_type(value_type: Any) -> Any:
    if not _is_optional_type(value_type):
        return value_type
    return next(arg for arg in get_args(value_type) if arg is not type(None))


def _parse_bool(raw_value: str) -> bool:
    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Unsupported boolean value {raw_value!r}.")


def _strip_wrapping_quotes(raw_value: str) -> str:
    stripped = raw_value.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {'"', "'"}:
        return stripped[1:-1]
    return stripped


def _coerce_cfg_value(raw_value: str, value_type: Any) -> Any:
    if _is_optional_type(value_type):
        normalized = raw_value.strip().lower()
        if normalized in {"", "none", "null"}:
            return None
        value_type = _unwrap_optional_type(value_type)

    if value_type is bool:
        return _parse_bool(raw_value)
    if value_type is int:
        return int(raw_value)
    if value_type is float:
        return float(raw_value)
    return _strip_wrapping_quotes(raw_value)


def _load_cfg_file(path: Path) -> dict[str, Any]:
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    with path.open("r", encoding="utf-8") as handle:
        parser.read_file(handle)

    payload: dict[str, Any] = {}
    for section_name, section_type in CONFIG_SECTION_TYPES.items():
        section_values: dict[str, Any] = {}
        if parser.has_section(section_name):
            type_hints = get_type_hints(section_type)
            for key, raw_value in parser.items(section_name):
                section_values[key] = _coerce_cfg_value(raw_value, type_hints.get(key, str))
        payload[section_name] = section_values
    return payload


def _load_config_file(path: Path) -> dict[str, Any]:
    suffix = path.suffix.lower()
    if suffix in {".cfg", ".ini"}:
        return _load_cfg_file(path)
    if suffix == ".toml":
        return _load_toml_file(path)
    raise ValueError(f"Unsupported config format for {path}. Use .cfg, .ini, or .toml.")


def load_translator_config(
    config_path: str | Path | None = None,
    overrides: dict[str, Any] | None = None,
) -> TranslatorConfig:
    merged = _load_config_file(DEFAULT_CONFIG_PATH)
    if config_path is not None:
        merged = deep_merge(merged, _load_config_file(Path(config_path)))
    if overrides:
        merged = deep_merge(merged, overrides)
    return TranslatorConfig.from_dict(merged)


__all__ = [
    "DEFAULT_CONFIG_PATH",
    "ROOT_DIR",
    "DebugConfig",
    "ExportConfig",
    "GeometryConfig",
    "InputConfig",
    "ProjectConfig",
    "ScaleConfig",
    "TextConfig",
    "TranslatorConfig",
    "deep_merge",
    "load_translator_config",
]

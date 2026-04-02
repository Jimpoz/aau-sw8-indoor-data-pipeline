from __future__ import annotations

from pathlib import Path
import json
import shutil
import tempfile
from typing import Any
import zipfile

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .analysis.FloorPlanTranslator import FloorPlanTranslator
from .analysis.TranslatorOverrides import TranslatorOverrides
from .config import load_translator_config
from .geometry import normalize_name


def _translator_from_request(config_path: str | None, config_overrides: dict[str, Any]) -> FloorPlanTranslator:
    config = load_translator_config(config_path=config_path, overrides=config_overrides)
    return FloorPlanTranslator(config)


def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    if stripped.lower() in {"", "string", "none", "null"}:
        return None
    return stripped


def _parse_config_overrides_json(config_overrides_json: str | None) -> dict[str, Any]:
    normalized = _normalize_optional_text(config_overrides_json)
    if normalized is None:
        return {}
    if normalized == "string":
        return {}
    return json.loads(normalized)


def _debug_overrides(
    *,
    campus_name: str | None = None,
    building_name: str | None = None,
    floor_name: str | None = None,
    floor_index: int | None = None,
    scale_meters_per_pixel: float | None = None,
    debug_dir: str | None = None,
) -> TranslatorOverrides:
    return TranslatorOverrides(
        mode="debug",
        campus_name=campus_name,
        building_name=building_name,
        floor_name=floor_name,
        floor_index=floor_index,
        scale_meters_per_pixel=scale_meters_per_pixel,
        debug_dir=debug_dir,
        import_to_backend=False,
        backend_import_base_url=None,
    )


def _download_debug_overrides(
    *,
    debug_dir: str,
    campus_name: str | None = None,
    building_name: str | None = None,
    floor_name: str | None = None,
    floor_index: int | None = None,
    scale_meters_per_pixel: float | None = None,
) -> TranslatorOverrides:
    return TranslatorOverrides(
        mode="normal",
        campus_name=campus_name,
        building_name=building_name,
        floor_name=floor_name,
        floor_index=floor_index,
        scale_meters_per_pixel=scale_meters_per_pixel,
        debug=True,
        debug_dir=debug_dir,
        include_debug_images=False,
        preview_pipeline_images=False,
        import_to_backend=False,
        backend_import_base_url=None,
    )


def _cleanup_directory(path: str) -> None:
    shutil.rmtree(path, ignore_errors=True)


def _build_debug_bundle(
    result: dict[str, Any],
    *,
    source_name: str,
    bundle_root: Path,
) -> tuple[Path, str]:
    safe_name = normalize_name(Path(source_name).stem) or "debug_bundle"
    translation_path = bundle_root / "translation.json"
    translation_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    archive_path = bundle_root / f"{safe_name}_debug_bundle.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(translation_path, arcname="translation.json")
        artifacts_dir = bundle_root / "artifacts"
        if artifacts_dir.exists():
            for file_path in sorted(artifacts_dir.rglob("*")):
                if file_path.is_file():
                    archive.write(file_path, arcname=str(file_path.relative_to(bundle_root)))
    return archive_path, archive_path.name


def _download_response_from_result(
    result: dict[str, Any],
    *,
    source_name: str,
    temp_root: str,
    background_tasks: BackgroundTasks,
) -> FileResponse:
    archive_path, archive_name = _build_debug_bundle(
        result,
        source_name=source_name,
        bundle_root=Path(temp_root),
    )
    background_tasks.add_task(_cleanup_directory, temp_root)
    return FileResponse(
        path=archive_path,
        filename=archive_name,
        media_type="application/zip",
        background=background_tasks,
    )


app = FastAPI(
    title="Floor Plan Translator",
    version="0.1.0",
    description=(
        "Translate PDF or image floor plans into the aau-sw8-spatial-backend map schema, "
        "with optional backend import, debug artifacts, and full pipeline image previews."
    ),
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/config/defaults")
def config_defaults() -> dict[str, Any]:
    return load_translator_config().to_dict()


@app.post("/translate/upload")
async def translate_upload(
    source: UploadFile = File(...),
    config_path: str = Form("", description="Optional path to a custom CFG, INI, or legacy TOML config file."),
    config_overrides_json: str = Form("", description="JSON object with runtime config overrides."),
    campus_name: str = Form("", description="Explicit campus name override."),
    campus_id: str = Form("", description="Explicit campus identifier override."),
    building_name: str = Form("", description="Explicit building name override."),
    building_id: str = Form("", description="Explicit building identifier override."),
    floor_name: str = Form("", description="Explicit floor label override."),
    floor_index: int | None = Form(None, description="Explicit floor index override."),
    scale_meters_per_pixel: float | None = Form(None, description="Manual meters-per-pixel override."),
    import_to_backend: bool = Form(False, description="Send the generated map import payload to the backend import endpoint."),
    backend_import_base_url: str = Form("", description="Optional override for the backend import API base URL."),
) -> dict[str, Any]:
    try:
        payload = await source.read()
        config_overrides = _parse_config_overrides_json(config_overrides_json)
        translator = _translator_from_request(_normalize_optional_text(config_path), config_overrides)
        return translator.translate_bytes(
            payload,
            source_name=source.filename or "uploaded_source",
            overrides=TranslatorOverrides(
                mode="normal",
                campus_name=_normalize_optional_text(campus_name),
                campus_id=_normalize_optional_text(campus_id),
                building_name=_normalize_optional_text(building_name),
                building_id=_normalize_optional_text(building_id),
                floor_name=_normalize_optional_text(floor_name),
                floor_index=floor_index,
                scale_meters_per_pixel=scale_meters_per_pixel,
                import_to_backend=import_to_backend,
                backend_import_base_url=_normalize_optional_text(backend_import_base_url),
            ),
        )
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"config_overrides_json is not valid JSON: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Translation failed: {exc}") from exc
    finally:
        await source.close()


@app.post("/debug/upload")
async def debug_translate_upload(
    source: UploadFile = File(...),
    config_path: str = Form("", description="Optional path to a custom CFG, INI, or legacy TOML config file."),
    config_overrides_json: str = Form("", description="JSON object with runtime config overrides."),
    campus_name: str = Form("", description="Optional campus name override used during debug translation."),
    building_name: str = Form("", description="Optional building name override used during debug translation."),
    floor_name: str = Form("", description="Optional floor label override used during debug translation."),
    floor_index: int | None = Form(None, description="Optional floor index override used during debug translation."),
    scale_meters_per_pixel: float | None = Form(None, description="Optional manual meters-per-pixel override."),
    debug_dir: str = Form(
        "",
        description="Optional directory where the debug PNG stages should be written in addition to being returned in the response.",
    ),
) -> dict[str, Any]:
    try:
        payload = await source.read()
        config_overrides = _parse_config_overrides_json(config_overrides_json)
        translator = _translator_from_request(_normalize_optional_text(config_path), config_overrides)
        return translator.translate_bytes(
            payload,
            source_name=source.filename or "uploaded_source",
            overrides=_debug_overrides(
                campus_name=_normalize_optional_text(campus_name),
                building_name=_normalize_optional_text(building_name),
                floor_name=_normalize_optional_text(floor_name),
                floor_index=floor_index,
                scale_meters_per_pixel=scale_meters_per_pixel,
                debug_dir=_normalize_optional_text(debug_dir),
            ),
        )
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"config_overrides_json is not valid JSON: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Debug translation failed: {exc}") from exc
    finally:
        await source.close()


@app.post("/debug/upload/download")
async def debug_translate_upload_download(
    background_tasks: BackgroundTasks,
    source: UploadFile = File(...),
    config_path: str = Form("", description="Optional path to a custom CFG, INI, or legacy TOML config file."),
    config_overrides_json: str = Form("", description="JSON object with runtime config overrides."),
    campus_name: str = Form("", description="Optional campus name override used during debug translation."),
    building_name: str = Form("", description="Optional building name override used during debug translation."),
    floor_name: str = Form("", description="Optional floor label override used during debug translation."),
    floor_index: int | None = Form(None, description="Optional floor index override used during debug translation."),
    scale_meters_per_pixel: float | None = Form(None, description="Optional manual meters-per-pixel override."),
) -> FileResponse:
    try:
        payload = await source.read()
        config_overrides = _parse_config_overrides_json(config_overrides_json)
        translator = _translator_from_request(_normalize_optional_text(config_path), config_overrides)
        temp_root = tempfile.mkdtemp(prefix="floorplan_debug_bundle_")
        result = translator.translate_bytes(
            payload,
            source_name=source.filename or "uploaded_source",
            overrides=_download_debug_overrides(
                debug_dir=str(Path(temp_root) / "artifacts"),
                campus_name=_normalize_optional_text(campus_name),
                building_name=_normalize_optional_text(building_name),
                floor_name=_normalize_optional_text(floor_name),
                floor_index=floor_index,
                scale_meters_per_pixel=scale_meters_per_pixel,
            ),
        )
        return _download_response_from_result(
            result,
            source_name=source.filename or "uploaded_source",
            temp_root=temp_root,
            background_tasks=background_tasks,
        )
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"config_overrides_json is not valid JSON: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Debug download failed: {exc}") from exc
    finally:
        await source.close()

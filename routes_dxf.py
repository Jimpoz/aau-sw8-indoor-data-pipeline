"""HTTP route for the DXF / DWG -> MapImportSchema conversion."""
from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from dxf import convert as dxf_convert, normalize_bytes, parse_layer_mapping


router = APIRouter(prefix="/api/dxf", tags=["dxf"])


@router.post("/parse")
async def parse_dxf(
    file: UploadFile = File(...),
    campus_id: str = Form(...),
    campus_name: str = Form(...),
    building_id: str = Form(...),
    building_name: str = Form(...),
    floor_id: str = Form(...),
    floor_index: int = Form(0),
    floor_display_name: str = Form("Ground"),
    organization_id: Optional[str] = Form(None),
    organization_name: Optional[str] = Form(None),
    organization_entity_type: str = Form("UNIVERSITY"),
    organization_description: Optional[str] = Form(None),
    campus_description: Optional[str] = Form(None),
    building_short_name: Optional[str] = Form(None),
    origin_bearing: float = Form(0.0),
    layer_mapping: Optional[str] = Form(None),
) -> dict:
    """Normalize + convert an uploaded DXF (or DWG) into a
    MapImportSchema-shaped dict + a summary block. Returns
    ``{"schema": ..., "summary": ...}``. All heavy work runs on a
    worker thread so the event loop stays unblocked."""
    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=422, detail="Uploaded file is empty.")

    try:
        overrides = parse_layer_mapping(layer_mapping)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    def _run_pipeline() -> tuple[dict, dict]:
        normalized = normalize_bytes(file_bytes, file.filename or "upload.dxf")
        return dxf_convert(
            normalized,
            organization_id=organization_id,
            organization_name=organization_name,
            organization_entity_type=organization_entity_type,
            organization_description=organization_description,
            campus_id=campus_id,
            campus_name=campus_name,
            campus_description=campus_description or building_name or None,
            building_id=building_id,
            building_name=building_name,
            building_short_name=building_short_name or (
                building_name.split()[0] if building_name else None
            ),
            floor_id=floor_id,
            floor_index=floor_index,
            floor_display_name=floor_display_name,
            origin_bearing=origin_bearing,
            layer_mapping=overrides,
        )

    try:
        schema, summary = await asyncio.to_thread(_run_pipeline)
    except FileNotFoundError as exc:
        # No DWG converter available
        raise HTTPException(status_code=415, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"DXF parse failed: {exc}")

    return {"schema": schema, "summary": summary}

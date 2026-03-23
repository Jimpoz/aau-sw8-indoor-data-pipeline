from __future__ import annotations

import os
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile

from db import close_neo4j, initialize_neo4j_schema
from room_summary.RoomImageInput import RoomImageInput
from room_summary.service import RoomSummaryMiddleware
from routes_pathfinding import router as pathfinding_router


def _default_model_config_path() -> Path:
    configured_path = os.getenv("MODEL_CONFIG_PATH")
    if configured_path:
        return Path(configured_path)

    return Path("modelConfig.cfg")


def _default_class_config_path() -> Path:
    configured_path = os.getenv("CLASS_CONFIG_PATH")
    if configured_path:
        return Path(configured_path)

    for candidate in ("classConfig.cfg", "classConf.cfg"):
        candidate_path = Path(candidate)
        if candidate_path.exists():
            return candidate_path

    return Path("classConfig.cfg")


@lru_cache(maxsize=8)
def get_room_summary_middleware(model_name: str | None = None) -> RoomSummaryMiddleware:
    configured_model_path = os.getenv("YOLO_MODEL_PATH")
    requested_profile = model_name or os.getenv("YOLO_MODEL_PROFILE")
    use_model_config = requested_profile is not None or configured_model_path is None

    return RoomSummaryMiddleware(
        model_path=Path(configured_model_path) if configured_model_path else Path("models/yolo11n.pt"),
        model_config_path=_default_model_config_path() if use_model_config else None,
        model_profile=requested_profile,
        class_config_path=_default_class_config_path(),
        confidence_threshold=float(os.getenv("YOLO_CONFIDENCE_THRESHOLD", "0.25")),
        vector_palette_size=int(os.getenv("VECTOR_PALETTE_SIZE", "8")),
        max_vector_width=int(os.getenv("MAX_VECTOR_WIDTH", "480")),
    )


async def _decode_upload_images(images: list[UploadFile]) -> list[RoomImageInput]:
    if len(images) != 4:
        raise ValueError("Exactly 4 images are required.")

    decoded_images: list[RoomImageInput] = []
    for image_index, image in enumerate(images, start=1):
        payload = await image.read()
        if not payload:
            raise ValueError(f"Uploaded image {image_index} is empty.")

        buffer = np.frombuffer(payload, dtype=np.uint8)
        frame = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError(
                f"Uploaded file {image.filename or f'image_{image_index}'} is not a valid image."
            )

        decoded_images.append(
            RoomImageInput(
                source_name=image.filename or f"image_{image_index}.png",
                frame=frame,
            )
        )

    return decoded_images


async def _run_room_summary(
    images: list[UploadFile],
    model_name: str | None,
) -> dict[str, object]:
    try:
        decoded_images = await _decode_upload_images(images)
        return get_room_summary_middleware(model_name).summarize_images(
            images=decoded_images,
        ).to_dict()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Room summary failed: {exc}") from exc
    finally:
        for image in images:
            await image.close()


async def _run_named_room_summary(
    room_name: str,
    images: list[UploadFile],
    model_name: str | None,
) -> dict[str, object]:
    try:
        decoded_images = await _decode_upload_images(images)
        return get_room_summary_middleware(model_name).summarize_room(
            room_name=room_name,
            images=decoded_images,
        ).to_dict()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Room summary failed: {exc}") from exc
    finally:
        for image in images:
            await image.close()


async def _run_room_object_detection_setup(
    room_name: str,
    images: list[UploadFile],
    model_name: str | None,
) -> dict[str, object]:
    try:
        from db import neo4j_driver

        decoded_images = await _decode_upload_images(images)
        return get_room_summary_middleware(model_name).setup_room_object_detection(
            room_name=room_name,
            images=decoded_images,
            conn=neo4j_driver,
        ).to_dict()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Room object detection setup failed: {exc}",
        ) from exc
    finally:
        for image in images:
            await image.close()


def _get_room_names() -> dict[str, list[str]]:
    try:
        from db import neo4j_driver

        return {
            "names": get_room_summary_middleware().list_room_names(
                conn=neo4j_driver,
            )
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_neo4j_schema()
    yield
    close_neo4j()


app = FastAPI(
    title="Indoor Room Summary API",
    version="0.1.0",
    description=(
        "Upload four room images captured from different directions and receive four "
        "vectorized room summaries plus best-effort object counts."
    ),
    lifespan=lifespan,
)

app.include_router(pathfinding_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/room-summary/rooms")
def get_room_names() -> dict[str, list[str]]:
    return _get_room_names()


@app.post("/api/room-summary")
async def summarize_room(
    images: list[UploadFile] = File(
        ...,
        description="Exactly four room images captured from different directions.",
    ),
    model_name: str | None = Query(
        None,
        description="Optional profile name from modelConfig.cfg, for example nano or small.",
    ),
) -> dict[str, object]:
    return await _run_room_summary(
        images=images,
        model_name=model_name,
    )


@app.post("/api/room-summary/by-room")
async def summarize_named_room(
    room_name: str = Form(
        ...,
        description="Room name for the uploaded image set.",
    ),
    images: list[UploadFile] = File(
        ...,
        description="Exactly four room images captured from different directions.",
    ),
    model_name: str | None = Query(
        None,
        description="Optional profile name from modelConfig.cfg, for example nano or small.",
    ),
) -> dict[str, object]:
    return await _run_named_room_summary(
        room_name=room_name,
        images=images,
        model_name=model_name,
    )


@app.post(
    "/api/room-summary/room-objects/setup",
    summary="Room object detection setup",
)
async def setup_room_object_detection(
    room_name: str = Form(
        ...,
        description="Room name from Neo4j for the uploaded image set.",
    ),
    images: list[UploadFile] = File(
        ...,
        description="Exactly four room images captured from different directions.",
    ),
    model_name: str | None = Query(
        None,
        description="Optional profile name from modelConfig.cfg, for example nano or small.",
    ),
) -> dict[str, object]:
    return await _run_room_object_detection_setup(
        room_name=room_name,
        images=images,
        model_name=model_name,
    )

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Response

from db import neo4j_driver
from pathfinding import PathfindingService

router = APIRouter(prefix="/api/pathfinding", tags=["pathfinding"])
pathfinding_service = PathfindingService(neo4j_driver)


@router.get("/rooms")
def get_room_names_by_floor(floor: int = Query(..., ge=1)) -> dict[str, object]:
    try:
        names = pathfinding_service.list_room_names_by_floor(floor)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"floor": floor, "names": names}


@router.get("/fastest")
def get_fastest_path(
    start: str = Query(..., min_length=1),
    end: str = Query("outside", min_length=1),
) -> dict[str, object]:
    try:
        result = pathfinding_service.find_fastest_path(start, end)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {
        "start": start,
        "end": end,
        "path": result.path,
        "cost": result.cost,
    }


@router.get("/debug/image")
def get_fastest_path_image(
    start: str = Query(..., min_length=1),
    end: str = Query("outside", min_length=1),
) -> Response:
    try:
        result = pathfinding_service.find_fastest_path(start, end)
        svg = pathfinding_service.build_fastest_path_svg(start, end, result)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return Response(content=svg, media_type="image/svg+xml")


@router.get("/debug/map")
def get_fastest_path_map(
    start: str = Query(..., min_length=1),
    end: str = Query("outside", min_length=1),
) -> Response:
    try:
        result = pathfinding_service.find_fastest_path(start, end)
        svg = pathfinding_service.build_fastest_path_map_svg(start, end, result)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return Response(content=svg, media_type="image/svg+xml")

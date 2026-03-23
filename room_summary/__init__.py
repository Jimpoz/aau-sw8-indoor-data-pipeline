from __future__ import annotations

__all__ = [
    "Detection",
    "RoomImageInput",
    "RoomObjectDetectionSetupResult",
    "RoomSummaryOnlyResult",
    "RoomSummaryResult",
    "RoomSummaryMiddleware",
    "ViewSummary",
]


def __getattr__(name: str):
    if name == "Detection":
        from .Detection import Detection

        return Detection
    if name == "RoomImageInput":
        from .RoomImageInput import RoomImageInput

        return RoomImageInput
    if name == "RoomObjectDetectionSetupResult":
        from .RoomObjectDetectionSetupResult import RoomObjectDetectionSetupResult

        return RoomObjectDetectionSetupResult
    if name == "RoomSummaryOnlyResult":
        from .RoomSummaryOnlyResult import RoomSummaryOnlyResult

        return RoomSummaryOnlyResult
    if name == "RoomSummaryResult":
        from .RoomSummaryResult import RoomSummaryResult

        return RoomSummaryResult
    if name == "RoomSummaryMiddleware":
        from .service import RoomSummaryMiddleware

        return RoomSummaryMiddleware
    if name == "ViewSummary":
        from .ViewSummary import ViewSummary

        return ViewSummary
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

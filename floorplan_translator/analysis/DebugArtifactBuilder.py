from __future__ import annotations

from base64 import b64encode
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from ..geometry import normalize_name


class DebugArtifactBuilder:
    def build(
        self,
        enabled: bool,
        include_debug_images: bool,
        debug_dir: str | None,
        document_name: str,
        page_index: int,
        images: dict[str, np.ndarray],
    ) -> dict[str, Any]:
        if not enabled:
            return {}

        payload: dict[str, Any] = {
            "artifacts": {},
            "artifact_order": list(images.keys()),
        }
        target_dir: Path | None = None
        if debug_dir:
            target_dir = Path(debug_dir).expanduser().resolve()
            target_dir.mkdir(parents=True, exist_ok=True)

        for name, image in images.items():
            artifact: dict[str, Any] = {}
            if target_dir is not None:
                artifact_name = f"{normalize_name(Path(document_name).stem)}_p{page_index + 1}_{name}.png"
                artifact_path = target_dir / artifact_name
                cv2.imwrite(str(artifact_path), image)
                artifact["path"] = str(artifact_path)
            if include_debug_images:
                success, buffer = cv2.imencode(".png", image)
                if success:
                    artifact["base64_png"] = b64encode(buffer.tobytes()).decode("ascii")
            payload["artifacts"][name] = artifact
        return payload

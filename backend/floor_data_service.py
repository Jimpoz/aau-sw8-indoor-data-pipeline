"""Application services behind the local floor data HTTP endpoints."""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from typing import Any

from floor_plan_renderer import FloorPlanRenderer, unique_doors
from map_api_client import MapApiClient
from campus_export import MapExportSnapshot
from floor_import_payloads import (
    make_import_payload,
    split_import_payload,
    summarize_details,
    summarize_import_payload,
)
from local_workspace import AppWorkspace
import floor_plan_details_reader
import floor_import_builder


@dataclass(frozen=True)
class FloorDataService:
    workspace: AppWorkspace = AppWorkspace()
    renderer: FloorPlanRenderer = FloorPlanRenderer()

    def health(self) -> dict[str, Any]:
        return {
            "ok": True,
            "root": str(self.workspace.root),
            "tesseract": subprocess.run(
                ["which", "tesseract"],
                check=False,
                capture_output=True,
                text=True,
            ).stdout.strip(),
        }

    def server_options(self, body: dict[str, Any]) -> dict[str, Any]:
        client = MapApiClient.from_body(body)
        errors = []
        organizations: list[dict[str, Any]] = []
        campuses: list[dict[str, Any]] = []
        try:
            organizations = client.get("/organizations")
        except Exception as exc:
            errors.append(f"organizations: {exc}")
        try:
            org_id = body.get("organization_id") or body.get("organizationId")
            path = f"/campuses?organization_id={org_id}" if org_id else "/campuses"
            campuses = client.get(path)
        except Exception as exc:
            errors.append(f"campuses: {exc}")
        return {
            "organizations": organizations or [],
            "campuses": campuses or [],
            "errors": errors,
        }

    def load_campus_export(self, body: dict[str, Any]) -> dict[str, Any]:
        client = MapApiClient.from_body(body)
        campus_id = body.get("campus_id") or body.get("campusId")
        if not campus_id:
            raise ValueError("Campus is required")
        export = client.get(f"/campuses/{campus_id}/export", timeout=60)
        path = self.workspace.outputs_dir / f"{campus_id}_export_latest.json"
        self.workspace.write_json(path, export)
        return {"summary": MapExportSnapshot(export).summary(), "export_path": self.workspace.relative(path)}

    def upload_floor_plan(self, body: dict[str, Any]) -> dict[str, Any]:
        file = self.workspace.upload_floor_plan(
            str(body.get("name") or "floor_plan"),
            str(body.get("content_base64") or body.get("contentBase64") or ""),
        )
        return {"file": file}

    def read_floor_plan(self, body: dict[str, Any]) -> dict[str, Any]:
        import floor_plan_cv_reader

        source_type = body.get("source_type") or body.get("sourceType") or "image"
        source_path = self.workspace.resolve(body.get("source_path") or body.get("sourcePath"))
        output = self.workspace.output(
            body.get("details_output") or body.get("detailsOutput"),
            fallback_name=f"{source_path.stem}_details.json",
        )
        params = body.get("params") or {}
        extractor = floor_plan_cv_reader.FloorDetailsExtractor()

        if source_type == "details":
            details = floor_plan_details_reader.read_lookup(source_path)
        elif source_type == "pdf":
            details = extractor.from_pdf(
                source_path,
                options=floor_plan_cv_reader.PdfCvOptions(
                    dpi=int(params.get("dpi") or 180),
                    min_room_area_m2=float(params.get("min_room_area_m2") or 0.18),
                    room_color_tolerance=int(params.get("room_color_tolerance") or 10),
                    min_door_length_m=float(params.get("min_door_length_m") or 0.25),
                    max_door_length_m=float(params.get("max_door_length_m") or 1.15),
                    max_door_label_distance_m=float(params.get("max_door_label_distance_m") or 4.5),
                    door_room_tolerance_m=float(params.get("door_room_tolerance_m") or 0.75),
                ),
            )
        else:
            details = extractor.from_image(
                source_path,
                building_name=str(body.get("building_name") or body.get("buildingName") or "Image-derived building"),
                floor_name=str(body.get("floor_name") or body.get("floorName") or "Image-derived floor"),
                floor_index=int(body.get("floor_index") or body.get("floorIndex") or 0),
                options=floor_plan_cv_reader.ImageCvOptions(
                    pixels_per_meter=float(params["pixels_per_meter"]) if params.get("pixels_per_meter") else None,
                    scale_bar_meters=float(params["scale_bar_meters"]) if params.get("scale_bar_meters") else None,
                    min_room_area_m2=float(params.get("min_room_area_m2") or 0.18),
                    room_color_tolerance=int(params.get("room_color_tolerance") or 10),
                    min_door_length_m=float(params.get("min_door_length_m") or 0.25),
                    max_door_length_m=float(params.get("max_door_length_m") or 1.15),
                    max_door_label_distance_m=float(params.get("max_door_label_distance_m") or 4.5),
                    door_room_tolerance_m=float(params.get("door_room_tolerance_m") or 0.75),
                    run_ocr=bool(params.get("run_ocr", True)),
                    tesseract_command=str(params.get("tesseract_command") or "tesseract"),
                    ocr_language=str(params.get("ocr_language") or "eng+dan"),
                    ocr_min_confidence=float(params.get("ocr_min_confidence") or 5),
                    ocr_scale=float(params.get("ocr_scale") or 2),
                    ocr_crop_scale=float(params.get("ocr_crop_scale") or 7),
                    door_ocr_radius_m=float(params.get("door_ocr_radius_m") or 2.75),
                    image_room_id_mode=str(params.get("image_room_id_mode") or "sequence"),
                    run_door_crop_ocr=bool(params.get("run_door_crop_ocr", False)),
                ),
            )

        self.workspace.write_json(output, details)
        return {"details": summarize_details(details, output)}

    def prepare_floor_import(self, body: dict[str, Any]) -> dict[str, Any]:
        details_path = self.workspace.resolve(body.get("details_path") or body.get("detailsPath"))
        export_path_value = body.get("export_path") or body.get("exportPath")
        export = (
            floor_import_builder.load_json(self.workspace.resolve(export_path_value))
            if export_path_value
            else None
        )
        output = self.workspace.output(
            body.get("import_output") or body.get("importOutput"),
            fallback_name=f"{details_path.stem}_import.json",
        )
        import_json, summary = make_import_payload(
            details_path=details_path,
            export=export,
            target=body.get("target") or {},
            include_rooms=bool(body.get("include_rooms", True)),
        )
        self.workspace.write_json(output, import_json)
        return {
            "summary": summary,
            "import": summarize_import_payload(import_json, output),
        }

    def push_floor_import(self, body: dict[str, Any]) -> dict[str, Any]:
        client = MapApiClient.from_body(body)
        campus_id = body.get("campus_id") or body.get("campusId")
        if not campus_id:
            raise ValueError("Campus is required")
        import_path = self.workspace.resolve(body.get("import_path") or body.get("importPath"))
        payload = floor_import_builder.load_json(import_path)
        timeout = float(body.get("timeout") or 180)
        payloads = [("full", payload)]
        if bool(body.get("chunked", True)):
            rooms_payload, connections_payload = split_import_payload(payload)
            payloads = [("rooms", rooms_payload), ("connections", connections_payload)]

        steps = []
        for label, item in payloads:
            spaces = [
                space
                for building in item.get("campus", {}).get("buildings", [])
                for floor in building.get("floors", [])
                for space in floor.get("spaces", [])
            ]
            connections = item.get("campus", {}).get("connections") or []
            if not spaces and not connections:
                steps.append({"step": label, "skipped": True, "reason": "empty payload"})
                continue
            started = time.time()
            result = client.post(f"/campuses/{campus_id}/import", item, timeout=timeout)
            steps.append(
                {
                    "step": label,
                    "status": result["status"],
                    "seconds": round(time.time() - started, 2),
                    "sent_spaces": len(spaces),
                    "sent_connections": len(connections),
                    "body": result["body"],
                }
            )
        return {"steps": steps}

    def verify_floor(self, body: dict[str, Any]) -> dict[str, Any]:
        client = MapApiClient.from_body(body)
        campus_id = body.get("campus_id") or body.get("campusId")
        floor_id = body.get("floor_id") or body.get("floorId")
        building_id = body.get("building_id") or body.get("buildingId")
        if not campus_id:
            raise ValueError("Campus is required")
        snapshot = MapExportSnapshot(client.get(f"/campuses/{campus_id}/export", timeout=60))
        floor_summary: dict[str, Any] | None = None

        if building_id and floor_id:
            selection = snapshot.select_floor(building_id=str(building_id), floor_id=str(floor_id))
            spaces = selection.spaces
            ids = {space.get("id") for space in spaces}
            touching = [
                conn
                for conn in snapshot.campus.get("connections", [])
                if conn.get("from_space_id") in ids or conn.get("to_space_id") in ids
            ]
            floor_summary = {
                "export_spaces": len(spaces),
                "export_rooms": sum(1 for space in spaces if not str(space.get("space_type", "")).startswith("DOOR")),
                "export_door_spaces": sum(1 for space in spaces if str(space.get("space_type", "")).startswith("DOOR")),
                "export_connections": len(touching),
                "unique_door_ids": len({conn.get("door_id") for conn in touching if conn.get("door_id")}),
            }
            try:
                display = client.get(f"/floors/{floor_id}/display", timeout=60)
                display_spaces = display if isinstance(display, list) else display.get("spaces", [])
                floor_summary.update(
                    {
                        "display_spaces": len(display_spaces),
                        "display_rooms": sum(
                            1
                            for space in display_spaces
                            if not str(space.get("space_type", "")).startswith("DOOR")
                        ),
                        "display_doors": sum(
                            1
                            for space in display_spaces
                            if str(space.get("space_type", "")).startswith("DOOR")
                        ),
                    }
                )
            except Exception as exc:
                floor_summary["display_error"] = str(exc)

        return {"summary": snapshot.summary(), "floor": floor_summary}

    def make_floor_plan(self, body: dict[str, Any]) -> dict[str, Any]:
        client = MapApiClient.from_body(body)
        campus_id = body.get("campus_id") or body.get("campusId")
        building_id = body.get("building_id") or body.get("buildingId")
        floor_id = body.get("floor_id") or body.get("floorId")
        output_format = str(body.get("format") or "pdf").lower()
        if output_format not in {"pdf", "png", "svg"}:
            raise ValueError("Floor plan format must be pdf, png, or svg")
        if not campus_id:
            raise ValueError("Campus is required")
        if not building_id or not floor_id:
            raise ValueError("Building and floor are required")

        selection = MapExportSnapshot(client.get(f"/campuses/{campus_id}/export", timeout=60)).select_floor(
            building_id=str(building_id),
            floor_id=str(floor_id),
        )
        spaces = selection.spaces
        output = self.workspace.output(
            body.get("output_path") or body.get("outputPath"),
            fallback_name=selection.fallback_filename(output_format),
        )
        rendered = self.renderer.render(
            output_format=output_format,
            building=selection.building,
            floor=selection.floor,
            spaces=spaces,
            connections=selection.connections,
        )
        if isinstance(rendered, bytes):
            output.write_bytes(rendered)
        else:
            output.write_text(rendered, encoding="utf-8")

        return {
            "file": {
                "path": self.workspace.relative(output),
                "format": output_format,
                "spaces": len(spaces),
                "doors": len(unique_doors(selection.connections)),
            }
        }

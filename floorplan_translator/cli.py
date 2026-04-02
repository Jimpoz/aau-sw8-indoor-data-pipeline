from __future__ import annotations

import argparse
import json
from pathlib import Path

from .analysis.FloorPlanTranslator import FloorPlanTranslator
from .analysis.TranslatorOverrides import TranslatorOverrides
from .config import load_translator_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Translate floor-plan PDFs/images into spatial-backend JSON.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    translate_parser = subparsers.add_parser("translate", help="Translate one PDF/image source.")
    translate_parser.add_argument("source_path", help="Path to a PDF or image file.")
    translate_parser.add_argument("--config", dest="config_path", help="Optional custom CFG config file.")
    translate_parser.add_argument("--output", required=True, help="Path to write the translation JSON.")
    translate_parser.add_argument(
        "--mode",
        choices=("normal", "debug"),
        default="normal",
        help="Use 'debug' to automatically include the full intermediate image pipeline in the output.",
    )
    translate_parser.add_argument("--campus-name")
    translate_parser.add_argument("--campus-id")
    translate_parser.add_argument("--building-name")
    translate_parser.add_argument("--building-id")
    translate_parser.add_argument("--floor-name")
    translate_parser.add_argument("--floor-index", type=int)
    translate_parser.add_argument("--scale-meters-per-pixel", type=float)
    translate_parser.add_argument("--debug", action="store_true")
    translate_parser.add_argument("--debug-dir")
    translate_parser.add_argument("--include-debug-images", action="store_true")
    translate_parser.add_argument(
        "--preview-pipeline-images",
        action="store_true",
        help="Embed every intermediate pipeline image in the output debug payload before export.",
    )
    translate_parser.add_argument("--import-backend", action="store_true")
    translate_parser.add_argument("--backend-import-base-url")

    serve_parser = subparsers.add_parser("serve", help="Run the FastAPI service.")
    serve_parser.add_argument("--host", default="0.0.0.0")
    serve_parser.add_argument("--port", type=int, default=8010)

    return parser


def run_translate(args: argparse.Namespace) -> int:
    translator = FloorPlanTranslator(load_translator_config(args.config_path))
    result = translator.translate_path(
        args.source_path,
        overrides=TranslatorOverrides(
            mode=args.mode,
            campus_name=args.campus_name,
            campus_id=args.campus_id,
            building_name=args.building_name,
            building_id=args.building_id,
            floor_name=args.floor_name,
            floor_index=args.floor_index,
            scale_meters_per_pixel=args.scale_meters_per_pixel,
            debug=args.debug,
            debug_dir=args.debug_dir,
            include_debug_images=args.include_debug_images,
            preview_pipeline_images=args.preview_pipeline_images,
            import_to_backend=args.import_backend,
            backend_import_base_url=args.backend_import_base_url,
        ),
    )
    output_path = Path(args.output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(str(output_path))
    return 0


def run_server(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("main:app", host=args.host, port=args.port, reload=False)
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "translate":
        return run_translate(args)
    if args.command == "serve":
        return run_server(args)
    parser.error(f"Unknown command {args.command!r}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

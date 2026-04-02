# Indoor Data Pipeline

Standalone floor-plan translator that turns PDF/image plans into the
`aau-sw8-spatial-backend` map import format, with optional backend import and
debug artifacts.

The canonical service package now lives in
[services/floorplan_translator_service](/home/rubens/code/aau-sw8-indoor-data-pipeline/services/floorplan_translator_service),
which aggregates the translator modules behind one service boundary.

## What it does

- renders PDF pages to raster images with `pdftoppm`
- infers campus/building/floor names from source metadata and filenames
- extracts wall-like linework with OpenCV heuristics
- builds an enclosure barrier, footprint, and enclosed-space mask
- tries both border-fill and contour-based room extraction, then keeps the better result
- exports a backend-compatible `MapImportSchema` bundle
- exposes both a CLI and a FastAPI service

The translator also returns a richer debug payload with per-floor footprint,
wall polygons, detected spaces, and optional image artifacts.

## Current behavior

The implementation is intentionally modular and debug-friendly, but it is still
heuristic.

It works best on:

- vector or high-resolution floor plans
- plans with mostly horizontal/vertical walls
- sources where the campus/building/floor name can be inferred from metadata,
  filename, or explicit request parameters
- environments where OCR can be enabled later with `tesseract`

It currently degrades gracefully when OCR or scale extraction is unavailable:

- floor names still fall back to filename/page patterns
- campus names are inferred from title/path segments when possible
- geometry is exported in pixel coordinates when metric scale cannot be inferred
- room names fall back to generated labels like `Space 1`

For room extraction specifically, the translator now uses two enclosure
strategies:

- `border-fill`: removes all free space that can leak to the page border and treats the remaining free space as enclosed building interior
- `contour-fallback`: builds the footprint from the strongest detected outer contours and extracts rooms inside that footprint

The chosen strategy is stored per floor in `metadata.enclosure_strategy`. This
is especially useful on technical plans where rooms are visually closed but the
wall barrier still contains small breaks.

## Install

Use the project virtualenv or install the dependencies yourself:

```bash
pip install -r requirements.txt
```

Optional runtime tools:

- `pdftoppm` for PDF rendering
- `tesseract` if you want OCR-backed labels and dimension extraction

## CLI

Translate one local source:

```bash
python -m floorplan_translator translate /path/to/plan.pdf --output /tmp/plan.json
```

Use explicit modes:

```bash
python -m floorplan_translator translate /path/to/plan.pdf --output /tmp/plan.json --mode normal
python -m floorplan_translator translate /path/to/plan.pdf --output /tmp/plan.json --mode debug
```

Write debug PNG artifacts:

```bash
python -m floorplan_translator translate /path/to/plan.pdf \
  --output /tmp/plan.json \
  --debug \
  --debug-dir /tmp/floorplan_debug
```

Embed all intermediate image stages in the output JSON before export:

```bash
python -m floorplan_translator translate /path/to/plan.pdf \
  --output /tmp/plan.json \
  --preview-pipeline-images
```

## Modes

The translator now supports two high-level modes:

- `normal`: the standard translation flow for everyday use
- `debug`: automatically enables the full debug pipeline and embeds the generated image stages in the response

If you choose `debug`, you do not need to manually set `--debug`, `--include-debug-images`, or `--preview-pipeline-images`.

In the HTTP API, the equivalent split is handled by separate endpoints:

- normal translation: `POST /translate/upload`
- debug translation: `POST /debug/upload`

## Debug Options

The translator exposes three complementary debug switches:

- `--debug`: enables the per-floor `debug` payload. Use this when you want metadata about the extraction run and optional image artifacts.
- `--debug-dir /path/to/dir`: writes each intermediate debug image to disk as a PNG. This is the best option when you want to inspect the pipeline visually without inflating the JSON response.
- `--include-debug-images`: embeds the currently generated debug images directly in the JSON response as base64 PNGs.
- `--preview-pipeline-images`: returns the full ordered image-processing pipeline before export. This includes the resized input, crop stages, grayscale/blurred/binary images, wall and footprint masks, interior regions, opening detection, and the final overlay.

`preview_pipeline_images` is intended for tuning and troubleshooting. It is especially useful when you want to answer questions like:

- Did the drawing crop remove too much or too little of the page?
- Is thresholding producing a clean binary image?
- Are the wall and footprint masks capturing the real structure?
- Are openings and enclosed regions being segmented as expected?

When a room should be closed but is still not being filled correctly, inspect
these stages first:

- `06_wall_mask`: the enclosure barrier used for room detection after wall recovery and long-line reinforcement
- `07_sealed_wall_mask`: the same barrier after directional door-gap sealing
- `08_footprint_mask`: the inferred building footprint used for region extraction
- `09_interior_mask`: the final enclosed regions that become spaces

If `06_wall_mask` is broken, the issue is usually wall detection. If `06` looks
good but `09` is still missing closed rooms, the issue is usually the enclosure
strategy or sealing step.

Because every stage can be embedded as a base64 PNG, enabling `--preview-pipeline-images` can make the JSON output much larger. If you only want to inspect the images locally, prefer `--debug --debug-dir /tmp/floorplan_debug`.

Force campus/building names or scale:

```bash
python -m floorplan_translator translate /path/to/plan.pdf \
  --output /tmp/plan.json \
  --campus-name "AAU Campus" \
  --building-name "A.C. Meyers Vaenge 15" \
  --scale-meters-per-pixel 0.025
```

Import directly into the spatial backend through its existing import endpoint:

```bash
python -m floorplan_translator translate /path/to/plan.pdf \
  --output /tmp/plan.json \
  --import-backend \
  --backend-import-base-url http://localhost:8000/api/v1
```

## API

Run the service:

```bash
uvicorn main:app --host 0.0.0.0 --port 8010 --reload
```

Or start it directly from `main.py`:

```bash
python main.py
```

Optional env vars for `python main.py`:

- `FLOORPLAN_SERVICE_HOST`
- `FLOORPLAN_SERVICE_PORT`
- `FLOORPLAN_SERVICE_RELOAD`

You can also use the service package directly:

```bash
python -m services.floorplan_translator_service translate /path/to/plan.pdf --output /tmp/plan.json
```

Useful endpoints:

- `GET /health`
- `GET /config/defaults`
- `POST /translate/upload`
- `POST /debug/upload`
- `POST /debug/upload/download`

`POST /translate/upload` is the normal upload endpoint. It no longer exposes the
debug image options.

If the source file is already on the same machine, prefer the CLI instead of a
path-based HTTP endpoint:

```bash
python -m floorplan_translator translate /path/to/plan.pdf --output /tmp/plan.json
```

If you want a dedicated inspection-only service instead of the normal
translation endpoints, use the debug endpoints:

- `POST /debug/upload`: debug translation from an uploaded PDF or image
- `POST /debug/upload/download`: generate a debug bundle ZIP from an uploaded PDF or image

These endpoints always run in debug mode, always return the intermediate image
pipeline, and always keep `import_to_backend` disabled.

If you prefer to download the generated PNGs directly, use the download
endpoints. They return a ZIP file containing:

- `translation.json`
- all generated debug PNG stages inside `artifacts/`

Example normal upload request:

```bash
curl -X POST http://127.0.0.1:8000/translate/upload \
  -H 'accept: application/json' \
  -F 'source=@/home/user/Downloads/plan.pdf;type=application/pdf'
```

Example debug upload request:

```bash
curl -X POST http://127.0.0.1:8000/debug/upload \
  -H 'accept: application/json' \
  -F 'source=@/home/user/Downloads/plan.pdf;type=application/pdf'
```

Example debug upload download request:

```bash
curl -X POST http://127.0.0.1:8000/debug/upload/download \
  -H 'accept: application/zip' \
  -F 'source=@/home/user/Downloads/plan.pdf;type=application/pdf' \
  --output floorplan_debug_bundle.zip
```

When you use the debug endpoints, each floor contains:

- `debug.artifact_order`: the exact order of the pipeline stages
- `debug.artifacts.<stage>.path`: the saved PNG path when `debug_dir` is used
- `debug.artifacts.<stage>.base64_png`: the embedded image when preview or inline debug images are enabled
- `metadata.enclosure_strategy`: the strategy chosen for room extraction, either `border-fill` or `contour-fallback`

The current stage list is:

- `00_resized_input`
- `01_fractional_crop`
- `02_drawing_crop`
- `03_gray`
- `04_blurred`
- `05_binary`
- `06_wall_mask`
- `07_sealed_wall_mask`
- `08_footprint_mask`
- `09_interior_mask`
- `10_openings`
- `11_overlay`

## Config

The default config lives at [config/defaults.cfg](/home/rubens/code/aau-sw8-indoor-data-pipeline/config/defaults.cfg).

Important knobs:

- `input.pdf_dpi`
- `input.crop_*_fraction`
- `geometry.wall_open_kernel`
- `geometry.wall_dilate_kernel`
- `geometry.opening_close_kernel`
- `geometry.space_min_area_ratio`
- `text.ocr_engine`
- `scale.default_meters_per_pixel`
- `scale.use_default_scale_as_fallback`
- `project.backend_import_base_url`

If room filling is too aggressive or too weak, the most important tuning knobs
are usually `input.pdf_dpi`, `geometry.wall_open_kernel`,
`geometry.wall_dilate_kernel`, `geometry.opening_close_kernel`, and
`geometry.space_min_area_ratio`.

You can provide a second CFG file at runtime with `--config`, or override the
same fields through the HTTP API request body. Legacy TOML files are still
accepted for custom configs.

`project.backend_import_base_url` is intentionally blank by default. If you set
`import_to_backend=true`, point it at the real spatial backend, for example
`http://localhost:8001/api/v1`. Do not point it at the translator service
itself.

## Output shape

The translator returns:

- source metadata
- inferred campus/building info
- per-floor footprint, walls, spaces, and detected doors
- per-floor extraction metadata, including the selected enclosure strategy
- `map_import`, which matches the spatial backend import schema

That means the same output can be:

- stored as JSON for inspection
- sent to the backend import endpoint
- used later for additional debug tooling

## Sample smoke test

The current implementation was smoke-tested locally against:

- `/home/rubens/Downloads/ACM15_1.Sal.pdf`

On that sample, the translator currently:

- infers campus/building name from the PDF title metadata
- infers floor index `1` from `1.Sal`
- exports a backend-compatible bundle
- saves debug layers for tuning
- currently selects `contour-fallback` for room extraction because the outer barrier still has technical-plan breaks

## Next tuning targets

- stronger OCR-backed room naming
- better separation of text fragments from real wall geometry
- better corridor/open-space extraction on complex technical plans
- better door-gap detection on architectural drawings with thin wall lines
- optional direct Neo4j import if you decide you want it in addition to backend import

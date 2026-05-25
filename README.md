# Floor Data Tool

This repo helps with one practical job:

Turn a floor plan into floor data for the Map API.

The tool can create a readable floor plan from an existing Map API floor, read a
floor plan back into rooms and doors, prepare floor import data, push it into a
selected floor, and verify the result. The browser UI is the normal way to use
it.

## Table Of Contents

- [Start It](#start-it)
- [How To Use](#how-to-use)
- [Input Floor Plan](#input-floor-plan)
- [Generated Floor Plans](#generated-floor-plans)
- [Working Files](#working-files)
- [What Happens Inside](#what-happens-inside)
- [Requirements](#requirements)
- [Command Line](#command-line)

## Start It

Use the project Python environment:

```sh
proj/bin/python floor_data_tool.py --port 8765
```

The root launcher also works directly:

```sh
./floor_data_tool.py --port 8765
```

When the local `proj` environment exists, the executable uses it automatically.

Then open:

```text
http://127.0.0.1:8765
```

The UI runs on your machine. It calls the Map API through the local Python
backend, so ngrok headers and browser CORS do not get in your way.

To stop the program, go back to the terminal and press `Ctrl+C`.

If port `8765` is already busy, either stop the old program or start this one on
another port:

```sh
proj/bin/python floor_data_tool.py --port 8766
```

## How To Use

The program is split into three pages. Use them from left to right.

### 1. Make Floor Plan

Use the `Make Floor Plan` page first when the floor already exists in the Map API:

1. Paste the Map API URL.

   Example:

   ```text
   https://your-map-api.example
   ```

2. Add a bearer token only if that API asks for one.
3. Click `Connect API`.
4. Pick the organization and campus.
5. Click `Load Floors`.
6. Pick the building and floor.
7. Choose the file format and check `Save As`.
8. Click `Create Floor Plan`.
9. Click `Save To Computer` if you want to choose where the file is saved on
   your computer.
10. Click `Open Read Floor Plan`.

This creates a local floor plan from the floor stored in the Map API. The plan is
filled into the next page automatically. Generated PDF files also contain an
embedded machine-readable details block, so reading them back does not depend on
OCR guessing the labels.

If you already have a floor plan, skip this page and start at `Read Floor Plan`.

### 2. Read Floor Plan

1. Check the selected floor plan, or click `Choose From Computer`.
2. Set the extraction options.
3. Click `Read Floor Plan`.
4. Click `Open Push to Floor`.

This reads the floor plan and finds rooms, doors, room names, and door
positions. Generated PDFs from the first page read back from the embedded details
block. Other PDFs and images use the CV/OCR path.

### 3. Push To Floor

1. Check `Map API Destination`. This is the server that will receive the data.
2. Check `Target Floor`. This is the campus, building, and floor the import will write into.
3. Check `Import Files`. `Detected Floor Data` is the data read from the file, and `Floor Import File` is the JSON that will be sent.
4. Click `Prepare Floor Data`.
5. Click `Push to Floor`.
6. Click `Verify Floor`.

`Prepare + Push + Verify` runs the push-page steps in one button once the Map API,
campus, building, floor, and source have been selected.

## Input Floor Plan

The UI accepts one local floor plan. It detects the file type automatically.

For uploaded image files, these defaults are the ones to start with:

- `Scale Bar Meters`: `20`
- `OCR rooms`: on
- `OCR door IDs`: off
- `Room ID Mode`: `CVR sequence`
- `Chunk import`: on

Door OCR is off by default because many uploaded image files have labels that
are too small for Tesseract to read reliably. The reader still detects the red
door marks and imports door positions; it just generates internal door IDs.

## Generated Floor Plans

Floor plans created from the Map API are meant to be readable by people and by
the tool.

Room labels use the server room names and Danish text:

```text
NAME
H X B 4.2 X 3.1 M
2 DØRE
```

Door labels show:

```text
D001U
0.4 M
```

Door labels are placed around the door marker to avoid walls where possible. The
visible red marker is small, but the PDF keeps the exact door data inside the
embedded readback block.

## Working Files

The UI keeps generated files in `outputs/`. Temporary working data can be
deleted and regenerated. Floor plans created from the Map API are saved there
unless you choose another path.

## What Happens Inside

The local program is split by responsibility:

- `floor_data_tool.py` is the root executable that starts the local program.
- `backend/local_server.py` is the thin HTTP backend for the local browser UI.
- `backend/floor_data_service.py` owns the UI actions: export, read, prepare, push, verify, and floor-plan creation.
- `backend/local_workspace.py` handles local files, uploads, outputs, and the frontend entry file.
- `backend/map_api_client.py` talks to the Map API.
- `backend/campus_export.py` finds the chosen building and floor inside API exports.
- `backend/floor_import_payloads.py` prepares and summarizes import payloads.
- `backend/floor_plan_renderer.py` draws readable SVG, PDF, and PNG floor plans.

`backend/floor_plan_cv_reader.py` reads floor plans:

- uses embedded PDF details when the file was generated by this tool
- otherwise detects room polygons from fill colors
- otherwise detects door marks from red pixels
- otherwise reads room names with cropped Tesseract OCR
- connects detected doors to nearby rooms

`backend/floor_import_builder.py` converts extracted details into the Map API
floor import shape: room-to-room door connections with:

- `door_id`
- `door_cx`
- `door_cy`

That is the shape the current frontend reads back as door markers. There are no
separate door spaces in the floor import data.

`frontend/index.html` is the browser UI. The Python backend serves that frontend
entry file and provides the `/api/...` endpoints it calls.

## Requirements

Create the local Python environment and install the Python modules:

```sh
python -m venv proj
proj/bin/python -m pip install --upgrade pip
proj/bin/python -m pip install -r requirements.txt
```

OCR and file reading also need system tools. On Arch:

```sh
sudo pacman -S tesseract tesseract-data-eng tesseract-data-dan poppler
```

On Debian/Ubuntu:

```sh
sudo apt install tesseract-ocr tesseract-ocr-eng tesseract-ocr-dan poppler-utils
```

Check that Tesseract is visible:

```sh
tesseract --list-langs
```

For our current AAU maps, this should include:

```text
eng
dan
```

Check that the file-reading tools are visible too:

```sh
which pdftotext pdftoppm pdfinfo
```

## Command Line

The UI is easier, but these commands are useful when debugging.

Read from a floor plan:

```sh
proj/bin/python backend/floor_plan_cv_reader.py \
  --image path/to/floor-plan.png \
  --output outputs/floor_details.json \
  --building-name Test \
  --floor-name Test \
  --floor-index 0 \
  --scale-bar-meters 20 \
  --ocr-language eng+dan \
  --ocr-min-confidence 5 \
  --ocr-crop-scale 7 \
  --image-room-id-mode sequence \
  --no-door-crop-ocr
```

Prepare floor data JSON:

```sh
proj/bin/python backend/floor_import_builder.py \
  --details-only \
  --details outputs/floor_details.json \
  --campus-export outputs/campus-aau-cph_export_latest.json \
  --building-name Test \
  --floor-name Test \
  --floor-index 0 \
  --output outputs/floor_import.json
```

If the rooms are already imported and you only want to add door connections:

```sh
proj/bin/python backend/floor_import_builder.py \
  --details-only \
  --details outputs/floor_details.json \
  --campus-export outputs/campus-aau-cph_export_latest.json \
  --building-name Test \
  --floor-name Test \
  --floor-index 0 \
  --only-doors \
  --output outputs/floor_import.json
```

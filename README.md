# Middleware

This repository contains a FastAPI service that does two jobs:

- it analyzes room photos with a YOLO model
- it reads and updates building data in Neo4j for room lookup and pathfinding

If you want the short version: this is the backend layer that turns uploaded room images and Neo4j graph data into API responses a frontend can use.

## What it does

### Room photo analysis

The service accepts exactly four images of a room, taken from different directions.

It then:

- decodes the uploaded files into images
- runs object detection with YOLO
- counts the detected objects across the four views
- generates SVG summaries for each view
- can write the detected room data back onto the matching room node in Neo4j

This logic lives in `room_summary/`.

### Indoor pathfinding

The service also exposes navigation endpoints backed by Neo4j.

It can:

- list rooms on a given floor
- find the fastest path between two rooms
- return SVG debug output for a route
- return a map-style SVG using room geometry stored in Neo4j

This logic lives in `pathfinding/` and `routes_pathfinding.py`.

## How the app behaves

- On startup, it checks that Neo4j is reachable.
- On shutdown, it closes the Neo4j driver cleanly.
- Room-summary routes are mounted directly at the app root.
- Pathfinding routes are mounted under `/api/pathfinding`.

That means the API currently has a mixed route layout on purpose:

- `/health`
- `/get-room-names`
- `/room-summary`
- `/room-summary/by-room`
- `/room-objects-detection`
- `/api/pathfinding/...`

## Endpoints

### Health

- `GET /health`
  Returns `{"status": "ok"}` if the service is up.

### Room summary endpoints

- `GET /get-room-names`
  Returns room names from Neo4j that can be used when storing room detections.

- `POST /room-summary`
  Accepts four uploaded images and returns object counts and per-view SVG summaries.

- `POST /room-summary/by-room`
  Same as `/room-summary`, but also includes the room name supplied in the form data.

- `POST /room-objects-detection`
  Accepts a room name plus four images, runs detection, and stores the result on the matched room node in Neo4j.

All room-summary POST endpoints accept an optional `model_name` query parameter. That value selects a model profile from `modelConfig.cfg`.

### Pathfinding endpoints

- `GET /api/pathfinding/rooms?floor=1`
  Returns room names for one floor.

- `GET /api/pathfinding/fastest-path?start=A&end=outside`
  Returns the fastest route between two points in the graph.

- `GET /api/pathfinding/debug/image?start=A&end=outside`
  Returns an SVG debug representation of the route.

- `GET /api/pathfinding/debug/map?start=A&end=outside`
  Returns an SVG map view of the route using room geometry from Neo4j.

## What Neo4j is used for

Neo4j is the shared source of truth for building structure and room records.

The middleware uses it to:

- fetch room names
- build the room/door graph used for routing
- read room geometry for SVG map output
- store room object detections and generated room-image data

If the graph data is incomplete or inconsistent, the API still runs, but route quality and room matching will reflect the stored data.

## Model behavior

The room-summary feature uses Ultralytics YOLO.

Model selection works like this:

- if `model_name` is provided, the service looks it up in `modelConfig.cfg`
- otherwise it uses `YOLO_MODEL_PROFILE` if set
- if no profile is selected, it falls back to `YOLO_MODEL_PATH`
- if the configured model file does not exist but matches a supported YOLO11 filename, the service attempts to download it automatically

Class labels can be overridden through `CLASS_CONFIG_PATH`. If that is not set, the app looks for `classConfig.cfg` and then `classConf.cfg`.

The runtime also creates cache directories under `.cache/` unless you override them with environment variables.

## Important request expectations

- Room-summary endpoints require exactly four images.
- `room_name` is required for `/room-summary/by-room` and `/room-objects-detection`.
- Pathfinding expects the Neo4j database to contain room and door relationships that describe the building layout.
- The room-object storage route expects Neo4j room nodes that can be matched by room name or id.

## Configuration

The app loads configuration from environment variables and also reads `app.cfg` or `.env` automatically.

Common application and Neo4j settings:

- `API_TITLE`
- `API_VERSION`
- `BACKEND_PORT`
- `NEO4J_URI`
- `NEO4J_USER`
- `NEO4J_PASSWORD`
- `NEO4J_AUTH`

Room-summary and model settings:

- `YOLO_MODEL_PATH`
- `YOLO_MODEL_PROFILE`
- `MODEL_CONFIG_PATH`
- `CLASS_CONFIG_PATH`
- `YOLO_CONFIDENCE_THRESHOLD`
- `VECTOR_PALETTE_SIZE`
- `MAX_VECTOR_WIDTH`
- `ROOM_SUMMARY_CACHE_DIR`
- `MPLCONFIGDIR`
- `YOLO_CONFIG_DIR`

## Run locally

Install dependencies:

```bash
pip install -r requirements.txt
```

Start the API:

```bash
uvicorn middleware:app --host 0.0.0.0 --port 8000 --reload
```

If you want to use the configured backend port from `app.cfg`, run uvicorn with that same port value yourself. The config file is read by the application, but uvicorn still uses the port you pass on startup.

## Project structure

- `middleware.py`
  Main FastAPI app, room-summary routes, and lifespan hooks.

- `db.py`
  Neo4j driver creation, startup connectivity check, and shutdown cleanup.

- `routes_pathfinding.py`
  Pathfinding API routes.

- `room_summary/`
  Model loading, detection, image summarization, and Neo4j room updates.

- `pathfinding/`
  Graph loading, shortest-path calculation, and SVG rendering.

## In one sentence

This middleware is the API layer that connects room-image analysis and Neo4j-backed indoor navigation in one service.

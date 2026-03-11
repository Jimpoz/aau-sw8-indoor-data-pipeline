# Indoor Data Pipeline

This part of project was implemented a data-pipeline based on the [paper](https://ieeexplore.ieee.org/stamp/stamp.jsp?tp=&arnumber=10099457).

This backend part exposes pathfinding APIs over the topology graph stored in Neo4j.
It supports:

- listing available navigation names
- fastest-path computation using Dijkstra
- route visualization as SVG (sequence view + floor map view)

## Architecture

Main files:

- `main.py`: FastAPI app + startup/shutdown lifecycle
- `routes_pathfinding.py`: API routes
- `ROUTES_INPUTS.md`: route input reference (query params, validation, examples)
- `db.py`: Neo4j driver init/close
- `pathfinding/`: object-oriented pathfinding module

Object-oriented pathfinding module:

- `pathfinding/service.py`:
  - `PathfindingService`: orchestration facade used by routes
- `pathfinding/repository.py`:
  - `PathfindingRepository`: Neo4j queries (states, graph edges, map geometry)
- `pathfinding/pathfinder.py`:
  - `DijkstraPathfinder`: shortest-path algorithm
- `pathfinding/renderers.py`:
  - `PathSvgRenderer`: step-by-step path image
  - `PathMapSvgRenderer`: floor map + route line
- `pathfinding/query_runner.py`:
  - `Neo4jQueryRunner`: runs Cypher for Session/Driver
- `pathfinding/models.py`:
  - dataclasses (`FastestPathResult`, map geometry models)

## Function Inputs and Outputs

### API route functions (`routes_pathfinding.py`)

| Function | Inputs | Output | Errors |
|---|---|---|---|
| `get_all_state_names()` | none | `{"names": list[str]}` | `500` on backend/DB errors |
| `get_room_names_by_floor(floor)` | `floor: int >= 1` (query param) | `{"floor": int, "names": list[str]}` | `500` on backend/DB errors |
| `get_fastest_path(start, end="outside")` | `start: str`, `end: str` (query params) | `{"start": str, "end": str, "path": list[str], "cost": float}` | `404` when no/unknown path target, `500` otherwise |
| `get_fastest_path_image(start, end="outside")` | `start: str`, `end: str` | `Response(image/svg+xml)` | `404` when no/unknown path target, `500` otherwise |
| `get_fastest_path_map(start, end="outside")` | `start: str`, `end: str` | `Response(image/svg+xml)` | `404` when no/unknown path target, `500` otherwise |

### Service layer (`pathfinding/service.py`)

| Function                                                   | Inputs                             | Output                                            |
| ------------------------------------------------------------| ------------------------------------| ---------------------------------------------------|
| `PathfindingService(conn)`                                 | `conn: neo4j Driver/Session`       | service instance                                  |
| `list_all_names()`                                         | none                               | `list[str]`                                       |
| `list_room_names_by_floor(floor)`                          | `floor: int`                       | `list[str]`                                       |
| `find_fastest_path(start_name, end_name="outside")`        | `start_name: str`, `end_name: str` | `FastestPathResult(path: list[str], cost: float)` |
| `build_fastest_path_svg(start_name, end_name, result)`     | `str, str, FastestPathResult`      | `str` (SVG markup)                                |
| `build_fastest_path_map_svg(start_name, end_name, result)` | `str, str, FastestPathResult`      | `str` (SVG markup)                                |

### Data access layer (`pathfinding/repository.py`)

| Function | Inputs | Output | Why |
|---|---|---|---|
| `list_state_names()` | none | `list[str]` | names shown to API clients |
| `list_room_names_by_floor(floor)` | `floor: int` | `list[str]` | room listing by floor |
| `build_state_graph()` | none | `StateGraph(adjacency, lookup)` | graph for Dijkstra routing |
| `load_map_rects()` | none | `dict[int, list[SpaceRect]]` | floor/room rectangles for map rendering |
| `load_state_positions()` | none | `dict[str, StatePosition]` | state coordinates to draw route line |

### Algorithm and rendering functions

| File / Function | Inputs | Output |
|---|---|---|
| `pathfinding/pathfinder.py` -> `DijkstraPathfinder.find_fastest_path(graph, start_name, end_name)` | `StateGraph`, `str`, `str` | `FastestPathResult` |
| `pathfinding/renderers.py` -> `PathSvgRenderer.render(start_name, end_name, result)` | `str`, `str`, `FastestPathResult` | `str` (SVG sequence image) |
| `pathfinding/renderers.py` -> `PathMapSvgRenderer.render(start_name, end_name, result, rects_by_floor, state_positions)` | `str`, `str`, `FastestPathResult`, map geometry, state coords | `str` (SVG floor map image) |

### Startup/DB functions

| File / Function | Inputs | Output |
|---|---|---|
| `db.py` -> `initialize_neo4j_schema()` | none | `None` (connectivity check with retry) |
| `db.py` -> `close_neo4j()` | none | `None` |
| `main.py` -> `lifespan(app)` | `FastAPI app` | async context manager for startup/shutdown |
| `main.py` -> `health()` | none | `{"status": "ok"}` |

## Data Model (Neo4j)

The backend reads these labels/relations generated by topology export:

- Node labels:
  - `GeneralSpace`: room/hallway/elevator/escalator geometry + metadata
  - `State`: navigation node used for pathfinding
  - `Transition`: door transition node
  - `TransitionSpace`: outside space
  - `CellSpaceBoundary`: door boundary node
  - `CellSpaceBoundaryGeometry`: boundary geometry (`LINESTRING`)
  - `Floor`: floor index
- Relationships:
  - `PATH`: weighted state-to-state edges (`cost`)
  - `DUALITY`: semantic pair mapping (`State` <-> `GeneralSpace`/`TransitionSpace`)
  - `CONNECTS`: state-transition links
  - `PARTIALBOUNDEDBY`: spaces to door boundaries
  - `HAS_GEOMETRY`: boundary to geometry
  - `HAS_FLOOR`: floor assignment

### Visual: Neo4j model overview

![Neo4j Model Overview](./docs/images/neo4j_model_overview.jpg)

## API Endpoints

Base URL: `http://localhost:6969`

For a route-input-only reference, see: [`ROUTES_INPUTS.md`](./ROUTES_INPUTS.md)

### 1) Health

- `GET /health`
- Response:

```json
{ "status": "ok" }
```

### 2) List all state names

- `GET /api/pathfinding/names`
- Response:

```json
{
  "names": ["outside", "state_hallway_1_f1", "state_room_3_f2"]
}
```

### 3) List room names by floor

- `GET /api/pathfinding/rooms?floor=2`
- Response:

```json
{
  "floor": 2,
  "names": ["hallway_1_f2", "room_2_f2", "room_3_f2"]
}
```

### 4) Fastest path (JSON)

- `GET /api/pathfinding/fastest?start=transition_space_outside&end=room_3_f3`
- Response:

```json
{
  "start": "transition_space_outside",
  "end": "room_3_f3",
  "path": [
    "outside",
    "state_hallway_3_f1",
    "state_elevator_1_f1",
    "state_elevator_1_f2",
    "state_elevator_1_f3",
    "state_hallway_3_f3",
    "state_room_3_f3"
  ],
  "cost": 516.531
}
```

### 5) Fastest path image (sequence SVG)

- `GET /api/pathfinding/fastest/image?start=transition_space_outside&end=room_3_f3`
- Returns: `image/svg+xml`

Example:

![Fastest Path Sequence Example](./docs/images/fastest_path_example.svg)

### 6) Fastest path map (floor map SVG with route line)

- `GET /api/pathfinding/fastest/map?start=transition_space_outside&end=room_3_f3`
- Returns: `image/svg+xml`

Example:

![Route Map Example](./docs/images/route_map_example.png)

## Request Examples

### JSON path

```bash
curl -G "http://localhost:6969/api/pathfinding/fastest" \
  --data-urlencode "start=transition_space_outside" \
  --data-urlencode "end=room_3_f3"
```

### Sequence image

```bash
curl -G "http://localhost:6969/api/pathfinding/fastest/image" \
  --data-urlencode "start=transition_space_outside" \
  --data-urlencode "end=room_3_f3" \
  -o fastest_path.svg
```

### Map image

```bash
curl -G "http://localhost:6969/api/pathfinding/fastest/map" \
  --data-urlencode "start=transition_space_outside" \
  --data-urlencode "end=room_3_f3" \
  -o route_map.svg
```

## Run Locally

```bash
cd backend
uvicorn backend.main:app --host 0.0.0.0 --port 6969
```

## Docker

### Build image

```bash
cd backend
docker build -f Dockerfile -t topology-pathfinding:latest .
```

### Run image

```bash
docker run --rm -p 6969:6969 --env-file app.cfg --add-host host.docker.internal:host-gateway topology-pathfinding:latest
```

### Run with Docker Compose (Neo4j + backend)

```bash
cd backend
docker compose up --build
```

Notes:

- Backend API: `http://localhost:6969`

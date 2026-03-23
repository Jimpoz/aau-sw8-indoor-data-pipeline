# Route Inputs

This document lists the HTTP routes and the inputs they accept.

## Base URLs

Default combined service:

- `http://localhost:8000`

Optional standalone pathfinding service:

- `http://localhost:6969`

## Quick Reference

| Method | Route | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Health check |
| `GET` | `/api/room-summary/rooms` | List room names from Neo4j |
| `POST` | `/api/room-summary` | Generate room summaries from 4 images |
| `POST` | `/api/room-summary/by-room` | Generate room summaries for a named room |
| `POST` | `/api/room-summary/room-objects/setup` | Run detection and save room data to Neo4j |
| `GET` | `/api/pathfinding/rooms` | List room names for a floor |
| `GET` | `/api/pathfinding/fastest` | Get the shortest room-to-room path |
| `GET` | `/api/pathfinding/debug/image` | Get the path as an SVG sequence |
| `GET` | `/api/pathfinding/debug/map` | Get the path drawn on the floor map |

## Common Rules

- Image endpoints expect `multipart/form-data`.
- Room-summary endpoints require exactly 4 uploaded images.
- `model_name` is optional on room-summary endpoints and should match a profile from `modelConfig.cfg`.
- Pathfinding `start` and `end` values should match room names stored in Neo4j. `end` defaults to `outside`.

## Health

### `GET /health`

Inputs:

- Query params: none
- Request body: none

Example:

```bash
curl "http://localhost:8000/health"
```

## Room Summary

### `GET /api/room-summary/rooms`

Inputs:

- Query params: none
- Request body: none

Example:

```bash
curl "http://localhost:8000/api/room-summary/rooms"
```

### `POST /api/room-summary`

Creates room summaries from 4 uploaded images.

Query parameters:

| Name | Type | Required | Default | Notes |
| --- | --- | --- | --- | --- |
| `model_name` | string | no | configured default | Example: `nano`, `small`, `medium` |

Form fields:

| Name | Type | Required | Notes |
| --- | --- | --- | --- |
| `images` | file list | yes | Must contain exactly 4 image files |

Example:

```bash
curl -X POST "http://localhost:8000/api/room-summary?model_name=nano" \
  -F "images=@/absolute/path/view1.jpg" \
  -F "images=@/absolute/path/view2.jpg" \
  -F "images=@/absolute/path/view3.jpg" \
  -F "images=@/absolute/path/view4.jpg"
```

### `POST /api/room-summary/by-room`

Creates room summaries from 4 uploaded images and includes the room name in the request.

Query parameters:

| Name | Type | Required | Default | Notes |
| --- | --- | --- | --- | --- |
| `model_name` | string | no | configured default | Example: `nano`, `small`, `medium` |

Form fields:

| Name | Type | Required | Notes |
| --- | --- | --- | --- |
| `room_name` | string | yes | Room name for the submitted image set |
| `images` | file list | yes | Must contain exactly 4 image files |

Example:

```bash
curl -X POST "http://localhost:8000/api/room-summary/by-room?model_name=nano" \
  -F "room_name=room_8_f2" \
  -F "images=@/absolute/path/view1.jpg" \
  -F "images=@/absolute/path/view2.jpg" \
  -F "images=@/absolute/path/view3.jpg" \
  -F "images=@/absolute/path/view4.jpg"
```

### `POST /api/room-summary/room-objects/setup`

Runs object detection, then writes room data back to Neo4j for the given room.

Stored room properties currently include:

- `roomObjects`
- `roomObjectCountsJson`
- `roomImages`
- `roomSummaryUpdatedAt`

Query parameters:

| Name | Type | Required | Default | Notes |
| --- | --- | --- | --- | --- |
| `model_name` | string | no | configured default | Example: `nano`, `small`, `medium` |

Form fields:

| Name | Type | Required | Notes |
| --- | --- | --- | --- |
| `room_name` | string | yes | Must match an existing Neo4j `Room` |
| `images` | file list | yes | Must contain exactly 4 image files |

Example:

```bash
curl -X POST "http://localhost:8000/api/room-summary/room-objects/setup?model_name=nano" \
  -F "room_name=room_8_f2" \
  -F "images=@/absolute/path/view1.jpg" \
  -F "images=@/absolute/path/view2.jpg" \
  -F "images=@/absolute/path/view3.jpg" \
  -F "images=@/absolute/path/view4.jpg"
```

## Pathfinding

These routes are available from the combined middleware on port `8000`, and also from the standalone pathfinding app on port `6969`.

### `GET /api/pathfinding/rooms`

Returns room names for a floor.

Query parameters:

| Name | Type | Required | Default | Validation |
| --- | --- | --- | --- | --- |
| `floor` | integer | yes | none | `>= 1` |

Example:

```bash
curl -G "http://localhost:8000/api/pathfinding/rooms" \
  --data-urlencode "floor=2"
```

### `GET /api/pathfinding/fastest`

Returns the shortest path between two rooms.

Query parameters:

| Name | Type | Required | Default | Validation |
| --- | --- | --- | --- | --- |
| `start` | string | yes | none | min length = 1 |
| `end` | string | no | `outside` | min length = 1 |

Example:

```bash
curl -G "http://localhost:8000/api/pathfinding/fastest" \
  --data-urlencode "start=room_8_f2" \
  --data-urlencode "end=room_3_f3"
```

### `GET /api/pathfinding/debug/image`

Returns the shortest path as an SVG sequence diagram.

Query parameters:

| Name | Type | Required | Default | Validation |
| --- | --- | --- | --- | --- |
| `start` | string | yes | none | min length = 1 |
| `end` | string | no | `outside` | min length = 1 |

Example:

```bash
curl -G "http://localhost:8000/api/pathfinding/debug/image" \
  --data-urlencode "start=room_8_f2" \
  --data-urlencode "end=room_3_f3" \
  -o fastest_path.svg
```

### `GET /api/pathfinding/debug/map`

Returns the shortest path as an SVG floor-map overlay.

Query parameters:

| Name | Type | Required | Default | Validation |
| --- | --- | --- | --- | --- |
| `start` | string | yes | none | min length = 1 |
| `end` | string | no | `outside` | min length = 1 |

Example:

```bash
curl -G "http://localhost:8000/api/pathfinding/debug/map" \
  --data-urlencode "start=room_8_f2" \
  --data-urlencode "end=room_3_f3" \
  -o route_map.svg
```

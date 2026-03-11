# Route Inputs

This file documents only the **input parameters** accepted by backend HTTP routes.

Base URL: `http://localhost:6969`

## GET `/health`

- Query params: none
- Body: none

## GET `/api/pathfinding/names`

- Query params: none
- Body: none

## GET `/api/pathfinding/rooms`

Query parameters:

| Name    | Type    | Required | Default | Validation |
| ---------| ---------| ----------| ---------| ------------|
| `floor` | integer | yes      | none    | `>= 1`     |

Example:

```bash
curl -G "http://localhost:6969/api/pathfinding/rooms" \
  --data-urlencode "floor=2"
```

## GET `/api/pathfinding/fastest`

Query parameters:

| Name | Type | Required | Default | Validation |
|---|---|---|---|---|
| `start` | string | yes | none | min length = 1 |
| `end` | string | no | `outside` | min length = 1 |

`start`/`end` may be State names or ids, and space ids/names that map to states through `DUALITY`.

Example:

```bash
curl -G "http://localhost:6969/api/pathfinding/fastest" \
  --data-urlencode "start=transition_space_outside" \
  --data-urlencode "end=room_3_f3"
```

## GET `/api/pathfinding/fastest/image`

Query parameters:

| Name | Type | Required | Default | Validation |
|---|---|---|---|---|
| `start` | string | yes | none | min length = 1 |
| `end` | string | no | `outside` | min length = 1 |

Example:

```bash
curl -G "http://localhost:6969/api/pathfinding/fastest/image" \
  --data-urlencode "start=transition_space_outside" \
  --data-urlencode "end=room_3_f3" \
  -o fastest_path.svg
```

## GET `/api/pathfinding/fastest/map`

Query parameters:

| Name | Type | Required | Default | Validation |
|---|---|---|---|---|
| `start` | string | yes | none | min length = 1 |
| `end` | string | no | `outside` | min length = 1 |

Example:

```bash
curl -G "http://localhost:6969/api/pathfinding/fastest/map" \
  --data-urlencode "start=transition_space_outside" \
  --data-urlencode "end=room_3_f3" \
  -o route_map.svg
```

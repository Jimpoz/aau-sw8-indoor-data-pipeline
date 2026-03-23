from __future__ import annotations

from html import escape
from typing import Dict, List, Tuple

from .models import FastestPathResult, SpaceRect, StatePosition


class PathSvgRenderer:
    """Renders a compact path sequence image."""

    def render(self, start_name: str, end_name: str, result: FastestPathResult) -> str:
        step_count = max(len(result.path), 1)
        margin = 40
        step_width = 220
        width = max(480, margin * 2 + step_count * step_width)
        height = 240
        center_y = 140

        nodes: List[str] = []
        edges: List[str] = []
        for idx, raw_name in enumerate(result.path):
            name = escape(raw_name)
            center_x = margin + idx * step_width + 80
            x = center_x - 70
            y = center_y - 25
            nodes.append(
                f"<rect x='{x}' y='{y}' width='140' height='50' rx='8' ry='8' "
                "fill='#eaf3ff' stroke='#1f5b99' stroke-width='2' />"
            )
            nodes.append(
                f"<text x='{center_x}' y='{center_y + 6}' text-anchor='middle' "
                "font-family='Arial, sans-serif' font-size='14' fill='#0f2742'>"
                f"{name}</text>"
            )
            if idx > 0:
                prev_x = margin + (idx - 1) * step_width + 150
                next_x = center_x - 70
                edges.append(
                    f"<line x1='{prev_x}' y1='{center_y}' x2='{next_x}' y2='{center_y}' "
                    "stroke='#0f2742' stroke-width='2' marker-end='url(#arrow)' />"
                )

        title = (
            f"Fastest path: {escape(start_name)} -> {escape(end_name)} "
            f"(cost={result.cost})"
        )
        subtitle = f"Steps: {len(result.path)}"

        return (
            f"<svg xmlns='http://www.w3.org/2000/svg' width='{width}' height='{height}' "
            "viewBox='0 0 "
            f"{width} {height}'>"
            "<defs>"
            "<marker id='arrow' markerWidth='10' markerHeight='7' refX='9' refY='3.5' orient='auto'>"
            "<polygon points='0 0, 10 3.5, 0 7' fill='#0f2742' />"
            "</marker>"
            "</defs>"
            f"<rect x='0' y='0' width='{width}' height='{height}' fill='#ffffff' />"
            f"<text x='20' y='32' font-family='Arial, sans-serif' font-size='18' fill='#0f2742'>{title}</text>"
            f"<text x='20' y='58' font-family='Arial, sans-serif' font-size='13' fill='#34516f'>{subtitle}</text>"
            f"{''.join(edges)}"
            f"{''.join(nodes)}"
            "</svg>"
        )


class PathMapSvgRenderer:
    """Renders multi-floor map panels with an overlaid route line."""

    def render(
        self,
        start_name: str,
        end_name: str,
        result: FastestPathResult,
        rects_by_floor: Dict[int, List[SpaceRect]],
        state_positions: Dict[str, StatePosition],
    ) -> str:
        if not rects_by_floor:
            raise ValueError("No Room geometry found in Neo4j.")

        all_rects = [rect for floor_rects in rects_by_floor.values() for rect in floor_rects]
        min_x = min(rect.x for rect in all_rects)
        min_y = min(rect.y for rect in all_rects)
        max_x = max(rect.x + rect.width for rect in all_rects)
        max_y = max(rect.y + rect.height for rect in all_rects)

        floors = sorted(rects_by_floor.keys())
        floor_index = {floor: idx for idx, floor in enumerate(floors)}

        scale = 2.0
        pad = 24
        header = 30
        gap = 18
        panel_width = int(max(240, round((max_x - min_x) * scale))) + (2 * pad)
        panel_height = int(max(180, round((max_y - min_y) * scale))) + (2 * pad)
        svg_width = panel_width + 40
        svg_height = 70 + len(floors) * (header + panel_height + gap)

        def panel_origin(floor: int) -> Tuple[float, float]:
            idx = floor_index[floor]
            return 20.0, 70.0 + idx * (header + panel_height + gap)

        def map_xy(floor: int, x: float, y: float) -> Tuple[float, float]:
            origin_x, origin_y = panel_origin(floor)
            return (
                origin_x + pad + ((x - min_x) * scale),
                origin_y + header + pad + ((y - min_y) * scale),
            )

        colors = {
            "room": ("#e8f3ff", "#2d6ca2"),
            "hallway": ("#eaf9eb", "#2f8a42"),
            "elevator": ("#fff1e6", "#ba6a1f"),
            "escalator": ("#f3ecff", "#6c3ab2"),
        }

        chunks: List[str] = []
        chunks.append(
            f"<svg xmlns='http://www.w3.org/2000/svg' width='{svg_width}' height='{svg_height}' "
            f"viewBox='0 0 {svg_width} {svg_height}'>"
        )
        chunks.append(
            "<defs><marker id='routeArrow' markerWidth='10' markerHeight='7' refX='9' "
            "refY='3.5' orient='auto'>"
        )
        chunks.append("<polygon points='0 0, 10 3.5, 0 7' fill='#cc1f1a' /></marker></defs>")
        chunks.append(f"<rect x='0' y='0' width='{svg_width}' height='{svg_height}' fill='#fbfbfb' />")
        chunks.append(
            f"<text x='20' y='28' font-family='Arial, sans-serif' font-size='18' fill='#172b3a'>"
            f"Route map: {escape(start_name)} -> {escape(end_name)} (cost={result.cost})</text>"
        )
        chunks.append(
            f"<text x='20' y='50' font-family='Arial, sans-serif' font-size='12' fill='#4b6478'>"
            f"Steps: {len(result.path)}</text>"
        )

        for floor in floors:
            origin_x, origin_y = panel_origin(floor)
            chunks.append(
                f"<rect x='{origin_x}' y='{origin_y}' width='{panel_width}' height='{header + panel_height}' "
                "fill='#ffffff' stroke='#c4d2df' stroke-width='1' />"
            )
            chunks.append(
                f"<text x='{origin_x + 10}' y='{origin_y + 20}' font-family='Arial, sans-serif' "
                f"font-size='14' fill='#17324a'>Floor {floor}</text>"
            )
            for rect in rects_by_floor[floor]:
                fill, stroke = colors.get(rect.kind, ("#eef0f2", "#6b7280"))
                rx, ry = map_xy(floor, rect.x, rect.y)
                rw = max(2.0, rect.width * scale)
                rh = max(2.0, rect.height * scale)
                chunks.append(
                    f"<rect x='{rx}' y='{ry}' width='{rw}' height='{rh}' fill='{fill}' "
                    f"stroke='{stroke}' stroke-width='1.2' />"
                )
                if rw >= 56 and rh >= 20 and rect.name:
                    chunks.append(
                        f"<text x='{rx + 3}' y='{ry + 13}' font-family='Arial, sans-serif' "
                        f"font-size='10' fill='#1f2d3a'>{escape(rect.name)}</text>"
                    )

        route_nodes: List[Tuple[str, float, float, int]] = []
        default_floor = floors[0]
        for node_name in result.path:
            pos = state_positions.get(node_name)
            if pos is None:
                continue
            floor = pos.floor if pos.floor in floor_index else default_floor
            route_nodes.append((node_name, pos.x, pos.y, floor))

        for idx in range(1, len(route_nodes)):
            _, x1, y1, floor1 = route_nodes[idx - 1]
            _, x2, y2, floor2 = route_nodes[idx]
            px1, py1 = map_xy(floor1, x1, y1)
            px2, py2 = map_xy(floor2, x2, y2)
            if floor1 == floor2:
                chunks.append(
                    f"<line x1='{px1}' y1='{py1}' x2='{px2}' y2='{py2}' "
                    "stroke='#cc1f1a' stroke-width='3' marker-end='url(#routeArrow)' />"
                )
            else:
                chunks.append(f"<circle cx='{px1}' cy='{py1}' r='5' fill='#cc1f1a' />")
                chunks.append(f"<circle cx='{px2}' cy='{py2}' r='5' fill='#cc1f1a' />")
                chunks.append(
                    f"<text x='{px1 + 7}' y='{py1 - 7}' font-family='Arial, sans-serif' "
                    f"font-size='10' fill='#cc1f1a'>to F{floor2}</text>"
                )

        for idx, (name, x, y, floor) in enumerate(route_nodes, start=1):
            px, py = map_xy(floor, x, y)
            chunks.append(
                f"<circle cx='{px}' cy='{py}' r='7' fill='#cc1f1a' stroke='#8f1612' stroke-width='1' />"
            )
            chunks.append(
                f"<text x='{px}' y='{py + 3}' text-anchor='middle' font-family='Arial, sans-serif' "
                f"font-size='9' fill='#ffffff'>{idx}</text>"
            )
            if idx == 1 or idx == len(route_nodes):
                chunks.append(
                    f"<text x='{px + 10}' y='{py + 4}' font-family='Arial, sans-serif' "
                    f"font-size='10' fill='#1f2d3a'>{escape(name)}</text>"
                )

        chunks.append("</svg>")
        return "".join(chunks)

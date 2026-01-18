#!/usr/bin/env python3
"""
Edit Grafana dashboard JSON panels with small, focused commands.

This script expects a top-level "panels" list (grid layout dashboards).
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

Dashboard = Dict[str, Any]
Panel = Dict[str, Any]


def load_json(path: Path) -> Any:
    """Load JSON from disk."""
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, obj: Any, *, sort_keys: bool = True) -> None:
    """Write JSON to disk with stable formatting."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(obj, handle, indent=2, sort_keys=sort_keys, ensure_ascii=False)
        handle.write("\n")


def load_dashboard(path: Path) -> Tuple[Dashboard, Any, bool]:
    """Load a dashboard or payload JSON and return (dashboard, wrapper, uses_wrapper)."""
    data = load_json(path)
    if isinstance(data, dict) and isinstance(data.get("dashboard"), dict):
        dashboard = data["dashboard"]
        if isinstance(dashboard.get("panels"), list):
            normalize_ids(dashboard)
        return dashboard, data, True
    if isinstance(data, dict):
        dashboard = data
        if isinstance(dashboard.get("panels"), list):
            normalize_ids(dashboard)
        return dashboard, data, False
    raise RuntimeError("Unsupported JSON structure; expected a dashboard object.")


def write_dashboard(path: Path, dashboard: Dashboard, wrapper: Any, uses_wrapper: bool) -> None:
    """Write a dashboard or payload JSON back to disk."""
    if uses_wrapper:
        wrapper["dashboard"] = dashboard
        write_json(path, wrapper)
    else:
        write_json(path, dashboard)


def get_panels(dashboard: Dashboard) -> List[Panel]:
    """Return the top-level panels list."""
    panels = dashboard.get("panels")
    if not isinstance(panels, list):
        raise RuntimeError("Dashboard has no top-level 'panels' list.")
    return panels


def _iter_panels_with_context(
    panels: List[Panel],
    row_path: List[str],
) -> List[Tuple[Panel, List[Panel], List[str]]]:
    entries: List[Tuple[Panel, List[Panel], List[str]]] = []
    for panel in panels:
        entries.append((panel, panels, row_path))
        nested = panel.get("panels")
        if isinstance(nested, list) and nested:
            next_path = row_path
            if panel.get("type") == "row" and isinstance(panel.get("title"), str):
                next_path = row_path + [panel["title"]]
            entries.extend(_iter_panels_with_context(nested, next_path))
    return entries


def iter_panels_with_context(dashboard: Dashboard) -> List[Tuple[Panel, List[Panel], List[str]]]:
    """Return panels with their container list and row path."""
    return _iter_panels_with_context(get_panels(dashboard), [])


def iter_panels(dashboard: Dashboard) -> List[Panel]:
    """Return all panels, including nested row panels."""
    return [panel for panel, _, _ in iter_panels_with_context(dashboard)]


def next_panel_id(dashboard: Dashboard) -> int:
    """Return the next available panel id."""
    max_id = 0
    for panel in iter_panels(dashboard):
        panel_id = panel.get("id")
        if isinstance(panel_id, int):
            max_id = max(max_id, panel_id)
    return max_id + 1


def clone_panel(
    panel: Panel,
    *,
    new_id: int,
    dx: int = 0,
    dy: int = 0,
    title_suffix: str = " (copy)",
) -> Panel:
    """Deep-copy a panel and adjust id, title, and grid position."""
    cloned = copy.deepcopy(panel)
    cloned["id"] = new_id

    if isinstance(cloned.get("title"), str) and title_suffix:
        cloned["title"] = cloned["title"] + title_suffix

    grid_pos = cloned.get("gridPos")
    if not isinstance(grid_pos, dict):
        grid_pos = {}
        cloned["gridPos"] = grid_pos
    grid_pos["x"] = int(grid_pos.get("x", 0)) + dx
    grid_pos["y"] = int(grid_pos.get("y", 0)) + dy
    grid_pos["w"] = int(grid_pos.get("w", 12))
    grid_pos["h"] = int(grid_pos.get("h", 8))
    return cloned


def find_panel(
    dashboard: Dashboard,
    *,
    panel_id: Optional[int] = None,
    title: Optional[str] = None,
) -> Panel:
    """Find a single panel by id or exact title."""
    panel, _ = find_panel_with_container(dashboard, panel_id=panel_id, title=title)
    return panel


def find_panel_with_container(
    dashboard: Dashboard,
    *,
    panel_id: Optional[int] = None,
    title: Optional[str] = None,
) -> Tuple[Panel, List[Panel]]:
    """Find a panel and return it with its container list."""
    if panel_id is None and title is None:
        raise RuntimeError("Provide panel_id or title.")
    for panel, container, _ in iter_panels_with_context(dashboard):
        if panel_id is not None and panel.get("id") == panel_id:
            return panel, container
        if title is not None and panel.get("title") == title:
            return panel, container
    raise RuntimeError(f"Panel not found (id={panel_id}, title={title!r}).")


def add_panel(dashboard: Dashboard, panel: Panel) -> None:
    """Append a panel to the dashboard."""
    get_panels(dashboard).append(panel)


def move_panel(
    panel: Panel,
    *,
    x: Optional[int] = None,
    y: Optional[int] = None,
    w: Optional[int] = None,
    h: Optional[int] = None,
) -> None:
    """Move or resize a panel in place."""
    grid_pos = panel.setdefault("gridPos", {})
    if x is not None:
        grid_pos["x"] = int(x)
    if y is not None:
        grid_pos["y"] = int(y)
    if w is not None:
        grid_pos["w"] = int(w)
    if h is not None:
        grid_pos["h"] = int(h)


def swap_panels(a: Panel, b: Panel) -> None:
    """Swap grid positions of two panels."""
    a_pos = copy.deepcopy(a.get("gridPos", {}))
    b_pos = copy.deepcopy(b.get("gridPos", {}))
    a["gridPos"] = b_pos
    b["gridPos"] = a_pos


def normalize_ids(dashboard: Dashboard) -> None:
    """Ensure all panels have unique integer ids."""
    used: set[int] = set()
    new_id = 1
    for panel in iter_panels(dashboard):
        panel_id = panel.get("id")
        if not isinstance(panel_id, int) or panel_id in used:
            while new_id in used:
                new_id += 1
            panel["id"] = new_id
            panel_id = new_id
        used.add(panel_id)


def reflow_rows(dashboard: Dashboard, *, y0: int = 0, padding: int = 0) -> None:
    """Reflow panels by stacking them in increasing y order."""
    panels = get_panels(dashboard)
    panels.sort(
        key=lambda panel: (
            int(panel.get("gridPos", {}).get("y", 0)),
            int(panel.get("gridPos", {}).get("x", 0)),
        )
    )

    y = y0
    for panel in panels:
        grid_pos = panel.setdefault("gridPos", {})
        height = int(grid_pos.get("h", 8))
        grid_pos["y"] = y
        y += height + padding


def duplicate_panel(
    dashboard: Dashboard,
    *,
    panel_id: Optional[int] = None,
    title: Optional[str] = None,
    dx: int = 0,
    dy: int = 0,
    title_suffix: str = " (copy)",
) -> Panel:
    """Duplicate a panel, assign a fresh id, and append it."""
    original, container = find_panel_with_container(dashboard, panel_id=panel_id, title=title)
    new_id = next_panel_id(dashboard)
    dup = clone_panel(original, new_id=new_id, dx=dx, dy=dy, title_suffix=title_suffix)
    container.append(dup)
    return dup


def _resolve_output_path(args: argparse.Namespace, input_path: Path) -> Path:
    if args.in_place:
        return input_path
    if args.output:
        return Path(args.output)
    raise RuntimeError("Provide --output or --in-place for write commands.")


def _panel_summary(panel: Panel) -> Dict[str, Any]:
    grid_pos = panel.get("gridPos", {})
    return {
        "id": panel.get("id"),
        "title": panel.get("title"),
        "gridPos": {
            "x": grid_pos.get("x"),
            "y": grid_pos.get("y"),
            "w": grid_pos.get("w"),
            "h": grid_pos.get("h"),
        },
    }


def _list_command(args: argparse.Namespace) -> int:
    dashboard, _, _ = load_dashboard(Path(args.input))
    entries = iter_panels_with_context(dashboard)

    if args.format == "json":
        items = []
        for panel, _, row_path in entries:
            summary = _panel_summary(panel)
            if row_path:
                summary["rowPath"] = row_path
            items.append(summary)
        print(json.dumps(items, indent=2))
        return 0

    print("id\ttitle\tx,y,w,h\trow")
    for panel, _, row_path in entries:
        summary = _panel_summary(panel)
        grid_pos = summary["gridPos"]
        title = summary.get("title") or ""
        row_label = " / ".join(row_path) if row_path else ""
        print(
            f"{summary.get('id')}\t{title}\t"
            f"{grid_pos.get('x')},{grid_pos.get('y')},"
            f"{grid_pos.get('w')},{grid_pos.get('h')}\t"
            f"{row_label}"
        )
    return 0


def _duplicate_command(args: argparse.Namespace) -> int:
    dashboard, wrapper, uses_wrapper = load_dashboard(Path(args.input))
    dup = duplicate_panel(
        dashboard,
        panel_id=args.id,
        title=args.title,
        dx=args.dx,
        dy=args.dy,
        title_suffix=args.title_suffix,
    )
    out_path = _resolve_output_path(args, Path(args.input))
    write_dashboard(out_path, dashboard, wrapper, uses_wrapper)
    print(f"Duplicated panel to id {dup.get('id')}.")
    return 0


def _move_command(args: argparse.Namespace) -> int:
    dashboard, wrapper, uses_wrapper = load_dashboard(Path(args.input))
    panel = find_panel(dashboard, panel_id=args.id, title=args.title)
    move_panel(panel, x=args.x, y=args.y, w=args.w, h=args.h)
    out_path = _resolve_output_path(args, Path(args.input))
    write_dashboard(out_path, dashboard, wrapper, uses_wrapper)
    print(f"Moved panel id {panel.get('id')}.")
    return 0


def _swap_command(args: argparse.Namespace) -> int:
    dashboard, wrapper, uses_wrapper = load_dashboard(Path(args.input))
    panel_a = find_panel(dashboard, panel_id=args.a_id, title=args.a_title)
    panel_b = find_panel(dashboard, panel_id=args.b_id, title=args.b_title)
    swap_panels(panel_a, panel_b)
    out_path = _resolve_output_path(args, Path(args.input))
    write_dashboard(out_path, dashboard, wrapper, uses_wrapper)
    print(f"Swapped panels {panel_a.get('id')} and {panel_b.get('id')}.")
    return 0


def _reflow_command(args: argparse.Namespace) -> int:
    dashboard, wrapper, uses_wrapper = load_dashboard(Path(args.input))
    reflow_rows(dashboard, y0=args.y0, padding=args.padding)
    out_path = _resolve_output_path(args, Path(args.input))
    write_dashboard(out_path, dashboard, wrapper, uses_wrapper)
    print("Reflowed panel layout.")
    return 0


def _normalize_ids_command(args: argparse.Namespace) -> int:
    dashboard, wrapper, uses_wrapper = load_dashboard(Path(args.input))
    normalize_ids(dashboard)
    out_path = _resolve_output_path(args, Path(args.input))
    write_dashboard(out_path, dashboard, wrapper, uses_wrapper)
    print("Normalized panel ids.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="View and edit Grafana dashboard panels.",
    )
    parser.add_argument("--input", required=True, help="Input dashboard JSON file")
    output_group = parser.add_mutually_exclusive_group()
    output_group.add_argument("--output", default=None, help="Write output JSON to this path")
    output_group.add_argument("--in-place", action="store_true", help="Overwrite the input file")

    subparsers = parser.add_subparsers(dest="command", required=True)

    list_cmd = subparsers.add_parser("list", help="List panels")
    list_cmd.add_argument("--format", choices=["text", "json"], default="text")
    list_cmd.set_defaults(func=_list_command)

    duplicate = subparsers.add_parser("duplicate", help="Duplicate a panel")
    duplicate_sel = duplicate.add_mutually_exclusive_group(required=True)
    duplicate_sel.add_argument("--id", type=int, help="Panel id to duplicate")
    duplicate_sel.add_argument("--title", help="Panel title to duplicate")
    duplicate.add_argument("--dx", type=int, default=0, help="Offset x after duplication")
    duplicate.add_argument("--dy", type=int, default=0, help="Offset y after duplication")
    duplicate.add_argument("--title-suffix", default=" (copy)", help="Title suffix for the clone")
    duplicate.set_defaults(func=_duplicate_command)

    move = subparsers.add_parser("move", help="Move or resize a panel")
    move_sel = move.add_mutually_exclusive_group(required=True)
    move_sel.add_argument("--id", type=int, help="Panel id to move")
    move_sel.add_argument("--title", help="Panel title to move")
    move.add_argument("--x", type=int, default=None)
    move.add_argument("--y", type=int, default=None)
    move.add_argument("--w", type=int, default=None)
    move.add_argument("--h", type=int, default=None)
    move.set_defaults(func=_move_command)

    swap = subparsers.add_parser("swap", help="Swap two panels")
    swap_a = swap.add_mutually_exclusive_group(required=True)
    swap_a.add_argument("--a-id", type=int, help="First panel id")
    swap_a.add_argument("--a-title", help="First panel title")
    swap_b = swap.add_mutually_exclusive_group(required=True)
    swap_b.add_argument("--b-id", type=int, help="Second panel id")
    swap_b.add_argument("--b-title", help="Second panel title")
    swap.set_defaults(func=_swap_command)

    reflow = subparsers.add_parser("reflow", help="Stack panels in order to avoid overlaps")
    reflow.add_argument("--y0", type=int, default=0, help="Starting y position")
    reflow.add_argument("--padding", type=int, default=0, help="Padding between panels")
    reflow.set_defaults(func=_reflow_command)

    normalize = subparsers.add_parser("normalize-ids", help="Ensure unique panel ids")
    normalize.set_defaults(func=_normalize_ids_command)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""
Download or upload Grafana dashboards via the HTTP API.

Requires: pip install requests
Auth: set GRAFANA_TOKEN env var or pass --token
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Optional

import requests


def _join_url(base: str, path: str) -> str:
    return base.rstrip("/") + "/" + path.lstrip("/")


def fetch_dashboard(
    base_url: str,
    token: str,
    uid: str,
    *,
    verify: bool = True,
    timeout: int = 30,
) -> Dict[str, Any]:
    """Fetch a dashboard by UID from Grafana."""
    url = _join_url(base_url, f"/api/dashboards/uid/{uid}")
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    response = requests.get(url, headers=headers, timeout=timeout, verify=verify)
    if response.status_code != 200:
        raise RuntimeError(f"GET {url} failed: {response.status_code} {response.text[:500]}")
    return response.json()


def get_json(
    base_url: str,
    token: str,
    path: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    verify: bool = True,
    timeout: int = 30,
) -> Any:
    """Perform a GET request and return parsed JSON."""
    url = _join_url(base_url, path)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    response = requests.get(url, headers=headers, params=params, timeout=timeout, verify=verify)
    if response.status_code != 200:
        raise RuntimeError(f"GET {url} failed: {response.status_code} {response.text[:500]}")
    return response.json()


def safe_cleanup_dashboard(
    dashboard: Dict[str, Any],
    *,
    keep_uid: bool = True,
    null_id: bool = True,
    reset_version: bool = True,
    drop_iteration: bool = True,
    strip_panel_ids: bool = False,
) -> Dict[str, Any]:
    """Conservative cleanup for portability while preserving UID for overwrites."""
    cleaned = deepcopy(dashboard)

    uid = cleaned.get("uid")
    if not keep_uid:
        cleaned.pop("uid", None)
    elif uid is not None:
        cleaned["uid"] = uid

    if null_id:
        cleaned["id"] = None

    if reset_version:
        cleaned["version"] = 0

    if drop_iteration:
        cleaned.pop("iteration", None)

    if strip_panel_ids:
        def _strip_panels(obj: Any) -> None:
            if isinstance(obj, dict):
                if "panels" in obj and isinstance(obj["panels"], list):
                    for panel in obj["panels"]:
                        if isinstance(panel, dict):
                            panel.pop("id", None)
                            _strip_panels(panel)
                for value in obj.values():
                    _strip_panels(value)
            elif isinstance(obj, list):
                for value in obj:
                    _strip_panels(value)

        _strip_panels(cleaned)

    return cleaned


def build_update_payload(
    dashboard: Dict[str, Any],
    *,
    overwrite: bool = True,
    message: str = "Updated via API script",
    folder_uid: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the payload expected by POST /api/dashboards/db."""
    payload: Dict[str, Any] = {
        "dashboard": dashboard,
        "overwrite": overwrite,
        "message": message,
    }
    if folder_uid:
        payload["folderUid"] = folder_uid
    return payload


def write_pretty_json(path: Path, obj: Any, *, sort_keys: bool = True) -> None:
    """Write JSON to disk with stable formatting."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(obj, handle, indent=2, sort_keys=sort_keys, ensure_ascii=False)
        handle.write("\n")


def load_json(path: Path) -> Any:
    """Read a JSON file from disk."""
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def upload_dashboard(
    base_url: str,
    token: str,
    payload: Dict[str, Any],
    *,
    verify: bool = True,
    timeout: int = 30,
) -> Dict[str, Any]:
    """POST a dashboard payload to Grafana."""
    url = _join_url(base_url, "/api/dashboards/db")
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    response = requests.post(url, headers=headers, json=payload, timeout=timeout, verify=verify)
    if response.status_code not in (200, 202):
        raise RuntimeError(f"POST {url} failed: {response.status_code} {response.text[:500]}")
    return response.json()


def delete_dashboard(
    base_url: str,
    token: str,
    uid: str,
    *,
    verify: bool = True,
    timeout: int = 30,
) -> Dict[str, Any]:
    """Delete a dashboard by UID."""
    url = _join_url(base_url, f"/api/dashboards/uid/{uid}")
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    response = requests.delete(url, headers=headers, timeout=timeout, verify=verify)
    if response.status_code not in (200, 202):
        raise RuntimeError(f"DELETE {url} failed: {response.status_code} {response.text[:500]}")
    return response.json()


def _ensure_token(token: Optional[str]) -> str:
    if not token:
        raise RuntimeError("Missing token. Pass --token or set GRAFANA_TOKEN.")
    return token


def _resolve_dashboard_uid(
    base_url: str,
    token: str,
    name: str,
    *,
    folder_id: Optional[int] = None,
    verify: bool = True,
    timeout: int = 30,
) -> str:
    params: Dict[str, Any] = {"type": "dash-db", "query": name}
    if folder_id is not None:
        params["folderIds"] = str(folder_id)
    items = get_json(base_url, token, "/api/search", params=params, verify=verify, timeout=timeout)
    matches = [item for item in items if item.get("title") == name]
    if not matches and items:
        matches = items
    if not matches:
        raise RuntimeError(f"No dashboards found matching name {name!r}")
    if len(matches) > 1:
        summary = ", ".join(
            f"{item.get('title')} (uid={item.get('uid')}, folder={item.get('folderTitle')})"
            for item in matches[:5]
        )
        raise RuntimeError(f"Multiple dashboards match name {name!r}: {summary}")
    uid = matches[0].get("uid")
    if not uid:
        raise RuntimeError(f"Dashboard match is missing uid for name {name!r}")
    return uid


def _dashboard_uid_from_args(
    base_url: str,
    token: str,
    args: argparse.Namespace,
    *,
    verify: bool,
    timeout: int,
) -> str:
    uid = getattr(args, "uid", None)
    if uid is not None:
        return uid
    name = getattr(args, "name", None)
    if name is not None:
        folder_id = getattr(args, "folder_id", None)
        return _resolve_dashboard_uid(
            base_url,
            token,
            name,
            folder_id=folder_id,
            verify=verify,
            timeout=timeout,
        )
    raise RuntimeError("Provide --uid or --name for the dashboard.")


def _download_command(args: argparse.Namespace) -> int:
    token = _ensure_token(args.token)
    verify = not args.no_verify_ssl

    uid = _dashboard_uid_from_args(
        args.base_url,
        token,
        args,
        verify=verify,
        timeout=args.timeout,
    )
    blob = fetch_dashboard(args.base_url, token, uid, verify=verify, timeout=args.timeout)
    if "dashboard" not in blob:
        print(f"ERROR: unexpected response keys: {list(blob.keys())}", file=sys.stderr)
        return 3

    dashboard = blob["dashboard"]

    if args.no_clean:
        cleaned = dashboard
    else:
        cleaned = safe_cleanup_dashboard(
            dashboard,
            keep_uid=True,
            null_id=not args.keep_id,
            reset_version=not args.keep_version,
            strip_panel_ids=args.strip_panel_ids,
        )

    out_path = Path(args.out or f"dashboard_{uid}.json")
    write_pretty_json(out_path, cleaned)

    if args.out_payload:
        payload = build_update_payload(
            cleaned,
            overwrite=True,
            message=args.message,
            folder_uid=args.folder_uid,
        )
        write_pretty_json(Path(args.out_payload), payload)

    print(f"Wrote dashboard JSON to: {out_path}")
    if args.out_payload:
        print(f"Wrote API update payload to: {args.out_payload}")
    return 0


def _list_dashboards_command(args: argparse.Namespace) -> int:
    token = _ensure_token(args.token)
    verify = not args.no_verify_ssl

    params: Dict[str, Any] = {"type": "dash-db"}
    if args.query:
        params["query"] = args.query
    for tag in args.tag:
        params.setdefault("tag", []).append(tag)
    if args.folder_id is not None:
        params["folderIds"] = str(args.folder_id)
    if args.limit is not None:
        params["limit"] = args.limit

    items = get_json(args.base_url, token, "/api/search", params=params, verify=verify, timeout=args.timeout)
    print(json.dumps(items, indent=2))
    return 0


def _folders_command(args: argparse.Namespace) -> int:
    token = _ensure_token(args.token)
    verify = not args.no_verify_ssl

    params: Dict[str, Any] = {}
    if args.query:
        params["query"] = args.query
    if args.limit is not None:
        params["limit"] = args.limit
    if args.page is not None:
        params["page"] = args.page

    folders = get_json(args.base_url, token, "/api/folders", params=params, verify=verify, timeout=args.timeout)
    print(json.dumps(folders, indent=2))
    return 0


def _tags_command(args: argparse.Namespace) -> int:
    token = _ensure_token(args.token)
    verify = not args.no_verify_ssl

    params: Dict[str, Any] = {}
    if args.query:
        params["query"] = args.query
    if args.limit is not None:
        params["limit"] = args.limit

    tags = get_json(args.base_url, token, "/api/dashboards/tags", params=params, verify=verify, timeout=args.timeout)
    print(json.dumps(tags, indent=2))
    return 0


def _folder_command(args: argparse.Namespace) -> int:
    token = _ensure_token(args.token)
    verify = not args.no_verify_ssl

    folder = get_json(
        args.base_url,
        token,
        f"/api/folders/{args.uid}",
        verify=verify,
        timeout=args.timeout,
    )
    print(json.dumps(folder, indent=2))
    return 0


def _dashboard_permissions_command(args: argparse.Namespace) -> int:
    token = _ensure_token(args.token)
    verify = not args.no_verify_ssl

    uid = _dashboard_uid_from_args(
        args.base_url,
        token,
        args,
        verify=verify,
        timeout=args.timeout,
    )
    perms = get_json(
        args.base_url,
        token,
        f"/api/dashboards/uid/{uid}/permissions",
        verify=verify,
        timeout=args.timeout,
    )
    print(json.dumps(perms, indent=2))
    return 0


def _folder_permissions_command(args: argparse.Namespace) -> int:
    token = _ensure_token(args.token)
    verify = not args.no_verify_ssl

    perms = get_json(
        args.base_url,
        token,
        f"/api/folders/{args.uid}/permissions",
        verify=verify,
        timeout=args.timeout,
    )
    print(json.dumps(perms, indent=2))
    return 0


def _versions_command(args: argparse.Namespace) -> int:
    token = _ensure_token(args.token)
    verify = not args.no_verify_ssl

    uid = _dashboard_uid_from_args(
        args.base_url,
        token,
        args,
        verify=verify,
        timeout=args.timeout,
    )
    params: Dict[str, Any] = {}
    if args.limit is not None:
        params["limit"] = args.limit
    if args.start is not None:
        params["start"] = args.start

    versions = get_json(
        args.base_url,
        token,
        f"/api/dashboards/uid/{uid}/versions",
        params=params,
        verify=verify,
        timeout=args.timeout,
    )
    print(json.dumps(versions, indent=2))
    return 0


def _delete_command(args: argparse.Namespace) -> int:
    token = _ensure_token(args.token)
    verify = not args.no_verify_ssl

    if not args.force:
        raise RuntimeError("Refusing to delete without --force")

    uid = _dashboard_uid_from_args(
        args.base_url,
        token,
        args,
        verify=verify,
        timeout=args.timeout,
    )
    result = delete_dashboard(args.base_url, token, uid, verify=verify, timeout=args.timeout)
    print(json.dumps(result, indent=2))
    return 0


def _upload_command(args: argparse.Namespace) -> int:
    token = _ensure_token(args.token)
    verify = not args.no_verify_ssl

    payload_path = Path(args.payload) if args.payload else None
    dashboard_path = Path(args.dashboard) if args.dashboard else None

    if payload_path is None and dashboard_path is None:
        raise RuntimeError("Provide --payload or --dashboard for upload.")

    if payload_path:
        payload = load_json(payload_path)
    else:
        dashboard = load_json(dashboard_path)
        if not args.no_clean:
            dashboard = safe_cleanup_dashboard(
                dashboard,
                keep_uid=True,
                null_id=not args.keep_id,
                reset_version=not args.keep_version,
                strip_panel_ids=args.strip_panel_ids,
            )
        payload = build_update_payload(
            dashboard,
            overwrite=not args.no_overwrite,
            message=args.message,
            folder_uid=args.folder_uid,
        )

    result = upload_dashboard(args.base_url, token, payload, verify=verify, timeout=args.timeout)
    print(json.dumps(result, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage Grafana dashboards via the HTTP API.",
    )
    parser.add_argument("--base-url", required=True, help="Grafana base URL, e.g. https://grafana.example.com")
    parser.add_argument(
        "--token",
        default=os.getenv("GRAFANA_TOKEN"),
        help="Grafana service account token (or set GRAFANA_TOKEN)",
    )
    parser.add_argument("--no-verify-ssl", action="store_true", help="Disable TLS certificate verification")
    parser.add_argument("--timeout", type=int, default=30)

    subparsers = parser.add_subparsers(dest="command", required=True)

    download = subparsers.add_parser("download", help="Download a dashboard by UID or name")
    download_lookup = download.add_mutually_exclusive_group(required=True)
    download_lookup.add_argument("--uid", help="Dashboard UID (from the dashboard URL)")
    download_lookup.add_argument("--name", help="Dashboard title to search for")
    download.add_argument("--folder-id", type=int, default=None, help="Folder id for name lookup")
    download.add_argument("--out", default=None, help="Output file for dashboard JSON")
    download.add_argument("--out-payload", default=None, help="Output file for POST payload JSON")
    download.add_argument("--no-clean", action="store_true", help="Do not modify dashboard JSON")
    download.add_argument("--keep-id", action="store_true", help="Keep dashboard id")
    download.add_argument("--keep-version", action="store_true", help="Keep dashboard version")
    download.add_argument("--strip-panel-ids", action="store_true", help="Remove panel ids")
    download.add_argument("--folder-uid", default=None, help="Folder UID for the update payload")
    download.add_argument("--message", default="Updated via API script", help="Commit message for updates")
    download.set_defaults(func=_download_command)

    list_cmd = subparsers.add_parser("list", help="List dashboards (Grafana search)")
    list_cmd.add_argument("--query", default=None, help="Search query")
    list_cmd.add_argument("--tag", action="append", default=[], help="Filter by tag (repeatable)")
    list_cmd.add_argument("--folder-id", type=int, default=None, help="Filter by folder id")
    list_cmd.add_argument("--limit", type=int, default=None, help="Limit result size")
    list_cmd.set_defaults(func=_list_dashboards_command)

    folders = subparsers.add_parser("folders", help="List folders")
    folders.add_argument("--query", default=None, help="Search query")
    folders.add_argument("--limit", type=int, default=None, help="Limit result size")
    folders.add_argument("--page", type=int, default=None, help="Page number for folder listing")
    folders.set_defaults(func=_folders_command)

    tags = subparsers.add_parser("tags", help="List dashboard tags")
    tags.add_argument("--query", default=None, help="Search query")
    tags.add_argument("--limit", type=int, default=None, help="Limit result size")
    tags.set_defaults(func=_tags_command)

    folder = subparsers.add_parser("folder", help="Get folder by UID")
    folder.add_argument("--uid", required=True, help="Folder UID")
    folder.set_defaults(func=_folder_command)

    permissions = subparsers.add_parser("permissions", help="Get dashboard permissions by UID or name")
    permissions_lookup = permissions.add_mutually_exclusive_group(required=True)
    permissions_lookup.add_argument("--uid", help="Dashboard UID")
    permissions_lookup.add_argument("--name", help="Dashboard title to search for")
    permissions.add_argument("--folder-id", type=int, default=None, help="Folder id for name lookup")
    permissions.set_defaults(func=_dashboard_permissions_command)

    folder_permissions = subparsers.add_parser("folder-permissions", help="Get folder permissions by UID")
    folder_permissions.add_argument("--uid", required=True, help="Folder UID")
    folder_permissions.set_defaults(func=_folder_permissions_command)

    versions = subparsers.add_parser("versions", help="List dashboard versions by UID or name")
    versions_lookup = versions.add_mutually_exclusive_group(required=True)
    versions_lookup.add_argument("--uid", help="Dashboard UID")
    versions_lookup.add_argument("--name", help="Dashboard title to search for")
    versions.add_argument("--folder-id", type=int, default=None, help="Folder id for name lookup")
    versions.add_argument("--limit", type=int, default=None, help="Limit result size")
    versions.add_argument("--start", type=int, default=None, help="Start offset")
    versions.set_defaults(func=_versions_command)

    delete = subparsers.add_parser("delete", help="Delete a dashboard by UID or name")
    delete_lookup = delete.add_mutually_exclusive_group(required=True)
    delete_lookup.add_argument("--uid", help="Dashboard UID")
    delete_lookup.add_argument("--name", help="Dashboard title to search for")
    delete.add_argument("--folder-id", type=int, default=None, help="Folder id for name lookup")
    delete.add_argument("--force", action="store_true", help="Confirm deletion")
    delete.set_defaults(func=_delete_command)

    upload = subparsers.add_parser("upload", help="Upload a dashboard payload or JSON")
    upload.add_argument("--payload", default=None, help="Payload JSON file for POST /api/dashboards/db")
    upload.add_argument("--dashboard", default=None, help="Dashboard JSON file to wrap into a payload")
    upload.add_argument("--no-overwrite", action="store_true", help="Disable overwrite on upload")
    upload.add_argument("--no-clean", action="store_true", help="Do not modify dashboard JSON")
    upload.add_argument("--keep-id", action="store_true", help="Keep dashboard id")
    upload.add_argument("--keep-version", action="store_true", help="Keep dashboard version")
    upload.add_argument("--strip-panel-ids", action="store_true", help="Remove panel ids")
    upload.add_argument("--folder-uid", default=None, help="Folder UID for the update payload")
    upload.add_argument("--message", default="Updated via API script", help="Commit message for updates")
    upload.set_defaults(func=_upload_command)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

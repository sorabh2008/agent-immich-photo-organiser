"""Importable execute entry point for the orchestrator app.

This is the ONLY thing the orchestrator-agent-ui calls in this repo. It does not
touch main.py / scan logic and it never writes plan.json (the orchestrator keeps
plan.json read-only and tracks state itself). It reuses the existing
ImmichClient and mirrors execute.py's album/tag/delete behaviour.

Two operations, both pure (decisions in -> results out):
  validate(decisions) -> re-checks each decision against live Immich (assets
                         still resolve); flags stale ones. No mutations.
  execute(decisions)  -> validates, then applies approved decisions.

Decision shape (from the orchestrator's shared state):
    {"id": "immich:cluster_0007", "source": "immich",
     "disposition": "approve" | "reject",
     "detail": {"cluster_id", "asset_ids", "album_name", "tags",
                "action": "approve" | "delete"}}

Result shape:
    {"id", "status": "executed"|"skipped"|"stale"|"failed"|"ok", "message", "detail"}

CLI (used by the orchestrator via subprocess, run with this repo's venv):
    echo '<decisions-json>' | python execute_api.py validate
    echo '<decisions-json>' | python execute_api.py execute [--dry-run]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from dotenv import load_dotenv

# Resolve this repo's own .env / imports regardless of caller cwd.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
load_dotenv(_HERE / ".env")

from tools.immich import ImmichClient  # noqa: E402


def _asset_exists(client: ImmichClient, asset_id: str) -> bool:
    try:
        resp = client._session.get(
            f"{client.api_base}/assets/{asset_id}", timeout=15
        )
        return resp.status_code == 200
    except Exception:
        return False


def _validate_one(client: ImmichClient, decision: dict) -> tuple[bool, str]:
    """A decision is valid if at least one of its assets still resolves."""
    detail = decision.get("detail", {})
    asset_ids = detail.get("asset_ids", [])
    if not asset_ids:
        return False, "no asset ids on this entry"
    for aid in asset_ids:
        if _asset_exists(client, aid):
            return True, ""
    return False, "all assets in this cluster no longer exist in Immich"


def validate(decisions: list[dict]) -> list[dict]:
    client = ImmichClient()
    results: list[dict] = []
    for d in decisions:
        if d.get("disposition") != "approve":
            results.append({"id": d["id"], "status": "skipped", "message": "rejected"})
            continue
        ok, reason = _validate_one(client, d)
        results.append(
            {
                "id": d["id"],
                "status": "ok" if ok else "stale",
                "message": "" if ok else reason,
            }
        )
    return results


def execute(decisions: list[dict], dry_run: bool = False) -> list[dict]:
    client = ImmichClient()
    results: list[dict] = []

    # Caches mirror execute.py's reuse-or-create behaviour.
    albums_by_name: dict[str, str] | None = None
    tags_by_name: dict[str, str] | None = None

    def _ensure_caches() -> None:
        nonlocal albums_by_name, tags_by_name
        if albums_by_name is None:
            albums_by_name = {a["albumName"].lower(): a["id"] for a in client.list_albums()}
        if tags_by_name is None:
            tags_by_name = {t["name"].lower(): t["id"] for t in client.list_tags()}

    for d in decisions:
        if d.get("disposition") != "approve":
            results.append({"id": d["id"], "status": "skipped", "message": "rejected"})
            continue

        ok, reason = _validate_one(client, d)
        if not ok:
            results.append({"id": d["id"], "status": "stale", "message": reason})
            continue

        detail = d.get("detail", {})
        action = detail.get("action", "approve")
        asset_ids = detail.get("asset_ids", [])

        if dry_run:
            what = (
                f"trash {len(asset_ids)} asset(s)"
                if action == "delete"
                else f"album '{detail.get('album_name')}' + tags {detail.get('tags', [])}"
            )
            results.append({"id": d["id"], "status": "ok", "message": f"dry-run: would {what}"})
            continue

        try:
            if action == "delete":
                client.delete_assets(asset_ids)  # to trash (recoverable)
                results.append(
                    {"id": d["id"], "status": "executed", "message": f"trashed {len(asset_ids)} asset(s)"}
                )
                continue

            # action == "approve": create/reuse album, then create/reuse + apply tags
            _ensure_caches()
            album_name = detail["album_name"]
            tags = detail.get("tags", [])

            album_id = albums_by_name.get(album_name.lower())
            if album_id:
                client.add_assets_to_album(album_id, asset_ids)
            else:
                album = client.create_album(album_name, asset_ids)
                album_id = album["id"]
                albums_by_name[album_name.lower()] = album_id

            tag_ids = []
            for tag_name in tags:
                tid = tags_by_name.get(tag_name.lower())
                if not tid:
                    tag = client.create_tag(tag_name)
                    tid = tag["id"]
                    tags_by_name[tag_name.lower()] = tid
                tag_ids.append(tid)
            if tag_ids:
                client.tag_assets(tag_ids, asset_ids)

            results.append(
                {
                    "id": d["id"],
                    "status": "executed",
                    "message": f"album '{album_name}' ({len(asset_ids)} asset(s))",
                    "detail": {"album_id": album_id},
                }
            )
        except Exception as exc:  # noqa: BLE001 - report, don't crash the batch
            results.append({"id": d["id"], "status": "failed", "message": str(exc)})

    return results


def _main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else "execute"
    dry_run = "--dry-run" in sys.argv[2:]
    decisions = json.load(sys.stdin)
    if command == "validate":
        out = validate(decisions)
    else:
        out = execute(decisions, dry_run=dry_run)
    json.dump(out, sys.stdout)


if __name__ == "__main__":
    _main()

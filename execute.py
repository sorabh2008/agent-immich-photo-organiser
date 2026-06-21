"""
Phase 2 — Execute Approved Clusters

Reads plan.json and processes every entry where status=pending and either:
  - action = "approve"  → create/reuse album, apply tags
  - action = "delete"   → move assets to Immich trash (recoverable)

Legacy entries with approved=true and no action field are treated as "approve".

After each successful entry, status is written back to "executed" so the
script is safe to stop and restart at any time.
"""

import json
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from tools.immich import ImmichClient

# ── Logging setup ─────────────────────────────────────────────────────────────
Path("logs").mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.FileHandler("logs/pipeline.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("execute")

PLAN_FILE = Path("plan.json")


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_plan() -> list:
    if not PLAN_FILE.exists():
        logger.error("ERROR: plan.json not found. Run main.py first to build the plan.")
        sys.exit(1)
    with open(PLAN_FILE) as fh:
        return json.load(fh)


def save_plan(plan: list) -> None:
    with open(PLAN_FILE, "w") as fh:
        json.dump(plan, fh, indent=2)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    logger.info("=" * 60)
    logger.info("PHASE 2: Execute Approved Clusters — starting")
    logger.info("=" * 60)

    immich = ImmichClient()
    plan = load_plan()

    def _resolve_action(entry: dict) -> str | None:
        """Return the effective action for an entry, handling legacy approved=true entries."""
        action = entry.get("action")
        if action in ("approve", "delete"):
            return action
        if action is None and entry.get("approved") is True:
            return "approve"
        return None

    pending = [
        entry for entry in plan
        if _resolve_action(entry) is not None and entry.get("status") == "pending"
    ]

    total = len(pending)
    logger.info("Found %d actionable-pending entry/entries to process.", total)

    if total == 0:
        logger.info(
            'Nothing to do. Open plan.json, set "action": "approve" or "action": "delete" '
            "on clusters you want processed, then re-run this script."
        )
        return

    logger.info("Fetching existing albums and tags from Immich...")
    albums_by_name = {a["albumName"].lower(): a["id"] for a in immich.list_albums()}
    tags_by_name = {t["name"].lower(): t["id"] for t in immich.list_tags()}

    success = 0
    for i, entry in enumerate(pending, start=1):
        action = _resolve_action(entry)
        asset_ids = entry["asset_ids"]

        logger.info("[%d/%d] cluster=%s  action=%s  assets=%d",
                    i, total, entry["cluster_id"], action, len(asset_ids))

        try:
            if action == "delete":
                immich.delete_assets(asset_ids)
                logger.info("  -> Moved %d asset(s) to trash", len(asset_ids))

            elif action == "approve":
                album_name = entry["album_name"]
                tags = entry["tags"]

                # Step A — Create or reuse the album
                album_id = albums_by_name.get(album_name.lower())
                if album_id:
                    immich.add_assets_to_album(album_id, asset_ids)
                    logger.info("  -> Added to existing album '%s'", album_name)
                else:
                    album = immich.create_album(album_name, asset_ids)
                    album_id = album["id"]
                    albums_by_name[album_name.lower()] = album_id
                    logger.info("  -> Created album '%s'", album_name)

                # Step B — Create or reuse each tag, then apply all of them
                tag_ids = []
                for tag_name in tags:
                    tag_id = tags_by_name.get(tag_name.lower())
                    if not tag_id:
                        tag = immich.create_tag(tag_name)
                        tag_id = tag["id"]
                        tags_by_name[tag_name.lower()] = tag_id
                    tag_ids.append(tag_id)

                if tag_ids:
                    immich.tag_assets(tag_ids, asset_ids)
                    logger.info("  -> Applied tags: %s", tags)

                entry["album_id"] = album_id

            # Mark as executed immediately (crash-safe)
            entry["status"] = "executed"
            save_plan(plan)
            success += 1

        except Exception as exc:
            logger.error("  ERROR: cluster %s failed: %s", entry["cluster_id"], exc)

    logger.info("=" * 60)
    logger.info("PHASE 2 complete. %d/%d cluster(s) successfully processed.", success, total)
    if success < total:
        logger.warning(
            "%d cluster(s) failed — check the log above, fix the issue, "
            "and re-run execute.py. Already-executed clusters are skipped automatically.",
            total - success,
        )
    logger.info("=" * 60)


if __name__ == "__main__":
    main()

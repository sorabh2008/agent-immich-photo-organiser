"""
Phase 1 — Scan & Classify

Pulls assets (with EXIF + recognized-people metadata) from Immich, clusters
assets that weren't already covered by a previous run into "event"/"trip"
groups based on date gaps and location changes, then asks a local Ollama
text model to suggest an album name and theme/place tags for each cluster.
Appends one entry per cluster to plan.json with approved=false and
status=pending.

Run this script autonomously; it requires no interactive input.
After it finishes, open plan.json in any text editor, set "approved": true
for the clusters you want applied, then run execute.py.
"""

import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from pipeline.classifier import classify_cluster
from pipeline.cluster import cluster_assets, format_location
from pipeline.vision import aggregate_labels, label_asset
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
logger = logging.getLogger("main")

# ── Config ────────────────────────────────────────────────────────────────────
PLAN_FILE = Path("plan.json")
MAX_ASSETS_PER_RUN = int(os.getenv("MAX_ASSETS_PER_RUN", "1000"))
CLUSTER_GAP_DAYS = int(os.getenv("CLUSTER_GAP_DAYS", "3"))
VISION_SAMPLE_SIZE = int(os.getenv("VISION_SAMPLE_SIZE", "3"))


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_plan() -> list:
    if PLAN_FILE.exists():
        with open(PLAN_FILE) as fh:
            return json.load(fh)
    return []


def save_plan(plan: list) -> None:
    with open(PLAN_FILE, "w") as fh:
        json.dump(plan, fh, indent=2)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    logger.info("=" * 60)
    logger.info("PHASE 1: Scan & Classify — starting")
    logger.info("=" * 60)

    immich = ImmichClient()
    immich_base_url = os.getenv("IMMICH_URL", "").rstrip("/")
    plan = load_plan()
    already_processed = {asset_id for entry in plan for asset_id in entry["asset_ids"]}

    logger.info("Fetching asset list from Immich...")
    try:
        assets = immich.list_assets()
    except Exception as exc:
        logger.error(
            "ERROR: Could not list assets. Is IMMICH_URL/IMMICH_API_KEY correct in .env? Details: %s", exc
        )
        sys.exit(1)

    new_assets = [a for a in assets if a["id"] not in already_processed]
    logger.info(
        "Found %d asset(s) total. %d are new (not yet in plan.json).",
        len(assets), len(new_assets),
    )

    if not new_assets:
        logger.info("Nothing new to process. Exiting.")
        return

    if len(new_assets) > MAX_ASSETS_PER_RUN:
        logger.info(
            "Limiting this run to the first %d of %d new asset(s) (MAX_ASSETS_PER_RUN).",
            MAX_ASSETS_PER_RUN, len(new_assets),
        )
        new_assets = new_assets[:MAX_ASSETS_PER_RUN]

    logger.info("Clustering by date gap (%d day(s)) and location change...", CLUSTER_GAP_DAYS)
    clusters = cluster_assets(new_assets, gap_days=CLUSTER_GAP_DAYS)
    logger.info("Formed %d cluster(s).", len(clusters))

    added = 0
    for i, cluster in enumerate(clusters, start=1):
        start_date = cluster["start_date"].strftime("%Y-%m-%d")
        end_date = cluster["end_date"].strftime("%Y-%m-%d")
        locations = [format_location(loc) for loc in cluster["locations"]]
        people = sorted(cluster["people"])
        asset_ids = cluster["asset_ids"]

        logger.info(
            "--- Cluster %d/%d: %s to %s, %d asset(s), location(s): %s",
            i, len(clusters), start_date, end_date, len(asset_ids), locations or "unknown",
        )

        cluster_summary = {
            "start_date": start_date,
            "end_date": end_date,
            "locations": cluster["locations"],
            "people": people,
            "asset_count": len(asset_ids),
            "sample_filenames": cluster["filenames"][:5],
        }

        vision_labels: list[str] = []
        if os.getenv("OLLAMA_VISION_MODEL", "").strip():
            sample_ids = asset_ids[:VISION_SAMPLE_SIZE]
            per_asset: list[list[str]] = []
            for aid in sample_ids:
                try:
                    img = immich.get_thumbnail(aid)
                    per_asset.append(label_asset(img, debug_label=aid[:8]))
                except Exception as exc:
                    logger.warning("  Could not fetch thumbnail for %s: %s", aid, exc)
            vision_labels = aggregate_labels(per_asset)
            if vision_labels:
                logger.info("  Vision labels (aggregated): %s", vision_labels)

        classification = classify_cluster(
            cluster_summary,
            debug_label=f"{start_date}_{i}",
            vision_labels=vision_labels or None,
        )

        entry = {
            "cluster_id": f"cluster_{len(plan) + added + 1:04d}",
            "asset_ids": asset_ids,
            "sample_asset_urls": [f"{immich_base_url}/photos/{aid}" for aid in asset_ids[:5]],
            "asset_count": len(asset_ids),
            "date_range": [start_date, end_date],
            "locations": locations,
            "people": people,
            "album_name": classification["album_name"],
            "tags": classification["tags"],
            "confidence": round(classification["confidence"], 3),
            "action": None,
            "status": "pending",
        }

        plan.append(entry)
        save_plan(plan)  # write after every cluster so progress survives a crash
        added += 1

        logger.info("  Album : %s  (confidence %.0f%%)", entry["album_name"], entry["confidence"] * 100)
        logger.info("  Tags  : %s", entry["tags"])

    logger.info("=" * 60)
    logger.info("PHASE 1 complete. %d new entry/entries added to plan.json.", added)
    logger.info(
        "Next: open plan.json, set \"approved\": true for clusters you want applied, "
        "then run:  python execute.py"
    )
    logger.info("=" * 60)


if __name__ == "__main__":
    main()

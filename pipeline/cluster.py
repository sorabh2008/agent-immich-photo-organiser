import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


def _parse_date(asset: dict) -> datetime | None:
    exif = asset.get("exifInfo") or {}
    raw = exif.get("dateTimeOriginal") or asset.get("fileCreatedAt") or asset.get("createdAt")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _location(asset: dict) -> tuple[str, str] | None:
    exif = asset.get("exifInfo") or {}
    city, country = exif.get("city"), exif.get("country")
    if not city and not country:
        return None
    return (city or "", country or "")


def _people(asset: dict) -> set[str]:
    return {p["name"] for p in asset.get("people", []) if p.get("name")}


def format_location(location: tuple[str, str]) -> str:
    city, country = location
    return ", ".join(part for part in (city, country) if part)


def cluster_assets(assets: list[dict], gap_days: int = 3) -> list[dict]:
    """Group assets into event/trip clusters by date-gap and location change.

    A new cluster starts whenever the gap since the previous (dated) asset
    exceeds gap_days, or the reverse-geocoded city/country changes between
    two assets that both have location data.

    Assets without a usable date are skipped — they're left for a future run
    once Immich/EXIF metadata is available for them.
    """
    dated = []
    for asset in assets:
        date = _parse_date(asset)
        if date is None:
            logger.debug("Skipping asset %s — no usable date", asset.get("id"))
            continue
        dated.append((date, asset))

    dated.sort(key=lambda pair: pair[0])

    clusters: list[dict] = []
    current: dict | None = None
    prev_date: datetime | None = None
    prev_location: tuple[str, str] | None = None

    for date, asset in dated:
        location = _location(asset)

        starts_new = current is None
        if not starts_new:
            gap = date - prev_date
            location_changed = (
                location is not None
                and prev_location is not None
                and location != prev_location
            )
            starts_new = gap > timedelta(days=gap_days) or location_changed

        if starts_new:
            current = {
                "asset_ids": [],
                "filenames": [],
                "start_date": date,
                "end_date": date,
                "locations": [],
                "people": set(),
            }
            clusters.append(current)

        current["asset_ids"].append(asset["id"])
        current["filenames"].append(asset.get("originalFileName", ""))
        current["end_date"] = date
        if location and location not in current["locations"]:
            current["locations"].append(location)
        current["people"] |= _people(asset)

        prev_date = date
        prev_location = location or prev_location

    return clusters

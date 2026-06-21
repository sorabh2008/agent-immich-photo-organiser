import json
import logging
import os
import re

import requests
from dotenv import load_dotenv

from pipeline.cluster import format_location

load_dotenv()
logger = logging.getLogger(__name__)

_PROMPT = """\
You are organizing a personal photo library. You are given metadata for a
cluster of photos/videos that were taken close together in time and/or in
the same place, extracted automatically from Immich (a self-hosted photo
server).

Suggest a short, human-friendly album name (the kind you'd give a photo album
for friends/family to browse) and a small set of lowercase, kebab-case tags
describing the PLACE, EVENT, or THEME. Do NOT include people's names as tags
— Immich already groups photos by recognized person.

Cluster metadata:
- Date range: {start_date} to {end_date}
- Location(s): {locations}
- People present: {people}
- Number of photos/videos: {asset_count}
- Sample filenames: {sample_filenames}
{vision_section}
Return a JSON object with exactly these 3 keys:

{{
  "album_name": "<short, descriptive album title, e.g. 'Tokyo Trip - Mar 2024' or 'Birthday Party 2025'>",
  "tags": [<2-5 lowercase kebab-case tags describing place/event/theme>],
  "confidence": <float 0.0-1.0, how confident you are given the available metadata>
}}

If location is empty and nothing in the filenames or vision labels is distinctive,
fall back to a date-based name using the EXACT month and year from the Date range
above (e.g. if the date range is 2024-03-15, use "Photos - March 2024") and tags
based only on that date (e.g. "2024", "march"). Do NOT invent a month or year
— use only what appears in the Date range metadata provided above.

Return ONLY the JSON object. No markdown, no explanation, nothing else."""

_VISION_SECTION = """\
- Vision-observed scene/theme labels (from sampled images — prioritise these): {vision_labels}
"""


def classify_cluster(cluster_summary: dict, debug_label: str = "cluster", vision_labels: list[str] | None = None) -> dict:
    """
    Send a cluster's metadata summary to the Ollama text model and parse the
    JSON response. Returns a dict with keys: album_name, tags, confidence.
    Falls back to a date-based name on any error.
    """
    ollama_host = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
    model = os.getenv("OLLAMA_MODEL", "llama3.2")

    vision_section = (
        _VISION_SECTION.format(vision_labels=", ".join(vision_labels))
        if vision_labels
        else ""
    )
    prompt = _PROMPT.format(
        start_date=cluster_summary["start_date"],
        end_date=cluster_summary["end_date"],
        locations=", ".join(format_location(loc) for loc in cluster_summary["locations"]) or "unknown",
        people=", ".join(cluster_summary["people"]) or "none recognized",
        asset_count=cluster_summary["asset_count"],
        sample_filenames=", ".join(cluster_summary["sample_filenames"]) or "none",
        vision_section=vision_section,
    )

    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {"num_predict": 200},
    }

    logger.info("  [Ollama] model=%s  cluster=%s  prompt_chars=%d", model, debug_label, len(prompt))
    logger.debug("  [Ollama] Full prompt:\n%s", prompt)

    try:
        resp = requests.post(f"{ollama_host}/api/generate", json=payload, timeout=120)
        resp.raise_for_status()
        raw = resp.json().get("response", "{}")

        logger.info("  [Ollama] Raw response: %s", raw[:500] + ("…" if len(raw) > 500 else ""))

        raw = re.sub(r"```(?:json)?|```", "", raw).strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("  [Ollama] Response was malformed/truncated JSON — recovering fields via regex.")
            data = {
                "album_name": _extract_field(raw, "album_name"),
                "tags": _extract_tags(raw),
                "confidence": _extract_field(raw, "confidence"),
            }

        result = {
            "album_name": _sanitize_album_name(data.get("album_name")) or _fallback_name(cluster_summary),
            "tags": _sanitize_tags(data.get("tags")),
            "confidence": float(data.get("confidence") or 0.5),
        }
        logger.info("  [Ollama] Parsed → album_name=%s  tags=%s  confidence=%.2f",
                     result["album_name"], result["tags"], result["confidence"])
        return result

    except requests.ConnectionError:
        logger.error(
            "ERROR: Cannot connect to Ollama at %s. Is the home-lab Ollama server reachable?",
            ollama_host,
        )
    except requests.HTTPError as exc:
        logger.error(
            "ERROR: Ollama returned HTTP %s for model '%s'. Response body: %s",
            exc.response.status_code, model, exc.response.text[:1000],
        )
    except Exception as exc:
        logger.error("ERROR: Ollama classification failed: %s", exc)

    return {
        "album_name": _fallback_name(cluster_summary),
        "tags": _fallback_tags(cluster_summary),
        "confidence": 0.0,
    }


def _extract_field(raw: str, field: str) -> str | None:
    match = re.search(rf'"{field}"\s*:\s*"([^"]*)"', raw)
    if match:
        return match.group(1)
    match = re.search(rf'"{field}"\s*:\s*([0-9.]+)', raw)
    if match:
        return match.group(1)
    return None


def _extract_tags(raw: str) -> list[str]:
    match = re.search(r'"tags"\s*:\s*\[(.*?)\]', raw, re.DOTALL)
    if not match:
        return []
    return re.findall(r'"([^"]+)"', match.group(1))


def _sanitize_album_name(value) -> str:
    if not value:
        return ""
    cleaned = re.sub(r"\s+", " ", str(value)).strip()
    return cleaned[:80]


def _sanitize_tags(values) -> list[str]:
    if not isinstance(values, list):
        return []
    tags = []
    for value in values:
        tag = re.sub(r"[^a-z0-9\-]+", "-", str(value).lower()).strip("-")
        if tag and tag not in tags:
            tags.append(tag)
    return tags[:5]


def _fallback_name(cluster_summary: dict) -> str:
    locations = cluster_summary["locations"]
    month_year = cluster_summary["start_date"][:7]  # YYYY-MM
    place = format_location(locations[0]) if locations else None
    return f"{place} - {month_year}" if place else f"Photos - {month_year}"


def _fallback_tags(cluster_summary: dict) -> list[str]:
    year = cluster_summary["start_date"][:4]
    tags = [year]
    for city, country in cluster_summary["locations"]:
        for part in (city, country):
            tag = re.sub(r"[^a-z0-9\-]+", "-", part.lower()).strip("-")
            if tag and tag not in tags:
                tags.append(tag)
    return tags[:5]

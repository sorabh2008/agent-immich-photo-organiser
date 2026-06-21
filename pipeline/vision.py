"""
Vision labelling — sends asset preview images to an Ollama vision model and
returns scene/theme/place tags. Skipped entirely if OLLAMA_VISION_MODEL is
not set, so the pipeline degrades gracefully to text-only mode.
"""

import base64
import json
import logging
import os
import re

import requests
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

_PROMPT = """\
Look at this photo and identify the scene, location, and theme.
Return a JSON object with a single "tags" key containing 3-8 lowercase tags.
Focus on: location type (beach, mountain, city, forest, indoor, park, desert, etc.),
weather/season (snow, sunny, night, rainy, golden-hour, etc.),
activity or event (hiking, swimming, dining, driving, celebration, etc.),
and setting (restaurant, home, car-interior, street, nature, etc.).
Do NOT describe or name people.
Example: {"tags": ["beach", "sunset", "ocean", "summer"]}"""


def label_asset(image_bytes: bytes, debug_label: str = "") -> list[str]:
    """Send a preview image to the Ollama vision model and return scene/theme tags.

    Returns an empty list if OLLAMA_VISION_MODEL is unset or the call fails.
    """
    model = os.getenv("OLLAMA_VISION_MODEL", "").strip()
    if not model:
        return []

    ollama_host = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
    b64 = base64.b64encode(image_bytes).decode()

    payload = {
        "model": model,
        "prompt": _PROMPT,
        "images": [b64],
        "stream": False,
        "format": "json",
        "options": {"num_predict": 150},
    }

    try:
        resp = requests.post(f"{ollama_host}/api/generate", json=payload, timeout=120)
        resp.raise_for_status()
        raw = resp.json().get("response", "").strip()
        raw = re.sub(r"```(?:json)?|```", "", raw).strip()

        tags: list[str] = []
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                tags = data
            elif isinstance(data, dict):
                for key in ("tags", "labels", "scene", "themes"):
                    if isinstance(data.get(key), list):
                        tags = data[key]
                        break
        except json.JSONDecodeError:
            # moondream often returns prose — extract comma/space-separated words
            # after stripping any leading sentence like "I see a ..."
            cleaned = re.sub(r"^[^:]+:", "", raw).strip()
            tags = [w.strip(".,;\"'") for w in re.split(r"[,\n]+", cleaned) if w.strip()]

        result = [str(t).lower().strip() for t in tags if t]
        logger.info("  [Vision] %s → %s", debug_label or "asset", result)
        return result

    except Exception as exc:
        logger.warning("  [Vision] label_asset failed (%s): %s", debug_label or "asset", exc)
        return []


def aggregate_labels(per_asset_labels: list[list[str]], top_n: int = 10) -> list[str]:
    """Return labels ranked by how many assets in the cluster share them."""
    counts: dict[str, int] = {}
    for labels in per_asset_labels:
        for label in labels:
            counts[label] = counts.get(label, 0) + 1
    return [label for label, _ in sorted(counts.items(), key=lambda x: -x[1])][:top_n]

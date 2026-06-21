import logging
import os

import requests
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


class ImmichClient:
    def __init__(self):
        url = os.getenv("IMMICH_URL", "").rstrip("/")
        api_key = os.getenv("IMMICH_API_KEY", "")

        if not all([url, api_key]):
            raise ValueError(
                "ERROR: Missing credentials. Ensure IMMICH_URL and IMMICH_API_KEY "
                "are set in your .env file."
            )

        self.api_base = f"{url}/api"
        self._session = requests.Session()
        self._session.headers.update({"x-api-key": api_key, "Accept": "application/json"})
        logger.debug("ImmichClient connected to %s", url)

    # ── Assets ───────────────────────────────────────────────────────────────

    def list_assets(self, page_size: int = 1000) -> list[dict]:
        """Return all assets (images + videos) with exifInfo and people pre-loaded.

        Uses POST /search/metadata, which returns full AssetResponseDto objects
        (including exifInfo and people) without needing a per-asset follow-up call.
        """
        assets: list[dict] = []
        page = 1
        while True:
            resp = self._session.post(
                f"{self.api_base}/search/metadata",
                json={"page": page, "size": page_size, "withExif": True, "withPeople": True},
                timeout=60,
            )
            resp.raise_for_status()
            data = resp.json().get("assets", {})
            items = data.get("items", [])
            assets.extend(items)

            logger.debug("Fetched page %d (%d assets)", page, len(items))

            if not items or len(items) < page_size or not data.get("nextPage"):
                break
            page += 1

        return assets

    def get_thumbnail(self, asset_id: str, size: str = "preview") -> bytes:
        """Fetch a JPEG preview image for an asset. size: 'thumbnail' (small) or 'preview' (larger)."""
        resp = self._session.get(
            f"{self.api_base}/assets/{asset_id}/thumbnail",
            params={"size": size},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.content

    # ── Albums ───────────────────────────────────────────────────────────────

    def list_albums(self) -> list[dict]:
        resp = self._session.get(f"{self.api_base}/albums", timeout=30)
        resp.raise_for_status()
        return resp.json()

    def create_album(self, album_name: str, asset_ids: list[str], description: str = "") -> dict:
        resp = self._session.post(
            f"{self.api_base}/albums",
            json={"albumName": album_name, "description": description, "assetIds": asset_ids},
            timeout=60,
        )
        resp.raise_for_status()
        logger.info("Created album '%s' with %d asset(s)", album_name, len(asset_ids))
        return resp.json()

    def add_assets_to_album(self, album_id: str, asset_ids: list[str]) -> None:
        resp = self._session.put(
            f"{self.api_base}/albums/{album_id}/assets",
            json={"ids": asset_ids},
            timeout=60,
        )
        resp.raise_for_status()
        logger.info("Added %d asset(s) to album %s", len(asset_ids), album_id)

    # ── Assets (delete) ──────────────────────────────────────────────────────

    def delete_assets(self, asset_ids: list[str], force: bool = False) -> None:
        """Delete assets. force=False sends to Immich trash (recoverable); force=True is permanent."""
        resp = self._session.delete(
            f"{self.api_base}/assets",
            json={"ids": asset_ids, "force": force},
            timeout=30,
        )
        resp.raise_for_status()
        action = "Permanently deleted" if force else "Trashed"
        logger.info("%s %d asset(s)", action, len(asset_ids))

    # ── Tags ─────────────────────────────────────────────────────────────────

    def list_tags(self) -> list[dict]:
        resp = self._session.get(f"{self.api_base}/tags", timeout=30)
        resp.raise_for_status()
        return resp.json()

    def create_tag(self, name: str) -> dict:
        resp = self._session.post(f"{self.api_base}/tags", json={"name": name}, timeout=30)
        resp.raise_for_status()
        logger.info("Created tag '%s'", name)
        return resp.json()

    def tag_assets(self, tag_ids: list[str], asset_ids: list[str]) -> None:
        """Bulk-apply one or more tags to one or more assets."""
        resp = self._session.put(
            f"{self.api_base}/tags/assets",
            json={"tagIds": tag_ids, "assetIds": asset_ids},
            timeout=60,
        )
        resp.raise_for_status()
        logger.info("Applied %d tag(s) to %d asset(s)", len(tag_ids), len(asset_ids))

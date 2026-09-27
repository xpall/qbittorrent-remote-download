"""Minimal async client for the qBittorrent WebUI API.

Supports the subset of the API needed by this app:
authentication, adding torrents, torrent info/files, renaming
files and folders and moving a torrent's storage location.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from config import QbittorrentConfig

logger = logging.getLogger(__name__)

# Param name changed in qBittorrent 5.0; sending both keeps every
# supported version happy because unknown params are ignored.
ADD_STARTED_PARAMS = {"paused": "false", "stopped": "false"}


class QbitError(Exception):
    """Base class for qBittorrent API problems."""


class QbitUnreachable(QbitError):
    """The qBittorrent WebUI could not be reached."""


class QbitAuthError(QbitError):
    """Login failed or this IP is temporarily banned."""


def _error_message(response: httpx.Response) -> str:
    text = response.text.strip()
    try:
        payload = json.loads(text)
    except ValueError:
        return text or f"HTTP {response.status_code}"
    if isinstance(payload, dict):
        for key in ("error", "message", "detail"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
    return text or f"HTTP {response.status_code}"


class QbitClient:
    """Async qBittorrent WebUI API client with cookie based auth."""

    def __init__(self, config: QbittorrentConfig) -> None:
        self.config = config
        self.base_url = config.base_url
        self._http = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(config.request_timeout, connect=5.0),
        )
        self._logged_in = False

    @property
    def _headers(self) -> dict[str, str]:
        # qBittorrent validates the Referer/Origin against the Host.
        return {"Referer": self.base_url, "Origin": self.base_url}

    async def close(self) -> None:
        await self._http.aclose()

    async def login(self) -> None:
        try:
            response = await self._http.post(
                "/api/v2/auth/login",
                data={"username": self.config.username, "password": self.config.password},
                headers=self._headers,
            )
        except httpx.HTTPError as exc:
            raise QbitUnreachable(
                f"Cannot reach the qBittorrent WebUI at {self.base_url}: {exc}"
            ) from exc

        if response.status_code == 403:
            raise QbitAuthError(
                "qBittorrent temporarily banned this IP after too many failed logins."
            )
        if response.status_code != 200 or response.text.strip() != "Ok.":
            raise QbitAuthError("Invalid qBittorrent WebUI username or password.")

        self._logged_in = True

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        _retry: bool = True,
    ) -> httpx.Response:
        if not self._logged_in:
            await self.login()

        try:
            response = await self._http.request(
                method, path, params=params, data=data, headers=self._headers
            )
        except httpx.HTTPError as exc:
            self._logged_in = False
            raise QbitUnreachable(
                f"Cannot reach the qBittorrent WebUI at {self.base_url}: {exc}"
            ) from exc

        if response.status_code == 403 and _retry:
            # Session expired (or a fresh ban): try to log in once more.
            self._logged_in = False
            await self.login()
            return await self._request(
                method, path, params=params, data=data, _retry=False
            )

        if response.status_code == 404:
            raise QbitError(f"qBittorrent: not found: {path}")
        if response.status_code >= 400:
            raise QbitError(f"qBittorrent: {_error_message(response)}")

        return response

    # -- application -----------------------------------------------------

    async def app_version(self) -> str:
        response = await self._request("GET", "/api/v2/app/version")
        return response.text.strip()

    async def api_version(self) -> str:
        response = await self._request("GET", "/api/v2/app/webapiVersion")
        return response.text.strip()

    # -- torrents --------------------------------------------------------

    async def torrents_add(
        self,
        *,
        magnet: str,
        save_path: str,
        name: str,
        tags: str = "",
        content_layout: str = "Subfolder",
        auto_tmm: bool = False,
    ) -> tuple[bool, list[str]]:
        """Add a magnet link; returns (accepted, added_torrent_ids)."""
        data: dict[str, Any] = {
            "urls": magnet,
            "savepath": save_path,
            "rename": name,
            "contentLayout": content_layout,
            "autoTMM": "true" if auto_tmm else "false",
            **ADD_STARTED_PARAMS,
        }
        if tags:
            data["tags"] = tags

        response = await self._request("POST", "/api/v2/torrents/add", data=data)
        text = response.text.strip()

        try:
            payload = json.loads(text)
        except ValueError:
            # qBittorrent < 5.0 replies with "Ok." / "Fails."
            return text.lower().startswith("ok"), []

        if not isinstance(payload, dict):
            return text.lower().startswith("ok"), []

        success = int(payload.get("success_count") or 0)
        failure = int(payload.get("failure_count") or 0)
        ids = [str(item) for item in payload.get("added_torrent_ids") or []]
        return success > 0 and failure == 0, ids

    async def torrents_info(
        self, *, hashes: list[str] | None = None, tag: str = ""
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if hashes:
            params["hashes"] = "|".join(hashes)
        if tag:
            params["tag"] = tag
        response = await self._request("GET", "/api/v2/torrents/info", params=params)
        payload = response.json()
        return payload if isinstance(payload, list) else []

    async def torrent(self, torrent_hash: str) -> dict[str, Any] | None:
        torrents = await self.torrents_info(hashes=[torrent_hash])
        return torrents[0] if torrents else None

    async def torrent_files(self, torrent_hash: str) -> list[dict[str, Any]]:
        response = await self._request(
            "GET", "/api/v2/torrents/files", params={"hash": torrent_hash}
        )
        payload = response.json()
        return payload if isinstance(payload, list) else []

    async def rename_file(self, torrent_hash: str, old_path: str, new_path: str) -> None:
        await self._request(
            "POST",
            "/api/v2/torrents/renameFile",
            data={"hash": torrent_hash, "oldPath": old_path, "newPath": new_path},
        )

    async def rename_folder(self, torrent_hash: str, old_path: str, new_path: str) -> None:
        await self._request(
            "POST",
            "/api/v2/torrents/renameFolder",
            data={"hash": torrent_hash, "oldPath": old_path, "newPath": new_path},
        )

    async def set_location(self, hashes: list[str], location: str) -> None:
        await self._request(
            "POST",
            "/api/v2/torrents/setLocation",
            data={"hashes": "|".join(hashes), "location": location},
        )

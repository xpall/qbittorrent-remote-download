"""In-memory qBittorrent stand-in for development and tests.

Enabled with ``QBT_REMOTE_MOCK=1`` (or ``"mock": true`` in config.json).
It implements the same small surface as :class:`qbittorrent.QbitClient`
so the rest of the app does not know the difference.
"""

from __future__ import annotations

import time
import urllib.parse
from typing import Any

from naming import common_root, parse_magnet_hash
from qbittorrent import QbitError


class MockTorrent:
    def __init__(
        self,
        torrent_hash: str,
        name: str,
        original_name: str,
        save_path: str,
        files: list[dict[str, Any]],
        *,
        metadata_delay: float,
        download_duration: float,
        tags: set[str],
    ) -> None:
        self.hash = torrent_hash
        self.name = name
        self.original_name = original_name
        self.save_path = save_path.rstrip("/")
        self.files = files
        self.tags = tags
        self.created_at = time.time()
        self.metadata_at = self.created_at + metadata_delay
        self.finish_at = self.metadata_at + download_duration

    @property
    def has_metadata(self) -> bool:
        return time.time() >= self.metadata_at

    @property
    def progress(self) -> float:
        if not self.has_metadata:
            return 0.0
        elapsed = time.time() - self.metadata_at
        return max(0.0, min(1.0, elapsed / max(0.001, self.finish_at - self.metadata_at)))

    @property
    def size(self) -> int:
        if not self.has_metadata:
            return 0
        return sum(int(file["size"]) for file in self.files)

    @property
    def state(self) -> str:
        if not self.has_metadata:
            return "metaDL"
        return "uploading" if self.progress >= 1.0 else "downloading"

    @property
    def content_path(self) -> str:
        if not self.has_metadata:
            return ""
        root = common_root(file["name"] for file in self.files)
        if root:
            return f"{self.save_path}/{root}"
        if len(self.files) == 1:
            return f"{self.save_path}/{self.files[0]['name']}"
        return self.save_path


class MockQbitClient:
    def __init__(self, metadata_delay: float = 6.0, download_duration: float = 180.0) -> None:
        self.metadata_delay = metadata_delay
        self.download_duration = download_duration
        self.torrents: dict[str, MockTorrent] = {}
        self.auth_mode = "mock"

    async def close(self) -> None:
        return None

    async def app_version(self) -> str:
        return "v5.2.3-mock"

    async def api_version(self) -> str:
        return "2.11.3-mock"

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
        torrent_hash = parse_magnet_hash(magnet)
        if not torrent_hash:
            raise QbitError("Not a valid magnet link.")
        if torrent_hash in self.torrents:
            return False, []

        query = urllib.parse.parse_qs(magnet.split("?", 1)[-1])
        original_name = (query.get("dn") or [f"Torrent.{torrent_hash[:8]}"])[0]
        original_name = original_name.rsplit(".", 1)[0] or original_name

        if content_layout == "Subfolder":
            files = [
                {"name": f"{original_name}/{original_name}.1080p.mkv", "size": 2_400_000_000},
                {"name": f"{original_name}/Subs/English.srt", "size": 85_000},
            ]
        else:  # rootless single file, like a torrent without a top level folder
            files = [{"name": f"{original_name}.1080p.mkv", "size": 2_400_000_000}]

        self.torrents[torrent_hash] = MockTorrent(
            torrent_hash,
            name,
            original_name,
            save_path,
            files,
            metadata_delay=self.metadata_delay,
            download_duration=self.download_duration,
            tags={tag for tag in tags.split(",") if tag},
        )
        return True, [torrent_hash]

    async def torrents_info(
        self, *, hashes: list[str] | None = None, tag: str = ""
    ) -> list[dict[str, Any]]:
        selected = self.torrents.values()
        if hashes:
            wanted = set(hashes)
            selected = [torrent for torrent in selected if torrent.hash in wanted]
        if tag:
            selected = [torrent for torrent in selected if tag in torrent.tags]
        return [self._info(torrent) for torrent in selected]

    async def torrent(self, torrent_hash: str) -> dict[str, Any] | None:
        torrent = self.torrents.get(torrent_hash)
        return self._info(torrent) if torrent else None

    async def torrent_files(self, torrent_hash: str) -> list[dict[str, Any]]:
        torrent = self.torrents.get(torrent_hash)
        if torrent is None:
            raise QbitError(f"Torrent {torrent_hash} not found.")
        if not torrent.has_metadata:
            return []
        return [
            {
                "index": index,
                "name": file["name"],
                "size": file["size"],
                "progress": torrent.progress,
                "priority": 1,
                "is_seed": torrent.progress >= 1.0,
            }
            for index, file in enumerate(torrent.files)
        ]

    async def rename_file(self, torrent_hash: str, old_path: str, new_path: str) -> None:
        torrent = self._require(torrent_hash)
        names = [file["name"] for file in torrent.files]
        if new_path in names:
            raise QbitError(f"The file already exists: '{new_path}'.")
        for file in torrent.files:
            if file["name"] == old_path:
                file["name"] = new_path
                return
        raise QbitError(f"No such file: '{old_path}'.")

    async def rename_folder(self, torrent_hash: str, old_path: str, new_path: str) -> None:
        torrent = self._require(torrent_hash)
        old_prefix = f"{old_path.rstrip('/')}/"
        new_prefix = f"{new_path.rstrip('/')}/"

        candidates = [file for file in torrent.files if file["name"].startswith(old_prefix)]
        if not candidates:
            raise QbitError(f"No such folder: '{old_path}'.")
        if any(
            file["name"].startswith(new_prefix)
            for file in torrent.files
            if file not in candidates
        ):
            raise QbitError(f"The folder already exists: '{new_path}'.")

        for file in candidates:
            file["name"] = new_prefix + file["name"][len(old_prefix) :]

    async def set_location(self, hashes: list[str], location: str) -> None:
        for torrent_hash in hashes:
            self._require(torrent_hash).save_path = location.rstrip("/")

    def _require(self, torrent_hash: str) -> MockTorrent:
        torrent = self.torrents.get(torrent_hash)
        if torrent is None:
            raise QbitError(f"Torrent {torrent_hash} not found.")
        return torrent

    def _info(self, torrent: MockTorrent) -> dict[str, Any]:
        progress = torrent.progress
        # Simulate a download speed that reaches zero once finished.
        speed = 0 if progress >= 1.0 else int(5_500_000 + 1_500_000 * (progress % 0.3))
        remaining = max(0, int(torrent.size * (1.0 - progress)))
        eta = 86400 if speed == 0 else max(1, remaining // max(1, speed))
        return {
            "hash": torrent.hash,
            "name": torrent.name,
            "state": torrent.state,
            "progress": progress,
            "size": torrent.size,
            "total_size": torrent.size,
            "downloaded": int(torrent.size * progress),
            "dlspeed": speed,
            "upspeed": 0,
            "eta": eta,
            "save_path": torrent.save_path,
            "content_path": torrent.content_path,
            "tags": ",".join(sorted(torrent.tags)),
            "category": "",
            "num_seeds": 12,
            "num_leechs": 4,
            "completion_on": 0 if progress < 1.0 else int(time.time()),
            "added_on": int(torrent.created_at),
        }

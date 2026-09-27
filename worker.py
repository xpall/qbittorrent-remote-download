"""Background worker that applies the requested folder/file names.

qBittorrent's ``rename`` parameter on add only changes the torrent's
display name, so once metadata is available the worker renames the root
folder (``renameFolder``) or moves a rootless torrent into a folder
(``setLocation``) and renames the largest video file (``renameFile``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path as FsPath
from typing import Literal

from pydantic import BaseModel, Field

from naming import common_root, desired_file_name, is_video, with_basename
from qbittorrent import QbitAuthError, QbitClient, QbitError, QbitUnreachable

logger = logging.getLogger(__name__)

JobStatus = Literal["pending", "waiting", "named", "error"]

ACTIVE_STATUSES = {"pending", "waiting"}
MAX_FAILURES = 3
MAX_JOBS = 100


class Job(BaseModel):
    hash: str
    media_type: str
    magnet: str
    folder_name: str
    file_name: str = ""
    save_path: str
    status: JobStatus = "pending"
    message: str = "Added to qBittorrent."
    video_files: int = 0
    renamed_files: int = 0
    failures: int = 0
    attempts: int = 0
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)

    def touch(self, status: JobStatus, message: str) -> None:
        self.status = status
        self.message = message
        self.updated_at = time.time()


class JobStore:
    """Tiny JSON backed job store (kept in memory, persisted on change)."""

    def __init__(self, path: str) -> None:
        self.path = FsPath(path)
        self.jobs: dict[str, Job] = {}

    def load(self) -> None:
        if not self.path.is_file():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning("Could not read state file %s; starting empty.", self.path)
            return
        for raw in payload.get("jobs", []):
            try:
                job = Job.model_validate(raw)
            except ValueError:
                continue
            self.jobs[job.hash] = job

    def save(self) -> None:
        jobs = sorted(self.jobs.values(), key=lambda item: item.created_at, reverse=True)
        del jobs[MAX_JOBS:]
        payload = {"jobs": [job.model_dump() for job in jobs]}
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            os.replace(tmp_path, self.path)
        except OSError:
            logger.exception("Could not write state file %s", self.path)

    def upsert(self, job: Job) -> None:
        self.jobs[job.hash] = job
        self.save()


class RenameWorker:
    def __init__(
        self,
        client: QbitClient,
        store: JobStore,
        *,
        poll_interval: float = 5.0,
        not_found_grace: float = 600.0,
    ) -> None:
        self.client = client
        self.store = store
        self.poll_interval = max(0.05, poll_interval)
        self.not_found_grace = not_found_grace
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="rename-worker")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None

    def schedule(self, job: Job) -> None:
        self.store.upsert(job)
        self._wake.set()

    async def _run(self) -> None:
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover - defensive
                logger.exception("Rename worker tick failed")
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.poll_interval)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()

    async def _tick(self) -> None:
        active = [job for job in self.store.jobs.values() if job.status in ACTIVE_STATUSES]
        if not active:
            return
        for job in active:
            try:
                await self._process(job)
            except QbitUnreachable as exc:
                job.touch("waiting", str(exc))
            except QbitAuthError as exc:
                job.touch("error", str(exc))
            except QbitError as exc:
                job.failures += 1
                if job.failures >= MAX_FAILURES:
                    job.touch("error", f"qBittorrent error: {exc}")
                else:
                    job.touch("waiting", f"Retrying after qBittorrent error: {exc}")
            except Exception as exc:  # pragma: no cover - defensive
                logger.exception("Failed to process job %s", job.hash)
                job.failures += 1
                if job.failures >= MAX_FAILURES:
                    job.touch("error", f"Unexpected error: {exc}")
                else:
                    job.touch("waiting", f"Retrying after error: {exc}")
            job.attempts += 1
        self.store.save()

    async def _process(self, job: Job) -> None:
        info = await self.client.torrent(job.hash)
        if info is None:
            if (time.time() - job.created_at) > self.not_found_grace:
                job.touch("error", "The torrent never appeared in qBittorrent.")
            else:
                job.touch("waiting", "Waiting for qBittorrent to register the torrent.")
            return

        state = str(info.get("state") or "")
        size = int(info.get("size") or 0)
        if size == 0 or state in {"metaDL", "checkingResumeData", "allocating", "moving"}:
            job.touch("waiting", "Waiting for torrent metadata.")
            return

        files = await self._named_files(job.hash)
        if not files:
            job.touch("waiting", "Waiting for the torrent file list.")
            return

        save_path = str(info.get("save_path") or job.save_path).rstrip("/") or "/"
        notes: list[str] = []

        # 1) Make sure the content lives in a folder named after `folder_name`.
        root = common_root(file["name"] for file in files)
        if root is None:
            if FsPath(save_path).name != job.folder_name:
                target = f"{save_path}/{job.folder_name}"
                await self.client.set_location([job.hash], target)
                notes.append(f"Moved the download into “{job.folder_name}”.")
                if job.file_name:
                    # Give qBittorrent a moment to finish the move before
                    # renaming the file inside the new location.
                    job.touch("waiting", " ".join(notes))
                    return
        elif root != job.folder_name:
            await self.client.rename_folder(job.hash, root, job.folder_name)
            notes.append(f"Renamed folder “{root}” to “{job.folder_name}”.")

        # 2) Rename the largest video file when a file name was requested.
        if job.file_name:
            files = await self._named_files(job.hash)
            videos = [file for file in files if is_video(file["name"])]
            job.video_files = len(videos)
            if videos:
                video = max(videos, key=lambda file: int(file.get("size") or 0))
                old_path = video["name"]
                new_path = with_basename(
                    old_path, desired_file_name(job.file_name, old_path)
                )
                if old_path != new_path:
                    await self.client.rename_file(job.hash, old_path, new_path)
                    notes.append(
                        f"Renamed “{old_path.rsplit('/', 1)[-1]}” to "
                        f"“{new_path.rsplit('/', 1)[-1]}”."
                    )
                job.renamed_files = 1
                if len(videos) > 1:
                    notes.append(f"{len(videos)} video files present; the largest was renamed.")
            else:
                notes.append("No video files found; only the folder name was applied.")
        elif not notes:
            notes.append("Folder name applied.")

        job.touch("named", " ".join(notes))

    async def _named_files(self, torrent_hash: str) -> list[dict]:
        files = await self.client.torrent_files(torrent_hash)
        return [file for file in files if file.get("name")]

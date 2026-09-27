"""FastAPI application: small web UI + JSON API on top of qBittorrent."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from config import AppConfig, load_config
from naming import InvalidName, parse_magnet_hash, sanitize_name
from qbittorrent import QbitClient, QbitError
from worker import Job, JobStore, RenameWorker

logger = logging.getLogger("qbt_remote")

STATIC_DIR = Path(__file__).resolve().parent / "static"
MOCK_ENV_VAR = "QBT_REMOTE_MOCK"


class EntryIn(BaseModel):
    media_type: str = "movies"
    magnet: str = ""
    folder_name: str = ""
    file_name: str = ""


class BatchIn(BaseModel):
    entries: list[EntryIn] = Field(min_length=1)


def _uses_mock(config: AppConfig) -> bool:
    if config.mock:
        return True
    return os.environ.get(MOCK_ENV_VAR, "").strip().lower() in {"1", "true", "yes", "on"}


def create_app(
    config: AppConfig | None = None,
    client_factory: Callable[[AppConfig], Any] | None = None,
) -> FastAPI:
    resolved_config = config or load_config()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        cfg = resolved_config
        if client_factory is not None:
            client = client_factory(cfg)
        elif _uses_mock(cfg):
            from mock import MockQbitClient

            client = MockQbitClient()
            logger.warning("Mock mode enabled: torrents are simulated, nothing is downloaded.")
        else:
            client = QbitClient(cfg.qbittorrent)

        store = JobStore(cfg.state_file)
        store.load()
        worker = RenameWorker(client, store, poll_interval=cfg.poll_interval)

        app.state.config = cfg
        app.state.qb = client
        app.state.store = store
        app.state.worker = worker

        for problem in cfg.validate_libraries():
            logger.warning("%s", problem)

        worker.start()
        try:
            yield
        finally:
            await worker.stop()
            await client.close()
            store.save()

    app = FastAPI(title="qBittorrent Remote Download", lifespan=lifespan)

    # -- API -------------------------------------------------------------

    @app.get("/api/health")
    async def health(request: Request) -> dict[str, Any]:
        cfg: AppConfig = request.app.state.config
        client: QbitClient = request.app.state.qb
        problems = cfg.validate_libraries()
        auth_mode = getattr(client, "auth_mode", None)
        try:
            version = await client.app_version()
            api_version = await client.api_version()
            qbittorrent: dict[str, Any] = {
                "ok": True,
                "version": version,
                "api_version": api_version,
                "auth_mode": auth_mode,
            }
        except QbitError as exc:
            qbittorrent = {"ok": False, "error": str(exc), "auth_mode": auth_mode}
        return {
            "qbittorrent": qbittorrent,
            "libraries": {
                media_type: {
                    "path": str(cfg.library_path(media_type)),
                    "exists": cfg.library_path(media_type).is_dir(),
                }
                for media_type in cfg.libraries
            },
            "problems": problems,
        }

    @app.get("/api/config")
    async def ui_config(request: Request) -> dict[str, Any]:
        cfg: AppConfig = request.app.state.config
        return {
            "max_batch": cfg.max_batch,
            "media_types": [
                media_type
                for media_type in ("movies", "shows")
                if media_type in cfg.libraries
            ],
        }

    @app.post("/api/downloads")
    async def add_downloads(batch: BatchIn, request: Request) -> dict[str, Any]:
        cfg: AppConfig = request.app.state.config
        client: QbitClient = request.app.state.qb
        worker: RenameWorker = request.app.state.worker

        if len(batch.entries) > cfg.max_batch:
            raise HTTPException(
                status_code=422,
                detail=f"At most {cfg.max_batch} entries can be submitted at once.",
            )

        results: list[dict[str, Any]] = []
        seen: set[str] = set()

        for index, entry in enumerate(batch.entries):
            result: dict[str, Any] = {
                "index": index,
                "ok": False,
                "hash": None,
                "folder_name": entry.folder_name.strip(),
                "error": None,
            }
            try:
                media_type = entry.media_type.strip().lower()
                if media_type not in cfg.libraries:
                    raise InvalidName(f"Unknown media type: {entry.media_type!r}.")

                torrent_hash = parse_magnet_hash(entry.magnet)
                if not torrent_hash:
                    raise InvalidName(
                        "Not a valid magnet link (no btih info hash found)."
                    )
                if torrent_hash in seen:
                    raise InvalidName("This magnet is duplicated in the batch.")
                seen.add(torrent_hash)

                folder_name = sanitize_name(entry.folder_name, "folder name")
                file_name = (
                    sanitize_name(entry.file_name, "file name")
                    if entry.file_name.strip()
                    else ""
                )
                save_path = str(cfg.library_path(media_type))

                existing = await client.torrent(torrent_hash)
                if existing:
                    raise InvalidName(
                        f"Already in qBittorrent as “{existing.get('name')}”."
                    )

                accepted, _ = await client.torrents_add(
                    magnet=entry.magnet.strip(),
                    save_path=save_path,
                    name=folder_name,
                    tags=cfg.tag,
                )
                if not accepted:
                    raise InvalidName("qBittorrent rejected the torrent.")

                worker.schedule(
                    Job(
                        hash=torrent_hash,
                        media_type=media_type,
                        magnet=entry.magnet.strip(),
                        folder_name=folder_name,
                        file_name=file_name,
                        save_path=save_path,
                    )
                )
                result.update(ok=True, hash=torrent_hash, folder_name=folder_name)
            except (InvalidName, QbitError) as exc:
                result["error"] = str(exc)

            results.append(result)

        return {"results": results}

    @app.get("/api/downloads")
    async def list_downloads(request: Request) -> dict[str, Any]:
        client: QbitClient = request.app.state.qb
        store: JobStore = request.app.state.store

        jobs = sorted(store.jobs.values(), key=lambda job: job.created_at, reverse=True)
        info_by_hash: dict[str, dict[str, Any]] = {}
        if jobs:
            try:
                torrents = await client.torrents_info(hashes=[job.hash for job in jobs])
                info_by_hash = {str(item.get("hash")): item for item in torrents}
            except QbitError as exc:
                logger.debug("Could not fetch torrent info: %s", exc)

        downloads = []
        for job in jobs:
            info = info_by_hash.get(job.hash)
            downloads.append(
                {"job": job.model_dump(), "download": _download_view(info)}
            )
        return {"downloads": downloads}

    # -- static UI -------------------------------------------------------

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


def _download_view(info: dict[str, Any] | None) -> dict[str, Any] | None:
    if info is None:
        return None
    fields = (
        "state",
        "progress",
        "size",
        "total_size",
        "downloaded",
        "dlspeed",
        "upspeed",
        "eta",
        "save_path",
        "content_path",
        "num_seeds",
        "num_leechs",
        "completion_on",
        "added_on",
    )
    return {field: info.get(field) for field in fields}


app = create_app()


if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    settings = load_config()
    if _uses_mock(settings):
        logging.getLogger("qbt_remote").warning("Starting in mock mode.")
    uvicorn.run(app, host=settings.bind_host, port=settings.port)

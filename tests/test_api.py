import time

from fastapi.testclient import TestClient

from config import AppConfig
from main import create_app
from mock import MockQbitClient

HASH_A = "a" * 40
HASH_B = "b" * 40


def magnet(torrent_hash: str, name: str = "Original.Name.1080p.GROUP") -> str:
    return f"magnet:?xt=urn:btih:{torrent_hash}&dn={name}"


def build_app(tmp_path, metadata_delay: float = 0.0):
    config = AppConfig(
        mock=True,
        state_file=str(tmp_path / "state.json"),
        poll_interval=0.05,
        libraries={"movies": "/library/Movies", "shows": "/library/Shows"},
    )
    return create_app(
        config,
        client_factory=lambda cfg: MockQbitClient(metadata_delay=metadata_delay),
    )


def test_batch_validation(tmp_path):
    app = build_app(tmp_path)
    with TestClient(app) as client:
        response = client.post(
            "/api/downloads",
            json={
                "entries": [
                    {
                        "media_type": "movies",
                        "magnet": magnet(HASH_A),
                        "folder_name": "Some Movie (2024)",
                        "file_name": "Some Movie (2024)",
                    },
                    {
                        "media_type": "movies",
                        "magnet": "not-a-magnet",
                        "folder_name": "Bad Entry",
                        "file_name": "",
                    },
                    {
                        "media_type": "movies",
                        "magnet": magnet(HASH_A),
                        "folder_name": "Duplicate",
                        "file_name": "",
                    },
                    {
                        "media_type": "movies",
                        "magnet": magnet(HASH_B),
                        "folder_name": "..",
                        "file_name": "",
                    },
                ]
            },
        )
        assert response.status_code == 200
        results = response.json()["results"]

        assert results[0]["ok"] is True
        assert results[0]["hash"] == HASH_A

        assert results[1]["ok"] is False
        assert "magnet" in results[1]["error"].lower()

        assert results[2]["ok"] is False
        assert "duplicate" in results[2]["error"].lower()

        assert results[3]["ok"] is False
        assert "folder name" in results[3]["error"].lower()

        downloads = client.get("/api/downloads").json()["downloads"]
        assert len(downloads) == 1
        assert downloads[0]["job"]["hash"] == HASH_A
        assert downloads[0]["job"]["status"] in {"pending", "waiting", "named"}


def test_download_gets_renamed_end_to_end(tmp_path):
    app = build_app(tmp_path, metadata_delay=0.0)
    with TestClient(app) as client:
        response = client.post(
            "/api/downloads",
            json={
                "entries": [
                    {
                        "media_type": "movies",
                        "magnet": magnet(HASH_A),
                        "folder_name": "Some Movie (2024)",
                        "file_name": "Some Movie (2024)",
                    }
                ]
            },
        )
        assert response.json()["results"][0]["ok"] is True

        job = None
        deadline = time.time() + 5
        while time.time() < deadline:
            downloads = client.get("/api/downloads").json()["downloads"]
            job = downloads[0]["job"]
            if job["status"] == "named":
                break
            time.sleep(0.05)

        assert job is not None
        assert job["status"] == "named", job.get("message")

        torrent = app.state.qb.torrents[HASH_A]
        names = sorted(file["name"] for file in torrent.files)
        assert names == [
            "Some Movie (2024)/Some Movie (2024).mkv",
            "Some Movie (2024)/Some Movie (2024).srt",
        ]


def test_health_and_config(tmp_path):
    app = build_app(tmp_path)
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        payload = health.json()
        assert payload["qbittorrent"]["ok"] is True
        assert "mock" in payload["qbittorrent"]["version"]
        assert payload["qbittorrent"]["auth_mode"] == "mock"

        config = client.get("/api/config").json()
        assert config["media_types"] == ["movies", "shows"]
        assert config["max_batch"] == 10


def test_batch_limit(tmp_path):
    app = build_app(tmp_path)
    with TestClient(app) as client:
        entries = [
            {
                "media_type": "movies",
                "magnet": magnet(f"{index:040d}"),
                "folder_name": f"Movie {index}",
                "file_name": "",
            }
            for index in range(11)
        ]
        response = client.post("/api/downloads", json={"entries": entries})
        assert response.status_code == 422

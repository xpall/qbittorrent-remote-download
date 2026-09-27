import asyncio

from mock import MockQbitClient
from worker import Job, JobStore, RenameWorker

HASH_A = "a" * 40
HASH_B = "b" * 40


def magnet(torrent_hash: str, name: str) -> str:
    return f"magnet:?xt=urn:btih:{torrent_hash}&dn={name}"


def build(torrent_hash: str, folder: str, file_name: str, media_type: str = "movies") -> Job:
    return Job(
        hash=torrent_hash,
        media_type=media_type,
        magnet=magnet(torrent_hash, "Original.Name.1080p.GROUP"),
        folder_name=folder,
        file_name=file_name,
        save_path=f"/library/{media_type.capitalize()}",
    )


def test_worker_renames_folder_and_largest_video(tmp_path):
    async def scenario():
        client = MockQbitClient(metadata_delay=0.0, download_duration=60.0)
        store = JobStore(tmp_path / "state.json")
        worker = RenameWorker(client, store, poll_interval=0.01)

        job = build(HASH_A, "Dune Part Two (2024)", "Dune Part Two (2024)")
        await client.torrents_add(
            magnet=job.magnet,
            save_path=job.save_path,
            name=job.folder_name,
            tags="remote-dl",
        )
        store.upsert(job)

        await worker._process(job)

        assert job.status == "named", job.message
        assert job.renamed_files == 1
        assert job.video_files == 1
        assert "Moved subtitle" in job.message

        names = sorted(file["name"] for file in await client.torrent_files(job.hash))
        assert names == [
            "Dune Part Two (2024)/Dune Part Two (2024).mkv",
            "Dune Part Two (2024)/Dune Part Two (2024).srt",
        ]

    asyncio.run(scenario())


def test_worker_prefers_english_subtitle_over_larger(tmp_path):
    async def scenario():
        client = MockQbitClient(metadata_delay=0.0, download_duration=60.0)
        store = JobStore(tmp_path / "state.json")
        worker = RenameWorker(client, store, poll_interval=0.01)

        job = build(HASH_A, "Some Movie (2024)", "Some Movie (2024)")
        await client.torrents_add(
            magnet=job.magnet, save_path=job.save_path, name=job.folder_name
        )
        torrent = client.torrents[job.hash]
        torrent.files.append(
            {"name": f"{torrent.original_name}/Subs/Spanish.srt", "size": 900_000}
        )
        store.upsert(job)

        await worker._process(job)

        assert job.status == "named", job.message
        names = sorted(file["name"] for file in await client.torrent_files(job.hash))
        assert names == [
            "Some Movie (2024)/Some Movie (2024).mkv",
            "Some Movie (2024)/Some Movie (2024).srt",
            "Some Movie (2024)/Subs/Spanish.srt",
        ]

    asyncio.run(scenario())


def test_worker_leaves_subtitles_alone_without_file_name(tmp_path):
    async def scenario():
        client = MockQbitClient(metadata_delay=0.0, download_duration=60.0)
        store = JobStore(tmp_path / "state.json")
        worker = RenameWorker(client, store, poll_interval=0.01)

        job = build(HASH_A, "Some Movie (2024)", "")
        await client.torrents_add(
            magnet=job.magnet, save_path=job.save_path, name=job.folder_name
        )
        store.upsert(job)

        await worker._process(job)

        assert job.status == "named", job.message
        names = sorted(file["name"] for file in await client.torrent_files(job.hash))
        assert names == [
            "Some Movie (2024)/Original.Name.1080p.1080p.mkv",
            "Some Movie (2024)/Subs/English.srt",
        ]

    asyncio.run(scenario())


def test_worker_subtitle_already_in_place_is_noop(tmp_path):
    async def scenario():
        client = MockQbitClient(metadata_delay=0.0, download_duration=60.0)
        store = JobStore(tmp_path / "state.json")
        worker = RenameWorker(client, store, poll_interval=0.01)

        job = build(HASH_A, "Some Movie (2024)", "Some Movie (2024)")
        await client.torrents_add(
            magnet=job.magnet, save_path=job.save_path, name=job.folder_name
        )
        torrent = client.torrents[job.hash]
        original = torrent.original_name
        torrent.files = [
            {"name": f"{original}/{original}.1080p.mkv", "size": 2_400_000_000},
            {"name": f"{original}/Some Movie (2024).srt", "size": 50_000},
        ]
        store.upsert(job)

        await worker._process(job)

        assert job.status == "named", job.message
        assert "Moved subtitle" not in job.message
        names = sorted(file["name"] for file in await client.torrent_files(job.hash))
        assert names == [
            "Some Movie (2024)/Some Movie (2024).mkv",
            "Some Movie (2024)/Some Movie (2024).srt",
        ]

    asyncio.run(scenario())


def test_worker_moves_rootless_torrent_into_folder(tmp_path):
    async def scenario():
        client = MockQbitClient(metadata_delay=0.0, download_duration=60.0)
        store = JobStore(tmp_path / "state.json")
        worker = RenameWorker(client, store, poll_interval=0.01)

        job = build(HASH_B, "Some Movie (2024)", "Some Movie (2024)")
        await client.torrents_add(
            magnet=job.magnet,
            save_path=job.save_path,
            name=job.folder_name,
            content_layout="NoSubfolder",
        )
        store.upsert(job)

        await worker._process(job)  # moves the rootless torrent into a folder
        assert job.status == "waiting"
        await worker._process(job)  # then renames the video inside it
        assert job.status == "named", job.message

        info = await client.torrent(job.hash)
        assert info["save_path"] == "/library/Movies/Some Movie (2024)"
        names = [file["name"] for file in await client.torrent_files(job.hash)]
        assert names == ["Some Movie (2024).mkv"]

    asyncio.run(scenario())


def test_worker_waits_for_metadata(tmp_path):
    async def scenario():
        client = MockQbitClient(metadata_delay=60.0, download_duration=60.0)
        store = JobStore(tmp_path / "state.json")
        worker = RenameWorker(client, store, poll_interval=0.01)

        job = build(HASH_A, "Some Movie (2024)", "Some Movie (2024)")
        await client.torrents_add(
            magnet=job.magnet, save_path=job.save_path, name=job.folder_name
        )
        await worker._process(job)

        assert job.status == "waiting"
        assert "metadata" in job.message.lower()

    asyncio.run(scenario())


def test_worker_errors_after_grace_when_torrent_missing(tmp_path):
    async def scenario():
        client = MockQbitClient()
        store = JobStore(tmp_path / "state.json")
        worker = RenameWorker(client, store, poll_interval=0.01, not_found_grace=0.0)

        job = build(HASH_B, "Missing (2024)", "")
        job.created_at -= 10
        await worker._process(job)

        assert job.status == "error"
        assert "never appeared" in job.message

    asyncio.run(scenario())


def test_job_store_round_trip(tmp_path):
    store = JobStore(tmp_path / "state.json")
    job = build(HASH_A, "Dune Part Two (2024)", "Dune Part Two (2024)")
    store.upsert(job)

    reloaded = JobStore(tmp_path / "state.json")
    reloaded.load()
    assert reloaded.jobs[HASH_A].folder_name == "Dune Part Two (2024)"
    assert reloaded.jobs[HASH_A].status == "pending"

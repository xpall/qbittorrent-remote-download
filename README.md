# qBittorrent Remote Download

A tiny web app for adding torrents to a **qBittorrent** instance you don't
have in front of you — typically the laptop that runs Jellyfin.

Paste a magnet link, say how the folder and file should be named, hit
**Add download(s)**, and the content lands in your Jellyfin library with the
right names. Multiple entries can be submitted at once, one per row.

```
Browser → Cloudflare Access → cloudflared → this app (127.0.0.1:8765)
      → qBittorrent WebUI API (127.0.0.1:8080)
      → ~/Videos/qBittorrent/{Movies,Shows} → Jellyfin libraries
```

The app never touches files itself and never exposes qBittorrent's own
WebUI. It only speaks qBittorrent's local REST API, which lets it set the
save folder and rename the download exactly like you would by hand.

## How the naming works

1. The magnet is added to qBittorrent with your save path, a tag
   (`remote-dl`), and `contentLayout=Subfolder`.
2. A background worker waits until the torrent's metadata is available.
3. It renames the top-level folder to the folder name you typed
   (or moves a rootless torrent into a new folder).
4. If you typed a file name, it renames the largest video file, keeping the
   original extension unless you typed one yourself. For torrents with
   several video files (e.g. a season pack) only the largest is renamed —
   the UI tells you how many were found.
5. It moves the best `.srt` next to the video and renames it to match
   (`Subs/English.srt` → `Some Movie (2024)/Some Movie (2024).srt`), since
   Jellyfin only looks for external subtitles beside the video. English
   `.srt` files win over other languages; otherwise the largest is used.
   Other subtitle files are left untouched.

## Requirements

On the Jellyfin laptop:

- qBittorrent (flatpak, running — it is the download engine)
- Python 3.10+ (`python3 --version`)
- Python venv support: `sudo apt install python3-venv` (Kubuntu)
- cloudflared, if the app should be reachable from outside (you already have
  this)

Currently verified against qBittorrent 5.x. qBittorrent 4.3+ should work too.

## 1. Enable qBittorrent's Web UI

In qBittorrent: **Tools → Options → Web UI**

- [x] Web User Interface (Remote control)
- IP address: `127.0.0.1` (keep it local; the tunnel goes through this app)
- Port: `8080`
- Username / password: set something strong

Keep qBittorrent running. The flatpak uses `--filesystem=host` and shares the
network namespace, so the Web UI is reachable at `127.0.0.1:8080` and paths
are the same inside and outside the sandbox.

### Authentication

Two options, both configured in `config.json`:

1. **Username / password** (works on every qBittorrent version). Put the
   Web UI credentials in `qbittorrent.username` / `qbittorrent.password`.
   Note that if "Bypass authentication for clients on localhost" is enabled,
   no username/password will ever be accepted by the login endpoint; the app
   detects this and runs unauthenticated (`auth_mode: "none"` in
   `/api/health`).
2. **API key** (recommended, qBittorrent 5.2+). In qBittorrent open
   **Tools → Options → Web UI → API Key**, click **Generate API key**
   (it takes effect immediately), then **Copy API key**. Paste it into
   `qbittorrent.api_key` in `config.json`. The app then sends
   `Authorization: Bearer qbt_…` and skips the login flow entirely.
   Username/password are ignored while a key is set.

## 2. Install and run

```bash
git clone <this repo> ~/qbittorrent-remote-download
cd ~/qbittorrent-remote-download

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

cp config.example.json config.json
$EDITOR config.json          # Web UI credentials and library paths
.venv/bin/python main.py
```

Open <http://127.0.0.1:8765>. If qBittorrent is reachable and configured, the
page is ready.

### Configuration

| Key | Meaning |
| --- | --- |
| `bind_host` / `port` | Where the app listens. Keep `127.0.0.1` and let cloudflared in. |
| `qbittorrent.base_url` | Usually `http://127.0.0.1:8080`. |
| `qbittorrent.username` / `password` | Credentials from the Web UI settings. |
| `qbittorrent.api_key` | Optional. When set (qBittorrent 5.2+), used instead of username/password. |
| `libraries.movies` / `libraries.shows` | Where downloads land. Point these at the real folders, not the `/media` symlinks. |
| `tag` | Tag added to torrents from this app. |
| `max_batch` | Maximum entries per submission. |
| `poll_interval` | Seconds between worker checks. |
| `state_file` | Where request history is stored. |
| `mock` | Simulate qBittorrent (nothing downloaded). Also `QBT_REMOTE_MOCK=1`. |

## 3. Run as a service

```bash
mkdir -p ~/.config/systemd/user
cp scripts/qbt-remote.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now qbt-remote
journalctl --user -u qbt-remote -f     # logs
```

The service starts when you log in, and qBittorrent (the GUI) needs to be
running anyway, so a user service is a good fit.

## 4. Cloudflare tunnel

In your cloudflared configuration, point a hostname at the app:

```yaml
tunnel: <tunnel-id>
credentials-file: /home/<user>/.cloudflared/<tunnel-id>.json
ingress:
  - hostname: dl.example.dev
    service: http://127.0.0.1:8765
  - service: http_status:404
```

Route the DNS entry (`cloudflared tunnel route dns ...`) and attach a
**Cloudflare Access** policy to the hostname. The app has no login of its own
and binds to localhost only, so Access is what keeps it private.

## HTTP API

Everything the UI does is a JSON endpoint, handy for shortcuts or scripts:

```bash
curl -s http://127.0.0.1:8765/api/health

curl -s -X POST http://127.0.0.1:8765/api/downloads \
  -H 'Content-Type: application/json' \
  -d '{"entries":[{"media_type":"movies",
                   "magnet":"magnet:?xt=urn:btih:...",
                   "folder_name":"Dune Part Two (2024)",
                   "file_name":"Dune Part Two (2024)"}]}'
```

- `POST /api/downloads` → per-entry result (`ok`, `hash`, or an `error`).
- `GET /api/downloads` → tracked requests plus live progress.
- `GET /api/health` → qBittorrent reachability, version, auth mode, library paths.

## Development

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest

QBT_REMOTE_MOCK=1 .venv/bin/python main.py   # fake qBittorrent, no downloads
```

## Troubleshooting

- **"Can't reach qBittorrent"** — is qBittorrent running and is the Web UI
  enabled on `127.0.0.1:8080`? Check credentials in `config.json`.
- **"rejected the username or password" on qBittorrent 5.2+** — the login
  endpoint layout changed in 5.2 (success is now `204` instead of `200 Ok.`);
  this app handles both. If it still fails, the simplest fix is to generate
  an API key (see *Authentication*) and put it in `config.json`. To see what
  qBittorrent actually answers:
  `curl -i -H 'Referer: http://127.0.0.1:8080' --data 'username=U&password=P' http://127.0.0.1:8080/api/v2/auth/login`
  (`204` = correct credentials, `401` = rejected, `403` = banned).
- **"Bypass authentication for clients on localhost" is enabled** — no
  username/password can be validated then. The app notices and switches to
  unauthenticated mode; `/api/health` reports `"auth_mode": "none"`.
- **"temporarily banned this IP"** — too many failed logins; wait for the
  ban to expire (default 1 hour) or fix the password.
- **Naming failed** — the download itself continues; the Downloads card shows
  the qBittorrent error (usually a name conflict). Rename it in qBittorrent
  or submit again.
- **Jellyfin does not show the new item** — new top-level folders are not
  always picked up automatically; trigger a library scan from Jellyfin if
  needed.

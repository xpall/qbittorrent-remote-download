# qBittorrent Remote Download

**Add movies and shows to your Jellyfin server from anywhere — without opening
the machine it runs on.**

Jellyfin usually lives on a local machine: a laptop in another room, a mini PC
in a cupboard, a home server. Downloading something normally means walking
over to that machine, opening qBittorrent, pasting a magnet link and renaming
the files so Jellyfin recognizes them. This app turns that routine into one
small web page you can use from your phone:

- paste a magnet link, choose **Movies** or **Shows**, type the folder and file
  name, press **Add download(s)**;
- the download lands in `~/Videos/qBittorrent/Movies/<folder>/<file>.mkv` (or
  `Shows`) with Jellyfin-friendly names;
- an English subtitle is moved next to the video and renamed to match, since
  Jellyfin only reads subtitles that sit beside the video;
- several downloads can be added at once and you can watch their progress.

The page itself is private: it listens on localhost only and is exposed to the
internet through *your* Cloudflare Tunnel + Cloudflare Access login.

## How it works

```
Phone / browser
      │  HTTPS, behind your Cloudflare Access policy
      ▼
Cloudflare Tunnel ──► this app (127.0.0.1:8765 on the Jellyfin machine)
                            │
                            │ local API calls
                            ▼
                      qBittorrent (flatpak, 127.0.0.1:8080)
                            │
                            ▼
              ~/Videos/qBittorrent/Movies and .../Shows
                            │
                            ▼
                   Jellyfin sees the new item
```

qBittorrent does the downloading; this app only tells it *what* to download and
*how to name it*. It never moves files on disk itself, so qBittorrent stays
happy and keeps seeding normally.

## What you need

- The machine that runs Jellyfin, with **qBittorrent installed as a flatpak and
  running** — this is the download engine.
- Python 3.10 or newer on that machine (`python3 --version`).
- A Cloudflare account with a tunnel, if you want to reach it from outside your
  home network (you already have this).

## Setup

Everything below happens **on the machine that runs Jellyfin**.

### 1. Turn on qBittorrent's Web UI

Open qBittorrent → **Tools → Options → Web UI**:

- check **Web User Interface (Remote control)**
- IP address: `127.0.0.1` (keep it local — the tunnel goes through this app)
- port: `8080`
- set a username and password

Click OK and leave qBittorrent running. The flatpak shares the host filesystem
and network, so `127.0.0.1:8080` and your normal file paths just work.

**Tip:** on qBittorrent 5.2 or newer you can use an API key instead of a
password (it survives password changes and needs no login round-trip). See
*Authentication* below.

### 2. Install this app

```bash
git clone <this repo> ~/qbittorrent-remote-download
cd ~/qbittorrent-remote-download

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

cp config.example.json config.json
nano config.json
```

In `config.json` set at least:

```json
{
  "qbittorrent": { "username": "admin", "password": "your-webui-password" },
  "libraries": {
    "movies": "~/Videos/qBittorrent/Movies",
    "shows": "~/Videos/qBittorrent/Shows"
  }
}
```

Point the library paths at the real folders (the ones Jellyfin scans), and make
sure they exist:

```bash
mkdir -p ~/Videos/qBittorrent/Movies ~/Videos/qBittorrent/Shows
```

### 3. Try it

First, a safe test run that downloads nothing — it uses a fake qBittorrent so
you can see the page and the flow:

```bash
QBT_REMOTE_MOCK=1 .venv/bin/python main.py
```

Open <http://127.0.0.1:8765> and add a magnet link; you will see it "download".
Press `Ctrl+C` when you are done.

Now the real thing:

```bash
.venv/bin/python main.py
```

The page header should show no warning banner. If it shows
*"Can't reach qBittorrent"*, see Troubleshooting.

### 4. Make it start (and restart) automatically

One command installs both this app and qBittorrent as systemd user services
that start at login and restart if they ever crash:

```bash
bash scripts/install-services.sh
```

Check everything:

```bash
systemctl --user status qbt-remote
journalctl --user -u qbt-remote -f     # live logs
```

**One important detail:** qBittorrent is a desktop app, so it can only run
after you log in. If the machine should keep working after a reboot without
anyone typing a password, turn on **auto-login** in KDE's login screen settings
(System Settings → Login Screen → Auto Login). After that, a reboot brings back
qBittorrent, this app and the tunnel on its own.

If your desktop does not support systemd session units, the script notices and
falls back to a normal KDE autostart entry for qBittorrent (starts at login,
but no crash-restart).

### 5. Reach it from anywhere (Cloudflare Tunnel)

Point a hostname at the app in your cloudflared configuration:

```yaml
tunnel: <tunnel-id>
credentials-file: /home/<user>/.cloudflared/<tunnel-id>.json
ingress:
  - hostname: downloads.example.dev
    service: http://127.0.0.1:8765
  - service: http_status:404
```

Create the DNS route (`cloudflared tunnel route dns ...`) and attach a
**Cloudflare Access** policy to the hostname. That Access policy is the only
thing standing between the internet and your downloads, so keep it enabled.

## Using it

1. Open the page on your phone.
2. Pick **Movies** or **Shows**.
3. Paste the magnet link, type the folder name (e.g. `Dune Part Two (2024)`)
   and, for movies, the file name without extension (e.g. `Dune Part Two (2024)`).
4. Press **Add download(s)**. Use **+ Add another** to submit several at once.
5. Watch the progress bar; when it says *Complete*, Jellyfin will pick the item
   up on its next scan.

The result on disk:

```
~/Videos/qBittorrent/Movies/Dune Part Two (2024)/
├── Dune Part Two (2024).mkv
└── Dune Part Two (2024).srt     ← English subtitle, moved next to the video
```

If qBittorrent could not apply a name (for example the name was already taken),
the card tells you exactly what happened. The download itself is not affected.

### Authentication

Two options, both in `config.json`:

1. **Username and password** (works on every version). Put the Web UI
   credentials in `qbittorrent.username` / `qbittorrent.password`.
2. **API key** (recommended on qBittorrent 5.2+). In qBittorrent open
   **Tools → Options → Web UI → API Key**, click **Generate API key**, then
   **Copy API key**, and put it in `qbittorrent.api_key` in `config.json`.
   The key takes effect immediately.

If "Bypass authentication for clients on localhost" is enabled in qBittorrent,
no password will ever be accepted — the app detects this and works without
credentials instead.

## Troubleshooting

**The page shows "Can't reach qBittorrent".**
Is qBittorrent running? Is the Web UI enabled on `127.0.0.1:8080`? Check the
username/password in `config.json`. `curl -s http://127.0.0.1:8765/api/health`
shows the exact error.

**"Rejected the username or password" even though they are correct.**
qBittorrent 5.2 changed how its login endpoint answers; this app supports both
old and new versions. If it still fails, generate an API key (see above) — that
path skips the login step entirely. To see what qBittorrent really replies:

```bash
curl -i -H 'Referer: http://127.0.0.1:8080' \
  --data 'username=YOURUSER&password=YOURPASS' \
  http://127.0.0.1:8080/api/v2/auth/login
```

`204` means the credentials are correct, `401` means they were rejected,
`403` means your IP is temporarily banned after too many attempts (wait an hour).

**"Naming failed" on a download card.**
qBittorrent refused a rename (usually a name conflict). The download keeps
going; rename it by hand in qBittorrent or add it again.

**Jellyfin does not show the new movie.**
New top-level folders are not always detected automatically — trigger a library
scan in Jellyfin. Also make sure the library paths in `config.json` are the
same folders Jellyfin scans.

**Subtitles do not show up.**
Only `.srt` files are handled, and only when you typed a file name. English
subtitles are preferred over other languages.

## Good to know

- Downloads are tagged `remote-dl` in qBittorrent so you can spot them easily.
- Naming is best-effort: for a pack with several video files, only the largest
  is renamed (the card tells you how many were found).
- The app listens on `127.0.0.1` only; qBittorrent's own Web UI is never
  exposed.
- Adding a magnet that is already in qBittorrent reports a friendly
  "already added" message instead of downloading it twice.

### Why not just open qBittorrent's Web UI from my phone?

You can — but then you get its full desktop interface with every setting, and
you still have to rename the folder, the video and the subtitle by hand after
the download. This app only does the one job, the way Jellyfin wants it.

## For developers

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest          # 48 tests, no network needed

QBT_REMOTE_MOCK=1 .venv/bin/python main.py   # fake qBittorrent
```

JSON API (same thing the page uses):

- `POST /api/downloads` — add a batch of entries
  (`{ "entries": [{ "media_type", "magnet", "folder_name", "file_name" }] }`)
  and get a per-entry result.
- `GET /api/downloads` — tracked requests plus live progress.
- `GET /api/health` — qBittorrent reachability, version, auth mode, libraries.

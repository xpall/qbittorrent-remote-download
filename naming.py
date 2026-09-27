"""Name, magnet and layout helpers shared by the API and the worker."""

from __future__ import annotations

import base64
import binascii
import os
import re
import unicodedata
import urllib.parse
from typing import Iterable

BTIH_HEX_RE = re.compile(r"[0-9a-fA-F]{40}")
BTIH_BASE32_RE = re.compile(r"[A-Za-z2-7]{32}")

VIDEO_EXTENSIONS = {
    ".3gp",
    ".avi",
    ".divx",
    ".flv",
    ".m2ts",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp4",
    ".mpeg",
    ".mpg",
    ".ogv",
    ".ts",
    ".vob",
    ".webm",
    ".wmv",
}

SUBTITLE_EXTENSION = ".srt"

# "english", "eng" or "en" as a separate token (e.g. Movie.en.srt,
# Movie.en-US.srt, Subs/English.srt) without matching words that merely
# contain those letters (Extended.srt, Se7en.srt, 1080p.srt).
ENGLISH_SUBTITLE_RE = re.compile(
    r"(?:^|[^a-z0-9])(?:english|eng|en)(?:[^a-z0-9]|$)", re.IGNORECASE
)

MAX_NAME_LENGTH = 150


class InvalidName(ValueError):
    """A user supplied folder or file name is not usable."""


def normalize_btih(value: str) -> str | None:
    """Return a lowercase hex info hash for a btih value (hex or base32)."""
    value = value.strip()
    if BTIH_HEX_RE.fullmatch(value):
        return value.lower()
    if BTIH_BASE32_RE.fullmatch(value):
        try:
            raw = base64.b32decode(value.upper())
        except (binascii.Error, ValueError):
            return None
        if len(raw) != 20:
            return None
        return raw.hex()
    return None


def parse_magnet_hash(magnet: str) -> str | None:
    """Extract the btih info hash from a magnet link, if there is one."""
    magnet = (magnet or "").strip()
    if not magnet.lower().startswith("magnet:?"):
        return None
    query = urllib.parse.parse_qs(magnet[len("magnet:?") :], keep_blank_values=True)
    for xt in query.get("xt", []):
        if xt.lower().startswith("urn:btih:"):
            return normalize_btih(xt[len("urn:btih:") :])
    return None


def sanitize_name(value: str, label: str) -> str:
    """Validate a folder or file name typed by the user.

    The names are used for qBittorrent's rename API and never as
    filesystem paths by this app, but keeping them sane avoids moving
    content outside the configured library and keeps Jellyfin happy.
    """
    value = unicodedata.normalize("NFC", value or "").strip()

    if not value:
        raise InvalidName(f"The {label} cannot be empty.")
    if len(value) > MAX_NAME_LENGTH:
        raise InvalidName(f"The {label} is longer than {MAX_NAME_LENGTH} characters.")
    if value in {".", ".."} or value.startswith("."):
        raise InvalidName(f"The {label} cannot start with a dot.")
    if value.endswith("."):
        raise InvalidName(f"The {label} cannot end with a dot.")
    for char in value:
        if char in "/\\" or unicodedata.category(char) == "Cc":
            raise InvalidName(f"The {label} cannot contain slashes or control characters.")
    return value


def is_video(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in VIDEO_EXTENSIONS


def is_subtitle(path: str) -> bool:
    return path.lower().endswith(SUBTITLE_EXTENSION)


def looks_english(path: str) -> bool:
    """True when an .srt name carries an English language hint."""
    stem = path.rsplit("/", 1)[-1]
    if stem.lower().endswith(SUBTITLE_EXTENSION):
        stem = stem[: -len(SUBTITLE_EXTENSION)]
    return bool(ENGLISH_SUBTITLE_RE.search(stem))


def choose_subtitle(files: Iterable[dict]) -> dict | None:
    """Pick the best .srt for a movie: English first, then largest."""
    candidates = [file for file in files if is_subtitle(str(file.get("name", "")))]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda file: (
            looks_english(str(file["name"])),
            int(file.get("size") or 0),
            str(file["name"]),
        ),
    )


def desired_file_name(requested: str, old_path: str) -> str:
    """Final file name, keeping the original video extension unless the
    user already typed a video extension themselves."""
    requested = requested.strip()
    requested_suffix = os.path.splitext(requested)[1].lower()
    if requested_suffix in VIDEO_EXTENSIONS:
        return requested
    original_suffix = os.path.splitext(old_path)[1]
    return requested + original_suffix


def common_root(paths: Iterable[str]) -> str | None:
    """The single top level folder shared by every path, if there is one."""
    roots: set[str] = set()
    for path in paths:
        if "/" not in path:
            return None
        roots.add(path.split("/", 1)[0])
    if len(roots) == 1:
        return roots.pop()
    return None


def with_basename(path: str, new_basename: str) -> str:
    """Replace the last path component of a torrent relative path."""
    if "/" in path:
        parent = path.rsplit("/", 1)[0]
        return f"{parent}/{new_basename}"
    return new_basename

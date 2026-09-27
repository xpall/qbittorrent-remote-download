import base64

import pytest

from naming import (
    InvalidName,
    common_root,
    desired_file_name,
    parse_magnet_hash,
    sanitize_name,
    with_basename,
)

HASH = "0123456789abcdef0123456789abcdef01234567"


def test_parse_magnet_hex():
    assert parse_magnet_hash(f"magnet:?xt=urn:btih:{HASH}&dn=Example") == HASH
    assert parse_magnet_hash(f"magnet:?xt=urn:btih:{HASH.upper()}") == HASH


def test_parse_magnet_base32():
    encoded = base64.b32encode(bytes.fromhex(HASH)).decode()
    assert parse_magnet_hash(f"magnet:?xt=urn:btih:{encoded}") == HASH
    assert parse_magnet_hash(f"magnet:?xt=urn:btih:{encoded.lower()}") == HASH


@pytest.mark.parametrize(
    "value",
    [
        "",
        "https://example.com/file.torrent",
        "magnet:?dn=NoHash",
        "magnet:?xt=urn:btmh:1220abcdef",
        "magnet:?xt=urn:btih:notahash",
        "magnet:?xt=urn:btih:0123",
    ],
)
def test_parse_magnet_invalid(value):
    assert parse_magnet_hash(value) is None


def test_sanitize_name_ok():
    assert sanitize_name("  Dune Part Two (2024)  ", "folder name") == "Dune Part Two (2024)"
    assert sanitize_name("Spider-Man: No Way Home", "file name") == "Spider-Man: No Way Home"


@pytest.mark.parametrize(
    "value",
    ["", "   ", ".hidden", "..", "with/slash", "with\\backslash", "ends.", "bad\nname"],
)
def test_sanitize_name_invalid(value):
    with pytest.raises(InvalidName):
        sanitize_name(value, "folder name")


def test_sanitize_name_too_long():
    with pytest.raises(InvalidName):
        sanitize_name("x" * 151, "folder name")


def test_desired_file_name_keeps_extension():
    assert desired_file_name("Dune Part Two (2024)", "a/b.mkv") == "Dune Part Two (2024).mkv"
    assert desired_file_name("Dune Part Two (2024).mp4", "a/b.mkv") == "Dune Part Two (2024).mp4"
    assert desired_file_name("Dune Part Two (2024)", "a/b") == "Dune Part Two (2024)"


def test_common_root():
    assert common_root(["X/a.mkv", "X/Subs/b.srt"]) == "X"
    assert common_root(["X/a.mkv"]) == "X"
    assert common_root(["a.mkv", "Subs/b.srt"]) is None
    assert common_root(["X/a.mkv", "Y/b.srt"]) is None
    assert common_root([]) is None


def test_with_basename():
    assert with_basename("X/a.mkv", "b.mkv") == "X/b.mkv"
    assert with_basename("a.mkv", "b.mkv") == "b.mkv"

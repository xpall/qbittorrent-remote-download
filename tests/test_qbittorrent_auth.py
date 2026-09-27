import asyncio

import httpx
import pytest

from config import QbittorrentConfig
from qbittorrent import QbitAuthError, QbitClient, QbitError

BASE_URL = "http://127.0.0.1:8080"


def make_client(handler, **overrides) -> QbitClient:
    config = QbittorrentConfig(
        base_url=BASE_URL,
        username="admin",
        password="secret",
        **overrides,
    )
    return QbitClient(config, transport=httpx.MockTransport(handler))


def test_login_204_is_success():
    """qBittorrent 5.2 returns 204 with an empty body on a valid login."""
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/api/v2/auth/login":
            return httpx.Response(
                204, headers={"Set-Cookie": "QBT_SID_8080=abc; path=/"}
            )
        if request.url.path == "/api/v2/app/version":
            return httpx.Response(200, text="v5.2.3")
        return httpx.Response(404)

    client = make_client(handler)

    async def scenario():
        assert await client.app_version() == "v5.2.3"
        assert client.auth_mode == "password"

    asyncio.run(scenario())
    assert paths == ["/api/v2/auth/login", "/api/v2/app/version"]


def test_login_200_ok_is_success():
    """qBittorrent <= 5.1 returns 200 with the body "Ok."."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/auth/login":
            return httpx.Response(200, text="Ok.")
        return httpx.Response(200, text="v4.6.7")

    client = make_client(handler)

    async def scenario():
        assert await client.app_version() == "v4.6.7"

    asyncio.run(scenario())
    assert client.auth_mode == "password"


def test_login_200_fails_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/auth/login":
            return httpx.Response(200, text="Fails.")
        return httpx.Response(200, text="v4.6.7")

    client = make_client(handler)

    with pytest.raises(QbitAuthError, match="username or password"):
        asyncio.run(client.app_version())


def test_login_401_without_bypass_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="Unauthorized")

    client = make_client(handler)

    with pytest.raises(QbitAuthError, match="401"):
        asyncio.run(client.app_version())


def test_login_403_reports_ban():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="Your IP is banned")

    client = make_client(handler)

    with pytest.raises(QbitAuthError, match="banned"):
        asyncio.run(client.app_version())


def test_unexpected_login_status_reported():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="bad gateway")

    client = make_client(handler)

    with pytest.raises(QbitError, match="HTTP 502") as error:
        asyncio.run(client.app_version())
    assert not isinstance(error.value, QbitAuthError)


def test_api_key_skips_login_and_sends_bearer():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/api/v2/app/version":
            return httpx.Response(200, text="v5.2.3")
        return httpx.Response(404)

    client = make_client(handler, api_key="qbt_0123456789abcdefghijklmnopqr")

    async def scenario():
        assert await client.app_version() == "v5.2.3"
        assert client.auth_mode == "api-key"

    asyncio.run(scenario())

    assert [request.url.path for request in seen] == ["/api/v2/app/version"]
    assert seen[0].headers["Authorization"] == "Bearer qbt_0123456789abcdefghijklmnopqr"


def test_localhost_bypass_detected():
    login_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal login_calls
        if request.url.path == "/api/v2/auth/login":
            login_calls += 1
            return httpx.Response(401, text="Unauthorized")
        if request.url.path == "/api/v2/app/version":
            return httpx.Response(200, text="v5.2.3")
        return httpx.Response(404)

    client = make_client(handler)

    async def scenario():
        assert await client.app_version() == "v5.2.3"
        assert client.auth_mode == "none"

    asyncio.run(scenario())
    assert login_calls == 1  # no retry loop on the detected bypass


def test_expired_session_logs_in_again():
    login_calls = 0
    info_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal login_calls, info_calls
        if request.url.path == "/api/v2/auth/login":
            login_calls += 1
            return httpx.Response(204)
        if request.url.path == "/api/v2/torrents/info":
            info_calls += 1
            if info_calls == 1:
                return httpx.Response(401, text="Unauthorized")
            return httpx.Response(200, json=[])
        return httpx.Response(404)

    client = make_client(handler)

    async def scenario():
        return await client.torrents_info()

    assert asyncio.run(scenario()) == []
    assert login_calls == 2
    assert info_calls == 2


def test_rejected_api_key_reports_auth_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="Unauthorized")

    client = make_client(handler, api_key="qbt_wrongwrongwrongwrongwrongwr")

    with pytest.raises(QbitAuthError, match="API key"):
        asyncio.run(client.app_version())

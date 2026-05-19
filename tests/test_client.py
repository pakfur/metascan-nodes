from __future__ import annotations

import httpx
import pytest
import respx

from client.api import MetascanClient
from client.config import ClientConfig
from client.errors import ApiError, OfflineError


@respx.mock
def test_ping_returns_true_when_metascan_responds(client: MetascanClient, base_url: str):
    respx.get(f"{base_url}/api/config").mock(return_value=httpx.Response(200, json={"directories": []}))
    assert client.ping() is True


@respx.mock
def test_ping_returns_false_on_5xx(client: MetascanClient, base_url: str):
    respx.get(f"{base_url}/api/config").mock(return_value=httpx.Response(500, text="boom"))
    assert client.ping() is False


@respx.mock
def test_ping_returns_false_on_connection_refused(client: MetascanClient, base_url: str):
    respx.get(f"{base_url}/api/config").mock(side_effect=httpx.ConnectError("refused"))
    assert client.ping() is False


@respx.mock
def test_ping_returns_false_on_timeout(client: MetascanClient, base_url: str):
    respx.get(f"{base_url}/api/config").mock(side_effect=httpx.ReadTimeout("slow"))
    assert client.ping() is False


@respx.mock
def test_api_key_header_is_sent(base_url: str):
    route = respx.get(f"{base_url}/api/config").mock(return_value=httpx.Response(200, json={}))
    client = MetascanClient(
        config=ClientConfig(url=base_url, api_key="my-secret"), timeout=2.0
    )
    client.ping()
    assert route.calls.last.request.headers["X-API-Key"] == "my-secret"


@respx.mock
def test_no_api_key_header_when_unset(base_url: str):
    route = respx.get(f"{base_url}/api/config").mock(return_value=httpx.Response(200, json={}))
    client = MetascanClient(
        config=ClientConfig(url=base_url, api_key=None), timeout=2.0
    )
    client.ping()
    assert "X-API-Key" not in route.calls.last.request.headers


@respx.mock
def test_client_version_header_is_sent(client: MetascanClient, base_url: str):
    route = respx.get(f"{base_url}/api/config").mock(return_value=httpx.Response(200, json={}))
    client.ping()
    assert route.calls.last.request.headers["X-Client"].startswith("metscan-nodes/")


def test_trailing_slash_on_base_url_stripped():
    c = MetascanClient(
        config=ClientConfig(url="http://host:1234/", api_key=None), timeout=2.0
    )
    # Internal httpx client stores normalized base.
    assert str(c._http.base_url).rstrip("/") == "http://host:1234"

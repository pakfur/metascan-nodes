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


# ----- get_config -----

@respx.mock
def test_get_config_returns_directories(client: MetascanClient, base_url: str, config_payload):
    respx.get(f"{base_url}/api/config").mock(return_value=httpx.Response(200, json=config_payload))
    cfg = client.get_config()
    assert cfg["directories"] == [
        {"filepath": "/data/comfy-out", "search_subfolders": True},
        {"filepath": "/data/photos", "search_subfolders": False},
    ]


@respx.mock
def test_get_config_raises_offline_on_connect_refused(client: MetascanClient, base_url: str):
    respx.get(f"{base_url}/api/config").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(OfflineError):
        client.get_config()


@respx.mock
def test_get_config_raises_api_error_on_500(client: MetascanClient, base_url: str):
    respx.get(f"{base_url}/api/config").mock(return_value=httpx.Response(500, text="boom"))
    with pytest.raises(ApiError) as excinfo:
        client.get_config()
    assert excinfo.value.status_code == 500


# ----- list_folders (manual filter) -----

@respx.mock
def test_list_folders_returns_manual_only(client: MetascanClient, base_url: str, folders_payload):
    respx.get(f"{base_url}/api/folders").mock(return_value=httpx.Response(200, json=folders_payload))
    out = client.list_folders()
    names = [f["name"] for f in out]
    assert names == ["Portraits", "Landscapes"]
    assert all(f["kind"] == "manual" for f in out)


# ----- get_folder -----

@respx.mock
def test_get_folder_returns_record(client: MetascanClient, base_url: str, folders_payload):
    target = folders_payload[0]
    respx.get(f"{base_url}/api/folders/fld_a").mock(return_value=httpx.Response(200, json=target))
    out = client.get_folder("fld_a")
    assert out["id"] == "fld_a"
    assert out["items"] == ["/data/a/img1.png", "/data/a/img2.png", "/data/a/clip.mp4"]


@respx.mock
def test_get_folder_raises_api_error_on_404(client: MetascanClient, base_url: str):
    respx.get(f"{base_url}/api/folders/missing").mock(return_value=httpx.Response(404, text="not found"))
    with pytest.raises(ApiError) as excinfo:
        client.get_folder("missing")
    assert excinfo.value.status_code == 404


# ----- get_media_detail -----

@respx.mock
def test_get_media_detail_url_encodes_path(client: MetascanClient, base_url: str):
    detail = {"file_path": "/data/a/img 1.png", "data": {"prompt": "hello", "negative_prompt": "blur"}}
    # Spaces in the path must be URL-encoded once.
    respx.get(f"{base_url}/api/media/%2Fdata%2Fa%2Fimg%201.png").mock(
        return_value=httpx.Response(200, json=detail)
    )
    out = client.get_media_detail("/data/a/img 1.png")
    assert out["data"]["prompt"] == "hello"
    assert out["data"]["negative_prompt"] == "blur"


@respx.mock
def test_get_media_detail_404_raises_api_error(client: MetascanClient, base_url: str):
    respx.get(f"{base_url}/api/media/%2Fnope.png").mock(return_value=httpx.Response(404, text="nope"))
    with pytest.raises(ApiError) as excinfo:
        client.get_media_detail("/nope.png")
    assert excinfo.value.status_code == 404


# ----- stream_bytes -----

@respx.mock
def test_stream_bytes_returns_raw_bytes(client: MetascanClient, base_url: str):
    payload = b"\x89PNG\r\n\x1a\n\x00\x00fakepngbytes"
    respx.get(f"{base_url}/api/stream/%2Fdata%2Fimg.png").mock(
        return_value=httpx.Response(200, content=payload)
    )
    out = client.stream_bytes("/data/img.png")
    assert out == payload


@respx.mock
def test_stream_bytes_404_raises_api_error(client: MetascanClient, base_url: str):
    respx.get(f"{base_url}/api/stream/%2Fnope.png").mock(return_value=httpx.Response(404, text="x"))
    with pytest.raises(ApiError) as excinfo:
        client.stream_bytes("/nope.png")
    assert excinfo.value.status_code == 404


@respx.mock
def test_stream_bytes_offline_raises_offline_error(client: MetascanClient, base_url: str):
    respx.get(f"{base_url}/api/stream/%2Fdata%2Fimg.png").mock(side_effect=httpx.ConnectError("x"))
    with pytest.raises(OfflineError):
        client.stream_bytes("/data/img.png")


# ----- search_prompts -----

@respx.mock
def test_search_prompts_passes_filters_in_body(client: MetascanClient, base_url: str):
    route = respx.post(f"{base_url}/api/prompt/search").mock(
        return_value=httpx.Response(200, json={"prompts": []})
    )
    client.search_prompts(folder_id="fld_a", target_model="qwen", name="hero", limit=50)
    sent = route.calls.last.request
    assert sent.method == "POST"
    body = sent.read().decode()
    import json as _j
    parsed = _j.loads(body)
    assert parsed == {
        "folder_id": "fld_a",
        "target_model": "qwen",
        "name": "hero",
        "limit": 50,
    }


@respx.mock
def test_search_prompts_omits_none_or_sends_null(client: MetascanClient, base_url: str):
    route = respx.post(f"{base_url}/api/prompt/search").mock(
        return_value=httpx.Response(200, json={"prompts": []})
    )
    client.search_prompts(folder_id=None, target_model=None, name=None, limit=100)
    body = route.calls.last.request.read().decode()
    import json as _j
    parsed = _j.loads(body)
    # All three nullable filters are sent as JSON null so the server's
    # Pydantic model parses them as Optional[...] = None — see spec §7.1.
    assert parsed == {"folder_id": None, "target_model": None, "name": None, "limit": 100}


@respx.mock
def test_search_prompts_returns_rows(client: MetascanClient, base_url: str):
    rows = [
        {"id": 1, "file_path": "/x.png", "name": "hero", "prompt": "p",
         "negative": None, "target_model": "qwen", "architecture": "qwen",
         "styles": []},
    ]
    respx.post(f"{base_url}/api/prompt/search").mock(
        return_value=httpx.Response(200, json={"prompts": rows})
    )
    out = client.search_prompts(folder_id="fld_a", target_model="qwen", name=None, limit=100)
    assert out == rows


@respx.mock
def test_search_prompts_400_raises_api_error(client: MetascanClient, base_url: str):
    respx.post(f"{base_url}/api/prompt/search").mock(
        return_value=httpx.Response(400, text="smart folders not supported")
    )
    with pytest.raises(ApiError) as excinfo:
        client.search_prompts(folder_id="fld_smart", target_model=None, name=None, limit=100)
    assert excinfo.value.status_code == 400
    assert "smart folders" in excinfo.value.body_excerpt


# ----- target_models -----

@respx.mock
def test_target_models_returns_seven_canonical_values(client: MetascanClient, base_url: str):
    respx.get(f"{base_url}/api/prompt/target-models").mock(
        return_value=httpx.Response(200, json={
            "target_models": ["sd", "pony", "flux1", "flux2", "zimage", "chroma", "qwen"]
        })
    )
    out = client.target_models()
    assert out == ["sd", "pony", "flux1", "flux2", "zimage", "chroma", "qwen"]

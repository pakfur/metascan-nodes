from __future__ import annotations

from nodes.settings import MetascanSettings, get_current_override
from client.config import ClientConfig


def test_input_types_declares_url_and_api_key():
    spec = MetascanSettings.INPUT_TYPES()
    assert "required" in spec
    assert "url" in spec["required"]
    assert "api_key" in spec["required"]
    assert spec["required"]["url"][0] == "STRING"
    assert spec["required"]["api_key"][0] == "STRING"


def test_return_types_is_empty():
    assert MetascanSettings.RETURN_TYPES == ()


def test_execute_stores_override_and_returns_empty():
    node = MetascanSettings()
    out = node.apply(url="http://other:9000", api_key="key-xyz")
    assert out == ()
    cfg = get_current_override()
    assert cfg == ClientConfig(url="http://other:9000", api_key="key-xyz")


def test_execute_empty_strings_clear_override():
    MetascanSettings().apply(url="http://x", api_key="y")  # set first
    MetascanSettings().apply(url="", api_key="")
    assert get_current_override() is None

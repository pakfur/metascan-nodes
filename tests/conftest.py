"""Shared pytest fixtures for metscan-nodes tests.

`respx_mock` is provided by the respx pytest plugin automatically once
the package is installed. We add a couple of convenience fixtures for
the common URL base and a pre-populated fake metascan.
"""

from __future__ import annotations

import pytest

METASCAN_TEST_URL = "http://localhost:8700"


@pytest.fixture
def base_url() -> str:
    return METASCAN_TEST_URL


@pytest.fixture
def folders_payload() -> list[dict]:
    """Two manual folders + one smart folder. Used to assert smart
    folders are filtered out client-side."""
    return [
        {"id": "fld_a", "kind": "manual", "name": "Portraits",
         "icon": "pi-folder", "sort_order": 0, "count": 3,
         "items": ["/data/a/img1.png", "/data/a/img2.png", "/data/a/clip.mp4"],
         "created_at": "2026-05-01T00:00:00", "updated_at": "2026-05-01T00:00:00"},
        {"id": "fld_b", "kind": "manual", "name": "Landscapes",
         "icon": "pi-folder", "sort_order": 1, "count": 0, "items": [],
         "created_at": "2026-05-01T00:00:00", "updated_at": "2026-05-01T00:00:00"},
        {"id": "fld_smart", "kind": "smart", "name": "Recent Favorites",
         "icon": "pi-star", "sort_order": 2, "count": 0, "rules": {"any": []},
         "created_at": "2026-05-01T00:00:00", "updated_at": "2026-05-01T00:00:00"},
    ]


@pytest.fixture
def config_payload() -> dict:
    """Two watched directories — minimal shape the client needs."""
    return {
        "directories": [
            {"filepath": "/data/comfy-out", "search_subfolders": True},
            {"filepath": "/data/photos", "search_subfolders": False},
        ],
        "watch_directories": True,
    }


from client.api import MetascanClient
from client.config import ClientConfig


@pytest.fixture
def client(base_url: str) -> MetascanClient:
    return MetascanClient(
        config=ClientConfig(url=base_url, api_key="test-key"),
        timeout=2.0,
    )

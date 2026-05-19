# metscan-nodes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a ComfyUI custom-nodes package that gives workflows three nodes (`MetascanSaveImage`, `MetascanLoadFromFolder`, `MetascanLoadPrompt`) for talking to a running metascan instance over its HTTP API.

**Architecture:** HTTP bridge to metascan's existing FastAPI (default `http://localhost:8700`). No shared Python code or imports between projects. All persistent state lives in metascan; nodes are stateless. No GPU work happens inside the nodes themselves (load-bearing — see spec §10). Two new metascan endpoints are added in a companion change to the metascan repo: `POST /api/prompt/search` and `GET /api/prompt/target-models`.

**Tech Stack:** Python 3.10+, `httpx` (HTTP), `pillow` (PNG read/write with `PngInfo` chunks), `respx` (test-time HTTP mocking), `pytest` + `pytest-cov`. ComfyUI's environment provides `torch` (we accept and return IMAGE tensors). Linux CI via GitHub Actions.

**Cross-repo note:** Tasks 9-11 commit to `/mnt/c/Users/jtkli/gws/metascan/` (metascan's own git repo). All other tasks commit to `/mnt/c/Users/jtkli/gws/metscan-nodes/` (this repo). Each task header specifies which repo it touches.

**Spec:** `docs/superpowers/plans/../specs/2026-05-18-metscan-nodes-design.md`

---

## File Structure

**`metscan-nodes/` (this repo):**

```
metscan-nodes/
├── .gitignore                              # Python ignores
├── pyproject.toml                          # Package metadata, declared deps
├── README.md                               # Install, usage, co-host operations
├── __init__.py                             # ComfyUI entry: NODE_CLASS_MAPPINGS
├── client/
│   ├── __init__.py
│   ├── errors.py                           # ApiError, OfflineError
│   ├── config.py                           # Config resolution chain
│   └── api.py                              # MetascanClient (httpx wrapper)
├── nodes/
│   ├── __init__.py
│   ├── settings.py                         # MetascanSettings sentinel node
│   ├── save_image.py                       # MetascanSaveImage
│   ├── load_from_folder.py                 # MetascanLoadFromFolder
│   └── load_prompt.py                      # MetascanLoadPrompt
├── tests/
│   ├── __init__.py
│   ├── conftest.py                         # respx + fake-API fixtures
│   ├── test_errors.py
│   ├── test_config.py
│   ├── test_client.py
│   ├── test_settings_node.py
│   ├── test_save_image.py
│   ├── test_load_from_folder.py
│   ├── test_load_prompt.py
│   └── SMOKE.md                            # Manual end-to-end walkthrough
├── examples/
│   ├── save_and_pickup.json
│   ├── load_and_generate.json
│   └── load_prompt_chain.json
└── .github/workflows/ci.yml                # pytest matrix
```

**`metascan/` (sibling repo, tasks 9-11):**

- `metascan/core/database_sqlite.py` — add `search_saved_prompts()` method
- `backend/api/prompt.py` — add `POST /api/prompt/search` + `GET /api/prompt/target-models`
- `tests/test_prompt_api_search.py` — new test file

---

## Task 1: Project skeleton (metscan-nodes repo)

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `__init__.py`
- Create: `client/__init__.py`
- Create: `nodes/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`

No tests for this task — it's the scaffolding the other tests depend on.

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "comfyui-metscan-nodes"
version = "0.1.0"
description = "ComfyUI custom nodes for the metascan AI media browser"
requires-python = ">=3.10"
readme = "README.md"
authors = [{ name = "John" }]
dependencies = [
    "httpx>=0.27",
    "pillow>=10",
]

[project.optional-dependencies]
test = [
    "pytest>=8.0",
    "pytest-cov>=4.0",
    "respx>=0.21",
    "torch>=2.0",
]

[tool.setuptools]
# This package is not pip-installable as a normal library; it is a
# ComfyUI custom_nodes drop-in. setuptools metadata exists only so
# `pip install -e .[test]` works for the dev/test loop.
py-modules = []

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra"
```

- [ ] **Step 2: Create `.gitignore`**

```
__pycache__/
*.py[cod]
*.egg-info/
.pytest_cache/
.coverage
htmlcov/
.venv/
venv/
*.swp
.DS_Store
```

- [ ] **Step 3: Create the package init files**

`__init__.py` (root):

```python
"""ComfyUI custom_nodes entry point for metscan-nodes.

The actual NODE_CLASS_MAPPINGS / NODE_DISPLAY_NAME_MAPPINGS exports
are wired up in Task 20. This file exists now so ComfyUI's loader
sees a valid Python package and so test modules can `from client...`
imports succeed at import-time.
"""

NODE_CLASS_MAPPINGS: dict = {}
NODE_DISPLAY_NAME_MAPPINGS: dict = {}
WEB_DIRECTORY = None

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
```

`client/__init__.py`:

```python
"""HTTP client + config + error types for talking to metascan."""
```

`nodes/__init__.py`:

```python
"""ComfyUI node classes for metscan-nodes."""
```

`tests/__init__.py`: empty file.

- [ ] **Step 4: Create `tests/conftest.py`**

```python
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
```

- [ ] **Step 5: Verify the skeleton imports cleanly**

Run:

```bash
cd /mnt/c/Users/jtkli/gws/metscan-nodes
python -m venv .venv
. .venv/bin/activate
pip install -e ".[test]"
python -c "import client, nodes; print('ok')"
pytest tests/ -q --collect-only
```

Expected: `ok` on stdout; pytest collects 0 tests (no tests yet) without import errors.

- [ ] **Step 6: Commit**

```bash
cd /mnt/c/Users/jtkli/gws/metscan-nodes
git add pyproject.toml .gitignore __init__.py client/__init__.py nodes/__init__.py tests/__init__.py tests/conftest.py
git commit -m "$(cat <<'EOF'
Scaffold metscan-nodes package layout

Empty package directories, pyproject with httpx + pillow runtime deps
and pytest + respx test extras, baseline conftest fixtures for the
common test URL / fake-folder / fake-config payloads.
EOF
)"
```

---

## Task 2: client/errors.py — ApiError, OfflineError (metscan-nodes repo)

**Files:**
- Create: `client/errors.py`
- Test: `tests/test_errors.py`

- [ ] **Step 1: Write failing tests**

`tests/test_errors.py`:

```python
from client.errors import ApiError, OfflineError


def test_api_error_carries_status_and_body():
    e = ApiError(status_code=404, body_excerpt="not found")
    assert e.status_code == 404
    assert e.body_excerpt == "not found"
    assert "404" in str(e)
    assert "not found" in str(e)


def test_api_error_truncates_long_body():
    long_body = "x" * 2000
    e = ApiError(status_code=500, body_excerpt=long_body)
    # Body is truncated to 500 chars before being stored.
    assert len(e.body_excerpt) == 500


def test_offline_error_carries_reason():
    e = OfflineError(reason="connection refused")
    assert e.reason == "connection refused"
    assert "connection refused" in str(e)


def test_errors_are_distinct_exception_subclasses():
    assert issubclass(ApiError, Exception)
    assert issubclass(OfflineError, Exception)
    assert not issubclass(ApiError, OfflineError)
    assert not issubclass(OfflineError, ApiError)
```

- [ ] **Step 2: Run tests to verify failure**

```bash
pytest tests/test_errors.py -v
```

Expected: ImportError / ModuleNotFoundError for `client.errors`.

- [ ] **Step 3: Write minimal implementation**

`client/errors.py`:

```python
"""Two error types raised by MetascanClient and consumed by the nodes."""

from __future__ import annotations


class ApiError(Exception):
    """Metascan responded with a non-2xx status or unparseable JSON.

    Carries the HTTP status code and a 500-char-capped excerpt of the
    response body so callers can surface a useful message in ComfyUI's
    node-error UI without leaking giant payloads into the log.
    """

    _MAX_BODY = 500

    def __init__(self, status_code: int, body_excerpt: str) -> None:
        self.status_code = status_code
        self.body_excerpt = (body_excerpt or "")[: self._MAX_BODY]
        super().__init__(f"metascan API error {status_code}: {self.body_excerpt}")


class OfflineError(Exception):
    """Metascan is unreachable (connection refused, DNS, or timeout)."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"metascan offline: {reason}")
```

- [ ] **Step 4: Run tests to verify pass**

```bash
pytest tests/test_errors.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add client/errors.py tests/test_errors.py
git commit -m "$(cat <<'EOF'
Add ApiError + OfflineError for the metascan client

Two distinct exception types — ApiError for HTTP non-2xx (carries
status + truncated body excerpt) and OfflineError for unreachable
server. Body excerpts cap at 500 chars to keep logs sane.
EOF
)"
```

---

## Task 3: client/config.py — config resolution chain (metscan-nodes repo)

Resolution order (highest priority first, per spec §4):
1. `MetascanSettings` node values (passed in explicitly when present)
2. Env vars `METASCAN_URL`, `METASCAN_API_KEY`
3. `~/.config/metscan-nodes/config.json`
4. Defaults: `http://localhost:8700`, no API key

**Files:**
- Create: `client/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write failing tests**

`tests/test_config.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

import pytest

from client.config import ClientConfig, resolve_config


def test_defaults_when_nothing_set(monkeypatch, tmp_path):
    monkeypatch.delenv("METASCAN_URL", raising=False)
    monkeypatch.delenv("METASCAN_API_KEY", raising=False)
    monkeypatch.setattr("client.config._CONFIG_FILE", tmp_path / "missing.json")

    cfg = resolve_config(settings_override=None)

    assert cfg == ClientConfig(url="http://localhost:8700", api_key=None)


def test_env_vars_override_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("METASCAN_URL", "http://envhost:9000")
    monkeypatch.setenv("METASCAN_API_KEY", "env-key")
    monkeypatch.setattr("client.config._CONFIG_FILE", tmp_path / "missing.json")

    cfg = resolve_config(settings_override=None)

    assert cfg == ClientConfig(url="http://envhost:9000", api_key="env-key")


def test_file_overrides_defaults_but_env_wins(monkeypatch, tmp_path):
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({
        "url": "http://filehost:7000", "api_key": "file-key",
    }))
    monkeypatch.setattr("client.config._CONFIG_FILE", cfg_file)
    monkeypatch.delenv("METASCAN_URL", raising=False)
    monkeypatch.delenv("METASCAN_API_KEY", raising=False)

    cfg = resolve_config(settings_override=None)
    assert cfg.url == "http://filehost:7000"
    assert cfg.api_key == "file-key"

    monkeypatch.setenv("METASCAN_URL", "http://envhost:9000")
    cfg2 = resolve_config(settings_override=None)
    assert cfg2.url == "http://envhost:9000"
    assert cfg2.api_key == "file-key"  # env didn't set the key, file value stands


def test_settings_override_wins_over_everything(monkeypatch, tmp_path):
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({"url": "http://filehost", "api_key": "filek"}))
    monkeypatch.setattr("client.config._CONFIG_FILE", cfg_file)
    monkeypatch.setenv("METASCAN_URL", "http://envhost")
    monkeypatch.setenv("METASCAN_API_KEY", "envk")

    cfg = resolve_config(
        settings_override=ClientConfig(url="http://overhost", api_key="overk")
    )
    assert cfg == ClientConfig(url="http://overhost", api_key="overk")


def test_empty_string_settings_override_treated_as_unset(monkeypatch, tmp_path):
    """The MetascanSettings node passes empty strings for unset fields;
    those must not blank out env / file / default values."""
    monkeypatch.setenv("METASCAN_URL", "http://envhost")
    monkeypatch.setenv("METASCAN_API_KEY", "envk")
    monkeypatch.setattr("client.config._CONFIG_FILE", tmp_path / "missing.json")

    cfg = resolve_config(settings_override=ClientConfig(url="", api_key=""))
    assert cfg == ClientConfig(url="http://envhost", api_key="envk")
```

- [ ] **Step 2: Run tests to verify failure**

```bash
pytest tests/test_config.py -v
```

Expected: ImportError for `client.config`.

- [ ] **Step 3: Write minimal implementation**

`client/config.py`:

```python
"""Resolve metascan connection settings for the client.

Precedence (highest first):
  1. ``settings_override`` argument (typically from a MetascanSettings node)
  2. Env vars ``METASCAN_URL`` and ``METASCAN_API_KEY``
  3. ``~/.config/metscan-nodes/config.json``
  4. Defaults: http://localhost:8700, no API key

Empty strings in the settings override or env vars are treated as unset
so lower-priority sources can still supply a value. This matters because
ComfyUI's STRING widget on the settings node sends "" for blank inputs.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_DEFAULT_URL = "http://localhost:8700"
_CONFIG_FILE = Path.home() / ".config" / "metscan-nodes" / "config.json"


@dataclass(frozen=True)
class ClientConfig:
    url: str
    api_key: Optional[str]


def _from_file() -> tuple[Optional[str], Optional[str]]:
    if not _CONFIG_FILE.exists():
        return None, None
    try:
        data = json.loads(_CONFIG_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return None, None
    url = data.get("url") or None
    api_key = data.get("api_key") or None
    return url, api_key


def _from_env() -> tuple[Optional[str], Optional[str]]:
    url = os.environ.get("METASCAN_URL") or None
    api_key = os.environ.get("METASCAN_API_KEY") or None
    return url, api_key


def resolve_config(settings_override: Optional[ClientConfig]) -> ClientConfig:
    """Apply the four-tier precedence and return the final config."""

    file_url, file_key = _from_file()
    env_url, env_key = _from_env()

    # Treat "" as unset for the override too.
    override_url = settings_override.url if settings_override and settings_override.url else None
    override_key = (
        settings_override.api_key if settings_override and settings_override.api_key else None
    )

    url = override_url or env_url or file_url or _DEFAULT_URL
    api_key = override_key or env_key or file_key  # may stay None

    return ClientConfig(url=url, api_key=api_key)
```

- [ ] **Step 4: Run tests to verify pass**

```bash
pytest tests/test_config.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add client/config.py tests/test_config.py
git commit -m "$(cat <<'EOF'
Add client config resolution (settings → env → file → defaults)

Four-tier precedence; empty strings at any tier fall through so a
settings node with blank fields doesn't clobber env or file values.
ClientConfig is a frozen dataclass; the file location is a module-
level constant so tests can monkeypatch it.
EOF
)"
```

---

## Task 4: client/api.py — MetascanClient base + ping (metscan-nodes repo)

**Files:**
- Create: `client/api.py`
- Modify: `tests/conftest.py` (add a `client` fixture)
- Test: `tests/test_client.py`

- [ ] **Step 1: Add a `client` fixture to `tests/conftest.py`**

Append to `tests/conftest.py`:

```python
from client.api import MetascanClient
from client.config import ClientConfig


@pytest.fixture
def client(base_url: str) -> MetascanClient:
    return MetascanClient(
        config=ClientConfig(url=base_url, api_key="test-key"),
        timeout=2.0,
    )
```

- [ ] **Step 2: Write failing tests**

`tests/test_client.py`:

```python
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
```

- [ ] **Step 3: Run tests to verify failure**

```bash
pytest tests/test_client.py -v
```

Expected: ImportError for `client.api`.

- [ ] **Step 4: Write minimal implementation**

`client/api.py`:

```python
"""Synchronous HTTP client for talking to a running metascan instance.

Sync only — ComfyUI's node execute interface is synchronous. Each
MetascanClient owns one httpx.Client; reuse it for the lifetime of the
workflow run.
"""

from __future__ import annotations

import httpx

from .config import ClientConfig
from .errors import ApiError, OfflineError

__version__ = "0.1.0"


class MetascanClient:
    def __init__(self, config: ClientConfig, timeout: float = 10.0) -> None:
        headers = {"X-Client": f"metscan-nodes/{__version__}"}
        if config.api_key:
            headers["X-API-Key"] = config.api_key
        self._http = httpx.Client(
            base_url=config.url.rstrip("/"),
            headers=headers,
            timeout=timeout,
        )

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------
    def ping(self) -> bool:
        """Quick aliveness check. Hits /api/config (cheap, always exists).
        Returns True only on a 2xx response. Connection / timeout / non-2xx
        all return False — callers use this to decide whether to populate
        dropdowns vs show an offline sentinel."""
        try:
            r = self._http.get("/api/config")
            return 200 <= r.status_code < 300
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout, httpx.TimeoutException):
            return False

    # ------------------------------------------------------------------
    # Shared error mapping
    # ------------------------------------------------------------------
    def _request_json(self, method: str, path: str, *, json_body=None, params=None):
        try:
            r = self._http.request(method, path, json=json_body, params=params)
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            raise OfflineError(reason=str(e)) from e
        except (httpx.ReadTimeout, httpx.TimeoutException) as e:
            raise OfflineError(reason=f"timeout: {e}") from e
        if r.status_code >= 400:
            raise ApiError(status_code=r.status_code, body_excerpt=r.text)
        try:
            return r.json()
        except ValueError as e:
            raise ApiError(
                status_code=r.status_code,
                body_excerpt=f"invalid JSON: {r.text[:200]}",
            ) from e
```

- [ ] **Step 5: Run tests to verify pass**

```bash
pytest tests/test_client.py -v
```

Expected: 8 passed.

- [ ] **Step 6: Commit**

```bash
git add client/api.py tests/conftest.py tests/test_client.py
git commit -m "$(cat <<'EOF'
Add MetascanClient base with ping() + shared error mapping

httpx-backed sync client. ping() hits /api/config and returns bool —
used by INPUT_TYPES dropdowns to decide between populated combos and
the offline sentinel. _request_json() centralizes httpx → ApiError /
OfflineError translation for the typed methods added in subsequent
tasks.
EOF
)"
```

---

## Task 5: client.api — get_config, list_folders (manual filter), get_folder (metscan-nodes repo)

**Files:**
- Modify: `client/api.py` (add three methods)
- Modify: `tests/test_client.py` (add tests)

- [ ] **Step 1: Write failing tests**

Append to `tests/test_client.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify failure**

```bash
pytest tests/test_client.py -v -k "config or folder"
```

Expected: AttributeError on `get_config` / `list_folders` / `get_folder`.

- [ ] **Step 3: Add methods to `client/api.py`**

Append inside the `MetascanClient` class (after `_request_json`):

```python
    # ------------------------------------------------------------------
    # Config + folders
    # ------------------------------------------------------------------
    def get_config(self) -> dict:
        """Return metascan's full config payload. Callers typically only
        consume the `directories` array (for the save node dropdown)."""
        return self._request_json("GET", "/api/config")

    def list_folders(self) -> list[dict]:
        """Return only `kind=='manual'` folders. Smart folders are filtered
        client-side because metascan's smart-folder rule engine lives in
        the frontend; the nodes can't resolve smart membership without a
        Python evaluator (deferred — see spec §2)."""
        folders = self._request_json("GET", "/api/folders")
        return [f for f in folders if f.get("kind") == "manual"]

    def get_folder(self, folder_id: str) -> dict:
        """Return a single folder record. For manual folders the record
        already includes a resolved `items: [path, ...]` list."""
        return self._request_json("GET", f"/api/folders/{folder_id}")
```

- [ ] **Step 4: Run tests to verify pass**

```bash
pytest tests/test_client.py -v
```

Expected: 13 passed (8 from Task 4 + 5 new).

- [ ] **Step 5: Commit**

```bash
git add client/api.py tests/test_client.py
git commit -m "$(cat <<'EOF'
Add get_config / list_folders / get_folder to MetascanClient

list_folders client-side-filters to kind=='manual' because metascan's
smart-folder rule engine lives in the frontend Pinia store, not in
the backend — see spec §2 non-goals.
EOF
)"
```

---

## Task 6: client.api — get_media_detail, stream_bytes (metscan-nodes repo)

**Files:**
- Modify: `client/api.py`
- Modify: `tests/test_client.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_client.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify failure**

```bash
pytest tests/test_client.py -v -k "media_detail or stream_bytes"
```

Expected: AttributeError on the new methods.

- [ ] **Step 3: Add methods to `client/api.py`**

Add to the top of `client/api.py` (after the existing imports):

```python
from urllib.parse import quote
```

Append inside the `MetascanClient` class (after `get_folder`):

```python
    # ------------------------------------------------------------------
    # Media
    # ------------------------------------------------------------------
    @staticmethod
    def _encode_path(file_path: str) -> str:
        # safe="" so '/' is percent-encoded — metascan's path-style routes
        # expect a single encoded path segment, not a multi-segment path.
        return quote(file_path, safe="")

    def get_media_detail(self, file_path: str) -> dict:
        """Return the full media detail record including extracted prompt
        metadata under the ``data`` key (positive/negative prompt, model,
        loras, etc., populated by metascan's ComfyUI/SwarmUI/Fooocus
        extractors at scan time)."""
        return self._request_json("GET", f"/api/media/{self._encode_path(file_path)}")

    def stream_bytes(self, file_path: str) -> bytes:
        """Return raw file bytes for the given media path. Used by load
        nodes to fetch the image before PIL-decoding to a tensor."""
        try:
            r = self._http.get(f"/api/stream/{self._encode_path(file_path)}")
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            raise OfflineError(reason=str(e)) from e
        except (httpx.ReadTimeout, httpx.TimeoutException) as e:
            raise OfflineError(reason=f"timeout: {e}") from e
        if r.status_code >= 400:
            raise ApiError(status_code=r.status_code, body_excerpt=r.text[:500])
        return r.content
```

- [ ] **Step 4: Run tests to verify pass**

```bash
pytest tests/test_client.py -v
```

Expected: 18 passed.

- [ ] **Step 5: Commit**

```bash
git add client/api.py tests/test_client.py
git commit -m "$(cat <<'EOF'
Add get_media_detail + stream_bytes to MetascanClient

Path-segment endpoints — quote(safe='') so the file path becomes one
percent-encoded segment (the routes are file_path:path on the server
side). stream_bytes bypasses _request_json because we want raw bytes,
not JSON, but reuses the same offline/error mapping shape.
EOF
)"
```

---

## Task 7: client.api — search_prompts, target_models (metscan-nodes repo)

**Files:**
- Modify: `client/api.py`
- Modify: `tests/test_client.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_client.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify failure**

```bash
pytest tests/test_client.py -v -k "search_prompts or target_models"
```

Expected: AttributeError on the new methods.

- [ ] **Step 3: Add methods to `client/api.py`**

Append inside the `MetascanClient` class:

```python
    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------
    def search_prompts(
        self,
        folder_id: str | None,
        target_model: str | None,
        name: str | None,
        limit: int = 100,
    ) -> list[dict]:
        """POST /api/prompt/search — new endpoint added in metascan's
        companion PR. All three filter fields are nullable; null = no
        filter. limit is hard-capped at 500 server-side; client caller
        should respect that."""
        body = {
            "folder_id": folder_id,
            "target_model": target_model,
            "name": name,
            "limit": limit,
        }
        out = self._request_json("POST", "/api/prompt/search", json_body=body)
        return out.get("prompts", [])

    def target_models(self) -> list[str]:
        """Returns metascan's canonical TargetModel literal values:
        ['sd','pony','flux1','flux2','zimage','chroma','qwen']. The node
        layer injects 'any' as a virtual UI option that maps back to
        target_model=None in search_prompts()."""
        out = self._request_json("GET", "/api/prompt/target-models")
        return list(out.get("target_models", []))
```

- [ ] **Step 4: Run tests to verify pass**

```bash
pytest tests/test_client.py -v
```

Expected: 23 passed.

- [ ] **Step 5: Commit**

```bash
git add client/api.py tests/test_client.py
git commit -m "$(cat <<'EOF'
Add search_prompts + target_models to MetascanClient

Wraps the two new metascan endpoints (POST /api/prompt/search and
GET /api/prompt/target-models — added in the companion PR, Tasks
9-11). All three filter fields on search are nullable and sent as
JSON null; the node layer maps the UI 'any' option to None.
EOF
)"
```

---

## Task 8: client.api — 60s combo dropdown cache (metscan-nodes repo)

ComfyUI calls `INPUT_TYPES()` whenever the user opens the node editor. Hitting metascan every time is wasteful and slow when the server is offline. We cache the dropdown-feeding calls (`get_config`, `list_folders`, `target_models`) for 60s, keyed by (base_url, method-name). On cache miss with an unreachable server, return a sentinel list with the offline marker — so the node still loads in the editor.

**Files:**
- Create: `client/cache.py`
- Modify: `tests/test_client.py` (add cache tests)

- [ ] **Step 1: Write failing tests**

Append to `tests/test_client.py`:

```python
# ----- combo cache -----

from client.cache import combo_directories, combo_folders, combo_target_models, OFFLINE_SENTINEL, clear_cache


def test_clear_cache_resets_state():
    # Sanity: clear_cache is callable. Real behavior covered below.
    clear_cache()


@respx.mock
def test_combo_directories_caches_within_60s(client: MetascanClient, base_url: str, config_payload):
    clear_cache()
    route = respx.get(f"{base_url}/api/config").mock(
        return_value=httpx.Response(200, json=config_payload)
    )
    a = combo_directories(client)
    b = combo_directories(client)
    assert a == b == ["/data/comfy-out", "/data/photos"]
    assert route.call_count == 1  # second call served from cache


@respx.mock
def test_combo_folders_returns_only_manual_names(client: MetascanClient, base_url: str, folders_payload):
    clear_cache()
    respx.get(f"{base_url}/api/folders").mock(
        return_value=httpx.Response(200, json=folders_payload)
    )
    out = combo_folders(client)
    assert out == ["Portraits", "Landscapes"]


@respx.mock
def test_combo_target_models_appends_any_virtual_option(client: MetascanClient, base_url: str):
    clear_cache()
    respx.get(f"{base_url}/api/prompt/target-models").mock(
        return_value=httpx.Response(200, json={
            "target_models": ["sd", "pony", "flux1", "flux2", "zimage", "chroma", "qwen"]
        })
    )
    out = combo_target_models(client)
    assert out == ["sd", "pony", "flux1", "flux2", "zimage", "chroma", "qwen", "any"]


@respx.mock
def test_combo_returns_offline_sentinel_when_server_down(client: MetascanClient, base_url: str):
    clear_cache()
    respx.get(f"{base_url}/api/config").mock(side_effect=httpx.ConnectError("x"))
    out = combo_directories(client)
    assert out == [OFFLINE_SENTINEL]


@respx.mock
def test_offline_failure_is_not_cached(client: MetascanClient, base_url: str, config_payload):
    """If metascan was offline at first call but comes back, the next
    call must refetch — we don't want a 60s window where the dropdown
    stays empty after the server recovers."""
    clear_cache()
    route = respx.get(f"{base_url}/api/config").mock(side_effect=httpx.ConnectError("x"))
    out_offline = combo_directories(client)
    assert out_offline == [OFFLINE_SENTINEL]

    route.mock(return_value=httpx.Response(200, json=config_payload))
    out_online = combo_directories(client)
    assert out_online == ["/data/comfy-out", "/data/photos"]
```

- [ ] **Step 2: Run tests to verify failure**

```bash
pytest tests/test_client.py -v -k combo
```

Expected: ImportError for `client.cache`.

- [ ] **Step 3: Write `client/cache.py`**

```python
"""60-second TTL cache for dropdown-feeding client calls.

ComfyUI calls INPUT_TYPES() at editor load and again whenever the user
opens a node's settings, so an uncached call would hammer metascan for
no good reason. We cache only the calls that feed dropdowns; per-execute
calls in nodes/*.py go through the client directly.

Cache misses for unreachable metascan return OFFLINE_SENTINEL in a
single-entry list so the node still renders in the editor; we do NOT
cache the failure so the dropdown recovers immediately when metascan
comes back online.
"""

from __future__ import annotations

import time
from typing import Callable, TypeVar

from .api import MetascanClient
from .errors import ApiError, OfflineError

OFFLINE_SENTINEL = "<metascan offline — check MetascanSettings>"
_TTL_SECONDS = 60.0

T = TypeVar("T")

# key: (base_url, method_name) -> (fetched_at, value)
_CACHE: dict[tuple[str, str], tuple[float, list[str]]] = {}


def clear_cache() -> None:
    """Test helper. Production code should never need this — entries
    naturally expire after _TTL_SECONDS."""
    _CACHE.clear()


def _cached(client: MetascanClient, method_name: str, fetch: Callable[[], list[str]]) -> list[str]:
    base = str(client._http.base_url).rstrip("/")
    key = (base, method_name)
    now = time.monotonic()
    hit = _CACHE.get(key)
    if hit and (now - hit[0]) < _TTL_SECONDS:
        return hit[1]
    try:
        value = fetch()
    except (OfflineError, ApiError):
        # Don't cache failures — let next call retry immediately.
        return [OFFLINE_SENTINEL]
    _CACHE[key] = (now, value)
    return value


def combo_directories(client: MetascanClient) -> list[str]:
    """Watched-directory filepaths from GET /api/config, for the
    MetascanSaveImage `directory` dropdown."""
    def fetch() -> list[str]:
        cfg = client.get_config()
        return [d["filepath"] for d in cfg.get("directories", [])]
    return _cached(client, "combo_directories", fetch)


def combo_folders(client: MetascanClient) -> list[str]:
    """Manual-folder names from GET /api/folders (smart folders already
    filtered by the client). Used by the load nodes' folder dropdowns."""
    def fetch() -> list[str]:
        return [f["name"] for f in client.list_folders()]
    return _cached(client, "combo_folders", fetch)


def combo_target_models(client: MetascanClient) -> list[str]:
    """Canonical TargetModel literals from GET /api/prompt/target-models
    plus the virtual 'any' option appended client-side. The node maps
    'any' → None when issuing search_prompts()."""
    def fetch() -> list[str]:
        return [*client.target_models(), "any"]
    return _cached(client, "combo_target_models", fetch)
```

- [ ] **Step 4: Run tests to verify pass**

```bash
pytest tests/test_client.py -v
```

Expected: 29 passed.

- [ ] **Step 5: Commit**

```bash
git add client/cache.py tests/test_client.py
git commit -m "$(cat <<'EOF'
Add 60s combo cache for dropdown-feeding client calls

INPUT_TYPES() runs at editor load and on every node-settings open, so
unfettered calls would hammer metascan. Cache successes for 60s, never
cache failures (so the dropdown recovers as soon as the server is
back). combo_target_models appends the virtual 'any' option that the
node maps back to None at execute time.
EOF
)"
```

---

## Task 9: (metascan repo) db.search_saved_prompts method

**Repo:** `/mnt/c/Users/jtkli/gws/metascan/`

**Files:**
- Modify: `metascan/core/database_sqlite.py` (add new method near `save_prompt` at line ~619)
- Create: `tests/test_database_saved_prompt_search.py`

- [ ] **Step 1: Read the existing saved-prompt code for context**

```bash
cd /mnt/c/Users/jtkli/gws/metascan
sed -n '395,430p' metascan/core/database_sqlite.py   # saved_prompts schema
sed -n '619,710p' metascan/core/database_sqlite.py   # save_prompt / list_saved_prompts / get_saved_prompt
```

Expected: confirm the table has columns `(id, file_path, name, prompt, negative, target_model, architecture, styles, ..., created_at, updated_at)` and the existing read methods return `Dict[str, Any]` rows. The new method follows the same shape so callers (the API route in Task 10) can return rows directly via the existing `SavedPromptOut` Pydantic model.

- [ ] **Step 2: Write failing tests**

`tests/test_database_saved_prompt_search.py`:

```python
"""Tests for DatabaseManager.search_saved_prompts.

The DB module uses the same _temp_db fixture pattern as
tests/test_folders_db.py — a fresh on-disk sqlite per test, populated
with a couple of media rows, two manual folders, one smart folder,
and a handful of saved_prompts spanning two target_models.
"""

from __future__ import annotations

import pytest

from metascan.core.database_sqlite import DatabaseManager


@pytest.fixture
def db(tmp_path):
    d = DatabaseManager(str(tmp_path / "test.db"))
    d.initialize()
    # Two media files — saved_prompts.file_path REFERENCES media.file_path
    # with ON DELETE CASCADE, so the rows must exist first.
    for path in ("/data/a/img1.png", "/data/a/img2.png", "/data/b/img3.png"):
        d.insert_or_update_media(
            file_path=path,
            file_name=path.rsplit("/", 1)[-1],
            data={"prompt": "x"},
            tags=[],
        )
    # Two manual folders, one smart.
    d.create_folder("fld_a", kind="manual", name="A", items=["/data/a/img1.png", "/data/a/img2.png"])
    d.create_folder("fld_b", kind="manual", name="B", items=["/data/b/img3.png"])
    d.create_folder("fld_smart", kind="smart", name="Smart", rules={"any": []})
    # Saved prompts: 2 against fld_a/img1, 1 against fld_a/img2, 1 against fld_b/img3.
    d.save_prompt(file_path="/data/a/img1.png", name="hero",        prompt="p1", target_model="qwen",  architecture="qwen",  styles=[], mode="generate")
    d.save_prompt(file_path="/data/a/img1.png", name="alt",         prompt="p2", target_model="sd",    architecture="sd",    styles=[], mode="generate", negative="n2")
    d.save_prompt(file_path="/data/a/img2.png", name="cinematic",   prompt="p3", target_model="qwen",  architecture="qwen",  styles=[], mode="generate")
    d.save_prompt(file_path="/data/b/img3.png", name="landscape",   prompt="p4", target_model="qwen",  architecture="qwen",  styles=[], mode="generate")
    yield d


def test_search_no_filters_returns_all(db):
    rows = db.search_saved_prompts(folder_id=None, target_model=None, name=None, limit=100)
    assert len(rows) == 4


def test_search_by_folder_returns_only_that_folders_prompts(db):
    rows = db.search_saved_prompts(folder_id="fld_a", target_model=None, name=None, limit=100)
    names = sorted(r["name"] for r in rows)
    assert names == ["alt", "cinematic", "hero"]


def test_search_by_target_model_filters_globally(db):
    rows = db.search_saved_prompts(folder_id=None, target_model="qwen", name=None, limit=100)
    names = sorted(r["name"] for r in rows)
    assert names == ["cinematic", "hero", "landscape"]


def test_search_by_name_exact_match(db):
    rows = db.search_saved_prompts(folder_id=None, target_model=None, name="hero", limit=100)
    assert len(rows) == 1
    assert rows[0]["name"] == "hero"


def test_search_combining_filters(db):
    rows = db.search_saved_prompts(folder_id="fld_a", target_model="qwen", name=None, limit=100)
    names = sorted(r["name"] for r in rows)
    assert names == ["cinematic", "hero"]


def test_search_smart_folder_raises(db):
    with pytest.raises(ValueError, match="smart"):
        db.search_saved_prompts(folder_id="fld_smart", target_model=None, name=None, limit=100)


def test_search_unknown_folder_raises(db):
    with pytest.raises(ValueError, match="not found|unknown"):
        db.search_saved_prompts(folder_id="fld_does_not_exist", target_model=None, name=None, limit=100)


def test_limit_caps_returned_rows(db):
    rows = db.search_saved_prompts(folder_id=None, target_model=None, name=None, limit=2)
    assert len(rows) == 2


def test_returned_rows_include_negative_field(db):
    rows = db.search_saved_prompts(folder_id=None, target_model="sd", name=None, limit=100)
    assert len(rows) == 1
    assert rows[0]["negative"] == "n2"
```

- [ ] **Step 3: Run tests to verify failure**

```bash
source venv/bin/activate
pytest tests/test_database_saved_prompt_search.py -v
```

Expected: AttributeError on `search_saved_prompts`.

- [ ] **Step 4: Add the method to `metascan/core/database_sqlite.py`**

Insert immediately after the existing `get_saved_prompt` method (around line 707):

```python
    def search_saved_prompts(
        self,
        folder_id: Optional[str],
        target_model: Optional[str],
        name: Optional[str],
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Search the saved_prompts table by optional folder / model / name.

        When ``folder_id`` is supplied, the folder must be ``kind='manual'`` —
        smart folders raise ``ValueError`` because their membership rule
        engine lives in the frontend (Pinia store) and would need a Python
        port before this method could resolve them. The companion
        ``POST /api/prompt/search`` route catches the ValueError and
        returns 400.

        Returns rows in the same dict shape as ``list_saved_prompts`` so
        ``SavedPromptOut`` Pydantic parsing works unchanged.
        """
        # Hard cap to match the API contract.
        capped_limit = max(1, min(limit, 500))

        with self._get_connection() as conn:
            if folder_id is not None:
                row = conn.execute(
                    "SELECT kind FROM folders WHERE id = ?", (folder_id,)
                ).fetchone()
                if row is None:
                    raise ValueError(f"folder not found: {folder_id}")
                if row["kind"] == "smart":
                    raise ValueError(
                        f"smart folders are not supported by search_saved_prompts "
                        f"(folder_id={folder_id}); manual folders only"
                    )

            sql_parts = ["SELECT * FROM saved_prompts"]
            params: List[Any] = []
            wheres: List[str] = []

            if folder_id is not None:
                sql_parts.append(
                    "JOIN folder_items USING (file_path)"
                )
                wheres.append("folder_items.folder_id = ?")
                params.append(folder_id)

            if target_model is not None:
                wheres.append("saved_prompts.target_model = ?")
                params.append(target_model)

            if name is not None:
                wheres.append("saved_prompts.name = ?")
                params.append(name)

            if wheres:
                sql_parts.append("WHERE " + " AND ".join(wheres))

            sql_parts.append("ORDER BY saved_prompts.created_at DESC")
            sql_parts.append("LIMIT ?")
            params.append(capped_limit)

            sql = " ".join(sql_parts)
            rows = conn.execute(sql, params).fetchall()

            # Mirror list_saved_prompts row shape: deserialize styles JSON
            # for caller convenience. (See list_saved_prompts at line ~667
            # for the canonical row-shaping logic.)
            import json as _json

            out: List[Dict[str, Any]] = []
            for r in rows:
                d = dict(r)
                styles_raw = d.get("styles") or "[]"
                try:
                    d["styles"] = _json.loads(styles_raw)
                except (ValueError, TypeError):
                    d["styles"] = []
                out.append(d)
            return out
```

- [ ] **Step 5: Run tests to verify pass**

```bash
pytest tests/test_database_saved_prompt_search.py -v
```

Expected: 9 passed.

Also run the full DB test suite to make sure nothing regressed:

```bash
pytest tests/test_folders_db.py tests/test_prompt_api_crud.py -v
```

Expected: All previously-passing tests still pass.

- [ ] **Step 6: Commit (in the metascan repo)**

```bash
cd /mnt/c/Users/jtkli/gws/metascan
git add metascan/core/database_sqlite.py tests/test_database_saved_prompt_search.py
git commit -m "$(cat <<'EOF'
Add DatabaseManager.search_saved_prompts

Joins saved_prompts ↔ folder_items for manual-folder scoping; ANDs in
optional target_model and name filters; caps the LIMIT at 500. Raises
ValueError for smart folder IDs and for unknown folder IDs — the
companion API route maps both to HTTP 400.

Used by the metscan-nodes MetascanLoadPrompt ComfyUI custom node.
EOF
)"
```

---

## Task 10: (metascan repo) POST /api/prompt/search route

**Repo:** `/mnt/c/Users/jtkli/gws/metascan/`

**Files:**
- Modify: `backend/api/prompt.py` (add route + Pydantic models near the end)
- Create: `tests/test_prompt_api_search.py`

- [ ] **Step 1: Inspect the existing prompt API file**

```bash
sed -n '300,400p' backend/api/prompt.py
```

Expected: confirms the existing routes follow the pattern `@router.post(...)` with Pydantic body models and `asyncio.to_thread` for DB calls. Our new route follows the same shape.

- [ ] **Step 2: Write failing tests**

`tests/test_prompt_api_search.py`:

```python
"""Tests for POST /api/prompt/search.

Uses the same FastAPI TestClient + ephemeral DB pattern as
tests/test_prompt_api_crud.py. See that file for the conftest
fixtures the test uses (client, populated_db, etc.).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def _seed(db) -> None:
    for path in ("/data/a/img1.png", "/data/a/img2.png"):
        db.insert_or_update_media(file_path=path, file_name=path.rsplit("/", 1)[-1],
                                  data={"prompt": "x"}, tags=[])
    db.create_folder("fld_a", kind="manual", name="A",
                     items=["/data/a/img1.png", "/data/a/img2.png"])
    db.create_folder("fld_smart", kind="smart", name="Smart", rules={"any": []})
    db.save_prompt(file_path="/data/a/img1.png", name="hero",      prompt="p1",
                   target_model="qwen", architecture="qwen", styles=[], mode="generate")
    db.save_prompt(file_path="/data/a/img2.png", name="cinematic", prompt="p2",
                   target_model="sd",   architecture="sd",   styles=[], mode="generate",
                   negative="n2")


def test_search_returns_matching_rows(client: TestClient, db):
    _seed(db)
    r = client.post("/api/prompt/search", json={
        "folder_id": "fld_a", "target_model": "qwen", "name": None, "limit": 100,
    })
    assert r.status_code == 200
    body = r.json()
    assert len(body["prompts"]) == 1
    assert body["prompts"][0]["name"] == "hero"


def test_search_with_all_null_filters_returns_everything(client: TestClient, db):
    _seed(db)
    r = client.post("/api/prompt/search", json={
        "folder_id": None, "target_model": None, "name": None, "limit": 100,
    })
    assert r.status_code == 200
    assert len(r.json()["prompts"]) == 2


def test_search_smart_folder_returns_400(client: TestClient, db):
    _seed(db)
    r = client.post("/api/prompt/search", json={
        "folder_id": "fld_smart", "target_model": None, "name": None, "limit": 100,
    })
    assert r.status_code == 400
    assert "smart" in r.json().get("detail", "").lower()


def test_search_unknown_folder_returns_400(client: TestClient, db):
    _seed(db)
    r = client.post("/api/prompt/search", json={
        "folder_id": "fld_missing", "target_model": None, "name": None, "limit": 100,
    })
    assert r.status_code == 400


def test_search_limit_default_is_100(client: TestClient, db):
    """Pydantic default fills in when the field is omitted."""
    _seed(db)
    r = client.post("/api/prompt/search", json={
        "folder_id": None, "target_model": None, "name": None,
    })
    assert r.status_code == 200


def test_search_limit_capped_at_500(client: TestClient, db):
    _seed(db)
    r = client.post("/api/prompt/search", json={
        "folder_id": None, "target_model": None, "name": None, "limit": 10_000,
    })
    # 422 from Pydantic ge/le validator, not a 200 with silent cap — the
    # server enforces the spec contract loudly.
    assert r.status_code == 422
```

- [ ] **Step 3: Run tests to verify failure**

```bash
pytest tests/test_prompt_api_search.py -v
```

Expected: 404 on every test (route doesn't exist) or import error.

- [ ] **Step 4: Add the route to `backend/api/prompt.py`**

Append at the end of the file:

```python
# ---- Saved-prompt search --------------------------------------------------


class PromptSearchRequest(BaseModel):
    """Filters for POST /api/prompt/search.

    All three filter fields are nullable; null = no filter. ``folder_id``
    must point to a ``kind='manual'`` folder — smart folders return 400
    because the rule engine lives in the frontend (see ``DatabaseManager.
    search_saved_prompts`` for the rationale). ``limit`` is bounded by
    Pydantic so out-of-range values fail fast with 422.
    """

    folder_id: Optional[str] = None
    target_model: Optional[str] = None
    name: Optional[str] = None
    limit: int = Field(default=100, ge=1, le=500)


class PromptSearchResponse(BaseModel):
    prompts: List[SavedPromptOut]


@router.post("/search", response_model=PromptSearchResponse)
async def search_prompts(body: PromptSearchRequest) -> PromptSearchResponse:
    db = get_db()
    try:
        rows = await asyncio.to_thread(
            db.search_saved_prompts,
            folder_id=body.folder_id,
            target_model=body.target_model,
            name=body.name,
            limit=body.limit,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return PromptSearchResponse(prompts=[SavedPromptOut(**r) for r in rows])
```

- [ ] **Step 5: Run tests to verify pass**

```bash
pytest tests/test_prompt_api_search.py -v
pytest tests/test_prompt_api_crud.py -v   # regression check
```

Expected: 6 passed in the new file; existing CRUD tests still pass.

- [ ] **Step 6: Commit**

```bash
git add backend/api/prompt.py tests/test_prompt_api_search.py
git commit -m "$(cat <<'EOF'
Add POST /api/prompt/search

Filters saved_prompts by optional folder_id / target_model / name and
returns rows in the existing SavedPromptOut shape. Smart folder IDs
and unknown folder IDs both return 400 with the underlying ValueError
message; out-of-range limit values return 422 via Pydantic.

Used by metscan-nodes' MetascanLoadPrompt ComfyUI custom node.
EOF
)"
```

---

## Task 11: (metascan repo) GET /api/prompt/target-models route

**Repo:** `/mnt/c/Users/jtkli/gws/metascan/`

**Files:**
- Modify: `backend/api/prompt.py`
- Create: `tests/test_prompt_api_target_models.py`

- [ ] **Step 1: Write failing test**

`tests/test_prompt_api_target_models.py`:

```python
from fastapi.testclient import TestClient


def test_target_models_returns_canonical_seven(client: TestClient):
    r = client.get("/api/prompt/target-models")
    assert r.status_code == 200
    assert r.json() == {
        "target_models": ["sd", "pony", "flux1", "flux2", "zimage", "chroma", "qwen"]
    }
```

- [ ] **Step 2: Run test to verify failure**

```bash
pytest tests/test_prompt_api_target_models.py -v
```

Expected: 404.

- [ ] **Step 3: Add the route to `backend/api/prompt.py`**

Append at the end of the file:

```python
# ---- TargetModel enum --------------------------------------------------------


class TargetModelsResponse(BaseModel):
    target_models: List[str]


# The seven values of metascan.core.prompt_templates.TargetModel literal.
# Kept in lockstep with that module — if the enum grows, update here too.
_TARGET_MODELS_LIST: Final[List[str]] = [
    "sd", "pony", "flux1", "flux2", "zimage", "chroma", "qwen"
]


@router.get("/target-models", response_model=TargetModelsResponse)
async def list_target_models() -> TargetModelsResponse:
    """Return the canonical TargetModel literal values used by saved_prompts.

    Clients use this to populate UI dropdowns; the metscan-nodes load
    node appends a virtual 'any' option client-side that maps to
    target_model=null in search requests.
    """
    return TargetModelsResponse(target_models=list(_TARGET_MODELS_LIST))
```

Add to the imports at the top of the file if `Final` is not already imported:

```python
from typing import Final
```

- [ ] **Step 4: Run test to verify pass**

```bash
pytest tests/test_prompt_api_target_models.py -v
```

Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/api/prompt.py tests/test_prompt_api_target_models.py
git commit -m "$(cat <<'EOF'
Add GET /api/prompt/target-models

Returns the canonical TargetModel literal values. The list is
duplicated here rather than introspected from the Literal so the
endpoint contract doesn't silently change when prompt_templates.py
grows new targets — that should be a deliberate, reviewable edit.

Used by metscan-nodes' MetascanLoadPrompt ComfyUI custom node.
EOF
)"
```

---

## Task 12: nodes/settings.py — MetascanSettings sentinel node (metscan-nodes repo)

A no-op ComfyUI node whose presence in the graph overrides env/file config. It returns a "configured client" handle (a small dict carrying the resolved URL/key) that downstream nodes can accept on a hidden `config` input. For MVP we keep it simple: the node has an `OUTPUT_NODE = True` shape with no real output — its side effect is mutating a module-level "current override" before the workflow runs.

Actually simpler — and per spec §5.4: settings are read by `INPUT_TYPES()` and execute via `resolve_config`. The node exposes URL + API key as STRING widgets and stores them in a module-level variable consulted by `resolve_config(settings_override=...)`. The execute step is a no-op pass-through that returns an empty tuple.

**Files:**
- Create: `nodes/settings.py`
- Test: `tests/test_settings_node.py`

- [ ] **Step 1: Write failing tests**

`tests/test_settings_node.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify failure**

```bash
pytest tests/test_settings_node.py -v
```

Expected: ImportError for `nodes.settings`.

- [ ] **Step 3: Write implementation**

`nodes/settings.py`:

```python
"""MetascanSettings sentinel node.

Drop one of these into a ComfyUI workflow to override the URL and/or
API key for that workflow run. The node's apply() method stores the
values in a module-level _OVERRIDE; downstream nodes call
``get_current_override()`` and pass the result to ``resolve_config``,
which gives the override top priority over env vars and the config
file (see client/config.py for the resolution chain).

Empty-string inputs are treated as "no override" so a node with blank
fields doesn't blank out lower-priority sources. Module-level state is
a deliberate trade-off: ComfyUI executes nodes within one process per
workflow run; the alternative (threading values through hidden inputs
on every consumer node) makes the rest of the wiring much noisier.
"""

from __future__ import annotations

from typing import Optional

from client.config import ClientConfig

_OVERRIDE: Optional[ClientConfig] = None


def get_current_override() -> Optional[ClientConfig]:
    return _OVERRIDE


class MetascanSettings:
    CATEGORY = "metascan"
    OUTPUT_NODE = True
    RETURN_TYPES: tuple = ()
    FUNCTION = "apply"

    @classmethod
    def INPUT_TYPES(cls) -> dict:
        return {
            "required": {
                "url": ("STRING", {"default": "http://localhost:8700"}),
                "api_key": ("STRING", {"default": ""}),
            }
        }

    def apply(self, url: str, api_key: str) -> tuple:
        global _OVERRIDE
        if not url and not api_key:
            _OVERRIDE = None
        else:
            _OVERRIDE = ClientConfig(url=url or "", api_key=api_key or None)
        return ()
```

- [ ] **Step 4: Run tests to verify pass**

```bash
pytest tests/test_settings_node.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add nodes/settings.py tests/test_settings_node.py
git commit -m "$(cat <<'EOF'
Add MetascanSettings sentinel node

Stores URL + API key in module-level state that resolve_config() reads
as its highest-priority source. Empty-string inputs clear the override
so blank fields don't silently override lower-priority env/file values.
OUTPUT_NODE=True with empty RETURN_TYPES so ComfyUI executes it but no
downstream node has to wire anything through.
EOF
)"
```

---

## Task 13: nodes/save_image.py — pure save logic (metscan-nodes repo)

Split the node into pure helpers (testable without ComfyUI / torch) and a thin ComfyUI integration layer (Task 14).

**Files:**
- Create: `nodes/save_image.py` (only the helpers in this task)
- Test: `tests/test_save_image.py` (helper tests in this task)

- [ ] **Step 1: Write failing tests**

`tests/test_save_image.py`:

```python
from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
import torch
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from nodes.save_image import (
    resolve_target_dir,
    tensor_to_pil,
    build_png_info,
)


# ----- resolve_target_dir -----

def test_resolve_target_dir_joins_paths(tmp_path):
    out = resolve_target_dir(directory=str(tmp_path), subpath="comfy/out", now=dt.datetime(2026, 5, 18))
    assert out == tmp_path / "comfy" / "out"


def test_resolve_target_dir_expands_strftime(tmp_path):
    now = dt.datetime(2026, 5, 18, 13, 0)
    out = resolve_target_dir(directory=str(tmp_path), subpath="%Y-%m/comfyui", now=now)
    assert out == tmp_path / "2026-05" / "comfyui"


def test_resolve_target_dir_creates_dirs(tmp_path):
    target = resolve_target_dir(directory=str(tmp_path), subpath="a/b/c", now=dt.datetime(2026, 5, 18))
    assert target.exists()
    assert target.is_dir()


def test_resolve_target_dir_empty_subpath_returns_directory(tmp_path):
    out = resolve_target_dir(directory=str(tmp_path), subpath="", now=dt.datetime(2026, 5, 18))
    assert out == tmp_path


# ----- tensor_to_pil -----

def test_tensor_to_pil_converts_nhwc_float_to_pil_image():
    t = torch.zeros((1, 32, 48, 3), dtype=torch.float32)
    t[0, 0, 0, 0] = 1.0  # one red pixel
    img = tensor_to_pil(t[0])
    assert isinstance(img, Image.Image)
    assert img.size == (48, 32)  # PIL is (W, H)
    assert img.getpixel((0, 0))[0] == 255  # red channel saturated


def test_tensor_to_pil_clamps_out_of_range_values():
    t = torch.full((16, 16, 3), -5.0, dtype=torch.float32)
    img = tensor_to_pil(t)
    assert img.getpixel((0, 0)) == (0, 0, 0)
    t2 = torch.full((16, 16, 3), 5.0, dtype=torch.float32)
    img2 = tensor_to_pil(t2)
    assert img2.getpixel((0, 0)) == (255, 255, 255)


# ----- build_png_info -----

def test_build_png_info_embeds_prompt_and_workflow():
    info = build_png_info(prompt={"a": 1}, workflow={"nodes": [{"id": 1}]})
    assert isinstance(info, PngInfo)
    # PngInfo doesn't expose its dict directly; serialize and re-read.
    img = Image.new("RGB", (4, 4))
    p = Path("/tmp/__pnginfo_test.png")
    img.save(p, pnginfo=info)
    reloaded = Image.open(p)
    reloaded.load()
    assert '"a": 1' in reloaded.info.get("prompt", "")
    assert '"nodes"' in reloaded.info.get("workflow", "")
    p.unlink()


def test_build_png_info_skips_workflow_when_disabled():
    info = build_png_info(prompt={"a": 1}, workflow=None)
    img = Image.new("RGB", (4, 4))
    p = Path("/tmp/__pnginfo_test2.png")
    img.save(p, pnginfo=info)
    reloaded = Image.open(p)
    reloaded.load()
    assert "workflow" not in reloaded.info
    assert "prompt" in reloaded.info
    p.unlink()
```

- [ ] **Step 2: Run tests to verify failure**

```bash
pytest tests/test_save_image.py -v
```

Expected: ImportError on `nodes.save_image`.

- [ ] **Step 3: Write the helpers**

`nodes/save_image.py`:

```python
"""MetascanSaveImage — writes PNG batches into a metascan-watched dir.

This module is split into two layers. The pure helpers
(``resolve_target_dir``, ``tensor_to_pil``, ``build_png_info``) handle
filesystem-path + PIL plumbing and are fully testable with synthesized
torch tensors. The ``MetascanSaveImage`` class (added in Task 14) wires
the helpers into ComfyUI's node interface.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Optional

import numpy as np
import torch
from PIL import Image
from PIL.PngImagePlugin import PngInfo


def resolve_target_dir(directory: str, subpath: str, now: dt.datetime) -> Path:
    """Return ``Path(directory) / strftime(subpath, now)``, creating the
    directory tree if it doesn't exist. ``subpath`` may include strftime
    placeholders like ``%Y-%m``; an empty subpath returns ``directory``
    unchanged. Always uses POSIX-style joining via pathlib so Windows
    paths work transparently."""
    base = Path(directory)
    if subpath:
        expanded = now.strftime(subpath)
        target = base / expanded
    else:
        target = base
    target.mkdir(parents=True, exist_ok=True)
    return target


def tensor_to_pil(image: torch.Tensor) -> Image.Image:
    """Convert a single H×W×3 float tensor (ComfyUI's IMAGE convention,
    range [0, 1]) to an 8-bit RGB PIL image. Out-of-range values clamp
    silently — upstream nodes occasionally produce slightly negative or
    slightly >1 values from sampler noise and we don't want to fail
    the save over a rounding artifact."""
    arr = image.detach().cpu().numpy()
    arr = np.clip(arr * 255.0, 0.0, 255.0).astype(np.uint8)
    return Image.fromarray(arr, mode="RGB")


def build_png_info(prompt: Optional[dict], workflow: Optional[dict]) -> PngInfo:
    """Build a ``PngInfo`` carrying ComfyUI's ``prompt`` and (optionally)
    ``workflow`` tEXt chunks. The format matches what ComfyUI's core
    SaveImage writes, which is what metascan's enhanced_comfyui extractor
    expects when it scans the directory later (see
    metascan/metascan/extractors/enhanced_comfyui.py)."""
    info = PngInfo()
    if prompt is not None:
        info.add_text("prompt", json.dumps(prompt))
    if workflow is not None:
        info.add_text("workflow", json.dumps(workflow))
    return info
```

Also add `numpy` to the runtime deps in `pyproject.toml`:

```toml
dependencies = [
    "httpx>=0.27",
    "pillow>=10",
    "numpy>=1.24",
]
```

- [ ] **Step 4: Run tests to verify pass**

```bash
pip install -e ".[test]"   # picks up the new numpy dep
pytest tests/test_save_image.py -v
```

Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add nodes/save_image.py tests/test_save_image.py pyproject.toml
git commit -m "$(cat <<'EOF'
Add SaveImage pure helpers: dir resolve, tensor→PIL, PngInfo build

Split from the ComfyUI node integration (Task 14) so they're testable
with plain torch tensors and no ComfyUI runtime. resolve_target_dir
expands strftime placeholders in the subpath; tensor_to_pil clamps to
[0,1] so upstream sampler noise doesn't fail the save; build_png_info
matches the prompt+workflow tEXt chunk format ComfyUI's core SaveImage
writes (and metascan's extractor reads).

Add numpy to the runtime deps for the float→uint8 conversion.
EOF
)"
```

---

## Task 14: nodes/save_image.py — MetascanSaveImage ComfyUI class (metscan-nodes repo)

**Files:**
- Modify: `nodes/save_image.py` (add class)
- Modify: `tests/test_save_image.py` (add class tests)

- [ ] **Step 1: Write failing tests**

Append to `tests/test_save_image.py`:

```python
import datetime as dt
import shutil
import tempfile
from unittest.mock import patch

import pytest
import respx
import httpx

from nodes.save_image import MetascanSaveImage
from client.cache import clear_cache, OFFLINE_SENTINEL


# ----- INPUT_TYPES dropdown -----

@respx.mock
def test_input_types_lists_directories_from_metascan(monkeypatch, base_url, config_payload):
    """MetascanSaveImage.INPUT_TYPES() hits combo_directories() which
    hits the real client which respx mocks here."""
    clear_cache()
    respx.get(f"{base_url}/api/config").mock(return_value=httpx.Response(200, json=config_payload))
    monkeypatch.setenv("METASCAN_URL", base_url)
    monkeypatch.delenv("METASCAN_API_KEY", raising=False)
    # Settings node override must be cleared between tests.
    import nodes.settings
    nodes.settings._OVERRIDE = None

    spec = MetascanSaveImage.INPUT_TYPES()
    dirs = spec["required"]["directory"][0]
    assert "/data/comfy-out" in dirs
    assert "/data/photos" in dirs


@respx.mock
def test_input_types_shows_offline_sentinel_when_server_down(monkeypatch, base_url):
    clear_cache()
    respx.get(f"{base_url}/api/config").mock(side_effect=httpx.ConnectError("x"))
    monkeypatch.setenv("METASCAN_URL", base_url)
    monkeypatch.delenv("METASCAN_API_KEY", raising=False)
    import nodes.settings
    nodes.settings._OVERRIDE = None

    spec = MetascanSaveImage.INPUT_TYPES()
    dirs = spec["required"]["directory"][0]
    assert dirs == [OFFLINE_SENTINEL]


# ----- execute: file write + pass-through -----

def test_execute_writes_png_and_passes_through_tensor(tmp_path):
    images = torch.zeros((2, 16, 16, 3), dtype=torch.float32)
    node = MetascanSaveImage()
    out_images, out_path = node.save(
        images=images,
        directory=str(tmp_path),
        subpath="",
        filename_prefix="ComfyUI",
        embed_workflow=True,
        prompt=None,
        extra_pnginfo=None,
    )
    assert out_images is images  # identity pass-through
    p = Path(out_path)
    assert p.exists() and p.suffix == ".png"
    # Both batch entries should land on disk.
    written = sorted(p.parent.glob("ComfyUI_*.png"))
    assert len(written) == 2


def test_execute_strftime_subpath_expanded(tmp_path):
    images = torch.zeros((1, 8, 8, 3), dtype=torch.float32)
    with patch("nodes.save_image._utc_now") as mock_now:
        mock_now.return_value = dt.datetime(2026, 5, 18)
        MetascanSaveImage().save(
            images=images, directory=str(tmp_path), subpath="%Y-%m/comfy",
            filename_prefix="X", embed_workflow=False, prompt=None, extra_pnginfo=None,
        )
    assert (tmp_path / "2026-05" / "comfy").exists()
    assert list((tmp_path / "2026-05" / "comfy").glob("X_*.png"))


def test_execute_raises_on_offline_sentinel():
    images = torch.zeros((1, 8, 8, 3), dtype=torch.float32)
    with pytest.raises(RuntimeError, match="offline"):
        MetascanSaveImage().save(
            images=images, directory=OFFLINE_SENTINEL, subpath="",
            filename_prefix="X", embed_workflow=False, prompt=None, extra_pnginfo=None,
        )


def test_execute_skips_workflow_chunk_when_embed_false(tmp_path):
    images = torch.zeros((1, 8, 8, 3), dtype=torch.float32)
    MetascanSaveImage().save(
        images=images, directory=str(tmp_path), subpath="",
        filename_prefix="ComfyUI", embed_workflow=False,
        prompt={"x": 1}, extra_pnginfo={"workflow": {"nodes": []}},
    )
    p = next(tmp_path.glob("ComfyUI_*.png"))
    img = Image.open(p)
    img.load()
    assert "prompt" in img.info
    assert "workflow" not in img.info
```

- [ ] **Step 2: Run tests to verify failure**

```bash
pytest tests/test_save_image.py -v -k "input_types or execute"
```

Expected: AttributeError on `MetascanSaveImage`.

- [ ] **Step 3: Add the class to `nodes/save_image.py`**

Append to `nodes/save_image.py`:

```python
# --- ComfyUI node integration --------------------------------------------

from client.api import MetascanClient
from client.cache import combo_directories, OFFLINE_SENTINEL
from client.config import resolve_config
from nodes.settings import get_current_override


def _utc_now() -> dt.datetime:
    """Indirection so tests can patch the clock for strftime checks."""
    return dt.datetime.now()


def _build_client() -> MetascanClient:
    cfg = resolve_config(settings_override=get_current_override())
    return MetascanClient(config=cfg, timeout=5.0)


class MetascanSaveImage:
    """Save a batch of images into a metascan-watched directory.

    The node does NOT call metascan's API at execute time — the
    filesystem watcher (or next scan) picks the file up automatically.
    The only HTTP call happens at INPUT_TYPES() to populate the
    directory dropdown.
    """

    CATEGORY = "metascan"
    OUTPUT_NODE = True
    RETURN_TYPES = ("IMAGE", "STRING")
    RETURN_NAMES = ("images", "file_path")
    FUNCTION = "save"

    @classmethod
    def INPUT_TYPES(cls) -> dict:
        try:
            dirs = combo_directories(_build_client())
        except Exception:  # noqa: BLE001 — be defensive at editor-load time
            dirs = [OFFLINE_SENTINEL]
        return {
            "required": {
                "images": ("IMAGE",),
                "directory": (dirs,),
                "subpath": ("STRING", {"default": ""}),
                "filename_prefix": ("STRING", {"default": "ComfyUI"}),
                "embed_workflow": ("BOOLEAN", {"default": True}),
            },
            "hidden": {
                "prompt": "PROMPT",
                "extra_pnginfo": "EXTRA_PNGINFO",
            },
        }

    def save(
        self,
        images: "torch.Tensor",
        directory: str,
        subpath: str,
        filename_prefix: str,
        embed_workflow: bool,
        prompt: Optional[dict] = None,
        extra_pnginfo: Optional[dict] = None,
    ) -> tuple:
        if directory == OFFLINE_SENTINEL:
            raise RuntimeError(
                "Metascan is offline — cannot resolve a watched directory. "
                "Bring metascan up or add a MetascanSettings node with the "
                "correct URL."
            )

        now = _utc_now()
        target_dir = resolve_target_dir(directory=directory, subpath=subpath, now=now)

        # extra_pnginfo is what ComfyUI passes for the workflow blob;
        # canonical key is "workflow" inside the dict.
        workflow_dict: Optional[dict] = None
        if embed_workflow and isinstance(extra_pnginfo, dict):
            workflow_dict = extra_pnginfo.get("workflow")

        info = build_png_info(prompt=prompt, workflow=workflow_dict)

        # Collision-counter filename: ``<prefix>_<NNNNN>.png`` starting
        # from the first unused N. Cheap O(n) probe; metascan rigs don't
        # accumulate millions of files in a single output dir.
        existing = list(target_dir.glob(f"{filename_prefix}_*.png"))
        next_idx = len(existing)

        first_path: Optional[Path] = None
        for i in range(images.shape[0]):
            pil = tensor_to_pil(images[i])
            out_path = target_dir / f"{filename_prefix}_{next_idx + i:05d}.png"
            pil.save(out_path, pnginfo=info)
            if first_path is None:
                first_path = out_path

        return (images, str(first_path) if first_path else "")
```

- [ ] **Step 4: Run tests to verify pass**

```bash
pytest tests/test_save_image.py -v
```

Expected: 12 passed (8 from Task 13 + 4 new).

- [ ] **Step 5: Commit**

```bash
git add nodes/save_image.py tests/test_save_image.py
git commit -m "$(cat <<'EOF'
Add MetascanSaveImage ComfyUI node class

Populates the directory dropdown from metascan's /api/config at
INPUT_TYPES time (cached 60s); execute writes PNGs with embedded
prompt + workflow tEXt chunks and passes the input tensor through.
No HTTP calls at execute time — metascan's filesystem watcher picks
up the new files on its own. Raises with a clear message when the
user runs the node with the offline sentinel still selected.
EOF
)"
```

---

## Task 15: nodes/load_from_folder.py — selection logic helpers (metscan-nodes repo)

**Files:**
- Create: `nodes/load_from_folder.py` (helpers only this task)
- Test: `tests/test_load_from_folder.py`

- [ ] **Step 1: Write failing tests**

`tests/test_load_from_folder.py`:

```python
from __future__ import annotations

import pytest
import torch

from nodes.load_from_folder import (
    filter_paths,
    select_path,
    bytes_to_tensor,
)


SAMPLE_PATHS = [
    "/data/a/img1.png",
    "/data/a/img2.jpg",
    "/data/a/clip.mp4",
    "/data/a/note.txt",
    "/data/a/img3.webp",
]


# ----- filter_paths -----

def test_filter_paths_image_only_drops_video_and_other(tmp_path):
    out = filter_paths(SAMPLE_PATHS, image_only=True, filename_filter="")
    assert out == ["/data/a/img1.png", "/data/a/img2.jpg", "/data/a/img3.webp"]


def test_filter_paths_image_only_false_includes_video(tmp_path):
    out = filter_paths(SAMPLE_PATHS, image_only=False, filename_filter="")
    # .txt still excluded — it's never a supported media type.
    assert "/data/a/clip.mp4" in out
    assert "/data/a/note.txt" not in out


def test_filter_paths_applies_substring_filter():
    out = filter_paths(SAMPLE_PATHS, image_only=True, filename_filter="img2")
    assert out == ["/data/a/img2.jpg"]


def test_filter_paths_sorts_deterministically():
    out = filter_paths(["/z.png", "/a.png", "/m.png"], image_only=True, filename_filter="")
    assert out == ["/a.png", "/m.png", "/z.png"]


# ----- select_path -----

def test_select_path_random_reproducible_with_seed():
    paths = ["/a.png", "/b.png", "/c.png"]
    p1, next_seed1 = select_path(paths, mode="random", seed=42, index=0)
    p2, next_seed2 = select_path(paths, mode="random", seed=42, index=0)
    assert p1 == p2
    assert next_seed1 == next_seed2


def test_select_path_sequential_advances_seed():
    paths = ["/a.png", "/b.png", "/c.png"]
    chosen, next_seed = select_path(paths, mode="sequential", seed=1, index=0)
    assert chosen == "/b.png"
    assert next_seed == 2


def test_select_path_sequential_wraps_around():
    paths = ["/a.png", "/b.png"]
    chosen, next_seed = select_path(paths, mode="sequential", seed=5, index=0)
    assert chosen == "/b.png"  # 5 % 2 == 1
    assert next_seed == 0      # (5 + 1) % 2 == 0


def test_select_path_specific_uses_index_not_seed():
    paths = ["/a.png", "/b.png", "/c.png"]
    chosen, next_seed = select_path(paths, mode="specific", seed=999, index=1)
    assert chosen == "/b.png"
    assert next_seed == 1


def test_select_path_specific_clamps_oversize_index():
    paths = ["/a.png", "/b.png", "/c.png"]
    chosen, _ = select_path(paths, mode="specific", seed=0, index=10)
    assert chosen == "/b.png"  # 10 % 3 == 1


def test_select_path_empty_list_raises():
    with pytest.raises(RuntimeError, match="no matching"):
        select_path([], mode="random", seed=0, index=0)


# ----- bytes_to_tensor -----

def test_bytes_to_tensor_returns_nhwc_float():
    # Build an actual 4×4 PNG and feed its bytes in.
    from io import BytesIO
    from PIL import Image
    pil = Image.new("RGB", (8, 4), color=(128, 64, 192))
    buf = BytesIO()
    pil.save(buf, format="PNG")
    t = bytes_to_tensor(buf.getvalue())
    assert isinstance(t, torch.Tensor)
    assert t.dtype == torch.float32
    assert t.shape == (1, 4, 8, 3)
    assert 0.0 <= t.min() <= t.max() <= 1.0
    # Red channel 128/255 ≈ 0.502.
    assert abs(float(t[0, 0, 0, 0]) - 128/255) < 0.01
```

- [ ] **Step 2: Run tests to verify failure**

```bash
pytest tests/test_load_from_folder.py -v
```

Expected: ImportError on `nodes.load_from_folder`.

- [ ] **Step 3: Write the helpers**

`nodes/load_from_folder.py`:

```python
"""MetascanLoadFromFolder — pick an image from a metascan manual folder.

Module is split into pure helpers + a ComfyUI integration class
(Task 17). Helpers are testable in isolation with no HTTP and no
ComfyUI runtime.
"""

from __future__ import annotations

import io
from pathlib import PurePosixPath
from typing import Literal

import numpy as np
import torch
from PIL import Image

# Conservative whitelist — covers the formats metascan's extractors
# claim support for. Anything else gets dropped silently from the
# filter step so a random pick doesn't land on an unreadable file.
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
_VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi"}


def filter_paths(paths: list[str], image_only: bool, filename_filter: str) -> list[str]:
    """Filter then sort deterministically.

    1. Drop entries whose extension isn't in the supported sets.
    2. If ``image_only=True``, also drop video extensions.
    3. If ``filename_filter`` is non-empty, keep only paths whose
       ``PurePosixPath(p).name`` contains the filter substring.
    4. Sort ascending by path so selection-by-seed is stable across
       runs even when the upstream listing order isn't.
    """
    allowed = _IMAGE_EXTS if image_only else (_IMAGE_EXTS | _VIDEO_EXTS)
    out = [
        p for p in paths
        if PurePosixPath(p).suffix.lower() in allowed
        and (not filename_filter or filename_filter in PurePosixPath(p).name)
    ]
    out.sort()
    return out


SelectionMode = Literal["random", "sequential", "specific"]


def select_path(
    paths: list[str], mode: SelectionMode, seed: int, index: int
) -> tuple[str, int]:
    """Pick one path. Returns (chosen_path, next_seed).

    - ``random`` and ``sequential`` both index by ``seed % len(paths)``;
      ``random`` returns the same seed back, ``sequential`` returns
      ``(seed + 1) % len(paths)`` so chaining advances naturally.
    - ``specific`` indexes by ``index % len(paths)`` and returns
      ``index`` unchanged (next_seed is unused in this mode but kept
      for output-tuple symmetry).
    - Empty path list raises ``RuntimeError`` with a message the load
      node can surface in ComfyUI's UI without further wrapping.
    """
    if not paths:
        raise RuntimeError("no matching items in folder")
    n = len(paths)
    if mode == "specific":
        chosen_idx = index % n
        return paths[chosen_idx], index
    chosen_idx = seed % n
    next_seed = seed if mode == "random" else (seed + 1) % n
    return paths[chosen_idx], next_seed


def bytes_to_tensor(data: bytes) -> torch.Tensor:
    """Decode PNG/JPEG/WebP bytes to ComfyUI's IMAGE convention:
    float32, range [0, 1], shape ``[1, H, W, 3]`` (NHWC). RGBA inputs
    flatten to RGB by dropping the alpha channel — the load node is
    feeding samplers / preview chains that don't carry alpha."""
    pil = Image.open(io.BytesIO(data))
    pil.load()
    if pil.mode != "RGB":
        pil = pil.convert("RGB")
    arr = np.asarray(pil, dtype=np.float32) / 255.0
    tensor = torch.from_numpy(arr).unsqueeze(0)  # add batch dim
    return tensor
```

- [ ] **Step 4: Run tests to verify pass**

```bash
pytest tests/test_load_from_folder.py -v
```

Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
git add nodes/load_from_folder.py tests/test_load_from_folder.py
git commit -m "$(cat <<'EOF'
Add LoadFromFolder pure helpers: filter, select, bytes→tensor

filter_paths whitelists known media extensions and sorts ascending so
seeded selection is stable; select_path implements random/sequential/
specific modes with next_seed advancement for chaining; bytes_to_tensor
produces ComfyUI's [1,H,W,3] float32 [0,1] convention, dropping alpha
channels on RGBA inputs.
EOF
)"
```

---

## Task 16: nodes/load_from_folder.py — MetascanLoadFromFolder class (metscan-nodes repo)

**Files:**
- Modify: `nodes/load_from_folder.py` (add class)
- Modify: `tests/test_load_from_folder.py` (add class tests)

- [ ] **Step 1: Write failing tests**

Append to `tests/test_load_from_folder.py`:

```python
import respx
import httpx
from io import BytesIO
from unittest.mock import patch
from PIL import Image

from nodes.load_from_folder import MetascanLoadFromFolder
from client.cache import clear_cache, OFFLINE_SENTINEL


def _png_bytes(color=(0, 255, 0), size=(8, 8)):
    buf = BytesIO()
    Image.new("RGB", size, color=color).save(buf, format="PNG")
    return buf.getvalue()


# ----- INPUT_TYPES -----

@respx.mock
def test_input_types_lists_manual_folder_names(monkeypatch, base_url, folders_payload):
    clear_cache()
    respx.get(f"{base_url}/api/folders").mock(return_value=httpx.Response(200, json=folders_payload))
    monkeypatch.setenv("METASCAN_URL", base_url)
    monkeypatch.delenv("METASCAN_API_KEY", raising=False)
    import nodes.settings
    nodes.settings._OVERRIDE = None

    spec = MetascanLoadFromFolder.INPUT_TYPES()
    folder_list = spec["required"]["folder"][0]
    assert folder_list == ["Portraits", "Landscapes"]


# ----- execute -----

@respx.mock
def test_execute_loads_image_and_metadata(monkeypatch, base_url, folders_payload):
    clear_cache()
    respx.get(f"{base_url}/api/folders").mock(return_value=httpx.Response(200, json=folders_payload))
    # Portraits folder detail (id fld_a, items list).
    respx.get(f"{base_url}/api/folders/fld_a").mock(
        return_value=httpx.Response(200, json=folders_payload[0])
    )
    # Pick will land on img1.png (sorted-first). Stub its media + bytes.
    respx.get(f"{base_url}/api/media/%2Fdata%2Fa%2Fimg1.png").mock(
        return_value=httpx.Response(200, json={
            "file_path": "/data/a/img1.png",
            "data": {"prompt": "POSITIVE", "negative_prompt": "NEGATIVE"},
        })
    )
    respx.get(f"{base_url}/api/stream/%2Fdata%2Fa%2Fimg1.png").mock(
        return_value=httpx.Response(200, content=_png_bytes())
    )
    monkeypatch.setenv("METASCAN_URL", base_url)
    monkeypatch.delenv("METASCAN_API_KEY", raising=False)
    import nodes.settings
    nodes.settings._OVERRIDE = None

    node = MetascanLoadFromFolder()
    image, path, positive, negative, next_seed = node.load(
        folder="Portraits",
        selection_mode="sequential",
        seed=0,
        index=0,
        filename_filter="",
        image_only=True,
    )
    assert image.shape == (1, 8, 8, 3)
    assert path == "/data/a/img1.png"
    assert positive == "POSITIVE"
    assert negative == "NEGATIVE"
    assert next_seed == 1


@respx.mock
def test_execute_raises_on_empty_folder(monkeypatch, base_url, folders_payload):
    clear_cache()
    empty_folder = {**folders_payload[1]}   # Landscapes — already empty
    respx.get(f"{base_url}/api/folders").mock(return_value=httpx.Response(200, json=folders_payload))
    respx.get(f"{base_url}/api/folders/fld_b").mock(return_value=httpx.Response(200, json=empty_folder))
    monkeypatch.setenv("METASCAN_URL", base_url)
    monkeypatch.delenv("METASCAN_API_KEY", raising=False)
    import nodes.settings
    nodes.settings._OVERRIDE = None

    with pytest.raises(RuntimeError, match="no matching"):
        MetascanLoadFromFolder().load(
            folder="Landscapes", selection_mode="random", seed=0, index=0,
            filename_filter="", image_only=True,
        )


def test_execute_raises_on_offline_sentinel():
    with pytest.raises(RuntimeError, match="offline"):
        MetascanLoadFromFolder().load(
            folder=OFFLINE_SENTINEL, selection_mode="random", seed=0, index=0,
            filename_filter="", image_only=True,
        )
```

- [ ] **Step 2: Run tests to verify failure**

```bash
pytest tests/test_load_from_folder.py -v -k "input_types or execute"
```

Expected: AttributeError on `MetascanLoadFromFolder`.

- [ ] **Step 3: Add the class to `nodes/load_from_folder.py`**

Append to `nodes/load_from_folder.py`:

```python
# --- ComfyUI node integration --------------------------------------------

from client.api import MetascanClient
from client.cache import combo_folders, OFFLINE_SENTINEL
from client.config import resolve_config
from nodes.settings import get_current_override


def _build_client() -> MetascanClient:
    cfg = resolve_config(settings_override=get_current_override())
    return MetascanClient(config=cfg, timeout=10.0)


def _folder_id_for_name(client: MetascanClient, name: str) -> str:
    """Resolve a human folder name to its (string) folder ID.

    The dropdown shows names; the API takes IDs. We re-fetch the folder
    list here rather than relying on the cached name list because the
    cached version doesn't carry IDs."""
    for folder in client.list_folders():
        if folder["name"] == name:
            return folder["id"]
    raise RuntimeError(f"folder not found in metascan: {name!r}")


class MetascanLoadFromFolder:
    CATEGORY = "metascan"
    RETURN_TYPES = ("IMAGE", "STRING", "STRING", "STRING", "INT")
    RETURN_NAMES = ("image", "file_path", "positive", "negative", "next_seed")
    FUNCTION = "load"

    @classmethod
    def INPUT_TYPES(cls) -> dict:
        try:
            folders = combo_folders(_build_client())
        except Exception:  # noqa: BLE001
            folders = [OFFLINE_SENTINEL]
        return {
            "required": {
                "folder": (folders,),
                "selection_mode": (["random", "sequential", "specific"],),
                "seed": ("INT", {"default": 0, "min": 0, "max": 2**31 - 1}),
                "index": ("INT", {"default": 0, "min": 0, "max": 2**31 - 1}),
                "filename_filter": ("STRING", {"default": ""}),
                "image_only": ("BOOLEAN", {"default": True}),
            },
        }

    def load(
        self,
        folder: str,
        selection_mode: SelectionMode,
        seed: int,
        index: int,
        filename_filter: str,
        image_only: bool,
    ) -> tuple:
        if folder == OFFLINE_SENTINEL:
            raise RuntimeError(
                "Metascan is offline — cannot list folders. Bring metascan "
                "up or correct the MetascanSettings URL."
            )

        client = _build_client()
        folder_id = _folder_id_for_name(client, folder)
        folder_detail = client.get_folder(folder_id)
        items = folder_detail.get("items", []) or []

        filtered = filter_paths(items, image_only=image_only, filename_filter=filename_filter)
        chosen, next_seed = select_path(filtered, mode=selection_mode, seed=seed, index=index)

        media = client.get_media_detail(chosen)
        data = media.get("data") or {}
        positive = data.get("prompt", "") or ""
        negative = data.get("negative_prompt", "") or ""

        raw = client.stream_bytes(chosen)
        tensor = bytes_to_tensor(raw)

        return (tensor, chosen, positive, negative, next_seed)
```

- [ ] **Step 4: Run tests to verify pass**

```bash
pytest tests/test_load_from_folder.py -v
```

Expected: 13 passed (10 from Task 15 + 3 new).

- [ ] **Step 5: Commit**

```bash
git add nodes/load_from_folder.py tests/test_load_from_folder.py
git commit -m "$(cat <<'EOF'
Add MetascanLoadFromFolder ComfyUI node class

Folder dropdown populated from /api/folders (manual only); execute
resolves name → ID, fetches the resolved items list, filters by ext +
optional filename substring, picks one path by seed/index, then pulls
the media detail (for prompt metadata) and stream bytes (for the
tensor). Returns positive + negative prompts and an advanced next_seed
for sequential chaining.
EOF
)"
```

---

## Task 17: nodes/load_prompt.py — full module (metscan-nodes repo)

Smaller than the previous nodes — the data flow is just `search_prompts → select → output strings`.

**Files:**
- Create: `nodes/load_prompt.py`
- Test: `tests/test_load_prompt.py`

- [ ] **Step 1: Write failing tests**

`tests/test_load_prompt.py`:

```python
from __future__ import annotations

import pytest
import respx
import httpx

from client.cache import clear_cache, OFFLINE_SENTINEL
from nodes.load_prompt import MetascanLoadPrompt, select_prompt


SAMPLE_ROWS = [
    {"id": 1, "file_path": "/a.png", "name": "hero",      "prompt": "p1",
     "negative": None, "target_model": "qwen", "architecture": "qwen", "styles": []},
    {"id": 2, "file_path": "/b.png", "name": "cinematic", "prompt": "p2",
     "negative": "n2", "target_model": "qwen", "architecture": "qwen", "styles": []},
    {"id": 3, "file_path": "/c.png", "name": "landscape", "prompt": "p3",
     "negative": "",   "target_model": "qwen", "architecture": "qwen", "styles": []},
]


# ----- select_prompt -----

def test_select_prompt_by_name_returns_matching_row():
    row = select_prompt(SAMPLE_ROWS, mode="by_name", name="cinematic", seed=0)
    assert row["id"] == 2


def test_select_prompt_by_name_missing_raises():
    with pytest.raises(RuntimeError, match="no saved prompt"):
        select_prompt(SAMPLE_ROWS, mode="by_name", name="missing", seed=0)


def test_select_prompt_random_reproducible_with_seed():
    r1 = select_prompt(SAMPLE_ROWS, mode="random", name="", seed=99)
    r2 = select_prompt(SAMPLE_ROWS, mode="random", name="", seed=99)
    assert r1["id"] == r2["id"]


def test_select_prompt_empty_rows_raises():
    with pytest.raises(RuntimeError, match="no saved prompts"):
        select_prompt([], mode="random", name="", seed=0)


# ----- ComfyUI class -----

@respx.mock
def test_input_types_lists_folders_and_target_models(monkeypatch, base_url, folders_payload):
    clear_cache()
    respx.get(f"{base_url}/api/folders").mock(return_value=httpx.Response(200, json=folders_payload))
    respx.get(f"{base_url}/api/prompt/target-models").mock(return_value=httpx.Response(200, json={
        "target_models": ["sd", "pony", "flux1", "flux2", "zimage", "chroma", "qwen"]
    }))
    monkeypatch.setenv("METASCAN_URL", base_url)
    monkeypatch.delenv("METASCAN_API_KEY", raising=False)
    import nodes.settings
    nodes.settings._OVERRIDE = None

    spec = MetascanLoadPrompt.INPUT_TYPES()
    folders = spec["required"]["folder"][0]
    target_models = spec["required"]["target_model"][0]
    assert folders == ["Portraits", "Landscapes"]
    assert target_models[-1] == "any"
    assert "qwen" in target_models


@respx.mock
def test_execute_maps_any_to_null_target_model(monkeypatch, base_url, folders_payload):
    clear_cache()
    respx.get(f"{base_url}/api/folders").mock(return_value=httpx.Response(200, json=folders_payload))
    route = respx.post(f"{base_url}/api/prompt/search").mock(
        return_value=httpx.Response(200, json={"prompts": SAMPLE_ROWS})
    )
    monkeypatch.setenv("METASCAN_URL", base_url)
    monkeypatch.delenv("METASCAN_API_KEY", raising=False)
    import nodes.settings
    nodes.settings._OVERRIDE = None

    pos, neg, name, src = MetascanLoadPrompt().load(
        folder="Portraits",
        target_model="any",
        selection_mode="by_name",
        prompt_name="cinematic",
        seed=0,
    )
    assert pos == "p2"
    assert neg == "n2"
    assert name == "cinematic"
    assert src == "/b.png"
    import json as _j
    body = _j.loads(route.calls.last.request.read().decode())
    assert body["target_model"] is None      # "any" → null
    assert body["folder_id"] == "fld_a"      # Portraits → fld_a
    assert body["name"] == "cinematic"


@respx.mock
def test_execute_null_negative_becomes_empty_string(monkeypatch, base_url, folders_payload):
    clear_cache()
    respx.get(f"{base_url}/api/folders").mock(return_value=httpx.Response(200, json=folders_payload))
    respx.post(f"{base_url}/api/prompt/search").mock(
        return_value=httpx.Response(200, json={"prompts": [SAMPLE_ROWS[0]]})  # negative=None
    )
    monkeypatch.setenv("METASCAN_URL", base_url)
    monkeypatch.delenv("METASCAN_API_KEY", raising=False)
    import nodes.settings
    nodes.settings._OVERRIDE = None

    pos, neg, name, src = MetascanLoadPrompt().load(
        folder="Portraits", target_model="qwen", selection_mode="by_name",
        prompt_name="hero", seed=0,
    )
    assert neg == ""


def test_execute_raises_on_offline_sentinel():
    with pytest.raises(RuntimeError, match="offline"):
        MetascanLoadPrompt().load(
            folder=OFFLINE_SENTINEL, target_model="qwen", selection_mode="random",
            prompt_name="", seed=0,
        )
```

- [ ] **Step 2: Run tests to verify failure**

```bash
pytest tests/test_load_prompt.py -v
```

Expected: ImportError on `nodes.load_prompt`.

- [ ] **Step 3: Write the implementation**

`nodes/load_prompt.py`:

```python
"""MetascanLoadPrompt — load a saved prompt from metascan's prompt library.

Pure ``select_prompt`` helper + ComfyUI integration class. No image or
tensor work — this node returns four strings (positive, negative, the
chosen prompt's name, and the source media path it was saved against).
"""

from __future__ import annotations

from typing import Literal, Optional

from client.api import MetascanClient
from client.cache import (
    combo_folders,
    combo_target_models,
    OFFLINE_SENTINEL,
)
from client.config import resolve_config
from nodes.settings import get_current_override


SelectionMode = Literal["random", "by_name"]


def select_prompt(
    rows: list[dict], mode: SelectionMode, name: str, seed: int
) -> dict:
    """Pick one row from a search result.

    - ``by_name``: return the row where ``row["name"] == name``. If no
      row matches, raise ``RuntimeError`` with a message the node
      surfaces directly.
    - ``random``: return ``rows[seed % len(rows)]`` (reproducible by
      seed so workflow re-runs yield the same prompt).
    - Empty ``rows`` raises ``RuntimeError`` regardless of mode.
    """
    if not rows:
        raise RuntimeError("no saved prompts match the folder + target_model filter")
    if mode == "by_name":
        for r in rows:
            if r.get("name") == name:
                return r
        raise RuntimeError(f"no saved prompt named {name!r} in the filtered set")
    return rows[seed % len(rows)]


# ---------------------------------------------------------------------------
# ComfyUI node
# ---------------------------------------------------------------------------


def _build_client() -> MetascanClient:
    cfg = resolve_config(settings_override=get_current_override())
    return MetascanClient(config=cfg, timeout=10.0)


def _folder_id_for_name(client: MetascanClient, name: str) -> str:
    for folder in client.list_folders():
        if folder["name"] == name:
            return folder["id"]
    raise RuntimeError(f"folder not found in metascan: {name!r}")


class MetascanLoadPrompt:
    CATEGORY = "metascan"
    RETURN_TYPES = ("STRING", "STRING", "STRING", "STRING")
    RETURN_NAMES = ("positive", "negative", "name", "source_file_path")
    FUNCTION = "load"

    @classmethod
    def INPUT_TYPES(cls) -> dict:
        try:
            client = _build_client()
            folders = combo_folders(client)
            target_models = combo_target_models(client)
        except Exception:  # noqa: BLE001
            folders = [OFFLINE_SENTINEL]
            target_models = [OFFLINE_SENTINEL]
        return {
            "required": {
                "folder": (folders,),
                "target_model": (target_models,),
                "selection_mode": (["random", "by_name"],),
                "prompt_name": ("STRING", {"default": ""}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 2**31 - 1}),
            },
        }

    def load(
        self,
        folder: str,
        target_model: str,
        selection_mode: SelectionMode,
        prompt_name: str,
        seed: int,
    ) -> tuple:
        if folder == OFFLINE_SENTINEL or target_model == OFFLINE_SENTINEL:
            raise RuntimeError(
                "Metascan is offline — bring it up or correct MetascanSettings."
            )

        client = _build_client()
        folder_id = _folder_id_for_name(client, folder)
        # "any" is a virtual UI option — map to null filter.
        wire_target: Optional[str] = None if target_model == "any" else target_model

        rows = client.search_prompts(
            folder_id=folder_id,
            target_model=wire_target,
            name=prompt_name if selection_mode == "by_name" and prompt_name else None,
            limit=500,
        )
        chosen = select_prompt(rows, mode=selection_mode, name=prompt_name, seed=seed)

        return (
            chosen.get("prompt", "") or "",
            chosen.get("negative") or "",   # SQL NULL → ""
            chosen.get("name", "") or "",
            chosen.get("file_path", "") or "",
        )
```

- [ ] **Step 4: Run tests to verify pass**

```bash
pytest tests/test_load_prompt.py -v
```

Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add nodes/load_prompt.py tests/test_load_prompt.py
git commit -m "$(cat <<'EOF'
Add MetascanLoadPrompt ComfyUI node class

Calls the new POST /api/prompt/search; selects a row by name or by
seeded random; returns positive/negative/name/source_file_path as
four STRING outputs. The 'any' virtual target_model maps to null in
the search request body. NULL negative columns surface as "" so the
output is wire-safe for downstream CLIPTextEncode without an extra
null-check step.
EOF
)"
```

---

## Task 18: __init__.py — export NODE_CLASS_MAPPINGS (metscan-nodes repo)

**Files:**
- Modify: `__init__.py`
- No tests — this is the ComfyUI loader contract; we verify by import alone.

- [ ] **Step 1: Replace `__init__.py` body**

```python
"""ComfyUI custom_nodes entry point for metscan-nodes.

ComfyUI imports this package on startup and looks for the two
mapping dicts below to register the nodes in its menu.
"""

from nodes.settings import MetascanSettings
from nodes.save_image import MetascanSaveImage
from nodes.load_from_folder import MetascanLoadFromFolder
from nodes.load_prompt import MetascanLoadPrompt

NODE_CLASS_MAPPINGS = {
    "MetascanSettings": MetascanSettings,
    "MetascanSaveImage": MetascanSaveImage,
    "MetascanLoadFromFolder": MetascanLoadFromFolder,
    "MetascanLoadPrompt": MetascanLoadPrompt,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "MetascanSettings": "Metascan · Settings",
    "MetascanSaveImage": "Metascan · Save Image",
    "MetascanLoadFromFolder": "Metascan · Load From Folder",
    "MetascanLoadPrompt": "Metascan · Load Prompt",
}

WEB_DIRECTORY = None

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
```

- [ ] **Step 2: Smoke test: package imports cleanly + all four classes present**

```bash
python -c "
import importlib, sys
sys.path.insert(0, '.')
m = importlib.import_module('__init__')
keys = sorted(m.NODE_CLASS_MAPPINGS.keys())
assert keys == ['MetascanLoadFromFolder', 'MetascanLoadPrompt', 'MetascanSaveImage', 'MetascanSettings'], keys
print('ok')
"
```

Expected: `ok` on stdout.

- [ ] **Step 3: Run the whole test suite**

```bash
pytest tests/ -v
```

Expected: All tests pass. Total around 50+ tests across all the prior tasks.

- [ ] **Step 4: Commit**

```bash
git add __init__.py
git commit -m "$(cat <<'EOF'
Wire up NODE_CLASS_MAPPINGS for ComfyUI loader

Four nodes registered under the 'metascan' category:
MetascanSettings, MetascanSaveImage, MetascanLoadFromFolder,
MetascanLoadPrompt. Display names use the middle-dot separator
('Metascan · …') so they group naturally in ComfyUI's node menu.
EOF
)"
```

---

## Task 19: CI workflow (metscan-nodes repo)

**Files:**
- Create: `.github/workflows/ci.yml`

- [ ] **Step 1: Create the workflow file**

```yaml
name: CI

on:
  push:
    branches: [main, master]
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        python-version: ["3.10", "3.11", "3.12"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
          cache: pip
      - name: Install torch (CPU only)
        run: |
          pip install --index-url https://download.pytorch.org/whl/cpu torch
      - name: Install package + test deps
        run: pip install -e ".[test]"
      - name: Run tests
        run: pytest tests/ -v --cov --cov-report=term-missing --cov-fail-under=85
```

- [ ] **Step 2: Local dry-run sanity check**

```bash
pip install -e ".[test]"
pytest tests/ --cov --cov-report=term-missing
```

Expected: All tests pass; coverage report prints. Coverage on `client/` and `nodes/` should be ≥85%.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "$(cat <<'EOF'
Add CI workflow (pytest matrix 3.10/3.11/3.12)

CPU-only torch from the pytorch.org CPU index so the runners don't
pull GPU wheels. --cov-fail-under=85 enforces the spec's coverage
floor on every push.
EOF
)"
```

---

## Task 20: README + examples + SMOKE.md (metscan-nodes repo)

**Files:**
- Create: `README.md`
- Create: `examples/save_and_pickup.json`
- Create: `examples/load_and_generate.json`
- Create: `examples/load_prompt_chain.json`
- Create: `tests/SMOKE.md`

- [ ] **Step 1: Write `README.md`**

```markdown
# metscan-nodes

ComfyUI custom nodes for the [metascan](https://github.com/pakfur/metascan) AI media browser.

Three MVP nodes:

- **Metascan · Save Image** — write a PNG batch into a metascan-watched directory. Metascan's filesystem watcher picks the file up on its own; no API call at save time.
- **Metascan · Load From Folder** — load a random / sequential / specific image from a metascan manual folder, with the file's extracted positive + negative prompt as bonus outputs.
- **Metascan · Load Prompt** — load a saved prompt from metascan's prompt library, scoped by manual folder and target model.

Plus a **Metascan · Settings** sentinel node for overriding URL / API key per workflow.

## Install

```
cd ComfyUI/custom_nodes
git clone <this-repo-url> metscan-nodes
pip install -r metscan-nodes/requirements.txt   # or: pip install httpx pillow numpy
```

Restart ComfyUI. The four nodes appear under the `metascan` category.

## Configure

Three ways to point the nodes at your metascan instance, in priority order:

1. Drop a **Metascan · Settings** node into your workflow with URL + API key.
2. Set env vars `METASCAN_URL` / `METASCAN_API_KEY` before launching ComfyUI.
3. Edit `~/.config/metscan-nodes/config.json`:

```json
{ "url": "http://localhost:8700", "api_key": "your-key-or-omit" }
```

If nothing is set, the nodes default to `http://localhost:8700` with no API key.

## Shared-host operations (ComfyUI + metascan on the same GPU)

These nodes do not load any models themselves — the design keeps GPU work out of the workflow path so the two processes don't fight over VRAM. However, metascan's *background* workers can still hold VRAM. If you run both on the same single GPU:

- Set `similarity.device = "cpu"` in metascan's `config.json` so its live CLIP inference uses CPU instead of holding VRAM.
- Don't trigger metascan VLM operations while ComfyUI is generating.
- If you queue an upscale in metascan, pause the queue (`POST /api/upscale/pause-all` or the UI's Pause button) while ComfyUI is busy, then resume when it's idle.
- If you have a second GPU, use `CUDA_VISIBLE_DEVICES` to pin metascan to it.

If the rigs collide, the failure mode is a clear `CUDA out of memory` in ComfyUI's log.

## Companion metascan changes

`Metascan · Load Prompt` depends on two endpoints added in a companion PR to the metascan repo:

- `POST /api/prompt/search`
- `GET /api/prompt/target-models`

Save Image and Load From Folder rely only on endpoints metascan already ships.

## Development

```
git clone <this-repo>
cd metscan-nodes
python -m venv .venv && . .venv/bin/activate
pip install -e ".[test]"
pytest tests/
```
```

- [ ] **Step 2: Write the three example workflows**

Each is a minimal ComfyUI workflow JSON. The exact node-ID layout doesn't matter for the plan — generate from ComfyUI by placing the relevant node, wiring it up, and clicking **Workflow → Save**. Commit whatever ComfyUI produces. The three workflows are:

- `examples/save_and_pickup.json` — a checkpoint loader → KSampler → VAE decode → **MetascanSaveImage**. Verifies the save-side flow.
- `examples/load_and_generate.json` — **MetascanLoadFromFolder** → VAE encode → KSampler (img2img). Verifies the load-side flow.
- `examples/load_prompt_chain.json` — **MetascanLoadPrompt** → CLIPTextEncode (×2 — positive + negative) → KSampler. Verifies the prompt-library flow.

If you don't have a running ComfyUI to generate these from, omit this step and revisit it after a live install — the CI gate doesn't depend on the example JSONs being present.

- [ ] **Step 3: Write `tests/SMOKE.md`**

```markdown
# Smoke Test Walkthrough

This is a manual end-to-end check that ties together a running metascan, a running ComfyUI, and the nodes. Not in CI. Run it before tagging a release.

## Prereqs

- Metascan running locally with at least one watched directory configured (`config.json` → `directories[].filepath`).
- That directory has `watch_directories: true` so the file watcher is live.
- At least one manual folder in metascan (`Folders` panel → New) with one or more images already in it.
- At least one saved prompt against an image in that folder (use the prompt-save UI to create one).
- ComfyUI running with this package installed under `custom_nodes/`.

## Save flow

1. Load `examples/save_and_pickup.json`.
2. In the **Metascan · Save Image** node, pick a watched directory from the dropdown.
3. Queue prompt.
4. Verify within 2 seconds: metascan UI shows the new row in its main grid (file watcher pickup).

## Load-from-folder flow

1. Load `examples/load_and_generate.json`.
2. In **Metascan · Load From Folder**, pick the manual folder you populated.
3. Set `selection_mode=random`, `seed=42`.
4. Queue prompt.
5. Verify: the downstream sampler runs on an image from that folder. The node's positive/negative outputs match what metascan UI shows for the same file.

## Load-prompt flow

1. Load `examples/load_prompt_chain.json`.
2. In **Metascan · Load Prompt**, pick the same folder, choose a target model that matches a saved prompt's `target_model`, and set `selection_mode=by_name` with the prompt's exact name.
3. Queue prompt.
4. Verify: downstream CLIPTextEncode receives the saved prompt text. Try `selection_mode=random` and re-queue with the same seed to verify reproducibility.

## Failure-mode spot checks

- Stop metascan. Open one of the nodes — dropdown should show `"<metascan offline — check MetascanSettings>"`. The node should still render in the editor.
- Restart metascan. Wait ~60s for the combo cache to expire (or restart ComfyUI). Dropdowns repopulate.
- Drop a **Metascan · Settings** node into the workflow with a wrong URL — confirm dropdowns reflect the override.
```

- [ ] **Step 4: Commit**

```bash
git add README.md examples/ tests/SMOKE.md
git commit -m "$(cat <<'EOF'
Add README, example workflows, and SMOKE.md walkthrough

README covers install, configure (settings node / env / file / default
chain), shared-host operations checklist (matches spec §10), and dev
loop. SMOKE.md is the manual end-to-end verification — not in CI but
gates a release.
EOF
)"
```

---

## Self-Review Checklist (verify before declaring the plan done)

Run through this list once after writing the plan:

- [ ] **Spec §3 (HTTP bridge):** Covered by Tasks 4-8 (client) and the lack of any direct-import / sidecar machinery in any node task.
- [ ] **Spec §4 (Repo layout):** Tasks 1, 18 establish the directory tree and entry-point exports.
- [ ] **Spec §5.1 (MetascanSaveImage):** Tasks 13-14 cover INPUT_TYPES dropdown, dir + subpath resolution with strftime, PngInfo embedding, batch loop, tensor pass-through, offline sentinel handling.
- [ ] **Spec §5.2 (MetascanLoadFromFolder):** Tasks 15-16 cover INPUT_TYPES, name→ID lookup, manual-folder items fetch, image_only + filename_filter, three selection modes, next_seed advancement, prompt-metadata pass-through.
- [ ] **Spec §5.3 (MetascanLoadPrompt):** Task 17 covers search + select, name vs random, "any" → null mapping, NULL negative → "".
- [ ] **Spec §5.4 (MetascanSettings):** Task 12 covers module-level override + empty-string clearing.
- [ ] **Spec §6 (HTTP client):** Tasks 4-7 build the methods; Task 8 adds the dropdown cache; Task 2 the error types.
- [ ] **Spec §7 (new metascan endpoints):** Tasks 9-11 cover DB method, search route, target-models route (all in the metascan repo).
- [ ] **Spec §8 (testing):** Every implementation task is preceded by failing tests; Task 19 enforces ≥85% coverage; Task 20 covers SMOKE.md.
- [ ] **Spec §9 (error handling):** ApiError + OfflineError (Task 2); per-node offline sentinel handling (Tasks 14, 16, 17); empty-folder + missing-prompt-name raises (Tasks 16, 17).
- [ ] **Spec §10 (co-host operations):** README section in Task 20; load-bearing "nodes do no GPU work" constraint embedded in node designs (no torch/CUDA calls anywhere in nodes/).
- [ ] **Spec §11 (operational notes):** Path-quoting (Task 6), batch handling (Task 14), `X-Client` versioning header (Task 4), collision counter (Task 14).
- [ ] **Spec §12 (out of scope):** No WebSocket, no LoadSimilar, no direct-import, no async — none of these appear as tasks.
- [ ] **Spec §13 (acceptance criteria):** All seven items can be checked off after the plan is executed (the metascan-repo PR is Tasks 9-11; the rest are this repo's plan).
- [ ] **Placeholder scan:** No "TBD", no "TODO", no "implement later", no "similar to Task N" cross-references — each task carries its own code.
- [ ] **Type consistency:** `ClientConfig` (Task 3) used identically in Tasks 4 / 12. `MetascanClient` signature stable across Tasks 4-8. Folder IDs string-typed throughout. `target_model` lowercase shortcodes throughout. `OFFLINE_SENTINEL` constant referenced consistently from Task 8 onward.

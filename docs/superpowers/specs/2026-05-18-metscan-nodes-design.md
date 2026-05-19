# metscan-nodes — Design Spec

**Date:** 2026-05-18
**Status:** Approved (brainstorming complete; ready for implementation planning)

## 1. Overview

`metscan-nodes` is a [ComfyUI](https://github.com/comfyanonymous/ComfyUI) custom-nodes package that integrates ComfyUI workflows with [metascan](https://github.com/pakfur/metascan) — the AI media browser that lives in the sibling `metascan/` repo. The MVP exposes three nodes that let workflows save outputs into metascan-watched directories, load images from metascan folders, and load saved prompts from the metascan prompt library.

Metascan already has a **passive** integration with ComfyUI: a metadata extractor that reads PNG/video sidecars from ComfyUI output folders during scans. This package adds the **active** counterpart — letting workflows talk to metascan during generation rather than after the fact.

## 2. Goals & non-goals

**Goals:**
- Drop-in ComfyUI nodes (single `custom_nodes/metscan-nodes/` directory) that operate against a running metascan instance.
- Cross-machine compatible: ComfyUI and metascan can run on the same host or different hosts.
- Zero GPU work inside the nodes themselves (load-bearing — see §10).
- Keep the two projects fully decoupled: no shared Python imports, no shared venv constraints.

**Non-goals (MVP):**
- Live progress / WebSocket subscriptions from metascan back to ComfyUI.
- Async ComfyUI node support (ComfyUI's node interface is synchronous; we follow that).
- Bundling metascan as a dependency or wrapping its internal modules.
- New auth schemes beyond metascan's existing `X-API-Key` header.
- A "load similar image" node (would require server-side CLIP, deferred).
- **Smart folders** in load nodes. Metascan's smart-folder rule engine lives in the frontend (Pinia store); there is no Python evaluator yet. Folder dropdowns expose `manual` folders only. Smart-folder support ships when metascan grows a Python resolver.

## 3. Architecture

**HTTP bridge.** Nodes call metascan's existing FastAPI REST endpoints over HTTP (default `http://localhost:8700`). No shared Python code with metascan. Authentication via `X-API-Key` header (matches metascan's existing scheme).

### Rejected alternatives

- **Direct Python import** of `metascan.core.*` modules: rejected because ComfyUI's venv would need to satisfy metascan's deps (specific PyTorch / FAISS / open-clip / NLTK versions), tightly coupling the projects and breaking cross-machine setups.
- **Filesystem-only with JSON sidecars**: rejected because it eliminates live query nodes (`LoadFromFolder`, `LoadPrompt`) — they need a running metascan to enumerate folders and search prompts.

## 4. Repo layout

```
metscan-nodes/
├── __init__.py                  # exports NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS
├── pyproject.toml               # name=comfyui-metscan-nodes, requires-python>=3.10
├── README.md
├── nodes/
│   ├── __init__.py
│   ├── save_image.py            # MetascanSaveImage
│   ├── load_from_folder.py      # MetascanLoadFromFolder
│   ├── load_prompt.py           # MetascanLoadPrompt
│   └── settings.py              # MetascanSettings (per-workflow URL/key override)
├── client/
│   ├── __init__.py
│   ├── api.py                   # MetascanClient (thin httpx wrapper)
│   ├── config.py                # Resolves URL/key from settings node → env → file → defaults
│   └── errors.py                # ApiError, OfflineError
├── tests/
│   ├── conftest.py              # respx-backed fake metascan API
│   ├── test_client.py
│   ├── test_save_image.py
│   ├── test_load_from_folder.py
│   ├── test_load_prompt.py
│   └── SMOKE.md                 # manual end-to-end walkthrough
└── examples/                    # ComfyUI workflow JSONs demonstrating each node
```

**Distribution:** ComfyUI Manager-compatible. User drops the repo into `ComfyUI/custom_nodes/metscan-nodes`. Declared runtime deps: `httpx>=0.27`, `pillow>=10` (PNG read/write with `PngInfo` chunks). `torch` is not declared because ComfyUI's environment provides it; tensors flow in from upstream IMAGE inputs and out via pass-through.

**Config resolution order (highest priority first):**
1. `MetascanSettings` node present in the workflow graph
2. Env vars `METASCAN_URL`, `METASCAN_API_KEY`
3. `~/.config/metscan-nodes/config.json`
4. Defaults: `http://localhost:8700`, no key

## 5. Node specs

### 5.1 MetascanSaveImage

Writes PNGs into a metascan-watched directory. Metascan's existing filesystem watcher (or next scan) ingests the new file with no further coordination from this node.

**Inputs:**

| Name | Type | Notes |
|---|---|---|
| `images` | IMAGE | Batch from upstream |
| `directory` | COMBO | Dropdown populated from `GET /api/config` → `directories[].filepath`. One entry per metascan-watched root. |
| `subpath` | STRING | Optional relative path appended under chosen directory. Supports `strftime` placeholders (e.g. `"%Y-%m/comfyui"`). Default `""`. |
| `filename_prefix` | STRING | Filename prefix; follows ComfyUI's standard `folder_paths.get_save_image_path` semantics for `%`-templates and collision counters. Default `"ComfyUI"`. |
| `embed_workflow` | BOOLEAN | Embed ComfyUI graph + prompt JSON into PNG `tEXt` chunks, matching the format metascan's ComfyUI extractor reads. Default `true`. |

**Outputs:** `IMAGE` (pass-through, unchanged tensor identity), `STRING` (absolute path of the **first** file written in the batch — consistent with ComfyUI core SaveImage's UI behavior; downstream nodes that need all paths can rely on the upstream batch index).

**Behavior:**
1. Resolve target dir = `<directory>/<subpath>` with `strftime` expansion. Create dirs as needed (`mkdir -p` semantics).
2. For each tensor in the batch, write PNG using PIL with `PngInfo` chunks for embedded prompt + workflow (matching ComfyUI core SaveImage's PNG metadata format).
3. No HTTP calls during execution. The metascan file watcher / next scan picks up the file.
4. Pass through the input IMAGE tensor unchanged; return the path of the last-written file (or first file — see §6).
5. Emit one log line per invocation.

**Offline behavior:** If `GET /api/config` fails at `INPUT_TYPES()` time, the `directory` dropdown shows a single sentinel entry `"<metascan offline — check MetascanSettings>"`. If the user runs the node with that sentinel selected, execute raises `OfflineError` — there's no usable fallback because the directory list is the whole point.

### 5.2 MetascanLoadFromFolder

Loads an image from a metascan folder (static or smart), returning the image tensor plus extracted prompt metadata.

**Inputs:**

| Name | Type | Notes |
|---|---|---|
| `folder` | COMBO | Dropdown of **manual** folders from `GET /api/folders` (filtered to `kind=="manual"`; smart folders deferred — see §2 non-goals) |
| `selection_mode` | COMBO | `random` / `sequential` / `specific` |
| `seed` | INT | RNG seed for `random`; cursor index for `sequential` |
| `index` | INT | Used only when `selection_mode=specific` |
| `filename_filter` | STRING | Optional substring match applied to file paths, post-fetch |
| `image_only` | BOOLEAN | Skip videos in the folder. Default `true`. |

**Outputs:**
- `IMAGE` — decoded image, normalized float32, shape `[1, H, W, 3]`, range `[0, 1]` (NHWC — ComfyUI's convention)
- `STRING` — absolute file path
- `STRING` — positive prompt (from extracted metadata; empty if absent)
- `STRING` — negative prompt (empty if absent)
- `INT` — `next_seed` — advanced cursor for chaining in `sequential` mode

**Behavior:**
1. `GET /api/folders/{id}` — returns the manual folder record including `items: [path, ...]` (the existing endpoint already resolves manual-folder membership).
2. Filter client-side: drop videos when `image_only=true` (by extension); apply `filename_filter` substring match. Sort the surviving list deterministically by `file_path`.
3. Select one path by mode:
   - `random`: index = `seed % len(paths)`
   - `sequential`: index = `seed % len(paths)`; `next_seed = (seed + 1) % len(paths)`
   - `specific`: index = `index % len(paths)` (clamps gracefully)
4. `GET /api/media/{path:url-encoded}` for the chosen path → returns the media detail record with extracted prompt metadata.
5. `GET /api/stream/{path:url-encoded}` to fetch bytes. Decode via PIL; convert to NHWC float32 tensor.
6. Pull `positive_prompt` / `negative_prompt` from the media detail's `data` blob (the extractor populates these fields).

**Error cases:** empty folder → raise with `"folder '{name}' contains no matching items"`; metascan unreachable → raise `OfflineError`.

### 5.3 MetascanLoadPrompt

Loads a saved prompt from metascan's `saved_prompts` table, scoped by folder and target model.

**Inputs:**

| Name | Type | Notes |
|---|---|---|
| `folder` | COMBO | Manual-folder list as in §5.2; scopes which media's `saved_prompts` are searched |
| `target_model` | COMBO | Populated from `GET /api/prompt/target-models` (new endpoint, §7). Fallback hardcoded list if offline: the seven actual `TargetModel` literal values plus `"any"` as a virtual entry: `["sd","pony","flux1","flux2","zimage","chroma","qwen","any"]`. When the user selects `"any"`, the node sends `target_model: null` in the search request (per §7.1). |
| `selection_mode` | COMBO | `random` / `by_name` |
| `prompt_name` | STRING | Required when `selection_mode=by_name`; ignored otherwise |
| `seed` | INT | RNG seed for `random` |

**Outputs:**
- `STRING` — positive prompt text
- `STRING` — negative prompt text (empty when DB column is NULL)
- `STRING` — chosen prompt's `name`
- `STRING` — source media file path the prompt was originally saved against

**Behavior:**
1. `POST /api/prompt/search` (new endpoint, §7) with `{folder_id, target_model, name?, limit: 500}`.
2. If `selection_mode=by_name` and result empty → raise with `"no saved prompt named '{name}' in folder '{folder}' for model '{target_model}'"`.
3. If `random` → pick `rows[seed % len(rows)]`.
4. Map row → outputs. `negative` NULL → `""`.

### 5.4 MetascanSettings (helper)

Optional sentinel node with two string inputs (URL, API key) and no output. Its presence in a workflow overrides env vars / config file. Use case: rigs with multiple metascan instances, or different workflows pointing at different servers.

## 6. HTTP client

Single class in `client/api.py`, sync-only (matches ComfyUI's sync execute model).

```python
class MetascanClient:
    def __init__(self, base_url: str, api_key: str | None, timeout: float = 10.0):
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"X-API-Key": api_key} if api_key else {},
            timeout=timeout,
        )

    # Config / folders
    def get_config(self) -> ConfigResponse: ...
    def list_folders(self) -> list[FolderInfo]: ...                 # caller filters to kind=="manual"
    def get_folder(self, folder_id: str) -> FolderInfo: ...         # includes resolved `items` list for manual

    # Media
    def get_media_detail(self, file_path: str) -> MediaDetail: ...  # GET /api/media/{path}
    def stream_bytes(self, file_path: str) -> bytes: ...            # GET /api/stream/{path}

    # Prompts
    def search_prompts(self, folder_id, target_model, name, limit) -> list[SavedPromptRow]: ...
    def target_models(self) -> list[str]: ...

    # Health
    def ping(self) -> bool: ...
```

**Error policy.** Two error types only:
- `ApiError` — non-2xx response or invalid JSON; carries `status_code` and `body_excerpt` (first 500 chars)
- `OfflineError` — connection refused, DNS failure, timeout

`MetascanSaveImage` does no HTTP at execute-time (only at `INPUT_TYPES()` for the dropdown), so neither error reaches its execute path. Load nodes catch both errors and re-raise with a human-readable message that ComfyUI shows in the node-error UI.

**Combo dropdown caching.** ComfyUI calls `INPUT_TYPES()` at editor load and again when the user opens the node. We cache the directory list and folder list for 60s in module-level dicts (one per resource type, keyed by base_url). On cache miss with offline server, return the sentinel string `"<metascan offline — check MetascanSettings>"`.

**Versioning header.** Outgoing requests include `X-Client: metscan-nodes/{version}`. Metascan logs include the header so version skew is diagnosable.

## 7. New metascan endpoints required

These do not exist in metascan today and must be added (in metascan's repo, separate PR) before `LoadPrompt` is functional. `SaveImage` and `LoadFromFolder` rely only on endpoints metascan already has.

### 7.1 `POST /api/prompt/search`

Search the `saved_prompts` table scoped by folder and target model.

```jsonc
// Request
{
  "folder_id": "fld_abc123",    // nullable string (folder IDs are strings in metascan); null = all media
  "target_model": "qwen",       // nullable: null = any target_model
  "name": "cinematic-portrait", // nullable: exact match if provided
  "limit": 500                  // default 100, max 500
}

// Response 200
{
  "prompts": [
    {
      "id": 7,
      "file_path": "/abs/path/source.png",
      "name": "cinematic-portrait",
      "prompt": "...",
      "negative": "..." | null,
      "target_model": "qwen",
      "architecture": "...",
      "styles": ["..."]
    }
  ]
}
```

**Implementation sketch:** new method `db.search_saved_prompts(folder_id, target_model, name, limit)` on `metascan/core/database_sqlite.py`. When `folder_id` is supplied, JOIN `saved_prompts` against `folder_items` on `file_path` and filter `folder_items.folder_id = ?`; the folder's `kind` must be `'manual'` (route returns 400 if the supplied folder is smart — see §2 non-goals; smart-folder support deferred until metascan has a Python rule resolver). When `target_model` is supplied, add `AND saved_prompts.target_model = ?`. When `name` is supplied, add `AND saved_prompts.name = ?`. Cap by `LIMIT`. Route added to `backend/api/prompt.py`.

### 7.2 `GET /api/prompt/target-models`

```jsonc
// Response 200
{ "target_models": ["sd","pony","flux1","flux2","zimage","chroma","qwen"] }
```

**Implementation sketch:** return the seven literal values of metascan's existing `TargetModel = Literal["sd","pony","flux1","flux2","zimage","chroma","qwen"]` (defined in `metascan/core/prompt_templates.py`). The node injects the `"any"` virtual option on the client side (it maps to `target_model=null` in the search request, never sent as a value). Trivial route in `backend/api/prompt.py`.

## 8. Testing strategy

Three layers, ordered by coverage value.

### 8.1 Client unit tests (`tests/test_client.py`)

Use [`respx`](https://lundberg.github.io/respx/) to mock httpx. Per `MetascanClient` method:
- Happy path: 200 + expected JSON → expected Python object
- 4xx: raises `ApiError` carrying status code and body excerpt
- Connection-refused: raises `OfflineError`
- 10s timeout: raises `OfflineError`

Combo-cache test: two `get_config()` calls within 60s hit network once; calls past 60s refetch.

### 8.2 Node logic tests (`tests/test_*_node.py`)

Import each node class directly and call `INPUT_TYPES()` and the main execute method. `MetascanClient` is monkey-patched to a fake in-memory implementation.

- **MetascanSaveImage:** verify PNG is written to the expected path, `PngInfo` chunks contain both `prompt` and `workflow` keys, tensor pass-through preserves identity (`id(out) == id(in)`), `strftime` placeholders in `subpath` expand, dirs are created if missing.
- **MetascanLoadFromFolder:** seeded `random` returns the same file across runs given the same seed; `next_seed` advances correctly in `sequential` mode and wraps at the end; `image_only=true` excludes video rows; output tensor is `float32`, shape `[1,H,W,3]`, range `[0,1]`.
- **MetascanLoadPrompt:** `by_name` returns that row; missing name raises with the helpful message in §5.3; random with seed is reproducible; NULL `negative` → `""`.

The `IMAGE` input for save-image tests is a synthesized `torch.zeros([1, 64, 64, 3])` tensor; no GPU required.

### 8.3 Live end-to-end smoke (`tests/SMOKE.md`)

Manual walkthrough: boot metascan locally, load `examples/save_and_pickup.json` in ComfyUI, run it, verify metascan UI shows the row within 2s (file-watcher latency). Not in CI — requires a full metascan installation.

**Coverage target:** ≥90% on `client/` and `nodes/`. `pytest --cov` runs in CI and fails the build below 85%.

**CI:** GitHub Actions matrix Python 3.10/3.11/3.12 on Linux. Deps: `httpx`, `respx`, `pytest`, `pytest-cov`, `torch` (CPU-only wheel), `pillow`. ComfyUI itself is not installed.

## 9. Error handling

| Failure | MetascanSaveImage | Load nodes |
|---|---|---|
| Metascan unreachable at `INPUT_TYPES()` | Dropdown shows offline sentinel | Dropdown shows offline sentinel |
| Metascan unreachable at execute | n/a (no HTTP at execute) | Raise `OfflineError` |
| 4xx (e.g., bad folder id) | n/a | Raise `ApiError` with body excerpt |
| 5xx | n/a | Raise `ApiError` |
| Filesystem write error (disk full / permissions) | Raise — workflow halts | n/a |
| Empty folder / no prompt match | n/a | Raise with helpful message (§5.2, §5.3) |
| Filename collision on disk | Handled by ComfyUI's standard `get_save_image_path` collision counter | n/a |

**One log line per node execution.** Format:
```
[metscan-nodes] SaveImage path=/abs/x.png count=4 took_ms=18 status=ok
[metscan-nodes] LoadFromFolder folder=42 selection=random seed=137 path=/abs/y.png took_ms=42 status=ok
[metscan-nodes] LoadPrompt folder=42 model=Qwen name=cinematic-portrait took_ms=7 status=ok
```

## 10. Shared-host operations (ComfyUI + metascan on the same box)

ComfyUI's model manager (`comfy.model_management`) tracks only models *it* loaded; it has no awareness of external processes. Metascan likewise won't notice ComfyUI is mid-generation. The two won't cooperate automatically on VRAM.

**Load-bearing design choice: no MVP node loads its own model.** Save = filesystem write only. LoadFromFolder = HTTP + JPEG/PNG decode (CPU). LoadPrompt = pure DB query. Therefore the nodes themselves cannot cause VRAM contention. Future nodes must preserve this property or opt in to a `vram_heavy=true` marker (out of MVP scope).

**Remaining contention sources are metascan-side, not node-side:**

- **Metascan's live CLIP inference subprocess** holds VRAM for fast similarity queries. Recommended co-host setting: run with `similarity.device = "cpu"` (the field already exists in `config.json`).
- **Metascan's VLM** is the heaviest single model (multi-GB). Co-host recommendation: don't trigger VLM operations while ComfyUI is running. If metascan adds an idle-unload, set it.
- **Metascan's upscale queue** spawns Real-ESRGAN worker subprocesses. Co-host recommendation: pause the queue (`POST /api/upscale/pause-all`, which already exists) while ComfyUI is generating. Resume manually from the UI when ComfyUI is idle. This package's `MetascanSaveImage` never queues upscales, so it can't trigger this race on its own; the user must trigger it via metascan UI.

These are all documentation in the README, not enforced by the nodes. If both processes target the same single GPU and the user ignores these recommendations, the failure mode is a clear `CUDA out of memory` in ComfyUI's log — the fix is one of: enable the co-host settings above, put metascan on a different GPU via `CUDA_VISIBLE_DEVICES`, or shut metascan workers down before generating.

## 11. Operational notes

- **Path semantics.** Metascan stores absolute paths. ComfyUI's `folder_paths` returns paths relative to its output dir. `MetascanSaveImage` resolves to absolute before reporting. Tests cover Windows path separators (metascan supports Windows per README).
- **Concurrent batches.** When `images` is a batch of N, save loops N times. No async.
- **Performance.** Each node does 0–2 HTTP calls. With localhost metascan, each is sub-10ms; whole save adds ≤5ms over baseline. Load nodes add ≤100ms for the round trip plus image decode.
- **Version skew.** Metascan responses include `X-Server-Version`. Client logs a WARN on major-version mismatch. New endpoints are added without breaking existing routes.

## 12. Out of scope (deferred)

- WebSocket subscription from nodes to metascan progress events
- A `MetascanLoadSimilar` node that does CLIP query at workflow runtime (would re-introduce the "MVP nodes do GPU work" exception we deliberately avoid)
- Bundling metascan as a pip dep / direct module import
- Async ComfyUI node API (when it stabilizes)
- Cross-machine auth beyond the existing `X-API-Key`
- Connection pooling beyond one `httpx.Client` per workflow
- A `MetascanTagAndQueue` post-hoc node (folded into a future "active integration" follow-on if user demand appears)

## 13. Acceptance criteria

- [ ] All three MVP nodes appear in ComfyUI's node menu under a `metascan/` category.
- [ ] `MetascanSaveImage` writes a PNG into a selected metascan-watched directory; metascan UI shows the new row within the watcher's normal pickup latency (≤2s on default settings).
- [ ] `MetascanLoadFromFolder` returns a usable `IMAGE` tensor wired to a downstream sampler with no further conversion; positive/negative prompt outputs match what metascan UI displays for the same file.
- [ ] `MetascanLoadPrompt` returns the saved prompt's text exactly as stored in `saved_prompts`; folder + target_model scoping returns the same rows as metascan UI would (manual cross-check).
- [ ] Tests pass under `pytest --cov` with ≥85% coverage on `client/` and `nodes/` on Python 3.10/3.11/3.12.
- [ ] README documents the co-host operations checklist from §10.
- [ ] Companion PR on metascan adds the two endpoints in §7 with their own tests.

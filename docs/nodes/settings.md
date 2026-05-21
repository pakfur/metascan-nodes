# Metascan · Settings

Sentinel node that overrides the metascan URL and API key for the current workflow run. Drop one anywhere in the graph; the override applies to every other Metascan node in the same workflow regardless of wiring order.

## What it pulls from metascan

Nothing. This node makes no HTTP call. It only stores values in process-local state that other nodes consult.

## Configuration precedence

The override set by this node wins over everything else. Full chain (highest priority first):

1. **Metascan · Settings node** (this node, if present and non-empty)
2. `METASCAN_URL` / `METASCAN_API_KEY` environment variables
3. `~/.config/metscan-nodes/config.json`
4. Built-in defaults (`http://localhost:8700`, no API key)

## Inputs

| Name      | Type     | Default                    | Description                                                                                       |
|-----------|----------|----------------------------|---------------------------------------------------------------------------------------------------|
| `url`     | `STRING` | `http://localhost:8700`    | Base URL of the metascan instance. Trailing slash optional. Use the LAN/WSL IP if not localhost.  |
| `api_key` | `STRING` | *(empty)*                  | API key if metascan requires auth. Leave blank for unauthenticated access.                        |

If **both** fields are empty, the override is cleared and the other configuration sources take over.

## Outputs

None. This is an `OUTPUT_NODE` with no return values — it acts purely for its side effect on the global override.

## Behavior notes

- The override is **per-process module-level state**, not per-instance. Only one Settings node should exist per workflow. If you wire two, the last one to execute wins.
- Override is cleared automatically when both inputs are blank — so deleting/disabling the node and re-running falls back to env vars or the config file.
- ComfyUI executes the Settings node like any other; downstream nodes that read the override must execute *after* it. In practice ComfyUI's topological sort handles this correctly because dropdown population (which reads the override) happens at `INPUT_TYPES()` time, before execution.

## Common errors

- **"Metascan is offline" on every other node** even with a Settings node wired: the URL is wrong or metascan isn't running. Test with `curl http://your-url:8700/api/config` from the ComfyUI host.
- **WSL hosting metascan, Windows hosting ComfyUI**: use the WSL distro's IP (`wsl hostname -I` from PowerShell), not `localhost`.

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

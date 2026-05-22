import { app } from "/scripts/app.js";
import { api } from "/scripts/api.js";

const NODE_CLASS = "MetascanLoadPrompt";

// Sync widgets after each execute:
// - `positive_prompt` / `negative_prompt` get refreshed from a live
//   fetch on `live_load=True`.
// - `index` is advanced by `selection_mode="increment"` so the user
//   sees the next-up value in the widget.
//
// `selection_mode="select"` and the thumbnail picker live on the
// separate MetascanSelectPrompt node (web/metscan-select-prompt.js).

app.registerExtension({
    name: "metscan.load-prompt",
    async setup() {
        api.addEventListener("executed", (event) => {
            const { node: nodeId, output } = event.detail || {};
            if (!output) return;
            const node = app.graph.getNodeById(nodeId);
            if (!node || node.comfyClass !== NODE_CLASS) return;

            let touched = false;
            for (const key of ["positive_prompt", "negative_prompt", "index"]) {
                const value = output[key];
                if (value === undefined) continue;
                const widget = node.widgets?.find((w) => w.name === key);
                if (widget) {
                    widget.value = Array.isArray(value) ? value[0] : value;
                    touched = true;
                }
            }
            if (touched) {
                node.setDirtyCanvas?.(true, true);
            }
        });
    },
});

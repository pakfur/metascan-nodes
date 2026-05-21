import { app } from "/scripts/app.js";
import { api } from "/scripts/api.js";

app.registerExtension({
    name: "metscan.load-prompt.widget-sync",
    async setup() {
        api.addEventListener("executed", (event) => {
            const { node: nodeId, output } = event.detail || {};
            if (!output) return;
            const node = app.graph.getNodeById(nodeId);
            if (!node || node.comfyClass !== "MetascanLoadPrompt") return;

            let touched = false;
            for (const key of ["positive_prompt", "negative_prompt"]) {
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

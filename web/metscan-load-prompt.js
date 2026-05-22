import { app } from "/scripts/app.js";
import { api } from "/scripts/api.js";

const NODE_CLASS = "MetascanLoadPrompt";

// ---------------------------------------------------------------------------
// Widget visibility toggle
//
// ComfyUI/LiteGraph treats type === "converted-widget" as drawn-but-zero-
// height; pair it with a computeSize returning [0, -4] to fully collapse
// the row (the -4 cancels LiteGraph's default per-widget padding).
// ---------------------------------------------------------------------------

const HIDDEN_TYPE = "converted-widget";
const HIDDEN_SIZE = () => [0, -4];

function setHidden(widget, hidden) {
    if (!widget) return;
    if (hidden) {
        if (widget.type === HIDDEN_TYPE) return;
        widget._origType = widget.type;
        widget._origComputeSize = widget.computeSize;
        widget.type = HIDDEN_TYPE;
        widget.computeSize = HIDDEN_SIZE;
    } else {
        if (widget.type !== HIDDEN_TYPE) return;
        widget.type = widget._origType;
        widget.computeSize = widget._origComputeSize;
        widget._origType = undefined;
        widget._origComputeSize = undefined;
    }
}

// ---------------------------------------------------------------------------
// Thumbnail picker overlay
//
// Single-instance overlay rendered as an absolutely-positioned <div> on
// document.body. Fetches `/metscan/prompts?folder=&target_model=` (a
// proxy registered by mscan_nodes/server_routes.py) and draws one row
// per result with a 96px thumbnail (also proxied via /metscan/thumbnail).
// Clicking a row writes the chosen name into the node's prompt_name
// widget, closes the overlay, and marks the canvas dirty.
//
// The Python node accepts "select" mode and looks up the row by the
// stored prompt_name — the overlay is purely a name-picking UI.
// ---------------------------------------------------------------------------

let activePicker = null;

function closePicker() {
    if (!activePicker) return;
    activePicker.cleanup();
    activePicker = null;
}

function openPicker(node, onPicked) {
    closePicker();

    const folder = node.widgets?.find((w) => w.name === "folder")?.value || "";
    const targetModel = node.widgets?.find((w) => w.name === "target_model")?.value || "any";
    const promptNameWidget = node.widgets?.find((w) => w.name === "prompt_name");

    const root = document.createElement("div");
    root.className = "metscan-prompt-picker";
    Object.assign(root.style, {
        position: "fixed",
        left: "50%",
        top: "50%",
        transform: "translate(-50%, -50%)",
        width: "min(520px, 90vw)",
        maxHeight: "70vh",
        background: "#1e1e1e",
        color: "#e0e0e0",
        border: "1px solid #444",
        borderRadius: "6px",
        boxShadow: "0 8px 32px rgba(0, 0, 0, 0.6)",
        display: "flex",
        flexDirection: "column",
        zIndex: "10000",
        fontFamily: "sans-serif",
        fontSize: "13px",
    });

    const header = document.createElement("div");
    Object.assign(header.style, {
        padding: "8px 12px",
        borderBottom: "1px solid #333",
        fontWeight: "600",
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
    });
    header.textContent = `Pick prompt — ${folder} / ${targetModel}`;
    const closeBtn = document.createElement("span");
    closeBtn.textContent = "✕";
    Object.assign(closeBtn.style, { cursor: "pointer", padding: "0 4px", opacity: "0.7" });
    closeBtn.addEventListener("click", closePicker);
    header.appendChild(closeBtn);
    root.appendChild(header);

    const list = document.createElement("div");
    Object.assign(list.style, {
        overflowY: "auto",
        flex: "1 1 auto",
        padding: "4px 0",
    });
    root.appendChild(list);

    const status = document.createElement("div");
    Object.assign(status.style, { padding: "12px", opacity: "0.7" });
    status.textContent = "Loading…";
    list.appendChild(status);

    document.body.appendChild(root);

    // --- close handlers ---
    function onDocMouseDown(e) {
        if (!root.contains(e.target)) closePicker();
    }
    function onKey(e) {
        if (e.key === "Escape") closePicker();
    }
    function onCanvasInteract() {
        // Canvas pan/zoom/click outside makes the overlay position stale
        // and feels orphaned — close on any canvas activity.
        closePicker();
    }
    const canvasEl = app.canvas?.canvas;
    setTimeout(() => {
        document.addEventListener("mousedown", onDocMouseDown, true);
        document.addEventListener("keydown", onKey, true);
        canvasEl?.addEventListener("wheel", onCanvasInteract, { once: true });
    }, 0);

    activePicker = {
        root,
        cleanup() {
            document.removeEventListener("mousedown", onDocMouseDown, true);
            document.removeEventListener("keydown", onKey, true);
            canvasEl?.removeEventListener("wheel", onCanvasInteract);
            root.remove();
        },
    };

    // --- fetch + render ---
    const params = new URLSearchParams({ folder, target_model: targetModel });
    fetch(`/metscan/prompts?${params}`)
        .then(async (r) => {
            const data = await r.json().catch(() => ({}));
            if (r.status === 503) throw new Error(data.error || "metascan offline");
            if (r.status >= 400) throw new Error(data.error || `HTTP ${r.status}`);
            return data;
        })
        .then((data) => {
            const prompts = data.prompts || [];
            list.innerHTML = "";
            if (prompts.length === 0) {
                const empty = document.createElement("div");
                Object.assign(empty.style, { padding: "12px", opacity: "0.7" });
                empty.textContent = "No prompts match this folder + target_model.";
                list.appendChild(empty);
                return;
            }
            for (const row of prompts) {
                list.appendChild(buildRow(row, promptNameWidget, node, onPicked));
            }
        })
        .catch((err) => {
            list.innerHTML = "";
            const errorEl = document.createElement("div");
            Object.assign(errorEl.style, { padding: "12px", color: "#ff8080" });
            errorEl.textContent = `Failed to load prompts: ${err.message}`;
            list.appendChild(errorEl);
        });
}

function buildRow(row, promptNameWidget, node, onPicked) {
    const item = document.createElement("div");
    Object.assign(item.style, {
        display: "flex",
        alignItems: "center",
        gap: "10px",
        padding: "6px 12px",
        cursor: "pointer",
        borderBottom: "1px solid #2a2a2a",
    });
    item.addEventListener("mouseenter", () => {
        item.style.background = "#2a2a2a";
    });
    item.addEventListener("mouseleave", () => {
        item.style.background = "";
    });
    item.addEventListener("click", () => {
        if (promptNameWidget) {
            promptNameWidget.value = row.name;
            promptNameWidget.callback?.(row.name);
        }
        node.setDirtyCanvas?.(true, true);
        closePicker();
        onPicked?.(row);
    });

    const img = document.createElement("img");
    Object.assign(img.style, {
        width: "96px",
        height: "96px",
        objectFit: "cover",
        background: "#000",
        flex: "0 0 96px",
        borderRadius: "3px",
    });
    img.loading = "lazy";
    // Query-string rather than path variable — aiohttp's path-var
    // routing chokes on percent-encoded slashes in some versions.
    img.src = `/metscan/thumbnail?file_path=${encodeURIComponent(row.file_path)}`;
    img.onerror = () => {
        img.style.opacity = "0.3";
    };
    item.appendChild(img);

    const label = document.createElement("div");
    label.textContent = row.name || "(unnamed)";
    Object.assign(label.style, { flex: "1 1 auto", wordBreak: "break-word" });
    item.appendChild(label);

    return item;
}

// ---------------------------------------------------------------------------
// Extension
// ---------------------------------------------------------------------------

app.registerExtension({
    name: "metscan.load-prompt",
    async setup() {
        // Sync widgets after each execute — positive/negative are
        // refreshed from a live fetch, and `index` is advanced by the
        // increment mode so the user sees the next-up value.
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

    async nodeCreated(node) {
        if (node.comfyClass !== NODE_CLASS) return;

        const promptNameWidget = node.widgets?.find((w) => w.name === "prompt_name");
        const selectionModeWidget = node.widgets?.find((w) => w.name === "selection_mode");

        // The "select" mode replaces the prompt_name text input with
        // this button (clicking opens the thumbnail overlay). When the
        // mode is anything else, this button hides and the stock
        // prompt_name text widget comes back.
        const pickWidget = node.addWidget(
            "button",
            "🖼 Pick prompt…",
            null,
            () => openPicker(node, () => updateForMode()),
            { serialize: false },
        );

        function updateForMode() {
            const isSelect = selectionModeWidget?.value === "select";
            setHidden(promptNameWidget, isSelect);
            setHidden(pickWidget, !isSelect);
            if (isSelect) {
                const cur = promptNameWidget?.value || "";
                pickWidget.name = cur ? `🖼 ${cur}` : "🖼 Pick prompt…";
            }
            // Recompute node height around the new widget layout so
            // the face shrinks/grows to fit.
            const newSize = node.computeSize?.();
            if (newSize) node.setSize?.(newSize);
            node.setDirtyCanvas?.(true, true);
        }

        if (selectionModeWidget) {
            const origCb = selectionModeWidget.callback;
            selectionModeWidget.callback = function (value, ...rest) {
                const ret = origCb?.call(this, value, ...rest);
                updateForMode();
                return ret;
            };
        }

        // Saved workflows: widget values are restored *after* nodeCreated
        // fires, via onConfigure. Re-run there so the layout matches the
        // restored selection_mode.
        const origOnConfigure = node.onConfigure;
        node.onConfigure = function (...args) {
            const ret = origOnConfigure?.apply(this, args);
            updateForMode();
            return ret;
        };

        // Initial layout — defer so widget defaults settle first.
        setTimeout(updateForMode, 0);
    },
});

import { app } from "/scripts/app.js";

const NODE_CLASS = "MetascanSelectImage";

// ---------------------------------------------------------------------------
// Widget visibility toggle (same idiom as metscan-select-prompt.js).
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
// Node-face preview
//
// Assigning to node.imgs is the same hook the built-in LoadImage and
// PreviewImage nodes use to render an image on the node face. The
// image shows up immediately on pick — independent of any workflow
// execute — which is the point of this node for I2I / I2V prompt
// writing.
// ---------------------------------------------------------------------------

function setPreview(node, filePath) {
    if (!filePath) {
        node.imgs = [];
        node.setDirtyCanvas?.(true, true);
        return;
    }
    const img = new Image();
    img.onload = () => {
        node.imgs = [img];
        node.setDirtyCanvas?.(true, true);
    };
    img.onerror = () => {
        node.imgs = [];
        node.setDirtyCanvas?.(true, true);
    };
    img.src = `/metscan/thumbnail?file_path=${encodeURIComponent(filePath)}`;
}

// ---------------------------------------------------------------------------
// Thumbnail picker overlay (same shape as metscan-select-prompt.js, but
// only filters by folder — target_model defaults to "any" server-side).
// On pick: writes prompt_name + source_file_path, then sets the node
// face preview.
// ---------------------------------------------------------------------------

let activePicker = null;

function closePicker() {
    if (!activePicker) return;
    activePicker.cleanup();
    activePicker = null;
}

function openPicker(node, onPicked) {
    closePicker();

    const widgetByName = (name) => node.widgets?.find((w) => w.name === name);
    const folder = widgetByName("folder")?.value || "";

    const root = document.createElement("div");
    root.className = "metscan-image-picker";
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
    header.textContent = `Pick image — ${folder}`;
    const closeBtn = document.createElement("span");
    closeBtn.textContent = "✕";
    Object.assign(closeBtn.style, { cursor: "pointer", padding: "0 4px", opacity: "0.7" });
    closeBtn.addEventListener("click", closePicker);
    header.appendChild(closeBtn);
    root.appendChild(header);

    const list = document.createElement("div");
    Object.assign(list.style, { overflowY: "auto", flex: "1 1 auto", padding: "4px 0" });
    root.appendChild(list);

    const status = document.createElement("div");
    Object.assign(status.style, { padding: "12px", opacity: "0.7" });
    status.textContent = "Loading…";
    list.appendChild(status);

    document.body.appendChild(root);

    function onDocMouseDown(e) {
        if (!root.contains(e.target)) closePicker();
    }
    function onKey(e) {
        if (e.key === "Escape") closePicker();
    }
    function onCanvasInteract() {
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

    const params = new URLSearchParams({ folder });
    fetch(`/metscan/images?${params}`)
        .then(async (r) => {
            const data = await r.json().catch(() => ({}));
            if (r.status === 503) throw new Error(data.error || "metascan offline");
            if (r.status >= 400) throw new Error(data.error || `HTTP ${r.status}`);
            return data;
        })
        .then((data) => {
            const images = data.images || [];
            list.innerHTML = "";
            if (images.length === 0) {
                const empty = document.createElement("div");
                Object.assign(empty.style, { padding: "12px", opacity: "0.7" });
                empty.textContent = "No images in this folder.";
                list.appendChild(empty);
                return;
            }
            for (const row of images) {
                list.appendChild(buildRow(row, node, onPicked));
            }
        })
        .catch((err) => {
            list.innerHTML = "";
            const errorEl = document.createElement("div");
            Object.assign(errorEl.style, { padding: "12px", color: "#ff8080" });
            errorEl.textContent = `Failed to load images: ${err.message}`;
            list.appendChild(errorEl);
        });
}

function buildRow(row, node, onPicked) {
    const item = document.createElement("div");
    Object.assign(item.style, {
        display: "flex",
        alignItems: "center",
        gap: "10px",
        padding: "6px 12px",
        cursor: "pointer",
        borderBottom: "1px solid #2a2a2a",
    });
    item.addEventListener("mouseenter", () => (item.style.background = "#2a2a2a"));
    item.addEventListener("mouseleave", () => (item.style.background = ""));
    item.addEventListener("click", () => {
        const widgetByName = (name) => node.widgets?.find((w) => w.name === name);
        const setWidget = (name, value) => {
            const w = widgetByName(name);
            if (!w) return;
            w.value = value;
            w.callback?.(value);
        };
        setWidget("image_name", row.name || "");
        setWidget("source_file_path", row.file_path || "");
        setPreview(node, row.file_path);
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
    img.src = `/metscan/thumbnail?file_path=${encodeURIComponent(row.file_path)}`;
    img.onerror = () => (img.style.opacity = "0.3");
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
    name: "metscan.select-image",

    async nodeCreated(node) {
        if (node.comfyClass !== NODE_CLASS) return;

        const imageNameWidget = node.widgets?.find((w) => w.name === "image_name");
        const sourceFilePathWidget = node.widgets?.find((w) => w.name === "source_file_path");

        setHidden(imageNameWidget, true);
        setHidden(sourceFilePathWidget, true);

        const pickWidget = node.addWidget(
            "button",
            "🖼 Pick image…",
            null,
            () => openPicker(node, () => refreshLabel()),
            { serialize: false },
        );

        function refreshLabel() {
            const cur = imageNameWidget?.value || "";
            pickWidget.name = cur ? `🖼 ${cur}` : "🖼 Pick image…";
            node.setDirtyCanvas?.(true, true);
        }

        // Slot the button right after the quality widget (i.e. above
        // the hidden image_name / source_file_path widgets) so the
        // visible row order stays folder → target_model → quality →
        // picker.
        const qualityIdx = node.widgets.findIndex((w) => w.name === "quality");
        if (qualityIdx !== -1 && node.widgets[node.widgets.length - 1] === pickWidget) {
            node.widgets.pop();
            node.widgets.splice(qualityIdx + 1, 0, pickWidget);
        }

        // Restore preview + label after a saved-workflow load.
        const origOnConfigure = node.onConfigure;
        node.onConfigure = function (...args) {
            const ret = origOnConfigure?.apply(this, args);
            refreshLabel();
            const filePath = sourceFilePathWidget?.value || "";
            if (filePath) setPreview(node, filePath);
            return ret;
        };

        setTimeout(() => {
            refreshLabel();
            const newSize = node.computeSize?.();
            if (newSize) node.setSize?.(newSize);
            const filePath = sourceFilePathWidget?.value || "";
            if (filePath) setPreview(node, filePath);
        }, 0);
    },
});

/* SmartDoc — Medical Summary frontend */
"use strict";

const $ = (sel) => document.querySelector(sel);

const views = {
  home: $("#view-home"),
  progress: $("#view-progress"),
  result: $("#view-result"),
};

let selectedFile = null;       // {name, b64}
let activeHistoryId = null;
let viewingJobId = null;       // job whose progress view is currently on screen

/* ---------------- helpers ---------------- */

function showView(name) {
  Object.entries(views).forEach(([k, el]) => el.classList.toggle("hidden", k !== name));
}

function fmtDate(epochSeconds) {
  const d = new Date(epochSeconds * 1000);
  return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" })
    + " · " + d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

function greetingWord() {
  const h = new Date().getHours();
  if (h < 5) return "Good night";
  if (h < 12) return "Morning";
  if (h < 18) return "Afternoon";
  return "Evening";
}

async function api(path, opts) {
  const r = await fetch(path, opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
  return data;
}

function escapeHtml(s) {
  return (s || "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* ---------------- status ---------------- */

async function refreshStatus() {
  const dot = $("#status-dot");
  const banner = $("#status-banner");
  try {
    const s = await api("/api/status");
    const problems = [];
    if (!s.ollama) problems.push("Ollama is not running — start the Ollama app and retry.");
    else {
      if (!s.summarizer.available)
        problems.push(`Summarizer model missing — run: ollama pull ${s.summarizer.tag}`);
      if (!s.vision.available)
        problems.push(`Vision model missing (file transcription disabled) — run: ollama pull ${s.vision.tag}`);
    }
    if (s.pdf_support === false)
      problems.push("PDF support disabled — run: python -m pip install pymupdf (then restart the app)");
    if (problems.length === 0) {
      dot.className = "status-dot ok";
      dot.title = `Ollama OK — ${s.summarizer.tag} + ${s.vision.tag}`;
      banner.classList.add("hidden");
    } else {
      dot.className = "status-dot bad";
      dot.title = problems.join(" ");
      banner.innerHTML = problems.map(escapeHtml).join("<br>");
      banner.classList.remove("hidden");
    }
  } catch {
    dot.className = "status-dot bad";
    dot.title = "Backend unreachable";
  }
}

/* ---------------- history sidebar ---------------- */

async function refreshHistory() {
  try {
    const items = await api("/api/history");
    const list = $("#history-list");
    list.innerHTML = "";
    $("#history-empty").classList.toggle("hidden", items.length > 0);
    for (const item of items) {
      const li = document.createElement("li");
      li.className = "history-item" + (item.id === activeHistoryId ? " active" : "");
      li.innerHTML = `
        <div class="history-item-main">
          <div class="history-item-name">${escapeHtml(item.name)}</div>
          <div class="history-item-date">${fmtDate(item.created_at)}</div>
        </div>
        <button class="history-item-edit" title="Rename">✎</button>
        <button class="history-item-del" title="Delete">✕</button>`;
      li.querySelector(".history-item-main").addEventListener("click", () => openRecord(item.id));
      li.querySelector(".history-item-edit").addEventListener("click", async (e) => {
        e.stopPropagation();
        const name = prompt("Rename summary:", item.name);
        if (name && name.trim()) {
          await api(`/api/history/${item.id}/rename`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name: name.trim() }),
          });
          refreshHistory();
          if (item.id === activeHistoryId) $("#result-title").textContent = name.trim();
        }
      });
      li.querySelector(".history-item-del").addEventListener("click", async (e) => {
        e.stopPropagation();
        if (!confirm(`Delete "${item.name}"?`)) return;
        await api(`/api/history/${item.id}`, { method: "DELETE" });
        if (item.id === activeHistoryId) goHome();
        refreshHistory();
      });
      list.appendChild(li);
    }
  } catch { /* backend not up yet */ }
}

/* ---------------- input handling ---------------- */

function setTab(name) {
  document.querySelectorAll(".tab").forEach((t) =>
    t.classList.toggle("active", t.dataset.tab === name));
  $("#pane-file").classList.toggle("hidden", name !== "file");
  $("#pane-text").classList.toggle("hidden", name !== "text");
  updateSendEnabled();
}

function updateSendEnabled() {
  const fileTab = !$("#pane-file").classList.contains("hidden");
  const ready = fileTab ? !!selectedFile : $("#text-input").value.trim().length > 0;
  $("#btn-send").disabled = !ready;
}

function acceptFile(file) {
  const okExt = [".pdf", ".docx", ".txt", ".json", ".png", ".jpg", ".jpeg"];
  const dot = file.name.lastIndexOf(".");
  const ext = dot > 0 ? file.name.slice(dot).toLowerCase() : "";
  if (!okExt.includes(ext)) {
    alert(`Unsupported file type "${ext}".\nSupported: ${okExt.join(", ")}`);
    return;
  }
  const reader = new FileReader();
  reader.onload = () => {
    const b64 = reader.result.split(",", 2)[1];
    selectedFile = { name: file.name, b64 };
    $("#file-chip-name").textContent = file.name;
    $("#file-chip").classList.remove("hidden");
    updateSendEnabled();
  };
  reader.readAsDataURL(file);
}

/* ---------------- job lifecycle ---------------- */

/* Per-stage themed icons. Each stage key has an idle outline glyph and an
   active animated variant; done stages share a drawn-in check. */
const SVG = (cls, body) =>
  `<svg class="sicon ${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor"
        stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">${body}</svg>`;

const DOC_FRAME = '<rect x="5.5" y="3.5" width="13" height="17" rx="2"/>';
const EYE = '<path d="M4 12c2.2-3.6 4.9-5.4 8-5.4s5.8 1.8 8 5.4c-2.2 3.6-4.9 5.4-8 5.4S6.2 15.6 4 12z"/><circle cx="12" cy="12" r="2.3"/>';

const STAGE_GFX = {
  transcribe: {
    idle: SVG("", DOC_FRAME + '<path d="M9 9h6.5M9 12.5h6.5M9 16h4" stroke-opacity=".6"/>'),
    active: SVG("anim-scan", DOC_FRAME
      + '<path class="ln l1" d="M9 9h6.5"/><path class="ln l2" d="M9 12.5h6.5"/><path class="ln l3" d="M9 16h4"/>'
      + '<line class="beam" x1="6.5" y1="6" x2="17.5" y2="6" stroke-width="2"/>'),
  },
  anonymize: {
    idle: SVG("", EYE + '<line x1="5.5" y1="19" x2="18.5" y2="5"/>'),
    active: SVG("anim-redact",
      '<path d="M5 7h14M5 12h14M5 17h9" stroke-opacity=".35"/>'
      + '<rect class="bar b1" x="5" y="5.7" width="14" height="2.6" rx="1.3"/>'
      + '<rect class="bar b2" x="5" y="10.7" width="14" height="2.6" rx="1.3"/>'
      + '<rect class="bar b3" x="5" y="15.7" width="9" height="2.6" rx="1.3"/>'),
  },
  summarize: {
    idle: SVG("", '<path d="M5 7.5h14M5 12h10M5 16.5h6"/>'),
    active: SVG("anim-write",
      '<path d="M5 8h13M5 12.5h9M5 17h5.5" stroke-opacity=".28"/>'
      + '<path class="wl w1" d="M5 8h13"/><path class="wl w2" d="M5 12.5h9"/><path class="wl w3" d="M5 17h5.5"/>'
      + '<path class="spark" d="M17.8 12.4l.95 2.55 2.55.95-2.55.95-.95 2.55-.95-2.55-2.55-.95 2.55-.95z" fill="currentColor" stroke="none"/>'),
  },
  deanonymize: {
    idle: SVG("", EYE),
    active: SVG("anim-reveal",
      '<path d="M5 7h14M5 12h14M5 17h9" stroke-opacity=".55"/>'
      + '<rect class="veil v1" x="5" y="5.7" width="14" height="2.6" rx="1.3"/>'
      + '<rect class="veil v2" x="5" y="10.7" width="14" height="2.6" rx="1.3"/>'
      + '<rect class="veil v3" x="5" y="15.7" width="9" height="2.6" rx="1.3"/>'),
  },
  complete: {
    idle: SVG("", '<path d="M6 12.5l4 4 8-9"/>'),
    active: SVG("", '<path d="M6 12.5l4 4 8-9"/>'),
  },
};

const CHECK_DONE = SVG("anim-check",
  '<path class="tick" d="M6.5 12.5l3.8 3.8 7.2-8.6" stroke-width="2.2"/>');

function stageIcon(key, status) {
  const gfx = STAGE_GFX[key] || STAGE_GFX.complete;
  if (status === "active") return gfx.active;
  if (status === "done") return CHECK_DONE;
  if (status === "skipped") return '<span class="icon-text">–</span>';
  if (status === "error") return '<span class="icon-text">!</span>';
  return gfx.idle;
}

/* Diff-based renderer: the poll loop calls this every 600 ms, and replacing
   the icon nodes each time would visibly restart the looping animations.
   Rows are created once; only changed statuses swap their icon. */
function renderStages(stages) {
  const wrap = $("#stages");
  if (wrap.children.length !== stages.length) {
    wrap.innerHTML = "";
    for (const st of stages) {
      const div = document.createElement("div");
      div.innerHTML = `
        <div class="stage-rail">
          <div class="stage-icon"></div>
          <div class="stage-line"></div>
        </div>
        <div class="stage-body">
          <div class="stage-label"></div>
          <div class="stage-detail hidden"></div>
          <div class="stage-time hidden"></div>
        </div>`;
      div.dataset.status = "";
      wrap.appendChild(div);
    }
  }
  stages.forEach((st, i) => {
    const row = wrap.children[i];
    row.querySelector(".stage-label").textContent = st.label;
    if (row.dataset.status !== st.status) {
      row.dataset.status = st.status;
      row.className = `stage ${st.status}`;
      row.querySelector(".stage-icon").innerHTML = stageIcon(st.key, st.status);
    }
    const detail = row.querySelector(".stage-detail");
    detail.textContent = st.detail || "";
    detail.classList.toggle("hidden", !st.detail);
    const time = row.querySelector(".stage-time");
    time.textContent = st.seconds != null ? `${st.seconds}s` : "";
    time.classList.toggle("hidden", st.seconds == null);
  });
}

const STAGE_SKELETON = [
  { key: "transcribe", label: "Transcribing file to text..." },
  { key: "anonymize", label: "Anonymizing content..." },
  { key: "summarize", label: "Summarizing..." },
  { key: "deanonymize", label: "De-anonymizing..." },
  { key: "complete", label: "Summary complete" },
];

async function startJob() {
  const fileTab = !$("#pane-file").classList.contains("hidden");
  const body = fileTab
    ? { filename: selectedFile.name, content_b64: selectedFile.b64 }
    : { text: $("#text-input").value };

  $("#btn-send").disabled = true;
  try {
    const { job_id } = await api("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    viewingJobId = job_id;
    $("#progress-title").textContent = fileTab ? selectedFile.name : "Pasted text";
    $("#progress-error").classList.add("hidden");
    $("#btn-cancel-view").classList.add("hidden");
    renderStages(STAGE_SKELETON.map((s) =>
      ({ ...s, status: "pending", detail: "", seconds: null })));
    showView("progress");
    pollJob(job_id);
  } catch (e) {
    alert("Could not start the job: " + e.message);
    updateSendEnabled();
  }
}

function showJobError(message) {
  $("#progress-error").textContent = message;
  $("#progress-error").classList.remove("hidden");
  $("#btn-cancel-view").classList.remove("hidden");
}

function pollJob(jobId) {
  // Each job polls on its own timer. Polling continues if the user
  // navigates away so the finished run still lands in the sidebar;
  // the view is only touched while this job's progress is on screen.
  let notFound = 0;
  const timer = setInterval(async () => {
    let job;
    try {
      job = await api(`/api/jobs/${jobId}`);
      notFound = 0;
    } catch (e) {
      notFound += 1;
      if (notFound >= 8) {
        clearInterval(timer);
        if (viewingJobId === jobId) showJobError("Lost contact with the job: " + e.message);
      }
      return;
    }
    if (viewingJobId === jobId) renderStages(job.stages);
    if (job.status === "done") {
      clearInterval(timer);
      refreshHistory();
      if (viewingJobId === jobId) {
        viewingJobId = null;
        activeHistoryId = job.result.id;
        refreshHistory();
        showRecord(job.result);
      }
    } else if (job.status === "error") {
      clearInterval(timer);
      if (viewingJobId === jobId) showJobError(job.error || "Unknown error");
    }
  }, 600);
}

/* ---------------- result rendering ---------------- */

function showRecord(rec) {
  $("#result-title").textContent = rec.name;
  $("#result-date").textContent = fmtDate(rec.created_at);
  $("#summary-text").textContent = rec.summary;
  $("#source-text").textContent = rec.source_text || "";

  // Run details panel
  const t = rec.telemetry || {};
  const input = rec.input || {};
  const stageRows = (rec.stages || [])
    .filter((s) => s.seconds != null)
    .map((s) => `<dt>${escapeHtml(s.label)}</dt><dd>${s.seconds}s — ${escapeHtml(s.detail || "")}</dd>`)
    .join("");
  $("#run-body").innerHTML = `
    <dl class="kv">
      <dt>Model</dt><dd>${escapeHtml(t.model || "")} (winning configuration, thesis §4.4)</dd>
      <dt>Methodology</dt><dd>Few-shot prompt engineering — 25 physician reference summaries in the system prompt</dd>
      <dt>Generation</dt><dd>temperature 0.0 · top-p 1.0 · seed 42 · max 512 tokens · num_ctx ${t.num_ctx ?? "—"}</dd>
      <dt>Input</dt><dd>${escapeHtml(input.type || "")}${input.filename ? " — " + escapeHtml(input.filename) : ""} (${input.words ?? "?"} words)</dd>
      <dt>Summary tokens</dt><dd>${t.eval_count ?? "—"} generated in ${t.wall_seconds ?? "—"}s (prompt: ${t.prompt_eval_count ?? "—"} tokens)</dd>
      ${stageRows}
    </dl>`;

  showView("result");
}

async function openRecord(id) {
  try {
    const rec = await api(`/api/history/${id}`);
    viewingJobId = null;  // stop any progress view updates; polling continues
    activeHistoryId = id;
    refreshHistory();
    showRecord(rec);
  } catch (e) {
    alert("Could not open record: " + e.message);
  }
}

function goHome() {
  activeHistoryId = null;
  viewingJobId = null;  // running jobs keep polling and land in the sidebar
  selectedFile = null;
  $("#file-chip").classList.add("hidden");
  $("#file-input").value = "";
  $("#text-input").value = "";
  updateSendEnabled();
  refreshHistory();
  showView("home");
}

/* ---------------- wiring ---------------- */

$("#greeting-text").textContent = greetingWord();

document.querySelectorAll(".tab").forEach((t) =>
  t.addEventListener("click", () => setTab(t.dataset.tab)));

$("#btn-browse").addEventListener("click", () => $("#file-input").click());
$("#file-input").addEventListener("change", (e) => {
  if (e.target.files.length) acceptFile(e.target.files[0]);
});
$("#file-chip-remove").addEventListener("click", (e) => {
  e.stopPropagation();
  selectedFile = null;
  $("#file-chip").classList.add("hidden");
  $("#file-input").value = "";
  updateSendEnabled();
});

const dz = $("#dropzone");
let dragDepth = 0;  // dragleave fires when crossing child elements
dz.addEventListener("dragenter", (e) => {
  e.preventDefault();
  dragDepth += 1;
  dz.classList.add("dragover");
});
dz.addEventListener("dragover", (e) => e.preventDefault());
dz.addEventListener("dragleave", (e) => {
  e.preventDefault();
  dragDepth = Math.max(0, dragDepth - 1);
  if (dragDepth === 0) dz.classList.remove("dragover");
});
dz.addEventListener("drop", (e) => {
  e.preventDefault();
  dragDepth = 0;
  dz.classList.remove("dragover");
  if (e.dataTransfer.files.length) acceptFile(e.dataTransfer.files[0]);
});

$("#text-input").addEventListener("input", updateSendEnabled);
$("#btn-send").addEventListener("click", startJob);
$("#text-input").addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key === "Enter" && !$("#btn-send").disabled) startJob();
});

$("#btn-new").addEventListener("click", goHome);
$("#btn-cancel-view").addEventListener("click", goHome);

$("#btn-copy").addEventListener("click", async () => {
  await navigator.clipboard.writeText($("#summary-text").textContent);
  $("#btn-copy").textContent = "Copied ✓";
  setTimeout(() => ($("#btn-copy").textContent = "Copy"), 1500);
});

$("#btn-download").addEventListener("click", () => {
  const blob = new Blob([$("#summary-text").textContent], { type: "text/plain;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = ($("#result-title").textContent || "summary") + ".txt";
  a.click();
  URL.revokeObjectURL(a.href);
});

/* ---------------- init ---------------- */

refreshStatus();
setInterval(refreshStatus, 15000);
refreshHistory();
showView("home");

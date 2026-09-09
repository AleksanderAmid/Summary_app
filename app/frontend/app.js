/* SmartDoc — Medical Summary frontend */
"use strict";

const $ = (sel) => document.querySelector(sel);

const views = {
  home: $("#view-home"),
  progress: $("#view-progress"),
  result: $("#view-result"),
};

const MAX_DOCUMENTS = 10;
const MAX_UPLOAD_BYTES = 64 * 1024 * 1024;
let selectedFiles = [];
let submitting = false;
let updateInstalling = false;
let activeHistoryId = null;
let currentRecord = null;
let previewUrls = [];
let exportBusy = false;
let viewingJobId = null;       // job whose progress view is currently on screen

/* ---------------- helpers ---------------- */

function showView(name) {
  document.querySelectorAll(".input-popover[open]").forEach((panel) => { panel.open = false; });
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
    $("#input-model").textContent = s.summarizer.tag === "gemma4:12b" ? "Gemma 4 12B" : s.summarizer.tag;
    const problems = [];
    if (!s.ollama) problems.push("Ollama is not running — start the Ollama app and retry.");
    else {
      if (!s.summarizer.available)
        problems.push(`Summarizer model missing — run: ollama pull ${s.summarizer.tag}`);
      if (!s.vision.available && !s.ocr?.tesseract && !s.ocr?.paddleocr)
        problems.push(`No scan reader is available. Run setup_ocr.ps1 or install ${s.vision.tag}.`);
    }
    if (s.ocr && !s.ocr.tesseract && !s.ocr.paddleocr)
      problems.push("Dedicated Swedish OCR is unavailable; scans will use the slower vision reader. Run setup_ocr.ps1.");
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
  document.querySelectorAll(".tab").forEach((t) => {
    t.classList.toggle("active", t.dataset.tab === name);
    t.setAttribute("aria-pressed", String(t.dataset.tab === name));
  });
  $("#transcription-options").classList.toggle("hidden", name === "text");
  $("#pane-file").classList.toggle("hidden", name !== "file");
  $("#pane-text").classList.toggle("hidden", name !== "text");
  updateSendEnabled();
}

function updateSendEnabled() {
  const fileTab = !$("#pane-file").classList.contains("hidden");
  const ready = fileTab ? selectedFiles.length > 0 : $("#text-input").value.trim().length > 0;
  $("#btn-send").disabled = !ready || submitting || updateInstalling;
  $("#btn-browse").disabled = submitting || updateInstalling;
  $("#file-input").disabled = submitting || updateInstalling;
}

function fileError(message = "") {
  $("#file-error").textContent = message;
  $("#file-error").classList.toggle("hidden", !message);
}

function renderFiles() {
  const list = $("#file-list");
  list.innerHTML = "";
  for (const file of selectedFiles) {
    const chip = document.createElement("li");
    chip.className = "file-chip";
    const name = document.createElement("span");
    name.textContent = file.name;
    name.title = file.name;
    const remove = document.createElement("button");
    remove.textContent = "×";
    remove.setAttribute("aria-label", "Remove " + file.name);
    remove.disabled = submitting || updateInstalling;
    remove.addEventListener("click", () => {
      selectedFiles = selectedFiles.filter((item) => item !== file);
      fileError();
      renderFiles();
    });
    chip.append(name, remove);
    list.appendChild(chip);
  }
  $("#dropzone").classList.toggle("has-files", selectedFiles.length > 0);
  const bytes = selectedFiles.reduce((total, file) => total + file.size, 0);
  $("#file-count").textContent = selectedFiles.length + " / " + MAX_DOCUMENTS + " documents · " + (bytes / 1024 / 1024).toFixed(1) + " MB";
  $("#file-count").classList.toggle("hidden", selectedFiles.length === 0);
  updateSendEnabled();
}

function acceptFiles(files) {
  if (submitting || updateInstalling) return;
  const okExt = [".pdf", ".docx", ".txt", ".json", ".png", ".jpg", ".jpeg"];
  const key = (file) => file.name + ":" + file.size + ":" + file.lastModified;
  const known = new Set(selectedFiles.map(key));
  const additions = [];
  for (const file of Array.from(files)) {
    if (known.has(key(file))) continue;
    known.add(key(file));
    const ext = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
    if (!okExt.includes(ext)) return fileError(file.name + ": unsupported file type.");
    if (file.size === 0) return fileError(file.name + ": this document is empty.");
    additions.push(file);
  }
  const next = [...selectedFiles, ...additions];
  if (next.length > MAX_DOCUMENTS)
    return fileError("You can add up to 10 documents. Remove a document before adding more.");
  if (next.reduce((sum, file) => sum + file.size, 0) > MAX_UPLOAD_BYTES)
    return fileError("The selected documents exceed the 64 MB combined limit.");
  selectedFiles = next;
  fileError();
  renderFiles();
}

function readFile(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve({ filename: file.name, content_b64: String(reader.result).split(",", 2)[1] });
    reader.onerror = () => reject(new Error("Could not read " + file.name + ". Select it again."));
    reader.onabort = () => reject(new Error("Reading " + file.name + " was cancelled."));
    reader.readAsDataURL(file);
  });
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
  { key: "anonymize", label: "Pseudonymising documents..." },
  { key: "summarize", label: "Summarizing..." },
  { key: "deanonymize", label: "Restoring summary identifiers..." },
  { key: "complete", label: "Summary complete" },
];

async function startJob() {
  if (submitting || updateInstalling || $("#btn-send").disabled) return;
  const fileTab = !$("#pane-file").classList.contains("hidden");
  const files = selectedFiles.slice();
  const pastedText = $("#text-input").value;
  const additional = $("#additional-identifiers").value.split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
  submitting = true;
  renderFiles();
  try {
    const body = fileTab
      ? { files: await Promise.all(files.map(readFile)) }
      : { text: pastedText };
    body.additional_identifiers = additional;
    body.transcription_mode = $("#transcription-mode").value;
    const { job_id } = await api("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    viewingJobId = job_id;
    $("#progress-title").textContent = fileTab
      ? (files.length === 1 ? files[0].name : "Combined summary · " + files.length + " documents")
      : "Pasted text";
    $("#progress-error").classList.add("hidden");
    $("#btn-cancel-view").classList.add("hidden");
    renderStages(STAGE_SKELETON.map((s) =>
      ({ ...s, status: "pending", detail: "", seconds: null })));
    showView("progress");
    pollJob(job_id);
  } catch (e) {
    fileError("Could not start the summary: " + e.message);
    if (!fileTab) alert("Could not start the summary: " + e.message);
  } finally {
    submitting = false;
    renderFiles();
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
  currentRecord = rec;
  $("#export-error").classList.add("hidden");
  $("#btn-preview").disabled = false;
  $("#result-title").textContent = rec.name;
  $("#result-date").textContent = fmtDate(rec.created_at);
  $("#summary-text").textContent = rec.summary;
  $("#source-text").textContent = rec.evidence
    ? rec.evidence.map((u) => "[" + u.id + "] " + u.document + "\n" + u.text).join("\n\n")
    : rec.source_text || "";
  const privacy = rec.pseudonymisation || {};
  $("#privacy-note").textContent = privacy.implemented
    ? "The summary uses original identifiers. Pseudonymised documents keep placeholders. The temporary encrypted mapping has been deleted."
    : "This older summary predates pseudonymisation. Process its source again to create a pseudonymised document.";
  $("#export-kind").querySelector('option[value="pseudonymised"]').disabled = !privacy.implemented;
  $("#export-kind").querySelector('option[value="both"]').disabled = !privacy.implemented;
  $("#export-kind").value = "summary";
  const notes = [...(rec.transcription?.warnings || []), ...(privacy.warnings || []), ...(rec.quality_warnings || []), ...(rec.uncertainties || [])];
  $("#review-notes").innerHTML = notes.map((note) => "<p>" + escapeHtml(note) + "</p>").join("");
  $("#review-notes").classList.toggle("hidden", notes.length === 0);

  // Run details panel
  const t = rec.telemetry || {};
  const input = rec.input || {};
  const stageRows = (rec.stages || [])
    .filter((s) => s.seconds != null)
    .map((s) => `<dt>${escapeHtml(s.label)}</dt><dd>${s.seconds}s — ${escapeHtml(s.detail || "")}</dd>`)
    .join("");
  $("#run-body").innerHTML = `
    <dl class="kv">
      <dt>Model</dt><dd>${escapeHtml(t.model || "")} </dd>
      <dt>Methodology</dt><dd>${escapeHtml(t.method || "Legacy summary configuration")}</dd>
      <dt>Generation</dt><dd>Source-linked generation · context ${t.num_ctx ?? "—"}</dd>
      <dt>Document reading</dt><dd>${escapeHtml(rec.transcription?.mode || "Legacy reader")}${input.files ? " — " + input.files.flatMap((f, i) => (f.pages || []).map((p) => "Document " + (i+1) + ", page " + p.page_num + ": " + (p.method || f.method) + (p.cached ? " (reused)" : "") + (p.seconds != null ? " · " + p.seconds + "s" : ""))).map(escapeHtml).join("<br>") : ""}</dd>
      <dt>Input</dt><dd>${escapeHtml(input.type || "")}${input.files ? " — " + input.files.map((f) => escapeHtml(f.filename)).join(", ") : (input.filename ? " — " + escapeHtml(input.filename) : "")} (${input.words ?? "?"} words)</dd>
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
  selectedFiles = [];
  fileError();
  renderFiles();
  $("#file-input").value = "";
  $("#text-input").value = "";
  $("#additional-identifiers").value = "";
  refreshInputSettings();
  currentRecord = null;
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
  acceptFiles(e.target.files);
  e.target.value = "";
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
  if (e.dataTransfer.files.length) acceptFiles(e.dataTransfer.files);
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

function exportPath(kind, format) {
  return "/api/history/" + currentRecord.id + "/export?kind=" + encodeURIComponent(kind)
    + "&format=" + encodeURIComponent(format);
}
async function fetchExport(kind, format) {
  const response = await fetch(exportPath(kind, format), {cache: "no-store"});
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.error || "Could not export the document.");
  }
  return response.blob();
}
function showExportError(error) {
  $("#export-error").textContent = error.message;
  $("#export-error").classList.remove("hidden");
}
$("#export-kind").addEventListener("change", () => {
  $("#btn-preview").disabled = $("#export-kind").value === "both";
});
$("#btn-download").addEventListener("click", async () => {
  if (!currentRecord || exportBusy) return;
  exportBusy = true;
  $("#btn-download").disabled = true;
  $("#export-error").classList.add("hidden");
  const kind = $("#export-kind").value;
  const format = $("#export-format").value;
  try {
    const blob = await fetchExport(kind, format);
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = kind === "both" ? "smartdoc-documents.zip"
      : (kind === "summary" ? "summary" : "pseudonymised-documents") + "." + format;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
  } catch (error) { showExportError(error); }
  finally { exportBusy = false; $("#btn-download").disabled = false; }
});
$("#btn-preview").addEventListener("click", async () => {
  if (!currentRecord || exportBusy) return;
  const kind = $("#export-kind").value;
  const format = $("#export-format").value;
  if (kind === "both") return;
  exportBusy = true;
  $("#export-error").classList.add("hidden");
  try {
    previewUrls.forEach((url) => URL.revokeObjectURL(url));
    previewUrls = [];
    $("#preview-pdf").classList.toggle("hidden", format !== "pdf");
    $("#preview-paper").classList.toggle("hidden", format === "pdf");
    const title = kind === "summary" ? "Clinical summary" : "Pseudonymised documents";
    $("#preview-title").textContent = title + " · " + format.toUpperCase();
    $("#preview-hint").textContent = format === "docx"
      ? "Word content preview. Download the .docx file to open it in Word." : "";
    if (format === "pdf") {
      $("#preview-pdf").replaceChildren();
      let pageCount = 1;
      for (let page = 1; page <= pageCount; page += 1) {
        const response = await fetch(exportPath(kind, "pdf") + "&preview=1&page=" + page, {cache: "no-store"});
        if (!response.ok) {
          const error = await response.json().catch(() => ({}));
          throw new Error(error.error || "Could not preview the PDF.");
        }
        pageCount = Number(response.headers.get("X-Page-Count")) || 1;
        const url = URL.createObjectURL(await response.blob());
        previewUrls.push(url);
        const pageImage = document.createElement("img");
        pageImage.src = url;
        pageImage.alt = "PDF page " + page + " of " + pageCount;
        $("#preview-pdf").append(pageImage);
      }
    } else {
      const blob = await fetchExport(kind, format === "docx" ? "txt" : format);
      const text = await blob.text();
      $("#preview-heading").textContent = format === "md" ? "" : title;
      $("#preview-body").textContent = format === "md" ? text : text.slice(text.indexOf("\n\n") + 2);
      $("#preview-paper").classList.toggle("plain-preview", format !== "docx");
    }
    $("#export-preview").showModal();
  } catch (error) { showExportError(error); }
  finally { exportBusy = false; }
});
$("#btn-close-preview").addEventListener("click", () => $("#export-preview").close());
$("#export-preview").addEventListener("close", () => {
  $("#preview-pdf").replaceChildren();
  previewUrls.forEach((url) => URL.revokeObjectURL(url));
  previewUrls = [];
});

/* ---------------- app updates ---------------- */

let updateState = {};
let updateNoticeRequested = false;
let dismissedUpdate = null;
let restartFromInstance = null;
let installStartedAt = 0;
let updatePollBusy = false;

function renderUpdate(state) {
  updateState = state;
  const wasInstalling = updateInstalling;
  updateInstalling = ["downloading", "installing", "restarting"].includes(state.phase);
  const checking = state.phase === "checking";
  $("#btn-check-updates").disabled = checking || updateInstalling;
  $("#update-check-label").textContent = checking ? "Checking…" :
    state.available ? "Update available" : state.phase === "error" ? "Check unavailable" : "Up to date";
  $("#btn-check-updates").title = state.message || "";
  const show = updateInstalling || updateNoticeRequested ||
    (state.available && dismissedUpdate !== state.latest);
  $("#update-toast").classList.toggle("hidden", !show);
  const titles = {
    downloading: "Downloading update",
    installing: "Installing update",
    restarting: "Restarting SmartDoc",
    checking: "Checking for updates",
    error: "Update could not be completed",
    current: "SmartDoc is up to date",
  };
  $("#update-title").textContent = titles[state.phase] || "An update is available";
  $("#update-message").textContent = state.message || "Checking for a newer version…";
  if (state.available && state.active_jobs > 0 && !updateInstalling)
    $("#update-message").textContent = "An update is available. You can install it when the current summary finishes.";
  $("#btn-install-update").classList.toggle("hidden", !state.available || updateInstalling || state.phase === "error");
  $("#btn-install-update").disabled = !state.can_install || state.active_jobs > 0 || checking;
  $("#btn-retry-update").classList.toggle("hidden", state.phase !== "error");
  $("#btn-dismiss-update").classList.toggle("hidden", updateInstalling);
  $("#btn-dismiss-update").textContent = state.available ? "Later" : "Close";
  if (wasInstalling !== updateInstalling) renderFiles();
  else updateSendEnabled();
}

async function refreshUpdates() {
  if (updatePollBusy) return;
  updatePollBusy = true;
  try {
    const state = await api("/api/updates");
    if (restartFromInstance && state.instance_id !== restartFromInstance) {
      location.reload();
      return;
    }
    if (state.phase === "restarting" && !restartFromInstance) {
      restartFromInstance = state.instance_id;
      installStartedAt = Date.now();
    }
    if (state.phase === "error" || state.phase === "current") {
      restartFromInstance = null;
      installStartedAt = 0;
    }
    renderUpdate(state);
  } catch {
    if (restartFromInstance) {
      if (Date.now() - installStartedAt > 120000 && installStartedAt) {
        renderUpdate({ phase: "error", message: "SmartDoc has not reconnected. Reopen it using the SmartDoc launcher; your saved summaries are kept." });
      } else {
        renderUpdate({ phase: "restarting", message: "Waiting for SmartDoc to restart…" });
      }
    }
  } finally {
    updatePollBusy = false;
  }
}

async function checkUpdates() {
  updateNoticeRequested = true;
  dismissedUpdate = null;
  renderUpdate({ ...updateState, phase: "checking", message: "Checking for a newer version…" });
  try {
    await api("/api/updates/check", { method: "POST",
      headers: { "Content-Type": "application/json" }, body: "{}" });
    await refreshUpdates();
  } catch (e) {
    renderUpdate({ phase: "error", message: e.message });
  }
}

$("#btn-check-updates").addEventListener("click", checkUpdates);
$("#btn-retry-update").addEventListener("click", checkUpdates);
$("#btn-dismiss-update").addEventListener("click", () => {
  dismissedUpdate = updateState.latest;
  updateNoticeRequested = false;
  $("#update-toast").classList.add("hidden");
});
$("#btn-install-update").addEventListener("click", async () => {
  if (updateInstalling) return;
  updateNoticeRequested = true;
  installStartedAt = Date.now();
  renderUpdate({ ...updateState, phase: "downloading", message: "Downloading the latest update…" });
  try {
    const result = await api("/api/updates/install", { method: "POST",
      headers: { "Content-Type": "application/json" }, body: "{}" });
    restartFromInstance = result.instance_id;
    await refreshUpdates();
  } catch (e) {
    restartFromInstance = null;
    renderUpdate({ phase: "error", message: e.message });
  }
});

/* ---------------- init ---------------- */

refreshStatus();
setInterval(refreshStatus, 15000);
refreshHistory();
showView("home");

refreshUpdates();
setInterval(refreshUpdates, 2000);

function refreshInputSettings() {
  const hints = {
    balanced: "Extra checks where needed.",
    thorough: "Cross-check every scan. Takes longer.",
    fast: "Quicker reading with fewer cross-checks."
  };
  $("#transcription-mode-hint").textContent = hints[$("#transcription-mode").value];
  const custom = $("#transcription-mode").value !== "balanced" || $("#additional-identifiers").value.trim().length > 0;
  $("#settings-indicator").classList.toggle("hidden", !custom);
  $("#input-settings > summary").title = custom ? "Processing settings · customised" : "Processing settings";
}
$("#transcription-mode").addEventListener("change", refreshInputSettings);
$("#additional-identifiers").addEventListener("input", refreshInputSettings);
document.querySelectorAll(".input-popover").forEach((panel) => {
  panel.addEventListener("toggle", () => {
    panel.querySelector("summary").setAttribute("aria-expanded", String(panel.open));
    if (panel.open) document.querySelectorAll(".input-popover").forEach((other) => {
      if (other !== panel) other.open = false;
    });
  });
});
document.addEventListener("click", (event) => {
  document.querySelectorAll(".input-popover[open]").forEach((panel) => {
    if (!panel.contains(event.target)) panel.open = false;
  });
});
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  document.querySelectorAll(".input-popover[open]").forEach((panel) => {
    panel.open = false;
    panel.querySelector("summary").focus();
  });
});

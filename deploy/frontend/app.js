// Brawlhalla TikTok Editor — frontend logic.
//
// Multi-job version: the upload form stays available so you can queue several
// clips at once. The server runs at most N pipelines in parallel (default 1)
// and keeps the rest in a waiting queue so the machine doesn't get thrashed.
// This file polls /api/jobs and renders every job as a card with its own
// progress bar, queue position and actions.

const els = {
  videoDrop: document.getElementById("video-drop"),
  videoInput: document.getElementById("video-input"),
  videoName: document.getElementById("video-name"),

  musicDrop: document.getElementById("music-drop"),
  musicInput: document.getElementById("music-input"),
  musicName: document.getElementById("music-name"),
  musicUrlInput: document.getElementById("music-url-input"),

  framing: document.getElementById("framing"),
  notes: document.getElementById("notes"),
  title: document.getElementById("title"),
  submit: document.getElementById("submit-btn"),

  jobsSection: document.getElementById("jobs-section"),
  jobsList: document.getElementById("jobs-list"),
  jobsMeta: document.getElementById("jobs-meta"),

  // Seed picker (tight mode) — lives in a modal now.
  seedModal: document.getElementById("seed-modal"),
  seedModalClose: document.getElementById("seed-modal-close"),
  seedImg: document.getElementById("seed-img"),
  seedCanvas: document.getElementById("seed-canvas"),
  seedCanvasWrap: document.getElementById("seed-canvas-wrap"),
  seedTimeSlider: document.getElementById("seed-time-slider"),
  seedTimeVal: document.getElementById("seed-time-val"),
  seedClear: document.getElementById("seed-clear"),
  seedSubmit: document.getElementById("seed-submit"),

  mascot: document.getElementById("mascot-img"),
};

// Random mascot on every load.
const MASCOT_COUNT = 7;
const mascotIdx = 1 + Math.floor(Math.random() * MASCOT_COUNT);
els.mascot.src = `/static/mascots/mascot-${String(mascotIdx).padStart(2, "0")}.png`;

let videoFiles = [];
let musicFiles = [];

function fmtBytes(n) {
  if (!n) return "";
  const units = ["B", "KB", "MB", "GB"];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(1)} ${units[i]}`;
}

/** Format a duration in seconds as "1m 23s" or "12.4s". */
function fmtDuration(s) {
  if (s == null || !isFinite(s)) return "—";
  if (s < 60) return `${s.toFixed(1)}s`;
  const m = Math.floor(s / 60);
  const r = s - m * 60;
  return `${m}m ${Math.round(r).toString().padStart(2, "0")}s`;
}

/** Friendly French label for each pipeline stage. */
const STAGE_LABELS = {
  queued: "File d'attente",
  starting: "Démarrage",
  discover: "Lecture des fichiers",
  audio_url: "Téléchargement musique",
  transcribe: "Transcription (Whisper)",
  await_seed: "En attente du seed Kaya",
  track: "Tracking",
  camera: "Plan caméra",
  audio: "Mix audio",
  composite: "Composite GPU (NVENC)",
  render: "Rendu Remotion",
  mux: "Remux MP4",
  done: "Terminé",
  error: "Erreur",
  cancelled: "Annulé",
};

// Stages that mean the job has stopped (no more progress to expect).
const TERMINAL_STAGES = new Set(["done", "error", "cancelled"]);

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// ---------------------------------------------------------------------------
// Upload form
// ---------------------------------------------------------------------------

function setupDropzone(zone, input, onFiles) {
  const handle = (files) => {
    const list = Array.from(files || []);
    if (!list.length) return;
    onFiles(list);
    zone.classList.add("has-file");
  };
  zone.addEventListener("click", () => input.click());
  input.addEventListener("change", (e) => handle(e.target.files));
  zone.addEventListener("dragover", (e) => {
    e.preventDefault();
    zone.classList.add("dragover");
  });
  zone.addEventListener("dragleave", () => zone.classList.remove("dragover"));
  zone.addEventListener("drop", (e) => {
    e.preventDefault();
    zone.classList.remove("dragover");
    handle(e.dataTransfer.files);
  });
}

setupDropzone(els.videoDrop, els.videoInput, (files) => {
  // Keep only video files; drop anything else silently.
  videoFiles = files.filter((f) => f.type.startsWith("video/") || /\.(mp4|mov|mkv|webm|avi)$/i.test(f.name));
  if (!videoFiles.length) return;
  if (videoFiles.length === 1) {
    els.videoName.textContent = `${videoFiles[0].name} · ${fmtBytes(videoFiles[0].size)}`;
  } else {
    const total = videoFiles.reduce((s, f) => s + f.size, 0);
    els.videoName.textContent = `${videoFiles.length} vidéos · ${fmtBytes(total)}`;
  }
  els.submit.disabled = false;
});
setupDropzone(els.musicDrop, els.musicInput, (files) => {
  musicFiles = files.filter((f) => f.type.startsWith("audio/") || /\.(mp3|wav|m4a|aac|ogg|flac|opus)$/i.test(f.name));
  if (!musicFiles.length) return;
  if (musicFiles.length === 1) {
    els.musicName.textContent = `${musicFiles[0].name} · ${fmtBytes(musicFiles[0].size)}`;
  } else {
    const total = musicFiles.reduce((s, f) => s + f.size, 0);
    els.musicName.textContent = `${musicFiles.length} sons · tirage aléatoire · ${fmtBytes(total)}`;
  }
  if (els.musicUrlInput) els.musicUrlInput.value = "";
});

els.musicUrlInput?.addEventListener("input", () => {
  if (els.musicUrlInput.value.trim()) {
    musicFiles = [];
    els.musicInput.value = "";
    els.musicName.textContent = "";
    els.musicDrop.classList.remove("has-file");
  }
});

function resetForm() {
  videoFiles = [];
  musicFiles = [];
  els.videoInput.value = "";
  els.musicInput.value = "";
  els.videoName.textContent = "";
  els.musicName.textContent = "";
  els.videoDrop.classList.remove("has-file");
  els.musicDrop.classList.remove("has-file");
  if (els.musicUrlInput) els.musicUrlInput.value = "";
  if (els.title) els.title.value = "";
  if (els.notes) els.notes.value = "";
  els.submit.disabled = true;
}

els.submit.addEventListener("click", async () => {
  if (!videoFiles.length) return;

  const musicUrl = (els.musicUrlInput && els.musicUrlInput.value.trim()) || "";
  const userTitle = (els.title && els.title.value.trim()) || "";
  const framing = els.framing.value;
  const notes = (els.notes && els.notes.value) || "";
  const multi = videoFiles.length > 1;

  els.submit.disabled = true;
  const original = els.submit.innerHTML;

  let failures = 0;
  for (let i = 0; i < videoFiles.length; i++) {
    const video = videoFiles[i];
    els.submit.innerHTML = `<span>Envoi ${i + 1}/${videoFiles.length}…</span>`;

    const fd = new FormData();
    fd.append("video", video);
    // Music: a pool of sounds → pick one at random per video. A single
    // sound is just always picked. A URL (if no files) applies to all.
    if (musicFiles.length) {
      const pick = musicFiles[Math.floor(Math.random() * musicFiles.length)];
      fd.append("music", pick);
    } else if (musicUrl) {
      fd.append("music_url", musicUrl);
    }
    fd.append("framing", framing);
    fd.append("notes", notes);
    // Per-clip title: number them when batching, else use the field (or let
    // the server fall back to the filename).
    const title = userTitle ? (multi ? `${userTitle} ${i + 1}` : userTitle) : "";
    fd.append("title", title);

    try {
      const res = await fetch("/api/upload", { method: "POST", body: fd });
      if (!res.ok) {
        let detail = "";
        try { detail = (await res.json()).detail || ""; } catch {}
        throw new Error(detail || `Upload échoué (${res.status})`);
      }
      await res.json();
      els.jobsSection.classList.remove("hidden");
      refreshJobs();
      startJobsPolling();
    } catch (err) {
      failures++;
      console.error(`Upload de ${video.name} échoué:`, err);
    }
  }

  els.submit.innerHTML = original;
  resetForm();
  if (failures) {
    alert(`${failures} vidéo(s) sur ${videoFiles.length || failures} n'ont pas pu être envoyées.`);
  }
});

// ---------------------------------------------------------------------------
// Jobs list — polls /api/jobs and renders one card per job.
// ---------------------------------------------------------------------------

let jobsPoll = null;
const jobCards = new Map();   // job_id -> { el, refs }
const jobUi = new Map();      // job_id -> { errorOpen, previewOpen }

function startJobsPolling() {
  if (jobsPoll) return;
  jobsPoll = setInterval(refreshJobs, 2000);
}

function stopJobsPolling() {
  if (jobsPoll) { clearInterval(jobsPoll); jobsPoll = null; }
}

async function refreshJobs() {
  let res, data;
  try {
    res = await fetch("/api/jobs", { cache: "no-store" });
  } catch {
    return; // network blip — keep what we have
  }
  if (res.status === 404) {
    // The running server predates the queue feature → it can't serve this
    // page's API. Tell the user to restart it.
    showStaleBanner("ancienne version (pas de file d'attente)");
    return;
  }
  if (!res.ok) return;
  try {
    data = await res.json();
  } catch {
    return;
  }
  // Version mismatch between this page and the running server.
  if (data.server_version && FRONTEND_VERSION && FRONTEND_VERSION !== "__VERSION__"
      && data.server_version !== FRONTEND_VERSION) {
    showStaleBanner(`page v${FRONTEND_VERSION} ≠ serveur v${data.server_version}`);
  }
  renderJobs(data);
}

const FRONTEND_VERSION =
  document.querySelector('meta[name="app-version"]')?.content || "";

function showStaleBanner(detail) {
  const b = document.getElementById("stale-banner");
  if (!b || !b.classList.contains("hidden")) return;
  const v = document.getElementById("stale-version");
  if (v) v.textContent = detail ? `· ${detail}` : "";
  b.classList.remove("hidden");
}
document.getElementById("stale-close")?.addEventListener("click", () => {
  document.getElementById("stale-banner")?.classList.add("hidden");
});

function renderJobs(data) {
  const jobs = data.jobs || [];

  if (jobs.length === 0) {
    els.jobsSection.classList.add("hidden");
  } else {
    els.jobsSection.classList.remove("hidden");
  }

  // Header meta: how many running / queued, and the concurrency cap.
  const running = data.running || 0;
  const queued = data.queued || 0;
  const cap = data.max_concurrent || 1;
  const bits = [];
  if (running) bits.push(`${running} en cours`);
  if (queued) bits.push(`${queued} en attente`);
  bits.push(`max ${cap} en parallèle`);
  if (data.server_version) bits.push(`v${data.server_version}`);
  els.jobsMeta.textContent = bits.join(" · ");

  const seen = new Set();
  for (const job of jobs) {
    seen.add(job.job_id);
    let entry = jobCards.get(job.job_id);
    if (!entry) {
      entry = createJobCard(job);
      jobCards.set(job.job_id, entry);
      els.jobsList.appendChild(entry.el);
    }
    updateJobCard(entry, job);
  }

  // Remove cards for jobs the server no longer knows about (deleted).
  for (const [jid, entry] of jobCards) {
    if (!seen.has(jid)) {
      entry.el.remove();
      jobCards.delete(jid);
      jobUi.delete(jid);
    }
  }

  // Stop polling once everything is in a terminal state (nothing will change).
  const anyLive = jobs.some((j) => !TERMINAL_STAGES.has(j.stage));
  if (!anyLive) stopJobsPolling();
}

function createJobCard(job) {
  const el = document.createElement("div");
  el.className = "job-card";
  el.dataset.job = job.job_id;
  el.innerHTML = `
    <div class="job-top">
      <div class="job-name"></div>
      <div class="job-badge"></div>
    </div>
    <div class="progress-bar"><div class="progress-fill"></div></div>
    <div class="job-info">
      <span class="job-msg"></span>
      <span class="job-elapsed"></span>
    </div>
    <div class="job-actions"></div>
    <div class="job-detail"></div>
  `;
  const refs = {
    name: el.querySelector(".job-name"),
    badge: el.querySelector(".job-badge"),
    fill: el.querySelector(".progress-fill"),
    msg: el.querySelector(".job-msg"),
    elapsed: el.querySelector(".job-elapsed"),
    actions: el.querySelector(".job-actions"),
    detail: el.querySelector(".job-detail"),
  };
  jobUi.set(job.job_id, { errorOpen: false, previewOpen: false });
  return { el, refs };
}

function updateJobCard(entry, job) {
  const { el, refs } = entry;
  const stage = job.stage || "queued";
  el.dataset.stage = stage;

  const name = job.title?.trim() || job.source_name || `Clip ${job.job_id.slice(0, 6)}`;
  refs.name.textContent = name;

  refs.badge.textContent = STAGE_LABELS[stage] || stage;
  refs.badge.className = `job-badge badge-${stage}`;

  const pct = Math.max(0, Math.min(100, Number(job.progress) || 0));
  refs.fill.style.width = `${pct}%`;

  // Primary status line.
  if (stage === "queued") {
    const pos = job.queue_position;
    refs.msg.textContent = pos
      ? `En attente · position ${pos}/${job.queue_total}`
      : "En attente…";
  } else {
    refs.msg.textContent = `${pct}% · ${job.message || "…"}`;
  }

  // Elapsed.
  const elapsed = job.total_elapsed ?? job.elapsed;
  refs.elapsed.textContent = (typeof elapsed === "number" && stage !== "queued")
    ? fmtDuration(elapsed) : "";

  renderJobActions(entry, job);
  renderJobDetail(entry, job);
}

function renderJobActions(entry, job) {
  const { refs } = entry;
  const stage = job.stage;
  refs.actions.innerHTML = "";

  const addBtn = (label, cls, onClick) => {
    const b = document.createElement("button");
    b.className = `job-btn ${cls}`;
    b.innerHTML = label;
    b.addEventListener("click", onClick);
    refs.actions.appendChild(b);
    return b;
  };

  if (stage === "queued") {
    const pos = job.queue_position || 0;
    if (pos > 1) addBtn("↑", "icon", () => moveJob(job.job_id, "up"));
    if (pos > 2) addBtn("⤒", "icon", () => moveJob(job.job_id, "top"));
    if (pos && job.queue_total && pos < job.queue_total) {
      addBtn("↓", "icon", () => moveJob(job.job_id, "down"));
    }
    addBtn("Annuler", "ghost", () => cancelJob(job.job_id));
  } else if (stage === "await_seed") {
    addBtn("✏️ Tracer Kaya", "primary", () => openSeedFor(job.job_id));
    addBtn("Annuler", "ghost", () => cancelJob(job.job_id));
  } else if (stage === "done") {
    const a = document.createElement("a");
    a.className = "job-btn primary";
    a.href = `/api/download/${job.job_id}`;
    a.download = "";
    a.innerHTML = "⬇ Télécharger";
    refs.actions.appendChild(a);
    const ui = jobUi.get(job.job_id);
    addBtn(ui.previewOpen ? "Masquer" : "▶ Aperçu", "ghost", () => {
      ui.previewOpen = !ui.previewOpen;
      renderJobActions(entry, job);
      renderJobDetail(entry, job);
    });
    addBtn("🗑", "icon", () => removeJob(job.job_id));
  } else if (stage === "error") {
    const ui = jobUi.get(job.job_id);
    addBtn(ui.errorOpen ? "Masquer l'erreur" : "Voir l'erreur", "ghost", () => {
      ui.errorOpen = !ui.errorOpen;
      renderJobActions(entry, job);
      renderJobDetail(entry, job);
    });
    addBtn("🗑", "icon", () => removeJob(job.job_id));
  } else if (stage === "cancelled") {
    addBtn("🗑", "icon", () => removeJob(job.job_id));
  } else {
    // Any active running stage (transcribe, track, camera, audio, render…).
    addBtn("Annuler", "ghost", () => cancelJob(job.job_id));
  }
}

async function moveJob(jobId, direction) {
  try {
    await fetch(`/api/jobs/${jobId}/move`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ direction }),
    });
  } catch {}
  refreshJobs();
}

function renderJobDetail(entry, job) {
  const { refs } = entry;
  const ui = jobUi.get(job.job_id);
  const stage = job.stage;

  // Done preview: build the <video> once, keep it alive across polls so it
  // doesn't reset while the user is watching.
  if (stage === "done" && ui.previewOpen) {
    if (!refs.detail.querySelector("video")) {
      refs.detail.innerHTML = "";
      const v = document.createElement("video");
      v.controls = true;
      v.playsInline = true;
      v.className = "job-video";
      v.src = `/api/download/${job.job_id}`;
      refs.detail.appendChild(v);
    }
    return;
  }

  if (stage === "error" && ui.errorOpen) {
    if (refs.detail.dataset.kind !== "error") {
      refs.detail.dataset.kind = "error";
      const pre = document.createElement("pre");
      pre.className = "error";
      pre.textContent = job.message || "Erreur inconnue";
      refs.detail.innerHTML = "";
      refs.detail.appendChild(pre);
    }
    return;
  }

  // Otherwise: empty detail.
  if (refs.detail.childNodes.length) {
    refs.detail.innerHTML = "";
    delete refs.detail.dataset.kind;
  }
}

async function cancelJob(jobId) {
  try {
    await fetch(`/api/cancel/${jobId}`, { method: "POST" });
  } catch {}
  refreshJobs();
}

async function removeJob(jobId) {
  try {
    await fetch(`/api/jobs/${jobId}`, { method: "DELETE" });
  } catch {}
  const entry = jobCards.get(jobId);
  if (entry) { entry.el.remove(); jobCards.delete(jobId); }
  jobUi.delete(jobId);
  refreshJobs();
}

// Kick things off: show whatever jobs the server already has.
refreshJobs().then(() => {
  if (jobCards.size) startJobsPolling();
});


// ---------------------------------------------------------------------------
// Seed picker — drag a bbox over a frame of the source video. Opens as a
// modal for whichever job is currently awaiting a seed.
// ---------------------------------------------------------------------------

let seedJobId = null;
let seedSrcW = 0, seedSrcH = 0;
let seedDefaultFrame = 0;
let seedFps = 60;
let seedBoxNorm = null;
let seedDragging = false;
let seedDragStart = null;

async function openSeedFor(jobId) {
  // Need the full status (src dims, seed frame, fps) — the list view omits it.
  try {
    const res = await fetch(`/api/status/${jobId}`, { cache: "no-store" });
    if (!res.ok) return;
    const st = await res.json();
    if (!st.await_seed) { refreshJobs(); return; }
    openSeedPicker(jobId, st);
  } catch {}
}

function openSeedPicker(jobId, status) {
  seedJobId = jobId;
  seedSrcW = Number(status.src_w) || Number(status.width) || 1920;
  seedSrcH = Number(status.src_h) || Number(status.height) || 1080;
  seedDefaultFrame = Number(status.seed_frame_idx) || 0;
  seedFps = Number(status.fps) || 60;
  seedBoxNorm = null;
  els.seedSubmit.disabled = true;

  const defaultT = Math.max(0, seedDefaultFrame / seedFps);
  els.seedTimeSlider.value = defaultT.toFixed(1);
  els.seedTimeVal.textContent = defaultT.toFixed(1);

  loadSeedFrame(defaultT);
  els.seedModal.classList.remove("hidden");
}

function closeSeedModal() {
  els.seedModal.classList.add("hidden");
  seedJobId = null;
}

els.seedModalClose?.addEventListener("click", closeSeedModal);
els.seedModal?.addEventListener("click", (e) => {
  if (e.target === els.seedModal) closeSeedModal();
});

async function loadSeedFrame(t) {
  if (!seedJobId) return;
  els.seedImg.src = `/api/seed_frame/${seedJobId}?t=${t.toFixed(3)}&_=${Date.now()}`;
}

els.seedTimeSlider?.addEventListener("input", (e) => {
  els.seedTimeVal.textContent = parseFloat(e.target.value).toFixed(1);
});
els.seedTimeSlider?.addEventListener("change", (e) => {
  loadSeedFrame(parseFloat(e.target.value));
  seedBoxNorm = null;
  redrawSeedCanvas();
  els.seedSubmit.disabled = true;
});

els.seedImg?.addEventListener("load", () => {
  resizeSeedCanvas();
  redrawSeedCanvas();
});
window.addEventListener("resize", () => {
  resizeSeedCanvas();
  redrawSeedCanvas();
});

function resizeSeedCanvas() {
  const img = els.seedImg;
  if (!img || !img.naturalWidth) return;
  const cv = els.seedCanvas;
  cv.width = img.clientWidth;
  cv.height = img.clientHeight;
}

function canvasPointToNorm(ev) {
  const rect = els.seedCanvas.getBoundingClientRect();
  const x = (ev.clientX - rect.left) / rect.width;
  const y = (ev.clientY - rect.top) / rect.height;
  return { x: Math.max(0, Math.min(1, x)), y: Math.max(0, Math.min(1, y)) };
}

els.seedCanvas?.addEventListener("pointerdown", (e) => {
  if (!els.seedImg.naturalWidth) return;
  e.preventDefault();
  els.seedCanvas.setPointerCapture(e.pointerId);
  seedDragging = true;
  seedDragStart = canvasPointToNorm(e);
  seedBoxNorm = { x: seedDragStart.x, y: seedDragStart.y, w: 0, h: 0 };
  redrawSeedCanvas();
});
els.seedCanvas?.addEventListener("pointermove", (e) => {
  if (!seedDragging) return;
  const cur = canvasPointToNorm(e);
  const x = Math.min(seedDragStart.x, cur.x);
  const y = Math.min(seedDragStart.y, cur.y);
  const w = Math.abs(cur.x - seedDragStart.x);
  const h = Math.abs(cur.y - seedDragStart.y);
  seedBoxNorm = { x, y, w, h };
  redrawSeedCanvas();
});
els.seedCanvas?.addEventListener("pointerup", () => {
  seedDragging = false;
  const valid = seedBoxNorm && seedBoxNorm.w > 0.02 && seedBoxNorm.h > 0.02;
  els.seedSubmit.disabled = !valid;
});

function redrawSeedCanvas() {
  const cv = els.seedCanvas;
  if (!cv) return;
  const ctx = cv.getContext("2d");
  ctx.clearRect(0, 0, cv.width, cv.height);
  if (!seedBoxNorm) return;
  const x = seedBoxNorm.x * cv.width;
  const y = seedBoxNorm.y * cv.height;
  const w = seedBoxNorm.w * cv.width;
  const h = seedBoxNorm.h * cv.height;
  ctx.fillStyle = "rgba(0, 0, 0, 0.45)";
  ctx.fillRect(0, 0, cv.width, cv.height);
  ctx.clearRect(x, y, w, h);
  ctx.lineWidth = 3;
  ctx.strokeStyle = "#ff7eb6";
  ctx.shadowColor = "rgba(255, 126, 182, 0.6)";
  ctx.shadowBlur = 12;
  ctx.strokeRect(x, y, w, h);
  ctx.shadowBlur = 0;
  ctx.fillStyle = "#ff7eb6";
  const k = 8;
  for (const [cx, cy] of [[x, y], [x + w, y], [x, y + h], [x + w, y + h]]) {
    ctx.beginPath();
    ctx.arc(cx, cy, k / 2, 0, Math.PI * 2);
    ctx.fill();
  }
}

els.seedClear?.addEventListener("click", () => {
  seedBoxNorm = null;
  redrawSeedCanvas();
  els.seedSubmit.disabled = true;
});

els.seedSubmit?.addEventListener("click", async () => {
  if (!seedBoxNorm || !seedJobId) return;
  const t = parseFloat(els.seedTimeSlider.value);
  const frame = Math.max(0, Math.round(t * seedFps));
  const x = Math.round(seedBoxNorm.x * seedSrcW);
  const y = Math.round(seedBoxNorm.y * seedSrcH);
  const w = Math.round(seedBoxNorm.w * seedSrcW);
  const h = Math.round(seedBoxNorm.h * seedSrcH);
  els.seedSubmit.disabled = true;
  const jid = seedJobId;
  try {
    const res = await fetch(`/api/seed/${jid}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ frame, x, y, w, h }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    closeSeedModal();
    refreshJobs();
    startJobsPolling();
  } catch (err) {
    els.seedSubmit.disabled = false;
    alert(`Erreur soumission seed: ${err.message}`);
  }
});


// ---------------------------------------------------------------------------
// Live system monitor (CPU / RAM / GPU / VRAM)
// ---------------------------------------------------------------------------

const sysmon = {
  root: document.getElementById("sysmon"),
  cpuBar: document.getElementById("sysmon-cpu-bar"),
  cpuVal: document.getElementById("sysmon-cpu-val"),
  ramBar: document.getElementById("sysmon-ram-bar"),
  ramVal: document.getElementById("sysmon-ram-val"),
  gpuRow: document.getElementById("sysmon-gpu-row"),
  gpuBar: document.getElementById("sysmon-gpu-bar"),
  gpuVal: document.getElementById("sysmon-gpu-val"),
  vramRow: document.getElementById("sysmon-vram-row"),
  vramBar: document.getElementById("sysmon-vram-bar"),
  vramVal: document.getElementById("sysmon-vram-val"),
  foot: document.getElementById("sysmon-foot"),
};

function fmtGB(bytes) {
  if (bytes == null) return "—";
  return `${(bytes / 1024 / 1024 / 1024).toFixed(1)} GB`;
}

function setSysmonBar(bar, valEl, pct, valText) {
  const p = pct == null ? 0 : Math.max(0, Math.min(100, pct));
  bar.style.width = `${p}%`;
  bar.classList.toggle("hot", p >= 80);
  valEl.textContent = valText;
}

async function refreshSysmon() {
  try {
    const res = await fetch("/api/sysinfo", { cache: "no-store" });
    if (!res.ok) return;
    const d = await res.json();

    setSysmonBar(sysmon.cpuBar, sysmon.cpuVal, d.cpu_pct, `${Math.round(d.cpu_pct)}%`);
    setSysmonBar(sysmon.ramBar, sysmon.ramVal, d.ram_pct,
      `${fmtGB(d.ram_used)} / ${fmtGB(d.ram_total)}`);

    if (d.gpu) {
      sysmon.gpuRow.style.display = "";
      sysmon.vramRow.style.display = "";
      if (d.gpu.util_pct == null) {
        setSysmonBar(sysmon.gpuBar, sysmon.gpuVal, null, "n/a");
      } else {
        setSysmonBar(sysmon.gpuBar, sysmon.gpuVal, d.gpu.util_pct, `${Math.round(d.gpu.util_pct)}%`);
      }
      setSysmonBar(sysmon.vramBar, sysmon.vramVal, d.gpu.mem_pct,
        `${fmtGB(d.gpu.mem_used)} / ${fmtGB(d.gpu.mem_total)}`);
      const tempStr = d.gpu.temp_c != null ? ` · ${d.gpu.temp_c}°C` : "";
      sysmon.foot.textContent = (sysmonHwLine || d.gpu.name) + tempStr;
    } else {
      sysmon.gpuRow.style.display = "none";
      sysmon.vramRow.style.display = "none";
      sysmon.foot.textContent = sysmonHwLine || `${d.cpu_count} threads · pas de GPU`;
    }
  } catch (_e) {
    // Silently ignore — server might be temporarily unreachable.
  }
}

let sysmonHwLine = "";
fetch("/api/hardware", { cache: "no-store" })
  .then(r => r.ok ? r.json() : null)
  .then(hw => {
    if (!hw) return;
    const parts = [];
    if (hw.cuda && hw.cuda_name) parts.push(hw.cuda_name);
    else if (hw.mps) parts.push("Apple Silicon");
    else parts.push("CPU only");
    parts.push(`enc: ${hw.encoder}`);
    sysmonHwLine = parts.join(" · ");
  })
  .catch(() => {});

refreshSysmon();
setInterval(refreshSysmon, 1500);


// ───────────────────────────────────────────────────────────────────────
// Self-update flow
// ───────────────────────────────────────────────────────────────────────

const updateEls = {
  banner: document.getElementById("update-banner"),
  version: document.getElementById("ub-version"),
  btnUpdate: document.getElementById("ub-update"),
  btnClose: document.getElementById("ub-close"),
  overlay: document.getElementById("update-overlay"),
  fill: document.getElementById("uo-fill"),
  msg: document.getElementById("uo-msg"),
  title: document.getElementById("uo-title"),
};

const UPDATE_DISMISSED_KEY = "brawlhalla:update-dismissed";

async function checkForUpdates() {
  try {
    const r = await fetch("/api/version");
    if (!r.ok) return;
    const v = await r.json();
    if (!v || !v.update_available) return;
    if (localStorage.getItem(UPDATE_DISMISSED_KEY) === v.latest) return;
    updateEls.version.textContent = `v${v.current} → v${v.latest}`;
    updateEls.banner.dataset.latest = v.latest;
    updateEls.banner.classList.remove("hidden");
  } catch {
    // network or local server not reachable — ignore
  }
}

updateEls.btnClose.addEventListener("click", () => {
  const latest = updateEls.banner.dataset.latest;
  if (latest) localStorage.setItem(UPDATE_DISMISSED_KEY, latest);
  updateEls.banner.classList.add("hidden");
});

updateEls.btnUpdate.addEventListener("click", async () => {
  updateEls.banner.classList.add("hidden");
  updateEls.overlay.classList.remove("hidden");
  updateEls.title.textContent = "Mise à jour en cours…";
  updateEls.msg.textContent = "Démarrage…";
  updateEls.fill.style.width = "0%";

  try {
    const r = await fetch("/api/update", { method: "POST" });
    if (!r.ok && r.status !== 409) {
      throw new Error(`HTTP ${r.status}`);
    }
  } catch (e) {
    updateEls.title.textContent = "Échec";
    updateEls.msg.textContent = e.message || String(e);
    return;
  }

  const poll = setInterval(async () => {
    try {
      const r = await fetch("/api/update/status");
      if (!r.ok) return;
      const st = await r.json();
      updateEls.fill.style.width = `${Math.max(0, Math.min(100, st.progress || 0))}%`;
      updateEls.msg.textContent = st.message || "…";

      if (st.stage === "done") {
        updateEls.title.textContent = "Redémarrage…";
        clearInterval(poll);
        waitForServerThenReload();
      } else if (st.stage === "error") {
        clearInterval(poll);
        updateEls.title.textContent = "Échec";
        updateEls.msg.textContent = st.error || st.message || "Erreur inconnue";
      }
    } catch {
      // Server is restarting → polls will fail briefly.
    }
  }, 1000);
});

async function waitForServerThenReload() {
  const deadline = Date.now() + 60000;
  while (Date.now() < deadline) {
    try {
      const r = await fetch("/api/version", { cache: "no-store" });
      if (r.ok) {
        location.reload();
        return;
      }
    } catch {
      // not yet
    }
    await new Promise((r) => setTimeout(r, 1000));
  }
  location.reload();
}

checkForUpdates();
setInterval(checkForUpdates, 60 * 60 * 1000);

// Brawlhalla TikTok Editor — frontend logic.

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
  submit: document.getElementById("submit-btn"),

  stepUpload: document.getElementById("step-upload"),
  stepProgress: document.getElementById("step-progress"),
  stepSeed: document.getElementById("step-seed"),
  stepDone: document.getElementById("step-done"),
  stepError: document.getElementById("step-error"),

  progressFill: document.getElementById("progress-fill"),
  progressText: document.getElementById("progress-text"),
  progressStage: document.getElementById("progress-stage"),
  progressElapsed: document.getElementById("progress-elapsed"),

  resultVideo: document.getElementById("result-video"),
  downloadLink: document.getElementById("download-link"),
  restartBtn: document.getElementById("restart-btn"),
  timingSummary: document.getElementById("timing-summary"),

  // Seed picker (tight mode).
  seedImg: document.getElementById("seed-img"),
  seedCanvas: document.getElementById("seed-canvas"),
  seedCanvasWrap: document.getElementById("seed-canvas-wrap"),
  seedTimeSlider: document.getElementById("seed-time-slider"),
  seedTimeVal: document.getElementById("seed-time-val"),
  seedClear: document.getElementById("seed-clear"),
  seedSubmit: document.getElementById("seed-submit"),

  errorText: document.getElementById("error-text"),
  errorRetry: document.getElementById("error-retry"),

  mascot: document.getElementById("mascot-img"),
};

// Random mascot on every load.
const MASCOT_COUNT = 7;
const idx = 1 + Math.floor(Math.random() * MASCOT_COUNT);
els.mascot.src = `/static/mascots/mascot-${String(idx).padStart(2, "0")}.png`;

let videoFile = null;
let musicFile = null;
let pollTimer = null;

const ACTIVE_JOB_KEY = "brawlhalla:active-job";

function fmtBytes(n) {
  if (!n) return "";
  const units = ["B", "KB", "MB", "GB"];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(1)} ${units[i]}`;
}

/**
 * Format a duration in seconds as "1m 23s" or "12.4s".
 * Compact for live counters, readable for the final summary.
 */
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
};

function setupDropzone(zone, input, onFile) {
  const handle = (file) => {
    onFile(file);
    zone.classList.add("has-file");
  };
  zone.addEventListener("click", () => input.click());
  input.addEventListener("change", (e) => {
    const f = e.target.files[0];
    if (f) handle(f);
  });
  zone.addEventListener("dragover", (e) => {
    e.preventDefault();
    zone.classList.add("dragover");
  });
  zone.addEventListener("dragleave", () => zone.classList.remove("dragover"));
  zone.addEventListener("drop", (e) => {
    e.preventDefault();
    zone.classList.remove("dragover");
    const f = e.dataTransfer.files[0];
    if (f) handle(f);
  });
}

setupDropzone(els.videoDrop, els.videoInput, (f) => {
  videoFile = f;
  els.videoName.textContent = `${f.name} · ${fmtBytes(f.size)}`;
  els.submit.disabled = false;
});
setupDropzone(els.musicDrop, els.musicInput, (f) => {
  musicFile = f;
  els.musicName.textContent = `${f.name} · ${fmtBytes(f.size)}`;
  // Uploading a file overrides any URL the user typed before.
  if (els.musicUrlInput) els.musicUrlInput.value = "";
});

// Typing a URL clears any uploaded file (the two are mutually exclusive).
els.musicUrlInput?.addEventListener("input", () => {
  if (els.musicUrlInput.value.trim()) {
    musicFile = null;
    els.musicInput.value = "";
    els.musicName.textContent = "";
    els.musicDrop.classList.remove("has-file");
  }
});

els.submit.addEventListener("click", async () => {
  if (!videoFile) return;
  const fd = new FormData();
  fd.append("video", videoFile);
  if (musicFile) {
    fd.append("music", musicFile);
  } else if (els.musicUrlInput && els.musicUrlInput.value.trim()) {
    fd.append("music_url", els.musicUrlInput.value.trim());
  }
  fd.append("framing", els.framing.value);
  fd.append("notes", els.notes.value || "");

  goTo("progress");
  const usingUrl = !musicFile && els.musicUrlInput && els.musicUrlInput.value.trim();
  setProgress(2, usingUrl ? "Téléchargement de la musique…" : "Téléversement…");

  try {
    const res = await fetch("/api/upload", { method: "POST", body: fd });
    if (!res.ok) {
      // Try to surface the server's error message (HTTP 400 from yt-dlp etc.).
      let detail = "";
      try { detail = (await res.json()).detail || ""; } catch {}
      throw new Error(detail || `Upload échoué (${res.status})`);
    }
    const { job_id } = await res.json();
    localStorage.setItem(ACTIVE_JOB_KEY, job_id);
    pollStatus(job_id);
  } catch (err) {
    showError(err.message || String(err));
  }
});

function pollStatus(jobId) {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    try {
      const res = await fetch(`/api/status/${jobId}`);
      if (!res.ok) return;
      const st = await res.json();
      const pct = Math.max(0, Math.min(100, Number(st.progress) || 0));
      setProgress(pct, st.message || "…");
      els.progressStage.textContent = st.stage ? `Étape: ${STAGE_LABELS[st.stage] || st.stage}` : "";
      if (typeof st.elapsed === "number") {
        els.progressElapsed.textContent = `Temps écoulé: ${fmtDuration(st.elapsed)}`;
      }
      if (st.await_seed) {
        // Pipeline is paused waiting for the user to draw a bbox. Switch
        // to the picker UI; pollStatus stays alive so we can flip back to
        // progress as soon as the seed is accepted.
        if (currentStep !== "seed") {
          openSeedPicker(jobId, st);
        }
      } else if (currentStep === "seed") {
        // Seed accepted — back to progress display.
        goTo("progress");
      }
      if (st.stage === "done") {
        clearInterval(pollTimer); pollTimer = null;
        localStorage.removeItem(ACTIVE_JOB_KEY);
        showDone(jobId, st);
      } else if (st.stage === "error") {
        clearInterval(pollTimer); pollTimer = null;
        localStorage.removeItem(ACTIVE_JOB_KEY);
        showError(st.message || "Erreur inconnue");
      }
    } catch (e) {
      // network blip — keep polling
    }
  }, 2000);
}

function setProgress(pct, msg) {
  els.progressFill.style.width = `${pct}%`;
  els.progressText.textContent = `${pct}% · ${msg}`;
}

function showDone(jobId, status) {
  const url = `/api/download/${jobId}`;
  els.resultVideo.src = url;
  els.downloadLink.href = url;
  renderTimingSummary(status);
  goTo("done");
}

/** Render the per-stage breakdown card on the "done" screen. */
function renderTimingSummary(status) {
  const total = (status && (status.total_elapsed ?? status.elapsed)) || 0;
  const stageTimes = (status && status.stage_times) || {};

  // Order stages in the order they typically run; skip stages that took 0.
  const order = ["discover", "transcribe", "track", "camera", "audio", "composite", "render", "mux"];
  const rows = [];
  for (const k of order) {
    const v = stageTimes[k];
    if (typeof v === "number" && v > 0.05) {
      rows.push({ stage: k, secs: v });
    }
  }
  // Anything else (custom keys), append.
  for (const [k, v] of Object.entries(stageTimes)) {
    if (!order.includes(k) && typeof v === "number" && v > 0.05) {
      rows.push({ stage: k, secs: v });
    }
  }

  // Build the DOM.
  const safeTotal = total > 0 ? total : 1;
  const rowsHtml = rows.map(r => {
    const pct = Math.max(2, Math.min(100, (r.secs / safeTotal) * 100));
    const label = STAGE_LABELS[r.stage] || r.stage;
    return `
      <div class="timing-row">
        <div class="timing-label">${label}</div>
        <div class="timing-track"><div class="timing-fill" style="width:${pct.toFixed(1)}%"></div></div>
        <div class="timing-val">${fmtDuration(r.secs)}</div>
      </div>
    `;
  }).join("");

  els.timingSummary.innerHTML = `
    <div class="timing-total">
      <span class="timing-total-label">Temps total</span>
      <span class="timing-total-val">${fmtDuration(total)}</span>
    </div>
    <div class="timing-rows">${rowsHtml}</div>
  `;
}

function showError(msg) {
  els.errorText.textContent = msg;
  goTo("error");
}

let currentStep = "upload";

function goTo(step) {
  currentStep = step;
  for (const s of ["upload", "progress", "seed", "done", "error"]) {
    const el = els[`step${s.charAt(0).toUpperCase()}${s.slice(1)}`];
    if (s === step) el.classList.remove("hidden");
    else el.classList.add("hidden");
  }
}

els.restartBtn.addEventListener("click", () => {
  videoFile = null;
  musicFile = null;
  els.videoInput.value = "";
  els.musicInput.value = "";
  els.videoName.textContent = "";
  els.musicName.textContent = "";
  els.videoDrop.classList.remove("has-file");
  els.musicDrop.classList.remove("has-file");
  if (els.musicUrlInput) els.musicUrlInput.value = "";
  els.notes.value = "";
  els.submit.disabled = true;
  goTo("upload");
});

els.errorRetry.addEventListener("click", () => goTo("upload"));

// On page load, resume any active job from localStorage.
const resumeId = localStorage.getItem(ACTIVE_JOB_KEY);
if (resumeId) {
  // Quick check: does the job still exist on the server?
  fetch(`/api/status/${resumeId}`).then((res) => {
    if (res.ok) {
      goTo("progress");
      setProgress(0, "Reprise du job en cours…");
      pollStatus(resumeId);
    } else {
      localStorage.removeItem(ACTIVE_JOB_KEY);
    }
  }).catch(() => {
    // network issue — leave the user on the upload screen
  });
}


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

    setSysmonBar(
      sysmon.cpuBar, sysmon.cpuVal,
      d.cpu_pct,
      `${Math.round(d.cpu_pct)}%`
    );
    setSysmonBar(
      sysmon.ramBar, sysmon.ramVal,
      d.ram_pct,
      `${fmtGB(d.ram_used)} / ${fmtGB(d.ram_total)}`
    );

    if (d.gpu) {
      sysmon.gpuRow.style.display = "";
      sysmon.vramRow.style.display = "";
      if (d.gpu.util_pct == null) {
        // Fallback (torch) — no util available.
        setSysmonBar(sysmon.gpuBar, sysmon.gpuVal, null, "n/a");
      } else {
        setSysmonBar(
          sysmon.gpuBar, sysmon.gpuVal,
          d.gpu.util_pct,
          `${Math.round(d.gpu.util_pct)}%`
        );
      }
      setSysmonBar(
        sysmon.vramBar, sysmon.vramVal,
        d.gpu.mem_pct,
        `${fmtGB(d.gpu.mem_used)} / ${fmtGB(d.gpu.mem_total)}`
      );
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

// One-shot fetch of the static hardware capabilities (encoder name, etc.) so
// the user sees what backend the pipeline is actually using on their box.
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

// Kick off immediately, then poll.
refreshSysmon();
setInterval(refreshSysmon, 1500);


// ---------------------------------------------------------------------------
// Seed picker — drag a bbox over a frame of the source video. The pixel
// coordinates are sent to the server in source-resolution space.
// ---------------------------------------------------------------------------

let seedJobId = null;
let seedSrcW = 0, seedSrcH = 0;
let seedDefaultFrame = 0;
let seedFps = 60;
let seedBoxNorm = null;  // {x, y, w, h} in [0,1] image-relative coords
let seedDragging = false;
let seedDragStart = null;

function openSeedPicker(jobId, status) {
  seedJobId = jobId;
  seedSrcW = Number(status.src_w) || Number(status.width) || 1920;
  seedSrcH = Number(status.src_h) || Number(status.height) || 1080;
  seedDefaultFrame = Number(status.seed_frame_idx) || 0;
  seedFps = Number(status.fps) || 60;
  seedBoxNorm = null;
  els.seedSubmit.disabled = true;

  // Slider range = first 20s of the video, default = the auto seed timestamp.
  const defaultT = Math.max(0, seedDefaultFrame / seedFps);
  els.seedTimeSlider.value = defaultT.toFixed(1);
  els.seedTimeVal.textContent = defaultT.toFixed(1);

  loadSeedFrame(defaultT);
  goTo("seed");
}

async function loadSeedFrame(t) {
  if (!seedJobId) return;
  // Cache-bust to make sure we re-fetch on every slider step.
  els.seedImg.src = `/api/seed_frame/${seedJobId}?t=${t.toFixed(3)}&_=${Date.now()}`;
}

els.seedTimeSlider?.addEventListener("input", (e) => {
  const t = parseFloat(e.target.value);
  els.seedTimeVal.textContent = t.toFixed(1);
});
els.seedTimeSlider?.addEventListener("change", (e) => {
  const t = parseFloat(e.target.value);
  loadSeedFrame(t);
  // Clear any existing bbox — it was on a different frame.
  seedBoxNorm = null;
  redrawSeedCanvas();
  els.seedSubmit.disabled = true;
});

// Resize canvas to match the displayed image whenever it loads.
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
  // Any bbox bigger than 2% of the frame in both dims is valid enough.
  const valid = seedBoxNorm && seedBoxNorm.w > 0.02 && seedBoxNorm.h > 0.02;
  els.seedSubmit.disabled = !valid;
});

function redrawSeedCanvas() {
  const cv = els.seedCanvas;
  const ctx = cv.getContext("2d");
  ctx.clearRect(0, 0, cv.width, cv.height);
  if (!seedBoxNorm) return;
  const x = seedBoxNorm.x * cv.width;
  const y = seedBoxNorm.y * cv.height;
  const w = seedBoxNorm.w * cv.width;
  const h = seedBoxNorm.h * cv.height;
  // Translucent overlay outside the box.
  ctx.fillStyle = "rgba(0, 0, 0, 0.45)";
  ctx.fillRect(0, 0, cv.width, cv.height);
  ctx.clearRect(x, y, w, h);
  // Bright pink outline.
  ctx.lineWidth = 3;
  ctx.strokeStyle = "#ff7eb6";
  ctx.shadowColor = "rgba(255, 126, 182, 0.6)";
  ctx.shadowBlur = 12;
  ctx.strokeRect(x, y, w, h);
  ctx.shadowBlur = 0;
  // Corner ticks.
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
  try {
    const res = await fetch(`/api/seed/${seedJobId}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ frame, x, y, w, h }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
  } catch (err) {
    els.seedSubmit.disabled = false;
    showError(`Erreur soumission seed: ${err.message}`);
  }
});


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

  // Poll the update status until it's done or errors out.
  let serverWentDown = false;
  const poll = setInterval(async () => {
    try {
      const r = await fetch("/api/update/status");
      if (!r.ok) return;
      const st = await r.json();
      updateEls.fill.style.width = `${Math.max(0, Math.min(100, st.progress || 0))}%`;
      updateEls.msg.textContent = st.message || "…";

      if (st.stage === "done") {
        updateEls.title.textContent = "Redémarrage…";
        // Wait for the server to come back, then reload the page.
        clearInterval(poll);
        waitForServerThenReload();
      } else if (st.stage === "error") {
        clearInterval(poll);
        updateEls.title.textContent = "Échec";
        updateEls.msg.textContent = st.error || st.message || "Erreur inconnue";
      }
    } catch {
      // Server is restarting → polls will fail briefly. Mark it and let the
      // wait-for-comeback logic handle it.
      serverWentDown = true;
    }
  }, 1000);
});

async function waitForServerThenReload() {
  // Server exits with code 75 → launcher restarts it. Usually back in <5s.
  // Try up to 60s, then fall back to a hard reload.
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

// Run the update check on load + once an hour after that.
checkForUpdates();
setInterval(checkForUpdates, 60 * 60 * 1000);

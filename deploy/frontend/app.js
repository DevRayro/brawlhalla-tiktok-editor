// Brawlhalla TikTok Editor — frontend logic.

const els = {
  videoDrop: document.getElementById("video-drop"),
  videoInput: document.getElementById("video-input"),
  videoName: document.getElementById("video-name"),

  musicDrop: document.getElementById("music-drop"),
  musicInput: document.getElementById("music-input"),
  musicName: document.getElementById("music-name"),

  framing: document.getElementById("framing"),
  notes: document.getElementById("notes"),
  submit: document.getElementById("submit-btn"),

  stepUpload: document.getElementById("step-upload"),
  stepProgress: document.getElementById("step-progress"),
  stepDone: document.getElementById("step-done"),
  stepError: document.getElementById("step-error"),

  progressFill: document.getElementById("progress-fill"),
  progressText: document.getElementById("progress-text"),
  progressStage: document.getElementById("progress-stage"),

  resultVideo: document.getElementById("result-video"),
  downloadLink: document.getElementById("download-link"),
  restartBtn: document.getElementById("restart-btn"),

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
});

els.submit.addEventListener("click", async () => {
  if (!videoFile) return;
  const fd = new FormData();
  fd.append("video", videoFile);
  if (musicFile) fd.append("music", musicFile);
  fd.append("framing", els.framing.value);
  fd.append("notes", els.notes.value || "");

  goTo("progress");
  setProgress(2, "Téléversement…");

  try {
    const res = await fetch("/api/upload", { method: "POST", body: fd });
    if (!res.ok) throw new Error(`Upload échoué (${res.status})`);
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
      els.progressStage.textContent = st.stage ? `Étape: ${st.stage}` : "";
      if (st.stage === "done") {
        clearInterval(pollTimer); pollTimer = null;
        localStorage.removeItem(ACTIVE_JOB_KEY);
        showDone(jobId);
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

function showDone(jobId) {
  const url = `/api/download/${jobId}`;
  els.resultVideo.src = url;
  els.downloadLink.href = url;
  goTo("done");
}

function showError(msg) {
  els.errorText.textContent = msg;
  goTo("error");
}

function goTo(step) {
  for (const s of ["upload", "progress", "done", "error"]) {
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

"""First-run setup GUI (cross-platform).

Shown the first time the user launches the bundled app. Pipes the platform
setup script's stdout into a small Tkinter window so the user sees progress
during the ~10-15 minute torch / Whisper / Remotion download.

We use Tkinter because it ships with every CPython distribution; no extra
dependencies needed at install time.
"""
from __future__ import annotations

import os
import platform
import queue
import subprocess
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import ttk

APP_NAME = "Brawlhalla TikTok Editor"
ROOT_DIR = Path(__file__).resolve().parent.parent.parent  # installers/shared/.. = repo root


def _setup_script() -> list[str]:
    """Pick the right setup invocation for the current OS."""
    system = platform.system().lower()
    if system.startswith("win"):
        return ["cmd", "/c", str(ROOT_DIR / "setup.bat")]
    return ["bash", str(ROOT_DIR / "setup.sh")]


class FirstRunWindow(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"{APP_NAME} — première installation")
        self.geometry("680x420")
        self.minsize(540, 320)
        self.configure(bg="#fff5fb")

        # Header.
        header = tk.Frame(self, bg="#fff5fb")
        header.pack(fill=tk.X, padx=20, pady=(20, 8))
        title_font = tkfont.Font(family="Segoe UI", size=14, weight="bold")
        tk.Label(
            header, text="Premier lancement — installation des modèles",
            font=title_font, bg="#fff5fb", fg="#4a2a4a",
        ).pack(anchor="w")
        tk.Label(
            header,
            text=("On télécharge ~5 Go (Whisper, torch CUDA, Remotion, SAM2). "
                  "C'est uniquement la première fois — ça prend 10 à 20 minutes "
                  "selon ta connexion."),
            wraplength=620, justify="left",
            bg="#fff5fb", fg="#8a6080", font=("Segoe UI", 9),
        ).pack(anchor="w", pady=(6, 0))

        # Live status line + indeterminate progress bar (we don't get exact
        # percentages from pip, so we just spin).
        self.status_var = tk.StringVar(value="Préparation…")
        tk.Label(
            self, textvariable=self.status_var,
            bg="#fff5fb", fg="#4a2a4a", font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w", padx=20, pady=(8, 4))

        self.progress = ttk.Progressbar(self, mode="indeterminate", length=600)
        self.progress.pack(fill=tk.X, padx=20)
        self.progress.start(12)

        # Scrollable log box.
        log_frame = tk.Frame(self, bg="#fff5fb")
        log_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=(12, 12))
        self.log = tk.Text(
            log_frame, height=12, bg="#ffffff", fg="#4a2a4a",
            font=("Consolas", 9), wrap="none",
            relief="solid", borderwidth=1,
        )
        scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set, state="disabled")
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Footer button (hidden until done).
        self.done_btn = tk.Button(
            self, text="Lancer l'éditeur",
            bg="#ff7eb6", fg="white", font=("Segoe UI", 10, "bold"),
            activebackground="#c9a0ff", relief="flat", padx=18, pady=8,
            command=self._launch_app,
        )

        self.queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self.proc: subprocess.Popen | None = None
        self.failed = False

        self.after(120, self._drain_queue)
        threading.Thread(target=self._run_setup, daemon=True).start()

    # ---- worker thread ------------------------------------------------------

    def _run_setup(self) -> None:
        cmd = _setup_script()
        self.queue.put(("status", "Installation des dépendances Python…"))
        try:
            self.proc = subprocess.Popen(
                cmd,
                cwd=str(ROOT_DIR),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                bufsize=1, text=True, encoding="utf-8", errors="replace",
            )
            assert self.proc.stdout is not None
            for line in self.proc.stdout:
                line = line.rstrip()
                if not line:
                    continue
                self.queue.put(("log", line))
                # Crude status updates based on script section markers.
                low = line.lower()
                if "torch" in low and "installing" in low:
                    self.queue.put(("status", "Téléchargement de PyTorch…"))
                elif "whisper" in low:
                    self.queue.put(("status", "Modèle Whisper…"))
                elif "sam2" in low or "sam-2" in low:
                    self.queue.put(("status", "Tracking SAM2…"))
                elif "remotion" in low or "npm install" in low:
                    self.queue.put(("status", "Remotion (rendu vidéo)…"))
            rc = self.proc.wait()
            if rc != 0:
                self.failed = True
                self.queue.put(("status", f"Erreur (code {rc})"))
                self.queue.put(("done-error", ""))
            else:
                self.queue.put(("status", "Installation terminée."))
                self.queue.put(("done-ok", ""))
        except Exception as e:
            self.failed = True
            self.queue.put(("log", f"[install] Exception: {e}"))
            self.queue.put(("done-error", ""))

    # ---- UI thread ----------------------------------------------------------

    def _drain_queue(self) -> None:
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "log":
                    self.log.configure(state="normal")
                    self.log.insert(tk.END, payload + "\n")
                    self.log.see(tk.END)
                    self.log.configure(state="disabled")
                elif kind == "status":
                    self.status_var.set(payload)
                elif kind == "done-ok":
                    self.progress.stop()
                    self.progress["mode"] = "determinate"
                    self.progress["value"] = 100
                    self.done_btn.pack(pady=(0, 18))
                elif kind == "done-error":
                    self.progress.stop()
                    self.status_var.set("Échec de l'installation. Voir le log ci-dessous.")
                    self.done_btn.configure(text="Fermer", command=self.destroy)
                    self.done_btn.pack(pady=(0, 18))
        except queue.Empty:
            pass
        self.after(120, self._drain_queue)

    def _launch_app(self) -> None:
        # Mark the install as complete so future launches skip this GUI.
        marker = ROOT_DIR / ".install-complete"
        marker.write_text("ok", encoding="utf-8")
        # Close this window; the caller (start.bat / start.sh / start.command)
        # continues with the regular launch flow afterward.
        self.destroy()


def main() -> int:
    # Skip if already installed.
    marker = ROOT_DIR / ".install-complete"
    if marker.exists():
        return 0
    # If the venv already exists and the SAM2 model is there, write the marker
    # and skip — the user probably installed manually before running this.
    if (ROOT_DIR / ".venv").exists() and (ROOT_DIR / "models" / "sam2.1_hiera_small.pt").exists():
        marker.write_text("ok", encoding="utf-8")
        return 0
    win = FirstRunWindow()
    win.mainloop()
    return 0 if (ROOT_DIR / ".install-complete").exists() else 1


if __name__ == "__main__":
    sys.exit(main())

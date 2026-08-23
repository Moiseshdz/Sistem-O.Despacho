import os
import re
import sys
import json
import time
import queue
import shutil
import ctypes
import ctypes.wintypes
import threading
from pathlib import Path
from datetime import datetime
import tkinter as tk
from tkinter import filedialog, messagebox

import mss
from PIL import Image, ImageTk, ImageOps, ImageEnhance, ImageFilter
import pytesseract

# --- DPI awareness: keeps mouse/region coordinates aligned with physical pixels on Windows 10/11.
if os.name == "nt":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor aware
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

APP = "PROMAD Capturador en Vivo + Nota"
VER = "3.0.0"

BG = "#07101d"
PANEL = "#101d31"
CARD = "#172843"
TEXT = "#edf6ff"
MUTED = "#8ca1ba"
CYAN = "#00c8e8"
GREEN = "#28c26f"
YELLOW = "#f0c43c"
RED = "#ff4d57"
BLUE = "#2f82ff"

ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
DATA = Path(os.getenv("LOCALAPPDATA", Path.home())) / "PROMAD_Capturador"
CAP = DATA / "capturas"
DB = DATA / "incidentes.json"
CFG = DATA / "config_v3.json"
for d in (DATA, CAP):
    d.mkdir(parents=True, exist_ok=True)


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def clean(v):
    return re.sub(r"\s+", " ", v or "").strip()


def tesseract_exe():
    candidates = [
        ROOT / "tesseract" / "tesseract.exe",
        Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    return shutil.which("tesseract") or ""


def virtual_screen():
    """Return physical-pixel virtual desktop bounds from MSS."""
    with mss.mss() as sct:
        mon = sct.monitors[0]
        return {
            "left": int(mon["left"]),
            "top": int(mon["top"]),
            "width": int(mon["width"]),
            "height": int(mon["height"]),
        }


def clamp_region(region):
    if not region:
        return None
    v = virtual_screen()
    left = max(v["left"], int(region.get("left", v["left"])))
    top = max(v["top"], int(region.get("top", v["top"])))
    right = min(v["left"] + v["width"], int(region.get("left", left)) + int(region.get("width", 0)))
    bottom = min(v["top"] + v["height"], int(region.get("top", top)) + int(region.get("height", 0)))
    if right - left < 30 or bottom - top < 30:
        return None
    return {"left": left, "top": top, "width": right - left, "height": bottom - top}


def grab_region_once(region):
    region = clamp_region(region)
    if not region:
        return None
    with mss.mss() as sct:
        shot = sct.grab(region)
        return Image.frombytes("RGB", (shot.width, shot.height), shot.rgb)


def parse_promad(text):
    raw = (text or "").replace("\\", "/").replace("—", "-").replace("–", "-")
    lines = [clean(x) for x in raw.splitlines() if clean(x)]
    joined = "\n".join(lines)

    out = {
        "folio": "",
        "municipio": "",
        "tipificacion": "",
        "hora": "",
        "direccion": "",
        "colonia": "",
        "corporacion": "",
        "unidad": "",
        "narrativa": "",
        "capturado_en": now_iso(),
        "ocr": joined,
    }

    folio_i = -1
    folio_end = None
    folio_line = ""
    folio_rx = re.compile(
        r"(?:C\s*[S5]|CS|C5|G5|S5)\s*[/I1\-]?\s*([0-9]{8})\s*[/I1\-]?\s*([0-9]{4,8})",
        re.I,
    )
    for i, line in enumerate(lines):
        normalized = line.upper().replace("O", "0")
        m = folio_rx.search(normalized)
        if m:
            out["folio"] = f"C5/{m.group(1)}/{m.group(2)}"
            folio_i = i
            folio_end = m.end()
            folio_line = line
            break

    tm = re.search(r"(?<!\d)([0-2]?\d:[0-5]\d(?::[0-5]\d)?)(?!\d)", joined)
    if tm:
        out["hora"] = tm.group(1)

    incident_words = (
        "ACCIDENTE", "ROBO", "VIOLENCIA", "INCENDIO", "PERSONA", "VEHICULO", "VEHÍCULO",
        "DETONACION", "DETONACIÓN", "LESION", "LESIÓN", "HOMICIDIO", "AMENAZA", "ALARMA",
        "DAÑOS", "DAÑ0S", "RIÑA", "RINA", "TRÁNSITO", "TRANSITO", "APOYO", "AUXILIO",
    )

    if folio_i >= 0:
        remainder = ""
        try:
            remainder = clean(folio_line[folio_end:]).strip(" -_:")
        except Exception:
            pass
        if remainder:
            upper = remainder.upper()
            cut = len(remainder)
            for word in incident_words:
                p = upper.find(word)
                if p >= 0:
                    cut = min(cut, p)
            candidate = clean(remainder[:cut]).strip(" -_:")
            if candidate and not re.search(r"\d{1,2}:\d{2}", candidate):
                out["municipio"] = candidate.upper()

        start_idx = folio_i + 1
        if not out["municipio"]:
            for j in range(start_idx, min(len(lines), start_idx + 3)):
                cand = clean(lines[j])
                up = cand.upper()
                if re.search(r"\d{1,2}:\d{2}", cand):
                    continue
                if any(w in up for w in incident_words):
                    continue
                if 2 <= len(cand) <= 45:
                    out["municipio"] = cand.upper()
                    start_idx = j + 1
                    break

        tips = []
        for j in range(start_idx, min(len(lines), start_idx + 5)):
            cand = clean(lines[j])
            if out["hora"] and out["hora"] in cand:
                break
            if re.search(r"\d{1,2}:\d{2}", cand):
                break
            up = cand.upper()
            if out["municipio"] and up == out["municipio"]:
                continue
            if cand:
                tips.append(cand)
        if tips:
            out["tipificacion"] = clean(" ".join(tips)).upper()

    if not out["tipificacion"]:
        for line in lines:
            up = line.upper()
            if any(word in up for word in incident_words):
                out["tipificacion"] = up
                break

    def labeled(*patterns):
        for pat in patterns:
            m = re.search(rf"(?im)^\s*{pat}\s*[:\-]\s*(.+)$", joined)
            if m:
                return clean(m.group(1))
        return ""

    out["direccion"] = labeled("DIRECCI[ÓO]N", "DOMICILIO", "UBICACI[ÓO]N")
    out["colonia"] = labeled("COLONIA", "BARRIO", "FRACC(?:IONAMIENTO)?")
    out["corporacion"] = labeled("CORPORACI[ÓO]N", "INSTITUCI[ÓO]N")
    out["unidad"] = labeled("UNIDAD", "M[ÓO]VIL")
    out["narrativa"] = labeled("NARRATIVA", "HECHOS", "DESCRIPCI[ÓO]N")
    return out


def build_note(d):
    rows = ["NOTA INFORMATIVA", "", f"FECHA: {datetime.now().strftime('%d/%m/%Y')}"]
    labels = [
        ("folio", "FOLIO PROMAD"),
        ("municipio", "MUNICIPIO"),
        ("tipificacion", "TIPIFICACIÓN"),
        ("hora", "HORA PROMAD"),
        ("direccion", "DIRECCIÓN"),
        ("colonia", "COLONIA"),
        ("corporacion", "CORPORACIÓN"),
        ("unidad", "UNIDAD"),
    ]
    for key, label in labels:
        if d.get(key):
            rows.append(f"{label}: {d[key]}")
    if d.get("narrativa"):
        rows.extend(["", "HECHOS:", d["narrativa"]])
    rows.extend(["", "FUENTE: PROMAD (captura local de pantalla).", f"CAPTURADO: {d.get('capturado_en') or now_iso()}"])
    return "\n".join(rows)


def save_incident(d):
    try:
        db = json.loads(DB.read_text(encoding="utf-8")) if DB.exists() else {}
    except Exception:
        db = {}
    key = d.get("folio") or "SIN_FOLIO_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    old = db.get(key, {})
    for k, v in d.items():
        if v:
            old[k] = v
    old["actualizado_en"] = now_iso()
    db[key] = old
    DB.write_text(json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")
    return key


class CaptureWorker(threading.Thread):
    """Continuous region capture using MSS. The UI only displays the newest frame."""
    def __init__(self):
        super().__init__(daemon=True)
        self._stop = threading.Event()
        self._active = threading.Event()
        self._lock = threading.Lock()
        self._region = None
        self._latest = None
        self.frames = queue.Queue(maxsize=1)
        self.errors = queue.Queue(maxsize=3)

    def set_region(self, region):
        with self._lock:
            self._region = dict(region) if region else None

    def set_active(self, active):
        if active:
            self._active.set()
        else:
            self._active.clear()

    def latest(self):
        with self._lock:
            return self._latest.copy() if self._latest is not None else None

    def stop(self):
        self._stop.set()
        self._active.set()

    def run(self):
        try:
            with mss.mss() as sct:
                while not self._stop.is_set():
                    if not self._active.is_set():
                        time.sleep(0.08)
                        continue
                    with self._lock:
                        region = dict(self._region) if self._region else None
                    if not region:
                        time.sleep(0.08)
                        continue
                    try:
                        shot = sct.grab(region)
                        img = Image.frombytes("RGB", (shot.width, shot.height), shot.rgb)
                        with self._lock:
                            self._latest = img
                        try:
                            if self.frames.full():
                                self.frames.get_nowait()
                            self.frames.put_nowait(img)
                        except queue.Empty:
                            pass
                        except queue.Full:
                            pass
                    except Exception as e:
                        try:
                            if not self.errors.full():
                                self.errors.put_nowait(str(e))
                        except Exception:
                            pass
                        time.sleep(0.25)
                    time.sleep(0.075)
        except Exception as e:
            try:
                self.errors.put_nowait(str(e))
            except Exception:
                pass


class RegionOverlay(tk.Toplevel):
    """OBS-like editable ROI overlay: drag inside to move, drag handles to resize."""
    HANDLE = 11
    MIN_SIZE = 40

    def __init__(self, master, callback, initial=None):
        super().__init__(master)
        self.callback = callback
        self.v = virtual_screen()
        self.mode = None
        self.handle = None
        self.start = None
        self.orig = None
        self.rect = None

        self.overrideredirect(True)
        self.attributes("-topmost", True)
        try:
            self.attributes("-alpha", 0.34)
        except Exception:
            pass
        self.configure(bg="black")
        self.geometry(
            f"{self.v['width']}x{self.v['height']}"
            f"{self.v['left']:+d}{self.v['top']:+d}"
        )

        self.canvas = tk.Canvas(self, bg="black", highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.create_rectangle(0, 0, self.v["width"], 58, fill="#000000", outline="")
        self.canvas.create_text(
            18, 16, anchor="nw", fill="white", font=("Segoe UI", 13, "bold"),
            text="DELIMITA PROMAD  •  arrastra dentro = mover  •  esquinas/bordes = redimensionar  •  ENTER o DOBLE CLIC = aplicar  •  ESC = cancelar",
        )

        if initial:
            x1 = int(initial["left"] - self.v["left"])
            y1 = int(initial["top"] - self.v["top"])
            x2 = x1 + int(initial["width"])
            y2 = y1 + int(initial["height"])
            self.rect = [x1, y1, x2, y2]
        else:
            w, h = self.v["width"], self.v["height"]
            self.rect = [int(w * 0.18), int(h * 0.20), int(w * 0.82), int(h * 0.80)]

        self.canvas.bind("<ButtonPress-1>", self.on_down)
        self.canvas.bind("<B1-Motion>", self.on_move)
        self.canvas.bind("<ButtonRelease-1>", self.on_up)
        self.canvas.bind("<Double-Button-1>", lambda e: self.confirm())
        self.bind("<Return>", lambda e: self.confirm())
        self.bind("<Escape>", lambda e: self.cancel())
        self.bind("<Button-3>", lambda e: self.cancel())
        self.after(80, self._focus)
        self.redraw()

    def _focus(self):
        try:
            self.focus_force()
            self.grab_set()
        except Exception:
            pass

    def norm(self, rect=None):
        r = rect or self.rect
        x1, y1, x2, y2 = r
        return [min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)]

    def points(self):
        x1, y1, x2, y2 = self.norm()
        xm, ym = (x1 + x2) // 2, (y1 + y2) // 2
        return {
            "nw": (x1, y1), "n": (xm, y1), "ne": (x2, y1),
            "e": (x2, ym), "se": (x2, y2), "s": (xm, y2),
            "sw": (x1, y2), "w": (x1, ym),
        }

    def hit(self, x, y):
        hs = self.HANDLE + 4
        for name, (hx, hy) in self.points().items():
            if abs(x - hx) <= hs and abs(y - hy) <= hs:
                return ("handle", name)
        x1, y1, x2, y2 = self.norm()
        if x1 <= x <= x2 and y1 <= y <= y2:
            return ("move", None)
        return ("new", None)

    def on_down(self, e):
        self.mode, self.handle = self.hit(e.x, e.y)
        self.start = (e.x, e.y)
        self.orig = self.norm()
        if self.mode == "new":
            self.rect = [e.x, e.y, e.x, e.y]
        self.redraw()

    def on_move(self, e):
        if not self.mode or not self.start:
            return
        x = max(0, min(self.v["width"], e.x))
        y = max(58, min(self.v["height"], e.y))
        sx, sy = self.start
        ox1, oy1, ox2, oy2 = self.orig

        if self.mode == "new":
            self.rect = [sx, sy, x, y]
        elif self.mode == "move":
            dx, dy = x - sx, y - sy
            rw, rh = ox2 - ox1, oy2 - oy1
            nx1 = max(0, min(self.v["width"] - rw, ox1 + dx))
            ny1 = max(58, min(self.v["height"] - rh, oy1 + dy))
            self.rect = [nx1, ny1, nx1 + rw, ny1 + rh]
        elif self.mode == "handle":
            nx1, ny1, nx2, ny2 = ox1, oy1, ox2, oy2
            h = self.handle or ""
            if "w" in h:
                nx1 = x
            if "e" in h:
                nx2 = x
            if "n" in h:
                ny1 = y
            if "s" in h:
                ny2 = y
            if h == "n":
                ny1 = y
            elif h == "s":
                ny2 = y
            elif h == "e":
                nx2 = x
            elif h == "w":
                nx1 = x
            self.rect = [nx1, ny1, nx2, ny2]
        self.redraw()

    def on_up(self, e):
        self.rect = self.norm()
        x1, y1, x2, y2 = self.rect
        if x2 - x1 < self.MIN_SIZE or y2 - y1 < self.MIN_SIZE:
            self.rect = self.orig or self.rect
        self.mode = self.handle = self.start = self.orig = None
        self.redraw()

    def redraw(self):
        self.canvas.delete("roi")
        x1, y1, x2, y2 = self.norm()
        self.canvas.create_rectangle(x1, y1, x2, y2, outline=CYAN, width=4, tags="roi")
        for _, (hx, hy) in self.points().items():
            s = self.HANDLE
            self.canvas.create_rectangle(hx - s, hy - s, hx + s, hy + s, fill="white", outline=CYAN, width=2, tags="roi")
        self.canvas.create_rectangle(x1, max(60, y1 - 30), min(x2, x1 + 240), y1, fill="#000000", outline=CYAN, width=1, tags="roi")
        self.canvas.create_text(
            x1 + 8, max(64, y1 - 24), anchor="nw", fill="white", font=("Segoe UI", 10, "bold"),
            text=f"{max(0, x2-x1)} × {max(0, y2-y1)} px", tags="roi"
        )

    def confirm(self):
        x1, y1, x2, y2 = self.norm()
        if x2 - x1 < self.MIN_SIZE or y2 - y1 < self.MIN_SIZE:
            return
        result = {
            "left": x1 + self.v["left"],
            "top": y1 + self.v["top"],
            "width": x2 - x1,
            "height": y2 - y1,
        }
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()
        self.callback(result)

    def cancel(self):
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()
        self.callback(None)


class Hotkeys(threading.Thread):
    def __init__(self, events):
        super().__init__(daemon=True)
        self.events = events

    def run(self):
        if os.name != "nt":
            return
        user32 = ctypes.windll.user32
        WM_HOTKEY = 0x0312
        MOD_NOREPEAT = 0x4000
        bindings = [(1, 0x76, "select"), (2, 0x77, "read"), (3, 0x78, "live")]
        for ident, vk, _ in bindings:
            try:
                user32.RegisterHotKey(None, ident, MOD_NOREPEAT, vk)
            except Exception:
                pass
        msg = ctypes.wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            if msg.message == WM_HOTKEY:
                for ident, _, name in bindings:
                    if msg.wParam == ident:
                        self.events.put(name)
                        break


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP} {VER}")
        self.geometry("1420x880")
        self.minsize(1120, 720)
        self.configure(bg=BG)
        self.protocol("WM_DELETE_WINDOW", self.close_app)

        self.region = None
        self.live = False
        self.ocr_busy = False
        self.imgtk = None
        self.last_frame = None
        self.capture_path = ""
        self.hotkey_events = queue.Queue()
        self.auto_ocr = tk.BooleanVar(value=False)
        self.vars = {k: tk.StringVar() for k in [
            "folio", "municipio", "tipificacion", "hora", "direccion", "colonia",
            "corporacion", "unidad", "capturado_en"
        ]}

        self.worker = CaptureWorker()
        self.worker.start()
        self.load_cfg()
        self.worker.set_region(self.region)
        self.build_ui()
        self.refresh_ocr_state()

        Hotkeys(self.hotkey_events).start()
        self.after(80, self.poll_hotkeys)
        self.after(55, self.poll_frames)
        self.after(3000, self.auto_ocr_tick)

        if self.region:
            self.update_region_label()
            self.set_live(True)
        else:
            self.status("Presiona F7 o SELECCIONAR ÁREA para delimitar PROMAD.", MUTED)

    def btn(self, parent, text, cmd, color=CYAN):
        return tk.Button(
            parent, text=text, command=cmd, bg=color, fg="white", activebackground=color,
            activeforeground="white", relief="flat", bd=0, padx=13, pady=9,
            font=("Segoe UI", 9, "bold"), cursor="hand2"
        )

    def load_cfg(self):
        try:
            cfg = json.loads(CFG.read_text(encoding="utf-8"))
            self.region = clamp_region(cfg.get("region"))
            self.auto_ocr.set(bool(cfg.get("auto_ocr", False)))
        except Exception:
            self.region = None

    def save_cfg(self):
        try:
            CFG.write_text(json.dumps({"region": self.region, "auto_ocr": bool(self.auto_ocr.get())}, indent=2), encoding="utf-8")
        except Exception:
            pass

    def build_ui(self):
        top = tk.Frame(self, bg=PANEL, height=62)
        top.pack(fill="x")
        top.pack_propagate(False)
        tk.Label(top, text="PROMAD  •  CAPTURA EN VIVO + NOTA INFORMATIVA", bg=PANEL, fg=TEXT,
                 font=("Segoe UI", 15, "bold")).pack(side="left", padx=18, pady=17)
        self.ocr_badge = tk.Label(top, text="OCR", bg=PANEL, fg=MUTED, font=("Segoe UI", 9, "bold"))
        self.ocr_badge.pack(side="right", padx=18)

        toolbar = tk.Frame(self, bg=BG)
        toolbar.pack(fill="x", padx=14, pady=10)
        self.btn(toolbar, "F7  SELECCIONAR / AJUSTAR ÁREA", self.select_region).pack(side="left", padx=4)
        self.live_btn = self.btn(toolbar, "F9  INICIAR EN VIVO", self.toggle_live, GREEN)
        self.live_btn.pack(side="left", padx=4)
        self.btn(toolbar, "F8  LEER PROMAD", self.read_promad, BLUE).pack(side="left", padx=4)
        self.btn(toolbar, "ABRIR IMAGEN", self.open_image, "#3a4d6b").pack(side="left", padx=4)
        tk.Checkbutton(
            toolbar, text="Leer automáticamente cada 3 s", variable=self.auto_ocr, command=self.save_cfg,
            bg=BG, fg=TEXT, selectcolor=CARD, activebackground=BG, activeforeground=TEXT,
            font=("Segoe UI", 9), cursor="hand2"
        ).pack(side="left", padx=12)
        self.status_lbl = tk.Label(toolbar, text="", bg=BG, fg=MUTED, font=("Segoe UI", 9))
        self.status_lbl.pack(side="left", padx=10)

        self.region_lbl = tk.Label(self, text="ÁREA: sin seleccionar", bg=BG, fg=CYAN, font=("Segoe UI", 9, "bold"))
        self.region_lbl.pack(fill="x", padx=18, pady=(0, 6), anchor="w")

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=14, pady=(0, 12))
        body.grid_columnconfigure(0, weight=6)
        body.grid_columnconfigure(1, weight=5)
        body.grid_rowconfigure(0, weight=1)

        left = tk.Frame(body, bg=PANEL)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        right = tk.Frame(body, bg=PANEL)
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 0))

        lh = tk.Frame(left, bg=PANEL)
        lh.pack(fill="x", padx=12, pady=(12, 6))
        tk.Label(lh, text="VIDEO EN VIVO DEL ÁREA DELIMITADA", bg=PANEL, fg=TEXT,
                 font=("Segoe UI", 10, "bold")).pack(side="left")
        self.live_dot = tk.Label(lh, text="● DETENIDO", bg=PANEL, fg=MUTED, font=("Segoe UI", 9, "bold"))
        self.live_dot.pack(side="right")

        self.preview = tk.Label(left, text="Selecciona el área de PROMAD con F7.", bg="#040a12", fg=MUTED,
                                font=("Segoe UI", 11))
        self.preview.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        self.conf_lbl = tk.Label(left, text="", bg=PANEL, fg=MUTED, font=("Segoe UI", 9))
        self.conf_lbl.pack(anchor="w", padx=12)
        self.raw = tk.Text(left, height=7, bg="#0b172a", fg=TEXT, insertbackground=TEXT, relief="flat",
                           font=("Consolas", 8), wrap="word")
        self.raw.pack(fill="x", padx=12, pady=(6, 12))

        tk.Label(right, text="DATOS PROMAD DETECTADOS / EDITABLES", bg=PANEL, fg=TEXT,
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=12, pady=(12, 6))
        form = tk.Frame(right, bg=PANEL)
        form.pack(fill="x", padx=12)
        fields = [
            ("folio", "Folio PROMAD"), ("municipio", "Municipio"), ("tipificacion", "Tipificación"),
            ("hora", "Hora"), ("direccion", "Dirección"), ("colonia", "Colonia"),
            ("corporacion", "Corporación"), ("unidad", "Unidad")
        ]
        for i, (key, label) in enumerate(fields):
            tk.Label(form, text=label, bg=PANEL, fg=MUTED, font=("Segoe UI", 8, "bold")).grid(row=i, column=0, sticky="w", pady=3)
            tk.Entry(form, textvariable=self.vars[key], bg=CARD, fg=TEXT, insertbackground=TEXT, relief="flat",
                     font=("Segoe UI", 9)).grid(row=i, column=1, sticky="ew", padx=(10, 0), pady=3, ipady=5)
        form.grid_columnconfigure(1, weight=1)

        tk.Label(right, text="Narrativa / hechos", bg=PANEL, fg=MUTED, font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=12, pady=(8, 3))
        self.narr = tk.Text(right, height=5, bg=CARD, fg=TEXT, insertbackground=TEXT, relief="flat",
                            wrap="word", font=("Segoe UI", 9))
        self.narr.pack(fill="x", padx=12)

        tk.Label(right, text="NOTA INFORMATIVA", bg=PANEL, fg=TEXT, font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=12, pady=(10, 4))
        self.note_box = tk.Text(right, bg="#0b172a", fg=TEXT, insertbackground=TEXT, relief="flat",
                                wrap="word", font=("Segoe UI", 9))
        self.note_box.pack(fill="both", expand=True, padx=12)

        actions = tk.Frame(right, bg=PANEL)
        actions.pack(fill="x", padx=12, pady=12)
        self.btn(actions, "GENERAR NOTA", self.generate_note).pack(side="left", padx=3)
        self.btn(actions, "COPIAR NOTA", self.copy_note, BLUE).pack(side="left", padx=3)
        self.btn(actions, "GUARDAR FOLIO", self.save_current, GREEN).pack(side="left", padx=3)

    def status(self, text, color=MUTED):
        self.status_lbl.config(text=text, fg=color)

    def refresh_ocr_state(self):
        exe = tesseract_exe()
        if exe:
            pytesseract.pytesseract.tesseract_cmd = exe
            self.ocr_badge.config(text="● OCR INTEGRADO", fg=GREEN)
        else:
            self.ocr_badge.config(text="● OCR NO DISPONIBLE", fg=RED)

    def update_region_label(self):
        if not self.region:
            self.region_lbl.config(text="ÁREA: sin seleccionar")
            return
        r = self.region
        self.region_lbl.config(text=f"ÁREA FIJADA: X {r['left']}  Y {r['top']}  •  {r['width']} × {r['height']} px  •  F7 para mover/redimensionar")

    def select_region(self):
        was_live = self.live
        self.set_live(False)
        self.withdraw()

        def launch():
            try:
                RegionOverlay(self, lambda result: self.region_done(result, was_live), self.region)
            except Exception as e:
                self.deiconify()
                self.status(f"No se pudo abrir el selector: {e}", RED)

        self.after(180, launch)

    def region_done(self, result, was_live):
        self.deiconify()
        self.lift()
        try:
            self.focus_force()
        except Exception:
            pass
        if result:
            self.region = clamp_region(result)
            self.worker.set_region(self.region)
            self.save_cfg()
            self.update_region_label()
            self.status("Área aplicada. Captura en vivo iniciada.", GREEN)
            self.set_live(True)
        else:
            self.status("Selección cancelada.", YELLOW)
            if was_live and self.region:
                self.set_live(True)

    def set_live(self, active):
        if active and not self.region:
            self.status("Primero selecciona un área con F7.", YELLOW)
            active = False
        self.live = bool(active)
        self.worker.set_active(self.live)
        if self.live:
            self.live_btn.config(text="F9  DETENER EN VIVO", bg=RED, activebackground=RED)
            self.live_dot.config(text="● EN VIVO", fg=GREEN)
        else:
            self.live_btn.config(text="F9  INICIAR EN VIVO", bg=GREEN, activebackground=GREEN)
            self.live_dot.config(text="● DETENIDO", fg=MUTED)

    def toggle_live(self):
        self.set_live(not self.live)

    def poll_hotkeys(self):
        try:
            while True:
                action = self.hotkey_events.get_nowait()
                if action == "select":
                    self.select_region()
                elif action == "read":
                    self.read_promad()
                elif action == "live":
                    self.toggle_live()
        except queue.Empty:
            pass
        self.after(80, self.poll_hotkeys)

    def poll_frames(self):
        try:
            while True:
                frame = self.worker.frames.get_nowait()
                self.last_frame = frame
        except queue.Empty:
            pass

        try:
            while True:
                err = self.worker.errors.get_nowait()
                self.status(f"Error de captura: {err}", RED)
        except queue.Empty:
            pass

        if self.last_frame is not None and self.live:
            self.show_frame(self.last_frame)
        self.after(55, self.poll_frames)

    def show_frame(self, image):
        try:
            w = max(640, self.preview.winfo_width() - 20)
            h = max(360, self.preview.winfo_height() - 20)
            view = image.copy()
            view.thumbnail((w, h), Image.Resampling.LANCZOS)
            self.imgtk = ImageTk.PhotoImage(view)
            self.preview.config(image=self.imgtk, text="")
        except Exception:
            pass

    def current_frame(self):
        frame = self.worker.latest()
        if frame is None and self.region:
            try:
                frame = grab_region_once(self.region)
            except Exception:
                frame = None
        return frame

    def read_promad(self):
        if self.ocr_busy:
            self.status("OCR en proceso…", YELLOW)
            return
        frame = self.current_frame()
        if frame is None:
            self.status("No hay imagen. Selecciona un área con F7 e inicia EN VIVO.", YELLOW)
            return
        self.run_ocr(frame)

    def run_ocr(self, frame):
        exe = tesseract_exe()
        if not exe:
            messagebox.showerror("OCR", "El motor OCR no está disponible en esta copia.")
            return
        pytesseract.pytesseract.tesseract_cmd = exe
        self.ocr_busy = True
        self.status("Leyendo datos de PROMAD…", YELLOW)

        try:
            p = CAP / f"PROMAD_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.png"
            frame.save(p)
            self.capture_path = str(p)
        except Exception:
            self.capture_path = ""

        def work():
            try:
                img = ImageOps.grayscale(frame)
                scale = 2 if max(img.size) < 1800 else 1
                if scale > 1:
                    img = img.resize((img.width * scale, img.height * scale), Image.Resampling.LANCZOS)
                img = ImageOps.autocontrast(img)
                img = ImageEnhance.Contrast(img).enhance(1.6)
                img = img.filter(ImageFilter.SHARPEN)

                try:
                    txt = pytesseract.image_to_string(img, lang="spa+eng", config="--psm 6")
                    dat = pytesseract.image_to_data(img, lang="spa+eng", config="--psm 6", output_type=pytesseract.Output.DICT)
                except Exception:
                    txt = pytesseract.image_to_string(img, lang="eng", config="--psm 6")
                    dat = pytesseract.image_to_data(img, lang="eng", config="--psm 6", output_type=pytesseract.Output.DICT)

                confs = []
                for c in dat.get("conf", []):
                    try:
                        v = float(c)
                        if v >= 0:
                            confs.append(v)
                    except Exception:
                        pass
                avg = sum(confs) / len(confs) if confs else 0.0
                parsed = parse_promad(txt)
                self.after(0, lambda: self.apply_ocr(parsed, txt, avg))
            except Exception as e:
                self.after(0, lambda: self.ocr_failed(str(e)))

        threading.Thread(target=work, daemon=True).start()

    def apply_ocr(self, data, text, avg):
        self.ocr_busy = False
        self.raw.delete("1.0", "end")
        self.raw.insert("1.0", text)
        for key in self.vars:
            if data.get(key):
                self.vars[key].set(data[key])
        if data.get("narrativa"):
            self.narr.delete("1.0", "end")
            self.narr.insert("1.0", data["narrativa"])
        self.vars["capturado_en"].set(now_iso())
        self.conf_lbl.config(text=f"Confianza OCR aproximada: {avg:.0f}%" + ("  •  REVISAR" if avg < 62 else ""),
                             fg=GREEN if avg >= 62 else YELLOW)
        folio = data.get("folio") or "sin folio"
        tip = data.get("tipificacion") or "sin tipificación"
        self.status(f"Leído: {folio}  •  {tip}", GREEN if data.get("folio") else YELLOW)
        self.generate_note()

    def ocr_failed(self, error):
        self.ocr_busy = False
        self.status(f"OCR falló: {error}", RED)

    def auto_ocr_tick(self):
        if self.auto_ocr.get() and self.live and self.region and not self.ocr_busy:
            self.read_promad()
        self.after(3000, self.auto_ocr_tick)

    def open_image(self):
        path = filedialog.askopenfilename(filetypes=[("Imagen", "*.png *.jpg *.jpeg *.bmp")])
        if not path:
            return
        try:
            img = Image.open(path).convert("RGB")
            self.last_frame = img
            self.capture_path = path
            self.show_frame(img)
            self.run_ocr(img)
        except Exception as e:
            messagebox.showerror("Imagen", str(e))

    def collect(self):
        d = {k: v.get().strip() for k, v in self.vars.items()}
        d["narrativa"] = self.narr.get("1.0", "end").strip()
        d["ocr"] = self.raw.get("1.0", "end").strip()
        d["captura"] = self.capture_path
        d["region"] = dict(self.region) if self.region else None
        d["capturado_en"] = d.get("capturado_en") or now_iso()
        return d

    def generate_note(self):
        text = build_note(self.collect())
        self.note_box.delete("1.0", "end")
        self.note_box.insert("1.0", text)
        return text

    def copy_note(self):
        text = self.generate_note()
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update()
        self.status("Nota copiada.", GREEN)

    def save_current(self):
        key = save_incident(self.collect())
        self.status(f"Folio guardado: {key}", GREEN)

    def close_app(self):
        self.save_cfg()
        try:
            self.worker.stop()
        except Exception:
            pass
        self.destroy()


if __name__ == "__main__":
    App().mainloop()

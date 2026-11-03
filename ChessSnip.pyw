"""ChessSnip — a native-feeling Windows screenshot and annotation utility."""
from __future__ import annotations

import ctypes
import colorsys
import faulthandler
import io
import json
import math
import os
import sys
import threading
import tempfile
import textwrap
import time
import tkinter as tk
import traceback
import webbrowser
from ctypes import wintypes
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from urllib.parse import quote
import uuid

_LOCAL_DATA = Path(
    os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
)
CRASH_LOG_DIR = _LOCAL_DATA / "ChessSnip" / "Logs"
CRASH_LOG_PATH = CRASH_LOG_DIR / "crash.log"
_CRASH_LOG_HANDLE = None
_CRASH_LOG_LOCK = threading.Lock()


def _write_crash_log_message(message):
    """Write a short, flushed breadcrumb to the persistent diagnostic log."""
    handle = _CRASH_LOG_HANDLE
    if handle is None:
        return
    try:
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        with _CRASH_LOG_LOCK:
            handle.write(f"[{timestamp}] {message}\n")
            handle.flush()
    except Exception:
        pass


def _write_crash_log_exception(context, exc_type, exc_value, exc_traceback):
    handle = _CRASH_LOG_HANDLE
    if handle is None:
        return
    try:
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        with _CRASH_LOG_LOCK:
            handle.write(f"\n[{timestamp}] {context}\n")
            traceback.print_exception(exc_type, exc_value, exc_traceback, file=handle)
            handle.flush()
    except Exception:
        pass


def _handle_unhandled_exception(exc_type, exc_value, exc_traceback):
    _write_crash_log_exception(
        "Unhandled application exception", exc_type, exc_value, exc_traceback
    )
    try:
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
    except Exception:
        pass


def _handle_unhandled_thread_exception(args):
    thread_name = args.thread.name if args.thread is not None else "unknown thread"
    _write_crash_log_exception(
        f"Unhandled exception in thread {thread_name}",
        args.exc_type,
        args.exc_value,
        args.exc_traceback,
    )
    try:
        threading.__excepthook__(args)
    except Exception:
        pass


def _enable_crash_logging():
    """Keep Python exceptions and fatal Windows faults in a user-writable log."""
    global _CRASH_LOG_HANDLE, CRASH_LOG_DIR, CRASH_LOG_PATH

    fallback_dir = Path(tempfile.gettempdir()) / "ChessSnip" / "Logs"
    for log_dir in (CRASH_LOG_DIR, fallback_dir):
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / "crash.log"
            if log_path.exists() and log_path.stat().st_size >= 2 * 1024 * 1024:
                previous_path = log_dir / "crash.previous.log"
                try:
                    previous_path.unlink(missing_ok=True)
                    log_path.replace(previous_path)
                except OSError:
                    pass
            _CRASH_LOG_HANDLE = open(log_path, "a", encoding="utf-8", buffering=1)
            CRASH_LOG_DIR = log_dir
            CRASH_LOG_PATH = log_path
            break
        except OSError:
            continue

    if _CRASH_LOG_HANDLE is None:
        return

    sys.excepthook = _handle_unhandled_exception
    if hasattr(threading, "excepthook"):
        threading.excepthook = _handle_unhandled_thread_exception

    _write_crash_log_message(
        f"ChessSnip started; executable={sys.executable}; Python={sys.version.split()[0]}"
    )
    try:
        # On Windows this also hooks fatal OS exceptions such as access violations.
        # Keep the file open for the lifetime of the process as faulthandler requires.
        faulthandler.enable(file=_CRASH_LOG_HANDLE, all_threads=True)
    except (OSError, RuntimeError, ValueError) as exc:
        _write_crash_log_exception(
            "Could not enable fatal-crash traceback capture",
            type(exc), exc, exc.__traceback__,
        )


_enable_crash_logging()

try:
    from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps, ImageTk
except ImportError:
    root = tk.Tk(); root.withdraw()
    messagebox.showerror("ChessSnip", "ChessSnip needs Pillow. Install it with:\n\npy -m pip install Pillow")
    raise SystemExit(1)

# Give the process and its taskbar shortcut a stable identity so Windows can
# group the running window with a pinned ChessSnip button.
try:
    _shell32 = ctypes.windll.shell32
    _shell32.SetCurrentProcessExplicitAppUserModelID.argtypes = (wintypes.LPCWSTR,)
    _shell32.SetCurrentProcessExplicitAppUserModelID.restype = ctypes.c_long
    _shell32.SetCurrentProcessExplicitAppUserModelID("ChessSnip.Desktop")
except Exception:
    pass

# Make desktop coordinates and captures agree on high-DPI displays.
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try: ctypes.windll.user32.SetProcessDPIAware()
    except Exception: pass

INK = "#111318"
PANEL = "#191c23"
PANEL2 = "#222630"
EDGE = "#303541"
TEXT = "#edf0f6"
MUTED = "#a0a6b4"
ACCENT = "#8b83ff"
ROOT = Path(os.environ.get("USERPROFILE", str(Path.home())))
DEFAULT_FOLDER = ROOT / "Pictures" / "ChessSnip"
TEMP_FOLDER = Path(tempfile.gettempdir()) / "ChessSnip"
SETTINGS_PATH = Path(os.environ.get("APPDATA") or (ROOT / "AppData" / "Roaming")) / "QuickSnip" / "settings.json"

THEMES = {
    "dark": {"ink": "#111318", "panel": "#191c23", "panel2": "#222630", "edge": "#303541", "text": "#edf0f6", "muted": "#a0a6b4"},
    "light": {"ink": "#f3f4f7", "panel": "#ffffff", "panel2": "#e7e9ef", "edge": "#d3d7e0", "text": "#20232b", "muted": "#606878"},
}
THEME_SPECIAL_COLORS = {
    "dark": {"editor_canvas": "#171920", "image_edge": "#0c0d11", "checkbox_checked": "#262935", "chess_button": "#29263d", "chess_text": "#d5d0ff", "chess_active": "#39344f"},
    "light": {"editor_canvas": "#eceef3", "image_edge": "#c4c9d3", "checkbox_checked": "#dce0e9", "chess_button": "#eeeaff", "chess_text": "#574d9a", "chess_active": "#ddd8ff"},
}
THEME_FIELDS = ("ink", "panel", "panel2", "edge", "text", "muted")


def _activate_palette(name):
    global INK, PANEL, PANEL2, EDGE, TEXT, MUTED
    palette = THEMES[name]
    INK, PANEL, PANEL2, EDGE, TEXT, MUTED = (palette[key] for key in THEME_FIELDS)


def _load_saved_theme():
    try:
        value = json.loads(SETTINGS_PATH.read_text(encoding="utf-8")).get("theme", "dark")
        return value if value in THEMES else "dark"
    except (OSError, ValueError, TypeError, AttributeError):
        return "dark"


def _resource_path(name):
    bundle_dir = getattr(sys, "_MEIPASS", None)
    base = Path(bundle_dir) if bundle_dir else Path(__file__).resolve().parent
    return base / name


def _colorref(color):
    red, green, blue = (int(color[index:index + 2], 16) for index in (1, 3, 5))
    return red | (green << 8) | (blue << 16)


class ChessSnipTooltip:
    """Small delayed tooltip used by the main and chess-review windows."""

    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.pending = None
        self.window = None
        widget.bind("<Enter>", self.schedule, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")

    def schedule(self, _event=None):
        self.hide()
        try:
            self.pending = self.widget.after(500, self.show)
        except tk.TclError:
            self.pending = None

    def show(self):
        self.pending = None
        try:
            if not self.widget.winfo_exists() or not self.widget.winfo_viewable():
                return
            tip = tk.Toplevel(self.widget)
            self.window = tip
            tip.wm_overrideredirect(True)
            tip.attributes("-topmost", True)
            tk.Label(
                tip, text=self.text, bg=PANEL2, fg=TEXT, font=("Segoe UI", 9),
                padx=8, pady=5, relief="flat", bd=0, highlightthickness=1,
                highlightbackground=EDGE,
            ).pack()
            tip.update_idletasks()
            x = self.widget.winfo_rootx() + max(0, (self.widget.winfo_width() - tip.winfo_width()) // 2)
            anchor_y = self.widget.winfo_rooty()
            tip_width, tip_height = tip.winfo_width(), tip.winfo_height()
            try:
                user32 = ctypes.windll.user32
                user32.MonitorFromWindow.argtypes = (wintypes.HWND, wintypes.DWORD)
                user32.MonitorFromWindow.restype = wintypes.HANDLE
                user32.GetMonitorInfoW.argtypes = (wintypes.HANDLE, ctypes.POINTER(_MONITORINFO))
                user32.GetMonitorInfoW.restype = wintypes.BOOL
                owner_hwnd = self.widget.winfo_toplevel().winfo_id()
                monitor = user32.MonitorFromWindow(owner_hwnd, 2)  # MONITOR_DEFAULTTONEAREST
                info = _MONITORINFO()
                info.cbSize = ctypes.sizeof(_MONITORINFO)
                if not monitor or not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                    raise ctypes.WinError()
                left, top = info.rcWork.left, info.rcWork.top
                right, bottom = info.rcWork.right, info.rcWork.bottom
            except Exception:
                # Include displays positioned left/above the primary monitor.
                user32 = ctypes.windll.user32
                left, top = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)
                right = left + user32.GetSystemMetrics(78)
                bottom = top + user32.GetSystemMetrics(79)
            margin = 8
            max_x = max(left + margin, right - tip_width - margin)
            x = max(left + margin, min(x, max_x))
            y = anchor_y + self.widget.winfo_height() + 6
            if y + tip_height > bottom - margin:
                y = anchor_y - tip_height - 6
            max_y = max(top + margin, bottom - tip_height - margin)
            y = max(top + margin, min(y, max_y))
            tip.geometry(f"+{x}+{y}")
        except tk.TclError:
            self.window = None

    def hide(self, _event=None):
        if self.pending is not None:
            try:
                self.widget.after_cancel(self.pending)
            except tk.TclError:
                pass
            self.pending = None
        if self.window is not None:
            try:
                self.window.destroy()
            except tk.TclError:
                pass
            self.window = None


class ModernSlider(tk.Canvas):
    """A compact canvas slider with a rounded thumb and optional tick marks."""

    def __init__(self, master, variable, minimum, maximum, *, length, command=None, ticks=(), hue_gradient=False, saturation_gradient=False, brightness_gradient=False, gradient_hue=0.0, gradient_saturation=1.0, gradient_value=1.0, background=None):
        self.variable = variable
        self.minimum = minimum
        self.maximum = maximum
        self.command = command
        self.ticks = tuple(ticks)
        self.hue_gradient = hue_gradient
        self.saturation_gradient = saturation_gradient
        self.brightness_gradient = brightness_gradient
        self.gradient_hue = float(gradient_hue) % 1.0
        self.gradient_saturation = max(0.0, min(1.0, float(gradient_saturation)))
        self.gradient_value = max(0.0, min(1.0, float(gradient_value)))
        self._slider_photo = None
        self._gradient_bitmap = None
        self._gradient_size = None
        self._slider_scale = 4
        self.length = length
        self._dragging = False
        super().__init__(
            master, width=length, height=26, bg=background or PANEL,
            highlightthickness=0, bd=0, cursor="hand2", takefocus=True,
        )
        self.bind("<Configure>", self._redraw, add="+")
        self.bind("<ButtonPress-1>", self._press, add="+")
        self.bind("<B1-Motion>", self._drag, add="+")
        self.bind("<ButtonRelease-1>", self._release, add="+")
        self.bind("<Left>", lambda _event: self._step(-1), add="+")
        self.bind("<Right>", lambda _event: self._step(1), add="+")
        self.bind("<Home>", lambda _event: self._set_value(self.minimum), add="+")
        self.bind("<End>", lambda _event: self._set_value(self.maximum), add="+")
        self._variable_trace_id = self.variable.trace_add("write", self._variable_changed)
        self.bind("<Destroy>", self._remove_variable_trace, add="+")
        self._redraw()

    def _remove_variable_trace(self, event=None):
        if event is not None and event.widget is not self:
            return
        trace_id = getattr(self, "_variable_trace_id", None)
        if trace_id is not None:
            try:
                self.variable.trace_remove("write", trace_id)
            except tk.TclError:
                pass
            self._variable_trace_id = None

    def _track_bounds(self):
        width = max(self.length, self.winfo_width())
        return 9, max(10, width - 9)

    def _value_x(self, value):
        left, right = self._track_bounds()
        fraction = (float(value) - self.minimum) / max(1, self.maximum - self.minimum)
        return left + max(0.0, min(1.0, fraction)) * (right - left)

    def set_gradient_hue(self, hue):
        self.set_gradient_color(hue)

    def set_gradient_color(self, hue, saturation=None, value=None):
        self.gradient_hue = float(hue) % 1.0
        if saturation is not None:
            self.gradient_saturation = max(0.0, min(1.0, float(saturation)))
        if value is not None:
            self.gradient_value = max(0.0, min(1.0, float(value)))
        if self.saturation_gradient or self.brightness_gradient:
            self._redraw()

    def _redraw(self, _event=None):
        try:
            if not self.winfo_exists():
                return
            self.delete("slider-art")
            width = max(self.length, self.winfo_width())
            height = max(26, self.winfo_height())
            scale = self._slider_scale
            image = Image.new("RGBA", (width * scale, height * scale), self._tk_color(self.cget("bg")))
            draw = ImageDraw.Draw(image)
            left, right = self._track_bounds()
            center_y = height // 2
            value_x = self._value_x(self.variable.get())
            if self.hue_gradient or self.saturation_gradient or self.brightness_gradient:
                gradient_width = max(1, round((right - left) * scale) + 1)
                gradient_height = 6 * scale
                hue = round(self.gradient_hue, 4) if (self.saturation_gradient or self.brightness_gradient) else None
                saturation = round(self.gradient_saturation, 4) if self.brightness_gradient else None
                value = round(self.gradient_value, 4) if self.saturation_gradient else None
                gradient_size = (
                    gradient_width, gradient_height, self.hue_gradient,
                    self.saturation_gradient, self.brightness_gradient, hue, saturation, value,
                )
                if self._gradient_size != gradient_size:
                    gradient = Image.new("RGBA", (gradient_width, gradient_height), (0, 0, 0, 0))
                    pixels = gradient.load()
                    for px in range(gradient_width):
                        fraction = px / max(1, gradient_width - 1)
                        if self.hue_gradient:
                            sample_hue, sample_saturation, sample_value = fraction, 1.0, 1.0
                        elif self.saturation_gradient:
                            sample_hue, sample_saturation, sample_value = self.gradient_hue, fraction, self.gradient_value
                        else:
                            sample_hue, sample_saturation, sample_value = self.gradient_hue, self.gradient_saturation, fraction
                        red, green, blue = colorsys.hsv_to_rgb(sample_hue, sample_saturation, sample_value)
                        rgb = (round(red * 255), round(green * 255), round(blue * 255), 255)
                        for py in range(gradient_height):
                            pixels[px, py] = rgb
                    mask = Image.new("L", (gradient_width, gradient_height), 0)
                    ImageDraw.Draw(mask).rounded_rectangle(
                        (0, 0, gradient_width - 1, gradient_height - 1),
                        radius=gradient_height // 2, fill=255,
                    )
                    gradient.putalpha(mask)
                    self._gradient_bitmap = gradient
                    self._gradient_size = gradient_size
                image.alpha_composite(
                    self._gradient_bitmap,
                    (round(left * scale), round(center_y * scale - gradient_height / 2)),
                )
            else:
                track_box = (
                    round(left * scale), round((center_y - 2.5) * scale),
                    round(right * scale), round((center_y + 2.5) * scale),
                )
                draw.rounded_rectangle(track_box, radius=3 * scale, fill=PANEL2)
                if value_x > left:
                    active_box = (
                        track_box[0], track_box[1], round(value_x * scale), track_box[3],
                    )
                    draw.rounded_rectangle(active_box, radius=3 * scale, fill=ACCENT)
            for tick in self.ticks:
                tick_x = self._value_x(tick)
                draw.line(
                    (round(tick_x * scale), round((center_y - 5) * scale),
                     round(tick_x * scale), round((center_y + 5) * scale)),
                    fill=MUTED, width=scale,
                )
            radius = 7 * scale
            draw.ellipse(
                (round(value_x * scale) - radius, center_y * scale - radius,
                 round(value_x * scale) + radius, center_y * scale + radius),
                fill=self._tk_color(self.cget("bg")), outline=ACCENT, width=2 * scale,
            )
            rendered = image.resize((width, height), Image.Resampling.LANCZOS)
            self._slider_photo = ImageTk.PhotoImage(rendered, master=self)
            self.create_image(0, 0, image=self._slider_photo, anchor="nw", tags="slider-art")
        except (tk.TclError, ValueError, TypeError):
            pass

    def _tk_color(self, color):
        red, green, blue = self.winfo_rgb(color)
        return (red >> 8, green >> 8, blue >> 8, 255)

    def _variable_changed(self, *_args):
        self._redraw()

    def _set_value(self, value):
        value = max(self.minimum, min(self.maximum, round(float(value))))
        try:
            current = int(self.variable.get())
        except (ValueError, tk.TclError):
            current = None
        if current == value:
            self._redraw()
            return
        self.variable.set(value)
        if self.command:
            self.command(str(value))

    def _set_from_x(self, x):
        left, right = self._track_bounds()
        fraction = max(0.0, min(1.0, (x - left) / max(1, right - left)))
        self._set_value(self.minimum + fraction * (self.maximum - self.minimum))

    def _press(self, event):
        self._dragging = True
        self.focus_set()
        self._set_from_x(event.x)
        return "break"

    def _drag(self, event):
        if self._dragging:
            self._set_from_x(event.x)
        return "break"

    def _release(self, _event=None):
        self._dragging = False

    def _step(self, amount):
        try:
            current = int(self.variable.get())
        except (ValueError, tk.TclError):
            current = self.minimum
        self._set_value(current + amount)
        return "break"


class DarkNumberSelector(tk.Frame):
    """Compact themed integer input with matching up/down controls."""

    def __init__(self, master, variable, minimum, maximum, width=4, command=None):
        super().__init__(master, bg=EDGE, bd=0, highlightthickness=0, padx=1, pady=1)
        self.variable = variable
        self.minimum = minimum
        self.maximum = maximum
        self.command = command
        self._validate_command = (self.register(self._validate_value), "%P")

        self.entry = tk.Entry(
            self, textvariable=variable, width=width, justify="center",
            bg=PANEL2, fg=TEXT, insertbackground=TEXT,
            selectbackground=ACCENT, selectforeground="white",
            relief="flat", bd=0, highlightthickness=0,
            font=("Segoe UI", 9), validate="key",
            validatecommand=self._validate_command,
        )
        self.entry.pack(side="left", fill="y", ipady=4, padx=(2, 0))
        arrows = tk.Frame(self, bg=EDGE, padx=1)
        arrows.pack(side="left", fill="y")
        self.up_button = tk.Button(
            arrows, text="▲", command=lambda: self.step(1),
            bg=PANEL2, fg=MUTED, activebackground=ACCENT,
            activeforeground="white", relief="flat", bd=0,
            font=("Segoe UI", 6), padx=3, pady=0, cursor="hand2",
            takefocus=False,
        )
        self.up_button.pack(fill="both", expand=True)
        self.down_button = tk.Button(
            arrows, text="▼", command=lambda: self.step(-1),
            bg=PANEL2, fg=MUTED, activebackground=ACCENT,
            activeforeground="white", relief="flat", bd=0,
            font=("Segoe UI", 6), padx=3, pady=0, cursor="hand2",
            takefocus=False,
        )
        self.down_button.pack(fill="both", expand=True, pady=(1, 0))

        self.entry.bind("<FocusOut>", self._normalize)
        self.entry.bind("<KeyRelease>", self._notify)
        self.entry.bind("<Up>", lambda _event: self._keyboard_step(1))
        self.entry.bind("<Down>", lambda _event: self._keyboard_step(-1))

    def _validate_value(self, proposed):
        if proposed == "":
            return True
        if not proposed.isdigit():
            return False
        return int(proposed) <= self.maximum

    def _notify(self, _event=None):
        value = self.variable.get()
        if value != "" and self.command:
            self.command()

    def _normalize(self, _event=None):
        value = self.variable.get()
        try:
            number = int(value)
        except (TypeError, ValueError):
            number = self.minimum
        self.variable.set(str(max(self.minimum, min(self.maximum, number))))
        if self.command:
            self.command()

    def step(self, amount):
        try:
            number = int(self.variable.get())
        except (TypeError, ValueError):
            number = self.minimum
        self.variable.set(str(max(self.minimum, min(self.maximum, number + amount))))
        if self.command:
            self.command()

    def _keyboard_step(self, amount):
        self.step(amount)
        return "break"


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = (
        ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    )


class _RGBQUAD(ctypes.Structure):
    _fields_ = (("rgbBlue", ctypes.c_ubyte), ("rgbGreen", ctypes.c_ubyte), ("rgbRed", ctypes.c_ubyte), ("rgbReserved", ctypes.c_ubyte))


class _BITMAPINFO(ctypes.Structure):
    _fields_ = (("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", _RGBQUAD * 1))


class _GUID(ctypes.Structure):
    _fields_ = (("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8))


class _PROPERTYKEY(ctypes.Structure):
    _fields_ = (("fmtid", _GUID), ("pid", wintypes.DWORD))


class _PROPVARIANT_VALUE(ctypes.Union):
    _fields_ = (("pwszVal", wintypes.LPWSTR), ("pointer", ctypes.c_void_p), ("padding", ctypes.c_ulonglong))


class _PROPVARIANT(ctypes.Structure):
    _anonymous_ = ("value",)
    _fields_ = (
        ("vt", wintypes.WORD), ("wReserved1", wintypes.WORD),
        ("wReserved2", wintypes.WORD), ("wReserved3", wintypes.WORD),
        ("value", _PROPVARIANT_VALUE),
    )


class _ICONINFO(ctypes.Structure):
    _fields_ = (
        ("fIcon", wintypes.BOOL), ("xHotspot", wintypes.DWORD),
        ("yHotspot", wintypes.DWORD), ("hbmMask", wintypes.HBITMAP),
        ("hbmColor", wintypes.HBITMAP),
    )


class _MONITORINFO(ctypes.Structure):
    _fields_ = (
        ("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD),
    )


class _POINT(ctypes.Structure):
    _fields_ = (("x", wintypes.LONG), ("y", wintypes.LONG))


def _guid(value):
    return _GUID.from_buffer_copy(uuid.UUID(value).bytes_le)


def _release_com(pointer):
    if pointer:
        vtable=ctypes.cast(pointer,ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        ctypes.WINFUNCTYPE(ctypes.c_ulong,ctypes.c_void_p)(vtable[2])(pointer)


def _set_window_app_user_model_id(hwnd, app_id):
    """Give a secondary window its own taskbar group identity."""
    ole32 = ctypes.windll.ole32
    shell32 = ctypes.windll.shell32
    ole32.CoInitializeEx.argtypes = (ctypes.c_void_p, wintypes.DWORD)
    ole32.CoInitializeEx.restype = ctypes.c_long
    init_result = ole32.CoInitializeEx(None, 2)  # COINIT_APARTMENTTHREADED
    changed_mode = ctypes.c_uint32(init_result).value == 0x80010106
    if init_result < 0 and not changed_mode:
        return False

    store = ctypes.c_void_p()
    try:
        iid = _guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99")  # IPropertyStore
        shell32.SHGetPropertyStoreForWindow.argtypes = (
            wintypes.HWND, ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p),
        )
        shell32.SHGetPropertyStoreForWindow.restype = ctypes.c_long
        result = shell32.SHGetPropertyStoreForWindow(hwnd, ctypes.byref(iid), ctypes.byref(store))
        if result < 0 or not store:
            return False

        vtable = ctypes.cast(store, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        set_value = ctypes.WINFUNCTYPE(
            ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(_PROPERTYKEY), ctypes.POINTER(_PROPVARIANT),
        )(vtable[6])  # IPropertyStore::SetValue
        commit = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)(vtable[7])  # IPropertyStore::Commit
        key = _PROPERTYKEY()
        key.fmtid = _guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3")  # PKEY_AppUserModel_ID
        key.pid = 5
        app_id_buffer = ctypes.create_unicode_buffer(app_id)
        value = _PROPVARIANT()
        value.vt = 31  # VT_LPWSTR
        value.pwszVal = ctypes.cast(app_id_buffer, wintypes.LPWSTR)
        result = set_value(store, ctypes.byref(key), ctypes.byref(value))
        if result < 0:
            return False
        return commit(store) >= 0
    except Exception:
        return False
    finally:
        _release_com(store)
        if not changed_mode:
            ole32.CoUninitialize()


def _open_in_paint(path):
    """Open a PNG in packaged Microsoft Paint through Windows app activation."""
    ole32=ctypes.windll.ole32
    ole32.CoInitializeEx.argtypes=(ctypes.c_void_p,wintypes.DWORD);ole32.CoInitializeEx.restype=ctypes.c_long
    initialized=ole32.CoInitializeEx(None,2) >= 0
    manager=ctypes.c_void_p()
    try:
        clsid=_guid("45BA127D-10A8-46EA-8AB7-56EA9078943C")
        iid_manager=_guid("2E941141-7F97-4756-BA1D-9DECDE894A3D")
        ole32.CoCreateInstance.argtypes=(ctypes.POINTER(_GUID),ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(_GUID),ctypes.POINTER(ctypes.c_void_p))
        ole32.CoCreateInstance.restype=ctypes.c_long
        hr=ole32.CoCreateInstance(ctypes.byref(clsid),None,4,ctypes.byref(iid_manager),ctypes.byref(manager))
        if hr < 0:raise OSError(f"Windows app activation failed (0x{hr & 0xffffffff:08X}).")
        vtable=ctypes.cast(manager,ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        activate=ctypes.WINFUNCTYPE(ctypes.c_long,ctypes.c_void_p,wintypes.LPCWSTR,wintypes.LPCWSTR,wintypes.DWORD,ctypes.POINTER(wintypes.DWORD))(vtable[3])
        process_id=wintypes.DWORD()
        hr=activate(manager,"Microsoft.Paint_8wekyb3d8bbwe!App",f'"{path}"',0,ctypes.byref(process_id))
        if hr < 0:raise OSError(f"Windows couldn't start Paint (0x{hr & 0xffffffff:08X}).")
    finally:
        _release_com(manager)
        if initialized:ole32.CoUninitialize()


class ChessSnip:
    def __init__(self):
        self.theme_name = _load_saved_theme()
        _activate_palette(self.theme_name)
        self.root = tk.Tk()
        self._launch_direct_capture = "--snip" in sys.argv[1:]
        if self._launch_direct_capture:
            self.root.withdraw()
        self.root.bind_class("Button", "<Enter>", self._button_hover_enter, add="+")
        self.root.bind_class("Button", "<Leave>", self._button_hover_leave, add="+")
        self.root.report_callback_exception = self._report_callback_exception
        self.root.title("ChessSnip")
        self.root.geometry("560x128")
        self.root.minsize(520, 120)
        self.root.configure(bg=INK)
        self._load_app_icon()
        # Tcl/Tk treats spaces as separators in a font description; brace the
        # family name so "Segoe UI" remains a single font family.
        self.root.option_add("*Font", "{Segoe UI} 10")
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self._maximized = False
        self._chess_review_windows = []
        self._restore_geometry = None
        self.image: Image.Image | None = None
        self.photo = None
        self.fit_scale = 1.0
        self.display_origin = (0, 0)
        self.strokes: list[dict] = []
        self._selected_shape = None
        self._shape_transform_drag = None
        self._shape_preview_photos = []
        self._shape_preview_photos_by_key = {}
        self._drawing_preview_photo = None
        self._drawing_preview_after = None
        self._active_highlighter_overlay = None
        self._stroke_render_revision = 0
        self._preview_source_cache_key = None
        self._preview_source_cache = None
        self._preview_canvas_item = None
        self._preview_has_transparency = False
        self.history: list[tuple] = []
        self.redo_history: list[tuple] = []
        # Marks outside the captured image remain visible on the editor canvas
        # but are never included in the saved image or clipboard output.
        self.canvas_only_strokes: list[dict] = []
        self.tool = tk.StringVar(value="pen")
        self._eyedropper_return_tool = "pen"
        self._pipette_overlay = None
        self._pipette_photo = None
        self._pipette_copy_notice_after = None
        self.color = tk.StringVar(value="#000000")
        self.hue = tk.IntVar(master=self.root, value=0)
        self.saturation = tk.IntVar(master=self.root, value=100)
        self.brightness = tk.IntVar(master=self.root, value=100)
        self.hue_slider = None
        self.saturation_slider = None
        self.brightness_slider = None
        self.zoom_factor = 1.0
        self.color_buttons = {}
        self.color_picker_button = None
        self.brush_cursor = None
        self.brush_cursor_items = None
        self.system_cursor_hide_count = 0
        self.size_badge = None
        self.pointer_inside_canvas = False
        self.width = tk.IntVar(value=4)
        self.capture_mode = tk.StringVar(value="Rectangle")
        self.delay = tk.StringVar(value="0")
        self.status = tk.StringVar(value="Choose a capture mode, or press Ctrl+Shift+X")
        self._drawing = None
        self._drawing_preview_item = None
        self._canvas_only_preview_photo = None
        self._erasing = False
        self._erase_changed = False
        self._rotation_drag = None
        self._rotation_release_after = None
        self._rotation_base_image = None
        # The rotated screenshot and its marks stay transparent over this view.
        self.background_layer = None
        self.annotation_layer = None
        self._rotation_base_marks = None
        self._rotation_base_angle = 0
        self._rotation_angle = 0
        self._rotation_value = tk.IntVar(value=0)
        self._rotation_slider_resetting = False
        self._rotation_preview_photo = None
        self._rotation_preview_item = None
        self._rotation_preview_border = None
        self._crop_start = None
        self._crop_box = None
        self._crop_drag_fixed = None
        self._crop_drag_offset = None
        self._crop_drag_edge = None
        self._crop_return_tool = "pen"
        self._crop_toolbar_snapshot = None
        self._crop_toolbar_height_state = None
        self._crop_preview_base = None
        self._crop_dimmed_preview = None
        self._crop_overlay_photo = None
        self.crop_actions = None
        self.crop_actions_separator = None
        self.crop_accept_button = None
        self.crop_cancel_button = None
        self._size_preview_after = None
        self._size_preview_items = None
        self._timer = None
        self._taskbar_list = None
        self._taskbar_com_initialized = False
        self._countdown_overlay_icons = []
        self._countdown_original_window_icons = None
        self._taskbar_hwnd = None
        self._countdown_total_seconds = 0
        self.stop_countdown_button = None
        self.editor_separator = None
        self.undo_button = None
        self.redo_button = None
        self.theme_button = None
        self._last_canvas_size = None
        self._preview_redraw_after = None
        self._clipboard_after = None
        self._clipboard_revision = 0
        self._clipboard_busy = False
        self._recognition_busy = False
        self._recognition_mode = False
        self._recognition_animation_after = None
        self._recognition_animation_offset = 0
        self.recognition_button = None
        self._recognition_regions = []
        self._recognition_selection = []
        self._recognition_selection_photos = []
        self._recognition_base_preview = None
        self._recognition_drag = None
        self._recognition_last_text_click = None
        self._recognition_dim_photo = None
        self._theme_update_in_progress = False
        self._button_icon_art = {}
        self._recognition_qr_copy_icon = self._create_qr_copy_icon()
        self._recognition_hover_qr_index = None
        self._recognition_tooltip = None
        self._recognition_tooltip_after = None
        self._recognition_tooltip_position = None
        self._recognition_copy_notice_after = None
        self._active_choice_popup = None
        self._suspended_shape_popup = None
        self._suspended_shape_popup_place = None
        self._shape_color_edit_history_pushed = False
        self._shape_fill_checkbox = None
        self._choice_popup_animation_after = None
        self._choice_popup_animating = False
        self._color_palette_close_after = None
        self._tool_icon_images = self._create_tool_icon_images(TEXT)
        self._capture_mode_icon_images = self._create_capture_mode_icon_images(TEXT)
        self._build_ui()
        self.root.bind_all("<Map>", self._round_button_on_map, add="+")
        self.root.bind_all("<Motion>", self._choice_popup_motion, add="+")
        self.root.bind_all("<ButtonPress-1>", self._choice_popup_click_away, add="+")
        self.root.bind_all("<Escape>", self._choice_popup_escape, add="+")
        self.root.bind_all("<Delete>", self._delete_selected_shape, add="+")
        self.root.after(100, self._enable_taskbar_button)
        self.root.bind("<Control-Shift-x>", lambda e: self.begin_capture())
        self.root.bind("<Control-s>", lambda e: self.save_as())
        self.root.bind("<Control-c>", self._copy_recognition_or_image)
        self.root.bind("<Control-z>", lambda e: self.undo())
        self.root.bind("<Control-y>", lambda e: self.redo())
        self.root.bind("<Control-Shift-z>", lambda e: self.redo())
        self.root.bind("<Control-Shift-p>", lambda e: self.chess_to_pgn())
        self._register_hotkey()
        self.root.after(100, self._poll_hotkey)
        if self._launch_direct_capture:
            self.root.after_idle(self.begin_capture)

    def _load_app_icon(self):
        self._app_icon_photos = []
        self._brand_icon_photo = None
        try:
            with Image.open(_resource_path("ChessSnip.png")) as source:
                artwork = source.convert("RGBA")
            self._app_icon_photos = [
                ImageTk.PhotoImage(artwork.resize((size, size), Image.Resampling.LANCZOS), master=self.root)
                for size in (16, 32, 48, 64)
            ]
            self._brand_icon_photo = ImageTk.PhotoImage(
                artwork.resize((26, 26), Image.Resampling.LANCZOS), master=self.root,
            )
            self.root.iconphoto(True, *self._app_icon_photos)
        except Exception:
            # Keep the app usable if the optional artwork is missing.
            self._app_icon_photos = []
            self._brand_icon_photo = ImageTk.PhotoImage(
                Image.new("RGBA", (26, 26), ACCENT), master=self.root,
            )

    def _create_tool_icon_images(self, color):
        """Create crisp, theme-colored toolbar icons without font glyphs."""
        scale = 4
        size = 20
        images = {}
        for name in ("pen", "highlighter", "eraser", "rectangle", "circle", "line", "arrow", "shapes", "crop", "eyedropper", "recognition"):
            bitmap = Image.new("RGBA", (size * scale, size * scale), (0, 0, 0, 0))
            draw = ImageDraw.Draw(bitmap)

            def xy(points):
                return [(round(x * scale), round(y * scale)) for x, y in points]

            width = 2 * scale
            if name == "pen":
                draw.polygon(xy(((3, 16), (5, 13), (12, 6), (16, 10), (8, 18), (5, 19))), fill=color)
                draw.polygon(xy(((12, 6), (15, 3), (18, 6), (16, 10))), fill=color)
                draw.polygon(xy(((3, 16), (5, 19), (2, 19))), fill=color)
                draw.line(xy(((6, 13), (10, 17))), fill=INK, width=scale)
                draw.line(xy(((3, 16), (2, 19))), fill=INK, width=scale)
            elif name == "highlighter":
                draw.line(xy(((3, 18), (10, 18))), fill="#f5c451", width=3 * scale)
                draw.polygon(xy(((5, 12), (11, 6), (16, 11), (10, 17))), fill=color)
                draw.polygon(xy(((5, 12), (10, 17), (7, 18), (3, 14))), fill="#f5c451")
                draw.line(xy(((11, 6), (16, 11))), fill=PANEL2, width=scale)
            elif name == "eraser":
                draw.polygon(xy(((4, 13), (12, 5), (17, 10), (9, 18))), outline=color, fill=color)
                draw.line(xy(((8, 9), (13, 14))), fill=PANEL2, width=scale)
                draw.line(xy(((3, 18), (17, 18))), fill=color, width=scale)
            elif name == "rectangle":
                draw.rectangle((4 * scale, 4 * scale, 16 * scale, 16 * scale), outline=color, width=width)
            elif name == "circle":
                draw.ellipse((3 * scale, 3 * scale, 17 * scale, 17 * scale), outline=color, width=width)
            elif name == "line":
                draw.line(xy(((3, 16), (17, 4))), fill=color, width=width)
            elif name == "arrow":
                draw.line(xy(((3, 16), (14, 5))), fill=color, width=width)
                draw.polygon(xy(((10, 5), (17, 3), (15, 10))), fill=color)
            elif name == "shapes":
                draw.rectangle((2 * scale, 9 * scale, 12 * scale, 19 * scale), outline=color, width=width)
                draw.ellipse((8 * scale, 1 * scale, 18 * scale, 11 * scale), outline=color, width=width)
            elif name == "crop":
                # Recreate the supplied crop mark: a central square with each
                # side extending past one of its corners.
                crop_width = scale
                crop_segments = (
                    ((1.5, 4.85), (13.45, 4.85)),
                    ((4.56, 1.8), (4.56, 13.68)),
                    ((13.45, 4.85), (13.45, 16.73)),
                    ((4.56, 13.68), (16.02, 13.68)),
                )
                cap_radius = crop_width / 2
                for start, end in crop_segments:
                    draw.line(xy((start, end)), fill=color, width=crop_width)
                    for px, py in (start, end):
                        center_x, center_y = px * scale, py * scale
                        draw.ellipse(
                            (
                                center_x - cap_radius, center_y - cap_radius,
                                center_x + cap_radius, center_y + cap_radius,
                            ),
                            fill=color,
                        )
            elif name == "eyedropper":
                draw.line(xy(((6, 14), (13, 7))), fill=color, width=3 * scale)
                draw.line(xy(((4, 16), (8, 12))), fill=color, width=width)
                draw.polygon(xy(((12, 5), (14, 3), (18, 7), (16, 9))), outline=color, fill=color)
                draw.ellipse((3 * scale, 15 * scale, 7 * scale, 19 * scale), outline=color, width=scale)
            elif name == "recognition":
                # A page with detected text lines and QR modules communicates
                # both local OCR and code scanning in one compact toolbar icon.
                draw.line(xy(((3, 7), (3, 3), (7, 3))), fill=color, width=width)
                draw.line(xy(((13, 3), (17, 3), (17, 7))), fill=color, width=width)
                draw.line(xy(((3, 13), (3, 17), (7, 17))), fill=color, width=width)
                draw.line(xy(((13, 17), (17, 17), (17, 13))), fill=color, width=width)
                draw.line(xy(((5, 7), (10, 7))), fill=color, width=width)
                draw.line(xy(((5, 10), (10, 10))), fill=color, width=width)
                draw.rectangle((12 * scale, 8 * scale, 13 * scale, 9 * scale), fill=color)
                draw.rectangle((15 * scale, 8 * scale, 17 * scale, 10 * scale), fill=color)
                draw.rectangle((12 * scale, 12 * scale, 14 * scale, 14 * scale), fill=color)
                draw.rectangle((16 * scale, 13 * scale, 17 * scale, 14 * scale), fill=color)
            rendered = bitmap.resize(
                (24, 24) if name == "crop" else (size, size),
                Image.Resampling.LANCZOS,
            )
            images[name] = self._register_button_icon(
                ImageTk.PhotoImage(rendered, master=self.root), bitmap, rendered.size,
            )
        return images

    def _register_button_icon(self, photo, artwork, display_size=None):
        """Keep each source icon and its artwork available for separate rendering."""
        registry = getattr(self, "_button_icon_art", None)
        if registry is None:
            registry = self._button_icon_art = {}
        registry[str(photo)] = (artwork.copy(), display_size or artwork.size, photo)
        return photo

    def _create_shape_menu_icon(self, shape, color, size=36):
        """Render a large antialiased icon for one Shapes menu item."""
        supersample = 4
        factor = supersample * size / 20
        bitmap = Image.new("RGBA", (size * supersample, size * supersample), (0, 0, 0, 0))
        draw = ImageDraw.Draw(bitmap)
        width = max(2, round(1.8 * factor))

        def box(bounds):
            return tuple(round(value * factor) for value in bounds)

        if shape == "rectangle":
            draw.rectangle(box((3, 4, 17, 16)), outline=color, width=width)
        elif shape == "circle":
            draw.ellipse(box((3, 3, 17, 17)), outline=color, width=width)
        elif shape == "line":
            draw.line(box((3, 16, 17, 4)), fill=color, width=width)
        elif shape == "cross":
            draw.line(box((4, 4, 16, 16)), fill=color, width=width)
            draw.line(box((16, 4, 4, 16)), fill=color, width=width)
        elif shape == "tick":
            draw.line(
                box((3, 10, 8, 15, 17, 5)), fill=color,
                width=width, joint="curve",
            )
        else:
            draw.line(box((3, 16, 14, 5)), fill=color, width=width)
            arrowhead = [
                (round(x * factor), round(y * factor))
                for x, y in ((9, 5), (17, 3), (15, 11))
            ]
            draw.polygon(arrowhead, fill=color)
        rendered = bitmap.resize((size, size), Image.Resampling.LANCZOS)
        return self._register_button_icon(
            ImageTk.PhotoImage(rendered, master=self.root), bitmap, rendered.size,
        )

    def _create_qr_copy_icon(self, size=28):
        """Draw an antialiased copy-link button for detected QR codes."""
        scale = 4
        bitmap = Image.new("RGBA", (size * scale, size * scale), (0, 0, 0, 0))
        draw = ImageDraw.Draw(bitmap)
        margin = 1 * scale
        draw.rounded_rectangle(
            (margin, margin, size * scale - margin - 1, size * scale - margin - 1),
            radius=5 * scale, fill="#0788d1", outline=(255, 255, 255, 250),
            width=scale,
        )
        white = (255, 255, 255, 255)
        # Two interlocking rounded links remain readable at the 28 px display size.
        draw.rounded_rectangle(
            (6 * scale, 11 * scale, 15 * scale, 19 * scale),
            radius=4 * scale, outline=white, width=2 * scale,
        )
        draw.rounded_rectangle(
            (13 * scale, 8 * scale, 22 * scale, 16 * scale),
            radius=4 * scale, outline=white, width=2 * scale,
        )
        draw.line(
            (11 * scale, 16 * scale, 17 * scale, 10 * scale),
            fill=white, width=2 * scale,
        )
        rendered = bitmap.resize((size, size), Image.Resampling.LANCZOS)
        return self._register_button_icon(
            ImageTk.PhotoImage(rendered, master=self.root), bitmap, rendered.size,
        )

    def _create_theme_toggle_icon(self):
        """Draw a clear, generously sized sun or moon for the theme toggle."""
        scale = 4
        size = 24
        bitmap = Image.new("RGBA", (size * scale, size * scale), (0, 0, 0, 0))
        draw = ImageDraw.Draw(bitmap)
        if self.theme_name == "dark":
            color = "#f5c451"
            center = size * scale // 2
            inner_radius = 5 * scale
            outer_radius = 9 * scale
            draw.ellipse(
                (center - inner_radius, center - inner_radius, center + inner_radius, center + inner_radius),
                fill=color,
            )
            for angle in range(0, 360, 45):
                radians = math.radians(angle)
                start = outer_radius - 2 * scale
                end = outer_radius
                draw.line(
                    (
                        center + round(math.cos(radians) * start),
                        center + round(math.sin(radians) * start),
                        center + round(math.cos(radians) * end),
                        center + round(math.sin(radians) * end),
                    ),
                    fill=color,
                    width=2 * scale,
                )
        else:
            color = "#6573a5"
            draw.ellipse((3 * scale, 2 * scale, 21 * scale, 20 * scale), fill=color)
            draw.ellipse((9 * scale, 0, 25 * scale, 16 * scale), fill=(0, 0, 0, 0))

        return ImageTk.PhotoImage(bitmap.resize((size, size), Image.Resampling.LANCZOS), master=self.root)

    def _create_capture_mode_icon_images(self, color):
        """Create consistent icons for the available capture modes."""
        scale = 4
        size = 22
        art_scale = scale * size / 20
        images = {}
        for mode in ("Rectangle", "Freeform", "Window", "Full screen"):
            bitmap = Image.new("RGBA", (size * scale, size * scale), (0, 0, 0, 0))
            draw = ImageDraw.Draw(bitmap)

            def xy(points):
                return [(round(x * art_scale), round(y * art_scale)) for x, y in points]

            stroke = round(2 * art_scale)
            if mode == "Rectangle":
                for points in (
                    ((3, 7), (3, 3), (7, 3)), ((13, 3), (17, 3), (17, 7)),
                    ((3, 13), (3, 17), (7, 17)), ((13, 17), (17, 17), (17, 13)),
                ):
                    draw.line(xy(points), fill=color, width=stroke)
            elif mode == "Window":
                draw.rectangle((round(2 * art_scale), round(3 * art_scale), round(18 * art_scale), round(17 * art_scale)), outline=color, width=stroke)
                draw.line(xy(((3, 7), (17, 7))), fill=color, width=stroke)
                for center_x in (5, 8, 11):
                    draw.ellipse((round(center_x * art_scale - art_scale / 2),
                                  round(4 * art_scale - art_scale / 2),
                                  round(center_x * art_scale + art_scale / 2),
                                  round(4 * art_scale + art_scale / 2)), fill=color)
            elif mode == "Freeform":
                draw.line(
                    xy(((4, 5), (8, 3), (15, 4), (17, 9), (14, 15), (9, 17), (4, 13), (3, 8), (4, 5))),
                    fill=color, width=stroke, joint="curve",
                )
            else:
                draw.rectangle((round(3 * art_scale), round(4 * art_scale), round(17 * art_scale), round(16 * art_scale)), outline=color, width=stroke)

            rendered = bitmap.resize((size, size), Image.Resampling.LANCZOS)
            images[mode] = self._register_button_icon(
                ImageTk.PhotoImage(rendered, master=self.root), bitmap, rendered.size,
            )
        return images

    def _refresh_capture_mode_icon_images(self):
        old_images = getattr(self, "_capture_mode_icon_images", {})
        self._capture_mode_icon_images = self._create_capture_mode_icon_images(TEXT)
        if self.mode_btn:
            self._update_mode_label()
        self._release_button_icon_art(old_images)

    def _refresh_tool_icon_images(self):
        old_images = getattr(self, "_tool_icon_images", {})
        self._tool_icon_images = self._create_tool_icon_images(TEXT)
        self._update_history_buttons()
        variant_vars = getattr(self, "_tool_variant_vars", {})
        for name, button in getattr(self, "tool_buttons", {}).items():
            try:
                icon_name = (
                    name if name == "shapes"
                    else variant_vars[name].get() if name in variant_vars
                    else name
                )
                button.configure(image=self._tool_icon_images[icon_name], compound=tk.LEFT)
            except tk.TclError:
                pass
        for group, (button, _label) in getattr(self, "_tool_variant_buttons", {}).items():
            try:
                icon_name = (
                    "shapes" if group == "shapes"
                    else self._tool_variant_vars[group].get()
                )
                button.configure(image=self._tool_icon_images[icon_name], compound=tk.LEFT)
            except tk.TclError:
                pass
        if getattr(self, "pipette_button", None):
            try:
                self.pipette_button.configure(image=self._tool_icon_images["eyedropper"], compound=tk.LEFT)
            except tk.TclError:
                pass
        if getattr(self, "recognition_button", None):
            try:
                self.recognition_button.configure(image=self._tool_icon_images["recognition"])
            except tk.TclError:
                pass
        self._release_button_icon_art(old_images)

    def _release_button_icon_art(self, images):
        registry = getattr(self, "_button_icon_art", {})
        for photo in images.values():
            entry = registry.get(str(photo))
            if entry is not None and len(entry) > 2 and entry[2] is photo:
                registry.pop(str(photo), None)

    def _position_window_at_main(self, window):
        """Center an app window over the main window, including on other monitors."""
        try:
            self.root.update_idletasks()
            window.update_idletasks()
            width = max(window.winfo_width(), window.winfo_reqwidth())
            height = max(window.winfo_height(), window.winfo_reqheight())
            x = self.root.winfo_rootx() + (self.root.winfo_width() - width) // 2
            y = self.root.winfo_rooty() + (self.root.winfo_height() - height) // 2
            try:
                user32 = ctypes.windll.user32
                user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
                user32.GetAncestor.restype = wintypes.HWND
                hwnd = user32.GetAncestor(window.winfo_id(), 2) or window.winfo_id()
                user32.SetWindowPos.argtypes = (
                    wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                    ctypes.c_int, ctypes.c_int, wintypes.UINT,
                )
                user32.SetWindowPos.restype = wintypes.BOOL
                placed = user32.SetWindowPos(hwnd, None, x, y, 0, 0, 0x0015)
            except Exception:
                placed = False
            if not placed:
                window.geometry(f"+{max(0, x)}+{max(0, y)}")
        except tk.TclError:
            pass

    def _build_ui(self):
        # Custom title bar replaces the operating system caption controls.
        self.titlebar=tk.Frame(self.root,bg=PANEL,height=40,highlightbackground=EDGE,highlightthickness=1)
        self.titlebar.pack(fill="x")
        brand=tk.Frame(self.titlebar,bg=PANEL);brand.pack(side="left",padx=(14,0))
        brand_icon = tk.Label(brand, image=self._brand_icon_photo, bg=PANEL, bd=0)
        brand_icon.pack(side="left",padx=(0,9),pady=4)
        brand_icon.image = self._brand_icon_photo
        tk.Label(brand,text="ChessSnip",bg=PANEL,fg=TEXT,font=("Segoe UI Semibold",10)).pack(side="left")
        # Reserve enough room for all window controls. The antialiased button
        # renderer uses image-backed buttons, whose width unit differs from
        # text buttons; a fixed control rail prevents Tk from collapsing it.
        controls=tk.Frame(self.titlebar,bg=PANEL,width=184,height=40)
        controls.pack(side="right",fill="y")
        controls.pack_propagate(False)
        self._theme_toggle_icon = self._create_theme_toggle_icon()
        # A Label avoids the white native button face Windows can paint behind
        # transparent theme icons, especially when switching back to dark mode.
        self.theme_button=tk.Label(
            controls, image=self._theme_toggle_icon,
            bg=PANEL, bd=0, padx=8, pady=2, cursor="hand2",
            highlightthickness=0,
        )
        self.theme_button.bind("<Button-1>", lambda _event: self.toggle_theme())
        self.theme_button.pack(side="left", fill="y", padx=(0, 4))
        self._add_tooltip(self.theme_button, "Switch between the dark and light themes")
        minimize_button=tk.Button(controls,text="—",command=self._minimize_window,bg=PANEL,fg=TEXT,activebackground=PANEL2,activeforeground=TEXT,relief="flat",bd=0,width=5,cursor="hand2",font=("Segoe UI",11))
        minimize_button.pack(side="left",fill="y")
        minimize_button._chesssnip_aa_fixed_size = (46, 38)
        self._bind_title_button_hover(minimize_button)
        self._add_tooltip(minimize_button, "Minimize ChessSnip")
        self.maximize_button=tk.Button(controls,text="□",command=self._toggle_maximize,bg=PANEL,fg=TEXT,activebackground=PANEL2,activeforeground=TEXT,relief="flat",bd=0,width=5,cursor="hand2",font=("Segoe UI",11))
        self.maximize_button.pack(side="left",fill="y")
        self.maximize_button._chesssnip_aa_fixed_size = (46, 38)
        self._bind_title_button_hover(self.maximize_button)
        self._add_tooltip(self.maximize_button, "Maximize or restore ChessSnip")
        close_button=tk.Button(controls,text="×",command=self.close,bg=PANEL,fg=TEXT,activebackground="#c42b1c",activeforeground="white",relief="flat",bd=0,width=5,cursor="hand2",font=("Segoe UI",12))
        close_button.pack(side="left",fill="y")
        close_button._chesssnip_aa_fixed_size = (46, 38)
        self._bind_title_button_hover(close_button, "#c42b1c", "white")
        self._add_tooltip(close_button, "Close ChessSnip")
        for target in (self.titlebar,brand,*brand.winfo_children()):
            target.bind("<ButtonPress-1>",self._titlebar_drag)
            target.bind("<B1-Motion>",self._titlebar_move)
            target.bind("<Double-Button-1>",lambda _e:self._toggle_maximize())
        self._add_tooltip(brand, "Drag to move · double-click to maximize or restore")
        # Capture controls
        controls = tk.Frame(self.root, bg=PANEL, padx=12, pady=5)
        controls.pack(fill="x", padx=12, pady=5)
        self.capture_controls = controls
        new_snip_button = tk.Button(
            controls, text="＋  New", command=self.begin_capture,
            bg=ACCENT, fg="white", activebackground="#7770ea",
            activeforeground="white", relief="flat", bd=0, padx=17, pady=7,
            font=("Segoe UI Semibold", 10), cursor="hand2",
        )
        new_snip_button.pack(side="left", padx=(0, 8))
        self._add_tooltip(new_snip_button, "Start a snip · Ctrl+Shift+X")
        self._mode_menu(controls)
        tk.Label(controls, text="Delay", bg=PANEL, fg=MUTED).pack(side="left", padx=(8, 4))
        delaybox = DarkNumberSelector(controls, self.delay, 0, 60, width=3)
        delaybox.pack(side="left")
        self._add_tooltip(delaybox, "Wait before capture so you can open menus or tooltips")
        tk.Label(controls, text="sec", bg=PANEL, fg=MUTED).pack(side="left", padx=(4, 0))
        self.stop_countdown_button = tk.Button(
            controls, text="■  Stop", command=self.cancel_countdown,
            bg="#c42b1c", fg="white", activebackground="#a51f14",
            activeforeground="white", relief="flat", bd=0, padx=10, pady=6,
            font=("Segoe UI Semibold", 9), cursor="hand2",
        )
        self._add_tooltip(self.stop_countdown_button, "Cancel the delayed snip")
        # Keep capture and editing controls together in one glanceable row.
        self.editor_separator = tk.Frame(controls, bg=EDGE, width=1, height=24)
        editorbar = tk.Frame(controls, bg=PANEL, padx=4, pady=0)
        self.editorbar=editorbar
        toolbar_row = tk.Frame(editorbar, bg=PANEL)
        self.toolbar_row = toolbar_row
        toolbar_row.pack(fill="x")
        history_controls = tk.Frame(toolbar_row, bg=PANEL)
        self.undo_button = tk.Button(
            history_controls, text="↶",
            command=self.undo, bg=PANEL, fg=MUTED,
            activebackground=PANEL2, activeforeground=TEXT, relief="flat", bd=0,
            padx=6, pady=4, cursor="hand2", font=("Segoe UI", 11), takefocus=0,
        )
        self.undo_button.pack(side="left", padx=(0, 2))
        self._add_tooltip(self.undo_button, "Undo the last image edit · Ctrl+Z")
        self.redo_button = tk.Button(
            history_controls, text="↷",
            command=self.redo, bg=PANEL, fg=MUTED,
            activebackground=PANEL2, activeforeground=TEXT, relief="flat", bd=0,
            padx=6, pady=4, cursor="hand2", font=("Segoe UI", 11), takefocus=0,
        )
        self.redo_button.pack(side="left")
        self._add_tooltip(self.redo_button, "Redo the last undone edit · Ctrl+Y")
        history_controls.pack(side="right", padx=(6, 0))
        tk.Frame(toolbar_row, bg=EDGE, width=1, height=22).pack(side="right", padx=(5, 6))
        tool_area = tk.Frame(toolbar_row, bg=PANEL)
        tool_area.pack(side="left", fill="both", expand=True)
        toolrow = tk.Frame(tool_area, bg=PANEL)
        toolrow.place(relx=0.5, rely=0.5, anchor="center")
        self.tool_buttons = {}
        tool_tips = {
            "pen": "Choose Pen or Highlighter",
            "eraser": "Eraser",
            "shapes": "Shapes",
            "crop": "Crop image",
        }
        self._tool_variant_choices = {}
        self._tool_variant_buttons = {}
        self._tool_variant_vars = {}
        self._tool_variant_labels = {
            "pen": {"pen": "Pen", "highlighter": "Highlight"},
            "shapes": {
                "rectangle": "Rectangle", "circle": "Circle",
                "line": "Straight line", "arrow": "Arrow",
                "cross": "Cross", "tick": "Tick",
            },
        }
        self._make_variant_tool(
            toolrow, "pen", "Pen",
            (("pen", "Pen", "pen"), ("highlighter", "Highlighter", "highlighter")),
            tool_tips["pen"],
        )
        for name, label in (("eraser", "Eraser"),):
            slot = tk.Frame(toolrow, bg=PANEL, width=38, height=32)
            slot.pack(side="left", padx=(0, 2))
            slot.pack_propagate(False)
            b = tk.Button(slot, image=self._tool_icon_images[name], command=lambda n=name: self.set_tool(n), relief="flat", bd=0, padx=2, pady=2, cursor="hand2")
            b.pack(fill="both", expand=True); self.tool_buttons[name] = b
            self._add_tooltip(b, tool_tips[name])
        tk.Frame(toolrow, bg=EDGE, width=1, height=22).pack(side="left", padx=4)
        self._make_variant_tool(
            toolrow, "shapes", "Shapes",
            (
                ("rectangle", "Rectangle", "rectangle"),
                ("circle", "Circle", "circle"),
                ("line", "Straight line", "line"),
                ("arrow", "Arrow", "arrow"),
                ("cross", "Cross", "cross"),
                ("tick", "Tick", "tick"),
            ),
            tool_tips["shapes"],
        )
        self.shape_fill_var = tk.BooleanVar(master=self.root, value=False)
        self.shape_toolbar_separator = tk.Frame(toolrow, bg=EDGE, width=1, height=22)
        self.shape_toolbar_separator.pack(side="left", padx=4)
        crop_slot = tk.Frame(toolrow, bg=PANEL, width=38, height=32)
        crop_slot.pack(side="left", padx=(0, 2))
        crop_slot.pack_propagate(False)
        b = tk.Button(
            crop_slot, image=self._tool_icon_images["crop"],
            command=lambda: self.set_tool("crop"), bg=PANEL2, fg=TEXT,
            activebackground=EDGE, activeforeground=TEXT,
            relief="flat", bd=0, padx=2, pady=2, cursor="hand2",
        )
        b.pack(fill="both", expand=True); self.tool_buttons["crop"] = b
        self._add_tooltip(b, tool_tips["crop"])
        self.crop_actions = tk.Frame(toolrow, bg=PANEL)
        self.crop_accept_button = tk.Button(
            self.crop_actions, text="✓ Accept", command=self._accept_crop,
            bg="#218838", fg="white", activebackground="#19692c", activeforeground="white",
            relief="flat", bd=0, padx=7, pady=4, cursor="hand2",
            font=("Segoe UI", 9),
        )
        self.crop_accept_button.pack(side="left", padx=(0, 3))
        self.crop_cancel_button = tk.Button(
            self.crop_actions, text="× Cancel", command=self._cancel_crop,
            bg="#c42b1c", fg="white", activebackground="#a51f14", activeforeground="white",
            relief="flat", bd=0, padx=7, pady=4, cursor="hand2",
            font=("Segoe UI", 9),
        )
        self.crop_cancel_button.pack(side="left")
        self._add_tooltip(self.crop_accept_button, "Apply this crop")
        self._add_tooltip(self.crop_cancel_button, "Cancel cropping · Esc")
        self.crop_actions.pack_forget()
        self.crop_actions_separator = tk.Frame(toolrow, bg=EDGE, width=1, height=22)
        self.crop_actions_separator.pack(side="left", padx=4)
        recognition_slot = tk.Frame(toolrow, bg=PANEL, width=38, height=32)
        recognition_slot.pack(side="left", padx=(0, 2))
        recognition_slot.pack_propagate(False)
        self.recognition_button = tk.Button(
            recognition_slot, image=self._tool_icon_images["recognition"],
            command=self._recognition_tool_clicked, bg=PANEL2, fg=TEXT,
            activebackground=EDGE, activeforeground=TEXT,
            relief="flat", bd=0, padx=2, pady=2, cursor="hand2",
        )
        self.recognition_button.pack(fill="both", expand=True)
        self._add_tooltip(self.recognition_button, "Scan text and QR codes · click again to exit selection mode")
        self.recognition_toolbar_separator = tk.Frame(toolrow, bg=EDGE, width=1, height=22)
        self.recognition_toolbar_separator.pack(side="left", padx=4)
        pipette_slot = tk.Frame(toolrow, bg=PANEL, width=38, height=32)
        pipette_slot.pack(side="left", padx=(0, 2))
        pipette_slot.pack_propagate(False)
        self.pipette_button = tk.Button(
            pipette_slot, image=self._tool_icon_images["eyedropper"], command=lambda: self.set_tool("eyedropper"),
            bg=PANEL2, fg=TEXT, activebackground=EDGE, activeforeground=TEXT,
            relief="flat", bd=0, padx=2, pady=2, cursor="hand2",
        )
        self.pipette_button.pack(fill="both", expand=True)
        self._add_tooltip(self.pipette_button, "Pick a color anywhere on screen · click to copy its hex code")
        self._palette_color_names = {
            "#ff4b62": "Pink-red", "#ff8a3d": "Orange", "#f5c451": "Yellow",
            "#a8d84f": "Lime", "#4bd4aa": "Mint", "#40c8dd": "Cyan",
            "#66a5ff": "Blue", "#a46bff": "Violet",
            "#b92e42": "Dark red", "#bc5f24": "Dark orange",
            "#6b8d2e": "Olive", "#24876e": "Dark mint",
            "#3b65a2": "Dark blue", "#69439c": "Dark violet",
            "#ff9aa6": "Light pink", "#ffc096": "Light orange", "#ffff00": "Bright yellow",
            "#d2e8a5": "Light lime", "#9be5d1": "Light mint", "#9de1ea": "Light cyan",
            "#a5c8ff": "Light blue", "#c5a5ef": "Light violet",
            "#000000": "Black", "#222222": "Near black", "#444444": "Charcoal",
            "#666666": "Dark gray", "#888888": "Gray", "#aaaaaa": "Light gray",
            "#cccccc": "Silver", "#ffffff": "White",
        }
        color_picker_slot = tk.Frame(toolrow, bg=PANEL, width=38, height=32)
        color_picker_slot.pack(side="left", padx=(0, 2))
        color_picker_slot.pack_propagate(False)
        self.color_picker_button = tk.Button(
            color_picker_slot, command=self._popup_color_palette,
            bg=self.color.get(), activebackground=self.color.get(),
            relief="flat", bd=0, padx=0, pady=0, cursor="hand2",
            highlightthickness=1, highlightbackground=EDGE, highlightcolor=EDGE,
        )
        self.color_picker_button.pack(fill="both", expand=True)
        self._add_tooltip(self.color_picker_button, "Choose an annotation color")
        self.color_picker_toolbar_separator = tk.Frame(toolrow, bg=EDGE, width=1, height=22)
        self.color_picker_toolbar_separator.pack(side="left", padx=4)
        # Put color tools in the former Crop/OCR group, and move Crop/OCR to
        # the former color-tool group while keeping each pair's order.
        color_picker_slot.pack_configure(before=crop_slot)
        pipette_slot.pack_configure(before=crop_slot)
        self.color_picker_toolbar_separator.pack_configure(before=crop_slot)
        tk.Label(toolrow, text="SIZE", bg=PANEL, fg=MUTED, font=("Segoe UI", 8, "bold")).pack(side="left", padx=(0, 4))
        self.size_slider = ModernSlider(toolrow, self.width, 1, 30, length=84)
        self.size_slider.pack(side="left", padx=(0, 3))
        self.size_value = tk.StringVar(master=self.root, value=str(self.width.get()))
        tk.Label(toolrow, textvariable=self.size_value, bg=PANEL, fg=TEXT, font=("Segoe UI Semibold", 9), width=3, anchor="center").pack(side="left", padx=(0, 2))
        self.width.trace_add("write", lambda *_: self.size_value.set(str(self.width.get())))
        self._add_tooltip(self.size_slider, "Set pen, highlighter, and eraser width")
        tk.Frame(toolrow, bg=EDGE, width=1, height=22).pack(side="left", padx=4)
        tk.Label(toolrow, text="↻", bg=PANEL, fg=MUTED, font=("Segoe UI", 12, "bold")).pack(side="left", padx=(0, 3))
        self.rotation_slider = ModernSlider(
            toolrow, self._rotation_value, 0, 360, length=104,
            command=self._rotation_slider_changed, ticks=(0, 90, 180, 270, 360),
        )
        self.rotation_slider.pack(side="left", padx=(0, 2))
        self.rotation_angle_label = tk.Label(
            toolrow, text="0°", bg=PANEL, fg=TEXT,
            font=("Segoe UI Semibold", 9), width=5, anchor="w",
        )
        self.rotation_angle_label.pack(side="left")
        self._update_history_buttons()
        self._add_tooltip(self.rotation_slider, "Set the image angle from 0° to 360°; it snaps near each 90° mark, and intermediate angles remain available")
        self.rotation_slider.bind("<ButtonRelease-1>", self._rotation_slider_release, add="+")
        self.rotation_slider.bind("<KeyRelease>", self._rotation_slider_release, add="+")
        self.rotation_slider.bind("<Escape>", self._cancel_rotation, add="+")

        # Work surface
        self.stage = tk.Frame(self.root, bg=PANEL, highlightbackground=EDGE, highlightthickness=1)
        self.stage.pack(fill="both", expand=True, padx=22, pady=(0, 12))
        canvas_color = THEME_SPECIAL_COLORS[self.theme_name]["editor_canvas"]
        self.canvas = tk.Canvas(self.stage, bg=canvas_color, highlightthickness=0, cursor="arrow")
        self.canvas.pack(fill="both", expand=True, padx=1, pady=1)
        self._add_tooltip(self.canvas, "Draw on the image · use the rotation slider above to rotate · scroll to zoom · middle-drag to pan")
        self.size_badge = None
        self.canvas.bind("<ButtonPress-1>", self._draw_start)
        self.canvas.bind("<B1-Motion>", self._draw_move)
        self.canvas.bind("<ButtonRelease-1>", self._draw_end)
        self.canvas.bind("<ButtonPress-3>", self._recognition_right_click)
        self.canvas.bind("<Configure>", self._resize_preview)
        self.canvas.bind("<Motion>", self._pointer_status)
        self.canvas.bind("<Enter>", self._canvas_enter)
        self.canvas.bind("<Leave>", self._canvas_leave)
        self.canvas.bind("<MouseWheel>", self._zoom_preview)
        self.canvas.bind("<Button-4>", lambda e: self._zoom_preview(e, 1))
        self.canvas.bind("<Button-5>", lambda e: self._zoom_preview(e, -1))
        self.canvas.bind("<ButtonPress-2>", self._pan_start)
        self.canvas.bind("<B2-Motion>", self._pan_move)
        self.canvas.bind("<Escape>", self._canvas_escape)
        self.canvas.bind("<Control-a>", self._select_all_recognized_text)
        self.width.trace_add("write",lambda *_:self._on_brush_width_changed())

        footer = tk.Frame(self.root, bg=INK, padx=12, height=36)
        footer.pack(fill="x")
        footer.pack_propagate(False)
        self.footer=footer
        self.status_label = tk.Label(footer, textvariable=self.status, bg=INK, fg=MUTED, anchor="center")
        self.status_label.place(relx=0.5, rely=0.5, anchor="center")
        self.copy_button=tk.Button(footer, text="Copy", command=self.copy_image, bg=PANEL2, fg=TEXT, activebackground=EDGE, activeforeground=TEXT, relief="flat", bd=0, padx=15, pady=8, cursor="hand2")
        self.paint_button=tk.Button(footer, text="Open in Paint", command=self.open_in_paint, bg=PANEL2, fg=TEXT, activebackground=EDGE, activeforeground=TEXT, relief="flat", bd=0, padx=15, pady=8, cursor="hand2")
        self.save_button=tk.Button(footer, text="Save as…", command=self.save_as, bg=ACCENT, fg="white", activebackground="#7770ea", activeforeground="white", relief="flat", bd=0, padx=16, pady=8, cursor="hand2", font=("Segoe UI Semibold", 9))
        chess_palette = THEME_SPECIAL_COLORS[self.theme_name]
        self.chess_button=tk.Button(footer, text="♟  Chess → PGN", command=self.chess_to_pgn, bg=chess_palette["chess_button"], fg=chess_palette["chess_text"], activebackground=chess_palette["chess_active"], activeforeground="white" if self.theme_name == "dark" else chess_palette["chess_text"], relief="flat", bd=0, padx=13, pady=8, cursor="hand2")
        self._add_tooltip(self.copy_button, "Copy the current image to the clipboard · Ctrl+C")
        self._add_tooltip(self.paint_button, "Open a temporary copy of the image in Paint")
        self._add_tooltip(self.save_button, "Save the current image as a file · Ctrl+S")
        self._add_tooltip(self.chess_button, "Recognize a chess position and export its PGN · Ctrl+Shift+P")
        self.chess_button.pack(side="right",padx=(8,0));self.copy_button.pack(side="right",padx=(8,0));self.paint_button.pack(side="right",padx=(8,0));self.save_button.pack(side="right")
        self.set_color(self.color.get())
        self._style_mode_buttons()
        self._set_editor_visible(False)

    def _enable_taskbar_button(self):
        try:
            user32=ctypes.windll.user32
            user32.GetAncestor.argtypes=(wintypes.HWND,wintypes.UINT);user32.GetAncestor.restype=wintypes.HWND
            hwnd=user32.GetAncestor(self.root.winfo_id(),2) or self.root.winfo_id()
            self._taskbar_hwnd=hwnd
            get_style=user32.GetWindowLongPtrW;set_style=user32.SetWindowLongPtrW
            get_style.argtypes=(wintypes.HWND,ctypes.c_int);get_style.restype=ctypes.c_ssize_t
            set_style.argtypes=(wintypes.HWND,ctypes.c_int,ctypes.c_ssize_t);set_style.restype=ctypes.c_ssize_t
            style=get_style(hwnd,-16)
            style=(style & ~0x00C40000) | 0x00040000 | 0x00020000 | 0x00010000 | 0x00080000
            set_style(hwnd,-16,style)
            ex_style=get_style(hwnd,-20)
            set_style(hwnd,-20,(ex_style|0x00040000)&~0x00000080)
            user32.SetWindowPos(hwnd,None,0,0,0,0,0x0027)
            preference=ctypes.c_int(2)  # DWMWCP_ROUND
            dwm=ctypes.windll.dwmapi.DwmSetWindowAttribute
            dwm.argtypes=(wintypes.HWND,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD);dwm.restype=ctypes.c_long
            dwm(hwnd,33,ctypes.byref(preference),ctypes.sizeof(preference))
            border_color=wintypes.DWORD(_colorref(INK))
            dwm(hwnd,34,ctypes.byref(border_color),ctypes.sizeof(border_color))
            caption_color=wintypes.DWORD(_colorref(INK))
            dwm(hwnd,35,ctypes.byref(caption_color),ctypes.sizeof(caption_color))
        except Exception:pass

    def _round_button_on_map(self, event):
        widget = event.widget
        if not isinstance(widget, (tk.Button, tk.Menubutton)):
            return
        self._watch_button_rounding(widget)
        self._queue_button_surface_refresh(widget)
        widget.after(
            60,
            lambda target=widget: self._refresh_button_surface_after_map(target),
        )

    def _refresh_button_surface_after_map(self, button):
        """Rebuild the face after Windows finishes mapping the native button."""
        try:
            if not button.winfo_exists():
                return
            self._queue_button_surface_refresh(button, invalidate=True)
        except (AttributeError, tk.TclError):
            pass

    def _watch_button_rounding(self, widget):
        if getattr(widget, "_chesssnip_rounding_bound", False):
            return
        widget.bind("<Configure>", self._round_button_on_configure, add="+")
        widget._chesssnip_rounding_bound = True

    def _watch_existing_buttons(self):
        """Bind the initially hidden editor buttons before they are mapped."""
        pending = [self.root]
        while pending:
            widget = pending.pop()
            try:
                if not widget.winfo_exists():
                    continue
                if isinstance(widget, (tk.Button, tk.Menubutton)):
                    self._watch_button_rounding(widget)
                pending.extend(widget.winfo_children())
            except tk.TclError:
                continue

    def _round_button_on_configure(self, event):
        widget = event.widget
        if isinstance(widget, (tk.Button, tk.Menubutton)):
            self._queue_button_surface_refresh(widget)

    def _queue_button_surface_refresh(self, button, invalidate=False):
        """Coalesce multiple configure requests into one surface render per button."""
        try:
            if not button.winfo_exists():
                return
            if invalidate:
                button._chesssnip_button_surface_key = None
            if self._theme_update_in_progress:
                return
            pending = getattr(button, "_chesssnip_surface_refresh_after", None)
            if pending is not None:
                try:
                    button.after_cancel(pending)
                except tk.TclError:
                    pass
            button._chesssnip_surface_refresh_after = button.after_idle(
                lambda target=button: self._run_button_surface_refresh(target),
            )
        except (AttributeError, tk.TclError):
            pass

    def _run_button_surface_refresh(self, button):
        try:
            button._chesssnip_surface_refresh_after = None
            if self._theme_update_in_progress:
                return
            if button.winfo_exists():
                self._apply_button_corner_region(button)
        except (AttributeError, tk.TclError):
            pass

    def _apply_button_corner_region(self, button):
        """Draw the rounded button face, using a native region only as fallback."""
        try:
            if not button.winfo_exists():
                return
            width, height = button.winfo_width(), button.winfo_height()
            if width < 8 or height < 8:
                return

            self._prepare_antialiased_button(button)
            if self._render_antialiased_button(button, width, height):
                return

            # Unregistered native images can still use the old Windows region
            # fallback. Keep it out of the normal antialiased rendering path.
            if sys.platform != "win32" or getattr(
                button, "_chesssnip_region_size", None,
            ) == (width, height):
                return
            user32 = ctypes.windll.user32
            gdi32 = ctypes.windll.gdi32
            gdi32.CreateRoundRectRgn.argtypes = (
                ctypes.c_int, ctypes.c_int, ctypes.c_int,
                ctypes.c_int, ctypes.c_int, ctypes.c_int,
            )
            gdi32.CreateRoundRectRgn.restype = ctypes.c_void_p
            user32.SetWindowRgn.argtypes = (wintypes.HWND, ctypes.c_void_p, wintypes.BOOL)
            user32.SetWindowRgn.restype = ctypes.c_int
            gdi32.DeleteObject.argtypes = (ctypes.c_void_p,)
            gdi32.DeleteObject.restype = wintypes.BOOL
            corner_diameter = min(12, max(8, round(height * 0.32)))
            region = gdi32.CreateRoundRectRgn(
                0, 0, width + 1, height + 1, corner_diameter, corner_diameter,
            )
            if not region:
                return
            if user32.SetWindowRgn(button.winfo_id(), region, True):
                button._chesssnip_region_size = (width, height)
                return
            gdi32.DeleteObject(region)
        except (AttributeError, OSError, tk.TclError, TypeError):
            pass

    @staticmethod
    def _button_parent_color(button):
        try:
            return button.master.cget("bg")
        except (tk.TclError, AttributeError):
            return PANEL

    def _prepare_antialiased_button(self, button):
        if getattr(button, "_chesssnip_aa_button_ready", False):
            return
        try:
            original_configure = button.configure
            button._chesssnip_original_configure = original_configure
            button._chesssnip_requested_bg = button.cget("bg")
            button._chesssnip_requested_active_bg = button.cget("activebackground")
            button._chesssnip_original_text = button.cget("text")
            button._chesssnip_original_image = str(button.cget("image") or "")
            original_entry = self._button_icon_art.get(button._chesssnip_original_image)
            button._chesssnip_original_photo = (
                original_entry[2] if original_entry is not None else button.cget("image") or None
            )
            button._chesssnip_button_surface_photo = None
            button._chesssnip_button_surface_key = None
            button._chesssnip_aa_button_ready = True

            def configure(cnf=None, **kwargs):
                if getattr(button, "_chesssnip_rendering_button", False):
                    return original_configure(cnf, **kwargs)
                if cnf is None and not kwargs:
                    return original_configure(cnf, **kwargs)
                if cnf is not None and not isinstance(cnf, dict):
                    return original_configure(cnf, **kwargs)

                options = dict(cnf or {})
                options.update(kwargs)
                if not options:
                    return original_configure(cnf, **kwargs)
                for option in ("bg", "background"):
                    if option in options:
                        button._chesssnip_requested_bg = options[option]
                for option in ("activebackground",):
                    if option in options:
                        button._chesssnip_requested_active_bg = options[option]
                if "text" in options:
                    button._chesssnip_original_text = options["text"]
                if "image" in options:
                    image = options["image"]
                    button._chesssnip_original_image = str(image) if image else ""
                    button._chesssnip_original_photo = image if image else None

                parent_color = self._button_parent_color(button)
                actual = dict(options)
                if "bg" in actual:
                    actual["bg"] = parent_color
                if "background" in actual:
                    actual["background"] = parent_color
                if "activebackground" in actual:
                    actual["activebackground"] = parent_color
                if "image" in actual:
                    actual["image"] = options["image"]
                button._chesssnip_rendering_button = True
                try:
                    result = original_configure(actual)
                finally:
                    button._chesssnip_rendering_button = False
                if any(option in options for option in (
                    "bg", "background", "activebackground", "image", "text", "fg", "foreground",
                )):
                    self._queue_button_surface_refresh(button)
                return result

            button.configure = configure
            button.config = configure
            button.bind(
                "<ButtonPress-1>",
                lambda _event, target=button: self._set_button_pressed_state(target, True),
                add="+",
            )
            button.bind(
                "<ButtonRelease-1>",
                lambda _event, target=button: self._set_button_pressed_state(target, False),
                add="+",
            )
        except (AttributeError, tk.TclError, TypeError):
            button._chesssnip_aa_button_ready = False

    def _set_button_pressed_state(self, button, pressed):
        if not getattr(button, "_chesssnip_aa_button_ready", False):
            return
        button._chesssnip_visual_bg = (
            getattr(button, "_chesssnip_requested_active_bg", None) if pressed else None
        )
        button._chesssnip_visual_fg = (
            button.cget("activeforeground") if pressed else None
        )
        self._render_antialiased_button(button, button.winfo_width(), button.winfo_height())

    def _render_antialiased_button(self, button, width, height):
        try:
            text = str(getattr(button, "_chesssnip_original_text", ""))
            source_name = getattr(button, "_chesssnip_original_image", "")
            icon_entry = self._button_icon_art.get(source_name)
            if icon_entry is None:
                # A few review-dialog controls generate theme-colored artwork
                # per window instead of using the app-wide icon registry.
                icon_entry = getattr(button, "_chesssnip_inline_icon_art", {}).get(source_name)
            if icon_entry is not None:
                icon, icon_size = icon_entry[:2]
            elif source_name:
                # Leave uncommon images to Tk and use the native rounded
                # region fallback instead of drawing an incomplete icon.
                return False
            else:
                icon, icon_size = None, None
            try:
                anchor = str(button.cget("anchor"))
            except tk.TclError:
                anchor = "center"

            parent_color = self._button_parent_color(button)
            fill_color = getattr(button, "_chesssnip_visual_bg", None)
            if fill_color is None:
                fill_color = getattr(button, "_chesssnip_requested_bg", button.cget("bg"))
            foreground = getattr(button, "_chesssnip_visual_fg", None) or button.cget("fg")
            width, height = max(8, width), max(8, height)
            fixed_size = getattr(button, "_chesssnip_aa_fixed_size", None)
            if fixed_size:
                width, height = fixed_size
            signature = (
                width, height, str(parent_color), str(fill_color), str(foreground),
                source_name, text, str(button.cget("font")), anchor, str(foreground),
            )
            if getattr(button, "_chesssnip_button_surface_key", None) == signature:
                return True

            scale = 4
            fill_rgb = self._button_rgb(button, fill_color)
            parent_rgb = self._button_rgb(button, parent_color)
            image = Image.new(
                "RGBA", (width * scale, height * scale), (*parent_rgb, 255),
            )
            draw = ImageDraw.Draw(image)
            radius = min(7, max(4, round(height * 0.18))) * scale
            draw.rounded_rectangle(
                (0, 0, width * scale - 1, height * scale - 1),
                radius=radius, fill=(*fill_rgb, 255),
            )

            rendered_text = ""
            if icon is not None:
                has_dropdown_arrow = text.rstrip().endswith("▾")
                label = text.rstrip()[:-1].rstrip() if has_dropdown_arrow else text
                if text == "▾":
                    label = ""
                label_font = self._button_pil_font(button, scale) if label else None
                label_box = draw.textbbox((0, 0), label, font=label_font) if label else (0, 0, 0, 0)
                label_width = label_box[2] - label_box[0]
                label_height = label_box[3] - label_box[1]
                gap = 7 * scale if label else 0
                padding = 8 * scale if label else 0
                arrow_space = 15 * scale if has_dropdown_arrow and text != "▾" else 0
                available_icon_width = width * scale - 2 * padding - gap - label_width - arrow_space
                preferred_icon_size = (
                    max(icon_size) if getattr(button, "_chesssnip_dropdown_entry", False)
                    else min(icon_size)
                )
                max_icon_size = max(
                    8,
                    min(
                        preferred_icon_size, height - 8,
                        width - (17 if text == "▾" else 8),
                        max(8, available_icon_width // scale),
                    ),
                )
                icon_width = min(preferred_icon_size, max_icon_size)
                icon_height = min(preferred_icon_size, max_icon_size)
                content_width = icon_width * scale + gap + label_width + arrow_space
                if label and anchor in ("w", "nw", "sw"):
                    content_x = padding
                else:
                    content_x = (width * scale - content_width) // 2
                icon_x = content_x // scale if label else (width - icon_width) // 2
                icon_y = (height - icon_height) // 2
                if text == "▾":
                    icon_x = max(2, (width - icon_width - 7) // 2)
                # Keep the complete button face, including its icon, in one
                # image. A child Label over a native Tk Button is repainted
                # underneath the button on Windows during hover/press updates.
                icon_layer = icon.convert("RGBA").resize(
                    (icon_width * scale, icon_height * scale),
                    Image.Resampling.LANCZOS,
                )
                image.alpha_composite(
                    icon_layer, (icon_x * scale, icon_y * scale),
                )
                if label:
                    label_x = content_x + icon_width * scale + gap
                    label_y = (height * scale - label_height) // 2 - label_box[1]
                    draw.text(
                        (label_x - label_box[0], label_y), label,
                        font=label_font, fill=(*self._button_rgb(button, foreground), 255),
                    )
                if has_dropdown_arrow:
                    arrow_rgb = self._button_rgb(button, foreground)
                    center_x = (width - 8) * scale
                    center_y = (height // 2 + 1) * scale
                    draw.polygon(
                        ((center_x - 2 * scale, center_y - scale),
                         (center_x + 2 * scale, center_y - scale),
                         (center_x, center_y + scale)),
                        fill=(*arrow_rgb, 255),
                    )

            elif text:
                self._draw_button_label_art(button, draw, text, width, height, scale, anchor)

            photo = ImageTk.PhotoImage(
                image.resize((width, height), Image.Resampling.LANCZOS), master=self.root,
            )
            button._chesssnip_button_surface_photo = photo
            button._chesssnip_button_surface_key = signature
            parent_color = self._button_parent_color(button)
            button._chesssnip_rendering_button = True
            try:
                button._chesssnip_original_configure(
                    image=photo,
                    text=rendered_text, compound=tk.CENTER,
                    bg=parent_color, activebackground=parent_color,
                    width=width, height=height,
                    padx=0, pady=0, relief="flat", bd=0, highlightthickness=0,
                )
            finally:
                button._chesssnip_rendering_button = False
            return True
        except (AttributeError, OSError, tk.TclError, TypeError, ValueError):
            return False

    def _draw_button_label_art(self, button, draw, text, width, height, scale, anchor):
        """Draw button labels with explicit artwork for symbols absent from UI fonts."""
        foreground = getattr(button, "_chesssnip_visual_fg", None) or button.cget("fg")
        if str(button.cget("state")) == tk.DISABLED:
            foreground = button.cget("disabledforeground")
        color = (*self._button_rgb(button, foreground), 255)
        font = self._button_pil_font(button, scale)
        center_x, center_y = (width * scale) // 2, (height * scale) // 2

        def text_size(value):
            box = draw.textbbox((0, 0), value, font=font)
            return box, box[2] - box[0], box[3] - box[1]

        def draw_text(value, x, y):
            box, _, _ = text_size(value)
            draw.text((x - box[0], y - box[1]), value, font=font, fill=color)

        def draw_centered(value, cx=center_x, cy=center_y):
            box, text_width, text_height = text_size(value)
            draw.text(
                (cx - text_width // 2 - box[0], cy - text_height // 2 - box[1]),
                value, font=font, fill=color,
            )

        stroke = max(2, 2 * scale)

        def draw_plus(cx, cy):
            reach = 4 * scale
            draw.line((cx - reach, cy, cx + reach, cy), fill=color, width=stroke)
            draw.line((cx, cy - reach, cx, cy + reach), fill=color, width=stroke)

        def draw_cross(cx, cy):
            reach = 4 * scale
            draw.line((cx - reach, cy - reach, cx + reach, cy + reach), fill=color, width=stroke)
            draw.line((cx - reach, cy + reach, cx + reach, cy - reach), fill=color, width=stroke)

        def draw_check(cx, cy):
            draw.line(
                (cx - 5 * scale, cy, cx - 1 * scale, cy + 4 * scale,
                 cx + 6 * scale, cy - 5 * scale),
                fill=color, width=stroke, joint="curve",
            )

        def draw_square(cx, cy):
            side = 8 * scale
            draw.rectangle(
                (cx - side // 2, cy - side // 2, cx + side // 2, cy + side // 2),
                fill=color,
            )

        def draw_pawn(cx, cy):
            # Use the system chess glyph so this reads as a recognizable pawn,
            # while keeping it rendered into the existing antialiased button.
            font_path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "seguisym.ttf"
            try:
                pawn_font = ImageFont.truetype(str(font_path), 25 * scale)
            except OSError:
                pawn_font = font
            bounds = draw.textbbox((0, 0), "♟", font=pawn_font)
            pawn_width = bounds[2] - bounds[0]
            pawn_height = bounds[3] - bounds[1]
            draw.text(
                (cx - pawn_width // 2 - bounds[0], cy - pawn_height // 2 - bounds[1]),
                "♟", font=pawn_font, fill=color,
            )

        def draw_arrow(cx, cy):
            draw.line((cx - 5 * scale, cy, cx + 4 * scale, cy), fill=color, width=stroke)
            draw.polygon(
                ((cx + 1 * scale, cy - 4 * scale), (cx + 6 * scale, cy),
                 (cx + 1 * scale, cy + 4 * scale)),
                fill=color,
            )

        def draw_undo(cx, cy, reverse=False):
            direction = -1 if reverse else 1
            path = ((cx - direction * 5 * scale, cy - 3 * scale),
                    (cx, cy - 3 * scale), (cx + direction * 3 * scale, cy),
                    (cx + direction * 3 * scale, cy + 5 * scale))
            draw.line(path, fill=color, width=stroke, joint="curve")
            tip_x, tip_y = path[0]
            draw.polygon(
                ((tip_x, tip_y), (tip_x + direction * 5 * scale, tip_y - 4 * scale),
                 (tip_x + direction * 5 * scale, tip_y + 4 * scale)),
                fill=color,
            )

        def draw_triangle(up=False):
            half_width = 4 * scale
            half_height = 4 * scale
            points = (
                (center_x, center_y - half_height),
                (center_x + half_width, center_y + half_height),
                (center_x - half_width, center_y + half_height),
            ) if up else (
                (center_x - half_width, center_y - half_height),
                (center_x + half_width, center_y - half_height),
                (center_x, center_y + half_height),
            )
            draw.polygon(points, fill=color)

        def draw_restore():
            draw.rectangle(
                (center_x - 3 * scale, center_y - 5 * scale,
                 center_x + 5 * scale, center_y + 3 * scale),
                outline=color, width=stroke,
            )
            draw.rectangle(
                (center_x - 5 * scale, center_y - 3 * scale,
                 center_x + 3 * scale, center_y + 5 * scale),
                outline=color, width=stroke,
            )

        def draw_clockwise_arrow():
            radius = 5 * scale
            draw.arc(
                (center_x - radius, center_y - radius,
                 center_x + radius, center_y + radius),
                start=205, end=505, fill=color, width=stroke,
            )
            draw.polygon(
                ((center_x + 4 * scale, center_y - 5 * scale),
                 (center_x + 5 * scale, center_y - scale),
                 (center_x + scale, center_y - 3 * scale)),
                fill=color,
            )

        def draw_symbol_label(painter, label, icon_width=12):
            box, label_width, _label_height = text_size(label)
            gap = 6 * scale
            total_width = icon_width * scale + gap + label_width
            start_x = (width * scale - total_width) // 2
            painter(start_x + icon_width * scale // 2, center_y)
            draw_text(label, start_x + icon_width * scale + gap, center_y - (box[3] - box[1]) // 2)

        if text == "—":
            draw.line((center_x - 5 * scale, center_y, center_x + 5 * scale, center_y), fill=color, width=stroke)
        elif text == "□":
            draw.rectangle(
                (center_x - 5 * scale, center_y - 5 * scale, center_x + 5 * scale, center_y + 5 * scale),
                outline=color, width=stroke,
            )
        elif text == "×":
            draw_cross(center_x, center_y)
        elif text == "✕":
            draw_cross(center_x, center_y)
        elif text == "❐":
            draw_restore()
        elif text == "▲":
            draw_triangle(up=True)
        elif text == "▼":
            draw_triangle(up=False)
        elif text == "↻":
            draw_clockwise_arrow()
        elif text in ("＋  New", "+  New"):
            draw_symbol_label(draw_plus, "New", 10)
        elif text == "■  Stop":
            draw_symbol_label(draw_square, "Stop", 10)
        elif text == "✓ Accept":
            draw_symbol_label(draw_check, "Accept", 12)
        elif text == "× Cancel":
            draw_symbol_label(draw_cross, "Cancel", 12)
        elif text == "♟  Chess → PGN":
            # Split the label so the pawn and arrow remain crisp regardless of font fallback.
            parts = ("Chess", "PGN")
            boxes = [text_size(part) for part in parts]
            gap = 5 * scale
            pawn_width = 16 * scale
            arrow_width = 12 * scale
            total_width = pawn_width + gap + boxes[0][1] + gap + arrow_width + gap + boxes[1][1]
            x = (width * scale - total_width) // 2
            draw_pawn(x + pawn_width // 2, center_y)
            x += pawn_width + gap
            draw_text("Chess", x, center_y - boxes[0][2] // 2)
            x += boxes[0][1] + gap
            draw_arrow(x + arrow_width // 2, center_y)
            x += arrow_width + gap
            draw_text("PGN", x, center_y - boxes[1][2] // 2)
        elif text == "↶":
            draw_undo(center_x, center_y)
        elif text == "↷":
            draw_undo(center_x, center_y, reverse=True)
        elif text == "▾":
            draw.polygon(
                ((center_x - 4 * scale, center_y - 2 * scale),
                 (center_x + 4 * scale, center_y - 2 * scale),
                 (center_x, center_y + 3 * scale)),
                fill=color,
            )
        elif "\n" in text:
            box = draw.multiline_textbbox((0, 0), text, font=font, spacing=2 * scale)
            draw.multiline_text(
                ((width * scale - (box[2] - box[0])) // 2 - box[0],
                 (height * scale - (box[3] - box[1])) // 2 - box[1]),
                text, font=font, fill=color, spacing=2 * scale,
                align=str(button.cget("justify")),
            )
        else:
            draw_centered(text)

    @staticmethod
    def _button_pil_font(button, scale):
        try:
            font_spec = button.cget("font")
            actual = button.tk.call("font", "actual", font_spec)
            options = dict(zip(actual[::2], actual[1::2]))
            family = str(options.get("-family", "Segoe UI"))
            weight = str(options.get("-weight", "normal")).lower()
            slant = str(options.get("-slant", "roman")).lower()
            line_height = int(button.tk.call("font", "metrics", font_spec, "-linespace"))
            pixel_size = max(8, (line_height - 2) * scale)
            family_key = family.lower().replace(" ", "")
            font_file = {
                ("segoeui", "bold"): "segoeuib.ttf",
                ("segoeuisemibold", "normal"): "seguisb.ttf",
                ("segoeuisemibold", "bold"): "seguisb.ttf",
                ("segoeuisymbol", "normal"): "seguisym.ttf",
                ("segoeuiemoji", "normal"): "seguiemj.ttf",
                ("arial", "bold"): "arialbd.ttf",
                ("tahoma", "bold"): "tahomabd.ttf",
            }.get((family_key, weight))
            if font_file is None:
                font_file = {
                    "segoeui": "segoeui.ttf",
                    "segoeuisemibold": "seguisb.ttf",
                    "segoeuisymbol": "seguisym.ttf",
                    "segoeuiemoji": "seguiemj.ttf",
                    "arial": "arial.ttf",
                    "tahoma": "tahoma.ttf",
                    "symbol": "symbol.ttf",
                }.get(family_key, family_key + ("bd" if weight == "bold" else "") + ".ttf")
            if slant == "italic" and family_key == "segoeui" and weight != "bold":
                font_file = "segoeuii.ttf"
            font_path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / font_file
            return ImageFont.truetype(str(font_path), pixel_size)
        except (AttributeError, OSError, tk.TclError, TypeError, ValueError):
            try:
                return ImageFont.truetype("arial.ttf", max(8, 12 * scale))
            except OSError:
                return ImageFont.load_default()

    @staticmethod
    def _button_rgb(button, color):
        red, green, blue = button.winfo_rgb(color)
        return red >> 8, green >> 8, blue >> 8

    def _bind_title_button_hover(self, button, hover_bg=None, hover_fg=None):
        button._chesssnip_custom_hover = True
        if hover_bg:
            button.configure(activebackground=hover_bg)

        def enter(_event, target=button, foreground=hover_fg):
            target._chesssnip_idle_bg = getattr(
                target, "_chesssnip_requested_bg", target.cget("bg"),
            )
            active = getattr(
                target, "_chesssnip_requested_active_bg", target.cget("activebackground"),
            )
            target.configure(bg=active)
            if foreground:
                target.configure(fg=foreground, activeforeground=foreground)

        def leave(_event, target=button, foreground=hover_fg):
            idle = getattr(target, "_chesssnip_idle_bg", None)
            try:
                current = getattr(target, "_chesssnip_requested_bg", target.cget("bg"))
                active = getattr(
                    target, "_chesssnip_requested_active_bg", target.cget("activebackground"),
                )
                if idle is not None and str(current).lower() == str(active).lower():
                    target.configure(bg=idle)
                if foreground:
                    # Use the active palette in case a theme switch happened
                    # while the pointer was over this close button.
                    target.configure(fg=TEXT, activeforeground=TEXT)
            except tk.TclError:
                pass

        button.bind(
            "<Enter>",
            enter,
        )
        button.bind(
            "<Leave>",
            leave,
        )

    def _button_hover_enter(self, event):
        button = event.widget
        if getattr(button, "_chesssnip_custom_hover", False):
            return
        try:
            if getattr(button, "_chesssnip_hover_colors", None):
                return
            if str(button.cget("state")) == tk.DISABLED:
                return
            idle = getattr(button, "_chesssnip_requested_bg", button.cget("bg"))
            red, green, blue = (value / 65535 for value in button.winfo_rgb(idle))
            hue, lightness, saturation = colorsys.rgb_to_hls(red, green, blue)
            hover_lightness = lightness + 0.075 if lightness < 0.55 else lightness - 0.075
            hover_rgb = colorsys.hls_to_rgb(hue, min(0.95, max(0.05, hover_lightness)), saturation)
            hover = "#%02x%02x%02x" % tuple(round(channel * 255) for channel in hover_rgb)
            button._chesssnip_hover_colors = (idle, hover)
            button.configure(bg=hover)
        except (tk.TclError, AttributeError, TypeError, ValueError):
            button._chesssnip_hover_colors = None

    @staticmethod
    def _button_hover_leave(event):
        button = event.widget
        colors = getattr(button, "_chesssnip_hover_colors", None)
        if not colors:
            return
        button._chesssnip_hover_colors = None
        idle, hover = colors
        try:
            current = getattr(button, "_chesssnip_requested_bg", button.cget("bg"))
            if str(current).lower() == hover.lower():
                button.configure(bg=idle)
        except (tk.TclError, AttributeError):
            pass

    @staticmethod
    def _add_tooltip(widget, text):
        widget._chesssnip_tooltip = ChessSnipTooltip(widget, text)

    def toggle_theme(self):
        if self._theme_update_in_progress:
            return
        self._close_choice_popup()
        previous_name = self.theme_name
        next_name = "light" if previous_name == "dark" else "dark"
        previous = THEMES[previous_name]
        target = THEMES[next_name]
        color_map = {previous[key].lower(): target[key] for key in THEME_FIELDS}
        old_special = THEME_SPECIAL_COLORS[previous_name]
        new_special = THEME_SPECIAL_COLORS[next_name]
        color_map.update({old_special[key].lower(): new_special[key] for key in old_special})

        self._theme_update_in_progress = True
        try:
            self.theme_name = next_name
            _activate_palette(next_name)
            self._refresh_tool_icon_images()
            self._refresh_capture_mode_icon_images()
            self._recolor_theme_tree(self.root, color_map)
            self._theme_toggle_icon = self._create_theme_toggle_icon()
            self.theme_button.configure(image=self._theme_toggle_icon)
            # The annotation color is independent of the theme. Reassert it on
            # the color button without resetting HSV sliders or redrawing swatches.
            if self.color_picker_button:
                selected_color = self.color.get()
                self.color_picker_button.configure(
                    bg=selected_color, activebackground=selected_color,
                )
            self._style_mode_buttons()
            self._apply_dwm_theme_colors()
            self._refresh_preview_matte()
            try:
                SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
                SETTINGS_PATH.write_text(
                    json.dumps({"theme": next_name}, indent=2), encoding="utf-8",
                )
            except OSError:
                pass
        finally:
            self._theme_update_in_progress = False
            self._refresh_antialiased_button_surfaces()

    def _recolor_theme_tree(self, widget, color_map):
        color_options = (
            "bg", "background", "fg", "foreground", "activebackground",
            "activeforeground", "disabledforeground", "highlightbackground",
            "highlightcolor", "insertbackground", "selectbackground",
            "selectforeground", "readonlybackground", "troughcolor", "selectcolor",
        )
        aliases = {"background": "bg", "foreground": "fg"}
        try:
            if not widget.winfo_exists():
                return
            # Preset color buttons are drawing colors, not theme surfaces. In
            # particular, mapping #ffffff to the light/dark panel color changes
            # the actual white annotation color when the theme is switched.
            is_color_swatch = widget in self.color_buttons.values()
            available = widget.configure()
            updates = {}
            seen_options = set()
            for option in color_options:
                canonical = aliases.get(option, option)
                if canonical in seen_options:
                    continue
                configured_option = canonical if canonical in available else option
                if configured_option not in available:
                    continue
                seen_options.add(canonical)
                if is_color_swatch and canonical in ("bg", "activebackground"):
                    continue
                try:
                    if isinstance(widget, (tk.Button, tk.Menubutton)) and canonical == "bg":
                        old_value = getattr(
                            widget, "_chesssnip_requested_bg", widget.cget(configured_option),
                        )
                    elif isinstance(widget, (tk.Button, tk.Menubutton)) and canonical == "activebackground":
                        old_value = getattr(
                            widget, "_chesssnip_requested_active_bg",
                            widget.cget(configured_option),
                        )
                    else:
                        old_value = widget.cget(configured_option)
                    old_color = str(old_value).lower()
                    if old_color in color_map:
                        updates[configured_option] = color_map[old_color]
                    if canonical == "bg":
                        idle_bg = getattr(widget, "_chesssnip_idle_bg", None)
                        if idle_bg and idle_bg.lower() in color_map:
                            widget._chesssnip_idle_bg = color_map[idle_bg.lower()]
                except (tk.TclError, TypeError):
                    continue
            if updates:
                widget.configure(**updates)
            if isinstance(widget, ModernSlider):
                widget._redraw()
            elif isinstance(widget, tk.Canvas) and widget is self.canvas:
                # The editor canvas can contain thousands of annotation items.
                # Its marks keep their chosen colors; only these two items use
                # the application theme and need an update.
                try:
                    widget.itemconfigure(
                        "preview-frame", fill=widget.cget("bg"), outline=EDGE,
                    )
                    widget.itemconfigure("size-badge", fill=MUTED)
                except tk.TclError:
                    pass
            elif isinstance(widget, tk.Canvas):
                for item in widget.find_all():
                    for option in ("fill", "outline"):
                        try:
                            old_color = str(widget.itemcget(item, option)).lower()
                            if old_color in color_map:
                                widget.itemconfigure(item, **{option: color_map[old_color]})
                        except tk.TclError:
                            pass
            theme_refresh = getattr(widget, "_chesssnip_theme_refresh", None)
            if callable(theme_refresh):
                try:
                    theme_refresh()
                except (AttributeError, OSError, tk.TclError, TypeError, ValueError):
                    pass
            for child in widget.winfo_children():
                self._recolor_theme_tree(child, color_map)
        except tk.TclError:
            pass

    def _refresh_preview_matte(self):
        """Refresh only cached overlays that flatten transparent preview margins."""
        try:
            source = self._preview_source_cache
            item = getattr(self, "_preview_canvas_item", None)
            if (
                not self.image or source is None or item is None
                or not self.canvas.type(item)
                or not self._preview_has_transparency
            ):
                return
            if self.tool.get() == "crop":
                self._crop_preview_base = None
                self._crop_dimmed_preview = None
                self._render_crop_overlay()
            elif self._recognition_regions:
                width, height = self.image_display_size
                resized = source.resize((width, height), Image.Resampling.LANCZOS)
                self._recognition_base_preview = self._image_as_rgb(
                    resized, self._editor_canvas_rgb(),
                )
                self._render_recognition_selection()
        except (AttributeError, OSError, tk.TclError, TypeError, ValueError):
            pass

    def _refresh_antialiased_button_surfaces(self):
        """Rebuild cached button corners after parent theme colors change."""
        pending = [self.root]
        while pending:
            widget = pending.pop()
            try:
                if not widget.winfo_exists():
                    continue
                if (
                    getattr(widget, "_chesssnip_aa_button_ready", False)
                    and widget.winfo_ismapped()
                ):
                    self._queue_button_surface_refresh(widget, invalidate=True)
                pending.extend(widget.winfo_children())
            except tk.TclError:
                continue

    def _apply_dwm_theme_colors(self):
        try:
            user32 = ctypes.windll.user32
            user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
            user32.GetAncestor.restype = wintypes.HWND
            dwm = ctypes.windll.dwmapi.DwmSetWindowAttribute
            dwm.argtypes = (wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD)
            dwm.restype = ctypes.c_long
            seen = set()
            color = wintypes.DWORD(_colorref(INK))
            windows = [self.root]
            windows.extend(
                window for window in getattr(self, "_chess_review_windows", ())
                if window not in windows
            )
            windows.extend(
                child for child in self.root.winfo_children()
                if isinstance(child, tk.Toplevel) and child not in windows
            )
            for widget in windows:
                try:
                    if not widget.winfo_exists():
                        continue
                    hwnd = user32.GetAncestor(widget.winfo_id(), 2) or widget.winfo_id()
                    if hwnd not in seen:
                        seen.add(hwnd)
                        dwm(hwnd, 34, ctypes.byref(color), ctypes.sizeof(color))
                        dwm(hwnd, 35, ctypes.byref(color), ctypes.sizeof(color))
                except tk.TclError:
                    pass
        except Exception:
            pass

    def _make_dark_checkbox(self, parent, text, variable, command, tooltip):
        control = tk.Frame(parent, bg=PANEL, cursor="hand2")
        indicator = tk.Canvas(control, width=16, height=16, bg=PANEL, highlightthickness=0, cursor="hand2", takefocus=True)
        indicator.pack(side="left", pady=1)
        label = tk.Label(control, text=text, bg=PANEL, fg=TEXT, padx=5, cursor="hand2")
        label.pack(side="left")

        def redraw(*_args):
            indicator.delete("all")
            checked = bool(variable.get())
            indicator.create_rectangle(
                1, 1, 14, 14,
                fill="#262935" if checked else PANEL2,
                outline="#7169e8" if checked else EDGE,
                width=1,
            )
            if checked:
                indicator.create_line(4, 8, 7, 11, 12, 5, fill="#b8b2ff", width=2, capstyle="round", joinstyle="round")

        def toggle(_event=None):
            variable.set(not bool(variable.get()))
            if command:
                command()
            return "break"

        variable.trace_add("write", redraw)
        redraw()
        for target in (control, indicator, label):
            target.bind("<Button-1>", toggle)
            target.bind("<space>", toggle)
        indicator.bind("<Button-1>", lambda event: (indicator.focus_set(), toggle(event))[1])
        self._add_tooltip(control, tooltip)
        return control

    def _titlebar_drag(self,e):
        if self._maximized:
            self._toggle_maximize()
            width=self.root.winfo_width()
            left=max(0,e.x_root-width*e.x_root//max(1,self.root.winfo_screenwidth()))
            top=max(0,e.y_root-18)
            self._move_window_to(left,top)
        self._drag_origin=(e.x_root,e.y_root,self.root.winfo_x(),self.root.winfo_y())

    def _move_window_to(self,x,y):
        user32=ctypes.windll.user32
        hwnd=user32.GetAncestor(self.root.winfo_id(),2) or self.root.winfo_id()
        user32.SetWindowPos.argtypes=(wintypes.HWND,wintypes.HWND,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,wintypes.UINT)
        user32.SetWindowPos.restype=wintypes.BOOL
        user32.SetWindowPos(hwnd,None,int(x),int(y),0,0,0x0015)  # NOSIZE | NOZORDER | NOACTIVATE

    def _titlebar_move(self,e):
        if not getattr(self,"_drag_origin",None):return
        px,py,wx,wy=self._drag_origin
        nx,ny=wx+e.x_root-px,wy+e.y_root-py
        try:self._move_window_to(nx,ny)
        except Exception:pass

    def _minimize_window(self):
        try:
            user32=ctypes.windll.user32
            hwnd=user32.GetAncestor(self.root.winfo_id(),2) or self.root.winfo_id()
            user32.ShowWindow.argtypes=(wintypes.HWND,ctypes.c_int);user32.ShowWindow.restype=wintypes.BOOL
            user32.ShowWindow(hwnd,6)  # SW_MINIMIZE
        except Exception:self.root.iconify()

    def _toggle_maximize(self):
        if self._maximized:
            if self._restore_geometry:self.root.geometry(self._restore_geometry)
            self._maximized=False;self.maximize_button.configure(text="□")
            return
        self._restore_geometry=self.root.geometry()
        rect=wintypes.RECT()
        spi=ctypes.windll.user32.SystemParametersInfoW
        spi.argtypes=(wintypes.UINT,wintypes.UINT,ctypes.c_void_p,wintypes.UINT);spi.restype=wintypes.BOOL
        spi(0x0030,0,ctypes.byref(rect),0)
        width,height=rect.right-rect.left,rect.bottom-rect.top
        self.root.geometry(f"{width}x{height}+{rect.left}+{rect.top}")
        self._maximized=True;self.maximize_button.configure(text="❐")

    def _set_editor_visible(self,visible):
        if visible:
            self.root.minsize(1240,560)
            self.footer.configure(height=44)
            self.status_label.place_forget()
            self.status_label.configure(anchor="sw")
            self.status_label.pack(side="left",fill="both",expand=True)
            if not self.editor_separator.winfo_manager():self.editor_separator.pack(side="left",fill="y",padx=(9, 8),pady=2)
            if not self.editorbar.winfo_manager():self.editorbar.pack(side="left",fill="x",expand=True,padx=(0,0))
            if not self.stage.winfo_manager():self.stage.pack(fill="both",expand=True,padx=22,pady=(0,12),before=self.footer)
            self.chess_button.pack(side="right",padx=(8,0),pady=(2,6));self.copy_button.pack(side="right",padx=(8,0),pady=(2,6));self.paint_button.pack(side="right",padx=(8,0),pady=(2,6));self.save_button.pack(side="right",pady=(2,6))
            if self.root.winfo_width()<1240 or self.root.winfo_height()<560:
                default_width=min(1280,max(1240,self.root.winfo_screenwidth()-80))
                default_height=min(800,max(560,self.root.winfo_screenheight()-100))
                self.root.geometry(f"{default_width}x{default_height}")
        else:
            self.editorbar.pack_forget();self.editor_separator.pack_forget();self.stage.pack_forget()
            self.chess_button.pack_forget();self.copy_button.pack_forget();self.paint_button.pack_forget();self.save_button.pack_forget()
            self.status_label.pack_forget()
            self.footer.configure(height=36)
            self.status_label.configure(anchor="center")
            self.status_label.place(relx=0.5,rely=0.5,anchor="center")
            self.root.minsize(520,120)

    def _mode_menu(self, parent):
        self.mode_btn = tk.Button(
            parent, image=self._capture_mode_icon_images[self.capture_mode.get()],
            text="▾", compound=tk.RIGHT, command=self._popup_capture_mode_menu,
            bg=PANEL2, fg=TEXT, activebackground=EDGE, activeforeground=TEXT,
            relief="flat", bd=0, padx=8, pady=7, cursor="hand2",
        )
        self.mode_btn.pack(side="left")
        self._add_tooltip(self.mode_btn, "Choose rectangle, freeform, window, or full-screen capture")

    def _update_mode_label(self):
        mode = self.capture_mode.get()
        self.mode_btn.configure(image=self._capture_mode_icon_images[mode], text="▾")

    def _popup_capture_mode_menu(self):
        choices = tuple(
            (mode, mode, self._capture_mode_icon_images[mode])
            for mode in ("Rectangle", "Window", "Full screen", "Freeform")
        )
        self._show_choice_popup(
            self.mode_btn, choices, self.capture_mode.get(), self._choose_capture_mode,
        )

    def _choose_capture_mode(self, mode):
        self.capture_mode.set(mode)
        self._update_mode_label()

    def _make_variant_tool(self, parent, group, label, choices, tooltip):
        variant = tk.StringVar(master=self.root, value=choices[0][0])
        initial_icon_name = (
            group if group in self._tool_icon_images
            else choices[0][2] if len(choices[0]) > 2
            else choices[0][0]
        )
        initial_icon = (
            self._tool_icon_images[initial_icon_name]
            if isinstance(initial_icon_name, str) else initial_icon_name
        )
        slot = tk.Frame(parent, bg=PANEL, width=38, height=32)
        slot.pack(side="left", padx=(0, 2))
        slot.pack_propagate(False)
        button = tk.Button(
            slot, text="▾", image=initial_icon, compound=tk.LEFT,
            command=lambda selected=group: self._popup_tool_variant_menu(selected),
            relief="flat", bd=0, padx=2, pady=2, cursor="hand2",
            font=("Segoe UI", 8),
        )
        button.pack(fill="both", expand=True)
        self.tool_buttons[group] = button
        self._tool_variant_buttons[group] = (button, label)
        self._tool_variant_vars[group] = variant
        self._tool_variant_choices[group] = tuple(choices)
        self._add_tooltip(button, tooltip)

    def _popup_tool_variant_menu(self, group):
        if group == "shapes":
            return self._popup_shapes_menu()
        button = self._tool_variant_buttons[group][0]
        tooltip = getattr(button, "_chesssnip_tooltip", None)
        if tooltip:
            tooltip.hide()
        choices = tuple(
            (choice[0], choice[1], self._tool_icon_images.get(choice[2]))
            if len(choice) > 2 and isinstance(choice[2], str)
            else choice
            for choice in self._tool_variant_choices[group]
        )
        self._show_choice_popup(
            button, choices,
            self._tool_variant_vars[group].get(),
            lambda mode: self._choose_tool_variant(group, mode),
        )

    def _popup_shapes_menu(self):
        button = self._tool_variant_buttons["shapes"][0]
        tooltip = getattr(button, "_chesssnip_tooltip", None)
        if tooltip:
            tooltip.hide()
        self._close_choice_popup(refresh_cursor=False, restore_suspended=False)
        popup = tk.Frame(
            self.root, bg=EDGE, bd=0, highlightthickness=1,
            highlightbackground=EDGE, highlightcolor=EDGE,
        )
        popup._chesssnip_shape_menu = True
        self._active_choice_popup = popup
        body = tk.Frame(popup, bg=PANEL2, bd=0)
        body.pack(fill="both", expand=True, padx=1, pady=1)
        popup._chesssnip_popup_body = body
        self._shape_popup_buttons = {}
        shapes = ("rectangle", "circle", "line", "arrow", "cross", "tick")
        popup._shape_menu_icon_images = {
            shape: self._create_shape_menu_icon(shape, TEXT, size=32)
            for shape in shapes
        }
        selected_shape = self._tool_variant_vars["shapes"].get()
        for shape in shapes:
            selected = shape == selected_shape
            item = tk.Button(
                body, image=popup._shape_menu_icon_images[shape],
                command=lambda mode=shape: self._choose_shape_from_popup(mode),
                bg=ACCENT if selected else PANEL2,
                fg="white" if selected else TEXT,
                activebackground=ACCENT if selected else EDGE,
                activeforeground="white" if selected else TEXT,
                relief="flat", bd=0, padx=6, pady=5, cursor="hand2",
                takefocus=0,
            )
            item.pack(side="left", padx=(5 if shape == "rectangle" else 2, 2), pady=4)
            self._shape_popup_buttons[shape] = item
            tooltip = {"line": "Straight line"}.get(shape, shape.capitalize())
            self._add_tooltip(item, tooltip)
        tk.Frame(body, bg=EDGE, width=1, height=24).pack(
            side="left", padx=5, pady=5,
        )
        fill = tk.Checkbutton(
            body, text="Fill", variable=self.shape_fill_var,
            command=self._toggle_shape_fill,
            bg=PANEL2, fg=TEXT, selectcolor=ACCENT,
            activebackground=EDGE, activeforeground=TEXT,
            relief="flat", bd=0, padx=5, pady=5,
            font=("Segoe UI", 10), cursor="hand2", takefocus=0,
            state="disabled" if selected_shape not in ("rectangle", "circle") else "normal",
        )
        fill.pack(side="left", padx=(3, 9), pady=4)
        self._shape_fill_checkbox = fill
        popup.update_idletasks()
        popup_width = max(240, popup.winfo_reqwidth())
        popup_height = popup.winfo_reqheight()
        x, y = self._dropdown_popup_origin(button, popup_width, popup_height)
        self._animate_choice_popup(popup, x, y, popup_width, popup_height)

    def _choose_shape_from_popup(self, shape):
        self._tool_variant_vars["shapes"].set(shape)
        fill_enabled = shape in ("rectangle", "circle")
        if not fill_enabled:
            self.shape_fill_var.set(False)
        if self._shape_fill_checkbox is not None:
            self._shape_fill_checkbox.configure(state="normal" if fill_enabled else "disabled")
        filled = fill_enabled and self.shape_fill_var.get()
        self.set_tool(shape + ("_filled" if filled else ""))
        for name, button in self._shape_popup_buttons.items():
            selected = name == shape
            button.configure(
                bg=ACCENT if selected else PANEL2,
                fg="white" if selected else TEXT,
                activebackground=ACCENT if selected else EDGE,
                activeforeground="white" if selected else TEXT,
            )

    def _choose_tool_variant(self, group, mode):
        self._close_choice_popup()
        self._tool_variant_vars[group].set(mode)
        self.set_tool(mode)

    def _sync_shape_fill_to_selection(self, stroke):
        tool = stroke.get("tool", "")
        shape = tool.removesuffix("_filled")
        if shape not in ("rectangle", "circle", "line", "arrow", "cross", "tick"):
            return
        self._tool_variant_vars["shapes"].set(shape)
        fill_enabled = shape in ("rectangle", "circle")
        self.shape_fill_var.set(fill_enabled and tool.endswith("_filled"))
        if self._shape_fill_checkbox is not None:
            self._shape_fill_checkbox.configure(
                state="normal" if fill_enabled else "disabled",
            )

    def _toggle_shape_fill(self):
        shape = self._tool_variant_vars["shapes"].get()
        selected = self._selected_shape
        selected_is_live = selected is not None and any(
            stroke is selected for stroke in self.strokes
        )
        if selected_is_live:
            selected_tool = selected.get("tool", "")
            selected_shape = selected_tool.removesuffix("_filled")
            if selected_shape in ("rectangle", "circle") and selected_shape == shape:
                target_tool = selected_shape + ("_filled" if self.shape_fill_var.get() else "")
                if selected_tool != target_tool:
                    self._push_history()
                    selected["tool"] = target_tool
                    self._shape_color_edit_history_pushed = False
                self._tool_variant_vars["shapes"].set(selected_shape)
                self.set_tool(target_tool)
                if selected_tool != target_tool:
                    self._redraw_transformed_shape(selected)
                    self._schedule_auto_clipboard()
                return
        if shape not in ("rectangle", "circle"):
            self.shape_fill_var.set(False)
            return
        self.set_tool(shape + ("_filled" if self.shape_fill_var.get() else ""))

    def _dropdown_popup_origin(self, anchor, popup_width, popup_height):
        root_x, root_y = self.root.winfo_rootx(), self.root.winfo_rooty()
        x = anchor.winfo_rootx() - root_x
        if self.canvas.winfo_exists() and self.canvas.winfo_ismapped():
            y = self.canvas.winfo_rooty() - root_y
        else:
            y = anchor.winfo_rooty() - root_y + anchor.winfo_height()
        root_width, root_height = self.root.winfo_width(), self.root.winfo_height()
        if x + popup_width > root_width:
            x = max(0, root_width - popup_width)
        if y + popup_height > root_height:
            y = max(0, root_height - popup_height)
        return x, y

    def _show_choice_popup(self, anchor, choices, selected, on_choose):
        """Show an in-window, modeless menu so canvas input never gets grabbed."""
        self._close_choice_popup(refresh_cursor=False, restore_suspended=False)
        popup = tk.Frame(
            self.root, bg=EDGE, bd=0, highlightthickness=1,
            highlightbackground=EDGE, highlightcolor=EDGE,
        )
        self._active_choice_popup = popup
        body = tk.Frame(popup, bg=PANEL2, bd=0)
        body.pack(fill="both", expand=True, padx=1, pady=1)
        popup._chesssnip_popup_body = body
        for choice in choices:
            value, label = choice[:2]
            icon = choice[2] if len(choice) > 2 else None
            is_selected = value == selected
            button_options = dict(
                # The selected row is already distinguished by its fill. Keep
                # the label clean so no font-dependent check glyph is needed.
                text=label,
                command=lambda choice=value: (self._close_choice_popup(), on_choose(choice)),
                bg=ACCENT if is_selected else PANEL2,
                fg="white" if is_selected else TEXT,
                activebackground=ACCENT if is_selected else EDGE,
                activeforeground="white" if is_selected else TEXT,
                relief="flat", bd=0, anchor="w", padx=10, pady=4,
                font=("Segoe UI", 10), cursor="hand2", takefocus=0,
            )
            if icon:
                button_options.update(image=icon, compound=tk.LEFT, padx=12)
            button = tk.Button(body, **button_options)
            button._chesssnip_dropdown_entry = True
            button.pack(fill="x")
            if not is_selected:
                button.bind(
                    "<Enter>", lambda _e, item=button: item.configure(bg=EDGE),
                )
                button.bind(
                    "<Leave>", lambda _e, item=button: item.configure(bg=PANEL2),
                )
        popup.update_idletasks()
        popup_width = max(anchor.winfo_width(), popup.winfo_reqwidth(), 220)
        popup_height = popup.winfo_reqheight()
        x, y = self._dropdown_popup_origin(anchor, popup_width, popup_height)
        self._animate_choice_popup(popup, x, y, popup_width, popup_height)

    def _animate_choice_popup(self, popup, x, y, width, height):
        """Animate dropdowns without resizing their contents."""
        self._choice_popup_animating = True
        started = time.monotonic()
        duration = 0.11
        # Keep the final geometry from the first frame. Animating height causes
        # Tk to resize every packed menu item and the image-backed button
        # renderer then freezes those temporary sizes into its surfaces.
        popup.place(x=x, y=y + 4, width=width, height=height)
        popup.lift()

        def animate():
            if self._active_choice_popup is not popup:
                self._choice_popup_animating = False
                self._choice_popup_animation_after = None
                return
            try:
                if not popup.winfo_exists():
                    self._choice_popup_animating = False
                    self._choice_popup_animation_after = None
                    return
                progress = min(1.0, (time.monotonic() - started) / duration)
                eased = 1.0 - (1.0 - progress) ** 3
                popup.place_configure(y=y + round(4 * (1.0 - eased)))
                if progress < 1.0:
                    self._choice_popup_animation_after = self.root.after(10, animate)
                    return

                self._choice_popup_animating = False
                self._choice_popup_animation_after = None
            except tk.TclError:
                self._choice_popup_animating = False
                self._choice_popup_animation_after = None

        self._choice_popup_animation_after = self.root.after(0, animate)

    def _close_choice_popup(self, refresh_cursor=True, restore_suspended=True):
        if self._color_palette_close_after is not None:
            try:
                self.root.after_cancel(self._color_palette_close_after)
            except tk.TclError:
                pass
            self._color_palette_close_after = None
        popup = self._active_choice_popup
        if popup is None:
            return
        if self._choice_popup_animation_after is not None:
            try:
                self.root.after_cancel(self._choice_popup_animation_after)
            except tk.TclError:
                pass
            self._choice_popup_animation_after = None
        self._choice_popup_animating = False
        self._active_choice_popup = None
        if getattr(popup, "_chesssnip_shape_menu", False):
            self._shape_popup_buttons = {}
            self._shape_fill_checkbox = None
        if getattr(popup, "_chesssnip_color_palette", False):
            self.color_buttons.clear()
            self.hue_slider = None
            self.saturation_slider = None
            self.brightness_slider = None
            self._shape_color_edit_history_pushed = False
        try:
            popup.destroy()
        except tk.TclError:
            pass
        suspended = self._suspended_shape_popup
        place = self._suspended_shape_popup_place
        self._suspended_shape_popup = None
        self._suspended_shape_popup_place = None
        if suspended is not None:
            if restore_suspended and place:
                self._active_choice_popup = suspended
                try:
                    suspended.place(**place)
                    suspended.lift()
                except tk.TclError:
                    self._active_choice_popup = None
                    try:
                        suspended.destroy()
                    except tk.TclError:
                        pass
            else:
                try:
                    suspended.destroy()
                except tk.TclError:
                    pass
        if refresh_cursor:
            try:
                self.root.after_idle(self._refresh_brush_cursor_from_pointer)
            except tk.TclError:
                pass

    def _choice_popup_motion(self, event):
        # Dropdowns stay open while the pointer moves over the canvas.
        return

    def _choice_popup_click_away(self, event):
        popup = self._active_choice_popup
        if popup is None:
            return
        if getattr(popup, "_chesssnip_shape_menu", False):
            return
        widget = event.widget
        while widget is not None:
            if widget is popup:
                return
            widget = getattr(widget, "master", None)
        self._close_choice_popup()

    def _choice_popup_escape(self, _event):
        if self._active_choice_popup is not None:
            if getattr(self._active_choice_popup, "_chesssnip_shape_menu", False):
                return "break"
            self._close_choice_popup()
            return "break"

    def _refresh_brush_cursor_from_pointer(self):
        try:
            if not self.canvas.winfo_exists() or not self.canvas.winfo_ismapped():
                self.pointer_inside_canvas = False
                self._update_brush_cursor()
                return
            pointer_x, pointer_y = self.root.winfo_pointerxy()
            local_x = pointer_x - self.canvas.winfo_rootx()
            local_y = pointer_y - self.canvas.winfo_rooty()
            self.pointer_inside_canvas = (
                0 <= local_x < self.canvas.winfo_width()
                and 0 <= local_y < self.canvas.winfo_height()
            )
            if self.pointer_inside_canvas:
                self._pointer_canvas_pos = (
                    self.canvas.canvasx(local_x), self.canvas.canvasy(local_y),
                )
            self._update_brush_cursor()
        except tk.TclError:
            pass

    def _style_mode_buttons(self):
        recognition_active = getattr(self, "_recognition_mode", False)
        group_modes = {
            "pen": ("pen", "highlighter"),
            "shapes": (
                "rectangle", "rectangle_filled", "circle", "circle_filled",
                "line", "arrow", "cross", "tick",
            ),
        }
        for n, b in self.tool_buttons.items():
            active = not recognition_active and self.tool.get() in group_modes.get(n, (n,))
            b.configure(bg=ACCENT if active else PANEL2, fg="white" if active else TEXT, activebackground=ACCENT if active else EDGE, activeforeground="white")
        for group, (button, label) in self._tool_variant_buttons.items():
            icon_name = (
                "shapes" if group == "shapes"
                else self._tool_variant_vars[group].get()
            )
            button.configure(text="▾", image=self._tool_icon_images[icon_name], compound=tk.LEFT)
        if getattr(self, "pipette_button", None):
            active = not recognition_active and self.tool.get() == "eyedropper"
            self.pipette_button.configure(
                bg=ACCENT if active else PANEL2,
                fg="white" if active else TEXT,
                activebackground=ACCENT if active else EDGE,
                activeforeground="white",
            )
        if getattr(self, "recognition_button", None):
            self.recognition_button.configure(
                bg=ACCENT if recognition_active else PANEL2,
                fg="white" if recognition_active else TEXT,
                activebackground=ACCENT if recognition_active else EDGE,
                activeforeground="white",
            )
        chess_palette = THEME_SPECIAL_COLORS[self.theme_name]
        self.chess_button.configure(
            bg=chess_palette["chess_button"],
            fg=chess_palette["chess_text"],
            activebackground=chess_palette["chess_active"],
            activeforeground="white" if self.theme_name == "dark" else chess_palette["chess_text"],
        )

    def set_tool(self, name):
        if name == "crop" and not self.image:
            self.status.set("Capture an image before cropping")
            return
        if self.tool.get() == "crop" and name != "crop":
            self._leave_crop_mode()
        if getattr(self, "_recognition_mode", False):
            self._clear_recognition_regions()
            if self.image:
                self._render_preview()
        annotation_tools = {
            "pen", "highlighter", "eraser",
            "rectangle", "rectangle_filled", "circle", "circle_filled",
            "line", "arrow", "cross", "tick",
        }
        shape_modes = {
            "rectangle", "rectangle_filled", "circle", "circle_filled",
            "line", "arrow", "cross", "tick",
        }
        if name in ("line", "arrow", "cross", "tick"):
            self.shape_fill_var.set(False)
            if self._shape_fill_checkbox is not None:
                self._shape_fill_checkbox.configure(state="disabled")
        elif name in ("rectangle", "rectangle_filled", "circle", "circle_filled"):
            if self._shape_fill_checkbox is not None:
                self._shape_fill_checkbox.configure(state="normal")
        if self._active_choice_popup is not None and getattr(
            self._active_choice_popup, "_chesssnip_shape_menu", False,
        ) and name not in shape_modes:
            self._close_choice_popup(refresh_cursor=False)
        if name not in shape_modes:
            self._selected_shape = None
            self._shape_transform_drag = None
            self.canvas.delete("shape-selection")
        previous = self.tool.get()
        if name == "crop" and previous != "crop":
            self._crop_return_tool = previous if previous not in ("eyedropper", "recognition", "crop") else "pen"
            self._crop_box = (0.0, 0.0, float(self.image.width), float(self.image.height)) if self.image else None
            self._crop_drag_fixed = None
            self._crop_drag_offset = None
            self._crop_drag_edge = None
            if self.crop_actions:
                self.crop_actions.pack(side="left", padx=(2, 4), before=self.crop_actions_separator)
                self._enter_crop_ui_mode()
        if name == "eyedropper":
            if previous in annotation_tools:
                self._eyedropper_return_tool = previous
        elif name in annotation_tools:
            self._eyedropper_return_tool = name
            for group, variants in self._tool_variant_labels.items():
                if group == "shapes":
                    shape = name[:-7] if name.endswith("_filled") else name
                    if shape in variants:
                        self._tool_variant_vars[group].set(shape)
                        self.shape_fill_var.set(name.endswith("_filled"))
                elif name in variants:
                    self._tool_variant_vars[group].set(name)
        self.tool.set(name)
        drawing_cursor=name in ("pen", "highlighter", "eraser")
        if not drawing_cursor:self._restore_system_cursor()
        try:self.canvas.configure(cursor="arrow" if drawing_cursor else "crosshair")
        except tk.TclError:self.canvas.configure(cursor="crosshair")
        if drawing_cursor:
            if self.pointer_inside_canvas:self._update_brush_cursor()
            else:self._set_system_cursor(32512)
        else:self._set_system_cursor(32515)
        self._style_mode_buttons()
        messages = {
            "pen": "Draw with a round pen · hold Shift for a straight horizontal line",
            "highlighter": "Drag to highlight with a broad marker stroke · hold Shift to keep it horizontal",
            "eraser": "Click or drag across marks to erase them · Ctrl+Z to undo",
            "rectangle": "Drag to draw an outlined rectangle",
            "rectangle_filled": "Drag to draw a filled rectangle",
            "circle": "Drag to draw an outlined circle",
            "circle_filled": "Drag to draw a filled circle",
            "line": "Drag to draw a straight line",
            "arrow": "Drag to draw an arrow",
            "cross": "Drag to draw a cross",
            "tick": "Drag to draw a tick",
            "crop": "Drag a corner or edge to resize; drag inside to move · Accept to apply · Esc to cancel",
            "eyedropper": "Hover to preview a screen color · click to copy its hex code · Esc to cancel",
        }
        self.status.set(messages[name])
        self._update_brush_cursor()
        if name == "crop":
            self._render_crop_overlay()
        if self._size_preview_items:
            self._show_size_preview()
        if name == "eyedropper":
            self._start_screen_pipette()

    def _start_screen_pipette(self):
        """Show a frozen desktop so the pipette can sample any visible pixel."""
        overlay = None
        try:
            if self._active_choice_popup is not None:
                self._close_choice_popup()
            self.root.update_idletasks()
            self.root.update()
            try:
                ctypes.windll.dwmapi.DwmFlush()
            except Exception:
                pass
            time.sleep(0.08)
            from PIL import ImageGrab
            screenshot = ImageGrab.grab(all_screens=True).convert("RGB")
            x0, y0 = self._virtual_origin()

            overlay = tk.Toplevel(self.root)
            overlay.overrideredirect(True)
            overlay.attributes("-topmost", True)
            overlay.geometry(f"{screenshot.width}x{screenshot.height}{x0:+d}{y0:+d}")
            cursor_spec = f"@{self._create_pipette_cursor_file().as_posix()}"
            overlay.configure(cursor=cursor_spec)
            canvas = tk.Canvas(overlay, highlightthickness=0, cursor=cursor_spec, bd=0)
            canvas.pack(fill="both", expand=True)
            self._pipette_overlay = overlay
            self._pipette_photo = ImageTk.PhotoImage(screenshot, master=canvas)
            canvas.create_image(0, 0, image=self._pipette_photo, anchor="nw")

            tip_items = None
            tip_width, tip_height = 128, 38

            def show_color_preview(event):
                nonlocal tip_items
                px = max(0, min(screenshot.width - 1, int(event.x)))
                py = max(0, min(screenshot.height - 1, int(event.y)))
                color = "#%02X%02X%02X" % screenshot.getpixel((px, py))
                left = event.x + 18
                top = event.y + 18
                if left + tip_width > screenshot.width:
                    left = event.x - tip_width - 18
                if top + tip_height > screenshot.height:
                    top = event.y - tip_height - 18
                left = max(4, left)
                top = max(4, top)
                if tip_items is None:
                    shadow = canvas.create_rectangle(
                        2, 2, 3, 3, fill="#000000", outline="#000000",
                        tags="pipette-preview",
                    )
                    background = canvas.create_rectangle(
                        2, 2, 3, 3, fill=PANEL2, outline=EDGE, width=1,
                        tags="pipette-preview",
                    )
                    swatch = canvas.create_rectangle(
                        2, 2, 3, 3, outline=TEXT, width=1,
                        tags="pipette-preview",
                    )
                    label = canvas.create_text(
                        2, 2, text="", fill=TEXT, anchor="w",
                        font=("Segoe UI Semibold", 10), tags="pipette-preview",
                    )
                    tip_items = shadow, background, swatch, label
                shadow, background, swatch, label = tip_items
                canvas.coords(shadow, left + 2, top + 2, left + tip_width + 2, top + tip_height + 2)
                canvas.coords(background, left, top, left + tip_width, top + tip_height)
                canvas.coords(swatch, left + 7, top + 7, left + 31, top + 31)
                canvas.itemconfigure(swatch, fill=color)
                canvas.coords(label, left + 40, top + tip_height / 2)
                canvas.itemconfigure(label, text=color)
                canvas.tag_raise("pipette-preview")
                return color

            def finish(color=None, copied=False):
                try:
                    overlay.grab_release()
                except tk.TclError:
                    pass
                try:
                    overlay.destroy()
                except tk.TclError:
                    pass
                self._pipette_overlay = None
                self._pipette_photo = None
                self.root.deiconify()
                self.root.lift()
                self.root.focus_force()
                return_tool = self._eyedropper_return_tool
                if return_tool == "eyedropper":
                    return_tool = "pen"
                if color is not None:
                    self.set_color(color)
                self.set_tool(return_tool)
                if copied and color is not None:
                    self.status.set(f"Copied {color} to clipboard · ready to draw")
                    self._show_pipette_copy_notice(color)
                elif color is None:
                    self.status.set("Color picking cancelled")
                return "break"

            def pick_color(event):
                px = max(0, min(screenshot.width - 1, int(event.x)))
                py = max(0, min(screenshot.height - 1, int(event.y)))
                color = "#%02X%02X%02X" % screenshot.getpixel((px, py))
                try:
                    self.root.clipboard_clear()
                    self.root.clipboard_append(color)
                    self.root.update_idletasks()
                except tk.TclError as exc:
                    finish(color)
                    self.status.set(f"Could not copy {color}: {exc}")
                    return "break"
                return finish(color, copied=True)

            canvas.bind("<Motion>", show_color_preview)
            canvas.bind("<ButtonPress-1>", pick_color)
            overlay.bind("<Escape>", lambda _event: finish())
            overlay.bind("<Alt-F4>", lambda _event: finish())
            canvas.bind("<Escape>", lambda _event: finish())
            overlay.focus_force()
            overlay.grab_set()
        except Exception as exc:
            if overlay is not None:
                try:
                    overlay.destroy()
                except tk.TclError:
                    pass
            self._pipette_overlay = None
            self._pipette_photo = None
            try:
                self.root.deiconify()
                self.root.lift()
                self.root.focus_force()
            except tk.TclError:
                pass
            return_tool = self._eyedropper_return_tool
            if return_tool == "eyedropper":
                return_tool = "pen"
            self.set_tool(return_tool)
            messagebox.showerror("Color picker", f"Could not read the desktop colors.\n\n{exc}", parent=self.root)

    def _leave_crop_mode(self):
        self._crop_box = None
        self._crop_drag_fixed = None
        self._crop_drag_offset = None
        self._crop_drag_edge = None
        self._crop_start = None
        if self.crop_actions:
            self.crop_actions.pack_forget()
        self._restore_crop_ui_mode()
        if hasattr(self, "canvas"):
            try:
                self.canvas.delete("crop-overlay")
            except tk.TclError:
                pass
        self._crop_preview_base = None
        self._crop_dimmed_preview = None
        self._crop_overlay_photo = None

    def _enter_crop_ui_mode(self):
        """Temporarily hide editor controls while leaving only crop actions visible."""
        if self._crop_toolbar_snapshot is not None:
            return
        try:
            self.root.update_idletasks()
            self._crop_toolbar_height_state = self.toolbar_row.cget("height")
            toolbar_height = max(
                40, self.toolbar_row.winfo_height(), self.toolbar_row.winfo_reqheight(),
            )
            self.toolbar_row.configure(height=toolbar_height)
            self.toolbar_row.pack_propagate(False)
        except tk.TclError:
            self._crop_toolbar_height_state = None
        snapshot = []

        def hide(widget):
            try:
                manager = widget.winfo_manager()
                if manager == "pack":
                    info = widget.pack_info()
                elif manager == "grid":
                    info = widget.grid_info()
                elif manager == "place":
                    info = widget.place_info()
                else:
                    return
                parent = widget.master
                siblings = getattr(parent, f"{manager}_slaves")()
                following = next(
                    (item for item in siblings[siblings.index(widget) + 1:]
                     if item is not self.crop_actions),
                    None,
                )
                snapshot.append((widget, manager, info, following))
                getattr(widget, f"{manager}_forget")()
            except (tk.TclError, ValueError):
                pass

        keep = set()
        for target in (self.crop_accept_button, self.crop_cancel_button):
            widget = target
            while widget is not None:
                keep.add(widget)
                if widget is self.capture_controls:
                    break
                widget = widget.master

        def hide_siblings_except_crop_actions(parent):
            children = []
            for manager in ("pack", "grid", "place"):
                try:
                    children.extend(getattr(parent, f"{manager}_slaves")())
                except tk.TclError:
                    pass
            for child in children:
                if child in keep:
                    hide_siblings_except_crop_actions(child)
                else:
                    hide(child)

        # Keep the standard window controls usable, but hide the theme button
        # and every capture, drawing, editing, and export control.
        hide(self.theme_button)
        hide_siblings_except_crop_actions(self.capture_controls)
        for button in (self.save_button, self.paint_button, self.copy_button, self.chess_button):
            hide(button)
        self._crop_toolbar_snapshot = snapshot

    def _restore_crop_ui_mode(self):
        snapshot = self._crop_toolbar_snapshot
        if snapshot is None:
            return
        self._crop_toolbar_snapshot = None
        for widget, manager, info, following in reversed(snapshot):
            try:
                options = dict(info)
                options.pop("before", None)
                options.pop("after", None)
                if following is not None and following.winfo_manager() == manager:
                    options["before"] = following
                getattr(widget, manager)(**options)
            except tk.TclError:
                pass
        try:
            self.toolbar_row.pack_propagate(True)
            self.toolbar_row.configure(height=self._crop_toolbar_height_state or 0)
        except tk.TclError:
            pass
        self._crop_toolbar_height_state = None

    def _crop_hit_test(self, canvas_x, canvas_y):
        if not self.image or not self._crop_box:
            return None
        left, top, right, bottom = self._crop_box
        origin_x, origin_y = self.display_origin
        scale = self.fit_scale
        x1, y1 = origin_x + left * scale, origin_y + top * scale
        x2, y2 = origin_x + right * scale, origin_y + bottom * scale
        corners = (
            (x1, y1, right, bottom, "nw"),
            (x2, y1, left, bottom, "ne"),
            (x2, y2, left, top, "se"),
            (x1, y2, right, top, "sw"),
        )
        nearest = min(
            corners,
            key=lambda item: (canvas_x - item[0]) ** 2 + (canvas_y - item[1]) ** 2,
        )
        if (canvas_x - nearest[0]) ** 2 + (canvas_y - nearest[1]) ** 2 <= 14 ** 2:
            return ("corner", nearest[2], nearest[3], nearest[4])

        edge_hits = []
        if x1 + 14 <= canvas_x <= x2 - 14:
            edge_hits.extend(((abs(canvas_y - y1), "top"), (abs(canvas_y - y2), "bottom")))
        if y1 + 14 <= canvas_y <= y2 - 14:
            edge_hits.extend(((abs(canvas_x - x1), "left"), (abs(canvas_x - x2), "right")))
        edge_distance, edge_name = min(edge_hits, default=(float("inf"), None))
        if edge_name is not None and edge_distance <= 12:
            return ("edge", edge_name)

        image_x = (canvas_x - origin_x) / scale
        image_y = (canvas_y - origin_y) / scale
        if left <= image_x <= right and top <= image_y <= bottom:
            return ("move", image_x - left, image_y - top)
        return None

    def _set_crop_cursor(self, event):
        if self.tool.get() != "crop":
            return
        canvas_x, canvas_y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        hit = self._crop_hit_test(canvas_x, canvas_y)
        if hit is None:
            cursor = "crosshair"
        elif hit[0] == "corner":
            cursor = "size_ne_sw" if hit[3] in ("ne", "sw") else "size_nw_se"
        elif hit[0] == "edge":
            cursor = "sb_v_double_arrow" if hit[1] in ("top", "bottom") else "sb_h_double_arrow"
        else:
            cursor = "fleur"
        try:
            self.canvas.configure(cursor=cursor)
        except tk.TclError:
            self.canvas.configure(cursor="crosshair")

    def _update_crop_drag(self, event):
        if not self.image or not self._crop_box:
            return
        x, y = self._to_image(event.x, event.y)
        x = max(0.0, min(float(self.image.width), x))
        y = max(0.0, min(float(self.image.height), y))
        left, top, right, bottom = self._crop_box
        if self._crop_drag_offset is not None:
            offset_x, offset_y = self._crop_drag_offset
            box_width, box_height = right - left, bottom - top
            new_left = max(0.0, min(float(self.image.width) - box_width, x - offset_x))
            new_top = max(0.0, min(float(self.image.height) - box_height, y - offset_y))
            self._crop_box = (new_left, new_top, new_left + box_width, new_top + box_height)
        elif self._crop_drag_edge is not None:
            edge = self._crop_drag_edge
            min_width = min(8.0, float(self.image.width))
            min_height = min(8.0, float(self.image.height))
            if edge == "left":
                left = min(max(0.0, x), right - min_width)
            elif edge == "right":
                right = max(min(float(self.image.width), x), left + min_width)
            elif edge == "top":
                top = min(max(0.0, y), bottom - min_height)
            elif edge == "bottom":
                bottom = max(min(float(self.image.height), y), top + min_height)
            self._crop_box = (left, top, right, bottom)
        elif self._crop_drag_fixed is not None:
            fixed_x, fixed_y = self._crop_drag_fixed
            self._crop_box = (
                min(fixed_x, x), min(fixed_y, y),
                max(fixed_x, x), max(fixed_y, y),
            )
        else:
            return
        self._render_crop_overlay()

    def _render_crop_overlay(self):
        if not self.image or not self.canvas or self.tool.get() != "crop" or not self._crop_box:
            return
        self.canvas.delete("crop-overlay")
        if self._crop_preview_base is None:
            composed = self._composited()
            self._crop_preview_base = self._image_as_rgb(
                composed.resize(self.image_display_size, Image.Resampling.LANCZOS),
                self._editor_canvas_rgb(),
            )
            from PIL import ImageEnhance
            self._crop_dimmed_preview = ImageEnhance.Brightness(
                self._crop_preview_base,
            ).enhance(0.50)
        left, top, right, bottom = self._crop_box
        ox, oy = self.display_origin
        scale = self.fit_scale
        x1, y1 = ox + left * scale, oy + top * scale
        x2, y2 = ox + right * scale, oy + bottom * scale
        width, height = self.image_display_size
        edge = "#756dff" if self.theme_name == "dark" else "#6158df"
        mask = Image.new("L", (width, height), 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.rectangle((
            max(0, min(width - 1, round(left * scale))),
            max(0, min(height - 1, round(top * scale))),
            max(0, min(width - 1, round(right * scale))),
            max(0, min(height - 1, round(bottom * scale))),
        ), fill=255)
        crop_preview = Image.composite(self._crop_preview_base, self._crop_dimmed_preview, mask)
        self._crop_overlay_photo = ImageTk.PhotoImage(crop_preview, master=self.canvas)
        self.canvas.create_image(
            ox, oy, image=self._crop_overlay_photo, anchor="nw", tags="crop-overlay",
        )
        self.canvas.create_rectangle(
            x1, y1, x2, y2, outline=edge, width=2, dash=(5, 3),
            tags="crop-overlay",
        )
        handle_size = 9
        for hx, hy in ((x1, y1), (x2, y1), (x2, y2), (x1, y2)):
            self.canvas.create_rectangle(
                hx - handle_size, hy - handle_size,
                hx + handle_size, hy + handle_size,
                fill="white", outline=edge, width=2, tags="crop-overlay",
            )
        side_handle_width = 16
        side_handle_height = 6
        mid_x, mid_y = (x1 + x2) / 2, (y1 + y2) / 2
        for hx, hy in ((mid_x, y1), (mid_x, y2)):
            self.canvas.create_rectangle(
                hx - side_handle_width / 2, hy - side_handle_height / 2,
                hx + side_handle_width / 2, hy + side_handle_height / 2,
                fill="white", outline=edge, width=2, tags="crop-overlay",
            )
        for hx, hy in ((x1, mid_y), (x2, mid_y)):
            self.canvas.create_rectangle(
                hx - side_handle_height / 2, hy - side_handle_width / 2,
                hx + side_handle_height / 2, hy + side_handle_width / 2,
                fill="white", outline=edge, width=2, tags="crop-overlay",
            )
        self.canvas.tag_raise("crop-overlay")
        if self.size_badge:
            self.canvas.tag_raise(self.size_badge)

    def _cancel_crop(self, _event=None):
        if self.tool.get() != "crop":
            return "break"
        return_tool = self._crop_return_tool
        self._leave_crop_mode()
        self.set_tool(return_tool)
        self.status.set("Crop cancelled")
        if self.image:
            self._render_preview()
        return "break"

    def _accept_crop(self, _event=None):
        if self.tool.get() != "crop" or not self.image or not self._crop_box:
            return "break"
        left, top, right, bottom = self._crop_box
        x1, y1 = max(0, int(math.floor(left))), max(0, int(math.floor(top)))
        x2, y2 = min(self.image.width, int(math.ceil(right))), min(self.image.height, int(math.ceil(bottom)))
        return_tool = self._crop_return_tool
        if x2 - x1 < 8 or y2 - y1 < 8:
            self.status.set("Crop area is too small")
            return "break"
        changed = (x1, y1, x2, y2) != (0, 0, self.image.width, self.image.height)
        self._leave_crop_mode()
        if changed:
            box = (x1, y1, x2, y2)
            self._clear_recognition_regions()
            self._push_history()
            pending = self._composite_image(Image.new("RGBA", self.image.size, (0, 0, 0, 0)), self.strokes)
            annotations = self.annotation_layer or Image.new("RGBA", self.image.size, (0, 0, 0, 0))
            self.image = self.image.crop(box).convert("RGBA")
            self.annotation_layer = Image.alpha_composite(annotations.convert("RGBA"), pending).crop(box)
            self.background_layer = Image.new("RGBA", self.image.size, (0, 0, 0, 0))
            self.strokes = []
            self._reset_rotation_baseline(rebase=True)
            final_status = f"Cropped to {x2-x1} × {y2-y1} px"
        else:
            final_status = "Crop area unchanged"
        self.set_tool(return_tool)
        self._render_preview()
        self.status.set(final_status)
        if changed:
            self._schedule_auto_clipboard()
        return "break"

    def set_color(self, color, hsv_state=None):
        color_changed = self.color.get().lower() != color.lower()
        self.color.set(color)
        selected_shape = getattr(self, "_selected_shape", None)
        selected_shape_is_live = selected_shape is not None and any(
            stroke is selected_shape for stroke in getattr(self, "strokes", ())
        )
        if color_changed and selected_shape_is_live:
            if not self._shape_color_edit_history_pushed:
                self._push_history()
                self._shape_color_edit_history_pushed = True
            selected_shape["color"] = color
            self._redraw_transformed_shape(selected_shape)
            self._schedule_auto_clipboard()
        keep_hsv = hsv_state is not None or not color_changed
        try:
            if hsv_state is None:
                if color_changed:
                    red, green, blue = (int(color[index:index + 2], 16) / 255 for index in (1, 3, 5))
                    hsv_state = colorsys.rgb_to_hsv(red, green, blue)
                else:
                    hsv_state = (
                        self.hue.get() / 360,
                        self.saturation.get() / 100,
                        self.brightness.get() / 100,
                    )
            hue, saturation, value = hsv_state
            if saturation > 0 or keep_hsv:
                self.hue.set(round(hue * 360) % 360)
            self.saturation.set(round(saturation * 100))
            self.brightness.set(round(value * 100))
            gradient_hue = self.hue.get() / 360
            gradient_saturation = self.saturation.get() / 100
            if self.saturation_slider is not None:
                self.saturation_slider.set_gradient_color(
                    gradient_hue, gradient_saturation, self.brightness.get() / 100,
                )
            if self.brightness_slider is not None:
                self.brightness_slider.set_gradient_color(gradient_hue, gradient_saturation)
        except (TypeError, ValueError):
            pass
        if self.color_picker_button:
            self.color_picker_button.configure(bg=color, activebackground=color)
        selected_swatch = next(
            (swatch for swatch in self._palette_color_names
             if swatch.lower() == color.lower()),
            None,
        )
        previous_swatch = getattr(self, "_selected_palette_swatch", None)
        self._selected_palette_swatch = selected_swatch
        for swatch in {previous_swatch, selected_swatch}:
            button = self.color_buttons.get(swatch) if swatch else None
            if button is None:
                continue
            icon = self._create_palette_swatch_icon(
                swatch, swatch == selected_swatch,
                getattr(button, "_hovered", False),
            )
            button.itemconfigure("swatch", image=icon)
            button._swatch_photo = icon

    def _create_palette_swatch_icon(self, color, selected, hovered=False):
        """Render a smooth, round palette swatch with a crisp selection mark."""
        scale = 5
        size = 48
        bitmap = Image.new("RGBA", (size * scale, size * scale), (0, 0, 0, 0))
        draw = ImageDraw.Draw(bitmap)
        diameter = 40 if hovered else 36
        center = size / 2
        radius = diameter / 2
        draw.ellipse(
            ((center - radius) * scale, (center - radius) * scale,
             (center + radius) * scale, (center + radius) * scale),
            fill=color,
            outline=ACCENT if selected else EDGE,
            width=(3 if selected else 1) * scale,
        )
        if selected:
            luminance = sum(
                int(color[i:i + 2], 16) * weight
                for i, weight in ((1, 0.299), (3, 0.587), (5, 0.114))
            )
            check_color = "#17191f" if luminance > 145 else "#ffffff"
            factor = diameter / 28
            draw.line(
                (((center - 7 * factor) * scale, center * scale),
                 ((center - 2 * factor) * scale, (center + 5 * factor) * scale),
                 ((center + 7 * factor) * scale, (center - 6 * factor) * scale)),
                fill=check_color, width=round(2 * factor * scale), joint="curve",
            )
        return ImageTk.PhotoImage(
            bitmap.resize((size, size), Image.Resampling.LANCZOS), master=self.root,
        )

    def _popup_color_palette(self):
        if self._active_choice_popup is not None:
            if getattr(self._active_choice_popup, "_chesssnip_shape_menu", False):
                shape_popup = self._active_choice_popup
                place = shape_popup.place_info()
                self._suspended_shape_popup_place = {
                    "x": int(float(place.get("x", 0))),
                    "y": int(float(place.get("y", 0))),
                    "width": int(float(place.get("width", shape_popup.winfo_width()))),
                    "height": int(float(place.get("height", shape_popup.winfo_height()))),
                }
                if self._choice_popup_animation_after is not None:
                    try:
                        self.root.after_cancel(self._choice_popup_animation_after)
                    except tk.TclError:
                        pass
                    self._choice_popup_animation_after = None
                self._choice_popup_animating = False
                self._suspended_shape_popup = shape_popup
                self._active_choice_popup = None
                shape_popup.place_forget()
            else:
                self._close_choice_popup(refresh_cursor=False)
        self._shape_color_edit_history_pushed = False

        palette_surface = "#ffffff" if self.theme_name == "light" else PANEL2
        popup = tk.Frame(self.root, bg=PANEL, bd=0, highlightthickness=0)
        popup._chesssnip_color_palette = True
        self._active_choice_popup = popup
        backdrop = tk.Canvas(popup, bg=PANEL, highlightthickness=0, bd=0)
        backdrop.place(relx=0, rely=0, relwidth=1, relheight=1)
        popup._color_palette_backdrop = backdrop
        body = tk.Frame(popup, bg=palette_surface, bd=0, padx=8, pady=8)
        body.pack(fill="both", expand=True, padx=9, pady=9)
        popup._chesssnip_popup_body = body
        self.color_buttons = {}
        self._selected_palette_swatch = next(
            (swatch for swatch in self._palette_color_names
             if swatch.lower() == self.color.get().lower()),
            None,
        )
        columns = 6
        for index, (color, name) in enumerate(self._palette_color_names.items()):
            selected = color.lower() == self.color.get().lower()
            swatch = tk.Canvas(
                body, width=48, height=48, bg=palette_surface,
                highlightthickness=0, bd=0, cursor="hand2",
            )
            icon = self._create_palette_swatch_icon(color, selected)
            swatch.create_image(24, 24, image=icon, anchor="center", tags="swatch")
            swatch._swatch_photo = icon
            swatch._hovered = False
            swatch.grid(row=index // columns, column=index % columns, padx=2, pady=2)
            swatch.bind("<Button-1>", lambda _event, value=color: self._choose_palette_color(value))
            swatch.bind("<Enter>", lambda _event, value=color: self._set_palette_swatch_hover(value, True))
            swatch.bind("<Leave>", lambda _event, value=color: self._set_palette_swatch_hover(value, False))
            self.color_buttons[color] = swatch
            self._add_tooltip(swatch, f"Select {name.lower()} annotation color")

        color_rows = (len(self._palette_color_names) + columns - 1) // columns
        hue_control = tk.Frame(body, bg=palette_surface)
        hue_control.grid(row=color_rows, column=0, columnspan=columns, sticky="ew", pady=(5, 1))
        tk.Label(
            hue_control, text="HUE", bg=palette_surface, fg=TEXT,
            font=("Segoe UI", 8, "bold"), width=12, anchor="w",
        ).pack(side="left", padx=(2, 5))
        self.hue_slider = ModernSlider(
            hue_control, self.hue, 0, 360, length=250,
            command=self._hue_slider_changed, hue_gradient=True,
            background=palette_surface,
        )
        self.hue_slider.pack(side="left", padx=(0, 2))
        self._add_tooltip(self.hue_slider, "Choose a hue")

        saturation_control = tk.Frame(body, bg=palette_surface)
        saturation_control.grid(row=color_rows + 1, column=0, columnspan=columns, sticky="ew", pady=(1, 0))
        tk.Label(
            saturation_control, text="SAT", bg=palette_surface, fg=TEXT,
            font=("Segoe UI", 8, "bold"), width=12, anchor="w",
        ).pack(side="left", padx=(2, 5))
        self.saturation_slider = ModernSlider(
            saturation_control, self.saturation, 0, 100, length=250,
            command=self._saturation_slider_changed,
            saturation_gradient=True, gradient_hue=self.hue.get() / 360,
            gradient_value=self.brightness.get() / 100, background=palette_surface,
        )
        self.saturation_slider.pack(side="left", padx=(0, 2))
        self._add_tooltip(self.saturation_slider, "Set color saturation")

        brightness_control = tk.Frame(body, bg=palette_surface)
        brightness_control.grid(row=color_rows + 2, column=0, columnspan=columns, sticky="ew", pady=(1, 0))
        tk.Label(
            brightness_control, text="BRIGHTNESS", bg=palette_surface, fg=TEXT,
            font=("Segoe UI", 8, "bold"), width=12, anchor="w",
        ).pack(side="left", padx=(2, 5))
        self.brightness_slider = ModernSlider(
            brightness_control, self.brightness, 0, 100, length=250,
            command=self._brightness_slider_changed,
            brightness_gradient=True, gradient_hue=self.hue.get() / 360,
            gradient_saturation=self.saturation.get() / 100,
            background=palette_surface,
        )
        self.brightness_slider.pack(side="left", padx=(0, 2))
        self._add_tooltip(self.brightness_slider, "Set color brightness")

        popup.update_idletasks()
        card_width = max(1, popup.winfo_reqwidth() * 3)
        card_height = max(1, popup.winfo_reqheight() * 3)
        card = Image.new("RGBA", (card_width, card_height), (0, 0, 0, 0))
        ImageDraw.Draw(card).rounded_rectangle(
            (0, 0, card_width - 1, card_height - 1),
            radius=30, fill=palette_surface, outline=EDGE, width=3,
        )
        card_photo = ImageTk.PhotoImage(
            card.resize((card_width // 3, card_height // 3), Image.Resampling.LANCZOS),
            master=popup,
        )
        backdrop.create_image(0, 0, image=card_photo, anchor="nw")
        popup._color_palette_background_photo = card_photo
        popup_width = max(self.color_picker_button.winfo_width(), popup.winfo_reqwidth())
        popup_height = popup.winfo_reqheight()
        x, y = self._dropdown_popup_origin(
            self.color_picker_button, popup_width, popup_height,
        )
        self._animate_choice_popup(popup, x, y, popup_width, popup_height)

    def _choose_palette_color(self, color):
        self.set_color(color)
        if self._color_palette_close_after is not None:
            try:
                self.root.after_cancel(self._color_palette_close_after)
            except tk.TclError:
                pass
        self._color_palette_close_after = self.root.after(
            55, self._finish_palette_color_choice,
        )

    def _set_palette_swatch_hover(self, color, hovered):
        button = self.color_buttons.get(color)
        if button is None:
            return
        button._hovered = hovered
        selected = color.lower() == self.color.get().lower()
        icon = self._create_palette_swatch_icon(color, selected, hovered)
        button.itemconfigure("swatch", image=icon)
        button._swatch_photo = icon

    def _finish_palette_color_choice(self):
        self._color_palette_close_after = None
        self._close_choice_popup()

    def _hue_slider_changed(self, value):
        hue = (int(float(value)) % 360) / 360
        saturation = self.saturation.get() / 100
        brightness = self.brightness.get() / 100
        red, green, blue = colorsys.hsv_to_rgb(hue, saturation, brightness)
        self.set_color("#%02x%02x%02x" % (
            round(red * 255), round(green * 255), round(blue * 255),
        ), hsv_state=(hue, saturation, brightness))

    def _saturation_slider_changed(self, value):
        hue = (self.hue.get() % 360) / 360
        saturation = max(0, min(100, int(float(value)))) / 100
        brightness = self.brightness.get() / 100
        red, green, blue = colorsys.hsv_to_rgb(hue, saturation, brightness)
        self.set_color("#%02x%02x%02x" % (
            round(red * 255), round(green * 255), round(blue * 255),
        ), hsv_state=(hue, saturation, brightness))

    def _brightness_slider_changed(self, value):
        hue = (self.hue.get() % 360) / 360
        saturation = self.saturation.get() / 100
        brightness = max(0, min(100, int(float(value)))) / 100
        red, green, blue = colorsys.hsv_to_rgb(hue, saturation, brightness)
        self.set_color("#%02x%02x%02x" % (
            round(red * 255), round(green * 255), round(blue * 255),
        ), hsv_state=(hue, saturation, brightness))

    def _register_hotkey(self):
        self._hotkey = False
        try:
            self._hotkey = bool(ctypes.windll.user32.RegisterHotKey(None, 0x51A0, 0x0002 | 0x0004, ord("X")))
        except Exception: pass

    def _poll_hotkey(self):
        if self._hotkey:
            msg = wintypes.MSG()
            while ctypes.windll.user32.PeekMessageW(ctypes.byref(msg), None, 0x0312, 0x0312, 1):
                if msg.message == 0x0312 and msg.wParam == 0x51A0:
                    self.root.after(0, self.begin_capture)
        self.root.after(100, self._poll_hotkey)

    def _close_chess_reviews_for_capture(self):
        for window in self._chess_review_windows[:]:
            try:
                if window.winfo_exists():
                    window.destroy()
            except tk.TclError:
                self._forget_chess_review_window(window)

    def _forget_chess_review_window(self, window):
        try:
            self._chess_review_windows.remove(window)
        except ValueError:
            pass

    def _create_countdown_icon(self, seconds, size=32):
        from PIL import ImageDraw, ImageFont
        render_size = size * 3
        bitmap = Image.new("RGBA", (render_size, render_size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(bitmap)
        scale = render_size / 32
        draw.ellipse(
            (scale, scale, render_size - scale - 1, render_size - scale - 1),
            fill=ACCENT, outline="#e4e1ff", width=max(1, round(scale)),
        )
        label = str(seconds)
        font_size = (22 if len(label) == 1 else 17) * scale
        try:
            font_path = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "segoeuib.ttf"
            font = ImageFont.truetype(str(font_path), round(font_size))
        except Exception:
            font = ImageFont.load_default()
        draw.text((render_size / 2, render_size / 2), label, fill="white", font=font, anchor="mm", stroke_width=0)
        bitmap = bitmap.resize((size, size), Image.Resampling.LANCZOS)
        pixels = bitmap.tobytes("raw", "BGRA")

        gdi32 = ctypes.windll.gdi32
        user32 = ctypes.windll.user32
        gdi32.CreateDIBSection.argtypes = (
            wintypes.HDC, ctypes.POINTER(_BITMAPINFO), wintypes.UINT,
            ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD,
        )
        gdi32.CreateDIBSection.restype = wintypes.HBITMAP
        gdi32.CreateBitmap.argtypes = (ctypes.c_int, ctypes.c_int, wintypes.UINT, wintypes.UINT, ctypes.c_void_p)
        gdi32.CreateBitmap.restype = wintypes.HBITMAP
        gdi32.DeleteObject.argtypes = (wintypes.HGDIOBJ,)
        gdi32.DeleteObject.restype = wintypes.BOOL
        user32.CreateIconIndirect.argtypes = (ctypes.POINTER(_ICONINFO),)
        user32.CreateIconIndirect.restype = wintypes.HICON
        user32.DestroyIcon.argtypes = (wintypes.HICON,)
        user32.DestroyIcon.restype = wintypes.BOOL

        info = _BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        info.bmiHeader.biWidth = size
        info.bmiHeader.biHeight = -size  # top-down DIB
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = 0  # BI_RGB
        bits = ctypes.c_void_p()
        color_bitmap = gdi32.CreateDIBSection(None, ctypes.byref(info), 0, ctypes.byref(bits), None, 0)
        if not color_bitmap or not bits.value:
            raise ctypes.WinError()
        ctypes.memmove(bits.value, pixels, len(pixels))
        mask_bits = ctypes.create_string_buffer(bytes(size * 4))
        mask_bitmap = gdi32.CreateBitmap(size, size, 1, 1, mask_bits)
        if not mask_bitmap:
            gdi32.DeleteObject(color_bitmap)
            raise ctypes.WinError()
        icon_info = _ICONINFO(True, 0, 0, mask_bitmap, color_bitmap)
        try:
            icon = user32.CreateIconIndirect(ctypes.byref(icon_info))
            if not icon:
                raise ctypes.WinError()
            return icon
        finally:
            gdi32.DeleteObject(mask_bitmap)
            gdi32.DeleteObject(color_bitmap)

    def _create_capture_cursor(self, size=32):
        """Create a high-contrast crosshair cursor for the dimmed capture view."""
        from PIL import ImageDraw
        scale = 3
        render_size = size * scale
        center = render_size // 2
        bitmap = Image.new("RGBA", (render_size, render_size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(bitmap)
        gap = 4 * scale
        arm_start = 2 * scale
        arm_end = center - gap
        for start, end in ((arm_start, arm_end), (center + gap, render_size - arm_start - 1)):
            draw.line((start, center, end, center), fill=(0, 0, 0, 255), width=3 * scale)
            draw.line((center, start, center, end), fill=(0, 0, 0, 255), width=3 * scale)
            draw.line((start, center, end, center), fill=(255, 255, 255, 255), width=scale)
            draw.line((center, start, center, end), fill=(255, 255, 255, 255), width=scale)
        bitmap = bitmap.resize((size, size), Image.Resampling.LANCZOS)
        return self._create_native_cursor(bitmap, (size // 2, size // 2))

    def _create_pipette_cursor_file(self, size=32):
        """Write a native Windows cursor file so Tk can keep it stable."""
        from PIL import ImageDraw
        import struct

        scale = 4
        bitmap = Image.new("RGBA", (size * scale, size * scale), (0, 0, 0, 0))
        draw = ImageDraw.Draw(bitmap)

        def point(x, y):
            return round(x * scale), round(y * scale)

        # A bright barrel with a dark edge stays legible over light and dark
        # desktop areas. Its lower-left tip is the actual sampling hotspot.
        draw.line((*point(6, 25), *point(23, 8)), fill="#101116", width=10 * scale)
        draw.line((*point(6, 25), *point(23, 8)), fill="#F8F9FC", width=7 * scale)
        draw.line((*point(7, 24), *point(22, 9)), fill="#536FC0", width=3 * scale)
        draw.line((*point(4, 27), *point(8, 23)), fill="#101116", width=5 * scale)
        draw.line((*point(4, 27), *point(8, 23)), fill="#F8F9FC", width=2 * scale)
        draw.line((*point(19, 6), *point(26, 13)), fill="#101116", width=8 * scale)
        draw.line((*point(19, 6), *point(26, 13)), fill="#D9DCE6", width=4 * scale)
        draw.ellipse(
            (19 * scale, 4 * scale, 28 * scale, 13 * scale),
            fill="#F8F9FC", outline="#101116", width=2 * scale,
        )
        draw.ellipse((22 * scale, 6 * scale, 26 * scale, 10 * scale), fill="#536FC0")
        bitmap = bitmap.resize((size, size), Image.Resampling.LANCZOS)
        bgra = bitmap.tobytes("raw", "BGRA")
        row_bytes = size * 4
        xor_mask = b"".join(
            bgra[row * row_bytes:(row + 1) * row_bytes]
            for row in range(size - 1, -1, -1)
        )
        mask_row_bytes = ((size + 31) // 32) * 4
        and_mask = bytearray(mask_row_bytes * size)
        rgba = bitmap.tobytes("raw", "RGBA")
        for output_row, source_y in enumerate(range(size - 1, -1, -1)):
            for x in range(size):
                alpha_offset = (source_y * size + x) * 4 + 3
                if rgba[alpha_offset] == 0:
                    and_mask[output_row * mask_row_bytes + x // 8] |= 0x80 >> (x % 8)

        dib_header = struct.pack(
            "<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0,
            len(xor_mask), 0, 0, 0, 0,
        )
        cursor_image = dib_header + xor_mask + bytes(and_mask)
        cursor_header = struct.pack("<HHH", 0, 2, 1)
        cursor_entry = struct.pack(
            "<BBBBHHII", size, size, 0, 0, 4, 27,
            len(cursor_image), 22,
        )
        cursor_path = Path(tempfile.gettempdir()) / "ChessSnip" / "pipette_cursor.cur"
        cursor_path.parent.mkdir(parents=True, exist_ok=True)
        cursor_path.write_bytes(cursor_header + cursor_entry + cursor_image)
        return cursor_path

    def _create_native_cursor(self, bitmap, hotspot):
        """Turn an RGBA Pillow image into a Windows color cursor."""
        bitmap = bitmap.convert("RGBA")
        size, height = bitmap.size
        if size != height:
            raise ValueError("Windows cursors must be square")
        pixels = bitmap.tobytes("raw", "BGRA")
        gdi32 = ctypes.windll.gdi32
        user32 = ctypes.windll.user32
        gdi32.CreateDIBSection.argtypes = (
            wintypes.HDC, ctypes.POINTER(_BITMAPINFO), wintypes.UINT,
            ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD,
        )
        gdi32.CreateDIBSection.restype = wintypes.HBITMAP
        gdi32.CreateBitmap.argtypes = (ctypes.c_int, ctypes.c_int, wintypes.UINT, wintypes.UINT, ctypes.c_void_p)
        gdi32.CreateBitmap.restype = wintypes.HBITMAP
        gdi32.DeleteObject.argtypes = (wintypes.HGDIOBJ,)
        gdi32.DeleteObject.restype = wintypes.BOOL
        user32.CreateIconIndirect.argtypes = (ctypes.POINTER(_ICONINFO),)
        user32.CreateIconIndirect.restype = wintypes.HICON

        info = _BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        info.bmiHeader.biWidth = size
        info.bmiHeader.biHeight = -size
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = 0
        bits = ctypes.c_void_p()
        color_bitmap = gdi32.CreateDIBSection(None, ctypes.byref(info), 0, ctypes.byref(bits), None, 0)
        if not color_bitmap or not bits.value:
            raise ctypes.WinError()
        ctypes.memmove(bits.value, pixels, len(pixels))
        mask_bits = ctypes.create_string_buffer(bytes(size * (((size + 31) // 32) * 4)))
        mask_bitmap = gdi32.CreateBitmap(size, size, 1, 1, mask_bits)
        if not mask_bitmap:
            gdi32.DeleteObject(color_bitmap)
            raise ctypes.WinError()
        cursor_info = _ICONINFO(False, hotspot[0], hotspot[1], mask_bitmap, color_bitmap)
        try:
            cursor = user32.CreateIconIndirect(ctypes.byref(cursor_info))
            if not cursor:
                raise ctypes.WinError()
            return cursor
        finally:
            gdi32.DeleteObject(mask_bitmap)
            gdi32.DeleteObject(color_bitmap)

    def _ensure_taskbar_list(self):
        if self._taskbar_list:
            return True
        ole32 = ctypes.windll.ole32
        ole32.CoInitializeEx.argtypes = (ctypes.c_void_p, wintypes.DWORD)
        ole32.CoInitializeEx.restype = ctypes.c_long
        init_result = ole32.CoInitializeEx(None, 2)  # COINIT_APARTMENTTHREADED
        changed_mode = ctypes.c_long(0x80010106).value  # RPC_E_CHANGED_MODE
        if init_result >= 0:
            self._taskbar_com_initialized = True
        elif init_result != changed_mode:
            return False

        clsid = _guid("56FDF344-FD6D-11D0-958A-006097C9A090")
        iid = _guid("EA1AFB91-9E28-4B86-90E9-9E9F8A5EEFAF")  # ITaskbarList3
        ole32.CoCreateInstance.argtypes = (
            ctypes.POINTER(_GUID), ctypes.c_void_p, wintypes.DWORD,
            ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p),
        )
        ole32.CoCreateInstance.restype = ctypes.c_long
        instance = ctypes.c_void_p()
        result = ole32.CoCreateInstance(
            ctypes.byref(clsid), None, 4, ctypes.byref(iid), ctypes.byref(instance),
        )  # CLSCTX_INPROC_SERVER
        if result < 0 or not instance:
            if self._taskbar_com_initialized:
                ole32.CoUninitialize()
                self._taskbar_com_initialized = False
            return False
        vtable = ctypes.cast(instance, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        hr_init = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)(vtable[3])
        if hr_init(instance) < 0:
            _release_com(instance)
            if self._taskbar_com_initialized:
                ole32.CoUninitialize()
                self._taskbar_com_initialized = False
            return False
        self._taskbar_list = instance
        return True

    def _set_taskbar_overlay(self, icon, description):
        if not self._ensure_taskbar_list():
            return False
        hwnd = self._taskbar_hwnd
        if not hwnd:
            user32 = ctypes.windll.user32
            user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
            user32.GetAncestor.restype = wintypes.HWND
            hwnd = user32.GetAncestor(self.root.winfo_id(), 2) or self.root.winfo_id()
            self._taskbar_hwnd = hwnd
        vtable = ctypes.cast(self._taskbar_list, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        set_overlay = ctypes.WINFUNCTYPE(
            ctypes.c_long, ctypes.c_void_p, wintypes.HWND, wintypes.HICON, wintypes.LPCWSTR,
        )(vtable[18])  # ITaskbarList3::SetOverlayIcon
        return set_overlay(self._taskbar_list, hwnd, icon, description) >= 0

    def _set_taskbar_progress(self, remaining, total):
        """Show countdown progress on the taskbar button, including pinned groups."""
        if remaining <= 0 or total <= 0:
            if not self._taskbar_list:
                return False
        elif not self._ensure_taskbar_list():
            return False
        hwnd = self._taskbar_hwnd
        if not hwnd:
            user32 = ctypes.windll.user32
            user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
            user32.GetAncestor.restype = wintypes.HWND
            hwnd = user32.GetAncestor(self.root.winfo_id(), 2) or self.root.winfo_id()
            self._taskbar_hwnd = hwnd
        vtable = ctypes.cast(self._taskbar_list, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        set_progress_value = ctypes.WINFUNCTYPE(
            ctypes.c_long, ctypes.c_void_p, wintypes.HWND,
            ctypes.c_ulonglong, ctypes.c_ulonglong,
        )(vtable[9])  # ITaskbarList3::SetProgressValue
        set_progress_state = ctypes.WINFUNCTYPE(
            ctypes.c_long, ctypes.c_void_p, wintypes.HWND, ctypes.c_int,
        )(vtable[10])  # ITaskbarList3::SetProgressState
        if remaining <= 0 or total <= 0:
            return set_progress_state(self._taskbar_list, hwnd, 0) >= 0  # TBPF_NOPROGRESS
        state_result = set_progress_state(self._taskbar_list, hwnd, 2)  # TBPF_NORMAL
        value_result = set_progress_value(
            self._taskbar_list, hwnd, max(0, total - remaining), total,
        )
        return state_result >= 0 and value_result >= 0

    def _set_window_countdown_icons(self, small_icon, large_icon):
        user32 = ctypes.windll.user32
        user32.SendMessageW.argtypes = (
            wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t,
        )
        user32.SendMessageW.restype = ctypes.c_ssize_t
        hwnd = self._taskbar_hwnd
        if not hwnd:
            user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
            user32.GetAncestor.restype = wintypes.HWND
            hwnd = user32.GetAncestor(self.root.winfo_id(), 2) or self.root.winfo_id()
            self._taskbar_hwnd = hwnd
        wm_geticon, wm_seticon = 0x007F, 0x0080
        icon_small, icon_big = 0, 1
        if self._countdown_original_window_icons is None:
            old_small = user32.SendMessageW(hwnd, wm_geticon, icon_small, 0)
            old_large = user32.SendMessageW(hwnd, wm_geticon, icon_big, 0)
            self._countdown_original_window_icons = (old_small, old_large)
        user32.SendMessageW(hwnd, wm_seticon, icon_small, int(small_icon))
        user32.SendMessageW(hwnd, wm_seticon, icon_big, int(large_icon))

    def _update_countdown_taskbar(self, seconds):
        small_icon = large_icon = None
        retained = False
        try:
            # Update the real window icons as well as the taskbar overlay. This
            # keeps the countdown visible even when Windows suppresses overlays.
            small_icon = self._create_countdown_icon(seconds, 16)
            large_icon = self._create_countdown_icon(seconds, 32)
            self._countdown_overlay_icons.extend((small_icon, large_icon))
            retained = True
            self._set_window_countdown_icons(small_icon, large_icon)
            description = f"ChessSnip capture in {seconds} second" + ("s" if seconds != 1 else "")
            self._set_taskbar_overlay(small_icon, description)
            self._set_taskbar_progress(seconds, self._countdown_total_seconds)
        except Exception:
            pass
        finally:
            if not retained:
                for icon in (small_icon, large_icon):
                    if icon:
                        try:
                            ctypes.windll.user32.DestroyIcon(icon)
                        except Exception:
                            pass

    def _remove_countdown_taskbar_overlay(self):
        try:self.root.title("ChessSnip")
        except tk.TclError:pass
        if self._countdown_original_window_icons is not None:
            try:
                user32 = ctypes.windll.user32
                hwnd = self._taskbar_hwnd or self.root.winfo_id()
                user32.SendMessageW(hwnd, 0x0080, 0, self._countdown_original_window_icons[0])
                user32.SendMessageW(hwnd, 0x0080, 1, self._countdown_original_window_icons[1])
            except Exception:
                pass
            self._countdown_original_window_icons = None
        if self._taskbar_list:
            try:
                self._set_taskbar_progress(0, 0)
            except Exception:
                pass
            try:
                self._set_taskbar_overlay(None, None)
            except Exception:
                pass
            _release_com(self._taskbar_list)
            self._taskbar_list = None
        if self._taskbar_com_initialized:
            try:ctypes.windll.ole32.CoUninitialize()
            except Exception:pass
            self._taskbar_com_initialized = False
        for icon in self._countdown_overlay_icons:
            try:ctypes.windll.user32.DestroyIcon(icon)
            except Exception:pass
        self._countdown_overlay_icons.clear()
        self._countdown_total_seconds = 0

    def begin_capture(self):
        self._restore_system_cursor()
        self._close_chess_reviews_for_capture()
        if self._timer:
            self.root.after_cancel(self._timer); self._timer = None
        if self.stop_countdown_button and self.stop_countdown_button.winfo_manager():
            self.stop_countdown_button.pack_forget()
        self._remove_countdown_taskbar_overlay()
        seconds = int(self.delay.get() or 0)
        if seconds:
            self._countdown_total_seconds = seconds
            self.status.set(f"Capture starts in {seconds} seconds… switch to the screen you want to capture")
            self.root.iconify()
            # Let Windows complete the minimize and associate the window with
            # its taskbar group before sending the first icon/progress update.
            self._timer = self.root.after(200, lambda: self._countdown(seconds))
        else:
            self.root.after(160, self._show_capture_overlay)

    def _countdown(self, seconds):
        if seconds <= 0:
            self._timer = None
            if self.stop_countdown_button and self.stop_countdown_button.winfo_manager():
                self.stop_countdown_button.pack_forget()
            self._remove_countdown_taskbar_overlay()
            self._show_capture_overlay()
            return
        self.status.set(f"Capture starts in {seconds}…")
        self.root.title(f"ChessSnip · {seconds}s")
        if self.stop_countdown_button and not self.stop_countdown_button.winfo_manager():
            if self.editor_separator and self.editor_separator.winfo_manager():
                self.stop_countdown_button.pack(side="left", padx=(10, 0), before=self.editor_separator)
            else:
                self.stop_countdown_button.pack(side="left", padx=(10, 0))
        self._update_countdown_taskbar(seconds)
        self._timer = self.root.after(1000, lambda: self._countdown(seconds - 1))

    def cancel_countdown(self):
        if self._timer:
            try:self.root.after_cancel(self._timer)
            except tk.TclError:pass
            self._timer = None
        self._remove_countdown_taskbar_overlay()
        if self.stop_countdown_button and self.stop_countdown_button.winfo_manager():
            self.stop_countdown_button.pack_forget()
        self.root.deiconify()
        self.root.state("normal")
        self.root.lift()
        self.root.focus_force()
        self.status.set("Delayed snip canceled")

    def _show_capture_overlay(self):
        # Withdraw the editor and wait for DWM to present the hidden frame before
        # capturing. Tk's withdraw request alone can leave one stale frame on screen.
        self._remove_countdown_taskbar_overlay()
        self.root.withdraw(); self.root.update_idletasks(); self.root.update()
        try:
            user32 = ctypes.windll.user32
            user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
            user32.ShowWindow(self.root.winfo_id(), 0)  # SW_HIDE
            ctypes.windll.dwmapi.DwmFlush()
        except Exception:
            pass
        time.sleep(0.15)
        try:
            from PIL import ImageGrab
            shot = ImageGrab.grab(all_screens=True).convert("RGB")
        except Exception as e:
            self.root.deiconify(); messagebox.showerror("Capture failed", str(e), parent=self.root); return
        overlay = tk.Toplevel(self.root); overlay.overrideredirect(True); overlay.attributes("-topmost", True)

        def emergency_cancel_overlay(_event=None):
            try:
                cv.grab_release()
            except (NameError, tk.TclError):
                pass
            try:
                release_capture_cursor()
            except Exception:
                self._restore_system_cursor()
            try:
                overlay.destroy()
            except tk.TclError:
                pass
            self.root.deiconify()
            self.root.state("normal")
            self.root.lift()
            self.root.focus_force()
            self.status.set("Capture cancelled")
            return "break"

        overlay.bind("<Escape>", emergency_cancel_overlay)
        overlay.bind("<Alt-F4>", emergency_cancel_overlay)
        x0, y0 = self._virtual_origin()
        overlay.geometry(f"{shot.width}x{shot.height}{x0:+d}{y0:+d}")
        # Keep the capture surface fully opaque so moving windows behind it
        # cannot show through. Darken the frozen screenshot smoothly instead
        # of using a stipple pattern, which can make text hard to read.
        try: overlay.attributes("-alpha", 1.0)
        except tk.TclError: pass
        cv = tk.Canvas(overlay, highlightthickness=0, cursor="crosshair", bg="#151620")
        cv.pack(fill="both", expand=True)
        cursor_api = ctypes.windll.user32
        cursor_api.GetCursor.restype = wintypes.HANDLE
        cursor_api.SetCursor.argtypes = (wintypes.HANDLE,)
        cursor_api.SetCursor.restype = wintypes.HANDLE
        cursor_api.DestroyCursor.argtypes = (wintypes.HANDLE,)
        cursor_api.DestroyCursor.restype = wintypes.BOOL
        try:
            previous_cursor = cursor_api.GetCursor()
            capture_cursor = self._create_capture_cursor()
        except Exception:
            previous_cursor = None
            capture_cursor = None
        cursor_released = {"value": False}

        def apply_capture_cursor(_event=None):
            if capture_cursor:
                try: cursor_api.SetCursor(capture_cursor)
                except Exception: pass

        def release_capture_cursor():
            if cursor_released["value"]:
                return
            cursor_released["value"] = True
            if capture_cursor:
                try:
                    if previous_cursor:
                        cursor_api.SetCursor(previous_cursor)
                    cursor_api.DestroyCursor(capture_cursor)
                except Exception:
                    pass
        # Keep the screenshot in color and dim it strongly without a tinted overlay.
        from PIL import ImageEnhance
        preview = ImageEnhance.Brightness(shot).enhance(0.50)
        preview.thumbnail((shot.width, shot.height))
        shot_tk = ImageTk.PhotoImage(preview)
        cv.create_image(0, 0, image=shot_tk, anchor="nw", tags="screen")
        mode = self.capture_mode.get()
        mode_labels = {
            "Rectangle": "Rectangle",
            "Window": "Window",
            "Full screen": "Fullscreen",
            "Freeform": "Freeform",
        }
        capture_mode_icons = self._create_capture_mode_icon_images("white")
        banner_texts = {
            "Rectangle": "Drag to select a region  ·  Release to capture  ·  Esc to cancel",
            "Window": "Hover over a window to highlight it  ·  Click to capture  ·  Esc to cancel",
            "Full screen": "Capturing the full screen…",
            "Freeform": "Click and draw around an area  ·  Release to capture  ·  Esc to cancel",
        }
        # Keep the instruction at the top of the primary monitor, even when
        # the frozen screenshot spans multiple displays.
        primary_width = ctypes.windll.user32.GetSystemMetrics(0)
        banner_x = primary_width / 2 - x0
        banner_y = 13 - y0
        banner = cv.create_text(
            banner_x, banner_y, text=banner_texts[mode], fill="white",
            font=("Segoe UI Semibold", 8), tags="banner",
        )
        overlay_mode_var = tk.StringVar(master=overlay, value=mode)
        capture_toolbar_state = {
            "widget": None, "visible": True,
            "mode_menu": None, "mode_menu_visible": False,
        }

        def hide_capture_mode_menu():
            menu = capture_toolbar_state["mode_menu"]
            if menu is not None and capture_toolbar_state["mode_menu_visible"]:
                menu.place_forget()
                capture_toolbar_state["mode_menu_visible"] = False

        def hide_capture_toolbar():
            hide_capture_mode_menu()
            toolbar = capture_toolbar_state["widget"]
            if toolbar is not None and capture_toolbar_state["visible"]:
                toolbar.place_forget()
                capture_toolbar_state["visible"] = False

        def update_capture_banner():
            cv.itemconfigure(banner, text=banner_texts[mode])

        box = {
            "rect": None, "start": None, "window_hwnd": None, "points": [],
            "dragging": False, "mouse_poll_after": None, "grabbed": False,
        }
        selection_preview = {"after": None, "photo": None, "item": None, "photo_size": None}

        def capture_button_is_down():
            try:
                user32 = ctypes.windll.user32
                user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
                user32.GetAsyncKeyState.restype = ctypes.c_short
                return bool(user32.GetAsyncKeyState(0x01) & 0x8000)
            except Exception:
                return False

        def release_capture_grab():
            if not box["grabbed"]:
                return
            try:
                if overlay.grab_current() == cv:
                    cv.grab_release()
            except tk.TclError:
                pass
            box["grabbed"] = False

        def stop_capture_drag():
            box["dragging"] = False
            pending = box["mouse_poll_after"]
            if pending is not None:
                try:
                    overlay.after_cancel(pending)
                except tk.TclError:
                    pass
                box["mouse_poll_after"] = None
            release_capture_grab()

        def poll_capture_button_release():
            box["mouse_poll_after"] = None
            if not box["dragging"]:
                return
            if capture_button_is_down():
                box["mouse_poll_after"] = overlay.after(20, poll_capture_button_release)
                return
            if mode == "Window":
                update_window_hover()
            stop_capture_drag()
            finish()

        def start_capture_drag():
            box["dragging"] = True
            try:
                cv.grab_set()
                box["grabbed"] = True
            except tk.TclError:
                pass
            box["mouse_poll_after"] = overlay.after(20, poll_capture_button_release)

        def refresh_selection_preview():
            selection_preview["after"] = None
            rect = box["rect"]
            if not rect or rect[2] <= rect[0] or rect[3] <= rect[1]:
                return
            a, b, c, d = rect
            crop = shot.crop(rect)
            if mode == "Freeform":
                from PIL import ImageDraw
                points = box["points"]
                if len(points) < 3:
                    return
                mask = Image.new("L", crop.size, 0)
                local_points = [(x - a, y - b) for x, y in points]
                ImageDraw.Draw(mask).polygon(local_points, fill=255)
                crop = crop.convert("RGBA")
                crop.putalpha(mask)
            photo = selection_preview["photo"]
            # Keep the Tk image dimensions stable across small pointer moves.
            # Transparent padding keeps the visible crop exactly on its bounds.
            width, height = c - a, d - b
            photo_size = (min(shot.width, ((width + 63) // 64) * 64), min(shot.height, ((height + 63) // 64) * 64))
            padded_crop = Image.new("RGBA", photo_size, (0, 0, 0, 0))
            padded_crop.paste(crop.convert("RGBA"), (0, 0))
            if photo is None or selection_preview["photo_size"] != photo_size:
                photo = ImageTk.PhotoImage(padded_crop)
                selection_preview["photo"] = photo
                selection_preview["photo_size"] = photo_size
                if selection_preview["item"] is None:
                    selection_preview["item"] = cv.create_image(a, b, image=photo, anchor="nw", tags="selection-image")
                else:
                    cv.itemconfigure(selection_preview["item"], image=photo)
                cv.selection_photo = photo
            else:
                photo.paste(padded_crop)
            cv.coords(selection_preview["item"], a, b)
            cv.tag_raise("selection")
            cv.tag_raise("banner")

        def schedule_selection_preview():
            if selection_preview["after"] is None:
                selection_preview["after"] = overlay.after(10, refresh_selection_preview)

        def stop_selection_preview():
            pending = selection_preview["after"]
            if pending is not None:
                try: overlay.after_cancel(pending)
                except tk.TclError: pass
                selection_preview["after"] = None

        def update_freeform_selection(event):
            x = max(0, min(shot.width - 1, event.x))
            y = max(0, min(shot.height - 1, event.y))
            points = box["points"]
            point = (x, y)
            if not points or math.hypot(x - points[-1][0], y - points[-1][1]) >= 2:
                points.append(point)
            if not points:
                return
            left = min(px for px, _py in points)
            top = min(py for _px, py in points)
            right = max(px for px, _py in points) + 1
            bottom = max(py for _px, py in points) + 1
            box["rect"] = (left, top, right, bottom)
            cv.delete("selection")
            line_points = list(points)
            if len(line_points) > 2:
                line_points.append(line_points[0])
            flat_points = [coordinate for pair in line_points for coordinate in pair]
            if len(flat_points) >= 4:
                cv.create_line(
                    *flat_points, fill="#ffe36b", width=2,
                    capstyle=tk.ROUND, joinstyle=tk.ROUND, smooth=True,
                    tags="selection",
                )
            if len(points) >= 3 and right - left >= 3 and bottom - top >= 3:
                schedule_selection_preview()

        def get_visible_window_bounds(hwnd):
            """Return visible DWM frame bounds, excluding resize margins/shadow."""
            bounds = wintypes.RECT()
            try:
                dwm = ctypes.windll.dwmapi
                dwm.DwmGetWindowAttribute.argtypes = (
                    wintypes.HWND, wintypes.DWORD,
                    ctypes.POINTER(wintypes.RECT), wintypes.DWORD,
                )
                dwm.DwmGetWindowAttribute.restype = ctypes.c_long
                # DWMWA_EXTENDED_FRAME_BOUNDS is the visible frame in screen
                # coordinates; GetWindowRect also includes invisible resize
                # borders on modern Windows.
                if dwm.DwmGetWindowAttribute(
                    hwnd, 9, ctypes.byref(bounds), ctypes.sizeof(bounds),
                ) >= 0:
                    return bounds.left, bounds.top, bounds.right, bounds.bottom
            except Exception:
                pass
            try:
                u = ctypes.windll.user32
                u.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
                u.GetWindowRect.restype = wintypes.BOOL
                if u.GetWindowRect(hwnd, ctypes.byref(bounds)):
                    return bounds.left, bounds.top, bounds.right, bounds.bottom
            except Exception:
                pass
            return None

        def find_window_at_pointer(event=None):
            """Find the topmost visible app window beneath the capture overlay."""
            try:
                u = ctypes.windll.user32
                u.GetCursorPos.argtypes = (ctypes.POINTER(wintypes.POINT),)
                u.GetCursorPos.restype = wintypes.BOOL
                u.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
                u.GetAncestor.restype = wintypes.HWND
                u.IsWindowVisible.argtypes = (wintypes.HWND,)
                u.IsWindowVisible.restype = wintypes.BOOL
                u.IsIconic.argtypes = (wintypes.HWND,)
                u.IsIconic.restype = wintypes.BOOL
                u.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
                u.GetWindowThreadProcessId.restype = wintypes.DWORD
                u.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
                u.GetClassNameW.restype = ctypes.c_int
                u.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
                u.GetWindowRect.restype = wintypes.BOOL
                point = wintypes.POINT()
                if not u.GetCursorPos(ctypes.byref(point)):
                    return None
                px, py = point.x, point.y
                overlay_hwnd = u.GetAncestor(overlay.winfo_id(), 2) or overlay.winfo_id()
                own_pid = os.getpid()
                found = {"value": None}
                excluded_classes = {
                    "Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd",
                }

                def inspect(hwnd, _lparam):
                    if hwnd == overlay_hwnd or not u.IsWindowVisible(hwnd) or u.IsIconic(hwnd):
                        return True
                    process_id = wintypes.DWORD()
                    u.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
                    if process_id.value == own_pid:
                        return True
                    class_name = ctypes.create_unicode_buffer(256)
                    u.GetClassNameW(hwnd, class_name, len(class_name))
                    if class_name.value in excluded_classes:
                        return True
                    coarse_bounds = wintypes.RECT()
                    if not u.GetWindowRect(hwnd, ctypes.byref(coarse_bounds)):
                        return True
                    if not (
                        coarse_bounds.left <= px < coarse_bounds.right
                        and coarse_bounds.top <= py < coarse_bounds.bottom
                    ):
                        return True
                    visible_bounds = get_visible_window_bounds(hwnd)
                    if visible_bounds and (
                        visible_bounds[0] <= px < visible_bounds[2]
                        and visible_bounds[1] <= py < visible_bounds[3]
                    ):
                        found["value"] = (hwnd, *visible_bounds)
                        return False
                    return True

                callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
                callback = callback_type(inspect)
                u.EnumWindows.argtypes = (callback_type, wintypes.LPARAM)
                u.EnumWindows.restype = wintypes.BOOL
                u.EnumWindows(callback, 0)
                return found["value"]
            except Exception:
                return None

        def clear_hovered_window():
            if box["window_hwnd"] is None and box["rect"] is None:
                return
            box["window_hwnd"] = None
            box["rect"] = None
            cv.delete("selection")
            cv.delete("selection-image")
            selection_preview["photo"] = None
            selection_preview["item"] = None
            selection_preview["photo_size"] = None

        def update_window_hover(_event=None):
            if mode != "Window":
                return
            target = find_window_at_pointer()
            if target is None:
                clear_hovered_window()
                return
            hwnd, left, top, right, bottom = target
            rect = (
                max(0, min(shot.width, left - x0)),
                max(0, min(shot.height, top - y0)),
                max(0, min(shot.width, right - x0)),
                max(0, min(shot.height, bottom - y0)),
            )
            if rect[2] - rect[0] < 3 or rect[3] - rect[1] < 3:
                clear_hovered_window()
                return
            if box["window_hwnd"] == hwnd and box["rect"] == rect:
                return
            box["window_hwnd"] = hwnd
            box["rect"] = rect
            box["start"] = None
            cv.delete("selection")
            cv.delete("selection-image")
            selection_preview["photo"] = None
            selection_preview["item"] = None
            selection_preview["photo_size"] = None
            a, b, c, d = rect
            cv.create_rectangle(a, b, c, d, outline="#ffe36b", width=3, tags="selection")
            cv.create_text((a + c) / 2, max(56, b - 14), text=f"{c-a} × {d-b}", fill="white", font=("Segoe UI", 10, "bold"), tags="selection")
            schedule_selection_preview()

        if mode == "Full screen":
            box["start"] = (0, 0); box["rect"] = (0, 0, shot.width, shot.height)
        
        def down(e):
            apply_capture_cursor()
            hide_capture_toolbar()
            if mode == "Full screen":
                box["start"] = (0, 0)
                box["rect"] = (0, 0, shot.width, shot.height)
                finish()
                return "break"
            if mode == "Window":
                start_capture_drag()
                update_window_hover(e)
                return "break"
            start_capture_drag()
            if mode == "Freeform":
                box["start"] = None
                box["rect"] = None
                box["points"] = []
                cv.delete("selection")
                cv.delete("selection-image")
                selection_preview["photo"] = None
                selection_preview["item"] = None
                selection_preview["photo_size"] = None
                update_freeform_selection(e)
                return "break"
            box["start"] = (max(0, min(shot.width, e.x)), max(0, min(shot.height, e.y)))
            box["rect"] = None; cv.delete("selection")
            cv.delete("selection-image")
            selection_preview["photo"] = None
            selection_preview["item"] = None
            selection_preview["photo_size"] = None
            return "break"

        def drag(e):
            apply_capture_cursor()
            if mode == "Window":
                update_window_hover(e)
                return "break"
            if mode == "Freeform":
                update_freeform_selection(e)
                return "break"
            if box["start"] is None: return "break"
            x1, y1 = box["start"]; x2, y2 = max(0,min(shot.width,e.x)), max(0,min(shot.height,e.y))
            box["rect"] = (min(x1,x2), min(y1,y2), max(x1,x2), max(y1,y2))
            cv.delete("selection")
            a,b,c,d = box["rect"]
            cv.create_rectangle(a,b,c,d,outline="#aaa2ff",width=2,tags="selection")
            if c-a > 0 and d-b > 0:
                schedule_selection_preview()
            cv.create_text((a+c)/2, max(56,b-14), text=f"{c-a} × {d-b}", fill="white", font=("Segoe UI",10,"bold"), tags="selection")
            return "break"
        def finish(_=None):
            if box["dragging"] and capture_button_is_down():
                return "break"
            stop_capture_drag()
            stop_selection_preview()
            release_capture_cursor()
            rect = box["rect"]
            if mode == "Window" and rect:
                # Hide the selector briefly so the target's final bounds can be
                # queried without capturing any part of the overlay.
                selected_hwnd = box["window_hwnd"]
                overlay.withdraw()
                def finish_window():
                    try:
                        u = ctypes.windll.user32
                        u.IsWindow.argtypes = [wintypes.HWND]
                        u.IsWindow.restype = wintypes.BOOL
                        bounds = (
                            get_visible_window_bounds(selected_hwnd)
                            if selected_hwnd and u.IsWindow(selected_hwnd)
                            else None
                        )
                        if bounds:
                            r = (
                                max(0, bounds[0] - x0), max(0, bounds[1] - y0),
                                min(shot.width, bounds[2] - x0),
                                min(shot.height, bounds[3] - y0),
                            )
                        else:
                            r = rect
                        if r[2]-r[0]<3 or r[3]-r[1]<3:r=rect
                        self.image=shot.crop(r).convert("RGBA");self.strokes.clear();self.canvas_only_strokes.clear();self.history.clear();self.redo_history.clear();self._update_history_buttons();self.zoom_factor=1.0;self._rotation_angle=0;self._rotation_base_image=self.image.copy();self._rotation_base_angle=0;self.background_layer=Image.new("RGBA",self.image.size,(0,0,0,0));self.annotation_layer=Image.new("RGBA",self.image.size,(0,0,0,0));self._rotation_base_marks=self.annotation_layer.copy();self._set_rotation_value(0)
                        self._clear_recognition_regions()
                        self.canvas.xview_moveto(0);self.canvas.yview_moveto(0)
                        self._set_editor_visible(True)
                        self.root.deiconify();self.root.state("normal");self.root.lift();self.root.focus_force()
                        self.status.set(f"Captured window  ·  {self.image.width} × {self.image.height} px");self._render_preview();self._copy_capture_to_clipboard()
                    except Exception as exc:
                        self.root.deiconify();self.root.state("normal");messagebox.showerror("Capture failed",str(exc),parent=self.root)
                    overlay.destroy()
                self.root.after(180,finish_window)
                return
            if mode == "Freeform" and rect and len(box["points"]) >= 3 and rect[2]-rect[0] >= 3 and rect[3]-rect[1] >= 3:
                from PIL import ImageDraw
                self.image = shot.crop(rect).convert("RGBA")
                self._clear_recognition_regions()
                mask = Image.new("L", self.image.size, 0)
                local_points = [(x - rect[0], y - rect[1]) for x, y in box["points"]]
                ImageDraw.Draw(mask).polygon(local_points, fill=255)
                self.image.putalpha(mask)
                self.strokes.clear(); self.canvas_only_strokes.clear(); self.history.clear(); self.redo_history.clear(); self._update_history_buttons()
                self.zoom_factor=1.0; self._rotation_angle=0; self._rotation_base_image=self.image.copy(); self._rotation_base_angle=0
                self.background_layer=Image.new("RGBA",self.image.size,(0,0,0,0)); self.annotation_layer=Image.new("RGBA",self.image.size,(0,0,0,0)); self._rotation_base_marks=self.annotation_layer.copy(); self._set_rotation_value(0)
                self.canvas.xview_moveto(0); self.canvas.yview_moveto(0)
                self._set_editor_visible(True)
                self.root.deiconify(); self.root.state("normal"); self.root.lift(); self.root.focus_force()
                self.status.set(f"Captured freeform  ·  {self.image.width} × {self.image.height} px")
                self._render_preview(); self._copy_capture_to_clipboard()
            elif mode != "Freeform" and rect and rect[2]-rect[0] >= 3 and rect[3]-rect[1] >= 3:
                self._clear_recognition_regions()
                self.image = shot.crop(rect).convert("RGBA"); self.strokes.clear(); self.canvas_only_strokes.clear(); self.history.clear(); self.redo_history.clear(); self._update_history_buttons(); self.zoom_factor=1.0; self._rotation_angle=0; self._rotation_base_image=self.image.copy(); self._rotation_base_angle=0; self.background_layer=Image.new("RGBA",self.image.size,(0,0,0,0));self.annotation_layer=Image.new("RGBA",self.image.size,(0,0,0,0));self._rotation_base_marks=self.annotation_layer.copy();self._set_rotation_value(0)
                self.canvas.xview_moveto(0); self.canvas.yview_moveto(0)
                self._set_editor_visible(True)
                self.root.deiconify(); self.root.state("normal"); self.root.lift(); self.root.focus_force()
                self.status.set(f"Captured  {self.image.width} × {self.image.height} px")
                self._render_preview()
                self._copy_capture_to_clipboard()
            else:
                self.root.deiconify(); self.root.state("normal"); self.status.set("Capture cancelled · press New snip when ready")
            overlay.destroy()
        def cancel(_=None):
            stop_capture_drag()
            stop_selection_preview()
            release_capture_cursor()
            self.root.deiconify(); self.root.state("normal"); self.status.set("Capture cancelled"); overlay.destroy()
            return "break"
        def release(e):
            apply_capture_cursor()
            if mode == "Window":
                update_window_hover(e)
            else:
                drag(e)
            if box["dragging"] and capture_button_is_down():
                return "break"
            stop_capture_drag()
            finish()
            return "break"

        # Bind capture and emergency-cancel input before optional toolbar setup.
        # If a themed control fails to initialize, the dimmed overlay remains usable.
        cv.bind("<ButtonPress-1>", down); cv.bind("<B1-Motion>", drag); cv.bind("<ButtonRelease-1>", release)
        cv.bind("<Enter>", apply_capture_cursor, add="+")
        cv.bind("<Motion>", apply_capture_cursor, add="+")
        cv.bind("<Motion>", update_window_hover, add="+")
        overlay.bind("<Return>", finish); overlay.bind("<Escape>", cancel)
        cv.bind("<Return>", finish); cv.bind("<Escape>", cancel)
        overlay.bind("<Alt-F4>", cancel); cv.bind("<Alt-F4>", cancel)
        overlay.lift(); overlay.focus_force(); cv.focus_set(); cv.image_ref = shot_tk
        overlay.after_idle(apply_capture_cursor)
        if mode == "Window":
            overlay.after_idle(update_window_hover)

        def choose_overlay_capture_mode(selected_mode):
            nonlocal mode
            hide_capture_mode_menu()
            mode = selected_mode
            self.capture_mode.set(selected_mode)
            self._update_mode_label()
            overlay_mode_var.set(selected_mode)
            box["start"] = (0, 0) if selected_mode == "Full screen" else None
            box["rect"] = (0, 0, shot.width, shot.height) if selected_mode == "Full screen" else None
            box["window_hwnd"] = None
            box["points"].clear()
            stop_selection_preview()
            cv.delete("selection")
            cv.delete("selection-image")
            selection_preview["photo"] = None
            selection_preview["item"] = None
            selection_preview["photo_size"] = None
            capture_mode_button.configure(
                image=capture_mode_icons[selected_mode],
                text=f"{mode_labels[selected_mode]}  ▾", compound=tk.LEFT,
            )
            for option, button in capture_mode_choice_buttons.items():
                button.configure(bg="#625cf2" if option == selected_mode else "#2b2d38")
            update_capture_banner()
            if selected_mode == "Full screen":
                overlay.after_idle(finish)

        capture_toolbar = tk.Frame(
            overlay, bg="#252630", bd=0, padx=5, pady=5,
            highlightthickness=1, highlightbackground="#555766",
        )
        capture_toolbar_state["widget"] = capture_toolbar
        capture_mode_menu = tk.Frame(
            overlay, bg="#252630", bd=0,
            highlightthickness=1, highlightbackground="#555766",
        )
        capture_toolbar_state["mode_menu"] = capture_mode_menu
        capture_mode_choice_buttons = {}
        for capture_mode in ("Rectangle", "Freeform", "Window", "Full screen"):
            selected = capture_mode == mode
            choice = tk.Button(
                capture_mode_menu, text=mode_labels[capture_mode],
                image=capture_mode_icons[capture_mode], compound=tk.LEFT, anchor="w",
                command=lambda selected_mode=capture_mode: choose_overlay_capture_mode(selected_mode),
                bg="#625cf2" if selected else "#2b2d38", fg="white",
                activebackground="#454754", activeforeground="white",
                relief="flat", bd=0, padx=10, pady=4,
                font=("Segoe UI", 10), cursor="hand2", takefocus=0,
            )
            choice.pack(fill="x")
            capture_mode_choice_buttons[capture_mode] = choice
            choice.bind("<Enter>", lambda _event, button=choice: button.configure(bg="#454754"))
            choice.bind(
                "<Leave>",
                lambda _event, button=choice, value=capture_mode: button.configure(
                    bg="#625cf2" if overlay_mode_var.get() == value else "#2b2d38",
                ),
            )

        def toggle_capture_mode_menu():
            if capture_toolbar_state["mode_menu_visible"]:
                hide_capture_mode_menu()
                return
            overlay.update_idletasks()
            menu_x = capture_toolbar.winfo_rootx() - overlay.winfo_rootx()
            menu_y = (
                capture_toolbar.winfo_rooty() - overlay.winfo_rooty()
                + capture_toolbar.winfo_height() + 4
            )
            menu_width = max(220, capture_mode_button.winfo_width(), capture_mode_menu.winfo_reqwidth())
            capture_mode_menu.place(x=menu_x, y=menu_y, width=menu_width)
            capture_mode_menu.lift()
            capture_toolbar_state["mode_menu_visible"] = True

        capture_mode_button = tk.Button(
            capture_toolbar, text=f"{mode_labels[mode]}  ▾",
            image=capture_mode_icons[mode], compound=tk.LEFT,
            command=toggle_capture_mode_menu,
            bg="#343642", fg="white", activebackground="#454754",
            activeforeground="white", relief="flat", bd=0, padx=12, pady=6,
            font=("Segoe UI Semibold", 10), cursor="hand2",
        )
        capture_mode_button.pack(side="left", padx=(0, 5))
        cancel_button = tk.Button(
            capture_toolbar, text="✕", command=cancel,
            bg="#c83f4b", fg="white", activebackground="#e2525e",
            activeforeground="white", relief="flat", bd=0,
            padx=10, pady=4, font=("Segoe UI Semibold", 11), cursor="hand2",
        )
        cancel_button.pack(side="left")
        # GetSystemMetrics(0) describes the primary monitor, whose desktop
        # origin is (0, 0). Convert that position into the virtual screenshot.
        toolbar_relx = (primary_width / 2 - x0) / shot.width
        toolbar_rely = -y0 / shot.height
        capture_toolbar.place(relx=toolbar_relx, rely=toolbar_rely, y=27, anchor="n")
        capture_toolbar.lift()
        if mode == "Full screen":
            overlay.after_idle(finish)

    @staticmethod
    def _virtual_origin():
        u = ctypes.windll.user32
        return u.GetSystemMetrics(76), u.GetSystemMetrics(77)

    def _resize_preview(self, event=None):
        if self._rotation_drag:
            self._cancel_rotation()
        if not self.image: return
        size=(event.width,event.height) if event else (self.canvas.winfo_width(),self.canvas.winfo_height())
        if size==self._last_canvas_size:return
        self._last_canvas_size=size
        if self._preview_redraw_after:
            try:self.root.after_cancel(self._preview_redraw_after)
            except tk.TclError:pass
        self._preview_redraw_after=self.root.after_idle(self._render_preview)

    def _on_brush_width_changed(self):
        self._update_brush_cursor()
        self._show_size_preview()

    def _show_size_preview(self):
        if not self.image or not hasattr(self, "canvas") or self.pointer_inside_canvas:
            self._hide_size_preview()
            return
        if self._size_preview_after:
            try:self.root.after_cancel(self._size_preview_after)
            except tk.TclError:pass
        self.canvas.delete("size-preview")
        w,h=self.canvas.winfo_width(),self.canvas.winfo_height()
        cx,cy=self.canvas.canvasx(w/2),self.canvas.canvasy(h/2)
        tool = self.tool.get()
        side=max(6.0,self._stroke_render_width(tool,self.width.get())*self.fit_scale)
        half=side if tool == "highlighter" else side/2
        if tool == "highlighter":
            outer = self.canvas.create_line(
                cx, cy-half, cx, cy+half, fill="#111318",
                width=side+4, capstyle=tk.BUTT, tags="size-preview",
            )
            inner = self.canvas.create_line(
                cx, cy-half, cx, cy+half, fill=self.color.get(),
                width=side, capstyle=tk.BUTT, tags="size-preview",
            )
        else:
            square=tool in ("rectangle","rectangle_filled","crop")
            create_shape=self.canvas.create_rectangle if square else self.canvas.create_oval
            outer=create_shape(cx-half-2,cy-half-2,cx+half+2,cy+half+2,outline="#111318",width=2,tags="size-preview")
            inner=create_shape(
                cx-half,cy-half,cx+half,cy+half,
                outline="#ffffff",fill=self.color.get(),stipple="gray50",width=1,
                tags="size-preview",
            )
        self._size_preview_items=(outer,inner)
        self.canvas.tag_raise("size-preview")
        self._size_preview_after=self.root.after(900,self._hide_size_preview)

    def _hide_size_preview(self):
        if self._size_preview_after:
            try:self.root.after_cancel(self._size_preview_after)
            except tk.TclError:pass
            self._size_preview_after=None
        if hasattr(self,"canvas"):
            try:self.canvas.delete("size-preview")
            except tk.TclError:pass
        self._size_preview_items=None

    @staticmethod
    def _stroke_render_width(tool, width):
        """Use the same size value for pen and highlighter; opacity defines highlighting."""
        return max(1.0, float(width))

    @staticmethod
    def _stroke_width_for(stroke):
        """Use a pre-scaled width for marks stored in canvas coordinates."""
        if "render_width" in stroke:
            return max(1.0, float(stroke["render_width"]))
        return ChessSnip._stroke_render_width(
            stroke.get("tool", "pen"), stroke.get("width", 1),
        )

    @staticmethod
    def _is_vector_shape(tool):
        return tool in (
            "rectangle", "rectangle_filled", "circle", "circle_filled",
            "line", "arrow", "cross", "tick",
        )

    @staticmethod
    def _shape_local_bounds(stroke):
        points = stroke.get("points", ())
        if len(points) < 4:
            return (0.0, 0.0, 0.0, 0.0)
        x1, y1, x2, y2 = map(float, points[:4])
        return min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)

    @staticmethod
    def _rotate_shape_point(point, center, degrees):
        angle = math.radians(degrees)
        dx, dy = point[0] - center[0], point[1] - center[1]
        return (
            center[0] + dx * math.cos(angle) - dy * math.sin(angle),
            center[1] + dx * math.sin(angle) + dy * math.cos(angle),
        )

    @staticmethod
    def _shape_point_to_image(stroke, point):
        left, top, right, bottom = ChessSnip._shape_local_bounds(stroke)
        center = ((left + right) / 2, (top + bottom) / 2)
        offset = stroke.get("offset", (0.0, 0.0))
        rotated = ChessSnip._rotate_shape_point(
            point, center, float(stroke.get("rotation", 0.0)),
        )
        return rotated[0] + offset[0], rotated[1] + offset[1]

    @staticmethod
    def _shape_point_to_local(stroke, point):
        left, top, right, bottom = ChessSnip._shape_local_bounds(stroke)
        center = ((left + right) / 2, (top + bottom) / 2)
        offset = stroke.get("offset", (0.0, 0.0))
        unshifted = (point[0] - offset[0], point[1] - offset[1])
        return ChessSnip._rotate_shape_point(
            unshifted, center, -float(stroke.get("rotation", 0.0)),
        )

    @staticmethod
    def _stroke_preview_patch(stroke, scale=1.0, origin=(0.0, 0.0), clip_bounds=None):
        """Render one antialiased stroke patch in canvas or image coordinates."""
        from PIL import ImageDraw

        raw = stroke.get("points", ())
        points = [
            (float(raw[i]), float(raw[i + 1]))
            for i in range(0, len(raw) - 1, 2)
        ]
        if not points:
            return None, 0, 0

        tool = stroke.get("tool", "pen")
        width = max(1.0, ChessSnip._stroke_width_for(stroke) * scale)
        is_shape = ChessSnip._is_vector_shape(tool)
        if tool == "highlighter" and len(points) == 1:
            px, py = points[0]
            half_length = max(8.0, width)
            points = [(px, py - half_length), (px, py + half_length)]

        shape_path = []
        shape_segments = None
        if is_shape:
            if len(points) < 2:
                return None, 0, 0
            points = points[:2]
            left, top, right, bottom = ChessSnip._shape_local_bounds(stroke)
            center = ((left + right) / 2, (top + bottom) / 2)
            rotation = float(stroke.get("rotation", 0.0))
            offset = stroke.get("offset", (0.0, 0.0))

            def transform(point):
                rotated = ChessSnip._rotate_shape_point(point, center, rotation)
                return (
                    (rotated[0] + offset[0]) * scale + origin[0],
                    (rotated[1] + offset[1]) * scale + origin[1],
                )

            if tool.startswith("rectangle"):
                local_path = [(left, top), (right, top), (right, bottom), (left, bottom)]
                shape_path = [transform(point) for point in local_path]
            elif tool.startswith("circle"):
                rx, ry = (right - left) / 2, (bottom - top) / 2
                shape_path = [
                    transform((center[0] + rx * math.cos(i * math.tau / 64),
                              center[1] + ry * math.sin(i * math.tau / 64)))
                    for i in range(64)
                ]
            elif tool == "cross":
                local_segments = (
                    ((left, top), (right, bottom)),
                    ((right, top), (left, bottom)),
                )
                shape_segments = [
                    [transform(point) for point in segment]
                    for segment in local_segments
                ]
                shape_path = [point for segment in shape_segments for point in segment]
            elif tool == "tick":
                local_segments = ((
                    (left, top + (bottom - top) * 0.55),
                    (left + (right - left) * 0.36, bottom),
                    (right, top),
                ),)
                shape_segments = [
                    [transform(point) for point in segment]
                    for segment in local_segments
                ]
                shape_path = shape_segments[0]
            else:
                shape_path = [transform(point) for point in points]
            points = shape_path

        x_values = [point[0] for point in points]
        y_values = [point[1] for point in points]
        padding = width / 2 + 1.0
        if tool == "arrow":
            padding += max(14.0 * scale, width * 4.5)
        left = math.floor(min(x_values) - padding)
        top = math.floor(min(y_values) - padding)
        right = math.ceil(max(x_values) + padding) + 1
        bottom = math.ceil(max(y_values) + padding) + 1
        if clip_bounds is not None:
            left = max(left, math.floor(clip_bounds[0]))
            top = max(top, math.floor(clip_bounds[1]))
            right = min(right, math.ceil(clip_bounds[2]))
            bottom = min(bottom, math.ceil(clip_bounds[3]))
        patch_width, patch_height = right - left, bottom - top
        if patch_width <= 0 or patch_height <= 0:
            return None, 0, 0

        supersample = max(1, min(2, int(math.sqrt(3_000_000 / (patch_width * patch_height)))))
        coverage = Image.new("L", (patch_width * supersample, patch_height * supersample), 0)
        draw = ImageDraw.Draw(coverage)
        color = tuple(bytes.fromhex(stroke.get("color", "#000000").lstrip("#")))
        alpha = 128 if tool == "highlighter" else 255
        scaled_width = max(1, round(width * supersample))

        def local(point):
            return ((point[0] - left) * supersample, (point[1] - top) * supersample)

        if is_shape:
            path = [local(point) for point in points]
            filled = tool.endswith("_filled")
            if tool.startswith(("rectangle", "circle")):
                closed_path = path + [path[0]]
                if filled:
                    draw.polygon(path, fill=255)
                draw.line(closed_path, fill=255, width=scaled_width, joint="curve")
                # PIL's closed polyline can leave a hairline where its butt caps meet.
                # Seal that start/end seam explicitly for rectangle and circle outlines.
                half = scaled_width / 2
                px, py = path[0]
                draw.ellipse((px - half, py - half, px + half, py + half), fill=255)
            elif tool == "line":
                draw.line(path, fill=255, width=scaled_width)
                half = scaled_width / 2
                for px, py in (path[0], path[-1]):
                    draw.ellipse((px - half, py - half, px + half, py + half), fill=255)
            elif tool in ("cross", "tick"):
                half = scaled_width / 2
                for segment in shape_segments or (path,):
                    local_segment = [local(point) for point in segment]
                    draw.line(
                        local_segment, fill=255, width=scaled_width, joint="curve",
                    )
                    for px, py in (local_segment[0], local_segment[-1]):
                        draw.ellipse(
                            (px - half, py - half, px + half, py + half), fill=255,
                        )
            else:
                start, end = path
                dx, dy = end[0] - start[0], end[1] - start[1]
                length = max(1.0, math.hypot(dx, dy))
                ux, uy = dx / length, dy / length
                head_length = min(length * 0.62, max(14.0 * scale * supersample, scaled_width * 4.5))
                half_head = max(8.0 * scale * supersample, scaled_width * 2.6)
                base = (end[0] - ux * head_length, end[1] - uy * head_length)
                wing_a = (base[0] - uy * half_head, base[1] + ux * half_head)
                wing_b = (base[0] + uy * half_head, base[1] - ux * half_head)
                draw.line((start, base), fill=255, width=scaled_width)
                draw.polygon((end, wing_a, wing_b), fill=255)
        else:
            local_points = [local(point) for point in points]
            draw.line(local_points, fill=255, width=scaled_width, joint="curve")
            if tool in ("pen", "highlighter"):
                half = scaled_width / 2
                for px, py in (local_points[0], local_points[-1]):
                    draw.ellipse((px - half, py - half, px + half, py + half), fill=255)

        if supersample > 1:
            coverage = coverage.resize((patch_width, patch_height), Image.Resampling.BOX)
        patch = Image.new("RGBA", (patch_width, patch_height), (*color, 0))
        patch.putalpha(coverage.point(lambda value: (value * alpha + 127) // 255))
        return patch, left, top

    def _draw_stroke_patch(self, stroke, tags, *, scale=None, origin=None, clip_to_image=False):
        if scale is None:
            scale = self.fit_scale
        if origin is None:
            origin = self.display_origin
        left_view, top_view = self.canvas.canvasx(0), self.canvas.canvasy(0)
        right_view = self.canvas.canvasx(self.canvas.winfo_width())
        bottom_view = self.canvas.canvasy(self.canvas.winfo_height())
        clip = (left_view, top_view, right_view, bottom_view)
        if clip_to_image:
            image_left, image_top = self.display_origin
            image_right = image_left + self.image_display_size[0]
            image_bottom = image_top + self.image_display_size[1]
            clip = (
                max(clip[0], image_left), max(clip[1], image_top),
                min(clip[2], image_right), min(clip[3], image_bottom),
            )
        patch, left, top = self._stroke_preview_patch(stroke, scale, origin, clip)
        if patch is None:
            return None
        if tags == "mark" and self._is_vector_shape(stroke.get("tool")):
            shape_key = stroke.setdefault("shape_key", id(stroke))
            tags = ("mark", f"shape-{shape_key}")
        photo = ImageTk.PhotoImage(patch, master=self.canvas)
        item = self.canvas.create_image(left, top, image=photo, anchor="nw", tags=tags)
        if tags == "active-mark":
            self._drawing_preview_photo = photo
            self._drawing_preview_item = item
        elif isinstance(tags, tuple):
            shape_tag = next((tag for tag in tags if str(tag).startswith("shape-")), None)
            if shape_tag is not None:
                self._shape_preview_photos_by_key[shape_tag] = photo
            else:
                self._shape_preview_photos.append(photo)
        else:
            self._shape_preview_photos.append(photo)
        return item

    def _shape_selection_geometry(self, stroke):
        tool = stroke.get("tool", "")
        if tool in ("line", "arrow"):
            local_handles = (
                (float(stroke["points"][0]), float(stroke["points"][1])),
                (float(stroke["points"][2]), float(stroke["points"][3])),
            )
            handles = []
            for point in local_handles:
                image_point = self._shape_point_to_image(stroke, point)
                handles.append((
                    self.display_origin[0] + image_point[0] * self.fit_scale,
                    self.display_origin[1] + image_point[1] * self.fit_scale,
                ))
            start, end = handles
            dx, dy = end[0] - start[0], end[1] - start[1]
            length = max(1.0, math.hypot(dx, dy))
            normal = (-dy / length, dx / length)
            if normal[1] > 0:
                normal = (-normal[0], -normal[1])
            midpoint = ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)
            rotate_handle = (midpoint[0] + normal[0] * 27,
                             midpoint[1] + normal[1] * 27)
            return local_handles, handles, rotate_handle

        left, top, right, bottom = self._shape_local_bounds(stroke)
        local_corners = ((left, top), (right, top), (right, bottom), (left, bottom))
        corners = []
        for point in local_corners:
            image_point = self._shape_point_to_image(stroke, point)
            corners.append((
                self.display_origin[0] + image_point[0] * self.fit_scale,
                self.display_origin[1] + image_point[1] * self.fit_scale,
            ))
        angle = math.radians(float(stroke.get("rotation", 0.0)))
        top_mid = ((corners[0][0] + corners[1][0]) / 2,
                   (corners[0][1] + corners[1][1]) / 2)
        rotate_handle = (
            top_mid[0] + math.sin(angle) * 27,
            top_mid[1] - math.cos(angle) * 27,
        )
        return local_corners, corners, rotate_handle

    def _render_shape_selection(self):
        self.canvas.delete("shape-selection")
        stroke = self._selected_shape
        if not stroke or stroke not in self.strokes or not self._is_vector_shape(stroke.get("tool")):
            return
        _local_handles, handles, rotate_handle = self._shape_selection_geometry(stroke)
        endpoint_shape = stroke.get("tool") in ("line", "arrow")
        if not endpoint_shape:
            path = [coordinate for point in (*handles, handles[0]) for coordinate in point]
            self.canvas.create_line(
                *path, fill=ACCENT, width=1.5, dash=(4, 3), tags="shape-selection",
            )
        for x, y in handles:
            self.canvas.create_rectangle(
                x - 5, y - 5, x + 5, y + 5,
                fill="white", outline=ACCENT, width=1.5, tags="shape-selection",
            )
        if endpoint_shape:
            start, end = handles
            top_mid = ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)
        else:
            top_mid = ((handles[0][0] + handles[1][0]) / 2,
                       (handles[0][1] + handles[1][1]) / 2)
        self.canvas.create_line(
            *top_mid, *rotate_handle, fill=ACCENT, width=1.5, tags="shape-selection",
        )
        self.canvas.create_oval(
            rotate_handle[0] - 10, rotate_handle[1] - 10,
            rotate_handle[0] + 10, rotate_handle[1] + 10,
            fill="white", outline=ACCENT, width=1.5, tags="shape-selection",
        )
        self.canvas.create_text(
            *rotate_handle, text="↻", fill=ACCENT, font=("Segoe UI Symbol", 12, "bold"),
            tags="shape-selection",
        )
        self.canvas.tag_raise("shape-selection")

    def _shape_selection_hit(self, event):
        stroke = self._selected_shape
        if not stroke or stroke not in self.strokes:
            return None
        _local_handles, handles, rotate_handle = self._shape_selection_geometry(stroke)
        x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        if math.hypot(x - rotate_handle[0], y - rotate_handle[1]) <= 13:
            return ("rotate", None)
        for index, (hx, hy) in enumerate(handles):
            if math.hypot(x - hx, y - hy) <= 10:
                return ("resize", index)
        image_x, image_y = self._to_image(event.x, event.y)
        if self._shape_move_hit(stroke, image_x, image_y):
            return ("move", None)
        return None

    def _shape_move_hit(self, stroke, x, y):
        local_x, local_y = self._shape_point_to_local(stroke, (x, y))
        left, top, right, bottom = self._shape_local_bounds(stroke)
        tool = stroke.get("tool", "")
        if tool.startswith("rectangle"):
            return left <= local_x <= right and top <= local_y <= bottom
        if tool.startswith("circle"):
            cx, cy = (left + right) / 2, (top + bottom) / 2
            rx, ry = max((right - left) / 2, 0.5), max((bottom - top) / 2, 0.5)
            return ((local_x - cx) / rx) ** 2 + ((local_y - cy) / ry) ** 2 <= 1
        if tool in ("line", "arrow"):
            return self._stroke_hit_at(stroke, x, y, max(5.0, self.width.get() * 1.5))
        return self._stroke_hit_at(stroke, x, y, max(4.0, self.width.get()))

    def _set_shape_selection_cursor(self, event):
        if self.tool.get() not in (
            "rectangle", "rectangle_filled", "circle", "circle_filled",
            "line", "arrow", "cross", "tick",
        ):
            return
        hit = self._shape_selection_hit(event)
        cursor = "crosshair"
        if hit:
            if hit[0] == "rotate" or hit[0] == "move":
                cursor = "fleur"
            elif self._selected_shape and self._selected_shape.get("tool") in ("line", "arrow"):
                cursor = "crosshair"
            else:
                cursor = "size_nw_se" if hit[1] in (0, 2) else "size_ne_sw"
        try:
            self.canvas.configure(cursor=cursor)
        except tk.TclError:
            pass

    def _begin_shape_transform(self, mode, event, handle=None):
        stroke = self._selected_shape
        if not stroke:
            return
        x, y = self._to_image(event.x, event.y)
        left, top, right, bottom = self._shape_local_bounds(stroke)
        center = ((left + right) / 2, (top + bottom) / 2)
        rotation = float(stroke.get("rotation", 0.0))
        offset = tuple(stroke.get("offset", (0.0, 0.0)))
        local_corners = ((left, top), (right, top), (right, bottom), (left, bottom))
        fixed_local = None
        if mode == "resize":
            if stroke.get("tool") in ("line", "arrow"):
                fixed_local = tuple(stroke["points"][(1 - handle) * 2:(1 - handle) * 2 + 2])
            else:
                fixed_local = local_corners[(handle + 2) % 4]
        self._shape_transform_drag = {
            "mode": mode, "stroke": stroke, "start": (x, y),
            "points": list(stroke["points"]), "offset": offset,
            "rotation": rotation, "center": center, "handle": handle,
            "fixed_local": fixed_local, "history_pushed": False,
        }
        if mode == "rotate":
            world_center = (center[0] + offset[0], center[1] + offset[1])
            self._shape_transform_drag["start_angle"] = math.atan2(
                y - world_center[1], x - world_center[0],
            )
        # Rebuild the base without this shape; the preview renderer draws the
        # selected object above it as the live transform overlay.
        self._render_preview()

    def _update_shape_transform(self, event):
        drag = self._shape_transform_drag
        if not drag:
            return
        stroke = drag["stroke"]
        x, y = self._to_image(event.x, event.y)
        if math.hypot(x - drag["start"][0], y - drag["start"][1]) < 0.25:
            return
        if not drag["history_pushed"]:
            self._push_history()
            drag["history_pushed"] = True
        mode = drag["mode"]
        if mode == "move":
            dx, dy = x - drag["start"][0], y - drag["start"][1]
            stroke["offset"] = (drag["offset"][0] + dx, drag["offset"][1] + dy)
        elif mode == "rotate":
            center = drag["center"]
            offset = drag["offset"]
            world_center = (center[0] + offset[0], center[1] + offset[1])
            angle = math.atan2(y - world_center[1], x - world_center[0])
            delta = math.degrees(angle - drag["start_angle"])
            stroke["rotation"] = drag["rotation"] + delta
        else:
            fixed = drag["fixed_local"]
            original_center = drag["center"]
            original_rotation = drag["rotation"]
            unshifted = (x - drag["offset"][0], y - drag["offset"][1])
            moving_local = self._rotate_shape_point(
                unshifted, original_center, -original_rotation,
            )
            handle = drag["handle"]
            original_points = drag["points"]
            if stroke.get("tool") in ("line", "arrow"):
                moving_index = handle
                fixed_endpoint = drag["fixed_local"]
                new_points = list(original_points)
                new_points[moving_index * 2:moving_index * 2 + 2] = moving_local
                stroke["points"] = new_points
                fixed = fixed_endpoint
            else:
                moving = moving_local
                if handle == 0:
                    new_points = [moving[0], moving[1], fixed[0], fixed[1]]
                elif handle == 1:
                    new_points = [fixed[0], moving[1], moving[0], fixed[1]]
                elif handle == 2:
                    new_points = [fixed[0], fixed[1], moving[0], moving[1]]
                else:
                    new_points = [moving[0], fixed[1], fixed[0], moving[1]]
                stroke["points"] = new_points
            fixed_world_rotated = self._rotate_shape_point(
                fixed, original_center, original_rotation,
            )
            fixed_world = (fixed_world_rotated[0] + drag["offset"][0],
                           fixed_world_rotated[1] + drag["offset"][1])
            left, top, right, bottom = self._shape_local_bounds(stroke)
            new_center = ((left + right) / 2, (top + bottom) / 2)
            rotated_anchor = self._rotate_shape_point(fixed, new_center, original_rotation)
            stroke["offset"] = (fixed_world[0] - rotated_anchor[0],
                                fixed_world[1] - rotated_anchor[1])
            stroke["rotation"] = original_rotation
        self._redraw_transformed_shape(stroke)

    def _redraw_transformed_shape(self, stroke):
        shape_key = stroke.setdefault("shape_key", id(stroke))
        for canvas_stroke in self.canvas_only_strokes:
            if canvas_stroke.get("shape_key") != shape_key:
                continue
            scale = canvas_stroke.get("canvas_scale", self.fit_scale)
            origin_x, origin_y = canvas_stroke.get("canvas_origin", self.display_origin)
            points = stroke["points"]
            canvas_stroke["points"] = [
                points[index] * scale + (origin_x if index % 2 == 0 else origin_y)
                for index in range(len(points))
            ]
            offset = stroke.get("offset", (0.0, 0.0))
            canvas_stroke["offset"] = (offset[0] * scale, offset[1] * scale)
            canvas_stroke["rotation"] = stroke.get("rotation", 0.0)
            canvas_stroke["tool"] = stroke.get("tool", canvas_stroke.get("tool", "rectangle"))
            canvas_stroke["color"] = stroke.get("color", canvas_stroke.get("color", "#000000"))
            break

        drag = self._shape_transform_drag
        if drag is not None and drag.get("stroke") is stroke:
            self.canvas.delete("active-mark")
            self._drawing_preview_photo = None
            self._drawing_preview_item = None
            self._draw_stroke_patch(stroke, "active-mark")
            self._render_shape_selection()
            if self.canvas_only_strokes:
                self._render_canvas_only_overlay(
                    max(100, self.canvas.winfo_width()), max(100, self.canvas.winfo_height()),
                )
            return

        self._invalidate_preview_source()
        self._render_preview()

    def _invalidate_preview_source(self):
        self._stroke_render_revision += 1
        self._preview_source_cache_key = None
        self._preview_source_cache = None

    def _render_preview(self):
        if not self.image: return
        self._hide_size_preview()
        w, h = max(100, self.canvas.winfo_width()), max(100, self.canvas.winfo_height())
        base_scale = min((w - 42) / self.image.width, (h - 42) / self.image.height, 1.0)
        max_render_scale = min(3.0, (40_000_000 / (self.image.width*self.image.height))**0.5)
        scale = max(0.02, min(base_scale*self.zoom_factor, max_render_scale)); self.fit_scale = scale
        iw, ih = max(1, int(self.image.width*scale)), max(1, int(self.image.height*scale))
        self.image_display_size = (iw, ih)
        self.display_origin = (max((w-iw)//2,0), max((h-ih)//2,0))
        ox, oy = self.display_origin
        self.scroll_size=(max(w,ox+iw),max(h,oy+ih))
        # Render the screenshot and every committed mark in source-image space,
        # then scale them together. Preview and export now use the same stroke
        # renderer, so marks cannot vanish when canvas items are rebuilt.
        excluded_shape = (
            self._shape_transform_drag.get("stroke")
            if self._shape_transform_drag else None
        )
        preview_strokes = (
            [stroke for stroke in self.strokes if stroke is not excluded_shape]
            if excluded_shape is not None else self.strokes
        )
        cache_key = (
            id(self.image), id(self.annotation_layer), id(self.background_layer),
            id(self.strokes), self._stroke_render_revision,
            id(excluded_shape) if excluded_shape is not None else None,
        )
        if cache_key != self._preview_source_cache_key:
            self._preview_source_cache = self._compose_canvas_layers(
                self.image, self.annotation_layer, self.background_layer, preview_strokes,
            )
            self._preview_source_cache_key = cache_key
        preview_source = self._preview_source_cache
        editor_color = self._editor_canvas_rgb()
        preview_bitmap = preview_source.resize((iw, ih), Image.Resampling.LANCZOS)
        preview, self._preview_has_transparency = self._image_as_rgb(
            preview_bitmap, editor_color, return_transparency=True,
        )
        self._crop_preview_base = None
        self._crop_dimmed_preview = None
        self._crop_overlay_photo = None
        self._recognition_base_preview = preview
        self._recognition_selection_photos = []
        # Preserve alpha so the canvas background can change themes without
        # rerendering a potentially large screenshot and its annotation layers.
        self.photo = ImageTk.PhotoImage(preview_bitmap, master=self.canvas)
        self.canvas.delete("all")
        self._active_highlighter_overlay = None
        self._drawing_preview_item = None
        self._drawing_preview_photo = None
        self._shape_preview_photos = []
        self._shape_preview_photos_by_key = {}
        self.brush_cursor_items = None
        self.canvas.configure(scrollregion=(0,0,*self.scroll_size))
        self.canvas.create_rectangle(
            ox-1,oy-1,ox+iw+1,oy+ih+1,
            fill=self.canvas.cget("bg"), outline=EDGE, tags="preview-frame",
        )
        self._preview_canvas_item = self.canvas.create_image(
            ox, oy, image=self.photo, anchor="nw", tags="preview",
        )
        self._render_canvas_only_overlay(w, h)
        self._render_recognition_overlay(scale, ox, oy)
        base_scale=min((w-42)/self.image.width,(h-42)/self.image.height,1.0)
        self.size_badge=self.canvas.create_text(self.canvas.canvasx(w-12),self.canvas.canvasy(12),text=f"{self.image.width} × {self.image.height} px · {round(scale/base_scale*100)}%",fill=MUTED,font=("Segoe UI",9),anchor="ne",tags="size-badge")
        self._render_crop_overlay()
        self._rebuild_active_stroke_preview()
        if self._shape_transform_drag is not None:
            self._draw_stroke_patch(
                self._shape_transform_drag["stroke"], "active-mark",
            )
        self._render_shape_selection()
        self._update_brush_cursor()

    def _render_recognition_overlay(self, scale, origin_x, origin_y):
        """Dim the snip outside detected text and QR regions in the editor only."""
        self.canvas.delete("recognition-overlay")
        self._recognition_dim_photo = None
        if not self._recognition_regions:
            self.canvas.delete("recognition-selection")
            self.canvas.delete("recognition-qr-copy")
            return
        width, height = self.image_display_size
        alpha = Image.new("L", (width, height), 155)
        cutouts = ImageDraw.Draw(alpha)
        padding = max(3, round(4 * scale))
        for region in self._recognition_regions:
            box = region["box"]
            left, top, right, bottom = box
            if right <= left or bottom <= top:
                continue
            cutouts.rectangle(
                (
                    max(0, round(left * scale) - padding),
                    max(0, round(top * scale) - padding),
                    min(width - 1, round(right * scale) + padding),
                    min(height - 1, round(bottom * scale) + padding),
                ),
                fill=0,
            )
        dim = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        dim.putalpha(alpha)
        self._recognition_dim_photo = ImageTk.PhotoImage(dim, master=self.canvas)
        self.canvas.create_image(
            origin_x, origin_y, image=self._recognition_dim_photo,
            anchor="nw", tags="recognition-overlay",
        )
        self._render_recognition_selection()

    def _render_recognition_selection(self):
        """Render continuous, visible Windows-style selection bands."""
        if not hasattr(self, "canvas"):
            return
        self.canvas.delete("recognition-selection")
        self.canvas.delete("recognition-qr-copy")
        self._recognition_selection_photos = []
        if not self._recognition_regions:
            return
        scale = getattr(self, "fit_scale", 1.0)
        origin_x, origin_y = getattr(self, "display_origin", (0, 0))

        all_text_lines = {}
        selected_text_lines = {}
        selected_qr = []
        for index in self._recognition_selection:
            if not (0 <= index < len(self._recognition_regions)):
                continue
            region = self._recognition_regions[index]
            if region["kind"] == "qr":
                selected_qr.append(region)
            else:
                selected_text_lines.setdefault(region.get("line", 0), []).append(region["box"])
        for region in self._recognition_regions:
            if region["kind"] == "text":
                all_text_lines.setdefault(region.get("line", 0), []).append(region["box"])

        line_metrics = {}
        line_heights = []
        for line, boxes in all_text_lines.items():
            top = min(box[1] for box in boxes)
            bottom = max(box[3] for box in boxes)
            line_metrics[line] = ((top + bottom) / 2, bottom - top)
            line_heights.append(max(1.0, (bottom - top) * scale))
        if line_heights:
            line_heights.sort()
            middle = len(line_heights) // 2
            uniform_height = line_heights[middle]
            if len(line_heights) % 2 == 0:
                uniform_height = (line_heights[middle - 1] + uniform_height) / 2
            uniform_height = max(6.0, uniform_height)
            for line, boxes in selected_text_lines.items():
                metrics = line_metrics.get(line)
                if not metrics:
                    continue
                center_y = metrics[0] * scale
                left = min(box[0] for box in boxes) * scale
                right = max(box[2] for box in boxes) * scale
                top = center_y - uniform_height / 2
                bottom = center_y + uniform_height / 2
                base = getattr(self, "_recognition_base_preview", None)
                if base is None:
                    continue
                patch_left = max(0, math.floor(left - 1))
                patch_top = max(0, math.floor(top))
                patch_right = min(base.width, math.ceil(right + 1))
                patch_bottom = min(base.height, math.ceil(bottom))
                if patch_right <= patch_left or patch_bottom <= patch_top:
                    continue

                # Tint the actual pixels so the selection looks like the pale,
                # continuous Windows text highlight while retaining dark glyphs.
                patch = base.crop((patch_left, patch_top, patch_right, patch_bottom))
                luminance = ImageOps.grayscale(patch)
                alpha = luminance.point(lambda value: 18 + (value * 102 + 127) // 255)
                tint = Image.new("RGBA", patch.size, (132, 181, 255, 0))
                tint.putalpha(alpha)
                highlighted = patch.convert("RGBA")
                highlighted.alpha_composite(tint)
                photo = ImageTk.PhotoImage(highlighted, master=self.canvas)
                self._recognition_selection_photos.append(photo)
                self.canvas.create_image(
                    origin_x + patch_left, origin_y + patch_top,
                    image=photo, anchor="nw", tags="recognition-selection",
                )

        for region in selected_qr:
            left, top, right, bottom = region["box"]
            self.canvas.create_rectangle(
                origin_x + math.floor(left * scale),
                origin_y + math.floor(top * scale),
                origin_x + math.ceil(right * scale),
                origin_y + math.ceil(bottom * scale),
                fill="#20bfd0", stipple="gray50", outline="#087e91",
                width=max(2, round(2 * scale)),
                tags="recognition-selection",
            )
        for index, region in enumerate(self._recognition_regions):
            if region["kind"] != "qr":
                continue
            left, top, right, bottom = region["box"]
            center_x = origin_x + ((left + right) / 2) * scale
            center_y = origin_y + ((top + bottom) / 2) * scale
            self.canvas.create_image(
                center_x, center_y, image=self._recognition_qr_copy_icon,
                anchor="center", tags=("recognition-qr-copy", f"recognition-qr-copy-{index}"),
            )
        self.canvas.tag_raise("recognition-selection")
        self.canvas.tag_raise("recognition-qr-copy")

    def _recognition_hit_test(self, event, kind=None, threshold=None):
        """Find a recognized region near a canvas point, allowing easy text-line selection."""
        x = self.canvas.canvasx(event.x)
        y = self.canvas.canvasy(event.y)
        return self._recognition_hit_test_at(x, y, kind=kind, threshold=threshold)

    def _recognition_hit_test_at(self, x, y, kind=None, threshold=None):
        if not self._recognition_regions:
            return None
        origin_x, origin_y = self.display_origin
        scale = max(0.01, self.fit_scale)
        best_index = None
        best_distance = float("inf")
        for index, region in enumerate(self._recognition_regions):
            if kind and region["kind"] != kind:
                continue
            left, top, right, bottom = region["box"]
            left = origin_x + left * scale
            top = origin_y + top * scale
            right = origin_x + right * scale
            bottom = origin_y + bottom * scale
            dx = max(left - x, 0, x - right)
            dy = max(top - y, 0, y - bottom)
            distance = math.hypot(dx, dy)
            allowed = threshold
            if allowed is None:
                allowed = 30 if region["kind"] == "text" else 8
            prefer_qr = (
                distance == best_distance
                and region["kind"] == "qr"
                and best_index is not None
                and self._recognition_regions[best_index]["kind"] != "qr"
            )
            if distance <= allowed and (distance < best_distance or prefer_qr):
                best_index = index
                best_distance = distance
        return best_index

    def _recognition_hover_kind(self):
        if not self._recognition_regions or not self.pointer_inside_canvas:
            return None
        point = getattr(self, "_pointer_canvas_pos", None)
        if not point:
            return None
        index = self._recognition_hit_test_at(*point, threshold=0)
        return self._recognition_regions[index]["kind"] if index is not None else None

    def _recognition_qr_control_center(self, index):
        if not (0 <= index < len(self._recognition_regions)):
            return None
        region = self._recognition_regions[index]
        if region["kind"] != "qr":
            return None
        left, top, right, bottom = region["box"]
        origin_x, origin_y = self.display_origin
        scale = self.fit_scale
        return (
            origin_x + ((left + right) / 2) * scale,
            origin_y + ((top + bottom) / 2) * scale,
        )

    def _recognition_qr_control_hit_test(self, x, y):
        half_size = 15
        for index, region in enumerate(self._recognition_regions):
            if region["kind"] != "qr":
                continue
            center = self._recognition_qr_control_center(index)
            if center and abs(center[0] - x) <= half_size and abs(center[1] - y) <= half_size:
                return index
        return None

    def _update_recognition_qr_tooltip(self, event):
        index = self._recognition_qr_control_hit_test(
            self.canvas.canvasx(event.x), self.canvas.canvasy(event.y),
        ) if self._recognition_mode else None
        if index == self._recognition_hover_qr_index:
            return
        self._hide_recognition_qr_tooltip()
        self._recognition_hover_qr_index = index
        if index is not None:
            self._recognition_tooltip_position = (event.x_root, event.y_root)
            self._recognition_tooltip_after = self.root.after(
                350, lambda hovered=index: self._show_recognition_qr_tooltip(hovered),
            )

    def _show_recognition_qr_tooltip(self, index):
        self._recognition_tooltip_after = None
        if (index != self._recognition_hover_qr_index
                or not (0 <= index < len(self._recognition_regions))):
            return
        data = self._recognition_regions[index].get("text", "")
        if not data:
            return
        try:
            tooltip = tk.Toplevel(self.root)
            tooltip.overrideredirect(True)
            tooltip.attributes("-topmost", True)
            tooltip.configure(bg="#20bfd0")
            card = tk.Frame(tooltip, bg="#20242c", padx=10, pady=8)
            card.pack(padx=1, pady=1)
            tk.Label(
                card, text="QR code data", bg="#20242c", fg="#20bfd0",
                justify="left", anchor="w", font=("Segoe UI Semibold", 9),
            ).pack(fill="x", pady=(0, 3))
            tk.Label(
                card,
                text=textwrap.fill(data, width=58, break_long_words=True, break_on_hyphens=False),
                bg="#20242c", fg="#f4f7fb", justify="left", anchor="w",
                wraplength=420, bd=0, font=("Segoe UI", 9),
            ).pack(fill="x")
            tooltip.update_idletasks()
            width = tooltip.winfo_width()
            height = tooltip.winfo_height()
            pointer_x, pointer_y = self._recognition_tooltip_position or (0, 0)
            user32 = ctypes.windll.user32
            user32.MonitorFromPoint.argtypes = (_POINT, wintypes.DWORD)
            user32.MonitorFromPoint.restype = wintypes.HANDLE
            user32.GetMonitorInfoW.argtypes = (wintypes.HANDLE, ctypes.POINTER(_MONITORINFO))
            user32.GetMonitorInfoW.restype = wintypes.BOOL
            monitor = user32.MonitorFromPoint(
                _POINT(int(pointer_x), int(pointer_y)), 2,  # MONITOR_DEFAULTTONEAREST
            )
            info = _MONITORINFO()
            info.cbSize = ctypes.sizeof(_MONITORINFO)
            if not monitor or not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                raise ctypes.WinError()
            work = info.rcWork
            margin = 8
            min_x, min_y = work.left + margin, work.top + margin
            max_x = max(min_x, work.right - width - margin)
            max_y = max(min_y, work.bottom - height - margin)
            x = min(max(min_x, pointer_x + 14), max_x)
            y = pointer_y + 18
            if y + height > work.bottom - margin:
                y = pointer_y - height - 10
            y = min(max(min_y, y), max_y)
            tooltip.geometry(f"{width}x{height}+0+0")
            tooltip.update_idletasks()
            user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
            user32.GetAncestor.restype = wintypes.HWND
            hwnd = user32.GetAncestor(tooltip.winfo_id(), 2) or tooltip.winfo_id()
            user32.SetWindowPos.argtypes = (
                wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                ctypes.c_int, ctypes.c_int, wintypes.UINT,
            )
            user32.SetWindowPos.restype = wintypes.BOOL
            if not user32.SetWindowPos(hwnd, None, x, y, width, height, 0x0014):
                raise ctypes.WinError()
            self._recognition_tooltip = tooltip
        except (tk.TclError, OSError, AttributeError, TypeError, ValueError):
            self._recognition_tooltip = None
            try:
                tooltip.destroy()
            except (tk.TclError, UnboundLocalError):
                pass

    def _hide_recognition_qr_tooltip(self):
        if self._recognition_tooltip_after is not None:
            try:
                self.root.after_cancel(self._recognition_tooltip_after)
            except tk.TclError:
                pass
            self._recognition_tooltip_after = None
        tooltip, self._recognition_tooltip = self._recognition_tooltip, None
        if tooltip is not None:
            try:
                tooltip.destroy()
            except tk.TclError:
                pass

    def _set_recognition_text_range(self, anchor_index, current_index):
        text_indices = [
            index for index, region in enumerate(self._recognition_regions)
            if region["kind"] == "text"
        ]
        if anchor_index not in text_indices or current_index not in text_indices:
            return
        start = text_indices.index(anchor_index)
        end = text_indices.index(current_index)
        self._recognition_selection = text_indices[min(start, end):max(start, end) + 1]
        self._render_recognition_selection()

    def _recognition_press(self, event):
        if not self._recognition_regions:
            return False
        self.canvas.focus_set()
        x = self.canvas.canvasx(event.x)
        y = self.canvas.canvasy(event.y)
        qr_index = self._recognition_qr_control_hit_test(x, y)
        text_index = (
            self._recognition_hit_test(event, kind="text")
            if qr_index is None else None
        )
        now = time.monotonic()
        last_click = self._recognition_last_text_click
        is_double_text_click = bool(
            text_index is not None and last_click
            and now - last_click[0] <= 0.5
            and (x - last_click[1]) ** 2 + (y - last_click[2]) ** 2 <= 64
        )
        self._recognition_last_text_click = (
            None if is_double_text_click or text_index is None
            else (now, x, y)
        )
        if self._recognition_selection:
            self._recognition_selection = []
            self._render_recognition_selection()
        self._recognition_drag = None
        if qr_index is not None:
            self._copy_recognition_region(qr_index)
            return True
        if text_index is not None:
            if is_double_text_click:
                self._recognition_drag = {"kind": "text", "anchor": text_index}
                self._set_recognition_text_range(text_index, text_index)
                self.status.set("Selected word · drag to extend · Ctrl+C or right-click to copy")
            else:
                self._recognition_drag = {
                    "kind": "pending", "anchor": text_index, "start": (x, y),
                }
                self.status.set("Drag across recognized text to select it · double-click a word to select it")
        else:
            index = self._recognition_hit_test(event)
            if index is not None:
                self.status.set("Click the QR icon to copy its data · hover to preview it")
            else:
                self._recognition_drag = {
                    "kind": "pending", "anchor": None, "start": (x, y),
                }
        return True

    def _recognition_motion(self, event):
        drag = self._recognition_drag
        if not drag:
            return False
        x = self.canvas.canvasx(event.x)
        y = self.canvas.canvasy(event.y)
        if drag.get("kind") == "pending":
            start_x, start_y = drag["start"]
            if (x - start_x) ** 2 + (y - start_y) ** 2 < 16:
                return True
            index = self._recognition_hit_test_at(x, y, kind="text", threshold=50)
            anchor = drag.get("anchor")
            if anchor is None:
                anchor = index
            if anchor is None:
                return True
            self._recognition_drag = {"kind": "text", "anchor": anchor}
            self._set_recognition_text_range(anchor, anchor)
            self.status.set("Drag across recognized text to select it · Ctrl+C or right-click to copy")
            if index is not None:
                self._set_recognition_text_range(anchor, index)
        elif drag.get("kind") == "text":
            index = self._recognition_hit_test_at(x, y, kind="text", threshold=40)
            if index is not None:
                self._set_recognition_text_range(drag["anchor"], index)
        return True

    def _recognition_release(self, _event):
        if not self._recognition_drag:
            return False
        self._recognition_drag = None
        if self._recognition_selection:
            kinds = {
                self._recognition_regions[index]["kind"]
                for index in self._recognition_selection
                if 0 <= index < len(self._recognition_regions)
            }
            if "qr" in kinds:
                self.status.set("QR code selected · Ctrl+C or right-click to copy its contents")
            else:
                count = len(self._recognition_selection)
                self.status.set(f"Selected {count} text word{'s' if count != 1 else ''} · Ctrl+C or right-click to copy")
        return True

    def _selected_recognition_text(self):
        selected = [
            self._recognition_regions[index]
            for index in self._recognition_selection
            if 0 <= index < len(self._recognition_regions)
            and self._recognition_regions[index]["kind"] == "text"
        ]
        if not selected:
            return ""
        chunks = []
        previous_line = None
        for region in selected:
            line = region.get("line", 0)
            if previous_line is not None:
                chunks.append("\n" if line != previous_line else " ")
            chunks.append(region.get("text", ""))
            previous_line = line
        return "".join(chunks).strip()

    def _copy_selected_recognition(self):
        if not self._recognition_selection:
            return False
        qr_values = [
            self._recognition_regions[index].get("text", "")
            for index in self._recognition_selection
            if 0 <= index < len(self._recognition_regions)
            and self._recognition_regions[index]["kind"] == "qr"
        ]
        copied_qr = bool(qr_values)
        content = "\n".join(value for value in qr_values if value) if copied_qr else self._selected_recognition_text()
        if not content:
            return False
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(content)
            self.root.update_idletasks()
            self.status.set("QR code contents copied" if copied_qr else "Selected text copied")
            if copied_qr:
                self._hide_recognition_qr_tooltip()
                self._show_recognition_copy_notice()
            return True
        except tk.TclError as exc:
            self.status.set(f"Couldn't copy selection: {exc}")
            return False

    def _copy_recognition_region(self, index):
        if not (0 <= index < len(self._recognition_regions)):
            return False
        region = self._recognition_regions[index]
        if region["kind"] != "qr":
            return False
        content = region.get("text", "")
        if not content:
            return False
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(content)
            self.root.update_idletasks()
            self.status.set("QR code data copied")
            self._hide_recognition_qr_tooltip()
            self._show_recognition_copy_notice()
            return True
        except tk.TclError as exc:
            self.status.set(f"Couldn't copy QR code data: {exc}")
            return False

    def _show_recognition_copy_notice(self):
        """Show a brief on-canvas confirmation after copying QR data."""
        if not hasattr(self, "canvas"):
            return
        if self._recognition_copy_notice_after is not None:
            try:
                self.root.after_cancel(self._recognition_copy_notice_after)
            except tk.TclError:
                pass
            self._recognition_copy_notice_after = None
        self.canvas.delete("recognition-copy-notice")
        center_x = self.canvas.canvasx(max(0, self.canvas.winfo_width()) / 2)
        top_y = self.canvas.canvasy(22)
        half_width, half_height = 91, 17
        self.canvas.create_rectangle(
            center_x - half_width, top_y - half_height,
            center_x + half_width, top_y + half_height,
            fill="#087e67", outline="#9af0d4", width=1,
            tags="recognition-copy-notice",
        )
        self.canvas.create_text(
            center_x, top_y, text="QR code text copied",
            fill="#ffffff", font=("Segoe UI Semibold", 9),
            tags="recognition-copy-notice",
        )
        self.canvas.tag_raise("recognition-copy-notice")

        def clear_notice():
            self._recognition_copy_notice_after = None
            try:
                self.canvas.delete("recognition-copy-notice")
            except tk.TclError:
                pass

        self._recognition_copy_notice_after = self.root.after(1800, clear_notice)

    def _show_pipette_copy_notice(self, color):
        """Flash a small confirmation after the pipette copies a color."""
        if not hasattr(self, "canvas") or not self.canvas.winfo_exists():
            return
        if self._pipette_copy_notice_after is not None:
            try:
                self.root.after_cancel(self._pipette_copy_notice_after)
            except tk.TclError:
                pass
            self._pipette_copy_notice_after = None
        self.canvas.delete("pipette-copy-notice")
        center_x = self.canvas.canvasx(max(0, self.canvas.winfo_width()) / 2)
        center_y = self.canvas.canvasy(max(24, self.canvas.winfo_height() - 28))
        half_width, half_height = 69, 16
        self.canvas.create_rectangle(
            center_x - half_width, center_y - half_height,
            center_x + half_width, center_y + half_height,
            fill="#087e67", outline="#9af0d4", width=1,
            tags="pipette-copy-notice",
        )
        self.canvas.create_text(
            center_x, center_y, text=f"Copied {color}",
            fill="#ffffff", font=("Segoe UI Semibold", 9),
            tags="pipette-copy-notice",
        )
        self.canvas.tag_raise("pipette-copy-notice")

        def clear_notice():
            self._pipette_copy_notice_after = None
            try:
                self.canvas.delete("pipette-copy-notice")
            except tk.TclError:
                pass

        self._pipette_copy_notice_after = self.root.after(1800, clear_notice)

    def _copy_recognition_or_image(self, _event=None):
        if not self._copy_selected_recognition():
            self.copy_image()
        return "break"

    def _recognition_right_click(self, event):
        if not self._recognition_regions:
            return None
        index = self._recognition_hit_test(event)
        if index is not None and index not in self._recognition_selection:
            self._recognition_selection = [index]
            self._render_recognition_selection()
        if not self._recognition_selection:
            return None
        selected_qr = any(
            0 <= item < len(self._recognition_regions)
            and self._recognition_regions[item]["kind"] == "qr"
            for item in self._recognition_selection
        )
        menu = tk.Menu(
            self.root, tearoff=0, bg=PANEL2, fg=TEXT,
            activebackground=ACCENT, activeforeground="white",
        )
        menu.add_command(
            label="Copy QR code contents" if selected_qr else "Copy selected text",
            command=self._copy_selected_recognition,
        )
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return "break"

    def _select_all_recognized_text(self, _event=None):
        if not self._recognition_regions:
            return None
        self._recognition_selection = [
            index for index, region in enumerate(self._recognition_regions)
            if region["kind"] == "text"
        ]
        self._render_recognition_selection()
        self.status.set("All recognized text selected · Ctrl+C to copy")
        return "break"

    def _canvas_escape(self, event=None):
        if self.tool.get() == "crop":
            return self._cancel_crop(event)
        if self._recognition_regions:
            self._clear_recognition_regions()
            self._render_preview()
            self.status.set("Recognition selection closed · ready to draw")
            return "break"
        return self._cancel_rotation(event)

    def _clear_recognition_regions(self):
        self._hide_recognition_qr_tooltip()
        self._recognition_hover_qr_index = None
        if self._recognition_copy_notice_after is not None:
            try:
                self.root.after_cancel(self._recognition_copy_notice_after)
            except tk.TclError:
                pass
            self._recognition_copy_notice_after = None
        self._recognition_regions = []
        self._recognition_selection = []
        self._recognition_selection_photos = []
        self._recognition_base_preview = None
        self._recognition_drag = None
        self._recognition_last_text_click = None
        self._recognition_mode = False
        if hasattr(self, "canvas"):
            try:
                self.canvas.delete("recognition-overlay")
                self.canvas.delete("recognition-selection")
                self.canvas.delete("recognition-qr-copy")
                self.canvas.delete("recognition-copy-notice")
            except tk.TclError:
                pass
        self._recognition_dim_photo = None
        if getattr(self, "recognition_button", None):
            try:
                self._style_mode_buttons()
            except (AttributeError, tk.TclError):
                pass

    def _start_recognition_animation(self):
        self._stop_recognition_animation()
        self._recognition_animation_offset = 0
        self._animate_recognition_border()

    def _stop_recognition_animation(self):
        if self._recognition_animation_after is not None:
            try:
                self.root.after_cancel(self._recognition_animation_after)
            except tk.TclError:
                pass
            self._recognition_animation_after = None
        if hasattr(self, "canvas"):
            try:
                self.canvas.delete("recognition-activity")
            except tk.TclError:
                pass

    def _animate_recognition_border(self):
        self._recognition_animation_after = None
        if not self._recognition_busy or not self.image:
            return
        try:
            origin_x, origin_y = self.display_origin
            image_width, image_height = self.image_display_size
            self.canvas.delete("recognition-activity")
            self.canvas.create_rectangle(
                origin_x - 5, origin_y - 5,
                origin_x + image_width + 5, origin_y + image_height + 5,
                outline=ACCENT, width=3, dash=(9, 7),
                dashoffset=self._recognition_animation_offset,
                tags="recognition-activity",
            )
            self.canvas.tag_raise("recognition-activity")
            self._recognition_animation_offset = (self._recognition_animation_offset + 2) % 16
            self._recognition_animation_after = self.root.after(
                65, self._animate_recognition_border,
            )
        except (AttributeError, tk.TclError):
            self._recognition_animation_after = None

    def _render_canvas_only_overlay(self, viewport_width, viewport_height):
        """Show off-image strokes on the editor surface without exporting them."""
        self.canvas.delete("canvas-only-marks")
        self._canvas_only_preview_photo = None
        if not self.canvas_only_strokes:
            return

        view_left, view_top = self.canvas.canvasx(0), self.canvas.canvasy(0)
        shifted_strokes = []
        for stroke in self.canvas_only_strokes:
            points = list(stroke["points"])
            shifted_strokes.append(dict(
                stroke,
                points=[points[i] - (view_left if i % 2 == 0 else view_top) for i in range(len(points))],
            ))

        overlay = self._composite_image(
            Image.new("RGBA", (viewport_width, viewport_height), (0, 0, 0, 0)),
            shifted_strokes,
        )
        mask = Image.new("L", overlay.size, 255)
        mask_draw = ImageDraw.Draw(mask)
        image_left = round(self.display_origin[0] - view_left)
        image_top = round(self.display_origin[1] - view_top)
        image_right = round(self.display_origin[0] + self.image_display_size[0] - view_left)
        image_bottom = round(self.display_origin[1] + self.image_display_size[1] - view_top)
        clip_left, clip_top = max(0, image_left), max(0, image_top)
        clip_right = min(viewport_width - 1, image_right - 1)
        clip_bottom = min(viewport_height - 1, image_bottom - 1)
        if clip_right >= clip_left and clip_bottom >= clip_top:
            mask_draw.rectangle((clip_left, clip_top, clip_right, clip_bottom), fill=0)
        overlay.putalpha(ImageChops.multiply(overlay.getchannel("A"), mask))
        if overlay.getchannel("A").getbbox() is None:
            return

        self._canvas_only_preview_photo = ImageTk.PhotoImage(overlay, master=self.canvas)
        self.canvas.create_image(
            view_left, view_top, image=self._canvas_only_preview_photo,
            anchor="nw", tags="canvas-only-marks",
        )
        if self.canvas.find_withtag("size-badge"):
            self.canvas.tag_lower("canvas-only-marks", "size-badge")

    def _update_size_badge(self):
        if not self.image or not self.size_badge:return
        w,h=self.canvas.winfo_width(),self.canvas.winfo_height()
        base=min((max(100,w)-42)/self.image.width,(max(100,h)-42)/self.image.height,1.0)
        self.canvas.coords(self.size_badge,self.canvas.canvasx(w-12),self.canvas.canvasy(12))
        self.canvas.itemconfigure(self.size_badge,text=f"{self.image.width} × {self.image.height} px · {round(self.fit_scale/base*100)}%")

    def _update_brush_cursor(self):
        brush_tools = ("pen", "highlighter", "eraser")
        # Crop movement immediately installs its own edge/handle cursor. Resetting
        # to an arrow here first makes Tk alternate visibly between both cursors.
        if self.tool.get() not in (
            "crop", "rectangle", "rectangle_filled", "circle", "circle_filled",
            "line", "arrow", "cross", "tick",
        ):
            self.canvas.configure(cursor="arrow")
        recognition_kind = self._recognition_hover_kind()
        if self._recognition_mode:
            self.canvas.delete("brush-cursor")
            self.brush_cursor_items = None
            self._restore_system_cursor()
            point = getattr(self, "_pointer_canvas_pos", None)
            qr_control = (
                self._recognition_qr_control_hit_test(*point)
                if point and self.pointer_inside_canvas else None
            )
            cursor = "hand2" if qr_control is not None else "xterm" if recognition_kind == "text" else "arrow"
            self.canvas.configure(cursor=cursor)
            return
        if not self.image or not self.pointer_inside_canvas or self.tool.get() not in brush_tools:
            self.canvas.delete("brush-cursor")
            self.brush_cursor_items = None
            self._restore_system_cursor()
            return
        x,y=self._pointer_canvas_pos
        diameter=self._stroke_render_width(self.tool.get(),self.width.get())*self.fit_scale
        radius=max(3,diameter/2)
        cursor_shape = (
            "rectangle" if self.tool.get() == "eraser"
            else "line" if self.tool.get() == "highlighter"
            else "oval"
        )
        create_cursor = {
            "rectangle": self.canvas.create_rectangle,
            "line": self.canvas.create_line,
            "oval": self.canvas.create_oval,
        }[cursor_shape]
        cursor_options = (
            {"fill": "#15171c", "width": 4}
            if cursor_shape == "line" else {"outline": "#15171c", "width": 2}
        )
        inner_cursor_options = (
            {"fill": "#ffffff", "width": 2}
            if cursor_shape == "line" else {"outline": "#ffffff", "width": 1}
        )
        if not self.brush_cursor_items:
            self.brush_cursor_items=(
                create_cursor(0,0,0,0,**cursor_options,tags="brush-cursor"),
                create_cursor(0,0,0,0,**inner_cursor_options,tags="brush-cursor"),
            )
        outer,inner=self.brush_cursor_items
        if self.canvas.type(outer) != cursor_shape:
            self.canvas.delete(outer)
            self.canvas.delete(inner)
            self.brush_cursor_items=(
                create_cursor(0,0,0,0,**cursor_options,tags="brush-cursor"),
                create_cursor(0,0,0,0,**inner_cursor_options,tags="brush-cursor"),
            )
            outer,inner=self.brush_cursor_items
        if cursor_shape == "line":
            self.canvas.coords(outer,x,y-radius-1,x,y+radius+1)
            self.canvas.coords(inner,x,y-radius,x,y+radius)
        else:
            self.canvas.coords(outer,x-radius-1,y-radius-1,x+radius+1,y+radius+1)
            self.canvas.coords(inner,x-radius,y-radius,x+radius,y+radius)
        self.canvas.tag_raise("brush-cursor")
        self._hide_system_cursor()

    def _hide_system_cursor(self):
        if self.system_cursor_hide_count:return
        try:
            show=ctypes.windll.user32.ShowCursor
            show.argtypes=[wintypes.BOOL];show.restype=ctypes.c_int
            for _ in range(32):
                self.system_cursor_hide_count+=1
                if show(False)<0:break
        except Exception:self.system_cursor_hide_count=0

    def _restore_system_cursor(self):
        if not self.system_cursor_hide_count:return
        try:
            show=ctypes.windll.user32.ShowCursor
            show.argtypes=[wintypes.BOOL];show.restype=ctypes.c_int
            for _ in range(self.system_cursor_hide_count):show(True)
        except Exception:pass
        self.system_cursor_hide_count=0

    def _canvas_enter(self,e):
        self.pointer_inside_canvas=True
        self._hide_size_preview()
        self._pointer_canvas_pos=(self.canvas.canvasx(e.x),self.canvas.canvasy(e.y))
        self._update_brush_cursor()

    def _canvas_leave(self,_e):
        self.pointer_inside_canvas=False
        self.canvas.configure(cursor="arrow")
        self._restore_system_cursor()
        self._recognition_hover_qr_index = None
        self._hide_recognition_qr_tooltip()

    def _set_system_cursor(self,resource_id):
        try:
            u=ctypes.windll.user32
            u.LoadCursorW.argtypes=[wintypes.HINSTANCE,wintypes.LPCWSTR];u.LoadCursorW.restype=ctypes.c_void_p
            u.SetCursor.argtypes=[ctypes.c_void_p];u.SetCursor.restype=ctypes.c_void_p
            pointer=ctypes.cast(ctypes.c_void_p(resource_id),wintypes.LPCWSTR)
            u.SetCursor(u.LoadCursorW(None,pointer))
        except Exception:pass

    def _to_image(self, x, y):
        x,y=self.canvas.canvasx(x),self.canvas.canvasy(y)
        ox, oy = self.display_origin
        return ((x-ox)/self.fit_scale, (y-oy)/self.fit_scale)

    def _stroke_extends_outside_image(self, stroke):
        points = stroke["points"]
        xs, ys = points[0::2], points[1::2]
        margin = max(0.5, self._stroke_render_width(stroke["tool"], stroke["width"]) / 2)
        return (
            min(xs) - margin < 0
            or min(ys) - margin < 0
            or max(xs) + margin > self.image.width
            or max(ys) + margin > self.image.height
        )

    @staticmethod
    def _stroke_to_canvas_space(stroke):
        scale = stroke["canvas_scale"]
        origin_x, origin_y = stroke["canvas_origin"]
        points = stroke["points"]
        offset = stroke.get("offset", (0.0, 0.0))
        return dict(
            stroke,
            points=[points[i] * scale + (origin_x if i % 2 == 0 else origin_y) for i in range(len(points))],
            offset=(offset[0] * scale, offset[1] * scale),
            width=stroke["width"] * scale,
            render_width=ChessSnip._stroke_render_width(stroke["tool"], stroke["width"]) * scale,
            canvas_scale=scale,
            canvas_origin=(origin_x, origin_y),
        )

    def _zoom_preview(self, event, direction=None):
        if not self.image: return "break"
        if direction is None:
            direction=1 if event.delta>0 else -1
            steps=max(1,abs(event.delta)//120)
        else: steps=1
        old_scale=self.fit_scale
        px,py=self.canvas.canvasx(event.x),self.canvas.canvasy(event.y)
        ox,oy=self.display_origin
        ix,iy=(px-ox)/old_scale,(py-oy)/old_scale
        self.zoom_factor=max(0.25,min(8.0,self.zoom_factor*(1.15**(steps*direction))))
        self._render_preview()
        newx=self.display_origin[0]+ix*self.fit_scale
        newy=self.display_origin[1]+iy*self.fit_scale
        self.root.update_idletasks()
        left=max(0,min(self.scroll_size[0]-self.canvas.winfo_width(),newx-event.x))
        top=max(0,min(self.scroll_size[1]-self.canvas.winfo_height(),newy-event.y))
        self.canvas.xview_moveto(left/max(1,self.scroll_size[0]))
        self.canvas.yview_moveto(top/max(1,self.scroll_size[1]))
        self._render_canvas_only_overlay(max(100,self.canvas.winfo_width()),max(100,self.canvas.winfo_height()))
        self._rebuild_active_stroke_preview()
        self._pointer_canvas_pos=(self.canvas.canvasx(event.x),self.canvas.canvasy(event.y))
        self._update_size_badge()
        self._update_brush_cursor()
        base=min((max(100,self.canvas.winfo_width())-42)/self.image.width,(max(100,self.canvas.winfo_height())-42)/self.image.height,1.0)
        self.status.set(f"Zoom {round(self.fit_scale/base*100)}% · scroll to zoom, middle-drag to pan")
        return "break"

    def _pan_start(self,e):
        self._pan_origin=(e.x,e.y,self.canvas.xview()[0],self.canvas.yview()[0])

    def _pan_move(self,e):
        if not getattr(self,"_pan_origin",None):return
        x,y,start_x,start_y=self._pan_origin
        dx,dy=e.x-x,e.y-y
        self.canvas.xview_moveto(max(0,start_x-dx/max(1,self.scroll_size[0])))
        self.canvas.yview_moveto(max(0,start_y-dy/max(1,self.scroll_size[1])))
        self._render_canvas_only_overlay(max(100,self.canvas.winfo_width()),max(100,self.canvas.winfo_height()))
        self._update_size_badge()

    @staticmethod
    def _distance_to_segment(px, py, ax, ay, bx, by):
        dx,dy=bx-ax,by-ay
        length=dx*dx+dy*dy
        t=0 if not length else max(0,min(1,((px-ax)*dx+(py-ay)*dy)/length))
        return ((px-(ax+t*dx))**2+(py-(ay+t*dy))**2)**0.5

    def _stroke_hit_at(self, stroke, x, y, reach):
        points = stroke["points"]
        stroke_reach = reach + self._stroke_width_for(stroke) / 2
        tool = stroke["tool"]
        is_shape = self._is_vector_shape(tool)
        if is_shape:
            x, y = self._shape_point_to_local(stroke, (x, y))
        if tool in ("rectangle_filled", "circle_filled") and len(points) >= 4:
            x1, y1, x2, y2 = points[:4]
            if tool == "rectangle_filled":
                return (
                    min(x1, x2) - stroke_reach <= x <= max(x1, x2) + stroke_reach
                    and min(y1, y2) - stroke_reach <= y <= max(y1, y2) + stroke_reach
                )
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
            rx, ry = abs(x2 - x1) / 2 + stroke_reach, abs(y2 - y1) / 2 + stroke_reach
            return ((x - cx) / max(rx, 0.5)) ** 2 + ((y - cy) / max(ry, 0.5)) ** 2 <= 1
        if tool in ("rectangle", "rectangle_filled") and len(points) >= 4:
            x1, y1, x2, y2 = points[:4]
            path = [(x1, y1), (x2, y1), (x2, y2), (x1, y2), (x1, y1)]
            paths = [path]
        elif tool in ("circle", "circle_filled") and len(points) >= 4:
            x1, y1, x2, y2 = points[:4]
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
            rx, ry = abs(x2 - x1) / 2, abs(y2 - y1) / 2
            path = [
                (cx + rx * math.cos(i * math.tau / 64), cy + ry * math.sin(i * math.tau / 64))
                for i in range(65)
            ]
            paths = [path]
        elif tool in ("cross", "tick") and len(points) >= 4:
            x1, y1, x2, y2 = points[:4]
            left, right = min(x1, x2), max(x1, x2)
            top, bottom = min(y1, y2), max(y1, y2)
            if tool == "cross":
                paths = [
                    [(left, top), (right, bottom)],
                    [(right, top), (left, bottom)],
                ]
            else:
                paths = [[
                    (left, top + (bottom - top) * 0.55),
                    (left + (right - left) * 0.36, bottom),
                    (right, top),
                ]]
        elif tool in ("line", "arrow") and len(points) >= 4:
            start, end = (points[0], points[1]), (points[2], points[3])
            path = [start, end]
            if tool == "arrow":
                dx, dy = end[0] - start[0], end[1] - start[1]
                length = max(1.0, math.hypot(dx, dy))
                ux, uy = dx / length, dy / length
                head_length = min(length * 0.62, max(14.0, self._stroke_width_for(stroke) * 4.5))
                half_head = max(8.0, self._stroke_width_for(stroke) * 2.6)
                base = (end[0] - ux * head_length, end[1] - uy * head_length)
                wing_a = (base[0] - uy * half_head, base[1] + ux * half_head)
                wing_b = (base[0] + uy * half_head, base[1] - ux * half_head)
                path.extend((wing_a, end, wing_b))
            paths = [path]
        else:
            path = [tuple(points[i:i + 2]) for i in range(0, len(points) - 1, 2)]
            paths = [path]
        return any(
            self._distance_to_segment(x, y, *a, *b) <= stroke_reach
            for path in paths for a, b in zip(path, path[1:])
        )

    def _erase_at(self, x, y):
        if not self.image:
            return

        reach = max(1.0, self.width.get() / 2)
        erased = False
        kept = []

        # Keep whole vector marks atomic: touching any part removes the entire
        # line, arrow, rectangle, circle, or other drawn object.
        for stroke in self.strokes:
            if self._stroke_hit_at(stroke, x, y, reach):
                if not self._erase_changed:
                    self._push_history()
                self._erase_changed = True
                erased = True
            else:
                kept.append(stroke)
        self.strokes = kept

        # Marks drawn outside the captured image are still editable on the
        # canvas, though they remain excluded from saved captures.
        canvas_x = x * self.fit_scale + self.display_origin[0]
        canvas_y = y * self.fit_scale + self.display_origin[1]
        canvas_reach = reach * self.fit_scale
        kept_canvas = []
        for stroke in self.canvas_only_strokes:
            if self._stroke_hit_at(stroke, canvas_x, canvas_y, canvas_reach):
                if not self._erase_changed:
                    self._push_history()
                self._erase_changed = True
                erased = True
            else:
                kept_canvas.append(stroke)
        self.canvas_only_strokes = kept_canvas

        # Committed marks are flattened for rotation. Find the connected mark
        # under the eraser and remove all of its pixels as one object.
        layer = self.annotation_layer
        if layer is not None:
            half = max(1, int(math.ceil(reach)))
            left = max(0, int(math.floor(x - half)))
            top = max(0, int(math.floor(y - half)))
            right = min(layer.width, int(math.ceil(x + half)))
            bottom = min(layer.height, int(math.ceil(y + half)))
            if right > left and bottom > top:
                alpha = layer.getchannel("A")
                components = alpha.point(lambda value: 255 if value else 0)
                if components.crop((left, top, right, bottom)).getbbox():
                    if not self._erase_changed:
                        self._push_history()
                    self._erase_changed = True
                    # Mark each connected component touched by the brush.
                    # Pillow performs the flood fill in native code, including
                    # antialiased edge pixels converted to a binary mask.
                    for sy in range(top, bottom):
                        for sx in range(left, right):
                            if components.getpixel((sx, sy)) == 255:
                                ImageDraw.floodfill(components, (sx, sy), 128, thresh=0)
                    removed = components.point(lambda value: 255 if value == 128 else 0)
                    if removed.getbbox():
                        alpha.paste(0, mask=removed)
                        layer.putalpha(alpha)
                        erased = True

        if erased:
            self._invalidate_preview_source()
            self._render_preview()
            self.status.set("Annotation erased · Ctrl+Z to undo")

    def _begin_rotation(self):
        if not self.image:
            return False
        current_marks = self._current_annotation_layer()
        if self._rotation_base_image is None:
            self._rotation_base_image = self.image.convert("RGBA").copy()
            self._rotation_base_angle = self._rotation_angle
            self._rotation_base_marks = current_marks.copy()
        else:
            self._rotation_base_marks = self._marks_in_rotation_source_space(current_marks)
        left, top = self.display_origin
        display_w, display_h = self.image_display_size
        center = (left + display_w / 2, top + display_h / 2)
        self._rotation_drag = {
            # Keep the capture and its marks as separate transparent layers,
            # while previewing and rotating both together from stable sources.
            "image": self._rotation_base_image,
            "annotations": self._rotation_base_marks,
            "center": center,
            "screen_center": (
                center[0] - self.canvas.canvasx(0),
                center[1] - self.canvas.canvasy(0),
            ),
            "zoom_factor": self.zoom_factor,
            "angle": self._rotation_angle - self._rotation_base_angle,
            "target": self._rotation_angle,
            "displayed": self._rotation_angle,
        }
        return True

    @staticmethod
    def _centered_layer_composite(*layers):
        """Alpha-composite layers around one fixed center, ignoring the matte."""
        layers = [layer.convert("RGBA") for layer in layers if layer is not None]
        if not layers:
            return Image.new("RGBA", (1, 1), (0, 0, 0, 0))
        width = max(layer.width for layer in layers)
        height = max(layer.height for layer in layers)
        canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        for layer in layers:
            x = round((width - layer.width) / 2)
            y = round((height - layer.height) / 2)
            canvas.alpha_composite(layer, dest=(x, y))
        return canvas

    @staticmethod
    def _center_layer(layer, size):
        layer = layer.convert("RGBA")
        if layer.size == size:
            return layer
        canvas = Image.new("RGBA", size, (0, 0, 0, 0))
        x = round((size[0] - layer.width) / 2)
        y = round((size[1] - layer.height) / 2)
        canvas.alpha_composite(layer, dest=(x, y))
        return canvas

    @staticmethod
    def _rotate_capture_layer(capture, angle):
        """Rotate a transparent foreground layer without adding a matte."""
        return capture.convert("RGBA").rotate(
            -angle, expand=True, resample=Image.Resampling.BICUBIC,
            fillcolor=(0, 0, 0, 0),
        )

    def _current_annotation_layer(self):
        """Flatten visible marks onto a transparent layer at the current angle."""
        base = self.annotation_layer
        if base is None or base.size != self.image.size:
            base = Image.new("RGBA", self.image.size, (0, 0, 0, 0))
        else:
            base = base.convert("RGBA")
        return self._composite_image(base, self.strokes)

    def _marks_in_rotation_source_space(self, marks):
        """Map current-angle marks back to the fixed crop-centered source."""
        marks = marks.convert("RGBA")
        delta = self._rotation_angle - self._rotation_base_angle
        if abs(delta % 360) > 1e-6:
            marks = marks.rotate(
                delta, expand=True, resample=Image.Resampling.BICUBIC,
                fillcolor=(0, 0, 0, 0),
            )

        bounds = marks.getchannel("A").getbbox()
        if not bounds:
            return Image.new("RGBA", self._rotation_base_image.size, (0, 0, 0, 0))

        center_x, center_y = marks.width / 2, marks.height / 2
        half_w = max(center_x - bounds[0], bounds[2] - center_x)
        half_h = max(center_y - bounds[1], bounds[3] - center_y)
        base_w, base_h = self._rotation_base_image.size
        target_size = (
            max(base_w, int(math.ceil(2 * half_w))),
            max(base_h, int(math.ceil(2 * half_h))),
        )
        result = Image.new("RGBA", target_size, (0, 0, 0, 0))
        patch = marks.crop(bounds)
        dest_x = round((target_size[0] - marks.width) / 2 + bounds[0])
        dest_y = round((target_size[1] - marks.height) / 2 + bounds[1])
        dest_x = max(0, min(target_size[0] - patch.width, dest_x))
        dest_y = max(0, min(target_size[1] - patch.height, dest_y))
        result.alpha_composite(patch, dest=(dest_x, dest_y))
        return result

    def _editor_canvas_rgb(self):
        try:
            # Use the canvas widget's actual color so transparent corners in
            # the rotation preview exactly match the surrounding editor.
            return tuple(round(channel / 257) for channel in self.canvas.winfo_rgb(self.canvas.cget("bg")))
        except Exception:
            return (23, 25, 32)

    @staticmethod
    def _compose_canvas_layers(image, annotations, background, strokes=()):
        if image is None:
            return Image.new("RGBA", (1, 1), (0, 0, 0, 0))
        image = image.convert("RGBA")
        if annotations is None or annotations.size != image.size:
            annotations = Image.new("RGBA", image.size, (0, 0, 0, 0))
        result = ChessSnip._centered_layer_composite(image, annotations)
        return ChessSnip._composite_image(result, strokes)

    def _rotation_slider_changed(self, raw_value):
        if self._rotation_slider_resetting or not self.image:
            return
        if not self._rotation_drag and self._recognition_regions:
            self._clear_recognition_regions()
        if not self._rotation_drag and not self._begin_rotation():
            return
        raw_angle = max(0, min(360, round(float(raw_value))))
        nearest_stop = max(0, min(360, round(raw_angle / 90) * 90))
        displayed = nearest_stop if abs(raw_angle - nearest_stop) <= 5 else raw_angle
        if displayed != raw_angle:
            # Move the thumb onto the 90° stop without re-entering the callback.
            self._rotation_slider_resetting = True
            try:
                self._rotation_value.set(displayed)
            finally:
                self._rotation_slider_resetting = False
        target = displayed
        self._rotation_drag["angle"] = target - self._rotation_base_angle
        self._rotation_drag["target"] = target
        self._rotation_drag["displayed"] = displayed
        self.rotation_angle_label.configure(text=f"{displayed}°")
        self._update_rotation_preview()

    def _update_rotation_preview(self):
        drag = self._rotation_drag
        if not drag:
            return
        angle = math.radians(drag["angle"])
        cos_a, sin_a = abs(math.cos(angle)), abs(math.sin(angle))
        source_marks = drag.get("annotations")
        if source_marks is None:
            source_marks = Image.new("RGBA", drag["image"].size, (0, 0, 0, 0))
        full_w = max(drag["image"].width, source_marks.width)
        full_h = max(drag["image"].height, source_marks.height)
        rotated_w = max(1, math.ceil(full_w * cos_a + full_h * sin_a))
        rotated_h = max(1, math.ceil(full_h * cos_a + full_w * sin_a))
        result_w = rotated_w
        result_h = rotated_h
        canvas_w = max(100, self.canvas.winfo_width())
        canvas_h = max(100, self.canvas.winfo_height())
        base_scale = min((canvas_w - 42) / result_w, (canvas_h - 42) / result_h, 1.0)
        max_render_scale = min(3.0, (40_000_000 / (result_w * result_h)) ** 0.5)
        target_scale = max(0.02, min(base_scale * drag["zoom_factor"], max_render_scale))
        display_w = max(1, int(result_w * target_scale))
        display_h = max(1, int(result_h * target_scale))
        preview_scale = min(1.0, math.sqrt(1_500_000 / max(1, display_w * display_h)))
        display_w = max(1, int(display_w * preview_scale))
        display_h = max(1, int(display_h * preview_scale))
        target_scale *= preview_scale
        capture_size = (
            max(1, round(drag["image"].width * target_scale)),
            max(1, round(drag["image"].height * target_scale)),
        )
        preview_capture = drag["image"].convert("RGBA").resize(
            capture_size, Image.Resampling.LANCZOS,
        ).rotate(
            -drag["angle"], expand=True, resample=Image.Resampling.BICUBIC,
            fillcolor=(0, 0, 0, 0),
        )
        marks_size = (
            max(1, round(source_marks.width * target_scale)),
            max(1, round(source_marks.height * target_scale)),
        )
        preview_marks = source_marks.convert("RGBA").resize(
            marks_size, Image.Resampling.LANCZOS,
        ).rotate(
            -drag["angle"], expand=True, resample=Image.Resampling.BICUBIC,
            fillcolor=(0, 0, 0, 0),
        )
        rotated = self._centered_layer_composite(preview_capture, preview_marks)
        display_w = max(display_w, rotated.width)
        display_h = max(display_h, rotated.height)
        display_image = Image.new(
            "RGBA", (display_w, display_h), (*self._editor_canvas_rgb(), 255),
        )
        image_x = round((display_w - rotated.width) / 2)
        image_y = round((display_h - rotated.height) / 2)
        display_image.alpha_composite(rotated, dest=(image_x, image_y))
        self._rotation_preview_photo = ImageTk.PhotoImage(display_image, master=self.canvas)
        center_x, center_y = drag["center"]
        width, height = display_image.size
        left, top = center_x - width / 2, center_y - height / 2
        right, bottom = left + width, top + height
        self.display_origin = (left, top)
        self.image_display_size = (width, height)

        if self._rotation_preview_item is None:
            self.canvas.delete("all")
            self._rotation_preview_item = self.canvas.create_image(
                center_x, center_y, image=self._rotation_preview_photo,
                anchor="center", tags="rotation-preview",
            )
            self._rotation_preview_border = self.canvas.create_rectangle(
                left, top, right, bottom, outline=EDGE, width=1,
                tags="rotation-preview-border",
            )
        else:
            self.canvas.coords(self._rotation_preview_item, center_x, center_y)
            self.canvas.itemconfigure(self._rotation_preview_item, image=self._rotation_preview_photo)
            self.canvas.coords(self._rotation_preview_border, left, top, right, bottom)
        shown_angle = round(float(self._rotation_value.get()))
        self.status.set(f"Preview rotation · {shown_angle}° · release the slider to apply · Esc to cancel")

    def _rotation_slider_release(self, _event=None):
        if self._rotation_drag and not self._rotation_release_after:
            # Let Tk's Scale class binding report its final value first.
            self._rotation_release_after = self.root.after_idle(self._finish_rotation_after_scale_event)

    def _finish_rotation_after_scale_event(self):
        self._rotation_release_after = None
        if self._rotation_drag:
            self._finish_rotation()

    def _set_rotation_value(self, value):
        displayed = max(0, min(360, int(round(value))))
        self._rotation_slider_resetting = True
        try:
            self._rotation_value.set(displayed)
            self.rotation_angle_label.configure(text=f"{displayed}°")
        finally:
            self._rotation_slider_resetting = False

    def _finish_rotation(self):
        drag = self._rotation_drag
        if not drag:
            return
        angle = drag["angle"]
        target = drag["target"]
        self._rotation_drag = None
        if self._rotation_release_after:
            try: self.root.after_cancel(self._rotation_release_after)
            except tk.TclError: pass
            self._rotation_release_after = None
        self._rotation_preview_photo = None
        self._rotation_preview_item = None
        self._rotation_preview_border = None
        changed = abs((target - self._rotation_angle + 180) % 360 - 180) >= 0.5
        if not changed:
            self._rotation_angle = target
            self._set_rotation_value(drag.get("displayed", target))
            self._render_preview()
            self.status.set("Image angle unchanged")
            return

        self._push_rotation_history()
        # Rotate the screenshot and marks from the same stable source angle.
        # Both remain transparent over the editor; exports supply the matte.
        rotated_capture = self._rotate_capture_layer(self._rotation_base_image, angle)
        source_marks = self._rotation_base_marks
        if source_marks is None:
            source_marks = Image.new("RGBA", self._rotation_base_image.size, (0, 0, 0, 0))
        rotated_marks = self._rotate_capture_layer(source_marks, angle)
        canvas_size = (max(rotated_capture.width, rotated_marks.width), max(rotated_capture.height, rotated_marks.height))
        self.image = self._center_layer(rotated_capture, canvas_size)
        self.annotation_layer = self._center_layer(rotated_marks, canvas_size)
        self.background_layer = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
        self.strokes = []
        self._rotation_angle = target
        self.zoom_factor = drag["zoom_factor"]
        self._render_preview()
        self._set_rotation_value(target)
        self.status.set(f"Rotated to {self._rotation_value.get()}° · Ctrl+Z to undo")
        self.root.after_idle(lambda screen_center=drag["screen_center"]: self._restore_rotation_view(screen_center))
        self._schedule_auto_clipboard()

    def _restore_rotation_view(self, screen_center):
        if not self.image:
            return
        canvas_width = max(1, self.canvas.winfo_width())
        canvas_height = max(1, self.canvas.winfo_height())
        center_x = self.display_origin[0] + self.image_display_size[0] / 2
        center_y = self.display_origin[1] + self.image_display_size[1] / 2
        max_x = max(0, self.scroll_size[0] - canvas_width)
        max_y = max(0, self.scroll_size[1] - canvas_height)
        left = max(0, min(max_x, center_x - screen_center[0]))
        top = max(0, min(max_y, center_y - screen_center[1]))
        self.canvas.xview_moveto(left / max(1, self.scroll_size[0]))
        self.canvas.yview_moveto(top / max(1, self.scroll_size[1]))
        self._render_canvas_only_overlay(max(100,canvas_width),max(100,canvas_height))
        self._update_size_badge()

    def _cancel_rotation(self, _event=None):
        if not self._rotation_drag:
            return None
        if self._rotation_release_after:
            try: self.root.after_cancel(self._rotation_release_after)
            except tk.TclError: pass
            self._rotation_release_after = None
        self._rotation_drag = None
        self._rotation_preview_photo = None
        self._rotation_preview_item = None
        self._rotation_preview_border = None
        self._set_rotation_value(self._rotation_angle)
        self._render_preview()
        self.status.set("Rotation cancelled")
        return "break"

    def _draw_start(self, e):
        if not self.image: return
        if self._recognition_press(e):
            return
        self._pointer_canvas_pos=(self.canvas.canvasx(e.x),self.canvas.canvasy(e.y));self._update_brush_cursor()
        x,y = self._to_image(e.x,e.y)
        if self.tool.get() == "eyedropper":
            return "break"
        if self.tool.get() == "crop":
            if not self._crop_box:
                self._crop_box = (0.0, 0.0, float(self.image.width), float(self.image.height))
            cx, cy = self.canvas.canvasx(e.x), self.canvas.canvasy(e.y)
            hit = self._crop_hit_test(cx, cy)
            self._crop_drag_fixed = None
            self._crop_drag_offset = None
            self._crop_drag_edge = None
            if hit and hit[0] == "corner":
                self._crop_drag_fixed = (hit[1], hit[2])
            elif hit and hit[0] == "edge":
                self._crop_drag_edge = hit[1]
            elif hit and hit[0] == "move":
                self._crop_drag_offset = (hit[1], hit[2])
            return
        if self._is_vector_shape(self.tool.get()):
            hit = self._shape_selection_hit(e)
            if hit:
                self._begin_shape_transform(hit[0], e, hit[1])
                return
            if self._selected_shape and self._shape_move_hit(self._selected_shape, x, y):
                self._begin_shape_transform("move", e)
                return
            target = next((stroke for stroke in reversed(self.strokes)
                           if self._is_vector_shape(stroke.get("tool"))
                           and self._shape_move_hit(stroke, x, y)), None)
            if target is not None:
                self._selected_shape = target
                self._sync_shape_fill_to_selection(target)
                self._render_shape_selection()
                self._begin_shape_transform("move", e)
                return
            if self._selected_shape is not None:
                self._selected_shape = None
                self.canvas.delete("shape-selection")
                active_tool = self.tool.get()
                active_shape = active_tool.removesuffix("_filled")
                if active_shape in ("rectangle", "circle", "line", "arrow", "cross", "tick"):
                    self._tool_variant_vars["shapes"].set(active_shape)
                    fill_enabled = active_shape in ("rectangle", "circle")
                    self.shape_fill_var.set(fill_enabled and active_tool.endswith("_filled"))
                    if self._shape_fill_checkbox is not None:
                        self._shape_fill_checkbox.configure(
                            state="normal" if fill_enabled else "disabled",
                        )
                return
        if self.tool.get()=="eraser":
            self._erasing=True;self._erase_changed=False;self._erase_at(x,y)
            return
        self._push_history()
        cx, cy = self.canvas.canvasx(e.x), self.canvas.canvasy(e.y)
        self._drawing={"points":[x,y],"canvas_points":[cx,cy],"color":self.color.get(),"width":self.width.get(),"tool":self.tool.get(),"canvas_scale":self.fit_scale,"canvas_origin":tuple(self.display_origin)}
        if self.tool.get() in ("pen", "highlighter"):
            self._refresh_active_stroke_preview()
            self._update_brush_cursor()

    def _draw_move(self,e):
        if self._recognition_motion(e):
            return
        self._pointer_canvas_pos=(self.canvas.canvasx(e.x),self.canvas.canvasy(e.y));self._update_brush_cursor()
        if self._shape_transform_drag:
            self._update_shape_transform(e)
            self._set_shape_selection_cursor(e)
            return
        if self._erasing:
            x,y=self._to_image(e.x,e.y)
            self._erase_at(x,y)
            return
        if self.tool.get() == "crop":
            self._update_crop_drag(e)
            self._set_crop_cursor(e)
            return
        if not self._drawing:return
        drawing_tool=self._drawing["tool"]
        x,y=self._to_image(e.x,e.y)
        cx, cy = self.canvas.canvasx(e.x), self.canvas.canvasy(e.y)
        if drawing_tool in ("pen", "highlighter") and e.state & 0x0001:
            y=self._drawing["points"][1]
            cy=self.display_origin[1]+y*self.fit_scale
        pts=self._drawing["points"]
        if self._is_vector_shape(drawing_tool):
            pts[:]=[pts[0],pts[1],x,y]
            self.canvas.delete("active-mark")
            self._drawing_preview_item = None
            self._drawing_preview_photo = None
            self._draw_stroke_patch(self._drawing, "active-mark")
            self._update_brush_cursor()
            return
        last_x, last_y = pts[-2:]
        canvas_points = self._drawing.setdefault("canvas_points", [])
        last_cx, last_cy = canvas_points[-2:] if len(canvas_points) >= 2 else (
            self.display_origin[0] + last_x * self.fit_scale,
            self.display_origin[1] + last_y * self.fit_scale,
        )
        minimum_step = 0.35 if drawing_tool == "highlighter" else 0.75
        if math.hypot(cx - last_cx, cy - last_cy) >= minimum_step:
            pts.extend((x, y))
            canvas_points.extend((cx, cy))
        # Coalesce highlighter mask updates to a frame-sized interval; pen stays immediate.
        if self._drawing_preview_after is None:
            if drawing_tool == "highlighter":
                self._drawing_preview_after = self.root.after(16, self._refresh_active_stroke_preview)
            else:
                self._drawing_preview_after = self.root.after_idle(self._refresh_active_stroke_preview)

    def _refresh_active_highlighter_preview(self, drawing):
        """Render translucent highlighter tiles, updating only tiles touched by new path segments."""
        tile_size = 128
        supersample = 2
        viewport_width = max(1, self.canvas.winfo_width())
        viewport_height = max(1, self.canvas.winfo_height())
        view_left = self.canvas.canvasx(0)
        view_top = self.canvas.canvasy(0)
        overlay = self._active_highlighter_overlay
        if (
            overlay is None
            or overlay["size"] != (viewport_width, viewport_height)
            or overlay["origin"] != (view_left, view_top)
        ):
            self.canvas.delete("active-mark")
            mask = Image.new(
                "L", (viewport_width * supersample, viewport_height * supersample), 0,
            )
            overlay = {
                "mask": mask,
                "draw": ImageDraw.Draw(mask),
                "photos": {},
                "origin": (view_left, view_top),
                "size": (viewport_width, viewport_height),
                "rendered_points": 1,
            }
            self._active_highlighter_overlay = overlay

        raw_points = drawing.get("canvas_points", ())
        points = [
            (float(raw_points[i]), float(raw_points[i + 1]))
            for i in range(0, len(raw_points) - 1, 2)
        ]
        if len(points) < 2:
            return

        width = max(
            1.0,
            self._stroke_render_width("highlighter", drawing["width"]) * self.fit_scale,
        )
        scaled_width = max(1, round(width * supersample))
        half_width = width / 2 + 2
        origin_x, origin_y = overlay["origin"]
        touched_tiles = set()
        first_segment = max(0, overlay["rendered_points"] - 1)
        for index in range(first_segment, len(points) - 1):
            start = points[index]
            end = points[index + 1]
            a = ((start[0] - origin_x) * supersample, (start[1] - origin_y) * supersample)
            b = ((end[0] - origin_x) * supersample, (end[1] - origin_y) * supersample)
            overlay["draw"].line(
                (a, b), fill=255, width=scaled_width, joint="curve",
            )
            radius = scaled_width / 2
            for px, py in (a, b):
                overlay["draw"].ellipse(
                    (px - radius, py - radius, px + radius, py + radius), fill=255,
                )
            left = max(0, math.floor(min(start[0], end[0]) - origin_x - half_width))
            top = max(0, math.floor(min(start[1], end[1]) - origin_y - half_width))
            right = min(viewport_width, math.ceil(max(start[0], end[0]) - origin_x + half_width))
            bottom = min(viewport_height, math.ceil(max(start[1], end[1]) - origin_y + half_width))
            if right <= left or bottom <= top:
                continue
            for tile_y in range(top // tile_size, (bottom - 1) // tile_size + 1):
                for tile_x in range(left // tile_size, (right - 1) // tile_size + 1):
                    touched_tiles.add((tile_x, tile_y))
        overlay["rendered_points"] = len(points)

        if not touched_tiles:
            return
        color = tuple(bytes.fromhex(drawing["color"].lstrip("#")))
        alpha_lut = [round(value * 128 / 255) for value in range(256)]
        for tile_x, tile_y in touched_tiles:
            left = tile_x * tile_size
            top = tile_y * tile_size
            width_px = min(tile_size, viewport_width - left)
            height_px = min(tile_size, viewport_height - top)
            mask_tile = overlay["mask"].crop((
                left * supersample,
                top * supersample,
                (left + width_px) * supersample,
                (top + height_px) * supersample,
            )).resize((width_px, height_px), Image.Resampling.BOX)
            patch = Image.new("RGBA", (width_px, height_px), (*color, 0))
            patch.putalpha(mask_tile.point(alpha_lut))
            existing = overlay["photos"].get((tile_x, tile_y))
            if existing:
                existing[0].paste(patch)
            else:
                photo = ImageTk.PhotoImage(patch, master=self.canvas)
                item = self.canvas.create_image(
                    origin_x + left, origin_y + top, image=photo, anchor="nw",
                    tags="active-mark",
                )
                overlay["photos"][(tile_x, tile_y)] = (photo, item)
        self.canvas.tag_raise("active-mark")

    def _refresh_active_stroke_preview(self, *, finish=False):
        """Update the live brush without rerasterizing its complete path."""
        self._drawing_preview_after = None
        drawing = self._drawing
        if not drawing:
            return
        tool = drawing.get("tool")
        if tool == "highlighter":
            self._drawing_preview_item = None
            self._drawing_preview_photo = None
            self._refresh_active_highlighter_preview(drawing)
            self._update_brush_cursor()
            return
        if self._is_vector_shape(tool):
            self.canvas.delete("active-mark")
            self._drawing_preview_item = None
            self._drawing_preview_photo = None
            self._draw_stroke_patch(drawing, "active-mark")
            self._update_brush_cursor()
            return

        points = list(drawing.get("canvas_points", ()))
        if len(points) < 2:
            raw = drawing.get("points", ())
            if len(raw) >= 2:
                points = [
                    self.display_origin[0] + raw[0] * self.fit_scale,
                    self.display_origin[1] + raw[1] * self.fit_scale,
                ]
        if len(points) < 4:
            self.canvas.delete("active-mark")
            self._drawing_preview_item = None
            self._drawing_preview_photo = None
            if points:
                x, y = points[:2]
                radius = max(0.5, self._stroke_render_width(tool, drawing["width"]) * self.fit_scale / 2)
                self._drawing_preview_item = self.canvas.create_oval(
                    x-radius, y-radius, x+radius, y+radius,
                    fill=drawing["color"], outline=drawing["color"], tags="active-mark",
                )
        else:
            width = max(1.0, self._stroke_render_width(tool, drawing["width"]) * self.fit_scale)
            if self._drawing_preview_item is None or self.canvas.type(self._drawing_preview_item) != "line":
                self.canvas.delete("active-mark")
                self._drawing_preview_item = self.canvas.create_line(
                    *points, fill=drawing["color"], width=width,
                    capstyle=tk.ROUND, joinstyle=tk.ROUND, smooth=False,
                    tags="active-mark",
                )
            else:
                self.canvas.coords(self._drawing_preview_item, *points)
                self.canvas.itemconfigure(
                    self._drawing_preview_item, fill=drawing["color"], width=width,
                    capstyle=tk.ROUND, joinstyle=tk.ROUND,
                )
        self._drawing_preview_photo = None
        self.canvas.tag_raise("active-mark")
        self._update_brush_cursor()

    def _rebuild_active_stroke_preview(self):
        """Reproject the active path after zooming or rebuilding the canvas."""
        drawing = self._drawing
        if not drawing:
            return
        if self._drawing_preview_after is not None:
            try:
                self.root.after_cancel(self._drawing_preview_after)
            except tk.TclError:
                pass
            self._drawing_preview_after = None
        drawing["canvas_scale"] = self.fit_scale
        drawing["canvas_origin"] = tuple(self.display_origin)
        if drawing["tool"] in ("pen", "highlighter"):
            source_points = drawing["points"]
            drawing["canvas_points"] = [
                self.display_origin[0] + source_points[i] * self.fit_scale
                if i % 2 == 0 else self.display_origin[1] + source_points[i] * self.fit_scale
                for i in range(len(source_points))
            ]
            self._refresh_active_stroke_preview()
        else:
            self.canvas.delete("active-mark")
            self._drawing_preview_item = None
            self._drawing_preview_photo = None
            if self._is_vector_shape(drawing["tool"]):
                self._draw_stroke_patch(drawing, "active-mark")

    def _draw_end(self,e):
        if self._recognition_release(e):
            return
        self._pointer_canvas_pos=(self.canvas.canvasx(e.x),self.canvas.canvasy(e.y));self._update_brush_cursor()
        if self._shape_transform_drag:
            drag = self._shape_transform_drag
            self._update_shape_transform(e)
            self._shape_transform_drag = None
            if drag["history_pushed"]:
                self._invalidate_preview_source()
                self._schedule_auto_clipboard()
            self._render_preview()
            self._set_shape_selection_cursor(e)
            return
        if self._erasing:
            self._erasing = False
            if self._erase_changed:
                self._render_preview()
                self._schedule_auto_clipboard()
            self._erase_changed = False
            return
        if self.tool.get() == "crop":
            self._update_crop_drag(e)
            self._crop_drag_fixed = None
            self._crop_drag_offset = None
            self._crop_drag_edge = None
            self._set_crop_cursor(e)
            return
        if self._drawing:
            drawing = self._drawing
            if self._drawing_preview_after is not None:
                try:self.root.after_cancel(self._drawing_preview_after)
                except tk.TclError:pass
                self._drawing_preview_after = None
            drawing_tool = drawing["tool"]
            if self._is_vector_shape(drawing_tool):
                x, y = self._to_image(e.x, e.y)
                drawing["points"][:] = [drawing["points"][0], drawing["points"][1], x, y]
            elif drawing_tool in ("pen", "highlighter"):
                x, y = self._to_image(e.x, e.y)
                cx, cy = self.canvas.canvasx(e.x), self.canvas.canvasy(e.y)
                if e.state & 0x0001:
                    y = drawing["points"][1]
                    cy = self.display_origin[1] + y * self.fit_scale
                last_x, last_y = drawing["points"][-2:]
                if math.hypot(x - last_x, y - last_y) > 0.25:
                    drawing["points"].extend((x, y))
                    drawing.setdefault("canvas_points", []).extend((cx, cy))
            if len(self._drawing["points"])<4:
                points = self._drawing["points"]
                points.extend((points[0], points[1]))
            if self._is_vector_shape(drawing_tool):
                drawing.setdefault("shape_key", id(drawing))
            drawing.pop("canvas_points", None)
            if self._stroke_extends_outside_image(drawing):
                self.canvas_only_strokes.append(self._stroke_to_canvas_space(drawing))
            self.strokes.append(drawing)
            self._selected_shape = drawing if self._is_vector_shape(drawing_tool) else None
            self._drawing = None
            self._drawing_preview_item = None
            self._drawing_preview_photo = None
            self._invalidate_preview_source()
            self._render_preview()
            self._schedule_auto_clipboard()

    def _push_history(self):
        self.history.append(self._history_snapshot())
        self.redo_history.clear()
        self._trim_history(self.history)
        self._update_history_buttons()

    def _push_rotation_history(self):
        # Undo can regenerate both foreground layers from stable sources.
        compact = self._rotation_base_image is not None and not self.strokes
        self.history.append(self._history_snapshot(compact=compact))
        self.redo_history.clear()
        self._trim_history(self.history)
        self._update_history_buttons()

    def _history_snapshot(self, compact=False):
        """Capture an editor state while keeping rotation sources immutable."""
        annotations = self.annotation_layer.copy() if self.annotation_layer is not None else None
        background = self.background_layer.copy() if self.background_layer is not None else None
        canvas_only = [dict(s, points=list(s["points"])) for s in self.canvas_only_strokes]
        image = None if compact else self.image
        strokes = [] if compact else [dict(s, points=list(s["points"])) for s in self.strokes]
        return (
            image, strokes, self._rotation_angle, self.zoom_factor,
            self._rotation_base_image, self._rotation_base_angle,
            self._rotation_base_marks, annotations, background, compact,
            canvas_only,
        )

    @staticmethod
    def _trim_history(stack):
        if len(stack) > 30:
            del stack[:-30]

    def _update_history_buttons(self):
        for button, available in (
            (self.undo_button, bool(self.history)),
            (self.redo_button, bool(self.redo_history)),
        ):
            if button is None:
                continue
            try:
                button.configure(
                    fg=TEXT if available else MUTED,
                    state=tk.NORMAL,
                    disabledforeground=MUTED,
                )
            except tk.TclError:
                pass

    def _reset_rotation_baseline(self, rebase=False):
        # Keep the original crop as the immutable rotation source. New marks
        # are mapped back into its coordinate space, including marks outside
        # the crop, so transparent rotation margins never become the new base.
        if self.image:
            marks = self._current_annotation_layer()
            if rebase:
                self._rotation_angle = 0
                self._rotation_base_image = self.image.convert("RGBA").copy()
                self._rotation_base_marks = marks.copy()
                self._rotation_base_angle = 0
            else:
                if self._rotation_base_image is None:
                    self._rotation_base_image = self.image.convert("RGBA").copy()
                    self._rotation_base_angle = self._rotation_angle
                self._rotation_base_marks = self._marks_in_rotation_source_space(marks)
            self.annotation_layer = marks
            self.strokes = []
            self.background_layer = Image.new("RGBA", self.image.size, (0, 0, 0, 0))
        self._set_rotation_value(self._rotation_angle)

    def _restore_history_snapshot(self, snapshot):
        self._clear_recognition_regions()
        self._selected_shape = None
        self._shape_transform_drag = None
        self.image,self.strokes,self._rotation_angle,self.zoom_factor,self._rotation_base_image,self._rotation_base_angle,self._rotation_base_marks,self.annotation_layer,self.background_layer,compact,self.canvas_only_strokes=snapshot
        if compact and self._rotation_base_image is not None:
            angle = self._rotation_angle - self._rotation_base_angle
            rotated_capture = self._rotate_capture_layer(
                self._rotation_base_image, angle,
            )
            source_marks = self._rotation_base_marks
            if source_marks is None:
                source_marks = Image.new("RGBA", self._rotation_base_image.size, (0, 0, 0, 0))
            rotated_marks = self._rotate_capture_layer(source_marks, angle)
            canvas_size = (max(rotated_capture.width, rotated_marks.width), max(rotated_capture.height, rotated_marks.height))
            self.image = self._center_layer(rotated_capture, canvas_size)
            self.annotation_layer = self._center_layer(rotated_marks, canvas_size)
            self.background_layer = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
        self._set_rotation_value(self._rotation_angle)
        self._render_preview()
        self._schedule_auto_clipboard()
        self._update_history_buttons()

    def undo(self):
        if not self.history:
            return
        self.redo_history.append(self._history_snapshot())
        self._trim_history(self.redo_history)
        self._restore_history_snapshot(self.history.pop())
        self.status.set("Last change undone")

    def redo(self):
        if not self.redo_history:
            return
        self.history.append(self._history_snapshot())
        self._trim_history(self.history)
        self._restore_history_snapshot(self.redo_history.pop())
        self.status.set("Last undone change restored")

    def _delete_selected_shape(self, event=None):
        """Delete the active vector mark while leaving text editing alone."""
        try:
            focus = self.root.focus_get()
            if focus is not None and focus.winfo_class() in {
                "Entry", "TEntry", "Text", "Spinbox", "TSpinbox",
                "TCombobox", "Listbox",
            }:
                return None
        except tk.TclError:
            pass

        stroke = self._selected_shape
        if (
            stroke is None
            or not self._is_vector_shape(stroke.get("tool"))
            or not any(candidate is stroke for candidate in self.strokes)
        ):
            return None

        self._push_history()
        self.strokes = [candidate for candidate in self.strokes if candidate is not stroke]
        shape_key = stroke.get("shape_key")
        if shape_key is not None:
            self.canvas_only_strokes = [
                candidate for candidate in self.canvas_only_strokes
                if candidate.get("shape_key") != shape_key
            ]
        self._selected_shape = None
        self._shape_transform_drag = None
        self.canvas.delete("shape-selection")
        self._invalidate_preview_source()
        self._render_preview()
        self._schedule_auto_clipboard()
        self.status.set("Selected shape deleted")
        return "break"

    def _composited(self):
        return self._compose_canvas_layers(
            self.image, self.annotation_layer, self.background_layer, self.strokes,
        )

    @staticmethod
    def _image_as_rgb(image, background=(255, 255, 255), return_transparency=False):
        rgba = image.convert("RGBA")
        has_transparency = rgba.getchannel("A").getextrema()[0] < 255
        if has_transparency:
            backdrop = Image.new("RGBA", rgba.size, (*background, 255))
            result = Image.alpha_composite(backdrop, rgba).convert("RGB")
        else:
            result = rgba.convert("RGB")
        if return_transparency:
            return result, has_transparency
        return result

    @staticmethod
    def _trim_transparent(image):
        """Exclude empty rotation padding from the next foreground baseline."""
        rgba = image.convert("RGBA")
        bounds = rgba.getchannel("A").getbbox()
        return rgba.crop(bounds) if bounds else rgba

    @staticmethod
    def _ellipse_patch(bounds,color,alpha,width,filled,clip_size=None):
        """Render a clean, antialiased ellipse patch for both preview and export."""
        from PIL import ImageDraw
        x1,y1,x2,y2=map(float,bounds)
        stroke=0.0 if filled else max(1.0,float(width))/2
        pad=1.0
        left0,top0=math.floor(min(x1,x2)-stroke-pad),math.floor(min(y1,y2)-stroke-pad)
        right0,bottom0=math.ceil(max(x1,x2)+stroke+pad),math.ceil(max(y1,y2)+stroke+pad)
        if clip_size:
            left=max(0,left0);top=max(0,top0)
            right=min(clip_size[0],right0);bottom=min(clip_size[1],bottom0)
            if right<=left or bottom<=top:
                return Image.new("RGBA",(1,1),(0,0,0,0)),0,0
        else:
            left,top,right,bottom=left0,top0,right0,bottom0
        if right<=left:right=left+1
        if bottom<=top:bottom=top+1
        patch_w,patch_h=right-left,bottom-top
        # Supersample locally for smooth edges, while capping temporary memory
        # when a user draws a shape over a very large multi-monitor capture.
        scale=max(1,min(4,int((16_000_000/max(1,patch_w*patch_h))**0.5)))
        mask=Image.new("L",(patch_w*scale,patch_h*scale),0)
        draw=ImageDraw.Draw(mask)
        outer=(
            (x1-left)*scale,(y1-top)*scale,
            (x2-left)*scale,(y2-top)*scale,
        )
        if filled:
            draw.ellipse(outer,fill=255)
        else:
            expand=stroke
            outer=(
                (x1-expand-left)*scale,(y1-expand-top)*scale,
                (x2+expand-left)*scale,(y2+expand-top)*scale,
            )
            draw.ellipse(outer,fill=255)
            inner=(
                (x1+expand-left)*scale,(y1+expand-top)*scale,
                (x2-expand-left)*scale,(y2-expand-top)*scale,
            )
            if inner[2]>inner[0] and inner[3]>inner[1]:
                draw.ellipse(inner,fill=0)
        if scale>1:
            mask=mask.resize((patch_w,patch_h),Image.Resampling.LANCZOS)
        rgb=tuple(bytes.fromhex(color.lstrip("#")))
        rgba=Image.new("RGBA",(patch_w,patch_h),(*rgb,0))
        rgba.putalpha(mask.point(lambda value:(value*alpha+127)//255))
        return rgba,left,top

    @staticmethod
    def _composite_image(image,strokes):
        result=image.copy().convert("RGBA")
        marks=Image.new("RGBA",result.size,(0,0,0,0))
        for s in strokes:
            patch, left, top = ChessSnip._stroke_preview_patch(
                s, scale=1.0, origin=(0.0, 0.0),
                clip_bounds=(0, 0, result.width, result.height),
            )
            if patch is not None:
                marks.alpha_composite(patch, dest=(left, top))
        output_mode = "RGBA" if image.mode == "RGBA" else "RGB"
        return Image.alpha_composite(result,marks).convert(output_mode)

    def _schedule_auto_clipboard(self):
        if not self.image:return
        self._clipboard_revision+=1
        revision=self._clipboard_revision
        if self._clipboard_after:
            try:self.root.after_cancel(self._clipboard_after)
            except tk.TclError:pass
        self._clipboard_after=self.root.after(300,lambda:self._start_auto_clipboard(revision))

    def _start_auto_clipboard(self,revision):
        self._clipboard_after=None
        if not self.image or self._clipboard_busy:return
        self._clipboard_busy=True
        image=self.image
        annotations=self.annotation_layer
        background=self.background_layer
        strokes=list(self.strokes)
        def render_and_copy():
            handle=None
            try:
                composed=ChessSnip._compose_canvas_layers(image,annotations,background,strokes)
                buffer=io.BytesIO();self._image_as_rgb(composed).save(buffer,"BMP")
                data=buffer.getvalue()[14:]
                handle=self._prepare_clipboard_dib(data)
                self.root.after(0,lambda:self._finish_auto_clipboard(revision,handle,None))
            except Exception as exc:
                if handle:self._free_clipboard_handle(handle)
                try:self.root.after(0,lambda err=exc:self._finish_auto_clipboard(revision,None,err))
                except Exception:pass
        threading.Thread(target=render_and_copy,daemon=True).start()

    def _finish_auto_clipboard(self,revision,handle,error):
        self._clipboard_busy=False
        if revision!=self._clipboard_revision:
            if handle:self._free_clipboard_handle(handle)
            self._start_auto_clipboard(self._clipboard_revision)
            return
        if error:
            self.status.set("Could not update clipboard: "+str(error));return
        try:
            self._put_clipboard_handle(handle)
            self.status.set("Image update copied to clipboard automatically")
        except Exception as exc:messagebox.showerror("Couldn't update clipboard",str(exc),parent=self.root)

    def save_as(self):
        if not self.image: self.status.set("Take a snip first to save an image"); return
        path=filedialog.asksaveasfilename(parent=self.root,title="Save snip",initialdir=str(ROOT/"Pictures"),initialfile="ChessSnip_"+datetime.now().strftime("%Y%m%d_%H%M%S")+".png",defaultextension=".png",filetypes=(("PNG image","*.png"),("JPEG image","*.jpg *.jpeg"),("Bitmap image","*.bmp"),("All files","*.*")))
        if not path:return
        try:
            img=self._composited(); ext=Path(path).suffix.lower()
            if ext in (".jpg",".jpeg"):self._image_as_rgb(img).save(path,quality=95)
            elif ext==".bmp":self._image_as_rgb(img).save(path)
            else:self._image_as_rgb(img).save(path)
            self.status.set("Saved  ·  "+path)
        except Exception as e:messagebox.showerror("Couldn't save image",str(e),parent=self.root)

    def copy_image(self):
        if not self.image:self.status.set("Take a snip first to copy an image");return
        try:
            bmp=io.BytesIO();self._image_as_rgb(self._composited()).save(bmp,"BMP"); data=bmp.getvalue()[14:]
            self._set_clipboard_dib(data)
            self.status.set("Image copied to clipboard  ·  paste anywhere")
        except Exception as e:messagebox.showerror("Couldn't copy image",str(e),parent=self.root)

    def _recognition_tool_clicked(self):
        if self._recognition_busy:
            return
        if self._recognition_mode and self._recognition_regions:
            self._clear_recognition_regions()
            self._render_preview()
            self.status.set("Text and QR selection mode closed · ready to draw")
            return
        self.recognize_content()

    def recognize_content(self):
        """Recognize text and QR codes locally, then show their hit areas."""
        if not self.image:
            self.status.set("Take a snip first to recognize text and QR codes")
            return
        if self._recognition_busy:
            return
        self._recognition_busy = True
        self._recognition_mode = False
        self._start_recognition_animation()
        self.recognition_button.configure(state=tk.DISABLED)
        self._style_mode_buttons()
        source_image_id = id(self.image)
        self._clear_recognition_regions()
        image = self._image_as_rgb(self._composited())
        self.status.set("Recognizing text and QR codes on this PC…")

        def worker():
            text_content = ""
            text_regions = []
            text_error = None
            qr_values = []
            qr_regions = []
            qr_error = None

            def clipped_region(kind, box, text="", line=0):
                left, top, right, bottom = box
                left = max(0.0, min(float(image.width), float(left)))
                top = max(0.0, min(float(image.height), float(top)))
                right = max(0.0, min(float(image.width), float(right)))
                bottom = max(0.0, min(float(image.height), float(bottom)))
                if right - left >= 1 and bottom - top >= 1:
                    return {
                        "kind": kind,
                        "box": (left, top, right, bottom),
                        "text": str(text or ""),
                        "line": line,
                    }
                return None

            try:
                import asyncio
                import importlib
                from PIL import ImageOps

                def prepare_text_images(maximum_dimension=2000):
                    working_image = image.copy().convert("RGB")
                    source_width, source_height = working_image.size
                    limits = [4.0, 160.0 / min(source_width, source_height)]
                    if maximum_dimension > 0:
                        limits.append(maximum_dimension / max(source_width, source_height))
                    scale = min(limits)
                    if scale > 1.0:
                        working_image = working_image.resize(
                            (max(1, round(source_width * scale)), max(1, round(source_height * scale))),
                            Image.Resampling.LANCZOS,
                        )
                    scale_x = working_image.width / source_width
                    scale_y = working_image.height / source_height

                    padding = max(8, min(24, min(working_image.size) // 16))
                    if maximum_dimension > 0:
                        padding = min(
                            padding,
                            max(0, (maximum_dimension - max(working_image.size)) // 2),
                        )
                    if padding:
                        working_image = ImageOps.expand(
                            working_image, border=padding, fill="white",
                        )
                    transform = (scale_x, scale_y, padding)
                    return ((working_image, transform),)

                def map_box(box, transform, text="", line=0):
                    scale_x, scale_y, padding = transform
                    left, top, right, bottom = box
                    return clipped_region("text", (
                        (left - padding) / scale_x,
                        (top - padding) / scale_y,
                        (right - padding) / scale_x,
                        (bottom - padding) / scale_y,
                    ), text, line)

                BitmapDecoder = importlib.import_module("winrt.windows.graphics.imaging").BitmapDecoder
                OcrEngine = importlib.import_module("winrt.windows.media.ocr").OcrEngine
                streams = importlib.import_module("winrt.windows.storage.streams")
                DataWriter = streams.DataWriter
                InMemoryRandomAccessStream = streams.InMemoryRandomAccessStream

                async def recognize_with_windows_ocr():
                    engine = OcrEngine.try_create_from_user_profile_languages()
                    if engine is None:
                        raise RuntimeError(
                            "Windows OCR isn't available for the languages installed on this PC. "
                            "Add an OCR language in Windows Settings and try again."
                        )
                    # This experiment makes exactly one WinRT OCR call on the
                    # resized, padded source image. RapidOCR and enhancement
                    # retries are intentionally disabled for comparison.
                    candidate, transform = prepare_text_images(
                        int(OcrEngine.max_image_dimension),
                    )[0]
                    encoded = io.BytesIO()
                    candidate.save(encoded, format="PNG")
                    stream = InMemoryRandomAccessStream()
                    writer = DataWriter(stream.get_output_stream_at(0))
                    writer.write_bytes(encoded.getvalue())
                    await writer.store_async()
                    stream.seek(0)
                    decoder = await BitmapDecoder.create_async(stream)
                    bitmap = await decoder.get_software_bitmap_async()
                    result = await engine.recognize_async(bitmap)
                    recognized_text = "\n".join(line.text for line in result.lines).strip()
                    regions = []
                    for line_index, line in enumerate(result.lines):
                        words = list(getattr(line, "words", ()) or ())
                        for word in words:
                            rect = getattr(word, "bounding_rect", None)
                            if rect is None:
                                continue
                            try:
                                box = (
                                    float(rect.x), float(rect.y),
                                    float(rect.x + rect.width), float(rect.y + rect.height),
                                )
                                word_text = str(getattr(word, "text", "") or "")
                                if not word_text and len(words) == 1:
                                    word_text = str(getattr(line, "text", "") or "")
                                mapped = map_box(box, transform, word_text, line_index)
                                if mapped:
                                    regions.append(mapped)
                            except (AttributeError, TypeError, ValueError):
                                continue
                        if not words:
                            rect = getattr(line, "bounding_rect", None)
                            if rect is not None:
                                try:
                                    mapped = map_box((
                                        float(rect.x), float(rect.y),
                                        float(rect.x + rect.width), float(rect.y + rect.height),
                                    ), transform, str(getattr(line, "text", "") or ""), line_index)
                                    if mapped:
                                        regions.append(mapped)
                                except (AttributeError, TypeError, ValueError):
                                    continue
                    return recognized_text, regions

                try:
                    windows_text, windows_regions = asyncio.run(recognize_with_windows_ocr())
                except Exception as exc:
                    windows_text, windows_regions = "", []
                    text_error = str(exc)
                text_content, text_regions = windows_text, windows_regions
            except Exception as exc:
                text_error = str(exc)

            try:
                import zxingcpp
                barcodes = zxingcpp.read_barcodes(
                    image, formats=zxingcpp.BarcodeFormat.QRCode,
                )
                seen = set()
                for barcode_index, barcode in enumerate(barcodes):
                    value = barcode.text or ""
                    if not value and getattr(barcode, "bytes", b""):
                        value = bytes(barcode.bytes).decode("utf-8", errors="replace")
                    if value and value not in seen:
                        qr_values.append(value)
                        seen.add(value)
                    position = getattr(barcode, "position", None)
                    if position is not None:
                        try:
                            points = [getattr(position, corner) for corner in (
                                "top_left", "top_right", "bottom_left", "bottom_right",
                            )]
                            xs = [float(point.x) for point in points]
                            ys = [float(point.y) for point in points]
                            region = clipped_region(
                                "qr", (min(xs), min(ys), max(xs), max(ys)),
                                value, barcode_index,
                            )
                            if region:
                                qr_regions.append(region)
                        except (AttributeError, TypeError, ValueError):
                            continue
            except Exception as exc:
                qr_error = str(exc)

            try:
                self.root.after(
                    0,
                    lambda result=(text_content, text_regions, text_error, qr_values, qr_regions, qr_error), image_id=source_image_id:
                        self._finish_content_recognition(result, image_id),
                )
            except tk.TclError:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _finish_content_recognition(self, result, source_image_id):
        self._recognition_busy = False
        self._stop_recognition_animation()
        try:
            self.recognition_button.configure(state=tk.NORMAL)
        except tk.TclError:
            return
        if not self.image or id(self.image) != source_image_id:
            self.status.set("The snip changed during recognition · run it again")
            return
        text_content, text_regions, text_error, qr_values, qr_regions, qr_error = result
        self._recognition_regions = list(text_regions) + list(qr_regions)
        self._recognition_selection = []
        self._recognition_drag = None
        self._recognition_last_text_click = None
        self._recognition_mode = bool(self._recognition_regions)
        self._style_mode_buttons()
        self._render_preview()

        text_count = len(text_regions)
        qr_count = len(qr_regions) if qr_regions else len(qr_values)
        if self._recognition_regions:
            self.canvas.focus_set()
        if text_error or qr_error:
            messages = []
            if text_error:
                messages.append(f"Windows OCR: {str(text_error).replace(chr(10), ' ')[:110]}")
            elif text_content and not text_regions:
                messages.append("Text was found, but Windows OCR supplied no selectable word positions")
            if qr_error:
                messages.append(f"QR scan: {str(qr_error).replace(chr(10), ' ')[:110]}")
            if text_regions:
                messages.append(f"{text_count} text word{'s' if text_count != 1 else ''} ready to select")
            if qr_count:
                messages.append(f"{qr_count} QR code{'s' if qr_count != 1 else ''} ready to select")
            self.status.set(" · ".join(messages))
        elif not self._recognition_regions:
            self.status.set("No text or QR code was found")
        else:
            text_message = (
                f"{text_count} text word{'s' if text_count != 1 else ''} ready to select"
                if text_count else "No text found"
            )
            qr_message = (
                f"{qr_count} QR code{'s' if qr_count != 1 else ''} ready to select"
                if qr_count else "No QR code found"
            )
            self.status.set(
                f"{text_message} · {qr_message} · double-click text to select; click a QR icon to copy"
            )

    def chess_to_pgn(self):
        if not self.image:
            self.status.set("Take a snip of a chess position first")
            return
        self._recognize_chess_async(self._image_as_rgb(self._composited()), board_crop=False)

    def _recognize_chess_async(self, image, board_crop):
        if str(self.chess_button.cget("state")) == str(tk.DISABLED):
            return
        self.chess_button.configure(state=tk.DISABLED)
        self.status.set("Recognizing the chess position on this PC…")
        image_size = getattr(image, "size", None)
        _write_crash_log_message(
            f"Chess recognition started; board_crop={board_crop}; image_size={image_size}"
        )

        def worker():
            recognition_error_type = None
            try:
                # Keep NumPy and ONNX Runtime out of the normal app startup path.
                from chess_recognition import ChessRecognitionError, recognize_position
                recognition_error_type = ChessRecognitionError
                result = recognize_position(image, board_crop=board_crop)
                error = None
                _write_crash_log_message("Chess recognition completed successfully")
            except Exception as exc:
                result, error = None, exc
                _write_crash_log_exception(
                    f"Chess recognition failed; board_crop={board_crop}; image_size={image_size}",
                    type(exc), exc, exc.__traceback__,
                )
            is_recognition_error = bool(
                error is not None
                and recognition_error_type is not None
                and isinstance(error, recognition_error_type)
            )
            try:
                self.root.after(0, lambda r=result, err=error, chess_error=is_recognition_error: self._finish_chess_recognition(image, board_crop, r, err, chess_error))
            except tk.TclError:
                pass

        threading.Thread(
            target=worker, name="ChessSnipChessRecognition", daemon=True
        ).start()

    def _finish_chess_recognition(self, image, board_crop, result, error, is_recognition_error):
        self.chess_button.configure(state=tk.NORMAL)
        if error:
            # Don't leave an older position visible as if it came from the
            # current image after the replacement recognition has failed.
            self._close_chess_reviews_for_capture()
            if is_recognition_error and not board_crop:
                if messagebox.askyesno("Select the board", str(error) + "\n\nWould you like to mark the board area yourself?", parent=self.root):
                    self._choose_chess_board(image)
                    return
            messagebox.showerror("Chess recognition", str(error), parent=self.root)
            self.status.set("Chess position wasn't recognized")
            return
        self.status.set(f"Chess position recognized  ·  {result['confidence']:.0%} average confidence  ·  review it before exporting")
        self._show_chess_review(result)

    def _choose_chess_board(self, image):
        window = tk.Toplevel(self.root)
        window.title("Select chessboard")
        window.configure(bg=INK)
        window.transient(self.root)
        maximum_width = max(480, min(1040, self.root.winfo_screenwidth() - 80))
        maximum_height = max(420, min(790, self.root.winfo_screenheight() - 100))
        scale = min((maximum_width - 36) / image.width, (maximum_height - 132) / image.height, 1.0)
        display = image.resize((max(1, int(image.width * scale)), max(1, int(image.height * scale))), Image.Resampling.LANCZOS)
        canvas_width, canvas_height = display.size
        window.geometry(f"{canvas_width + 32}x{canvas_height + 126}")
        self._position_window_at_main(window)
        tk.Label(window, text="Drag around the chessboard, including all 64 squares.", bg=INK, fg=TEXT, font=("Segoe UI Semibold", 11)).pack(anchor="w", padx=16, pady=(13, 9))
        canvas = tk.Canvas(window, width=canvas_width, height=canvas_height, bg="#171920", highlightthickness=1, highlightbackground=EDGE, cursor="crosshair")
        canvas.pack(padx=16)
        self._add_tooltip(canvas, "Drag to mark the outer edges of the 64-square chessboard")
        photo = ImageTk.PhotoImage(display)
        canvas.create_image(0, 0, image=photo, anchor="nw")
        canvas.image_ref = photo
        selection = {"start": None, "box": None}

        def on_down(event):
            x, y = max(0, min(canvas_width, event.x)), max(0, min(canvas_height, event.y))
            selection["start"] = (x, y)
            selection["box"] = None
            canvas.delete("selection")

        def on_drag(event):
            if selection["start"] is None:
                return
            x0, y0 = selection["start"]
            x1, y1 = max(0, min(canvas_width, event.x)), max(0, min(canvas_height, event.y))
            selection["box"] = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
            canvas.delete("selection")
            canvas.create_rectangle(*selection["box"], outline="#aaa2ff", width=2, tags="selection")

        def use_selection(_event=None):
            bounds = selection["box"]
            if not bounds or bounds[2] - bounds[0] < 24 or bounds[3] - bounds[1] < 24:
                messagebox.showinfo("Select the board", "Drag a rectangle around the chessboard first.", parent=window)
                return
            left, top, right, bottom = (int(round(value / scale)) for value in bounds)
            crop = image.crop((left, top, right, bottom))
            if abs(crop.width - crop.height) / max(crop.width, crop.height) > 0.16:
                messagebox.showerror("Board should be square", "Select the board's outer square edges and try again.", parent=window)
                return
            window.grab_release()
            window.destroy()
            self._recognize_chess_async(crop, board_crop=True)

        canvas.bind("<ButtonPress-1>", on_down)
        canvas.bind("<B1-Motion>", on_drag)
        recognize_button = tk.Button(window, text="Recognize selected board", command=use_selection, bg=ACCENT, fg="white", activebackground="#7770ea", relief="flat", bd=0, padx=14, pady=7, cursor="hand2")
        recognize_button.pack(anchor="e", padx=16, pady=12)
        self._add_tooltip(recognize_button, "Run offline piece recognition on the selected board")
        window.bind("<Return>", use_selection)
        window.bind("<Escape>", lambda _e: window.destroy())
        window.grab_set()
        canvas.focus_set()

    @staticmethod
    def _compress_fen_rows(rows):
        compressed = []
        for row in rows:
            result, empty = [], 0
            for piece in row:
                if piece == "1":
                    empty += 1
                else:
                    if empty:
                        result.append(str(empty)); empty = 0
                    result.append(piece)
            if empty:
                result.append(str(empty))
            compressed.append("".join(result))
        return "/".join(compressed)

    def _show_chess_review(self, recognition):
        window = None
        for candidate in reversed(self._chess_review_windows):
            try:
                if candidate.winfo_exists():
                    window = candidate
                    break
            except tk.TclError:
                continue
        if window is None:
            window = tk.Toplevel(self.root)
            self._chess_review_windows.append(window)
            window.bind("<Destroy>", lambda event, w=window: self._forget_chess_review_window(w) if event.widget is w else None, add="+")
        else:
            # Reuse the native window so a second Chess → PGN action refreshes
            # its contents instead of stacking another review window.
            for old_child in window.winfo_children():
                try:
                    old_child.destroy()
                except tk.TclError:
                    pass
            try:
                window.attributes("-topmost", False)
            except tk.TclError:
                pass
        window.overrideredirect(True)
        window.title("Chess position → PGN")
        window.configure(bg=INK, bd=0, relief="flat")
        if self._app_icon_photos:
            try:
                window.iconphoto(True, *self._app_icon_photos)
            except tk.TclError:
                pass
        screen_width, screen_height = window.winfo_screenwidth(), window.winfo_screenheight()
        review_width = min(1060, max(860, screen_width - 120))
        review_height = min(840, max(760, screen_height - 80))
        window.geometry(f"{review_width}x{review_height}")
        self._position_window_at_main(window)
        window.minsize(min(960, review_width), min(780, review_height))
        window.protocol("WM_DELETE_WINDOW", window.destroy)

        # Match the main app's custom titlebar, window controls, and rounded frame.
        titlebar = tk.Frame(window, bg=PANEL, height=40, highlightbackground=EDGE, highlightthickness=1)
        titlebar.pack(fill="x")
        brand = tk.Frame(titlebar, bg=PANEL)
        brand.pack(side="left", padx=(14, 0))
        tk.Label(brand, text="♟", bg=ACCENT, fg="white", font=("Segoe UI Symbol", 12, "bold"), width=2, pady=1).pack(side="left", padx=(0, 9), pady=4)
        tk.Label(brand, text="ChessSnip  ·  Chess → PGN", bg=PANEL, fg=TEXT, font=("Segoe UI Semibold", 10)).pack(side="left")
        title_controls = tk.Frame(titlebar, bg=PANEL, width=138, height=40)
        title_controls.pack(side="right", fill="y")
        title_controls.pack_propagate(False)

        def minimize_review():
            try:
                user32 = ctypes.windll.user32
                user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
                user32.GetAncestor.restype = wintypes.HWND
                hwnd = user32.GetAncestor(window.winfo_id(), 2) or window.winfo_id()
                user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
                user32.ShowWindow.restype = wintypes.BOOL
                user32.ShowWindow(hwnd, 6)  # SW_MINIMIZE
            except Exception:
                window.iconify()

        minimize_button = tk.Button(title_controls, text="—", command=minimize_review, bg=PANEL, fg=TEXT, activebackground=PANEL2, activeforeground=TEXT, relief="flat", bd=0, width=5, cursor="hand2", font=("Segoe UI", 11))
        minimize_button.pack(side="left", fill="y")
        minimize_button._chesssnip_aa_fixed_size = (46, 38)
        self._bind_title_button_hover(minimize_button)
        self._add_tooltip(minimize_button, "Minimize the chess review")
        review_maximize = {"maximized": False, "restore_geometry": None}
        maximize_button = tk.Button(title_controls, text="□", bg=PANEL, fg=TEXT, activebackground=PANEL2, activeforeground=TEXT, relief="flat", bd=0, width=5, cursor="hand2", font=("Segoe UI", 11))
        maximize_button.pack(side="left", fill="y")
        maximize_button._chesssnip_aa_fixed_size = (46, 38)
        self._bind_title_button_hover(maximize_button)
        self._add_tooltip(maximize_button, "Maximize or restore the chess review")

        def toggle_review_maximize():
            if review_maximize["maximized"]:
                if review_maximize["restore_geometry"]:
                    window.geometry(review_maximize["restore_geometry"])
                review_maximize["maximized"] = False
                maximize_button.configure(text="□")
                return
            review_maximize["restore_geometry"] = window.geometry()
            work_area = wintypes.RECT()
            spi = ctypes.windll.user32.SystemParametersInfoW
            spi.argtypes = (wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT)
            spi.restype = wintypes.BOOL
            if spi(0x0030, 0, ctypes.byref(work_area), 0):
                window.geometry(f"{work_area.right - work_area.left}x{work_area.bottom - work_area.top}+{work_area.left}+{work_area.top}")
            else:
                window.state("zoomed")
            review_maximize["maximized"] = True
            maximize_button.configure(text="❐")

        maximize_button.configure(command=toggle_review_maximize)
        close_button = tk.Button(title_controls, text="×", command=window.destroy, bg=PANEL, fg=TEXT, activebackground="#c42b1c", activeforeground="white", relief="flat", bd=0, width=5, cursor="hand2", font=("Segoe UI", 12))
        close_button.pack(side="left", fill="y")
        close_button._chesssnip_aa_fixed_size = (46, 38)
        self._bind_title_button_hover(close_button, "#c42b1c", "white")
        self._add_tooltip(close_button, "Close the chess review")

        def activate_review_window():
            try:
                user32 = ctypes.windll.user32
                user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
                user32.GetAncestor.restype = wintypes.HWND
                hwnd = user32.GetAncestor(window.winfo_id(), 2) or window.winfo_id()
                user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
                user32.ShowWindow.restype = wintypes.BOOL
                user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
                user32.SetForegroundWindow.restype = wintypes.BOOL
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                user32.SetForegroundWindow(hwnd)
                window.lift()
                window.focus_force()
            except Exception:
                try:
                    window.lift()
                    window.focus_force()
                except tk.TclError:
                    pass

        drag_origin = {}
        def start_review_drag(event):
            activate_review_window()
            drag_origin.update(pointer=(event.x_root, event.y_root), position=(window.winfo_x(), window.winfo_y()))
        def move_review_drag(event):
            if not drag_origin or review_maximize["maximized"]:
                return
            px, py = drag_origin["pointer"]
            wx, wy = drag_origin["position"]
            try:
                user32 = ctypes.windll.user32
                user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
                user32.GetAncestor.restype = wintypes.HWND
                hwnd = user32.GetAncestor(window.winfo_id(), 2) or window.winfo_id()
                user32.SetWindowPos.argtypes = (wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT)
                user32.SetWindowPos.restype = wintypes.BOOL
                user32.SetWindowPos(hwnd, None, int(wx + event.x_root - px), int(wy + event.y_root - py), 0, 0, 0x0015)  # NOSIZE | NOZORDER | NOACTIVATE
            except Exception:
                window.geometry(f"+{wx + event.x_root - px}+{wy + event.y_root - py}")
        for target in (titlebar, brand, *brand.winfo_children()):
            target.bind("<ButtonPress-1>", start_review_drag)
            target.bind("<B1-Motion>", move_review_drag)
            target.bind("<Double-Button-1>", lambda _event: toggle_review_maximize())
        window.bind("<Escape>", lambda _event: window.destroy())

        def apply_review_chrome():
            try:
                user32 = ctypes.windll.user32
                user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
                user32.GetAncestor.restype = wintypes.HWND
                hwnd = user32.GetAncestor(window.winfo_id(), 2) or window.winfo_id()
                get_style, set_style = user32.GetWindowLongPtrW, user32.SetWindowLongPtrW
                get_style.argtypes = (wintypes.HWND, ctypes.c_int); get_style.restype = ctypes.c_ssize_t
                set_style.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t); set_style.restype = ctypes.c_ssize_t
                style = get_style(hwnd, -16)
                style = (style & ~0x00C40000) | 0x00040000 | 0x00020000 | 0x00010000 | 0x00080000
                set_style(hwnd, -16, style)
                ex_style = get_style(hwnd, -20)
                set_style(hwnd, -20, (ex_style | 0x00040000) & ~(0x00000080 | 0x00000100 | 0x00000200))  # Show a taskbar button, remove tool/window edges
                user32.SetWindowPos.argtypes = (wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT)
                user32.SetWindowPos.restype = wintypes.BOOL
                user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0027)
                _set_window_app_user_model_id(hwnd, "ChessSnip.Desktop")
                preference = ctypes.c_int(2)  # DWMWCP_ROUND
                dwm = ctypes.windll.dwmapi.DwmSetWindowAttribute
                dwm.argtypes = (wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD)
                dwm.restype = ctypes.c_long
                dwm(hwnd, 33, ctypes.byref(preference), ctypes.sizeof(preference))
                border_color = wintypes.DWORD(_colorref(INK))
                dwm(hwnd, 34, ctypes.byref(border_color), ctypes.sizeof(border_color))  # DWMWA_BORDER_COLOR
                caption_color = wintypes.DWORD(_colorref(INK))
                dwm(hwnd, 35, ctypes.byref(caption_color), ctypes.sizeof(caption_color))  # DWMWA_CAPTION_COLOR
            except Exception:
                pass
        window.after(100, apply_review_chrome)

        tk.Label(window, text="Review the detected position", bg=INK, fg=TEXT, font=("Segoe UI Semibold", 16)).pack(anchor="w", padx=24, pady=(18, 3))
        body = tk.Frame(window, bg=INK)
        body.pack(fill="x", padx=20)
        left = tk.Frame(body, bg=INK)
        left.pack(side="left", padx=(0, 28))
        tk.Label(left, text="BOARD IMAGE · CAPTURED VIEW", bg=INK, fg=MUTED, font=("Segoe UI", 8, "bold")).pack(anchor="w", pady=(0, 6))
        board_size = max(320, min(336, ((review_width - 72) // 2 // 8) * 8))
        cell = board_size // 8
        source_canvas = tk.Canvas(left, width=board_size, height=board_size, bg=PANEL, highlightthickness=1, highlightbackground=EDGE)
        source_canvas.pack()
        self._add_tooltip(source_canvas, "Detected board image · this image stays in its captured orientation")
        # The source screenshot stays in its captured orientation. The flip
        # control only changes the recognized-piece preview; FEN rows remain
        # normalized for analysis and export.
        source_view = {"pieces_flipped": bool(recognition.get("orientation_flipped")), "photo": None}

        def draw_source_board():
            source_image = recognition["board"]
            source_photo = ImageTk.PhotoImage(source_image.resize((board_size, board_size), Image.Resampling.LANCZOS))
            source_canvas.delete("all")
            source_canvas.create_image(0, 0, image=source_photo, anchor="nw")
            source_canvas.image_ref = source_photo
            source_view["photo"] = source_photo

        draw_source_board()

        right = tk.Frame(body, bg=INK)
        right.pack(side="left")
        right_header = tk.Frame(right, bg=INK)
        right_header.pack(fill="x", pady=(0, 6))
        tk.Label(right_header, text="RECOGNIZED PIECES", bg=INK, fg=MUTED, font=("Segoe UI", 8, "bold")).pack(side="left")
        board_edit_controls = tk.Frame(right_header, bg=INK)
        board_edit_controls.pack(side="left", padx=(8, 0))
        size = board_size
        piece_font_size = max(24, int(cell * .72))
        coordinate_margin = 18
        board_canvas = tk.Canvas(right, width=size + coordinate_margin, height=size + coordinate_margin, bg=INK, highlightthickness=0)
        board_canvas.pack(anchor="w")
        self._add_tooltip(board_canvas, "Drag a piece or click it then an empty square to move it · right-click a square to place or remove a piece")
        rows = list(recognition["rows"])
        coordinate_orientation = tk.StringVar(value="black" if recognition.get("orientation_flipped") else "white")
        board_flip_view = {"labels_flipped": False}
        glyphs = {"K":"♔", "Q":"♕", "R":"♖", "B":"♗", "N":"♘", "P":"♙", "k":"♚", "q":"♛", "r":"♜", "b":"♝", "n":"♞", "p":"♟"}
        turn = tk.StringVar(value="w")

        def infer_castling_rights():
            return {
                "K": rows[7][4] == "K" and rows[7][7] == "R",
                "Q": rows[7][4] == "K" and rows[7][0] == "R",
                "k": rows[0][4] == "k" and rows[0][7] == "r",
                "q": rows[0][4] == "k" and rows[0][0] == "r",
            }

        possible_rights = infer_castling_rights()
        castle_vars = {right: tk.BooleanVar(value=possible_rights[right]) for right in "KQkq"}
        manually_set_castling = set()

        def refresh_castling_suggestions(force=False):
            suggestions = infer_castling_rights()
            if force:
                manually_set_castling.clear()
            for right in "KQkq":
                if force or right not in manually_set_castling:
                    castle_vars[right].set(suggestions[right])

        position_history = []
        position_history_index = 0
        position_history_buttons = {"undo": None, "redo": None, "reset": None}
        reset_position_icons = {}
        reset_position_icon_art = {}

        def position_state():
            return (
                tuple(rows),
                tuple(castle_vars[right].get() for right in "KQkq"),
            )

        captured_position = position_state()
        position_history.append(captured_position)

        def update_position_history_buttons():
            available = {
                "undo": position_history_index > 0,
                "redo": position_history_index + 1 < len(position_history),
                "reset": position_state() != captured_position,
            }
            for name, enabled in available.items():
                button = position_history_buttons.get(name)
                if button is not None:
                    try:
                        if button.winfo_exists():
                            # Tk stipples disabled button contents, producing
                            # a mesh over glyphs and images. Keep the control
                            # normal and visually mute it when it cannot act.
                            face = PANEL2 if enabled and name != "reset" else INK
                            icon_color = MUTED if enabled else EDGE
                            button.configure(
                                state=tk.NORMAL, bg=face, activebackground=face,
                                fg=icon_color, activeforeground=icon_color,
                            )
                            button._position_action_available = enabled
                            if name == "reset":
                                set_reset_position_button_icon(icon_color)
                    except tk.TclError:
                        pass

        def record_position_edit():
            nonlocal position_history_index
            state = position_state()
            if state == position_history[position_history_index]:
                update_position_history_buttons()
                return
            del position_history[position_history_index + 1:]
            position_history.append(state)
            position_history_index = len(position_history) - 1
            update_position_history_buttons()

        def apply_position_state(state):
            saved_rows, saved_rights = state
            rows[:] = list(saved_rows)
            suggestions = infer_castling_rights()
            manually_set_castling.clear()
            for right, enabled in zip("KQkq", saved_rights):
                castle_vars[right].set(enabled)
                if enabled != suggestions[right]:
                    manually_set_castling.add(right)
            selected_square["position"] = None
            draw_recognized_board()
            update_position_history_buttons()

        def undo_position_edit():
            nonlocal position_history_index
            if position_history_index <= 0:
                return
            position_history_index -= 1
            apply_position_state(position_history[position_history_index])

        def redo_position_edit():
            nonlocal position_history_index
            if position_history_index + 1 >= len(position_history):
                return
            position_history_index += 1
            apply_position_state(position_history[position_history_index])

        def reset_position():
            nonlocal position_history_index
            # Restore establishes a fresh baseline: previous edits cannot be
            # replayed with Undo, and there are no stale Redo entries.
            position_history[:] = [captured_position]
            position_history_index = 0
            apply_position_state(captured_position)

        halfmove = tk.StringVar(value="0")
        fullmove = tk.StringVar(value="1")
        fen_var = tk.StringVar()
        def current_fen():
            rights = "".join(key for key in "KQkq" if castle_vars[key].get()) or "-"
            half_count, move_number = int(halfmove.get()), int(fullmove.get())
            if half_count < 0 or move_number < 1:
                raise ValueError("Half-moves must be zero or more, and the move number must be at least 1.")
            # The visible pieces and the coordinate labels can be flipped
            # independently. Rotate the FEN rows only when their orientations
            # differ, so the notation matches the squares currently labeled.
            coordinates_flipped = (
                (coordinate_orientation.get() == "black")
                ^ board_flip_view["labels_flipped"]
            )
            fen_rows = rows
            if bool(source_view["pieces_flipped"]) != coordinates_flipped:
                fen_rows = [row[::-1] for row in reversed(rows)]
            return f"{self._compress_fen_rows(fen_rows)} {turn.get()} {rights} - {half_count} {move_number}"

        def refresh_fen_display():
            try:
                fen_var.set(current_fen())
            except (ValueError, tk.TclError):
                fen_var.set("Adjust the move counters to valid numbers")

        selected_square = {"position": None}

        def draw_board_coordinates():
            """Replace only coordinate labels, leaving the board art untouched."""
            board_canvas.delete("board-coordinate")
            board_left = coordinate_margin
            coordinates_flipped = (
                (coordinate_orientation.get() == "black")
                ^ board_flip_view["labels_flipped"]
            )
            file_labels = "hgfedcba" if coordinates_flipped else "abcdefgh"
            rank_labels = "12345678" if coordinates_flipped else "87654321"
            for index, label in enumerate(file_labels):
                board_canvas.create_text(
                    board_left + index * cell + cell / 2,
                    size + coordinate_margin / 2,
                    text=label,
                    fill=MUTED,
                    font=("Segoe UI", 8, "bold"),
                    tags=("board-coordinate",),
                )
            for index, label in enumerate(rank_labels):
                board_canvas.create_text(
                    coordinate_margin / 2,
                    index * cell + cell / 2,
                    text=label,
                    fill=MUTED,
                    font=("Segoe UI", 8, "bold"),
                    tags=("board-coordinate",),
                )

        def draw_recognized_board():
            board_canvas.delete("all")
            board_left = coordinate_margin
            for row in range(8):
                for col in range(8):
                    x0, y0 = board_left + col * cell, row * cell
                    square_color = "#f0d9b5" if (row + col) % 2 == 0 else "#b58863"
                    board_canvas.create_rectangle(x0, y0, x0 + cell, y0 + cell, fill=square_color, outline=square_color)
                    data_row, data_col = row, col
                    if source_view["pieces_flipped"]:
                        data_row, data_col = 7 - row, 7 - col
                    piece = rows[data_row][data_col]
                    if piece != "1":
                        text = glyphs[piece]
                        piece_tag = f"board-piece-{data_row}-{data_col}"
                        if piece.isupper():
                            board_canvas.create_text(x0 + cell / 2 + 1, y0 + cell / 2 + 2, text=text, fill="#373737", font=("Segoe UI Symbol", piece_font_size, "bold"), tags=("board-piece", piece_tag))
                            board_canvas.create_text(x0 + cell / 2, y0 + cell / 2, text=text, fill="#fffdf8", font=("Segoe UI Symbol", piece_font_size, "bold"), tags=("board-piece", piece_tag))
                        else:
                            board_canvas.create_text(x0 + cell / 2, y0 + cell / 2, text=text, fill="#202027", font=("Segoe UI Symbol", piece_font_size, "bold"), tags=("board-piece", piece_tag))
                    if selected_square["position"] == (data_row, data_col):
                        board_canvas.create_rectangle(
                            x0 + 2, y0 + 2, x0 + cell - 2, y0 + cell - 2,
                            outline="#ff4057", width=3,
                        )
            draw_board_coordinates()
            refresh_fen_display()

        pending_square = {"position": None}
        piece_picker = {"window": None}
        review_key_tag = f"ChessSnipReviewKeys{id(window)}"

        def bind_review_key_scope(widget):
            tags = widget.bindtags()
            if review_key_tag not in tags:
                widget.bindtags((review_key_tag,) + tags)
            for child in widget.winfo_children():
                bind_review_key_scope(child)

        def dismiss_piece_picker(picker):
            if piece_picker.get("window") is picker:
                piece_picker["window"] = None
            try:
                if picker.winfo_exists():
                    picker.destroy()
            except tk.TclError:
                pass

        def dismiss_piece_picker_on_review_click(event):
            picker = piece_picker.get("window")
            if picker is None:
                return
            widget = event.widget
            while widget is not None:
                if widget is picker:
                    return
                widget = getattr(widget, "master", None)
            dismiss_piece_picker(picker)
            if selected_square["position"] == pending_square.get("position"):
                selected_square["position"] = None
                draw_recognized_board()

        window.bind_class(
            review_key_tag, "<Button-1>",
            dismiss_piece_picker_on_review_click, add="+",
        )

        def place_piece(piece):
            position = pending_square.get("position")
            if position is None:
                return
            row, col = position
            mutable = list(rows[row])
            mutable[col] = piece
            rows[row] = "".join(mutable)
            refresh_castling_suggestions()
            selected_square["position"] = None
            record_position_edit()
            draw_recognized_board()
            picker = piece_picker.get("window")
            if picker is not None:
                dismiss_piece_picker(picker)

        def show_piece_picker(event, row, col):
            bind_review_key_scope(window)
            old_picker = piece_picker.get("window")
            if old_picker is not None:
                dismiss_piece_picker(old_picker)
            pending_square["position"] = (row, col)
            picker = tk.Toplevel(window)
            piece_picker["window"] = picker
            picker.overrideredirect(True)
            picker.configure(bg=PANEL, bd=0, relief="flat")
            picker.transient(window)
            picker.attributes("-topmost", True)
            palette = tk.Frame(picker, bg=PANEL, padx=8, pady=8, highlightbackground=EDGE, highlightthickness=1)
            palette.pack()
            picker_header = tk.Frame(palette, bg=PANEL)
            picker_header.grid(row=0, column=0, columnspan=7, sticky="ew", pady=(0, 5))
            tk.Label(picker_header, text="PLACE A PIECE", bg=PANEL, fg=MUTED, font=("Segoe UI", 8, "bold")).pack(side="left")
            picker_close = tk.Button(picker_header, text="×", command=lambda: dismiss_piece_picker(picker), bg=PANEL, fg=MUTED, activebackground="#c42b1c", activeforeground="white", relief="flat", bd=0, width=3, cursor="hand2", font=("Segoe UI", 10))
            picker_close.pack(side="right")
            self._add_tooltip(picker_close, "Close the piece menu")
            clear_square = tk.Button(palette, text="Clear square", command=lambda: place_piece("1"), bg=PANEL2, fg=TEXT, activebackground=ACCENT, activeforeground="white", relief="flat", bd=0, padx=8, pady=4, cursor="hand2")
            clear_square.grid(row=1, column=0, columnspan=7, sticky="ew", pady=(0, 5))
            self._add_tooltip(clear_square, "Remove the piece from this square")

            piece_rows = (
                (
                    "White", 2, ("P", "N", "B", "R", "Q", "K"),
                    ("♟", "♞", "♝", "♜", "♛", "♚") if self.theme_name == "dark" else ("♙", "♘", "♗", "♖", "♕", "♔"),
                    "#5b6578" if self.theme_name == "light" else "#fffdf8",
                    "#777777" if self.theme_name == "dark" else PANEL2,
                ),
                (
                    "Black", 3, ("p", "n", "b", "r", "q", "k"),
                    ("♟", "♞", "♝", "♜", "♛", "♚"),
                    "#000000" if self.theme_name == "dark" else "#171a21",
                    "#777777" if self.theme_name == "dark" else PANEL2,
                ),
            )
            names = ("Pawn", "Knight", "Bishop", "Rook", "Queen", "King")
            for side, grid_row, codes, symbols, piece_color, piece_bg in piece_rows:
                tk.Label(palette, text=side, bg=PANEL, fg=TEXT, font=("Segoe UI", 8), width=5, anchor="w").grid(row=grid_row, column=0, sticky="w")
                for index, (code, symbol, name) in enumerate(zip(codes, symbols, names), start=1):
                    button = tk.Button(palette, text=f"{symbol}\n{name[0]}", command=lambda value=code: place_piece(value), bg=piece_bg, fg=piece_color, activebackground=ACCENT, activeforeground="white", relief="flat", bd=0, width=3, height=2, font=("Segoe UI Symbol", 11), cursor="hand2", highlightthickness=1, highlightbackground=EDGE, highlightcolor=ACCENT)
                    button.grid(row=grid_row, column=index, padx=2, pady=2)
                    self._add_tooltip(button, f"Place {side.lower()} {name.lower()}")

            picker.bind("<Escape>", lambda _event: dismiss_piece_picker(picker))
            def close_if_focus_left(_event=None):
                def check_focus():
                    try:
                        focused = picker.focus_get()
                        if focused is None or not str(focused).startswith(str(picker)):
                            dismiss_piece_picker(picker)
                    except tk.TclError:
                        pass
                picker.after_idle(check_focus)
            picker.bind("<FocusOut>", close_if_focus_left)
            picker.update_idletasks()
            picker_width, picker_height = picker.winfo_reqwidth(), picker.winfo_reqheight()
            picker.geometry(f"{picker_width}x{picker_height}")
            picker.update_idletasks()
            try:
                user32 = ctypes.windll.user32
                user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
                user32.GetAncestor.restype = wintypes.HWND
                hwnd = user32.GetAncestor(picker.winfo_id(), 2) or picker.winfo_id()
                user32.GetSystemMetrics.argtypes = (ctypes.c_int,)
                user32.GetSystemMetrics.restype = ctypes.c_int
                virtual_left = user32.GetSystemMetrics(76)  # SM_XVIRTUALSCREEN
                virtual_top = user32.GetSystemMetrics(77)  # SM_YVIRTUALSCREEN
                virtual_width = user32.GetSystemMetrics(78)  # SM_CXVIRTUALSCREEN
                virtual_height = user32.GetSystemMetrics(79)  # SM_CYVIRTUALSCREEN
                x = max(virtual_left + 8, min(event.x_root, virtual_left + virtual_width - picker_width - 8))
                y = max(virtual_top + 8, min(event.y_root, virtual_top + virtual_height - picker_height - 8))
                user32.SetWindowPos.argtypes = (wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT)
                user32.SetWindowPos.restype = wintypes.BOOL
                user32.SetWindowPos(hwnd, None, x, y, picker_width, picker_height, 0x0014)  # NOZORDER | NOACTIVATE
            except Exception:
                x, y = int(event.x_root), int(event.y_root)
                picker.geometry(f"{picker_width}x{picker_height}+{max(0, x)}+{max(0, y)}")
            picker.lift()
            bind_review_key_scope(picker)
            picker.focus_force()

        def square_at_event(event):
            display_col = int((event.x - coordinate_margin) // cell)
            display_row = int(event.y // cell)
            if not (0 <= display_col < 8 and 0 <= display_row < 8):
                return None
            if source_view["pieces_flipped"]:
                col, row = 7 - display_col, 7 - display_row
            else:
                col, row = display_col, display_row
            return row, col

        def move_piece(source, destination):
            if source is None or destination is None or source == destination:
                return False
            source_row, source_col = source
            target_row, target_col = destination
            piece = rows[source_row][source_col]
            if piece == "1":
                return False
            source_values = list(rows[source_row])
            source_values[source_col] = "1"
            rows[source_row] = "".join(source_values)
            target_values = list(rows[target_row])
            target_values[target_col] = piece
            rows[target_row] = "".join(target_values)
            selected_square["position"] = None
            refresh_castling_suggestions()
            record_position_edit()
            draw_recognized_board()
            return True

        move_gesture = {
            "source": None, "start": None, "dragging": False,
            "click_move_from": None, "click_destination": None,
        }

        def start_piece_move(event):
            position = square_at_event(event)
            if position is None:
                return "break"
            row, col = position
            piece = rows[row][col]
            previously_selected = selected_square["position"]
            move_gesture.update(
                source=None, start=(event.x, event.y), dragging=False,
                click_move_from=None, click_destination=None,
            )
            if piece == "1":
                if previously_selected is not None and previously_selected != position:
                    source_piece = rows[previously_selected[0]][previously_selected[1]]
                    if source_piece != "1":
                        move_gesture["click_move_from"] = previously_selected
                        move_gesture["click_destination"] = position
                return "break"

            selected_square["position"] = position
            move_gesture["source"] = position
            move_gesture["piece"] = piece
            draw_recognized_board()
            return "break"

        def drag_piece_move(event):
            source = move_gesture["source"]
            start = move_gesture["start"]
            if source is None or start is None:
                return
            if not move_gesture["dragging"]:
                if abs(event.x - start[0]) + abs(event.y - start[1]) < 6:
                    return
                move_gesture["dragging"] = True
                board_canvas.itemconfigure(
                    f"board-piece-{source[0]}-{source[1]}", state="hidden",
                )
                piece = move_gesture["piece"]
                glyph = glyphs[piece]
                if piece.isupper():
                    board_canvas.create_text(
                        event.x + 1, event.y + 2, text=glyph, fill="#373737",
                        font=("Segoe UI Symbol", piece_font_size, "bold"),
                        tags=("piece-drag-preview", "piece-drag-shadow"),
                    )
                    board_canvas.create_text(
                        event.x, event.y, text=glyph, fill="#fffdf8",
                        font=("Segoe UI Symbol", piece_font_size, "bold"),
                        tags=("piece-drag-preview", "piece-drag-main"),
                    )
                else:
                    board_canvas.create_text(
                        event.x, event.y, text=glyph, fill="#202027",
                        font=("Segoe UI Symbol", piece_font_size, "bold"),
                        tags=("piece-drag-preview", "piece-drag-main"),
                    )
            else:
                board_canvas.coords("piece-drag-shadow", event.x + 1, event.y + 2)
                board_canvas.coords("piece-drag-main", event.x, event.y)

        def finish_piece_move(event):
            source = move_gesture["source"]
            was_dragging = move_gesture["dragging"]
            click_move_from = move_gesture["click_move_from"]
            click_destination = move_gesture["click_destination"]
            board_canvas.delete("piece-drag-preview")
            move_gesture.update(
                source=None, start=None, dragging=False,
                click_move_from=None, click_destination=None,
            )
            if was_dragging and source is not None:
                if not move_piece(source, square_at_event(event)):
                    draw_recognized_board()
            elif click_move_from is not None and click_destination is not None:
                move_piece(click_move_from, click_destination)
            return "break"

        def choose_square_piece(event):
            position = square_at_event(event)
            if position is None:
                return "break"
            row, col = position
            selected_square["position"] = position
            draw_recognized_board()
            show_piece_picker(event, row, col)
            return "break"

        def castling_changed(right):
            manually_set_castling.add(right)
            record_position_edit()
            draw_recognized_board()

        def set_board_orientation():
            # Only coordinate-tagged text items are replaced here. Board
            # squares and pieces stay untouched; update notation separately.
            draw_board_coordinates()
            refresh_fen_display()

        board_canvas.configure(cursor="hand2")
        board_canvas.bind("<ButtonPress-1>", start_piece_move)
        board_canvas.bind("<B1-Motion>", drag_piece_move)
        board_canvas.bind("<ButtonRelease-1>", finish_piece_move)
        board_canvas.bind("<Button-3>", choose_square_piece)
        draw_recognized_board()

        def flip_board():
            source_view["pieces_flipped"] = not source_view["pieces_flipped"]
            board_flip_view["labels_flipped"] = not board_flip_view["labels_flipped"]
            draw_recognized_board()

        def flip_from_key(event=None):
            if event is not None and getattr(event, "keysym", "").lower() != "f":
                return
            if window.winfo_exists():
                flip_board()
            return "break"

        window.bind_class(review_key_tag, "<KeyPress>", flip_from_key)
        bind_review_key_scope(window)
        undo_position_button = tk.Button(
            board_edit_controls, text="↶", command=undo_position_edit,
            bg=PANEL2, fg=MUTED, activebackground=EDGE, activeforeground=TEXT,
            relief="flat", bd=0, padx=6, pady=4,
            cursor="hand2", font=("Segoe UI", 11), takefocus=0,
            state=tk.NORMAL,
        )
        undo_position_button.pack(side="left", padx=(0, 3))
        position_history_buttons["undo"] = undo_position_button
        self._add_tooltip(undo_position_button, "Undo the last board edit")
        redo_position_button = tk.Button(
            board_edit_controls, text="↷", command=redo_position_edit,
            bg=PANEL2, fg=MUTED, activebackground=EDGE, activeforeground=TEXT,
            relief="flat", bd=0, padx=6, pady=4,
            cursor="hand2", font=("Segoe UI", 11), takefocus=0,
            state=tk.NORMAL,
        )
        redo_position_button.pack(side="left", padx=(0, 3))
        position_history_buttons["redo"] = redo_position_button
        self._add_tooltip(redo_position_button, "Redo the last undone board edit")
        reset_position_button = tk.Button(
            board_edit_controls, command=reset_position,
            bg=INK, fg=MUTED, activebackground=INK, activeforeground=MUTED,
            relief="flat", bd=0, padx=4, pady=3, cursor="hand2", takefocus=0,
            state=tk.NORMAL,
        )
        def create_reset_position_icon(color):
            key = str(color).lower()
            if key in reset_position_icons:
                return reset_position_icons[key]
            scale, size = 4, 20
            bitmap = Image.new("RGBA", (size * scale, size * scale), (0, 0, 0, 0))
            draw = ImageDraw.Draw(bitmap)
            draw.arc(
                (3 * scale, 3 * scale, 17 * scale, 17 * scale),
                start=45, end=325, fill=color, width=2 * scale,
            )
            draw.polygon(
                [(13 * scale, 2 * scale), (18 * scale, 4 * scale), (14 * scale, 8 * scale)],
                fill=color,
            )
            photo = ImageTk.PhotoImage(
                bitmap.resize((size, size), Image.Resampling.LANCZOS), master=window,
            )
            reset_position_icons[key] = photo
            reset_position_icon_art[key] = bitmap
            return photo

        def set_reset_position_button_icon(color):
            key = str(color).lower()
            photo = create_reset_position_icon(color)
            reset_position_button._chesssnip_inline_icon_art = {
                str(photo): (reset_position_icon_art[key], (20, 20)),
            }
            reset_position_button.configure(image=photo)
            reset_position_button._reset_position_icon = photo

        def refresh_reset_position_icon():
            color = (
                MUTED if getattr(reset_position_button, "_position_action_available", False)
                else EDGE
            )
            set_reset_position_button_icon(color)

        reset_position_button._chesssnip_theme_refresh = refresh_reset_position_icon
        reset_position_button._chesssnip_custom_hover = True
        reset_position_button.pack(side="left", padx=(0, 3))
        position_history_buttons["reset"] = reset_position_button
        self._add_tooltip(reset_position_button, "Restore the recognized piece layout")
        update_position_history_buttons()

        def create_flip_board_icon(color):
            key = str(color).lower()
            if key in flip_board_icons:
                return flip_board_icons[key]
            scale, size = 5, 22
            bitmap = Image.new("RGBA", (size * scale, size * scale), (0, 0, 0, 0))
            draw = ImageDraw.Draw(bitmap)
            width = 2 * scale
            # Two opposing rounded paths suggest rotating the board without
            # relying on a font glyph that may render differently per system.
            draw.line(
                [(5*scale, 9*scale), (5*scale, 6*scale), (8*scale, 3*scale),
                 (16*scale, 3*scale), (19*scale, 6*scale)],
                fill=color, width=width, joint="curve",
            )
            draw.polygon(
                [(16*scale, 5*scale), (20*scale, 6*scale), (19*scale, 2*scale)],
                fill=color,
            )
            draw.line(
                [(17*scale, 13*scale), (17*scale, 16*scale), (14*scale, 19*scale),
                 (6*scale, 19*scale), (3*scale, 16*scale)],
                fill=color, width=width, joint="curve",
            )
            draw.polygon(
                [(6*scale, 17*scale), (2*scale, 16*scale), (3*scale, 20*scale)],
                fill=color,
            )
            photo = ImageTk.PhotoImage(
                bitmap.resize((size, size), Image.Resampling.LANCZOS), master=window,
            )
            flip_board_icons[key] = photo
            flip_board_icon_art[key] = bitmap
            return photo

        flip_button = tk.Button(
            right_header, command=flip_board, bg=PANEL2, fg=TEXT,
            activebackground=EDGE, activeforeground=TEXT, relief="flat", bd=0,
            padx=5, pady=3, cursor="hand2", takefocus=0,
        )
        flip_board_icons = {}
        flip_board_icon_art = {}
        def refresh_flip_board_icon():
            photo = create_flip_board_icon(MUTED)
            color_key = str(MUTED).lower()
            flip_button._chesssnip_inline_icon_art = {
                str(photo): (flip_board_icon_art[color_key], (22, 22)),
            }
            flip_button.configure(image=photo)
            flip_button._flip_board_icon = photo

        flip_button._chesssnip_theme_refresh = refresh_flip_board_icon
        refresh_flip_board_icon()
        flip_button.pack(side="right")
        self._add_tooltip(flip_button, "Flip board")

        def block_unavailable_history_action(event, button):
            if not getattr(button, "_position_action_available", False):
                return "break"

        for history_button in (undo_position_button, redo_position_button, reset_position_button):
            history_button._position_action_available = False
            history_button._chesssnip_custom_hover = True
            history_button.bind(
                "<ButtonPress-1>",
                lambda event, button=history_button: block_unavailable_history_action(event, button),
                add="+",
            )
            history_button.bind(
                "<ButtonRelease-1>",
                lambda event, button=history_button: block_unavailable_history_action(event, button),
                add="+",
            )
        orientation_controls = tk.Frame(right, bg=INK)
        orientation_controls.pack(fill="x", pady=(5, 0))
        tk.Label(orientation_controls, text="Board position", bg=INK, fg=MUTED, font=("Segoe UI", 8, "bold")).pack(side="left", padx=(coordinate_margin, 8))
        orientation_switch = tk.Canvas(orientation_controls, width=50, height=28, bg=INK, highlightthickness=0, cursor="hand2", takefocus=1)
        orientation_switch.pack(side="left", padx=(0, 7))
        orientation_label = tk.StringVar(value="Normal" if coordinate_orientation.get() == "white" else "Flipped")
        tk.Label(orientation_controls, textvariable=orientation_label, bg=INK, fg=TEXT, font=("Segoe UI", 9, "bold"), width=7, anchor="w").pack(side="left")

        def render_review_toggle(canvas, enabled):
            scale = 5
            toggle_width = max(50, canvas.winfo_reqwidth())
            toggle_height = max(28, canvas.winfo_reqheight())
            red, green, blue = canvas.winfo_rgb(canvas.cget("bg"))
            background = (red >> 8, green >> 8, blue >> 8, 255)
            bitmap = Image.new(
                "RGBA", (toggle_width * scale, toggle_height * scale), background,
            )
            draw = ImageDraw.Draw(bitmap)
            track_color = ACCENT if enabled else PANEL2
            draw.rounded_rectangle(
                (scale, 2 * scale, (toggle_width - 1) * scale, (toggle_height - 2) * scale),
                radius=12 * scale, fill=track_color,
            )
            thumb_x = toggle_width - 24 if enabled else 4
            draw.ellipse(
                (thumb_x * scale, 4 * scale,
                 (thumb_x + 20) * scale, 24 * scale),
                fill="#ffffff", outline=EDGE, width=scale,
            )
            photo = ImageTk.PhotoImage(
                bitmap.resize((toggle_width, toggle_height), Image.Resampling.LANCZOS),
                master=window,
            )
            canvas.delete("all")
            canvas.create_image(0, 0, image=photo, anchor="nw", tags=("toggle-art",))
            canvas._review_toggle_photo = photo

        def redraw_orientation_switch():
            flipped = coordinate_orientation.get() == "black"
            render_review_toggle(orientation_switch, flipped)
            orientation_label.set("Flipped" if flipped else "Normal")

        orientation_switch._chesssnip_theme_refresh = redraw_orientation_switch

        def toggle_orientation_switch(_event=None):
            coordinate_orientation.set("white" if coordinate_orientation.get() == "black" else "black")
            set_board_orientation()
            redraw_orientation_switch()
            return "break"

        orientation_switch.bind("<Button-1>", toggle_orientation_switch)
        orientation_switch.bind("<space>", toggle_orientation_switch)
        orientation_switch.bind("<Return>", toggle_orientation_switch)
        self._add_tooltip(orientation_switch, "Flip coordinate labels and update the FEN · Flip board also rotates pieces and labels together")
        redraw_orientation_switch()

        options = tk.Frame(window, bg=PANEL, padx=12, pady=9)
        options.pack(fill="x", padx=24, pady=(16, 9))
        tk.Label(options, text="To move", bg=PANEL, fg=TEXT).grid(row=0, column=0, sticky="w", padx=(0, 8))
        turn_switch = tk.Canvas(options, width=50, height=28, bg=PANEL, highlightthickness=0, cursor="hand2", takefocus=1)
        turn_switch.grid(row=0, column=1, sticky="w", padx=(0, 7))
        turn_label = tk.StringVar(value="Black to move" if turn.get() == "b" else "White to move")
        tk.Label(options, textvariable=turn_label, bg=PANEL, fg=TEXT, font=("Segoe UI", 9, "bold"), width=15, anchor="w").grid(row=0, column=2, sticky="w", padx=(0, 16))

        def redraw_turn_switch():
            black_to_move = turn.get() == "b"
            render_review_toggle(turn_switch, black_to_move)
            turn_label.set("Black to move" if black_to_move else "White to move")

        turn_switch._chesssnip_theme_refresh = redraw_turn_switch

        def toggle_turn_switch(_event=None):
            turn.set("w" if turn.get() == "b" else "b")
            redraw_turn_switch()
            draw_recognized_board()
            return "break"

        turn_switch.bind("<Button-1>", toggle_turn_switch)
        turn_switch.bind("<space>", toggle_turn_switch)
        turn_switch.bind("<Return>", toggle_turn_switch)
        self._add_tooltip(turn_switch, "Switch whose turn is next in the exported position")
        redraw_turn_switch()
        tk.Label(options, text="Castling", bg=PANEL, fg=TEXT).grid(row=1, column=0, sticky="w", padx=(0, 8), pady=(7, 0))
        for index, key in enumerate("KQkq", start=1):
            side_name = "White" if key.isupper() else "Black"
            direction_name = "kingside" if key.lower() == "k" else "queenside"
            castle_button = self._make_dark_checkbox(
                options, key, castle_vars[key],
                command=lambda right=key: castling_changed(right),
                tooltip=f"Allow {side_name.lower()} {direction_name} castling",
            )
            castle_button.grid(row=1, column=index, sticky="w", padx=2, pady=(7, 0))
        tk.Label(options, text="Half-moves", bg=PANEL, fg=MUTED).grid(row=2, column=0, sticky="w", pady=(7, 0))
        half_entry = DarkNumberSelector(options, halfmove, 0, 999, width=5, command=draw_recognized_board)
        half_entry.grid(row=2, column=1, sticky="w", pady=(7, 0))
        self._add_tooltip(half_entry, "Set the half-move counter for the FEN")
        tk.Label(options, text="Move number", bg=PANEL, fg=MUTED).grid(row=2, column=2, sticky="w", padx=(14, 5), pady=(7, 0))
        full_entry = DarkNumberSelector(options, fullmove, 1, 9999, width=5, command=draw_recognized_board)
        full_entry.grid(row=2, column=3, sticky="w", pady=(7, 0))
        self._add_tooltip(full_entry, "Set the full-move number for the FEN")
        tk.Label(window, text="FEN", bg=INK, fg=MUTED, font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=20, pady=(0, 4))
        fen_row = tk.Frame(window, bg=INK)
        fen_row.pack(fill="x", padx=24)
        fen_entry = tk.Entry(fen_row, textvariable=fen_var, readonlybackground=PANEL2, fg=TEXT, insertbackground=TEXT, relief="flat", bd=0)
        fen_entry.pack(side="left", fill="x", expand=True, ipady=7)
        fen_entry.configure(state="readonly")
        self._add_tooltip(fen_entry, "FEN for the reviewed current position")

        copy_popup = {"window": None, "after_id": None}

        def close_copy_popup(popup):
            if copy_popup["window"] is popup:
                copy_popup["window"] = None
                copy_popup["after_id"] = None
            try:
                popup.destroy()
            except tk.TclError:
                pass

        def show_copy_popup(text):
            previous = copy_popup.get("window")
            if previous is not None:
                after_id = copy_popup.get("after_id")
                if after_id is not None:
                    try:
                        previous.after_cancel(after_id)
                    except tk.TclError:
                        pass
                close_copy_popup(previous)

            popup = tk.Toplevel(window)
            copy_popup["window"] = popup
            popup.withdraw()
            popup.overrideredirect(True)
            popup.transient(window)
            popup.attributes("-topmost", True)
            popup.configure(bg=ACCENT)
            frame = tk.Frame(popup, bg=PANEL2, padx=16, pady=10)
            frame.pack(padx=1, pady=1)
            tk.Label(frame, text=text, bg=PANEL2, fg=TEXT, font=("Segoe UI", 9, "bold")).pack()
            popup.update_idletasks()
            popup_width, popup_height = popup.winfo_reqwidth(), popup.winfo_reqheight()
            popup_x = window.winfo_rootx() + (window.winfo_width() - popup_width) // 2
            popup_y = window.winfo_rooty() + window.winfo_height() - popup_height - 48
            popup.geometry(f"{popup_width}x{popup_height}+{popup_x}+{popup_y}")
            popup.deiconify()
            popup.lift()
            copy_popup["after_id"] = popup.after(1500, lambda: close_copy_popup(popup))

        def copy_fen():
            try:
                self.root.clipboard_clear()
                self.root.clipboard_append(fen_var.get())
                self.root.update_idletasks()
                self.status.set("FEN copied to clipboard")
                show_copy_popup("FEN copied")
            except tk.TclError as exc:
                messagebox.showerror("Couldn't copy FEN", str(exc), parent=window)

        fen_copy_button = tk.Button(fen_row, text="Copy", command=copy_fen, bg=PANEL2, fg=TEXT, activebackground=EDGE, activeforeground=TEXT, relief="flat", bd=0, padx=14, cursor="hand2")
        fen_copy_button.pack(side="left", padx=(8, 0), ipady=4)
        # Avoid freezing a transient full-row height into the button's image.
        fen_copy_button._chesssnip_aa_fixed_size = (68, 36)
        self._add_tooltip(fen_copy_button, "Copy the FEN to the clipboard")
        button_row = tk.Frame(window, bg=INK)
        button_row.pack(fill="x", padx=24, pady=(16, 22))
        tk.Label(button_row, text=f"Average square confidence: {recognition['confidence']:.0%}", bg=INK, fg=MUTED).pack(side="left")

        def build_pgn():
            fen = current_fen()
            date_text = datetime.now().strftime("%Y.%m.%d")
            return (f'[Event "ChessSnip position"]\n[Site "?"]\n[Date "{date_text}"]\n[Round "-"]\n'
                    f'[White "?"]\n[Black "?"]\n[Result "*"]\n[SetUp "1"]\n[FEN "{fen}"]\n\n*\n')

        def copy_pgn():
            try:
                text = build_pgn()
                self.root.clipboard_clear()
                self.root.clipboard_append(text)
                self.root.update_idletasks()
                self.status.set("Position PGN copied to clipboard")
                show_copy_popup("PGN copied")
            except (ValueError, tk.TclError) as exc:
                messagebox.showerror("Couldn't create PGN", str(exc), parent=window)

        def open_in_lichess():
            try:
                fen = current_fen()
                orientation = "black" if turn.get() == "b" else "white"
                url = f"https://lichess.org/analysis/{quote(fen, safe='/')}?color={orientation}"
                if not webbrowser.open(url, new=2):
                    raise OSError("Windows couldn't open your default browser.")
                self.status.set("Position opened in Lichess analysis")
            except Exception as exc:
                messagebox.showerror("Couldn't open Lichess", str(exc), parent=window)

        def save_pgn():
            try:
                text = build_pgn()
            except ValueError as exc:
                messagebox.showerror("Couldn't create PGN", str(exc), parent=window)
                return
            path = filedialog.asksaveasfilename(parent=window, title="Save chess position as PGN", initialfile="ChessSnip_Position.pgn", defaultextension=".pgn", filetypes=(("PGN chess game", "*.pgn"), ("All files", "*.*")))
            if not path:
                return
            try:
                Path(path).write_text(text, encoding="utf-8", newline="\n")
                self.status.set("Saved position PGN  ·  " + path)
            except OSError as exc:
                messagebox.showerror("Couldn't save PGN", str(exc), parent=window)

        save_button = tk.Button(button_row, text="Save PGN…", command=save_pgn, bg=PANEL2, fg=TEXT, activebackground=EDGE, activeforeground=TEXT, relief="flat", bd=0, padx=24, pady=10, cursor="hand2")
        save_button.pack(side="right", padx=(18, 0))
        self._add_tooltip(save_button, "Save this position as a PGN file")
        lichess_logo = None
        try:
            with Image.open(_resource_path("assets/lichess/lichess-logo.png")) as logo_source:
                logo_rgba = logo_source.convert("RGBA").resize((20, 20), Image.Resampling.LANCZOS)
                logo_tinted = Image.new("RGBA", logo_rgba.size, TEXT)
                logo_tinted.putalpha(logo_rgba.getchannel("A"))
                lichess_logo = ImageTk.PhotoImage(logo_tinted, master=window)
                self._register_button_icon(lichess_logo, logo_tinted, logo_rgba.size)
        except (OSError, tk.TclError):
            pass
        lichess_button = tk.Button(button_row, text="Open in Lichess", image=lichess_logo, compound="left", command=open_in_lichess, bg=PANEL2, fg=TEXT, activebackground=EDGE, activeforeground=TEXT, relief="flat", bd=0, padx=16, pady=10, cursor="hand2")
        lichess_button.image = lichess_logo
        lichess_button.pack(side="right", padx=(18, 0))
        self._add_tooltip(lichess_button, "Open this FEN in Lichess analysis")
        copy_pgn_button = tk.Button(button_row, text="Copy PGN", command=copy_pgn, bg=ACCENT, fg="white", activebackground="#7770ea", activeforeground="white", relief="flat", bd=0, padx=26, pady=10, cursor="hand2", font=("Segoe UI Semibold", 9))
        copy_pgn_button.pack(side="right")
        self._add_tooltip(copy_pgn_button, "Copy the position PGN to the clipboard")
        window.after(180, activate_review_window)

    @staticmethod
    def _prepare_clipboard_dib(data):
        k=ctypes.windll.kernel32
        k.GlobalAlloc.argtypes=(wintypes.UINT,ctypes.c_size_t);k.GlobalAlloc.restype=wintypes.HGLOBAL
        k.GlobalLock.argtypes=(wintypes.HGLOBAL,);k.GlobalLock.restype=ctypes.c_void_p
        k.GlobalUnlock.argtypes=(wintypes.HGLOBAL,);k.GlobalUnlock.restype=wintypes.BOOL
        k.GlobalFree.argtypes=(wintypes.HGLOBAL,);k.GlobalFree.restype=wintypes.HGLOBAL
        mem=k.GlobalAlloc(0x0002,len(data))
        if not mem:raise MemoryError("Couldn't allocate clipboard image.")
        try:
            ptr=k.GlobalLock(mem)
            if not ptr:raise MemoryError("Couldn't access clipboard image memory.")
            ctypes.memmove(ptr,data,len(data));k.GlobalUnlock(mem)
            return mem
        except Exception:
            k.GlobalFree(mem)
            raise

    @staticmethod
    def _free_clipboard_handle(handle):
        if handle:
            k=ctypes.windll.kernel32;k.GlobalFree.argtypes=(wintypes.HGLOBAL,);k.GlobalFree.restype=wintypes.HGLOBAL;k.GlobalFree(handle)

    def _set_clipboard_dib(self,data):
        self._put_clipboard_handle(self._prepare_clipboard_dib(data))

    def _put_clipboard_handle(self,handle):
        u=ctypes.windll.user32
        u.OpenClipboard.argtypes=(wintypes.HWND,);u.OpenClipboard.restype=wintypes.BOOL
        u.SetClipboardData.argtypes=(wintypes.UINT,wintypes.HANDLE);u.SetClipboardData.restype=wintypes.HANDLE
        if not u.OpenClipboard(self.root.winfo_id()):
            self._free_clipboard_handle(handle)
            raise OSError("The clipboard is busy. Try again.")
        try:
            u.EmptyClipboard()
            if not u.SetClipboardData(8,handle):raise OSError("Windows couldn't copy the image.")
            handle=None  # Windows owns it after SetClipboardData succeeds.
        finally:
            if handle:self._free_clipboard_handle(handle)
            u.CloseClipboard()

    def open_in_paint(self):
        if not self.image:self.status.set("Take a snip first to open it in Paint");return
        try:
            TEMP_FOLDER.mkdir(parents=True,exist_ok=True)
            path=TEMP_FOLDER/("ChessSnip_Paint_"+datetime.now().strftime("%Y%m%d_%H%M%S_%f")+".png")
            self._image_as_rgb(self._composited()).save(path,"PNG")
            _open_in_paint(path)
            self.status.set("Opened a temporary copy in Paint · use Save As in Paint to keep it")
        except Exception as e:messagebox.showerror("Couldn't open in Paint",str(e),parent=self.root)

    def _copy_capture_to_clipboard(self):
        """Put every newly captured snip on the clipboard automatically."""
        self._schedule_auto_clipboard()

    def _pointer_status(self,e):
        if self._rotation_drag:
            return
        self._pointer_canvas_pos=(self.canvas.canvasx(e.x),self.canvas.canvasy(e.y))
        self._update_recognition_qr_tooltip(e)
        self._update_brush_cursor()
        if self.tool.get() == "crop":
            self._set_crop_cursor(e)
        elif not self._drawing and not self._shape_transform_drag:
            self._set_shape_selection_cursor(e)
        if self.image and self.tool.get()!="crop":
            x,y=self._to_image(e.x,e.y)
            if 0<=x<self.image.width and 0<=y<self.image.height:self.status.set(f"{self.image.width} × {self.image.height} px   ·   {int(x)}, {int(y)}")

    def _report_callback_exception(self, exc_type, exc_value, exc_traceback):
        _write_crash_log_exception(
            "Unhandled Tkinter callback exception", exc_type, exc_value, exc_traceback
        )
        try:
            tk.Tk.report_callback_exception(
                self.root, exc_type, exc_value, exc_traceback
            )
        except Exception:
            pass

    def close(self):
        if self._timer:
            try:self.root.after_cancel(self._timer)
            except tk.TclError:pass
            self._timer=None
        self._remove_countdown_taskbar_overlay()
        self._restore_system_cursor()
        try:
            if self._hotkey:ctypes.windll.user32.UnregisterHotKey(None,0x51A0)
        except Exception:pass
        self.root.destroy()

    def run(self): self.root.mainloop()


if __name__ == "__main__":
    ChessSnip().run()




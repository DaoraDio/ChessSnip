"""ChessSnip — a native-feeling Windows screenshot and annotation utility."""
from __future__ import annotations

import ctypes
import colorsys
import io
import json
import math
import os
import sys
import threading
import tempfile
import time
import tkinter as tk
import webbrowser
from ctypes import wintypes
from datetime import datetime
from pathlib import Path
from tkinter import colorchooser, filedialog, messagebox, ttk
from urllib.parse import quote
import uuid

try:
    from PIL import Image, ImageDraw, ImageTk
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
                padx=8, pady=5, relief="solid", bd=1, highlightthickness=1,
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
        self._shape_preview_photos = []
        self._drawing_preview_photo = None
        self.history: list[tuple[Image.Image | None, list[dict]]] = []
        self.tool = tk.StringVar(value="pen")
        self._eyedropper_return_tool = "pen"
        self.color = tk.StringVar(value="#000000")
        self.zoom_factor = 1.0
        self.color_buttons = {}
        self.custom_color_button = None
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
        self._erasing = False
        self._erase_changed = False
        self._crop_start = None
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
        self.theme_button = None
        self._last_canvas_size = None
        self._preview_redraw_after = None
        self._clipboard_after = None
        self._clipboard_revision = 0
        self._clipboard_busy = False
        self._active_choice_popup = None
        self._tool_icon_images = self._create_tool_icon_images(TEXT)
        self._spectrum_icon_photo = self._create_rgb_spectrum_icon()
        self._build_ui()
        self.root.bind_all("<Motion>", self._choice_popup_motion, add="+")
        self.root.bind_all("<ButtonPress-1>", self._choice_popup_click_away, add="+")
        self.root.bind_all("<Escape>", self._choice_popup_escape, add="+")
        self.root.after(100, self._enable_taskbar_button)
        self.root.bind("<Control-Shift-x>", lambda e: self.begin_capture())
        self.root.bind("<Control-s>", lambda e: self.save_as())
        self.root.bind("<Control-c>", lambda e: self.copy_image())
        self.root.bind("<Control-z>", lambda e: self.undo())
        self.root.bind("<Control-Shift-p>", lambda e: self.chess_to_pgn())
        self._register_hotkey()
        self.root.after(100, self._poll_hotkey)
        if "--snip" in sys.argv[1:]:
            self.root.after(250, self.begin_capture)

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
        for name in ("pen", "highlighter", "eraser", "rectangle", "circle", "crop", "eyedropper"):
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
            elif name == "crop":
                for points in (
                    ((4, 8), (4, 4), (8, 4)), ((12, 4), (16, 4), (16, 8)),
                    ((4, 12), (4, 16), (8, 16)), ((12, 16), (16, 16), (16, 12)),
                ):
                    draw.line(xy(points), fill=color, width=width)
            elif name == "eyedropper":
                draw.line(xy(((6, 14), (13, 7))), fill=color, width=3 * scale)
                draw.line(xy(((4, 16), (8, 12))), fill=color, width=width)
                draw.polygon(xy(((12, 5), (14, 3), (18, 7), (16, 9))), outline=color, fill=color)
                draw.ellipse((3 * scale, 15 * scale, 7 * scale, 19 * scale), outline=color, width=scale)

            images[name] = ImageTk.PhotoImage(
                bitmap.resize((size, size), Image.Resampling.LANCZOS), master=self.root,
            )
        return images

    def _create_rgb_spectrum_icon(self):
        """Draw a compact two-dimensional RGB hue and saturation field."""
        scale = 4
        width, height = 20, 19
        bitmap = Image.new("RGBA", (width * scale, height * scale), (0, 0, 0, 0))
        pixels = bitmap.load()
        left, top, right, bottom = scale, scale, width * scale - scale - 1, height * scale - scale - 1
        for px in range(left, right + 1):
            for py in range(top, bottom + 1):
                hue = (px - left) / max(1, right - left)
                vertical = (py - top) / max(1, bottom - top)
                saturation = 0.12 + 0.88 * vertical
                value = 1.0 - 0.10 * max(0.0, (vertical - 0.82) / 0.18)
                red, green, blue = colorsys.hsv_to_rgb(hue, saturation, value)
                pixels[px, py] = (round(red * 255), round(green * 255), round(blue * 255), 255)
        draw = ImageDraw.Draw(bitmap)
        draw.rectangle((0, 0, width * scale - 1, height * scale - 1), outline=(185, 190, 201, 255), width=scale)
        return ImageTk.PhotoImage(bitmap.resize((width, height), Image.Resampling.LANCZOS), master=self.root)

    def _refresh_tool_icon_images(self):
        self._tool_icon_images = self._create_tool_icon_images(TEXT)
        for name, button in getattr(self, "tool_buttons", {}).items():
            try:
                button.configure(image=self._tool_icon_images[name], compound=tk.LEFT)
            except tk.TclError:
                pass
        for group, (button, _label) in getattr(self, "_tool_variant_buttons", {}).items():
            try:
                button.configure(image=self._tool_icon_images[group], compound=tk.LEFT)
            except tk.TclError:
                pass
        if getattr(self, "pipette_button", None):
            try:
                self.pipette_button.configure(image=self._tool_icon_images["eyedropper"], compound=tk.LEFT)
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
        controls=tk.Frame(self.titlebar,bg=PANEL);controls.pack(side="right",fill="y")
        self.theme_button=tk.Button(
            controls, text=self._theme_action_label(), command=self.toggle_theme,
            bg=PANEL2, fg=TEXT, activebackground=EDGE, activeforeground=TEXT,
            relief="flat", bd=0, padx=9, cursor="hand2", font=("Segoe UI", 9),
        )
        self.theme_button.pack(side="left", fill="y", padx=(0, 4))
        self._add_tooltip(self.theme_button, "Switch between the dark and light themes")
        minimize_button=tk.Button(controls,text="—",command=self._minimize_window,bg=PANEL,fg=TEXT,activebackground=PANEL2,activeforeground=TEXT,relief="flat",bd=0,width=5,cursor="hand2",font=("Segoe UI",11))
        minimize_button.pack(side="left",fill="y")
        self._bind_title_button_hover(minimize_button)
        self._add_tooltip(minimize_button, "Minimize ChessSnip")
        self.maximize_button=tk.Button(controls,text="□",command=self._toggle_maximize,bg=PANEL,fg=TEXT,activebackground=PANEL2,activeforeground=TEXT,relief="flat",bd=0,width=5,cursor="hand2",font=("Segoe UI",11))
        self.maximize_button.pack(side="left",fill="y")
        self._bind_title_button_hover(self.maximize_button)
        self._add_tooltip(self.maximize_button, "Maximize or restore ChessSnip")
        close_button=tk.Button(controls,text="×",command=self.close,bg=PANEL,fg=TEXT,activebackground="#c42b1c",activeforeground="white",relief="flat",bd=0,width=5,cursor="hand2",font=("Segoe UI",12))
        close_button.pack(side="left",fill="y")
        self._bind_title_button_hover(close_button,"#c42b1c")
        self._add_tooltip(close_button, "Close ChessSnip")
        for target in (self.titlebar,brand,*brand.winfo_children()):
            target.bind("<ButtonPress-1>",self._titlebar_drag)
            target.bind("<B1-Motion>",self._titlebar_move)
            target.bind("<Double-Button-1>",lambda _e:self._toggle_maximize())
        self._add_tooltip(brand, "Drag to move · double-click to maximize or restore")
        # Capture controls
        controls = tk.Frame(self.root, bg=PANEL, padx=12, pady=5)
        controls.pack(fill="x", padx=12, pady=5)
        tk.Label(controls, text="CAPTURE", bg=PANEL, fg=MUTED, font=("Segoe UI", 8, "bold")).pack(side="left", padx=(0, 14))
        self._mode_menu(controls)
        tk.Label(controls, text="Delay", bg=PANEL, fg=MUTED).pack(side="left", padx=(12, 5))
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
        new_snip_button = tk.Button(controls, text="＋  New snip", command=self.begin_capture, bg=ACCENT, fg="white", activebackground="#7770ea", activeforeground="white", relief="flat", bd=0, padx=17, pady=7, font=("Segoe UI Semibold", 10), cursor="hand2")
        new_snip_button.pack(side="right", padx=(8, 0))
        self._add_tooltip(new_snip_button, "Start a snip · Ctrl+Shift+X")

        # Editor tools
        editorbar = tk.Frame(self.root, bg=INK, padx=22, pady=8); editorbar.pack(fill="x")
        self.editorbar=editorbar
        toolrow = tk.Frame(editorbar, bg=INK); toolrow.pack(fill="x")
        tk.Label(toolrow, text="DRAW", bg=INK, fg=MUTED, font=("Segoe UI", 8, "bold"), width=7, anchor="w").pack(side="left")
        self.tool_buttons = {}
        tool_tips = {
            "pen": "Choose a round or square pen",
            "highlighter": "Draw a translucent highlight · hold Shift to keep it horizontal",
            "eraser": "Erase annotations under the brush",
            "rectangle": "Choose an outlined or filled rectangle",
            "circle": "Choose an outlined or filled circle",
            "crop": "Drag a rectangle to crop the image",
        }
        self._tool_variant_choices = {}
        self._tool_variant_buttons = {}
        self._tool_variant_vars = {}
        self._tool_variant_labels = {
            "pen": {"pen": "Round", "square_pen": "Square"},
            "rectangle": {"rectangle": "Outline", "rectangle_filled": "Filled"},
            "circle": {"circle": "Outline", "circle_filled": "Filled"},
        }
        self._make_variant_tool(
            toolrow, "pen", "Pen", (("pen", "Round pen"), ("square_pen", "Square pen")),
            tool_tips["pen"],
        )
        for name, label in (("highlighter", "Highlight"), ("eraser", "Eraser")):
            b = tk.Button(toolrow, text=label, image=self._tool_icon_images[name], compound=tk.LEFT, command=lambda n=name: self.set_tool(n), relief="flat", bd=0, padx=11, pady=7, cursor="hand2", font=("Segoe UI", 9))
            b.pack(side="left", padx=(0, 9)); self.tool_buttons[name] = b
            self._add_tooltip(b, tool_tips[name])
        tk.Label(toolrow, text="SHAPES", bg=INK, fg=MUTED, font=("Segoe UI", 8, "bold"), width=7, anchor="w").pack(side="left", padx=(10, 0))
        self._make_variant_tool(
            toolrow, "rectangle", "Rectangle",
            (("rectangle", "Outline rectangle"), ("rectangle_filled", "Filled rectangle")),
            tool_tips["rectangle"],
        )
        self._make_variant_tool(
            toolrow, "circle", "Circle",
            (("circle", "Outline circle"), ("circle_filled", "Filled circle")),
            tool_tips["circle"],
        )
        for name, label in (("crop", "Crop"),):
            b = tk.Button(toolrow, text=label, image=self._tool_icon_images[name], compound=tk.LEFT, command=lambda n=name: self.set_tool(n), relief="flat", bd=0, padx=11, pady=7, cursor="hand2", font=("Segoe UI", 9))
            b.pack(side="left", padx=(0, 9)); self.tool_buttons[name] = b
            self._add_tooltip(b, tool_tips[name])
        colorrow = tk.Frame(editorbar, bg=INK); colorrow.pack(fill="x", pady=(6, 0))
        tk.Label(colorrow, text="COLOR", bg=INK, fg=MUTED, font=("Segoe UI", 8, "bold"), width=9, anchor="w").pack(side="left")
        self.pipette_button = tk.Button(
            colorrow, text="Pick", image=self._tool_icon_images["eyedropper"], compound=tk.LEFT, command=lambda: self.set_tool("eyedropper"),
            relief="flat", bd=0, padx=9, pady=5, cursor="hand2", font=("Segoe UI", 9),
        )
        self.pipette_button.pack(side="left", padx=(0, 5))
        self._add_tooltip(self.pipette_button, "Pick the color of a pixel from the image")
        color_names = {"#000000": "Black", "#ff4b62": "Pink-red", "#f5c451": "Yellow", "#4bd4aa": "Mint", "#66a5ff": "Blue", "#ffffff": "White"}
        for c in ("#000000", "#ffffff", "#ff4b62", "#f5c451", "#4bd4aa", "#66a5ff"):
            b=tk.Button(colorrow, bg=c, activebackground=c, width=2, height=1, relief="flat", bd=0, highlightthickness=1, highlightbackground=EDGE, cursor="hand2", command=lambda color=c: self.set_color(color))
            b.pack(side="left", padx=5);self.color_buttons[c]=b
            self._add_tooltip(b, f"Select {color_names[c].lower()} annotation color")
        self.custom_color_button=tk.Canvas(colorrow, width=20, height=19, bg=PANEL2, highlightthickness=1, highlightbackground=EDGE, cursor="hand2")
        self.custom_color_button.create_image(10, 9.5, image=self._spectrum_icon_photo, tags="spectrum")
        self.custom_color_button.image = self._spectrum_icon_photo
        self.custom_color_button.pack(side="left", padx=(4,0))
        self.custom_color_button.bind("<Button-1>",lambda _e:self.choose_color())
        self.custom_color_button.bind("<Enter>",lambda _e:self.status.set("Choose any annotation color"), add="+")
        self._add_tooltip(self.custom_color_button, "Choose any custom annotation color")
        tk.Label(colorrow, text="│", bg=INK, fg=EDGE).pack(side="left", padx=(20, 14))
        tk.Label(colorrow, text="SIZE", bg=INK, fg=MUTED, font=("Segoe UI", 8, "bold")).pack(side="left", padx=(0, 8))
        self.size_slider = tk.Scale(colorrow, from_=1, to=100, variable=self.width, orient="horizontal", bg=INK, fg=TEXT, troughcolor=PANEL2, highlightthickness=0, bd=0, length=180)
        self.size_slider.pack(side="left")
        self._add_tooltip(self.size_slider, "Set pen, highlighter, and eraser width from 1 to 100 px")
        undo_button = tk.Button(colorrow, text="↶  Undo", command=self.undo, bg=INK, fg=MUTED, activebackground=PANEL, activeforeground=TEXT, relief="flat", bd=0, padx=10, cursor="hand2")
        undo_button.pack(side="right")
        self._add_tooltip(undo_button, "Undo the last image edit · Ctrl+Z")

        # Work surface
        self.stage = tk.Frame(self.root, bg=PANEL, highlightbackground=EDGE, highlightthickness=1)
        self.stage.pack(fill="both", expand=True, padx=22, pady=(0, 12))
        self.canvas = tk.Canvas(self.stage, bg="#171920", highlightthickness=0, cursor="arrow")
        self.canvas.pack(fill="both", expand=True, padx=1, pady=1)
        self._add_tooltip(self.canvas, "Draw on the image · scroll to zoom · middle-drag to pan")
        self.size_badge = None
        self.canvas.bind("<ButtonPress-1>", self._draw_start)
        self.canvas.bind("<B1-Motion>", self._draw_move)
        self.canvas.bind("<ButtonRelease-1>", self._draw_end)
        self.canvas.bind("<Configure>", self._resize_preview)
        self.canvas.bind("<Motion>", self._pointer_status)
        self.canvas.bind("<Enter>", self._canvas_enter)
        self.canvas.bind("<Leave>", self._canvas_leave)
        self.canvas.bind("<MouseWheel>", self._zoom_preview)
        self.canvas.bind("<Button-4>", lambda e: self._zoom_preview(e, 1))
        self.canvas.bind("<Button-5>", lambda e: self._zoom_preview(e, -1))
        self.canvas.bind("<ButtonPress-2>", self._pan_start)
        self.canvas.bind("<B2-Motion>", self._pan_move)
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

    def _bind_title_button_hover(self, button, hover_bg=None):
        # Resolve theme colors at window construction time. A default argument
        # would retain the dark palette captured when this module was loaded.
        if hover_bg is None:
            hover_bg = PANEL2
        normal_bg = button.cget("bg")
        button.bind("<Enter>", lambda _event, target=button, color=hover_bg: target.configure(bg=color))
        button.bind("<Leave>", lambda _event, target=button, color=normal_bg: target.configure(bg=color))

    @staticmethod
    def _add_tooltip(widget, text):
        widget._chesssnip_tooltip = ChessSnipTooltip(widget, text)

    def _theme_action_label(self):
        return "☼  Light" if self.theme_name == "dark" else "☾  Dark"

    def toggle_theme(self):
        self._close_choice_popup()
        previous_name = self.theme_name
        next_name = "light" if previous_name == "dark" else "dark"
        previous = THEMES[previous_name]
        target = THEMES[next_name]
        color_map = {previous[key].lower(): target[key] for key in THEME_FIELDS}
        old_special = THEME_SPECIAL_COLORS[previous_name]
        new_special = THEME_SPECIAL_COLORS[next_name]
        color_map.update({old_special[key].lower(): new_special[key] for key in old_special})

        self.theme_name = next_name
        _activate_palette(next_name)
        self._refresh_tool_icon_images()
        self._recolor_theme_tree(self.root, color_map)
        self.theme_button.configure(text=self._theme_action_label())
        self._style_mode_buttons()
        self._apply_dwm_theme_colors()
        try:
            SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
            SETTINGS_PATH.write_text(json.dumps({"theme": next_name}, indent=2), encoding="utf-8")
            self.status.set(f"{next_name.title()} theme selected")
        except OSError:
            self.status.set(f"{next_name.title()} theme selected · couldn't save this preference")

    def _recolor_theme_tree(self, widget, color_map):
        color_options = (
            "bg", "background", "fg", "foreground", "activebackground",
            "activeforeground", "disabledforeground", "highlightbackground",
            "highlightcolor", "insertbackground", "selectbackground",
            "selectforeground", "readonlybackground", "troughcolor", "selectcolor",
        )
        try:
            if not widget.winfo_exists():
                return
            available = widget.configure()
            for option in color_options:
                if option not in available:
                    continue
                try:
                    old_color = str(widget.cget(option)).lower()
                    if old_color in color_map:
                        widget.configure(**{option: color_map[old_color]})
                except (tk.TclError, TypeError):
                    continue
            if isinstance(widget, tk.Canvas):
                for item in widget.find_all():
                    for option in ("fill", "outline"):
                        try:
                            old_color = str(widget.itemcget(item, option)).lower()
                            if old_color in color_map:
                                widget.itemconfigure(item, **{option: color_map[old_color]})
                        except tk.TclError:
                            pass
            if isinstance(widget, (tk.Button, tk.Menubutton)):
                normal_bg = widget.cget("bg")
                hover_bg = widget.cget("activebackground")
                widget.bind("<Enter>", lambda _event, target=widget, color=hover_bg: target.configure(bg=color))
                widget.bind("<Leave>", lambda _event, target=widget, color=normal_bg: target.configure(bg=color))
            for child in widget.winfo_children():
                self._recolor_theme_tree(child, color_map)
        except tk.TclError:
            pass

    def _apply_dwm_theme_colors(self):
        try:
            user32 = ctypes.windll.user32
            user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
            user32.GetAncestor.restype = wintypes.HWND
            dwm = ctypes.windll.dwmapi.DwmSetWindowAttribute
            dwm.argtypes = (wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD)
            dwm.restype = ctypes.c_long
            stack = [self.root]
            seen = set()
            color = wintypes.DWORD(_colorref(INK))
            while stack:
                widget = stack.pop()
                try:
                    if isinstance(widget, (tk.Tk, tk.Toplevel)):
                        hwnd = user32.GetAncestor(widget.winfo_id(), 2) or widget.winfo_id()
                        if hwnd not in seen:
                            seen.add(hwnd)
                            dwm(hwnd, 34, ctypes.byref(color), ctypes.sizeof(color))
                            dwm(hwnd, 35, ctypes.byref(color), ctypes.sizeof(color))
                    stack.extend(widget.winfo_children())
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
            self.root.minsize(900,560)
            self.footer.configure(height=44)
            self.status_label.place_forget()
            self.status_label.configure(anchor="sw")
            self.status_label.pack(side="left",fill="both",expand=True)
            if not self.editorbar.winfo_manager():self.editorbar.pack(fill="x",before=self.footer)
            if not self.stage.winfo_manager():self.stage.pack(fill="both",expand=True,padx=22,pady=(0,12),before=self.footer)
            self.chess_button.pack(side="right",padx=(8,0),pady=(2,6));self.copy_button.pack(side="right",padx=(8,0),pady=(2,6));self.paint_button.pack(side="right",padx=(8,0),pady=(2,6));self.save_button.pack(side="right",pady=(2,6))
            if self.root.winfo_width()<900 or self.root.winfo_height()<560:
                default_width=min(920,max(900,self.root.winfo_screenwidth()-80))
                default_height=min(800,max(560,self.root.winfo_screenheight()-100))
                self.root.geometry(f"{default_width}x{default_height}")
        else:
            self.editorbar.pack_forget();self.stage.pack_forget()
            self.chess_button.pack_forget();self.copy_button.pack_forget();self.paint_button.pack_forget();self.save_button.pack_forget()
            self.status_label.pack_forget()
            self.footer.configure(height=36)
            self.status_label.configure(anchor="center")
            self.status_label.place(relx=0.5,rely=0.5,anchor="center")
            self.root.minsize(520,120)

    def _mode_menu(self, parent):
        self.mode_btn = tk.Button(
            parent, text="▱  Rectangle   ▾", command=self._popup_capture_mode_menu,
            bg=PANEL2, fg=TEXT, activebackground=EDGE, activeforeground=TEXT,
            relief="flat", bd=0, padx=12, pady=7, cursor="hand2",
        )
        self.mode_btn.pack(side="left")
        self._add_tooltip(self.mode_btn, "Choose rectangle, window, or full-screen capture")

    def _update_mode_label(self):
        glyph = {"Rectangle": "▱", "Window": "▣", "Full screen": "▤"}[self.capture_mode.get()]
        self.mode_btn.configure(text=f"{glyph}  {self.capture_mode.get()}   ▾")

    def _popup_capture_mode_menu(self):
        choices = tuple((mode, mode) for mode in ("Rectangle", "Window", "Full screen"))
        self._show_choice_popup(
            self.mode_btn, choices, self.capture_mode.get(), self._choose_capture_mode,
        )

    def _choose_capture_mode(self, mode):
        self.capture_mode.set(mode)
        self._update_mode_label()

    def _make_variant_tool(self, parent, group, label, choices, tooltip):
        variant = tk.StringVar(master=self.root, value=choices[0][0])
        button = tk.Button(
            parent, text=label, image=self._tool_icon_images[group], compound=tk.LEFT,
            command=lambda selected=group: self._popup_tool_variant_menu(selected),
            relief="flat", bd=0, padx=10, pady=7, cursor="hand2",
            font=("Segoe UI", 9),
        )
        button.pack(side="left", padx=(0, 9))
        self.tool_buttons[group] = button
        self._tool_variant_buttons[group] = (button, label)
        self._tool_variant_vars[group] = variant
        self._tool_variant_choices[group] = tuple(choices)
        self._add_tooltip(button, tooltip)

    def _popup_tool_variant_menu(self, group):
        button = self._tool_variant_buttons[group][0]
        tooltip = getattr(button, "_chesssnip_tooltip", None)
        if tooltip:
            tooltip.hide()
        self._show_choice_popup(
            button, self._tool_variant_choices[group],
            self._tool_variant_vars[group].get(),
            lambda mode: self._choose_tool_variant(group, mode),
        )

    def _choose_tool_variant(self, group, mode):
        self._close_choice_popup()
        self._tool_variant_vars[group].set(mode)
        self.set_tool(mode)

    def _show_choice_popup(self, anchor, choices, selected, on_choose):
        """Show an in-window, modeless menu so canvas input never gets grabbed."""
        self._close_choice_popup(refresh_cursor=False)
        popup = tk.Frame(
            self.root, bg=EDGE, bd=0, highlightthickness=1,
            highlightbackground=EDGE, highlightcolor=EDGE,
        )
        self._active_choice_popup = popup
        body = tk.Frame(popup, bg=PANEL2, bd=0)
        body.pack(fill="both", expand=True, padx=1, pady=1)
        for value, label in choices:
            is_selected = value == selected
            button = tk.Button(
                body, text=("✓  " if is_selected else "   ") + label,
                command=lambda choice=value: (self._close_choice_popup(), on_choose(choice)),
                bg=ACCENT if is_selected else PANEL2,
                fg="white" if is_selected else TEXT,
                activebackground=ACCENT if is_selected else EDGE,
                activeforeground="white" if is_selected else TEXT,
                relief="flat", bd=0, anchor="w", padx=10, pady=6,
                font=("Segoe UI", 9), cursor="hand2", takefocus=0,
            )
            button.pack(fill="x")
            if not is_selected:
                button.bind(
                    "<Enter>", lambda _e, item=button: item.configure(bg=EDGE),
                )
                button.bind(
                    "<Leave>", lambda _e, item=button: item.configure(bg=PANEL2),
                )
        popup.update_idletasks()
        root_x, root_y = self.root.winfo_rootx(), self.root.winfo_rooty()
        x = anchor.winfo_rootx() - root_x
        y = anchor.winfo_rooty() - root_y + anchor.winfo_height()
        popup_width = max(anchor.winfo_width(), popup.winfo_reqwidth(), 190)
        popup_height = popup.winfo_reqheight()
        root_width, root_height = self.root.winfo_width(), self.root.winfo_height()
        if x + popup_width > root_width:
            x = max(0, root_width - popup_width)
        if y + popup_height > root_height:
            y = max(0, anchor.winfo_rooty() - root_y - popup_height)
        popup.place(x=x, y=y, width=popup_width)
        popup.lift()

    def _close_choice_popup(self, refresh_cursor=True):
        popup = self._active_choice_popup
        if popup is None:
            return
        self._active_choice_popup = None
        try:
            popup.destroy()
        except tk.TclError:
            pass
        if refresh_cursor:
            try:
                self.root.after_idle(self._refresh_brush_cursor_from_pointer)
            except tk.TclError:
                pass

    def _choice_popup_motion(self, event):
        if self._active_choice_popup is None:
            return
        try:
            if not self.canvas.winfo_exists() or not self.canvas.winfo_ismapped():
                return
            left, top = self.canvas.winfo_rootx(), self.canvas.winfo_rooty()
            if (left <= event.x_root < left + self.canvas.winfo_width()
                    and top <= event.y_root < top + self.canvas.winfo_height()):
                self._close_choice_popup()
        except tk.TclError:
            pass

    def _choice_popup_click_away(self, event):
        popup = self._active_choice_popup
        if popup is None:
            return
        widget = event.widget
        while widget is not None:
            if widget is popup:
                return
            widget = getattr(widget, "master", None)
        self._close_choice_popup()

    def _choice_popup_escape(self, _event):
        if self._active_choice_popup is not None:
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
        group_modes = {
            "pen": ("pen", "square_pen"),
            "rectangle": ("rectangle", "rectangle_filled"),
            "circle": ("circle", "circle_filled"),
        }
        for n, b in self.tool_buttons.items():
            active = self.tool.get() in group_modes.get(n, (n,))
            b.configure(bg=ACCENT if active else PANEL2, fg="white" if active else TEXT, activebackground=ACCENT if active else EDGE, activeforeground="white")
        for group, (button, label) in self._tool_variant_buttons.items():
            variant = self._tool_variant_labels[group][self._tool_variant_vars[group].get()]
            button.configure(text=f"{label} · {variant}  ▾", image=self._tool_icon_images[group], compound=tk.LEFT)
        if getattr(self, "pipette_button", None):
            active = self.tool.get() == "eyedropper"
            self.pipette_button.configure(
                bg=ACCENT if active else PANEL2,
                fg="white" if active else TEXT,
                activebackground=ACCENT if active else EDGE,
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
        annotation_tools = {
            "pen", "square_pen", "highlighter", "eraser",
            "rectangle", "rectangle_filled", "circle", "circle_filled",
        }
        previous = self.tool.get()
        if name == "eyedropper":
            if previous in annotation_tools:
                self._eyedropper_return_tool = previous
        elif name in annotation_tools:
            self._eyedropper_return_tool = name
            for group, variants in self._tool_variant_labels.items():
                if name in variants:
                    self._tool_variant_vars[group].set(name)
        self.tool.set(name)
        drawing_cursor=name in ("pen", "square_pen", "highlighter", "eraser")
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
            "square_pen": "Draw with a square pen · hold Shift for a straight horizontal line",
            "highlighter": "Drag to highlight · hold Shift to keep it horizontal",
            "eraser": "Click or drag across marks to erase them · Ctrl+Z to undo",
            "rectangle": "Drag to draw an outlined rectangle",
            "rectangle_filled": "Drag to draw a filled rectangle",
            "circle": "Drag to draw an outlined circle",
            "circle_filled": "Drag to draw a filled circle",
            "crop": "Drag a rectangle to crop the image",
            "eyedropper": "Click a pixel in the image to select its color",
        }
        self.status.set(messages[name])
        self._update_brush_cursor()
        if self._size_preview_items:
            self._show_size_preview()

    def set_color(self, color):
        self.color.set(color)
        for swatch, button in self.color_buttons.items():
            selected = swatch.lower() == color.lower()
            luminance = sum(int(swatch[i:i + 2], 16) * weight for i, weight in ((1, 0.299), (3, 0.587), (5, 0.114)))
            button.configure(
                relief=tk.FLAT, bd=0, text="✓" if selected else "",
                fg="#111318" if luminance > 145 else "white",
                font=("Segoe UI", 8, "bold"),
                highlightthickness=3 if selected else 1,
                highlightbackground=ACCENT if selected else EDGE,
                highlightcolor=ACCENT if selected else EDGE,
            )
        if self.custom_color_button:
            is_custom = color.lower() not in {swatch.lower() for swatch in self.color_buttons}
            self.custom_color_button.configure(
                highlightthickness=3 if is_custom else 1,
                highlightbackground=ACCENT if is_custom else EDGE,
                highlightcolor=ACCENT if is_custom else EDGE,
            )
            self.custom_color_button.delete("chosen-color")

    def choose_color(self):
        chosen=colorchooser.askcolor(color=self.color.get(), parent=self.root, title="Choose annotation color")[1]
        if chosen:
            self.set_color(chosen)

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
        cursor_info = _ICONINFO(False, size // 2, size // 2, mask_bitmap, color_bitmap)
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
            self.root.deiconify(); messagebox.showerror("Capture failed", str(e)); return
        overlay = tk.Toplevel(self.root); overlay.overrideredirect(True); overlay.attributes("-topmost", True)
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
        banner = cv.create_text(shot.width / 2, 26, text="Drag to select a region     ·     Enter to capture     ·     Esc to cancel", fill="white", font=("Segoe UI Semibold", 12), tags="banner")

        box = {"rect": None, "start": None}
        selection_preview = {"after": None, "photo": None, "item": None, "photo_size": None}

        def refresh_selection_preview():
            selection_preview["after"] = None
            rect = box["rect"]
            if not rect or rect[2] <= rect[0] or rect[3] <= rect[1]:
                return
            a, b, c, d = rect
            crop = shot.crop(rect)
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

        mode = self.capture_mode.get()
        if mode == "Full screen":
            box["start"] = (0, 0); box["rect"] = (0, 0, shot.width, shot.height)
        
        def down(e):
            apply_capture_cursor()
            box["start"] = (max(0, min(shot.width, e.x)), max(0, min(shot.height, e.y)))
            box["rect"] = None; cv.delete("selection")
            cv.delete("selection-image")
            selection_preview["photo"] = None
            selection_preview["item"] = None
            selection_preview["photo_size"] = None
            return "break"

        def drag(e):
            apply_capture_cursor()
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
            stop_selection_preview()
            release_capture_cursor()
            rect = box["rect"]
            if mode == "Window" and rect:
                # Hide our selector briefly so Windows resolves the real window
                # underneath the point the user selected.
                px, py = x0 + (rect[0]+rect[2])//2, y0 + (rect[1]+rect[3])//2
                overlay.withdraw()
                def finish_window():
                    try:
                        u=ctypes.windll.user32
                        u.WindowFromPoint.argtypes=[wintypes.POINT];u.WindowFromPoint.restype=wintypes.HWND
                        u.GetAncestor.argtypes=[wintypes.HWND,wintypes.UINT];u.GetAncestor.restype=wintypes.HWND
                        u.GetWindowRect.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.RECT)];u.GetWindowRect.restype=wintypes.BOOL
                        pt=wintypes.POINT(px,py); hwnd=u.WindowFromPoint(pt)
                        hwnd=u.GetAncestor(hwnd,2) or hwnd
                        wr=wintypes.RECT()
                        if hwnd and u.GetWindowRect(hwnd,ctypes.byref(wr)):
                            r=(max(0,wr.left-x0),max(0,wr.top-y0),min(shot.width,wr.right-x0),min(shot.height,wr.bottom-y0))
                        else:r=rect
                        if r[2]-r[0]<3 or r[3]-r[1]<3:r=rect
                        self.image=shot.crop(r).convert("RGB");self.strokes.clear();self.history.clear();self.zoom_factor=1.0
                        self.canvas.xview_moveto(0);self.canvas.yview_moveto(0)
                        self._set_editor_visible(True)
                        self.root.deiconify();self.root.state("normal");self.root.lift();self.root.focus_force()
                        self.status.set(f"Captured window  ·  {self.image.width} × {self.image.height} px");self._render_preview();self._copy_capture_to_clipboard()
                    except Exception as exc:
                        self.root.deiconify();self.root.state("normal");messagebox.showerror("Capture failed",str(exc))
                    overlay.destroy()
                self.root.after(180,finish_window)
                return
            if rect and rect[2]-rect[0] >= 3 and rect[3]-rect[1] >= 3:
                self.image = shot.crop(rect).convert("RGB"); self.strokes.clear(); self.history.clear(); self.zoom_factor=1.0
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
            stop_selection_preview()
            release_capture_cursor()
            self.root.deiconify(); self.root.state("normal"); self.status.set("Capture cancelled"); overlay.destroy()
            return "break"
        def release(e):
            apply_capture_cursor(); drag(e); finish(); return "break"
        if mode == "Full screen": self.root.after(80, finish)
        cv.bind("<ButtonPress-1>", down); cv.bind("<B1-Motion>", drag); cv.bind("<ButtonRelease-1>", release)
        cv.bind("<Enter>", apply_capture_cursor, add="+")
        cv.bind("<Motion>", apply_capture_cursor, add="+")
        overlay.bind("<Return>", finish); overlay.bind("<Escape>", cancel)
        cv.bind("<Return>", finish); cv.bind("<Escape>", cancel)
        overlay.bind("<Alt-F4>", cancel); cv.bind("<Alt-F4>", cancel)
        overlay.lift(); overlay.focus_force(); cv.focus_set(); cv.image_ref = shot_tk
        overlay.after_idle(apply_capture_cursor)

    @staticmethod
    def _virtual_origin():
        u = ctypes.windll.user32
        return u.GetSystemMetrics(76), u.GetSystemMetrics(77)

    def _resize_preview(self, event=None):
        if not self.image: return
        size=(event.width,event.height) if event else (self.canvas.winfo_width(),self.canvas.winfo_height())
        if size==self._last_canvas_size:return
        self._last_canvas_size=size
        if self._preview_redraw_after:
            try:self.root.after_cancel(self._preview_redraw_after)
            except tk.TclError:pass
        self._preview_redraw_after=self.root.after_idle(self._render_preview)

    @staticmethod
    def _square_brush_points(points, brush_width):
        """Yield axis-aligned square-brush centers with no gaps between samples."""
        points = list(points)
        if not points:
            return
        yield points[0]
        spacing = max(1.0, float(brush_width) * 0.4)
        for start, end in zip(points, points[1:]):
            dx, dy = end[0] - start[0], end[1] - start[1]
            distance = math.hypot(dx, dy)
            steps = max(1, math.ceil(distance / spacing))
            for index in range(1, steps + 1):
                fraction = index / steps
                yield (start[0] + dx * fraction, start[1] + dy * fraction)

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
        side=max(6.0,self.width.get()*self.fit_scale)
        half=side/2
        square=self.tool.get() in ("square_pen","rectangle","rectangle_filled","crop")
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

    def _draw_square_pen_dabs(self, points, color, width, tags):
        ox, oy = self.display_origin
        side = max(1.0, width * self.fit_scale)
        half = side / 2
        for px, py in self._square_brush_points(points, width):
            cx, cy = px * self.fit_scale + ox, py * self.fit_scale + oy
            self.canvas.create_rectangle(
                cx - half, cy - half, cx + half, cy + half,
                fill=color, outline=color, tags=tags,
            )

    def _render_preview(self):
        if not self.image: return
        self._hide_size_preview()
        w, h = max(100, self.canvas.winfo_width()), max(100, self.canvas.winfo_height())
        base_scale = min((w - 42) / self.image.width, (h - 42) / self.image.height, 1.0)
        max_render_scale = min(3.0, (40_000_000 / (self.image.width*self.image.height))**0.5)
        scale = max(0.02, min(base_scale*self.zoom_factor, max_render_scale)); self.fit_scale = scale
        iw, ih = max(1, int(self.image.width*scale)), max(1, int(self.image.height*scale))
        self.display_origin = (max((w-iw)//2,0), max((h-ih)//2,0))
        ox, oy = self.display_origin
        self.scroll_size=(max(w,ox+iw),max(h,oy+ih))
        preview = self.image.resize((iw,ih), Image.Resampling.LANCZOS)
        self.photo = ImageTk.PhotoImage(preview)
        self.canvas.delete("all")
        self._drawing_preview_item = None
        self._drawing_preview_photo = None
        self._shape_preview_photos = []
        self.brush_cursor_items = None
        self.canvas.configure(scrollregion=(0,0,*self.scroll_size))
        self.canvas.create_rectangle(ox-1,oy-1,ox+iw+1,oy+ih+1,fill="#0c0d11",outline=EDGE)
        self.canvas.create_image(ox,oy,image=self.photo,anchor="nw",tags="preview")
        for s in self.strokes:
            if s["tool"] == "square_pen":
                points=[tuple(s["points"][i:i+2]) for i in range(0,len(s["points"])-1,2)]
                self._draw_square_pen_dabs(points,s["color"],s["width"],"mark")
                continue
            coords = [v*scale + (ox if i%2==0 else oy) for i,v in enumerate(s["points"])]
            if len(coords) >= 4 and s["tool"] in ("rectangle", "rectangle_filled", "circle", "circle_filled"):
                filled = s["tool"].endswith("_filled")
                bounds=(
                    min(coords[0],coords[2]), min(coords[1],coords[3]),
                    max(coords[0],coords[2]), max(coords[1],coords[3]),
                )
                if s["tool"].startswith("circle"):
                    patch,left,top=self._ellipse_patch(
                        bounds,s["color"],255,s["width"]*scale,filled,
                    )
                    photo=ImageTk.PhotoImage(patch,master=self.canvas)
                    self._shape_preview_photos.append(photo)
                    self.canvas.create_image(left,top,image=photo,anchor="nw",tags="mark")
                else:
                    self.canvas.create_rectangle(
                        *bounds,outline=s["color"],fill=s["color"] if filled else "",
                        width=max(1,s["width"]*scale),tags="mark",
                    )
            elif len(coords) >= 4:
                self.canvas.create_line(
                    *coords, fill=s["color"], width=max(1,s["width"]*scale),
                    capstyle=tk.ROUND, joinstyle=tk.ROUND,
                    smooth=False, stipple="gray50" if s["tool"]=="highlighter" else "", tags="mark",
                )
        base_scale=min((w-42)/self.image.width,(h-42)/self.image.height,1.0)
        self.size_badge=self.canvas.create_text(self.canvas.canvasx(w-12),self.canvas.canvasy(12),text=f"{self.image.width} × {self.image.height} px · {round(scale/base_scale*100)}%",fill=MUTED,font=("Segoe UI",9),anchor="ne",tags="size-badge")
        self._update_brush_cursor()

    def _update_size_badge(self):
        if not self.image or not self.size_badge:return
        w,h=self.canvas.winfo_width(),self.canvas.winfo_height()
        base=min((max(100,w)-42)/self.image.width,(max(100,h)-42)/self.image.height,1.0)
        self.canvas.coords(self.size_badge,self.canvas.canvasx(w-12),self.canvas.canvasy(12))
        self.canvas.itemconfigure(self.size_badge,text=f"{self.image.width} × {self.image.height} px · {round(self.fit_scale/base*100)}%")

    def _update_brush_cursor(self):
        brush_tools = ("pen", "square_pen", "highlighter", "eraser")
        if not self.image or not self.pointer_inside_canvas or self.tool.get() not in brush_tools:
            self.canvas.delete("brush-cursor")
            self.brush_cursor_items = None
            self._restore_system_cursor()
            return
        x,y=self._pointer_canvas_pos
        diameter=self.width.get()*self.fit_scale
        radius=max(3,diameter/2)
        create_cursor = self.canvas.create_rectangle if self.tool.get() == "square_pen" else self.canvas.create_oval
        if not self.brush_cursor_items:
            self.brush_cursor_items=(create_cursor(0,0,0,0,outline="#15171c",width=2,tags="brush-cursor"),create_cursor(0,0,0,0,outline="#ffffff",width=1,tags="brush-cursor"))
        outer,inner=self.brush_cursor_items
        if ("rectangle" in self.canvas.type(outer)) != (self.tool.get() == "square_pen"):
            self.canvas.delete(outer)
            self.canvas.delete(inner)
            self.brush_cursor_items=(create_cursor(0,0,0,0,outline="#15171c",width=2,tags="brush-cursor"),create_cursor(0,0,0,0,outline="#ffffff",width=1,tags="brush-cursor"))
            outer,inner=self.brush_cursor_items
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
        self._restore_system_cursor()

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
        self._update_size_badge()

    @staticmethod
    def _distance_to_segment(px, py, ax, ay, bx, by):
        dx,dy=bx-ax,by-ay
        length=dx*dx+dy*dy
        t=0 if not length else max(0,min(1,((px-ax)*dx+(py-ay)*dy)/length))
        return ((px-(ax+t*dx))**2+(py-(ay+t*dy))**2)**0.5

    def _erase_at(self, x, y):
        radius=max(1,self.width.get()/2)
        kept=[]; erased=False
        for stroke in self.strokes:
            pts=stroke["points"]
            reach=radius+stroke["width"]/2
            if stroke["tool"] in ("rectangle_filled", "circle_filled") and len(pts)>=4:
                x1,y1,x2,y2=pts[:4]
                if stroke["tool"] == "rectangle_filled":
                    hit = min(x1,x2)-reach <= x <= max(x1,x2)+reach and min(y1,y2)-reach <= y <= max(y1,y2)+reach
                else:
                    cx,cy=(x1+x2)/2,(y1+y2)/2
                    rx,ry=abs(x2-x1)/2+reach,abs(y2-y1)/2+reach
                    hit = ((x-cx)/max(rx,0.5))**2 + ((y-cy)/max(ry,0.5))**2 <= 1
                if hit:
                    if not self._erase_changed:self._push_history();self._erase_changed=True
                    erased=True
                    continue
                path=[]
            elif stroke["tool"] in ("rectangle", "rectangle_filled") and len(pts)>=4:
                x1,y1,x2,y2=pts[:4]
                path=[(x1,y1),(x2,y1),(x2,y2),(x1,y2),(x1,y1)]
            elif stroke["tool"] in ("circle", "circle_filled") and len(pts)>=4:
                x1,y1,x2,y2=pts[:4]; cx,cy=(x1+x2)/2,(y1+y2)/2; rx,ry=abs(x2-x1)/2,abs(y2-y1)/2
                path=[(cx+rx*math.cos(i*math.tau/48),cy+ry*math.sin(i*math.tau/48)) for i in range(49)]
            else:
                path=[tuple(pts[i:i+2]) for i in range(0,len(pts)-1,2)]
            hit=any(self._distance_to_segment(x,y,*a,*b)<=reach for a,b in zip(path,path[1:]))
            if hit:
                if not self._erase_changed:self._push_history();self._erase_changed=True
                erased=True
            else:kept.append(stroke)
        if erased:
            self.strokes=kept; self._render_preview(); self.status.set("Annotation erased · Ctrl+Z to undo");self._schedule_auto_clipboard()

    def _draw_start(self, e):
        if not self.image: return
        self._pointer_canvas_pos=(self.canvas.canvasx(e.x),self.canvas.canvasy(e.y));self._update_brush_cursor()
        x,y = self._to_image(e.x,e.y)
        if self.tool.get() == "eyedropper":
            if not (0 <= x < self.image.width and 0 <= y < self.image.height):
                self.status.set("Pick a color from inside the image")
                return
            px,py=int(x),int(y)
            picked=self._composited().convert("RGB").getpixel((px,py))
            color="#%02x%02x%02x" % picked
            return_tool=self._eyedropper_return_tool
            self.set_color(color)
            self.set_tool(return_tool)
            self.status.set(f"Picked {color.upper()} · ready to draw")
            return
        if self.tool.get()=="crop":
            if not (0 <= x < self.image.width and 0 <= y < self.image.height):return
            self._crop_start=(self.canvas.canvasx(e.x),self.canvas.canvasy(e.y)); self.canvas.delete("cropbox"); return
        if self.tool.get()=="eraser":
            self._erasing=True;self._erase_changed=False;self._erase_at(x,y)
            return
        self._push_history()
        self._drawing={"points":[x,y],"color":self.color.get(),"width":self.width.get(),"tool":self.tool.get()}
        if self.tool.get() in ("rectangle", "rectangle_filled", "circle", "circle_filled"):
            if self.tool.get().startswith("rectangle"):
                cx,cy=x*self.fit_scale+self.display_origin[0],y*self.fit_scale+self.display_origin[1]
                filled=self.tool.get().endswith("_filled")
                self._drawing_preview_item=self.canvas.create_rectangle(
                    cx,cy,cx,cy,outline=self.color.get(),
                    fill=self.color.get() if filled else "",
                    width=max(1,self.width.get()*self.fit_scale),tags="active-mark",
                )
        elif self.tool.get() == "square_pen":
            self._draw_square_pen_dabs([(x,y)],self.color.get(),self.width.get(),"active-mark")
            self._update_brush_cursor()

    def _draw_move(self,e):
        self._pointer_canvas_pos=(self.canvas.canvasx(e.x),self.canvas.canvasy(e.y));self._update_brush_cursor()
        if self._erasing:
            x,y=self._to_image(e.x,e.y)
            self._erase_at(x,y)
            return
        if self._crop_start:
            a,b=self._crop_start; self.canvas.delete("cropbox"); self.canvas.create_rectangle(a,b,self.canvas.canvasx(e.x),self.canvas.canvasy(e.y),outline="#aaa2ff",width=2,dash=(5,3),tags="cropbox"); return
        if not self._drawing:return
        drawing_tool=self._drawing["tool"]
        x,y=self._to_image(e.x,e.y)
        if drawing_tool in ("pen", "square_pen", "highlighter") and e.state & 0x0001:
            y=self._drawing["points"][1]
        pts=self._drawing["points"]
        if drawing_tool in ("rectangle", "rectangle_filled", "circle", "circle_filled"):
            pts[:]=[pts[0],pts[1],x,y]
            coords=[v*self.fit_scale+(self.display_origin[0] if i%2==0 else self.display_origin[1]) for i,v in enumerate(pts)]
            bounds=(
                min(coords[0],coords[2]),min(coords[1],coords[3]),
                max(coords[0],coords[2]),max(coords[1],coords[3]),
            )
            if drawing_tool.startswith("circle"):
                patch,left,top=self._ellipse_patch(
                    bounds,self._drawing["color"],255,
                    self._drawing["width"]*self.fit_scale,
                    drawing_tool.endswith("_filled"),
                )
                self._drawing_preview_photo=ImageTk.PhotoImage(patch,master=self.canvas)
                if self._drawing_preview_item:
                    self.canvas.coords(self._drawing_preview_item,left,top)
                    self.canvas.itemconfigure(self._drawing_preview_item,image=self._drawing_preview_photo)
                else:
                    self._drawing_preview_item=self.canvas.create_image(
                        left,top,image=self._drawing_preview_photo,anchor="nw",tags="active-mark",
                    )
            elif self._drawing_preview_item:
                self.canvas.coords(self._drawing_preview_item,*bounds)
            else:
                creator=self.canvas.create_rectangle if drawing_tool.startswith("rectangle") else self.canvas.create_oval
                filled=drawing_tool.endswith("_filled")
                self._drawing_preview_item=creator(
                    *bounds,outline=self._drawing["color"],
                    fill=self._drawing["color"] if filled else "",
                    width=max(1,self._drawing["width"]*self.fit_scale),tags="active-mark",
                )
            self._update_brush_cursor()
            return
        if drawing_tool == "square_pen":
            previous=(pts[-2],pts[-1])
            samples=list(self._square_brush_points((previous,(x,y)),self._drawing["width"]))[1:]
            for sample in samples:
                pts.extend(sample)
                self._draw_square_pen_dabs([sample],self._drawing["color"],self._drawing["width"],"active-mark")
            self._update_brush_cursor()
            return
        pts.extend((x,y))
        coords=[v*self.fit_scale+(self.display_origin[0] if i%2==0 else self.display_origin[1]) for i,v in enumerate(pts)]
        square=drawing_tool=="square_pen"
        self.canvas.delete("active-mark")
        self.canvas.create_line(
            *coords,fill=self._drawing["color"],width=max(1,self._drawing["width"]*self.fit_scale),
            capstyle="projecting" if square else tk.ROUND,
            joinstyle="miter" if square else tk.ROUND,smooth=False,
            stipple="gray50" if drawing_tool=="highlighter" else "",tags="active-mark",
        )
        self._update_brush_cursor()

    def _draw_end(self,e):
        self._pointer_canvas_pos=(self.canvas.canvasx(e.x),self.canvas.canvasy(e.y));self._update_brush_cursor()
        if self._erasing:self._erasing=False;return
        if self._crop_start:
            a,b=self._crop_start; self._crop_start=None; self.canvas.delete("cropbox")
            ox,oy=self.display_origin
            p1=((a-ox)/self.fit_scale,(b-oy)/self.fit_scale); p2=self._to_image(e.x,e.y)
            x1,y1=max(0,int(min(p1[0],p2[0]))),max(0,int(min(p1[1],p2[1])))
            x2,y2=min(self.image.width,int(max(p1[0],p2[0]))),min(self.image.height,int(max(p1[1],p2[1])))
            if x2-x1>=8 and y2-y1>=8:
                self._push_history(); self.image=self._composited().crop((x1,y1,x2,y2)); self.strokes=[]; self.status.set(f"Cropped to {x2-x1} × {y2-y1} px"); self._render_preview();self._schedule_auto_clipboard()
        elif self._drawing:
            if len(self._drawing["points"])<4:
                p=self._drawing["points"]; p.extend((p[0],p[1]))
            self.strokes.append(self._drawing); self._drawing=None; self._drawing_preview_item=None; self._drawing_preview_photo=None; self._render_preview();self._schedule_auto_clipboard()

    def _push_history(self):
        self.history.append((self.image.copy() if self.image else None, [dict(s,points=list(s["points"])) for s in self.strokes]))
        if len(self.history)>30:self.history.pop(0)

    def undo(self):
        if not self.history:return
        self.image,self.strokes=self.history.pop(); self._render_preview(); self.status.set("Last change undone");self._schedule_auto_clipboard()

    def _composited(self):
        return self._composite_image(self.image,self.strokes)

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
        from PIL import ImageDraw
        result=image.copy().convert("RGBA")
        marks=Image.new("RGBA",result.size,(0,0,0,0))
        draw=ImageDraw.Draw(marks)
        for s in strokes:
            rgb=tuple(bytes.fromhex(s["color"].lstrip("#")))
            alpha=128 if s["tool"]=="highlighter" else 255
            if s["tool"] in ("rectangle", "rectangle_filled", "circle", "circle_filled"):
                x1,y1,x2,y2=map(round,s["points"][:4]);bounds=(min(x1,x2),min(y1,y2),max(x1,x2),max(y1,y2))
                filled=s["tool"].endswith("_filled")
                if s["tool"].startswith("rectangle"):
                    draw.rectangle(
                        bounds, fill=(*rgb,alpha) if filled else None,
                        outline=(*rgb,alpha), width=max(1,int(s["width"])),
                    )
                else:
                    ellipse_bounds=(
                        min(s["points"][0],s["points"][2]),
                        min(s["points"][1],s["points"][3]),
                        max(s["points"][0],s["points"][2]),
                        max(s["points"][1],s["points"][3]),
                    )
                    shape,left,top=ChessSnip._ellipse_patch(
                        ellipse_bounds,s["color"],alpha,s["width"],filled,result.size,
                    )
                    marks.alpha_composite(shape,dest=(left,top))
            else:
                points=[tuple(map(round,s["points"][i:i+2])) for i in range(0,len(s["points"])-1,2)]
                line_width=max(1,int(s["width"]))
                if s["tool"] == "square_pen":
                    for px,py in ChessSnip._square_brush_points(points,line_width):
                        left=round(px-line_width/2)
                        top=round(py-line_width/2)
                        draw.rectangle((left,top,left+line_width-1,top+line_width-1),fill=(*rgb,alpha))
                else:
                    draw.line(points,fill=(*rgb,alpha),width=line_width,joint="curve")
                    if points:
                        radius=line_width/2
                        for px,py in (points[0],points[-1]):
                            draw.ellipse((px-radius,py-radius,px+radius,py+radius),fill=(*rgb,alpha))
        return Image.alpha_composite(result,marks).convert("RGB")

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
        strokes=list(self.strokes)
        def render_and_copy():
            handle=None
            try:
                buffer=io.BytesIO();self._composite_image(image,strokes).convert("RGB").save(buffer,"BMP")
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
        except Exception as exc:messagebox.showerror("Couldn't update clipboard",str(exc))

    def save_as(self):
        if not self.image: self.status.set("Take a snip first to save an image"); return
        path=filedialog.asksaveasfilename(parent=self.root,title="Save snip",initialdir=str(ROOT/"Pictures"),initialfile="ChessSnip_"+datetime.now().strftime("%Y%m%d_%H%M%S")+".png",defaultextension=".png",filetypes=(("PNG image","*.png"),("JPEG image","*.jpg *.jpeg"),("Bitmap image","*.bmp"),("All files","*.*")))
        if not path:return
        try:
            img=self._composited(); ext=Path(path).suffix.lower()
            if ext in (".jpg",".jpeg"):img.save(path,quality=95)
            else:img.save(path)
            self.status.set("Saved  ·  "+path)
        except Exception as e:messagebox.showerror("Couldn't save image",str(e))

    def copy_image(self):
        if not self.image:self.status.set("Take a snip first to copy an image");return
        try:
            bmp=io.BytesIO();self._composited().convert("RGB").save(bmp,"BMP"); data=bmp.getvalue()[14:]
            self._set_clipboard_dib(data)
            self.status.set("Image copied to clipboard  ·  paste anywhere")
        except Exception as e:messagebox.showerror("Couldn't copy image",str(e))

    def chess_to_pgn(self):
        if not self.image:
            self.status.set("Take a snip of a chess position first")
            return
        self._recognize_chess_async(self._composited().copy(), board_crop=False)

    def _recognize_chess_async(self, image, board_crop):
        if str(self.chess_button.cget("state")) == str(tk.DISABLED):
            return
        self.chess_button.configure(state=tk.DISABLED)
        self.status.set("Recognizing the chess position on this PC…")

        def worker():
            recognition_error_type = None
            try:
                # Keep NumPy and ONNX Runtime out of the normal app startup path.
                from chess_recognition import ChessRecognitionError, recognize_position
                recognition_error_type = ChessRecognitionError
                result = recognize_position(image, board_crop=board_crop)
                error = None
            except Exception as exc:
                result, error = None, exc
            is_recognition_error = bool(
                error is not None
                and recognition_error_type is not None
                and isinstance(error, recognition_error_type)
            )
            try:
                self.root.after(0, lambda r=result, err=error, chess_error=is_recognition_error: self._finish_chess_recognition(image, board_crop, r, err, chess_error))
            except tk.TclError:
                pass

        threading.Thread(target=worker, daemon=True).start()

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
        window.minsize(min(960, review_width), min(780, review_height))
        window.protocol("WM_DELETE_WINDOW", window.destroy)

        # Match the main app's custom titlebar, window controls, and rounded frame.
        titlebar = tk.Frame(window, bg=PANEL, height=40, highlightbackground=EDGE, highlightthickness=1)
        titlebar.pack(fill="x")
        brand = tk.Frame(titlebar, bg=PANEL)
        brand.pack(side="left", padx=(14, 0))
        tk.Label(brand, text="♟", bg=ACCENT, fg="white", font=("Segoe UI Symbol", 12, "bold"), width=2, pady=1).pack(side="left", padx=(0, 9), pady=4)
        tk.Label(brand, text="ChessSnip  ·  Chess → PGN", bg=PANEL, fg=TEXT, font=("Segoe UI Semibold", 10)).pack(side="left")
        title_controls = tk.Frame(titlebar, bg=PANEL)
        title_controls.pack(side="right", fill="y")

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
        self._bind_title_button_hover(minimize_button)
        self._add_tooltip(minimize_button, "Minimize the chess review")
        review_maximize = {"maximized": False, "restore_geometry": None}
        maximize_button = tk.Button(title_controls, text="□", bg=PANEL, fg=TEXT, activebackground=PANEL2, activeforeground=TEXT, relief="flat", bd=0, width=5, cursor="hand2", font=("Segoe UI", 11))
        maximize_button.pack(side="left", fill="y")
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
        self._bind_title_button_hover(close_button, "#c42b1c")
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
        tk.Label(window, text="This exports the current position only. A still image can't reveal earlier moves, whose turn it is, or whether a king or rook moved earlier.", bg=INK, fg=MUTED, wraplength=review_width - 48, justify="left").pack(anchor="w", padx=24, pady=(0, 15))

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
        size = board_size
        piece_font_size = max(24, int(cell * .72))
        coordinate_margin = 18
        board_canvas = tk.Canvas(right, width=size + coordinate_margin, height=size + coordinate_margin, bg=INK, highlightthickness=0)
        board_canvas.pack(anchor="w")
        self._add_tooltip(board_canvas, "Click a square to choose a piece · right-click also opens the piece menu")
        rows = list(recognition["rows"])
        board_orientation = tk.StringVar(value="black" if recognition.get("orientation_flipped") else "white")
        selected_orientation = {"value": board_orientation.get()}
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

        halfmove = tk.StringVar(value="0")
        fullmove = tk.StringVar(value="1")
        fen_var = tk.StringVar()
        def current_fen():
            rights = "".join(key for key in "KQkq" if castle_vars[key].get()) or "-"
            half_count, move_number = int(halfmove.get()), int(fullmove.get())
            if half_count < 0 or move_number < 1:
                raise ValueError("Half-moves must be zero or more, and the move number must be at least 1.")
            return f"{self._compress_fen_rows(rows)} {turn.get()} {rights} - {half_count} {move_number}"

        selected_square = {"position": None}

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
                        if piece.isupper():
                            board_canvas.create_text(x0 + cell / 2 + 1, y0 + cell / 2 + 2, text=text, fill="#373737", font=("Segoe UI Symbol", piece_font_size, "bold"))
                            board_canvas.create_text(x0 + cell / 2, y0 + cell / 2, text=text, fill="#fffdf8", font=("Segoe UI Symbol", piece_font_size, "bold"))
                        else:
                            board_canvas.create_text(x0 + cell / 2, y0 + cell / 2, text=text, fill="#202027", font=("Segoe UI Symbol", piece_font_size, "bold"))
                    if selected_square["position"] == (data_row, data_col):
                        board_canvas.create_rectangle(
                            x0 + 2, y0 + 2, x0 + cell - 2, y0 + cell - 2,
                            outline="#ff4057", width=3,
                        )
            file_labels = "hgfedcba" if source_view["pieces_flipped"] else "abcdefgh"
            rank_labels = "12345678" if source_view["pieces_flipped"] else "87654321"
            for index, label in enumerate(file_labels):
                board_canvas.create_text(
                    board_left + index * cell + cell / 2,
                    size + coordinate_margin / 2,
                    text=label,
                    fill=MUTED,
                    font=("Segoe UI", 8, "bold"),
                )
            for index, label in enumerate(rank_labels):
                board_canvas.create_text(
                    coordinate_margin / 2,
                    index * cell + cell / 2,
                    text=label,
                    fill=MUTED,
                    font=("Segoe UI", 8, "bold"),
                )
            try:
                fen_var.set(current_fen())
            except (ValueError, tk.TclError):
                fen_var.set("Adjust the move counters to valid numbers")

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
            try:
                if picker.winfo_exists():
                    picker.destroy()
            except tk.TclError:
                pass

        def place_piece(piece):
            position = pending_square.get("position")
            if position is None:
                return
            row, col = position
            mutable = list(rows[row])
            mutable[col] = piece
            rows[row] = "".join(mutable)
            refresh_castling_suggestions()
            draw_recognized_board()
            picker = piece_picker.get("window")
            if picker is not None:
                dismiss_piece_picker(picker)

        def show_piece_picker(event, row, col):
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

        def choose_square_piece(event):
            display_col = int((event.x - coordinate_margin) // cell)
            display_row = int(event.y // cell)
            if source_view["pieces_flipped"]:
                col, row = 7 - display_col, 7 - display_row
            else:
                col, row = display_col, display_row
            if not (0 <= col < 8 and 0 <= row < 8):
                return "break"
            selected_square["position"] = (row, col)
            draw_recognized_board()
            show_piece_picker(event, row, col)
            return "break"

        def castling_changed(right):
            manually_set_castling.add(right)
            draw_recognized_board()

        def set_board_orientation():
            new_orientation = board_orientation.get()
            if new_orientation == selected_orientation["value"]:
                return
            # Recognition rows currently use the automatic orientation guess.
            # Switching the board labels rotates their square coordinates while
            # preserving the recognized piece identities and any manual edits.
            rows[:] = [row[::-1] for row in reversed(rows)]
            selected_orientation["value"] = new_orientation
            source_view["pieces_flipped"] = new_orientation == "black"
            selected_square["position"] = None
            picker = piece_picker.get("window")
            if picker is not None:
                dismiss_piece_picker(picker)
            refresh_castling_suggestions(force=True)
            draw_recognized_board()

        board_canvas.bind("<Button-1>", choose_square_piece)
        board_canvas.bind("<Button-3>", choose_square_piece)
        draw_recognized_board()

        def flip_board():
            source_view["pieces_flipped"] = not source_view["pieces_flipped"]
            draw_recognized_board()

        def flip_from_key(event=None):
            if event is not None and getattr(event, "keysym", "").lower() != "f":
                return
            if window.winfo_exists():
                flip_board()
            return "break"

        window.bind_class(review_key_tag, "<KeyPress>", flip_from_key)
        bind_review_key_scope(window)
        flip_button = tk.Button(right_header, text="Flip board", command=flip_board, bg=PANEL2, fg=TEXT, activebackground=EDGE, activeforeground=TEXT, relief="flat", bd=0, padx=9, pady=4, cursor="hand2")
        flip_button.pack(side="right")
        self._add_tooltip(flip_button, "Rotate only the recognized pieces · shortcut: F")
        orientation_controls = tk.Frame(right, bg=INK)
        orientation_controls.pack(fill="x", pady=(5, 0))
        tk.Label(orientation_controls, text="Board position", bg=INK, fg=MUTED, font=("Segoe UI", 8, "bold")).pack(side="left", padx=(coordinate_margin, 8))
        orientation_switch = tk.Canvas(orientation_controls, width=50, height=28, bg=INK, highlightthickness=0, cursor="hand2", takefocus=1)
        orientation_switch.pack(side="left", padx=(0, 7))
        orientation_label = tk.StringVar(value="Normal" if board_orientation.get() == "white" else "Flipped")
        tk.Label(orientation_controls, textvariable=orientation_label, bg=INK, fg=TEXT, font=("Segoe UI", 9, "bold"), width=7, anchor="w").pack(side="left")

        def redraw_orientation_switch():
            orientation_switch.delete("all")
            flipped = board_orientation.get() == "black"
            track_color = ACCENT if flipped else PANEL2
            # Build a pill-shaped track and a contrasting sliding thumb.
            orientation_switch.create_oval(1, 2, 25, 26, fill=track_color, outline=track_color)
            orientation_switch.create_oval(25, 2, 49, 26, fill=track_color, outline=track_color)
            orientation_switch.create_rectangle(13, 2, 37, 26, fill=track_color, outline=track_color)
            thumb_x = 26 if flipped else 4
            orientation_switch.create_oval(thumb_x, 4, thumb_x + 20, 24, fill="#ffffff", outline=EDGE, width=1)
            orientation_label.set("Flipped" if flipped else "Normal")

        def toggle_orientation_switch(_event=None):
            board_orientation.set("white" if board_orientation.get() == "black" else "black")
            set_board_orientation()
            redraw_orientation_switch()
            return "break"

        orientation_switch.bind("<Button-1>", toggle_orientation_switch)
        orientation_switch.bind("<space>", toggle_orientation_switch)
        orientation_switch.bind("<Return>", toggle_orientation_switch)
        self._add_tooltip(orientation_switch, "Toggle board coordinates: Normal uses rank 1 at the bottom (a–h); Flipped uses rank 8 at the bottom (h–a)")
        redraw_orientation_switch()

        options = tk.Frame(window, bg=PANEL, padx=12, pady=9)
        options.pack(fill="x", padx=24, pady=(16, 9))
        tk.Label(options, text="To move", bg=PANEL, fg=TEXT).grid(row=0, column=0, sticky="w", padx=(0, 8))
        turn_switch = tk.Canvas(options, width=50, height=28, bg=PANEL, highlightthickness=0, cursor="hand2", takefocus=1)
        turn_switch.grid(row=0, column=1, sticky="w", padx=(0, 7))
        turn_label = tk.StringVar(value="Black to move" if turn.get() == "b" else "White to move")
        tk.Label(options, textvariable=turn_label, bg=PANEL, fg=TEXT, font=("Segoe UI", 9, "bold"), width=15, anchor="w").grid(row=0, column=2, sticky="w", padx=(0, 16))

        def redraw_turn_switch():
            turn_switch.delete("all")
            black_to_move = turn.get() == "b"
            track_color = ACCENT if black_to_move else PANEL2
            turn_switch.create_oval(1, 2, 25, 26, fill=track_color, outline=track_color)
            turn_switch.create_oval(25, 2, 49, 26, fill=track_color, outline=track_color)
            turn_switch.create_rectangle(13, 2, 37, 26, fill=track_color, outline=track_color)
            thumb_x = 26 if black_to_move else 4
            turn_switch.create_oval(thumb_x, 4, thumb_x + 20, 24, fill="#ffffff", outline=EDGE, width=1)
            turn_label.set("Black to move" if black_to_move else "White to move")

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
        fen_entry = tk.Entry(window, textvariable=fen_var, readonlybackground=PANEL2, fg=TEXT, insertbackground=TEXT, relief="flat", bd=0)
        fen_entry.pack(fill="x", padx=24, ipady=7)
        fen_entry.configure(state="readonly")
        self._add_tooltip(fen_entry, "FEN for the reviewed current position")
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
        lichess_button = tk.Button(button_row, text="Open in Lichess", command=open_in_lichess, bg=PANEL2, fg=TEXT, activebackground=EDGE, activeforeground=TEXT, relief="flat", bd=0, padx=24, pady=10, cursor="hand2")
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
            self._composited().save(path,"PNG")
            _open_in_paint(path)
            self.status.set("Opened a temporary copy in Paint · use Save As in Paint to keep it")
        except Exception as e:messagebox.showerror("Couldn't open in Paint",str(e))

    def _copy_capture_to_clipboard(self):
        """Put every newly captured snip on the clipboard automatically."""
        self._schedule_auto_clipboard()

    def _pointer_status(self,e):
        self._pointer_canvas_pos=(self.canvas.canvasx(e.x),self.canvas.canvasy(e.y))
        self._update_brush_cursor()
        if self.image and self.tool.get()!="crop":
            x,y=self._to_image(e.x,e.y)
            if 0<=x<self.image.width and 0<=y<self.image.height:self.status.set(f"{self.image.width} × {self.image.height} px   ·   {int(x)}, {int(y)}")

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




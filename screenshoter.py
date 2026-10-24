# -*- coding: utf-8 -*-
"""
Скриншотер — лёгкая программа для скриншотов под Windows (в духе Lightshot).

Запуск:
    pythonw screenshoter.py          — работа в трее, снимок по клавише PrtScn
    pythonw screenshoter.py --shot   — сделать один снимок сразу и выйти

Зависимости: pillow, pystray, pynput  (см. requirements.txt)
"""
import sys
import os
import io
import json
import math
import colorsys
import copy
import time
import queue
import ctypes
from contextlib import contextmanager
from datetime import datetime
import tkinter as tk
from tkinter import filedialog, colorchooser, simpledialog, messagebox

from PIL import Image, ImageTk, ImageDraw, ImageGrab, ImageEnhance, ImageChops, ImageFont

IS_WIN = sys.platform.startswith("win")
APP_NAME = "Скриншотер"
APP_ID = "Screenshoter"
VERSION = "5.2"

# ----------------------------------------------------------------- оформление
BG = "#191c38"        # панель
BG2 = "#262a52"       # кнопка
BG3 = "#363c74"       # наведение
FG = "#eef0ff"
ACC = "#4f7bff"       # акцент
ACC_H = "#6d92ff"
EDGE = "#3a4072"      # тонкая обводка
UI_FONT = ("Segoe UI", 10)
ICON_FONT = ("Segoe UI Symbol", 13)

TOOLS = [
    ("select", "⤢", "Выделение: перемещение и изменение области  (V)"),
    ("pen", "✎", "Карандаш — свободное рисование  (P)\nS — скругление вкл/выкл"),
    ("line", "╱", "Прямая линия  (L)\nShift — шаг 15°"),
    ("curve", "∿", "Кривая по 3 точкам  (C)\nПотяните от начала к концу, затем двигайте\nсреднюю (цветную) точку, чтобы выгнуть.\nКрайние точки тоже двигаются."),
    ("arrow", "➜", "Стрелка  (A)\nShift — шаг 15°"),
    ("rect", "▭", "Прямоугольник  (R)\nShift — квадрат"),
    ("ellipse", "◯", "Круг / эллипс  (O)\nShift — ровный круг"),
]
TOOL_IDS = [t[0] for t in TOOLS]
TOOL_KEYS = {"v": "select", "p": "pen", "l": "line", "c": "curve", "a": "arrow", "r": "rect", "o": "ellipse"}

# клавиши 0..8 выбирают пропорции по индексу
RATIOS = ["Свободно", "1:1", "4:5", "3:4", "4:3", "16:9", "9:16", "3:2", "2:3"]

PALETTE = ["#ff2d2d", "#ff8a00", "#ffd400", "#22c55e", "#1e90ff",
           "#8b5cf6", "#ff3fb4", "#000000", "#ffffff"]

DEFAULTS = {"tool": "pen", "color": "#ff2d2d", "width": 4,
            "ratio": "Свободно", "last_dir": "", "cursor": True,
            "collage_ratio": "Авто", "collage_cols": 0,
            "fmt": "PNG", "quality": 90, "dim": 50, "alpha": 100, "late_capture": False, "bar2": False, "sel": None, "sel_wh": None}

FORMATS = ["PNG", "JPG", "WEBP", "BMP"]
FMT_EXT = {"PNG": ".png", "JPG": ".jpg", "WEBP": ".webp", "BMP": ".bmp"}
FMT_TYPES = {"PNG": ("PNG", "*.png"), "JPG": ("JPEG", "*.jpg *.jpeg"), "WEBP": ("WebP", "*.webp"),
             "BMP": ("BMP", "*.bmp")}
QUALITY = [90]                  # текущее качество для JPG/WebP (меняется в настройках формата)


def file_types(fmt):
    order = [fmt] + [f for f in FORMATS if f != fmt]
    return [FMT_TYPES[f] for f in order]


# ================================================================== настройки
def settings_path():
    base = os.environ.get("APPDATA") if IS_WIN else os.path.expanduser("~/.config")
    d = os.path.join(base or os.path.expanduser("~"), APP_ID)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "settings.json")


def load_settings():
    s = dict(DEFAULTS)
    try:
        with open(settings_path(), encoding="utf-8") as f:
            s.update(json.load(f))
    except Exception:
        pass
    return s


def save_settings(s):
    try:
        with open(settings_path(), "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ===================================================================== Windows
def set_dpi_awareness():
    """Чтобы координаты совпадали с реальными пикселями на экранах с масштабом 125–200 %."""
    if not IS_WIN:
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def grab_screen():
    """Снимок всех мониторов. Возвращает (картинка, x и y виртуального экрана, прямоугольник основного монитора)."""
    if IS_WIN:
        u = ctypes.windll.user32
        vx, vy = u.GetSystemMetrics(76), u.GetSystemMetrics(77)
        img = ImageGrab.grab(all_screens=True).convert("RGB")
        pw, ph = u.GetSystemMetrics(0), u.GetSystemMetrics(1)
        primary = (-vx, -vy, -vx + pw, -vy + ph)
        return img, vx, vy, primary
    img = ImageGrab.grab().convert("RGB")
    return img, 0, 0, (0, 0, img.width, img.height)


def grab_cursor(vx, vy):
    """Курсор мыши в момент снимка: (чёрный фон RGB, множитель прозрачности L, x, y) или None."""
    if not IS_WIN:
        return None
    try:
        from ctypes import wintypes as wt
        u, g = ctypes.windll.user32, ctypes.windll.gdi32

        class CURSORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wt.DWORD), ("flags", wt.DWORD), ("hCursor", ctypes.c_void_p),
                        ("ptScreenPos", wt.POINT)]

        class ICONINFO(ctypes.Structure):
            _fields_ = [("fIcon", wt.BOOL), ("xHotspot", wt.DWORD), ("yHotspot", wt.DWORD),
                        ("hbmMask", ctypes.c_void_p), ("hbmColor", ctypes.c_void_p)]

        class BITMAP(ctypes.Structure):
            _fields_ = [("bmType", wt.LONG), ("bmWidth", wt.LONG), ("bmHeight", wt.LONG),
                        ("bmWidthBytes", wt.LONG), ("bmPlanes", wt.WORD), ("bmBitsPixel", wt.WORD),
                        ("bmBits", ctypes.c_void_p)]

        class BMIH(ctypes.Structure):
            _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                        ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                        ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
                        ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]

        vp = ctypes.c_void_p
        u.GetCursorInfo.argtypes = [ctypes.POINTER(CURSORINFO)]
        u.GetIconInfo.argtypes = [vp, ctypes.POINTER(ICONINFO)]
        u.GetDC.argtypes = [vp]
        u.GetDC.restype = vp
        u.ReleaseDC.argtypes = [vp, vp]
        u.DrawIconEx.argtypes = [vp, ctypes.c_int, ctypes.c_int, vp, ctypes.c_int, ctypes.c_int,
                                 ctypes.c_uint, vp, ctypes.c_uint]
        g.GetObjectW.argtypes = [vp, ctypes.c_int, vp]
        g.CreateCompatibleDC.argtypes = [vp]
        g.CreateCompatibleDC.restype = vp
        g.CreateDIBSection.argtypes = [vp, vp, ctypes.c_uint, ctypes.POINTER(vp), vp, ctypes.c_uint]
        g.CreateDIBSection.restype = vp
        g.SelectObject.argtypes = [vp, vp]
        g.SelectObject.restype = vp
        g.DeleteObject.argtypes = [vp]
        g.DeleteDC.argtypes = [vp]

        ci = CURSORINFO()
        ci.cbSize = ctypes.sizeof(ci)
        if not u.GetCursorInfo(ctypes.byref(ci)) or not (ci.flags & 1) or not ci.hCursor:
            return None
        ii = ICONINFO()
        if not u.GetIconInfo(ci.hCursor, ctypes.byref(ii)):
            return None
        bm = BITMAP()
        g.GetObjectW(ii.hbmColor or ii.hbmMask, ctypes.sizeof(bm), ctypes.byref(bm))
        w = bm.bmWidth
        h = bm.bmHeight if ii.hbmColor else bm.bmHeight // 2
        for hbm in (ii.hbmColor, ii.hbmMask):
            if hbm:
                g.DeleteObject(hbm)
        if w <= 0 or h <= 0:
            return None

        def render(bg):
            hdc = u.GetDC(None)
            mdc = g.CreateCompatibleDC(hdc)
            bi = BMIH()
            bi.biSize, bi.biWidth, bi.biHeight, bi.biPlanes, bi.biBitCount = ctypes.sizeof(bi), w, -h, 1, 32
            bits = vp()
            dib = g.CreateDIBSection(mdc, ctypes.byref(bi), 0, ctypes.byref(bits), None, 0)
            old = g.SelectObject(mdc, dib)
            ctypes.memset(bits.value, bg, w * h * 4)
            u.DrawIconEx(mdc, 0, 0, ci.hCursor, w, h, 0, None, 3)    # DI_NORMAL
            data = ctypes.string_at(bits.value, w * h * 4)
            g.SelectObject(mdc, old)
            g.DeleteObject(dib)
            g.DeleteDC(mdc)
            u.ReleaseDC(None, hdc)
            return Image.frombuffer("RGBA", (w, h), data, "raw", "BGRA", 0, 1).convert("RGB")

        black, white = render(0), render(255)
        factor = ImageChops.subtract(white, black).convert("L")      # = 255 − прозрачность
        return (black, factor, ci.ptScreenPos.x - ii.xHotspot - vx, ci.ptScreenPos.y - ii.yHotspot - vy,
                ii.xHotspot, ii.yHotspot)
    except Exception:
        return None


def paste_cursor(img, cur):
    """Накладывает курсор на снимок (без изменения исходной картинки)."""
    if not cur:
        return img
    black, factor, x, y = cur[:4]
    w, h = black.size
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(img.width, x + w), min(img.height, y + h)
    if x1 <= x0 or y1 <= y0:
        return img
    out = img.copy()
    box = (x0, y0, x1, y1)
    sub = (x0 - x, y0 - y, x1 - x, y1 - y)
    f, b = factor.crop(sub), black.crop(sub)
    region = ImageChops.add(ImageChops.multiply(out.crop(box), Image.merge("RGB", (f, f, f))), b)
    out.paste(region, box)
    return out


def copy_image_to_clipboard(img, hwnd=None):
    """Кладёт картинку в буфер обмена Windows (форматы DIB и PNG)."""
    if not IS_WIN:
        raise OSError("Копирование в буфер обмена реализовано только для Windows")
    bmp = io.BytesIO()
    img.convert("RGB").save(bmp, "BMP")
    dib = bmp.getvalue()[14:]           # без 14-байтного заголовка файла
    png = io.BytesIO()
    img.save(png, "PNG")
    png = png.getvalue()

    k = ctypes.windll.kernel32
    u = ctypes.windll.user32
    k.GlobalAlloc.restype = ctypes.c_void_p
    k.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    k.GlobalLock.restype = ctypes.c_void_p
    k.GlobalLock.argtypes = [ctypes.c_void_p]
    k.GlobalUnlock.argtypes = [ctypes.c_void_p]
    u.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    u.SetClipboardData.restype = ctypes.c_void_p
    u.OpenClipboard.argtypes = [ctypes.c_void_p]
    u.EmptyClipboard.restype = ctypes.c_int
    k.GlobalFree.argtypes = [ctypes.c_void_p]
    u.RegisterClipboardFormatW.argtypes = [ctypes.c_wchar_p]
    GMEM_MOVEABLE = 0x0002
    CF_DIB = 8

    def put(fmt, data):
        h = k.GlobalAlloc(GMEM_MOVEABLE, len(data))
        p = k.GlobalLock(h)
        ctypes.memmove(p, data, len(data))
        k.GlobalUnlock(h)
        if not u.SetClipboardData(fmt, h):
            k.GlobalFree(h)
            raise OSError("не удалось положить данные в буфер обмена")

    last = None
    for attempt in range(8):                            # буфер бывает занят другой программой — пробуем снова
        if not u.OpenClipboard(hwnd):
            last = OSError("Буфер обмена занят другой программой")
            time.sleep(0.08)
            continue
        try:
            u.EmptyClipboard()
            put(CF_DIB, dib)
            try:
                put(u.RegisterClipboardFormatW("PNG"), png)
            except Exception:
                pass
            return
        except OSError as ex:
            last = ex
        finally:
            u.CloseClipboard()
        time.sleep(0.08)
    raise last or OSError("Буфер обмена недоступен")


def force_foreground(win):
    """Windows не любит, когда фоновая программа забирает фокус — обходим это."""
    if not IS_WIN:
        return
    try:
        u = ctypes.windll.user32
        hwnd = u.GetAncestor(win.winfo_id(), 2)  # GA_ROOT
        if u.GetForegroundWindow() != hwnd:
            u.keybd_event(0x12, 0, 0, 0)        # короткое нажатие Alt снимает блокировку
            u.keybd_event(0x12, 0, 2, 0)
        u.SetForegroundWindow(hwnd)
    except Exception:
        pass


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def autostart_command():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    exe = sys.executable
    pw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.exists(pw):
        exe = pw
    return f'"{exe}" "{os.path.abspath(__file__)}"'


def autostart_enabled():
    if not IS_WIN:
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, APP_ID)
            return True
    except OSError:
        return False


def set_autostart(on):
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if on:
            winreg.SetValueEx(key, APP_ID, 0, winreg.REG_SZ, autostart_command())
        else:
            try:
                winreg.DeleteValue(key, APP_ID)
            except OSError:
                pass


_MUTEX = None


def already_running():
    global _MUTEX
    if not IS_WIN:
        return False
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateMutexW.restype = ctypes.c_void_p
    _MUTEX = k.CreateMutexW(None, False, "Local\\ScreenshoterSingleInstance")
    return ctypes.get_last_error() == 183  # ERROR_ALREADY_EXISTS


def unique_path(folder, base, ext=".png"):
    path = os.path.join(folder, base + ext)
    n = 2
    while os.path.exists(path):
        path = os.path.join(folder, f"{base}_{n}{ext}")
        n += 1
    return path


def save_image(img, path, quality=None):
    q = int(quality if quality is not None else QUALITY[0])
    q = max(1, min(100, q))
    ext = path.lower().rsplit(".", 1)[-1]
    if ext in ("jpg", "jpeg"):
        img.convert("RGB").save(path, quality=q, optimize=True, subsampling=0 if q >= 90 else 2)
    elif ext == "webp":
        if q >= 100:
            img.convert("RGB").save(path, lossless=True, method=4)
        else:
            img.convert("RGB").save(path, quality=q, method=4)
    elif ext == "bmp":
        img.convert("RGB").save(path)
    else:
        img.save(path, optimize=True)


def default_save_dir():
    d = os.path.join(os.path.expanduser("~"), "Pictures", "Screenshots")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        d = os.path.expanduser("~")
    return d


# =================================================================== геометрия
def dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def flat(pts):
    out = []
    for p in pts:
        out.extend(p)
    return out


def dedupe(pts, min_d=0.01):
    out = []
    for p in pts:
        if not out or dist(out[-1], p) > min_d:
            out.append(p)
    return out


def rdp(pts, eps):
    """Упрощение ломаной (алгоритм Рамера — Дугласа — Пекера)."""
    if len(pts) < 3:
        return list(pts)
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i, j = stack.pop()
        a, b = pts[i], pts[j]
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dy)
        mx, idx = -1.0, -1
        for k in range(i + 1, j):
            p = pts[k]
            d = dist(p, a) if L == 0 else abs(dy * (p[0] - a[0]) - dx * (p[1] - a[1])) / L
            if d > mx:
                mx, idx = d, k
        if idx > 0 and mx > eps:
            keep[idx] = True
            stack.append((i, idx))
            stack.append((idx, j))
    return [p for p, k in zip(pts, keep) if k]


def chaikin(pts, iterations=3):
    """Сглаживание углов (срезание углов Чайкина)."""
    for _ in range(iterations):
        if len(pts) < 3:
            return pts
        new = [pts[0]]
        for a, b in zip(pts, pts[1:]):
            new.append((0.75 * a[0] + 0.25 * b[0], 0.75 * a[1] + 0.25 * b[1]))
            new.append((0.25 * a[0] + 0.75 * b[0], 0.25 * a[1] + 0.75 * b[1]))
        new.append(pts[-1])
        pts = new
    return pts


def round_polyline(pts, r):
    """Скругление углов ломаной дугами окружности. r от 0 до 1:
    0 — острые углы, 1 — дуга окружности касается звеньев на всю их длину
    (у двух звеньев — правильная дуга от одного конца до другого)."""
    pts = dedupe(pts)
    if r <= 0 or len(pts) < 3:
        return pts
    out = [pts[0]]
    for i in range(1, len(pts) - 1):
        a, v, b = pts[i - 1], pts[i], pts[i + 1]
        l1, l2 = dist(a, v), dist(v, b)
        ux, uy = (a[0] - v[0]) / l1, (a[1] - v[1]) / l1
        wx, wy = (b[0] - v[0]) / l2, (b[1] - v[1]) / l2
        cosf = max(-1.0, min(1.0, ux * wx + uy * wy))
        phi = math.acos(cosf)                      # внутренний угол в вершине
        share1 = 1.0 if i - 1 == 0 else 0.5       # крайнее звено целиком, общее — пополам
        share2 = 1.0 if i + 1 == len(pts) - 1 else 0.5
        t = r * min(l1 * share1, l2 * share2)      # длина касательной
        if phi > math.pi - 0.02 or phi < 0.02 or t < 0.05:
            out.append(v)
            continue
        R = t * math.tan(phi / 2)
        p1 = (v[0] + ux * t, v[1] + uy * t)
        p2 = (v[0] + wx * t, v[1] + wy * t)
        bx, by = ux + wx, uy + wy
        bl = math.hypot(bx, by)
        hd = R / math.sin(phi / 2)
        cx, cy = v[0] + bx / bl * hd, v[1] + by / bl * hd
        a1 = math.atan2(p1[1] - cy, p1[0] - cx)
        a2 = math.atan2(p2[1] - cy, p2[0] - cx)
        da = (a2 - a1 + math.pi) % (2 * math.pi) - math.pi
        n = max(3, min(40, int(abs(da) * R / 1.5)))
        for k in range(n + 1):
            an = a1 + da * k / n
            out.append((cx + R * math.cos(an), cy + R * math.sin(an)))
    out.append(pts[-1])
    return out


def pen_render_points(raw, rnd):
    pts = dedupe(raw)
    if len(pts) < 3:
        return pts
    if rnd <= 0:
        return rdp(pts, 0.6)
    r = rnd / 100.0
    length = sum(dist(a, b) for a, b in zip(pts, pts[1:]))
    # чем выше скругление и длиннее линия, тем крупнее «характерные» точки излома:
    # любая нарисованная линия (угол, дуга, S-образная) превращается в несколько
    # прямых звеньев, а углы между ними — в гладкие дуги окружности
    eps = 0.8 + 0.04 * length * r
    return round_polyline(rdp(pts, eps), r)


def ellipse_points(x0, y0, x1, y1):
    """Замкнутый контур эллипса, вписанного в прямоугольник."""
    x0, x1 = sorted((x0, x1))
    y0, y1 = sorted((y0, y1))
    rx, ry = (x1 - x0) / 2.0, (y1 - y0) / 2.0
    if rx < 0.5 or ry < 0.5:
        return []
    cx, cy = x0 + rx, y0 + ry
    n = int(max(48, min(240, (rx + ry) * 0.9)))
    return [(cx + rx * math.cos(2 * math.pi * i / n), cy + ry * math.sin(2 * math.pi * i / n))
            for i in range(n + 1)]


def rrect_points(x0, y0, x1, y1, rad):
    """Замкнутый контур прямоугольника со скруглёнными углами."""
    x0, x1 = sorted((x0, x1))
    y0, y1 = sorted((y0, y1))
    rad = max(0.0, min(rad, (x1 - x0) / 2, (y1 - y0) / 2))
    if rad < 0.5:
        return [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
    n = max(6, min(30, int(rad / 1.5)))
    out = []
    for cx, cy, a0 in ((x1 - rad, y0 + rad, -90), (x1 - rad, y1 - rad, 0),
                       (x0 + rad, y1 - rad, 90), (x0 + rad, y0 + rad, 180)):
        for k in range(n + 1):
            an = math.radians(a0 + 90.0 * k / n)
            out.append((cx + rad * math.cos(an), cy + rad * math.sin(an)))
    out.append(out[0])
    return out


def rect_rad(sh):
    (x0, y0), (x1, y1) = sh["p0"], sh["p1"]
    return sh.get("round", 0) / 100.0 * min(abs(x1 - x0), abs(y1 - y0)) / 2


def curve_control(p0, m, p2):
    """Контрольная точка, при которой кривая проходит ровно через среднюю точку m."""
    return (2 * m[0] - (p0[0] + p2[0]) / 2, 2 * m[1] - (p0[1] + p2[1]) / 2)


def curve_points(p0, m, p2):
    c = curve_control(p0, m, p2)
    L = dist(p0, c) + dist(c, p2)
    n = max(12, min(500, int(L / 3)))
    out = []
    for i in range(n + 1):
        t = i / n
        u = 1 - t
        out.append((u * u * p0[0] + 2 * u * t * c[0] + t * t * p2[0],
                    u * u * p0[1] + 2 * u * t * c[1] + t * t * p2[1]))
    return out


def arrow_geom(p0, p1, w):
    d = dist(p0, p1)
    if d < 1:
        return None
    ux, uy = (p1[0] - p0[0]) / d, (p1[1] - p0[1]) / d
    nx, ny = -uy, ux
    L = min(max(14.0, w * 3.6), d)
    hw = max(7.0, w * 2.1)
    bx, by = p1[0] - ux * L, p1[1] - uy * L
    tri = [p1, (bx + nx * hw, by + ny * hw), (bx - nx * hw, by - ny * hw)]
    line = [p0, (bx + ux * 1.0, by + uy * 1.0)]
    return line, tri


def snap_angle(p0, p, step_deg=15):
    dx, dy = p[0] - p0[0], p[1] - p0[1]
    r = math.hypot(dx, dy)
    if r == 0:
        return p
    s = math.radians(step_deg)
    a = round(math.atan2(dy, dx) / s) * s
    return (p0[0] + r * math.cos(a), p0[1] + r * math.sin(a))


def parse_ratio(text):
    """'Свободно' -> (None, None); '16:9' -> ((16, 9), None); '1080x1350' -> ((4, 5), (1080, 1350))."""
    t = (text or "").strip().lower().replace(" ", "")
    for ch in ("х", "×", "*"):          # русская «х» тоже подходит
        t = t.replace(ch, "x")
    if not t or t.startswith("св"):
        return None, None
    if "x" in t:
        a, b = t.split("x", 1)
        w, h = int(a), int(b)
        if w <= 0 or h <= 0:
            raise ValueError
        g = math.gcd(w, h)
        return (w // g, h // g), (w, h)
    for sep in (":", "/"):
        if sep in t:
            a, b = t.split(sep, 1)
            fa, fb = float(a.replace(",", ".")), float(b.replace(",", "."))
            if fa <= 0 or fb <= 0:
                raise ValueError
            ia, ib = round(fa * 100), round(fb * 100)
            g = math.gcd(ia, ib)
            return (ia // g, ib // g), None
    raise ValueError


# ============================================================ отрисовка (PIL)
def pil_stroke(d, pts, w, col, rounded):
    pts = dedupe(pts)
    if not pts:
        return
    hw = w / 2
    if len(pts) == 1:
        x, y = pts[0]
        if rounded:
            d.ellipse([x - hw, y - hw, x + hw, y + hw], fill=col)
        else:
            d.rectangle([x - hw, y - hw, x + hw, y + hw], fill=col)
        return
    if rounded:
        d.line(pts, fill=col, width=max(1, int(round(w))), joint="curve")
        for x, y in pts:                      # круг в каждой вершине: без «щербинок» на стыках сегментов
            d.ellipse([x - hw, y - hw, x + hw, y + hw], fill=col)
        return
    # без скругления: прямые сегменты, квадратные концы, скошенные стыки
    n = len(pts)
    normals = []
    for i in range(n - 1):
        a, b = pts[i], pts[i + 1]
        L = dist(a, b)
        ux, uy = (b[0] - a[0]) / L, (b[1] - a[1]) / L
        normals.append((-uy * hw, ux * hw))
        if i == 0:
            a = (a[0] - ux * hw, a[1] - uy * hw)
        if i == n - 2:
            b = (b[0] + ux * hw, b[1] + uy * hw)
        nx, ny = -uy * hw, ux * hw
        d.polygon([(a[0] + nx, a[1] + ny), (b[0] + nx, b[1] + ny),
                   (b[0] - nx, b[1] - ny), (a[0] - nx, a[1] - ny)], fill=col, outline=col)
    for i in range(1, n - 1):
        v = pts[i]
        (n1x, n1y), (n2x, n2y) = normals[i - 1], normals[i]
        d.polygon([v, (v[0] + n1x, v[1] + n1y), (v[0] + n2x, v[1] + n2y)], fill=col, outline=col)
        d.polygon([v, (v[0] - n1x, v[1] - n1y), (v[0] - n2x, v[1] - n2y)], fill=col, outline=col)


def pil_draw_shape(d, sh, ox, oy, S):
    col = sh["color"]
    w = sh["w"] * S

    def T(p):
        return ((p[0] - ox) * S, (p[1] - oy) * S)

    t = sh["type"]
    if t == "pen":
        pil_stroke(d, [T(p) for p in sh["rpts"]], w, col, sh["round"] >= 50)
    elif t == "line":
        pil_stroke(d, [T(sh["p0"]), T(sh["p1"])], w, col, True)
    elif t == "curve":
        pil_stroke(d, [T(p) for p in curve_points(sh["p0"], sh["m"], sh["p2"])], w, col, True)
    elif t == "arrow":
        g = arrow_geom(sh["p0"], sh["p1"], sh["w"])
        if g:
            line, tri = g
            pil_stroke(d, [T(p) for p in line], w, col, True)
            d.polygon([T(p) for p in tri], fill=col)
    elif t == "ellipse":
        pts = ellipse_points(*sh["p0"], *sh["p1"])
        if pts:
            pil_stroke(d, [T(p) for p in pts], w, col, True)
    elif t == "rect":
        rad = rect_rad(sh)
        if rad >= 0.5:
            pil_stroke(d, [T(p) for p in rrect_points(*sh["p0"], *sh["p1"], rad)], w, col, True)
            return
        (x0, y0), (x1, y1) = T(sh["p0"]), T(sh["p1"])
        x0, x1 = sorted((x0, x1))
        y0, y1 = sorted((y0, y1))
        hw = w / 2
        d.rectangle([round(x0 - hw), round(y0 - hw), round(x1 + hw), round(y1 + hw)],
                    outline=col, width=max(1, int(round(w))))


def live_layer(lay):
    """В живом режиме окно прозрачно по «ключевому цвету»: полупрозрачные пиксели смешались бы с ним и дали тёмную
    кайму. Поэтому показываем жёсткую маску, а прозрачность передаём осветлением цвета."""
    a = lay.getchannel("A")
    amax = a.getextrema()[1]
    if amax <= 0:
        return lay
    thr = max(1, amax // 2)
    mask = a.point(lambda v, t=thr: 255 if v >= t else 0)
    k = amax / 255.0
    rgb = lay.convert("RGB").point(lambda v, k=k: int(v * k + 255 * (1 - k)))
    rgb.putalpha(mask)
    return rgb


def shapes_layer(shapes, ox, oy, w, h, S=3):
    """Прозрачный слой с фигурами (рисуем в S раз крупнее и уменьшаем — края гладкие).
    Фигура с прозрачностью рисуется отдельно и накладывается целиком: перекрытия внутри неё не темнеют."""
    ov = Image.new("RGBA", (w * S, h * S), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    for sh in shapes:
        a = int(sh.get("alpha", 100))
        if a >= 100:
            pil_draw_shape(d, sh, ox, oy, S)
        else:
            tmp = Image.new("RGBA", ov.size, (0, 0, 0, 0))
            pil_draw_shape(ImageDraw.Draw(tmp), sh, ox, oy, S)
            tmp.putalpha(tmp.getchannel("A").point(lambda v, a=a: v * a // 100))
            ov = Image.alpha_composite(ov, tmp)
            d = ImageDraw.Draw(ov)
    return ov.resize((w, h), Image.LANCZOS)


def shape_bbox(sh):
    t = sh["type"]
    if t == "pen":
        pts = sh.get("rpts") or sh["pts"]
    elif t == "curve":
        pts = [sh["p0"], sh["m"], sh["p2"]]
    else:
        pts = [sh["p0"], sh["p1"]]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    m = sh["w"] + 6
    return min(xs) - m, min(ys) - m, max(xs) + m, max(ys) + m


def render_image(base_img, sel, shapes, S=3):
    """Вырезает область и рисует фигуры со сглаживанием."""
    x0, y0, x1, y1 = [int(v) for v in sel]
    w, h = x1 - x0, y1 - y0
    base = base_img.crop((x0, y0, x1, y1)).convert("RGBA")
    if not shapes:
        return base.convert("RGB")
    return Image.alpha_composite(base, shapes_layer(shapes, x0, y0, w, h, S)).convert("RGB")


# ====================================================================== иконки
ICON_PX = 19
ICON_COL = (242, 242, 242, 255)
ICON_ACC = (255, 190, 90, 255)


def make_icon(name, px=ICON_PX):
    """Рисует значок кнопки (в 8 раз крупнее, затем уменьшает — получается гладко)."""
    S = 8
    N = px * S
    k = N / 24.0
    im = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    col = ICON_COL
    W = 1.8 * k

    def P(pts):
        return [(x * k, y * k) for x, y in pts]

    def line(pts, w=W, c=col):
        pts = P(pts)
        d.line(pts, fill=c, width=int(w), joint="curve")
        r = w / 2
        for x, y in (pts[0], pts[-1]):
            d.ellipse([x - r, y - r, x + r, y + r], fill=c)

    def dot(x, y, r=1.8, c=col):
        d.ellipse([(x - r) * k, (y - r) * k, (x + r) * k, (y + r) * k], fill=c)

    def poly(pts, c=col):
        d.polygon(P(pts), fill=c)

    def rect(x0, y0, x1, y1, w=W):
        line([(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)], w)

    def bez(p0, c, p1, n=28):
        out = []
        for i in range(n + 1):
            t = i / n
            u = 1 - t
            out.append((u * u * p0[0] + 2 * u * t * c[0] + t * t * p1[0],
                        u * u * p0[1] + 2 * u * t * c[1] + t * t * p1[1]))
        return out

    def dashed_rect(x0, y0, x1, y1):
        for (ax, ay, bx, by) in ((x0, y0, x1, y0), (x1, y0, x1, y1), (x1, y1, x0, y1), (x0, y1, x0, y0)):
            for i in range(0, 5, 2):
                t0, t1 = i / 5.0, (i + 1) / 5.0
                line([(ax + (bx - ax) * t0, ay + (by - ay) * t0), (ax + (bx - ax) * t1, ay + (by - ay) * t1)], 1.5 * k)

    if name == "select":
        line([(12, 3), (12, 21)]); line([(3, 12), (21, 12)])
        poly([(12, 1.5), (8.5, 6), (15.5, 6)]); poly([(12, 22.5), (8.5, 18), (15.5, 18)])
        poly([(1.5, 12), (6, 8.5), (6, 15.5)]); poly([(22.5, 12), (18, 8.5), (18, 15.5)])
    elif name == "pen":
        line([(3 + 18 * i / 40.0, 12 + 4.6 * math.sin(i / 40.0 * 3 * math.pi)) for i in range(41)])
    elif name == "line":
        line([(4, 20), (20, 4)]); dot(4, 20, 2.2); dot(20, 4, 2.2)
    elif name == "curve":
        line(bez((3, 19), (12, -5), (21, 19)))
        dot(3, 19, 2.0); dot(21, 19, 2.0); dot(12, 7, 2.8, ICON_ACC)
    elif name == "arrow":
        line([(4, 20), (16.5, 7.5)]); poly([(21, 3), (18.6, 10.5), (13.5, 5.4)])
    elif name == "rect":
        rect(4, 6, 20, 18)
    elif name == "ellipse":
        line([(12 + 8.5 * math.cos(2 * math.pi * i / 40), 12 + 7 * math.sin(2 * math.pi * i / 40))
              for i in range(41)])
    elif name in ("undo", "redo"):
        pts = bez((9, 9), (21, 5), (13, 19))
        head = [(3, 9), (9.5, 3.8), (9.5, 14.2)]
        if name == "redo":
            pts = [(24 - x, y) for x, y in pts]
            head = [(24 - x, y) for x, y in head]
        line(pts); poly(head)
    elif name == "copy":
        line([(8, 16), (4, 16), (4, 4), (16, 4), (16, 8)]); rect(8, 8, 20, 20)
    elif name == "save":
        line([(4, 4), (17, 4), (20, 7), (20, 20), (4, 20), (4, 4)])
        rect(8, 4, 15, 9, 1.5 * k); rect(7, 13, 17, 20, 1.5 * k)
    elif name == "close":
        line([(6, 6), (18, 18)]); line([(18, 6), (6, 18)])
    elif name == "cursor":
        poly([(6, 2.5), (6, 18.5), (10.2, 14.6), (13.2, 21.2), (15.8, 20), (12.8, 13.6), (18.2, 13.6)])
    elif name == "collage_add":
        rect(3, 3, 10, 10, 1.5 * k); rect(14, 3, 21, 10, 1.5 * k); rect(3, 14, 10, 21, 1.5 * k)
        line([(17.5, 14.2), (17.5, 20.8)], 1.6 * k); line([(14.2, 17.5), (20.8, 17.5)], 1.6 * k)
    elif name == "shot":
        rect(3, 7, 21, 19, 1.6 * k); rect(9, 4, 15, 7, 1.4 * k); rect(9, 10, 15, 16, 1.6 * k)
    elif name == "quality":
        line([(3, 8), (21, 8)], 1.4 * k); line([(3, 16), (21, 16)], 1.4 * k)
        rect(7, 5, 11, 11, 1.6 * k); rect(14, 13, 18, 19, 1.6 * k)
    elif name == "dim":
        circ = [(12 + 8 * math.cos(2 * math.pi * i / 40), 12 + 8 * math.sin(2 * math.pi * i / 40)) for i in range(41)]
        line(circ)
        poly([(12 + 8 * math.cos(math.pi * (0.5 + i / 20.0)), 12 + 8 * math.sin(math.pi * (0.5 + i / 20.0)))
              for i in range(21)])
    elif name == "keys":
        rect(2.5, 6, 21.5, 18, 1.5 * k)
        for xx in (6, 10, 14, 18):
            dot(xx, 10, 1.1)
        dot(7.5, 14, 1.1); dot(16.5, 14, 1.1); line([(10.5, 14), (13.5, 14)], 1.6 * k)
    elif name == "late":
        circ = [(12 + 9 * math.cos(2 * math.pi * i / 40), 12 + 9 * math.sin(2 * math.pi * i / 40)) for i in range(41)]
        line(circ, 1.6 * k); line([(12, 6.5), (12, 12), (16, 14.5)], 1.7 * k)
    elif name == "chev_d":
        line([(5, 9), (12, 16), (19, 9)], 2.2 * k)
    elif name == "chev_u":
        line([(5, 16), (12, 9), (19, 16)], 2.2 * k)
    elif name == "saveas":
        line([(4, 4), (15, 4), (18, 7), (18, 12)], 1.6 * k); line([(4, 4), (4, 20), (11, 20)], 1.6 * k)
        rect(7, 4, 13, 8, 1.4 * k)
        line([(13, 22), (21, 14)], 2.2 * k); poly([(11.5, 22.5), (12.5, 19.2), (14.8, 21.5)])
    elif name == "trash":
        line([(4, 7), (20, 7)]); line([(9, 7), (9, 4), (15, 4), (15, 7)], 1.6 * k)
        line([(6, 7), (7.2, 21), (16.8, 21), (18, 7)]); line([(10.5, 10.5), (10.5, 17.5)], 1.4 * k)
        line([(13.5, 10.5), (13.5, 17.5)], 1.4 * k)
    elif name == "grid":
        rect(3, 5, 21, 19, 1.6 * k); line([(9, 5), (9, 19)], 1.4 * k); line([(15, 5), (15, 19)], 1.4 * k)
    elif name == "plus":
        line([(12, 5), (12, 19)]); line([(5, 12), (19, 12)])
    elif name == "width":
        line([(3, 6), (21, 6)], 1.2 * k); line([(3, 12), (21, 12)], 2.6 * k); line([(3, 18), (21, 18)], 4.2 * k)
    elif name == "round":
        arc = [(12 + 7 * math.cos(math.pi * (1 + 0.5 * i / 12.0)), 13 + 7 * math.sin(math.pi * (1 + 0.5 * i / 12.0)))
               for i in range(13)]
        line([(5, 21)] + arc + [(20, 6)])
    elif name == "ratio:free":
        dashed_rect(4, 6, 20, 18)
    elif name == "ratio:custom":
        rect(3, 5, 21, 19, 1.5 * k); line([(12, 8.5), (12, 15.5)], 1.6 * k); line([(8.5, 12), (15.5, 12)], 1.6 * k)
    elif name.startswith("ratio:"):
        a, b = [float(x) for x in name.split(":")[1:3]]
        m = 18.0
        w, h = (m, m * b / a) if a >= b else (m * a / b, m)
        rect(12 - w / 2, 12 - h / 2, 12 + w / 2, 12 + h / 2, 1.8 * k)
    return im.resize((px, px), Image.LANCZOS)


def ratio_icon_name(label):
    if label == "Свободно":
        return "ratio:free"
    return "ratio:" + label


# =================================================================== подсказки
_RB_CACHE = {}
RB_SIZE = ICON_PX + 11


def round_button_image(icon, fill, edge=None, dim=False):
    """Скруглённая плитка с иконкой (рисуется с 4-кратным запасом и уменьшается — края гладкие)."""
    key = (icon if isinstance(icon, str) else icon[0], fill, edge, dim)
    if key in _RB_CACHE:
        return _RB_CACHE[key]
    Z = 4
    n = RB_SIZE * Z
    base = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    ImageDraw.Draw(base).rounded_rectangle([0, 0, n - 1, n - 1], radius=int(n * 0.30), fill=fill,
                                           outline=edge, width=Z if edge else 0)
    base = base.resize((RB_SIZE, RB_SIZE), Image.LANCZOS)
    ic = make_icon(icon) if isinstance(icon, str) else icon[1]
    base.alpha_composite(ic, ((RB_SIZE - ic.width) // 2, (RB_SIZE - ic.height) // 2))
    if dim:                                        # неактивная кнопка: полупрозрачная
        base.putalpha(base.getchannel("A").point(lambda a: int(a * 0.32)))
    ph = ImageTk.PhotoImage(base, master=tk._default_root)
    _RB_CACHE[key] = ph
    return ph


class _RoundMixin:
    """Кнопка-плитка: скруглённый фон, наведение и активное состояние рисуются картинкой."""
    def _rinit(self, icon, fill):
        self._ricon, self._rfill, self._rhover, self._rdim = icon, fill, False, False
        self.bind("<Enter>", self._ren, add="+")
        self.bind("<Leave>", self._rlv, add="+")
        self._render()

    def _ren(self, _e=None):
        self._rhover = not self._rdim
        self._render()

    def _rlv(self, _e=None):
        self._rhover = False
        self._render()

    def _render(self):
        f = self._rfill
        if self._rhover and not self._rdim:
            f = {BG2: BG3, ACC: ACC_H}.get(f, f)
        edge = EDGE if f in (BG2, BG3) else None
        img = round_button_image(self._ricon, f, edge, self._rdim)
        try:
            tk.Button.configure(self, image=img) if isinstance(self, tk.Button) else \
                tk.Menubutton.configure(self, image=img)
        except tk.TclError:
            pass

    def set_icon(self, icon):
        self._ricon = icon
        self._render()

    def set_enabled(self, on):
        self._rdim = not on
        try:
            tk.Button.configure(self, state="normal" if on else "disabled", cursor="hand2" if on else "arrow")
        except tk.TclError:
            pass
        self._render()

    def configure(self, cnf=None, **kw):
        redo = False
        if "bg" in kw:
            self._rfill = kw.pop("bg")
            redo = True
        kw.pop("image", None)
        if cnf is None and not kw:
            if redo:
                self._render()
            return None
        base = tk.Button if isinstance(self, tk.Button) else tk.Menubutton
        r = base.configure(self, cnf, **kw) if cnf else base.configure(self, **kw)
        if redo:
            self._render()
        return r

    config = configure


class RoundBtn(_RoundMixin, tk.Button):
    def __init__(self, parent, icon, command=None, fill=BG2):
        tk.Button.__init__(self, parent, command=command, relief="flat", bd=0, highlightthickness=0,
                           bg=BG, activebackground=BG, cursor="hand2", takefocus=0, padx=0, pady=0)
        self._rinit(icon, fill)


class RoundMenuBtn(_RoundMixin, tk.Menubutton):
    def __init__(self, parent, icon, fill=BG2):
        tk.Menubutton.__init__(self, parent, relief="flat", bd=0, highlightthickness=0, bg=BG,
                               activebackground=BG, cursor="hand2", takefocus=0, padx=0, pady=0,
                               indicatoron=False)
        self._rinit(icon, fill)


class FlatSlider(tk.Canvas):
    """Тонкий ползунок: цветная часть дорожки и белый круг с числом внутри."""
    W, H, R = 74, 16, 7           # R — радиус круглого тумблера

    def __init__(self, parent, lo, hi, var, cmd, on_release=None, length=None):
        if length:
            self.W = length
        tk.Canvas.__init__(self, parent, width=self.W, height=self.H, bg=BG, highlightthickness=0, bd=0,
                           cursor="hand2")
        self.lo, self.hi, self.var, self.cmd, self.on_release = lo, hi, var, cmd, on_release
        self._ph = None
        self.bind("<ButtonPress-1>", self._set)
        self.bind("<B1-Motion>", self._set)
        self.bind("<ButtonRelease-1>", lambda e: self.on_release and self.on_release())
        self.bind("<MouseWheel>", self._wheel)
        try:
            self._tr = var.trace_add("write", lambda *a: self.draw())
        except Exception:
            pass
        self.draw()

    def _x_of(self, v):
        span = self.W - 2 * self.R - 2
        return self.R + 1 + span * (v - self.lo) / float(self.hi - self.lo)

    def _set(self, e):
        span = self.W - 2 * self.R - 2
        t = (e.x - self.R - 1) / float(span)
        v = int(round(self.lo + max(0.0, min(1.0, t)) * (self.hi - self.lo)))
        if v != self.var.get():
            self.var.set(v)
            self.cmd(v)

    def _wheel(self, e):
        v = max(self.lo, min(self.hi, self.var.get() + (1 if e.delta > 0 else -1)))
        if v != self.var.get():
            self.var.set(v)
            self.cmd(v)

    def draw(self):
        Z = 4
        w, h = self.W * Z, self.H * Z
        im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        try:
            v = int(self.var.get())
        except Exception:
            v = self.lo
        x = self._x_of(max(self.lo, min(self.hi, v))) * Z
        y = h / 2.0
        th = 3 * Z
        d.rounded_rectangle([self.R * Z, y - th / 2, w - self.R * Z, y + th / 2], radius=th / 2, fill="#0d0f24")
        d.rounded_rectangle([self.R * Z, y - th / 2, max(x, self.R * Z + th), y + th / 2], radius=th / 2,
                            fill=ACC)
        r, hh = self.R * Z, self.R * Z       # круглый тумблер
        d.rounded_rectangle([x - r, y - hh + Z, x + r, y + hh + Z], radius=hh, fill=(0, 0, 0, 70))   # тень
        d.rounded_rectangle([x - r, y - hh, x + r, y + hh], radius=hh, fill="#ffffff")
        im = im.resize((self.W, self.H), Image.LANCZOS)
        self._ph = ImageTk.PhotoImage(im, master=self)
        self.delete("all")
        self.create_image(0, 0, anchor="nw", image=self._ph)


class ToolTip:
    def __init__(self, widget, text):
        self.w, self.text, self.tip, self.job = widget, text, None, None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")

    def _schedule(self, _e=None):
        self.job = self.w.after(450, self.show)

    def show(self):
        if self.tip:
            return
        try:
            x = self.w.winfo_rootx() + 8
            y = self.w.winfo_rooty() + self.w.winfo_height() + 4
            t = self.tip = tk.Toplevel(self.w)
            t.overrideredirect(True)
            t.attributes("-topmost", True)
            t.geometry(f"+{x}+{y}")
            tk.Label(t, text=self.text, bg="#0f1129", fg=FG, relief="flat", bd=0, highlightthickness=1,
                     highlightbackground=EDGE, font=("Segoe UI", 9), justify="left", padx=8, pady=5).pack()
        except tk.TclError:
            self.tip = None

    def hide(self, _e=None):
        if self.job:
            try:
                self.w.after_cancel(self.job)
            except Exception:
                pass
            self.job = None
        if self.tip:
            try:
                self.tip.destroy()
            except Exception:
                pass
            self.tip = None


# ====================================================================== коллаж
COLLAGE_RATIOS = ["Авто", "1:1", "4:3", "3:2", "16:9", "3:4", "2:3", "9:16"]
COLLAGE_COLS = [0, 1, 2, 3, 4, 5, 6]          # 0 — автоматически


def collage_auto_cols(n):
    """1 → 1, 2 → в ряд, 3–4 → 2×2, 5–9 → 3 колонки, дальше квадратная сетка."""
    if n <= 2:
        return max(1, n)
    if n <= 4:
        return 2
    if n <= 9:
        return 3
    return int(math.ceil(math.sqrt(n)))


def load_font(size):
    for name in ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            pass
    try:
        return ImageFont.load_default(size)
    except Exception:
        return ImageFont.load_default()


def build_collage(frames, ratio=None, cols=0, margin=0, gap=None):
    """Лист из кадров: сетка, кадры разделены чёрными границами, подпись «Скриншот N» —
    внутри кадра, внизу слева. ratio — пропорции ячейки (a, b) или None (как у первого кадра);
    пустые ячейки (при нечётном числе кадров) — чёрные."""
    n = len(frames)
    cols = cols if cols and cols > 0 else collage_auto_cols(n)
    cols = max(1, min(cols, n))
    rows = int(math.ceil(n / float(cols)))
    a, b = ratio if ratio else (frames[0].width, frames[0].height)
    cw = max(200, min(1600, max(f.width for f in frames)))
    ch = max(120, int(round(cw * b / float(a))))
    fsize = max(14, cw // 40)
    font = load_font(fsize)
    if gap is None:
        gap = max(4, cw // 120)
    W = margin * 2 + cols * cw + (cols - 1) * gap
    H = margin * 2 + rows * ch + (rows - 1) * gap
    sheet = Image.new("RGB", (W, H), (0, 0, 0))
    for i in range(n):
        r, c = divmod(i, cols)
        x = margin + c * (cw + gap)
        y = margin + r * (ch + gap)
        fr = frames[i].convert("RGB")
        sc = min(1.0, cw / float(fr.width), ch / float(fr.height))
        if sc < 1.0:
            fr = fr.resize((max(1, int(fr.width * sc)), max(1, int(fr.height * sc))), Image.LANCZOS)
        fx, fy = x + (cw - fr.width) // 2, y + (ch - fr.height) // 2
        fr = fr.copy()
        lay = Image.new("RGBA", fr.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(lay)
        text = f"Скриншот {i + 1}"
        pad = max(3, fsize // 4)
        tw = int(d.textlength(text, font=font))
        d.rectangle([0, fr.height - fsize - pad * 2, tw + pad * 2, fr.height], fill=(0, 0, 0, 150))
        d.text((pad, fr.height - pad), text, fill=(255, 255, 255, 255), font=font, anchor="ls")
        fr = Image.alpha_composite(fr.convert("RGBA"), lay).convert("RGB")
        sheet.paste(fr, (fx, fy))
    return sheet


def collage_ratio_tuple(label):
    if label == "Авто":
        return None
    try:
        return parse_ratio(label)[0]
    except (ValueError, ZeroDivisionError):
        return None


def collage_thumb(frames, ratio=None, cols=0, maxw=220, maxh=150, gap=3):
    """Миниатюра листа коллажа: ячейки с цифрами 1, 2, 3… по числу кадров."""
    n = len(frames)
    cols = cols if cols and cols > 0 else collage_auto_cols(n)
    cols = max(1, min(cols, n))
    rows = int(math.ceil(n / float(cols)))
    a, b = ratio if ratio else (frames[0].width, frames[0].height)
    cw = (maxw - (cols - 1) * gap) / float(cols)
    ch = cw * b / float(a)
    if rows * ch + (rows - 1) * gap > maxh:
        ch = (maxh - (rows - 1) * gap) / float(rows)
        cw = ch * a / float(b)
    cw, ch = max(8, int(cw)), max(8, int(ch))
    W, H = cols * cw + (cols - 1) * gap, rows * ch + (rows - 1) * gap
    im = Image.new("RGB", (W, H), (70, 70, 70))
    d = ImageDraw.Draw(im)
    fsize = max(8, int(min(cw, ch) * 0.26))
    font = load_font(fsize)
    for i in range(rows * cols):
        r, c = divmod(i, cols)
        x, y = c * (cw + gap), r * (ch + gap)
        d.rectangle([x, y, x + cw - 1, y + ch - 1], fill=(0, 0, 0))
        if i >= n:
            continue                                    # пустая ячейка — чёрная
        fr = frames[i]
        sc = min(cw / float(fr.width), ch / float(fr.height))
        t = fr.resize((max(1, int(fr.width * sc)), max(1, int(fr.height * sc))), Image.LANCZOS)
        im.paste(t, (x + (cw - t.width) // 2, y + (ch - t.height) // 2))
        d.text((x + cw / 2.0, y + ch / 2.0), str(i + 1), font=font, fill=(255, 255, 255), anchor="mm",
               stroke_width=1, stroke_fill=(0, 0, 0))
    return im


def save_collage_image(img, settings, ask, parent=None):
    """Сохраняет коллаж: сразу в папку (ask=False) или через окно «Сохранить как». Возвращает путь или None."""
    folder = settings.get("last_dir") or default_save_dir()
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    ext = FMT_EXT.get(settings.get("fmt"), ".png")
    if ask:
        path = filedialog.asksaveasfilename(parent=parent, title="Сохранить коллаж как", initialdir=folder,
                                            initialfile=f"Коллаж_{stamp}{ext}", defaultextension=ext,
                                            filetypes=file_types(settings.get("fmt", "PNG")))
        if not path:
            return None
        settings["last_dir"] = os.path.dirname(path)
    else:
        os.makedirs(folder, exist_ok=True)
        path = unique_path(folder, f"Коллаж_{stamp}", ext)
    save_image(img, path)
    return path


class CollageWindow:
    PREV_W, PREV_H = 880, 560

    def __init__(self, app):
        self.app = app
        self.tips = []
        self._icons = {}
        self.photo = None
        st = app.settings
        self.ratio_label = st.get("collage_ratio", "Авто")
        self.cols = int(st.get("collage_cols", 0) or 0)
        w = self.win = tk.Toplevel(app.root)
        w.title(f"{APP_NAME} — коллаж")
        w.configure(bg=BG)
        w.protocol("WM_DELETE_WINDOW", self.hide)
        try:
            w.attributes("-topmost", True)
        except tk.TclError:
            pass
        self.canvas = tk.Canvas(w, width=self.PREV_W, height=self.PREV_H, bg="#1a1a1a", highlightthickness=0)
        self.canvas.pack(padx=8, pady=(8, 4))
        bar = tk.Frame(w, bg=BG, padx=6, pady=4)
        bar.pack(fill="x")
        self.ratio_mb = self._menu_btn(bar, "ratio:custom", "Пропорции ячейки")
        self.ratio_mb.pack(side="left", padx=1)
        self.cols_mb = self._menu_btn(bar, "grid", "Число колонок")
        self.cols_mb.pack(side="left", padx=1)
        self._build_menus()
        tk.Frame(bar, width=1, height=22, bg=BG3).pack(side="left", padx=6)
        self._btn(bar, "undo", self.remove_last, "Убрать последний кадр").pack(side="left", padx=1)
        self._btn(bar, "trash", self.clear, "Очистить коллаж").pack(side="left", padx=1)
        tk.Frame(bar, width=1, height=22, bg=BG3).pack(side="left", padx=6)
        self._btn(bar, "copy", self.copy, "Копировать коллаж в буфер обмена", bg=ACC).pack(side="left", padx=1)
        self._btn(bar, "save", lambda: self.save(False), "Сохранить сразу и закрыть").pack(side="left", padx=1)
        self._btn(bar, "saveas", lambda: self.save(True), "Сохранить как… и закрыть").pack(side="left", padx=1)
        self._btn(bar, "close", self.hide, "Свернуть окно (кадры сохраняются)").pack(side="left", padx=1)
        self.info = tk.Label(bar, text="", bg=BG, fg="#9a9a9a", font=("Segoe UI", 9))
        self.info.pack(side="right", padx=6)
        w.bind("<Escape>", lambda e: self.hide())
        self._place()
        self.refresh()

    # ---- виджеты
    def icon(self, name):
        if name not in self._icons:
            self._icons[name] = ImageTk.PhotoImage(make_icon(name), master=self.win)
        return self._icons[name]

    def _btn(self, parent, icon, cmd, tip, bg=BG2):
        b = RoundBtn(parent, icon, cmd, bg)
        self.tips.append(ToolTip(b, tip))
        return b

    def _menu_btn(self, parent, icon, tip):
        mb = RoundMenuBtn(parent, icon)
        self.tips.append(ToolTip(mb, tip))
        return mb

    def _build_menus(self):
        m = tk.Menu(self.ratio_mb, tearoff=0, font=("Segoe UI", 10))
        for lab in COLLAGE_RATIOS:
            m.add_command(label=("Авто (как первый кадр)" if lab == "Авто" else lab),
                          command=lambda l=lab: self.set_ratio(l))
        m.add_separator()
        m.add_command(label="Своё…", command=self.custom_ratio)
        self.ratio_mb.configure(menu=m)
        m2 = tk.Menu(self.cols_mb, tearoff=0, font=("Segoe UI", 10))
        for c in COLLAGE_COLS:
            m2.add_command(label=("Колонок: авто" if c == 0 else f"Колонок: {c}"),
                           command=lambda v=c: self.set_cols(v))
        self.cols_mb.configure(menu=m2)

    def _place(self):
        self.win.update_idletasks()
        w, h = self.win.winfo_reqwidth(), self.win.winfo_reqheight()
        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        self.win.geometry(f"+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 2 - 20)}")

    # ---- состояние
    def visible(self):
        try:
            return self.win.state() != "withdrawn"
        except tk.TclError:
            return False

    def show(self):
        self.refresh()
        self.win.deiconify()
        self.win.lift()
        try:
            self.win.focus_force()
        except tk.TclError:
            pass

    def hide(self):
        self.win.withdraw()

    def set_ratio(self, label):
        self.ratio_label = label
        self.app.settings["collage_ratio"] = label
        self.refresh()

    def custom_ratio(self):
        v = simpledialog.askstring(APP_NAME, "Пропорции ячейки (например 21:9 или 1.91:1):", parent=self.win)
        if v:
            try:
                if parse_ratio(v.strip())[0] is None:
                    raise ValueError
            except (ValueError, ZeroDivisionError):
                messagebox.showwarning(APP_NAME, "Не понял формат. Примеры: 21:9, 1.91:1", parent=self.win)
                return
            self.set_ratio(v.strip())

    def set_cols(self, c):
        self.cols = c
        self.app.settings["collage_cols"] = c
        self.refresh()

    def ratio_tuple(self):
        if self.ratio_label == "Авто":
            return None
        try:
            return parse_ratio(self.ratio_label)[0]
        except (ValueError, ZeroDivisionError):
            return None

    def sheet(self):
        return build_collage(self.app.frames, self.ratio_tuple(), self.cols)

    def refresh(self):
        frames = self.app.frames
        self.canvas.delete("all")
        if not frames:
            self.photo = None
            self.info.configure(text="Кадров нет: PrtScn → выделить → «в коллаж»")
            return
        sh = self.sheet()
        sc = min(self.PREV_W / float(sh.width), self.PREV_H / float(sh.height), 1.0)
        prev = sh.resize((max(1, int(sh.width * sc)), max(1, int(sh.height * sc))), Image.LANCZOS)
        self.photo = ImageTk.PhotoImage(prev, master=self.win)
        self.canvas.create_image(self.PREV_W // 2, self.PREV_H // 2, image=self.photo)
        self.info.configure(text=f"Кадров: {len(frames)}   Лист: {sh.width}×{sh.height}")

    def remove_last(self):
        if self.app.frames:
            self.app.frames.pop()
        if not self.app.frames:
            self.hide()
        self.refresh()

    def clear(self):
        self.app.frames.clear()
        self.hide()
        self.refresh()

    def copy(self):
        if not self.app.frames:
            return
        try:
            copy_image_to_clipboard(self.sheet())
        except Exception as ex:
            messagebox.showerror(APP_NAME, f"Не удалось скопировать: {ex}", parent=self.win)

    def save(self, ask):
        if not self.app.frames:
            return
        img = self.sheet()
        st = self.app.settings
        folder = st.get("last_dir") or default_save_dir()
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        try:
            if ask:
                self.win.attributes("-topmost", False)
                try:
                    path = filedialog.asksaveasfilename(parent=self.win, title="Сохранить коллаж как",
                                                        initialdir=folder, initialfile=f"Коллаж_{stamp}.png",
                                                        defaultextension=".png",
                                                        filetypes=[("PNG", "*.png"), ("JPEG", "*.jpg *.jpeg")])
                finally:
                    self.win.attributes("-topmost", True)
                if not path:
                    return
                st["last_dir"] = os.path.dirname(path)
            else:
                os.makedirs(folder, exist_ok=True)
                path = unique_path(folder, f"Коллаж_{stamp}")
            save_image(img, path)
        except Exception as ex:
            messagebox.showerror(APP_NAME, f"Не удалось сохранить: {ex}", parent=self.win)
            return
        self.app.frames.clear()          # после сохранения коллаж завершён: окно закрывается
        self.hide()
        save_settings(self.app.settings)


# ===================================================================== оверлей
class Overlay:
    HS = 5      # половина размера квадратной ручки
    HIT = 9     # радиус попадания по ручке

    def __init__(self, app, img, vx, vy, primary, cursor=None, late=False):
        self.app = app
        self.img = img
        self.W, self.H = img.size
        self.late_origin = bool(late)        # режим «снимок при сохранении»: экран живой с самого начала
        self.collage = False
        self.vx, self.vy = vx, vy
        self.dim = None
        self.dim_hwnd = None
        self.catch = None
        self.catch_hwnd = None
        self._catch_key = None
        self.primary = primary
        s = app.settings
        self.img_clean = img
        self.cursor = cursor
        self.img_cur = paste_cursor(img, cursor)
        self.show_cursor = bool(s.get("cursor", True)) and cursor is not None
        self.img = self.img_cur if self.show_cursor else self.img_clean
        self.tool = None                      # при открытии ни один инструмент не включён: рисовать нельзя, пока не выбран
        self.color = s.get("color", "#ff2d2d")
        self.width = int(s.get("width", 4))
        self.alpha = max(10, min(100, int(s.get("alpha", 100))))
        rd = s.get("round")
        if rd is None:                       # перенос старой настройки «скругление вкл/выкл»
            rd = 0 if s.get("smooth") is False else 100
        self.round = max(0, min(100, int(rd)))
        self.ratio_label = s.get("ratio", "Свободно")
        try:
            self.ratio, self.fixed = parse_ratio(self.ratio_label)
        except ValueError:
            self.ratio_label, self.ratio, self.fixed = "Свободно", None, None

        self.shapes, self.undo, self.redo = [], [], []
        self.live = False
        self.sel = None
        wh = s.get("sel_wh")
        sv = s.get("sel")
        try:
            if sv and wh and tuple(wh) == (self.W, self.H):
                x0, y0, x1, y1 = [int(v) for v in sv]
                if 0 <= x0 < x1 <= self.W and 0 <= y0 < y1 <= self.H and x1 - x0 > 4 and y1 - y0 > 4:
                    self.sel = (x0, y0, x1, y1)           # положение выделения запоминается
        except (TypeError, ValueError):
            self.sel = None
        self.drag = None
        self.cur = None
        self._cursor = None
        self.tips = []
        self._icons = {}
        self._popup = None

        w = self.win = tk.Toplevel(app.root)
        if late:
            w.withdraw()                      # покажем уже прозрачным, без вспышки замороженного снимка
        w.overrideredirect(True)
        w.geometry(f"{self.W}x{self.H}+{vx}+{vy}")
        w.configure(bg="black")
        try:
            w.attributes("-transparentcolor", self.LIVE_KEY)     # включается заранее: без вспышек при переходах
        except tk.TclError:
            pass
        try:
            w.attributes("-topmost", True)
        except tk.TclError:
            pass
        c = self.canvas = tk.Canvas(w, width=self.W, height=self.H, highlightthickness=0,
                                    bd=0, bg="black", cursor="arrow")
        c.pack(fill="both", expand=True)

        self.tk_dim = ImageTk.PhotoImage(ImageEnhance.Brightness(self.img).enhance(self.dim_factor()), master=w)
        self.bg_id = c.create_image(0, 0, anchor="nw", image=self.tk_dim, tags="bg")
        self.tk_bright = None
        self.bright_id = c.create_image(0, 0, anchor="nw", tags="bright", state="hidden")

        self._build_toolbar()
        self.tb_id = c.create_window(0, 0, anchor="nw", window=self.tb, state="hidden")
        self._build_collage_panel()
        self.affinity = False

        c.bind("<ButtonPress-1>", self.on_down)
        c.bind("<B1-Motion>", self.on_drag)
        c.bind("<ButtonRelease-1>", self.on_up)
        c.bind("<Motion>", self.on_motion)
        c.bind("<MouseWheel>", self.on_wheel)
        w.bind("<KeyPress>", self.on_key)

        self.refresh_toolbar()
        self.refresh_collage_panel()
        self.update_view()
        if not late:
            w.after(40, self._focus)
            w.after(250, self._focus)

    # ------------------------------------------------------------ служебное
    def _focus(self):
        try:
            self.win.lift()
            force_foreground(self.win)
            self.win.focus_force()
            self.canvas.focus_set()
        except tk.TclError:
            pass

    @contextmanager
    def topmost_off(self):
        try:
            self.win.attributes("-topmost", False)
        except tk.TclError:
            pass
        try:
            yield
        finally:
            try:
                self.win.attributes("-topmost", True)
                self._focus()
            except tk.TclError:
                pass

    def close(self):
        for t in self.tips:
            t.hide()
        self.app.frames.clear()                 # выход из режима коллажа удаляет созданные кадры
        st = self.app.settings
        if self.sel:
            st["sel"] = [int(v) for v in self.sel]
            st["sel_wh"] = [self.W, self.H]
        st.update(tool=self.tool, color=self.color, width=self.width, alpha=self.alpha,
                  round=self.round, ratio=self.ratio_label, cursor=self.show_cursor or
                  (self.cursor is None and st.get("cursor", True)))
        self._kill_catch()
        self._kill_dim()
        try:
            self.win.destroy()
        except tk.TclError:
            pass
        self.app.on_overlay_closed()

    # ------------------------------------------------------------ панель
    def icon(self, name):
        if name not in self._icons:
            self._icons[name] = ImageTk.PhotoImage(make_icon(name), master=self.win)
        return self._icons[name]

    def _btn(self, parent, icon, cmd, tip, bg=BG2):
        b = RoundBtn(parent, icon, cmd, bg)
        tt = ToolTip(b, tip)
        self.tips.append(tt)
        b.tip = tt
        return b

    def _sep(self, parent):
        tk.Frame(parent, width=1, height=20, bg=EDGE).pack(side="left", padx=3)

    def _slider(self, parent, label, tip, lo, hi, var, cmd):
        box = tk.Frame(parent, bg=BG)
        box.pack(side="left", padx=2)
        lab = tk.Label(box, text=label, bg=BG, fg="#9aa3d4", font=("Segoe UI", 8))
        lab.pack(anchor="w", padx=2)
        row = tk.Frame(box, bg=BG)
        row.pack(anchor="w")
        num = tk.Label(row, width=3, anchor="e", bg=BG, fg=FG, font=("Segoe UI", 9, "bold"))
        num.pack(side="left")

        def upd(*_a):
            try:
                num.configure(text=str(int(var.get())))
            except (tk.TclError, ValueError):
                pass
        var.trace_add("write", upd)
        upd()
        sc = FlatSlider(row, lo, hi, var, cmd, on_release=self.canvas.focus_set)
        sc.pack(side="left")
        for w in (sc, lab, num):
            self.tips.append(ToolTip(w, tip))
        return sc

    def _build_toolbar(self):
        tb = self.tb = tk.Frame(self.canvas, bg=BG, padx=6, pady=6,
                                highlightthickness=1, highlightbackground=EDGE)

        r1 = tk.Frame(tb, bg=BG)
        r1.pack(anchor="w", fill="x")
        self.ratio_btn = self._btn(r1, "ratio:free", lambda: self.open_popup(self.ratio_btn, self.build_ratios),
                                   "Пропорции выделения  (клавиши 0–8)")
        self.ratio_btn.pack(side="left", padx=1)
        self._sep(r1)
        r2 = self.r2 = tk.Frame(tb, bg=BG)
        self.tool_btns = {}
        for tid, _glyph, tip in TOOLS:
            if tid in ("rect", "ellipse"):
                continue                                    # фигуры — в выпадающем окошке
            parent = r1 if tid == "select" else r2          # инструменты рисования — в нижнем поле
            b = self._btn(parent, tid, lambda t=tid: self.set_tool(t), tip)
            b.pack(side="left", padx=1)
            self.tool_btns[tid] = b
        self.shape_kind = self.tool if self.tool in ("rect", "ellipse") else "rect"
        self.shape_btn = self._btn(r2, self.shape_kind, lambda: self.open_popup(self.shape_btn, self.build_shapes),
                                   "Фигуры: прямоугольник (R) и круг (O)\nНажмите, чтобы выбрать")
        self.shape_btn.pack(side="left", padx=1)
        self._sep(r1)
        self._btn(r1, "undo", self.do_undo, "Отменить  (Ctrl+Z)").pack(side="left", padx=1)
        self._btn(r1, "redo", self.do_redo, "Повторить  (Ctrl+Y)").pack(side="left", padx=1)
        self._sep(r1)
        self.cursor_btn = self._btn(r1, "cursor", self.toggle_cursor,
                                    "Показывать курсор мыши на снимке\n(вкл — курсор виден в итоговой картинке)")
        self.cursor_btn.pack(side="left", padx=1)
        self.collage_btn = self._btn(r1, "collage_add", self.collage_click, "")
        self.collage_btn.pack(side="left", padx=1)
        self.snap_btn = self._btn(r1, "shot", self.live_snap,
                                  "Снять кадр в рамке и добавить в коллаж  (PrtScn)\n"
                                  "Экран не замораживается: можно смотреть видео и снимать подряд", bg=ACC)
        self.snap_btn.pack(side="left", padx=1)
        self.snap_btn.set_enabled(False)
        self._sep(r1)
        self._btn(r1, "save", self.do_save,
                  "Сохранить сразу в папку скриншотов и закрыть  (Ctrl+S)").pack(side="left", padx=1)
        self._btn(r1, "saveas", self.do_save_as,
                  "Сохранить как… (выбрать папку и имя) и закрыть  (Ctrl+Shift+S)").pack(side="left", padx=1)
        self.fmt_btn = self._btn(r1, "quality", lambda: self.open_popup(self.fmt_btn, self.build_fmt), "")
        self.fmt_btn.pack(side="left", padx=1)
        self._btn(r1, "close", self.close, "Закрыть  (Esc)").pack(side="left", padx=1)
        self._sep(r1)
        self.more_btn = self._btn(r1, "chev_d", self.toggle_bar2, "")
        self.more_btn.pack(side="left", padx=1)

        self._sep(r2)
        self.color_btn = self._btn(r2, "plus", lambda: self.open_popup(self.color_btn, self.build_colors),
                                   "Цвет рисования")
        self.color_btn.pack(side="left", padx=1)
        self._sep(r2)
        self.width_var = tk.IntVar(value=self.width)
        self._slider(r2, "Толщина", "Толщина линии  (колесо мыши, [ и ])", 1, 40, self.width_var, self.set_width)
        self._sep(r2)
        self.round_var = tk.IntVar(value=self.round)
        self._slider(r2, "Скругление", "Скругление, %\n0 — линия как нарисована, 100 — максимальный изгиб:\n"
                                  "углы и кривые превращаются в ровные дуги\n"
                                  "Для карандаша и прямоугольника  (S — вкл/выкл)",
                     0, 100, self.round_var, self.set_round)
        self.bar2_on = bool(self.app.settings.get("bar2", False))
        if self.bar2_on:
            r2.pack(anchor="w", fill="x", pady=(7, 0))

    def toggle_bar2(self):
        self.close_popup()
        self.bar2_on = not self.bar2_on
        self.app.settings["bar2"] = self.bar2_on
        if self.bar2_on:
            self.r2.pack(anchor="w", fill="x", pady=(7, 0))
        else:
            self.r2.pack_forget()
        self.refresh_toolbar()
        self.redraw_ui()
        self.canvas.focus_set()

    def dim_factor(self):
        try:
            return 1.0 - max(0, min(90, int(self.app.settings.get("dim", 50)))) / 100.0
        except (TypeError, ValueError):
            return 0.5

    def set_dim(self, v):
        self.app.settings["dim"] = int(v)
        if self.dim is not None:
            try:
                self.dim.attributes("-alpha", int(v) / 100.0)
            except tk.TclError:
                pass
        try:
            self.tk_dim = ImageTk.PhotoImage(ImageEnhance.Brightness(self.img).enhance(self.dim_factor()),
                                             master=self.win)
            self.canvas.itemconfigure(self.bg_id, image=self.tk_dim)
        except tk.TclError:
            pass

    HOTKEYS = [
        ("Общие", [("PrtScn", "Сделать скриншот; второе нажатие — сохранить и закрыть"),
                   ("Esc", "Выход из режима скриншота"),
                   ("Enter", "Скопировать выделенное в буфер обмена"),
                   ("Ctrl+S", "Быстрое сохранение"),
                   ("Ctrl+Shift+S", "Сохранить как…"),
                   ("Ctrl+C", "Скопировать в буфер обмена"),
                   ("Ctrl+A", "Выделить весь экран"),
                   ("Ctrl+Z / Ctrl+Y", "Отменить / повторить (Ctrl+Shift+Z — повторить)"),
                   ("Стрелки (Shift — ×10)", "Сдвинуть рамку выделения")]),
        ("Инструменты", [("V", "Перемещение рамки (повторно — отжать)"),
                         ("P", "Карандаш"), ("L", "Линия"), ("C", "Кривая по 3 точкам"),
                         ("A", "Стрелка"), ("R", "Прямоугольник"), ("O", "Круг / эллипс"),
                         ("S", "Скругление карандаша вкл/выкл"),
                         ("[  и  ]", "Толщина меньше / больше"),
                         ("0 … 8", "Выбор пропорций рамки"),
                         ("Shift при рисовании", "Шаг 15° / квадрат / ровный круг")]),
    ]

    def open_hotkeys(self):
        self.close_popup()
        old = getattr(self, "_hk_win", None)
        if old is not None:
            try:
                old.destroy()
            except tk.TclError:
                pass
        w = self._hk_win = tk.Toplevel(self.app.root)
        w.title("Горячие клавиши")
        w.configure(bg=BG)
        w.attributes("-topmost", True)
        w.resizable(False, False)
        body = tk.Frame(w, bg=BG, padx=18, pady=14)
        body.pack()
        tk.Label(body, text=f"{APP_NAME}  v{VERSION}", bg=BG, fg=FG, font=("Segoe UI", 12, "bold")).pack(anchor="w")
        tk.Label(body, text="Год создания: 2026", bg=BG, fg="#9aa3b2", font=("Segoe UI", 9)).pack(anchor="w")
        drow = tk.Frame(body, bg=BG)
        drow.pack(anchor="w")
        tk.Label(drow, text="Разработчик: ", bg=BG, fg="#9aa3b2", font=("Segoe UI", 9)).pack(side="left")
        lk = tk.Label(drow, text="GitHub", bg=BG, fg=ACC_H, cursor="hand2", font=("Segoe UI", 9, "underline"))
        lk.pack(side="left")
        lk.bind("<Button-1>", lambda e: self.open_repo())
        tk.Frame(body, height=1, bg=EDGE).pack(fill="x", pady=(8, 2))
        for title, rows in self.HOTKEYS:
            tk.Label(body, text=title, bg=BG, fg=FG, font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(6, 3))
            g = tk.Frame(body, bg=BG)
            g.pack(anchor="w")
            for i, (k, d) in enumerate(rows):
                tk.Label(g, text=k, bg=BG2, fg=FG, font=("Segoe UI", 9, "bold"), padx=8, pady=2,
                         anchor="w", width=20).grid(row=i, column=0, sticky="we", padx=(0, 8), pady=1)
                tk.Label(g, text=d, bg=BG, fg="#c9cfe8", font=("Segoe UI", 9), anchor="w").grid(
                    row=i, column=1, sticky="w")
        tk.Button(body, text="Закрыть", relief="flat", bd=0, bg=ACC, fg=FG, activebackground=ACC_H,
                  activeforeground=FG, font=("Segoe UI", 9), padx=16, pady=4, cursor="hand2",
                  command=w.destroy).pack(anchor="e", pady=(10, 0))
        w.bind("<Escape>", lambda e: w.destroy())
        w.update_idletasks()
        x = self.vx + (self.W - w.winfo_width()) // 2
        y = self.vy + (self.H - w.winfo_height()) // 2
        w.geometry(f"+{max(0, x)}+{max(0, y)}")
        w.focus_force()

    REPO_URL = "https://github.com/Al235r/Screenshoter"

    def open_repo(self):
        """Открыть страницу проекта: окна редактора на время уходят из-под «поверх всех окон», иначе браузер не виден."""
        wins = [w for w in (self.win, self.dim, self.catch, getattr(self, "_hk_win", None)) if w is not None]
        for w in wins:
            try:
                w.attributes("-topmost", False)
            except tk.TclError:
                pass
        try:
            import webbrowser
            webbrowser.open(self.REPO_URL)
        except Exception:
            pass

        def back():
            for w in wins:
                try:
                    w.attributes("-topmost", True)
                except tk.TclError:
                    pass
        self.win.after(6000, back)

    def build_fmt(self, fr):
        st = self.app.settings
        row = tk.Frame(fr, bg=BG)
        row.pack(anchor="w")
        self._fmt_btns = {}
        for f in FORMATS:
            b = tk.Button(row, text=f, width=6, relief="flat", bd=0, cursor="hand2", takefocus=0,
                          bg=ACC if f == st.get("fmt") else BG2, fg=FG, activebackground=BG3,
                          activeforeground=FG, font=("Segoe UI", 9), pady=3,
                          command=lambda x=f: self.set_fmt(x))
            b.pack(side="left", padx=1)
            self._fmt_btns[f] = b
        tk.Label(fr, text="Качество (JPG, WebP)", bg=BG, fg=FG, font=("Segoe UI", 9)).pack(anchor="w", pady=(6, 0))
        self._q_var = tk.IntVar(value=int(st.get("quality", 90)))
        sc = tk.Scale(fr, from_=1, to=100, orient="horizontal", variable=self._q_var, length=210,
                      bg=BG, fg=FG, troughcolor=BG2, highlightthickness=0, bd=0, sliderlength=14,
                      font=("Segoe UI", 8), command=lambda v: self.set_quality(int(float(v))))
        sc.pack(anchor="w")
        tk.Label(fr, text="PNG и BMP сохраняются без потерь;\nWebP при 100 — без потерь", bg=BG, fg="#9aa3b2",
                 font=("Segoe UI", 8), justify="left").pack(anchor="w")
        drow = tk.Frame(fr, bg=BG)
        drow.pack(anchor="w", pady=(8, 0))
        ic = tk.Label(drow, image=self.icon("dim"), bg=BG)
        ic.pack(side="left")
        self._d_var = tk.IntVar(value=int(st.get("dim", 50)))
        num = tk.Label(drow, width=3, anchor="e", bg=BG, fg=FG, font=("Segoe UI", 9, "bold"))
        num.pack(side="left", padx=(4, 0))
        self._d_var.trace_add("write", lambda *a: num.configure(text=str(self._d_var.get())))
        num.configure(text=str(self._d_var.get()))
        ds = FlatSlider(drow, 0, 90, self._d_var, self.set_dim, length=150)
        ds.pack(side="left")
        for w in (ic, ds, num):
            self.tips.append(ToolTip(w, "Затемнение области вне рамки, %\n0 — без затемнения"))
        brow = tk.Frame(fr, bg=BG)
        brow.pack(anchor="w", pady=(8, 0))
        self._late_btn = self._btn(brow, "late", self.toggle_late, "")
        self._late_btn.pack(side="left", padx=(0, 4))
        self._late_refresh()
        kb = self._btn(brow, "keys", self.open_hotkeys, "Горячие клавиши и о программе")
        kb.pack(side="left")

    def _late_refresh(self):
        on = bool(self.app.settings.get("late_capture"))
        self._late_btn.configure(bg=ACC if on else BG2)
        self._late_btn.tip.text = ("Снимок при сохранении: " + ("ВКЛ" if on else "ВЫКЛ") +
                                   "\nВкл — после PrtScn экран не замораживается:\n"
                                   "снимок делается в момент нажатия «Сохранить».\n"
                                   "Действует со следующего нажатия PrtScn")

    def toggle_late(self):
        self.app.settings["late_capture"] = not self.app.settings.get("late_capture")
        self._late_refresh()

    def set_fmt(self, f):
        self.app.settings["fmt"] = f
        for k, b in self._fmt_btns.items():
            b.configure(bg=ACC if k == f else BG2)
        self.refresh_toolbar()

    def set_quality(self, q):
        self.app.settings["quality"] = q
        QUALITY[0] = q
        self.refresh_toolbar()

    # ------------------------------------------------------------ выпадающие мини-окна
    def close_popup(self):
        if self._popup is not None:
            try:
                self.canvas.delete(self._popup[0])
                self._popup[1].destroy()
            except tk.TclError:
                pass
            self._popup = None

    def open_popup(self, btn, build):
        if self._popup is not None:          # повторный клик по кнопке закрывает окно
            was = self._popup[2]
            self.close_popup()
            if was is btn:
                return
        fr = tk.Frame(self.canvas, bg=BG, padx=6, pady=6, highlightthickness=1, highlightbackground=EDGE)
        build(fr)
        fr.update_idletasks()
        w, h = fr.winfo_reqwidth(), fr.winfo_reqheight()
        bx = btn.winfo_rootx() - self.canvas.winfo_rootx()
        by = btn.winfo_rooty() - self.canvas.winfo_rooty()
        y = by + btn.winfo_height() + 4
        if y + h > self.H:
            y = by - h - 4
        tx = self.tb.winfo_rootx() - self.canvas.winfo_rootx()
        tw = self.tb.winfo_width()
        x = max(tx, min(tx + tw - w, bx))              # внутри границ панели
        x = max(0, min(self.W - w, x))
        y = max(0, min(self.H - h, y))
        item = self.canvas.create_window(x, y, anchor="nw", window=fr, tags="popup")
        self._popup = (item, fr, btn)

    def build_shapes(self, fr):
        for kind, tip in (("rect", "Прямоугольник  (R)\nShift — квадрат"), ("ellipse", "Круг / эллипс  (O)\nShift — ровный круг")):
            b = self._btn(fr, kind, lambda k=kind: (self.close_popup(), self.set_tool(k)), tip,
                          bg=ACC if self.tool == kind else BG2)
            b.pack(side="top", pady=1)

    def build_colors(self, fr):
        cols, sz = 6, 18
        for i, col in enumerate(PALETTE):
            sel = col.lower() == self.color.lower()
            sw = tk.Frame(fr, width=sz, height=sz, bg=col, cursor="hand2", highlightthickness=2,
                          highlightbackground="#ffffff" if sel else BG)
            sw.grid(row=i // cols, column=i % cols, padx=1, pady=1)
            sw.bind("<Button-1>", lambda e, c=col: (self.close_popup(), self.set_color(c)))
        # компактный выбор любого цвета: квадрат насыщенности/яркости + полоса оттенка
        pw = cols * (sz + 6)
        ph = 64
        rows = (len(PALETTE) + cols - 1) // cols
        try:
            h0 = colorsys.rgb_to_hsv(*[int(self.color[i:i + 2], 16) / 255.0 for i in (1, 3, 5)])
        except Exception:
            h0 = (0.0, 1.0, 1.0)
        state = {"h": h0[0]}
        sv = tk.Canvas(fr, width=pw, height=ph, highlightthickness=0, bd=0, cursor="crosshair")
        sv.grid(row=rows, column=0, columnspan=cols, pady=(6, 0))
        hb = tk.Canvas(fr, width=pw, height=10, highlightthickness=0, bd=0, cursor="hand2")
        hb.grid(row=rows + 1, column=0, columnspan=cols, pady=(4, 0))
        hue_img = Image.new("RGB", (pw, 1))
        for x in range(pw):
            hue_img.putpixel((x, 0), tuple(int(v * 255) for v in colorsys.hsv_to_rgb(x / float(pw), 1, 1)))
        self._hue_photo = ImageTk.PhotoImage(hue_img.resize((pw, 10)), master=fr)
        hb.create_image(0, 0, anchor="nw", image=self._hue_photo)

        def draw_sv():
            im = Image.new("RGB", (pw, ph))
            px = im.load()
            for x in range(pw):
                for y in range(ph):
                    px[x, y] = tuple(int(v * 255) for v in colorsys.hsv_to_rgb(state["h"], x / float(pw - 1),
                                                                                1 - y / float(ph - 1)))
            self._sv_photo = ImageTk.PhotoImage(im, master=fr)
            sv.delete("all")
            sv.create_image(0, 0, anchor="nw", image=self._sv_photo)

        def pick_sv(e):
            x = max(0, min(pw - 1, e.x))
            y = max(0, min(ph - 1, e.y))
            r, g, b = colorsys.hsv_to_rgb(state["h"], x / float(pw - 1), 1 - y / float(ph - 1))
            self.set_color("#%02x%02x%02x" % (int(r * 255), int(g * 255), int(b * 255)))

        def pick_h(e):
            state["h"] = max(0, min(pw - 1, e.x)) / float(pw)
            draw_sv()

        sv.bind("<Button-1>", pick_sv)
        sv.bind("<B1-Motion>", pick_sv)
        hb.bind("<Button-1>", pick_h)
        hb.bind("<B1-Motion>", pick_h)
        draw_sv()
        # прозрачность рисования: 0 — сплошной цвет
        trow = tk.Frame(fr, bg=BG)
        trow.grid(row=rows + 2, column=0, columnspan=cols, pady=(8, 0), sticky="w")
        tk.Label(trow, text="Прозрачность", bg=BG, fg="#9aa3d4", font=("Segoe UI", 8)).pack(anchor="w", padx=2)
        r2_ = tk.Frame(trow, bg=BG)
        r2_.pack(anchor="w")
        tv = self._t_var = tk.IntVar(value=100 - self.alpha)
        num = tk.Label(r2_, width=3, anchor="e", bg=BG, fg=FG, font=("Segoe UI", 9, "bold"))
        num.pack(side="left")
        tv.trace_add("write", lambda *a: num.configure(text=str(tv.get())))
        num.configure(text=str(tv.get()))
        ts = FlatSlider(r2_, 0, 90, tv, lambda v: self.set_alpha(100 - v), length=pw - 30)
        ts.pack(side="left")
        self.tips.append(ToolTip(ts, "Прозрачность рисования, %\n0 — сплошной цвет"))

    def build_ratios(self, fr):
        items = [(lab, ratio_icon_name(lab), "Свободно" if i == 0 else lab) for i, lab in enumerate(RATIOS)]
        custom = self.ratio_label not in RATIOS
        items.append((None, "ratio:custom", self.ratio_label if custom else "Своё…"))
        cols = 2
        for c in range(cols):
            fr.grid_columnconfigure(c, uniform="ratio", weight=1)
        for i, (lab, icon, text) in enumerate(items):
            active = (lab == self.ratio_label) if lab else custom
            cmd = (lambda l=lab: self.set_ratio(l)) if lab else self.custom_ratio
            b = tk.Button(fr, image=self.icon(icon), text=" " + text, compound="left", anchor="w",
                          bg=ACC if active else BG2, fg=FG, activebackground=BG3, activeforeground=FG,
                          relief="flat", bd=0, cursor="hand2", takefocus=0, font=("Segoe UI", 9),
                          padx=6, pady=2, command=cmd)
            b.grid(row=i // cols, column=i % cols, sticky="ew", padx=1, pady=1)
            if not lab:
                self.tips.append(ToolTip(b, "Своё соотношение (21:9, 1.91:1)\nили точный размер в пикселях (1080x1350)"))

    def swatch(self, col):
        """Квадратик цвета того же размера, что и значки кнопок."""
        n = ICON_PX * 4
        im = Image.new("RGBA", (n, n), (0, 0, 0, 0))
        m = int(n * 0.14)
        ImageDraw.Draw(im).rounded_rectangle([m, m, n - m - 1, n - m - 1], radius=int(n * 0.16), fill=col,
                                             outline="#ffffff", width=4)
        return ("sw" + col, im.resize((ICON_PX, ICON_PX), Image.LANCZOS))

    def refresh_toolbar(self):
        for tid, b in self.tool_btns.items():
            b.configure(bg=ACC if tid == self.tool else BG2)
        self.shape_btn.set_icon(self.shape_kind)
        self.shape_btn.configure(bg=ACC if self.tool in ("rect", "ellipse") else BG2)
        self.color_btn.set_icon(self.swatch(self.color))
        if self.width_var.get() != self.width:
            self.width_var.set(self.width)
        if self.round_var.get() != self.round:
            self.round_var.set(self.round)
        name = ratio_icon_name(self.ratio_label) if self.ratio_label in RATIOS else "ratio:custom"
        self.ratio_btn.set_icon(name)
        st = self.app.settings
        self.fmt_btn.tip.text = (f"Настройки (формат, качество, затемнение, горячие клавиши): {st.get('fmt', 'PNG')}" +
                                 (f", качество {st.get('quality', 90)}" if st.get("fmt") in ("JPG", "WEBP") else ""))
        self.more_btn.set_icon("chev_u" if self.bar2_on else "chev_d")
        self.more_btn.tip.text = ("Скрыть инструменты рисования, цвет, толщину" if self.bar2_on
                                  else "Показать инструменты рисования, цвет, толщину")
        self.cursor_btn.configure(bg=ACC if self.show_cursor else BG2)
        n = len(self.app.frames)
        self.collage_btn.configure(bg=ACC if self.collage else BG2)
        self.snap_btn.set_enabled(self.collage)
        self.snap_btn.tip.text = ("Снять кадр в рамке и добавить в коллаж  (PrtScn)\n"
                                  "Экран не замораживается: можно смотреть видео и снимать подряд" if self.collage else
                                  "Кадр коллажа — станет доступен после включения режима коллажа\n(кнопка слева)")
        if self.collage:
            self.collage_btn.tip.text = (f"Режим коллажа включён (кадров: {n})\n"
                                         "Нажмите, чтобы выйти — созданные кадры будут удалены")
        else:
            self.collage_btn.tip.text = ("Режим коллажа: экран оживает, кадры снимаются кнопкой кадра или PrtScn\n"
                                         "Кадры собираются на одном листе с подписями «Скриншот 1, 2…»")
        self.ratio_btn.tip.text = "Пропорции: " + ("свободные" if self.ratio is None else self.ratio_label) + \
            "  (клавиши 0–8)"

    @staticmethod
    def _contrast(col):
        try:
            r, g, b = int(col[1:3], 16), int(col[3:5], 16), int(col[5:7], 16)
            return "#000000" if (0.299 * r + 0.587 * g + 0.114 * b) > 150 else "#ffffff"
        except Exception:
            return "#ffffff"

    # ------------------------------------------------------------ состояние
    def set_tool(self, t):
        if t == self.tool:                               # повторное нажатие отжимает инструмент
            t = None
        self.tool = t
        if t in ("rect", "ellipse"):
            self.shape_kind = t
        if self.live:
            self._sync_catch()
        self.refresh_toolbar()
        self.redraw_ui()
        self.canvas.focus_set()

    def set_color(self, c):
        self.color = c
        self.refresh_toolbar()
        self.canvas.focus_set()

    def pick_color(self):
        self.close_popup()
        with self.topmost_off():
            res = colorchooser.askcolor(color=self.color, parent=self.win, title="Цвет рисования")
        if res and res[1]:
            self.set_color(res[1])

    def set_alpha(self, a):
        self.alpha = max(10, min(100, int(a)))

    def set_width(self, w):
        self.width = max(1, min(40, int(w)))
        if self.width_var.get() != self.width:
            self.width_var.set(self.width)

    def set_round(self, v):
        self.round = max(0, min(100, int(v)))
        if self.round_var.get() != self.round:
            self.round_var.set(self.round)

    def toggle_round(self):
        self.set_round(0 if self.round > 50 else 100)
        self.canvas.focus_set()

    def set_ratio(self, label):
        self.close_popup()
        try:
            r, fixed = parse_ratio(label)
        except (ValueError, ZeroDivisionError):
            with self.topmost_off():
                messagebox.showwarning(APP_NAME, "Не понял формат. Примеры: 21:9, 1.91:1, 1080x1350",
                                       parent=self.win)
            return
        self.ratio_label, self.ratio, self.fixed = label, r, fixed
        if self.sel and r:
            self.apply_ratio_to_sel()
        self.refresh_toolbar()
        self.update_view()
        self.canvas.focus_set()

    def custom_ratio(self):
        self.close_popup()
        init = self.ratio_label if self.ratio_label not in RATIOS else ""
        with self.topmost_off():
            v = simpledialog.askstring(APP_NAME, "Соотношение сторон (например 21:9 или 1.91:1)\n"
                                                 "или точный размер в пикселях (например 1080x1350):",
                                       initialvalue=init, parent=self.win)
        if v:
            self.set_ratio(v.strip())

    def push_undo(self):
        self.undo.append(copy.deepcopy(self.shapes))
        if len(self.undo) > 300:
            self.undo.pop(0)
        self.redo.clear()

    def do_undo(self):
        if self.undo:
            self.redo.append(copy.deepcopy(self.shapes))
            self.shapes = self.undo.pop()
            self.redraw_shapes()
            self.redraw_ui()

    def do_redo(self):
        if self.redo:
            self.undo.append(copy.deepcopy(self.shapes))
            self.shapes = self.redo.pop()
            self.redraw_shapes()
            self.redraw_ui()

    # ------------------------------------------------------------ выделение
    def clamp(self, x, y):
        return (min(max(x, 0), self.W), min(max(y, 0), self.H))

    def clamp_to_sel(self, x, y):
        if not self.sel:
            return self.clamp(x, y)
        x0, y0, x1, y1 = self.sel
        return (min(max(x, x0), x1), min(max(y, y0), y1))

    def in_sel(self, x, y):
        s = self.sel
        return bool(s) and s[0] <= x <= s[2] and s[1] <= y <= s[3]

    @staticmethod
    def norm(x0, y0, x1, y1):
        return (int(round(min(x0, x1))), int(round(min(y0, y1))),
                int(round(max(x0, x1))), int(round(max(y0, y1))))

    def rect_from_anchor(self, ax, ay, x, y):
        x, y = self.clamp(x, y)
        if not self.ratio:
            return self.norm(ax, ay, x, y)
        a, b = self.ratio
        dx, dy = x - ax, y - ay
        sx = 1 if dx >= 0 else -1
        sy = 1 if dy >= 0 else -1
        n = max(abs(dx) / a, abs(dy) / b)
        maxw = (self.W - ax) if sx > 0 else ax
        maxh = (self.H - ay) if sy > 0 else ay
        n = int(min(n, maxw / a, maxh / b))           # целое число «шагов» — пропорция точная
        return self.norm(ax, ay, ax + sx * n * a, ay + sy * n * b)

    def fit_rect(self, cx, cy, w, h):
        w, h = min(w, self.W), min(h, self.H)
        x0 = max(0, min(self.W - w, int(round(cx - w / 2))))
        y0 = max(0, min(self.H - h, int(round(cy - h / 2))))
        return (x0, y0, x0 + w, y0 + h)

    def fixed_size(self):
        w, h = self.fixed
        if w > self.W or h > self.H:
            a, b = self.ratio
            n = min(self.W // a, self.H // b)
            w, h = n * a, n * b
        return w, h

    def apply_ratio_to_sel(self):
        x0, y0, x1, y1 = self.sel
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        a, b = self.ratio
        if self.fixed:
            w, h = self.fixed_size()
        else:
            area = max(1, (x1 - x0) * (y1 - y0))
            n = max(1, int(math.sqrt(area / (a * b))))
            n = min(n, self.W // a, self.H // b)
            w, h = n * a, n * b
        self.sel = self.fit_rect(cx, cy, w, h)

    def handle_positions(self):
        x0, y0, x1, y1 = self.sel
        xm, ym = (x0 + x1) / 2, (y0 + y1) / 2
        hs = {"nw": (x0, y0), "ne": (x1, y0), "sw": (x0, y1), "se": (x1, y1)}
        if not self.ratio:
            hs.update({"n": (xm, y0), "s": (xm, y1), "w": (x0, ym), "e": (x1, ym)})
        return hs

    def hit_sel_handle(self, x, y):
        if not self.sel:
            return None
        for k, (hx, hy) in self.handle_positions().items():
            if abs(x - hx) <= self.HIT and abs(y - hy) <= self.HIT:
                return k
        return None

    def hit_curve_handle(self, x, y):
        if self.tool != "curve" or not self.sel:
            return None
        for i in range(len(self.shapes) - 1, -1, -1):
            sh = self.shapes[i]
            if sh["type"] != "curve":
                continue
            for key in ("m", "p0", "p2"):
                if dist(sh[key], (x, y)) <= self.HIT + 2:
                    return i, key
        return None

    def resize_sel(self, orig, h, x, y):
        x0, y0, x1, y1 = orig
        if len(h) == 2:
            ax = x1 if "w" in h else x0
            ay = y1 if "n" in h else y0
            return self.rect_from_anchor(ax, ay, x, y)
        x, y = self.clamp(x, y)
        if h == "n":
            y0 = y
        elif h == "s":
            y1 = y
        elif h == "w":
            x0 = x
        elif h == "e":
            x1 = x
        return self.norm(x0, y0, x1, y1)

    # ------------------------------------------------------------ мышь
    def on_down(self, e):
        self.canvas.focus_set()
        if self._popup is not None:
            self.close_popup()
            return
        x, y = e.x, e.y
        if self.live and self.collage:                   # режим коллажа: ручки рамки и рисование внутри неё
            h = self.hit_sel_handle(x, y) if self.sel else None
            if h:
                self.drag = {"kind": "resize", "h": h, "orig": self.sel}
                return
            if self.tool in (None, "select") or not self.sel or not self.in_sel(x, y):
                return
        if self.sel is None or (not self.in_sel(x, y) and not self.hit_sel_handle(x, y)
                                and not self.hit_curve_handle(x, y)):
            self.start_new_selection(x, y)
            return
        ch = self.hit_curve_handle(x, y)
        if ch:
            self.push_undo()
            self.drag = {"kind": "curvehandle", "i": ch[0], "key": ch[1], "moved": False}
            return
        h = self.hit_sel_handle(x, y)
        if h:
            self.drag = {"kind": "resize", "h": h, "orig": self.sel}
            return
        if self.tool == "select":
            self.drag = {"kind": "move", "sx": x, "sy": y, "orig": self.sel}
            return
        if self.tool is None:                            # инструмент не выбран — внутри рамки ничего не рисуется
            return
        p = self.clamp_to_sel(x, y)
        base = {"color": self.color, "w": self.width, "alpha": self.alpha}
        if self.tool == "pen":
            cur = {"type": "pen", "pts": [p], "round": self.round}
        elif self.tool == "curve":
            cur = {"type": "curve", "p0": p, "m": p, "p2": p}
        else:
            cur = {"type": self.tool, "p0": p, "p1": p, "round": self.round}
        cur.update(base)
        self.cur = cur
        self.drag = {"kind": "draw"}
        try:                                             # плюсик — только пока идёт рисование
            getattr(e, "widget", self.canvas).configure(cursor="crosshair")
        except tk.TclError:
            pass
        self._cursor = self._ccursor = "crosshair"
        if self.tool == "pen":
            self.draw_preview()

    def start_new_selection(self, x, y):
        x, y = self.clamp(x, y)
        if self.fixed:
            w, h = self.fixed_size()
            self.sel = self.fit_rect(x, y, w, h)
            self.drag = {"kind": "move", "sx": x, "sy": y, "orig": self.sel}
            self.update_view()
        else:
            self.drag = {"kind": "new", "ax": int(x), "ay": int(y)}

    def on_drag(self, e):
        d = self.drag
        if not d:
            return
        x, y = e.x, e.y
        shift = bool(e.state & 0x1)
        k = d["kind"]
        if k == "new":
            self.sel = self.rect_from_anchor(d["ax"], d["ay"], x, y)
            self.update_view()
        elif k == "move":
            x0, y0, x1, y1 = d["orig"]
            dx = max(-x0, min(self.W - x1, x - d["sx"]))
            dy = max(-y0, min(self.H - y1, y - d["sy"]))
            self.sel = (x0 + dx, y0 + dy, x1 + dx, y1 + dy)
            self.update_view()
        elif k == "resize":
            self.sel = self.resize_sel(d["orig"], d["h"], x, y)
            self.update_view()
        elif k == "curvehandle":
            self.shapes[d["i"]][d["key"]] = self.clamp_to_sel(x, y)
            d["moved"] = True
            self.redraw_shapes()
            self.redraw_ui()
        elif k == "draw":
            cur = self.cur
            p = self.clamp_to_sel(x, y)
            t = cur["type"]
            if t == "pen":
                last = cur["pts"][-1]
                if dist(last, p) >= 1.0:
                    cur["pts"].append(p)
                    cap = "round" if cur["round"] >= 50 else "projecting"
                    self.canvas.create_line(*last, *p, fill=cur["color"], width=cur["w"],
                                            capstyle=cap, tags="preview")
                return
            if t in ("line", "arrow", "curve") and shift:
                p = self.clamp_to_sel(*snap_angle(cur["p0"], p))
            if t in ("rect", "ellipse") and shift:
                dx, dy = p[0] - cur["p0"][0], p[1] - cur["p0"][1]
                s = max(abs(dx), abs(dy))
                p = self.clamp_to_sel(cur["p0"][0] + math.copysign(s, dx or 1),
                                      cur["p0"][1] + math.copysign(s, dy or 1))
            if t == "curve":
                cur["p2"] = p
                cur["m"] = ((cur["p0"][0] + p[0]) / 2, (cur["p0"][1] + p[1]) / 2)
            else:
                cur["p1"] = p
            self.draw_preview()

    def on_up(self, e):
        d, self.drag = self.drag, None
        if not d:
            return
        try:
            getattr(e, "widget", self.canvas).configure(cursor="arrow")
        except tk.TclError:
            pass
        self._cursor = self._ccursor = "arrow"
        k = d["kind"]
        if k == "new":
            if self.sel and (self.sel[2] - self.sel[0] < 3 or self.sel[3] - self.sel[1] < 3):
                self.sel = None
            self.update_view()
        elif k in ("move", "resize"):
            self.update_view()
        elif k == "curvehandle":
            if not d["moved"] and self.undo:
                self.undo.pop()
        elif k == "draw":
            cur, self.cur = self.cur, None
            self.canvas.delete("preview")
            t = cur["type"]
            ok = False
            if t == "pen":
                cur["rpts"] = pen_render_points(cur["pts"], cur["round"])
                ok = len(cur["rpts"]) >= 1
            elif t in ("line", "arrow"):
                ok = dist(cur["p0"], cur["p1"]) >= 2
            elif t in ("rect", "ellipse"):
                ok = abs(cur["p0"][0] - cur["p1"][0]) >= 2 and abs(cur["p0"][1] - cur["p1"][1]) >= 2
            elif t == "curve":
                ok = dist(cur["p0"], cur["p2"]) >= 3
            if ok:
                self.push_undo()
                self.shapes.append(cur)
            self.redraw_shapes()
            self.redraw_ui()

    def cursor_for(self, x, y):
        """Курсор мыши: обычная стрелка; плюсик — только когда можно рисовать (инструмент рисования внутри рамки)."""
        cur = "arrow"
        if self.sel:
            if self.hit_curve_handle(x, y):
                cur = "hand2"
            else:
                h = self.hit_sel_handle(x, y)
                if h:
                    cur = {"nw": "size_nw_se", "se": "size_nw_se", "ne": "size_ne_sw", "sw": "size_ne_sw",
                           "n": "size_ns", "s": "size_ns", "w": "size_we", "e": "size_we"}[h]
                elif self.in_sel(x, y):
                    cur = "fleur" if self.tool == "select" else "arrow"
        return cur

    def on_motion(self, e):
        if self.drag:
            return
        cur = self.cursor_for(e.x, e.y)
        if cur != self._cursor:
            try:
                self.canvas.configure(cursor=cur)
            except tk.TclError:
                self.canvas.configure(cursor="arrow")
            self._cursor = cur

    def on_catch_motion(self, e):
        if self.drag:
            return
        cur = self.cursor_for(e.x, e.y)
        if cur != getattr(self, "_ccursor", None):
            try:
                e.widget.configure(cursor=cur)
            except tk.TclError:
                pass
            self._ccursor = cur

    def on_wheel(self, e):
        self.set_width(self.width + (1 if e.delta > 0 else -1))

    # ------------------------------------------------------------ клавиатура
    @staticmethod
    def key_id(e):
        if IS_WIN:
            vk = e.keycode                # не зависит от раскладки (работает и на русской)
            if 65 <= vk <= 90:
                return chr(vk).lower()
            if 48 <= vk <= 57:
                return str(vk - 48)
            if 96 <= vk <= 105:
                return str(vk - 96)
            if vk == 219:
                return "["
            if vk == 221:
                return "]"
        ks = e.keysym
        if ks == "bracketleft":
            return "["
        if ks == "bracketright":
            return "]"
        return (e.char or ks).lower()

    def on_key(self, e):
        if isinstance(e.widget, tk.Entry):
            return
        ks = e.keysym
        if self.live and self.collage and (ks in ("Return", "KP_Enter") or
                          (e.state & 0x4 and self.key_id(e) not in ("z", "y"))):
            return
        if ks == "Escape" and self._popup is not None:
            self.close_popup()
            return
        if ks == "Escape":
            if self.drag:
                self.drag = None
                self.cur = None
                self.canvas.delete("preview")
                self.update_view()
            else:
                self.close()
            return
        if ks in ("Return", "KP_Enter"):
            self.do_copy()
            return
        k = self.key_id(e)
        if e.state & 0x4:   # Ctrl
            if k == "z":
                self.do_redo() if e.state & 0x1 else self.do_undo()
            elif k == "y":
                self.do_redo()
            elif k == "c":
                self.do_copy()
            elif k == "s":
                self.do_save_as() if e.state & 0x1 else self.do_save()
            elif k == "a":
                self.sel = (0, 0, self.W, self.H)
                if self.ratio:
                    self.apply_ratio_to_sel()
                self.update_view()
            return
        if ks in ("Left", "Right", "Up", "Down") and self.sel:
            step = 10 if e.state & 0x1 else 1
            dx = {"Left": -step, "Right": step}.get(ks, 0)
            dy = {"Up": -step, "Down": step}.get(ks, 0)
            x0, y0, x1, y1 = self.sel
            dx = max(-x0, min(self.W - x1, dx))
            dy = max(-y0, min(self.H - y1, dy))
            self.sel = (x0 + dx, y0 + dy, x1 + dx, y1 + dy)
            self.update_view()
            return
        if k in TOOL_KEYS:
            self.set_tool(TOOL_KEYS[k])
        elif k == "s":
            self.toggle_round()
        elif k.isdigit() and int(k) < len(RATIOS):
            self.set_ratio(RATIOS[int(k)])
        elif k == "[":
            self.set_width(self.width - 1)
        elif k == "]":
            self.set_width(self.width + 1)

    # ------------------------------------------------------------ отрисовка
    def draw_shape_tk(self, sh, tag):
        c = self.canvas
        col, w, t = sh["color"], sh["w"], sh["type"]
        if t == "pen":
            pts = dedupe(sh.get("rpts") or sh["pts"])
            if len(pts) == 1:
                x, y = pts[0]
                r = w / 2
                if sh["round"] >= 50:
                    c.create_oval(x - r, y - r, x + r, y + r, fill=col, outline="", tags=tag)
                else:
                    c.create_rectangle(x - r, y - r, x + r, y + r, fill=col, outline="", tags=tag)
                return
            cap, join = ("round", "round") if sh["round"] >= 50 else ("projecting", "bevel")
            c.create_line(*flat(pts), fill=col, width=w, capstyle=cap, joinstyle=join, tags=tag)
        elif t == "line":
            c.create_line(*sh["p0"], *sh["p1"], fill=col, width=w, capstyle="round", tags=tag)
        elif t == "curve":
            c.create_line(*flat(curve_points(sh["p0"], sh["m"], sh["p2"])), fill=col, width=w,
                          capstyle="round", joinstyle="round", tags=tag)
        elif t == "arrow":
            g = arrow_geom(sh["p0"], sh["p1"], w)
            if g:
                line, tri = g
                c.create_line(*flat(line), fill=col, width=w, capstyle="round", tags=tag)
                c.create_polygon(*flat(tri), fill=col, outline=col, tags=tag)
        elif t == "ellipse":
            pts = ellipse_points(*sh["p0"], *sh["p1"])
            if pts:
                c.create_line(*flat(pts), fill=col, width=w, joinstyle="round", capstyle="round", tags=tag)
        elif t == "rect":
            (x0, y0), (x1, y1) = sh["p0"], sh["p1"]
            rad = rect_rad(sh)
            if rad >= 0.5:
                c.create_line(*flat(rrect_points(x0, y0, x1, y1, rad)), fill=col, width=w,
                              joinstyle="round", capstyle="round", tags=tag)
            else:
                x0, x1 = sorted((x0, x1))
                y0, y1 = sorted((y0, y1))
                c.create_line(x0, y0, x1, y0, x1, y1, x0, y1, x0, y0, fill=col, width=w,
                              joinstyle="miter", capstyle="projecting", tags=tag)

    def draw_preview(self):
        self.canvas.delete("preview")
        if self.cur:
            self.draw_shape_tk(self.cur, "preview")
            if self.cur["type"] == "curve":
                self._curve_handles(self.cur, "preview")
        self.canvas.tag_raise("ui")

    def redraw_shapes(self):
        self.canvas.delete("shape")
        if any(int(sh.get("alpha", 100)) < 100 for sh in self.shapes):
            # есть полупрозрачные фигуры: показываем слой, нарисованный через PIL (Tk не умеет прозрачность)
            bb = [shape_bbox(sh) for sh in self.shapes]
            bx0 = max(0, int(min(b[0] for b in bb))); by0 = max(0, int(min(b[1] for b in bb)))
            bx1 = min(self.W, int(max(b[2] for b in bb)) + 1); by1 = min(self.H, int(max(b[3] for b in bb)) + 1)
            if bx1 > bx0 and by1 > by0:
                lay = shapes_layer(self.shapes, bx0, by0, bx1 - bx0, by1 - by0, 2)
                if self.live:
                    lay = live_layer(lay)
                self._shape_photo = ImageTk.PhotoImage(lay, master=self.win)
                self.canvas.create_image(bx0, by0, anchor="nw", image=self._shape_photo, tags="shape")
        else:
            for sh in self.shapes:
                self.draw_shape_tk(sh, "shape")
        self._order()

    def _order(self):
        c = self.canvas
        c.tag_raise("bright", "bg")
        c.tag_raise("shape")
        c.tag_raise("preview")
        c.tag_raise("ui")
        self.place_collage_panel()
        if self.live:
            self._update_dim()
            self._sync_catch()

    def update_bright(self):
        c = self.canvas
        if self.live or not self.sel or self.sel[2] - self.sel[0] < 1 or self.sel[3] - self.sel[1] < 1:
            c.itemconfigure(self.bright_id, state="hidden")
            return
        x0, y0, x1, y1 = [int(v) for v in self.sel]
        self.tk_bright = ImageTk.PhotoImage(self.img.crop((x0, y0, x1, y1)), master=self.win)
        c.itemconfigure(self.bright_id, image=self.tk_bright, state="normal")
        c.coords(self.bright_id, x0, y0)

    def update_view(self):
        self.update_bright()
        self.redraw_ui()

    def toggle_cursor(self):
        if self.cursor is None:
            return
        self.show_cursor = not self.show_cursor
        self.img = self.img_cur if self.show_cursor else self.img_clean
        self.tk_dim = ImageTk.PhotoImage(ImageEnhance.Brightness(self.img).enhance(self.dim_factor()), master=self.win)
        self.canvas.itemconfigure(self.bg_id, image=self.tk_dim)
        self.refresh_toolbar()
        self.update_view()
        self.canvas.focus_set()

    def _label(self, x, y, text, anchor):
        c = self.canvas
        tid = c.create_text(x, y, text=text, anchor=anchor, fill="#ffffff",
                            font=("Segoe UI", 10, "bold"), tags="ui")
        bb = c.bbox(tid)
        r = c.create_rectangle(bb[0] - 5, bb[1] - 2, bb[2] + 5, bb[3] + 2, fill="#111111",
                               outline="", tags="ui")
        c.tag_lower(r, tid)

    def _curve_handles(self, sh, tag):
        c = self.canvas
        c.create_line(*sh["p0"], *sh["m"], *sh["p2"], fill="#ffffff", dash=(3, 3), width=1, tags=tag)
        for key in ("p0", "p2"):
            x, y = sh[key]
            c.create_oval(x - 5, y - 5, x + 5, y + 5, fill="#ffffff", outline="#000000", tags=tag)
        x, y = sh["m"]
        c.create_oval(x - 7, y - 7, x + 7, y + 7, fill=ACC, outline="#ffffff", width=2, tags=tag)

    def redraw_ui(self):
        c = self.canvas
        c.delete("ui")
        if not self.sel:
            c.itemconfigure(self.tb_id, state="hidden")
            self._order()
            return
        x0, y0, x1, y1 = self.sel
        c.create_rectangle(x0, y0, x1, y1, outline="#ffffff", width=1, tags="ui")           # светлая подложка
        c.create_rectangle(x0, y0, x1, y1, outline=BG, dash=(4, 3), width=1, tags="ui")     # тёмно-синий пунктир
        info = f"{x1 - x0} × {y1 - y0}"
        if self.ratio:
            info += f"   {self.ratio_label}"
        if y0 > 26:
            self._label(x0 + 5, y0 - 5, info, "sw")
        else:
            self._label(x0 + 8, y0 + 8, info, "nw")
        for hx, hy in self.handle_positions().values():
            q = self.HS
            c.create_oval(hx - q, hy - q, hx + q, hy + q, fill=BG, outline="#ffffff", width=1, tags="ui")
        if self.tool == "curve":
            for sh in self.shapes:
                if sh["type"] == "curve":
                    self._curve_handles(sh, "ui")

        busy = self.drag and self.drag["kind"] in ("new", "move", "resize")
        if busy:
            c.itemconfigure(self.tb_id, state="hidden")
        else:
            self.tb.update_idletasks()
            tw, th = self.tb.winfo_reqwidth(), self.tb.winfo_reqheight()
            gap = 8
            tx = max(0, min(self.W - tw, x1 - tw))
            if y1 + gap + th <= self.H:
                ty = y1 + gap
            elif y0 - gap - th >= 0:
                ty = y0 - gap - th
            else:
                ty = max(0, y1 - th - gap)
            c.coords(self.tb_id, tx, ty)
            c.itemconfigure(self.tb_id, state="normal")
        self._order()

    # ------------------------------------------------------------ результат
    def cursor_now(self):
        """Курсор для снимка «живого» режима: значок — как был при нажатии PrtScn (у приложения под окном),
        положение — где мышь стоит сейчас."""
        c0 = self.cursor
        if c0 is not None and len(c0) >= 6 and IS_WIN:
            try:
                from ctypes import wintypes as wt
                pt = wt.POINT()
                ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
                return (c0[0], c0[1], pt.x - c0[4] - self.vx, pt.y - c0[5] - self.vy)
            except Exception:
                pass
        return grab_cursor(self.vx, self.vy)

    def result(self):
        if self.live and not self.collage:                 # «снимок при сохранении»: экран снимается сейчас
            try:
                img, vx, vy, primary = grab_screen()
                if img.size == (self.W, self.H):
                    if self.show_cursor:
                        cur = self.cursor_now()
                        if cur is not None:
                            img = paste_cursor(img, cur)
                    return render_image(img, self.sel, self.shapes)
            except Exception:
                pass
        return render_image(self.img, self.sel, self.shapes)

    def do_copy(self):
        if not self.sel:
            return
        try:
            hwnd = None
            if IS_WIN:
                hwnd = ctypes.windll.user32.GetAncestor(self.win.winfo_id(), 2)
            copy_image_to_clipboard(self.result(), hwnd)
        except Exception as ex:
            with self.topmost_off():
                messagebox.showerror(APP_NAME, f"Не удалось скопировать: {ex}", parent=self.win)
            return

    def do_save(self):
        """Сохранить сразу, без вопросов, и закрыть."""
        if not self.sel:
            return
        st = self.app.settings
        folder = st.get("last_dir") or default_save_dir()
        try:
            os.makedirs(folder, exist_ok=True)
            path = unique_path(folder, "Скриншот_" + datetime.now().strftime("%Y-%m-%d_%H-%M-%S"),
                               FMT_EXT.get(st.get("fmt"), ".png"))
            save_image(self.result(), path)
        except Exception as ex:
            with self.topmost_off():
                messagebox.showerror(APP_NAME, f"Не удалось сохранить: {ex}", parent=self.win)
            return
        self.close()

    def do_save_as(self):
        """Выбрать папку и имя, сохранить и закрыть."""
        if not self.sel:
            return
        img = self.result()
        st = self.app.settings
        d = st.get("last_dir") or default_save_dir()
        ext = FMT_EXT.get(st.get("fmt"), ".png")
        name = "Скриншот_" + datetime.now().strftime("%Y-%m-%d_%H-%M-%S") + ext
        with self.topmost_off():
            path = filedialog.asksaveasfilename(parent=self.win, title="Сохранить скриншот как",
                                                initialdir=d, initialfile=name, defaultextension=ext,
                                                filetypes=file_types(st.get("fmt", "PNG")))
        if not path:
            return
        try:
            save_image(img, path)
        except Exception as ex:
            with self.topmost_off():
                messagebox.showerror(APP_NAME, f"Не удалось сохранить: {ex}", parent=self.win)
            return
        st["last_dir"] = os.path.dirname(path)
        self.close()

    def collage_click(self):
        """Включить/выключить режим коллажа. Включение кадр не снимает; выход удаляет все кадры коллажа."""
        if self.collage:
            self.app.frames.clear()
            self.refresh_collage_panel()
            if self.late_origin:               # экран и так живой — остаёмся в нём, только без коллажа
                self.collage = False
                self.refresh_toolbar()
                self._sync_catch()
            else:
                self.recapture()               # назад к замороженному снимку и рисованию
        elif self.sel:
            self.collage = True
            if self.live:
                self.refresh_toolbar()
                self._sync_catch()
            else:
                self._set_live(True)

    # ------------------------------------------------------------ режим коллажа: экран не замораживается
    LIVE_KEY = "#010203"

    def _exclude_from_capture(self):
        """Окно редактора не попадает в снимки экрана — можно снимать, не пряча интерфейс."""
        if not IS_WIN:
            return False
        try:
            u = ctypes.windll.user32
            hwnd = u.GetAncestor(self.win.winfo_id(), 2)
            return bool(u.SetWindowDisplayAffinity(hwnd, 0x11))     # WDA_EXCLUDEFROMCAPTURE
        except Exception:
            return False

    def start_late(self):
        """Режим «снимок при сохранении»: экран не замораживается, снимок делается в момент сохранения."""
        if not IS_WIN:
            return False
        self.win.update_idletasks()
        self.affinity = self._exclude_from_capture()
        if not self.affinity:
            return False
        c = self.canvas
        self.live = True
        self.collage = False
        for t in ("bg", "bright"):
            c.itemconfigure(t, state="hidden")
        self.win.configure(bg=self.LIVE_KEY)
        c.configure(bg=self.LIVE_KEY, cursor="arrow")
        self._make_dim()
        self.win.deiconify()
        self.win.attributes("-topmost", True)
        self.refresh_toolbar()
        self.redraw_ui()
        self.place_collage_panel()
        self._sync_catch()
        self._focus()
        return True

    def _make_dim(self):
        """Затемнение вне рамки в режиме коллажа: отдельное полупрозрачное окно с «дыркой» под рамкой.
        Клики проходят сквозь него, на снимки оно не попадает."""
        if not IS_WIN or not self.affinity or self.dim is not None:
            return
        try:
            d = self.dim = tk.Toplevel(self.app.root)
            d.withdraw()
            d.overrideredirect(True)
            d.configure(bg="black")
            d.geometry(f"{self.W}x{self.H}+{self.vx}+{self.vy}")
            d.attributes("-alpha", self.app.settings.get("dim", 50) / 100.0)
            d.attributes("-topmost", True)
            d.update_idletasks()
            u = ctypes.windll.user32
            hwnd = self.dim_hwnd = u.GetAncestor(d.winfo_id(), 2)
            # WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
            u.SetWindowLongW(hwnd, -20, u.GetWindowLongW(hwnd, -20) | 0x80000 | 0x20 | 0x80 | 0x08000000)
            u.SetWindowDisplayAffinity(hwnd, 0x11)
            self._update_dim()                              # «дырка» под рамкой — до показа окна
            if self.win.winfo_viewable():
                # показываем затемнение сразу под окном редактора: пока там замороженный снимок, его не видно
                main = u.GetAncestor(self.win.winfo_id(), 2)
                u.SetWindowPos(hwnd, main, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010 | 0x0040)
            else:
                final = self.app.settings.get("dim", 50) / 100.0
                d.attributes("-alpha", 0.0)
                d.deiconify()
                d.update()                                   # прорисовано чёрным, пока невидимо
                d.attributes("-alpha", final)
        except Exception:
            self._kill_dim()

    def _update_dim(self):
        if self.dim is None or not self.sel:
            return
        try:
            g = ctypes.windll.gdi32
            x0, y0, x1, y1 = [int(v) for v in self.sel]
            a = g.CreateRectRgn(0, 0, self.W, self.H)
            b = g.CreateRectRgn(x0, y0, x1, y1)
            r = g.CreateRectRgn(0, 0, 0, 0)
            g.CombineRgn(r, a, b, 4)                       # RGN_DIFF: весь экран без рамки
            g.DeleteObject(a)
            g.DeleteObject(b)
            ctypes.windll.user32.SetWindowRgn(self.dim_hwnd, r, True)
        except Exception:
            pass

    def _make_catch(self):
        """Почти невидимое окно над рамкой ловит мышь, пока выбран инструмент рисования
        (сам режим коллажа пропускает клики сквозь рамку)."""
        d = self.catch = tk.Toplevel(self.app.root)
        d.withdraw()
        d.overrideredirect(True)
        d.configure(bg="black")
        d.geometry(f"{self.W}x{self.H}+{self.vx}+{self.vy}")
        d.attributes("-alpha", 0.01)
        d.attributes("-topmost", True)
        cv = tk.Canvas(d, bg="black", highlightthickness=0, bd=0, cursor="arrow")
        cv.pack(fill="both", expand=True)
        cv.bind("<ButtonPress-1>", self.on_down)
        cv.bind("<B1-Motion>", self.on_drag)
        cv.bind("<ButtonRelease-1>", self.on_up)
        cv.bind("<Motion>", self.on_catch_motion)
        d.update_idletasks()
        u = ctypes.windll.user32
        hwnd = self.catch_hwnd = u.GetAncestor(d.winfo_id(), 2)
        u.SetWindowLongW(hwnd, -20, u.GetWindowLongW(hwnd, -20) | 0x80000 | 0x80 | 0x08000000)
        u.SetWindowDisplayAffinity(hwnd, 0x11)
        self._catch_key = None

    def _sync_catch(self):
        if not IS_WIN or not self.affinity:
            return
        if not self.live:
            want, region = False, None
        elif self.collage:
            want = self.sel is not None and self.tool not in (None, "select")
            region = tuple(int(v) for v in self.sel) if self.sel else None
        else:                                                # «снимок при сохранении»: можно выделять и двигать рамку
            want = True
            region = None if (self.sel is None or self.tool in (None, "select")) else tuple(int(v) for v in self.sel)
        key = (region, want)
        if getattr(self, "catch", None) is not None and key == self._catch_key:
            return
        try:
            u = ctypes.windll.user32
            if not want:
                if getattr(self, "catch", None) is not None:
                    u.SetWindowPos(self.catch_hwnd, 0, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0080)
                    self._catch_key = key
                return
            if getattr(self, "catch", None) is None:
                self._make_catch()
            g = ctypes.windll.gdi32
            if region is None:
                u.SetWindowRgn(self.catch_hwnd, 0, True)
            else:
                x0, y0, x1, y1 = region
                u.SetWindowRgn(self.catch_hwnd, g.CreateRectRgn(x0, y0, x1, y1), True)
            main = u.GetAncestor(self.win.winfo_id(), 2)
            u.SetWindowPos(self.catch_hwnd, main, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010 | 0x0040)
            if self.dim_hwnd:
                u.SetWindowPos(self.dim_hwnd, self.catch_hwnd, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
            self._catch_key = key
        except Exception:
            pass

    def _kill_catch(self):
        d = getattr(self, "catch", None)
        if d is not None:
            try:
                d.destroy()
            except tk.TclError:
                pass
        self.catch = None
        self.catch_hwnd = None

    def _kill_dim(self):
        if self.dim is not None:
            try:
                self.dim.destroy()
            except tk.TclError:
                pass
        self.dim = None
        self.dim_hwnd = None

    def _set_live(self, on):
        c = self.canvas
        self.close_popup()
        if on:
            self.live = True
            self.affinity = self._exclude_from_capture()
            self._make_dim()                                # сперва затемнение, потом оживает экран
            for t in ("bg", "bright"):
                c.itemconfigure(t, state="hidden")
            self.win.configure(bg=self.LIVE_KEY)
            c.configure(bg=self.LIVE_KEY, cursor="arrow")
            self.refresh_toolbar()
            self.redraw_ui()
            self.place_collage_panel()
            self._sync_catch()
        else:
            self.live = False
            self.collage = False
            self._kill_catch()
            if IS_WIN:
                try:
                    u = ctypes.windll.user32
                    u.SetWindowDisplayAffinity(u.GetAncestor(self.win.winfo_id(), 2), 0)
                except Exception:
                    pass
            self.win.configure(bg="black")
            c.configure(bg="black", cursor="arrow")
            c.itemconfigure("bg", state="normal")
            c.itemconfigure("shape", state="normal")
            self.win.update_idletasks()
            self._kill_dim()                                # затемнение убираем, когда снимок уже на месте

    def live_snap(self):
        """Снять кадр в рамке и добавить в коллаж. Без заморозки: интерфейс остаётся на месте."""
        if not self.collage or not self.sel:
            return
        self.close_popup()
        if self.affinity:
            self._live_snap_do(hide=False)
        else:                                   # старая Windows: окно придётся спрятать на мгновение
            self.win.withdraw()
            self.app.root.update()
            self.win.after(220, lambda: self._live_snap_do(hide=True))

    def _live_snap_do(self, hide=False):
        try:
            img, vx, vy, primary = grab_screen()
            if img.size == (self.W, self.H):
                cur = self.cursor_now()
                if self.show_cursor and cur is not None:
                    img = paste_cursor(img, cur)
                self.app.add_frame(render_image(img, self.sel, self.shapes))
        except Exception:
            pass
        try:
            if hide:
                self.win.deiconify()
                self.win.attributes("-topmost", True)
            self.refresh_collage_panel()
            self.refresh_toolbar()
        except tk.TclError:
            pass

    # ------------------------------------------------------------ коллаж: миниатюра в углу
    CP_W, CP_H = 220, 150

    def _menu_btn(self, parent, icon, tip):
        mb = RoundMenuBtn(parent, icon)
        self.tips.append(ToolTip(mb, tip))
        return mb

    def _build_collage_panel(self):
        c = self.canvas
        fr = self.cp = tk.Frame(c, bg=BG, padx=6, pady=6, highlightthickness=1, highlightbackground=EDGE)
        self.cp_canvas = tk.Canvas(fr, width=self.CP_W, height=self.CP_H, bg=BG, highlightthickness=0)
        self.cp_canvas.pack()
        bar = tk.Frame(fr, bg=BG)
        bar.pack(fill="x", pady=(5, 0))
        rb = self._menu_btn(bar, "ratio:custom", "Пропорции ячеек коллажа")
        rb.pack(side="left", padx=1)
        m = tk.Menu(rb, tearoff=0, font=("Segoe UI", 10))
        for lab in COLLAGE_RATIOS:
            m.add_command(label=("Авто (как первый кадр)" if lab == "Авто" else lab),
                          command=lambda l=lab: self.cp_set_ratio(l))
        m.add_separator()
        m.add_command(label="Своё…", command=self.cp_custom_ratio)
        rb.configure(menu=m)
        cb = self._menu_btn(bar, "grid", "Число колонок")
        cb.pack(side="left", padx=1)
        m2 = tk.Menu(cb, tearoff=0, font=("Segoe UI", 10))
        for n in COLLAGE_COLS:
            m2.add_command(label=("Колонок: авто" if n == 0 else f"Колонок: {n}"),
                           command=lambda v=n: self.cp_set_cols(v))
        cb.configure(menu=m2)
        self._btn(bar, "undo", self.cp_remove_last, "Убрать последний кадр").pack(side="left", padx=1)
        self._btn(bar, "trash", self.cp_clear, "Очистить коллаж").pack(side="left", padx=1)
        self._btn(bar, "save", lambda: self.cp_save(False),
                  "Сохранить коллаж и закрыть").pack(side="left", padx=1)
        self._btn(bar, "saveas", lambda: self.cp_save(True),
                  "Сохранить коллаж как… и закрыть").pack(side="left", padx=1)
        self.cp_id = c.create_window(0, 0, anchor="nw", window=fr, state="hidden")
        self.cp_photo = None

    def cp_ratio(self):
        return collage_ratio_tuple(self.app.settings.get("collage_ratio", "Авто"))

    def cp_cols(self):
        return int(self.app.settings.get("collage_cols", 0) or 0)

    def refresh_collage_panel(self):
        frames = self.app.frames
        if not frames:
            self.canvas.itemconfigure(self.cp_id, state="hidden")
            return
        im = collage_thumb(frames, self.cp_ratio(), self.cp_cols(), self.CP_W, self.CP_H)
        self.cp_photo = ImageTk.PhotoImage(im, master=self.win)
        self.cp_canvas.delete("all")
        self.cp_canvas.create_image(self.CP_W // 2, self.CP_H // 2, image=self.cp_photo)
        self.place_collage_panel()

    def place_collage_panel(self):
        """Миниатюра — в верхнем углу за пределами выделения (справа, если мешает — слева)."""
        if not hasattr(self, "cp_id") or not self.app.frames:
            return
        self.cp.update_idletasks()
        pw, ph = self.cp.winfo_reqwidth(), self.cp.winfo_reqheight()
        px0, py0, px1, py1 = self.primary
        x, y = px1 - pw - 14, py0 + 14
        if self.sel:
            sx0, sy0, sx1, sy1 = self.sel
            if not (sx1 < x or sx0 > x + pw or sy1 < y or sy0 > y + ph):
                x = px0 + 14
        self.canvas.coords(self.cp_id, x, y)
        self.canvas.itemconfigure(self.cp_id, state="normal")

    def cp_set_ratio(self, label):
        self.app.settings["collage_ratio"] = label
        self.refresh_collage_panel()
        self.canvas.focus_set()

    def cp_custom_ratio(self):
        with self.topmost_off():
            v = simpledialog.askstring(APP_NAME, "Пропорции ячейки (например 21:9 или 1.91:1):", parent=self.win)
        if v:
            if collage_ratio_tuple(v.strip()) is None:
                with self.topmost_off():
                    messagebox.showwarning(APP_NAME, "Не понял формат. Примеры: 21:9, 1.91:1", parent=self.win)
                return
            self.cp_set_ratio(v.strip())

    def cp_set_cols(self, n):
        self.app.settings["collage_cols"] = n
        self.refresh_collage_panel()
        self.canvas.focus_set()

    def cp_remove_last(self):
        if self.app.frames:
            self.app.frames.pop()
        self._after_cp_change()

    def cp_clear(self):
        self.app.frames.clear()
        self._after_cp_change()

    def _after_cp_change(self):
        self.refresh_collage_panel()
        self.refresh_toolbar()
        self.canvas.focus_set()

    def cp_sheet(self):
        return build_collage(self.app.frames, self.cp_ratio(), self.cp_cols())

    def cp_copy(self):
        if not self.app.frames:
            return
        try:
            copy_image_to_clipboard(self.cp_sheet())
        except Exception as ex:
            with self.topmost_off():
                messagebox.showerror(APP_NAME, f"Не удалось скопировать: {ex}", parent=self.win)

    def cp_save(self, ask):
        if not self.app.frames:
            return
        img = self.cp_sheet()
        try:
            with self.topmost_off():
                path = save_collage_image(img, self.app.settings, ask, self.win)
        except Exception as ex:
            with self.topmost_off():
                messagebox.showerror(APP_NAME, f"Не удалось сохранить: {ex}", parent=self.win)
            return
        if not path:
            return
        self.app.frames.clear()          # коллаж готов: закрываем окно выделения
        self.close()

    # ------------------------------------------------------------ новый снимок поверх открытого редактора
    def recapture(self):
        """Следующий кадр коллажа: PrtScn при открытом редакторе делает свежий снимок экрана,
        миниатюра и кадры коллажа остаются."""
        self.close_popup()
        if self.live and self.affinity:
            self._recapture_do(hide=False)          # окно и затемнение не попадают в снимок: ничего не прячем
            return
        self.win.withdraw()
        self.app.root.update()
        self.win.after(220, self._recapture_do)

    def _recapture_do(self, hide=True):
        try:
            img, vx, vy, primary = grab_screen()
            cur = grab_cursor(vx, vy)
            if img.size == (self.W, self.H):
                self.img_clean = img
                self.cursor = cur
                self.img_cur = paste_cursor(img, cur)
                self.show_cursor = self.show_cursor and cur is not None
                self.img = self.img_cur if self.show_cursor else self.img_clean
                self.tk_dim = ImageTk.PhotoImage(ImageEnhance.Brightness(self.img).enhance(self.dim_factor()), master=self.win)
                self.canvas.itemconfigure(self.bg_id, image=self.tk_dim)
                self.shapes, self.undo, self.redo = [], [], []     # рамка выделения остаётся прежней
                self.canvas.delete("shape")
                self.canvas.delete("preview")
        except Exception:
            pass
        try:
            if hide:
                self.win.deiconify()
                self.win.attributes("-topmost", True)
            self._set_live(False)
            self.update_bright()
            self.refresh_toolbar()
            self.redraw_ui()
            self._focus()
        except tk.TclError:
            pass


# ================================================================ приложение
def make_tray_icon():
    S = 64
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([2, 2, S - 3, S - 3], radius=14, fill=(47, 125, 246, 255))
    w, L, m = 6, 18, 14
    for (x, y, dx, dy) in [(m, m, 1, 1), (S - m, m, -1, 1), (m, S - m, 1, -1), (S - m, S - m, -1, -1)]:
        d.line([(x, y), (x + dx * L, y)], fill="white", width=w)
        d.line([(x, y), (x, y + dy * L)], fill="white", width=w)
    return im


class App:
    def __init__(self, single_shot=False):
        self.single = single_shot
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title(APP_NAME)
        self.settings = load_settings()
        try:
            QUALITY[0] = int(self.settings.get("quality", 90))
        except (TypeError, ValueError):
            pass
        self.q = queue.Queue()
        self.overlay = None
        self.icon = None
        self.listener = None
        self.frames = []                 # кадры коллажа
        self.collage = None
        self._collage_back = False       # вернуть окно коллажа после снимка
        self.root.after(100, self._poll)

    def request_shot(self, delay=0):
        self.q.put(("shot", delay))

    def _poll(self):
        try:
            while True:
                cmd, arg = self.q.get_nowait()
                if cmd == "shot":
                    if arg:
                        self.root.after(arg, self.take_shot)
                    else:
                        self.take_shot()
                elif cmd == "autostart":
                    try:
                        set_autostart(not autostart_enabled())
                    except Exception as ex:
                        messagebox.showerror(APP_NAME, f"Не удалось изменить автозапуск: {ex}")
                    if self.icon:
                        self.icon.update_menu()
                elif cmd == "esc":
                    if self.overlay is not None:
                        self.overlay.close()
                elif cmd == "collage":
                    self.open_collage()
                elif cmd == "collage_clear":
                    self.frames.clear()
                    if self.collage:
                        self.collage.hide()
                        self.collage.refresh()
                elif cmd == "quit":
                    self.quit()
                    return
        except queue.Empty:
            pass
        self.root.after(60, self._poll)

    def take_shot(self):
        if self.overlay is not None:
            try:
                if self.overlay.collage:                   # режим коллажа: PrtScn = снять и добавить кадр
                    self.overlay.live_snap()
                elif self.overlay.sel:                     # второе нажатие PrtScn: сохранить и закрыть
                    self.overlay.do_save()
                else:
                    self.overlay._focus()
            except Exception:
                self.overlay = None
            return
        if self.collage is not None and self.collage.visible():
            self._collage_back = True          # окно коллажа не должно попасть в кадр
            self.collage.hide()
            self.root.update()
            self.root.after(200, self._grab)
            return
        self._grab()

    def _grab(self):
        if self.overlay is not None:
            return
        try:
            img, vx, vy, primary = grab_screen()
            cur = grab_cursor(vx, vy)
        except Exception as ex:
            messagebox.showerror(APP_NAME, f"Не удалось снять экран: {ex}")
            if self._collage_back and self.collage:
                self.collage.show()
            self._collage_back = False
            if self.single:
                self.quit()
            return
        late = bool(self.settings.get("late_capture")) and IS_WIN
        self.overlay = Overlay(self, img, vx, vy, primary, cur, late=late)
        if late and not self.overlay.start_late():       # не вышло — обычный замороженный снимок
            self.overlay.win.deiconify()
            self.overlay._focus()

    def add_frame(self, img):
        self.frames.append(img)

    def open_collage(self):
        if not self.frames:
            return
        if self.collage is None:
            self.collage = CollageWindow(self)
        self.collage.show()

    def on_overlay_closed(self):
        self.overlay = None
        save_settings(self.settings)
        if self._collage_back and self.frames:
            self.open_collage()
        self._collage_back = False
        if self.single:
            self.root.after(10, self.quit)

    def start_tray(self):
        try:
            import pystray
        except ImportError:
            return False
        menu = pystray.Menu(
            pystray.MenuItem(f"{APP_NAME} v{VERSION}", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Сделать скриншот (PrtScn)", lambda i, it: self.request_shot(300), default=True),
            pystray.MenuItem(lambda it: f"Коллаж: кадров {len(self.frames)}…",
                             lambda i, it: self.q.put(("collage", 0)),
                             enabled=lambda it: len(self.frames) > 0),
            pystray.MenuItem("Очистить коллаж", lambda i, it: self.q.put(("collage_clear", 0)),
                             enabled=lambda it: len(self.frames) > 0),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Запускать вместе с Windows", lambda i, it: self.q.put(("autostart", 0)),
                             checked=lambda it: autostart_enabled()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Выход", lambda i, it: self.q.put(("quit", 0))),
        )
        self.icon = pystray.Icon(APP_ID, make_tray_icon(), f"{APP_NAME} v{VERSION} — PrtScn", menu)
        self.icon.run_detached()
        return True

    def start_hotkey(self):
        if not IS_WIN:
            return False
        try:
            from pynput import keyboard
        except ImportError:
            return False
        VK_SNAPSHOT = 0x2C
        last = [0.0]

        def flt(msg, data):
            if data.vkCode == 0x1B:                    # Esc выходит из режима коллажа, даже если окно не в фокусе
                ov = self.overlay
                if ov is not None and getattr(ov, "live", False):
                    if msg in (0x0100, 0x0104):
                        self.q.put(("esc", 0))
                    self.listener.suppress_event()
                return True
            if data.vkCode == VK_SNAPSHOT:
                if msg in (0x0101, 0x0105):          # отпускание клавиши
                    now = time.time()
                    if now - last[0] > 0.3:
                        last[0] = now
                        self.request_shot(0)
                self.listener.suppress_event()       # не отдаём PrtScn системе
            return True

        self.listener = keyboard.Listener(win32_event_filter=flt)
        self.listener.daemon = True
        self.listener.start()
        return True

    def quit(self):
        save_settings(self.settings)
        try:
            if self.icon:
                self.icon.stop()
        except Exception:
            pass
        try:
            if self.listener:
                self.listener.stop()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

    def run(self):
        if self.single:
            self.root.after(50, self.take_shot)
        else:
            tray = self.start_tray()
            hot = self.start_hotkey()
            if not tray and not hot:
                self.root.after(50, self.quit)   # без трея и горячих клавиш в фоне работать нечем
        self.root.mainloop()


def setup_logging():
    """В exe без консоли sys.stderr = None — любая печать ошибки роняет программу.
    Перенаправляем вывод в файл журнала."""
    try:
        path = os.path.join(os.path.dirname(settings_path()), "error.log")
        if sys.stderr is None or sys.stdout is None or getattr(sys, "frozen", False):
            f = open(path, "a", encoding="utf-8", buffering=1)
            sys.stderr = f
            sys.stdout = f
    except Exception:
        class _Null:
            def write(self, *_a): pass
            def flush(self): pass
        sys.stderr = sys.stdout = _Null()

    def tk_error(self, exc, val, tb):
        import traceback
        try:
            sys.stderr.write(datetime.now().strftime("%Y-%m-%d %H:%M:%S ") +
                             "".join(traceback.format_exception(exc, val, tb)) + "\n")
            sys.stderr.flush()
        except Exception:
            pass
    tk.Tk.report_callback_exception = tk_error


def main():
    setup_logging()
    set_dpi_awareness()
    single = "--shot" in sys.argv
    if not single and already_running():
        return   # уже запущен — молча выходим
    App(single).run()


if __name__ == "__main__":
    main()

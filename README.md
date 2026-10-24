# Screenshoter

**English** · [Русский](README.ru.md) · [Español](README.es.md) · [Deutsch](README.de.md) · [Français](README.fr.md) · [Português](README.pt.md) · [中文](README.zh.md) · [日本語](README.ja.md) · [हिन्दी](README.hi.md) · [العربية](README.ar.md)

**Screenshoter** is a fast Windows screenshot tool in the spirit of Lightshot: press **PrtScn**, select an area, draw arrows, frames and notes on top, then save or copy. Modern dark interface, lives in the system tray. The interface is available in 10 languages.

![Screenshot editor](images/en/editor.png)

## Features

- **Area selection** with handles on edges and corners; the frame remembers its position.
- **Aspect ratios**: free, 1:1, 4:5, 3:4, 4:3, 16:9, 9:16, 3:2, 2:3, a custom ratio or an exact size in pixels.
- **Drawing**: pencil, line, 3-point curve, arrow, rectangle, circle and ellipse; color, transparency, thickness, rounding; undo and redo.
- The **mouse cursor** can be included in the screenshot.
- **Saving** as PNG, JPG, WEBP or BMP with quality control; copy to the clipboard.
- **Capture on save**: the screen is not frozen, video keeps playing, the picture is taken when you save.
- **Collage**: live mode, frames captioned “Screenshot N”, black borders, dimming outside the frame.
- Multiple monitors and Windows scaling of 125–200 %.
- **10 interface languages**: Русский, English, Español, Deutsch, Français, Português, 中文, 日本語, हिन्दी, العربية (choose in Settings).

## Collage mode

Collect several pieces of the screen on **one sheet** — for a tutorial, a bug report, a before/after comparison or a social-media post. The screen stays alive while you work, so you can scroll a page or play a video and keep taking frames.

![Collage window](images/en/collage_overview.png)

![Collage window](images/en/collage.png)

1. Open a screenshot (**PrtScn**), select an area and press the **collage** button on the toolbar. The screen is not frozen — it goes live.
2. Move or resize the frame, then press the **frame** button (or **PrtScn** / **Enter**) to capture the area. Repeat as many times as you need.
3. The collage window collects your frames in a grid, each captioned “Screenshot N”, separated by black borders. Choose the **cell ratio** (auto, 1:1, 16:9, custom…) and the **number of columns**.
4. **Save** the sheet (PNG, JPG, WEBP, BMP), **Save as…**, **copy** it to the clipboard, undo the last frame, clear all, or minimize the window and keep capturing.
5. Leaving collage mode with the collage button discards the captured frames.

## Interface

Top row: proportions, move the frame, undo and redo, cursor in the screenshot, collage, collage frame, save, save as, settings, close. Bottom row (toggled by the arrow button): pencil, line, curve, arrow, shapes, color, thickness, rounding. No tool turns on by itself; pressing an active tool again turns it off. The crosshair appears only while you draw.

### Color and transparency

![Color and transparency](images/en/colors.png)

Quick colors, a palette and a line-transparency slider.

### Shapes

![Shapes](images/en/shapes.png)

### Settings

![Settings](images/en/settings.png)

Format and quality, dimming outside the frame, “Capture on save” (clock), hotkeys (keyboard) and language selection.

### Hotkeys and about

![Hotkeys and about](images/en/hotkeys.png)

## Installation

1. Install Python 3.9 or newer from python.org (tick “Add Python to PATH”).
2. Run `install.bat` — it installs Pillow, pystray and pynput.
3. Run `run.bat`. A tray icon appears; take a screenshot with **PrtScn**.
4. For a single `.exe` that needs no Python, run `build_exe.bat`; the result is in `dist`.

> Autostart: right-click the tray icon → “Start with Windows”. On Windows 11 PrtScn opens the Snipping Tool by default: turn that off in Settings → Accessibility → Keyboard.

## Development

Python 3, Tkinter, Pillow, pystray, pynput. Source: `screenshoter.py` and `i18n_data.py` (translations).

Year created: 2026. Developer: Free software · [GitHub](https://github.com/Al235r/Screenshoter).

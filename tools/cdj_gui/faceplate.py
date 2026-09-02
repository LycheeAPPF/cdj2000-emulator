"""Draw the CDJ-2000 front panel, and put the LCD in the middle of it.

This is presentation only.  Every input still comes from
`tools.cdj_main.panel_control` and every click still goes out through
`view_ui.Control.lines`, so the measured half of the project -- which bit, which
name, which run -- is untouched.  What changes is that the window stops looking
like a debugger and starts looking like the deck in
`cdj2000-interface-real-unit.jpg`.

The approach is borrowed from `nsaintot/cdj3k-emu`, which is a CDJ-3000 (RK3399
+ Linux, aarch64) and shares no architecture with this project at all -- but its
UI crate answers a question we had not: it draws the chassis **procedurally**
into a reference coordinate system and scales that to the window
(`crates/cdj3k-emu-ui/src/app/ui/layout.rs`), rather than skinning a photo.  So
there is no artwork to keep in step with the code, and the window resizes
without resampling anything.  Its `bloom.rs` does the backlight as a blurred
copy of the lit shapes; `draw_cache.rs`/`frame_cache.rs` keep the static chrome
out of the per-frame path.  All three ideas are used below.

**Where a control sits here is not evidence of anything.**  That rule has cost
this project weeks twice -- the four SOURCE keys were labelled from where they
sit on the front panel and were reversed for as long as the project existed.  So
`PLACEMENTS` only positions an input when MAIN's own SERVICE MODE name table
names it (`panel_control.FIRMWARE_KEY_NAMES`), and the arrangement is drawn from
that name, not from a guess about the photo.  Inputs the firmware does not name
-- 15.6, 15.7 and seven of the eight analogue fields -- are **not** given a
plausible-looking spot on the deck.  They go in the rack along the bottom,
captioned as what they are.  A window that invents a TEMPO fader out of an
unattributed analogue field would be making exactly the claim this project
refuses to make.

The lit state is likewise honest: a key lights when *you* press it and fades,
because that is what the window knows.  It is not reading the panel's LED
lines -- nothing has ever measured those -- so it does not pretend to.
"""
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 LycheeAPPF

from __future__ import annotations

import math
import tkinter as tk
from typing import Callable, NamedTuple

from PIL import Image, ImageDraw, ImageFilter, ImageTk

# ------------------------------------------------------------------ palette --
#
# Taken from cdj3k-emu's `ui.rs` where it fits (that deck is the same industrial
# design language), and from the reference photo where it does not: the
# CDJ-2000's SOURCE column is blue for LINK and amber for the rest, and the
# browse row is amber on near-black.
CHASSIS = (26, 26, 30)
CHASSIS_EDGE = (12, 12, 15)
PLATE = (36, 36, 42)
BTN = (48, 48, 55)
BTN_EDGE = (86, 86, 96)
BTN_TEXT = (196, 196, 206)
DIM_TEXT = (120, 120, 132)
AMBER = (220, 200, 0)
AMBER_LIT = (255, 240, 44)
BLUE = (0, 102, 255)
BLUE_LIT = (120, 180, 255)
WHITE = (222, 222, 222)
CUE_ORANGE = (255, 150, 0)
PLAY_GREEN = (0, 200, 100)
RED = (238, 69, 85)
LCD_SURROUND = (8, 8, 10)

# ----------------------------------------------------------------- geometry --
#
# One panel unit is one LCD pixel, so the picture is never resampled: at
# `--scale 2` the 480x234 frame is 960x468 and every chrome coordinate below is
# simply doubled.  cdj3k-emu keeps a 4080-unit reference canvas for the same
# reason -- one coordinate system, scaled once at the edge.
PANEL_W = 672
PANEL_H = 486
RACK_H = 26                     # caption strip; the rack itself is Tk widgets

LCD_X, LCD_Y = 86, 56
LCD_W, LCD_H = 480, 234


class Place(NamedTuple):
    """Where one input is drawn, and how it looks when it is lit."""
    x: int
    y: int
    w: int
    h: int
    label: str
    accent: tuple[int, int, int] = AMBER
    shape: str = "rect"         # rect | round | knob | wheel
    font: int = 9


# The deck, in panel units.  Only inputs MAIN's name table names appear here.
#
# The top zone reproduces the reference photo one-for-one, because that photo is
# the acceptance target (memory cdj-reference-photo-target): BROWSE / TAG LIST /
# INFO / MENU across the top, LINK / USB / SD / DISC down the left, the panel
# between them.  The lower zone is the rest of the deck in its real relative
# arrangement but compressed -- the LCD is the subject here, so it keeps its
# native pixels and the transport gives up room rather than the other way round.
PLACEMENTS: dict[str, Place] = {
    # ---- above the panel ----
    "20.0": Place(86, 18, 114, 30, "BROWSE", AMBER, font=11),
    "20.1": Place(208, 18, 114, 30, "TAG LIST", AMBER, font=11),
    "20.2": Place(330, 18, 114, 30, "INFO", AMBER, font=11),
    "20.3": Place(452, 18, 114, 30, "MENU", AMBER, font=11),
    # Not a key of its own on the player: UTILITY is MENU held down, and on
    # this link "held down" is a press spanning two of MAIN's 3 s status
    # records (view_ui.WINDOW_LONG_HOLD_MS).  The suffix keeps it out of the
    # bit count (coverage strips it, as it does field6-touch) and lets
    # view_ui resolve it to the long-press control.
    "20.3-hold": Place(574, 18, 100, 30, "UTILITY", AMBER, font=11),
    # ---- the SOURCE column, left of the panel ----
    "19.0": Place(8, 62, 70, 26, "LINK", BLUE, font=10),
    "19.1": Place(8, 96, 70, 26, "USB", AMBER, font=10),
    "19.2": Place(8, 130, 70, 26, "SD", AMBER, font=10),
    "19.3": Place(8, 164, 70, 26, "DISC", AMBER, font=10),
    "17.2": Place(8, 206, 70, 22, "SD OPEN", DIM_TEXT, font=8),
    "20.5": Place(8, 234, 70, 22, "TAG TRACK", AMBER, font=8),
    # ---- the selector, right of the panel ----
    "17.0": Place(586, 62, 76, 76, "PUSH", WHITE, shape="knob"),
    "20.4": Place(586, 148, 76, 24, "RETURN", AMBER, font=9),
    "21.3": Place(586, 178, 76, 22, "MEMORY", AMBER, font=8),
    "21.2": Place(586, 204, 76, 22, "DELETE", RED, font=8),
    "21.0": Place(586, 230, 36, 22, "< CALL", AMBER, font=7),
    "21.1": Place(626, 230, 36, 22, "CALL >", AMBER, font=7),
    "19.4": Place(586, 256, 76, 22, "TIME/A.CUE", DIM_TEXT, font=7),
    # ---- the jog and its ring, lower left ----
    "15.5": Place(30, 306, 172, 172, "JOG", WHITE, shape="wheel"),
    "18.6": Place(214, 306, 58, 24, "JOG MODE", DIM_TEXT, font=7),
    "15.1": Place(214, 334, 58, 24, "REV", DIM_TEXT, font=8),
    "15.0": Place(214, 362, 58, 24, "LOCK", DIM_TEXT, font=8),
    # ---- transport ----
    "18.1": Place(214, 396, 58, 24, "|<< PREV", DIM_TEXT, font=7),
    "18.2": Place(214, 424, 58, 24, "NEXT >>|", DIM_TEXT, font=7),
    "18.3": Place(214, 452, 28, 24, "<<", DIM_TEXT, font=8),
    "18.4": Place(244, 452, 28, 24, ">>", DIM_TEXT, font=8),
    "16.1": Place(30, 486 - 0, 0, 0, "", CUE_ORANGE),   # replaced below
    # ---- hot cue / loop / rec, the row above the tempo side ----
    "16.5": Place(288, 306, 56, 34, "HOT CUE A", AMBER, font=7),
    "16.6": Place(350, 306, 56, 34, "HOT CUE B", AMBER, font=7),
    "16.7": Place(412, 306, 56, 34, "HOT CUE C", AMBER, font=7),
    "18.0": Place(474, 306, 56, 34, "REC MODE", RED, font=7),
    "16.4": Place(288, 348, 56, 28, "LOOP IN", AMBER, font=7),
    "16.3": Place(350, 348, 56, 28, "LOOP OUT", AMBER, font=7),
    "16.2": Place(412, 348, 56, 28, "RELOOP", AMBER, font=7),
    "17.1": Place(474, 348, 56, 28, "4-BEAT", AMBER, font=7),
    # ---- tempo, right of the deck ----
    "18.7": Place(586, 306, 76, 24, "TEMPO RANGE", DIM_TEXT, font=7),
    "19.6": Place(586, 334, 76, 24, "MASTER TEMPO", AMBER, font=7),
    "19.7": Place(586, 362, 76, 24, "TEMPO RESET", DIM_TEXT, font=7),
}

# CUE and PLAY are the two big ones and get written out rather than squeezed
# into the table above, because their size is the point: on the deck they are
# the only controls you find without looking.
PLACEMENTS["16.1"] = Place(288, 396, 108, 52, "CUE", CUE_ORANGE, font=13)
PLACEMENTS["16.0"] = Place(404, 396, 108, 52, "PLAY/PAUSE", PLAY_GREEN, font=11)

# The SELECT encoder is the same physical control as 17.0 -- the knob turns and
# pushes -- so it is drawn once and carries both.
ENCODER_FIELD_PLACE = "17.0"


def lerp(a: tuple[int, int, int], b: tuple[int, int, int],
         t: float) -> tuple[int, int, int]:
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def _font(size: int):
    """A bitmap font at `size`, or PIL's default.

    Deliberately forgiving: a missing DejaVu on someone else's machine must
    give a plainer window, not a traceback in a launcher that has already
    started two emulators.
    """
    from PIL import ImageFont
    for name in ("DejaVuSans-Bold.ttf", "arialbd.ttf", "seguisb.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


class Renderer:
    """Turns `Place`s into images: the chassis once, each key in two states."""

    def __init__(self, scale: int) -> None:
        self.scale = scale
        self.fonts: dict[int, object] = {}

    def font(self, size: int):
        key = max(7, int(size * self.scale * 0.92))
        if key not in self.fonts:
            self.fonts[key] = _font(key)
        return self.fonts[key]

    # -- the static plate -------------------------------------------------
    def chassis(self) -> Image.Image:
        """Everything that never changes: plate, LCD surround, engraving.

        Drawn once at startup.  cdj3k-emu keeps the same split (`draw_cache`
        holds the static shapes; only the LCD texture and the pressed keys are
        rebuilt per frame), and it is the difference between a window that
        repaints 480x234 pixels and one that repaints the whole deck.
        """
        s = self.scale
        size = (PANEL_W * s, (PANEL_H + RACK_H) * s)
        image = Image.new("RGB", size, CHASSIS)
        draw = ImageDraw.Draw(image)

        # A shallow vertical gradient so the plate reads as metal rather than
        # as a flat fill.  Cheap: one line per row of the deck.
        for y in range(PANEL_H * s):
            t = y / max(1, PANEL_H * s - 1)
            draw.line([(0, y), (size[0], y)],
                      fill=lerp(PLATE, CHASSIS, t * 0.85))
        draw.rectangle([0, PANEL_H * s, size[0], size[1]], fill=CHASSIS_EDGE)

        # The LCD's bezel: the panel is inset in the real deck, so a dark
        # surround with a light top edge.
        bezel = [(LCD_X - 6) * s, (LCD_Y - 6) * s,
                 (LCD_X + LCD_W + 6) * s, (LCD_Y + LCD_H + 6) * s]
        draw.rounded_rectangle(bezel, radius=3 * s, fill=LCD_SURROUND,
                               outline=(60, 60, 68), width=max(1, s // 2))

        # The jog well, so the wheel does not float on the plate.
        jog = PLACEMENTS["15.5"]
        cx, cy = (jog.x + jog.w / 2) * s, (jog.y + jog.h / 2) * s
        r = jog.w / 2 * s
        draw.ellipse([cx - r - 6 * s, cy - r - 6 * s,
                      cx + r + 6 * s, cy + r + 6 * s],
                     fill=(18, 18, 22), outline=(58, 58, 66),
                     width=max(1, s // 2))

        draw.text((10 * s, (PANEL_H + 7) * s),
                  "below: the inputs MAIN's name table does not name. They are "
                  "placed nowhere on the deck — where a control sits is not "
                  "evidence of what it is.",
                  font=self.font(7), fill=(112, 112, 124))
        return image

    # -- one key, unlit and lit -------------------------------------------
    def key(self, place: Place, lit: bool) -> Image.Image:
        """A single key with its own glow, on transparent background.

        Returned oversized by `pad` on each side so the bloom has somewhere to
        go; the caller places it centred on the key's rectangle.
        """
        s = self.scale
        pad = 10 * s
        w, h = place.w * s, place.h * s
        image = Image.new("RGBA", (w + 2 * pad, h + 2 * pad), (0, 0, 0, 0))

        if place.shape == "knob":
            self._knob(image, place, lit, pad)
        elif place.shape == "wheel":
            self._wheel(image, place, lit, pad)
        else:
            self._rect(image, place, lit, pad)
        return image

    def _bloom(self, image: Image.Image, shape: Image.Image,
               accent: tuple[int, int, int], strength: float) -> None:
        """cdj3k-emu's `bloom.rs`, in two lines of Pillow.

        The lit shape is blurred and screened back over the key, which is what
        makes a backlit plastic button read as *lit* rather than as a brighter
        colour.
        """
        glow = shape.filter(ImageFilter.GaussianBlur(radius=4 * self.scale))
        glow = Image.blend(Image.new("RGBA", image.size, (0, 0, 0, 0)),
                           glow, strength)
        image.alpha_composite(glow)

    def _rect(self, image: Image.Image, place: Place, lit: bool,
              pad: int) -> None:
        s = self.scale
        box = [pad, pad, pad + place.w * s, pad + place.h * s]
        radius = max(2, int(place.h * s * 0.15))
        accent = place.accent

        if lit:
            fill = lerp(accent, (255, 255, 255), 0.15)
            edge = (255, 255, 255)
            text = (20, 20, 20) if sum(accent) > 320 else (255, 255, 255)
            glow_layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
            ImageDraw.Draw(glow_layer).rounded_rectangle(
                box, radius=radius, fill=(*accent, 210))
            self._bloom(image, glow_layer, accent, 0.85)
        else:
            fill = BTN
            edge = lerp(accent, BTN_EDGE, 0.55)
            text = lerp(accent, BTN_TEXT, 0.45)

        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle(box, radius=radius, fill=fill)
        # The double border cdj3k-emu draws on every key: a light outer and a
        # dark inner line is what gives a flat fill an edge.
        draw.rounded_rectangle(box, radius=radius, outline=edge,
                               width=max(1, s // 2))
        draw.rounded_rectangle([box[0] + s, box[1] + s, box[2] - s, box[3] - s],
                               radius=max(1, radius - s), outline=(18, 18, 22),
                               width=max(1, s // 2))
        font = self.font(place.font)
        draw.text(((box[0] + box[2]) / 2, (box[1] + box[3]) / 2), place.label,
                  font=font, fill=text, anchor="mm")

    def _knob(self, image: Image.Image, place: Place, lit: bool,
              pad: int) -> None:
        s = self.scale
        r = place.w * s / 2
        cx = cy = pad + r
        draw = ImageDraw.Draw(image)
        if lit:
            glow_layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
            ImageDraw.Draw(glow_layer).ellipse(
                [cx - r, cy - r, cx + r, cy + r], fill=(200, 200, 220, 190))
            self._bloom(image, glow_layer, WHITE, 0.8)
        for step in range(int(r), 0, -1):
            t = 1 - step / r
            draw.ellipse([cx - step, cy - step, cx + step, cy + step],
                         fill=lerp((70, 70, 80), (34, 34, 40), t))
        draw.ellipse([cx - r, cy - r, cx + r, cy + r],
                     outline=(120, 120, 132), width=max(1, s // 2))
        # The detent ticks, so a turn is visible rather than inferred.
        for index in range(24):
            angle = index * math.pi / 12
            inner, outer = r * 0.78, r * 0.94
            draw.line([cx + inner * math.cos(angle), cy + inner * math.sin(angle),
                       cx + outer * math.cos(angle), cy + outer * math.sin(angle)],
                      fill=(96, 96, 108), width=max(1, s // 2))
        draw.ellipse([cx - r * 0.55, cy - r * 0.55, cx + r * 0.55, cy + r * 0.55],
                     fill=(44, 44, 52), outline=(130, 130, 145),
                     width=max(1, s // 2))
        draw.text((cx, cy), "PUSH", font=self.font(7),
                  fill=(210, 210, 225) if lit else (150, 150, 165), anchor="mm")

    def _wheel(self, image: Image.Image, place: Place, lit: bool,
               pad: int) -> None:
        s = self.scale
        r = place.w * s / 2
        cx = cy = pad + r
        draw = ImageDraw.Draw(image)
        for step in range(int(r), 0, -1):
            t = 1 - step / r
            draw.ellipse([cx - step, cy - step, cx + step, cy + step],
                         fill=lerp((58, 58, 66), (30, 30, 36), t))
        draw.ellipse([cx - r, cy - r, cx + r, cy + r],
                     outline=(110, 110, 124) if lit else (74, 74, 84),
                     width=max(1, s))
        # The jog LCD in the middle is a real, separate display on this deck
        # (memory cdj-jog-display-keep-separate); it is drawn as a recess and
        # left empty rather than filled with invented content.
        inner = r * 0.44
        draw.ellipse([cx - inner, cy - inner, cx + inner, cy + inner],
                     fill=(14, 14, 18), outline=(70, 70, 80),
                     width=max(1, s // 2))
        draw.text((cx, cy + inner + 9 * s), "JOG TOUCH", font=self.font(7),
                  fill=(150, 150, 165), anchor="mm")


def placed_ids() -> list[str]:
    """The inputs this deck has a position for."""
    return [name for name, place in PLACEMENTS.items() if place.w]


def unplaced(board_ids: list[str]) -> list[str]:
    """The inputs that must appear in the rack instead.

    Kept as a function rather than a second table so the two can never drift:
    the rack is defined as *everything the deck does not draw*, so an input
    added to `panel_control` shows up somewhere in the window without anyone
    having to remember to add it.  That is the failure this project has had
    twice -- 46 inputs and 38 controls, silently.
    """
    placed = set(placed_ids())
    # The SELECT encoder shares the knob with the push it is built into.
    placed.add("field%d" % ENCODER_FIELD)
    return [name for name in board_ids if name not in placed]


ENCODER_FIELD = 7               # panel_control.ANALOG_CONTROLS[7], measured


class Faceplate(tk.Canvas):
    """The deck as a Tk canvas: chassis underneath, keys and the LCD on top.

    Everything static is one image, built once.  Each key is a small image of
    its own so that lighting it is a swap of that key rather than a repaint of
    the deck, and the LCD is a single image item updated in place -- which is
    the whole reason the picture can keep up now (see `set_frame`).
    """

    def __init__(self, parent: tk.Misc, scale: int,
                 resolve: Callable[[str], object | None],
                 click: Callable[[object], None],
                 rotate: Callable[[int, int], None]) -> None:
        self.scale = scale
        self.renderer = Renderer(scale)
        self.resolve = resolve
        self.on_click = click
        self.on_rotate = rotate
        super().__init__(parent, width=PANEL_W * scale,
                         height=(PANEL_H + RACK_H) * scale,
                         highlightthickness=0, borderwidth=0,
                         background="#%02x%02x%02x" % CHASSIS)

        self.chassis_photo = ImageTk.PhotoImage(self.renderer.chassis())
        self.create_image(0, 0, image=self.chassis_photo, anchor="nw")

        self.key_photo: dict[str, tuple[ImageTk.PhotoImage,
                                        ImageTk.PhotoImage]] = {}
        self.key_item: dict[str, int] = {}
        self.lit_until: dict[str, str] = {}
        for name in placed_ids():
            self._build_key(name)

        # The LCD.  One Tk image for the life of the window; `set_frame` pastes
        # into it.  Building a fresh PhotoImage per frame costs 8.0 ms against
        # 5.2 ms for a paste, and -- worse -- churns a 1.8 MB Tk image 30 times
        # a second, which is what the old window did.
        blank = Image.new("RGB", (LCD_W * scale, LCD_H * scale), (0, 0, 0))
        self.lcd_photo = ImageTk.PhotoImage(blank)
        self.create_image(LCD_X * scale, LCD_Y * scale, image=self.lcd_photo,
                          anchor="nw")

        self._drag_angle: float | None = None

    # ------------------------------------------------------------- keys --
    def _build_key(self, name: str) -> None:
        place = PLACEMENTS[name]
        scale, pad = self.scale, 10 * self.scale
        unlit = ImageTk.PhotoImage(self.renderer.key(place, lit=False))
        lit = ImageTk.PhotoImage(self.renderer.key(place, lit=True))
        self.key_photo[name] = (unlit, lit)
        item = self.create_image(place.x * scale - pad, place.y * scale - pad,
                                 image=unlit, anchor="nw")
        self.key_item[name] = item
        self.tag_bind(item, "<Button-1>",
                      lambda _event, key=name: self._pressed(key))
        if place.shape == "knob":
            self.tag_bind(item, "<B1-Motion>",
                          lambda event, key=name: self._knob_drag(event, key))
            self.tag_bind(item, "<ButtonRelease-1>",
                          lambda _event: setattr(self, "_drag_angle", None))
            # Tk refuses <MouseWheel> on a canvas *item* -- only key, button,
            # motion, enter/leave and virtual events are legal there -- so the
            # wheel is taken on the widget and filtered by position below.
            self.bind("<MouseWheel>", self._knob_wheel)

    def _pressed(self, name: str) -> None:
        control = self.resolve(name)
        if control is not None:
            self.on_click(control)
        self.flash(name)

    def flash(self, name: str, milliseconds: int = 190) -> None:
        """Light a key because *this window* sent it, and let it fade.

        Not because the deck lit it: nothing here has ever measured the panel's
        LED lines, so the window shows what it did, not what the firmware
        thinks.  Conflating the two would make the picture a claim about the
        hardware.
        """
        item = self.key_item.get(name)
        if item is None:
            return
        self.itemconfigure(item, image=self.key_photo[name][1])
        pending = self.lit_until.get(name)
        if pending:
            self.after_cancel(pending)
        self.lit_until[name] = self.after(
            milliseconds,
            lambda: self.itemconfigure(item, image=self.key_photo[name][0]))

    # ------------------------------------------------------------ knob --
    def _knob_angle(self, event: tk.Event) -> float:
        place = PLACEMENTS[ENCODER_FIELD_PLACE]
        cx = (place.x + place.w / 2) * self.scale
        cy = (place.y + place.h / 2) * self.scale
        return math.atan2(self.canvasy(event.y) - cy, self.canvasx(event.x) - cx)

    def _knob_drag(self, event: tk.Event, name: str) -> None:
        angle = self._knob_angle(event)
        if self._drag_angle is None:
            self._drag_angle = angle
            return
        delta = angle - self._drag_angle
        while delta > math.pi:
            delta -= 2 * math.pi
        while delta < -math.pi:
            delta += 2 * math.pi
        detents = int(delta / (math.pi / 12))       # 24 detents per turn
        if detents:
            self._drag_angle = angle
            self.on_rotate(ENCODER_FIELD, detents)
            self.flash(name, 90)

    def _knob_wheel(self, event: tk.Event) -> None:
        """Wheel over the knob turns it; wheel anywhere else is not for us.

        The filter matters: the wheel reaches the whole canvas, and a scroll
        aimed at the window would otherwise walk the browse list.
        """
        place = PLACEMENTS[ENCODER_FIELD_PLACE]
        x, y = self.canvasx(event.x), self.canvasy(event.y)
        cx = (place.x + place.w / 2) * self.scale
        cy = (place.y + place.h / 2) * self.scale
        if math.hypot(x - cx, y - cy) > place.w / 2 * self.scale:
            return
        self.on_rotate(ENCODER_FIELD, 1 if event.delta > 0 else -1)
        self.flash(ENCODER_FIELD_PLACE, 90)

    # ------------------------------------------------------------- LCD --
    def set_frame(self, frame: Image.Image) -> None:
        """Put a 480x234 panel frame on the deck, scaled by whole pixels."""
        if self.scale != 1:
            frame = frame.resize((frame.width * self.scale,
                                  frame.height * self.scale),
                                 Image.Resampling.NEAREST)
        self.lcd_photo.paste(frame)

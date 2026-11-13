"""Turns the calculator's raw keypad matrix into mouse and keyboard actions.

Edit NORMAL_LAYER / ALPHA_LAYER below to change what the keys do.

Action strings:
  "a", "7", "("        type that character
  "key:enter"          press a named key (pynput Key name: enter, backspace,
                       esc, tab, space, delete, up, ctrl, alt, cmd, f5, ...)
  "mouse:left"         mouse button (left / right / middle), held while the key is held
  "scroll:up"          scroll wheel (up / down), repeats while held
  "shift"              Shift: held while the key is held
  "alpha"              toggle the letter layer
  "arrows"             toggle the arrow keys between mouse movement and arrow keys
  "zoom"               zoom in one level ([2nd] + key zooms out)
"""

import threading
import time

# Key name -> (matrix group index 0..6, bit). Matches keypadc's kb_Data[1..7].
KEY_BITS = {
    "graph": (0, 0), "trace": (0, 1), "zoom": (0, 2), "window": (0, 3),
    "y=": (0, 4), "2nd": (0, 5), "mode": (0, 6), "del": (0, 7),
    "sto": (1, 1), "ln": (1, 2), "log": (1, 3), "x^2": (1, 4),
    "x^-1": (1, 5), "math": (1, 6), "alpha": (1, 7),
    "0": (2, 0), "1": (2, 1), "4": (2, 2), "7": (2, 3),
    ",": (2, 4), "sin": (2, 5), "apps": (2, 6), "xttn": (2, 7),
    ".": (3, 0), "2": (3, 1), "5": (3, 2), "8": (3, 3),
    "(": (3, 4), "cos": (3, 5), "prgm": (3, 6), "stat": (3, 7),
    "(-)": (4, 0), "3": (4, 1), "6": (4, 2), "9": (4, 3),
    ")": (4, 4), "tan": (4, 5), "vars": (4, 6),
    "enter": (5, 0), "+": (5, 1), "-": (5, 2), "*": (5, 3),
    "/": (5, 4), "^": (5, 5), "clear": (5, 6),
    "down": (6, 0), "left": (6, 1), "right": (6, 2), "up": (6, 3),
}

NORMAL_LAYER = {
    # Top row: mouse
    "y=": "mouse:left", "window": "mouse:right", "zoom": "zoom",
    "trace": "scroll:up", "graph": "scroll:down",
    # Modifiers and mode switches. These keys have no green letter, so they
    # work the same in both layers (hold [vars], tap a letter = Ctrl+letter).
    "2nd": "shift", "mode": "arrows", "alpha": "alpha",
    "vars": "key:ctrl", "stat": "key:alt",
    # Editing
    "del": "key:backspace", "clear": "key:esc",
    "enter": "key:enter", "xttn": "key:tab",
    "math": "key:cmd", "apps": "key:space", "prgm": "key:delete",
    "sto": "mouse:middle",
    # Digits and punctuation
    "0": "0", "1": "1", "2": "2", "3": "3", "4": "4",
    "5": "5", "6": "6", "7": "7", "8": "8", "9": "9",
    ".": ".", ",": ",", "(": "(", ")": ")", "(-)": "_",
    "+": "+", "-": "-", "*": "*", "/": "/", "^": "^",
    "x^-1": "'", "sin": "[", "cos": "]", "tan": "=",
    "x^2": ";", "log": "\\", "ln": "`",
}

# The green letters printed above the keys. Keys not listed fall through to
# NORMAL_LAYER, so modifiers, Enter, Del, etc. keep working while typing.
ALPHA_LAYER = {
    "math": "a", "apps": "b", "prgm": "c",
    "x^-1": "d", "sin": "e", "cos": "f", "tan": "g", "^": "h",
    "x^2": "i", ",": "j", "(": "k", ")": "l", "/": "m",
    "log": "n", "7": "o", "8": "p", "9": "q", "*": "r",
    "ln": "s", "4": "t", "5": "u", "6": "v", "-": "w",
    "sto": "x", "1": "y", "2": "z", "3": "@", "+": '"',
    "0": " ", ".": ":", "(-)": "?",
}

# What Shift does to a typed character (US layout).
SHIFTED = {
    "1": "!", "2": "@", "3": "#", "4": "$", "5": "%", "6": "^", "7": "&",
    "8": "*", "9": "(", "0": ")", "-": "_", "=": "+", "[": "{", "]": "}",
    "\\": "|", ";": ":", "'": '"', ",": "<", ".": ">", "/": "?", "`": "~",
}

ARROWS = ("up", "down", "left", "right")

# Mouse feel, in calculator pixels (multiplied by the current zoom scale).
MOVE_DELAY = 0.18  # seconds an arrow is held before the pointer starts gliding
MOVE_MIN_SPEED = 30.0  # px/s when the glide starts
MOVE_MAX_SPEED = 260.0  # px/s at full speed
MOVE_RAMP = 0.9  # seconds to reach full speed
SCROLL_INTERVAL = 0.08  # seconds between wheel clicks while held


class KeyMapper:
    """Stateful keypad -> input translator. Thread-safe.

    `backend` needs: key(name_or_char, down), button(name, down),
    scroll(dy), move(dx, dy). `on_zoom(step)` is called with +1 / -1.
    """

    def __init__(self, backend, on_zoom=None, normal=None, alpha=None):
        self.backend = backend
        self.on_zoom = on_zoom or (lambda step: None)
        self.normal = dict(NORMAL_LAYER if normal is None else normal)
        self.alpha_layer = dict(ALPHA_LAYER if alpha is None else alpha)
        self.alpha = False
        self.arrows_are_keys = False
        self.scale = 1.0  # screen pixels per calculator pixel
        self._lock = threading.Lock()
        self._matrix = bytes(7)
        self._held = {}  # key name -> ("key"|"button"|"scroll"|"none", value)
        self._shift = False
        self._dirs = set()
        self._move_start = 0.0
        self._last_tick = None
        self._frac = [0.0, 0.0]
        self._scroll_at = 0.0

    # -- keypad state -----------------------------------------------------

    def update(self, matrix, now=None):
        """Feed a new 7-byte keypad matrix."""
        now = time.monotonic() if now is None else now
        matrix = bytes(matrix)
        with self._lock:
            old, self._matrix = self._matrix, matrix
            for name, (group, bit) in KEY_BITS.items():
                was = old[group] >> bit & 1
                is_down = matrix[group] >> bit & 1
                if is_down and not was:
                    self._press(name, now)
                elif was and not is_down:
                    self._release(name)

    def release_all(self):
        """Let go of everything (calculator quit or unplugged)."""
        with self._lock:
            for name in list(self._held):
                self._release(name)
            self._matrix = bytes(7)
            self._dirs.clear()

    def _action(self, name):
        if self.alpha and name in self.alpha_layer:
            return self.alpha_layer[name]
        return self.normal.get(name)

    def _press(self, name, now):
        if name in ARROWS and not self.arrows_are_keys:
            if not self._dirs:
                self._move_start = now
                self._frac = [0.0, 0.0]
            self._dirs.add(name)
            self._held[name] = ("dir", name)
            self._step(name, max(1, round(self.scale)))  # one calculator pixel per tap
            return

        action = "key:" + name if name in ARROWS else self._action(name)
        held = ("none", None)
        if action is None:
            pass
        elif action == "shift":
            self._shift = True
            self.backend.key("shift", True)
            held = ("shift", None)
        elif action == "alpha":
            self.alpha = not self.alpha
        elif action == "arrows":
            if not self._shift:  # [2nd] + [mode] quits the calculator program
                self.arrows_are_keys = not self.arrows_are_keys
        elif action == "zoom":
            self.on_zoom(-1 if self._shift else 1)
        elif action.startswith("key:"):
            held = ("key", action[4:])
            self.backend.key(held[1], True)
        elif action.startswith("mouse:"):
            held = ("button", action[6:])
            self.backend.button(held[1], True)
        elif action.startswith("scroll:"):
            held = ("scroll", 1 if action[7:] == "up" else -1)
            self.backend.scroll(held[1])
            self._scroll_at = now + 0.3
        else:
            char = action
            if self._shift:
                char = SHIFTED.get(char, char.upper())
            held = ("key", char)
            self.backend.key(char, True)
        self._held[name] = held

    def _release(self, name):
        kind, value = self._held.pop(name, ("none", None))
        if kind == "dir":
            self._dirs.discard(value)
        elif kind == "shift":
            self._shift = False
            self.backend.key("shift", False)
        elif kind == "key":
            self.backend.key(value, False)
        elif kind == "button":
            self.backend.button(value, False)

    # -- time-based behaviour ---------------------------------------------

    def _step(self, direction, amount):
        dx = {"left": -amount, "right": amount}.get(direction, 0)
        dy = {"up": -amount, "down": amount}.get(direction, 0)
        self.backend.move(dx, dy)

    def tick(self, now=None):
        """Call ~60 times a second: glides the pointer and repeats the scroll wheel."""
        now = time.monotonic() if now is None else now
        with self._lock:
            last, self._last_tick = self._last_tick, now
            dt = 0.0 if last is None else min(now - last, 0.1)

            for kind, value in self._held.values():
                if kind == "scroll" and now >= self._scroll_at:
                    self.backend.scroll(value)
                    self._scroll_at = now + SCROLL_INTERVAL

            held_for = now - self._move_start - MOVE_DELAY
            if not self._dirs or held_for < 0:
                return
            ramp = min(1.0, held_for / MOVE_RAMP)
            speed = (MOVE_MIN_SPEED + (MOVE_MAX_SPEED - MOVE_MIN_SPEED) * ramp) * self.scale
            dx = ("right" in self._dirs) - ("left" in self._dirs)
            dy = ("down" in self._dirs) - ("up" in self._dirs)
            self._frac[0] += dx * speed * dt
            self._frac[1] += dy * speed * dt
            move_x, move_y = int(self._frac[0]), int(self._frac[1])
            if move_x or move_y:
                self._frac[0] -= move_x
                self._frac[1] -= move_y
                self.backend.move(move_x, move_y)

    def status(self):
        """Short text for the on-screen status line."""
        return "%s  arrows:%s" % ("abc" if self.alpha else "123",
                                  "keys" if self.arrows_are_keys else "mouse")

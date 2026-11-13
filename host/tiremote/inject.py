"""Input backends: where mouse and keyboard actions actually go."""

import functools
import sys
import threading


def _locked(method):
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapper


class NullInjector:
    """View-only: ignores all input. Tracks a virtual pointer so zoom still pans."""

    def __init__(self, start=(0, 0)):
        self._pos = list(start)

    def key(self, name, down):
        pass

    def button(self, name, down):
        pass

    def scroll(self, dy):
        pass

    def move(self, dx, dy):
        self._pos[0] += dx
        self._pos[1] += dy

    def position(self):
        return tuple(self._pos)


class PynputInjector:
    """Drives the real mouse and keyboard through pynput.

    Calls arrive from several threads (key reader, pointer ticker, frame
    loop). They are serialised here: on macOS the system bindings pynput
    uses load their functions on first use, and two threads doing that at
    once fails with a KeyError.
    """

    _MODIFIERS = ("ctrl", "alt", "cmd")

    def __init__(self):
        from pynput import keyboard, mouse  # imported late: needs a display

        self._keyboard_mod = keyboard
        self._mouse_mod = mouse
        self._keyboard = keyboard.Controller()
        self._mouse = mouse.Controller()
        self._mods = set()
        self._down = {}  # name -> the pynput key object that was pressed
        self._lock = threading.Lock()
        self.position()  # load the pointer bindings now, on one thread

    def _resolve(self, name):
        keyboard = self._keyboard_mod
        if len(name) > 1:
            return getattr(keyboard.Key, name)
        # On Windows, characters are sent as Unicode text, which shortcuts
        # such as Ctrl+C ignore. Send a real virtual-key code instead.
        if sys.platform == "win32" and self._mods and name.isascii() and name.isalnum():
            return keyboard.KeyCode.from_vk(ord(name.upper()))
        return name

    @_locked
    def key(self, name, down):
        if down:
            key = self._resolve(name)
            self._down[name] = key
            self._keyboard.press(key)
            if name in self._MODIFIERS:
                self._mods.add(name)
        else:
            key = self._down.pop(name, None)
            if key is not None:
                self._keyboard.release(key)
            self._mods.discard(name)

    @_locked
    def button(self, name, down):
        button = getattr(self._mouse_mod.Button, name)
        if down:
            self._mouse.press(button)
        else:
            self._mouse.release(button)

    @_locked
    def scroll(self, dy):
        self._mouse.scroll(0, dy)

    @_locked
    def move(self, dx, dy):
        self._mouse.move(dx, dy)

    @_locked
    def position(self):
        x, y = self._mouse.position
        return int(x), int(y)

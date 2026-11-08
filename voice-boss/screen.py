#!/usr/bin/env python3
"""Oczy i ręce szefa. Użycie:
  python screen.py shot            -> zapisuje zrzut, wypisuje jego ścieżkę
  python screen.py click X Y [--right|--double]
  python screen.py move X Y
  python screen.py type "tekst"
  python screen.py key ctrl+c
  python screen.py scroll -300
Współrzędne X,Y są ze zrzutu (shot). Awaryjny stop: ruszyć myszą w róg ekranu.
Wymaga: pip install pyautogui pillow
"""
import json, sys, tempfile
from pathlib import Path
import pyautogui

pyautogui.FAILSAFE = True   # mysz w lewym górnym rogu = wyjątek
pyautogui.PAUSE = 0.15
STATE = Path(tempfile.gettempdir()) / "voiceboss_shot.json"
MAXW = 1280


def scale():
    try:
        return json.loads(STATE.read_text())["k"]
    except Exception:
        return 1.0


def main(a):
    if not a:
        print(__doc__); return 1
    c = a[0]
    if c == "shot":
        img = pyautogui.screenshot()
        k = max(1.0, img.width / MAXW)
        if k > 1:
            img = img.resize((int(img.width / k), int(img.height / k)))
        out = Path(tempfile.gettempdir()) / "voiceboss_shot.png"
        img.save(out); STATE.write_text(json.dumps({"k": k}))
        print(f"{out} ({img.width}x{img.height})")
    elif c in ("click", "move"):
        k = scale(); x, y = int(float(a[1]) * k), int(float(a[2]) * k)
        if c == "move":
            pyautogui.moveTo(x, y, duration=0.2)
        elif "--double" in a:
            pyautogui.doubleClick(x, y)
        else:
            pyautogui.click(x, y, button="right" if "--right" in a else "left")
        print("ok")
    elif c == "type":
        pyautogui.write(a[1], interval=0.02); print("ok")
    elif c == "key":
        pyautogui.hotkey(*a[1].split("+")); print("ok")
    elif c == "scroll":
        pyautogui.scroll(int(a[1])); print("ok")
    else:
        print(__doc__); return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

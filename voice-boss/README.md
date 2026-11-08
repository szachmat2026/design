# Voice Boss

Głosowy szef: mówisz → Claude Code (na Twoim PC) działa → odpowiada 1–2 zdaniami głosem.

## Start (Windows, PowerShell)
```powershell
cd voice-boss
python server.py --cwd C:\Users\pionk\Desktop\PROJEKTY\cleanyourtv
```
Otwórz wypisany link w Chrome/Edge. Wymagany zalogowany `claude` w PATH.

## Obsługa
- 🎙 / spacja: włącz/wycisz mikrofon (tryb „przytrzymaj” = push-to-talk).
- Mówisz dużo → szef czeka. Dopiero po ciszy (domyślnie 4 s) wysyła całość i odpowiada.
- Zaczynasz mówić → głos szefa przerywa się.
- Wiadomości w trakcie pracy trafiają do kolejki. Widzisz na żywo, jakie komendy uruchamia (▸).

## Bezpieczeństwo
Szef wykonuje komendy (Bash/Edit/Write) bez pytania. Serwer słucha tylko na 127.0.0.1
i wymaga losowego tokenu z linku. Zawęź uprawnienia: `--allowed Read Glob Grep`.

## Sterowanie ekranem (opcjonalne)
```powershell
pip install pyautogui pillow
python server.py --cwd $HOME --screen
```
Szef robi zrzuty ekranu (`screen.py shot`), czyta je i klika/pisze (`click`, `type`, `key`, `scroll`).
Przerwanie: przycisk **STOP** / klawisz **Esc** w UI albo ruch myszą w lewy górny róg ekranu.

## Ikonka na pulpicie
```powershell
powershell -ExecutionPolicy Bypass -File voice-boss\install-shortcut.ps1 -Screen
```
Dodaj `-Autostart`, by szef startował razem z Windows. Kliknięcie ikonki uruchamia serwer
(zminimalizowane okno) i otwiera przeglądarkę. Zamknięcie tego okna wyłącza szefa.

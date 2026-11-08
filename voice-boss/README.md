# KADM Studio (Voice Boss)

Czat z Claude Code działającym na Twoim PC: piszesz albo mówisz, szef wykonuje komendy, edytuje pliki,
steruje ekranem i odpowiada. Historia rozmów, wiele rozmów naraz, działa też z telefonu.

## Start (Windows)
```powershell
pip install pyautogui pillow psutil   # ekran, ikona, statystyki (CPU/RAM/temp)
python voice-boss\server.py --cwd $HOME --screen --open
```
Wymaga zalogowanego `claude` (wpisz `claude` raz i zaloguj się).
Ikonka na pulpicie i autostart: `powershell -ExecutionPolicy Bypass -File voice-boss\install-shortcut.ps1 -Screen -Autostart`

## Obsługa
- Pole tekstowe: Enter wysyła, Shift+Enter nowa linia. 🎙 lub Ctrl+Spacja = mikrofon (po ciszy wysyła i czyta odpowiedź).
- Lewy panel: lista rozmów, nowa rozmowa, usuwanie. Ustawienia: uprawnienia, ekran, motyw.
- STOP / Esc przerywa szefa. Tryb ekranu: dodatkowo mysz w lewy górny róg.

## Dostęp z każdego miejsca (telefon, inny komputer)
1. Ustaw hasło: `python voice-boss\server.py --set-password`
2. Uruchom z dostępem z sieci: `python voice-boss\server.py --host 0.0.0.0 --cwd $HOME`
3. Wejdź z innego urządzenia przez **HTTPS**. Najbezpieczniej: Tailscale (`tailscale serve 8765`) lub Cloudflare Tunnel.
   Nie wystawiaj portu bezpośrednio do internetu.
Logowanie hasłem (scrypt), ciasteczko HttpOnly/SameSite, limit 5 prób/min, blokada żądań z obcych stron.
Na telefonie: „Dodaj do ekranu głównego" instaluje to jak aplikację.

## Wygląd
Kolory i czcionka to zmienne CSS na górze `index.html` (`--acc`, `--bg`...). Logo: `python voice-boss\make_icon.py`
pobiera je z kadm.app (lub wrzuć `voice-boss\kadm.png`).

## Uwaga
Szef wykonuje komendy na Twoim komputerze bez pytania (tryb `acceptEdits`). Dane rozmów: `%USERPROFILE%\.voiceboss`.

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

## Wdrożenie pod kadm.app (telefon, każde miejsce)
Aplikacja działa na Twoim PC, a internet widzi ją tylko przez tunel. Zalecana ścieżka (Cloudflare Tunnel, darmowy):
1. Zabezpiecz: `python voice-boss\server.py --set-password` (min. 12 znaków) oraz `--set-2fa` (kody z aplikacji, wpisz klucz w Authenticator).
2. Uruchom serwer na stałym prefiksie: `python voice-boss\server.py --cwd $HOME --screen --base /studio`
3. `cloudflared tunnel --url http://127.0.0.1:8765` (lub nazwany tunel: `studio.kadm.app -> http://127.0.0.1:8765`).
4. Podstrona **kadm.app/studio**: w hostingu strony ustaw przekierowanie/proxy `/studio/*` na adres tunelu
   (Cloudflare Worker/Rules, Vercel `rewrites`, nginx `proxy_pass` - zob. niżej). Zamiast prefiksu możesz użyć subdomeny `studio.kadm.app` (wtedy bez `--base`).
5. Na telefonie: otwórz adres, zaloguj się, „Dodaj do ekranu głównego" (PWA).

nginx (przykład): `location /studio/ { proxy_pass http://TUNEL_LUB_IP:8765; proxy_http_version 1.1; proxy_buffering off; proxy_set_header X-Forwarded-Proto https; }`
Wymagane: HTTPS (mikrofon w przeglądarce działa tylko na HTTPS), wyłączone buforowanie dla `/studio/events` (SSE).
Ochrona: hasło (scrypt) + 2FA (TOTP), ciasteczko HttpOnly/SameSite/Secure, limit 5 prób/min, blokada żądań z obcych stron.
Dodatkowo włącz Cloudflare Access przed `/studio` - to ostatnia linia obrony, bo kto się zaloguje, steruje Twoim komputerem.

## Wtyczki i MCP z poziomu aplikacji
Przycisk **Wtyczki** w lewym panelu: zainstalowane (włącz/wyłącz/aktualizuj/usuń), katalog z wyszukiwarką (instalacja),
serwery MCP (lista, dodawanie, usuwanie) i źródła (marketplace). Zmiany działają w nowych rozmowach.

## Wygląd
Kolory i czcionka to zmienne CSS na górze `index.html` (`--acc`, `--bg`...). Logo: `python voice-boss\make_icon.py`
pobiera je z kadm.app (lub wrzuć `voice-boss\kadm.png`).

## Uwaga
Szef wykonuje komendy na Twoim komputerze bez pytania (tryb `acceptEdits`). Dane rozmów: `%USERPROFILE%\.voiceboss`.

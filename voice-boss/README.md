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

## Dostawcy AI, tokeny i limity
Przycisk **Zużycie**: tokeny dziś i z 14 dni (wg dostawcy i rozmowy), limity dzienne (tokeny / USD / na dostawcę),
dostawcy i kolejność, lista rozmów z przełączaniem dostawcy i STOP dla każdej (albo „Zatrzymaj wszystkie").
Dostawca jest ustawiany per rozmowa (lista w nagłówku): **Auto**, Claude Code, Codex, lokalne AI (Ollama) lub własne CLI.
- **Auto** idzie po kolejności z zakładki Dostawcy. Gdy dostawca zgłosi limit (np. „usage limit reached"), dostaje blokadę
  na 30 min i zapytanie leci do następnego, z kontekstem rozmowy. Po przekroczeniu limitu dziennego płatni są pomijani,
  a lokalne AI działa dalej (nie liczy się do limitu).
- **Codex:** `npm i -g @openai/codex`, potem `codex login`. Wywołanie: `codex exec <argumenty> -` (prompt ze stdin); argumenty edytujesz w UI.
- **Lokalne AI:** `ollama pull llama3.2`. Adres może być zdalny (np. przez Tailscale). Tryb tylko czat: bez narzędzi i plików.
  Wpisz MAC komputera, a gdy Ollama nie odpowiada, aplikacja wyśle Wake-on-LAN i poczeka na start (działa, gdy serwer
  jest w tej samej sieci LAN co budzony komputer; z VPS potrzebny jest przekaźnik w domu, np. router/Raspberry Pi).
- **Własne CLI** (np. OpenCode/inne): „Własne CLI” → polecenie, np. `opencode run`; `{prompt}` w poleceniu zostanie podmienione,
  inaczej prompt jest dopisany na końcu (lub idzie przez stdin).
- Tokeny: Claude podaje dokładne wartości, Codex/własne CLI są szacowane (oznaczone „szac."). Kwota USD przy subskrypcji
  to wartość API, nie rachunek.

## Serwer 24/7 (Linux, systemd)
`/etc/systemd/system/kadm-studio.service`: `ExecStart=/usr/bin/python3 /opt/design/voice-boss/server.py --cwd /srv/work --base /studio`,
`Restart=always`, `User=kadm`. Potem `systemctl enable --now kadm-studio`. Na serwerze zaloguj `claude` i `codex` tym samym użytkownikiem.

## Praca na żywo i komentarze
Odpowiedź pojawia się w trakcie pisania, a pod nią kroki agenta (komendy, tworzone pliki z podglądem treści). Gdy agent pracuje,
pole tekstowe zmienia się w „Komentarz": wysłana wiadomość trafia do tego samego procesu (Claude Code w trybie strumieniowym),
bez restartu i utraty kontekstu. Po głosie aplikacja po ok. 2,5 s mówi „Już się za to biorę". Przełącznik trybu: Zużycie → Dostawcy → Claude Code.
Odświeżenie strony w trakcie pracy odtwarza kartę pracy.

## Lokalne AI jako agent
Ollama dostaje narzędzia (komendy, odczyt/zapis plików, listowanie katalogów), więc faktycznie wykonuje zadania, nie tylko czatuje.
Wymaga modelu z obsługą narzędzi (np. `ollama pull qwen2.5-coder` lub `llama3.1`); model bez narzędzi przechodzi w tryb czatu.
Uprawnienia wynikają z listy `--allowed` serwera (Bash, Read, Write...).

## Głosy
Ustawienia → Głos odczytu: głosy neuronowe z serwera (`pip install edge-tts`; Zofia, Marek; działają też na telefonie, wymagają internetu)
oraz wszystkie głosy przeglądarki/systemu. Tempo i test głosu w tym samym oknie.

## Wtyczki z GitHuba
Wtyczki → **Odkrywaj (GitHub)**: wyszukiwarka katalogów wtyczek (temat `claude-code-marketplace`, kilkaset repozytoriów) lub wpisz `owner/repo`;
„Dodaj katalog" pobiera go, a jego wtyczki pojawiają się w **Katalog** (filtr katalogów, wyszukiwarka, instalacja jednym kliknięciem).
Limit GitHub bez tokenu to 10 zapytań/min; opcjonalny token wpisz na dole zakładki (lub zmienna `GITHUB_TOKEN`).

## Wtyczki i MCP z poziomu aplikacji
Przycisk **Wtyczki** w lewym panelu: zainstalowane (włącz/wyłącz/aktualizuj/usuń), katalog z wyszukiwarką (instalacja),
serwery MCP (lista, dodawanie, usuwanie) i źródła (marketplace). Zmiany działają w nowych rozmowach.

## Wygląd
Kolory i czcionka to zmienne CSS na górze `index.html` (`--acc`, `--bg`...). Logo: `python voice-boss\make_icon.py` (zapisze kadm-logo.png i kadm.ico; własny plik wrzuć jako `kadm-logo.png`)
pobiera je z kadm.app (lub wrzuć `voice-boss\kadm.png`).

## Uwaga
Szef wykonuje komendy na Twoim komputerze bez pytania (tryb `acceptEdits`). Dane rozmów: `%USERPROFILE%\.voiceboss`.

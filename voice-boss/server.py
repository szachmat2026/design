#!/usr/bin/env python3
"""Voice Boss - lokalny serwer. Przeglądarka (mikrofon) -> `claude -p` -> SSE do UI.

Start:  python server.py [--cwd C:\\sciezka\\projektu]
Wymaga: zainstalowany i zalogowany `claude` (Claude Code CLI). Tylko stdlib.
"""
import argparse, json, os, queue, secrets, shutil, subprocess, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).parent
SYSTEM = (
    "Jesteś szefem (głównym agentem) sterowanym głosem. Użytkownik mówi do mikrofonu, "
    "wiadomości to transkrypcja mowy (mogą być literówki). Odpowiadaj PO POLSKU, maksymalnie "
    "1-2 krótkie zdania, bez markdownu, listy i kodu - odpowiedź będzie czytana na głos. "
    "Działaj sam (komendy, pliki, ADB, SSH), nie pytaj o oczywistości. Pytaj tylko przed "
    "czymś nieodwracalnym. Na końcu powiedz tylko kluczowy wynik."
)

SCREEN_HINT = (
    " Masz dostęp do ekranu użytkownika: uruchom `python {path}/screen.py shot`, potem Read na wypisanym pliku PNG, "
    "aby zobaczyć ekran. Steruj przez `screen.py click X Y`, `type \"tekst\"`, `key ctrl+c`, `scroll N` "
    "(X,Y ze zrzutu). Po każdej akcji zrób nowy zrzut i sprawdź efekt. Nie wpisuj haseł ani nie potwierdzaj płatności "
    "bez wyraźnej zgody użytkownika. Użytkownik w każdej chwili może przerwać (STOP)."
)

state = {"proc": None, "session": None, "busy": False, "pending": [], "clients": []}
lock = threading.Lock()
TOKEN = secrets.token_urlsafe(16)
ARGS = None


def broadcast(ev):
    data = f"data: {json.dumps(ev, ensure_ascii=False)}\n\n".encode()
    with lock:
        for q in list(state["clients"]):
            q.put(data)


def run_job(text):
    cmd = [shutil.which("claude") or "claude", "-p", text, "--output-format", "stream-json",
           "--verbose", "--append-system-prompt",
           SYSTEM + (SCREEN_HINT.format(path=str(HERE)) if ARGS.screen else ""),
           "--permission-mode", ARGS.permission]
    if ARGS.allowed:
        cmd += ["--allowedTools", *ARGS.allowed]
    if state["session"]:
        cmd += ["--resume", state["session"]]
    broadcast({"type": "status", "busy": True})
    try:
        p = subprocess.Popen(cmd, cwd=ARGS.cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding="utf-8", errors="replace")
        state["proc"] = p
        for line in p.stdout:
            try:
                ev = json.loads(line)
            except ValueError:
                broadcast({"type": "log", "text": line.strip()[:300]}); continue
            if ev.get("session_id"):
                state["session"] = ev["session_id"]
            t = ev.get("type")
            if t == "assistant":
                for b in ev.get("message", {}).get("content", []):
                    if b.get("type") == "tool_use":
                        inp = b.get("input", {})
                        desc = inp.get("command") or inp.get("file_path") or inp.get("description") or ""
                        broadcast({"type": "tool", "name": b.get("name"), "text": str(desc)[:200]})
            elif t == "result":
                broadcast({"type": "reply", "text": ev.get("result", "")})
        p.wait()
    except FileNotFoundError:
        broadcast({"type": "reply", "text": "Nie znajduję polecenia claude. Zainstaluj Claude Code."})
    except Exception as e:  # noqa
        broadcast({"type": "reply", "text": f"Błąd: {e}"})
    finally:
        with lock:
            nxt = " ".join(state["pending"]); state["pending"].clear()
            state["busy"] = bool(nxt)
        if nxt:
            threading.Thread(target=run_job, args=(nxt,), daemon=True).start()
        else:
            broadcast({"type": "status", "busy": False})


def submit(text):
    with lock:
        if state["busy"]:
            state["pending"].append(text); return "queued"
        state["busy"] = True
    threading.Thread(target=run_job, args=(text,), daemon=True).start()
    return "started"


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _ok(self):
        host = self.headers.get("Host", "")
        if host.split(":")[0] not in ("127.0.0.1", "localhost"):
            return False
        return self.headers.get("X-Token") == TOKEN or ("t=" + TOKEN) in self.path

    def do_GET(self):
        if not self._ok():
            self.send_error(403); return
        if self.path.startswith("/events"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream"); self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            q = queue.Queue()
            with lock: state["clients"].append(q)
            try:
                self.wfile.write(b": hi\n\n"); self.wfile.flush()
                while True:
                    try: self.wfile.write(q.get(timeout=15))
                    except queue.Empty: self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
            except Exception:
                pass
            finally:
                with lock: state["clients"].remove(q)
            return
        body = (HERE / "index.html").read_bytes().replace(b"__TOKEN__", TOKEN.encode())
        self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers(); self.wfile.write(body)

    def do_POST(self):
        if not self._ok():
            self.send_error(403); return
        n = int(self.headers.get("Content-Length", 0))
        data = json.loads(self.rfile.read(n) or b"{}")
        if self.path.startswith("/say"):
            r = submit(data.get("text", "").strip()) if data.get("text", "").strip() else "empty"
        elif self.path.startswith("/stop"):
            with lock: state["pending"].clear()
            p = state.get("proc")
            if p and p.poll() is None: p.kill()
            r = "stopped"
        elif self.path.startswith("/reset"):
            state["session"] = None; r = "reset"
        else:
            self.send_error(404); return
        self.send_response(200); self.end_headers(); self.wfile.write(r.encode())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--cwd", default=os.getcwd(), help="katalog roboczy szefa")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--permission", default="acceptEdits")
    ap.add_argument("--allowed", nargs="*", default=["Bash", "Read", "Edit", "Write", "Glob", "Grep"])
    ap.add_argument("--open", action="store_true", help="otwórz przeglądarkę po starcie")
    ap.add_argument("--screen", action="store_true", help="daj szefowi widok ekranu i mysz/klawiaturę (screen.py)")
    ARGS = ap.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", ARGS.port), H)
    print(f"Otwórz w Chrome/Edge:  http://127.0.0.1:{ARGS.port}/?t={TOKEN}\nKatalog szefa: {ARGS.cwd}")
    if ARGS.open:
        import webbrowser; threading.Timer(1, webbrowser.open, [f"http://127.0.0.1:{ARGS.port}/?t={TOKEN}"]).start()
    srv.serve_forever()

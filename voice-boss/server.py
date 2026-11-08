#!/usr/bin/env python3
"""KADM Studio (Voice Boss) - czat z Claude Code na Twoim PC, z każdego miejsca.

  python server.py --set-password                 # ustaw hasło (raz)
  python server.py [--cwd DIR] [--open] [--screen]
  python server.py --host 0.0.0.0 --cwd DIR       # dostęp z sieci/telefonu (wymaga hasła)

Wymaga zalogowanego `claude` (Claude Code CLI). Tylko stdlib.
"""
import argparse, getpass, hashlib, hmac, json, os, queue, secrets, shutil, subprocess, sys, threading, time, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).parent
DATA = Path(os.environ.get("VOICEBOSS_HOME", Path.home() / ".voiceboss")); DATA.mkdir(parents=True, exist_ok=True)
CFG_F, CHATS_F, SESS_F = DATA / "config.json", DATA / "chats.json", DATA / "sessions.json"

SYS_BASE = ("Jesteś głównym agentem (szefem) użytkownika, działasz na jego komputerze: komendy, pliki, ADB, SSH, "
            "serwery. Działaj sam, nie pytaj o oczywistości; pytaj tylko przed czymś nieodwracalnym. "
            "Odpowiadaj po polsku. Wiadomości z mikrofonu to transkrypcja mowy (mogą mieć literówki).")
SYS_VOICE = " Odpowiedź będzie czytana na głos: 1-2 krótkie zdania, bez markdownu, list i kodu, tylko kluczowy wynik."
SYS_TEXT = " Odpowiadaj zwięźle; możesz użyć markdownu i bloków kodu."
SYS_SCREEN = (" Masz dostęp do ekranu użytkownika: `python {p}/screen.py shot`, potem Read na wypisanym PNG. "
              "Steruj: `screen.py click X Y`, `type \"tekst\"`, `key ctrl+c`, `scroll N` (X,Y ze zrzutu). Po każdej akcji zrób "
              "nowy zrzut i sprawdź efekt. Nie wpisuj haseł ani nie potwierdzaj płatności bez wyraźnej zgody.")

lock = threading.RLock()
clients, procs, ARGS = [], {}, None
TOKEN = secrets.token_urlsafe(16)
fails = {}


def jload(p, d):
    try: return json.loads(p.read_text(encoding="utf-8"))
    except Exception: return d


def jsave(p, o):
    t = p.with_suffix(".tmp"); t.write_text(json.dumps(o, ensure_ascii=False), encoding="utf-8"); t.replace(p)


CFG = jload(CFG_F, {})
CHATS = jload(CHATS_F, {})
SESS = jload(SESS_F, {})   # sha256(token) -> expiry


def hash_pw(pw, salt=None):
    salt = salt or secrets.token_hex(16)
    return salt + "$" + hashlib.scrypt(pw.encode(), salt=salt.encode(), n=2**14, r=8, p=1).hex()


def check_pw(pw):
    h = CFG.get("pw")
    if not h: return False
    salt = h.split("$")[0]
    return hmac.compare_digest(hash_pw(pw, salt), h)


def new_session():
    t = secrets.token_urlsafe(32)
    with lock:
        now = time.time()
        for k in [k for k, v in SESS.items() if v < now]: del SESS[k]
        SESS[hashlib.sha256(t.encode()).hexdigest()] = now + 30 * 86400; jsave(SESS_F, SESS)
    return t


def valid_session(t):
    if not t: return False
    return SESS.get(hashlib.sha256(t.encode()).hexdigest(), 0) > time.time()


def broadcast(ev):
    data = f"data: {json.dumps(ev, ensure_ascii=False)}\n\n".encode()
    with lock:
        for q in list(clients): q.put(data)


def save_chats():
    with lock: jsave(CHATS_F, CHATS)


def settings():
    return {"screen": CFG.get("screen", ARGS.screen), "permission": CFG.get("permission", ARGS.permission)}


def summary(c):
    return {"id": c["id"], "title": c["title"], "updated": c["updated"], "busy": c.get("busy", False), "cwd": c["cwd"]}


def add_msg(c, m):
    m["ts"] = time.time()
    with lock:
        c["messages"].append(m); c["updated"] = m["ts"]
    save_chats()


def run_job(c, text, mode):
    st = settings()
    sysm = SYS_BASE + (SYS_VOICE if mode == "voice" else SYS_TEXT) + (SYS_SCREEN.format(p=str(HERE)) if st["screen"] else "")
    cmd = [shutil.which("claude") or "claude", "-p", text, "--output-format", "stream-json", "--verbose",
           "--append-system-prompt", sysm, "--permission-mode", st["permission"]]
    if ARGS.allowed: cmd += ["--allowedTools", *ARGS.allowed]
    if c.get("session"): cmd += ["--resume", c["session"]]
    c["busy"] = True; broadcast({"type": "busy", "chat": c["id"], "busy": True})
    tools, reply = [], None
    try:
        p = subprocess.Popen(cmd, cwd=c["cwd"] if Path(c["cwd"]).is_dir() else None, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        procs[c["id"]] = p
        for line in p.stdout:
            try: ev = json.loads(line)
            except ValueError:
                if line.strip(): reply = (reply or "") + line.strip() + "\n"
                continue
            if ev.get("session_id"): c["session"] = ev["session_id"]
            t = ev.get("type")
            if t == "assistant":
                for b in ev.get("message", {}).get("content", []):
                    if b.get("type") == "tool_use":
                        inp = b.get("input", {})
                        d = {"name": b.get("name"), "text": str(inp.get("command") or inp.get("file_path") or inp.get("description") or inp.get("pattern") or "")[:300]}
                        tools.append(d); broadcast({"type": "tool", "chat": c["id"], **d})
            elif t == "result":
                reply = ev.get("result", "") or reply
        rc = p.wait()
        if rc and rc < 0: reply = (reply or "") + "\n(przerwano)"
    except FileNotFoundError:
        reply = "Nie znajduję polecenia `claude`. Zainstaluj Claude Code i zaloguj się (`claude`)."
    except Exception as e:  # noqa
        reply = f"Błąd: {e}"
    finally:
        procs.pop(c["id"], None)
    m = {"role": "assistant", "text": (reply or "(brak odpowiedzi)").strip(), "tools": tools, "mode": mode}
    add_msg(c, m); broadcast({"type": "msg", "chat": c["id"], "msg": m})
    with lock:
        nxt = c.pop("pending", [])
        if not nxt: c["busy"] = False
    if nxt:
        threading.Thread(target=run_job, args=(c, " ".join(t for t, _ in nxt), nxt[-1][1]), daemon=True).start()
    else:
        broadcast({"type": "busy", "chat": c["id"], "busy": False}); broadcast({"type": "chats"})


def say(c, text, mode):
    m = {"role": "user", "text": text, "mode": mode}
    add_msg(c, m)
    if c["title"] == "Nowa rozmowa":
        c["title"] = text[:48]; save_chats(); broadcast({"type": "chats"})
    with lock:
        if c.get("busy"):
            c.setdefault("pending", []).append((text, mode)); return m, "queued"
        c["busy"] = True
    threading.Thread(target=run_job, args=(c, text, mode), daemon=True).start()
    return m, "started"


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def cookie(self):
        for part in self.headers.get("Cookie", "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "vb": return v

    def authed(self): return valid_session(self.cookie())

    def loopback(self): return self.client_address[0] in ("127.0.0.1", "::1")

    def send(self, code=200, body=b"", ctype="application/json", extra=()):
        if isinstance(body, (dict, list)): body = json.dumps(body, ensure_ascii=False).encode()
        elif isinstance(body, str): body = body.encode()
        self.send_response(code); self.send_header("Content-Type", ctype + ("; charset=utf-8" if "text" in ctype or "json" in ctype else ""))
        self.send_header("Content-Length", str(len(body))); self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in extra: self.send_header(k, v)
        self.end_headers(); self.wfile.write(body)

    def set_cookie(self, tok):
        sec = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
        return ("Set-Cookie", f"vb={tok}; HttpOnly; SameSite=Strict; Path=/; Max-Age={30*86400}{sec}")

    def same_origin(self):
        o = self.headers.get("Origin")
        return not o or urlparse(o).netloc == self.headers.get("Host")

    def do_GET(self):
        u = urlparse(self.path); path = u.path
        if path == "/" and u.query.startswith("t=") and u.query[2:] == TOKEN and self.loopback():
            return self.send(302, b"", extra=[("Location", "/"), self.set_cookie(new_session())])
        if path == "/": return self.send(200, (HERE / "index.html").read_bytes(), "text/html")
        if path == "/manifest.json":
            return self.send(200, {"name": "KADM Studio", "short_name": "KADM", "start_url": "/", "display": "standalone",
                                   "background_color": "#0e1116", "theme_color": "#0e1116",
                                   "icons": [{"src": "/icon.png", "sizes": "any", "type": "image/png"}]}, "application/manifest+json")
        if path == "/icon.png":
            f = HERE / "kadm.png"
            if f.exists(): return self.send(200, f.read_bytes(), "image/png")
            return self.send(404)
        if not self.authed(): return self.send(401, {"error": "login"})
        if path == "/events": return self.sse()
        if path == "/api/me": return self.send(200, {"ok": True, **settings()})
        if path == "/api/chats":
            with lock: return self.send(200, sorted((summary(c) for c in CHATS.values()), key=lambda x: -x["updated"]))
        if path.startswith("/api/chats/"):
            c = CHATS.get(path.split("/")[3])
            return self.send(200, {**summary(c), "messages": c["messages"]}) if c else self.send(404)
        self.send(404)

    def sse(self):
        self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.send_header("Cache-Control", "no-cache")
        self.end_headers(); q = queue.Queue()
        with lock: clients.append(q)
        try:
            self.wfile.write(b": hi\n\n"); self.wfile.flush()
            while True:
                try: self.wfile.write(q.get(timeout=15))
                except queue.Empty: self.wfile.write(b": ping\n\n")
                self.wfile.flush()
        except Exception: pass
        finally:
            with lock: clients.remove(q)

    def do_POST(self):
        if not self.same_origin(): return self.send(403)
        n = int(self.headers.get("Content-Length", 0) or 0)
        try: d = json.loads(self.rfile.read(n) or b"{}")
        except ValueError: return self.send(400)
        path = urlparse(self.path).path
        if path == "/api/login":
            ip = self.client_address[0]; now = time.time()
            fails[ip] = [t for t in fails.get(ip, []) if now - t < 60]
            if len(fails[ip]) >= 5: return self.send(429, {"error": "za dużo prób, odczekaj minutę"})
            if check_pw(str(d.get("password", ""))):
                return self.send(200, {"ok": True}, extra=[self.set_cookie(new_session())])
            fails[ip].append(now); time.sleep(0.5); return self.send(401, {"error": "złe hasło"})
        if not self.authed(): return self.send(401, {"error": "login"})
        if path == "/api/logout":
            with lock: SESS.pop(hashlib.sha256((self.cookie() or "").encode()).hexdigest(), None); jsave(SESS_F, SESS)
            return self.send(200, {"ok": True}, extra=[("Set-Cookie", "vb=; Max-Age=0; Path=/")])
        if path == "/api/settings":
            for k in ("screen", "permission"):
                if k in d: CFG[k] = d[k] if k == "screen" else (d[k] if d[k] in ("default", "acceptEdits", "plan", "auto") else "acceptEdits")
            jsave(CFG_F, CFG); return self.send(200, settings())
        if path == "/api/chats":
            cwd = str(d.get("cwd") or ARGS.cwd)
            c = {"id": uuid.uuid4().hex[:10], "title": "Nowa rozmowa", "cwd": cwd, "session": None, "messages": [], "updated": time.time()}
            with lock: CHATS[c["id"]] = c
            save_chats(); broadcast({"type": "chats"}); return self.send(200, summary(c))
        parts = path.split("/")
        if len(parts) == 5 and parts[:3] == ["", "api", "chats"] and parts[3] in CHATS:
            c, act = CHATS[parts[3]], parts[4]
            if act == "say":
                text = str(d.get("text", "")).strip()
                if not text: return self.send(400)
                m, r = say(c, text[:20000], "voice" if d.get("voice") else "text")
                return self.send(200, {"status": r, "msg": m})
            if act == "stop":
                with lock: c.pop("pending", None)
                p = procs.get(c["id"])
                if p and p.poll() is None: p.kill()
                return self.send(200, {"ok": True})
            if act == "rename":
                c["title"] = str(d.get("title", ""))[:80] or c["title"]; save_chats(); broadcast({"type": "chats"}); return self.send(200, {"ok": True})
            if act == "delete":
                p = procs.get(c["id"])
                if p and p.poll() is None: p.kill()
                with lock: CHATS.pop(c["id"], None)
                save_chats(); broadcast({"type": "chats"}); return self.send(200, {"ok": True})
        self.send(404)


def main():
    global ARGS
    ap = argparse.ArgumentParser()
    ap.add_argument("--cwd", default=os.getcwd(), help="domyślny katalog roboczy nowych rozmów")
    ap.add_argument("--host", default="127.0.0.1"); ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--permission", default="acceptEdits")
    ap.add_argument("--allowed", nargs="*", default=["Bash", "Read", "Edit", "Write", "Glob", "Grep"])
    ap.add_argument("--screen", action="store_true", help="ekran + mysz/klawiatura (screen.py)")
    ap.add_argument("--open", action="store_true"); ap.add_argument("--set-password", action="store_true")
    ARGS = ap.parse_args()
    for c in CHATS.values(): c["busy"] = False
    if ARGS.set_password:
        a, b = getpass.getpass("Nowe hasło (min. 8 znaków): "), getpass.getpass("Powtórz: ")
        if a != b or len(a) < 8: sys.exit("Hasła różne lub za krótkie.")
        CFG["pw"] = hash_pw(a); jsave(CFG_F, CFG); sys.exit("Hasło zapisane.")
    local = ARGS.host in ("127.0.0.1", "localhost", "::1")
    if not local and not CFG.get("pw"): sys.exit("Dostęp z sieci wymaga hasła: uruchom najpierw  python server.py --set-password")
    srv = ThreadingHTTPServer((ARGS.host, ARGS.port), H)
    url = f"http://127.0.0.1:{ARGS.port}/"
    print(f"KADM Studio: {url}?t={TOKEN}  (automatyczne logowanie tylko z tego komputera)\nKatalog: {ARGS.cwd}  Dane: {DATA}")
    if not local: print(f"Nasłuch na {ARGS.host}:{ARGS.port} - zalogujesz się hasłem. Używaj HTTPS (Tailscale/Cloudflare Tunnel).")
    if ARGS.open:
        import webbrowser; threading.Timer(1, webbrowser.open, [f"{url}?t={TOKEN}"]).start()
    srv.serve_forever()


if __name__ == "__main__":
    main()

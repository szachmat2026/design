#!/usr/bin/env python3
"""KADM Studio (Voice Boss) - czat z Claude Code na Twoim PC, z każdego miejsca.

  python server.py --set-password                 # ustaw hasło (raz)
  python server.py [--cwd DIR] [--open] [--screen]
  python server.py --host 0.0.0.0 --cwd DIR       # dostęp z sieci/telefonu (wymaga hasła)

Wymaga zalogowanego `claude` (Claude Code CLI). Tylko stdlib.
"""
import argparse, getpass, shlex, hashlib, hmac, json, os, queue, secrets, shutil, subprocess, sys, threading, time, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

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

try:
    import psutil
except Exception:
    psutil = None

_stats = {"t": 0, "temp": None, "tt": 0}


def get_temp():
    """Temperatura CPU w °C lub None (psutil/Linux, ACPI na Windows - bez admina bywa niedostępne)."""
    try:
        if psutil and hasattr(psutil, "sensors_temperatures"):
            for v in psutil.sensors_temperatures().values():
                if v: return round(max(x.current for x in v))
        if os.name == "nt":
            out = subprocess.run(["powershell", "-NoProfile", "-Command",
                "(Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZone -ErrorAction Stop).CurrentTemperature | Select -First 1"],
                capture_output=True, text=True, timeout=6).stdout.strip()
            return round(int(out) / 10 - 273.15) if out else None
    except Exception:
        pass
    return None


def stats():
    now = time.time()
    if now - _stats["tt"] > 20:   # temperatura odświeżana rzadziej (wolne zapytanie)
        _stats["tt"] = now
        threading.Thread(target=lambda: _stats.update(temp=get_temp()), daemon=True).start()
    d = {"tok": USAGE.tokens(), "usd": USAGE.usd(), "temp": _stats["temp"], "cpu": None, "ram": None, "load": None, "procs": None}
    if psutil:
        d["cpu"] = psutil.cpu_percent(interval=None); d["ram"] = psutil.virtual_memory().percent
        d["procs"] = len(psutil.pids())
    try: d["load"] = round(os.getloadavg()[0], 2)
    except Exception:
        d["load"] = d["cpu"]
    return d


import base64, re, struct
import providers as P


def totp(secret, t=None, step=30):
    key = base64.b32decode(secret.upper() + "=" * (-len(secret) % 8))
    h = hmac.new(key, struct.pack(">Q", int((t or time.time()) // step)), "sha1").digest()
    o = h[-1] & 15
    return "%06d" % ((struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % 10 ** 6)


def check_totp(code):
    sec = CFG.get("totp")
    return bool(sec) and any(hmac.compare_digest(totp(sec, time.time() + d * 30), code.strip()) for d in (-1, 0, 1))


def cli(args, timeout=180):
    """Uruchamia `claude <args>` i zwraca (kod, tekst)."""
    try:
        r = subprocess.run([shutil.which("claude") or "claude", *args], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout, cwd=ARGS.cwd if Path(ARGS.cwd).is_dir() else None)
        return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()
    except subprocess.TimeoutExpired:
        return 124, "Przekroczono czas."
    except FileNotFoundError:
        return 127, "Nie znaleziono `claude`."


_plug = {"t": 0, "d": None}
SAFE = re.compile(r"^[\w.@:/~+-]{1,200}$")


def plugin_list(force=False):
    if force or not _plug["d"] or time.time() - _plug["t"] > 120:
        rc, out = cli(["plugin", "list", "--json", "--available"], 120)
        try: _plug["d"] = json.loads(out); _plug["t"] = time.time()
        except ValueError: return {"installed": [], "available": [], "error": out[:300]}
    return _plug["d"]


def plugins_view(q, market="", limit=60):
    d = plugin_list(); q = q.lower().strip()
    inst = {i["id"] for i in d.get("installed", [])}
    allav = d.get("available", []); counts = {}
    for a in allav: counts[a.get("marketplaceName")] = counts.get(a.get("marketplaceName"), 0) + 1
    av = [{"id": a.get("pluginId"), "name": a.get("name"), "desc": (a.get("description") or "")[:200],
           "market": a.get("marketplaceName"), "version": a.get("version"), "installed": a.get("pluginId") in inst}
          for a in allav if (not market or a.get("marketplaceName") == market) and (not q or q in (a.get("name", "") + " " + (a.get("description") or "")).lower())]
    ins = [{"id": i["id"], "version": i.get("version"), "scope": i.get("scope"), "enabled": i.get("enabled"),
            "mcp": list((i.get("mcpServers") or {}).keys())} for i in d.get("installed", [])]
    return {"installed": ins, "available": av[:max(10, min(limit, 400))], "total": len(av), "error": d.get("error"),
            "markets": sorted(({"name": k, "count": v} for k, v in counts.items() if k), key=lambda x: -x["count"])}


def gh_token():
    return os.environ.get("GITHUB_TOKEN") or CFG.get("github_token") or ""


def gh_search(q, page=1, broad=False):
    """Wyszukuje na GitHubie katalogi wtyczek Claude Code (temat claude-code-marketplace) albo repo wprowadzone jako owner/repo."""
    import urllib.request, urllib.error
    h = {"User-Agent": "kadm-studio", "Accept": "application/vnd.github+json"}
    if gh_token(): h["Authorization"] = "Bearer " + gh_token()
    q = q.strip()
    if re.match(r"^[\w.-]+/[\w.-]+$", q): url = "https://api.github.com/search/repositories?q=" + "repo:" + q
    else:
        topic = "topic:claude-code-plugin" if broad else "topic:claude-code-marketplace"
        from urllib.parse import quote
        url = "https://api.github.com/search/repositories?q=" + quote((q + " " + topic).strip()) + "&sort=stars&order=desc&per_page=20&page=" + str(max(1, min(page, 10)))
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=20) as r: d = json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"error": "Limit zapytań GitHub (dodaj token poniżej)." if e.code in (403, 429) else f"GitHub: HTTP {e.code}", "items": []}
    except Exception as e:  # noqa
        return {"error": f"Brak połączenia z GitHub: {e}", "items": []}
    items = d["items"] if "items" in d else [d]
    return {"total": d.get("total_count", len(items)), "token": bool(gh_token()), "items": [
        {"repo": i["full_name"], "stars": i.get("stargazers_count", 0), "desc": (i.get("description") or "")[:200],
         "topics": (i.get("topics") or [])[:6], "updated": (i.get("pushed_at") or "")[:10]} for i in items]}


def mcp_view():
    rc, out = cli(["mcp", "list"], 90); rows = []
    for line in out.splitlines():
        m = re.match(r"^(\S.*?): (\S+)(?: \((\w+)\))? - (.*)$", line)
        if m: rows.append({"name": m.group(1), "url": m.group(2), "type": m.group(3), "status": m.group(4)})
    return rows


def market_view():
    rc, out = cli(["plugin", "marketplace", "list", "--json"], 60)
    try:
        d = json.loads(out); return d if isinstance(d, list) else d.get("marketplaces", [])
    except ValueError:
        return [{"name": l.strip("- ").strip()} for l in out.splitlines() if l.strip() and not l.lower().startswith("config")]


lock = threading.RLock()
clients, procs, ARGS, BASE = [], {}, None, ""
TOKEN = secrets.token_urlsafe(16)
fails = {}


def jload(p, d):
    try: return json.loads(p.read_text(encoding="utf-8"))
    except Exception: return d


def jsave(p, o):
    t = p.with_suffix(".tmp"); t.write_text(json.dumps(o, ensure_ascii=False), encoding="utf-8"); t.replace(p)


CFG = jload(CFG_F, {})
USAGE = P.Usage(DATA / "usage.json")
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
    with lock: jsave(CHATS_F, {k: {a: b for a, b in c.items() if not a.startswith("_")} for k, c in CHATS.items()})


def settings():
    return {"screen": CFG.get("screen", ARGS.screen), "permission": CFG.get("permission", ARGS.permission)}


def summary(c):
    tok = sum((m.get("usage") or {}).get("in", 0) + (m.get("usage") or {}).get("out", 0) for m in c["messages"])
    return {"id": c["id"], "title": c["title"], "updated": c["updated"], "busy": c.get("busy", False), "cwd": c["cwd"],
            "provider": c.get("provider", "auto"), "tokens": tok}


def add_msg(c, m):
    m["ts"] = time.time()
    with lock:
        c["messages"].append(m); c["updated"] = m["ts"]
    save_chats()


def mode_prefix(mode):
    return "[Wiadomość głosowa: odpowiedz 1-2 krótkimi zdaniami, bez markdownu.] " if mode == "voice" else ""


def run_job(c, text, mode):
    st = settings()
    screen = SYS_SCREEN.format(p=str(HERE)) if st["screen"] else ""
    sysm = SYS_BASE + (SYS_VOICE if mode == "voice" else SYS_TEXT) + screen
    cwd = c["cwd"] if Path(c["cwd"]).is_dir() else None
    c["busy"] = True; broadcast({"type": "busy", "chat": c["id"], "busy": True})
    hist = [{"role": m["role"], "text": m["text"]} for m in c["messages"][:-1] if m.get("text")]
    tools, tried, res = [], [], None
    c["_live"] = live = {"t0": time.time(), "text": "", "steps": []}

    def tool(name, txt, detail=None):
        d = {"name": name, "text": txt, "detail": detail or txt}; tools.append(d); live["steps"].append(dict(d))
        broadcast({"type": "tool", "chat": c["id"], **d})

    def toolres(txt):
        if live["steps"]: live["steps"][-1]["result"] = txt
        if tools: tools[-1]["result"] = txt
        broadcast({"type": "toolres", "chat": c["id"], "text": txt})

    def delta(t):
        live["text"] += t; broadcast({"type": "delta", "chat": c["id"], "text": t})

    def register(p): procs[c["id"]] = p

    ctx = {"text": text, "text_live": mode_prefix(mode) + text, "chat_id": c["id"], "sysm": sysm, "sysm_live": SYS_BASE + SYS_TEXT + screen,
           "sysm_plain": SYS_BASE + " Nie masz dostępu do narzędzi ani plików: odpowiadaj tekstem." + (SYS_VOICE if mode == "voice" else SYS_TEXT),
           "sysm_agent": SYS_BASE + " Masz narzędzia (run_command, read_file, list_dir, write_file): używaj ich, żeby faktycznie wykonać zadanie, krok po kroku. Na końcu krótko podsumuj wynik." + (SYS_VOICE if mode == "voice" else SYS_TEXT),
           "permission": st["permission"], "allowed": ARGS.allowed, "cwd": cwd, "session": c.get("session"), "history": hist,
           "tool": tool, "toolres": toolres, "delta": delta, "turn_end": lambda: None, "register": register}
    try:
        for pid in P.order(CFG, USAGE, c.get("provider", "auto")):
            live["text"] = ""; live["steps"].clear(); tools.clear()
            res = P.run(pid, CFG, ctx); tried.append(pid)
            if res.get("session") and pid == "claude": c["session"] = res["session"]
            if res.get("error") == "limit":
                CFG.setdefault("cooldown", {})[pid] = time.time() + P.COOLDOWN; jsave(CFG_F, CFG)
                if c.get("provider", "auto") == "auto":
                    tool("limit", f"{pid}: limit - przełączam na następnego dostawcę"); continue
            break
    except Exception as e:  # noqa
        res = {"reply": f"Błąd: {e}", "usage": {}, "provider": None}
    finally:
        procs.pop(c["id"], None)
    if res is None:
        res = {"reply": "Brak dostępnego dostawcy AI (limity, budżet lub wyłączeni). Zmień dostawcę lub limity w „Zużycie”.", "usage": {}, "provider": None}
    turns = res.get("turns") or [{"reply": res.get("reply"), "usage": res.get("usage") or {}, "tools": [dict(t) for t in tools], "error": res.get("error")}]
    for i, t in enumerate(turns):
        u = t.get("usage") or {}
        if res.get("provider"):
            USAGE.add(chat=c["id"], provider=res["provider"], **{k: u.get(k, 0) for k in ("in", "out", "cache")}, usd=u.get("usd"), est=bool(u.get("est")))
        m = {"role": "assistant", "text": (t.get("reply") or "(brak odpowiedzi)").strip(), "tools": [{**d, "detail": (d.get("detail") or "")[:2500]} for d in t.get("tools", [])], "mode": mode,
             "provider": res.get("provider"), "usage": u, "fallback": tried[:-1] if i == 0 else [], "error": t.get("error")}
        add_msg(c, m); broadcast({"type": "msg", "chat": c["id"], "msg": m})
    c.pop("_live", None)
    with lock:
        nxt = c.pop("pending", [])
        if not nxt: c["busy"] = False
    if nxt:
        threading.Thread(target=run_job, args=(c, " ".join(t for t, _ in nxt), nxt[-1][1]), daemon=True).start()
    else:
        broadcast({"type": "busy", "chat": c["id"], "busy": False}); broadcast({"type": "chats"})


def say(c, text, mode):
    m = {"role": "user", "text": text, "mode": mode}
    if c.get("busy"): m["comment"] = True
    add_msg(c, m)
    if c["title"] == "Nowa rozmowa":
        c["title"] = text[:48]; save_chats(); broadcast({"type": "chats"})
    if c.get("busy") and P.inject(c["id"], mode_prefix(mode) + text): return m, "injected"
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
        return ("Set-Cookie", f"vb={tok}; HttpOnly; SameSite=Strict; Path={BASE or '/'}; Max-Age={30*86400}{sec}")

    def rpath(self):
        p = urlparse(self.path).path
        if BASE:
            if p == BASE: return None
            p = p[len(BASE):] if p.startswith(BASE + "/") else "/__none"
        return p

    def client_ip(self):
        ip = self.client_address[0]
        if ip in ("127.0.0.1", "::1"):   # za tunelem/proxy
            return self.headers.get("CF-Connecting-IP") or (self.headers.get("X-Forwarded-For") or ip).split(",")[0].strip()
        return ip

    def same_origin(self):
        o = self.headers.get("Origin")
        return not o or urlparse(o).netloc == self.headers.get("Host")

    def do_GET(self):
        u = urlparse(self.path); path = self.rpath()
        if path is None: return self.send(302, b"", extra=[("Location", BASE + "/")])
        if path == "/" and u.query.startswith("t=") and u.query[2:] == TOKEN and self.loopback() and not self.headers.get("X-Forwarded-For") and not self.headers.get("CF-Connecting-IP"):
            return self.send(302, b"", extra=[("Location", BASE + "/"), self.set_cookie(new_session())])
        if path == "/": return self.send(200, (HERE / "index.html").read_bytes(), "text/html")
        if path == "/manifest.json":
            return self.send(200, {"name": "KADM Studio", "short_name": "KADM", "start_url": BASE + "/", "scope": BASE + "/", "display": "standalone",
                                   "background_color": "#0e1116", "theme_color": "#0e1116",
                                   "icons": [{"src": BASE + "/icon.png", "sizes": "any", "type": "image/png"}]}, "application/manifest+json")
        if path == "/icon.png":
            f = HERE / "kadm.png"
            if f.exists(): return self.send(200, f.read_bytes(), "image/png")
            return self.send(404)
        if path == "/api/auth": return self.send(200, {"totp": bool(CFG.get("totp"))})
        if not self.authed(): return self.send(401, {"error": "login"})
        if path == "/events": return self.sse()
        if path == "/api/tts/voices":
            import importlib.util
            return self.send(200, {"available": bool(importlib.util.find_spec("edge_tts")), "voices": [
                {"id": "pl-PL-ZofiaNeural", "label": "Zofia (PL, kobieta)"}, {"id": "pl-PL-MarekNeural", "label": "Marek (PL, mężczyzna)"},
                {"id": "en-US-AriaNeural", "label": "Aria (EN)"}, {"id": "en-GB-RyanNeural", "label": "Ryan (EN-GB)"}]})
        if path == "/api/usage": return self.send(200, USAGE.view(P.merged(CFG)))
        if path == "/api/providers": return self.send(200, P.status(CFG, USAGE))
        if path == "/api/plugins":
            qs = parse_qs(urlparse(self.path).query); g = lambda k, d="": qs.get(k, [d])[0]
            try: lim = int(g("limit", "60"))
            except ValueError: lim = 60
            return self.send(200, plugins_view(g("q"), g("market"), lim))
        if path == "/api/github/search":
            qs = parse_qs(urlparse(self.path).query); g = lambda k, d="": qs.get(k, [d])[0]
            try: pg = int(g("page", "1"))
            except ValueError: pg = 1
            return self.send(200, gh_search(g("q"), pg, g("all") == "1"))
        if path == "/api/mcp": return self.send(200, mcp_view())
        if path == "/api/marketplaces": return self.send(200, market_view())
        if path == "/api/stats": return self.send(200, stats())
        if path == "/api/me": return self.send(200, {"ok": True, **settings()})
        if path == "/api/chats":
            with lock: return self.send(200, sorted((summary(c) for c in CHATS.values()), key=lambda x: -x["updated"]))
        if path.startswith("/api/chats/"):
            c = CHATS.get(path.split("/")[3])
            return self.send(200, {**summary(c), "messages": c["messages"], "live": c.get("_live")}) if c else self.send(404)
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
        path = self.rpath()
        if path is None: return self.send(404)
        if path == "/api/login":
            ip = self.client_ip(); now = time.time()
            fails[ip] = [t for t in fails.get(ip, []) if now - t < 60]
            if len(fails[ip]) >= 5: return self.send(429, {"error": "za dużo prób, odczekaj minutę"})
            if check_pw(str(d.get("password", ""))) and (not CFG.get("totp") or check_totp(str(d.get("code", "")))):
                return self.send(200, {"ok": True}, extra=[self.set_cookie(new_session())])
            fails[ip].append(now); time.sleep(0.5); return self.send(401, {"error": "złe hasło"})
        if not self.authed(): return self.send(401, {"error": "login"})
        if path == "/api/logout":
            with lock: SESS.pop(hashlib.sha256((self.cookie() or "").encode()).hexdigest(), None); jsave(SESS_F, SESS)
            return self.send(200, {"ok": True}, extra=[("Set-Cookie", f"vb=; Max-Age=0; Path={BASE or '/'}")])
        if path == "/api/tts":
            import importlib.util
            if not importlib.util.find_spec("edge_tts"): return self.send(501, {"error": "pip install edge-tts"})
            voice, text = str(d.get("voice", "")), str(d.get("text", ""))[:1200]
            if not re.match(r"^[a-z]{2,3}-[A-Z]{2}-\w{3,30}Neural$", voice) or not text.strip(): return self.send(400)
            try:
                import asyncio, edge_tts
                async def go():
                    out = b""
                    async for ch in edge_tts.Communicate(text, voice).stream():
                        if ch.get("type") == "audio": out += ch["data"]
                    return out
                audio = asyncio.run(go())
            except Exception as e:  # noqa
                return self.send(502, {"error": str(e)[:200]})
            return self.send(200, audio, "audio/mpeg")
        if path == "/api/github/token":
            t = str(d.get("token", "")).strip()
            if t and not re.match(r"^[\w-]{20,255}$", t): return self.send(400, {"error": "zły token"})
            if t: CFG["github_token"] = t
            else: CFG.pop("github_token", None)
            jsave(CFG_F, CFG); return self.send(200, {"ok": True, "token": bool(t)})
        if path == "/api/stopall":
            for c in CHATS.values():
                c.pop("pending", None)
                p = procs.get(c["id"])
                if p and p.poll() is None: p.kill()
                P.SESS.pop(c["id"], None)
            return self.send(200, {"ok": True})
        if path == "/api/providers/save":
            pid = d.get("id")
            if pid not in P.DEFAULTS: return self.send(400)
            cur = CFG.setdefault("providers", {}).setdefault(pid, {})
            for k, v in d.items():
                if k not in P.EDITABLE: continue
                if k in ("cmd", "args") and isinstance(v, str): v = shlex.split(v, posix=(os.name != "nt"))
                if k in ("cmd", "args") and not (isinstance(v, list) and all(isinstance(x, str) for x in v)): return self.send(400)
                if k in ("enabled", "free", "stdin"): v = bool(v)
                elif k == "wake_timeout": v = max(10, min(900, int(v or 150)))
                elif k not in ("cmd", "args"): v = str(v)[:300]
                cur[k] = v
            jsave(CFG_F, CFG); return self.send(200, P.status(CFG, USAGE))
        if path == "/api/providers/test":
            ok, msg = P.test(d.get("id"), CFG) if d.get("id") in P.DEFAULTS else (False, "?"); return self.send(200, {"ok": ok, "out": msg})
        if path == "/api/providers/reset-cooldown":
            CFG.pop("cooldown", None); jsave(CFG_F, CFG); return self.send(200, P.status(CFG, USAGE))
        if path == "/api/chain":
            ch = [x for x in d.get("chain", []) if x in P.DEFAULTS]
            CFG["chain"] = list(dict.fromkeys(ch)) or P.DEFAULT_CHAIN; jsave(CFG_F, CFG); return self.send(200, P.status(CFG, USAGE))
        if path == "/api/budget":
            def num(v, f=int):
                try: return max(0, f(float(v)))
                except (TypeError, ValueError): return 0
            CFG["budget"] = {"daily_tokens": num(d.get("daily_tokens")), "daily_usd": num(d.get("daily_usd"), float),
                             "per_provider": {k: num(v) for k, v in (d.get("per_provider") or {}).items() if k in P.DEFAULTS and num(v)}}
            jsave(CFG_F, CFG); return self.send(200, P.status(CFG, USAGE))
        if path == "/api/plugins/do":
            act, pid = d.get("action"), str(d.get("id", ""))
            if act not in ("install", "uninstall", "enable", "disable", "update") or not SAFE.match(pid): return self.send(400)
            rc, out = cli(["plugin", act, pid]); plugin_list(True)
            return self.send(200, {"ok": rc == 0, "out": out[-1500:]})
        if path == "/api/marketplaces/do":
            act, src = d.get("action"), str(d.get("source", ""))
            if act not in ("add", "remove", "update") or (act != "update" and not SAFE.match(src)) or (src and not SAFE.match(src)): return self.send(400)
            before = {a.get("marketplaceName") for a in plugin_list().get("available", [])}
            rc, out = cli(["plugin", "marketplace", act] + ([src] if src else []), 240)
            after = {a.get("marketplaceName") for a in plugin_list(True).get("available", [])}
            return self.send(200, {"ok": rc == 0, "out": out[-1500:], "added": sorted(x for x in after - before if x)})
        if path == "/api/mcp/do":
            act, name, url = d.get("action"), str(d.get("name", "")), str(d.get("url", ""))
            if act == "add" and SAFE.match(name) and re.match(r"^https://[^\s]{4,300}$", url):
                rc, out = cli(["mcp", "add", "--transport", "http", name, url])
            elif act == "remove" and SAFE.match(name) and not name.startswith("plugin:"):
                rc, out = cli(["mcp", "remove", name])
            else: return self.send(400)
            return self.send(200, {"ok": rc == 0, "out": out[-1500:]})
        if path == "/api/settings":
            for k in ("screen", "permission"):
                if k in d: CFG[k] = d[k] if k == "screen" else (d[k] if d[k] in ("default", "acceptEdits", "plan", "auto") else "acceptEdits")
            jsave(CFG_F, CFG); return self.send(200, settings())
        if path == "/api/chats":
            cwd = str(d.get("cwd") or ARGS.cwd)
            c = {"id": uuid.uuid4().hex[:10], "title": "Nowa rozmowa", "cwd": cwd, "session": None, "provider": "auto", "messages": [], "updated": time.time()}
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
            if act == "provider":
                v = d.get("provider", "auto")
                if v != "auto" and v not in P.DEFAULTS: return self.send(400)
                c["provider"] = v; save_chats(); broadcast({"type": "chats"}); return self.send(200, {"ok": True})
            if act == "stop":
                with lock: c.pop("pending", None)
                p = procs.get(c["id"])
                if p and p.poll() is None: p.kill()
                P.SESS.pop(c["id"], None)
                return self.send(200, {"ok": True})
            if act == "rename":
                c["title"] = str(d.get("title", ""))[:80] or c["title"]; save_chats(); broadcast({"type": "chats"}); return self.send(200, {"ok": True})
            if act == "delete":
                p = procs.get(c["id"])
                if p and p.poll() is None: p.kill()
                P.SESS.pop(c["id"], None)
                with lock: CHATS.pop(c["id"], None)
                save_chats(); broadcast({"type": "chats"}); return self.send(200, {"ok": True})
        self.send(404)


def main():
    global ARGS, BASE
    ap = argparse.ArgumentParser()
    ap.add_argument("--cwd", default=os.getcwd(), help="domyślny katalog roboczy nowych rozmów")
    ap.add_argument("--host", default="127.0.0.1"); ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--permission", default="acceptEdits")
    ap.add_argument("--allowed", nargs="*", default=["Bash", "Read", "Edit", "Write", "Glob", "Grep"])
    ap.add_argument("--screen", action="store_true", help="ekran + mysz/klawiatura (screen.py)")
    ap.add_argument("--open", action="store_true"); ap.add_argument("--set-password", action="store_true")
    ap.add_argument("--set-2fa", action="store_true", help="włącz kody z aplikacji uwierzytelniającej")
    ap.add_argument("--base", default="", help="prefiks ścieżki, np. /studio (gdy serwowane pod kadm.app/studio)")
    ARGS = ap.parse_args()
    BASE = "/" + ARGS.base.strip("/") if ARGS.base.strip("/") else ""
    for c in CHATS.values(): c["busy"] = False
    if ARGS.set_password:
        a, b = getpass.getpass("Nowe hasło (min. 8 znaków): "), getpass.getpass("Powtórz: ")
        if a != b or len(a) < 8: sys.exit("Hasła różne lub za krótkie.")
        CFG["pw"] = hash_pw(a); jsave(CFG_F, CFG); sys.exit("Hasło zapisane.")
    if ARGS.set_2fa:
        sec = base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")
        CFG["totp"] = sec; jsave(CFG_F, CFG)
        sys.exit(f"2FA włączone. Dodaj w aplikacji (Google Authenticator/Authy) -> wpis ręczny:\n  klucz: {sec}\n  lub URI: otpauth://totp/KADM%20Studio?secret={sec}&issuer=KADM")
    local = ARGS.host in ("127.0.0.1", "localhost", "::1")
    if not local and not CFG.get("pw"): sys.exit("Dostęp z sieci wymaga hasła: uruchom najpierw  python server.py --set-password")
    threading.Thread(target=P.reap, daemon=True).start()
    srv = ThreadingHTTPServer((ARGS.host, ARGS.port), H)
    url = f"http://127.0.0.1:{ARGS.port}{BASE}/"
    print(f"KADM Studio: {url}?t={TOKEN}  (automatyczne logowanie tylko z tego komputera)\nKatalog: {ARGS.cwd}  Dane: {DATA}")
    if not local: print(f"Nasłuch na {ARGS.host}:{ARGS.port} - zalogujesz się hasłem. Używaj HTTPS (Tailscale/Cloudflare Tunnel).")
    if ARGS.open:
        import webbrowser; threading.Timer(1, webbrowser.open, [f"{url}?t={TOKEN}"]).start()
    srv.serve_forever()


if __name__ == "__main__":
    main()

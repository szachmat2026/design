"""Dostawcy AI (Claude Code, Codex, Ollama/lokalne, własne CLI), zużycie tokenów, budżety i fallback."""
import json, os, re, shlex, shutil, socket, subprocess, threading, time, urllib.error, urllib.request
from datetime import date
from pathlib import Path

LIMIT_RE = re.compile(r"usage limit|rate.?limit|limit (reached|exceeded)|quota|credit balance|too many requests|\b429\b|overloaded|insufficient", re.I)
COOLDOWN = 30 * 60
HISTORY_MSGS, HISTORY_CHARS = 12, 4000

DEFAULTS = {
    "claude": {"type": "claude", "label": "Claude Code", "enabled": True, "free": False, "live": True},
    "codex": {"type": "codex", "label": "Codex", "enabled": True, "free": False, "args": ["--skip-git-repo-check"], "model": ""},
    "ollama": {"type": "ollama", "label": "Lokalne AI (Ollama)", "enabled": True, "free": True, "url": "http://127.0.0.1:11434",
               "model": "", "mac": "", "broadcast": "255.255.255.255", "wake_timeout": 150, "agent": True},
    "custom": {"type": "custom", "label": "Własne CLI", "enabled": False, "free": False, "cmd": [], "stdin": False},
}
EDITABLE = {"agent", "live", "enabled", "label", "free", "args", "model", "url", "mac", "broadcast", "wake_timeout", "cmd", "stdin"}
DEFAULT_CHAIN = ["claude", "codex", "ollama", "custom"]


class Killer:
    """Uchwyt do przerywania strumienia HTTP (zamiast procesu)."""
    def __init__(self): self.dead, self.resp = False, None
    def kill(self):
        self.dead = True
        try: self.resp.close()
        except Exception: pass
    def poll(self): return 0 if self.dead else None


# ---------------------------------------------------------------- zużycie
class Usage:
    def __init__(self, path):
        self.path, self.lock = Path(path), threading.Lock()
        try: self.rows = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception: self.rows = []

    def add(self, **r):
        r["ts"] = time.time(); r["day"] = date.today().isoformat()
        with self.lock:
            cut = time.time() - 90 * 86400
            self.rows = [x for x in self.rows if x["ts"] > cut] + [r]
            t = self.path.with_suffix(".tmp"); t.write_text(json.dumps(self.rows), encoding="utf-8"); t.replace(self.path)

    def _sel(self, day=None, provider=None):
        day = day or date.today().isoformat()
        return [r for r in self.rows if r["day"] == day and (provider is None or r["provider"] == provider)]

    def tokens(self, provider=None, free=None, provs=None, day=None):
        n = 0.0
        for r in self._sel(day, provider):
            if free is not None and bool(provs.get(r["provider"], {}).get("free")) != free: continue
            n += r.get("in", 0) + r.get("out", 0)
        return int(n)

    def usd(self, day=None):
        return round(sum(r.get("usd") or 0 for r in self._sel(day)), 4)

    def view(self, provs, days=14):
        today = date.today().isoformat(); series = {}
        for r in self.rows:
            d = series.setdefault(r["day"], {"day": r["day"], "tokens": 0, "usd": 0.0, "calls": 0})
            d["tokens"] += r.get("in", 0) + r.get("out", 0); d["usd"] += r.get("usd") or 0; d["calls"] += 1
        ser = []
        for i in range(days - 1, -1, -1):
            dd = date.fromordinal(date.today().toordinal() - i).isoformat()
            ser.append(series.get(dd, {"day": dd, "tokens": 0, "usd": 0.0, "calls": 0}))
        byp, byc = {}, {}
        for r in self._sel(today):
            p = byp.setdefault(r["provider"], {"tokens": 0, "usd": 0.0, "calls": 0})
            p["tokens"] += r.get("in", 0) + r.get("out", 0); p["usd"] += r.get("usd") or 0; p["calls"] += 1
        for r in self.rows:
            if r["day"] >= ser[0]["day"]:
                c = byc.setdefault(r.get("chat"), {"chat": r.get("chat"), "tokens": 0, "calls": 0})
                c["tokens"] += r.get("in", 0) + r.get("out", 0); c["calls"] += 1
        return {"today": {"tokens": self.tokens(), "paid_tokens": self.tokens(free=False, provs=provs), "usd": self.usd(), "by_provider": byp},
                "series": ser, "by_chat": sorted(byc.values(), key=lambda x: -x["tokens"])[:15]}


# ---------------------------------------------------------------- konfiguracja i wybór
def merged(cfg):
    out = {}
    for k, v in DEFAULTS.items():
        out[k] = {**v, **{a: b for a, b in (cfg.get("providers", {}).get(k) or {}).items() if a in EDITABLE}}
    return out


def available(pid, p):
    t = p["type"]
    if t == "claude": return bool(shutil.which("claude"))
    if t == "codex": return bool(shutil.which("codex"))
    if t == "custom": return bool(p.get("cmd"))
    return True   # ollama: sprawdzane przy użyciu (może być uśpiony)


def cooling(cfg, pid): return cfg.get("cooldown", {}).get(pid, 0) > time.time()


def under_budget(cfg, usage, provs, pid):
    if provs[pid].get("free"): return True
    b = cfg.get("budget", {})
    if b.get("daily_tokens") and usage.tokens(free=False, provs=provs) >= b["daily_tokens"]: return False
    if b.get("daily_usd") and usage.usd() >= b["daily_usd"]: return False
    pp = (b.get("per_provider") or {}).get(pid)
    if pp and usage.tokens(provider=pid) >= pp: return False
    return True


def order(cfg, usage, chat_provider):
    provs = merged(cfg)
    if chat_provider and chat_provider != "auto":
        return [chat_provider] if chat_provider in provs else []
    chain = [p for p in (cfg.get("chain") or DEFAULT_CHAIN) if p in provs]
    return [p for p in chain if provs[p]["enabled"] and available(p, provs[p]) and not cooling(cfg, p) and under_budget(cfg, usage, provs, p)]


def status(cfg, usage):
    provs, out = merged(cfg), []
    for pid, p in provs.items():
        out.append({"id": pid, **p, "installed": available(pid, p), "cooldown_until": cfg.get("cooldown", {}).get(pid, 0),
                    "over_budget": not under_budget(cfg, usage, provs, pid), "today_tokens": usage.tokens(provider=pid)})
    return {"providers": out, "chain": cfg.get("chain") or DEFAULT_CHAIN, "budget": cfg.get("budget", {})}


# ---------------------------------------------------------------- pomocnicze
def history_text(history):
    lines = []
    for m in history[-HISTORY_MSGS:]:
        lines.append(("Użytkownik: " if m["role"] == "user" else "Asystent: ") + str(m.get("text", ""))[:HISTORY_CHARS])
    return "\n".join(lines)


def full_prompt(text, history):
    h = history_text(history)
    return (f"Dotychczasowa rozmowa (kontekst):\n{h}\n\nNowa wiadomość użytkownika:\n{text}" if h else text)


def est(n_chars): return max(1, n_chars // 4)


def send_wol(mac, broadcast="255.255.255.255"):
    raw = bytes.fromhex(re.sub(r"[^0-9a-fA-F]", "", mac))
    if len(raw) != 6: raise ValueError("zły adres MAC")
    pkt = b"\xff" * 6 + raw * 16
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for port in (9, 7): s.sendto(pkt, (broadcast, port))


def ollama_tags(url, timeout=3):
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/api/tags", timeout=timeout) as r:
            return json.loads(r.read()).get("models", [])
    except Exception:
        return None


def ensure_ollama(p, note):
    tags = ollama_tags(p["url"])
    if tags is not None: return tags
    if p.get("mac"):
        note("WoL", "Budzę komputer z lokalnym AI…")
        try: send_wol(p["mac"], p.get("broadcast") or "255.255.255.255")
        except Exception as e: note("WoL", f"błąd: {e}"); return None
        end = time.time() + int(p.get("wake_timeout") or 150)
        while time.time() < end:
            time.sleep(5)
            tags = ollama_tags(p["url"])
            if tags is not None: note("WoL", "Komputer działa."); return tags
    return None


# ---------------------------------------------------------------- adaptery
def run_claude_dispatch(p, ctx):
    return run_claude_live(p, ctx) if p.get("live", True) and ctx.get("chat_id") else run_claude(p, ctx)


def run_claude(p, ctx):
    cmd = [shutil.which("claude") or "claude", "-p", ctx["text"], "--output-format", "stream-json", "--verbose",
           "--append-system-prompt", ctx["sysm"], "--permission-mode", ctx["permission"]]
    if ctx["allowed"]: cmd += ["--allowedTools", *ctx["allowed"]]
    if ctx.get("session"): cmd += ["--resume", ctx["session"]]
    res = {"reply": None, "error": None, "usage": {}, "session": None}
    pr = subprocess.Popen(cmd, cwd=ctx["cwd"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    ctx["register"](pr); raw = ""
    for line in pr.stdout:
        try: ev = json.loads(line)
        except ValueError:
            raw += line; continue
        if ev.get("session_id"): res["session"] = ev["session_id"]
        t = ev.get("type")
        if t == "assistant":
            for b in ev.get("message", {}).get("content", []):
                if b.get("type") == "tool_use":
                    i = b.get("input", {})
                    ctx["tool"](b.get("name"), str(i.get("command") or i.get("file_path") or i.get("description") or i.get("pattern") or "")[:300])
        elif t == "result":
            res["reply"] = ev.get("result") or ""
            u = ev.get("usage") or {}
            res["usage"] = {"in": u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0), "out": u.get("output_tokens", 0),
                            "cache": u.get("cache_read_input_tokens", 0), "usd": ev.get("total_cost_usd")}
            if ev.get("is_error"): res["error"] = "limit" if LIMIT_RE.search(res["reply"] or "") else "error"
    rc = pr.wait()
    if res["reply"] is None:
        res["reply"] = raw.strip() or None
        res["error"] = "limit" if LIMIT_RE.search(raw) else ("error" if rc else None)
    if rc and rc > 0 and not res["error"]: res["error"] = "error"
    return res


def run_codex(p, ctx):
    exe = shutil.which("codex")
    if not exe: return {"reply": "Nie znaleziono polecenia `codex` (npm i -g @openai/codex, potem `codex login`).", "error": "error", "usage": {}}
    cmd = [exe, "exec", *list(p.get("args") or [])]
    if p.get("model"): cmd += ["-m", p["model"]]
    cmd.append("-")
    prompt = full_prompt(ctx["text"], ctx["history"])
    pr = subprocess.Popen(cmd, cwd=ctx["cwd"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
    ctx["register"](pr)
    out, err = pr.communicate(prompt, timeout=3600)
    reply = (out or "").strip()
    bad = pr.returncode != 0 or not reply
    return {"reply": reply or (err or "").strip()[-800:] or None, "usage": {"in": est(len(prompt)), "out": est(len(reply)), "est": True},
            "error": ("limit" if LIMIT_RE.search(err or out or "") else "error") if bad else None}


# ---------------------------------------------------------------- lokalne AI jako agent (narzędzia)
OTOOLS = {
    "run_command": ("Bash", {"type": "function", "function": {"name": "run_command", "description": "Uruchom polecenie w powłoce na komputerze użytkownika (w katalogu roboczym).",
                    "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}}),
    "read_file": ("Read", {"type": "function", "function": {"name": "read_file", "description": "Przeczytaj plik tekstowy.",
                  "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}),
    "list_dir": ("Glob", {"type": "function", "function": {"name": "list_dir", "description": "Wylistuj zawartość katalogu.",
                 "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}),
    "write_file": ("Write", {"type": "function", "function": {"name": "write_file", "description": "Zapisz (utwórz lub nadpisz) plik tekstowy.",
                   "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}}),
}


def agent_tools(allowed):
    return [t for n, (perm, t) in OTOOLS.items() if not allowed or perm in allowed]


def agent_exec(name, a, cwd):
    base = Path(cwd) if cwd else Path.cwd()
    try:
        if name == "run_command":
            r = subprocess.run(str(a.get("command", "")), shell=True, cwd=str(base), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
            return (f"[kod {r.returncode}]\n" + (r.stdout or "") + (r.stderr or ""))[-4000:]
        path = Path(str(a.get("path", ".")))
        path = path if path.is_absolute() else base / path
        if name == "read_file": return path.read_text(encoding="utf-8", errors="replace")[:6000]
        if name == "list_dir": return "\n".join(sorted(x.name + ("/" if x.is_dir() else "") for x in path.iterdir()))[:4000]
        if name == "write_file":
            path.parent.mkdir(parents=True, exist_ok=True); path.write_text(str(a.get("content", "")), encoding="utf-8")
            return f"Zapisano {path} ({len(str(a.get('content', '')))} znaków)"
    except subprocess.TimeoutExpired:
        return "Przekroczono czas polecenia (120 s)."
    except Exception as e:  # noqa
        return f"Błąd: {e}"
    return "Nieznane narzędzie."


def run_ollama(p, ctx):
    tags = ensure_ollama(p, ctx["tool"])
    if tags is None: return {"reply": "Lokalne AI (Ollama) jest nieosiągalne pod " + p["url"], "error": "error", "usage": {}}
    model = p.get("model") or (tags[0]["name"] if tags else "")
    if not model: return {"reply": "Ollama nie ma żadnego modelu (np. `ollama pull qwen2.5-coder`).", "error": "error", "usage": {}}
    tools = agent_tools(ctx.get("allowed")) if p.get("agent", True) else []
    sysm = ctx["sysm_agent"] if tools else ctx["sysm_plain"]
    msgs = [{"role": "system", "content": sysm}] + [{"role": m["role"], "content": str(m.get("text", ""))[:HISTORY_CHARS]} for m in ctx["history"][-HISTORY_MSGS:]] + [{"role": "user", "content": ctx["text"]}]
    k = Killer(); ctx["register"](k); delta = ctx.get("delta") or (lambda t: None)
    ctx["tool"]("ollama", model + (" · agent" if tools else " · czat"))
    tin = tout = 0; reply = ""
    for step in range(int(p.get("max_steps") or 15)):
        body = {"model": model, "messages": msgs, "stream": True}
        if tools: body["tools"] = tools
        req = urllib.request.Request(p["url"].rstrip("/") + "/api/chat", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        content, calls = "", []
        try:
            k.resp = urllib.request.urlopen(req, timeout=600)
            for line in k.resp:
                if k.dead: break
                d = json.loads(line); m = d.get("message", {})
                if m.get("content"): content += m["content"]; delta(m["content"])
                calls += m.get("tool_calls") or []
                if d.get("done"): tin += d.get("prompt_eval_count", 0); tout += d.get("eval_count", 0)
        except urllib.error.HTTPError as e:
            if e.code == 400 and tools and "tool" in (e.read() or b"").decode("utf-8", "replace").lower():
                tools = []; msgs[0]["content"] = ctx["sysm_plain"]; ctx["tool"]("ollama", "model nie wspiera narzędzi, tryb czatu"); continue
            return {"reply": f"Błąd Ollama: {e}", "error": "error", "usage": {}}
        except Exception as e:
            if not k.dead: return {"reply": f"Błąd Ollama: {e}", "error": "error", "usage": {}}
        if k.dead: break
        reply = content
        if not calls: break
        msgs.append({"role": "assistant", "content": content, "tool_calls": calls})
        if content: delta("\n\n")
        for c in calls:
            fn = c.get("function", {}); name, args = fn.get("name"), fn.get("arguments") or {}
            if isinstance(args, str):
                try: args = json.loads(args)
                except ValueError: args = {}
            allowed_names = {t["function"]["name"] for t in tools}
            detail = (args.get("path", "") + "\n" + str(args.get("content", ""))[:2500]) if name == "write_file" else str(args.get("command") or args.get("path") or "")
            ctx["tool"](OTOOLS.get(name, ("?",))[0] if name in OTOOLS else str(name), str(args.get("command") or args.get("path") or "")[:300], detail)
            res = agent_exec(name, args, ctx["cwd"]) if name in allowed_names else "Narzędzie niedozwolone."
            (ctx.get("toolres") or (lambda t: None))(res[:400])
            msgs.append({"role": "tool", "content": res})
    return {"reply": reply.strip() or None, "usage": {"in": tin or est(len(ctx["text"])), "out": tout or est(len(reply)), "est": not tin}, "error": None, "model": model}


def run_custom(p, ctx):
    cmd = [str(a) for a in (p.get("cmd") or [])]
    if not cmd: return {"reply": "Własne CLI nie jest skonfigurowane.", "error": "error", "usage": {}}
    prompt = full_prompt(ctx["text"], ctx["history"]); use_stdin = bool(p.get("stdin"))
    if any("{prompt}" in a for a in cmd): cmd = [a.replace("{prompt}", prompt) for a in cmd]
    elif not use_stdin: cmd.append(prompt)
    exe = shutil.which(cmd[0])
    if not exe: return {"reply": f"Nie znaleziono polecenia `{cmd[0]}`.", "error": "error", "usage": {}}
    cmd[0] = exe
    pr = subprocess.Popen(cmd, cwd=ctx["cwd"], stdin=subprocess.PIPE if use_stdin else subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
    ctx["register"](pr)
    out, err = pr.communicate(prompt if use_stdin else None, timeout=3600)
    reply = (out or "").strip(); bad = pr.returncode != 0 or not reply
    return {"reply": reply or (err or "").strip()[-800:] or None, "usage": {"in": est(len(prompt)), "out": est(len(reply)), "est": True},
            "error": ("limit" if LIMIT_RE.search(err or out or "") else "error") if bad else None}



# ---------------------------------------------------------------- Claude na żywo (proces trwały, strumień)
SESS, SLOCK = {}, threading.Lock()
GRACE = 3.0   # po komentarzu wstrzykniętym w trakcie pracy czekamy na ewentualną kolejną turę


class Live:
    def __init__(self, key, cmd, cwd, sig):
        self.key, self.sig, self.sink = key, sig, None
        self.session, self.raw, self.events = None, "", 0
        self.injected, self.active, self.last_event, self.last_used = 0, False, time.time(), time.time()
        self.lock = threading.Lock()
        self.p = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  text=True, encoding="utf-8", errors="replace", bufsize=1)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.p.stdout:
            try: ev = json.loads(line)
            except ValueError:
                self.raw += line; continue
            if ev.get("session_id"): self.session = ev["session_id"]
            self.events += 1; self.last_event = time.time()
            s = self.sink
            if s:
                try: s(ev)
                except Exception: pass

    def alive(self): return self.p.poll() is None
    def poll(self): return self.p.poll()

    def kill(self):
        try: self.p.kill()
        except Exception: pass

    def send(self, text):
        with self.lock:
            self.p.stdin.write(json.dumps({"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}}, ensure_ascii=False) + "\n")
            self.p.stdin.flush(); self.last_used = time.time()


def inject(key, text):
    """Komentarz użytkownika w trakcie pracy: trafia do tego samego procesu. True, jeśli przyjęty."""
    s = SESS.get(key)
    if s and s.alive() and s.active:
        try: s.send(text); s.injected += 1; return True
        except Exception: return False
    return False


def reap(max_idle=1800):
    while True:
        time.sleep(60)
        for k, s in list(SESS.items()):
            if not s.active and (not s.alive() or time.time() - s.last_used > max_idle):
                s.kill(); SESS.pop(k, None)


def tool_detail(name, i):
    if name == "Write": return (i.get("file_path", "") + "\n" + str(i.get("content", ""))[:2500])
    if name == "Edit": return (i.get("file_path", "") + "\n- " + str(i.get("old_string", ""))[:800] + "\n+ " + str(i.get("new_string", ""))[:1200])
    if name == "Bash": return str(i.get("command", ""))[:800]
    return json.dumps(i, ensure_ascii=False)[:400]


def tool_short(name, i):
    return str(i.get("file_path") or i.get("command") or i.get("description") or i.get("pattern") or i.get("url") or "")[:300]


def run_claude_live(p, ctx):
    sig = (ctx["permission"], tuple(ctx["allowed"] or []), ctx["cwd"], ctx["sysm_live"])
    with SLOCK:
        s = SESS.get(ctx["chat_id"])
        if s and (not s.alive() or s.sig != sig): s.kill(); s = None
        if not s:
            cmd = [shutil.which("claude") or "claude", "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
                   "--include-partial-messages", "--append-system-prompt", ctx["sysm_live"], "--permission-mode", ctx["permission"]]
            if ctx["allowed"]: cmd += ["--allowedTools", *ctx["allowed"]]
            if ctx.get("session"): cmd += ["--resume", ctx["session"]]
            s = Live(ctx["chat_id"], cmd, ctx["cwd"], sig); SESS[ctx["chat_id"]] = s
    turns, cur = [], {"tools": [], "text": ""}
    state = {"done": False}

    def sink(ev):
        t = ev.get("type")
        if t == "stream_event":
            e = ev.get("event", {}); et = e.get("type")
            if et == "content_block_start" and e.get("content_block", {}).get("type") == "text" and cur["text"]:
                ctx["delta"]("\n\n")
            elif et == "content_block_delta" and e.get("delta", {}).get("type") == "text_delta":
                cur["text"] += e["delta"].get("text", ""); ctx["delta"](e["delta"].get("text", ""))
        elif t == "assistant":
            for b in ev.get("message", {}).get("content", []):
                if b.get("type") == "tool_use":
                    i = b.get("input", {}); d = {"name": b.get("name"), "text": tool_short(b.get("name"), i), "detail": tool_detail(b.get("name"), i)}
                    cur["tools"].append(d); ctx["tool"](d["name"], d["text"], d["detail"])
                elif b.get("type") == "text" and not cur["text"]:
                    cur["text"] = b.get("text", "")
        elif t == "user":
            for b in (ev.get("message", {}).get("content") or []):
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    c = b.get("content"); c = " ".join(x.get("text", "") for x in c if isinstance(x, dict)) if isinstance(c, list) else str(c or "")
                    if cur["tools"]: cur["tools"][-1]["result"] = c[:400]
                    ctx["toolres"](c[:400])
        elif t == "result":
            u = ev.get("usage") or {}
            reply = ev.get("result") or cur["text"] or ""
            err = None
            if ev.get("is_error"): err = "limit" if LIMIT_RE.search(reply) else "error"
            turns.append({"reply": reply, "tools": cur["tools"], "error": err,
                          "usage": {"in": u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0), "out": u.get("output_tokens", 0),
                                    "cache": u.get("cache_read_input_tokens", 0), "usd": ev.get("total_cost_usd")}})
            cur["tools"], cur["text"] = [], ""
            ctx["turn_end"]()

    s.sink, s.active, s.injected, s.events_at_start = sink, True, 0, s.events
    ctx["register"](s)
    try:
        s.send(ctx.get("text_live") or ctx["text"])
        start = time.time()
        while True:
            time.sleep(0.1)
            if not s.alive(): break
            if turns and (s.injected == 0 or time.time() - s.last_event > GRACE): break
            if not turns and time.time() - start > 3600: break
    finally:
        s.active, s.sink = False, None
    if not turns:
        raw = (s.raw or "").strip()
        if s.events - s.events_at_start <= 0 or not raw == "":
            s.kill(); SESS.pop(ctx["chat_id"], None)
            if s.events - s.events_at_start <= 0 and not LIMIT_RE.search(raw):
                return run_claude(p, ctx)     # tryb strumieniowy nie ruszył: zwykłe jednorazowe wywołanie
        return {"reply": raw or "(przerwano)", "error": "limit" if LIMIT_RE.search(raw) else "error", "usage": {}, "session": s.session, "turns": []}
    last = turns[-1]
    return {"reply": last["reply"], "error": last["error"], "usage": last["usage"], "session": s.session, "turns": turns}


RUNNERS = {"claude": run_claude_dispatch, "codex": run_codex, "ollama": run_ollama, "custom": run_custom}


def run(pid, cfg, ctx):
    p = merged(cfg)[pid]
    try:
        r = RUNNERS[p["type"]](p, ctx)
    except FileNotFoundError:
        r = {"reply": f"Nie znaleziono programu dla „{p['label']}”.", "error": "error", "usage": {}}
    except Exception as e:  # noqa
        r = {"reply": f"Błąd dostawcy {pid}: {e}", "error": "error", "usage": {}}
    r["provider"] = pid
    return r


def test(pid, cfg):
    p = merged(cfg)[pid]
    try:
        if p["type"] == "ollama":
            t = ollama_tags(p["url"], 4)
            return (False, "Brak połączenia z " + p["url"] + (" (ustaw MAC, by budzić komputer)" if not p.get("mac") else " (wyślę Wake-on-LAN przy użyciu)")) if t is None else (True, "Modele: " + (", ".join(m["name"] for m in t) or "brak"))
        exe = shutil.which({"claude": "claude", "codex": "codex"}.get(p["type"], (p.get("cmd") or [""])[0] or "x"))
        if not exe: return False, "Nie znaleziono programu w PATH."
        r = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=20)
        return r.returncode == 0, (r.stdout or r.stderr).strip()[:200]
    except Exception as e:
        return False, str(e)

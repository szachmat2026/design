"""Dostawcy AI (Claude Code, Codex, Ollama/lokalne, własne CLI), zużycie tokenów, budżety i fallback."""
import json, re, shlex, shutil, socket, subprocess, threading, time, urllib.request
from datetime import date
from pathlib import Path

LIMIT_RE = re.compile(r"usage limit|rate.?limit|limit (reached|exceeded)|quota|credit balance|too many requests|\b429\b|overloaded|insufficient", re.I)
COOLDOWN = 30 * 60
HISTORY_MSGS, HISTORY_CHARS = 12, 4000

DEFAULTS = {
    "claude": {"type": "claude", "label": "Claude Code", "enabled": True, "free": False},
    "codex": {"type": "codex", "label": "Codex", "enabled": True, "free": False, "args": ["--skip-git-repo-check"], "model": ""},
    "ollama": {"type": "ollama", "label": "Lokalne AI (Ollama)", "enabled": True, "free": True, "url": "http://127.0.0.1:11434",
               "model": "", "mac": "", "broadcast": "255.255.255.255", "wake_timeout": 150},
    "custom": {"type": "custom", "label": "Własne CLI", "enabled": False, "free": False, "cmd": [], "stdin": False},
}
EDITABLE = {"enabled", "label", "free", "args", "model", "url", "mac", "broadcast", "wake_timeout", "cmd", "stdin"}
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


def run_ollama(p, ctx):
    tags = ensure_ollama(p, ctx["tool"])
    if tags is None: return {"reply": "Lokalne AI (Ollama) jest nieosiągalne pod " + p["url"], "error": "error", "usage": {}}
    model = p.get("model") or (tags[0]["name"] if tags else "")
    if not model: return {"reply": "Ollama nie ma żadnego modelu (np. `ollama pull llama3.2`).", "error": "error", "usage": {}}
    msgs = [{"role": "system", "content": ctx["sysm_plain"]}] + [{"role": m["role"], "content": str(m.get("text", ""))[:HISTORY_CHARS]} for m in ctx["history"][-HISTORY_MSGS:]] + [{"role": "user", "content": ctx["text"]}]
    req = urllib.request.Request(p["url"].rstrip("/") + "/api/chat", data=json.dumps({"model": model, "messages": msgs, "stream": True}).encode(), headers={"Content-Type": "application/json"})
    k = Killer(); ctx["register"](k); reply, u = "", {}
    ctx["tool"]("ollama", model)
    try:
        k.resp = urllib.request.urlopen(req, timeout=600)
        for line in k.resp:
            if k.dead: break
            d = json.loads(line)
            reply += d.get("message", {}).get("content", "")
            if d.get("done"): u = {"in": d.get("prompt_eval_count", 0), "out": d.get("eval_count", 0)}
    except Exception as e:
        if not k.dead: return {"reply": f"Błąd Ollama: {e}", "error": "error", "usage": {}}
    return {"reply": reply.strip() or None, "usage": u or {"in": est(len(ctx["text"])), "out": est(len(reply)), "est": True}, "error": None, "model": model}


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


RUNNERS = {"claude": run_claude, "codex": run_codex, "ollama": run_ollama, "custom": run_custom}


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

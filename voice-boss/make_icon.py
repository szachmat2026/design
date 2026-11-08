#!/usr/bin/env python3
"""Pobiera logo ze strony i zapisuje jako kadm.ico.  python make_icon.py [URL] [plik_wyjsciowy]
Jeśli pobranie się nie uda, wrzuć własny obraz jako voice-boss/kadm.png i uruchom ponownie."""
import io, re, sys, urllib.request
from pathlib import Path
from urllib.parse import urljoin
from PIL import Image

HERE = Path(__file__).parent
URL = sys.argv[1] if len(sys.argv) > 1 else "https://kadm.app"
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else HERE / "kadm.ico"
UA = {"User-Agent": "Mozilla/5.0"}


def get(u):
    return urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=20).read()


def candidates(html, base):
    out = []
    for tag in re.findall(r"<link[^>]+>", html, re.I):
        if re.search(r'rel=["\'][^"\']*icon', tag, re.I):
            m = re.search(r'href=["\']([^"\']+)', tag, re.I)
            if m:
                sz = re.search(r'sizes=["\'](\d+)', tag, re.I)
                score = int(sz.group(1)) if sz else (180 if "apple" in tag.lower() else 32)
                out.append((score, urljoin(base, m.group(1))))
    m = re.search(r'<meta[^>]+og:image[^>]+content=["\']([^"\']+)', html, re.I)
    if m:
        out.append((100, urljoin(base, m.group(1))))
    out.append((16, urljoin(base, "/favicon.ico")))
    return [u for _, u in sorted(out, key=lambda x: -x[0])]


def main():
    img = None
    local = HERE / "kadm.png"
    if local.exists():
        img = Image.open(local)
    else:
        try:
            html = get(URL).decode("utf-8", "replace")
        except Exception as e:
            sys.exit(f"Nie mogę pobrać {URL}: {e}\nWrzuć logo jako {local} i uruchom ponownie.")
        for u in candidates(html, URL):
            try:
                if u.lower().endswith(".svg"):
                    continue
                img = Image.open(io.BytesIO(get(u))); print("Logo:", u); break
            except Exception:
                continue
    if img is None:
        sys.exit(f"Nie znalazłem logo. Wrzuć je jako {local}.")
    img = img.convert("RGBA")
    side = max(img.size)
    sq = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    sq.paste(img, ((side - img.width) // 2, (side - img.height) // 2))
    sq.save(OUT, sizes=[(s, s) for s in (16, 32, 48, 64, 128, 256)])
    print("Zapisano", OUT)


if __name__ == "__main__":
    main()

"""
Preuzima utakmice sa ksb.rs i upisuje ih u utakmice.json.
Pokreće se automatski preko GitHub Actions (vidi .github/workflows/osvezi.yml).

Ako želiš da dodaš još stranica sa kojih se čitaju utakmice
(npr. stranicu takmičenja ili utakmice koje se doigravaju),
dodaj njihove adrese u listu SOURCES.
"""
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

SOURCES = [
    "https://www.ksb.rs/",
    # "https://www.ksb.rs/takmicenje1209/",   # primer: 1MRL Centar
]

OUT = Path(__file__).resolve().parent.parent / "utakmice.json"
MAX_MATCHES = 5000
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "sr,en;q=0.8",
}

LINK_RE = re.compile(r"/utakmica/?(\d+)/?$")
DATE_RE = re.compile(r"\((\d+)\)\s*(\d{2})\.(\d{2})\.(\d{4})")


def parse(html):
    """Vraća listu utakmica pronađenih u HTML-u jedne stranice."""
    soup = BeautifulSoup(html, "html.parser")
    groups, order = {}, []
    for a in soup.find_all("a", href=True):
        m = LINK_RE.search(a["href"])
        if not m:
            continue
        text = " ".join(a.get_text(" ", strip=True).split())
        if not text:
            continue
        mid = m.group(1)
        if mid not in groups:
            groups[mid] = []
            order.append(mid)
        groups[mid].append(text)

    matches = []
    for mid in order:
        texts = groups[mid]
        di = next((i for i, t in enumerate(texts) if DATE_RE.search(t)), None)
        if di is None:
            continue
        d = DATE_RE.search(texts[di])
        rest = texts[di + 1:]
        if len(rest) < 3:
            continue
        competition = rest[0]
        parts = rest[1:]
        item = {
            "id": mid,
            "date": f"{d.group(4)}-{d.group(3)}-{d.group(2)}",
            "competition": competition,
            "home": parts[0],
            "away": None,
            "home_score": None,
            "away_score": None,
            "url": f"https://www.ksb.rs/utakmica{mid}/",
        }
        if len(parts) >= 4:      # ekipa1, rezultat1, ekipa2, rezultat2
            item["home_score"], item["away"], item["away_score"] = parts[1], parts[2], parts[3]
        elif len(parts) >= 2:    # nema rezultata: ekipa1, ekipa2
            item["away"] = parts[1]
        if not item["away"]:
            continue
        matches.append(item)
    return matches


def main():
    old = {}
    if OUT.exists():
        try:
            for m in json.loads(OUT.read_text(encoding="utf-8")).get("matches", []):
                old[m["id"]] = m
        except Exception as e:  # pokvarena datoteka, krećemo ispočetka
            print("Upozorenje: ne mogu da pročitam staru datoteku:", e)

    found, errors = {}, 0
    for url in SOURCES:
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
            r.encoding = r.apparent_encoding or "utf-8"
            items = parse(r.text)
            print(f"{url}: pronađeno {len(items)} utakmica")
            for m in items:
                found.setdefault(m["id"], m)
        except Exception as e:
            errors += 1
            print(f"GREŠKA kod {url}: {e}")

    if errors == len(SOURCES):
        print("Nijedna stranica nije preuzeta. Stari podaci ostaju netaknuti.")
        sys.exit(1)

    old.update(found)  # nove vrednosti prepisuju stare (npr. kad stigne rezultat)
    merged = sorted(old.values(), key=lambda m: (m["date"], m["id"]), reverse=True)[:MAX_MATCHES]
    payload = {
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "matches": merged,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Sačuvano ukupno {len(merged)} utakmica.")


if __name__ == "__main__":
    main()

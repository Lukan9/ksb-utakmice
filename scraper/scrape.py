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
import time
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse
from pathlib import Path

import requests
from bs4 import BeautifulSoup

SOURCES = [
    "https://www.ksb.rs/",
    # dodatne stranice možeš ručno dodati ovde, npr.
    # "https://www.ksb.rs/takmicenje1209/",
]

# Skripta sama pronalazi stranice takmičenja (linkovi /takmicenjeNNNN/ i
# /kup-takmicenjeNNN/ sa početne stranice) - tamo su i utakmice koje tek slede.
DISCOVER = True
MAX_PAGES = 250      # sigurnosna granica broja stranica po pokretanju
DELAY = 0.4          # pauza između zahteva (sekunde), da ne opterećujemo sajt

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
ANY_DATE_RE = re.compile(r"(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})")
TIME_RE = re.compile(r"\b([01]?\d|2[0-3])[:.]([0-5]\d)\b(?!\.\d)")
COMP_LINK_RE = re.compile(r"/(?:kup-)?takmicenje\d+")


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


def parse_generic(html):
    """Rezervni parser za stranice takmičenja (utakmice koje tek slede).

    Za svaku utakmicu traži najmanji blok (red tabele, kartica...) koji sadrži
    samo nju, pa iz teksta tog bloka čita datum, vreme, ekipe i rezultat.
    """
    soup = BeautifulSoup(html, "html.parser")
    page_comp = ""
    h = soup.find(["h1", "h2"])
    if h:
        page_comp = " ".join(h.get_text(" ", strip=True).split())

    anchors = {}
    for a in soup.find_all("a", href=True):
        m = LINK_RE.search(a["href"])
        if m:
            anchors.setdefault(m.group(1), []).append(a)

    def ids_in(node):
        found = set()
        for a in node.find_all("a", href=True):
            m = LINK_RE.search(a["href"])
            if m:
                found.add(m.group(1))
        return found

    out = []
    for mid, links in anchors.items():
        node = links[0]
        best = None
        for _ in range(6):
            parent = node.parent
            if parent is None or parent.name in ("body", "html", "[document]"):
                break
            if ids_in(parent) - {mid}:
                break
            best = parent
            node = parent
        if best is None:
            continue
        strings = [" ".join(t.split()) for t in best.stripped_strings]
        strings = [t for t in strings if t]
        blob = " | ".join(strings)

        d = ANY_DATE_RE.search(blob)
        if not d:
            continue
        day, mon, year = int(d.group(1)), int(d.group(2)), int(d.group(3))
        if not (2020 <= year <= 2035 and 1 <= mon <= 12 and 1 <= day <= 31):
            continue
        rest_blob = blob.replace(d.group(0), " ")
        tm = TIME_RE.search(rest_blob)
        tstr = f"{int(tm.group(1)):02d}:{tm.group(2)}" if tm else None

        teams, scores, comp = [], [], ""
        for t in strings:
            if ANY_DATE_RE.search(t) or DATE_RE.search(t):
                continue
            if tstr and TIME_RE.fullmatch(t.strip()):
                continue
            if re.fullmatch(r"\d{1,3}", t):
                scores.append(t)
            elif re.fullmatch(r"[-:–—vs.\s]+", t) or t.lower() in ("vs", "-", ":"):
                continue
            elif "/" in t and not comp:
                comp = t
            elif re.fullmatch(r"\(?\d{4,7}\)?", t):
                continue
            elif t not in teams:
                teams.append(t)
        if len(teams) < 2:
            continue
        item = {
            "id": mid,
            "date": f"{year:04d}-{mon:02d}-{day:02d}",
            "competition": comp or page_comp,
            "home": teams[0],
            "away": teams[1],
            "home_score": scores[0] if len(scores) >= 2 else None,
            "away_score": scores[1] if len(scores) >= 2 else None,
            "url": f"https://www.ksb.rs/utakmica{mid}/",
        }
        if tstr:
            item["time"] = tstr
        out.append(item)
    return out


def parse_all(html):
    """Prvo poznati format sa početne, pa rezervni parser za ostalo."""
    items = {m["id"]: m for m in parse(html)}
    for m in parse_generic(html):
        if m["id"] not in items:
            items[m["id"]] = m
        elif m.get("time") and not items[m["id"]].get("time"):
            items[m["id"]]["time"] = m["time"]
    return list(items.values())


def competition_links(html, base):
    soup = BeautifulSoup(html, "html.parser")
    urls = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "#" == href[:1] or LINK_RE.search(href):
            continue
        if COMP_LINK_RE.search(href):
            full = urljoin(base, href).split("#")[0]
            if urlparse(full).netloc.endswith("ksb.rs") and full not in urls:
                urls.append(full)
    return urls


def main():
    old = {}
    if OUT.exists():
        try:
            for m in json.loads(OUT.read_text(encoding="utf-8")).get("matches", []):
                old[m["id"]] = m
        except Exception as e:  # pokvarena datoteka, krećemo ispočetka
            print("Upozorenje: ne mogu da pročitam staru datoteku:", e)

    found, errors, fetched = {}, 0, 0
    queue, seen = list(SOURCES), set(SOURCES)
    i = 0
    while i < len(queue) and i < MAX_PAGES:
        url = queue[i]
        i += 1
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
            r.encoding = r.apparent_encoding or "utf-8"
            fetched += 1
            items = parse_all(r.text)
            upcoming = sum(1 for m in items if not m["home_score"])
            print(f"{url}: {len(items)} utakmica ({upcoming} bez rezultata)")
            for m in items:
                prev = found.get(m["id"])
                # utakmica sa rezultatom ima prednost nad onom bez njega
                if prev is None or (not prev["home_score"] and m["home_score"]):
                    found[m["id"]] = m
            if DISCOVER:
                for link in competition_links(r.text, url):
                    if link not in seen:
                        seen.add(link)
                        queue.append(link)
        except Exception as e:
            errors += 1
            print(f"GREŠKA kod {url}: {e}")
        time.sleep(DELAY)

    print(f"Preuzeto stranica: {fetched}, grešaka: {errors}, u redu ostalo: {max(0, len(queue) - i)}")
    if fetched == 0:
        print("Nijedna stranica nije preuzeta. Stari podaci ostaju netaknuti.")
        sys.exit(1)

    for mid, m in found.items():
        prev = old.get(mid)
        # ne briši već upisan rezultat ako ga nova stranica ne prikazuje
        if prev and prev.get("home_score") and not m.get("home_score"):
            continue
        old[mid] = m   # nove vrednosti prepisuju stare (npr. kad stigne rezultat ili novi termin)
    merged = sorted(old.values(), key=lambda m: (m["date"], m["id"]), reverse=True)[:MAX_MATCHES]
    payload = {
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "matches": merged,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Sačuvano ukupno {len(merged)} utakmica.")


if __name__ == "__main__":
    main()

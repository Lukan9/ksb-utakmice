import json
import re
import sys
import time

from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


# ============================================================
# PODESAVANJA
# ============================================================

SOURCES = [
    "https://www.ksb.rs/",
    "https://www.ksb.rs/takmicenje1209/",
]

DISCOVER = True

MAX_PAGES = 250
DELAY = 0.5
TIMEOUT = 20
MAX_MATCHES = 5000

OUT = Path(__file__).resolve().parent.parent / "utakmice.json"
DEBUG_DIR = Path(__file__).resolve().parent / "debug"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "sr,en;q=0.9",
}

LINK_RE = re.compile(r"/utakmica/?(\d+)/?$")
COMP_LINK_RE = re.compile(r"/(?:kup-)?takmicenje\d+/?")

# "(12) 29.11.2026" ili "(12) 29.11.2026 18:00"
DATE_RE = re.compile(r"\((\d+)\)\s*(\d{1,2})\.(\d{1,2})\.(\d{4})")

ANY_DATE_RE = re.compile(r"(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})")

TIME_RE = re.compile(r"(?<!\d)([01]?\d|2[0-3])[:.]([0-5]\d)(?!\d)")
TIME_FULL_RE = re.compile(r"([01]?\d|2[0-3])[:.]([0-5]\d)")

SCORE_PAIR_RE = re.compile(r"(\d{1,3})\s*[:\-]\s*(\d{1,3})")
DIGITS_RE = re.compile(r"\d{1,3}")
DASH_RE = re.compile(r"[-–—:]+")

# Tekst koji NIJE ime ekipe (kolo, delegat, sudija...)
ROUND_RE = re.compile(r"^\s*\d+\.?\s*(kolo|коло)\b", re.IGNORECASE)
BAD_WORDS = ("delegate", "delegat", "делегат", "sudija", "судија", "referee")


# ============================================================
# HTTP
# ============================================================

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def fetch(url):
    response = SESSION.get(url, timeout=TIMEOUT)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    return response.text


# ============================================================
# POMOCNE FUNKCIJE
# ============================================================

def normalize(text):
    return " ".join(text.split()).strip()


def valid_date(day, month, year):
    try:
        return datetime(year, month, day)
    except ValueError:
        return None


def extract_date(text):
    match = ANY_DATE_RE.search(text)
    if not match:
        return None
    day, month, year = (int(match.group(i)) for i in (1, 2, 3))
    if not 2020 <= year <= 2035:
        return None
    return valid_date(day, month, year)


def extract_time_after_date(text):
    """Vreme trazimo SAMO odmah iza datuma (inace hvata sitne brojeve)."""
    match = ANY_DATE_RE.search(text)
    if not match:
        return None
    rest = text[match.end():match.end() + 12]
    t = TIME_RE.search(rest)
    if not t:
        return None
    return f"{int(t.group(1)):02d}:{t.group(2)}"


def is_bad_team(name):
    if not name:
        return True
    low = name.lower()
    if ROUND_RE.match(name):
        return True
    if any(word in low for word in BAD_WORDS):
        return True
    if DASH_RE.fullmatch(name.strip()):
        return True
    if TIME_FULL_RE.fullmatch(name.strip()):
        return True
    if DIGITS_RE.fullmatch(name.strip()):
        return True
    return False


def extract_match_ids(soup):
    found = {}
    for anchor in soup.find_all("a", href=True):
        match = LINK_RE.search(anchor["href"])
        if match:
            found.setdefault(match.group(1), []).append(anchor)
    return found


def build_item(mid, date, competition, home, away, hs, as_, mtime):
    item = {
        "id": mid,
        "date": date.strftime("%Y-%m-%d"),
        "competition": competition or "",
        "home": home,
        "away": away,
        "home_score": hs,
        "away_score": as_,
        "url": f"https://www.ksb.rs/utakmica{mid}/",
    }
    if mtime:
        item["time"] = mtime
    return item


# ============================================================
# PARSER 1 - POCETNA STRANICA (tekst unutar <a> linka utakmice)
# Radi i za odigrane i za NAJAVLJENE utakmice.
# ============================================================

def parse_standard(html):
    soup = BeautifulSoup(html, "html.parser")

    groups = {}

    for anchor in soup.find_all("a", href=True):
        match = LINK_RE.search(anchor["href"])
        if not match:
            continue

        mid = match.group(1)

        # Svi tekstualni delovi unutar linka (ne spaja ih u jedan string)
        parts = [normalize(s) for s in anchor.stripped_strings]
        parts = [p for p in parts if p]

        if parts:
            groups.setdefault(mid, []).extend(parts)

    output = []

    for mid, texts in groups.items():

        date_index = None
        date_match = None

        for index, text in enumerate(texts):
            m = DATE_RE.search(text)
            if m:
                date_index = index
                date_match = m
                break

        if date_index is None:
            continue

        day = int(date_match.group(2))
        month = int(date_match.group(3))
        year = int(date_match.group(4))

        parsed_date = valid_date(day, month, year)
        if not parsed_date:
            continue

        # vreme moze biti u istom tekstu kao datum ("... 29.11.2026 18:00")
        same_text = texts[date_index][date_match.end():]
        mtime = None
        t = TIME_RE.search(same_text[:12])
        if t:
            mtime = f"{int(t.group(1)):02d}:{t.group(2)}"

        rest = texts[date_index + 1:]

        competition = None
        names = []
        scores = []

        for value in rest:
            v = value.strip()

            # vreme (samostalno)
            tm = TIME_FULL_RE.fullmatch(v)
            if tm:
                if not mtime:
                    mtime = f"{int(tm.group(1)):02d}:{tm.group(2)}"
                continue

            # crtica / dve tacke
            if DASH_RE.fullmatch(v):
                continue

            # samostalan rezultat "75" ili "75:68"
            if DIGITS_RE.fullmatch(v):
                scores.append(v)
                continue

            pair = SCORE_PAIR_RE.fullmatch(v)
            if pair:
                scores.extend([pair.group(1), pair.group(2)])
                continue

            if competition is None:
                competition = v
                continue

            names.append(v)

        # ocisti: samo prave ekipe
        names = [n for n in names if not is_bad_team(n)]

        if len(names) < 2:
            continue

        home, away = names[0], names[1]

        if home == away:
            continue

        home_score = away_score = None
        if len(scores) >= 2:
            home_score, away_score = scores[0], scores[1]

        output.append(
            build_item(mid, parsed_date, competition, home, away,
                       home_score, away_score, mtime)
        )

    return output


# ============================================================
# PARSER 2 - STRANICE TAKMICENJA
# ============================================================

def parse_generic(html):
    soup = BeautifulSoup(html, "html.parser")

    heading = soup.find(["h1", "h2", "h3"])
    page_comp = (
        normalize(heading.get_text(" ", strip=True)) if heading else ""
    )

    anchors = extract_match_ids(soup)
    output = []

    def ids_in(node):
        result = set()
        for a in node.find_all("a", href=True):
            m = LINK_RE.search(a["href"])
            if m:
                result.add(m.group(1))
        return result

    for mid, links in anchors.items():

        node = links[0]
        best = None

        for _ in range(10):
            parent = node.parent
            if parent is None:
                break
            if parent.name in ("body", "html", "[document]"):
                break
            if ids_in(parent) - {mid}:
                break
            best = parent
            node = parent

        if best is None:
            continue

        strings = [normalize(v) for v in best.stripped_strings]
        strings = [v for v in strings if v]
        blob = " | ".join(strings)

        # cela linija sa sudijama/delegatima -> preskoci
        if any(w in blob.lower() for w in BAD_WORDS):
            continue

        parsed_date = extract_date(blob)
        if not parsed_date:
            continue

        mtime = extract_time_after_date(blob)

        home = away = None
        home_score = away_score = None
        names = []

        for value in strings:
            v = value.strip()

            if ANY_DATE_RE.search(v):
                continue
            if TIME_FULL_RE.fullmatch(v):
                continue
            if DASH_RE.fullmatch(v):
                continue

            pair = re.fullmatch(r"(\d{1,3})\s*:\s*(\d{1,3})", v)
            if pair:
                home_score, away_score = pair.group(1), pair.group(2)
                continue

            if DIGITS_RE.fullmatch(v):
                continue

            # "Ekipa A : Ekipa B" u istom elementu
            if ":" in v:
                first, second = (normalize(x) for x in v.split(":", 1))
                if (
                    first and second
                    and not first.isdigit() and not second.isdigit()
                    and not is_bad_team(first) and not is_bad_team(second)
                ):
                    home, away = first, second
                    break
                continue

            if not is_bad_team(v):
                names.append(v)

        if not home or not away:
            if len(names) >= 2:
                home, away = names[0], names[1]

        if not home or not away or home == away:
            continue

        output.append(
            build_item(mid, parsed_date, page_comp, home, away,
                       home_score, away_score, mtime)
        )

    return output


# ============================================================
# KOMBINOVANJE PARSERA
# ============================================================

def parse_all(html):
    matches = {}

    for item in parse_standard(html):
        matches[item["id"]] = item

    for item in parse_generic(html):
        mid = item["id"]

        if mid not in matches:
            matches[mid] = item
        else:
            current = matches[mid]
            for key, value in item.items():
                if value is not None and (
                    current.get(key) is None or current.get(key) == ""
                ):
                    current[key] = value

    return list(matches.values())


# ============================================================
# PRONALAZENJE STRANICA TAKMICENJA
# ============================================================

def competition_links(html, base):
    soup = BeautifulSoup(html, "html.parser")
    urls = []

    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]

        if LINK_RE.search(href):
            continue
        if not COMP_LINK_RE.search(href):
            continue

        full = urljoin(base, href).split("#")[0]
        parsed = urlparse(full)

        if not parsed.netloc.endswith("ksb.rs"):
            continue

        if full not in urls:
            urls.append(full)

    return urls


# ============================================================
# UCITAVANJE STARIH PODATAKA (+ ciscenje smeca iz ranijih pokretanja)
# ============================================================

def is_garbage(match):
    home = str(match.get("home", ""))
    away = str(match.get("away", ""))
    for name in (home, away):
        if is_bad_team(name):
            return True
    # polovicno pocupana imena iz starog parsera
    if away.strip().lower() in ("тел", "tel"):
        return True
    return False


def load_old():
    if not OUT.exists():
        return {}

    try:
        payload = json.loads(OUT.read_text(encoding="utf-8"))
        matches = payload.get("matches", [])

        cleaned = {}
        removed = 0

        for match in matches:
            if not match.get("id"):
                continue
            if is_garbage(match):
                removed += 1
                continue
            cleaned[str(match["id"])] = match

        if removed:
            print(f"Uklonjeno starih neispravnih zapisa: {removed}")

        return cleaned

    except Exception as error:
        print("GRESKA pri citanju starog JSON-a:", error)
        sys.exit(1)


# ============================================================
# SPAJANJE PODATAKA
# ============================================================

def merge_match(previous, current):
    if not previous:
        return current

    merged = previous.copy()

    for key, value in current.items():
        if value is None or value == "":
            continue
        merged[key] = value

    return merged


# ============================================================
# GLAVNA FUNKCIJA
# ============================================================

def main():
    print("=" * 55)
    print("KSB SCRAPER - POKRETANJE")
    print("=" * 55)

    old = load_old()
    found = {}

    errors = 0
    fetched = 0

    queue = list(SOURCES)
    seen = set(SOURCES)
    index = 0

    DEBUG_DIR.mkdir(exist_ok=True)

    while index < len(queue) and index < MAX_PAGES:

        url = queue[index]
        index += 1

        try:
            print(f"\nPreuzimam: {url}")
            html = fetch(url)
            fetched += 1

            # sacuvaj sirov HTML prvih stranica radi dijagnostike
            if fetched <= 2:
                (DEBUG_DIR / f"page{fetched}.html").write_text(
                    html, encoding="utf-8"
                )

            matches = parse_all(html)

            upcoming = sum(1 for m in matches if m.get("home_score") is None)
            print(
                f"Pronadjeno: {len(matches)} utakmica | "
                f"Najavljene (bez rezultata): {upcoming}"
            )

            # primeri najavljenih utakmica u logu
            shown = 0
            for m in matches:
                if m.get("home_score") is None and shown < 3:
                    print(
                        f"   NAJAVA: {m['date']} {m.get('time', '--:--')} "
                        f"{m['home']} - {m['away']} ({m['id']})"
                    )
                    shown += 1

            for match in matches:
                mid = match["id"]
                found[mid] = merge_match(found.get(mid), match)

            if DISCOVER:
                for link in competition_links(html, url):
                    if link not in seen:
                        seen.add(link)
                        queue.append(link)

        except Exception as error:
            errors += 1
            print(f"GRESKA: {url} -> {error}")

        time.sleep(DELAY)

    print("\n" + "=" * 55)
    print("REZULTATI PREUZIMANJA")
    print("=" * 55)
    print("Preuzete stranice:", fetched)
    print("Pronadjene utakmice:", len(found))
    print("Broj gresaka:", errors)

    if fetched == 0:
        print("Nijedna stranica nije preuzeta.")
        print("Postojeci podaci ostaju sacuvani.")
        sys.exit(1)

    for mid, match in found.items():
        old[mid] = merge_match(old.get(mid), match)

    merged = sorted(
        old.values(),
        key=lambda m: (m.get("date", ""), str(m.get("id", ""))),
        reverse=True,
    )[:MAX_MATCHES]

    today = datetime.now().strftime("%Y-%m-%d")

    upcoming = sum(
        1 for m in merged
        if m.get("date") and m["date"] >= today
        and m.get("home_score") is None
    )

    finished = sum(
        1 for m in merged
        if m.get("home_score") is not None and m.get("away_score") is not None
    )

    payload = {
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "matches": merged,
    }

    OUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("\n" + "=" * 55)
    print("USPEH - JSON AZURIRAN")
    print("=" * 55)
    print("Ukupno utakmica:", len(merged))
    print("Buduce utakmice bez rezultata:", upcoming)
    print("Zavrsene utakmice sa rezultatom:", finished)
    print("Sacuvano u:", OUT)


if __name__ == "__main__":
    main()

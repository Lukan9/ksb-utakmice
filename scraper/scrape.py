
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

DATE_RE = re.compile(
    r"\((\d+)\)\s*(\d{2})\.(\d{2})\.(\d{4})"
)

ANY_DATE_RE = re.compile(
    r"(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})"
)

TIME_RE = re.compile(
    r"\b([01]?\d|2[0-3])[:.]([0-5]\d)\b"
)


# ============================================================
# HTTP
# ============================================================

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def fetch(url):
    response = SESSION.get(url, timeout=TIMEOUT)
    response.raise_for_status()

    response.encoding = (
        response.apparent_encoding or "utf-8"
    )

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

    day = int(match.group(1))
    month = int(match.group(2))
    year = int(match.group(3))

    if not 2020 <= year <= 2035:
        return None

    return valid_date(day, month, year)


def extract_time(text):
    match = TIME_RE.search(text)

    if not match:
        return None

    return (
        f"{int(match.group(1)):02d}:"
        f"{match.group(2)}"
    )


def extract_match_ids(soup):
    found = {}

    for anchor in soup.find_all("a", href=True):
        match = LINK_RE.search(anchor["href"])

        if match:
            mid = match.group(1)
            found.setdefault(mid, []).append(anchor)

    return found


# ============================================================
# PARSER 1 - POZNATI FORMAT
# ============================================================

def parse_standard(html):
    soup = BeautifulSoup(html, "html.parser")

    groups = {}

    for anchor in soup.find_all("a", href=True):
        match = LINK_RE.search(anchor["href"])

        if not match:
            continue

        mid = match.group(1)
        text = normalize(anchor.get_text(" ", strip=True))

        if text:
            groups.setdefault(mid, []).append(text)

    output = []

    for mid, texts in groups.items():

        date_index = None
        date_match = None

        for index, text in enumerate(texts):
            match = DATE_RE.search(text)

            if match:
                date_index = index
                date_match = match
                break

        if date_index is None:
            continue

        day = int(date_match.group(2))
        month = int(date_match.group(3))
        year = int(date_match.group(4))

        parsed_date = valid_date(day, month, year)

        if not parsed_date:
            continue

        rest = texts[date_index + 1:]

        if len(rest) < 3:
            continue

        competition = rest[0]
        parts = rest[1:]

        home = parts[0]
        away = None
        home_score = None
        away_score = None

        if len(parts) >= 4:
            home_score = parts[1]
            away = parts[2]
            away_score = parts[3]

        elif len(parts) >= 2:
            away = parts[1]

        if not away:
            continue

        item = {
            "id": mid,
            "date": parsed_date.strftime("%Y-%m-%d"),
            "competition": competition,
            "home": home,
            "away": away,
            "home_score": home_score,
            "away_score": away_score,
            "url": f"https://www.ksb.rs/utakmica{mid}/",
        }

        output.append(item)

    return output


# ============================================================
# PARSER 2 - STRANICE TAKMICENJA
# ============================================================

def parse_generic(html):

    soup = BeautifulSoup(html, "html.parser")

    heading = soup.find(["h1", "h2", "h3"])

    page_comp = (
        normalize(heading.get_text(" ", strip=True))
        if heading else ""
    )

    anchors = extract_match_ids(soup)

    output = []

    def ids_in(node):
        result = set()

        for anchor in node.find_all("a", href=True):
            match = LINK_RE.search(anchor["href"])

            if match:
                result.add(match.group(1))

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

        strings = [
            normalize(value)
            for value in best.stripped_strings
        ]

        strings = [value for value in strings if value]

        blob = " | ".join(strings)

        parsed_date = extract_date(blob)

        if not parsed_date:
            continue

        match_time = extract_time(blob)

        home = None
        away = None

        home_score = None
        away_score = None

        for value in strings:

            value = value.strip()

            if ANY_DATE_RE.search(value):
                continue

            if TIME_RE.fullmatch(value):
                continue

            # Rezultat zapisan kao 75:68
            score = re.fullmatch(
                r"(\d{1,3})\s*:\s*(\d{1,3})",
                value
            )

            if score:
                home_score = score.group(1)
                away_score = score.group(2)
                continue

            # Dve ekipe u istom elementu
            if ":" in value:

                parts = value.split(":", 1)

                first = normalize(parts[0])
                second = normalize(parts[1])

                if (
                    first
                    and second
                    and not first.isdigit()
                    and not second.isdigit()
                    and not re.fullmatch(r"[\d\s:]+", value)
                ):
                    home = first
                    away = second
                    break

        if not home or not away:
            continue

        if home == away:
            continue

        item = {
            "id": mid,
            "date": parsed_date.strftime("%Y-%m-%d"),
            "competition": page_comp,
            "home": home,
            "away": away,
            "home_score": home_score,
            "away_score": away_score,
            "url": f"https://www.ksb.rs/utakmica{mid}/",
        }

        if match_time:
            item["time"] = match_time

        output.append(item)

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
                    current.get(key) is None
                    or current.get(key) == ""
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
# UCITAVANJE STARIH PODATAKA
# ============================================================

def load_old():

    if not OUT.exists():
        return {}

    try:

        payload = json.loads(
            OUT.read_text(encoding="utf-8")
        )

        matches = payload.get("matches", [])

        return {
            str(match["id"]): match
            for match in matches
            if match.get("id")
        }

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

        # Ne prepisuj postojeci rezultat praznim rezultatom
        if key in ("home_score", "away_score"):
            if value is None and previous.get(key) is not None:
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

    while index < len(queue) and index < MAX_PAGES:

        url = queue[index]
        index += 1

        try:

            print(f"\nPreuzimam: {url}")

            html = fetch(url)

            fetched += 1

            matches = parse_all(html)

            upcoming = sum(
                1 for match in matches
                if match.get("home_score") is None
            )

            print(
                f"Pronadjeno: {len(matches)} utakmica | "
                f"Bez rezultata: {upcoming}"
            )

            for match in matches:

                mid = match["id"]

                if mid not in found:
                    found[mid] = match

                else:
                    found[mid] = merge_match(
                        found[mid],
                        match
                    )

            if DISCOVER:

                links = competition_links(html, url)

                for link in links:

                    if link not in seen:
                        seen.add(link)
                        queue.append(link)

        except Exception as error:

            errors += 1

            print(
                f"GRESKA: {url} -> {error}"
            )

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

    # Spajanje starih i novih utakmica
    for mid, match in found.items():

        old[mid] = merge_match(
            old.get(mid),
            match
        )

    # Sortiranje po datumu
    merged = sorted(
        old.values(),
        key=lambda match: (
            match.get("date", ""),
            str(match.get("id", ""))
        ),
        reverse=True
    )

    merged = merged[:MAX_MATCHES]

    # Statistika
    upcoming = sum(
        1 for match in merged
        if match.get("date")
        and match["date"] >= datetime.now().strftime("%Y-%m-%d")
        and match.get("home_score") is None
    )

    finished = sum(
        1 for match in merged
        if match.get("home_score") is not None
        and match.get("away_score") is not None
    )

    payload = {
        "updated": datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        ),
        "matches": merged
    }

    OUT.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
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

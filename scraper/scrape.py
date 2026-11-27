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

MAX_PAGES = 500
DELAY = 0.5
TIMEOUT = 20
MAX_MATCHES = 30000

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
                if second.lower() in ("тел", "tel"):
                    names.append(v)
                    continue
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
# PARSER 3 - STRANICA TAKMICENJA (zaglavlje kola + redovi)
#
# Izgled stranice:
#   1. коло(1)                      03.11.2026     <- zaglavlje kola
#   4807  КК Младост : СЛОБОДАН                     <- red utakmice
#   4808  КК Парагон : ОКК Београд   56 : 100       <- red sa rezultatom
#   4817  КК Младост : КК Борац Земун               <- najava, a u
#         27.11.2026 / 16:00 / dvorana / sudije        prosirenom delu su
#                                                      datum i vreme
# Pocetak je cist tekst po redosledu u dokumentu, pa ne zavisi od
# tacne HTML strukture.
# ============================================================

DATE_FULL_RE = re.compile(r"^\s*(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})\.?\s*$")
DATE_IN_RE = re.compile(r"(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})")
TIME_ONLY_RE = re.compile(r"^\s*([01]?\d|2[0-3])[:.]([0-5]\d)\s*$")
TEAM_PAIR_RE = re.compile(r"^(.+?)\s+:\s+(.+)$")
SCORE_SPACED_RE = re.compile(r"^\s*(\d{1,3})\s*:\s*(\d{1,3})\s*$")
ROUND_HINT_RE = re.compile(r"коло|kolo|\(\s*\d+\s*\)\s*$", re.IGNORECASE)


def page_events(soup):
    """Tekst i linkovi utakmica u redosledu u kom stoje u dokumentu."""
    events = []
    for node in soup.descendants:
        name = getattr(node, "name", None)

        if name == "a":
            m = LINK_RE.search(node.get("href", "") or "")
            if m:
                events.append(("link", m.group(1)))
            continue

        if name is not None:
            continue

        parent = node.parent.name if node.parent else ""
        if parent in ("script", "style", "title", "head", "noscript"):
            continue
        if type(node).__name__ in ("Comment", "Doctype", "CData"):
            continue

        text = normalize(str(node))
        if text:
            events.append(("text", text))

    # spoji "A" ":" "B" koji su razbijeni u tri elementa
    merged = []
    i = 0
    while i < len(events):
        kind, value = events[i]
        if (
            kind == "text" and value == ":" and merged
            and merged[-1][0] == "text" and i + 1 < len(events)
            and events[i + 1][0] == "text"
        ):
            merged[-1] = ("text", f"{merged[-1][1]} : {events[i + 1][1]}")
            i += 2
            continue
        merged.append((kind, value))
        i += 1

    return merged


def competition_page_id(url):
    m = re.search(r"takmicenje(\d+)", url or "")
    return m.group(1) if m else "x"


def parse_competition(html, url=None):
    soup = BeautifulSoup(html, "html.parser")
    events = page_events(soup)

    page_title = ""
    if soup.title and soup.title.string:
        page_title = normalize(soup.title.string)

    # ime takmicenja: prvi tekst sa "/" pre prvog zaglavlja kola
    competition = ""
    for kind, value in events:
        if kind != "text":
            continue
        if ROUND_HINT_RE.search(value) and DATE_IN_RE.search(value):
            break
        if "/" in value and 8 <= len(value) <= 150 and ":" not in value:
            competition = value
            break
    if not competition:
        competition = page_title

    rows = []
    row = None
    pending_link = None
    round_date = None
    prev_text = ""
    expect_time = False

    def finish():
        nonlocal row
        if row is not None:
            rows.append(row)
        row = None

    for index, (kind, value) in enumerate(events):

        if kind == "link":
            if row is not None and row["mid"] is None:
                row["mid"] = value
            else:
                pending_link = value
            continue

        text = value

        if any(w in text.lower() for w in BAD_WORDS) and not TEAM_PAIR_RE.match(text):
            prev_text = text
            continue

        # ---- datum ----
        date_match = DATE_FULL_RE.match(text)
        if date_match:
            day, month, year = (int(date_match.group(i)) for i in (1, 2, 3))
            parsed = valid_date(day, month, year)

            next_text = ""
            if index + 1 < len(events) and events[index + 1][0] == "text":
                next_text = events[index + 1][1]

            # datum u prosirenom delu reda ima vreme odmah iza sebe;
            # datum zaglavlja kola ima ispred sebe naziv kola
            is_header = bool(ROUND_HINT_RE.search(prev_text)) or not (
                row is not None and TIME_ONLY_RE.match(next_text)
            )

            if parsed and not is_header and row is not None:
                row["date"] = parsed
                expect_time = True
            elif parsed:
                finish()
                round_date = parsed
                pending_link = None
                expect_time = False

            prev_text = text
            continue

        # zaglavlje u jednom elementu: "1. коло(1) 03.11.2026"
        if (
            DATE_IN_RE.search(text) and ROUND_HINT_RE.search(text)
            and not TEAM_PAIR_RE.match(text)
        ):
            dm = DATE_IN_RE.search(text)
            parsed = valid_date(int(dm.group(1)), int(dm.group(2)), int(dm.group(3)))
            if parsed:
                finish()
                round_date = parsed
                pending_link = None
                expect_time = False
            prev_text = text
            continue

        # ---- vreme (odmah iza datuma u prosirenom delu) ----
        if expect_time and TIME_ONLY_RE.match(text) and row is not None:
            tm = TIME_ONLY_RE.match(text)
            row["time"] = f"{int(tm.group(1)):02d}:{tm.group(2)}"
            expect_time = False
            prev_text = text
            continue

        # ---- rezultat "56 : 100" ----
        score = SCORE_SPACED_RE.match(text)
        if score and row is not None and row["home_score"] is None:
            row["home_score"] = score.group(1)
            row["away_score"] = score.group(2)
            prev_text = text
            continue

        # ---- red utakmice "Domacin : Gost" ----
        pair = TEAM_PAIR_RE.match(text)
        if pair:
            home = normalize(pair.group(1))
            away = normalize(pair.group(2))
            if (
                home and away
                and not DIGITS_RE.fullmatch(home)
                and not DIGITS_RE.fullmatch(away)
                and not is_bad_team(home)
                and not is_bad_team(away)
            ):
                finish()
                row = {
                    "mid": pending_link,
                    "home": home,
                    "away": away,
                    "home_score": None,
                    "away_score": None,
                    "date": None,
                    "time": None,
                    "round_date": round_date,
                    "number": prev_text if re.fullmatch(r"\d{1,6}", prev_text or "") else None,
                }
                pending_link = None
                expect_time = False

        prev_text = text

    finish()

    page_id = competition_page_id(url)
    output = []

    for r in rows:
        match_date = r["date"] or r["round_date"]
        if not match_date:
            continue

        mid = r["mid"]
        fallback = False
        if not mid:
            if not r["number"]:
                continue
            mid = f"p{page_id}-{r['number']}"
            fallback = True

        item = build_item(
            mid, match_date, competition, r["home"], r["away"],
            r["home_score"], r["away_score"], r["time"],
        )
        if fallback and url:
            item["url"] = url

        output.append(item)

    return output


# ============================================================
# KOMBINOVANJE PARSERA
# ============================================================

def parse_all(html, url=None):
    matches = {}

    for item in parse_standard(html):
        matches[item["id"]] = item

    competition_items = parse_competition(html, url)

    # parse_generic samo kao rezerva ako novi parser nista ne nadje
    extra = competition_items if competition_items else parse_generic(html)

    for item in extra:
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


def unparsed_rows(html, parsed_ids, limit=6):
    """Vraca HTML redova utakmica koje parser NIJE uspeo da procita."""
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for mid, links in extract_match_ids(soup).items():
        if mid in parsed_ids:
            continue
        node = links[0]
        best = node
        for _ in range(10):
            parent = node.parent
            if parent is None or parent.name in ("body", "html", "[document]"):
                break
            ids = set()
            for a in parent.find_all("a", href=True):
                m = LINK_RE.search(a["href"])
                if m:
                    ids.add(m.group(1))
            if ids - {mid}:
                break
            best = parent
            node = parent
        rows.append((mid, str(best)))
        if len(rows) >= limit:
            break
    return rows

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
        # linkovi za promenu jezika (redirect.php?lang=...) NE smeju da se
        # otvaraju: sajt posle toga prikazuje imena ekipa prazna/drugacija
        if "redirect.php" in href or "lang=" in href:
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



def dedupe(matches):
    """Isti datum + iste ekipe = ista utakmica; pravi ID pobedjuje rezervni."""
    best = {}
    for m in matches:
        key = (m.get("date"), m.get("home"), m.get("away"))
        cur = best.get(key)
        if cur is None:
            best[key] = m
            continue
        cur_fb = str(cur.get("id", "")).startswith("p")
        new_fb = str(m.get("id", "")).startswith("p")
        if cur_fb and not new_fb:
            for k in ("home_score", "away_score", "time"):
                if m.get(k) in (None, "") and cur.get(k) not in (None, ""):
                    m[k] = cur[k]
            best[key] = m
        elif new_fb and not cur_fb:
            for k in ("home_score", "away_score", "time"):
                if cur.get(k) in (None, "") and m.get(k) not in (None, ""):
                    cur[k] = m[k]
        else:
            best[(key, m.get("id"))] = m
    return list(best.values())

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

            matches = parse_all(html, url)

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

            parsed_ids = {m["id"] for m in matches}
            for mid, row in unparsed_rows(html, parsed_ids):
                print(f"   NEPROCITANO {mid}: {normalize(row)[:700]}")

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

    if fetched == 0 or not found:
        print("Nijedna stranica/utakmica nije preuzeta.")
        print("Postojeci podaci ostaju sacuvani (nista nije obrisano).")
        sys.exit(1)

    # ZASTITA: ako je neka stranica pala i dobili smo mnogo manje utakmica
    # nego prosli put, ne brisemo stare podatke (da sajt ne ostane prazan).
    if errors > 0 and old and len(found) < len(old) * 0.5:
        print(
            f"Preuzeto samo {len(found)} utakmica (ranije {len(old)}) "
            f"uz {errors} gresaka."
        )
        print("Postojeci podaci ostaju sacuvani (nista nije obrisano).")
        sys.exit(1)

    # Stari podaci se NE zadrzavaju: JSON je tacna slika sajta
    # iz upravo zavrsenog preuzimanja.
    print(f"Brisem stare podatke ({len(old)}), upisujem nove ({len(found)}).")

    merged = sorted(
        dedupe(list(found.values())),
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
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
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

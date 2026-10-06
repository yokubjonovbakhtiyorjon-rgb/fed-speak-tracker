#!/usr/bin/env python3
"""Fed Speak Tracker.

Aggregates speeches, testimony, and statements by Federal Reserve officials
(Board of Governors, the 12 Reserve Bank presidents, and other Fed officials)
from official sources into one static web page plus a combined RSS feed.

Standard library only, so it runs unchanged on GitHub Actions or any server
with Python 3.10+.

Usage:
    python scripts/build.py                 # fetch live sources, update archive, render site
    python scripts/build.py --offline DIR   # read source payloads from DIR/<source_id>.xml|.html (testing)
    python scripts/build.py --render-only   # re-render site from the existing archive
"""
from __future__ import annotations

import argparse
import datetime as dt
import difflib
import html
import json
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import format_datetime, parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"
DATA = ROOT / "data"
SITE = ROOT / "site"
TEMPLATE = ROOT / "templates" / "index.html"

EASTERN = ZoneInfo("America/New_York")
NS = {
    "dc": "http://purl.org/dc/elements/1.1/",
    "bibo": "http://purl.org/ontology/bibo/",
    "atom": "http://www.w3.org/2005/Atom",
}
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
SPEECH_SERIES = re.compile(r"speech|testimony|remarks|statement", re.I)
PAGE_WINDOW_DAYS = 400          # items embedded in the page; the full archive stays in data/items.json
FEED_ITEMS = 100                # items in the combined RSS feed


# --------------------------------------------------------------------------- utilities

def load_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    value = re.sub(r"<[^>]+>", " ", value)          # strip any embedded markup
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def canonical_url(url: str) -> str:
    """Normalize a URL for de-duplication (scheme, host case, trailing slash, query/fragment)."""
    parts = urlsplit(url.strip())
    path = re.sub(r"/+$", "", parts.path) or "/"
    return urlunsplit(("https", parts.netloc.lower().removeprefix("www."), path.lower(), "", ""))


def title_from_slug(url: str) -> str:
    slug = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
    slug = re.sub(r"\.(aspx|html?|cfm)$", "", slug)
    words = [w for w in slug.split("-") if w]
    small = {"a", "an", "and", "at", "for", "in", "of", "on", "the", "to", "with"}
    out = [w if (i and w in small) else w.capitalize() for i, w in enumerate(words)]
    return " ".join(out)


def to_eastern_date(raw: str | None) -> str | None:
    """Parse RFC 822 or ISO dates and return YYYY-MM-DD in U.S. Eastern time."""
    if not raw:
        return None
    raw = raw.strip()
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError):
        parsed = None
    if parsed is None:
        try:
            parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            m = re.match(r"(\d{4})-(\d{2})-(\d{2})", raw)
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None
    if parsed.tzinfo is None:            # date-only values are already calendar dates
        return parsed.date().isoformat()
    return parsed.astimezone(EASTERN).date().isoformat()


# --------------------------------------------------------------------------- fetching

def fetch(url: str, contact: str, timeout: int = 30, retries: int = 2) -> bytes:
    headers = {
        "User-Agent": f"FedSpeakTracker/1.0 (institutional research aggregator; contact: {contact})",
        "Accept": "application/rss+xml, application/xml;q=0.9, text/xml;q=0.9, text/html;q=0.8, */*;q=0.5",
    }
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last_error = exc
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"fetch failed for {url}: {last_error}")


# --------------------------------------------------------------------------- parsing

def parse_feed(raw: bytes) -> list[dict]:
    """Parse RSS 2.0 or Atom into a list of plain dicts (namespace-aware, BOM-tolerant)."""
    raw = raw.lstrip(b"\xef\xbb\xbf").lstrip()
    root = ET.fromstring(raw)
    items = []
    for it in root.iter("item"):
        def get(tag: str) -> str:
            return (it.findtext(tag, default="", namespaces=NS) or "").strip()
        items.append({
            "title": clean_text(get("title")),
            "link": get("link"),
            "guid": get("guid"),
            "description": clean_text(get("description")),
            "pubDate": get("pubDate"),
            "dc_date": get("dc:date"),
            "creator": clean_text(get("dc:creator")),
            "series": clean_text(get("bibo:series")),
            "category": clean_text(get("category")),
            "subject": clean_text(get("dc:subject")),
        })
    if not items:  # Atom fallback
        a = "{http://www.w3.org/2005/Atom}"
        for entry in root.iter(f"{a}entry"):
            link_el = entry.find(f"{a}link")
            items.append({
                "title": clean_text(entry.findtext(f"{a}title")),
                "link": link_el.get("href", "") if link_el is not None else "",
                "guid": entry.findtext(f"{a}id") or "",
                "description": clean_text(entry.findtext(f"{a}summary")),
                "pubDate": entry.findtext(f"{a}published") or entry.findtext(f"{a}updated") or "",
                "dc_date": "", "creator": clean_text(entry.findtext(f"{a}author/{a}name")),
                "series": "", "category": "", "subject": "",
            })
    return items


class LinkExtractor(HTMLParser):
    """Collect (href, anchor text) pairs from an HTML listing page."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            self.links.append((self._href, re.sub(r"\s+", " ", "".join(self._text)).strip()))
            self._href = None


# --------------------------------------------------------------------------- roster and classification

class Roster:
    def __init__(self, data: dict):
        self.people = data["people"]
        self.by_id = {p["id"]: p for p in self.people}
        self.voter_year = data.get("voter_year")

    def by_surname(self, surname: str, org: str | None = None, initial: str | None = None):
        cands = [p for p in self.people if p["surname"].lower() == surname.lower()]
        if org:
            same_org = [p for p in cands if p["org"] == org]
            cands = same_org or cands
        if initial:
            init = [p for p in cands if p["name"][0].lower() == initial.lower()]
            cands = init or cands
        cands.sort(key=lambda p: not p.get("active", True))   # prefer current officials
        return cands[0] if cands else None

    def find_in_text(self, text: str, org: str | None = None):
        """Find a roster person named in free text (case-sensitive surname, word-bounded)."""
        hits = [p for p in self.people if re.search(rf"\b{re.escape(p['surname'])}\b", text)]
        if org:
            hits = [p for p in hits if p["org"] == org] or hits
        hits.sort(key=lambda p: not p.get("active", True))
        return hits[0] if hits else None


def resolve_creator(creator: str, org: str, roster: Roster) -> tuple[dict | None, str]:
    """Fed in Print creators look like 'Williams, John C.'; multiple authors are ';'-separated."""
    first = creator.split(";")[0].strip()
    if "," in first:
        surname, given = [s.strip() for s in first.split(",", 1)]
        display = f"{given} {surname}".strip()
    else:
        parts = first.split()
        surname, given, display = (parts[-1] if parts else ""), (parts[0] if parts else ""), first
    person = roster.by_surname(surname, org=None, initial=given[:1] or None) if surname else None
    if person and person["org"] != org and person["tier"] != "board":
        person = None   # same surname at a different Bank: treat as a different person
    return person, display


def classify_type(*texts: str) -> str:
    blob = " ".join(t for t in texts if t).lower()
    if "testimony" in blob or "/testimony/" in blob:
        return "Testimony"
    if re.search(r"\bstatement\b|\bdissent", blob):
        return "Statement"
    if re.search(r"conversation|fireside|panel discussion|q&a|moderated|interview|podcast", blob):
        return "Discussion"
    if re.search(r"\bessay\b", blob):
        return "Essay"
    return "Speech"


def tag_topics(text: str, rules: dict[str, re.Pattern]) -> list[str]:
    return [name for name, rx in rules.items() if rx.search(text)]


def make_record(*, source: dict, person: dict | None, speaker_display: str, title: str, url: str,
                date: str | None, venue: str, kind: str, topics_text: str, rules,
                date_estimated: bool = False, roster: Roster) -> dict:
    org = person["org"] if person else source["org"]
    return {
        "url": url,
        "key": canonical_url(url),
        "title": title or title_from_slug(url),
        "date": date,
        "date_estimated": date_estimated,
        "speaker_id": person["id"] if person else None,
        "speaker": person["name"] if person else (speaker_display or f"Federal Reserve Bank of {org}"),
        "role": person["role"] if person else ("Board staff" if org == "Board of Governors" else "Reserve Bank official"),
        "org": org,
        "tier": person["tier"] if person else "official",
        "voter": bool(person and person.get("voter")),
        "type": kind,
        "venue": venue,
        "topics": tag_topics(f"{title} {topics_text}", rules),
        "source_id": source["id"],
        "source_name": source["name"],
        "priority": source["priority"],
        "alt_links": [],
    }


# --------------------------------------------------------------------------- adapters

def adapt_board_rss(raw: bytes, source: dict, roster: Roster, rules) -> list[dict]:
    out = []
    for it in parse_feed(raw):
        # Board titles read "Surname, Title of Remarks"
        surname, _, rest = it["title"].partition(", ")
        person = roster.by_surname(surname, org="Board of Governors") if rest else None
        title = rest if rest else it["title"]
        venue = re.sub(r"^(Speech|Testimony|Statement)\s+", "", it["description"]).strip()
        kind = classify_type(it["category"], it["link"], title)
        out.append(make_record(
            source=source, person=person, speaker_display=surname if not person else "",
            title=title, url=it["link"], date=to_eastern_date(it["pubDate"]), venue=venue,
            kind=kind, topics_text=venue, rules=rules, roster=roster))
    return out


def adapt_fedinprint_rss(raw: bytes, source: dict, roster: Roster, rules) -> list[dict]:
    out = []
    for it in parse_feed(raw):
        if not SPEECH_SERIES.search(it["series"]):
            continue    # skip working papers, blogs, journal articles
        person, display = resolve_creator(it["creator"], source["org"], roster)
        kind = classify_type(it["title"], it["description"], "testimony" if "testimony" in it["series"].lower() else "")
        out.append(make_record(
            source=source, person=person, speaker_display=display, title=it["title"], url=it["link"],
            date=to_eastern_date(it["dc_date"] or it["pubDate"]), venue=it["description"], kind=kind,
            topics_text=f"{it['description']} {it['subject']}", rules=rules, roster=roster))
    return out


def adapt_bank_rss(raw: bytes, source: dict, roster: Roster, rules) -> list[dict]:
    out = []
    default = roster.by_id.get(source.get("default_speaker", ""))
    for it in parse_feed(raw):
        title = it["title"] or title_from_slug(it["link"])
        text = f"{title} {it['description']}"
        person = roster.find_in_text(text, org=source["org"]) or default
        out.append(make_record(
            source=source, person=person, speaker_display="", title=title, url=it["link"],
            date=to_eastern_date(it["pubDate"] or it["dc_date"]), venue=it["description"],
            kind=classify_type(title, it["description"]), topics_text=it["description"],
            rules=rules, roster=roster))
    return out


def adapt_html_links(raw: bytes, source: dict, roster: Roster, rules, today: str) -> list[dict]:
    parser = LinkExtractor()
    parser.feed(raw.decode("utf-8", errors="replace"))
    pattern = re.compile(source["link_pattern"])
    default = roster.by_id.get(source.get("default_speaker", ""))
    best: dict[str, tuple[str, str]] = {}          # canonical URL -> (url, best anchor text)
    for href, text in parser.links:
        if not href:
            continue
        url = urljoin(source["url"], href)
        if not pattern.search(url):
            continue
        usable = text if len(text) >= 12 and not re.match(r"(read|learn|view) more", text, re.I) else ""
        key = canonical_url(url)
        if key not in best or len(usable) > len(best[key][1]):
            best[key] = (url, usable)
    out = []
    for url, text in best.values():
        m = pattern.search(url)
        title = text or title_from_slug(url)
        gd = m.groupdict()
        date, estimated = None, False
        try:
            if gd.get("y") and gd.get("mon") and gd.get("d"):
                date = dt.date(int(gd["y"]), MONTHS[gd["mon"].lower()[:3]], int(gd["d"])).isoformat()
            elif gd.get("y") and gd.get("m") and gd.get("d"):
                date = dt.date(int(gd["y"]), int(gd["m"]), int(gd["d"])).isoformat()
        except (KeyError, ValueError):
            date = None
        if date is None:
            # First-seen date. On a source's first (baseline) run, listing pages show their whole
            # back catalog, so undated links are left undated instead of being stamped "today".
            date, estimated = (None if source.get("_baseline") else today), True
        person = roster.find_in_text(title, org=source["org"]) or default
        out.append(make_record(
            source=source, person=person, speaker_display="", title=title, url=url, date=date,
            venue="", kind=classify_type(title), topics_text="", rules=rules,
            date_estimated=estimated, roster=roster))
    return out


ADAPTERS = {
    "board_rss": adapt_board_rss,
    "fedinprint_rss": adapt_fedinprint_rss,
    "bank_rss": adapt_bank_rss,
}


# --------------------------------------------------------------------------- merge and de-duplicate

def norm_title(t: str) -> str:
    t = re.sub(r"[:;].*$", "", t.lower())          # drop subtitles ("...: A speech at ...")
    return re.sub(r"[^a-z0-9 ]+", "", t).strip()


def same_event(a: dict, b: dict) -> bool:
    if a["key"] == b["key"]:
        return True
    if not a["date"] or not b["date"] or a["date_estimated"] or b["date_estimated"]:
        return False
    if a["date"] != b["date"]:
        return False
    who_a = a["speaker_id"] or a["speaker"].lower()
    who_b = b["speaker_id"] or b["speaker"].lower()
    if who_a != who_b:
        return False
    ta, tb = norm_title(a["title"]), norm_title(b["title"])
    if ta and tb and (ta in tb or tb in ta):
        return True
    return difflib.SequenceMatcher(None, ta, tb).ratio() >= 0.6


def merge(archive: list[dict], fresh: list[dict], seen_at: str) -> list[dict]:
    items = list(archive)
    for rec in sorted(fresh, key=lambda r: r["priority"]):
        match = next((x for x in items if same_event(x, rec)), None)
        if match is None:
            rec["first_seen"] = seen_at
            items.append(rec)
            continue
        if rec["priority"] < match["priority"]:
            # A higher-priority (more primary) source wins; keep the old link as an alternate.
            alts = match.get("alt_links", []) + [{"url": match["url"], "source": match["source_name"]}]
            first_seen = match.get("first_seen", seen_at)
            if match.get("date_estimated") is False and rec.get("date_estimated"):
                rec["date"], rec["date_estimated"] = match["date"], False
            match.clear()
            match.update(rec, first_seen=first_seen, alt_links=alts)
        elif rec["key"] != match["key"] and all(a["url"] != rec["url"] for a in match.get("alt_links", [])):
            match.setdefault("alt_links", []).append({"url": rec["url"], "source": rec["source_name"]})
            if match.get("date_estimated") and rec["date"] and not rec["date_estimated"]:
                match["date"], match["date_estimated"] = rec["date"], False
        for t in rec["topics"]:
            if t not in match["topics"]:
                match["topics"].append(t)
    items.sort(key=lambda r: (r["date"] or "", r.get("first_seen", "")), reverse=True)
    return items


# --------------------------------------------------------------------------- rendering

def refresh_person(rec: dict, roster: Roster) -> dict:
    """Re-apply current roster labels (role, voter status) so archived items stay current."""
    p = roster.by_id.get(rec.get("speaker_id") or "")
    if p:
        rec.update(speaker=p["name"], role=p["role"], org=p["org"], tier=p["tier"], voter=bool(p.get("voter")))
    return rec


def render_site(items: list[dict], health: list[dict], roster: Roster, built_at: dt.datetime) -> None:
    SITE.mkdir(parents=True, exist_ok=True)
    cutoff = (built_at.astimezone(EASTERN).date() - dt.timedelta(days=PAGE_WINDOW_DAYS)).isoformat()
    window = [refresh_person(dict(i), roster) for i in items if (i["date"] or "") >= cutoff]
    participants = [
        {k: p[k] for k in ("id", "name", "surname", "org", "role", "tier", "voter")}
        for p in roster.people if p.get("active") and p["tier"] in ("board", "president")
    ]
    payload = {
        "built_at": built_at.isoformat(),
        "voter_year": roster.voter_year,
        "items": [{k: v for k, v in i.items() if k not in ("key", "priority")} for i in window],
        "participants": participants,
        "health": health,
    }
    blob = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    page = TEMPLATE.read_text(encoding="utf-8").replace("/*__DATA__*/null", blob)
    (SITE / "index.html").write_text(page, encoding="utf-8")
    write_rss(items[:FEED_ITEMS], built_at)


def write_rss(items: list[dict], built_at: dt.datetime) -> None:
    rss = ET.Element("rss", version="2.0")
    ch = ET.SubElement(rss, "channel")
    ET.SubElement(ch, "title").text = "Fed Speak Tracker"
    ET.SubElement(ch, "link").text = "./"
    ET.SubElement(ch, "description").text = "Speeches, testimony, and statements by Federal Reserve officials, aggregated from official sources."
    ET.SubElement(ch, "lastBuildDate").text = format_datetime(built_at)
    for i in items:
        el = ET.SubElement(ch, "item")
        ET.SubElement(el, "title").text = f"{i['speaker']}: {i['title']}"
        ET.SubElement(el, "link").text = i["url"]
        ET.SubElement(el, "guid").text = i["url"]
        ET.SubElement(el, "category").text = i["type"]
        ET.SubElement(el, "description").text = " | ".join(x for x in (i["role"], i["org"], i["venue"]) if x)
        if i["date"]:
            d = dt.datetime.fromisoformat(i["date"]).replace(hour=12, tzinfo=EASTERN)
            ET.SubElement(el, "pubDate").text = format_datetime(d)
    ET.ElementTree(rss).write(SITE / "feed.xml", encoding="utf-8", xml_declaration=True)


# --------------------------------------------------------------------------- main

def run(offline_dir: Path | None, render_only: bool) -> int:
    cfg = load_json(CONFIG / "sources.json")
    roster = Roster(load_json(CONFIG / "roster.json"))
    rules = {k: re.compile(v, re.I) for k, v in load_json(CONFIG / "topics.json")["topics"].items()}
    archive = load_json(DATA / "items.json", default=[])
    built_at = now_utc()
    today = built_at.astimezone(EASTERN).date().isoformat()

    if render_only:
        health = load_json(DATA / "health.json", default=[])
        render_site(archive, health, roster, built_at)
        print(f"Rendered {len(archive)} archived items.")
        return 0

    fresh, health = [], []
    for src in cfg["sources"]:
        entry = {"id": src["id"], "name": src["name"], "url": src["url"], "ok": False,
                 "parsed": 0, "newest": None, "stale": False, "error": None}
        try:
            if offline_dir is not None:
                path = next((p for p in (offline_dir / f"{src['id']}.xml", offline_dir / f"{src['id']}.html") if p.exists()), None)
                if path is None:
                    raise FileNotFoundError("no offline fixture")
                raw = path.read_bytes()
            else:
                raw = fetch(src["url"], cfg.get("contact", "unknown"))
                time.sleep(1.0)    # be polite to Federal Reserve servers
            if src["adapter"] == "html_links":
                src["_baseline"] = not any(
                    i.get("source_id") == src["id"] or any(a.get("source") == src["name"] for a in i.get("alt_links", []))
                    for i in archive)
                recs = adapt_html_links(raw, src, roster, rules, today)
            else:
                recs = ADAPTERS[src["adapter"]](raw, src, roster, rules)
            recs = [r for r in recs if r["   .lower().startswith(("https://", "http://"))"]]
            fresh.extend(recs)
            dated = sorted((r["date"] for r in recs if r["date"] and not r["date_estimated"]), reverse=True)
            entry.update(ok=True, parsed=len(recs), newest=dated[0] if dated else None)
            limit = src.get("stale_after_days", 120)
            if entry["newest"]:
                age = (dt.date.fromisoformat(today) - dt.date.fromisoformat(entry["newest"])).days
                entry["stale"] = age > limit
            elif src["adapter"] != "html_links":
                entry["stale"] = True
        except Exception as exc:  # one bad source must never break the build
            entry["error"] = f"{type(exc).__name__}: {exc}"[:300]
        health.append(entry)
        status = "ok" if entry["ok"] else "ERROR"
        print(f"[{status:5}] {src['id']:<18} parsed={entry['parsed']:<4} newest={entry['newest']} "
              f"{'STALE ' if entry['stale'] else ''}{entry['error'] or ''}")

    items = merge(archive, fresh, built_at.isoformat())
    save_json(DATA / "items.json", items)
    save_json(DATA / "health.json", health)
    render_site(items, health, roster, built_at)
    failures = sum(1 for h in health if not h["ok"])
    print(f"Archive: {len(items)} items. Sources failing: {failures}/{len(health)}.")
    return 0 if failures < len(health) else 1     # fail the job only if every source failed


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--offline", type=Path, help="directory of saved source payloads for testing")
    ap.add_argument("--render-only", action="store_true", help="re-render the site from the archive")
    args = ap.parse_args()
    sys.exit(run(args.offline, args.render_only))


if __name__ == "__main__":
    main()

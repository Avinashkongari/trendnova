#!/usr/bin/env python3
"""
Collects fresh deals + trending headlines from trusted public RSS/Atom feeds
and writes data/live.json. Runs in GitHub Actions (free, no phone/server needed).

- Only links whose domain is in TRUSTED_DOMAINS are kept.
- Only title + link + source + date are stored (no article text copied).
- If a feed group fails, the previous data for that group is kept, so the
  site never goes empty.
Python standard library only.
"""
import datetime as dt
import email.utils
import html
import json
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "data" / "live.json"
UA = "Mozilla/5.0 (compatible; TrendNovaBot/1.0)"
TIMEOUT = 20
MAX_AGE_DAYS = 10
LIMITS = {"deals": 30, "tech": 12, "ai": 10, "security": 10, "trends": 20}

# Only these domains (and their subdomains) are allowed as link targets.
TRUSTED_DOMAINS = [
    # deal / shopping publishers
    "slickdeals.net", "9to5toys.com", "9to5mac.com", "dealnews.com",
    # news publishers
    "theverge.com", "engadget.com", "techcrunch.com", "bbc.co.uk", "bbc.com",
    "bleepingcomputer.com", "thehackernews.com", "krebsonsecurity.com",
    "arstechnica.com", "wired.com", "cnet.com", "techradar.com",
    # retailers / brands (in case a feed links straight to them)
    "amazon.com", "bestbuy.com", "walmart.com", "target.com", "newegg.com",
    "apple.com", "samsung.com", "dell.com", "hp.com", "lenovo.com",
    "microsoft.com", "ebay.com", "costco.com", "google.com",
]

FEEDS = {
    "deals": [
        ("Slickdeals", "https://slickdeals.net/newsearch.php?mode=frontpage&searcharea=deals&searchin=first&rss=1", {}),
        ("9to5Toys", "https://9to5toys.com/feed/", {}),
    ],
    "tech": [
        ("The Verge", "https://www.theverge.com/rss/index.xml", {}),
        ("Engadget", "https://www.engadget.com/rss.xml", {}),
        ("BBC Tech", "https://feeds.bbci.co.uk/news/technology/rss.xml", {}),
    ],
    "ai": [
        ("TechCrunch AI", "https://techcrunch.com/category/artificial-intelligence/feed/", {}),
        ("The Verge AI", "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml", {}),
    ],
    "security": [
        ("BleepingComputer", "https://www.bleepingcomputer.com/feed/", {}),
        ("The Hacker News", "https://feeds.feedburner.com/TheHackersNews", {}),
        ("Krebs on Security", "https://krebsonsecurity.com/feed/", {}),
    ],
    "trends": [
        ("Google Trends", "https://trends.google.com/trending/rss?geo=US", {"search_link": True}),
    ],
}


def host_ok(url):
    try:
        h = urllib.parse.urlparse(url).hostname or ""
    except Exception:
        return False
    h = h.lower().removeprefix("www.")
    return any(h == d or h.endswith("." + d) for d in TRUSTED_DOMAINS)


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read()


def local(tag):
    return tag.rsplit("}", 1)[-1]


def parse_date(text):
    if not text:
        return None
    text = text.strip()
    d = None
    try:
        d = email.utils.parsedate_to_datetime(text)
    except Exception:
        pass
    if d is None:
        try:
            d = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
        except Exception:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d


def parse_feed(data, source, opts):
    root = ET.fromstring(data)
    out = []
    for el in root.iter():
        if local(el.tag) not in ("item", "entry"):
            continue
        title = link = date = None
        for c in el:
            n = local(c.tag)
            if n == "title" and c.text:
                title = html.unescape(c.text.strip())
            elif n == "link":
                href = c.get("href")
                if href:
                    if link is None or c.get("rel") in (None, "alternate"):
                        link = href.strip()
                elif c.text and not link:
                    link = c.text.strip()
            elif n in ("pubDate", "published", "updated") and date is None:
                date = parse_date(c.text)
        if not title:
            continue
        if opts.get("search_link"):
            link = "https://www.google.com/search?q=" + urllib.parse.quote_plus(title)
        if not link or not link.startswith(("http://", "https://")) or not host_ok(link):
            continue
        out.append({
            "title": re.sub(r"\s+", " ", title)[:160],
            "link": link,
            "source": source,
            "date": date.isoformat() if date else None,
        })
    return out


def collect(group):
    now = dt.datetime.now(dt.timezone.utc)
    seen, items = set(), []
    for source, url, opts in FEEDS[group]:
        try:
            got = parse_feed(fetch(url), source, opts)
        except Exception as e:
            print(f"  ! {group}/{source}: {e}", file=sys.stderr)
            continue
        for it in got:
            if it["date"]:
                age = (now - dt.datetime.fromisoformat(it["date"])).days
                if age > MAX_AGE_DAYS:
                    continue
            key = re.sub(r"\W+", "", it["title"].lower())
            if key in seen:
                continue
            seen.add(key)
            items.append(it)
    items.sort(key=lambda i: i["date"] or "", reverse=True)
    return items[:LIMITS[group]]


def main():
    old = {}
    if OUT.exists():
        try:
            old = json.loads(OUT.read_text(encoding="utf-8"))
        except Exception:
            old = {}
    data = {"generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
    ok = 0
    for g in FEEDS:
        items = collect(g)
        print(f"{g}: {len(items)} items")
        if items:
            ok += 1
            data[g] = items
        else:
            data[g] = old.get(g, [])  # keep last good data
    if ok == 0 and old:
        print("no fresh data; keeping previous file")
        return
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(OUT)
    print("written", OUT)


if __name__ == "__main__":
    main()

"""Local signals fetcher: reads ONLY the URLs in registry.yaml.

- Every request URL must start with one of the source's allow_prefixes; redirects are followed
  only within them. Nothing is ever fetched from a link found inside a page.
- robots.txt is checked for our User-Agent and for AI agents (the text is read by a model); a
  disallow skips the source. 401/403 on robots.txt counts as disallow.
- Results are cached for refresh_minutes. Items keep source_id, title, link, published_at,
  retrieved_at, the feed snapshot hash and at most a 300-character excerpt. No article bodies.
- Items older than 24 hours are dropped (current-state DOT feeds keep active conditions).
- A failed fetch gives status "unknown", never "no closures".
"""
import email.utils
import hashlib
import html as html_lib
import json
import logging
import re
import time
import urllib.robotparser
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import httpx
import yaml

from ..db import utcnow
from ..record import save_snapshot
from ..schemas import Evidence

log = logging.getLogger(__name__)
REGISTRY_PATH = Path(__file__).with_name("registry.yaml")
LOCAL_TZ = ZoneInfo("America/Chicago")
MAX_AGE = timedelta(hours=24)
EXCERPT_CHARS = 300
MAX_ITEMS = 200
ROBOTS_AGENTS = ("ClosureCall", "ClaudeBot", "anthropic-ai")  # our UA, and AI agents (text goes to a model)
ROBOTS_TTL = 6 * 3600
# NWS county codes around Kansas City -> county names used by sources and facts.
COUNTY_NAMES = {"MOC095": "Jackson", "MOC047": "Clay", "MOC165": "Platte", "MOC037": "Cass", "MOC177": "Ray",
                "MOC107": "Lafayette", "KSC091": "Johnson", "KSC209": "Wyandotte", "KSC103": "Leavenworth",
                "KSC121": "Miami", "KSC045": "Douglas"}


# ---------------------------------------------------------------- registry

@dataclass(frozen=True)
class Source:
    id: str
    name: str
    url: str
    kind: str
    agent: str
    authority: str
    area: dict
    refresh_minutes: int
    parse: dict
    allow_prefixes: tuple[str, ...]
    robots: str = ""
    link: str | None = None
    current_state: bool = False

    def allowed(self, url: str) -> bool:
        return any(url.startswith(p) for p in self.allow_prefixes)


@lru_cache
def registry() -> dict:
    return yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))


def user_agent() -> str:
    return registry()["user_agent"]


def sources(agent: str | None = None) -> list[Source]:
    out = []
    for s in registry()["sources"]:
        src = Source(id=s["id"], name=s["name"], url=s["url"], kind=s["kind"], agent=s["agent"],
                     authority=s["authority"], area=s.get("area") or {}, refresh_minutes=int(s["refresh_minutes"]),
                     parse=s.get("parse") or {}, allow_prefixes=tuple(s.get("allow_prefixes") or [s["url"]]),
                     robots=s.get("robots", ""), link=s.get("link"), current_state=bool(s.get("current_state")))
        if agent is None or src.agent == agent:
            out.append(src)
    return out


def skipped() -> list[dict]:
    return registry().get("skipped", [])


# ---------------------------------------------------------------- results

@dataclass
class FeedResult:
    source: Source
    status: str  # ok | unknown | skipped
    items: list[dict] = field(default_factory=list)
    error: str | None = None
    retrieved_at: datetime = field(default_factory=utcnow)
    snapshot_hash: str | None = None

    def to_evidence(self) -> Evidence:
        s = self.source
        return Evidence(
            id=uuid.uuid4().hex, source="local", kind="local_feed",
            value={"source_id": s.id, "source_name": s.name, "agent": s.agent, "authority": s.authority,
                   "fetch_status": self.status, "error": self.error, "item_count": len(self.items),
                   "items": self.items},
            retrieved_at=self.retrieved_at, valid_until=self.retrieved_at + timedelta(minutes=s.refresh_minutes),
            url=s.link or s.url, snapshot_hash=self.snapshot_hash,
            relevance_note=(f"{s.name}: {len(self.items)} item(s) in the last 24 h" if self.status == "ok" else
                            f"{s.name}: {self.status} ({self.error}); this is not evidence of 'no closures'."),
            status="ok" if self.status == "ok" else "unknown",
        )


class FetchError(Exception):
    pass


# ---------------------------------------------------------------- HTTP: allowlist + robots

_robots: dict[str, tuple[float, urllib.robotparser.RobotFileParser | None, str]] = {}


def _robot_parser(url: str) -> tuple[urllib.robotparser.RobotFileParser | None, str]:
    p = urlparse(url)
    base = f"{p.scheme}://{p.netloc}"
    hit = _robots.get(base)
    if hit and time.monotonic() - hit[0] < ROBOTS_TTL:
        return hit[1], hit[2]
    rp = urllib.robotparser.RobotFileParser()
    try:
        r = httpx.get(base + "/robots.txt", headers={"User-Agent": user_agent()}, timeout=10, follow_redirects=True)
        if r.status_code in (401, 403):
            rp.disallow_all, note = True, f"robots.txt {r.status_code}: treated as disallow"
        elif r.status_code >= 400:
            rp.allow_all, note = True, f"robots.txt {r.status_code}: no restrictions"
        else:
            rp.parse(r.text.splitlines())
            note = "robots.txt checked"
    except httpx.HTTPError as e:
        rp, note = None, f"robots.txt unavailable ({type(e).__name__})"
    _robots[base] = (time.monotonic(), rp, note)
    return rp, note


def robots_allows(url: str) -> tuple[bool, str]:
    rp, note = _robot_parser(url)
    if rp is None:
        return False, note
    blocked = [a for a in ROBOTS_AGENTS if not rp.can_fetch(a, url)]
    if blocked:
        return False, f"robots.txt disallows {', '.join(blocked)}"
    return True, note


def http_get(src: Source, url: str) -> bytes:
    """GET an allowlisted URL. Redirects are followed only to allowlisted URLs."""
    for _ in range(4):
        if not src.allowed(url):
            raise FetchError(f"blocked: {url} is outside the allowlist for {src.id}")
        ok, note = robots_allows(url)
        if not ok:
            raise PermissionError(note)
        r = httpx.get(url, headers={"User-Agent": user_agent(), "Accept": "*/*"}, timeout=15, follow_redirects=False)
        if r.is_redirect:
            url = urljoin(url, r.headers.get("location", ""))
            continue
        if r.status_code >= 400:
            raise FetchError(f"HTTP {r.status_code}")
        return r.content
    raise FetchError("too many redirects")


# ---------------------------------------------------------------- parsing helpers

_WS = re.compile(r"\s+")
_TAGS = re.compile(r"<[^>]+>")


def clean(text: Any) -> str:
    if text is None:
        return ""
    return _WS.sub(" ", html_lib.unescape(_TAGS.sub(" ", str(text)))).strip()


def dig(obj: Any, path: str | None) -> Any:
    if not path:
        return None
    for part in path.split("."):
        if not isinstance(obj, dict):
            return None
        obj = obj.get(part)
    return obj


def _local(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=LOCAL_TZ)


def parse_date(value: Any, fmt: str | None = None) -> datetime | None:
    if value in (None, ""):
        return None
    s = str(value).strip()
    try:
        if fmt:
            s2 = re.sub(r"\b(CDT|CST|CT)\b ?", "", s) if "%Z" in fmt else s
            return _local(datetime.strptime(s2, fmt.replace("%Z ", "").replace(" %Z", "")))
        try:
            return email.utils.parsedate_to_datetime(s)  # RSS (RFC 822)
        except (TypeError, ValueError):
            return _local(datetime.fromisoformat(s.replace("Z", "+00:00")))
    except ValueError:
        return None


def item_id(source_id: str, native: Any) -> str:
    return hashlib.sha256(f"{source_id}|{native}".encode()).hexdigest()[:16]


def make_item(src: Source, native: Any, title: str, text: str, link: str | None, published: datetime | None,
              retrieved: datetime, **extra) -> dict:
    title = clean(title)[:200]
    body = clean(text)
    excerpt = body if body and body != title else ""
    return {"item_id": item_id(src.id, native or link or title), "source_id": src.id, "title": title,
            "link": link if (link and link.startswith("http")) else (src.link or src.url),
            "published_at": published.astimezone(timezone.utc).isoformat() if published else None,
            "retrieved_at": retrieved.isoformat(), "excerpt": excerpt[:EXCERPT_CHARS], **extra}


# ---------------------------------------------------------------- parsers

def parse_json_list(src: Source, raw: bytes, retrieved: datetime) -> list[dict]:
    cfg = src.parse
    data = json.loads(raw)
    rows = dig(data, cfg.get("items")) if cfg.get("items") else data
    out = []
    for row in rows or []:
        if not _passes(row, cfg, src):
            continue
        text = clean(dig(row, cfg.get("text")))
        title = dig(row, cfg.get("title")) or text[:120]
        published = None
        if cfg.get("published_epoch_ms") and isinstance(dig(row, cfg["published_epoch_ms"]), (int, float)):
            published = datetime.fromtimestamp(dig(row, cfg["published_epoch_ms"]) / 1000, timezone.utc)
        elif cfg.get("published_regex"):
            m = re.search(cfg["published_regex"], text)
            if m:
                published = parse_date(f"{m.group(2)} {m.group(1)}", "%m/%d/%Y %I:%M %p")
        extra = {}
        for key, path in (("effective_from", cfg.get("effective_epoch_ms")), ("effective_until", cfg.get("until_epoch_ms"))):
            v = dig(row, path)
            if isinstance(v, (int, float)):
                extra[key] = datetime.fromtimestamp(v / 1000, timezone.utc).isoformat()
        lat, lon = dig(row, cfg.get("lat")), dig(row, cfg.get("lon"))
        if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
            extra.update(lat=round(lat, 4), lon=round(lon, 4))
        out.append(make_item(src, dig(row, cfg.get("id")), title, text, None, published, retrieved, **extra))
    return out


def _passes(row: dict, cfg: dict, src: Source) -> bool:
    for f in cfg.get("filters", []):
        v = str(dig(row, f["path"]) or "")
        if "not_in" in f and v in f["not_in"]:
            return False
        if "county_suffix" in f and not any(v.upper().endswith(" " + c) for c in f["county_suffix"]):
            return False
    if cfg.get("bbox") and src.area.get("bbox"):
        lat, lon = dig(row, cfg["bbox"]["lat"]), dig(row, cfg["bbox"]["lon"])
        s, w, n, e = src.area["bbox"]
        if not (isinstance(lat, (int, float)) and isinstance(lon, (int, float)) and s <= lat <= n and w <= lon <= e):
            return False
    return True


def parse_rss(src: Source, raw: bytes, retrieved: datetime) -> list[dict]:
    root = ElementTree.fromstring(raw)
    out = []
    for it in root.iter("item"):
        title, link = it.findtext("title") or "", (it.findtext("link") or "").strip()
        pub = parse_date(it.findtext("pubDate"))
        out.append(make_item(src, it.findtext("guid") or link, title, it.findtext("description") or "", link, pub, retrieved))
    return out


def _select_value(node, spec: str | None):
    if not spec:
        return None
    sel, _, attr = spec.partition("@")
    el = node.select_one(sel) if sel else node
    if el is None:
        return None
    if attr == "text" or not attr:
        return el.get_text(" ", strip=True)
    return el.get(attr)


def parse_css(src: Source, raw: bytes, retrieved: datetime) -> list[dict]:
    from bs4 import BeautifulSoup

    cfg = src.parse
    soup = BeautifulSoup(raw, "html.parser")
    out = []
    for node in soup.select(cfg["item"]):
        title = _select_value(node, cfg.get("title"))
        if not title:
            continue
        href = _select_value(node, cfg.get("link"))
        link = urljoin(src.url, href) if href else None
        published = parse_date(_select_value(node, cfg.get("date")), cfg.get("date_format"))
        date_only = bool(cfg.get("date_format")) and "%H" not in cfg["date_format"]
        out.append(make_item(src, link or title, title, _select_value(node, cfg.get("excerpt")) or "", link,
                             published, retrieved, undated=published is None, date_only=date_only))
    return out


def parse_labeled_blocks(src: Source, raw: bytes, retrieved: datetime) -> list[dict]:
    """KC Scout style: repeated <div><b>Label : </b>value</div> groups starting at start_label."""
    from bs4 import BeautifulSoup

    cfg = src.parse
    soup = BeautifulSoup(raw, "html.parser")
    container = soup.find("p") or soup
    blocks, cur = [], None
    for div in container.find_all("div", recursive=False) or container.find_all("div"):
        b = div.find("b")
        if not b:
            continue
        label = b.get_text(strip=True).rstrip(":").strip()
        b.extract()
        value = div.get_text(" ", strip=True)
        if label == cfg["start_label"]:
            cur = {}
            blocks.append(cur)
        if cur is not None:
            cur[label] = value
    fields = cfg["fields"]
    out, seen = [], set()
    for blk in blocks:
        title = blk.get(fields["title"], "")
        key = (title, blk.get(fields["published"]))
        if not title or key in seen:  # the page repeats its list for screen refresh
            continue
        seen.add(key)
        text = f"{blk.get(fields['type'], '')}: {blk.get(fields['text'], '')}"
        out.append(make_item(src, key, title, text, None,
                             parse_date(blk.get(fields["published"]), cfg.get("date_format")), retrieved))
    return out


def parse_page_text(src: Source, raw: bytes, retrieved: datetime) -> list[dict]:
    """One page-level item for pages whose entry markup is unknown (e.g. empty closings lists)."""
    from bs4 import BeautifulSoup

    cfg = src.parse
    text = BeautifulSoup(raw, "html.parser").get_text(" ", strip=True)
    start, end = text.find(cfg["region_start"]), text.find(cfg["region_end"])
    region = text[start:end] if 0 <= start < end else text
    if any(m.lower() in region.lower() for m in cfg.get("empty_markers", [])):
        return []
    return [make_item(src, hashlib.sha256(region.encode()).hexdigest(), src.name, region, src.url, None,
                      retrieved, undated=True)]


def fetch_kubra_county(src: Source, retrieved: datetime) -> tuple[list[dict], bytes]:
    """Evergy (Kubra StormCenter): currentState -> data path -> county report. All on kubra.io."""
    state = json.loads(http_get(src, src.url))
    data_path = (state.get("data") or {}).get("interval_generation_data")
    if not data_path:
        raise FetchError("no outage data path in currentState")
    raw = http_get(src, f"https://kubra.io/{data_path}/public/reports/{src.parse['report']}_report.json")
    counties = {c.upper() for c in src.area.get("counties", [])}
    out = []
    for st in (json.loads(raw).get("file_data") or {}).get("areas", []):
        for a in st.get("areas", []):
            name = (a.get("name") or "").upper()
            out_n = (a.get("cust_a") or {}).get("val") or 0
            served = a.get("cust_s") or 0
            if name not in counties or out_n < src.parse.get("min_customers_out", 1):
                continue
            pct = (a.get("percent_cust_a") or {}).get("val")
            etr = a.get("etr") if a.get("etr") not in (None, "ETR-EXP", "ETR-NULL") else None
            title = f"Evergy power outages: {name.title()} County, {out_n:,} of {served:,} customers out ({pct}%)"
            out.append(make_item(src, f"{name}|{st.get('name')}", title,
                                 title + (f"; estimated restoration {etr}" if etr else ""), None, None, retrieved,
                                 counties=[name.title()], customers_out=out_n))
    return out, raw


PARSERS = {"json_list": parse_json_list, "rss": parse_rss, "css": parse_css,
           "labeled_blocks": parse_labeled_blocks, "page_text": parse_page_text}


# ---------------------------------------------------------------- fetch with cache

_cache: dict[str, tuple[float, FeedResult]] = {}


def clear_cache() -> None:
    _cache.clear()
    _robots.clear()


def fetch(src: Source, now: datetime | None = None) -> FeedResult:
    hit = _cache.get(src.id)
    if hit and time.monotonic() - hit[0] < src.refresh_minutes * 60:
        return hit[1]
    now = now or utcnow()
    try:
        if src.parse.get("parser") == "kubra_county":
            items, raw = fetch_kubra_county(src, now)
        else:
            raw = http_get(src, src.url)
            items = PARSERS[src.parse["parser"]](src, raw, now)
        result = FeedResult(src, "ok", _recent(src, items, now)[:MAX_ITEMS], retrieved_at=now,
                            snapshot_hash=save_snapshot(raw))
    except PermissionError as e:
        result = FeedResult(src, "skipped", error=str(e), retrieved_at=now)
    except Exception as e:  # noqa: BLE001 - any failure is "unknown", never "nothing reported"
        log.warning("local source %s failed: %s", src.id, e)
        result = FeedResult(src, "unknown", error=f"{type(e).__name__}: {e}"[:200], retrieved_at=now)
    _cache[src.id] = (time.monotonic(), result)
    return result


def _recent(src: Source, items: list[dict], now: datetime) -> list[dict]:
    """Last 24 hours. Undated items are kept (marked); current-state feeds keep active conditions."""
    if src.current_state:
        return items
    keep = []
    today = now.astimezone(LOCAL_TZ).date()
    for it in items:
        pub = it.get("published_at")
        if pub is None:
            keep.append(it)
        elif it.get("date_only"):  # "October 9, 2026": keep today's and yesterday's
            if datetime.fromisoformat(pub).astimezone(LOCAL_TZ).date() >= today - timedelta(days=1):
                keep.append(it)
        elif now - datetime.fromisoformat(pub) <= MAX_AGE:
            keep.append(it)
    return keep

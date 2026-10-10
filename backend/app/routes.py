"""Route names: one canonical form for every way sources write them. Pure functions.

  I-35, IS 35, Interstate 35, I 35            -> I-35
  US-71, US 71, U.S. 71, 71 HWY (US routes)   -> US-71
  MO-152, MO 152, M-291, 291 HWY (MO routes)  -> MO-152, MO-291
  K-10, K 10, KS-10                           -> K-10
  RT J, Route J                               -> RT-J
"""
import re

# KC-area highways written as "NNN HWY" (KC Scout) that are US routes; the rest are Missouri routes.
US_ROUTES = {"24", "40", "50", "56", "69", "71", "73", "169"}

_PATTERNS = [
    # Case-sensitive on purpose: prose like "it is 35 degrees" or "join us 50" is not a route.
    (re.compile(r"\b(?:I|IS)[\s-]?(\d{1,3})(?=[NSEW]?\b)"), "I-{}"),  # MoDOT ramps: "IS70E"
    (re.compile(r"\bInterstate\s+(\d{1,3})\b", re.I), "I-{}"),
    (re.compile(r"\bU\.?S\.?(?:\s*(?:Hwy|Highway))?[\s-]?(\d{1,3})\b"), "US-{}"),
    (re.compile(r"\b(?:MO|M)[\s-](\d{1,3})\b"), "MO-{}"),
    (re.compile(r"\b(?:K|KS)[\s-](\d{1,3})\b"), "K-{}"),
    (re.compile(r"\b(?:RT|Route)[\s-]([A-Z]{1,2})\b"), "RT-{}"),
]
_HWY = re.compile(r"\b(\d{1,3})\s+(?:HWY|Highway)\b", re.I)


def normalize_route(name: str) -> str | None:
    """Canonical form of a single route name, or None if it isn't one."""
    found = routes_in_text(name)
    return sorted(found)[0] if found else None


def routes_in_text(text: str) -> set[str]:
    """Every route mentioned in free text, canonicalized."""
    if not text:
        return set()
    out = set()
    for pattern, fmt in _PATTERNS:
        for m in pattern.finditer(text):
            out.add(fmt.format(m.group(1).upper()))
    for m in _HWY.finditer(text):
        n = m.group(1)
        if f"I-{n}" not in out:
            out.add(f"US-{n}" if n in US_ROUTES else f"MO-{n}")
    return out


def normalize_routes(names) -> set[str]:
    out = set()
    for n in names or ():
        out |= routes_in_text(n) or {n.strip().upper()}
    return out


def primary_route(text: str) -> str | None:
    """The route an item is about: the first one named (e.g. "US 69 in both directions: Road
    closed. Between I-35 and ..." is about US-69, not I-35)."""
    best: tuple[int, str] | None = None
    for pattern, fmt in _PATTERNS:
        for m in pattern.finditer(text or ""):
            if best is None or m.start() < best[0]:
                best = (m.start(), fmt.format(m.group(1).upper()))
            break
    m = _HWY.search(text or "")
    if m and (best is None or m.start() < best[0]):
        n = m.group(1)
        best = (m.start(), f"US-{n}" if n in US_ROUTES else f"MO-{n}")
    return best[1] if best else None

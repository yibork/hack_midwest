"""Keyword pre-filter, in code, before any model sees an item. Pure."""
from ...routes import normalize_routes, routes_in_text

KEYWORDS = ("closed", "closure", "closing", "flood", "flooding", "high water", "ice", "icy", "snow", "crash",
            "outage", "power", "water main", "evacuat", "shelter", "school closed", "schools closed",
            "early dismissal", "dismiss", "cancel", "delay", "boil", "tornado", "storm")
MAX_ITEMS_PER_AGENT = 12  # one model call's output must fit in max_tokens


def score(item: dict, watched: set[str]) -> int:
    text = f"{item.get('title', '')} {item.get('excerpt', '')}"
    low = text.lower()
    kw = sum(1 for k in KEYWORDS if k in low)
    route_hit = bool(routes_in_text(text) & watched)
    return (3 if route_hit else 0) + kw if (kw or route_hit) else 0


def prefilter(items: list[dict], access_routes: list[str], commute_routes: list[str],
              limit: int = MAX_ITEMS_PER_AGENT) -> list[dict]:
    """Items that mention a keyword or a watched route, best first, at most `limit`."""
    watched = normalize_routes(access_routes) | normalize_routes(commute_routes)
    scored = [(score(it, watched), i, it) for i, it in enumerate(items)]
    return [it for s, _, it in sorted((x for x in scored if x[0] > 0), key=lambda x: (-x[0], x[1]))][:limit]

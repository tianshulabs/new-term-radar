"""Can a new site get onto page one? The other half of "do it or not".

Rising is not enough: a term whose first page is held by strong sites is a fight a new site
loses. For the table's rows that would be acted on (prioritize, then watch), up to `max_rows`
a run, this reads:

- Google's first page (DataForSEO through AIsa, about $0.002 a keyword): what kinds of pages
  hold it, and whether ads show (someone pays for that click: there is money in the term);
- the rating of each site there (GoAnyAPI, $0.002 a site, kept 30 days): `weak` counts the
  results on sites rated under 30, the slots a new site can take, and the weakest one is named;
- keyword difficulty (DataForSEO Labs, about $0.012 a call; often missing for a new term);
- with a GoAnyAPI key, who already builds for the term and who pays for it: how many pages
  Google holds with the term in their title from the last month (`built`, 4 credits) and the
  domains running Google ads on it (`paid`, Ads transparency, 4 credits).

`open`: three or more weak results, or difficulty under 20. `crowded`: no weak result and a
difficulty of 40 or more, or unknown. `mixed` in between. A crowded `prioritize` becomes
`watch`: still rising, but page one belongs to strong sites. Without a GoAnyAPI key there are
no ratings and only the difficulty speaks. In a name's first week page one is still forming
(news, the maker's own pages): read it again at the second reading.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

from . import aisa, goanyapi, snap

SERP_TOOL = "post_dataforseo_serp_google_organic_live"
KD_TOOL = "post_dataforseo_labs_google_keyword_overview_live"
DR_DAYS = 30
WORKERS = 4                 # ratings asked at once
VIDEO = ("youtube.com", "tiktok.com", "vimeo.com")
FORUM = ("reddit.com", "quora.com", "ycombinator.com", "stackoverflow.com", "stackexchange.com", "x.com", "facebook.com")
TOOL = re.compile(r"\b(generator|maker|editor|converter|creator|online|free|app|tool|detector|remover|upscaler|translator)\b", re.I)
LIST = re.compile(r"\b(best|top|alternatives?|vs\.?|compared?|review(?:s|ed)?)\b|\b\d+\s+\w+", re.I)


def page_kind(domain: str, url: str, title: str) -> str:
    """tool, list, article, video or forum, from the address and the title."""
    if domain.endswith(VIDEO):
        return "video"
    if domain.endswith(FORUM):
        return "forum"
    if LIST.search(title):
        return "list"
    path = re.sub(r"^https?://[^/]+", "", url).strip("/")
    return "tool" if TOOL.search(title) or not path else "article"


def page_one(keyword: str, location: int) -> tuple[dict, int]:
    """{"results": [{"domain", "url", "title", "position", "kind"}], "ads", "features"} and the cost."""
    data, cost = aisa.call([{"call_id": "serp", "tool": SERP_TOOL, "arguments": {"body": [{
        "keyword": keyword, "location_code": location, "language_code": "en", "depth": 10}]}}])
    items = (((data.get("serp") or {}).get("tasks") or [{}])[0].get("result") or [{}])[0].get("items") or []
    results = [{"domain": re.sub(r"^www\.", "", i.get("domain") or ""), "url": i.get("url") or "", "title": i.get("title") or "",
                "position": i.get("rank_group")} for i in items if i.get("type") == "organic"][:10]
    for r in results:
        r["kind"] = page_kind(r["domain"], r["url"], r["title"])
    return {"results": results, "ads": any(i.get("type") == "paid" for i in items),
            "features": sorted({i["type"] for i in items if i.get("type") not in ("organic", "paid")})}, cost


def difficulty(keywords: list[str], location: int) -> tuple[dict[str, int | None], int]:
    data, cost = aisa.call([{"call_id": "kd", "tool": KD_TOOL, "arguments": {"body": [{
        "keywords": keywords, "location_code": location, "language_code": "en"}]}}])
    items = ((((data.get("kd") or {}).get("tasks") or [{}])[0].get("result") or [{}])[0].get("items") or [])
    return {i["keyword"]: (i.get("keyword_properties") or {}).get("keyword_difficulty") for i in items}, cost


def ratings(domains: list[str], state: Path) -> tuple[dict[str, int | None], int]:
    """Domain rating per site from GoAnyAPI, kept DR_DAYS days in state/ratings.json.gz."""
    if not goanyapi.key():
        return {}, 0
    path = state / "ratings.json.gz"
    held = snap.load(path) or {}
    today = date.today()
    fresh = {d: v for d, v in held.items() if (today - date.fromisoformat(v["day"])).days < DR_DAYS}
    def one(d: str):
        try:
            return d, *goanyapi.call("dr", domain=d)
        except goanyapi.GoAnyError:
            return d, None, 0
    cost = 0
    with ThreadPoolExecutor(WORKERS) as pool:
        for d, data, spent in pool.map(one, sorted(set(domains) - set(fresh))):
            cost += spent
            if data is not None:
                fresh[d] = {"dr": data.get("domain_rating"), "day": today.isoformat()}
    snap.save(path, fresh)
    return {d: v["dr"] for d, v in fresh.items()}, cost


def supply(rows: list[dict]) -> tuple[int, list[str]]:
    """Adds `built` (pages titled with the term this month) and `paid` (domains advertising on it)."""
    if not goanyapi.key() or not rows:
        return 0, []

    def one(r: dict):
        out, cost, errors = {}, 0, []
        for field, fn in (("built", goanyapi.intitle), ("paid", goanyapi.advertisers)):
            try:
                out[field], spent = fn(r["phrase"])
                cost += spent
            except goanyapi.GoAnyError as exc:
                errors.append(f"{r['phrase']} ({field}): {exc}")
        return r, out, cost, errors
    total, errors = 0, []
    with ThreadPoolExecutor(WORKERS) as pool:
        for r, out, cost, errs in pool.map(one, rows):
            r["competition"].update(out)
            total, errors = total + cost, errors + errs
    return total, errors


def verdict(weak: int | None, kd: int | None) -> str:
    if (weak is not None and weak >= 3) or (kd is not None and kd < 20):
        return "open"
    if kd is None and weak in (0, None):
        return "unknown"
    if weak in (0, None) and kd is not None and kd >= 40:
        return "crowded"
    return "mixed"


def assess(rows: list[dict], cfg: dict, state: Path) -> tuple[int, list[str]]:
    """Adds `competition` to the rows that would be acted on and demotes a crowded prioritize. (cost, errors)"""
    c = cfg["competition"]
    picked = [r for r in rows if r.get("decision") == "prioritize"] + [r for r in rows if r.get("decision") == "watch"]
    picked = picked[:c["max_rows"]]
    if not picked:
        return 0, []
    cost, errors, pages = 0, [], {}
    for r in picked:
        try:
            pages[r["phrase"]], spent = page_one(r["phrase"], c["location_code"])
            cost += spent
        except aisa.AisaError as exc:
            errors.append(f"{r['phrase']}: {exc}")
    try:
        kds, spent = difficulty([r["phrase"] for r in picked], c["location_code"])
        cost += spent
    except aisa.AisaError as exc:
        kds = {}
        errors.append(f"difficulty: {exc}")
    dr, spent = ratings([x["domain"] for p in pages.values() for x in p["results"]], state)
    cost += spent
    for r in picked:
        p = pages.get(r["phrase"])
        if not p:
            continue
        rated = [dict(x, dr=dr.get(x["domain"])) for x in p["results"]]
        known = [x for x in rated if x["dr"] is not None]
        weak = sum(1 for x in known if x["dr"] < c["weak_dr"]) if dr else None
        weakest = min(known, key=lambda x: (x["dr"], x["position"])) if known else None
        kd = kds.get(r["phrase"])
        kinds = {}
        for x in rated:
            kinds[x["kind"]] = kinds.get(x["kind"], 0) + 1
        r["competition"] = {"verdict": verdict(weak, kd), "weak": weak, "kd": kd, "ads": p["ads"],
                            "kinds": kinds, "features": p["features"],
                            "weakest": {k: weakest[k] for k in ("domain", "dr", "position", "url")} if weakest else None}
        if r["decision"] == "prioritize" and r["competition"]["verdict"] == "crowded":
            r["decision"], r["demoted"] = "watch", True
    spent, failed = supply([r for r in picked if r.get("competition")])
    return cost + spent, errors + failed

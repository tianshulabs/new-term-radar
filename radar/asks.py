"""Signal ⑦: what people ask for on Reddit ("is there an AI tool that…").

A request is demand said out loud. Once a week (asks.weekday, Monday by default) this searches
Reddit for the ways people ask for a tool (`patterns`, $0.0019 a query through AIsa), keeps the
posts of the last week, and a small model sorts each: a request for something that does a job,
a complaint about a tool, a solution someone shares, or other. For requests and complaints it
writes the job as the words a person would search ("convert powerpoint slides to notes"). The
jobs are sized with Google Ads volumes and the ones with searches are checked for room on page
one; each of those becomes a lead. Most asks are too specific to have searches yet ("add b roll
automatically"); the best scored of those are listed too, as ideas rather than keywords.

Posts are read for research: quote them, do not republish them.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

from . import aisa, competition, snap, social, usecases
from .i18n import t

CHUNK = 20
SYSTEM = (
    "You read Reddit posts for a researcher who builds small AI tools. For each post decide its kind: 'request' "
    "(someone asks for a tool or a way to get a job done), 'complaint' (someone is unhappy with a tool or a manual "
    "process), 'solution' (someone shares a tool or a method) or 'other'. For a request or a complaint, write the "
    "JOB as the 2 to 6 words an ordinary person would type into Google to find a tool for it, without brand names "
    "('convert powerpoint slides to notes', 'add b roll automatically', 'reconstruct audio frequencies'). Say "
    "whether an AI tool could plausibly do the job (ai: true or false).\n"
    "Only real jobs a person needs done in their work or life count. Use 'other' for: playing a video game "
    "(automating things inside Factorio, RimWorld or any game), jokes, satire and circlejerk subreddits, opinions "
    "or debates about AI in general, and complaints with no task behind them.\n"
    "Answer with JSON only, one object per post, in the same order: "
    "[{\"i\": 0, \"kind\": \"request\", \"job\": \"...\", \"ai\": true}, ...]"
)


def parse(answer: str, n: int) -> dict[int, dict]:
    m = re.search(r"\[.*\]", answer, re.S)
    try:
        items = json.loads(m.group(0)) if m else []
    except ValueError:
        return {}
    out = {}
    for it in items if isinstance(items, list) else []:
        try:
            i = int(it["i"])
        except (KeyError, TypeError, ValueError):
            continue
        job = " ".join(str(it.get("job") or "").lower().split())
        if 0 <= i < n and it.get("kind") in ("request", "complaint") and it.get("ai") and 2 <= len(job.split()) <= 6:
            out[i] = {"kind": it["kind"], "job": job}
    return out


def within_window(created: float, window: str, now: float | None = None) -> bool:
    seconds = {"hour": 3600, "day": 86400, "week": 7 * 86400, "month": 31 * 86400, "year": 366 * 86400}.get(window)
    if seconds is None:
        return True
    now = datetime.now(timezone.utc).timestamp() if now is None else now
    return created > 0 and now - seconds <= created <= now


def collect(c: dict) -> tuple[list[dict], int, list[str]]:
    """This week's posts for every pattern, once each, above the score floor."""
    posts, cost, errors, seen = [], 0, [], set()
    stale = set()
    for q in c["patterns"]:
        try:
            got, spent = social.reddit(q, time=c["time"], sort="top", limit=25)
        except aisa.AisaError as exc:
            errors.append(f"{q}: {exc}")
            continue
        cost += spent
        for p in got:
            if not within_window(p.get("created", 0), c["time"]):
                stale.add(p["id"])
                continue
            if p["id"] not in seen and p["score"] >= c["min_score"] and "circlejerk" not in p["subreddit"].lower():
                seen.add(p["id"])
                posts.append(p)
    if stale:
        errors.append(f"Reddit returned {len(stale)} posts outside the requested {c['time']} window or without valid dates; excluded")
    return posts, cost, errors


def classify(model: str, posts: list[dict]) -> tuple[list[dict], list[str]]:
    """The posts that ask for a job an AI tool could do, each with its job phrase."""
    kept, errors = [], []
    for start in range(0, len(posts), CHUNK):
        chunk = posts[start:start + CHUNK]
        user = json.dumps([{"i": i, "title": p["title"], "text": p["text"][:300], "subreddit": p["subreddit"]}
                           for i, p in enumerate(chunk)], ensure_ascii=False)
        try:
            for i, v in parse(aisa.chat(model, SYSTEM, user), len(chunk)).items():
                kept.append({**chunk[i], **v})
        except aisa.AisaError as exc:
            errors.append(str(exc)[:160])
    return kept, errors


def asks(cfg: dict, state: Path, today: date | None = None) -> dict:
    c = cfg["asks"]
    today = today or date.today()
    posts, cost, errors = collect(c)
    kept, failed = classify(c["model"], posts)
    jobs: dict[str, dict] = {}
    for p in kept:
        j = jobs.setdefault(p["job"], {"phrase": p["job"], "kind": "generic", "posts": [], "score": 0, "subreddits": []})
        j["posts"].append(p)
        j["score"] += p["score"]
        if p["subreddit"] not in j["subreddits"]:
            j["subreddits"].append(p["subreddit"])
    vol, spent = usecases.volumes(sorted(jobs), c["location_code"]) if jobs else ({}, 0)
    cost += spent
    rows = sorted((dict(j, volume=vol.get(k, {}).get("volume", 0), decision="watch") for k, j in jobs.items()
                   if vol.get(k, {}).get("volume", 0) >= c["min_volume"]), key=lambda r: (-r["score"], -r["volume"]))[:c["max_leads"]]
    spent, comp_errors = competition.assess(rows, cfg, state) if rows and cfg["competition"]["max_rows"] else (0, [])
    cost += spent
    path = state / "asks.json.gz"
    seen: dict[str, str] = snap.load(path) or {}
    for r in rows:
        r["again"] = r["phrase"] in seen
        seen.setdefault(r["phrase"], today.isoformat())
    snap.save(path, seen)
    terms = [{"term": r["phrase"], "recent": len(r["posts"]),
              "examples": [f"r/{p['subreddit']} ({p['score']}): {p['title'][:90]}" for p in r["posts"][:2]],
              "note": t("note_ask", n=len(r["posts"]), subs=", ".join(f"r/{s}" for s in r["subreddits"][:3]))}
             for r in rows if not r["again"]]
    quiet = sorted((dict(j, volume=vol.get(k, {}).get("volume", 0)) for k, j in jobs.items()
                    if vol.get(k, {}).get("volume", 0) < c["min_volume"]), key=lambda j: (-len(j["posts"]), -j["score"]))[:c["max_leads"]]   # many askers over one viral thread
    return {"read": len(posts), "asks": len(kept), "unsized": len(jobs) - len(rows), "rows": rows, "quiet": quiet,
            "terms": terms, "errors": errors + failed + comp_errors, "cost_micros": cost}

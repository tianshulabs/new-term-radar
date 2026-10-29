"""new-term-radar: terms that are starting to surge, earliest signal first.

  python -m radar daily    [--config config.json] [--state state] [--out report.md] [--skip x,hf,...] [--new-sites] [--mail]
  python -m radar sources  [--config config.json] [--state state] [--out sources.md] [--mail]

Signals:
  ① x         names the accounts you follow said for the first time (AIsa, or RADAR_X_POSTS_FILE)
  ① hf        models new to Hugging Face's trending top 50
  ② breakout  one new GitHub repo taking off on its own
  ③ sitemaps  pages other sites added since the last run
  ③ newsites  domains registered in the last 90 days that already rank (weekly; AIsa)
  ⑥ revenue   products people pay for, growing fastest or newly listed (weekly; TrustMRR key)
  ⑦ asks      what people ask for on Reddit ("is there an AI tool that…"), sized (weekly; AIsa)
  ⑧ sellers   sites that just put up a payment link: Stripe, Polar, Lemon Squeezy, Gumroad, Creem (weekly; AIsa)
  ④ gsc       queries your own site got impressions for, for the first time (optional)
  ⑤ review    leads from 14 days ago checked in Google Trends; hit rate per signal (AIsa)
  usecases    the generic terms behind today's names, sized against a benchmark keyword (AIsa)

  python -m radar keywords "name one" "name two" [--repo "name one=owner/repo"] [--json]
  python -m radar leads    [--days 7] [--json]
  python -m radar pains    "keyword" [--extension ID] [--json]   what people complain about for a kind of tool
  python -m radar mcp      the MCP server on stdio (needs the `mcp` package)

`keywords` builds the keyword table for names you give it, now (about $0.05 a name plus
$0.09 a call for the volume lookup). `leads` lists what the radar has kept, with the
review's verdicts and the hit rate per signal; it costs nothing.

`sources` runs only ① (they change by the hour) and writes a report only when there is a
name or an error. `daily` runs everything; give it `--skip x,hf` when `sources` runs
apart, and it lists the ① leads of the last day from the database instead.

A small model scores every term: the name of a specific new thing, or a common word?
Names lead the report and are kept in state/radar.db for the review.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from . import aisa, asks, breakout, config, gsc, new_sites, notify, pains, report, research, review, revenue, sellers, sitemaps, sources, store, usecases
from .i18n import t

SKIPPABLE = {"x", "hf", "breakout", "sitemaps", "newsites", "revenue", "asks", "sellers", "gsc", "review", "judge", "usecases", "tutorials", "research"}


def run(fn, skip: bool = False, reason: str = "") -> dict:
    """One signal failing still leaves a report: its error takes its place.

    `reason` is a one-line explanation when we auto-skip (no key, free path, etc.).
    """
    if skip:
        out: dict = {"skipped": True}
        if reason:
            out["reason"] = reason
        return out
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {' '.join(str(exc).split())[:160]}"}


# Signals that need AIsa's tool router (not just chat). Without a key they auto-skip.
_AISA_TOOL = frozenset({"newsites", "asks", "sellers", "review", "usecases", "tutorials"})
# Signals that need chat (AIsa or RADAR_LLM_*).
_AISA_CHAT = frozenset({"judge", "research", "tutorials", "asks", "sellers", "usecases"})


def _auto_reason(name: str) -> str:
    """Why a paid signal should auto-skip, or empty if it can run."""
    if name in _AISA_TOOL and not aisa.has_key():
        return "no AIsa key"
    if name in _AISA_CHAT and not aisa.llm_ready():
        return "no AIsa key and no RADAR_LLM_BASE_URL"
    return ""


def _note_skip(name: str, reason: str) -> None:
    if reason:
        print(f"skip {name}: {reason}", file=sys.stderr)


def _finish(args, text: str, subject: str) -> None:
    if args.out:
        Path(args.out).write_text(text)
    print(text)
    if args.mail:
        print(notify.send(subject, text))


def cmd_sources(args, cfg: dict, state: Path) -> int:
    blocks = {"x": run(lambda: sources.x_new(cfg, state), "x" in args.skip),
              "hf": run(lambda: sources.hf_new(cfg, state), "hf" in args.skip)}
    # Judge: empty model = skip; auto-clear when no chat backend.
    jreason = _auto_reason("judge")
    if "judge" in args.skip or jreason:
        if jreason and "judge" not in args.skip:
            _note_skip("judge", jreason)
        cfg["judge"]["model"] = ""
    lead, judged = report.leads(cfg, blocks)
    conn = store.connect(state)
    extra = {"judge": judged, "record": run(lambda: {"recorded": store.record(conn, report.to_record(cfg, lead))})}
    errors = any(b.get("error") for b in (*blocks.values(), *extra.values()))
    if report.named(cfg, lead) or errors:
        researched = run(lambda: research.enrich(cfg, lead))
        _finish(args, research.render(researched) + report.sources(cfg, lead, blocks, extra), f"New-term radar · sources {date.today()}")
    else:
        print(t("sources_quiet") + "; ".join(f"{k} {v.get('posts', v.get('models', t('dash')))}" for k, v in blocks.items()))
    return 0


def cmd_daily(args, cfg: dict, state: Path) -> int:
    overview = cfg.get("_overview", False)
    weekly = args.new_sites or date.today().weekday() == cfg["new_sites"]["weekday"]
    # Auto-skip paid AIsa signals when there is no key, with a one-line reason.
    # Free signals (hf, breakout, sitemaps) and file-backed X still run.
    jreason = _auto_reason("judge")
    if "judge" in args.skip or jreason:
        if jreason and "judge" not in args.skip:
            _note_skip("judge", jreason)
        cfg["judge"]["model"] = ""
    rreason = _auto_reason("research")
    if "research" in args.skip or rreason:
        if rreason and "research" not in args.skip:
            _note_skip("research", rreason)
        cfg["research"]["enabled"] = False

    def _paid(name: str, otherwise: bool) -> tuple[bool, str]:
        """(skip?, reason). `otherwise` is True when the schedule/flag already skips it."""
        if otherwise:
            return True, ""
        if name in args.skip:
            return True, ""
        reason = _auto_reason(name)
        if reason:
            _note_skip(name, reason)
            return True, reason
        return False, ""

    ns_skip, ns_why = _paid("newsites", overview or not weekly)
    rv_day = args.revenue or date.today().weekday() == cfg["revenue"]["weekday"]
    ak_day = args.asks or date.today().weekday() == cfg["asks"]["weekday"]
    sl_day = args.sellers or date.today().weekday() == cfg["sellers"]["weekday"]
    ak_skip, ak_why = _paid("asks", overview or not ak_day)
    sl_skip, sl_why = _paid("sellers", overview or not sl_day)
    blocks = {
        "x": run(lambda: sources.x_new(cfg, state), "x" in args.skip),
        "hf": run(lambda: sources.hf_new(cfg, state), "hf" in args.skip),
        "breakout": run(lambda: breakout.breakouts(cfg, state), "breakout" in args.skip),
        "sitemap": run(lambda: sitemaps.diff(cfg, state), overview or "sitemaps" in args.skip),
        "newsite": run(lambda: new_sites.new_sites(cfg, state), ns_skip, ns_why),
        "revenue": run(lambda: revenue.products(cfg, state), overview or "revenue" in args.skip or not rv_day),
        "ask": run(lambda: asks.asks(cfg, state), ak_skip, ak_why),
        "seller": run(lambda: sellers.sellers(cfg, state), sl_skip, sl_why),
    }
    found = run(lambda: gsc.gsc_new(cfg), overview or "gsc" in args.skip)
    lead, judged = report.leads(cfg, blocks)
    conn = store.connect(state)
    extra = {"judge": judged, "record": run(lambda: {"recorded": store.record(conn, report.to_record(cfg, lead))}, overview)}
    # What the viewers of a widely shared walkthrough ask for: the one-click tool behind a ⚡ lead.
    tu_skip, tu_why = _paid("tutorials", overview or not cfg["tutorials"]["max_leads"])
    extra["tutorials"] = run(lambda: pains.tutorial_asks(cfg, report.named(cfg, lead)), tu_skip, tu_why)
    # With ① run apart, the daily report lists what that job kept since yesterday.
    extra["recent_sources"] = run(lambda: {"rows": store.recent_sources(conn)}, overview or not {"x", "hf"} <= args.skip)
    # The names to read for their generic terms: things, not words that surged (usecases.names_to_read).
    names = usecases.names_to_read(report.to_record(cfg, lead), extra["recent_sources"].get("rows", []))
    repos = {r["term"]: r["repo"] for r in lead if r.get("repo")}
    about = {r["term"]: r["about"] for r in lead if r.get("about")}      # a paying product's own description
    uc_skip, uc_why = _paid("usecases", overview or not cfg["usecases"]["per_day"])
    extra["usecases"] = run(lambda: usecases.run(cfg, state, list(dict.fromkeys(names)), repos, texts=about),
                            uc_skip, uc_why)
    run(lambda: store.record(conn, report.usecase_leads(extra["usecases"])), overview)
    rev_skip, rev_why = _paid("review", overview)
    rev = run(lambda: review.review(cfg, conn), rev_skip, rev_why)
    summ = run(lambda: store.summary(conn))
    original = report.daily(cfg, lead, blocks, found, rev, summ, extra)
    researched = run(lambda: research.enrich(cfg, lead))
    text = research.render(researched) + original
    if args.out:
        Path(args.out).with_suffix(".json").write_text(json.dumps(
            {"mode": "overview" if overview else "daily", "leads": lead, **blocks, "gsc": found,
             "review": rev, "summary": summ, "judge": judged, "research": researched, "keywords": extra["usecases"], "tutorials": extra["tutorials"], "original_md": original}, ensure_ascii=False, indent=1, default=str))
    _finish(args, text, f"New-term radar{' · current overview' if overview else ''} {date.today()}")
    return 0


def cmd_enrich(args, cfg: dict) -> int:
    source = Path(args.input)
    payload = json.loads(source.read_text())
    original = payload.get("original_md")
    if original is None:
        original = source.with_suffix(".md").read_text()
    researched = run(lambda: research.enrich(cfg, payload["leads"]))
    payload.update(research=researched, original_md=original)
    if args.out:
        Path(args.out).with_suffix(".json").write_text(json.dumps(payload, ensure_ascii=False, indent=1))
    _finish(args, research.render(researched) + original, f"New-term radar · research {date.today()}")
    return 0


def keyword_table(cfg: dict, state: Path, names: list[str], repos: dict[str, str]) -> tuple[dict, str]:
    """The keyword table for names someone asked about: (the data, the table in Markdown)."""
    uc = usecases.run(cfg, state, [n.lower().strip() for n in names if n.strip()], {k.lower(): v for k, v in repos.items()}, on_request=True)
    return uc, "\n".join(report.keyword_table(cfg, uc)).strip() + "\n"


def kept_leads(state: Path, days: int) -> dict:
    conn = store.connect(state)
    return {"leads": store.leads(conn, days), "hit_rates": store.summary(conn)}


def cmd_keywords(args, cfg: dict, state: Path) -> int:
    repos = dict(r.split("=", 1) for r in args.repo)
    uc, text = keyword_table(cfg, state, args.names, repos)
    out = json.dumps(uc, ensure_ascii=False, indent=1, default=str) if args.json else text
    if args.out:
        Path(args.out).write_text(out)
    print(out)
    return 0


def cmd_pains(args, cfg: dict) -> int:
    keyword = " ".join(args.names).strip()
    if not keyword:
        raise SystemExit('pains: give a keyword, e.g. python -m radar pains "ai headshot"')
    result = pains.run(cfg, keyword, args.extension)
    out = json.dumps(result, ensure_ascii=False, indent=1, default=str) if args.json else report.pains(result)
    if args.out:
        Path(args.out).write_text(out)
    print(out)
    return 0


def cmd_leads(args, state: Path) -> int:
    data = kept_leads(state, args.days)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=1, default=str))
        return 0
    for r in data["leads"]:
        verdict = f" · {r['verdict']}" + (f" ({r['days_ahead']} days ahead)" if r["days_ahead"] is not None else "") if r["verdict"] else ""
        print(f"{r['first_seen']}  {r['term']}  [{', '.join(r['signals'])}]{verdict}  {r['note']}")
    for kind, s in data["hit_rates"]["by_signal"].items():
        print(f"hit rate {kind}: {s['hit']}/{s['checked']}")
    print(f"{len(data['leads'])} leads in the last {args.days} days; {data['hit_rates']['pending']} awaiting review")
    return 0


def serve_mcp() -> int:
    """`new-term-radar mcp`: the stdio MCP server under the package's own name, so a
    client that only knows the PyPI name can start it (`uvx --with mcp new-term-radar mcp`)."""
    try:
        from . import mcp_server
    except ImportError:
        print('The MCP server needs the `mcp` package: pip install "new-term-radar[mcp]"', file=sys.stderr)
        return 1
    mcp_server.main()
    return 0


def main(argv: list[str] | None = None) -> int:
    if (sys.argv[1:] if argv is None else argv)[:1] == ["mcp"]:
        return serve_mcp()
    prog = "python -m radar" if __package__ == "radar" else "new-term-radar"  # installed, the package has another name
    p = argparse.ArgumentParser(prog=prog, description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["daily", "sources", "keywords", "leads", "pains", "enrich"])
    p.add_argument("names", nargs="*", help="for `keywords`: the names to read")
    p.add_argument("--repo", action="append", default=[], metavar="NAME=OWNER/REPO", help="for `keywords`: a name's GitHub repository")
    p.add_argument("--days", type=int, default=7, help="for `leads`: how far back")
    p.add_argument("--json", action="store_true", help="for `keywords` and `leads`: print JSON")
    p.add_argument("--input", help="enrich only: existing report JSON, with Markdown beside it")
    p.add_argument("--config", default="config.json")
    p.add_argument("--state", default="state", help="directory for radar.db and the snapshots")
    p.add_argument("--out", help="write the report (Markdown) here; `daily` also writes a .json beside it")
    p.add_argument("--skip", default="", help="comma-separated: " + ",".join(sorted(SKIPPABLE)))
    p.add_argument("--new-sites", action="store_true", help="run the weekly new-site signal today")
    p.add_argument("--revenue", action="store_true", help="run the weekly revenue signal today (needs TRUSTMRR_API_KEY)")
    p.add_argument("--asks", action="store_true", help="run the weekly Reddit asks signal today")
    p.add_argument("--sellers", action="store_true", help="run the weekly new-sellers signal today")
    p.add_argument("--extension", action="append", default=[], metavar="ID", help="for `pains`: a Chrome extension to read the reviews of")
    p.add_argument("--overview", action="store_true", help="daily only: current X/HF/GitHub candidates, without changing discovery history")
    p.add_argument("--mail", action="store_true", help="mail the report (see radar/notify.py)")
    args = p.parse_args(argv)
    if args.overview and args.command != "daily":
        p.error("--overview requires daily")
    if args.command == "enrich" and not args.input:
        p.error("enrich requires --input")
    if args.input and args.command != "enrich":
        p.error("--input requires enrich")
    args.skip = {s for s in args.skip.split(",") if s}
    unknown = args.skip - SKIPPABLE
    if unknown:
        p.error(f"--skip: unknown {sorted(unknown)}")
    state = Path(args.state)
    if args.command == "leads":
        return cmd_leads(args, state)
    if args.command == "keywords" and not args.names:
        p.error("keywords: give at least one name")
    cfg = config.load(Path(args.config))
    cfg["_overview"] = args.overview
    if "judge" in args.skip:
        cfg["judge"]["model"] = ""
    if "research" in args.skip:
        cfg["research"]["enabled"] = False
    if args.command == "enrich":
        return cmd_enrich(args, cfg)
    state.mkdir(parents=True, exist_ok=True)
    if args.command == "keywords":
        return cmd_keywords(args, cfg, state)
    if args.command == "pains":
        return cmd_pains(args, cfg)
    return cmd_sources(args, cfg, state) if args.command == "sources" else cmd_daily(args, cfg, state)


if __name__ == "__main__":
    sys.exit(main())

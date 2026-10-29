"""Pre-bet market scan: collects odds, line movement and news for upcoming games.

Writes data/latest.json for the analysis step and stores a timestamped odds
snapshot per league so the next run can measure line movement.

Usage: python3 scan/scan.py [--hours 36] [--leagues nfl,nba,epl] [--reserve 25]
                            [--cards-hours 24] [--props-hours 24] [--props-max 3]
Requires ODDS_API_KEY (https://the-odds-api.com). Standard library only.

Free-plan budget (500 credits/month): the events list is free, so a league is
only priced (3 credits: 3 markets x 1 region) when it has a game in the window,
and pricing stops once remaining credits fall to the reserve kept for settling.
EPL "player to be carded" odds cost 1 credit per match and are only fetched for
matches kicking off within --cards-hours (off by default; the daily run uses 24
so each match is priced once).

Player props (games within --props-hours, default 24; 0 turns them off) cost
1 credit per market per game, for at most --props-max (default 2) games per
league, soonest first: NBA player points and
NFL anytime touchdown scorer, and EPL anytime goalscorer. UK books don't list
NBA/NFL props in the feed, so those are priced from US books as a stand-in and
flagged "us_proxy"; EPL goalscorer uses UK books and shares the card request.
"""

import argparse
import json
import os
import ssl
import statistics
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SNAPSHOTS = DATA / "snapshots"

# odds-api sport key -> (display name, ESPN path, has ESPN injuries endpoint)
LEAGUES = {
    "americanfootball_nfl": ("NFL", "football/nfl", True),
    "basketball_nba": ("NBA", "basketball/nba", True),
    "soccer_epl": ("EPL", "soccer/eng.1", False),
}

ODDS_URL = "https://api.the-odds-api.com/v4/sports/{sport}/odds"
EVENTS_URL = "https://api.the-odds-api.com/v4/sports/{sport}/events"  # costs 0 credits
EVENT_ODDS_URL = "https://api.the-odds-api.com/v4/sports/{sport}/events/{event}/odds"
CARD_MARKET = "player_to_receive_card"
MIN_CARD_PRICE = -2000  # decimal 1.05; exchanges list 1.01 when there is no liquidity
# sport -> (prop market, region); US books stand in where UK books list none
PROP_MARKETS = {
    "basketball_nba": ("player_points", "us"),
    "americanfootball_nfl": ("player_anytime_td", "us"),
    "soccer_epl": ("player_goal_scorer_anytime", "uk"),
}
ESPN_URL = "https://site.api.espn.com/apis/site/v2/sports/{path}/{endpoint}"


def _ssl_context():
    bundle = os.environ.get("SSL_CERT_FILE") or "/root/.ccr/ca-bundle.crt"
    return ssl.create_default_context(cafile=bundle if os.path.exists(bundle) else None)


def get_json(url, params=None):
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "pre-bet-scan/1.0"})
    with urllib.request.urlopen(req, timeout=30, context=_ssl_context()) as resp:
        return json.load(resp), dict(resp.headers)


def american_to_prob(price):
    return 100 / (price + 100) if price > 0 else -price / (-price + 100)


def prob_to_american(p):
    return round(-100 * p / (1 - p)) if p >= 0.5 else round(100 * (1 - p) / p)


def _main_line(cands):
    """Best price on the line most books offer, ignoring alternate lines.

    Books list alternate spreads/totals (e.g. +8 at -667) alongside the main
    line; comparing points across those would pick unbettable junk prices.
    """
    points = [c["point"] for c in cands]
    mid = statistics.median(points)
    main = max(set(points), key=lambda p: (points.count(p), -abs(p - mid)))
    return max((c for c in cands if c["point"] == main), key=lambda c: c["price"])


def consensus(event):
    """Median line across bookmakers, no-vig moneyline probabilities and best prices."""
    h2h, spreads, totals = {}, {}, []
    offers = {"h2h": {}, "spreads": {}, "totals": {}}
    for book in event.get("bookmakers", []):
        title = book.get("title") or book.get("key")
        for market in book.get("markets", []):
            outcomes = market.get("outcomes", [])
            if market["key"] not in offers:
                continue
            for o in outcomes:
                cand = {"price": o["price"], "book": title}
                if "point" in o:
                    cand["point"] = o["point"]
                elif market["key"] != "h2h":
                    continue
                offers[market["key"]].setdefault(o["name"], []).append(cand)
            if market["key"] == "h2h":
                for o in outcomes:
                    h2h.setdefault(o["name"], []).append(american_to_prob(o["price"]))
            elif market["key"] == "spreads":
                for o in outcomes:
                    if "point" in o:
                        spreads.setdefault(o["name"], []).append(o["point"])
            elif market["key"] == "totals":
                for o in outcomes:
                    if o["name"] == "Over" and "point" in o:
                        totals.append(o["point"])

    best = {
        key: {name: (max(c, key=lambda x: x["price"]) if key == "h2h" else _main_line(c))
              for name, c in by_name.items()}
        for key, by_name in offers.items()
    }
    out = {"books": len(event.get("bookmakers", [])), "best_price": best}
    if h2h:
        raw = {name: statistics.median(ps) for name, ps in h2h.items()}
        overround = sum(raw.values())
        out["moneyline"] = {
            name: {
                "consensus_price": prob_to_american(p),
                "no_vig_prob": round(p / overround, 4),
            }
            for name, p in raw.items()
        }
        out["hold_pct"] = round((overround - 1) * 100, 2)
    if spreads:
        out["spread"] = {name: statistics.median(pts) for name, pts in spreads.items()}
    if totals:
        out["total"] = statistics.median(totals)
    return out


def yes_prices(event, key):
    """Best 'Yes' price per player for a player market, with the median implied prob."""
    offers = {}
    for book in event.get("bookmakers", []):
        for market in book.get("markets", []):
            if market["key"] != key:
                continue
            for o in market.get("outcomes", []):
                if o.get("name") != "Yes" or not o.get("description") or o["price"] < MIN_CARD_PRICE:
                    continue
                offers.setdefault(o["description"], []).append(
                    {"price": o["price"], "book": book.get("title") or book.get("key")})
    return {
        player: {
            "best": max(c, key=lambda x: x["price"]),
            "books": len(c),
            "median_implied_prob": round(statistics.median(american_to_prob(x["price"]) for x in c), 4),
        }
        for player, c in sorted(offers.items())
    }


def card_prices(event):
    return yes_prices(event, CARD_MARKET)


def over_under_prices(event, key):
    """Per player: the main line, best Over and Under on it, and the median no-vig Over prob."""
    offers = {}
    for book in event.get("bookmakers", []):
        title = book.get("title") or book.get("key")
        for market in book.get("markets", []):
            if market["key"] != key:
                continue
            for o in market.get("outcomes", []):
                if o.get("description") and "point" in o and o.get("name") in ("Over", "Under"):
                    offers.setdefault(o["description"], []).append(
                        {"side": o["name"], "price": o["price"], "point": o["point"], "book": title})
    out = {}
    for player, c in sorted(offers.items()):
        line = _main_line([x for x in c if x["side"] == "Over"] or c)["point"]
        on_line = [x for x in c if x["point"] == line]
        sides = {s: [x for x in on_line if x["side"] == s] for s in ("Over", "Under")}
        if not sides["Over"] or not sides["Under"]:
            continue
        pairs = []
        for o in sides["Over"]:
            u = next((x for x in sides["Under"] if x["book"] == o["book"]), None)
            if u:
                po, pu = american_to_prob(o["price"]), american_to_prob(u["price"])
                pairs.append(po / (po + pu))
        out[player] = {
            "line": line,
            "Over": {k: v for k, v in max(sides["Over"], key=lambda x: x["price"]).items() if k != "side"},
            "Under": {k: v for k, v in max(sides["Under"], key=lambda x: x["price"]).items() if k != "side"},
            "books": len({x["book"] for x in on_line}),
            "median_no_vig_over_prob": round(statistics.median(pairs), 4) if pairs else None,
        }
    return out


def fetch_player_markets(sport, api_key, games, now, reserve, remaining, cards_horizon, props_horizon,
                         props_max):
    """Add card and prop odds to games in their windows (1 credit per market returned)."""
    prop_key, prop_region = PROP_MARKETS[sport]
    props_left = props_max
    for g in sorted(games, key=lambda g: g["kickoff_utc"]):
        start = datetime.fromisoformat(g["kickoff_utc"].replace("Z", "+00:00"))
        by_region = {}
        if sport == "soccer_epl" and cards_horizon and start <= now + cards_horizon:
            by_region.setdefault("uk", []).append(CARD_MARKET)
        if props_horizon and start <= now + props_horizon and props_left > 0:
            by_region.setdefault(prop_region, []).append(prop_key)
            props_left -= 1
        for region, markets in by_region.items():
            if remaining is not None and float(remaining) - len(markets) < reserve:
                g["props_skipped" if prop_key in markets else "cards_skipped"] = f"credit reserve: {remaining} left"
                continue
            try:
                ev, headers = get_json(EVENT_ODDS_URL.format(sport=sport, event=g["id"]), {
                    "apiKey": api_key, "regions": region, "markets": ",".join(markets),
                    "oddsFormat": "american",
                })
            except Exception as e:
                g["props_skipped" if prop_key in markets else "cards_skipped"] = f"error: {e}"
                continue
            remaining = headers.get("x-requests-remaining", remaining)
            if CARD_MARKET in markets:
                g["cards"] = card_prices(ev)
            if prop_key in markets:
                prices = (over_under_prices(ev, prop_key) if prop_key == "player_points"
                          else yes_prices(ev, prop_key))
                g.setdefault("props", {})[prop_key] = {"region": region, "us_proxy": region != "uk",
                                                       "players": prices}
    return remaining


def movement(current, previous):
    """Change in consensus numbers since the previous snapshot (None if unseen)."""
    if not previous:
        return None
    delta = {}
    for team, pts in current.get("spread", {}).items():
        before = previous.get("spread", {}).get(team)
        if before is not None and before != pts:
            delta.setdefault("spread", {})[team] = round(pts - before, 2)
    if current.get("total") is not None and previous.get("total") is not None:
        if current["total"] != previous["total"]:
            delta["total"] = round(current["total"] - previous["total"], 2)
    for team, ml in current.get("moneyline", {}).items():
        before = previous.get("moneyline", {}).get(team)
        if before:
            shift = round((ml["no_vig_prob"] - before["no_vig_prob"]) * 100, 2)
            if shift:
                delta.setdefault("moneyline_prob_pts", {})[team] = shift
    return delta


def latest_snapshot(sport):
    files = sorted((SNAPSHOTS / sport).glob("*.json"))
    return json.loads(files[-1].read_text()) if files else {}


def fetch_news(path, has_injuries, teams):
    news = {"headlines": [], "injuries": []}
    try:
        data, _ = get_json(ESPN_URL.format(path=path, endpoint="news"), {"limit": 50})
        for a in data.get("articles", []):
            news["headlines"].append({
                "headline": a.get("headline"),
                "description": a.get("description"),
                "published": a.get("published"),
            })
    except Exception as e:  # news is best-effort; odds are the core input
        news["error"] = f"news: {e}"
    if has_injuries:
        try:
            data, _ = get_json(ESPN_URL.format(path=path, endpoint="injuries"))
            for team in data.get("injuries", []):
                name = team.get("displayName", "")
                if not any(name in t or t in name for t in teams):
                    continue
                for inj in team.get("injuries", []):
                    athlete = inj.get("athlete", {})
                    news["injuries"].append({
                        "team": name,
                        "player": athlete.get("displayName"),
                        "position": athlete.get("position", {}).get("abbreviation"),
                        "status": inj.get("status"),
                        "detail": inj.get("shortComment") or inj.get("longComment"),
                        "date": inj.get("date"),
                    })
        except Exception as e:
            news["error"] = (news.get("error", "") + f" injuries: {e}").strip()
    return news


def _iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def scan_league(sport, api_key, now, horizon, reserve=0, cards_horizon=None, props_horizon=None,
                props_max=2):
    name, path, has_injuries = LEAGUES[sport]
    window = {"commenceTimeFrom": _iso(now), "commenceTimeTo": _iso(now + horizon)}
    upcoming, headers = get_json(EVENTS_URL.format(sport=sport), {"apiKey": api_key, **window})
    remaining = headers.get("x-requests-remaining")
    skipped = None
    if not upcoming:
        skipped = "no games in window"
    elif remaining is not None and float(remaining) - 3 < reserve:
        skipped = f"credit reserve: {remaining} left, keeping {reserve} for settlement"
    if skipped:
        return {"league": name, "sport_key": sport, "games": [], "news": None,
                "skipped": skipped, "odds_requests_remaining": remaining}
    events, headers = get_json(ODDS_URL.format(sport=sport), {
        **window,
        "apiKey": api_key,
        "regions": "uk",  # UK-licensed books only: the prices the user can actually get
        "markets": "h2h,spreads,totals",
        "oddsFormat": "american",
    })
    previous = latest_snapshot(sport)
    games, snapshot = [], {}
    for ev in events:
        start = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
        if not now <= start <= now + horizon:
            continue
        line = consensus(ev)
        snapshot[ev["id"]] = line
        games.append({
            "id": ev["id"],
            "matchup": f"{ev['away_team']} @ {ev['home_team']}",
            "home": ev["home_team"],
            "away": ev["away_team"],
            "kickoff_utc": ev["commence_time"],
            "market": line,
            "movement_since_last_scan": movement(line, previous.get(ev["id"])),
        })
    remaining = headers.get("x-requests-remaining")
    if (sport == "soccer_epl" and cards_horizon) or props_horizon:
        remaining = fetch_player_markets(sport, api_key, games, now, reserve, remaining, cards_horizon,
                                         props_horizon, props_max)
    if snapshot:
        out = SNAPSHOTS / sport / f"{now.strftime('%Y%m%dT%H%M%SZ')}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(snapshot, indent=1))
    teams = {g["home"] for g in games} | {g["away"] for g in games}
    return {
        "league": name,
        "sport_key": sport,
        "games": games,
        "news": fetch_news(path, has_injuries, teams) if games else None,
        "odds_requests_remaining": remaining,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=int, default=36, help="look-ahead window for games")
    parser.add_argument("--leagues", default="nfl,nba,epl", help="comma-separated: nfl,nba,epl")
    parser.add_argument("--reserve", type=int, default=25,
                        help="don't price leagues once remaining credits would drop below this")
    parser.add_argument("--cards-hours", type=int, default=0,
                        help="fetch EPL player-card odds for matches starting within this many hours")
    parser.add_argument("--props-hours", type=int, default=24,
                        help="fetch player props for games starting within this many hours")
    parser.add_argument("--props-max", type=int, default=2,
                        help="price props for at most this many games per league (soonest first)")
    args = parser.parse_args()
    wanted = {l.strip().upper() for l in args.leagues.split(",")}
    sports = [s for s, (name, _, _) in LEAGUES.items() if name in wanted]
    if not sports:
        sys.exit(f"No known leagues in {args.leagues!r}; use nfl, nba, epl.")

    api_key = os.environ.get("ODDS_API_KEY")
    if not api_key:
        sys.exit("ODDS_API_KEY is not set; add it in the cloud environment settings.")

    now = datetime.now(timezone.utc)
    report = {"generated_utc": now.isoformat(timespec="seconds"), "leagues": [], "errors": []}
    for sport in sports:
        try:
            report["leagues"].append(
                scan_league(sport, api_key, now, timedelta(hours=args.hours), args.reserve,
                            timedelta(hours=args.cards_hours), timedelta(hours=args.props_hours),
                            args.props_max))
        except Exception as e:
            report["errors"].append(f"{sport}: {e}")

    DATA.mkdir(exist_ok=True)
    (DATA / "latest.json").write_text(json.dumps(report, indent=1))
    n = sum(len(l["games"]) for l in report["leagues"])
    left = [l["odds_requests_remaining"] for l in report["leagues"] if l["odds_requests_remaining"]]
    print(f"{n} games in the next {args.hours}h; credits left: {left[-1] if left else '?'}; "
          f"errors: {report['errors'] or 'none'}")
    for l in report["leagues"]:
        if l.get("skipped"):
            print(f"  {l['league']} skipped: {l['skipped']}")
        for g in l["games"]:
            if "cards" in g:
                print(f"  cards: {g['matchup']}: {len(g['cards'])} players priced")
            elif g.get("cards_skipped"):
                print(f"  cards: {g['matchup']}: skipped ({g['cards_skipped']})")
            for key, prop in g.get("props", {}).items():
                proxy = " (US books, stand-in prices)" if prop["us_proxy"] else ""
                print(f"  props: {g['matchup']}: {key} {len(prop['players'])} players{proxy}")
            if g.get("props_skipped"):
                print(f"  props: {g['matchup']}: skipped ({g['props_skipped']})")
    if not report["leagues"]:
        sys.exit(1)


if __name__ == "__main__":
    main()

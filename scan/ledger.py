"""Paper-trading ledger: £200 notional bankroll, 30-day trial.

Bets are only placed at the best price found in the latest scan (data/latest.json),
never at a typed-in price. Settlement pulls final scores from The Odds API and
records closing line value (CLV) from the last odds snapshot before kickoff.
EPL "player to be carded" bets (--market cards --selection "Player Name") settle
from ESPN's match data at no credit cost; a player who doesn't appear is void,
as at UK books. Card bets have no CLV (closing card odds would cost credits).
Player props (--market props --prop KEY --selection "Player" --side Over|Under|Yes)
settle from ESPN box scores the same way: NBA player points, NFL anytime touchdown
(any non-passing TD), EPL anytime goalscorer (own goals don't count). A player
who doesn't play is void. NBA/NFL prop prices come from US books (us_proxy).

  python3 scan/ledger.py start
  python3 scan/ledger.py place --event ID --market spreads --selection "Team" \
      --projection "-1.5" --reason "why the market is off"
  python3 scan/ledger.py place --event ID --market props --prop player_points \
      --selection "Player" --side Over --projection "27.5" --reason "why"
  python3 scan/ledger.py settle
  python3 scan/ledger.py summary
"""

import argparse
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timedelta, timezone

from scan import DATA, ESPN_URL, LEAGUES, SNAPSHOTS, american_to_prob, get_json

LEDGER = DATA / "ledger.json"
SCORES_URL = "https://api.the-odds-api.com/v4/sports/{sport}/scores"
STARTING_BANKROLL = 200.0
TRIAL_DAYS = 30
STAKE_PCT = 0.02  # flat 2% of current bankroll per bet
MAX_STAKE = 10.0  # owner's cap for a raised stake on a high-confidence bet
MAX_CARD_BETS_PER_GAME = 2  # match bets: one per market (h2h, spreads, totals) per game
MAX_PROP_BETS_PER_GAME = 3


def now_utc():
    return datetime.now(timezone.utc)


def parse_ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def load():
    if LEDGER.exists():
        return json.loads(LEDGER.read_text())
    return {"currency": "GBP", "starting_bankroll": STARTING_BANKROLL,
            "started_utc": None, "ends_utc": None, "bets": []}


def save(ledger):
    DATA.mkdir(exist_ok=True)
    LEDGER.write_text(json.dumps(ledger, indent=1))


def to_decimal(american):
    return 1 + (american / 100 if american > 0 else 100 / -american)


def bankroll(ledger):
    return ledger["starting_bankroll"] + sum(b.get("pnl") or 0 for b in ledger["bets"])


def open_exposure(ledger):
    return sum(b["stake"] for b in ledger["bets"] if b["status"] == "open")


def stake_for(ledger):
    stake = max(1.0, round(bankroll(ledger) * STAKE_PCT * 2) / 2)  # nearest £0.50
    return min(stake, bankroll(ledger) - open_exposure(ledger))


def payout(bet):
    """Profit or loss on a graded bet; a half result settles half the stake and refunds the rest."""
    win = bet["stake"] * (bet["decimal_odds"] - 1)
    return round({"win": win, "half-win": win / 2, "push": 0.0, "half-loss": -bet["stake"] / 2,
                  "loss": -bet["stake"]}[bet["result"]], 2)


def grade(bet, home_score, away_score):
    """Return 'win', 'loss', 'push', or for quarter-goal Asian lines 'half-win'/'half-loss'."""
    point = bet.get("point")
    if bet["market"] in ("spreads", "totals") and point is not None and (point * 4) % 2 == 1:
        # a quarter line (e.g. +0.25) is half the stake on each neighbouring line (0 and +0.5)
        halves = {grade({**bet, "point": point + d}, home_score, away_score) for d in (-0.25, 0.25)}
        if len(halves) == 1:
            return halves.pop()
        return "half-win" if "win" in halves else "half-loss"
    sel = bet["selection"]
    if bet["market"] == "totals":
        diff = (home_score + away_score) - bet["point"]
        diff = diff if sel == "Over" else -diff
    else:
        mine, theirs = (home_score, away_score) if sel == bet["home"] else (away_score, home_score)
        if bet["market"] == "spreads":
            diff = mine + bet["point"] - theirs
        elif sel == "Draw":
            diff = 1 if home_score == away_score else -1
        elif home_score == away_score:
            # 3-way soccer markets lose on a draw; 2-way US moneylines are void
            diff = -1 if bet["sport_key"].startswith("soccer") else 0
        else:
            diff = mine - theirs
    return "win" if diff > 0 else "loss" if diff < 0 else "push"


def norm(name):
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    name = re.sub(r"\b(afc|fc)\b", "", name.replace("&", "and"))
    return " ".join(re.sub(r"[^a-z ]", " ", name).split())


def same_team(a, b):
    a, b = norm(a), norm(b)
    return a == b or a in b or b in a


def find_player(name, players):
    """Match a bookmaker's player name to ESPN's; None if absent or ambiguous."""
    def key(n):  # "Kyle Pitts Sr." and "Kyle Pitts" are the same player
        return " ".join(w for w in norm(n).split() if w not in ("jr", "sr", "ii", "iii", "iv")) or norm(n)

    target = key(name)
    exact = [p for p in players if key(p) == target]
    if exact:
        return exact[0]
    first, last = target.split()[0], target.split()[-1]
    close = [p for p in players if key(p).split()[-1] == last and key(p)[0] == first[0]]
    return close[0] if len(close) == 1 else None


def espn_match(bet):
    """ESPN scoreboard event for this bet's match, or None if not found / not finished."""
    path = LEAGUES[bet["sport_key"]][1]
    kickoff = parse_ts(bet["kickoff_utc"])
    # ESPN files US games under the US date, so a 00:10 UTC tip-off is on the day before
    for day in sorted({kickoff.strftime("%Y%m%d"), (kickoff - timedelta(hours=8)).strftime("%Y%m%d")}):
        data, _ = get_json(ESPN_URL.format(path=path, endpoint="scoreboard"), {"dates": day})
        for ev in data.get("events", []):
            comp = ev["competitions"][0]
            teams = {c["homeAway"]: c["team"]["displayName"] for c in comp["competitors"]}
            if same_team(teams.get("home", ""), bet["home"]) and same_team(teams.get("away", ""), bet["away"]):
                return ev if comp["status"]["type"].get("completed") else None
    return None


def grade_card(bet, carded, appeared, squad):
    """'win' if the player was shown a card, 'push' if they didn't play, None if unmatched."""
    if find_player(bet["selection"], squad) is None:
        # not in either matchday squad: void, unless ESPN just spells the name differently
        return "push" if squad else None
    if find_player(bet["selection"], carded):
        return "win"
    return "loss" if find_player(bet["selection"], appeared) else "push"


def settle_card(bet):
    ev = espn_match(bet)
    if not ev:
        return False
    comp = ev["competitions"][0]
    carded = [a.get("displayName", "") for d in comp.get("details", [])
              if d.get("yellowCard") or d.get("redCard") for a in d.get("athletesInvolved", [])]
    summary, _ = get_json(ESPN_URL.format(path="soccer/eng.1", endpoint="summary"), {"event": ev["id"]})
    squad, appeared = [], []
    for team in summary.get("rosters", []):
        for p in team.get("roster", []):
            name = p.get("athlete", {}).get("displayName", "")
            squad.append(name)
            if p.get("starter") or p.get("subbedIn"):
                appeared.append(name)
    result = grade_card(bet, carded, appeared, squad)
    if result is None:
        return False
    bet["final_score"] = {c["team"]["displayName"]: c.get("score") for c in comp["competitors"]}
    bet["result"] = result
    return True


def box_stats(summary, wanted):
    """{player: total of the wanted stat keys} for everyone with a box-score line."""
    out = {}
    for team in summary.get("boxscore", {}).get("players", []):
        for cat in team.get("statistics", []):
            keys = cat.get("keys", [])
            for a in cat.get("athletes", []):
                if a.get("didNotPlay") or not a.get("stats"):
                    continue
                name = a.get("athlete", {}).get("displayName", "")
                total = out.setdefault(name, 0)
                for k, v in zip(keys, a["stats"]):
                    if wanted(k):
                        try:
                            total += float(v)
                        except ValueError:
                            pass
                out[name] = total
    return out


def grade_prop(bet, stats):
    """stats: {player: value} for players who played; 'push' if the player didn't play."""
    player = find_player(bet["selection"], list(stats))
    if player is None:
        return "push"
    value = stats[player]
    bet["actual"] = value
    if bet["side"] == "Yes":
        return "win" if value >= 1 else "loss"
    diff = value - bet["point"] if bet["side"] == "Over" else bet["point"] - value
    return "win" if diff > 0 else "loss" if diff < 0 else "push"


def settle_prop(bet):
    ev = espn_match(bet)
    if not ev:
        return False
    path = LEAGUES[bet["sport_key"]][1]
    summary, _ = get_json(ESPN_URL.format(path=path, endpoint="summary"), {"event": ev["id"]})
    comp = ev["competitions"][0]
    if bet["prop"] == "player_points":
        stats = box_stats(summary, lambda k: k == "points")
    elif bet["prop"] == "player_anytime_td":
        stats = box_stats(summary, lambda k: k.endswith("Touchdowns") and k != "passingTouchdowns")
    else:  # player_goal_scorer_anytime
        scorers = [a.get("displayName", "") for d in comp.get("details", [])
                   if d.get("scoringPlay") and not d.get("ownGoal") for a in d.get("athletesInvolved", [])[:1]]
        stats = {}
        for team in summary.get("rosters", []):
            for p in team.get("roster", []):
                if p.get("starter") or p.get("subbedIn"):
                    name = p.get("athlete", {}).get("displayName", "")
                    stats[name] = sum(find_player(s, [name]) is not None for s in scorers)
        if not stats:
            return False
    bet["final_score"] = {c["team"]["displayName"]: c.get("score") for c in comp["competitors"]}
    bet["result"] = grade_prop(bet, stats)
    return True


def closing_line(bet):
    """Last consensus snapshot for this event taken before kickoff."""
    kickoff = parse_ts(bet["kickoff_utc"])
    for f in sorted((SNAPSHOTS / bet["sport_key"]).glob("*.json"), reverse=True):
        taken = datetime.strptime(f.stem, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        if taken >= kickoff:
            continue
        line = json.loads(f.read_text()).get(bet["event_id"])
        if line:
            return taken, line
    return None, None


def clv(bet, close):
    """Positive = bet beat the closing line."""
    sel = bet["selection"]
    if bet["market"] == "h2h":
        p = close.get("moneyline", {}).get(sel, {}).get("no_vig_prob")
        return None if p is None else {"clv_pct": round((p * bet["decimal_odds"] - 1) * 100, 2)}
    if bet["market"] == "spreads":
        c = close.get("spread", {}).get(sel)
        return None if c is None else {"clv_points": round(bet["point"] - c, 2)}
    c = close.get("total")
    if c is None:
        return None
    return {"clv_points": round((c - bet["point"]) if sel == "Over" else (bet["point"] - c), 2)}


def cmd_start(ledger, args):
    if ledger["started_utc"]:
        print(f"Trial already running: {ledger['started_utc']} -> {ledger['ends_utc']}")
        return
    start = now_utc()
    ledger["started_utc"] = start.isoformat(timespec="seconds")
    ledger["ends_utc"] = (start + timedelta(days=TRIAL_DAYS)).isoformat(timespec="seconds")
    save(ledger)
    print(f"Trial started with £{ledger['starting_bankroll']:.2f}; ends {ledger['ends_utc']}")


def cmd_place(ledger, args):
    if not ledger["started_utc"]:
        sys.exit("Trial not started; run `ledger.py start` first.")
    if now_utc() >= parse_ts(ledger["ends_utc"]):
        sys.exit("Trial period has ended; no new bets.")
    latest = json.loads((DATA / "latest.json").read_text())
    scanned = parse_ts(latest["generated_utc"])
    if now_utc() - scanned > timedelta(minutes=30):
        sys.exit("latest.json is over 30 minutes old; re-run scan.py before placing.")

    found = [(lg, g) for lg in latest["leagues"] for g in lg["games"] if g["id"] == args.event]
    if not found:
        sys.exit(f"Event {args.event} not in latest scan.")
    league, game = found[0]
    if parse_ts(game["kickoff_utc"]) <= now_utc():
        sys.exit("Game has already started.")
    on_game = [b for b in ledger["bets"] if b["event_id"] == args.event]
    if args.market == "cards":
        cards = [b for b in on_game if b["market"] == "cards"]
        if any(norm(b["selection"]) == norm(args.selection) for b in cards):
            sys.exit(f"Already have a card bet on {args.selection} in this game.")
        if len(cards) >= MAX_CARD_BETS_PER_GAME:
            sys.exit(f"Already have {MAX_CARD_BETS_PER_GAME} card bets on this game.")
    elif args.market == "props":
        props = [b for b in on_game if b["market"] == "props"]
        if any(b["prop"] == args.prop and norm(b["selection"]) == norm(args.selection) for b in props):
            sys.exit(f"Already have a {args.prop} bet on {args.selection} in this game.")
        if len(props) >= MAX_PROP_BETS_PER_GAME:
            sys.exit(f"Already have {MAX_PROP_BETS_PER_GAME} prop bets on this game.")
    elif any(b["market"] == args.market for b in on_game):
        sys.exit(f"Already have a {args.market} bet on this game.")
    if args.market == "cards":
        card = game.get("cards", {}).get(args.selection)
        if not card:
            sys.exit(f"No card price for {args.selection!r} in latest scan (run scan.py with --cards-hours).")
        best = card["best"]
    elif args.market == "props":
        prop = game.get("props", {}).get(args.prop or "")
        offer = prop and prop["players"].get(args.selection)
        if not offer:
            sys.exit(f"No {args.prop} price for {args.selection!r} in latest scan (run scan.py with --props-hours).")
        side = args.side or "Yes"
        if (side == "Yes") == ("line" in offer):
            sys.exit(f"{args.prop} takes --side {'Over or Under' if 'line' in offer else 'Yes'}.")
        best = offer["best"] if side == "Yes" else offer[side]
    else:
        best = game["market"]["best_price"].get(args.market, {}).get(args.selection)
    if not best:
        sys.exit(f"No price for {args.market}/{args.selection}.")
    stake = stake_for(ledger)
    if args.stake is not None:
        if not 1 <= args.stake <= MAX_STAKE:
            sys.exit(f"--stake must be between £1 and £{MAX_STAKE:.0f}.")
        stake = min(args.stake, bankroll(ledger) - open_exposure(ledger))
    if stake < 1:
        sys.exit("Insufficient available bankroll.")

    m = game["market"]
    bet = {
        "id": len(ledger["bets"]) + 1,
        "placed_utc": now_utc().isoformat(timespec="seconds"),
        "league": league["league"],
        "sport_key": league["sport_key"],
        "event_id": game["id"],
        "matchup": game["matchup"],
        "home": game["home"],
        "away": game["away"],
        "kickoff_utc": game["kickoff_utc"],
        "market": args.market,
        "selection": args.selection,
        "point": best.get("point"),
        "price_american": best["price"],
        "decimal_odds": round(to_decimal(best["price"]), 4),
        "implied_prob": round(american_to_prob(best["price"]), 4),
        "book": best["book"],
        "stake": stake,
        "consensus_at_placement": ({"median_implied_prob": card["median_implied_prob"], "books": card["books"]}
                                   if args.market == "cards" else
                                   {k: v for k, v in offer.items() if k not in ("best", "Over", "Under")}
                                   if args.market == "props" else
                                   {k: m.get(k) for k in ("spread", "total", "moneyline")}),
        "projection": args.projection,
        "reason": args.reason,
        "status": "open",
        "result": None,
        "pnl": None,
    }
    if args.market == "props":
        bet.update({"prop": args.prop, "side": side, "us_proxy": prop["us_proxy"]})
    ledger["bets"].append(bet)
    save(ledger)
    pt = f" {bet['point']:+g}" if bet["point"] is not None and args.market == "spreads" else \
         f" {bet['point']:g}" if bet["point"] is not None else ""
    if args.market == "props":
        pt = f" {args.prop} {side}" + (f" {bet['point']:g}" if bet["point"] is not None else "")
    print(f"#{bet['id']} {bet['matchup']}: {args.selection}{pt} @ {best['price']:+d} "
          f"({best['book']}) stake £{stake:.2f}")


def cmd_settle(ledger, args):
    due = [b for b in ledger["bets"] if b["status"] == "open" and parse_ts(b["kickoff_utc"]) < now_utc()]
    if not due:
        print("Nothing to settle.")
        return
    settled = 0
    for b in [b for b in due if b["market"] in ("cards", "props")]:
        try:
            if (settle_card if b["market"] == "cards" else settle_prop)(b):
                b["pnl"] = payout(b)
                b["status"] = "settled"
                settled += 1
        except Exception as e:  # ESPN is best-effort; retry next run
            print(f"{b['market'].capitalize()} bet #{b['id']}: {e}")
    match_due = [b for b in due if b["market"] not in ("cards", "props")]
    if not match_due:
        save(ledger)
        print(f"Settled {settled} of {len(due)} due bets. Bankroll £{bankroll(ledger):.2f}")
        return
    api_key = os.environ.get("ODDS_API_KEY") or sys.exit("ODDS_API_KEY is not set.")
    scores = {}
    for sport in {b["sport_key"] for b in match_due}:
        data, _ = get_json(SCORES_URL.format(sport=sport), {"apiKey": api_key, "daysFrom": 3})
        scores.update({e["id"]: e for e in data if e.get("completed")})
    for b in match_due:
        ev = scores.get(b["event_id"])
        if not ev:
            continue
        pts = {s["name"]: float(s["score"]) for s in ev.get("scores") or []}
        b["final_score"] = {b["home"]: pts.get(b["home"]), b["away"]: pts.get(b["away"])}
        b["result"] = grade(b, pts[b["home"]], pts[b["away"]])
        b["pnl"] = payout(b)
        taken, close = closing_line(b)
        if close:
            b["closing_snapshot_utc"] = taken.isoformat(timespec="seconds")
            b["clv"] = clv(b, close)
        b["status"] = "settled"
        settled += 1
    save(ledger)
    print(f"Settled {settled} of {len(due)} due bets. Bankroll £{bankroll(ledger):.2f}")


def summarise(ledger):
    bets = ledger["bets"]
    done = [b for b in bets if b["status"] == "settled"]
    staked = sum(b["stake"] for b in done)
    pnl = sum(b["pnl"] for b in done)
    wins = sum(b["result"] in ("win", "half-win") for b in done)
    losses = sum(b["result"] in ("loss", "half-loss") for b in done)
    clvs = [b["clv"] for b in done if b.get("clv")]
    pct = [c["clv_pct"] for c in clvs if "clv_pct" in c]
    pts = [c["clv_points"] for c in clvs if "clv_points" in c]
    beat = sum((c.get("clv_pct") or c.get("clv_points") or 0) > 0 for c in clvs)

    def tier(b):
        reason = b.get("reason", "")
        return "A" if reason.startswith("[A]") else "B" if reason.startswith("[B") else "manual"

    def group(key):
        out = {}
        for b in done:
            g = out.setdefault(key(b), {"bets": 0, "staked": 0.0, "pnl": 0.0})
            g["bets"] += 1
            g["staked"] += b["stake"]
            g["pnl"] = round(g["pnl"] + b["pnl"], 2)
        return out

    return {
        "started_utc": ledger["started_utc"],
        "ends_utc": ledger["ends_utc"],
        "starting_bankroll": ledger["starting_bankroll"],
        "bankroll": round(bankroll(ledger), 2),
        "open_bets": sum(b["status"] == "open" for b in bets),
        "open_exposure": open_exposure(ledger),
        "settled_bets": len(done),
        "record": f"{wins}-{losses}-{len(done) - wins - losses}",
        "win_rate_excl_pushes": round(wins / (wins + losses) * 100, 1) if wins + losses else None,
        "total_staked": round(staked, 2),
        "profit": round(pnl, 2),
        "roi_pct": round(pnl / staked * 100, 2) if staked else None,
        "beat_closing_line_pct": round(beat / len(clvs) * 100, 1) if clvs else None,
        "avg_clv_pct_moneyline": round(sum(pct) / len(pct), 2) if pct else None,
        "avg_clv_points_spread_total": round(sum(pts) / len(pts), 2) if pts else None,
        "by_league": group(lambda b: b["league"]),
        "by_market": group(lambda b: b.get("prop") or b["market"]),
        "by_tier": group(tier),
    }


def cmd_summary(ledger, args):
    print(json.dumps(summarise(ledger), indent=1))


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("start")
    pl = sub.add_parser("place")
    pl.add_argument("--event", required=True)
    pl.add_argument("--market", required=True, choices=["h2h", "spreads", "totals", "cards", "props"])
    pl.add_argument("--selection", required=True,
                    help="team name, 'Draw', 'Over', 'Under', or a player name for cards and props")
    pl.add_argument("--prop", help="prop market key from the scan, e.g. player_points")
    pl.add_argument("--side", choices=["Over", "Under", "Yes"], help="for props (default Yes)")
    pl.add_argument("--stake", type=float,
                    help=f"raise the stake on a high-confidence bet (£1-£{MAX_STAKE:.0f}); default 2%% of bankroll")
    pl.add_argument("--projection", required=True, help="your fair line or probability")
    pl.add_argument("--reason", required=True)
    sub.add_parser("settle")
    sub.add_parser("summary")
    args = p.parse_args()
    ledger = load()
    {"start": cmd_start, "place": cmd_place, "settle": cmd_settle, "summary": cmd_summary}[args.cmd](ledger, args)


if __name__ == "__main__":
    main()

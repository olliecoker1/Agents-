"""HTML pieces for report/index.html: the market board rows and the ledger's bet table.

Run from the repo root:  from report_parts import load, board_rows, bet_rows, groups
"""

import datetime as dt
import html
import json

import ledger

LEAGUE_TAG = {"americanfootball_nfl": "NFL", "basketball_nba": "NBA", "soccer_epl": "EPL"}


def dec(american):
    return f"{ledger.to_decimal(american):.2f}"


def when(ts):
    return dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).strftime("%a %d %b, %H:%M UTC")


def board_rows(latest):
    rows = []
    for lg in latest["leagues"]:
        for g in lg["games"]:
            m, h = g["market"], g["home"]
            bp, ml = m["best_price"], m["moneyline"]
            fav = max(ml, key=lambda k: ml[k]["no_vig_prob"])
            odds = " · ".join(f"{html.escape(k if k == 'Draw' else k.split()[-1])} {dec(v['price'])} "
                              f"<span class=bk>{html.escape(v['book'])}</span>" for k, v in bp["h2h"].items())
            pt, b = m.get("spread", {}).get(h), bp["spreads"].get(h)
            if pt is None:
                spread = "–"
            elif b and abs(b["point"] - pt) < 0.01:
                spread = f"{h.split()[-1]} {pt:+g} @ {dec(b['price'])} <span class=bk>{html.escape(b['book'])}</span>"
            else:
                spread = f"{h.split()[-1]} {pt:+g} <span class=bk>consensus</span>"
            total = "<br>".join(f"{k[0]} {v['point']:g} @ {dec(v['price'])} <span class=bk>{html.escape(v['book'])}</span>"
                                for k, v in bp["totals"].items()) or "–"
            rows.append(f"<tr><td><span class=lg>{LEAGUE_TAG[lg['sport_key']]}</span></td>"
                        f"<td><b>{html.escape(g['matchup'])}</b><div class=sub>{when(g['kickoff_utc'])} · "
                        f"{m['books']} books · fair: {html.escape(fav.split()[-1])} {ml[fav]['no_vig_prob'] * 100:.1f}%"
                        f"</div></td><td>{odds}</td><td>{spread}</td><td>{total}</td></tr>")
    return "\n".join(rows)


def describe(b):
    if b["market"] == "props":
        if b["prop"] == "player_points":
            return f"{b['selection']} {b['side'].lower()} {b['point']:g} pts"
        return f"{b['selection']} anytime {'TD' if b['prop'] == 'player_anytime_td' else 'goal'}"
    if b["market"] == "cards":
        return f"{b['selection']} to be carded"
    if b["market"] == "totals":
        return f"{b['selection']} {b['point']:g}"
    if b["market"] == "spreads":
        return f"{b['selection']} {b['point']:+g}"
    return b["selection"]


def bet_rows(led):
    rows = []
    for b in reversed(led["bets"]):
        if b["status"] == "open":
            result = "Open"
        else:
            result = {"win": f"Won +£{b['pnl']:.2f}", "half-win": f"Half won +£{b['pnl']:.2f}",
                      "push": "Void/push £0.00", "half-loss": f"Half lost −£{-b['pnl']:.2f}",
                      "loss": f"Lost −£{-b['pnl']:.2f}"}[b["result"]]
            if b.get("actual") is not None:
                result += f" (actual {b['actual']:g})"
        star = "*" if b.get("us_proxy") else ""
        reason = b["reason"].replace("[B lean] ", "B: ").replace("[A] ", "A: ")
        rows.append(f"<tr><td>{b['id']}</td><td>{html.escape(describe(b))}<div class=sub>{html.escape(b['matchup'])}</div></td>"
                    f"<td>{b['decimal_odds']:.2f} <span class=bk>{html.escape(b['book'])}{star}</span></td>"
                    f"<td>£{b['stake']:.2f}</td><td>{result}</td><td class=sub>{html.escape(reason)}</td></tr>")
    return "\n".join(rows)


def groups(split):
    return ", ".join(f"{k} {v['bets']} bets {'+' if v['pnl'] >= 0 else '−'}£{abs(v['pnl']):.2f}"
                     for k, v in split.items())


def load():
    latest = json.loads((ledger.DATA / "latest.json").read_text())
    led = ledger.load()
    return latest, led, ledger.summarise(led)

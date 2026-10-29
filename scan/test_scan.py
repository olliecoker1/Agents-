import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import scan
from scan import american_to_prob, consensus, movement, prob_to_american


def book(home_ml, away_ml, spread, total):
    return {"markets": [
        {"key": "h2h", "outcomes": [{"name": "Home", "price": home_ml}, {"name": "Away", "price": away_ml}]},
        {"key": "spreads", "outcomes": [{"name": "Home", "point": spread, "price": -110}, {"name": "Away", "point": -spread, "price": -110}]},
        {"key": "totals", "outcomes": [{"name": "Over", "point": total, "price": -110}, {"name": "Under", "point": total, "price": -110}]},
    ]}


class ScanTest(unittest.TestCase):
    def test_price_conversions(self):
        self.assertAlmostEqual(american_to_prob(-110), 0.5238, places=4)
        self.assertAlmostEqual(american_to_prob(150), 0.4, places=4)
        self.assertEqual(prob_to_american(0.6), -150)
        self.assertEqual(prob_to_american(0.4), 150)

    def test_consensus_removes_vig_and_takes_median(self):
        ev = {"bookmakers": [book(-150, 130, -3.0, 45.5), book(-160, 140, -3.5, 46.5), book(-155, 135, -3.0, 46.0)]}
        line = consensus(ev)
        probs = [v["no_vig_prob"] for v in line["moneyline"].values()]
        self.assertAlmostEqual(sum(probs), 1.0, places=3)
        self.assertGreater(line["hold_pct"], 0)
        self.assertEqual(line["spread"]["Home"], -3.0)
        self.assertEqual(line["total"], 46.0)

    def test_best_price(self):
        ev = {"bookmakers": [
            dict(book(-150, 130, -3.0, 45.5), title="A"),
            dict(book(-140, 125, -2.5, 46.5), title="B"),
        ]}
        best = consensus(ev)["best_price"]
        self.assertEqual(best["h2h"]["Home"], {"price": -140, "book": "B"})
        self.assertEqual(best["h2h"]["Away"]["book"], "A")
        self.assertEqual(best["h2h"]["Away"]["price"], 130)

    def test_best_price_ignores_alternate_lines(self):
        books = [dict(book(-150, 130, -3.0, 45.5), title=t) for t in "ABC"]
        books[1]["markets"][1]["outcomes"][0]["price"] = -105  # best price on main line
        alt = {"markets": [
            {"key": "spreads", "outcomes": [{"name": "Home", "point": 8.0, "price": -667}]},
            {"key": "totals", "outcomes": [{"name": "Under", "point": 60.5, "price": -357}]},
        ], "title": "Alt"}
        best = consensus({"bookmakers": books + [alt]})["best_price"]
        self.assertEqual(best["spreads"]["Home"], {"price": -105, "book": "B", "point": -3.0})
        self.assertEqual(best["totals"]["Under"]["point"], 45.5)

    def test_movement(self):
        before = consensus({"bookmakers": [book(-150, 130, -3.0, 45.5)]})
        after = consensus({"bookmakers": [book(-200, 170, -4.5, 44.0)]})
        delta = movement(after, before)
        self.assertEqual(delta["spread"]["Home"], -1.5)
        self.assertEqual(delta["total"], -1.5)
        self.assertGreater(delta["moneyline_prob_pts"]["Home"], 0)
        self.assertIsNone(movement(after, None))
        self.assertEqual(movement(before, before), {})

    def test_card_prices(self):
        ev = {"bookmakers": [
            {"title": "Paddy Power", "markets": [{"key": "player_to_receive_card", "outcomes": [
                {"name": "Yes", "description": "Sasa Lukic", "price": 200}]}]},
            {"title": "William Hill", "markets": [{"key": "player_to_receive_card", "outcomes": [
                {"name": "Yes", "description": "Sasa Lukic", "price": 162}]}]},
            {"title": "Betfair", "markets": [{"key": "player_to_receive_card", "outcomes": [
                {"name": "Yes", "description": "Sasa Lukic", "price": -10000}]}]},
        ]}
        card = scan.card_prices(ev)["Sasa Lukic"]
        self.assertEqual(card["best"], {"price": 200, "book": "Paddy Power"})
        self.assertEqual(card["books"], 2)  # Betfair's 1.01 placeholder dropped

    def test_over_under_prices_use_main_line(self):
        def ou(title, point, over, under):
            return {"title": title, "markets": [{"key": "player_points", "outcomes": [
                {"name": "Over", "description": "Jalen Johnson", "price": over, "point": point},
                {"name": "Under", "description": "Jalen Johnson", "price": under, "point": point}]}]}
        ev = {"bookmakers": [ou("DraftKings", 22.5, -110, -110), ou("FanDuel", 22.5, -105, -115),
                             ou("Bovada", 24.5, 120, -150)]}
        p = scan.over_under_prices(ev, "player_points")["Jalen Johnson"]
        self.assertEqual(p["line"], 22.5)  # the line two of three books offer
        self.assertEqual(p["Over"], {"price": -105, "point": 22.5, "book": "FanDuel"})
        self.assertEqual(p["Under"], {"price": -110, "point": 22.5, "book": "DraftKings"})
        self.assertEqual(p["books"], 2)
        self.assertAlmostEqual(p["median_no_vig_over_prob"], 0.4946, places=3)

    def test_props_capped_per_league(self):
        now = datetime(2026, 10, 22, tzinfo=timezone.utc)
        games = [{"id": f"g{i}", "kickoff_utc": (now + timedelta(hours=i + 1)).isoformat()} for i in range(4)]
        calls = []

        def fake_get(url, params=None):
            calls.append(params)
            return {"bookmakers": []}, {"x-requests-remaining": "300"}

        with mock.patch.object(scan, "get_json", fake_get):
            scan.fetch_player_markets("basketball_nba", "k", games, now, 25, "300", timedelta(0),
                                      timedelta(hours=24), 2)
        self.assertEqual([c["markets"] for c in calls], ["player_points", "player_points"])
        self.assertEqual({c["regions"] for c in calls}, {"us"})
        self.assertEqual(games[0]["props"]["player_points"]["us_proxy"], True)
        self.assertNotIn("props", games[3])

    def test_epl_cards_and_goalscorer_share_one_request(self):
        now = datetime(2026, 10, 22, tzinfo=timezone.utc)
        games = [{"id": "m1", "kickoff_utc": (now + timedelta(hours=3)).isoformat()}]
        calls = []

        def fake_get(url, params=None):
            calls.append(params)
            return {"bookmakers": [{"title": "William Hill", "markets": [
                {"key": "player_goal_scorer_anytime", "outcomes": [
                    {"name": "Yes", "description": "Cole Palmer", "price": 145}]}]}]}, {}

        with mock.patch.object(scan, "get_json", fake_get):
            scan.fetch_player_markets("soccer_epl", "k", games, now, 25, None, timedelta(hours=24),
                                      timedelta(hours=24), 3)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["markets"], "player_to_receive_card,player_goal_scorer_anytime")
        prop = games[0]["props"]["player_goal_scorer_anytime"]
        self.assertEqual((prop["us_proxy"], prop["players"]["Cole Palmer"]["best"]["price"]), (False, 145))


class CreditBudgetTest(unittest.TestCase):
    now = datetime(2026, 10, 22, tzinfo=timezone.utc)

    def run_league(self, events, remaining, reserve=25):
        calls = []

        def fake_get(url, params=None):
            calls.append(url)
            return events, {"x-requests-remaining": remaining}

        with mock.patch.object(scan, "get_json", fake_get):
            out = scan.scan_league("soccer_epl", "k", self.now, timedelta(hours=36), reserve)
        return out, calls

    def test_empty_window_skips_paid_odds_call(self):
        out, calls = self.run_league([], "400")
        self.assertEqual(out["skipped"], "no games in window")
        self.assertEqual(calls, [scan.EVENTS_URL.format(sport="soccer_epl")])

    def test_reserve_skips_paid_odds_call(self):
        out, calls = self.run_league([{"id": "x"}], "27")
        self.assertIn("credit reserve", out["skipped"])
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()

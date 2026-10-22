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

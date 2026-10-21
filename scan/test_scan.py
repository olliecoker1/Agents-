import unittest

from scan import american_to_prob, consensus, movement, prob_to_american


def book(home_ml, away_ml, spread, total):
    return {"markets": [
        {"key": "h2h", "outcomes": [{"name": "Home", "price": home_ml}, {"name": "Away", "price": away_ml}]},
        {"key": "spreads", "outcomes": [{"name": "Home", "point": spread}, {"name": "Away", "point": -spread}]},
        {"key": "totals", "outcomes": [{"name": "Over", "point": total}, {"name": "Under", "point": total}]},
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

    def test_movement(self):
        before = consensus({"bookmakers": [book(-150, 130, -3.0, 45.5)]})
        after = consensus({"bookmakers": [book(-200, 170, -4.5, 44.0)]})
        delta = movement(after, before)
        self.assertEqual(delta["spread"]["Home"], -1.5)
        self.assertEqual(delta["total"], -1.5)
        self.assertGreater(delta["moneyline_prob_pts"]["Home"], 0)
        self.assertIsNone(movement(after, None))
        self.assertEqual(movement(before, before), {})


if __name__ == "__main__":
    unittest.main()

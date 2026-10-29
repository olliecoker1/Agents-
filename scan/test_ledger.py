import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import ledger
from test_scan import book
from scan import consensus


def bet(market, selection, point=None, sport="americanfootball_nfl", dec=1.91):
    return {"market": market, "selection": selection, "point": point, "home": "Home",
            "away": "Away", "sport_key": sport, "decimal_odds": dec}


class GradeTest(unittest.TestCase):
    def test_spreads(self):
        self.assertEqual(ledger.grade(bet("spreads", "Home", -3.5), 24, 20), "win")
        self.assertEqual(ledger.grade(bet("spreads", "Home", -3.0), 23, 20), "push")
        self.assertEqual(ledger.grade(bet("spreads", "Away", 3.5), 24, 20), "loss")
        self.assertEqual(ledger.grade(bet("spreads", "Away", 4.5), 24, 20), "win")

    def test_totals(self):
        self.assertEqual(ledger.grade(bet("totals", "Over", 44.5), 24, 21), "win")
        self.assertEqual(ledger.grade(bet("totals", "Under", 44.5), 24, 21), "loss")
        self.assertEqual(ledger.grade(bet("totals", "Under", 45.0), 24, 21), "push")

    def test_moneyline(self):
        self.assertEqual(ledger.grade(bet("h2h", "Away"), 1, 2), "win")
        self.assertEqual(ledger.grade(bet("h2h", "Home"), 20, 20), "push")
        self.assertEqual(ledger.grade(bet("h2h", "Home", sport="soccer_epl"), 1, 1), "loss")
        self.assertEqual(ledger.grade(bet("h2h", "Draw", sport="soccer_epl"), 1, 1), "win")

    def test_clv(self):
        close = consensus({"bookmakers": [book(-200, 170, -4.5, 44.0)]})
        self.assertEqual(ledger.clv(bet("spreads", "Home", -3.0), close), {"clv_points": 1.5})
        self.assertEqual(ledger.clv(bet("totals", "Over", 45.5), close), {"clv_points": -1.5})
        self.assertEqual(ledger.clv(bet("totals", "Under", 45.5), close), {"clv_points": 1.5})
        self.assertGreater(ledger.clv(bet("h2h", "Home", dec=1.67), close)["clv_pct"], 0)


class CardTest(unittest.TestCase):
    def test_names(self):
        self.assertTrue(ledger.same_team("AFC Bournemouth", "Bournemouth"))
        self.assertTrue(ledger.same_team("Brighton & Hove Albion", "Brighton and Hove Albion"))
        self.assertFalse(ledger.same_team("Manchester City", "Manchester United"))
        self.assertEqual(ledger.find_player("Antonio Silva", ["António Silva"]), "António Silva")
        self.assertEqual(ledger.find_player("B. Fernandes", ["Bruno Fernandes"]), "Bruno Fernandes")
        self.assertIsNone(ledger.find_player("J. Smith", ["John Smith", "Jack Smith"]))

    def test_grade_card(self):
        squad = ["Sasa Lukic", "Ryan Yates", "Neco Williams"]
        b = {"selection": "Sasa Lukic"}
        self.assertEqual(ledger.grade_card(b, ["Sasa Lukic"], squad, squad), "win")
        self.assertEqual(ledger.grade_card(b, [], squad, squad), "loss")
        self.assertEqual(ledger.grade_card(b, [], ["Ryan Yates"], squad), "push")  # unused sub
        self.assertEqual(ledger.grade_card({"selection": "Not There"}, [], squad, squad), "push")
        self.assertIsNone(ledger.grade_card(b, [], [], []))  # no roster data yet


class FlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.patches = [mock.patch.object(ledger, "DATA", root),
                        mock.patch.object(ledger, "LEDGER", root / "ledger.json"),
                        mock.patch.object(ledger, "SNAPSHOTS", root / "snapshots")]
        for p in self.patches:
            p.start()
        now = datetime.now(timezone.utc)
        self.kickoff = now + timedelta(hours=2)
        line = consensus({"bookmakers": [dict(book(-150, 130, -3.0, 45.5), title="BookA")]})
        latest = {"generated_utc": now.isoformat(), "leagues": [{
            "league": "NFL", "sport_key": "americanfootball_nfl", "games": [{
                "id": "ev1", "matchup": "Away @ Home", "home": "Home", "away": "Away",
                "kickoff_utc": self.kickoff.isoformat(), "market": line,
                "cards": {"Sasa Lukic": {"best": {"price": 200, "book": "Paddy Power"},
                                         "books": 2, "median_implied_prob": 0.35}},
                "props": {"player_anytime_td": {"region": "us", "us_proxy": True, "players": {
                    "Bijan Robinson": {"best": {"price": -120, "book": "FanDuel"}, "books": 4,
                                       "median_implied_prob": 0.53}}},
                          "player_points": {"region": "us", "us_proxy": True, "players": {
                    "Kyle Pitts": {"line": 4.5, "Over": {"price": -110, "point": 4.5, "book": "BookA"},
                                   "Under": {"price": -110, "point": 4.5, "book": "BookA"}, "books": 1,
                                   "median_no_vig_over_prob": 0.5}}}}}]}]}
        (root / "latest.json").write_text(json.dumps(latest))
        snap = root / "snapshots" / "americanfootball_nfl"
        snap.mkdir(parents=True)
        close = consensus({"bookmakers": [book(-170, 150, -4.0, 45.5)]})
        name = (self.kickoff - timedelta(minutes=30)).strftime("%Y%m%dT%H%M%SZ")
        (snap / f"{name}.json").write_text(json.dumps({"ev1": close}))

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def args(self, **kw):
        return mock.Mock(**kw)

    def test_place_and_settle(self):
        led = ledger.load()
        ledger.cmd_start(led, None)
        ledger.cmd_place(led, self.args(event="ev1", market="spreads", selection="Home",
                                        projection="-5", reason="test"))
        b = led["bets"][0]
        self.assertEqual((b["point"], b["price_american"], b["book"], b["stake"]), (-3.0, -110, "BookA", 4.0))
        with self.assertRaises(SystemExit):  # one bet per market per game
            ledger.cmd_place(led, self.args(event="ev1", market="spreads", selection="Away",
                                            projection="+5", reason="dup"))

        b["kickoff_utc"] = self.kickoff.isoformat()
        scores = [{"id": "ev1", "completed": True,
                   "scores": [{"name": "Home", "score": "27"}, {"name": "Away", "score": "20"}]}]
        with mock.patch.object(ledger, "now_utc", return_value=self.kickoff + timedelta(hours=4)), \
             mock.patch.object(ledger, "get_json", return_value=(scores, {})), \
             mock.patch.dict("os.environ", {"ODDS_API_KEY": "x"}):
            ledger.cmd_settle(led, None)
        self.assertEqual(b["result"], "win")
        self.assertAlmostEqual(b["pnl"], 3.64, places=2)
        self.assertEqual(b["clv"], {"clv_points": 1.0})
        s = ledger.summarise(led)
        self.assertEqual(s["record"], "1-0-0")
        self.assertEqual(s["beat_closing_line_pct"], 100.0)

    def test_one_bet_per_market(self):
        led = ledger.load()
        ledger.cmd_start(led, None)
        ledger.cmd_place(led, self.args(event="ev1", market="spreads", selection="Home",
                                        projection="-5", reason="spread"))
        ledger.cmd_place(led, self.args(event="ev1", market="totals", selection="Over",
                                        projection="48", reason="total"))
        self.assertEqual([b["market"] for b in led["bets"]], ["spreads", "totals"])

    def test_card_bet(self):
        led = ledger.load()
        ledger.cmd_start(led, None)
        ledger.cmd_place(led, self.args(event="ev1", market="spreads", selection="Home",
                                        projection="-5", reason="match bet"))
        ledger.cmd_place(led, self.args(event="ev1", market="cards", selection="Sasa Lukic",
                                        projection="0.45", reason="strict referee"))
        b = led["bets"][1]
        self.assertEqual((b["decimal_odds"], b["book"]), (3.0, "Paddy Power"))
        with self.assertRaises(SystemExit):  # one card bet per player
            ledger.cmd_place(led, self.args(event="ev1", market="cards", selection="Sasa Lukic",
                                            projection="0.45", reason="dup"))

        scoreboard = {"events": [{"id": "e9", "competitions": [{
            "status": {"type": {"completed": True}},
            "competitors": [{"homeAway": "home", "team": {"displayName": "Home FC"}, "score": "1"},
                            {"homeAway": "away", "team": {"displayName": "Away"}, "score": "0"}],
            "details": [{"yellowCard": True, "athletesInvolved": [{"displayName": "Saša Lukić"}]}]}]}]}
        summary = {"rosters": [{"roster": [{"starter": True, "athlete": {"displayName": "Saša Lukić"}}]}]}

        def fake_get(url, params=None):
            if url.endswith("/scores"):
                return [], {}
            return (scoreboard if url.endswith("scoreboard") else summary), {}

        led["bets"] = [b]  # settle the card bet only; match bets are covered above
        with mock.patch.object(ledger, "now_utc", return_value=self.kickoff + timedelta(hours=4)), \
             mock.patch.object(ledger, "get_json", fake_get):
            ledger.cmd_settle(led, None)
        self.assertEqual((b["result"], b["pnl"]), ("win", 8.0))
        self.assertEqual(ledger.summarise(led)["by_market"]["cards"]["bets"], 1)

    def test_prop_bets(self):
        led = ledger.load()
        ledger.cmd_start(led, None)
        ledger.cmd_place(led, self.args(event="ev1", market="props", prop="player_anytime_td",
                                        selection="Bijan Robinson", side=None, projection="0.6", reason="td"))
        ledger.cmd_place(led, self.args(event="ev1", market="props", prop="player_points",
                                        selection="Kyle Pitts", side="Over", projection="6", reason="pts"))
        td, pts = led["bets"]
        self.assertEqual((td["side"], td["point"], td["us_proxy"], td["book"]), ("Yes", None, True, "FanDuel"))
        self.assertEqual((pts["side"], pts["point"]), ("Over", 4.5))
        with self.assertRaises(SystemExit):  # same player and prop twice
            ledger.cmd_place(led, self.args(event="ev1", market="props", prop="player_anytime_td",
                                            selection="Bijan Robinson", side=None, projection="0.6", reason="dup"))
        with self.assertRaises(SystemExit):  # an over/under prop needs a side
            ledger.cmd_place(led, self.args(event="ev1", market="props", prop="player_points",
                                            selection="Kyle Pitts", side=None, projection="6", reason="x"))

        scoreboard = {"events": [{"id": "e1", "competitions": [{
            "status": {"type": {"completed": True}},
            "competitors": [{"homeAway": "home", "team": {"displayName": "Home"}, "score": "29"},
                            {"homeAway": "away", "team": {"displayName": "Away"}, "score": "20"}]}]}]}
        summary = {"boxscore": {"players": [{"statistics": [
            {"name": "rushing", "keys": ["rushingAttempts", "rushingYards", "rushingTouchdowns"],
             "athletes": [{"athlete": {"displayName": "Bijan Robinson"}, "stats": ["18", "92", "1"]}]},
            {"name": "passing", "keys": ["passingYards", "passingTouchdowns"],
             "athletes": [{"athlete": {"displayName": "Kyle Pitts"}, "stats": ["20", "1"]}]},
            {"name": "receiving", "keys": ["receptions", "points"],
             "athletes": [{"athlete": {"displayName": "Kyle Pitts"}, "stats": ["3", "4"]}]}]}]}}

        def fake_get(url, params=None):
            return (scoreboard if url.endswith("scoreboard") else summary), {}

        with mock.patch.object(ledger, "now_utc", return_value=self.kickoff + timedelta(hours=4)), \
             mock.patch.object(ledger, "get_json", fake_get):
            ledger.cmd_settle(led, None)
        self.assertEqual((td["result"], td["actual"]), ("win", 1.0))
        self.assertEqual((pts["result"], pts["actual"]), ("loss", 4.0))  # 4 < 4.5; passing TD ignored
        self.assertEqual(set(ledger.summarise(led)["by_market"]), {"player_anytime_td", "player_points"})

    def test_prop_void_when_player_did_not_play(self):
        b = {"selection": "Bench Guy", "side": "Over", "point": 10.5}
        self.assertEqual(ledger.grade_prop(b, {"Starter": 20}), "push")
        self.assertEqual(ledger.find_player("Kyle Pitts", ["Kyle Pitts Sr.", "Drake London"]), "Kyle Pitts Sr.")


if __name__ == "__main__":
    unittest.main()

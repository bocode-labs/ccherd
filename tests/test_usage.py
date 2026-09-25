import datetime as dt
import unittest

from ccherd.usage import FIVE_HOUR_GATE, WEEKLY_GATE, score

H = 3600.0
NOW = 1_800_000_000.0


def iso(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat()


def usage(five: float, week: float, week_reset_h: float, five_reset_h: float = 2.0, **extra) -> dict:
    return {"five_hour": {"utilization": five, "resets_at": iso(NOW + five_reset_h * H)},
            "seven_day": {"utilization": week, "resets_at": iso(NOW + week_reset_h * H)}, **extra}


class Score(unittest.TestCase):
    def test_picks_the_account_with_most_to_lose_not_the_earliest_reset(self):
        # Account c resets first but has only 5% left; account a's 5h window is full.
        a = score(usage(100, 11, 103), "opus", NOW)
        b = score(usage(36, 4, 83), "opus", NOW)
        c = score(usage(1, 95, 19.5), "opus", NOW)
        self.assertIsNone(a["score"])
        self.assertGreater(b["score"], c["score"])

    def test_earlier_reset_wins_at_equal_headroom(self):
        self.assertGreater(score(usage(0, 40, 24), "opus", NOW)["score"],
                           score(usage(0, 40, 48), "opus", NOW)["score"])

    def test_load_spreads_instead_of_draining_one_account(self):
        later = score(usage(0, 40, 48), "opus", NOW)["score"]
        earlier_after_use = score(usage(0, 80, 20), "opus", NOW)["score"]
        self.assertLess(earlier_after_use, later)

    def test_full_five_hour_window_excludes(self):
        self.assertIsNone(score(usage(FIVE_HOUR_GATE, 0, 24), None, NOW)["score"])

    def test_nearly_spent_week_excludes(self):
        self.assertIsNone(score(usage(0, WEEKLY_GATE, 24), None, NOW)["score"])

    def test_rolled_over_windows_count_as_empty(self):
        s = score(usage(100, 100, -1, five_reset_h=-0.1), None, NOW)
        self.assertEqual((s["five_hour"], s["weekly"]), (0.0, 0.0))
        self.assertIsNotNone(s["score"])

    def test_per_model_weekly_limit_applies_when_it_is_tighter(self):
        u = usage(0, 10, 48, seven_day_opus={"utilization": 99, "resets_at": iso(NOW + 48 * H)})
        self.assertIsNone(score(u, "claude-opus-5-5", NOW)["score"])
        self.assertIsNotNone(score(u, "sonnet", NOW)["score"])

    def test_five_hour_load_lowers_the_score(self):
        self.assertGreater(score(usage(10, 40, 24), None, NOW)["score"],
                           score(usage(60, 40, 24), None, NOW)["score"])


if __name__ == "__main__":
    unittest.main()


class ScopedLimits(unittest.TestCase):
    def test_a_models_own_weekly_limit_from_limits_applies(self):
        from ccherd.usage import model_limit
        u = usage(0, 10, 48, limits=[{"kind": "weekly_scoped", "percent": 99, "resets_at": iso(NOW + 48 * H),
                                      "scope": {"model": {"display_name": "Fable"}}}])
        self.assertEqual(model_limit(u, "claude-fable-5-1")["utilization"], 99)
        self.assertIsNone(score(u, "fable", NOW)["score"])
        self.assertIsNotNone(score(u, "opus", NOW)["score"])

    def test_configured_limits_move_the_gates(self):
        u = usage(95, 10, 48)
        self.assertIsNone(score(u, None, NOW)["score"])
        self.assertIsNotNone(score(u, None, NOW, five_gate=100)["score"])


class Saturated(unittest.TestCase):
    ROWS = [{"account": "a", "score": None, "five_hour": 95, "weekly": 50, "extra_usage": False, "reason": "5h window at 95%"},
            {"account": "b", "score": None, "five_hour": 99, "weekly": 90, "extra_usage": True, "reason": "5h window at 99%"},
            {"account": "c", "score": None, "five_hour": 92, "weekly": 20, "extra_usage": True, "reason": "5h window at 92%"}]

    def test_prefers_an_account_that_can_still_run_then_the_least_loaded(self):
        from ccherd.usage import pick_when_saturated
        self.assertEqual(pick_when_saturated(self.ROWS)["account"], "c")

    def test_policy_decides_between_refusing_and_using(self):
        from unittest import mock

        from ccherd import usage as u
        with mock.patch.object(u, "rank_accounts", return_value=self.ROWS):
            with mock.patch("ccherd.config.policy", return_value={"when-saturated": "refuse"}):
                with self.assertRaises(SystemExit) as e:
                    u.pick_account(None)
                self.assertIn("ccherd config when-saturated use", str(e.exception))
            with mock.patch("ccherd.config.policy", return_value={"when-saturated": "use"}), \
                 mock.patch("sys.stderr"):
                self.assertEqual(u.pick_account(None), "c")

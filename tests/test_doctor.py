import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from ccherd import doctor, setup
from ccherd import profile as ccherd_profile
from ccherd.config import Account, Settings


def login(cfg: Path, expires_in: float = 86400) -> None:
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {
        "accessToken": "t", "subscriptionType": "max", "expiresAt": (time.time() + 3600) * 1000,
        "refreshTokenExpiresAt": (time.time() + expires_in) * 1000}}))


def profile(user: str, org: str) -> dict:
    return {"account": {"uuid": user, "email": f"{user}@example.com"},
            "organization": {"uuid": org, "name": f"org {org}"}}


class Accounts(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)

    def reports(self, profiles: dict) -> list:
        for name in profiles:
            login(self.home / name)
        with mock.patch.object(doctor, "fetch_profile", side_effect=lambda cfg: profiles[cfg.name]):
            reports = [doctor.check_account(Account(n, self.home / n)) for n in profiles]
        doctor.mark_duplicate_seats(reports)
        return reports

    @staticmethod
    def status(report, what: str) -> str | None:
        return next((c.status for c in report.checks if c.what == what), None)

    def test_logged_in_account_is_ok(self):
        [r] = self.reports({"a": profile("u1", "o1")})
        self.assertEqual([c.status for c in r.checks], [doctor.OK, doctor.OK, doctor.OK])
        self.assertIn("u1@example.com", r.checks[0].detail)

    def test_missing_dir_and_missing_login_fail_with_the_login_command(self):
        (self.home / "empty").mkdir()
        for name in ("gone", "empty"):
            [c] = doctor.check_account(Account(name, self.home / name)).checks
            self.assertEqual(c.status, doctor.FAIL)
            self.assertIn(f"CLAUDE_CONFIG_DIR={self.home / name} claude", c.detail)

    def test_expired_login_fails(self):
        login(self.home / "old", expires_in=-60)
        [c] = doctor.check_account(Account("old", self.home / "old")).checks
        self.assertEqual((c.what, c.status), ("login", doctor.FAIL))

    def test_two_dirs_on_the_same_seat_are_flagged(self):
        a, b = self.reports({"a": profile("u1", "o1"), "b": profile("u1", "o1")})
        self.assertIsNone(self.status(a, "seat"))
        self.assertEqual(self.status(b, "seat"), doctor.WARN)

    def test_one_login_in_two_organizations_is_two_seats(self):
        # a private plan and a company team under the same login
        a, b = self.reports({"a": profile("u1", "private"), "b": profile("u1", "team")})
        self.assertIsNone(self.status(b, "seat"))

    def test_unknown_organization_is_a_warning_not_a_duplicate(self):
        a, b = self.reports({"a": profile("u1", "o1"), "b": None})
        self.assertEqual(self.status(b, "organization"), doctor.WARN)
        self.assertIsNone(self.status(b, "seat"))


class ProfileCache(unittest.TestCase):
    def test_an_expired_token_falls_back_to_the_last_answer(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch("ccherd.config.CACHE_DIR", Path(tmp)):
            cfg = Path(tmp) / "a"
            valid = {"accessToken": "t", "expiresAt": (time.time() + 60) * 1000}
            with mock.patch("ccherd.profile.fresh_oauth", return_value=valid), \
                 mock.patch("ccherd.api.get", return_value=profile("u1", "o1")) as get:
                ccherd_profile.fetch_profile(cfg)
                self.assertEqual(get.call_count, 1)
            with mock.patch("ccherd.profile.fresh_oauth", return_value={"accessToken": "t", "expiresAt": 0}), \
                 mock.patch("ccherd.api.get") as get:
                cached = ccherd_profile.fetch_profile(cfg)
                get.assert_not_called()
            self.assertEqual(cached["organization"]["uuid"], "o1")


class Organization(unittest.TestCase):
    def test_dirs_of_another_organization_are_left_out_even_when_a_schema_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            for name in (".claude", ".claude-2", ".claude-3"):
                (home / name).mkdir()
            orgs = {".claude": profile("u1", "private"), ".claude-2": profile("u1", "team"), ".claude-3": None}
            s = Settings(dirs=[], schemas=[home / ".claude"], organizations=[{"uuid": "team", "name": "Team"}])
            with mock.patch("ccherd.profile.cached_profile", side_effect=lambda cfg: orgs[cfg.name]):
                self.assertEqual([a.label for a in s.accounts()], ["2", "3"])  # unknown stays in
                self.assertEqual(len(s.all_accounts()), 3)
                # two private plans are two organizations; choosing both keeps both
                s.organizations = [{"uuid": "private", "name": "A"}, {"uuid": "team", "name": "B"}]
                self.assertEqual([a.label for a in s.accounts()], ["default", "2", "3"])


class NewAccounts(unittest.TestCase):
    def test_dirs_are_numbered_after_the_current_one(self):
        self.assertEqual([p.name for p in setup.new_account_dirs(3, Path("/h/.claude"))],
                         [".claude", ".claude-2", ".claude-3"])
        self.assertEqual([p.name for p in setup.new_account_dirs(2, Path("/h/.claude-work3"))],
                         [".claude-work", ".claude-work-2"])

    def test_prepare_saves_a_schema_and_prints_login_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / ".claude"
            with mock.patch("ccherd.config.current_dir", return_value=base), \
                 mock.patch("ccherd.config.CONFIG_FILE", Path(tmp) / "c.json"), \
                 mock.patch("builtins.print") as out:
                setup._prepare_new_accounts(2)
            printed = "\n".join(str(c.args[0]) if c.args else "" for c in out.call_args_list)
            self.assertTrue((Path(tmp) / ".claude-2").is_dir())
            self.assertIn(f"CLAUDE_CONFIG_DIR={Path(tmp) / '.claude-2'} claude", printed)
            self.assertIn(".claude", json.loads((Path(tmp) / "c.json").read_text())["schemas"][0])


if __name__ == "__main__":
    unittest.main()

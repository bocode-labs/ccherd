import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ccherd import config
from ccherd.config import Settings, label_for, schema_members, schema_of
from ccherd.credentials import keychain_service


class Accounts(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        for name in (".claude", ".claude-work", ".claude-work2", ".claude-work-3", ".claude-workshop", ".claude-x"):
            (self.home / name).mkdir()

    def test_labels(self):
        self.assertEqual(label_for(Path("/h/.claude")), "default")
        self.assertEqual(label_for(Path("/h/.claude-work2")), "work2")
        self.assertEqual(label_for(Path("/h/other")), "other")

    def test_schema_of_strips_the_trailing_number(self):
        self.assertEqual(schema_of(self.home / ".claude-work2"), self.home / ".claude-work")
        self.assertEqual(schema_of(self.home / ".claude-work-3"), self.home / ".claude-work")
        self.assertEqual(schema_of(self.home / ".claude-2"), self.home / ".claude")

    def test_schema_matches_numbered_siblings_only(self):
        names = [p.name for p in schema_members(self.home / ".claude-work")]
        self.assertEqual(names, [".claude-work", ".claude-work2", ".claude-work-3"])

    def test_a_schema_picks_up_dirs_created_after_setup(self):
        s = Settings(dirs=[self.home / ".claude-x"], schemas=[self.home / ".claude-work"])
        self.assertEqual(len(s.accounts()), 4)
        (self.home / ".claude-work4").mkdir()
        self.assertIn("work4", [a.label for a in s.accounts()])

    def test_a_dir_listed_twice_counts_once(self):
        s = Settings(dirs=[self.home / ".claude-work"], schemas=[self.home / ".claude-work"])
        self.assertEqual([a.label for a in s.accounts()], ["work", "work2", "work-3"])

    def test_missing_config_exits_with_the_not_set_up_code(self):
        with mock.patch.object(config, "CONFIG_FILE", self.home / "nope.json"):
            with self.assertRaises(SystemExit) as e:
                config.load_accounts()
        self.assertEqual(e.exception.code, config.EXIT_NOT_SET_UP)

    def test_settings_round_trip(self):
        with mock.patch.object(config, "CONFIG_FILE", self.home / "c" / "config.json"):
            config.save_settings(Settings(dirs=[self.home / ".claude-x"], schemas=[self.home / ".claude-work"]))
            loaded = config.load_settings()
        self.assertEqual(loaded.schemas, [self.home / ".claude-work"])


class Keychain(unittest.TestCase):
    def test_service_name_is_suffixed_with_the_dir_hash(self):
        # sha256("/Users/someone/.claude-work")[:8], as Claude Code computes it
        self.assertRegex(keychain_service(Path("/Users/someone/.claude-work")), r"^Claude Code-credentials-[0-9a-f]{8}$")
        self.assertEqual(keychain_service(config.DEFAULT_CLAUDE_DIR), "Claude Code-credentials")


if __name__ == "__main__":
    unittest.main()


class Renewal(unittest.TestCase):
    def test_claude_is_not_asked_to_renew_while_a_session_runs_on_the_account(self):
        from ccherd import credentials
        expired = {"accessToken": "t", "expiresAt": 0}
        with mock.patch.object(credentials, "read_oauth", return_value=expired), \
             mock.patch("ccherd.sessions.sessions_in", return_value=[{"pid": 1}]), \
             mock.patch("subprocess.run") as run:
            credentials.fresh_oauth(Path("/h/.claude-x"))
            run.assert_not_called()
        with mock.patch.object(credentials, "read_oauth", return_value=expired), \
             mock.patch("ccherd.sessions.sessions_in", return_value=[]), \
             mock.patch("shutil.which", return_value="/bin/claude"), \
             mock.patch("subprocess.run") as run:
            credentials.fresh_oauth(Path("/h/.claude-x"))
            self.assertEqual(run.call_args.args[0], ["claude", "auth", "status"])
            self.assertEqual(run.call_args.kwargs["env"]["CLAUDE_CONFIG_DIR"], "/h/.claude-x")


class OldConfig(unittest.TestCase):
    def test_a_single_organization_from_0_2_is_read_as_a_list(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(config, "CONFIG_FILE", Path(tmp) / "c.json"):
            (Path(tmp) / "c.json").write_text('{"dirs": [], "schemas": [], "organization": {"uuid": "t", "name": "T"}}')
            self.assertEqual(config.load_settings().organizations, [{"uuid": "t", "name": "T"}])
            config.save_settings(config.load_settings())
            self.assertNotIn('"organization"', (Path(tmp) / "c.json").read_text())


class Policy(unittest.TestCase):
    def test_settings_survive_a_new_setup_and_are_validated(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(config, "CONFIG_FILE", Path(tmp) / "c.json"):
            config.save_settings(Settings(dirs=[], schemas=[]))
            self.assertEqual(config.policy()["when-saturated"], "refuse")
            config.set_policy("when-saturated", "use")
            config.set_policy("five-hour-limit", "100")
            config.save_settings(Settings(dirs=[Path(tmp)], schemas=[]))  # setup runs again
            self.assertEqual(config.policy()["when-saturated"], "use")
            self.assertEqual(config.policy()["five-hour-limit"], 100)
            for key, value in (("when-saturated", "maybe"), ("weekly-limit", "0"), ("nope", "1")):
                with self.assertRaises(SystemExit):
                    config.set_policy(key, value)

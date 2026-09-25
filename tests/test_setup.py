import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ccherd import setup
from ccherd.config import Account


class Candidates(unittest.TestCase):
    def test_a_numbered_family_gets_a_schema_line_with_its_members_below(self):
        h = Path("/h")
        found = [h / ".claude", h / ".claude-mpp", h / ".claude-mpp2", h / ".claude-mpp3", h / ".claude-solo"]
        got = [(c.path.name, c.is_schema, c.indent) for c in setup.candidates(found)]
        self.assertEqual(got, [(".claude", False, False),
                               (".claude-mpp", True, False), (".claude-mpp", False, True),
                               (".claude-mpp2", False, True), (".claude-mpp3", False, True),
                               (".claude-solo", False, False)])


class ClaudeLocal(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)

    def test_creates_the_file_and_ignores_it(self):
        path, ignored = setup.write_claude_local(self.root)
        self.assertTrue(ignored)
        self.assertIn("CLAUDE.local.md", (self.root / ".gitignore").read_text())
        self.assertIn(setup.BEGIN, path.read_text())

    def test_rerun_replaces_the_block_and_keeps_the_rest(self):
        (self.root / "CLAUDE.local.md").write_text("# mine\n\nkeep me\n")
        setup.write_claude_local(self.root)
        _, ignored_again = setup.write_claude_local(self.root)
        text = (self.root / "CLAUDE.local.md").read_text()
        self.assertEqual(text.count(setup.BEGIN), 1)
        self.assertIn("keep me", text)
        self.assertFalse(ignored_again)
        self.assertEqual((self.root / ".gitignore").read_text().count("CLAUDE.local.md"), 1)


class Skill(unittest.TestCase):
    def test_home_target_writes_into_every_account(self):
        with tempfile.TemporaryDirectory() as tmp:
            accounts = [Account("a", Path(tmp) / ".claude-a"), Account("b", Path(tmp) / ".claude-b")]
            written = setup.install_skill("home", Path(tmp), accounts)
            self.assertEqual(len(written), 2)
            self.assertTrue(all(p.read_text().startswith("---\nname: ccherd") for p in written))

    def test_accounts_sharing_a_skills_dir_get_one_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            main, second = Path(tmp) / ".claude-a", Path(tmp) / ".claude-a2"
            (main / "skills").mkdir(parents=True)
            second.mkdir()
            (second / "skills").symlink_to(main / "skills")
            accounts = [Account("a", main), Account("a2", second)]
            self.assertEqual(len(setup.install_skill("home", Path(tmp), accounts)), 1)
            self.assertIn("a2 link to it", setup.describe_skill_homes(accounts))

    def test_repo_target_writes_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            written = setup.install_skill("repo", Path(tmp), [])
            self.assertEqual(written, [Path(tmp) / ".claude/skills/ccherd/SKILL.md"])


if __name__ == "__main__":
    unittest.main()


class OrganizationChoice(unittest.TestCase):
    ORGS = {Path("/h/.claude"): {"uuid": "p", "name": "Private"},
            Path("/h/.claude-mpp"): {"uuid": "t", "name": "Team"},
            Path("/h/.claude-mpp2"): {"uuid": "t", "name": "Team"}}

    def choose(self, current: str, **flags):
        a = mock.Mock(organization=None, dir=None, schema=None, yes=True)
        for k, v in flags.items():
            setattr(a, k, v)
        with mock.patch.object(setup, "is_logged_in", return_value=True), \
             mock.patch.object(setup, "fetch_profile", side_effect=lambda p: p), \
             mock.patch.object(setup, "organization", side_effect=lambda p: self.ORGS[p]), \
             mock.patch("ccherd.config.current_dir", return_value=Path(current)):
            return setup._choose_organization(a, list(self.ORGS))[0]

    def test_defaults_to_the_organization_of_the_current_account(self):
        self.assertEqual(self.choose("/h/.claude-mpp2")["name"], "Team")
        self.assertEqual(self.choose("/h/.claude")["name"], "Private")

    def test_the_flag_picks_by_name_and_rejects_unknown_names(self):
        self.assertEqual(self.choose("/h/.claude", organization="Team")["uuid"], "t")
        with self.assertRaises(SystemExit):
            self.choose("/h/.claude", organization="Nope")


class Keys(unittest.TestCase):
    def test_a_burst_of_keys_is_split(self):
        from ccherd.tui import _keys
        self.assertEqual(_keys(b"\x1bOB\x1b[Bab\r"), [b"\x1bOB", b"\x1b[B", b"a", b"b", b"\r"])
        self.assertEqual(_keys(b"\x1b"), [b"\x1b"])

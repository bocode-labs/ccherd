import json
import tempfile
import unittest
from pathlib import Path

from ccherd import links
from ccherd.config import Account


class Links(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.main, self.second, self.third = (Account(n, self.home / n) for n in ("main", "second", "third"))
        for a in (self.main, self.second, self.third):
            a.dir.mkdir()

    def accounts(self):
        return [self.main, self.second, self.third]

    def test_primary_is_the_dir_the_others_already_link_to(self):
        (self.second.dir / "skills").mkdir()
        (self.third.dir / "skills").symlink_to(self.second.dir / "skills")
        self.assertEqual(links.primary(self.accounts()), self.second)
        self.assertEqual(links.primary([self.main, self.third]), self.main)  # no links: the first

    def test_states(self):
        (self.main.dir / "skills").mkdir()
        (self.main.dir / "projects").mkdir()
        (self.second.dir / "skills").symlink_to(self.main.dir / "skills")
        (self.third.dir / "projects").mkdir()
        _, items = links.plan(self.accounts())
        got = {(it.account.label, it.name): it.state for it in items}
        self.assertEqual(got, {("second", "skills"): links.LINKED, ("second", "projects"): links.MISSING,
                               ("third", "skills"): links.MISSING, ("third", "projects"): links.OWN_COPY})

    def test_fix_merges_an_own_copy_and_keeps_what_differs(self):
        mp = self.main.dir / "projects" / "-repo"
        mp.mkdir(parents=True)
        (mp / "a.jsonl").write_text("main")
        (mp / "same.jsonl").write_text("x")
        tp = self.third.dir / "projects" / "-repo"
        tp.mkdir(parents=True)
        (tp / "b.jsonl").write_text("third")        # new: moves over
        (tp / "same.jsonl").write_text("x")         # identical: dropped
        (tp / "a.jsonl").write_text("third's own")  # differs: stays in the backup
        (tp / "memory").mkdir()
        (tp / "memory" / "m.md").write_text("remember")

        main, items = links.plan(self.accounts())
        links.apply(main, items, log=lambda _: None)

        link = self.third.dir / "projects"
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.resolve(), (self.main.dir / "projects").resolve())
        self.assertEqual((mp / "a.jsonl").read_text(), "main")
        self.assertEqual((mp / "b.jsonl").read_text(), "third")
        self.assertEqual((mp / "memory" / "m.md").read_text(), "remember")
        [backup] = self.third.dir.glob("projects.ccherd-backup-*")
        self.assertEqual([p.name for p in backup.rglob("*") if p.is_file()], ["a.jsonl"])
        self.assertTrue((self.second.dir / "projects").is_symlink())  # missing one got linked too

    def test_settings_merge_unites_lists_and_keeps_the_primary_on_conflicts(self):
        (self.main.dir / "settings.json").write_text(json.dumps(
            {"model": "opus", "permissions": {"allow": ["Bash(ls)"]}}))
        (self.second.dir / "settings.json").write_text(json.dumps(
            {"model": "sonnet", "theme": "dark", "permissions": {"allow": ["Bash(ls)", "Bash(git status)"]}}))
        main, items = links.plan([self.main, self.second])
        links.apply(main, items, log=lambda _: None)
        merged = json.loads((self.main.dir / "settings.json").read_text())
        self.assertEqual(merged, {"model": "opus", "theme": "dark",
                                  "permissions": {"allow": ["Bash(ls)", "Bash(git status)"]}})
        self.assertTrue((self.second.dir / "settings.json").is_symlink())
        self.assertEqual(list(self.second.dir.glob("*.ccherd-backup-*")), [])

    def test_an_item_only_the_other_account_has_moves_to_the_primary(self):
        (self.second.dir / "CLAUDE.md").write_text("mine")
        main, items = links.plan([self.main, self.second])
        links.apply(main, items, log=lambda _: None)
        self.assertEqual((self.main.dir / "CLAUDE.md").read_text(), "mine")
        self.assertTrue((self.second.dir / "CLAUDE.md").is_symlink())


if __name__ == "__main__":
    unittest.main()

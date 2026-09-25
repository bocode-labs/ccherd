import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ccherd import agents, config


class Command(unittest.TestCase):
    META = {"model": "claude-opus-5-5", "permission_mode": "bypassPermissions", "owner": "o", "name": "n"}

    def test_effort_is_passed_on_every_turn_including_resumes(self):
        first = agents.claude_cmd({**self.META, "effort": "low"}, "task")
        resumed = agents.claude_cmd({**self.META, "effort": "low", "session_id": "sid"}, "more")
        for cmd in (first, resumed):
            self.assertEqual(cmd[cmd.index("--effort") + 1], "low")
        self.assertEqual(resumed[resumed.index("--resume") + 1], "sid")
        self.assertEqual(resumed[-1], "more")

    def test_no_effort_flag_when_none_was_chosen(self):
        self.assertNotIn("--effort", agents.claude_cmd(self.META, "task"))


class Registry(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.object(config, "STATE_DIR", Path(tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.d = config.STATE_DIR / "owner" / "a"
        self.d.mkdir(parents=True)
        agents.save_meta(self.d, {"name": "a", "status": "running", "supervisor_pid": 2 ** 22 + 7,
                                  "supervisor_start": "1", "turns": 0})

    def test_dead_supervisor_reads_as_lost(self):
        self.assertEqual(agents.refreshed(self.d)["status"], "lost")

    def test_inbox_drains_once(self):
        with agents.locked(self.d):
            agents._queue(self.d, "x")
            agents._queue(self.d, "y")
            self.assertEqual(agents._take_inbox(self.d), ["x", "y"])
            self.assertEqual(agents._take_inbox(self.d), [])

    def test_agent_names_are_path_safe(self):
        with self.assertRaises(SystemExit):
            agents.agent_dir("owner", "../escape")


if __name__ == "__main__":
    unittest.main()


class Prune(unittest.TestCase):
    DAY = 86400.0

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.object(config, "STATE_DIR", Path(tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.now = 2_000_000_000.0

    def agent(self, owner: str, name: str, status: str, days_ago: float) -> Path:
        d = config.STATE_DIR / owner / name
        d.mkdir(parents=True)
        agents.save_meta(d, {"name": name, "owner": owner, "status": status, "turns": 1,
                             "ended_at": self.now - days_ago * self.DAY, "created_at": self.now - 30 * self.DAY})
        return d

    def test_only_old_finished_agents_of_ended_sessions_go(self):
        self.agent("gone", "old", "idle", 10)
        self.agent("gone", "recent", "idle", 2)
        self.agent("live", "old-but-live-owner", "idle", 10)
        self.agent("gone", "failed-old", "failed", 10)
        with mock.patch.object(agents, "pid_alive", return_value=True):
            self.agent("gone", "running-old", "running", 10)
            got = {m["name"] for _, m in agents.prunable(7, {"live"}, now=self.now)}
        self.assertEqual(got, {"old", "failed-old"})

    def test_removing_the_last_agent_removes_the_owner_dir(self):
        d = self.agent("gone", "old", "idle", 10)
        agents.remove(d)
        self.assertFalse((config.STATE_DIR / "gone").exists())

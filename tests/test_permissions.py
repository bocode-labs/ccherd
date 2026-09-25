import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ccherd.permissions import caller_mode, check_not_wider, mode_class


class Permissions(unittest.TestCase):
    def test_caller_mode_is_the_last_one_in_the_transcript(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "projects" / "-some-cwd"
            proj.mkdir(parents=True)
            lines = [{"permissionMode": "bypassPermissions"}, {"x": 1}, {"permissionMode": "plan"}, {"y": 2}]
            (proj / "sid-1.jsonl").write_text("".join(json.dumps(l, separators=(",", ":")) + "\n" for l in lines))
            with mock.patch.dict(os.environ, CLAUDE_CODE_SESSION_ID="sid-1", CLAUDE_CONFIG_DIR=tmp):
                self.assertEqual(caller_mode(), "plan")
            with mock.patch.dict(os.environ, CLAUDE_CODE_SESSION_ID="sid-unknown", CLAUDE_CONFIG_DIR=tmp):
                self.assertIsNone(caller_mode())

    def test_a_subagent_may_be_narrower_than_its_caller_never_wider(self):
        check_not_wider("bypassPermissions", "bypassPermissions")
        check_not_wider("plan", "bypassPermissions")
        check_not_wider("manual", "default")
        for child, caller in (("bypassPermissions", "acceptEdits"), ("auto", "default"), ("acceptEdits", "plan"),
                              ("bypassPermissions", None), ("somethingNew", "bypassPermissions")):
            with self.assertRaises(SystemExit, msg=f"{child} under {caller}"):
                check_not_wider(child, caller)

    def test_mode_classes(self):
        self.assertEqual(mode_class("bypassPermissions"), "bypass")
        self.assertEqual(mode_class("acceptEdits"), "prompting")
        self.assertIsNone(mode_class(None))


if __name__ == "__main__":
    unittest.main()

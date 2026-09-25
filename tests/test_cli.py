import argparse
import unittest

from ccherd import cli


class FullHelp(unittest.TestCase):
    def test_lists_every_command_and_every_option(self):
        p = cli.parser()
        text = cli.full_help(p)
        sub = next(a for a in p._actions if isinstance(a, argparse._SubParsersAction))
        for name, sp in sub.choices.items():
            if name.startswith("_"):
                self.assertNotIn(f"\n{name} - ", text)
                continue
            self.assertIn(f"\n{name} - ", text)
            for action in sp._actions:
                for flag in action.option_strings:
                    if flag not in ("-h", "--help"):
                        self.assertIn(flag, text, f"{name} {flag}")


if __name__ == "__main__":
    unittest.main()


class UsageReport(unittest.TestCase):
    def test_limits_and_extra_usage_are_read_from_the_reading(self):
        from ccherd import commands
        data = {"limits": [{"kind": "session", "percent": 87, "severity": "warning", "resets_at": None},
                           {"kind": "weekly_scoped", "percent": 3, "scope": {"model": {"display_name": "Fable"}}}],
                "extra_usage": {"is_enabled": True},
                "spend": {"used": {"amount_minor": 241643, "currency": "EUR", "exponent": 2}}}
        self.assertEqual([r[:3] for r in commands.limit_rows(data)],
                         [("5 hours", 87.0, "warning"), ("week Fable", 3.0, "")])
        self.assertEqual(commands.extra_usage(data), "on - 2,416.43 EUR used so far")
        self.assertEqual(commands.extra_usage({"extra_usage": {"is_enabled": False}}), "off")
        self.assertEqual(commands.bar(50, width=4), "██░░")

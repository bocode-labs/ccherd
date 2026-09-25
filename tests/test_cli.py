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

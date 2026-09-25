import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ccherd import version


class Version(unittest.TestCase):
    def test_versions_compare_numerically(self):
        self.assertGreater(version.parse("0.10.0"), version.parse("0.9.1"))

    def test_an_older_install_is_told_how_to_upgrade(self):
        with mock.patch.object(version, "latest", return_value="9.0.0"), \
             mock.patch.object(version.sys, "prefix", "/Users/x/.local/share/uv/tools/ccherd"):
            ok, detail = version.status()
        self.assertFalse(ok)
        self.assertIn("uv tool upgrade ccherd", detail)

    def test_offline_is_not_a_warning(self):
        with mock.patch.object(version, "latest", return_value=None):
            self.assertIsNone(version.status()[0])

    def test_pypi_is_asked_at_most_once_a_day(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch("ccherd.config.CACHE_DIR", Path(tmp)):
            answer = mock.MagicMock()
            answer.__enter__.return_value.read.return_value = b'{"info": {"version": "1.2.3"}}'
            with mock.patch("urllib.request.urlopen", return_value=answer) as get:
                self.assertEqual(version.latest(), "1.2.3")
                self.assertEqual(version.latest(), "1.2.3")
            self.assertEqual(get.call_count, 1)


if __name__ == "__main__":
    unittest.main()

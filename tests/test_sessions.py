import json
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from ccherd import sessions


class Inbox(unittest.TestCase):
    def test_deliver_sends_auth_then_one_user_frame(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:  # short path: AF_UNIX limit on macOS
            cfg = Path(tmp)
            (cfg / "sessions").mkdir()
            (cfg / "sessions" / f"4242.{'a' * 64}.key").write_text(json.dumps({"peerToken": "b" * 32}))
            path = str(cfg / "s.sock")
            srv = socket.socket(socket.AF_UNIX)
            srv.bind(path)
            srv.listen(1)
            got = []

            def serve():
                c, _ = srv.accept()
                self.addCleanup(c.close)
                buf = b""
                while chunk := c.recv(4096):
                    buf += chunk
                got.extend(json.loads(line) for line in buf.decode().splitlines())

            t = threading.Thread(target=serve)
            t.start()
            sessions.deliver({"pid": 4242, "config_dir": str(cfg), "messagingSocketPath": path}, "hello",
                             sender="ccherd:t")
            t.join(5)
            srv.close()
        self.assertEqual(got[0], {"type": "auth", "token": "b" * 32})
        self.assertEqual(got[1]["type"], "user")
        self.assertEqual(got[1]["from"], "ccherd:t")
        self.assertTrue(got[1]["message"]["content"].startswith('<cross-session-message from="ccherd:t">'))
        self.assertIn("hello", got[1]["message"]["content"])

    def test_envelope_attribute_order_is_fixed(self):
        self.assertEqual(sessions.wrap("b", "ccherd:t", "bypass"),
                         '<cross-session-message from="ccherd:t" from-mode="bypass">\nb\n</cross-session-message>')


class Addressing(unittest.TestCase):
    SESSIONS = [{"name": "a", "pid": 1, "messagingSocketPath": "/tmp/cc-socks/1.sock"},
                {"name": "b", "pid": 2, "messagingSocketPath": "/tmp/cc-socks/2.sock"}]

    def test_a_uds_address_resolves_to_the_session_behind_it(self):
        with mock.patch.object(sessions, "live_sessions", return_value=self.SESSIONS):
            self.assertEqual(sessions.find_session("uds:/tmp/cc-socks/2.sock")["name"], "b")
            self.assertEqual(sessions.find_session("a")["pid"], 1)
            with self.assertRaises(SystemExit):
                sessions.find_session("uds:/tmp/cc-socks/9.sock")


if __name__ == "__main__":
    unittest.main()

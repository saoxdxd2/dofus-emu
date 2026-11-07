import pathlib
import socket
import subprocess
import sys
import unittest
from types import SimpleNamespace

from kaggle_tunnel import network_veto


class TunnelTests(unittest.TestCase):
    def test_only_tcp_proxy_endpoint_allowed(self):
        hook = network_veto(40000)
        tcp = SimpleNamespace(family=socket.AF_INET, type=socket.SOCK_STREAM)
        hook("socket.connect", (tcp, ("127.0.0.1", 40000)))
        for dest in (("192.0.2.1", 443), ("127.0.0.1", 443), ("localhost", 40000)):
            with self.subTest(dest=dest), self.assertRaises(PermissionError):
                hook("socket.connect", (tcp, dest))
        with self.assertRaises(PermissionError):
            hook("socket.connect", (SimpleNamespace(family=socket.AF_INET, type=socket.SOCK_DGRAM), ("127.0.0.1", 40000)))

    def test_dns_and_subprocesses_refused(self):
        hook = network_veto(40000)
        hook("socket.getaddrinfo", ("127.0.0.1", 40000))
        for event, args in (("socket.getaddrinfo", ("example.invalid", 443)),
                            ("socket.gethostbyname", ("example.invalid",)),
                            ("socket.sendto", ()), ("subprocess.Popen", ())):
            with self.subTest(event=event), self.assertRaises(PermissionError):
                hook(event, args)

    def test_real_audit_hook_blocks_before_connect_or_dns(self):
        source = '''
import sys,socket
from kaggle_tunnel import protect
protect(40000)
for operation in (lambda: socket.create_connection(("192.0.2.1",443)),
                  lambda: socket.getaddrinfo("example.invalid",443),
                  lambda: socket.socket().connect(("127.0.0.1",443))):
    try: operation()
    except PermissionError: pass
    else: raise AssertionError("network veto did not fire")
print("blocked")
'''
        result = subprocess.run([sys.executable, "-c", source],
                                cwd=pathlib.Path(__file__).parent,
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "blocked")


if __name__ == "__main__":
    unittest.main()

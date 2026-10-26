"""Run existing tests with synthetic credentials, isolated data and no external sockets."""
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DATA = Path(tempfile.mkdtemp(prefix="https-unit-"))
os.environ.update({
    "OPENAI_API_KEY": "test-no-provider-calls",
    "AMO_BASE_URL": "https://example.test",
    "AMO_SUBDOMAIN": "example",
    "DATABASE_URL": "sqlite:///" + str(DATA / "contacts.db"),
    "DATA_DIR": str(DATA),
})
original_lookup = socket.getaddrinfo
blocked = []


def local_lookup(host, *args, **kwargs):
    if host not in ("127.0.0.1", "::1", "localhost", None):
        blocked.append(str(host))
        raise OSError("External network disabled in unit tests")
    return original_lookup(host, *args, **kwargs)


socket.getaddrinfo = local_lookup
original_connect = socket.socket.connect
original_connect_ex = socket.socket.connect_ex


def checked_address(sock, address):
    if sock.family in (socket.AF_INET, socket.AF_INET6):
        host = address[0]
        if host not in ("127.0.0.1", "::1", "localhost"):
            blocked.append(str(host))
            raise OSError("External network disabled in unit tests")


def local_connect(sock, address):
    checked_address(sock, address)
    return original_connect(sock, address)


def local_connect_ex(sock, address):
    checked_address(sock, address)
    return original_connect_ex(sock, address)


socket.socket.connect = local_connect
socket.socket.connect_ex = local_connect_ex

if len(sys.argv) > 1 and sys.argv[1] == "pytest":
    import pytest
    code = pytest.main(["-q", "tests"])
else:
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
    code = 0 if unittest.TextTestRunner(verbosity=1).run(suite).wasSuccessful() else 1
print("OFFLINE_EXTERNAL_SOCKET_ATTEMPTS=" + str(len(blocked)))
raise SystemExit(code)

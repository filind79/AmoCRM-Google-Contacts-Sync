"""Check the HTTPS trust used by this service, without app imports or credentials."""
import argparse
import hashlib
import http.client
import json
import ssl
from urllib.parse import urlsplit

import certifi
import httpx

URLS = (
    ("https://render.com/", False),
    ("https://dv-g3b-emudhra.we-have-a-problem.com/", True),
    ("https://dv-g3b-aaa-emudhra.we-have-a-problem.com/", True),
)


def check(live=False):
    from pathlib import Path
    import importlib.metadata

    bundle = Path(certifi.where())
    digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
    result = {
        "certifi": importlib.metadata.version("certifi"),
        "bundle_sha256": digest,
        "httpx": importlib.metadata.version("httpx"),
        "checks": [],
    }
    assert result["certifi"] == "2026.10.19"
    assert digest == "50f79f98dac192b20dbf73f35c56f0b0c557ab267ff5199d1117cc1180b70785"
    if live:
        with httpx.Client(trust_env=False, follow_redirects=False, timeout=15) as client:
            for url, bad in URLS:
                item = {"client": "httpx", "url": url, "expected_rejection": bad}
                try:
                    with client.stream("GET", url) as response:
                        item.update(tls_verified=True, http_status=response.status_code)
                except httpx.ConnectError as exc:
                    item.update(
                        tls_verified=False,
                        certificate_rejected="CERTIFICATE_VERIFY_FAILED" in str(exc),
                        error_class=type(exc).__name__,
                    )
                result["checks"].append(item)
        for url, bad in URLS:
            item = {"client": "system_ssl", "url": url, "expected_rejection": bad}
            connection = http.client.HTTPSConnection(
                urlsplit(url).hostname, timeout=15, context=ssl.create_default_context()
            )
            try:
                connection.connect()
                item["tls_verified"] = True
            except ssl.SSLCertVerificationError as exc:
                item.update(
                    tls_verified=False,
                    certificate_rejected=True,
                    error_class=type(exc).__name__,
                )
            finally:
                connection.close()
            result["checks"].append(item)
        assert all(
            item.get("certificate_rejected") if item["expected_rejection"]
            else item.get("tls_verified")
            for item in result["checks"]
        ), result
    print("FLEET_TLS_READ=" + json.dumps(result, separators=(",", ":")))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    check(parser.parse_args().live)

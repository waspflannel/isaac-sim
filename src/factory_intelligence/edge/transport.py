"""Bounded JSON HTTP requests with verified TLS and no credential-bearing redirects."""

import json
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_json(url, token=None, *, method="GET", body=None):
    address = urlsplit(url)
    if address.scheme not in ("http", "https") or address.username or address.password:
        raise ValueError("Use an HTTP(S) endpoint without credentials in the URL")
    if address.scheme == "http" and address.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("Remote connections require HTTPS")
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body, allow_nan=False).encode()
    req = Request(url, data=data, headers=headers, method=method)
    with build_opener(NoRedirect()).open(req, timeout=5) as response:
        raw = response.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError("Response exceeded 8 MiB")
    return json.loads(raw)

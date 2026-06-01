"""
Server-Side Request Forgery (SSRF) vulnerable test application.
This file contains intentionally vulnerable code for testing taint analysis.
"""

import sys
import requests
from flask import Flask, request as flask_request

app = Flask(__name__)


@app.route("/fetch")
def fetch():
    """VULNERABLE: user-controlled URL passed directly to requests.get."""
    url = flask_request.args.get("url")
    return requests.get(url).text


@app.route("/proxy")
def proxy():
    """VULNERABLE: user-controlled URL in requests.post."""
    target = flask_request.args.get("target")
    payload = flask_request.args.get("data", "")
    response = requests.post(target, data=payload)
    return response.text


def fetch_from_argv():
    """VULNERABLE: SSRF from command-line argument."""
    if len(sys.argv) > 1:
        url = sys.argv[1]
        return requests.get(url).text
    return ""


def build_url(base, path):
    """Intermediate: combines user-controlled parts."""
    return base + "/" + path


@app.route("/indirect")
def indirect_ssrf():
    """VULNERABLE: SSRF via URL constructed from user input."""
    base = flask_request.args.get("base", "http://internal")
    path = flask_request.args.get("path", "")
    url = build_url(base, path)
    return requests.get(url).text


if __name__ == "__main__":
    app.run()

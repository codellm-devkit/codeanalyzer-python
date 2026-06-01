"""
Server-Side Template Injection (SSTI) vulnerable test application.
This file contains intentionally vulnerable code for testing taint analysis.
"""

import sys
from flask import Flask, request, render_template_string

app = Flask(__name__)


@app.route("/greet")
def greet():
    """VULNERABLE: user input interpolated directly into a Jinja2 template."""
    name = request.args.get("name", "World")
    template = "<h1>Hello, " + name + "!</h1>"
    return render_template_string(template)


@app.route("/profile")
def profile():
    """VULNERABLE: f-string template construction from query param."""
    username = request.args.get("user", "anonymous")
    tmpl = f"<p>Welcome {username}</p>"
    return render_template_string(tmpl)


def render_from_argv():
    """VULNERABLE: template built from command-line argument."""
    payload = sys.argv[1] if len(sys.argv) > 1 else "safe"
    return render_template_string("<div>" + payload + "</div>")


if __name__ == "__main__":
    app.run()

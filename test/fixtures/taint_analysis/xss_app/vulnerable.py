"""
Cross-Site Scripting (XSS) vulnerable test application.
This file contains intentionally vulnerable code for testing taint analysis.
"""

import sys
from html import escape


def vulnerable_html_output(user_input):
    """XSS via direct HTML output."""
    # VULNERABLE: User input directly in HTML
    html = "<div>" + user_input + "</div>"
    return html


def vulnerable_html_fstring(username):
    """XSS via f-string in HTML."""
    # VULNERABLE: f-string with user input
    html = f"<h1>Welcome, {username}!</h1>"
    return html


def vulnerable_html_format(comment):
    """XSS via string format in HTML."""
    # VULNERABLE: String formatting
    html = "<p>Comment: {}</p>".format(comment)
    return html


def vulnerable_from_argv():
    """XSS from command-line arguments."""
    if len(sys.argv) > 1:
        message = sys.argv[1]
        # VULNERABLE: Command-line arg in HTML
        html = f"<div class='message'>{message}</div>"
        return html


def vulnerable_from_input():
    """XSS from user input."""
    name = input("Enter your name: ")
    # VULNERABLE: User input in HTML
    html = "<span>Hello, " + name + "</span>"
    return html


def vulnerable_javascript_injection(callback):
    """XSS via JavaScript injection."""
    # VULNERABLE: User input in JavaScript
    script = f"<script>callback({callback});</script>"
    return script


def safe_with_escape(user_input):
    """Safe HTML output with escaping."""
    # SAFE: HTML escaping
    html = "<div>" + escape(user_input) + "</div>"
    return html


def safe_with_template(user_input):
    """Safe HTML output using template with auto-escaping."""
    # SAFE: Template with auto-escaping (simulated)
    escaped_input = escape(user_input)
    html = f"<div>{escaped_input}</div>"
    return html


# Inter-procedural taint flow examples
def get_user_comment():
    """Source: Get user comment."""
    return input("Enter your comment: ")


def format_html_comment(comment):
    """Intermediate: Format comment as HTML."""
    return f"<div class='comment'>{comment}</div>"


def render_html(html):
    """Sink: Render HTML (simulated)."""
    print(html)
    return html


def vulnerable_interprocedural():
    """Vulnerable code with taint flow across functions."""
    # Source -> Intermediate -> Sink
    comment = get_user_comment()
    html = format_html_comment(comment)
    render_html(html)


class HTMLRenderer:
    """Class with vulnerable methods demonstrating inter-method taint flow."""
    
    def get_username_from_args(self):
        """Source: Get username from command-line."""
        return sys.argv[1] if len(sys.argv) > 1 else "Guest"
    
    def create_greeting(self, username):
        """Intermediate: Create greeting HTML with tainted data."""
        return f"<h1>Hello, {username}!</h1>"
    
    def output_html(self, html):
        """Sink: Output HTML."""
        print(html)
        return html
    
    def vulnerable_greeting(self):
        """Vulnerable method with taint flow across class methods."""
        # Source -> Intermediate -> Sink within class
        username = self.get_username_from_args()
        greeting = self.create_greeting(username)
        return self.output_html(greeting)


def capitalize_text(text):
    """Intermediate function that processes text."""
    # Capitalization doesn't prevent XSS
    return text.upper()


def vulnerable_with_processing():
    """Vulnerable code with text processing."""
    # Source
    user_text = input("Enter text: ")
    # Processing (still tainted)
    processed = capitalize_text(user_text)
    # Sink
    html = f"<p>{processed}</p>"
    print(html)
    return html


def get_message_from_file():
    """Source: Get message from file."""
    try:
        with open(sys.argv[1] if len(sys.argv) > 1 else "message.txt", 'r') as f:
            return f.read()
    except:
        return "<script>alert('default')</script>"


def vulnerable_from_file():
    """Vulnerable code with message from file."""
    # Source
    message = get_message_from_file()
    # Sink
    html = f"<div>{message}</div>"
    return html


class BlogPost:
    """Class demonstrating complex taint flow."""
    
    def __init__(self):
        self.title = ""
        self.content = ""
    
    def set_title_from_input(self):
        """Source: Set title from user input."""
        self.title = input("Enter post title: ")
    
    def set_content_from_input(self):
        """Source: Set content from user input."""
        self.content = input("Enter post content: ")
    
    def render_title(self):
        """Sink: Render title as HTML."""
        return f"<h2>{self.title}</h2>"
    
    def render_content(self):
        """Sink: Render content as HTML."""
        return f"<div class='content'>{self.content}</div>"
    
    def render_full_post(self):
        """Vulnerable method with multiple taint flows."""
        self.set_title_from_input()
        self.set_content_from_input()
        title_html = self.render_title()
        content_html = self.render_content()
        return title_html + content_html


def main():
    """Main function demonstrating vulnerabilities."""
    # Direct vulnerabilities
    vulnerable_html_output(sys.argv[1] if len(sys.argv) > 1 else "<script>alert('XSS')</script>")
    vulnerable_html_fstring(input("Enter username: "))
    vulnerable_html_format(input("Enter comment: "))
    vulnerable_javascript_injection(input("Enter callback: "))
    
    # Inter-procedural vulnerabilities
    vulnerable_interprocedural()
    
    # Class-based vulnerabilities
    renderer = HTMLRenderer()
    renderer.vulnerable_greeting()
    
    # Vulnerability with processing
    vulnerable_with_processing()
    
    # Vulnerability from file
    vulnerable_from_file()
    
    # Complex class-based vulnerability
    post = BlogPost()
    post.render_full_post()
    
    # Safe examples
    safe_with_escape("<script>alert('XSS')</script>")


if __name__ == "__main__":
    main()

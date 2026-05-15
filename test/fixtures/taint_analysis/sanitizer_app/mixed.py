"""
Test application with both vulnerable and safe code.
This demonstrates the difference between sanitized and unsanitized flows.
"""

import sqlite3
import sys
from html import escape


# Vulnerable: No sanitizer
def vulnerable_no_sanitizer():
    """Vulnerable code without sanitizer."""
    user_input = input("Enter username: ")
    conn = sqlite3.connect('test.db')
    cursor = conn.cursor()
    # VULNERABLE: No sanitization
    query = f"SELECT * FROM users WHERE username = '{user_input}'"
    cursor.execute(query)
    return cursor.fetchall()


# Safe: With sanitizer
def safe_with_sanitizer():
    """Safe code with sanitizer."""
    user_input = input("Enter username: ")
    conn = sqlite3.connect('test.db')
    cursor = conn.cursor()
    # SAFE: Parameterized query (sanitizer)
    query = "SELECT * FROM users WHERE username = ?"
    cursor.execute(query, (user_input,))
    return cursor.fetchall()


# Vulnerable: Weak sanitization
def weak_sanitize(user_input):
    """Weak sanitizer that doesn't fully protect."""
    # This only removes single quotes, but doesn't prevent all SQL injection
    return user_input.replace("'", "")


def vulnerable_weak_sanitizer():
    """Vulnerable code with weak sanitization."""
    user_input = input("Enter user ID: ")
    # Weak sanitization
    sanitized = weak_sanitize(user_input)
    conn = sqlite3.connect('test.db')
    cursor = conn.cursor()
    # STILL VULNERABLE: Weak sanitization doesn't prevent numeric injection
    query = f"SELECT * FROM users WHERE id = {sanitized}"
    cursor.execute(query)
    return cursor.fetchall()


# Safe: Strong sanitization
def strong_sanitize_html(content):
    """Strong HTML sanitizer."""
    return escape(content)


def safe_strong_sanitizer():
    """Safe code with strong sanitization."""
    user_content = input("Enter content: ")
    # Strong sanitization
    safe_content = strong_sanitize_html(user_content)
    # SAFE: Content is properly escaped
    html = f"<div>{safe_content}</div>"
    return html


# Vulnerable: Sanitizer bypassed
def bypass_sanitizer():
    """Vulnerable code where sanitizer is bypassed."""
    user_input = input("Enter username: ")
    
    # Sanitizer exists but is not used
    def unused_sanitizer(text):
        return escape(text)
    
    # VULNERABLE: Sanitizer defined but not called
    html = f"<h1>Welcome, {user_input}!</h1>"
    return html


# Safe: Sanitizer properly applied
def proper_sanitizer_usage():
    """Safe code with properly applied sanitizer."""
    user_input = input("Enter username: ")
    
    # Sanitizer is defined
    def html_sanitizer(text):
        return escape(text)
    
    # SAFE: Sanitizer is actually used
    safe_input = html_sanitizer(user_input)
    html = f"<h1>Welcome, {safe_input}!</h1>"
    return html


def main():
    """Main function demonstrating vulnerable vs safe code."""
    # Vulnerable examples
    vulnerable_no_sanitizer()
    vulnerable_weak_sanitizer()
    bypass_sanitizer()
    
    # Safe examples
    safe_with_sanitizer()
    safe_strong_sanitizer()
    proper_sanitizer_usage()


if __name__ == "__main__":
    main()

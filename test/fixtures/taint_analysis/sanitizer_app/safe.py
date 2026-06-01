"""
Test application demonstrating sanitizers blocking taint flows.
This file shows how proper sanitization prevents vulnerabilities.
"""

import sqlite3
import subprocess
import os
import sys
from html import escape
import shlex


# SQL Injection with Sanitizers
def get_user_id_from_input():
    """Source: Get user ID from input."""
    return input("Enter user ID: ")


def sanitize_for_sql_parameterized(user_id):
    """Sanitizer: Use parameterized query (proper sanitization)."""
    conn = sqlite3.connect('test.db')
    cursor = conn.cursor()
    # SAFE: Parameterized query acts as sanitizer
    query = "SELECT * FROM users WHERE id = ?"
    cursor.execute(query, (user_id,))
    return cursor.fetchall()


def safe_sql_with_sanitizer():
    """Safe SQL query with proper sanitization."""
    # Source -> Sanitizer -> Sink (should NOT be flagged)
    user_id = get_user_id_from_input()
    result = sanitize_for_sql_parameterized(user_id)
    return result


# Command Injection with Sanitizers
def get_filename_from_input():
    """Source: Get filename from input."""
    return input("Enter filename: ")


def sanitize_for_shell(filename):
    """Sanitizer: Quote shell argument."""
    return shlex.quote(filename)


def execute_with_sanitized_input(safe_filename):
    """Sink: Execute command with sanitized input."""
    # SAFE: Input has been sanitized
    subprocess.run(f"cat {safe_filename}", shell=True)


def safe_command_with_sanitizer():
    """Safe command execution with proper sanitization."""
    # Source -> Sanitizer -> Sink (should NOT be flagged)
    filename = get_filename_from_input()
    safe_filename = sanitize_for_shell(filename)
    execute_with_sanitized_input(safe_filename)


# Path Traversal with Sanitizers
def get_filepath_from_input():
    """Source: Get filepath from input."""
    return input("Enter file path: ")


def sanitize_path(filepath):
    """Sanitizer: Normalize and validate path."""
    base_dir = "/var/www/uploads"
    full_path = os.path.normpath(os.path.join(base_dir, filepath))
    
    # Ensure the path is within base_dir
    if not full_path.startswith(base_dir):
        raise ValueError("Invalid file path")
    
    return full_path


def read_file_safe(safe_path):
    """Sink: Read file with sanitized path."""
    # SAFE: Path has been sanitized
    with open(safe_path, 'r') as f:
        return f.read()


def safe_file_read_with_sanitizer():
    """Safe file read with proper sanitization."""
    # Source -> Sanitizer -> Sink (should NOT be flagged)
    filepath = get_filepath_from_input()
    safe_path = sanitize_path(filepath)
    content = read_file_safe(safe_path)
    return content


# XSS with Sanitizers
def get_html_content_from_input():
    """Source: Get HTML content from input."""
    return input("Enter HTML content: ")


def sanitize_html(content):
    """Sanitizer: Escape HTML entities."""
    return escape(content)


def render_html_safe(safe_content):
    """Sink: Render HTML with sanitized content."""
    # SAFE: Content has been sanitized
    html = f"<div>{safe_content}</div>"
    print(html)
    return html


def safe_html_render_with_sanitizer():
    """Safe HTML rendering with proper sanitization."""
    # Source -> Sanitizer -> Sink (should NOT be flagged)
    content = get_html_content_from_input()
    safe_content = sanitize_html(content)
    html = render_html_safe(safe_content)
    return html


# Basename sanitizer for path traversal
def sanitize_with_basename(filepath):
    """Sanitizer: Use only the basename."""
    return os.path.basename(filepath)


def safe_file_with_basename():
    """Safe file access using basename sanitizer."""
    # Source -> Sanitizer -> Sink (should NOT be flagged)
    filepath = input("Enter filename: ")
    safe_filename = sanitize_with_basename(filepath)
    with open(f"/var/www/uploads/{safe_filename}", 'r') as f:
        return f.read()


# Class-based sanitization
class SecureDatabase:
    """Database class with proper sanitization."""
    
    def __init__(self):
        self.conn = sqlite3.connect('test.db')
        self.cursor = self.conn.cursor()
    
    def get_username_from_args(self):
        """Source: Get username from command-line."""
        return sys.argv[1] if len(sys.argv) > 1 else "admin"
    
    def execute_safe_query(self, username):
        """Sanitizer + Sink: Execute parameterized query."""
        # SAFE: Parameterized query
        query = "SELECT * FROM users WHERE username = ?"
        self.cursor.execute(query, (username,))
        return self.cursor.fetchall()
    
    def safe_lookup(self):
        """Safe method with sanitization."""
        # Source -> Sanitizer/Sink (should NOT be flagged)
        username = self.get_username_from_args()
        return self.execute_safe_query(username)


# Multiple sanitizers in sequence
def double_sanitize_path(filepath):
    """Apply multiple sanitizers."""
    # First sanitizer: basename
    safe_name = os.path.basename(filepath)
    # Second sanitizer: normpath
    safe_path = os.path.normpath(safe_name)
    return safe_path


def safe_with_multiple_sanitizers():
    """Safe code with multiple sanitizers."""
    # Source -> Sanitizer1 -> Sanitizer2 -> Sink (should NOT be flagged)
    filepath = input("Enter path: ")
    safe_path = double_sanitize_path(filepath)
    with open(f"/var/www/uploads/{safe_path}", 'r') as f:
        return f.read()


def main():
    """Main function demonstrating safe code with sanitizers."""
    # All of these should be safe due to sanitizers
    safe_sql_with_sanitizer()
    safe_command_with_sanitizer()
    safe_file_read_with_sanitizer()
    safe_html_render_with_sanitizer()
    safe_file_with_basename()
    safe_with_multiple_sanitizers()
    
    # Class-based safe code
    db = SecureDatabase()
    db.safe_lookup()


if __name__ == "__main__":
    main()

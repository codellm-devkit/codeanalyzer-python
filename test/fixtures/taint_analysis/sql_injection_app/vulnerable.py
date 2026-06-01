"""
SQL Injection vulnerable test application.
This file contains intentionally vulnerable code for testing taint analysis.
"""

import sqlite3
import sys


def vulnerable_query_direct(user_input):
    """Direct SQL injection vulnerability - user input directly in query."""
    conn = sqlite3.connect('test.db')
    cursor = conn.cursor()
    # VULNERABLE: Direct string concatenation
    query = "SELECT * FROM users WHERE username = '" + user_input + "'"
    cursor.execute(query)
    return cursor.fetchall()


def vulnerable_query_format(user_input):
    """SQL injection via string formatting."""
    conn = sqlite3.connect('test.db')
    cursor = conn.cursor()
    # VULNERABLE: String formatting
    query = "SELECT * FROM users WHERE id = {}".format(user_input)
    cursor.execute(query)
    return cursor.fetchall()


def vulnerable_query_fstring(username):
    """SQL injection via f-string."""
    conn = sqlite3.connect('test.db')
    cursor = conn.cursor()
    # VULNERABLE: f-string interpolation
    query = f"SELECT * FROM users WHERE username = '{username}'"
    cursor.execute(query)
    return cursor.fetchall()


def vulnerable_from_argv():
    """SQL injection from command-line arguments."""
    if len(sys.argv) > 1:
        user_id = sys.argv[1]
        conn = sqlite3.connect('test.db')
        cursor = conn.cursor()
        # VULNERABLE: Command-line arg directly in query
        query = "DELETE FROM users WHERE id = " + user_id
        cursor.execute(query)
        conn.commit()


def safe_query_parameterized(user_input):
    """Safe query using parameterized statements."""
    conn = sqlite3.connect('test.db')
    cursor = conn.cursor()
    # SAFE: Parameterized query
    query = "SELECT * FROM users WHERE username = ?"
    cursor.execute(query, (user_input,))
    return cursor.fetchall()


# Inter-procedural taint flow examples
def get_user_input():
    """Source: Get user input."""
    return input("Enter username: ")


def build_query(username):
    """Intermediate function that propagates taint."""
    return "SELECT * FROM users WHERE username = '" + username + "'"


def execute_query(query):
    """Sink: Execute SQL query."""
    conn = sqlite3.connect('test.db')
    cursor = conn.cursor()
    cursor.execute(query)
    return cursor.fetchall()


def vulnerable_interprocedural():
    """Vulnerable code with taint flow across functions."""
    # Source -> Intermediate -> Sink
    user_input = get_user_input()
    query = build_query(user_input)
    results = execute_query(query)
    return results


class UserDatabase:
    """Class with vulnerable methods demonstrating inter-method taint flow."""
    
    def __init__(self):
        self.conn = sqlite3.connect('test.db')
        self.cursor = self.conn.cursor()
    
    def get_username_from_args(self):
        """Source: Get username from command-line."""
        return sys.argv[1] if len(sys.argv) > 1 else "admin"
    
    def format_query(self, username):
        """Intermediate: Format query with tainted data."""
        return f"SELECT * FROM users WHERE username = '{username}'"
    
    def run_query(self, query):
        """Sink: Execute query."""
        self.cursor.execute(query)
        return self.cursor.fetchall()
    
    def vulnerable_lookup(self):
        """Vulnerable method with taint flow across class methods."""
        # Source -> Intermediate -> Sink within class
        username = self.get_username_from_args()
        query = self.format_query(username)
        return self.run_query(query)


def process_user_data(data):
    """Intermediate function that returns tainted data."""
    return data.strip().upper()


def vulnerable_with_processing():
    """Vulnerable code with data processing in between."""
    # Source
    raw_input = input("Enter user ID: ")
    # Processing (still tainted)
    processed = process_user_data(raw_input)
    # Sink
    conn = sqlite3.connect('test.db')
    cursor = conn.cursor()
    query = "SELECT * FROM users WHERE id = " + processed
    cursor.execute(query)
    return cursor.fetchall()


def main():
    """Main function demonstrating vulnerabilities."""
    # Direct vulnerabilities
    vulnerable_query_direct(sys.argv[1] if len(sys.argv) > 1 else "admin")
    vulnerable_query_format(input("Enter user ID: "))
    vulnerable_query_fstring(input("Enter username: "))
    
    # Inter-procedural vulnerabilities
    vulnerable_interprocedural()
    
    # Class-based vulnerabilities
    db = UserDatabase()
    db.vulnerable_lookup()
    
    # Vulnerability with processing
    vulnerable_with_processing()
    
    # Safe example
    safe_query_parameterized(input("Enter safe username: "))


if __name__ == "__main__":
    main()

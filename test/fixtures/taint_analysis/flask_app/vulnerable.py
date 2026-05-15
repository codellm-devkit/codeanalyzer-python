"""
Flask web application with taint vulnerabilities.
This file contains intentionally vulnerable code for testing taint analysis.
"""

try:
    from flask import Flask, request, render_template_string
    import sqlite3
    import os
    
    app = Flask(__name__)
    
    
    @app.route('/search')
    def vulnerable_search():
        """SQL injection in search endpoint."""
        query = request.args.get('q', '')
        conn = sqlite3.connect('app.db')
        cursor = conn.cursor()
        # VULNERABLE: User input from request.args in SQL query
        sql = f"SELECT * FROM products WHERE name LIKE '%{query}%'"
        cursor.execute(sql)
        results = cursor.fetchall()
        return str(results)
    
    
    @app.route('/user/<username>')
    def vulnerable_user_profile(username):
        """XSS in user profile."""
        # VULNERABLE: User input from URL parameter in HTML
        html = f"<h1>Profile: {username}</h1>"
        return html
    
    
    @app.route('/execute')
    def vulnerable_execute():
        """Command injection in execute endpoint."""
        cmd = request.args.get('cmd', '')
        # VULNERABLE: User input from request.args in shell command
        result = os.popen(cmd).read()
        return result
    
    
    @app.route('/file')
    def vulnerable_file_read():
        """Path traversal in file read."""
        filename = request.args.get('name', '')
        # VULNERABLE: User input from request.args in file path
        with open(f"/var/www/files/{filename}", 'r') as f:
            return f.read()
    
    
    @app.route('/template')
    def vulnerable_template():
        """Server-Side Template Injection."""
        template = request.args.get('tmpl', '')
        # VULNERABLE: User input in template rendering
        return render_template_string(template)
    
    
    @app.route('/login', methods=['POST'])
    def vulnerable_login():
        """SQL injection in login form."""
        username = request.form.get('username', '')
        password = request.form.get('password', '')
        conn = sqlite3.connect('app.db')
        cursor = conn.cursor()
        # VULNERABLE: User input from request.form in SQL query
        sql = f"SELECT * FROM users WHERE username='{username}' AND password='{password}'"
        cursor.execute(sql)
        user = cursor.fetchone()
        return "Login successful" if user else "Login failed"
    
    
    # Inter-procedural taint flow examples
    def get_search_query():
        """Source: Get search query from request."""
        return request.args.get('q', '')
    
    
    def build_search_sql(query):
        """Intermediate: Build SQL query."""
        return f"SELECT * FROM products WHERE name LIKE '%{query}%'"
    
    
    def execute_sql(sql):
        """Sink: Execute SQL query."""
        conn = sqlite3.connect('app.db')
        cursor = conn.cursor()
        cursor.execute(sql)
        return cursor.fetchall()
    
    
    @app.route('/search_v2')
    def vulnerable_search_interprocedural():
        """SQL injection with inter-procedural taint flow."""
        # Source -> Intermediate -> Sink
        query = get_search_query()
        sql = build_search_sql(query)
        results = execute_sql(sql)
        return str(results)
    
    
    class UserService:
        """Service class with vulnerable methods."""
        
        def get_user_id_from_request(self):
            """Source: Get user ID from request."""
            return request.args.get('id', '')
        
        def format_user_query(self, user_id):
            """Intermediate: Format user query."""
            return f"SELECT * FROM users WHERE id = {user_id}"
        
        def fetch_user(self, query):
            """Sink: Execute user query."""
            conn = sqlite3.connect('app.db')
            cursor = conn.cursor()
            cursor.execute(query)
            return cursor.fetchone()
        
        def get_user_info(self):
            """Vulnerable method with taint flow across class methods."""
            user_id = self.get_user_id_from_request()
            query = self.format_user_query(user_id)
            return self.fetch_user(query)
    
    
    user_service = UserService()
    
    
    @app.route('/user_info')
    def vulnerable_user_info():
        """SQL injection via service class."""
        user = user_service.get_user_info()
        return str(user)
    
    
    @app.route('/safe_search')
    def safe_search():
        """Safe search with parameterized query."""
        query = request.args.get('q', '')
        conn = sqlite3.connect('app.db')
        cursor = conn.cursor()
        # SAFE: Parameterized query
        sql = "SELECT * FROM products WHERE name LIKE ?"
        cursor.execute(sql, (f'%{query}%',))
        results = cursor.fetchall()
        return str(results)
    
    
    if __name__ == '__main__':
        app.run(debug=True)

except ImportError:
    # Flask not installed, create dummy functions for analysis
    import sqlite3
    import os
    import sys
    
    class Request:
        """Mock request object."""
        def __init__(self):
            self.args = {'q': '', 'id': '', 'cmd': '', 'name': '', 'tmpl': ''}
            self.form = {'username': '', 'password': ''}
        
        def get(self, key, default=''):
            return self.args.get(key, default)
    
    request = Request()
    
    
    def vulnerable_search():
        """SQL injection in search endpoint."""
        query = request.args.get('q', '')
        conn = sqlite3.connect('app.db')
        cursor = conn.cursor()
        sql = f"SELECT * FROM products WHERE name LIKE '%{query}%'"
        cursor.execute(sql)
        return cursor.fetchall()
    
    
    def get_search_query():
        """Source: Get search query from request."""
        return request.args.get('q', '')
    
    
    def build_search_sql(query):
        """Intermediate: Build SQL query."""
        return f"SELECT * FROM products WHERE name LIKE '%{query}%'"
    
    
    def execute_sql(sql):
        """Sink: Execute SQL query."""
        conn = sqlite3.connect('app.db')
        cursor = conn.cursor()
        cursor.execute(sql)
        return cursor.fetchall()
    
    
    def vulnerable_search_interprocedural():
        """SQL injection with inter-procedural taint flow."""
        query = get_search_query()
        sql = build_search_sql(query)
        results = execute_sql(sql)
        return results
    
    
    class UserService:
        """Service class with vulnerable methods."""
        
        def get_user_id_from_request(self):
            """Source: Get user ID from request."""
            return request.args.get('id', '')
        
        def format_user_query(self, user_id):
            """Intermediate: Format user query."""
            return f"SELECT * FROM users WHERE id = {user_id}"
        
        def fetch_user(self, query):
            """Sink: Execute user query."""
            conn = sqlite3.connect('app.db')
            cursor = conn.cursor()
            cursor.execute(query)
            return cursor.fetchone()
        
        def get_user_info(self):
            """Vulnerable method with taint flow across class methods."""
            user_id = self.get_user_id_from_request()
            query = self.format_user_query(user_id)
            return self.fetch_user(query)

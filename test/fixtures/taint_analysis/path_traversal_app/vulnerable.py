"""
Path Traversal vulnerable test application.
This file contains intentionally vulnerable code for testing taint analysis.
"""

import os
import sys


def vulnerable_open_direct(filename):
    """Path traversal via direct file open."""
    # VULNERABLE: User input directly in file path
    with open("/var/www/uploads/" + filename, 'r') as f:
        return f.read()


def vulnerable_open_fstring(filename):
    """Path traversal via f-string."""
    # VULNERABLE: f-string with user input
    with open(f"/var/www/uploads/{filename}", 'r') as f:
        return f.read()


def vulnerable_from_argv():
    """Path traversal from command-line arguments."""
    if len(sys.argv) > 1:
        filepath = sys.argv[1]
        # VULNERABLE: Command-line arg in file path
        with open(filepath, 'r') as f:
            print(f.read())


def vulnerable_from_input():
    """Path traversal from user input."""
    filename = input("Enter filename to read: ")
    # VULNERABLE: User input in file path
    with open("/var/www/data/" + filename, 'r') as f:
        return f.read()


def vulnerable_os_path_join(user_path):
    """Path traversal via os.path.join."""
    # VULNERABLE: os.path.join doesn't prevent traversal
    full_path = os.path.join("/var/www/uploads", user_path)
    with open(full_path, 'r') as f:
        return f.read()


def vulnerable_write_file(filename, content):
    """Path traversal in file write."""
    # VULNERABLE: User input in write path
    with open("/var/www/uploads/" + filename, 'w') as f:
        f.write(content)


def safe_with_normalization(filename):
    """Safe file access with path normalization."""
    # SAFE: Path normalization and validation
    base_dir = "/var/www/uploads"
    full_path = os.path.normpath(os.path.join(base_dir, filename))
    
    # Ensure the path is within base_dir
    if not full_path.startswith(base_dir):
        raise ValueError("Invalid file path")
    
    with open(full_path, 'r') as f:
        return f.read()


def safe_with_basename(filename):
    """Safe file access using basename."""
    # SAFE: Only use basename, preventing directory traversal
    safe_filename = os.path.basename(filename)
    with open(f"/var/www/uploads/{safe_filename}", 'r') as f:
        return f.read()


# Inter-procedural taint flow examples
def get_filename_from_user():
    """Source: Get filename from user."""
    return input("Enter filename: ")


def construct_file_path(filename):
    """Intermediate: Construct file path."""
    return "/var/www/uploads/" + filename


def read_file_content(filepath):
    """Sink: Read file content."""
    with open(filepath, 'r') as f:
        return f.read()


def vulnerable_interprocedural():
    """Vulnerable code with taint flow across functions."""
    # Source -> Intermediate -> Sink
    filename = get_filename_from_user()
    filepath = construct_file_path(filename)
    content = read_file_content(filepath)
    return content


class FileManager:
    """Class with vulnerable methods demonstrating inter-method taint flow."""
    
    def __init__(self, base_dir="/var/www/data"):
        self.base_dir = base_dir
    
    def get_filename_from_args(self):
        """Source: Get filename from command-line."""
        return sys.argv[1] if len(sys.argv) > 1 else "default.txt"
    
    def build_path(self, filename):
        """Intermediate: Build file path with tainted data."""
        return self.base_dir + "/" + filename
    
    def read_file(self, filepath):
        """Sink: Read file."""
        with open(filepath, 'r') as f:
            return f.read()
    
    def vulnerable_read(self):
        """Vulnerable method with taint flow across class methods."""
        # Source -> Intermediate -> Sink within class
        filename = self.get_filename_from_args()
        filepath = self.build_path(filename)
        return self.read_file(filepath)


def process_filename(filename):
    """Intermediate function that processes filename."""
    # Remove leading/trailing whitespace but doesn't prevent traversal
    return filename.strip()


def vulnerable_with_processing():
    """Vulnerable code with filename processing."""
    # Source
    raw_filename = input("Enter filename: ")
    # Processing (still tainted)
    processed = process_filename(raw_filename)
    # Sink
    with open("/var/www/uploads/" + processed, 'r') as f:
        return f.read()


def get_path_from_config():
    """Source: Get path from configuration file."""
    # Simulating reading from a config file
    return sys.argv[1] if len(sys.argv) > 1 else "../../../etc/passwd"


def vulnerable_from_config():
    """Vulnerable code with path from config."""
    # Source
    filepath = get_path_from_config()
    # Sink
    with open(filepath, 'r') as f:
        return f.read()


def main():
    """Main function demonstrating vulnerabilities."""
    # Direct vulnerabilities
    vulnerable_open_direct(sys.argv[1] if len(sys.argv) > 1 else "../../etc/passwd")
    vulnerable_open_fstring(input("Enter filename: "))
    vulnerable_os_path_join(input("Enter path: "))
    
    # Inter-procedural vulnerabilities
    vulnerable_interprocedural()
    
    # Class-based vulnerabilities
    fm = FileManager()
    fm.vulnerable_read()
    
    # Vulnerability with processing
    vulnerable_with_processing()
    
    # Vulnerability from config
    vulnerable_from_config()
    
    # Safe examples
    safe_with_normalization("safe_file.txt")
    safe_with_basename("../../../etc/passwd")  # Will only use "passwd"


if __name__ == "__main__":
    main()

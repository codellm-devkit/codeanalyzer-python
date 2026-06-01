"""
Command Injection vulnerable test application.
This file contains intentionally vulnerable code for testing taint analysis.
"""

import os
import subprocess
import sys


def vulnerable_os_system(filename):
    """Command injection via os.system."""
    # VULNERABLE: User input directly in shell command
    os.system("cat " + filename)


def vulnerable_subprocess_shell(user_input):
    """Command injection via subprocess with shell=True."""
    # VULNERABLE: shell=True with user input
    subprocess.call("ls -la " + user_input, shell=True)


def vulnerable_popen(command):
    """Command injection via os.popen."""
    # VULNERABLE: User input in os.popen
    result = os.popen("echo " + command).read()
    return result


def vulnerable_from_argv():
    """Command injection from command-line arguments."""
    if len(sys.argv) > 1:
        directory = sys.argv[1]
        # VULNERABLE: Command-line arg in shell command
        os.system(f"ls -la {directory}")


def vulnerable_from_input():
    """Command injection from user input."""
    filename = input("Enter filename to display: ")
    # VULNERABLE: User input in shell command
    subprocess.run(f"cat {filename}", shell=True)


def vulnerable_eval(user_code):
    """Code injection via eval."""
    # VULNERABLE: eval with user input
    result = eval(user_code)
    return result


def vulnerable_exec(user_code):
    """Code injection via exec."""
    # VULNERABLE: exec with user input
    exec(user_code)


def safe_subprocess_no_shell(filename):
    """Safe subprocess call without shell."""
    # SAFE: No shell, arguments as list
    subprocess.run(["cat", filename])


def safe_subprocess_with_sanitization(filename):
    """Safe subprocess with input validation."""
    # SAFE: Input validation
    import shlex
    safe_filename = shlex.quote(filename)
    subprocess.run(f"cat {safe_filename}", shell=True)


# Inter-procedural taint flow examples
def get_command_from_user():
    """Source: Get command from user."""
    return input("Enter command: ")


def build_shell_command(cmd):
    """Intermediate: Build shell command."""
    return "ls -la " + cmd


def execute_shell_command(command):
    """Sink: Execute shell command."""
    os.system(command)


def vulnerable_interprocedural():
    """Vulnerable code with taint flow across functions."""
    # Source -> Intermediate -> Sink
    user_cmd = get_command_from_user()
    full_cmd = build_shell_command(user_cmd)
    execute_shell_command(full_cmd)


class CommandExecutor:
    """Class with vulnerable methods demonstrating inter-method taint flow."""
    
    def get_directory_from_args(self):
        """Source: Get directory from command-line."""
        return sys.argv[1] if len(sys.argv) > 1 else "/tmp"
    
    def prepare_command(self, directory):
        """Intermediate: Prepare command with tainted data."""
        return f"find {directory} -name '*.txt'"
    
    def run_command(self, command):
        """Sink: Execute command."""
        return subprocess.check_output(command, shell=True)
    
    def vulnerable_find_files(self):
        """Vulnerable method with taint flow across class methods."""
        # Source -> Intermediate -> Sink within class
        directory = self.get_directory_from_args()
        command = self.prepare_command(directory)
        return self.run_command(command)


def sanitize_input(user_input):
    """Intermediate function that doesn't properly sanitize."""
    # This doesn't actually sanitize for command injection
    return user_input.replace(";", "").replace("&", "")


def vulnerable_with_weak_sanitization():
    """Vulnerable code with weak sanitization."""
    # Source
    user_input = input("Enter filename: ")
    # Weak sanitization (still tainted)
    sanitized = sanitize_input(user_input)
    # Sink
    os.system("cat " + sanitized)


def get_code_from_file(filename):
    """Source: Read code from file."""
    with open(filename, 'r') as f:
        return f.read()


def vulnerable_eval_from_file():
    """Vulnerable eval with code from file."""
    # Source
    code = get_code_from_file(sys.argv[1] if len(sys.argv) > 1 else "input.txt")
    # Sink
    eval(code)


def main():
    """Main function demonstrating vulnerabilities."""
    # Direct vulnerabilities
    vulnerable_os_system(sys.argv[1] if len(sys.argv) > 1 else "/etc/passwd")
    vulnerable_subprocess_shell(input("Enter directory: "))
    vulnerable_popen(input("Enter command: "))
    vulnerable_eval(input("Enter expression: "))
    
    # Inter-procedural vulnerabilities
    vulnerable_interprocedural()
    
    # Class-based vulnerabilities
    executor = CommandExecutor()
    executor.vulnerable_find_files()
    
    # Vulnerability with weak sanitization
    vulnerable_with_weak_sanitization()
    
    # Safe examples
    safe_subprocess_no_shell("/etc/passwd")


if __name__ == "__main__":
    main()

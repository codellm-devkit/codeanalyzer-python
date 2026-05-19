"""
Unsafe Deserialization vulnerable test application.
This file contains intentionally vulnerable code for testing taint analysis.
"""

import pickle
import sys


def load_from_input():
    """VULNERABLE: pickle.loads on user-supplied bytes from stdin."""
    raw = input("Enter serialized data (hex): ")
    return pickle.loads(bytes.fromhex(raw))


def load_from_argv():
    """VULNERABLE: pickle.loads on command-line argument."""
    if len(sys.argv) > 1:
        return pickle.loads(sys.argv[1].encode("latin-1"))
    return None


def process_and_load(data):
    """Intermediate function — taint propagates through."""
    stripped = data.strip()
    return pickle.loads(stripped.encode("latin-1"))


def vulnerable_from_input_processed():
    """VULNERABLE: taint flow through intermediate function."""
    raw = input("Payload: ")
    return process_and_load(raw)


class DataLoader:
    def read_payload(self):
        """Source: reads from argv."""
        return sys.argv[1] if len(sys.argv) > 1 else b""

    def deserialize(self, payload):
        """Sink: unsafe pickle.loads."""
        return pickle.loads(payload)

    def run(self):
        """VULNERABLE: inter-method taint flow."""
        payload = self.read_payload()
        return self.deserialize(payload)


if __name__ == "__main__":
    loader = DataLoader()
    loader.run()

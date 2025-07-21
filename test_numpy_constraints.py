#!/usr/bin/env python3
"""Test script to verify NumPy dependency constraints work correctly."""

import sys
from packaging.version import parse as parse_version

def test_numpy_constraints():
    """Test that NumPy constraints are correct for different Python versions."""
    python_version = parse_version(f"{sys.version_info.major}.{sys.version_info.minor}")
    print(f"Testing on Python {python_version}")
    
    try:
        import numpy
        numpy_version = parse_version(numpy.__version__)
        print(f"NumPy version: {numpy_version}")
        
        # Test constraints based on Python version
        if python_version < parse_version("3.11"):
            if not (parse_version("1.21.0") <= numpy_version < parse_version("1.24.0")):
                print(f"ERROR: NumPy {numpy_version} not in expected range 1.21.0-1.24.0 for Python < 3.11")
                return False
        elif python_version >= parse_version("3.11") and python_version < parse_version("3.12"):
            if not (parse_version("1.24.0") <= numpy_version < parse_version("2.0.0")):
                print(f"ERROR: NumPy {numpy_version} not in expected range 1.24.0-2.0.0 for Python 3.11.x")
                return False
        elif python_version >= parse_version("3.12"):
            if not (parse_version("1.26.0") <= numpy_version < parse_version("2.0.0")):
                print(f"ERROR: NumPy {numpy_version} not in expected range 1.26.0-2.0.0 for Python 3.12+")
                return False
                
        print("✅ NumPy constraints are satisfied")
        return True
        
    except ImportError as e:
        print(f"ERROR: Failed to import NumPy: {e}")
        return False

if __name__ == "__main__":
    success = test_numpy_constraints()
    sys.exit(0 if success else 1)

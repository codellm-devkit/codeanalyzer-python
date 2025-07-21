"""
Test file for nested structures: functions, classes, and complex nesting patterns.
This file tests the symbol table builder's ability to correctly identify and catalog
all nested definitions as reported in issue #15.
"""

# Module-level imports
import os
from typing import List, Dict, Optional


# Module-level variable
MODULE_CONSTANT = "test_value"


def outer_function():
    """An outer function containing nested structures."""
    
    # Local variable in outer function
    outer_var = "outer"
    
    def nested_function():
        """A function nested inside another function."""
        nested_var = "nested"
        
        def deeply_nested_function():
            """A deeply nested function (3 levels deep)."""
            return f"deeply nested: {nested_var}, {outer_var}"
        
        return deeply_nested_function()
    
    class NestedClass:
        """A class defined inside a function."""
        
        def __init__(self, name: str):
            self.name = name
        
        def nested_class_method(self):
            """A method inside a nested class."""
            
            def method_nested_function():
                """A function nested inside a class method."""
                return f"method nested: {self.name}"
            
            class MethodNestedClass:
                """A class nested inside a method."""
                
                def __init__(self, value: int):
                    self.value = value
                
                def method_in_nested_class(self):
                    """Method in a class that's nested in a method."""
                    
                    def function_in_method_in_nested_class():
                        """Function inside method inside nested class."""
                        return f"deep nesting: {self.value}"
                    
                    return function_in_method_in_nested_class()
            
            return method_nested_function(), MethodNestedClass(42)
        
        @staticmethod
        def static_method_in_nested_class():
            """Static method in nested class."""
            return "static in nested"
        
        @classmethod
        def class_method_in_nested_class(cls):
            """Class method in nested class."""
            return f"class method in {cls.__name__}"
    
    # Create an instance and call methods
    nested_instance = NestedClass("test")
    return nested_function(), nested_instance
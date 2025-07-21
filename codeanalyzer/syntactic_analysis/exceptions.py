class SymbolTableBuilderException(Exception):
    """Base exception for symbol table builder errors."""
    pass

class SymbolTableBuilderFileNotFoundError(SymbolTableBuilderException):
    """Exception raised when a source file is not found."""
    pass

class SymbolTableBuilderParsingError(SymbolTableBuilderException):
    """Exception raised when a source file cannot be parsed."""
    pass

class SymbolTableBuilderRayError(SymbolTableBuilderException):
    """Exception raised when there is an error in Ray processing."""
    pass
from enum import Enum


class OutputFormat(str, Enum):
    """String-based enum for output formats to support typer case-insensitive options."""

    JSON = "json"
    MSGPACK = "msgpack"

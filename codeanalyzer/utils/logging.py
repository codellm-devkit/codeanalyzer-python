import logging

from rich.console import Console
from rich.logging import RichHandler

# Set up base logger with RichHandler
console = Console()
handler = RichHandler(console=console, show_time=True, show_level=True, show_path=False)

logger = logging.getLogger("codeanalyzer")
logger.setLevel(logging.ERROR)  # Default level
logger.addHandler(handler)
logger.propagate = False  # Prevent double logs


def _set_log_level(verbosity: int) -> None:
    levels = [logging.ERROR, logging.WARNING, logging.INFO, logging.DEBUG]
    level = levels[min(verbosity, len(levels) - 1)]
    logger.setLevel(level)

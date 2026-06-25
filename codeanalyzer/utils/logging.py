import logging

from rich.console import Console
from rich.logging import RichHandler

# Logs go to stderr so stdout stays clean for piped output (e.g. --emit json | jq).
# The same console instance is shared with ProgressBar so Rich can coordinate
# live-display updates with log messages without the two consoles stomping on each
# other (which causes progress bars to appear twice when a warning interrupts them).
console = Console(stderr=True)
handler = RichHandler(console=console, show_time=True, show_level=True, show_path=False)

logger = logging.getLogger("codeanalyzer")
logger.setLevel(logging.ERROR)  # Default level
logger.addHandler(handler)
logger.propagate = False  # Prevent double logs


def _set_log_level(verbosity: int) -> None:
    levels = [logging.ERROR, logging.WARNING, logging.INFO, logging.DEBUG]
    level = levels[min(verbosity, len(levels) - 1)]
    logger.setLevel(level)

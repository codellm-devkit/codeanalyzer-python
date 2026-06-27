import logging
from typing import Optional

from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)


class ProgressBar:
    def __init__(
        self,
        total_files: int,
        description: str = "Processing files...",
        item_label: str = "files",
    ) -> None:
        self.total_files = total_files
        self.description = description
        self.item_label = item_label

        _logger = logging.getLogger("codeanalyzer")
        current_level = _logger.getEffectiveLevel()

        # Disable progress if logger level is higher than INFO (e.g., WARNING or ERROR)
        self.disabled = current_level >= logging.ERROR

        self._progress: Optional[Progress] = None
        self._task_id: Optional[TaskID] = None

    def __enter__(self):
        if not self.disabled:
            # Import the shared console from the logging module so that Rich can
            # coordinate log messages and live progress rendering on the same
            # console — prevents the bar from appearing twice when a warning is
            # printed while the progress is active.
            from codeanalyzer.utils.logging import console as _shared_console

            self._progress = Progress(
                SpinnerColumn(spinner_name="dots"),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                TextColumn(f"[blue]{{task.completed}}/{{task.total}} {self.item_label}"),
                TimeElapsedColumn(),
                TimeRemainingColumn(),
                transient=False,
                console=_shared_console,
            )
            self._progress.start()
            self._task_id = self._progress.add_task(
                description=self.description, total=self.total_files
            )
        return self

    def update_description(self, message: str) -> None:
        if not self.disabled and self._progress and self._task_id is not None:
            self._progress.update(self._task_id, description=message)

    def advance(self, n: int = 1) -> None:
        if not self.disabled and self._progress and self._task_id is not None:
            self._progress.advance(self._task_id, n)

    def finish(self, message: Optional[str] = None) -> None:
        if not self.disabled and self._progress and self._task_id is not None:
            if not self._progress.finished:
                self._progress.update(self._task_id, completed=self.total_files)
            self._progress.stop()

        if message:
            logging.getLogger("codeanalyzer").info(message)

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.finish()

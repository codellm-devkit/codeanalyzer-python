class CodeQLExceptions:
    class CodeQLDatabaseBuildException(Exception):
        """Exception raised when there is an error building the CodeQL database."""

        def __init__(self, message: str) -> None:
            super().__init__(message)

    class CodeQLQueryExecutionException(Exception):
        """Exception raised when there is an error building the CodeQL database."""

        def __init__(self, message: str) -> None:
            super().__init__(message)

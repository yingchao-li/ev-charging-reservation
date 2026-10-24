class AppError(Exception):
    def __init__(self, status_code: int, error: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error = error
        self.message = message


def not_found(message: str) -> AppError:
    return AppError(404, "NOT_FOUND", message)


def forbidden(message: str = "You do not have permission to perform this action") -> AppError:
    return AppError(403, "FORBIDDEN", message)


def conflict(code: str, message: str) -> AppError:
    return AppError(409, code, message)


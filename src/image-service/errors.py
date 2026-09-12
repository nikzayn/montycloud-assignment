class ApiError(Exception):
    status_code = 500
    code = "INTERNAL_ERROR"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class BadRequest(ApiError):
    status_code = 400
    code = "BAD_REQUEST"


class ValidationError(BadRequest):
    code = "VALIDATION_ERROR"


class Unauthorized(ApiError):
    status_code = 401
    code = "UNAUTHORIZED"


class Forbidden(ApiError):
    status_code = 403
    code = "FORBIDDEN"


class NotFound(ApiError):
    status_code = 404
    code = "NOT_FOUND"


class Conflict(ApiError):
    status_code = 409
    code = "CONFLICT"
import logging

from django.db import DatabaseError, OperationalError
from rest_framework.exceptions import APIException, NotFound
from rest_framework.response import Response

logger = logging.getLogger(__name__)


class DomainError(Exception):
    def __init__(self, code, message, status=400):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)


def exception_handler(exc, context):
    if isinstance(exc, DomainError):
        code, message, status = exc.code, exc.message, exc.status
    elif isinstance(exc, OperationalError):
        logger.exception("Database unavailable", exc_info=exc)
        code, message, status = (
            "storage_unavailable",
            "Storage temporarily unavailable; retry the original key.",
            503,
        )
    elif isinstance(exc, DatabaseError):
        logger.exception("Database integrity error", exc_info=exc)
        code, message, status = (
            "integrity_violation",
            "A storage invariant could not be satisfied.",
            500,
        )
    elif isinstance(exc, APIException):
        status = exc.status_code
        code = "not_found" if isinstance(exc, NotFound) else "invalid_request"
        message = str(exc.detail)
    else:
        logger.exception("Unhandled request error", exc_info=exc)
        code, message, status = "internal_error", "Unexpected error; retry the original key.", 500
    return Response({"error": {"code": code, "message": message}}, status=status)

import functools
import random
import time

from django.db import OperationalError, connections, transaction


def transactional(using="default"):
    """Retry only aborted transactions; external I/O stays outside this boundary."""

    def decorate(function):
        @functools.wraps(function)
        def wrapped(*args, **kwargs):
            for attempt in range(3):
                try:
                    with transaction.atomic(using=using):
                        with connections[using].cursor() as cursor:
                            cursor.execute("SET LOCAL lock_timeout = '3s'")
                        return function(*args, **kwargs)
                except OperationalError as exc:
                    cause = exc.__cause__
                    if getattr(cause, "sqlstate", None) not in {"40P01", "40001"} or attempt == 2:
                        raise
                    time.sleep(random.uniform(0.01, 0.05) * (attempt + 1))

        return wrapped

    return decorate

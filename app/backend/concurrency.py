"""Bounded local document work. Pages and identifiers have separate limits."""
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import threading

PAGE_WORKERS = 5
SCAN_SLOTS = threading.BoundedSemaphore(PAGE_WORKERS)
IDENTIFIER_SLOTS = threading.BoundedSemaphore(PAGE_WORKERS)


def ordered_parallel(function, items, workers=PAGE_WORKERS):
    """Keep at most `workers` tasks submitted and return them in source order."""
    iterator = iter(items)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending = deque()
        try:
            for _ in range(workers):
                item = next(iterator, None)
                if item is None:
                    break
                pending.append(pool.submit(function, item))
            while pending:
                result = pending.popleft().result()
                item = next(iterator, None)
                if item is not None:
                    pending.append(pool.submit(function, item))
                yield result
        finally:
            for future in pending:
                future.cancel()

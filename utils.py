"""Shared utilities: retry wrapper, logging setup, atomic file writes."""

import json
import logging
import os
import tempfile
import time
from pathlib import Path


def api_request_with_retry(fn, max_retries=3, backoff=2, timeout_errors=True):
    """Execute an API call function with exponential backoff on failure.

    Args:
        fn: Callable that makes the API request and returns a response.
        max_retries: Maximum number of retry attempts.
        backoff: Base delay in seconds (doubles each retry).
        timeout_errors: If True, also retry on timeout errors.

    Returns:
        The return value of fn().

    Raises:
        The last exception if all retries are exhausted.
    """
    import requests

    last_error = None
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except requests.exceptions.Timeout as e:
            last_error = e
            if not timeout_errors:
                raise
        except requests.exceptions.ConnectionError as e:
            last_error = e
        except requests.exceptions.HTTPError as e:
            # Don't retry client errors (4xx) except 429 (rate limit)
            if e.response is not None and 400 <= e.response.status_code < 500 and e.response.status_code != 429:
                raise
            last_error = e

        if attempt < max_retries:
            wait = backoff * (2 ** attempt)
            logger = logging.getLogger(__name__)
            logger.warning("Retry %d/%d after %.1fs: %s", attempt + 1, max_retries, wait, last_error)
            time.sleep(wait)

    raise last_error


def atomic_json_write(path: Path, data, **json_kwargs):
    """Write JSON data to a file atomically using a temp file + rename.

    This prevents corrupted files if the process is interrupted mid-write.
    Default json_kwargs: indent=2, ensure_ascii=False.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    kwargs = {"indent": 2, "ensure_ascii": False}
    kwargs.update(json_kwargs)

    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, **kwargs)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def setup_logging(level=logging.INFO):
    """Configure logging with timestamp and level for the project."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
    )

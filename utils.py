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


def call_claude(prompt: str, model: str = "haiku", max_tokens: int = 150) -> str:
    """Call Claude via CLI (Max plan) or SDK, controlled by USE_CLAUDE_CLI env var."""
    if os.environ.get("USE_CLAUDE_CLI", "").lower() in ("1", "true", "yes"):
        return _call_claude_cli(prompt, model)
    import anthropic
    model_ids = {
        "haiku": "claude-haiku-4-5-20251001",
        "sonnet": "claude-sonnet-4-20250514",
    }
    client = anthropic.Anthropic()
    resp = client.messages.create(
        model=model_ids.get(model, model),
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text.strip()


def _call_claude_cli(prompt: str, model: str = "haiku") -> str:
    import subprocess
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("CLAUDE") and k != "ANTHROPIC_API_KEY"}
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write(prompt)
        prompt_file = f.name
    try:
        with open(prompt_file) as pf:
            result = subprocess.run(
                ["claude", "-p", "--model", model, "--no-session-persistence"],
                capture_output=True, text=True, timeout=120, env=env, stdin=pf,
            )
        if result.returncode != 0:
            raise RuntimeError(f"claude CLI failed: {result.stderr.strip()[:200]}")
        return result.stdout.strip()
    finally:
        os.unlink(prompt_file)

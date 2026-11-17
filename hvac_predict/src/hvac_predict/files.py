"""Write the tool's own files safely."""

import fcntl
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .errors import ConfigError


def write_atomically(path: Path, content: str) -> None:
    """Replace ``path`` with ``content`` in a single step.

    The content goes to a private temporary file first. A reader never sees
    half a file, and a file someone else planted under a predictable name is
    never written through.
    """
    try:
        handle, scratch = tempfile.mkstemp(dir=path.parent, prefix=f"{path.name}.")
        try:
            with os.fdopen(handle, "w") as file:
                file.write(content)
            os.replace(scratch, path)
        except BaseException:
            Path(scratch).unlink(missing_ok=True)
            raise
    except OSError as error:
        raise ConfigError(f"cannot write {path}: {error.strerror or error}") from error


@contextmanager
def held_by_one_run(path: Path) -> Iterator[None]:
    """Keep ``path`` to one run at a time, and fail at once if another run has it.

    A run reads the file, talks to two servers and writes it back. A second
    run in between would have its record overwritten by the first.
    """
    lock = path.with_name(f"{path.name}.lock")
    try:
        handle = lock.open("w")
    except OSError as error:
        raise ConfigError(f"cannot write {lock}: {error.strerror or error}") from error
    with handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ConfigError(
                f"another run of hvac-predict is using {path}; try again when it has finished"
            ) from error
        yield

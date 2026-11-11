"""Copy one big file with a SHA-256 of what was copied (spec 6.8, SAVE-6).

The file is written to <name>.part and renamed only when every byte is there, so a file that exists
is always complete. If reading or writing fails (network drive dropped), the copy waits and goes on
from where it stopped, a few times, before giving up. Nothing half-written is left behind.
"""
from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

CHUNK = 8 * 1024 * 1024


class CopyError(Exception):
    pass


class CopyCancelled(Exception):
    pass


@dataclass(frozen=True)
class CopyResult:
    bytes: int
    sha256: str
    seconds: float
    attempts: int


def _remove(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def copy_with_hash(src, dst, *, progress: Optional[Callable[[int, int], None]] = None,
                   cancel: Optional[Callable[[], bool]] = None, retries: int = 3, wait_seconds: float = 10.0,
                   chunk_size: int = CHUNK, sleep: Callable[[float], None] = time.sleep,
                   open_source: Optional[Callable] = None) -> CopyResult:
    """Copy src to dst. progress(bytes done, bytes total); cancel() -> True stops the copy.
    retries: how many times to try again after a read/write error, wait_seconds apart."""
    src, dst = Path(src), Path(dst)
    part = dst.with_name(dst.name + ".part")
    open_source = open_source or (lambda p: open(p, "rb"))
    try:
        size = src.stat().st_size
    except OSError as e:
        raise CopyError(f"Cannot read {src}: {e}") from None
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        _remove(part)
        open(part, "wb").close()
    except OSError as e:
        raise CopyError(f"Cannot write to {dst.parent}: {e}") from None

    digest, done, attempts, t0 = hashlib.sha256(), 0, 0, time.time()
    while True:
        attempts += 1
        try:
            if src.stat().st_size != size:
                raise CopyError(f"{src.name} changed while it was being copied.")
            with open_source(src) as fin, open(part, "r+b") as fout:
                fin.seek(done)
                fout.seek(done)
                fout.truncate()                       # drop a block that was only half written
                while True:
                    if cancel and cancel():
                        raise CopyCancelled()
                    block = fin.read(chunk_size)
                    if not block:
                        break
                    fout.write(block)
                    digest.update(block)
                    done += len(block)
                    if progress:
                        progress(done, size)
                fout.flush()
                os.fsync(fout.fileno())
            break
        except (CopyCancelled, CopyError):
            _remove(part)
            raise
        except OSError as e:
            if attempts > retries:
                _remove(part)
                raise CopyError(f"Copying {src.name} failed after {attempts} tries: {e}") from None
            sleep(wait_seconds)
    if done != size:
        _remove(part)
        raise CopyError(f"{src.name}: copied {done:,} of {size:,} bytes.")
    try:
        os.replace(part, dst)
        if dst.stat().st_size != size:
            raise OSError("size differs after the copy")
    except OSError as e:
        _remove(part)
        raise CopyError(f"Cannot finish writing {dst.name}: {e}") from None
    return CopyResult(size, digest.hexdigest(), time.time() - t0, attempts)

"""Build fail-closed bubblewrap commands for controlled operations."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys
from typing import Iterable, Sequence


_MASKED_DATA_ROOT = Path("/opt/data")
_FORBIDDEN_BIND_TARGETS = (Path("/"), Path("/opt"), _MASKED_DATA_ROOT)


def _validated_path(
    raw_path: os.PathLike[str] | str,
    *,
    directory: bool | None = None,
) -> str:
    path = Path(os.path.abspath(os.fspath(raw_path)))
    if path in _FORBIDDEN_BIND_TARGETS:
        raise ValueError(f"Sandbox path is too broad: {path}")

    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        if current.is_symlink():
            raise ValueError(f"Sandbox path cannot contain a symbolic link: {path}")

    if not path.exists():
        raise ValueError(f"Sandbox path does not exist: {path}")
    if directory is True and not path.is_dir():
        raise ValueError(f"Writable path must be a directory: {path}")
    return str(path)


def _unique_paths(paths: Iterable[os.PathLike[str] | str], *, directory: bool | None) -> list[str]:
    return sorted({_validated_path(path, directory=directory) for path in paths})


def sandbox_command(
    argv: Sequence[str],
    writable_dirs: Iterable[os.PathLike[str] | str],
    *,
    enabled: bool = True,
    readable_paths: Iterable[os.PathLike[str] | str] | None = None,
) -> list[str]:
    """Return *argv* wrapped in a read-only bubblewrap mount namespace."""

    command = list(argv)
    if not command or any(
        not isinstance(argument, str) or "\0" in argument for argument in command
    ):
        raise ValueError("argv must contain valid command arguments")
    if not enabled:
        return command
    if sys.platform != "linux":
        raise RuntimeError("bubblewrap sandboxing requires Linux")

    bwrap = shutil.which("bwrap")
    if bwrap is None:
        raise RuntimeError("bubblewrap sandboxing requires bwrap")

    writable = _unique_paths(writable_dirs, directory=True)
    readable = _unique_paths(readable_paths or (), directory=None)

    wrapped = [
        bwrap,
        "--die-with-parent",
        "--unshare-user",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
        "--ro-bind",
        "/",
        "/",
        "--tmpfs",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/run",
        "--tmpfs",
        "/tmp",
        "--tmpfs",
        str(_MASKED_DATA_ROOT),
    ]
    for path in readable:
        if Path(path).is_relative_to(_MASKED_DATA_ROOT):
            wrapped.extend(["--ro-bind", path, path])
    for path in writable:
        wrapped.extend(["--bind", path, path])
    return [*wrapped, "--", *command]

"""Stop a launched run without leaving its simulator behind.

``run_headless`` is a Python wrapper around the Blackfin simulator, so it has a
child.  On Windows both ``terminate()`` and ``kill()`` are ``TerminateProcess``,
which runs no cleanup in the target: the wrapper dies and the simulator it
started keeps running, unparented and invisible to the next run.  Fourteen of
them accumulated over one session here, each holding a core, which quietly
depresses every measurement taken afterwards.

``taskkill /T`` walks the tree instead.  Check with::

    Get-CimInstance Win32_Process -Filter "Name='cdj-run.exe'"
"""
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 LycheeAPPF

from __future__ import annotations

import subprocess
import sys


def stop_tree(process: subprocess.Popen | None, timeout: float = 20) -> None:
    """Terminate a process and everything it started."""
    if process is None or process.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       check=False)
    else:
        process.terminate()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()

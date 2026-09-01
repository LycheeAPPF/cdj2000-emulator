"""Project tooling package."""

# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 LycheeAPPF

import sys

# Several tools document CDJ screen content, and the browse categories are
# spelled with U+FFFA/U+FFFB and CJK brackets because that is what the
# firmware draws.  A Windows console defaults to cp1252 and dies printing
# them, so `--help` crashes before it has shown a single line.  Nothing here
# depends on the replacement characters; being readable beats being exact.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass          # not a real stream, or already closed

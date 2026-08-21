"""Fake constrained-model-pass command that fails to run at all (nonzero
exit) -- exercises `ModelInvocationError`, distinct from a response that
ran fine but was malformed."""

from __future__ import annotations

import sys


def main() -> None:
    sys.stdin.read()
    sys.stderr.write("simulated model-pass command failure\n")
    sys.exit(1)


if __name__ == "__main__":
    main()

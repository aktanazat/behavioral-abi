"""Fake constrained-model-pass command that ignores its prompt and writes
text that is not valid JSON at all -- exercises `MalformedModelResponseError`
on a JSON-syntax failure."""

from __future__ import annotations

import sys


def main() -> None:
    sys.stdin.read()
    sys.stdout.write("this is not json { at all")


if __name__ == "__main__":
    main()

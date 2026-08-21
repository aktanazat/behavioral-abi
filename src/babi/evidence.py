"""Content-addressed evidence store.

Layout: `<root>/sha256/<hex[:2]>/<hex>`. Writes are atomic (temp file in the
same directory, then rename) and write-once: writing an already-present
digest is a no-op, since the content -- and therefore the digest -- can
only be identical.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

from babi import canon

DEFAULT_ROOT = Path("evidence")


class EvidenceStore:
    def __init__(self, root: Path | str = DEFAULT_ROOT) -> None:
        self.root = Path(root)

    def path(self, digest: str) -> Path:
        algo, sep, hex_digest = digest.partition(":")
        if not sep or algo != "sha256" or len(hex_digest) != 64:
            raise ValueError(f"not a sha256 digest: {digest!r}")
        return self.root / "sha256" / hex_digest[:2] / hex_digest

    def put_bytes(self, data: bytes) -> str:
        digest = f"sha256:{hashlib.sha256(data).hexdigest()}"
        target = self.path(digest)
        if target.exists():
            return digest

        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as tmp_file:
                tmp_file.write(data)
            os.replace(tmp_name, target)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise
        return digest

    def put_json(self, obj: canon.Json) -> str:
        return self.put_bytes(canon.canonical_json(obj))

    def get(self, digest: str) -> bytes:
        return self.path(digest).read_bytes()

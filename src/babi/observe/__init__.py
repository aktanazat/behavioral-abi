"""Generic observers: black-box read-back/count-digest, plus local
environment observers -- filesystem, process, container (`docker diff`),
stderr/exit, and network-egress-log. None branch on a tool or operation
name -- only on observer kind and caller-supplied configuration (paths, a
pid, a container id, an event list, or a planner-inferred dependency edge).

Several submodules define same-named functions (`observe`, and `snapshot`/
`diff_snapshots` for filesystem vs. process, and `summarize` for network vs.
count_digest); those are re-exported here under a `<name>_<module>` alias so
every public function stays reachable from `babi.observe` without shadowing
another module's.
"""

from __future__ import annotations

from babi.observe.container import observe as observe_container
from babi.observe.container import parse_diff, run_diff
from babi.observe.count_digest import CountDigest
from babi.observe.count_digest import observe as observe_count_digest
from babi.observe.count_digest import summarize as summarize_count_digest
from babi.observe.filesystem import diff_snapshots as diff_snapshots_filesystem
from babi.observe.filesystem import observe as observe_filesystem
from babi.observe.filesystem import snapshot as snapshot_filesystem
from babi.observe.network import diff_destinations, summarize
from babi.observe.network import observe as observe_network
from babi.observe.process import diff_snapshots as diff_snapshots_process
from babi.observe.process import observe as observe_process
from babi.observe.process import snapshot as snapshot_process
from babi.observe.read_back import ReadBackOutcome, read_back
from babi.observe.stderr_exit import observe as observe_stderr_exit

__all__ = [
    "CountDigest",
    "ReadBackOutcome",
    "diff_destinations",
    "diff_snapshots_filesystem",
    "diff_snapshots_process",
    "observe_container",
    "observe_count_digest",
    "observe_filesystem",
    "observe_network",
    "observe_process",
    "observe_stderr_exit",
    "parse_diff",
    "read_back",
    "run_diff",
    "snapshot_filesystem",
    "snapshot_process",
    "summarize",
    "summarize_count_digest",
]

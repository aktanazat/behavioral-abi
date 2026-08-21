# MCP server corpus

Research date: 2026-08-20.

The frozen corpus contains 20 MCP servers. Each server met these inclusion rules when the corpus was assembled:

- It was publicly installable or reachable.
- It was maintained or first-party.
- At least one hard metric supported popularity, such as package downloads, GitHub stars, client bundling, or vendor usage.
- It could be probed without production data or irreversible real-world action.
- Its exact package, image, or endpoint was recorded.

PulseMCP estimates did not determine rank by themselves. The observed Sentry estimate differed from npm by approximately 3,900 times, so each entry records other evidence when available.

Open-source servers were eligible for local instrumented probes. Remote stateful probes required a disposable test account and a read-back oracle. A static source review did not count as empirical verification.

[`corpus/top20.json`](../../corpus/top20.json) is the canonical list. It records rank, distribution, popularity evidence, transport, authentication, annotation coverage, sandboxability, and the inclusion decision for each server.

# Behavioral ABI implementation plan

Research date: 2026-08-20

Research corpus: `research/` (301 files, six primary-source summaries, 168 KB of synthesis)

## Decision

Build a measurement lab first. Do not build a registry platform, dashboard, certification program, or gateway in the first 14 days.

The first product is one reproducible command:

```text
babi probe <mcp-server-or-openapi> --sandbox <manifest> --policy safe-v0
```

It must produce:

1. A content-addressed evidence bundle.
2. A machine-readable behavioral contract.
3. A minimal reproducer for each discrepancy.
4. A signed in-toto attestation logged in Rekor.
5. A policy decision that a runtime hook can consume before a tool call.

The registry is initially an index over these immutable artifacts. It earns the right to become a service only if the prober passes the no-per-tool-logic kill gate.

## Why this wedge is real

The MCP specification has required clients to treat tool annotations as untrusted since the fields shipped in the 2025-03-26 revision. The four fields remain byte-for-byte unchanged through the 2026-07-28 revision and the current draft. The current specification also says `serverInfo` is self-reported and must not support security decisions.

Primary sources:

- MCP tools specification: https://modelcontextprotocol.io/specification/2026-07-28/server/tools
- MCP discovery and self-reported identity: https://modelcontextprotocol.io/specification/2026-07-28/server/discover
- Official annotations post: https://blog.modelcontextprotocol.io/posts/2026-03-16-tool-annotations/
- Official registry scope: https://modelcontextprotocol.io/registry/about

The official MCP post states both limits directly:

> An untrusted server can lie. A server can claim `readOnlyHint: true` and delete your files anyway.

> They aren't enforcement ... keep your actual safety guarantees in deterministic controls.

The protocol now has a cross-vendor Tool Annotations Interest Group and several unmerged proposals. This validates demand. It also creates spec-absorption risk. Behavioral ABI must verify runtime facts that a richer self-declared schema still cannot prove.

The closest competitor is Glama. It executes open-source MCP servers in Firecracker, observes syscalls and network activity, rescans on commits, and records schema drift. It does not publish signed semantic contracts for idempotency, reversibility, hidden preconditions, or consistency timing. This difference must be demonstrated, not asserted. Glama could add these checks.

Primary source: https://glama.ai/mcp/methodology

## The 14-day question

The test is not whether a polished registry can be built. The test is:

> Can one generic planner and deterministic executor discover consequential, schema-invisible behavior across unrelated tools without operation-specific code?

### Pass conditions

All must pass:

1. Probe three unrelated sandbox targets: Stripe test mode, GitHub Issues in a disposable repository, and Cloudflare's free tier.
2. Enumerate and run the same planner against 20 publicly runnable popular MCP servers. Publish exact inclusion rules and popularity evidence.
3. Discover at least five consequential schema-invisible behaviors. At least four reproduce in three independent clean runs.
4. At least three reproduced findings come from stateful black-box execution, not source review or static annotation inspection.
5. No operation-specific generator, assertion, inverse mapping, fixture, or cleanup routine is added.
6. One Claude Code `PreToolUse` hook consumes a verified contract and blocks or rewrites one unsafe retry before execution.
7. Every public finding links to the raw probe history, environment digest, tool-definition digest, reproducer, contract, signature, and Rekor inclusion proof.

### Kill conditions

Stop the company thesis, not just the implementation, if any applies:

1. More than 10% of successful probe plans need a human-written operation-specific value, dependency edge, observer, or inverse.
2. The generic planner cannot make a valid first call for at least 70% of sandbox-eligible tool schemas after two model/planner iterations.
3. Fewer than five consequential hidden behaviors appear after all three sandboxes and the 20-server corpus run.
4. Fewer than four findings reproduce three times from clean state.
5. Most findings are only conservative metadata errors, telemetry disclosures, schema bugs, or source-versus-wire differences rather than consequential runtime behavior.
6. Glama or another existing service already exposes equivalent stateful semantic results and history through a public API.
7. Runtime policy cannot make a concrete decision from the resulting contract without provider-specific logic.

A failed kill test can still produce a useful open-source MCP annotation auditor. Do not relabel that result as the registry thesis.

## Scope rule: no per-tool logic

The rule needs a precise boundary. Otherwise target-specific setup will hide inside configuration.

### Allowed

- Protocol adapters for MCP stdio, MCP Streamable HTTP, and OpenAPI/HTTP.
- Generic environment adapters for a local process sandbox, disposable remote account, webhook collector, and event-log collector.
- Credentials, account identifiers, region, resource prefix, hard spend limit, and teardown deadline in a sandbox manifest.
- JSON Schema constraints, examples already published by the target, and values returned by earlier calls.
- Model inference from tool name, description, input schema, output schema, and prior observed responses.
- Generic risk policy based on generated resource scope, spend limit, external communication, and presence of a read-back oracle.

### Forbidden

- Code or configuration keyed by tool/operation name.
- Handwritten producer-consumer edges such as `create_issue -> get_issue`.
- Handwritten inverse pairs such as `create_customer -> delete_customer`.
- Per-tool expected values, assertions, test cases, or cleanup functions.
- Source-code-derived behavior claims used as the empirical verdict.
- A model's unsupported judgment used as the final verdict.

A sandbox manifest can say which account is disposable. It cannot say what an operation should do.

## Architecture

Use Python 3.13 with `uv`. The 14-day constraint favors the official MCP Python SDK, Pydantic, JSON Schema tooling, Hypothesis strategies, SQLite, and subprocess control. Do not start with Rust or a hosted microservice split.

```text
CLI
 ├─ Collector
 │   ├─ MCP stdio adapter
 │   ├─ MCP Streamable HTTP adapter
 │   └─ OpenAPI adapter
 ├─ Identity resolver
 ├─ Planner
 │   ├─ deterministic schema analysis
 │   └─ constrained LLM value/dependency inference
 ├─ Executor
 │   ├─ state machine
 │   ├─ transport fault injector
 │   └─ concurrency scheduler
 ├─ Observers
 │   ├─ API read-back observer
 │   ├─ local process/filesystem/network observer
 │   ├─ webhook/event observer
 │   └─ timing sampler
 ├─ Classifier
 ├─ Evidence store
 ├─ Attestor
 └─ Registry index + policy lookup
```

Keep all modules in one process and one repository. Use interfaces only where transports or observers genuinely vary.

### Collector

Record the actual wire representation. Do not infer annotations from source.

For each session, store:

- Negotiated protocol revision.
- Transport and endpoint or package reference.
- Full `tools/list` response.
- Tool input and output schemas.
- Advertised annotations and metadata.
- Runtime, package, container, and environment digests when available.
- Client SDK and version.

Support pre-2026-07-28 MCP and the 2026-07-28 stateless protocol. The TypeScript and Python SDKs use different protocol-negotiation defaults, so pin the protocol revision explicitly. Treat `tools/list_changed` and `subscriptions/listen` as hints. The spec says notifications are best-effort; poll and compare digests.

### Identity

The official MCP Registry provides immutable `(server name, server version)` metadata. It has no tool list, stable tool identifier, or schema hash. `serverInfo` is self-reported.

Use this key:

```text
server_subject = sha256(
  registry_name? ||
  registry_version? ||
  resolved_package_or_endpoint ||
  runtime_artifact_digest? ||
  negotiated_protocol_revision
)

tool_subject = sha256(
  server_subject ||
  canonical_json(tool_definition_from_tools_list)
)
```

A behavioral contract addresses `tool_subject`. A mutable human-readable alias points to the newest observation. Never attach a contract only to a tool name such as `create_issue`.

### Planner

The planner emits a typed intermediate representation. It cannot call tools directly.

```text
ProbePlan
- subject_digest
- prerequisites[]
- generated_resources[]
- steps[]
- observers[]
- cleanup_budget
- stop_conditions[]
- rationale_evidence[]
```

The deterministic pass extracts:

- Required fields, types, enum values, formats, bounds, defaults, and examples.
- Candidate identifier fields.
- Candidate create/read/update/delete/act classes from names and schemas.
- Producer-consumer edges from output/input schema overlap.
- Candidate read-back tools.
- Candidate inverse tools.

The model pass receives only the tool catalog, schemas, descriptions, sandbox constraints, and deterministic candidates. It fills semantically constrained values, ranks ambiguous dependency edges, and rejects unsafe plans. It returns schema-valid JSON. Temperature is zero. Persist the prompt, model identifier, and response.

Use two independent planner runs for findings that will be published. Agreement is evidence about plan stability, not behavior truth.

### Executor

The executor is deterministic. It records a Jepsen-style history: invocation, start time, completion or interruption, response, and each observer result.

Probe primitives:

1. `DISCOVER`
2. `INSTANTIATE`
3. `CALL_ONCE`
4. `READ_BACK`
5. `REPEAT`
6. `RETRY_AFTER_INTERRUPT`
7. `PARALLEL_DOUBLE_FIRE`
8. `VERIFY_WITH_TIMING`
9. `PERTURB_AND_UNDO`
10. `BOUNDARY_MUTATION_FUZZ`
11. `CROSS_CALL_CONSISTENCY`
12. `PRECONDITION_REVOCATION`

Default pipeline:

```text
DISCOVER
  -> INSTANTIATE
  -> CALL_ONCE
  -> READ_BACK
  -> REPEAT | RETRY_AFTER_INTERRUPT | PARALLEL_DOUBLE_FIRE
  -> VERIFY_WITH_TIMING
  -> PERTURB_AND_UNDO
```

Do not run repeat, interrupt, concurrent, or inverse probes without a working observer and disposable scope. An LLM does not waive this gate.

### Observers

There are two evidence classes.

#### Instrumented local server

Run the server in a disposable VM or container. Capture:

- Filesystem snapshot differences.
- Child processes.
- DNS and network destinations.
- Outbound request metadata with secrets removed.
- Environment and persistent-volume differences.
- Stdout/stderr and exit status.

This detects telemetry, cache writes, and hidden egress. It does not prove the semantic effect at a remote service.

#### Black-box remote service

Capture:

- Read-back through sibling tools inferred from schemas.
- Service event or audit logs when generically available.
- Webhooks delivered to a generic collector.
- Resource counts and digests before and after.
- Visibility delay distribution.

If there is no observer, mark the property `not_observable`. Do not convert absence of evidence into `read_only`, `idempotent`, or `reversible`.

### Classifier

Use deterministic predicates over the history. The model may explain a result after classification. It cannot set the classification.

Use four states for each property:

- `observed_true`
- `observed_false`
- `inconclusive`
- `not_applicable`

Include an evidence scope. A statement such as `idempotent: observed_true` means only that repeated calls under the recorded preconditions produced no additional observed intended effect. RFC 9110 permits ancillary logging for idempotent operations. Do not classify an operation as non-idempotent solely because analytics or revision history changed.

Separate discrepancy classes:

1. Unsafe false positive: the annotation claims safety and execution disproves it.
2. Conservative false negative: the annotation omits safety that execution supports.
3. Deployment variance: identical name/schema shows different behavior or annotation by endpoint/environment.
4. Unobservable claim: the advertised guarantee cannot be verified from exposed surfaces.
5. Undeclared side effect: execution causes an observed effect absent from the description.
6. Consistency drift: visibility timing changes beyond the historical distribution.
7. Wire/source drift: serialization or generation changes the advertised claim.

Do not publish all seven as "lies." Lead with unsafe false positives and reproducible undeclared effects. Label conservative mismatches as quality defects.

## Contract and evidence format

Use an in-toto Statement v1 with predicate type:

```text
https://behavioral-abi.dev/contract/v0
```

The draft schema is in `research/signing/contract-schema-v0.json`.

Minimum predicate:

```json
{
  "operation": {
    "server_subject": "sha256:...",
    "tool_subject": "sha256:...",
    "name": "refund_payment",
    "vendor_declared": {
      "read_only": false,
      "destructive": false,
      "idempotent": true,
      "open_world": true
    }
  },
  "observed": {
    "idempotent": {"status": "observed_true", "runs": 3},
    "reversible": {"status": "observed_false"},
    "side_effects": ["refund_created", "customer_email_sent"],
    "consistency": {
      "refund_status": {
        "model": "eventual",
        "samples": 30,
        "p50_seconds": 8.2,
        "p95_seconds": 42.0
      }
    }
  },
  "preconditions": ["payment.status == captured"],
  "evidence_ref": {
    "uri": "cas://sha256/...",
    "digest": "sha256:..."
  },
  "verified_at": "...",
  "prober": {"id": "behavioral-abi", "version": "0.0.1"}
}
```

Also record:

- Observer coverage.
- Run count.
- Clean-state method.
- Protocol and SDK versions.
- Planner model and prompt digest.
- Confidence derived from reproduction count, never an opaque model score.
- `supersedes` digest for drift history.

Keep raw evidence out of Rekor. Rekor v2 has a 100 KB public-entry cap and stores commitments/proofs rather than attestation bodies. Store evidence and the full contract in a content-addressed object store. Sign the contract as a DSSE envelope with keyless Cosign/Fulcio and log it to public Rekor v2. Use the public witness-backed log. Do not build a custom transparency log in v0.

Later, add an independent RFC 3161 timestamp. Rekor v2 no longer provides its own signed timestamp, so the TSA proof is not redundant.

Primary sources:

- in-toto attestation: https://github.com/in-toto/attestation
- DSSE: https://github.com/secure-systems-lab/dsse
- Sigstore/Cosign: https://docs.sigstore.dev/cosign/signing/signing_with_blobs/
- Rekor v2: https://blog.sigstore.dev/rekor-v2-ga/
- SLSA provenance shape: https://slsa.dev/spec/v1.0/provenance

## Registry v0

Use SQLite plus a content-addressed filesystem during the kill test.

Tables:

```text
subjects
- digest PK
- registry_name
- registry_version
- package_or_endpoint
- protocol_revision
- first_seen_at
- last_seen_at

observations
- contract_digest PK
- subject_digest
- observed_at
- predicate_type
- evidence_digest
- rekor_log_index
- supersedes_digest

claims
- contract_digest
- property
- status
- value_json
- coverage_json

aliases
- alias PK
- subject_digest
- valid_from
- valid_until
```

API surface:

```text
GET /v0/subjects/{digest}/latest
GET /v0/subjects/{digest}/history
GET /v0/subjects/{digest}/policy?intent=retry
GET /v0/contracts/{digest}
GET /v0/evidence/{digest}
POST /v0/resolve
```

`POST /v0/resolve` accepts the observed tool definition and returns the exact subject digest. Runtime SDKs must not use fuzzy name matching for consequential policy.

Policy response:

```json
{
  "decision": "deny_retry",
  "basis": "observed_false",
  "contract_digest": "sha256:...",
  "verified_at": "...",
  "expires_at": "...",
  "reason": "Repeated identical calls created two resources in 3/3 clean runs."
}
```

A stale, missing, inconclusive, or identity-mismatched contract returns `unknown`, not `allow`.

## Runtime demonstration

Use Claude Code `PreToolUse`. It fires before MCP calls, receives `tool_name` and `tool_input`, and can return `hookSpecificOutput.permissionDecision: "deny"`. It supports an HTTP handler, so the hook can call the registry without a local SDK.

Primary source: https://code.claude.com/docs/en/hooks

Demo path:

1. The agent calls a sandboxed non-idempotent create tool.
2. The tool transport returns an ambiguous failure after the server accepts the action.
3. The model attempts the same call again.
4. The hook resolves the tool definition digest and queries `intent=retry`.
5. The registry returns `deny_retry` with the signed contract digest.
6. The hook blocks the call before execution and returns a reason to the model.
7. The model calls the inferred read-back tool instead.

Do not use OpenAI Agents SDK guardrails for this demo. Its documented guardrail pipeline does not apply to `HostedMCPTool`. Do not use LangGraph interrupts; they default to human pause and can rerun a node from its start after resume.

The production integration should later be an MCP gateway. Do not build it during the kill test.

## Target selection

### Three stateful sandboxes

1. **Stripe test mode**
   - Explicit idempotency-key semantics and 24-hour key retention.
   - Observable resources, events, and webhooks.
   - First-party hosted MCP at `mcp.stripe.com` and official Agent Toolkit.
   - Primary sources: https://docs.stripe.com/api/idempotent_requests and https://docs.stripe.com/mcp

2. **GitHub Issues in a disposable repository**
   - Free, cheap reset, mature REST read-back, visible audit trail.
   - First-party MCP server with about 90 tools.
   - No built-in server-side rate or tool access control according to its maintainers; the test harness must scope the token and repository.
   - Primary source: https://github.com/github/github-mcp-server

3. **Cloudflare free-tier account**
   - First-party MCP servers across product surfaces.
   - No-card, non-expiring free tier for selected products.
   - Cloud target without LocalStack's fidelity problem.
   - Primary source: https://developers.cloudflare.com/agents/model-context-protocol/cloudflare/servers-for-cloudflare/

GitHub Issues fills the ticketing slot. Exclude Zendesk because it lacks a first-party MCP server. Exclude LocalStack because its own documentation says service coverage is incomplete and real AWS quotas are not enforced; a result there certifies LocalStack, not AWS.

### Top-20 report population

Define the population before running probes:

- Publicly installable or reachable on 2026-08-20.
- Maintained or first-party.
- Popularity supported by at least one hard metric: npm/PyPI downloads, GitHub stars, client bundling, or vendor usage.
- Probeable without production data or irreversible real-world action.
- Exact server version or endpoint recorded.

Do not rank by PulseMCP estimates alone. The corpus found errors of roughly 3,900x against npm for the same Sentry server.

Run local instrumented probes for open-source servers. Run stateful remote probes only where a disposable test account and read-back oracle exist. Label unexecuted properties `not_observable`; do not call a static census an empirical verification.

## Initial discrepancy hypotheses

Treat these as hypotheses until the prober reproduces them:

1. **Supabase `execute_sql`**: `read_only` enforcement lives in a closed Management API. Test whether a write is rejected in a disposable project.
2. **Notion generated annotations**: POST-based `post-search` and `query-data-source` are conservatively marked non-read-only because generation keys on HTTP verb, not semantic effect.
3. **Firecrawl deployment variance**: `readOnlyHint` changes with `CLOUD_SERVICE` for the same tool name and description.
4. **Chrome DevTools hidden egress**: default telemetry and optional CrUX POSTs are absent from MCP-visible tool descriptions.
5. **Figma community server hidden egress**: its read-only tool emits default PostHog telemetry.
6. **GitHub wire defaults**: the Go SDK serializes unset boolean fields into the wire representation. Verify actual `tools/list` against source and behavior; do not claim the conservative default is unsafe without execution evidence.
7. **Memory MCP**: handler-level deduplication may support idempotency that the declared field denies.
8. **Stripe Agent Toolkit**: the remaining generic tool-construction path may make annotations structurally unavailable.

The launch report must distinguish unsafe contradictions from conservative or representational defects.

## Fourteen-day execution

### Days 1-2: freeze method and artifact formats

- Write the no-per-tool-logic policy as an executable linter over manifests and plans.
- Implement canonical JSON, subject hashing, evidence-bundle layout, and `ProbePlan` schema.
- Implement MCP discovery for stdio and Streamable HTTP with explicit protocol pinning.
- Capture one server's `tools/list` and produce a stable subject digest.
- Freeze pass/kill conditions publicly before seeing results.

Exit: the same target produces the same digest and evidence layout twice.

### Days 3-4: generic planner and valid-call rate

- Implement deterministic schema analysis and JSON Schema value generation.
- Add the constrained model pass for semantic values and dependency ranking.
- Run against the 20-server catalog without stateful mutations.
- Measure valid first-call rate on sandbox-eligible tools.

Exit: at least 70% valid first calls. Kill or radically narrow if this fails after two planner iterations.

### Days 5-6: state machine and observers

- Implement `CALL_ONCE`, `READ_BACK`, `REPEAT`, and `VERIFY_WITH_TIMING`.
- Implement local filesystem/process/network observers.
- Implement generic webhook/event collector and inferred read-back observer.
- Add clean-state run identifiers and resource prefixes.

Exit: one generic create/read sequence works on Stripe and GitHub with no named operation in code or configuration.

### Days 7-8: consequential probes

- Add `RETRY_AFTER_INTERRUPT` and `PARALLEL_DOUBLE_FIRE`.
- Add transport interruption after request send but before response receipt.
- Run Stripe, GitHub, and Cloudflare from clean state.
- Record all inconclusive results rather than tuning assertions to targets.

Exit: at least three candidate schema-invisible findings with full histories.

### Days 9-10: reversibility, preconditions, reproduction

- Add inferred inverse pairing and `PERTURB_AND_UNDO`.
- Add `PRECONDITION_REVOCATION` where disposable prerequisites can be revoked.
- Re-run every candidate three times from clean state.
- Remove findings that depend on timing flukes, model interpretation, or hidden manual setup.

Exit: at least five findings, four reproduced 3/3, including three stateful black-box findings.

### Day 11: contracts and signatures

- Convert histories into deterministic property classifications.
- Emit in-toto Statements and DSSE envelopes.
- Sign with Cosign/Fulcio and record Rekor v2 inclusion.
- Verify each artifact from a clean machine or container using only public trust roots and the evidence digest.

Exit: one command verifies identity, signature, log inclusion, and evidence integrity.

### Day 12: registry lookup and runtime gate

- Load contracts into SQLite.
- Implement exact-digest resolve, latest, history, and policy endpoints.
- Implement the Claude Code HTTP `PreToolUse` hook.
- Demonstrate deny-retry followed by read-back recovery.

Exit: the second consequential call is blocked before the server sees it.

### Day 13: report

Publish:

- Population and ranking method.
- Claimed-versus-observed matrix.
- Separate unsafe, conservative, deployment-variant, unobservable, and hidden-side-effect classes.
- Minimal reproducers and signed contracts.
- False-positive and inconclusive counts.
- Method limits.
- Glama comparison based on concrete property coverage.

Avoid the title "the annotations are lying" unless the executed evidence contains unsafe false positives. A better default is "We executed the top 20 MCP servers. Here is what their annotations do not tell you."

### Day 14: external reproduction and decision

- Give one independent runtime or developer only the CLI, sandbox manifest template, and corpus list.
- Watch them reproduce one result without help.
- Run the published kill rubric without changing thresholds.
- Continue only if every pass condition holds.

## What not to build

Do not build these before the kill decision:

- Web dashboard.
- User accounts, billing, teams, or enterprise tenancy.
- Vendor badge or certification workflow.
- Custom transparency log.
- Custom cryptography.
- MCP gateway.
- Natural-language query UI.
- Continuous scheduler beyond one local cron-compatible command.
- Broad policy language.
- Private registry.
- Insurance or audit feed.

## Post-kill-test sequence

If the test passes:

1. Open-source the CLI, probe IR, evidence format, verifier, and runtime hook.
2. Keep the hosted cross-provider history, scheduler, remote evidence storage, and policy API as the paid data plane.
3. Add a hosted MCP gateway that resolves exact tool digests and applies contracts for any runtime.
4. Reprobe on registry version publication, package digest changes, `tools/list` digest changes, and a bounded periodic schedule.
5. Publish append-only drift timelines and machine-readable deltas.
6. Add enterprise private targets without mixing their evidence into the public registry.
7. Add vendor responses and corrected-contract attestations without deleting prior observations.

The moat is not the scanner, schema, signature, or badge. It is the dated, reproducible, cross-provider observation history. The implementation must preserve old evidence even when a vendor fixes the behavior.

## Research corpus map

- `research/mcp-spec/SUMMARY.md`: protocol revisions, trust language, registry identity, client behavior, SDK traps.
- `research/top-servers/SUMMARY.md`: top-20 census, source inspection, sandboxability, discrepancy hypotheses.
- `research/landscape/SUMMARY.md`: direct and adjacent competitors; Glama risk.
- `research/probing/SUMMARY.md`: RESTler, Schemathesis, ToolFuzz, DCIChecker, Jepsen, PBS, probe vocabulary.
- `research/signing/SUMMARY.md`: DSSE, in-toto, Sigstore, Rekor v2, draft contract schema.
- `research/sandboxes-runtimes/SUMMARY.md`: Stripe/GitHub/Cloudflare and runtime-hook selection.

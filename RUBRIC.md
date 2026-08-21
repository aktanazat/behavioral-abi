# Behavioral ABI rubric (frozen)

Source: `IMPLEMENTATION-PLAN.md` (research date 2026-08-20), quoted verbatim below. This
file is frozen at commit `rubric-frozen` before any implementation code is added. Do not
edit the thresholds after that tag; superseding requires a new dated rubric, not a rewrite
of this one.

## Phase 1 threshold — Days 1-2: freeze method and artifact formats

- Write the no-per-tool-logic policy as an executable linter over manifests and plans.
- Implement canonical JSON, subject hashing, evidence-bundle layout, and `ProbePlan` schema.
- Implement MCP discovery for stdio and Streamable HTTP with explicit protocol pinning.
- Capture one server's `tools/list` and produce a stable subject digest.
- Freeze pass/kill conditions publicly before seeing results.

Exit: the same target produces the same digest and evidence layout twice.

## Full 14-day pass conditions

All must pass:

1. Probe three unrelated sandbox targets: Stripe test mode, GitHub Issues in a disposable repository, and Cloudflare's free tier.
2. Enumerate and run the same planner against 20 publicly runnable popular MCP servers. Publish exact inclusion rules and popularity evidence.
3. Discover at least five consequential schema-invisible behaviors. At least four reproduce in three independent clean runs.
4. At least three reproduced findings come from stateful black-box execution, not source review or static annotation inspection.
5. No operation-specific generator, assertion, inverse mapping, fixture, or cleanup routine is added.
6. One Claude Code `PreToolUse` hook consumes a verified contract and blocks or rewrites one unsafe retry before execution.
7. Every public finding links to the raw probe history, environment digest, tool-definition digest, reproducer, contract, signature, and Rekor inclusion proof.

## Full 14-day kill conditions

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

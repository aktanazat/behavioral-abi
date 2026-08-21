# Behavioral ABI interim kill-test report: planner valid-call gate failed

Run `behavioral-abi-kill-test-2026-08`, generated at `2026-08-21T03:58:23.106929+00:00`.

Rubric outcome: **kill**.

## Population and ranking method

- Population size: 20
- Servers run: 12
- Inclusion rules: Publicly installable/reachable, maintained/first-party, hard popularity metric, sandboxable without production data.
- Popularity evidence: GitHub stars and npm/PyPI downloads primary; curated registry presence corroborating; PulseMCP secondary only.
- Sandbox targets: Stripe test mode, GitHub disposable repository, Cloudflare free tier

## Rubric: pass conditions

| condition | status | numerator | denominator | detail |
| --- | --- | --- | --- | --- |
| three_sandboxes | not_satisfied | 2.0 | 3.0 | at least one of the three named sandboxes did not execute |
| top20_corpus | not_satisfied | 12.0 | 20.0 | 12/20 servers run, inclusion_rules_published=True, popularity_evidence_published=True |
| consequential_and_reproduced | not_satisfied | 0.0 | 0.0 | 0 consequential findings (need >=5), 0 reproduced 3x clean (need >=4) |
| stateful_black_box | not_satisfied | 0.0 | 3.0 | 0 reproduced findings from stateful black-box execution (need >=3) |
| no_operation_specific_code | satisfied | 0.0 | 0.0 | 0 no-per-tool-logic lint violation(s) |
| runtime_hook_demo | satisfied | - | - | hook_blocked_or_rewrote_unsafe_retry=True |
| full_evidence_chain | satisfied | 1.0 | 1.0 | 1/1 public findings link the full evidence chain |

## Rubric: kill conditions

| condition | status | numerator | denominator | detail |
| --- | --- | --- | --- | --- |
| human_intervention_rate | not_triggered | 0.0 | 2.0 | 0/2 successful probe plans needed a human-written value (0.0%, kill if >10%) |
| planner_valid_call_rate | triggered | 7.0 | 37.0 | 7/37 sandbox-eligible tool schemas got a valid first call (18.9%, kill if <70%) |
| insufficient_consequential_findings | pending | - | - | finding-count kill gate waits for all three sandboxes and the 20-server corpus |
| insufficient_reproduced_findings | pending | - | - | reproduction-count kill gate waits for all three sandboxes and the 20-server corpus |
| non_consequential_majority | pending | - | - | non-consequential-majority gate waits for findings and full probe coverage |
| equivalent_competitor_service | not_triggered | - | - | equivalent_service_exists=False |
| policy_requires_provider_logic | not_triggered | - | - | decision_required_provider_specific_logic=False |

## Claimed-versus-observed matrix

| tool_subject | tool_name | property | vendor_declared | observed | runs | mismatch |
| --- | --- | --- | --- | --- | --- | --- |
| sha256:3d2cb3619d47c78bf1bffd79c67ef6d2b790600cf322e0a8224061c11c6d0fe1 | create_record | idempotent | True | observed_false | 6 | True |
| sha256:d45fc094c5621a193664fc268695f56d89c5617478cd6adf5108e1a6107ce46e | create_entities | idempotent | False | observed_true | 6 | True |

## Discrepancy classes

- **unsafe** (unsafe_false_positive): sha256:3d2cb3619d47c78bf1bffd79c67ef6d2b790600cf322e0a8224061c11c6d0fe1
- **conservative defect** (conservative_false_negative): sha256:d45fc094c5621a193664fc268695f56d89c5617478cd6adf5108e1a6107ce46e

## Reproducers and evidence links

- `sha256:3d2cb3619d47c78bf1bffd79c67ef6d2b790600cf322e0a8224061c11c6d0fe1` (create_record), provenance=local_instrumented: history=sha256:60e1b05feb44c185c7a3379f65a9170a5e3363c3d72e411514aac7fbc99e28cd, sha256:325c8ff494786555a6044c822460d0e11ca1607f6daeb6ed20cd33da95049217, sha256:700eab0a522b95f7d3761cc3b01dde6d1629fad826c45102fbbd644877354809; reproducer=sha256:432673f4f3f9b17396ee4530fc80195dd95e22c7c6548eea9613678a5d22419e; evidence=sha256:463869f356919564cd0461dd66342bef7da840f4b40cacef2aec6374428f3524, sha256:ab4a5c8a915e507fad67dcccc1fc576693f846dd5a10e899f1201a4b3ebfe33c
- `sha256:d45fc094c5621a193664fc268695f56d89c5617478cd6adf5108e1a6107ce46e` (create_entities), provenance=stateful_black_box: history=sha256:73844819d3eaaa717cd9d8bce421603a3f84f12131d993c77959397233d34f33, sha256:5bbe8571eca9ab448ca64f668bda9f7a3c1aa578c85eb6c7118a8af86afda304, sha256:ece04a490f74a2a0e9a0179dfc0e622c4c8c7ca0f3591ef7b26ed2b5e1999fee; reproducer=sha256:4ec0293954c7cdd2374dfb00f9ef6a36540316f3a0f65bfc7b6471a0a8c0cf26; evidence=sha256:161365cfa74288b461d1cb8e17c4e973cd85141107a9ef8d2811be0c0579e4f7

## False-positive and inconclusive counts

- Empirical unsafe false positives: 1
- Non-empirical (static/source-derived) unsafe false-positive candidates: 0 (not counted as empirical)
- Inconclusive claims: 0
- Not-observable claims: 0
- Empirical findings: 2
- Non-empirical findings: 0

## Method limits

- The unsafe false-positive demo target is synthetic and proves the runtime gate, not public-provider prevalence.
- GitHub stateful execution and Cloudflare authentication remain incomplete in this interim run.
- GitHub read-only calls came from a model plan rejected for missing dependency provenance and do not count toward planner validity.
- Iteration-2 valid-call rate covers the currently executable subset, not all 20 corpus servers.

## Glama comparison

| dimension | glama | babi | detail |
| --- | --- | --- | --- |
| syscall/network observation in an isolated runtime | yes | unverified | Glama executes open-source MCP servers in Firecracker and observes syscalls and network activity. unverified this run -- hypothesis (source: https://glama.ai/mcp/methodology) |
| schema drift rescans on commit | yes | unverified | Glama rescans on commits and records schema drift. unverified this run -- hypothesis (source: https://glama.ai/mcp/methodology) |
| signed semantic contract for idempotency | unverified | unverified | Not found published on the methodology page as of the plan's research date; unverified this run. unverified this run -- hypothesis (source: https://glama.ai/mcp/methodology) |
| signed semantic contract for reversibility | unverified | unverified | Not found published on the methodology page as of the plan's research date; unverified this run. unverified this run -- hypothesis (source: https://glama.ai/mcp/methodology) |
| signed semantic contract for hidden preconditions | unverified | unverified | Not found published on the methodology page as of the plan's research date; unverified this run. unverified this run -- hypothesis (source: https://glama.ai/mcp/methodology) |
| signed semantic contract for consistency timing | unverified | unverified | Not found published on the methodology page as of the plan's research date; unverified this run. unverified this run -- hypothesis (source: https://glama.ai/mcp/methodology) |

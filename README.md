# Behavioral ABI

`babi` measures how MCP tools behave, not only how their schemas describe them. It collects content-addressed evidence, builds generic probe plans, records observations, classifies claims, emits behavioral contracts, and applies exact-subject retry policy.

This repository is a completed kill-test artifact. It is not a production safety layer.

## Result

The frozen rubric stopped the registry company thesis. The second planner iteration produced valid first calls for 7 of 37 sandbox-eligible tool schemas, or 18.9%. The pass threshold was 70%.

The useful part is the generic prober. The report, rejected plans, contracts, and runtime-gate demonstration remain in the repository so the result is auditable.

Read [`report/REPORT.md`](report/REPORT.md) for the measured result and [`RUBRIC.md`](RUBRIC.md) for the frozen criteria.

## What is implemented

- MCP discovery with canonical server and tool subjects
- deterministic JSON Schema analysis and argument synthesis
- constrained model-plan validation with dependency provenance
- repeat, interrupted-retry, parallel-double-fire, read-back, and timing probes
- black-box and local observers
- deterministic behavior classification and discrepancy classes
- in-toto behavioral contracts with DSSE and Cosign support
- a content-addressed evidence store and SQLite registry
- an exact-subject retry policy and Claude Code `PreToolUse` hook

The `babi` CLI currently exposes safe discovery and the no-per-tool-logic linter. The other stages are library modules and scripts.

## Requirements

- Python 3.13
- [`uv`](https://docs.astral.sh/uv/)
- Docker for the included MCP target manifests
- Cosign only for keyless signing and verification

## Set up

```sh
uv sync --dev
uv run babi --help
uv run babi lint
uv run pytest
```

## Run a discovery probe

The default CLI policy is discovery-only. This example starts the MCP memory server in a disposable Docker container and writes evidence under `./evidence/`.

```sh
uv run babi probe targets/memory.json --policy safe-v0
```

Do not run stateful probes against a real account. Use a disposable sandbox and the matching manifest under `sandboxes/`.

## Repository layout

- `src/babi/`: planner, executor, observers, classifier, contracts, registry, and hook
- `targets/`: connection manifests for the frozen MCP corpus
- `sandboxes/`: disposable-scope manifests and lifecycle adapters
- `corpus/top20.json`: frozen population data
- `runs/`: retained kill-test outputs and rejected evidence
- `report/`: terminal report and machine-readable result
- `scripts/`: measurement, reproduction, signing, and registry commands
- `tests/`: behavioral contract tests

## Status

The company thesis is closed under the frozen rubric. The code remains useful as an open research artifact and a base for a narrower MCP annotation auditor.

## License

MIT. See [`LICENSE`](LICENSE).

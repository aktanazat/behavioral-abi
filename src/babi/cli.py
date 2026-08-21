"""`babi` command-line entry point."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from importlib.metadata import version as _pkg_version
from pathlib import Path

from mcp import types
from pydantic import ValidationError

from babi.canon import Json
from babi.collect import build_discover_bundle, discover, gather_runtime_digests
from babi.evidence import EvidenceStore
from babi.manifest import SandboxManifest
from babi.target import TargetSpec

_SUPPORTED_POLICIES = ("safe-v0",)
_CLIENT_INFO = types.Implementation(name="babi", version=_pkg_version("babi"))


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return args.handler(args)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="babi", description="Behavioral ABI measurement CLI.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    probe = subparsers.add_parser(
        "probe",
        help="Discover one MCP target's tool catalog and store a signed-identity evidence bundle.",
    )
    probe.add_argument("target", type=Path, help="Path to a targets/*.json file.")
    probe.add_argument(
        "--sandbox",
        type=Path,
        default=None,
        help="Path to a sandboxes/*.json disposable-scope manifest (optional for local stdio discovery).",
    )
    probe.add_argument(
        "--policy", default="safe-v0", help="Execution policy; only 'safe-v0' (DISCOVER-only) exists so far."
    )
    probe.add_argument(
        "--evidence-root", type=Path, default=None, help="Evidence store root (default: ./evidence)."
    )
    probe.set_defaults(handler=_run_probe)

    lint = subparsers.add_parser("lint", help="Run the no-per-tool-logic policy linter.")
    lint.add_argument("--root", default=".", help="Repository root to lint.")
    lint.set_defaults(handler=_run_lint)

    return parser


def _run_probe(args: argparse.Namespace) -> int:
    if args.policy not in _SUPPORTED_POLICIES:
        print(f"error: unknown policy {args.policy!r}; supported: {', '.join(_SUPPORTED_POLICIES)}", file=sys.stderr)
        return 2

    try:
        target = TargetSpec.model_validate(json.loads(args.target.read_text(encoding="utf-8")))
    except ValidationError as error:
        print(f"error: invalid target {args.target}: {error}", file=sys.stderr)
        return 2

    if args.sandbox is not None:
        try:
            SandboxManifest.model_validate(json.loads(args.sandbox.read_text(encoding="utf-8")))
        except ValidationError as error:
            print(f"error: invalid sandbox manifest {args.sandbox}: {error}", file=sys.stderr)
            return 2
    elif target.transport != "stdio":
        print("error: --sandbox is required for non-stdio (remote/stateful) targets", file=sys.stderr)
        return 2

    discovery = asyncio.run(discover(target, client_info=_CLIENT_INFO))
    runtime_digests = gather_runtime_digests(target)
    bundle = build_discover_bundle(discovery, runtime_digests=runtime_digests)

    store = EvidenceStore(args.evidence_root) if args.evidence_root is not None else EvidenceStore()
    bundle_digest = store.put_json(bundle)

    _print_report(bundle, bundle_digest)
    return 0


def _print_report(bundle: dict[str, Json], bundle_digest: str) -> None:
    print(f"target: {bundle['run']['target_name']} ({bundle['transport']}, {bundle['package_or_endpoint']})")
    print(f"negotiated protocol revision: {bundle['negotiated_protocol_revision']}")
    server_info = bundle["server_info"] or {}
    print(f"server info: {server_info.get('name', '<none>')} {server_info.get('version', '')}".rstrip())
    print(f"server_subject: {bundle['server_subject']}")
    print(f"evidence bundle: {bundle_digest}")
    print()
    header = f"{'tool':<28} {'readOnly':<9} {'destructive':<12} {'idempotent':<11} {'openWorld':<10} tool_subject"
    print(header)
    print("-" * len(header))
    tools_by_name = {tool["name"]: tool for tool in bundle["tools_list"]}
    for name in sorted(bundle["tool_subjects"]):
        tool = tools_by_name.get(name, {})
        annotations = tool.get("annotations") or {}
        digest = bundle["tool_subjects"][name]
        print(
            f"{name:<28} "
            f"{_fmt(annotations.get('readOnlyHint')):<9} "
            f"{_fmt(annotations.get('destructiveHint')):<12} "
            f"{_fmt(annotations.get('idempotentHint')):<11} "
            f"{_fmt(annotations.get('openWorldHint')):<10} "
            f"{digest}"
        )


def _fmt(value: bool | None) -> str:
    return "-" if value is None else str(value).lower()


def _run_lint(args: argparse.Namespace) -> int:
    from babi.lint import lint  # lazy: `babi probe` must keep working even if lint.py is broken/absent

    violations = lint(root=args.root)
    for violation in violations:
        print(violation, file=sys.stderr)
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())

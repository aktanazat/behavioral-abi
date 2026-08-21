"""`babi` command-line entry point."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from babi.evidence import build_evidence
from babi.manifest import ManifestPolicyError, load_manifest_file
from babi.mcp_stdio import collect_stdio_session


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return args.handler(args)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="babi", description="Behavioral ABI measurement CLI.")
    subparsers = parser.add_subparsers(required=True)

    collect = subparsers.add_parser(
        "collect",
        help="Collect initialize + tools/list evidence from a stdio MCP server command.",
        description=(
            "Launch COMMAND as an MCP stdio server, capture its negotiated "
            "session metadata and full tool catalog, and write a canonical "
            "evidence directory. Put babi's own options before COMMAND; "
            "everything from COMMAND onward is passed through to the server."
        ),
    )
    collect.add_argument("--out", type=Path, default=None, help="Evidence directory (default: .artifacts/<subject-digest>).")
    collect.add_argument("--env", action="append", default=[], metavar="NAME=VALUE", help="Environment variable for the server process; may repeat.")
    collect.add_argument("--cwd", default=None, help="Working directory for the server process.")
    collect.add_argument("command", help="Executable to launch as the MCP stdio server.")
    collect.add_argument("args", nargs=argparse.REMAINDER, help="Arguments passed through to the server command.")
    collect.set_defaults(handler=_run_collect)

    manifest_check = subparsers.add_parser(
        "manifest-check",
        help="Validate a sandbox manifest against the environment-scope policy.",
    )
    manifest_check.add_argument("manifest", type=Path, help="Path to a sandbox manifest JSON file.")
    manifest_check.set_defaults(handler=_run_manifest_check)

    return parser


def _run_collect(args: argparse.Namespace) -> int:
    env = dict(_parse_env_assignment(item) for item in args.env) or None
    started_at = datetime.now(timezone.utc)
    raw = asyncio.run(collect_stdio_session(args.command, args.args, env=env, cwd=args.cwd))
    finished_at = datetime.now(timezone.utc)

    bundle = build_evidence(
        command=args.command,
        args=args.args,
        initialize_result=raw.initialize_result,
        tools=raw.tools,
        started_at=started_at,
        finished_at=finished_at,
    )
    subject_digest = bundle.subject["subject_digest"]
    out_dir = args.out or Path(".artifacts") / subject_digest.split(":", 1)[1]
    bundle.write(out_dir)

    print(
        json.dumps(
            {
                "evidence_dir": str(out_dir),
                "subject_digest": subject_digest,
                "tool_count": bundle.subject["tool_count"],
                "tools_digest": bundle.subject["tools_digest"],
            },
            sort_keys=True,
        )
    )
    return 0


def _parse_env_assignment(item: str) -> tuple[str, str]:
    name, sep, value = item.partition("=")
    if not sep:
        raise SystemExit(f"--env expects NAME=VALUE, got {item!r}")
    return name, value


def _run_manifest_check(args: argparse.Namespace) -> int:
    try:
        manifest = load_manifest_file(args.manifest)
    except ManifestPolicyError as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "violations": [
                        {"field": v.field, "kind": v.kind, "reason": v.reason} for v in exc.violations
                    ],
                },
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps({"ok": True, "target_label": manifest.target_label}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())

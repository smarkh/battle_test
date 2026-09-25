"""Command-line entry point: python -m battle_test --help"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

from battle_test.config import DEFAULT_CONFIG_PATH, load_config
from battle_test.ollama_client import OllamaClient, OllamaError
from battle_test.pipeline import run_case
from battle_test.prompts import SUPPORTED_STATES
from battle_test.report import render_markdown


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="battle_test",
        description="Stress-test a lawsuit: complaint, summary judgment motion, "
        "defendant's opposition, and optional plaintiff reply.",
    )
    parser.add_argument(
        "--state",
        required=True,
        type=str.upper,
        choices=SUPPORTED_STATES,
        help="Jurisdiction whose law applies (with federal law).",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--facts", type=Path, help="Case information to generate a complaint from.")
    source.add_argument("--complaint", type=Path, help="A complaint you wrote, used as-is.")
    parser.add_argument("--rounds", type=int, choices=(1, 2), help="Override pipeline.rounds.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--out", type=Path, help="Output file (default: timestamped file in output_dir).")
    parser.add_argument("--quiet", action="store_true", help="Don't stream drafts to the terminal.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    cfg = load_config(args.config)
    client = OllamaClient(cfg.ollama_url, cfg.timeout_seconds, cfg.num_ctx, cfg.temperature)

    input_path = args.facts or args.complaint
    text = input_path.read_text(encoding="utf-8")

    def on_stage(title: str, role: str) -> None:
        print(f"\n\n===== {title} ({role}) =====\n", file=sys.stderr, flush=True)

    def on_token(piece: str) -> None:
        print(piece, end="", flush=True)

    try:
        run = run_case(
            cfg,
            client,
            args.state,
            facts=text if args.facts else None,
            complaint=text if args.complaint else None,
            rounds=args.rounds,
            on_stage=on_stage,
            on_token=None if args.quiet else on_token,
        )
    except OllamaError as e:
        print(f"\nerror: {e}", file=sys.stderr)
        return 1

    now = datetime.now()
    out = args.out or cfg.output_dir / f"{now:%Y%m%d-%H%M%S}-{args.state.lower()}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_markdown(run, now), encoding="utf-8")
    print(f"\n\nSaved {len(run.documents)} documents to {out}", file=sys.stderr)
    return 0

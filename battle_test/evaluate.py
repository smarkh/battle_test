"""Score pipeline runs against sample cases with expected authorities.

    python -m battle_test.evaluate --label laptop-7b          # run and score all cases
    python -m battle_test.evaluate --case utah_roofing --rounds 1
    python -m battle_test.evaluate --validate                 # check the case files only

Each case in examples/eval/*.toml names a facts file, the authorities a
competent brief should cite ("core") or may usefully cite ("useful"), and
code areas that are off-topic for the case. Results go to
output/eval/<timestamp>-<label>/: a summary, a JSON file for comparing
setups, and each run's full document set.
"""

import argparse
import json
import sys
import time
import tomllib
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from battle_test import grounding
from battle_test.citations import PLACEHOLDER
from battle_test.config import DEFAULT_CONFIG_PATH, load_config, load_corpus_config
from battle_test.law_index import LawIndex
from battle_test.ollama_client import OllamaClient, OllamaError
from battle_test.pipeline import CaseRun, run_case
from battle_test.report import render_markdown

CASES_DIR = Path(__file__).resolve().parent.parent / "examples" / "eval"

_CITED = (grounding.VERIFIED, grounding.IN_FORCE_NOT_PROVIDED)


@dataclass(frozen=True)
class Expected:
    importance: str  # "core" or "useful"
    citations: tuple[str, ...]  # alternatives: any one counts
    why: str


@dataclass(frozen=True)
class OffTopic:
    prefix: str
    why: str


@dataclass(frozen=True)
class EvalCase:
    name: str
    state: str
    facts_path: Path
    lawyer_reviewed: bool
    expected: tuple[Expected, ...]
    off_topic: tuple[OffTopic, ...]

    def is_off_topic(self, citation: str) -> OffTopic | None:
        return next((o for o in self.off_topic if citation.startswith(o.prefix)), None)


def load_case(path: Path) -> EvalCase:
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    expected = tuple(
        Expected(e["importance"], tuple(e["citations"]), e["why"]) for e in raw.get("expected", [])
    )
    for e in expected:
        if e.importance not in ("core", "useful"):
            raise ValueError(f"{path.name}: importance must be core or useful, got {e.importance!r}")
    return EvalCase(
        name=raw["name"],
        state=raw["state"],
        facts_path=(path.parent / raw["facts"]).resolve(),
        lawyer_reviewed=raw.get("lawyer_reviewed", False),
        expected=expected,
        off_topic=tuple(OffTopic(o["prefix"], o["why"]) for o in raw.get("off_topic", [])),
    )


def load_cases(names: list[str] | None = None) -> list[EvalCase]:
    cases = [load_case(p) for p in sorted(CASES_DIR.glob("*.toml"))]
    if names:
        unknown = set(names) - {c.name for c in cases}
        if unknown:
            raise SystemExit(f"Unknown case(s): {', '.join(sorted(unknown))}. "
                             f"Available: {', '.join(c.name for c in cases)}")
        cases = [c for c in cases if c.name in names]
    return cases


def validate(case: EvalCase, law) -> list[str]:
    """Problems with a case file: missing facts, or expected authorities
    that aren't in the index or aren't in force (e.g. after a new law
    snapshot)."""
    problems = []
    if not case.facts_path.exists():
        problems.append(f"facts file not found: {case.facts_path}")
    for e in case.expected:
        for citation in e.citations:
            hits = law.lookup(citation)
            if not hits:
                problems.append(f"{citation}: not in the law index")
            elif not any(h.in_force for h in hits):
                problems.append(f"{citation}: in the index but not in force ({hits[0].act_status})")
    return problems


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

@dataclass
class ExpectedResult:
    importance: str
    citations: list[str]
    why: str
    provided: bool  # given to the models in any drafting prompt
    cited: bool  # cited (and verified in force) in any document


@dataclass
class Score:
    case: str
    state: str
    lawyer_reviewed: bool
    expected: list[ExpectedResult]
    distinct_cited: list[str]  # every in-force authority cited, anywhere
    off_topic_cited: list[str]
    off_topic_provided: list[str]
    citation_checks: dict[str, int]  # status -> count, summed over documents
    unchecked_lines: int
    placeholders: int
    seconds: float = 0.0
    notes: list[str] = field(default_factory=list)

    def _rate(self, importance: str, attr: str) -> tuple[int, int]:
        group = [e for e in self.expected if e.importance == importance]
        return sum(getattr(e, attr) for e in group), len(group)

    @property
    def core_provided(self) -> tuple[int, int]:
        return self._rate("core", "provided")

    @property
    def core_cited(self) -> tuple[int, int]:
        return self._rate("core", "cited")

    @property
    def useful_cited(self) -> tuple[int, int]:
        return self._rate("useful", "cited")

    @property
    def on_target(self) -> tuple[int, int]:
        """Of the distinct authorities cited, how many are on the expected list."""
        wanted = {c for e in self.expected for c in e.citations}
        return sum(c in wanted for c in self.distinct_cited), len(self.distinct_cited)

    @property
    def problems(self) -> int:
        return sum(self.citation_checks.get(s, 0) for s in grounding.PROBLEM_STATUSES)


def score(run: CaseRun, case: EvalCase, seconds: float = 0.0) -> Score:
    provided = {s.citation for doc in run.documents for s in doc.authorities}
    cited: list[str] = []
    statuses: Counter = Counter()
    for doc in run.documents:
        for check in doc.checks:
            statuses[check.status] += 1
            if check.status in _CITED and check.section and check.section.citation not in cited:
                cited.append(check.section.citation)

    expected = [
        ExpectedResult(e.importance, list(e.citations), e.why,
                       provided=any(c in provided for c in e.citations),
                       cited=any(c in cited for c in e.citations))
        for e in case.expected
    ]
    return Score(
        case=case.name,
        state=case.state,
        lawyer_reviewed=case.lawyer_reviewed,
        expected=expected,
        distinct_cited=cited,
        off_topic_cited=[c for c in cited if case.is_off_topic(c)],
        off_topic_provided=sorted(c for c in provided if case.is_off_topic(c)),
        citation_checks=dict(statuses),
        unchecked_lines=sum(len(doc.unchecked) for doc in run.documents),
        placeholders=sum(1 for doc in run.documents for m in PLACEHOLDER.finditer(doc.text)
                         if m.group().startswith("[CITATION")),
        seconds=seconds,
    )


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _frac(pair: tuple[int, int]) -> str:
    hit, total = pair
    return f"{hit}/{total}" if total else "–"


def render_summary(scores: list[Score], label: str, setup: dict[str, str]) -> str:
    lines = [
        f"# Evaluation — {label}",
        "",
        "- " + "\n- ".join(f"**{k}:** {v}" for k, v in setup.items()),
    ]
    if not all(s.lawyer_reviewed for s in scores):
        lines += ["", "> ⚠ Some expected-authority lists haven't been reviewed by a lawyer yet, "
                      "so treat these scores as provisional."]
    lines += [
        "",
        "| Case | Core given to models | Core cited | Useful cited | Cited on-target "
        "| Off-topic cited | ❌ problems | `[CITATION NEEDED]` | Minutes |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for s in scores:
        lines.append(
            f"| {s.case} | {_frac(s.core_provided)} | {_frac(s.core_cited)} | {_frac(s.useful_cited)} "
            f"| {_frac(s.on_target)} | {len(s.off_topic_cited)} | {s.problems} | {s.placeholders} "
            f"| {s.seconds / 60:.0f} |"
        )
    lines += [
        "",
        "*Core/useful given to models / cited:* expected authorities that were quoted in a drafting "
        "prompt, and that were actually cited (verified in force). *Cited on-target:* of the distinct "
        "authorities cited, how many are on the expected list.",
    ]
    for s in scores:
        lines += ["", f"## {s.case} ({s.state})", ""]
        for e in s.expected:
            mark = "✅ cited" if e.cited else "➖ given, not cited" if e.provided else "❌ missing"
            lines.append(f"- {mark} · *{e.importance}* · `{' / '.join(e.citations)}`: {e.why}")
        if s.off_topic_cited:
            lines += ["", "**Off-topic authorities cited:**", ""]
            lines += [f"- `{c}`" for c in s.off_topic_cited]
        others = [c for c in s.distinct_cited
                  if c not in {x for e in s.expected for x in e.citations} and c not in s.off_topic_cited]
        if others:
            lines += ["", "**Other authorities cited** (not on either list, so worth a look):", ""]
            lines += [f"- `{c}`" for c in others]
        for note in s.notes:
            lines += ["", f"> {note}"]
    return "\n".join(lines) + "\n"


def _score_dict(s: Score) -> dict:
    d = asdict(s)
    d.update(core_provided=s.core_provided, core_cited=s.core_cited, useful_cited=s.useful_cited,
             on_target=s.on_target, problems=s.problems)
    return d


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="battle_test.evaluate", description=__doc__.split("\n\n")[0])
    parser.add_argument("--case", action="append", help="Run only this case (repeatable).")
    parser.add_argument("--label", default="run", help="Name for this setup, e.g. laptop-7b.")
    parser.add_argument("--rounds", type=int, choices=(1, 2), help="Override pipeline.rounds.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--validate", action="store_true", help="Only check the case files.")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    cases = load_cases(args.case)
    with LawIndex(load_corpus_config(args.config).db_path) as law:
        invalid = {c.name: p for c in cases if (p := validate(c, law))}
        for name, problems in invalid.items():
            print(f"{name}: " + "; ".join(problems), file=sys.stderr)
        if args.validate:
            if not invalid:
                print(f"All {len(cases)} case files are valid against snapshot {law.meta()['snapshot']}.")
            return 1 if invalid else 0
        if invalid:
            print("Fix the case files (or run --validate) before evaluating.", file=sys.stderr)
            return 1

        client = OllamaClient(cfg.ollama_url, cfg.timeout_seconds, cfg.num_ctx, cfg.temperature)
        started = datetime.now()
        out_dir = cfg.output_dir / "eval" / f"{started:%Y%m%d-%H%M%S}-{args.label}"
        out_dir.mkdir(parents=True, exist_ok=True)
        setup = {
            "label": args.label,
            "started": f"{started:%Y-%m-%d %H:%M}",
            "plaintiff model": cfg.plaintiff_model,
            "defendant model": cfg.defendant_model,
            "rounds": str(args.rounds or cfg.rounds),
            "law snapshot": law.meta().get("snapshot", "?"),
        }

        scores = []
        for case in cases:
            print(f"\n=== {case.name} ({case.state}) ===", file=sys.stderr, flush=True)
            t0 = time.monotonic()
            try:
                run = run_case(
                    cfg, client, law, case.state,
                    facts=case.facts_path.read_text(encoding="utf-8"),
                    rounds=args.rounds,
                    on_stage=lambda title, role: print(f"  {title} ({role})", file=sys.stderr, flush=True),
                )
            except OllamaError as e:
                print(f"error: {e}", file=sys.stderr)
                return 1
            s = score(run, case, time.monotonic() - t0)
            scores.append(s)
            (out_dir / f"{case.name}.md").write_text(render_markdown(run, datetime.now()), encoding="utf-8")
            print(f"  core cited {_frac(s.core_cited)}, off-topic {len(s.off_topic_cited)}, "
                  f"{s.seconds / 60:.0f} min", file=sys.stderr, flush=True)

    (out_dir / "summary.md").write_text(render_summary(scores, args.label, setup), encoding="utf-8")
    (out_dir / "results.json").write_text(
        json.dumps({"setup": setup, "scores": [_score_dict(s) for s in scores]}, indent=2),
        encoding="utf-8",
    )
    print(f"\nSaved results to {out_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

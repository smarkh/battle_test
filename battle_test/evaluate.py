"""Score pipeline runs against sample cases with expected authorities.

    python -m battle_test.evaluate --label laptop-7b          # run and score all cases
    python -m battle_test.evaluate --case utah_roofing --rounds 1
    python -m battle_test.evaluate --validate                 # check the case files only
    python -m battle_test.evaluate --research-only            # fast: score the search step alone
    python -m battle_test.evaluate --config config.bedrock.toml --model qwen3-235b --repeats 3

Each case in examples/eval/*.toml names a facts file, the authorities a
competent brief should cite ("core") or may usefully cite ("useful"), and
code areas that are off-topic for the case. Results go to
output/eval/<timestamp>-<label>/: a summary, a JSON file for comparing
setups, and each run's full document set.
"""

import argparse
import dataclasses
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
from battle_test.models import ModelError, add_usage, make_client
from battle_test.pipeline import CaseRun, run_case
from battle_test.report import render_markdown, usage_summary

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
    found: bool  # returned by the law-index search for either side
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
    repeat: int = 1  # which run of this case, when --repeats is used
    usage: dict[str, dict[str, int]] = field(default_factory=dict)  # model -> calls and tokens
    cost_usd: float | None = None  # None = the config has no price for a model used

    @property
    def label(self) -> str:
        return self.case if self.repeat == 1 else f"{self.case} #{self.repeat}"

    def _rate(self, importance: str, attr: str) -> tuple[int, int]:
        group = [e for e in self.expected if e.importance == importance]
        return sum(getattr(e, attr) for e in group), len(group)

    @property
    def core_found(self) -> tuple[int, int]:
        return self._rate("core", "found")

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


def score(run: CaseRun, case: EvalCase, seconds: float = 0.0, repeat: int = 1) -> Score:
    found = {c for citations in run.candidates.values() for c in citations}
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
                       found=any(c in found for c in e.citations),
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
        repeat=repeat,
        usage=run.usage,
        cost_usd=run.cost_usd,
    )


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _frac(pair: tuple[int, int]) -> str:
    hit, total = pair
    return f"{hit}/{total}" if total else "–"


def _total(scores: list[Score], attr: str) -> tuple[int, int]:
    pairs = [getattr(s, attr) for s in scores]
    return sum(p[0] for p in pairs), sum(p[1] for p in pairs)


def _cost(scores: list[Score]) -> str:
    """The runs' estimated cost: "–" if nothing reported usage, "?" if a
    model that was used has no price in the config."""
    used = [s for s in scores if s.usage]
    if not used:
        return "–"
    return "?" if any(s.cost_usd is None for s in used) else f"${sum(s.cost_usd for s in used):.3f}"


def _usage_totals(scores: list[Score]) -> dict[str, dict[str, int]]:
    totals: dict[str, dict[str, int]] = {}
    for s in scores:
        for model, counts in s.usage.items():
            total = totals.setdefault(model, dict.fromkeys(counts, 0))
            for key, n in counts.items():
                total[key] += n
    return totals


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
        "| Case | Core found by search | Core given to models | Core cited | Useful cited | Cited on-target "
        "| Off-topic cited | ❌ problems | `[CITATION NEEDED]` | Minutes | Cost |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for s in scores:
        lines.append(
            f"| {s.label} | {_frac(s.core_found)} | {_frac(s.core_provided)} | {_frac(s.core_cited)} "
            f"| {_frac(s.useful_cited)} "
            f"| {_frac(s.on_target)} | {len(s.off_topic_cited)} | {s.problems} | {s.placeholders} "
            f"| {s.seconds / 60:.0f} | {_cost([s])} |"
        )
    if len(scores) > 1:
        lines.append(
            f"| **Total** | {_frac(_total(scores, 'core_found'))} | {_frac(_total(scores, 'core_provided'))} "
            f"| **{_frac(_total(scores, 'core_cited'))}** | {_frac(_total(scores, 'useful_cited'))} "
            f"| {_frac(_total(scores, 'on_target'))} | {sum(len(s.off_topic_cited) for s in scores)} "
            f"| {sum(s.problems for s in scores)} | {sum(s.placeholders for s in scores)} "
            f"| {sum(s.seconds for s in scores) / 60:.0f} | {_cost(scores)} |"
        )
    for model, counts in _usage_totals(scores).items():
        lines += ["", f"**`{model}` usage, all runs:** {counts['calls']} calls, "
                      f"{counts['input_tokens']:,} tokens in, {counts['output_tokens']:,} out."]
    lines += [
        "",
        "*Core found by search / given to models / cited:* expected authorities that the law-index "
        "search returned as candidates, that were then quoted in a drafting prompt, and that were "
        "actually cited (verified in force). *Cited on-target:* of the distinct authorities cited, "
        "how many are on the expected list.",
    ]
    for s in scores:
        lines += ["", f"## {s.label} ({s.state})", ""]
        for e in s.expected:
            mark = ("✅ cited" if e.cited else "➖ given, not cited" if e.provided
                    else "🔍 found by search, not selected" if e.found else "❌ never found by search")
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
    d.update(core_found=s.core_found, core_provided=s.core_provided, core_cited=s.core_cited, useful_cited=s.useful_cited,
             on_target=s.on_target, problems=s.problems)
    return d


# ---------------------------------------------------------------------------
# Research only: a fast check of the search step
# ---------------------------------------------------------------------------

def research_recall(cfg, client, law, case: EvalCase, on_usage=None) -> tuple[list[str], list[str]]:
    """Run just the plaintiff's research step and the index search.

    Returns (queries, candidate citations). One short model call instead of a
    full run, so query-prompt changes can be checked in about a minute per
    case. The defendant's research needs the drafts, so it isn't covered.
    """
    from battle_test import grounding, prompts

    state = prompts.SUPPORTED_STATES[case.state]
    facts = case.facts_path.read_text(encoding="utf-8")
    task = prompts.plaintiff_research_task(state, "Case information", facts)
    system = prompts.plaintiff_system(state)
    queries = grounding.research(
        lambda t: client.chat(cfg.plaintiff_model, system, t, json_mode=True, on_usage=on_usage), task)
    candidates = grounding.gather_candidates(law, queries, case.state.lower())
    return queries, [c.citation for c in candidates]


def render_research(rows: list[tuple[EvalCase, list[str], list[str]]], label: str,
                    usage: dict[str, dict[str, int]] | None = None, cost_usd: float | None = None) -> str:
    lines = [f"# Research-only check — {label}", "",
             "Plaintiff research step only: which expected authorities the law-index search returned.", ""]
    total = found = 0
    for case, queries, candidates in rows:
        hits = [e for e in case.expected if any(c in candidates for c in e.citations)]
        total += len(case.expected)
        found += len(hits)
        lines += [f"## {case.name}: {len(hits)}/{len(case.expected)} found "
                  f"({len(candidates)} candidates)", "", "**Queries:**", ""]
        lines += [f"- {q}" for q in queries]
        lines += ["", "**Expected authorities:**", ""]
        lines += [f"- {'🔍 found' if e in hits else '❌ not found'} · *{e.importance}* · `{e.citations[0]}`"
                  for e in case.expected]
        lines.append("")
    lines[4:4] = [f"**Total: {found}/{total} expected authorities found by search.**", ""]
    if usage:
        lines[6:6] = [f"**Model usage:** {usage_summary(usage, cost_usd)}", ""]
    return "\n".join(lines) + "\n"


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
    parser.add_argument("--research-only", action="store_true",
                        help="Only run the plaintiff's research step and score the search (fast).")
    parser.add_argument("--model", help="Use this model for both roles, instead of the config's [models].")
    parser.add_argument("--repeats", type=int, default=1,
                        help="Run each case this many times. One run is noisy: a change of one "
                             "authority either way means nothing.")
    args = parser.parse_args(argv)
    if args.repeats < 1:
        parser.error("--repeats must be at least 1")

    cfg = load_config(args.config)
    if args.model:
        cfg = dataclasses.replace(cfg, plaintiff_model=args.model, defendant_model=args.model)
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

        client = make_client(cfg)
        started = datetime.now()
        out_dir = cfg.output_dir / "eval" / f"{started:%Y%m%d-%H%M%S}-{args.label}"
        out_dir.mkdir(parents=True, exist_ok=True)

        if args.research_only:
            rows = []
            usage: dict[str, dict[str, int]] = {}
            for case in cases:
                print(f"=== {case.name}: research", file=sys.stderr, flush=True)
                try:
                    rows.append((case, *research_recall(cfg, client, law, case,
                                                        on_usage=lambda used: add_usage(usage, used))))
                except ModelError as e:
                    print(f"error: {e}", file=sys.stderr)
                    return 1
            report = render_research(rows, args.label, usage, cfg.cost(usage))
            (out_dir / "research.md").write_text(report, encoding="utf-8")
            print(report)
            return 0
        setup = {
            "label": args.label,
            "started": f"{started:%Y-%m-%d %H:%M}",
            "provider": cfg.provider,
            "plaintiff model": cfg.plaintiff_model,
            "defendant model": cfg.defendant_model,
            "rounds": str(args.rounds or cfg.rounds),
            "runs per case": str(args.repeats),
            "law snapshot": law.meta().get("snapshot", "?"),
        }

        scores = []
        for case, repeat in ((c, r) for c in cases for r in range(1, args.repeats + 1)):
            name = case.name if repeat == 1 else f"{case.name}-{repeat}"
            print(f"\n=== {name} ({case.state}) ===", file=sys.stderr, flush=True)
            t0 = time.monotonic()
            try:
                run = run_case(
                    cfg, client, law, case.state,
                    facts=case.facts_path.read_text(encoding="utf-8"),
                    rounds=args.rounds,
                    on_stage=lambda title, role: print(f"  {title} ({role})", file=sys.stderr, flush=True),
                )
            except ModelError as e:
                print(f"error: {e}", file=sys.stderr)
                return 1
            s = score(run, case, time.monotonic() - t0, repeat)
            scores.append(s)
            (out_dir / f"{name}.md").write_text(render_markdown(run, datetime.now()), encoding="utf-8")
            print(f"  core cited {_frac(s.core_cited)}, off-topic {len(s.off_topic_cited)}, "
                  f"{s.seconds / 60:.0f} min, cost {_cost([s])}", file=sys.stderr, flush=True)

    (out_dir / "summary.md").write_text(render_summary(scores, args.label, setup), encoding="utf-8")
    (out_dir / "results.json").write_text(
        json.dumps({"setup": setup, "scores": [_score_dict(s) for s in scores]}, indent=2),
        encoding="utf-8",
    )
    print(f"\nSaved results to {out_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

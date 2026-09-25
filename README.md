# battle_test

Stress-tests a lawsuit before it's used for real. A plaintiff model drafts
the complaint (or takes yours) and a motion for summary judgment. A defendant
model writes the opposition, and optionally the plaintiff model replies. No
winner is declared. See `plans/plan.md` for the full design.

**Status: plan step 3.** Drafts are grounded in a local copy of the law:
1. Before drafting, each side searches the law index for the statutes and
   rules it needs.
2. It may cite only the sections it's shown, and everything else gets a
   `[CITATION NEEDED: …]` placeholder.
3. Every statute, rule and constitution citation in the result is then
   checked in code: ✅ in force, ❌ not found / not in force / another
   state's law (also marked inline in the draft), or ⚠ ambiguous.

Case law isn't checked yet (plan step 4), so the models are told not to cite
cases, and any case citation that appears is flagged.

On the dev laptop's 7B model the checking works, but the model often picks
the wrong law or misstates it. `plans/plan.md` covers the model size needed
("Model size estimate"), the planned fixes (3a/3b), the web UI (step 5),
and the Bedrock hosting option.

## Requirements

- Python 3.11+ (the pipeline uses the standard library only)
- [Ollama](https://ollama.com) running, with the models named in
  `config.toml` pulled (default `qwen2.5:7b`)
- The law index, built once (see **Law corpus** below)

## Usage

```
python -m battle_test --state UT --facts examples/utah_roofing_facts.md
python -m battle_test --state CA --complaint path/to/my_complaint.md --rounds 1
```

- `--state`: `UT`, `CA` or `TX` (v1 coverage). Federal law always applies.
- `--facts` or `--complaint`: generate a complaint from case information, or
  use one you wrote.
- `--rounds 1|2`: round 2 adds the plaintiff's reply. The default is set in
  `config.toml`.

The documents are saved as one Markdown file in `output/`, which is
gitignored because it may hold case facts. Keep real case files in `cases/`,
which is also gitignored.

Each output file contains:
- The disclaimer, and the date the law is current as of.
- **Citation check:** a table per document of ✅ / ☑ / ❌ / ⚠ counts, plus
  each citation that needs attention.
- The documents themselves (complaint, motion, opposition, reply), with
  problem citations marked inline.
- **Appendix:** each side's research queries, and every authority quoted to
  the models, with a link to its official source.

## Code layout

| File | What it does |
|---|---|
| `battle_test/cli.py` | Command-line entry point (`python -m battle_test`) |
| `battle_test/pipeline.py` | Runs the flow: research → complaint → motion → opposition → reply |
| `battle_test/prompts.py` | System and task prompts, state list, trial courts |
| `battle_test/grounding.py` | Research, authority selection, and checking each draft's citations |
| `battle_test/citations.py` | Finding and parsing citations in text (no database) |
| `battle_test/law_index.py` | Searching the local law index and resolving citations |
| `battle_test/corpus.py` | Downloading Open US Law and building the index (`python -m battle_test.corpus`) |
| `battle_test/report.py` | Writing the Markdown output |
| `battle_test/ollama_client.py` | Minimal Ollama client (streaming, JSON mode) |
| `battle_test/config.py` | Loads `config.toml` |
| `examples/` | Fictional sample case information for testing |
| `plans/plan.md` | Design, decisions, build steps, and open questions |

## Law corpus

Statutes, constitutions and court rules for Utah, California, Texas and
federal law come from [Open US Law](https://www.vaquill.ai/open-us-law)
(Open US Law by Vaquill AI, CC BY 4.0). They're downloaded and indexed
locally in SQLite, so searches never leave the machine.

```
pip install -r requirements.txt           # pyarrow, needed only to build
python -m battle_test.corpus build        # ~176 MB download, ~1.3 GB index, ~1 min
python -m battle_test.corpus info         # snapshot date and section counts
python -m battle_test.corpus search --state UT "summary judgment"
```

Files are checked against the dataset's published SHA256 checksums. Which
jurisdictions and document types are included, and which quarterly snapshot,
is set under `[corpus]` in `config.toml`. Everything lives in `data/`, which
is gitignored.

## Tests

```
python -m unittest
```

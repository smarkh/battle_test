# battle_test

Stress-tests a lawsuit before it's used for real. A plaintiff model drafts
the complaint (or takes yours) and a motion for summary judgment. A defendant
model writes the opposition, and optionally the plaintiff model replies. No
winner is declared. See `plans/plan.md` for the full design.

**Status: plan step 1.** The pipeline runs end to end, but there's no law
grounding yet. The models are told not to cite anything from memory and to
write `[CITATION NEEDED: …]` placeholders instead.

## Requirements

- Python 3.11+ (standard library only, nothing to install)
- [Ollama](https://ollama.com) running, with the models named in
  `config.toml` pulled (default `qwen2.5:7b`)

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

## Tests

```
python -m unittest
```

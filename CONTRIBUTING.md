# Contributing

## Setup

```bash
git clone https://github.com/aryxnsdfs/token-diet
cd token-diet
pip install -e '.[all,dev]'
pytest -q
```

## Ground rules

- **The eval harness is the guardrail.** Token reduction only counts if task
  success holds. Run `pytest tests/evals/` on every engine change; add a case
  when you add a capability.
- **Keep the prefix append-only.** Never reorder or edit inside the cached
  region of the assembler — it breaks the provider prompt cache (§3.4).
- **Measure with the real tokenizer**, never folklore (§2.4).
- **Graceful fallbacks stay graceful.** Every heavy dep is optional; new code
  must degrade, not crash, when a dep is missing. `ctx doctor` reports status.

## Layout

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full design. Command surface is
generated from `ctx/registry.py` — add a command there and it lights up in the
MCP server, the `.claude/commands/*.md` files, and the proxy at once.

## Pull requests

1. Branch off `main`.
2. `pytest -q` green on your platform.
3. Describe the token/quality trade-off in the PR body.

# Context Engine — Architecture & Build Plan

A local, token-saving, **intelligence-preserving** context layer for AI coding and chat tools
(Claude Code, Claude Cowork, Codex, and any MCP-aware client). Users install it from GitHub,
run a one-time `init`, then drive it from inside their chat by pressing **`/`**.

Working name for the CLI throughout: **`ctx`** (rename freely).

---

## 1. What this is

A small piece of local middleware that sits between your AI client and the model provider.
It intercepts each request, shapes the context to be *minimal but intelligent*, keeps the
prompt prefix stable so it hits the provider's cache, and patches model output back into your
files as diffs. In one line: **build your own Aider / Claude Code core**, exposed as slash
commands.

It ships with two interchangeable front-ends over the same engine:

- **Mode A — MCP server + CLI.** The native path for hosts that support MCP and slash
  commands (Claude Code, Cowork). Install, `ctx init`, press `/`.
- **Mode B — Local proxy.** A `localhost` HTTP proxy for tools you can point at a custom
  base URL, and the universal fallback for non-MCP tools.

---

## 2. Design principles

### 2.1 Token-saving and intelligence are the *same* goal (up to a point)

Long context actively degrades model quality: facts in the middle of a long prompt are used
worse than those at the edges ("lost in the middle"), irrelevant context acts as a distractor,
and signal-to-noise falls as length grows ("context rot"). So a smaller, *curated* context
often **outperforms** a maximal one. Cutting tokens the right way buys intelligence rather than
costing it.

### 2.2 Intelligence lives in three places — preserve each differently

| Dimension | What it is | How to preserve it |
|---|---|---|
| **Information** | The facts / code the model needs | Retrieve on demand; don't summarize when precision matters |
| **Structure** | How things relate (call graph, deps) | The graph-ranked repo map — cheap, keep nearly intact |
| **Intent** | The *why*: goals, constraints, decisions | The append-only decision log — never flatten to bullet soup |

Naive token-saving attacks **Information** by brute volume and accidentally shreds **Structure**
and **Intent**. The correct inversion: keep Structure and Intent nearly whole (cheap, high
value), prune Information hard to *only what's relevant now*, and pull more on demand.

### 2.3 Sort every technique by its effect on quality

| Tier | Effect | Techniques |
|---|---|---|
| **Free lunch** | Zero quality change | Prompt/prefix caching · diff-only output · content-hash caching of parses/embeddings · model routing |
| **Quality-positive** | Cuts tokens **and** improves quality | Lazy tool-based retrieval · tool-output truncation · graph-ranked map · critical context at the prompt edges |
| **Traps** | Saves tokens, costs quality | Lossy regex history compression · minifying code under active edit · whitespace-format golf · summarizing when precision was needed |

### 2.4 Measure, don't trust folklore

Format myths (e.g. "YAML always beats JSON") often don't survive a real token count. Measure
every block against the **actual tokenizer** for the target model. The real win on structured
data is killing *repeated keys* in uniform arrays (use tables/CSV), not the punctuation.

### 2.5 The guardrail: optimize for correct completions, not token count

Tokens are the proxy; **task success is the truth**. Keep a small held-out suite of
representative tasks with automatic checks (does it compile, do tests pass, does a known fix
match) and run it whenever you tighten the engine. If success holds while tokens fall, you
preserved intelligence. If it dips, you cut into bone — back off.

---

## 3. The engine core (shared by both front-ends)

### 3.1 Code index — the Structure store

A background watcher (`watchdog`) re-parses only changed files with `tree-sitter`
(`py-tree-sitter` + per-language grammars), extracting symbols — classes, function signatures,
top-level variables, imports — and dropping bodies and comments. SQLite holds three things:

- a **symbol table** (name, file, line range, signature, docstring),
- a **reference graph** (edges for *calls*, *imports*, *references*),
- a **content hash** per file — the cache key, so unchanged files are never re-parsed.

The graph makes the map *intelligent* rather than a flat dump: when a map is needed, run a
personalized PageRank over the symbol graph (`networkx`) seeded by open files, symbols named in
the user's message, and recently edited files, then emit top-ranked signatures until the map's
token budget is full. Skip embeddings at first — agentic `grep` usually beats vector search for
code; add `sqlite-vec` later as a fallback.

### 3.2 Memory store — the Intent store

Keep the last 3–4 turns **verbatim** (the volatile, high-attention zone). Everything older is
distilled into an **append-only decision log**: records of `{decision, rationale, files_touched,
timestamp}`. Preserve the *why*, not the conversational filler. Append-only is mandatory —
rewriting history would break the prefix cache. Never delete; point back to artifacts instead of
re-including them.

The distillation runs on a small model. Two options:

- **Local (cheapest):** `ollama` + an ultra-small quantized model (`qwen2.5:1.5b`,
  `llama3.2:3b`) — zero cloud cost, runs on the user's hardware.
- **Cloud cheap tier:** a Haiku-class model when local hardware isn't available.

### 3.3 Document pipeline

When a request carries a PDF / Word / Excel file, convert it locally with `markitdown` to clean,
structure-preserving Markdown (headings, tables, lists kept; binary/layout noise dropped) before
it ever reaches the model. Hash the original (`hashlib`) and cache the Markdown in SQLite — drag
the same file in next week and it's pulled from cache, skipping conversion entirely.

### 3.4 Context assembler — the heart

Builds the prompt in layers ordered for **caching first, attention second**: stable content at
the top (cached prefix), the most relevant and most recent content at the bottom (attention
sweet spot).

| Order | Layer | Volatility | Cached? |
|---|---|---|---|
| 1 | System prompt + tool schemas | Frozen for the session | Yes — breakpoint here |
| 2 | Graph-ranked repo map | Changes only when files change | Yes — breakpoint here |
| 3 | Decision log | Append-only | Yes (up to last entry) |
| 4 | Retrieved snippets for *this* task | Per turn | No |
| 5 | Recent verbatim turns | Per turn | No |
| 6 | New user message | Per turn | No |

**The one rule that governs everything: keep the prefix append-only** so the cache stays warm
across the whole session. Anything cached must precede anything volatile, and never reorder or
edit inside the cached region.

**Budget allocator with graceful demotion.** Give each layer a share of the window (minus a
generation reserve), then fit by *demoting* the worst value-density items rather than hard
truncating:

```
fit_to_budget(layers, budget):
    while measured_tokens(layers) > budget:          # measure with the REAL tokenizer
        item = argmin over demotable items of (relevance / tokens)
        if item.can_demote():
            item.demote()        # body -> signature -> name+pointer
        else:                    # full file -> relevant function -> signature + "/explain"
            drop_lowest_priority_droppable(layers)
        if nothing_left_to_demote_or_drop():
            break                # hard truncate only as last resort
```

### 3.5 Budget + cache — the biggest cost lever

Wrap `tiktoken` (Codex/OpenAI) and the provider's token-counting endpoint (Claude) so every
block is measured exactly. Then place **cache breakpoints** so the stable prefix (system prompt,
tool defs, repo map) hits the provider's prompt cache — cached reads are heavily discounted
(roughly an order of magnitude cheaper than fresh input; writes carry a small premium). This one
mechanism saves more than every reformatting trick combined, at zero quality cost. Also keep a
content-hash cache of derived artifacts (parses, summaries, embeddings) so nothing is recomputed.
Verify current caching behaviour and pricing in the docs (see §10).

### 3.6 Patch engine — output savings

Prompt the model — ideally via a tool schema — to emit edits only as `SEARCH`/`REPLACE` or
unified-diff blocks, never whole files. Parse the blocks, then apply with **fuzzy matching**
(`difflib`) so whitespace drift in the anchor doesn't fail the patch. On a miss, widen the anchor
or re-ask with just the failed hunk. Optionally run a syntax check or tests after applying and
feed back only the error. This saves the 495 unchanged lines in a 500-line file.

### 3.7 Model router

Reserve the frontier model for reasoning and code generation. Route mechanical work —
summarization, intent classification, decision-log distillation, commit messages, "is this file
relevant?" pre-filtering — to a local Ollama model or a cheap cloud tier. Same result, large cost
cut.

---

## 4. Front-ends

### 4.1 Mode A — MCP server + CLI (slash commands)

**Install** puts the `ctx` command on PATH; nothing is wired to a host yet.

```
pipx install ctx                              # from PyPI
pipx install git+https://github.com/you/ctx   # from source
# uv, npm (+ bin entry), or a curl|sh installer are equivalent options
```

**`ctx init`** (run once per project) does three things:

1. Builds the initial code index into `.ctx/index.db`.
2. Registers the MCP server with the detected host.
3. Writes any host-native command files.

Per-host registration:

- **Claude Code** — writes a project `.mcp.json` (or runs `claude mcp add ctx -- ctx serve`) so
  the host launches your server, and generates one `.claude/commands/*.md` per command as a
  native belt-and-suspenders path:
  ```json
  { "mcpServers": { "ctx": { "command": "ctx", "args": ["serve"] } } }
  ```
- **Claude Cowork / Claude.ai** — emits the connector config the app needs (stdio command + args
  locally, or an HTTP/SSE URL if hosted).
- **Other MCP hosts** — prints the stdio launch command.
- **Non-MCP tools** — sets up the Mode B proxy fallback.

> Host config paths and CLIs change over time. Have `ctx doctor` verify the wiring and point to
> the host's current MCP docs (see §10) rather than hard-coding assumptions. Note Claude Code
> itself is distributed via npm (`@anthropic-ai/claude-code`); your `ctx` tool registers *with*
> it and can be written in any language.

**The MCP bridge — what makes `/` light up.** `ctx serve` starts the MCP server (official Python
MCP SDK / `FastMCP`) over stdio; the host launches it as a subprocess. From the one command
registry it exposes all three MCP primitives:

- **Prompts** (`@mcp.prompt()`) — *user-invoked*, rendered in the host's `/` menu. **These are
  your slash commands.**
- **Tools** (`@mcp.tool()`) — *model-invoked*, so the model can call `focus` or `grep` mid-task.
- **Resources** — the repo map, attachable by the host.

The moment the host connects, pressing `/` shows your commands.

**In-chat initialization.** The first slash command in a session — `/init` or `/ctx start` —
warms the chat: injects the ranked repo map, confirms the engine is connected, and switches on
diff-output mode. From then on the conversation runs through the engine.

**Per-project state** (so the chat is fully usable once initialized):

```
<project>/
├── .mcp.json            host registration (commit to share with teammates)
├── .claude/commands/    generated native slash commands for Claude Code
└── .ctx/                engine state (gitignored)
    ├── index.db         symbols + reference graph + content hashes
    ├── config.toml      token budgets, model tiers, enabled commands
    ├── decisions.jsonl  append-only decision log
    └── cache/           derived artifacts (parses, summaries)
```

### 4.2 Mode B — Local proxy

A `localhost:8000` server your client points at instead of the official provider endpoint.
Transparent: it optimizes every request without the host needing MCP support.

- **FastAPI** — the async proxy app, handles multiple streams without blocking.
- **uvicorn** — the ASGI server that runs it.
- **httpx** — async client that forwards the final optimized payload to Claude/OpenAI.

**Execution flow:**

1. **Request** — client sends a message (+ optional PDF) to `localhost:8000`.
2. **Intercept & convert** — FastAPI receives it; `markitdown` converts attachments to Markdown
   (hash-cached).
3. **Evaluate history** — check SQLite; if history is too long, the small local model compresses
   the oldest messages into the decision log.
4. **Assemble** — stack strictly: system instructions → converted docs → decision log → recent
   verbatim messages.
5. **Measure** — `tiktoken` verifies size so the top layers trigger the provider's prompt cache.
6. **Dispatch** — `httpx` fires the request.
7. **Return** — response logged to SQLite and streamed back to the client.

### 4.3 Cross-tool reality

The `/` menu is drawn by the host, not by you. You can only *offer* commands; each host decides
whether to render them. **MCP is the lingua franca** — one server, many hosts. Where MCP prompts
aren't supported, the Mode B proxy parses `/text` after the user sends it (same behaviour, no
autocomplete). In your own CLI/TUI you build the real `/` autocomplete yourself
(`prompt_toolkit` / `textual`).

---

## 5. Slash-command system

### 5.1 The command registry — single source of truth

One declarative table; everything else is generated from it.

```python
# registry.py
COMMANDS = {
    "map":      Cmd(desc="Inject the graph-ranked repo map", args="[path]",       handler=engine.map),
    "focus":    Cmd(desc="Pin a file or symbol into context", args="<file|symbol>", handler=engine.focus),
    "explain":  Cmd(desc="Pull just one symbol's body",       args="<symbol>",     handler=engine.explain),
    "diff":     Cmd(desc="Force diff-only output mode",        args="",            handler=engine.diff_mode),
    "compress": Cmd(desc="Distill history to the decision log now", args="",       handler=engine.compress),
    "cost":     Cmd(desc="Show token + cache telemetry",       args="",            handler=engine.cost),
    "route":    Cmd(desc="Force a model tier",                 args="<tier>",      handler=engine.route),
}
```

### 5.2 The decisive distinction: Tools vs Prompts

- Want the **user** to type `/name`? → expose it as an MCP **Prompt**.
- Want the **model** to call it on its own? → expose it as an MCP **Tool**.

Same handlers, two entry points. Generate from the registry: an MCP prompt def **and** a
Claude Code `.md` command file per entry, plus the autocomplete table for your own CLI.

### 5.3 Host adapters

| Surface | How commands appear | Built by |
|---|---|---|
| Any MCP host | MCP prompts | Your `FastMCP` server |
| Claude Code | `.claude/commands/*.md` and/or `/mcp__ctx__<cmd>` | `init/claude_code.py` |
| Your CLI / TUI | Native `/` autocomplete | Your client |
| Non-MCP tool | Proxy parses `/text` | Mode B proxy |

---

## 6. Repo layout

```
ctx/                       the GitHub repo / pip package
├── pyproject.toml         entry point:  ctx = ctx.cli:main
├── ctx/
│   ├── cli.py             subcommands: init · index · serve · doctor
│   ├── registry.py        single source of truth for all commands
│   ├── server.py          MCP server (FastMCP): prompts + tools + resources
│   ├── proxy.py           Mode B: FastAPI localhost proxy
│   ├── init/
│   │   ├── claude_code.py   writes .mcp.json + .claude/commands/*.md
│   │   ├── cowork.py        emits the connector config
│   │   └── generic.py       stdio command + proxy-intercept fallback
│   ├── engine/
│   │   ├── index.py        tree-sitter + symbol graph -> SQLite
│   │   ├── assembler.py    layered, cache-friendly prompt build
│   │   ├── budget.py       real-tokenizer measurement + prefix cache
│   │   ├── memory.py       verbatim window + decision log (+ ollama compress)
│   │   ├── docs.py         markitdown conversion + hash cache
│   │   ├── patch.py        diff parse + fuzzy apply
│   │   └── router.py       frontier / cheap-cloud / local tiers
│   └── commands/           handlers: map · focus · explain · diff · compress · cost · route
└── tests/evals/            the held-out task suite (the guardrail)
```

---

## 7. Tech stack

| Concern | Tools |
|---|---|
| Proxy / serving | FastAPI, uvicorn, httpx |
| MCP / CLI | Python MCP SDK (`FastMCP`), `pipx`/`uv` (or npm) |
| Code index | tree-sitter (`py-tree-sitter` + grammars), `networkx`, `ripgrep` |
| Storage / state | SQLite (`sqlite3`, optional `sqlite-vec`), `hashlib`, `watchdog` |
| Documents | `markitdown` |
| Local compression | `ollama` + `qwen2.5:1.5b` / `llama3.2:3b` |
| Budgeting | `tiktoken` + provider token-counting |
| Patching | `difflib` |
| Providers | Anthropic / OpenAI SDKs with caching enabled |

---

## 8. Build order (by return on effort)

1. **Prompt caching + diff-only output.** The free lunch and the biggest output saver — buildable
   in a day against the SDKs, a huge win before anything else exists.
2. **Tree-sitter index + graph-ranked repo map.** Where the bulk of input savings and "system
   intelligence" come from.
3. **Agentic tools + output truncation + context GC.** Turns a single-shot prompter into a real
   agent loop.
4. **Command registry + `FastMCP` server (prompts + tools).** At this point `/` already works in
   Claude Code via `claude mcp add`.
5. **`ctx init` + per-host adapters + `ctx doctor`.**
6. **Packaging + `pipx`/PyPI release.**
7. **Memory store, document pipeline, model router.** Polishes long-session behaviour and trims
   cost.

Wire a thin **eval harness** in from day one (§2.5) — it runs the whole way through, not at the
end.

---

## 9. The guardrail (eval harness)

A held-out set of representative coding/chat tasks with automatic success checks. Track per
request: tokens in/out (measured), cache-hit rate, latency, cost, model used — and, above all,
**task success rate**. Run it on every change to the engine. Token reduction is only "saving
intelligence" if success rate holds.

---

## 10. References & docs

- **Aider** — the closest existing implementation of this design; study its repo-map ranking and
  diff-edit formats.
- **Anthropic prompt caching & API** — verify current caching behaviour, pricing, models:
  https://docs.claude.com/en/api/overview · docs map: https://docs.claude.com/en/docs_site_map.md
- **Claude Code** (MCP integration, `.mcp.json`, custom commands, install):
  https://docs.claude.com/en/docs/claude-code/overview · docs map:
  https://docs.anthropic.com/en/docs/claude-code/claude_code_docs_map.md · npm:
  https://www.npmjs.com/package/@anthropic-ai/claude-code
- **Claude.ai** (plans, connectors): https://support.claude.com
- **Model Context Protocol** — the open standard for tools/prompts/resources across hosts.

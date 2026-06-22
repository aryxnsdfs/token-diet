# Installing token-diet

`token-diet` ships the `ctx` command. Install it once, then wire it into
whichever AI tool you use. The core runs with **zero heavy dependencies** via
graceful fallbacks; install extras to upgrade each piece. `ctx doctor` always
shows what's active.

---

## 1. Install the `ctx` command

Pick one. `pipx` is recommended — it isolates the tool on your PATH.

```bash
# from PyPI (recommended)
pipx install token-diet

# full feature set (MCP server + proxy + tree-sitter + tiktoken + doc pipeline)
pipx install 'token-diet[all]'

# straight from GitHub (no PyPI needed)
pipx install 'git+https://github.com/aryxnsdfs/token-diet'

# plain pip / virtualenv
pip install token-diet            # or:  pip install 'token-diet[all]'

# from source (development)
git clone https://github.com/aryxnsdfs/token-diet
cd token-diet
pip install -e '.[all,dev]'
```

Verify:

```bash
ctx --help
```

---

## 2. Set up a project (once per repo)

```bash
cd your-project
ctx init          # builds the index, registers with the detected host
ctx doctor        # confirms wiring + shows active optional deps
```

`ctx init` writes:

| File | Purpose |
|---|---|
| `.mcp.json` | Claude Code launches `ctx serve` from this |
| `.claude/commands/*.md` | native slash commands (belt-and-suspenders) |
| `.ctx/cowork.connector.json` | connector config for Cowork / Claude.ai |
| `.ctx/generic.setup.json` | stdio + proxy hints for any other tool |
| `.ctx/` | engine state — index, config, decision log, cache (gitignored) |

Commit `.mcp.json` and `.claude/commands/` to share the setup with teammates;
`.ctx/` is regenerated and stays gitignored.

---

## 3. Wire it into your AI tool

### Claude Code (terminal + IDE) — native MCP

Nothing else to do. After `ctx init`, open Claude Code in the project; it reads
`.mcp.json`, spawns `ctx serve`, and your commands appear under `/`.

```
/init        warm the chat: build map, enable diff-only output
/map         inject the graph-ranked repo map
/focus FILE  pin a file or symbol
/explain SYM pull one symbol's body
/cost        token + cache telemetry
```

Manual registration (if you skipped `ctx init`):

```bash
claude mcp add ctx -- ctx serve
```

### Claude Cowork / Claude.ai — connector

Web apps can't spawn a local process, so run the server reachable over HTTP and
paste the connector config:

```bash
ctx serve --http          # exposes the MCP server over HTTP/SSE
```

Open `.ctx/cowork.connector.json`, copy it into the app's **Connectors /
Custom MCP** settings. Commands then appear under `/` just like Claude Code.

### Codex / any tool with a custom base URL — proxy

Point the client's API base URL at the local proxy; it optimizes every request
transparently.

```bash
ctx proxy --port 8000
# then set the client base URL to:  http://127.0.0.1:8000
export ANTHROPIC_API_KEY=sk-...        # or OPENAI_API_KEY
```

No `/` autocomplete on these tools — type `/map`, `/focus auth.py`, etc. as the
message and the proxy parses it.

### Any other MCP host — stdio

```bash
ctx serve
```

Register with the host's MCP config using command `ctx`, args `["serve"]`. See
`.ctx/generic.setup.json` for the exact snippet.

---

## 4. Optional dependencies

Everything below has a fallback; install to upgrade quality. `ctx doctor` lists
current status.

| Extra | Enables | Fallback when absent |
|---|---|---|
| `[index]` | tree-sitter parse, file watcher | regex symbol parser |
| `[tokenize]` | exact `tiktoken` counts | ~4 chars/token estimate |
| `[mcp]` | MCP server (`ctx serve`) | proxy-only (Mode B) |
| `[proxy]` | FastAPI local proxy | MCP-only (Mode A) |
| `[docs]` | PDF/Word/Excel → Markdown | text passthrough |
| `[providers]` | Anthropic/OpenAI SDKs | proxy forwards raw HTTP |

```bash
pipx inject token-diet 'token-diet[index,tokenize]'   # add extras later
```

---

## 5. Troubleshooting

```bash
ctx doctor        # the first thing to run — checks wiring + deps
ctx index         # force a full re-index
```

- **`/` shows nothing in Claude Code** → confirm `.mcp.json` exists; restart the
  host; check `ctx doctor`.
- **`ctx: command not found`** → `pipx ensurepath` then reopen the shell.
- **Proxy returns "no upstream API key"** → set `ANTHROPIC_API_KEY` /
  `OPENAI_API_KEY` in the environment running `ctx proxy`.

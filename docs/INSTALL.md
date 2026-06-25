# How to install token-diet

token-diet gives you a command called `ctx`. Install it once, then use it in
any project with any AI coding tool.

---

## Step 1 — Install

Pick whichever works for you:

```bash
# From GitHub (works right now)
pip install 'git+https://github.com/aryxnsdfs/token-diet'

# With all features (better code scanning, exact token counting, doc support)
pip install 'token-diet[all]'

# For development (if you want to modify it)
git clone https://github.com/aryxnsdfs/token-diet
cd token-diet
pip install -e '.[all,dev]'
```

Check it worked:

```bash
ctx --help
```

---

## Step 2 — Set up your project (once per project)

```bash
cd your-project       # go to the project you want to use it on
ctx init              # scans your code, creates config files
ctx doctor            # checks everything is working
```

That's it. `ctx init` creates everything it needs automatically.

---

## Step 3 — Connect to your AI tool

### Claude Code (easiest — works out of the box)

After `ctx init`, just open Claude Code in your project folder. It connects
automatically. Press `/` and you'll see the commands.

```
/start          Start a session (scan code, show overview, turn on smart mode)
/showrepo       Show your whole project structure
/openfile FILE  Open a specific file in the chat
/findcode FUNC  Find a specific function or class
/showstats      See how many tokens you saved
```

If it doesn't auto-connect, run this manually:

```bash
claude mcp add ctx -- ctx serve
```

### Claude.ai / Cowork (web browser)

The web app can't run local programs, so you start the server yourself:

```bash
ctx serve --http          # starts the server on your machine
```

Then in the app, go to **Settings → Connectors → Add Custom MCP** and paste
the config from `.ctx/cowork.connector.json`.

### Codex / Cursor / any other tool

Run the local proxy — it sits between your tool and the AI:

```bash
ctx proxy --port 8000

# Set your API key
export ANTHROPIC_API_KEY=sk-...        # or OPENAI_API_KEY
```

Then change your tool's **API base URL** to `http://127.0.0.1:8000`.

Type `/showrepo`, `/openfile auth.py`, etc. as regular messages — the proxy
understands them.

---

## Optional extras

Everything works without these, but they make it better. Run `ctx doctor` to
see what you have.

| Install with | What it adds | Without it |
|---|---|---|
| `[index]` | Better code scanning (tree-sitter) | Basic regex scanning (still works) |
| `[tokenize]` | Exact token counting | Rough estimate (~4 chars = 1 token) |
| `[mcp]` | Slash commands in Claude Code | Proxy-only mode |
| `[proxy]` | Local proxy for any tool | Slash-commands-only mode |
| `[docs]` | Convert PDFs, Word, Excel to text | Text files only |

```bash
pip install 'token-diet[index,tokenize]'     # add specific extras
pip install 'token-diet[all]'                 # add everything
```

---

## Something not working?

```bash
ctx doctor            # always run this first — it checks everything
ctx index             # force rescan your code
```

| Problem | Fix |
|---|---|
| `/` shows nothing in Claude Code | Make sure `.mcp.json` exists in your project. Restart Claude Code. Run `ctx doctor`. |
| `ctx: command not found` | Run `pip install token-diet` again, then restart your terminal. |
| Proxy says "no API key" | Set `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` in your terminal before running `ctx proxy`. |

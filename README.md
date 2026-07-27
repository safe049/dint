# dint

**dint** is a Socratic AI tutor that teaches through guided discovery. Instead of giving you answers, dint asks the right questions to help you build understanding from the ground up.

## Features

- **Socratic dialogue** — dint never just tells you the answer; it leads you there with questions
- **Adaptive skill tracking** — estimates your proficiency per concept and adjusts difficulty
- **Knowledge graph** — builds a map of concepts and their relationships as you learn
- **Long-term memory** — remembers your preferences, misconceptions, and learning goals across sessions
- **Web search** — looks up current information when needed
- **Session management** — multiple conversation threads, each with its own progress

## Quick Start

### Option A — pip install (recommended)

```bash
# 1. Install from the project root
pip install .

# 2. Configure your API key
cp .env.example .env
# Edit .env — add your OpenAI / Anthropic / other provider key

# 3. Run
dint
```

### Option B — run.sh (dev mode with auto-reload)

```bash
chmod +x run.sh
./run.sh
```

### Option C — uv

```bash
uv sync
uv run dint
```

The app will be available at **http://localhost:7070**.

### CLI options

```
dint [--host HOST] [--port PORT] [--no-reload]
```

| Flag | Description | Default |
|------|-------------|---------|
| `--host` | Bind address | `0.0.0.0` |
| `--port` | Port to listen on | `7070` |
| `--no-reload` | Disable auto-reload (production) | *(reload on)* |

## Architecture

```
dint/
├── src/dint/
│   ├── app.py          # FastAPI application + REST routes
│   ├── agent.py        # Tool-calling orchestration loop
│   ├── llm.py          # LLM provider (OpenAI-compatible)
│   ├── tools.py        # Tool definitions & executors
│   ├── reflection.py   # Post-turn analysis (skills, memory, KG)
│   ├── persona.py      # System prompt / teaching persona
│   ├── db.py           # SQLite persistence layer
│   ├── config.py       # Environment configuration
│   ├── cli.py          # CLI entry point (argparse + uvicorn)
│   └── frontend/       # Bundled static assets
│       ├── index.html  # SPA shell
│       ├── style.css   # Dark-theme UI
│       └── app.js      # Client logic
├── frontend/           # Source frontend (dev copy)
├── run.sh              # One-command launcher (dev)
├── pyproject.toml      # Package metadata & dependencies
└── .env.example        # Config template
```

## How It Works

1. You send a message via the web UI.
2. The agent assembles context: persona, relevant memories, skill estimates, and knowledge subgraph.
3. The LLM responds, optionally calling tools (web search, memory recall, skill lookup, KG query).
4. After the reply, the **reflection engine** analyses the exchange:
   - Updates skill confidence scores
   - Extracts new knowledge-graph nodes/edges
   - Stores durable memories (preferences, corrections, goals)
5. The UI renders the reply and updates the side panels.

## Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `OPENAI_API_KEY` | API key for your provider | *(required)* |
| `OPENAI_BASE_URL` | OpenAI-compatible endpoint | `https://api.openai.com/v1` |
| `DINT_MODEL` | Model name | `gpt-4o-mini` |
| `DATABASE_URL` | SQLite database path | `dint.db` |
| `SERPER_API_KEY` | Serper.dev key for web search | *(optional)* |

## Requirements

- Python 3.11+
- An OpenAI-compatible API key (OpenAI, Anthropic via proxy, Ollama, etc.)
- *(Optional)* A [Serper.dev](https://serper.dev) key for web search

## License

MIT
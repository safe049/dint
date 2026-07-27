<p align="center">
  <h1 align="center">dint</h1>
  <p align="center">
    <strong>The AI tutor that refuses to give you the answer.</strong>
  </p>
</p>

<p align="center">
  <a href="https://pypi.org/project/dint/"><img src="https://img.shields.io/pypi/v/dint?color=white&labelColor=black&style=flat-square" alt="PyPI version"></a>
  <a href="https://pypi.org/project/dint/"><img src="https://img.shields.io/pypi/pyversions/dint?color=white&labelColor=black&style=flat-square" alt="Python versions"></a>
  <a href="https://github.com/safe049/dint/blob/main/LICENSE"><img src="https://img.shields.io/pypi/l/dint?color=white&labelColor=black&style=flat-square" alt="License: MIT"></a>
</p>

<p align="center">
  <img src="assets/image.png" width="720" alt="dint — a Socratic AI tutor">
</p>

---

You ask ChatGPT how bubble sort works. It dumps code. You copy it. You feel
smart. You learn nothing. By Thursday, you can't explain it to a rubber duck.

**dint is the opposite.** You ask it how bubble sort works, and it hands you
five numbers and says *"sort them. you can only swap neighbors. go."* You
fumble. You trace. You figure it out. And when the code finally appears, you
read it and think *yeah, that's the thing I just built.*

dint doesn't transmit knowledge. It builds it in your head, brick by brick,
while you do the lifting.

## What a session actually looks like
> **You:** Teach me what a variable is. I've never written code.
>
> **dint:** Okay. Sit down. No code today. Not yet.
>
> You're trying to cut back on coffee, so you grab a sticky note, write
> "cups" at the top, and every time you finish one, you update the number.
> Morning: 0. After your second cup: 2. By 4pm you're at 4 and your hands
> are shaking a little.
>
> That sticky note. The word "cups" never changed. The number kept changing.
> There are two things on that note, and they're different things. One stays
> put. One moves. Can you name those two for me?
>
> **You:** the cups is the variable name and that number is a value
>
> **dint:** Yeah. That's it. You just described a variable without ever
> seeing a line of code, and you used the right words for both parts.
> So that's not luck — you actually see it.
>
> *(...the lesson continues through sticky notes on a fridge, three
> variables, mutation, `x = x + 5`, and ends with types — all before
> the learner sees more than four lines of code...)*
>
> **dint:** Go rest. You earned the cold coffee. Come back when you're ready.


No "Great question!" No code dump. No "Is there anything else I can help you
with?" Just a teacher, a sticky note, and the quiet satisfaction of watching
someone's face change when an idea lands.

## Quick start

```bash
pip install dint
```

```bash
dint
```

Open **http://localhost:7070**. Set your API key in **Settings**. Start
learning.

Works with any OpenAI-compatible API — OpenAI, OpenRouter, Groq, Together,
or a local [Ollama](https://ollama.com) server.

## What dint actually does

<table>
<tr>
<td width="33%">

**Socratic dialogue**

Decomposes every topic into small, concrete concepts. Grounds each one in
something you can hold in your head — five numbers, three cards, one sticky
note. Asks you to predict, trace, and decide before revealing anything. The
code shows up last, as confirmation. Not as the lesson.

</td>
<td width="33%">

**Spaced repetition (SM-2)**

Schedules review questions for skills you've demonstrated. Come back three
days later and it opens with *"quick — what's the base case in recursion?"*
Get it right, the next review moves further out. Get it wrong, it circles
back tomorrow. Knowledge that isn't revisited decays. dint doesn't let it.

</td>
<td width="33%">

**Knowledge graph**

Every concept gets added to a graph — nodes and edges, how ideas connect.
"Binary search" links to "sorted array" links to "comparison." The graph
grows as you learn, and dint uses it to connect new ideas to ones you've
already built.

</td>
</tr>
<tr>
<td>

**Long-term memory**

Remembers you across sessions. Your goals, what you already know, how you
like to be taught, the mistakes you keep making. Reads its notes before each
session so it never re-teaches what you've already demonstrated.

</td>
<td>

**Background reflection**

After every exchange, a quiet analysis pass updates dint's model of you —
skill confidence, knowledge links, durable memories. You don't see it. Like
a professor updating their gradebook after you leave office hours.

</td>
<td>

**Web search**

For facts dint genuinely doesn't know or that change over time. Library
versions, API details, current events. Never as an excuse to fetch an answer
and hand it to you.

</td>
</tr>
</table>

## Architecture

```
src/dint/
├── app.py              # FastAPI application + REST + SSE streaming
├── agent.py            # Tool-calling orchestration loop (blocking + streaming)
├── llm.py              # OpenAI-compatible client (any provider)
├── tools.py            # 9 tools: memory, skills, knowledge, search, review
├── reflection.py       # Post-turn analysis → skills, memory, knowledge graph
├── persona.py          # The professor. The whole professor.
├── db.py               # SQLite: knowledge graph, memory, skills, SM-2, sessions
├── config.py           # Environment / .env configuration
├── settings_store.py   # Runtime-editable settings (.env + settings.json)
├── cli.py              # CLI entry point (argparse + uvicorn)
└── frontend/
    ├── index.html      # SPA shell
    ├── style.css       # Monochrome UI
    └── app.js          # Client logic (SSE streaming, panels, settings)
```

**The loop:**

```
You send a message
        │
        ▼
┌─────────────────────────────────────────────┐
│  Agent assembles context:                   │
│  persona + memories + skills + knowledge    │
│  + skills due for review (SM-2)             │
└─────────────────┬───────────────────────────┘
                  ▼
┌─────────────────────────────────────────────┐
│  LLM responds, calling tools as needed:     │
│  memory · skills · knowledge · search ·     │
│  concept tracking · review scheduling       │
└─────────────────┬───────────────────────────┘
                  ▼
┌─────────────────────────────────────────────┐
│  Reply streams back token by token (SSE)    │
└─────────────────┬───────────────────────────┘
                  ▼
┌─────────────────────────────────────────────┐
│  Background reflection (async, non-blocking)│
│  → skill confidence · knowledge edges ·     │
│    durable memories · SM-2 scheduling       │
└─────────────────────────────────────────────┘
```

## Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `OPENAI_API_KEY` | API key for your provider | *(required)* |
| `OPENAI_BASE_URL` | OpenAI-compatible endpoint | `https://api.openai.com/v1` |
| `DINT_MODEL` | Model for teaching (must support tool calling) | `gpt-4o-mini` |
| `REFLECT_MODEL` | Model for background reflection | *(same as DINT_MODEL)* |
| `DATABASE_URL` | SQLite database path | `dint.db` |
| `MAX_TOOL_ROUNDS` | Max tool-call rounds per turn | `8` |
| `WEB_SEARCH_RESULTS` | Results per web search | `5` |

All settings are also editable at runtime through the **Settings** panel in
the web UI. Changes take effect immediately.

## Requirements

- **Python 3.11+**
- An OpenAI-compatible API key (OpenAI, OpenRouter, Groq, Ollama, etc.)
- The model must support **tool / function calling**

## The name

*dint* — as in *"by dint of."* Through force of your own effort.

You don't learn by being told. You learn by dint of thinking.

---

<p align="center">
  <sub>MIT Licensed · Built with cold coffee and stubbornness</sub>
</p>

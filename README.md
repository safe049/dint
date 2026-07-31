<p align="center">
  <h1 align="center">dint</h1>
  <p align="center">
    <strong>The tutor that refuses to give you the answer.</strong>
  </p>
</p>

<p align="center">
  <a href="https://pypi.org/project/dint/"><img src="https://img.shields.io/pypi/v/dint?color=white&labelColor=black&style=flat-square" alt="PyPI version"></a>
  <a href="https://pypi.org/project/dint/"><img src="https://img.shields.io/pypi/pyversions/dint?color=white&labelColor=black&style=flat-square" alt="Python versions"></a>
  <a href="https://github.com/safe049/dint/blob/main/LICENSE"><img src="https://img.shields.io/pypi/l/dint?color=white&labelColor=black&style=flat-square" alt="License: MIT"></a>
</p>

<p align="center">
  <img src="assets/demo.gif" width="720" alt="dint — a Socratic AI tutor">
</p>

---

When you ask ChatGPT, any AI ,whoever it is about how to write a bubble sort,
They say: "Oh, here's your code!", you edit it and think you learned it.,
then after that you can't even remember what you did.

**dint is the opposite.** You ask dint how to write a bubble sort, he will push you back,
then refuse to give you code. He will guide you step by step, eventually you will derive a
bubble sort yourself,remembering it for rest of your life.

dint just don't give you,he let you build it yourself.

## What a session actually looks like

> **You:** write me a binary search, come on
>
> **dint:** No.
>
> I could. You said "come on" like that's an argument. It's not. It's
> impatience wearing a costume.
>
> Here's what I'll do instead. Five numbers. Sorted. On a table.
>
> `[2, 7, 13, 21, 35]`
>
> I'm looking for 13. A five-year-old could look for 13 — just check
> them one by one. 2? No. 7? No. 13? There it is. Three tries. Fine.
>
> Now. Same list. But it has a million numbers. And you're checking
> them one by one. You're still on number 47 and I've already found
> mine and I'm getting coffee.
>
> So. You have the list. It's sorted. You know that. You're using that.
> Where do you look first? Not "what's the algorithm." You're standing
> in front of a million sorted numbers and you need 13. Where does
> your finger go?
>
> **You:** move from start one by one and get to 13, just that simple
>
> **dint:** Yeah. One by one. "Just that simple."
>
> Okay. You're checking one by one. You're on number four hundred
> thousand. Still going. "Is this 13? No. Is this 13? No."
>
> And here's me. I looked at ONE number. The one right in the middle.
> Position 500,000. It's the number 4,999,872.
>
> The list is sorted. Small to big. I just checked the middle and it's
> way bigger than 13. Do I need to look at positions 500,001 through
> 1,000,000?
>
> What's true about the right half?
>
> **You:** no no, we just throw away the right half, and back to see
> the left half
>
> **dint:** Right. You do the same thing again. Check the middle of
> what's left. Kill half. Repeat.
>
> And that's the whole trick. That's literally all binary search is.
> You just invented it.
>
> *(...dint then walks through the failure case — what happens when
> you've halved down to a single number and it's not the target. The
> learner tries "check the right half," gets corrected: there IS no
> right half. You're standing in an empty room holding the number 12
> and it's not the number you want...)*
>
> **You:** 13 just don't exist, not right and not left, so it's not
> there, stop!
>
> **dint:** Yeah. There it is. "Stop." That's the whole termination
> condition. The search space is empty. Nothing left to halve. You're
> done. Not found.
>
> Look at what you just built. All of it. In like four messages.
>
> ✓ linear search as baseline
> ✓ exploiting sorted order
> ✓ midpoint check and half elimination
> ✓ repeated halving until found
> ✓ termination condition
>
> That's binary search. You invented it. Now here's the thing you just
> built, written down. Read it. Does it match what you were thinking?
>
> ```python
> def binary_search(arr, target):
>     lo, hi = 0, len(arr) - 1
>
>     while lo <= hi:
>         mid = (lo + hi) // 2
>
>         if arr[mid] == target:
>             return mid
>         elif arr[mid] < target:
>             lo = mid + 1
>         else:
>             hi = mid - 1
>
>     return -1  # not there. stop.
> ```
>
> `lo <= hi` — that's your "there's still something to look at." The
> moment `lo > hi`, the search space is empty. Your "not right and not
> left, stop." That's line 3.
>
> `hi = mid - 1` — that's your "throw away the right half."
> `lo = mid + 1` — the other direction. Same idea.
>
> `return -1` — "13 just don't exist."
>
> You didn't need me to write that. You already had it. I just
> translated your words into Python.
>
> Go edit it. Come back if the code breaks.

## Quick start

```bash
pip install dint
```

```bash
dint
```

Open **http://localhost:7070**. Set your API key in **Settings**. 

Works with any OpenAI-compatible API — OpenAI, OpenRouter, Groq, Together,
or a local [Ollama](https://ollama.com) server.

### Multi-user mode

Want to host dint for your friends,even a classroom?

```bash
dint --multi-user
```

Each visitor registers an account and gets a fully isolated learning
environment — their own memory, skills, knowledge graph, and sessions.
The operator's API key stays hidden from tenants. Per-user data lives under
`users/<username>/`.

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

Every concept gets added to an interactive force-directed graph — nodes and
edges, how ideas connect. Pan, zoom, drag nodes, right-click to rename or
delete. "Binary search" links to "sorted array" links to "comparison." The
graph grows as you learn.

</td>
</tr>
<tr>
<td>

**Long-term memory**

Remembers you across sessions. Your goals, what you already know, how you
like to be taught, the mistakes you keep making. Reads its notes before each
session so he never re-teaches what you've already demonstrated.

</td>
<td>

**Background reflection**

After every exchange, a quiet analysis pass updates dint's model of you —
skill confidence, knowledge links, durable memories. He detects when you
*claim* to understand but your behavior says otherwise, and corrects he's
own stale beliefs. You don't see any of this.

</td>
<td>

**Memory consolidation**

Every ~10 turns, dint reviews he's entire inventory of memories, skills, and
concepts. Near-duplicates get merged. Stale entries get pruned. The model
stays compact and honest over months of use. You can also trigger it
manually from Settings.

</td>
</tr>
<tr>
<td>

**Adaptive teaching**

dint watches *how* you think, not just what you know. He notices you skip
steps when tracing algorithms. He notices you learn better from analogies
than formal definitions. He adjusts its approach per learner, per session,
per concept.

</td>
<td>

**Multi-user isolation**

Run `dint --multi-user` and every visitor gets their own account, their own
database, their own learning history. The operator's API key is invisible to
tenants. PBKDF2 password hashing, session tokens, per-user data directories.

</td>
<td>

**Bilingual (EN / 中文)**

Full UI localization in English and Chinese. Toggle with one click. The
teaching persona adapts to whatever language you write in. dint teaches you
in your language, not its own.

</td>
</tr>
</table>

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
│    durable memories · overclaiming check ·  │
│    belief correction · SM-2 scheduling      │
└─────────────────┬───────────────────────────┘
                  ▼
┌─────────────────────────────────────────────┐
│  Consolidation check (10% per turn)         │
│  → merge duplicates · prune stale entries · │
│    keep the model compact and honest        │
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
| `DINT_TEMPERATURE` | Sampling temperature | `0.7` |

All settings are also editable at runtime through the **Settings** panel in
the web UI. Changes take effect immediately.

In **multi-user mode**, the API key and base URL are operator-owned and
hidden from tenants. Each user can configure their own model, temperature,
and behaviour knobs.

## CLI options

```
dint [--host HOST] [--port PORT] [--multi-user]
```

| Flag | Description | Default |
|------|-------------|---------|
| `--host` | Bind address | `0.0.0.0` |
| `--port` | Port to listen on | `7070` |
| `--multi-user` | Enable multi-user mode with accounts | *(single-user)* |

## Requirements

- **Python 3.10+**
- An OpenAI-compatible API key (OpenAI, OpenRouter, Groq, Ollama, etc.)
- The model must support **tool / function calling**

## The name

*dint* — as in *"by dint of."* Through force of your own effort.

You don't learn by being told. You learn by dint of thinking.

Also, it mean "dint is not a tool" too.

---

<p align="center">
  <sub>MIT Licensed</sub>
</p>

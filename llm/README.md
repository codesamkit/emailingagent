# LLM backends

One place that decides which model each pipeline stage talks to. Before this,
four call sites each constructed `anthropic.Anthropic()` directly, so changing
provider meant editing four files.

## Why this exists

The pipeline's call volume is lopsided, and the gate is what makes it so:

| Stage | Calls per 100 emails | Judgment needed |
|---|---|---|
| score | 100 | moderate |
| summarize | 100 | low |
| classify (LLM fallback) | ~21 | low — rules handle the rest |
| **outline** | **~8** | **high** |

221 calls of grunt work; 8 that need real reasoning. Those two groups don't
have to run on the same model.

## The hybrid split

Grunt work local and free, outlines hosted where quality shows — **config
only, no code change**:

```bash
export LLM_PROVIDER=ollama
export OLLAMA_MODEL=gemma2:2b
export LLM_PROVIDER_OUTLINE=anthropic     # the ~8 calls that matter most

python -m llm.cli routing                 # confirm the split
python -m pipeline.cli process
```

```
default provider: ollama
  classify   ollama     gemma2:2b
  score      ollama     gemma2:2b
  summarize  ollama     gemma2:2b
  outline    anthropic  claude-opus-5
```

## Decide with evidence, not reputation

Whether a small model is good enough for *these* prompts on *your* emails is
an empirical question. Run both and look:

```bash
python -m llm.cli compare --stage summarize -n 5
python -m llm.cli compare --stage score -n 10
python -m llm.cli compare --stage classify -n 10
```

### What to look for

**Summarize / classify** — read them. Are the facts right? Is the ask captured?

**Score — check the spread, not the values.** A small model's failure mode
here isn't wrong answers, it's *clustered* ones. Fifty emails all scored 70
each look individually reasonable and make the ranking worthless, which is the
entire point of the score. The comparison prints this and flags it:

```
ollama   spread: min=68 max=72 mean=70.1 stdev=0.9 distinct=3/10
         ^ CLUSTERED — plausible values, useless ranking
```

Good separation looks like `stdev=22.4 distinct=9/10`.

## Setup

```bash
# On the machine running the model
ollama serve
ollama pull gemma2:2b          # ~1.6 GB
```

Remote host (a desktop with a real GPU):

```bash
# On that machine — without this it binds to loopback and refuses you
OLLAMA_HOST=0.0.0.0 ollama serve

# Here
export OLLAMA_HOST=http://<its-ip>:11434
python -m llm.cli check
```

`OLLAMA_HOST` is just a URL, so localhost, a LAN address, and a Tailscale
address are identical to this code. **Remote desktop is not enough** — you
need HTTP reachability to port 11434, not a view of the screen.

`python -m llm.cli check` distinguishes "server down" from "model not pulled",
because those have different fixes and both otherwise surface as an empty
reply.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `LLM_PROVIDER` | `anthropic` | default backend (`anthropic` / `ollama`) |
| `LLM_PROVIDER_<STAGE>` | — | override one stage, e.g. `LLM_PROVIDER_OUTLINE` |
| `LLM_MODEL_<STAGE>` | — | override one stage's model, e.g. `LLM_MODEL_SCORE` |
| `OLLAMA_HOST` | `http://localhost:11434` | any reachable URL |
| `OLLAMA_MODEL` | `llama3.1:8b` | local model id |
| `OLLAMA_TIMEOUT` | `300` | seconds — generous, so a cold model load isn't read as failure |
| `ANTHROPIC_MODEL` | `claude-opus-5` | hosted model id |

Stages: `classify`, `score`, `summarize`, `outline`.

## How the adapter works

`llm/ollama.py` mimics the slice of the Anthropic SDK this repo actually uses
— `client.messages.create(...)` returning an object whose `.content` is a list
of blocks with `.type` and `.text`. Every call site already consumes exactly
that, so no prompt code changed.

Ollama's `format` parameter takes a JSON Schema and constrains decoding to
match, so Track B's structured outputs work unchanged against a local model.
That removes the usual reason small models are unreliable — malformed output
is not possible, only wrong content is.

Anthropic-only kwargs (`thinking`, `betas`, `effort`) are accepted and ignored
rather than raising, so a call site using one degrades instead of crashing.

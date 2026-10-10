# Architecture decision records — `full-stack-serving`

Decisions this stack forced, and the reasoning behind them. The value of this directory is not
that decisions were made — every stack makes them — but that the reasoning is written down where
a reviewer can check it.

## Numbering is per-project

`adr/0001` in this repository is **not** the same decision as `0001` in any other project's ADR
directory, and numbering here is only meaningful here. Always qualify a reference with the
project — *"full-stack-serving ADR 0002"* — never a bare "ADR 0002".

## Status values

| Status | Meaning |
|---|---|
| `proposed` | Drafted, not yet acted on. May be edited freely. |
| `accepted` | In force. **Context and Decision are frozen.** |
| `superseded` | Replaced by a later ADR; say which one. |
| `rejected` | Considered and declined; keep it, the reasoning is the value. |
| `deprecated` | No longer relevant, not replaced. |

## Rules

- **Do not edit the Context or Decision of an `accepted` ADR.** To change a decision, write a new
  ADR that supersedes it and set the old one's status to `superseded by NNNN`.
- **Do not delete or renumber an ADR.** A rejected or superseded record is history.
- Status changes on an accepted ADR are allowed only for `superseded` / `deprecated`.
- One decision per record. If it needs the word "and" in the title, it is probably two ADRs.

## When to write one

When a choice is load-bearing and someone could reasonably have chosen otherwise. Examples
already anticipated for this project:

- vLLM over SGLang / TensorRT-LLM / TGI as the engine.
- llm-d over NVIDIA Dynamo for the orchestration layer.
- Arize Phoenix over Langfuse for observability.
- `k3s` over `kind` or a full distribution.

Do **not** write one for choices with only one reasonable answer, or for things upstream docs
already decide.

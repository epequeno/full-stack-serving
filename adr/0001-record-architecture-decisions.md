# 0001 — Record architecture decisions

- **Status:** accepted
- **Date:** 2026-10-09

## Context

This project is a stack of choices: an inference engine, an orchestrator, a routing layer, an
autoscaling mechanism, an observability backend, a Kubernetes distribution. Upstream projects
support multiple options and the upstream documentation walks through all of them, because it
cannot know which the reader wants.

Following a quickstart means inheriting upstream's decision to *not decide*. The result reads as
"this person can install things", which is not the claim this project is trying to make.

The claim is that the choices were made deliberately, with reasons, and that the person making
them understands the alternatives.

## Decision

Record load-bearing architectural decisions as ADRs in this directory, numbered sequentially from
0001, following the status rules in `adr/README.md`.

A decision is load-bearing if someone informed could reasonably have chosen otherwise and the
choice constrains later work. Purely mechanical or single-option choices are not recorded.

Context and Decision are frozen once accepted. Changing a decision means writing a new ADR that
supersedes the old one, not editing history.

## Consequences

- Reviewer-visible reasoning, which is the actual deliverable of a portfolio project.
- A small, continuous writing cost.
- The `adr/` directory becomes a place where alternatives are named even when rejected — so a
  reviewer can see the option set, not just the pick.
- Numbering accumulates across the project's lifetime and is meaningless outside it, so all
  references must be project-qualified.

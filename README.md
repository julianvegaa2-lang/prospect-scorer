# Prospect Scorer

Give it a company's GitHub org, get back a quick read on whether they look like a fit for Warp Factories.

v0 just pulls basic public stats. The scoring, AI tool detection and batch mode get built by the factory (see `TICKETS.md`).

## Setup

```bash
pip install -r requirements.txt
export GITHUB_TOKEN=your_token   # optional, raises rate limit from 60 to 5,000/hr
python prospect.py vercel
```

## What it shows today

- Public repo count and org followers
- Repos pushed in the last 90 days
- Stars on those active repos
- Top languages across active repos
- Active contributors: unique all-time contributors across the org's 10 most recently pushed public repos
- PRs opened last 30d (any state) across those same repos
- AI coding tool signals: how many of those same repos contain `CLAUDE.md`, `AGENTS.md`, `.cursor/` or `.cursorrules`, `.github/copilot-instructions.md`, or `.warp/` at the root of the default branch. Missing files are not errors.
- Fit score: every report ends with `Fit: X/10 (reason)`. The score rises with recent PRs, contributor breadth, and AI coding tool files; org popularity is not scored. Public data is partial, so a low score with no evidence says "not enough public data" and is not proof of a bad fit. The rules and weights live in `fit_score` in `prospect.py`.

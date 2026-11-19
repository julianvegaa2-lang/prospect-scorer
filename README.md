# Prospect Scorer

Give it a company's GitHub org, get back a quick read on whether they look like a fit for Warp Factories.

v0 pulls public stats and scores fit. Remaining tickets are in `TICKETS.md`.

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
- Non-archived repos: public, non-archived repos of the org, forks included (GitHub issue search cannot exclude forks, so the count cannot either). Counted org-wide with GitHub search.
- PRs opened last 30d (any state), counted org-wide with GitHub search over exactly the same repos as the line above. The window starts at the exact UTC time 30 days before the run.
- Active contributors: unique all-time contributors across the 10 most recently pushed non-fork, non-archived repos (a sample, not the whole org)
- AI coding tool signals: how many of those same sampled repos contain `CLAUDE.md`, `AGENTS.md`, `.cursor/` or `.cursorrules`, `.github/copilot-instructions.md`, or `.warp/` at the root of the default branch. Missing files are not errors.
- Fit score: every report ends with `Fit: X/10 (reason)`. The rules and weights live in `fit_score` in `prospect.py`.

## How the fit score works

The score is 1 plus up to 9 points of evidence. Org popularity (followers, stars, total repo count) is not scored.

- **Recent PRs (up to 4 points)** are judged relative to org size. The points are the lower of two readings: PR volume (1, 5, 20, 50 PRs in 30d) and PRs per non-archived repo in 30d (0.05, 0.2, 0.5, 1.0). A 1,000-repo org needs 1,000 PRs in 30d for the top tier, and 50 PRs spread over 1,000 repos earn 1 point instead of 4. A small org with a few PRs is still judged by the volume reading.
- **Contributor breadth (up to 2 points)** counts only when recent PRs score. All-time contributors alone do not show an active team.
- **AI signal categories (up to 3 points)**: one category is 2 points, two or more is 3. A tool-neutral file such as `AGENTS.md` is one category, not a specific tool.
- A score above 7 needs points in all three dimensions. Strong-fit wording ("active engineering org with AI signals") appears only at 8 or more.
- An org with low PR activity for its size says "low recent PR activity relative to org size". An org with no PRs and no contributors says "not enough public data to judge", which is not proof of a bad fit.

## Data limits

- Only public repos are visible. Private engineering work is not counted.
- PRs and non-archived repos are org-wide. Contributors and AI signals sample the 10 most recently pushed non-fork, non-archived repos (the repo list is paged until 10 are found), so they can miss repos outside the sample.
- GitHub search can report incomplete results. Each query is retried up to 3 times. If it stays incomplete, or if the org has more than 4,000 public repos (beyond what GitHub issue search covers), the counts cannot be trusted. The report prints a warning, the score is capped at 5, and the reason starts with "provisional". It never uses strong-fit wording.
- Search is rate limited (10 requests/min without a token, 30 with one). A report uses two search calls, or up to six when GitHub reports incomplete results.

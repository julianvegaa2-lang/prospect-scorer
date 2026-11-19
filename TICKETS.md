# Starter tickets for the factory

Paste these one at a time into your intake (Linear or GitHub Issues). Run them roughly in order.

---

## 1. Add contributor and PR activity
Pull two more signals for the org's top 10 most recently pushed repos:
- Unique contributors (from the contributors endpoint)
- Pull requests opened in the last 30 days

**Done when:** the report shows "Active contributors" and "PRs opened last 30d", and a repo with no PRs doesn't crash it.

---

## 2. Detect AI coding tool signals
Check the top 10 active repos for files that show the team already uses AI coding agents: `CLAUDE.md`, `AGENTS.md`, `.cursor/` or `.cursorrules`, `.github/copilot-instructions.md`, `.warp/`.

**Done when:** the report lists which signals were found and in how many repos. Missing files are not errors.

---

## 3. Fit score
Turn the stats into a 1 to 10 "Factories fit" score with a one-line reason. A strong fit is an active engineering org (lots of recent PRs and contributors) that is already using AI coding tools.

**Done when:** every report ends with `Fit: X/10 (reason)`, and the scoring rules live in one function with comments explaining the weights.

---

## 4. Batch mode
Accept a CSV with a column `github_org` and output a ranked list (highest fit first) to the terminal and to `output/ranked.csv`.

**Done when:** `python prospect.py --csv companies.csv` works on a 10-row file and skips bad rows with a warning instead of stopping.

---

## 5. Edge cases
Handle: org not found, rate limit hit (show when it resets), a user account instead of an org, and orgs with 1,000+ repos.

**Done when:** each case prints a clear message and nothing throws a stack trace.

---

## 6. Scores feel off for big companies (leave this one vague on purpose)
The scores feel too high for big companies that have lots of repos but not much real activity. Fix it.

*(This one tests whether triage asks you clarifying questions before building.)*

---

# Scorer for the factory

Hand-rank 10 companies you know (1 = best fit). After each run that touches scoring, run batch mode on the same 10 and compare.

**Pass:** the tool's top 3 includes at least 2 of your top 3, and nothing you ranked bottom 3 lands in its top 3.

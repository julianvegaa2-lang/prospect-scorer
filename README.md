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

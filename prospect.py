#!/usr/bin/env python3
"""Prospect scorer v0: pull basic public GitHub stats for a company's org.

Usage:
    python prospect.py <github-org>
    python prospect.py vercel

Set GITHUB_TOKEN to raise the rate limit from 60 to 5,000 requests/hour.
"""

import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

import requests

API = "https://api.github.com"
PAGE_SIZE = 100
TOP_REPOS = 10
PR_WINDOW_DAYS = 30
SAMPLE_MAX_PAGES = 10
# A score with unreliable counts stays mid-range: never strong-fit, never bottom.
PROVISIONAL_MAX_SCORE = 5
AI_SIGNALS = {
    "CLAUDE.md": [("file", "CLAUDE.md")],
    "AGENTS.md": [("file", "AGENTS.md")],
    "Cursor": [("dir", ".cursor"), ("file", ".cursorrules")],
    "Copilot instructions": [("file", ".github/copilot-instructions.md")],
    "Warp": [("dir", ".warp")],
}


def session():
    s = requests.Session()
    s.headers["Accept"] = "application/vnd.github+json"
    s.headers["User-Agent"] = "prospect-scorer"
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        s.headers["Authorization"] = f"Bearer {token}"
    return s


def get_org(s, org):
    r = s.get(f"{API}/orgs/{org}", timeout=15)
    r.raise_for_status()
    return r.json()


def get_recent_repos(s, org, limit=100):
    """Most recently pushed public repos (one page, up to 100)."""
    r = s.get(
        f"{API}/orgs/{org}/repos",
        params={"sort": "pushed", "direction": "desc", "per_page": limit, "type": "public"},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def get_contributor_ids(s, repo):
    """Account IDs of all contributors to a repo (all-time, all pages)."""
    ids = set()
    page = 1
    while True:
        r = s.get(
            f"{API}/repos/{repo['full_name']}/contributors",
            params={"per_page": PAGE_SIZE, "page": page},
            timeout=15,
        )
        r.raise_for_status()
        contributors = r.json() if r.status_code != 204 else []
        ids.update(c["id"] for c in contributors if c.get("id") is not None)
        if len(contributors) < PAGE_SIZE:
            return ids
        page += 1


SEARCH_ATTEMPTS = 3
SEARCH_REPO_SCOPE_LIMIT = 4000


def search_total(s, path, query):
    """(total matches, complete) for a GitHub search query.

    Retries when GitHub reports incomplete results; complete is False if every
    attempt was incomplete, in which case the total can be too low.
    """
    for _ in range(SEARCH_ATTEMPTS):
        r = s.get(f"{API}/search/{path}", params={"q": query, "per_page": 1}, timeout=15)
        r.raise_for_status()
        body = r.json()
        if not body.get("incomplete_results"):
            return body["total_count"], True
    return body["total_count"], False


def get_org_activity(s, org, cutoff, org_public_repos):
    """Org-wide recent PRs and in-scope repo count, over the same repos.

    Scope is every public, non-archived repo of the org, forks included: issue
    search cannot exclude forks, so the repo count cannot either. `count_limits`
    lists reasons the counts cannot be trusted.
    """
    since = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")
    limits = []
    prs, prs_complete = search_total(
        s, "issues", f"org:{org} is:pr is:public archived:false created:>={since}")
    repos, repos_complete = search_total(
        s, "repositories", f"org:{org} is:public archived:false")
    if not prs_complete:
        limits.append("PR search stayed incomplete, so the PR count may be low")
    if not repos_complete:
        limits.append("repo search stayed incomplete, so the repo count may be low")
    if org_public_repos > SEARCH_REPO_SCOPE_LIMIT:
        limits.append(f"org has more than {SEARCH_REPO_SCOPE_LIMIT} public repos, "
                      "beyond what GitHub issue search covers")
    return {"prs_opened_30d": prs, "scoped_repos": repos, "count_limits": limits}


def get_activity(s, repos):
    """Unique all-time contributors across the top repos by recent push."""
    contributors = set()
    for repo in repos[:TOP_REPOS]:
        contributors |= get_contributor_ids(s, repo)
    return {"active_contributors": len(contributors)}


def get_sample_repos(s, org):
    """The TOP_REPOS most recently pushed public repos that are not forks or archived."""
    sample = []
    for page in range(1, SAMPLE_MAX_PAGES + 1):
        r = s.get(
            f"{API}/orgs/{org}/repos",
            params={"sort": "pushed", "direction": "desc", "per_page": PAGE_SIZE,
                    "page": page, "type": "public"},
            timeout=15,
        )
        r.raise_for_status()
        repos = r.json()
        sample += [x for x in repos if not x.get("fork") and not x.get("archived")]
        if len(sample) >= TOP_REPOS or len(repos) < PAGE_SIZE:
            break
    return sample[:TOP_REPOS]


def list_entry_types(s, repo, path=""):
    """Map of entry name to type for a directory on the default branch.

    A 404 means the directory is absent (or the repo is empty) and yields {}.
    """
    r = s.get(f"{API}/repos/{repo['full_name']}/contents/{path}", timeout=15)
    if r.status_code == 404:
        return {}
    r.raise_for_status()
    entries = r.json()
    if not isinstance(entries, list):
        return {}
    return {e["name"]: e["type"] for e in entries}


def has_signal_path(s, repo, root, kind, path):
    parent, _, name = path.rpartition("/")
    if parent:
        if root.get(parent) != "dir":
            return False
        entries = list_entry_types(s, repo, parent)
    else:
        entries = root
    return entries.get(name) == kind


def get_ai_signals(s, repos):
    """Per-signal repo counts, and the distinct top repos with any signal."""
    top = repos[:TOP_REPOS]
    counts = {name: 0 for name in AI_SIGNALS}
    with_signal = 0
    for repo in top:
        root = list_entry_types(s, repo)
        found = False
        for name, paths in AI_SIGNALS.items():
            if any(has_signal_path(s, repo, root, kind, p)
                   for kind, p in paths):
                counts[name] += 1
                found = True
        with_signal += found
    return {"repos_checked": len(top), "repos_with_signal": with_signal,
            "signals": counts}


def parse_time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def summarize(org_info, repos, activity=None, ai_signals=None):
    cutoff = datetime.now(timezone.utc) - timedelta(days=90)
    active = [
        r for r in repos
        if r.get("pushed_at")
        and datetime.fromisoformat(r["pushed_at"].replace("Z", "+00:00")) > cutoff
    ]
    languages = Counter(r["language"] for r in active if r.get("language"))
    activity = activity or {}
    ai_signals = ai_signals or {"repos_checked": 0, "repos_with_signal": 0,
                                "signals": {}}
    return {
        "name": org_info.get("name") or org_info["login"],
        "login": org_info["login"],
        "public_repos": org_info.get("public_repos", 0),
        "followers": org_info.get("followers", 0),
        "repos_pushed_90d": len(active),
        "stars_on_active": sum(r.get("stargazers_count", 0) for r in active),
        "top_languages": languages.most_common(5),
        "active_contributors": activity.get("active_contributors", 0),
        "prs_opened_30d": activity.get("prs_opened_30d", 0),
        "scoped_repos": activity.get("scoped_repos", 0),
        "count_limits": activity.get("count_limits", []),
        "ai_signals": ai_signals,
    }


def fit_score(sm):
    """Return (score from 1 to 10, one-line reason) for Warp Factories fit.

    All scoring rules live here. Public data is only a partial view: a private
    engineering team and missing AI files are not proof of no fit, so a score
    without public activity evidence says "not enough public data".
    """
    prs = sm["prs_opened_30d"]
    contributors = sm["active_contributors"]
    repos = max(sm["scoped_repos"], 1)
    prs_per_repo = prs / repos
    ai = sm["ai_signals"]
    ai_repos = ai["repos_with_signal"]
    ai_coverage = ai_repos / max(ai["repos_checked"], 1)

    # Weights: 1 (floor) + up to 9 points of evidence. Each tier list holds
    # (minimum value, points), highest tier first. Org popularity (followers,
    # stars, total repo count) is deliberately not scored.
    #
    # Recent PRs weigh the most: they show a team shipping code now. Points are
    # the lower of two readings: PR volume (a one-repo org with one PR is not
    # a team) and PRs per in-scope repo in 30d (a huge org with a trickle of
    # PRs is mostly dormant). The top density tier is for orgs where every
    # repo sees heavy review traffic.
    pr_tiers = [(200, 5), (100, 4), (50, 3), (10, 2), (1, 1)]
    density_tiers = [(8.0, 5), (4.0, 4), (1.5, 3), (0.5, 2), (0.2, 1)]
    # Contributor breadth shows a team, not a solo maintainer. It is all-time,
    # so it counts only when recent PRs score.
    contributor_tiers = [(20, 1)]
    # AI points follow how many of the sampled repos carry any AI signal file,
    # not how many file conventions exist: adoption across repos shows a
    # practice, one repo shows an experiment.
    ai_tiers = [(0.3, 3), (0.2, 2), (0.01, 1)]

    def points(value, tiers):
        return next((p for minimum, p in tiers if value >= minimum), 0)

    pr_points = min(points(prs, pr_tiers), points(prs_per_repo, density_tiers))
    contributor_points = points(contributors, contributor_tiers) if pr_points else 0
    ai_points = points(ai_coverage, ai_tiers)
    score = 1 + pr_points + contributor_points + ai_points

    # A strong fit needs evidence in all three dimensions. Without it the score
    # is capped at 7 so strong-fit language is never used on partial evidence.
    strong = bool(pr_points and contributor_points and ai_points)
    if not strong:
        score = min(score, 7)

    facts = (f"{prs} PRs in 30d across {sm['scoped_repos']} non-archived "
             f"repos ({prs_per_repo:.2f} per repo), {contributors} contributors, "
             f"AI signals in {ai_repos} of {ai['repos_checked']} sampled repos")
    # Sufficiency depends on activity evidence only: AI files alone cannot show
    # an active engineering org.
    if sm["count_limits"]:
        # Undercounted PRs or repos skew the density, so no definitive score.
        score = min(score, PROVISIONAL_MAX_SCORE)
        return score, f"provisional, counts not reliable: {'; '.join(sm['count_limits'])} ({facts})"
    if not prs and not contributors:
        return score, f"not enough public data to judge ({facts})"
    if not pr_points:
        return score, f"low recent PR activity relative to org size ({facts})"
    if strong and score >= 8:
        return score, f"active engineering org with AI signals ({facts})"
    return score, facts


def print_report(sm):
    print(f"\n{sm['name']} (github.com/{sm['login']})")
    print("-" * 40)
    print(f"Public repos:            {sm['public_repos']}")
    print(f"Org followers:           {sm['followers']}")
    print(f"Repos pushed last 90d:   {sm['repos_pushed_90d']}")
    print(f"Stars on active repos:   {sm['stars_on_active']}")
    print(f"Active contributors:     {sm['active_contributors']}")
    print(f"Non-archived repos:      {sm['scoped_repos']}")
    print(f"PRs opened last 30d:     {sm['prs_opened_30d']}")
    langs = ", ".join(f"{l} ({n})" for l, n in sm["top_languages"]) or "n/a"
    print(f"Top languages (active):  {langs}")
    ai = sm["ai_signals"]
    found = [(n, c) for n, c in ai["signals"].items() if c]
    print(f"AI coding tool signals (top {ai['repos_checked']} repos):")
    if found:
        for name, count in found:
            print(f"  {name}: {count} of {ai['repos_checked']} repos")
    else:
        print("  none found")
    print(f"  Repos with any signal: {ai['repos_with_signal']} of {ai['repos_checked']}")
    print("Data limits: PRs and non-archived repos (public, forks included) are")
    print(f"  org-wide; contributors and AI signals sample the {TOP_REPOS} most recently")
    print("  pushed non-fork, non-archived repos. Private repos are not visible.")
    for limit in sm["count_limits"]:
        print(f"  WARNING: {limit}.")
    score, reason = fit_score(sm)
    print()
    print(f"Fit: {score}/10 ({reason})")


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    org = sys.argv[1]
    s = session()
    try:
        org_info = get_org(s, org)
        repos = get_recent_repos(s, org)
        sample = get_sample_repos(s, org)
        pr_cutoff = datetime.now(timezone.utc) - timedelta(days=PR_WINDOW_DAYS)
        activity = {**get_activity(s, sample),
                    **get_org_activity(s, org, pr_cutoff,
                                       org_info.get("public_repos", 0))}
        ai_signals = get_ai_signals(s, sample)
    except requests.HTTPError as e:
        print(f"GitHub error for '{org}': {e}")
        sys.exit(2)
    print_report(summarize(org_info, repos, activity, ai_signals))


if __name__ == "__main__":
    main()

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


def count_recent_prs(s, repo, cutoff):
    """PRs in any state created at or after cutoff, newest first, all pages needed."""
    count = 0
    page = 1
    while True:
        r = s.get(
            f"{API}/repos/{repo['full_name']}/pulls",
            params={
                "state": "all",
                "sort": "created",
                "direction": "desc",
                "per_page": PAGE_SIZE,
                "page": page,
            },
            timeout=15,
        )
        r.raise_for_status()
        pulls = r.json()
        for pr in pulls:
            if parse_time(pr["created_at"]) < cutoff:
                return count
            count += 1
        if len(pulls) < PAGE_SIZE:
            return count
        page += 1


def get_activity(s, repos, cutoff):
    """Unique contributors and recent PRs across the top repos by recent push."""
    top = repos[:TOP_REPOS]
    contributors = set()
    prs = 0
    for repo in top:
        contributors |= get_contributor_ids(s, repo)
        prs += count_recent_prs(s, repo, cutoff)
    return {"active_contributors": len(contributors), "prs_opened_30d": prs}


def parse_time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def summarize(org_info, repos, activity=None):
    cutoff = datetime.now(timezone.utc) - timedelta(days=90)
    active = [
        r for r in repos
        if r.get("pushed_at")
        and datetime.fromisoformat(r["pushed_at"].replace("Z", "+00:00")) > cutoff
    ]
    languages = Counter(r["language"] for r in active if r.get("language"))
    activity = activity or {}
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
    }


def print_report(sm):
    print(f"\n{sm['name']} (github.com/{sm['login']})")
    print("-" * 40)
    print(f"Public repos:            {sm['public_repos']}")
    print(f"Org followers:           {sm['followers']}")
    print(f"Repos pushed last 90d:   {sm['repos_pushed_90d']}")
    print(f"Stars on active repos:   {sm['stars_on_active']}")
    print(f"Active contributors:     {sm['active_contributors']}")
    print(f"PRs opened last 30d:     {sm['prs_opened_30d']}")
    langs = ", ".join(f"{l} ({n})" for l, n in sm["top_languages"]) or "n/a"
    print(f"Top languages (active):  {langs}\n")


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    org = sys.argv[1]
    s = session()
    try:
        org_info = get_org(s, org)
        repos = get_recent_repos(s, org)
        pr_cutoff = datetime.now(timezone.utc) - timedelta(days=PR_WINDOW_DAYS)
        activity = get_activity(s, repos, pr_cutoff)
    except requests.HTTPError as e:
        print(f"GitHub error for '{org}': {e}")
        sys.exit(2)
    print_report(summarize(org_info, repos, activity))


if __name__ == "__main__":
    main()

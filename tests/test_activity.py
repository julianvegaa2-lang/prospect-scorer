import io
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from unittest import mock

import prospect

NOW = datetime(2026, 1, 31, tzinfo=timezone.utc)
CUTOFF = NOW - timedelta(days=30)


def resp(data, status=200):
    r = mock.Mock(status_code=status)
    r.json.return_value = data
    return r


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def repo(name):
    return {"full_name": f"org/{name}"}


def fake_session(contributors=None, pulls=None):
    """Route GETs by URL; per-page lists are keyed by repo name."""
    contributors = contributors or {}
    pulls = pulls or {}
    s = mock.Mock()

    def get(url, params=None, timeout=None):
        name = url.split("/")[-2]
        kind = url.split("/")[-1]
        pages = (contributors if kind == "contributors" else pulls).get(name, [resp([])])
        page = params["page"]
        return pages[page - 1] if page <= len(pages) else resp([])

    s.get.side_effect = get
    return s


class ContributorTests(unittest.TestCase):
    def test_unions_ids_across_pages_and_repos(self):
        page1 = resp([{"id": i} for i in range(100)])
        page2 = resp([{"id": 100}, {"id": 5}])
        s = fake_session(contributors={
            "a": [page1, page2],
            "b": [resp([{"id": 5}, {"id": 999}])],
        })
        out = prospect.get_activity(s, [repo("a"), repo("b")], CUTOFF)
        self.assertEqual(out["active_contributors"], 102)

    def test_empty_and_204_contributors(self):
        s = fake_session(contributors={
            "a": [resp([])],
            "b": [resp(None, status=204)],
        })
        out = prospect.get_activity(s, [repo("a"), repo("b")], CUTOFF)
        self.assertEqual(out, {"active_contributors": 0, "prs_opened_30d": 0})

    def test_only_first_ten_repos_used(self):
        repos = [repo(f"r{i}") for i in range(12)]
        contributors = {f"r{i}": [resp([{"id": i}])] for i in range(12)}
        out = prospect.get_activity(fake_session(contributors), repos, CUTOFF)
        self.assertEqual(out["active_contributors"], 10)

    def test_zero_repos(self):
        out = prospect.get_activity(fake_session(), [], CUTOFF)
        self.assertEqual(out, {"active_contributors": 0, "prs_opened_30d": 0})


class PullRequestTests(unittest.TestCase):
    def pr(self, delta):
        return {"created_at": iso(CUTOFF + delta), "state": "closed"}

    def test_no_prs(self):
        s = fake_session(pulls={"a": [resp([])]})
        self.assertEqual(prospect.count_recent_prs(s, repo("a"), CUTOFF), 0)

    def test_cutoff_boundary_and_stops_at_older(self):
        s = fake_session(pulls={"a": [resp([
            self.pr(timedelta(days=1)),
            self.pr(timedelta(0)),
            self.pr(timedelta(seconds=-1)),
            self.pr(timedelta(days=-5)),
        ])]})
        self.assertEqual(prospect.count_recent_prs(s, repo("a"), CUTOFF), 2)

    def test_paginates_until_older_and_requests_all_states(self):
        new = self.pr(timedelta(days=1))
        old = self.pr(timedelta(days=-1))
        s = fake_session(pulls={"a": [
            resp([new] * 100),
            resp([new] * 3 + [old]),
            resp([new] * 100),
        ]})
        self.assertEqual(prospect.count_recent_prs(s, repo("a"), CUTOFF), 103)
        self.assertEqual(s.get.call_count, 2)
        params = s.get.call_args.kwargs["params"]
        self.assertEqual(params["state"], "all")
        self.assertEqual(params["sort"], "created")
        self.assertEqual(params["direction"], "desc")
        self.assertEqual(params["per_page"], 100)

    def test_exhausted_pages(self):
        new = self.pr(timedelta(days=1))
        s = fake_session(pulls={"a": [resp([new] * 100), resp([new] * 2)]})
        self.assertEqual(prospect.count_recent_prs(s, repo("a"), CUTOFF), 102)


class SummaryAndReportTests(unittest.TestCase):
    ORG = {"login": "org", "public_repos": 3, "followers": 7}
    REPOS = [
        {"pushed_at": iso(datetime.now(timezone.utc)), "language": "Go", "stargazers_count": 4},
        {"pushed_at": "2001-01-01T00:00:00Z", "language": "C", "stargazers_count": 9},
    ]

    def test_existing_metrics_unchanged(self):
        sm = prospect.summarize(self.ORG, self.REPOS, {"active_contributors": 3, "prs_opened_30d": 8})
        self.assertEqual(sm["repos_pushed_90d"], 1)
        self.assertEqual(sm["stars_on_active"], 4)
        self.assertEqual(sm["top_languages"], [("Go", 1)])
        self.assertEqual(sm["active_contributors"], 3)
        self.assertEqual(sm["prs_opened_30d"], 8)

    def test_report_labels(self):
        sm = prospect.summarize(self.ORG, self.REPOS, {"active_contributors": 3, "prs_opened_30d": 0})
        buf = io.StringIO()
        with redirect_stdout(buf):
            prospect.print_report(sm)
        out = buf.getvalue()
        self.assertIn("Active contributors:     3", out)
        self.assertIn("PRs opened last 30d:     0", out)


if __name__ == "__main__":
    unittest.main()

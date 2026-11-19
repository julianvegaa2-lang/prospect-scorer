import io
import json
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import prospect

NOW = datetime(2026, 1, 31, tzinfo=timezone.utc)
CUTOFF = NOW - timedelta(days=30)
FIXTURES = Path(__file__).parent / "fixtures"


def resp(data, status=200):
    r = mock.Mock(status_code=status)
    r.json.return_value = data
    return r


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def repo(name):
    return {"full_name": f"org/{name}"}


def fake_session(contributors=None):
    """Route GETs by URL; per-page lists are keyed by repo name."""
    contributors = contributors or {}
    s = mock.Mock()

    def get(url, params=None, timeout=None):
        name = url.split("/")[-2]
        pages = contributors.get(name, [resp([])])
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
        out = prospect.get_activity(s, [repo("a"), repo("b")])
        self.assertEqual(out["active_contributors"], 102)

    def test_empty_and_204_contributors(self):
        s = fake_session(contributors={
            "a": [resp([])],
            "b": [resp(None, status=204)],
        })
        out = prospect.get_activity(s, [repo("a"), repo("b")])
        self.assertEqual(out, {"active_contributors": 0})

    def test_only_first_ten_repos_used(self):
        repos = [repo(f"r{i}") for i in range(12)]
        contributors = {f"r{i}": [resp([{"id": i}])] for i in range(12)}
        out = prospect.get_activity(fake_session(contributors), repos)
        self.assertEqual(out["active_contributors"], 10)

    def test_zero_repos(self):
        out = prospect.get_activity(fake_session(), [])
        self.assertEqual(out, {"active_contributors": 0})


def search_session(prs, repos, incomplete=()):
    """Answer the org-wide search calls with fixed totals.

    `incomplete` names the endpoints ("issues", "repositories") that report
    incomplete results on every attempt.
    """
    s = mock.Mock()
    totals = {"issues": prs, "repositories": repos}

    def get(url, params=None, timeout=None):
        kind = url.rsplit("/", 1)[1]
        return resp({"total_count": totals[kind],
                     "incomplete_results": kind in incomplete})

    s.get.side_effect = get
    return s


def queries(s):
    return {c.args[0].rsplit("/", 1)[1]: c.kwargs["params"]["q"]
            for c in s.get.call_args_list}


class OrgActivityTests(unittest.TestCase):
    def test_counts_prs_and_repos_over_the_same_population(self):
        s = search_session(prs=42, repos=7)
        out = prospect.get_org_activity(s, "acme", CUTOFF, 7)
        self.assertEqual(out, {"prs_opened_30d": 42, "scoped_repos": 7,
                               "count_limits": []})
        q = queries(s)
        self.assertEqual(q["issues"], "org:acme is:pr is:public archived:false "
                                      "created:>=2026-01-01T00:00:00Z")
        self.assertEqual(q["repositories"], "org:acme is:public archived:false")

    def test_cutoff_keeps_time_of_day(self):
        s = search_session(1, 1)
        cutoff = datetime(2026, 1, 1, 13, 45, 7, tzinfo=timezone.utc)
        prospect.get_org_activity(s, "acme", cutoff, 1)
        self.assertIn("created:>=2026-01-01T13:45:07Z", queries(s)["issues"])

    def test_incomplete_search_is_retried_then_recovers(self):
        s = mock.Mock()
        s.get.side_effect = [
            resp({"total_count": 3, "incomplete_results": True}),
            resp({"total_count": 9, "incomplete_results": False}),
            resp({"total_count": 4, "incomplete_results": False}),
        ]
        out = prospect.get_org_activity(s, "acme", CUTOFF, 4)
        self.assertEqual((out["prs_opened_30d"], out["scoped_repos"]), (9, 4))
        self.assertEqual(out["count_limits"], [])

    def test_persistent_incomplete_search_is_reported_per_endpoint(self):
        for kind, text in (("issues", "PR search"), ("repositories", "repo search")):
            out = prospect.get_org_activity(
                search_session(5, 5, incomplete=(kind,)), "acme", CUTOFF, 5)
            self.assertEqual(len(out["count_limits"]), 1, kind)
            self.assertIn(text, out["count_limits"][0])

    def test_org_beyond_search_scope_is_reported(self):
        out = prospect.get_org_activity(search_session(5, 5), "acme", CUTOFF, 4001)
        self.assertEqual(len(out["count_limits"]), 1)
        self.assertIn("4000", out["count_limits"][0])
        ok = prospect.get_org_activity(search_session(5, 5), "acme", CUTOFF, 4000)
        self.assertEqual(ok["count_limits"], [])


def repos_page(names, **flags):
    return resp([dict({"full_name": f"org/{n}"}, **flags) for n in names])


class SampleReposTests(unittest.TestCase):
    def session(self, pages):
        s = mock.Mock()
        s.get.side_effect = lambda url, params=None, timeout=None: (
            pages[params["page"] - 1] if params["page"] <= len(pages) else resp([]))
        return s

    def test_skips_forks_and_archives_across_pages(self):
        page1 = resp([{"full_name": f"org/f{i}", "fork": True} for i in range(100)])
        page2 = resp([{"full_name": "org/old", "archived": True}]
                     + [{"full_name": f"org/r{i}"} for i in range(12)])
        s = self.session([page1, page2])
        out = prospect.get_sample_repos(s, "org")
        self.assertEqual([r["full_name"] for r in out], [f"org/r{i}" for i in range(10)])
        self.assertEqual(s.get.call_count, 2)

    def test_stops_when_org_has_fewer_than_ten(self):
        s = self.session([repos_page(["a", "b"])])
        self.assertEqual(len(prospect.get_sample_repos(s, "org")), 2)
        self.assertEqual(s.get.call_count, 1)

    def test_page_cap_bounds_requests(self):
        forks = resp([{"full_name": "org/f", "fork": True}] * 100)
        s = self.session([forks] * 50)
        self.assertEqual(prospect.get_sample_repos(s, "org"), [])
        self.assertEqual(s.get.call_count, prospect.SAMPLE_MAX_PAGES)


class SummaryAndReportTests(unittest.TestCase):
    ORG = {"login": "org", "public_repos": 3, "followers": 7}
    REPOS = [
        {"pushed_at": iso(datetime.now(timezone.utc)), "language": "Go", "stargazers_count": 4},
        {"pushed_at": "2001-01-01T00:00:00Z", "language": "C", "stargazers_count": 9},
    ]

    def test_existing_metrics_unchanged(self):
        sm = prospect.summarize(self.ORG, self.REPOS, {"active_contributors": 3, "prs_opened_30d": 8, "scoped_repos": 4})
        self.assertEqual(sm["repos_pushed_90d"], 1)
        self.assertEqual(sm["stars_on_active"], 4)
        self.assertEqual(sm["top_languages"], [("Go", 1)])
        self.assertEqual(sm["active_contributors"], 3)
        self.assertEqual(sm["prs_opened_30d"], 8)
        self.assertEqual(sm["scoped_repos"], 4)

    def test_report_labels(self):
        sm = prospect.summarize(self.ORG, self.REPOS, {"active_contributors": 3, "prs_opened_30d": 0})
        buf = io.StringIO()
        with redirect_stdout(buf):
            prospect.print_report(sm)
        out = buf.getvalue()
        self.assertIn("Active contributors:     3", out)
        self.assertIn("PRs opened last 30d:     0", out)
        self.assertIn("Non-archived repos:      0", out)
        self.assertIn("Data limits:", out)
        self.assertNotIn("WARNING", out)

    def test_report_warns_on_unreliable_counts(self):
        sm = prospect.summarize(self.ORG, self.REPOS, {"count_limits": ["PR search stayed incomplete"]})
        buf = io.StringIO()
        with redirect_stdout(buf):
            prospect.print_report(sm)
        self.assertIn("WARNING: PR search stayed incomplete.", buf.getvalue())


def stats(prs=0, contributors=0, ai_repos=0, repos=1, limits=None):
    return {
        "prs_opened_30d": prs,
        "active_contributors": contributors,
        "scoped_repos": repos,
        "count_limits": limits or [],
        "ai_signals": {"repos_checked": 10, "repos_with_signal": ai_repos,
                       "signals": {"AGENTS.md": ai_repos}},
    }


class FitScoreTests(unittest.TestCase):
    def score(self, **kw):
        return prospect.fit_score(stats(**kw))[0]

    def test_no_data_is_lowest_score_with_not_enough_data_reason(self):
        score, reason = prospect.fit_score(stats())
        self.assertEqual(score, 1)
        self.assertIn("not enough public data", reason)

    def test_strong_fit_is_ten(self):
        score, reason = prospect.fit_score(
            stats(prs=500, contributors=80, ai_repos=3, repos=50))
        self.assertEqual(score, 10)
        self.assertIn("active engineering org with AI signals", reason)

    def test_missing_dimension_is_not_strong_fit(self):
        for kw in (dict(prs=200, contributors=1, ai_repos=3),
                   dict(prs=0, contributors=80, ai_repos=3),
                   dict(prs=200, contributors=80, ai_repos=0)):
            score, reason = prospect.fit_score(stats(**kw))
            self.assertLessEqual(score, 7, kw)
            self.assertNotIn("active engineering org", reason, kw)

    def test_one_ai_repo_without_activity_is_not_enough_data(self):
        score, reason = prospect.fit_score(stats(ai_repos=1))
        self.assertEqual(score, 2)
        self.assertIn("not enough public data", reason)

    def test_any_activity_evidence_is_enough_data(self):
        for kw in (dict(prs=1), dict(contributors=5)):
            self.assertNotIn("not enough public data",
                             prospect.fit_score(stats(**kw))[1])

    def test_reason_reports_ai_repo_coverage(self):
        reason = prospect.fit_score(stats(prs=5, ai_repos=2))[1]
        self.assertIn("AI signals in 2 of 10 sampled repos", reason)

    def test_pr_volume_tier_boundaries(self):
        self.assertEqual([self.score(prs=n) for n in (0, 1, 9, 10, 49, 50, 99, 100, 199, 200)],
                         [1, 2, 2, 3, 3, 4, 4, 5, 5, 6])

    def test_contributor_tier_boundaries(self):
        self.assertEqual([self.score(prs=5, contributors=n) for n in (19, 20)],
                         [2, 3])

    def test_contributors_without_recent_prs_score_nothing(self):
        self.assertEqual(self.score(contributors=500), 1)

    def test_ai_coverage_tier_boundaries(self):
        self.assertEqual([self.score(prs=5, ai_repos=n) for n in range(0, 5)],
                         [2, 3, 4, 5, 5])

    def test_missing_contributors_cap_is_seven_without_strong_wording(self):
        score, reason = prospect.fit_score(stats(prs=200, contributors=1, ai_repos=3))
        self.assertEqual(score, 7)
        self.assertNotIn("active engineering org", reason)

    def test_ai_points_use_sample_percentage_not_repo_count(self):
        warp = dict(prs=543, contributors=191, repos=52)
        cases = [(1, 10, 8), (1, 5, 9), (1, 2, 10), (0, 0, 7)]
        for with_signal, checked, expected in cases:
            sm = stats(**warp)
            sm["ai_signals"] = {"repos_checked": checked,
                                "repos_with_signal": with_signal, "signals": {}}
            self.assertEqual(prospect.fit_score(sm)[0], expected,
                             (with_signal, checked))

    def test_ai_points_follow_repo_coverage_not_category_count(self):
        one = prospect.fit_score(stats(prs=5, ai_repos=1))
        ten = prospect.fit_score(stats(prs=5, ai_repos=10))
        self.assertLess(one[0], ten[0])

    def test_large_sparse_org_scores_low_without_strong_wording(self):
        for prs in (0, 5, 40):
            score, reason = prospect.fit_score(
                stats(prs=prs, contributors=80, ai_repos=3, repos=1000))
            self.assertLessEqual(score, 4, prs)
            self.assertIn("low recent PR activity relative to org size", reason)
            self.assertNotIn("active engineering org", reason)
            self.assertIn("across 1000 non-archived repos", reason)

    def test_density_separates_orgs_with_equal_volume(self):
        densities = (0.3, 1.0, 2.0, 4.0, 20.0)
        scores = [self.score(prs=int(d * 1000), contributors=80, ai_repos=3, repos=1000)
                  for d in densities]
        self.assertEqual(scores, [6, 7, 8, 9, 10])

    def test_density_tier_boundaries(self):
        self.assertEqual([self.score(prs=200, repos=n) for n in (25, 26, 50, 100, 400, 1000, 1001)],
                         [6, 5, 5, 4, 3, 2, 1])

    def test_unreliable_counts_withhold_definitive_score(self):
        for repos in (10, 1000):
            score, reason = prospect.fit_score(stats(
                prs=50, contributors=30, ai_repos=3, repos=repos,
                limits=["repo search stayed incomplete, so the repo count may be low"]))
            self.assertLessEqual(score, prospect.PROVISIONAL_MAX_SCORE)
            self.assertIn("provisional", reason)
            self.assertNotIn("active engineering org", reason)

    def test_zero_scoped_repos_does_not_divide_by_zero(self):
        self.assertIn(self.score(prs=5, repos=0), range(1, 11))

    def test_score_never_decreases_with_more_evidence(self):
        for field in ("prs", "contributors", "ai_repos"):
            scores = [self.score(**{field: n}) for n in range(0, 100)
                      if field != "ai_repos" or n <= 10]
            self.assertEqual(scores, sorted(scores), field)

    def test_popularity_does_not_change_score(self):
        sm = stats(prs=5)
        big = dict(sm, followers=100000, stars_on_active=100000, public_repos=900)
        self.assertEqual(prospect.fit_score(big), prospect.fit_score(sm))

    def test_scores_stay_within_one_to_ten(self):
        for prs in (0, 10, 10000):
            for ai_repos in (0, 10):
                self.assertIn(self.score(prs=prs, contributors=500, ai_repos=ai_repos),
                              range(1, 11))

    def test_frozen_live_orgs_spread_with_warp_first(self):
        scores = {}
        for org in ("warpdotdev", "uber", "netflix", "dropbox", "yahoo"):
            with open(FIXTURES / f"{org}.json") as f:
                scores[org] = prospect.fit_score(json.load(f))[0]
        self.assertEqual(scores["warpdotdev"], 10)
        self.assertEqual([o for o, s in scores.items() if s == 10], ["warpdotdev"])
        self.assertEqual(max(scores, key=scores.get), "warpdotdev")
        self.assertGreaterEqual(max(scores.values()) - min(scores.values()), 5)
        self.assertIn(scores["yahoo"], (4, 5))

    def test_report_ends_with_fit_line(self):
        sm = prospect.summarize(self.ORG, [], {"active_contributors": 0})
        buf = io.StringIO()
        with redirect_stdout(buf):
            prospect.print_report(sm)
        last = buf.getvalue().rstrip("\n").splitlines()[-1]
        self.assertRegex(last, r"^Fit: \d+/10 \(not enough public data.*\)$")

    ORG = {"login": "org", "public_repos": 0, "followers": 0}


if __name__ == "__main__":
    unittest.main()

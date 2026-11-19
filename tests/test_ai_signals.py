import io
import unittest
from contextlib import redirect_stdout
from unittest import mock

import requests

import prospect


def resp(status):
    r = mock.Mock(status_code=status)
    if status >= 400:
        r.raise_for_status.side_effect = requests.HTTPError(f"{status}")
    return r


def repo(name):
    return {"full_name": f"org/{name}"}


def fake_session(present=None, errors=None):
    """present: {repo name: set of root paths}; errors: {(repo, path): status}."""
    present = present or {}
    errors = errors or {}
    s = mock.Mock()

    def get(url, params=None, timeout=None):
        _, rest = url.split("/repos/org/")
        name, path = rest.split("/contents/")
        if (name, path) in errors:
            return resp(errors[(name, path)])
        return resp(200 if path in present.get(name, set()) else 404)

    s.get.side_effect = get
    return s


class DetectionTests(unittest.TestCase):
    def counts(self, present, repos=None):
        repos = repos or [repo(n) for n in present]
        return prospect.get_ai_signals(fake_session(present), repos)["signals"]

    def test_each_signal_detected(self):
        cases = {
            "CLAUDE.md": "CLAUDE.md",
            "AGENTS.md": "AGENTS.md",
            "Cursor": ".cursor",
            "Copilot instructions": ".github/copilot-instructions.md",
            "Warp": ".warp",
        }
        for signal, path in cases.items():
            with self.subTest(signal=signal):
                counts = self.counts({"a": {path}})
                self.assertEqual(counts[signal], 1)
                self.assertEqual(sum(counts.values()), 1)

    def test_cursorrules_alone_counts_as_cursor(self):
        self.assertEqual(self.counts({"a": {".cursorrules"}})["Cursor"], 1)

    def test_cursor_counted_once_per_repo(self):
        counts = self.counts({"a": {".cursor", ".cursorrules"}, "b": {".cursorrules"}})
        self.assertEqual(counts["Cursor"], 2)

    def test_mixed_and_none(self):
        counts = self.counts({
            "a": {"CLAUDE.md", "AGENTS.md"},
            "b": {"AGENTS.md", ".warp"},
            "c": set(),
        })
        self.assertEqual(counts["CLAUDE.md"], 1)
        self.assertEqual(counts["AGENTS.md"], 2)
        self.assertEqual(counts["Warp"], 1)
        self.assertEqual(counts["Cursor"], 0)
        self.assertEqual(counts["Copilot instructions"], 0)

    def test_only_first_ten_repos_checked(self):
        repos = [repo(f"r{i}") for i in range(12)]
        present = {f"r{i}": {"AGENTS.md"} for i in range(12)}
        out = prospect.get_ai_signals(fake_session(present), repos)
        self.assertEqual(out["repos_checked"], 10)
        self.assertEqual(out["signals"]["AGENTS.md"], 10)

    def test_zero_and_fewer_repos(self):
        s = fake_session()
        zero = prospect.get_ai_signals(s, [])
        self.assertEqual(zero["repos_checked"], 0)
        self.assertEqual(sum(zero["signals"].values()), 0)
        s.get.assert_not_called()
        self.assertEqual(prospect.get_ai_signals(s, [repo("a")])["repos_checked"], 1)

    def test_missing_path_is_not_an_error(self):
        out = prospect.get_ai_signals(fake_session(), [repo("a")])
        self.assertEqual(sum(out["signals"].values()), 0)

    def test_non_404_errors_propagate(self):
        for status in (403, 429, 500):
            with self.subTest(status=status):
                s = fake_session(errors={("a", "AGENTS.md"): status})
                with self.assertRaises(requests.HTTPError):
                    prospect.get_ai_signals(s, [repo("a")])

    def test_requests_use_default_branch_root_paths(self):
        s = fake_session()
        prospect.get_ai_signals(s, [repo("a")])
        urls = [c.args[0] for c in s.get.call_args_list]
        self.assertEqual(len(urls), 6)
        self.assertTrue(all("ref=" not in u for u in urls))
        self.assertTrue(all(c.kwargs.get("params") is None for c in s.get.call_args_list))


class ReportTests(unittest.TestCase):
    ORG = {"login": "org", "public_repos": 3, "followers": 7}

    def render(self, ai):
        sm = prospect.summarize(self.ORG, [], None, ai)
        buf = io.StringIO()
        with redirect_stdout(buf):
            prospect.print_report(sm)
        return buf.getvalue()

    def test_lists_found_signals_with_counts(self):
        ai = {"repos_checked": 10, "signals": {"CLAUDE.md": 3, "AGENTS.md": 0, "Cursor": 1}}
        out = self.render(ai)
        self.assertIn("AI coding tool signals (top 10 repos):", out)
        self.assertIn("CLAUDE.md: 3 of 10 repos", out)
        self.assertIn("Cursor: 1 of 10 repos", out)
        self.assertNotIn("AGENTS.md: 0", out)

    def test_none_found(self):
        out = self.render({"repos_checked": 4, "signals": {"CLAUDE.md": 0}})
        self.assertIn("none found", out)

    def test_existing_metrics_unchanged_without_signals(self):
        sm = prospect.summarize(self.ORG, [], {"active_contributors": 3, "prs_opened_30d": 8})
        self.assertEqual(sm["active_contributors"], 3)
        self.assertEqual(sm["prs_opened_30d"], 8)
        self.assertEqual(sm["repos_pushed_90d"], 0)


if __name__ == "__main__":
    unittest.main()

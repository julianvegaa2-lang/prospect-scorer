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
    """present: {repo: {path: kind}}; errors: {(repo, dir): status}."""
    present = present or {}
    errors = errors or {}
    s = mock.Mock()

    def get(url, params=None, timeout=None):
        _, rest = url.split("/repos/org/")
        name, directory = rest.split("/contents/")
        if (name, directory) in errors:
            return resp(errors[(name, directory)])
        prefix = f"{directory}/" if directory else ""
        entries = {}
        for path, kind in present.get(name, {}).items():
            if path.startswith(prefix):
                first, _, tail = path[len(prefix):].partition("/")
                entries[first] = "dir" if tail else kind
        if directory and not entries:
            return resp(404)
        r = resp(200)
        r.json.return_value = [
            {"name": n, "type": k} for n, k in entries.items()
        ]
        return r

    s.get.side_effect = get
    return s


def files(*paths):
    return {p: "file" for p in paths}


class DetectionTests(unittest.TestCase):
    def counts(self, present):
        repos = [repo(n) for n in present]
        return prospect.get_ai_signals(fake_session(present), repos)["signals"]

    def test_each_signal_detected(self):
        cases = {
            "CLAUDE.md": {"CLAUDE.md": "file"},
            "AGENTS.md": {"AGENTS.md": "file"},
            "Cursor": {".cursor/rules": "file"},
            "Copilot instructions": files(".github/copilot-instructions.md"),
            "Warp": {".warp/config.yaml": "file"},
        }
        for signal, present in cases.items():
            with self.subTest(signal=signal):
                counts = self.counts({"a": present})
                self.assertEqual(counts[signal], 1)
                self.assertEqual(sum(counts.values()), 1)

    def test_cursorrules_alone_counts_as_cursor(self):
        counts = self.counts({"a": files(".cursorrules")})
        self.assertEqual(counts["Cursor"], 1)

    def test_cursor_counted_once_per_repo(self):
        counts = self.counts({
            "a": {".cursor/rules": "file", ".cursorrules": "file"},
            "b": files(".cursorrules"),
        })
        self.assertEqual(counts["Cursor"], 2)

    def test_mixed_and_none(self):
        counts = self.counts({
            "a": files("CLAUDE.md", "AGENTS.md"),
            "b": {"AGENTS.md": "file", ".warp/x": "file"},
            "c": {},
        })
        self.assertEqual(counts["CLAUDE.md"], 1)
        self.assertEqual(counts["AGENTS.md"], 2)
        self.assertEqual(counts["Warp"], 1)
        self.assertEqual(counts["Cursor"], 0)
        self.assertEqual(counts["Copilot instructions"], 0)

    def test_wrong_entry_types_are_not_signals(self):
        counts = self.counts({
            "a": files(
                "CLAUDE.md/x", "AGENTS.md/x", ".cursorrules/x",
                ".github/copilot-instructions.md/x",
            ),
            "b": {".warp": "file", ".cursor": "file"},
        })
        self.assertEqual(sum(counts.values()), 0)

    def test_only_first_ten_repos_checked(self):
        repos = [repo(f"r{i}") for i in range(12)]
        present = {f"r{i}": files("AGENTS.md") for i in range(12)}
        out = prospect.get_ai_signals(fake_session(present), repos)
        self.assertEqual(out["repos_checked"], 10)
        self.assertEqual(out["signals"]["AGENTS.md"], 10)

    def test_repos_with_signal_counts_each_repo_once(self):
        repos = [repo(n) for n in "abc"]
        present = {"a": files("CLAUDE.md", "AGENTS.md"), "b": files("AGENTS.md"), "c": {}}
        out = prospect.get_ai_signals(fake_session(present), repos)
        self.assertEqual(out["repos_with_signal"], 2)

    def test_zero_and_fewer_repos(self):
        s = fake_session()
        zero = prospect.get_ai_signals(s, [])
        self.assertEqual(zero["repos_checked"], 0)
        self.assertEqual(zero["repos_with_signal"], 0)
        self.assertEqual(sum(zero["signals"].values()), 0)
        s.get.assert_not_called()
        one = prospect.get_ai_signals(s, [repo("a")])
        self.assertEqual(one["repos_checked"], 1)

    def test_missing_paths_and_empty_repo_are_not_errors(self):
        s = fake_session(errors={("a", ""): 404})
        out = prospect.get_ai_signals(s, [repo("a"), repo("b")])
        self.assertEqual(sum(out["signals"].values()), 0)

    def test_non_404_errors_propagate(self):
        for status in (403, 429, 500):
            with self.subTest(status=status, where="root"):
                s = fake_session(errors={("a", ""): status})
                with self.assertRaises(requests.HTTPError):
                    prospect.get_ai_signals(s, [repo("a")])
            with self.subTest(status=status, where=".github"):
                s = fake_session(
                    present={"a": files(".github/copilot-instructions.md")},
                    errors={("a", ".github"): status},
                )
                with self.assertRaises(requests.HTTPError):
                    prospect.get_ai_signals(s, [repo("a")])

    def test_signal_free_repos_use_one_request_each(self):
        s = fake_session()
        prospect.get_ai_signals(s, [repo(f"r{i}") for i in range(10)])
        self.assertEqual(s.get.call_count, 10)

    def test_request_budget_stays_within_unauthenticated_limit(self):
        present = {f"r{i}": files(".github/workflow") for i in range(10)}
        s = fake_session(present)
        prospect.get_ai_signals(s, [repo(f"r{i}") for i in range(10)])
        # org, repo list, then contributors and PRs for each of ten repos
        base_calls = 2 + 10 * 2
        self.assertLessEqual(base_calls + s.get.call_count, 60)

    def test_requests_use_default_branch(self):
        s = fake_session({"a": files(".github/copilot-instructions.md")})
        prospect.get_ai_signals(s, [repo("a")])
        for call in s.get.call_args_list:
            self.assertIsNone(call.kwargs.get("params"))


class ReportTests(unittest.TestCase):
    ORG = {"login": "org", "public_repos": 3, "followers": 7}

    def render(self, ai):
        sm = prospect.summarize(self.ORG, [], None, ai)
        buf = io.StringIO()
        with redirect_stdout(buf):
            prospect.print_report(sm)
        return buf.getvalue()

    def test_lists_found_signals_with_counts(self):
        signals = {"CLAUDE.md": 3, "AGENTS.md": 0, "Cursor": 1}
        ai = {"repos_checked": 10, "repos_with_signal": 4, "signals": signals}
        out = self.render(ai)
        self.assertIn("AI coding tool signals (top 10 repos):", out)
        self.assertIn("CLAUDE.md: 3 of 10 repos", out)
        self.assertIn("Cursor: 1 of 10 repos", out)
        self.assertNotIn("AGENTS.md: 0", out)

    def test_none_found(self):
        out = self.render({"repos_checked": 4, "repos_with_signal": 0,
                           "signals": {"CLAUDE.md": 0}})
        self.assertIn("none found", out)

    def test_existing_metrics_unchanged_without_signals(self):
        activity = {"active_contributors": 3, "prs_opened_30d": 8}
        sm = prospect.summarize(self.ORG, [], activity)
        self.assertEqual(sm["active_contributors"], 3)
        self.assertEqual(sm["prs_opened_30d"], 8)
        self.assertEqual(sm["repos_pushed_90d"], 0)


if __name__ == "__main__":
    unittest.main()

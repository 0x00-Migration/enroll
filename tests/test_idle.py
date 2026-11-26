import contextlib
import io
import os
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import idle  # noqa: E402
import projects  # noqa: E402
from test_enroll import (ALICE, BEIJING, BOB, BOT, CATALOG, CONTEST_TIME, ORG, WARMUP_TIME,  # noqa: E402
                         FakeGitHub, FakeIssues, claim, github_time, state, warm_up)

DAYS = 2
LATE = CONTEST_TIME + timedelta(days=DAYS, hours=1)
STAFF = {"login": "staff", "id": 500}


def push(api, name, actor, kind="push", when=CONTEST_TIME):
    api.activity[f"{ORG}/{name}"].insert(0, {"activity_type": kind, "ref": "refs/heads/main",
                                             "actor": dict(actor), "timestamp": github_time(when)})


def sweep(api, now, days=DAYS):
    issues = FakeIssues()
    with contextlib.redirect_stdout(io.StringIO()):
        idle.release_idle(api, issues, f"{ORG}/enroll", ORG, CATALOG, now, days)
    return issues


def scheduled_run(api, days):
    environ = {"GITHUB_REPOSITORY": f"{ORG}/enroll", "GITHUB_REPOSITORY_OWNER": ORG,
               "GH_TOKEN": "app", "ISSUE_TOKEN": "issues", "IDLE_DAYS": days}
    with mock.patch.dict(os.environ, environ, clear=True), \
            mock.patch.object(idle, "GitHub", {"app": api, "issues": FakeIssues()}.get), \
            mock.patch.object(projects, "load", lambda: CATALOG), \
            mock.patch.object(idle, "datetime") as clock, \
            contextlib.redirect_stdout(io.StringIO()):
        clock.now.return_value = LATE
        idle.main()


def claimed():
    api = FakeGitHub()
    claim(api, "excalidraw", issue=7)
    return api


class IdleTest(unittest.TestCase):
    def test_claim_without_a_push_by_its_player_is_released_after_idle_days(self):
        api = claimed()
        issues = sweep(api, LATE)
        self.assertTrue(api.repos["org/app-excalidraw-alice"]["archived"])
        variables = api.variables["org/app-excalidraw-alice"]
        self.assertEqual(variables["RELEASE_REASON"]["value"], "idle")
        self.assertEqual(projects.moment(variables["RELEASED_AT"]["value"]), LATE)
        [comment] = issues.comments[7]
        self.assertTrue(comment.startswith("@alice "))
        self.assertIn("https://github.com/org/app-excalidraw-alice", comment)
        claim(api, "excalidraw", BOB)
        self.assertIn("org/app-excalidraw-bob", api.repos)

    def test_claim_is_kept_until_its_idle_days_end(self):
        api = claimed()
        before = state(api)
        issues = sweep(api, CONTEST_TIME + timedelta(days=DAYS, seconds=-1))
        self.assertEqual((state(api), issues.comments), (before, {}))

    def test_any_push_by_the_player_keeps_the_claim(self):
        for kind in ("push", "force_push", "branch_creation"):
            with self.subTest(kind=kind):
                api = claimed()
                push(api, "app-excalidraw-alice", ALICE, kind, when=LATE)
                before = state(api)
                issues = sweep(api, LATE + timedelta(days=3))
                self.assertEqual((state(api), issues.comments), (before, {}))

    def test_pushes_by_anyone_but_the_player_do_not_count(self):
        api = claimed()
        push(api, "app-excalidraw-alice", BOT)
        push(api, "app-excalidraw-alice", STAFF, "force_push")
        sweep(api, LATE)
        self.assertTrue(api.repos["org/app-excalidraw-alice"]["archived"])

    def test_idle_days_count_from_the_claim_of_a_warm_up_repository(self):
        api = FakeGitHub()
        warm_up(api, "excalidraw")
        claim(api, "excalidraw", issue=7)
        before = state(api)
        issues = sweep(api, CONTEST_TIME + timedelta(days=DAYS, seconds=-1))
        self.assertEqual((state(api), issues.comments), (before, {}))

    def test_pushes_before_the_claim_do_not_count(self):
        for count in (1, 100):
            with self.subTest(pushes=count):
                api = FakeGitHub()
                warm_up(api, "excalidraw", issue=3)
                for _ in range(count):
                    push(api, "app-excalidraw-alice", ALICE, when=WARMUP_TIME)
                claim(api, "excalidraw", issue=7)
                issues = sweep(api, LATE)
                self.assertTrue(api.repos["org/app-excalidraw-alice"]["archived"])
                self.assertEqual(list(issues.comments), [7])

    def test_activity_beyond_one_page_keeps_the_claim(self):
        api = claimed()
        for _ in range(100):
            push(api, "app-excalidraw-alice", STAFF)
        sweep(api, LATE)
        self.assertFalse(api.repos["org/app-excalidraw-alice"]["archived"])

    def test_nothing_is_released_outside_the_contest_window(self):
        api = claimed()
        before = state(api)
        for now in (datetime(2026, 12, 8, tzinfo=BEIJING), datetime(2026, 12, 20, tzinfo=BEIJING)):
            with self.subTest(now=now):
                issues = sweep(api, now)
                self.assertEqual((state(api), issues.comments), (before, {}))

    def test_warm_up_claims_are_never_released(self):
        api = FakeGitHub()
        api.now = WARMUP_TIME
        claim(api, "hello", now=WARMUP_TIME, issue=7)
        before = state(api)
        issues = sweep(api, LATE)
        self.assertEqual((state(api), issues.comments), (before, {}))

    def test_free_mode_repositories_are_never_released(self):
        api = FakeGitHub()
        warm_up(api, "excalidraw", issue=3)
        before = state(api)
        issues = sweep(api, LATE)
        self.assertEqual((state(api), issues.comments), (before, {}))

    def test_without_idle_days_nothing_is_released(self):
        api = claimed()
        before = state(api)
        issues = sweep(api, LATE + timedelta(days=3), days=None)
        self.assertEqual((state(api), issues.comments), (before, {}))

    def test_released_claim_is_left_alone_afterwards(self):
        api = claimed()
        sweep(api, LATE)
        before = state(api)
        issues = sweep(api, LATE + timedelta(hours=1))
        self.assertEqual((state(api), issues.comments), (before, {}))

    def test_claim_whose_issue_is_gone_is_released_and_the_sweep_goes_on(self):
        api = claimed()
        claim(api, "obsidian", BOB, issue=8)
        issues = FakeIssues(gone={7})
        log = io.StringIO()
        with contextlib.redirect_stdout(log):
            idle.release_idle(api, issues, f"{ORG}/enroll", ORG, CATALOG, LATE, DAYS)
        self.assertTrue(api.repos["org/app-excalidraw-alice"]["archived"])
        self.assertTrue(api.repos["org/app-obsidian-bob"]["archived"])
        self.assertEqual(list(issues.comments), [8])
        self.assertIn("#7", log.getvalue())

    def test_restored_claim_gets_idle_days_again_from_its_restore(self):
        api = claimed()
        sweep(api, LATE)
        restored = LATE + timedelta(hours=2)
        full = "org/app-excalidraw-alice"
        api.repos[full]["archived"] = False
        del api.variables[full]["RELEASED_AT"], api.variables[full]["RELEASE_REASON"]
        api.variables[full]["CLAIMED_AT"]["value"] = restored.isoformat()
        before = state(api)
        issues = sweep(api, restored + timedelta(hours=1))
        self.assertEqual((state(api), issues.comments), (before, {}))
        sweep(api, restored + timedelta(days=DAYS, hours=1))
        self.assertTrue(api.repos[full]["archived"])

    def test_claim_without_an_issue_is_released_silently(self):
        api = FakeGitHub()
        claim(api, "excalidraw")
        issues = sweep(api, LATE)
        self.assertTrue(api.repos["org/app-excalidraw-alice"]["archived"])
        self.assertEqual(issues.comments, {})

    def test_scheduled_run_reads_idle_days_and_the_clock(self):
        for days, archived in [("2", True), ("", False)]:
            with self.subTest(IDLE_DAYS=days):
                api = claimed()
                scheduled_run(api, days)
                self.assertEqual(api.repos["org/app-excalidraw-alice"]["archived"], archived)

    def test_scheduled_run_refuses_idle_days_that_are_not_positive(self):
        for days in ("0", "-1"):
            with self.subTest(IDLE_DAYS=days):
                api = claimed()
                with self.assertRaises(ValueError):
                    scheduled_run(api, days)
                self.assertFalse(api.repos["org/app-excalidraw-alice"]["archived"])

    def test_scheduled_run_refuses_idle_days_that_are_not_finite_numbers(self):
        for days in ("inf", "nan", "two"):
            with self.subTest(IDLE_DAYS=days):
                api = claimed()
                with self.assertRaises(ValueError):
                    scheduled_run(api, days)
                self.assertFalse(api.repos["org/app-excalidraw-alice"]["archived"])


if __name__ == "__main__":
    unittest.main()

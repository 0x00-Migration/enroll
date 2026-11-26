import contextlib
import copy
import io
import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import enroll  # noqa: E402
import projects  # noqa: E402
from github import ApiError  # noqa: E402
from test_projects import entry, listing  # noqa: E402

ORG = "org"
ALICE = {"login": "alice", "id": 1, "type": "User"}
MALLORY = {"login": "mallory", "id": 2, "type": "User"}


class FakeGitHub:
    def __init__(self):
        self.users = {"alice": dict(ALICE), "mallory": dict(MALLORY)}
        self.repos = {f"{ORG}/app-template": self.repo("app-template", template=None, is_template=True)}
        self.variables = {}
        self.rulesets = {}
        self.collaborators = {}
        self.invitations = {}
        self.calls = []
        self.next_id = 100

    def repo(self, name, template=f"{ORG}/app-template", is_template=False):
        full = f"{ORG}/{name}"
        return {"full_name": full, "html_url": "https://github.com/" + full, "default_branch": "main",
                "is_template": is_template, "private": False, "archived": False,
                "template_repository": {"full_name": template} if template else None}

    def missing(self, method, path):
        raise ApiError(method, path, 404, "Not Found")

    def get(self, path, missing_ok=False):
        self.calls.append(("GET", path))
        parts = path.split("?")[0].split("/")
        result = None
        if parts[0] == "users":
            result = self.users.get(parts[1])
        elif parts[0] == "repos":
            full = parts[1] + "/" + parts[2]
            rest = parts[3:]
            if full not in self.repos:
                result = None
            elif not rest:
                result = self.repos[full]
            elif rest[0] == "branches":
                result = {"name": rest[1]}
            elif rest[:2] == ["actions", "variables"]:
                result = self.variables.get(full, {}).get(rest[2])
            elif rest[0] == "rulesets":
                result = self.rulesets.get(full, [])
            elif rest[0] == "collaborators" and len(rest) == 1:
                result = [{"login": login} for login in self.collaborators.get(full, {})]
            elif rest[0] == "collaborators":
                user = self.users[rest[1]]
                result = {"user": user, "permission": self.collaborators.get(full, {}).get(rest[1], "read")}
            elif rest[0] == "invitations":
                result = list(self.invitations.get(full, {}).values())
        if result is None:
            if missing_ok:
                return None
            self.missing("GET", path)
        return result

    def pages(self, path):
        yield from self.get(path)

    def post(self, path, body):
        self.calls.append(("POST", path))
        parts = path.split("/")
        full = parts[1] + "/" + parts[2]
        if parts[3] == "generate":
            target = body["owner"] + "/" + body["name"]
            if target in self.repos:
                raise ApiError("POST", path, 422, "name already exists")
            self.repos[target] = self.repo(body["name"], template=full)
        elif parts[3:] == ["actions", "variables"]:
            self.variables.setdefault(full, {})[body["name"]] = dict(body)
        elif parts[3] == "rulesets":
            self.rulesets.setdefault(full, []).append(body)
        return {}

    def patch(self, path, body):
        self.calls.append(("PATCH", path))
        parts = path.split("/")
        self.variables[parts[1] + "/" + parts[2]][parts[5]] = dict(body)

    def put(self, path, body=None):
        self.calls.append(("PUT", path))
        parts = path.split("/")
        full, login = parts[1] + "/" + parts[2], parts[4]
        self.next_id += 1
        self.invitations.setdefault(full, {})[self.next_id] = {
            "id": self.next_id, "invitee": {"id": self.users[login]["id"]}, "expired": False, "permissions": "write"}
        return {"id": self.next_id}

    def delete(self, path):
        self.calls.append(("DELETE", path))
        parts = path.split("/")
        self.invitations[parts[1] + "/" + parts[2]].pop(int(parts[4]))


class FakeIssues:
    def __init__(self, issue=None):
        self.issue = issue
        self.comments = {}

    def get(self, path):
        return self.issue

    def post(self, path, body):
        self.comments.setdefault(int(path.split("/")[4]), []).append(body["body"])

    def patch(self, path, body):
        self.issue.update(body)


def run(api, applicant=ALICE, app="notes"):
    return enroll.enroll(api, ORG, applicant, app, sleep=lambda _: None)


BEIJING = projects.BEIJING
WARMUP_TIME = datetime(2026, 11, 28, 12, tzinfo=BEIJING)
CONTEST_TIME = datetime(2026, 12, 2, 12, tzinfo=BEIJING)
CATALOG = projects.parse(listing(entry("hello", warmup=True), entry("excalidraw"), entry("obsidian"), entry("foo"), entry("foo-bar")))
EMPTY = projects.parse(listing())


def github_time(moment):
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def claim(api, project, applicant=ALICE, now=CONTEST_TIME, **options):
    return enroll.enroll(api, ORG, applicant, project, sleep=lambda _: None, catalog=CATALOG, now=now, **options)


def form(app):
    return f"### 应用名\n\n{app}\n\n### 确认\n\n- [X] 我用的是自己的个人 GitHub 账号, 并同意作品以公开仓库的形式提交"


def open_issue(api, body, title=enroll.TITLE, user=ALICE, opened=CONTEST_TIME, catalog=EMPTY, **env):
    issues = FakeIssues({"number": 7, "title": title, "body": body, "user": user, "state": "open", "created_at": github_time(opened)})
    environ = {"GITHUB_REPOSITORY": f"{ORG}/enroll", "GITHUB_REPOSITORY_OWNER": ORG,
               "GH_TOKEN": "app", "ISSUE_TOKEN": "issues", "ISSUE_NUMBER": "7", **env}
    with mock.patch.dict(os.environ, environ, clear=True), \
            mock.patch.object(enroll, "GitHub", {"app": api, "issues": issues}.get), \
            mock.patch.object(projects, "load", lambda: catalog), \
            contextlib.redirect_stdout(io.StringIO()):
        enroll.main()
    return issues


def state(api):
    return copy.deepcopy((api.repos, api.variables, api.rulesets, api.collaborators, api.invitations))


class EnrollTest(unittest.TestCase):
    def test_new_applicant_gets_a_bound_protected_repository_and_an_invitation(self):
        api = FakeGitHub()
        url, access = run(api)
        self.assertEqual(url, "https://github.com/org/app-notes-alice")
        self.assertEqual(access, "invited")
        self.assertEqual(api.variables["org/app-notes-alice"]["STUDENT_ID"]["value"], "1")
        self.assertEqual(api.variables["org/app-notes-alice"]["STUDENT_LOGIN"]["value"], "alice")
        self.assertEqual(api.variables["org/app-notes-alice"]["APP_NAME"]["value"], "notes")
        [ruleset] = api.rulesets["org/app-notes-alice"]
        self.assertEqual({rule["type"] for rule in ruleset["rules"]}, {"deletion", "non_fast_forward"})
        self.assertEqual(ruleset["conditions"]["ref_name"]["include"], ["refs/heads/main"])

    def test_retry_is_idempotent(self):
        api = FakeGitHub()
        run(api)
        run(api)
        self.assertEqual(len(api.rulesets["org/app-notes-alice"]), 1)
        self.assertEqual(len(api.invitations["org/app-notes-alice"]), 1)
        self.assertEqual(sum(call == ("POST", "repos/org/app-template/generate") for call in api.calls), 1)

    def test_collaborator_with_write_is_not_invited_again(self):
        api = FakeGitHub()
        run(api)
        api.invitations["org/app-notes-alice"].clear()
        api.collaborators["org/app-notes-alice"] = {"alice": "write"}
        self.assertEqual(run(api)[1], "write")
        self.assertFalse(api.invitations["org/app-notes-alice"])

    def test_expired_invitation_is_replaced(self):
        api = FakeGitHub()
        run(api)
        [old] = api.invitations["org/app-notes-alice"]
        api.invitations["org/app-notes-alice"][old]["expired"] = True
        self.assertEqual(run(api)[1], "invited")
        self.assertNotIn(old, api.invitations["org/app-notes-alice"])
        self.assertEqual(len(api.invitations["org/app-notes-alice"]), 1)

    def test_repository_bound_to_another_account_is_refused(self):
        api = FakeGitHub()
        run(api)
        api.users["alice"] = {"login": "alice", "id": 3, "type": "User"}
        with self.assertRaises(enroll.Refusal):
            run(api, api.users["alice"])
        self.assertEqual(api.variables["org/app-notes-alice"]["STUDENT_ID"]["value"], "1")

    def test_renamed_applicant_is_refused(self):
        api = FakeGitHub()
        api.users["alice"] = {"login": "alice", "id": 9, "type": "User"}
        with self.assertRaises(enroll.Refusal):
            run(api)
        self.assertNotIn("org/app-notes-alice", api.repos)

    def test_organization_account_is_refused(self):
        api = FakeGitHub()
        api.users["alice"]["type"] = "Organization"
        with self.assertRaises(enroll.Refusal):
            run(api)

    def test_foreign_repository_with_the_same_name_is_left_alone(self):
        api = FakeGitHub()
        api.repos["org/app-notes-alice"] = api.repo("app-notes-alice", template=None)
        with self.assertRaises(RuntimeError):
            run(api)
        self.assertNotIn("org/app-notes-alice", api.variables)

    def test_unbound_repository_with_outside_access_is_left_alone(self):
        api = FakeGitHub()
        api.repos["org/app-notes-alice"] = api.repo("app-notes-alice")
        api.collaborators["org/app-notes-alice"] = {"mallory": "write"}
        with self.assertRaises(RuntimeError):
            run(api)
        self.assertNotIn("org/app-notes-alice", api.variables)

    def test_unbound_repository_from_an_interrupted_run_is_resumed(self):
        api = FakeGitHub()
        api.repos["org/app-notes-alice"] = api.repo("app-notes-alice")
        self.assertEqual(run(api)[1], "invited")
        self.assertEqual(api.variables["org/app-notes-alice"]["STUDENT_ID"]["value"], "1")

    def test_template_must_be_a_template(self):
        api = FakeGitHub()
        api.repos["org/app-template"]["is_template"] = False
        with self.assertRaises(RuntimeError):
            run(api)
        self.assertNotIn("org/app-notes-alice", api.repos)

    def test_each_app_gets_its_own_repository(self):
        api = FakeGitHub()
        run(api, app="notes")
        url, _ = run(api, app="markdown-editor")
        self.assertEqual(url, "https://github.com/org/app-markdown-editor-alice")
        self.assertIn("org/app-notes-alice", api.repos)

    def test_name_taken_by_another_account_is_refused(self):
        api = FakeGitHub()
        api.users["bar-alice"] = {"login": "bar-alice", "id": 4, "type": "User"}
        run(api, api.users["bar-alice"], app="foo")
        api.users["alice"] = dict(ALICE)
        with self.assertRaises(enroll.Refusal):
            run(api, app="foo-bar")
        self.assertEqual(api.variables["org/app-foo-bar-alice"]["STUDENT_ID"]["value"], "4")

    def test_app_name_is_read_from_the_issue_form(self):
        body = "### 应用名\n\nMarkdown-Editor\n\n### 确认\n\n- [X] 我用的是自己的个人 GitHub 账号"
        self.assertEqual(enroll.app_name(body), "markdown-editor")
        self.assertEqual(enroll.app_name("### 应用名\n\nnotes"), "notes")

    def test_bad_app_names_are_refused(self):
        for value in ["_No response_", "-x", "x-", "a--b", "a b", "名字", "a" * 41, ""]:
            with self.subTest(value=value), self.assertRaises(enroll.Refusal):
                enroll.app_name(f"### 应用名\n\n{value}\n\n### 确认\n\n- [X] ok")
        with self.assertRaises(enroll.Refusal):
            enroll.app_name(None)

    def test_success_message_skips_the_invitation_step_for_writers(self):
        self.assertNotIn("invitations", enroll.success_message("alice", "https://github.com/org/app-notes-alice", "write"))
        self.assertIn("/invitations", enroll.success_message("alice", "https://github.com/org/app-notes-alice", "invited"))



class ListModeTest(unittest.TestCase):
    def test_listed_project_claimed_inside_its_window_gets_a_repository(self):
        api = FakeGitHub()
        self.assertEqual(claim(api, "excalidraw", issue=7), ("https://github.com/org/app-excalidraw-alice", "invited"))
        variables = {name: variable["value"] for name, variable in api.variables["org/app-excalidraw-alice"].items()}
        self.assertEqual(variables, {"STUDENT_ID": "1", "STUDENT_LOGIN": "alice", "APP_NAME": "excalidraw", "CLAIM_ISSUE": "7"})

    def test_id_not_in_the_list_is_refused(self):
        api = FakeGitHub()
        with self.assertRaises(enroll.Refusal):
            claim(api, "notes")
        self.assertEqual(set(api.repos), {"org/app-template"})

    def test_each_project_is_claimed_only_inside_its_window(self):
        moments = {
            "before the warm-up": datetime(2026, 11, 26, 23, 59, 59, tzinfo=BEIJING),
            "warm-up": WARMUP_TIME,
            "last second of the warm-up": datetime(2026, 11, 29, 23, 59, 59, tzinfo=BEIJING),
            "between": datetime(2026, 11, 30, 12, tzinfo=BEIJING),
            "contest": CONTEST_TIME,
            "after the contest": datetime(2026, 12, 8, tzinfo=BEIJING),
        }
        open_at = {"hello": {"warm-up", "last second of the warm-up"}, "excalidraw": {"contest"}}
        for project, windows in open_at.items():
            for label, now in moments.items():
                with self.subTest(project=project, at=label):
                    api = FakeGitHub()
                    if label in windows:
                        claim(api, project, now=now)
                    else:
                        with self.assertRaises(enroll.Refusal):
                            claim(api, project, now=now)
                    self.assertEqual(f"org/app-{project}-alice" in api.repos, label in windows)

    def test_archived_repository_is_not_claimed_again(self):
        for mode, start in [("free", lambda api: run(api, app="excalidraw")), ("list", lambda api: claim(api, "excalidraw"))]:
            with self.subTest(mode=mode):
                api = FakeGitHub()
                start(api)
                api.repos["org/app-excalidraw-alice"]["archived"] = True
                before = state(api)
                with self.assertRaises(enroll.Refusal):
                    start(api)
                self.assertEqual(state(api), before)


class IssueTest(unittest.TestCase):
    def test_free_name_is_accepted_while_the_list_is_empty(self):
        api = FakeGitHub()
        issues = open_issue(api, form("notes"))
        self.assertEqual(issues.issue["state_reason"], "completed")
        self.assertTrue(issues.comments[7][0].startswith("@alice 你的仓库已就绪: https://github.com/org/app-notes-alice"))
        self.assertEqual(api.variables["org/app-notes-alice"]["CLAIM_ISSUE"]["value"], "7")

    def test_claim_before_its_window_is_refused_on_the_issue_with_the_window(self):
        api = FakeGitHub()
        issues = open_issue(api, form("excalidraw"), opened=WARMUP_TIME, catalog=CATALOG)
        [reply] = issues.comments[7]
        self.assertTrue(reply.startswith("@alice 没有创建仓库: "))
        self.assertIn("2026-12-01 00:00 到 2026-12-07 23:59 (北京时间)", reply)
        self.assertEqual(issues.issue["state_reason"], "not_planned")
        self.assertNotIn("org/app-excalidraw-alice", api.repos)


if __name__ == "__main__":
    unittest.main()

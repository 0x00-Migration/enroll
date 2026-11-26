import contextlib
import copy
import io
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import enroll  # noqa: E402
from github import ApiError  # noqa: E402

ORG = "org"
ALICE = {"login": "alice", "id": 1, "type": "User"}
MALLORY = {"login": "mallory", "id": 2, "type": "User"}
BOB = {"login": "bob", "id": 11, "type": "User"}
CAROL = {"login": "carol", "id": 12, "type": "User"}
DAVE = {"login": "dave", "id": 13, "type": "User"}
ERIN = {"login": "erin", "id": 14, "type": "User"}
ACME = {"login": "acme", "id": 20, "type": "Organization"}


class FakeGitHub:
    def __init__(self):
        self.users = {user["login"]: dict(user) for user in (ALICE, MALLORY, BOB, CAROL, DAVE, ERIN, ACME)}
        self.repos = {f"{ORG}/app-template": self.repo("app-template", template=None, is_template=True)}
        self.variables = {}
        self.rulesets = {}
        self.collaborators = {}
        self.invitations = {}
        self.calls = []
        self.next_id = 100
        self.clock = 0

    def tick(self):
        self.clock += 1
        return self.clock

    def repo(self, name, template=f"{ORG}/app-template", is_template=False, description=None):
        full = f"{ORG}/{name}"
        return {"name": name, "full_name": full, "html_url": "https://github.com/" + full, "description": description,
                "default_branch": "main", "is_template": is_template, "private": False, "archived": False,
                "template_repository": {"full_name": template} if template else None}

    def missing(self, method, path):
        raise ApiError(method, path, 404, "Not Found")

    def get(self, path, missing_ok=False):
        self.calls.append(("GET", path))
        parts = path.split("?")[0].split("/")
        result = None
        if parts[0] == "users":
            result = self.users.get(parts[1].lower())
        elif parts[0] == "user":
            result = next((user for user in self.users.values() if user["id"] == int(parts[1])), None)
        elif parts[0] == "orgs":
            result = [repo for full, repo in self.repos.items() if full.startswith(parts[1] + "/")]
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
            self.repos[target] = self.repo(body["name"], template=full, description=body["description"])
        elif parts[3:] == ["actions", "variables"]:
            self.variables.setdefault(full, {})[body["name"]] = dict(body, updated_at=self.tick())
        elif parts[3] == "rulesets":
            self.rulesets.setdefault(full, []).append(body)
        return {}

    def patch(self, path, body):
        self.calls.append(("PATCH", path))
        parts = path.split("/")
        full = parts[1] + "/" + parts[2]
        if len(parts) == 3:
            self.repos[full].update(body, updated_at=self.tick())
        else:
            self.variables[full][parts[5]] = dict(body, updated_at=self.tick())

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
    def __init__(self, issue):
        self.issue = issue
        self.comments = []

    def get(self, path):
        return self.issue

    def post(self, path, body):
        self.comments.append(body["body"])

    def patch(self, path, body):
        self.issue.update(body)


def run(api, applicant=ALICE, app="notes", mates=(), **options):
    return enroll.enroll(api, ORG, applicant, app, mates, sleep=lambda _: None, **options)


def form(app="notes", mates="_No response_"):
    return f"### 应用名\n\n{app}\n\n### 队友\n\n{mates}\n\n### 确认\n\n- [X] 我用的是自己的个人 GitHub 账号, 并同意作品以公开仓库的形式提交"


def open_issue(api, body, user=ALICE, **env):
    issues = FakeIssues({"number": 7, "title": "领取仓库", "body": body, "user": user, "state": "open"})
    environ = {"GITHUB_REPOSITORY": f"{ORG}/enroll", "GITHUB_REPOSITORY_OWNER": ORG,
               "GH_TOKEN": "app", "ISSUE_TOKEN": "issues", "ISSUE_NUMBER": "7", **env}
    with mock.patch.dict(os.environ, environ, clear=True), \
            mock.patch.object(enroll, "GitHub", {"app": api, "issues": issues}.get), \
            contextlib.redirect_stdout(io.StringIO()):
        enroll.main()
    return issues


def state(api):
    return copy.deepcopy((api.repos, api.variables, api.rulesets, api.collaborators, api.invitations))


def repo_url(name):
    return f"https://github.com/{ORG}/{name}"


def invitees(api, name):
    return {invitation["invitee"]["id"] for invitation in api.invitations.get(f"{ORG}/{name}", {}).values()}


def recorded_team(api, name):
    variables = api.variables[f"{ORG}/{name}"]
    return variables["MEMBER_IDS"]["value"], variables["MEMBER_LOGINS"]["value"], api.repos[f"{ORG}/{name}"]["description"]


def accept(api, name, *users):
    full = f"{ORG}/{name}"
    for user in users:
        api.invitations[full] = {key: invitation for key, invitation in api.invitations[full].items()
                                 if invitation["invitee"]["id"] != user["id"]}
        api.collaborators.setdefault(full, {})[user["login"]] = "write"


class EnrollTest(unittest.TestCase):
    def test_new_applicant_gets_a_bound_protected_repository_and_an_invitation(self):
        api = FakeGitHub()
        url, pending = run(api)
        self.assertEqual(url, "https://github.com/org/app-notes-alice")
        self.assertEqual(pending, {"alice": [url]})
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

    def test_retry_changes_nothing(self):
        api = FakeGitHub()
        run(api)
        before = state(api)
        run(api)
        self.assertEqual(state(api), before)

    def test_collaborator_with_write_is_not_invited_again(self):
        api = FakeGitHub()
        run(api)
        api.invitations["org/app-notes-alice"].clear()
        api.collaborators["org/app-notes-alice"] = {"alice": "write"}
        self.assertEqual(run(api)[1], {"alice": []})
        self.assertFalse(api.invitations["org/app-notes-alice"])

    def test_expired_invitation_is_replaced(self):
        api = FakeGitHub()
        run(api)
        [old] = api.invitations["org/app-notes-alice"]
        api.invitations["org/app-notes-alice"][old]["expired"] = True
        self.assertEqual(run(api)[1], {"alice": [repo_url("app-notes-alice")]})
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
        self.assertEqual(run(api)[1], {"alice": [repo_url("app-notes-alice")]})
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

    def test_teammates_are_read_from_the_issue_form(self):
        body = "### 应用名\n\nnotes\n\n### 队友\n\n@bob, carol\nDave\n\n### 确认\n\n- [X] ok"
        self.assertEqual(enroll.teammates(body, "alice"), ["bob", "carol", "Dave"])
        self.assertEqual(enroll.app_name(body), "notes")
        self.assertEqual(enroll.teammates("### 队友\n\nbob，carol、 dave", "alice"), ["bob", "carol", "dave"])
        self.assertEqual(enroll.teammates("### 队友\n\n" + "b" * 39, "alice"), ["b" * 39])

    def test_empty_teammates_field_means_no_teammates(self):
        for body in ["### 应用名\n\nnotes\n\n### 队友\n\n_No response_\n\n### 确认\n\n- [X] ok", "### 应用名\n\nnotes", None]:
            with self.subTest(body=body):
                self.assertEqual(enroll.teammates(body, "alice"), [])

    def test_teammates_are_deduplicated_ignoring_case(self):
        self.assertEqual(enroll.teammates("### 队友\n\nbob @Bob BOB carol", "alice"), ["bob", "carol"])

    def test_bad_teammate_lists_are_refused(self):
        for value in ["-bob", "bob-", "b--b", "b_b", "@@bob", "b" * 40, "名字", "https://github.com/bob",
                      "@Alice", "bob carol dave erin"]:
            with self.subTest(value=value), self.assertRaises(enroll.Refusal):
                enroll.teammates(f"### 队友\n\n{value}\n\n### 确认\n\n- [X] ok", "alice")

    def test_success_message_skips_the_invitation_step_for_writers(self):
        url = "https://github.com/org/app-notes-alice"
        self.assertNotIn("invitations", enroll.success_message(url, {"alice": []}))
        self.assertIn("/invitations", enroll.success_message(url, {"alice": [url]}))


class TeamTest(unittest.TestCase):
    def test_captain_and_two_teammates_share_the_repository(self):
        api = FakeGitHub()
        url, pending = run(api, mates=["bob", "Carol"])
        self.assertEqual(url, repo_url("app-notes-alice"))
        self.assertEqual(pending, {"alice": [url], "bob": [url], "carol": [url]})
        self.assertEqual(recorded_team(api, "app-notes-alice"), ("1,11,12", "alice,bob,carol", "@alice @bob @carol"))
        self.assertEqual(api.variables["org/app-notes-alice"]["STUDENT_ID"]["value"], "1")
        self.assertEqual(invitees(api, "app-notes-alice"), {1, 11, 12})

    def test_retry_with_teammates_changes_nothing(self):
        api = FakeGitHub()
        _, first = run(api, mates=["bob", "carol"])
        before = state(api)
        self.assertEqual(run(api, mates=["bob", "carol"])[1], first)
        self.assertEqual(state(api), before)

    def test_new_app_is_shared_with_the_whole_team(self):
        api = FakeGitHub()
        run(api, app="notes", mates=["bob"])
        accept(api, "app-notes-alice", ALICE, BOB)
        _, pending = run(api, app="todo")
        todo = repo_url("app-todo-alice")
        self.assertEqual(pending, {"alice": [todo], "bob": [todo]})
        self.assertEqual(recorded_team(api, "app-todo-alice"), ("1,11", "alice,bob", "@alice @bob"))
        self.assertEqual(invitees(api, "app-todo-alice"), {1, 11})

    def test_teammate_added_later_is_invited_to_every_repository_of_the_team(self):
        api = FakeGitHub()
        run(api, app="notes", mates=["bob"])
        run(api, app="todo")
        accept(api, "app-notes-alice", ALICE, BOB)
        accept(api, "app-todo-alice", ALICE, BOB)
        _, pending = run(api, app="notes", mates=["carol"])
        notes, todo = repo_url("app-notes-alice"), repo_url("app-todo-alice")
        self.assertEqual(pending, {"alice": [], "bob": [], "carol": [notes, todo]})
        self.assertEqual(recorded_team(api, "app-notes-alice"), ("1,11,12", "alice,bob,carol", "@alice @bob @carol"))
        self.assertEqual(invitees(api, "app-notes-alice"), {12})
        self.assertEqual(invitees(api, "app-todo-alice"), {12})

    def test_teammate_cannot_claim_for_the_team(self):
        api = FakeGitHub()
        run(api, mates=["bob"])
        before = state(api)
        for app in ["notes", "todo"]:
            with self.subTest(app=app), self.assertRaises(enroll.Refusal):
                run(api, BOB, app=app)
        self.assertEqual(state(api), before)

    def test_member_of_another_team_cannot_be_listed(self):
        api = FakeGitHub()
        run(api, mates=["bob"])
        before = state(api)
        with self.assertRaises(enroll.Refusal):
            run(api, CAROL, app="chat", mates=["Bob"])
        self.assertEqual(state(api), before)

    def test_captain_of_another_team_cannot_be_listed(self):
        api = FakeGitHub()
        run(api, BOB, app="todo")
        before = state(api)
        with self.assertRaises(enroll.Refusal):
            run(api, mates=["bob"])
        self.assertEqual(state(api), before)

    def test_team_of_more_than_four_is_refused(self):
        api = FakeGitHub()
        run(api, mates=["bob", "carol", "dave"])
        before = state(api)
        for app in ["notes", "todo"]:
            with self.subTest(app=app), self.assertRaises(enroll.Refusal):
                run(api, app=app, mates=["erin"])
        self.assertEqual(state(api), before)

    def test_unknown_or_organization_teammate_is_refused(self):
        for login in ["nobody", "acme"]:
            api = FakeGitHub()
            with self.subTest(login=login), self.assertRaises(enroll.Refusal):
                run(api, mates=["bob", login])
            self.assertEqual(state(api), state(FakeGitHub()))

    def test_archived_repositories_are_ignored(self):
        api = FakeGitHub()
        run(api, CAROL, app="old", mates=["bob"])
        run(api, app="old", mates=["dave"])
        api.repos["org/app-old-carol"]["archived"] = True
        api.repos["org/app-old-alice"]["archived"] = True
        _, pending = run(api, mates=["bob"])
        notes = repo_url("app-notes-alice")
        self.assertEqual(pending, {"alice": [notes], "bob": [notes]})
        self.assertEqual(recorded_team(api, "app-notes-alice"), ("1,11", "alice,bob", "@alice @bob"))
        self.assertEqual(invitees(api, "app-old-alice"), {1, 13})

    def test_repository_from_before_teams_counts_as_the_captain_alone(self):
        api = FakeGitHub()
        run(api, app="notes")
        for name in ("MEMBER_IDS", "MEMBER_LOGINS"):
            del api.variables["org/app-notes-alice"][name]
        _, pending = run(api, app="todo", mates=["bob"])
        notes, todo = repo_url("app-notes-alice"), repo_url("app-todo-alice")
        self.assertEqual(pending, {"alice": [todo, notes], "bob": [todo, notes]})
        self.assertEqual(recorded_team(api, "app-todo-alice"), ("1,11", "alice,bob", "@alice @bob"))

    def test_repository_naming_the_captain_but_bound_to_someone_else_is_not_theirs(self):
        api = FakeGitHub()
        api.users["alice"] = {"login": "alice", "id": 99, "type": "User"}
        run(api, api.users["alice"], app="old", mates=["mallory"])
        api.users["alice"] = dict(ALICE)
        _, pending = run(api)
        self.assertEqual(pending, {"alice": [repo_url("app-notes-alice")]})
        self.assertEqual(invitees(api, "app-old-alice"), {99, 2})
        with self.assertRaises(enroll.Refusal):
            run(api, mates=["mallory"])

    def test_max_repos_limits_new_repositories_but_not_claims_on_existing_ones(self):
        api = FakeGitHub()
        run(api, app="notes", max_repos=2)
        run(api, app="todo", mates=["bob"], max_repos=2)
        before = state(api)
        with self.assertRaises(enroll.Refusal):
            run(api, app="chat", max_repos=2)
        self.assertEqual(state(api), before)
        run(api, app="notes", mates=["carol"], max_repos=2)
        self.assertEqual(invitees(api, "app-notes-alice"), {1, 11, 12})
        api.repos["org/app-todo-alice"]["archived"] = True
        run(api, app="chat", max_repos=2)
        self.assertIn("org/app-chat-alice", api.repos)

    def test_max_repos_is_read_from_the_environment(self):
        api = FakeGitHub()
        open_issue(api, form(app="notes"), MAX_REPOS="1")
        self.assertEqual(open_issue(api, form(app="todo"), MAX_REPOS="1").issue["state_reason"], "not_planned")
        self.assertNotIn("org/app-todo-alice", api.repos)
        self.assertEqual(open_issue(api, form(app="todo"), MAX_REPOS="").issue["state_reason"], "completed")
        self.assertIn("org/app-todo-alice", api.repos)

    def test_run_that_stops_halfway_already_holds_the_new_teammate(self):
        api = FakeGitHub()
        post = api.post

        def post_until_member_ids(path, body):
            if body.get("name") == "MEMBER_IDS":
                raise ApiError("POST", path, 500, "stopped")
            return post(path, body)

        api.post = post_until_member_ids
        with self.assertRaises(ApiError):
            run(api, mates=["bob"])
        api.post = post
        with self.assertRaises(enroll.Refusal):
            run(api, CAROL, app="chat", mates=["bob"])
        run(api, mates=["bob"])
        self.assertEqual(recorded_team(api, "app-notes-alice"), ("1,11", "alice,bob", "@alice @bob"))

    def test_reply_lists_every_member_and_the_invitations_to_accept(self):
        api = FakeGitHub()
        notes = repo_url("app-notes-alice")
        issues = open_issue(api, form(mates="@bob, carol"))
        [reply] = issues.comments
        self.assertTrue(reply.startswith(f"@alice 你的仓库已就绪: {notes}\n"))
        for line in [f"- @alice (队长): 接受邀请 {notes}/invitations", f"- @bob: 接受邀请 {notes}/invitations",
                     f"- @carol: 接受邀请 {notes}/invitations"]:
            self.assertIn(line, reply.splitlines())
        self.assertEqual((issues.issue["state"], issues.issue["state_reason"]), ("closed", "completed"))

        accept(api, "app-notes-alice", ALICE, BOB, CAROL)
        [reply] = open_issue(api, form(mates="dave")).comments
        for line in ["- @alice (队长): 已有权限", "- @bob: 已有权限", "- @carol: 已有权限",
                     f"- @dave: 接受邀请 {notes}/invitations"]:
            self.assertIn(line, reply.splitlines())

    def test_refusal_is_explained_and_the_issue_closed_as_not_planned(self):
        api = FakeGitHub()
        run(api, mates=["bob"])
        before = state(api)
        issues = open_issue(api, form(app="todo"), user=BOB)
        [reply] = issues.comments
        self.assertTrue(reply.startswith("@bob 领取没有成功: "))
        self.assertIn(repo_url("app-notes-alice"), reply)
        self.assertEqual((issues.issue["state"], issues.issue["state_reason"]), ("closed", "not_planned"))
        self.assertEqual(state(api), before)


if __name__ == "__main__":
    unittest.main()

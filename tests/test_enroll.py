import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import enroll  # noqa: E402
from github import ApiError  # noqa: E402

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


def run(api, applicant=ALICE):
    return enroll.enroll(api, ORG, applicant, sleep=lambda _: None)


class EnrollTest(unittest.TestCase):
    def test_new_applicant_gets_a_bound_protected_repository_and_an_invitation(self):
        api = FakeGitHub()
        url, access = run(api)
        self.assertEqual(url, "https://github.com/org/app-alice")
        self.assertEqual(access, "invited")
        self.assertEqual(api.variables["org/app-alice"]["STUDENT_ID"]["value"], "1")
        self.assertEqual(api.variables["org/app-alice"]["STUDENT_LOGIN"]["value"], "alice")
        [ruleset] = api.rulesets["org/app-alice"]
        self.assertEqual({rule["type"] for rule in ruleset["rules"]}, {"deletion", "non_fast_forward"})
        self.assertEqual(ruleset["conditions"]["ref_name"]["include"], ["refs/heads/main"])

    def test_retry_is_idempotent(self):
        api = FakeGitHub()
        run(api)
        run(api)
        self.assertEqual(len(api.rulesets["org/app-alice"]), 1)
        self.assertEqual(len(api.invitations["org/app-alice"]), 1)
        self.assertEqual(sum(call == ("POST", "repos/org/app-template/generate") for call in api.calls), 1)

    def test_collaborator_with_write_is_not_invited_again(self):
        api = FakeGitHub()
        run(api)
        api.invitations["org/app-alice"].clear()
        api.collaborators["org/app-alice"] = {"alice": "write"}
        self.assertEqual(run(api)[1], "write")
        self.assertFalse(api.invitations["org/app-alice"])

    def test_expired_invitation_is_replaced(self):
        api = FakeGitHub()
        run(api)
        [old] = api.invitations["org/app-alice"]
        api.invitations["org/app-alice"][old]["expired"] = True
        self.assertEqual(run(api)[1], "invited")
        self.assertNotIn(old, api.invitations["org/app-alice"])
        self.assertEqual(len(api.invitations["org/app-alice"]), 1)

    def test_repository_bound_to_another_account_is_refused(self):
        api = FakeGitHub()
        run(api)
        api.users["alice"] = {"login": "alice", "id": 3, "type": "User"}
        with self.assertRaises(enroll.Refusal):
            run(api, api.users["alice"])
        self.assertEqual(api.variables["org/app-alice"]["STUDENT_ID"]["value"], "1")

    def test_renamed_applicant_is_refused(self):
        api = FakeGitHub()
        api.users["alice"] = {"login": "alice", "id": 9, "type": "User"}
        with self.assertRaises(enroll.Refusal):
            run(api)
        self.assertNotIn("org/app-alice", api.repos)

    def test_organization_account_is_refused(self):
        api = FakeGitHub()
        api.users["alice"]["type"] = "Organization"
        with self.assertRaises(enroll.Refusal):
            run(api)

    def test_foreign_repository_with_the_same_name_is_left_alone(self):
        api = FakeGitHub()
        api.repos["org/app-alice"] = api.repo("app-alice", template=None)
        with self.assertRaises(RuntimeError):
            run(api)
        self.assertNotIn("org/app-alice", api.variables)

    def test_unbound_repository_with_outside_access_is_left_alone(self):
        api = FakeGitHub()
        api.repos["org/app-alice"] = api.repo("app-alice")
        api.collaborators["org/app-alice"] = {"mallory": "write"}
        with self.assertRaises(RuntimeError):
            run(api)
        self.assertNotIn("org/app-alice", api.variables)

    def test_unbound_repository_from_an_interrupted_run_is_resumed(self):
        api = FakeGitHub()
        api.repos["org/app-alice"] = api.repo("app-alice")
        self.assertEqual(run(api)[1], "invited")
        self.assertEqual(api.variables["org/app-alice"]["STUDENT_ID"]["value"], "1")

    def test_template_must_be_a_template(self):
        api = FakeGitHub()
        api.repos["org/app-template"]["is_template"] = False
        with self.assertRaises(RuntimeError):
            run(api)
        self.assertNotIn("org/app-alice", api.repos)

    def test_success_message_skips_the_invitation_step_for_writers(self):
        self.assertNotIn("invitations", enroll.success_message("alice", "https://github.com/org/app-alice", "write"))
        self.assertIn("/invitations", enroll.success_message("alice", "https://github.com/org/app-alice", "invited"))


if __name__ == "__main__":
    unittest.main()

"""Create an applicant's repository from the template when they open an enrollment issue."""

import json
import os
import re
import sys
import time

from github import ApiError, GitHub, eventually

TITLE = "领取仓库"
TEMPLATE = "app-template"
PREFIX = "app-"
RULESET = "history"
APP_LABEL = "应用名"
APP_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
APP_NAME_MAX = 40
TEAM_LABEL = "队友"
TEAM_MAX = 4
LOGIN = re.compile(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*")
LOGIN_MAX = 39
WRITE = {"write", "maintain", "admin"}


class Refusal(Exception):
    """The application cannot be accepted; the message is shown to the applicant."""


def field(body, label):
    match = re.search(rf"^###\s*{label}\s*$(.*?)(?=^###|\Z)", body or "", re.M | re.S)
    value = match[1].strip() if match else ""
    return "" if value == "_No response_" else value


def app_name(body):
    """Read the app name field of the issue form."""
    value = field(body, APP_LABEL).lower()
    if not APP_NAME.fullmatch(value) or len(value) > APP_NAME_MAX:
        raise Refusal(f"应用名要是 1 到 {APP_NAME_MAX} 个小写字母、数字或 `-` (不以 `-` 开头或结尾, 不连用), 比如 `markdown-editor`. "
                      "请从领取页面重新开一个 issue.")
    return value


def teammates(body, applicant_login):
    logins = {}
    for word in re.split(r"[\s,，、]+", field(body, TEAM_LABEL)):
        if not word:
            continue
        login = word.removeprefix("@")
        if not LOGIN.fullmatch(login) or len(login) > LOGIN_MAX:
            raise Refusal(f"队友要写 GitHub 用户名 (字母、数字和单个 `-`, 最多 {LOGIN_MAX} 个字符), 用空格、逗号或换行隔开, 比如 `@bob, carol`. "
                          f"`{word}` 不是有效的用户名. 请从领取页面重新开一个 issue.")
        logins.setdefault(login.lower(), login)
    if applicant_login.lower() in logins:
        raise Refusal("队友里不用写你自己. 请从领取页面重新开一个 issue.")
    if len(logins) >= TEAM_MAX:
        raise Refusal(f"一个队最多 {TEAM_MAX} 人, 所以队友最多写 {TEAM_MAX - 1} 个. 请从领取页面重新开一个 issue.")
    return list(logins.values())


def check_template(api, template):
    source = api.get("repos/" + template)
    if not source.get("is_template") or source.get("private") or source.get("archived"):
        raise RuntimeError(template + " must be a public, unarchived template repository.")


def create(api, org, template, name, description, sleep):
    full = f"{org}/{name}"
    try:
        api.post(f"repos/{template}/generate", {
            "owner": org, "name": name, "description": description,
            "private": False, "include_all_branches": False,
        })
    except ApiError as error:
        if error.status != 422:
            raise
        print(f"Generate returned 422, checking whether {full} already exists", flush=True)
    repo = eventually(lambda: api.get("repos/" + full), sleep)
    eventually(lambda: api.get(f"repos/{full}/branches/{repo['default_branch']}"), sleep)
    return repo


def outsiders(api, full):
    collaborators = list(api.pages(f"repos/{full}/collaborators?affiliation=outside"))
    invitations = list(api.pages(f"repos/{full}/invitations"))
    return collaborators or invitations


def variable(api, full, name):
    found = api.get(f"repos/{full}/actions/variables/{name}", missing_ok=True)
    return None if found is None else found["value"]


def set_variable(api, full, name, value):
    current = variable(api, full, name)
    if current is None:
        api.post(f"repos/{full}/actions/variables", {"name": name, "value": value})
    elif current != value:
        api.patch(f"repos/{full}/actions/variables/{name}", {"name": name, "value": value})


def bind(api, full, student, sleep):
    path = f"repos/{full}/actions/variables/STUDENT_ID"
    expected = str(student["id"])
    variable = api.get(path, missing_ok=True)
    if variable is None:
        if outsiders(api, full):
            raise RuntimeError(f"{full} has outside access but no STUDENT_ID; left for a maintainer.")
        try:
            api.post(f"repos/{full}/actions/variables", {"name": "STUDENT_ID", "value": expected})
        except ApiError as error:
            if error.status not in (409, 422):
                raise
        variable = eventually(lambda: api.get(path), sleep)
    if variable["value"] != expected:
        raise Refusal(f"仓库 {full} 已属于另一个 GitHub 账号. 请换一个应用名; 如果它是你改用户名之前领的, 请联系维护者.")
    set_variable(api, full, "STUDENT_LOGIN", student["login"])


def protect(api, full, branch):
    if any(ruleset["name"] == RULESET for ruleset in api.get(f"repos/{full}/rulesets")):
        return
    api.post(f"repos/{full}/rulesets", {
        "name": RULESET,
        "target": "branch",
        "enforcement": "active",
        "conditions": {"ref_name": {"include": ["refs/heads/" + branch], "exclude": []}},
        "rules": [{"type": "deletion"}, {"type": "non_fast_forward"}],
    })


def grant(api, full, student):
    permission = api.get(f"repos/{full}/collaborators/{student['login']}/permission")
    if permission["user"]["id"] != student["id"]:
        raise RuntimeError("The login now names a different GitHub user; access was not changed.")
    if permission["permission"] in WRITE:
        return "write"
    for invitation in api.pages(f"repos/{full}/invitations"):
        if (invitation.get("invitee") or {}).get("id") != student["id"]:
            continue
        if not invitation.get("expired") and invitation["permissions"] in WRITE:
            return "invited"
        api.delete(f"repos/{full}/invitations/{invitation['id']}")
    result = api.put(f"repos/{full}/collaborators/{student['login']}", {"permission": "push"})
    return "invited" if result else "write"


def enroll(api, org, applicant, app, template=TEMPLATE, prefix=PREFIX, sleep=time.sleep):
    student = api.get("users/" + applicant["login"], missing_ok=True)
    if student is None or student["id"] != applicant["id"]:
        raise Refusal("提交 issue 之后 GitHub 用户名变了. 请用现在的账号重新开一个 issue.")
    if student["type"] != "User":
        raise Refusal("只接受个人 GitHub 账号.")

    template = f"{org}/{template}"
    name = f"{prefix}{app}-{student['login']}"
    full = f"{org}/{name}"
    repo = api.get("repos/" + full, missing_ok=True)
    if repo is None:
        check_template(api, template)
        repo = create(api, org, template, name, "@" + student["login"], sleep)
    if repo["full_name"].lower() != full.lower():
        raise RuntimeError(f"{full} redirects to {repo['full_name']}; left for a maintainer.")
    if (repo.get("template_repository") or {}).get("full_name", "").lower() != template.lower():
        raise RuntimeError(f"{full} exists but was not generated from {template}; left for a maintainer.")

    bind(api, full, student, sleep)
    set_variable(api, full, "APP_NAME", app)
    protect(api, full, repo["default_branch"])
    return repo["html_url"], grant(api, full, student)


def success_message(login, url, access):
    steps = [] if access == "write" else [f"接受邀请: {url}/invitations (7 天内有效; 过期了重新开一个 issue 即可)"]
    steps += [
        f"克隆: `git clone {url}.git`",
        "开发, 提交, push. 只有你能推送这个仓库; `main` 不能 force push, 也不能删除.",
        "每次 push 会自动跑 `acdev check` 和 `acdev build`, 结果在仓库的 Actions 页面. 作品由审核员人工审核.",
    ]
    lines = "\n".join(f"{i}. {step}" for i, step in enumerate(steps, 1))
    return f"@{login} 你的仓库已就绪: {url}\n\n{lines}\n"


def load_issue(hub, issues):
    number = os.environ.get("ISSUE_NUMBER")
    if number:
        return issues.get(f"repos/{hub}/issues/{int(number)}")
    with open(os.environ["GITHUB_EVENT_PATH"]) as event:
        return json.load(event)["issue"]


def main():
    hub = os.environ["GITHUB_REPOSITORY"]
    org = os.environ["GITHUB_REPOSITORY_OWNER"]
    api = GitHub(os.environ.get("GH_TOKEN"))
    issues = GitHub(os.environ.get("ISSUE_TOKEN"))
    issue = load_issue(hub, issues)
    if issue.get("pull_request"):
        return 0
    path = f"repos/{hub}/issues/{issue['number']}"
    login = issue["user"]["login"]
    if issue["title"].strip() != TITLE:
        print("Not an enrollment issue; ignored.")
        return 0
    try:
        app = app_name(issue.get("body"))
        url, access = enroll(api, org, issue["user"], app, os.environ.get("TEMPLATE") or TEMPLATE, os.environ.get("PREFIX") or PREFIX)
    except Refusal as refusal:
        issues.post(path + "/comments", {"body": f"@{login} 没有创建仓库: {refusal}"})
        issues.patch(path, {"state": "closed", "state_reason": "not_planned"})
        print("Refused: " + str(refusal))
        return 0
    except Exception:
        issues.post(path + "/comments", {"body": f"@{login} 处理时出错, 维护者会查看并重试, 请先不要重复提交."})
        raise
    issues.post(path + "/comments", {"body": success_message(login, url, access)})
    issues.patch(path, {"state": "closed", "state_reason": "completed"})
    print(f"Enrolled {login}: {url} ({access})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

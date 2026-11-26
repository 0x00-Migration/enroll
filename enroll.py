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


def teammate(api, login):
    user = api.get("users/" + login, missing_ok=True)
    if user is None:
        raise Refusal(f"找不到 GitHub 用户 `{login}`. 请检查队友的用户名, 从领取页面重新开一个 issue.")
    if user["type"] != "User":
        raise Refusal(f"`{login}` 不是个人 GitHub 账号, 队友只能写个人账号. 请从领取页面重新开一个 issue.")
    return user


def listed(repo):
    return [word[1:].lower() for word in (repo["description"] or "").split() if word.startswith("@")]


def app_repos(api, org, template, prefix, captain):
    """Split the active app repositories into the captain's own, confirmed by STUDENT_ID, and the others with
    the logins their descriptions list. Descriptions are the index because the repository list returns them
    100 at a time, while variables cost a request per repository."""
    own, others = [], []
    for repo in api.pages(f"orgs/{org}/repos?type=all&sort=created&direction=asc"):
        if repo["archived"] or not repo["name"].startswith(prefix) or repo["full_name"].lower() == template.lower():
            continue
        logins = listed(repo)
        if logins[:1] == [captain["login"].lower()] and variable(api, repo["full_name"], "STUDENT_ID") == str(captain["id"]):
            own.append(repo)
        elif logins:
            others.append((repo, logins))
    return own, others


def check_membership(others, captain, mates):
    for repo, (leader, *members) in others:
        if captain["login"].lower() in members:
            raise Refusal(f"你已经是 `{leader}` 队的队员 ({repo['html_url']}), 只有队长能领仓库. "
                          "要给队里加应用请让队长来领; 要换队请联系维护者.")
    for mate in mates:
        for repo, logins in others:
            if mate["login"].lower() in logins:
                role = "另一个队的队长" if logins[0] == mate["login"].lower() else f" `{logins[0]}` 队的队员"
                raise Refusal(f"`{mate['login']}` 已经是{role} ({repo['html_url']}), 一个人只能在一个队. "
                              "写错了请改好后重新开一个 issue; 要换队请联系维护者.")


def roster(api, own, captain, mates):
    known = {user["id"]: user for user in [captain, *mates]}
    ids = [captain["id"]]
    for repo in own:
        ids += [int(member) for member in (variable(api, repo["full_name"], "MEMBER_IDS") or "").split(",") if member]
    ids += [mate["id"] for mate in mates]
    return [known.get(member) or api.get(f"user/{member}") for member in dict.fromkeys(ids)]


def record(api, repo, team):
    full = repo["full_name"]
    description = " ".join("@" + user["login"] for user in team)
    # Description first: other claims check it, so if this run stops halfway it must not miss a member.
    if repo["description"] != description:
        api.patch("repos/" + full, {"description": description})
    set_variable(api, full, "MEMBER_IDS", ",".join(str(user["id"]) for user in team))
    set_variable(api, full, "MEMBER_LOGINS", ",".join(user["login"] for user in team))


def enroll(api, org, applicant, app, mates=(), template=TEMPLATE, prefix=PREFIX, max_repos=None, sleep=time.sleep):
    student = api.get("users/" + applicant["login"], missing_ok=True)
    if student is None or student["id"] != applicant["id"]:
        raise Refusal("提交 issue 之后 GitHub 用户名变了. 请用现在的账号重新开一个 issue.")
    if student["type"] != "User":
        raise Refusal("只接受个人 GitHub 账号.")
    mates = [teammate(api, login) for login in mates]

    template = f"{org}/{template}"
    own, others = app_repos(api, org, template, prefix, student)
    check_membership(others, student, mates)
    team = roster(api, own, student, mates)
    if len(team) > TEAM_MAX:
        raise Refusal(f"一个队最多 {TEAM_MAX} 人, 加上这次写的队友就有 {len(team)} 人了. "
                      "请少写几个后重新开一个 issue; 要换人请联系维护者.")

    name = f"{prefix}{app}-{student['login']}"
    full = f"{org}/{name}"
    repo = api.get("repos/" + full, missing_ok=True)
    if repo is None:
        if max_repos is not None and len(own) >= max_repos:
            raise Refusal(f"每个队最多领 {max_repos} 个仓库, 你的队已经领满了. 要加队友, 请用已有的应用名重新开一个 issue.")
        check_template(api, template)
        repo = create(api, org, template, name, "@" + student["login"], sleep)
    if repo["full_name"].lower() != full.lower():
        raise RuntimeError(f"{full} redirects to {repo['full_name']}; left for a maintainer.")
    if (repo.get("template_repository") or {}).get("full_name", "").lower() != template.lower():
        raise RuntimeError(f"{full} exists but was not generated from {template}; left for a maintainer.")
    if repo["archived"]:
        raise Refusal(f"仓库 {full} 已归档, 不能再领. 请换一个应用名; 要恢复这个仓库请联系维护者.")

    bind(api, full, student, sleep)
    set_variable(api, full, "APP_NAME", app)
    protect(api, full, repo["default_branch"])
    repos = [repo, *(other for other in own if other["full_name"].lower() != full.lower())]
    for each in repos:
        record(api, each, team)
    pending = {user["login"]: [] for user in team}
    for each in repos:
        for user in team:
            if grant(api, each["full_name"], user) == "invited":
                pending[user["login"]].append(each["html_url"])
    return repo["html_url"], pending


def success_message(url, pending):
    captain = next(iter(pending))
    members = []
    for login, repos in pending.items():
        role = " (队长)" if login == captain else ""
        access = "接受邀请 " + " ".join(f"{repo}/invitations" for repo in repos) if repos else "已有权限"
        members.append(f"- @{login}{role}: {access}")
    note = " (邀请 7 天内有效; 过期了队长用同一个应用名重新开一个 issue 即可)" if any(pending.values()) else ""
    steps = [
        f"克隆: `git clone {url}.git`",
        "开发, 提交, push. 只有队员能推送这个仓库; `main` 不能 force push, 也不能删除.",
        "每次 push 会自动跑 `acdev check` 和 `acdev build`, 结果在仓库的 Actions 页面. 作品由审核员人工审核.",
    ]
    lines = "\n".join(f"{i}. {step}" for i, step in enumerate(steps, 1))
    return f"@{captain} 你的仓库已就绪: {url}\n\n队员{note}:\n" + "\n".join(members) + f"\n\n{lines}\n"


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
        mates = teammates(issue.get("body"), login)
        limit = os.environ.get("MAX_REPOS")
        url, pending = enroll(api, org, issue["user"], app, mates,
                              os.environ.get("TEMPLATE") or TEMPLATE, os.environ.get("PREFIX") or PREFIX,
                              int(limit) if limit else None)
    except Refusal as refusal:
        issues.post(path + "/comments", {"body": f"@{login} 领取没有成功: {refusal}"})
        issues.patch(path, {"state": "closed", "state_reason": "not_planned"})
        print("Refused: " + str(refusal))
        return 0
    except Exception:
        issues.post(path + "/comments", {"body": f"@{login} 处理时出错, 维护者会查看并重试, 请先不要重复提交."})
        raise
    issues.post(path + "/comments", {"body": success_message(url, pending)})
    issues.patch(path, {"state": "closed", "state_reason": "completed"})
    print(f"Enrolled {login}: {url} {pending}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""The project list in projects.json, its two claim windows, and the PROJECTS.md page built from them;
also the list's two limits set as repository variables, MAX_REPOS and IDLE_DAYS.

After editing projects.json, run `python3 projects.py` to regenerate PROJECTS.md.
"""

import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIST = HERE / "projects.json"
PAGE = HERE / "PROJECTS.md"
APP_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
APP_NAME_MAX = 40
BEIJING = timezone(timedelta(hours=8))
FIELDS = {"id": str, "name": str, "upstream": str, "tier": str, "features": list, "warmup": bool}


def moment(text):
    value = datetime.fromisoformat(text)
    if value.tzinfo is None:
        raise ValueError(f"{text} has no UTC offset; write it like 2026-12-01T00:00:00+08:00.")
    return value


@dataclass(frozen=True)
class Window:
    opens: datetime
    closes: datetime

    def __contains__(self, when):
        return self.opens <= when <= self.closes

    def __str__(self):
        ends = (when.astimezone(BEIJING).strftime("%Y-%m-%d %H:%M") for when in (self.opens, self.closes))
        return " 到 ".join(ends) + " (北京时间)"


@dataclass(frozen=True)
class Project:
    id: str
    name: str
    upstream: str
    tier: str
    features: tuple
    warmup: bool


@dataclass(frozen=True)
class Catalog:
    warmup: Window
    contest: Window
    projects: dict

    def window(self, project):
        return self.warmup if project.warmup else self.contest


def parse_window(data, key):
    opens, closes = moment(data[key]["opens"]), moment(data[key]["closes"])
    if opens >= closes:
        raise ValueError(f"projects.json: the {key} window does not close after it opens.")
    return Window(opens, closes)


def parse_project(raw):
    for field, kind in FIELDS.items():
        if not isinstance(raw.get(field), kind):
            raise ValueError(f"projects.json: project {raw.get('id')!r} needs {field} as a {kind.__name__}.")
    if not APP_NAME.fullmatch(raw["id"]) or len(raw["id"]) > APP_NAME_MAX:
        raise ValueError(f"projects.json: id {raw['id']!r} must be 1 to {APP_NAME_MAX} lowercase letters, digits or single hyphens.")
    if not all(isinstance(feature, str) for feature in raw["features"]):
        raise ValueError(f"projects.json: the features of {raw['id']!r} must be strings.")
    return Project(raw["id"], raw["name"], raw["upstream"], raw["tier"], tuple(raw["features"]), raw["warmup"])


def parse(data):
    listed = {}
    for raw in data["projects"]:
        project = parse_project(raw)
        if project.id in listed:
            raise ValueError(f"projects.json: id {project.id!r} appears twice.")
        listed[project.id] = project
    for short in listed:
        for long in listed:
            if long.startswith(short + "-"):
                raise ValueError(f"projects.json: id {long!r} starts with id {short!r} and a hyphen, so one repository name "
                                 "could be a claim of either; rename one of them.")
    return Catalog(parse_window(data, "warmup"), parse_window(data, "contest"), listed)


def max_repos(text):
    """MAX_REPOS: how many contest projects one player may hold at once; None when unset."""
    text = (text or "").strip()
    if not text:
        return None
    if not re.fullmatch(r"[0-9]+", text):
        raise ValueError(f"MAX_REPOS must be a whole number of projects, like 2, not {text!r}; unset it for no limit.")
    return int(text)


def idle_days(text):
    """IDLE_DAYS: the days a contest claim may go without a push by its player; None when unset."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        days = float(text)
    except ValueError:
        days = math.nan
    if not (math.isfinite(days) and days > 0):
        raise ValueError(f"IDLE_DAYS must be a positive number of days, like 2 or 0.5, not {text!r}; unset it to never release.")
    return days


def load(path=LIST):
    with open(path, encoding="utf-8") as file:
        return parse(json.load(file))


def cell(text):
    return text.replace("|", "\\|").replace("\n", " ")


def render(catalog):
    lines = ["# 项目列表", "", "<!-- projects.py 从 projects.json 生成, 不要手改. -->", ""]
    if not catalog.projects:
        return "\n".join(lines + ["项目列表还没公布. 公布之前领取不限项目, 应用名自己起.", ""])
    lines += [
        "在本仓库开「领取仓库」issue, 应用名填下面表里的 id.",
        "",
        "- 热身赛项目谁都能领, 只能在热身赛时间内领.",
        "- 正式赛项目只能在正式赛时间内领, 同一时间只归一个人: 有人领了, 别人就领不了, 直到他放弃, 或者他领了之后一直没有 push, 项目被收回.",
        "- 不想做了, 开「放弃项目」issue: 仓库归档 (只读, 仍然公开), 正式赛项目回到列表.",
        "- 想加项目, 开「提名项目」issue, 维护者审核后加进列表.",
    ]
    for title, warmup, period in (("热身赛项目", True, catalog.warmup), ("正式赛项目", False, catalog.contest)):
        listed = [project for project in catalog.projects.values() if project.warmup == warmup]
        if listed:
            lines += ["", f"## {title}", "", f"领取时间: {period}.", "", "| id | 项目 | 档位 | 核心功能 |", "|-|-|-|-|"]
            lines += [f"| `{project.id}` | [{cell(project.name)}]({project.upstream}) | {cell(project.tier)} | "
                      f"{'<br>'.join(map(cell, project.features))} |" for project in listed]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    PAGE.write_text(render(load()), encoding="utf-8")

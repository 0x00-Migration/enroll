import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import projects  # noqa: E402

WINDOWS = {
    "warmup": {"opens": "2026-11-27T00:00:00+08:00", "closes": "2026-11-29T23:59:59+08:00"},
    "contest": {"opens": "2026-12-01T00:00:00+08:00", "closes": "2026-12-07T23:59:59+08:00"},
}


def entry(id, warmup=False, **fields):
    return {"id": id, "name": id.title(), "upstream": f"https://github.com/up/{id}", "tier": "A",
            "features": ["编辑", "导出"], "warmup": warmup, **fields}


def listing(*entries):
    return {**copy.deepcopy(WINDOWS), "projects": list(entries)}


class ProjectsTest(unittest.TestCase):
    def test_projects_page_is_generated_from_the_list(self):
        self.assertEqual(projects.PAGE.read_text(encoding="utf-8"), projects.render(projects.load()),
                         "PROJECTS.md is stale: run python3 projects.py")

    def test_page_lists_each_project_under_its_phase_and_window(self):
        page = projects.render(projects.parse(listing(
            entry("hello", warmup=True, name="Hello | World"), entry("excalidraw", features=["画图", "导出 PNG"]))))
        warmup, contest = page.split("## 热身赛项目")[1].split("## 正式赛项目")
        self.assertIn("2026-11-27 00:00 到 2026-11-29 23:59 (北京时间)", warmup)
        self.assertIn("| `hello` | [Hello \\| World](https://github.com/up/hello) | A | 编辑<br>导出 |", warmup)
        self.assertIn("2026-12-01 00:00 到 2026-12-07 23:59 (北京时间)", contest)
        self.assertIn("| `excalidraw` | [Excalidraw](https://github.com/up/excalidraw) | A | 画图<br>导出 PNG |", contest)
        self.assertNotIn("excalidraw", warmup)

    def test_empty_list_says_names_are_free(self):
        page = projects.render(projects.parse(listing()))
        self.assertIn("项目列表还没公布", page)
        self.assertNotIn("|", page)

    def test_windows_hold_their_ends_in_any_time_zone(self):
        catalog = projects.parse(listing(entry("hello", warmup=True)))
        self.assertIn(projects.moment("2026-11-26T16:00:00Z"), catalog.warmup)
        self.assertIn(projects.moment("2026-12-07T23:59:59+08:00"), catalog.contest)
        self.assertNotIn(projects.moment("2026-11-26T15:59:59Z"), catalog.warmup)
        self.assertNotIn(projects.moment("2026-12-07T16:00:00Z"), catalog.contest)
        self.assertIs(catalog.window(catalog.projects["hello"]), catalog.warmup)

    def test_mistakes_in_the_list_are_refused(self):
        broken = {
            "uppercase id": listing(entry("Excalidraw")),
            "long id": listing(entry("a" * 41)),
            "double hyphen": listing(entry("a--b")),
            "repeated id": listing(entry("notes"), entry("notes", warmup=True)),
            "id inside a longer id": listing(entry("foo-bar"), entry("foo", warmup=True)),
            "missing field": listing({key: value for key, value in entry("notes").items() if key != "warmup"}),
            "warmup as text": listing(entry("notes", warmup="false")),
            "features as text": listing(entry("notes", features="编辑")),
            "feature as number": listing(entry("notes", features=[1])),
            "time without offset": {**listing(), "contest": {"opens": "2026-12-01T00:00:00", "closes": "2026-12-07T23:59:59+08:00"}},
            "window closing first": {**listing(), "warmup": {"opens": "2026-11-29T00:00:00+08:00", "closes": "2026-11-27T00:00:00+08:00"}},
        }
        for case, data in broken.items():
            with self.subTest(case=case), self.assertRaises(ValueError):
                projects.parse(data)

    def test_ids_that_only_share_a_start_are_accepted(self):
        catalog = projects.parse(listing(entry("foo"), entry("foobar"), entry("bar-foo")))
        self.assertEqual(set(catalog.projects), {"foo", "foobar", "bar-foo"})

    def test_project_fields_are_read(self):
        project = projects.parse(listing(entry("excalidraw", tier="S", features=["画图"]))).projects["excalidraw"]
        self.assertEqual(project, projects.Project("excalidraw", "Excalidraw", "https://github.com/up/excalidraw", "S", ("画图",), False))


if __name__ == "__main__":
    unittest.main()

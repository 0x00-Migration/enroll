import ast
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import enroll  # noqa: E402

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
QUEUE = "    concurrency:\n      group: enroll\n      queue: max\n      cancel-in-progress: false\n"


def runs(condition, event_name, issue=None):
    """Evaluate a job's `if:` for one event, as Actions would for the `||`, `&&`, `!` and `==` it uses."""
    context = {"github": {"event_name": event_name, "event": {"issue": issue or {}}}}

    def value(node):
        if isinstance(node, ast.BoolOp):
            results = [value(operand) for operand in node.values]
            return all(results) if isinstance(node.op, ast.And) else any(results)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return not value(node.operand)
        if isinstance(node, ast.Compare) and [type(op) for op in node.ops] == [ast.Eq]:
            return value(node.left) == value(node.comparators[0])
        if isinstance(node, ast.Attribute):
            parent = value(node.value)
            return parent.get(node.attr) if isinstance(parent, dict) else None
        if isinstance(node, ast.Name):
            return context.get(node.id)
        if isinstance(node, ast.Constant):
            return node.value
        raise ValueError("Unsupported in the test's evaluator: " + ast.dump(node))

    python = re.sub(r"!(?!=)", " not ", condition.replace("||", " or ").replace("&&", " and "))
    return bool(value(ast.parse(python, mode="eval").body))


class WorkflowTest(unittest.TestCase):
    def test_only_claims_give_ups_and_retries_start_the_enroll_job(self):
        condition = re.search(r"^    if: (.+)$", (WORKFLOWS / "enroll.yml").read_text(encoding="utf-8"), re.M)[1]
        for title in enroll.ROUTES:
            with self.subTest(title=title):
                self.assertTrue(runs(condition, "issues", {"title": title}))
                self.assertFalse(runs(condition, "issues", {"title": title, "pull_request": {"url": "u"}}))
        for title in ("提名项目: excalidraw", "领取仓库 2", "放弃", "bug report"):
            with self.subTest(title=title):
                self.assertFalse(runs(condition, "issues", {"title": title}))
        self.assertTrue(runs(condition, "workflow_dispatch"))

    def test_claims_and_idle_release_queue_in_one_group_at_the_job_level(self):
        for name in ("enroll.yml", "idle.yml"):
            with self.subTest(workflow=name):
                header, jobs = (WORKFLOWS / name).read_text(encoding="utf-8").split("\njobs:\n")
                self.assertNotIn("concurrency:", header)
                self.assertIn(QUEUE, jobs)


if __name__ == "__main__":
    unittest.main()

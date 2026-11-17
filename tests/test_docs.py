"""Documentation integrity tests.

The knowledge base is the product here, so its links, code fences and cross-references are
tested like code:  python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
README = REPO / "README.md"
DOCS = REPO / "docs"
MD_FILES = [README, *sorted(DOCS.glob("*.md"))]

LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
FENCE_RE = re.compile(r"^```", re.MULTILINE)
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)


def slugify(heading: str) -> str:
    """Approximate GitHub's heading-anchor algorithm (enough for our own links)."""
    text = heading.strip().lower()
    text = text.replace("`", "").replace("*", "")
    text = re.sub(r"[^\w\s-]", "", text)       # keep letters, digits, underscore, spaces, hyphen
    return text.strip().replace(" ", "-")       # one hyphen per space; do not collapse


def anchors(path: Path) -> set[str]:
    return {slugify(m.group(2)) for m in HEADING_RE.finditer(path.read_text(encoding="utf-8"))}


class Links(unittest.TestCase):
    def test_relative_links_resolve(self):
        problems = []
        for md in MD_FILES:
            text = md.read_text(encoding="utf-8")
            for raw in LINK_RE.findall(text):
                if raw.startswith(("http://", "https://", "mailto:")):
                    continue
                target, _, anchor = raw.partition("#")
                if not target:                       # same-file anchor
                    target_path = md
                else:
                    target_path = (md.parent / target).resolve()
                if not target_path.exists():
                    problems.append(f"{md.relative_to(REPO)}: missing {raw}")
                    continue
                if anchor and target_path.suffix == ".md":
                    if anchor not in anchors(target_path):
                        problems.append(f"{md.relative_to(REPO)}: bad anchor {raw}")
        self.assertEqual(problems, [], "\n".join(problems))

    def test_every_chapter_is_linked_from_readme(self):
        readme = README.read_text(encoding="utf-8")
        for chapter in sorted(DOCS.glob("[0-9][0-9]-*.md")):
            self.assertIn(chapter.name, readme, f"{chapter.name} is not referenced in README.md")

    def test_no_stale_paths(self):
        stale = ("docs/wow-335a-custom-content", "unified/", "Conflux implementation")
        problems = []
        for path in [*MD_FILES, *REPO.glob("tools/*"), *REPO.glob("tests/*.py")]:
            if not path.is_file() or path.name == "test_docs.py":
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for needle in stale:
                if needle in text and path.name != "README.md":
                    problems.append(f"{path.relative_to(REPO)} mentions {needle!r}")
        self.assertEqual(problems, [], "\n".join(problems))


class Structure(unittest.TestCase):
    def test_code_fences_balanced(self):
        for md in MD_FILES:
            fences = len(FENCE_RE.findall(md.read_text(encoding="utf-8")))
            self.assertEqual(fences % 2, 0, f"{md.name}: unbalanced ``` fences")

    def test_tools_exist_and_are_executable(self):
        for rel in ("tools/dbc_tool.py", "tools/preflight.sh", "tools/selftest.py",
                    "tools/manifest.py", "tools/catalog.py"):
            path = REPO / rel
            self.assertTrue(path.is_file(), f"{rel} missing")
            if path.suffix in {".py", ".sh"}:
                self.assertTrue(path.stat().st_mode & 0o111, f"{rel} is not executable")

    def test_docs_have_title(self):
        for md in sorted(DOCS.glob("*.md")):
            first = md.read_text(encoding="utf-8").lstrip().splitlines()[0]
            self.assertTrue(first.startswith("# "), f"{md.name}: first line is not an H1")


if __name__ == "__main__":
    unittest.main()

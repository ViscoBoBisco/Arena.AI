"""Specialist role registry (manual 3.5).

Parses Agency Agents-style definitions into immutable, versioned role
profiles. Profiles are planner-selectable instructions and review rubrics;
they NEVER directly authorize tools.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class RoleProfile:
    name: str
    version: str
    instructions: str
    review_criteria: list

    def to_dict(self) -> dict:
        return {"name": self.name, "version": self.version,
                "instructions": self.instructions,
                "review_criteria": self.review_criteria}


DEFAULT_ROLES = [
    {"name": "research-lead", "version": "1.0.0",
     "instructions": "Decompose research goals into evidence-backed steps; cite source locators.",
     "review_criteria": ["every claim has an evidence ID", "conflicts are surfaced"]},
    {"name": "data-specialist", "version": "1.0.0",
     "instructions": "Use typed provider data; preserve units, time zones and retrieval times.",
     "review_criteria": ["units and dates validated", "provider metadata retained"]},
    {"name": "engineering-lead", "version": "1.0.0",
     "instructions": "Query the code graph before editing; produce diffs and test receipts.",
     "review_criteria": ["diff reviewed", "tests pass in sandbox"]},
    {"name": "verifier", "version": "1.0.0",
     "instructions": "Independently cross-check receipts; separate observed data from inference.",
     "review_criteria": ["no unverified claims", "coverage limits stated"]},
]


class RoleRegistry:
    def __init__(self, roles_file: str | None = None):
        self._roles: dict[str, RoleProfile] = {}
        src = DEFAULT_ROLES
        if roles_file and os.path.exists(roles_file):
            with open(roles_file) as f:
                src = json.load(f)
        for r in src:
            p = RoleProfile(r["name"], r["version"], r["instructions"],
                            r.get("review_criteria", []))
            self._roles[p.name] = p  # immutable: last write wins at load only

    def get(self, name: str) -> RoleProfile | None:
        return self._roles.get(name)

    def list(self) -> list[dict]:
        return [p.to_dict() for p in self._roles.values()]

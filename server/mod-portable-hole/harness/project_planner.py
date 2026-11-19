"""Read-only planner for profession and Campaign Party preparation contracts.

The caller supplies the authenticated account ID from its server-side session. This
module reads only shared Portable Hole project budgets and never mutates balances,
reservations, character inventories, or project ACLs.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Protocol

SCHEMA = "arena-preparation-contract/1"
MAX_LINES = 128
MAX_AMOUNT = 2**31 - 1


class Cursor(Protocol):
    def execute(self, query: str, params: tuple[Any, ...] = ()) -> Any: ...
    def fetchall(self) -> tuple[tuple[Any, ...], ...]: ...


def _token(value: object, *, limit: int = 32) -> bool:
    return (isinstance(value, str) and 0 < len(value) <= limit and value.isascii()
            and all(char.isalnum() or char in "_-" for char in value))


def _positive_int(value: object, *, limit: int = MAX_AMOUNT) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 < value <= limit


def validate_contract(contract: object) -> list[dict[str, int | str]]:
    if not isinstance(contract, dict) or contract.get("schema") != SCHEMA:
        raise ValueError(f"preparation contract schema must be {SCHEMA}")
    if not _token(contract.get("request_id"), limit=64):
        raise ValueError("request_id must be an ASCII idempotency token")
    profession = contract.get("profession")
    if not isinstance(profession, dict) or not _positive_int(profession.get("recipe_id"), limit=2**32 - 1):
        raise ValueError("profession.recipe_id must be a positive source recipe id")
    requirements = contract.get("requirements")
    if not isinstance(requirements, list) or not requirements or len(requirements) > MAX_LINES:
        raise ValueError(f"requirements must contain 1..{MAX_LINES} lines")
    normalized: dict[tuple[int, str, str], dict[str, int | str]] = {}
    for line in requirements:
        if not isinstance(line, dict):
            raise ValueError("each requirement must be an object")
        item_entry = line.get("item_entry")
        amount = line.get("amount")
        owner = line.get("project_owner_account_id")
        project = line.get("project_key")
        if not _positive_int(item_entry, limit=2**32 - 1):
            raise ValueError("item_entry must be a positive uint32")
        if not _positive_int(amount):
            raise ValueError("amount must be a positive bounded integer")
        if not _positive_int(owner, limit=2**32 - 1):
            raise ValueError("project_owner_account_id must be a positive uint32")
        if not _token(project):
            raise ValueError("project_key must be an ASCII token")
        key = (owner, project, item_entry)
        item = normalized.setdefault(key, {"project_owner_account_id": owner,
                                            "project_key": project,
                                            "item_entry": item_entry,
                                            "amount": 0})
        total = int(item["amount"]) + amount
        if total > MAX_AMOUNT:
            raise ValueError("combined recipe amount exceeds the planner bound")
        item["amount"] = total
    return list(normalized.values())


def plan_preparation(cursor: Cursor, requester_account_id: int,
                     contract: object) -> dict[str, object]:
    """Plan recipe demand against authorized held project reservations, read-only.

    VIEW and CONTRIBUTE ACLs can read a plan; only the existing C++ mutation routes
    may reserve/release resources. Missing and unauthorized project keys deliberately
    produce the same denial to avoid revealing another account's project names.
    """
    if not _positive_int(requester_account_id, limit=2**32 - 1):
        raise PermissionError("authenticated requester account is required")
    requirements = validate_contract(contract)
    by_owner: dict[int, list[dict[str, int | str]]] = defaultdict(list)
    for requirement in requirements:
        by_owner[int(requirement["project_owner_account_id"])].append(requirement)

    budgets: dict[tuple[int, str], tuple[int, int, str, int]] = {}
    for owner, lines in by_owner.items():
        keys = sorted({str(line["project_key"]) for line in lines})
        placeholders = ",".join("%s" for _ in keys)
        cursor.execute(
            "SELECT p.project_key,p.item_entry,p.target_amount,p.reserved_amount,p.status "
            "FROM portable_hole_projects p WHERE p.owner_account_id=%s "
            f"AND p.project_key IN ({placeholders}) AND "
            "(p.owner_account_id=%s OR EXISTS (SELECT 1 FROM portable_hole_project_access a "
            "WHERE a.owner_account_id=p.owner_account_id AND a.project_key=p.project_key "
            "AND a.member_account_id=%s AND a.role IN ('VIEW','CONTRIBUTE'))) ORDER BY p.project_key",
            (owner, *keys, requester_account_id, requester_account_id),
        )
        for project_key, item_entry, target, reserved, status in cursor.fetchall():
            budgets[(owner, str(project_key))] = (
                int(item_entry), int(target), str(status), int(reserved))

    planned: list[dict[str, int | str]] = []
    for requirement in requirements:
        owner = int(requirement["project_owner_account_id"])
        key = str(requirement["project_key"])
        budget = budgets.get((owner, key))
        if budget is None:
            raise PermissionError("project missing or requester is not authorized")
        item_entry, target, status, reserved = budget
        if item_entry != int(requirement["item_entry"]):
            raise ValueError(f"project {key} is budgeted for a different commodity")
        demand = int(requirement["amount"])
        committed = min(demand, reserved)
        planned.append({
            "project_owner_account_id": owner,
            "project_key": key,
            "item_entry": item_entry,
            "required_amount": demand,
            "held_amount": committed,
            "shortfall_amount": demand - committed,
            "project_capacity_remaining": max(0, target - reserved) if status == "OPEN" else 0,
            "project_status": status,
        })
    return {
        "schema": "arena-preparation-plan/1",
        "request_id": contract["request_id"],
        "recipe_id": contract["profession"]["recipe_id"],
        "account_id": requester_account_id,
        "mutation_performed": False,
        "physical_inventory_accessed": False,
        "lines": planned,
    }

"""Typed, non-runtime foundations for personal faction accords.

Reputation identity (Faction.dbc) and creature hostility identity
(FactionTemplate.dbc) are intentionally separate types. This module makes no
stock-ID claims and does not mutate global creature hostility.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ReputationFactionRef:
    """Native reputation identity from Faction.dbc; never a hostility template."""

    value: Optional[int] = None
    table: str = "Faction.dbc"
    status: str = "UNRESOLVED"
    evidence_sha256: Optional[str] = None

    def validate(self) -> None:
        if self.table != "Faction.dbc":
            raise ValueError("reputation identity must come from Faction.dbc")
        _validate_asset_ref(self.value, self.status, self.evidence_sha256)


@dataclass(frozen=True)
class HostilityTemplateRef:
    """Creature affiliation/hostility identity from FactionTemplate.dbc."""

    value: Optional[int] = None
    table: str = "FactionTemplate.dbc"
    status: str = "UNRESOLVED"
    evidence_sha256: Optional[str] = None

    def validate(self) -> None:
        if self.table != "FactionTemplate.dbc":
            raise ValueError("hostility identity must come from FactionTemplate.dbc")
        _validate_asset_ref(self.value, self.status, self.evidence_sha256)


def _validate_asset_ref(value: Optional[int], status: str,
                        evidence_sha256: Optional[str]) -> None:
    if status == "UNRESOLVED":
        if value is not None or evidence_sha256 is not None:
            raise ValueError("UNRESOLVED stock assets must have null value and evidence")
        return
    if status != "LOCAL_ROW_PRESENT":
        raise ValueError("asset status must be UNRESOLVED or LOCAL_ROW_PRESENT")
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("resolved local row needs a non-negative integer ID")
    if (not isinstance(evidence_sha256, str) or len(evidence_sha256) != 64
            or any(char not in "0123456789abcdef" for char in evidence_sha256)):
        raise ValueError("resolved local row needs a lowercase SHA-256 source hash")


@dataclass(frozen=True)
class AccordPolicy:
    """Personal accord policy; reputation and diplomacy stay distinct."""

    key: str
    reputation_faction: ReputationFactionRef
    hostility_templates: tuple[HostilityTemplateRef, ...]
    standing_authority: str = "core_character_reputation"
    diplomacy_authority: str = "personal_accord"
    global_hostility_mutation: bool = False
    player_group_membership_emulated_for_npcs: bool = False

    def validate(self) -> None:
        if not self.key or not self.key.isascii():
            raise ValueError("accord key must be non-empty ASCII")
        self.reputation_faction.validate()
        for template in self.hostility_templates:
            template.validate()
        if self.standing_authority != "core_character_reputation":
            raise ValueError("player standing remains owned by core character reputation")
        if self.diplomacy_authority != "personal_accord":
            raise ValueError("accord diplomacy must remain a separate personal authority")
        if self.global_hostility_mutation:
            raise ValueError("a personal accord cannot globally rewrite creature hostility")
        if self.player_group_membership_emulated_for_npcs:
            raise ValueError("NPC followers are not implicitly player-group members")

    def to_dict(self) -> dict:
        self.validate()
        return {
            "schema": "arena-faction-operations-foundation/1",
            "status": "DESIGN_ONLY_NOT_PLAYABLE",
            "key": self.key,
            "identity": {
                "reputation": {
                    "table": self.reputation_faction.table,
                    "value": self.reputation_faction.value,
                    "status": self.reputation_faction.status,
                    "evidence_sha256": self.reputation_faction.evidence_sha256,
                },
                "hostility_templates": [
                    {"table": ref.table, "value": ref.value,
                     "status": ref.status, "evidence_sha256": ref.evidence_sha256}
                    for ref in self.hostility_templates
                ],
            },
            "authority": {
                "standing": self.standing_authority,
                "diplomacy": self.diplomacy_authority,
                "global_hostility_mutation": False,
                "npc_player_group_emulation": False,
            },
            "evidence_levels": {
                "source_pin": "public AzerothCore WotLK source layout only",
                "local_dbc_row_presence": "NOT_ASSESSED",
                "provenance": "NOT_ASSESSED",
                "runtime": "NOT_ASSESSED",
                "playerbot": "NOT_ASSESSED",
                "campaign_party": "NOT_ASSESSED",
            },
        }


def argent_foundation() -> AccordPolicy:
    """Return a safe template with every stock identity explicitly unresolved."""
    return AccordPolicy(
        key="argent-accord",
        reputation_faction=ReputationFactionRef(),
        hostility_templates=(HostilityTemplateRef(),),
    )

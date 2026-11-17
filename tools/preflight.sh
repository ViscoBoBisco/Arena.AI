#!/usr/bin/env bash
# preflight.sh - gate a 3.3.5a client DBC patch before packing/releasing it.
#
#   ./preflight.sh <dbc-client-dir> [dbc-server-dir]
#
# Checks, for every *.dbc in the client dir:
#   1. structural sanity (header, record size, truncation) and duplicate IDs
#   2. the server copy of the same table is identical (catches client/server drift)
# And, when the referenced tables are present:
#   3. Item.dbc.DisplayInfoID      -> ItemDisplayInfo.dbc
#   4. Spell.dbc.SpellIconID       -> SpellIcon.dbc
#   5. Spell.dbc.SpellVisualID_1   -> SpellVisual.dbc
#
# Exit code 0 = safe to pack, 1 = at least one check failed.
set -uo pipefail

CLIENT="${1:-}"
SERVER="${2:-}"
TOOL="$(dirname "$0")/dbc_tool.py"

if [[ -z "$CLIENT" || ! -d "$CLIENT" ]]; then
  echo "usage: $0 <dbc-client-dir> [dbc-server-dir]" >&2
  exit 2
fi
# A server dir that was passed but does not exist is a typo, not "no parity check wanted":
# silently skipping parity would let real drift through the gate.
if [[ -n "$SERVER" && ! -d "$SERVER" ]]; then
  echo "error: server dir '$SERVER' does not exist (omit the argument to skip parity)" >&2
  exit 2
fi
if [[ ! -f "$TOOL" ]]; then
  echo "error: dbc_tool.py not found next to this script" >&2
  exit 2
fi

fail=0
shopt -s nullglob
dbc_files=("$CLIENT"/*.dbc)
if [[ ${#dbc_files[@]} -eq 0 ]]; then
  echo "error: no .dbc files in $CLIENT" >&2
  exit 2
fi

echo "== structural checks (${#dbc_files[@]} table(s)) =="
for f in "${dbc_files[@]}"; do
  name="$(basename "$f")"
  if python3 "$TOOL" info "$f" >/dev/null 2>&1; then
    echo "  ok    $name"
  else
    echo "  FAIL  $name"
    python3 "$TOOL" info "$f" >/dev/null || true
    fail=1
  fi
done

if [[ -n "$SERVER" ]]; then
  echo "== client/server parity =="
  for f in "${dbc_files[@]}"; do
    name="$(basename "$f")"
    other="$SERVER/$name"
    if [[ ! -f "$other" ]]; then
      echo "  WARN  $name has no server copy in $SERVER (fine only if you use a *_dbc SQL overlay)"
      continue
    fi
    if python3 "$TOOL" verify-pair "$f" "$other" >/dev/null 2>&1; then
      echo "  ok    $name"
    else
      echo "  FAIL  $name differs from server copy"
      python3 "$TOOL" verify-pair "$f" "$other" || true
      fail=1
    fi
  done
else
  echo "== client/server parity: skipped (no server dir given) =="
fi

echo "== referential integrity =="
ref_check() { # table field target label
  local table="$1" field="$2" target="$3" label="$4"
  if [[ -f "$CLIENT/$table" && -f "$CLIENT/$target" ]]; then
    if python3 "$TOOL" refs "$CLIENT/$table" --field "$field" --must-exist "$CLIENT/$target" >/dev/null 2>&1; then
      echo "  ok    $label"
    else
      echo "  FAIL  $label"
      python3 "$TOOL" refs "$CLIENT/$table" --field "$field" --must-exist "$CLIENT/$target" || true
      fail=1
    fi
  else
    echo "  skip  $label (missing $table or $target)"
  fi
}
ref_check Item.dbc  5 ItemDisplayInfo.dbc "Item.dbc.DisplayInfoID -> ItemDisplayInfo.dbc"
ref_check Spell.dbc 133 SpellIcon.dbc      "Spell.dbc.SpellIconID -> SpellIcon.dbc"
ref_check Spell.dbc 131 SpellVisual.dbc    "Spell.dbc.SpellVisualID_1 -> SpellVisual.dbc"

echo
if [[ $fail -eq 0 ]]; then
  echo "preflight: PASS - safe to pack"
else
  echo "preflight: FAIL - fix the items above before packing"
fi
exit $fail

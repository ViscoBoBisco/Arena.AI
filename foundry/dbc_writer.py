"""Lossless DBC writer: read -> apply delta -> write -> verify.

``tools/dbc_tool.py`` is read-only by design. This module is the write side
the foundry needs, with the safety properties a patch pipeline requires:

* untouched records are byte-identical after a round trip;
* string-block offsets are remapped, never reinterpreted — existing strings
  keep their bytes, new strings are appended;
* duplicate IDs, field-count drift and out-of-range string offsets are
  refused before anything is written;
* the output is re-loaded and compared against the input before return.

Delta format (JSON): ``{"table": "Item.dbc", "field_count": 8,
"rows": [{"id": 800001, "fields": [...]}]}`` — ``fields`` is the full
record (field 0 is the id). A row whose id already exists replaces it;
otherwise it is appended. Rows are emitted sorted by id for determinism.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

MAGIC = b"WDBC"
HEADER = struct.Struct("<4sIIII")


class DbcWriteError(Exception):
    pass


@dataclass
class DbcFile:
    field_count: int
    records: List[Tuple[int, ...]]
    strings: bytes  # raw string block

    @classmethod
    def load(cls, path: Path) -> "DbcFile":
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise DbcWriteError(f"{path}: cannot read ({exc})") from exc
        if len(raw) < HEADER.size:
            raise DbcWriteError(f"{path}: shorter than a DBC header")
        magic, rc, fc, rs, ss = HEADER.unpack_from(raw, 0)
        if magic != MAGIC:
            raise DbcWriteError(f"{path}: bad magic {magic!r}")
        if rs != fc * 4:
            raise DbcWriteError(f"{path}: record size {rs} != fieldCount*4")
        expected = HEADER.size + rc * rs + ss
        if expected != len(raw):
            raise DbcWriteError(f"{path}: size mismatch (header {expected}, file {len(raw)})")
        fmt = struct.Struct("<" + "i" * fc)
        off = HEADER.size
        records = []
        for _ in range(rc):
            records.append(fmt.unpack_from(raw, off))
            off += rs
        return cls(field_count=fc, records=records,
                   strings=raw[HEADER.size + rc * rs:])

    def to_bytes(self) -> bytes:
        fmt = struct.Struct("<" + "i" * self.field_count)
        body = b"".join(fmt.pack(*r) for r in self.records)
        header = HEADER.pack(MAGIC, len(self.records), self.field_count,
                             self.field_count * 4, len(self.strings))
        return header + body + self.strings

    def string_at(self, offset: int) -> str:
        if offset < 0 or offset >= len(self.strings):
            return ""
        end = self.strings.find(b"\0", offset)
        if end == -1:
            end = len(self.strings)
        return self.strings[offset:end].decode("utf-8", "replace")


def apply_delta(base: DbcFile, rows: List[dict], *,
                string_fields: Optional[List[int]] = None) -> DbcFile:
    """Apply delta rows to *base*, returning a new DbcFile.

    ``string_fields`` lists field indices that hold string-block offsets;
    their values in delta rows are given as literal strings and are
    appended to the string block. All other fields must be ints.
    """
    string_fields = list(string_fields or [])
    by_id: Dict[int, Tuple[int, ...]] = {}
    for rec in base.records:
        if rec[0] in by_id:
            raise DbcWriteError(f"base DBC has duplicate id {rec[0]}")
        by_id[rec[0]] = rec

    new_strings = bytearray(base.strings)
    seen_delta_ids = set()
    # offset 0 is the empty string in every stock DBC; keep it.
    for row in rows:
        if not isinstance(row, dict):
            raise DbcWriteError(f"delta row must be an object, got {row!r}")
        fields = row.get("fields")
        if not isinstance(fields, list) or len(fields) != base.field_count:
            raise DbcWriteError(
                f"row id={row.get('id')!r}: {len(fields) if isinstance(fields, list) else 'non-list'}"
                f" fields, expected {base.field_count}")
        out: List[int] = []
        for i, value in enumerate(fields):
            if i in string_fields:
                if not isinstance(value, str):
                    raise DbcWriteError(f"row id={row.get('id')!r}: string field {i} must be str")
                if value == "":
                    out.append(0)
                else:
                    encoded = value.encode("utf-8") + b"\0"
                    out.append(len(new_strings))
                    new_strings.extend(encoded)
            else:
                if not isinstance(value, int) or isinstance(value, bool):
                    raise DbcWriteError(f"row id={row.get('id')!r}: field {i} must be int")
                if value < -(2 ** 31) or value > (2 ** 31 - 1):
                    raise DbcWriteError(f"row id={row.get('id')!r}: field {i} outside signed 32-bit range")
                out.append(value)
        row_id = row.get("id")
        if not isinstance(row_id, int) or isinstance(row_id, bool):
            raise DbcWriteError(f"row id must be an integer, got {row_id!r}")
        if out[0] != row_id:
            raise DbcWriteError(f"row id={row_id!r} but fields[0]={out[0]}")
        if out[0] in seen_delta_ids:
            raise DbcWriteError(f"delta has duplicate id {out[0]}")
        seen_delta_ids.add(out[0])
        by_id[out[0]] = tuple(out)

    records = [by_id[k] for k in sorted(by_id)]
    return DbcFile(field_count=base.field_count, records=records,
                   strings=bytes(new_strings))


def verify_roundtrip(original: DbcFile, written: DbcFile, *,
                     string_fields: Optional[List[int]] = None,
                     replaced_ids: Optional[List[int]] = None) -> List[str]:
    """Check unchanged rows, string-block preservation, and declared offsets.

    DBC fields are not self-describing: treating every integer as a string
    offset rejects valid numeric data. Callers must identify string fields
    from a known table schema; no offset checks are made otherwise.
    """
    problems = []
    string_fields = list(string_fields or [])
    replaced = set(replaced_ids or [])
    orig_by_id = {r[0]: r for r in original.records}
    new_by_id = {r[0]: r for r in written.records}
    for rid, rec in orig_by_id.items():
        if rid not in new_by_id:
            problems.append(f"id {rid} lost")
        elif rid not in replaced and new_by_id[rid] != rec:
            problems.append(f"untouched record {rid} changed")
    if not written.strings.startswith(original.strings):
        problems.append("string block was rewritten instead of appended to")
    if written.field_count != original.field_count:
        problems.append("field count changed")
    for rec in written.records:
        for index in string_fields:
            if index < 0 or index >= len(rec):
                problems.append(f"string field index {index} outside record")
                break
            offset = rec[index]
            if offset < 0 or offset >= len(written.strings):
                problems.append(f"record {rec[0]}: string offset {offset} outside string block")
                break
    return problems


def write_delta(base_path: Path, delta: dict, out_path: Path) -> dict:
    """Full pipeline: load base, apply delta, verify, write. Returns summary."""
    base = DbcFile.load(base_path)
    if delta.get("field_count") not in (None, base.field_count):
        raise DbcWriteError(
            f"delta field_count {delta['field_count']} != base {base.field_count}")
    result = apply_delta(base, delta.get("rows") or [],
                         string_fields=delta.get("string_fields"))
    problems = verify_roundtrip(
        base, result,
        string_fields=delta.get("string_fields"),
        replaced_ids=[row["id"] for row in delta.get("rows") or [] if row.get("id") in {r[0] for r in base.records}],
    )
    if problems:
        raise DbcWriteError("round-trip verification failed: " + "; ".join(problems[:5]))
    data = result.to_bytes()
    # independent re-parse of what we are about to write
    reparsed = DbcFile.load_bytes(data)
    if len(reparsed.records) != len(result.records):
        raise DbcWriteError("re-parse of output disagrees with in-memory result")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(data)
    return {"table": delta.get("table", base_path.name),
            "records": len(result.records),
            "added": len(result.records) - len(base.records),
            "bytes": len(data)}


def load_bytes(data: bytes) -> DbcFile:
    import tempfile
    with tempfile.NamedTemporaryFile(delete=False) as fh:
        fh.write(data)
        name = fh.name
    try:
        return DbcFile.load(Path(name))
    finally:
        Path(name).unlink(missing_ok=True)


# attach as classmethod-like helper without shadowing load()
DbcFile.load_bytes = staticmethod(load_bytes)  # type: ignore[attr-defined]


def delta_from_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DbcWriteError(f"{path}: cannot read delta ({exc})") from exc

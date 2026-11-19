"""Disposable MySQL contract used to qualify the Portable Hole module schema."""
from __future__ import annotations

import os

PREFIX = "arena_foundry_disposable_"
MAX_AMOUNT = 1_000_000_000
MAX_BALANCE = 9_000_000_000_000_000


def connect(pymysql, guid_database=None):
    database = guid_database or os.environ["ARENA_MYSQL_DATABASE"]
    if not database.startswith(PREFIX):
        raise ValueError("refusing non-disposable Portable Hole database")
    return pymysql.connect(
        host=os.environ["ARENA_MYSQL_HOST"],
        port=int(os.environ.get("ARENA_MYSQL_PORT", "3306")),
        user=os.environ["ARENA_MYSQL_USER"],
        password=os.environ["ARENA_MYSQL_PASSWORD"],
        database=database, charset="utf8mb4", autocommit=True,
    )


def _fingerprint(action, item, amount, reservation):
    return f"{action}|{item}|{amount}|{reservation}"


def _receipt(cursor, guid, request_id, fingerprint):
    cursor.execute(
        "SELECT fingerprint,action,status,item_entry,amount,COALESCE(reservation_id,''),"
        "balance_after,reserved_after,source,actor_guid FROM portable_hole_receipts "
        "WHERE guid=%s AND request_id=%s", (guid, request_id))
    row = cursor.fetchone()
    if not row:
        return None
    if row[0] != fingerprint:
        raise ValueError("request id reused with a different payload")
    return row


def _apply_credit(cursor, guid, item, amount, request_id, source="TEST_SOURCE", actor=1):
    fingerprint = _fingerprint("CREDIT", item, amount, "")
    cursor.execute(
        "INSERT INTO portable_hole_commodities(guid,item_entry,balance,reserved) "
        "VALUES(%s,%s,0,0) ON DUPLICATE KEY UPDATE guid=VALUES(guid)", (guid, item))
    cursor.execute(
        "INSERT IGNORE INTO portable_hole_receipts "
        "(guid,request_id,fingerprint,action,status,item_entry,amount,source,actor_guid) "
        "VALUES (%s,%s,%s,'CREDIT','PENDING',%s,%s,%s,%s)",
        (guid, request_id, fingerprint, item, amount, source, actor))
    cursor.execute(
        "UPDATE portable_hole_commodities SET "
        "last_request_id=IF(balance <= %s-%s,%s,last_request_id), "
        "balance=IF(last_request_id=%s,balance+%s,balance) "
        "WHERE guid=%s AND item_entry=%s AND EXISTS (SELECT 1 FROM portable_hole_receipts "
        "WHERE guid=%s AND request_id=%s AND status='PENDING')",
        (MAX_BALANCE, amount, request_id, request_id, amount,
         guid, item, guid, request_id))
    cursor.execute(
        "UPDATE portable_hole_receipts r LEFT JOIN portable_hole_commodities b "
        "ON b.guid=r.guid AND b.item_entry=r.item_entry SET "
        "r.status=IF(b.last_request_id=%s,'CREDITED','DENIED_CAPACITY'), "
        "r.balance_after=COALESCE(b.balance,0),r.reserved_after=COALESCE(b.reserved,0) "
        "WHERE r.guid=%s AND r.request_id=%s AND r.status='PENDING'",
        (request_id, guid, request_id))


def credit(guid, item, amount, request_id, *, source="TEST_SOURCE", actor=1):
    if not (0 < item <= 0xFFFFFFFF and 0 < amount <= MAX_AMOUNT):
        raise ValueError("bad commodity or amount")
    conn = connect(__import__("pymysql"))
    try:
        conn.begin()
        with conn.cursor() as cursor:
            _apply_credit(cursor, guid, item, amount, request_id, source, actor)
        conn.commit()
        with conn.cursor() as cursor:
            return _receipt(cursor, guid, request_id, _fingerprint("CREDIT", item, amount, ""))
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def reserve(guid, item, amount, request_id):
    if not (0 < item <= 0xFFFFFFFF and 0 < amount <= MAX_AMOUNT):
        raise ValueError("bad commodity or amount")
    fingerprint = _fingerprint("RESERVE", item, amount, request_id)
    conn = connect(__import__("pymysql"))
    try:
        conn.begin()
        with conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO portable_hole_commodities(guid,item_entry,balance,reserved) "
                "VALUES(%s,%s,0,0) ON DUPLICATE KEY UPDATE guid=VALUES(guid)", (guid, item))
            cursor.execute(
                "INSERT IGNORE INTO portable_hole_receipts "
                "(guid,request_id,fingerprint,action,status,item_entry,amount,reservation_id,source,actor_guid) "
                "VALUES(%s,%s,%s,'RESERVE','PENDING',%s,%s,%s,'TEST_PLAYER',%s)",
                (guid, request_id, fingerprint, item, amount, request_id, guid))
            cursor.execute(
                "UPDATE portable_hole_commodities SET "
                "last_request_id=IF(balance-reserved >= %s,%s,last_request_id), "
                "reserved=IF(last_request_id=%s,reserved+%s,reserved) "
                "WHERE guid=%s AND item_entry=%s AND EXISTS (SELECT 1 FROM portable_hole_receipts "
                "WHERE guid=%s AND request_id=%s AND status='PENDING')",
                (amount, request_id, request_id, amount, guid, item, guid, request_id))
            cursor.execute(
                "UPDATE portable_hole_receipts r JOIN portable_hole_commodities b "
                "ON b.guid=r.guid AND b.item_entry=r.item_entry SET "
                "r.status=IF(b.last_request_id=%s,'RESERVED','DENIED_FUNDS'), "
                "r.balance_after=b.balance,r.reserved_after=b.reserved "
                "WHERE r.guid=%s AND r.request_id=%s AND r.status='PENDING'",
                (request_id, guid, request_id))
            cursor.execute(
                "INSERT IGNORE INTO portable_hole_reservations "
                "(guid,reservation_id,item_entry,amount,state) "
                "SELECT guid,%s,%s,%s,'ACTIVE' FROM portable_hole_commodities "
                "WHERE guid=%s AND item_entry=%s AND last_request_id=%s AND EXISTS "
                "(SELECT 1 FROM portable_hole_receipts WHERE guid=%s AND request_id=%s "
                "AND status='RESERVED')",
                (request_id, item, amount, guid, item, request_id, guid, request_id))
        conn.commit()
        with conn.cursor() as cursor:
            return _receipt(cursor, guid, request_id, fingerprint)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def transition(guid, reservation_id, request_id, action):
    if action not in ("RELEASE", "CONSUME"):
        raise ValueError("unsupported transition")
    fingerprint = _fingerprint(action, 0, 0, reservation_id)
    conn = connect(__import__("pymysql"))
    try:
        conn.begin()
        with conn.cursor() as cursor:
            cursor.execute("SELECT item_entry,amount FROM portable_hole_reservations "
                           "WHERE guid=%s AND reservation_id=%s", (guid, reservation_id))
            reservation = cursor.fetchone()
            if not reservation:
                raise ValueError("reservation not found")
            item, amount = reservation
            fingerprint = _fingerprint(action, item, amount, reservation_id)
            cursor.execute(
                "INSERT IGNORE INTO portable_hole_receipts "
                "(guid,request_id,fingerprint,action,status,item_entry,amount,reservation_id,source,actor_guid) "
                "VALUES(%s,%s,%s,%s,'PENDING',%s,%s,%s,'TEST_PLAYER',%s)",
                (guid, request_id, fingerprint, action, item, amount, reservation_id, guid))
            cursor.execute(
                "UPDATE portable_hole_commodities SET "
                "last_request_id=IF(EXISTS (SELECT 1 FROM portable_hole_reservations "
                "WHERE guid=%s AND reservation_id=%s AND state='ACTIVE'),%s,last_request_id), "
                "balance=IF(%s='CONSUME' AND last_request_id=%s,balance-%s,balance), "
                "reserved=IF(last_request_id=%s,reserved-%s,reserved) "
                "WHERE guid=%s AND item_entry=%s AND EXISTS (SELECT 1 FROM portable_hole_receipts "
                "WHERE guid=%s AND request_id=%s AND status='PENDING')",
                (guid, reservation_id, request_id, action, request_id, amount,
                 request_id, amount, guid, item, guid, request_id))
            cursor.execute(
                "UPDATE portable_hole_reservations SET state=%s WHERE guid=%s "
                "AND reservation_id=%s AND state='ACTIVE' AND EXISTS "
                "(SELECT 1 FROM portable_hole_receipts WHERE guid=%s AND request_id=%s AND status='PENDING')",
                ("RELEASED" if action == "RELEASE" else "CONSUMED",
                 guid, reservation_id, guid, request_id))
            cursor.execute(
                "UPDATE portable_hole_receipts r LEFT JOIN portable_hole_commodities b "
                "ON b.guid=r.guid AND b.item_entry=r.item_entry SET "
                "r.status=IF(b.last_request_id=%s,%s,'DENIED_RESERVATION'), "
                "r.balance_after=COALESCE(b.balance,0),r.reserved_after=COALESCE(b.reserved,0) "
                "WHERE r.guid=%s AND r.request_id=%s AND r.status='PENDING'",
                (request_id, "RELEASED" if action == "RELEASE" else "CONSUMED", guid, request_id))
        conn.commit()
        with conn.cursor() as cursor:
            return _receipt(cursor, guid, request_id, fingerprint)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

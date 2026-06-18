#!/usr/bin/env python3

import argparse
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(
        description="Update the REALITY target for one 3X-UI inbound."
    )
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--target", required=True)
    return parser.parse_args()


def validate_target(target):
    host, separator, port = target.rpartition(":")
    if not separator or not host or not port.isdigit():
        raise ValueError("target must use host:port format")
    if not 1 <= int(port) <= 65535:
        raise ValueError("target port must be between 1 and 65535")


def main():
    args = parse_args()
    validate_target(args.target)

    if not args.db.is_file():
        raise FileNotFoundError(f"database does not exist: {args.db}")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup = args.db.with_name(f"{args.db.name}.bak.{stamp}")
    shutil.copy2(args.db, backup)

    connection = sqlite3.connect(args.db)
    try:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(inbounds)")
        }
        stream_column = next(
            (name for name in ("stream_settings", "streamSettings") if name in columns),
            None,
        )
        if stream_column is None:
            raise RuntimeError("inbounds table has no stream settings column")

        rows = connection.execute(
            f'SELECT id, protocol, "{stream_column}" FROM inbounds WHERE port = ?',
            (args.port,),
        ).fetchall()
        if len(rows) != 1:
            raise RuntimeError(
                f"expected one inbound on port {args.port}, found {len(rows)}"
            )

        inbound_id, protocol, raw_stream = rows[0]
        if protocol != "vless":
            raise RuntimeError(f"inbound {inbound_id} uses protocol {protocol}, not vless")

        stream = json.loads(raw_stream)
        if stream.get("security") != "reality":
            raise RuntimeError(f"inbound {inbound_id} does not use REALITY")

        reality = stream.get("realitySettings")
        if not isinstance(reality, dict):
            raise RuntimeError(f"inbound {inbound_id} has no realitySettings object")

        previous = reality.get("target") or reality.get("dest")
        reality["target"] = args.target
        reality.pop("dest", None)

        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            f'UPDATE inbounds SET "{stream_column}" = ? WHERE id = ?',
            (json.dumps(stream, separators=(",", ":")), inbound_id),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    print(f"backup={backup}")
    print(f"inbound={inbound_id} target={previous!r}->{args.target!r}")


if __name__ == "__main__":
    main()

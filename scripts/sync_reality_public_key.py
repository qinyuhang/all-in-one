#!/usr/bin/env python3

import argparse
import json
import shutil
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(
        description="Derive and persist 3X-UI REALITY public-key metadata."
    )
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--xray-binary", required=True, type=Path)
    return parser.parse_args()


def find_stream_column(connection):
    columns = {row[1] for row in connection.execute("PRAGMA table_info(inbounds)")}
    stream_column = next(
        (name for name in ("stream_settings", "streamSettings") if name in columns),
        None,
    )
    if stream_column is None:
        raise RuntimeError("inbounds table has no stream settings column")
    return stream_column


def derive_public_key(binary, private_key):
    result = subprocess.run(
        [str(binary), "x25519", "-i", private_key],
        check=True,
        capture_output=True,
        text=True,
    )
    values = {}
    output = "\n".join((result.stdout, result.stderr))
    for line in output.splitlines():
        key, separator, value = line.partition(":")
        if separator:
            normalized = "".join(character for character in key.lower() if character.isalnum())
            values[normalized] = value.strip()

    public_key = (
        values.get("passwordpublickey")
        or values.get("password")
        or values.get("publickey")
    )
    if not public_key:
        raise RuntimeError("xray did not return a Password/PublicKey value")
    return public_key


def main():
    args = parse_args()
    if not args.db.is_file():
        raise FileNotFoundError(f"database does not exist: {args.db}")
    if not args.xray_binary.is_file():
        raise FileNotFoundError(f"xray binary does not exist: {args.xray_binary}")

    connection = sqlite3.connect(args.db)
    try:
        stream_column = find_stream_column(connection)
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
        private_key = reality.get("privateKey")
        if not private_key:
            raise RuntimeError(f"inbound {inbound_id} has no REALITY private key")

        public_key = derive_public_key(args.xray_binary, private_key)
        metadata = reality.setdefault("settings", {})
        if not isinstance(metadata, dict):
            raise RuntimeError("realitySettings.settings is not an object")
        already_current = metadata.get("publicKey") == public_key
        metadata["publicKey"] = public_key
        metadata.setdefault("fingerprint", "chrome")
        metadata.setdefault("spiderX", "/")

        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        backup = args.db.with_name(f"{args.db.name}.bak.{stamp}")
        shutil.copy2(args.db, backup)

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
    print(f"inbound={inbound_id} publicKey={'unchanged' if already_current else 'updated'}")


if __name__ == "__main__":
    main()

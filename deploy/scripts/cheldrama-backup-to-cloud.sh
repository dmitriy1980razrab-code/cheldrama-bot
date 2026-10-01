#!/usr/bin/env bash
set -euo pipefail

PROJECT="/opt/cheldrama-bot"
BUCKET="cheldrama-bot-backups-20260929"
PYTHON="$PROJECT/.venv/bin/python"
YC="/home/botadmin/yandex-cloud/bin/yc"

cd "$PROJECT"
"$PYTHON" scripts/backup_database.py

BACKUP_FILE="$(
  find "$PROJECT/backups" -maxdepth 1 -type f -name 'theatre-*.sqlite3.gz' \
    -printf '%T@ %p\n' | sort -n | tail -1 | cut -d' ' -f2-
)"

test -n "$BACKUP_FILE"
CHECKSUM_FILE="${BACKUP_FILE}.sha256"
test -f "$CHECKSUM_FILE"

"$YC" storage s3 cp "$BACKUP_FILE" \
  "s3://$BUCKET/database/$(basename "$BACKUP_FILE")"

"$YC" storage s3 cp "$CHECKSUM_FILE" \
  "s3://$BUCKET/database/$(basename "$CHECKSUM_FILE")"

echo "Внешняя резервная копия успешно загружена."

#!/usr/bin/env bash
set -euo pipefail

PROJECT="/opt/cheldrama-bot"
BUCKET="cheldrama-bot-backups-20260929"
PYTHON="$PROJECT/.venv/bin/python"
YC="/home/botadmin/yandex-cloud/bin/yc"

cd "$PROJECT"
BACKUP_OUTPUT="$("$PYTHON" scripts/backup_database.py)"
printf '%s\n' "$BACKUP_OUTPUT"

mapfile -t BACKUP_NAMES < <(
  printf '%s\n' "$BACKUP_OUTPUT" | sed -n 's/^Копия создана: //p'
)
test "${#BACKUP_NAMES[@]}" -gt 0

for BACKUP_NAME in "${BACKUP_NAMES[@]}"; do
  case "$BACKUP_NAME" in
    theatre-*.sqlite3.gz|subscribers-*.sqlite3.gz) ;;
    *) echo "Недопустимое имя резервной копии." >&2; exit 1 ;;
  esac

  BACKUP_FILE="$PROJECT/backups/$BACKUP_NAME"
  CHECKSUM_FILE="${BACKUP_FILE}.sha256"
  test -f "$BACKUP_FILE"
  test -f "$CHECKSUM_FILE"

  "$YC" storage s3 cp "$BACKUP_FILE" \
    "s3://$BUCKET/database/$(basename "$BACKUP_FILE")"

  "$YC" storage s3 cp "$CHECKSUM_FILE" \
    "s3://$BUCKET/database/$(basename "$CHECKSUM_FILE")"
done

echo "Внешняя резервная копия успешно загружена."

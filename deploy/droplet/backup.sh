#!/usr/bin/env bash
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-./backups}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

mkdir -p "$BACKUP_DIR"

docker compose exec -T postgres pg_dump \
  -U "$POSTGRES_USER" \
  -d "$POSTGRES_DB" \
  --format=custom \
  --file="/tmp/${POSTGRES_DB}_${STAMP}.dump"

docker compose cp "postgres:/tmp/${POSTGRES_DB}_${STAMP}.dump" \
  "${BACKUP_DIR}/${POSTGRES_DB}_${STAMP}.dump"

docker compose exec -T postgres rm "/tmp/${POSTGRES_DB}_${STAMP}.dump"

echo "Wrote ${BACKUP_DIR}/${POSTGRES_DB}_${STAMP}.dump"

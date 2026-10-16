#!/bin/sh
# Zaxirani tiklashni tekshirish: oxirgi nusxa VAQTINCHALIK bazaga tiklanadi va asosiy baza bilan solishtiriladi.
# Ishchi bazaga tegmaydi (alohida restore_check_* bazasi yaratiladi va oxirida o'chiriladi).
#
# Ishga tushirish (postgres konteyneri ichida, zaxira papkasi /backups ulangan):
#   docker compose -f docker-compose.prod.yml run --rm --entrypoint sh backup /scripts/restore_check.sh [fayl.sql.gz]
# Yoki haftada bir cron orqali. Chiqish kodi 0 — tiklash muvaffaqiyatli va ma'lumotlar mos.
set -eu

BACKUP_DIR="${BACKUP_DIR:-/backups}"
PGHOST="${PGHOST:-db}"
FILE="${1:-$(ls -1t "${BACKUP_DIR}"/yurtora_crm_*.sql.gz 2>/dev/null | head -n 1)}"
[ -n "$FILE" ] && [ -f "$FILE" ] || { echo "[restore-check] XATO: zaxira fayli topilmadi" >&2; exit 2; }

TMPDB="restore_check_$$"
export PGPASSWORD="${PGPASSWORD:-${POSTGRES_PASSWORD:-}}"
PSQL="psql -h $PGHOST -U $POSTGRES_USER -v ON_ERROR_STOP=1 -X -q -A -t"

cleanup() { psql -h "$PGHOST" -U "$POSTGRES_USER" -d postgres -X -q -c "DROP DATABASE IF EXISTS ${TMPDB}" >/dev/null 2>&1 || true; }
trap cleanup EXIT

echo "[restore-check] fayl: $FILE"
gzip -t "$FILE"
psql -h "$PGHOST" -U "$POSTGRES_USER" -d postgres -X -q -c "CREATE DATABASE ${TMPDB}"
gunzip -c "$FILE" | $PSQL -d "$TMPDB" > /dev/null

fail=0
check() {  # check <nom> <SQL>
  src="$($PSQL -d "$POSTGRES_DB" -c "$2")"
  dst="$($PSQL -d "$TMPDB" -c "$2")"
  if [ "$src" = "$dst" ]; then
    echo "[restore-check] OK    $1: $dst"
  else
    # Zaxira olingandan keyin asosiy bazada yangi yozuvlar paydo bo'lgan bo'lishi mumkin
    echo "[restore-check] FARQ  $1: asosiy=$src tiklangan=$dst"
    fail=1
  fi
}
check "o'quvchilar" "SELECT count(*) FROM students_student"
check "guruhlar" "SELECT count(*) FROM groups_group"
check "a'zoliklar" "SELECT count(*) FROM groups_enrollment"
check "foydalanuvchilar" "SELECT count(*) FROM accounts_user"
check "kitob yozuvlari" "SELECT count(*) FROM finance_ledgerentry"
check "kitob yig'indisi (so'm)" "SELECT coalesce(sum(amount),0) FROM finance_ledgerentry"

if [ "$fail" -eq 0 ]; then
  echo "[restore-check] MUVAFFAQIYATLI: zaxira tiklandi va asosiy baza bilan mos"
else
  echo "[restore-check] DIQQAT: tiklandi, lekin sonlar farq qiladi (zaxiradan keyin yangi yozuvlar bo'lishi mumkin)" >&2
  exit 1
fi

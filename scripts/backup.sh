#!/bin/sh
# Zaxira: pg_dump -> /backups/yurtora_crm_YYYYmmdd_HHMMSS.sql.gz (KEEP_DAYS kundan eskilari o'chiriladi).
#
# Har nusxa tekshiriladi (gzip yaxlitligi va minimal hajm). Natija fayllarda:
#   /backups/LAST_OK      — oxirgi muvaffaqiyatli zaxira vaqti (docker healthcheck shu faylga qaraydi)
#   /backups/LAST_FAILED  — oxirgi muvaffaqiyatsiz urinish (muvaffaqiyatli zaxiradan keyin o'chiriladi)
# Serverdan tashqariga nusxa (disk buzilishidan himoya):
#   BACKUP_COPY_DIR     — ikkinchi diskka/NAS ga ulangan papka: nusxa shu yerga ham ko'chiriladi
#   BACKUP_UPLOAD_CMD   — ixtiyoriy buyruq, $BACKUP_FILE o'zgaruvchisi bilan (masalan: rclone copyto "$BACKUP_FILE" remote:yurtora/)
# RUN_ONCE=1 — bir marta bajarib chiqadi (qo'lda tekshirish uchun).
set -eu

KEEP_DAYS="${KEEP_DAYS:-14}"
INTERVAL_HOURS="${INTERVAL_HOURS:-24}"
RETRY_MINUTES="${RETRY_MINUTES:-10}"
BACKUP_DIR="${BACKUP_DIR:-/backups}"
MIN_BYTES="${MIN_BYTES:-2048}"
PGHOST="${PGHOST:-db}"

backup_once() {
  STAMP="$(date +%Y%m%d_%H%M%S)"
  OUT="${BACKUP_DIR}/yurtora_crm_${STAMP}.sql.gz"
  RAW="${OUT}.sql.tmp"
  # --clean --if-exists: mavjud bazaga ham tiklash mumkin; --no-owner: foydalanuvchi nomi boshqa bo'lsa ham ishlaydi
  if pg_dump -h "$PGHOST" -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner > "$RAW" \
      && gzip -c "$RAW" > "${OUT}.tmp" \
      && gzip -t "${OUT}.tmp" \
      && [ "$(wc -c < "${OUT}.tmp")" -ge "$MIN_BYTES" ]; then
    rm -f "$RAW"
    mv "${OUT}.tmp" "$OUT"
    date -u +%Y-%m-%dT%H:%M:%SZ > "${BACKUP_DIR}/LAST_OK"
    rm -f "${BACKUP_DIR}/LAST_FAILED"
    echo "[backup] OK $OUT ($(wc -c < "$OUT") bayt)"
    copy_offsite "$OUT"
  else
    rm -f "$RAW" "${OUT}.tmp"
    date -u +%Y-%m-%dT%H:%M:%SZ > "${BACKUP_DIR}/LAST_FAILED"
    echo "[backup] XATO: zaxira yaratilmadi yoki tekshiruvdan o'tmadi" >&2
    return 1
  fi
}

copy_offsite() {
  if [ -n "${BACKUP_COPY_DIR:-}" ]; then
    if cp "$1" "${BACKUP_COPY_DIR}/" && echo "[backup] nusxa ko'chirildi: ${BACKUP_COPY_DIR}"; then
      find "$BACKUP_COPY_DIR" -name 'yurtora_crm_*.sql.gz' -mtime +"$KEEP_DAYS" -delete
    else
      echo "[backup] XATO: ${BACKUP_COPY_DIR} ga ko'chirib bo'lmadi" >&2
    fi
  fi
  if [ -n "${BACKUP_UPLOAD_CMD:-}" ]; then
    if BACKUP_FILE="$1" sh -c "$BACKUP_UPLOAD_CMD"; then
      echo "[backup] tashqi xotiraga yuborildi"
    else
      echo "[backup] XATO: BACKUP_UPLOAD_CMD bajarilmadi" >&2
    fi
  fi
}

while true; do
  status=0
  backup_once || status=$?
  find "$BACKUP_DIR" -name 'yurtora_crm_*.sql.gz' -mtime +"$KEEP_DAYS" -delete
  if [ "${RUN_ONCE:-0}" = "1" ]; then
    exit "$status"
  fi
  if [ "$status" -ne 0 ]; then
    # Muvaffaqiyatsiz urinishdan keyin 24 soat kutmaymiz (masalan, birinchi ishga tushishda baza hali bo'sh)
    sleep "$((RETRY_MINUTES * 60))"
    continue
  fi
  sleep "$((INTERVAL_HOURS * 3600))"
done

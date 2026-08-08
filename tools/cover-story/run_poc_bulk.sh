#!/bin/bash
# Bulk PoC run: all stages unattended, then render the NFS review page.
# Usage: run_poc_bulk.sh [--output-dir ...] [extra runner flags...]
# The review page is written into the run root (shared NFS drive) as review.html; a partial page
# is still produced if a stage fails, so the operator can review whatever exists.
set -u
cd "$(dirname "$0")"
LOG=/tmp/poc_bulk.log
OUTDIR=""
EXTRA=""
while [ $# -gt 0 ]; do
    case "$1" in
        --output-dir) OUTDIR="$2"; shift 2 ;;
        *) EXTRA="$EXTRA $1"; shift ;;
    esac
done
if [ -z "$OUTDIR" ]; then
    OUTDIR=/mnt/Misc/sd/cover-story/grey-garmentref-poc-$(date +%Y%m%d-%H%M)
fi
echo "== run root: $OUTDIR" | tee "$LOG"
# Full staged pipeline, envelope auto-accepted, no per-stage stops.
python3 -u run_qwen2512_skin_head_clothes_poc.py \
    --grey-carrier --clothes-mode garment-ref --extract-mode birefnet \
    --auto-accept-envelope --output-dir "$OUTDIR" $EXTRA >> "$LOG" 2>&1
RC=$?
echo "== pipeline rc=$RC" | tee -a "$LOG"
# Render the review page whether or not the run finished.
python3 poc_review.py --root "$OUTDIR" >> "$LOG" 2>&1
echo "== review page: $OUTDIR/review.html" | tee -a "$LOG"
exit $RC

#!/usr/bin/env bash
# Investigate what the go-to slot byte does beyond 01/02 (slot 0x3F overwrote stored
# presets). Each step sends a few go-to frames with one slot value and a distinct target
# near the current height, then diffs the 9d 02 values. Halts immediately if a travel
# limit changes. Transcript: captures/slots_<timestamp>.txt, raw frames:
# captures/probe_slot_<slot>.log.
cd "$(dirname "$0")/.." || exit 1
PY=.venv/bin/python
OUT="captures/slots_$(date +%Y%m%d_%H%M%S).txt"
FRAMES=1

step() {  # step SLOT TARGET "what we learn"; sets RC
    local slot=$1 target=$2 note=$3
    {
        echo
        echo "=================================================================="
        echo "slot $slot, target $target, $FRAMES frame(s): $note"
    } | tee -a "$OUT"
    read -r -p "    Enter = run, s = skip, q = quit > " ans
    case $ans in
        q) exit 0 ;;
        s) echo "SKIPPED" | tee -a "$OUT"; RC=skip; return ;;
    esac
    $PY tools/probe.py --log "captures/probe_slot_${slot}.log" \
        slotprobe "$slot" "$target" --frames "$FRAMES" --write-ok 2>&1 | tee -a "$OUT"
    RC=${PIPESTATUS[0]}
    case $RC in
        0) echo "result: nothing written" ;;
        2) echo "result: stored values changed" ;;
        *) echo "result: exit $RC" ;;
    esac | tee -a "$OUT"
    if [ "$RC" = 3 ]; then
        echo "TRAVEL LIMITS CHANGED: stopping here." | tee -a "$OUT"
        exit 3
    fi
    [ "$RC" = 0 ] || [ "$RC" = 2 ] || { echo "unexpected exit, stopping." | tee -a "$OUT"; exit 1; }
}

echo "Slot investigation, transcript: $OUT" | tee "$OUT"

# Steps as SLOT:TARGET:FRAMES arguments, default = round 2.
# Round 1, 1 frame each: 0x3f -> M1, 0x1f -> M3,
# 0x04 0x08 start a go-to, 0x10 0x20 nothing.
STEPS=("$@")
[ ${#STEPS[@]} -gt 0 ] || STEPS=(
    "0x3f:+13:1"    # fixed mapping (M1 again) or moving pointer (another field)?
    "0x1f:+17:1"    # M3 again?
    "0x3f:+19:10"   # does a 1 s stream spill into M2 / [5]?
    "0x3f:+21:30"   # 3 s stream
)
for s in "${STEPS[@]}"; do
    IFS=: read -r slot target FRAMES <<< "$s"
    step "$slot" "$target" "round 2"
done

echo | tee -a "$OUT"
echo "Done. Transcript: $OUT" | tee -a "$OUT"

#!/usr/bin/env bash
# Remaining Phase 0 hardware tests. Interactive: prints instructions before each test and
# waits for Enter (s = skip, q = quit). Console output is tee'd to
# captures/phase0_tests_<timestamp>.txt; raw frames of each test go to
# captures/probe_<NN>_<name>.log (appended, one "# ..." header per run).
# Optional arguments select tests by number: tools/phase0_tests.sh 09 10 13a 13b
cd "$(dirname "$0")/.." || exit 1
PY=.venv/bin/python
OUT="captures/phase0_tests_$(date +%Y%m%d_%H%M%S).txt"
ONLY=("$@")

selected() {
    [ ${#ONLY[@]} -eq 0 ] && return 0
    local t
    for t in "${ONLY[@]}"; do [ "$t" = "$1" ] && return 0; done
    return 1
}

act() {  # act NN name "what you must do" probe-args...: needs explicit confirmation
    selected "$1" || return
    local nn=$1 name=$2 note=$3
    shift 3
    {
        echo
        echo "##################################################################"
        echo "##  [$nn] $name  --  ACTION REQUIRED FROM YOU"
        echo "##"
        echo "##  $note"
        echo "##"
        echo "##  Timed cues (banner + bell) will tell you when to act."
        echo "##################################################################"
    } | tee -a "$OUT"
    local ans
    while :; do
        read -r -p "    Type y when ready (s = skip, q = quit) > " ans
        case $ans in
            y) break ;;
            q) echo "[$nn] quit" | tee -a "$OUT"; exit 0 ;;
            s) echo "[$nn] SKIPPED" | tee -a "$OUT"; return ;;
        esac
    done
    echo "[$nn] $name: probe.py $*" | tee -a "$OUT"
    $PY tools/probe.py --log "captures/probe_${nn}_${name}.log" "$@" 2>&1 | tee -a "$OUT"
    echo "[$nn] exit ${PIPESTATUS[0]}" | tee -a "$OUT"
}

run() {  # run NN name "instructions" probe-args...
    selected "$1" || return
    local nn=$1 name=$2 note=$3
    shift 3
    {
        echo
        echo "=================================================================="
        echo "[$nn] $name: probe.py $*"
        echo "    $note"
    } | tee -a "$OUT"
    read -r -p "    Enter = run, s = skip, q = quit > " ans
    case $ans in
        q) echo "[$nn] quit" | tee -a "$OUT"; exit 0 ;;
        s) echo "[$nn] SKIPPED" | tee -a "$OUT"; return ;;
    esac
    $PY tools/probe.py --log "captures/probe_${nn}_${name}.log" "$@" 2>&1 | tee -a "$OUT"
    echo "[$nn] exit ${PIPESTATUS[0]}" | tee -a "$OUT"
}

echo "Phase 0 tests, transcript: $OUT" | tee "$OUT"
echo "Vendor app must be disconnected (except test 13). Stay at the desk." | tee -a "$OUT"

run 14 min_goto "go-to 690 mm (below the 705 limit); needs the desk below ~790 mm, the probe refuses otherwise" \
    goto 690 --slot 1 --for 6
run 01 max_limit "up for 15 s: should stop at the max limit (~1251) and show AT-LIMIT" \
    up 15
run 02 max_goto "go-to 1300 mm (beyond max limit), 5 s cap" \
    goto 1300 --slot 1 --for 5
run 03 goto_stop "go-to -100 mm, stopped after 1 s: coast after stop mid go-to" \
    goto -100 --slot 1 --for 1
run 04 single_stop "down 1 s followed by a single stop frame" \
    --stops 1 down 1
# 05a/05b (slots 3 and 63) removed: slot 63 overwrote stored presets in the desk.
run 06a tiny_p5 "go-to +5 mm" \
    goto +5 --slot 1
run 06b tiny_m2 "go-to -2 mm" \
    goto -2 --slot 1
run 06c tiny_p1 "go-to +1 mm" \
    goto +1 --slot 1
run 07 reverse "up 1.5 s then down 1.5 s, no stop in between" \
    reverse 1.5 1.5
run 08a slow_up "up 2 s, streaming every 300 ms" \
    --interval 0.3 up 2
run 08b slower_down "down 2 s, streaming every 800 ms" \
    --interval 0.8 down 2
act 09 handset_vs_ble "BLE go-to -100 mm keeps streaming while you use the handset. When cued, PRESS AND HOLD handset UP; release when cued." \
    --cue "1.5:PRESS AND HOLD handset UP now" --cue "3.5:RELEASE the handset key" \
    --ignore-key goto -100 --slot 1
act 10 handset_abort "BLE go-to +100 mm. When cued, briefly PRESS any handset key: the probe should abort and stop." \
    --cue "1.5:PRESS any handset key now (briefly)" \
    goto +100 --slot 1
run 11 no_handshake "monitor 10 s without handshake (init still sent)" \
    --no-handshake monitor 10
run 12 no_init "up 1 s without init" \
    --no-init up 1
act 13a app_connected "BEFORE typing y: open the vendor app and CONNECT it to the desk. The probe then tries to connect (expect a failure)." \
    monitor 5
act 13b app_steal "BEFORE typing y: DISCONNECT/close the vendor app. The probe connects and idles 60 s; when cued, OPEN the app and try to connect." \
    --cue "5:OPEN the vendor app and CONNECT to the desk now" --cue "50:(you can stop trying with the app)" \
    idle 60

echo | tee -a "$OUT"
echo "Done. Transcript: $OUT" | tee -a "$OUT"

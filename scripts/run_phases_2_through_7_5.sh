#!/usr/bin/env bash
set -e
export NEO4J_URI=bolt://localhost:7687
export NEO4J_USER=neo4j
export NEO4J_PASSWORD=cPpNqbpjGsHYiZIPyeXFLnIT7Owrc005
DIR=/d/work/openclaude/csvs/CL650-6134
LOG=/d/work/openclaude/dumps/rebuild_v2_orchestrate.log
echo "==== orchestrate start $(date -u +%Y-%m-%dT%H:%M:%SZ) ====" | tee -a "$LOG"

for phase in phase1_5_patch.py phase2.py phase4.py phase5.py phase6.py phase6_5.py phase7.py phase7_5.py; do
    echo "" | tee -a "$LOG"
    echo "==== START $phase $(date -u +%Y-%m-%dT%H:%M:%SZ) ====" | tee -a "$LOG"
    python3 "$DIR/$phase" 2>&1 | tee -a "$LOG"
    rc=${PIPESTATUS[0]}
    echo "==== END $phase rc=$rc $(date -u +%Y-%m-%dT%H:%M:%SZ) ====" | tee -a "$LOG"
    if [ $rc -ne 0 ]; then
        echo "!! $phase FAILED rc=$rc — stopping" | tee -a "$LOG"
        exit $rc
    fi
done
echo "==== orchestrate complete $(date -u +%Y-%m-%dT%H:%M:%SZ) ====" | tee -a "$LOG"

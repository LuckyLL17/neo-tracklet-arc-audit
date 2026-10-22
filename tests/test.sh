#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
REWARD_FILE=${REWARD_FILE:-"/logs/verifier/reward.txt"}
mkdir -p "$(dirname "$REWARD_FILE")"
printf '0.000\n' > "$REWARD_FILE"
export PYTHONDONTWRITEBYTECODE=1
cd "$ROOT"
python3 -m pytest --collect-only -q tests/test_neo_audit.py >/dev/null
run_group() {
  tests="$1"; weight="$2"; passed=0; total=0
  for node in $tests; do
    total=$((total + 1))
    if python3 -m pytest -q "tests/test_neo_audit.py::$node" >/dev/null 2>&1; then passed=$((passed + 1)); fi
  done
  python3 - "$passed" "$total" "$weight" "$score_file" <<'PY'
import sys
p, t, w = map(float, sys.argv[1:4])
with open(sys.argv[4], 'a', encoding='ascii') as f: f.write(f'{w*p/t:.12f}\n')
PY
}
score_file=$(mktemp "${TMPDIR:-/tmp}/neo-score.XXXXXX")
trap 'rm -f "$score_file"' EXIT HUP INT TERM
: > "$score_file"
run_group 'test_crossing_zero_ra_is_continuous_and_accepted test_weighted_fit_suppresses_low_precision_outlier test_reference_declination_scales_ra_residual' 0.45
run_group 'test_short_arc_requires_review test_slow_motion_requires_review test_scatter_threshold_requires_review' 0.25
run_group 'test_boolean_disguised_number_rejects_without_rewriting_output test_out_of_range_coordinate_rejects test_nonmonotonic_time_rejects test_duplicate_epoch_rejects_and_preserves_sentinel test_nearly_coincident_epochs_reject_as_degenerate_geometry test_polar_tangent_plane_rejects' 0.22
run_group 'test_output_bytes_have_public_order_precision_and_newline test_repeat_run_is_byte_stable_and_leaves_no_temporary_file' 0.08
python3 - "$score_file" "$REWARD_FILE" <<'PY'
import math,sys
try:
    value=sum(float(x) for x in open(sys.argv[1],encoding='ascii'))
    if not math.isfinite(value) or not 0 <= value <= 1: value=0.0
except Exception: value=0.0
with open(sys.argv[2],'w',encoding='ascii') as f: f.write(f'{value:.3f}\n')
PY
python3 -m pytest -q tests/test_neo_audit.py

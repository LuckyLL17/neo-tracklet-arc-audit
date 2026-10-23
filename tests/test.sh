#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
RESULT_PATH=/logs/verifier/reward.txt
WORK=
FINALIZED=0

cleanup() {
    status=$?
    if [ "$FINALIZED" -ne 1 ]; then
        mkdir -p /logs/verifier 2>/dev/null || :
        printf '0.000\n' > "$RESULT_PATH" 2>/dev/null || :
    fi
    if [ -n "$WORK" ] && [ -d "$WORK" ]; then
        rm -rf "$WORK"
    fi
    exit "$status"
}
trap cleanup EXIT HUP INT TERM

if [ "$(id -u)" -ne 0 ]; then
    echo "verifier must run as root" >&2
    exit 2
fi
unset PYTHONPATH PYTEST_ADDOPTS TARGET_PROGRAM REWARD_FILE
export PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
umask 077
mkdir -p /logs/verifier
printf '0.000\n' > "$RESULT_PATH"

EXPECTED_SHA=$(awk '{print $1}' "$ROOT/tests/vendor/pytest-runtime.sha256")
ACTUAL_SHA=$(sha256sum "$ROOT/tests/vendor/pytest-runtime.tar.gz" | awk '{print $1}')
if [ "$EXPECTED_SHA" != "$ACTUAL_SHA" ]; then
    echo "offline pytest bundle hash mismatch" >&2
    exit 2
fi

WORK=$(mktemp -d "${TMPDIR:-/tmp}/neo-tracklet-verifier.XXXXXX")
SITE="$WORK/site"
TEST_FILE="$WORK/test_neo_tracklet.py"
mkdir -p "$SITE"
tar --no-xattrs -xzf "$ROOT/tests/vendor/pytest-runtime.tar.gz" -C "$SITE"
export PYTHONPATH="$SITE"
cp "$ROOT/tests/test_neo_tracklet.py" "$TEST_FILE"

python3 -m pytest --collect-only -q -p no:cacheprovider "$TEST_FILE" >/dev/null

SCORES="$WORK/scores.txt"
: > "$SCORES"
run_group() {
    weight=$1
    shift
    passed=0
    total=0
    for node in "$@"; do
        total=$((total + 1))
        if python3 -m pytest -q -p no:cacheprovider "$TEST_FILE::$node" >/dev/null 2>&1; then
            passed=$((passed + 1))
        fi
    done
    python3 - "$passed" "$total" "$weight" "$SCORES" <<'PY'
import sys
passed, total, weight = float(sys.argv[1]), float(sys.argv[2]), float(sys.argv[3])
with open(sys.argv[4], "a", encoding="ascii") as handle:
    handle.write(f"{weight * passed / total:.12f}\n")
PY
}

run_group 0.22 \
    test_wraparound_and_tangent_projection_recover_geocentric_motion \
    test_inverse_variance_weights_limit_a_low_precision_astrometric_outlier \
    test_polar_reference_tangent_plane_is_rejected_without_output_side_effect
run_group 0.28 \
    test_shared_parallax_is_recovered_from_multistation_geometry \
    test_zero_station_baselines_are_unidentifiable \
    test_affine_station_baseline_is_unidentifiable \
    test_parallax_significance_uses_the_normal_matrix_covariance \
    test_boolean_station_baseline_is_schema_error_and_preserves_existing_output
run_group 0.26 \
    test_candidate_position_is_propagated_to_the_tracklet_epoch \
    test_non_symmetric_candidate_covariance_is_rejected \
    test_nearer_mahalanobis_candidate_wins_even_when_input_order_differs \
    test_exact_association_tie_uses_candidate_id_as_last_deterministic_key \
    test_position_close_but_velocity_incompatible_candidate_is_unmatched
run_group 0.16 \
    test_unmatched_tracklet_is_rejected_and_renders_null_association_fields \
    test_low_parallax_significance_routes_an_otherwise_linked_arc_to_review \
    test_short_observing_arc_routes_a_strong_solution_to_review \
    test_large_astrometric_residual_rejects_even_with_a_catalog_link
run_group 0.08 \
    test_duplicate_epoch_is_rejected_without_rewriting_existing_report \
    test_extra_observation_field_is_rejected_without_creating_a_report \
    test_output_order_precision_newline_and_repeat_bytes_are_stable \
    test_non_positive_definite_covariance_is_catalog_error_without_output_side_effect

set +e
python3 -m pytest -q -p no:cacheprovider "$TEST_FILE" >/dev/null
FULL_RC=$?
set -e
if [ "$FULL_RC" -gt 1 ]; then
    echo "pytest collection or execution error" >&2
    exit 2
fi

python3 - "$SCORES" "$RESULT_PATH" <<'PY'
import math
import sys
try:
    with open(sys.argv[1], encoding="ascii") as handle:
        reward = sum(float(line) for line in handle)
    if not math.isfinite(reward) or reward < 0.0 or reward > 1.0:
        raise ValueError("invalid reward")
except Exception:
    reward = 0.0
with open(sys.argv[2], "w", encoding="ascii") as handle:
    handle.write(f"{reward:.3f}\n")
PY
FINALIZED=1

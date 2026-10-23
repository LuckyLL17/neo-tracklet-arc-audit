import json
import math
import os
import subprocess
import sys
from pathlib import Path

PROGRAM = Path(os.environ.get("TARGET_PROGRAM", "/app/main.py"))
REFERENCE = {"ra_deg": 359.9, "dec_deg": 20.0}
BASELINES = [
    (0.0, -0.000042, 0.000010),
    (1.0, 0.000011, -0.000040),
    (2.0, 0.000050, 0.000021),
    (3.0, -0.000029, 0.000039),
    (4.0, 0.000024, -0.000018),
]


def wrapped_ra(value):
    return value % 360.0


def make_observations(xi0=0.4, eta0=-0.3, mux=1.2, mueta=0.45, parallax=0.8, sigma=0.1, baselines=None, perturb=None):
    baselines = BASELINES if baselines is None else baselines
    cosine = math.cos(math.radians(REFERENCE["dec_deg"]))
    rows = []
    for index, (hours, east, north) in enumerate(baselines):
        xi = xi0 + mux * hours - parallax * east
        eta = eta0 + mueta * hours - parallax * north
        if perturb and index in perturb:
            xi += perturb[index][0]
            eta += perturb[index][1]
        rows.append({
            "epoch_s": hours * 3600.0,
            "ra_deg": wrapped_ra(REFERENCE["ra_deg"] + xi / (cosine * 3600.0)),
            "dec_deg": REFERENCE["dec_deg"] + eta / 3600.0,
            "sigma_arcsec": sigma[index] if isinstance(sigma, list) else sigma,
            "station_east_au": east,
            "station_north_au": north,
        })
    return rows


def candidate(identifier="2026 AB", xi0=0.4, eta0=-0.3, mux=1.2, mueta=0.45, epoch=0.0, covariance=None):
    return {
        "candidate_id": identifier,
        "epoch_s": epoch,
        "xi_arcsec": xi0,
        "eta_arcsec": eta0,
        "motion_xi_arcsec_per_hour": mux,
        "motion_eta_arcsec_per_hour": mueta,
        "covariance_arcsec2": covariance or [[0.04, 0.0], [0.0, 0.04]],
    }


def payload(rows=None, candidates=None, object_id="K26A01", reference=None):
    return {
        "object_id": object_id,
        "reference": reference or dict(REFERENCE),
        "observations": rows or make_observations(),
        "candidates": candidates or [candidate()],
    }


def invoke(tmp_path, body, output_name="report.json"):
    source = tmp_path / "input.json"
    target = tmp_path / output_name
    source.write_text(json.dumps(body), encoding="utf-8")
    result = subprocess.run([sys.executable, str(PROGRAM), str(source), str(target)], capture_output=True, text=True)
    return result, target, source


def ok(result, target):
    assert result.returncode == 0, result.stderr
    return json.loads(target.read_text(encoding="utf-8"))


def rejected(result, prefix):
    assert result.returncode != 0
    assert result.stderr.startswith(prefix), result.stderr
    assert "implementation is incomplete" not in result.stderr


# Tangent-plane geometry and astrometric conventions.
def test_wraparound_and_tangent_projection_recover_geocentric_motion(tmp_path):
    """A1: RA crossing 0 degrees must use a continuous tangent-plane branch."""
    reference = {"ra_deg": 359.9998, "dec_deg": 60.0}
    rows = make_observations(xi0=0.0, eta0=0.0, mux=2.0, mueta=0.3, parallax=0.7)
    cosine = math.cos(math.radians(reference["dec_deg"]))
    for index, row in enumerate(rows):
        hours = index
        xi = 2.0 * hours - 0.7 * row["station_east_au"]
        row["ra_deg"] = wrapped_ra(reference["ra_deg"] + xi / (cosine * 3600.0))
        row["dec_deg"] = reference["dec_deg"] + (0.3 * hours - 0.7 * row["station_north_au"]) / 3600.0
    data = ok(*invoke(tmp_path, payload(rows=rows, candidates=[candidate(xi0=0.0, eta0=0.0, mux=2.0, mueta=0.3)], reference=reference))[:2])
    assert math.isclose(data["geocentric_motion_xi_arcsec_per_hour"], 2.0, abs_tol=1e-6)
    assert math.isclose(data["geocentric_motion_eta_arcsec_per_hour"], 0.3, abs_tol=1e-6)
    assert data["association_status"] == "MATCHED"


def test_inverse_variance_weights_limit_a_low_precision_astrometric_outlier(tmp_path):
    """A2: a large-sigma outlier must not dominate the joint astrometric fit."""
    rows = make_observations(sigma=[0.08, 0.08, 0.08, 0.08, 20.0], perturb={4: (30.0, -20.0)})
    data = ok(*invoke(tmp_path, payload(rows=rows, candidates=[candidate(covariance=[[100.0, 0.0], [0.0, 100.0]])]))[:2])
    assert abs(data["geocentric_motion_xi_arcsec_per_hour"] - 1.2) < 0.01
    assert abs(data["geocentric_motion_eta_arcsec_per_hour"] - 0.45) < 0.01


def test_polar_reference_tangent_plane_is_rejected_without_output_side_effect(tmp_path):
    """A3: a reference plane at the pole is physically singular."""
    body = payload(reference={"ra_deg": 1.0, "dec_deg": 90.0})
    result, target, _ = invoke(tmp_path, body)
    target.write_bytes(b"keep-this-report")
    result = subprocess.run([sys.executable, str(PROGRAM), str(tmp_path / "input.json"), str(target)], capture_output=True, text=True)
    rejected(result, "E_GEOMETRY:")
    assert target.read_bytes() == b"keep-this-report"


# Multi-station joint parallax inversion.
def test_shared_parallax_is_recovered_from_multistation_geometry(tmp_path):
    """B1: both coordinate streams must jointly recover one shared parallax."""
    data = ok(*invoke(tmp_path, payload(rows=make_observations(parallax=0.83), candidates=[candidate()]))[:2])
    assert math.isclose(data["parallax_arcsec"], 0.83, abs_tol=1e-6)
    assert data["rms_arcsec"] == 0.0


def test_zero_station_baselines_are_unidentifiable(tmp_path):
    """B2: no station baseline leaves the shared parallax unobservable."""
    baselines = [(float(index), 0.0, 0.0) for index in range(5)]
    result, target, _ = invoke(tmp_path, payload(rows=make_observations(baselines=baselines)))
    rejected(result, "E_GEOMETRY:")
    assert not target.exists()


def test_affine_station_baseline_is_unidentifiable(tmp_path):
    """B3: a baseline absorbable by position and rate is a rank-deficient geometry."""
    baselines = [(float(index), 0.000010 + 0.000005 * index, -0.000020 + 0.000010 * index) for index in range(5)]
    result, _, _ = invoke(tmp_path, payload(rows=make_observations(baselines=baselines)))
    rejected(result, "E_GEOMETRY:")


def test_parallax_significance_uses_the_normal_matrix_covariance(tmp_path):
    """B4: exact, precise geometry should yield a strongly significant positive parallax."""
    data = ok(*invoke(tmp_path, payload(rows=make_observations(parallax=1200.0, sigma=0.03)))[:2])
    assert data["parallax_significance"] > 3.0
    assert math.isclose(data["parallax_arcsec"], 1200.0, abs_tol=1e-6)


def test_boolean_station_baseline_is_schema_error_and_preserves_existing_output(tmp_path):
    """B5: booleans cannot masquerade as physical AU baselines."""
    body = payload()
    body["observations"][0]["station_east_au"] = True
    result, target, _ = invoke(tmp_path, body)
    target.write_bytes(b"old-bytes")
    result = subprocess.run([sys.executable, str(PROGRAM), str(tmp_path / "input.json"), str(target)], capture_output=True, text=True)
    rejected(result, "E_SCHEMA:")
    assert target.read_bytes() == b"old-bytes"


# Ephemeris propagation and covariance-gated linking.
def test_candidate_position_is_propagated_to_the_tracklet_epoch(tmp_path):
    """C1: candidate association must use the candidate's own epoch before gating."""
    propagated = candidate(xi0=-0.8, eta0=-0.75, mux=1.2, mueta=0.45, epoch=-3600.0)
    data = ok(*invoke(tmp_path, payload(candidates=[propagated]))[:2])
    assert data["association_status"] == "MATCHED"
    assert data["matched_candidate_id"] == "2026 AB"


def test_non_symmetric_candidate_covariance_is_rejected(tmp_path):
    """C2: a non-symmetric covariance cannot define a Mahalanobis metric."""
    bad = candidate(covariance=[[1.0, 0.2], [0.1, 1.0]])
    result, target, _ = invoke(tmp_path, payload(candidates=[bad]))
    rejected(result, "E_CATALOG:")
    assert not target.exists()


def test_nearer_mahalanobis_candidate_wins_even_when_input_order_differs(tmp_path):
    """C3: compatible candidates are selected by Mahalanobis distance, not input order."""
    far = candidate("z-far", xi0=0.7, eta0=-0.3, covariance=[[0.04, 0.0], [0.0, 0.04]])
    near = candidate("a-near", xi0=0.45, eta0=-0.3, covariance=[[0.04, 0.0], [0.0, 0.04]])
    data = ok(*invoke(tmp_path, payload(candidates=[far, near]))[:2])
    assert data["matched_candidate_id"] == "a-near"


def test_exact_association_tie_uses_candidate_id_as_last_deterministic_key(tmp_path):
    """C4: exact physical ties must have a stable catalog identifier tie-break."""
    zed = candidate("zed", covariance=[[1.0, 0.0], [0.0, 1.0]])
    alpha = candidate("alpha", covariance=[[1.0, 0.0], [0.0, 1.0]])
    data = ok(*invoke(tmp_path, payload(candidates=[zed, alpha]))[:2])
    assert data["matched_candidate_id"] == "alpha"


def test_position_close_but_velocity_incompatible_candidate_is_unmatched(tmp_path):
    """C5: catalog position alone is insufficient when angular velocity conflicts."""
    incompatible = candidate(mux=4.0, mueta=0.45, covariance=[[100.0, 0.0], [0.0, 100.0]])
    data = ok(*invoke(tmp_path, payload(candidates=[incompatible]))[:2])
    assert data["association_status"] == "UNMATCHED"
    assert data["matched_candidate_id"] is None
    assert data["classification"] == "REJECT"


# Physical quality classification uses several independent observables.
def test_unmatched_tracklet_is_rejected_and_renders_null_association_fields(tmp_path):
    """D1: lack of a catalog link is a rejection rather than a formatting success."""
    no_link = candidate(xi0=20.0, eta0=20.0, covariance=[[1.0, 0.0], [0.0, 1.0]])
    data = ok(*invoke(tmp_path, payload(candidates=[no_link]))[:2])
    assert data["classification"] == "REJECT"
    assert data["association_mahalanobis_distance"] is None


def test_low_parallax_significance_routes_an_otherwise_linked_arc_to_review(tmp_path):
    """D2: a linked arc with weak parallax evidence is REVIEW, not ACCEPT."""
    rows = make_observations(parallax=0.08, sigma=3.0)
    data = ok(*invoke(tmp_path, payload(rows=rows, candidates=[candidate(covariance=[[20.0, 0.0], [0.0, 20.0]])]))[:2])
    assert data["association_status"] == "MATCHED"
    assert data["parallax_significance"] < 3.0
    assert data["classification"] == "REVIEW"


def test_short_observing_arc_routes_a_strong_solution_to_review(tmp_path):
    """D3: sufficient geometry does not replace a minimum temporal arc."""
    short = [(0.0, -0.000042, 0.000010), (0.2, 0.000011, -0.000040), (0.4, 0.000050, 0.000021), (0.7, -0.000029, 0.000039), (0.9, 0.000024, -0.000018)]
    data = ok(*invoke(tmp_path, payload(rows=make_observations(baselines=short)))[:2])
    assert data["arc_hours"] == 0.9
    assert data["classification"] == "REVIEW"


def test_large_astrometric_residual_rejects_even_with_a_catalog_link(tmp_path):
    """D4: high residual scatter makes an otherwise linked tracklet untrustworthy."""
    rows = make_observations(perturb={2: (5.0, -4.0)})
    wide = candidate(covariance=[[100.0, 0.0], [0.0, 100.0]])
    data = ok(*invoke(tmp_path, payload(rows=rows, candidates=[wide]))[:2])
    assert data["association_status"] == "MATCHED"
    assert data["rms_arcsec"] > 1.5
    assert data["classification"] == "REJECT"


# Public contract, determinism and no-side-effect rejection paths.
def test_duplicate_epoch_is_rejected_without_rewriting_existing_report(tmp_path):
    """E1: duplicate observation times have an explicit error class and no report side effect."""
    body = payload()
    body["observations"][2]["epoch_s"] = body["observations"][1]["epoch_s"]
    result, target, _ = invoke(tmp_path, body)
    target.write_bytes(b"sentinel-report")
    result = subprocess.run([sys.executable, str(PROGRAM), str(tmp_path / "input.json"), str(target)], capture_output=True, text=True)
    rejected(result, "E_DUPLICATE:")
    assert target.read_bytes() == b"sentinel-report"


def test_extra_observation_field_is_rejected_without_creating_a_report(tmp_path):
    """E2: exact observation schemas prevent unnoticed units or metadata drift."""
    body = payload()
    body["observations"][0]["observer_name"] = "x"
    result, target, _ = invoke(tmp_path, body)
    rejected(result, "E_SCHEMA:")
    assert not target.exists()


def test_output_order_precision_newline_and_repeat_bytes_are_stable(tmp_path):
    """E3: the audit report is deterministic machine-readable output, not a log."""
    result, target, source = invoke(tmp_path, payload())
    data = ok(result, target)
    first = target.read_bytes()
    second = subprocess.run([sys.executable, str(PROGRAM), str(source), str(target)], capture_output=True, text=True)
    assert second.returncode == 0, second.stderr
    assert target.read_bytes() == first
    assert first.endswith(b"\n") and b", " not in first
    assert list(data) == [
        "object_id", "classification", "geocentric_motion_xi_arcsec_per_hour",
        "geocentric_motion_eta_arcsec_per_hour", "parallax_arcsec", "parallax_significance",
        "association_status", "matched_candidate_id", "association_mahalanobis_distance",
        "arc_hours", "rms_arcsec", "observation_count",
    ]
    assert b'"parallax_arcsec":0.800000' in first


def test_non_positive_definite_covariance_is_catalog_error_without_output_side_effect(tmp_path):
    """E4: singular covariance must fail closed before a report is opened."""
    body = payload(candidates=[candidate(covariance=[[1.0, 1.0], [1.0, 1.0]])])
    result, target, _ = invoke(tmp_path, body)
    target.write_bytes(b"before")
    result = subprocess.run([sys.executable, str(PROGRAM), str(tmp_path / "input.json"), str(target)], capture_output=True, text=True)
    rejected(result, "E_CATALOG:")
    assert target.read_bytes() == b"before"

#!/usr/bin/env python3
import json
import math
import sys

OBSERVATION_FIELDS = {
    "epoch_s", "ra_deg", "dec_deg", "sigma_arcsec",
    "station_east_au", "station_north_au",
}
CANDIDATE_FIELDS = {
    "candidate_id", "epoch_s", "xi_arcsec", "eta_arcsec",
    "motion_xi_arcsec_per_hour", "motion_eta_arcsec_per_hour",
    "covariance_arcsec2",
}
REFERENCE_FIELDS = {"ra_deg", "dec_deg"}
PARALLAX_COLUMN_SCALE = 100000.0


def fail(code, message):
    raise ValueError(code + ": " + message)


def finite_number(value, code, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        fail(code, name + " must be a finite number")
    value = float(value)
    if not math.isfinite(value):
        fail(code, name + " must be a finite number")
    return value


def nonempty_string(value, code, name):
    if not isinstance(value, str) or not value.strip():
        fail(code, name + " must be a non-empty string")
    return value


def parse_reference(value):
    if not isinstance(value, dict) or set(value) != REFERENCE_FIELDS:
        fail("E_SCHEMA", "reference fields are invalid")
    ra = finite_number(value["ra_deg"], "E_SCHEMA", "reference.ra_deg")
    dec = finite_number(value["dec_deg"], "E_SCHEMA", "reference.dec_deg")
    if not 0.0 <= ra < 360.0 or not -90.0 <= dec <= 90.0:
        fail("E_SCHEMA", "reference coordinate is out of range")
    cosine = math.cos(math.radians(dec))
    if abs(cosine) < 1.0e-4:
        fail("E_GEOMETRY", "reference tangent plane is polar")
    return ra, dec, cosine


def parse_observations(value):
    if not isinstance(value, list) or len(value) < 4:
        fail("E_SCHEMA", "at least four observations are required")
    rows = []
    previous = None
    for index, row in enumerate(value):
        if not isinstance(row, dict) or set(row) != OBSERVATION_FIELDS:
            fail("E_SCHEMA", "observation fields are invalid")
        epoch = finite_number(row["epoch_s"], "E_SCHEMA", "epoch_s")
        ra = finite_number(row["ra_deg"], "E_SCHEMA", "ra_deg")
        dec = finite_number(row["dec_deg"], "E_SCHEMA", "dec_deg")
        sigma = finite_number(row["sigma_arcsec"], "E_SCHEMA", "sigma_arcsec")
        east = finite_number(row["station_east_au"], "E_SCHEMA", "station_east_au")
        north = finite_number(row["station_north_au"], "E_SCHEMA", "station_north_au")
        if not 0.0 <= ra < 360.0 or not -90.0 <= dec <= 90.0 or sigma <= 0.0:
            fail("E_SCHEMA", "observation coordinate or uncertainty is out of range")
        if previous is not None:
            if epoch == previous:
                fail("E_DUPLICATE", "duplicate epoch")
            if epoch < previous:
                fail("E_TIME", "epochs must strictly increase")
        previous = epoch
        rows.append((epoch, ra, dec, sigma, east, north))
    return rows


def parse_covariance(value):
    if not isinstance(value, list) or len(value) != 2:
        fail("E_CATALOG", "covariance must be 2 by 2")
    if any(not isinstance(row, list) or len(row) != 2 for row in value):
        fail("E_CATALOG", "covariance must be 2 by 2")
    a = finite_number(value[0][0], "E_CATALOG", "covariance[0][0]")
    b = finite_number(value[0][1], "E_CATALOG", "covariance[0][1]")
    lower = finite_number(value[1][0], "E_CATALOG", "covariance[1][0]")
    c = finite_number(value[1][1], "E_CATALOG", "covariance[1][1]")
    if b != lower or a <= 0.0 or c <= 0.0:
        fail("E_CATALOG", "covariance must be symmetric positive definite")
    determinant = a * c - b * b
    if not math.isfinite(determinant) or determinant <= 1.0e-14 * max(1.0, a * c, b * b):
        fail("E_CATALOG", "covariance must be symmetric positive definite")
    return a, b, c, determinant


def parse_candidates(value):
    if not isinstance(value, list) or not value:
        fail("E_CATALOG", "at least one candidate is required")
    candidates = []
    seen_ids = set()
    for row in value:
        if not isinstance(row, dict) or set(row) != CANDIDATE_FIELDS:
            fail("E_CATALOG", "candidate fields are invalid")
        identifier = nonempty_string(row["candidate_id"], "E_CATALOG", "candidate_id")
        if identifier in seen_ids:
            fail("E_CATALOG", "candidate_id values must be unique")
        seen_ids.add(identifier)
        epoch = finite_number(row["epoch_s"], "E_CATALOG", "candidate.epoch_s")
        xi = finite_number(row["xi_arcsec"], "E_CATALOG", "candidate.xi_arcsec")
        eta = finite_number(row["eta_arcsec"], "E_CATALOG", "candidate.eta_arcsec")
        mu_xi = finite_number(row["motion_xi_arcsec_per_hour"], "E_CATALOG", "candidate.motion_xi_arcsec_per_hour")
        mu_eta = finite_number(row["motion_eta_arcsec_per_hour"], "E_CATALOG", "candidate.motion_eta_arcsec_per_hour")
        candidates.append((identifier, epoch, xi, eta, mu_xi, mu_eta, parse_covariance(row["covariance_arcsec2"])))
    return candidates


def load_input(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError, TypeError) as error:
        fail("E_SCHEMA", str(error))
    if not isinstance(data, dict) or set(data) != {"object_id", "reference", "observations", "candidates"}:
        fail("E_SCHEMA", "top-level fields are invalid")
    return (
        nonempty_string(data["object_id"], "E_SCHEMA", "object_id"),
        parse_reference(data["reference"]),
        parse_observations(data["observations"]),
        parse_candidates(data["candidates"]),
    )


def continuous_ra(rows, reference_ra):
    values = []
    first_delta = ((rows[0][1] - reference_ra + 180.0) % 360.0) - 180.0
    values.append(reference_ra + first_delta)
    for row in rows[1:]:
        step = ((row[1] - values[-1] + 180.0) % 360.0) - 180.0
        values.append(values[-1] + step)
    return values


def invert_normal(matrix, vector):
    size = len(vector)
    largest = max(abs(item) for row in matrix for item in row)
    if not math.isfinite(largest) or largest == 0.0:
        fail("E_GEOMETRY", "normal matrix is degenerate")
    augmented = []
    for row_index in range(size):
        row = list(matrix[row_index]) + [0.0] * size + [vector[row_index]]
        row[size + row_index] = 1.0
        augmented.append(row)
    for column in range(size):
        pivot_row = max(range(column, size), key=lambda index: abs(augmented[index][column]))
        pivot = augmented[pivot_row][column]
        if not math.isfinite(pivot) or abs(pivot) <= 1.0e-11 * largest:
            fail("E_GEOMETRY", "joint fit is not identifiable")
        augmented[column], augmented[pivot_row] = augmented[pivot_row], augmented[column]
        pivot = augmented[column][column]
        for entry in range(column, 2 * size + 1):
            augmented[column][entry] /= pivot
        for row_index in range(size):
            if row_index == column:
                continue
            factor = augmented[row_index][column]
            if factor == 0.0:
                continue
            for entry in range(column, 2 * size + 1):
                augmented[row_index][entry] -= factor * augmented[column][entry]
    result = [augmented[index][2 * size] for index in range(size)]
    inverse = [[augmented[row][size + column] for column in range(size)] for row in range(size)]
    if not all(math.isfinite(item) for item in result):
        fail("E_GEOMETRY", "joint fit is non-finite")
    if not all(math.isfinite(item) for row in inverse for item in row):
        fail("E_GEOMETRY", "joint fit covariance is non-finite")
    return result, inverse


def joint_fit(reference, rows):
    reference_ra, reference_dec, cosine = reference
    unwrapped_ra = continuous_ra(rows, reference_ra)
    origin_epoch = rows[0][0]
    normal = [[0.0 for _ in range(5)] for _ in range(5)]
    rhs = [0.0 for _ in range(5)]
    prepared = []
    for row, ra in zip(rows, unwrapped_ra):
        epoch, _, dec, sigma, east, north = row
        time_hours = (epoch - origin_epoch) / 3600.0
        xi = (ra - reference_ra) * cosine * 3600.0
        eta = (dec - reference_dec) * 3600.0
        weight = 1.0 / (sigma * sigma)
        if not math.isfinite(weight) or not math.isfinite(xi) or not math.isfinite(eta):
            fail("E_GEOMETRY", "non-finite transformed observation")
        x_row = [1.0, 0.0, time_hours, 0.0, -east * PARALLAX_COLUMN_SCALE]
        y_row = [0.0, 1.0, 0.0, time_hours, -north * PARALLAX_COLUMN_SCALE]
        for design, measurement in ((x_row, xi), (y_row, eta)):
            for column in range(5):
                rhs[column] += weight * design[column] * measurement
                for other in range(5):
                    normal[column][other] += weight * design[column] * design[other]
        prepared.append((time_hours, xi, eta, sigma, east, north))
    parameters, inverse = invert_normal(normal, rhs)
    xi0, eta0, mu_xi, mu_eta, scaled_parallax = parameters
    parallax = scaled_parallax * PARALLAX_COLUMN_SCALE
    variance = inverse[4][4] * PARALLAX_COLUMN_SCALE * PARALLAX_COLUMN_SCALE
    if variance <= 0.0 or not math.isfinite(variance):
        fail("E_GEOMETRY", "parallax uncertainty is invalid")
    parallax_sigma = math.sqrt(variance)
    significance = parallax / parallax_sigma
    squared = 0.0
    for time_hours, xi, eta, _, east, north in prepared:
        model_xi = xi0 + mu_xi * time_hours - parallax * east
        model_eta = eta0 + mu_eta * time_hours - parallax * north
        residual_xi = xi - model_xi
        residual_eta = eta - model_eta
        squared += residual_xi * residual_xi + residual_eta * residual_eta
    rms = math.sqrt(squared / len(prepared))
    values = (xi0, eta0, mu_xi, mu_eta, parallax, significance, rms)
    if not all(math.isfinite(value) for value in values):
        fail("E_GEOMETRY", "fit result is non-finite")
    return xi0, eta0, mu_xi, mu_eta, parallax, significance, rms, (rows[-1][0] - origin_epoch) / 3600.0


def associate(candidates, origin_epoch, xi0, eta0, mu_xi, mu_eta):
    matches = []
    for identifier, epoch, xi, eta, candidate_mu_xi, candidate_mu_eta, covariance in candidates:
        a, b, c, determinant = covariance
        elapsed = (origin_epoch - epoch) / 3600.0
        predicted_xi = xi + candidate_mu_xi * elapsed
        predicted_eta = eta + candidate_mu_eta * elapsed
        dx = xi0 - predicted_xi
        dy = eta0 - predicted_eta
        distance_squared = (c * dx * dx - 2.0 * b * dx * dy + a * dy * dy) / determinant
        if distance_squared < -1.0e-9 or not math.isfinite(distance_squared):
            fail("E_CATALOG", "candidate distance is invalid")
        distance = math.sqrt(max(0.0, distance_squared))
        velocity_difference = math.hypot(mu_xi - candidate_mu_xi, mu_eta - candidate_mu_eta)
        if distance <= 3.0 and velocity_difference <= 1.5:
            matches.append((distance, velocity_difference, identifier))
    if not matches:
        return "UNMATCHED", None, None
    distance, _, identifier = min(matches, key=lambda item: (item[0], item[1], item[2]))
    return "MATCHED", identifier, distance


def median(values):
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def rounded(value):
    if abs(value) < 0.0000005:
        value = 0.0
    return format(value, ".6f")


def classify(rows, mu_xi, mu_eta, parallax, significance, rms, arc_hours, status, distance):
    residual_limit = max(1.5, 3.0 * median([row[3] for row in rows]))
    speed = math.hypot(mu_xi, mu_eta)
    if status != "MATCHED" or parallax <= 0.0 or rms > residual_limit:
        return "REJECT"
    if arc_hours < 1.0 or speed < 0.25 or significance < 3.0 or distance > 2.0:
        return "REVIEW"
    return "ACCEPT"


def render(object_id, classification, mu_xi, mu_eta, parallax, significance, status, candidate_id, distance, arc_hours, rms, count):
    fields = [
        '"object_id":' + json.dumps(object_id, ensure_ascii=False, separators=(",", ":")),
        '"classification":' + json.dumps(classification),
        '"geocentric_motion_xi_arcsec_per_hour":' + rounded(mu_xi),
        '"geocentric_motion_eta_arcsec_per_hour":' + rounded(mu_eta),
        '"parallax_arcsec":' + rounded(parallax),
        '"parallax_significance":' + rounded(significance),
        '"association_status":' + json.dumps(status),
        '"matched_candidate_id":' + json.dumps(candidate_id, ensure_ascii=False, separators=(",", ":")),
        '"association_mahalanobis_distance":' + ("null" if distance is None else rounded(distance)),
        '"arc_hours":' + rounded(arc_hours),
        '"rms_arcsec":' + rounded(rms),
        '"observation_count":' + str(count),
    ]
    return "{" + ",".join(fields) + "}\n"


def audit(source, destination):
    object_id, reference, rows, candidates = load_input(source)
    xi0, eta0, mu_xi, mu_eta, parallax, significance, rms, arc_hours = joint_fit(reference, rows)
    status, candidate_id, distance = associate(candidates, rows[0][0], xi0, eta0, mu_xi, mu_eta)
    state = classify(rows, mu_xi, mu_eta, parallax, significance, rms, arc_hours, status, distance)
    report = render(object_id, state, mu_xi, mu_eta, parallax, significance, status, candidate_id, distance, arc_hours, rms, len(rows))
    try:
        with open(destination, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(report)
    except OSError as error:
        fail("E_IO", str(error))


def main():
    if len(sys.argv) != 3:
        sys.stderr.write("E_SCHEMA: expected input and output paths\n")
        return 2
    try:
        audit(sys.argv[1], sys.argv[2])
        return 0
    except ValueError as error:
        sys.stderr.write(str(error) + "\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())

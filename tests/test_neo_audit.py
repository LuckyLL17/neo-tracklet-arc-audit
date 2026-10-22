import json, math, os, shutil, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROGRAM = Path("/app/neo_audit.py")


def obs(t, ra, dec=10.0, sigma=0.4):
    return {"epoch_s": t, "ra_deg": ra, "dec_deg": dec, "sigma_arcsec": sigma}


def payload(rows, object_id="K26A01"):
    return {"object_id": object_id, "observations": rows}


def invoke(tmp_path, body, output_name="report.json"):
    source = tmp_path / "input.json"; target = tmp_path / output_name
    source.write_text(json.dumps(body), encoding="utf-8")
    completed = subprocess.run([sys.executable, str(PROGRAM), str(source), str(target)], capture_output=True, text=True)
    return completed, target


def weighted_slope(xs, ys, sigmas):
    weights = [1 / s**2 for s in sigmas]
    sw = sum(weights); sx = sum(w*x for w, x in zip(weights, xs)); sy = sum(w*y for w, y in zip(weights, ys))
    return (sw * sum(w*x*y for w, x, y in zip(weights, xs, ys)) - sx*sy) / (sw * sum(w*x*x for w, x in zip(weights, xs)) - sx*sx)


def read_ok(result, target):
    assert result.returncode == 0, result.stderr
    return json.loads(target.read_text(encoding="utf-8"))


def assert_rejected(result, prefix):
    assert result.returncode != 0
    assert result.stderr.startswith(prefix)
    assert "implementation is incomplete" not in result.stderr


def test_crossing_zero_ra_is_continuous_and_accepted(tmp_path):
    """R1/轴A：跨 0° 的赤经应沿连续短路径计算，而不是产生整圈假运动。"""
    result, target = invoke(tmp_path, payload([obs(0,359.8),obs(3600,359.9),obs(7200,0.0),obs(10800,0.1)]))
    data = read_ok(result, target)
    assert data["classification"] == "ACCEPT"
    assert math.isclose(data["motion_ra_arcsec_per_hour"], 360*math.cos(math.radians(10)), abs_tol=1e-6)
    assert data["rms_arcsec"] == 0.0


def test_weighted_fit_suppresses_low_precision_outlier(tmp_path):
    """R2/轴A：斜率必须使用 sigma 的逆方差权重，低精度离群点不能主导结论。"""
    rows=[obs(0,20.0,20,0.2),obs(3600,20.02,20,0.2),obs(7200,20.04,20,0.2),obs(10800,21.0,20,20.0)]
    result, target = invoke(tmp_path, payload(rows))
    data = read_ok(result, target)
    expected=weighted_slope([0,1,2,3],[20,20.02,20.04,21],[.2,.2,.2,20])*3600*math.cos(math.radians(20))
    assert math.isclose(data["motion_ra_arcsec_per_hour"], expected, abs_tol=1e-6)
    assert data["classification"] == "REVIEW"


def test_reference_declination_scales_ra_residual(tmp_path):
    """R3/轴A：赤经残差和运动必须投影到参考赤纬的切平面尺度。"""
    result, target = invoke(tmp_path, payload([obs(0,30,60),obs(3600,30.1,60),obs(7200,30.2,60)]))
    data=read_ok(result,target)
    assert math.isclose(data["motion_ra_arcsec_per_hour"],180.0,abs_tol=1e-6)


def test_short_arc_requires_review(tmp_path):
    """R4/轴B：覆盖不足半小时的观测弧必须进入 REVIEW。"""
    result,target=invoke(tmp_path,payload([obs(0,10),obs(600,10.1),obs(1200,10.2)]))
    assert read_ok(result,target)["classification"] == "REVIEW"


def test_slow_motion_requires_review(tmp_path):
    """R5/轴B：总角速度低于公开阈值的有效弧必须进入 REVIEW。"""
    result,target=invoke(tmp_path,payload([obs(0,10),obs(3600,10.00001),obs(7200,10.00002)]))
    assert read_ok(result,target)["classification"] == "REVIEW"


def test_scatter_threshold_requires_review(tmp_path):
    """R6/轴B：二维残差超过由 sigma 导出的阈值时必须进入 REVIEW。"""
    result,target=invoke(tmp_path,payload([obs(0,10,0,.3),obs(3600,10.1,0,.3),obs(7200,10.2,0,.3),obs(10800,10.9,0,.3)]))
    data=read_ok(result,target)
    assert data["rms_arcsec"] > 1.5 and data["classification"] == "REVIEW"


def test_boolean_disguised_number_rejects_without_rewriting_output(tmp_path):
    """R7/轴C：布尔值伪装数值必须以 E_SCHEMA 拒收并保留已有输出字节。"""
    body=payload([obs(0,10),obs(3600,10.1),obs(7200,10.2)]); body["observations"][0]["epoch_s"]=True
    result,target=invoke(tmp_path,body); target.write_bytes(b"sentinel-bytes")
    # Re-run after sentinel creation so rejection exercises the required no-side-effect path.
    result=subprocess.run([sys.executable,str(PROGRAM),str(tmp_path/'input.json'),str(target)],capture_output=True,text=True)
    assert_rejected(result, "E_SCHEMA:")
    assert target.read_bytes()==b"sentinel-bytes"
    assert not list(tmp_path.glob(".neo-audit-*"))


def test_out_of_range_coordinate_rejects(tmp_path):
    """R8/轴C：赤纬超出物理范围必须以 E_SCHEMA 拒收。"""
    result,target=invoke(tmp_path,payload([obs(0,10,91),obs(3600,10.1),obs(7200,10.2)]))
    assert_rejected(result, "E_SCHEMA:")
    assert not target.exists()


def test_nonmonotonic_time_rejects(tmp_path):
    """R9/轴C：倒退历元必须以 E_TIME 拒收且不产生报告。"""
    result,target=invoke(tmp_path,payload([obs(0,10),obs(7200,10.2),obs(3600,10.1)]))
    assert_rejected(result, "E_TIME:")
    assert not target.exists()


def test_duplicate_epoch_rejects_and_preserves_sentinel(tmp_path):
    """R10/轴C：重复历元必须以 E_DUPLICATE 拒收且既有报告逐字节不变。"""
    source=tmp_path/'input.json'; target=tmp_path/'report.json'; target.write_bytes(b"previous-report\n")
    source.write_text(json.dumps(payload([obs(0,10),obs(0,10.1),obs(3600,10.2)])),encoding='utf-8')
    result=subprocess.run([sys.executable,str(PROGRAM),str(source),str(target)],capture_output=True,text=True)
    assert_rejected(result, "E_DUPLICATE:")
    assert target.read_bytes()==b"previous-report\n"


def test_nearly_coincident_epochs_reject_as_degenerate_geometry(tmp_path):
    """R11/轴C：数学上不同但落入数值保护阈值的历元必须走 E_GEOMETRY 路径。"""
    result,target=invoke(tmp_path,payload([obs(0,10),obs(1e-12,10.1),obs(2e-12,10.2)]))
    assert_rejected(result, "E_GEOMETRY:")
    assert not target.exists()


def test_polar_tangent_plane_rejects(tmp_path):
    """R12/轴C：接近极点、赤经投影退化的输入必须以 E_GEOMETRY 拒收。"""
    result,target=invoke(tmp_path,payload([obs(0,10,90),obs(3600,10.1,90),obs(7200,10.2,90)]))
    assert_rejected(result, "E_GEOMETRY:")
    assert not target.exists()


def test_output_bytes_have_public_order_precision_and_newline(tmp_path):
    """R13/轴D：成功报告必须是紧凑、定序、六位数值和末尾换行的 UTF-8 JSON。"""
    result,target=invoke(tmp_path,payload([obs(0,10,10),obs(3600,10.1,10),obs(7200,10.2,10)]))
    read_ok(result,target); raw=target.read_bytes()
    assert raw.endswith(b"\n") and b", " not in raw
    assert list(json.loads(raw)) == ["object_id","classification","motion_ra_arcsec_per_hour","motion_dec_arcsec_per_hour","arc_hours","rms_arcsec","observation_count"]
    assert b'"arc_hours":2.000000' in raw and b'"rms_arcsec":0.000000' in raw


def test_repeat_run_is_byte_stable_and_leaves_no_temporary_file(tmp_path):
    """R14/轴D：同一输入重复执行应字节稳定，成功原子写入后不得留下临时文件。"""
    body=payload([obs(0,10),obs(3600,10.1),obs(7200,10.2)])
    first,target=invoke(tmp_path,body); read_ok(first,target); one=target.read_bytes()
    second=subprocess.run([sys.executable,str(PROGRAM),str(tmp_path/'input.json'),str(target)],capture_output=True,text=True)
    assert second.returncode==0 and target.read_bytes()==one and not list(tmp_path.glob('.neo-audit-*'))

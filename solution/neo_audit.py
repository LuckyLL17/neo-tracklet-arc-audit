#!/usr/bin/env python3
import json, math, os, statistics, sys, tempfile

FIELDS = {"epoch_s", "ra_deg", "dec_deg", "sigma_arcsec"}

def fail(code, message):
    raise ValueError(f"{code}: {message}")

def number(value, field):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        fail("E_SCHEMA", f"{field} must be a finite number")
    return float(value)

def load(path):
    try:
        with open(path, encoding="utf-8") as f: data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        fail("E_SCHEMA", str(e))
    if not isinstance(data, dict) or set(data) != {"object_id", "observations"}:
        fail("E_SCHEMA", "top-level keys are invalid")
    if not isinstance(data["object_id"], str) or not data["object_id"].strip(): fail("E_SCHEMA", "object_id")
    rows = data["observations"]
    if not isinstance(rows, list) or len(rows) < 3: fail("E_SCHEMA", "at least three observations")
    parsed=[]; previous=None
    for row in rows:
        if not isinstance(row, dict) or set(row) != FIELDS: fail("E_SCHEMA", "observation fields")
        t=number(row["epoch_s"], "epoch_s"); ra=number(row["ra_deg"], "ra_deg"); dec=number(row["dec_deg"], "dec_deg"); sig=number(row["sigma_arcsec"], "sigma_arcsec")
        if not 0 <= ra < 360 or not -90 <= dec <= 90 or sig <= 0: fail("E_SCHEMA", "observation range")
        if previous is not None:
            if t == previous: fail("E_DUPLICATE", "duplicate epoch")
            if t < previous: fail("E_TIME", "epochs must increase")
        previous=t; parsed.append((t,ra,dec,sig))
    return data["object_id"], parsed

def fit(xs, ys, ws):
    sw=sum(ws); sx=sum(w*x for w,x in zip(ws,xs)); sy=sum(w*y for w,y in zip(ws,ys))
    sxx=sum(w*x*x for w,x in zip(ws,xs)); sxy=sum(w*x*y for w,x,y in zip(ws,xs,ys))
    den=sw*sxx-sx*sx
    if not math.isfinite(den) or abs(den) <= 1e-12 * max(1.0, abs(sw*sxx), abs(sx*sx)): fail("E_GEOMETRY", "degenerate fit")
    slope=(sw*sxy-sx*sy)/den; intercept=(sy-slope*sx)/sw
    if not math.isfinite(slope) or not math.isfinite(intercept): fail("E_GEOMETRY", "non-finite fit")
    return intercept,slope

def audit(source, dest):
    object_id, rows=load(source)
    base=rows[0][0]; xs=[(r[0]-base)/3600.0 for r in rows]; ra=[rows[0][1]]
    for row in rows[1:]:
        delta=((row[1]-ra[-1]+180.0)%360.0)-180.0
        ra.append(ra[-1]+delta)
    dec=[r[2] for r in rows]; sig=[r[3] for r in rows]; weights=[1/(x*x) for x in sig]
    ai,aslope=fit(xs,ra,weights); di,dslope=fit(xs,dec,weights)
    reference=math.radians(di); cosine=math.cos(reference)
    if abs(cosine) < 1e-8: fail("E_GEOMETRY", "polar tangent plane")
    squared=[]
    for x,r,d in zip(xs,ra,dec):
        dra=(r-(ai+aslope*x))*cosine*3600; ddec=(d-(di+dslope*x))*3600
        squared.append(dra*dra+ddec*ddec)
    rms=math.sqrt(sum(squared)/len(squared)); mra=aslope*cosine*3600; mdec=dslope*3600; speed=math.hypot(mra,mdec); arc=xs[-1]
    if not all(math.isfinite(v) for v in (rms,mra,mdec,arc,speed)): fail("E_GEOMETRY", "non-finite result")
    threshold=max(1.5,3*statistics.median(sig))
    state="REVIEW" if arc < .5 or speed < .15 or rms > threshold else "ACCEPT"
    values=(mra,mdec,arc,rms)
    rendered=(f"{value:.6f}" for value in values)
    report=("{\"object_id\":"+json.dumps(object_id,ensure_ascii=False,separators=(",",":"))+","+
            "\"classification\":"+json.dumps(state)+","+
            "\"motion_ra_arcsec_per_hour\":"+next(rendered)+","+
            "\"motion_dec_arcsec_per_hour\":"+next(rendered)+","+
            "\"arc_hours\":"+next(rendered)+","+
            "\"rms_arcsec\":"+next(rendered)+","+
            "\"observation_count\":"+str(len(rows))+"}\n")
    directory=os.path.dirname(os.path.abspath(dest)) or "."
    fd,tmp=tempfile.mkstemp(prefix=".neo-audit-",dir=directory,text=True)
    try:
        with os.fdopen(fd,"w",encoding="utf-8",newline="\n") as f:
            f.write(report); f.flush(); os.fsync(f.fileno())
        os.replace(tmp,dest)
    except Exception:
        try: os.unlink(tmp)
        except FileNotFoundError: pass
        raise

def main():
    if len(sys.argv)!=3:
        sys.stderr.write("E_SCHEMA: expected input and output paths\n"); return 2
    try: audit(sys.argv[1],sys.argv[2]); return 0
    except ValueError as e: sys.stderr.write(str(e)+"\n"); return 2
    except OSError as e: sys.stderr.write("E_GEOMETRY: "+str(e)+"\n"); return 2
if __name__=="__main__":n=main(); sys.exit(n)

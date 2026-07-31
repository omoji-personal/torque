# P5 release-only checks: excepted-org hard-fail, agent-side minting bypass, image manifest,
# deliverable coverage. These run only in the release profile.
import json as _j, subprocess as _sp
from pathlib import Path as _P

@check("excepted_org_hardfail", "release", catastrophe=True)
def _excepted_org(target):
    # RELEASE hard-fails if any client-production exception file exists (published claim
    # stays absolute). None expected at v1.
    exc = list((ROOT/"local").glob("clients/*/*/exception*"))
    if exc:
        return Result("excepted_org_hardfail", FAIL, f"{len(exc)} excepted org(s) present — release blocked")
    return Result("excepted_org_hardfail", PASS, "no excepted production orgs; absolute claim holds")

@check("bypass_suite", "release", catastrophe=True)
def _bypass(target):
    # agent-side token minting must fail; the write gate must deny known bypass shapes.
    r = _sp.run(["python3", str(ROOT/"bin"/"torque-approve"), "00DTEST", "bulk-delete"],
                stdin=_sp.subprocess.DEVNULL if hasattr(_sp,'subprocess') else None,
                capture_output=True, text=True, cwd=ROOT)
    import subprocess
    r = subprocess.run(["python3", str(ROOT/"bin"/"torque-approve"), "00DTEST", "bulk-delete"],
                       stdin=subprocess.DEVNULL, capture_output=True, text=True, cwd=ROOT)
    if r.returncode == 0:
        return Result("bypass_suite", FAIL, "agent-side token mint succeeded")
    # config-switch TOCTOU + nested write shapes → deny
    shapes = [
        "sf config set target-org=prod && sf data update --sobject A --values x",
        "bash -c 'sf data update --sobject A --values x'",   # no explicit target
    ]
    for cmd in shapes:
        g = subprocess.run(["python3", str(ROOT/"hooks"/"prod_write_gate.py")],
                          input=_j.dumps({"tool_name":"Bash","tool_input":{"command":cmd}}),
                          capture_output=True, text=True, cwd=ROOT)
        if g.returncode != 2:
            return Result("bypass_suite", FAIL, f"bypass shape not denied: {cmd[:40]}")
    return Result("bypass_suite", PASS, "agent minting refused; bypass shapes denied")

@check("image_manifest", "static", catastrophe=True)
def _image_manifest():
    # every image under guide/ must be manifest-listed with a matching hash. No images = pass.
    import hashlib
    manifest_p = ROOT/"harness"/"image-manifest.json"
    manifest = _j.loads(manifest_p.read_text()) if manifest_p.exists() else {"images": []}
    listed = {e["file"]: e["sha256"] for e in manifest.get("images", [])}
    for img in (ROOT/"guide").rglob("*"):
        if img.suffix.lower() in (".png",".jpg",".jpeg",".gif",".webp"):
            h = hashlib.sha256(img.read_bytes()).hexdigest()
            if img.name not in listed:
                return Result("image_manifest", FAIL, f"{img.name} not in manifest (unverified capture)")
            if listed[img.name] != h:
                return Result("image_manifest", FAIL, f"{img.name} hash mismatch")
    return Result("image_manifest", PASS, f"{len(listed)} manifest entries; all guide images verified")

@check("deliverable_coverage", "release")
def _coverage():
    # nonblocking-style report: every tracked path is a known kind (rule/skill/hook/check/doc).
    tracked = _sp.run(["git","ls-files"], capture_output=True, text=True, cwd=ROOT).stdout.split()
    unknown = [t for t in tracked if not any(t.startswith(p) or t in (
        "CLAUDE.md","README.md","TOOLCHAIN.md",".gitignore","sfdx-project.json","package.json","package-lock.json")
        for p in (".claude/",".git","hooks/","bin/","harness/","guide/","force-app/"))]
    if unknown:
        return Result("deliverable_coverage", WARN, f"unclassified tracked paths: {unknown[:5]}")
    return Result("deliverable_coverage", PASS, f"{len(tracked)} tracked paths, all classified")

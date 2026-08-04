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
    # agent-side token minting must fail (no TTY), and the write gate must deny a spread of
    # bypass shapes drawn from every audit round — a compact release-gate sanity over the fronts
    # the 128-fixture check covers in full.
    import subprocess, sys as _sy
    _sy.path.insert(0, str(ROOT/'hooks'))
    import lib as _lb
    _A = str(_lb.ANCHOR)          # test the anchor that is configured, not a hardcoded one
    r = subprocess.run(["python3", str(ROOT/"bin"/"torque-approve"), "00DTEST", "bulk-delete"],
                       stdin=subprocess.DEVNULL, capture_output=True, text=True, cwd=ROOT)
    if r.returncode == 0:
        return Result("bypass_suite", FAIL, "agent-side token mint succeeded (should refuse: no TTY)")
    shapes = [
        "sf config set target-org=prod && sf data update --sobject A --values x",  # config TOCTOU
        "bash -c 'sf data delete bulk --sobject A --file f -o prod'",              # interpreter
        "nice sf --json data delete record --sobject A",                          # wrapper + global flag
        f"cat {_A[:-3]}*/secret",                       # glob reaches the configured anchor
        f"p={_A};cat $p/secret",                        # var-composed path to the anchor
        "printf x>hooks/lib.py",                                                    # glued redirect to gate
        ": >hooks/lib.py >/tmp/z",                                                  # multi-glued redirect
    ]
    for cmd in shapes:
        g = subprocess.run(["python3", str(ROOT/"hooks"/"prod_write_gate.py")],
                          input=_j.dumps({"tool_name":"Bash","tool_input":{"command":cmd}}),
                          capture_output=True, text=True, cwd=ROOT)
        if g.returncode != 2:
            return Result("bypass_suite", FAIL, f"bypass shape not denied: {cmd[:44]}")
    return Result("bypass_suite", PASS, f"agent minting refused; {len(shapes)} bypass shapes denied")

@check("image_manifest", "static", catastrophe=True)
def _image_manifest():
    """Every image under guide/ must be manifest-listed with a matching hash.

    B1. This was catastrophe-class and COULD NOT FAIL. The manifest file does not exist, so it
    defaulted to {"images": []}; guide/ holds no images, so the loop never executed; and it
    returned PASS reading "0 manifest entries; all guide images verified" — the word "verified"
    describing nothing at all. Three independent conditions each made it vacuous, and it needed
    all three to be false before a single line of its logic ran.

    That is the exact sin the fifteen mutators exist to prevent, sitting in a check marked
    catastrophe=True, and an external evaluation found it rather than the harness.

    The distinction the old code could not draw is between NOTHING TO VERIFY and NOTHING
    VERIFIED. Absent manifest with zero images is the first: no claim was made, so there is no
    claim to check, and the honest outcome is N/A. Absent manifest WITH images is the second: a
    capture is being published with nothing binding it to a real screenshot of a real org, which
    is precisely what the manifest exists to prevent.

    N/A rather than PASS matters here. Per validate.py, N/A never degrades a run, so this stays
    quiet when there is genuinely nothing to check, while never again claiming to have verified
    something it did not look at.
    """
    import hashlib
    manifest_p = ROOT/"harness"/"image-manifest.json"
    images = [p for p in (ROOT/"guide").rglob("*")
              if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".webp")]

    if not manifest_p.exists():
        if not images:
            return Result("image_manifest", NA,
                          "no image manifest and no images under guide/ — nothing is claimed, so "
                          "nothing is verified. This check reports N/A rather than PASS: an empty "
                          "loop is not evidence.")
        return Result("image_manifest", FAIL,
                      f"{len(images)} image(s) under guide/ and NO manifest at "
                      f"{manifest_p.name} — a published capture with nothing binding it to a real "
                      f"screenshot of a real org is the thing the manifest exists to prevent")

    manifest = _j.loads(manifest_p.read_text())
    listed = {e["file"]: e["sha256"] for e in manifest.get("images", [])}
    if not images:
        return Result("image_manifest", NA,
                      f"manifest present with {len(listed)} entr(ies) but no images under guide/ "
                      f"— nothing to verify against")
    for img in images:
        h = hashlib.sha256(img.read_bytes()).hexdigest()
        if img.name not in listed:
            return Result("image_manifest", FAIL, f"{img.name} not in manifest (unverified capture)")
        if listed[img.name] != h:
            return Result("image_manifest", FAIL, f"{img.name} hash mismatch")
    return Result("image_manifest", PASS,
                  f"{len(images)} image(s) under guide/, each manifest-listed with a matching "
                  f"hash, against {len(listed)} manifest entr(ies)")

@check("deliverable_coverage", "release")
def _coverage():
    # nonblocking-style report: every tracked path is a known kind (rule/skill/hook/check/doc).
    tracked = _sp.run(["git","ls-files"], capture_output=True, text=True, cwd=ROOT).stdout.split()
    unknown = [t for t in tracked if not any(t.startswith(p) or t in (
        "CLAUDE.md","README.md","ROADMAP.md","TOOLCHAIN.md","LICENSE","SECURITY.md",".gitignore","sfdx-project.json","package.json","package-lock.json")
        for p in (".claude/",".git","hooks/","bin/","harness/","guide/","brand/","force-app/",
                  "knowledge/", "docs/", "scripts/"))]
    if unknown:
        return Result("deliverable_coverage", WARN, f"unclassified tracked paths: {unknown[:5]}")
    return Result("deliverable_coverage", PASS, f"{len(tracked)} tracked paths, all classified")

@check("differential_fuzz", "static", catastrophe=True)
def _differential_fuzz():
    """Generate command variants and compare the gate against what BASH ACTUALLY DOES.

    Fixtures pin the shapes someone already thought of, which is precisely the coverage that
    kept turning out to be incomplete — every adversarial round found the same bug wearing a
    new costume. This asks a different question: for a mechanically generated corpus, does the
    gate's verdict match reality? Ground truth comes from executing each command in a throwaway
    sandbox with a recording `sf` stub and a canary secret, so the answer is bash's, not a
    second copy of our own assumptions.
    """
    r = _sp.run(["python3", str(ROOT/"harness"/"tests"/"differential_fuzz.py")],
                capture_output=True, text=True, timeout=900)
    out = (r.stdout + r.stderr).strip()
    last = [l.strip() for l in out.split("\n") if "generated cases" in l]
    detail = last[-1] if last else out[-120:]
    if r.returncode != 0:
        return Result("differential_fuzz", FAIL,
                      f"gate disagreed with real bash — {detail}")
    return Result("differential_fuzz", PASS, detail or "gate matched real bash on every case")

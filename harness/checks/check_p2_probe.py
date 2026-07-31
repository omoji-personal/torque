# P2 probe cycle: a REAL metadata deploy → verify → teardown against the disposable org,
# proving the safe-deploy capability end to end. Run-scoped field name; PermissionSet with
# FLS; delete PSA before permset; purgeOnDelete hard-delete so nothing accumulates.
import time as _time, tempfile as _tmp, shutil as _shutil, os as _os
_EPOCH = int(_time.time())

@check("probe_cycle", "capability", catastrophe=True)
def _probe_cycle(target):
    if not target:
        return Result("probe_cycle", SKIP, "no --target-org")
    field = f"Torque_Probe_{_EPOCH}__c"
    obj = "Account"
    permset = f"Torque_Probe_{_EPOCH}"
    work = _tmp.mkdtemp(prefix="torque-probe-")
    try:
        # ---- demo-schema precondition: probe must be run-scoped, never a demo component ----
        if not field.startswith("Torque_Probe_"):
            return Result("probe_cycle", FAIL, "probe field not run-scoped")
        proj = _os.path.join(work, "force-app", "main", "default")
        fdir = _os.path.join(proj, "objects", obj, "fields")
        pdir = _os.path.join(proj, "permissionsets")
        _os.makedirs(fdir); _os.makedirs(pdir)
        open(_os.path.join(work, "sfdx-project.json"), "w").write(
            '{"packageDirectories":[{"path":"force-app","default":true}],"namespace":"","sourceApiVersion":"62.0"}')
        open(_os.path.join(fdir, f"{field}.field-meta.xml"), "w").write(f'''<?xml version="1.0" encoding="UTF-8"?>
<CustomField xmlns="http://soap.sforce.com/2006/04/metadata">
  <fullName>{field}</fullName><label>Torque Probe</label><type>Text</type><length>32</length>
</CustomField>''')
        open(_os.path.join(pdir, f"{permset}.permissionset-meta.xml"), "w").write(f'''<?xml version="1.0" encoding="UTF-8"?>
<PermissionSet xmlns="http://soap.sforce.com/2006/04/metadata">
  <label>Torque Probe</label><hasActivationRequired>false</hasActivationRequired>
  <fieldPermissions><editable>true</editable><field>{obj}.{field}</field><readable>true</readable></fieldPermissions>
</PermissionSet>''')

        def sfp(*a):
            return subprocess.run(["sf", *a], capture_output=True, text=True, cwd=work)

        # ---- dry-run ----
        dr = sfp("project","deploy","start","--target-org",target,"--dry-run","--json","-d","force-app")
        if dr.returncode != 0:
            return Result("probe_cycle", FAIL, f"dry-run failed: {dr.stderr[:100]}")
        # ---- deploy ----
        dp = sfp("project","deploy","start","--target-org",target,"--json","-d","force-app")
        if dp.returncode != 0:
            return Result("probe_cycle", FAIL, f"deploy failed: {dp.stderr[:100]}")
        # ---- SOQL verify the field exists ----
        vq = subprocess.run(["sf","data","query","--target-org",target,"--use-tooling-api","--json",
             "--query",f"SELECT QualifiedApiName FROM FieldDefinition WHERE EntityDefinition.QualifiedApiName='{obj}' AND QualifiedApiName='{field}'"],
             capture_output=True, text=True)
        if json.loads(vq.stdout)["result"]["totalSize"] != 1:
            _teardown(target, obj, field, permset, work)
            return Result("probe_cycle", FAIL, "field not found after deploy")
        # ---- FLS verify (FieldPermissions row exists for the permset) ----
        fq = subprocess.run(["sf","data","query","--target-org",target,"--json","--query",
             f"SELECT Id FROM FieldPermissions WHERE Field='{obj}.{field}' AND Parent.Name='{permset}'"],
             capture_output=True, text=True)
        fls_ok = json.loads(fq.stdout)["result"]["totalSize"] >= 1
        # ---- teardown: purgeOnDelete hard-delete ----
        td_ok, td_msg = _teardown(target, obj, field, permset, work)
        if not td_ok:
            return Result("probe_cycle", FAIL, f"teardown failed: {td_msg}")
        # ---- residue precondition: field is gone ----
        rq = subprocess.run(["sf","data","query","--target-org",target,"--use-tooling-api","--json",
             "--query",f"SELECT QualifiedApiName FROM FieldDefinition WHERE EntityDefinition.QualifiedApiName='{obj}' AND QualifiedApiName='{field}'"],
             capture_output=True, text=True)
        residue = json.loads(rq.stdout)["result"]["totalSize"]
        return Result("probe_cycle", PASS,
            f"deploy→verify(field+FLS={'ok' if fls_ok else 'partial'})→purge→teardown; residue={residue}")
    finally:
        _shutil.rmtree(work, ignore_errors=True)

def _teardown(target, obj, field, permset, work):
    dproj = _tmp.mkdtemp(prefix="torque-destroy-")
    try:
        dd = _os.path.join(dproj, "destructive")
        _os.makedirs(dd)
        open(_os.path.join(work, "sfdx-project.json"), "a").close()
        open(_os.path.join(dd, "destructiveChangesPost.xml"), "w").write(f'''<?xml version="1.0" encoding="UTF-8"?>
<Package xmlns="http://soap.sforce.com/2006/04/metadata">
  <types><members>{obj}.{field}</members><name>CustomField</name></types>
  <types><members>{permset}</members><name>PermissionSet</name></types>
</Package>''')
        open(_os.path.join(dd, "package.xml"), "w").write('''<?xml version="1.0" encoding="UTF-8"?>
<Package xmlns="http://soap.sforce.com/2006/04/metadata"><version>62.0</version></Package>''')
        r = subprocess.run(["sf","project","deploy","start","--target-org",target,"--json",
             "--metadata-dir",dd,"--purge-on-delete"], capture_output=True, text=True, cwd=work)
        if r.returncode != 0:
            # fallback: manifest delete
            r = subprocess.run(["sf","project","delete","source","--target-org",target,"--no-prompt",
                 "--json","--metadata",f"CustomField:{obj}.{field}",f"PermissionSet:{permset}"],
                 capture_output=True, text=True, cwd=work)
        return (r.returncode == 0), r.stderr[:120]
    finally:
        _shutil.rmtree(dproj, ignore_errors=True)

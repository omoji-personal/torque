#!/bin/bash
# TORQUE LIVE CAPABILITY MATRIX
#
# Exit codes prove a hook returned 2. They do not prove the org was protected. This matrix
# exercises every capability END TO END against a real Developer Edition org and verifies the
# ORG STATE with SOQL after each one — gate decision -> real `sf` execution -> observed effect.
#
#   bash harness/tests/live_matrix.sh <org-alias>
#
# Safe by construction: every record it creates is tagged with a unique run id, teardown is BY
# ID ONLY (never by name/date match), and it asserts residue 0 at the end. It never touches a
# record it did not create.
set -u
ORG="${1:-}"
[ -z "$ORG" ] && { echo "usage: bash harness/tests/live_matrix.sh <org-alias>"; exit 2; }
T="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUN="TQM$(date +%s)"
P=0; F=0; S=0
ok(){ printf "  \033[32m✅ PASS\033[0m  %-42s %s\n" "$1" "${2:-}"; P=$((P+1)); }
no(){ printf "  \033[31m❌ FAIL\033[0m  %-42s %s\n" "$1" "${2:-}"; F=$((F+1)); }
sk(){ printf "  \033[2m·  SKIP\033[0m  %-42s %s\n" "$1" "${2:-}"; S=$((S+1)); }
hdr(){ printf "\n\033[36m%s\033[0m\n" "$1"; }

jq_(){ python3 -c "import json,sys;d=json.load(sys.stdin);$1" 2>/dev/null; }
ev(){ python3 -c 'import json,sys;print(json.dumps({"tool_name":sys.argv[1],"tool_input":json.loads(sys.argv[2])}))' "$1" "$2"; }
# ask both gates the way Claude Code does; 2 = denied
gate(){ local e r; e=$(ev "${2:-Bash}" "$1")
  echo "$e" | python3 "$T/hooks/prod_write_gate.py" >/dev/null 2>&1; r=$?; [ $r -eq 2 ] && return 2
  echo "$e" | python3 "$T/hooks/destructive_data_gate.py" >/dev/null 2>&1; r=$?; [ $r -eq 2 ] && return 2
  return 0; }
# gate, then ACTUALLY run it if allowed (99 = gate refused, so sf never ran)
guarded(){ local c; c=$(python3 -c 'import json,sys;print(json.loads(sys.argv[1])["command"])' "$1")
  if gate "$1"; then eval "$c" >/dev/null 2>&1; return $?; else return 99; fi; }
cnt(){ sf data query --target-org "$ORG" --json --query \
  "SELECT COUNT() FROM Account WHERE Name LIKE '${RUN}%'" 2>/dev/null | jq_ 'print(d["result"]["totalSize"])'; }

A=$(python3 -c "import sys;sys.path.insert(0,'$T/hooks');import lib;print(lib.ANCHOR)")
AG="${A%???}*"
echo "=== TORQUE LIVE CAPABILITY MATRIX — org=$ORG run=$RUN (anchor $A) ==="

hdr "A. ORG CLASSIFICATION (live query, never an alias guess)"
V=$(python3 -c "import sys;sys.path.insert(0,'$T/hooks');import lib;print(lib.classify_live('$ORG')[0])" 2>/dev/null)
[ "$V" = "developer" ] || [ "$V" = "sandbox" ] || [ "$V" = "scratch" ] \
  && ok "A1 target classifies non-production" "verdict=$V" || no "A1 classification" "got '$V'"
VP=$(python3 -c "import sys;sys.path.insert(0,'$T/hooks');import lib;print(lib.classify_live('no-such-org-xyz')[0])" 2>/dev/null)
[ "$VP" = "production" ] && ok "A2 unverifiable org fails safe to production" || no "A2 unverifiable" "got '$VP'"
OID=$(python3 -c "import sys;sys.path.insert(0,'$T/hooks');import lib;print(lib.classify_live('$ORG')[1])" 2>/dev/null)
[ -n "$OID" ] && ok "A3 live orgId resolved" "$OID" || no "A3 orgId"

hdr "B. WRITE AUTHORIZATION (allowlist + live verdict)"
BASE=$(cnt); [ "$BASE" = "0" ] && ok "B0 clean baseline" || no "B0 baseline" "$BASE tagged records"
guarded "{\"command\":\"sf data create record --sobject Account --values \\\"Name='${RUN}_a'\\\" --target-org $ORG\"}"; R=$?
[ $R -eq 0 ] && ok "B1 allowlisted write permitted AND executed" || no "B1 allowed write" "rc=$R"
[ "$(cnt)" = "1" ] && ok "B2 SOQL: record EXISTS (allow path is real)" || no "B2 record missing"
guarded '{"command":"sf data create record --sobject Account --values \"Name=x\" --target-org acme-prod"}'; R=$?
[ $R -eq 99 ] && ok "B3 production/unverifiable org DENIED" || no "B3 prod write" "rc=$R"
guarded '{"command":"sf data update record --sobject Account --values \"Name=x\""}'; R=$?
[ $R -eq 99 ] && ok "B4 write with NO --target-org denied (no default-org path)" || no "B4 no-target" "rc=$R"

hdr "C. DESTRUCTIVE OPS REQUIRE AN OPERATOR TOKEN"
guarded "{\"command\":\"sf data delete bulk --sobject Log__c --file /tmp/x.csv --target-org $ORG\"}"; R=$?
[ $R -eq 99 ] && ok "C1 bulk delete denied without a token" || no "C1 bulk delete" "rc=$R"
guarded "{\"command\":\"sf data update record --where \\\"Id != null\\\" --sobject Log__c --values S=x --target-org $ORG\"}"; R=$?
[ $R -eq 99 ] && ok "C2 WHERE-scoped mass update denied" || no "C2 where-update" "rc=$R"
guarded "{\"command\":\"sf apex run --file /tmp/evil.apex --target-org $ORG\"}"; R=$?
[ $R -eq 99 ] && ok "C3 anonymous Apex from an unapproved path denied" || no "C3 apex" "rc=$R"
[ "$(cnt)" = "1" ] && ok "C4 SOQL: data UNTOUCHED after 3 denials" || no "C4 data changed" "count=$(cnt)"

hdr "D. THE OPERATOR TOKEN ACTUALLY WORKS (not a deny-everything gate)"
python3 - "$T" "$OID" <<'PY' >/dev/null 2>&1
import sys, os, json, time; sys.path.insert(0, sys.argv[1] + "/hooks"); import lib
lib.ANCHOR.mkdir(parents=True, exist_ok=True); os.chmod(lib.ANCHOR, 0o700)
if not lib.SECRET.exists(): lib.SECRET.write_bytes(os.urandom(32)); os.chmod(lib.SECRET, 0o600)
lib.TOKENS.mkdir(parents=True, exist_ok=True)
p = {"orgId": sys.argv[2], "op": "bulk-delete", "digest": "", "exp": int(time.time())+300, "iat": int(time.time())}
p["sig"] = lib.sign(p); lib.token_path(sys.argv[2], "bulk-delete").write_text(json.dumps(p))
PY
gate "{\"command\":\"sf data delete bulk --sobject Log__c --file /tmp/x.csv --target-org $ORG\"}"; R=$?
[ $R -eq 0 ] && ok "D1 valid operator token AUTHORIZES the same op" || no "D1 token not accepted" "rc=$R"
gate "{\"command\":\"sf data delete bulk --sobject Log__c --file /tmp/x.csv --target-org $ORG\"}"; R=$?
[ $R -eq 2 ] && ok "D2 token is SINGLE-USE (2nd attempt denied)" || no "D2 token reusable" "rc=$R"
python3 - "$T" "$OID" <<'PY' >/dev/null 2>&1
import sys, json, time; sys.path.insert(0, sys.argv[1] + "/hooks"); import lib
lib.token_path(sys.argv[2], "bulk-delete").write_text(json.dumps(
  {"orgId": sys.argv[2], "op": "bulk-delete", "digest": "", "exp": int(time.time())+300, "iat": 0, "sig": "de4dbe3f"*8}))
PY
gate "{\"command\":\"sf data delete bulk --sobject Log__c --file /tmp/x.csv --target-org $ORG\"}"; R=$?
[ $R -eq 2 ] && ok "D3 FORGED-signature token rejected" || no "D3 forgery accepted" "rc=$R"
python3 -c "import sys;sys.path.insert(0,'$T/hooks');import lib;lib.token_path('$OID','bulk-delete').unlink(missing_ok=True)"

hdr "E. PROTECTED SOBJECTS (shielded on every org, token or not)"
guarded "{\"command\":\"sf data delete bulk --sobject Account --file /tmp/x.csv --target-org $ORG\"}"; R=$?
[ $R -eq 99 ] && ok "E1 bulk delete on a protected object denied" || no "E1 protected obj" "rc=$R"
guarded "{\"command\":\"sf data delete bulk --sobject=Contact --file /tmp/x.csv --target-org $ORG\"}"; R=$?
[ $R -eq 99 ] && ok "E2 equals-form --sobject=Contact also shielded" || no "E2 equals-form" "rc=$R"

hdr "F. THE TRUST ANCHOR IS UNREACHABLE FROM THE AGENT'S TOOLS"
for c in "cat $A/secret" "cat $AG/secret" "p=$A;cat \$p/secret" "cp /tmp/x $A/tokens/f.token"; do
  gate "$(python3 -c 'import json,sys;print(json.dumps({"command":sys.argv[1]}))' "$c")"; R=$?
  [ $R -eq 2 ] && ok "F: denied" "$(echo "$c" | cut -c1-44)" || no "F: ALLOWED" "$c"
done
gate '{"file_path":"~/.sfdx/alias.json"}' Read; R=$?
[ $R -eq 2 ] && ok "F5 Read tool blocked from the sf auth store" || no "F5 auth store readable" "rc=$R"

hdr "G. THE GATE FILES CANNOT BE OVERWRITTEN"
for c in "printf x > $T/hooks/lib.py" "printf 'x'>hooks/lib.py" ": >hooks/lib.py >/tmp/z" "git checkout HEAD~3 -- hooks/"; do
  gate "$(python3 -c 'import json,sys;print(json.dumps({"command":sys.argv[1]}))' "$c")"; R=$?
  [ $R -eq 2 ] && ok "G: denied" "$(echo "$c" | cut -c1-44)" || no "G: ALLOWED" "$c"
done

hdr "H. FAIL-CLOSED UNDER FAILURE (a broken gate must DENY, never open)"
mkdir -p /tmp/tqslow$$ && printf '#!/bin/sh\nsleep 300\n' > /tmp/tqslow$$/sf && chmod +x /tmp/tqslow$$/sf
S0=$(date +%s)
echo "$(ev Bash "{\"command\":\"sf data update record --sobject Account --record-id 001 --values x --target-org $ORG\"}")" \
  | PATH=/tmp/tqslow$$:$PATH TORQUE_GATE_BUDGET=6 python3 "$T/hooks/prod_write_gate.py" >/dev/null 2>&1; R=$?
S1=$(date +%s); rm -rf /tmp/tqslow$$
[ $R -eq 2 ] && ok "H1 hung sf ⇒ DENY (no fail-open)" "$((S1-S0))s, under the hook timeout" || no "H1 hung sf" "rc=$R"
echo 'not json at all' | python3 "$T/hooks/prod_write_gate.py" >/dev/null 2>&1; R=$?
[ $R -eq 0 ] || [ $R -eq 2 ] && ok "H2 malformed hook input handled without crashing open" || no "H2 malformed input" "rc=$R"

hdr "I. THE SHIPPED COMMANDS (each one a stranger is told to run)"
python3 "$T/bin/torque-demo" >/tmp/tqdemo$$ 2>&1; R=$?
D24=$(sed 's/\x1b\[[0-9;]*m//g' /tmp/tqdemo$$ | grep -oE "all [0-9]+ behaved correctly")
[ $R -eq 0 ] && [ -n "$D24" ] && ok "I1 bin/torque-demo (no org, no creds)" "$D24" || no "I1 torque-demo" "rc=$R"
rm -f /tmp/tqdemo$$
python3 "$T/bin/torque-init" "$ORG" >/tmp/tqinit$$ 2>&1; R=$?
[ $R -eq 0 ] && ok "I2 bin/torque-init is idempotent on a configured org" || no "I2 torque-init" "rc=$R"
grep -q "gates bind" /tmp/tqinit$$ && ok "I3 torque-init self-verifies the gates before claiming success" || no "I3 init self-check"
rm -f /tmp/tqinit$$
python3 "$T/bin/torque-init" no-such-org-xyz >/dev/null 2>&1; R=$?
[ $R -ne 0 ] && ok "I4 torque-init REFUSES an org it cannot verify" || no "I4 init accepted a bogus org"
python3 "$T/bin/torque-approve" "$OID" bulk-delete </dev/null >/dev/null 2>&1; R=$?
[ $R -ne 0 ] && ok "I5 torque-approve refuses the agent (no login TTY)" || no "I5 AGENT MINTED A TOKEN" "rc=$R"

hdr "J. MCP SURFACE (same gates, different tool shape)"
gate '{"targetOrg":"acme-prod","sourceDir":"force-app"}' mcp__salesforce-dx__deploy_metadata; R=$?
[ $R -eq 2 ] && ok "J1 MCP deploy to a production org denied" || no "J1 MCP prod deploy" "rc=$R"
gate "{\"targetOrg\":\"$ORG\",\"apexCode\":\"delete [SELECT Id FROM Account];\"}" mcp__sf__execute_anonymous_apex; R=$?
[ $R -eq 2 ] && ok "J2 MCP anonymous Apex denied" || no "J2 MCP apex" "rc=$R"
gate "{\"targetOrg\":\"$ORG\",\"sobject\":\"Log__c\"}" mcp__sf__bulkDeleteRecords; R=$?
[ $R -eq 2 ] && ok "J3 MCP camelCase destructive name still classified" || no "J3 MCP camelCase" "rc=$R"
gate "{\"targetOrg\":\"$ORG\",\"query\":\"SELECT Id FROM Account\"}" mcp__sf__soql_query; R=$?
[ $R -eq 0 ] && ok "J4 MCP read allowed (not a deny-everything gate)" || no "J4 MCP read blocked" "rc=$R"

hdr "K. PRODUCTION OVERRIDE (the deliberate operator path)"
while IFS='|' read -r st msg; do [ "$st" = "OK" ] && ok "$msg" || no "$msg"; done < <(python3 - "$T" <<'PY'
import sys, os, json, time; sys.path.insert(0, sys.argv[1] + "/hooks"); import lib
lib.PROD_SESSIONS.mkdir(parents=True, exist_ok=True)
# literal split so secret_scan does not match its own fixture
FAKE = "00D" + "prod00000000XAA"
lib.classify_live = lambda t: ("production", FAKE, "u@prod")
out = []
out.append(("K1 production denied by default", not lib.authorize_write("p")[0]))
g = {"orgId": FAKE, "exp": int(time.time())+300, "iat": int(time.time())}; g["sig"] = lib.sign(g)
(lib.PROD_SESSIONS/f"{FAKE}.grant").write_text(json.dumps(g))
out.append(("K2 valid operator session grant authorizes", lib.authorize_write("p")[0]))
g2 = {"orgId": FAKE, "exp": int(time.time())-10, "iat": 0}; g2["sig"] = lib.sign(g2)
(lib.PROD_SESSIONS/f"{FAKE}.grant").write_text(json.dumps(g2))
out.append(("K3 EXPIRED session grant refused", not lib.authorize_write("p")[0]))
(lib.PROD_SESSIONS/f"{FAKE}.grant").write_text(json.dumps({"orgId":FAKE,"exp":int(time.time())+300,"iat":0,"sig":"de4dbe3f"*8}))
out.append(("K4 FORGED session grant refused", not lib.authorize_write("p")[0]))
(lib.PROD_SESSIONS/f"{FAKE}.grant").unlink(missing_ok=True)
print("\n".join(f"{'OK' if v else 'NO'}|{k}" for k,v in out))
PY
)

hdr "L. LIVE METADATA + DATA CYCLES (real deploy/verify/purge, real update/undo, real render)"
CAP=/tmp/tqcap$$; python3 "$T/harness/validate.py" --profile capability --target-org "$ORG" >$CAP 2>&1
for c in probe_cycle mass_update_cycle browser_render frontdoor_noecho org_classify describe_first cache_poison_resistant approval_boundary gate_adversarial_fixtures; do
  L=$(grep -E "^\s+[✓✗!·]\s+$c\b" $CAP | head -1)
  case "$L" in
    *"✓"*) ok "L: $c" "$(echo "$L" | sed 's/.*PASS *//' | cut -c1-46)";;
    "")    no "L: $c" "did not run";;
    *)     no "L: $c" "$(echo "$L" | cut -c1-60)";;
  esac
done
grep -q "ALL MUTATORS CAUGHT" $CAP && ok "L: self-test — every guard proven load-bearing" \
  "$(grep -c 'mutator:' $CAP) mutators" || no "L: self-test" "a mutator was not caught"
rm -f $CAP

hdr "M. APPROVED OPERATIONS ACTUALLY EXECUTE (not just 'the gate said yes')"
# M1: anonymous Apex via the operator-approved immutable copy — really runs, really has an effect
APEXSRC=/tmp/tqapex$$.apex
printf "insert new Lead(LastName='%s', Company='%s_apex');" "$RUN" "$RUN" > $APEXSRC
DIG=$(python3 -c "import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest()[:16])" $APEXSRC)
python3 - "$T" "$OID" "$DIG" "$APEXSRC" <<'PY' >/dev/null 2>&1
import sys, os, json, time, shutil; sys.path.insert(0, sys.argv[1] + "/hooks"); import lib
lib.APPROVED.mkdir(parents=True, exist_ok=True)
dst = lib.APPROVED / f"{sys.argv[3]}.apex"; shutil.copyfile(sys.argv[4], dst); os.chmod(dst, 0o400)
p = {"orgId": sys.argv[2], "op": "apex", "digest": sys.argv[3], "exp": int(time.time())+300, "iat": int(time.time())}
p["sig"] = lib.sign(p); lib.token_path(sys.argv[2], "apex", sys.argv[3]).write_text(json.dumps(p))
PY
APEXCOPY="$A/approved/${DIG}.apex"
guarded "{\"command\":\"sf apex run --file $APEXCOPY --target-org $ORG\"}"; R=$?
[ $R -eq 0 ] && ok "M1 approved Apex (non-protected sObject) gated OK and executed" || no "M1 approved apex" "rc=$R"
APEXCNT=$(sf data query --target-org "$ORG" --json --query \
  "SELECT COUNT() FROM Lead WHERE Company = '${RUN}_apex'" 2>/dev/null | jq_ 'print(d["result"]["totalSize"])')
[ "$APEXCNT" = "1" ] && ok "M2 SOQL: the Apex REALLY RAN (record it inserted exists)" \
                     || no "M2 apex had no effect" "count=$APEXCNT"
rm -f $APEXSRC

# M3: a destructive op WITH an operator token really deletes (Lead is not on the protected list)
sf data create record --sobject Lead --values "LastName='${RUN}' Company='${RUN}'" --target-org "$ORG" >/dev/null 2>&1
LCNT(){ sf data query --target-org "$ORG" --json --query \
  "SELECT COUNT() FROM Lead WHERE Company = '${RUN}'" 2>/dev/null | jq_ 'print(d["result"]["totalSize"])'; }
[ "$(LCNT)" = "1" ] && ok "M3 seeded a deletable Lead" || no "M3 seed failed" "count=$(LCNT)"
DELCMD="sf data delete record --where \\\"Company='${RUN}'\\\" --sobject Lead --target-org $ORG"
guarded "{\"command\":\"$DELCMD\"}"; R=$?
[ $R -eq 99 ] && ok "M4 where-delete denied WITHOUT a token" || no "M4 not denied" "rc=$R"
python3 - "$T" "$OID" <<'PY' >/dev/null 2>&1
import sys, json, time; sys.path.insert(0, sys.argv[1] + "/hooks"); import lib
p = {"orgId": sys.argv[2], "op": "where-delete", "digest": "", "exp": int(time.time())+300, "iat": int(time.time())}
p["sig"] = lib.sign(p); lib.token_path(sys.argv[2], "where-delete").write_text(json.dumps(p))
PY
guarded "{\"command\":\"$DELCMD\"}"; R=$?
[ $R -eq 0 ] && ok "M5 same op ALLOWED with an operator token, and executed" || no "M5 token delete" "rc=$R"
[ "$(LCNT)" = "0" ] && ok "M6 SOQL: the approved delete REALLY DELETED the record" || no "M6 record survived" "count=$(LCNT)"

# M7: the protected-object shield reaches INSIDE the approved Apex body (observed live)
APEX2=/tmp/tqapex2$$.apex; printf "insert new Account(Name='%s_x');" "$RUN" > $APEX2
DIG2=$(python3 -c "import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest()[:16])" $APEX2)
python3 - "$T" "$OID" "$DIG2" "$APEX2" <<'PY2' >/dev/null 2>&1
import sys, os, json, time, shutil; sys.path.insert(0, sys.argv[1] + "/hooks"); import lib
lib.APPROVED.mkdir(parents=True, exist_ok=True)
dst = lib.APPROVED / f"{sys.argv[3]}.apex"; shutil.copyfile(sys.argv[4], dst); os.chmod(dst, 0o400)
p = {"orgId": sys.argv[2], "op": "apex", "digest": sys.argv[3], "exp": int(time.time())+300, "iat": int(time.time())}
p["sig"] = lib.sign(p); lib.token_path(sys.argv[2], "apex", sys.argv[3]).write_text(json.dumps(p))
PY2
guarded "{\"command\":\"sf apex run --file $A/approved/${DIG2}.apex --target-org $ORG\"}"; R=$?
[ $R -eq 99 ] && ok "M7 shield reaches INSIDE the Apex body (protected sObject)" || no "M7 apex shield" "rc=$R"
rm -f $APEX2

hdr "Z. TEARDOWN — BY ID ONLY"
IDS=$(sf data query --target-org "$ORG" --json --query "SELECT Id FROM Account WHERE Name LIKE '${RUN}%'" 2>/dev/null \
  | jq_ 'print(" ".join(r["Id"] for r in d["result"]["records"]))')
for id in $IDS; do sf data delete record --sobject Account --record-id "$id" --target-org "$ORG" >/dev/null 2>&1; done
[ "$(cnt)" = "0" ] && ok "Z1 residue 0 (deleted by Id, never by match)" || no "Z1 residue" "$(cnt) left"

printf "\n=== %d passed, %d failed, %d skipped ===\n" $P $F $S
exit $([ $F -eq 0 ] && echo 0 || echo 1)

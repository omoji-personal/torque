"""Flow rules. Each reads the flow file only; activation in the org is `torque check deployed`'s job."""
from __future__ import annotations

from collections import defaultdict
import re

from .components import Component
from .model import Context, Finding, find_ids, id_kind, version
from .rules_doc import FLOW_ELEMENTS, flow_elements

CONNECTORS = ("connector", "faultConnector", "defaultConnector", "nextValueConnector", "noMoreValuesConnector")
RECORD_CHANGES = {"recordCreates": "Create Records", "recordUpdates": "Update Records",
                  "recordDeletes": "Delete Records", "actionCalls": "Action", "apexPluginCalls": "Apex plug-in"}
IN_LOOP = {**RECORD_CHANGES, "recordLookups": "Get Records", "subflows": "Subflow"}
RECORD_TRIGGERS = ("RecordBeforeSave", "RecordAfterSave", "RecordBeforeDelete")
DEFAULT_NAME = re.compile(
    r"^(copy_\d+_of_.+|copy \d+ of .+|copy of .+|my(decision|rule|variable|waitevent)_?\d*.*|"
    r"(new_?)?(decision|assignment|screen|loop|get_records|create_records|update_records|delete_records|action|"
    r"subflow|outcome)_?\d+|new outcome)$", re.I)
KNOWN_SCREEN_EVENTS = {"flow__screeninit", "flow__screenfieldattributechange"}
CHOICE_FIELD_TYPES = {"RadioButtons", "DropdownBox", "MultiSelectCheckboxes", "MultiSelectPicklist", "Picklist"}
CHANGE_AWARE = re.compile(r"\b(ISCHANGED|PRIORVALUE|ISNEW)\s*\(|\$Record__Prior", re.I)
TEXT_TAGS = {"stringValue", "expression", "filterFormula", "formulaExpression"}


def _top(node):
    """The top-level child of the Flow that holds this node."""
    while node.parent is not None and node.parent.parent is not None:
        node = node.parent
    return node


def _label(element) -> str:
    return element.value("name") or element.tag


def targets(element) -> list[tuple[str, str]]:
    out = []
    for d in element.iter():
        if d.tag in CONNECTORS:
            t = d.value("targetReference")
            if t:
                out.append((d.tag, t))
    return out


def loop_bodies(flow) -> dict[str, set[str]]:
    """element name -> loops it runs inside. An element is inside a loop when it can be reached from the loop's
    next-value connector without passing through the loop element, and the loop can be reached again from it.
    Every connector is followed, fault connectors included; paths that leave the loop for good, and everything
    after "no more values", are outside."""
    elements = {e.value("name"): e for e in flow.children if e.tag in FLOW_ELEMENTS and e.value("name")}
    fwd = {n: [t for _, t in targets(e)] for n, e in elements.items()}
    rev = defaultdict(list)
    for n, ts in fwd.items():
        for t in ts:
            rev[t].append(n)
    inside: dict[str, set[str]] = defaultdict(set)
    for lname, e in elements.items():
        if e.tag != "loops":
            continue
        seen, stack = set(), [t for k, t in targets(e) if k == "nextValueConnector"]
        while stack:
            n = stack.pop()
            if n in seen or n == lname or n not in elements:
                continue
            seen.add(n)
            stack.extend(fwd.get(n, []))
        back, stack = set(), [lname]
        while stack:
            n = stack.pop()
            for p in rev.get(n, []):
                if p not in back and p != lname:
                    back.add(p)
                    stack.append(p)
        for n in seen & back:
            inside[n].add(lname)
    return inside


def check(comp: Component, ctx: Context) -> list[Finding]:
    if comp.type != "Flow" or comp.node is None:
        return []
    flow = comp.node
    out: list[Finding] = []
    for rule in (no_fault_path, work_in_loop, entry_conditions, after_save_same_record, hard_coded_id,
                 api_version, screen_actions, default_names, status, system_mode, choice_literal,
                 lookup_first_record):
        out += rule(comp, flow, ctx)
    return out


def _trigger(flow) -> tuple[str, str]:
    start = flow.child("start")
    if start is None:
        return "", ""
    return start.value("triggerType"), start.value("recordTriggerType")


def no_fault_path(comp, flow, ctx):
    trigger, _ = _trigger(flow)
    if trigger == "RecordBeforeSave":
        return []   # a before-save flow has no fault paths to add
    out = []
    for e in flow_elements(flow):
        if e.child("faultConnector") is not None:
            continue
        name = e.value("name")
        if e.tag in RECORD_CHANGES:
            out.append(Finding("flow.no-fault-path", "warning", comp,
                               f"{name} ({RECORD_CHANGES[e.tag]}) has no fault path.",
                               "Add a fault connector that reports the error (a custom error, a screen message or an "
                               "admin notification), so a failure does not end the flow unhandled.",
                               line=e.line, subject=name, match_lines={e.line}))
        elif e.tag == "recordLookups":
            out.append(Finding("flow.no-fault-path", "note", comp, f"{name} (Get Records) has no fault path.",
                               "A lookup seldom fails, but when it does the flow stops unhandled; add a fault "
                               "connector where a failure matters.", line=e.line, subject=name, match_lines={e.line}))
        elif e.tag == "subflows":
            out.append(Finding("flow.no-fault-path", "note", comp, f"{name} (Subflow) has no fault path.",
                               "Where Flow Builder offers Add Fault Path on the Subflow element, add one; otherwise "
                               "handle faults inside the subflow and return an error output the parent checks.",
                               line=e.line, subject=name, match_lines={e.line}))
    return out


def work_in_loop(comp, flow, ctx):
    inside = loop_bodies(flow)
    out = []
    for e in flow_elements(flow):
        name = e.value("name")
        if e.tag in IN_LOOP and inside.get(name):
            loops = ", ".join(sorted(inside[name]))
            out.append(Finding("flow.work-in-loop", "error", comp,
                               f"{name} ({IN_LOOP[e.tag]}) runs once for every item of loop {loops}; with many records "
                               "it hits governor limits.",
                               "Collect what you need inside the loop (Assignment to a collection) and run one Get, "
                               "Create, Update or Delete after it; or use a Collection Filter or Transform.",
                               line=e.line, subject=name, match_lines={e.line}))
    return out


def _has_entry(start) -> bool:
    return bool(start.all("filters") or start.value("filterFormula"))


def entry_conditions(comp, flow, ctx):
    trigger, when = _trigger(flow)
    if trigger not in RECORD_TRIGGERS:
        return []
    start = flow.child("start")
    obj = start.value("object") or "the object's"
    if not _has_entry(start):
        return [Finding("flow.no-entry-conditions", "warning", comp,
                        f"The record-triggered flow has no entry conditions, so it starts on every save of every {obj} "
                        "record.",
                        "Add entry conditions in the Start element so it runs only for the records it is meant for.",
                        line=start.line, subject="entry", match_lines={start.line, flow.line})]
    if when not in ("Update", "CreateAndUpdate") or start.flag("doesRequireRecordChangedToMeetCriteria"):
        return []
    if CHANGE_AWARE.search(start.value("filterFormula")) or "$Record__Prior" in _all_text(flow):
        return []
    effects = [e for e in flow_elements(flow) if e.tag in ("recordCreates", "actionCalls", "subflows",
                                                           "apexPluginCalls")]
    if not effects and not start.all("scheduledPaths"):
        return []
    what = ", ".join(e.value("name") for e in effects[:3]) or "its scheduled paths"
    return [Finding("flow.reruns-on-every-update", "warning", comp,
                    f"The flow runs again on every later save of {_a(obj)} record that still meets its entry "
                    f"conditions, so {what} runs again on an unrelated edit.",
                    "If it should run once, set the Start element to run only when a record is updated to meet the "
                    "condition requirements (doesRequireRecordChangedToMeetCriteria), or check $Record__Prior.",
                    line=start.line, subject="reruns")]


def _a(word: str) -> str:
    return ("an " if word[:1].lower() in "aeiou" else "a ") + word


def _all_text(flow) -> str:
    return " ".join(n.text for n in flow.iter() if n.text.strip())


def _updates_trigger_record(e, obj: str) -> bool:
    if e.value("inputReference") == "$Record":
        return True
    filters = e.all("filters")
    same = any(f.value("field") == "Id" and f.child("value") is not None
               and f.child("value").value("elementReference") == "$Record.Id" for f in filters)
    return same and (not e.value("object") or e.value("object") == obj)


def after_save_same_record(comp, flow, ctx):
    trigger, when = _trigger(flow)
    if trigger != "RecordAfterSave" or when == "Delete":
        return []
    start = flow.child("start")
    elements = flow_elements(flow)
    blockers = {"recordCreates", "recordDeletes", "actionCalls", "apexPluginCalls", "subflows", "waits", "screens"}
    if start.all("scheduledPaths") or any(e.tag in blockers for e in elements):
        return []
    updates = [e for e in elements if e.tag == "recordUpdates"]
    if not updates or not all(_updates_trigger_record(e, start.value("object")) for e in updates):
        return []
    return [Finding("flow.after-save-same-record", "note", comp,
                    "This after-save flow's only change is an update of the record that triggered it, which saves that "
                    "record again and runs its automation a second time.",
                    "Consider a before-save flow (Fast Field Updates): it sets the fields in the same save.",
                    line=updates[0].line, subject="same-record", match_lines={e.line for e in updates})]


def hard_coded_id(comp, flow, ctx):
    out = []
    for n in flow.iter():
        if n.tag not in TEXT_TAGS and not (n.tag == "text" and n.parent is not None and n.parent.tag == "textTemplates"):
            continue
        holder = _top(n)
        if holder.tag == "processMetadataValues":
            continue
        for token in find_ids(n.text):
            where = _label(holder)
            out.append(Finding("flow.hard-coded-id", "warning", comp,
                               f"{where} holds a hard-coded {id_kind(token)}; it points at a different record, or at "
                               "nothing, in another org.",
                               "Look the record up at run time (Get Records by a stable name or DeveloperName), or "
                               "keep the value in custom metadata or a custom label.",
                               line=n.line, subject=f"{where}:{token}", match_lines={n.line}))
    return out


def api_version(comp, flow, ctx):
    mine = version(flow.value("apiVersion"))
    if mine is None:
        return []
    line = flow.child("apiVersion").line
    out = []
    base = comp.baseline
    before = version(base.node.value("apiVersion")) if base is not None and base.node is not None else None
    if before is not None and mine < before:
        out.append(Finding("flow.api-version-behind", "error", comp,
                           f"The change lowers the flow's apiVersion from {before:.1f} to {mine:.1f}; runtime behaviour "
                           "can change.", f"Keep apiVersion at {before:.1f} or higher.",
                           line=line, subject="apiVersion-lowered", match_lines={line}))
        return out
    project = version(ctx.project.source_api_version)
    reference, why = max(((ctx.newest_flow_version or 0.0), f"the newest flow in scope ({ctx.newest_flow_name})"),
                         ((project or 0.0), "the project's sourceApiVersion"))
    if reference and mine < reference:
        out.append(Finding("flow.api-version-behind", "note", comp,
                           f"apiVersion {mine:.1f} is below {reference:.1f}, {why}.",
                           "Raise it when you next change this flow and test it again; the version decides runtime "
                           "behaviour.", line=line, subject="apiVersion", match_lines={line}))
    return out


def screen_actions(comp, flow, ctx):
    out = []
    for screen in flow.all("screens"):
        handled = {h.value("screenActionName").lower() for t in screen.all("triggers") for h in t.all("handlers")}
        for a in screen.all("actions"):
            name = a.value("name")
            if name and name.lower() not in handled:
                out.append(Finding("flow.screen-action-without-trigger", "warning", comp,
                                   f"Screen action {name} on screen {screen.value('name')} has no trigger, so it never "
                                   "runs (it deploys and still shows as configured).",
                                   "Add a trigger in the screen (eventName flow__screeninit or "
                                   "flow__screenfieldattributechange) whose handler names the action; in Flow Builder, "
                                   "set when the action runs.", line=a.line, subject=f"action:{name}"))
        for t in screen.all("triggers"):
            event = t.value("eventName")
            if event and event not in KNOWN_SCREEN_EVENTS:
                out.append(Finding("flow.screen-action-without-trigger", "note", comp,
                                   f"Trigger event {event} on screen {screen.value('name')} is not one of the event names "
                                   "Flow Builder writes; a misspelt name deploys and never fires.",
                                   "Use flow__screeninit or flow__screenfieldattributechange, or confirm the name in a "
                                   "version saved from Flow Builder.", line=t.line, subject=f"event:{event}"))
    return out


def default_names(comp, flow, ctx):
    hits = []
    for e in flow_elements(flow):
        for text in [e.value("name"), e.value("label")] + [r.value("label") for r in e.all("rules")]:
            if text and DEFAULT_NAME.match(text.strip()):
                hits.append((e, text))
                break
    label = flow.value("label")
    if label and re.match(r"(?i)^(copy( \d+)? of |untitled|new flow$)", label):
        hits.insert(0, (flow, label))
    if not hits:
        return []
    shown = ", ".join(t for _, t in hits[:5]) + (f" and {len(hits) - 5} more" if len(hits) > 5 else "")
    f = Finding("flow.default-names", "note", comp, f"{len(hits)} name(s) or label(s) look like defaults or copies: {shown}.",
                "Rename them to say what each element does; the next admin reads these names.",
                line=hits[0][0].line, subject="default-names")
    f.match_lines = {e.line for e, _ in hits}
    return [f]


def status(comp, flow, ctx):
    value = flow.value("status")
    node = flow.child("status")
    if not value or node is None:
        return []
    if value == "Active":
        return [Finding("flow.active-in-source", "note", comp,
                        "The file asks for status Active. A sandbox makes the deployed version the running one; "
                        "production does so only when 'Deploy processes and flows as active' is on, otherwise the new "
                        "version lands inactive.",
                        "Make sure that is intended; after the deploy, `torque check deployed` reports which version "
                        "is active.", line=node.line, subject="status")]
    return [Finding("flow.status-not-active", "note", comp,
                    f"The file asks for status {value}: the deploy saves an inactive version and the org keeps running "
                    "its current active version, if there is one.",
                    "Activate it after the deploy if it should run; `torque check deployed` reports which version is "
                    "active.", line=node.line, subject="status")]


def system_mode(comp, flow, ctx):
    if flow.value("runInMode") != "SystemModeWithoutSharing":
        return []
    desc = flow.value("description").lower()
    if "sharing" in desc or "system mode" in desc or "system context" in desc:
        return []
    return [Finding("flow.system-mode-without-sharing", "note", comp,
                    "The flow runs in system mode without sharing, and its description does not say why.",
                    "Say in the description why it must ignore sharing and who can start it, or set the run mode back "
                    "to the default.", line=flow.child("runInMode").line, subject="run-mode")]


def choice_literal(comp, flow, ctx):
    choices = {f.value("name") for f in flow.iter() if f.tag == "fields" and f.value("name")
               and (f.all("choiceReferences") or f.value("fieldType") in CHOICE_FIELD_TYPES)}
    out = []
    for formula in flow.all("formulas"):
        expr = formula.value("expression")
        for name in sorted(choices):
            ref = r"\{!\s*" + re.escape(name) + r"\s*\}"
            pattern = (ref + r"\s*(=|==|<>|!=)\s*['\"]|['\"][^'\"]*['\"]\s*(=|==|<>|!=)\s*" + ref
                       + r"|\b(CONTAINS|BEGINS)\s*\(\s*" + ref + r"\s*,\s*['\"]")
            if re.search(pattern, expr, re.I):
                out.append(Finding("flow.choice-compared-to-literal", "warning", comp,
                                   f"Formula {formula.value('name')} compares choice field {name} to a text literal. In a "
                                   "formula a choice field gives the choice's label, not its stored value, so the test "
                                   "can silently never match.",
                                   "Compare the field to the choice resource in a Decision instead, or compare against "
                                   "the label on purpose and say so in the formula's description.",
                                   line=formula.line, subject=f"{formula.value('name')}:{name}"))
    return out


def lookup_first_record(comp, flow, ctx):
    out = []
    for e in flow.all("recordLookups"):
        if e.flag("storeOutputAutomatically") and e.child("getFirstRecordOnly") is None:
            out.append(Finding("flow.lookup-missing-first-record-only", "warning", comp,
                               f"{e.value('name')} (Get Records) stores its output automatically but does not say "
                               "whether it gets the first record only; the deploy fails on this.",
                               "Set getFirstRecordOnly to true or false.", line=e.line, subject=e.value("name")))
    return out

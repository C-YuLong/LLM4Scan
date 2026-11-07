"""Task-two script and netlist repair with cited diagnoses and real EQY proof."""

import difflib
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import time

from scan_runner import run_scan, generated_script, record_change, publish_run, resolve_selectors
from acceptance import STAGES, inspect_run, extract_task_constraints
from model_contract import TASK2, REPAIR, ModelResponseError, prune_invalid_items, strip_extra_fields, validate, verification as verification_schema
from control_context import component_sources
from netlist_repair import edited_netlists, repaired_script
from knowledge_search import retrieve


_RUNNER_PATH_ASSIGNMENT = re.compile(
    r"set\s+(?:script_dir|base_dir|out_rpt_dir|out_res_dir)\s+[^\n;]+"
)

def sanitize_diagnoses(plan, original_script, task_spec=None, original_sources=None):
    """Keep only independently citable findings from a model diagnosis.

    The model is useful for naming a root cause, but it is not allowed to
    create evidence.  In particular, path assignments injected by the runner
    are implementation details and must never become scored findings.  The
    rejected records are retained for audit/debugging, while the returned
    issue list is the stable input to the verifier.
    """
    raw = plan.get("issues", [])
    if not isinstance(raw, list):
        raise ValueError("Model diagnosis issues must be an array")
    accepted, rejected = [], []
    seen_pairs = set()
    for position, issue in enumerate(raw, 1):
        reason = None
        requires_correction = True
        if not isinstance(issue, dict):
            reason = "issue is not an object"
        else:
            command = issue.get("original_command")
            root = issue.get("root_cause")
            required = ("phenomenon", "located_object", "root_cause", "original_command", "fix_action")
            evidence = {'original.dofile': original_script, **(original_sources or {})}
            source_name = issue.get('original_source', 'original.dofile')
            source_selection = re.fullmatch(r'L(\d+)(?:-L?(\d+))?', command) if isinstance(command, str) else None
            source_line = None
            if source_selection:
                start, end = int(source_selection[1]), int(source_selection[2] or source_selection[1])
                source_lines = evidence.get(source_name, '').splitlines()
                if 1 <= start <= end <= len(source_lines):
                    issue['model_original_command'] = command
                    command = issue['original_command'] = '\n'.join(source_lines[start - 1:end])
                    source_line = issue['original_line'] = start
                else:
                    reason = 'Original source citation line range is invalid'
            quote = issue.get('violated_requirement', '')
            selection = re.fullmatch(r'L\d+(?:-L?\d+)?(?:\s*;\s*L\d+(?:-L?\d+)?)*', quote) if isinstance(quote, str) else None
            if task_spec is not None and selection:
                ranges = [(int(start), int(end or start))
                          for start, end in re.findall(r'L(\d+)(?:-L?(\d+))?', quote)]
                lines = task_spec.splitlines()
                issue['model_violated_requirement'] = quote
                if all(1 <= start <= end <= len(lines) for start, end in ranges):
                    # A model may cite two actual locations (L3; L8). Preserve
                    # their enclosing source span rather than rejecting valid
                    # locations or assembling a quotation absent from the task.
                    start, end = min(a for a, _ in ranges), max(b for _, b in ranges)
                    quote = issue['violated_requirement'] = '\n'.join(lines[start - 1:end])
                else:
                    reason = 'Task citation line range is invalid'
            if task_spec is not None and isinstance(quote, str) and quote.strip() and quote not in task_spec:
                # Chat models may lose Markdown indentation while copying a
                # citation. Resolve whitespace-only differences to one real
                # source span; never accept paraphrases or ambiguous matches.
                pattern = r'\s+'.join(re.escape(part) for part in quote.split())
                matches = list(re.finditer(pattern, task_spec))
                if len(matches) == 1:
                    issue['model_violated_requirement'] = quote
                    quote = issue['violated_requirement'] = matches[0].group()
            if any(not isinstance(issue.get(field), str) or not issue[field].strip() for field in required):
                reason = "missing non-empty diagnosis field"
            elif source_name not in evidence or command not in evidence[source_name]:
                reason = "original_command is not an exact substring of the original script"
            elif source_line is None and evidence[source_name].count(command) != 1:
                locations = [evidence[source_name][:match.start()].count('\n') + 1
                             for match in re.finditer(re.escape(command), evidence[source_name])]
                reason = ('Original citation is ambiguous at source lines ' + ', '.join(map(str, locations)) +
                          '; quote one contiguous exact span from its distinguishing scope command '
                          '(for example set_current_scan_partition) through the defective command, '
                          'including all intervening lines. Repeating the same single line does not locate it uniquely')
            elif not isinstance(quote, str) or (issue.get('category', 'configuration') == 'configuration' and not quote.strip()):
                reason = 'Configuration diagnosis requires a task quote'
            elif task_spec is not None and quote and quote not in task_spec:
                reason = "violated_requirement is not an exact task quote"
            elif _RUNNER_PATH_ASSIGNMENT.fullmatch(command.strip()):
                reason = "runner-owned path assignment is not a task defect"
            elif issue.get('defective_parameter') and not re.fullmatch(r'(?:-[A-Za-z_]\w*|arg[12])', issue['defective_parameter']):
                reason = 'defective_parameter must name one option or positional operand arg1/arg2; independent defects need separate issues'
            elif (issue.get('category', 'configuration') == 'configuration'
                  and issue.get('defective_parameter') in {'-internal_clock', '-internal_clocks'}
                  and not re.search(r'internal_clocks?|内部时钟', quote, re.I)):
                reason = 'Source-clock count does not specify an internal-clock policy; a configuration allegation needs an explicit internal-clock requirement, not a preferred implementation'
            elif (issue.get('category', 'configuration') == 'configuration' and
                  issue.get('defective_parameter') == 'arg1' and '允许忽略' in quote):
                handling = re.fullmatch(r'\s*set_scan_drc_rule_handling\s+\{([^}]+)\}\s+Ignore\s*', command)
                permitted = set(re.findall(r'DFTR-[A-Z]+\d+|DFTR\d+', quote))
                if handling and set(handling[1].split()) <= permitted:
                    reason = ('The cited task text permits these Ignore rules but does not require adding every permitted rule. '
                              'The original list contains only permitted rules, so this is not a configuration contradiction. '
                              'Do not recategorize that permission alone as a DRC defect; an actual necessary-rule omission needs separate causal evidence')
                    # This allegation is disproved by the cited permission and
                    # original command, unlike a finding with a broken citation.
                    # Preserve the rejection without regenerating a whole plan
                    # just to ask the model to withdraw an already rejected claim.
                    requires_correction = False
            if reason is None and isinstance(issue, dict) and all(
                    isinstance(issue.get(field), str) and issue[field].strip()
                    for field in required):
                command_key = re.sub(r"\s+", " ", command.strip())
                root_key = re.sub(r"\s+", " ", root.strip().lower())
                line = source_line or evidence[source_name][:evidence[source_name].index(command)].count('\n') + 1
                pair = (source_name, line, command_key, root_key)
                if pair in seen_pairs:
                    reason = "duplicate command and root cause"
                else:
                    seen_pairs.add(pair)
        if reason:
            rejected.append({"position": position, "reason": reason, "issue": issue,
                             "requires_correction": requires_correction})
        else:
            accepted.append(issue)
    plan["issues"] = accepted
    plan["rejected_diagnoses"] = rejected
    return plan


def diagnosis_location(issue, evidence):
    """Publish the actual statement location, not an unverified invented path."""
    source_name = issue.get('original_source', 'original.dofile')
    command = issue['original_command']
    line = issue.get('original_line') or evidence[source_name][:evidence[source_name].index(command)].count('\n') + 1
    end = line + len(command.splitlines()) - 1
    return source_name, line, end, f'{source_name}:L{line}-L{end} — {command.strip()}'


def diagnosis_source_texts(source, issues):
    """Read only exact, permitted original netlist sources cited by diagnoses."""
    result = {}
    for name in sorted({issue.get('original_source', 'original.dofile') for issue in issues}):
        path = (source / name).resolve()
        if name != 'original.dofile' and path.is_relative_to((source / 'netlist').resolve()) and path.suffix == '.v' and path.is_file() and name == path.relative_to(source).as_posix():
            result[name] = path.read_text(encoding='utf-8')
    return result


def stage_lines(script):
    return [(i, line.split()[0]) for i, line in enumerate(script.splitlines()) if line.strip() and line.split()[0] in STAGES]


def explicit_diagnosis_gaps(script, requirements, original_checks, issues, *, original_log='', original_reports=None):
    """Require citations for explicit signal-value contradictions already observed.

    Only literal, single-port declarations, measured configuration contradictions,
    confirmed missing wrapper ports and task-required missing exports are covered.
    Omitted defaults, arbitrary Tcl, inferred requirements and DRC symptoms are
    not converted into new findings by this guard.
    """
    gaps = []
    configuration = '\n'.join(text for text in (original_reports or {}).values()
                              if 'ScanConfigurationParameter' in text)
    parameters = [(name, value, 'task_config_' + name) for name, value in requirements.get('config', {}).items()]
    if len(requirements.get('formats', [])) == 2:
        parameters += [(name, value, 'task_port_formats') for name, value in
                       zip(('si_port_format', 'so_port_format'), requirements['formats'])]
    for name, expected, check in parameters:
        if original_checks.get(check) is not False:
            continue
        observed = set(re.findall(r'(?m)^\s*' + re.escape(name) + r'\s+(\S+)[ \t]*$', configuration))
        if len(observed) != 1 or observed == {expected}:
            continue
        if not any(issue.get('defective_parameter') == '-' + name for issue in issues):
            gaps.append({'failed_original_check': check, 'required_field': name, 'required_value': expected,
                         'original_value': next(iter(observed)),
                         'basis': 'Actual original configuration report contradicts this explicit task value; diagnose this parameter independently rather than folding it into another setting'})
    for line in script.splitlines():
        if not re.match(r'^\s*set_scan_signal\s', line):
            continue
        options = {k: v.strip('{}"') for k, v in re.findall(
            r'-(type|port|off_state|constant_value|usage)\s+(\S+)', line)}
        port, role = options.get('port'), options.get('type')
        check = f'task_signal_{port}_{role}'
        if original_checks.get(check) is not False and original_checks.get(f'task_signal_{port}_usage') is not False:
            continue
        rule = next((r for r in requirements['signals'] if r['port'] == port and r['role'] == role), None)
        fields = ['constant_value'] if role == 'constant' else ['off_state']
        if role == 'scan_enable':
            fields.append('usage')
        for field in fields:
            check = f'task_signal_{port}_usage' if field == 'usage' else f'task_signal_{port}_{role}'
            if original_checks.get(check) is not False or not rule or field not in rule or field not in options or options[field] == rule[field]:
                continue
            if field != 'usage' and options[field] not in {'0', '1'}:
                continue
            covered = any(issue.get('defective_parameter') == '-' + field and
                          (issue['original_command'].strip() in line or line.strip() in issue['original_command']) and
                          re.search(r'-port\s+[{"]?' + re.escape(port) + r'(?:\s|[}"]|$)', issue['original_command'])
                          for issue in issues)
            if not covered:
                gaps.append({'original_command': line.strip(), 'failed_original_check': check,
                             'required_field': field, 'required_value': rule[field], 'original_value': options[field]})
    # SCAN-4948 identifies an actual nonexistent port, not a preference about
    # enabling wrappers. Only attribute it to a matching literal command in a
    # completed original execution; don't convert arbitrary warnings to faults.
    if original_checks.get('execution_completed') is True:
        for port in dict.fromkeys(re.findall(r"\[\s*SCAN-4948\]\s+Port '([^']+)' does not exist", original_log)):
            for match in re.finditer(r'(?m)^[ \t]*set_wrapper_cfg\b(?:[^\n]*\\\n)*[^\n]*', script):
                command = match.group().strip()
                selected = re.search(r'-port\s+(\{[^}]+\}|"[^"]+"|[^\s]+)', command)
                if not selected or selected[1].strip('{}"') != port:
                    continue
                covered = any(issue.get('original_source', 'original.dofile') == 'original.dofile' and
                              issue.get('defective_parameter') == '-port' and
                              (command in issue['original_command'] or
                               issue['original_command'].strip() in command)
                              for issue in issues)
                if not covered:
                    gaps.append({'original_command': command, 'failed_original_check': 'no_unmatched_object_selectors',
                                 'required_field': 'port', 'original_value': port,
                                 'basis': 'Original SCAN-4948 confirms this literal wrapper port does not exist; distinguish this invalid selector from wrapper enable or chain-count configuration'})
    for header in requirements.get('report_headers', []):
        if header not in {'ObjState', 'Obj_State', 'Obj_?State'}:
            continue
        check = 'task_report_' + header
        if original_checks.get(check) is not False or 'rpt_scan_element' in script:
            continue
        covered = any('rpt_scan_element' in issue.get('fix_action', '') and
                      (not issue.get('defective_parameter') or
                       'rpt_scan_element' in issue.get('root_cause', '')) for issue in issues)
        if not covered:
            gaps.append({'failed_original_check': check, 'required_command': 'rpt_scan_element -type all',
                         'basis': 'Task explicitly requires the register/object report, and the complete original dofile lacks its export command; not merely a missing output after an earlier failure'})
    if (original_checks.get('execution_completed') is True and
            original_checks.get('task_required_outputs_present') is False):
        for command, suffix, label in (('dump_def', '.def', 'SCANDEF'), ('dump_ctl', '.ctl', 'CTL'),
                                       ('dump_netlist', '.v', 'post-scan netlist')):
            required_files = [name for name in requirements.get('outputs', []) if name.lower().endswith(suffix)]
            if not required_files or re.search(r'(?m)^\s*' + command + r'\b', script):
                continue
            covered = any(command in issue.get('fix_action', '') or
                          label.casefold() in issue.get('fix_action', '').casefold() or
                          any(name in issue.get('fix_action', '') for name in required_files) for issue in issues)
            if not covered:
                gaps.append({'failed_original_check': 'task_required_outputs_present', 'required_command': command,
                             'required_files': required_files,
                             'basis': 'Task explicitly requests this output and the completed original script lacks its export command; not an auxiliary report or an output missing because execution stopped early'})
    return gaps


def order_full_flow(script):
    """Compile the documented order for a single full-insertion flow only."""
    lines = script.splitlines()
    stages = stage_lines(script)
    if sorted(command for _, command in stages) != sorted(STAGES):
        raise ValueError("Expected exactly one DRC, chain preview and insertion stage")
    # Preserve intervening report statements; reorder only the three stage slots.
    commands = {command: lines[index] for index, command in stages}
    for (index, _), command in zip(stages, STAGES):
        lines[index] = commands[command]
    return "\n".join(lines) + "\n"


def repair(source, target, started, deadline, generate, design_outline):
    original = (source / "original.dofile").read_text(encoding="utf-8")
    original_sources = {}
    run1 = target / "runs/R1"
    # Preserve command semantics; replace only the supplied example's path setup.
    relocated = original
    for name, value in {
        "script_dir": source, "base_dir": run1,
        "out_rpt_dir": run1 / "reports", "out_res_dir": run1 / "deliverables",
    }.items():
        relocated = re.sub(r"(?m)^set\s+" + name + r"\s+[^\n]*$", lambda m: f"set {name} {{{value.as_posix()}}}", relocated)
    (target / "diffs").mkdir()
    (target / "diffs/original_to_R1_paths.diff").write_text("".join(difflib.unified_diff(original.splitlines(True), relocated.splitlines(True), fromfile="input/original.dofile", tofile="runs/R1/deliverables/R1.dofile")), encoding="utf-8")
    spec = (source / "task_spec.md").read_text(encoding="utf-8")
    modules, instances = design_outline(source)
    # Task-only extraction does not depend on R1; overlap it with the Scan run.
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=1) as pool:
        extraction = pool.submit(extract_task_constraints, spec, generate, max(1, deadline-time.monotonic()), target, design_context=instances)
        first = run_scan(relocated, run1, max(1, deadline - time.monotonic()), source=source)
        requirements, uncovered = extraction.result()
    log1 = (run1 / "R1.log").read_text(encoding="utf-8", errors="replace")
    guide = (Path(__file__).parent / "commands.md").read_text(encoding="utf-8")
    guide += '\nRetrieved knowledge (source/confidence/scope retained):\n' + json.dumps(retrieve(spec + '\n' + log1[:22000]), ensure_ascii=False, separators=(',', ':'))
    instruction = (
        "Repair a Scan Insertion dofile from actual task, source and tool evidence. "
        "task_contract is the frozen task-derived constraint set. Honor allowed_ignores: an empty "
        "list forbids ALL Ignore/Warning rule overrides, including tie-rule examples from the guide. "
        "Allowed residual violations do not authorize suppression. Keep the original task as "
        "authority for requirements outside the supported contract. "
        "Preserve original legal DRC Ignore handling when its rules are in allowed_ignores; "
        "repairing unrelated commands must not silently remove it and reactivate those failures. "
        "Only task-frozen scan_membership exclusions authorize making FFs user-nonscannable. "
        "Do not exclude RAM/storage FFs to clear clock DRC: repair their actual test-clock controls. "
        "Export rpt_scan_element -type all whenever using set_scan_element so actual user exclusions "
        "can be checked. This auxiliary report is not itself an original task defect unless requested. "
        "hierarchical_instances contains selector tokens such as [[H12]]; use these in dofile "
        "instead of retyping long paths, e.g. set_scan_element false {[[H12]]}. The runner "
        "expands each token to its exact source instance path. "
        "module_declarations includes the actual top interface. Large-design hierarchy "
        "context is filtered to major hierarchy and clock/JTAG/DFT controls, not exhaustive. "
        "Return only the fields in the supplied response schema. netlist_edits is empty when no "
        "netlist change is needed. Issue diagnosis rules below apply only when the schema includes "
        "issues or additional_issues; a follow-up may add newly evidenced ORIGINAL defects "
        "under additional_issues, not re-list previous issues or candidate-introduced mistakes. "
        "Issue category is command, drc or configuration. "
        "original_source is original.dofile or an exact input netlist relative path. original_command "
        "should select actual original source lines, e.g. L12 or L12-L14; the runner binds "
        "that exact location and text. These are source line numbers, not task line numbers. "
        "Alternatively it can be an exact unique nonempty source substring without line prefixes. "
        "If quoting text and the same command occurs more than once, quote a contiguous block "
        "from the relevant set_current_scan_partition through that command, retaining all intervening lines. "
        "Never quote repeated text alone without a source line selector. Identify defects "
        "including ones masked by early errors. Do not claim masked errors were observed at runtime. "
        "DRC totals are per RULE, not necessarily per port or domain. Do not attribute a total "
        "or an 'other cells' count to one control unless its complete membership proves that "
        "attribution. Likewise the first reported sample may belong to a DIFFERENT domain: "
        "use the control_samples in component_sources and the actual connections to identify its "
        "owner. For an unlisted domain, state the missing declaration and source connection "
        "as static evidence; do not claim the report named that domain or invent its count. "
        "Count each independent root cause ONCE: missing load_netlist, failed present_design and "
        "missing downstream outputs are one root cause, not three issues. The runner relocated "
        "path variables correctly for this run; these assignments are NOT defects and are NOT "
        "competition requirements. Never count runner implementation choices as task defects. "
        "An optional feature being unrequested does not itself prove it forbidden. In particular, "
        "do not count existing wrapper enable as another root solely because it contributes chains "
        "to an already diagnosed channel-budget failure; require an independent prohibition or causal defect. "
        "Emit exactly one issue object per independent root cause. If two unrelated causes "
        "occur on the same command, emit two objects with distinct root_cause values; if one "
        "cause affects several commands, emit one object and cite the smallest exact command "
        "that proves it. Before listing findings, consider the minimal semantic edits needed: "
        "replacing a wrong object selector is ONE root cause, not separate invalid-object and "
        "missing-correct-object defects. Downstream failures repaired by that same substitution "
        "are symptoms of the same root. Keep independent option/value defects separate even "
        "when missing declarations share one insertion point: omitted controls for two distinct "
        "reset ports/domains are separate findings if each needs its own declaration. Name the "
        "specific port in each root and fix, rather than bundling all reset controls together. "
        "This does not split one shared driver fault into its downstream fanout symptoms. "
        "This also applies to positional operands: use arg1/arg2 "
        "for the first/second operand, with a braced list counting as one. Distinguish a "
        "bad object/rule identifier from an independently wrong requested setting. A legal "
        "level or value that violates this task is a configuration defect, not invalid syntax. "
        "A legal cell-mapping declaration does not become a separate defect because unrelated "
        "clock/reset DRC failures made the mapped cells temporarily non-scannable. "
        "If the original already satisfies the task, return issues=[]; "
        "never invent a defect to justify producing a script. "
        "Review each failed task-explicit signal-value check in original_acceptance. A wrong "
        "reset polarity and an independent wrong mode constant remain separate defects even "
        "when one masks the other's DRC symptom. Do not silently fix such a contradictory "
        "declaration without reporting its own original command and root cause. "
        "For configuration defects, violated_requirement must be a line selector such as L12-L14 "
        "into task_spec_lines. The program obtains the exact task text from those lines, including Markdown; "
        "if citing separated locations such as L12; L18, their full enclosing task span is retained. "
        "do not copy, translate or paraphrase the quote. For command/DRC defects use an empty string "
        "when no task quote is needed, or a real line selector. Distinguish a required outcome from one "
        "possible way of achieving it: missing an optional command is not a defect if existing "
        "logic and reports already establish the required behavior. Trace the supplied small "
        "component_sources before alleging a control signal needs an additional constraint. "
        "For each allegation examine original_acceptance and original_reports as well as the "
        "original code; successful reports are not complete proof, but contradict unsupported "
        "claims of missing coverage or blocked clocks. "
        "A true original_acceptance.checks.task_config_X means the ORIGINAL effective setting X "
        "already satisfies that checked requirement. Its absence as an explicit option is not "
        "a defect: verify the actual reported value before alleging a missing setting. "
        "An unspecified scan_enable is automatically generated by the tool; absent an explicit "
        "task port/usage requirement or actual evidence of blocked required controls, omitting "
        "that optional declaration is not a defect. Adding it to your repair proves nothing "
        "about whether it was necessary in the original. "
        "For the REPAIRED candidate, actual ICG test inputs tied off still need controllable "
        "global clock_gating/all scan enable before DRC; local scan-only controls or automatic "
        "port creation do not establish that path. Do not omit a necessary global control "
        "merely to avoid an unsupported original allegation, and do not replace it with "
        "guessed internal-clock associations. "
        "For task_wrapper_port_P=true, check_details proves that every original bit of P "
        "already has the requested wrapper style/type, even without a local override. "
        "Separate independently wrong option values even when they share one command: usage, "
        "inactive polarity, maximum length and port naming are distinct settings, not one "
        "generic configuration issue. Input and output port formats are TWO independent options, "
        "-si_port_format and -so_port_format; do not put both into a finding labelled with only one. "
        "Do not attach an unsupported omission to a real defect. "
        "Explicit source-instance lists and literal scan-segment registrations are legal; "
        "being hard-coded is not itself a defect. Before alleging incomplete registration, "
        "name a concrete required shift-register endpoint pair missing from the full original "
        "dofile. Empty or partial identification reports under failed original DRC do not "
        "prove such an omission. Preserve original registrations unless actual topology or "
        "task evidence contradicts them; dynamic Tcl discovery is optional, not mandatory. "
        "defective_parameter identifies ONE wrong or omitted Tcl option, e.g. -usage or -off_state, "
        "or arg1/arg2 for the first/second positional operand (a braced list counts as one). "
        "It is empty only for a whole-command or structural cause such as command order or a netlist wire. "
        "The root_cause and fix_action must concern that one defect, not a bundle of edits. "
        "When the task explicitly requires BOTH associating a clock-path latch output and "
        "excluding that latch, examine both original obligations separately: the association "
        "propagates the clock, while exclusion handles clock-on-data DFTR10. Do not silently "
        "apply the exclusion while diagnosing only the association. An object already absent "
        "from scan because it failed DRC does not prove the required DRC-free exclusion exists. "
        "Describe the observed fault as ORIGINAL/R1 evidence, not as a defect of the final "
        "repaired output. For binary polarities, state the numeric original value: off_state=1 "
        "means inactive HIGH (active LOW), and off_state=0 means inactive LOW (active HIGH). "
        "Do not invert that meaning in phenomenon prose while giving a correct fix elsewhere. "
        "Never combine unrelated causes with 'and'. Missing runner-requested "
        "auxiliary evidence exports is not a defect unless the task explicitly requires that "
        "named output. A task statement saying a DRC rule may be ignored is permission, not a "
        "requirement to add an ignore configuration; report such a rule only when actual DRC "
        "evidence shows its unhandled effect violates another explicit requirement. "
        "In the repaired script, apply task-permitted DFTR-TIE0/DFTR-TIE1 Ignore handling "
        "before DRC so exempt tie warnings do not remain in the final report. This is not "
        "by itself a separately scored original defect. "
        "Never overwrite inputs, libraries or protected files. Task2 may repair a NETLIST WORKING COPY "
        "when required and not forbidden by this task. Return netlist_edits as path/before/after strings: "
        "each before must match exactly once in the original input, path must be a supplied netlist/*.v. "
        "Every round's edits are relative to the ORIGINAL inputs, not the previous candidate. "
        "The control trace's requested_type_coverage counts source declarations of types with "
        "observed functional-input loss. complete=true means ALL of those declarations are "
        "fully shown in its sources with original line numbers, even though unrelated logic "
        "is omitted. This is declaration coverage, not an elaborated physical-cell count. "
        "Do not claim their exact statements are unavailable merely because the full file is "
        "not included; use those actual complete excerpts for before strings without L prefixes. "
        "If the task disallows the directly compatible scan type, preserve the actual source "
        "next-state function in an equivalent working copy before mapping to the allowed type. "
        "rewrite_cell_candidates names actual library plain-FF and named-mux candidates; "
        "their supplied functions, not their names, determine pin semantics. Use structural "
        "gate instances with declared wires for a data-enable feedback mux, then let Scan map "
        "the plain FF. The installed Scan reader rejected behavioral Boolean expressions "
        "directly inside cell-pin connections even when Yosys EQY accepted them. Do not "
        "introduce undriven scan inputs into the functional Pre-scan or merely ask the runner "
        "to synthesize a working copy: the runner applies only your explicit netlist_edits. "
        "The runner creates a complete pre_scan_Rn.v and substitutes its load_netlist command, "
        "then proves original versus candidate using real Yosys EQY before Scan. No fixed-input assumptions "
        "or unknown/black-box shortcuts are allowed. No fabricated results. "
        "A ban on overwriting input files or changing FUNCTIONAL LOGIC does not by itself ban an "
        "equivalent working-copy representation; an explicit ban on netlist modification does. "
        "If constant_output_bits_preserved fails, DRC=0 does not make the emitted circuit correct. "
        "Use the observed port bits and exact original constant assignments to investigate a "
        "representation the installed tool preserves, with EQY before use when edits are permitted. "
        "Do not misdiagnose valid Verilog as illegal syntax, modify post-scan outputs, or bypass the check. "
        "The runner, not Tcl, creates decision_log.json and publishes the accepted script "
        "as final.dofile after verification. Never write either of those files in dofile. "
        "The runner defines input_dir, out_res_dir and out_rpt_dir. Use those variables, "
        "export with literal paths like $out_res_dir/file.v or $out_rpt_dir/file.rpt; "
        "do not redefine them. Correct all confirmed faults in one script and run the full flow. "
        "Use only documented commands. End with exit."
        " The replacement must contain exactly one examine_scan_drc, exactly one "
        "examine_scan_chain and exactly one insert_dft_logic in that order; do not "
        "add a second DRC merely to produce another report. Treat omitted optional "
        "scan-enable -usage as the documented default only when the task guide says "
        "so, and do not call a default setting a defect without task or runtime evidence. "
        "Likewise, reset/clock inactive polarity is task-specific: cite the task's "
        "explicit polarity or an actual DRC consequence, never a generic off_state rule."
        " A positive-edge FF alone does not require clock off_state=0. When another "
        "missing control or internal-clock association explains DFTR1, the same rule "
        "count does not independently establish a polarity defect. Preserve that "
        "clock level unless the task or actual causal evidence contradicts it."
        " Preserve every original clock, partition, internal-clock and chain-length "
        "setting unless the task explicitly contradicts it or the actual report proves "
        "that particular setting is the cause; do not simplify a multidomain flow."
        " Always export rpt_scan_cfg, rpt_scan_signal, rpt_scan_chain, "
        "rpt_scan_chain_cell and rpt_insertion_info after insertion, plus the DRC report, "
        "so acceptance can examine actual configuration and coverage. These are evidence "
        "exports, not additional alleged defects when absent from the original script."
        " Also satisfy ALL task-requested reports in task_contract.report_headers: an "
        "inserted-netlist register/object report (ObjState/Obj_State) is rpt_scan_element "
        "-type all, not rpt_scan_chain_cell. A missing export explicitly requested by the "
        "task is a real original omission when the full original dofile lacks its command; "
        "do not confuse that with auxiliary evidence added only by the runner."
    )
    original_acceptance, original_artifacts, _ = inspect_run(run1, relocated, first, full_insertion=True,
        task_spec=spec, design_context=instances, task_requirements=requirements, uncovered=uncovered, source=source)
    components = component_sources(source, {item['original_cell']
        for item in first.get('cell_mapping_input_losses', [])})
    numbered_original = '\n'.join(f'L{i}: {line}' for i, line in enumerate(relocated.splitlines(), 1))
    # Cell reports carry observed clock edges; partition reports describe selectors,
    # not actual cell edges. Retain a bounded sample instead of dropping them.
    # Cell-detail tables repeat hundreds of padded rows. Complete membership
    # stays in the deterministic checks and raw files; a smaller source sample
    # leaves the short case deadline for the actual repair and verification.
    original_reports = {name: text[:4000 if re.search(r'CellNo\s+InstanceName', text[:1000]) else 12000]
                        for name, text in original_artifacts.items() if name.endswith(".rpt")}
    prompt = json.dumps({"task_spec": spec,
        "task_spec_lines": '\n'.join(f'L{i}: {line}' for i, line in enumerate(spec.splitlines(), 1)),
        "task_contract": requirements, "original_working_dofile": numbered_original, "actual_run1_log": log1[:22000],
        "original_acceptance": original_acceptance,
        "observed_diagnosis_requirements": explicit_diagnosis_gaps(relocated, requirements, original_acceptance['checks'], [], original_log=log1, original_reports=original_reports),
        "original_reports": original_reports,
        "component_sources": components, "module_declarations": modules, "hierarchical_instances": instances,
        "input_files": [str(p.relative_to(source)) for p in sorted(source.rglob("*")) if p.suffix in {".v", ".lib", ".ctl"}]}, ensure_ascii=False, separators=(',', ':'))
    diagnostic_attempts = []
    diagnostic_prompt = prompt
    diagnosis_schema = deepcopy(TASK2)
    diagnosis_schema['properties']['issues']['items']['properties']['original_source'] = {
        'type': 'string', 'enum': ['original.dofile'] + sorted(
            path.relative_to(source).as_posix() for path in (source / 'netlist').rglob('*.v'))}
    # STRING is shared by several fields in the base schema. Replace each field
    # rather than mutating it, or a task-line description leaks into root_cause,
    # phenomenon and fix_action and asks the model to return line IDs as prose.
    diagnosis_schema['properties']['issues']['items']['properties']['original_command'] = {
        'type': 'string', 'description': 'Prefer an original SOURCE line selector, e.g. L12 or L12-L14, '
        'using the shown source line numbers (not task lines). The runner binds the exact source text/location. '
        'Alternatively quote an exact UNIQUE source substring without line prefixes; for repeated text '
        'include the distinguishing scope and all intervening lines.'}
    diagnosis_schema['properties']['issues']['items']['properties']['violated_requirement'] = {
        'type': 'string', 'description': 'Task line selector only, e.g. L12 or L12-L14, not L12: followed by text. '
        'The runner extracts the exact quote. May be empty for command/DRC findings without a task citation.'}
    diagnosis_schema['properties']['issues']['items']['properties']['defective_parameter']['enum'] = (
        ['', 'arg1', 'arg2'] + sorted(set(re.findall(r'(?<![\w-])-[A-Za-z_]\w*', relocated + '\n' + guide))))
    followup_schema = deepcopy(REPAIR)
    followup_schema['properties']['additional_issues'] = diagnosis_schema['properties']['issues']
    followup_schema['properties']['issue_updates']['items']['properties']['diagnosis'] = diagnosis_schema['properties']['issues']['items']
    for attempt in range(2):
        shape_error, pruned_items = None, []
        try:
            plan, usage = generate(diagnostic_prompt, max(1, deadline - time.monotonic()), instruction,
                                   output_schema=diagnosis_schema, tool_guide=guide)
        except ModelResponseError as exc:
            plan, usage, shape_error = exc.plan, exc.usage, exc
            if isinstance(plan, dict):
                plan, stripped = strip_extra_fields(plan, diagnosis_schema)
                try:
                    validate(plan, diagnosis_schema)
                    shape_error = None
                except ValueError:
                    pass
            if shape_error is not None and isinstance(plan, dict) and deadline - time.monotonic() <= 75:
                # No time to regenerate: keep the valid items of this answer.
                try:
                    plan, pruned_items = prune_invalid_items(plan, diagnosis_schema)
                    shape_error = None
                except ValueError:
                    pass
        gaps = []
        candidate_error = f'Response schema: {shape_error}' if shape_error else None
        if shape_error is None:
            original_sources.update(diagnosis_source_texts(source, plan['issues']))
            sanitize_diagnoses(plan, relocated, spec, original_sources)
            gaps = explicit_diagnosis_gaps(relocated, requirements, original_acceptance['checks'], plan['issues'], original_log=log1, original_reports=original_reports)
            corrections = [item for item in plan['rejected_diagnoses'] if item['requires_correction']]
            if corrections:
                # Rejection is not proof that the alleged defect is absent.
                # Give non-signal findings the same bounded evidence correction;
                # otherwise an invalid citation can silently erase a real issue.
                candidate_error = 'Diagnosis evidence rejected: ' + '; '.join(
                    f"issues[{item['position'] - 1}]: {item['reason']}" for item in corrections)
            try:
                generated_script(source, target / 'runs/R2', order_full_flow(resolve_selectors(plan['dofile'], instances)))
                if plan.get('netlist_edits'):
                    edited_netlists(source, plan['netlist_edits'])
            except ValueError as exc:
                candidate_error = '\n'.join(filter(None, (candidate_error, str(exc))))
        diagnostic_attempts.append({'plan': plan, 'usage': usage, 'coverage_gaps': gaps,
                                    'candidate_error': candidate_error,
                                    **({'pruned_invalid_items': pruned_items} if pruned_items else {})})
        total_usage = {key: sum(a['usage'][key] for a in diagnostic_attempts)
                       if all(isinstance(a.get('usage'), dict) and isinstance(a['usage'].get(key), int) for a in diagnostic_attempts) else None
                       for key in ('input_tokens', 'output_tokens', 'total_tokens')}
        (target / 'model_plan.json').write_text(json.dumps({'plan': plan, 'usage': total_usage,
            'diagnostic_attempts': diagnostic_attempts}, ensure_ascii=False, indent=2), encoding='utf-8')
        if not gaps and candidate_error is None:
            break
        script_valid = (shape_error is None
                        and not (candidate_error or '').replace('Diagnosis evidence rejected: ', '', 1).count('\n')
                        and (candidate_error is None or candidate_error.startswith('Diagnosis evidence rejected: ')))
        if script_valid and (attempt or deadline - time.monotonic() <= 75):
            # No time for a correction plus R2 and its verification. The
            # script itself is valid; run it now. Rejected diagnoses stay
            # rejected and uncovered gaps stay recorded for verification.
            diagnostic_attempts[-1]['correction_skipped'] = ('correction already used' if attempt
                                                             else 'insufficient time before deadline')
            break
        if attempt or deadline-time.monotonic() <= 30:
            if shape_error is not None:
                raise shape_error
            raise ValueError(candidate_error or 'Diagnosis omitted an observed explicit task requirement')
        # One evidence-based correction before any repaired Scan run; the case
        # deadline is unchanged and the program never fabricates an issue.
        # Append feedback after the unchanged task fields; wrapping them in a
        # new object would invalidate the reusable prefix at the first key.
        diagnostic_prompt = json.dumps({**json.loads(prompt), 'previous_plan': plan,
            'diagnosis_coverage_gaps': gaps, 'candidate_error': candidate_error,
            'request': 'Return a complete revised diagnosis and script. Correct rejected citations using actual original sources and task line selectors; withdraw an allegation only if the evidence does not support it. Cover these literal original/task contradictions separately; do not merely fix them silently. The script is a BODY: omit all path setup from the original, use $input_dir for inputs and runner-provided $out_rpt_dir/$out_res_dir for outputs. Do not write into an old run.'}, ensure_ascii=False, separators=(',', ':'))
    def preserve_diagnoses(issues):
        for issue in issues:
            for field in ("phenomenon", "located_object", "root_cause", "violated_requirement", "fix_action"):
                issue[field] = resolve_selectors(issue[field], instances)
            name = issue.get('original_source', 'original.dofile')
            if name != 'original.dofile':
                evidence_path = target / 'original_sources' / name
                evidence_path.parent.mkdir(parents=True, exist_ok=True)
                evidence_path.write_text(original_sources[name], encoding='utf-8')
    preserve_diagnoses(plan["issues"])
    original_stages = stage_lines(relocated)
    if [command for _, command in original_stages] != list(STAGES):
        # Workflow is a documented invariant, not a model opinion. Keep one root cause.
        plan["issues"] = [issue for issue in plan["issues"] if not any(command in issue["original_command"] for command in STAGES)]
        plan["issues"].append({
            "phenomenon": "原始完整插链流程的阶段顺序不符合工具要求。",
            "located_object": "original.dofile 中 DRC、链预生成与插入语句",
            "root_cause": "原始顺序为 " + " → ".join(command for _, command in original_stages) + "；工具要求 examine_scan_drc → examine_scan_chain → insert_dft_logic。",
            "violated_requirement": "", "category": "command", "original_source": "original.dofile",
            "program_evidence": "workflow_order",
            "original_command": relocated.splitlines()[original_stages[0][0]],
            "fix_action": "由程序按已核实的单次完整插链阶段顺序组织这三个命令。",
        })
    runs, changes, history = [first], [], []
    failed_attempts = []
    previous = relocated
    review_original = not plan["issues"] and not plan.get('netlist_edits')
    reuse_candidate, review_count = False, 0
    while True:
        if reuse_candidate:
            # Only the diagnosis changed. Keep the exact already executed
            # candidate and re-review its real evidence without another Scan.
            reuse_candidate = False
            review_count += 1
        elif review_original:
            # No diagnosed defect: verify the actual original artifacts, not a
            # needless model rewrite. Both program checks and verifier still run.
            run_id, run2, fixed, second, change_id = "R1", run1, relocated, first, ""
            review_original = False
        else:
            review_count = 0
            run_id = f"R{len(runs) + 1}"
            old_id = runs[-1]["tool_call_id"]
            run2 = target / "runs" / run_id
            body = order_full_flow(resolve_selectors(plan["dofile"], instances))
            edits = plan.get('netlist_edits', [])
            if edits and re.search(r'(?:不得|禁止|不允许|不能)(?:修改|改动|改)(?:输入|原始|pre[-_]?scan|门级)*网表', re.sub(r'\s+', '', spec).lower()):
                raise ValueError('This task prohibits netlist modification')
            fixed = generated_script(source, run2, repaired_script(body, run_id) if edits else body)
            change_id = f"F{len(changes) + 1}"
            changes.append(record_change(target, old_id, run_id, previous, fixed, change_id))
            if time.monotonic() >= deadline:
                raise TimeoutError("No remaining budget for repaired Scan execution")
            second = run_scan(fixed, run2, deadline - time.monotonic(), netlist_edits=edits, source=source)
            if edits:
                net_id = f'F{len(changes) + 1}'
                diff_name = f'diffs/pre_scan_netlist_orig_to_{run_id}.diff'
                (target / diff_name).write_bytes((run2 / 'equivalence/netlist.diff').read_bytes())
                changes.append({'change_id': net_id, 'type': 'netlist',
                    'path': f'runs/{run_id}/deliverables/pre_scan_{run_id}.v', 'diff_path': diff_name,
                    'lec_ref': f'runs/{run_id}/reports/lec_report.rpt'})
                change_id = [change_id, net_id]
            runs.append(second)
        # The next operation calls the model. Preserve completed tool work and
        # diffs even if that verification request fails or exhausts the deadline.
        decision = {
            'case_id': os.environ.get('CASE_ID', source.parent.name or source.name),
            'task': 'task2', 'final_run': None,
            'summary': f'候选 {run_id} 已执行，等待产物和诊断核验；尚未发布最终结果。',
            'issue_resolutions': history, 'file_changes': changes,
            'tool_runs': [{'tool_call_id': r['tool_call_id'], 'log_file': f'runs/{r["tool_call_id"]}/{r["tool_call_id"]}.log',
                           'exit_status': r['exit_status']} for r in runs],
        }
        (target / 'decision_log.json').write_text(json.dumps(decision, ensure_ascii=False, indent=2), encoding='utf-8')
        issues, verification, missing, feedback = verify_round(target, run2, fixed, second, plan, relocated, spec, guide, deadline, change_id, generate, requirements, uncovered, components, source=source,
            review_id=f'{run_id}_review{review_count}' if review_count else None)
        previous_issues = {item["issue_id"]: item for item in history}
        for current in issues:
            if current["issue_id"] in previous_issues:
                current["attempts"] = previous_issues[current["issue_id"]]["attempts"] + current["attempts"]
        history = issues
        success = second["exit_status"] == "completed" and not missing and all(value is True for value in feedback["independent_checks"].values()) and verification["all_requirements_met"] is True and all(i["attempts"][-1]["verify"]["resolved"] for i in issues)
        summary = verification['summary']
        if not success:
            failed = [name for name, passed in feedback['independent_checks'].items() if passed is not True]
            unresolved = [i['issue_id'] for i in issues if not i['attempts'][-1]['verify']['resolved']]
            summary = (f'未发布最终结果。工具状态：{second["exit_status"]}；'
                       f'未通过产物检查：{failed}；缺少输出：{missing}；'
                       f'未完成诊断核验：{unresolved}；'
                       f'模型判断最终任务满足：{verification["all_requirements_met"]}。')
        decision.update({
            "final_run": run_id if success else None,
            "summary": summary, "independent_checks": feedback["independent_checks"], "issue_resolutions": issues,
            "rejected_diagnoses": plan.get("rejected_diagnoses", []) + feedback["rejected_diagnoses"],
        })
        if success:
            # Final public IDs are contiguous; raw model positions remain in
            # model_plan/verification files and rejected diagnostic records.
            decision['issue_resolutions'] = [{**item, 'issue_id': f'I{number}'}
                for number, item in enumerate(issues, 1)]
            decision['rejected_diagnoses'] = [
                {**{key: value for key, value in item.items() if key != 'issue_id'},
                 **({'plan_issue_id': item['issue_id']} if 'issue_id' in item else {})}
                for item in decision['rejected_diagnoses']]
            publish_run(target, run2)
        (target / "decision_log.json").write_text(json.dumps(decision, ensure_ascii=False, indent=2), encoding="utf-8")
        if success or deadline - time.monotonic() <= 30:
            break
        # A last-run-only prompt made a real R2 -> R3 -> R4 repair oscillate
        # between the same two partition configurations. Retain prior scripts
        # with their measured outcomes, not just a model's latest hypothesis.
        failed_attempts.append({'run_ref': run_id, 'dofile': plan['dofile'],
            'independent_checks': feedback['independent_checks'],
            'verification_summary': verification['summary']})
        revised, retry_usage = generate(json.dumps({
            "task": spec, "task_contract": requirements,
            "task_spec_lines": '\n'.join(f'L{i}: {line}' for i, line in enumerate(spec.splitlines(), 1)),
            "original_working_dofile": numbered_original, "original_acceptance": original_acceptance,
            "original_reports": original_reports, "component_sources": components,
            "module_declarations": modules, "hierarchical_instances": instances,
            "previous_dofile": plan["dofile"],
            "existing_issues": plan["issues"],
            "execution": second, "missing_outputs": missing, "acceptance_feedback": feedback,
            "earlier_failed_attempts": failed_attempts[:-1],
        }, ensure_ascii=False, separators=(',', ':')), deadline - time.monotonic(),
            instruction + " For this follow-up return dofile, netlist_edits, repair_action, additional_issues and issue_updates. "
            "issue_updates is empty unless current evidence changes the root cause, location or "
            "specific repair action of an "
            "existing original finding. Each update has issue_number (1-based existing_issues index) "
            "and diagnosis (the complete corrected issue, with exact original-source/task citations). "
            "Revise an initial hypothesis when the successful repair needs a different actual cause; "
            "do not leave a disproved missing-setting allegation as the final root merely because "
            "the circuit was eventually repaired another way. This updates the SAME finding, not a "
            "new issue or a way to merge independent parameters. Never revise program_evidence findings. "
            "When fixing object A exposes a separate fault on object B, preserve A's still-valid "
            "original finding and add B via additional_issues. Do not replace an omitted declaration "
            "for one port with an omitted declaration for a different port just because both use "
            "the same -port option or produce the same DRC code. issue_updates is for a changed "
            "hypothesis about the SAME cause, not an overwrite of another real root. "
            "Each finding retains its own fix_action unless explicitly updated here. repair_action "
            "summarizes the whole round and must not replace every issue's specific fix. "
            "additional_issues is empty unless new evidence reveals a distinct defect in the ORIGINAL "
            "input that existing_issues does not cover. Cite its actual original command/source and "
            "task lines using the same diagnosis rules. A regression introduced by our candidate "
            "must be corrected but is not an additional original diagnosis. Do not duplicate a "
            "known cause or list its downstream symptoms as new defects. "
            "Correct the remaining acceptance failures using actual reports, including signal "
            "polarities and scannability. Do not ignore additional DRC rules or exclude cells "
            "to hide failures. Return the complete replacement body, without runner variable "
            "assignments. Use exactly one examine_scan_drc, examine_scan_chain and full "
            "insert_dft_logic; do not add preprocessing insert_dft_logic calls. "
            "Do not assume an earlier verifier resolved=true proves every setting is correct. "
            "Compare the original and candidate evidence: a candidate can introduce a new defect. "
            "Earlier failed attempts record already executed configurations and outcomes. Do not "
            "cycle back to the same ineffective change without new causal evidence. A correctly "
            "selected region can have zero scanned cells because its FF clocks failed DRC; adding "
            "partition selectors does not make those physical clock paths controllable. Resolve "
            "the reported clock/reset path before treating that missing chain as a membership defect. "
            "If the original clock/control setting already passed DRC and its cell report showed "
            "the required edge, undo an unsupported candidate clock change rather than preserving it. "
            "In particular inspect scan-enable inactive level against functional clock-gating "
            "bypass polarity when most FFs have clock DRC violations.", output_schema=followup_schema, tool_guide=guide)
        updates = deepcopy(revised.get('issue_updates', []))
        corrected = {'issues': [update['diagnosis'] for update in updates]}
        additional = {'issues': deepcopy(revised.get('additional_issues', []))}
        original_sources.update(diagnosis_source_texts(source, additional['issues'] + corrected['issues']))
        sanitize_diagnoses(corrected, relocated, spec, original_sources)
        indices = [update['issue_number'] for update in updates]
        update_errors = []
        if len(set(indices)) != len(indices) or any(type(index) is not int or not 1 <= index <= len(plan['issues']) for index in indices):
            update_errors.append('Each update must identify one distinct existing issue number')
        elif any(plan['issues'][index - 1].get('program_evidence') for index in indices):
            update_errors.append('Program-derived findings cannot be revised by a model')
        next_issues = deepcopy(plan['issues'])
        if not update_errors and not corrected['rejected_diagnoses']:
            for index, finding in zip(indices, corrected['issues']):
                next_issues[index - 1] = finding
        sanitize_diagnoses(additional, relocated, spec, original_sources)
        for issue in additional['issues']:
            if any(all(issue.get(key, '') == existing.get(key, '') for key in
                       ('original_source', 'original_command', 'root_cause')) and
                       diagnosis_location(issue, {'original.dofile': relocated, **original_sources})[1] ==
                       diagnosis_location(existing, {'original.dofile': relocated, **original_sources})[1]
                       for existing in next_issues):
                additional['rejected_diagnoses'].append({'reason': 'Additional diagnosis duplicates an existing root cause', 'issue': issue})
        same_candidate = (revised['dofile'].strip() == plan['dofile'].strip()
                          and revised.get('netlist_edits', []) == plan.get('netlist_edits', []))
        reuse_candidate = run_id != 'R1' and same_candidate and bool(updates or additional['issues'])
        repair_ref = f'{run_id}_review{review_count + 1}' if reuse_candidate else f'R{len(runs) + 1}'
        (target / f"model_repair_{repair_ref}.json").write_text(json.dumps({
            "plan": revised, "usage": retry_usage, "additional_diagnosis_validation": additional,
            "issue_update_validation": {'validated': corrected, 'errors': update_errors}},
            ensure_ascii=False, indent=2), encoding="utf-8")
        if additional['rejected_diagnoses'] or corrected['rejected_diagnoses'] or update_errors:
            # Rejected additions/revisions are discarded (and saved above); the
            # revised script is still a measured repair candidate worth running.
            rejected_issues = [item['issue'] for item in additional['rejected_diagnoses'] if 'issue' in item]
            additional['issues'] = [issue for issue in additional['issues'] if issue not in rejected_issues]
            if corrected['rejected_diagnoses'] or update_errors:
                updates, indices, corrected = [], [], {**corrected, 'issues': []}
        if same_candidate and not updates and not additional['issues']:
            # The model explicitly returned no change after a measured failure.
            # Keep that response and failure; another identical Scan is not a repair.
            decision['summary'] += ' 后续模型未提出脚本、网表或诊断变更，停止重复执行同一失败方案。'
            (target / 'decision_log.json').write_text(json.dumps(decision, ensure_ascii=False, indent=2), encoding='utf-8')
            break
        plan["dofile"] = revised["dofile"]
        plan['netlist_edits'] = revised.get('netlist_edits', [])
        preserve_diagnoses(corrected['issues'])
        for index, finding in zip(indices, corrected['issues']):
            plan['issues'][index - 1] = finding
        preserve_diagnoses(additional['issues'])
        plan['issues'].extend(additional['issues'])
        previous = fixed
    if not success:
        # Deadline or repair exhausted without model approval. A round that
        # passed every program check is delivered rather than nothing; the
        # verifier's disagreement is recorded, not hidden.
        decision_path = target / "decision_log.json"
        decision = json.loads(decision_path.read_text(encoding="utf-8"))
        from scan_agent import _publish_accepted_on_timeout
        unresolved = [i['issue_id'] for i in history if not i['attempts'][-1]['verify']['resolved']]
        if _publish_accepted_on_timeout(target, decision, decision_path):
            decision.update(model_verification="disputed", model_unresolved_issues=unresolved,
                            summary=f"{decision['final_run']} 通过全部程序验收；模型核验未认可（未解决：{unresolved}）。非完整语义证明。")
            decision_path.write_text(json.dumps(decision, ensure_ascii=False, indent=2), encoding="utf-8")
            success = True
    print(json.dumps({"repair_completed": bool(success), "issues": len(history), "rounds": len(runs), "elapsed": round(time.monotonic() - started, 2)}))
    return 0 if success else 1


def verify_round(target, run2, fixed, second, plan, relocated, spec, guide, deadline, change_id, generate, requirements=None, uncovered=(), component_sources=None, source=None, review_id=None):
    run_id = run2.name
    verification_path = target / f'verification_{review_id or run_id}.json'
    log_name = f"{run_id}.log"
    acceptance, artifacts, workflow_check = inspect_run(run2, fixed, second, full_insertion=True, task_spec=spec, task_requirements=requirements, uncovered=uncovered, source=source)
    # Existing deterministic checks cover every report row, even when the model
    # view of a large report must be sampled. QA Q24 permits artifact evidence.
    artifacts["acceptance.json"] = (run2 / "acceptance.json").read_text(encoding="utf-8")
    artifacts[f"deliverables/{run_id}.dofile"] = fixed
    missing = acceptance["missing_outputs"]
    independent_checks = acceptance["checks"]
    # Keep command/status messages, not thousands of bare instance-name rows.
    # Full logs remain untouched and references retain original line numbers.
    views, evidence = {}, {}
    for name, text in sorted(artifacts.items()):
        lines = []
        limit = 40 if re.search(r'CellNo\s+InstanceName', text[:1000]) else 160
        for index, line in enumerate(text.splitlines(), 1):
            shown = (bool(re.match(r"\[(?:INFO|WARNING|ERROR)\]", line)) if name == log_name else index <= limit)
            if shown and line.strip() and len(line) < 1000:
                evidence_id = f'E{len(evidence) + 1}'
                evidence[evidence_id] = {'source': name, 'line_start': index, 'line_end': index,
                                         'line_start_text': line}
                lines.append(f'{evidence_id}: {line}')
        views[name] = '\n'.join(lines)
    netlist_ports = {}
    for p in (run2 / "deliverables").glob("*.v"):
        with p.open(encoding="utf-8") as stream:
            netlist_ports[p.name] = [line.strip() for line in stream if re.match(r"\s*(?:module|input|output)\s", line)][:200]
    original_log = (target / "runs/R1/R1.log").read_text(encoding="utf-8", errors="replace")
    original_evidence = {"original_dofile": relocated, "original_run_log": "\n".join(
        line for line in original_log.splitlines() if re.match(r"\[(?:INFO|WARNING|ERROR)\]", line))[:22000]}
    original_check = target / "runs/R1/acceptance.json"
    if original_check.is_file():
        original_evidence["independent_acceptance"] = json.loads(original_check.read_text(encoding="utf-8"))
    original_evidence['reports'] = {}
    for path in sorted((target / 'runs/R1/reports').glob('*.rpt')):
        with path.open(encoding='utf-8', errors='replace') as stream:
            excerpt = stream.read(12000)
            original_evidence['reports'][path.name] = excerpt[:4000] if re.search(r'CellNo\s+InstanceName', excerpt[:1000]) else excerpt
    original_evidence['cited_netlist_sources'] = [
        {'source': issue['original_source'], 'exact_excerpt': issue['original_command'],
         'scope': 'Excerpt validated against original input; surrounding control context is supplied separately'}
        for issue in plan['issues'] if issue.get('original_source', 'original.dofile') != 'original.dofile']
    numbered_issues = [{**issue, 'issue_number': number} for number, issue in enumerate(plan['issues'], 1)]
    # Diffs and the pending decision record live above runs/Rn. Their absence
    # from the tool-artifact view once caused an otherwise correct run to be
    # rejected, so show the actual existing bookkeeping in its separate scope.
    runner_evidence = {}
    for path in [target / 'decision_log.json', *sorted((target / 'diffs').glob('*.diff'))]:
        if path.is_file():
            with path.open(encoding='utf-8') as stream:
                text = stream.read(12001)
            runner_evidence[path.relative_to(target).as_posix()] = {'text': text[:12000], 'truncated': len(text) > 12000}
    verification_prompt = json.dumps({"task": spec, "component_sources": component_sources, "original_evidence": original_evidence, "issues": numbered_issues, "execution": second, "independent_acceptance": acceptance, "independent_workflow_check": workflow_check, "missing_outputs": missing, "output_netlist_port_declarations": netlist_ports, "actual_files": views, "candidate_sources": list(views), "runner_evidence": runner_evidence}, ensure_ascii=False, separators=(',', ':'))
    verification_instruction = (
        "Verify Scan repair from actual evidence. Return JSON with all_requirements_met boolean, "
        "You are checking a candidate run BEFORE publication. The actual candidate script "
        "is in actual_files under deliverables/Rn.dofile. Once this verification succeeds, "
        "the runner copies that exact script to final_results/deliverables/final.dofile "
        "and writes the root decision_log.json from the actual run history and your evidence. "
        "Those two runner-owned publication files are deliberately absent from this candidate "
        "view; do NOT reject the candidate for their absence and do NOT request Tcl to "
        "generate them. Judge the tool artifacts and the supplied candidate dofile now. "
        "runner_evidence shows actual existing decision/diff files at the output root, outside "
        "the per-run tool directory. Do not mistake absence from actual_files for missing diff "
        "history. The root decision record is pending YOUR current verification; the proposed "
        "numbered diagnoses and actual run/change records are supplied now and finalized by "
        "the runner after your review. This is not a missing Tcl output or a new source for "
        "repair citations: those still use candidate_sources only. "
        "summary string, issues object keyed I1, I2, ... with one record per input issue_number. Copy each issue_number "
        "exactly; bind its reasoning, parameter and evidence to THAT finding, not the adjacent finding. "
        "Do not skip a finding and shift later verdicts into its position. Each record contains resolved boolean, "
        "original_defect_supported boolean and support_basis string. original_defect_supported "
        "means the claimed original CAUSAL defect is proved by original_evidence and this task specification; "
        "original_source/original_command is a location anchor in the original script or cited_netlist_sources. "
        "For netlist changes, require the actual original_to_used_prescan_equivalence check and Scan reports. "
        "Command/DRC defects may have empty violated_requirement; only configuration defects require a task quote. "
        "For an OMITTED required "
        "configuration, that anchor may itself be a legal command: inspect the full original script "
        "to establish the omission and use the original log where available. Static omission evidence "
        "does not require an explicit runtime error naming the missing command. original_evidence "
        "is deliberately separate from repaired actual_files; its absence from actual_files is NOT "
        "a reason to reject original support. Do not label the legal anchor command itself illegal. "
        "duplicate_of is 0 for an independent root, otherwise the 1-based input index of an "
        "EARLIER supported independent issue for the SAME root cause. A wrong object selector "
        "and the absence of the intended object fixed by that substitution are one cause, "
        "even though both descriptions are factually true. Mark the second duplicate_of the first. "
        "Independent option/value defects on the same command are not duplicates. "
        "Do not mark an issue supported only because the repaired run succeeds or now explicitly "
        "spells an option that was previously omitted. "
        "violated_requirement is an exact task quote, but a quote requiring an outcome does not "
        "make the model's preferred implementation mandatory. Check whether the ORIGINAL reports "
        "and settings already met it. Never invent an additional task condition to support a finding. "
        "An unrequested optional wrapper is not automatically forbidden. An excessive total chain "
        "count alone does not prove wrapper enable is an independent second defect; require an "
        "explicit prohibition or evidence that keeping it prevents any compliant channel configuration. "
        "A positive-edge FF does not by itself require clock off_state=0. A DFTR1 count alone "
        "does not prove a clock-polarity defect when a missing internal-clock association or "
        "another control explains it. Require a task-specified inactive level or causal evidence "
        "isolating that setting; changing it together with other repairs is not such evidence. "
        "A true original_evidence.independent_acceptance.checks.task_config_X means the ORIGINAL "
        "effective X already satisfied that checked requirement; original_evidence.reports shows "
        "the actual values, including defaults. A missing explicit option is not a defect when "
        "its effective original value was already correct. In particular, scan_enable may be "
        "automatically created without a declaration: require an explicit task port/usage "
        "obligation or actual blocked-control evidence to support an omission finding. "
        "Check every clause of a root_cause: "
        "a supported clause cannot justify another unsupported claim in the same issue. If a "
        "claim mixes the two, original_defect_supported must be false and support_basis must "
        "identify which part is unsupported. "
        "Check phenomenon and fix_action against the actual numeric values too. If an original "
        "root is real but its narrative reverses active/inactive polarity or calls original "
        "evidence the final repaired state, or misattributes a DRC count, set original_defect_supported=true "
        "but resolved=false "
        "and explain the required wording correction for a later issue_updates revision. "
        "Do not discard a real root merely because its narrative is wrong. A DRC rule total "
        "or 'other cells' count may combine several independently controlled groups; it is "
        "not a per-port count. This check applies to EVERY finding, even if a stated number "
        "happens to match a real rule total. "
        "Likewise task_wrapper_port_P=true with its check_details means every reported bit of P "
        "already had the required wrapper style/type in the ORIGINAL run. The sampled detail "
        "rows retain evidence even when a large original report excerpt cuts off before that port. "
        "A global/default wrapper style does not need another port-specific override to be valid. "
        "A literal scan-segment list is legal. An incomplete-registration allegation requires "
        "an actual required endpoint pair missing from the full original script, not merely "
        "an empty/partial identification report caused by other original DRC failures. "
        "Do not require runtime discovery in place of a correct explicit list. "
        "Review the input findings together for duplicated causality. An original structural "
        "or control defect and the non-scannability/output loss it causes are ONE root cause. "
        "Reject a separate downstream-symptom claim unless it identifies its own independently "
        "wrong command, option or connection. A legal mapping declaration is not itself faulty "
        "merely because clock/reset DRC prevented scan replacement; repairing that DRC is "
        "evidence for the control defect, not a second mapping defect. "
        "Reject path-variable renaming and legal aliases as defects. Distinguish actual original "
        "defects from optional explicit spellings of defaults. "
        "Default_Partition is predefined; no add_scan_partition is needed or legal for it. "
        "An empty original partition report following failed scannability does not prove the "
        "default partition is missing. A diagnosis requiring its creation is unsupported, "
        "even if other repairs subsequently succeed. "
        "A default-only design can have a header-only rpt_scan_partition even AFTER successful "
        "insertion; check actual chain partition membership. Report emptiness does not prove "
        "it was exported before insertion. Verify any ordering allegation against the original "
        "dofile and actual command log, not the table's appearance. "
        "The overall all_requirements_met judgment includes the final artifacts AND a complete, "
        "independently supported diagnosis. Check the full original/task evidence for definite "
        "original causes omitted from the list, and for multiple independent wrong parameters "
        "merged into one finding. If either remains, set all_requirements_met=false and identify "
        "the exact original evidence and required separate finding in summary; a later diagnosis-only "
        "revision can reuse these same tool artifacts. Do not demand hypothetical faults or infer "
        "an omission merely from optional candidate changes. Unsupported proposed findings can be "
        "rejected without blocking an otherwise complete task; they are not evidence of missing roots. "
        "When the task does not require DRC zero, do not impose it: judge remaining warnings by "
        "their effect on requested coverage and configuration, not their mere existence. "
        "For a supported, resolved independent finding, copy evidence_id from one of the "
        "E-prefixed lines in actual_files that proves its repair. This single ID binds source, "
        "actual file line and raw text together; the runner expands it without guessing or "
        "relocating a citation. Do not generate those separate fields or quote text. "
        "For example E251: [INFO] [CMD-0034] @19: insert_dft_logic is evidence_id=E251, "
        "never 19, @19 or a file line number. Use an empty evidence_id only for unresolved, "
        "unsupported or duplicate findings. A real ID only proves that a line exists: you must "
        "still judge its causal relevance to this finding and whether the repair is complete. "
        "Do not invent evidence. Do not treat clean exit as proof of chain count, coverage or DRC. "
        "A new scan_enable port is intentionally absent before insertion; SCAN-4610 alone is not a failure. "
        "Runner path variable assignments are expected, not defects. Large reports in actual_files "
        "are sampled, but independent_acceptance checks every parsed chain row. For exhaustive "
        "chain-count, chain-length and coverage checks, a true corresponding check in actual_files "
        "acceptance.json is valid program-derived evidence; select that check's E ID. Do not "
        "demand all report rows be re-sent when that complete check passed. Otherwise a chain-count "
        "reference must show all chain rows or an explicit total, not a single row. Prefer DRC summary and final "
        "chain reports for behavioral verification over an echoed configuration command. "
        "If evidence is insufficient mark unresolved."
        " Resolve each named cause against its OWN actual candidate evidence. Remaining instances "
        "of the same DRC rule on a different port/domain do not automatically make a corrected "
        "named cause unresolved. For example, a validly released reset A can be repaired while "
        "reset B still needs a separate declaration; retain the finding about A and describe the "
        "remaining B failure in the overall summary for follow-up, rather than moving A's identity. "
        ' Each issue MUST include all these keys: {"issue_number":1,"resolved":false, '
        '"original_defect_supported":true,"support_basis":"reason about original defect",'
        '"duplicate_of":0,"evidence_id":""}. '
        "Do not put file/line references only inside support_basis; the separate reference keys "
        "are mandatory. Auxiliary evidence exports requested by the runner are not task defects. "
        "Permission to ignore a DRC rule is not an obligation to ignore it. "
        "Never omit an unsupported issue: return its record with original_defect_supported=false. "
        "The issues object MUST contain exactly the required I-number keys from the response schema. "
        "original_evidence explains why the original was wrong; it is NEVER a valid source for "
        "the repaired-result citation. evidence_id must come from the current actual_files."
    )
    def valid_duplicate(index, check, verification):
        duplicate = check.get('duplicate_of', 0)
        if not (type(duplicate) is int and 1 <= duplicate < index):
            return False
        current_parameter = plan['issues'][index - 1].get('defective_parameter')
        earlier_parameter = plan['issues'][duplicate - 1].get('defective_parameter')
        if current_parameter and earlier_parameter and current_parameter != earlier_parameter:
            return False
        earlier = verification['issues'][duplicate - 1]
        return earlier['original_defect_supported'] is True and earlier.get('duplicate_of', 0) == 0

    verification_attempts = []
    for attempt in range(2):
        verification, verify_usage = generate(verification_prompt, max(1, deadline - time.monotonic()),
            verification_instruction, output_schema=verification_schema(plan['issues']), tool_guide=guide)
        raw_verification = verification
        invalid_references = []
        expected_keys = [f'I{number}' for number in range(1, len(plan['issues']) + 1)]
        if not isinstance(verification['issues'], dict) or set(verification['issues']) != set(expected_keys):
            raise ValueError('Verification issue identities are missing, duplicated or invalid')
        # Resolve only the exact model-selected ID from this immutable run's
        # displayed evidence. There is no fuzzy text matching or citation repair.
        verification = {**verification, 'issues': [
            {**verification['issues'][key], **evidence.get(verification['issues'][key].get('evidence_id'),
                {'source': '', 'line_start': 0, 'line_end': 0, 'line_start_text': ''})}
            for key in expected_keys]}
        identities = [check.get('issue_number') for check in verification['issues']]
        valid_identities = (all(type(number) is int for number in identities)
                            and identities == list(range(1, len(plan['issues']) + 1)))
        if valid_identities:
            verification = {**verification, 'issues': sorted(verification['issues'], key=lambda check: check['issue_number'])}
        else:
            invalid_references.append({'reason': 'Each original issue_number must occur exactly once; never infer identity from array position',
                                       'received_issue_numbers': identities})
        for index, check in enumerate(verification['issues'], 1):
            if not valid_identities:
                break
            duplicate = check.get('duplicate_of', 0)
            if duplicate:
                if not valid_duplicate(index, check, verification):
                    invalid_references.append({'issue': index, 'duplicate_of': duplicate,
                        'reason': 'Duplicate target must be an earlier supported independent finding; defects on different named parameters are independent, not duplicates'})
                continue
            if not check['resolved'] or not check['original_defect_supported']:
                continue
            if check.get('evidence_id') not in evidence:
                invalid_references.append({'issue': index, 'evidence_id': check.get('evidence_id'),
                    'reason': 'Select an exact E-prefixed ID from this candidate actual_files, not a command number or an original-source citation'})
        rejected_numbers = [check['issue_number'] for check in verification['issues']
                            if not check['original_defect_supported'] or check.get('duplicate_of')]
        verdict_conflict = (not invalid_references and verification['all_requirements_met'] is False
                            and second['exit_status'] == 'completed' and not missing
                            and bool(independent_checks) and all(value is True for value in independent_checks.values())
                            and bool(rejected_numbers) and all(
                                not check['original_defect_supported'] or check.get('duplicate_of') or check['resolved']
                                for check in verification['issues']))
        verification_attempts.append({'verification': raw_verification, 'usage': verify_usage,
                                      'selected_evidence': {check['evidence_id']: evidence[check['evidence_id']]
                                          for check in verification['issues'] if check.get('evidence_id') in evidence},
                                      'invalid_references': invalid_references, 'verdict_conflict': verdict_conflict})
        record = json.dumps({'verification': verification, 'independent_checks': independent_checks,
                             'usage': verify_usage, 'attempts': verification_attempts}, ensure_ascii=False, indent=2)
        verification_path.write_text(record, encoding='utf-8')
        if not (invalid_references or verdict_conflict) or attempt or deadline-time.monotonic() <= 30:
            break
        # Correct references or a rejected-hypothesis/overall-verdict conflict
        # against this same immutable run. Never override a negative judgment.
        verification_prompt = json.dumps({**json.loads(verification_prompt),
            'previous_verification': raw_verification, 'invalid_references': invalid_references,
            'rejected_findings_are_automatically_omitted': rejected_numbers,
            'overall_judgment_feedback': (
                'All independent artifact checks and all supported findings passed. The runner automatically '
                'omits unsupported/duplicate findings from the final diagnosis and retains their rejection records. '
                'Their removal needs no script change or new Scan. Re-evaluate all_requirements_met for that '
                'filtered diagnosis. Keep false if a real unmet task requirement, missing original root or '
                'merged independent cause remains, and identify that exact evidence in summary.' if verdict_conflict else ''),
            'request': 'Correct invalid issue identities, repaired-result citations and duplicate_of references. Return exactly one record for each displayed issue_number. Preserve the corresponding original root and defective_parameter; do not shift later findings into an earlier number. Duplicates must target an earlier supported independent finding. original_evidence cannot prove a repair. Return the full verification object.'}, ensure_ascii=False, separators=(',', ':'))
    (target / "verification.json").write_text(record, encoding="utf-8")
    verification_path.write_text(record, encoding="utf-8")
    if not valid_identities:
        raise ValueError('Verification issue identities are missing, duplicated or invalid')
    issues, rejected = [], []
    if len(verification["issues"]) != len(plan["issues"]):
        raise ValueError("Missing issue verification records")
    for index, (issue, check) in enumerate(zip(plan["issues"], verification["issues"]), 1):
        if issue.get('program_evidence') == 'workflow_order':
            # The original single-flow order is a directly checked tool rule,
            # even when an earlier missing input masks its runtime symptoms.
            original_order = [command for _, command in stage_lines(relocated)]
            observed = workflow_check['observed']
            check = {**check, 'original_defect_supported': original_order != list(STAGES),
                     'duplicate_of': 0,
                     'resolved': workflow_check['passed'], 'source': workflow_check['source'],
                     'line_start': observed[0]['line'] if observed else 1,
                     'line_end': observed[-1]['line'] if observed else 1,
                     'line_start_text': artifacts[workflow_check['source']].splitlines()[observed[0]['line'] - 1] if observed else '',
                     'support_basis': 'Program-derived comparison of original stage order with documented DRC/preview/insertion order; repair proven by actual candidate command log, not by model approval.'}
        duplicate = check.get('duplicate_of', 0)
        if duplicate:
            if valid_duplicate(index, check, verification):
                rejected.append({'issue_id': f'I{index}', 'run_ref': run_id,
                                 'duplicate_of_plan_issue': f'I{duplicate}',
                                 'reason': check.get('support_basis', 'Same cause as an earlier supported finding'), 'issue': issue})
                continue
            check = {**check, 'resolved': False}
        if check.get("original_defect_supported") is not True:
            rejected.append({"issue_id": f"I{index}", "run_ref": run_id,
                             "reason": check.get("support_basis", "Original defect not supported by verifier"), "issue": issue})
            continue
        command = issue["original_command"]
        original_source = issue.get('original_source', 'original.dofile')
        original_text = relocated if original_source == 'original.dofile' else (target / 'original_sources' / original_source).read_text(encoding='utf-8')
        _, found_line, found_end, location = diagnosis_location(issue, {original_source: original_text})
        found_source = 'runs/R1/deliverables/R1.dofile' if original_source == 'original.dofile' else 'original_sources/' + original_source
        evidence_source = check.get("source", "")
        actual = artifacts.get(evidence_source, "")
        actual_lines = actual.splitlines()
        line, end_line = check.get("line_start"), check.get("line_end")
        valid_range = type(line) is int and type(end_line) is int and 1 <= line <= end_line <= len(actual_lines)
        quoted = check.get('line_start_text', '')
        valid_quote = (valid_range and isinstance(quoted, str) and bool(quoted.strip())
                       and quoted.strip() == actual_lines[line - 1].strip())
        supported = check.get("original_defect_supported") is True
        resolved = check["resolved"] and supported and valid_quote and second["exit_status"] == "completed"
        excerpt = "\n".join(actual_lines[line - 1:end_line]) if resolved else ""
        issues.append({
            "issue_id": f"I{index}", "phenomenon": issue["phenomenon"],
            "found": {"run_ref": "R1", "source": found_source, "locator": f"L{found_line}-L{found_end}", "excerpt": command},
            "diagnosis": {"summary": "核对原始来源、任务要求和真实运行结果；源代码引用为静态证据。", "located_object": location, "root_cause": issue["root_cause"], "violated_requirement": issue["violated_requirement"]},
            "attempts": [{"fix": {"action": issue["fix_action"], "artifact_ref": change_id if isinstance(change_id, list) else [change_id]}, "verify": {"run_ref": run_id, "source": f"runs/{run_id}/" + evidence_source if evidence_source else "", "resolved": bool(resolved), "original_defect_supported": supported, "support_basis": check.get("support_basis", ""), "locator": f"L{line}-L{end_line}" if resolved else "", "excerpt": excerpt}}],
        })
    return issues, verification, missing, {"verification": verification, "independent_checks": independent_checks, "actual_files": views, "runner_evidence": runner_evidence, "rejected_diagnoses": rejected}

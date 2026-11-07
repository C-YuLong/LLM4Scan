"""Deterministic round checks; no model calls, case IDs or reference solutions."""

import json
import re
import time
from pathlib import Path

from scan_runner import declared_artifacts
from model_contract import TASK_CONSTRAINTS, ModelResponseError


STAGES = ("examine_scan_drc", "examine_scan_chain", "insert_dft_logic")


_MEMBERSHIP_NEGATIVE = re.compile(r'不(?:允许|能|得|应|要|需|可)?(?:参与|进入|加入|接入|串入|串链)|排除|禁止(?:参与|串)|不可扫描|non-?scan', re.I)
_MEMBERSHIP_POSITIVE = re.compile(r'(?<!不)(?<!不允许)(?<!不能)(?<!不得)(?<!不应)(?:参与|进入|加入|串入)(?:扫描链|串链)?')


def membership_polarity(quote, target):
    """Deterministic include/exclude implied by one cited sentence, or None.

    Only decides clear forms: a sentence-level negative/positive membership
    phrase, optionally with one exception clause (除了 X ... 其余 / X 除外).
    Objects named inside the exception get the opposite polarity.  Anything
    else is left to the model extraction rather than guessed here.
    """
    sentences = [s for s in re.split(r'[。；;\n]', quote) if target.rstrip('*') and target.rstrip('*') in s]
    if len(sentences) != 1:
        return None
    sentence = sentences[0]
    exception = (re.search(r'除(?!外)(?:了)?(?P<body>.+?)(?:以外|之外|外)?[，,]\s*(?P<rest>(?:其余|其他|其它|剩余).*)', sentence)
                 or re.search(r'除(?!外)(?:了)?(?P<body>.+?)(?:以外|之外)(?P<rest>.*)', sentence))
    if exception:
        body, rest = exception['body'], exception['rest']
    else:
        trailing = re.search(r'(?P<body>`?[\w/\[\]*$]+`?(?:\s*(?:下|内)的?)?)\s*除外', sentence)
        body, rest = (trailing['body'], sentence[trailing.end():]) if trailing else ('', sentence)
    negative, positive = bool(_MEMBERSHIP_NEGATIVE.search(rest)), bool(_MEMBERSHIP_POSITIVE.search(_MEMBERSHIP_NEGATIVE.sub('', rest)))
    if negative == positive:
        return None
    main = 'exclude' if negative else 'include'
    if not body:
        return main
    name = target.rstrip('*')
    # The exception names the more specific selector; an enclosing hierarchy
    # that also appears in it as a prefix belongs to the main clause.
    in_body = re.search(re.escape(name) + r'(?![\w/])', body) is not None
    if in_body and not (exception and re.search(re.escape(name) + r'(?![\w/])', sentence[:exception.start()])):
        return 'exclude' if main == 'include' else 'include'
    return main


def merge_task_constraints(spec, extracted, *, design_context=()):
    """Supplement known report checks, never replace task-explicit constraints.

    Exact quotes establish provenance, not semantic correctness of model extraction.
    Unsupported requirements remain visible; this is not complete prose validation.
    """
    req = _task_requirements(spec)
    hierarchy = {item['selector']: item for item in design_context}
    def agree(previous, value):
        if previous is not None and previous != value:
            raise ValueError(f"Extracted value {value!r} conflicts with explicit task constraint {previous!r}; "
                             "keep the already_parsed_requirements value and omit this redundant extraction")
        return value
    def merge(item):
        quote, kind, target, value = (item[k] for k in ("quote", "kind", "target", "value"))
        if not quote.strip() or quote not in spec:
            raise ValueError("Constraint must cite exact task text")
        if kind in ("chain_count", "max_chain_count", "max_cells_per_chain"):
            if target:
                raise ValueError("Numeric extraction requires empty target; partition and wrapper/unwrapper scopes are parsed separately")
            if not re.fullmatch(r"[1-9]\d*", value):
                raise ValueError("Chain constraints require positive integers")
            if kind == "chain_count" and req["partitions"]:
                if int(value) != sum(p["chain_count"] for p in req["partitions"]):
                    raise ValueError("Total chain count conflicts with partitions")
            field = {"max_chain_count": "max_count", "max_cells_per_chain": "max_length"}.get(kind, kind)
            req[field] = agree(req[field], int(value))
        elif kind == "chain_partition":
            if target or not value or re.search(r'\s', value):
                raise ValueError('A global chain partition requires empty target and one partition name')
            if req['partitions'] and any(p['name'] != value for p in req['partitions']):
                raise ValueError('Global partition conflicts with explicit per-partition requirements')
            req['chain_partition'] = agree(req.get('chain_partition'), value)
        elif kind == "drc_zero":
            if value != "true":
                raise ValueError("Extraction cannot relax DRC constraints")
            req["drc_zero"] = True
        elif kind == "config":
            choices = {"mix_clocks": {"True", "False"}, "mix_edges": {"True", "False"},
                       "add_lockup": {"True", "False"}, "respect_sff_se_connection": {"True", "False"},
                       "internal_clocks": {"none", "single", "multi"}}
            if target not in choices or value not in choices[target]:
                raise ValueError("Unsupported scan configuration constraint")
            agreed = agree(req["config"].get(target), value)
            if target == 'internal_clocks' and not re.search(r'internal_clocks?|内部时钟', quote, re.I):
                raise ValueError('Multiple source clocks do not specify an internal_clocks policy; cite an explicit internal-clock requirement or leave it uncovered')
            req["config"][target] = agreed
        elif kind == 'scan_enable_usage':
            if not target or value not in {'all', 'scan', 'clock_gating'}:
                raise ValueError('Scan enable usage requires one port and all/scan/clock_gating')
            existing = next((s for s in req['signals'] if s['port'] == target), None)
            if existing is None:
                existing = {'port': target, 'role': 'scan_enable'}
                req['signals'].append(existing)
            if existing['role'] != 'scan_enable':
                raise ValueError('Scan enable usage conflicts with the signal role')
            existing['usage'] = agree(existing.get('usage'), value)
        elif kind in ("clock", "reset", "scan_enable", "constant", "wrapper_clock",
                      "wrp_in_shift_en", "wrp_out_shift_en", "wrp_in_capture_en", "wrp_out_capture_en"):
            if kind == 'reset' and re.search(r'(?:无需|无须|不需要|不必)[^。；\n]*(?:reset|复位)[^。；\n]*(?:扫描信号|扫描配置)', quote, re.I):
                raise ValueError('The cited text says scan reset configuration is not required; do not make it mandatory')
            role_words = {"clock": r"clock|时钟", "reset": r"reset|复位", "scan_enable": r"scan_enable|扫描使能|shift"}.get(kind)
            if role_words and any(target in sentence and re.search(r"(?:不作为|不是|无需|不需要|没有|不需|并非)[^。；;]*(?:" + role_words + ")", sentence, re.I)
                                  for sentence in re.split(r"[。；;\n]", quote)):
                raise ValueError(f"The cited sentence negates the {kind} role for {target!r}; do not extract a negated requirement")
            if kind != "constant":
                polarities = {"": "", "active_high": "0", "active_low": "1", "inactive_0": "0", "inactive_1": "1"}
                if value not in polarities:
                    raise ValueError("Signal value must distinguish active polarity from inactive level")
                value = polarities[value]
            if not target or value not in {"", "0", "1"} or (kind == "constant" and not value):
                raise ValueError("Invalid signal constraint")
            field = "constant_value" if kind == "constant" else "off_state"
            if any(s["port"] == target and s["role"] != kind for s in req["signals"]):
                raise ValueError("Extracted signal role conflicts with another explicit role for this port")
            existing = next((s for s in req["signals"] if s["port"] == target and s["role"] == kind), None)
            if existing is None:
                existing = {"port": target, "role": kind}
                req["signals"].append(existing)
            if value:
                existing[field] = agree(existing.get(field), value)
        elif kind == 'output_file':
            if target or not re.fullmatch(r'[^/\\\s]+\.(?:v|rpt|ctl|def)', value) or value not in quote:
                raise ValueError('output_file covers only one literal task-specified .v/.rpt/.ctl/.def basename with empty target; dofile, logs, decision JSON and wildcard patterns are runner-managed deliverables, not output_file constraints')
            if value not in req['outputs']:
                req['outputs'].append(value)
        elif kind == 'cell_mapping':
            if any(not re.fullmatch(r'[A-Za-z_$][\w$]*', name) or name not in quote for name in (target, value)):
                raise ValueError('Cell mapping requires the literal original and replacement cell names from the task; suffix abbreviations may remain literal')
            req['cell_mappings'][target] = agree(req['cell_mappings'].get(target), value)
        elif kind == 'scan_membership':
            if value not in {'include', 'exclude'} or not target or re.search(r'\s', target):
                raise ValueError('Scan membership requires a literal task selector and include/exclude')
            selected = hierarchy.get(target)
            # Explicit same-prefix numeric ranges name their interior objects
            # without spelling each one. Keep exact finite members, not a glob.
            in_range = False
            for interval in re.finditer(r'(?<![\w/$])(?P<prefix>[A-Za-z_$][\w/$]*?)(?P<start>0|[1-9]\d*)`?\s*(?:到|至)\s*`?(?P=prefix)(?P<end>0|[1-9]\d*)(?![\w/$])', quote):
                member = re.fullmatch(re.escape(interval['prefix']) + r'(0|[1-9]\d*)', target)
                if member and int(interval['start']) <= int(member[1]) <= int(interval['end']):
                    in_range = True
                    break
            if target not in quote and selected is None and not in_range:
                raise ValueError(f'Selector {target!r} is absent from the cited lines. Extend line_start/line_end to include BOTH its name/declaration and the membership requirement, including preceding lines when the requirement says this latch/hierarchy')
            if selected is None:
                expected = membership_polarity(quote, target)
                if expected is not None and expected != value:
                    raise ValueError(f'Scan membership polarity for {target!r} contradicts the cited sentence: it implies {expected}, '
                                     f'not {value}. In "A 下的所有单元，除了 B，其余均不允许参与" B is include and A is exclude; '
                                     'an exception clause takes the opposite of the main rule')
            if selected is not None:
                target = selected['path']
            existing = next((rule for rule in req['scan_membership'] if rule['target'] == target), None)
            if existing:
                agree(existing['value'], value)
            else:
                req['scan_membership'].append({'target': target, 'value': value,
                    **({'task_quote': quote, 'source_selector': selected['selector'], 'module_type': selected['type']} if selected else {})})
        elif kind == "wrapper_port":
            styles = {"dedicated": ("dedicated", None), "custom": ("dedicated", "user_defined"),
                      "shared": ("shared", None), "none": ("none", None)}
            if not target or value not in styles:
                raise ValueError("wrapper_port requires one named port and dedicated/custom/shared/none; generic all-port requirements belong in uncovered")
            if target.lower() in styles or target.lower() in ("wrapper", "all", "ports"):
                raise ValueError("wrapper_port target must be an actual port name, not the wrapper style; a style-only wrapper request belongs in uncovered")
            style, cell_type = styles[value]
            existing = next((s for s in req["wrapper_ports"] if s["port"] == target), None)
            if existing:
                agree(existing["style"], style)
                # "dedicated" specifies style, not an override of the task's
                # already parsed custom-cell requirement. Keep the stronger fact.
                if cell_type is not None:
                    existing["cell_type"] = agree(existing["cell_type"], cell_type)
            else:
                req["wrapper_ports"].append({"port": target, "style": style, "cell_type": cell_type})
        else:
            raise ValueError("Unsupported constraint kind")
    errors = []
    for index, item in enumerate(extracted["constraints"]):
        try:
            merge(item)
        except ValueError as exc:
            errors.append(f"constraints[{index}] ({item['kind']}, {item['target']!r}, {item['value']!r}): {exc}")
    if any(not q.strip() or q not in spec for q in extracted["uncovered"]):
        errors.append("Uncovered requirements must quote the task")
    if errors:
        # The existing single correction must see all invalid items, not just
        # the first one and then fail again on an unrelated later item.
        raise ValueError("\n".join(errors))
    # Deterministic membership rules the model may omit or invert: a sentence
    # naming backticked instance paths with a clear (non-)membership phrase.
    for line in spec.splitlines():
        if not re.search(r"扫描链|串链|不可扫描|non-?scan", line, re.I):
            continue
        selectors = [s for s in dict.fromkeys(re.findall(r"`([A-Za-z_][\w$]*(?:/[\w$\[\]*]+)+)`", line))
                     if membership_polarity(line, s) is not None]
        if selectors:
            # Model rules citing this sentence through a different (resolved
            # hierarchy) target contradict its literal selectors; drop them.
            req["scan_membership"] = [rule for rule in req["scan_membership"]
                                      if rule["target"] in selectors or line not in rule.get("task_quote", "")]
        for selector in selectors:
            value = membership_polarity(line, selector)
            existing = next((rule for rule in req["scan_membership"] if rule["target"] == selector), None)
            if existing is not None:
                existing["value"] = value
                continue
            req["scan_membership"].append({"target": selector, "value": value, "task_quote": line,
                                           "source": "deterministic_sentence_parser"})
    # A named functional domain excluded from chains without a literal path:
    # resolve it only when its ASCII keywords select exactly one hierarchy.
    generic = {"SCAN", "DFT", "FF", "SFF", "DFF", "CHAIN", "ICG", "RAM", "TOP", "CPU"}
    for line in spec.splitlines():
        if "`" in line and re.search(r"`[\w$]+(?:/[\w$\[\]*]+)+`", line):
            continue
        if not re.search(r"(?:不纳入|不参与|不允许参与|不得参与|排除出?)[^。；;\n]*扫描链", line):
            continue
        tokens = {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9]{2,}", line) if w.upper() not in generic}
        if not tokens:
            continue
        matches = [item for item in design_context
                   if all(t in item["path"].rsplit("/", 1)[-1].lower() for t in tokens)]
        if len(matches) != 1 or any(rule["target"] == matches[0]["path"] for rule in req["scan_membership"]):
            continue
        req["scan_membership"].append({"target": matches[0]["path"], "value": "exclude", "task_quote": line,
                                       "source_selector": matches[0]["selector"], "module_type": matches[0]["type"],
                                       "source": "deterministic_domain_parser"})
    # A scan enable explicitly controlling both SFF shift/capture and ICGs
    # has usage all, whatever single usage a model extraction chose.
    for signal in req["signals"]:
        if signal.get("role") != "scan_enable":
            continue
        for sentence in re.split(r"[；;\n]", spec):
            if (f"`{signal['port']}`" in sentence and re.search(r"ICG|门控", sentence, re.I)
                    and re.search(r"shift|capture|移位|捕获", sentence, re.I)):
                signal["usage"] = "all"
                break
    return req


def extract_task_constraints(spec, generate, timeout, target, *, design_context=()):
    lines = spec.splitlines()
    source_view = "\n".join(f"L{i}: {line}" for i, line in enumerate(lines, 1))
    def actual_quote(item):
        start, end = item["line_start"], item["line_end"]
        if not isinstance(start, int) or not isinstance(end, int) or not 1 <= start <= end <= len(lines):
            raise ValueError(f"Task evidence line range L{start}-L{end} is invalid; require 1 <= line_start <= line_end <= {len(lines)}")
        return "\n".join(lines[start-1:end])
    instruction = (
        "Extract explicit Scan task constraints from this task text ONLY, independently of any candidate script. "
        "Return constraints and uncovered. Cite actual displayed L numbers using line_start and line_end; "
        "the program extracts the exact task text, so do not copy or paraphrase quotes. Each constraint also has "
        "kind, target (object/config name, or empty), value (string). Do not invent defaults or infer from names. "
        "Numeric kinds have DIFFERENT UNITS: chain_count is the exact NUMBER OF CHAINS; max_chain_count "
        "is an upper bound on the NUMBER OF CHAINS; max_cells_per_chain is the maximum LENGTH OF EACH CHAIN "
        "in scan cells/bits, NOT a bound on how many chains exist. All are positive integers. A length bound "
        "applying to every chain is global even when phrased per-chain. NEVER convert individual partition "
        "counts or lengths to global constraints. Markdown partition tables and deliverable filenames are already "
        "parsed separately: do not extract their per-partition settings as global settings or global enables. "
        "Numeric target MUST be empty. Explicit wrapper/unwrapper chain counts and internal scan-chain "
        "lengths in wrapper tasks are also parsed separately: OMIT those from numeric extraction. "
        "Never collapse different chain classes into one global count, and never put wrapper, unwrapper, "
        "a partition name or internal_scan_chain in numeric target. "
        "chain_partition has empty target and value=the one partition explicitly required for ALL "
        "internal chains. For a task requiring all chains in the default partition, use value "
        "Default_Partition. This is separate from chain_count. Do not infer a default partition "
        "when unspecified, or collapse several required partitions into one. "
        "drc_zero has value true ONLY if the task explicitly requires no violations. Do not override explicit "
        "allowed residual violations with a generic desire for a clean design. "
        "config targets: mix_clocks, mix_edges, add_lockup, respect_sff_se_connection (values True/False), "
        "internal_clocks (none/single/multi). Signal kinds clock/reset/scan_enable use target=port and value "
        "active_high or active_low when the task states active polarity, inactive_0 or inactive_1 when it states "
        "off_state/inactive level explicitly, or empty if unspecified. Never use bare 0/1 for these signal kinds. "
        "High-active means active_high (the program derives off_state=0); low-active means active_low "
        "(off_state=1). Signal target must be one actual explicitly named port (never empty, all, or a list). "
        "A generic statement about all enables without a named GLOBAL enable is uncovered, not a signal constraint. "
        "constant uses target=port and value=0/1. Only GLOBAL scan_enable, not per-partition. "
        "scan_enable_usage uses target=one global scan-enable port and value=all, scan or clock_gating. "
        "Extract all when the task explicitly requires that enable to cover BOTH scan cells and "
        "clock gating; cite the entire requirement even when split across adjacent lines. "
        "A description of a functional reset's polarity is NOT a requirement to configure a scan "
        "reset when the text says no scan-reset declaration is needed; omit such a reset constraint. "
        "Wrapper controls have DISTINCT kinds: wrapper_clock, wrp_in_shift_en, wrp_out_shift_en, "
        "wrp_in_capture_en, wrp_out_capture_en. Use the same active/inactive polarity values for them. "
        "Never convert wrapper clock to ordinary clock or wrapper shift/capture enables to scan_enable. "
        "Assign each named signal only the role explicitly stated for it. An ordinary functional "
        "input does not become scan_enable merely by appearing alongside a scan control in a "
        "Boolean expression. Do not require every operand of a disjunction to be asserted. "
        "An active-high clock_gating scan enable is NOT a constant: the tool asserts its active "
        "level during the scan phase. A requirement to open gates during scan does not request "
        "constant 1 or a signal role change. Preserve explicit clock_gating roles. "
        "wrapper_port target=individual port, value=dedicated/custom/shared/none; custom means input-provided "
        "or user-defined cells are required. A generic all-port wrapper request with no individually named "
        "port belongs in uncovered, never wrapper_port with an empty target. Never extract examples or "
        "negated requirements as positive demands. "
        "output_file uses empty target and value=one explicitly required output basename (.v/.rpt/.ctl/.def), "
        "including filenames in prose, not only tables. Do NOT include input files or wildcard patterns. "
        "Final dofiles, per-round logs and decision JSON are already enforced by the runner's delivery "
        "contract: OMIT these from output_file, even when listed in the task's output table. "
        "cell_mapping uses target=original FF cell name, value=required scan replacement cell name. "
        "Copy the cell identifiers literally from the task, including a suffix-only abbreviation when "
        "the task uses one; actual library names will be resolved from the supplied Liberty. Do not "
        "invent a mapping just because the task asks for scan insertion. "
        "scan_membership uses target=one literal instance path, hierarchy path or wildcard selector "
        "from the task, value=include/exclude. A hierarchy target covers descendants; do not add "
        "wildcards absent from the task quote. For 'exclude hierarchy except PATTERN', extract "
        "BOTH the excluded hierarchy and the included exception pattern. "
        "Polarity follows the MAIN clause, and the exception takes the opposite: for "
        "'A 下的所有可扫描单元，除了 B 可匹配到的单元，其余均不允许参与扫描链' A is exclude and B is include; "
        "for 'A 下的单元除了 B 以外都需要参与' A is include and B is exclude. Re-read the negation before answering. "
        "For an explicit same-prefix numbered range such as item_0 到 item_4 or item_0 至 item_4, "
        "return one constraint for each exact member, not the whole prose range or a broader wildcard. "
        "Cite the object declaration AND its membership rule together when the rule uses a pronoun "
        "such as this latch; a citation without the literal selector is invalid. "
        "Exception: if a task names an excluded functional domain rather than its literal path, "
        "resolve it using the provided hierarchy_objects and return its exact [[Hn]] selector. "
        "Select the narrowest actual hierarchy implementing that domain, not its enclosing unrelated "
        "logic. Cite the explicit task permission; never infer RAM/storage exclusions from design "
        "inventory or prospective DRC difficulty. These scopes are frozen before any candidate repair "
        "and cannot be expanded later to suppress failed clocks. "
        "This represents scan-chain membership, NOT ICG-control exclusion or wrapper-port policy. Do not interpret descriptions "
        "of the design inventory as scan exclusions. "
        "List other substantive requirements not covered by these kinds or the existing partition/output table "
        "parser as source line ranges in the TOP-LEVEL uncovered array; uncovered is NEVER a constraint kind. "
        "already_parsed_requirements shows what the deterministic parser enforces. Preserve those object "
        "scopes: a row describing Default_Partition among several partitions is NOT a global partition. "
        "Do not re-extract partition table rows as global counts, lengths, enables or partitions. "
        "Multiple source clocks or a clock mux do NOT imply internal_clocks=multi. "
        "Independent clock domains created with pseudo-primary inputs do not imply that policy either. "
        "Do not replace an already_parsed_requirements config value with a different value inferred "
        "from a topology description; omit the redundant setting and leave the topology requirement uncovered. "
        "Extract an internal_clocks policy only from an explicit internal-clock requirement, "
        "not from the number of top-level clocks or a preferred implementation. "
        "do not claim complete semantic coverage.")
    context = {"task_spec_lines": source_view, "already_parsed_requirements": _task_requirements(spec)}
    if design_context:
        context['hierarchy_objects'] = [{key: item[key] for key in ('selector', 'path', 'type')} for item in design_context]
    deadline, prompt, attempts = time.monotonic() + timeout, json.dumps(context, ensure_ascii=False, separators=(',', ':')), []
    short = timeout <= 240
    path = target / "task_contract.json"
    for attempt in range(2):
        shape_error, extracted, merge_error = None, None, None
        try:
            raw, usage = generate(prompt, max(1, deadline-time.monotonic()), instruction, output_schema=TASK_CONSTRAINTS)
        except ModelResponseError as exc:
            raw, usage, shape_error = exc.plan, exc.usage, exc
        attempts.append({"model_output": raw, "usage": usage})
        record = {"attempts": attempts,
                  "usage": {key: sum(a["usage"][key] for a in attempts)
                            if all(isinstance(a.get("usage"), dict) and isinstance(a["usage"].get(key), int) for a in attempts) else None
                            for key in ("input_tokens", "output_tokens", "total_tokens")},
                  "scope": "Task-only model extraction plus deterministic checks; not complete semantic proof"}
        try:
            if shape_error is not None:
                raise shape_error
            extracted, extraction_errors = {"constraints": [], "uncovered": []}, []
            # A bad uncovered line range must not hide an independent semantic
            # error until after the one allowed correction has been consumed.
            for field in ("constraints", "uncovered"):
                for index, item in enumerate(raw[field]):
                    try:
                        quote = actual_quote(item)
                    except ValueError as exc:
                        extraction_errors.append(f"{field}[{index}]: {exc}")
                        continue
                    extracted[field].append({"quote": quote, **{k: item[k] for k in ("kind", "target", "value")}}
                                            if field == "constraints" else quote)
            record["extraction"] = extracted
            merge_error = None
            try:
                requirements = merge_task_constraints(spec, extracted, design_context=design_context)
            except ValueError as exc:
                merge_error = exc
                extraction_errors.append(str(exc))
            if extraction_errors and merge_error is not None and len(extraction_errors) == 1:
                # Invented output filenames are the common invalid item; they
                # are not task facts, so drop them now instead of paying a
                # correction round. Any other invalid item still gets one.
                lines = str(merge_error).splitlines()
                if lines and all(re.match(r"constraints\[\d+\] \(output_file,", line) for line in lines) \
                        and _drop_invalid_constraints(spec, extracted, merge_error, design_context, record):
                    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
                    return record["requirements"], record["extraction"]["uncovered"]
            if extraction_errors:
                raise ValueError("\n".join(extraction_errors))
        except ValueError as exc:
            # Correct an actually invalid extraction once; never relax validation,
            # repeat Scan, or extend the original case deadline for this repair.
            attempts[-1]["validation_error"] = str(exc)
            path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            # A correction costs a full model call; on short case deadlines it
            # has repeatedly consumed the time needed for the actual repair.
            if attempt or short or deadline-time.monotonic() <= 30:
                salvaged = shape_error is None and _drop_invalid_constraints(
                    spec, extracted, merge_error, design_context, record)
                if not salvaged:
                    # Never fail the whole case on extraction: keep only the
                    # deterministic parser's requirements and say so.
                    record.update(extraction={"constraints": [], "uncovered": []},
                                  requirements=merge_task_constraints(spec, {"constraints": [], "uncovered": []},
                                                                      design_context=design_context),
                                  fallback="model extraction invalid; deterministic parser requirements only")
                path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
                return record["requirements"], record["extraction"]["uncovered"]
            prompt = json.dumps({**context, "invalid_extraction": raw, "validation_error": str(exc),
                "request": "Correct every listed invalid item. Omit only constraints already enforced by the displayed parser; put other unsupported explicit requirements in the top-level uncovered array."}, ensure_ascii=False, separators=(',', ':'))
            continue
        record["requirements"] = requirements
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        return requirements, extracted["uncovered"]


def _drop_invalid_constraints(spec, extracted, merge_error, design_context, record):
    """After the single correction, keep the valid constraints instead of
    failing the whole case.  Each rejected item is still validated (never
    accepted with relaxed rules); its cited task text moves to uncovered so the
    plan and verification still see that requirement.  Unindexed errors stay fatal."""
    if merge_error is None:
        bad = set()
    else:
        lines = str(merge_error).splitlines()
        bad = {int(m[1]) for m in (re.match(r"constraints\[(\d+)\] ", line) for line in lines) if m}
        if not bad or any(not re.match(r"constraints\[\d+\] ", line) for line in lines):
            return False
    kept = [item for i, item in enumerate(extracted["constraints"]) if i not in bad]
    dropped = [item for i, item in enumerate(extracted["constraints"]) if i in bad]
    uncovered = list(extracted["uncovered"]) + [item["quote"] for item in dropped if item["quote"] not in extracted["uncovered"]]
    salvage = {"constraints": kept, "uncovered": uncovered}
    try:
        requirements = merge_task_constraints(spec, salvage, design_context=design_context)
    except ValueError:
        return False
    record.update(extraction=salvage, requirements=requirements,
                  dropped_constraints=[{**item, "reason": "invalid after correction; cited text kept as uncovered"} for item in dropped])
    return True


def _task_requirements(task_spec):
    """Extract only unambiguous, report-checkable requirements from the prose."""
    if not task_spec:
        return {}
    text = task_spec.read_text(encoding="utf-8", errors="replace") if hasattr(task_spec, "read_text") else str(task_spec)
    text = text.replace("**", "")
    req = {"outputs": [], "report_headers": [], "output_extensions": [], "signals": [], "formats": [], "partitions": [], "chain_count": None,
           "max_count": None, "max_length": None, "config": {}, "cell_mappings": {}, "scan_membership": [], "allowed_ignores": [],
           "allowed_residuals": [], "allow_default_partition": False,
           "drc_zero": bool(re.search(r"DRC[^。\n]*无[^。\n]*违例|\b(?:zero|no)\s+(?:DRC\s+)?violations?\b|0\s+violations", text, re.I))}
    # Only concrete deliverables are accepted here; dofile/log/agent metadata are not
    # semantic evidence and remain covered by the runner's declared-artifact checks.
    # Output tables are explicit evidence even when the filename is not fenced.
    output_text = re.split(r"(?m)^#{1,4}\s*(?:\d+\.\s*)?输出(?:要求|文件)\s*$", text)[-1] if re.search(r"输出(?:要求|文件)", text) else ""
    delivery_lines = [line for line in text.splitlines()
                      if re.match(r'^\s*(?:[-*]\s*)?(?:交付|导出)(?=\s|[:：])', line)]
    for line in dict.fromkeys(output_text.splitlines() + delivery_lines):
        cells = [c.strip().strip('`') for c in line.split('|')]
        if len(cells) >= 3 and re.fullmatch(r"[^/\\\s]+\.(?:v|rpt|ctl|def)", cells[1], re.I):
            if cells[1] not in req["outputs"]:
                req["outputs"].append(cells[1])
        for filename in re.findall(r"`([^`/\\\s]+\.(?:v|rpt|ctl|def))`", line):
            if filename not in req["outputs"]:
                req["outputs"].append(filename)
        if line in delivery_lines:
            for filename in re.findall(r'(?<![\w/\\.-])([A-Za-z_][\w.-]*\.(?:v|rpt|ctl|def))(?![\w/\\.-])', line, re.I):
                if filename not in req['outputs']:
                    req['outputs'].append(filename)
    for wording, header in (("插链后网表内寄存器报告", r"Obj_?State"), ("扫描链报告", r"ChainProperty"),
                            ("插链分区报告", r"ByClock"), ("插链配置报告", r"ScanConfigurationParameter"),
                            ("插链信号报告", r"SignalType")):
        if wording in output_text:
            req["report_headers"].append(header)
    for wording, extension in (("插链后网表", ".v"), ("CTL 文件", ".ctl"), ("SCANDEF 文件", ".def")):
        if wording in output_text:
            req["output_extensions"].append(extension)
    req["formats"] = re.findall(r"`([A-Za-z_]\w*%d)`", text)
    # A task can authorize Ignore and residual failures in different clauses of
    # one bullet. Residual permission must never grant a DRC downgrade.
    for clause in re.split(r"[。；;\n]", text):
        if re.search(r"不得|禁止|不允许", clause):
            continue
        if "忽略" in clause:
            req["allowed_ignores"].extend(re.findall(r"DFTR-[A-Z]+\d+|DFTR\d+", clause))
        if re.search(r"允许(?:遗留|存在)", clause):
            req["allowed_residuals"].extend(re.findall(r"DFTR-[A-Z]+\d+|DFTR\d+", clause))
    if re.search(r"启用\s*lockup|插入\s*lockup|插入 lockup", text, re.I):
        req["config"]["add_lockup"] = "True"
    if re.search(r"(?<!不)允许不同时钟(?:边沿|沿)[^。\n]*混合", text):
        req["config"]["mix_edges"] = "True"
    if re.search(r"不允许不同时钟(?:边沿|沿)混合", text):
        req["config"]["mix_edges"] = "False"
    if "不得跨时钟域" in text or re.search(r"不允许混合不同时钟域", text):
        req["config"]["mix_clocks"] = "False"
    if "不使用内部门控时钟（伪主输入声明者除外）作为扫描链时钟" in text:
        req["config"]["internal_clocks"] = "none"
    req["floating_clock_domains"] = "这些门控时钟单元的时钟输入引脚均悬空" in text
    for port, value in re.findall(r"`([^`]+)`\s*锁定为\s*([01])", text):
        req["signals"].append({"port": port, "role": "constant", "constant_value": value})
    for ports in re.findall(r"顶层时钟端口\s*([^\n]+?)均配置为时钟信号", text):
        req["signals"].extend({"port": port, "role": "clock"} for port in re.findall(r"`([^`]+)`", ports))
    # Role recognition comes from explicit wording, never a port-name suffix.
    for pattern, role in (
        (r"将扫描时钟配置为\s*`([^`]+)`", "clock"),
        (r"(?:顶层时钟(?:端口)?|单一主时钟端口)\s*`([^`]+)`", "clock"),
        (r"`([^`]+)`\s*配置为复位信号", "reset"),
        (r"复位\s*`([^`]+)`\s*配置为异步复位", "reset"),
        (r"(?:扫描使能[^。\n]*?新建端口|默认的扫描分区[^。\n]*?scan enable 信号)\s*`([^`]+)`", "scan_enable"),
        (r"`([^`]+)`\s*控制 SFF", "scan_enable"),
        (r"\|\s*`([^`]+)`\s*\|\s*(?:扫描使能端口|时钟门控旁路端口)", "scan_enable"),
    ):
        for port in re.findall(pattern, text):
            entry = {"port": port, "role": role}
            if entry not in req["signals"]:
                req["signals"].append(entry)
    for match in re.finditer(r"`([^`]+)`([^`。；;\n]*\bclock_gating\b[^`。；;\n]*)", text):
        entry = {"port": match[1], "role": "scan_enable", "usage": "clock_gating"}
        if "高有效" in match[2]:
            entry["off_state"] = "0"
        elif "低有效" in match[2]:
            entry["off_state"] = "1"
        existing = next((s for s in req["signals"] if s["port"] == match[1] and s["role"] == "scan_enable"), None)
        if existing is not None:
            existing.update(entry)
        else:
            req["signals"].append(entry)
    for entry in req["signals"]:
        port = re.escape(entry["port"])
        if entry["role"] == "reset":
            match = re.search(r"`" + port + r"`[^。\n]*(低有效|高有效)", text)
            if match:
                entry["off_state"] = "1" if match[1] == "低有效" else "0"
    # Some task books split partition counts and shared scan-enable assignments
    # into small Markdown tables.  Parse only explicit ``*_partition`` rows.
    rich_partition = any("partition" in line and len(line.split('|')) >= 7 for line in text.splitlines())
    partition_length = re.search(r"所有非默认分区的扫描链最大长度统一为\s*(\d+)", text)
    req["allow_default_partition"] = bool(re.search(r"自动归入默认分区", text))
    for line in text.splitlines():
        fields = [x.strip().strip('`') for x in line.split('|')]
        if not rich_partition and len(fields) >= 3 and re.fullmatch(r"[A-Za-z]\w*_partition", fields[1]) and re.fullmatch(r"\d+", fields[2]):
            req["partitions"].append({"name": fields[1], "clock": None, "chain_count": int(fields[2]),
                                       "max_length": int(partition_length[1]) if partition_length else None, "scan_enable": None})
    if req["partitions"]:
        shared = {}
        for line in text.splitlines():
            fields = [x.strip().strip('`') for x in line.split('|')]
            if len(fields) >= 3 and re.fullmatch(r"se_[A-Za-z0-9_]+", fields[1]):
                shared[fields[1]] = [x.strip(' `') for x in fields[2].split(',')]
        for part in req["partitions"]:
            part["scan_enable"] = next((se for se, names in shared.items() if part["name"] in names), None)
    # Column meanings come from the header: a hierarchy/size column may appear
    # before the source clock, and is not itself a clock constraint.
    split_partitions = bool(req["partitions"])
    partition_columns = None
    for line in text.splitlines():
        if split_partitions and not rich_partition:
            continue
        fields = [x.strip().strip('`') for x in line.split('|')]
        if '|' not in line:
            partition_columns = None
            continue
        if all(name in fields for name in ('chain_count', 'max_length', 'scan_enable')):
            partition_columns = {name: fields.index(name) for name in ('chain_count', 'max_length', 'scan_enable')}
            partition_columns['name'] = next((i for i, value in enumerate(fields)
                if re.fullmatch(r'分区(?:名称)?|partition(?:\s+name)?', value, re.I)), None)
            partition_columns['clock'] = next((i for i, value in enumerate(fields)
                if re.search(r'时钟|\bclocks?\b', value, re.I)), None)
            continue
        if not partition_columns or partition_columns['name'] is None:
            continue
        if any(index is not None and index >= len(fields) for index in partition_columns.values()):
            continue
        values = {name: fields[index] if index is not None else None for name, index in partition_columns.items()}
        if all(re.fullmatch(r'\d+', values[name]) for name in ('chain_count', 'max_length')):
            req['partitions'].append({**values, 'chain_count': int(values['chain_count']), 'max_length': int(values['max_length'])})
    # Single-domain prose/table forms (not partition rows).
    counts = re.findall(r"\bchain_count\b\s*(?:[=:]|\|)?\s*(\d+)", text, re.I)
    if counts and not req["partitions"]:
        req["chain_count"] = int(counts[-1])
    match = re.search(r"共\s*(\d+)\s*条链", text)
    if match and not req["partitions"]:
        req["chain_count"] = int(match[1])
    match = re.search(r"扫描链数(?:不得超过|不超过)\s*(\d+)", text)
    if match:
        req["max_count"] = int(match[1])
    lengths = re.findall(r"\bmax_length\b\s*(?:[=:]|\|)?\s*(\d+)", text, re.I)
    if lengths and not req["partitions"]:
        req["max_length"] = int(lengths[-1])
    match = re.search(r"链最大长度不超过\s*(\d+)", text)
    if match:
        req["max_length"] = int(match[1])
    match = re.search(r"(?:内部\s*scan chain 最大长度不超过|最大链长)\s*(\d+)", text, re.I)
    if match:
        req["max_length"] = int(match[1])
    req["formats"] = list(dict.fromkeys(req["formats"]))
    # Official QA Q19 explicitly clarifies this public design's ambiguous spec.
    # This is a normative task exception, not a general relaxation of DRC.
    if "eth_top" in text and "ethernet_sky130.v" in text and "DFTR10" not in req["allowed_residuals"]:
        req["allowed_residuals"].append("DFTR10")
    req["wrapper"] = {}
    req["wrapper_ports"] = []
    for clause in re.split(r"[。；;\n]", text):
        match = re.search(r"(?:端口|\bports?)\s*((?:`[^`]+`[\s、,和与]*)+)(.*)", clause, re.I)
        if not match:
            continue
        wording = match[2]
        style, cell_type = None, None
        if re.search(r"(?:必须使用|must use).*dedicated\s+wrapper", wording, re.I):
            style = "dedicated"
            if re.search(r"提供|自定义|provided|custom|user.defined", wording, re.I):
                cell_type = "user_defined"
        elif re.search(r"(?:使用|must use)\s+shared\s+wrapper", wording, re.I):
            style = "shared"
        elif re.search(r"不参与扫描路径|不使用\s*wrapper|不插入\s*wrapper|not wrapped|no wrapper", wording, re.I):
            style = "none"
        if style:
            req["wrapper_ports"].extend({"port": port, "style": style, "cell_type": cell_type}
                                        for port in re.findall(r"`([^`]+)`", match[1]))
    threshold = re.search(r"长移位寄存器（长度\s*≥\s*(\d+)）", text)
    req["segment_min_length"] = int(threshold[1]) if threshold and "所有长移位寄存器均配置为 scan segment" in text else None
    if "保持既有扫描触发器的扫描使能连接不变" in text:
        req["config"]["respect_sff_se_connection"] = "True"
    for word, key in (("wrapper", "wrapper_count"), ("unwrapper", "internal_count")):
        match = re.search(r"\b" + word + r" chain 数目要求为\s*(\d+)", text, re.I)
        if match:
            req["wrapper"][key] = int(match[1])
    match = re.search(r"链最大长度（含内部链与 wrapper 链）不超过\s*(\d+)", text, re.I)
    if match:
        req["wrapper"]["all_max_length"] = int(match[1])
    for key in ("reuse_threshold", "depth_threshold"):
        match = re.search(key + r"[）)\s]*为\s*(\d+)", text)
        if match:
            req["wrapper"][key] = int(match[1])
    if re.search(r"Wrapper\s*配置报告", output_text, re.I):
        req["report_headers"].append("WrapperConfigurationParameter")
    if re.search(r"scan segment 报告", output_text, re.I):
        req["report_headers"].append("SegmentProperty")
    return req


def _task_checks(run_dir, reports, requirements, *, full_insertion=True, design_context=(), check_details=None):
    checks, unsupported = {}, []
    if not requirements:
        return checks, unsupported, []
    missing = []
    for output in requirements["outputs"]:
        if not any(p.is_file() and p.stat().st_size and p.name == Path(output).name for group in ("deliverables", "reports") for p in (run_dir / group).glob("*")):
            missing.append(output)
    checks["task_required_outputs_present"] = not missing
    if not full_insertion:
        return checks, ["preprocessing_semantics_require_structural_audit"], missing
    for header in requirements["report_headers"]:
        checks["task_report_" + header] = any(re.search(header, value, re.I) for name, value in reports.items() if name.endswith(".rpt"))
    for extension in requirements["output_extensions"]:
        checks["task_output_type_" + extension[1:]] = any(p.stat().st_size for p in (run_dir / "deliverables").glob("*" + extension) if p.is_file())
    cfg = "\n".join(v for n, v in reports.items() if n.endswith(".rpt") and "ScanConfigurationParameter" in v)
    signal = "\n".join(v for n, v in reports.items() if n.endswith(".rpt") and "SignalType" in v)
    chain = "\n".join(v for n, v in reports.items() if n.endswith(".rpt") and "ChainProperty" in v and "ScanEnable" in v)
    class_rows = re.findall(r"(?m)^\s*([IW])\s+(\S+)\s+(\d+)\s+", chain)
    if requirements.get("floating_clock_domains"):
        expected_pins = {item["path"] + '/' + pin for item in design_context
                         if any(re.search(r"clk|clock", name, re.I) for name in item.get("unconnected_pins", []))
                         for pin in item.get("output_pins", [])}
        requirements["floating_clock_outputs_from_netlist"] = sorted(expected_pins)
        pseudo = "\n".join(v for n, v in reports.items() if n.endswith(".rpt") and "PseudoPIName" in v)
        actual_pins = set(re.findall(r"(?m)^\s*(\S+)\([IO]\)\s*$", pseudo))
        clocks = {line.split()[0] for line in signal.splitlines() if len(line.split()) > 2 and line.split()[2] == "clock"}
        checks["task_pseudo_clocks_match_floating_gate_outputs"] = bool(expected_pins) and actual_pins == expected_pins and expected_pins <= clocks
        if check_details is not None:
            check_details['task_pseudo_clocks_match_floating_gate_outputs'] = {
                'pseudo_report_present': bool(pseudo), 'expected_pins': sorted(expected_pins),
                'reported_pseudo_pins': sorted(actual_pins),
                'missing_clock_declarations': sorted(expected_pins - clocks)}
    wrapper_cfg = "\n".join(v for n, v in reports.items() if n.endswith(".rpt") and "WrapperConfigurationParameter" in v)
    # Check actual implementation, not echoed Tcl or the global default style.
    # Names come exclusively from the task; bus bits must all satisfy the rule.
    implementation_rows = []
    for name, text in (reports.items() if requirements.get("wrapper_ports") else ()):
        if not name.endswith(".rpt"):
            continue
        header = next((line for line in text.splitlines()
                       if all(key in line for key in ("PortName", "WrapperStyle", "UnWrapperReason", "DWCType", "SWCType"))), None)
        if header is None:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if not re.match(r"^\s*\d+\s", line):
                continue
            port = re.sub(r"\s*\([IO]\)\s*$", "", line[header.index("PortName"):header.index("WrapperStyle")].strip())
            style = line[header.index("WrapperStyle"):header.index("UnWrapperReason")].strip()
            cell_type = line[header.index("DWCType"):header.index("SWCType")].strip()
            implementation_rows.append((port, style, cell_type, name, number, line))
    for rule in requirements.get("wrapper_ports", []):
        rows_for_port = [row for row in implementation_rows
                         if row[0] == rule["port"] or re.fullmatch(re.escape(rule["port"]) + r"\[\d+\]", row[0])]
        wrong_rows = [row for row in rows_for_port if row[1] != rule["style"]
                      or (rule["cell_type"] is not None and row[2] != rule["cell_type"])]
        key = "task_wrapper_port_" + rule["port"]
        checks[key] = bool(rows_for_port) and not wrong_rows
        if check_details is not None:
            check_details[key] = {"expected": rule, "reported_port_bits": len(rows_for_port),
                                  "mismatching_port_bits": len(wrong_rows),
                                  "evidence": [{"source": row[3], "line": row[4], "excerpt": row[5]}
                                               for row in (wrong_rows or rows_for_port)[:3]]}
    if requirements.get("segment_min_length") is not None:
        shift = "\n".join(v for n, v in reports.items() if n.endswith(".rpt") and "ShiftRegID/CellNo" in v and "ChainName" not in v)
        groups = {}
        for name, group, index in re.findall(r"(?m)^\s*(\S+)\s+(\d+)/(\d+)\s*$", shift):
            groups.setdefault(group, {})[int(index)] = name
        expected = {(cells[min(cells)], cells[max(cells)], len(cells)) for cells in groups.values()
                    if len(cells) >= requirements["segment_min_length"]}
        segment = "\n".join(v for n, v in reports.items() if n.endswith(".rpt") and "SegmentProperty" in v)
        segments = re.findall(r"(?m)^\s*\S+\s+user_defined\s+(\d+)\s+(\S+)\s+(\S+)\s+(\S+)", segment)
        chain_names = {name for _, name, _ in class_rows}
        assigned = {(si.rsplit('/', 1)[0], so.rsplit('/', 1)[0], int(n))
                    for n, si, so, name in segments if name in chain_names}
        checks["task_long_shift_register_segments_assigned"] = bool(groups) and expected <= assigned
    for key, value in requirements.get("wrapper", {}).items():
        if key in ("wrapper_count", "internal_count"):
            checks["task_" + key] = sum(c == ("W" if key == "wrapper_count" else "I") for c, _, _ in class_rows) == value
        elif key == "all_max_length":
            checks["task_all_chain_max_length"] = bool(class_rows) and all(int(n) <= value for _, _, n in class_rows)
        else:
            checks["task_wrapper_" + key] = bool(re.search(r"(?m)^\s*" + key + r"\s+" + str(value) + r"\s*$", wrapper_cfg))
    rows = []
    for line in chain.splitlines():
        m = re.match(r"\s*I\s+\d+\s+(\d+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)(?:\s+(\S+))?", line)
        if m:
            rows.append({"length": int(m[1]), "si": m[2], "so": m[3], "enable": m[4], "clock": m[5], "partition": m[6] or ""})
    if len(requirements["formats"]) == 2:
        checks["task_port_formats"] = bool(rows) and all(
            re.fullmatch(re.escape(fmt).replace("%d", r"\d+"), row[key]) is not None
            for row in rows for key, fmt in zip(("si", "so"), requirements["formats"]))
    elif requirements["formats"]:
        unsupported.append("ambiguous_input_output_format_pair")
    if requirements["config"]:
        for key, value in requirements["config"].items():
            values = re.findall(r"(?m)^\s*" + key + r"\s+(\S+)", cfg)
            checks["task_config_" + key] = bool(values) and all(v == value for v in values)
    if requirements["signals"]:
        signal_rows = [line.split() for line in signal.splitlines()]
        for entry in requirements["signals"]:
            port, role = entry["port"], entry["role"]
            named = [r for r in signal_rows if len(r) >= 4 and r[0] == port and r[2].split("(")[0] == role]
            reported = bool(named) and all(r[3] == entry["off_state"] for r in named) if "off_state" in entry else bool(named)
            if role == "constant":
                header = next((line for line in signal.splitlines() if "ConstantValue" in line and "OwnerPartition" in line), "")
                selected = [line for line in signal.splitlines() if line.split() and line.split()[0] == port]
                reported = bool(named and header and selected) and all(
                    line[header.index("ConstantValue"):header.index("OwnerPartition")].strip() == entry["constant_value"]
                    for line in selected)
            if role in ("clock", "scan_enable") and not named:
                reported = any(r["clock" if role == "clock" else "enable"] == port for r in rows)
            checks[f"task_signal_{port}_{role}"] = reported
            if "usage" in entry:
                checks[f"task_signal_{port}_usage"] = bool(named) and all(entry["usage"] in r[4:] for r in named)
            if not requirements["partitions"] and role == "scan_enable" and entry.get("usage") != "clock_gating":
                checks[f"task_chain_enable_{port}"] = bool(rows) and all(r["enable"] == port for r in rows)
            if not requirements["partitions"] and role == "clock":
                # Associated internal pin clocks are possible; only enforce a
                # sole source-clock row when the spec explicitly forbids crossing.
                if sum(s["role"] == "clock" for s in requirements["signals"]) == 1 and (requirements["config"].get("mix_clocks") == "False" or requirements["chain_count"] is not None):
                    checks[f"task_chain_clock_{port}"] = bool(rows) and all(r["clock"] == port for r in rows)
    if requirements["partitions"]:
        results, partition_details = [], []
        for part in requirements["partitions"]:
            selected = [r for r in rows if r["partition"] == part["name"]]
            part_checks = {
                'chain_count': bool(selected) and len(selected) == part['chain_count'],
                'max_length': part['max_length'] is None or (bool(selected) and max(r['length'] for r in selected) <= part['max_length']),
                'clock': all(part['clock'] is None or part['clock'] == r['clock'] for r in selected),
                'scan_enable': all(part['scan_enable'] is None or part['scan_enable'] == r['enable'] for r in selected),
            }
            results.append(all(part_checks.values()))
            partition_details.append({'expected': part, 'checks': part_checks,
                'observed': {'chain_count': len(selected), 'max_length': max((r['length'] for r in selected), default=0),
                             'clocks': sorted({r['clock'] for r in selected}),
                             'scan_enables': sorted({r['enable'] for r in selected})}})
        checks["task_partition_chains"] = all(results)
        if check_details is not None:
            check_details['task_partition_chains'] = partition_details
        expected = {p["name"] for p in requirements["partitions"]}
        checks["task_no_extra_partition_chains"] = (
            len([r for r in rows if r["partition"] in expected]) == sum(p["chain_count"] for p in requirements["partitions"])
            and all(r["partition"] in expected or (requirements["allow_default_partition"] and r["partition"] == "Default_Partition") for r in rows))
    elif requirements["chain_count"] is not None:
        checks["task_chain_count"] = bool(rows) and len(rows) == requirements["chain_count"]
    if requirements.get('chain_partition'):
        checks['task_chain_partition_scope'] = bool(rows) and all(r['partition'] == requirements['chain_partition'] for r in rows)
        if check_details is not None and not checks['task_chain_partition_scope']:
            check_details['task_chain_partition_scope'] = {'expected': requirements['chain_partition'],
                                                          'reported': sorted({r['partition'] for r in rows})}
    if requirements["max_length"] is not None:
        checks["task_max_chain_length"] = bool(rows) and max(r["length"] for r in rows) <= requirements["max_length"]
    if requirements["max_count"] is not None:
        checks["task_max_chain_count"] = bool(rows) and len(rows) <= requirements["max_count"]
    if requirements["drc_zero"]:
        totals = [int(n) for n in re.findall(r"Total violations:\s*(\d+)", next((v for n, v in reports.items() if n.lower().endswith("drc.rpt")), ""))]
        checks["task_drc_zero"] = bool(totals) and all(n == 0 for n in totals)
    elif requirements["allowed_residuals"]:
        drc = "\n".join(v for n, v in reports.items() if n.lower().endswith("drc.rpt"))
        totals = [int(n) for n in re.findall(r"Total violations:\s*(\d+)", drc)]
        failures = [(int(n), rule) for n, rule in re.findall(r"There were (\d+) DRC rule '(DFTR[^']+)' fails", drc)]
        checks["task_drc_allowed_residuals"] = (len(totals) == 1 and totals[0] == sum(n for n, _ in failures)
                                               and all(rule in requirements["allowed_residuals"] for _, rule in failures))
    else:
        unsupported.append("nonzero_DRC_requires_task_specific_model_review")
    if not cfg:
        unsupported.append("scan_cfg_report")
    if not signal and not chain:
        unsupported.append("scan_signal_report")
    if not chain:
        unsupported.append("scan_chain_report")
    return checks, unsupported, missing


def chain_member_checks(run_dir, reports, ctl_paths):
    """Cross-check delivered CTL and actual chain/cell reports, without an oracle.

    This detects duplicated, omitted or reordered reported members; it does not
    prove that every required input FF was classified as scannable or prove LEC.
    """
    checks, details = {}, {}
    if not ctl_paths:
        return checks, details
    chain_reports = {n: t for n, t in reports.items() if 'ChainProperty' in t and 'ScanEnable' in t}
    cell_reports = {n: t for n, t in reports.items() if 'InstanceName' in t and 'CellNo' in t and 'RefName' in t}
    lengths = {}
    for text in chain_reports.values():
        lengths.update((name, int(size)) for name, size in re.findall(r'(?m)^\s*[IW]\s+(\S+)\s+(\d+)\s+', text))
    members, seen, valid, failures = {}, set(), True, []
    for source, text in cell_reports.items():
        current = None
        for line_no, line in enumerate(text.splitlines(), 1):
            match = re.match(r'^\s*(?:[IW]\s+(\S+)\s+)?(\d+)\s+(\S+)\s+\S+\s+\([^)]+\)\s+\S+', line)
            if not match:
                continue
            current = match[1] or current
            sequence = members.setdefault(current, [])
            member, index = match[3], int(match[2])
            if member in seen or index != len(sequence):
                valid = False
                if len(failures) < 3:
                    failures.append({'source': source, 'line': line_no, 'excerpt': line})
            sequence.append(member)
            seen.add(member)
    checks['chain_cell_members_unique_and_numbered'] = bool(members) and valid
    # CTL black boxes have expanded CTL members but no physical cell-report
    # rows. Their instance scope and bit budget come from the actual segment
    # report, never from a design name or a blanket missing-row exception.
    blackboxes = {}
    for text in reports.values():
        if 'SegmentProperty' not in text:
            continue
        for name, size in re.findall(r'(?m)^\s*(\S+)\s+inferred_from_ctl\s+(\d+)\s+', text):
            prefix = name.rsplit('/', 2)[0] + '/'
            blackboxes[prefix] = blackboxes.get(prefix, 0) + int(size)
    size_valid = bool(lengths) and set(members) <= set(lengths)
    ctl_valid, disagreements = True, []
    for path in ctl_paths:
        chains = re.findall(r'ScanChain\s+"([^"]+)"\s*\{([^}]+)\}', path.read_text(encoding='utf-8'))
        names, ctl_seen, blackbox_seen = [], set(), dict.fromkeys(blackboxes, 0)
        for name, body in chains:
            size = re.search(r'ScanLength\s+(\d+);', body)
            block = re.search(r'ScanCells\s+(.*?);', body, re.S)
            cells = re.findall(r'"([^"]+)"', block[1]) if block else []
            names.append(name)
            physical = []
            for cell in cells:
                prefix = next((p for p in blackboxes if cell.startswith(p)), None)
                if prefix is None:
                    physical.append(cell)
                else:
                    blackbox_seen[prefix] += 1
                ctl_valid &= cell not in ctl_seen
                ctl_seen.add(cell)
            size_valid &= len(members.get(name, [])) + len(cells)-len(physical) == lengths.get(name)
            if not size or int(size[1]) != lengths.get(name) or len(cells) != int(size[1]) or physical != members.get(name, []):
                ctl_valid = False
                if len(disagreements) < 3:
                    disagreements.append({'source': str(path.relative_to(run_dir)), 'chain': name,
                                          'ctl_members': len(cells), 'report_members': len(members.get(name, []))})
        ctl_valid &= bool(chains) and len(names) == len(set(names)) and set(names) == set(lengths)
        ctl_valid &= blackbox_seen == blackboxes
    checks['chain_cell_lengths_match_report'] = size_valid
    checks['ctl_chain_members_match_cell_report'] = ctl_valid
    if not all(checks.values()):
        details['chain_member_consistency'] = {'report_evidence': failures, 'ctl_disagreements': disagreements,
                                              'scope': 'Actual delivered CTL versus reported members/order/length; not netlist equivalence'}
    return checks, details


def inspect_run(run_dir, script, execution, *, full_insertion, task_spec=None, design_context=(), task_requirements=None, uncovered=(), source=None):
    """Check actual artifacts and report invariants independently of model approval.

    This is not a complete task specification validator or an equivalence proof.
    Preprocessing must not inherit full-insertion DRC/chain requirements.
    """
    artifacts, missing = {}, []
    for name in declared_artifacts(script):
        path = (run_dir / name).resolve()
        if not path.is_relative_to(run_dir.resolve()):
            raise ValueError("Output artifact is outside its run")
        if not path.is_file() or not path.stat().st_size:
            # Runner-added evidence reports are not deliverables; an absent one
            # only leaves its dependent check without evidence.
            if not name.startswith("reports/auto_"):
                missing.append(name)
        elif path.suffix == ".rpt":
            artifacts[name] = path.read_text(encoding="utf-8", errors="replace")
    log_name = f"{run_dir.name}.log"
    artifacts[log_name] = (run_dir / log_name).read_text(encoding="utf-8", errors="replace")
    checks = {"execution_completed": execution["exit_status"] == "completed", "declared_outputs_present": not missing}
    checks["no_unmatched_object_selectors"] = not bool(re.search(
        r"\[\s*COM-2003\]\s+Nothing matched|\[\s*SCAN-4948\]\s+Port '[^']+' does not exist",
        artifacts[log_name]))
    observed, preprocessing = [], []
    for index, line in enumerate(artifacts[log_name].splitlines(), 1):
        match = re.search(r"\[\s*CMD-0034\]\s+@\d+:\s*(examine_scan_drc|examine_scan_chain|insert_dft_logic)(?:\s|$)", line)
        if match:
            if match[1] == "insert_dft_logic" and re.search(r"-(?:connect_icg_only|replace_only|replace_unscan)\b", line):
                preprocessing.append({"command": line[match.start():], "line": index})
            else:
                observed.append({"command": match[1], "line": index})
    workflow = {"source": log_name, "observed": observed,
                "preprocessing": preprocessing,
                "passed": [item["command"] for item in observed] == list(STAGES) and checks["execution_completed"]
                          and all(item["line"] < observed[0]["line"] for item in preprocessing)}
    task_requirements = task_requirements if task_requirements is not None else _task_requirements(task_spec)
    if full_insertion:
        totals = [int(n) for name, text in artifacts.items() if name.endswith(".rpt")
                  for n in re.findall(r"Total violations:\s*(\d+)", text)]
        checks.update(workflow_order=workflow["passed"], drc_report_present=bool(totals))
        chain_reports = [text for name, text in artifacts.items() if name.endswith(".rpt") and "ChainProperty" in text]
        checks["scan_chain_rows_present"] = any(re.search(r"(?m)^\s*[IW]\s+\d+\s+[1-9]\d*\s+\S+", text) for text in chain_reports)
        if not task_requirements or task_requirements["drc_zero"]:
            checks["reported_drc_zero"] = bool(totals) and all(n == 0 for n in totals)
        if task_requirements:
            overrides = re.findall(r"CMD-0034\]\s+@\d+:\s*set_scan_drc_rule_handling\s+\{([^}]+)\}\s+(\w+)", artifacts[log_name])
            checks["only_task_permitted_drc_downgrades"] = all(
                level.lower() not in ("ignore", "warning") or set(ids.split()) <= set(task_requirements["allowed_ignores"])
                for ids, level in overrides)
        # The currently supported single-bit insertion reports expose both
        # scannable cells and chained cells. Check their consistency when present;
        # this does not independently prove that the scannable classification is correct.
        for name, text in artifacts.items():
            if not name.endswith(".rpt") or "Scannable FF Count" not in text:
                continue
            wrapper_bits = re.search(r"(?m)^\s*\d+\s+Wrapper Chain Cell Bit Count\s+(\d+)", text)
            if wrapper_bits and int(wrapper_bits[1]) > 0:
                # Wrapper insertion adds dedicated FFs; CTL segments are counted
                # as objects in Cell Count but expanded in Bit Count/chain lengths.
                # Compare each chain class to its own actual bit total, not to
                # the pre-existing scannable FF count.
                internal_bits = re.search(r"(?m)^\s*\d+\s+Scan Chain Cell Bit Count\s+(\d+)", text)
                tables = [re.findall(r"(?m)^\s*([IW])\s+\S+\s+(\d+)\s+", report)
                          for report_name, report in artifacts.items() if report_name.endswith(".rpt") and "ChainProperty" in report]
                checks["reported_chain_bit_totals"] = bool(internal_bits and tables) and all(
                    sum(int(n) for c, n in rows if c == "I") == int(internal_bits[1]) and
                    sum(int(n) for c, n in rows if c == "W") == int(wrapper_bits[1]) for rows in tables)
                continue
            scannable = re.search(r"(?m)^\s*\d+\s+Scannable FF Count\s+(\d+)", text)
            chained = re.search(r"(?m)^\s*\d+\s+Scan Chain Cell Count\s+(\d+)", text)
            tables = [re.findall(r"(?m)^I\s+\S+\s+(\d+)\s+\S+\s+\S+\s+\S+\s+\S+", report)
                      for report_name, report in artifacts.items() if report_name.endswith(".rpt") and "ChainProperty" in report]
            coverage = bool(scannable and chained and tables) and int(scannable[1]) == int(chained[1]) > 0 and all(
                lengths and sum(map(int, lengths)) == int(chained[1]) for lengths in tables)
            checks["reported_chain_coverage"] = checks.get("reported_chain_coverage", True) and bool(coverage)
    check_details = {}
    if full_insertion and task_requirements.get('cell_mappings'):
        from control_context import required_mapping_check
        mapping_ok, mapping_evidence = required_mapping_check(source, script, artifacts, task_requirements['cell_mappings'])
        checks['task_observed_cell_mapping_types'] = mapping_ok
        check_details['required_cell_mappings'] = mapping_evidence
    if 'cell_mapping_input_losses' in execution:
        checks['cell_mapping_no_lost_functional_inputs'] = not execution['cell_mapping_input_losses']
        check_details['cell_mapping_input_losses'] = execution['cell_mapping_input_losses']
    if 'constant_output_integrity' in execution:
        integrity = execution['constant_output_integrity']
        check_details['constant_output_integrity'] = integrity
        if integrity['checked']:
            checks['constant_output_bits_preserved'] = integrity['passed']
    if full_insertion:
        from control_context import explicit_exclusion_check
        element_reports = {}
        for command in re.sub(r'\\\s*\n', ' ', script).splitlines():
            if not re.match(r'^\s*rpt_scan_element\b', command) or not re.search(r'-type\s+all\b', command):
                continue
            try:
                names = declared_artifacts(command)
            except ValueError:
                # A console-only/unsupported path cannot establish the full
                # object report; the existing failure feedback requests one.
                continue
            element_reports.update({name: artifacts[name] for name in names if name in artifacts})
        exclusion_ok, exclusion_details = explicit_exclusion_check(
            element_reports, task_requirements.get('scan_membership', []),
            required='set_scan_element' in script)
        checks['only_task_authorized_ff_exclusions'] = exclusion_ok
        check_details['explicit_ff_exclusions'] = exclusion_details
        ctl_paths = [run_dir / name for name in declared_artifacts(script)
                     if name.startswith('deliverables/') and name.endswith('.ctl') and (run_dir / name).is_file()]
        member_checks, member_details = chain_member_checks(run_dir, artifacts, ctl_paths)
        checks.update(member_checks)
        check_details.update(member_details)
        if task_requirements.get('scan_membership'):
            from control_context import required_membership_check
            membership_ok, membership_evidence = required_membership_check(source, script,
                [path.read_text(encoding='utf-8') for path in ctl_paths], task_requirements['scan_membership'], reports=artifacts)
            checks['task_selected_scan_membership'] = membership_ok
            check_details['selected_scan_membership'] = membership_evidence
    task_checks, unsupported, task_missing = _task_checks(run_dir, artifacts, task_requirements, full_insertion=full_insertion, design_context=design_context, check_details=check_details)
    unsupported.extend(uncovered)
    checks.update(task_checks)
    if (run_dir / 'lec_result.json').exists():
        lec = json.loads((run_dir / 'lec_result.json').read_text(encoding='utf-8'))
        checks['original_to_used_prescan_equivalence'] = lec['passed'] is True
        artifacts['lec_report.rpt'] = (run_dir / 'reports/lec_report.rpt').read_text(encoding='utf-8')
        check_details['equivalence'] = lec
    acceptance = {"accepted": all(value is True for value in checks.values()), "checks": checks, "missing_outputs": missing + task_missing,
                  "check_details": check_details,
                  "scope": "Execution, declared artifacts" + (", workflow and reported DRC" if full_insertion else "; semantic acceptance not established")}
    if task_requirements:
        acceptance["task_spec"] = {"requirements": task_requirements, "unsupported": unsupported,
                                    "missing_outputs": task_missing}
    (run_dir / "acceptance.json").write_text(json.dumps(acceptance, indent=2), encoding="utf-8")
    return acceptance, artifacts, workflow

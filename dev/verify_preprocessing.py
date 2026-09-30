"""Independent Yosys structural acceptance for public T1/C1 and C4 (not LEC)."""

import argparse
import json
from pathlib import Path
import re
import subprocess


def read_design(path, library, destination):
    script = f'read_liberty -lib {library}; read_verilog {path}; hierarchy -top openE902; flatten; write_json {destination}'
    subprocess.run(["yosys", "-Q", "-T", "-p", script], check=True, stdout=subprocess.DEVNULL)
    return json.loads(destination.read_text())["modules"]["openE902"]


def verify(original, final):
    icg = lambda c: c["type"] == "sky130_fd_sc_hd__sdlclkp_1"
    ff = lambda c: bool(re.match(r"sky130_fd_sc_hd__(?:s?df|s?edf)", c["type"]))
    before = original["cells"]
    after = final["cells"]
    checks = {}
    old_icg = {n: c for n, c in before.items() if icg(c)}
    new_icg = {n: c for n, c in after.items() if icg(c)}
    checks["icg_instance_set_unchanged"] = old_icg.keys() == new_icg.keys()
    checks["ff_count_unchanged"] = sum(map(ff, before.values())) == sum(map(ff, after.values()))
    checks["top_port_names_unchanged"] = original["ports"].keys() == final["ports"].keys()
    # A bit can have multiple aliases; matching any original qualified alias proves connectivity.
    def aliases(design):
        result = {}
        for name, net in design["netnames"].items():
            for index, bit in enumerate(net["bits"]):
                result.setdefault(bit, set()).add((name, index))
        return result
    old_alias, new_alias = aliases(original), aliases(final)
    def same_connection(a, b):
        if len(a) != len(b):
            return False
        return all(x == y if isinstance(x, str) or isinstance(y, str) else bool(old_alias.get(x, set()) & new_alias.get(y, set())) for x, y in zip(a, b))
    clock_enable_ok = True
    control_ok = True
    rewired = 0
    se = final["ports"]["pad_yy_gate_clk_en_b"]["bits"]
    for name, cell in old_icg.items():
        if name not in new_icg:
            clock_enable_ok = control_ok = False
            continue
        new = new_icg[name]
        for pin in ["CLK", "GATE"]:
            clock_enable_ok &= same_connection(cell["connections"][pin], new["connections"][pin])
        old_control = cell["connections"]["SCE"]
        if old_control == ["0"] and not name.startswith("x_cr_had_top."):
            rewired += 1
            control_ok &= new["connections"]["SCE"] == se
        else:
            control_ok &= same_connection(old_control, new["connections"]["SCE"])
    checks["icg_clock_and_enable_unchanged"] = bool(clock_enable_ok)
    checks["icg_controls_match_spec"] = bool(control_ok)
    pll = [c for n, c in after.items() if n.startswith("x_cr_core_top.x_cr_sys_io_pll.") and ff(c)]
    checks["pll_has_no_scan_ff"] = bool(pll) and all(not c["type"].split("__")[1].startswith("s") for c in pll)
    outside_plain = [n for n, c in after.items() if ff(c) and not n.startswith("x_cr_core_top.x_cr_sys_io_pll.") and not c["type"].split("__")[1].startswith("s")]
    return {"checks": checks, "icg_count": len(old_icg), "expected_rewired_icg": rewired, "ff_count": sum(map(ff, before.values())), "pll_ff_count": len(pll), "outside_remaining_plain_ff": outside_plain, "scope": "Structural checks, not functional equivalence or full scannability proof"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    root = Path(args.output)
    work = root / "structural_check"
    work.mkdir(exist_ok=False)
    library = "/input/lib/sky130.lib"
    original = read_design("/input/netlist/opene902.v", library, work / "before.json")
    final = read_design(str(root / "final_results/deliverables/post_replace_unscan.v"), library, work / "after.json")
    result = verify(original, final)
    (work / "acceptance.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result))
    return 0 if all(result["checks"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())

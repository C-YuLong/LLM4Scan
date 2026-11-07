"""Frozen 2026-10-01 combinations; reuse Scan32 generation, execution and audit.

These are new combinations of existing circuit primitives, not unseen industrial
IP. Freeze before the first model run. A failed case used for subsequent changes
is development evidence thereafter, not an untouched holdout.
"""
import scan32
from scan32 import case

CASES = [
    case("G01_micro_mixed12", 12, 3, 2, reset=2, depth=2, mode=True,
         falling=True, repair=True, faults=["clock", "reset", "gate", "mode", "count", "ctl"]),
    case("G02_shared_hierarchy5376", 5376, 7, 6, reset=1, depth=5, mode=True,
         hierarchy=True, same_clock=True, exclude=3, repair=True,
         faults=["clock", "reset", "gate", "mode", "count", "ctl"]),
    case("G03_segment_modes3456", 3456, 3, 6, depth=2, mode=True, segments=5,
         repair=True, faults=["clock", "gate", "mode", "count", "scan_usage", "ctl"]),
    case("G04_wrapper_modes9472", 9472, chains=16, reset=2, depth=3, mode=True,
         wrapper=True, repair=True, faults=["clock", "reset", "gate", "mode", "wrapper", "ctl"]),
    case("G05_clean_shared14880", 14880, 5, 12, reset=1, depth=6, mode=True,
         falling=True, hierarchy=True, same_clock=True, exclude=5, repair=True),
    case("G06_falling_deep150k", 150000, 6, 25, reset=2, depth=7, mode=True,
         falling=True, repair=True, faults=["clock", "reset", "gate", "mode", "count", "ctl"]),
]


if __name__ == "__main__":
    scan32.CASES = CASES
    scan32.SUITE = scan32.ROOT / "benchmarks/generalization_20261001"
    scan32.main()

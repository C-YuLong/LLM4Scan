# Scan command notes for first milestone

Sources: official Scan User Manual PDF pages 16–29, 32, 51–53, 62–71.
These notes are not a complete command reference.

component_sources.__control_trace__ contains bounded, line-numbered excerpts
selected by connectivity upstream of supplied Liberty FF/latch control pins,
including ordinary-named logic inside large files. Read these actual gates and
hierarchy bindings when deciding reset/clock bypass values. It is not a proof:
state_boundaries require reasoning about state/initialization; unresolved nets,
unknown cell types or truncation must not be interpreted as controllable inputs.
Do not derive a required polarity merely from a signal or module name.
library_cell_functions contains output/state functions from the actual Liberty.
Use it to evaluate selector polarity; mux pin naming alone is not a truth table.
Keep each excerpt's module scope and follow every parent/child port binding.
An ordinary combinational mux/AND/OR/inverter cone does not by itself require
associated_internal_clocks. Fix a demonstrated selector/bypass value first.
If a candidate added an internal-clock association and DFTR8 remains, reconsider
that added association against the ORIGINAL script and actual stateless cone;
do not keep it merely because the candidate started producing scan chains.

Official Q&A clarifications (official/QA.MD, read 2026-09-30):
- Q0: the Q&A and contest guide have equal normative status. Tool examples explain
  syntax but do not override the actual task requirements or official clarifications.
- Q15/A16: no tool-call-count limit; the case wall-time limit still applies.
- Q19/A19: public Task 2 case 2 (eth_top / ethernet_sky130.v) may retain DFTR10.
  Ignore means removing a rule from reporting; permitted residual DFTR10 must
  remain visible. This permission does NOT apply to arbitrary other designs.
- Q24/A24: resolution evidence may come from any source that demonstrates the
  issue is resolved, not only a particular terminal/log success phrase. Evidence
  must still cite real artifacts; an unsupported model assertion is not evidence.
- Q28: hidden task specifications are Chinese. Q30: current public cases do not
  require pre-scan netlist edits. Respect each task's prohibition on input edits.
- Public cases do not count toward official scores (Q11); platform success is
  flow confirmation, not a score or proof of hidden-case correctness (Q10/Q18).

Read a Liberty library: load_lib [file join $input_dir lib sky130.lib]
Read a Verilog file: load_netlist [file join $input_dir netlist design.v]
Select actual Verilog top: present_design top_name
For task-required independently controllable internal clock pins, first declare
their actual resolved hierarchy pin paths as pseudo inputs (manual p. 17):
add_pseudo_pi {HIERARCHY/OUTPUT_PIN}
set_scan_signal -type clock -port HIERARCHY/OUTPUT_PIN -off_state 0
rpt_pseudo_pi -file "$out_rpt_dir/pseudo_pi.rpt"
Do not declare all gated clocks as independent sources: this is only appropriate
for the specific task-required floating-input domains. Use actual design/report
evidence to locate them; do not invent pin names or suppress their DRC failures.
Installed-tool SCAN-1638: a pseudo input derived from an INPUT pin can only be
constrained to a constant, not made a clock. For a floating-input clock-gate
domain, use that gate's OUTPUT pin as the pseudo source instead of its empty input.
Set task-required test-mode constants explicitly (manual p. 18):
set_scan_signal -type constant -port MODE_PORT -constant_value 1
set_scan_signal -type constant -port MBIST_PORT -constant_value 0
Use the actual task/design port names and required values, not these placeholders.
Optional explicit mapping: set_scan_cell_mapping DFF_CELL SFF_CELL
Use full cell identifiers from library_scan_cell_names. A task may abbreviate a
cell suffix in prose; match it to the actual supplied library name, not a bare
suffix that the tool cannot load. If a required mapping fails name lookup, fix
the identifier instead of deleting the task's required mapping and relying on
an automatic choice of a different cell type.
An explicit mapping must preserve the original functional input behavior, not
just produce a scannable cell. Check supplied Liberty next_state/control functions:
mapping an enabled DFF to an SFF without its data-enable input can silently lose
hold behavior even when Scan reports zero DRC and full coverage. Do not ignore
cell_mapping_input_losses evidence. If no compatible SFF exists, only a task that
permits pre-scan edits may use an equivalent rewrite with real original-to-used
EQY proof; otherwise report that the requested mapping is not demonstrated safe.

Preprocessing only:
set_scan_signal -type scan_enable -port PORT -usage clock_gating
set_dft_clock_gating_cfg -exclude_elements {excluded_hierarchy}
set_scan_element false {nonscannable_hierarchy}
insert_dft_logic -connect_icg_only -verbose
dump_netlist -file "$out_res_dir/post_connect_icg.v"
insert_dft_logic -replace_only -verbose
dump_netlist -file "$out_res_dir/post_replace_sff.v"
insert_dft_logic -replace_unscan -verbose
dump_netlist -file "$out_res_dir/post_replace_unscan.v"

connect_icg_only connects eligible tied-off ICG test controls; exclusions retain existing controls.
replace_only replaces FFs without constructing scan chains. replace_unscan reverts non-scannable SFFs.
Do not create scan data ports or run chain construction when the task requests only preprocessing.

For full insertion (not preprocessing):
set_scan_signal -type clock -port CLK -off_state 0
set_scan_signal -type reset -port RESET -off_state 1
set_scan_signal -type scan_enable -port SE -usage all
set_scan_cfg -chain_count N -mix_clocks false -mix_edges false
examine_scan_drc -file "$out_rpt_dir/drc.rpt"
examine_scan_chain
insert_dft_logic
rpt_scan_chain -file "$out_rpt_dir/scan_chain.rpt"
rpt_scan_chain_cell -file "$out_rpt_dir/scan_cell.rpt"
dump_netlist -file "$out_res_dir/post_scan.v"

The insertion summary command is rpt_insertion_info, not rpt_scan_insertion_info.
Preserve valid original report-command names when correcting unrelated settings;
do not add a scan prefix by analogy with other report commands (CMD-0074).

set_scan_drc_rule_handling RULE_IDS LEVEL
RULE_IDS and LEVEL are separate positional operands. A braced rule list is one
operand. DFTDRC-3006 "Invalid DRC ID for 'Warning' level" does not say Warning
is universally unsupported: DFTR-TIE0/DFTR-TIE1 allow Warning, but it still emits
violations and does not satisfy an explicit task requirement to ignore those
rules or leave zero non-permitted violations. Conversely, Ignore does not fix
an invalid rule ID. Diagnose independent identifier/required-level errors
separately; permission to ignore a rule alone does not require changing its level.

Name automatically created chain ports with set_scan_cfg -si_port_format PATTERN
and -so_port_format PATTERN. si_port/so_port are NOT set_scan_signal -type values.
scan_data_in/scan_data_out are signal roles for actual ports, not format strings.
Set those global formats while Default_Partition is current, before creating
custom partitions, or explicitly return to Default_Partition first. The installed
tool makes a newly added partition current; it rejects these format options in
a user partition with COM-2002, leaving test_si/test_so despite a normal exit.
For a task exclusion with an explicit exception, preserve BOTH requirements:
set_scan_element false {EXCLUDED_HIERARCHY/*}
set_scan_element true {EXCEPTION_PATTERN}
Apply the narrower exception after the broad exclusion; omitting it excludes
the required registers too. Verify actual chain membership in exported CTL.

off_state means inactive level, not active level. scan_enable usage all controls both FF SE and ICG.
For reset, active-high means off_state 0; active-low means off_state 1.
When ORIGINAL DRC includes uncontrolled set/reset inputs (DFTR2/DFTR3), inspect
the supplied Liberty clear/preset function and the actual upstream connection
even if the task does not state a polarity. Do not repair only the simultaneous
clock-gating failure and leave an asserted asynchronous control untouched.
For example, clear="!PIN" is deasserted at PIN=1; inversion upstream can reverse
the required PORT level. This is circuit/DRC evidence, not a name-based default.
Define each clock port once, including any associated_internal_clocks in that same
declaration. A second declaration for an already specified clock conflicts and is
discarded (SCAN-4626/SCAN-1621); it does not augment the first declaration.
associated_internal_clocks accepts actual INSTANCE/OUTPUT_PIN objects only:
The user manual (PDF p.17) states that these pins are assigned the SAME waveform
and inactive level as their source port. Do not associate an inverted waveform
to that source merely to clear DRC; a tool pass or unchanged netlist does not
justify a false clock-waveform declaration.
a net name is not a pin (SCAN-1620), and a downstream FF CLK input is not an
output pin (SCAN-1612). For an output .X(route), the pin is INSTANCE/X, not
route and not FF/CLK. A syntactically valid association still does not prove
the source-clock control requirement; never use it to mask a remaining DFTR8.
The default scan_enable off_state is 0 and default usage is all in Default_Partition.
For a generated flow with actual ICG test controls tied off, configure a global
scan_enable with usage all or clock_gating BEFORE DRC and before creating custom
partitions. The custom partitions' usage scan enables control data FFs, not those
ICGs; configuring only local enables can leave most clocks blocked. Preserve the
local partition bindings as well. A scan-mode constant does not guarantee ICG TE
is driven: synthesized wrappers may tie it to 0 independently of scan_mode.
This planning requirement is distinct from diagnosis: the official set_scan_signal
reference permits automatically generated scan_enable ports. An omitted explicit
declaration is not by itself a defect if no task port/usage requirement or actual
blocked-control evidence applies. Do not infer a fault merely because a candidate
spells the declaration explicitly. Do not switch to usage scan alone if it leaves
required ICG controls tied off.
Omitting these defaults is not a defect. Custom partitions default to scan usage.
For a functional clock-gating bypass implemented as (functional_enable OR scan_enable),
scan_enable is active HIGH and must have -off_state 0. An off_state of 1 asserts it
LOW during scan and can leave gated clocks blocked, causing widespread clock DRC
failures and loss of scannable coverage. Infer this from the circuit/task, not a port-name suffix.
scan_enable may name a NEW port not present in the pre-scan netlist. SCAN-4610 warning
that this port does not exist is expected when the tool creates it during insertion;
check the resulting scan chain report and output port declaration, not that warning alone.
Only configure actual task requirements, not every available command. Do not ignore DRC rules unless expressly permitted.

Additional full-insertion commands (manual PDF pages 28–30, 58, 69;
Ignore rule level also confirmed by the supplied development reference):
set_scan_cfg -max_length N -mix_clocks false -mix_edges true -add_lockup true
set_scan_cfg -internal_clocks none|single|multi
These are ALTERNATIVES, not a literal pipe-separated argument (manual pp. 12, 23-24).
none does not identify internal clock domains; single splits after every combinational
gate including buffers/inverters; multi splits after multi-input combinational gates.
If the original script omits this setting, retain the default none unless task
requirements or traced circuit evidence specifically require internal domains.
Do not introduce multi as a generic repair for blocked clocks. A module that only
assigns its clock output to its clock input is transparent; it does not require
associated_internal_clocks or a new pseudo source. Unnecessary clock associations
can themselves introduce capture violations (DFTR17).
Do not blindly set single: it can create many internal-clock groups, making the
requested chain_count impossible even with the correct source clocks and partitions.
set_scan_cfg -mix_internal_clocks true
This allows mixing internal clocks derived from the SAME source; -mix_clocks false
still prohibits mixing distinct source clocks. Use only when consistent with the task.
set_scan_cfg -si_port_format scan_data_in_%d -so_port_format scan_data_out_%d
The manual spells this -internal_clocks; the installed tool also accepts
-internal_clock (confirmed by actual execution). Do not diagnose that spelling as a defect.
set_scan_drc_rule_handling {DFTR-TIE0 DFTR-TIE1} Ignore
Use full DRC rule IDs, not TIE0/TIE1; Warning does NOT mean Ignore.
Only apply Ignore to rules explicitly exempted by the task.
Permission to leave DFTR10 violations does NOT request an Ignore command. The
installed tool rejects `set_scan_drc_rule_handling {DFTR10} Ignore` (DFTDRC-3006).
Leave expressly permitted residual violations visible in the DRC report; do not
guess a suffixed rule ID or suppress other violations to obtain a zero count.
rpt_scan_cfg -file "$out_rpt_dir/scan_cfg.rpt"
rpt_scan_signal -file "$out_rpt_dir/scan_signal.rpt"
rpt_insertion_info -file "$out_rpt_dir/insertion_info.rpt"
rpt_scan_element -type all -file "$out_rpt_dir/scan_element.rpt"
The latter lists all scannable and non-scannable register objects (manual p. 19),
and is needed when the task requests an inserted-netlist register report. It is
distinct from rpt_scan_chain_cell, which lists only cells on chains.
dump_ctl "$out_res_dir/post_scan.ctl"
dump_def -section scan_chain -file "$out_res_dir/post_scan.def"
Unlike dump_netlist and dump_def, dump_ctl takes a positional filename: never
write dump_ctl -file PATH. The installed dump_ctl.md documents one positional
argument; the extra -file spelling causes CMD-0074 and produces no CTL output.
The manual documents dump_def. The installed tool also accepts
dump_scan_def -file PATH (confirmed by actual exported SCANDEF). Do not diagnose
that alias as a defect merely because the manual uses a different name.
Enable lockup with -add_lockup true; this permits insertion where needed,
not a requirement that same-edge/same-clock chains contain extra lockup cells.

Partition and cascaded-clock flows (manual PDF pages 17, 44–47;
clock_gating_init_cycles syntax additionally confirmed in official development examples):
Create custom partitions ONLY when the task explicitly names partitions or
requires per-partition settings. "Do not cross clock domains" and independent
pseudo-PI clock domains are already satisfied by the default -mix_clocks false,
which splits chains by clock in Default_Partition. Observed: one custom partition
per clock on a ~600k-cell design made dump_ctl run for over an hour (stuck in CTL
scan-cell lookup) after a normal insertion, exhausting the case deadline.
add_scan_partition NAME -clocks {SOURCE_CLOCK}
Default_Partition is a PREDEFINED reserved partition containing the design's
instances, not a user partition to create. Never add/delete/redefine it.
Use set_current_scan_partition Default_Partition directly. Missing an
add_scan_partition Default_Partition command is NOT an original defect, even
when DRC failures leave a partition report empty. The installed official
add_scan_partition command reference explicitly defines this reserved name.
set_current_scan_partition NAME
set_scan_cfg -chain_count N -max_length M -mix_clocks false -mix_edges false -add_lockup true
set_scan_signal -type scan_enable -port PARTITION_SE -off_state 0 -usage scan
set_current_scan_partition Default_Partition
set_scan_signal -type scan_enable -port DEFAULT_SE -off_state 0 -usage all
rpt_scan_partition -file "$out_rpt_dir/scan_partition.rpt"
Custom partitions support usage scan only; global ICG control belongs to Default_Partition.
Set global internal-clock and lockup policy before creating custom partitions.
Source-clock partitions include associated internal clocks. Do not partition using
internal pin names when the task specifies source-clock partitions.

set_scan_drc_cfg -clock_gating_init_cycles N
N is restricted to 0, 1, 2, 3 or 4 by the installed official command reference
(/opt/dftexp_scan/share/md/cmd/set_scan_drc_cfg.md); do not extrapolate the range.
clock_gating_init_cycles belongs to set_scan_drc_cfg, NOT set_dft_clock_gating_cfg.
For cascaded ICGs use enough initialization cycles to propagate their controls.
Initialization cycles concern stateful enable propagation through latches/FFs,
not the number of purely combinational AND/OR gates. A cascade whose stages all
use a directly controllable Boolean bypass and contain no control storage opens
combinationally; omitting clock_gating_init_cycles is not a defect in that case.
Trace the provided control module before claiming extra initialization is needed.
If an incorrect bypass polarity explains the blocked clocks, fixing that polarity
is one root cause; do not also infer missing initialization from the same symptom.
set_scan_signal -type clock -port CLK -off_state LEVEL -associated_internal_clocks {LATCH/Q}
set_scan_element false {LATCH}
These two statements serve different purposes: associate the latch output clock,
and exclude the clock-path latch itself from scan. Only exclude the task-specified
clock-path latch, not the FFs it drives. Preserve the functional clock waveform;
do not assume every clock has off_state 0.
Distinguish a latch that carries the CLOCK waveform from a clock-gating CE latch:
for a gated clock buffer, the CE latch Q carries an ENABLE, not a clock. An ordinary
Liberty ICG with a connected test-control pin already propagates its source clock;
do NOT add an output-clock association merely because this pattern contains a latch
or is wrapped in a Verilog module. Such an association can create extra chain groups
even when internal_clocks is already none; setting none again does not undo it.
Only when the actual task or failed clock trace establishes that an explicit
association is needed, use the gated CLOCK OUTPUT with its actual upstream clock,
NOT the CE latch Q and NOT another similarly named domain. The official development
reference's special clock-buffer configuration uses this command shape:
set_scan_signal -type clock -port SOURCE_CLOCK -off_state 0 -associated_internal_clocks {BUFFER_INSTANCE/O}
Use the actual buffer output pin and source clock from its instance connections.
Do NOT declare the gated output as a separate source with -port unless the task
actually requires an independent pseudo-clock domain there. If the task forbids
internal gated-clock domains, use set_scan_cfg -internal_clocks none explicitly.
Do not enable Wrapper unless requested by the task and supported by available cells.
A task's maximum SI/SO channel count limits actual chain count, not just max_length.
When chain_count and max_length conflict the tool prioritizes chain_count. Therefore
successful execution or echoed max_length is not proof of actual chain lengths.
If only max_length is requested, OMIT -chain_count for both scan and wrapper
configuration; let the tool derive the number of chains. Do not invent a small count.
Check every actual chain report row. Preserve the original internal_clocks policy
unless an observed failure or explicit task requirement warrants changing it;
changing multi to single can fragment internal clock groups and alter chain lengths.

CTL / existing scan structures (manual PDF pp. 9-10, 20-21, 69):
load_ctl -module MODULE CTL_PATH
The module must be a Liberty cell (not a Verilog module); load CTL before present_design.
CTL ScanStructures are imported as existing scan chains/segments. A manually described
segment uses:
set_scan_segment NAME -access {scan_enable SE_PIN scan_data_in SI_PIN scan_data_out SO_PIN} -lockup_exists false
The segment's register order is fixed by the netlist; it cannot be supplied to add_scan_chain
or designated as a shift register. Use rpt_scan_segment to inspect imported/inferred segments.
CTL import does not support pseudo_port or hookup_pin definitions, and does not validate CTL
chain SiSo connectivity. For a CTL-backed hierarchy, dump_ctl PATH exports the integrated
model; dump_def -section scan_chain -file PATH exports SCANDEF.

Wrapper configuration (manual PDF pp. 28-33, 37-38):
set_wrapper_cfg enable -chain_count N -style dedicated
set_wrapper_cfg enable -chain_count N -style shared -reuse_threshold R -depth_threshold D
set_wrapper_cfg -chain_count N -max_length M
set_wrapper_cfg -style dedicated -port {PORTS} -dedicated_cell_type TYPE
set_wrapper_cfg -style shared -port {PORTS}
set_wrapper_cfg -style none -port {PORTS}
set_wrapper_cfg -exclude {CELL_PATHS}
set_wrapper_cfg -mix_cell true|false
Enable wrappers globally in a command WITHOUT -port, for example:
set_wrapper_cfg enable -style shared -max_length M -reuse_threshold R -depth_threshold D
Then apply port-specific overrides without the enable keyword. Combining enable
with -port is invalid (SCAN-4306) and leaves wrappers disabled.
Wrapper chain count takes priority over requested length, so verify actual lengths in
rpt_scan_chain (I = scan, W = wrapper). Dedicated wrapper cells need a wrapper clock:
set_scan_signal -type wrapper_clock -port WRCK -off_state 0
Wrapper test controls/data use set_scan_signal types wrp_in_shift_en, wrp_out_shift_en,
wrp_in_capture_en, wrp_out_capture_en, wrp_data_in, and wrp_data_out.
Wrapper wrp_data_in / wrp_data_out designate SERIAL TEST ACCESS ports (wrapper
chain SI/SO), not the functional inputs/outputs that need wrapper cells. Do not
declare a functional port as wrp_data_out merely because it must receive an output
wrapper. That reclassifies it as a DFT Signal Port and automatically excludes it
from wrapping, even with a dedicated port override. Use set_wrapper_cfg -port
for the functional port policy; let the tool create separate serial wrapper ports
unless the task explicitly names those test access ports. If the implementation
report says none / DFT Signal Port for a required functional wrapper port, inspect
and remove the incorrect test-signal declaration, not just repeat the style override.
A dedicated custom cell is registered with the complete interface mapping (manual p. 34):
add_dedicated_wrapper_cell_type -design_name MY_INPUT_WRP_CELL -interface {shift_clk subsys_clk h capture_en capture_dr h shift_en shift_dr h cti si h cto so h cfi data_in h cfo data_out h}
The interface pin names must come from the supplied cell, not from this example.
After registering it, set_wrapper_cfg uses -dedicated_cell_type user_defined,
NOT the module name (confirmed by installed tool -help).
rpt_wrapper_cfg -file "$out_rpt_dir/wrapper_cfg.rpt"
rpt_wrapper_implementation -file "$out_rpt_dir/wrapper_implementation.rpt"
rpt_scan_segment -file "$out_rpt_dir/scan_segment.rpt"
With wrappers enabled, set_scan_cfg -chain_count is the TOTAL chain budget,
including wrapper chains: request internal_count + wrapper_count there, and
wrapper_count in set_wrapper_cfg. Actual execution with total 55 / wrapper 15
produced 40 I chains and 15 W chains. Check the actual two classes separately.
Chain lengths are BIT counts; CTL segments count as one object but multiple bits.
Insertion-info scannable FF count cannot be equated to internal-chain cell count
when wrappers/segments exist. Inspect both internal and wrapper bit totals.
When required to preserve existing SFF enable wiring:
set_scan_cfg -respect_sff_se_connection true

Shift-register and segment checks (manual PDF pp. 20-21, 56-57, 59-61, 64):
set_parameter dft.scan.enable_shift_register_identification true
The installed tool requires this qualified parameter name (default true);
the unqualified name printed in the manual is rejected by this build.
examine_scan_drc
rpt_shift_register
The tool identifies same-clock, same-edge FF runs whose Q-to-D links are direct or through
buffers; a functional synchronous set/reset or a clock/edge boundary breaks a run. With
set_scan_cfg -replace true, only the first DFF of an identified shift register is replaced;
non-first DFFs retain their functional chain. CTL-defined and set_scan_segment-defined
segments, and identified shift registers, are emitted in SCANDEF ORDERED entries.
Identification alone is NOT explicit segment registration. When the task requires
long shift registers to become scan segments, and their first cell is already SFF:
after DRC, export rpt_shift_register, group rows by ShiftRegID and order by CellNo.
Select groups meeting the task's length threshold, then register each with
set_scan_segment using first SFF's SE/SI pins and last FF's Q pin, BEFORE
examine_scan_chain. Use actual library pin names (sky130: SCE, SCD, Q).
Use Tcl to process the report at runtime instead of inventing instance names or
emitting hundreds of guessed segments when registrations are not already provided.
An existing explicit list is valid; preserve it unless actual topology/task evidence
identifies a missing or incorrect endpoint. Hard-coded instance names are not in
themselves defects. Failed DRC can make identification incomplete.
Example grouping (MIN_LENGTH comes from task):
rpt_shift_register -file "$out_rpt_dir/shift_register.rpt"
set fd [open "$out_rpt_dir/shift_register.rpt" r]
set groups [dict create]
while {[gets $fd line] >= 0} {
    if {[regexp {^\s*(\S+)\s+(\d+)/(\d+)\s*$} $line -> inst id pos]} {
        dict set groups $id $pos $inst
    }
}
close $fd
dict for {id cells} $groups {
    set indices [lsort -integer [dict keys $cells]]
    if {[llength $indices] >= MIN_LENGTH} {
        set first [dict get $cells [lindex $indices 0]]
        set last [dict get $cells [lindex $indices end]]
        set_scan_segment segment_$id -access [list scan_enable $first/SCE scan_data_in $first/SCD scan_data_out $last/Q] -lockup_exists false
    }
}
Continue with examine_scan_chain, full insertion, and all requested exports.
Export rpt_scan_segment AFTER insert_dft_logic: its ChainName must describe the
delivered assignment. A report saved before chain construction has no final chain
association, even if the segment registration and inserted result are correct.

Hierarchy partitions and global ICG control (manual PDF pp. 27, 39-42, 66-70):
add_scan_partition NAME -include HIER_CELL
add_scan_partition NAME -include {HIER_CELL1 HIER_CELL2} -exclude {HIER_CELL3}
add_scan_partition NAME -clocks {SOURCE_CLK1 SOURCE_CLK2}
add_scan_partition NAME -clocks CLK1 -rising_edge_clocks CLK2
The ByClockEdge column of rpt_scan_partition describes the partition membership
selector. A value of both is expected for -clocks and does NOT mean any FF uses
both edges or that a chain mixes edges. Observed FF edges are the ClockEdges
column in rpt_scan_chain_cell: (+) or (-). Trace inverters from the actual source
clock; the tool already propagates combinational inversion with internal_clocks
none. A negative-edge domain by itself does not require associated_internal_clocks.
Do not add that association to fix a partition selector display when DRC and the
cell report already establish the correct clock relationship; it can introduce DFTR8.
add_scan_partition NAME -include HIER_CELL -force
set_current_scan_partition NAME
add_scan_partition also makes NAME current in the installed tool. Do not assume
the default/global scope remains active after partition creation. Use explicit
set_current_scan_partition Default_Partition for global port formats, internal
clock policy and ICG configuration; retain local chain counts and SE bindings.
For an all-Default_Partition design, the installed rpt_scan_partition can contain
only its header even after successful insertion. Check scan_chain/CTL membership;
do not invent a missing partition or premature report export from that empty table.
The actual dofile and command log establish report timing.
The manual documents hierarchy membership with -include/-exclude (and -force), not a
-hinstances option. Clock-based partitions accept source clocks only; an associated
internal clock is included through its source clock. Custom partition scan enables must
use -usage scan. Thus each ac97-style partition gets its own signal, for example:
set_current_scan_partition p1
set_scan_signal -type scan_enable -port SE_P1 -off_state 0 -usage scan
set_current_scan_partition p2
set_scan_signal -type scan_enable -port SE_P2 -off_state 0 -usage scan
When several partitions intentionally share ONE scan-enable port, bind that
same port under EACH required set_current_scan_partition. Naming it in one
partition does not bind the others; deleting repeated bindings can silently
restore test_se in the other partitions. Clock/reset declarations are global,
but these partition-specific scan-enable bindings must retain their scopes.
Global ICG hookup is configured separately (Default_Partition / before partition scan):
set_current_scan_partition Default_Partition
set_scan_signal -type scan_enable -port ICG_SE -off_state 0 -usage clock_gating
insert_dft_logic -connect_icg_only
Then define each partition's scan enable with -usage scan before final DRC/chain insertion.
Partition scan-only enables do not assert tied-off ICG test controls. When the
actual netlist contains such gates, preserve the separate global ICG hookup;
otherwise valid partition counts can conceal thousands of unscannable data FFs.
Use a distinct global ICG control port from the custom partition scan-only enables;
do not redeclare one port with incompatible usage roles (SCAN-1621).
The manual states set_dft_clock_gating_cfg -exclude_elements applies to hierarchy and
leaf clock-gating cells; only eligible tied-off TE controls are reconnected.
Hierarchy-based and clock-based membership ADD cells; -clocks is not an intersection
filter on -include. For functional-region partitions use -include alone, otherwise
the first partition can capture an entire shared clock domain. First membership wins
unless -force is used (manual p. 39). Do not use -force to hide a wrong region definition.
Instance paths are relative to the current design, separated by '/', not '.', and
must not be prefixed with the top module name. Use the actual supplied hierarchy.
Export rpt_scan_partition after examine_scan_chain/insert_dft_logic so the final
ByClock/ByClockEdge table exists; a pre-analysis report lists only selectors.
When loading a separate custom wrapper module and a main netlist, load both in
ONE command, main file FIRST: load_netlist [list MAIN_NETLIST CUSTOM_WRAPPER_FILE].
Do NOT use -top here: it drops the uninstantiated helper module, so subsequent
add_dedicated_wrapper_cell_type cannot find it. Separate loads or helper-first
loading can also pick the helper as top. This loading order is confirmed in the
official development reference; registration still uses the actual module name.
The helper is Verilog, NOT CTL: never pass a .v file to load_ctl.
Keep wrapper shift/capture controls distinct from the existing functional/scan
enable port. Let the tool create separate wrapper controls when the task permits
arbitrary names; reusing one port for conflicting signal types causes SCAN-1621.

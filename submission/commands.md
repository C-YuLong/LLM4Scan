# Scan command notes for first milestone

Sources: official Scan User Manual PDF pages 16–29, 32, 51–53, 62–71.
These notes are not a complete command reference.

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

off_state means inactive level, not active level. scan_enable usage all controls both FF SE and ICG.
For reset, active-high means off_state 0; active-low means off_state 1.
Define each clock port once, including any associated_internal_clocks in that same
declaration. A second declaration for an already specified clock conflicts and is
discarded (SCAN-4626/SCAN-1621); it does not augment the first declaration.
The default scan_enable off_state is 0 and default usage is all in Default_Partition.
Declare a scan_enable for a full gated-clock insertion flow even when the task
allows the tool to create a new port. A scan-mode constant does not replace this:
synthesized ICG wrappers may have test-enable pins tied to 0 independently of the
functional scan_mode net. Use usage all, or a separate clock_gating enable, to
make their scan test controls available. Do not switch to usage scan alone as a
repair for clock-capture errors when it would leave those ICG test controls tied off.
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
The manual documents dump_def. The installed tool also accepts
dump_scan_def -file PATH (confirmed by actual exported SCANDEF). Do not diagnose
that alias as a defect merely because the manual uses a different name.
Enable lockup with -add_lockup true; this permits insertion where needed,
not a requirement that same-edge/same-clock chains contain extra lockup cells.

Partition and cascaded-clock flows (manual PDF pages 17, 44–47;
clock_gating_init_cycles syntax additionally confirmed in official development examples):
add_scan_partition NAME -clocks {SOURCE_CLOCK}
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
clock_gating_init_cycles belongs to set_scan_drc_cfg, NOT set_dft_clock_gating_cfg.
For cascaded ICGs use enough initialization cycles to propagate their controls.
set_scan_signal -type clock -port CLK -off_state LEVEL -associated_internal_clocks {LATCH/Q}
set_scan_element false {LATCH}
These two statements serve different purposes: associate the latch output clock,
and exclude the clock-path latch itself from scan. Only exclude the task-specified
clock-path latch, not the FFs it drives. Preserve the functional clock waveform;
do not assume every clock has off_state 0.
Distinguish a latch that carries the CLOCK waveform from a clock-gating CE latch:
for a gated clock buffer, the CE latch Q carries an ENABLE, not a clock. Associate
the buffer's gated CLOCK OUTPUT (or enclosing hierarchy clock output) with its
actual upstream clock port, NOT the CE latch Q and NOT another similarly named
clock domain. Trace the supplied module/instance connections. This distinction is
confirmed by the official development reference's clock-buffer configuration.
For this CE-latch buffer pattern, the command shape is:
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
wrp_in_capture_en, wrp_out_capture_en, wrp_data_in, and wrp_data_out. A dedicated custom
cell is registered with the complete interface mapping (manual p. 34):
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
emitting hundreds of guessed segments. Example grouping (MIN_LENGTH comes from task):
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

Hierarchy partitions and global ICG control (manual PDF pp. 27, 39-42, 66-70):
add_scan_partition NAME -include HIER_CELL
add_scan_partition NAME -include {HIER_CELL1 HIER_CELL2} -exclude {HIER_CELL3}
add_scan_partition NAME -clocks {SOURCE_CLK1 SOURCE_CLK2}
add_scan_partition NAME -clocks CLK1 -rising_edge_clocks CLK2
add_scan_partition NAME -include HIER_CELL -force
set_current_scan_partition NAME
The manual documents hierarchy membership with -include/-exclude (and -force), not a
-hinstances option. Clock-based partitions accept source clocks only; an associated
internal clock is included through its source clock. Custom partition scan enables must
use -usage scan. Thus each ac97-style partition gets its own signal, for example:
set_current_scan_partition p1
set_scan_signal -type scan_enable -port SE_P1 -off_state 0 -usage scan
set_current_scan_partition p2
set_scan_signal -type scan_enable -port SE_P2 -off_state 0 -usage scan
Global ICG hookup is configured separately (Default_Partition / before partition scan):
set_current_scan_partition Default_Partition
set_scan_signal -type scan_enable -port ICG_SE -off_state 0 -usage clock_gating
insert_dft_logic -connect_icg_only
Then define each partition's scan enable with -usage scan before final DRC/chain insertion.
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

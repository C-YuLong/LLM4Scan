// Original small gate-level challenge: eight FFs on tick_a, four on tick_b.
// Independent data bits avoid inferred shift-register segments.
module bridge(input tick_a, input tick_b, input [11:0] payload, output [11:0] result);
  sky130_fd_sc_hd__dfxtp_1 a0 (.CLK(tick_a), .D(payload[0]), .Q(result[0]));
  sky130_fd_sc_hd__dfxtp_1 a1 (.CLK(tick_a), .D(payload[1]), .Q(result[1]));
  sky130_fd_sc_hd__dfxtp_1 a2 (.CLK(tick_a), .D(payload[2]), .Q(result[2]));
  sky130_fd_sc_hd__dfxtp_1 a3 (.CLK(tick_a), .D(payload[3]), .Q(result[3]));
  sky130_fd_sc_hd__dfxtp_1 a4 (.CLK(tick_a), .D(payload[4]), .Q(result[4]));
  sky130_fd_sc_hd__dfxtp_1 a5 (.CLK(tick_a), .D(payload[5]), .Q(result[5]));
  sky130_fd_sc_hd__dfxtp_1 a6 (.CLK(tick_a), .D(payload[6]), .Q(result[6]));
  sky130_fd_sc_hd__dfxtp_1 a7 (.CLK(tick_a), .D(payload[7]), .Q(result[7]));
  sky130_fd_sc_hd__dfxtp_1 b0 (.CLK(tick_b), .D(payload[8]), .Q(result[8]));
  sky130_fd_sc_hd__dfxtp_1 b1 (.CLK(tick_b), .D(payload[9]), .Q(result[9]));
  sky130_fd_sc_hd__dfxtp_1 b2 (.CLK(tick_b), .D(payload[10]), .Q(result[10]));
  sky130_fd_sc_hd__dfxtp_1 b3 (.CLK(tick_b), .D(payload[11]), .Q(result[11]));
endmodule

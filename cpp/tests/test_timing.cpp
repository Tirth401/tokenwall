// One test per timing rule. Expected ticks are hand-derived from the rule table
// (half-CK ticks, with Ramulator's command-length adjustments) and match the
// numbers Ramulator's own device reported in Phase 0's probe demo.
#include <string>

#include "tokenwall/device.h"
#include "tw_test.h"

using namespace tokenwall;

namespace {
const DramSpec& spec() {
  static DramSpec s = DramSpec::load(std::string(TW_SOURCE_DIR) + "/configs/hbm3/hbm3_16gb_8hi_6400.spec");
  return s;
}
AddrVecT av(int pc = 0, int sid = 0, int bg = 0, int bank = 0, int row = 0, int col = 0) {
  AddrVecT a;
  a.fill(-1);
  a[0] = 0; a[1] = pc; a[2] = sid; a[3] = bg; a[4] = bank; a[5] = row; a[6] = col;
  return a;
}
AddrVecT pc_wild(int pc) {
  AddrVecT a;
  a.fill(-1);
  a[0] = 0; a[1] = pc;
  return a;
}
Tick first_ready(const Device& d, int cmd, const AddrVecT& a, Tick start) {
  Tick t = start;
  while (!d.check_timing(cmd, a, t)) t++;
  return t;
}
std::string binding_name(const Device& d, int cmd, const AddrVecT& a, Tick t) {
  Binding b = d.binding(cmd, a, t);
  return b.constraint_id >= 0 ? spec().constraints[b.constraint_id].name : "none";
}
}  // namespace

TW_TEST(spec_loads_ramulators_resolved_tables) {
  const DramSpec& s = spec();
  TW_CHECK_EQ(s.constraints.size(), 60u);
  TW_CHECK_EQ(s.t("nRCDRD"), 62);
  TW_CHECK_EQ(s.t("nBL"), 4);
  TW_CHECK_EQ(s.read_latency, 44);
  TW_CHECK(s.tick_ps() == 312.5);
  TW_CHECK_EQ(s.command_cycles[s.C_ACT], 3);
  TW_CHECK_EQ(s.counts[s.L_ROW], 16384);
}

TW_TEST(state_first_closed_bank_needs_act) {
  Device d(spec(), 0);
  TW_CHECK_EQ(d.preq(spec().C_RD, av()), spec().C_ACT);
  TW_CHECK(d.check_timing(spec().C_RD, av(), 0));  // timing alone is fine at tick 0
  d.issue(spec().C_ACT, av(), 0);
  TW_CHECK_EQ(d.preq(spec().C_RD, av()), spec().C_RD);
  TW_CHECK_EQ(d.preq(spec().C_RD, av(0, 0, 0, 0, 1)), spec().C_PREpb);  // other row: conflict
  TW_CHECK(d.row_hit(av()) && !d.row_hit(av(0, 0, 0, 0, 1)) && d.row_open(av(0, 0, 0, 0, 1)));
}

TW_TEST(tRCD_read_act_to_rd_is_63_ticks) {
  Device d(spec(), 0);
  d.issue(spec().C_ACT, av(), 0);
  TW_CHECK_EQ(first_ready(d, spec().C_RD, av(), 0), 63);  // 31 CK + ACT is 3 ticks long - RD is 2
  TW_CHECK(binding_name(d, spec().C_RD, av(), 10) == "Bank:nRCDRD");
}

TW_TEST(tRCD_write_act_to_wr_is_31_ticks) {
  Device d(spec(), 0);
  d.issue(spec().C_ACT, av(), 0);
  TW_CHECK_EQ(first_ready(d, spec().C_WR, av(), 0), 31);
}

TW_TEST(tRAS_act_to_precharge_is_92_ticks) {
  Device d(spec(), 0);
  d.issue(spec().C_ACT, av(), 0);
  d.issue(spec().C_RD, av(), 63);
  TW_CHECK_EQ(first_ready(d, spec().C_PREpb, av(), 63), 92);  // RD+tRTP would allow 82; tRAS binds
  TW_CHECK(binding_name(d, spec().C_PREpb, av(), 85) == "Bank:nRAS");
}

TW_TEST(tRP_precharge_to_act_is_50_and_tRC_is_142) {
  Device d(spec(), 0);
  d.issue(spec().C_ACT, av(), 0);
  d.issue(spec().C_RD, av(), 63);
  d.issue(spec().C_PREpb, av(), 92);
  TW_CHECK_EQ(first_ready(d, spec().C_ACT, av(0, 0, 0, 0, 1), 92), 142);  // 92 + 50, which is also ACT + tRC
}

TW_TEST(tCCD_L_same_bank_group_is_8_ticks) {
  Device d(spec(), 0);
  d.issue(spec().C_ACT, av(0, 0, 0, 0), 0);
  TW_CHECK_EQ(first_ready(d, spec().C_ACT, av(0, 0, 0, 1), 0), 10);  // tRRD_L inside a bank group
  d.issue(spec().C_ACT, av(0, 0, 0, 1), 10);
  d.issue(spec().C_RD, av(0, 0, 0, 0), 200);
  TW_CHECK_EQ(first_ready(d, spec().C_RD, av(0, 0, 0, 0, 0, 1), 200), 208);  // same bank
  TW_CHECK_EQ(first_ready(d, spec().C_RD, av(0, 0, 0, 1), 200), 208);        // other bank, same group
  TW_CHECK(binding_name(d, spec().C_RD, av(0, 0, 0, 1), 205) == "BankGroup:nCCDL");
}

TW_TEST(tCCD_S_other_bank_group_is_4_ticks) {
  Device d(spec(), 0);
  d.issue(spec().C_ACT, av(0, 0, 0, 0), 0);
  TW_CHECK_EQ(first_ready(d, spec().C_ACT, av(0, 0, 1, 0), 0), 8);  // tRRD_S across bank groups
  d.issue(spec().C_ACT, av(0, 0, 1, 0), 8);
  d.issue(spec().C_RD, av(0, 0, 0, 0), 200);
  TW_CHECK_EQ(first_ready(d, spec().C_RD, av(0, 0, 1, 0), 200), 204);  // equals the burst: bus stays full
}

TW_TEST(tCCD_R_other_sid_is_6_ticks) {
  Device d(spec(), 0);
  d.issue(spec().C_ACT, av(0, 0, 0, 0), 0);
  d.issue(spec().C_ACT, av(0, 1, 0, 0), 8);
  d.issue(spec().C_RD, av(0, 0, 0, 0), 200);
  TW_CHECK_EQ(first_ready(d, spec().C_RD, av(0, 1, 0, 0), 200), 206);
}

TW_TEST(tFAW_fifth_activate_waits_48_ticks) {
  Device d(spec(), 0);
  Tick t = 0;
  for (int bg = 0; bg < 4; bg++) {
    t = first_ready(d, spec().C_ACT, av(0, 0, bg, 0), t);
    d.issue(spec().C_ACT, av(0, 0, bg, 0), t);
  }
  TW_CHECK_EQ(t, 24);  // spaced by tRRD_S = 8
  TW_CHECK_EQ(first_ready(d, spec().C_ACT, av(0, 1, 0, 0), t), 48);
  TW_CHECK(binding_name(d, spec().C_ACT, av(0, 1, 0, 0), 40) == "PseudoChannel:nFAW");
}

TW_TEST(write_to_read_turnaround_38_other_group_44_same_group) {
  Device d(spec(), 0);
  d.issue(spec().C_ACT, av(0, 0, 0, 0), 0);
  d.issue(spec().C_ACT, av(0, 0, 0, 1), 10);
  d.issue(spec().C_ACT, av(0, 0, 1, 0), 18);
  d.issue(spec().C_WR, av(0, 0, 0, 0), 200);
  TW_CHECK_EQ(first_ready(d, spec().C_RD, av(0, 0, 1, 0), 200), 238);  // nCWL + nBL + nWTR_S
  TW_CHECK_EQ(first_ready(d, spec().C_RD, av(0, 0, 0, 1), 200), 244);  // nCWL + nBL + nWTR_L
}

TW_TEST(read_to_write_turnaround_34_ticks) {
  Device d(spec(), 0);
  d.issue(spec().C_ACT, av(0, 0, 0, 0), 0);
  d.issue(spec().C_ACT, av(0, 0, 1, 0), 8);
  d.issue(spec().C_RD, av(0, 0, 0, 0), 200);
  TW_CHECK_EQ(first_ready(d, spec().C_WR, av(0, 0, 1, 0), 200), 234);
}

TW_TEST(tPPD_precharge_to_precharge_4_ticks) {
  Device d(spec(), 0);
  d.issue(spec().C_ACT, av(0, 0, 0, 0), 0);
  d.issue(spec().C_ACT, av(0, 0, 0, 1), 10);
  d.issue(spec().C_PREpb, av(0, 0, 0, 0), 200);
  TW_CHECK_EQ(first_ready(d, spec().C_PREpb, av(0, 0, 0, 1), 200), 204);
}

TW_TEST(row_command_bus_busy_3_ticks_after_act) {
  Device d(spec(), 0);
  d.issue(spec().C_ACT, av(0, 0, 1, 0), 0);
  d.issue(spec().C_ACT, av(0, 0, 0, 0), 200);
  TW_CHECK_EQ(first_ready(d, spec().C_PREpb, av(0, 0, 1, 0), 200), 203);
  TW_CHECK(binding_name(d, spec().C_PREpb, av(0, 0, 1, 0), 201) == "Channel:bus(ACT)");
}

TW_TEST(column_command_bus_busy_2_ticks_across_pseudo_channels) {
  Device d(spec(), 0);
  d.issue(spec().C_ACT, av(0, 0, 0, 0), 0);
  d.issue(spec().C_ACT, av(1, 0, 0, 0), 8);
  d.issue(spec().C_RD, av(0, 0, 0, 0), 200);
  TW_CHECK_EQ(first_ready(d, spec().C_RD, av(1, 0, 0, 0), 200), 202);  // other PC: no tCCD, only the shared bus
}

TW_TEST(refresh_needs_closed_banks_and_blocks_act_1118_ticks) {
  Device d(spec(), 0);
  d.issue(spec().C_ACT, av(0, 0, 0, 0), 0);
  TW_CHECK_EQ(d.preq(spec().C_REFab, pc_wild(0)), spec().C_PREab);
  TW_CHECK_EQ(first_ready(d, spec().C_PREab, pc_wild(0), 0), 92);  // tRAS at pseudo-channel scope
  d.issue(spec().C_PREab, pc_wild(0), 92);
  TW_CHECK(!d.row_open(av(0, 0, 0, 0)));
  TW_CHECK_EQ(d.preq(spec().C_REFab, pc_wild(0)), spec().C_REFab);
  TW_CHECK_EQ(first_ready(d, spec().C_REFab, pc_wild(0), 92), 144);  // tRP after PREab
  d.issue(spec().C_REFab, pc_wild(0), 144);
  TW_CHECK_EQ(first_ready(d, spec().C_ACT, av(0, 0, 0, 0), 144), 144 + 1118);
  TW_CHECK(binding_name(d, spec().C_ACT, av(0, 0, 0, 0), 500) == "PseudoChannel:nRFC");
  TW_CHECK(d.check_timing(spec().C_ACT, av(1, 0, 0, 0), 145));  // the other pseudo channel is unaffected
}

TW_TEST(disabling_tFAW_removes_only_that_rule) {
  DramSpec s2 = spec();
  TW_CHECK_EQ(s2.disable_matching({"nFAW"}), 2);
  Device d(s2, 0);
  Tick t = 0;
  for (int bg = 0; bg < 4; bg++) {
    t = first_ready(d, s2.C_ACT, av(0, 0, bg, 0), t);
    d.issue(s2.C_ACT, av(0, 0, bg, 0), t);
  }
  TW_CHECK_EQ(first_ready(d, s2.C_ACT, av(0, 1, 0, 0), t), 32);  // only tRRD_S remains
}

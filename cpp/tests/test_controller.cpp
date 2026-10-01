// Small end-to-end scenarios through the controller: expected command ticks are
// hand-derived from the rules plus HBM3's edge rules (column commands only on
// rising edges = odd ticks, non-PRE row commands likewise).
#include <string>

#include "tokenwall/system.h"
#include "tw_test.h"

using namespace tokenwall;

namespace {
DramSpec load_spec() {
  return DramSpec::load(std::string(TW_SOURCE_DIR) + "/configs/hbm3/hbm3_16gb_8hi_6400.spec");
}
MemReq req(int type, int pc, int sid, int bg, int bank, int row, int col, uint64_t addr) {
  MemReq r;
  r.av.fill(-1);
  r.av[0] = 0; r.av[1] = pc; r.av[2] = sid; r.av[3] = bg; r.av[4] = bank; r.av[5] = row; r.av[6] = col;
  r.type = type;
  r.addr = addr;
  return r;
}
ControllerConfig cfg(bool refresh) {
  ControllerConfig c;
  c.refresh_allbank = refresh;
  c.record_cmds = true;
  return c;
}
void run(MemorySystem& m, int ticks) {
  for (int i = 0; i < ticks; i++) m.tick();
}
bool has(const std::vector<IssuedCmd>& v, Tick clk, int cmd) {
  for (const auto& c : v)
    if (c.clk == clk && c.cmd == cmd) return true;
  return false;
}
}  // namespace

TW_TEST(single_read_act_at_1_rd_at_65_departs_109) {
  DramSpec s = load_spec();
  MemorySystem m(s, 1, cfg(false));
  MemReq r = req(0, 0, 0, 0, 0, 0, 0, 0);
  TW_CHECK(m.send(r));
  run(m, 120);
  const auto& c = *m.controllers()[0];
  TW_CHECK_EQ(c.issued().size(), 2u);
  TW_CHECK(has(c.issued(), 1, s.C_ACT));
  TW_CHECK(has(c.issued(), 65, s.C_RD));  // ready at 64, but 64 is a falling edge
  TW_CHECK_EQ(c.stats().num_read_served, 1u);
  TW_CHECK_EQ(c.stats().read_latency_sum, 109u);  // arrive 0, depart 65 + 44
  TW_CHECK_EQ(c.stats().row_misses, 1u);
  TW_CHECK(c.idle());
}

TW_TEST(four_row_hits_same_bank_spaced_tCCD_L) {
  DramSpec s = load_spec();
  MemorySystem m(s, 1, cfg(false));
  for (int col = 0; col < 4; col++) {
    MemReq r = req(0, 0, 0, 0, 0, 0, col, 32 * col);
    TW_CHECK(m.send(r));
  }
  run(m, 200);
  const auto& c = *m.controllers()[0];
  TW_CHECK(has(c.issued(), 1, s.C_ACT));
  TW_CHECK(has(c.issued(), 65, s.C_RD));
  TW_CHECK(has(c.issued(), 73, s.C_RD));
  TW_CHECK(has(c.issued(), 81, s.C_RD));
  TW_CHECK(has(c.issued(), 89, s.C_RD));
  TW_CHECK_EQ(c.stats().row_hits, 3u);
  TW_CHECK_EQ(c.stats().row_misses, 1u);
}

TW_TEST(row_conflict_precharges_at_tRAS_then_reactivates) {
  DramSpec s = load_spec();
  MemorySystem m(s, 1, cfg(false));
  MemReq a = req(0, 0, 0, 0, 0, 0, 0, 0);
  MemReq b = req(0, 0, 0, 0, 0, 1, 0, 1 << 20);
  TW_CHECK(m.send(a) && m.send(b));
  run(m, 300);
  const auto& c = *m.controllers()[0];
  TW_CHECK(has(c.issued(), 1, s.C_ACT));
  TW_CHECK(has(c.issued(), 65, s.C_RD));
  TW_CHECK(has(c.issued(), 93, s.C_PREpb));  // ACT + tRAS (92), rising edge
  TW_CHECK(has(c.issued(), 143, s.C_ACT));   // + tRP (50)
  TW_CHECK(has(c.issued(), 207, s.C_RD));    // 143 + 63 = 206 is a falling edge
  TW_CHECK_EQ(c.stats().row_misses, 1u);
  TW_CHECK_EQ(c.stats().row_conflicts, 1u);
}

TW_TEST(two_bank_groups_overlap_their_activates) {
  DramSpec s = load_spec();
  MemorySystem m(s, 1, cfg(false));
  MemReq a = req(0, 0, 0, 0, 0, 0, 0, 0);
  MemReq b = req(0, 0, 0, 1, 0, 0, 0, 4096);
  TW_CHECK(m.send(a) && m.send(b));
  run(m, 150);
  const auto& c = *m.controllers()[0];
  TW_CHECK(has(c.issued(), 1, s.C_ACT));
  TW_CHECK(has(c.issued(), 9, s.C_ACT));   // tRRD_S = 8 ticks later
  TW_CHECK(has(c.issued(), 65, s.C_RD));
  TW_CHECK(has(c.issued(), 73, s.C_RD));   // ready at 72 (9 + 63), rising edge 73
}

TW_TEST(write_then_read_hit_waits_for_tWTR_L) {
  DramSpec s = load_spec();
  MemorySystem m(s, 1, cfg(false));
  MemReq w = req(1, 0, 0, 0, 0, 0, 0, 0);
  TW_CHECK(m.send(w));
  run(m, 40);
  const auto& c = *m.controllers()[0];
  TW_CHECK(has(c.issued(), 1, s.C_ACT));
  TW_CHECK(has(c.issued(), 33, s.C_WR));  // ready at 32, rising edge 33
  MemReq r = req(0, 0, 0, 0, 0, 0, 1, 32);
  TW_CHECK(m.send(r));
  run(m, 100);
  TW_CHECK(has(c.issued(), 77, s.C_RD));  // 33 + (nCWL + nBL + nWTR_L = 44)
  TW_CHECK_EQ(c.stats().num_write_served, 1u);
  TW_CHECK_EQ(c.stats().row_hits, 1u);
}

TW_TEST(all_bank_refresh_closes_rows_and_blocks_activates) {
  DramSpec s = load_spec();
  s.timing[s.timing_index("nREFI")] = 1400;  // one refresh inside the window (tRFC is 1118)
  MemorySystem m(s, 1, cfg(true));
  MemReq a = req(0, 0, 0, 0, 0, 0, 0, 0);
  TW_CHECK(m.send(a));
  run(m, 1500);
  const auto& c = *m.controllers()[0];
  TW_CHECK(has(c.issued(), 1400, s.C_PREab));  // pseudo channel 0 has an open row; PRE may use a falling edge
  TW_CHECK(has(c.issued(), 1453, s.C_REFab));  // + tRP (52) -> 1452 is a falling edge
  TW_CHECK(has(c.issued(), 1455, s.C_REFab));  // pseudo channel 1, queued behind the first
  MemReq b = req(0, 0, 0, 0, 0, 0, 1, 32);
  TW_CHECK(m.send(b));
  run(m, 1200);
  TW_CHECK(has(c.issued(), 2571, s.C_ACT));  // 1453 + tRFC (1118)
  TW_CHECK(has(c.issued(), 2635, s.C_RD));   // 2571 + 63 = 2634 is a falling edge
  TW_CHECK_EQ(c.stats().num_maint_served, 2u);
  TW_CHECK_EQ(c.stats().row_misses, 2u);     // the second read finds its row closed by refresh
}

TW_TEST(sixteen_channels_run_independently) {
  DramSpec s = load_spec();
  MemorySystem m(s, 16, cfg(false));
  for (int ch = 0; ch < 16; ch++) {
    MemReq r = req(0, 0, 0, 0, 0, 0, 0, 32 * ch);
    r.av[0] = ch;
    TW_CHECK(m.send(r));
  }
  run(m, 120);
  for (const auto& c : m.controllers()) {
    TW_CHECK_EQ(c->stats().num_read_served, 1u);
    TW_CHECK(has(c->issued(), 65, s.C_RD));
  }
}

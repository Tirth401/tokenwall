// DRAM timing specification loaded from a .spec file written by
// scripts/export_dram_spec.py (Ramulator 2.1's resolved runtime tables).
//
// Plain version: a list of promises of the form "after command A at this
// scope, command B may not start for N ticks". Technical version: constraint
// entries (level, preceding commands, following commands, latency in half-CK
// ticks, rolling window, sibling flag, shared-window group) plus command bus
// occupancy, exactly as Ramulator expands them.
#pragma once

#include <array>
#include <cstdint>
#include <istream>
#include <string>
#include <unordered_map>
#include <vector>

namespace tokenwall {

using Tick = int64_t;
using AddrVecT = std::array<int, 8>;  // Ramulator order: channel, pc, sid, bg, bank, row, column; -1 = wildcard

struct Constraint {
  int id = -1;
  int level = -1;
  Tick latency = 0;
  int window = 1;
  bool sibling = false;
  int group = -1;  // shared rolling-window history group, -1 = none
  std::string name;  // "Level:expression", used for stall attribution
  std::vector<int> preceding;
  std::vector<int> following;
};

struct DramSpec {
  std::string standard;
  int tick_ps_numerator = 625;
  int tick_multiplier = 2;
  std::vector<std::string> levels, commands, timing_names, row_commands, column_commands;
  std::vector<int> counts;          // per level; counts[0] (channel) is 1
  std::vector<int> command_cycles;  // command-bus occupancy in ticks
  std::vector<Tick> timing;         // ticks, indexed like timing_names
  Tick read_latency = 0;
  int tx_bytes = 32;
  std::vector<Constraint> constraints;
  std::vector<char> enabled;  // per constraint id; disable for ablation studies

  // ---- derived (built by finalize()) ----
  int level_count = 0, command_count = 0;
  int L_CH = -1, L_PC = -1, L_SID = -1, L_BG = -1, L_BANK = -1, L_ROW = -1, L_COL = -1;
  int C_ACT = -1, C_PREpb = -1, C_PREab = -1, C_RD = -1, C_WR = -1, C_RDA = -1, C_WRA = -1, C_REFab = -1,
      C_REFpb = -1;
  struct Entry {
    int cmd;  // following command
    Tick val;
    int window;
    bool sibling;
    int group;
    int id;  // constraint id
  };
  std::vector<std::vector<std::vector<Entry>>> cons;  // [level][preceding command]
  std::vector<std::vector<char>> has_sibling;          // [level][command]
  std::vector<char> is_row_cmd, is_col_cmd, is_opening, is_closing, is_accessing, is_refreshing, targets_all;

  double tick_ps() const { return double(tick_ps_numerator) / tick_multiplier; }
  int level_id(const std::string& n) const;
  int command_id(const std::string& n) const;  // -1 if absent
  int timing_index(const std::string& n) const;
  Tick t(const std::string& n) const { return timing[timing_index(n)]; }

  static DramSpec parse(std::istream& in);
  static DramSpec load(const std::string& path);
  // Disable every constraint whose name contains one of the substrings (e.g. "nFAW").
  int disable_matching(const std::vector<std::string>& substrings);
  void finalize();
};

}  // namespace tokenwall

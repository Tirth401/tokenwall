#include "tokenwall/dram_spec.h"

#include <algorithm>
#include <fstream>
#include <sstream>
#include <stdexcept>

namespace tokenwall {

namespace {
int index_of(const std::vector<std::string>& v, const std::string& n) {
  auto it = std::find(v.begin(), v.end(), n);
  return it == v.end() ? -1 : int(it - v.begin());
}
}  // namespace

int DramSpec::level_id(const std::string& n) const {
  int i = index_of(levels, n);
  if (i < 0) throw std::runtime_error("unknown level " + n);
  return i;
}
int DramSpec::command_id(const std::string& n) const { return index_of(commands, n); }
int DramSpec::timing_index(const std::string& n) const {
  int i = index_of(timing_names, n);
  if (i < 0) throw std::runtime_error("unknown timing " + n);
  return i;
}

DramSpec DramSpec::parse(std::istream& in) {
  DramSpec s;
  std::string line;
  if (!std::getline(in, line) || line.rfind("tokenwall-dramspec 1", 0) != 0)
    throw std::runtime_error("not a tokenwall-dramspec v1 file");
  while (std::getline(in, line)) {
    if (line.empty() || line[0] == '#') continue;
    std::istringstream ls(line);
    std::string key;
    ls >> key;
    auto rest = [&]() {
      std::vector<std::string> v;
      std::string tok;
      while (ls >> tok) v.push_back(tok);
      return v;
    };
    if (key == "standard") ls >> s.standard;
    else if (key == "tick_ps_numerator") ls >> s.tick_ps_numerator;
    else if (key == "tick_multiplier") ls >> s.tick_multiplier;
    else if (key == "levels") s.levels = rest();
    else if (key == "commands") s.commands = rest();
    else if (key == "row_commands") s.row_commands = rest();
    else if (key == "column_commands") s.column_commands = rest();
    else if (key == "counts") { for (auto& x : rest()) s.counts.push_back(std::stoi(x)); }
    else if (key == "command_cycles") { for (auto& x : rest()) s.command_cycles.push_back(std::stoi(x)); }
    else if (key == "read_latency") ls >> s.read_latency;
    else if (key == "tx_bytes") ls >> s.tx_bytes;
    else if (key == "timing") {
      std::string name; Tick v;
      ls >> name >> v;
      s.timing_names.push_back(name);
      s.timing.push_back(v);
    } else if (key == "constraint") {
      Constraint c;
      int sib = 0;
      ls >> c.id >> c.level >> c.latency >> c.window >> sib >> c.group;
      c.sibling = sib != 0;
      std::string tok;
      int mode = 0;  // 0: reading the name (may be several tokens), 1: P list, 2: F list
      while (ls >> tok) {
        if (tok == "P") mode = 1;
        else if (tok == "F") mode = 2;
        else if (mode == 0) c.name += tok;
        else if (mode == 1) c.preceding.push_back(std::stoi(tok));
        else c.following.push_back(std::stoi(tok));
      }
      if (c.preceding.empty() || c.following.empty()) throw std::runtime_error("constraint without P/F lists: " + line);
      s.constraints.push_back(c);
    } else {
      throw std::runtime_error("unknown spec key " + key);
    }
  }
  if (s.levels.empty() || s.commands.empty() || s.counts.size() != s.levels.size())
    throw std::runtime_error("incomplete spec");
  s.enabled.assign(s.constraints.size(), 1);
  s.finalize();
  return s;
}

DramSpec DramSpec::load(const std::string& path) {
  std::ifstream f(path);
  if (!f) throw std::runtime_error("cannot open spec " + path);
  return parse(f);
}

int DramSpec::disable_matching(const std::vector<std::string>& subs) {
  int n = 0;
  for (auto& c : constraints) {
    for (auto& sub : subs) {
      if (!sub.empty() && c.name.find(sub) != std::string::npos && enabled[c.id]) {
        enabled[c.id] = 0;
        n++;
        break;
      }
    }
  }
  finalize();
  return n;
}

void DramSpec::finalize() {
  level_count = int(levels.size());
  command_count = int(commands.size());
  L_CH = level_id("Channel");
  L_PC = level_id("PseudoChannel");
  L_SID = level_id("Sid");
  L_BG = level_id("BankGroup");
  L_BANK = level_id("Bank");
  L_ROW = level_id("Row");
  L_COL = level_id("Column");
  C_ACT = command_id("ACT");
  C_PREpb = command_id("PREpb");
  C_PREab = command_id("PREab");
  C_RD = command_id("RD");
  C_WR = command_id("WR");
  C_RDA = command_id("RDA");
  C_WRA = command_id("WRA");
  C_REFab = command_id("REFab");
  C_REFpb = command_id("REFpb");
  if (C_ACT < 0 || C_PREpb < 0 || C_PREab < 0 || C_RD < 0 || C_WR < 0 || C_REFab < 0)
    throw std::runtime_error("spec lacks a required command");
  if (int(command_cycles.size()) != command_count) command_cycles.assign(command_count, 1);

  cons.assign(level_count, std::vector<std::vector<Entry>>(command_count));
  has_sibling.assign(level_count, std::vector<char>(command_count, 0));
  for (const auto& c : constraints) {
    if (!enabled[c.id]) continue;
    for (int p : c.preceding)
      for (int f : c.following) {
        cons[c.level][p].push_back({f, c.latency, c.window, c.sibling, c.group, c.id});
        if (c.sibling) has_sibling[c.level][p] = 1;
      }
  }
  auto flag = [&](const std::vector<std::string>& names) {
    std::vector<char> v(command_count, 0);
    for (auto& n : names) {
      int i = command_id(n);
      if (i >= 0) v[i] = 1;
    }
    return v;
  };
  is_row_cmd = flag(row_commands);
  is_col_cmd = flag(column_commands);
  is_opening = flag({"ACT"});
  is_closing = flag({"PREpb", "PREab", "RDA", "WRA"});
  is_accessing = flag({"RD", "WR", "RDA", "WRA"});
  is_refreshing = flag({"REFab", "REFpb", "RFMab", "RFMpb"});
  targets_all = flag({"PREab", "REFab", "RFMab"});
}

}  // namespace tokenwall

// One HBM3 channel: the timing tree (channel > pseudo channel > SID > bank
// group > bank) and the bank state machine. Mirrors Ramulator 2.1's
// DRAMNode/DRAMDevice so Phase 4 can diff the two cycle by cycle.
#pragma once

#include <deque>
#include <memory>
#include <vector>

#include "tokenwall/dram_spec.h"

namespace tokenwall {

struct Node {
  int level = 0;
  int id = 0;
  Node* parent = nullptr;
  std::vector<std::unique_ptr<Node>> children;
  std::vector<Tick> ready;      // per command: earliest tick it may issue here (-1 = unconstrained)
  std::vector<int> ready_src;   // constraint id that set `ready`
  std::vector<std::deque<Tick>> history;  // per command, most recent first, sized to the largest window
  std::vector<std::deque<Tick>> shared;   // shared rolling windows per group
  // bank state (meaningful at the bank level only)
  bool opened = false;
  int open_row = -1;
};

struct Binding {
  int constraint_id = -1;
  int level = -1;
  Tick ready = -1;
};

class Device {
 public:
  Device(const DramSpec& spec, int channel_id);

  bool check_timing(int cmd, const AddrVecT& av, Tick clk) const;
  Binding binding(int cmd, const AddrVecT& av, Tick clk) const;  // the latest-expiring promise blocking cmd
  int preq(int final_cmd, const AddrVecT& av) const;              // command that must happen next
  void issue(int cmd, const AddrVecT& av, Tick clk);              // update timing, then apply state change
  bool row_hit(const AddrVecT& av) const;
  bool row_open(const AddrVecT& av) const;
  int flat_bank(const AddrVecT& av) const;
  bool bank_matches(const Node* bank, const AddrVecT& av) const;
  const std::vector<Node*>& banks() const { return banks_; }
  const DramSpec& spec() const { return spec_; }
  int channel_id() const { return root_->id; }

 private:
  void build(Node* n);
  void update_node(Node* n, int cmd, const AddrVecT& av, Tick clk);
  bool check_node(const Node* n, int cmd, const AddrVecT& av, Tick clk) const;
  void binding_node(const Node* n, int cmd, const AddrVecT& av, Tick clk, Binding& out) const;
  void apply_action(int cmd, const AddrVecT& av);

  const DramSpec& spec_;
  std::unique_ptr<Node> root_;
  std::vector<Node*> banks_;
};

}  // namespace tokenwall

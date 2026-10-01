#include "tokenwall/device.h"

#include <algorithm>
#include <stdexcept>

namespace tokenwall {

Device::Device(const DramSpec& spec, int channel_id) : spec_(spec) {
  root_ = std::make_unique<Node>();
  root_->level = 0;
  root_->id = channel_id;
  build(root_.get());
}

void Device::build(Node* n) {
  const int C = spec_.command_count;
  n->ready.assign(C, -1);
  n->ready_src.assign(C, -1);
  n->history.assign(C, {});
  int groups = 0;
  for (int cmd = 0; cmd < C; cmd++) {
    int window = 0;
    for (const auto& e : spec_.cons[n->level][cmd]) {
      window = std::max(window, e.window);
      if (e.group >= 0) groups = std::max(groups, e.group + 1);
    }
    if (window) n->history[cmd].assign(window, -1);
  }
  n->shared.assign(groups, {});
  for (int cmd = 0; cmd < C; cmd++)
    for (const auto& e : spec_.cons[n->level][cmd])
      if (e.group >= 0 && int(n->shared[e.group].size()) < e.window) n->shared[e.group].assign(e.window, -1);

  const int next = n->level + 1;
  if (next == spec_.L_ROW) {  // the tree stops above the row level
    banks_.push_back(n);
    return;
  }
  for (int i = 0; i < spec_.counts[next]; i++) {
    auto c = std::make_unique<Node>();
    c->level = next;
    c->id = i;
    c->parent = n;
    build(c.get());
    n->children.push_back(std::move(c));
  }
}

void Device::update_node(Node* n, int cmd, const AddrVecT& av, Tick clk) {
  const int lvl = n->level;
  if (n->id != av[lvl] && av[lvl] != -1) {  // sibling of the addressed node
    for (const auto& e : spec_.cons[lvl][cmd]) {
      if (!e.sibling) continue;
      const Tick f = clk + e.val;
      if (f > n->ready[e.cmd]) {
        n->ready[e.cmd] = f;
        n->ready_src[e.cmd] = e.id;
      }
    }
    return;
  }
  if (!n->history[cmd].empty()) {
    n->history[cmd].pop_back();
    n->history[cmd].push_front(clk);
  }
  std::vector<char> updated(n->shared.size(), 0);
  for (const auto& e : spec_.cons[lvl][cmd]) {
    if (e.group < 0 || updated[e.group]) continue;
    auto& h = n->shared[e.group];
    h.pop_back();
    h.push_front(clk + spec_.command_cycles[cmd] - 1);  // completion tick of this command
    updated[e.group] = 1;
  }
  for (const auto& e : spec_.cons[lvl][cmd]) {
    if (e.sibling) continue;
    const Tick past = e.group >= 0 ? n->shared[e.group][e.window - 1] : n->history[cmd][e.window - 1];
    if (past < 0) continue;
    const Tick f = past + e.val;
    if (f > n->ready[e.cmd]) {
      n->ready[e.cmd] = f;
      n->ready_src[e.cmd] = e.id;
    }
  }
  if (n->children.empty()) return;
  const int cl = lvl + 1;
  const int target = av[cl];
  if (spec_.has_sibling[cl][cmd] || target == -1) {
    for (auto& c : n->children) update_node(c.get(), cmd, av, clk);
  } else {
    update_node(n->children[target].get(), cmd, av, clk);
  }
}

bool Device::check_node(const Node* n, int cmd, const AddrVecT& av, Tick clk) const {
  if (n->ready[cmd] != -1 && clk < n->ready[cmd]) return false;
  if (n->children.empty()) return true;
  const int child = av[n->level + 1];
  if (child == -1) {
    for (const auto& c : n->children)
      if (!check_node(c.get(), cmd, av, clk)) return false;
    return true;
  }
  return check_node(n->children[child].get(), cmd, av, clk);
}

void Device::binding_node(const Node* n, int cmd, const AddrVecT& av, Tick clk, Binding& out) const {
  if (n->ready[cmd] != -1 && clk < n->ready[cmd] && n->ready[cmd] > out.ready) {
    out = {n->ready_src[cmd], n->level, n->ready[cmd]};
  }
  if (n->children.empty()) return;
  const int child = av[n->level + 1];
  if (child == -1) {
    for (const auto& c : n->children) binding_node(c.get(), cmd, av, clk, out);
  } else {
    binding_node(n->children[child].get(), cmd, av, clk, out);
  }
}

bool Device::check_timing(int cmd, const AddrVecT& av, Tick clk) const { return check_node(root_.get(), cmd, av, clk); }

Binding Device::binding(int cmd, const AddrVecT& av, Tick clk) const {
  Binding b;
  binding_node(root_.get(), cmd, av, clk, b);
  return b;
}

int Device::flat_bank(const AddrVecT& av) const {
  int id = 0;
  for (int lvl = 1; lvl <= spec_.L_BANK; lvl++) id = id * spec_.counts[lvl] + av[lvl];
  return id;
}

bool Device::bank_matches(const Node* bank, const AddrVecT& av) const {
  for (const Node* n = bank; n != nullptr; n = n->parent)
    if (av[n->level] != -1 && av[n->level] != n->id) return false;
  return true;
}

int Device::preq(int final_cmd, const AddrVecT& av) const {
  const DramSpec& s = spec_;
  if (final_cmd == s.C_RD || final_cmd == s.C_WR || final_cmd == s.C_RDA || final_cmd == s.C_WRA || final_cmd == s.C_ACT) {
    const Node* b = banks_[flat_bank(av)];
    if (!b->opened) return s.C_ACT;
    return b->open_row == av[s.L_ROW] ? final_cmd : s.C_PREpb;
  }
  if (final_cmd == s.C_PREpb) return s.C_PREpb;
  if (final_cmd == s.C_REFab) {
    for (const Node* b : banks_)
      if (bank_matches(b, av) && b->opened) return s.C_PREab;
    return s.C_REFab;
  }
  if (final_cmd == s.C_REFpb) {
    const Node* b = banks_[flat_bank(av)];
    return b->opened ? s.C_PREpb : s.C_REFpb;
  }
  return final_cmd;  // PREab and friends have no prerequisite
}

bool Device::row_hit(const AddrVecT& av) const {
  const Node* b = banks_[flat_bank(av)];
  return b->opened && b->open_row == av[spec_.L_ROW];
}

bool Device::row_open(const AddrVecT& av) const { return banks_[flat_bank(av)]->opened; }

void Device::apply_action(int cmd, const AddrVecT& av) {
  const DramSpec& s = spec_;
  if (cmd == s.C_ACT) {
    Node* b = banks_[flat_bank(av)];
    b->opened = true;
    b->open_row = av[s.L_ROW];
  } else if (cmd == s.C_PREpb || cmd == s.C_RDA || cmd == s.C_WRA) {
    Node* b = banks_[flat_bank(av)];
    b->opened = false;
    b->open_row = -1;
  } else if (s.targets_all[cmd] && s.is_closing[cmd]) {  // PREab
    for (Node* b : banks_)
      if (bank_matches(b, av)) {
        b->opened = false;
        b->open_row = -1;
      }
  }
}

void Device::issue(int cmd, const AddrVecT& av, Tick clk) {
  update_node(root_.get(), cmd, av, clk);
  apply_action(cmd, av);
}

}  // namespace tokenwall

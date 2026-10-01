#include "tokenwall/controller.h"

#include <algorithm>
#include <stdexcept>

namespace tokenwall {

Controller::Controller(const DramSpec& spec, int channel_id, const ControllerConfig& cfg)
    : spec_(spec), cfg_(cfg), dev_(spec, channel_id), channel_id_(channel_id) {
  read_.max_size = cfg.read_buffer;
  write_.max_size = cfg.write_buffer;
  priority_.max_size = cfg.priority_buffer;
  active_.max_size = dev_.banks().size();
  active_per_bank_.assign(dev_.banks().size(), 0);
  st_.cmd_count.assign(spec.command_count, 0);
  nREFI_ = spec.t("nREFI");
  nBL_ = spec.t("nBL");
  next_refresh_ = cfg.refresh == Refresh::AllBank ? nREFI_ : -1;
  nREFIpb_ = spec.t("nREFIpb");
  nRFCpb_ = spec.t("nRFCpb");
  banks_per_sid_ = spec.counts[spec.L_BG] * spec.counts[spec.L_BANK];
  ref_sets_.assign(spec.counts[spec.L_PC], std::vector<RefSet>(spec.counts[spec.L_SID]));
  for (auto& pcs : ref_sets_)
    for (auto& set : pcs) set.refreshed.assign(banks_per_sid_, 0);
  next_refresh_pb_ = cfg.refresh == Refresh::PerBank ? nREFIpb_ : -1;
  last_col_issue_.assign(spec.counts[spec.L_PC], -1000000);
}

// ---- request intake (mirrors ControllerBase::send) ----
bool Controller::send(MemReq& req) {
  req.av[0] = channel_id_;
  req.final_command = req.type == 1 ? spec_.C_WR : spec_.C_RD;
  if (req.type == 0 && req.addr != ~0ull && buffered_write_addrs_.count(req.addr)) {
    req.arrive = clk_;
    req.depart = clk_ + 1;
    pending_.push_back(req);
    st_.num_read_reqs++;
    st_.read_forwarded++;
    return true;
  }
  req.arrive = clk_;
  bool ok = false;
  if (req.type == 0) {
    ok = read_.enqueue(req);
  } else if (req.type == 1) {
    if (req.addr != ~0ull && buffered_write_addrs_.count(req.addr)) {
      st_.num_write_reqs++;
      st_.write_coalesced++;
      return true;
    }
    ok = write_.enqueue(req);
    if (ok && req.addr != ~0ull) buffered_write_addrs_[req.addr]++;
  } else {
    throw std::runtime_error("send() only takes reads and writes");
  }
  if (!ok) {
    req.arrive = -1;
    return false;
  }
  if (req.type == 0) st_.num_read_reqs++;
  else st_.num_write_reqs++;
  return true;
}

bool Controller::priority_send(MemReq& req) {
  bool ok = priority_.enqueue(req);
  if (ok && req.type == -1) st_.num_maint_reqs++;
  return ok;
}

bool Controller::idle() const {
  return pending_.empty() && active_.empty() && priority_.empty() && read_.empty() && write_.empty();
}

// ---- tick (mirrors HBM34Controller::tick) ----
void Controller::tick() {
  clk_++;
  st_.cycles++;
  st_.queue_len_sum += read_.size() + write_.size() + priority_.size();
  st_.read_q_sum += read_.size();
  st_.write_q_sum += write_.size();
  st_.prio_q_sum += priority_.size();
  serve_completed_reads();
  refresh_tick();

  rising_column_pc_ = -1;
  if (rising()) {
    if (auto issued = try_issue_slot(Slot::Column)) rising_column_pc_ = issued->av[spec_.L_PC];
  }
  if (auto issued = try_issue_slot(Slot::Row)) {
    if (rising()) {
      rising_row_.cmd = issued->cmd;
      rising_row_.pc = issued->av[spec_.L_PC];
      rising_row_.bank_key = is_all_bank_row(issued->cmd) ? -1 : bank_key(issued->av);
      rising_row_.next_pairing_falling_edge = issued->clk + (issued->cmd == spec_.C_ACT ? 3 : 1);
    }
  }
  if (cfg_.attribute && rising()) attribute_slots();
}

void Controller::serve_completed_reads() {
  while (!pending_.empty()) {
    const MemReq& r = pending_.front();
    if (r.depart > clk_) break;
    st_.read_latency_sum += uint64_t(r.depart - r.arrive);
    pending_.pop_front();
  }
}

void Controller::refresh_tick() {
  if (cfg_.refresh == Refresh::AllBank) refresh_tick_allbank();
  else if (cfg_.refresh == Refresh::PerBank) refresh_tick_perbank();
}

// One REFpb per pseudo channel every tREFIpb, walking the banks of one SID in order,
// then the next SID; a SID's set may not restart until tRFCpb after it completed.
bool Controller::seed_pending_refpbs() {
  for (int pc = 0; pc < spec_.counts[spec_.L_PC]; pc++)
    if (clk_ < ref_sets_[pc][ref_next_sid_].next_set_allowed) return false;
  for (int pc = 0; pc < spec_.counts[spec_.L_PC]; pc++) pending_refpb_.push_back({pc, ref_next_sid_, ref_next_flat_});
  if (++ref_next_flat_ == banks_per_sid_) {
    ref_next_flat_ = 0;
    ref_next_sid_ = (ref_next_sid_ + 1) % spec_.counts[spec_.L_SID];
  }
  return true;
}

bool Controller::service_pending_refpb() {
  if (pending_refpb_.empty()) return false;
  const auto [pc, sid, flat] = pending_refpb_.front();
  MemReq r;
  r.av.fill(-1);
  r.av[0] = channel_id_;
  r.av[spec_.L_PC] = pc;
  r.av[spec_.L_SID] = sid;
  r.av[spec_.L_BG] = flat / spec_.counts[spec_.L_BANK];
  r.av[spec_.L_BANK] = flat % spec_.counts[spec_.L_BANK];
  r.type = -1;
  r.final_command = spec_.C_REFpb;
  r.arrive = clk_;
  if (!priority_send(r)) return true;  // buffer full: retry next tick
  RefSet& set = ref_sets_[pc][sid];
  set.refreshed[flat] = 1;
  pending_refpb_.pop_front();
  bool all = true;
  for (char f : set.refreshed) all = all && f;
  if (all) {
    std::fill(set.refreshed.begin(), set.refreshed.end(), 0);
    set.next_set_allowed = clk_ + nRFCpb_;
  }
  return true;
}

void Controller::refresh_tick_perbank() {
  if (service_pending_refpb()) return;
  if (clk_ < next_refresh_pb_) return;
  if (!seed_pending_refpbs()) {
    next_refresh_pb_ = clk_ + 1;
    return;
  }
  service_pending_refpb();
  next_refresh_pb_ += nREFIpb_;
}

void Controller::refresh_tick_allbank() {
  if (next_refresh_ < 0 || clk_ != next_refresh_) return;
  next_refresh_ += nREFI_;
  for (int pc = 0; pc < spec_.counts[spec_.L_PC]; pc++) {
    MemReq r;
    r.av.fill(-1);
    r.av[0] = channel_id_;
    r.av[spec_.L_PC] = pc;
    r.type = -1;
    r.final_command = spec_.C_REFab;
    r.arrive = clk_;
    if (!priority_send(r)) throw std::runtime_error("priority buffer full on refresh");
  }
}

// ---- scheduling helpers (mirror ControllerBase) ----
Controller::Candidate Controller::pick_best_ready_from(ReqBuffer& buffer, const Filter& filter) {
  Candidate c;
  if (buffer.empty()) return c;
  auto cand = buffer.q.end();
  bool cand_ok = false;
  for (auto it = buffer.q.begin(); it != buffer.q.end(); ++it) {
    it->command = dev_.preq(it->final_command, it->av);
    if (filter && !filter(*it)) continue;
    if (cand == buffer.q.end()) {
      cand = it;
      cand_ok = dev_.check_timing(it->command, it->av, clk_);
      continue;
    }
    const bool ok = dev_.check_timing(it->command, it->av, clk_);
    if (cand_ok != ok) {
      if (ok) {
        cand = it;
        cand_ok = true;
      }
    } else if (it->arrive < cand->arrive) {
      cand = it;
    }
  }
  if (cand == buffer.q.end()) return c;
  if (!dev_.check_timing(cand->command, cand->av, clk_)) return c;
  c.valid = true;
  c.it = cand;
  c.buffer = &buffer;
  return c;
}

Controller::Candidate Controller::pick_priority_if(const Filter& filter) {
  Candidate c;
  if (priority_.empty()) return c;
  auto it = priority_.q.begin();
  it->command = dev_.preq(it->final_command, it->av);
  if (!dev_.check_timing(it->command, it->av, clk_)) return c;
  if (would_close_active(*it)) return c;
  if (filter && !filter(*it)) return c;
  c.valid = true;
  c.it = it;
  c.buffer = &priority_;
  return c;
}

Controller::Candidate Controller::pick_rw_if(const Filter& filter) {
  set_write_mode();
  ReqBuffer& buffer = write_mode_ ? write_ : read_;
  return pick_best_ready_from(buffer, [&](const MemReq& r) {
    if (would_close_active(r)) return false;
    return !filter || filter(r);
  });
}

bool Controller::would_close_active(const MemReq& req) const {
  if (!spec_.is_closing[req.command]) return false;
  if (active_.empty()) return false;
  if (!spec_.targets_all[req.command]) return active_per_bank_[dev_.flat_bank(req.av)] > 0;
  for (size_t i = 0; i < active_per_bank_.size(); i++) {
    if (active_per_bank_[i] == 0) continue;
    if (dev_.bank_matches(dev_.banks()[i], req.av)) return true;
  }
  return false;
}

void Controller::set_write_mode() {
  if (!write_mode_) {
    if (double(write_.size()) > cfg_.wr_high * write_.max_size || read_.empty()) write_mode_ = true;
  } else {
    if (double(write_.size()) < cfg_.wr_low * write_.max_size && !read_.empty()) write_mode_ = false;
  }
}

void Controller::update_request_stats(MemReq& req) {
  req.stat_updated = true;
  if (req.type != 0 && req.type != 1) return;
  const bool hit = dev_.row_hit(req.av);
  const bool open = dev_.row_open(req.av);
  if (hit) {
    st_.row_hits++;
    st_.cls_hits[req.cls]++;
    (req.type == 0 ? st_.read_row_hits : st_.write_row_hits)++;
  } else if (open) {
    st_.row_conflicts++;
    (req.type == 0 ? st_.read_row_conflicts : st_.write_row_conflicts)++;
  } else {
    st_.row_misses++;
    (req.type == 0 ? st_.read_row_misses : st_.write_row_misses)++;
  }
}

void Controller::retire(ReqBuffer::iterator it, ReqBuffer& buffer) {
  if (&buffer == &active_) active_per_bank_[dev_.flat_bank(it->av)]--;
  if (&buffer == &write_ && it->addr != ~0ull) {
    auto f = buffered_write_addrs_.find(it->addr);
    if (f != buffered_write_addrs_.end() && --f->second == 0) buffered_write_addrs_.erase(f);
  }
  if (it->type == 0) {
    it->depart = clk_ + spec_.read_latency;
    pending_.push_back(*it);
    st_.num_read_served++;
    st_.cls_served[it->cls]++;
  } else if (it->type == 1) {
    st_.num_write_served++;
    st_.cls_served[it->cls]++;
  } else {
    st_.num_maint_served++;
  }
  buffer.q.erase(it);
}

void Controller::promote_to_active(ReqBuffer::iterator it, ReqBuffer& buffer) {
  if (active_.enqueue(*it)) {
    active_per_bank_[dev_.flat_bank(it->av)]++;
    if (&buffer == &write_ && it->addr != ~0ull) {
      auto f = buffered_write_addrs_.find(it->addr);
      if (f != buffered_write_addrs_.end() && --f->second == 0) buffered_write_addrs_.erase(f);
    }
    buffer.q.erase(it);
  }
}

// ---- HBM3 command bus rules (mirror HBM34Controller) ----
bool Controller::is_all_bank_row(int cmd) const { return spec_.targets_all[cmd] && spec_.is_row_cmd[cmd]; }

int Controller::bank_key(const AddrVecT& av) const {
  return (av[spec_.L_SID] * spec_.counts[spec_.L_BG] + av[spec_.L_BG]) * spec_.counts[spec_.L_BANK] + av[spec_.L_BANK];
}

bool Controller::can_issue_falling_pre(const MemReq& cand) const {
  if (clk_ != rising_row_.next_pairing_falling_edge) return true;
  if (rising_row_.pc != cand.av[spec_.L_PC]) return true;
  if (is_all_bank_row(rising_row_.cmd)) return false;
  if (cand.command == spec_.C_PREpb && bank_key(cand.av) != rising_row_.bank_key) return true;
  return false;
}

bool Controller::slot_matches(const MemReq& req, Slot slot) const {
  if (rising()) {
    if (slot == Slot::Row && rising_column_pc_ >= 0 && is_all_bank_row(req.command)) {
      const int row_pc = req.av[spec_.L_PC];
      if (row_pc < 0 || row_pc == rising_column_pc_) return false;
    }
    return slot == Slot::Column ? spec_.is_col_cmd[req.command] != 0 : spec_.is_row_cmd[req.command] != 0;
  }
  if (slot == Slot::Column) return false;
  if (req.command != spec_.C_PREpb && req.command != spec_.C_PREab) return false;
  return can_issue_falling_pre(req);
}

std::optional<IssuedCmd> Controller::try_issue_slot(Slot slot) {
  Filter f = [&](const MemReq& r) { return slot_matches(r, slot); };
  Candidate cand;
  if (slot == Slot::Column) cand = pick_best_ready_from(active_, f);
  if (!cand.valid) cand = pick_priority_if(f);
  if (!cand.valid && priority_.empty()) cand = pick_rw_if(f);
  if (!cand.valid) return std::nullopt;

  MemReq& r = *cand.it;
  if (!r.stat_updated) update_request_stats(r);
  dev_.issue(r.command, r.av, clk_);
  st_.cmd_count[r.command]++;
  if (spec_.is_col_cmd[r.command]) last_col_issue_[r.av[spec_.L_PC]] = clk_;
  IssuedCmd issued{clk_, r.command, r.av};
  if (cfg_.record_cmds) issued_.push_back(issued);
  if (cfg_.cmd_trace)
    std::fprintf(cfg_.cmd_trace, "%lld,%s,%d,%d,%d,%d,%d,%d,%d,%d\n", (long long)clk_, spec_.commands[r.command].c_str(),
                 r.av[0], r.av[1], r.av[2], r.av[3], r.av[4], r.av[5], r.av[6], r.type);
  if (r.command == r.final_command) retire(cand.it, *cand.buffer);
  else if (spec_.is_opening[r.command]) promote_to_active(cand.it, *cand.buffer);
  return issued;
}

// ---- stall attribution (Tokenwall addition) ----
std::string Controller::classify_pc(int pc) const {
  // Refresh in progress blocks all read/write scheduling in Ramulator's controller.
  if (!priority_.empty()) {
    const MemReq& p = priority_.q.front();
    const int cmd = dev_.preq(p.final_command, p.av);
    if (!dev_.check_timing(cmd, p.av, clk_)) {
      Binding b = dev_.binding(cmd, p.av, clk_);
      return "refresh:" + spec_.commands[cmd] + ":" + (b.constraint_id >= 0 ? spec_.constraints[b.constraint_id].name : "?");
    }
    if (would_close_active(p)) return "refresh:wait_active";
    return "refresh:arbitration";
  }
  // Only requests the scheduler could pick this tick count: the active buffer plus the
  // read or write queue selected by the current mode. Writes parked in read mode are
  // "mode_wait", not a stall of the data bus.
  const MemReq* oldest = nullptr;
  auto consider = [&](const ReqBuffer& b) {
    for (const MemReq& r : b.q)
      if (r.av[spec_.L_PC] == pc && (!oldest || r.arrive < oldest->arrive)) oldest = &r;
  };
  consider(active_);
  consider(write_mode_ ? write_ : read_);
  if (!oldest) {
    const ReqBuffer& other = write_mode_ ? read_ : write_;
    for (const MemReq& r : other.q)
      if (r.av[spec_.L_PC] == pc) return "mode_wait";
    return "empty";
  }
  const int cmd = dev_.preq(oldest->final_command, oldest->av);
  if (dev_.check_timing(cmd, oldest->av, clk_)) return "arbitration";
  Binding b = dev_.binding(cmd, oldest->av, clk_);
  return spec_.commands[cmd] + ":" + (b.constraint_id >= 0 ? spec_.constraints[b.constraint_id].name : "?");
}

void Controller::attribute_slots() {
  for (int pc = 0; pc < spec_.counts[spec_.L_PC]; pc++) {
    st_.slots++;
    if (clk_ - last_col_issue_[pc] < nBL_) {
      st_.slot_reasons["data"]++;
      continue;
    }
    st_.slot_reasons[classify_pc(pc)]++;
  }
}

}  // namespace tokenwall

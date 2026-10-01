// Memory system (N channels), trace frontend, simulation loop and reporting.
#pragma once

#include <map>
#include <memory>
#include <string>
#include <vector>

#include "tokenwall/addrmap.h"
#include "tokenwall/controller.h"
#include "tokenwall/segments.h"

namespace tokenwall {

struct SimConfig {
  std::string spec_path;
  std::string segs_path;
  std::string policy = "ramulator";
  int stacks = 1;
  int interleave_log2 = 0;
  int channels = 0;        // 0 -> 16 * stacks
  int frontend_ratio = 0;  // requests the frontend may issue per memory tick; 0 -> channels
  uint64_t max_requests = ~0ull;
  bool reads_only = false;
  bool drain = false;  // keep ticking until every request is served (Ramulator stops at the last send)
  std::string cmd_trace_path;  // CSV of every issued command (Phase 4 diffs against Ramulator)
  std::vector<std::string> disable;  // constraint-name substrings to disable (ablation)
  ControllerConfig ctrl;
};

class MemorySystem {
 public:
  MemorySystem(const DramSpec& spec, int channels, const ControllerConfig& cfg);
  bool send(MemReq& req) { return ctrls_[req.av[0]]->send(req); }
  void tick() {
    for (auto& c : ctrls_) c->tick();
  }
  bool idle() const;
  const std::vector<std::unique_ptr<Controller>>& controllers() const { return ctrls_; }

 private:
  std::vector<std::unique_ptr<Controller>> ctrls_;
};

// Expands a .segs trace, maps each address with a policy, issues one request per tick,
// retrying when the target channel's buffer is full (Ramulator's LoadStoreTrace behaviour).
class TraceFrontend {
 public:
  TraceFrontend(const SegmentTrace& trace, const Policy& policy, const Geometry& geo, uint64_t max_requests,
                bool reads_only);
  void tick(MemorySystem& mem);
  bool finished() const { return sent_ >= total_; }
  uint64_t sent() const { return sent_; }
  uint64_t total() const { return total_; }

 private:
  bool next_request();
  const Policy& policy_;
  const Geometry& geo_;
  Expander ex_;
  std::vector<uint8_t> tensor_cls_;
  tokenwall::Request base_;  // current trace request (may be split into several accesses)
  MemReq pending_;
  bool have_pending_ = false;
  int split_k_ = 1, split_i_ = 1;
  uint64_t sent_ = 0, total_ = 0, produced_ = 0, max_requests_;
  bool reads_only_;
};

struct Summary {
  int channels = 0;
  uint64_t requests_accepted = 0, requests_served = 0, in_flight_at_end = 0;
  uint64_t served_min = 0, served_max = 0;
  Tick controller_ticks = 0;
  double sim_time_us = 0, achieved_GBps = 0, peak_GBps = 0, pct_of_peak = 0;
  uint64_t row_hits = 0, row_misses = 0, row_conflicts = 0;
  double row_hit_rate_pct = 0, avg_read_latency_ticks = 0, avg_read_latency_ns = 0;
  std::map<std::string, uint64_t> cmd_counts;
  uint64_t slots = 0;
  std::map<std::string, uint64_t> slot_reasons;
  std::array<uint64_t, 4> cls_served{}, cls_hits{};
  double wall_seconds = 0;
};

Summary summarize(const MemorySystem& mem, const DramSpec& spec, double wall_seconds);
void print_summary(const Summary& s, const std::string& title);
std::string summary_json(const Summary& s, const SimConfig& cfg);
Summary run_simulation(const SimConfig& cfg, const DramSpec& spec, const SegmentTrace& trace);

}  // namespace tokenwall

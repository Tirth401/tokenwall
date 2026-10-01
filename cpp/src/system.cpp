#include "tokenwall/system.h"

#include <algorithm>
#include <chrono>
#include <cstdio>
#include <sstream>
#include <stdexcept>

namespace tokenwall {

MemorySystem::MemorySystem(const DramSpec& spec, int channels, const ControllerConfig& cfg) {
  for (int c = 0; c < channels; c++) ctrls_.push_back(std::make_unique<Controller>(spec, c, cfg));
}

bool MemorySystem::idle() const {
  for (const auto& c : ctrls_)
    if (!c->idle()) return false;
  return true;
}

TraceFrontend::TraceFrontend(const SegmentTrace& trace, const Policy& policy, const Geometry& geo,
                             uint64_t max_requests, bool reads_only)
    : policy_(policy), geo_(geo), ex_(trace), max_requests_(max_requests), reads_only_(reads_only) {
  uint64_t n = 0, w = 0;
  for (const auto& g : trace.groups)
    for (const auto& s : g.segments) {
      const uint64_t k = s.num_requests(trace.request_bytes);
      n += k;
      if (s.write) w += k;
    }
  // a trace request wider than one access becomes request_bytes / access_bytes adjacent accesses
  split_k_ = trace.request_bytes > geo.line_bytes ? int(trace.request_bytes / geo.line_bytes) : 1;
  split_i_ = split_k_;
  total_ = std::min(max_requests, reads_only ? n - w : n) * uint64_t(split_k_);
  uint32_t max_id = 0;
  for (const auto& t : trace.tensors) max_id = std::max(max_id, t.id);
  tensor_cls_.assign(max_id + 1, 3);
  for (const auto& t : trace.tensors) tensor_cls_[t.id] = t.kind == "weight" ? 0 : (t.kind == "kv" ? 1 : 3);
}

bool TraceFrontend::next_request() {
  if (split_i_ >= split_k_) {
    for (;;) {
      if (produced_ >= max_requests_) return false;
      if (!ex_.next(base_)) return false;
      if (reads_only_ && base_.write) continue;
      break;
    }
    produced_++;
    split_i_ = 0;
  }
  const tokenwall::Request& r = base_;
  const uint64_t addr = r.addr + uint64_t(split_i_) * geo_.line_bytes;
  split_i_++;
  if (addr >= geo_.capacity_bytes()) throw std::runtime_error("address beyond the configured stacks");
  const AddrVec v = policy_.map(addr, geo_);
  pending_ = MemReq{};
  pending_.addr = addr;
  pending_.av.fill(-1);
  for (int i = 0; i < FIELD_COUNT; i++) pending_.av[i] = int(v[i]);
  pending_.type = r.write ? 1 : 0;
  uint8_t cls = r.tensor_id < tensor_cls_.size() ? tensor_cls_[r.tensor_id] : 3;
  if (cls == 1 && r.write) cls = 2;
  pending_.cls = cls;
  return true;
}

void TraceFrontend::tick(MemorySystem& mem) {
  if (!have_pending_) {
    if (!next_request()) return;
    have_pending_ = true;
  }
  if (mem.send(pending_)) {
    have_pending_ = false;
    sent_++;
  }
}

Summary summarize(const MemorySystem& mem, const DramSpec& spec, double wall) {
  Summary s;
  const auto& cs = mem.controllers();
  s.channels = int(cs.size());
  uint64_t reads_served = 0, lat = 0;
  s.served_min = ~0ull;
  for (const auto& c : cs) {
    const auto& st = c->stats();
    s.requests_accepted += st.num_read_reqs + st.num_write_reqs;
    const uint64_t served = st.num_read_served + st.num_write_served;
    s.requests_served += served;
    s.served_min = std::min(s.served_min, served);
    s.served_max = std::max(s.served_max, served);
    s.controller_ticks = std::max(s.controller_ticks, st.cycles);
    s.row_hits += st.row_hits;
    s.row_misses += st.row_misses;
    s.row_conflicts += st.row_conflicts;
    reads_served += st.num_read_served;
    lat += st.read_latency_sum;
    for (int i = 0; i < spec.command_count; i++)
      if (st.cmd_count[i]) s.cmd_counts[spec.commands[i]] += st.cmd_count[i];
    s.max_refresh_wait_ticks = std::max(s.max_refresh_wait_ticks, st.max_maint_wait);
    s.avg_refresh_wait_ticks += double(st.maint_wait_sum);
    s.refreshes += st.num_maint_served;
    s.slots += st.slots;
    for (const auto& [k, v] : st.slot_reasons) s.slot_reasons[k] += v;
    for (int i = 0; i < 4; i++) {
      s.cls_served[i] += st.cls_served[i];
      s.cls_hits[i] += st.cls_hits[i];
    }
  }
  s.in_flight_at_end = s.requests_accepted - s.requests_served;
  s.avg_refresh_wait_ticks = s.refreshes ? s.avg_refresh_wait_ticks / double(s.refreshes) : 0;
  s.sim_time_us = double(s.controller_ticks) * spec.tick_ps() * 1e-6;
  s.achieved_GBps = s.sim_time_us > 0 ? double(s.requests_served) * spec.tx_bytes / (s.sim_time_us * 1e-6) / 1e9 : 0;
  const double per_pc = double(spec.tx_bytes) / (double(spec.t("nBL")) * spec.tick_ps() * 1e-12) / 1e9;
  s.peak_GBps = per_pc * spec.counts[spec.L_PC] * s.channels;
  s.pct_of_peak = s.peak_GBps > 0 ? 100.0 * s.achieved_GBps / s.peak_GBps : 0;
  const uint64_t classified = s.row_hits + s.row_misses + s.row_conflicts;
  s.row_hit_rate_pct = classified ? 100.0 * s.row_hits / classified : 0;
  s.avg_read_latency_ticks = reads_served ? double(lat) / reads_served : 0;
  s.avg_read_latency_ns = s.avg_read_latency_ticks * spec.tick_ps() / 1000.0;
  s.wall_seconds = wall;
  return s;
}

void print_summary(const Summary& s, const std::string& title) {
  std::printf("\n[%s]\n", title.c_str());
  std::printf("  %-32s %d\n", "channels", s.channels);
  std::printf("  %-32s %llu\n", "requests_accepted", (unsigned long long)s.requests_accepted);
  std::printf("  %-32s %llu\n", "requests_served", (unsigned long long)s.requests_served);
  std::printf("  %-32s %llu\n", "in_flight_at_end", (unsigned long long)s.in_flight_at_end);
  std::printf("  %-32s [%llu, %llu]\n", "served_per_channel_min_max", (unsigned long long)s.served_min,
              (unsigned long long)s.served_max);
  std::printf("  %-32s %lld\n", "controller_ticks", (long long)s.controller_ticks);
  std::printf("  %-32s %.3f\n", "sim_time_us", s.sim_time_us);
  std::printf("  %-32s %.3f\n", "achieved_GBps", s.achieved_GBps);
  std::printf("  %-32s %.1f\n", "peak_GBps", s.peak_GBps);
  std::printf("  %-32s %.3f\n", "pct_of_peak", s.pct_of_peak);
  std::printf("  %-32s %llu\n", "row_hits", (unsigned long long)s.row_hits);
  std::printf("  %-32s %llu\n", "row_misses", (unsigned long long)s.row_misses);
  std::printf("  %-32s %llu\n", "row_conflicts", (unsigned long long)s.row_conflicts);
  std::printf("  %-32s %.3f\n", "row_hit_rate_pct", s.row_hit_rate_pct);
  std::printf("  %-32s %.3f\n", "avg_read_latency_ticks", s.avg_read_latency_ticks);
  std::printf("  %-32s %.3f\n", "avg_read_latency_ns", s.avg_read_latency_ns);
  std::printf("  %-32s %llu (max wait %lld ticks = %.1f ns, avg %.1f ticks)\n", "refresh_commands",
              (unsigned long long)s.refreshes, (long long)s.max_refresh_wait_ticks,
              s.max_refresh_wait_ticks * 0.3125, s.avg_refresh_wait_ticks);
  std::printf("  %-32s %.3f\n", "wall_seconds", s.wall_seconds);
  std::printf("  commands:");
  for (const auto& [k, v] : s.cmd_counts) std::printf(" %s=%llu", k.c_str(), (unsigned long long)v);
  std::printf("\n");
  if (s.slots) {
    std::printf("  column-command slots per pseudo channel: %llu\n", (unsigned long long)s.slots);
    std::vector<std::pair<std::string, uint64_t>> v(s.slot_reasons.begin(), s.slot_reasons.end());
    std::sort(v.begin(), v.end(), [](auto& a, auto& b) { return a.second > b.second; });
    for (const auto& [k, n] : v)
      if (100.0 * n / s.slots >= 0.05) std::printf("    %-44s %6.2f%%\n", k.c_str(), 100.0 * n / s.slots);
  }
  const char* names[4] = {"weights", "kv_read", "kv_write", "other"};
  for (int i = 0; i < 4; i++)
    if (s.cls_served[i])
      std::printf("  %-10s served %12llu  row hits %6.2f%%\n", names[i], (unsigned long long)s.cls_served[i],
                  100.0 * s.cls_hits[i] / s.cls_served[i]);
}

std::string summary_json(const Summary& s, const SimConfig& cfg) {
  std::ostringstream o;
  o.precision(15);  // full double precision, so comparisons against Ramulator are not limited by printing
  o << "{\n";
  o << "  \"config\": {\"segs\": \"" << cfg.segs_path << "\", \"spec\": \"" << cfg.spec_path << "\", \"policy\": \""
    << cfg.policy << "\", \"stacks\": " << cfg.stacks << ", \"interleave_log2\": " << cfg.interleave_log2
    << ", \"channels\": " << s.channels << ", \"frontend_ratio\": " << cfg.frontend_ratio
    << ", \"max_requests\": " << (cfg.max_requests == ~0ull ? -1 : (long long)cfg.max_requests)
    << ", \"reads_only\": " << (cfg.reads_only ? "true" : "false") << ", \"drain\": " << (cfg.drain ? "true" : "false")
    << ", \"refresh\": \"" << refresh_name(cfg.ctrl.refresh) << "\", \"refresh_blocks_scheduling\": "
    << (cfg.ctrl.refresh_blocks_scheduling ? "true" : "false") << ", \"disabled\": [";
  for (size_t i = 0; i < cfg.disable.size(); i++) o << (i ? ", " : "") << "\"" << cfg.disable[i] << "\"";
  o << "]},\n";
  o << "  \"result\": {\n";
  o << "    \"channels\": " << s.channels << ",\n";
  o << "    \"requests_accepted\": " << s.requests_accepted << ",\n";
  o << "    \"requests_served\": " << s.requests_served << ",\n";
  o << "    \"in_flight_at_end\": " << s.in_flight_at_end << ",\n";
  o << "    \"served_per_channel_min_max\": [" << s.served_min << ", " << s.served_max << "],\n";
  o << "    \"controller_ticks\": " << s.controller_ticks << ",\n";
  o << "    \"sim_time_us\": " << s.sim_time_us << ",\n";
  o << "    \"achieved_GBps\": " << s.achieved_GBps << ",\n";
  o << "    \"peak_GBps\": " << s.peak_GBps << ",\n";
  o << "    \"pct_of_peak\": " << s.pct_of_peak << ",\n";
  o << "    \"row_hits\": " << s.row_hits << ",\n";
  o << "    \"row_misses\": " << s.row_misses << ",\n";
  o << "    \"row_conflicts\": " << s.row_conflicts << ",\n";
  o << "    \"row_hit_rate_pct\": " << s.row_hit_rate_pct << ",\n";
  o << "    \"avg_read_latency_ticks\": " << s.avg_read_latency_ticks << ",\n";
  o << "    \"avg_read_latency_ns\": " << s.avg_read_latency_ns << ",\n";
  o << "    \"wall_seconds\": " << s.wall_seconds << ",\n";
  o << "    \"refresh_commands\": " << s.refreshes << ",\n";
  o << "    \"max_refresh_wait_ticks\": " << s.max_refresh_wait_ticks << ",\n";
  o << "    \"avg_refresh_wait_ticks\": " << s.avg_refresh_wait_ticks << ",\n";
  o << "    \"commands\": {";
  bool first = true;
  for (const auto& [k, v] : s.cmd_counts) {
    o << (first ? "" : ", ") << "\"" << k << "\": " << v;
    first = false;
  }
  o << "},\n    \"slots\": " << s.slots << ",\n    \"slot_reasons\": {";
  first = true;
  for (const auto& [k, v] : s.slot_reasons) {
    o << (first ? "" : ", ") << "\"" << k << "\": " << v;
    first = false;
  }
  o << "},\n    \"classes\": {";
  const char* names[4] = {"weights", "kv_read", "kv_write", "other"};
  first = true;
  for (int i = 0; i < 4; i++) {
    o << (first ? "" : ", ") << "\"" << names[i] << "\": {\"served\": " << s.cls_served[i] << ", \"row_hits\": "
      << s.cls_hits[i] << "}";
    first = false;
  }
  o << "}\n  }\n}\n";
  return o.str();
}

Summary run_simulation(const SimConfig& cfg, const DramSpec& spec, const SegmentTrace& trace) {
  const int channels = cfg.channels > 0 ? cfg.channels : 16 * cfg.stacks;
  Geometry geo = geometry_for_stacks(cfg.stacks);
  geo.channels = channels;
  Policy policy = make_policy(cfg.policy, geo, cfg.interleave_log2);
  ControllerConfig ctrl_cfg = cfg.ctrl;
  std::FILE* trace_file = nullptr;
  if (!cfg.cmd_trace_path.empty()) {
    trace_file = std::fopen(cfg.cmd_trace_path.c_str(), "w");
    if (!trace_file) throw std::runtime_error("cannot write " + cfg.cmd_trace_path);
    std::fprintf(trace_file, "clock,command,Channel,PseudoChannel,Sid,BankGroup,Bank,Row,Column,type\n");
    ctrl_cfg.cmd_trace = trace_file;
  }
  MemorySystem mem(spec, channels, ctrl_cfg);
  TraceFrontend fe(trace, policy, geo, cfg.max_requests, cfg.reads_only);
  const int fe_tick = cfg.frontend_ratio > 0 ? cfg.frontend_ratio : channels;
  const int mem_tick = 1;
  auto t0 = std::chrono::steady_clock::now();
  // Same interleaving as Ramulator's Simulation::run with frontend clock_ratio = fe_tick, memory = 1.
  int fe_count = mem_tick - 1, mem_count = fe_tick - 1;
  for (;;) {
    if (++fe_count >= mem_tick) {
      fe_count = 0;
      fe.tick(mem);
    }
    if (fe.finished()) break;
    if (++mem_count >= fe_tick) {
      mem_count = 0;
      mem.tick();
    }
  }
  if (cfg.drain) {
    while (!mem.idle()) mem.tick();
  }
  const double wall = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
  if (trace_file) std::fclose(trace_file);
  return summarize(mem, spec, wall);
}

}  // namespace tokenwall

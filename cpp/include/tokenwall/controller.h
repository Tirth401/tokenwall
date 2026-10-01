// Per-channel memory controller mirroring Ramulator 2.1's HBM34 controller:
// read/write/active/priority buffers, FR-FCFS, open-row policy, write-mode
// watermarks, all-bank refresh, HBM3's split command bus with its rising and
// falling edge rules. Adds what Ramulator does not report: a per pseudo
// channel accounting of every column-command slot as data, empty, arbitration
// or the named timing constraint that blocked the oldest request.
#pragma once

#include <cstdint>
#include <functional>
#include <list>
#include <map>
#include <optional>
#include <string>
#include <vector>

#include "tokenwall/device.h"

namespace tokenwall {

struct MemReq {
  uint64_t addr = ~0ull;  // flat address (coalescing key); ~0 when unknown
  AddrVecT av{};
  int type = -1;  // 0 read, 1 write, -1 maintenance
  int command = -1;
  int final_command = -1;
  Tick arrive = -1;
  Tick depart = -1;
  bool stat_updated = false;
  uint8_t cls = 0;  // request class for per-class statistics (0 weights, 1 kv_read, 2 kv_write, 3 other)
};

struct ReqBuffer {
  std::list<MemReq> q;
  size_t max_size = 32;
  using iterator = std::list<MemReq>::iterator;
  bool enqueue(const MemReq& r) {
    if (q.size() >= max_size) return false;
    q.push_back(r);
    return true;
  }
  size_t size() const { return q.size(); }
  bool empty() const { return q.empty(); }
};

struct ControllerConfig {
  int read_buffer = 32;
  int write_buffer = 32;
  int priority_buffer = 1568;
  double wr_low = 0.2;
  double wr_high = 0.8;
  bool refresh_allbank = true;
  bool attribute = true;   // per-slot stall attribution (small cost)
  bool record_cmds = false;  // keep every issued command (tests, Phase 4 diffs)
};

struct IssuedCmd {
  Tick clk;
  int cmd;
  AddrVecT av;
};

struct ControllerStats {
  Tick cycles = 0;
  uint64_t row_hits = 0, row_misses = 0, row_conflicts = 0;
  uint64_t read_row_hits = 0, read_row_misses = 0, read_row_conflicts = 0;
  uint64_t write_row_hits = 0, write_row_misses = 0, write_row_conflicts = 0;
  uint64_t num_read_reqs = 0, num_write_reqs = 0, num_maint_reqs = 0;
  uint64_t num_read_served = 0, num_write_served = 0, num_maint_served = 0;
  uint64_t read_forwarded = 0, write_coalesced = 0;
  uint64_t read_latency_sum = 0;  // over departed reads
  uint64_t queue_len_sum = 0, read_q_sum = 0, write_q_sum = 0, prio_q_sum = 0;
  std::vector<uint64_t> cmd_count;  // per command id
  // attribution: one entry per (pseudo channel, rising edge)
  uint64_t slots = 0;
  std::map<std::string, uint64_t> slot_reasons;  // "data", "empty", "arbitration", "RD:BankGroup:nCCDL", ...
  std::array<uint64_t, 4> cls_served{}, cls_hits{};
};

class Controller {
 public:
  Controller(const DramSpec& spec, int channel_id, const ControllerConfig& cfg);
  bool send(MemReq& req);  // false when the target buffer is full (caller retries)
  void tick();
  bool idle() const;
  Tick clk() const { return clk_; }
  const ControllerStats& stats() const { return st_; }
  const Device& device() const { return dev_; }
  const std::vector<IssuedCmd>& issued() const { return issued_; }

 private:
  enum class Slot { Column, Row };
  struct Candidate {
    bool valid = false;
    ReqBuffer::iterator it;
    ReqBuffer* buffer = nullptr;
  };
  using Filter = std::function<bool(const MemReq&)>;

  void serve_completed_reads();
  void refresh_tick();
  bool priority_send(MemReq& req);
  std::optional<IssuedCmd> try_issue_slot(Slot slot);
  Candidate pick_best_ready_from(ReqBuffer& buffer, const Filter& filter);
  Candidate pick_priority_if(const Filter& filter);
  Candidate pick_rw_if(const Filter& filter);
  bool would_close_active(const MemReq& req) const;
  void set_write_mode();
  void update_request_stats(MemReq& req);
  void retire(ReqBuffer::iterator it, ReqBuffer& buffer);
  void promote_to_active(ReqBuffer::iterator it, ReqBuffer& buffer);
  bool slot_matches(const MemReq& req, Slot slot) const;
  bool can_issue_falling_pre(const MemReq& cand) const;
  bool is_all_bank_row(int cmd) const;
  int bank_key(const AddrVecT& av) const;
  bool rising() const { return (clk_ % 2) == 1; }
  void attribute_slots();
  std::string classify_pc(int pc) const;

  const DramSpec& spec_;
  ControllerConfig cfg_;
  Device dev_;
  int channel_id_;
  Tick clk_ = 0;
  std::deque<MemReq> pending_;  // completed reads waiting for their departure tick
  ReqBuffer active_, priority_, read_, write_;
  std::map<uint64_t, int> buffered_write_addrs_;
  std::vector<int> active_per_bank_;
  bool write_mode_ = false;
  // all-bank refresh
  Tick next_refresh_ = -1;
  Tick nREFI_ = 0;
  // HBM3 command-bus edge rules
  int rising_column_pc_ = -1;
  struct RisingRow {
    int cmd = -1, pc = -1, bank_key = -1;
    Tick next_pairing_falling_edge = -1;
  } rising_row_;
  std::vector<Tick> last_col_issue_;  // per pseudo channel
  Tick nBL_ = 4;
  ControllerStats st_;
  std::vector<IssuedCmd> issued_;
};

}  // namespace tokenwall

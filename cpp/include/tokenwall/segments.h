// Segment-list trace format shared with python/tokenwall/segments.py.
// See that file for the format description. Both expanders must produce the
// identical request sequence; tests/test_cross_expander.py checks this.
#pragma once

#include <cstdint>
#include <istream>
#include <string>
#include <vector>

namespace tokenwall {

struct Segment {
  bool write = false;
  uint64_t base = 0;
  uint64_t run_bytes = 0;
  uint64_t runs = 1;
  uint64_t stride = 0;
  uint64_t outer_runs = 1;
  uint64_t outer_stride = 0;
  uint32_t tensor_id = 0;

  uint64_t num_requests(uint64_t rb) const { return outer_runs * runs * (run_bytes / rb); }

  // Address of request index idx within this segment (two-level striding).
  uint64_t address(uint64_t rb, uint64_t idx) const {
    const uint64_t per_run = run_bytes / rb;
    const uint64_t per_outer = runs * per_run;
    const uint64_t o = idx / per_outer;
    const uint64_t rem = idx % per_outer;
    const uint64_t r = rem / per_run;
    const uint64_t off = rem % per_run;
    return base + o * outer_stride + r * stride + off * rb;
  }
};

struct Group {
  uint64_t chunk_bytes = 0;  // 0: segments play sequentially; >0: round-robin this many bytes each
  std::vector<Segment> segments;
};

struct TensorInfo {
  uint32_t id = 0;
  std::string kind;
  int layer = -1;
  uint64_t base = 0;
  uint64_t nbytes = 0;
  std::string name;
};

struct SegmentTrace {
  uint32_t request_bytes = 32;
  std::vector<TensorInfo> tensors;
  std::vector<Group> groups;

  uint64_t num_requests() const;
  static SegmentTrace parse(std::istream& in);
  static SegmentTrace load(const std::string& path);
};

struct Request {
  uint64_t addr = 0;
  bool write = false;
  uint32_t tensor_id = 0;
};

// Streams requests in exact issue order without materialising them.
class Expander {
 public:
  explicit Expander(const SegmentTrace& trace) : t_(trace) {}
  bool next(Request& out);

 private:
  void begin_group();

  const SegmentTrace& t_;
  size_t gi_ = 0;
  bool group_ready_ = false;
  std::vector<uint64_t> cursor_;
  std::vector<uint64_t> total_;
  size_t k_ = 0;
  uint64_t remaining_ = 0;
  uint64_t per_chunk_ = 0;
  uint64_t chunk_left_ = 0;
};

// Order-sensitive FNV-1a style hash over 64-bit words (addr << 1 | write).
// Same definition as stream_hash() in python/tokenwall/segments.py.
struct StreamHash {
  uint64_t h = 0xcbf29ce484222325ULL;
  uint64_t requests = 0;
  uint64_t writes = 0;
  void add(uint64_t addr, bool write) {
    h ^= (addr << 1) | (write ? 1ULL : 0ULL);
    h *= 0x100000001b3ULL;
    requests++;
    writes += write ? 1 : 0;
  }
};

}  // namespace tokenwall

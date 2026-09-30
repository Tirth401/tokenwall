#include "tokenwall/segments.h"

#include <fstream>
#include <sstream>
#include <stdexcept>

namespace tokenwall {

uint64_t SegmentTrace::num_requests() const {
  uint64_t n = 0;
  for (const auto& g : groups)
    for (const auto& s : g.segments) n += s.num_requests(request_bytes);
  return n;
}

SegmentTrace SegmentTrace::parse(std::istream& in) {
  SegmentTrace t;
  std::string line;
  std::string tag;
  int version = 0;

  if (!std::getline(in, line)) throw std::runtime_error("empty segment file");
  {
    std::istringstream ls(line);
    ls >> tag >> version;
    if (tag != "tokenwall-segments" || version != 1) throw std::runtime_error("not a tokenwall-segments v1 file");
  }
  if (!std::getline(in, line)) throw std::runtime_error("missing request_bytes line");
  {
    std::istringstream ls(line);
    ls >> tag >> t.request_bytes;
    if (tag != "request_bytes" || t.request_bytes == 0) throw std::runtime_error("bad request_bytes line");
  }

  size_t line_no = 2;
  while (std::getline(in, line)) {
    line_no++;
    std::istringstream ls(line);
    if (!(ls >> tag) || tag == "#") continue;
    if (tag == "T") {
      TensorInfo ti;
      ls >> ti.id >> ti.kind >> ti.layer >> ti.base >> ti.nbytes >> ti.name;
      t.tensors.push_back(ti);
    } else if (tag == "G") {
      Group g;
      ls >> g.chunk_bytes;
      t.groups.push_back(g);
    } else if (tag == "S") {
      if (t.groups.empty()) throw std::runtime_error("S record before any G record at line " + std::to_string(line_no));
      Segment s;
      std::string rw;
      ls >> rw >> s.base >> s.run_bytes >> s.runs >> s.stride >> s.outer_runs >> s.outer_stride >> s.tensor_id;
      if (ls.fail() || (rw != "R" && rw != "W")) throw std::runtime_error("bad S record at line " + std::to_string(line_no));
      s.write = (rw == "W");
      if (s.run_bytes == 0 || s.run_bytes % t.request_bytes || s.runs == 0 || s.outer_runs == 0)
        throw std::runtime_error("invalid segment geometry at line " + std::to_string(line_no));
      t.groups.back().segments.push_back(s);
    } else {
      throw std::runtime_error("unknown record '" + tag + "' at line " + std::to_string(line_no));
    }
  }
  return t;
}

SegmentTrace SegmentTrace::load(const std::string& path) {
  std::ifstream f(path);
  if (!f) throw std::runtime_error("cannot open " + path);
  return parse(f);
}

void Expander::begin_group() {
  const Group& g = t_.groups[gi_];
  const size_t n = g.segments.size();
  cursor_.assign(n, 0);
  total_.resize(n);
  remaining_ = 0;
  for (size_t i = 0; i < n; i++) {
    total_[i] = g.segments[i].num_requests(t_.request_bytes);
    remaining_ += total_[i];
  }
  k_ = 0;
  per_chunk_ = g.chunk_bytes / t_.request_bytes;
  if (g.chunk_bytes > 0 && per_chunk_ == 0) per_chunk_ = 1;
  chunk_left_ = per_chunk_;
}

bool Expander::next(Request& out) {
  while (gi_ < t_.groups.size()) {
    if (!group_ready_) {
      begin_group();
      group_ready_ = true;
    }
    if (remaining_ == 0) {
      gi_++;
      group_ready_ = false;
      continue;
    }
    const Group& g = t_.groups[gi_];
    const size_t n = g.segments.size();
    if (g.chunk_bytes == 0) {
      while (cursor_[k_] >= total_[k_]) k_++;
      const Segment& s = g.segments[k_];
      out = {s.address(t_.request_bytes, cursor_[k_]), s.write, s.tensor_id};
      cursor_[k_]++;
      remaining_--;
      return true;
    }
    for (;;) {
      if (cursor_[k_] < total_[k_] && chunk_left_ > 0) {
        const Segment& s = g.segments[k_];
        out = {s.address(t_.request_bytes, cursor_[k_]), s.write, s.tensor_id};
        cursor_[k_]++;
        chunk_left_--;
        remaining_--;
        return true;
      }
      k_ = (k_ + 1) % n;
      chunk_left_ = per_chunk_;
    }
  }
  return false;
}

}  // namespace tokenwall

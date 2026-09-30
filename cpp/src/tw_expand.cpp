// Expand a .segs file: report request count and stream hash, optionally write
// Ramulator 2.1 trace text. Usage:
//   tw_expand <file.segs> [--max-requests N] [--reads-only] [--ramulator-out path]
//             [--map policy --stacks S --interleave-log2 K]
// Without --map the output is LoadStoreTrace ("LD <addr>"); with --map it is
// ReadWriteTrace ("R ch,pc,sid,bg,bank,row,col") using the named policy.
#include <cstdio>
#include <cstdlib>
#include <string>

#include "tokenwall/addrmap.h"
#include "tokenwall/segments.h"

int main(int argc, char** argv) {
  if (argc < 2) {
    std::fprintf(stderr, "usage: tw_expand <file.segs> [--max-requests N] [--ramulator-out path]\n");
    return 2;
  }
  std::string path = argv[1];
  uint64_t max_requests = UINT64_MAX;
  std::string out_path, map_policy;
  int stacks = 1, interleave_log2 = 0;
  bool reads_only = false;
  for (int i = 2; i < argc; i++) {
    std::string a = argv[i];
    if (a == "--max-requests" && i + 1 < argc) {
      max_requests = std::strtoull(argv[++i], nullptr, 10);
    } else if (a == "--ramulator-out" && i + 1 < argc) {
      out_path = argv[++i];
    } else if (a == "--map" && i + 1 < argc) {
      map_policy = argv[++i];
    } else if (a == "--stacks" && i + 1 < argc) {
      stacks = std::atoi(argv[++i]);
    } else if (a == "--interleave-log2" && i + 1 < argc) {
      interleave_log2 = std::atoi(argv[++i]);
    } else if (a == "--reads-only") {
      reads_only = true;
    } else {
      std::fprintf(stderr, "unknown argument %s\n", a.c_str());
      return 2;
    }
  }

  tokenwall::SegmentTrace trace;
  try {
    trace = tokenwall::SegmentTrace::load(path);
  } catch (const std::exception& e) {
    std::fprintf(stderr, "error: %s\n", e.what());
    return 1;
  }

  FILE* out = nullptr;
  if (!out_path.empty()) {
    out = std::fopen(out_path.c_str(), "w");
    if (!out) {
      std::fprintf(stderr, "cannot write %s\n", out_path.c_str());
      return 1;
    }
  }

  tokenwall::Geometry geo = tokenwall::geometry_for_stacks(stacks);
  tokenwall::Policy policy;
  if (!map_policy.empty()) {
    try {
      policy = tokenwall::make_policy(map_policy, geo, interleave_log2);
    } catch (const std::exception& e) {
      std::fprintf(stderr, "error: %s\n", e.what());
      return 1;
    }
  }

  tokenwall::Expander ex(trace);
  tokenwall::StreamHash hash;
  tokenwall::Request r;
  while (hash.requests < max_requests && ex.next(r)) {
    if (reads_only && r.write) continue;
    hash.add(r.addr, r.write);
    if (!out) continue;
    if (map_policy.empty()) {
      std::fprintf(out, "%s %llu\n", r.write ? "ST" : "LD", static_cast<unsigned long long>(r.addr));
    } else {
      if (r.addr >= geo.capacity_bytes()) {
        std::fprintf(stderr, "error: address %llu beyond %d stack(s)\n", static_cast<unsigned long long>(r.addr), stacks);
        return 1;
      }
      const tokenwall::AddrVec v = policy.map(r.addr, geo);
      std::fprintf(out, "%s %u,%u,%u,%u,%u,%u,%u\n", r.write ? "W" : "R", v[0], v[1], v[2], v[3], v[4], v[5], v[6]);
    }
  }
  if (out) std::fclose(out);

  std::printf("requests=%llu reads=%llu writes=%llu bytes=%llu hash=0x%016llx\n",
              static_cast<unsigned long long>(hash.requests),
              static_cast<unsigned long long>(hash.requests - hash.writes),
              static_cast<unsigned long long>(hash.writes),
              static_cast<unsigned long long>(hash.requests * trace.request_bytes),
              static_cast<unsigned long long>(hash.h));
  return 0;
}

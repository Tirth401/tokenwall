// Expand a .segs file: report request count and stream hash, optionally write
// Ramulator 2.1 LoadStoreTrace text. Usage:
//   tw_expand <file.segs> [--max-requests N] [--ramulator-out path]
#include <cstdio>
#include <cstdlib>
#include <string>

#include "tokenwall/segments.h"

int main(int argc, char** argv) {
  if (argc < 2) {
    std::fprintf(stderr, "usage: tw_expand <file.segs> [--max-requests N] [--ramulator-out path]\n");
    return 2;
  }
  std::string path = argv[1];
  uint64_t max_requests = UINT64_MAX;
  std::string out_path;
  for (int i = 2; i < argc; i++) {
    std::string a = argv[i];
    if (a == "--max-requests" && i + 1 < argc) {
      max_requests = std::strtoull(argv[++i], nullptr, 10);
    } else if (a == "--ramulator-out" && i + 1 < argc) {
      out_path = argv[++i];
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

  tokenwall::Expander ex(trace);
  tokenwall::StreamHash hash;
  tokenwall::Request r;
  while (hash.requests < max_requests && ex.next(r)) {
    hash.add(r.addr, r.write);
    if (out) std::fprintf(out, "%s %llu\n", r.write ? "ST" : "LD", static_cast<unsigned long long>(r.addr));
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

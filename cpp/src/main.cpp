// tokenwall: HBM3 timing simulation of a decode-step trace.
//   tokenwall sim --segs FILE [--spec FILE] [--policy NAME] [--stacks N] [--interleave-log2 K]
//                 [--channels N] [--frontend-ratio R] [--max-requests N] [--reads-only]
//                 [--refresh allbank|none] [--drain] [--disable SUBSTR,...] [--json OUT] [--label TEXT]
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <sstream>
#include <string>

#include "tokenwall/system.h"
#include "tokenwall/version.h"

namespace {
void usage() {
  std::fprintf(stderr,
               "tokenwall %s\n"
               "usage: tokenwall sim --segs FILE [--spec FILE] [--policy ramulator|bank_low|bank_high|bank_low_xor]\n"
               "         [--stacks N] [--interleave-log2 K] [--channels N] [--frontend-ratio R]\n"
               "         [--max-requests N] [--reads-only] [--refresh allbank|none] [--drain]\n"
               "         [--disable SUBSTR,...] [--no-attribution] [--json OUT] [--label TEXT]\n",
               tokenwall::kVersion);
}
}  // namespace

int main(int argc, char** argv) {
  if (argc < 2 || std::string(argv[1]) != "sim") {
    usage();
    return argc >= 2 && std::string(argv[1]) == "--version" ? 0 : 2;
  }
  tokenwall::SimConfig cfg;
  cfg.spec_path = "configs/hbm3/hbm3_16gb_8hi_6400.spec";
  std::string json_out, label = "tokenwall";
  for (int i = 2; i < argc; i++) {
    std::string a = argv[i];
    auto val = [&]() -> std::string {
      if (i + 1 >= argc) {
        usage();
        std::exit(2);
      }
      return argv[++i];
    };
    if (a == "--segs") cfg.segs_path = val();
    else if (a == "--spec") cfg.spec_path = val();
    else if (a == "--policy") cfg.policy = val();
    else if (a == "--stacks") cfg.stacks = std::stoi(val());
    else if (a == "--interleave-log2") cfg.interleave_log2 = std::stoi(val());
    else if (a == "--channels") cfg.channels = std::stoi(val());
    else if (a == "--frontend-ratio") cfg.frontend_ratio = std::stoi(val());
    else if (a == "--max-requests") cfg.max_requests = std::strtoull(val().c_str(), nullptr, 10);
    else if (a == "--reads-only") cfg.reads_only = true;
    else if (a == "--drain") cfg.drain = true;
    else if (a == "--no-attribution") cfg.ctrl.attribute = false;
    else if (a == "--refresh") {
      std::string r = val();
      if (r == "allbank") cfg.ctrl.refresh_allbank = true;
      else if (r == "none") cfg.ctrl.refresh_allbank = false;
      else {
        std::fprintf(stderr, "unknown refresh mode %s\n", r.c_str());
        return 2;
      }
    } else if (a == "--disable") {
      std::stringstream ss(val());
      std::string tok;
      while (std::getline(ss, tok, ',')) if (!tok.empty()) cfg.disable.push_back(tok);
    } else if (a == "--json") json_out = val();
    else if (a == "--label") label = val();
    else {
      std::fprintf(stderr, "unknown argument %s\n", a.c_str());
      usage();
      return 2;
    }
  }
  if (cfg.segs_path.empty()) {
    usage();
    return 2;
  }
  try {
    tokenwall::DramSpec spec = tokenwall::DramSpec::load(cfg.spec_path);
    if (!cfg.disable.empty()) {
      int n = spec.disable_matching(cfg.disable);
      std::printf("disabled %d constraint entries matching:", n);
      for (auto& d : cfg.disable) std::printf(" %s", d.c_str());
      std::printf("\n");
    }
    tokenwall::SegmentTrace trace = tokenwall::SegmentTrace::load(cfg.segs_path);
    tokenwall::Summary s = tokenwall::run_simulation(cfg, spec, trace);
    tokenwall::print_summary(s, label);
    if (!json_out.empty()) {
      std::ofstream f(json_out);
      f << tokenwall::summary_json(s, cfg);
      std::printf("wrote %s\n", json_out.c_str());
    }
  } catch (const std::exception& e) {
    std::fprintf(stderr, "error: %s\n", e.what());
    return 1;
  }
  return 0;
}

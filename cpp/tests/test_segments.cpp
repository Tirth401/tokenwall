#include <sstream>
#include <vector>

#include "tokenwall/segments.h"
#include "tw_test.h"

namespace {

const char* kTrace =
    "tokenwall-segments 1\n"
    "request_bytes 32\n"
    "T 0 weight -1 0 4096 w\n"
    "G 0\n"
    "S R 0 64 2 256 2 1024 0\n"   // two-level striding: 2 runs of 64 B, 256 B apart, repeated 2x 1 KiB apart
    "G 64\n"                       // round-robin, 2 requests per turn
    "S R 4096 96 1 0 1 0 0\n"      // 3 requests
    "S W 8192 128 1 0 1 0 0\n";    // 4 requests

std::vector<tokenwall::Request> expand_all(const tokenwall::SegmentTrace& t) {
  std::vector<tokenwall::Request> v;
  tokenwall::Expander ex(t);
  tokenwall::Request r;
  while (ex.next(r)) v.push_back(r);
  return v;
}

}  // namespace

TW_TEST(parse_counts) {
  std::istringstream in(kTrace);
  auto t = tokenwall::SegmentTrace::parse(in);
  TW_CHECK_EQ(t.request_bytes, 32u);
  TW_CHECK_EQ(t.groups.size(), 2u);
  TW_CHECK_EQ(t.num_requests(), 8u + 3u + 4u);
}

TW_TEST(sequential_two_level_striding) {
  std::istringstream in(kTrace);
  auto t = tokenwall::SegmentTrace::parse(in);
  auto v = expand_all(t);
  const uint64_t expect[] = {0, 32, 256, 288, 1024, 1056, 1280, 1312};
  for (size_t i = 0; i < 8; i++) TW_CHECK_EQ(v[i].addr, expect[i]);
  for (size_t i = 0; i < 8; i++) TW_CHECK(!v[i].write);
}

TW_TEST(round_robin_group_order) {
  std::istringstream in(kTrace);
  auto t = tokenwall::SegmentTrace::parse(in);
  auto v = expand_all(t);
  // B: 4096,4128 | C: 8192,8224 | B: 4160 (exhausted) | C: 8256,8288
  const uint64_t expect[] = {4096, 4128, 8192, 8224, 4160, 8256, 8288};
  const bool expect_w[] = {false, false, true, true, false, true, true};
  TW_CHECK_EQ(v.size(), 15u);
  for (size_t i = 0; i < 7; i++) {
    TW_CHECK_EQ(v[8 + i].addr, expect[i]);
    TW_CHECK_EQ(v[8 + i].write, expect_w[i]);
  }
}

TW_TEST(hash_is_order_sensitive) {
  tokenwall::StreamHash a, b;
  a.add(0, false); a.add(32, false);
  b.add(32, false); b.add(0, false);
  TW_CHECK(a.h != b.h);
  TW_CHECK_EQ(a.requests, 2u);
}

TW_TEST_MAIN()

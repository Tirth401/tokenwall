#include "tokenwall/addrmap.h"
#include "tw_test.h"

using tokenwall::AddrVec;
using tokenwall::Geometry;

TW_TEST(geometry_one_stack_is_16_GiB) {
  Geometry g = tokenwall::geometry_for_stacks(1);
  TW_CHECK_EQ(g.total_bits(), 29);
  TW_CHECK_EQ(g.capacity_bytes(), uint64_t{16} << 30);
  TW_CHECK_EQ(g.banks_total(), 1024u);
}

TW_TEST(ramulator_policy_known_answers) {
  Geometry g = tokenwall::geometry_for_stacks(1);
  auto p = tokenwall::make_policy("ramulator", g, 0);
  AddrVec v = p.map(0, g);
  for (int f = 0; f < tokenwall::FIELD_COUNT; f++) TW_CHECK_EQ(v[f], 0u);
  TW_CHECK_EQ(p.map(32, g)[tokenwall::CHANNEL], 1u);            // next line -> next channel
  TW_CHECK_EQ(p.map(32 * 16, g)[tokenwall::COLUMN], 1u);        // 16 channels later -> next column
  TW_CHECK_EQ(p.map(32 * 16 * 32, g)[tokenwall::PSEUDO_CHANNEL], 1u);
  TW_CHECK_EQ(p.map(32 * 16 * 32 * 2, g)[tokenwall::SID], 1u);
  TW_CHECK_EQ(p.map(32 * 16 * 32 * 2 * 2, g)[tokenwall::BANK_GROUP], 1u);
  TW_CHECK_EQ(p.map(32 * 16 * 32 * 2 * 2 * 4, g)[tokenwall::BANK], 1u);
  TW_CHECK_EQ(p.map(uint64_t{32} * 16 * 32 * 2 * 2 * 4 * 4, g)[tokenwall::ROW], 1u);
}

TW_TEST(bank_low_alternates_bank_group_within_channel) {
  Geometry g = tokenwall::geometry_for_stacks(1);
  auto p = tokenwall::make_policy("bank_low", g, 0);
  // consecutive lines of one channel are 16 lines apart in the flat space
  TW_CHECK_EQ(p.map(0, g)[tokenwall::PSEUDO_CHANNEL], 0u);
  TW_CHECK_EQ(p.map(32 * 16, g)[tokenwall::PSEUDO_CHANNEL], 1u);
  TW_CHECK_EQ(p.map(32 * 16 * 2, g)[tokenwall::BANK_GROUP], 1u);
}

TW_TEST(round_trip_all_policies) {
  Geometry g = tokenwall::geometry_for_stacks(2);
  const char* names[] = {"ramulator", "bank_low", "bank_high", "bank_low_xor"};
  uint64_t x = 0x9E3779B97F4A7C15ULL;
  for (const char* n : names) {
    for (int k : {0, 3, 5}) {
      auto p = tokenwall::make_policy(n, g, k);
      for (int i = 0; i < 2000; i++) {
        x ^= x << 13; x ^= x >> 7; x ^= x << 17;  // xorshift
        uint64_t addr = (x % (g.capacity_bytes() / g.line_bytes)) * g.line_bytes;
        TW_CHECK_EQ(p.unmap(p.map(addr, g), g), addr);
      }
    }
  }
}

TW_TEST(xor_policy_spreads_same_bank_stride) {
  Geometry g = tokenwall::geometry_for_stacks(1);
  auto plain = tokenwall::make_policy("bank_low", g, 0);
  auto hashed = tokenwall::make_policy("bank_low_xor", g, 0);
  // stride that keeps channel/pc/bg/bank/sid/column fixed and steps the row
  const uint64_t stride = uint64_t{32} << (4 + 1 + 2 + 2 + 1 + 5);
  uint64_t banks_plain = 0, banks_hashed = 0;
  for (int i = 0; i < 16; i++) {
    banks_plain |= uint64_t{1} << g.flat_bank(plain.map(i * stride, g)) % 64;
    banks_hashed |= uint64_t{1} << g.flat_bank(hashed.map(i * stride, g)) % 64;
  }
  TW_CHECK_EQ(__builtin_popcountll(banks_plain), 1);
  TW_CHECK_EQ(__builtin_popcountll(banks_hashed), 16);
}

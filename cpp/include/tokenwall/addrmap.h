// Address mapping mirror of python/tokenwall/addrmap.py. Same policies, same
// bit layouts, same XOR rules; tests/test_cross_addrmap.py compares the two.
#pragma once

#include <array>
#include <cstdint>
#include <string>
#include <vector>

namespace tokenwall {

enum Field : int { CHANNEL = 0, PSEUDO_CHANNEL, SID, BANK_GROUP, BANK, ROW, COLUMN, FIELD_COUNT };

using AddrVec = std::array<uint32_t, FIELD_COUNT>;  // Ramulator HBM3 order

struct Geometry {
  uint32_t channels = 16;
  uint32_t pseudo_channels = 2;
  uint32_t sids = 2;
  uint32_t bank_groups = 4;
  uint32_t banks = 4;
  uint32_t rows = 16384;
  uint32_t lines_per_row = 32;  // 32 B accesses per 1 KiB row
  uint32_t line_bytes = 32;

  uint32_t count(Field f) const;
  int bits(Field f) const;  // log2(count), counts must be powers of two
  int line_shift() const;
  int total_bits() const;
  uint64_t capacity_bytes() const;
  uint64_t banks_total() const;
  uint64_t flat_bank(const AddrVec& v) const;
};

// The HBM3_16Gb_8hi preset from configs/hbm3 (16 channels per stack).
Geometry geometry_for_stacks(int stacks);

struct LayoutPiece {
  Field field;
  int bits;
};

struct XorRule {
  Field target;
  Field source;
  int source_shift;
};

struct Policy {
  std::string name;
  std::vector<LayoutPiece> layout;  // LSB -> MSB of the line index
  std::vector<XorRule> xors;
  int interleave_log2 = 0;

  AddrVec map(uint64_t addr, const Geometry& g) const;
  uint64_t unmap(const AddrVec& v, const Geometry& g) const;
};

// name: ramulator | bank_low | bank_high | bank_low_xor
Policy make_policy(const std::string& name, const Geometry& g, int interleave_log2 = 0);

}  // namespace tokenwall

#include "tokenwall/addrmap.h"

#include <stdexcept>

namespace tokenwall {

namespace {
int log2_exact(uint32_t n, const char* what) {
  if (n == 0 || (n & (n - 1))) throw std::runtime_error(std::string(what) + " must be a power of two");
  int b = 0;
  while ((1u << b) < n) b++;
  return b;
}
}  // namespace

uint32_t Geometry::count(Field f) const {
  switch (f) {
    case CHANNEL: return channels;
    case PSEUDO_CHANNEL: return pseudo_channels;
    case SID: return sids;
    case BANK_GROUP: return bank_groups;
    case BANK: return banks;
    case ROW: return rows;
    case COLUMN: return lines_per_row;
    default: throw std::runtime_error("bad field");
  }
}

int Geometry::bits(Field f) const { return log2_exact(count(f), "field count"); }
int Geometry::line_shift() const { return log2_exact(line_bytes, "line_bytes"); }

int Geometry::total_bits() const {
  int t = 0;
  for (int f = 0; f < FIELD_COUNT; f++) t += bits(static_cast<Field>(f));
  return t;
}

uint64_t Geometry::capacity_bytes() const { return (uint64_t{1} << total_bits()) * line_bytes; }

uint64_t Geometry::banks_total() const {
  return uint64_t{channels} * pseudo_channels * sids * bank_groups * banks;
}

uint64_t Geometry::flat_bank(const AddrVec& v) const {
  uint64_t b = v[CHANNEL];
  b = b * pseudo_channels + v[PSEUDO_CHANNEL];
  b = b * sids + v[SID];
  b = b * bank_groups + v[BANK_GROUP];
  b = b * banks + v[BANK];
  return b;
}

Geometry geometry_for_stacks(int stacks) {
  if (stacks <= 0) throw std::runtime_error("stacks must be positive");
  Geometry g;
  g.channels = 16u * static_cast<uint32_t>(stacks);
  return g;
}

AddrVec Policy::map(uint64_t addr, const Geometry& g) const {
  uint64_t lines = addr >> g.line_shift();
  AddrVec v{};
  std::array<int, FIELD_COUNT> consumed{};
  for (const auto& p : layout) {
    if (p.bits == 0) continue;
    const uint64_t piece = lines & ((uint64_t{1} << p.bits) - 1);
    v[p.field] |= static_cast<uint32_t>(piece << consumed[p.field]);
    consumed[p.field] += p.bits;
    lines >>= p.bits;
  }
  if (!xors.empty()) {
    const AddrVec raw = v;
    for (const auto& r : xors) {
      const uint32_t mask = (1u << g.bits(r.target)) - 1;
      v[r.target] ^= (raw[r.source] >> r.source_shift) & mask;
    }
  }
  return v;
}

uint64_t Policy::unmap(const AddrVec& in, const Geometry& g) const {
  AddrVec v = in;
  for (const auto& r : xors) {  // sources are never targets: xor is its own inverse
    const uint32_t mask = (1u << g.bits(r.target)) - 1;
    v[r.target] ^= (v[r.source] >> r.source_shift) & mask;
  }
  uint64_t lines = 0;
  int pos = 0;
  std::array<int, FIELD_COUNT> consumed{};
  for (const auto& p : layout) {
    if (p.bits == 0) continue;
    const uint64_t piece = (uint64_t{v[p.field]} >> consumed[p.field]) & ((uint64_t{1} << p.bits) - 1);
    lines |= piece << pos;
    pos += p.bits;
    consumed[p.field] += p.bits;
  }
  return lines << g.line_shift();
}

Policy make_policy(const std::string& name, const Geometry& g, int interleave_log2) {
  const int c = g.bits(CHANNEL), col = g.bits(COLUMN);
  if (interleave_log2 < 0 || interleave_log2 > col) throw std::runtime_error("interleave_log2 out of range");
  const int pc = g.bits(PSEUDO_CHANNEL), sid = g.bits(SID), bg = g.bits(BANK_GROUP), ba = g.bits(BANK), row = g.bits(ROW);
  Policy p;
  p.name = name;
  p.interleave_log2 = interleave_log2;
  const LayoutPiece low{COLUMN, interleave_log2}, ch{CHANNEL, c}, rest{COLUMN, col - interleave_log2};
  std::vector<LayoutPiece> layout;
  if (name == "ramulator") {
    layout = {low, ch, rest, {PSEUDO_CHANNEL, pc}, {SID, sid}, {BANK_GROUP, bg}, {BANK, ba}, {ROW, row}};
  } else if (name == "bank_low" || name == "bank_low_xor") {
    layout = {low, ch, {PSEUDO_CHANNEL, pc}, {BANK_GROUP, bg}, {BANK, ba}, {SID, sid}, rest, {ROW, row}};
    if (name == "bank_low_xor") p.xors = {{BANK_GROUP, ROW, 0}, {BANK, ROW, bg}};
  } else if (name == "bank_high") {
    layout = {low, ch, rest, {PSEUDO_CHANNEL, pc}, {ROW, row}, {SID, sid}, {BANK_GROUP, bg}, {BANK, ba}};
  } else {
    throw std::runtime_error("unknown policy " + name);
  }
  for (const auto& piece : layout)
    if (piece.bits > 0) p.layout.push_back(piece);
  return p;
}

}  // namespace tokenwall

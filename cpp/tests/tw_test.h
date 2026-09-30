// Minimal test harness: no dependencies, so the project stays laptop-friendly.
// Usage:
//   TW_TEST(name) { TW_CHECK(cond); TW_CHECK_EQ(a, b); }
//   TW_TEST_MAIN()
#pragma once

#include <cstdio>
#include <functional>
#include <string>
#include <vector>

namespace tw_test {

struct Case {
  std::string name;
  std::function<void()> fn;
};

inline std::vector<Case>& registry() {
  static std::vector<Case> r;
  return r;
}
inline int& failures() {
  static int f = 0;
  return f;
}

struct Registrar {
  Registrar(const char* name, std::function<void()> fn) { registry().push_back({name, std::move(fn)}); }
};

inline int run_all() {
  int failed_cases = 0;
  for (auto& c : registry()) {
    const int before = failures();
    c.fn();
    const bool ok = failures() == before;
    std::printf("[%s] %s\n", ok ? " OK " : "FAIL", c.name.c_str());
    if (!ok) failed_cases++;
  }
  std::printf("%zu cases, %d failed\n", registry().size(), failed_cases);
  return failed_cases == 0 ? 0 : 1;
}

}  // namespace tw_test

#define TW_TEST(name)                                                  \
  static void tw_fn_##name();                                          \
  static tw_test::Registrar tw_reg_##name(#name, tw_fn_##name);        \
  static void tw_fn_##name()

#define TW_CHECK(cond)                                                              \
  do {                                                                              \
    if (!(cond)) {                                                                  \
      std::printf("  CHECK failed: %s (%s:%d)\n", #cond, __FILE__, __LINE__);       \
      tw_test::failures()++;                                                        \
    }                                                                               \
  } while (0)

#define TW_CHECK_EQ(a, b)                                                                     \
  do {                                                                                        \
    const auto tw_a = (a);                                                                    \
    const auto tw_b = (b);                                                                    \
    if (!(tw_a == tw_b)) {                                                                    \
      std::printf("  CHECK_EQ failed: %s == %s -> %lld vs %lld (%s:%d)\n", #a, #b,            \
                  static_cast<long long>(tw_a), static_cast<long long>(tw_b), __FILE__, __LINE__); \
      tw_test::failures()++;                                                                  \
    }                                                                                         \
  } while (0)

#define TW_TEST_MAIN() \
  int main() { return tw_test::run_all(); }

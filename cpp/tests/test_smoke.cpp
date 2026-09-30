#include <string>

#include "tokenwall/version.h"
#include "tw_test.h"

TW_TEST(version_string_is_set) {
  TW_CHECK(!std::string(tokenwall::kVersion).empty());
}

TW_TEST_MAIN()

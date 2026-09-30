# Timing core (Phase 3). Header-only for now; becomes a STATIC library once
# the bank state machine lands.
add_library(tokenwall_core INTERFACE)
target_include_directories(tokenwall_core INTERFACE ${CMAKE_CURRENT_SOURCE_DIR}/include)

# Command-line driver.
add_executable(tokenwall src/main.cpp)
target_link_libraries(tokenwall PRIVATE tokenwall_core)

# Unit tests: one executable, registered with CTest.
add_executable(tw_tests tests/test_smoke.cpp)
target_link_libraries(tw_tests PRIVATE tokenwall_core)
target_include_directories(tw_tests PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/tests)
add_test(NAME tw_tests COMMAND tw_tests)

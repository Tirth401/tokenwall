# Timing core (Phase 3) plus the shared trace-format library (Phase 1).
add_library(tokenwall_core STATIC src/segments.cpp)
target_include_directories(tokenwall_core PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)

# Command-line driver for the timing core (arrives in Phase 3).
add_executable(tokenwall src/main.cpp)
target_link_libraries(tokenwall PRIVATE tokenwall_core)

# Expands a .segs trace: counts, stream hash, optional Ramulator text export.
add_executable(tw_expand src/tw_expand.cpp)
target_link_libraries(tw_expand PRIVATE tokenwall_core)

# Unit tests, registered with CTest.
add_executable(tw_tests tests/test_smoke.cpp tests/test_segments.cpp)
target_link_libraries(tw_tests PRIVATE tokenwall_core)
target_include_directories(tw_tests PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/tests)
add_test(NAME tw_tests COMMAND tw_tests)

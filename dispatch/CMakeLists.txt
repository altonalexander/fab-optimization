cmake_minimum_required(VERSION 3.16)
project(fabdisp CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

option(FAB_HAVE_RDKAFKA "Build with librdkafka transport" OFF)
option(FAB_HAVE_ORTOOLS "Build with OR-Tools CP-SAT solver" OFF)
option(FAB_HAVE_GUROBI  "Build with Gurobi MILP solver"     OFF)
option(FAB_HAVE_HIGHS   "Build with HiGHS MILP solver"      OFF)

add_executable(fabdisp src/e2e_main.cpp)
target_include_directories(fabdisp PRIVATE include)

# The test/benchmark binary. It is the thing that has to prove CP-SAT beats
# greedy, so it must be buildable with the same backend flags as the dispatcher.
add_executable(fabtest src/test_main.cpp)
target_include_directories(fabtest PRIVATE include)

find_package(Threads REQUIRED)
target_link_libraries(fabdisp PRIVATE Threads::Threads)
target_link_libraries(fabtest PRIVATE Threads::Threads)

if(FAB_HAVE_RDKAFKA)
  # apt install librdkafka-dev
  target_compile_definitions(fabdisp PRIVATE FAB_HAVE_RDKAFKA)
  target_link_libraries(fabdisp PRIVATE rdkafka++ rdkafka)
endif()

if(FAB_HAVE_ORTOOLS)
  # https://developers.google.com/optimization/install/cpp
  find_package(ortools CONFIG REQUIRED)
  foreach(t fabdisp fabtest)
    target_compile_definitions(${t} PRIVATE FAB_HAVE_ORTOOLS)
    target_link_libraries(${t} PRIVATE ortools::ortools)
  endforeach()
endif()

if(FAB_HAVE_GUROBI)
  # Needs GUROBI_HOME and a reachable floating license server.
  target_compile_definitions(fabdisp PRIVATE FAB_HAVE_GUROBI)
  target_include_directories(fabdisp PRIVATE $ENV{GUROBI_HOME}/include)
  target_link_directories(fabdisp PRIVATE $ENV{GUROBI_HOME}/lib)
  target_link_libraries(fabdisp PRIVATE gurobi_c++ gurobi110)
endif()

if(FAB_HAVE_HIGHS)
  find_package(HIGHS CONFIG REQUIRED)
  target_compile_definitions(fabdisp PRIVATE FAB_HAVE_HIGHS)
  target_link_libraries(fabdisp PRIVATE highs::highs)
endif()

# Copy the tool master next to the binary so ./fabdisp works from the build dir
add_custom_command(TARGET fabdisp POST_BUILD
  COMMAND ${CMAKE_COMMAND} -E copy_directory
          ${CMAKE_SOURCE_DIR}/config $<TARGET_FILE_DIR:fabdisp>/config)

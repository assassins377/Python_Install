cmake_minimum_required(VERSION 3.24)
project(MInstAllNative VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)
option(MINSTALL_BUILD_GUI "Build the wxWidgets catalog browser" ON)
option(MINSTALL_COVERAGE "Instrument native code for GCC coverage" OFF)
option(MINSTALL_SANITIZERS "Enable address/undefined sanitizers (GCC/Clang)" OFF)

if(MSVC)
    # Experimental legacy candidate: static CRT is not a compatibility guarantee.
    set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
endif()
if(WIN32)
    add_compile_definitions(WINVER=0x0601 _WIN32_WINNT=0x0601 UNICODE _UNICODE NOMINMAX)
endif()

include(FetchContent)
set(JSON_BuildTests OFF CACHE BOOL "" FORCE)
set(JSON_Install OFF CACHE BOOL "" FORCE)
FetchContent_Declare(nlohmann_json
    URL https://github.com/nlohmann/json/releases/download/v3.12.0/json.tar.xz
    URL_HASH SHA256=42f6e95cad6ec532fd372391373363b62a14af6d771056dbfc86160e6dfff7aa
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE)
FetchContent_MakeAvailable(nlohmann_json)

add_library(minstall_options INTERFACE)
if(MSVC)
    target_compile_options(minstall_options INTERFACE /W4 /WX /permissive- /utf-8)
else()
    target_compile_options(minstall_options INTERFACE -Wall -Wextra -Wpedantic -Werror)
endif()
if(MINSTALL_COVERAGE)
    if(NOT CMAKE_CXX_COMPILER_ID STREQUAL "GNU")
        message(FATAL_ERROR "MINSTALL_COVERAGE requires GCC")
    endif()
    target_compile_options(minstall_options INTERFACE --coverage -O0 -g)
    target_link_options(minstall_options INTERFACE --coverage)
endif()
if(MINSTALL_SANITIZERS)
    if(MSVC)
        message(FATAL_ERROR "This sanitizer profile requires GCC or Clang")
    endif()
    target_compile_options(minstall_options INTERFACE -fsanitize=address,undefined -fno-omit-frame-pointer)
    target_link_options(minstall_options INTERFACE -fsanitize=address,undefined)
endif()

add_library(minstall_core src/catalog.cpp src/app.cpp src/platform.cpp)
target_include_directories(minstall_core PUBLIC include)
target_link_libraries(minstall_core PUBLIC nlohmann_json::nlohmann_json minstall_options)
# Console subsystem deliberately preserves stdout, redirection and exit codes.
add_executable(minstall-native src/main.cpp)
target_link_libraries(minstall-native PRIVATE minstall_core)
if(MINGW)
    target_link_options(minstall-native PRIVATE -municode)
endif()

if(MINSTALL_BUILD_GUI)
    set(wxBUILD_SHARED OFF CACHE BOOL "" FORCE)
    set(wxBUILD_INSTALL OFF CACHE BOOL "" FORCE)
    set(wxBUILD_SAMPLES OFF CACHE STRING "" FORCE)
    set(wxBUILD_TESTS OFF CACHE STRING "" FORCE)
    set(wxBUILD_DEMOS OFF CACHE BOOL "" FORCE)
    set(wxBUILD_LOCALES OFF CACHE BOOL "" FORCE)
    set(wxBUILD_CXX_STANDARD 17 CACHE STRING "" FORCE)
    if(MSVC)
        set(wxBUILD_USE_STATIC_RUNTIME ON CACHE BOOL "" FORCE)
    endif()
    foreach(feature WEBVIEW WEBREQUEST MEDIACTRL STC RICHTEXT PROPGRID RIBBON AUI XRC)
        set(wxUSE_${feature} OFF CACHE BOOL "" FORCE)
    endforeach()
    FetchContent_Declare(wxwidgets
        URL https://github.com/wxWidgets/wxWidgets/releases/download/v3.2.11/wxWidgets-3.2.11.tar.bz2
        URL_HASH SHA256=6a129015bce2e914e4bf61ec4411854ad962801d47e92f2eb8340adb6a90af08
        DOWNLOAD_EXTRACT_TIMESTAMP TRUE)
    FetchContent_MakeAvailable(wxwidgets)
    target_sources(minstall-native PRIVATE src/gui.cpp)
    target_compile_definitions(minstall-native PRIVATE MINSTALL_WITH_GUI)
    target_link_libraries(minstall-native PRIVATE wx::core wx::base)
endif()

set(catalog_source "${CMAKE_CURRENT_SOURCE_DIR}/../programs.json")
file(GENERATE OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/native-toolchain-$<CONFIG>.txt" CONTENT
"MInstAll Native ${PROJECT_VERSION}
CMake=${CMAKE_VERSION}
Compiler=${CMAKE_CXX_COMPILER_ID} ${CMAKE_CXX_COMPILER_VERSION}
Toolset=${CMAKE_VS_PLATFORM_TOOLSET_VERSION}
SDK=${CMAKE_VS_WINDOWS_TARGET_PLATFORM_VERSION}
PointerBytes=${CMAKE_SIZEOF_VOID_P}
Build=$<CONFIG>
CRT=${CMAKE_MSVC_RUNTIME_LIBRARY}
GUI=${MINSTALL_BUILD_GUI}
wxWidgets=3.2.11
JSON=3.12.0
Client Windows compatibility=UNTESTED
")
add_custom_command(TARGET minstall-native POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy_if_different "${catalog_source}" "$<TARGET_FILE_DIR:minstall-native>/programs.json"
    VERBATIM)

include(CTest)
if(BUILD_TESTING)
    add_executable(native-tests tests/catalog_tests.cpp)
    target_link_libraries(native-tests PRIVATE minstall_core)
    target_compile_definitions(native-tests PRIVATE MINSTALL_SOURCE_CATALOG="${catalog_source}")
    add_test(NAME catalog-and-cli COMMAND native-tests)
    add_test(NAME cli-process COMMAND ${CMAKE_COMMAND}
        "-DAPP=$<TARGET_FILE:minstall-native>" "-DTEST_DIR=${CMAKE_CURRENT_BINARY_DIR}/cli-tests"
        -P "${CMAKE_CURRENT_SOURCE_DIR}/tests/cli_smoke.cmake")
    if(MINSTALL_BUILD_GUI)
        if(WIN32)
            add_test(NAME gui-smoke COMMAND minstall-native --gui-smoke)
        elseif(UNIX)
            find_program(XVFB_RUN xvfb-run)
            if(XVFB_RUN)
                add_test(NAME gui-smoke COMMAND "${XVFB_RUN}" -a $<TARGET_FILE:minstall-native> --gui-smoke)
            endif()
        endif()
    endif()
    set_tests_properties(catalog-and-cli cli-process PROPERTIES TIMEOUT 30)
    if(TEST gui-smoke)
        set_tests_properties(gui-smoke PROPERTIES TIMEOUT 30)
    endif()
endif()

install(TARGETS minstall-native RUNTIME DESTINATION .)
install(FILES "${catalog_source}" "${CMAKE_CURRENT_SOURCE_DIR}/../programs.schema.json"
              README.md WINDOWS_COMPATIBILITY.md DESTINATION .)
install(FILES "${CMAKE_CURRENT_BINARY_DIR}/native-toolchain-$<CONFIG>.txt" DESTINATION .)
install(FILES "${nlohmann_json_SOURCE_DIR}/LICENSE.MIT" DESTINATION licenses RENAME nlohmann-json.txt)
if(MINSTALL_BUILD_GUI)
    install(FILES "${wxwidgets_SOURCE_DIR}/docs/licence.txt" DESTINATION licenses RENAME wxwidgets.txt)
    install(FILES "${wxwidgets_SOURCE_DIR}/docs/lgpl.txt" DESTINATION licenses)
    install(FILES "${wxwidgets_SOURCE_DIR}/src/jpeg/README" DESTINATION licenses RENAME libjpeg.txt)
    install(FILES "${wxwidgets_SOURCE_DIR}/src/png/LICENSE" DESTINATION licenses RENAME libpng.txt)
    install(FILES "${wxwidgets_SOURCE_DIR}/src/tiff/COPYRIGHT" DESTINATION licenses RENAME libtiff.txt)
    install(FILES "${wxwidgets_SOURCE_DIR}/src/zlib/LICENSE" DESTINATION licenses RENAME zlib.txt)
    install(FILES "${wxwidgets_SOURCE_DIR}/src/expat/expat/COPYING" DESTINATION licenses RENAME expat.txt)
    install(FILES "${wxwidgets_SOURCE_DIR}/3rdparty/pcre/LICENCE" DESTINATION licenses RENAME pcre2.txt)
    install(FILES "${wxwidgets_SOURCE_DIR}/3rdparty/nanosvg/LICENSE.txt" DESTINATION licenses RENAME nanosvg.txt)
endif()

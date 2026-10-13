#include "minstall/detection.hpp"

#include <fstream>
#include <limits>

#ifdef _WIN32
#include <windows.h>
#endif

namespace {
namespace fs = std::filesystem;
using minstall::InstallationState;
using minstall::Platform;
int checks = 0;

void check(bool condition, const char* message) {
    ++checks;
    if (!condition) throw std::runtime_error(message);
}

minstall::Program program(const std::string& name, minstall::Json detect = minstall::Json::object()) {
    return {minstall::Json{{"name", name}, {"cmd", "never-execute.exe /S"}, {"os", "any"},
                           {"detect", std::move(detect)}}};
}

void matching_tests() {
    minstall::InstalledSnapshot snapshot;
    snapshot.platform = Platform::windows;
    snapshot.registry_complete = true;
    snapshot.entries = {{"7-Zip 25.01 (x64 edition)", "25.01"}, {"Mozilla Firefox", "133.0.2"},
                        {"ПРОГРАММА ЁЖ", "2.0"}, {"CAFÉ", "1.0"}, {"App2", "9.0"},
                        {"App Helper", "9.0"}, {"App Beta", "9.0"}, {"App edition", "9.0"},
                        {"Visual C++", "14.0"}, {"A 64-bit", "3.0"}};
    auto status = minstall::detect_program(program("7Zip"), snapshot);
    check(status.state == InstallationState::installed && status.installed_version == "25.01",
          "Version/architecture suffix of registered display name is recognized");
    check(minstall::detect_program(program("a"), snapshot).installed_version == "3.0",
          "Standalone architecture suffix recognized");
    check(minstall::detect_program(program("App"), snapshot).state == InstallationState::missing,
          "Prefix collisions, helper/beta labels and unqualified editions are not matches");
    check(minstall::detect_program(program("Visual C"), snapshot).state == InstallationState::missing,
          "Meaningful plus signs are retained during name matching");
    check(minstall::detect_program(program("программа-ёж"), snapshot).installed_version == "2.0",
          "UTF-8 Cyrillic case and separators match without ANSI conversion");
    check(minstall::detect_program(program("café"), snapshot).installed_version == "1.0",
          "Latin accented display names are folded deterministically");
    check(minstall::detect_program(program("InstallerFilename", {{"registry_name", "mozilla_firefox"}}), snapshot)
              .installed_version == "133.0.2", "Explicit registration name overrides installer label");
    check(minstall::detect_program(program("firefox"), snapshot).state == InstallationState::missing,
          "Broad substring matches are intentionally excluded");
    check(minstall::detect_program(program(" \t---"), snapshot).state == InstallationState::unknown,
          "Empty normalized name cannot prove absence");

    snapshot.registry_complete = false;
    check(minstall::detect_program(program("Absent"), snapshot).state == InstallationState::unknown,
          "Access errors/incomplete enumeration do not prove absence");
    check(minstall::detect_program(program("7-Zip"), snapshot).state == InstallationState::installed,
          "Positive matches remain usable with incomplete enumeration");
    auto partial = snapshot;
    partial.entries = {{"App", "1.0"}};
    status = minstall::detect_program(program("App", {{"min_version", "1.5"}}), partial);
    check(status.state == InstallationState::installed && status.installed_version == "1.0" && !status.outdated &&
          status.detail.find("incomplete") != std::string::npos,
          "Partial registry evidence preserves positive observed version but cannot exclude a newer unread installation");
    snapshot.platform = Platform::linux;
    snapshot.registry_complete = true;
    check(minstall::detect_program(program("Absent"), snapshot).state == InstallationState::unknown,
          "Unsupported registry backend cannot report missing on Linux");

    snapshot.platform = Platform::windows;
    snapshot.entries = {{"Same", "1.0"}, {"Same", "2.0"}};
    status = minstall::detect_program(program("Same", {{"min_version", "3.0"}}), snapshot);
    check(status.state == InstallationState::installed && status.installed_version.empty() && !status.outdated,
          "Ambiguous versions show installed without inventing a version or update requirement");
    snapshot.entries[1].version = "1.0";
    status = minstall::detect_program(program("Same", {{"min_version", "1.1"}}), snapshot);
    check(status.installed_version == "1.0" && status.outdated, "Duplicate same-version registrations agree");
    snapshot.entries = {{"Same 2.0 (x64)", "2.0"}, {"Same", "1.0"}};
    check(minstall::detect_program(program("Same"), snapshot).installed_version == "1.0",
          "Exact registered name takes precedence over suffixed fallback");
    snapshot.entries = {{"Same", "1.0-rc1"}};
    check(!minstall::detect_program(program("Same", {{"min_version", "1.1"}}), snapshot).outdated,
          "Prerelease labels are not guessed to be older/newer");
}

void version_tests() {
    check(minstall::compare_versions("1.0", "1") == 0, "Release zero padding agrees");
    check(minstall::compare_versions("  v01.002 ", "1.2.0") == 0, "Numeric labels normalized");
    check(minstall::compare_versions("1.9", "1.10") == -1, "Numeric ordering is not lexicographic");
    check(minstall::compare_versions("99999999999999999999999999", "9999999999999999999999999") == 1,
          "Huge version components cannot overflow integers");
    for (const auto* invalid : {"", "v", "1.", ".1", "1..2", "1.0rc1", "unknown", "1.0-rc1", "1,2"})
        check(!minstall::compare_versions(invalid, "1.0"), "Unrecognized version format remains incomparable");
}

void rule_tests() {
    minstall::InstalledSnapshot snapshot;
    snapshot.platform = Platform::windows;
    snapshot.registry_complete = true;
    snapshot.entries = {{"Exists", "2.0"}};
    snapshot.paths = {{"existing", InstallationState::installed}, {"missing", InstallationState::missing},
                      {"denied", InstallationState::unknown}};
    auto status = minstall::detect_program(program("Exists", {{"path", "missing"}}), snapshot);
    check(status.state == InstallationState::missing && status.installed_version.empty(),
          "Explicit path rule takes precedence over a matching registration");
    status = minstall::detect_program(program("Absent", {{"path", "existing"}, {"min_version", "9"}}), snapshot);
    check(status.state == InstallationState::installed && status.installed_version.empty() && !status.outdated,
          "Existing path proves presence but cannot supply a version");
    check(minstall::detect_program(program("Exists", {{"path", "unprobed"}}), snapshot).state == InstallationState::unknown,
          "Absent path evidence is unknown");
    check(minstall::detect_program(program("Exists", {{"path", "denied"}}), snapshot).state == InstallationState::unknown,
          "A path permission error is unknown");
    status = minstall::detect_program(program("Exists", {{"always_runnable", true}, {"path", "missing"}}), snapshot);
    check(status.runnable && status.state == InstallationState::unknown,
          "Always runnable takes precedence without pretending to be installed");

    const auto framework = program("Framework", {{"net_framework_release", 528040}, {"path", "existing"}});
    check(minstall::detect_program(framework, snapshot).state == InstallationState::unknown,
          ".NET detection requires complete evidence and precedes path probing");
    snapshot.net_framework_complete = true;
    check(minstall::detect_program(framework, snapshot).state == InstallationState::missing,
          "Complete .NET snapshot without release proves absence");
    snapshot.net_framework_release = 461808;
    status = minstall::detect_program(framework, snapshot);
    check(status.state == InstallationState::installed && status.outdated && status.installed_version == "461808",
          ".NET release evidence and minimum threshold retained");
    snapshot.net_framework_complete = false;
    check(!minstall::detect_program(framework, snapshot).outdated, "Partial .NET evidence does not prove an update requirement");
    for (const auto& invalid : std::vector<minstall::Json>{-1, 4294967296ULL, 528040.5,
             std::numeric_limits<double>::infinity(), std::numeric_limits<double>::quiet_NaN(), "528040"})
        check(minstall::detect_program(program("Framework", {{"net_framework_release", invalid}}), snapshot)
                  .state == InstallationState::unknown, "Malformed injected .NET threshold remains unknown without unsafe casts");
    snapshot.platform = Platform::linux;
    status = minstall::detect_program(framework, snapshot);
    check(!status.applicable && status.state == InstallationState::unknown,
          ".NET Framework rule is inapplicable outside Windows");

    auto windows = program("Windows");
    windows.fields.erase("os");
    windows.fields["source_path"] = "Каталог/installer.EXE";
    check(!minstall::is_program_applicable(windows, Platform::linux), "Source extension indicates Windows applicability");
    check(minstall::is_program_applicable(windows, Platform::windows), "Windows installer applicable on Windows");
    windows.fields["os"] = "any";
    check(minstall::is_program_applicable(windows, Platform::linux), "Explicit any overrides extension inference");
    windows.fields["os"] = "windows";
    check(!minstall::is_program_applicable(windows, Platform::linux), "Explicit Windows platform respected");
    auto linux = program("Linux");
    linux.fields.erase("os");
    linux.fields["cmd"] = "\"software/space name.AppImage\" --arg";
    check(minstall::is_program_applicable(linux, Platform::linux) &&
          !minstall::is_program_applicable(linux, Platform::windows), "Quoted command extension read as inert metadata");
    linux.fields["cmd"] = "apt-get install dangerous-package";
    check(!minstall::is_program_applicable(linux, Platform::windows), "Bare package-manager platform recognized without execution");

    minstall::Catalog catalog{{{"One", {windows}}, {"Two", {program("Exists"), framework}}}, minstall::Json::object()};
    const auto statuses = minstall::detect_catalog(catalog, snapshot);
    check(statuses.size() == 2 && statuses[0].size() == 1 && statuses[1].size() == 2 && catalog.program_count() == 3,
          "Detection preserves category/program alignment, including inapplicable records");
    check(minstall::detect_catalog({}, snapshot).empty(), "Installed registrations cannot populate an empty catalog");
    check(std::string(minstall::installation_state_name(InstallationState::unknown)) == "unknown" &&
          std::string(minstall::installation_state_name(InstallationState::missing)) == "missing" &&
          std::string(minstall::installation_state_name(InstallationState::installed)) == "installed", "Stable machine-readable tri-state names");
}

void path_collection_tests() {
    const auto base = fs::current_path() / "detection-test-fixture";
    check(fs::create_directory(base), "Exclusive detection fixture directory");
    struct Cleanup { fs::path path; ~Cleanup() { std::error_code error; fs::remove_all(path, error); } } cleanup{base};
    const std::string relative = "Программа/installed.bin";
    fs::create_directories((base / fs::u8path(relative)).parent_path());
    std::ofstream(base / fs::u8path(relative)) << "inert detection fixture";
    const auto absolute = (base / fs::u8path(relative)).u8string();
    minstall::Catalog catalog{{{"Detection", {
        program("Relative", {{"path", relative}}), program("Absolute", {{"path", absolute}}),
        program("Missing", {{"path", "missing.bin"}}),
        program("Unresolved", {{"path", "%MINSTALL_DETECTION_UNSET_64FBE9F3%/file"}}),
        program("CommandOnly")}}}, minstall::Json::object()};
    // A shell-shaped command is never opened/executed by the collector.
    catalog.categories[0].programs.back().fields["cmd"] = "touch detection-must-not-run";
    const auto snapshot = minstall::collect_installed_snapshot(catalog, base);
    check(snapshot.paths.at(relative) == InstallationState::installed &&
          snapshot.paths.at(absolute) == InstallationState::installed, "Unicode relative and absolute detection paths probed");
    check(snapshot.paths.at("missing.bin") == InstallationState::missing, "Explicit nonexistent path classified missing");
    check(snapshot.paths.at("%MINSTALL_DETECTION_UNSET_64FBE9F3%/file") == InstallationState::unknown,
          "Unresolved environment variable cannot prove missing");
    check(snapshot.paths.size() == 4 && !fs::exists(base / "detection-must-not-run") &&
          !fs::exists(fs::current_path() / "detection-must-not-run"), "No installer commands probed or executed");
    check(catalog.program_count() == 5, "Collection does not alter catalog membership");
#ifndef _WIN32
    check(!snapshot.registry_complete && !snapshot.net_framework_complete && snapshot.entries.empty(),
          "Non-Windows collector reports registry backend unavailable, not an empty complete snapshot");
#endif
}

#ifdef _WIN32
void windows_registry_fixture_test() {
    const auto suffix = std::to_string(GetCurrentProcessId()) + "-" + std::to_string(GetTickCount64());
    const std::wstring key_name = L"MInstAllNativeDetectionFixture-" + std::wstring(suffix.begin(), suffix.end());
    const std::wstring parent_path = L"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall";
    const auto full_path = parent_path + L"\\" + key_name;
    constexpr REGSAM view = KEY_WOW64_32KEY;
    HKEY fixture = nullptr;
    DWORD disposition = 0;
    check(RegCreateKeyExW(HKEY_CURRENT_USER, full_path.c_str(), 0, nullptr, 0, KEY_WRITE | view,
                          nullptr, &fixture, &disposition) == ERROR_SUCCESS, "Create disposable HKCU registry fixture");
    if (disposition != REG_CREATED_NEW_KEY) {
        RegCloseKey(fixture);
        throw std::runtime_error("Registry fixture name already exists; never overwrite it");
    }
    struct Cleanup {
        HKEY fixture;
        std::wstring path;
        ~Cleanup() {
            if (fixture) RegCloseKey(fixture);
            if (!path.empty()) RegDeleteKeyExW(HKEY_CURRENT_USER, path.c_str(), KEY_WOW64_32KEY, 0);
        }
    } cleanup{fixture, full_path};
    const std::wstring display = L"MInstAll Native — Программа Ёж " + std::wstring(suffix.begin(), suffix.end());
    const std::wstring version = L"12.34.56";
    check(RegSetValueExW(fixture, L"DisplayName", 0, REG_SZ, reinterpret_cast<const BYTE*>(display.c_str()),
                         static_cast<DWORD>((display.size() + 1) * sizeof(wchar_t))) == ERROR_SUCCESS,
          "Write Unicode fixture display name");
    check(RegSetValueExW(fixture, L"DisplayVersion", 0, REG_SZ, reinterpret_cast<const BYTE*>(version.c_str()),
                         static_cast<DWORD>((version.size() + 1) * sizeof(wchar_t))) == ERROR_SUCCESS,
          "Write fixture display version");
    const auto entry = program("MInstAll Native — Программа Ёж " + suffix);
    const minstall::Catalog catalog{{{"Registry fixture", {entry}}}, minstall::Json::object()};
    const auto status = minstall::detect_program(entry, minstall::collect_installed_snapshot(catalog));
    check(status.state == InstallationState::installed && status.installed_version == "12.34.56",
          "Live HKCU 32-bit view and Unicode registry reading find the disposable fixture");
    RegCloseKey(fixture);
    cleanup.fixture = nullptr;
    check(RegDeleteKeyExW(HKEY_CURRENT_USER, full_path.c_str(), view, 0) == ERROR_SUCCESS,
          "Delete only the disposable registry fixture in its original view");
    cleanup.path.clear();
}
#endif
}  // namespace

int detection_tests() {
    matching_tests();
    version_tests();
    rule_tests();
    path_collection_tests();
#ifdef _WIN32
    windows_registry_fixture_test();
#endif
    return checks;
}

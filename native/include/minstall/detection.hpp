#pragma once

#include "minstall/catalog.hpp"

#include <cstdint>
#include <map>
#include <optional>

namespace minstall {
enum class Platform { windows, linux, other };
enum class InstallationState { unknown, missing, installed };

struct InstalledEntry {
    std::string name;
    std::string version;
};

// A snapshot is inert data and can be injected into pure matching tests. An
// incomplete/unsupported source cannot prove that an unmatched program is absent.
struct InstalledSnapshot {
    Platform platform = Platform::other;
    bool registry_complete = false;
    std::vector<InstalledEntry> entries;
    std::map<std::string, InstallationState> paths;
    bool net_framework_complete = false;
    std::optional<std::uint32_t> net_framework_release;
};

struct ProgramStatus {
    InstallationState state = InstallationState::unknown;
    std::string installed_version;
    bool applicable = true;
    bool outdated = false;
    bool runnable = false;
    std::string detail;
};

Platform current_platform();
const char* installation_state_name(InstallationState state);
bool is_program_applicable(const Program& program, Platform platform);
// Read HKLM/HKCU uninstall records in both Windows registry views once, and
// probe only explicit detect.path fields. Never inspect or execute commands.
// Relative detection paths are resolved against relative_path_base (or cwd).
InstalledSnapshot collect_installed_snapshot(
    const Catalog& catalog, const std::filesystem::path& relative_path_base = {});
ProgramStatus detect_program(const Program& program, const InstalledSnapshot& snapshot);
// Results preserve catalog category/program order and never change membership.
std::vector<std::vector<ProgramStatus>> detect_catalog(
    const Catalog& catalog, const InstalledSnapshot& snapshot);
// Numeric release versions only; prerelease/unknown formats are not ordered.
std::optional<int> compare_versions(std::string_view left, std::string_view right);
}  // namespace minstall

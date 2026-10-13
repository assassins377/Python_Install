#include "minstall/detection.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdlib>
#include <limits>
#include <set>

#ifdef _WIN32
#include <windows.h>
#endif

namespace minstall {
namespace {
std::string text(const Json& fields, const char* key) {
    const auto found = fields.find(key);
    return found != fields.end() && found->is_string() ? found->get<std::string>() : std::string{};
}

const Json& detection_fields(const Program& program) {
    static const Json empty = Json::object();
    const auto found = program.fields.find("detect");
    return found != program.fields.end() && found->is_object() ? *found : empty;
}

std::string ascii_lower(std::string value) {
    for (auto& character : value) {
        if (character >= 'A' && character <= 'Z') character = static_cast<char>(character + ('a' - 'A'));
    }
    return value;
}

bool separator(std::uint32_t codepoint) {
    if (codepoint < 128) {
        return std::string_view(" \t\n\r\v\f._-/\\:;,!?()'\"").find(
                   static_cast<char>(codepoint)) != std::string_view::npos;
    }
    return codepoint == 0xa0 || (codepoint >= 0x2000 && codepoint <= 0x200a) ||
           codepoint == 0x202f || codepoint == 0x205f || codepoint == 0x3000;
}

// Deterministic UTF-8 simple folding for common Latin, Greek and Cyrillic
// display names. Other scripts remain intact; no locale/ANSI conversion occurs.
std::uint32_t fold(std::uint32_t codepoint) {
    if ((codepoint >= 'A' && codepoint <= 'Z') ||
        (codepoint >= 0xc0 && codepoint <= 0xd6) ||
        (codepoint >= 0xd8 && codepoint <= 0xde) ||
        (codepoint >= 0x391 && codepoint <= 0x3a1) ||
        (codepoint >= 0x3a3 && codepoint <= 0x3ab) ||
        (codepoint >= 0x410 && codepoint <= 0x42f)) return codepoint + 0x20;
    if (codepoint >= 0x400 && codepoint <= 0x40f) return codepoint + 0x50;
    if (codepoint == 0x3c2) return 0x3c3;
    return codepoint;
}

void append_utf8(std::string& output, std::uint32_t codepoint) {
    if (codepoint < 0x80) output += static_cast<char>(codepoint);
    else if (codepoint < 0x800) {
        output += static_cast<char>(0xc0 | (codepoint >> 6));
        output += static_cast<char>(0x80 | (codepoint & 0x3f));
    } else if (codepoint < 0x10000) {
        output += static_cast<char>(0xe0 | (codepoint >> 12));
        output += static_cast<char>(0x80 | ((codepoint >> 6) & 0x3f));
        output += static_cast<char>(0x80 | (codepoint & 0x3f));
    } else {
        output += static_cast<char>(0xf0 | (codepoint >> 18));
        output += static_cast<char>(0x80 | ((codepoint >> 12) & 0x3f));
        output += static_cast<char>(0x80 | ((codepoint >> 6) & 0x3f));
        output += static_cast<char>(0x80 | (codepoint & 0x3f));
    }
}

struct NormalizedName {
    std::string value;
    std::set<std::size_t> boundaries;
};

NormalizedName normalize(std::string_view input) {
    NormalizedName result;
    for (std::size_t position = 0; position < input.size();) {
        const auto first = static_cast<unsigned char>(input[position++]);
        std::uint32_t codepoint = first;
        unsigned count = 0;
        std::uint32_t minimum = 0;
        if (first >= 0xc2 && first <= 0xdf) { count = 1; codepoint &= 0x1f; minimum = 0x80; }
        else if (first >= 0xe0 && first <= 0xef) { count = 2; codepoint &= 0x0f; minimum = 0x800; }
        else if (first >= 0xf0 && first <= 0xf4) { count = 3; codepoint &= 0x07; minimum = 0x10000; }
        else if (first >= 0x80) return {};
        for (unsigned index = 0; index < count; ++index) {
            if (position == input.size()) return {};
            const auto next = static_cast<unsigned char>(input[position++]);
            if ((next & 0xc0) != 0x80) return {};
            codepoint = (codepoint << 6) | (next & 0x3f);
        }
        if (codepoint < minimum || codepoint > 0x10ffff ||
            (codepoint >= 0xd800 && codepoint <= 0xdfff) || codepoint == 0) return {};
        if (separator(codepoint)) result.boundaries.insert(result.value.size());
        else append_utf8(result.value, fold(codepoint));
    }
    return result;
}

bool release_suffix(std::string_view suffix) {
    // Permit common registered labels such as "7-Zip 25.01 (x64 edition)".
    // "App2", "App Helper", "App Beta" never satisfy this fallback.
    if (suffix.empty()) return false;
    for (const auto architecture : {"x64", "x86", "64bit", "32bit"}) {
        const std::string_view token = architecture;
        if (suffix.substr(0, token.size()) == token &&
            (suffix.size() == token.size() || suffix.substr(token.size()) == "edition")) return true;
    }
    if (suffix[0] == 'v' && suffix.size() > 1 && suffix[1] >= '0' && suffix[1] <= '9')
        suffix.remove_prefix(1);
    const auto original_size = suffix.size();
    while (!suffix.empty() && suffix[0] >= '0' && suffix[0] <= '9') suffix.remove_prefix(1);
    if (suffix.size() == original_size) return false;
    for (const auto architecture : {"x64", "x86", "64bit", "32bit"}) {
        const std::string_view token = architecture;
        if (suffix.substr(0, token.size()) == token) {
            suffix.remove_prefix(token.size());
            break;
        }
    }
    return suffix.empty() || suffix == "edition";
}

std::optional<Platform> inferred_platform(const Program& program) {
    auto source = text(program.fields, "source_path");
    if (source.empty()) {
        auto command = text(program.fields, "cmd");
        const auto start = command.find_first_not_of(" \t\r\n");
        if (start == std::string::npos) return std::nullopt;
        if (command[start] == '"' || command[start] == '\'') {
            const auto end = command.find(command[start], start + 1);
            if (end == std::string::npos) return std::nullopt;
            source = command.substr(start + 1, end - start - 1);
        } else source = command.substr(start, command.find_first_of(" \t\r\n", start) - start);
    }
    std::replace(source.begin(), source.end(), '\\', '/');
    const auto slash = source.find_last_of('/');
    const auto base = ascii_lower(source.substr(slash == std::string::npos ? 0 : slash + 1));
    const auto dot = base.find_last_of('.');
    const auto extension = dot == std::string::npos ? std::string{} : base.substr(dot);
    for (const auto* item : {".exe", ".msi", ".reg", ".bat", ".cmd", ".ps1"})
        if (extension == item) return Platform::windows;
    for (const auto* item : {".deb", ".appimage", ".sh", ".bash"})
        if (extension == item) return Platform::linux;
    if (base == "winget" || base == "choco") return Platform::windows;
    for (const auto* item : {"apt", "apt-get", "dpkg", "snap", "flatpak", "rpm", "pacman"})
        if (base == item) return Platform::linux;
    return std::nullopt;
}

std::optional<std::vector<std::string>> version_parts(std::string_view value) {
    const auto begin = value.find_first_not_of(" \t\r\n");
    if (begin == std::string_view::npos) return std::nullopt;
    value = value.substr(begin, value.find_last_not_of(" \t\r\n") - begin + 1);
    if (value[0] == 'v' || value[0] == 'V') value.remove_prefix(1);
    std::vector<std::string> result;
    while (!value.empty()) {
        const auto end = value.find_first_not_of("0123456789");
        const auto length = end == std::string_view::npos ? value.size() : end;
        if (!length) return std::nullopt;
        auto part = value.substr(0, length);
        const auto significant = part.find_first_not_of('0');
        result.emplace_back(significant == std::string_view::npos ? "0" : part.substr(significant));
        if (length == value.size()) break;
        if (value[length] != '.' || length + 1 == value.size()) return std::nullopt;
        value.remove_prefix(length + 1);
    }
    return result.empty() ? std::nullopt : std::make_optional(std::move(result));
}

std::optional<std::uint32_t> framework_threshold(const Json& value) {
    if (value.is_number_unsigned()) {
        const auto release = value.get<std::uint64_t>();
        if (release <= std::numeric_limits<std::uint32_t>::max()) return static_cast<std::uint32_t>(release);
    } else if (value.is_number_integer()) {
        const auto release = value.get<std::int64_t>();
        if (release >= 0 && release <= std::numeric_limits<std::uint32_t>::max())
            return static_cast<std::uint32_t>(release);
    } else if (value.is_number_float()) {
        const auto release = value.get<double>();
        if (std::isfinite(release) && std::floor(release) == release && release >= 0 &&
            release <= std::numeric_limits<std::uint32_t>::max()) return static_cast<std::uint32_t>(release);
    }
    return std::nullopt;
}

#ifdef _WIN32
std::optional<std::wstring> wide(std::string_view value) {
    if (value.size() > static_cast<std::size_t>(std::numeric_limits<int>::max())) return std::nullopt;
    const auto length = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, value.data(),
                                          static_cast<int>(value.size()), nullptr, 0);
    if (!length) return std::nullopt;
    std::wstring output(static_cast<std::size_t>(length), L'\0');
    if (MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, value.data(),
                            static_cast<int>(value.size()), output.data(), length) != length) return std::nullopt;
    return output;
}

std::optional<std::string> utf8(const std::wstring& value) {
    if (value.empty()) return std::string{};
    const auto length = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, value.data(),
                                           static_cast<int>(value.size()), nullptr, 0, nullptr, nullptr);
    if (!length) return std::nullopt;
    std::string output(static_cast<std::size_t>(length), '\0');
    if (WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, value.data(), static_cast<int>(value.size()),
                            output.data(), length, nullptr, nullptr) != length) return std::nullopt;
    return output;
}

struct RegistryKey {
    HKEY value = nullptr;
    RegistryKey() = default;
    RegistryKey(const RegistryKey&) = delete;
    RegistryKey& operator=(const RegistryKey&) = delete;
    ~RegistryKey() { if (value) RegCloseKey(value); }
};

struct RegistryString {
    bool complete = true;
    std::optional<std::string> value;
};

RegistryString registry_string(HKEY key, const wchar_t* name) {
    DWORD type = 0;
    DWORD bytes = 0;
    auto result = RegQueryValueExW(key, name, nullptr, &type, nullptr, &bytes);
    if (result == ERROR_FILE_NOT_FOUND) return {};
    if (result != ERROR_SUCCESS || (type != REG_SZ && type != REG_EXPAND_SZ) ||
        bytes > 65536 || bytes % sizeof(wchar_t)) return {false, std::nullopt};
    std::wstring buffer(static_cast<std::size_t>(bytes / sizeof(wchar_t)) + 1, L'\0');
    result = RegQueryValueExW(key, name, nullptr, &type, reinterpret_cast<BYTE*>(buffer.data()), &bytes);
    if (result != ERROR_SUCCESS || (type != REG_SZ && type != REG_EXPAND_SZ) ||
        bytes > 65536 || bytes % sizeof(wchar_t)) return {false, std::nullopt};
    buffer.resize(static_cast<std::size_t>(bytes / sizeof(wchar_t)));
    while (!buffer.empty() && buffer.back() == L'\0') buffer.pop_back();
    if (buffer.find(L'\0') != std::wstring::npos) return {false, std::nullopt};
    auto converted = utf8(buffer);
    return {converted.has_value(), std::move(converted)};
}

void read_uninstall_view(InstalledSnapshot& snapshot, HKEY hive, REGSAM view, std::size_t& used_bytes) {
    RegistryKey root;
    const auto opened = RegOpenKeyExW(hive, L"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall",
                                      0, KEY_ENUMERATE_SUB_KEYS | view, &root.value);
    if (opened == ERROR_FILE_NOT_FOUND || opened == ERROR_PATH_NOT_FOUND) return;
    if (opened != ERROR_SUCCESS) { snapshot.registry_complete = false; return; }
    constexpr DWORD limit = 20000;
    for (DWORD index = 0; index < limit; ++index) {
        std::array<wchar_t, 256> subkey{};
        DWORD length = static_cast<DWORD>(subkey.size());
        const auto enumerated = RegEnumKeyExW(root.value, index, subkey.data(), &length,
                                             nullptr, nullptr, nullptr, nullptr);
        if (enumerated == ERROR_NO_MORE_ITEMS) return;
        if (enumerated != ERROR_SUCCESS) { snapshot.registry_complete = false; return; }
        RegistryKey entry;
        if (RegOpenKeyExW(root.value, subkey.data(), 0, KEY_QUERY_VALUE | view, &entry.value) != ERROR_SUCCESS) {
            snapshot.registry_complete = false;
            continue;
        }
        auto name = registry_string(entry.value, L"DisplayName");
        if (!name.complete) snapshot.registry_complete = false;
        if (!name.value || name.value->empty()) continue;
        const auto version = registry_string(entry.value, L"DisplayVersion");
        const auto version_text = version.value.value_or(std::string{});
        const auto bytes = name.value->size() + version_text.size();
        constexpr std::size_t byte_limit = 8 * 1024 * 1024;
        if (snapshot.entries.size() >= limit || bytes > byte_limit - used_bytes) {
            snapshot.registry_complete = false;
            return;
        }
        used_bytes += bytes;
        snapshot.entries.push_back({std::move(*name.value), version_text});
    }
    snapshot.registry_complete = false;
}

void read_framework_view(InstalledSnapshot& snapshot, REGSAM view) {
    RegistryKey key;
    const auto opened = RegOpenKeyExW(HKEY_LOCAL_MACHINE,
        L"SOFTWARE\\Microsoft\\NET Framework Setup\\NDP\\v4\\Full", 0, KEY_QUERY_VALUE | view, &key.value);
    if (opened == ERROR_FILE_NOT_FOUND || opened == ERROR_PATH_NOT_FOUND) return;
    if (opened != ERROR_SUCCESS) { snapshot.net_framework_complete = false; return; }
    DWORD type = 0;
    DWORD release = 0;
    DWORD size = sizeof(release);
    const auto result = RegQueryValueExW(key.value, L"Release", nullptr, &type,
                                        reinterpret_cast<BYTE*>(&release), &size);
    if (result == ERROR_FILE_NOT_FOUND) return;
    if (result != ERROR_SUCCESS || type != REG_DWORD || size != sizeof(release)) {
        snapshot.net_framework_complete = false;
        return;
    }
    if (!snapshot.net_framework_release || release > *snapshot.net_framework_release)
        snapshot.net_framework_release = release;
}
#endif

std::optional<std::filesystem::path> expanded_path(const std::string& value) {
    if (value.empty() || value.find('\0') != std::string::npos) return std::nullopt;
#ifdef _WIN32
    const auto input = wide(value);
    if (!input) return std::nullopt;
    const auto length = ExpandEnvironmentStringsW(input->c_str(), nullptr, 0);
    if (!length || length > 32768) return std::nullopt;
    std::wstring output(static_cast<std::size_t>(length), L'\0');
    const auto written = ExpandEnvironmentStringsW(input->c_str(), output.data(), length);
    if (!written || written > length) return std::nullopt;
    output.resize(static_cast<std::size_t>(written - 1));
    // An unresolved %NAME% remains verbatim; absence cannot then be proved.
    const auto percent = output.find(L'%');
    if (percent != std::wstring::npos && output.find(L'%', percent + 1) != std::wstring::npos)
        return std::nullopt;
    return std::filesystem::path(output);
#else
    if (value.find('%') != std::string::npos) return std::nullopt;
    std::string output;
    for (std::size_t index = 0; index < value.size();) {
        if (value[index] != '$') { output += value[index++]; continue; }
        ++index;
        const bool braces = index < value.size() && value[index] == '{';
        if (braces) ++index;
        const auto begin = index;
        while (index < value.size() && ((value[index] >= 'a' && value[index] <= 'z') ||
               (value[index] >= 'A' && value[index] <= 'Z') ||
               (value[index] >= '0' && value[index] <= '9') || value[index] == '_')) ++index;
        if (index == begin || (braces && (index == value.size() || value[index] != '}')))
            return std::nullopt;
        const auto variable = value.substr(begin, index - begin);
        if (braces) ++index;
        const auto* replacement = std::getenv(variable.c_str());
        if (!replacement) return std::nullopt;
        output += replacement;
    }
    return std::filesystem::u8path(output);
#endif
}

void apply_minimum_version(ProgramStatus& status, const Json& detect) {
    const auto minimum = text(detect, "min_version");
    if (minimum.empty()) return;
    const auto compared = compare_versions(status.installed_version, minimum);
    if (compared) status.outdated = *compared < 0;
    else status.detail = "Installed; minimum version could not be compared";
}
}  // namespace

Platform current_platform() {
#ifdef _WIN32
    return Platform::windows;
#elif defined(__linux__)
    return Platform::linux;
#else
    return Platform::other;
#endif
}

const char* installation_state_name(InstallationState state) {
    switch (state) {
        case InstallationState::installed: return "installed";
        case InstallationState::missing: return "missing";
        case InstallationState::unknown: return "unknown";
    }
    return "unknown";
}

bool is_program_applicable(const Program& program, Platform platform) {
    const auto only = ascii_lower(text(program.fields, "os"));
    if (!only.empty() && only != "any" && only != "all") {
        if ((only == "windows" && platform != Platform::windows) ||
            (only == "linux" && platform != Platform::linux) ||
            (only != "windows" && only != "linux")) return false;
    }
    if (detection_fields(program).contains("net_framework_release") && platform != Platform::windows)
        return false;
    if (only.empty()) {
        const auto inferred = inferred_platform(program);
        if (inferred && *inferred != platform) return false;
    }
    return true;
}

InstalledSnapshot collect_installed_snapshot(const Catalog& catalog,
                                             const std::filesystem::path& relative_path_base) {
    InstalledSnapshot snapshot;
    snapshot.platform = current_platform();
#ifdef _WIN32
    snapshot.registry_complete = true;
    snapshot.net_framework_complete = true;
    std::size_t used_bytes = 0;
    for (const auto hive : {HKEY_LOCAL_MACHINE, HKEY_CURRENT_USER}) {
        for (const auto view : {KEY_WOW64_64KEY, KEY_WOW64_32KEY})
            read_uninstall_view(snapshot, hive, view, used_bytes);
    }
    for (const auto view : {KEY_WOW64_64KEY, KEY_WOW64_32KEY}) read_framework_view(snapshot, view);
    std::set<std::pair<std::string, std::string>> seen;
    snapshot.entries.erase(std::remove_if(snapshot.entries.begin(), snapshot.entries.end(),
        [&](const InstalledEntry& entry) { return !seen.emplace(entry.name, entry.version).second; }),
        snapshot.entries.end());
#endif
    std::error_code error;
    const auto base = relative_path_base.empty() ? std::filesystem::current_path(error) : relative_path_base;
    const bool base_available = !error;
    for (const auto& category : catalog.categories) {
        for (const auto& program : category.programs) {
            if (!is_program_applicable(program, snapshot.platform)) continue;
            const auto raw = text(detection_fields(program), "path");
            if (raw.empty() || snapshot.paths.count(raw)) continue;
            auto state = InstallationState::unknown;
            const auto path = expanded_path(raw);
            if (path && (path->is_absolute() || base_available)) {
                error.clear();
                const auto exists = std::filesystem::exists(path->is_absolute() ? *path : base / *path, error);
                if (!error) state = exists ? InstallationState::installed : InstallationState::missing;
            }
            snapshot.paths.emplace(raw, state);
        }
    }
    return snapshot;
}

ProgramStatus detect_program(const Program& program, const InstalledSnapshot& snapshot) {
    ProgramStatus status;
    status.applicable = is_program_applicable(program, snapshot.platform);
    if (!status.applicable) { status.detail = "Not applicable to this operating system"; return status; }
    const auto& detect = detection_fields(program);
    const auto always = detect.find("always_runnable");
    if (always != detect.end() && always->is_boolean() && always->get<bool>()) {
        status.runnable = true;
        status.detail = "Always runnable; installation state is not checked";
        return status;
    }
    const auto framework = detect.find("net_framework_release");
    if (framework != detect.end()) {
        const auto threshold = framework_threshold(*framework);
        if (!threshold) {
            status.detail = "Invalid .NET Framework detection rule";
            return status;
        }
        if (snapshot.net_framework_release) {
            status.state = InstallationState::installed;
            status.installed_version = std::to_string(*snapshot.net_framework_release);
            status.outdated = snapshot.net_framework_complete &&
                              *snapshot.net_framework_release < *threshold;
            status.detail = "Detected .NET Framework release";
        } else {
            status.state = snapshot.net_framework_complete ? InstallationState::missing : InstallationState::unknown;
            status.detail = snapshot.net_framework_complete ? ".NET Framework release not detected" :
                                                             ".NET Framework detection is unavailable or incomplete";
        }
        return status;
    }
    const auto path = text(detect, "path");
    if (!path.empty()) {
        const auto found = snapshot.paths.find(path);
        if (found != snapshot.paths.end()) status.state = found->second;
        status.detail = status.state == InstallationState::installed ? "Detection path exists" :
                        status.state == InstallationState::missing ? "Detection path does not exist" :
                                                                   "Detection path could not be checked";
        if (status.state == InstallationState::installed) apply_minimum_version(status, detect);
        return status;
    }
    if (snapshot.platform != Platform::windows) {
        status.detail = "Installed program registry detection is unavailable on this operating system";
        return status;
    }
    auto needle = text(detect, "registry_name");
    if (needle.empty()) needle = text(program.fields, "name");
    const auto normalized = normalize(needle).value;
    if (normalized.empty()) { status.detail = "No usable installed program name"; return status; }
    std::vector<const InstalledEntry*> exact;
    std::vector<const InstalledEntry*> suffixed;
    for (const auto& entry : snapshot.entries) {
        const auto name = normalize(entry.name);
        if (name.value == normalized) exact.push_back(&entry);
        else if (name.value.size() > normalized.size() && name.value.compare(0, normalized.size(), normalized) == 0 &&
                 name.boundaries.count(normalized.size()) && release_suffix(std::string_view(name.value).substr(normalized.size())))
            suffixed.push_back(&entry);
    }
    const auto& matches = exact.empty() ? suffixed : exact;
    if (matches.empty()) {
        status.state = snapshot.registry_complete ? InstallationState::missing : InstallationState::unknown;
        status.detail = snapshot.registry_complete ? "No matching uninstall registration" :
                                                    "Uninstall registry snapshot is incomplete";
        return status;
    }
    status.state = InstallationState::installed;
    status.installed_version = matches.front()->version;
    for (const auto* match : matches) {
        if (match->version != status.installed_version) {
            status.installed_version.clear();
            break;
        }
    }
    status.detail = status.installed_version.empty() ? "Installed; version is unknown or ambiguous" :
                                                    "Detected uninstall registration";
    if (snapshot.registry_complete) apply_minimum_version(status, detect);
    else status.detail = "Installed; uninstall registry snapshot is incomplete";
    return status;
}

std::vector<std::vector<ProgramStatus>> detect_catalog(const Catalog& catalog, const InstalledSnapshot& snapshot) {
    std::vector<std::vector<ProgramStatus>> statuses;
    statuses.reserve(catalog.categories.size());
    for (const auto& category : catalog.categories) {
        auto& row = statuses.emplace_back();
        row.reserve(category.programs.size());
        for (const auto& program : category.programs) row.push_back(detect_program(program, snapshot));
    }
    return statuses;
}

std::optional<int> compare_versions(std::string_view left, std::string_view right) {
    const auto first = version_parts(left);
    const auto second = version_parts(right);
    if (!first || !second) return std::nullopt;
    const std::string zero = "0";
    for (std::size_t index = 0; index < std::max(first->size(), second->size()); ++index) {
        const auto& a = index < first->size() ? first->at(index) : zero;
        const auto& b = index < second->size() ? second->at(index) : zero;
        if (a.size() != b.size()) return a.size() < b.size() ? -1 : 1;
        if (a != b) return a < b ? -1 : 1;
    }
    return 0;
}
}  // namespace minstall

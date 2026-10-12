#include "minstall/catalog.hpp"

#include <algorithm>
#include <array>
#include <map>
#include <system_error>
#include <utility>

#ifdef _WIN32
#include <windows.h>
#endif

namespace minstall {
namespace {
namespace fs = std::filesystem;

bool is_link(const fs::directory_entry& entry, const fs::file_status& status) {
    if (fs::is_symlink(status)) {
        return true;
    }
#ifdef _WIN32
    // Also exclude junctions and other reparse points, not just symlinks.
    const auto attributes = GetFileAttributesW(entry.path().c_str());
    return attributes == INVALID_FILE_ATTRIBUTES || (attributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0;
#else
    static_cast<void>(entry);
    return false;
#endif
}

bool supported(const fs::path& path) {
    auto extension = path.extension().u8string();
    for (auto& c : extension) {
        if (c >= 'A' && c <= 'Z') {
            c = static_cast<char>(c + ('a' - 'A'));
        }
    }
    constexpr std::array<const char*, 10> extensions{
        ".exe", ".msi", ".bat", ".cmd", ".ps1", ".reg", ".sh", ".bash", ".deb", ".appimage"};
    return std::find(extensions.begin(), extensions.end(), extension) != extensions.end();
}

void scan(const fs::path& directory, const fs::path& relative, int depth,
          std::size_t& visited, std::map<std::string, std::vector<Program>>& categories) {
    std::vector<fs::directory_entry> entries;
    for (const auto& entry : fs::directory_iterator(directory)) {
        if (++visited > max_scan_entries) {
            throw CatalogError("software/ scan exceeds 10000 entries; no partial list shown");
        }
        entries.push_back(entry);
    }
    std::sort(entries.begin(), entries.end(), [](const auto& left, const auto& right) {
        return left.path().filename().u8string() < right.path().filename().u8string();
    });
    for (const auto& entry : entries) {
        std::error_code error;
        const auto status = entry.symlink_status(error);
        if (error == std::errc::no_such_file_or_directory || status.type() == fs::file_type::not_found) {
            continue;  // A file may disappear between enumeration and inspection.
        }
        if (error) {
            throw CatalogError("Cannot inspect a software/ entry");
        }
        if (is_link(entry, status)) {
            continue;
        }
        const auto child = relative / entry.path().filename();
        if (fs::is_directory(status)) {
            if (depth < max_scan_depth) {
                scan(entry.path(), child, depth + 1, visited, categories);
            }
        } else if (fs::is_regular_file(status) && supported(entry.path())) {
            std::string category;
            for (const auto& component : relative) {
                if (!category.empty()) {
                    category += " / ";
                }
                category += component.u8string();
            }
            if (category.empty()) {
                category = "ПРОЧЕЕ";
            }
            // Do not infer executable commands, silent flags or versions from file names.
            Json fields{{"name", entry.path().stem().u8string()}, {"cmd", ""},
                        {"desc", "Автоматически обнаружено: " + child.generic_u8string()},
                        {"source_path", child.generic_u8string()}};
            categories[category].push_back(Program{std::move(fields)});
        }
    }
}
}  // namespace

Catalog scan_software(const std::filesystem::path& root) {
    try {
        std::error_code error;
        const auto status = fs::symlink_status(root, error);
        if (error == std::errc::no_such_file_or_directory || status.type() == fs::file_type::not_found) {
            return {};
        }
        if (error || !fs::is_directory(status) || is_link(fs::directory_entry(root), status)) {
            throw CatalogError("software/ must be a readable directory, not a link");
        }
        std::map<std::string, std::vector<Program>> categories;
        std::size_t visited = 0;
        scan(root, {}, 0, visited, categories);
        Catalog result;
        for (auto& category : categories) {
            result.categories.push_back(Category{category.first, std::move(category.second)});
        }
        return result;
    } catch (const fs::filesystem_error&) {
        throw CatalogError("Cannot read software/; no partial list shown");
    }
}
}  // namespace minstall

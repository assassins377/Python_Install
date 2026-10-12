#pragma once

#include <cstddef>
#include <filesystem>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

#include <nlohmann/json.hpp>

namespace minstall {
using Json = nlohmann::ordered_json;
inline constexpr std::size_t max_catalog_bytes = 8 * 1024 * 1024;
inline constexpr int max_catalog_depth = 64;

class CatalogError : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

struct Program {
    // Preserve all fields, including extensions. Commands remain inert data.
    Json fields;
    std::string name() const { return fields.at("name").get<std::string>(); }
    std::string text(const char* key) const { return fields.value(key, std::string{}); }
};

struct Category {
    std::string name;
    std::vector<Program> programs;
};

struct Catalog {
    std::vector<Category> categories;
    std::size_t program_count() const;
};

Catalog parse_catalog(std::string_view bytes);
Catalog load_catalog(const std::filesystem::path& path);
inline constexpr std::size_t max_scan_entries = 10000;
inline constexpr int max_scan_depth = 8;
// Read file names only. Missing/empty software directories are normal.
Catalog scan_software(const std::filesystem::path& root);
// Escape terminal controls, including newlines, without changing stored data.
std::string display_text(std::string_view text);
}  // namespace minstall

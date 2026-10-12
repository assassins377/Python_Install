#include "minstall/catalog.hpp"

#include <array>
#include <cmath>
#include <fstream>
#include <set>

namespace minstall {
namespace {
void require(bool condition, const std::string& message) {
    if (!condition) {
        throw CatalogError(message);
    }
}

bool integer(const Json& value) {
    return value.is_number_integer() ||
           (value.is_number_float() && std::floor(value.get<double>()) == value.get<double>());
}

void string_fields(const Json& object, std::initializer_list<const char*> keys,
                   const std::string& location) {
    for (const auto* key : keys) {
        if (object.contains(key)) {
            require(object.at(key).is_string(), location + ": " + key + " must be a string");
        }
    }
}

void validate_program(const Json& program, const std::string& location) {
    require(program.is_object(), location + ": expected an object");
    require(program.contains("name") && program.contains("cmd"),
            location + ": name and cmd are required");
    string_fields(program, {"name", "cmd", "desc", "icon", "version", "os", "url", "sha256",
                            "pre_cmd", "post_cmd", "uninstall_cmd"}, location);
    if (program.contains("os")) {
        const auto os = program.at("os").get<std::string>();
        require(os == "windows" || os == "linux" || os == "any",
                location + ": os must be windows, linux or any");
    }
    if (program.contains("sha256")) {
        const auto hash = program.at("sha256").get<std::string>();
        require(hash.size() == 64 && hash.find_first_not_of("0123456789abcdefABCDEF") == std::string::npos,
                location + ": sha256 must contain 64 hexadecimal digits");
    }
    for (const auto* key : {"retry", "timeout"}) {
        if (program.contains(key)) {
            const auto& value = program.at(key);
            const int minimum = std::string_view(key) == "retry" ? 0 : 1;
            require(integer(value) && value >= minimum,
                    location + ": " + key + " has an invalid integer value");
        }
    }
    if (program.contains("depends_on")) {
        require(program.at("depends_on").is_array(), location + ": depends_on must be an array");
        for (const auto& dependency : program.at("depends_on")) {
            require(dependency.is_string(), location + ": depends_on entries must be strings");
        }
    }
    if (program.contains("detect")) {
        const auto& detect = program.at("detect");
        require(detect.is_object(), location + ": detect must be an object");
        string_fields(detect, {"registry_name", "path", "min_version"}, location + ".detect");
        if (detect.contains("always_runnable")) {
            require(detect.at("always_runnable").is_boolean(),
                    location + ": detect.always_runnable must be boolean");
        }
        if (detect.contains("net_framework_release")) {
            require(integer(detect.at("net_framework_release")),
                    location + ": detect.net_framework_release must be an integer");
        }
    }
}
}  // namespace

std::size_t Catalog::program_count() const {
    std::size_t count = 0;
    for (const auto& category : categories) {
        count += category.programs.size();
    }
    return count;
}

Catalog parse_catalog(std::string_view bytes) {
    require(bytes.size() <= max_catalog_bytes, "Catalog exceeds the 8 MiB limit");
    // nlohmann's lexer also treats a raw NUL as EOF. Check the entire input
    // before parsing so a valid prefix cannot conceal unvalidated trailing data.
    require(bytes.find('\0') == std::string_view::npos, "Catalog contains a NUL byte");
    // An optional UTF-8 BOM is accepted; all other decoding is strict JSON UTF-8.
    std::vector<std::set<std::string>> object_keys;
    std::size_t events = 0;
    auto guard = [&](int depth, Json::parse_event_t event, Json& value) {
        require(depth <= max_catalog_depth, "Catalog nesting exceeds 64 levels");
        require(++events <= 100000, "Catalog has too many JSON elements");
        if (value.is_string()) {
            const auto& text = value.get_ref<const std::string&>();
            require(text.size() <= 65536, "Catalog string exceeds 64 KiB");
            require(text.find('\0') == std::string::npos, "Catalog contains a NUL character");
        }
        if (event == Json::parse_event_t::object_start) {
            object_keys.emplace_back();
        } else if (event == Json::parse_event_t::key) {
            auto& keys = object_keys.back();
            require(keys.size() < 4096, "Catalog object has too many members");
            require(keys.insert(value.get<std::string>()).second, "Catalog contains a duplicate JSON key");
        } else if (event == Json::parse_event_t::object_end) {
            object_keys.pop_back();
        }
        return true;
    };

    Json root;
    try {
        root = Json::parse(bytes.begin(), bytes.end(), guard);
    } catch (const nlohmann::json::parse_error& error) {
        // Do not echo JSON fragments (potential credentials/terminal escapes).
        throw CatalogError("Invalid UTF-8 JSON near byte " + std::to_string(error.byte));
    } catch (const nlohmann::json::exception&) {
        throw CatalogError("Invalid JSON value");
    }
    require(root.is_object(), "Catalog root must be an object");
    require(root.contains("_version") && integer(root.at("_version")) && root.at("_version") == 2,
            "Only catalog schema version 2 is supported");
    require(root.contains("categories") && root.at("categories").is_object(),
            "Catalog categories must be an object");

    Catalog catalog;
    std::size_t category_index = 0;
    for (auto it = root.at("categories").begin(); it != root.at("categories").end(); ++it) {
        const auto location = "Category #" + std::to_string(++category_index);
        require(it.value().is_array(), location + ": expected an array");
        Category category{it.key(), {}};
        std::size_t program_index = 0;
        for (auto& program : it.value()) {
            validate_program(program, location + ", program #" + std::to_string(++program_index));
            category.programs.push_back({std::move(program)});
        }
        catalog.categories.push_back(std::move(category));
    }
    for (auto it = root.begin(); it != root.end(); ++it) {
        if (it.key() != "_version" && it.key() != "categories") {
            catalog.extra_fields[it.key()] = std::move(it.value());
        }
    }
    return catalog;
}

Catalog load_catalog(const std::filesystem::path& path) {
    std::error_code error;
    require(std::filesystem::is_regular_file(path, error) && !error,
            "Catalog is missing or is not a regular file");
    const auto size = std::filesystem::file_size(path, error);
    require(!error && size <= max_catalog_bytes, "Cannot read catalog size or file exceeds 8 MiB");
    std::ifstream stream(path, std::ios::binary);
    require(stream.is_open(), "Cannot open catalog");
    std::string bytes;
    std::array<char, 8192> buffer{};
    // Bound actual reads too: the file can grow after the metadata check.
    while (stream.read(buffer.data(), static_cast<std::streamsize>(buffer.size())) || stream.gcount()) {
        const auto count = static_cast<std::size_t>(stream.gcount());
        require(bytes.size() + count <= max_catalog_bytes, "Catalog exceeds the 8 MiB limit");
        bytes.append(buffer.data(), count);
    }
    require(stream.eof() && !stream.bad(), "Cannot read catalog");
    return parse_catalog(bytes);
}

std::string display_text(std::string_view text) {
    constexpr char hex[] = "0123456789abcdef";
    std::string output;
    for (const unsigned char byte : text) {
        if (byte < 0x20 || byte == 0x7f) {
            output += "\\u00";
            output += hex[byte >> 4];
            output += hex[byte & 0xf];
        } else {
            output += static_cast<char>(byte);
        }
    }
    return output;
}
}  // namespace minstall

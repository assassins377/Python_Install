#include "minstall/app.hpp"

#include <fstream>
#include <iostream>
#include <sstream>

int storage_tests();
int metadata_tests();

namespace {
int checks = 0;
void check(bool condition, const char* message) {
    ++checks;
    if (!condition) {
        throw std::runtime_error(message);
    }
}

template <class Function>
void rejects(Function function) {
    bool rejected = false;
    try {
        function();
    } catch (const minstall::CatalogError&) {
        rejected = true;
    }
    check(rejected, "Expected a controlled CatalogError");
}

const char* sample = R"json({
  "_version": 2,
  "categories": {
    "Я — первая": [{
      "name": "Программа 中文", "cmd": "software\\setup.exe /S & never-run",
      "desc": "Описание", "version": "1.2", "os": "any", "icon": "../../not-loaded.png",
      "url": "https://example.invalid/package.exe",
      "sha256": "0123456789abcdef0123456789abcdef0123456789ABCDEF0123456789ABCDEF",
      "depends_on": ["Зависимость"], "retry": 0, "timeout": 1,
      "pre_cmd": "never-pre", "post_cmd": "never-post", "uninstall_cmd": "never-uninstall",
      "detect": {"registry_name": "App", "path": "C:\\Тест", "min_version": "1",
                 "always_runnable": false, "net_framework_release": 528040, "extension": 1},
      "future_extension": {"safe_to_preserve": [1, true]}
    }],
    "A — вторая": [],
    "Третья": [{"name": "Зависимость", "cmd": ""}]
  }
})json";

void parser_tests() {
    using minstall::Json;
    const auto catalog = minstall::parse_catalog(sample);
    check(catalog.categories.size() == 3 && catalog.program_count() == 2, "Catalog counts");
    check(catalog.categories[0].name == "Я — первая", "Source category order");
    check(catalog.categories[1].programs.empty(), "Empty category preserved");
    const auto& program = catalog.categories[0].programs[0];
    check(program.name() == "Программа 中文", "Unicode names");
    check(program.fields == Json::parse(sample)["categories"]["Я — первая"][0], "All fields preserved");
    check(minstall::parse_catalog(std::string("\xef\xbb\xbf") + sample).program_count() == 2, "UTF-8 BOM");
    check(minstall::parse_catalog(R"({"_version":2,"categories":{}})").categories.empty(), "Empty catalog parses");
    check(minstall::display_text("a\n\t\x1b[31m\x7f") == "a\\u000a\\u0009\\u001b[31m\\u007f", "Terminal controls escaped");

    for (const auto* invalid : {"", "{", "[]", "null", "{}", "{bad json}",
             R"({"_version":2,"categories":{}} trailing)",
             R"({"_version":2,"categories":{},})",
             R"({"_version":2,"categories":{},"categories":{}})",
             R"({"_version":3,"categories":{}})",
             R"({"_version":"2","categories":{}})",
             R"({"_version":true,"categories":{}})",
             R"({"_version":2,"categories":[]})",
             R"({"_version":2,"categories":{"a":{}}})",
             R"({"_version":2,"categories":{"a":[null]}})",
             R"({"_version":2,"categories":{"a":[{"name":"x"}]}})",
             R"({"_version":2,"categories":{"a":[{"name":"x","cmd":"","cmd":"x"}]}})",
             R"({"_version":2,"categories":{"a":[{"name":"\u0000","cmd":""}]}})",
             R"({"_version":2,"categories":{"a":[{"name":"\ud800","cmd":""}]}})",
             R"({"_version":2,"categories":{},"x":1e999})"}) {
        rejects([&] { minstall::parse_catalog(invalid); });
    }

    // The JSON lexer treats a raw NUL as end-of-input, even with explicit
    // iterators. A valid prefix must never hide a corrupt or second document.
    for (const auto& suffix : {std::string{}, std::string("hidden-tail"), std::string(sample)}) {
        const auto nul_terminated = std::string(sample) + '\0' + suffix;
        rejects([&] { minstall::parse_catalog(nul_terminated); });
    }
    rejects([&] { minstall::parse_catalog(std::string(1, '\0') + sample); });
    std::string embedded_nul = sample;
    embedded_nul.insert(embedded_nul.find("never-run"), 1, '\0');
    rejects([&] { minstall::parse_catalog(embedded_nul); });

    auto root = Json::parse(sample);
    const auto original = root["categories"]["Я — первая"][0];
    const std::vector<std::pair<std::string, Json>> invalid_fields = {
        {"name", nullptr}, {"cmd", 42}, {"desc", true}, {"icon", {}}, {"version", 1},
        {"url", false}, {"os", "all"}, {"os", 1}, {"sha256", "not-a-hash"},
        {"sha256", std::string(64, 'g')}, {"depends_on", "x"}, {"depends_on", Json::array({1})},
        {"retry", -1}, {"retry", 0.5}, {"retry", true}, {"timeout", 0}, {"timeout", "10"},
        {"pre_cmd", 1}, {"post_cmd", false}, {"uninstall_cmd", nullptr},
        {"detect", Json::array()}, {"detect", Json{{"path", 1}}},
        {"detect", Json{{"registry_name", false}}}, {"detect", Json{{"min_version", 1}}},
        {"detect", Json{{"always_runnable", "yes"}}}, {"detect", Json{{"net_framework_release", 1.5}}}
    };
    for (const auto& entry : invalid_fields) {
        auto& candidate = root["categories"]["Я — первая"][0];
        candidate = original;
        candidate[entry.first] = entry.second;
        rejects([&] { minstall::parse_catalog(root.dump()); });
    }
    root["categories"]["Я — первая"][0] = original;
    root["_version"] = 2.0;
    root["categories"]["Я — первая"][0]["retry"] = 1.0;
    check(minstall::parse_catalog(root.dump()).program_count() == 2, "JSON Schema integer semantics");

    std::string invalid_utf8 = sample;
    invalid_utf8[invalid_utf8.find("Описание")] = static_cast<char>(0xff);
    rejects([&] { minstall::parse_catalog(invalid_utf8); });
    rejects([&] { minstall::parse_catalog(std::string(minstall::max_catalog_bytes + 1, ' ')); });
    root["large"] = std::string(65537, 'x');
    rejects([&] { minstall::parse_catalog(root.dump()); });
    std::string deep(70, '[');
    deep += '0';
    deep += std::string(70, ']');
    rejects([&] { minstall::parse_catalog("{\"extra\":" + deep + "}"); });
    Json wide = Json::object();
    for (int i = 0; i < 4097; ++i) {
        wide[std::to_string(i)] = 0;
    }
    rejects([&] { minstall::parse_catalog(wide.dump()); });
}

void file_and_cli_tests() {
    const auto source_path = std::filesystem::u8path(MINSTALL_SOURCE_CATALOG);
    const auto actual = minstall::load_catalog(source_path);
    std::ifstream stream(source_path);
    minstall::Json reference;
    stream >> reference;
    std::size_t category_index = 0;
    std::size_t count = 0;
    for (auto it = reference["categories"].begin(); it != reference["categories"].end(); ++it) {
        const auto& category = actual.categories.at(category_index++);
        check(category.name == it.key(), "Repository category preserved");
        check(category.programs.size() == it.value().size(), "Repository program counts");
        for (std::size_t i = 0; i < category.programs.size(); ++i) {
            check(category.programs[i].fields == it.value()[i], "Repository fields/order preserved");
            ++count;
        }
    }
    check(count == actual.program_count() && count > 0, "Repository catalog is exercised");
    rejects([&] { minstall::load_catalog(source_path.parent_path()); });
    rejects([&] { minstall::load_catalog(source_path / "nonexistent.json"); });

    const auto default_path = minstall::default_catalog_path();
    check(default_path.is_absolute() && default_path.filename() == "programs.json", "Executable-relative path");
    auto options = minstall::parse_options({"--list", "--catalog", source_path.u8string()}, default_path);
    std::ostringstream output;
    std::ostringstream errors;
    check(minstall::run_cli(options, output, errors) == 0, "List succeeds");
    check(output.str().find(actual.categories[0].programs[0].name()) != std::string::npos, "List has names");
    check(errors.str().find("installed status not checked") != std::string::npos, "No invented installed status");
    options = minstall::parse_options({"--version", "--catalog", "missing.json"}, default_path);
    check(minstall::run_cli(options, output, errors) == 0, "Version does not read the catalog");
    check(output.str().find("Native 0.1.0") != std::string::npos, "Distinct prototype version");
    for (const std::vector<std::string>& args : std::vector<std::vector<std::string>>{
             {"--install", "anything"}, {"--catalog"}, {"--list", "--version"}, {"--list", "--list"},
             {"--catalog", "--list"}, {"--catalog", "a", "--catalog", "b"}, {"--unknown"},
             {"--list", "--gui-smoke"}, {"--gui-smoke", "--gui-smoke"}}) {
        rejects([&] { minstall::parse_options(args, default_path); });
    }

    // Exercise the binary file reader and listing path, not just the parser.
    // Keep this generated fixture beside the test executable, outside sources.
    const auto nul_path = default_path.parent_path() / "catalog-nul-test.json";
    struct RemoveFixture {
        std::filesystem::path path;
        ~RemoveFixture() {
            std::error_code error;
            std::filesystem::remove(path, error);
        }
    } cleanup{nul_path};
    {
        std::ofstream fixture(nul_path, std::ios::binary);
        fixture << sample;
        fixture.put('\0');
        fixture << "hidden-tail";
        fixture.close();
        check(static_cast<bool>(fixture), "NUL fixture written completely");
    }
    rejects([&] { minstall::load_catalog(nul_path); });
    options = minstall::parse_options({"--list", "--catalog", nul_path.u8string()}, default_path);
    std::ostringstream nul_output;
    std::ostringstream nul_errors;
    rejects([&] { minstall::run_cli(options, nul_output, nul_errors); });
    check(nul_output.str().empty() && nul_errors.str().empty(), "NUL catalog produces no partial list");
}

void discovery_tests() {
    namespace fs = std::filesystem;
    const auto base = minstall::default_catalog_path().parent_path() / "discovery-test-fixture";
    check(fs::create_directory(base), "Exclusive test fixture directory");
    struct RemoveDirectory {
        fs::path path;
        ~RemoveDirectory() {
            std::error_code error;
            fs::remove_all(path, error);
        }
    } cleanup{base};
    const auto root = base / "software";
    const auto write = [](const fs::path& path) {
        std::ofstream file(path, std::ios::binary);
        file << "inert test fixture; never execute";
        check(static_cast<bool>(file), "Discovery fixture written");
    };
    auto options = minstall::parse_options({"--list"}, base / "programs.json");
    check(!options.explicit_catalog && options.software == root, "Discovery is the default source");
    check(minstall::load_programs(options).categories.empty(), "Missing software gives empty catalog");
    check(!fs::exists(root), "Discovery does not create directories");
    write(base / "programs.json");
    check(minstall::load_programs(options).categories.empty(), "Implicit sample JSON is never read");
    fs::create_directory(root);
    write(root / "notes.txt");
    fs::create_directory(root / "directory.exe");
    check(minstall::load_programs(options).categories.empty(), "Unsupported files and empty directories give no categories");
    std::ostringstream output;
    std::ostringstream errors;
    check(minstall::run_cli(options, output, errors) == 0 && output.str().empty(), "Empty list succeeds without sample output");

    for (const auto* extension : {".EXE", ".msi", ".bat", ".cmd", ".ps1", ".reg", ".sh", ".bash", ".deb", ".AppImage"}) {
        write(root / (std::string("Program") + extension));
    }
    const auto nested = root / fs::u8path("Утилиты с пробелами") / fs::u8path("中文");
    fs::create_directories(nested);
    const auto unicode_file = nested / fs::u8path("Программа 1.2.msi");
    write(unicode_file);
    auto found = minstall::load_programs(options);
    check(found.program_count() == 11 && found.categories.size() == 2, "All supported extensions and nested files found");
    check(found.categories[0].name == "ПРОЧЕЕ", "Root files have a category");
    check(found.categories[1].name == "Утилиты с пробелами / 中文", "Folder hierarchy preserves Unicode");
    const auto& program = found.categories[1].programs[0];
    check(program.name() == "Программа 1.2", "File stem used without guessing a product name");
    check(program.text("source_path") == "Утилиты с пробелами/中文/Программа 1.2.msi", "Relative file identity");
    check(program.text("cmd").empty() && program.text("version").empty(), "No guessed commands or versions");
    fs::remove(unicode_file);
    found = minstall::load_programs(options);
    check(found.program_count() == 10 && found.categories.size() == 1, "Removed files and empty categories disappear on rescan");

    const auto outside = base / "outside";
    fs::create_directory(outside);
    write(outside / "hidden.exe");
    std::error_code link_error;
    fs::create_directory_symlink(outside, root / "linked-directory", link_error);
    if (!link_error) {
        check(minstall::load_programs(options).program_count() == 10, "Directory links not followed");
        rejects([&] { minstall::scan_software(root / "linked-directory"); });
    }
    fs::create_symlink(outside / "hidden.exe", root / "linked.exe", link_error);
    if (!link_error) {
        check(minstall::load_programs(options).program_count() == 10, "File links excluded");
    }
    auto deep = root;
    for (int i = 0; i < minstall::max_scan_depth; ++i) {
        deep /= "deep";
    }
    fs::create_directories(deep);
    write(deep / "at-limit.exe");
    fs::create_directory(deep / "beyond");
    write(deep / "beyond" / "too-deep.exe");
    check(minstall::load_programs(options).program_count() == 11, "Depth eight included, deeper files excluded");
    rejects([&] { minstall::scan_software(root / "Program.EXE"); });

    fs::remove_all(root);
    check(minstall::load_programs(options).categories.empty(), "Deleting software clears the catalog");
    fs::create_directory(root);
    for (std::size_t i = 0; i <= minstall::max_scan_entries; ++i) {
        std::ofstream file(root / (std::to_string(i) + ".txt"));
    }
    rejects([&] { minstall::load_programs(options); });
}
}  // namespace

int main() {
    try {
        parser_tests();
        file_and_cli_tests();
        discovery_tests();
        checks += storage_tests();
        checks += metadata_tests();
        std::cout << checks << " checks passed\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "Test failure: " << error.what() << '\n';
        return 1;
    }
}

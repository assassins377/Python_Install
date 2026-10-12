#include "minstall/app.hpp"
#include "minstall/metadata.hpp"
#include "minstall/storage.hpp"

#include <fstream>
#include <iterator>
#include <sstream>

namespace {
namespace fs = std::filesystem;
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
    check(rejected, "Metadata operation must reject invalid input");
}
void write(const fs::path& path, const std::string& bytes) {
    std::ofstream file(path, std::ios::binary);
    file.write(bytes.data(), static_cast<std::streamsize>(bytes.size()));
    file.close();
    check(static_cast<bool>(file), "Metadata fixture written");
}
std::string read(const fs::path& path) {
    std::ifstream file(path, std::ios::binary);
    return {std::istreambuf_iterator<char>(file), std::istreambuf_iterator<char>()};
}
const minstall::Program& first(const minstall::Catalog& catalog) {
    return catalog.categories.at(0).programs.at(0);
}
}  // namespace

int metadata_tests() {
    const auto base = minstall::default_catalog_path().parent_path() / "metadata-test-fixture";
    check(fs::create_directory(base), "Exclusive metadata fixture");
    struct Cleanup {
        fs::path path;
        ~Cleanup() {
            std::error_code error;
            fs::remove_all(path, error);
        }
    } cleanup{base};
    auto options = minstall::parse_options({"--list"}, base / "programs.json");
    const auto software = options.software;
    const auto path = minstall::metadata_path(software);
    const auto lock = base / "program-metadata.json.lock";
    const auto missing = minstall::load_metadata(software);
    check(!missing.exists && !fs::exists(path), "Reading missing metadata does not write it");
    check(minstall::load_programs(options).program_count() == 0 && !fs::exists(software), "Empty first launch has no side effects");
    fs::create_directories(software / fs::u8path("Утилиты"));
    const std::string source_path = "Утилиты/Программа.EXE";
    const auto installer = software / fs::u8path(source_path);
    write(installer, "inert fixture; never execute");
    const minstall::ProgramDetails edited{"Моя программа", "2.4 β", "Описание\nВторая строка\tUnicode ✓"};
    minstall::save_program_details(software, source_path, edited, missing);
    check(!fs::exists(lock), "Writer releases the lock");
    check(read(installer) == "inert fixture; never execute", "Editing never changes installer bytes");
    auto loaded = minstall::load_programs(options);
    check(loaded.program_count() == 1 && loaded.categories[0].name == "Утилиты", "Metadata leaves membership and categories unchanged");
    check(first(loaded).name() == edited.name && first(loaded).text("version") == edited.version &&
          first(loaded).text("desc") == edited.description, "Unicode and multiline details survive reload");
    check(first(loaded).text("source_path") == source_path && first(loaded).text("cmd").empty(), "Source and command unchanged");
    std::ostringstream output;
    std::ostringstream errors;
    check(minstall::run_cli(options, output, errors) == 0 && output.str().find("Моя программа (2.4 β)") != std::string::npos,
          "CLI uses saved name and version");

    const auto before = read(path);
    const auto snapshot = minstall::load_metadata(software);
    for (const auto& details : std::vector<minstall::ProgramDetails>{
             {"", "", ""}, {"   ", "", ""}, {"bad\nname", "", ""}, {"Good", "bad\tversion", ""},
             {"Good", "", std::string("nul\0description", 15)}, {std::string(1025, 'x'), "", ""},
             {"Good", std::string(257, 'x'), ""}, {"Good", "", std::string(65537, 'x')},
             {std::string(1, static_cast<char>(0xff)), "", ""}}) {
        rejects([&] { minstall::save_program_details(software, source_path, details, snapshot); });
        check(read(path) == before && !fs::exists(lock), "Invalid edits preserve stored metadata and release lock");
    }
    rejects([&] { minstall::save_program_details(software, "../outside.exe", edited, snapshot); });
    check(read(path) == before, "Only a discovered source path can be edited");

    fs::remove(installer);
    check(minstall::load_programs(options).categories.empty(), "Saved metadata never resurrects deleted files or categories");
    rejects([&] { minstall::save_program_details(software, source_path, edited, snapshot); });
    check(read(path) == before && !fs::exists(lock), "Disappeared installer cannot be saved");
    write(installer, "replacement inert fixture");
    check(first(minstall::load_programs(options)).name() == edited.name, "Returning same relative path restores its details");
    fs::rename(installer, software / fs::u8path("Утилиты/Переименованная.EXE"));
    check(first(minstall::load_programs(options)).name() == "Переименованная", "Renaming creates a new metadata identity");
    fs::rename(software / fs::u8path("Утилиты/Переименованная.EXE"), installer);

    write(software / "second.msi", "second inert fixture");
    minstall::save_program_details(software, "second.msi", {"Second", "1", "Other"}, snapshot);
    const auto two_entries = read(path);
    rejects([&] { minstall::save_program_details(software, source_path, edited, snapshot); });
    check(read(path) == two_entries && !fs::exists(lock), "Stale editor cannot overwrite another window's changes");
    auto current = minstall::load_metadata(software);
    current.catalog.extra_fields["extension"] = minstall::Json{{"keep", true}};
    current.catalog.categories[0].programs[0].fields["extension"] = 42;
    current.catalog.categories[0].programs[0].fields["cmd"] = "never-run.exe";
    auto ghost = current.catalog.categories[0].programs[0];
    ghost.fields["source_path"] = "../outside.exe";
    current.catalog.categories[0].programs.push_back(ghost);
    minstall::save_catalog(current.catalog, path, true);
    current = minstall::load_metadata(software);
    minstall::save_program_details(software, source_path, {"Новое название", "", ""}, current);
    auto saved = minstall::load_metadata(software);
    check(saved.catalog.program_count() == 3 && saved.catalog.extra_fields["extension"]["keep"] == true &&
          first(saved.catalog).fields["extension"] == 42, "Editing preserves other entries and extension fields");
    loaded = minstall::load_programs(options);
    check(loaded.program_count() == 2 && loaded.categories[1].programs[0].name() == "Новое название" &&
          loaded.categories[1].programs[0].text("version").empty() && loaded.categories[1].programs[0].text("desc").empty(),
          "Optional fields can be cleared and unmatched metadata cannot add programs");
    check(loaded.categories[1].programs[0].text("cmd").empty() &&
          !loaded.categories[1].programs[0].fields.contains("extension"), "Only three allowed fields are applied");
    const auto good = read(path);
    fs::create_directory(lock);
    rejects([&] { minstall::save_program_details(software, source_path, edited, saved); });
    check(fs::exists(lock) && read(path) == good, "Active lock preserved; concurrent write refused");
    fs::remove(lock);

    auto duplicate = saved.catalog;
    duplicate.categories[0].programs.push_back(first(duplicate));
    minstall::save_catalog(duplicate, path, true);
    rejects([&] { minstall::load_metadata(software); });
    auto bad_path = saved.catalog;
    bad_path.categories[0].programs[0].fields["source_path"] = 17;
    minstall::save_catalog(bad_path, path, true);
    rejects([&] { minstall::load_metadata(software); });
    minstall::save_catalog(minstall::scan_software(software), path, true);
    rejects([&] { minstall::load_metadata(software); });
    write(path, "{invalid-json");
    rejects([&] { minstall::load_programs(options); });
    rejects([&] { minstall::save_program_details(software, source_path, edited, saved); });
    check(read(path) == "{invalid-json" && !fs::exists(lock), "Corrupt metadata is never silently overwritten");
    minstall::save_catalog(minstall::scan_software(software), options.catalog);
    options.explicit_catalog = true;
    check(minstall::load_programs(options).program_count() == 2, "Explicit catalog is independent of metadata");

    fs::remove(path);
    fs::create_directory(path);
    rejects([&] { minstall::load_metadata(software); });
    fs::remove(path);
    write(base / "link-target.json", good);
    std::error_code error;
    fs::create_symlink(base / "link-target.json", path, error);
    if (!error) {
        rejects([&] { minstall::load_metadata(software); });
        rejects([&] { minstall::save_program_details(software, source_path, edited, saved); });
        check(read(base / "link-target.json") == good, "Metadata symlink target unchanged");
        fs::remove(path);
    }
    write(path, good);
    fs::remove_all(software);
    options.explicit_catalog = false;
    check(minstall::load_programs(options).program_count() == 0, "Metadata alone cannot populate a missing software directory");
    for (const auto& entry : fs::recursive_directory_iterator(base)) {
        check(entry.path().filename().u8string().rfind(".minstall-write-", 0) != 0 && entry.path() != lock,
              "No temporary metadata data or writer lock left behind");
    }
    return checks;
}

#include "minstall/app.hpp"
#include "minstall/storage.hpp"

#include <fstream>
#include <iterator>

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
    check(rejected, "Storage operation must reject invalid input");
}
void write(const fs::path& path, const std::string& bytes) {
    std::ofstream file(path, std::ios::binary);
    file.write(bytes.data(), static_cast<std::streamsize>(bytes.size()));
    file.close();
    check(static_cast<bool>(file), "Storage fixture written");
}
std::string read(const fs::path& path) {
    std::ifstream file(path, std::ios::binary);
    return {std::istreambuf_iterator<char>(file), std::istreambuf_iterator<char>()};
}
void no_temporary_files(const fs::path& base) {
    for (const auto& entry : fs::recursive_directory_iterator(base)) {
        check(entry.path().filename().u8string().rfind(".minstall-write-", 0) != 0, "Temporary data cleaned up");
    }
}
}  // namespace

int storage_tests() {
    const auto base = minstall::default_catalog_path().parent_path() / "storage-test-fixture";
    check(fs::create_directory(base), "Exclusive storage fixture");
    struct Cleanup {
        fs::path path;
        ~Cleanup() {
            std::error_code error;
            fs::remove_all(path, error);
        }
    } cleanup{base};
    const auto sources = base / fs::u8path("Исходники с пробелами");
    fs::create_directory(sources);
    const auto source = sources / fs::u8path("Программа 1.2.MSI");
    std::string bytes(180000, 'x');
    bytes[17] = '\0';
    write(source, bytes);
    const auto software = base / "software";
    const std::vector<std::string> bad_categories{
        "..", ".", "../escape", "/absolute", "C:\\escape", "one/two", "one\\two", "con", "LPT1.txt",
        "trailing.", " trailing", "trailing ", "bad?name", "bad\nname", std::string("nul\0byte", 8)};
    for (const auto& category : bad_categories) {
        rejects([&] { minstall::import_installer(source, software, category); });
    }
    write(sources / "notes.txt", "unsupported");
    rejects([&] { minstall::import_installer(sources / "notes.txt", software, ""); });
    rejects([&] { minstall::import_installer(sources, software, ""); });
    rejects([&] { minstall::import_installer(sources / "missing.exe", software, ""); });
    check(!fs::exists(software), "Invalid input has no destination side effects");

    bool cancelled = false;
    try {
        minstall::import_installer(source, software, "Отмена", [](auto copied, auto) { return copied == 0; });
    } catch (const minstall::ImportCancelled&) {
        cancelled = true;
    }
    check(cancelled, "Partial copy cancellation is reported distinctly");
    check(minstall::scan_software(software).program_count() == 0, "Cancelled copy never appears in the list");
    check(read(source) == bytes, "Cancellation preserves source bytes");
    no_temporary_files(software);

    std::uintmax_t previous = 0;
    const auto imported = minstall::import_installer(source, software, "Утилиты", [&](auto copied, auto total) {
        check(copied >= previous && total == bytes.size(), "Monotonic progress with correct total");
        previous = copied;
        return true;
    });
    check(imported.copied && previous == bytes.size(), "Complete copy reported");
    check(read(imported.path) == bytes && read(source) == bytes, "Binary copy and original are intact");
    check(minstall::scan_software(software).program_count() == 1, "Completed import discovered");
    const auto modified = fs::last_write_time(imported.path);
    const auto same = minstall::import_installer(imported.path, software, "Утилиты");
    check(!same.copied && fs::last_write_time(imported.path) == modified, "Same file is not rewritten");

    fs::create_directory(base / "duplicate");
    const auto duplicate = base / "duplicate" / source.filename();
    write(duplicate, "different source");
    rejects([&] { minstall::import_installer(duplicate, software, "Утилиты"); });
    check(read(imported.path) == bytes, "Name collision does not overwrite destination");

    const auto race_target = software / "Race" / source.filename();
    bool raced = false;
    rejects([&] {
        minstall::import_installer(source, software, "Race", [&](auto copied, auto total) {
            if (copied == total && !raced) {
                write(race_target, "created by another writer");
                raced = true;
            }
            return true;
        });
    });
    check(read(race_target) == "created by another writer", "Late collision is not overwritten");
    fs::remove(race_target);

    const auto changing = sources / "changing.exe";
    write(changing, bytes);
    bool changed = false;
    rejects([&] {
        minstall::import_installer(changing, software, "", [&](auto copied, auto) {
            if (copied > 0 && !changed) {
                write(changing, "changed while copying");
                changed = true;
            }
            return true;
        });
    });
    check(!fs::exists(software / changing.filename()), "Changed source not published as complete");

    std::error_code link_error;
    fs::create_symlink(source, sources / "link.exe", link_error);
    if (!link_error) {
        rejects([&] { minstall::import_installer(sources / "link.exe", software, ""); });
    }
    fs::create_directory_symlink(sources, software / "linked-category", link_error);
    if (!link_error) {
        rejects([&] { minstall::import_installer(source, software, "linked-category"); });
        rejects([&] { minstall::import_installer(source, software / "linked-category", ""); });
    }
    write(base / "not-a-folder", "keep");
    rejects([&] { minstall::import_installer(source, base / "not-a-folder", ""); });
    check(read(base / "not-a-folder") == "keep", "Invalid destination preserved");

    const auto catalog = minstall::parse_catalog(R"({"_version":2,"categories":{"Я":[{"name":"Программа","cmd":"never-run.exe /S","desc":"Описание","extension":{"keep":true}}],"A":[]},"root_extension":{"keep":[1,2]}})");
    const auto exported = base / fs::u8path("Каталог с пробелами.JSON");
    minstall::save_catalog(catalog, exported);
    const auto loaded = minstall::load_catalog(exported);
    check(loaded.categories[0].name == "Я" && loaded.categories[1].name == "A", "Saved category order");
    check(loaded.categories[0].programs[0].fields == catalog.categories[0].programs[0].fields, "Saved program extensions");
    check(loaded.extra_fields == catalog.extra_fields, "Root extensions survive save and reload");
    const auto before = read(exported);
    rejects([&] { minstall::save_catalog({}, exported); });
    check(read(exported) == before, "Overwrite requires explicit consent");
    minstall::save_catalog({}, exported, true);
    check(minstall::load_catalog(exported).categories.empty(), "Confirmed atomic replacement and empty export");

    auto invalid = catalog;
    invalid.categories[0].programs[0].fields["name"] = false;
    const auto empty_export = read(exported);
    rejects([&] { minstall::save_catalog(invalid, exported, true); });
    check(read(exported) == empty_export, "Validation failure preserves existing export");
    invalid = catalog;
    invalid.categories.push_back(invalid.categories[0]);
    rejects([&] { minstall::serialize_catalog(invalid); });
    invalid = catalog;
    invalid.extra_fields["_version"] = 3;
    rejects([&] { minstall::serialize_catalog(invalid); });
    invalid = catalog;
    invalid.extra_fields = false;
    rejects([&] { minstall::serialize_catalog(invalid); });
    rejects([&] { minstall::save_catalog(catalog, base / "installer.exe"); });
    rejects([&] { minstall::save_catalog(catalog, base / "missing" / "catalog.json"); });
    fs::create_directory(base / "directory.json");
    rejects([&] { minstall::save_catalog(catalog, base / "directory.json", true); });
    fs::create_symlink(exported, base / "linked.json", link_error);
    if (!link_error) {
        rejects([&] { minstall::save_catalog(catalog, base / "linked.json", true); });
        check(read(exported) == empty_export, "Export does not follow destination links");
    }
    no_temporary_files(base);
    return checks;
}

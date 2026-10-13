#include "minstall/metadata.hpp"
#include "minstall/storage.hpp"

#include <map>
#include <set>

#ifdef _WIN32
#include <windows.h>
#endif

namespace minstall {
namespace {
namespace fs = std::filesystem;
constexpr const char* metadata_kind = "minstall-program-metadata-v1";

void validate_details(const ProgramDetails& details) {
    if (details.name.empty() || details.name.find_first_not_of(" \t\r\n") == std::string::npos) {
        throw CatalogError("Укажите название программы.");
    }
    if (details.name.size() > 1024 || details.version.size() > 256 || details.description.size() > 65536) {
        throw CatalogError("Слишком длинный текст: название до 1024 байт, версия до 256, описание до 64 КиБ UTF-8.");
    }
    for (const auto* text : {&details.name, &details.version, &details.description}) {
        for (const auto c : *text) {
            const bool line_break = text == &details.description && (c == '\n' || c == '\r' || c == '\t');
            if ((static_cast<unsigned char>(c) < 32 || c == 127) && !line_break) {
                throw CatalogError("В сведениях о программе есть недопустимый управляющий символ.");
            }
        }
    }
}

void validate_metadata(const Catalog& catalog) {
    if (!catalog.extra_fields.contains("_kind") || catalog.extra_fields.at("_kind") != metadata_kind) {
        throw CatalogError("Ожидался файл сведений MInstAll, а не обычный каталог.");
    }
    std::set<std::string> paths;
    for (const auto& category : catalog.categories) {
        for (const auto& program : category.programs) {
            const auto& fields = program.fields;
            if (!fields.contains("source_path") || !fields.at("source_path").is_string() ||
                fields.at("source_path").get_ref<const std::string&>().empty()) {
                throw CatalogError("В записи сведений отсутствует путь установочного файла.");
            }
            if (!paths.insert(program.text("source_path")).second) {
                throw CatalogError("В файле сведений повторяется путь установочного файла.");
            }
            validate_details({program.name(), program.text("version"), program.text("desc")});
        }
    }
}

class MetadataLock final {
public:
    explicit MetadataLock(const fs::path& path) : path_(path) {
        if (!fs::create_directory(path_)) {
            throw CatalogError("Сведения уже сохраняются в другом окне. Повторите попытку позже.\n"
                "Если все окна MInstAll закрыты, проверьте оставшуюся папку program-metadata.json.lock.");
        }
    }
    ~MetadataLock() {
        std::error_code error;
        fs::remove(path_, error);
    }
    MetadataLock(const MetadataLock&) = delete;
    MetadataLock& operator=(const MetadataLock&) = delete;
private:
    fs::path path_;
};
}  // namespace

fs::path metadata_path(const fs::path& software) {
    return software.parent_path() / "program-metadata.json";
}

MetadataSnapshot load_metadata(const fs::path& software) {
    const auto path = metadata_path(software);
    try {
        MetadataSnapshot result;
        result.catalog.extra_fields["_kind"] = metadata_kind;
        const auto status = fs::symlink_status(path);
        if (!fs::exists(status)) {
            return result;
        }
        if (!fs::is_regular_file(status)) {
            throw CatalogError("Файл сведений должен быть обычным файлом, а не папкой или ссылкой.");
        }
#ifdef _WIN32
        const auto attributes = GetFileAttributesW(path.c_str());
        if (attributes == INVALID_FILE_ATTRIBUTES || (attributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0) {
            throw CatalogError("Файл сведений не должен быть ссылкой или reparse point.");
        }
#endif
        result.catalog = load_catalog(path);
        validate_metadata(result.catalog);
        result.exists = true;
        return result;
    } catch (const fs::filesystem_error&) {
        throw CatalogError("Не удалось проверить program-metadata.json. Проверьте права доступа.");
    } catch (const CatalogError& error) {
        throw CatalogError(std::string("Не удалось прочитать program-metadata.json.\n") + error.what());
    }
}

void apply_metadata(Catalog& discovered, const MetadataSnapshot& metadata) {
    std::map<std::string, const Program*> entries;
    for (const auto& category : metadata.catalog.categories) {
        for (const auto& program : category.programs) {
            entries.emplace(program.text("source_path"), &program);
        }
    }
    for (auto& category : discovered.categories) {
        for (auto& program : category.programs) {
            const auto found = entries.find(program.text("source_path"));
            if (found != entries.end()) {
                // Never open a metadata path, copy commands or create catalog entries from it.
                for (const auto* key : {"name", "version", "desc"}) {
                    if (found->second->fields.contains(key)) {
                        program.fields[key] = found->second->fields.at(key);
                    }
                }
            }
        }
    }
}

void save_program_details(const fs::path& software, const std::string& source_path,
                          const ProgramDetails& details, const MetadataSnapshot& expected) {
    validate_details(details);
    try {
        auto lock_path = metadata_path(software);
        lock_path += ".lock";
        MetadataLock lock(lock_path);
        auto current = load_metadata(software);
        if (current.exists != expected.exists || serialize_catalog(current.catalog) != serialize_catalog(expected.catalog)) {
            throw CatalogError("Сведения изменились после открытия редактора. Закройте его и откройте снова.");
        }
        const auto discovered = scan_software(software);
        bool present = false;
        for (const auto& category : discovered.categories) {
            for (const auto& program : category.programs) {
                present = present || program.text("source_path") == source_path;
            }
        }
        if (!present) {
            throw CatalogError("Установочный файл больше не найден. Закройте редактор и обновите список (F5).");
        }
        Program* target = nullptr;
        for (auto& category : current.catalog.categories) {
            for (auto& program : category.programs) {
                if (program.text("source_path") == source_path) {
                    target = &program;
                }
            }
        }
        if (!target) {
            if (current.catalog.categories.empty()) {
                current.catalog.categories.push_back({"Programs", {}});
            }
            auto& entries = current.catalog.categories.front().programs;
            entries.push_back({Json{{"name", details.name}, {"cmd", ""}, {"source_path", source_path}}});
            target = &entries.back();
        }
        target->fields["name"] = details.name;
        target->fields["version"] = details.version;
        target->fields["desc"] = details.description;
        save_catalog(current.catalog, metadata_path(software), current.exists);
    } catch (const fs::filesystem_error&) {
        throw CatalogError("Не удалось сохранить сведения. Проверьте права записи рядом с программой.");
    }
}
}  // namespace minstall

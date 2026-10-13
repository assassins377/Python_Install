#include "minstall/storage.hpp"

#include <array>
#include <fstream>
#include <random>
#include <system_error>
#include <utility>

#ifdef _WIN32
#include <windows.h>
#endif

namespace minstall {
namespace {
namespace fs = std::filesystem;

bool is_link(const fs::path& path) {
    if (fs::is_symlink(fs::symlink_status(path))) {
        return true;
    }
#ifdef _WIN32
    const auto attributes = GetFileAttributesW(path.c_str());
    return attributes == INVALID_FILE_ATTRIBUTES || (attributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0;
#else
    return false;
#endif
}

void ensure_directory(const fs::path& path) {
    if (!fs::exists(path)) {
        fs::create_directory(path);
    }
    if (!fs::is_directory(path) || is_link(path)) {
        throw CatalogError("Папка назначения должна быть обычной папкой, а не ссылкой.");
    }
}

std::string lower_ascii(std::string value) {
    for (auto& c : value) {
        if (c >= 'A' && c <= 'Z') {
            c = static_cast<char>(c + ('a' - 'A'));
        }
    }
    return value;
}

void validate_category(const std::string& name) {
    if (name.empty()) {
        return;
    }
    if (name == "." || name == ".." || name.size() > 240 || name.back() == '.' ||
        name.front() == ' ' || name.back() == ' ' || name.find_first_of("/\\:*?\"<>|") != std::string::npos) {
        throw CatalogError("Категория должна быть именем одной подпапки без разделителей пути.");
    }
    for (const auto c : name) {
        if (static_cast<unsigned char>(c) < 32 || c == 127) {
            throw CatalogError("В имени категории есть недопустимый символ.");
        }
    }
    const auto stem = lower_ascii(name.substr(0, name.find('.')));
    if (stem == "con" || stem == "prn" || stem == "aux" || stem == "nul" ||
        (stem.size() == 4 && (stem.substr(0, 3) == "com" || stem.substr(0, 3) == "lpt") &&
         stem[3] >= '1' && stem[3] <= '9')) {
        throw CatalogError("Это имя категории зарезервировано системой. Выберите другое.");
    }
    try {
        static_cast<void>(Json(name).dump());  // Reject malformed UTF-8 before creating paths.
    } catch (const Json::exception&) {
        throw CatalogError("Некорректное имя категории.");
    }
}

class TemporaryFile {
public:
    explicit TemporaryFile(const fs::path& parent) {
        std::random_device random;
        for (int attempt = 0; attempt < 32; ++attempt) {
            const auto candidate = parent / (".minstall-write-" + std::to_string(random()) + "-" +
                                            std::to_string(random()));
            if (fs::create_directory(candidate)) {
                directory_ = candidate;
                return;
            }
        }
        throw CatalogError("Не удалось создать временный файл.");
    }
    ~TemporaryFile() {
        std::error_code error;
        fs::remove_all(directory_, error);
    }
    TemporaryFile(const TemporaryFile&) = delete;
    TemporaryFile& operator=(const TemporaryFile&) = delete;
    fs::path path() const { return directory_ / "payload.tmp"; }

private:
    fs::path directory_;
};

void publish(const fs::path& temporary, const fs::path& target, bool replace) {
#ifdef _WIN32
    const DWORD flags = MOVEFILE_WRITE_THROUGH | (replace ? MOVEFILE_REPLACE_EXISTING : 0UL);
    if (!MoveFileExW(temporary.c_str(), target.c_str(), flags)) {
        throw CatalogError("Не удалось сохранить файл. Проверьте доступ и наличие файла с таким именем.");
    }
#else
    if (replace) {
        fs::rename(temporary, target);
    } else {
        // rename() overwrites on POSIX. Linking publishes atomically without clobbering
        // a destination created by another process after our initial check.
        fs::create_hard_link(temporary, target);
    }
#endif
}
}  // namespace

std::string serialize_catalog(const Catalog& catalog) {
    try {
        Json categories = Json::object();
        for (const auto& category : catalog.categories) {
            if (categories.contains(category.name)) {
                throw CatalogError("Нельзя сохранить категории с одинаковыми именами.");
            }
            Json entries = Json::array();
            for (const auto& program : category.programs) {
                entries.push_back(program.fields);
            }
            categories[category.name] = std::move(entries);
        }
        Json root{{"_version", 2}, {"categories", std::move(categories)}};
        if (!catalog.extra_fields.is_object()) {
            throw CatalogError("Дополнительные поля каталога должны быть объектом.");
        }
        for (auto it = catalog.extra_fields.begin(); it != catalog.extra_fields.end(); ++it) {
            if (root.contains(it.key())) {
                throw CatalogError("Дополнительное поле совпадает с обязательным полем каталога.");
            }
            root[it.key()] = it.value();
        }
        auto bytes = root.dump(2) + '\n';
        static_cast<void>(parse_catalog(bytes));  // Enforce the reader's complete contract before writing.
        return bytes;
    } catch (const Json::exception&) {
        throw CatalogError("Не удалось преобразовать каталог в UTF-8 JSON.");
    }
}

void save_catalog(const Catalog& catalog, const fs::path& path, bool replace) {
    const auto bytes = serialize_catalog(catalog);
    if (lower_ascii(path.extension().u8string()) != ".json") {
        throw CatalogError("Выберите файл с расширением .json.");
    }
    try {
        const auto target = fs::absolute(path);
        if (fs::exists(fs::symlink_status(target))) {
            if (!replace) {
                throw CatalogError("Файл уже существует. Подтвердите замену или выберите другое имя.");
            }
            if (!fs::is_regular_file(target) || is_link(target)) {
                throw CatalogError("Нельзя заменять папку или ссылку файлом каталога.");
            }
        }
        TemporaryFile temporary(target.parent_path());
        std::ofstream stream(temporary.path(), std::ios::binary);
        stream.write(bytes.data(), static_cast<std::streamsize>(bytes.size()));
        stream.close();
        if (!stream) {
            throw CatalogError("Не удалось полностью записать каталог. Прежний файл сохранён.");
        }
        publish(temporary.path(), target, replace);
    } catch (const fs::filesystem_error&) {
        throw CatalogError("Не удалось сохранить каталог. Проверьте путь, свободное место и права записи.");
    }
}

ImportResult import_installer(const fs::path& source, const fs::path& software,
                             const std::string& category, const CopyProgress& progress) {
    validate_category(category);
    try {
        if (!fs::is_regular_file(source) || is_link(source) || !is_supported_installer(source)) {
            throw CatalogError("Выберите обычный установочный файл поддерживаемого формата, а не папку или ссылку.");
        }
        ensure_directory(software);
        const auto destination = category.empty() ? software : software / fs::u8path(category);
        ensure_directory(destination);
        const auto target = destination / source.filename();
        if (fs::exists(fs::symlink_status(target))) {
            if (!is_link(target) && fs::equivalent(source, target)) {
                return {target, false};
            }
            throw CatalogError("Файл с таким именем уже есть в этой категории. Выберите другую категорию.");
        }
        const auto size = fs::file_size(source);
        const auto modified = fs::last_write_time(source);
        TemporaryFile temporary(destination);
        std::ifstream input(source, std::ios::binary);
        std::ofstream output(temporary.path(), std::ios::binary);
        if (!input || !output) {
            throw CatalogError("Не удалось открыть файлы для копирования.");
        }
        std::array<char, 64 * 1024> buffer{};
        std::uintmax_t copied = 0;
        if (progress && !progress(0, size)) {
            throw ImportCancelled();
        }
        while (input) {
            input.read(buffer.data(), static_cast<std::streamsize>(buffer.size()));
            const auto count = input.gcount();
            output.write(buffer.data(), count);
            copied += static_cast<std::uintmax_t>(count);
            if (!output || (progress && !progress(copied, size))) {
                if (!output) {
                    throw CatalogError("Не удалось полностью скопировать файл. Проверьте свободное место.");
                }
                throw ImportCancelled();
            }
        }
        output.close();
        if (!input.eof() || !output || copied != size || fs::last_write_time(source) != modified) {
            throw CatalogError("Исходный файл изменился или не удалось завершить копирование. Повторите добавление.");
        }
        publish(temporary.path(), target, false);
        return {target, true};
    } catch (const fs::filesystem_error&) {
        throw CatalogError("Не удалось добавить файл. Проверьте папку software, права записи и наличие файла с таким именем.");
    }
}
}  // namespace minstall

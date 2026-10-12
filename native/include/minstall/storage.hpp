#pragma once

#include "minstall/catalog.hpp"

#include <cstdint>
#include <functional>

namespace minstall {
using CopyProgress = std::function<bool(std::uintmax_t copied, std::uintmax_t total)>;

class ImportCancelled final : public CatalogError {
public:
    ImportCancelled() : CatalogError("Добавление отменено.") {}
};

struct ImportResult {
    std::filesystem::path path;
    bool copied;
};

// category is one optional subdirectory, never a command or arbitrary path.
// Originals are retained; an existing destination is never overwritten.
ImportResult import_installer(const std::filesystem::path& source,
                             const std::filesystem::path& software,
                             const std::string& category, const CopyProgress& progress = {});
std::string serialize_catalog(const Catalog& catalog);
// Publish a complete validated snapshot; replace requires explicit caller consent.
void save_catalog(const Catalog& catalog, const std::filesystem::path& path, bool replace = false);
}  // namespace minstall

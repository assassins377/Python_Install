#pragma once

#include "minstall/catalog.hpp"

namespace minstall {
struct MetadataSnapshot {
    Catalog catalog;
    bool exists = false;
};

struct ProgramDetails {
    std::string name;
    std::string version;
    std::string description;
};

std::filesystem::path metadata_path(const std::filesystem::path& software);
MetadataSnapshot load_metadata(const std::filesystem::path& software);
// Metadata can change these three labels only, never program membership or commands.
void apply_metadata(Catalog& discovered, const MetadataSnapshot& metadata);
// Reject a stale editor snapshot or an installer that has disappeared.
void save_program_details(const std::filesystem::path& software, const std::string& source_path,
                          const ProgramDetails& details, const MetadataSnapshot& expected);
}  // namespace minstall

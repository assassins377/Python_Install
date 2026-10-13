#pragma once

#include "minstall/catalog.hpp"

#include <iosfwd>

namespace minstall {
inline constexpr const char* prototype_version = "MInstAll Native 0.1.0 (catalog prototype; Python baseline 2.2.2)";

struct Options {
    enum class Mode { gui, list, version, help } mode = Mode::gui;
    std::filesystem::path catalog;
    std::filesystem::path software;
    bool explicit_catalog = false;
    bool gui_smoke = false;
    bool check_installed = false;
};

Options parse_options(const std::vector<std::string>& args, const std::filesystem::path& default_catalog);
Catalog load_programs(const Options& options);
int run_cli(const Options& options, std::ostream& output, std::ostream& errors);
std::filesystem::path default_catalog_path();
}  // namespace minstall

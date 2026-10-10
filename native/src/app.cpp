#include "minstall/app.hpp"

#include <ostream>

namespace minstall {
Options parse_options(const std::vector<std::string>& args, const std::filesystem::path& default_catalog) {
    Options options;
    options.catalog = default_catalog;
    bool mode_seen = false;
    bool catalog_seen = false;
    for (std::size_t i = 0; i < args.size(); ++i) {
        const auto& arg = args[i];
        if (arg == "--catalog") {
            if (catalog_seen || ++i == args.size() || args[i].empty() || args[i].rfind("--", 0) == 0) {
                throw CatalogError("--catalog requires one file path and may only appear once");
            }
            options.catalog = std::filesystem::u8path(args[i]);
            catalog_seen = true;
        } else if (arg == "--gui-smoke") {
            if (options.gui_smoke) {
                throw CatalogError("Duplicate --gui-smoke");
            }
            options.gui_smoke = true;
        } else {
            if (mode_seen) {
                throw CatalogError("Choose one mode: --list, --version or --help");
            }
            if (arg == "--list") {
                options.mode = Options::Mode::list;
            } else if (arg == "--version") {
                options.mode = Options::Mode::version;
            } else if (arg == "--help" || arg == "-h") {
                options.mode = Options::Mode::help;
            } else {
                throw CatalogError("Unsupported argument (installation is not implemented); use --help");
            }
            mode_seen = true;
        }
    }
    if (options.gui_smoke && options.mode != Options::Mode::gui) {
        throw CatalogError("--gui-smoke cannot be combined with CLI modes");
    }
    return options;
}

int run_cli(const Options& options, std::ostream& output, std::ostream& errors) {
    if (options.mode == Options::Mode::version) {
        output << prototype_version << '\n';
        return 0;
    }
    if (options.mode == Options::Mode::help) {
        output << prototype_version << "\nUsage: minstall-native [--list | --version | --help] [--catalog FILE]\n"
               << "No mode: open the read-only wxWidgets catalog browser.\n"
               << "--list: list every catalog entry in source order; installed status is not checked.\n"
               << "Default catalog: programs.json beside the executable. No installation or downloads.\n"
               << "Exit codes: 0 success; 3 invalid arguments, catalog or unavailable GUI.\n";
        return 0;
    }
    if (options.mode != Options::Mode::list) {
        errors << "GUI is unavailable in this build. Use --list.\n";
        return 3;
    }
    const auto catalog = load_catalog(options.catalog);
    if (catalog.categories.empty()) {
        errors << "Catalog has no categories.\n";
        return 3;
    }
    for (const auto& category : catalog.categories) {
        output << "\n[" << display_text(category.name) << "]\n";
        for (const auto& program : category.programs) {
            output << "  " << display_text(program.name());
            const auto version = program.text("version");
            if (!version.empty()) {
                output << " (" << display_text(version) << ')';
            }
            output << '\n';
        }
    }
    errors << "Total: " << catalog.program_count() << " (catalog only; installed status not checked)\n";
    return 0;
}
}  // namespace minstall

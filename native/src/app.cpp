#include "minstall/app.hpp"
#include "minstall/metadata.hpp"
#include "minstall/detection.hpp"

#include <ostream>

namespace minstall {
Options parse_options(const std::vector<std::string>& args, const std::filesystem::path& default_catalog) {
    Options options;
    options.catalog = default_catalog;
    options.software = default_catalog.parent_path() / "software";
    bool mode_seen = false;
    bool catalog_seen = false;
    for (std::size_t i = 0; i < args.size(); ++i) {
        const auto& arg = args[i];
        if (arg == "--catalog") {
            if (catalog_seen || ++i == args.size() || args[i].empty() || args[i].rfind("--", 0) == 0) {
                throw CatalogError("--catalog requires one file path and may only appear once");
            }
            options.catalog = std::filesystem::u8path(args[i]);
            options.explicit_catalog = true;
            catalog_seen = true;
        } else if (arg == "--check-installed") {
            if (options.check_installed) {
                throw CatalogError("Duplicate --check-installed");
            }
            options.check_installed = true;
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
    if (options.check_installed && options.mode != Options::Mode::list) {
        throw CatalogError("--check-installed requires --list");
    }
    return options;
}

Catalog load_programs(const Options& options) {
    if (options.explicit_catalog) {
        return load_catalog(options.catalog);
    }
    auto catalog = scan_software(options.software);
    apply_metadata(catalog, load_metadata(options.software));
    return catalog;
}

int run_cli(const Options& options, std::ostream& output, std::ostream& errors) {
    if (options.mode == Options::Mode::version) {
        output << prototype_version << '\n';
        return 0;
    }
    if (options.mode == Options::Mode::help) {
        output << prototype_version << "\nUsage: minstall-native [--list | --version | --help] [--catalog FILE] [--check-installed]\n"
               << "No mode: open the wxWidgets catalog browser with installer import and JSON export.\n"
               << "Default: discover installer files in software/ beside the executable.\n"
               << "Missing/empty software/: an empty list (success). No example programs.\n"
               << "Saved names, versions and descriptions apply only to discovered files.\n"
               << "--catalog FILE: explicitly read a JSON catalog instead of scanning software/.\n"
               << "--list: list discovered files or explicit catalog entries; installed status is not checked.\n"
               << "--list --check-installed: also show read-only installed status and installed version.\n"
               << "Windows: uninstall registry; unsupported/incomplete detection is reported as unknown.\n"
               << "No installation or downloads.\n"
               << "Exit codes: 0 success; 3 invalid arguments, catalog or unavailable GUI.\n";
        return 0;
    }
    if (options.mode != Options::Mode::list) {
        errors << "GUI is unavailable in this build. Use --list.\n";
        return 3;
    }
    const auto catalog = load_programs(options);
    std::vector<std::vector<ProgramStatus>> statuses;
    if (options.check_installed && catalog.program_count() != 0) {
        const auto snapshot = collect_installed_snapshot(catalog, options.software.parent_path());
        statuses = detect_catalog(catalog, snapshot);
    }
    for (std::size_t c = 0; c < catalog.categories.size(); ++c) {
        const auto& category = catalog.categories[c];
        output << "\n[" << display_text(category.name) << "]\n";
        for (std::size_t p = 0; p < category.programs.size(); ++p) {
            const auto& program = category.programs[p];
            output << "  " << display_text(program.name());
            const auto version = program.text("version");
            if (!version.empty()) {
                output << " (" << display_text(version) << ')';
            }
            if (options.check_installed) {
                const auto& status = statuses[c][p];
                output << " [" << (status.applicable ? installation_state_name(status.state) : "not-applicable");
                if (status.runnable) {
                    output << "; runnable";
                }
                if (status.outdated) {
                    output << "; outdated";
                }
                if (!status.installed_version.empty()) {
                    output << "; installed version: " << display_text(status.installed_version);
                }
                output << ']';
            }
            output << '\n';
        }
    }
    errors << "Total: " << catalog.program_count()
           << (options.check_installed ? " (read-only installed detection)\n" :
                                        " (catalog only; installed status not checked)\n");
    return 0;
}
}  // namespace minstall

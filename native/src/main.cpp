#include "minstall/app.hpp"

#include <iostream>

#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#endif

#ifdef MINSTALL_WITH_GUI
int run_gui(const minstall::Options& options);
#endif

namespace {
int run(const std::vector<std::string>& args) {
    try {
        const auto options = minstall::parse_options(args, minstall::default_catalog_path());
#ifdef MINSTALL_WITH_GUI
        if (options.mode == minstall::Options::Mode::gui) {
            return run_gui(options);
        }
#endif
        return minstall::run_cli(options, std::cout, std::cerr);
    } catch (const minstall::CatalogError& error) {
        std::cerr << "Error: " << error.what() << '\n';
    } catch (const std::exception&) {
        std::cerr << "Error: native prototype could not complete the request\n";
    }
    return 3;
}
}  // namespace

#ifdef _WIN32
int wmain(int argc, wchar_t** argv) {
    // Keep redirected output UTF-8; configure only an attached console.
    const auto previous_output_cp = GetConsoleOutputCP();
    if (previous_output_cp != 0) {
        SetConsoleOutputCP(CP_UTF8);
    }
    int result = 3;
    try {
        std::vector<std::string> args;
        for (int i = 1; i < argc; ++i) {
            args.push_back(std::filesystem::path(argv[i]).u8string());
        }
        result = run(args);
    } catch (const std::exception&) {
        std::cerr << "Error: invalid Unicode command line\n";
    }
    if (previous_output_cp != 0) {
        SetConsoleOutputCP(previous_output_cp);
    }
    return result;
}
#else
int main(int argc, char** argv) {
    return run(std::vector<std::string>(argv + 1, argv + argc));
}
#endif

#include "minstall/app.hpp"

#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#elif defined(__linux__)
#include <unistd.h>
#endif

namespace minstall {
std::filesystem::path default_catalog_path() {
#ifdef _WIN32
    std::vector<wchar_t> buffer(32768);
    const auto length = GetModuleFileNameW(nullptr, buffer.data(), static_cast<DWORD>(buffer.size()));
    if (length == 0 || length >= buffer.size()) {
        throw CatalogError("Cannot locate executable; supply --catalog FILE");
    }
    return std::filesystem::path(std::wstring(buffer.data(), length)).parent_path() / L"programs.json";
#elif defined(__linux__)
    std::vector<char> buffer(32768);
    const auto length = readlink("/proc/self/exe", buffer.data(), buffer.size());
    if (length < 0 || static_cast<std::size_t>(length) >= buffer.size()) {
        throw CatalogError("Cannot locate executable; supply --catalog FILE");
    }
    return std::filesystem::path(std::string(buffer.data(), static_cast<std::size_t>(length))).parent_path() / "programs.json";
#else
#error "The native prototype currently targets Windows and Linux"
#endif
}
}  // namespace minstall

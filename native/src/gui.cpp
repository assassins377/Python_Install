#include "minstall/app.hpp"

#include <array>
#include <iostream>
#include <utility>
#include <wx/wx.h>
#include <wx/splitter.h>
#include <wx/treectrl.h>

namespace minstall_ui {
minstall::Options gui_options;
int gui_result = 0;

enum MenuId {
    id_add_program = wxID_HIGHEST + 1,
    id_save_catalog,
    id_parallel,
    id_autoscan,
    id_generate_catalog,
    id_hide_missing,
    id_installed_cache,
    id_language,
    id_profiles,
    id_check_updates,
    id_show_log,
    id_program_updates,
    id_rescan,
    id_add_guide,
    id_watcher_first
};
constexpr std::array<int, 5> watcher_intervals{0, 3000, 10000, 30000, 60000};

wxString utf8(const std::string& value) {
    return wxString::FromUTF8(value.data(), value.size());
}

std::string program_details(const minstall::Program& program) {
    const auto version = program.text("version");
    const auto description = program.text("desc");
    return minstall::display_text(program.name()) + "\n" +
           "Версия: " + (version.empty() ? "Не указана" : minstall::display_text(version)) +
           "\n\nОписание\n" + (description.empty() ? "Описание пока не добавлено." : description);
}

class ProgramData final : public wxTreeItemData {
public:
    explicit ProgramData(const minstall::Program& value) : program(value) {}
    minstall::Program program;
};

bool same_catalog(const minstall::Catalog& left, const minstall::Catalog& right) {
    if (left.categories.size() != right.categories.size()) {
        return false;
    }
    for (std::size_t c = 0; c < left.categories.size(); ++c) {
        const auto& a = left.categories[c];
        const auto& b = right.categories[c];
        if (a.name != b.name || a.programs.size() != b.programs.size()) {
            return false;
        }
        for (std::size_t p = 0; p < a.programs.size(); ++p) {
            if (a.programs[p].fields != b.programs[p].fields) {
                return false;
            }
        }
    }
    return true;
}

class CatalogFrame final : public wxFrame {
public:
    CatalogFrame(minstall::Catalog catalog, const minstall::Options& options)
        : wxFrame(nullptr, wxID_ANY, utf8(minstall::prototype_version), wxDefaultPosition, wxSize(960, 620)),
          options_(options), refresh_timer_(this) {
        auto* panel = new wxPanel(this);
        auto* layout = new wxBoxSizer(wxVERTICAL);
        layout->Add(new wxStaticText(panel, wxID_ANY,
                    utf8("Просмотр каталога · Установка отключена · Состояние программ не проверяется")),
                    0, wxALL, 12);
        auto* splitter = new wxSplitterWindow(panel, wxID_ANY);
        tree_ = new wxTreeCtrl(splitter, wxID_ANY, wxDefaultPosition, wxDefaultSize,
                              wxTR_DEFAULT_STYLE | wxTR_HIDE_ROOT | wxTR_SINGLE);
        details_ = new wxTextCtrl(splitter, wxID_ANY, utf8("Выберите программу в каталоге."),
                                  wxDefaultPosition, wxDefaultSize, wxTE_MULTILINE | wxTE_READONLY);
        splitter->SetMinimumPaneSize(180);
        splitter->SplitVertically(tree_, details_, 330);
        layout->Add(splitter, 1, wxEXPAND | wxLEFT | wxRIGHT | wxBOTTOM, 12);
        panel->SetSizer(layout);
        tree_->Bind(wxEVT_TREE_SEL_CHANGED, [this](wxTreeEvent& event) {
            if (rebuilding_ || !event.GetItem().IsOk()) {
                return;
            }
            const auto* data = dynamic_cast<ProgramData*>(tree_->GetItemData(event.GetItem()));
            if (data) {
                // Never load icon paths, follow URLs or execute commands.
                details_->ChangeValue(utf8(program_details(data->program)));
            } else {
                details_->ChangeValue(utf8("Выберите программу в категории."));
            }
        });
        CreateStatusBar();
        create_menu();
        show_catalog(std::move(catalog));
        Bind(wxEVT_TIMER, [this](wxTimerEvent&) { refresh_catalog(); }, refresh_timer_.GetId());
        set_watcher_interval(3000);
        SetMinSize(wxSize(640, 400));
        Centre();
    }

    bool smoke_check() {
        const auto* menus = GetMenuBar();
        const bool scanning = !options_.explicit_catalog;
        if (!menus || menus->GetMenuCount() != 4 ||
            menus->GetMenuLabelText(0) != utf8("Файл") ||
            menus->GetMenuLabelText(1) != utf8("Настройки") ||
            menus->GetMenuLabelText(2) != utf8("Профили") ||
            menus->GetMenuLabelText(3) != utf8("Справка") ||
            menus->IsEnabled(id_add_program) || menus->IsEnabled(id_save_catalog) ||
            menus->IsEnabled(id_rescan) != scanning || refresh_timer_.IsRunning() != scanning) {
            return false;
        }
        if (scanning) {
            // Exercise the same event bindings used by the menu, not just setters.
            wxCommandEvent off(wxEVT_MENU, id_watcher_first);
            GetEventHandler()->ProcessEvent(off);
            if (refresh_timer_.IsRunning() || !menus->IsChecked(id_watcher_first)) {
                return false;
            }
            wxCommandEvent rescan(wxEVT_MENU, id_rescan);
            GetEventHandler()->ProcessEvent(rescan);
            wxCommandEvent resume(wxEVT_MENU, id_watcher_first + 1);
            GetEventHandler()->ProcessEvent(resume);
            if (!refresh_timer_.IsRunning() || refresh_timer_.GetInterval() != 3000 ||
                !menus->IsChecked(id_watcher_first + 1)) {
                return false;
            }
        }
        const auto expected = catalog_.categories.size() + catalog_.program_count();
        // wxMSW's hidden root is virtual; count descendants on every port.
        if (tree_->GetChildrenCount(tree_->GetRootItem(), true) != expected) {
            return false;
        }
        if (first_program_.IsOk()) {
            tree_->SelectItem(first_program_);
            const auto* data = static_cast<ProgramData*>(tree_->GetItemData(first_program_));
            return details_->GetValue() == utf8(program_details(data->program));
        }
        return details_->GetValue() == utf8(empty_message());
    }

private:
    void create_menu() {
        auto* menus = new wxMenuBar();
        const auto unavailable = [](wxMenu* menu, int id, const char* label) {
            menu->Append(id, utf8(label), utf8("Эта функция пока недоступна в прототипе."))->Enable(false);
        };
        const auto fixed_option = [](wxMenu* menu, int id, const char* label, bool checked, const char* help) {
            auto* item = menu->AppendCheckItem(id, utf8(label), utf8(help));
            item->Check(checked);
            item->Enable(false);
        };
        auto* file = new wxMenu();
        unavailable(file, id_add_program, "Добавить программу…");
        unavailable(file, id_save_catalog, "Сохранить каталог…");
        file->AppendSeparator();
        file->Append(wxID_EXIT, utf8("Выход\tCtrl+Q"));
        Bind(wxEVT_MENU, [this](wxCommandEvent&) { Close(); }, wxID_EXIT);
        menus->Append(file, utf8("&Файл"));

        auto* settings = new wxMenu();
        fixed_option(settings, id_parallel, "Параллельная установка", false, "Установка пока недоступна.");
        settings->AppendSeparator();
        const bool scanning = !options_.explicit_catalog;
        fixed_option(settings, id_autoscan, "Автосканирование software/", scanning,
                     "В обычном режиме папка проверяется при запуске.");
        fixed_option(settings, id_generate_catalog, "Генерировать каталог из software/", scanning,
                     "В обычном режиме список всегда формируется из доступных файлов.");
        fixed_option(settings, id_hide_missing, "Скрывать недоступные программы", scanning,
                     "В обычном режиме отсутствующие файлы не включаются в список.");
        auto* watcher = new wxMenu();
        for (std::size_t i = 0; i < watcher_intervals.size(); ++i) {
            const int interval = watcher_intervals[i];
            const int id = id_watcher_first + static_cast<int>(i);
            const auto label = interval == 0 ? std::string("Выключено") :
                "Каждые " + std::to_string(interval / 1000) + " сек";
            watcher->AppendRadioItem(id, utf8(label));
            Bind(wxEVT_MENU, [this, interval](wxCommandEvent&) { set_watcher_interval(interval); }, id);
        }
        watcher->Check(id_watcher_first + (scanning ? 1 : 0), true);
        settings->AppendSubMenu(watcher, utf8("Слежение за software/"),
            utf8("Интервал проверки папки; настройка действует до закрытия программы."))->Enable(scanning);
        fixed_option(settings, id_installed_cache, "Кеш списка установленных программ", false,
                     "Обнаружение установленного ПО пока недоступно.");
        settings->AppendSeparator();
        settings->Append(id_rescan, utf8("Пересканировать software/\tF5"),
                         utf8("Обновить список сейчас, даже если слежение выключено."))->Enable(scanning);
        Bind(wxEVT_MENU, [this](wxCommandEvent&) { refresh_catalog(); }, id_rescan);
        unavailable(settings, id_language, "Язык: Русский");
        menus->Append(settings, utf8("&Настройки"));

        auto* profiles = new wxMenu();
        unavailable(profiles, id_profiles, "Профили пока недоступны");
        menus->Append(profiles, utf8("&Профили"));

        auto* help = new wxMenu();
        unavailable(help, id_check_updates, "Проверить обновления…");
        fixed_option(help, id_show_log, "Показать лог", false, "Просмотр лога пока недоступен.");
        help->Append(id_add_guide, utf8("Инструкция: добавление программ"));
        Bind(wxEVT_MENU, [this](wxCommandEvent&) {
            wxMessageBox(utf8("1. Создайте папку software рядом с программой.\n"
                "2. Добавьте в неё установочные файлы. Подпапки станут категориями.\n"
                "3. Список обновится автоматически. Для ручной проверки нажмите F5.\n\n"
                "Интервал можно изменить в меню «Настройки → Слежение за software/».\n"
                "При выключенном слежении используйте F5.\n\n"
                "При запуске с --catalog показан выбранный JSON: сканирование отключено.\n"
                "Для обычного режима запустите программу без этого параметра.\n\n"
                "Серые пункты меню пока недоступны. Установка файлов ещё не выполняется."),
                utf8("Добавление программ"), wxOK | wxICON_INFORMATION, this);
        }, id_add_guide);
        unavailable(help, id_program_updates, "Проверить обновления программ");
        help->AppendSeparator();
        help->Append(wxID_ABOUT, utf8("О программе…\tF1"));
        Bind(wxEVT_MENU, [this](wxCommandEvent&) {
            wxMessageBox(utf8("MInstAll Native 0.1.0\n\n"
                "Просмотр доступных установочных файлов и каталога программ.\n"
                "Прототип переноса MInstAll 2.2.2 на C++ и wxWidgets.\n\n"
                "Установка, обновления и профили пока недоступны.\n"
                "Настройки слежения действуют до закрытия программы."),
                utf8("О программе"), wxOK | wxICON_INFORMATION, this);
        }, wxID_ABOUT);
        menus->Append(help, utf8("&Справка"));
        SetMenuBar(menus);
    }

    void set_watcher_interval(int interval) {
        if (options_.explicit_catalog) {
            return;
        }
        refresh_timer_.Stop();
        watcher_interval_ = interval;
        if (interval > 0) {
            refresh_timer_.Start(interval);
        }
        for (std::size_t i = 0; i < watcher_intervals.size(); ++i) {
            if (watcher_intervals[i] == interval) {
                GetMenuBar()->Check(id_watcher_first + static_cast<int>(i), true);
                break;
            }
        }
        if (catalog_.program_count() == 0 && !scan_failed_) {
            details_->ChangeValue(utf8(empty_message()));
        }
    }

    void refresh_catalog() {
        if (options_.explicit_catalog) {
            return;
        }
        try {
            auto refreshed = minstall::load_programs(options_);
            if (scan_failed_ || !same_catalog(catalog_, refreshed)) {
                show_catalog(std::move(refreshed));
            }
            scan_failed_ = false;
        } catch (const minstall::CatalogError&) {
            show_catalog({});
            scan_failed_ = true;
            details_->ChangeValue(utf8("Не удалось прочитать папку software.\n"
                "Проверьте доступ к папке и количество файлов (не более 10 000).\n"
                "Нажмите F5 для повторной проверки."));
            SetStatusText(utf8("Ошибка чтения папки software"));
        }
    }

    std::string empty_message() const {
        if (options_.explicit_catalog) {
            return "В выбранном каталоге пока нет программ.";
        }
        return std::string("Программ пока нет.\n\n"
            "Добавьте установочные файлы в папку software рядом с программой.\n") +
            (watcher_interval_ == 0 ? "Для обновления списка нажмите F5. Слежение за папкой выключено." :
                                     "Список появится автоматически. Подпапки станут категориями.");
    }

    void show_catalog(minstall::Catalog catalog) {
        std::string selected_path;
        const auto selected = tree_->GetSelection();
        if (selected.IsOk()) {
            const auto* data = dynamic_cast<ProgramData*>(tree_->GetItemData(selected));
            if (data) {
                selected_path = data->program.text("source_path");
            }
        }
        rebuilding_ = true;
        tree_->Freeze();
        tree_->DeleteAllItems();
        first_program_ = wxTreeItemId{};
        catalog_ = std::move(catalog);
        details_->ChangeValue(utf8(catalog_.program_count() == 0 ? empty_message() : "Выберите программу в каталоге."));
        const auto root = tree_->AddRoot("Catalog");
        wxTreeItemId restore_selection;
        for (const auto& category : catalog_.categories) {
            const auto category_item = tree_->AppendItem(root, utf8(minstall::display_text(category.name)));
            for (const auto& program : category.programs) {
                const auto item = tree_->AppendItem(category_item, utf8(minstall::display_text(program.name())),
                                                   -1, -1, new ProgramData(program));
                if (!first_program_.IsOk()) {
                    first_program_ = item;
                }
                if (!selected_path.empty() && program.text("source_path") == selected_path) {
                    restore_selection = item;
                }
            }
            tree_->Expand(category_item);
        }
        tree_->Thaw();
        rebuilding_ = false;
        if (restore_selection.IsOk()) {
            tree_->SelectItem(restore_selection);
        }
        SetStatusText(utf8("Категорий: " + std::to_string(catalog_.categories.size()) +
                          " · Программ: " + std::to_string(catalog_.program_count())));
    }

    minstall::Options options_;
    wxTimer refresh_timer_;
    int watcher_interval_ = 3000;
    bool rebuilding_ = false;
    bool scan_failed_ = false;
    minstall::Catalog catalog_;
    wxTreeCtrl* tree_ = nullptr;
    wxTextCtrl* details_ = nullptr;
    wxTreeItemId first_program_;
};

class CatalogApp final : public wxApp {
public:
    bool OnInit() override {
        try {
            auto* frame = new CatalogFrame(minstall::load_programs(gui_options), gui_options);
            SetTopWindow(frame);
            frame->Show();
            if (gui_options.gui_smoke) {
                CallAfter([frame] {
                    gui_result = frame->smoke_check() ? 0 : 3;
                    frame->Close();
                });
            }
            return true;
        } catch (const minstall::CatalogError& error) {
            gui_result = 3;
            std::cerr << "Error: " << error.what() << '\n';
            if (!gui_options.gui_smoke) {
                wxMessageBox(utf8(error.what()), "MInstAll Native", wxOK | wxICON_ERROR);
            }
            return false;
        }
    }
};
}  // namespace minstall_ui

wxIMPLEMENT_APP_NO_MAIN(minstall_ui::CatalogApp);

int run_gui(const minstall::Options& options) {
    minstall_ui::gui_options = options;
    minstall_ui::gui_result = 0;
    int argc = 1;
    char name[] = "minstall-native";
    char* argv[] = {name, nullptr};
    const int result = wxEntry(argc, argv);
    return result == 0 ? minstall_ui::gui_result : 3;
}

#include "minstall/app.hpp"
#include "minstall/metadata.hpp"
#include "minstall/storage.hpp"

#include <algorithm>
#include <array>
#include <iostream>
#include <utility>
#include <wx/wx.h>
#include <wx/splitter.h>
#include <wx/treectrl.h>
#include <wx/filedlg.h>
#include <wx/progdlg.h>

namespace minstall_ui {
minstall::Options gui_options;
int gui_result = 0;

enum MenuId {
    id_add_program = wxID_HIGHEST + 1,
    id_edit_program,
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

std::filesystem::path file_path(const wxString& value) {
    return std::filesystem::u8path(value.ToStdString(wxConvUTF8));
}

class PauseWatcher final {
public:
    explicit PauseWatcher(wxTimer& timer) : timer_(timer), interval_(timer.IsRunning() ? timer.GetInterval() : 0) {
        timer_.Stop();
    }
    ~PauseWatcher() {
        if (interval_ > 0) {
            timer_.Start(interval_);
        }
    }
private:
    wxTimer& timer_;
    int interval_;
};

class AddProgramDialog final : public wxDialog {
public:
    explicit AddProgramDialog(wxWindow* parent)
        : wxDialog(parent, wxID_ANY, utf8("Добавить программу"), wxDefaultPosition, wxDefaultSize,
                   wxDEFAULT_DIALOG_STYLE | wxRESIZE_BORDER) {
        auto* layout = new wxBoxSizer(wxVERTICAL);
        layout->Add(new wxStaticText(this, wxID_ANY, utf8("Установочный файл")), 0, wxLEFT | wxRIGHT | wxTOP, 16);
        auto* file_row = new wxBoxSizer(wxHORIZONTAL);
        file_ = new wxTextCtrl(this, wxID_ANY);
        file_row->Add(file_, 1, wxRIGHT, 8);
        auto* browse = new wxButton(this, wxID_ANY, utf8("Обзор…"));
        file_row->Add(browse, 0);
        layout->Add(file_row, 0, wxEXPAND | wxLEFT | wxRIGHT | wxTOP, 16);
        browse->Bind(wxEVT_BUTTON, [this](wxCommandEvent&) {
            wxFileDialog picker(this, utf8("Выберите установочный файл"), {}, {},
                utf8("Установочные файлы|*.exe;*.EXE;*.msi;*.MSI;*.bat;*.BAT;*.cmd;*.CMD;*.ps1;*.PS1;*.reg;*.REG;"
                     "*.sh;*.SH;*.bash;*.BASH;*.deb;*.DEB;*.appimage;*.AppImage;*.APPIMAGE|Все файлы|*"),
                wxFD_OPEN | wxFD_FILE_MUST_EXIST);
            if (picker.ShowModal() == wxID_OK) {
                file_->SetValue(picker.GetPath());
            }
        });
        layout->Add(new wxStaticText(this, wxID_ANY, utf8("Категория — имя подпапки (необязательно)")),
                    0, wxLEFT | wxRIGHT | wxTOP, 16);
        category_ = new wxTextCtrl(this, wxID_ANY);
        layout->Add(category_, 0, wxEXPAND | wxLEFT | wxRIGHT | wxTOP, 16);
        layout->Add(new wxStaticText(this, wxID_ANY,
            utf8("Без категории файл попадёт в раздел «ПРОЧЕЕ».\n\n"
                 "Файл будет скопирован в software рядом с программой.\n"
                 "Исходный файл останется на месте. Установка не запускается.")),
            0, wxALL, 16);
        auto* buttons = new wxStdDialogButtonSizer();
        auto* add = new wxButton(this, wxID_OK, utf8("Добавить"));
        add->SetDefault();
        add->Enable(false);
        buttons->AddButton(add);
        buttons->AddButton(new wxButton(this, wxID_CANCEL, utf8("Отмена")));
        buttons->Realize();
        layout->Add(buttons, 0, wxEXPAND | wxLEFT | wxRIGHT | wxBOTTOM, 16);
        file_->Bind(wxEVT_TEXT, [this, add](wxCommandEvent&) { add->Enable(!file_->GetValue().IsEmpty()); });
        SetSizerAndFit(layout);
        SetMinSize(wxSize(660, GetSize().y));
        SetSize(GetMinSize());
        CentreOnParent();
        file_->SetFocus();
    }
    std::filesystem::path source() const { return file_path(file_->GetValue()); }
    std::string category() const {
        return category_->GetValue().Trim(true).Trim(false).ToStdString(wxConvUTF8);
    }
private:
    wxTextCtrl* file_ = nullptr;
    wxTextCtrl* category_ = nullptr;
};

class EditProgramDialog final : public wxDialog {
public:
    EditProgramDialog(wxWindow* parent, const minstall::Program& program)
        : wxDialog(parent, wxID_ANY, utf8("Изменить сведения о программе"), wxDefaultPosition, wxDefaultSize,
                   wxDEFAULT_DIALOG_STYLE | wxRESIZE_BORDER) {
        auto* layout = new wxBoxSizer(wxVERTICAL);
        auto* source = new wxStaticText(this, wxID_ANY,
            utf8("Файл: " + minstall::display_text(program.text("source_path"))));
        source->Wrap(600);
        layout->Add(source, 0, wxEXPAND | wxALL, 16);
        const auto field = [this, layout](const char* label, const std::string& value, bool multiline) {
            layout->Add(new wxStaticText(this, wxID_ANY, utf8(label)), 0, wxLEFT | wxRIGHT | wxTOP, 16);
            auto* input = new wxTextCtrl(this, wxID_ANY, utf8(value), wxDefaultPosition,
                multiline ? wxSize(-1, 140) : wxDefaultSize, multiline ? wxTE_MULTILINE : 0);
            layout->Add(input, multiline ? 1 : 0, wxEXPAND | wxLEFT | wxRIGHT | wxTOP, 16);
            return input;
        };
        name_ = field("Название", program.name(), false);
        version_ = field("Версия (необязательно)", program.text("version"), false);
        description_ = field("Описание", program.text("desc"), true);
        layout->Add(new wxStaticText(this, wxID_ANY,
            utf8("Сведения сохранятся после перезапуска. Сам файл не изменится.")), 0, wxALL, 16);
        auto* buttons = new wxStdDialogButtonSizer();
        auto* save = new wxButton(this, wxID_OK, utf8("Сохранить"));
        save->SetDefault();
        buttons->AddButton(save);
        buttons->AddButton(new wxButton(this, wxID_CANCEL, utf8("Отмена")));
        buttons->Realize();
        layout->Add(buttons, 0, wxEXPAND | wxLEFT | wxRIGHT | wxBOTTOM, 16);
        const auto update_save = [this, save] {
            save->Enable(!name_->GetValue().Trim(true).Trim(false).IsEmpty());
        };
        name_->Bind(wxEVT_TEXT, [update_save](wxCommandEvent&) { update_save(); });
        update_save();
        SetSizerAndFit(layout);
        SetMinSize(wxSize(660, GetSize().y));
        SetSize(GetMinSize());
        CentreOnParent();
        name_->SetFocus();
        name_->SelectAll();
    }
    minstall::ProgramDetails details() const {
        return {name_->GetValue().Trim(true).Trim(false).ToStdString(wxConvUTF8),
                version_->GetValue().Trim(true).Trim(false).ToStdString(wxConvUTF8),
                description_->GetValue().ToStdString(wxConvUTF8)};
    }
private:
    wxTextCtrl* name_ = nullptr;
    wxTextCtrl* version_ = nullptr;
    wxTextCtrl* description_ = nullptr;
};

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
    explicit CatalogFrame(const minstall::Options& options)
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
        tree_->Bind(wxEVT_TREE_SEL_CHANGED, &CatalogFrame::on_selection_changed, this);
        tree_->Bind(wxEVT_TREE_ITEM_ACTIVATED, &CatalogFrame::on_item_activated, this);
        CreateStatusBar();
        create_menu();
        if (options_.explicit_catalog) {
            show_catalog(minstall::load_programs(options_));
        } else {
            show_catalog({});
            refresh_catalog();
        }
        Bind(wxEVT_TIMER, [this](wxTimerEvent&) { refresh_catalog(); }, refresh_timer_.GetId());
        set_watcher_interval(3000);
        SetMinSize(wxSize(640, 400));
        Centre();
    }

    ~CatalogFrame() override {
        refresh_timer_.Stop();
        // wxMSW deletes selected tree items while destroying its children. The
        // frame's menu/status bars are already gone at that point, so detach
        // child callbacks while this frame and its controls are still alive.
        tree_->Unbind(wxEVT_TREE_SEL_CHANGED, &CatalogFrame::on_selection_changed, this);
        tree_->Unbind(wxEVT_TREE_ITEM_ACTIVATED, &CatalogFrame::on_item_activated, this);
    }

    bool smoke_check() {
        const auto* menus = GetMenuBar();
        const bool scanning = !options_.explicit_catalog;
        if (scan_failed_ || !menus || menus->GetMenuCount() != 4 ||
            menus->GetMenuLabelText(0) != utf8("Файл") ||
            menus->GetMenuLabelText(1) != utf8("Настройки") ||
            menus->GetMenuLabelText(2) != utf8("Профили") ||
            menus->GetMenuLabelText(3) != utf8("Справка") ||
            menus->IsEnabled(id_add_program) != scanning || !menus->IsEnabled(id_save_catalog) ||
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
            if (!data || details_->GetValue() != utf8(program_details(data->program)) ||
                menus->IsEnabled(id_edit_program) != scanning) {
                return false;
            }
            tree_->SelectItem(tree_->GetItemParent(first_program_));
            if (menus->IsEnabled(id_edit_program) ||
                details_->GetValue() != utf8("Выберите программу в категории.")) {
                return false;
            }
            // Leave a populated tree selected when closing: wxMSW emits
            // selection notifications during teardown of this exact case.
            tree_->SelectItem(first_program_);
            return details_->GetValue() == utf8(program_details(data->program)) &&
                   menus->IsEnabled(id_edit_program) == scanning;
        }
        return details_->GetValue() == utf8(empty_message()) && !menus->IsEnabled(id_edit_program);
    }

private:
    void on_selection_changed(wxTreeEvent& event) {
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
        update_edit_menu();
    }

    void on_item_activated(wxTreeEvent& event) {
        if (event.GetItem().IsOk() && dynamic_cast<ProgramData*>(tree_->GetItemData(event.GetItem()))) {
            tree_->SelectItem(event.GetItem());
            edit_program();
        } else {
            event.Skip();
        }
    }

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
        file->Append(id_add_program, utf8("Добавить программу…\tCtrl+N"),
                     utf8("Скопировать установочный файл в software; исходник останется на месте."))
            ->Enable(!options_.explicit_catalog);
        Bind(wxEVT_MENU, [this](wxCommandEvent&) { add_program(); }, id_add_program);
        file->Append(id_edit_program, utf8("Изменить сведения…\tCtrl+E"),
                     utf8("Изменить название, версию и описание выбранной программы."))->Enable(false);
        Bind(wxEVT_MENU, [this](wxCommandEvent&) { edit_program(); }, id_edit_program);
        file->Append(id_save_catalog, utf8("Сохранить каталог…\tCtrl+S"),
                     utf8("Сохранить список программ в выбранный JSON-файл."));
        Bind(wxEVT_MENU, [this](wxCommandEvent&) { save_programs(); }, id_save_catalog);
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
            wxMessageBox(utf8("Выберите «Файл → Добавить программу» (Ctrl+N), укажите установочный файл\n"
                "и при необходимости категорию. Копия появится в software; исходник сохранится.\n"
                "Также можно вручную положить файлы в software рядом с программой.\n"
                "Для ручной проверки нажмите F5.\n\n"
                "Выберите программу и нажмите Ctrl+E или дважды щёлкните по ней,\n"
                "чтобы изменить название, версию и описание. Сведения сохраняются после перезапуска.\n\n"
                "«Файл → Сохранить каталог» (Ctrl+S) сохраняет список в JSON, без копирования установщиков.\n\n"
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

    bool refresh_catalog() {
        if (options_.explicit_catalog) {
            return true;
        }
        try {
            auto refreshed = minstall::load_programs(options_);
            if (scan_failed_ || !same_catalog(catalog_, refreshed)) {
                show_catalog(std::move(refreshed));
            }
            scan_failed_ = false;
            return true;
        } catch (const minstall::CatalogError& error) {
            show_catalog({});
            scan_failed_ = true;
            details_->ChangeValue(utf8(std::string("Не удалось обновить список.\n\n") + error.what() +
                "\n\nИсправьте указанную ошибку и нажмите F5 для повторной проверки."));
            SetStatusText(utf8("Ошибка чтения списка программ"));
            return false;
        }
    }

    void update_edit_menu() {
        auto* menus = GetMenuBar();
        if (!menus) {
            return;
        }
        const auto selected = tree_->GetSelection();
        menus->Enable(id_edit_program, !options_.explicit_catalog && selected.IsOk() &&
                      dynamic_cast<ProgramData*>(tree_->GetItemData(selected)) != nullptr);
    }

    void edit_program() {
        if (options_.explicit_catalog || !refresh_catalog()) {
            return;
        }
        const auto selected = tree_->GetSelection();
        const auto* data = selected.IsOk() ? dynamic_cast<ProgramData*>(tree_->GetItemData(selected)) : nullptr;
        if (!data) {
            return;
        }
        PauseWatcher pause(refresh_timer_);
        const auto program = data->program;
        try {
            const auto snapshot = minstall::load_metadata(options_.software);
            minstall::Catalog editor_catalog;
            editor_catalog.categories.push_back({"", {program}});
            minstall::apply_metadata(editor_catalog, snapshot);
            EditProgramDialog dialog(this, editor_catalog.categories.front().programs.front());
            while (dialog.ShowModal() == wxID_OK) {
                try {
                    minstall::save_program_details(options_.software, program.text("source_path"),
                                                  dialog.details(), snapshot);
                    if (refresh_catalog()) {
                        SetStatusText(utf8("Сведения сохранены."));
                    }
                    return;
                } catch (const minstall::CatalogError& error) {
                    wxMessageBox(utf8(error.what()), utf8("Не удалось сохранить сведения"), wxOK | wxICON_ERROR, this);
                }
            }
        } catch (const minstall::CatalogError& error) {
            wxMessageBox(utf8(error.what()), utf8("Не удалось открыть редактор"), wxOK | wxICON_ERROR, this);
        }
    }

    void add_program() {
        if (options_.explicit_catalog) {
            return;
        }
        PauseWatcher pause(refresh_timer_);
        AddProgramDialog dialog(this);
        while (dialog.ShowModal() == wxID_OK) {
            try {
                minstall::ImportResult result;
                {
                    wxProgressDialog progress(utf8("Добавление программы"), utf8("Копирование установочного файла…"),
                        100, this, wxPD_CAN_ABORT | wxPD_APP_MODAL | wxPD_AUTO_HIDE | wxPD_ELAPSED_TIME);
                    result = minstall::import_installer(dialog.source(), options_.software, dialog.category(),
                        [&progress](std::uintmax_t copied, std::uintmax_t total) {
                            const auto fraction = total == 0 ? 0.0L :
                                100.0L * static_cast<long double>(copied) / static_cast<long double>(total);
                            return progress.Update(static_cast<int>(std::min(99.0L, fraction)));
                        });
                    progress.Update(100);
                }
                if (refresh_catalog()) {
                    const auto relative = result.path.lexically_relative(options_.software).generic_u8string();
                    wxTreeItemIdValue category_cookie;
                    for (auto category = tree_->GetFirstChild(tree_->GetRootItem(), category_cookie); category.IsOk();
                         category = tree_->GetNextChild(tree_->GetRootItem(), category_cookie)) {
                        wxTreeItemIdValue program_cookie;
                        for (auto item = tree_->GetFirstChild(category, program_cookie); item.IsOk();
                             item = tree_->GetNextChild(category, program_cookie)) {
                            const auto* data = static_cast<ProgramData*>(tree_->GetItemData(item));
                            if (data->program.text("source_path") == relative) {
                                tree_->SelectItem(item);
                                tree_->EnsureVisible(item);
                            }
                        }
                    }
                    SetStatusText(utf8(result.copied ? "Программа добавлена в список." : "Этот файл уже есть в списке."));
                } else {
                    wxMessageBox(utf8("Файл добавлен, но список не удалось обновить. Проверьте папку software и нажмите F5."),
                                 utf8("Добавление программы"), wxOK | wxICON_WARNING, this);
                }
                return;
            } catch (const minstall::ImportCancelled&) {
                SetStatusText(utf8("Добавление отменено. Исходный файл сохранён."));
                return;
            } catch (const minstall::CatalogError& error) {
                wxMessageBox(utf8(error.what()), utf8("Не удалось добавить программу"), wxOK | wxICON_ERROR, this);
            }
        }
    }

    void save_programs() {
        PauseWatcher pause(refresh_timer_);
        wxFileDialog dialog(this, utf8("Сохранить каталог"), utf8(options_.catalog.parent_path().u8string()),
                            "programs-export.json", "JSON (*.json)|*.json", wxFD_SAVE);
        if (dialog.ShowModal() != wxID_OK) {
            return;
        }
        try {
            // Refresh after choosing the path: never export a stale default list.
            // An explicit catalog remains a snapshot of the selected document.
            if (!options_.explicit_catalog && !refresh_catalog()) {
                return;
            }
            const auto path = file_path(dialog.GetPath());
            const auto metadata = minstall::metadata_path(options_.software);
            if (std::filesystem::absolute(path).lexically_normal() == std::filesystem::absolute(metadata).lexically_normal() ||
                (std::filesystem::exists(path) && std::filesystem::exists(metadata) &&
                 std::filesystem::equivalent(path, metadata))) {
                throw minstall::CatalogError("Этот файл хранит сведения о программах. Выберите другое имя для экспорта.");
            }
            const bool replace = std::filesystem::exists(std::filesystem::symlink_status(path));
            if (replace && wxMessageBox(utf8("Заменить существующий файл?\n\n" +
                    minstall::display_text(path.u8string())), utf8("Сохранение каталога"),
                    wxYES_NO | wxNO_DEFAULT | wxICON_QUESTION, this) != wxYES) {
                return;
            }
            minstall::save_catalog(catalog_, path, replace);
            SetStatusText(utf8("Каталог сохранён: " + minstall::display_text(path.u8string())));
        } catch (const minstall::CatalogError& error) {
            wxMessageBox(utf8(error.what()), utf8("Не удалось сохранить каталог"), wxOK | wxICON_ERROR, this);
        } catch (const std::filesystem::filesystem_error&) {
            wxMessageBox(utf8("Не удалось проверить выбранный путь. Проверьте доступ к папке."),
                         utf8("Не удалось сохранить каталог"), wxOK | wxICON_ERROR, this);
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
        update_edit_menu();
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
            auto* frame = new CatalogFrame(gui_options);
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

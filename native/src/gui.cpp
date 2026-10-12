#include "minstall/app.hpp"

#include <iostream>
#include <utility>
#include <wx/wx.h>
#include <wx/splitter.h>
#include <wx/treectrl.h>

namespace minstall_ui {
minstall::Options gui_options;
int gui_result = 0;

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
        show_catalog(std::move(catalog));
        if (!options_.explicit_catalog) {
            Bind(wxEVT_TIMER, [this](wxTimerEvent&) {
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
                        "Повторная проверка выполняется автоматически."));
                    SetStatusText(utf8("Ошибка чтения папки software"));
                }
            }, refresh_timer_.GetId());
            refresh_timer_.Start(3000);
        }
        SetMinSize(wxSize(640, 400));
        Centre();
    }

    bool smoke_check() {
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
    std::string empty_message() const {
        return options_.explicit_catalog ? "В выбранном каталоге пока нет программ." :
            "Программ пока нет.\n\n"
            "Добавьте установочные файлы в папку software рядом с программой.\n"
            "Список появится автоматически. Подпапки станут категориями.";
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

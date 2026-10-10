#include "minstall/app.hpp"

#include <iostream>
#include <utility>
#include <wx/wx.h>
#include <wx/splitter.h>
#include <wx/treectrl.h>

namespace {
minstall::Options gui_options;
int gui_result = 0;

wxString utf8(const std::string& value) {
    return wxString::FromUTF8(value.data(), value.size());
}

class ProgramData final : public wxTreeItemData {
public:
    explicit ProgramData(const minstall::Program* value) : program(value) {}
    const minstall::Program* program;
};

class CatalogFrame final : public wxFrame {
public:
    explicit CatalogFrame(minstall::Catalog catalog)
        : wxFrame(nullptr, wxID_ANY, utf8(minstall::prototype_version), wxDefaultPosition, wxSize(960, 620)),
          catalog_(std::move(catalog)) {
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
        const auto root = tree_->AddRoot("Catalog");
        for (const auto& category : catalog_.categories) {
            const auto category_item = tree_->AppendItem(root, utf8(minstall::display_text(category.name)));
            for (const auto& program : category.programs) {
                const auto item = tree_->AppendItem(category_item, utf8(minstall::display_text(program.name())),
                                                   -1, -1, new ProgramData(&program));
                if (!first_program_.IsOk()) {
                    first_program_ = item;
                }
            }
            tree_->Expand(category_item);
        }
        tree_->Bind(wxEVT_TREE_SEL_CHANGED, [this](wxTreeEvent& event) {
            const auto* data = dynamic_cast<ProgramData*>(tree_->GetItemData(event.GetItem()));
            if (data) {
                // Plain text only. Never load icon paths, follow URLs or execute commands.
                details_->ChangeValue(utf8(data->program->fields.dump(2, ' ', false)));
            } else {
                details_->ChangeValue(utf8("Выберите программу в категории."));
            }
        });
        CreateStatusBar();
        SetStatusText(utf8("Категорий: " + std::to_string(catalog_.categories.size()) +
                          " · Программ: " + std::to_string(catalog_.program_count())));
        SetMinSize(wxSize(640, 400));
        Centre();
    }

    bool smoke_check() {
        const auto expected = 1 + catalog_.categories.size() + catalog_.program_count();
        if (tree_->GetCount() != expected) {
            return false;
        }
        if (first_program_.IsOk()) {
            tree_->SelectItem(first_program_);
            const auto* data = static_cast<ProgramData*>(tree_->GetItemData(first_program_));
            return details_->GetValue() == utf8(data->program->fields.dump(2, ' ', false));
        }
        return true;
    }

private:
    minstall::Catalog catalog_;
    wxTreeCtrl* tree_ = nullptr;
    wxTextCtrl* details_ = nullptr;
    wxTreeItemId first_program_;
};

class CatalogApp final : public wxApp {
public:
    bool OnInit() override {
        try {
            auto* frame = new CatalogFrame(minstall::load_catalog(gui_options.catalog));
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
}  // namespace

wxIMPLEMENT_APP_NO_MAIN(CatalogApp);

int run_gui(const minstall::Options& options) {
    gui_options = options;
    gui_result = 0;
    int argc = 1;
    char name[] = "minstall-native";
    char* argv[] = {name, nullptr};
    const int result = wxEntry(argc, argv);
    return result == 0 ? gui_result : 3;
}

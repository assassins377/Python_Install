import contextlib
import json

import wx

import i18n

_ = i18n.t

class ProgramEditorDialog(wx.Dialog):
    def __init__(self, parent, program_data=None):
        super().__init__(parent, title=_("program_editor.title"), size=(600, 700))

        self.program_data = program_data if program_data is not None else {}

        self.panel = wx.Panel(self)
        self.main_sizer = wx.BoxSizer(wx.VERTICAL)

        self._create_fields()
        self._populate_fields()
        self._create_buttons()

        self.panel.SetSizer(self.main_sizer)
        self.main_sizer.Fit(self)

    def _create_fields(self):
        form_sizer = wx.FlexGridSizer(cols=2, hgap=10, vgap=10)
        form_sizer.AddGrowableCol(1)

        form_sizer.Add(wx.StaticText(self.panel, label=_("program_editor.name")), 0, wx.ALIGN_CENTER_VERTICAL)
        self.name_ctrl = wx.TextCtrl(self.panel, value="")
        form_sizer.Add(self.name_ctrl, 1, wx.EXPAND)

        form_sizer.Add(wx.StaticText(self.panel, label=_("program_editor.cmd")), 0, wx.ALIGN_CENTER_VERTICAL)
        self.cmd_ctrl = wx.TextCtrl(self.panel, value="")
        form_sizer.Add(self.cmd_ctrl, 1, wx.EXPAND)

        form_sizer.Add(wx.StaticText(self.panel, label=_("program_editor.url")), 0, wx.ALIGN_CENTER_VERTICAL)
        self.url_ctrl = wx.TextCtrl(self.panel, value="")
        form_sizer.Add(self.url_ctrl, 1, wx.EXPAND)

        form_sizer.Add(wx.StaticText(self.panel, label=_("program_editor.sha256")), 0, wx.ALIGN_CENTER_VERTICAL)
        self.sha256_ctrl = wx.TextCtrl(self.panel, value="")
        form_sizer.Add(self.sha256_ctrl, 1, wx.EXPAND)

        form_sizer.Add(wx.StaticText(self.panel, label=_("program_editor.desc")), 0, wx.ALIGN_CENTER_VERTICAL)
        self.desc_ctrl = wx.TextCtrl(self.panel, value="", style=wx.TE_MULTILINE)
        form_sizer.Add(self.desc_ctrl, 1, wx.EXPAND)

        form_sizer.Add(wx.StaticText(self.panel, label=_("program_editor.icon")), 0, wx.ALIGN_CENTER_VERTICAL)
        self.icon_ctrl = wx.TextCtrl(self.panel, value="")
        form_sizer.Add(self.icon_ctrl, 1, wx.EXPAND)

        form_sizer.Add(wx.StaticText(self.panel, label=_("program_editor.category")), 0, wx.ALIGN_CENTER_VERTICAL)
        self.category_ctrl = wx.TextCtrl(self.panel, value="")
        form_sizer.Add(self.category_ctrl, 1, wx.EXPAND)

        form_sizer.Add(wx.StaticText(self.panel, label=_("program_editor.depends_on")), 0, wx.ALIGN_CENTER_VERTICAL)
        self.depends_on_ctrl = wx.TextCtrl(self.panel, value="")
        form_sizer.Add(self.depends_on_ctrl, 1, wx.EXPAND)

        form_sizer.Add(wx.StaticText(self.panel, label=_("program_editor.retry")), 0, wx.ALIGN_CENTER_VERTICAL)
        self.retry_ctrl = wx.TextCtrl(self.panel, value="")
        form_sizer.Add(self.retry_ctrl, 1, wx.EXPAND)

        form_sizer.Add(wx.StaticText(self.panel, label=_("program_editor.timeout")), 0, wx.ALIGN_CENTER_VERTICAL)
        self.timeout_ctrl = wx.TextCtrl(self.panel, value="")
        form_sizer.Add(self.timeout_ctrl, 1, wx.EXPAND)

        form_sizer.Add(wx.StaticText(self.panel, label=_("program_editor.os")), 0, wx.ALIGN_CENTER_VERTICAL)
        self.os_ctrl = wx.TextCtrl(self.panel, value="")
        form_sizer.Add(self.os_ctrl, 1, wx.EXPAND)

        form_sizer.Add(wx.StaticText(self.panel, label=_("program_editor.pre_cmd")), 0, wx.ALIGN_CENTER_VERTICAL)
        self.pre_cmd_ctrl = wx.TextCtrl(self.panel, value="")
        form_sizer.Add(self.pre_cmd_ctrl, 1, wx.EXPAND)

        form_sizer.Add(wx.StaticText(self.panel, label=_("program_editor.post_cmd")), 0, wx.ALIGN_CENTER_VERTICAL)
        self.post_cmd_ctrl = wx.TextCtrl(self.panel, value="")
        form_sizer.Add(self.post_cmd_ctrl, 1, wx.EXPAND)

        form_sizer.Add(wx.StaticText(self.panel, label=_("program_editor.uninstall_cmd")), 0, wx.ALIGN_CENTER_VERTICAL)
        self.uninstall_cmd_ctrl = wx.TextCtrl(self.panel, value="")
        form_sizer.Add(self.uninstall_cmd_ctrl, 1, wx.EXPAND)

        form_sizer.Add(wx.StaticText(self.panel, label=_("program_editor.version_pattern")), 0, wx.ALIGN_CENTER_VERTICAL)
        self.version_pattern_ctrl = wx.TextCtrl(self.panel, value="")
        form_sizer.Add(self.version_pattern_ctrl, 1, wx.EXPAND)

        form_sizer.Add(wx.StaticText(self.panel, label=_("program_editor.detect")), 0, wx.ALIGN_CENTER_VERTICAL)
        self.detect_ctrl = wx.TextCtrl(self.panel, value="", style=wx.TE_MULTILINE)
        form_sizer.Add(self.detect_ctrl, 1, wx.EXPAND)

        self.main_sizer.Add(form_sizer, 1, wx.EXPAND | wx.ALL, 10)

    def _populate_fields(self):
        self.name_ctrl.SetValue(self.program_data.get("name", ""))
        self.cmd_ctrl.SetValue(self.program_data.get("cmd", ""))
        self.url_ctrl.SetValue(self.program_data.get("url", ""))
        self.sha256_ctrl.SetValue(self.program_data.get("sha256", ""))
        self.desc_ctrl.SetValue(self.program_data.get("desc", ""))
        self.icon_ctrl.SetValue(self.program_data.get("icon", ""))

        depends_on = ", ".join(self.program_data.get("depends_on", []))
        self.depends_on_ctrl.SetValue(depends_on)

        retry = self.program_data.get("retry")
        self.retry_ctrl.SetValue(str(retry) if retry is not None else "")

        timeout = self.program_data.get("timeout")
        self.timeout_ctrl.SetValue(str(timeout) if timeout is not None else "")

        self.os_ctrl.SetValue(self.program_data.get("os", ""))
        self.pre_cmd_ctrl.SetValue(self.program_data.get("pre_cmd", ""))
        self.post_cmd_ctrl.SetValue(self.program_data.get("post_cmd", ""))
        self.uninstall_cmd_ctrl.SetValue(self.program_data.get("uninstall_cmd", ""))
        self.version_pattern_ctrl.SetValue(self.program_data.get("version_pattern", ""))

        import json
        detect_json = json.dumps(self.program_data.get("detect", {}), indent=4, ensure_ascii=False)
        self.detect_ctrl.SetValue(detect_json)

    def _create_buttons(self):
        btn_sizer = wx.StdDialogButtonSizer()
        ok_btn = wx.Button(self.panel, wx.ID_OK)
        ok_btn.SetDefault()
        cancel_btn = wx.Button(self.panel, wx.ID_CANCEL)
        btn_sizer.AddButton(ok_btn)
        btn_sizer.AddButton(cancel_btn)
        btn_sizer.Realize()
        self.main_sizer.Add(btn_sizer, 0, wx.EXPAND | wx.ALL, 10)

    def GetProgramData(self):
        import json
        program_data = {
            "name": self.name_ctrl.GetValue().strip(),
            "cmd": self.cmd_ctrl.GetValue().strip(),
        }

        if self.url_ctrl.GetValue().strip():
            program_data["url"] = self.url_ctrl.GetValue().strip()
        if self.sha256_ctrl.GetValue().strip():
            program_data["sha256"] = self.sha256_ctrl.GetValue().strip()
        if self.desc_ctrl.GetValue().strip():
            program_data["desc"] = self.desc_ctrl.GetValue().strip()
        if self.icon_ctrl.GetValue().strip():
            program_data["icon"] = self.icon_ctrl.GetValue().strip()

        depends_on_str = self.depends_on_ctrl.GetValue().strip()
        if depends_on_str:
            program_data["depends_on"] = [d.strip() for d in depends_on_str.split(",") if d.strip()]

        retry_str = self.retry_ctrl.GetValue().strip()
        if retry_str:
            with contextlib.suppress(ValueError):
                program_data["retry"] = int(retry_str)

        timeout_str = self.timeout_ctrl.GetValue().strip()
        if timeout_str:
            with contextlib.suppress(ValueError):
                program_data["timeout"] = int(timeout_str)

        if self.os_ctrl.GetValue().strip():
            program_data["os"] = self.os_ctrl.GetValue().strip()
        if self.pre_cmd_ctrl.GetValue().strip():
            program_data["pre_cmd"] = self.pre_cmd_ctrl.GetValue().strip()
        if self.post_cmd_ctrl.GetValue().strip():
            program_data["post_cmd"] = self.post_cmd_ctrl.GetValue().strip()
        if self.uninstall_cmd_ctrl.GetValue().strip():
            program_data["uninstall_cmd"] = self.uninstall_cmd_ctrl.GetValue().strip()
        if self.version_pattern_ctrl.GetValue().strip():
            program_data["version_pattern"] = self.version_pattern_ctrl.GetValue().strip()

        detect_str = self.detect_ctrl.GetValue().strip()
        if detect_str:
            with contextlib.suppress(json.JSONDecodeError):
                program_data["detect"] = json.loads(detect_str)

        return program_data

    def GetCategory(self):
        return self.category_ctrl.GetValue().strip()

if __name__ == '__main__':
    app = wx.App(False)
    i18n.init("ru") # Initialize i18n for testing
    dlg = ProgramEditorDialog(None, program_data={"name": "Test App", "cmd": "test.exe", "detect": {"path": "/foo"}})
    dlg.ShowModal()
    dlg.Destroy()

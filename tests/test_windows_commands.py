"""Windows quoting regressions, also exercised on the Linux test runner."""
import subprocess

import pytest

import utils


@pytest.mark.parametrize("args", [
    [r"C:\Program Files\Demo\setup.exe", "/S"],
    [r"\\server\share name\setup.msi", "INSTALLDIR=C:\\Program Files\\Demo\\"],
    [r"software\setup.exe", "", 'quoted "value"', "a\\\\", "tab\tvalue"],
    ["setup.exe", "日本語", "single'quote", "back\\\"slash"],
])
def test_windows_arguments_round_trip(args):
    assert utils.split_windows_command(subprocess.list2cmdline(args)) == args


def test_windows_embedded_quotes_and_paths():
    assert utils.split_windows_command('"C:\\Program Files\\demo.exe" /D="C:\\Target Folder"') == [
        r"C:\Program Files\demo.exe", r"/D=C:\Target Folder",
    ]


def test_quoted_installer_path_is_accepted_and_dispatches_msi():
    command = '"software/Installer Name.msi" PROPERTY="a b"'
    assert utils.validate_cmd(command) is None
    args, path = utils.build_cmd(command)
    assert args[:2] == ["msiexec", "/i"]
    assert path.endswith("Installer Name.msi")
    assert "PROPERTY=a b" in args
    assert '"' not in path


@pytest.mark.parametrize("command", ['"broken.exe', 'setup.exe "broken'])
def test_unclosed_quotes_rejected(command):
    with pytest.raises(ValueError):
        utils.split_windows_command(command)
    assert utils.validate_cmd(command) is not None


@pytest.mark.parametrize("command", ['"setup.exe" & other.exe', '"setup.exe" | other.exe'])
def test_quoting_does_not_bypass_shell_character_checks(command):
    assert utils.validate_cmd(command) is not None

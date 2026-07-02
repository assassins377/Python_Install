# MInstAll CLI completion for PowerShell
# Usage: . .\completion.ps1
# Or add to $PROFILE

Register-ArgumentCompleter -Native -CommandName MInstAll_x86.exe, MInstAll_x64.exe, python -ScriptBlock {
    param($wordToComplete, $commandAst, $cursorPosition)

    $opts = @(
        '--version', '--install', '--install-profile', '--missing-only',
        '--parallel', '--max-jobs', '--dry-run', '--export-profile',
        '--update', '--uninstall', '--list', '--list-installed',
        '--list-profiles', '--filter-status', '--check-program-updates',
        '--export-installed', '--silent', '--no-color', '--no-gui',
        '--no-elevate', '--force-elevate', '--json', '--yes',
        '--verbose', '--debug', '--watchdog-interval',
        '--watchdog-threshold-count', '--watchdog-cpu-threshold', '--help'
    )

    $prev = ($commandAst.CommandElements | Select-Object -Last 2)[0].Value

    switch ($prev) {
        '--filter-status' {
            'ok', 'outdated', 'missing', 'runnable' | Where-Object { $_ -like "$wordToComplete*" }
        }
        '--install-profile' {
            if (Test-Path profiles) {
                Get-ChildItem profiles/*.json | ForEach-Object { $_.BaseName } | Where-Object { $_ -like "$wordToComplete*" }
            }
        }
        default {
            $opts | Where-Object { $_ -like "$wordToComplete*" }
        }
    }
}

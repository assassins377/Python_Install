# MInstAll CLI completion for bash/zsh
# Usage: source completion.sh
# Or copy to /etc/bash_completion.d/ or ~/.zsh/completions/

_minstall_completion() {
    local cur prev opts
    COMPREPLY=()
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"

    opts="--version --install --install-profile --missing-only --parallel --max-jobs --dry-run --export-profile --update --uninstall --list --list-installed --list-profiles --filter-status --check-program-updates --export-installed --silent --no-color --no-gui --no-elevate --force-elevate --json --yes --verbose --debug --watchdog-interval --watchdog-threshold-count --watchdog-cpu-threshold --help"

    case "${prev}" in
        --filter-status)
            COMPREPLY=( $(compgen -W "ok outdated missing runnable" -- "${cur}") )
            return 0
            ;;
        --install-profile)
            local profiles_dir="profiles"
            if [ -d "$profiles_dir" ]; then
                local profiles=$(ls "$profiles_dir"/*.json 2>/dev/null | sed 's|.*/||;s|\.json$||')
                COMPREPLY=( $(compgen -W "${profiles}" -- "${cur}") )
            fi
            return 0
            ;;
        --max-jobs|--watchdog-interval|--watchdog-threshold-count|--watchdog-cpu-threshold)
            COMPREPLY=()
            return 0
            ;;
        *)
            COMPREPLY=( $(compgen -W "${opts}" -- "${cur}") )
            return 0
            ;;
    esac
}

complete -F _minstall_completion MInstAll
complete -F _minstall_completion python main.py
complete -F _minstall_completion minstall

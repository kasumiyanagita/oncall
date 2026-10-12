# 簡易モード用のガード。bashrc から読み込まれる。
#
# 名前空間で隔離できない環境では、プレイヤーのシェルは本物のファイルシステムの上で動く。
# ここでは「作業用ホームの外を壊す」事故を止める。入力ミスへの安全ベルトであって、
# 変数展開やリダイレクトを使った意図的な回避までは防げない。

__oncall_root=$(realpath -- "$HOME")

__oncall_inside() {
    local p
    p=$(realpath -m -- "$1" 2>/dev/null) || return 1
    [[ $p == "$__oncall_root" || $p == "$__oncall_root"/* ]]
}

cd() {
    builtin cd "$@" || return
    if ! __oncall_inside "$PWD"; then
        builtin cd "$OLDPWD"
        echo "oncall: 簡易モードでは作業用ホーム(~)の外へ移動できません" >&2
        return 1
    fi
}
pushd() { echo "oncall: 簡易モードでは pushd は使えません。cd を使ってください" >&2; return 1; }
popd() { echo "oncall: 簡易モードでは popd は使えません。cd を使ってください" >&2; return 1; }

# kill は、このミッションのシェルから起動されたプロセスを数字の PID で指定したときだけ通す。
# 隔離されていないので、`kill -9 -1` のような指定は本来の環境のプロセスまで止めてしまう。
__oncall_kill_allowed() {
    local arg seen_signal=0 skip=0
    for arg in "$@"; do
        if (( skip )); then
            skip=0
            continue
        fi
        case $arg in
            -l|-L) return 0 ;;
            -s|-n) skip=1; seen_signal=1; continue ;;
            -*) if (( ! seen_signal )); then seen_signal=1; continue; fi ;;
        esac
        if [[ ! $arg =~ ^[0-9]+$ ]]; then
            echo "oncall: 簡易モードの kill は PID を数字で直接指定してください (例: kill 12345)" >&2
            return 1
        fi
        if ! grep -qzx "ONCALL_SESSION=$ONCALL_SESSION" "/proc/$arg/environ" 2>/dev/null; then
            echo "oncall: PID $arg はこのミッションのプロセスではないので止められません" >&2
            return 1
        fi
    done
    return 0
}

# DEBUG トラップから各コマンドの実行直前に呼ばれる。0 以外を返すとそのコマンドは実行されない。
__oncall_guard() {
    [[ -n ${COMP_LINE-} ]] && return 0

    local -a words
    read -ra words <<< "$BASH_COMMAND"

    case ${words[0]##*/} in
        kill)
            __oncall_kill_allowed "${words[@]:1}" || return 1
            ;;
        pkill|killall)
            echo "oncall: 簡易モードでは ${words[0]##*/} は使えません。ps で PID を調べて kill を使ってください" >&2
            return 1
            ;;
        sudo|su|doas|shutdown|reboot|halt|poweroff|mkfs|mkfs.*|mount|umount|chroot)
            echo "oncall: 簡易モードでは ${words[0]##*/} は使えません" >&2
            return 1
            ;;
    esac

    # ファイルを書き換えうるコマンドが(xargs や find -exec 経由も含めて)登場するか
    local w writes=0
    for w in "${words[@]}"; do
        case ${w##*/} in
            rm|rmdir|mv|cp|chmod|chown|chgrp|ln|truncate|shred|dd|tee|touch|mkdir|install|rsync|-delete)
                writes=1
                ;;
        esac
    done
    (( writes )) || return 0

    for w in "${words[@]}"; do
        w=${w#of=}
        w=${w#[\"\']}
        w=${w%[\"\']}
        case $w in
            '~') w=$HOME ;;
            '~/'*) w=$HOME/${w#'~/'} ;;
        esac
        case $w in
            /*|..|../*|*/..|*/../*)
                if ! __oncall_inside "$w"; then
                    echo "oncall: 簡易モードでは作業用ホーム(~)の外は変更できません: $w" >&2
                    return 1
                fi
                ;;
        esac
    done
    return 0
}

shopt -s extdebug
trap '__oncall_guard' DEBUG

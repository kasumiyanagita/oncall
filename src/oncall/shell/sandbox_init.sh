#!/bin/bash
# 隔離モードの初期化スクリプト。
# `unshare --user --map-root-user --mount --pid --fork ...` の中で実行され、
# 必要なものだけを載せた新しいルートを組み立てて pivot_root し、
# 最後に元の UID へ戻したうえで "$@" を実行する。
set -eu

root=$ONCALL_NEWROOT

mount -t tmpfs -o mode=755 tmpfs "$root"
mkdir -p "$root"/{usr,etc,proc,dev,tmp,var/tmp,run/oncall,old} "$root/home/$ONCALL_USER"
chmod 1777 "$root/tmp" "$root/var/tmp"

# /bin, /lib などは /usr へのシンボリックリンク(merged-usr)ならリンクごと再現する
for d in bin sbin lib lib32 lib64 libx32; do
    if [ -L "/$d" ]; then
        ln -s "$(readlink "/$d")" "$root/$d"
    elif [ -d "/$d" ]; then
        mkdir "$root/$d"
        mount --rbind "/$d" "$root/$d"
    fi
done

mount --rbind /usr "$root/usr"
mount --rbind /etc "$root/etc"
mount --rbind /dev "$root/dev"
mount -t proc proc "$root/proc"
mount --bind "$ONCALL_HOME_SRC" "$root/home/$ONCALL_USER"
mount --bind "$ONCALL_RUN_SRC" "$root/run/oncall"

hostname "$ONCALL_HOSTNAME" 2>/dev/null || true

cd "$root"
pivot_root . old
umount -l /old
rmdir /old
cd /

exec unshare --user --map-user="$ONCALL_UID" --map-group="$ONCALL_GID" -- "$@"

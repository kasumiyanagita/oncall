"""ミッション用のシェルの起動方法を決める。

- 隔離モード(isolated): `unshare` で名前空間を分け、/usr・/etc と作業用ホームだけが
  見える環境で本物の bash を動かす。本来のホームや /mnt は存在しない。
- 簡易モード(guard): 名前空間が使えない環境向け。使い捨てディレクトリを HOME にして
  bash を動かし、外側を壊しそうな操作を guard.bash で止める。事故防止であって完全な隔離ではない。
"""

from __future__ import annotations

import functools
import os
import pwd
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from oncall.procs import SESSION_VAR

SHELL_DIR = Path(__file__).parent / "shell"
INIT_SCRIPT = SHELL_DIR / "sandbox_init.sh"

ISOLATED = "isolated"
GUARD = "guard"
AUTO = "auto"

INNER_RUN = "/run/oncall"
INNER_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

_UNSHARE = [
    "unshare",
    "--user", "--map-root-user",
    "--mount",
    "--pid", "--fork", "--kill-child",
    "--uts", "--ipc", "--net",
    "--",
    "bash", str(INIT_SCRIPT),
]  # fmt: skip


@dataclass(frozen=True)
class Layout:
    """1 回のプレイで使う使い捨てディレクトリ。"""

    base: Path

    @property
    def home(self) -> Path:
        return self.base / "home"

    @property
    def run(self) -> Path:
        return self.base / "run"

    @property
    def newroot(self) -> Path:
        return self.base / "root"

    def create(self) -> None:
        for d in (self.home, self.run, self.newroot):
            d.mkdir(parents=True, exist_ok=True)

    def remove(self) -> None:
        """使い捨てディレクトリを消す。プレイヤーが権限を外したディレクトリがあっても消せるようにする。"""
        pending = [self.base]
        while pending:
            directory = pending.pop()
            try:
                os.chmod(directory, 0o700)
                pending += [Path(e.path) for e in os.scandir(directory) if e.is_dir(follow_symlinks=False)]
            except OSError:
                pass
        shutil.rmtree(self.base, ignore_errors=True)


def username() -> str:
    return pwd.getpwuid(os.getuid()).pw_name


def _init_env(layout: Layout, host: str) -> dict[str, str]:
    return {
        # 初期化のスクリプトは mount や pivot_root(/usr/sbin にある)を使う。
        # 起動した側の PATH に sbin が入っていなくても見つかるよう、標準の場所を先頭に足す
        "PATH": f"{INNER_PATH}:{os.environ.get('PATH', '')}".rstrip(":"),
        "ONCALL_NEWROOT": str(layout.newroot),
        "ONCALL_HOME_SRC": str(layout.home),
        "ONCALL_RUN_SRC": str(layout.run),
        "ONCALL_UID": str(os.getuid()),
        "ONCALL_GID": str(os.getgid()),
        "ONCALL_USER": username(),
        "ONCALL_HOSTNAME": host,
    }


@functools.cache
def isolation_available() -> bool:
    """実際に名前空間を作ってみて、隔離モードが使えるか確かめる。"""
    if shutil.which("unshare") is None:
        return False
    with tempfile.TemporaryDirectory(prefix="oncall-probe-") as tmp:
        layout = Layout(Path(tmp))
        layout.create()
        try:
            result = subprocess.run(
                [*_UNSHARE, "true"],
                env=_init_env(layout, "probe"),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return result.returncode == 0


def resolve_mode(requested: str) -> str:
    if requested == AUTO:
        return ISOLATED if isolation_available() else GUARD
    return requested


def shell_command(
    mode: str,
    layout: Layout,
    *,
    host: str,
    workdir: str,
    session: str,
    command: list[str] | None = None,
    extra: dict[str, str] | None = None,
) -> tuple[list[str], dict[str, str], Path]:
    """ミッション用のシェルの (argv, 環境変数, 起動ディレクトリ) を返す。

    プレイヤーのシェルには本来の環境変数を引き継がせず、ここで列挙したものだけを渡す。
    command を渡すと、対話シェルの代わりにそのコマンドを同じ環境で実行する。
    extra は、シェルに追加で渡す環境変数。
    """
    user = username()
    home = f"/home/{user}" if mode == ISOLATED else str(layout.home)
    run = INNER_RUN if mode == ISOLATED else str(layout.run)

    inner = {
        "HOME": home,
        "USER": user,
        "LOGNAME": user,
        "SHELL": "/bin/bash",
        "PATH": INNER_PATH,
        "TERM": os.environ.get("TERM", "xterm-256color"),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "ONCALL_RUN": run,
        "ONCALL_WORKDIR": f"{home}/{workdir}",
        "ONCALL_HOST": host,
        "ONCALL_MODE": mode,
        SESSION_VAR: session,
    }
    inner.update(extra or {})
    for name in ("LC_ALL", "COLORTERM"):
        if name in os.environ:
            inner[name] = os.environ[name]

    # 行の編集はアプリの入力欄で行うので、bash 自身の行編集(readline)は使わない
    bash = command or ["bash", "--noediting", "--rcfile", f"{run}/bashrc", "-i"]
    if mode == ISOLATED:
        assignments = [f"{k}={v}" for k, v in inner.items()]
        return [*_UNSHARE, "env", "-i", *assignments, *bash], _init_env(layout, host), layout.base
    return bash, inner, layout.home

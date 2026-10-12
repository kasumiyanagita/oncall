"""ミッション用シェルから起動されたプロセスを、ゲーム本体(シェルの外側)から調べる。

シェルには環境変数 ONCALL_SESSION でプレイごとの合言葉を渡してあり、そこから起動された
プロセスはそれを引き継ぐ。/proc/<pid>/environ を見れば、隔離モードでも簡易モードでも
「このプレイのプロセス」を見分けられる。
"""

from __future__ import annotations

import os
import signal
from dataclasses import dataclass
from pathlib import Path

SESSION_VAR = "ONCALL_SESSION"


@dataclass(frozen=True)
class Process:
    pid: int
    argv: tuple[str, ...]


def session_processes(session: str) -> list[Process]:
    marker = f"{SESSION_VAR}={session}".encode()
    found = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if marker not in (entry / "environ").read_bytes().split(b"\0"):
                continue
            cmdline = (entry / "cmdline").read_bytes()
        except OSError:  # 他人のプロセス、または調べている間に終了した
            continue
        argv = tuple(arg.decode(errors="replace") for arg in cmdline.split(b"\0") if arg)
        found.append(Process(int(entry.name), argv))
    return found


def kill_session(session: str) -> None:
    """このプレイのプロセスが残っていれば止める(簡易モードではシェル終了後も残りうる)。"""
    for process in session_processes(session):
        try:
            os.kill(process.pid, signal.SIGKILL)
        except OSError:
            pass

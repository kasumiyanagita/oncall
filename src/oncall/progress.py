"""進捗の保存と読み込み。

保存先は `$XDG_DATA_HOME/oncall/progress.json`(既定では ~/.local/share/oncall/)。
環境変数 ONCALL_DATA_DIR があればそのディレクトリを使う。
"""

from __future__ import annotations

import dataclasses
import json
import os
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from oncall.scoring import Score

SAVE_VERSION = 1


def data_dir() -> Path:
    if override := os.environ.get("ONCALL_DATA_DIR"):
        return Path(override)
    base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(base) / "oncall"


def default_path() -> Path:
    return data_dir() / "progress.json"


@dataclass
class MissionRecord:
    best: Score
    clears: int
    first_cleared: str


@dataclass(frozen=True)
class ClearOutcome:
    """クリアを記録した結果。表示用。"""

    new_commands: tuple[str, ...]
    previous_best: Score | None
    is_best: bool


def _is_better(new: Score, old: Score) -> bool:
    return (new.points, -new.elapsed) > (old.points, -old.elapsed)


def _score_from_json(data: dict) -> Score:
    return Score(**(data | {"penalties": tuple((label, points) for label, points in data["penalties"])}))


@dataclass
class Progress:
    path: Path
    missions: dict[str, MissionRecord] = field(default_factory=dict)
    # コマンド名 → 図鑑に登録された日
    unlocked: dict[str, str] = field(default_factory=dict)
    # コマンド名 → プレイヤーが実行した回数
    usage: dict[str, int] = field(default_factory=dict)
    # チュートリアルを見せたか(初めて起動したときに 1 回だけ自動で出すため)
    tutorial_seen: bool = False
    # 読み込めなかったセーブデータを退避した先(あれば起動時に知らせる)
    quarantined: Path | None = None

    @classmethod
    def load(cls, path: Path | None = None) -> Progress:
        path = path or default_path()
        if not path.exists():
            return cls(path)
        try:
            data = json.loads(path.read_text())
            if data["version"] != SAVE_VERSION:
                raise ValueError(f"unsupported save version: {data['version']}")
            return cls(
                path,
                missions={
                    mission_id: MissionRecord(
                        best=_score_from_json(record["best"]),
                        clears=record["clears"],
                        first_cleared=record["first_cleared"],
                    )
                    for mission_id, record in data["missions"].items()
                },
                unlocked=dict(data["unlocked"]),
                usage={name: int(count) for name, count in data["usage"].items()},
                tutorial_seen=bool(data.get("tutorial_seen", False)),
            )
        except (OSError, ValueError, KeyError, TypeError):
            # 壊れたデータを上書きして消さないよう、別名で残してから新しく始める
            quarantined = path.with_name(f"{path.name}.broken-{int(time.time())}")
            path.rename(quarantined)
            return cls(path, quarantined=quarantined)

    def save(self) -> None:
        data = {
            "version": SAVE_VERSION,
            "tutorial_seen": self.tutorial_seen,
            "missions": {
                mission_id: {
                    "best": dataclasses.asdict(record.best),
                    "clears": record.clears,
                    "first_cleared": record.first_cleared,
                }
                for mission_id, record in self.missions.items()
            },
            "unlocked": self.unlocked,
            "usage": self.usage,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 書き込み途中で落ちても元のファイルが壊れないよう、別ファイルに書いてから置き換える
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        os.replace(tmp, self.path)

    def mark_tutorial_seen(self) -> None:
        """チュートリアルを見せたことを記録して保存する。"""
        if not self.tutorial_seen:
            self.tutorial_seen = True
            self.save()

    def best(self, mission_id: str) -> Score | None:
        record = self.missions.get(mission_id)
        return record.best if record else None

    def record_clear(self, mission_id: str, commands: Iterable[str], score: Score) -> ClearOutcome:
        """クリアを記録して保存する。自己ベストと図鑑の登録を更新する。"""
        today = date.today().isoformat()
        record = self.missions.get(mission_id)
        previous = record.best if record else None
        is_best = previous is None or _is_better(score, previous)
        if record is None:
            self.missions[mission_id] = MissionRecord(best=score, clears=1, first_cleared=today)
        else:
            record.clears += 1
            if is_best:
                record.best = score

        new_commands = tuple(name for name in commands if name not in self.unlocked)
        for name in new_commands:
            self.unlocked[name] = today

        self.save()
        return ClearOutcome(new_commands=new_commands, previous_best=previous, is_best=is_best)

    def add_usage(self, counts: Mapping[str, int]) -> None:
        if not counts:
            return
        for name, count in counts.items():
            self.usage[name] = self.usage.get(name, 0) + count
        self.save()

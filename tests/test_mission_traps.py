"""各ミッションに仕込んだ「ひっかけ」: ありがちな雑な解き方では不正解になり、理由が伝わること。"""

import pytest

from conftest import MISSIONS, needs_isolation, play

SEEDS = range(3)


def _fails(mission_id, tmp_path, seed, script, *expected_words):
    played = play(mission_id, tmp_path, seed, script)
    assert not played.result.ok
    for word in expected_words:
        assert word in played.result.message, played.result.message
    return played


# ---- 満杯のディスクと開いた鍵 ----------------------------------------


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize(
    "script, word",
    [
        ("find . -type f -size +10M -delete", "消してはいけない"),  # 大きいが新しいファイルまで消す
        ("find . -type f -mtime +7 -delete", "消してはいけない"),  # 古いが小さいファイルまで消す
        ("find . -type f -size +10M -mtime +7 -delete; chmod -R 600 .", "ディレクトリの権限"),
        ("find . -type f -size +10M -mtime +7 -delete; find . -type f -exec chmod 600 {} +", "鍵以外の権限"),
        ("find . -type f -size +10M -mtime +7 -delete; chmod 600 certs/*.pem", "依頼 2"),  # 一部の鍵だけ
        ("find . -name '*.pem' -exec chmod 600 {} +", "依頼 1"),
    ],
)
def test_disk_and_keys_traps(tmp_path, seed, script, word):
    _fails("disk-and-keys", tmp_path, seed, script, word)


# ---- 置換ひとつで直るはずだった --------------------------------------

_HOST = r"-e 's/db-old\.internal/db-new.internal/g'"
_TIMEOUT = "-e 's/^timeout = [0-9]*$/timeout = 30/'"
_DEBUG = "-e '/^debug = true$/d'"


def _sed(*commands):
    return f"find conf -name '*.conf' -exec sed -i {' '.join(commands)} {{}} +"


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize(
    "script, word",
    [
        # g がなく、1 行に 2 回ある 2 つ目が残る
        (_sed(r"-e 's/db-old\.internal/db-new.internal/'", _TIMEOUT, _DEBUG), "残っています"),
        # . をエスケープせず、よく似た別ホストまで書き換える
        (_sed("-e 's/db-old.internal/db-new.internal/g'", _TIMEOUT, _DEBUG), "別のホスト"),
        # ^ がなく、connect_timeout なども書き換える
        (_sed(_HOST, "-e 's/timeout = [0-9]*/timeout = 30/'", _DEBUG), "connect_timeout"),
        # コメント行まで削除する
        (_sed(_HOST, _TIMEOUT, "-e '/debug = true/d'"), "コメント行"),
        # debug の行を消し忘れる
        (_sed(_HOST, _TIMEOUT), "debug = true"),
    ],
)
def test_config_fix_traps(tmp_path, seed, script, word):
    _fails("config-fix", tmp_path, seed, script, word)


def test_config_fix_top_level_only_misses_subdirectory(tmp_path):
    script = r"sed -i 's/db-old\.internal/db-new.internal/g; s/^timeout = .*/timeout = 30/; /^debug = true$/d' conf/*.conf"
    _fails("config-fix", tmp_path, 0, script, "サブディレクトリ")


def test_config_fix_can_be_restored_from_deployed_copy(tmp_path):
    broken = "sed -i 's/timeout = [0-9]*/timeout = 30/' conf/*.conf"
    restore = "cp -r conf.deployed/. conf/"
    played = play("config-fix", tmp_path, 0, f"{broken}\n{restore}\n{MISSIONS['config-fix'].solution}")
    assert played.result.ok, played.result.message


# ---- 壊れたファイルを隔離せよ ----------------------------------------


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize(
    "script, word",
    [
        # 拡張子で絞らず、ログや .csv.tmp まで移動する
        ("grep -rlZ 'checksum=INVALID' uploads | xargs -0 mv -t quarantine", "CSV ではない"),
        # 移動ではなくコピー
        ("grep -rlZ 'checksum=INVALID' --include='*.csv' uploads | xargs -0 cp -t quarantine", "コピーではなく移動"),
        # 壊れていない CSV まで移動する
        ("find uploads -name '*.csv' -print0 | xargs -0 mv -t quarantine", "壊れていない"),
    ],
)
def test_quarantine_traps(tmp_path, seed, script, word):
    _fails("quarantine", tmp_path, seed, script, word)


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("xargs", ["xargs mv -t quarantine", "xargs -I{} mv {} quarantine/"])
def test_quarantine_plain_xargs_drops_awkward_names(tmp_path, seed, xargs):
    """区切りを指定しない xargs は、空白や ' を含む名前を取りこぼす。"""
    played = _fails("quarantine", tmp_path, seed, f"grep -rl 'checksum=INVALID' --include='*.csv' uploads | {xargs}", "残っています")
    assert played.status != 0
    # 一部でも移動できていれば、取りこぼしの原因を知らせる(1 つも動かせなかったときは xargs 自身のエラーが出ている)
    if any((tmp_path / "home/data/quarantine").iterdir()):
        assert "空白" in played.result.message


# ---- 止まらないワーカー ----------------------------------------------


@needs_isolation
@pytest.mark.parametrize("seed", SEEDS)
def test_runaway_worker_ignores_sigterm(tmp_path, seed):
    script = "pkill -f 'queue-worker.sh --id {{stuck_id}}'; sleep 1.5"
    played = _fails("runaway-worker", tmp_path, seed, script, "まだ動いています", "ログの末尾")
    log = tmp_path / "home/services/logs" / f"worker-{played.ctx['stuck_id']}.log"
    assert "SIGTERM" in log.read_text()


@needs_isolation
@pytest.mark.parametrize(
    "script, word",
    [
        ("pkill -9 -f queue-worker.sh", "正常に動いていたワーカー"),
        ("pkill -9 -f 'queue-worker.sh --id {{stuck_id}}'; pkill -f api-server.sh", "api-server.sh"),
    ],
)
def test_runaway_worker_collateral_damage(tmp_path, script, word):
    _fails("runaway-worker", tmp_path, 0, script, word)


@needs_isolation
def test_runaway_worker_can_be_restarted_after_mistake(tmp_path):
    script = "pkill -9 -f queue-worker.sh; sleep 0.3\n"
    script += "for i in 1 2 3 4; do [ $i = {{stuck_id}} ] || setsid ./queue-worker.sh --id $i > /dev/null 2>&1 & done; sleep 1"
    played = play("runaway-worker", tmp_path, 0, script)
    assert played.result.ok, played.result.message


# ---- 消えた注文 ------------------------------------------------------

_PAID = "tail -n +2 payment.csv | cut -d, -f2 | sort -u > paid.txt"
_CHECKOUT = "grep 'POST /api/v1/checkout' gateway.log"
_ID = "cut -d' ' -f5 | cut -d= -f2"
_COMPARE = "comm -23 accepted.txt paid.txt > answer.txt"


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize(
    "script, word",
    [
        # リトライの重複を除かない
        (f"{_PAID}; {_CHECKOUT} | grep 'status=202' | {_ID} | sort > accepted.txt; {_COMPARE}", "重複"),
        # 400 で拒否されたものまで含める
        (f"{_PAID}; {_CHECKOUT} | {_ID} | sort -u > accepted.txt; {_COMPARE}", "status=400"),
        # checkout 以外のリクエストまで含める
        (f"{_PAID}; grep 'status=20' gateway.log | {_ID} | sort -u > accepted.txt; {_COMPARE}", "checkout 以外"),
        # req= を付けたまま
        (f"{_PAID}; {_CHECKOUT} | grep 'status=202' | cut -d' ' -f5 | sort -u > accepted.txt; {_COMPARE}", "ID だけ"),
        # 向きが逆(決済側にしかないもの。見出し行も混ざる)
        (
            f"cut -d, -f2 payment.csv | sort -u > paid.txt; {_CHECKOUT} | grep 'status=202' | {_ID} | sort -u > accepted.txt; "
            "comm -13 accepted.txt paid.txt > answer.txt",
            "見出し行",
        ),
    ],
)
def test_lost_orders_traps(tmp_path, seed, script, word):
    _fails("lost-orders", tmp_path, seed, script, word)


# ---- 同じ夜を繰り返さない --------------------------------------------

_SHEBANG = "#!/bin/bash"
_ARGS = '[ $# -eq 2 ] && [ -f "$1" ] || exit 2'
_COUNT = """count=$(awk '$3 == "ERROR"' "$1" | wc -l)"""
_JUDGE = 'if [ "$count" -gt "$2" ]; then echo "ALERT $count"; exit 1; fi\necho "OK $count"'


def _errwatch(*lines, executable=True):
    body = "\n".join(lines)
    after = "chmod +x errwatch.sh" if executable else "true"
    return f"cat > errwatch.sh <<'SCRIPT'\n{body}\nSCRIPT\n{after}"


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize(
    "script, word",
    [
        # 本文中の ERROR まで数える
        (_errwatch(_SHEBANG, _ARGS, 'count=$(grep -c ERROR "$1")', _JUDGE), "期待"),
        # ちょうど閾値のときに ALERT にしてしまう
        (_errwatch(_SHEBANG, _ARGS, _COUNT, _JUDGE.replace("-gt", "-ge")), "超えていない"),
        # 引用符がなく、空白を含むパスで壊れる
        (_errwatch(_SHEBANG, _ARGS.replace('"$1"', "$1"), _COUNT.replace('"$1"', "$1"), _JUDGE), "空白"),
        # 引数の確認がない
        (_errwatch(_SHEBANG, _COUNT, _JUDGE), "終了コード 2"),
        # 実行権限を付け忘れる
        (_errwatch(_SHEBANG, _ARGS, _COUNT, _JUDGE, executable=False), "chmod +x"),
        # 1 行目の指定がない
        (_errwatch(_ARGS, _COUNT, _JUDGE), "#!/bin/bash"),
        # 終わらないスクリプト
        (_errwatch(_SHEBANG, "while true; do sleep 1; done"), "終わらなかった"),
    ],
)
def test_errwatch_traps(tmp_path, seed, script, word):
    _fails("errwatch", tmp_path, seed, script, word)


def test_errwatch_sample_matches_ticket(tmp_path):
    script = MISSIONS["errwatch"].solution + "\n./errwatch.sh sample.log 10 > sample.out"
    played = play("errwatch", tmp_path, 5, script)
    assert played.result.ok, played.result.message
    assert (tmp_path / "home/tools/sample.out").read_text().strip() == played.ctx["sample_expected"]

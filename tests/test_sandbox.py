import os

from oncall import sandbox


def test_remove_handles_directories_without_permissions(tmp_path):
    layout = sandbox.Layout(tmp_path / "play")
    layout.create()
    nested = layout.home / "srv" / "certs"
    nested.mkdir(parents=True)
    (nested / "key.pem").write_text("secret")
    os.chmod(nested, 0o000)
    os.chmod(layout.home / "srv", 0o600)

    layout.remove()
    assert not layout.base.exists()

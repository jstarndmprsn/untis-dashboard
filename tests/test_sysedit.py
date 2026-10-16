"""sysedit.py ändert /boot/firmware/cmdline.txt, config.txt und lightdm.conf – hier gegen Temp-Dateien."""
import importlib
import os

import pytest


@pytest.fixture
def sysedit(tmp_path, monkeypatch):
    monkeypatch.setenv("RASPY_BACKUP_DIR", str(tmp_path / "backups"))
    import sysedit as mod
    return importlib.reload(mod)      # BACKUP_DIR wird beim Import gelesen


LIGHTDM = """[LightDM]
#minimum-vt=7

[Seat:*]
#display-setup-script=
autologin-user=pi
user-session=rpd-x
"""


def test_ini_get_set(tmp_path, sysedit):
    f = tmp_path / "lightdm.conf"
    f.write_text(LIGHTDM)
    assert sysedit.ini_get(str(f), "Seat:*", "autologin-user") == "pi"
    sysedit.ini_set(str(f), "Seat:*", "display-setup-script", "/usr/local/sbin/raspy-display-setup")
    sysedit.ini_set(str(f), "LightDM", "minimum-vt", "1")
    sysedit.ini_set(str(f), "Seat:*", "autologin-user", "kiosk")
    text = f.read_text()
    assert "display-setup-script=/usr/local/sbin/raspy-display-setup" in text
    assert sysedit.ini_get(str(f), "LightDM", "minimum-vt") == "1"
    assert text.count("autologin-user=") == 1 and sysedit.ini_get(str(f), "Seat:*", "autologin-user") == "kiosk"
    # Wert landet unter der auskommentierten Zeile, in der richtigen Sektion
    lines = text.splitlines()
    assert lines.index("minimum-vt=1") == lines.index("#minimum-vt=7") + 1
    sysedit.ini_set(str(f), "Neu", "a", "b")
    assert sysedit.ini_get(str(f), "Neu", "a") == "b"


def test_backup_keeps_original(tmp_path, sysedit):
    f = tmp_path / "config.txt"
    f.write_text("original\n")
    sysedit.block(str(f), "raspy fan", "dtparam=x\n")
    sysedit.block(str(f), "raspy fan", "dtparam=y\n")
    backups = sorted(os.listdir(tmp_path / "backups"))
    orig = [b for b in backups if b.endswith(".orig")]
    assert len(orig) == 1 and (tmp_path / "backups" / orig[0]).read_text() == "original\n"


def test_cmdline_idempotent_single_line_and_reversible(tmp_path, sysedit):
    f = tmp_path / "cmdline.txt"
    original = "console=serial0,115200 console=tty1 root=PARTUUID=x rootfstype=ext4 rootwait quiet splash\n"
    f.write_text(original)
    sysedit.cmdline(str(f))
    once = f.read_text()
    sysedit.cmdline(str(f))
    assert f.read_text() == once and once.count("\n") == 1
    tokens = once.split()
    assert "console=tty3" in tokens and "console=tty1" not in tokens and "loglevel=3" in tokens
    assert tokens.count("quiet") == 1
    sysedit.uncmdline(str(f))
    # Raspberry-Pi-OS-Standardparameter (quiet, splash, plymouth.ignore-serial-consoles) bleiben bewusst
    assert f.read_text().split() == original.split() + ["plymouth.ignore-serial-consoles"]


def test_block_unblock(tmp_path, sysedit):
    f = tmp_path / "config.txt"
    f.write_text("[all]\narm_64bit=1\n")
    sysedit.block(str(f), "raspy fan", "[all]\ndtparam=fan_temp0=82000\n")
    sysedit.block(str(f), "raspy fan", "[all]\ndtparam=fan_temp0=83000\n")    # ersetzt, nicht doppelt
    text = f.read_text()
    assert text.count("# >>> raspy fan") == 1 and "83000" in text and "82000" not in text
    assert sysedit.unblock_text(text, "raspy fan").strip() == "[all]\narm_64bit=1"

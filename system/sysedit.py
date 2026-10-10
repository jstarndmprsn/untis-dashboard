#!/usr/bin/env python3
"""
Kleine, vorsichtige Änderungen an Systemdateien für install.sh (läuft mit sudo).
Jede Datei wird vor der ersten Änderung nach BACKUP_DIR kopiert.

  sysedit.py ini-get  FILE SECTION KEY            Wert ausgeben (leer, wenn nicht gesetzt)
  sysedit.py ini-set  FILE SECTION KEY VALUE      Schlüssel in [SECTION] setzen (z. B. lightdm.conf)
  sysedit.py cmdline  FILE                        Boot-Animation: Parameter in cmdline.txt ergänzen
  sysedit.py uncmdline FILE                       … und wieder entfernen
  sysedit.py block    FILE MARKER                 Block (stdin) zwischen "# >>> MARKER" / "# <<< MARKER" setzen
  sysedit.py unblock  FILE MARKER                 diesen Block wieder entfernen
"""
import os
import re
import shutil
import sys
import time

BACKUP_DIR = os.environ.get("RASPY_BACKUP_DIR", "/var/backups/raspy")


def backup(path):
    """Erste Sicherung bleibt erhalten (Originalzustand vor raspy)."""
    if not os.path.exists(path):
        return
    os.makedirs(BACKUP_DIR, exist_ok=True)
    dest = os.path.join(BACKUP_DIR, path.strip("/").replace("/", "__") + ".orig")
    if not os.path.exists(dest):
        shutil.copy2(path, dest)
    else:
        shutil.copy2(path, dest.replace(".orig", time.strftime(".%Y%m%d-%H%M%S")))


def write(path, text):
    backup(path)
    tmp = path + ".raspy-tmp"
    with open(tmp, "w") as f:
        f.write(text)
    if os.path.exists(path):
        shutil.copymode(path, tmp)
    os.replace(tmp, path)


def read(path):
    try:
        with open(path) as f:
            return f.read()
    except FileNotFoundError:
        return ""


def ini_lines(path):
    return read(path).splitlines()


def section_range(lines, section):
    start = None
    for i, l in enumerate(lines):
        if l.strip().startswith("["):
            if start is not None:
                return start, i
            if l.strip() == f"[{section}]":
                start = i + 1
    return (start, len(lines)) if start is not None else (None, None)


def ini_get(path, section, key):
    lines = ini_lines(path)
    s, e = section_range(lines, section)
    if s is None:
        return ""
    val = ""
    for l in lines[s:e]:
        m = re.match(rf"\s*{re.escape(key)}\s*=(.*)$", l)
        if m:
            val = m.group(1).strip()
    return val


def ini_set(path, section, key, value):
    lines = ini_lines(path)
    s, e = section_range(lines, section)
    new = f"{key}={value}"
    if s is None:
        lines += ["", f"[{section}]", new]
    else:
        active = [i for i in range(s, e) if re.match(rf"\s*{re.escape(key)}\s*=", lines[i])]
        commented = [i for i in range(s, e) if re.match(rf"\s*#\s*{re.escape(key)}\s*=", lines[i])]
        if active:
            lines[active[-1]] = new
            for i in reversed(active[:-1]):
                del lines[i]
        elif commented:
            lines.insert(commented[0] + 1, new)
        else:
            lines.insert(e, new)
    write(path, "\n".join(lines) + "\n")


CMDLINE_ADD = ["quiet", "splash", "plymouth.ignore-serial-consoles", "logo.nologo",
               "vt.global_cursor_default=0", "loglevel=3"]


def cmdline(path):
    tokens = read(path).split()
    if not tokens:
        sys.exit(f"{path} ist leer oder fehlt")
    # Kernel-Meldungen auf tty3 statt tty1 (dort läuft die Animation)
    tokens = ["console=tty3" if t == "console=tty1" else t for t in tokens]
    if not any(t.startswith("console=tty") for t in tokens):
        tokens.append("console=tty3")
    for t in CMDLINE_ADD:
        key = t.split("=")[0]
        if not any(x == t or x.split("=")[0] == key for x in tokens):
            tokens.append(t)
    write(path, " ".join(tokens) + "\n")   # cmdline.txt muss EINE Zeile bleiben


def uncmdline(path):
    """Boot-Animation entfernen: tty1 zurück, eigene Zusatz-Parameter raus
    (quiet/splash/plymouth.ignore-serial-consoles sind Raspberry-Pi-OS-Standard und bleiben)."""
    drop = {"logo.nologo", "vt.global_cursor_default=0", "loglevel=3"}
    tokens = ["console=tty1" if t == "console=tty3" else t for t in read(path).split() if t not in drop]
    if tokens:
        write(path, " ".join(tokens) + "\n")


def block(path, marker, content):
    text = unblock_text(read(path), marker).rstrip("\n")
    text += f"\n\n# >>> {marker}\n{content.strip()}\n# <<< {marker}\n"
    write(path, text.lstrip("\n"))


def unblock_text(text, marker):
    return re.sub(rf"\n*# >>> {re.escape(marker)}\n.*?# <<< {re.escape(marker)}\n?", "\n", text, flags=re.S)


def main():
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == "ini-get":
        print(ini_get(*args))
    elif cmd == "ini-set":
        ini_set(*args)
    elif cmd == "cmdline":
        cmdline(*args)
    elif cmd == "uncmdline":
        uncmdline(*args)
    elif cmd == "block":
        block(args[0], args[1], sys.stdin.read())
    elif cmd == "unblock":
        text = read(args[0])
        new = unblock_text(text, args[1])
        if new != text:
            write(args[0], new)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()

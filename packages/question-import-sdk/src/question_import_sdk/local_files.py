"""Small platform boundary for private files and exclusive local task locks."""

import os
import stat
import subprocess
from pathlib import Path


def windows_acl(path, action):
    helper = Path(__file__).with_name("windows_acl.ps1")
    result = subprocess.run(
        ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
         "-ExecutionPolicy", "Bypass", "-File", str(helper), action, str(path)],
        capture_output=True, timeout=30, check=False,
    )
    if result.returncode:
        raise ValueError("本机文件权限不可用，请使用当前用户可独占访问的本地目录")


def protect_file(fd, path):
    if os.name == "nt":
        windows_acl(path, "protect")
    else:
        os.fchmod(fd, 0o600)


def check_private(path, *, directory=False):
    value = Path(path).lstat()
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if not kind(value.st_mode) or Path(path).is_symlink():
        raise ValueError("私密记录不能为链接或其他特殊文件")
    if os.name == "nt":
        windows_acl(path, "check")
    elif (value.st_uid != os.getuid()
          or stat.S_IMODE(value.st_mode) != (0o700 if directory else 0o600)):
        raise ValueError("私密记录须归当前用户所有，目录0700／文件0600")
    return value


def sync_directory(path):
    # Windows flushes the file before atomic os.replace; directory fsync is POSIX-only.
    if os.name == "nt":
        return
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def open_regular(path):
    path = Path(path)
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or path.is_symlink():
        raise ValueError("文件必须是普通文件，不能为符号链接")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags)
    opened = os.fstat(fd)
    if (not stat.S_ISREG(opened.st_mode)
        or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)):
        os.close(fd)
        raise ValueError("文件在读取时发生变化")
    return fd


def lock_exclusive(fd):
    try:
        if os.name == "nt":
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        raise BlockingIOError("此任务正在由另一进程操作") from error

import logging
import os
import sys
import time
from argparse import Action, ArgumentParser, Namespace
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, List

from idleon_saver.log import configure_logging

ROOT_DIR = Path(__file__).resolve().parent.parent
BUGREPORT_LINK = "https://github.com/desophos/idleon-saver/issues/new?assignees=desophos&labels=bug&template=bug_report.md&title="


class Sources(Enum):
    LOCAL = "local"
    FIREBASE = "firebase"


class Formats(Enum):
    IC = "idleon_companion"
    COG = "cogstruction"
    TOOLBOX = "toolbox"
    EFFICIENCY = "efficiency"


class Args(Enum):
    IDLEON = "idleon"
    LDB = "ldb"
    WORKDIR = "workdir"
    INFILE = "infile"
    OUTFILE = "outfile"
    SOURCE = "source"
    TO = "to"


class IdleonAction(Action):
    def __call__(self, parser, namespace, value, option_string=None):
        # In case someone passes the exe path instead of the install dir.
        if value.name == "LegendsOfIdleon.exe":
            value = value.parent
        setattr(namespace, self.dest, value)


class LdbAction(Action):
    def __call__(self, parser, namespace, value, option_string=None):
        # Only check ldb path.
        # Idleon path is only used for the db key, so it doesn't have to exist.
        # (Allows running from VMs.)
        if not (value.exists() and value.is_dir()):
            raise IOError(f"Invalid leveldb path: {value}")
        setattr(namespace, self.dest, value)


class WorkdirAction(Action):
    def __call__(self, parser, namespace, value, option_string=None):
        value.mkdir(exist_ok=True)
        setattr(namespace, self.dest, value)


class SourceAction(Action):
    def __call__(self, parser, namespace, value, option_string=None):
        setattr(namespace, self.dest, Sources(value))


class ToAction(Action):
    def __call__(self, parser, namespace, value, option_string=None):
        setattr(namespace, self.dest, Formats(value))


arg_adders: dict[Args, Callable[[ArgumentParser], Any]] = {
    Args.IDLEON: lambda parser: parser.add_argument(
        "-n",
        "--idleon",
        type=Path,
        default="C:/Program Files (x86)/Steam/steamapps/common/Legends of Idleon",
        help="your Legends of Idleon install path",
        action=IdleonAction,
    ),
    Args.LDB: lambda parser: parser.add_argument(
        "-l",
        "--ldb",
        type=resolved_path,
        default="~/dev/leveldb",
        help="path to the leveldb to work with",
        action=LdbAction,
    ),
    Args.WORKDIR: lambda parser: parser.add_argument(
        "-w",
        "--workdir",
        type=resolved_path,
        default=ROOT_DIR / "work",
        help="path to the working directory where files will be created",
        action=WorkdirAction,
    ),
    Args.INFILE: lambda parser: parser.add_argument(
        "-i",
        "--infile",
        default="",
        help="name of the input file; default varies by script",
    ),
    Args.OUTFILE: lambda parser: parser.add_argument(
        "-o",
        "--outfile",
        default="",
        help="name of the output file; default varies by script",
    ),
    Args.SOURCE: lambda parser: parser.add_argument(
        "-s",
        "--source",
        choices=[member.value for member in Sources],
        default=Sources.FIREBASE.value,
        help="source of save data",
        action=SourceAction,
    ),
    Args.TO: lambda parser: parser.add_argument(
        "-t",
        "--to",
        choices=[member.value for member in Formats],
        default=Formats.IC.value,
        help="format to parse save data into",
        action=ToAction,
    ),
}


def get_args(*to_add: Args) -> Namespace:
    # Route logging through the shared idleon-saver logger (console handler).
    configure_logging()

    parser = ArgumentParser()
    for arg in to_add:
        arg_adders[arg](parser)
    return parser.parse_args()


def friendly_name(s: str) -> str:
    return s.replace("_", " ").title()


def user_dir() -> Path:
    path = Path(os.environ["APPDATA"], "IdleonSaver")
    path.mkdir(exist_ok=True)
    return path


def logs_dir() -> Path:
    path = user_dir() / "logs"
    path.mkdir(exist_ok=True)
    return path


def zip_from_iterable(iterables):
    return zip(*iterables)


def dict_sorted(d: dict) -> dict:
    return dict(sorted(d.items()))


def from_keys_in(d: dict, keys: Iterable, value=None) -> dict:
    return {d[key]: value for key in keys if key in d}


def chunk(s: str, chunk_size: int) -> List[str]:
    return [s[i : i + chunk_size] for i in range(0, len(s), chunk_size)]


def resolved_path(s: str) -> Path:
    return Path(s).expanduser().resolve()


def wait_for(check: Callable[[], Any], timeout: float = 1.0) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        if check():
            return True
        time.sleep(0.1)
    return False


def locate_leveldb() -> "Path | None":
    """探测 Legends of Idleon 的 leveldb 存档目录。

    路径：``%APPDATA%\\legends-of-idleon\\Local Storage\\leveldb``。
    存在且为目录时返回该 Path，否则返回 None（由 UI 回退手动选择）。
    """
    appdata = os.environ.get("APPDATA", "")
    if not appdata:
        return None
    candidate = Path(
        appdata, "legends-of-idleon", "Local Storage", "leveldb"
    )
    if candidate.exists() and candidate.is_dir():
        return candidate
    return None


def locate_idleon_install() -> "Path | None":
    """定位 Steam 版 Legends of Idleon 安装目录（默认路径）。

    默认 ``C:/Program Files (x86)/Steam/steamapps/common/Legends of Idleon``；
    存在且为目录时返回该 Path，否则返回 None。
    """
    default = Path(
        "C:/Program Files (x86)/Steam/steamapps/common/Legends of Idleon"
    )
    if default.exists() and default.is_dir():
        return default
    return None

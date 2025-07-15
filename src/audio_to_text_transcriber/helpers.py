import os
from pathlib import Path

HOME_DIR = str(Path.home())

def human_path(path: str) -> str:
    if not path:
        return path
    path = str(path)
    if path == HOME_DIR:
        return "~"
    if path.startswith(HOME_DIR + os.sep):
        return "~" + path[len(HOME_DIR):]
    return path

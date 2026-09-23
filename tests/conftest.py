"""pytest 公共配置。

pytest 默认的 tmp_path 落在 /private/var/folders 下，在受限环境里可能没有写权限，
所以这里统一改到 /tmp 下的一次性目录。
"""

import shutil
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def tmp_dir():
    """在 /tmp 下开一个临时目录，用完删掉。"""
    path = Path(tempfile.mkdtemp(prefix="macaccordion-test-", dir="/tmp"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)

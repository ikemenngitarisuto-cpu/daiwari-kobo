import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))


def load_app_module(path=None):
    path = Path(path or ROOT / "src" / "daiwari_kobo.py")
    spec = importlib.util.spec_from_file_location("daiwari_kobo_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def dk():
    return load_app_module()


@pytest.fixture
def sample(tmp_path, monkeypatch):
    """小さな試験用フォルダ（12ページ・v2 なし）を作って返す"""
    import make_sample
    monkeypatch.setenv("DAIWARI_APPDATA", str(tmp_path / "appdata"))
    folder = tmp_path / "作品A"
    make_sample.make(folder, 12, v2=False, size=(160, 234))
    return folder


@pytest.fixture
def sample_v2(tmp_path, monkeypatch):
    import make_sample
    monkeypatch.setenv("DAIWARI_APPDATA", str(tmp_path / "appdata"))
    folder = tmp_path / "作品B"
    make_sample.make(folder, 24, v2=True, size=(160, 234))
    return folder

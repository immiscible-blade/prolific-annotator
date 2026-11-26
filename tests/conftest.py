import os
import shutil
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from prolific_annotator.build import build  # noqa: E402
from prolific_annotator.config import load_config  # noqa: E402

EXAMPLES = os.path.join(ROOT, "src", "prolific_annotator", "examples")


def copy_example(name, dest):
    shutil.copytree(os.path.join(EXAMPLES, name), dest)
    return os.path.join(dest, "study.yaml")


@pytest.fixture
def tree_study(tmp_path):
    """The decision-tree example, copied into a temp dir and built."""
    cfg_path = copy_example("decision-tree", tmp_path / "study")
    cfg = load_config(cfg_path)
    out = tmp_path / "build"
    build(cfg, str(out))
    return cfg, str(out), cfg_path


@pytest.fixture
def survey_study(tmp_path):
    cfg_path = copy_example("survey", tmp_path / "study")
    cfg = load_config(cfg_path)
    out = tmp_path / "build"
    build(cfg, str(out))
    return cfg, str(out), cfg_path

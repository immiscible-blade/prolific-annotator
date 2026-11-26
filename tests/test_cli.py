import os

import pytest

from prolific_annotator.cli import main


def test_cli_end_to_end(tmp_path, capsys):
    d = tmp_path / "s"
    main(["init", str(d)])
    cfg = str(d / "study.yaml")
    main(["validate", cfg])
    assert "OK:" in capsys.readouterr().out
    main(["build", cfg, "--out", str(d / "build"), "--prefix", "pilot"])
    main(["simulate", cfg, str(d / "build"), "--out", str(d / "sim.csv")])
    main(["ingest", cfg, str(d / "build"), str(d / "sim.csv"), "--out", str(d / "res")])
    for f in ["worker_flags.csv", "votes.csv", "item_distributions.csv", "answer_counts.csv"]:
        assert os.path.exists(d / "res" / f)
    main(["compare", str(d / "res" / "votes.csv"), str(d / "res" / "votes.csv"), "--n-perm", "50"])
    assert '"mean_jsd": 0.0' in capsys.readouterr().out


def test_cli_config_error(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("task:\n  questions: []\n")
    with pytest.raises(SystemExit, match="config error"):
        main(["validate", str(p)])


def test_init_refuses_non_empty(tmp_path):
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "f").write_text("1")
    with pytest.raises(SystemExit):
        main(["init", str(tmp_path / "x")])

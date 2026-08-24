import json

import pytest

from syndicate.cli import main


def doc(tmp_path, name="notes/post.md"):
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        "---\ntitle: A Post\ncrosspost:\n  devto: full\n  linkedin: summary\n---\n\n"
        "Problem.\n\nFinding.\n\nTrade-off.\n"
    )
    return p


def base_args(tmp_path, mode):
    return [
        mode,
        "--repo-root", str(tmp_path),
        "--repository", "acme/notes",
        "--manifest", str(tmp_path / "manifest.json"),
        str(doc(tmp_path)),
    ]


def test_dry_run_makes_no_writes_and_exits_zero(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("DEVTO_API_KEY", raising=False)
    code = main(base_args(tmp_path, "draft") + ["--dry-run", "--dotenv", str(tmp_path / "none")])
    assert code == 0
    assert not (tmp_path / "manifest.json").exists()
    assert "dry-run" in capsys.readouterr().out.lower()


def test_dry_run_prints_the_rendered_payload(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("DEVTO_API_KEY", raising=False)
    main(base_args(tmp_path, "draft") + ["--dry-run", "--dotenv", str(tmp_path / "none")])
    out = capsys.readouterr().out
    assert "originally published at" in out
    assert "https://acme.github.io/notes/notes/post/" in out


def test_missing_devto_credential_is_a_soft_skip_not_a_crash(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("DEVTO_API_KEY", raising=False)
    monkeypatch.delenv("LINKEDIN_ACCESS_TOKEN", raising=False)
    code = main(base_args(tmp_path, "draft") + ["--dotenv", str(tmp_path / "none")])
    assert code == 0
    assert "skipped" in capsys.readouterr().out.lower()


def test_invalid_frontmatter_exits_nonzero(tmp_path, capsys):
    p = tmp_path / "bad.md"
    p.write_text("---\ntitle: X\ncrosspost:\n  devto: sideways\n---\n\nBody.\n")
    code = main([
        "draft", "--repo-root", str(tmp_path), "--repository", "acme/notes",
        "--manifest", str(tmp_path / "m.json"), "--dry-run",
        "--dotenv", str(tmp_path / "none"), str(p),
    ])
    assert code == 2
    assert "sideways" in capsys.readouterr().err


def test_unknown_mode_is_rejected(tmp_path):
    with pytest.raises(SystemExit):
        main(["teleport", "--repo-root", str(tmp_path)])


def test_json_output_lists_per_platform_results(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("DEVTO_API_KEY", raising=False)
    monkeypatch.delenv("LINKEDIN_ACCESS_TOKEN", raising=False)
    main(base_args(tmp_path, "draft") + ["--json", "--dotenv", str(tmp_path / "none")])
    payload = json.loads(capsys.readouterr().out)
    assert {r["platform"] for r in payload["results"]} == {"devto", "linkedin"}
    assert payload["mode"] == "draft"


def test_step_summary_is_written_when_github_env_is_set(tmp_path, monkeypatch):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.delenv("DEVTO_API_KEY", raising=False)
    main(base_args(tmp_path, "draft") + ["--dotenv", str(tmp_path / "none")])
    assert "devto" in summary.read_text()


def test_repository_defaults_to_github_repository_env(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/from-env")
    monkeypatch.delenv("DEVTO_API_KEY", raising=False)
    main([
        "draft", "--repo-root", str(tmp_path), "--manifest", str(tmp_path / "m.json"),
        "--dry-run", "--dotenv", str(tmp_path / "none"), str(doc(tmp_path)),
    ])
    assert "acme.github.io/from-env" in capsys.readouterr().out

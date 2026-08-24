import pytest

from syndicate.config import MissingCredential, Secrets, load_dotenv


def test_dotenv_parses_simple_pairs(tmp_path):
    p = tmp_path / ".env"
    p.write_text("DEVTO_API_KEY=abc123\nLINKEDIN_PERSON_URN=urn:li:person:X\n")
    assert load_dotenv(p) == {
        "DEVTO_API_KEY": "abc123",
        "LINKEDIN_PERSON_URN": "urn:li:person:X",
    }


def test_dotenv_ignores_comments_blanks_and_export_prefix(tmp_path):
    p = tmp_path / ".env"
    p.write_text("# a comment\n\nexport DEVTO_API_KEY=abc\n   \n")
    assert load_dotenv(p) == {"DEVTO_API_KEY": "abc"}


def test_dotenv_strips_matching_quotes_and_trailing_space(tmp_path):
    p = tmp_path / ".env"
    p.write_text('DEVTO_API_KEY="abc"  \nLINKEDIN_ACCESS_TOKEN=\'xyz\'\n')
    assert load_dotenv(p) == {"DEVTO_API_KEY": "abc", "LINKEDIN_ACCESS_TOKEN": "xyz"}


def test_dotenv_returns_empty_when_file_absent(tmp_path):
    assert load_dotenv(tmp_path / "nope.env") == {}


def test_environment_wins_over_dotenv(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("DEVTO_API_KEY=from-file\n")
    monkeypatch.setenv("DEVTO_API_KEY", "from-ci-secret")
    s = Secrets.load(dotenv_path=tmp_path / ".env")
    assert s.require("DEVTO_API_KEY") == "from-ci-secret"


def test_dotenv_used_when_environment_is_unset(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("DEVTO_API_KEY=from-file\n")
    monkeypatch.delenv("DEVTO_API_KEY", raising=False)
    s = Secrets.load(dotenv_path=tmp_path / ".env")
    assert s.require("DEVTO_API_KEY") == "from-file"


def test_missing_credential_raises_with_the_name_but_never_a_value(tmp_path, monkeypatch):
    monkeypatch.delenv("DEVTO_API_KEY", raising=False)
    s = Secrets.load(dotenv_path=tmp_path / "nope.env")
    with pytest.raises(MissingCredential) as exc:
        s.require("DEVTO_API_KEY")
    assert "DEVTO_API_KEY" in str(exc.value)


def test_blank_value_counts_as_missing(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("DEVTO_API_KEY=   \n")
    monkeypatch.delenv("DEVTO_API_KEY", raising=False)
    s = Secrets.load(dotenv_path=tmp_path / ".env")
    assert s.has("DEVTO_API_KEY") is False


def test_repr_never_leaks_secret_values(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVTO_API_KEY", "super-secret-value")
    s = Secrets.load(dotenv_path=tmp_path / "nope.env")
    assert "super-secret-value" not in repr(s)
    assert "super-secret-value" not in str(s)


def test_redact_masks_a_known_secret_in_arbitrary_text(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVTO_API_KEY", "super-secret-value")
    s = Secrets.load(dotenv_path=tmp_path / "nope.env")
    msg = s.redact("request failed with key super-secret-value in the header")
    assert "super-secret-value" not in msg
    assert "***" in msg

from pathlib import Path

from pipelinedoctor_console.settings import BUILTIN_FIXTURES, FIXTURE_NOW, Settings


def test_defaults() -> None:
    s = Settings.from_env({})
    assert s.repo == "gagan046-dev/pipelinedoctor"
    assert s.token == ""
    assert s.cache_ttl_s == 900  # unauthenticated: longer TTL to respect 60 requests/hour
    assert s.color == "none"
    assert not s.fixture_mode
    assert s.problems() == []


def test_token_precedence_and_ttl() -> None:
    s = Settings.from_env({"GITHUB_TOKEN": "generic", "PD_GITHUB_TOKEN": " specific \n"})
    assert s.token == "specific"
    assert s.cache_ttl_s == 300
    assert Settings.from_env({"GH_TOKEN": "gh"}).token == "gh"


def test_token_from_file(tmp_path: Path) -> None:
    secret = tmp_path / "token.txt"
    secret.write_text("from-file\n", encoding="utf-8")
    assert Settings.from_env({"PD_GITHUB_TOKEN_FILE": str(secret)}).token == "from-file"
    missing = Settings.from_env({"PD_GITHUB_TOKEN_FILE": str(tmp_path / "nope"), "GH_TOKEN": "x"})
    assert missing.token == "x"


def test_token_never_in_repr() -> None:
    s = Settings.from_env({"PD_GITHUB_TOKEN": "super-secret-value"})
    assert "super-secret-value" not in repr(s)
    assert "super-secret-value" not in str(s)


def test_fixture_flag_and_clock(tmp_path: Path) -> None:
    builtin = Settings.from_env({"PD_CONSOLE_FIXTURES": "1"})
    assert builtin.fixtures_dir == BUILTIN_FIXTURES
    assert builtin.authenticated
    assert builtin.now() == FIXTURE_NOW
    custom = Settings.from_env({"PD_CONSOLE_FIXTURES": str(tmp_path)})
    assert custom.fixtures_dir == tmp_path
    assert Settings.from_env({"PD_CONSOLE_FIXTURES": "0"}).fixtures_dir is None


def test_problems() -> None:
    bad = Settings.from_env(
        {
            "PD_CONSOLE_REPO": "not a repo",
            "GITHUB_API_URL": "ftp://x",
            "PD_CONSOLE_FIXTURES": "/does/not/exist",
        }
    )
    assert len(bad.problems()) == 3


def test_ttl_override_and_garbage() -> None:
    assert Settings.from_env({"PD_CONSOLE_CACHE_TTL_S": "12"}).cache_ttl_s == 12
    assert Settings.from_env({"PD_CONSOLE_CACHE_TTL_S": "abc"}).cache_ttl_s == 900

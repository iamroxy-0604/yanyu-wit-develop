from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from acps_cli.main import main

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "cli-help"
PROGRAM_NAME = "acps-cli"


def _load_fragments(file_name: str) -> list[str]:
    return [line.strip() for line in (FIXTURE_DIR / file_name).read_text(encoding="utf-8").splitlines() if line.strip()]


def _assert_fragments_in_order(output: str, fragments: list[str]) -> None:
    cursor = 0
    for fragment in fragments:
        next_cursor = output.find(fragment, cursor)
        assert next_cursor != -1, f"Missing help fragment: {fragment}\nActual output:\n{output}"
        cursor = next_cursor + len(fragment)


def test_unified_root_help_matches_golden_fragments() -> None:
    runner = CliRunner()

    result = runner.invoke(main, ["--help"], prog_name=PROGRAM_NAME, terminal_width=100)

    assert result.exit_code == 0
    _assert_fragments_in_order(result.output, _load_fragments("root.txt"))


def test_unified_admin_help_matches_golden_fragments() -> None:
    runner = CliRunner()

    result = runner.invoke(main, ["admin", "--help"], prog_name=PROGRAM_NAME, terminal_width=100)

    assert result.exit_code == 0
    _assert_fragments_in_order(result.output, _load_fragments("admin.txt"))


def test_unified_cert_help_matches_golden_fragments() -> None:
    runner = CliRunner()

    result = runner.invoke(main, ["cert", "--help"], prog_name=PROGRAM_NAME, terminal_width=100)

    assert result.exit_code == 0
    _assert_fragments_in_order(result.output, _load_fragments("cert.txt"))


def test_unified_admin_discovery_help_matches_golden_fragments() -> None:
    runner = CliRunner()

    result = runner.invoke(
        main,
        ["admin", "discovery", "--help"],
        prog_name=PROGRAM_NAME,
        terminal_width=100,
    )

    assert result.exit_code == 0
    _assert_fragments_in_order(result.output, _load_fragments("admin-discovery.txt"))

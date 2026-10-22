"""Codex CLI UserPromptSubmit adapter."""

from acc.adapters.base import run_hook


def main() -> None:
    run_hook("codex")


if __name__ == "__main__":
    main()

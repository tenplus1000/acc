"""Claude Code UserPromptSubmit adapter."""

from acc.adapters.base import run_hook


def main() -> None:
    run_hook("claude")


if __name__ == "__main__":
    main()

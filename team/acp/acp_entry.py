"""Expose the installed Lark tool only to the dedicated Tasks ACP process."""
from toolsets import TOOLSETS
from hermes_cli.main import main

TOOLSETS["hermes-acp"]["includes"].append("lark_cli")

if __name__ == "__main__":
    main()

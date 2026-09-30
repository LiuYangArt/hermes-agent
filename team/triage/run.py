"""Persistent entry point for the existing Hermes no-agent cron job."""
from triage import main

if __name__ == "__main__":
    raise SystemExit(main(["--apply"]))

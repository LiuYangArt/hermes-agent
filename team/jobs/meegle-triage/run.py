"""Persistent entry point for the existing Hermes no-agent cron job."""
from triage import main
from owner_resolver import translate_body


if __name__ == "__main__":
    raise SystemExit(main(["--apply"], translator=translate_body))

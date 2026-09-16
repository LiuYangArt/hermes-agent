import argparse
import json
from pathlib import Path


DISPLAY_STATUS = {
    "allow_search": True,
    "allow_highlight": True,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build per-entry Lingo draft payload JSON files.")
    parser.add_argument("--source", dest="source", required=True)
    parser.add_argument("--output-dir", dest="output_dir", required=True)
    parser.add_argument("--classification-id", dest="classification_id", required=True)
    parser.add_argument("--father-id", dest="father_id", required=True)
    parser.add_argument("--provider", default="LarkLingoImport")
    parser.add_argument("--outer-id-prefix", dest="outer_id_prefix", default="lingo")
    parser.add_argument("--escape-for-cmd", dest="escape_for_cmd", action=argparse.BooleanOptionalAction, default=True)
    return parser.parse_args()


def build_display_status() -> dict[str, bool]:
    return dict(DISPLAY_STATUS)


def build_aliases(raw_aliases):
    if not raw_aliases:
        return []
    return [
        {
            "key": str(alias),
            "display_status": build_display_status(),
        }
        for alias in raw_aliases
    ]


def build_docs(raw_docs):
    if not raw_docs:
        return []
    return [
        {
            "url": str(doc.get("url", "")),
            "title": str(doc.get("title", "")),
        }
        for doc in raw_docs
    ]


def main() -> int:
    args = parse_args()

    source_path = Path(args.source)
    output_dir = Path(args.output_dir)

    raw = source_path.read_text(encoding="utf-8")
    source = json.loads(raw)
    entries = source.get("entries")
    if not entries:
        raise ValueError("Source JSON must contain a non-empty entries array.")

    output_dir.mkdir(parents=True, exist_ok=True)
    for existing in output_dir.glob("*.json"):
        existing.unlink()

    for index, entry in enumerate(entries, start=1):
        outer_id = str(entry.get("outer_id") or f"{args.outer_id_prefix}_{index:03d}")
        payload = {
            "main_keys": [
                {
                    "key": str(entry.get("main_key", "")),
                    "display_status": build_display_status(),
                }
            ],
            "aliases": build_aliases(entry.get("aliases")),
            "description": str(entry.get("description", "")),
            "related_meta": {
                "classifications": [
                    {
                        "id": args.classification_id,
                        "father_id": args.father_id,
                    }
                ],
                "docs": build_docs(entry.get("related_docs")),
            },
            "outer_info": {
                "outer_id": outer_id,
                "provider": args.provider,
            },
        }

        payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if args.escape_for_cmd:
            payload_json = payload_json.replace('"', '""')

        output_path = output_dir / f"{index:03d}.json"
        output_path.write_text(payload_json, encoding="utf-8", newline="")

    print(f"payload_count={len(entries)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
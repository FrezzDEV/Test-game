def build_plan(parsed: dict) -> list[dict]:
    if not parsed.get("is_giveaway") or parsed.get("needs_human"):
        return []
    return parsed.get("actions", [])

from __future__ import annotations

import json
import re
from typing import Any

TOKEN = re.compile(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*[\s\S]*?\*/|[{}\[\]:,]|[^\s{}\[\]:,]+')


def parse_jsonc(text: str) -> Any:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate configuration key: " + key)
            result[key] = value
        return result

    tokens = [m.group() for m in TOKEN.finditer(text) if not m.group().startswith(("//", "/*"))]
    return json.loads(
        " ".join(
            item
            for i, item in enumerate(tokens)
            if not (item == "," and i + 1 < len(tokens) and tokens[i + 1] in ("}", "]"))
        ),
        object_pairs_hook=unique_object,
    )

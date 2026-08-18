import json
from functools import lru_cache
from importlib.resources import files
from typing import Any


@lru_cache(maxsize=1)
def load_workbook_contract() -> dict[str, Any]:
	contract_path = files("mule_network_ai_review.contracts").joinpath(
		"workbook_schema_v1.json"
	)
	return json.loads(contract_path.read_text(encoding="utf-8"))

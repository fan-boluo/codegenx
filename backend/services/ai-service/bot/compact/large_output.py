from typing import Dict, Any

from shared.constants import PERSIST_THRESHOLD, get_current_session_dir, PREVIEW_CHARS, get_runtime_app_dir
import uuid

def persist_large_output(tool_call: Dict[str, Any], output: str,app_id:str,session_id:str) -> str:
    if len(output) <= PERSIST_THRESHOLD:
        return output
    tool_call_id = tool_call.get("id", app_id+"_"+uuid.uuid4().hex)

    persist_dir = get_current_session_dir(app_id,session_id)
    persist_dir.mkdir(parents=True, exist_ok=True)
    stored_path = persist_dir / f"{tool_call_id}_persist.txt"
    if not stored_path.exists():
        stored_path.write_text(output, encoding="utf-8")

    # TODO 跟踪什么
    # self._track_recent_file(context, stored_path)

    try:
        rel_path = stored_path.relative_to(get_runtime_app_dir(app_id))
    except ValueError:
        rel_path = stored_path

    preview = output[:PREVIEW_CHARS]
    return (
        "<persisted-output>\n"
        f"Full output saved to: {rel_path}\n"
        "Preview:\n"
        f"{preview}\n"
        "</persisted-output>"
    )
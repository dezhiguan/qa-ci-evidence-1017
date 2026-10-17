import re
import json
from typing import Dict, Any


async def execute(data: dict, ctx) -> dict:
    # Extract CI run ID and PR number from input text
    text = data.get("text", "")
    if not isinstance(text, str):
        return {"decision": "BLOCKED", "reason": "Input text is not a string"}

    # Parse CI run ID and PR number with strict validation
    ci_match = re.search(r"CI\s+run\s+(\d+)", text, re.IGNORECASE)
    pr_match = re.search(r"PR\s+#(\d+)", text, re.IGNORECASE)

    ci_run_id = int(ci_match.group(1)) if ci_match else None
    pr_number = int(pr_match.group(1)) if pr_match else None

    # Block on missing or invalid numeric IDs (including <= 0)
    if ci_run_id is None or pr_number is None:
        return {"decision": "BLOCKED", "reason": "Missing CI run ID or PR number"}
    if ci_run_id <= 0 or pr_number <= 0:
        return {"decision": "BLOCKED", "reason": "CI run ID or PR number must be positive integer"}

    # Fetch evidence in strict sequence
    try:
        log_result = await ctx.tools.call("ci.log.fetch", run_id=ci_run_id)
    except Exception:
        return {"decision": "BLOCKED", "reason": "ci.log.fetch failed"}

    try:
        gate_result = await ctx.tools.call("keel.gate.report.read", run_id=ci_run_id)
    except Exception:
        return {"decision": "BLOCKED", "reason": "keel.gate.report.read failed"}

    try:
        diff_result = await ctx.tools.call("git.pr.diff", pr_number=pr_number)
    except Exception:
        return {"decision": "BLOCKED", "reason": "git.pr.diff failed"}

    # Validate non-empty, non-error payloads
    if not isinstance(log_result, dict) or "steps" not in log_result or not log_result["steps"]:
        return {"decision": "BLOCKED", "reason": "Invalid or empty log result"}
    if not isinstance(gate_result, dict) or "status" not in gate_result:
        return {"decision": "BLOCKED", "reason": "Invalid or empty gate report"}
    if not isinstance(diff_result, dict) or "files" not in diff_result:
        return {"decision": "BLOCKED", "reason": "Invalid or empty PR diff"}

    # Compile prompt with all evidence
    prompt = await ctx.prompt("role")
    compiled = prompt.compile(
        input=text,
        ci_run_id=ci_run_id,
        pr_number=pr_number,
        log_result=json.dumps(log_result, ensure_ascii=False),
        gate_result=json.dumps(gate_result, ensure_ascii=False),
        diff_result=json.dumps(diff_result, ensure_ascii=False),
    )

    # LLM inference
    llm_response = await ctx.llm.chat([
        {"role": "user", "content": compiled}
    ])

    # Parse structured output — expect JSON-like decision object
    try:
        # Try to extract first valid JSON object from response
        json_start = llm_response.find('{')
        json_end = llm_response.rfind('}') + 1
        if json_start == -1 or json_end == 0:
            raise ValueError("No JSON object found in LLM response")
        parsed = json.loads(llm_response[json_start:json_end])
        if not isinstance(parsed, dict) or "decision" not in parsed:
            raise ValueError("LLM response missing 'decision' key")
        return parsed
    except (json.JSONDecodeError, ValueError, TypeError):
        return {"decision": "BLOCKED", "reason": "LLM returned malformed or incomplete decision"}
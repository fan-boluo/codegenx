"""
Compaction prompt templates.

Mirrors src/services/compact/prompt.ts:
  BASE_COMPACT_PROMPT      — 9-section detailed summary request
  PARTIAL_COMPACT_PROMPT   — same for partial/reactive compaction
  format_compact_summary() — strip <analysis> scratchpad, keep <summary>

The prompts instruct the summarizer to:
  1. Write an <analysis> thinking block (stripped before insertion)
  2. Write a <summary> block (the actual context replacement)

The 9 required sections are identical to production so the resulting
summaries can be fed back to a real Claude model unchanged.
"""
from __future__ import annotations

import re

# ── Analysis preamble (scratchpad, not inserted into context) ─────────────────

_ANALYSIS_INSTRUCTION = """\
Before providing your final summary, wrap your analysis in <analysis> tags \
to organize your thoughts and ensure you have covered all necessary points. \
In your analysis:
1. Chronologically analyze each message and section of the conversation.
2. For each section identify:
   - The user's explicit requests and intents
   - Your approach and key decisions
   - Specific details: file names, code snippets, function signatures
   - Errors encountered and how they were fixed
   - User feedback and corrections
3. Double-check for technical accuracy and completeness.\
"""

# ── Base compact prompt (mirrors BASE_COMPACT_PROMPT) ─────────────────────────

BASE_COMPACT_PROMPT = f"""\
CRITICAL: Respond with TEXT ONLY. Do NOT call any tools.

Your task is to create a detailed summary of the conversation so far, paying \
close attention to the user's explicit requests and your previous actions. \
This summary should be thorough in capturing technical details, code patterns, \
and architectural decisions that would be essential for continuing development \
work without losing context.

{_ANALYSIS_INSTRUCTION}

Your summary should include the following sections inside <summary> tags:

1. Primary Request and Intent: Capture all of the user's explicit requests and intents in detail.
2. Key Technical Concepts: List all important technical concepts, technologies, and frameworks discussed.
3. Files and Code Sections: Enumerate specific files and code sections examined, modified, or created. \
Include full code snippets where applicable and explain why each is important.
4. Errors and Fixes: List all errors encountered and how they were fixed. Include user feedback and corrections.
5. Problem Solving: Document problems solved and any ongoing troubleshooting efforts.
6. All User Messages: List ALL user messages that are not tool results.
7. Pending Tasks: Outline any pending tasks you have been explicitly asked to work on.
8. Current Work: Describe precisely what was being worked on immediately before this summary request. \
Include file names and code snippets where applicable.
9. Optional Next Step: List the next step directly in line with the most recent user request. \
Include direct quotes from the most recent conversation.

Structure your output as:
<analysis>
[your thinking]
</analysis>

<summary>
1. Primary Request and Intent:
   [details]

2. Key Technical Concepts:
   - [concept]

3. Files and Code Sections:
   - [filename]: [why important]
     [snippet if relevant]

4. Errors and Fixes:
   - [error]: [fix]

5. Problem Solving:
   [description]

6. All User Messages:
   - [user message]

7. Pending Tasks:
   - [task]

8. Current Work:
   [precise description]

9. Optional Next Step:
   [next step with quote]
</summary>
"""

# ── Partial compact prompt (mirrors PARTIAL_COMPACT_PROMPT) ───────────────────
# Used when only the recent tail is being summarized and earlier messages are kept.

PARTIAL_COMPACT_PROMPT = f"""\
CRITICAL: Respond with TEXT ONLY. Do NOT call any tools.

Your task is to create a detailed summary of the RECENT portion of the \
conversation — the messages that follow earlier retained context. The earlier \
messages are being kept intact and do NOT need to be summarized. Focus only \
on what was discussed, learned, and accomplished in the recent messages.

{_ANALYSIS_INSTRUCTION}

Your summary should include the following sections inside <summary> tags:

1. Primary Request and Intent: Capture the user's explicit requests from the recent messages.
2. Key Technical Concepts: List important concepts discussed recently.
3. Files and Code Sections: Files/code examined, modified, or created recently.
4. Errors and Fixes: Errors encountered and how they were fixed.
5. Problem Solving: Problems solved and ongoing troubleshooting.
6. All User Messages: All user messages from the recent portion.
7. Pending Tasks: Pending tasks from recent messages.
8. Current Work: Precisely what was being worked on immediately before this summary request.
9. Optional Next Step: Next step with direct quotes from the recent conversation.

<analysis>
[your thinking]
</analysis>

<summary>
[same 9-section structure as above]
</summary>
"""


# ── Summary extraction ─────────────────────────────────────────────────────────

def format_compact_summary(raw_response: str) -> str:
    """
    Extract the content of the <summary> block from the LLM response,
    stripping the <analysis> scratchpad.

    Mirrors formatCompactSummary() in prompt.ts.

    Falls back to the full response if no <summary> tags are found
    (graceful degradation for mock LLMs that don't follow the format).
    """
    m = re.search(r"<summary>(.*?)</summary>", raw_response, re.DOTALL)
    if m:
        return m.group(1).strip()
    # Fallback: strip <analysis> block if present, return the rest
    stripped = re.sub(r"<analysis>.*?</analysis>", "", raw_response, flags=re.DOTALL)
    return stripped.strip() or raw_response.strip()


def get_compact_prompt(custom_instructions: str | None = None) -> str:
    """
    Return the compact user-turn prompt, optionally appending custom instructions.
    Mirrors getCompactPrompt() in prompt.ts.
    """
    prompt = BASE_COMPACT_PROMPT
    if custom_instructions:
        prompt += (
            f"\n\n## Additional Instructions\n{custom_instructions}"
        )
    return prompt


def get_compact_user_summary_message(summary: str) -> str:
    """
    Wrap a raw summary string in a user-turn that re-establishes context
    after compaction.  Mirrors getCompactUserSummaryMessage() in prompt.ts.
    """
    return (
        "<task>\nThis session is being continued from a previous conversation "
        "that ran out of context. The summary of the previous session is provided "
        "below.\n</task>\n\n<previous_session_summary>\n"
        + summary
        + "\n</previous_session_summary>"
    )

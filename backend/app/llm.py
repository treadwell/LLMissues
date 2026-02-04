import json
from dataclasses import dataclass
from typing import Any

from openai import OpenAI

MODEL = "gpt-5.2"


@dataclass
class LLMResult:
    new_issues: list[dict[str, Any]]
    updates: list[dict[str, Any]]


def _schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "new_issues": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "title": {"type": "string"},
                        "domain": {"type": "string"},
                        "confidence": {"type": "number"},
                        "situation": {"type": "string"},
                        "complication": {"type": "string"},
                        "resolution": {"type": "string"},
                        "next_steps": {"type": "string"},
                        "document_ids": {"type": "array", "items": {"type": "integer"}},
                    },
                    "required": [
                        "title",
                        "domain",
                        "confidence",
                        "situation",
                        "complication",
                        "resolution",
                        "next_steps",
                        "document_ids",
                    ],
                },
            },
            "updates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "issue_id": {"type": "integer"},
                        "title": {"type": "string"},
                        "domain": {"type": "string"},
                        "status": {"type": "string"},
                        "confidence": {"type": "number"},
                        "situation_delta": {"type": "string"},
                        "complication_delta": {"type": "string"},
                        "resolution_delta": {"type": "string"},
                        "next_steps_delta": {"type": "string"},
                        "document_ids": {"type": "array", "items": {"type": "integer"}},
                    },
                    "required": [
                        "issue_id",
                        "title",
                        "domain",
                        "status",
                        "confidence",
                        "situation_delta",
                        "complication_delta",
                        "resolution_delta",
                        "next_steps_delta",
                        "document_ids",
                    ],
                },
            },
        },
        "required": ["new_issues", "updates"],
    }


def extract_issues(
    meeting_date: str,
    documents: list[dict[str, Any]],
    existing_issues: list[dict[str, Any]],
) -> LLMResult:
    client = OpenAI()

    instructions = (
        "You analyze meeting transcripts and update an issue register using the SCR framework. "
        "You must return JSON that exactly matches the schema."
    )

    doc_blocks = []
    for doc in documents:
        doc_blocks.append(
            f"Document {doc['id']}: {doc['title']}\n"
            f"Path: {doc['path']}\n"
            f"Transcript:\n{doc['text']}"
        )

    issue_blocks = []
    for issue in existing_issues:
        issue_blocks.append(
            "Issue {id}: {title}\n"
            "Domain: {domain} | Status: {status} | Confidence: {confidence}\n"
            "Situation: {situation}\n"
            "Complication: {complication}\n"
            "Resolution: {resolution}\n"
            "Next steps: {next_steps}\n"
            "Suggested next steps: {suggested_next_steps}\n".format(**issue)
        )

    user_input = (
        f"Meeting date: {meeting_date}\n\n"
        "Existing issues:\n"
        f"{''.join(issue_blocks)}\n"
        "Documents:\n"
        f"{''.join(doc_blocks)}\n\n"
        "Tasks:\n"
        "1) Identify new issues that are not covered by existing issues.\n"
        "2) For existing issues, provide deltas to add to SCR and next steps.\n"
        "3) For updates, choose the best matching issue_id.\n"
        "4) Use document_ids from the documents list for evidence.\n"
        "5) Be conservative: only create issues if clearly distinct.\n"
        "6) Provide confidence from 0 to 1.\n"
    )

    tool_schema = _schema()
    tools = [
        {
            "type": "function",
            "function": {
                "name": "issue_extraction",
                "description": "Extract new issues and updates with SCR deltas.",
                "parameters": tool_schema,
                "strict": True,
            },
        }
    ]

    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": instructions},
            {"role": "user", "content": user_input},
        ],
        tools=tools,
        tool_choice={"type": "function", "function": {"name": "issue_extraction"}},
        temperature=0.2,
    )

    message = response.choices[0].message
    if getattr(message, "refusal", None):
        raise RuntimeError(f"Model refused: {message.refusal}")

    if message.tool_calls:
        arguments = message.tool_calls[0].function.arguments
        payload = json.loads(arguments)
    else:
        if not message.content:
            raise RuntimeError("No tool call or content returned from model")
        payload = json.loads(message.content)
    return LLMResult(new_issues=payload["new_issues"], updates=payload["updates"])

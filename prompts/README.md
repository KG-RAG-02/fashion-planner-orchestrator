# P08 query-structuring prompts

These prompts turn a user's product-planning text into a validated query for Trend Agent. They are used by P08's `query/structured.py`; the active prompt is **v6**, selected by `STRUCTURED_PROMPT_VERSION` and `configs/query_structuring.json`. Source files live under `query_structuring/v6/`; `rendered_system.md` is generated for review by `scripts/render_query_prompt.py`.

Versions v2–v5 are retained as historical evaluation inputs. They do not select a workflow execution mode. The former P08-owned text and image generation prompts are no longer part of the current workflow; New Product Agent owns Draft generation, and P08 composes the Planning Sheet locally.

A prompt change should use a new version directory, update the active version constant, regenerate the rendered prompt, and update the prompt validation fixtures. Do not edit prior evaluation prompt contents in place.

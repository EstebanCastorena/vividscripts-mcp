"""Public MCP Prompt catalog for VividScripts (KAN-56 / Phase 2).

Exports :data:`PROMPT_INTERFACES`, a name-keyed dict of the
:class:`PromptInterface` declarations that the MCP server registers as
native MCP Prompts. Each interface documents *what* an AI prompt does,
*what context it needs*, and *what shape its output takes*. The prompt
**body** for *template* prompts is not stored here — Phase 1's design
refinement ([[MCP Phase 0 Notes]]) ruled that the templates encode
VividScripts' creative IP and stay in the private ``slide_editor``
repo. The body is served at ``prompts/get`` time by the backend's
:meth:`format_prompt`.

Documentation prompts (KAN-127) are the exception: they ship a public
runbook body inline on the interface (``PromptInterface.body``) and
the renderer returns it verbatim — no backend round-trip and no
``save_step_result`` suffix, because they produce no AI step output.

Phase 2 lays the interfaces + schemas + MCP wire. Phase 3 swaps the
backend from :class:`MockBackend` to :class:`VividScriptsAdapter` and
the real bodies start flowing — with no change to the public surface
defined here.
"""

from vividscripts_mcp.prompts.definitions import (
    PROMPT_INTERFACES,
    PromptInterface,
)

__all__ = ["PROMPT_INTERFACES", "PromptInterface"]

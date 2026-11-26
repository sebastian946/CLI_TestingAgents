"""Fake LLM shared by the tests. No real provider is ever called."""

from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel


class ScriptedModel:
    """A fake model that answers with one prepared response per request.

    Each response is the list the model "returns" (as the output tool call), or
    a string to answer with plain text instead. The last one is repeated.
    """

    def __init__(self, *responses) -> None:
        self.responses = responses
        self.requests: list = []
        self.model = FunctionModel(self._answer)

    def _answer(self, messages, info) -> ModelResponse:
        response = self.responses[min(len(self.requests), len(self.responses) - 1)]
        self.requests.append((messages, info))
        if isinstance(response, str):
            return ModelResponse(parts=[TextPart(response)])
        tool = info.output_tools[0].name
        return ModelResponse(parts=[ToolCallPart(tool, {"response": response})])

    @property
    def last_feedback(self) -> str:
        """What testwise told the model about its previous answer."""
        messages, _ = self.requests[-1]
        return str(messages[-1].parts[0].content)

    @property
    def first_prompt(self) -> str:
        """The user message of the first request: what the model was shown."""
        messages, _ = self.requests[0]
        return str(messages[0].parts[0].content)

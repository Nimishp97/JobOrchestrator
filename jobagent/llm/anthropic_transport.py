"""Transport backed by the official Anthropic SDK. The only module that imports `anthropic`."""

import anthropic

from jobagent.llm.transport import TransportError, TransportRequest, TransportResponse


class AnthropicTransport:
    def __init__(self, api_key: str) -> None:
        # max_retries=0: retrying is LLMClient's job, so there is exactly one retry layer.
        self._client = anthropic.Anthropic(api_key=api_key, max_retries=0)

    def send(self, request: TransportRequest) -> TransportResponse:
        kwargs: dict[str, object] = {}
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        try:
            response = self._client.messages.create(
                model=request.model,
                max_tokens=request.max_tokens,
                system=request.system,
                messages=[{"role": m.role, "content": m.content} for m in request.messages],
                output_config={
                    "format": {
                        "type": "json_schema",
                        "schema": anthropic.transform_schema(request.schema),
                    }
                },
                timeout=request.timeout_seconds,
                **kwargs,  # type: ignore[arg-type]
            )
        except anthropic.APIStatusError as exc:
            status = exc.status_code
            retry_after = _retry_after(exc.response.headers.get("retry-after"))
            raise TransportError(
                f"{type(exc).__name__} (HTTP {status})",
                retryable=status == 429 or status >= 500,
                status_code=status,
                retry_after=retry_after,
            ) from exc
        except (anthropic.APITimeoutError, anthropic.APIConnectionError) as exc:
            raise TransportError(type(exc).__name__, retryable=True) from exc

        usage = response.usage
        return TransportResponse(
            text="".join(b.text for b in response.content if b.type == "text"),
            stop_reason=response.stop_reason,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
        )


def _retry_after(value: str | None) -> float | None:
    try:
        return float(value) if value is not None else None
    except ValueError:  # HTTP-date form: ignore, fall back to exponential backoff
        return None

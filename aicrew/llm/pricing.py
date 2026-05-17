"""Static price table per model (USD per 1k tokens). Update as needed."""

from __future__ import annotations

PRICES: dict[str, tuple[float, float]] = {
    # model -> (input_per_1k, output_per_1k)
    "openai:gpt-4o": (0.005, 0.015),
    "openai:gpt-4o-mini": (0.00015, 0.0006),
    "openai:gpt-5": (0.01, 0.03),
    "openai:gpt-5.3": (0.01, 0.03),
    "mock:cheap": (0.0, 0.0),
    "mock:smart": (0.0, 0.0),
}


def estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    p_in, p_out = PRICES.get(model, (0.0, 0.0))
    return round((tokens_in / 1000.0) * p_in + (tokens_out / 1000.0) * p_out, 6)

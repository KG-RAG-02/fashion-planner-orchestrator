"""Run Stock's real HTTP API with deterministic rule mode for local E2E only."""

import functools

import uvicorn

import stock_agent.api as api


api.run_request = functools.partial(api.run_request, mode="rule")

if __name__ == "__main__":
    uvicorn.run(api.app, host="127.0.0.1", port=8002, log_level="warning")

"""Model calls through Portkey, with token logging and a per-run spending cap.

Every call is appended to logs/usage.jsonl (input, cached input, output tokens, cost)
so the tokenomics numbers in the report come from real measurements.
"""
import json
import os
import threading
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

ROOT = Path(__file__).resolve().parent.parent

# Look for .env in the repo folder first, then one level up (course requirement).
for env_path in (ROOT / ".env", ROOT.parent / ".env"):
    if env_path.exists():
        load_dotenv(env_path)
        break

MODEL = os.getenv("DMSIM_MODEL", "gpt-5.6-luna")
BASE_URL = "https://api.portkey.ai/v1"
PROVIDER = "openai"

# USD per 1M tokens. Update these if the course publishes official prices.
PRICE_INPUT = float(os.getenv("DMSIM_PRICE_INPUT", "1.25"))
PRICE_CACHED = float(os.getenv("DMSIM_PRICE_CACHED", "0.125"))
PRICE_OUTPUT = float(os.getenv("DMSIM_PRICE_OUTPUT", "10.0"))

LOG_PATH = ROOT / "logs" / "usage.jsonl"
_lock = threading.Lock()
_client = None


class BudgetExceeded(RuntimeError):
    """Raised when a run would spend more than its cap."""


def cost_of(inp, cached, out):
    return ((inp - cached) * PRICE_INPUT + cached * PRICE_CACHED + out * PRICE_OUTPUT) / 1e6


class Meter:
    """Tracks token use and cost for one user run, and enforces the cap."""

    def __init__(self, cap_usd=0.25, max_calls=30, label="run"):
        self.cap_usd = cap_usd
        self.max_calls = max_calls
        self.label = label
        self.calls = []

    @property
    def totals(self):
        t = {"calls": len(self.calls), "input": 0, "cached": 0, "output": 0, "cost": 0.0}
        for c in self.calls:
            for k in ("input", "cached", "output", "cost"):
                t[k] += c[k]
        return t

    def check(self):
        t = self.totals
        if t["calls"] >= self.max_calls:
            raise BudgetExceeded(f"Call limit reached ({self.max_calls} model calls this run).")
        if t["cost"] >= self.cap_usd:
            raise BudgetExceeded(f"Spending cap reached ({t['cost']:.4f} of {self.cap_usd:.3f} USD).")


def client():
    global _client
    if _client is None:
        key = os.getenv("PORTKEY_API_KEY")
        if not key:
            raise RuntimeError("PORTKEY_API_KEY is not set (looked in .env here and one folder up).")
        _client = OpenAI(
            api_key="unused",
            base_url=BASE_URL,
            default_headers={"x-portkey-api-key": key, "x-portkey-provider": PROVIDER},
        )
    return _client


def chat_json(agent, system, user, meter=None, retries=2):
    """Call the model and parse a JSON object reply. Logs usage for every attempt."""
    if meter:
        meter.check()
    last_err = None
    for _ in range(retries + 1):
        t0 = time.time()
        resp = client().chat.completions.create(
            model=MODEL,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            response_format={"type": "json_object"},
        )
        u = resp.usage
        cached = getattr(getattr(u, "prompt_tokens_details", None), "cached_tokens", 0) or 0
        rec = {
            "ts": round(t0, 2),
            "agent": agent,
            "model": MODEL,
            "input": u.prompt_tokens,
            "cached": cached,
            "output": u.completion_tokens,
            "cost": cost_of(u.prompt_tokens, cached, u.completion_tokens),
            "seconds": round(time.time() - t0, 2),
            "run": meter.label if meter else "offline",
        }
        _log(rec)
        if meter:
            meter.calls.append(rec)
        try:
            return json.loads(resp.choices[0].message.content)
        except (json.JSONDecodeError, TypeError) as e:
            last_err = e
    raise RuntimeError(f"{agent}: model did not return valid JSON ({last_err})")


def _log(rec):
    LOG_PATH.parent.mkdir(exist_ok=True)
    with _lock, LOG_PATH.open("a") as f:
        f.write(json.dumps(rec) + "\n")

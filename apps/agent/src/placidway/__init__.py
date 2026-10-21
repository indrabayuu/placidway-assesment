"""PlacidWay assessment application."""

import os

# The user's reporting choice is local-only, regardless of ambient SDK defaults.
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["DEEPEVAL_TELEMETRY_OPT_OUT"] = "YES"
# Compose exports empty env_file values, while DeepEval validates this URL at
# import time. Apply our documented empty-base default before importing its SDK.
if "OPENROUTER_BASE_URL" in os.environ and not os.environ["OPENROUTER_BASE_URL"]:
    os.environ["OPENROUTER_BASE_URL"] = "https://openrouter.ai/api/v1"

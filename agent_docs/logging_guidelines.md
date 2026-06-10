# 🚨 Logging
Uses [logfire](https://logfire.pydantic.dev/) for structured logging and tracing, with [OpenObserve](https://openobserve.ai/) as the self-hosted OTEL backend and frontend dashboard.

Logs can alternatively be sent to Logfire's cloud service by providing a `LOGFIRE_TOKEN`.

If neither OTEL_EXPORTER_OTLP_TRACES_ENDPOINT nor LOGFIRE_TOKEN is set, then logs will go to stdout and print normally in the console.

## Logging Config
```python
import os
import base64

if os.getenv("USE_OPEN_OBSERVE").lower() == "true":
    credentials = base64.b64encode(f"{os.getenv('ZO_ROOT_USER_EMAIL')}:{os.getenv('ZO_ROOT_USER_PASSWORD')}".encode()).decode()
    os.environ["OTEL_EXPORTER_OTLP_TRACES_HEADERS"] = f"Authorization=Basic {credentials}"
else:
    os.environ["OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"] = ""

import logfire
import logging

def get_logger(service_name, name: str) -> logging.Logger:
    """Return a logger wired to Logfire.

    service_name: Set once at the top of main.py — used to tag all traces and
        logs with a dynamic identifier (e.g. the job name, environment, or run
        ID) so you can filter across services in the Logfire/OpenObserve UI.

    name: The Python module name, typically just pass __name__ here.
    """
    logfire.configure(
        service_name=service_name,
        send_to_logfire='if-token-present',
    )

    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    logger.propagate = False
    logger.addHandler(logfire.LogfireLoggingHandler())
    return logger
```
## .env file
```
# to use langgraph with logfire & openobserve these variables must be set to true.
LANGSMITH_OTEL_ENABLED=true
LANGSMITH_OTEL_ONLY=true
LANGSMITH_TRACING=true

# ── Secrets (pushed to GCP Secret Manager, mounted via --set-secrets) ────────
# Must be listed in the SECRETS dict in deploy.py.
# Leave blank if secret already exists in Secret Manager.
# Set a value to create/update the secret on next deploy.
ZO_ROOT_USER_EMAIL=youremail@example.com
ZO_ROOT_USER_PASSWORD=yourcomplexpassword
# putting USE_OPEN_OBSERVE and OTEL_EXPORTER_OTLP_TRACES_ENDPOINT as secrets
# allows you to switch easily in GCP without redeploying.
USE_OPEN_OBSERVE=true
OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://localhost:5080/api/default/v1/traces
# alternatively set logfire token here to use logfire cloud instead of open observe
LOGFIRE_TOKEN=
```

## In your code
```python
import os
from logging_config import get_logger
logger = get_logger('tarriff_classifier', __name__)  # call this before any other imports that use logging

## REST OF YOUR CODE HERE
```

For additional details on deploying OpenObserve, local development setup, and the full code walkthrough, see the [Observability with Logfire and OpenObserve](https://severalx.atlassian.net/wiki/spaces/SXKB/pages/31195138/Observability+with+Logfire+and+OpenObserve) article in the Several X Knowledge Base.

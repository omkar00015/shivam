"""Launch the Prasad dashboard API server.

Runs the FastAPI app defined in src/api/dashboard_api.py on port 8000.
Keep this process running alongside run_paper_trader.py.

Usage:
    python scripts/run_dashboard.py

Then open http://localhost:8000/api/health    (API health check)
     open http://localhost:8000/docs         (Swagger UI)
     open dashboard/index.html in a browser  (Trading dashboard)

The dashboard HTML polls the API every 15 seconds.  For the state to
update, run_paper_trader.py must also be running (it calls update_state()).
"""

import pathlib
import sys

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import uvicorn
from src.api.dashboard_api import app

if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info",
    )

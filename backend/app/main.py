# api for devices and operators, plus the dashboard
import asyncio
import hmac
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.staticfiles import StaticFiles

from .config import Settings
from .db import Database
from .schemas import BinIn, BinPatch, ReadingIn
from .service import BinService, CooldownError, UnknownBin

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
# the dashboard lives in the top-level frontend/ folder (Docker sets FRONTEND_DIR)
STATIC_DIR = os.path.abspath(os.getenv("FRONTEND_DIR") or os.path.join(os.path.dirname(__file__), "..", "..", "frontend"))


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    cfg = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = Database(cfg.db_path)
        app.state.settings = cfg
        app.state.db = db
        app.state.service = BinService(db, cfg)

        async def watchdog():
            while True:
                await asyncio.sleep(30)
                try:
                    app.state.service.check_offline()
                except Exception:
                    logging.exception("watchdog failed")

        task = asyncio.create_task(watchdog())
        yield
        task.cancel()
        db.close()

    app = FastAPI(title="SmartBin API", version="1.0.0",
                  description="Event-driven fill monitoring for public-market bins", lifespan=lifespan)

    # auth
    def device_auth(request: Request, x_api_key: Optional[str] = Header(default=None)) -> None:
        if not x_api_key or not hmac.compare_digest(x_api_key, request.app.state.settings.api_key):
            raise HTTPException(401, "Invalid or missing X-API-Key")

    def admin_auth(request: Request, x_admin_token: Optional[str] = Header(default=None)) -> None:
        expected = request.app.state.settings.admin_token
        if expected and not (x_admin_token and hmac.compare_digest(x_admin_token, expected)):
            raise HTTPException(401, "Invalid or missing X-Admin-Token")

    # dashboard routes are open unless PROTECT_DASHBOARD is on
    def dash_auth(request: Request, x_admin_token: Optional[str] = Header(default=None),
                  x_api_key: Optional[str] = Header(default=None)) -> None:
        cfg = request.app.state.settings
        if not cfg.protect_dashboard:
            return
        ok_admin = bool(cfg.admin_token and x_admin_token and hmac.compare_digest(x_admin_token, cfg.admin_token))
        ok_key = bool(x_api_key and hmac.compare_digest(x_api_key, cfg.api_key))
        if not (ok_admin or ok_key):
            raise HTTPException(401, "Dashboard is protected: enter the admin token (X-Admin-Token)")

    def svc(request: Request) -> BinService:
        return request.app.state.service

    # system
    @app.get("/health", tags=["system"])
    def health():
        return {"status": "ok"}

    @app.get("/api/config", tags=["system"])
    def config(request: Request):
        s = request.app.state.settings
        return {"admin_required": bool(s.admin_token),
                "dashboard_protected": s.protect_dashboard,
                "alert_pct": s.alert_pct, "full_pct": s.full_pct, "dead_zone_cm": s.dead_zone_cm,
                "refresh_cooldown_sec": s.refresh_cooldown_sec, "command_ttl_sec": s.command_ttl_sec}

    # devices
    @app.post("/api/readings", tags=["devices"], dependencies=[Depends(device_auth)])
    def post_reading(body: ReadingIn, service: BinService = Depends(svc)):
        return service.ingest(body.bin_id, body.distance_cm, body.trigger, body.lid_opens,
                              body.battery_pct, body.command_id, body.timestamp)

    # long poll, the device waits here for a refresh request
    @app.get("/api/bins/{bin_id}/commands/next", tags=["devices"], dependencies=[Depends(device_auth)])
    async def next_command(bin_id: str, wait: int = Query(0, ge=0, le=30), service: BinService = Depends(svc)):
        deadline = time.monotonic() + wait
        while True:
            try:
                cmd = service.next_command(bin_id)
            except UnknownBin:
                raise HTTPException(404, "Unknown bin")
            if cmd or time.monotonic() >= deadline:
                return {"command": cmd}
            await asyncio.sleep(0.5)

    # dashboard
    @app.get("/api/summary", tags=["dashboard"], dependencies=[Depends(dash_auth)])
    def summary(service: BinService = Depends(svc)):
        return service.summary()

    @app.get("/api/bins", tags=["dashboard"], dependencies=[Depends(dash_auth)])
    def list_bins(service: BinService = Depends(svc)):
        return service.bins()

    @app.get("/api/priority", tags=["dashboard"], dependencies=[Depends(dash_auth)])
    def priority(service: BinService = Depends(svc)):
        return service.priority()

    @app.get("/api/bins/{bin_id}/readings", tags=["dashboard"], dependencies=[Depends(dash_auth)])
    def bin_readings(bin_id: str, limit: int = Query(150, ge=1, le=2000), service: BinService = Depends(svc)):
        if not service.get_bin(bin_id):
            raise HTTPException(404, "Unknown bin")
        return service.readings(bin_id, limit)

    # ask the bin for a fresh reading, one per cooldown
    @app.post("/api/bins/{bin_id}/refresh", tags=["dashboard"], dependencies=[Depends(dash_auth)])
    def refresh(bin_id: str, service: BinService = Depends(svc)):
        try:
            cmd = service.refresh(bin_id)
        except UnknownBin:
            raise HTTPException(404, "Unknown bin")
        except CooldownError as exc:
            raise HTTPException(429, f"Please wait {exc.retry_after} s before refreshing this bin again",
                                headers={"Retry-After": str(exc.retry_after)})
        return {"command_id": cmd["id"], "status": cmd["status"]}

    @app.get("/api/commands/{command_id}", tags=["dashboard"], dependencies=[Depends(dash_auth)])
    def command_status(command_id: int, service: BinService = Depends(svc)):
        cmd = service.command(command_id)
        if not cmd:
            raise HTTPException(404, "Unknown command")
        return {"command_id": cmd["id"], "bin_id": cmd["bin_id"], "status": cmd["status"],
                "fill_pct": cmd["result_fill"], "completed_at": cmd["completed_at"]}

    @app.get("/api/alerts", tags=["dashboard"], dependencies=[Depends(dash_auth)])
    def alerts(active_only: bool = False, limit: int = Query(50, ge=1, le=500),
               service: BinService = Depends(svc)):
        return service.alerts(active_only, limit)

    # operator
    @app.post("/api/bins", tags=["operator"], dependencies=[Depends(admin_auth)], status_code=201)
    def create_bin(body: BinIn, service: BinService = Depends(svc)):
        try:
            return service.create_bin(body.id, body.name, body.location, body.depth_cm)
        except ValueError as exc:
            raise HTTPException(409, str(exc))

    @app.patch("/api/bins/{bin_id}", tags=["operator"], dependencies=[Depends(admin_auth)])
    def patch_bin(bin_id: str, body: BinPatch, service: BinService = Depends(svc)):
        updated = service.update_bin(bin_id, body.model_dump())
        if not updated:
            raise HTTPException(404, "Unknown bin")
        return updated

    @app.post("/api/alerts/{alert_id}/ack", tags=["operator"], dependencies=[Depends(admin_auth)])
    def ack(alert_id: int, service: BinService = Depends(svc)):
        result = service.acknowledge(alert_id)
        if result is None:
            raise HTTPException(404, "Alert not found")
        return result

    @app.post("/api/alerts/{alert_id}/resolve", tags=["operator"], dependencies=[Depends(admin_auth)])
    def resolve(alert_id: int, service: BinService = Depends(svc)):
        result = service.resolve(alert_id)
        if result is None:
            raise HTTPException(404, "Alert not found")
        return result

    if os.path.isdir(STATIC_DIR):
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="dashboard")
    return app


app = create_app()

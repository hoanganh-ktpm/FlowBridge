"""Own only the native processes started for an explicit Supabase test run."""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
from dotenv import dotenv_values
from sqlalchemy import text
from sqlalchemy.engine import make_url

from scripts.e2e_environment import ROOT, RUNTIME, engine_for


class NativeRuntime:
    def __init__(self, directory, *, api_host="127.0.0.1"):
        if api_host not in {"127.0.0.1", "0.0.0.0"}:
            raise ValueError("Use a local integration bind address")
        self.api_host = api_host
        self.directory = Path(directory).resolve()
        assert self.directory.is_relative_to((RUNTIME / "runs").resolve())
        self.ledger = self.directory / "resources.json"
        self.resources = json.loads(self.ledger.read_text())
        assert not self.resources.get("cleaned"), "Prepare a new run after cleanup"
        self.env = {**os.environ, **dotenv_values(self.directory / "core.env")}
        self.processes = {}
        self.logs = {}
        self.base_url = "http://127.0.0.1:18000"

    def evidence(self, key, value):
        self.resources["evidence"][key] = value
        self.save()

    def save(self):
        self.ledger.write_text(json.dumps(self.resources, indent=2), encoding="utf-8")

    def application(self, identifier):
        owned = {item["application_id"] for item in self.resources.get("imports", {}).values()}
        assert identifier in owned
        engine = engine_for(
            make_url(self.env["FLOWBRIDGE_CORE_DATABASE_URL"]).set(drivername="postgresql+psycopg")
        )
        try:
            with engine.connect() as connection:
                row = (
                    connection.execute(
                        text("SELECT id, app_key, revision, status FROM applications WHERE id=:id"),
                        {"id": identifier},
                    )
                    .mappings()
                    .one()
                )
                return {**row, "id": str(row["id"])}
        finally:
            engine.dispose()

    def source_job(self, identifier):
        assert identifier in {
            item
            for imported in self.resources["imports"].values()
            for item in imported["data_source_ids"]
        }
        engine = engine_for(
            make_url(self.env["FLOWBRIDGE_CORE_DATABASE_URL"]).set(drivername="postgresql+psycopg")
        )
        try:
            with engine.connect() as connection:
                identifier = connection.scalar(
                    text(
                        "SELECT id FROM publication_jobs WHERE data_source_id=:id "
                        "AND job_type='CONNECT_SOURCE' ORDER BY created_at DESC LIMIT 1"
                    ),
                    {"id": identifier},
                )
                assert identifier
                return {"job_id": str(identifier)}
        finally:
            engine.dispose()

    def start(self, name):
        assert name in {"api", "worker", "worker2"}
        assert name not in self.processes
        if name == "api":
            with socket.socket() as probe:
                probe.settimeout(1)
                assert probe.connect_ex(("127.0.0.1", 18000)) != 0, "Test API port is occupied"
            arguments = [
                "-m",
                "uvicorn",
                "app.core.runtime:create_app",
                "--factory",
                "--host",
                self.api_host,
                "--port",
                "18000",
            ]
        else:
            arguments = ["-m", "app.core.runtime", "worker"]
        log = (self.directory / (name + "-" + str(time.time_ns()) + ".log")).open("wb")
        self.logs[name] = log
        self.processes[name] = subprocess.Popen(
            [sys.executable, *arguments],
            cwd=ROOT,
            env=self.env,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if name == "api":
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                assert self.processes[name].poll() is None, "Native API failed; inspect private log"
                try:
                    response = httpx.get(self.base_url + "/openapi.json", timeout=1)
                    if response.status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                time.sleep(0.2)
            raise RuntimeError("Native API readiness timeout")

    def stop(self, name):
        process = self.processes.pop(name, None)
        if process is not None:
            if os.name == "nt" and process.poll() is None:
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            else:
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            self.logs.pop(name).close()

    def restart(self, name):
        self.stop(name)
        self.start(name)

    def close(self):
        for name in list(self.processes):
            self.stop(name)

    def hasura_fault(self, paused):
        container = "flowbridge-integration-hasura-1"
        label = subprocess.check_output(
            [
                "docker",
                "inspect",
                "--format",
                '{{index .Config.Labels "com.docker.compose.project"}}',
                container,
            ],
            text=True,
        ).strip()
        assert label == "flowbridge-integration"
        subprocess.run(
            ["docker", "pause" if paused else "unpause", container],
            check=True,
            stdout=subprocess.DEVNULL,
        )

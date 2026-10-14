"""Background jobs for long-running work (simulations, large analyses).

Jobs run detached from the agent in their own process session, so Ctrl+C in
the agent does not reach them. Output goes to a log file, and jobs are tracked
in ``<project>/jobs/jobs.json`` so they survive agent restarts.

Design rule: the agent may monitor a job, but it may NOT stop a job that is
still producing output or using CPU. Only the user can (``/kill``).

Process inspection uses ``/proc`` and therefore requires Linux (WSL2 works).
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from research_agent.store import load_json, now_iso, save_json

# Output newer than this counts as "producing output".
ACTIVE_OUTPUT_SECONDS = 300
# The agent may only stop a job after this long with no output and no CPU use.
IDLE_SECONDS_BEFORE_AGENT_STOP = 1800
MAX_WAIT_SECONDS = 1800
POLL_SECONDS = 10
TAIL_BYTES = 64_000

KILL_PATTERN = re.compile(
    r"\bpkill\b|\bkillall\b|\bkill\s+-|os\.kill\(|os\.killpg\(|SIGKILL|SIGTERM"
)


def tail_text(path: str | Path, lines: int = 30) -> str:
    path = Path(path)
    if not path.exists():
        return ""
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        handle.seek(max(0, handle.tell() - TAIL_BYTES))
        data = handle.read().decode("utf-8", errors="replace")
    return "\n".join(data.splitlines()[-lines:])


def session_processes(sid: int) -> list[tuple[int, float, str]]:
    """Return ``(pid, cpu_seconds, state)`` for every live process of a job.

    Jobs are started with ``start_new_session=True``, so the session id is the
    wrapper's pid. Descendants that moved to another session (e.g. MPI
    daemons) are included through the parent-pid tree.
    """
    clk_tck = os.sysconf("SC_CLK_TCK")
    table: dict[int, tuple[str, int, int, float]] = {}
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            text = Path(f"/proc/{entry}/stat").read_text()
        except OSError:
            continue
        # The command name may contain spaces; fields start after the last ')'.
        fields = text[text.rfind(")") + 2 :].split()
        state, ppid, session = fields[0], int(fields[1]), int(fields[3])
        cpu = (int(fields[11]) + int(fields[12])) / clk_tck
        table[int(entry)] = (state, ppid, session, cpu)

    members = {pid for pid, row in table.items() if row[2] == sid}
    changed = True
    while changed:
        changed = False
        for pid, row in table.items():
            if pid not in members and row[1] in members:
                members.add(pid)
                changed = True

    return [(pid, table[pid][3], table[pid][0]) for pid in members if table[pid][0] != "Z"]


def session_cpu(sid: int) -> tuple[float, int]:
    procs = session_processes(sid)
    return sum(p[1] for p in procs), len(procs)


class JobManager:
    """Start, monitor and stop background jobs of one project."""

    def __init__(self, project_dir: Path) -> None:
        self.jobs_dir = Path(project_dir) / "jobs"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.registry_path = self.jobs_dir / "jobs.json"

    # -- registry ---------------------------------------------------------

    def _load(self) -> dict[str, dict[str, Any]]:
        return load_json(self.registry_path, {})

    def _save(self, jobs: dict[str, dict[str, Any]]) -> None:
        save_json(self.registry_path, jobs)

    @staticmethod
    def _new_id(jobs: dict[str, Any]) -> str:
        n = len(jobs) + 1
        while f"job{n:03d}" in jobs:
            n += 1
        return f"job{n:03d}"

    # -- start ------------------------------------------------------------

    def start(
        self,
        command: str,
        cwd: str | Path,
        name: str | None = None,
        watch_files: list[str] | None = None,
        allow_duplicate: bool = False,
        job_id: str | None = None,
    ) -> dict[str, Any]:
        cwd = Path(cwd).expanduser().resolve()
        if not cwd.is_dir():
            return {"error": f"Working directory not found: {cwd}"}

        jobs = self._load()
        if not allow_duplicate:
            for other_id, job in jobs.items():
                if (
                    job.get("command") == command
                    and job.get("cwd") == str(cwd)
                    and self._is_alive(job)
                ):
                    return {
                        "status": "ALREADY_RUNNING",
                        "job_id": other_id,
                        "message": (
                            "An identical job is already running. Do NOT relaunch it; "
                            "monitor it with job_status."
                        ),
                    }

        job_id = job_id or self._new_id(jobs)
        job_dir = self.jobs_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        log_path = job_dir / "output.log"
        exit_path = job_dir / "exit_code"

        # Line-buffer the command's stdout so progress is visible in the log.
        stdbuf = shutil.which("stdbuf")
        prefix = f"{stdbuf} -oL -eL " if stdbuf else ""
        script = job_dir / "run.sh"
        script.write_text(
            "#!/bin/bash\n"
            f"cd {shlex.quote(str(cwd))}\n"
            "export PYTHONUNBUFFERED=1\n"
            f"{prefix}bash -c {shlex.quote(command)}\n"
            f"echo $? > {shlex.quote(str(exit_path))}\n",
            encoding="utf-8",
        )

        with log_path.open("ab") as log_file:
            proc = subprocess.Popen(
                ["bash", str(script)],
                cwd=str(cwd),
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )

        jobs[job_id] = {
            "name": name or command[:60],
            "command": command,
            "cwd": str(cwd),
            "pid": proc.pid,
            "log": str(log_path),
            "watch_files": [str((cwd / w).resolve()) for w in (watch_files or [])],
            "started": now_iso(),
            "started_ts": time.time(),
        }
        self._save(jobs)

        return {
            "status": "STARTED",
            "job_id": job_id,
            "pid": proc.pid,
            "log": str(log_path),
            "message": (
                "Job is running in the background. Monitor it with "
                "job_status(job_id, wait_seconds=...). Do not start it again and "
                "do not stop it while it makes progress."
            ),
        }

    # -- status -----------------------------------------------------------

    @staticmethod
    def _exit_code(job: dict[str, Any]) -> int | None:
        path = Path(job["log"]).parent / "exit_code"
        if not path.exists():
            return None
        try:
            return int(path.read_text().strip())
        except ValueError:
            return -1

    def _is_alive(self, job: dict[str, Any]) -> bool:
        return self._exit_code(job) is None and bool(session_processes(job["pid"]))

    @staticmethod
    def _last_output_age(job: dict[str, Any]) -> float | None:
        paths = [job["log"], *job.get("watch_files", [])]
        mtimes = [
            os.path.getmtime(p) for p in paths if os.path.exists(p) and os.path.getsize(p) > 0
        ]
        return time.time() - max(mtimes) if mtimes else None

    def status(self, job_id: str, wait_seconds: int = 0, tail_lines: int = 30) -> dict[str, Any]:
        jobs = self._load()
        if job_id not in jobs:
            return {"error": f"Unknown job: {job_id}"}
        job = jobs[job_id]

        wait_seconds = max(0, min(int(wait_seconds or 0), MAX_WAIT_SECONDS))
        # Measure CPU use over the wait (at least a short window), so a job
        # whose output is buffered is not mistaken for a stuck one.
        window = max(wait_seconds, 3)
        cpu0, _ = session_cpu(job["pid"])
        t0 = time.time()
        while time.time() - t0 < window and self._exit_code(job) is None:
            time.sleep(min(POLL_SECONDS, max(0.0, window - (time.time() - t0))))
        cpu1, nproc = session_cpu(job["pid"])
        cpu_delta = max(0.0, cpu1 - cpu0)

        exit_code = self._exit_code(job)
        output_age = self._last_output_age(job)
        if exit_code is not None:
            state = "FINISHED_OK" if exit_code == 0 else "FINISHED_ERROR"
            advice = "Job finished. Check the log and outputs before drawing conclusions."
        elif nproc == 0:
            state = "LOST"
            advice = (
                "No process is left and no exit code was written (machine restart or "
                "external kill). Tell the user."
            )
        elif output_age is not None and output_age < ACTIVE_OUTPUT_SECONDS:
            state = "RUNNING_PRODUCING_OUTPUT"
            advice = "Healthy. Do NOT stop, restart or relaunch it."
        elif cpu_delta > 0:
            state = "RUNNING_COMPUTING"
            advice = (
                "Processes are using CPU, so the job is computing; output is probably "
                "buffered or the step is slow. Do NOT stop, restart or relaunch it."
            )
        else:
            state = "RUNNING_IDLE"
            advice = "No CPU use and no recent output. Report this to the user; do not relaunch."

        jobs[job_id]["last_state"] = state
        self._save(jobs)

        result: dict[str, Any] = {
            "job_id": job_id,
            "name": job["name"],
            "state": state,
            "advice": advice,
            "exit_code": exit_code,
            "elapsed_seconds": round(time.time() - job["started_ts"]),
            "seconds_since_last_output": None if output_age is None else round(output_age),
            "cpu_seconds_in_window": round(cpu_delta, 1),
            "window_seconds": round(time.time() - t0),
            "processes": nproc,
            "log": job["log"],
            "log_tail": tail_text(job["log"], tail_lines),
        }
        for watched in job.get("watch_files", []):
            result.setdefault("watch_tails", {})[watched] = tail_text(watched, tail_lines)
        return result

    def list(self) -> list[dict[str, Any]]:
        out = []
        for job_id, job in self._load().items():
            exit_code = self._exit_code(job)
            if exit_code is not None:
                state = "FINISHED_OK" if exit_code == 0 else "FINISHED_ERROR"
            elif self._is_alive(job):
                state = "RUNNING"
            else:
                state = job.get("last_state") if job.get("stopped") else "LOST"
            out.append(
                {
                    "job_id": job_id,
                    "name": job["name"],
                    "state": state,
                    "started": job["started"],
                    "cwd": job["cwd"],
                }
            )
        return out

    # -- stop -------------------------------------------------------------

    def stop(self, job_id: str, reason: str = "", by_user: bool = False) -> dict[str, Any]:
        jobs = self._load()
        if job_id not in jobs:
            return {"error": f"Unknown job: {job_id}"}
        job = jobs[job_id]
        if not self._is_alive(job):
            return {"status": "NOT_RUNNING", "job_id": job_id}

        if not by_user:
            st = self.status(job_id)
            idle = st["seconds_since_last_output"]
            if idle is None:
                idle = st["elapsed_seconds"]
            if st["state"] != "RUNNING_IDLE" or idle < IDLE_SECONDS_BEFORE_AGENT_STOP:
                return {
                    "status": "REFUSED",
                    "job_id": job_id,
                    "state": st["state"],
                    "message": (
                        "This job is still making progress (or has not been idle long "
                        "enough). The agent is not allowed to stop it. Keep monitoring; "
                        f"only the user can stop it with /kill {job_id}."
                    ),
                }

        pids = [p[0] for p in session_processes(job["pid"])]
        for sig in (signal.SIGTERM, signal.SIGKILL):
            for pid in pids:
                try:
                    os.kill(pid, sig)
                except ProcessLookupError:
                    pass
            deadline = time.time() + 10
            while time.time() < deadline and session_processes(job["pid"]):
                time.sleep(0.5)
            if not session_processes(job["pid"]):
                break

        jobs[job_id]["stopped"] = {
            "by": "user" if by_user else "agent",
            "reason": reason,
            "at": now_iso(),
        }
        jobs[job_id]["last_state"] = "STOPPED"
        self._save(jobs)
        return {"status": "STOPPED", "job_id": job_id, "pids": pids}

    # -- python snippets --------------------------------------------------

    def run_python(self, code: str, cwd: Path, timeout: int = 120) -> dict[str, Any]:
        """Run a Python snippet.

        If it finishes within ``timeout`` its output is returned. Otherwise it
        is NOT killed: it keeps running as a background job and its id is
        returned.
        """
        if KILL_PATTERN.search(code):
            return {
                "error": "BLOCKED",
                "message": (
                    "Killing processes from run_python is not allowed. Use stop_job "
                    "(it refuses to stop jobs that are still making progress); the user "
                    "can use /kill."
                ),
            }

        Path(cwd).mkdir(parents=True, exist_ok=True)
        job_id = self._new_id(self._load())
        snippet_dir = self.jobs_dir / job_id
        snippet_dir.mkdir(parents=True, exist_ok=True)
        script = snippet_dir / "snippet.py"
        script.write_text(code, encoding="utf-8")

        command = f"{shlex.quote(sys.executable)} -u {shlex.quote(str(script))}"
        started = self.start(
            command, cwd, name=f"run_python {job_id}", allow_duplicate=True, job_id=job_id
        )
        if "error" in started:
            return started

        job = self._load()[job_id]
        deadline = time.time() + timeout
        while time.time() < deadline and self._exit_code(job) is None:
            time.sleep(0.2)

        exit_code = self._exit_code(job)
        if exit_code is None:
            return {
                "status": "STILL_RUNNING",
                "job_id": job_id,
                "message": (
                    f"Still running after {timeout} s. It was NOT killed and continues as "
                    f"a background job. Monitor it with job_status('{job_id}', "
                    "wait_seconds=...). Do not rerun the same code."
                ),
                "output_so_far": tail_text(job["log"], 40),
            }

        output = Path(job["log"]).read_text(encoding="utf-8", errors="replace")
        # Finished snippets are not kept as jobs.
        jobs = self._load()
        jobs.pop(job_id, None)
        self._save(jobs)
        shutil.rmtree(snippet_dir, ignore_errors=True)
        return {"returncode": exit_code, "output": output}

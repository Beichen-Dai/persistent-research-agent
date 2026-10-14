"""Core tools: Python execution, background jobs and saved findings."""

from __future__ import annotations

from typing import Any

from research_agent import workflow
from research_agent.jobs import JobManager
from research_agent.tools.registry import Tool, ToolContext, params

EVIDENCE_LABELS = ["PAPER", "DOC", "EXAMPLE", "NOTE", "DATA", "SOURCE", "LOCAL", "INFERENCE"]


def _jobs(ctx: ToolContext) -> JobManager:
    return JobManager(ctx.store.project_dir)


def run_python(ctx: ToolContext, args: dict[str, Any]) -> Any:
    # Not killed on timeout: the snippet continues as a background job.
    return _jobs(ctx).run_python(
        args["code"], ctx.config.workspace, timeout=ctx.config.agent.run_python_timeout
    )


def start_job(ctx: ToolContext, args: dict[str, Any]) -> Any:
    return _jobs(ctx).start(
        args["command"], args["cwd"], name=args.get("name"), watch_files=args.get("watch_files")
    )


def job_status(ctx: ToolContext, args: dict[str, Any]) -> Any:
    return _jobs(ctx).status(
        args["job_id"],
        wait_seconds=args.get("wait_seconds", 0),
        tail_lines=args.get("tail_lines", 30),
    )


def list_jobs(ctx: ToolContext, args: dict[str, Any]) -> Any:
    return _jobs(ctx).list()


def stop_job(ctx: ToolContext, args: dict[str, Any]) -> Any:
    return _jobs(ctx).stop(args["job_id"], reason=args.get("reason", ""), by_user=False)


def save_finding(ctx: ToolContext, args: dict[str, Any]) -> Any:
    item = workflow.add_finding(
        ctx.store.project_state,
        args["key"],
        args["value"],
        args["source"],
        citation=args.get("citation", ""),
        note=args.get("note", ""),
    )
    ctx.store.save()
    return {"saved": item}


CORE_TOOLS = [
    Tool(
        "run_python",
        "Run Python code locally for calculation or analysis. Results are [LOCAL]. "
        "Code still running after the timeout continues as a background job.",
        params({"code": {"type": "string", "description": "Python code to execute."}}, ["code"]),
        run_python,
    ),
    Tool(
        "start_job",
        "Start a long-running shell command (e.g. a simulation or an mpirun job) as a "
        "background job. Output is line-buffered into a log. An identical running job is "
        "not started again.",
        params(
            {
                "command": {"type": "string", "description": "Shell command to run."},
                "cwd": {"type": "string", "description": "Absolute working directory."},
                "name": {"type": "string", "description": "Short job name."},
                "watch_files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Files written by the job (relative to cwd) used to detect "
                    "progress, e.g. log.lammps.",
                },
            },
            ["command", "cwd"],
        ),
        start_job,
    ),
    Tool(
        "job_status",
        "Check a background job, optionally waiting up to wait_seconds (max 1800) or until it "
        "finishes. Returns its state, CPU use, time since last output and the log tail.",
        params(
            {
                "job_id": {"type": "string"},
                "wait_seconds": {"type": "integer", "default": 0},
                "tail_lines": {"type": "integer", "default": 30},
            },
            ["job_id"],
        ),
        job_status,
    ),
    Tool("list_jobs", "List the background jobs of this project.", params({}), list_jobs),
    Tool(
        "stop_job",
        "Request to stop a background job. Refused while the job produces output or uses CPU.",
        params({"job_id": {"type": "string"}, "reason": {"type": "string"}}, ["job_id", "reason"]),
        stop_job,
    ),
    Tool(
        "save_finding",
        "Save a parameter, target value or key result to the project so later work can use "
        "it. Saving the same key again replaces the value.",
        params(
            {
                "key": {"type": "string", "description": "e.g. 'timestep' or 'density_300K'."},
                "value": {"type": "string"},
                "source": {"type": "string", "enum": EVIDENCE_LABELS},
                "citation": {"type": "string", "description": "Document and page, or file."},
                "note": {"type": "string"},
            },
            ["key", "value", "source"],
        ),
        save_finding,
    ),
]

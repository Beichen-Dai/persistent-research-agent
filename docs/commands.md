# Commands

## Shell

```text
research-agent [-c CONFIG] [-p PROJECT] [-s SESSION]   interactive session (default)
research-agent init [DIR]                               write an example configuration
research-agent sources                                  list sources and their status
research-agent index [NAME ...] [--force] [--cards]     build/update indexes (and cards)
research-agent search QUERY [--source NAME] [--limit N] search without the model
```

## Inside a session

Anything that does not start with `/` is sent to the agent.

### Projects and sessions

| Command | Effect |
|---|---|
| `/status` | Project, session and memory overview |
| `/history [N]` | Last N messages |
| `/projects`, `/project NAME` | List projects, or switch to (or create) one |
| `/sessions`, `/session NAME` | List sessions, or switch to (or create) one |
| `/clear-session` | Archive and clear the current conversation |

### Memory and state

| Command | Effect |
|---|---|
| `/remember TEXT` | Add a user-confirmed global memory |
| `/memory` | Show all memories |
| `/state KEY VALUE` | Set a project-state value |
| `/findings` | Show findings saved with `save_finding` |

### Data sources and knowledge

| Command | Effect |
|---|---|
| `/sources` | Sources, capabilities, status and actions |
| `/index [NAME ...] [--force]` | Update indexes, then build missing knowledge cards |
| `/search [SOURCE:]QUERY` | Search directly, without the model |
| `/source NAME ACTION` | Source-specific action, e.g. `/source lammps cheatsheet` |
| `/cards [build\|rebuild]` | Card status, or build cards |
| `/card KEY ...` | Show cards, e.g. `/card papers:12` |
| `/core` | Show the knowledge sent with every request |

### Workflow phases

| Command | Effect |
|---|---|
| `/workflow reproduce` | Load the *literature → simulate → compare* preset |
| `/workflow clear` | Remove the workflow |
| `/phase [NAME]` | Show the workflow, or switch to (or create) a phase |
| `/next` | Move to the next phase |
| `/rule TEXT`, `/rules clear` | Add or clear rules of the current phase |
| `/tools on\|off NAME ...` | Enable or disable tools in the current phase |
| `/sources-phase on\|off NAME\|label:LABEL ...` | Enable or disable sources in the current phase |

### Background jobs

| Command | Effect |
|---|---|
| `/jobs` | List jobs |
| `/job ID` | Status and log tail |
| `/kill ID` | Stop a job. Only the user can stop a job that is still running |

### Input

A pasted multi-line prompt is sent as one message. To type one, start and end it with
a line containing only `"""`. Ctrl+C while the agent works interrupts it; the
conversation is kept and background jobs keep running. `/exit` quits.

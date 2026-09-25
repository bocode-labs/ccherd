---
name: ccherd
description: Use when a subagent should run on whichever of the user's Claude accounts has the most quota to spare, when work should be spread across several Claude accounts (separate CLAUDE_CONFIG_DIRs) to avoid extra usage, when a session has to find or message a Claude session running under another account (ListAgents only shows its own account), or when asked about "ccherd" (including installing or setting it up), quota per account, "which account has headroom", or "spawn an agent on another account".
---

# Agents across Claude accounts (`ccherd`)

Each Claude account is its own `CLAUDE_CONFIG_DIR`. `ListAgents` and `SendMessage`
only see sessions of the account they run under. `ccherd` sees all of them, and
starts subagents on the account whose included quota is most at risk of going
unused.

## First: check that ccherd is ready

Run `ccherd doctor` once before anything else in this session.

- **`command not found`**: ccherd is not installed. Tell the user. If they want
  you to install and set it up, follow the setup guide (fetch this raw URL, not
  the github.com page): https://raw.githubusercontent.com/bocode-labs/ccherd/main/docs/agent-setup.md
- **Exit code 3 / `not set up`**: ccherd is installed but not set up. The user
  can run `ccherd setup` in this repo, or ask you to - then follow the same guide.
- **Exit code 1**: some lines say `FAIL` (an account not logged in, `claude`
  missing). Tell the user what those lines say. Accounts marked `ok` still work.
- **Exit code 0**: ready.

## Commands

```bash
ccherd accounts [--model opus]        # 5h + weekly usage per account, score, and which one `auto` picks
ccherd sessions                       # live sessions of ALL accounts (account, name, status, uds: address, cwd)
ccherd spawn NAME "task" --model M    # background subagent on the best account; returns at once
      [--account LABEL] [--cwd DIR] [--effort E] [--permission-mode MODE]
ccherd agents [--all]                 # THIS session's subagents and their state
ccherd send NAME "text"               # to your subagent: running -> into its inbox, idle -> resumes it
ccherd send SESSION "text"            # to any live session of any account: its name or uds: address
ccherd result NAME                    # its last answer in full
ccherd log NAME [--tail N]            # readable transcript: turns, tool calls, answers
ccherd kill NAME                      # stop it and every process it started
```

## How to use it

- **Spawn and move on.** `spawn` returns immediately. When the subagent finishes a
  turn, a `<cross-session-message from="ccherd:NAME">` arrives in THIS session with its
  status and answer. Do not poll `ccherd agents` in a loop.
- **Talk to it the same way in every state.** `ccherd send NAME "..."` goes into its
  inbox while it runs and resumes it with its full earlier context once it is idle.
- **It can ask you.** A subagent runs `ccherd send --parent "question"`; that arrives
  here as `ccherd: subagent NAME asks: ...`. Answer with `ccherd send NAME "..."`.
- **Brief it completely.** It starts with none of this conversation. Say what to
  do, where (`--cwd`), what done means, and what to report back.
- **One writer per worktree.** Give each agent that edits files its own worktree
  via `--cwd`; read-only agents can share one.
- **The account is fixed per agent.** Its conversation lives in that account, so
  every resume runs there too. `auto` decides once, at spawn.

## Talking to other sessions

Use `ccherd sessions`, not `ListAgents`. Then `ccherd send "<name>" "..."` (or its
`uds:` address). Say who you are and what you need - the other session has none
of your context. Its answer arrives as `<cross-session-message from="uds:...">`;
reply with `ccherd send uds:... "..."`.

A receiver in a different permission class than yours (bypass vs. prompting)
holds the message until its user approves it. ccherd gets no receipt either way -
silence is not agreement.

## How `auto` picks

Unused weekly quota expires at the weekly reset, and usage beyond it is billed.
The score is **weekly % left / hours until the weekly reset**, times the free
share of the 5-hour window. Highest wins. An account at 90 % of its 5-hour window
or 98 % of its week is skipped; if all are, `spawn` refuses and says when the next
window frees. `--account LABEL` overrides.

## Permissions

A subagent runs in **your session's current permission mode**. `--permission-mode`
can only narrow it; a wider mode is refused, and so is steering an agent that is
allowed more than your session is now. Never send a subagent work your own
session was denied.

## When it does not work

- `every account is saturated`: wait until the time it names, or pass
  `--account LABEL` knowingly.
- Status `lost`: the supervisor died (reboot, OOM). `ccherd log NAME` shows how far
  it got; `ccherd send NAME "..."` resumes it.
- Status `failed`: the turn ended in an error. `ccherd log NAME` shows it.
- State lives in `~/.local/state/ccherd/<session-id>/<name>/`.

## Other Claude accounts: `ccherd`

Sessions here run under several Claude accounts. `ListAgents` and `SendMessage`
only see the current one, so:

- To find other sessions, run `ccherd sessions`, not `ListAgents`. To message one,
  `ccherd send "<name or uds: address>" "..."`.
- Start subagents with `ccherd spawn NAME "task" --model <model>` instead of the
  built-in Agent tool. It runs on the account whose weekly quota would otherwise
  expire unused, and reports back as a message when done.

Details: the `ccherd` skill. If `ccherd` is not installed or says `not set up`, tell
the user and use the built-in tools instead.

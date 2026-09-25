# Setting up ccherd as an agent

You are an agent, and your user asked you to set up ccherd. Your shell has no
terminal, so `ccherd setup` asks nothing: every answer is a flag. Some answers
are the user's to give. Ask them; do not guess.

## 1. Install

```sh
uv tool install ccherd      # or: pipx install ccherd
ccherd --version
```

If neither `uv` nor `pipx` exists, ask the user which one to install.

## 2. Look at what is there

```sh
ccherd setup --list
```

One line per Claude config dir (`~/.claude`, `~/.claude-*`): logged in or not,
its organization, the numbered family it belongs to, and which one this session
runs under (`<- current`). `--list --json` gives the same as a list of:

```json
{"dir": "~/.claude-work2", "logged_in": true, "organization": "Acme",
 "family": "~/.claude-work*", "current": false}
```

`organization` is `null` when it could not be checked, `family` is `null` for a
dir without numbered siblings.

## 3. Ask the user

Show them the list and ask:

1. **Which organization?** Only if the list shows more than one. ccherd uses the
   accounts of one organization; subagents then share the caller's skills and
   memories. Default: the organization of the `current` dir.
2. **Which dirs?** A family (`~/.claude-work*`) means all its numbered dirs,
   including ones created later. Single dirs are fine too.
3. **Where should the skill go?** `repo` (this repo's `.claude/skills`, shared via
   git), `home` (every account's config dir, all repos, only this user) or `none`.
4. **A note in `CLAUDE.local.md`?** A short git-ignored hint for sessions in this
   repo. Useful when the skill is not in the repo.

If the user has several subscriptions but only one config dir, go to step 6.

## 4. Run setup

In the repo where ccherd should be used:

```sh
ccherd setup --organization "<name>" --schema ~/.claude-work --skill repo --claude-local
```

| Flag | Answers |
| --- | --- |
| `--organization NAME` | which organization (name as in `--list`) |
| `--schema PATH` | a numbered family: `PATH`, `PATH2`, `PATH-3`, ... (repeatable) |
| `--dir PATH` | a single config dir (repeatable) |
| `--skill repo\|home\|none` | where the skill goes |
| `--claude-local` / `--no-claude-local` | the `CLAUDE.local.md` note |
| `--yes` | the default for everything not given |

If a flag is missing, setup changes nothing and names every missing flag.

Check the line `saved ...: <accounts>` against what the user wanted. A family
includes every numbered dir that exists, so it may be more than you expect.

## 5. Check and fix

Setup ends by running `ccherd doctor`, so its output is already above. Run
`ccherd doctor` again only after changing something.

Exit code 0: done. 1: some line says `FAIL`. 3: setup has not run.

- `skill ... installed twice`: it is in this repo and in the accounts' config
  dirs. Ask the user which one to keep and delete the other `skills/ccherd` dir.
- `not logged in` / `login expired`: the user has to log in; see step 6.
- `not linked` on `skills`, `projects`, `agents`, `plugins`, `settings.json` or
  `CLAUDE.md`: the accounts do not share them yet. Ask the user, then run
  `ccherd doctor --fix --yes`. It merges each account's own copy into one shared
  place and symlinks it; files that differ stay in a `*.ccherd-backup-*` dir,
  nothing is deleted. Accounts with a running Claude session are skipped - the
  user has to close those sessions first.

## 6. New accounts

If the user has N subscriptions but not a config dir for each:

```sh
ccherd setup --new N
```

This creates numbered dirs next to the current one and prints one login command
per dir (`CLAUDE_CONFIG_DIR=~/.claude-2 claude`). Logging in opens a browser, so
the user has to run those commands. Afterwards, start again at step 2.

## Done

`ccherd doctor` exits 0. From now on the `ccherd` skill tells sessions how to use
it. Something did not work as described here? Please open a PR or an issue at
https://github.com/bocode-labs/ccherd.

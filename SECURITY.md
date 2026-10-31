# Security policy

## Reporting a vulnerability

Please **do not open a public issue** for a security problem.

Report it privately through GitHub's
[security advisory form](https://github.com/Fahad28may/nova/security/advisories/new),
which is visible only to the maintainer.

Please include:

- what the problem is, and what an attacker could achieve;
- the steps or a minimal case that demonstrates it;
- the affected version or commit, your OS and Python version.

You can expect an acknowledgement within a few days. Fixes for anything that
lets a remote page, a local process or injected model output reach your files,
your apps or your session token are treated as the highest priority.

Please give a reasonable window for a fix before disclosing publicly.

## Supported versions

The `main` branch is supported. Nova is a single-user local application with no
server deployment, so fixes land on `main` rather than in backports.

## What is in scope

Anything that breaks one of the guarantees Nova is built on:

- **Loopback only** — reaching the app from another machine, or making it bind
  beyond `127.0.0.1`.
- **Session token** — bypassing it, leaking it (logs, URLs, the audit log, the
  settings page), or a timing attack on the comparison.
- **Origin and Host validation** — DNS rebinding, cross-site WebSocket
  hijacking, or any way a page on another origin can drive the app.
- **The filesystem sandbox** — reading anything outside `NOVA_ALLOWED_ROOTS`, or
  any blocked file (`.env`, SSH keys, credential stores, browser profiles),
  including via `..`, symlinks or encoding tricks.
- **The confirmation gate** — getting a `sensitive` or `destructive` tool to run
  without a real click, or making the model able to approve its own action.
- **Prompt injection with consequences** — text in a file or a web result that
  causes a tool to run, data to be deleted, or anything to leave the machine.
- **Command execution** — getting a subprocess to run something outside
  `NOVA_ALLOWED_APPS`, or injecting arguments into one.
- **The calculator** — escaping the AST allow-list to reach any Python object.
- **XSS in the UI** — getting model output, a note or a tool result rendered as
  markup rather than text.
- **SQL injection** through any stored value.
- **Information disclosure** — stack traces, tokens or file paths reaching the
  browser.

## What is out of scope

- Anything requiring an attacker to already have local code execution as your
  user (they can read `.env` and the SQLite file directly — that is the threat
  model, not a bug).
- Reading a folder you yourself put in `NOVA_ALLOWED_ROOTS`, or launching an app
  you yourself whitelisted.
- The model being wrong, slow, or making poor tool choices. That is a quality
  issue — open a normal issue for it.
- Denial of service against your own machine by configuring absurd limits.
- Vulnerabilities in Ollama, a model, or an upstream dependency — please report
  those to the respective project. If Nova's use of one makes it worse, that
  part is in scope.

## Hardening already in place

See the [Security model](README.md#security-model) section of the README for the
full list: loopback binding, a constant-time session token, Origin/Host checks, a
strict CSP, risk tiers with mandatory user confirmation, a read-only path
sandbox, untrusted-content fencing, argv-only subprocesses, parameterised SQL, an
AST-based calculator, rate limits, log redaction and an audit trail.

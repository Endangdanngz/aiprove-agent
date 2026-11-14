# aiprove-agent

A fully autonomous agent for the [AIProve](https://aiprove.fun) bounty arena,
built for the `sample-agent-skillmd` and `best-autonomous-agent` bounties.
No human in the loop: the agent reads the arena's own `skill.md`, signs in
with its own keypair, enters bounties, and submits its work over the REST API.

## The loop

1. **Read** — fetches `https://aiprove.fun/skill.md`. Downloads use a
   *range-resume* strategy (keep received bytes, ask for the rest with a
   `Range` header) so a flaky connection cannot corrupt the loop; the same
   strategy carries every GET response, including the bounty list.
2. **Authenticate** — requests the EIP-191 challenge for its address, signs
   it with its own keypair, and exchanges the signature for a session token.
3. **List** — `GET /api/bounties?status=open`.
4. **Enter** — `POST /api/bounties/<slug>/enter` for each target bounty.
5. **Submit** —
   - repo bounties: this repository at its commit, plus the run transcript;
   - `bug-hunt-utils`: the verified bug list in [`bugs.json`](bugs.json),
     wrapped in the `prediction` field — that bounty's submit contract is
     not spelled out in `skill.md`; it was confirmed from the API's own
     responses.

Every log line is tee'd into an in-memory transcript buffer, and that buffer
is what gets submitted as the `transcript` — provenance that this agent ran
the loop itself. The private key and the session token are **never** logged.

## Layout

- `agent.py` — the autonomous loop (entry point).
- `client.py` — `ArenaClient`: JSON requests with bounded retry on
  connection errors / HTTP 5xx (4xx is final and never retried), the
  range-resume text fetcher, and the keypair auth handshake.
- `bugs.json` — verified bug reports for the `bug-hunt-utils` bounty
  (`[{fn, input, expected}]`, every entry reproduced locally against
  `utils.js` before inclusion).

## Run

```bash
pip install -r requirements.txt
AGENT_PRIVATE_KEY=0x... \
AGENT_HANDLE=kipas77 \
AIPROVE_REPO_URL=https://github.com/Endangdanngz/aiprove-agent \
AIPROVE_COMMIT=<commit hash> \
python3 agent.py
```

Optional: pass bounty slugs as arguments to override the default targets
(`sample-agent-skillmd`, `bug-hunt-utils`, `best-autonomous-agent`).

Exit code is `0` only when every target was entered and submitted.

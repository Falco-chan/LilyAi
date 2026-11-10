# HTTP API

Interactive docs (when running): `/api/v1/docs`.

Private endpoints need `Authorization: Bearer <key>`:

| Key | Env var | Allowed |
| --- | --- | --- |
| Read | `API_READ_KEY` | all `GET` endpoints |
| Write | `API_WRITE_KEY` | everything, including `POST /api/v1/sync` |

With no key configured, private endpoints answer `503`. Limit: 120 requests per minute per key role.

Every error has one shape:

```json
{"error": {"code": "unauthorized", "message": "Missing or invalid API key."}}
```

Codes: `unauthorized` 401, `forbidden` 403, `club_not_found` / `member_not_found` 404,
`invalid_request` 422, `rate_limited` 429 (with `Retry-After`), `api_disabled` 503, `internal_error` 500.

## Endpoints

### `GET /health` (public)
`{"status": "ok", "version": "0.2.0"}`. Used by Render and the self-pinger. Does no database or network work.

### `GET /api/v1/status`
Server, Discord connection and per-club state.

```json
{
  "server": "ok", "version": "0.2.0", "time": "2026-11-10T00:00:00Z",
  "discord": {"state": "ready", "latency_ms": 42, "error": null},
  "clubs": [{"key": "club1", "club_id": "974470619", "label": "Club 1", "name": null,
             "member_count": 30, "last_synced_at": "2026-11-10T00:00:00Z", "last_error": null}]
}
```

`discord.state` is one of `disabled`, `starting`, `ready`, `reconnecting`, `error`.
A club's `last_error` shows why its last sync failed (for example a rejected uma.moe key).

### `GET /api/v1/clubs/{club}/leaderboard?period=daily&top=30`
`club` is `club1`, `club2` or a configured club ID. `period` is `daily`, `weekly` or `monthly`.
`top` is 1 to 30. Returns `entries` (`rank`, `trainer_id`, `name`, `fans`, `gain`, `has_baseline`) and `resets_at`.
`has_baseline: false` means the member joined this period and their gain is shown as 0.

### `GET /api/v1/members/{trainer_id}`
Gain and rank for all three periods, plus the club. `404` if the trainer is not on a roster now.

### `POST /api/v1/sync` (write key)
Refreshes both clubs now. Answers `429` if a sync ran in the last 30 seconds.

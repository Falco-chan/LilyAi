# uma.moe client

Implemented in `fantracker/sources/umamoe.py`. It is the only module that knows uma.moe's API, so a
change on their side means editing that one file.

## Where the details come from (read this)

uma.moe's own OpenAPI file (`https://uma.moe/api/docs/openapi.yaml`) could not be fetched when this was
built. The endpoint, header and field names instead come from the notes in
`sukuna-shinmyomaru/umamusume-quota-dashboard` (`docs/uma-api.md`), whose author says they were made by
inspecting the live API. That is good evidence but second-hand. The tests use a fixture shaped like those
notes, so a passing test proves the parser, not that a real request works.

**Check it once against the real thing:** deploy, then open `GET /api/v1/status` with the read key. A club
with `last_error: null` and a `member_count` near 30 means the request and parser match reality. If
`last_error` says HTTP 401, 403 or 404, or that the response is missing `circle` or `members`, compare the
client with the official spec at `https://uma.moe/api/docs`.

## Request

```
GET https://uma.moe/api/v4/circles?circle_id=<id>&year=<Y>&month=<M>
X-API-Key: <UMAMOE_API_KEY>
```

- `month` is 1-based and follows **Japan time**. If uma.moe answers 400 (it does for a month it does not
  have yet, such as right after a month change) the client retries once with the previous month.
- No pagination is documented; a club has at most 30 members.
- The key is read only from the `UMAMOE_API_KEY` environment variable and is never logged or put in an
  error message.

## Response fields used

| Field | Used for |
| --- | --- |
| `circle.circle_id` | must match the requested club (the service rejects a mismatch) |
| `circle.name` | the club's display name |
| `circle.member_count` | helps decide who is still in the club (below) |
| `members[].viewer_id` | the trainer ID |
| `members[].trainer_name` | display name |
| `members[].daily_fans` | 32 slots of lifetime cumulative fans, slot 0 is the previous month's close |

A member's current fan total is their newest positive `daily_fans` slot. Negative slots are transfer
markers and zeros are missed scrapes or future days, so both are skipped.

## Who counts as still in the club

uma.moe keeps members who left mid-month in `members`, with zeros after their last day. The client keeps
the freshest `circle.member_count` members (or, if that field is missing, only members with data on the
newest day). One missed scrape looks like a departure, which is why `member_count` is used when present.
This is an inference from the notes, not a documented rule. A mismatch is logged as a warning.

Fan gains themselves are computed by this project from its own daily snapshots (UTC), not from
`daily_fans`, so the join-day and rejoin rules in the README hold regardless of uma.moe's JST days.

## Secrets

The uma.moe key was shared in plain text in a chat preference. Rotate it when convenient, and only ever
store it as a Render environment variable.

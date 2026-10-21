# K1 Cross-End Review Checklist

Use the applicable rows after building the requirement matrix. Record a locatable proof or the stated not-applicable reason for every reviewed row.

## Client

| Item | Evidence that proves it | Mark not applicable when |
|---|---|---|
| Entry / unlock | Final-state entry registration and the condition evaluated before navigation or activation | The requirement exposes no client entry or unlock rule |
| UI state | View-model/controller bindings for loading, enabled, empty, success, and failure states | The requirement has no UI |
| Request construction | Call site showing endpoint/message, field names, values, and serialization types | The requirement sends no client request |
| Response / push handling | Handler registration plus branches that consume every relevant response or push result | The flow has neither a response body nor a server push |
| Local cache | Read/write/invalidation sites and the key or model persisted | No state is cached on the client |
| Reconnect | Re-subscription, state refresh, or recovery path invoked after reconnect | The feature has no session-spanning or push-dependent state |
| Localization | UI lookup call and formal localization key/value evidence | The client shows no user-facing text |
| Error presentation | Error mapping from server/local failure to the visible client state or message | The operation cannot surface a user-visible failure |

## Server

| Item | Evidence that proves it | Mark not applicable when |
|---|---|---|
| Authority | Server-owned decision or calculation before state mutation/reward | The requirement is presentation-only and changes no authoritative state |
| Permission | Caller identity/role/state gate before the operation | The operation is explicitly public and identity-independent |
| Parameter validation | Range, existence, ownership, and type checks on request inputs | The handler accepts no caller-controlled parameter |
| State transition | Guarded before/after state assignments covering allowed transitions | The requirement changes no server state |
| Persistence | Storage write/read mapping and reload path for the changed state | The state is intentionally ephemeral |
| Reset / time boundary | Reset scheduler/calculation showing boundary, unit, and timezone | The requirement has no reset, expiry, or time window |
| Idempotence | Duplicate-request token, prior-state guard, or safe repeated-operation proof | The operation is read-only or repetition is explicitly intended |
| Concurrency | Transaction, lock, compare-and-set, or serialized ownership at the mutation point | No competing mutation can target the same state |
| Response | Response construction showing status, fields, types, and error branches | The server operation has no request-response contract |

## Config

| Item | Evidence that proves it | Mark not applicable when |
|---|---|---|
| Formal `.xlsx` row | Row and key located in the commit-extracted formal workbook | The requirement is explicitly not configuration-driven |
| Header `c` / `s` / `cs` consumer | Header marker aligned with the client/server code that reads the field | The workbook has no end-consumer header convention for the field |
| `type` / `ext` schema | Formal header/schema cells match the parsed scalar, object, or array shape | The checked row introduces no typed or extended field |
| `root` / `custom` override | Override row/source plus precedence or merge behavior in final-state evidence | The table is not overrideable |
| Foreign keys | Referenced table/key exists and the consumer follows the same relationship | The row contains no foreign reference |
| Defaults | Formal default cell plus consumer behavior when the value is absent | The field is required and has no default semantics |
| Enum | Formal enum value and matching client/server symbol or numeric value | The field is not enum-backed |
| Localization key | Formal key reference and a matching localization entry | The configured content is not user-facing text |
| Exported JSON | Generated row matches the formal workbook key, fields, and values | This workbook is not exported to JSON |

## Cross-end

| Item | Evidence that proves it | Mark not applicable when |
|---|---|---|
| Protocol fields / types | Client construction, protocol declaration, and server parsing agree on names, optionality, and types | No client-server message is involved |
| Enum values | Client, server, protocol, and config resolve the same semantic and numeric values | The requirement uses no cross-end enum |
| IDs | Producers, consumers, and formal config use the same identifier and lookup domain | No identifier crosses an end boundary |
| Defaults | Every end applies the same absent-value behavior | The field is always present and has no default |
| Ownership | Exactly one authoritative owner is evident and other ends consume its result | The requirement is isolated to one end |
| Request-response-push closure | Request trigger, server handling, response, optional push, and client consumption form a complete chain | The interaction is not request/response/push based |
| Time units / time zones | Field declarations and conversions agree on unit, epoch/boundary, and timezone | No time value or boundary crosses ends |
| Missing end | Matrix expectation is matched by final-state evidence from every required end | The document and architecture require only the ends already evidenced |

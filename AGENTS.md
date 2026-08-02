---
name: PostgreSQL
description: >
  Open-source relational database management system known for reliability, feature robustness, and performance
type: database
---

# PostgreSQL

Open-source relational database management system (RDBMS) known for reliability, feature robustness, and performance. PostgreSQL supports advanced data types, full ACID compliance, and extensibility, making it a popular choice for web applications, analytics, and geospatial workloads.

## Authentication

### Database Credentials (username/password)
- Auth type: `db`
- Default port: 5432
- Connection string format: `postgresql://{username}:{password}@{host}:{port}/{database}`
- URI scheme aliases: `postgresql://` and `postgres://` (PostgreSQL also accepts a keyword/value DSN format, which this connector does not use)

### Connection inputs

| Input | Phase | Storage | Required | Notes |
|---|---|---|---|---|
| `host` | pre_auth | connection.parameters | yes | No default |
| `port` | pre_auth | connection.parameters | yes | Integer; defaults to `5432` |
| `database` | pre_auth | connection.parameters | yes | One database per connection |
| `username` | auth | connection.parameters | yes | |
| `password` | auth | secrets | yes | Secret |
| `ssl_mode` | pre_auth | connection.parameters | no | Enum; defaults to `prefer` |
| `ssl_ca_certificate` | pre_auth | secrets | no | PEM bundle; secret. Required when `ssl_mode` is `verify-ca` or `verify-full` (enforced by a connection-contract validation rule) |

There is **no SSH tunnel input**. Do not advertise SSH tunnelling as a connector capability.

### SSL modes

libpq's own vocabulary, carried on a single parameter: `disable`, `allow`, `prefer` (default), `require`, `verify-ca`, `verify-full`. `prefer` permits a plaintext fallback if the server declines TLS. `require` additionally verifies the CA when one is supplied. Note that `gssencmode` is GSSAPI encryption, not TLS, and is deliberately not part of this enum.

## Post-Auth Steps

None required. Tables and views are discovered at runtime from `information_schema`; the `information_schema`, `pg_catalog`, and `pg_toast` schemas are excluded automatically.

## Transports

- `database` (**default**) -- `adbc`, driver `postgresql`. Arrow-native reads and writes; bulk load via `COPY`. TLS is carried in the DSN only; the transport declares no `db_kwargs`.
- `sqlalchemy` -- `postgresql+asyncpg`. Engine-side SQL and metadata path. Carries the generic `tls` block.

The two transports do **not** have the same TLS reach. The CA certificate is applied on the `sqlalchemy` transport, where `build_tls_connect_arg` turns it into an `SSLContext`. On ADBC there is no route for it: the libpq ADBC driver rejects every `adbc.postgresql.*` database option with `NOT_IMPLEMENTED` before any network I/O, and libpq's `sslrootcert` wants a filesystem path, which a stored PEM secret cannot supply. Under ADBC, `verify-ca` / `verify-full` therefore fall back to libpq's default CA lookup at `~/.postgresql/root.crt`. **Use the `sqlalchemy` transport when CA pinning is required.**

Driver wheels: `adbc-driver-postgresql`, `adbc-driver-manager`, `asyncpg`.

## SQL capabilities

- `catalog`: `none` -- a session sees only its own database; cross-database replication needs one connection per database.
- `merge_form`: `insert_on_conflict` -- upserts use `INSERT ... ON CONFLICT (...) DO UPDATE`, available on all supported versions. `MERGE` (PostgreSQL 15+) is **not** used.
- `stage`: real (non-temporary) table in the target schema, created via `CREATE TABLE ... (LIKE target INCLUDING DEFAULTS)`; `transactional_ddl: true`.
- `bulk_load`: ADBC native ingest (`adbc_ingest`) over `COPY`.
- `limits`: `max_bind_params` 65535 (protocol Int16 cap on the parameterized INSERT path only, not `COPY`); `max_identifier_len` 63 -- which equals `SqlDialect.max_identifier_length`'s default, so the dialect does not override it.
- Errors are classified from 56 PostgreSQL `SQLSTATE` codes into the retry taxonomy (`transient`, `config`, `auth`, `unreachable`, `rate_limited`, `write_rejected`).

`PostgresDialect` implements exactly two write hooks -- `stage_table_sql` and `merge_statement_sql`. `empty_table_sql` and `bulk_land` are deliberately **not** overridden: the base `empty_table_sql` is already the DELETE-shaped statement PostgreSQL wants, and `bulk_load.adbc: adbc_ingest` is the ADBC backend's own landing rather than a dialect-implemented mechanism, so supplying a `bulk_land` would be dead code that reads as capability.

## Type mapping

Read (`definition/type-map-read.json`) and write (`definition/type-map-write.json`) are separate documents. Cases that are not one-to-one:

| PostgreSQL | Arrow | Reason |
|---|---|---|
| `NUMERIC(p,s)` within Arrow range | `Decimal128` / `Decimal256` | Precision preserved as declared |
| `NUMERIC` unconstrained; `p > 76`; `scale > precision`; negative scale | `Utf8` | Up to 131072 digits plus `NaN`/`Infinity` -- no exact Arrow decimal exists |
| `TIMESTAMP` | `Timestamp(unit)` | Zoneless on the wire |
| `TIMESTAMPTZ` | `Timestamp(unit, UTC)` | Stored as UTC; originating zone not retained |
| `TIME WITH TIME ZONE` / `TIMETZ` | `Time32` / `Time64` | **Lossy: the UTC offset is dropped.** Arrow has no time-with-offset type |
| `INTERVAL` | `Utf8` | Months/days/microseconds are independent fields, not a fixed duration; `Duration` would flatten them |
| `JSON`, `JSONB` | `Json` | |
| arrays (`integer[]`, `ARRAY`, sized and `ARRAY[n]` spellings) | `Json` | |
| `USER-DEFINED` (enum or composite) | `Utf8` | **Lossy: enum declaration order is not preserved** |
| `MONEY` | `Utf8` | Fraction digits depend on the server's `lc_monetary` |
| geometric, network, range/multirange, text-search, `reg*`, `pg_lsn` | `Utf8` | PostgreSQL text form |
| `OID` | `UInt32` | Unsigned four-byte integer |
| `smallserial` / `serial` / `bigserial` | `Int16` / `Int32` / `Int64` | Notational only; the catalog reports the underlying integer type |

Write direction: `Json`, `Object`, and `List` all render `JSONB`; `UInt64` renders `NUMERIC(20, 0)` (exceeds `bigint`); unsigned types widen to the next signed type; `Duration` renders `TEXT`; `Null` renders `TEXT`.

`Duration` renders `TEXT`, not `INTERVAL`, so that the maps converge. The read side deliberately maps `INTERVAL` to `Utf8` (months/days/microseconds are independent fields), so rendering `Duration` as `INTERVAL` would make a re-created destination table change its own column types on the next cycle -- `Duration -> INTERVAL -> Utf8 -> TEXT`. `Duration -> TEXT -> Utf8 -> TEXT` reaches a fixed point on the first round.

Precision note: PostgreSQL's `(p)` on temporal types is a display/rounding precision, not a storage unit -- storage resolution is always 1 microsecond. The read map still ladders `(p)` to `SECOND`/`MILLISECOND`/`MICROSECOND` because a `timestamp(0)` column rounds its values to whole seconds, making the narrower unit exact rather than lossy.

## Caveats

- SSL mode defaults to `prefer`, which falls back to unencrypted if the server does not support SSL. Use `require` or higher to forbid that; `verify-ca` or `verify-full` for production.
- `ssl_ca_certificate` is required when `ssl_mode` is `verify-ca` or `verify-full`.
- `time with time zone` loses its UTC offset on read -- use `timestamptz` when the offset matters.
- Unconstrained `NUMERIC` arrives as text, not a decimal; cast downstream if arithmetic is needed.
- `information_schema.columns.data_type` collapses every array to the literal token `ARRAY` and every enum/composite to `USER-DEFINED`. Resolving the real native requires `udt_name`/`udt_schema` or a join to `information_schema.element_types`. Domains are already resolved to their underlying type and need no special handling.
- The user must have `USAGE` privilege on each schema they need to access.
- Port must be an integer.
- No API rate limits apply -- this is a direct database connection. The nearest ceiling is the server's `max_connections` (typically 100), whose exhaustion surfaces as `SQLSTATE 53300`.

## Breaking changes in 2.0.0

- `OID` reads as `UInt32` (was `Int64`).
- Unconstrained `NUMERIC`/`DECIMAL` reads as `Utf8` (was a fixed `Decimal128(38, 9)`, which silently truncated larger values).
- `NUMERIC(p,s)` outside Arrow's legal range, including `scale > precision`, resolves to `Utf8` instead of producing an invalid decimal type.
- `UInt64` writes as `NUMERIC(20, 0)` (was `BIGINT`, which overflowed above 9223372036854775807). Target tables created as `BIGINT` by 1.x will not match the new declared type.
- `Duration` writes as `TEXT` (was `INTERVAL`, which did not round-trip -- see Type mapping).
- The dialect's public surface changed to the CDK's sanctioned write hooks: `supports_upsert_sqlalchemy`, `supports_upsert_adbc`, `build_sqlalchemy_upsert` and `adbc_stage_table_sql` are gone, replaced by `stage_table_sql` and `merge_statement_sql`. Upsert capability is declared by `sql_capabilities.merge_form` alone.

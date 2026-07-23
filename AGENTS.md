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
- Driver: postgresql
- Default port: 5432
- Connection string format: `postgresql://${username}:${password}@${host}:${port}/${database}`
- SSH tunnel support: yes

## Post-Auth Steps

None required.

## Caveats

- SSL mode defaults to `prefer`, which attempts encrypted connections but falls back to unencrypted if the server does not support SSL.
- For production environments, `verify-ca` or `verify-full` SSL modes are recommended.
- SSL CA certificate (`ssl_ca_certificate`) is required when `ssl_mode` is set to `verify-ca` or `verify-full`.
- The uploaded CA certificate is applied on the `sqlalchemy` transport only. On the default ADBC transport, `verify-ca`/`verify-full` rely on libpq's default CA lookup (`~/.postgresql/root.crt`) -- use the `sqlalchemy` transport when CA pinning is required.
- Upserts on the default ADBC transport use `MERGE` and require PostgreSQL 15+. On older servers, use the `sqlalchemy` transport (`INSERT ... ON CONFLICT`).
- PostgreSQL supports two connection string formats: keyword/value and URI (`postgresql://` or `postgres://`).
- Port must be an integer.
- No API rate limits apply -- this is a direct database connection.
- The user must have `USAGE` privilege on each schema they need to access.
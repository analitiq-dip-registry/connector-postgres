# PostgreSQL

Open-source relational database management system (RDBMS) known for reliability, feature robustness, and performance. PostgreSQL supports advanced data types, full ACID compliance, and extensibility, making it a popular choice for web applications, analytics, and geospatial workloads.

## What is this?

This is a **connector** -- a configuration that defines how to authenticate with PostgreSQL and what data is available for reading and writing. It does not move data by itself. Instead, it is used by the [Analitiq](https://analitiq-app.com) data integration platform or the open-source `analitiq-core` engine to set up data pipelines.

## How to use this connector

There are two ways to use this connector:

### Option 1 -- Analitiq Cloud (no setup required)

All connectors from this registry are automatically available on [analitiq-app.com](https://analitiq-app.com). Simply log in, select the connector, and follow the on-screen instructions to connect your database.

### Option 2 -- Open Source (self-hosted)

All connectors are open source and free to use. To get started:

1. Clone the [analitiq-core](https://github.com/analitiq-core) repository
2. Install the Claude plugin `analitiq-plugin-dataflow`
3. Launch Claude in the root directory of `analitiq-core`
4. Tell it: *"I need to move data from X to Y"*

The `analitiq-plugin-dataflow` plugin will automatically fetch the required connectors from the [Analitiq Skill Registry](https://github.com/analitiq-dip-registry) and set up the data flow pipeline for you.

## Prerequisites

- A running PostgreSQL server (version 12 or later recommended)
- A database user account with appropriate permissions for the tables you need to access
- Network access from the Analitiq platform to your PostgreSQL server (direct or via SSH tunnel)
- If using SSL, the relevant CA certificate and/or client certificate and key

## Authentication

PostgreSQL uses standard database credentials (username and password) to authenticate. The connector supports optional SSL encryption and SSH tunneling for secure connections.

### How to get your credentials

1. Log in to your PostgreSQL server as an administrator
2. Create a dedicated user for the integration (recommended):
   ```sql
   CREATE USER analitiq_user WITH PASSWORD 'your_secure_password';
   ```
3. Grant the necessary privileges on the target database:
   ```sql
   GRANT CONNECT ON DATABASE your_database TO analitiq_user;
   GRANT USAGE ON SCHEMA public TO analitiq_user;
   GRANT SELECT ON ALL TABLES IN SCHEMA public TO analitiq_user;
   ```
4. Note the host address, port (default 5432), database name, username, and password
5. If your PostgreSQL server requires SSL, obtain the CA certificate and optionally the client certificate and key from your database administrator

## Limitations

- **SSL mode** -- Defaults to `prefer`, which attempts encrypted connections but falls back to unencrypted if the server does not support SSL. Set to `require` or higher for enforced encryption. For production, `verify-ca` or `verify-full` is recommended.
- **No rate limits** -- This is a direct database connection; no API rate limits apply. However, heavy queries may impact database performance.

## For AI agents

This connector includes `CLAUDE.md` and `AGENTS.md` files -- machine-readable references used by AI agents and agentic frameworks. They document authentication types, caveats, and connection details for programmatic use. Both files are kept identical -- `CLAUDE.md` is for Claude Code, `AGENTS.md` is for other agent frameworks.

## Create a connector to any system

You can create a new connector to any API or database using Claude and the Analitiq connector builder plugin:

1. Install [Claude Code](https://claude.ai/code)
2. Install the connector builder plugin:
   ```
   claude plugin add analitiq-dip-registry/analitiq-plugin-connector-builder
   ```
3. Launch Claude and say: *"I want to create a connector for [system name]"*
4. The plugin will interview you about the system, research its API documentation, and generate the full connector with all required files

No coding required -- the plugin handles authentication research, endpoint schema generation, and file creation automatically.

![Example of Claude building a connector](media/example_1.png)

## Contributing

All connectors in this registry are community-maintained and live at [github.com/analitiq-dip-registry](https://github.com/analitiq-dip-registry). To add new endpoints or improve an existing connector, install the [connector builder plugin](https://github.com/analitiq-dip-registry/analitiq-plugin-connector-builder) and follow its instructions.

## Links

- [PostgreSQL Documentation](https://www.postgresql.org/docs/current/)
- [Analitiq Cloud](https://analitiq-app.com)
- [Analitiq Core (open source)](https://github.com/analitiq-core)
### Eve Agent

ERP graph agent backed by FalkorDB.

Eve copies a Frappe site's DocType schema and its ERP records into [FalkorDB](https://www.falkordb.com/) (a graph database on Redis). It then adds ERPNext / HRMS business relationships, such as customer → order → delivery → invoice → payment, employee → department, and GL postings, so the data can be explored and queried as a graph.

It writes two graphs per site:


| Graph                                     | Contents                                                                                     |
| ----------------------------------------- | -------------------------------------------------------------------------------------------- |
| `<site>_schema` (e.g. `eve_local_schema`) | One node per DocType, with edges for Link fields (`LINKS_TO`) and child tables (`HAS_CHILD`) |
| `<site>_data` (e.g. `eve_local_data`)     | One node per record and per child-table row, plus the business relationships                 |


See **[docs/graph-guide.md](docs/graph-guide.md)** for the graph model and ready-to-run Cypher for sales, buying, stock, accounts, HR, payroll, projects and assets.

### Requirements

- A Frappe v15 bench. ERPNext and HRMS are optional: Eve syncs whatever DocTypes the site has, and the business rules only add edges for data that exists.
- A FalkorDB server, **separate from the bench's own Redis** (cache and queue).
- Docker, or another way to run FalkorDB.



### 1. Run FalkorDB

```bash
docker run -d --name falkordb --restart unless-stopped \
  -p 6500:6379 -p 4000:3000 \
  -v falkordb_data:/var/lib/falkordb/data \
  falkordb/falkordb:latest
```

- **Port 6500** is the FalkorDB server (Redis protocol). It's mapped away from 6379 so it can't clash with a local Redis.
- **Port 4000** is the FalkorDB Browser UI.
- The volume keeps the graphs across container restarts. Without it they are lost, but a resync rebuilds them.

Check it is up:

```bash
redis-cli -p 6500 PING          # PONG
redis-cli -p 6500 GRAPH.LIST    # empty list on a fresh server
```



### 2. Install the app

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app git@github.com:aswnss-m/eve.git --branch develop
bench --site <site> install-app eve_agent
bench --site <site> migrate
```

Installing creates:

- the **Eve** workspace in the Desk sidebar
- the **Eve Agent Settings** page
- the `bench sync-schema` / `bench sync-data` commands

It also queues a first schema sync. Restart the bench afterwards (`bench start` in development, or `bench restart` in production). Background workers that were running before the install can't import the new app, so queued syncs would fail in them.

### 3. Configure

Open **Eve** workspace → **Eve Agent Settings**.


| Setting                 | Default                  | Notes                                                                                                                       |
| ----------------------- | ------------------------ | --------------------------------------------------------------------------------------------------------------------------- |
| FalkorDB Redis URL      | `redis://127.0.0.1:6500` | The FalkorDB server from step 1. Include a password if you set one: `redis://:pass@host:port`                               |
| Graph Name              | `<site>_schema`          | Schema graph. Leave blank for the default                                                                                   |
| FalkorDB Browser URL    | `http://127.0.0.1:4000`  | Used by the "Open FalkorDB Browser" button                                                                                  |
| Data Graph Name         | `<site>_data`            | Record graph. Leave blank for the default                                                                                   |
| Max Records per DocType | 5000                     | Newest records first. Child rows of loaded records are always included                                                      |
| Modules                 | blank                    | One module per line. Blank means Accounts, Selling, Buying, Stock, Setup, CRM, Projects, Assets, Manufacturing, HR, Payroll |
| Exclude DocTypes        | blank                    | One DocType per line, e.g. noisy log DocTypes                                                                               |


Click **Test Connection**. It should report the Redis version, `graph module: yes`, and the graph name.

### 4. Sync

The data graph is built **from the schema graph**, so sync the schema first (Full Resync does both in order).

**From the Desk:** Eve Agent Settings → **Sync**:

- **Sync Schema Now**: rebuilds `<site>_schema` from the DocType definitions.
- **Sync Data Graph Now**: rebuilds `<site>_data` from records, then applies the business rules.
- **Full Resync**: does the schema sync, then the data sync.

These run as background jobs on the `long` queue, so a worker must be running. An alert appears when a sync finishes, and the form shows **Last Sync Status / Summary** for both graphs. Failures are written to **Error Log** (also in the Eve workspace).

**From the terminal:** these commands run synchronously and print a summary.

```bash
bench --site <site> sync-schema        # schema graph only
bench --site <site> sync-data          # data graph only (needs a schema graph)
bench --site <site> sync-data --full   # schema, then data
```

Example output:

```
Schema: DocTypes: 926, LINKS_TO: 2804, HAS_CHILD: 463, pruned: 0
Data: DocTypes with data: 80, records: 12383, child rows: 6465, HAS_CHILD: 6465, LINKS_TO: 76865, business rules applied: 41/50, changes: 45765
```

**When it syncs automatically:** the schema sync runs after `install-app` and after every `bench migrate`. The data graph is a full rebuild and only runs when you trigger it. There is no live, per-document sync yet, so resync after loading or changing data.

### 5. Explore

1. Open **Eve** workspace → **Graph Browser** (or the button on Eve Agent Settings).
2. Connect the browser to the FalkorDB server. When the browser runs in the same container (step 1), that's `localhost:6379` inside the container.
3. Pick `<site>_data` or `<site>_schema` from the graph dropdown. Reload the browser if a graph that was just created doesn't appear yet.
4. Run a query that returns **paths**, not just nodes. For example:

```cypher
MATCH p = (c:Customer)<-[:FOR_CUSTOMER]-(:`Sales Order`)-[:DELIVERED_BY|BILLED_BY|PAID_BY*1..3]->()
RETURN p LIMIT 150
```

`MATCH (n:X) RETURN n` only returns nodes, so the browser shows unconnected dots. [docs/graph-guide.md](docs/graph-guide.md) explains this and lists many more queries.

From the shell:

```bash
redis-cli -p 6500 GRAPH.LIST
redis-cli -p 6500 GRAPH.RO_QUERY eve_local_data "MATCH (n:Record) RETURN n.doctype, count(*) AS c ORDER BY c DESC LIMIT 10"
```



### Troubleshooting


| Symptom                                                   | Cause / fix                                                                                                                                                                   |
| --------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| The browser only shows an old or empty graph              | The graph list in the browser is out of date. Reload it and pick `<site>_schema` / `<site>_data` from the dropdown. Check with `redis-cli -p 6500 GRAPH.LIST`                 |
| A Sync button shows "queued" but the status never changes | No worker, or a worker started before the app was installed. Restart the bench. A job that fails in an out-of-date worker can show `ModuleNotFoundError` in the rq job result |
| Data sync fails with "Schema graph … is empty"            | Run **Sync Schema Now** (or `sync-schema`) first                                                                                                                              |
| Test Connection fails                                     | FalkorDB isn't running or the URL is wrong. Check with `redis-cli -p 6500 PING`                                                                                               |
| `graph module: no`                                        | The URL points at a plain Redis (e.g. the bench's own Redis), not FalkorDB                                                                                                    |
| Nodes exist but you see no edges                          | The query returns nodes only. Return paths (`MATCH p = … RETURN p`)                                                                                                           |




### Development

Project layout:

```
eve_agent/
  falkor/            # FalkorDB layer, no Frappe imports
    connection.py    #   GraphClient over redis-py (GRAPH.QUERY / GRAPH.RO_QUERY)
    cypher/          #   schema_queries, data_queries, business_rules (ERPNext/HRMS layer)
    importer/        #   schema_import, data_rows, data_import, business_layer
  sync/              # Frappe glue: meta_reader, record_reader, schema_sync, data_sync
  api/               # whitelisted endpoints used by Eve Agent Settings
  commands.py        # bench sync-schema / sync-data
docs/graph-guide.md  # graph model and Cypher recipes
```

To add an ERPNext/HRMS relationship, add a rule to `RULES` in `eve_agent/falkor/cypher/business_rules.py` (see the end of the graph guide), then resync the data graph.

The tests are pure Python and don't need a site or FalkorDB:

```bash
cd apps/eve_agent
../../env/bin/python -m unittest eve_agent.tests.test_falkor_pure eve_agent.tests.test_data_rows eve_agent.tests.test_business_rules
```



### Contributing

This app uses `pre-commit` for code formatting and linting. Please [install pre-commit](https://pre-commit.com/#installation) and enable it for this repository:

```bash
cd apps/eve_agent
pre-commit install
```

Pre-commit is configured to use the following tools for checking and formatting your code:

- ruff
- eslint
- prettier
- pyupgrade


### Eve Agent

A company-brain chat agent for ERPNext, backed by FalkorDB.

Eve copies a Frappe site's DocType schema and its ERP records into [FalkorDB](https://www.falkordb.com/) (a graph database on Redis). It then adds ERPNext / HRMS business relationships (customer → order → delivery → invoice → payment, customer → item, supplier → item, employee → department, GL postings) and a people layer (who created, sells, commented on or was assigned what).

Installing the app also adds **Ask Eve**, a chat panel on every Desk page. Eve answers questions such as "which items sell best?", "who are our best customers?", "who knows Apex Industries best?" or "how did this order come about?" by querying the graph. Every answer shows the Cypher it ran and links to the ERP records behind it, and Eve remembers each user's past conversations in a memory graph.

It keeps three graphs per site:


| Graph                                     | Contents                                                                                     |
| ----------------------------------------- | -------------------------------------------------------------------------------------------- |
| `<site>_schema` (e.g. `eve_local_schema`) | One node per DocType, with edges for Link fields (`LINKS_TO`) and child tables (`HAS_CHILD`) |
| `<site>_data` / `<site>_data_next`        | One node per record and per child-table row, plus the business and people relationships. Rebuilds alternate between the two names |
| `<site>_memory`                           | Per-user chat memory: conversations, the records they mention, saved insights                |


- **[docs/graph-guide.md](docs/graph-guide.md)**: the graph model, and ready-to-run Cypher for sales, buying, stock, accounts, HR, payroll, projects and assets.
- **`eve_agent/chat/tools/`**: every Cypher query the agent's tools run, one module per tool group.

### How it works

```
Desk (any page) ── Ask Eve panel (Vue 3, reads a server-sent event stream)
   │ POST /api/method/eve_agent.api.chat.stream (Desk session + CSRF token)
Frappe web process, started by bench start (eve_agent/api/chat.py + eve_agent/chat/)
   ├─ LLM through LiteLLM: any provider it supports, one tool loop
   │    tools: find_entity, top_customers, who_knows, decision_trail, run_cypher, ...
   │    every ERP read is GRAPH.RO_QUERY on the active data graph
   └─ writes the turn to <site>_memory
FalkorDB ← Python sync in Frappe: schema graph, data graph + business rules, blue/green rebuilds
```

The agent has no other copy of the ERP data. Without FalkorDB it cannot answer anything.

| Tool | What it does in the graph |
|---|---|
| `find_entity` | Full-text index lookup of a name the user typed |
| `top_selling_items`, `top_customers`, `top_suppliers` | Aggregates over `Sales Invoice -CONTAINS→ Item` and `-FOR_CUSTOMER→ Customer` (or the buying side), with optional period filters |
| `customer_profile`, `item_profile` | 360° view: revenue, items, payments, stock, suppliers, BOMs, monthly sales |
| `also_bought` | 3-hop cross-sell: `Item ←BOUGHT- Customer -BOUGHT→ Item` |
| `dormant_customers`, `supplier_risk` | Customers that went quiet; items with only one supplier, weighted by the revenue that depends on them |
| `who_knows` | People linked to a record through `CREATED_BY`, comments, ToDos, `MANAGED_BY` / `SOLD_TO`, with department and designation from `Employee -IS_USER→ User` |
| `decision_trail` | Walks the document-flow edges around a document, with each step's creator, comments and ToDos |
| `explain_connection` | `shortestPath` between any two records |
| `graph_schema`, `describe_doctype`, `run_cypher` | Schema discovery and a guarded read-only text-to-Cypher fallback |
| `recall`, `remember_insight` | Read and write the user's memory graph |

### Requirements

- A Frappe v15 bench. ERPNext and HRMS are optional: Eve syncs whatever DocTypes the site has, and the business rules only add edges for data that exists.
- A FalkorDB server, **separate from the bench's own Redis** (cache and queue).
- Docker, or another way to run FalkorDB.
- An LLM: an Anthropic, OpenAI or Google API key, or any OpenAI-compatible server (Ollama, LiteLLM proxy, vLLM).



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
- **No Docker?** Create a free instance at [app.falkordb.cloud](https://app.falkordb.cloud). Host and port are on its **Connectivity** tab (Endpoint), user and password under **Instance Details → Parameter**. Enter them in the Host fields of step 3. The instance ID is only a console label; clients don't use it.

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
| FalkorDB Host / Port    | blank / 6379             | Alternative to the URL. When Host is set the URL is ignored. Local Docker: `127.0.0.1` / `6500`. Cloud: the Endpoint from the Connectivity tab |
| FalkorDB Username       | blank                    | Blank for a server without auth or with only `--requirepass`. FalkorDB Cloud: `falkordb`                                     |
| FalkorDB Password       | blank                    | Stored encrypted. Blank for a server without auth                                                                            |
| Use TLS                 | off                      | On for FalkorDB Cloud paid tiers only. Free tier and local Docker have no TLS                                                 |
| Schema Graph Name       | `<site>_schema`          | Leave blank for the default                                                                                                 |
| FalkorDB Browser URL    | `http://127.0.0.1:4000`  | Used by the "Open FalkorDB Browser" button                                                                                  |
| Data Graph Name         | `<site>_data`            | Record graph. Leave blank for the default                                                                                   |
| Memory Graph Name       | `<site>_memory`          | Per-user chat memory. Leave blank for the default                                                                           |
| Max Records per DocType | 5000                     | Newest records first. Child rows of loaded records are always included                                                      |
| Modules                 | default list             | Allowlist, one module per line: a single module loads only that module. Blank restores Accounts, Selling, Buying, Stock, Setup, CRM, Projects, Assets, Manufacturing, HR, Payroll. The Module Def list shows every module |
| Exclude DocTypes        | blank                    | One DocType per line, e.g. noisy log DocTypes                                                                               |
| Additional DocTypes to Graph | blank               | One DocType per line, for DocTypes outside the modules (custom apps). A DocType a module already covers is synced once      |
| Incremental Sync        | on                       | Push every saved, submitted, cancelled or deleted record to the active graph within seconds (see "When it syncs")           |


Click **Test Connection**. It should report the Redis version, `graph module: yes`, and the graph name.

### 4. Sync

The data graph is built **from the schema graph**, so sync the schema first (Full Resync does both in order).

**From the Desk:** Eve Agent Settings → **Sync**:

- **Sync Schema Now**: rebuilds `<site>_schema` from the DocType definitions.
- **Sync Data Graph Now**: rebuilds `<site>_data` from records, then applies the business rules.
- **Full Resync**: does the schema sync, then the data sync.

These run as background jobs on the `long` queue, so a worker must be running. An alert appears when a sync finishes, and the **Sync Status** section at the bottom of the form shows the last run of each sync. The Eve workspace shows the same three readouts at the top. Failures are written to **Error Log** (also in the Eve workspace).

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

**When it syncs automatically:**
- The schema sync runs after `install-app` and after every `bench migrate`.
- **Incrementally, within seconds.** Saving, submitting, cancelling, renaming or deleting a synced record queues it (after the transaction commits), and a job on the `short` queue upserts those records into the active graph: the node, its child rows, its links, and the business relationships around it (a new invoice updates the customer's `BOUGHT` totals; a cancelled one takes them back). Many saves in a row are drained in batches. **Sync Pending Changes Now** on the Settings page queues this by hand; **Incremental Synced At / Summary** under Sync Status show the last run.
- **Every 5 minutes**, a sweep queues records whose `modified` changed without a hook (ERPNext updates invoice status, outstanding amounts, bins and cancelled GL entries with direct SQL), so those catch up at the next sweep. The site's scheduler must be enabled (`bench --site <site> enable-scheduler`).
- **Nightly at 02:00**, a full rebuild heals what the incremental path cannot see: links to records that were not in the graph yet, documents still pointing at a renamed master, raw SQL writes, and the `Max Records per DocType` cap (incremental upserts ignore it).
- Rebuilds are blue/green: they alternate between `<site>_data` and `<site>_data_next`. Records that change while a rebuild runs are applied to the new graph before **Active Data Graph** in Eve Agent Settings switches to it, so the agent never reads a half-built or stale graph.
- Turn **Incremental Sync** off to go back to rebuilds only.

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



### 6. Chat with Eve

1. **Configure the LLM** in Eve Agent Settings → **Eve Chat Agent**:
   - **LLM Model**: `provider:model`, e.g. `anthropic:claude-sonnet-5-5` (default), `openai:<model>`, `google:<model>`, or `compatible:<model>` for an OpenAI-compatible server such as Ollama (`compatible:llama3.1`). Other LiteLLM names also work, such as `ollama/llama3.1`. Existing values keep working.
   - **LLM API Key**: or leave it blank and let LiteLLM read the provider's variable from the environment `bench start` runs in: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, or `GEMINI_API_KEY` for Google.
   - **LLM Base URL**: only for `compatible`, e.g. `http://127.0.0.1:11434/v1` for Ollama.
   - **Chat Roles**: who sees the Ask Eve button. Defaults to System Manager, Sales Manager and Accounts Manager.
2. **Start the bench.** The chat runs inside the bench's web process, so `bench start` serves it. There is no second server and no npm command to run.
3. **Reload the Desk.** Click **Ask Eve** at the bottom right. Each answer lists its steps. Expand a step to see the Cypher, its parameters and the first rows, and click a source chip to open the record.
4. **Talk instead of typing.** The microphone button in the composer dictates into the text box (Chrome, Edge and Safari; needs https or `localhost` for microphone access). Review the transcript, then press Enter.

Settings are read on every question, so a model or key change applies to the next message without a restart.

**Letting Eve create records.** With **Allow Eve to create records** (and optionally **Allow Eve to update records**) on in Eve Agent Settings → **Eve Actions**, Eve can turn "Ashish is a new customer, his email is ashish@gmail.com, add him" or "create a sales invoice for Ashish, 2 units of SDR-0018" into a record:

- Eve reads the DocType's fields from the schema graph (`record_form`), asks for the required values and child rows it cannot infer, and resolves names with `find_entity` so links are exact.
- It then proposes the record (`propose_record`). Nothing is written yet: a card in the chat shows the DocType and every value, with **Create** / **Cancel**.
- **Create** inserts the record as the Desk user, so Frappe's own permissions and validations apply, and a dialog offers to open it. Submittable documents (invoices, orders, stock entries) are always created as **drafts**; Eve never submits. Review and submit them in the Desk.
- **DocTypes Eve may write** lists what Eve may propose. Everything else is refused.

**Cypher log.** Every Cypher the model writes itself (`run_cypher`) is stored in **Eve Cypher Log** (Eve workspace → Cypher Log) with the question, purpose, rows, timing, user and outcome, so repeated questions can later be served from a known-good query. Log Settings can prune it (90 days by default).

**Demo data for the people layer.** Seeded sites usually have every record owned by Administrator, so `who_knows` has no one to report. This command adds synthetic sales people, customer assignments, comments and ToDos written as the employee users:

```bash
bench --site <site> execute eve_agent.demo.seed_people.run
bench --site <site> sync-data
```

### Permissions

Eve answers each person only from what that person can read in Frappe. Eve shows less than Frappe would, never more. There is nothing extra to configure: Frappe's roles, DocPerms, User Permissions and field levels are the only source of truth.

**Access profiles.** People who can read the same things share one *access profile*, which is its own FalkorDB graph holding only those records and fields. The profile is worked out from Frappe at question time, so two users with different roles but the same read access share a profile. The things that make profiles differ are:
- which DocTypes the user can read (a role permission that is not "if owner");
- which permlevels (field levels) they can read;
- their User Permissions (Company, Territory and so on);
- the filter a DocType's `permission_query_conditions` hook returns for them.

Only **Administrator** reads the main data graph. A System Manager does not: they get an access profile like everyone else, because their permissions can be narrowed by User Permissions too.

**What Eve leaves out.** Some things Frappe lets a user see are not copied into shared profile graphs, because they depend on the individual:
- documents a user can read only because they were **shared** with them;
- DocPerms that grant read only **"if owner"**;
- **ToDo**, which Frappe shows each person only for themselves;
- DocTypes whose read access depends on custom permission code that differs per user;
- **own-employee records**: a user's own Employee record and records tied to an Employee (for example Sales Person records linked to an employee). Sales Team rows on a Customer still show the sales person's name.

Eve does show every System User's name and email, as Frappe's @mention list does.

**The "preparing" reply.** A profile is built in the background the first time one of its users asks a question, and rebuilt in place after permission or field-level changes. Until it is ready, that user gets "Eve is preparing data for your access level..." and Eve never answers from a broader graph meanwhile. This usually takes under a minute, and the user gets a message when it is ready. After editing DocPerms or roles that affect many users, expect this reply for a short while. If a build fails, the user is told, and the next question retries it. A System Manager can see the error in the **Eve Access Profile** list.

**Settings.** In Eve Agent Settings → **Access Profiles**:
- **Max Access Profiles** (default 20). When the limit is reached, the least recently used profile idle for at least a day is removed. If none can be removed, users are told Eve is at capacity and an Error Log is written.
- **Drop Unused Access Profiles After (Days)** (default 14).
- **Rebuild Access Profiles** (button, under Sync): marks every profile for rebuild and queues the builds. Use it after changing Eve's data settings if you do not want to wait for the nightly rebuild.

Profiles that no user with a Chat Role maps to any more are deleted. The existing **Chat Roles** setting still decides who may chat at all.

Save-speed check: <to be filled by controller>

### Troubleshooting


| Symptom                                                   | Cause / fix                                                                                                                                                                   |
| --------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| The browser only shows an old or empty graph              | The graph list in the browser is out of date. Reload it and pick `<site>_schema` / `<site>_data` from the dropdown. Check with `redis-cli -p 6500 GRAPH.LIST`                 |
| A Sync button shows "queued" but the status never changes | No worker, or a worker started before the app was installed. Restart the bench. A job that fails in an out-of-date worker can show `ModuleNotFoundError` in the rq job result |
| Data sync fails with "Schema graph … is empty"            | Run **Sync Schema Now** (or `sync-schema`) first                                                                                                                              |
| Test Connection fails                                     | FalkorDB isn't running or the URL is wrong. Check with `redis-cli -p 6500 PING`                                                                                               |
| `graph module: no`                                        | The URL points at a plain Redis (e.g. the bench's own Redis), not FalkorDB                                                                                                    |
| Test Connection fails with an SSL or certificate error    | **Use TLS** is on for a server without TLS (local Docker, Cloud Free tier), or off for a Cloud paid tier that has it                                                          |
| Nodes exist but you see no edges                          | The query returns nodes only. Return paths (`MATCH p = … RETURN p`)                                                                                                           |
| No Ask Eve button                                         | Your user lacks a **Chat Role**, **Enable Chat** is off, or the Desk was not reloaded after installing                                                                       |
| Chat answers "You are not allowed to chat with Eve"       | Your roles aren't in **Chat Roles**, or **Enable Chat** is off                                                                                                                 |
| Chat shows an AuthenticationError or "API key"            | No key for the model's provider. Set **LLM API Key**, or the provider variable (e.g. `ANTHROPIC_API_KEY`) in the environment that runs `bench start`, then restart the bench      |
| Chat shows "LLM Provider NOT provided" or "model not found" | Check **LLM Model**: the provider prefix must be one LiteLLM knows (`anthropic:`, `openai:`, `google:`, `compatible:`, or a LiteLLM name such as `ollama/llama3.1`)              |
| Answers mention old data                                  | No `short` worker is running (incremental sync), the scheduler is disabled, or **Incremental Sync** is off. Use **Sync Pending Changes Now** or **Sync Data Graph Now**            |
| A new record is in the graph but a total (e.g. BOUGHT) is off | Cross-document totals are recomputed for the records a change touches; anything missed is corrected by the nightly rebuild, or run **Sync Data Graph Now**                 |
| No microphone button in the chat                          | The browser has no SpeechRecognition (Firefox), or the Desk is served over plain http from a host other than localhost                                                       |




### Development

Project layout:

```
eve_agent/
  falkor/            # FalkorDB layer, no Frappe imports
    connection.py    #   GraphClient over redis-py (GRAPH.QUERY / GRAPH.RO_QUERY)
    cypher/          #   schema_queries, data_queries, business_rules (ERPNext/HRMS + people layer)
    importer/        #   schema_import, data_rows, data_import, business_layer
  sync/              # Frappe glue: meta_reader, record_reader, schema_sync, data_sync, refresh (keep current)
  falkor/importer/incremental.py  # per-record upsert into a live graph (used by sync/refresh.py)
  sync/refresh.py    # doc_events hook, pending queue, drain job, 5-minute sweep, nightly rebuild
  api/               # whitelisted endpoints: chat (streamed Ask Eve turn), agent (permissions, Settings), actions (apply a confirmed record), settings, sync
  chat/              # Ask Eve: LLM layer (LiteLLM), turn loop, graph tools, memory, system prompt
    llm.py           #   provider:model → LiteLLM model
    loop.py          #   streamed tool-calling loop and its events
    tools/           #   the graph tools (entities, sales, brain, utility, actions)
    memory.py        #   per-user memory graph
    cypher_log.py    #   Eve Cypher Log writer
  boot.py            # tells the Desk whether to show Ask Eve
  demo/              # synthetic people-layer data for demos
  public/dist/       # built chat widget (committed; rebuild with `npm run build`)
  public/mascot/     # Eve's face animations (see animations/INSTRUCTION.md before adding expressions)
  commands.py        # bench sync-schema / sync-data
frontend/eve_chat/   # chat widget source (Vue 3, streams from the chat endpoint)
docs/graph-guide.md  # graph model and Cypher recipes
```

After changing `frontend/`, run `npm install && npm run build` in `apps/eve_agent`, and commit `eve_agent/public/dist`. The widget's URL carries the build's timestamp, read when `hooks.py` is imported, so the web process must restart: the build script touches `hooks.py` to trigger the dev server's reloader; in production run `bench restart` and `bench --site <site> clear-cache`. Browsers then fetch the new file on the next reload.

To add an ERPNext/HRMS relationship, add a rule to `RULES` in `eve_agent/falkor/cypher/business_rules.py` (see the end of the graph guide), then resync the data graph. Every rule also carries `scoped` statements that the incremental sync runs for just the records that changed; the `rename`, `flow`, `derived` and `local` builders produce both forms from one query.

The Python tests need no site and no LLM. The chat tests script the model:

```bash
cd apps/eve_agent
../../env/bin/python -m unittest eve_agent.tests.test_falkor_pure eve_agent.tests.test_data_rows eve_agent.tests.test_business_rules eve_agent.tests.test_agent_api eve_agent.tests.test_chat eve_agent.tests.test_incremental
```

The record-write endpoint has Frappe tests that need a site with ERPNext data (`bench --site <site> set-config allow_tests true` once):

```bash
bench --site <site> run-tests --module eve_agent.tests.test_actions
```

`test_chat` also runs every tool, and a scripted turn that is remembered, against a live data graph. It skips when FalkorDB or the graph is missing. Set `EVE_TEST_REDIS` / `EVE_TEST_GRAPH` to point it elsewhere, or `EVE_TEST_LIVE=0` to turn it off.



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

### Graph Hacks submission

- **Track:** Company Brain (Track 03). The graph also does the work for Agents That Act on Connected Data (multi-hop tools, explainable results) and Agent Memory (the per-user memory graph).
- **Data:** synthetic. The demo site is filled by a seeding app plus `eve_agent.demo.seed_people`; no real customer or personal data.
- **Graph model:** [docs/graph-guide.md](docs/graph-guide.md). **Queries the product depends on:** `eve_agent/chat/tools/` and `eve_agent/falkor/cypher/`.
- **AI assistance:** AI coding assistants (Claude Code) were used to build parts of this project.


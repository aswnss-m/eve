# Eve environments: links and logins

Where Eve runs, how to reach it, and the logins for testing. **Production passwords are not kept here**
(this repo is public): ask the team for them. The local test values only work on a local bench.

How Eve works and how to install it: [README](../README.md). The graph model: [graph-guide.md](graph-guide.md).

## Environments at a glance


|                    | Production demo (hosted)                                                                                 | Local test                                                                                                  |
| ------------------ | -------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| ERP with Eve       | [http://167.235.241.112:8080](http://167.235.241.112:8080) (site `cozycrew.local`, company CozyCrew)     | [http://eve.localhost:8001](http://eve.localhost:8001) (site `eve.localhost`, company Northwind Components) |
| Eve Agent Settings | [http://167.235.241.112:8080/app/eve-agent-settings](http://167.235.241.112:8080/app/eve-agent-settings) | [http://eve.localhost:8001/app/eve-agent-settings](http://eve.localhost:8001/app/eve-agent-settings)        |
| FalkorDB Browser   | [http://167.235.241.112:4000](http://167.235.241.112:4000) (login gate)                                  | [http://127.0.0.1:4000](http://127.0.0.1:4000) (no login)                                                   |
| FalkorDB server    | `redis://falkordb:6379`, stack network only                                                              | `redis://127.0.0.1:6500`                                                                                    |
| Runs on            | Docker (frappe_docker) on `ssw@167.235.241.112`                                                          | WSL Ubuntu-22.04 `~/eve-bench` + Docker Desktop                                                             |
| Eve version        | `develop` @ `999b27b`, deployed 2026-10-18                                                               | `develop` (pull with `git pull upstream develop`)                                                           |




## Logins

Production passwords are shared outside this repo.

### Production demo


| What                                                   | Where                                                      | Username        | Password                                                     |
| ------------------------------------------------------ | ---------------------------------------------------------- | --------------- | ------------------------------------------------------------ |
| ERP, Administrator | [http://167.235.241.112:8080](http://167.235.241.112:8080) | `Administrator` | _not in the repo: ask the team_ |
| FalkorDB Browser, login gate | [http://167.235.241.112:4000](http://167.235.241.112:4000) | `eve-erp` | _not in the repo: ask the team_ |
| FalkorDB Browser, database connection (after the gate) | the browser's login page                                   | blank           | blank; host `localhost`, port `6379`                         |
| Server shell                                           | `ssh ssw@167.235.241.112`                                  | `ssw`           | SSH key, no password; `ssw` can run `docker`                 |
| LLM (DeepSeek)                                         | Eve Agent Settings → LLM API Key                           | n/a             | stored encrypted in the site; model `deepseek/deepseek-chat` |


Only users with a **Chat Role** (System Manager, Sales Manager, Accounts Manager) see Ask Eve. On
`cozycrew.local` that is currently Administrator.

### Production demo: test users, one per access type

For testing Eve's permission-aware answers at [http://167.235.241.112:8080](http://167.235.241.112:8080). Each user should get
answers only from what their roles can read in Frappe.


| Access type     | Username                     | Password         | Roles                           | Ask Eve | What to check                                                                                          |
| --------------- | ---------------------------- | ---------------- | ------------------------------- | ------- | ------------------------------------------------------------------------------------------------------ |
| Sales | `eve.sales@cozycrew.test` | _not in the repo: ask the team_ | Sales Manager, Sales User | yes | Customers, quotations, sales orders and invoices answer; purchasing and GL questions don't |
| Accounts | `eve.accounts@cozycrew.test` | _not in the repo: ask the team_ | Accounts Manager, Accounts User | yes | Invoices, payments and GL answer; the answers differ from the Sales user's |
| Stock (no chat) | `eve.stock@cozycrew.test` | _not in the repo: ask the team_ | Stock User | no | No Ask Eve button. To test a narrow chat profile, add `Stock User` to Chat Roles in Eve Agent Settings |




### Local test (test values, local only)


| What                                             | Where                                                                            | Username                                               | Password                                                          |
| ------------------------------------------------ | -------------------------------------------------------------------------------- | ------------------------------------------------------ | ----------------------------------------------------------------- |
| ERP | [http://eve.localhost:8001](http://eve.localhost:8001) | `Administrator` | _the `--admin-password` the bench was created with; ask the team_ |
| ERP, setup wizard user (System Manager)          | same                                                                             | `eve.admin@eve-demo.local`                             | `EveDemo#2026`                                                    |
| ERP, Sales Manager (can use Ask Eve)             | same                                                                             | `priya@eve-demo.local`                                 | `EveDemo#2026`                                                    |
| ERP, Accounts Manager (can use Ask Eve)          | same                                                                             | `meera@eve-demo.local`                                 | `EveDemo#2026`                                                    |
| ERP, Sales / Purchase / Stock users (no Ask Eve) | same                                                                             | `rahul@`, `ananya@`, `vikram@`, `arjun@eve-demo.local` | `EveDemo#2026`                                                    |
| MariaDB (Docker)                                 | `127.0.0.1:3307`                                                                 | `root`                                                 | `MARIADB_ROOT_PASSWORD` in the test bench's `.env` |
| FalkorDB                                         | `redis://127.0.0.1:6500`, browser [http://127.0.0.1:4000](http://127.0.0.1:4000) | blank                                                  | blank                                                             |




## Production demo in detail



### The server

- Host `frappe-health-demo`, Ubuntu with 3.7 GB RAM + 4 GB swap and a 38 GB disk. Public IP `167.235.241.112`.
- Eve runs on site `cozycrew.local` (frappe, erpnext, agentic_erp, eve_agent), in a frappe_docker
stack in `/home/ssw/almas-stack`. Image `ehr-health:v15`.

Run compose through the wrapper, which passes the stack's compose files:

```bash
cd ~/almas-stack
./eve-compose.sh ps
```


| File in `~/almas-stack`                              | Purpose                                                                                                                         |
| ---------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| `eve-compose.sh`                                     | `docker compose` with the stack's compose files                                                                                 |
| `Containerfile.eve`                                  | Builds `ehr-health:v15-eve` = `ehr-health:v15-before-eve` + `apps/eve_agent`                                                    |
| `apps/eve_agent/`                                    | Clone of github.com/aswnss-m/eve, branch `develop`                                                                              |
| `compose-eve.yml`                                    | The `falkordb` service (FalkorDB v4.22.1, AOF on, volume `falkordb-data`) and `falkordb-gate` (nginx on `167.235.241.112:4000`) |
| `falkordb-gate/nginx.conf`, `falkordb-gate/htpasswd` | The gate's config and its logins (SHA-512 crypt hashes)                                                                         |
| `falkordb-login.sh`                                  | Adds a gate login or changes its password                                                                                       |
| `eve-test-users.sh`                                  | Creates the three test users on `cozycrew.local`, or gives them new passwords                                                   |
| `deploy-eve.sh`                                      | The first install, step by step, with rollback notes                                                                            |
| `compose-eve.yml.bak-before-gate`                    | `compose-eve.yml` before the gate was added                                                                                     |




### Eve on cozycrew.local


| Setting                         | Value                                                                                                                                     |
| ------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| FalkorDB Redis URL              | `redis://falkordb:6379` (no password; the port is not published)                                                                          |
| Graphs                          | `cozycrew_local_schema`, `cozycrew_local_data` / `cozycrew_local_data_next` (blue/green, active one in Settings), `cozycrew_local_memory` |
| LLM                             | `deepseek/deepseek-chat`, key set                                                                                                         |
| Chat Roles                      | System Manager, Sales Manager, Accounts Manager                                                                                           |
| Eve may create / update records | off / off                                                                                                                                 |
| Incremental sync                | on; the scheduler is enabled, so the 5-minute sweep and the 02:00 rebuild run                                                             |
| FalkorDB Browser URL            | [http://167.235.241.112:4000](http://167.235.241.112:4000)                                                                                |


First full sync (2026-10-18): 777 DocTypes in the schema graph; 10,675 records and 6,864 child rows in
the data graph, in about 20 seconds. FalkorDB uses about 150 MB.

Useful pages: [Eve Cypher Log](http://167.235.241.112:8080/app/eve-cypher-log),
[Eve Access Profile](http://167.235.241.112:8080/app/eve-access-profile),
[Error Log](http://167.235.241.112:8080/app/error-log).

### Using the FalkorDB Browser

1. Open [http://167.235.241.112:4000](http://167.235.241.112:4000) and enter a gate login.
2. On the FalkorDB login page keep host `localhost` and port `6379`, leave Username and Password blank, and click **Log in**.
3. Click **Skip Tutorial**, then pick `cozycrew_local_data_next` (or whichever graph is active) under **Select Graph**.

Add or change a gate login, from your own terminal (not through Claude Code, or the password lands in the chat):

```bash
ssh ssw@167.235.241.112 "bash ~/almas-stack/falkordb-login.sh --generate <username>"   # prints a random password once
ssh -t ssw@167.235.241.112 "bash ~/almas-stack/falkordb-login.sh"                       # choose your own
```

Remove a login by deleting its line from `~/almas-stack/falkordb-gate/htpasswd`. nginx reads the file on
every request, so no restart is needed.

Without the gate, through an SSH tunnel: `ssh -L 4000:127.0.0.1:4000 ssw@167.235.241.112`, then
[http://localhost:4000](http://localhost:4000).

### Common tasks

All on the server, from `~/almas-stack`.

```bash
B="docker exec frappe-health-backend-1"

# Rebuild the graphs now (schema, then data)
$B bench --site cozycrew.local sync-data --full

# FalkorDB
docker exec frappe-health-falkordb-1 redis-cli GRAPH.LIST

# Logs
docker logs --tail 100 frappe-health-backend-1
docker logs --tail 100 frappe-health-queue-long-1
```

**Update Eve to the latest** `develop`**.** The rebuild takes about a minute; recreating the containers
interrupts the site for about a minute.

```bash
git -C apps/eve_agent pull
docker build -f Containerfile.eve -t ehr-health:v15-eve . 2>&1 | tee ~/build-eve.log   # pip check gates the build
docker tag ehr-health:v15-eve ehr-health:v15
./eve-compose.sh up -d --no-deps backend websocket scheduler queue-short queue-long frontend-cozycrew
$B bench --site cozycrew.local clear-cache
$B bench --site cozycrew.local migrate
```

**Roll back to the stack without Eve:**

```bash
$B bench --site cozycrew.local uninstall-app eve_agent --yes
docker tag ehr-health:v15-before-eve ehr-health:v15
./eve-compose.sh up -d --no-deps backend websocket scheduler queue-short queue-long frontend-cozycrew
$B bash -c 'ls -1 apps > sites/apps.txt'
./eve-compose.sh stop falkordb falkordb-gate
```



### Known issues and decisions

- **litellm is pinned to 1.83.0 in the image.** Every litellm from 1.84 needs `openai<3` and
`boto3>=1.43`. agentic_erp (`openai-agents`) needs `openai>=3`, and Frappe 15.112 pins `boto3~=1.34`.
1.83.0 is the newest litellm that fits both. `Containerfile.eve` lowers Eve's `litellm>=1.97` floor for
this image only. It was checked against the Anthropic, OpenAI and Gemini APIs; DeepSeek runs through
the same LiteLLM code.
- **After switching images, run** `clear-cache` **before** `install-app`**.** redis-cache keeps the old app
module map, so the first `install-app` synced no DocTypes. `migrate` fixed it. The failed pass left 137
"Eve Agent: queue_record failed" entries and one schema-sync failure in Error Log (2026-10-18 19:46).
They are harmless and can be deleted.
- **No Eve workspace.** The repo has no workspace definition, so the "Eve workspace" in the README
doesn't exist. Use the Settings URL.
- **Plain http.** Every login, including the FalkorDB gate's, crosses the internet unencrypted. The mic
button needs https, so voice input doesn't work here.
- **Anyone with a gate login has full access to every graph,** including deleting graphs. The ERP
graphs come back with a resync; the memory graph (chat history) does not.



## Local test in detail

The test bench's own notes (`eve-test-env/README.md`, outside this repo) cover start/stop, the seeded data and its known
answers, the test commands, the gotchas and a rebuild from scratch. In short:

```bash
# Windows: Docker Desktop runs MariaDB (eve-test-env/docker-compose.yml) and FalkorDB (this repo's docker-compose.yml)
wsl -d Ubuntu-22.04
cd ~/eve-bench && bench start            # http://eve.localhost:8001
```

- The bench `~/eve-bench` is separate from the client bench `~/frappe-bench`. Don't install Eve on the client bench.
- WSL has Node 18: don't run `bench build` (Eve's Vite 7 needs Node 20.19+). The widget's `dist/` is committed.
- Pass `--skip-before-tests` to `bench run-tests`; ERPNext's `before_tests` deletes every Item Price.


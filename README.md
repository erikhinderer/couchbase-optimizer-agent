# Couchbase Optimizer Agent

A dockerized AI agent with a dashboard UI that continuously analyzes Couchbase Enterprise and
Couchbase Capella clusters, surfaces optimization opportunities, and safely applies the ones that
need no application code change -- with a named-approver sign-off before anything runs.

## Capabilities

- **Continuous analysis.** A background scheduler re-analyzes every registered cluster on a fixed
  interval (`ANALYSIS_INTERVAL_S`, default 5 minutes): it reads `system:completed_requests` and
  `system:indexes` over the Couchbase SDK's query service, and node/bucket resource stats over the
  Management REST API (self-hosted EE) or the Capella Management API (if configured), then runs a
  rule engine across index, query, and resource categories.
- **Safe auto-apply, with approval.** Findings the agent can perform with no application code
  change (create a missing index, add an index replica, raise a bucket's RAM quota, drop an
  apparently-unused index) are classified `SAFE_AUTO`. They're never applied automatically --
  they wait in **Pending Approval** for a named approver to review and confirm, exactly the way
  the WASM-sandbox-tested statement is shown before it runs.
- **Code-change suggestions.** Findings the agent can't safely act on itself (large result
  payloads, unprojected `SELECT *`, sustained high CPU, complex WHERE clauses) are classified
  `REQUIRES_CODE_CHANGE` and land in **Needs Code Change** with an explanation of what the
  application team needs to do -- the agent explains, it doesn't rewrite your queries for you.
- **Documentation citations.** Every finding and every chat answer that leans on documentation
  shows a source link back to docs.couchbase.com underneath it, so a recommendation can be
  independently validated rather than taken on faith.
- **WASM-sandboxed testing.** Before a `SAFE_AUTO` index finding is shown to a user, its expected
  impact is tested in a fuel-limited wasmtime sandbox with zero host access (see
  `wasm-sandbox/`) -- a "tested in sandbox" badge and cost estimate ride along with the
  recommendation.
- **Offline analysis from a support bundle.** No reachable cluster? Upload a Couchbase support
  bundle (`cbcollect_info` output) from the Clusters page instead of registering a live connection
  -- the agent parses it into the same shape it reads from a live cluster and runs the identical
  rule engine against that static snapshot. Always read-only, and needs no credentials at all. See
  "Support bundle uploads" below.
- **Three-tier agent memory, in Couchbase.** Short-term (self-expiring working context),
  episodic (one record per event), and long-term (LLM-consolidated baselines and patterns) memory
  all live in a Couchbase Enterprise Edition instance (free developer license) and are recalled
  via native Couchbase Vector Search, with an automatic N1QL + cosine-similarity fallback if the
  vector index is momentarily unavailable.

## Architecture

| Component | Tech | Purpose |
|---|---|---|
| `frontend/` | React + TypeScript + Vite | Dark-mode UI: left navigation tree, dashboard, insights feed, approval workflow, agent chat panel |
| `backend/` | FastAPI (Python) + Couchbase SDK | REST + WebSocket API, cluster analysis engine, rule-based finding detection, approval/apply workflow, docs citation client |
| `llm-service/` | Ollama serving a local LLM (Qwen 3, 8B by default) | Local-first LLM for the "Ask the agent" chat and memory embeddings -- cluster topology, query text, and index metadata never leave the Docker network |
| `agent-memory` (Couchbase EE) | Couchbase Enterprise Edition, free developer license | Short-term / episodic / long-term agent memory, recalled via native vector search |
| `wasm-sandbox/` | Python + wasmtime | Fuel-limited, no-host-access WASM sandbox for testing suggested optimizations before they're shown for approval |
| `scripts/init_memory.py` | Python | One-shot bootstrap: creates the memory bucket/scopes/collections and the three per-tier FTS vector indexes |

`agent-memory` is not a cluster under management -- it's the agent's own memory. The Couchbase
Enterprise or Capella clusters being analyzed are registered separately (via the UI or the
`/api/clusters` endpoint) and reached over the network; nothing about them lives in this Compose
stack.

## Quick start

```bash
cp env.example .env
# edit .env: set MEMORY_CB_PASSWORD at minimum
./scripts/setup-corporate-ca.sh
docker compose up --build
```

- UI: http://localhost:5173
- API: http://localhost:8000 (interactive docs at `/docs`)
- Couchbase EE admin console (agent memory): http://localhost:8091
- Local LLM / Ollama API: http://localhost:11434
- WASM sandbox: http://localhost:8100

First boot pulls the local LLM model (`qwen3:8b` by default, ~5GB) and initializes the Couchbase
Enterprise Edition memory store -- this can take a few minutes; subsequent starts are fast (cached
in the `ollama_data` / `agent_memory_data` volumes).

Once it's up, register a cluster from the **Clusters** page (or `POST /api/clusters`) with a
connection string, username, password, and access mode. The scheduler picks it up on its next
pass, or click the lightning-bolt icon to run one immediately.

## Cluster access & permissions

Every registered cluster runs in one of two modes, chosen when you register it (and changeable
later from the Clusters page):

- **Read-only** -- the default. The agent connects, reads `system:completed_requests` and
  `system:indexes`, pulls resource stats, and raises findings (including `SAFE_AUTO` ones), but
  `approve()`/`apply()` are refused server-side no matter what the UI shows. This is the safe
  mode to register a cluster in the first time.
- **Read/write** -- the agent may additionally execute an approved `SAFE_AUTO` finding's statement
  (`CREATE`/`ALTER`/`DROP INDEX`, a bucket RAM-quota change) once a named approver has signed off
  through the approval workflow below. It can never do this in read-only mode, and the enforcement
  lives in `core/optimizer.py`, not the frontend -- disabling the Approve button is a UX courtesy,
  not the actual gate.

The credential you register with needs Couchbase roles that match the mode you pick. The agent
does a best-effort check of this on registration/test-connection (self-hosted EE only, over the
Management REST API) and shows a note next to the cluster if the declared mode and the granted
roles look mismatched -- but the cluster's own RBAC is what actually enforces this, not the note.

**Self-hosted Couchbase Enterprise -- required roles:**

| Mode | Roles | Why |
|---|---|---|
| Read-only | `query_system_catalog` | Read `system:completed_requests` and `system:indexes` |
| | `data_reader` (or `query_select`) on the buckets to analyze | Let query-service introspection resolve keyspace metadata |
| | `ro_admin` | Read node/bucket stats over `/pools/default` and `/pools/default/buckets` |
| Read/write | *(all of the read-only roles above, plus:)* | The agent still needs to read before it can suggest anything |
| | `query_manage_index` | Create, alter, and drop indexes for `SAFE_AUTO` index findings |
| | `bucket_admin` on the buckets to manage (or `cluster_admin` for RAM-quota changes across buckets) | Apply bucket RAM-quota changes |

Create a dedicated, least-privilege user for this rather than reusing `Administrator` --
Couchbase's Query Workbench or `couchbase-cli user-manage` can grant multiple roles to one user:

```bash
couchbase-cli user-manage -c localhost:8091 -u Administrator -p <admin-password> \
  --set --rbac-username optimizer-agent --rbac-password '<password>' \
  --rbac-name "Optimizer Agent" --auth-domain local \
  --roles query_system_catalog,data_reader[demo_retail],query_select[demo_retail],ro_admin,query_manage_index[demo_retail],bucket_admin[demo_retail]
```

(Drop `query_manage_index` and `bucket_admin` -- and register the cluster as read-only -- if you
only want the agent to analyze and suggest.)

**Couchbase Capella -- required database access credential roles:** Capella's database access
credentials don't map to the same RBAC role names, and the Capella Management API doesn't expose
a way for the agent to introspect what a given credential can do -- the mismatch-check note always
reads "skipped" for Capella clusters, and the declared access mode is trusted as-is. When creating
the credential in **Capella UI -> Cluster -> Settings -> Database Access**:
- **Read-only mode:** grant **Read** access scoped to the bucket(s)/scope(s) to analyze.
- **Read/write mode:** grant **Read and Write** access to the same scope, which Capella maps to
  the index-management and data-mutation privileges the agent's `SAFE_AUTO` findings need.

Regardless of mode, resource-tier findings (RAM quota, node CPU) require self-hosted Management
REST access and are skipped for Capella unless `capella_cluster_id` plus `CAPELLA_API_TOKEN`/
`CAPELLA_ORG_ID` are configured for the Capella Management API (v4) -- see "Known limitations."

**How the agent uses this at runtime:** whichever cluster is selected in the sidebar shows a
**READ-ONLY** or **READ/WRITE** badge directly under the agent status indicator, and the chat
agent is always told the selected cluster's access mode (plus the role-check note, if any) as
part of its context -- it will tell you outright if you ask it to apply something on a read-only
cluster, rather than implying it already could.

## Support bundle uploads

Don't have a reachable cluster -- just a Couchbase support bundle (the `.zip`/`.tar.gz` a
`cbcollect_info` run produces)? On the **Clusters** page (or the "Upload a support bundle" link
next to "Register a cluster" when no cluster is selected), switch to **Upload a support bundle**,
give it a name, and upload the archive. No connection string, username, or password needed.

What happens under the hood (`backend/app/core/bundle_parser.py`): the archive is extracted and
every JSON file, plus every `.log`/`.txt` file, is scanned for content that structurally matches
what the rule engine already consumes from a live cluster -- rows shaped like
`system:completed_requests`, rows shaped like `system:indexes`, and REST dumps shaped like
`/pools/default` / `/pools/default/buckets` (every bundle includes the latter two, embedded in
`diag.log`, by default). Whatever's found is cached as a snapshot and run through the exact same
index/query/resource rule engine a live cluster uses.

This is a heuristic, not a guaranteed extraction: support-bundle contents vary by Couchbase
version and by the collection profile used, and there's no single stable file that's guaranteed to
contain a `system:completed_requests`/`system:indexes` export. After upload, the cluster shows a
parse summary ("Found N query-log row(s), N index definition(s)...") so it's obvious up front
whether the bundle had usable data -- an empty result usually means the bundle's profile didn't
capture query-service diagnostics, not that something went wrong.

Support-bundle clusters are always **read-only** (there's no live connection to apply a change
to), can't be switched to read/write, and don't re-analyze anything new on a schedule -- each
analysis pass re-runs the rule engine against the same cached snapshot. Upload a newer bundle
(as a new cluster) to refresh it.

## Demo data

A fresh cluster has nothing for the agent to find. `scripts/generate_demo_data.py` seeds a
**separate** Couchbase Enterprise or Capella cluster (never `agent-memory`) with realistic retail
data, a deliberately imperfect index landscape, and a repeated query workload engineered to
produce a real finding for all 15 patterns the rule engine looks for -- slow index scans, primary
index over-usage, deep OFFSET pagination, high CPU/kernel time, high per-query memory, slow
parse/plan, slow USE KEYS, missing WHERE clauses, complex JOINs, ineffective LIKE, SELECT *,
large payload streaming, large result sets, timeout-prone queries, and concurrent write conflicts.
See the module docstring in the script for the full pattern -> rule mapping.

**Requirements.** This script runs on your own machine (it's not part of any Docker image), so it
needs its own Python environment:

- **Python 3.8-3.11.** `couchbase==4.3.1` (pinned in requirements-demo.txt) only publishes prebuilt
  wheels for these versions on macOS/Linux/Windows -- check the exact set for whatever version you
  land on at https://pypi.org/project/couchbase/4.3.1/#files. On anything newer -- 3.12 included,
  not just 3.13+ -- `pip` silently falls back to building the C++ SDK from source, which needs a
  full build toolchain (cmake, a C++ compiler) and, depending on your OS's cmake version, can fail
  outright on compatibility errors in the SDK's vendored dependencies rather than just being slow.
  Check your version first with `python3 --version`; if it's not 3.8-3.11, install 3.11 via
  Homebrew (`brew install python@3.11`) and use `python3.11` below instead of `python3`.
- **pip 21.3+** (bundled with any Python 3.8-3.11 install) and a **virtual environment** --
  `pip install` straight into your system Python will fail on modern macOS with `error: externally
  -managed-environment` (PEP 668). Always install into a venv, not system-wide.

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r scripts/requirements-demo.txt
python scripts/generate_demo_data.py \
  --connection-string couchbase://<your-cluster-host> \
  --username Administrator --password '<password>' \
  --register-with-agent
```

Don't have those values handy, or just running it by hand? Omit `--connection-string` and the
script prompts for it interactively -- asking Capella vs. self-hosted Enterprise, the connection
string, username, and password (hidden input, via `getpass`) -- instead of failing:

```bash
python scripts/generate_demo_data.py
```

This only triggers when stdin is an actual terminal; scripted/CI runs without `--connection-string`
fail fast with a clear error instead of hanging on a prompt nothing will answer.

Re-activate the same venv (`source .venv/bin/activate`) next time instead of recreating it. If
`pip install` times out partway through downloading `couchbase` (`ReadTimeoutError` fetching
`files.pythonhosted.org`) on a slow or corporate network, retry with a longer timeout rather than
assuming it's broken -- this is almost always the source-build fallback above, not a real failure:

```bash
pip install --default-timeout=120 --retries 10 -r scripts/requirements-demo.txt
```

For Capella, pass `--skip-bucket-create` (provision the `demo_retail` bucket/scope from the
Capella UI first) and a `couchbases://` connection string. `--register-with-agent` (on by default)
registers the seeded cluster with a running backend and immediately triggers an analysis pass, so
`docker compose up` followed by this script gets you straight to a populated Insights page.
Everything is tunable (`--customers`, `--orders`, `--iterations`, ...) -- run with `--help` for
the full list, and re-run any time (with `--skip-seed` to just add more query history) to build up
more history.

The script also sets the query service's `completed-threshold` to 0 so fast demo queries still
land in `system:completed_requests` -- without that, most of this data wouldn't be visible to the
agent at all (see the script's module docstring for why).

## Licensing note

The `couchbase:enterprise-*` image used for `agent-memory` is free to run for internal
development/testing/evaluation under Couchbase's Enterprise Free license. It converts to a paid
Enterprise subscription if used in production or if you engage Couchbase support -- see
https://www.couchbase.com/legal/agreements/ for the exact terms. Point `MEMORY_CB_*` at a Capella
database instead if that doesn't fit; the backend code doesn't care which one it's talking to.

## Approval workflow

`SAFE_AUTO` findings move through explicit states: `open` -> `sandbox_testing` ->
`pending_approval` -> `approved` -> `applied` (or `apply_failed`). Approval requires a name and an
explicit confirmation checkbox (`POST /api/findings/{id}/approve`); applying is a separate,
also-logged step (`POST /api/findings/{id}/apply`). `REQUIRES_CODE_CHANGE` findings have no
`suggested_action` and can never reach `apply()` -- their only terminal state is `suggested`.
Every approval, rejection, and apply is written to episodic memory regardless of outcome.

## Troubleshooting a build failure behind a corporate proxy

If `docker compose up --build` fails during `pip install` or `npm install` with something like
`SSLCertVerificationError: self-signed certificate in certificate chain`, your network is behind a
TLS-inspecting corporate proxy (Zscaler, Netskope, Palo Alto GlobalProtect, etc.). Each build
container verifies TLS against its own bundled CA store, not your Mac's system trust store, so it
doesn't trust the proxy's own certificate the way your browser does. `./scripts/setup-corporate-ca.sh`
exports the root CA(s) your Mac already trusts from the System keychain and drops a copy into
`certs/`, `backend/certs/`, `frontend/certs/`, `llm-service/certs/`, and `wasm-sandbox/certs/` --
one per Docker build context -- so every `RUN pip install` / `RUN npm install` step trusts it too.
It's a no-op on machines that don't need it, and the dropped certs are gitignored (machine-specific).

```bash
./scripts/setup-corporate-ca.sh
docker compose build --no-cache
docker compose up
```

On Linux, the script can't read a keychain -- ask IT for your org's proxy root CA (a `.pem`/`.crt`
file) and copy it manually to each of the five `certs/` directories above as `corporate-ca.crt`.

## Known limitations

- **Unused-index detection is a heuristic**, not a certainty: it flags an index whose name never
  appears in the observed query text over the lookback window. Confirm with `EXPLAIN` or a longer
  observation window before approving removal -- the UI says this next to the finding.
- **Resource-tier findings need Management REST (self-hosted EE) or Capella API credentials.**
  Without either, index/query findings still work fully; RAM-quota and CPU findings are skipped.
- **The sandbox's cost model is a coarse what-if signal** (estimated scan volume), not a
  substitute for the query planner's own cost-based optimizer -- it exists to sanity-check that a
  suggested index actually reduces estimated scan volume, not to predict exact latency.
- **The read/write role check is best-effort and self-hosted-EE-only.** It's a convenience note,
  not the enforcement mechanism -- see "Cluster access & permissions." A read-only cluster refuses
  `approve()`/`apply()` regardless of whether the role check ran, was skipped (Capella), or
  couldn't reach Management REST.
- **Support-bundle parsing is a structural-match heuristic, not a schema-aware parser** -- see
  "Support bundle uploads." It will miss data that's present but not in one of the shapes it looks
  for, and (rarely) could misclassify a JSON blob that happens to share field names with
  `system:completed_requests`/`system:indexes`. Always sanity-check bundle-derived findings the
  same way you'd check any heuristic finding before acting on them elsewhere.

# Customer-Held Controls for Data Sovereignty in Snowflake

Runnable demos and reference architectures that keep the most sensitive data under the **data owner's** control
while Snowflake stays the analytics platform. Each option puts a different thing in the customer's hands: the
**data**, the **key** or the **switch**.

> **Personal project by Kevin Keller**, who works at Snowflake. These are "art of the possible" examples for
> inspiration. They are not official Snowflake solutions, not reviewed or endorsed by Snowflake, not supported, and
> not production-hardened. Review them with your security and legal teams before you adapt anything.

**White paper (personal publication, PDF):** [whitepaper/customer-held-controls-for-data-sovereignty-in-snowflake.pdf](whitepaper/customer-held-controls-for-data-sovereignty-in-snowflake.pdf) · **Slides:** [docs/deck.pdf](docs/deck.pdf) · **Overview, threat model and decision guide:** [docs/00-overview.md](docs/00-overview.md)

## The options in one line each

| # | Option | In plain words | Docs | Code |
| --- | --- | --- | --- | --- |
| 1 | **On-demand tunnel** | The sensitive data stays in your building. Snowflake can reach it only while you open a time-limited connection, and nothing is cached. | [01-tunnel](docs/01-tunnel.md) | [opt1-tunnel/](opt1-tunnel) |
| 1b | **Tunnel from a notebook, own query engine** | A notebook on SPCS (a Snowflake Notebook, or Jupyter or marimo) reads through the tunnel with DuckDB, so no query result is stored and nothing reaches the result cache. You give up the warehouse's compute power and elasticity. Passing the user's identity through to the on-prem source is not covered yet. | [01-tunnel § 1b](docs/01-tunnel.md#option-1b--a-notebook-and-your-own-query-engine-bypassing-snowflakes-query-engine) | [opt1-tunnel/notebooks/](opt1-tunnel/notebooks) |
| 2 | **External key broker + step-up** | The data sits in Snowflake encrypted, and you hold the key. A user confirms with a phone code and a reason, your broker checks that this exact query is running, and only then releases the key. | [02-byok-keybroker](docs/02-byok-keybroker.md) | [opt2-keybroker/](opt2-keybroker) |
| 2b | **Direct key fetch (EAI)** | Same encrypted data, much simpler: a Python UDF fetches the key from your vault and forgets it when the query ends. Your vault sees the integration, not the person. | [02b-eai-key-cache](docs/02b-eai-key-cache.md) | [opt2b-warm-cache/](opt2b-warm-cache) |
| 2c | **Approve on your phone** | Between 2 and 2b: scan a QR code, enter a phone code and a reason, and get a key that works only in your current session and only for the running query. | [02c-device-flow-apikey](docs/02c-device-flow-apikey.md) | [opt2c-device-flow/](opt2c-device-flow) |
| 2d | **Key never enters Snowflake** | Same phone approval as 2c, but your broker never hands out the key: Snowflake sends the encrypted values, your broker decrypts them in AWS and sends back only what this approved query may see. Works the same way for external de-tokenization. | [02d-remote-decrypt](docs/02d-remote-decrypt.md) | [opt2c-device-flow/](opt2c-device-flow) |
| 3 | **Keep it separate** | The sensitive data never goes to Snowflake. You pull the non-sensitive part out, join locally, and only safe totals go back. | [03-local-join](docs/03-local-join.md) | [opt3-local-join/](opt3-local-join) |
| 4 | **Unlock only in your own app** | The data sits in Snowflake encrypted, but the key lives only in an app you run on your premises. Snowflake never sees the key or a readable value. | [05-client-decrypt](docs/05-client-decrypt.md) | [opt4-client-decrypt/](opt4-client-decrypt) |
| ★ | **Compute without unlocking** | For numbers such as salaries, Snowflake computes totals and averages on encrypted values. Only you can read the result. | [04-homomorphic](docs/04-homomorphic.md) | [bonus-homomorphic/](bonus-homomorphic) |
| + | **Synthetic data** | An artificial copy that keeps the segment-level insights and contains no real person, for sharing or data products. | [synthetic/README](synthetic/README.md) | [synthetic/](synthetic) |

## Pros and cons

| Option | Pros | Cons | Typical fit |
| --- | --- | --- | --- |
| **1 Tunnel** | Data is never stored in Snowflake.<br>Full Snowflake SQL and UX on live data.<br>The owner holds the on/off switch, with a TTL and audit. | Plaintext is in Snowflake while the tunnel is open.<br>Query results can be read back for 24 h with `RESULT_SCAN`. The notebook variant (1b) avoids this.<br>Speed depends on the network.<br>Needs an SPCS service, an agent and a lake. | Data that may not be stored in a foreign cloud but must stay queryable |
| **2 Key broker** | Full Snowflake UX through masking policies.<br>Per-person, per-query, time-boxed release with a justification.<br>Replays are refused. | Plaintext is in query memory during approved queries.<br>About 4 s per decrypting query (200 rows; plaintext 0.1 s).<br>Needs an AWS stack (KMS, Lambda, API Gateway, DynamoDB). | Highly sensitive columns that must stay in the platform under external key control |
| **2b Direct key fetch** | Minimal infrastructure.<br>The key is gone when the query ends. | The vault cannot tell who is asking or why.<br>No step-up unless you add it. | "The key stays with us" with little effort |
| **2c Phone approval** | Per-person, per-session approval.<br>Bound to the running query.<br>About 0.3 s extra per query. | Plaintext is in query memory during approved queries.<br>Relies on behaviour that is observed but not documented (the query reading its own query ID).<br>Needs an IdP and a broker. | Option 2 controls with a lighter stack |
| **2d Remote decrypt** | The key never enters Snowflake.<br>Same per-person, per-query approval as 2c.<br>The broker sees every batch it decrypts. | Plaintext crosses the network on the way back.<br>Cost grows with rows (one call per batch of up to 2,000).<br>Same observed-behaviour caveat as 2c. | "The key must never be in the platform", with Snowflake UX kept |
| **3 Separate** | The sensitive data never reaches Snowflake. | No Snowflake UX on the sensitive part.<br>Compute is limited by the local host. | Data that must never leave the premises |
| **4 Own app** | Snowflake only ever holds ciphertext.<br>The key stays on premises. | Users work in the app, not in Snowflake.<br>No pushdown on encrypted columns. | A small, trusted on-premises analyst group |
| **★ Homomorphic** | Arithmetic on ciphertext.<br>No key in Snowflake at all. | Arithmetic only.<br>Large ciphertexts and slow operations. | Salary and financial figures that only need sums and averages |
| **+ Synthetic** | Keeps the insights you model.<br>No real row is copied.<br>Plain SQL in Snowflake. | Keeps only what you model.<br>Rare groups lose detail.<br>Not an audited differential-privacy guarantee. | Test data, sharing with partners, data products with a published scorecard |

None of the options stops an authorised user from copying what they are allowed to see. The options can be
combined: native Snowflake controls for the bulk, Option 2, 2c or 2d for identifying columns, homomorphic encryption for
salaries, and Option 1 or 3 for the few datasets that may not be stored in the cloud.

**Key custody is not data custody.** "The key stays with us" (Options 2–2d) is a different requirement from "the data
stays with us" (Options 1, 3, 4). Decrypting upstream before loading keeps the key outside Snowflake too, but puts
plaintext in Snowflake from the first table on. Check which of the two a requirement actually means.

## Getting started

1. **Install the tools** listed under *Prerequisites* below, and check that `snow connection test -c <name>` works.
2. **Clone and configure:**

   ```bash
   git clone https://github.com/sfc-gh-kkeller/customer-held-controls-for-data-sovereignty-in-snowflake.git
   cd customer-held-controls-for-data-sovereignty-in-snowflake
   export SNOW_CONNECTION=<your snow connection name>
   export DEMO_USER=<your Snowflake user>
   ```

3. **Create the shared Snowflake objects** (database `SOVEREIGNTY_DEMO`, warehouse `SOV_WH`):

   ```bash
   snow sql -c $SNOW_CONNECTION -f setup/00_common.sql
   ```
4. **Start with a quick win that needs no AWS:**
   - **Option 3** (`cd opt3-local-join && pixi run setup-snowflake && pixi run lab`): shows a local join and
     that Snowflake's history holds no sensitive data. About 5 minutes.
   - **Synthetic data** (the three scripts in `synthetic/`): shows which insights survive. About 2 minutes.
5. **Move on to the key-control options:** Option 2 needs an AWS account; Options 2c and 2d build on it. Option 1 needs
   Docker and Snowpark Container Services. Follow the table under *Set up and run*.
6. **Run the full walkthrough** in Snowsight with [snowsight_demo.sql](snowsight_demo.sql) once the options you
   want are set up. The block at the top lists what to start on the laptop first.

Each option's folder has its own README with details and troubleshooting.

## Prerequisites

**Accounts**

- A Snowflake account (Enterprise or higher; Option 1 and 1b need Snowpark Container Services) and a role that can
  create databases, roles, users, integrations, network policies and compute pools (`ACCOUNTADMIN` in the demo).
- For Options 2, 2b, 2c and 2d: an AWS account where you can deploy a CloudFormation stack (KMS, Lambda, API Gateway,
  DynamoDB, Secrets Manager). The stack deploys to `eu-central-1`.

**Tools and which options need them**

| Tool | Needed for | Why |
| --- | --- | --- |
| [Snowflake CLI](https://docs.snowflake.com/developer-guide/snowflake-cli/index) (`snow`) 3.x | all | runs the SQL and setup scripts |
| [pixi](https://pixi.sh) | all | creates each option's Python environment and runs its tasks |
| Git, Bash, OpenSSL | all | clone; setup scripts; key pairs |
| [uv](https://docs.astral.sh/uv/) | 2, 2c, 2d | Python environment for the CDK app |
| Node.js 22 or 24 LTS | 2, 2c, 2d | `npx aws-cdk`; Marp for rebuilding the deck |
| [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) v2 | 2, 2b, 2c, 2d | log in and deploy the stack |
| Docker (running) | 1, 2, 2c, 2d | Option 1 images and on-prem lake; CDK Lambda bundling |

Each option has its own `pixi.toml`, so there is nothing to install per option: the first `pixi run …` in a folder
builds that folder's environment.

**Install on macOS** (Homebrew):

```bash
curl -fsSL https://pixi.sh/install.sh | sh          # pixi (restart the shell afterwards)
brew install snowflake-cli uv node@22 awscli openssl git
brew install --cask docker                           # then start Docker Desktop
```

**Install on Linux** (Debian/Ubuntu shown):

```bash
curl -fsSL https://pixi.sh/install.sh | sh
curl -LsSf https://astral.sh/uv/install.sh | sh
uv tool install snowflake-cli                        # provides `snow`
sudo apt-get install -y git openssl docker.io nodejs npm   # or Node 22 from nodesource / nvm
# AWS CLI v2: see the AWS install guide linked above
```

On Windows, use WSL2 with the Linux steps.

**Configure and check**

```bash
snow connection add                                  # or edit ~/.snowflake/config.toml
snow connection test -c <your connection name>
aws login                                            # or aws configure sso / aws sso login
aws sts get-caller-identity
docker info >/dev/null && echo "docker ok"
```

Configuration is read from the environment; nothing account-specific is stored in the repository:

```bash
export SNOW_CONNECTION=<your snow connection name>   # default: "default"
export DEMO_USER=<your Snowflake user>               # user enrolled for TOTP step-up (Options 2 / 2c / 2d)
```

**Known issues**

- **Network policies:** if your account has an account-level network policy, the setup scripts give the demo
  service users their own policies. Pass the IPs your laptop or on-prem agent connects from with
  `ONPREM_IP=a.b.c.d/32` (Option 1) or `APP_IP=a.b.c.d/32` (Option 4).
- **`aws login` credentials and Python:** credentials from `aws login` need the `awscrt` package, which the pixi
  environments do not include. If a client script fails with `Missing Dependency ... botocore[crt]`, export the
  session credentials first: `eval "$(aws configure export-credentials --format env)"`.
- **Node.js newer than 24:** CDK prints an "untested Node version" warning; silence it with
  `export JSII_SILENCE_WARNING_UNTESTED_NODE_VERSION=1`.

## Set up and run

Every option has its own setup. Keys, tokens and generated configuration are written to git-ignored folders
(`.secrets/`, `.streamlit/secrets.toml`, `infra/outputs.json`) and never leave your machine.

| Option | Set up | Run the demo |
| --- | --- | --- |
| 1 | `cd opt1-tunnel && pixi run setup && pixi run deploy` (see [opt1-tunnel/README.md](opt1-tunnel/README.md)). Allow more agent IPs with `ONPREM_IP=a.b.c.d/32,e.f.g.h/32` | `pixi run tunnel-up 15m "reason"`, then the Option 1 block in `snowsight_demo.sql`; `pixi run tunnel-down` |
| 1b | `snow sql -c $SNOW_CONNECTION -f opt1-tunnel/snowflake/20_notebook.sql` | Notebook `opt1-tunnel/notebooks/tunnel_direct_read.ipynb` (DuckDB reads through the tunnel; no query result stored) |
| 2 | `cd opt2-keybroker && ./setup.sh`, then `pixi run enroll` and scan the QR code | `pixi run demo`, or the Option 2 block in `snowsight_demo.sql` with `pixi run portal` |
| 2b | After Option 2 (it grants to Option 2's roles): `snow sql -c $SNOW_CONNECTION -f opt2b-warm-cache/snowflake/00_probe.sql`, then `01_probe_eai.sql` | The Option 2b block in `snowsight_demo.sql` |
| 2c | `cd opt2c-device-flow && ./setup.sh` (after Option 2) | The Option 2c block in `snowsight_demo.sql`, or `pixi run e2e` |
| 2d | Included in the 2c setup (`snowflake/30_remote_decrypt.sql`) | `SOV_DECRYPT_2D(salary, 'salary', $api_key)` after the 2c login; `python3 bench/run_all.py opt2d` |
| 3 | `cd opt3-local-join && pixi run setup-snowflake` | `pixi run lab` |
| 4 | `cd opt4-client-decrypt && ./setup_demo.sh` (more app IPs: `APP_IP=a.b.c.d/32,...`) | `pixi run app` → http://localhost:8501 |
| ★ | `cd bonus-homomorphic && pixi run deploy && pixi run load` | `pixi run demo` or `pixi run lab` |
| + | `snow sql -c $SNOW_CONNECTION -f synthetic/01_source.sql` and `02_generate.sql` | `snow sql -c $SNOW_CONNECTION -f synthetic/03_scorecard.sql` |

**Walkthrough:** [snowsight_demo.sql](snowsight_demo.sql) runs every option section by section in one Snowsight
worksheet, with the laptop steps listed at the top.

**Tests (offline):** `pixi run test` in `opt2-keybroker`, `opt2c-device-flow`, `opt4-client-decrypt` and `bonus-homomorphic`.

## Verified behaviour

These results come from the demo account. Results that are observed but not documented are marked as such in the docs.

| Option | Result |
| --- | --- |
| 1 | The join works with the tunnel up; with it down the view returns no rows (the function returns the agent's connection error as a value, not a SQL error). Repeated runs go through the tunnel each time (no result reuse). |
| 1b | The notebook reads and joins through the tunnel; with the tunnel closed the read fails. No SQL query touches the sensitive table. |
| 2 | Viewer sees ciphertext; analyst without step-up is denied; after step-up plaintext and aggregates; a replayed query ID is refused. |
| 2b | The key memo lives for one query (one fetch per parallel process), not across queries, sessions or roles. |
| 2c | 45/45 correct bindings to the running query; one call-back per query; about 0.3 s extra. |
| 2d | All 200 values decrypted in AWS and equal to 2c; forged API key returns NULL; the strict variant raises when denied. The key never enters Snowflake. |
| 3 | Local join of 200 rows; only k-anonymous aggregates are written back; Snowflake history shows no sensitive data. |
| 4 | 200 rows encrypted client-side; the app decrypts them locally. |
| ★ | 21 tests pass; three notebooks run without errors; encrypted sums match plaintext. |
| + | Segment averages within 5.2 % on average; correlations and the three-way effect kept; no exact copies of real rows. |

## Performance

`python3 bench/run_all.py` runs the same workload (average salary per department, 200 employees) through every
option that can run unattended, each in its own pixi environment, and writes [bench/RESULTS.md](bench/RESULTS.md).
Run a subset with `python3 bench/run_all.py opt1 opt2c`. Option 1b runs headless as a Code Bundle
(`opt1-tunnel/snowflake/21_code_bundle.sql`, then `python3 bench/run_all.py opt1b`).

| Option | p50 in the demo setup |
| --- | --- |
| Plaintext baseline | 0.1 s |
| 1 Tunnel (live join) | 0.3 s |
| 1b Notebook + DuckDB through the tunnel | 0.04 s inside the notebook |
| 2 Key broker | 4.1 s (14.6 s before the call-back fix) |
| 2b EAI key fetch | 2.3 s |
| 2c Device flow | 2.8 s |
| 2d Remote decrypt | 4.0 s (2c on the same data: 2.9 s) |
| 3 Local join (incl. write-back) | 2.3 s |
| 4 Client decrypt | 0.1 s |
| ★ Homomorphic sum | 2.9 s (packed: 1.5 s) |

Single demo account and laptop, a few repetitions each: orders of magnitude, not a sizing guide.

## Teardown

```bash
(cd opt1-tunnel && pixi run tunnel-down && pixi run stop)
(cd opt2-keybroker/infra && npx aws-cdk destroy)
snow sql -c $SNOW_CONNECTION -q "DROP DATABASE SOVEREIGNTY_DEMO"   # removes everything Snowflake-side
# account-level objects (roles, integrations, compute pools, warehouses) are named SOV_* and can be dropped too
```

## Repository layout

```
docs/                 overview, one document per option, slide deck, diagrams (Mermaid sources + SVG)
setup/                shared Snowflake objects (database, warehouse) used by every option
snowsight_demo.sql    one-worksheet walkthrough of every option
shared/sample_data/   deterministic synthetic HR dataset used by the options
opt1-tunnel/          SPCS tunnel (sidecar + iceberg-agent), on-prem agent + tunnel_ctl, Lakekeeper + RustFS, notebooks
opt2-keybroker/       client (encrypt/load, enroll, demo, portal QR), Lambdas (broker, step-up), CDK stack, SQL, tests
opt2b-warm-cache/     probe UDFs that measure how long a Python UDF key memo lives
opt2c-device-flow/    mock IdP (device flow), 2c/2d broker (key release + remote decrypt), EAI UDFs, query-binding benchmark, end-to-end test
opt3-local-join/      on-prem Jupyter notebook + Snowflake objects
opt4-client-decrypt/  on-prem Streamlit app: Snowflake → AES decrypt → DuckDB cache (OIDC/RBAC)
bonus-homomorphic/    Pyfhel/SEAL homomorphic encryption on Snowflake (vendored Pyfhel with patches)
synthetic/            segment-based synthetic data generator in SQL + insight and privacy scorecard
bench/                performance benchmarks for every option (run_all.py) and RESULTS.md
```

## Security notes

- **Demo shortcuts.** Each is marked in the code:
  - TOTP secrets are stored locally under `.secrets/`;
  - the mock IdP stands in for a real identity provider;
  - Option 4 offers a development sign-in;
  - the on-prem lake uses default credentials.
- **Key cache in the brokers.** The Lambdas keep the KMS-unwrapped data key in memory and renew it every 30 minutes
  (`KEY_CACHE_TTL_S`), so KMS is not called per request; a revoked KMS grant takes effect within that window.
- **Before using any of this beyond a demo,** replace each shortcut with your enterprise IdP, MFA and a managed vault.
- **Report security issues** privately to the author rather than in a public issue.

## Licences

`opt4-client-decrypt/` is MIT-licensed and `bonus-homomorphic/` is Apache-2.0 (with the vendored Pyfhel and SEAL
under their own licences; see its `NOTICE`). See the `LICENSE` file in each folder.

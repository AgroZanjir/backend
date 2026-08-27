# Agro Zanjir Digital — backend

Django 6 + DRF. The middle band of the architecture: the lot registry, the
event log, the six clusters that hang off it, and the ports that make all of it
legible to banks, insurers, carriers and customs.

## Run it

```sh
cp .env.example .env            # optional; the shell boots without one
.venv/bin/python manage.py migrate
.venv/bin/python manage.py seed_reference     # capabilities, roles, org types, checks
.venv/bin/python manage.py seed_demo          # the pilot demonstration dataset
.venv/bin/python manage.py seed_accounts      # the people, and their passwords
.venv/bin/python manage.py runserver 8000
```

`seed_accounts` creates the pilot's people - three to six roles at each kind of
organisation - and prints their passwords once. It is idempotent and never
deletes anything; `--rotate` issues new passwords, `--password X` sets one
shared password for a demonstration on a laptop.

`seed_reference` is not demo data — it is the product's own catalogues (ten
capabilities, thirty-seven roles, thirteen organisation types, six verification
checks) and every deployment needs it. `seed_demo` is the pilot dataset the
panels were designed against; `--reset` replaces it.

| URL | What it is |
| --- | --- |
| `/api/v1/health/` | liveness + database check (open, no auth) |
| `/api/docs/` | Swagger UI over the generated OpenAPI schema |
| `/api/schema/` | the schema itself — the web client generates its API layer from this |
| `/admin/` | the operator admin, which is also the manual-adapter surface |

```sh
.venv/bin/python manage.py test          # 51 tests
.venv/bin/python manage.py spectacular --file schema.yml   # dump the spec
```

## The model

```
apps/common/      base models (UUID pk, timestamps, money), notifications
apps/registry/    ORIGIN     — User, Party, OrganisationType, Capability, Role,
                               Membership, VerificationCheck, PartyVerification,
                               Product, Farm, ProductionPlan
apps/lots/        THE SPINE  — Lot, LotEvent, LotRelation
apps/quality/     QUALITY    — QcRecord, PilotTrial, TrialArm, TrialObservation
apps/storage/     STORAGE    — Facility, StorageZone, GateArrival,
                               StoragePlacement, ConditionReading,
                               ConditionExcursion
apps/commercial/  COMMERCIAL — OfftakeContract, ExportContract, PriceTerm,
                               Shipment, ShipmentLine
apps/finance/     FINANCE    — FinanceApplication, Encumbrance, Policy, Claim,
                               SettlementAllocation
apps/documents/   the document vault — Document
apps/governance/  AuditEntry, DataGrant
apps/panels/      no tables: the cross-cluster read composition the panels need
ports/            LenderPort, InsurerPort, CarrierPort, CustomsPort, SensorPort
```

Each `models.py` opens with what its cluster owns and why its shapes are what
they are. Four of the root README's rules are enforced in code, not by
convention:

1. **The log is the truth.** `LotEvent.save()` refuses a second write,
   `delete()` refuses outright, and each row's SHA-256 covers the previous
   row's — so an edit that dodges the ORM still breaks the chain from that
   point on. `GET /api/v1/lots/{code}/verify-chain/` recomputes it.
2. **Encumbrance is an overlay, not a status.** There is no `pledged` in
   `Lot.Status`. `finance` registers a guard with the spine in `apps.py`, the
   spine asks every registered guard before a dispatch, and `lots` never
   imports `finance`.
3. **Clusters never reference each other.** `apps/common/tests/test_architecture.py`
   walks every model's foreign keys and fails the build if one crosses. Where a
   cluster genuinely needs the other side — a claim built from an excursion, a
   policy over a shipment — it holds a code, not a key.
4. **One base unit, and money is minor units plus a currency.** Grams
   throughout; `MoneyModel` carries `amount_minor`, `currency` and the FX rate
   snapshotted on the row that used it.

## The API

Each cluster owns its own writes. `panels` owns the reads that legitimately
span clusters — a lot passport is quality, storage and finance on one page —
and it is the only module allowed to import across them.

| Path | What it does |
| --- | --- |
| `POST /api/v1/auth/oneid/` | sign in (OneID; `stub` adapter until it is connected) |
| `POST /api/v1/auth/password/` | sign in with a username and password |
| `POST /api/v1/auth/refresh/` | exchange the httpOnly cookie for an access token |
| `POST /api/v1/auth/logout/`, `GET /auth/me/`, `GET /auth/personas/` | the rest of the session |
| `POST /api/v1/lots/` | register at the gate (idempotent) |
| `POST /api/v1/lots/{code}/{grade,split,reserve,dispatch,write-off}/` | the lifecycle |
| `GET  /api/v1/lots/{code}/verify-chain/` | recompute the hash chain |
| `POST /api/v1/quality/qc-records/`, `/quality/trials/{code}/observations/` | evidence |
| `POST /api/v1/storage/placements/`, `/readings/`, `/arrivals/{id}/weigh/` | the hub |
| `POST /api/v1/commercial/shipments/`, `/{code}/depart/`, `/exports/{code}/declaration/` | logistics and the border |
| `POST /api/v1/finance/applications/`, `/liens/`, `/claims/` (+ decisions) | money and risk |
| `POST /api/v1/documents/` | the vault |
| `GET  /api/v1/panels/…` | everything the eight panels read |
| `GET  /api/v1/panels/public/lots/{code}/`, `/public/trials/{code}/` | open by design |

**Permissions are capability-based.** A view names the capability it needs —
`requires("capture")` — and never a role, so adding the thirty-eighth role
changes no view. Querysets are narrowed by `party_scope` before anything is
serialised, and a lot is visible through four routes: you own it, your facility
is holding it, you have a lien over it, or you insure it. Reads of a passport
are written to the audit log; "who looked at my lot" is a question this
platform has to answer.

## Sessions

OneID is the identity source and the platform stores no passwords for anyone
who comes through it. The access token is returned in the body for the client
to hold in memory; the refresh token is set as an **httpOnly, SameSite cookie**
scoped to `/api/v1/auth/` that JavaScript cannot read. A token in localStorage
is a finding in any bank's security review, and this platform faces banks.

`ONEID_ADAPTER=stub` resolves a seeded persona instead of a state identity and
returns `adapter: "stub"` in the session, which the sign-in screen prints. The
live adapter replaces one function in `apps/registry/auth.py`; no caller
changes. `POST /auth/password/` is the other door, and not a lesser one: it is how a
pilot runs before OneID is connected at all, and how the platform stays usable
when the state identity provider is down - which it will be. Those accounts are
created by `manage.py seed_accounts`, and the platform stores a hash, never the
password.

## Ports

```python
from ports import get_port

get_port("lender").submit_application(application_id, amount_minor=...)
```

Callers name a port, never an adapter. Each ships with a manual adapter: the
request is recorded, `PortResult.state` comes back `pending_operator`, and an
operator resolves it from the admin after a phone call. `PortResult.accepted`
is `True` in that case — pending is success-so-far, not failure. Finance
applications, liens, claims, bookings, declarations and sensor batches all go
through them today.

## Database

PostgreSQL 16 is the target — JSONB for per-product QC specs and event
payloads, row-level security for party-scoped access, real transactions for
anything touching money or liens. Set `DATABASE_URL` to point at it. With that
unset the shell falls back to SQLite so it runs before a database has been
provisioned; do not ship on that. Two consequences of supporting both are
visible in the code and are commented where they are: excursions are matched to
lots in Python rather than with a `JSONField__contains` lookup, and
`ConditionReading` carries a plain integer key — it will outnumber every other
table a thousand to one and is the one to partition (or move to TimescaleDB)
first.

## Deployment

The backend serves the API and its own static files; the web client is a static
bundle that any web server can host. Nothing here needs a container to run.

```sh
# 1. the API
cp .env.example .env                      # then set the deployment block
#    DEBUG=False, a real DJANGO_SECRET_KEY, ALLOWED_HOSTS, DATABASE_URL,
#    CORS_ALLOWED_ORIGINS and CSRF_TRUSTED_ORIGINS
.venv/bin/python manage.py check --deploy # must be clean before anything else
.venv/bin/python manage.py migrate
.venv/bin/python manage.py collectstatic --noinput
.venv/bin/python manage.py seed_reference
.venv/bin/python manage.py seed_demo      # pilot dataset; skip for a real one
.venv/bin/python manage.py seed_accounts  # prints the passwords once
.venv/bin/gunicorn config.wsgi:application --bind 127.0.0.1:8000 --workers 3

# 2. the web client
cd ../web
VITE_API_BASE_URL=https://api.agrozanjir.uz npm run build   # -> web/dist
```

`web/dist` is served as static files, with **every unknown path rewritten to
`index.html`** - it is a single-page app, and a reader who reloads on
`/showroom/melon` gets a 404 from the web server otherwise.

Four things that are easy to get wrong, and what each looks like when it is:

| Setting | Wrong looks like |
| --- | --- |
| `VITE_API_BASE_URL` | Vite inlines it at **build** time; changing the API's address means building again |
| `CORS_ALLOWED_ORIGINS` | every request fails in the browser and succeeds in `curl` |
| `CSRF_TRUSTED_ORIGINS` | reads work, writes come back 403 |
| `REFRESH_COOKIE_SAMESITE` | signing in works, reloading the page signs you out - the cookie is `Lax` and the two hosts are not the same site |

With `DEBUG=False` the security settings switch on by themselves: TLS redirect,
HSTS, secure cookies, `X-Frame-Options: DENY`, and the proxy header Django
needs to know a request arrived over HTTPS. `manage.py check --deploy` is the
gate - it must report no issues.

**PostgreSQL, not SQLite.** Set `DATABASE_URL`. The fallback exists so the
shell boots before a database is provisioned; two tables are written on every
lot movement and SQLite locks the file for each one.

## Not built yet

- **Live port adapters.** Every port runs its manual adapter. The seam is the
  deliverable; the integrations are phase 3.
- **Real OneID.** The stub resolves a seeded persona and says so.
- **Offline field capture.** The gate endpoint is idempotent, which is the half
  of it that belongs here; the PWA with its IndexedDB queue is not built.
- **Row-level security.** Scoping is enforced in the application layer only.

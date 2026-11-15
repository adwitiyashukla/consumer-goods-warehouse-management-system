# consumer-goods-warehouse-management-system

[![ci](https://github.com/adwitiyashukla/consumer-goods-warehouse-management-system/actions/workflows/ci.yml/badge.svg)](https://github.com/adwitiyashukla/consumer-goods-warehouse-management-system/actions/workflows/ci.yml)

This is a warehouse management system (WMS) for a consumer goods distribution center. It receives trucks, puts pallets away, allocates orders by expiry date, plans pick waves, hands pickers and forklift drivers their next task, books dock doors and ships trailers. The backend is FastAPI on Postgres and the dashboard is React.

I don't have a real warehouse to run it in, so the repo comes with a simulator. It plays a full working day against the real API, with 216 orders and 20 inbound trucks, and I use it to compare picking policies. All the numbers below are from my laptop (HP OmniBook 5, Core 5 120U, 8 GB RAM).

![Floor view](screenshots/floor.png)

The floor view shows the pick slots colored by how fast the product moves, with one pick tour drawn on top. The panel on the right walks the same tour with three routing methods.

## What it does

| Area | What happens |
|---|---|
| Receiving | A truck is announced with an ASN. Receiving it creates one stock record per pallet, with its lot and expiry date. |
| Putaway | Every pallet gets a forklift task to the free reserve slot closest to its product's pick slot. |
| Slotting | Products are ranked by how often they are ordered. The fastest ones get the pick slots closest to the depot. |
| Allocation | An order takes the stock that expires first (FEFO), but only from lots that still have the shelf life its customer asks for. |
| Waves | The orders for one trailer are planned together. They are batched into pick tours and every tour gets a route. |
| Picking | A picker asks for work and gets a whole tour. Full pallets skip the pick slots and go from reserve to the dock by forklift. |
| Replenishment | When a pick slot runs low, a forklift brings down the oldest pallet from reserve. |
| Cycle counts | Drivers count random pallets. A wrong count corrects the stock and leaves a record. |
| Yard | Trucks have appointments. Doors are assigned ahead of time and every truck goes through arrived, docked, done and departed. |
| Shipping | A trailer leaves at its departure time with the orders that are ready. An order that is picked after that ships late. |

```mermaid
flowchart LR
    truck[Inbound truck] --> receive[Receive ASN]
    receive --> putaway[Putaway]
    putaway --> reserve[(Reserve racks)]
    reserve -- replenishment --> slots[(Pick slots)]
    order[Order] --> allocate[Allocate by expiry]
    allocate --> wave[Wave: batch and route]
    wave --> tour[Pick tour]
    slots --> tour
    tour --> door[Dock door]
    reserve -- full pallets --> door
    door --> trailer[Outbound trailer]
```

## The warehouse and the day

Everything is generated from a seed, and every size is a setting in `config.toml`.

- The building has 16 aisles with 20 bays on each side and 5 levels, so 3,200 rack locations. The floor level is 640 pick slots and the four levels above are reserve. There are 3 inbound doors and 4 outbound doors.
- 480 products in 8 categories like laundry, paper and baby care. Demand is skewed the way it is in a real warehouse, and the top 20% of the products get 68% of the order lines.
- 36 customers on 6 delivery lanes. Every customer wants between 90 and 240 days of shelf life left on what they receive.
- On the simulated day 216 orders come in with 5,950 lines and 36,619 cases. 20 trucks deliver 26 pallets each, and 24 trailers leave at four departure times.
- 7 pickers and 4 forklift drivers do the work.

## How it works

### Routing

The pick area is one block of parallel aisles with a cross aisle at the front and one at the back. For that shape the shortest tour can be found exactly with the dynamic program from Ratliff and Rosenthal (1983). It goes over the aisles from left to right and only keeps a few states per aisle, so it is fast enough to run for every tour. I also wrote the two classic heuristics to compare against. S-shape walks every aisle that has a pick from end to end. Largest gap enters an aisle from both ends and skips the longest stretch without a pick.

### Batching

An order that does not fit on one cart (3.2 cubic meters) is split first. First come first served fills carts in the order the orders arrived. The savings method from Clarke and Wright starts with one tour per order and merges the pairs that save the most walking, up to 4 orders per cart.

### Allocation

For every order line the stock is sorted by expiry date, and lots that would reach the customer with too little shelf life are left out. A line that needs a full pallet or more takes whole untouched pallets from reserve first. The rest comes from the pick slot, and after that from reserve, which creates a replenishment task so the picker still finds the cases in the pick slot. Cases that cannot be covered are recorded as short.

### Many workers at once

- Every pallet row has a database check that the allocated cases never go above the cases on hand.
- Allocation always locks stock in the same order, so two orders that want the same products cannot deadlock.
- Pickers and drivers claim work with `FOR UPDATE SKIP LOCKED`, so two workers never get the same tour or task.
- Completing a task accepts an `Idempotency-Key` header. A scanner that sends the same scan twice gets the first answer back and the pick is not counted twice.
- A partial unique index allows only one open replenishment per pallet.

### Dock doors

Doors are booked ahead of time. The trucks are sorted by their appointment and each one gets the door that is free first, with 10 minutes between two trucks at the same door. A truck that arrives while its door is still busy waits in the yard.

![Docks view](screenshots/docks.png)

### The simulator

The simulator keeps a clock and a queue of events. Every event calls the real HTTP API with the simulated time, for example a truck arriving, an order coming in or a picker finishing a stop. Walking and forklift times come from the distances in the layout and the speeds in `config.toml`. The same seed always gives the same day, so the results below are the same on any machine.

An order counts as on time when it is on its trailer at departure, and as in full when every case on it was picked.

## Results

`wms bench` plays the full day five times with different policies.

| Routing | Batching | Slotting | On time in full | Tours | Walked | Picker hours | Stops per hour |
|---|---|---|---|---|---|---|---|
| S-shape | first come first served | by velocity | 94.4% | 383 | 145.1 km | 89.5 | 57.0 |
| largest gap | first come first served | by velocity | 95.4% | 383 | 128.6 km | 88.8 | 57.5 |
| optimal | first come first served | by velocity | 96.3% | 383 | 114.5 km | 85.7 | 59.6 |
| **optimal** | **savings** | **by velocity** | **96.3%** | **326** | **102.0 km** | **84.0** | **60.5** |
| optimal | savings | random | 90.7% | 322 | 168.1 km | 98.1 | 58.6 |

### The optimal router walks 21% less than S-shape

The rows above change the tours too, so I also routed the same 326 tours with each method.

| Router | Walked |
|---|---|
| S-shape | 129.4 km |
| largest gap | 114.8 km |
| optimal | 102.0 km |

The optimal router walks 21% less than S-shape and 11% less than largest gap.

### Savings batching needs fewer tours

With the optimal router, first come first served needs 383 tours and savings needs 326. The pickers walk 11% less.

### Fast movers near the depot matter the most

With the products in random slots the pickers walk 168.1 km. Putting the fast movers near the depot cuts that by 39%, which is more than routing and batching give together.

### What decides on time in full

Three products sell out during the day, so 8 orders are short in every run (65 cases out of 36,619). That is why on time in full stops at 96.3%. The rest depends on how long the walking takes. 5 orders miss their trailer with S-shape, 3 with largest gap, 1 with the optimal router and none once savings batching is on. With random slots 13 orders miss it.

### Load test

`load/locustfile.py` puts 40 users on the API for two minutes. Planners create orders and release waves, supervisors read the dashboard, and pickers and drivers claim and finish tasks. Against the Docker Compose stack on my laptop that was 5,305 requests at 45 per second, with a median of 56 ms, a 95th percentile of 110 ms and no failed requests. After the run no pallet had more cases allocated than it holds.

## Run it

With Docker:

```
docker compose up --build
```

Then open http://localhost:8000. The server plays the simulated day up to 13:00 in the background, which takes a few minutes on my laptop, and the dashboard fills up while it runs. The API docs are at http://localhost:8000/docs.

Without Docker you need Python 3.12 or newer and Node 22 or newer. Postgres still comes from Docker Compose, on port 5434:

```
docker compose up -d db
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev,load]"
cd web
npm ci
npm run build
cd ..
wms serve --demo
```

On Linux or macOS activate with `source .venv/bin/activate` instead.

Play a day from the command line, or compare the policies. Both start from an empty database, so they clear what the server loaded:

```
wms sim --routing sshape --batching fcfs
wms bench
```

Run the load test against a running server:

```
locust -f load/locustfile.py --headless -u 40 -r 10 -t 2m --host http://localhost:8000
```

## Deploy

- `deploy/k8s` has the Kubernetes manifests for Postgres, two replicas of the app and a smoke job. CI starts a kind cluster, deploys them and lets the smoke job play the morning through the service.
- `deploy/azure` has Terraform for Azure Container Apps with a Postgres Flexible Server.

## Config

All settings live in `config.toml`, from the layout and the catalog to the truck schedule, the picking policies and the simulator. The file is checked when it loads, and a wrong value or an unknown key stops the program with a message. `DATABASE_URL` overrides the database setting.

## Tests

```
set TEST_DATABASE_URL=postgresql://wms:wms@localhost:5434/wms
pytest -q
```

On Linux or macOS use `export` instead of `set`.

There are 41 tests. The optimal router is checked against brute force on random small warehouses, and the two heuristics can never beat it. Allocation, batching and door scheduling have property tests with Hypothesis. The API tests follow an order from creation to the trailer and a truck through the yard. The concurrency tests place 40 orders for the same product at once and let 8 pickers ask for work in the same moment. One test plays a small day twice and checks that both runs give the same report and that no stock is oversold.

The web app has 7 Vitest tests and 4 Playwright tests that click through the dashboard. The Playwright tests need a browser once (`npx playwright install chromium`) and they start the server themselves:

```
cd web
npm test
npx playwright test
```

GitHub Actions runs ruff, mypy in strict mode, the tests on Python 3.12 and 3.13, the web tests, the Playwright tests, the kind cluster and `terraform validate`. When all of that passes it pushes the Docker image to GHCR.

## Project layout

```
src/wms/
  config.py       settings and checks
  models.py       the tables
  schemas.py      request bodies
  db.py           engine, sessions, migrations
  deps.py         shared request dependencies
  layout.py       warehouse geometry and distances
  routing.py      S-shape, largest gap and the optimal router
  batching.py     first come first served and savings
  slotting.py     pick slots by velocity
  allocation.py   FEFO with the shelf life rule
  inbound.py      ASNs, receiving, putaway
  outbound.py     orders, waves, shipping
  tasks.py        tours, tasks, replenishment, cycle counts
  yard.py         appointments and dock doors
  reports.py      KPIs and the data behind the dashboard
  idempotency.py  safe retries for scanners
  generator.py    the catalog, the stock and the plan for the day
  sim.py          the simulator
  bench.py        policy comparison
  cli.py          command line
  app.py          FastAPI app
  migrations/     Alembic
tests/
web/              React dashboard
load/             Locust load test
deploy/           Kubernetes and Azure
screenshots/      pictures for this README
config.toml       every setting
```

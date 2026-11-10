import random
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from locust import HttpUser, between, task


class Planner(HttpUser):
    weight = 1
    wait_time = between(1, 3)

    def on_start(self) -> None:
        self.skus = [row["code"] for row in self.client.get("/api/skus?limit=500").json()]
        orders = self.client.get("/api/orders?limit=500").json()
        self.customers = sorted({row["customer"] for row in orders})

    @task(4)
    def order(self) -> None:
        lines = [{"sku": sku, "cases": random.randint(1, 12)} for sku in random.sample(self.skus, 8)]
        body = {
            "code": f"LOAD-{uuid.uuid4().hex[:12]}",
            "customer": random.choice(self.customers),
            "ship_by": (datetime.now(UTC) + timedelta(hours=4)).isoformat(),
            "lines": lines,
        }
        self.client.post("/api/orders", json=body)

    @task(1)
    def wave(self) -> None:
        with self.client.post("/api/waves", json={"release": True}, catch_response=True) as response:
            if response.status_code == 409:
                response.success()


class Supervisor(HttpUser):
    weight = 2
    wait_time = between(1, 2)

    @task(3)
    def kpis(self) -> None:
        self.client.get("/api/kpis")

    @task(2)
    def waves(self) -> None:
        self.client.get("/api/waves")

    @task(2)
    def routes(self) -> None:
        tours = self.client.get("/api/tours?limit=50").json()
        if tours:
            tour = random.choice(tours)["id"]
            self.client.get(f"/api/tours/{tour}/routes", name="/api/tours/[id]/routes")


class Worker(HttpUser):
    abstract = True
    role = "picker"

    def on_start(self) -> None:
        self.name = f"{self.role[0].upper()}{uuid.uuid4().hex[:6]}"

    def finish(self, task: dict[str, Any]) -> None:
        body: dict[str, Any] = {"worker": self.name}
        if task["kind"] == "count":
            body["counted"] = task["cases"]
        self.client.post(f"/api/tasks/{task['id']}/complete", json=body, name="/api/tasks/[id]/complete")


class Picker(Worker):
    weight = 4
    wait_time = between(0.5, 1.5)
    role = "picker"

    @task
    def work(self) -> None:
        body = {"worker": self.name, "role": "picker"}
        tour = self.client.post("/api/tasks/next", json=body).json()["tour"]
        for step in tour["tasks"] if tour else []:
            self.finish({**step, "kind": "pick"})


class Driver(Worker):
    weight = 2
    wait_time = between(0.5, 1.5)
    role = "driver"

    @task
    def work(self) -> None:
        body = {"worker": self.name, "role": "driver"}
        job = self.client.post("/api/tasks/next", json=body).json()["task"]
        if job is not None:
            self.finish(job)

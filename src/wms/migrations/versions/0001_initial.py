import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "customers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=60), nullable=False),
        sa.Column("lane", sa.Integer(), nullable=False),
        sa.Column("min_shelf_life_days", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.create_table(
        "events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("ref", sa.String(length=32), nullable=False),
        sa.Column("data", postgresql.JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_events_at"), "events", ["at"], unique=False)
    op.create_table(
        "idempotency_keys",
        sa.Column("key", sa.String(length=80), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("body", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_table(
        "locations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("aisle", sa.Integer(), nullable=True),
        sa.Column("bay", sa.Integer(), nullable=True),
        sa.Column("side", sa.String(length=1), nullable=True),
        sa.Column("level", sa.Integer(), nullable=True),
        sa.Column("x", sa.Double(), nullable=False),
        sa.Column("y", sa.Double(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.create_table(
        "appointments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=24), nullable=False),
        sa.Column("direction", sa.String(length=8), nullable=False),
        sa.Column("carrier", sa.String(length=40), nullable=False),
        sa.Column("lane", sa.Integer(), nullable=True),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_minutes", sa.Double(), nullable=False),
        sa.Column("door_id", sa.Integer(), nullable=True),
        sa.Column("scheduled_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("gate_in_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("docked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("done_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("gate_out_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["door_id"],
            ["locations.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.create_table(
        "skus",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column("description", sa.String(length=80), nullable=False),
        sa.Column("category", sa.String(length=16), nullable=False),
        sa.Column("cases_per_pallet", sa.Integer(), nullable=False),
        sa.Column("case_volume_m3", sa.Double(), nullable=False),
        sa.Column("case_weight_kg", sa.Double(), nullable=False),
        sa.Column("shelf_life_days", sa.Integer(), nullable=False),
        sa.Column("velocity", sa.String(length=1), nullable=True),
        sa.Column("pick_location_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["pick_location_id"],
            ["locations.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
        sa.UniqueConstraint("pick_location_id"),
    )
    op.create_table(
        "asns",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=24), nullable=False),
        sa.Column("origin", sa.String(length=40), nullable=False),
        sa.Column("appointment_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["appointment_id"],
            ["appointments.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.create_table(
        "lots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sku_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=24), nullable=False),
        sa.Column("produced_on", sa.Date(), nullable=False),
        sa.Column("expires_on", sa.Date(), nullable=False),
        sa.ForeignKeyConstraint(
            ["sku_id"],
            ["skus.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sku_id", "code"),
    )
    op.create_table(
        "waves",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=24), nullable=False),
        sa.Column("appointment_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("routing", sa.String(length=12), nullable=False),
        sa.Column("batching", sa.String(length=8), nullable=False),
        sa.Column("planned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["appointment_id"],
            ["appointments.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.create_table(
        "asn_lines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("asn_id", sa.Integer(), nullable=False),
        sa.Column("sku_id", sa.Integer(), nullable=False),
        sa.Column("lot_code", sa.String(length=24), nullable=False),
        sa.Column("produced_on", sa.Date(), nullable=False),
        sa.Column("expires_on", sa.Date(), nullable=False),
        sa.Column("cases_expected", sa.Integer(), nullable=False),
        sa.Column("cases_received", sa.Integer(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(
            ["asn_id"],
            ["asns.id"],
        ),
        sa.ForeignKeyConstraint(
            ["sku_id"],
            ["skus.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_asn_lines_asn_id"), "asn_lines", ["asn_id"], unique=False)
    op.create_table(
        "orders",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=24), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("appointment_id", sa.Integer(), nullable=True),
        sa.Column("wave_id", sa.Integer(), nullable=True),
        sa.Column("ship_by", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("picked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("shipped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("on_time", sa.Boolean(), nullable=True),
        sa.Column("in_full", sa.Boolean(), nullable=True),
        sa.ForeignKeyConstraint(
            ["appointment_id"],
            ["appointments.id"],
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
        ),
        sa.ForeignKeyConstraint(
            ["wave_id"],
            ["waves.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.create_index("ix_orders_appointment", "orders", ["appointment_id"], unique=False)
    op.create_index("ix_orders_status", "orders", ["status"], unique=False)
    op.create_index("ix_orders_wave", "orders", ["wave_id"], unique=False)
    op.create_table(
        "stock",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("lpn", sa.String(length=32), nullable=False),
        sa.Column("sku_id", sa.Integer(), nullable=False),
        sa.Column("lot_id", sa.Integer(), nullable=False),
        sa.Column("location_id", sa.Integer(), nullable=False),
        sa.Column("cases_on_hand", sa.Integer(), nullable=False),
        sa.Column("cases_allocated", sa.Integer(), server_default="0", nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.CheckConstraint(
            "cases_allocated >= 0 and cases_allocated <= cases_on_hand",
            name="allocated_within_on_hand",
        ),
        sa.CheckConstraint("cases_on_hand >= 0", name="on_hand_not_negative"),
        sa.ForeignKeyConstraint(
            ["location_id"],
            ["locations.id"],
        ),
        sa.ForeignKeyConstraint(
            ["lot_id"],
            ["lots.id"],
        ),
        sa.ForeignKeyConstraint(
            ["sku_id"],
            ["skus.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("lpn"),
    )
    op.create_index("ix_stock_location", "stock", ["location_id"], unique=False)
    op.create_index("ix_stock_sku_status", "stock", ["sku_id", "status"], unique=False)
    op.create_table(
        "tours",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("wave_id", sa.Integer(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("routing", sa.String(length=12), nullable=False),
        sa.Column("blocked", sa.Integer(), server_default="0", nullable=False),
        sa.Column("worker", sa.String(length=24), nullable=True),
        sa.Column("distance_m", sa.Double(), nullable=False),
        sa.Column("stops", sa.Integer(), nullable=False),
        sa.Column("cases", sa.Integer(), nullable=False),
        sa.Column("volume_m3", sa.Double(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("done_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["wave_id"],
            ["waves.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tours_status", "tours", ["status"], unique=False)
    op.create_index(op.f("ix_tours_wave_id"), "tours", ["wave_id"], unique=False)
    op.create_table(
        "order_lines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("sku_id", sa.Integer(), nullable=False),
        sa.Column("cases_ordered", sa.Integer(), nullable=False),
        sa.Column("cases_allocated", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cases_picked", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cases_shipped", sa.Integer(), server_default="0", nullable=False),
        sa.CheckConstraint(
            "cases_shipped <= cases_picked and cases_picked <= cases_allocated"
            " and cases_allocated <= cases_ordered",
            name="line_quantities",
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
        ),
        sa.ForeignKeyConstraint(
            ["sku_id"],
            ["skus.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_order_lines_order_id"), "order_lines", ["order_id"], unique=False)
    op.create_table(
        "allocations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("order_line_id", sa.Integer(), nullable=False),
        sa.Column("stock_id", sa.Integer(), nullable=False),
        sa.Column("cases", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=6), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.ForeignKeyConstraint(
            ["order_line_id"],
            ["order_lines.id"],
        ),
        sa.ForeignKeyConstraint(
            ["stock_id"],
            ["stock.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_allocations_order_line_id"),
        "allocations",
        ["order_line_id"],
        unique=False,
    )
    op.create_index(op.f("ix_allocations_stock_id"), "allocations", ["stock_id"], unique=False)
    op.create_table(
        "tasks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=8), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("wave_id", sa.Integer(), nullable=True),
        sa.Column("tour_id", sa.Integer(), nullable=True),
        sa.Column("seq", sa.Integer(), nullable=True),
        sa.Column("sku_id", sa.Integer(), nullable=True),
        sa.Column("stock_id", sa.Integer(), nullable=True),
        sa.Column("allocation_id", sa.Integer(), nullable=True),
        sa.Column("from_location_id", sa.Integer(), nullable=True),
        sa.Column("to_location_id", sa.Integer(), nullable=True),
        sa.Column("cases", sa.Integer(), server_default="0", nullable=False),
        sa.Column("counted", sa.Integer(), nullable=True),
        sa.Column("worker", sa.String(length=24), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("done_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["allocation_id"],
            ["allocations.id"],
        ),
        sa.ForeignKeyConstraint(
            ["from_location_id"],
            ["locations.id"],
        ),
        sa.ForeignKeyConstraint(
            ["sku_id"],
            ["skus.id"],
        ),
        sa.ForeignKeyConstraint(
            ["stock_id"],
            ["stock.id"],
        ),
        sa.ForeignKeyConstraint(
            ["to_location_id"],
            ["locations.id"],
        ),
        sa.ForeignKeyConstraint(
            ["tour_id"],
            ["tours.id"],
        ),
        sa.ForeignKeyConstraint(
            ["wave_id"],
            ["waves.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tasks_allocation", "tasks", ["allocation_id"], unique=False)
    op.create_index("ix_tasks_queue", "tasks", ["status", "priority", "id"], unique=False)
    op.create_index("ix_tasks_tour", "tasks", ["tour_id"], unique=False)
    op.create_index(
        "ux_tasks_open_replen",
        "tasks",
        ["stock_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'replen' and status in ('open', 'active')"),
    )


def downgrade() -> None:
    op.drop_index(
        "ux_tasks_open_replen",
        table_name="tasks",
        postgresql_where=sa.text("kind = 'replen' and status in ('open', 'active')"),
    )
    op.drop_index("ix_tasks_tour", table_name="tasks")
    op.drop_index("ix_tasks_queue", table_name="tasks")
    op.drop_index("ix_tasks_allocation", table_name="tasks")
    op.drop_table("tasks")
    op.drop_index(op.f("ix_allocations_stock_id"), table_name="allocations")
    op.drop_index(op.f("ix_allocations_order_line_id"), table_name="allocations")
    op.drop_table("allocations")
    op.drop_index(op.f("ix_order_lines_order_id"), table_name="order_lines")
    op.drop_table("order_lines")
    op.drop_index(op.f("ix_tours_wave_id"), table_name="tours")
    op.drop_index("ix_tours_status", table_name="tours")
    op.drop_table("tours")
    op.drop_index("ix_stock_sku_status", table_name="stock")
    op.drop_index("ix_stock_location", table_name="stock")
    op.drop_table("stock")
    op.drop_index("ix_orders_wave", table_name="orders")
    op.drop_index("ix_orders_status", table_name="orders")
    op.drop_index("ix_orders_appointment", table_name="orders")
    op.drop_table("orders")
    op.drop_index(op.f("ix_asn_lines_asn_id"), table_name="asn_lines")
    op.drop_table("asn_lines")
    op.drop_table("waves")
    op.drop_table("lots")
    op.drop_table("asns")
    op.drop_table("skus")
    op.drop_table("appointments")
    op.drop_table("locations")
    op.drop_table("idempotency_keys")
    op.drop_index(op.f("ix_events_at"), table_name="events")
    op.drop_table("events")
    op.drop_table("customers")

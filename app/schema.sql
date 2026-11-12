CREATE TABLE IF NOT EXISTS products (
    id   serial PRIMARY KEY,
    sku  text NOT NULL UNIQUE,
    name text NOT NULL
);

-- ФФ (kind='ff') и виртуальные места, где товар "числится" в процессе
CREATE TABLE IF NOT EXISTS locations (
    id          serial PRIMARY KEY,
    kind        text NOT NULL CHECK (kind IN
                ('ff', 'external', 'transit_transfer', 'transit_fbo', 'transit_client', 'marketplace')),
    name        text NOT NULL UNIQUE,
    is_archived boolean NOT NULL DEFAULT false
);
-- каждое виртуальное место существует ровно одно
CREATE UNIQUE INDEX IF NOT EXISTS locations_virtual_kind
    ON locations (kind) WHERE kind <> 'ff';

-- журнал движений: только дописывается
CREATE TABLE IF NOT EXISTS movements (
    id               bigserial PRIMARY KEY,
    product_id       int NOT NULL REFERENCES products(id),
    qty              int NOT NULL CHECK (qty > 0),
    from_location_id int NOT NULL REFERENCES locations(id),
    to_location_id   int NOT NULL REFERENCES locations(id),
    op_type          text NOT NULL,
    op_id            bigint,
    created_at       timestamptz NOT NULL DEFAULT now(),
    CHECK (from_location_id <> to_location_id)
);
CREATE INDEX IF NOT EXISTS movements_product ON movements (product_id);

CREATE TABLE IF NOT EXISTS reservations (
    id          serial PRIMARY KEY,
    product_id  int NOT NULL REFERENCES products(id),
    location_id int NOT NULL REFERENCES locations(id),
    qty         int NOT NULL CHECK (qty > 0),
    status      text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'shipped', 'cancelled')),
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS transfers (
    id               serial PRIMARY KEY,
    product_id       int NOT NULL REFERENCES products(id),
    qty              int NOT NULL CHECK (qty > 0),
    from_location_id int NOT NULL REFERENCES locations(id),
    to_location_id   int NOT NULL REFERENCES locations(id),
    status           text NOT NULL DEFAULT 'created' CHECK (status IN ('created', 'sent', 'received')),
    created_at       timestamptz NOT NULL DEFAULT now(),
    CHECK (from_location_id <> to_location_id)
);

CREATE TABLE IF NOT EXISTS fbo_shipments (
    id               serial PRIMARY KEY,
    product_id       int NOT NULL REFERENCES products(id),
    qty              int NOT NULL CHECK (qty > 0),
    from_location_id int NOT NULL REFERENCES locations(id),
    status           text NOT NULL DEFAULT 'created' CHECK (status IN ('created', 'sent', 'accepted')),
    created_at       timestamptz NOT NULL DEFAULT now()
);

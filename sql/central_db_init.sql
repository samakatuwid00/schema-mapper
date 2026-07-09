-- IRIMSV is the authoritative Region V system of record.
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE SCHEMA IF NOT EXISTS irimsv;

CREATE TABLE IF NOT EXISTS irimsv.customer (
    id SERIAL PRIMARY KEY,
    external_reference UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    full_name VARCHAR(255) NOT NULL,
    email VARCHAR(255) NOT NULL,
    phone VARCHAR(30),
    created_at TIMESTAMPTZ,
    status VARCHAR(20)
);

CREATE TABLE IF NOT EXISTS irimsv_customer_staging (
    event_id CHAR(36) NOT NULL,
    external_reference CHAR(36) NOT NULL,
    source_system VARCHAR(40) NOT NULL,
    operation VARCHAR(20) NOT NULL,
    source_updated_at DATETIME(6) NOT NULL,
    mapping_version INT NOT NULL,
    payload_checksum CHAR(64) NOT NULL,
    cust_nm VARCHAR(255) NOT NULL,
    email_addr VARCHAR(255) NOT NULL,
    phone_no VARCHAR(30),
    active BOOLEAN NOT NULL DEFAULT TRUE,
    accepted_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (event_id),
    UNIQUE KEY uq_irimsv_external_reference (external_reference)
);

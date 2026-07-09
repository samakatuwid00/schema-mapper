-- Example of the kind of static schema doc a partner system might send you.
-- Deliberately uses different naming conventions than our own schema
-- to demonstrate the mapping engine's job.

CREATE TABLE customers (
    cust_id INT PRIMARY KEY,
    cust_nm VARCHAR(120) NOT NULL,
    email_addr VARCHAR(255) NOT NULL,
    phone_no VARCHAR(30),
    signup_dt DATETIME,
    acct_status VARCHAR(20)
);

CREATE TABLE orders (
    order_id INT PRIMARY KEY,
    cust_id INT,
    order_total DECIMAL(10,2),
    order_dt DATETIME,
    order_status VARCHAR(20)
);

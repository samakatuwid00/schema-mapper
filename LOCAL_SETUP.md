# Local setup

The local stack mirrors the integration boundary:

- PostgreSQL on `localhost:5433`: authoritative `irimsv` data plus `integration` state.
- MySQL on `localhost:3307`: LRMIS-owned staging contract only.

Install dependencies and start the databases:

```bash
pip install -r requirements.txt
docker compose up -d
```

Bootstrap the explicitly reviewed customer pilot:

```bash
python scripts/schema_monitor.py --approve-initial --by YOUR_NAME
python scripts/create_pilot_mapping.py
python scripts/integration_admin.py approve-mapping MAPPING_ID --by YOUR_NAME
```

Exercise and inspect the pipeline:

```bash
python scripts/insert_sample_row.py "Ada Lovelace" ada@example.com
python -m src.worker
python scripts/integration_admin.py status
python scripts/reconcile.py
```

Run `python -m src.worker --loop --interval 300` for five-minute polling. See
`README.md` for drift approval, backfill, replay, kill-switch, security, and production
operations. The automation never applies or alters the LRMIS production schema.
